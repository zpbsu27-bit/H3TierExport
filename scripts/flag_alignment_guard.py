#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict H3 map-town flag alignment gate.

The guard never moves artwork. It verifies a 3X candidate against its H3TierExport
receipt and an identity-specific landmark spec, then writes an optional proof image.
Unknown identities or incomplete measurements fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

SPEC_SCHEMA = "h3.flag-alignment-spec.v1"
BATCH_SCHEMA = "h3.flag-alignment-batch.v1"
RECEIPT_BATCH_SCHEMA = "h3.flag-alignment-receipt-batch.v1"
REPORT_SCHEMA = "h3.flag-alignment-report.v1"
RECEIPT_SCHEMA = "h3.master-expand.receipt.v1"
VERIFY_SCHEMA = "h3.master-expand.run.v1"
FLAG_ANCHOR_AUDIT_SCHEMA = "h3.town-entrance-flag-pole-anchor-audit.v1"
REQUIRED_SCALE = 3
FLAG_FACE_SIZE_1X = (14, 5)
FLAG_FACE_PIXELS_1X = 42
GROUND_SUPPORT_SIZE_3X = (17, 17)
GROUND_SUPPORT_RATIO = 1.0
DEFAULT_ALPHA_THRESHOLD = 1


class GuardFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise GuardFailure(code, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GuardFailure("JSON_READ_FAILED", f"Cannot read JSON {path}: {error}") from error
    _need(isinstance(value, dict), "JSON_ROOT_NOT_OBJECT", f"Expected a JSON object in {path}")
    return value


def _resolve(base: Path, value: Any, field: str) -> Path:
    _need(isinstance(value, str) and value.strip(), "PATH_MISSING", f"{field} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _pair(value: Any, field: str) -> tuple[int, int]:
    _need(
        isinstance(value, list) and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value),
        "COORDINATE_INVALID",
        f"{field} must be a two-integer [x, y] pair",
    )
    return value[0], value[1]


def _rect(value: Any, field: str) -> tuple[int, int, int, int]:
    _need(
        isinstance(value, list) and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value),
        "RECTANGLE_INVALID",
        f"{field} must be integer [left, top, right, bottom] (right/bottom exclusive)",
    )
    return value[0], value[1], value[2], value[3]


def _rgba(path: Path, field: str) -> Image.Image:
    _need(path.is_file(), "INPUT_NOT_FOUND", f"{field} not found: {path}")
    try:
        with Image.open(path) as image:
            return image.convert("RGBA")
    except (OSError, ValueError) as error:
        raise GuardFailure("PNG_READ_FAILED", f"Cannot decode {field} {path}: {error}") from error


def _mask_alpha(image: Image.Image) -> Image.Image:
    """Read a real alpha channel, or an explicit grayscale mask; reject opaque RGB art."""
    if image.mode == "L":
        return image.copy()
    if image.mode == "1":
        return image.convert("L")
    if "A" in image.getbands():
        return image.getchannel("A")
    raise GuardFailure("FLAG_MASK_NEEDS_ALPHA", "Flag face must be an RGBA image or an explicit grayscale mask")


def _load_flag_face(spec: dict[str, Any], spec_path: Path) -> tuple[Image.Image, tuple[int, int]]:
    face = spec.get("flag_face")
    _need(isinstance(face, dict), "FLAG_FACE_SPEC_MISSING", "Spec must pin a separate flag_face mask image")
    path = _resolve(spec_path.parent, face.get("path"), "flag_face.path")
    expected_sha = face.get("sha256")
    _need(isinstance(expected_sha, str) and len(expected_sha) == 64,
          "FLAG_FACE_SHA_MISSING", "flag_face.sha256 must pin the reviewed source image")
    actual_sha = _sha256(path) if path.is_file() else ""
    _need(actual_sha == expected_sha.lower(), "FLAG_FACE_SHA_DRIFT",
          f"Flag face SHA mismatch: expected {expected_sha}, actual {actual_sha or 'MISSING'}")
    source = face.get("source_image")
    _need(isinstance(source, dict), "FLAG_FACE_SOURCE_MISSING",
          "flag_face.source_image must pin the reviewed official source image")
    source_path = _resolve(spec_path.parent, source.get("path"), "flag_face.source_image.path")
    source_sha = source.get("sha256")
    _need(isinstance(source_sha, str) and len(source_sha) == 64
          and source_path.is_file() and _sha256(source_path) == source_sha.lower(),
          "FLAG_FACE_SOURCE_SHA_DRIFT",
          "The source image used to extract the 14x5 face mask must match its pinned SHA")
    try:
        with Image.open(path) as opened:
            if opened.mode == "P" and "transparency" in opened.info:
                mask = opened.convert("RGBA").getchannel("A")
            else:
                mask = _mask_alpha(opened)
    except (OSError, ValueError) as error:
        raise GuardFailure("FLAG_MASK_READ_FAILED", f"Cannot decode flag face mask {path}: {error}") from error
    bbox = mask.getbbox()
    _need(bbox is not None, "FLAG_FACE_EMPTY", "Flag face mask has no non-transparent pixels")
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    alpha_pixels = sum(value > 0 for value in mask.tobytes())
    _need((width, height) == FLAG_FACE_SIZE_1X and alpha_pixels == FLAG_FACE_PIXELS_1X,
          "FLAG_FACE_SHAPE_MISMATCH",
          f"Expected one 1X arrow face with bbox 14x5 and 42 alpha pixels; got {width}x{height} and {alpha_pixels}")
    cropped = mask.crop(bbox)
    hang = _pair(face.get("hang_point_local_1x"), "flag_face.hang_point_local_1x")
    _need(0 <= hang[0] < width and 0 <= hang[1] < height,
          "FLAG_HANGPOINT_OUTSIDE_FACE", f"Hang point {hang} is outside the 14x5 flag face")
    _need(cropped.getpixel(hang) > 0, "FLAG_HANGPOINT_NOT_ON_FACE",
          f"Hang point {hang} must land on an opaque pixel of the flag face")
    mask.close()
    return cropped, hang


