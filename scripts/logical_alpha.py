"""Produce receipt-bound 3X logical-alpha data from the frozen road contract.

The companion verifies producer-bound PNG data against the canonical H3 slot
inventory. The native EXE owns resampling and the locked, atomic output batch.
This is a data transform, not painted art, an art approval, or game acceptance.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from PIL import Image

PRODUCT = resolve_tool_root(__file__)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_native_batch as native  # Reuse canonical inventory/key/geometry parsing.

OUTPUT_BASE = PRODUCT / "build" / "experiments" / "logical-alpha"
MANIFEST_SCHEMA = "h3.map-ui.road-logical-alpha-source-data-contract.v1"
MATERIAL_MANIFEST_SCHEMA = "h3.logical-alpha.road-material-input.v1"
MATERIAL_ROLE = "road-material-3x"
SPEC_SCHEMA = "h3.logical-alpha.render-spec.v1"
RECEIPT_SCHEMA = "h3.logical-alpha.completion.v2"
COMPLETION_NAME = "logical-alpha-completion.json"
QUALITY_ALGORITHM = "linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4"
FAMILY_ORDER = ("DIRTRD", "GRAVRD", "COBBRD")
FRAMES_PER_FAMILY = tuple(range(17))
MAX_CONTRACT_BYTES = 16 * 1024 * 1024
MAX_INPUT_PNG_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_PNG_BYTES = 128 * 1024 * 1024
MAX_BATCH_FRAMES = 51
OPERATION_RE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class LogicalAlphaError(RuntimeError):
    """Input, output, or evidence does not satisfy the logical-alpha contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _same_file_path(left: Any, right: Path) -> bool:
    if not isinstance(left, str) or not left:
        return False
    try:
        native.reject_reparse_components(Path(left), allow_missing_leaf=False)
        return _path_key(Path(left).resolve(strict=True)) == _path_key(right.resolve(strict=True))
    except (OSError, native.ReviewError):
        return False


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError as error:
        raise LogicalAlphaError(f"{label}: cannot read: {error}") from error
    if not data or len(data) > limit:
        raise LogicalAlphaError(f"{label}: byte limit exceeded or empty")
    return data


def _exact(value: Any, required: set[str], label: str, optional: set[str] | None = None) -> None:
    if not isinstance(value, dict):
        raise LogicalAlphaError(f"{label}: object required")
    optional = optional or set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        raise LogicalAlphaError(f"{label}: schema mismatch (missing={sorted(missing)}, unknown={sorted(unknown)})")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise LogicalAlphaError(f"{label}: lowercase SHA256 required")
    return value


def _pair(value: Any, label: str, minimum: int = 1) -> list[int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(part) is not int or part < minimum or part > 16384 for part in value)):
        raise LogicalAlphaError(f"{label}: bounded integer pair required")
    return value


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


def _contract_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise LogicalAlphaError(f"{label}: absolute path required")
    raw = Path(value)
    if not raw.is_absolute():
        raise LogicalAlphaError(f"{label}: absolute path required")
    try:
        native.reject_reparse_components(raw, allow_missing_leaf=False)
        resolved = raw.resolve(strict=True)
    except (OSError, native.ReviewError) as error:
        raise LogicalAlphaError(f"{label}: path is missing or unsafe: {error}") from error
    if not resolved.is_file():
        raise LogicalAlphaError(f"{label}: regular file required")
    return resolved


