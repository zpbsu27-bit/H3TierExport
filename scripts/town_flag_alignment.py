#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create bottom-right registered town 3X candidates and fixed-flag review sheets.

This workflow never edits a master, formal 3X asset, or MOD file. The body is
exported through the invoking H3TierExport binary with the official town frame
and bottom_right registration. Flag previews are rendered from the VCMI
spriteFlagsInfo/flag_grey pair. Identities without an exact key use the original
1999 DEF palette-index-5 owner-flag plane; coordinates are never guessed from a
modern MOD overlay.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps


REQUEST_SCHEMA = "h3.town-flag-alignment-request.v3"
LOCK_SCHEMA = "h3.town-flag-alignment-lock.v2"
REPORT_SCHEMA = "h3.town-flag-alignment-report.v2"
VERIFY_SCHEMA = "h3.town-flag-alignment-verify.v2"
DEFAULT_IDENTITIES = (
    "AVCCAST0", "AVCCASX0", "AVCDUNG0", "AVCDUNX0", "AVCFTRT0", "AVCFTRX0",
    "AVCHFOR0", "AVCHFORZ", "AVCINFT0", "AVCINFX0", "AVCNECR0", "AVCNECX0",
    "AVCRAMP0", "AVCRAMX0", "AVCSTRO0", "AVCSTRX0", "AVCTOWR0", "AVCTOWX0",
)
BASE_PIXELS = (192, 192)
TARGET_SCALE = 3
GRID_PIXELS_3X = 96
FLAG_GLYPH_PIXELS_3X = (43, 25)
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
IDENTITY_RE = re.compile(r"^[A-Z0-9_]{4,24}$")


class AlignmentFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AlignmentFailure(code, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(value)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise AlignmentFailure("OUTPUT_ALREADY_EXISTS", f"Refusing to overwrite existing file: {path}") from error


def _read_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AlignmentFailure("JSON_READ_FAILED", f"Cannot read {field} {path}: {error}") from error
    _need(isinstance(value, dict), "JSON_ROOT_NOT_OBJECT", f"{field} must contain a JSON object")
    return value


def _absolute(value: Any, field: str) -> Path:
    if isinstance(value, Path):
        path = value
    elif isinstance(value, str) and value.strip():
        path = Path(value)
    else:
        raise AlignmentFailure("PATH_MISSING", f"{field} is required")
    _need(path.is_absolute(), "PATH_NOT_ABSOLUTE", f"{field} must be an absolute path: {path}")
    return path.resolve()


def _parse_identity_map(values: list[str], field: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        identity, sep, raw_path = value.partition("=")
        identity = identity.strip().upper()
        _need(bool(sep) and IDENTITY_RE.fullmatch(identity) is not None and bool(raw_path.strip()),
              "MAPPING_INVALID", f"{field} must be ID=ABSOLUTE_PATH; got {value!r}")
        path = _absolute(raw_path.strip(), field)
        _need(identity not in result, "MAPPING_DUPLICATE", f"Duplicate {field} identity {identity}")
        result[identity] = path
    return result


def _read_sprite_flags(path: Path, identities: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Match VCMI: duplicate rows may repeat only identical values; last identical row wins."""
    _need(path.is_file(), "SPRITE_FLAGS_INFO_MISSING", f"spriteFlagsInfo.txt not found: {path}")
    parsed: dict[str, list[dict[str, Any]]] = {}
    raw = path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(raw.splitlines(), 1):
        columns = line.split()
        if not columns or columns[0].startswith("#"):
            continue
        identity = columns[0].upper()
        if identity not in identities:
            continue
        try:
            count = int(columns[1])
            _need(count == 2 and len(columns) == 2 + count * 3,
                  "FLAG_COORDINATE_ROW_INVALID",
                  f"{path}:{line_number} must contain exactly two [x,y,mirror] flag anchors")
            anchors = []
            for index in range(count):
                x, y, mirror = (int(v) for v in columns[2 + 3 * index:5 + 3 * index])
                _need(x >= 0 and y >= 0 and mirror in (0, 1), "FLAG_COORDINATE_INVALID",
                      f"{path}:{line_number} has an invalid x/y/mirror value")
                anchors.append({"x": x, "y": y, "mirrored": bool(mirror)})
        except (ValueError, IndexError) as error:
            raise AlignmentFailure("FLAG_COORDINATE_ROW_INVALID",
                                   f"Cannot parse {path}:{line_number}: {error}") from error
        if identity in parsed:
            _need(parsed[identity] == anchors, "FLAG_COORDINATE_DUPLICATE_CONFLICT",
                  f"Duplicate rows for {identity} disagree; refuse ambiguous flag anchors")
        parsed[identity] = anchors
    return parsed


def _decode_def_body(body: bytes, width: int, height: int, fmt: int) -> bytes:
    if fmt == 0:
        _need(len(body) == width * height, "DEF_RAW_LENGTH_INVALID", "Raw DEF body length differs from geometry")
        return body
    if fmt not in (1, 2, 3):
        raise AlignmentFailure("DEF_CODEC_UNSUPPORTED", f"Unsupported original DEF frame codec {fmt}")
    if fmt == 3:
        _need(width % 32 == 0, "DEF_FORMAT3_WIDTH_INVALID", "Original DEF format 3 width must be a multiple of 32")
        block_count = height * (width // 32)
        _need(len(body) >= 2 * block_count, "DEF_OFFSET_TABLE_TRUNCATED", "Original DEF format 3 offset table is truncated")
        offsets = struct.unpack_from("<" + "H" * block_count, body, 0)
        block_width = 32
    elif fmt == 2:
        _need(len(body) >= 2 * height, "DEF_OFFSET_TABLE_TRUNCATED", "Original DEF format 2 offset table is truncated")
        offsets = struct.unpack_from("<" + "H" * height, body, 0)
        block_count = height
        block_width = width
    else:
        _need(len(body) >= 4 * height, "DEF_OFFSET_TABLE_TRUNCATED", "Original DEF format 1 offset table is truncated")
        offsets = struct.unpack_from("<" + "I" * height, body, 0)
        block_count = height
        block_width = width
    decoded = bytearray()
    for index in range(block_count):
        position = offsets[index]
        row = bytearray()
        while len(row) < block_width:
            _need(position < len(body), "DEF_RLE_TRUNCATED", "Original DEF run stream ends early")
            code = body[position]
            position += 1
            if fmt == 1:
                _need(position < len(body), "DEF_RLE_TRUNCATED", "Original DEF run length is missing")
                length = body[position] + 1
                position += 1
                literal = code == 255
                value = code
            else:
                length = (code & 31) + 1
                value = code >> 5
                literal = value == 7
            _need(len(row) + length <= block_width, "DEF_RLE_OVERRUN", "Original DEF run overruns its row/block")
            if literal:
                _need(position + length <= len(body), "DEF_RLE_TRUNCATED", "Original DEF literal run is truncated")
                row.extend(body[position:position + length])
                position += length
            else:
                row.extend(bytes([value]) * length)
        _need(len(row) == block_width, "DEF_RLE_ROW_INVALID", "Original DEF row/block has wrong width")
        decoded.extend(row)
    _need(len(decoded) == width * height, "DEF_DECODED_SIZE_INVALID", "Decoded DEF pixels differ from frame geometry")
    return bytes(decoded)


def _native_palette5(def_path: Path, expected_canvas: tuple[int, int] = BASE_PIXELS) -> tuple[Image.Image, dict[str, Any]]:
    """Extract original 1999 DEF palette index 5 (owner flag) without color-key guessing."""
    _need(def_path.is_file(), "NATIVE_DEF_NOT_FOUND", f"Original 1X DEF not found: {def_path}")
    data = def_path.read_bytes()
    _need(len(data) >= 784, "NATIVE_DEF_HEADER_TRUNCATED", f"Original DEF header is truncated: {def_path}")
    group_count = struct.unpack_from("<I", data, 12)[0]
    _need(0 < group_count <= 64, "NATIVE_DEF_GROUP_COUNT_INVALID", f"Unexpected DEF group count {group_count}")
    cursor = 784
    target_offset = None
    for _ in range(group_count):
        _need(cursor + 16 <= len(data), "NATIVE_DEF_GROUP_TABLE_TRUNCATED", "Original DEF group table is truncated")
        group_id, frame_count = struct.unpack_from("<2I", data, cursor)
        cursor += 16
        _need(frame_count <= 4096, "NATIVE_DEF_FRAME_COUNT_INVALID", "Original DEF frame count is unreasonable")
        cursor += 13 * frame_count
        _need(cursor + 4 * frame_count <= len(data), "NATIVE_DEF_FRAME_TABLE_TRUNCATED", "Original DEF frame table is truncated")
        for frame_index in range(frame_count):
            frame_offset = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            if group_id == 0 and frame_index == 0:
                target_offset = frame_offset
    _need(target_offset is not None and target_offset + 32 <= len(data),
          "NATIVE_DEF_IDLE_FRAME_MISSING", "Original DEF group 0 frame 0 is missing")
    size, fmt, full_width, full_height, width, height, margin_left, margin_top = struct.unpack_from(
        "<8I", data, target_offset)
    _need((full_width, full_height) == expected_canvas, "NATIVE_DEF_CANVAS_MISMATCH",
          f"Original DEF canvas {(full_width, full_height)} differs from {expected_canvas}")
    _need(width > 0 and height > 0 and margin_left + width <= full_width and margin_top + height <= full_height,
          "NATIVE_DEF_CROP_GEOMETRY_INVALID", "Original DEF crop and margins do not fit its full canvas")
    body = data[target_offset + 32:target_offset + 32 + size]
    _need(len(body) == size, "NATIVE_DEF_FRAME_TRUNCATED", "Original DEF frame body is truncated")
    pixels = _decode_def_body(body, width, height, fmt)
    mask = Image.new("L", (full_width, full_height), 0)
    mask_pixels = mask.load()
    for y in range(height):
        offset = y * width
        for x in range(width):
            if pixels[offset + x] == 5:
                mask_pixels[margin_left + x, margin_top + y] = 255
    count = sum(value == 5 for value in pixels)
    bbox = mask.getbbox()
    _need(count > 0 and bbox is not None, "NATIVE_DEF_OWNER_FLAG_MASK_EMPTY",
          f"Original DEF has no palette-index-5 owner flag pixels: {def_path.name}")
    return mask, {"path": str(def_path.resolve()), "sha256": _sha256(def_path),
                  "group": 0, "frame": 0, "codec": fmt, "crop_pixels": [width, height],
                  "full_canvas_pixels": [full_width, full_height],
                  "margin_left_top": [margin_left, margin_top],
                  "palette_index": 5, "palette5_pixel_count": count,
                  "palette5_bbox_1x": list(bbox),
                  "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}


def _palette5_components(mask: Image.Image) -> list[dict[str, Any]]:
    pixels = mask.load()
    seen: set[tuple[int, int]] = set()
    components: list[dict[str, Any]] = []
    for y in range(mask.height):
        for x in range(mask.width):
            if pixels[x, y] == 0 or (x, y) in seen:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            xs, ys = [], []
            while stack:
                px, py = stack.pop()
                xs.append(px); ys.append(py)
                for neighbor in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    nx, ny = neighbor
                    if 0 <= nx < mask.width and 0 <= ny < mask.height and pixels[nx, ny] and neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            components.append({"pixels": len(xs), "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1]})
    components.sort(key=lambda component: (component["bbox"][1], component["bbox"][0]))
    return components


def _crosscheck_native_flags(identity: str, anchors: list[dict[str, Any]], native_mask: Image.Image) -> dict[str, Any]:
    components = _palette5_components(native_mask)
    flag_components = [component for component in components
                       if component["pixels"] == 42
                       and component["bbox"][2] - component["bbox"][0] == 14
                       and component["bbox"][3] - component["bbox"][1] == 5]
    if not anchors:
        return {"status": "NO_EXACT_VCMI_KEY_NATIVE_DEF_MASK_USED",
                "palette5_pixel_count": sum(component["pixels"] for component in components),
                "components": components}
    expected = {(anchor["x"], anchor["y"]) for anchor in anchors}
    observed = {(component["bbox"][0], component["bbox"][1]) for component in flag_components}
    _need(expected <= observed, "NATIVE_FLAG_MASK_ANCHOR_MISMATCH",
          f"{identity}: spriteFlagsInfo flag faces {sorted(expected)} do not match original DEF palette-5 faces {sorted(observed)}")
    return {"status": "SPRITE_FLAGS_INFO_CROSSCHECKED_AGAINST_1999_DEF",
            "expected_face_top_left_1x": [list(point) for point in sorted(expected)],
            "observed_face_top_left_1x": [list(point) for point in sorted(observed)],
            "palette5_pixel_count": sum(component["pixels"] for component in components),
            "components": components}


def _png_info(path: Path, field: str) -> tuple[tuple[int, int], str, tuple[int, int, int, int] | None]:
    _need(path.is_file(), "INPUT_NOT_FOUND", f"{field} not found: {path}")
    try:
        with Image.open(path) as opened:
            mode = opened.mode
            image = opened.convert("RGBA")
            size = image.size
            alpha_box = image.getchannel("A").getbbox()
            image.close()
    except (OSError, ValueError) as error:
        raise AlignmentFailure("PNG_READ_FAILED", f"Cannot decode {field} {path}: {error}") from error
    return size, mode, alpha_box


def _load_request(path: Path, expected_sha: str, operation_id: str) -> tuple[dict[str, Any], str]:
    _need(path.is_file(), "REQUEST_NOT_FOUND", f"Request manifest not found: {path}")
    actual_sha = _sha256(path)
    _need(actual_sha == expected_sha.lower(), "REQUEST_SHA_MISMATCH",
          f"Request manifest SHA mismatch: expected {expected_sha}, got {actual_sha}")
    request = _read_json(path, "request manifest")
    _need(request.get("schema") == REQUEST_SCHEMA, "REQUEST_SCHEMA_UNSUPPORTED",
          f"Expected {REQUEST_SCHEMA}, got {request.get('schema')!r}")
    _need(request.get("operation_id") == operation_id, "OPERATION_ID_MISMATCH",
          "Task operation ID must equal the frozen request operation_id")
    _need(OPERATION_ID_RE.fullmatch(operation_id) is not None, "OPERATION_ID_INVALID",
          "operation_id must be a safe ASCII path component")
    _need(request.get("base_pixels") == list(BASE_PIXELS)
          and request.get("target_scale") == TARGET_SCALE
          and request.get("fit_anchor") == "bottom_right",
          "REGISTRATION_CONTRACT_INVALID",
          "Town flag alignment requires official 192x192 frames, 3X output, and fit_anchor=bottom_right")
    items = request.get("items")
    _need(isinstance(items, list) and 1 <= len(items) <= 36, "REQUEST_ITEMS_INVALID",
          "Request must contain between 1 and 36 town slots")
    identities = [str(item.get("identity", "")).upper() for item in items if isinstance(item, dict)]
    _need(len(identities) == len(items) and len(set(identities)) == len(identities),
          "REQUEST_IDENTITIES_INVALID", "Request identities must be unique item objects")
    return request, actual_sha


def _resolve_item(request_path: Path, request: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    identity = str(item.get("identity", "")).upper()
    _need(IDENTITY_RE.fullmatch(identity) is not None, "IDENTITY_INVALID", f"Invalid identity {identity!r}")
    source_root = _absolute(request.get("source_root"), "source_root")
    native_root = _absolute(request.get("native_frames_root"), "native_frames_root")
    native_def_root = _absolute(request.get("native_def_root"), "native_def_root")
    source_rel = item.get("source_relpath")
    _need(isinstance(source_rel, str) and source_rel, "SOURCE_RELPATH_MISSING", f"{identity}: source_relpath required")
    source = (source_root / source_rel).resolve()
    _need(source.is_relative_to(source_root), "SOURCE_ESCAPES_ROOT", f"{identity}: source path escapes source_root")
    _need(source.parent.name.upper() == identity, "SOURCE_IDENTITY_MISMATCH",
          f"{identity}: master must be in its official identity directory, got {source.parent.name}")
    _need(source.name == "0_0.png", "SOURCE_FRAME_NAME_INVALID", f"{identity}: source frame must be 0_0.png")
    _need(source.is_file(), "SOURCE_NOT_FOUND", f"{identity}: master not found: {source}")
    source_sha = _sha256(source)
    _need(source_sha == str(item.get("source_sha256", "")).lower(), "SOURCE_SHA_DRIFT",
          f"{identity}: master SHA differs from frozen request")
    source_size, _, source_bbox = _png_info(source, f"{identity} master")
    _need(source_bbox is not None, "SOURCE_ALPHA_EMPTY", f"{identity}: master has no visible alpha")

    reference_rel = item.get("native_reference_relpath")
    _need(isinstance(reference_rel, str) and reference_rel, "REFERENCE_RELPATH_MISSING",
          f"{identity}: native_reference_relpath required")
    reference = (native_root / reference_rel).resolve()
    _need(reference.is_relative_to(native_root), "REFERENCE_ESCAPES_ROOT",
          f"{identity}: native reference path escapes native_frames_root")
    _need(reference.parent.name.upper() == identity and reference.name == "0_0.png",
          "REFERENCE_IDENTITY_MISMATCH", f"{identity}: native reference must be frames/{identity}/0_0.png")
    ref_size, _, ref_bbox = _png_info(reference, f"{identity} official 1X reference")
    _need(ref_size == BASE_PIXELS and ref_bbox is not None, "REFERENCE_GEOMETRY_INVALID",
          f"{identity}: official frame must be a visible 192x192 RGBA canvas")
    ref_sha = _sha256(reference)
    _need(ref_sha == str(item.get("native_reference_sha256", "")).lower(), "REFERENCE_SHA_DRIFT",
          f"{identity}: official 1X frame SHA differs from frozen request")

    def_rel = item.get("native_def_relpath")
    _need(isinstance(def_rel, str) and def_rel, "NATIVE_DEF_RELPATH_MISSING",
          f"{identity}: original 1999 DEF path is required")
    native_def = (native_def_root / def_rel).resolve()
    _need(native_def.is_relative_to(native_def_root), "NATIVE_DEF_ESCAPES_ROOT",
          f"{identity}: native DEF path escapes native_def_root")
    _need(native_def.name.upper() == f"{identity}.DEF", "NATIVE_DEF_IDENTITY_MISMATCH",
          f"{identity}: native DEF filename must be {identity}.DEF")
    native_def_sha = _sha256(native_def) if native_def.is_file() else ""
    _need(native_def_sha == str(item.get("native_def_sha256", "")).lower(), "NATIVE_DEF_SHA_DRIFT",
          f"{identity}: original 1999 DEF SHA differs from frozen request")
    native_mask, native_mask_info = _native_palette5(native_def)
    _need(native_mask_info.get("mask_sha256") == item.get("native_palette5", {}).get("mask_sha256")
          and native_mask_info.get("palette5_pixel_count") == item.get("native_palette5", {}).get("palette5_pixel_count"),
          "NATIVE_DEF_FLAG_MASK_DRIFT", f"{identity}: original palette-5 flag mask differs from frozen request")
    crosscheck = _crosscheck_native_flags(identity, item.get("native_flag_anchors", []), native_mask)
    _need(crosscheck == item.get("native_palette5_crosscheck"), "NATIVE_DEF_FLAG_ANCHOR_CROSSCHECK_DRIFT",
          f"{identity}: 1999 palette-5 mask no longer matches the frozen spriteFlagsInfo cross-check")

    return {"identity": identity, "source": source, "source_sha256": source_sha,
            "source_size": list(source_size), "source_alpha_bbox": list(source_bbox),
            "native_reference": reference, "native_reference_sha256": ref_sha,
            "native_reference_size": list(ref_size), "native_def": native_def,
            "native_def_sha256": native_def_sha, "native_palette5": native_mask_info,
            "native_palette5_crosscheck": crosscheck, "native_palette5_mask": native_mask,
            "item": item}


def _validate_anchor_files(request: dict[str, Any], identities: set[str]) -> tuple[Path, Path, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    anchors = request.get("flag_authority")
    _need(isinstance(anchors, dict), "FLAG_AUTHORITY_MISSING", "flag_authority is required")
    sprite_flags = _absolute(anchors.get("sprite_flags_info"), "flag_authority.sprite_flags_info")
    glyph_path = _absolute(anchors.get("flag_grey"), "flag_authority.flag_grey")
    _need(sprite_flags.is_file() and _sha256(sprite_flags) == str(anchors.get("sprite_flags_info_sha256", "")).lower(),
          "SPRITE_FLAGS_INFO_SHA_DRIFT", "Current spriteFlagsInfo.txt does not match the frozen request")
    _need(glyph_path.is_file() and _sha256(glyph_path) == str(anchors.get("flag_grey_sha256", "")).lower(),
          "FLAG_GLYPH_SHA_DRIFT", "Current flag_grey.png does not match the frozen request")
    glyph_size, _, glyph_bbox = _png_info(glyph_path, "VCMI flag_grey")
    _need(glyph_size == FLAG_GLYPH_PIXELS_3X and glyph_bbox is not None,
          "FLAG_GLYPH_GEOMETRY_INVALID", f"VCMI flag_grey must be {FLAG_GLYPH_PIXELS_3X}, got {glyph_size}")
    table = _read_sprite_flags(sprite_flags, identities)
    rows = anchors.get("anchor_rows")
    _need(isinstance(rows, dict), "ANCHOR_SNAPSHOT_MISSING", "Frozen request must contain anchor_rows")
    for identity in identities:
        _need(rows.get(identity) == table.get(identity), "ANCHOR_SNAPSHOT_DRIFT",
              f"{identity}: current spriteFlagsInfo anchors differ from frozen request")
    warnings = []
    for identity in sorted(identities):
        if identity not in table:
            warnings.append({"identity": identity, "status": "NO_EXACT_FLAG_KEY"})
    return sprite_flags, glyph_path, table, warnings


def _safe_manifest_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        raise AlignmentFailure("PATH_OUTSIDE_ROOT", f"Path {path} is outside {base}")


def freeze_request(args: argparse.Namespace) -> dict[str, Any]:
    source_root = _absolute(args.source_root, "source_root")
    native_root = _absolute(args.native_frames_root, "native_frames_root")
    native_def_root = _absolute(args.native_def_root, "native_def_root")
    sprite_flags = _absolute(args.sprite_flags_info, "sprite_flags_info")
    glyph = _absolute(args.flag_grey, "flag_grey")
    identities = [value.upper() for value in (args.identity or list(DEFAULT_IDENTITIES))]
    _need(len(identities) == len(set(identities)), "IDENTITIES_DUPLICATE", "Identity list contains duplicates")
    _need(all(IDENTITY_RE.fullmatch(value) for value in identities), "IDENTITY_INVALID", "Invalid identity in request")
    masters = _parse_identity_map(args.master or [], "master")
    _need(source_root.is_dir(), "SOURCE_ROOT_NOT_FOUND", f"source_root is not a directory: {source_root}")
    _need(native_root.is_dir(), "NATIVE_FRAMES_ROOT_NOT_FOUND",
          f"native_frames_root is not a directory: {native_root}")
    _need(native_def_root.is_dir(), "NATIVE_DEF_ROOT_NOT_FOUND",
          f"native_def_root is not a directory: {native_def_root}")
    _need(set(masters) <= set(identities), "MASTER_IDENTITY_UNSELECTED", "A --master identity was not selected")
    _need(sprite_flags.is_file() and glyph.is_file(), "FLAG_AUTHORITY_MISSING", "VCMI flag authority files are required")
    table = _read_sprite_flags(sprite_flags, set(identities))
    glyph_size, _, glyph_bbox = _png_info(glyph, "VCMI flag_grey")
    _need(glyph_size == FLAG_GLYPH_PIXELS_3X and glyph_bbox is not None,
          "FLAG_GLYPH_GEOMETRY_INVALID", f"VCMI flag_grey must be {FLAG_GLYPH_PIXELS_3X}, got {glyph_size}")

    matches_by_identity: dict[str, list[Path]] = {identity: [] for identity in identities}
    if len(masters) < len(identities):
        for candidate in source_root.rglob("0_0.png"):
            identity = candidate.parent.name.upper()
            if identity in matches_by_identity:
                matches_by_identity[identity].append(candidate.resolve())

    items = []
    for identity in identities:
        if identity in masters:
            source = masters[identity]
        else:
            matches = matches_by_identity[identity]
            _need(len(matches) == 1, "MASTER_MATCH_NOT_UNIQUE",
                  f"{identity}: expected exactly one source_root/**/{identity}/0_0.png, found {len(matches)}")
            source = matches[0].resolve()
        _need(source.is_relative_to(source_root), "SOURCE_ESCAPES_ROOT", f"{identity}: master is outside source_root")
        _need(source.parent.name.upper() == identity and source.name == "0_0.png",
              "SOURCE_IDENTITY_MISMATCH", f"{identity}: master path must end in {identity}/0_0.png")
        source_size, _, source_bbox = _png_info(source, f"{identity} master")
        _need(source_bbox is not None, "SOURCE_ALPHA_EMPTY", f"{identity}: master has no visible alpha")
        native = native_root / identity / "0_0.png"
        native_size, _, native_bbox = _png_info(native, f"{identity} official reference")
        _need(native_size == BASE_PIXELS and native_bbox is not None,
              "REFERENCE_GEOMETRY_INVALID", f"{identity}: official 1X frame must be visible 192x192")
        native_def = native_def_root / f"{identity}.DEF"
        native_mask, native_mask_info = _native_palette5(native_def)
        native_crosscheck = _crosscheck_native_flags(identity, table.get(identity, []), native_mask)
        native_mask.close()
        item = {
            "identity": identity,
            "source_relpath": _safe_manifest_path(source, source_root),
            "source_sha256": _sha256(source),
            "source_pixels": list(source_size),
            "native_reference_relpath": f"{identity}/0_0.png",
            "native_reference_sha256": _sha256(native),
            "native_reference_pixels": list(native_size),
            "native_flag_anchors": table.get(identity, []),
            "native_def_relpath": native_def.name,
            "native_def_sha256": _sha256(native_def),
            "native_palette5": native_mask_info,
            "native_palette5_crosscheck": native_crosscheck,
        }
        items.append(item)

    _need(OPERATION_ID_RE.fullmatch(args.operation_id) is not None, "OPERATION_ID_INVALID",
          "operation_id must be a safe ASCII path component")
    request = {
        "schema": REQUEST_SCHEMA,
        "operation_id": args.operation_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "selected H3 town map-building frames; default is the current 18-slot town set",
        "source_root": str(source_root),
        "native_frames_root": str(native_root),
        "native_def_root": str(native_def_root),
        "base_pixels": list(BASE_PIXELS),
        "target_scale": TARGET_SCALE,
        "fit_anchor": "bottom_right",
        "flag_authority": {
            "sprite_flags_info": str(sprite_flags),
            "sprite_flags_info_sha256": _sha256(sprite_flags),
            "flag_grey": str(glyph),
            "flag_grey_sha256": _sha256(glyph),
            "flag_grey_pixels_3x": list(glyph_size),
            "anchor_rows": {identity: table.get(identity) for identity in identities},
            "missing_exact_keys": [identity for identity in identities if identity not in table],
            "native_owner_flag_palette_index": 5,
            "native_def_source": "1999 Complete DEF group 0 frame 0 palette index 5; full canvas and margins preserved",
        },
        "items": items,
        "publication": {"master_written": False, "codex_3x_written": False,
                        "mod_written": False, "game_deployed": False},
    }
    destination = _absolute(args.out, "out")
    _need(not destination.exists(), "REQUEST_OUTPUT_EXISTS", f"Refusing to overwrite request: {destination}")
    _need(not destination.is_relative_to(source_root) and not destination.is_relative_to(native_root)
          and not destination.is_relative_to(native_def_root),
          "REQUEST_OUTPUT_INSIDE_ASSET_ROOT", "Save the request outside the master, 1X frame, and native DEF roots")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(_json_bytes(request))
        stream.flush()
        os.fsync(stream.fileno())
    return {"schema": "h3.town-flag-alignment-freeze.v1", "status": "FROZEN",
            "request": str(destination), "request_sha256": _sha256(destination),
            "items": len(items), "missing_exact_keys": request["flag_authority"]["missing_exact_keys"],
            "outputs_written": 1}


def _renderer(args: argparse.Namespace) -> tuple[Path, str]:
    path = _absolute(args.renderer, "renderer")
    _need(path.is_file(), "RENDERER_NOT_FOUND", f"Invoking H3TierExport binary not found: {path}")
    digest = _sha256(path)
    _need(digest == str(args.renderer_sha256 or "").lower(), "RENDERER_SHA_MISMATCH",
          "Invoking H3TierExport binary SHA differs from the companion wrapper identity")
    return path, digest


def _invoke(renderer: Path, arguments: list[str], timeout: int = 3600) -> tuple[int, str, str]:
    try:
        result = subprocess.run([str(renderer), *arguments], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                cwd=str(renderer.parent))
    except subprocess.TimeoutExpired as error:
        raise AlignmentFailure("NATIVE_EXPORT_TIMEOUT", f"H3TierExport command timed out: {arguments[0]}") from error
    except OSError as error:
        raise AlignmentFailure("NATIVE_EXPORT_START_FAILED", f"Cannot start H3TierExport: {error}") from error
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr)[-4000:]
        raise AlignmentFailure("NATIVE_EXPORT_FAILED",
                               f"H3TierExport {arguments[0]} exited {result.returncode}: {tail}")
    return result.returncode, result.stdout, result.stderr


def _validate_anchor_overlay_files(request: dict[str, Any]) -> tuple[Path, Path, dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    identities = {str(item["identity"]).upper() for item in request["items"]}
    return _validate_anchor_files(request, identities)


def check_task(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _absolute(args.manifest, "manifest")
    request, request_sha = _load_request(manifest, args.manifest_sha256, args.operation_id)
    renderer, renderer_sha = _renderer(args)
    output = _absolute(args.outdir, "outdir")
    lock_path = output / "request-lock.json"
    check_path = output / "check-report.json"
    if output.exists():
        if lock_path.is_file() and check_path.is_file():
            lock = _read_json(lock_path, "request lock")
            existing = _read_json(check_path, "check report")
            _need(lock.get("request_sha256") == request_sha and lock.get("operation_id") == args.operation_id
                  and lock.get("renderer_sha256") == renderer_sha,
                  "OPERATION_REQUEST_DRIFT", "Existing output root belongs to a different frozen request or renderer")
            return existing
        _need(not any(output.iterdir()), "OUTPUT_ROOT_NOT_EMPTY",
              f"Output root is not empty and has no matching H3TierExport request lock: {output}")
    else:
        output.mkdir(parents=True, exist_ok=False)

    resolved = [_resolve_item(manifest, request, item) for item in request["items"]]
    sprite_flags, glyph, anchor_table, anchor_warnings = _validate_anchor_overlay_files(request)
    native_palette5_fallbacks = [
        {"identity": row["identity"], "native_def": str(row["native_def"]),
         "native_def_sha256": row["native_def_sha256"],
         "palette_index": 5, "palette5_pixel_count": row["native_palette5"]["palette5_pixel_count"],
         "components": row["native_palette5_crosscheck"]["components"]}
        for row in resolved if row["identity"] not in anchor_table
    ]

    report_dir = output / "reports"
    report_dir.mkdir(exist_ok=True)
    candidate_root = output / "candidate-3x"
    _need(not candidate_root.exists(), "CANDIDATE_ROOT_EXISTS",
          f"Refusing pre-existing candidate output root: {candidate_root}")
    source_root = _absolute(request.get("source_root"), "source_root")
    native_root = _absolute(request.get("native_frames_root"), "native_frames_root")
    native_def_root = _absolute(request.get("native_def_root"), "native_def_root")
    _need(not candidate_root.resolve().is_relative_to(source_root)
          and not candidate_root.resolve().is_relative_to(native_root)
          and not candidate_root.resolve().is_relative_to(native_def_root),
          "CANDIDATE_ROOT_OVERLAPS_SOURCE",
          "Candidate output root must remain outside master, official frame, and native DEF roots")
    candidate_root.mkdir(parents=True, exist_ok=False)
    batch_path = output / "expand-batch.json"
    plan_args = ["expand-plan"]
    for row in resolved:
        plan_args.extend(["--source", str(row["source"])])
    plan_args.extend(["--base-width", "192", "--base-height", "192", "--target-scale", "3",
                      "--reference-root", str(native_root), "--fit-anchor", "bottom_right",
                      "--mirror-source-root", str(source_root), "--output-root", str(candidate_root),
                      "--out", str(batch_path)])
    _invoke(renderer, plan_args)
    _need(batch_path.is_file(), "EXPAND_BATCH_NOT_WRITTEN", "H3TierExport did not create expand-batch.json")
    batch = _read_json(batch_path, "expand-batch")
    batch_items = batch.get("items")
    _need(isinstance(batch_items, list) and len(batch_items) == len(resolved),
          "EXPAND_BATCH_ITEM_COUNT_MISMATCH", "Native expansion plan does not cover every frozen town slot")
    by_source = {os.path.normcase(str(row["source"].resolve())): row for row in resolved}
    planned = []
    for item in batch_items:
        source = Path(item.get("source", "")).resolve()
        row = by_source.get(os.path.normcase(str(source)))
        _need(row is not None, "EXPAND_BATCH_SOURCE_UNEXPECTED", f"Unexpected source in expansion batch: {source}")
        _need(item.get("fit_anchor") == "bottom_right", "EXPAND_BATCH_ANCHOR_INVALID",
              f"{row['identity']}: native batch did not freeze bottom_right registration")
        _need(item.get("source_sha256") == row["source_sha256"], "EXPAND_BATCH_SOURCE_SHA_MISMATCH",
              f"{row['identity']}: native batch source SHA does not match request")
        _need(item.get("reference_sha256") == row["native_reference_sha256"],
              "EXPAND_BATCH_REFERENCE_SHA_MISMATCH", f"{row['identity']}: native 1X reference SHA mismatch")
        planned.append({"identity": row["identity"], "source": str(source),
                        "output": str(Path(item["output"]).resolve()),
                        "fit_anchor": item["fit_anchor"], "source_sha256": item["source_sha256"],
                        "native_reference": str(row["native_reference"]),
                        "native_reference_sha256": row["native_reference_sha256"],
                        "native_def": str(row["native_def"]),
                        "native_def_sha256": row["native_def_sha256"],
                        "native_palette5_mask_sha256": row["native_palette5"]["mask_sha256"],
                        "native_palette5_crosscheck": row["native_palette5_crosscheck"]})
    check_native = report_dir / "expand-check.json"
    _invoke(renderer, ["expand-check", "--batch", str(batch_path), "--report", str(check_native)])
    preflight = _read_json(check_native, "expand-check report")
    _need(preflight.get("failed", 0) == 0 and preflight.get("blocked", 0) == 0
          and preflight.get("ready") == len(resolved),
          "EXPAND_CHECK_NOT_READY", "Native bottom_right batch is not fully ready")

    lock = {"schema": LOCK_SCHEMA, "operation_id": args.operation_id,
            "request_path": str(manifest), "request_sha256": request_sha,
            "renderer": str(renderer), "renderer_sha256": renderer_sha,
            "sprite_flags_info": str(sprite_flags), "sprite_flags_info_sha256": _sha256(sprite_flags),
            "flag_grey": str(glyph), "flag_grey_sha256": _sha256(glyph),
            "native_def_root": request["native_def_root"],
            "native_owner_palette_index": 5,
            "expand_batch": str(batch_path), "expand_batch_sha256": _sha256(batch_path),
            "expand_check": str(check_native), "expand_check_sha256": _sha256(check_native),
            "candidate_3x_root": str(candidate_root), "items": planned}
    _write_once(lock_path, lock)
    result = {"schema": REPORT_SCHEMA, "status": "READY", "operation_id": args.operation_id,
              "request": str(manifest), "request_sha256": request_sha,
              "renderer": str(renderer), "renderer_sha256": renderer_sha,
              "fit_anchor": "bottom_right", "target_scale": 3,
              "items": len(planned), "exact_vcmi_flag_keys": len(planned) - len(anchor_warnings),
              "missing_exact_flag_keys": anchor_warnings,
              "native_palette5_fallbacks": native_palette5_fallbacks,
              "expand_batch": str(batch_path), "expand_check": str(check_native),
              "source_masters_modified": 0, "formal_3x_modified": 0, "mod_modified": 0,
              "images_written": 0, "report_path": str(check_path)}
    _write_once(check_path, result)
    for row in resolved:
        row["native_palette5_mask"].close()
    return result


def _colorized_glyph(glyph: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    tint = Image.new("RGBA", glyph.size, (*color, 0))
    tint.putalpha(glyph.getchannel("A"))
    return tint


def _render_preview(candidate_path: Path, item: dict[str, Any], anchor_rows: dict[str, list[dict[str, Any]]],
                    glyph_path: Path, native_def_root: Path, preview_path: Path,
                    overlay_path: Path) -> dict[str, Any]:
    identity = str(item["identity"]).upper()
    try:
        candidate = Image.open(candidate_path).convert("RGBA")
    except (OSError, ValueError) as error:
        raise AlignmentFailure("CANDIDATE_PNG_READ_FAILED", f"{identity}: cannot decode 3X candidate: {error}") from error
    _need(candidate.size == (576, 576), "CANDIDATE_CANVAS_INVALID",
          f"{identity}: expected 576x576 RGBA after native export, got {candidate.size}")
    content_bbox = candidate.getchannel("A").getbbox()
    _need(content_bbox is not None, "CANDIDATE_ALPHA_EMPTY", f"{identity}: exported body has no visible alpha")
    anchor_rows_used: list[dict[str, Any]] = []
    overlay_source = ""
    overlay = Image.new("RGBA", (576, 576), (255, 255, 255, 0))
    preview = candidate.copy()

    if identity in anchor_rows:
        glyph = Image.open(glyph_path).convert("RGBA")
        anchors = sorted(anchor_rows[identity], key=lambda row: (row["x"], row["y"]))
        colors = ((242, 56, 64), (70, 164, 248))
        for side, anchor in enumerate(anchors):
            oriented = ImageOps.mirror(glyph) if anchor["mirrored"] else glyph.copy()
            origin = (anchor["x"] * TARGET_SCALE, anchor["y"] * TARGET_SCALE)
            _need(origin[0] + oriented.width <= candidate.width and origin[1] + oriented.height <= candidate.height,
                  "FLAG_GLYPH_OUT_OF_CANVAS", f"{identity}: fixed VCMI flag glyph leaves 576x576 canvas at {origin}")
            overlay.alpha_composite(_colorized_glyph(oriented, (255, 255, 255)), origin)
            preview.alpha_composite(_colorized_glyph(oriented, colors[side]), origin)
            anchor_rows_used.append({"side": "left" if side == 0 else "right",
                                     "native_top_left": [anchor["x"], anchor["y"]],
                                     "output_origin_3x": list(origin),
                                     "mirrored": anchor["mirrored"],
                                     "glyph_pixels_3x": list(oriented.size)})
            oriented.close()
        glyph.close()
        overlay_source = "VCMI spriteFlagsInfo.txt + flag_grey.png"
    else:
        native_def = (native_def_root / item["native_def_relpath"]).resolve()
        _need(_sha256(native_def) == item["native_def_sha256"], "NATIVE_DEF_SHA_DRIFT",
              f"{identity}: original 1999 DEF changed before preview")
        native_mask, native_mask_info = _native_palette5(native_def)
        _need(native_mask_info["mask_sha256"] == item["native_palette5"]["mask_sha256"],
              "NATIVE_DEF_FLAG_MASK_DRIFT", f"{identity}: original palette-5 flag mask changed")
        mask_3x = native_mask.resize((576, 576), Image.Resampling.NEAREST)
        overlay.putalpha(mask_3x)
        tint = Image.new("RGBA", (576, 576), (240, 182, 60, 0))
        tint.putalpha(mask_3x)
        preview.alpha_composite(tint)
        anchor_rows_used.append({"status": "NATIVE_1999_DEF_PALETTE5",
                                 "native_def": str(native_def),
                                 "native_def_sha256": item["native_def_sha256"],
                                 "palette_index": 5,
                                 "palette5_pixel_count": native_mask_info["palette5_pixel_count"],
                                 "components_1x": item["native_palette5_crosscheck"]["components"],
                                 "positions_source": "original Complete DEF 1X; no spriteFlagsInfo key"})
        overlay_source = "1999 original DEF palette index 5"
        mask_3x.close()
        native_mask.close()

    draw = ImageDraw.Draw(preview, "RGBA")
    for coordinate in range(GRID_PIXELS_3X, 576, GRID_PIXELS_3X):
        draw.line((coordinate, 0, coordinate, 575), fill=(20, 20, 18, 135), width=1)
        draw.line((0, coordinate, 575, coordinate), fill=(20, 20, 18, 135), width=1)
    draw.rectangle((0, 0, 575, 575), outline=(255, 255, 255, 65), width=1)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    _need(not overlay_path.exists(), "FLAG_OVERLAY_CANDIDATE_EXISTS",
          f"Refusing to overwrite generated overlay: {overlay_path}")
    overlay.save(overlay_path, format="PNG", optimize=True)
    preview.save(preview_path, format="PNG", optimize=True)
    overlay.close()
    preview.close()
    candidate.close()
    return {"identity": identity, "candidate_3x": str(candidate_path),
            "candidate_3x_sha256": _sha256(candidate_path), "candidate_alpha_bbox_3x": list(content_bbox),
            "flag_overlay_source": overlay_source, "fixed_flags": anchor_rows_used,
            "flag_overlay_candidate": str(overlay_path),
            "flag_overlay_candidate_sha256": _sha256(overlay_path),
            "preview_png": str(preview_path), "preview_sha256": _sha256(preview_path),
            "grid_tile_pixels_3x": GRID_PIXELS_3X, "canvas_centerline_x_3x": 288,
            "fit_anchor": "bottom_right", "entrance_axis": "NOT_MEASURED",
            "flag_pole_art_quality": "REVIEW_REQUIRED"}


def _write_review_sheet(items: list[dict[str, Any]], output_root: Path, title: str) -> list[str]:
    rows_per_page = 3
    columns = 2
    panel_w, panel_h = 590, 646
    page_paths = []
    for page_index in range((len(items) + rows_per_page * columns - 1) // (rows_per_page * columns)):
        page_items = items[page_index * rows_per_page * columns:(page_index + 1) * rows_per_page * columns]
        sheet = Image.new("RGBA", (panel_w * columns, panel_h * rows_per_page), (28, 37, 39, 255))
        for slot, item in enumerate(page_items):
            row, column = divmod(slot, columns)
            image = Image.open(item["preview_png"]).convert("RGBA")
            panel = Image.new("RGBA", (panel_w, panel_h), (28, 37, 39, 255))
            draw = ImageDraw.Draw(panel)
            state = "VCMI fixed flags" if item["fixed_flags"] and item["fixed_flags"][0].get("status") is None else item["flag_overlay_source"]
            draw.text((8, 5), f"{item['identity']} | {state}", fill=(240, 240, 235, 255))
            draw.text((8, 22), f"bottom_right | alpha {item['candidate_alpha_bbox_3x']}",
                      fill=(185, 215, 220, 255))
            panel.alpha_composite(image, (7, 42))
            sheet.alpha_composite(panel, (column * panel_w, row * panel_h))
            image.close()
            panel.close()
        output_root.mkdir(parents=True, exist_ok=True)
        path = output_root / f"flag-alignment-review-{page_index + 1:02}.png"
        _need(not path.exists(), "REVIEW_SHEET_EXISTS", f"Refusing to overwrite existing review sheet: {path}")
        sheet.convert("RGB").save(path, optimize=True)
        page_paths.append(path)
        sheet.close()
    return [str(path) for path in page_paths]


def _write_html_index(items: list[dict[str, Any]], output: Path, page_paths: list[str]) -> None:
    cards = []
    for item in items:
        relative = Path(item["preview_png"]).resolve().relative_to(output.resolve()).as_posix()
        flags = html.escape(item["flag_overlay_source"])
        cards.append(f'<article><h2>{html.escape(item["identity"])}</h2>'
                     f'<p>底右注册仅为候选，旗杆和入口均未通过对齐审核</p><p>旗标层：{flags}</p>'
                     f'<a href="{html.escape(relative, quote=True)}"><img src="{html.escape(relative, quote=True)}" '
                     f'alt="{html.escape(item["identity"])} 旗标固定位置预览"></a></article>')
    page_links = " ".join(f'<a href="{html.escape(Path(p).name, quote=True)}">{index + 1}</a>'
                           for index, p in enumerate(page_paths))
    body = ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>城镇旗标对齐复核</title>"
            "<style>body{font:15px system-ui;background:#182123;color:#eee;margin:24px}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}"
            "article{background:#252f31;padding:12px;border-radius:6px}img{width:100%;height:auto}"
            "a{color:#9ad7ff}p{color:#c4d3d4}</style>"
            "<h1>城镇旗标对齐复核</h1><p>本页是底右注册候选，不能证明旗杆和入口对齐；画布中心不是道路或旗杆中心。"
            "红/蓝区分有精确 VCMI 键的左右旗，金色表示从 1999 DEF palette index 5 提取的旗面。"
            "复核图不是发布审批，入口中轴需要人工核对。</p>"
            f"<nav>Contact sheets: {page_links}</nav><main class=\"grid\">{''.join(cards)}</main></html>")
    index = output / "index.html"
    _need(not index.exists(), "REVIEW_INDEX_EXISTS", f"Refusing to overwrite {index}")
    index.write_text(body, encoding="utf-8")


def run_task(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _absolute(args.manifest, "manifest")
    request, request_sha = _load_request(manifest, args.manifest_sha256, args.operation_id)
    renderer, renderer_sha = _renderer(args)
    output = _absolute(args.outdir, "outdir")
    lock_path = output / "request-lock.json"
    check_path = output / "check-report.json"
    _need(lock_path.is_file() and check_path.is_file(), "CHECK_REQUIRED",
          "Run the GUI/task-center check stage for this operation before run")
    lock = _read_json(lock_path, "request lock")
    check = _read_json(check_path, "check report")
    _need(lock.get("request_sha256") == request_sha and lock.get("renderer_sha256") == renderer_sha
          and check.get("status") == "READY", "CHECK_BINDING_MISMATCH",
          "Run stage does not match the checked request or renderer")
    current_inputs = [_resolve_item(manifest, request, item) for item in request["items"]]
    _, _, _, current_anchor_warnings = _validate_anchor_overlay_files(request)
    locked_by_id = {row["identity"]: row for row in lock.get("items", [])}
    for row in current_inputs:
        locked = locked_by_id.get(row["identity"])
        _need(locked is not None and locked.get("source_sha256") == row["source_sha256"]
              and locked.get("native_reference_sha256") == row["native_reference_sha256"]
              and locked.get("native_def_sha256") == row["native_def_sha256"]
              and locked.get("native_palette5_mask_sha256") == row["native_palette5"]["mask_sha256"],
              "INPUT_SHA_DRIFT_AFTER_CHECK", f"{row['identity']}: source, official frame, or 1999 DEF changed after check")
        row["native_palette5_mask"].close()
    run_report_path = output / "run-report.json"
    if run_report_path.exists():
        previous = _read_json(run_report_path, "run report")
        _need(previous.get("request_sha256") == request_sha and previous.get("renderer_sha256") == renderer_sha,
              "RUN_REPORT_CONFLICT", "Existing run report belongs to another request or renderer")
        return previous

    batch_path = Path(lock["expand_batch"])
    batch = _read_json(batch_path, "expand-batch")
    expand_run_path = output / "reports" / "expand-run.json"
    expand_verify_path = output / "reports" / "expand-verify.json"
    _invoke(renderer, ["expand", "--batch", str(batch_path), "--report", str(expand_run_path)])
    expand_run = _read_json(expand_run_path, "expand-run report")
    _need(expand_run.get("status") == "COMPLETED" and expand_run.get("failed", 0) == 0,
          "EXPAND_RUN_NOT_COMPLETED", "Native H3TierExport expansion did not complete all candidates")
    _invoke(renderer, ["expand-verify", "--batch", str(batch_path), "--report", str(expand_verify_path)])
    expand_verify = _read_json(expand_verify_path, "expand-verify report")
    results = expand_verify.get("results", [])
    _need(expand_verify.get("status") == "COMPLETED" and expand_verify.get("failed", 0) == 0
          and len(results) == len(batch.get("items", []))
          and all(result.get("status") == "VERIFIED" for result in results),
          "EXPAND_VERIFY_FAILED", "Native expand-verify did not verify every bottom_right candidate")

    _, glyph_path, anchor_table, anchor_warnings = _validate_anchor_overlay_files(request)
    native_def_root = _absolute(request.get("native_def_root"), "native_def_root")
    source_map = {os.path.normcase(str(Path(row["source"]).resolve())): row for row in lock["items"]}
    preview_dir = output / "previews"
    preview_dir.mkdir(exist_ok=False)
    overlay_dir = output / "flag-overlays"
    overlay_dir.mkdir(exist_ok=False)
    review_items = []
    for batch_item in batch["items"]:
        source_key = os.path.normcase(str(Path(batch_item["source"]).resolve()))
        locked = source_map.get(source_key)
        _need(locked is not None, "RUN_SOURCE_UNEXPECTED", f"Unexpected source in expansion batch: {batch_item['source']}")
        _need(batch_item.get("fit_anchor") == "bottom_right", "RUN_ANCHOR_INVALID",
              f"{locked['identity']}: candidate is not bottom_right registered")
        candidate = Path(batch_item["output"]).resolve()
        preview = preview_dir / f"{locked['identity']}-bottom-right-fixed-flags.png"
        overlay_output = overlay_dir / f"{locked['identity']}-OVERLAY.png"
        entry = next(item for item in request["items"] if str(item["identity"]).upper() == locked["identity"])
        overlay_item = dict(entry)
        rendered = _render_preview(candidate, overlay_item, anchor_table, glyph_path,
                                   native_def_root, preview, overlay_output)
        rendered["native_reference"] = locked["native_reference"]
        rendered["native_reference_sha256"] = locked["native_reference_sha256"]
        rendered["master"] = locked["source"]
        rendered["master_sha256"] = locked["source_sha256"]
        rendered["native_def"] = locked["native_def"]
        rendered["native_def_sha256"] = locked["native_def_sha256"]
        rendered["native_palette5_mask_sha256"] = locked["native_palette5_mask_sha256"]
        rendered["native_palette5_crosscheck"] = locked["native_palette5_crosscheck"]
        rendered["expand_receipt"] = next((r.get("receipt") for r in expand_run.get("results", [])
                                             if Path(r.get("output", "")).resolve() == candidate), None)
        rendered["human_approved"] = False
        rendered["game_tested"] = False
        review_items.append(rendered)
    review_items.sort(key=lambda row: row["identity"])
    pages = _write_review_sheet(review_items, output, "城镇旗标对齐复核")
    _write_html_index(review_items, output, pages)
    # Re-read each master after resampling: preview work must never modify its parent.
    for item in review_items:
        master = Path(item["master"])
        _need(_sha256(master) == item["master_sha256"], "MASTER_CHANGED_DURING_RUN",
              f"{item['identity']}: source master changed during candidate generation")
    native_palette5_fallbacks = [item for item in review_items
                                  if item["flag_overlay_source"] == "1999 original DEF palette index 5"]
    result = {"schema": REPORT_SCHEMA, "status": "EXPORTED_ALIGNMENT_UNVERIFIED",
              "operation_id": args.operation_id, "request": str(manifest), "request_sha256": request_sha,
              "renderer": str(renderer), "renderer_sha256": renderer_sha,
              "fit_anchor": "bottom_right", "target_scale": 3,
              "items": len(review_items), "expand_run": str(expand_run_path),
              "expand_run_sha256": _sha256(expand_run_path), "expand_verify": str(expand_verify_path),
              "expand_verify_sha256": _sha256(expand_verify_path), "preview_pages": pages,
              "preview_page_sha256": {page: _sha256(Path(page)) for page in pages},
              "html_review": str(output / "index.html"),
              "html_review_sha256": _sha256(output / "index.html"), "review_items": review_items,
              "missing_exact_flag_keys": anchor_warnings,
              "native_palette5_fallbacks": native_palette5_fallbacks,
              "flag_overlay_candidates": len(review_items),
              "scope_boundary": "Fixed flag coordinates are overlaid from VCMI sources; this report does not measure gate centerline, approve artwork, or imply game acceptance.",
              "source_masters_modified": 0, "formal_3x_modified": 0, "mod_modified": 0,
              "human_approved": False, "game_tested": False}
    _write_once(run_report_path, result)
    return result


def verify_task(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _absolute(args.manifest, "manifest")
    request, request_sha = _load_request(manifest, args.manifest_sha256, args.operation_id)
    renderer, renderer_sha = _renderer(args)
    output = _absolute(args.outdir, "outdir")
    lock = _read_json(output / "request-lock.json", "request lock")
    run_report = _read_json(output / "run-report.json", "run report")
    _need(lock.get("request_sha256") == request_sha and lock.get("renderer_sha256") == renderer_sha
          and run_report.get("request_sha256") == request_sha
          and run_report.get("renderer_sha256") == renderer_sha,
          "VERIFY_INPUT_BINDING_MISMATCH", "Request or renderer differs from the checked/run operation")
    _, glyph, anchors, anchor_warnings = _validate_anchor_overlay_files(request)
    batch_path = Path(lock["expand_batch"])
    _need(batch_path.is_file() and _sha256(batch_path) == lock.get("expand_batch_sha256"),
          "EXPAND_BATCH_SHA_DRIFT", "Frozen native expansion batch changed")
    review_items = run_report.get("review_items", [])
    _need(isinstance(review_items, list) and len(review_items) == len(request["items"]),
          "REVIEW_ITEM_COUNT_MISMATCH", "Run report does not contain every requested town")
    _need({str(item.get("identity", "")).upper() for item in review_items}
          == {str(item.get("identity", "")).upper() for item in request["items"]},
          "REVIEW_IDENTITY_SET_MISMATCH", "Review report identities differ from the frozen request")
    resolved_inputs = [_resolve_item(manifest, request, item) for item in request["items"]]
    input_by_id = {row["identity"]: row for row in resolved_inputs}
    expand_run = Path(run_report["expand_run"])
    expand_verify = Path(run_report["expand_verify"])
    _need(expand_run.is_file() and _sha256(expand_run) == run_report.get("expand_run_sha256"),
          "EXPAND_RUN_SHA_DRIFT", "Native expand run report changed")
    _need(expand_verify.is_file() and _sha256(expand_verify) == run_report.get("expand_verify_sha256"),
          "EXPAND_VERIFY_SHA_DRIFT", "Native expand verify report changed")
    verify_native = output / "reports" / "expand-verify-replay.json"
    if not verify_native.exists():
        _invoke(renderer, ["expand-verify", "--batch", str(lock["expand_batch"]), "--report", str(verify_native)])
    native_report = _read_json(verify_native, "replayed expand-verify report")
    native_results = native_report.get("results", [])
    _need(native_report.get("status") == "COMPLETED" and native_report.get("failed", 0) == 0
          and len(native_results) == len(lock["items"])
          and all(row.get("status") == "VERIFIED" for row in native_results),
          "VERIFY_NATIVE_REPLAY_FAILED", "Replayed native expand-verify did not verify all candidates")
    for item in review_items:
        candidate = Path(item["candidate_3x"])
        preview = Path(item["preview_png"])
        overlay = Path(item["flag_overlay_candidate"])
        _need(candidate.is_file() and _sha256(candidate) == item["candidate_3x_sha256"],
              "CANDIDATE_SHA_DRIFT", f"{item['identity']}: exported 3X candidate changed")
        _need(preview.is_file() and _sha256(preview) == item["preview_sha256"],
              "PREVIEW_SHA_DRIFT", f"{item['identity']}: fixed flag review preview changed")
        _need(overlay.is_relative_to(output) and overlay.is_file()
              and _sha256(overlay) == item["flag_overlay_candidate_sha256"],
              "FLAG_OVERLAY_SHA_DRIFT", f"{item['identity']}: generated owner-flag overlay changed")
        overlay_size, overlay_mode, overlay_bbox = _png_info(overlay, f"{item['identity']} generated owner-flag overlay")
        _need(overlay_size == (576, 576) and overlay_mode == "RGBA" and overlay_bbox is not None,
              "FLAG_OVERLAY_GEOMETRY_INVALID", f"{item['identity']}: generated overlay must be visible 576x576 RGBA")
        master = Path(item["master"])
        native = Path(item["native_reference"])
        _need(_sha256(master) == item["master_sha256"], "MASTER_SHA_DRIFT",
              f"{item['identity']}: source master changed")
        _need(_sha256(native) == item["native_reference_sha256"], "REFERENCE_SHA_DRIFT",
              f"{item['identity']}: official 1X reference changed")
        resolved = input_by_id[item["identity"]]
        _need(_sha256(resolved["native_def"]) == item["native_def_sha256"]
              and resolved["native_palette5"]["mask_sha256"] == item["native_palette5_mask_sha256"],
              "NATIVE_DEF_FLAG_MASK_DRIFT", f"{item['identity']}: 1999 DEF owner-flag layer changed")
    for resolved in resolved_inputs:
        resolved["native_palette5_mask"].close()
    for page in run_report.get("preview_pages", []):
        page_path = _absolute(page, "preview page")
        _need(page_path.is_relative_to(output) and page_path.is_file()
              and _sha256(page_path) == run_report.get("preview_page_sha256", {}).get(str(page_path)),
              "REVIEW_PAGE_SHA_DRIFT", f"Review contact sheet changed: {page_path}")
    html_path = _absolute(run_report.get("html_review"), "html_review")
    _need(html_path.is_relative_to(output) and html_path.is_file()
          and _sha256(html_path) == run_report.get("html_review_sha256"),
          "REVIEW_HTML_SHA_DRIFT", f"HTML review page changed: {html_path}")
    verify = {"schema": VERIFY_SCHEMA, "status": "PIXELS_VERIFIED_ALIGNMENT_UNVERIFIED",
              "operation_id": args.operation_id, "request_sha256": request_sha,
              "renderer_sha256": renderer_sha, "items": len(run_report.get("review_items", [])),
              "fit_anchor": "bottom_right", "native_expand_verify_replay": str(verify_native),
              "native_expand_verify_replay_sha256": _sha256(verify_native),
              "candidate_and_preview_sha_readback": "PASS",
              "missing_exact_flag_keys": anchor_warnings,
              "native_1999_def_palette5_crosscheck": "PASS",
              "flag_overlay_candidates": len(review_items),
              "human_approved": False, "game_tested": False,
              "mod_written": False}
    verify_path = output / "verify-report.json"
    if verify_path.exists():
        existing = _read_json(verify_path, "verify report")
        _need(existing.get("request_sha256") == request_sha and existing.get("renderer_sha256") == renderer_sha,
              "VERIFY_REPORT_CONFLICT", "Existing verify report belongs to a different request or renderer")
        return existing
    _write_once(verify_path, verify)
    return verify


def _add_renderer_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--renderer", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--renderer-sha256", help=argparse.SUPPRESS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="Freeze an immutable request for the city flag alignment workflow")
    freeze.add_argument("--source-root", type=Path, required=True,
                        help="Common root containing <town ID>/0_0.png master directories")
    freeze.add_argument("--native-frames-root", type=Path, required=True,
                        help="Official 1X frames root containing <town ID>/0_0.png")
    freeze.add_argument("--native-def-root", type=Path, required=True,
                        help="1999 Complete DEF payload root containing <town ID>.DEF")
    freeze.add_argument("--sprite-flags-info", type=Path, required=True)
    freeze.add_argument("--flag-grey", type=Path, required=True)
    freeze.add_argument("--operation-id", required=True)
    freeze.add_argument("--out", type=Path, required=True)
    freeze.add_argument("--identity", action="append", help="Town DEF identity; default is the current 18-slot set")
    freeze.add_argument("--master", action="append", help="Override one source: ID=ABSOLUTE_PATH")
    _add_renderer_args(freeze)
    for name, description in (("check", "Preflight pinned source/flag data and freeze a bottom_right native export batch"),
                              ("run", "Export bottom_right 3X candidates and generate fixed flag review sheets"),
                              ("verify", "Replay native expand-verify and check all source/candidate/preview hashes")):
        stage = sub.add_parser(name, help=description)
        stage.add_argument("--manifest", type=Path, required=True)
        stage.add_argument("--manifest-sha256", required=True)
        stage.add_argument("--outdir", type=Path, required=True)
        stage.add_argument("--operation-id", required=True)
        _add_renderer_args(stage)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("--renderer", "--renderer-sha256"):
        parser.error("the following arguments are required: command")
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze_request(args)
        else:
            _need(args.renderer is not None and args.renderer_sha256,
                  "RENDERER_IDENTITY_REQUIRED", "The invoking H3TierExport binary path and SHA are required")
            if args.command == "check":
                result = check_task(args)
            elif args.command == "run":
                result = run_task(args)
            else:
                result = verify_task(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AlignmentFailure as error:
        print(json.dumps({"schema": REPORT_SCHEMA, "status": "FAILED",
                          "errors": [{"code": error.code, "message": str(error)}]},
                         ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"schema": REPORT_SCHEMA, "status": "FAILED",
                          "errors": [{"code": "UNEXPECTED_INPUT_ERROR", "message": str(error)}]},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