def _bresenham(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    x0, y0 = start
    x1, y1 = end
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    points: list[tuple[int, int]] = []
    while True:
        points.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return points
        twice = 2 * error
        if twice > -dy:
            error -= dy
            x0 += sx
        if twice < dx:
            error += dx
            y0 += sy


def _line_points(points_3x: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in zip(points_3x, points_3x[1:]):
        segment = _bresenham(start, end)
        out.extend(segment if not out else segment[1:])
    return out


def _inside_point(point: tuple[int, int], size: tuple[int, int], field: str) -> None:
    _need(0 <= point[0] < size[0] and 0 <= point[1] < size[1],
          "ANCHOR_OUT_OF_BOUNDS", f"{field} {point} lies outside canvas {size}")


def _check_receipt(
    receipt_path: Path,
    verify_report_path: Path,
    building_path: Path,
    spec: dict[str, Any],
    native_path: Path,
    native_size: tuple[int, int],
    candidate_size: tuple[int, int],
) -> dict[str, Any]:
    receipt = _read_json(receipt_path)
    _need(receipt.get("schema") == RECEIPT_SCHEMA, "RECEIPT_SCHEMA_UNSUPPORTED",
          f"Expected {RECEIPT_SCHEMA}; got {receipt.get('schema')!r}")
    contract = receipt.get("contract")
    entry = contract.get("entry") if isinstance(contract, dict) else None
    silhouette = receipt.get("silhouette")
    _need(isinstance(entry, dict) and isinstance(silhouette, dict),
          "RECEIPT_ALIGNMENT_DATA_MISSING", "Receipt must include contract.entry and silhouette")
    _need(entry.get("fit_anchor") == "bottom_right" and silhouette.get("fit_anchor") == "bottom_right",
          "RECEIPT_FIT_ANCHOR_NOT_BOTTOM_RIGHT",
          f"Map asset requires bottom_right; receipt has entry={entry.get('fit_anchor')!r}, silhouette={silhouette.get('fit_anchor')!r}")
    _need(receipt.get("target_scale") == REQUIRED_SCALE and entry.get("target_scale") == REQUIRED_SCALE,
          "RECEIPT_TARGET_SCALE_MISMATCH", f"Town flag check requires target_scale={REQUIRED_SCALE}")
    verification = _read_json(verify_report_path)
    _need(verification.get("schema") == VERIFY_SCHEMA
          and verification.get("status") == "COMPLETED"
          and verification.get("failed") == 0,
          "EXPAND_VERIFY_REPORT_FAILED",
          "A successful H3TierExport expand-verify report is required")
    verify_results = verification.get("results")
    _need(isinstance(verify_results, list), "EXPAND_VERIFY_RESULTS_MISSING",
          "expand-verify report has no results array")
    verified = []
    for row in verify_results:
        if not isinstance(row, dict) or not isinstance(row.get("receipt"), str):
            continue
        row_receipt = _resolve(verify_report_path.parent, row["receipt"], "expand-verify result receipt")
        if row_receipt == receipt_path:
            verified.append(row)
    _need(len(verified) == 1 and verified[0].get("status") == "VERIFIED",
          "EXPAND_VERIFY_RECEIPT_NOT_VERIFIED",
          "expand-verify must contain exactly one VERIFIED result for this receipt")
    expected_native_sha = spec["native_reference"]["sha256"].lower()
    entry_ref_sha = entry.get("reference_sha256")
    silhouette_ref_sha = silhouette.get("reference_sha256")
    _need(isinstance(entry_ref_sha, str) and isinstance(silhouette_ref_sha, str)
          and entry_ref_sha.lower() == expected_native_sha
          and silhouette_ref_sha.lower() == expected_native_sha,
          "RECEIPT_REFERENCE_SHA_MISMATCH", "Receipt is not bound to the pinned official 1X reference SHA")
    entry_ref = _resolve(receipt_path.parent, entry.get("reference"), "receipt.contract.entry.reference")
    silhouette_ref = _resolve(receipt_path.parent, silhouette.get("reference"), "receipt.silhouette.reference")
    _need(entry_ref == native_path and silhouette_ref == native_path,
          "RECEIPT_REFERENCE_PATH_MISMATCH", "Receipt reference path differs from the pinned official 1X frame")
    _need(entry.get("reference_pixels") == list(native_size)
          and silhouette.get("reference_pixels") == list(native_size),
          "RECEIPT_REFERENCE_DIMENSIONS_MISMATCH", "Receipt's reference canvas dimensions do not match the official 1X frame")
    receipt_output = _resolve(receipt_path.parent, receipt.get("output"), "receipt.output")
    entry_output = _resolve(receipt_path.parent, entry.get("output"), "receipt.contract.entry.output")
    _need(receipt_output == building_path and entry_output == building_path,
          "RECEIPT_OUTPUT_PATH_MISMATCH", "Receipt does not name the exact candidate 3X image being checked")
    candidate_sha = _sha256(building_path)
    receipt_sha = receipt.get("png_sha256")
    _need(isinstance(receipt_sha, str) and receipt_sha.lower() == candidate_sha,
          "RECEIPT_OUTPUT_SHA_MISMATCH", "Candidate 3X PNG SHA does not match its H3TierExport receipt")
    expected_size = (native_size[0] * REQUIRED_SCALE, native_size[1] * REQUIRED_SCALE)
    _need(candidate_size == expected_size and receipt.get("official_output_pixels") == list(expected_size),
          "RECEIPT_OUTPUT_DIMENSIONS_MISMATCH",
          f"Expected official 3X canvas {expected_size}, candidate is {candidate_size}")
    _need(receipt.get("output_pixels") == list(expected_size),
          "RECEIPT_OUTPUT_DIMENSIONS_MISMATCH", "Receipt output_pixels does not match the exact official 3X canvas")
    _need(entry.get("base_pixels") == list(native_size),
          "RECEIPT_BASE_DIMENSIONS_MISMATCH", "Receipt base_pixels must match the official 1X slot canvas")
    source = entry.get("source")
    _need(isinstance(source, str) and Path(source).parent.name.upper() == spec["identity"].upper(),
          "RECEIPT_IDENTITY_MISMATCH", "Receipt source directory does not match the spec identity")
    return receipt


def _validate_native_reference(spec: dict[str, Any], spec_path: Path) -> tuple[Path, tuple[int, int], Image.Image]:
    ref = spec.get("native_reference")
    _need(isinstance(ref, dict), "NATIVE_REFERENCE_SPEC_MISSING", "Spec must pin native_reference.path and sha256")
    path = _resolve(spec_path.parent, ref.get("path"), "native_reference.path")
    identity = spec.get("identity")
    _need(isinstance(identity, str) and path.parent.name.upper() == identity.upper(),
          "NATIVE_REFERENCE_IDENTITY_MISMATCH",
          "Pinned official 1X frame directory must match the registered identity")
    expected_sha = ref.get("sha256")
    _need(isinstance(expected_sha, str) and len(expected_sha) == 64,
          "NATIVE_REFERENCE_SHA_MISSING", "native_reference.sha256 must pin the official 1999 1X frame")
    actual_sha = _sha256(path) if path.is_file() else ""
    _need(actual_sha == expected_sha.lower(), "NATIVE_REFERENCE_SHA_DRIFT",
          f"Official 1X reference SHA mismatch: expected {expected_sha}, actual {actual_sha or 'MISSING'}")
    image = _rgba(path, "official 1X reference")
    size = image.size
    _need(ref.get("canvas_px") == list(size), "NATIVE_REFERENCE_DIMENSIONS_MISMATCH",
          f"Spec canvas_px {ref.get('canvas_px')!r} does not match official frame {size}")
    return path, size, image


def _load_flag_anchor_authority(
    spec: dict[str, Any],
    spec_path: Path,
) -> tuple[str, dict[str, tuple[int, int]], dict[str, Any]]:
    """Bind per-town 3X feet to the measured spriteFlagsInfo authority; no copied defaults."""
    authority = spec.get("flag_anchor_authority")
    _need(isinstance(authority, dict), "FLAG_ANCHOR_AUTHORITY_MISSING",
          "Spec must pin the official per-identity flag-anchor audit")
    audit_path = _resolve(spec_path.parent, authority.get("path"), "flag_anchor_authority.path")
    expected_audit_sha = authority.get("sha256")
    _need(isinstance(expected_audit_sha, str) and len(expected_audit_sha) == 64,
          "FLAG_ANCHOR_AUTHORITY_SHA_MISSING", "flag_anchor_authority.sha256 is required")
    _need(_sha256(audit_path) == expected_audit_sha.lower(),
          "FLAG_ANCHOR_AUTHORITY_SHA_DRIFT", "Pinned flag-anchor audit SHA does not match")
    audit = _read_json(audit_path)
    _need(audit.get("schema") == FLAG_ANCHOR_AUDIT_SCHEMA,
          "FLAG_ANCHOR_AUTHORITY_SCHEMA_UNSUPPORTED",
          f"Expected {FLAG_ANCHOR_AUDIT_SCHEMA}; got {audit.get('schema')!r}")
    identity = spec["identity"].upper()
    rows = [row for row in audit.get("assets", [])
            if isinstance(row, dict) and str(row.get("identity", "")).upper() == identity]
    _need(len(rows) == 1, "FLAG_ANCHOR_IDENTITY_NOT_UNIQUE",
          f"Flag-anchor audit must contain exactly one row for {identity}")
    row = rows[0]
    status = row.get("status")
    _need(status in ("EXACT_FLAG_KEY", "NO_EXACT_FLAG_KEY"),
          "FLAG_ANCHOR_STATUS_UNSUPPORTED", f"Unsupported authoritative flag status {status!r}")
    _need(spec.get("flag_status") == status, "FLAG_STATUS_MISMATCH",
          f"Spec flag_status {spec.get('flag_status')!r} differs from official audit {status!r}")

    coordinate_path = _resolve(audit_path.parent, audit.get("coordinate_authority"),
                               "flag-anchor audit coordinate_authority")
    coordinate_sha = audit.get("coordinate_authority_sha256")
    _need(isinstance(coordinate_sha, str) and len(coordinate_sha) == 64
          and _sha256(coordinate_path) == coordinate_sha.lower(),
          "FLAG_COORDINATE_AUTHORITY_SHA_DRIFT",
          "Current spriteFlagsInfo.txt does not match the SHA pinned by the flag-anchor audit")
    sprite_path = _resolve(audit_path.parent, audit.get("flag_grey_path"),
                           "flag-anchor audit flag_grey_path")
    sprite_sha = audit.get("flag_grey_sha256")
    _need(isinstance(sprite_sha, str) and len(sprite_sha) == 64
          and _sha256(sprite_path) == sprite_sha.lower(),
          "FLAG_SPRITE_SHA_DRIFT", "Current official flag sprite does not match its pinned SHA")
    sprite = _rgba(sprite_path, "official flag sprite")
    try:
        _need(audit.get("flag_sprite_pixels_3x") == list(sprite.size),
              "FLAG_SPRITE_DIMENSIONS_MISMATCH", "Flag sprite dimensions differ from the anchor audit")
    finally:
        sprite.close()
    _need(audit.get("flag_red_alpha_geometry_matches_grey") is True,
          "FLAG_COLOR_GEOMETRY_MISMATCH", "Color flag and grey anchor sprite geometries are not verified equal")

    parsed: list[tuple[tuple[tuple[int, int], bool], ...]] = []
    raw = coordinate_path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(raw.splitlines(), 1):
        parts = line.split()
        if not parts or parts[0].startswith("#") or parts[0].upper() != identity:
            continue
        try:
            count = int(parts[1])
            _need(count == 2 and len(parts) == 2 + count * 3,
                  "FLAG_COORDINATE_ROW_INVALID",
                  f"{coordinate_path}:{line_number} must contain two [x,y,mirror] anchors")
            anchors = []
            for index in range(count):
                x, y, mirror = (int(value) for value in parts[2 + index * 3:5 + index * 3])
                _need(mirror in (0, 1), "FLAG_MIRROR_VALUE_INVALID",
                      f"{coordinate_path}:{line_number} mirror values must be 0 or 1")
                anchors.append(((x, y), bool(mirror)))
            parsed.append(tuple(anchors))
        except (ValueError, IndexError) as error:
            raise GuardFailure("FLAG_COORDINATE_ROW_INVALID",
                               f"Cannot parse {coordinate_path}:{line_number}: {error}") from error
    _need(not parsed or all(row_value == parsed[0] for row_value in parsed),
          "FLAG_COORDINATE_DUPLICATE_CONFLICT",
          f"Duplicate spriteFlagsInfo rows for {identity} disagree")
    coordinate_anchors = parsed[0] if parsed else ()

    if status == "NO_EXACT_FLAG_KEY":
        _need(not coordinate_anchors and row.get("fixed_flagpole_ground_points_3x") == [],
              "NO_FLAG_STATUS_CONFLICT",
              f"{identity} is marked without a flag key but has native flag anchors")
        return status, {}, {"audit_path": str(audit_path), "audit_sha256": expected_audit_sha.lower(),
                            "coordinate_authority": str(coordinate_path),
                            "coordinate_authority_sha256": coordinate_sha.lower(),
                            "flag_sprite": str(sprite_path), "flag_sprite_sha256": sprite_sha.lower()}

    _need(len(coordinate_anchors) == 2, "FLAG_COORDINATES_MISSING",
          f"{identity} requires exactly two native spriteFlagsInfo anchors")
    audited_points = row.get("fixed_flagpole_ground_points_3x")
    _need(isinstance(audited_points, list) and len(audited_points) == 2,
          "FLAG_GROUND_POINTS_MISSING", f"{identity} authoritative audit needs two 3X ground points")
    foot_unmirrored = _pair(audit.get("pole_foot_pixel_unmirrored"), "pole_foot_pixel_unmirrored")
    foot_mirrored = _pair(audit.get("pole_foot_pixel_mirrored"), "pole_foot_pixel_mirrored")
    expected_by_anchor: list[tuple[tuple[int, int], bool, tuple[int, int]]] = []
    for anchor, mirror in coordinate_anchors:
        local_foot = foot_mirrored if mirror else foot_unmirrored
        expected_by_anchor.append((anchor, mirror,
                                   (anchor[0] * REQUIRED_SCALE + local_foot[0],
                                    anchor[1] * REQUIRED_SCALE + local_foot[1])))
    expected_by_x = sorted(expected_by_anchor, key=lambda entry: entry[2][0])
    audited_by_x = sorted(audited_points, key=lambda point: point["flagpole_ground_3x"][0])
    _need(len(audited_by_x) == 2, "FLAG_GROUND_POINTS_MISSING", f"{identity} needs two ground points")
    points_by_side: dict[str, tuple[int, int]] = {}
    for side, expected, recorded in zip(("left", "right"), expected_by_x, audited_by_x):
        recorded_anchor = _pair(recorded.get("native_flag_anchor"), f"{side}.native_flag_anchor")
        recorded_mirror = recorded.get("mirrored_across_vertical_axis")
        recorded_ground = _pair(recorded.get("flagpole_ground_3x"), f"{side}.flagpole_ground_3x")
        _need(recorded_anchor == expected[0] and recorded_mirror is expected[1]
              and recorded_ground == expected[2],
              "FLAG_GROUND_POINT_AUTHORITY_MISMATCH",
              f"{identity} {side} ground point differs from spriteFlagsInfo.txt and official sprite foot")
        points_by_side[side] = recorded_ground
    return status, points_by_side, {
        "audit_path": str(audit_path),
        "audit_sha256": expected_audit_sha.lower(),
        "coordinate_authority": str(coordinate_path),
        "coordinate_authority_sha256": coordinate_sha.lower(),
        "flag_sprite": str(sprite_path),
        "flag_sprite_sha256": sprite_sha.lower(),
    }


def _validate_flags_3x(
    spec: dict[str, Any],
    candidate: Image.Image,
    hang_local: tuple[int, int],
    alpha_threshold: int,
    authoritative_feet: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    """Use exact candidate 3X coordinates; do not round sprite-space points through 1X."""
    flags = spec.get("flags")
    _need(isinstance(flags, list) and len(flags) == 2,
          "FLAG_PAIR_REQUIRED", "Spec must contain two measured flags; one midpoint cannot replace them")
    sides = [flag.get("side") if isinstance(flag, dict) else None for flag in flags]
    _need(all(isinstance(side, str) for side in sides) and sorted(sides) == ["left", "right"],
          "FLAG_SIDES_INVALID", f"Flag sides must be exactly left and right; got {sides!r}")
    scale = spec.get("target_scale")
    size = candidate.size
    positions: dict[str, tuple[int, int]] = {}
    results: list[dict[str, Any]] = []
    for flag in flags:
        side = flag["side"]
        top = _pair(flag.get("pole_top_3x"), f"{side}.pole_top_3x")
        foot = _pair(flag.get("pole_foot_3x"), f"{side}.pole_foot_3x")
        _need(foot == authoritative_feet.get(side), "FLAG_FOOT_DOES_NOT_MATCH_AUTHORITY",
              f"{side} pole foot {foot} differs from the identity's official ground point {authoritative_feet.get(side)}")
        origin = _pair(flag.get("flag_origin_3x"), f"{side}.flag_origin_3x")
        raw_line = flag.get("pole_centerline_3x")
        _need(isinstance(raw_line, list) and len(raw_line) >= 2,
              "POLE_CENTERLINE_MISSING", f"{side} flag requires a measured pole_centerline_3x")
        centerline = [_pair(point, f"{side}.pole_centerline_3x") for point in raw_line]
        for index, point in enumerate(centerline):
            _inside_point(point, size, f"{side}.pole_centerline_3x[{index}]")
        _inside_point(top, size, f"{side}.pole_top_3x")
        _inside_point(foot, size, f"{side}.pole_foot_3x")
        _need(top != foot, "POLE_AXIS_DEGENERATE", f"{side} pole top and foot must be separate points")
        hang = (origin[0] + hang_local[0] * scale, origin[1] + hang_local[1] * scale)
        _need(hang == top, "FLAG_HANGPOINT_MISALIGNED",
              f"{side} flag hanging end {hang} must equal its own pole top {top}")
        _need(centerline[0] == top and centerline[-1] == foot,
              "POLE_CENTERLINE_ENDPOINT_MISMATCH",
              f"{side} pole centerline must start at its pole_top_3x and end at its pole_foot_3x")
        face_w = FLAG_FACE_SIZE_1X[0] * scale
        face_h = FLAG_FACE_SIZE_1X[1] * scale
        _need(origin[0] >= 0 and origin[1] >= 0
              and origin[0] + face_w <= size[0] and origin[1] + face_h <= size[1],
              "FLAG_FACE_OUT_OF_BOUNDS", f"{side} projected 3X flag face at {origin} leaves canvas {size}")
        top_alpha = candidate.getpixel(top)[3]
        _need(top_alpha >= alpha_threshold, "POLE_TOP_TRANSPARENT",
              f"{side} pole top at {top} has alpha {top_alpha} below {alpha_threshold}")

        rect = _rect(flag.get("ground_support_rect_3x"), f"{side}.ground_support_rect_3x")
        left, upper, right, lower = rect
        _need((right - left, lower - upper) == GROUND_SUPPORT_SIZE_3X,
              "GROUND_SUPPORT_SIZE_INVALID", f"{side} support patch must be exactly 17x17")
        _need(0 <= left < right <= size[0] and 0 <= upper < lower <= size[1],
              "GROUND_SUPPORT_RECT_OUT_OF_BOUNDS",
              f"{side} support rect {rect} must fit entirely inside 3X canvas {size}; edge clamping is forbidden")
        _need(left <= foot[0] < right and upper <= foot[1] < lower,
              "GROUND_SUPPORT_DOES_NOT_CONTAIN_POLE_FOOT",
              f"{side} support patch must include its own measured pole foot {foot}")
        alphas = [candidate.getpixel((x, y))[3] for y in range(upper, lower) for x in range(left, right)]
        supported = sum(alpha >= alpha_threshold for alpha in alphas)
        ratio = supported / len(alphas)
        _need(ratio == GROUND_SUPPORT_RATIO, "GROUND_SUPPORT_INCOMPLETE",
              f"{side} support patch is {supported}/{len(alphas)} ({ratio:.1%}); required 100%")

        line = _line_points(centerline)
        tolerance = flag.get("shaft_tolerance_3x", 1)
        _need(isinstance(tolerance, int) and 0 <= tolerance <= 3,
              "SHAFT_TOLERANCE_INVALID", f"{side}.shaft_tolerance_3x must be an integer from 0 through 3")
        hits = 0
        for x, y in line:
            found = any(
                0 <= x + dx < size[0] and 0 <= y + dy < size[1]
                and candidate.getpixel((x + dx, y + dy))[3] >= alpha_threshold
                for dy in range(-tolerance, tolerance + 1)
                for dx in range(-tolerance, tolerance + 1)
            )
            hits += int(found)
        _need(hits == len(line), "POLE_SHAFT_DISCONNECTED",
              f"{side} pole shaft alpha continuity is {hits}/{len(line)}; complete pole required")
        positions[side] = top
        results.append({
            "side": side,
            "pole_top_3x": list(top),
            "pole_top_alpha": top_alpha,
            "flag_origin_3x": list(origin),
            "flag_hangpoint_local_1x": list(hang_local),
            "flag_hangpoint_3x": list(hang),
            "pole_foot_3x": list(foot),
            "ground_support_rect_3x": list(rect),
            "ground_support_pixels": supported,
            "ground_support_total": len(alphas),
            "ground_support_ratio": ratio,
            "pole_centerline_samples_3x": len(line),
            "pole_centerline_supported": hits,
            "pole_centerline_ratio": hits / len(line),
        })
    _need(positions["left"][0] < positions["right"][0],
          "FLAG_SIDE_ORDER_INVALID", "Left pole top must be left of right pole top; shared midpoint is insufficient")
    return results


def _write_proof(
    source: Image.Image,
    flag_mask: Image.Image,
    flags: list[dict[str, Any]],
    scale: int,
    destination: Path,
) -> None:
    proof = source.copy()
    draw = ImageDraw.Draw(proof)
    grid = 32 * scale
    for x in range(0, proof.width + 1, grid):
        draw.line((x, 0, x, proof.height), fill=(255, 230, 0, 170), width=1)
    for y in range(0, proof.height + 1, grid):
        draw.line((0, y, proof.width, y), fill=(255, 230, 0, 170), width=1)
    face = flag_mask.resize((FLAG_FACE_SIZE_1X[0] * scale, FLAG_FACE_SIZE_1X[1] * scale),
                            Image.Resampling.NEAREST)
    for flag in flags:
        origin = flag["flag_origin_3x"]
        color = (0, 220, 255, 255) if flag["side"] == "left" else (255, 80, 200, 255)
        overlay = Image.new("RGBA", face.size, color)
        overlay.putalpha(face)
        proof.alpha_composite(overlay, origin)
        x, y = flag["pole_top_3x"]
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=color, width=2)
        draw.line((x - 10, y, x + 10, y), fill=color, width=1)
        draw.line((x, y - 10, x, y + 10), fill=color, width=1)
        x, y = flag["pole_foot_3x"]
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline=(255, 30, 30, 255), width=2)
        left, top, right, bottom = flag["ground_support_rect_3x"]
        draw.rectangle((left, top, right - 1, bottom - 1), outline=(0, 255, 70, 255), width=1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    proof.save(destination)
    face.close()
    proof.close()


def verify_building_flag_alignment(
    building_3x_path: Path,
    spec_path: Path,
    receipt_path: Path,
    verify_report_path: Path,
    output_proof_path: Path | None = None,
) -> dict[str, Any]:
    building_3x_path = building_3x_path.resolve()
    spec_path = spec_path.resolve()
    receipt_path = receipt_path.resolve()
    verify_report_path = verify_report_path.resolve()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "FAIL",
        "identity": None,
        "building_3x": str(building_3x_path),
        "spec": str(spec_path),
        "receipt": str(receipt_path),
        "expand_verify_report": str(verify_report_path),
        "errors": [],
        "flags": [],
    }
    native_image = None
    candidate = None
    mask = None
    try:
        spec = _read_json(spec_path)
        _need(spec.get("schema") == SPEC_SCHEMA, "SPEC_SCHEMA_UNSUPPORTED",
              f"Expected {SPEC_SCHEMA}; got {spec.get('schema')!r}")
        identity = spec.get("identity")
        _need(isinstance(identity, str) and identity.strip(), "SPEC_IDENTITY_MISSING",
              "Spec identity is required; unregistered images cannot pass")
        report["identity"] = identity.upper()
        _need(spec.get("fit_anchor") == "bottom_right", "SPEC_FIT_ANCHOR_NOT_BOTTOM_RIGHT",
              "Town map objects require fit_anchor=bottom_right")
        _need(spec.get("target_scale") == REQUIRED_SCALE, "SPEC_TARGET_SCALE_MISMATCH",
              f"Spec must target {REQUIRED_SCALE}X")
        _need(isinstance(spec.get("native_reference"), dict), "NATIVE_REFERENCE_SPEC_MISSING",
              "Spec must bind to an official 1999 1X frame")
        anchor_status, authoritative_feet, authority_receipt = _load_flag_anchor_authority(spec, spec_path)
        report["flag_status"] = anchor_status
        report["flag_anchor_authority"] = authority_receipt
        native_path, native_size, native_image = _validate_native_reference(spec, spec_path)
        candidate = _rgba(building_3x_path, "candidate 3X")
        receipt = _check_receipt(receipt_path, verify_report_path, building_3x_path, spec, native_path,
                                 native_size, candidate.size)
        report.update({
            "native_reference": str(native_path),
            "native_reference_sha256": spec["native_reference"]["sha256"].lower(),
            "native_reference_pixels": list(native_size),
            "candidate_sha256": _sha256(building_3x_path),
            "candidate_pixels": list(candidate.size),
            "receipt_sha256": _sha256(receipt_path),
            "fit_anchor": receipt["silhouette"]["fit_anchor"],
            "target_scale": REQUIRED_SCALE,
        })
        if anchor_status == "NO_EXACT_FLAG_KEY":
            _need(spec.get("flags") == [] and "flag_face" not in spec,
                  "NO_FLAG_SPEC_MUST_BE_EMPTY",
                  "An official no-key identity must not invent flag faces or pole anchors")
            report["status"] = "REVIEW_REQUIRED"
            report["reason"] = "No exact spriteFlagsInfo key; native DEF or actual MOD overlay still requires review"
            return report
        mask, hang_local = _load_flag_face(spec, spec_path)
        report["flag_face"] = {
            "bbox_1x": list(FLAG_FACE_SIZE_1X),
            "alpha_pixels_1x": FLAG_FACE_PIXELS_1X,
            "hang_point_local_1x": list(hang_local),
            "source_image": spec["flag_face"]["source_image"],
        }
        alpha_threshold = spec.get("alpha_threshold", DEFAULT_ALPHA_THRESHOLD)
        _need(isinstance(alpha_threshold, int) and 1 <= alpha_threshold <= 255,
              "ALPHA_THRESHOLD_INVALID", "alpha_threshold must be an integer from 1 through 255")
        report["flags"] = _validate_flags_3x(spec, candidate, hang_local, alpha_threshold,
                                             authoritative_feet)
        if output_proof_path is not None:
            _write_proof(candidate, mask, report["flags"], REQUIRED_SCALE, output_proof_path.resolve())
            report["proof"] = str(output_proof_path.resolve())
        report["status"] = "REVIEW_REQUIRED"
        report["reason"] = "Legacy alpha/support checks do not prove the actual flag-to-pole contact or road entrance"
    except GuardFailure as error:
        report["errors"].append({"code": error.code, "message": str(error)})
    except (OSError, ValueError, KeyError, TypeError) as error:
        report["errors"].append({"code": "UNEXPECTED_INPUT_ERROR", "message": str(error)})
    finally:
        for image in (native_image, candidate, mask):
            if image is not None:
                image.close()
    return report


def verify_batch(batch_path: Path) -> dict[str, Any]:
    batch_path = batch_path.resolve()
    batch = _read_json(batch_path)
    _need(batch.get("schema") == BATCH_SCHEMA, "BATCH_SCHEMA_UNSUPPORTED",
          f"Expected {BATCH_SCHEMA}; got {batch.get('schema')!r}")
    items = batch.get("items")
    _need(isinstance(items, list) and items, "BATCH_ITEMS_MISSING", "Batch must contain at least one item")
    results = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"status": "FAIL", "errors": [{"code": "BATCH_ITEM_INVALID",
                                                           "message": f"Item {index} is not an object"}]})
            continue
        try:
            identity = item.get("identity")
            _need(isinstance(identity, str) and identity.strip(),
                  "IDENTITY_MISSING", f"Item {index} needs an explicit identity")
            candidate = _resolve(batch_path.parent, item.get("building_3x"), f"items[{index}].building_3x")
            spec = _resolve(batch_path.parent, item.get("spec"), f"items[{index}].spec")
            receipt = _resolve(batch_path.parent, item.get("receipt"), f"items[{index}].receipt")
            verify_report = _resolve(batch_path.parent, item.get("verify_report"), f"items[{index}].verify_report")
            proof_value = item.get("proof")
            proof = _resolve(batch_path.parent, proof_value, f"items[{index}].proof") if proof_value else None
            result = verify_building_flag_alignment(candidate, spec, receipt, verify_report, proof)
            if result.get("identity") != identity.upper():
                result["status"] = "FAIL"
                result["errors"].append({
                    "code": "BATCH_IDENTITY_MISMATCH",
                    "message": f"Batch identity {identity} differs from spec identity {result.get('identity')!r}",
                })
            results.append(result)
        except GuardFailure as error:
            results.append({"status": "FAIL", "errors": [{"code": error.code, "message": str(error)}]})
    passed = sum(result.get("status") == "PASS" for result in results)
    not_applicable = sum(result.get("status") == "NOT_APPLICABLE" for result in results)
    review_required = sum(result.get("status") == "REVIEW_REQUIRED" for result in results)
    failed = len(results) - passed - not_applicable - review_required
    return {
        "schema": "h3.flag-alignment-batch-report.v1",
        "status": ("FAIL" if failed else "REVIEW_REQUIRED" if review_required else
                   "PASS_WITH_NOT_APPLICABLE" if not_applicable else "PASS"),
        "items": len(results),
        "passed": passed,
        "not_applicable": not_applicable,
        "review_required": review_required,
        "failed": failed,
        "results": results,
    }


