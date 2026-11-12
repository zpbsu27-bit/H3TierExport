"""Build a reproducible 1999 Complete DEF geometry/flag catalogue from one LOD.

The catalogue contains only facts read from the supplied original H3sprite.lod.
Palette-index-5 flag faces are recognised against the original AVMGEMS0 face;
partially obscured shapes are kept as candidates, never promoted to exact points.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image

from build_official_def_frame_map import parse_def, read_lod
from native_h3_io import decode_native_pixels


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def components(mask: Image.Image) -> list[dict]:
    pixels = mask.load()
    seen: set[tuple[int, int]] = set()
    found = []
    for y in range(mask.height):
        for x in range(mask.width):
            if not pixels[x, y] or (x, y) in seen:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            points = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if 0 <= nx < mask.width and 0 <= ny < mask.height and pixels[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            found.append({"pixels": len(points), "bbox": [min(x for x, _ in points), min(y for _, y in points),
                                                           max(x for x, _ in points) + 1, max(y for _, y in points) + 1],
                          "points": points})
    return sorted(found, key=lambda x: (x["bbox"][1], x["bbox"][0]))


def geometry(slot: dict) -> dict:
    h = slot["frame_header"]
    cw, ch, w, height, x, y = h[2:8]
    valid = cw > 0 and ch > 0 and w > 0 and height > 0 and x >= 0 and y >= 0 and x + w <= cw and y + height <= ch
    return {"group": slot["group_id"], "frame": slot["frame_index"], "resource": slot["resource"],
            "canvas_1x": [cw, ch], "crop_1x": [w, height], "origin_1x": [x, y],
            "geometry_status": "VALID" if valid else "UNRESOLVED_INVALID_FRAME_HEADER",
            "source_frame_offset": slot["offset"]}


def native_mask(blob: bytes, slot: dict, geo: dict) -> Image.Image:
    w, h = geo["crop_1x"]
    pixels = decode_native_pixels(blob, slot)
    crop = Image.frombytes("L", (w, h), bytes(255 if value == 5 else 0 for value in pixels))
    mask = Image.new("L", tuple(geo["canvas_1x"]), 0)
    mask.paste(crop, tuple(geo["origin_1x"]))
    crop.close()
    return mask


def canonical_face(defs: dict) -> set[tuple[int, int]]:
    blob = defs["AVMGEMS0"]["bytes"]
    slot = next(s for s in parse_def(blob)["slots"] if (s["group_id"], s["frame_index"]) == (0, 0))
    mask = native_mask(blob, slot, geometry(slot))
    try:
        faces = [c for c in components(mask) if c["pixels"] == 42
                 and c["bbox"][2] - c["bbox"][0] == 14 and c["bbox"][3] - c["bbox"][1] == 5]
        if len(faces) != 1:
            raise ValueError("1999_CANONICAL_FACE_NOT_UNIQUE")
        face = faces[0]
        x0, y0 = face["bbox"][:2]
        return {(x - x0, y - y0) for x, y in face["points"]}
    finally:
        mask.close()


def inspect_flags(blob: bytes, slot: dict, geo: dict, template: set[tuple[int, int]]) -> dict:
    if geo["geometry_status"] != "VALID":
        return {"status": "UNRESOLVED_INVALID_FRAME_HEADER", "anchors": [], "partial_flags": []}
    mask = native_mask(blob, slot, geo)
    try:
        templates = [(False, template), (True, {(13 - x, y) for x, y in template})]
        anchors, partial = [], []
        for c in components(mask):
            x0, y0, x1, y1 = c["bbox"]
            rel = {(x - x0, y - y0) for x, y in c["points"]}
            full = [(mirrored, t) for mirrored, t in templates
                    if x1 - x0 == 14 and y1 - y0 == 5 and c["pixels"] in (41, 42) and rel <= t]
            if len(full) == 1:
                mirrored = full[0][0]
                # This is a visible flag-edge pixel, not a claim about a separate support-pole tip.
                edge = [x0 if mirrored else x1 - 1, y0]
                anchors.append({"x": x0, "y": y0, "bbox_1x": c["bbox"], "pixels": c["pixels"],
                                "mirrored": mirrored, "attachment_edge_pixel_1x": edge,
                                "attachment_side_source": "1999 palette-5 face shape; inferred"})
            elif y1 - y0 == 5 and 10 <= x1 - x0 <= 14 and 30 <= c["pixels"] <= 40:
                possibilities = []
                observed = set(c["points"])
                for mirrored, t in templates:
                    for left in range(max(0, x1 - 14), min(x0, geo["canvas_1x"][0] - 14) + 1):
                        if all((x - left, y - y0) in t for x, y in observed):
                            possibilities.append({"face_bbox_candidate_1x": [left, y0, left + 14, y0 + 5],
                                                  "mirrored": mirrored})
                if possibilities:
                    partial.append({"observed_bbox_1x": c["bbox"], "pixels": c["pixels"],
                                    "face_candidates": possibilities})
        status = "COMPLETE_AND_PARTIAL" if anchors and partial else "COMPLETE" if anchors else "PARTIAL" if partial else "NO_FLAG_PATTERN"
        return {"status": status, "anchors": anchors, "partial_flags": partial,
                "palette5_mask_sha256": digest(mask.tobytes())}
    finally:
        mask.close()


def build(archive: Path, expected_sha: str) -> dict:
    raw = archive.read_bytes()
    archive_sha = digest(raw)
    if archive_sha != expected_sha.lower():
        raise ValueError("SOURCE_LOD_SHA_MISMATCH")
    version, defs = read_lod(archive)
    template = canonical_face(defs)
    rows, counts = [], Counter()
    for ident in sorted(defs):
        blob = defs[ident]["bytes"]
        parsed = parse_def(blob)
        frames, scan_cache = [], {}
        for slot in parsed["slots"]:
            geo = geometry(slot)
            counts["logical_frames"] += 1
            if geo["geometry_status"] != "VALID":
                counts["invalid_frame_headers"] += 1
            if ident.startswith("AV"):
                key = slot["offset"]
                if key not in scan_cache:
                    scan_cache[key] = inspect_flags(blob, slot, geo, template)
                flags = scan_cache[key]
            else:
                flags = {"status": "NOT_SCANNED_NON_ADVENTURE", "anchors": [], "partial_flags": []}
            frames.append({**geo, "flags": flags})
        counts["identities"] += 1
        if ident.startswith("AVM"):
            counts["mine_identities"] += 1
            counts["mine_logical_frames"] += len(frames)
            if frames and frames[0]["flags"]["anchors"]:
                counts["mine_with_complete_1999_flag"] += 1
            if frames and frames[0]["flags"]["partial_flags"]:
                counts["mine_with_partial_1999_flag"] += 1
        rows.append({"identity": ident, "source_def_sha256": digest(blob), "frames": frames})
    return {"schema": "h3.flag-pole-reference-1999.v2",
            "provenance": {"source": "1999 Complete original H3sprite.lod DEF frame headers and palette index 5",
                           "archive_name": archive.name, "archive_sha256": archive_sha,
                           "archive_bytes": len(raw), "lod_version": version,
                           "canonical_flag_def": "AVMGEMS0.DEF group 0 frame 0",
                           "canonical_flag_def_sha256": digest(defs["AVMGEMS0"]["bytes"]),
                           "flag_edge_policy": "visible 1999 flag-face attachment edge; pole head/length require separate review"},
            "counts": dict(counts), "entries": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.archive.is_absolute() or not args.out.is_absolute() or args.out.exists():
        parser.error("absolute archive and new absolute output paths are required")
    result = build(args.archive, args.archive_sha256)
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    encoded = gzip.compress(payload, compresslevel=9, mtime=0)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as stream:
        stream.write(encoded)
    print(json.dumps({"status": "OK", "output": str(args.out), "sha256": digest(encoded),
                      "bytes": len(encoded), "counts": result["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
