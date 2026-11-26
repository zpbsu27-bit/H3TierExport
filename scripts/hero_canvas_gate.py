"""Mandatory post-export canvas x3 + origin x3 gate for mounted-hero (AH*) DEFs.

A mounted-hero 3X frame must keep the FULL official canvas (canvas_1x * 3) with
its artwork placed at the official origin (origin_1x * 3) inside the official
crop. An export collapsed to crop_1x * 3 loses the transparent margin and the
anchor, so the engine mis-places the sprite (the horse walks into walls). This
gate reads the built-in 1999 Complete DEF geometry (the same authority as
flag-pole-audit) and the accepted full-canvas sprite standard, and refuses any
AH* output that is not full canvas x3 with alpha inside the official crop.

check / verify are read-only and fail closed. run additionally re-pads a
collapsed crop x3 output back onto canvas x3 (lossless, no resampling), or
clips only a tiny low-alpha fringe outside the official crop (at most 64 pixels
with alpha <= 8), into a NEW isolated candidate directory. Larger/visible
out-of-crop content remains blocked for deliberate repair; the mode never edits
a master, formal 3X delivery, MOD, or original DEF/LOD in place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

from PIL import Image, ImageChops, ImageOps

import flag_pole_audit
from campaign_vcmi_mod import assert_plain, validate_full_canvas_sprite

PRODUCT = Path(__file__).resolve().parents[1]
SCHEMA = "h3.hero-canvas-gate.receipt.v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FRAME = re.compile(r"^(0|[1-9][0-9]*)_(0|[1-9][0-9]*)\.png\Z")
IDENTITY = re.compile(r"^[A-Z0-9_]{3,24}\Z")
MAX_ITEMS = 10000
MAX_SAFE_CROP_CLIP_ALPHA = 8
MAX_SAFE_CROP_CLIP_PIXELS = 64


class GateError(ValueError):
    pass


def need(value: bool, message: str) -> None:
    if not value:
        raise GateError(message)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def is_mounted_hero_folder(name: str) -> bool:
    return name.upper().startswith("AH")


def frame_slot(path: Path) -> tuple[int, int]:
    match = FRAME.fullmatch(path.name)
    need(match is not None, "HERO_FRAME_NAME_INVALID: expected <group>_<frame>.png: " + str(path))
    return int(match.group(1)), int(match.group(2))


def load_hero_geometry() -> tuple[dict[str, dict[tuple[int, int], dict]], str]:
    table, reference_sha, _document = flag_pole_audit.load_reference()
    geometry: dict[str, dict[tuple[int, int], dict]] = {}
    for identity, row in table.items():
        if not is_mounted_hero_folder(identity):
            continue
        slots: dict[tuple[int, int], dict] = {}
        for frame in row["frames"]:
            slots[(frame["group"], frame["frame"])] = {
                "canvas": frame["canvas_1x"],
                "crop": frame["crop_1x"],
                "anchor": frame["origin_1x"],
                "native_lod": "H3sprite.lod",
                "resource": frame.get("resource"),
            }
        geometry[identity.upper()] = slots
    need(geometry, "HERO_GEOMETRY_TABLE_EMPTY")
    return geometry, reference_sha


def native_geometry(geometry: dict, identity: str, group: int, frame: int) -> dict:
    slots = geometry.get(identity.upper())
    need(slots is not None, "HERO_IDENTITY_NOT_IN_1999_TABLE: " + identity)
    native = slots.get((group, frame))
    need(native is not None, f"HERO_FRAME_NOT_IN_1999_TABLE: {identity} {group}_{frame}")
    return native


def repad_to_full_canvas(source: Path, native: dict) -> Image.Image:
    canvas, crop, origin = native["canvas"], native["crop"], native["anchor"]
    target = (canvas[0] * 3, canvas[1] * 3)
    collapsed = (crop[0] * 3, crop[1] * 3)
    with Image.open(source) as image:
        image.load()
        need(image.mode == "RGBA", "HERO_COLLAPSED_SOURCE_NOT_RGBA: " + str(source))
        need(image.size == collapsed,
             f"HERO_REPAD_SOURCE_NOT_CROP_X3: {source} size {image.size} != crop x3 {collapsed}")
        content = image.convert("RGBA")
    full = Image.new("RGBA", target, (0, 0, 0, 0))
    # Copy RGBA verbatim (no alpha mask): a mask would premultiply semi-transparent
    # edge RGB onto the transparent canvas and darken the original straight-alpha pixels.
    full.paste(content, (origin[0] * 3, origin[1] * 3))
    return full


def clip_tiny_alpha_outside_official_crop(source: Path, native: dict) -> tuple[Image.Image, int, int]:
    """Clear only a tiny low-alpha resampling fringe outside the official 3X crop.

    This never changes pixels inside the native crop, never resamples, and is
    bounded so meaningful artwork outside the crop still fails closed for review.
    """
    canvas, crop, origin = native["canvas"], native["crop"], native["anchor"]
    expected = (canvas[0] * 3, canvas[1] * 3)
    rect = (origin[0] * 3, origin[1] * 3,
            (origin[0] + crop[0]) * 3, (origin[1] + crop[1]) * 3)
    with Image.open(source) as image:
        image.load()
        need(image.mode == "RGBA" and image.size == expected,
             "HERO_CROP_CLIP_REQUIRES_FULL_CANVAS_RGBA_3X")
        content = image.copy()
    crop_mask = Image.new("L", expected, 0)
    crop_mask.paste(255, rect)
    outside_alpha = ImageChops.multiply(content.getchannel("A"), ImageOps.invert(crop_mask))
    histogram = outside_alpha.histogram()
    pixels = sum(histogram[1:])
    maximum = max((alpha for alpha, count in enumerate(histogram) if alpha and count), default=0)
    need(pixels > 0, "HERO_CROP_CLIP_HAS_NO_OUTSIDE_ALPHA")
    need(pixels <= MAX_SAFE_CROP_CLIP_PIXELS and maximum <= MAX_SAFE_CROP_CLIP_ALPHA,
         f"HERO_ALPHA_OUTSIDE_CROP_NOT_SAFE_TO_CLIP: pixels={pixels},max_alpha={maximum}")
    content.putalpha(ImageChops.multiply(content.getchannel("A"), crop_mask))
    return content, pixels, maximum


def gate_item(item: dict, geometry: dict, mode: str, outdir: Path | None,
              batch_root: Path) -> dict:
    source = Path(item["source"])
    identity = source.parent.name
    output = Path(item["output"])
    group, frame = frame_slot(output)
    native = native_geometry(geometry, identity, group, frame)
    canvas, crop, origin = native["canvas"], native["crop"], native["anchor"]
    expected = [canvas[0] * 3, canvas[1] * 3]
    crop_rect = [origin[0] * 3, origin[1] * 3,
                 (origin[0] + crop[0]) * 3, (origin[1] + crop[1]) * 3]
    row: dict[str, Any] = {
        "id": item.get("id"), "identity": identity.upper(), "group": group, "frame": frame,
        "output": str(output), "canvas_1x": canvas, "crop_1x": crop, "origin_1x": origin,
        "expected_full_canvas_3x": expected, "official_crop_rect_3x": crop_rect,
        "normalized": False,
    }
    if not output.is_file():
        row["status"] = "MISSING"
        row["error"] = "HERO_OUTPUT_MISSING: " + str(output)
        return row
    with Image.open(output) as image:
        image.load()
        size = list(image.size)
        rgba = image.mode == "RGBA"
    row["output_pixels"] = size
    if not rgba:
        row["status"] = "BLOCKED"
        row["error"] = "HERO_OUTPUT_NOT_RGBA: " + str(output)
        return row
    collapsed = size == [crop[0] * 3, crop[1] * 3]
    if size == expected:
        try:
            geometry_result = validate_full_canvas_sprite(output, native, identity.upper(), group, frame)
            row["status"] = "PASS"
            row["source_alpha_bbox_3x"] = geometry_result["source_alpha_bbox_3x"]
        except ValueError as exc:
            if mode == "run" and "MAP_SPRITE_ALPHA_OUTSIDE_OFFICIAL_CROP" in str(exc):
                need(outdir is not None, "HERO_RUN_REQUIRES_OUTDIR")
                relative = output.relative_to(batch_root) if output.is_relative_to(batch_root) else Path(output.name)
                target = outdir / relative
                need(not target.exists(), "HERO_NORMALIZED_TARGET_ALREADY_EXISTS: " + str(target))
                try:
                    normalized, clipped_pixels, maximum_alpha = clip_tiny_alpha_outside_official_crop(output, native)
                except GateError as clip_error:
                    row["status"] = "BLOCKED"
                    row["error"] = str(clip_error)
                    return row
                target.parent.mkdir(parents=True, exist_ok=True)
                normalized.save(target, format="PNG")
                row["normalized"] = True
                row["normalized_path"] = str(target)
                row["normalization"] = "ZERO_TINY_ALPHA_OUTSIDE_OFFICIAL_CROP"
                row["normalized_alpha_pixels_removed"] = clipped_pixels
                row["maximum_alpha_removed"] = maximum_alpha
                row["source_output_sha256"] = sha256(output)
                row["normalized_sha256"] = sha256(target)
                try:
                    geometry_result = validate_full_canvas_sprite(target, native, identity.upper(), group, frame)
                    row["status"] = "PASS"
                    row["source_alpha_bbox_3x"] = geometry_result["source_alpha_bbox_3x"]
                except ValueError as clipped_error:
                    row["status"] = "BLOCKED"
                    row["error"] = str(clipped_error)
                return row
            row["status"] = "BLOCKED"
            row["error"] = str(exc)
        return row
    if collapsed and mode == "run":
        need(outdir is not None, "HERO_RUN_REQUIRES_OUTDIR")
        relative = output.relative_to(batch_root) if output.is_relative_to(batch_root) else Path(output.name)
        target = outdir / relative
        need(not target.exists(), "HERO_NORMALIZED_TARGET_ALREADY_EXISTS: " + str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        full = repad_to_full_canvas(output, native)
        full.save(target, format="PNG")
        row["normalized"] = True
        row["normalized_path"] = str(target)
        row["source_output_sha256"] = sha256(output)
        row["normalized_sha256"] = sha256(target)
        try:
            geometry_result = validate_full_canvas_sprite(target, native, identity.upper(), group, frame)
            row["status"] = "PASS"
            row["source_alpha_bbox_3x"] = geometry_result["source_alpha_bbox_3x"]
        except ValueError as exc:
            row["status"] = "BLOCKED"
            row["error"] = str(exc)
        return row
    if collapsed:
        row["status"] = "COLLAPSED"
        row["error"] = (f"HERO_CANVAS_X3_REQUIRED: {identity.upper()} {group}_{frame} output {size} "
                        f"is crop x3; full canvas x3 {expected} at origin x3 is required "
                        "(run mode re-pads losslessly into a new candidate directory)")
        return row
    row["status"] = "BLOCKED"
    row["error"] = (f"HERO_OUTPUT_WRONG_SIZE: {identity.upper()} {group}_{frame} output {size} "
                    f"is neither full canvas x3 {expected} nor crop x3 {[crop[0]*3, crop[1]*3]}")
    return row


def run_gate(batch_path: Path, batch_sha256: str, mode: str, outdir: Path | None) -> dict:
    need(mode in ("check", "verify", "run"), "HERO_GATE_MODE_INVALID")
    need(SHA256.fullmatch(batch_sha256) is not None, "HERO_BATCH_SHA256_INVALID")
    assert_plain(batch_path)
    need(batch_path.is_file(), "HERO_BATCH_NOT_FOUND: " + str(batch_path))
    need(sha256(batch_path) == batch_sha256, "HERO_BATCH_SHA256_DRIFT")
    batch = json.loads(batch_path.read_text(encoding="utf-8-sig"))
    need(isinstance(batch, dict) and isinstance(batch.get("items"), list), "HERO_BATCH_INVALID")
    need(len(batch["items"]) <= MAX_ITEMS, "HERO_BATCH_TOO_LARGE")
    batch_root = Path(batch.get("output_root", str(batch_path.parent)))
    if mode == "run":
        need(outdir is not None, "HERO_RUN_REQUIRES_OUTDIR")
        assert_plain(outdir, must_exist=False)
        need(not outdir.exists(), "HERO_OUTDIR_ALREADY_EXISTS: " + str(outdir))
        outdir.mkdir(parents=True, exist_ok=True)
    geometry, reference_sha = load_hero_geometry()
    items = []
    for item in batch["items"]:
        source = Path(item.get("source", ""))
        if not is_mounted_hero_folder(source.parent.name):
            continue
        items.append(gate_item(item, geometry, mode, outdir, batch_root))
    counts = {"pass": 0, "blocked": 0, "collapsed": 0, "missing": 0, "normalized": 0}
    for row in items:
        status = row["status"]
        if status == "PASS":
            counts["pass"] += 1
        elif status == "COLLAPSED":
            counts["collapsed"] += 1
            counts["blocked"] += 1
        elif status == "MISSING":
            counts["missing"] += 1
            counts["blocked"] += 1
        else:
            counts["blocked"] += 1
        if row.get("normalized"):
            counts["normalized"] += 1
    blocked = counts["blocked"]
    receipt = {
        "schema": SCHEMA, "mode": mode,
        "batch": {"path": str(batch_path.resolve()), "sha256": batch_sha256},
        "flag_reference_sha256": reference_sha,
        "native_geometry_contract": "official-full-canvas-3x-alpha-within-crop",
        "hero_items": len(items), "counts": counts,
        "status": "BLOCKED" if blocked else "PASS",
        "items": items,
        "model_inference_performed": False, "art_approved": False, "game_deployed": False,
        "master_untouched": True, "source_unchanged": True,
    }
    if outdir is not None:
        receipt["outdir"] = str(outdir.resolve())
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hero-canvas-gate", description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--batch-sha256", required=True)
    parser.add_argument("--mode", choices=("check", "verify", "run"), default="verify")
    parser.add_argument("--outdir")
    parser.add_argument("--report")
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        outdir = Path(args.outdir) if args.outdir else None
        receipt = run_gate(Path(args.batch), args.batch_sha256, args.mode, outdir)
        if args.report:
            report = Path(args.report)
            assert_plain(report, must_exist=False)
            need(not report.exists(), "HERO_REPORT_ALREADY_EXISTS: " + str(report))
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_bytes(json_bytes(receipt))
        if not args.quiet:
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 2 if receipt["status"] == "BLOCKED" else 0
    except (GateError, ValueError, OSError, json.JSONDecodeError) as exc:
        error = {"schema": "h3.hero-canvas-gate.error.v1", "status": "FAILED", "error": str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