def audit_expansion_receipt(
    building_3x_path: Path,
    receipt_path: Path,
    verify_report_path: Path,
    identity: str | None = None,
) -> dict[str, Any]:
    """Audit receipt bindings and map anchor policy without claiming flag geometry passed."""
    building_3x_path = building_3x_path.resolve()
    receipt_path = receipt_path.resolve()
    verify_report_path = verify_report_path.resolve()
    result: dict[str, Any] = {
        "status": "FAIL",
        "building_3x": str(building_3x_path),
        "receipt": str(receipt_path),
        "expand_verify_report": str(verify_report_path),
        "errors": [],
    }
    reference = None
    candidate = None
    try:
        receipt = _read_json(receipt_path)
        entry = receipt.get("contract", {}).get("entry", {})
        silhouette = receipt.get("silhouette", {})
        source = entry.get("source")
        if identity is None:
            identity = Path(source).parent.name if isinstance(source, str) else None
        _need(isinstance(identity, str) and identity.strip(), "IDENTITY_MISSING",
              "Receipt audit needs the resource identity")
        result["identity"] = identity.upper()
        result["fit_anchor"] = silhouette.get("fit_anchor")
        result["candidate_sha256"] = _sha256(building_3x_path) if building_3x_path.is_file() else None
        result["receipt_sha256"] = _sha256(receipt_path)
        result["expand_verify_report_sha256"] = _sha256(verify_report_path) if verify_report_path.is_file() else None
        native_path = _resolve(receipt_path.parent, entry.get("reference"), "receipt.reference")
        native_sha = _sha256(native_path) if native_path.is_file() else ""
        _need(isinstance(entry.get("reference_sha256"), str)
              and native_sha == entry["reference_sha256"].lower(),
              "NATIVE_REFERENCE_SHA_DRIFT",
              "Current official 1X frame does not match the SHA pinned in the H3TierExport receipt")
        reference = _rgba(native_path, "official 1X reference")
        candidate = _rgba(building_3x_path, "candidate 3X")
        spec = {
            "identity": identity,
            "native_reference": {"sha256": entry.get("reference_sha256")},
        }
        _check_receipt(receipt_path, verify_report_path, building_3x_path, spec,
                       native_path, reference.size, candidate.size)
        result["candidate_pixels"] = list(candidate.size)
        result["native_reference"] = str(native_path)
        result["native_reference_pixels"] = list(reference.size)
        result["status"] = "PASS"
    except GuardFailure as error:
        result["errors"].append({"code": error.code, "message": str(error)})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["errors"].append({"code": "UNEXPECTED_INPUT_ERROR", "message": str(error)})
    finally:
        for image in (reference, candidate):
            if image is not None:
                image.close()
    return result


