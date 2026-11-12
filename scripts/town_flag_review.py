#!/usr/bin/env python3
"""Review the actual 3X town body and its independently movable VCMI flag overlay.

The review is a snapshot. It never changes the source body, overlay, or game.
An exported layout records proposed 3X-pixel offsets, not artwork approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops

SCHEMA = "h3.town-flag-overlay-review.v1"
LAYOUT_SCHEMA = "h3.town-flag-overlay-layout.v1"
IDENTITY = re.compile(r"^[A-Z0-9_]{4,24}$")
SIZE = (576, 576)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def any_alpha_overlap(left: Image.Image, right: Image.Image) -> bool:
    """Treat even alpha=1 in both masks as overlap; multiply alone rounds it away."""
    l = left.point(lambda value: 255 if value else 0)
    r = right.point(lambda value: 255 if value else 0)
    return ImageChops.multiply(l, r).getbbox() is not None


def read_manifest(path: Path, expected: str) -> dict:
    require(path.is_absolute() and path.is_file(), "MANIFEST_NOT_FOUND")
    require(sha(path) == expected.lower(), "MANIFEST_SHA_MISMATCH")
    data = json.loads(path.read_text(encoding="utf-8"))
    require(data.get("schema") == SCHEMA, "MANIFEST_SCHEMA_UNSUPPORTED")
    rows = data.get("items")
    require(isinstance(rows, list) and 1 <= len(rows) <= 36, "MANIFEST_ITEMS_INVALID")
    ids = [r.get("identity") for r in rows]
    require(all(isinstance(x, str) and IDENTITY.fullmatch(x) for x in ids)
            and len(set(ids)) == len(ids), "IDENTITIES_INVALID")
    return data


def checked_input(row: dict, name: str) -> Path:
    value = row.get(name + "_path")
    require(isinstance(value, str), f"{row['identity']}: {name.upper()}_PATH_REQUIRED")
    path = Path(value)
    require(path.is_absolute() and path.is_file(), f"{row['identity']}: {name.upper()}_NOT_FOUND")
    require(sha(path) == str(row.get(name + "_sha256", "")).lower(),
            f"{row['identity']}: {name.upper()}_SHA_DRIFT")
    return path.resolve()


def extra_overlay_boundary(alpha: Image.Image) -> int:
    """Detect a distant upper overlay such as an original capitol roof flag.

    Return 0 if there is no distant upper portion. This is a separation aid,
    not a claim about the artistic meaning of every pixel.
    """
    rows = [y for y in range(SIZE[1]) if alpha.crop((0, y, SIZE[0], y + 1)).getbbox()]
    gaps = [(b - a - 1, a, b) for a, b in zip(rows, rows[1:]) if b - a > 1]
    if not gaps:
        return 0
    width, upper, lower = max(gaps)
    return (upper + lower) // 2 if width >= 96 and lower >= 288 else 0


def split_overlay(path: Path, split: int, gate_y_min: int | None = None) -> tuple[Image.Image, Image.Image, list[list[int]], Image.Image]:
    require(isinstance(split, int) and not isinstance(split, bool) and 0 < split < SIZE[0],
            "FLAG_SPLIT_INVALID")
    with Image.open(path) as source:
        require(source.mode == "RGBA" and source.size == SIZE, "OVERLAY_GEOMETRY_INVALID")
        image = source.copy()
    alpha = image.getchannel("A")
    detected = extra_overlay_boundary(alpha)
    require(gate_y_min is not None or not detected, "ADDITIONAL_OVERLAY_REQUIRES_GATE_REGION")
    gate_y_min = 0 if gate_y_min is None else gate_y_min
    require(isinstance(gate_y_min, int) and not isinstance(gate_y_min, bool)
            and 0 <= gate_y_min < SIZE[1], "GATE_FLAG_REGION_INVALID")
    if detected:
        upper = alpha.crop((0, 0, SIZE[0], gate_y_min)).getbbox()
        lower = alpha.crop((0, gate_y_min, SIZE[0], SIZE[1])).getbbox()
        require(upper is not None and lower is not None and gate_y_min == detected,
                "GATE_FLAG_REGION_MISMATCH")
    else:
        require(gate_y_min == 0, "GATE_FLAG_REGION_UNEXPECTED")
    extra = Image.new("L", SIZE, 0)
    if gate_y_min:
        extra.paste(alpha.crop((0, 0, SIZE[0], gate_y_min)), (0, 0))
    left = image.crop((0, gate_y_min, split, SIZE[1]))
    right = image.crop((split, gate_y_min, SIZE[0], SIZE[1]))
    a, b = left.getchannel("A").getbbox(), right.getchannel("A").getbbox()
    require(a is not None and b is not None, "TWO_FLAGS_REQUIRED")
    boxes = [[a[0], a[1] + gate_y_min, a[2], a[3] + gate_y_min],
             [b[0] + split, b[1] + gate_y_min, b[2] + split, b[3] + gate_y_min]]
    image.close()
    return left, right, boxes, extra


def safe_output(path: Path, inputs: list[Path]) -> Path:
    require(path.is_absolute(), "OUTPUT_PATH_MUST_BE_ABSOLUTE")
    out = path.resolve()
    require(not out.exists(), "OUTPUT_ALREADY_EXISTS")
    require(all(not out.is_relative_to(p.parent) for p in inputs), "OUTPUT_INSIDE_INPUT_DIRECTORY")
    return out


def read_default_town_mask(path: Path, expected: str) -> dict:
    require(path.is_absolute() and path.is_file() and sha(path) == expected.lower(),
            "TOWN_MASK_CONFIG_SHA_DRIFT")
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r'"town"\s*:\s*\{\s*"index"\s*:\s*98\b.*?"mask"\s*:\s*\[([^\]]+)\]',
                      text, re.DOTALL)
    require(match is not None, "TOWN_MASK_NOT_FOUND")
    block = re.sub(r'//[^\r\n]*', '', match.group(1))
    rows = re.findall(r'"([0VBAHT ]+)"', block)
    require(len(rows) == 6 and all(len(row) == 6 for row in rows), "TOWN_MASK_GEOMETRY_INVALID")
    active = [(x, y) for y, row in enumerate(rows) for x, char in enumerate(row) if char == "A"]
    require(len(active) == 1, "TOWN_MASK_VISIT_TILE_AMBIGUOUS")
    x, y = active[0]
    return {"source": str(path), "source_sha256": expected.lower(), "rows": rows,
            "tile": [x, y], "bounds_3x": [x * 96, y * 96, (x + 1) * 96, (y + 1) * 96],
            "center_3x": [x * 96 + 48, y * 96 + 48],
            "scope": "VCMI default template; saved maps or other mods may override it"}


def freeze_mod(reference_request: Path, reference_sha: str, sprites_root: Path,
               operation_id: str, destination: Path, extra_identities: list[str] | None = None,
               town_mask_config: Path | None = None) -> dict:
    require(reference_request.is_absolute() and reference_request.is_file()
            and sha(reference_request) == reference_sha.lower(), "REFERENCE_REQUEST_SHA_MISMATCH")
    reference = json.loads(reference_request.read_text(encoding="utf-8"))
    require(reference.get("schema") == "h3.town-flag-alignment-request.v3", "REFERENCE_REQUEST_SCHEMA_INVALID")
    require(sprites_root.is_absolute() and sprites_root.is_dir(), "SPRITES_ROOT_NOT_FOUND")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", operation_id) is not None,
            "OPERATION_ID_INVALID")
    sprites_root = sprites_root.resolve()
    native_root = Path(reference["native_frames_root"]).resolve()
    def_root = Path(reference["native_def_root"]).resolve()
    reference_items = list(reference["items"])
    seen = {item["identity"] for item in reference_items}
    if extra_identities:
        from town_flag_alignment import _native_palette5, _read_sprite_flags
        authority = reference["flag_authority"]
        flag_info = Path(authority["sprite_flags_info"]).resolve()
        require(flag_info.is_file() and sha(flag_info) == authority["sprite_flags_info_sha256"],
                "FLAG_AUTHORITY_SHA_DRIFT")
        for identity in extra_identities:
            identity = identity.upper()
            require(IDENTITY.fullmatch(identity) is not None and identity not in seen,
                    "EXTRA_IDENTITY_INVALID_OR_DUPLICATE")
            native = native_root / identity / "0_0.png"
            native_def = def_root / (identity + ".DEF")
            require(native.is_file() and native_def.is_file(), f"{identity}: ORIGINAL_REFERENCE_NOT_FOUND")
            mask, info = _native_palette5(native_def); mask.close()
            table = _read_sprite_flags(flag_info, {identity})
            reference_items.append({"identity": identity, "native_reference_relpath": f"{identity}/0_0.png",
                "native_reference_sha256": sha(native), "native_def_relpath": native_def.name,
                "native_def_sha256": sha(native_def), "native_palette5": info,
                "native_flag_anchors": table.get(identity, [])})
            seen.add(identity)
    require(1 <= len(reference_items) <= 36, "MANIFEST_ITEMS_INVALID")
    rows = []
    for item in reference_items:
        identity = item["identity"]
        require(IDENTITY.fullmatch(identity) is not None, "IDENTITY_INVALID")
        descriptor = sprites_root / (identity + ".json")
        require(descriptor.is_file(), f"{identity}: DESCRIPTOR_NOT_FOUND")
        desc = json.loads(descriptor.read_text(encoding="utf-8"))
        frames = [f for f in desc.get("images", []) if f.get("group") == 0 and f.get("frame") == 0]
        require(len(frames) == 1, f"{identity}: DESCRIPTOR_FRAME_NOT_UNIQUE")
        body = (sprites_root / desc["basepath"] / frames[0]["file"]).resolve()
        require(body.is_relative_to(sprites_root) and body.parent.name.upper() == identity
                and body.is_file(), f"{identity}: DESCRIPTOR_BODY_INVALID")
        overlay = body.with_name(body.stem + "-OVERLAY.png")
        require(overlay.is_file(), f"{identity}: OVERLAY_NOT_FOUND")
        native = (native_root / item["native_reference_relpath"]).resolve()
        native_def = (def_root / item["native_def_relpath"]).resolve()
        require(native.is_relative_to(native_root) and native.is_file()
                and sha(native) == item["native_reference_sha256"], f"{identity}: NATIVE_REFERENCE_SHA_DRIFT")
        require(native_def.is_relative_to(def_root) and native_def.is_file()
                and sha(native_def) == item["native_def_sha256"], f"{identity}: NATIVE_DEF_SHA_DRIFT")
        with Image.open(body) as im:
            require(im.mode == "RGBA" and im.size == SIZE, f"{identity}: BODY_GEOMETRY_INVALID")
        with Image.open(overlay) as im:
            require(im.mode == "RGBA" and im.size == SIZE, f"{identity}: OVERLAY_GEOMETRY_INVALID")
            alpha = im.getchannel("A")
            gate_y_min = extra_overlay_boundary(alpha)
            columns = [x for x in range(SIZE[0]) if alpha.crop((x, gate_y_min, x + 1, SIZE[1])).getbbox()]
        gaps = [(b - a - 1, a, b) for a, b in zip(columns, columns[1:]) if b - a > 1]
        require(bool(gaps), f"{identity}: FLAG_SPLIT_NOT_FOUND")
        width, left, right = max(gaps)
        require(width >= 10, f"{identity}: FLAG_SPLIT_AMBIGUOUS")
        split = (left + right) // 2
        a, b, _, extra = split_overlay(overlay, split, gate_y_min); a.close(); b.close(); extra.close()
        row = {"identity": identity, "flag_split_x": split, "gate_flag_y_min_3x": gate_y_min,
               "flag_source": "current MOD overlay; reference " +
                   ("VCMI flag data" if item.get("native_flag_anchors") else "1999 DEF palette-5"),
               "native_def_path": str(native_def), "native_def_sha256": sha(native_def),
               "native_palette5_mask_sha256": item["native_palette5"]["mask_sha256"]}
        for key, path in (("body", body), ("overlay", overlay), ("native_reference", native), ("descriptor", descriptor)):
            row[key + "_path"] = str(path); row[key + "_sha256"] = sha(path)
        rows.append(row)
    require(destination.is_absolute() and not destination.exists(), "MANIFEST_OUTPUT_INVALID")
    destination = destination.resolve()
    require(not destination.is_relative_to(sprites_root) and not destination.is_relative_to(native_root)
            and not destination.is_relative_to(def_root), "MANIFEST_OUTPUT_INSIDE_ASSET_ROOT")
    doc = {"schema": SCHEMA, "operation_id": operation_id, "items": rows,
           "source_request": str(reference_request), "source_request_sha256": reference_sha.lower(),
           "note": "Current MOD overlay is a visual layer; road and pole contact require separate review."}
    if town_mask_config:
        require(town_mask_config.is_absolute() and town_mask_config.is_file(), "TOWN_MASK_CONFIG_NOT_FOUND")
        town_mask_config = town_mask_config.resolve()
        doc["default_town_mask"] = read_default_town_mask(town_mask_config, sha(town_mask_config))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2); f.write("\n")
    warnings = []
    if not town_mask_config:
        warnings.append("VCMI default visitable tile not pinned; provide --town-mask-config")
    if "AVCHFOR0" in seen and "AVCHFORZ" in seen and "AVCHFORX" not in seen:
        warnings.append("Conflux fort/castle AVCHFORX is absent; add --extra-identity AVCHFORX")
    return {"status": "FROZEN", "manifest": str(destination), "manifest_sha256": sha(destination),
            "items": len(rows), "warnings": warnings, "mod_written": False}


def build_review(manifest: Path, expected: str, outdir: Path) -> dict:
    data = read_manifest(manifest, expected)
    town_mask = None
    if data.get("default_town_mask"):
        mask = data["default_town_mask"]
        town_mask = read_default_town_mask(Path(mask["source"]), mask["source_sha256"])
        require(town_mask == mask, "TOWN_MASK_MANIFEST_MISMATCH")
    prepared = []
    paths = []
    for row in data["items"]:
        require(row.get("review_status", "LIVE_MOD") in ("LIVE_MOD", "CANDIDATE"),
                f"{row['identity']}: REVIEW_STATUS_INVALID")
        body = checked_input(row, "body")
        overlay = checked_input(row, "overlay")
        native = checked_input(row, "native_reference")
        native_def = checked_input(row, "native_def") if row.get("native_def_path") else None
        descriptor = checked_input(row, "descriptor") if row.get("descriptor_path") else None
        with Image.open(body) as img:
            require(img.size == SIZE and img.mode == "RGBA", f"{row['identity']}: BODY_GEOMETRY_INVALID")
        with Image.open(native) as img:
            require(img.size == (192, 192), f"{row['identity']}: NATIVE_GEOMETRY_INVALID")
        left, right, boxes, extra = split_overlay(overlay, row.get("flag_split_x"), row.get("gate_flag_y_min_3x"))
        extra_bbox = extra.getbbox()
        left.close(); right.close(); extra.close()
        prepared.append((row, body, overlay, native, descriptor, native_def, boxes, extra_bbox))
        paths.extend([body, overlay, native] + ([descriptor] if descriptor else []) + ([native_def] if native_def else []))
    out = safe_output(outdir, paths)
    out.mkdir(parents=True, exist_ok=False)
    for sub in ("bodies", "overlays", "native", "native-flags"):
        (out / sub).mkdir()
    entries = []
    for row, body, overlay, native, descriptor, native_def, boxes, extra_bbox in prepared:
        identity = row["identity"]
        for source, sub, key in ((body, "bodies", "body"), (overlay, "overlays", "overlay"),
                                 (native, "native", "native_reference")):
            target = out / sub / (identity + ".png")
            shutil.copyfile(source, target)
            require(sha(source) == row[key + "_sha256"] == sha(target),
                    f"{identity}: INPUT_CHANGED_DURING_SNAPSHOT")
        native_flags = None
        if native_def:
            from town_flag_alignment import _native_palette5
            mask, info = _native_palette5(native_def)
            require(info["mask_sha256"] == row.get("native_palette5_mask_sha256"),
                    f"{identity}: NATIVE_FLAG_MASK_SHA_DRIFT")
            layer = Image.new("RGBA", (192, 192), (255, 255, 255, 0)); layer.putalpha(mask)
            layer.save(out / "native-flags" / (identity + ".png"), format="PNG")
            layer.close(); mask.close()
            native_flags = f"native-flags/{identity}.png"
        entries.append({"identity": identity, "body": f"bodies/{identity}.png",
                        "overlay": f"overlays/{identity}.png", "native": f"native/{identity}.png",
                        "native_flags": native_flags,
                        "body_sha256": row["body_sha256"], "overlay_sha256": row["overlay_sha256"],
                        "native_reference_sha256": row["native_reference_sha256"],
                        "review_status": row.get("review_status", "LIVE_MOD"),
                        "flag_source": row.get("flag_source", "UNSPECIFIED"), "flag_boxes": boxes,
                        "gate_flag_y_min_3x": row.get("gate_flag_y_min_3x") or 0,
                        "fixed_extra_overlay_bbox_3x": list(extra_bbox) if extra_bbox else None})
    report = {"schema": "h3.town-flag-overlay-review-report.v1", "status": "REVIEW_REQUIRED",
              "operation_id": data.get("operation_id"), "manifest": str(manifest),
              "manifest_sha256": expected.lower(), "items": entries,
              "default_town_mask": town_mask,
              "scope": "SHA-pinned candidate and/or current MOD snapshots, independent flag previews. Pole, entrance and game remain unverified.",
              "mod_written": False, "game_tested": False}
    (out / "review-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = json.dumps({"manifest_sha256": expected.lower(), "items": entries,
                          "default_town_mask": town_mask}, ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    return {"status": "REVIEW_REQUIRED", "items": len(entries), "html_review": str(out / "index.html"),
            "report": str(out / "review-report.json"), "mod_written": False}


def translate_body_candidate(body: Path, native: Path, offset: list[int], identity: str):
    """Move an existing 3X canvas by whole pixels without clipping visible alpha."""
    with Image.open(body) as image:
        require(image.size == SIZE and image.mode == "RGBA", f"{identity}: BODY_GEOMETRY_INVALID")
        source = image.copy()
    with Image.open(native) as image:
        require(image.size == (192, 192), f"{identity}: NATIVE_GEOMETRY_INVALID")
        native_rgba = image.convert("RGBA")
        native_box = native_rgba.getchannel("A").getbbox()
        native_rgba.close()
    box = source.getchannel("A").getbbox()
    require(box is not None and native_box is not None, f"{identity}: EMPTY_BODY_OR_NATIVE")
    dx, dy = offset
    moved = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
    allowed = tuple(v * 3 for v in native_box)
    require(0 <= moved[0] < moved[2] <= SIZE[0] and 0 <= moved[1] < moved[3] <= SIZE[1],
            f"{identity}: BODY_SHIFT_CLIPS_ALPHA")
    require(allowed[0] <= moved[0] and allowed[1] <= moved[1]
            and moved[2] <= allowed[2] and moved[3] <= allowed[3],
            f"{identity}: BODY_SHIFT_OUTSIDE_NATIVE_ALPHA_BOX")
    result = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    result.paste(source, (dx, dy))
    require(result.getchannel("A").getbbox() == moved, f"{identity}: BODY_SHIFT_ALPHA_MISMATCH")
    # The entire nontransparent source is in this intersection; copy RGBA exactly.
    src_box = (max(0, -dx), max(0, -dy), min(SIZE[0], SIZE[0] - dx), min(SIZE[1], SIZE[1] - dy))
    dst_box = (src_box[0] + dx, src_box[1] + dy, src_box[2] + dx, src_box[3] + dy)
    require(source.crop(src_box).tobytes() == result.crop(dst_box).tobytes(),
            f"{identity}: BODY_SHIFT_PIXEL_MISMATCH")
    source.close()
    return result, {"body_offset_3x": offset, "source_alpha_bbox_3x": list(box),
                    "result_alpha_bbox_3x": list(moved), "native_alpha_bbox_3x": list(allowed),
                    "pixel_resampling": False}


def export_overlays(manifest: Path, expected: str, layout_path: Path, layout_sha: str, outdir: Path,
                    allow_body_translation: bool = False) -> dict:
    data = read_manifest(manifest, expected)
    if data.get("default_town_mask"):
        mask = data["default_town_mask"]
        require(read_default_town_mask(Path(mask["source"]), mask["source_sha256"]) == mask,
                "TOWN_MASK_MANIFEST_MISMATCH")
    require(layout_path.is_absolute() and layout_path.is_file() and sha(layout_path) == layout_sha.lower(),
            "LAYOUT_SHA_MISMATCH")
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    require(layout.get("schema") == LAYOUT_SCHEMA and layout.get("manifest_sha256") == expected.lower(),
            "LAYOUT_MANIFEST_MISMATCH")
    rows = {r["identity"]: r for r in data["items"]}
    values = layout.get("items")
    require(isinstance(values, list) and values, "LAYOUT_ITEMS_REQUIRED")
    ids = [v.get("identity") for v in values]
    require(len(ids) == len(set(ids)) and set(ids) <= set(rows), "LAYOUT_IDENTITIES_INVALID")
    prepared = []
    input_paths = []
    for value in values:
        identity = value["identity"]; row = rows[identity]
        body = checked_input(row, "body"); overlay = checked_input(row, "overlay")
        require(value.get("body_sha256") == row["body_sha256"]
                and value.get("overlay_sha256") == row["overlay_sha256"], f"{identity}: LAYOUT_INPUT_MISMATCH")
        body_offset = value.get("body_offset_3x", [0, 0])
        require(isinstance(body_offset, list) and len(body_offset) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) and -575 <= v <= 575
                        for v in body_offset), f"{identity}: BODY_OFFSET_INVALID")
        require(body_offset == [0, 0] or allow_body_translation,
                f"{identity}: BODY_SHIFT_REQUIRES_SEPARATE_NATIVE_EXPORT")
        shifted_body = None
        body_info = None
        if body_offset != [0, 0]:
            native = checked_input(row, "native_reference")
            shifted_body, body_info = translate_body_candidate(body, native, body_offset, identity)
            input_paths.append(native)
        gate_y_min = row.get("gate_flag_y_min_3x")
        left, right, boxes, extra = split_overlay(overlay, row.get("flag_split_x"), gate_y_min)
        gate_y_min = gate_y_min or 0
        offsets = []
        for side in ("left", "right"):
            pair = value.get(side + "_offset_3x")
            require(isinstance(pair, list) and len(pair) == 2
                    and all(isinstance(v, int) and not isinstance(v, bool) and -575 <= v <= 575 for v in pair),
                    f"{identity}: {side.upper()}_OFFSET_INVALID")
            offsets.append(pair)
        masks = []
        for part, box, (dx, dy) in zip((left, right), boxes, offsets):
            require(0 <= box[0] + dx < box[2] + dx <= SIZE[0]
                    and 0 <= box[1] + dy < box[3] + dy <= SIZE[1], f"{identity}: FLAG_OUT_OF_BOUNDS")
            mask = Image.new("L", SIZE, 0)
            shift_x = dx if part is left else row["flag_split_x"] + dx
            mask.paste(part.getchannel("A"), (shift_x, gate_y_min + dy))
            masks.append(mask)
        require(not any_alpha_overlap(*masks), f"{identity}: FLAG_OVERLAP")
        require(all(not any_alpha_overlap(extra, mask) for mask in masks),
                f"{identity}: FLAG_OVERLAPS_FIXED_EXTRA")
        result = Image.new("RGBA", SIZE, (255, 255, 255, 0))
        result.putalpha(ImageChops.lighter(ImageChops.lighter(*masks), extra))
        prepared.append((identity, result, offsets, row, shifted_body, body_info))
        left.close(); right.close(); extra.close()
        for mask in masks: mask.close()
        input_paths.extend([body, overlay])
    for value in values:
        row = rows[value["identity"]]
        checked_input(row, "body"); checked_input(row, "overlay")
        if value.get("body_offset_3x", [0, 0]) != [0, 0]:
            checked_input(row, "native_reference")
    out = safe_output(outdir, input_paths)
    out.mkdir(parents=True, exist_ok=False)
    entries = []
    for identity, image, offsets, row, shifted_body, body_info in prepared:
        dest = out / (identity + "-OVERLAY.png")
        image.save(dest, format="PNG", optimize=True); image.close()
        entry = {"identity": identity, "output": str(dest), "sha256": sha(dest),
                 "left_offset_3x": offsets[0], "right_offset_3x": offsets[1],
                 "source_body_sha256": row["body_sha256"], "source_overlay_sha256": row["overlay_sha256"]}
        if shifted_body is not None:
            body_dest = out / "bodies" / identity / "0_0.png"
            body_dest.parent.mkdir(parents=True, exist_ok=True)
            shifted_body.save(body_dest, format="PNG", optimize=True)
            with Image.open(body_dest) as readback:
                require(readback.mode == "RGBA" and readback.size == SIZE
                        and readback.tobytes() == shifted_body.tobytes(),
                        f"{identity}: BODY_SHIFT_READBACK_MISMATCH")
            shifted_body.close()
            entry["body_candidate"] = {"output": str(body_dest), "sha256": sha(body_dest),
                                       "native_reference_sha256": row["native_reference_sha256"], **body_info}
        entries.append(entry)
    receipt = {"status": "REVIEW_REQUIRED", "manifest_sha256": expected.lower(),
               "layout_sha256": layout_sha.lower(), "items": entries, "mod_written": False,
               "game_tested": False, "claim_boundary": "Generated overlay and optional translated 3X candidates only; no master, art or game acceptance."}
    (out / "overlay-candidate-report.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>城镇旗面逐城复核</title><style>
body{font:15px system-ui;background:#182123;color:#eee;margin:20px}p{color:#c8d5d5}button,input{font:inherit}button{margin:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:15px}article{background:#273235;padding:12px;border-radius:6px}
canvas{display:block;background:#202b2d;width:100%;max-width:576px;touch-action:none;cursor:crosshair}label{margin-right:8px}.coords{font-family:monospace}
</style><h1>城镇旗面对齐复核</h1><p id="snapshot-summary"></p><p>每张图标明“新候选”或“当前 MOD”；新候选只供审图，不代表已经安装到游戏。红、蓝旗面和建筑可分别拖动预览；远离入口的额外旗面以金色显示并保持固定。旗面位置仅为视觉层，不改变道路或通行格。绿色框为已绑定的 VCMI 默认可访问格，地图或其他 MOD 可覆盖；格中心不等于实际道路线。可逐一标记旗面挂点、杆顶、门口和道路，再用“按杆顶对旗”计算左右旗位移。保存布局后，可显式导出不超原版边界的建筑平移候选和旗面候选，详见工具手册；不会自动写回。缺杆、堵路或杆距不匹配需另修建筑。数值单位为 3X 像素。</p>
<p><label>建筑亮度对照 <input type="range" id="brightness" min="40" max="100" value="100"><output id="brightness-value">100%</output></label><button id="brightness-70">70% 预览</button><button id="brightness-100">恢复原亮度</button> 只影响页面中的建筑显示，不改图片；均匀调暗仅用于对照，不代表保留高光的最终绘画效果。</p>
<p><button id="save">保存全部布局 JSON</button><label>载入此前布局 <input type="file" id="load" accept="application/json"></label><span id="message"></span></p><main class="grid" id="grid"></main>
<script>
const data=__DATA__,states={};const grid=document.getElementById('grid');
document.getElementById('snapshot-summary').textContent=`本页：新候选 ${data.items.filter(x=>x.review_status==='CANDIDATE').length} 张；当前 MOD ${data.items.filter(x=>x.review_status==='LIVE_MOD').length} 张。候选尚未自动写入 MOD。`;
const brightness=document.getElementById('brightness'),brightnessValue=document.getElementById('brightness-value');
function updateBrightness(){brightnessValue.textContent=brightness.value+'%';for(const s of Object.values(states))draw(s)}
brightness.oninput=updateBrightness;document.getElementById('brightness-70').onclick=()=>{brightness.value=70;updateBrightness()};document.getElementById('brightness-100').onclick=()=>{brightness.value=100;updateBrightness()};
function img(src){let x=new Image();x.src=src;return x}function point(e,c){let r=c.getBoundingClientRect();return [Math.round((e.clientX-r.left)*576/r.width),Math.round((e.clientY-r.top)*576/r.height)]}
function draw(s){const c=s.canvas,x=c.getContext('2d');x.clearRect(0,0,576,576);x.imageSmoothingEnabled=false;
 if(s.native.checked){if(s.ref.complete)x.drawImage(s.ref,0,0,576,576);if(s.nativeFlags&&s.nativeFlags.complete){let t=document.createElement('canvas');t.width=t.height=192;let z=t.getContext('2d');z.drawImage(s.nativeFlags,0,0);z.globalCompositeOperation='source-in';z.fillStyle='#fa3348';z.fillRect(0,0,192,192);x.drawImage(t,0,0,576,576)}}else {if(s.body.complete){x.save();x.filter=`brightness(${brightness.value}%)`;x.drawImage(s.body,...s.bodyOffset);x.restore()}
 if(s.overlay.complete){if(s.row.gate_flag_y_min_3x){let h=s.row.gate_flag_y_min_3x,t=document.createElement('canvas');t.width=576;t.height=h;let z=t.getContext('2d');z.drawImage(s.overlay,0,0,576,h,0,0,576,h);z.globalCompositeOperation='source-in';z.fillStyle='#ffc85a';z.fillRect(0,0,576,h);x.drawImage(t,0,0)}for(let i=0;i<2;i++){let b=s.row.flag_boxes[i],o=s.offset[i],w=b[2]-b[0],h=b[3]-b[1];let t=document.createElement('canvas');t.width=w;t.height=h;let z=t.getContext('2d');z.drawImage(s.overlay,b[0],b[1],w,h,0,0,w,h);z.globalCompositeOperation='source-in';z.fillStyle=i?'#45aaff':'#fa3348';z.fillRect(0,0,w,h);x.drawImage(t,b[0]+o[0],b[1]+o[1])}}}
 if(s.grid.checked){x.strokeStyle='#ffffff36';for(let n=96;n<576;n+=96){x.beginPath();x.moveTo(n,0);x.lineTo(n,576);x.moveTo(0,n);x.lineTo(576,n);x.stroke()}}
 if(s.visit.checked&&data.default_town_mask){let b=data.default_town_mask.bounds_3x,c=data.default_town_mask.center_3x;x.strokeStyle='#54ff9a';x.lineWidth=2;x.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);x.beginPath();x.moveTo(c[0]-8,c[1]);x.lineTo(c[0]+8,c[1]);x.moveTo(c[0],c[1]-8);x.lineTo(c[0],c[1]+8);x.stroke()}
 for(let [k,p0] of Object.entries(s.marks)){if(!p0||s.native.checked)continue;let p=k==='road'?p0:[p0[0]+s.bodyOffset[0],p0[1]+s.bodyOffset[1]];x.strokeStyle=k==='left'?'#fa3348':k==='right'?'#45aaff':k==='gate'?'#ffe675':'#6df49d';x.lineWidth=2;x.beginPath();x.arc(p[0],p[1],6,0,Math.PI*2);x.moveTo(p[0]-10,p[1]);x.lineTo(p[0]+10,p[1]);x.moveTo(p[0],p[1]-10);x.lineTo(p[0],p[1]+10);x.stroke()}
 for(let i=0;i<2;i++){let p=s.flagAttach[i];if(!p||s.native.checked)continue;x.strokeStyle=i?'#45aaff':'#fa3348';x.lineWidth=2;x.strokeRect(p[0]+s.offset[i][0]-5,p[1]+s.offset[i][1]-5,10,10)}
 s.info.textContent=`建筑 Δ(${s.bodyOffset})　左旗 Δ(${s.offset[0]})　右旗 Δ(${s.offset[1]})　杆顶 ${JSON.stringify([s.marks.left,s.marks.right])}　入口/道路 ${JSON.stringify([s.marks.gate,s.marks.road])}`}
function setup(row){let a=document.createElement('article');a.innerHTML=`<h2></h2><p class="source"></p><canvas width="576" height="576"></canvas><p><label>操作 <select><option value="flag-left">拖左旗</option><option value="flag-right">拖右旗</option><option value="body">拖建筑（预览）</option><option value="left">标左杆顶</option><option value="right">标右杆顶</option><option value="attach-left">标左旗挂点</option><option value="attach-right">标右旗挂点</option><option value="gate">标门口</option><option value="road">标道路入口</option></select></label><label><input type="checkbox" class="gridbox" checked>网格</label><label><input type="checkbox" class="visit" checked>默认访问格</label><label><input type="checkbox" class="native">原版 1X 对照</label><button class="align">按杆顶对旗</button><button class="reset">还原位置</button></p><p class="coords"></p>`;a.querySelector('h2').textContent=row.identity+' · '+(row.review_status==='CANDIDATE'?'新候选（未安装）':'当前 MOD');a.querySelector('.source').textContent=`旗面来源: ${row.flag_source}；当前审图身体 SHA ${row.body_sha256.slice(0,12)}${row.fixed_extra_overlay_bbox_3x?'；额外旗面固定不移动':''}${data.default_town_mask?'':'；未绑定访问格'}`;grid.appendChild(a);
 let s={row,canvas:a.querySelector('canvas'),mode:a.querySelector('select'),grid:a.querySelector('.gridbox'),visit:a.querySelector('.visit'),native:a.querySelector('.native'),info:a.querySelector('.coords'),body:img(row.body),overlay:img(row.overlay),ref:img(row.native),nativeFlags:row.native_flags?img(row.native_flags):null,offset:[[0,0],[0,0]],bodyOffset:[0,0],flagAttach:[null,null],marks:{left:null,right:null,gate:null,road:null}};states[row.identity]=s;
 for(let im of [s.body,s.overlay,s.ref,s.nativeFlags].filter(Boolean))im.onload=()=>draw(s);s.grid.onchange=s.visit.onchange=s.native.onchange=()=>draw(s);a.querySelector('.reset').onclick=()=>{s.offset=[[0,0],[0,0]];s.bodyOffset=[0,0];draw(s)};
 a.querySelector('.align').onclick=()=>{if(!s.marks.left||!s.marks.right||!s.flagAttach[0]||!s.flagAttach[1]){s.info.textContent='请先分别标记左右杆顶及左右旗面挂点。';return}for(let i=0;i<2;i++){let p=i?s.marks.right:s.marks.left;s.offset[i]=[p[0]+s.bodyOffset[0]-s.flagAttach[i][0],p[1]+s.bodyOffset[1]-s.flagAttach[i][1]]}draw(s)};
 let start=null;s.canvas.onpointerdown=e=>{if(s.native.checked)return;let p=point(e,s.canvas),mode=s.mode.value;if(mode==='flag-left'||mode==='flag-right'||mode==='body'){let i=mode==='flag-left'?0:1;start={p,i,body:mode==='body',old:[...(mode==='body'?s.bodyOffset:s.offset[i])]};s.canvas.setPointerCapture(e.pointerId)}else if(mode.startsWith('attach-')){let i=mode==='attach-left'?0:1;s.flagAttach[i]=[p[0]-s.offset[i][0],p[1]-s.offset[i][1]];draw(s)}else{s.marks[mode]=mode==='road'?p:[p[0]-s.bodyOffset[0],p[1]-s.bodyOffset[1]];draw(s)}};
 s.canvas.onpointermove=e=>{if(!start)return;let p=point(e,s.canvas),o=[start.old[0]+p[0]-start.p[0],start.old[1]+p[1]-start.p[1]];if(start.body)s.bodyOffset=o;else s.offset[start.i]=o;draw(s)};s.canvas.onpointerup=()=>{start=null};draw(s)}
for(const row of data.items)setup(row);
document.getElementById('save').onclick=()=>{let items=data.items.map(r=>{let s=states[r.identity];return {identity:r.identity,body_sha256:r.body_sha256,overlay_sha256:r.overlay_sha256,left_offset_3x:s.offset[0],right_offset_3x:s.offset[1],body_offset_3x:s.bodyOffset,flag_attach_original_3x:s.flagAttach,marks_3x:s.marks}});let o={schema:'h3.town-flag-overlay-layout.v1',manifest_sha256:data.manifest_sha256,items,status:'REVIEW_REQUIRED'};let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(o,null,2)],{type:'application/json'}));a.download='town-flag-layout.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
document.getElementById('load').onchange=async e=>{try{let o=JSON.parse(await e.target.files[0].text());if(o.schema!=='h3.town-flag-overlay-layout.v1'||o.manifest_sha256!==data.manifest_sha256)throw Error('布局和当前快照不匹配');for(let r of o.items){let s=states[r.identity];if(!s||r.body_sha256!==s.row.body_sha256||r.overlay_sha256!==s.row.overlay_sha256)throw Error('素材 SHA 不匹配');let body=r.body_offset_3x||[0,0];for(let p of [r.left_offset_3x,r.right_offset_3x,body])if(!Array.isArray(p)||p.length!==2||!p.every(Number.isInteger))throw Error('位移必须为整数');s.offset=[r.left_offset_3x,r.right_offset_3x];s.bodyOffset=body;s.flagAttach=r.flag_attach_original_3x||[null,null];s.marks=r.marks_3x||s.marks;draw(s)}document.getElementById('message').textContent='已载入布局；仍须复核。'}catch(ex){document.getElementById('message').textContent=ex.message}};
</script></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--manifest", type=Path)
    parser.add_argument("--reference-request", type=Path)
    parser.add_argument("--reference-request-sha256")
    parser.add_argument("--sprites3x-root", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--extra-identity", action="append", help="Add another DEF from original 1X references and current MOD")
    parser.add_argument("--town-mask-config", type=Path, help="Pin VCMI default town mask for visitable-tile display")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--layout-sha256")
    parser.add_argument("--export-body-translation", action="store_true",
                        help="Explicitly export a losslessly shifted 3X body candidate within native alpha bounds")
    args = parser.parse_args()
    try:
        if args.freeze:
            require(not args.export_body_translation, "BODY_TRANSLATION_REQUIRES_LAYOUT")
            require(all((args.reference_request, args.reference_request_sha256, args.sprites3x_root,
                         args.operation_id, args.out)), "FREEZE_ARGUMENTS_REQUIRED")
            result = freeze_mod(args.reference_request, args.reference_request_sha256, args.sprites3x_root,
                                args.operation_id, args.out, args.extra_identity, args.town_mask_config)
        elif args.layout:
            require(all((args.manifest, args.manifest_sha256, args.outdir)), "REVIEW_ARGUMENTS_REQUIRED")
            require(bool(args.layout_sha256), "LAYOUT_SHA_REQUIRED")
            result = export_overlays(args.manifest, args.manifest_sha256, args.layout, args.layout_sha256,
                                     args.outdir, args.export_body_translation)
        else:
            require(not args.export_body_translation, "BODY_TRANSLATION_REQUIRES_LAYOUT")
            require(all((args.manifest, args.manifest_sha256, args.outdir)), "REVIEW_ARGUMENTS_REQUIRED")
            result = build_review(args.manifest, args.manifest_sha256, args.outdir)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False)); return 2


if __name__ == "__main__":
    sys.exit(main())
