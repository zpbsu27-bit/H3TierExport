"""Freeze exact official-3X reuse and lossless transparent-reframe candidates.

Reads one canonical H3HD inventory. Ambiguous placement or visible crop loss is
held for art review. Writes only new JSON contracts in --outdir, never pixels,
shared manifests, approvals, DEF, LOD, or source artwork.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path

from PIL import Image


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def scale_pair(pair: list[int], multiplier: int) -> list[int]:
    return [pair[0] * multiplier, pair[1] * multiplier]


def original_targets(row: dict) -> dict:
    canvas, crop, origin = [row[name] for name in ("canvas", "crop", "anchor")]
    assert all(len(x) == 2 and all(isinstance(v, int) for v in x) for x in (canvas, crop, origin))
    assert all(v > 0 for v in canvas + crop) and all(v >= 0 for v in origin)
    assert all(origin[i] + crop[i] <= canvas[i] for i in range(2))
    return {f"{name}_{scale}x": scale_pair(values, scale)
            for name, values in (("canvas", canvas), ("crop", crop), ("origin", origin))
            for scale in (1, 2, 3)}


def placements(source_size: tuple[int, int], target_size: tuple[int, int], visible_box: tuple[int, int, int, int] | None) -> list[tuple[int, int]]:
    if visible_box is None:
        return []
    width, height = source_size
    target_w, target_h = target_size
    if width < target_w or height < target_h or width - target_w > 6 or height - target_h > 6:
        return []
    x0, y0, x1, y1 = visible_box
    return [(x, y) for y in range(height - target_h + 1) for x in range(width - target_w + 1)
            if x <= x0 and x1 <= x + target_w and y <= y0 and y1 <= y + target_h]


def prepare(inventory: Path, expected_inventory_sha: str, h3hd_root: Path, identity: str, outdir: Path) -> dict:
    inventory = inventory.resolve()
    h3hd_root = h3hd_root.resolve()
    outdir = outdir.resolve()
    if digest(inventory) != expected_inventory_sha:
        raise ValueError("SOURCE_INVENTORY_REVISION_CHANGED")
    audit_path, native_path, spec_path = [outdir / name for name in ("official-3x-triage.json", "native-contracts.json", "motion-spec.json")]
    if any(path.exists() for path in (audit_path, native_path, spec_path)):
        raise FileExistsError("Frozen official-3X contracts already exist; recover them before retrying")
    catalog = json.loads(gzip.decompress(inventory.read_bytes()))
    rows = sorted((row for row in catalog["rows"] if row.get("kind") == "def" and row.get("category") == "creatures" and row.get("identity") == identity),
                  key=lambda row: (row["group"], row["frame"]))
    if not rows or len({(row["group"], row["frame"]) for row in rows}) != len(rows):
        raise ValueError("Missing or duplicate original creature slots")
    decisions, entries, items, frames = [], [], [], []
    owners: dict[str, str] = {}
    for row in rows:
        key = ["def", identity, row["group"], row["frame"]]
        targets = original_targets(row)
        decision = {"key": key, "targets": targets, "source_resource": row["resource"], "classification": row["classification"]}
        if row["classification"] != "official_direct" or len(row.get("x3", [])) != 1:
            decision["status"] = "HOLD_NO_SINGLE_OFFICIAL_3X_SOURCE"
            decisions.append(decision)
            continue
        relative = Path(row["x3"][0])
        if relative.parts[:2] != ("assets", "OfficialHD2015") or relative.is_absolute():
            raise ValueError("Official source outside declared asset root")
        official_base = (h3hd_root / "OfficialHD2015") if (h3hd_root / "OfficialHD2015").is_dir() else (h3hd_root / "assets" / "OfficialHD2015")
        source = (official_base / Path(*relative.parts[2:])).resolve()
        if not source.is_relative_to(official_base.resolve()) or not source.is_file():
            raise ValueError("Official source is missing or outside the official asset root")
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            visible_box = rgba.getchannel("A").getbbox()  # alpha > 0; even alpha=1 must survive.
            source_size = rgba.size
        source_sha = digest(source)
        target_size = tuple(targets["crop_3x"])
        candidates = placements(source_size, target_size, visible_box)
        decision.update(source=str(source), source_sha256=source_sha, source_pixels=list(source_size),
                        visible_box_alpha_gt0=list(visible_box) if visible_box else None,
                        crop_3x=list(target_size), placement_count=len(candidates))
        if not candidates:
            decision["status"] = "HOLD_VISIBLE_ART_OR_SIZE_EXCEEDS_ORIGINAL_CROP"
        elif len(candidates) != 1:
            decision["status"] = "HOLD_AMBIGUOUS_TRANSPARENT_PLACEMENT"
        else:
            left, top = candidates[0]
            mode = "reuse" if source_size == target_size else "reframe"
            decision.update(status="CANDIDATE_PENDING_ART_REVIEW", mode=mode, source_rect=[left, top, *target_size])
            # Alias only exact original picture bytes with the same native geometry
            # and lossless source rectangle; animation timing stays per original slot.
            equivalence = hashlib.sha256(json.dumps({"identity": identity, "targets": targets,
                                                    "source_sha256": source_sha,
                                                    "source_rect": [left, top, *target_size]},
                                                   sort_keys=True).encode()).hexdigest()
            entries.append({"key": key, "targets": targets, "equivalence_class": equivalence})
            frames.append({"key": key, "targets": targets, "source_equivalence_class": equivalence})
            item_id = f"{identity}_G{row['group']}_F{row['frame']}_OFFICIAL"
            item = {"id": item_id, "key": key, "targets": targets, "mode": mode}
            if equivalence in owners:
                item.update(mode="reuse", reuse_from=owners[equivalence])
                decision["alias_of"] = owners[equivalence]
            else:
                owners[equivalence] = item_id
                item["source"] = {"path": str(source), "sha256": source_sha, "pixels": list(source_size), "key": key, "native_scale": 3}
                if mode == "reframe":
                    item["source_rect"] = [left, top, *target_size]
            items.append(item)
        decisions.append(decision)
    if not items:
        raise ValueError("No uniquely placed official3X candidate")
    audit = {"schema": "h3hd.official3x.geometry-triage.v1", "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "identity": identity, "inventory": str(inventory), "inventory_sha256": expected_inventory_sha,
             "full_original_slots": len(rows), "candidate_slots": len(items),
             "direct_byte_reuse": sum(x.get("mode") == "reuse" for x in decisions),
             "lossless_transparent_reframe": sum(x.get("mode") == "reframe" for x in decisions),
             "unique_picture_geometry_owners": len(owners), "alias_slots": len(items) - len(owners),
             "held_slots": len(rows) - len(items),
             "frames": frames, "decisions": decisions,
             "equivalence_policy": "Alias only identical source SHA + original targets + lossless source_rect within one creature; keep each native slot and timing distinct",
             "new_model_calls": 0, "art_accepted": False, "native_timing_verified": False, "game_deployed": False,
             "shared_manifest_writes": 0, "source_pngs_modified": 0}
    write_new(audit_path, audit)
    native = {"schema": "h3.motion.native-slot-projection.v1", "projection_source": {"path": str(audit_path), "sha256": digest(audit_path)}, "entries": entries}
    write_new(native_path, native)
    spec = {"schema": "h3.motion.spec.v1", "output_root": str(outdir / "candidates"),
            "native_contracts": [{"path": str(native_path), "sha256": digest(native_path)}],
            "settings": {"backend": "cpu", "space": "linear", "filter": "h3-quality", "workers": 4, "memory_gib": 2},
            "items": items, "triage": {"path": str(audit_path), "sha256": digest(audit_path)},
            "note": "Official3X geometry candidates only; source bytes preserved; art, native timing and game acceptance open."}
    write_new(spec_path, spec)
    return {"status": "OFFICIAL_3X_CANDIDATE_SPEC_FROZEN", "identity": identity, "full_slots": len(rows),
            "candidate_slots": len(items), "direct_reuse": audit["direct_byte_reuse"], "transparent_reframe": audit["lossless_transparent_reframe"],
            "held": audit["held_slots"], "unique_picture_geometry_owners": len(owners),
            "alias_slots": audit["alias_slots"], "audit": str(audit_path), "native": str(native_path),
            "spec": str(spec_path), "images_written": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--h3hd-root", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.inventory, args.inventory_sha256, args.h3hd_root, args.identity.upper(), args.outdir), ensure_ascii=False))


if __name__ == "__main__":
    main()