def audit_receipt_batch(batch_path: Path) -> dict[str, Any]:
    batch_path = batch_path.resolve()
    batch = _read_json(batch_path)
    _need(batch.get("schema") == RECEIPT_BATCH_SCHEMA, "RECEIPT_BATCH_SCHEMA_UNSUPPORTED",
          f"Expected {RECEIPT_BATCH_SCHEMA}; got {batch.get('schema')!r}")
    items = batch.get("items")
    _need(isinstance(items, list) and items, "BATCH_ITEMS_MISSING", "Batch must contain at least one item")
    results = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"status": "FAIL", "errors": [{"code": "BATCH_ITEM_INVALID",
                                                           "message": f"Item {index} is not an object"}]})
            continue
        try:
            identity = item.get("identity")
            _need(isinstance(identity, str) and identity.strip(),
                  "IDENTITY_MISSING", f"Item {index} needs an explicit identity")
            candidate = _resolve(batch_path.parent, item.get("building_3x"), f"items[{index}].building_3x")
            receipt = _resolve(batch_path.parent, item.get("receipt"), f"items[{index}].receipt")
            verify_report = _resolve(batch_path.parent, item.get("verify_report"), f"items[{index}].verify_report")
            results.append(audit_expansion_receipt(candidate, receipt, verify_report, identity))
        except GuardFailure as error:
            results.append({"status": "FAIL", "errors": [{"code": error.code, "message": str(error)}]})
    passed = sum(result.get("status") == "PASS" for result in results)
    return {
        "schema": "h3.flag-alignment-receipt-batch-report.v1",
        "status": "PASS" if passed == len(results) else "FAIL",
        "items": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
        "claim_boundary": "Receipt and expand-verify binding only; flag geometry still requires verify with a complete landmark spec.",
    }


