"""Audit every original H3 slot against retained highest-pixel model sources.

This is an upper-bound audit, not an upscaler. A whole PNG smaller than the
original crop/canvas times three is certainly under capacity. A large PNG is
only potentially sufficient until its effective art rectangle is bound. No
candidate is promoted, edited, approved, or confused with a derived 3X file.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import re

from PIL import Image


DEF_EXACT = re.compile(r"^def_(.+)_(\d+)_(\d+)__$", re.IGNORECASE)
DEF_RAW = re.compile(r"^(.+?)_(\d+)_(\d+)_raw(?:_|$)", re.IGNORECASE)
DEF_FRAME = re.compile(r"^(.+?)-g(\d+)-f(\d+)(.*)$", re.IGNORECASE)


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def native_key(row: dict) -> tuple:
    if row["kind"] == "def":
        return ("def", row["identity"].upper(), row["group"], row["frame"])
    if row["kind"] == "pcx":
        return ("pcx", row["identity"].upper())
    raise ValueError("unsupported original resource kind")


def json_key(key: tuple) -> list:
    return list(key)


def filename_binding(path: Path, original: dict[tuple, dict]) -> tuple | None:
    """Only accept names that identify one original slot without a frame range."""
    prefix = path.stem.split("__", 1)[0]
    exact = DEF_EXACT.fullmatch(prefix + "__")
    if exact:
        key = ("def", exact[1].upper(), int(exact[2]), int(exact[3]))
        return key if key in original else None
    raw = DEF_RAW.match(prefix)
    if raw:
        key = ("def", raw[1].upper(), int(raw[2]), int(raw[3]))
        if key in original:
            return key
    frame = DEF_FRAME.match(prefix)
    if frame:
        suffix = frame[4]
        # f01-04 is a shared phase source; its per-frame art rectangle is unknown.
        if not re.match(r"^-\d+(?:-|$)", suffix):
            key = ("def", frame[1].upper(), int(frame[2]), int(frame[3]))
            if key in original:
                return key
    token = prefix.split("_raw", 1)[0].split("-", 1)[0].upper()
    key = ("pcx", token)
    return key if key in original else None


def image_binding(path: Path, provenance: str, effective_pixels: list[int] | None = None,
                  effective_is_upper_bound: bool = False) -> dict:
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"source is not PNG: {path}")
        pixels = [image.width, image.height]
    if effective_pixels is not None:
        if (not isinstance(effective_pixels, list) or len(effective_pixels) != 2
                or any(type(x) is not int or x < 1 for x in effective_pixels)
                or any(effective_pixels[n] > pixels[n] for n in range(2))):
            raise ValueError(f"invalid effective_pixels for {path}")
    return {"path": str(path), "sha256": sha256(path), "file_pixels": pixels,
            "effective_pixels": effective_pixels, "effective_is_upper_bound": effective_is_upper_bound,
            "provenance": provenance}


def required_pixels(row: dict) -> list[int]:
    base = row.get("crop") if row["kind"] == "def" else row["canvas"]
    if (not isinstance(base, list) or len(base) != 2
            or any(type(x) is not int or x < 1 for x in base)):
        raise ValueError(f"invalid original crop/canvas: {native_key(row)}")
    return [value * 3 for value in base]


def capacity(source: dict, needed: list[int]) -> dict:
    pixels = source["effective_pixels"] or source["file_pixels"]
    shortfall = [max(0, needed[n] - pixels[n]) for n in range(2)]
    if any(shortfall):
        status = ("UNDER_3X_CELL_UPPER_BOUND" if source["effective_is_upper_bound"]
                  else "UNDER_3X_EFFECTIVE" if source["effective_pixels"]
                  else "UNDER_3X_WHOLE_FILE_UPPER_BOUND")
    else:
        status = ("MEETS_3X_EFFECTIVE" if source["effective_pixels"] and not source["effective_is_upper_bound"]
                  else "POSSIBLE_3X_EFFECTIVE_RECT_UNVERIFIED")
    return {**source, "measured_pixels": pixels, "shortfall_pixels": shortfall, "capacity_status": status}


def load_explicit(path: Path | None, expected_sha: str | None, original: dict[tuple, dict]) -> dict[tuple, dict]:
    if path is None:
        return {}
    if not expected_sha or sha256(path) != expected_sha.lower():
        raise ValueError("explicit binding SHA256 missing or changed")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if raw.get("schema") != "h3.native3x.source-bindings.v1":
        raise ValueError("unsupported explicit binding schema")
    result = {}
    for item in raw["bindings"]:
        key = tuple(item["key"])
        if key not in original or key in result:
            raise ValueError(f"unknown/duplicate original slot in explicit bindings: {key}")
        source = Path(item["path"]).resolve(strict=True)
        binding = image_binding(source, "EXPLICIT_CURRENT_SOURCE", item.get("effective_pixels"))
        if binding["sha256"] != item["sha256"]:
            raise ValueError(f"explicit source bytes changed: {key}")
        result[key] = binding
    return result


def receipt_candidates(receipt_root: Path, original: dict[tuple, dict],
                       retained_by_hash: dict[str, list[dict]]) -> tuple[dict[tuple, list[dict]], dict]:
    """Associate retained bytes with frozen requests; never infer current selection."""
    linked: dict[tuple, list[dict]] = {}
    counters = {"examined": 0, "eligible": 0, "retained_byte_matches": 0,
                "stale_or_unreadable": 0, "unknown_original_keys": 0}
    accepted = {"h3.art-town.raw-intake.v1", "h3.map-ui.raw-intake.v1", "h3.hero.portrait.raw.v1",
                "h3.creature.sequence.raw.v2"}
    receipt_paths = set(receipt_root.rglob("*raw.json")) | set(receipt_root.rglob("creature-sequence-*.json"))
    for path in sorted(receipt_paths):
        counters["examined"] += 1
        try:
            receipt = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(receipt, dict) or receipt.get("schema") not in accepted:
                continue
            counters["eligible"] += 1
            grid = None
            if receipt["schema"] == "h3.hero.portrait.raw.v1":
                source_hash = receipt["raw_sha256"]
                keys = [("pcx", receipt["resource"].upper())]
            elif receipt["schema"] == "h3.creature.sequence.raw.v2":
                source_hash = receipt["raw_sha256"]
                request = Path(receipt["request_path"])
                if not request.is_file() or sha256(request) != receipt["request_sha256"]:
                    raise ValueError("creature request bytes unavailable or changed")
                request_doc = json.loads(request.read_text(encoding="utf-8-sig"))
                keys = [tuple(item["key"]) for item in request_doc["original_slots"]]
                grid = request_doc["grid"]
                if (not isinstance(grid, list) or len(grid) != 2
                        or any(type(x) is not int or x < 1 for x in grid)
                        or len(keys) > grid[0] * grid[1]):
                    raise ValueError("invalid creature source grid")
            else:
                source_hash = receipt["raw"]["sha256"]
                request_binding = receipt["request"]
                request = Path(request_binding["path"])
                if not request.is_file():
                    request = path.parent.parent / "requests" / request.name
                if not request.is_file() or sha256(request) != request_binding["sha256"]:
                    raise ValueError("request bytes unavailable or changed")
                request_doc = json.loads(request.read_text(encoding="utf-8-sig"))
                if receipt["schema"] == "h3.art-town.raw-intake.v1":
                    keys = [tuple(request_doc["key"])]
                else:
                    keys = [native_key(item["row"]) for item in request_doc["original_slots"]]
            retained = retained_by_hash.get(source_hash, [])
            if not retained:
                continue
            counters["retained_byte_matches"] += 1
            for key in keys:
                if key not in original:
                    counters["unknown_original_keys"] += 1
                    continue
                for source in retained:
                    candidate = {**source, "provenance": "PINNED_RAW_RECEIPT_RETAINED_FILE_CANDIDATE",
                                 "receipt": str(path), "receipt_sha256": sha256(path)}
                    if grid:
                        if source["file_pixels"] != receipt["raw_info"]["pixels"]:
                            raise ValueError("creature raw pixels changed")
                        candidate["effective_pixels"] = [(source["file_pixels"][n] + grid[n] - 1) // grid[n]
                                                          for n in range(2)]
                        candidate["effective_is_upper_bound"] = True
                        candidate["source_grid"] = grid
                        candidate["provenance"] = "PINNED_CREATURE_GRID_RAW_CANDIDATE"
                    linked.setdefault(key, []).append(candidate)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            counters["stale_or_unreadable"] += 1
    return linked, counters


def audit(inventory: Path, expected_sha: str, masters_root: Path,
          outdir: Path, bindings: Path | None = None, bindings_sha: str | None = None,
          receipt_root: Path | None = None) -> dict:
    inventory = inventory.resolve(strict=True)
    masters_root = masters_root.resolve(strict=True)
    outdir = outdir.resolve()
    if outdir.exists():
        raise FileExistsError("capacity audit destination already exists; choose a new outdir")
    if sha256(inventory) != expected_sha.lower():
        raise ValueError("original inventory SHA256 changed")
    catalog = json.loads(gzip.decompress(inventory.read_bytes()))
    if not isinstance(catalog.get("rows"), list):
        raise ValueError("original inventory rows missing")
    original = {native_key(row): row for row in catalog["rows"]}
    if len(original) != len(catalog["rows"]):
        raise ValueError("duplicate original resource slot")
    explicit = load_explicit(bindings.resolve(strict=True) if bindings else None, bindings_sha, original)
    candidates: dict[tuple, list[dict]] = {}
    unmatched = []
    retained_by_hash: dict[str, list[dict]] = {}
    for path in sorted(masters_root.rglob("*.png")):
        if not path.is_file():
            continue
        source = image_binding(path, "FILENAME_SINGLE_SLOT_CANDIDATE")
        retained_by_hash.setdefault(source["sha256"], []).append(source)
        key = filename_binding(path, original)
        if key is None:
            unmatched.append(str(path))
        else:
            candidates.setdefault(key, []).append(source)
    filename_mapped_slots = len(candidates)
    receipt_counts = None
    receipt_linked_slots = 0
    if receipt_root is not None:
        receipt_root = receipt_root.resolve(strict=True)
        from_receipts, receipt_counts = receipt_candidates(receipt_root, original, retained_by_hash)
        receipt_linked_slots = len(from_receipts)
        for key, sources in from_receipts.items():
            candidates.setdefault(key, []).extend(sources)
    for key, sources in candidates.items():
        by_bytes: dict[str, dict] = {}
        for source in sources:
            old = by_bytes.get(source["sha256"])
            if old is None or source["provenance"].startswith("PINNED_"):
                by_bytes[source["sha256"]] = source
        candidates[key] = list(by_bytes.values())
    slots = []
    api = []
    counts: dict[str, int] = {}
    for key, original_row in original.items():
        needed = required_pixels(original_row)
        bound = explicit.get(key)
        sources = [capacity(bound, needed)] if bound else [capacity(s, needed) for s in candidates.get(key, [])]
        if bound:
            verdict = sources[0]["capacity_status"]
            api_confidence = "EXPLICIT_CURRENT_SOURCE" if verdict.startswith("UNDER_") else None
        elif not sources:
            verdict = "NO_IDENTIFIED_MODEL_SOURCE"
            api_confidence = None
        elif all(s["capacity_status"].startswith("UNDER_") for s in sources):
            verdict = "ALL_FILENAME_CANDIDATES_UNDER_3X_CURRENTNESS_UNVERIFIED"
            api_confidence = "PROVISIONAL_FILENAME_CANDIDATES"
        elif any(s["capacity_status"].startswith("UNDER_") for s in sources):
            verdict = "MIXED_FILENAME_CANDIDATES_CURRENTNESS_UNVERIFIED"
            api_confidence = None
        else:
            verdict = "POSSIBLE_3X_EFFECTIVE_RECT_UNVERIFIED"
            api_confidence = None
        counts[verdict] = counts.get(verdict, 0) + 1
        slot = {"key": json_key(key), "category": original_row.get("category"),
                "label": original_row.get("label", ""), "resource": original_row.get("resource"),
                "required_3x_pixels": needed, "required_from": "original DEF crop" if key[0] == "def" else "original PCX canvas",
                "status": verdict, "sources": sources}
        slots.append(slot)
        if api_confidence:
            for source in sources:
                api.append({"key": json_key(key), "category": slot["category"], "label": slot["label"],
                            "resource": slot["resource"], "required_3x_pixels": needed,
                            "source_pixels": source["measured_pixels"], "shortfall_pixels": source["shortfall_pixels"],
                            "source": source["path"], "source_sha256": source["sha256"],
                            "confidence": api_confidence,
                            "action": "API_REDRAW_HIGHER_NATIVE_MASTER_THEN_ONE_STEP_DOWNSAMPLE_TO_EXACT_3X"})
    summary = {"schema": "h3.native3x.capacity-audit.v1", "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "inventory": str(inventory), "inventory_sha256": expected_sha.lower(),
               "masters_root": str(masters_root), "explicit_bindings": str(bindings) if bindings else None,
               "explicit_bindings_sha256": bindings_sha, "original_slots": len(slots),
               "retained_master_pngs_scanned": sum(len(paths) for paths in retained_by_hash.values()),
               "filename_mapped_slots": filename_mapped_slots, "receipt_linked_slots": receipt_linked_slots,
               "candidate_mapped_slots_total": len(candidates), "explicit_current_bindings": len(explicit),
               "unmatched_master_pngs_by_name": len(unmatched), "receipt_root": str(receipt_root) if receipt_root else None,
               "receipt_evidence": receipt_counts, "api_6k_list_rows": len(api),
               "status_counts": counts, "source_pngs_modified": 0, "derived_3x_treated_as_native_source": False,
               "art_approved": False, "game_deployed": False, "runtime_accepted": False,
               "limitations": ["Filename matches identify candidates, not canonical current artwork.",
                               "Whole-file dimensions can prove a deficit; sufficient dimensions need an effective art rectangle.",
                               "Shared multi-frame images and unmapped files require owner-provided explicit bindings."]}
    outdir.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("all-original-slots.json", slots),
                        ("api-6k-redraw-list.json", api), ("unmapped-master-files.json", unmatched)):
        (outdir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (outdir / "api-6k-redraw-list.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["kind", "identity", "group", "frame", "category", "resource", "required_w", "required_h",
                         "source_w", "source_h", "short_w", "short_h", "confidence", "source", "source_sha256"])
        for row in api:
            key = row["key"]
            writer.writerow([key[0], key[1], *([key[2], key[3]] if len(key) == 4 else ["", ""]),
                             row["category"], row["resource"], *row["required_3x_pixels"], *row["source_pixels"],
                             *row["shortfall_pixels"], row["confidence"], row["source"], row["source_sha256"]])
    return {"status": "AUDIT_WRITTEN_REVIEW_REQUIRED", "original_slots": len(slots),
            "api_6k_rows": len(api), "summary": str(outdir / "summary.json"),
            "api_list": str(outdir / "api-6k-redraw-list.csv"),
            "summary_sha256": sha256(outdir / "summary.json"), "images_written": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--masters-root", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--bindings-sha256")
    parser.add_argument("--receipt-root", type=Path,
                        help="Optional existing review root; only retained byte-identical raw receipts become candidates")
    args = parser.parse_args()
    print(json.dumps(audit(args.inventory, args.inventory_sha256, args.masters_root,
                           args.outdir, args.bindings, args.bindings_sha256, args.receipt_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