def _relative_input(root: Path, relative: Any, recorded: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\0" in relative:
        raise LogicalAlphaError(f"{label}: archive-relative path required")
    rel = Path(relative)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise LogicalAlphaError(f"{label}: archive-relative path is invalid")
    try:
        unresolved = root / rel
        native.reject_reparse_components(unresolved, allow_missing_leaf=False)
        path = unresolved.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except (OSError, native.ReviewError) as error:
        raise LogicalAlphaError(f"{label}: archived input is missing or unsafe: {error}") from error
    if not _path_within(path, root_resolved) or not path.is_file():
        raise LogicalAlphaError(f"{label}: input is outside the frozen archive root")
    if not isinstance(recorded, str) or _path_key(Path(recorded).resolve(strict=True)) != _path_key(path):
        raise LogicalAlphaError(f"{label}: archive_path differs from archive_root/relative path")
    return path


def _decode_logical_png(raw: bytes, expected_pixels: list[int], expected_alpha_sha: str,
                        expected_positive: int, label: str) -> tuple[bytes, dict[str, Any]]:
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[24] != 8 or raw[25] != 6:
        raise LogicalAlphaError(f"{label}: 8-bit RGBA PNG required")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise LogicalAlphaError(f"{label}: static RGBA PNG required")
            if list(image.size) != expected_pixels:
                raise LogicalAlphaError(f"{label}: dimensions differ from frozen native crop")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            rgba = image.tobytes()
    except (OSError, ValueError) as error:
        if isinstance(error, LogicalAlphaError):
            raise
        raise LogicalAlphaError(f"{label}: PNG decode failed: {error}") from error
    alpha = rgba[3::4]
    if sha256_bytes(alpha) != expected_alpha_sha:
        raise LogicalAlphaError(f"{label}: alpha-plane SHA256 mismatch")
    if sum(value > 0 for value in alpha) != expected_positive:
        raise LogicalAlphaError(f"{label}: alpha positive-pixel count mismatch")
    channels = [[rgba[offset + channel] for offset in range(0, len(rgba), 4)] for channel in range(3)]
    visible_colors = {tuple(rgba[offset:offset + 3]) for offset in range(0, len(rgba), 4)
                      if rgba[offset + 3] > 0}
    all_white = all(min(channel) == 255 and max(channel) == 255 for channel in channels)
    rgb_encoding = {"all_rgb_pixels_white": all_white,
                    "channel_extrema": [[min(channel), max(channel)] for channel in channels],
                    "classification": "RGBA_RGB_ALL_WHITE_ALPHA_MASK" if all_white else "RGBA_RGB_NONWHITE_ALPHA_MASK",
                    "visible_rgb_unique_count": len(visible_colors)}
    return alpha, {"pixels": expected_pixels, "alpha_sha256": expected_alpha_sha,
                   "alpha_positive_pixels": expected_positive, "rgb_encoding": rgb_encoding}


def _validate_authority(contract: dict[str, Any]) -> tuple[Path, str, dict[tuple[Any, ...], dict[str, Any]]]:
    authority = contract.get("source_authority")
    _exact(authority, {"native_image_reader", "native_catalog", "native_lod", "map_ui_producer", "claim_boundary"},
           "source_authority")
    reader = authority["native_image_reader"]
    catalog = authority["native_catalog"]
    lod = authority["native_lod"]
    producer = authority["map_ui_producer"]
    for item, required, label in (
            (reader, {"bytes", "path", "sha256", "symbol"}, "native_image_reader"),
            (catalog, {"bytes", "path", "sha256"}, "native_catalog"),
            (lod, {"bytes", "path", "sha256"}, "native_lod"),
            (producer, {"bytes", "path", "sha256"}, "map_ui_producer")):
        _exact(item, required, label)
        _sha(item["sha256"], f"{label}.sha256")
        if type(item["bytes"]) is not int or item["bytes"] <= 0:
            raise LogicalAlphaError(f"{label}.bytes: positive byte count required")
    if reader["symbol"] not in ("art_town_batch_r16.py::native_image", "native_h3_io.py::native_image"):
        raise LogicalAlphaError("native image reader symbol differs from the logical-alpha contract")
    canonical = native.canonical_inventory_path()
    if _contract_path(catalog["path"], "source_authority.native_catalog") != canonical:
        raise LogicalAlphaError("source_authority.native_catalog is not the canonical H3 inventory")
    inventory_sha = _sha(catalog["sha256"], "source_authority.native_catalog.sha256")
    rows, raw_inventory = native.load_inventory(canonical, inventory_sha)
    if len(raw_inventory) != catalog["bytes"] or sha256_bytes(raw_inventory) != inventory_sha:
        raise LogicalAlphaError("canonical inventory bytes changed after load")
    return canonical, inventory_sha, rows


def _geometry_3x(geometry: dict[str, Any], label: str) -> dict[str, list[int]]:
    _exact(geometry, {"canvas", "crop", "crop_box_exclusive", "origin", "outside_crop_zero",
                      "prototype_alpha_positive_outside_crop", "prototype_missing_positive_source_blocks",
                      "prototype_output_alpha_positive_pixels", "prototype_output_pixels", "calculation"}, label)
    canvas = _pair(geometry["canvas"], label + ".canvas")
    crop = _pair(geometry["crop"], label + ".crop")
    origin = _pair(geometry["origin"], label + ".origin", 0)
    box = geometry["crop_box_exclusive"]
    if not isinstance(box, list) or len(box) != 4 or any(type(part) is not int or part < 0 for part in box):
        raise LogicalAlphaError(f"{label}.crop_box_exclusive: nonnegative rectangle required")
    expected_box = [origin[0], origin[1], origin[0] + crop[0], origin[1] + crop[1]]
    if box != expected_box or box[2] > canvas[0] or box[3] > canvas[1]:
        raise LogicalAlphaError(f"{label}.crop_box_exclusive: inconsistent with canvas/crop/origin")
    if geometry["outside_crop_zero"] is not True:
        raise LogicalAlphaError(f"{label}: producer-reported out-of-crop coverage is not zero")
    if geometry["prototype_alpha_positive_outside_crop"] != 0:
        raise LogicalAlphaError(f"{label}: producer-reported out-of-crop alpha is not zero")
    if geometry["prototype_missing_positive_source_blocks"] != 0:
        raise LogicalAlphaError(f"{label}: producer-reported source-cell coverage failed")
    if geometry["prototype_output_pixels"] != crop:
        raise LogicalAlphaError(f"{label}: producer geometry dimensions differ")
    return {"canvas": canvas, "crop": crop, "origin": origin}


def validate_contract(manifest_path: Path, manifest_sha256: str,
                      identities: list[str] | None = None) -> dict[str, Any]:
    """Read and completely preflight the selected families against source authority."""
    manifest_path = _contract_path(str(manifest_path), "manifest")
    expected = _sha(manifest_sha256, "manifest_sha256")
    raw_contract = _read_bounded(manifest_path, MAX_CONTRACT_BYTES, "logical-alpha source contract")
    actual_manifest_sha = sha256_bytes(raw_contract)
    if actual_manifest_sha != expected:
        raise LogicalAlphaError("MANIFEST_SHA256_MISMATCH")
    try:
        contract = native.parse_json(raw_contract, "logical-alpha source contract")
    except native.ReviewError as error:
        raise LogicalAlphaError(str(error)) from error
    if isinstance(contract, dict) and contract.get("schema") == MATERIAL_MANIFEST_SCHEMA:
        try:
            return _validate_material_contract(contract, manifest_path, actual_manifest_sha, identities)
        except (KeyError, TypeError, IndexError, AttributeError) as error:
            raise LogicalAlphaError(f"MATERIAL_INPUT_STRUCTURE_INVALID: {error}") from error
    _exact(contract, {"schema", "created_utc", "archive_root", "data_role", "request_scope", "source_key_order",
                      "source_authority", "provenance_evidence", "alpha_plane_decode", "input_encoding",
                      "summary", "claim_boundary", "frames"}, "source contract")
    if contract["schema"] != MANIFEST_SCHEMA:
        raise LogicalAlphaError("SOURCE_CONTRACT_SCHEMA_UNSUPPORTED")
    if contract["data_role"] != "1X native logical-alpha input data only; not painted art or a high-resolution master":
        raise LogicalAlphaError("SOURCE_DATA_ROLE_UNSUPPORTED")
    archive_root_text = contract["archive_root"]
    if not isinstance(archive_root_text, str) or not Path(archive_root_text).is_absolute():
        raise LogicalAlphaError("SOURCE_ARCHIVE_ROOT_INVALID")
    native.reject_reparse_components(Path(archive_root_text), allow_missing_leaf=False)
    archive_root = Path(archive_root_text).resolve(strict=True)
    if not archive_root.is_dir():
        raise LogicalAlphaError("SOURCE_ARCHIVE_ROOT_NOT_DIRECTORY")

    key_order = contract["source_key_order"]
    _exact(key_order, {"identities", "frames_per_identity", "keys", "bound_by", "prototype_array_order_note"},
           "source_key_order")
    if key_order["identities"] != list(FAMILY_ORDER) or key_order["frames_per_identity"] != list(FRAMES_PER_FAMILY):
        raise LogicalAlphaError("SOURCE_KEY_ORDER_UNSUPPORTED")
    expected_keys = [["def", identity, 0, frame] for identity in FAMILY_ORDER for frame in FRAMES_PER_FAMILY]
    if key_order["keys"] != expected_keys:
        raise LogicalAlphaError("SOURCE_KEY_LIST_INCOMPLETE_OR_REORDERED")

    provenance = contract["provenance_evidence"]
    _exact(provenance, {"all_51_private_prototype", "source_review", "native_extraction_receipt",
                        "prototype_reported_native_positive_alpha_total", "prototype_reported_native_crop_gate_failures"},
           "provenance_evidence")
    alpha_decode = contract["alpha_plane_decode"]
    _exact(alpha_decode, {"library", "version", "method", "source_png_bytes_reencoded"}, "alpha_plane_decode")
    if alpha_decode["source_png_bytes_reencoded"] is not False:
        raise LogicalAlphaError("SOURCE_PNG_BYTES_MUST_REMAIN_UNCHANGED")
    encoding = contract["input_encoding"]
    _exact(encoding, {"mode_counts", "rgb_white_alpha_mask_count", "nonwhite_rgb_mask_count", "encoding_counts",
                      "observed_encoding", "neutral_encoding_required_for_current_inputs", "if_future_source_rgb_is_nonwhite"},
           "input_encoding")
    if (encoding["mode_counts"] != {"RGBA": 51}
            or type(encoding["rgb_white_alpha_mask_count"]) is not int
            or type(encoding["nonwhite_rgb_mask_count"]) is not int
            or encoding["rgb_white_alpha_mask_count"] + encoding["nonwhite_rgb_mask_count"] != 51
            or not isinstance(encoding["encoding_counts"], dict)
            or any(not isinstance(key, str) or type(count) is not int or count < 0
                   for key, count in encoding["encoding_counts"].items())
            or sum(encoding["encoding_counts"].values()) != 51
            or not isinstance(encoding["observed_encoding"], str)
            or not isinstance(encoding["if_future_source_rgb_is_nonwhite"], str)
            or encoding["neutral_encoding_required_for_current_inputs"]
            is not (encoding["nonwhite_rgb_mask_count"] > 0)):
        raise LogicalAlphaError("SOURCE_LOGICAL_ALPHA_ENCODING_SUMMARY_INVALID")

    canonical_inventory, inventory_sha, rows = _validate_authority(contract)
    summary = contract["summary"]
    _exact(summary, {"input_png_count", "input_png_total_bytes", "decoded_alpha_plane_total_bytes",
                     "full_native_positive_alpha_pixels_reported_by_source_prototype_total", "3x_geometry_matches_prototype",
                     "3x_outputs_copied", "families", "native_positive_alpha_outside_crop_reported_total",
                     "frames_with_native_positive_alpha_outside_crop_zero"}, "summary")
    expected_summary = {"input_png_count": 51, "families": {identity: 17 for identity in FAMILY_ORDER},
                        "3x_geometry_matches_prototype": 51, "3x_outputs_copied": 0,
                        "native_positive_alpha_outside_crop_reported_total": 0,
                        "frames_with_native_positive_alpha_outside_crop_zero": 51}
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            raise LogicalAlphaError(f"SOURCE_SUMMARY_INVALID: {key}")
    if (type(summary.get("input_png_total_bytes")) is not int or summary["input_png_total_bytes"] < 1
            or type(summary.get("decoded_alpha_plane_total_bytes")) is not int
            or summary["decoded_alpha_plane_total_bytes"] < 1
            or type(summary.get("full_native_positive_alpha_pixels_reported_by_source_prototype_total")) is not int
            or summary["full_native_positive_alpha_pixels_reported_by_source_prototype_total"] < 0):
        raise LogicalAlphaError("SOURCE_SUMMARY_BYTE_TOTALS_INVALID")

    frames = contract["frames"]
    if not isinstance(frames, list) or len(frames) != 51:
        raise LogicalAlphaError("SOURCE_FRAME_COUNT_MUST_BE_51")
    requested = list(FAMILY_ORDER) if identities is None else identities
    if not requested or len(set(requested)) != len(requested) or any(identity not in FAMILY_ORDER for identity in requested):
        raise LogicalAlphaError("IDENTITY_SELECTION_INVALID")
    requested_set = set(requested)
    selected = [identity for identity in FAMILY_ORDER if identity in requested_set]
    selected_set = set(selected)
    validated: list[dict[str, Any]] = []
    total_bytes = 0
    total_alpha_bytes = 0
    contract_png_bytes = 0
    contract_alpha_bytes = 0
    contract_positive = 0
    contract_prototype_positive = 0
    white_mask_count = 0
    nonwhite_mask_count = 0
    encoding_counts: dict[str, int] = {}
    for index, frame in enumerate(frames):
        _exact(frame, {"order_index", "key", "prototype_key", "native_lod_resource", "native_member_payload_sha256",
                       "native_geometry_1x", "native_alpha_source_report", "input_crop_png", "geometry_3x"},
               f"frames[{index}]")
        expected_key = expected_keys[index]
        if (frame["order_index"] != index or frame["key"] != expected_key
                or frame["prototype_key"] != expected_key[1:]):
            raise LogicalAlphaError(f"SOURCE_FRAME_ORDER_OR_KEY_INVALID: {index}")
        identity, group, frame_number = expected_key[1], expected_key[2], expected_key[3]
        key_tuple = native.key_tuple(expected_key)
        row = rows.get(key_tuple)
        if row is None:
            raise LogicalAlphaError(f"OFFICIAL_SLOT_NOT_FOUND: {expected_key}")
        official_targets = native.official_targets(row, key_tuple)
        official = {"canvas": official_targets["canvas_1x"], "crop": official_targets["crop_1x"],
                    "origin": official_targets["origin_1x"]}
        native_geometry = frame["native_geometry_1x"]
        _exact(native_geometry, {"canvas", "crop", "origin"}, f"frames[{index}].native_geometry_1x")
        geometry_1x = {"canvas": _pair(native_geometry["canvas"], "native_geometry_1x.canvas"),
                       "crop": _pair(native_geometry["crop"], "native_geometry_1x.crop"),
                       "origin": _pair(native_geometry["origin"], "native_geometry_1x.origin", 0)}
        if geometry_1x != official:
            raise LogicalAlphaError(f"OFFICIAL_GEOMETRY_MISMATCH: {expected_key}")
        if frame["native_lod_resource"] != row.get("resource"):
            raise LogicalAlphaError(f"OFFICIAL_RESOURCE_MISMATCH: {expected_key}")
        _sha(frame["native_member_payload_sha256"], f"frames[{index}].native_member_payload_sha256")
        geometry_3x = _geometry_3x(frame["geometry_3x"], f"frames[{index}].geometry_3x")
        expected_3x = {name: [part * 3 for part in geometry_1x[name]] for name in ("canvas", "crop", "origin")}
        if geometry_3x != expected_3x:
            raise LogicalAlphaError(f"SOURCE_3X_GEOMETRY_NOT_EXACTLY_3X: {expected_key}")
        alpha_report = frame["native_alpha_source_report"]
        _exact(alpha_report, {"crop_positive_alpha_pixels_in_prototype", "evidence_file_sha256",
                              "full_native_positive_alpha_outside_crop", "full_native_positive_alpha_pixels", "note"},
               f"frames[{index}].native_alpha_source_report")
        _sha(alpha_report["evidence_file_sha256"], f"frames[{index}].native_alpha_source_report.evidence_file_sha256")
        if alpha_report["full_native_positive_alpha_outside_crop"] != 0:
            raise LogicalAlphaError(f"SOURCE_NATIVE_CROP_CLAIM_INVALID: {expected_key}")
        input_row = frame["input_crop_png"]
        _exact(input_row, {"alpha_plane_encoding", "alpha_plane_sha256", "alpha_positive_pixels_pil_verified",
                           "archive_path", "archive_relative_path", "bytes", "pixels", "rgb_encoding", "sha256", "source_scratch_path", "stored_mode"},
               f"frames[{index}].input_crop_png")
        pixels = _pair(input_row["pixels"], f"frames[{index}].input_crop_png.pixels")
        if pixels != geometry_1x["crop"] or input_row["stored_mode"] != "RGBA":
            raise LogicalAlphaError(f"SOURCE_CROP_DIMENSIONS_OR_MODE_INVALID: {expected_key}")
        if input_row["alpha_plane_encoding"] != "8-bit alpha bytes in row-major top-to-bottom order from Pillow RGBA decode":
            raise LogicalAlphaError(f"SOURCE_ALPHA_ENCODING_UNSUPPORTED: {expected_key}")
        rgb_encoding = input_row["rgb_encoding"]
        _exact(rgb_encoding, {"all_rgb_pixels_white", "channel_extrema", "classification", "visible_rgb_unique_count"},
               f"frames[{index}].input_crop_png.rgb_encoding")
        if type(rgb_encoding["all_rgb_pixels_white"]) is not bool:
            raise LogicalAlphaError(f"SOURCE_RGB_ENCODING_INVALID: {expected_key}")
        expected_classification = ("RGBA_RGB_ALL_WHITE_ALPHA_MASK" if rgb_encoding["all_rgb_pixels_white"]
                                   else "RGBA_RGB_NONWHITE_ALPHA_MASK")
        if rgb_encoding["classification"] != expected_classification:
            raise LogicalAlphaError(f"SOURCE_RGB_ENCODING_INVALID: {expected_key}")
        extrema = rgb_encoding["channel_extrema"]
        if (not isinstance(extrema, list) or len(extrema) != 3
                or any(not isinstance(pair, list) or len(pair) != 2
                       or any(type(value) is not int or not 0 <= value <= 255 for value in pair)
                       or pair[0] > pair[1] for pair in extrema)):
            raise LogicalAlphaError(f"SOURCE_RGB_EXTREMA_INVALID: {expected_key}")
        if rgb_encoding["all_rgb_pixels_white"] != all(pair == [255, 255] for pair in extrema):
            raise LogicalAlphaError(f"SOURCE_RGB_WHITE_CLASSIFICATION_MISMATCH: {expected_key}")
        unique_count = rgb_encoding["visible_rgb_unique_count"]
        if type(unique_count) is not int or not 0 <= unique_count <= pixels[0] * pixels[1]:
            raise LogicalAlphaError(f"SOURCE_RGB_UNIQUE_COUNT_INVALID: {expected_key}")
        source_sha = _sha(input_row["sha256"], f"frames[{index}].input_crop_png.sha256")
        alpha_sha = _sha(input_row["alpha_plane_sha256"], f"frames[{index}].input_crop_png.alpha_plane_sha256")
        positive = input_row["alpha_positive_pixels_pil_verified"]
        if type(positive) is not int or not 0 <= positive <= pixels[0] * pixels[1]:
            raise LogicalAlphaError(f"SOURCE_ALPHA_COUNT_INVALID: {expected_key}")
        if (alpha_report["crop_positive_alpha_pixels_in_prototype"] != positive
                or alpha_report["full_native_positive_alpha_pixels"] != positive):
            raise LogicalAlphaError(f"SOURCE_PRODUCER_ALPHA_COUNT_MISMATCH: {expected_key}")
        if type(input_row["bytes"]) is not int or not 1 <= input_row["bytes"] <= MAX_INPUT_PNG_BYTES:
            raise LogicalAlphaError(f"SOURCE_PNG_BYTE_COUNT_INVALID: {expected_key}")
        contract_png_bytes += input_row["bytes"]
        contract_alpha_bytes += pixels[0] * pixels[1]
        contract_positive += positive
        contract_prototype_positive += alpha_report["full_native_positive_alpha_pixels"]
        if rgb_encoding["all_rgb_pixels_white"]:
            white_mask_count += 1
        else:
            nonwhite_mask_count += 1
        encoding_counts[rgb_encoding["classification"]] = encoding_counts.get(rgb_encoding["classification"], 0) + 1
        if identity not in selected_set:
            continue
        source = _relative_input(archive_root, input_row["archive_relative_path"], input_row["archive_path"],
                                 f"frames[{index}].input_crop_png")
        source_raw = _read_bounded(source, MAX_INPUT_PNG_BYTES, f"source PNG {expected_key}")
        if len(source_raw) != input_row["bytes"] or sha256_bytes(source_raw) != source_sha:
            raise LogicalAlphaError(f"SOURCE_PNG_SHA256_OR_SIZE_MISMATCH: {expected_key}")
        alpha, png_info = _decode_logical_png(source_raw, pixels, alpha_sha, positive, f"source PNG {expected_key}")
        if png_info["rgb_encoding"] != rgb_encoding:
            raise LogicalAlphaError(f"SOURCE_RGB_METADATA_MISMATCH: {expected_key}")
        if sum(value > 0 for value in alpha) != positive:
            raise LogicalAlphaError(f"SOURCE_ALPHA_COUNT_MISMATCH: {expected_key}")
        record_id = f"{identity}_g{group}_f{frame_number}"
        validated.append({"id": record_id, "key": expected_key, "source": source, "source_sha256": source_sha,
                          "alpha_sha256": alpha_sha, "source_pixels": pixels, "source_positive_pixels": positive,
                          "source_alpha": alpha, "source_rgb_encoding": rgb_encoding,
                          "geometry_1x": geometry_1x, "geometry_3x": expected_3x,
                          "source_bytes": len(source_raw), "archive_relative_path": input_row["archive_relative_path"],
                          "native_lod_resource": frame["native_lod_resource"],
                          "native_member_payload_sha256": frame["native_member_payload_sha256"],
                          "prototype_native_positive_pixels": alpha_report["full_native_positive_alpha_pixels"]})
        total_bytes += len(source_raw)
        total_alpha_bytes += len(alpha)

    expected_count = 17 * len(selected)
    if len(validated) != expected_count:
        raise LogicalAlphaError("SELECTED_FAMILY_FRAME_COUNT_INVALID")
    if (contract_png_bytes != summary["input_png_total_bytes"]
            or contract_alpha_bytes != summary["decoded_alpha_plane_total_bytes"]
            or contract_positive != summary["full_native_positive_alpha_pixels_reported_by_source_prototype_total"]
            or contract_prototype_positive != summary["full_native_positive_alpha_pixels_reported_by_source_prototype_total"]):
        raise LogicalAlphaError("SOURCE_SUMMARY_TOTALS_DO_NOT_MATCH_FRAME_DATA")
    if (white_mask_count != encoding["rgb_white_alpha_mask_count"]
            or nonwhite_mask_count != encoding["nonwhite_rgb_mask_count"]
            or encoding_counts != encoding["encoding_counts"]):
        raise LogicalAlphaError("SOURCE_ENCODING_SUMMARY_DOES_NOT_MATCH_FRAME_DATA")
    return {"manifest_path": manifest_path, "manifest_sha256": actual_manifest_sha, "contract": contract,
            "archive_root": archive_root, "inventory_path": canonical_inventory, "inventory_sha256": inventory_sha,
            "identities": selected, "frames": validated, "source_png_bytes": total_bytes,
            "source_alpha_positive_pixels": sum(frame["source_positive_pixels"] for frame in validated),
            "source_alpha_cells": total_alpha_bytes, "independent_lod_verified": False}


def _material_pin(ref: Any, label: str, inputs: dict[str, dict]) -> dict[str, Any]:
    if not isinstance(ref, dict) or not {"path", "sha256"} <= ref.keys():
        raise LogicalAlphaError(f"MATERIAL_INPUT_BINDING_REQUIRED: {label}")
    path = _contract_path(ref["path"], label)
    digest = _sha(ref["sha256"], label)
    size = path.stat().st_size
    if not 0 < size <= 512 * 1024 * 1024:
        raise LogicalAlphaError(f"MATERIAL_INPUT_SIZE_LIMIT: {label}")
    if "bytes" in ref and (type(ref["bytes"]) is not int or ref["bytes"] != size):
        raise LogicalAlphaError(f"MATERIAL_INPUT_SIZE_MISMATCH: {label}")
    key = _path_key(path)
    pin = {"path": str(path), "sha256": digest, "bytes": size}
    if key in inputs:
        if inputs[key] != pin:
            raise LogicalAlphaError(f"MATERIAL_INPUT_BINDING_CONFLICT: {label}")
    else:
        if len(inputs) >= 512 or sum(row["bytes"] for row in inputs.values()) + size > 512 * 1024 * 1024:
            raise LogicalAlphaError("MATERIAL_INPUT_BUDGET_EXCEEDED")
        if native.sha256_file(path, 512 * 1024 * 1024) != digest:
            raise LogicalAlphaError(f"MATERIAL_INPUT_DRIFT: {label}")
        inputs[key] = pin
    return {"path": str(path), "sha256": digest}


def _material_json(ref: dict, label: str, inputs: dict[str, dict]) -> dict:
    pin = _material_pin(ref, label, inputs)
    raw = _read_bounded(Path(pin["path"]), MAX_CONTRACT_BYTES, label)
    if sha256_bytes(raw) != pin["sha256"]:
        raise LogicalAlphaError(f"MATERIAL_INPUT_DRIFT: {label}")
    try:
        value = native.parse_json(raw, label)
    except native.ReviewError as error:
        raise LogicalAlphaError(str(error)) from error
    if not isinstance(value, dict):
        raise LogicalAlphaError(f"MATERIAL_JSON_OBJECT_REQUIRED: {label}")
    return value


def _material_image(ref: dict, pixels: list[int], label: str, inputs: dict[str, dict], *,
                    rgba_required: bool = True) -> bytes:
    pin = _material_pin(ref, label, inputs)
    raw = _read_bounded(Path(pin["path"]), MAX_INPUT_PNG_BYTES, label)
    if sha256_bytes(raw) != pin["sha256"] or ref.get("pixels") != pixels:
        raise LogicalAlphaError(f"MATERIAL_PNG_BINDING_MISMATCH: {label}")
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[24] != 8:
        raise LogicalAlphaError(f"MATERIAL_RGBA8_PNG_REQUIRED: {label}")
    with Image.open(io.BytesIO(raw)) as image:
        if (image.format != "PNG" or list(image.size) != pixels or getattr(image, "n_frames", 1) != 1
                or image.mode not in ({"RGBA"} if rgba_required else {"RGB", "RGBA"})
                or image.mode != ref.get("stored_mode")):
            raise LogicalAlphaError(f"MATERIAL_PNG_GEOMETRY_OR_MODE_INVALID: {label}")
        rgba = image.convert("RGBA").tobytes()
    alpha = rgba[3::4]
    if (sha256_bytes(rgba) != ref.get("rgba_sha256")
            or [min(alpha), max(alpha)] != ref.get("alpha_extrema")):
        raise LogicalAlphaError(f"MATERIAL_PNG_PIXELS_MISMATCH: {label}")
    if "alpha_plane_sha256" in ref and (
            sha256_bytes(alpha) != ref["alpha_plane_sha256"]
            or sum(n > 0 for n in alpha) != ref["alpha_positive_pixels"]):
        raise LogicalAlphaError(f"MATERIAL_MASK_ALPHA_MISMATCH: {label}")
    return rgba


def _recheck_material_inputs(batch: dict[str, Any]) -> None:
    for pin in batch.get("material_inputs", {}).get("current_files", []):
        path = _contract_path(pin["path"], "material current input")
        if path.stat().st_size != pin["bytes"] or native.sha256_file(path, 512 * 1024 * 1024) != pin["sha256"]:
            raise LogicalAlphaError("MATERIAL_INPUT_CHANGED_DURING_OPERATION")


def _validate_material_contract(contract: dict, manifest_path: Path, manifest_sha: str,
                                identities: list[str] | None) -> dict[str, Any]:
    names = ("base_manifest", "material_handoff", "source_facts", "source_decision")
    _exact(contract, {"schema", "data_role", *names}, "road material input")
    if contract["data_role"] != MATERIAL_ROLE:
        raise LogicalAlphaError("MATERIAL_DATA_ROLE_UNSUPPORTED")
    inputs: dict[str, dict] = {}
    _material_pin({"path": str(manifest_path), "sha256": manifest_sha}, "material manifest", inputs)
    for name in names:
        _exact(contract[name], {"path", "sha256"}, name)
    refs = {name: _material_pin(contract[name], name, inputs) for name in names}
    base, handoff, facts, decision = (_material_json(refs[name], name, inputs) for name in names)
    if (base.get("schema") != "h3.map-ui.road-material-logical-candidate-input.v1"
            or handoff.get("schema") != "h3.map-ui.road-material-consumer-handoff-proposal.v1"
            or facts.get("schema") != "h3.map-ui.road51-source-native-facts-readback.v1"
            or decision.get("schema") != "h3.map-ui.road51.Root-readonly-data-decision.v1"
            or decision.get("status") != "READONLY_ROAD51_INPUT_FACTS_ACCEPTED_FOR_TOOL_MATERIAL_ROLE_REVIEW"):
        raise LogicalAlphaError("MATERIAL_OWNER_DATA_SCHEMA_UNSUPPORTED")
    if (decision.get("boundaries", {}).get("this_is_not_Map_writer_authority") is not True
            or decision.get("historical_source_data_semantics", {}).get("Tool_independent_lod_verified") is not False):
        raise LogicalAlphaError("MATERIAL_OWNER_DATA_BOUNDARY_INVALID")

    def equal_pin(left: dict, right: dict, label: str) -> None:
        if _material_pin(left, label, inputs) != _material_pin(right, label, inputs):
            raise LogicalAlphaError(f"MATERIAL_REFERENCE_MISMATCH: {label}")

    for left, name in ((decision["fact_readback"], "source_facts"),
                       (decision["base_manifest"], "base_manifest"),
                       (decision["consumer_handoff"], "material_handoff"),
                       (handoff["base_manifest"], "base_manifest"),
                       (facts["immutable_inputs"]["base_manifest"], "base_manifest"),
                       (facts["immutable_inputs"]["consumer_handoff"], "material_handoff")):
        equal_pin(left, refs[name], name)
    current = facts["current_source"]
    for name, filename, decided in (("map", "map_ui_batch_r16.py", "current_source"),
                                     ("town", "art_town_batch_r16.py", "current_native_reader")):
        equal_pin(current[name], decision[decided], name)
        if not (_same_file_path(current[name]["path"], native.h3_root() / "tools" / filename)
                or _same_file_path(current[name]["path"], native.h3_root() / "H3HD/tools" / filename)):
            raise LogicalAlphaError("MATERIAL_CURRENT_SOURCE_PATH_INVALID")

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if {"path", "sha256", "bytes"} <= value.keys():
                _material_pin(value, "current source facts", inputs)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for name in ("current_source", "immutable_inputs", "current_material_inputs", "historical_sampling"):
        collect(facts[name])
    legacy = [row for row in base["provenance"] if row.get("role") == "source-data-contract-r01.json"]
    if len(legacy) != 1:
        raise LogicalAlphaError("MATERIAL_MASK_CONTRACT_REQUIRED")
    refs["mask_contract"] = _material_pin(legacy[0], "frozen mask contract", inputs)
    batch = validate_contract(Path(refs["mask_contract"]["path"]), refs["mask_contract"]["sha256"], identities)
    equal_pin(facts["immutable_inputs"]["native_catalog"], batch["contract"]["source_authority"]["native_catalog"], "native catalog")
    expected_keys = [["def", family, 0, frame] for family in FAMILY_ORDER for frame in FRAMES_PER_FAMILY]
    order = base["source_key_order"]
    _exact(order, {"identities", "frames_per_identity", "keys"}, "material source_key_order")
    if (order.get("identities") != list(FAMILY_ORDER) or order.get("frames_per_identity") != list(FRAMES_PER_FAMILY)
            or json.dumps(order["keys"]) != json.dumps(expected_keys)
            or json.dumps(order["frames_per_identity"]) != json.dumps(list(FRAMES_PER_FAMILY))
            or len(base["frames"]) != 51
            or base["board_layout"].get("grid") != [6, 3]
            or base["board_layout"].get("tile_canvas_3x") != [96, 96]):
        raise LogicalAlphaError("MATERIAL_KEY_ORDER_OR_GRID_INVALID")
    materials = handoff["material_sources"]
    fact_materials = facts["current_material_inputs"]
    if ([row.get("identity") for row in materials] != list(FAMILY_ORDER)
            or [row.get("family") for row in fact_materials] != list(FAMILY_ORDER)):
        raise LogicalAlphaError("MATERIAL_FAMILY_BINDING_INVALID")
    boards = {}
    for material, fact in zip(materials, fact_materials, strict=True):
        family = material["identity"]
        for field, other in (("raw_png", "raw"), ("generation_request", "generation_request"),
                             ("sampled_board_3x_png", "board3x"), ("qualified_sampling_receipt", "frozen_sampling_receipt")):
            equal_pin(material[field], fact[other], family + "." + field)
        _material_image(material["raw_png"], material["raw_png"]["pixels"], family + " raw", inputs, rgba_required=False)
        board = _material_image(material["sampled_board_3x_png"], [576, 288], family + " board", inputs)
        if any(alpha != 255 for alpha in board[3::4]):
            raise LogicalAlphaError("MATERIAL_BOARD_MUST_BE_OPAQUE")
        sampling = _material_json(material["qualified_sampling_receipt"], family + " sampling", inputs)
        entry = sampling.get("contract", {}).get("entry", {})
        if (sampling.get("schema") != "h3.master-expand.receipt.v1" or sampling.get("target_scale") != 3
                or sampling.get("source_sha256") != material["raw_png"]["sha256"]
                or entry.get("source_sha256") != material["raw_png"]["sha256"]
                or sampling.get("source_pixels") != material["raw_png"]["pixels"]
                or sampling.get("png_sha256") != material["sampled_board_3x_png"]["sha256"]
                or sampling.get("output_pixels") != [576, 288]
                or sampling.get("rgba_sha256") != material["sampled_board_3x_png"]["rgba_sha256"]):
            raise LogicalAlphaError("MATERIAL_FROZEN_SAMPLING_BINDING_INVALID")
        boards[family] = (material["sampled_board_3x_png"], board)
    selected = {tuple(frame["key"]): frame for frame in batch["frames"]}
    for index, row in enumerate(base["frames"]):
        key = expected_keys[index]
        old = batch["contract"]["frames"][index]
        geometry = {name: [n * 3 for n in old["native_geometry_1x"][name]] for name in ("canvas", "crop", "origin")}
        cell = [key[3] % 6 * 96, key[3] // 6 * 96, 96, 96]
        for name in ("native_geometry_1x", "geometry_3x"):
            _exact(row[name], {"canvas", "crop", "origin"}, "material " + name)
            for component in ("canvas", "crop", "origin"):
                _pair(row[name][component], "material " + name + "." + component, 0 if component == "origin" else 1)
        if (type(row.get("order_index")) is not int or row["order_index"] != index
                or row.get("key") != key or any(type(n) is not int for n in row["key"][2:])
                or row.get("material_identity") != key[1] or geometry["canvas"] != [96, 96]
                or row.get("native_geometry_1x") != old["native_geometry_1x"] or row.get("geometry_3x") != geometry
                or row.get("native_lod_resource") != old["native_lod_resource"]
                or row.get("native_member_payload_sha256") != old["native_member_payload_sha256"]
                or type(row["material_cell"].get("frame")) is not int or row["material_cell"]["frame"] != key[3]
                or any(type(n) is not int for n in row["material_cell"].get("sampled_board_rect_3x_xywh", []))
                or row["material_cell"].get("sampled_board_rect_3x_xywh") != cell):
            raise LogicalAlphaError("MATERIAL_KEY_GEOMETRY_OR_CELL_INVALID")
        masks = row["logical_masks"]
        one = _material_image(masks["crop_1x"], old["native_geometry_1x"]["crop"], "native mask", inputs)
        crop = _material_image(masks["crop_3x"], geometry["crop"], "crop mask", inputs)
        canvas = _material_image(masks["canvas_3x"], geometry["canvas"], "canvas mask", inputs)
        if sha256_bytes(one[3::4]) != old["input_crop_png"]["alpha_plane_sha256"]:
            raise LogicalAlphaError("MATERIAL_NATIVE_ALPHA_BINDING_INVALID")
        cx, cy = geometry["origin"]
        width, height = geometry["crop"]
        for y in range(96):
            for x in range(96):
                alpha = canvas[(y * 96 + x) * 4 + 3]
                inside = cx <= x < cx + width and cy <= y < cy + height
                if (not inside and alpha) or (inside and alpha != crop[((y - cy) * width + x - cx) * 4 + 3]):
                    raise LogicalAlphaError("MATERIAL_CROP_CANVAS_ALPHA_MISMATCH")
        sw, sh = old["native_geometry_1x"]["crop"]
        if any(one[(y * sw + x) * 4 + 3] and not any(crop[((y * 3 + yy) * width + x * 3 + xx) * 4 + 3]
               for yy in range(3) for xx in range(3)) for y in range(sh) for x in range(sw)):
            raise LogicalAlphaError("MATERIAL_POSITIVE_NATIVE_CELL_LOSS")
        if tuple(key) not in selected:
            continue
        frame = selected[tuple(key)]
        board_ref, board = boards[key[1]]
        result = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                dst = (y * width + x) * 4
                alpha = crop[dst + 3]
                if alpha:
                    src = ((cell[1] + cy + y) * 576 + cell[0] + cx + x) * 4
                    result[dst:dst + 3] = board[src:src + 3]
                result[dst + 3] = alpha
        frame["material"] = {"board": _material_pin(board_ref, "board", inputs),
                             "mask_crop_3x": _material_pin(masks["crop_3x"], "crop mask", inputs),
                             "mask_canvas_3x": _material_pin(masks["canvas_3x"], "canvas mask", inputs),
                             "cell_3x": cell, "output_layout": "native-crop"}
        frame["material_expected_rgba"] = bytes(result)
    batch.update(manifest_path=manifest_path, manifest_sha256=manifest_sha, data_role=MATERIAL_ROLE,
                 material_inputs={**refs, "current_files": [inputs[key] for key in sorted(inputs)]})
    _recheck_material_inputs(batch)
    return batch


def _output_path(outdir: Path, operation_id: str, *, must_exist: bool) -> Path:
    if not isinstance(operation_id, str) or not OPERATION_RE.fullmatch(operation_id):
        raise LogicalAlphaError("OPERATION_ID_INVALID")
    supplied = Path(outdir)
    if any(part in {"..", ""} for part in supplied.parts):
        raise LogicalAlphaError("OUTPUT_PATH_TRAVERSAL_OR_EMPTY_COMPONENT")
    base = Path(os.path.abspath(OUTPUT_BASE))
    requested = Path(os.path.abspath(outdir))
    if _path_key(requested.parent) != _path_key(base) or requested.name != operation_id:
        raise LogicalAlphaError("OUTPUT_MUST_BE_OPERATION_ID_CHILD_OF_IGNORED_LOGICAL_ALPHA_ROOT")
    try:
        native.reject_reparse_components(base, allow_missing_leaf=True)
        native.reject_reparse_components(requested, allow_missing_leaf=not must_exist)
    except native.ReviewError as error:
        raise LogicalAlphaError(str(error)) from error
    staging = base / ('.' + operation_id + '.staging')
    if os.path.lexists(staging):
        native.reject_reparse_components(staging, allow_missing_leaf=False)
        raise LogicalAlphaError('STAGING_PRESENT_NOT_PUBLISHED; inspect preserved operation before new work: ' + str(staging))
    if must_exist:
        if not requested.is_dir() or requested.is_symlink():
            raise LogicalAlphaError("EXISTING_OPERATION_DIRECTORY_REQUIRED")
        return requested
    if os.path.lexists(requested):
        raise LogicalAlphaError("OPERATION_DIRECTORY_ALREADY_EXISTS; inspect or verify before reuse")
    return requested


def _build_binding(renderer: Path, renderer_sha256: str) -> dict[str, Any]:
    renderer = _contract_path(str(renderer), "renderer")
    renderer_sha = _sha(renderer_sha256, "renderer_sha256")
    actual = native.sha256_file(renderer, 1024 * 1024 * 1024)
    if actual != renderer_sha:
        raise LogicalAlphaError("RENDERER_SHA256_MISMATCH")
    try:
        review_binding = native._read_build_receipt(renderer_sha, renderer)
    except Exception as error:
        raise LogicalAlphaError(f"CURRENT_BUILD_RECEIPT_INVALID: {error}") from error
    receipt_path = Path(review_binding["path"])
    raw = _read_bounded(receipt_path, 16 * 1024 * 1024, "build receipt")
    receipt = native.parse_json(raw, "build receipt")
    sources = receipt.get("sources")
    if not isinstance(sources, list):
        raise LogicalAlphaError("BUILD_SOURCE_ROWS_INVALID")
    normalized: dict[str, str] = {}
    for row in sources:
        if not isinstance(row, dict):
            raise LogicalAlphaError("BUILD_SOURCE_ROW_INVALID")
        relative = native._normalize_build_source_path(row.get("path"))
        if relative in normalized:
            raise LogicalAlphaError(f"BUILD_SOURCE_DUPLICATE: {relative}")
        normalized[relative] = _sha(row.get("sha256"), f"build source {relative}")
    if review_binding.get("authority") == "formal_release":
        # C++ pixels are produced inside the pinned formal EXE, not by today's
        # unrelated candidate tree. Bind the actual qualified runtime module.
        if "src/logical_alpha.hpp" not in normalized:
            raise LogicalAlphaError("RELEASE_SOURCE_MISSING: src/logical_alpha.hpp")
        companion = "scripts/logical_alpha.py"
        if normalized.get(companion) != native.sha256_file(native.runtime_source_path(companion), 16 * 1024 * 1024):
            raise LogicalAlphaError("RELEASE_COMPANION_SOURCE_DRIFT: " + companion)
    else:
        header = PRODUCT / "src" / "logical_alpha.hpp"
        current_header_sha = native.sha256_file(header, 16 * 1024 * 1024)
        if normalized.get("src/logical_alpha.hpp") != current_header_sha:
            raise LogicalAlphaError("BUILD_SOURCE_STALE_OR_MISSING: src/logical_alpha.hpp")
    try:
        process = subprocess.run([str(renderer), "capabilities"], shell=False, check=False, capture_output=True,
                                 timeout=30)
    except subprocess.TimeoutExpired as error:
        raise LogicalAlphaError("RENDERER_CAPABILITIES_TIMEOUT") from error
    if process.returncode != 0:
        raise LogicalAlphaError(f"RENDERER_CAPABILITIES_FAILED: {process.stderr[-2000:].decode('utf-8', 'replace')}")
    capabilities = native.parse_json(process.stdout, "renderer capabilities")
    native_commands = capabilities.get("native_commands", [])
    if not any(isinstance(item, dict) and item.get("name") == "logical-alpha-render" for item in native_commands):
        raise LogicalAlphaError("RENDERER_CAPABILITY_MISSING: logical-alpha-render")
    return {"path": str(renderer), "sha256": renderer_sha, "build_receipt": review_binding,
            "capabilities_sha256": sha256_bytes(process.stdout)}


def _native_spec(batch: dict[str, Any], operation_id: str) -> dict[str, Any]:
    result = {"schema": SPEC_SCHEMA, "operation_id": operation_id,
            "data_role": batch.get("data_role", "logical-alpha-mask"), "source_contract": {"path": str(batch["manifest_path"]),
                                                                  "sha256": batch["manifest_sha256"]},
            "official_inventory": {"path": str(batch["inventory_path"]),
                                   "sha256": batch["inventory_sha256"]},
            "source_authority": batch["contract"]["source_authority"],
            "selected_identities": batch["identities"],
            "independent_lod_verified": False,
            "frames": [{"id": item["id"], "key": item["key"], "source_png": str(item["source"]),
                        "source_sha256": item["source_sha256"], "alpha_plane_sha256": item["alpha_sha256"],
                        "alpha_positive_pixels": item["source_positive_pixels"],
                        "source_rgb_encoding": item["source_rgb_encoding"],
                        "source_pixels": item["source_pixels"], "native_geometry_1x": item["geometry_1x"],
                        "geometry_3x": item["geometry_3x"],
                        "native_lod_resource": item["native_lod_resource"],
                        "native_member_payload_sha256": item["native_member_payload_sha256"]}
                       for item in batch["frames"]]}
    if "material_inputs" in batch:
        result["material_inputs"] = batch["material_inputs"]
        for rendered, item in zip(result["frames"], batch["frames"], strict=True):
            rendered["material"] = item["material"]
    return result


def _native_spec_bytes(batch: dict[str, Any], operation_id: str) -> bytes:
    spec = _native_spec(batch, operation_id)
    return (json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _native_spec_sha256(batch: dict[str, Any], operation_id: str) -> str:
    return sha256_bytes(_native_spec_bytes(batch, operation_id))


def _existing_scope(outdir: Path, operation_id: str) -> list[str]:
    output = _output_path(outdir, operation_id, must_exist=True)
    receipt_path = output / COMPLETION_NAME
    try:
        native.reject_reparse_components(receipt_path, allow_missing_leaf=False)
        receipt = native.parse_json(_read_bounded(receipt_path, MAX_CONTRACT_BYTES,
                                                   "logical-alpha completion receipt"),
                                    "logical-alpha completion receipt")
    except Exception as error:
        raise LogicalAlphaError(f"PARTIAL_OUTPUT_OR_INVALID_RECEIPT: {error}") from error
    selected = receipt.get("selected_identities") if isinstance(receipt, dict) else None
    if not isinstance(selected, list) or not selected or len(selected) > len(FAMILY_ORDER):
        raise LogicalAlphaError("PARTIAL_OUTPUT_OR_INVALID_RECEIPT: selected family scope is missing")
    if len(set(selected)) != len(selected) or any(identity not in FAMILY_ORDER for identity in selected):
        raise LogicalAlphaError("PARTIAL_OUTPUT_OR_INVALID_RECEIPT: selected family scope is invalid")
    return [identity for identity in FAMILY_ORDER if identity in set(selected)]


def _load_png(path: Path, expected_sha: str, expected_pixels: list[int], label: str, *,
              expected_rgba: bytes | None = None) -> tuple[bytes, list[int]]:
    raw = _read_bounded(path, MAX_OUTPUT_PNG_BYTES, label)
    if sha256_bytes(raw) != expected_sha:
        raise LogicalAlphaError(f"{label}: output SHA256 mismatch")
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[24] != 8 or raw[25] != 6:
        raise LogicalAlphaError(f"{label}: 8-bit RGBA PNG required")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise LogicalAlphaError(f"{label}: static RGBA PNG required")
            if list(image.size) != expected_pixels:
                raise LogicalAlphaError(f"{label}: dimensions mismatch")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            rgba = image.tobytes()
    except (OSError, ValueError) as error:
        if isinstance(error, LogicalAlphaError):
            raise
        raise LogicalAlphaError(f"{label}: PNG decode failed: {error}") from error
    if expected_rgba is not None and rgba != expected_rgba:
        raise LogicalAlphaError(f"{label}: material RGB/alpha bytes differ from the bound crop")
    if expected_rgba is None and any(rgba[offset] != 255 or rgba[offset + 1] != 255 or rgba[offset + 2] != 255
                                    for offset in range(0, len(rgba), 4)):
        raise LogicalAlphaError(f"{label}: logical-alpha output RGB is not neutral white")
    return raw, list(rgba[3::4])


def _verify_operation(batch: dict[str, Any], renderer: dict[str, Any], outdir: Path,
                      operation_id: str) -> dict[str, Any]:
    material = batch.get("data_role") == MATERIAL_ROLE
    _recheck_material_inputs(batch)
    output = _output_path(outdir, operation_id, must_exist=True)
    receipt_path = output / COMPLETION_NAME
    try:
        native.reject_reparse_components(receipt_path, allow_missing_leaf=False)
        raw_receipt = _read_bounded(receipt_path, MAX_CONTRACT_BYTES, "logical-alpha completion receipt")
        receipt = native.parse_json(raw_receipt, "logical-alpha completion receipt")
    except Exception as error:
        raise LogicalAlphaError(f"PARTIAL_OUTPUT_OR_INVALID_RECEIPT: {error}") from error
    _exact(receipt, {"schema", "created_utc", "status", "operation_id", "source_contract", "official_inventory",
                     "source_authority", "renderer", "algorithm", "data_role", "selected_identities",
                     "native_spec_sha256", "frame_count", "source_png_bytes", "source_alpha_positive_pixels", "source_alpha_cells",
                     "independent_lod_verified", "frames", "candidate_generation_performed", "new_art_created",
                      "art_approved", "runtime_accepted", "game_deployed", "publication"}
           | ({"material_inputs"} if material else set()), "completion receipt")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["status"] != "COMPLETED_REVIEW_REQUIRED":
        raise LogicalAlphaError("COMPLETION_RECEIPT_SCHEMA_OR_STATUS_INVALID")
    publication = receipt['publication']
    _exact(publication, {'mode', 'layout', 'input_rechecked_before_seal', 'start_sha256'}, 'publication')
    if (publication['mode'] != 'same-volume-no-overwrite-directory-rename'
            or publication['layout'] != 'items-v1' or publication['input_rechecked_before_seal'] is not True):
        raise LogicalAlphaError('PUBLICATION_CONTRACT_INVALID')
    start_path = output / 'logical-alpha-start.json'
    native.reject_reparse_components(start_path, allow_missing_leaf=False)
    start_raw = _read_bounded(start_path, MAX_CONTRACT_BYTES, 'operation start')
    if sha256_bytes(start_raw) != _sha(publication['start_sha256'], 'publication.start_sha256'):
        raise LogicalAlphaError('START_RECEIPT_SHA_MISMATCH')
    started = native.parse_json(start_raw, 'operation start')
    expected_start = {'schema': 'h3.logical-alpha.start.v1', 'operation_id': operation_id,
                      'native_spec_sha256': _native_spec_sha256(batch, operation_id),
                      'source_contract_sha256': batch['manifest_sha256'], 'selected_identities': batch['identities'],
                      'renderer_sha256': renderer['sha256'],
                      'build_receipt_sha256': renderer['build_receipt']['sha256']}
    if started != expected_start:
        raise LogicalAlphaError('START_RECEIPT_BINDING_MISMATCH')
    if receipt["operation_id"] != operation_id:
        raise LogicalAlphaError("RECEIPT_OPERATION_ID_MISMATCH")
    _exact(receipt["source_contract"], {"path", "sha256"}, "receipt.source_contract")
    if (receipt["source_contract"]["sha256"] != batch["manifest_sha256"]
            or not _same_file_path(receipt["source_contract"]["path"], batch["manifest_path"])):
        raise LogicalAlphaError("RECEIPT_SOURCE_CONTRACT_MISMATCH")
    _exact(receipt["official_inventory"], {"path", "sha256"}, "receipt.official_inventory")
    if (receipt["official_inventory"]["sha256"] != batch["inventory_sha256"]
            or not _same_file_path(receipt["official_inventory"]["path"], batch["inventory_path"])):
        raise LogicalAlphaError("RECEIPT_OFFICIAL_INVENTORY_MISMATCH")
    _exact(receipt["renderer"], {"path", "sha256", "build_binding"}, "receipt.renderer")
    if receipt["renderer"].get("sha256") != renderer["sha256"]:
        raise LogicalAlphaError("RECEIPT_RENDERER_SHA_MISMATCH")
    if (not _same_file_path(receipt["renderer"]["path"], Path(renderer["path"]))
            or not isinstance(receipt["renderer"]["build_binding"], dict)):
        raise LogicalAlphaError("RECEIPT_RENDERER_BUILD_BINDING_INVALID")
    if receipt["native_spec_sha256"] != _native_spec_sha256(batch, operation_id):
        raise LogicalAlphaError("RECEIPT_NATIVE_SPEC_SHA_MISMATCH")
    if receipt["source_authority"] != batch["contract"]["source_authority"]:
        raise LogicalAlphaError("RECEIPT_SOURCE_AUTHORITY_MISMATCH")
    if receipt["data_role"] != batch.get("data_role", "logical-alpha-mask") or receipt["independent_lod_verified"] is not False:
        raise LogicalAlphaError("RECEIPT_AUTHORITY_BOUNDARY_INVALID")
    if material and receipt["material_inputs"] != batch["material_inputs"]:
        raise LogicalAlphaError("RECEIPT_MATERIAL_INPUT_BINDING_MISMATCH")
    if (receipt["selected_identities"] != batch["identities"] or receipt["frame_count"] != len(batch["frames"])
            or receipt["source_png_bytes"] != batch["source_png_bytes"]
            or receipt["source_alpha_positive_pixels"] != batch["source_alpha_positive_pixels"]
            or receipt["source_alpha_cells"] != batch["source_alpha_cells"]):
        raise LogicalAlphaError("RECEIPT_SOURCE_TOTALS_OR_SCOPE_MISMATCH")
    if (receipt["candidate_generation_performed"] is not material
            or any(receipt[field] is not False for field in ("new_art_created", "art_approved", "runtime_accepted", "game_deployed"))):
        raise LogicalAlphaError("RECEIPT_CLAIM_BOUNDARY_INVALID")
    algorithm = receipt["algorithm"]
    if (not isinstance(algorithm, dict)
            or algorithm.get("filter") != ("none" if material else "h3-quality")
            or algorithm.get("direct_scale") != ("existing-3X-integer-crop" if material else "1X-crop-to-3X-crop")
            or algorithm.get("quality_algorithm") != (None if material else QUALITY_ALGORITHM)
            or (material and type(algorithm.get("resampler_count")) is not int)
            or algorithm["resampler_count"] != (0 if material else 1)):
        raise LogicalAlphaError("RECEIPT_ALGORITHM_BINDING_INVALID")
    frames = receipt["frames"]
    if not isinstance(frames, list) or len(frames) != len(batch["frames"]):
        raise LogicalAlphaError("RECEIPT_FRAME_COUNT_MISMATCH")
    verified_frames = []
    expected_files = {COMPLETION_NAME, 'logical-alpha-start.json'}
    for expected, actual in zip(batch["frames"], frames, strict=True):
        _exact(actual, {"id", "key", "source_png", "source_sha256", "alpha_plane_sha256", "source_pixels",
                        "source_positive_pixels", "source_rgb_encoding", "native_geometry_1x", "geometry_3x", "outputs",
                        "source_positive_cells_covered", "source_positive_cells_missing",
                        "output_alpha_positive_pixels", "output_outside_crop_positive_pixels"}
               | ({"material"} if material else set()),
               f"receipt.frames.{expected['id']}")
        if (actual["id"] != expected["id"] or actual["key"] != expected["key"]
                or not _same_file_path(actual["source_png"], expected["source"])
                or actual["source_sha256"] != expected["source_sha256"]
                or actual["alpha_plane_sha256"] != expected["alpha_sha256"]
                or actual["source_pixels"] != expected["source_pixels"]
                or actual["source_positive_pixels"] != expected["source_positive_pixels"]
                or actual["source_rgb_encoding"] != expected["source_rgb_encoding"]
                or actual["native_geometry_1x"] != expected["geometry_1x"]
                or actual["geometry_3x"] != expected["geometry_3x"]):
            raise LogicalAlphaError(f"RECEIPT_FRAME_BINDING_MISMATCH: {expected['id']}")
        if material and actual["material"] != expected["material"]:
            raise LogicalAlphaError(f"RECEIPT_MATERIAL_FRAME_BINDING_MISMATCH: {expected['id']}")
        outputs = actual["outputs"]
        _exact(outputs, {"crop_3x"} if material else {"crop_3x", "canvas_3x"}, f"receipt.frames.{expected['id']}.outputs")
        expected_outputs = {"crop_3x": (f"items/{expected['id']}/crop-3x.png", expected["geometry_3x"]["crop"])}
        if not material:
            expected_outputs["canvas_3x"] = (f"items/{expected['id']}/canvas-3x.png", expected["geometry_3x"]["canvas"])
        decoded_outputs: dict[str, list[int]] = {}
        for name, (relative, dims) in expected_outputs.items():
            output_info = outputs[name]
            _exact(output_info, {"path", "pixels", "bytes", "sha256"}
                   | ({"layout", "canvas_pixels", "origin_pixels"} if material else set()), f"{expected['id']}.{name}")
            if output_info["path"] != relative or output_info["pixels"] != dims:
                raise LogicalAlphaError(f"RECEIPT_OUTPUT_GEOMETRY_MISMATCH: {expected['id']}.{name}")
            if material and (output_info["layout"] != "native-crop"
                             or output_info["canvas_pixels"] != expected["geometry_3x"]["canvas"]
                             or output_info["origin_pixels"] != expected["geometry_3x"]["origin"]):
                raise LogicalAlphaError(f"RECEIPT_VIRTUAL_CANVAS_MISMATCH: {expected['id']}")
            expected_files.add(relative)
            path = output / Path(*relative.split("/"))
            native.reject_reparse_components(path, allow_missing_leaf=False)
            png_raw, alpha = _load_png(path, _sha(output_info["sha256"], f"{expected['id']}.{name}.sha256"), dims,
                                       f"output {expected['id']}.{name}",
                                       expected_rgba=expected["material_expected_rgba"] if material else None)
            if len(png_raw) != output_info["bytes"]:
                raise LogicalAlphaError(f"RECEIPT_OUTPUT_SIZE_MISMATCH: {expected['id']}.{name}")
            decoded_outputs[name] = alpha
        crop = decoded_outputs["crop_3x"]
        cw, ch = expected["geometry_3x"]["crop"]
        source_alpha = expected["source_alpha"]
        sw, sh = expected["source_pixels"]
        covered = 0
        for y in range(sh):
            for x in range(sw):
                if source_alpha[y * sw + x] == 0:
                    continue
                if any(crop[yy * cw + xx] > 0 for yy in range(y * 3, y * 3 + 3)
                       for xx in range(x * 3, x * 3 + 3)):
                    covered += 1
        if covered != expected["source_positive_pixels"]:
            raise LogicalAlphaError(f"OUTPUT_POSITIVE_SOURCE_CELL_LOSS: {expected['id']}")
        canvas_w, canvas_h = expected["geometry_3x"]["canvas"]
        origin_x, origin_y = expected["geometry_3x"]["origin"]
        if material:
            canvas = [0] * (canvas_w * canvas_h)
            for y in range(ch):
                offset = (origin_y + y) * canvas_w + origin_x
                canvas[offset:offset + cw] = crop[y * cw:(y + 1) * cw]
        else:
            canvas = decoded_outputs["canvas_3x"]
        outside_positive = 0
        for y in range(canvas_h):
            for x in range(canvas_w):
                alpha_value = canvas[y * canvas_w + x]
                inside = origin_x <= x < origin_x + cw and origin_y <= y < origin_y + ch
                if not inside and alpha_value > 0:
                    outside_positive += 1
                if inside and alpha_value != crop[(y - origin_y) * cw + (x - origin_x)]:
                    raise LogicalAlphaError(f"OUTPUT_CANVAS_CROP_DIFFERS: {expected['id']}")
        if outside_positive:
            raise LogicalAlphaError(f"OUTPUT_OUTSIDE_CROP_ALPHA_NONZERO: {expected['id']}")
        if (actual["source_positive_cells_covered"] != covered
                or actual["source_positive_cells_missing"] != 0
                or actual["output_outside_crop_positive_pixels"] != 0
                or actual["output_alpha_positive_pixels"] != sum(value > 0 for value in crop)):
            raise LogicalAlphaError(f"RECEIPT_COVERAGE_MEASUREMENT_MISMATCH: {expected['id']}")
        verified_frames.append({"id": expected["id"], "key": expected["key"],
                                "output_alpha_positive_pixels": sum(value > 0 for value in crop),
                                "source_positive_cells_covered": covered})
    actual_files = set()
    for path in output.rglob('*'):
        native.reject_reparse_components(path, allow_missing_leaf=False)
        if path.is_file():
            actual_files.add(path.relative_to(output).as_posix())
    if actual_files != expected_files:
        raise LogicalAlphaError('OUTPUT_TREE_UNRECEIPTED_OR_INCOMPLETE')
    _recheck_material_inputs(batch)
    return {"status": "VERIFIED_REVIEW_REQUIRED", "operation_id": operation_id,
            "outdir": str(output), "receipt": str(receipt_path), "receipt_sha256": sha256_bytes(raw_receipt),
            "frame_count": len(verified_frames), "selected_identities": batch["identities"],
            "independent_lod_verified": False, "art_approved": False, "runtime_accepted": False,
            "game_deployed": False, "frames": verified_frames}


def check(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str,
          identities: list[str] | None, renderer_path: Path, renderer_sha256: str) -> dict[str, Any]:
    output = _output_path(outdir, operation_id, must_exist=False)
    batch = validate_contract(manifest_path, manifest_sha256, identities)
    for frame in batch["frames"]:
        if native._overlap(frame["source"], output) or native._overlap(batch["inventory_path"], output):
            raise LogicalAlphaError("INPUT_OUTPUT_OVERLAP")
    renderer = _build_binding(renderer_path, renderer_sha256)
    return {"status": "CHECKED", "operation_id": operation_id, "manifest_sha256": batch["manifest_sha256"],
            "official_inventory_sha256": batch["inventory_sha256"], "renderer_sha256": renderer["sha256"],
            "selected_identities": batch["identities"], "frame_count": len(batch["frames"]),
            "source_png_bytes": batch["source_png_bytes"],
            "source_alpha_positive_pixels": batch["source_alpha_positive_pixels"],
            "source_alpha_cells": batch["source_alpha_cells"], "outdir": str(output),
            "independent_lod_verified": False, "output_written": False, "new_art_created": False,
            "art_approved": False, "runtime_accepted": False, "game_deployed": False}


def run(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str,
        identities: list[str] | None, renderer_path: Path, renderer_sha256: str) -> dict[str, Any]:
    already_exists = os.path.lexists(Path(os.path.abspath(outdir)))
    if already_exists and identities is None:
        identities = _existing_scope(outdir, operation_id)
    batch = validate_contract(manifest_path, manifest_sha256, identities)
    renderer = _build_binding(renderer_path, renderer_sha256)
    requested = _output_path(outdir, operation_id, must_exist=already_exists)
    if already_exists:
        reused = _verify_operation(batch, renderer, requested, operation_id)
        reused["status"] = "REUSED_VERIFIED"
        return reused
    for frame in batch["frames"]:
        if native._overlap(frame["source"], requested) or native._overlap(batch["inventory_path"], requested):
            raise LogicalAlphaError("INPUT_OUTPUT_OVERLAP")
    spec_raw = _native_spec_bytes(batch, operation_id)
    spec_sha = sha256_bytes(spec_raw)
    with tempfile.TemporaryDirectory(prefix="h3-logical-alpha-spec-") as temporary:
        spec_path = Path(temporary) / "render-spec.json"
        spec_path.write_bytes(spec_raw)
        args = [renderer["path"], "logical-alpha-render", "--spec", str(spec_path), "--spec-sha256", spec_sha,
                "--outdir", str(requested), "--operation-id", operation_id]
        try:
            process = subprocess.run(args, shell=False, check=False, capture_output=True, timeout=3500)
        except subprocess.TimeoutExpired as error:
            raise LogicalAlphaError("NATIVE_RENDER_TIMED_OUT; inspect operation directory before retry") from error
    if process.returncode != 0:
        detail = process.stderr[-4000:].decode("utf-8", "replace")
        raise LogicalAlphaError(f"NATIVE_RENDER_FAILED exit={process.returncode}: {detail}")
    try:
        native_result = native.parse_json(process.stdout, "native logical-alpha result")
    except native.ReviewError as error:
        raise LogicalAlphaError(f"NATIVE_RENDER_RESULT_INVALID: {error}") from error
    if (not isinstance(native_result, dict) or native_result.get("status") != "COMPLETED_REVIEW_REQUIRED"
            or native_result.get("operation_id") != operation_id or native_result.get("independent_lod_verified") is not False):
        raise LogicalAlphaError("NATIVE_RENDER_COMPLETION_POINTER_INVALID")
    verified = _verify_operation(batch, renderer, requested, operation_id)
    if native_result.get("receipt_sha256") != verified["receipt_sha256"]:
        raise LogicalAlphaError("NATIVE_RENDER_RECEIPT_SHA_MISMATCH")
    verified["status"] = "COMPLETED_REVIEW_REQUIRED"
    verified["native_spec_sha256"] = spec_sha
    verified["renderer_sha256"] = renderer["sha256"]
    return verified


def verify(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str,
           identities: list[str] | None, renderer_path: Path, renderer_sha256: str) -> dict[str, Any]:
    if identities is None:
        identities = _existing_scope(outdir, operation_id)
    batch = validate_contract(manifest_path, manifest_sha256, identities)
    renderer = _build_binding(renderer_path, renderer_sha256)
    return _verify_operation(batch, renderer, outdir, operation_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "run", "verify"):
        command = subcommands.add_parser(name, allow_abbrev=False)
        command.add_argument("--manifest", required=True, type=Path)
        command.add_argument("--manifest-sha256", required=True)
        command.add_argument("--outdir", required=True, type=Path)
        command.add_argument("--operation-id", required=True)
        command.add_argument("--identity", action="append", dest="identities",
                             help="Select one explicit producer family; repeat for multiple families. Default: all manifest families.")
        command.add_argument("--renderer", required=True, type=Path,
                             help=argparse.SUPPRESS)
        command.add_argument("--renderer-sha256", required=True,
                             help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            result = check(args.manifest, args.manifest_sha256, args.outdir, args.operation_id,
                           args.identities, args.renderer, args.renderer_sha256)
        elif args.command == "run":
            result = run(args.manifest, args.manifest_sha256, args.outdir, args.operation_id,
                         args.identities, args.renderer, args.renderer_sha256)
        else:
            result = verify(args.manifest, args.manifest_sha256, args.outdir, args.operation_id,
                            args.identities, args.renderer, args.renderer_sha256)
    except Exception as error:
        failure = {"schema": "h3.logical-alpha.error.v1", "status": "FAILED", "error": str(error),
                   "operation_id": args.operation_id, "independent_lod_verified": False,
                   "art_approved": False, "game_deployed": False}
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