def _emit_report(report: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed validator for two separate town entrance flags, pole tips, feet, and H3TierExport 3X receipts."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="Verify one 3X image against its pinned spec and expansion receipt")
    verify.add_argument("--building-3x", type=Path, required=True)
    verify.add_argument("--spec", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--verify-report", type=Path, required=True)
    verify.add_argument("--proof", type=Path)
    verify.add_argument("--report", type=Path)
    batch = sub.add_parser("verify-batch", help="Verify every item in a pinned batch JSON")
    batch.add_argument("--batch", type=Path, required=True)
    batch.add_argument("--report", type=Path)
    audit = sub.add_parser("audit-receipts", help="Audit receipt hashes, bottom_right policy, and expand-verify for a batch")
    audit.add_argument("--batch", type=Path, required=True)
    audit.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_building_flag_alignment(args.building_3x, args.spec, args.receipt,
                                                    args.verify_report, args.proof)
        elif args.command == "verify-batch":
            result = verify_batch(args.batch)
        else:
            result = audit_receipt_batch(args.batch)
        _emit_report(result, args.report)
        return 0 if result.get("status") in ("PASS", "NOT_APPLICABLE", "PASS_WITH_NOT_APPLICABLE") else 1
    except GuardFailure as error:
        result = {"schema": REPORT_SCHEMA, "status": "FAIL",
                  "errors": [{"code": error.code, "message": str(error)}]}
        _emit_report(result, getattr(args, "report", None))
        return 1


if __name__ == "__main__":
    sys.exit(main())
