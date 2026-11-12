"""Preflight, deliver, and verify a frozen native creature action batch.

This companion consumes a SHA-bound creature_action_plan and per-slot RGBA
PNG bindings. It never renders, resamples, mirrors, interpolates, edits pixels,
quantizes a DEF, or approves artwork. A complete receipt means byte-preserving
PNG delivery completed; it does not close unknown timing, art, or game review.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
from dataclasses import dataclass, field
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import stat
from typing import Any

import numpy as np
from PIL import Image

import creature_action_plan
from review_native_batch import OutputRootGuard, reject_reparse_components
from review_motion_sequence import inspect_frame


PRODUCT = resolve_tool_root(__file__)
OUTPUT_BASE = PRODUCT / "build" / "experiments" / "creature-production"
MANIFEST_SCHEMA = "h3.creature.production-manifest.v1"
CHECK_SCHEMA = "h3.creature.production-check.v1"
RECEIPT_SCHEMA = "h3.creature.production-receipt.v1"
COMPLETE_SCHEMA = "h3.creature.production-complete.v1"
ERROR_SCHEMA = "h3.creature.production-error.v1"
DENSITY_SCHEMA = "h3.creature.production-density-report.v1"
DENSITY_ALPHA_THRESHOLD = 128
DENSITY_MASTER_SCALE = 3
OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", re.ASCII)
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$", re.ASCII)
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_PNG_BYTES = 256 * 1024 * 1024
MAX_PNG_PIXELS = 16_000_000

AUTHORITY_FALSE = {
    "model_inference_performed": False,
    "model_native_output": False,
    "human_approved": False,
    "art_approved": False,
    "native_timing_verified": False,
    "game_deployed": False,
    "runtime_accepted": False,
}


class ProductionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Context:
    operation_id: str
    outdir: Path
    manifest_path: Path
    manifest_raw: bytes
    manifest: dict[str, Any]
    manifest_sha256: str
    plan_path: Path
    plan_raw: bytes
    plan: dict[str, Any]
    plan_sha256: str
    inventory_path: Path
    inventory_sha256: str
    expected_slots: list[dict[str, Any]]
    expected_by_key: dict[tuple[str, str, int, int], dict[str, Any]]
    fingerprint: str
    action_fingerprints: dict[str, str] = field(default_factory=dict)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def parse_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProductionError("JSON_INVALID", f"{label}: {error}") from error


def _exact_fields(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProductionError("SCHEMA_OBJECT_REQUIRED", f"{label} must be an object")
    missing = required - value.keys()
    extra = value.keys() - required
    if missing or extra:
        raise ProductionError("SCHEMA_FIELDS_INVALID",
                              f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    return value


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise ProductionError("INPUT_UNREADABLE", f"{label}: {error}") from error
    if not raw or len(raw) > limit:
        raise ProductionError("INPUT_SIZE_INVALID", f"{label} is empty or exceeds {limit} bytes")
    return raw


def _absolute_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ProductionError("PATH_INVALID", f"{label} must be an absolute file path")
    raw = Path(value)
    if not raw.is_absolute():
        raise ProductionError("PATH_INVALID", f"{label} must be absolute")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ProductionError("INPUT_UNAVAILABLE", f"{label}: {error}") from error
    if not resolved.is_file():
        raise ProductionError("INPUT_NOT_FILE", f"{label} is not a regular file")
    return resolved


def _file_binding(value: Any, label: str) -> tuple[Path, str]:
    binding = _exact_fields(value, {"path", "sha256"}, label)
    expected = binding["sha256"]
    if not isinstance(expected, str) or not SHA_RE.fullmatch(expected):
        raise ProductionError("SHA_INVALID", f"{label}.sha256 must be 64 hexadecimal characters")
    return _absolute_file(binding["path"], f"{label}.path"), expected.lower()


def _verify_file_binding(value: Any, label: str, *, limit: int) -> tuple[Path, str, bytes]:
    path, expected = _file_binding(value, label)
    raw = _read_bounded(path, limit, label)
    actual = sha256_bytes(raw)
    if actual != expected:
        raise ProductionError("SHA_MISMATCH", f"{label} bytes do not match its pinned SHA256")
    return path, actual, raw


def _slot_key(value: Any, label: str) -> tuple[str, str, int, int]:
    if (not isinstance(value, list) or len(value) != 4 or value[0] != "def"
            or not isinstance(value[1], str) or not value[1]
            or type(value[2]) is not int or type(value[3]) is not int
            or not 0 <= value[2] <= 21 or value[3] < 0):
        raise ProductionError("SLOT_KEY_INVALID", f"{label} must be [\"def\", identity, group 0..21, frame]")
    try:
        identity = creature_action_plan.safe_identity(value[1])
    except creature_action_plan.PlanError as error:
        raise ProductionError("SLOT_KEY_INVALID", f"{label}: {error}") from error
    return "def", identity, value[2], value[3]


def _slot_key_json(key: tuple[str, str, int, int]) -> list[Any]:
    return [key[0], key[1], key[2], key[3]]


def _sha_text(path: Path, limit: int, label: str) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ProductionError("INPUT_SIZE_INVALID", f"{label} exceeds {limit} bytes")
                digest.update(chunk)
    except OSError as error:
        raise ProductionError("INPUT_UNREADABLE", f"{label}: {error}") from error
    if total == 0:
        raise ProductionError("INPUT_SIZE_INVALID", f"{label} is empty")
    return digest.hexdigest()


def _cached_binding(value: Any, label: str, cache: dict[Path, str]) -> dict[str, str]:
    path, expected = _file_binding(value, label)
    try:
        reject_reparse_components(Path(value["path"]), allow_missing_leaf=False)
    except Exception as error:
        raise ProductionError("PROVENANCE_PATH_UNSAFE", f"{label}: {error}") from error
    actual = cache.get(path)
    if actual is None:
        actual = _sha_text(path, MAX_EVIDENCE_BYTES, label)
        cache[path] = actual
    if actual != expected:
        raise ProductionError("SHA_MISMATCH", f"{label} bytes do not match its pinned SHA256")
    return {"path": str(path), "sha256": actual}


def _path_identity(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProductionError("PATH_INVALID", f"{label} must be an absolute path")
    raw = Path(value)
    if not raw.is_absolute():
        raise ProductionError("PATH_INVALID", f"{label} must be absolute")
    try:
        return raw.resolve(strict=False)
    except OSError as error:
        raise ProductionError("PATH_INVALID", f"{label}: {error}") from error


def _load_context(manifest_arg: str, expected_manifest_sha: str,
                  operation_id: str, outdir_arg: str) -> Context:
    if not OPERATION_RE.fullmatch(operation_id):
        raise ProductionError("OPERATION_ID_INVALID", "operation-id must be 1..64 safe ASCII letters, digits, _ or -")
    if not isinstance(expected_manifest_sha, str) or not SHA_RE.fullmatch(expected_manifest_sha):
        raise ProductionError("MANIFEST_SHA_INVALID", "manifest-sha256 must be 64 hexadecimal characters")
    manifest_path = _absolute_file(manifest_arg, "manifest")
    manifest_raw = _read_bounded(manifest_path, MAX_MANIFEST_BYTES, "manifest")
    manifest_sha = sha256_bytes(manifest_raw)
    if manifest_sha.lower() != expected_manifest_sha.lower():
        raise ProductionError("MANIFEST_SHA_MISMATCH", "manifest bytes do not match --manifest-sha256")
    manifest = parse_json(manifest_raw, "manifest")
    _exact_fields(manifest, {"schema", "operation_id", "action_plan", "official_inventory", "slots"}, "manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ProductionError("MANIFEST_SCHEMA_UNSUPPORTED", f"schema must be {MANIFEST_SCHEMA}")
    if manifest.get("operation_id") != operation_id:
        raise ProductionError("OPERATION_ID_MISMATCH", "manifest operation_id differs from CLI --operation-id")

    plan_path, plan_sha, plan_raw = _verify_file_binding(manifest["action_plan"], "action_plan", limit=MAX_PLAN_BYTES)
    plan = parse_json(plan_raw, "action plan")
    if not isinstance(plan, dict) or plan.get("schema") != creature_action_plan.OUTPUT_SCHEMA:
        raise ProductionError("ACTION_PLAN_SCHEMA_UNSUPPORTED", "action_plan must be a creature_action_plan output")
    inventory_binding = _exact_fields(manifest["official_inventory"], {"path", "sha256"}, "official_inventory")
    if not isinstance(inventory_binding["sha256"], str) or not SHA_RE.fullmatch(inventory_binding["sha256"]):
        raise ProductionError("INVENTORY_SHA_INVALID", "official_inventory.sha256 must be 64 hexadecimal characters")
    inventory_path = _absolute_file(inventory_binding["path"], "official_inventory.path")
    inventory_sha = inventory_binding["sha256"].lower()
    plan_inventory = plan.get("inventory")
    if not isinstance(plan_inventory, dict):
        raise ProductionError("ACTION_PLAN_INVALID", "action plan inventory binding is missing")
    try:
        plan_inventory_path = _absolute_file(plan_inventory["path"], "action_plan.inventory.path")
    except (KeyError, OSError, TypeError) as error:
        raise ProductionError("ACTION_PLAN_INVALID", "action plan inventory path is unavailable") from error
    if (plan_inventory_path != inventory_path or plan_inventory.get("sha256", "").lower() != inventory_sha
            or plan_inventory.get("schema") != creature_action_plan.INVENTORY_SCHEMA):
        raise ProductionError("INVENTORY_BINDING_MISMATCH", "manifest inventory differs from the frozen action plan")

    units = plan.get("units")
    if not isinstance(units, list) or not units:
        raise ProductionError("ACTION_PLAN_INVALID", "action plan has no units")
    identities: list[str] = []
    for unit in units:
        if not isinstance(unit, dict) or not isinstance(unit.get("identity"), str):
            raise ProductionError("ACTION_PLAN_INVALID", "action plan unit identity is invalid")
        identities.append(creature_action_plan.safe_identity(unit["identity"]))
    document, actual_inventory_sha = creature_action_plan.load_catalog(inventory_path, inventory_sha)
    reconstructed = creature_action_plan.build_plan(document, actual_inventory_sha, identities, inventory_path)
    if plan != reconstructed:
        raise ProductionError("ACTION_PLAN_DRIFT", "action plan is not the exact current projection of its pinned inventory")

    expected_slots: list[dict[str, Any]] = []
    expected_by_key: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for unit in reconstructed["units"]:
        for group in unit["action_groups"]:
            for slot in group["slots"]:
                key_obj = slot["key"]
                key = ("def", key_obj["identity"], key_obj["group"], key_obj["frame"])
                expected_slots.append(slot)
                expected_by_key[key] = slot
    if not expected_slots:
        raise ProductionError("ACTION_PLAN_EMPTY", "action plan contains no actual native slots")

    raw_outdir = Path(outdir_arg)
    if not raw_outdir.is_absolute():
        raise ProductionError("OUTPUT_ROOT_OUTSIDE_DEVELOPMENT_ROOT", "outdir must be absolute")
    output_path = Path(os.path.abspath(raw_outdir))
    expected_outdir = Path(os.path.abspath(OUTPUT_BASE / operation_id))
    if os.path.normcase(os.path.normpath(str(output_path))) != os.path.normcase(os.path.normpath(str(expected_outdir))):
        raise ProductionError("OUTPUT_ROOT_OUTSIDE_DEVELOPMENT_ROOT",
                              f"outdir must be exactly {expected_outdir}")
    try:
        # Check the caller's lexical path before resolving it. A junction at
        # the expected operation path must never redirect verify outside build/.
        reject_reparse_components(output_path, allow_missing_leaf=True)
        resolved_output = output_path.resolve(strict=False)
        resolved_output.relative_to(PRODUCT.resolve(strict=True))
    except Exception as error:
        raise ProductionError("OUTPUT_ROOT_PATH_UNSAFE", f"outdir has a reparse component or escapes product root: {error}") from error
    canonical_plan_sha = sha256_bytes(plan_raw)
    fingerprint_material = {
        "schema": "h3.creature.production.identity.v1",
        "operation_id": operation_id,
        "manifest_sha256": manifest_sha,
        "action_plan_sha256": canonical_plan_sha,
        "inventory_sha256": inventory_sha,
    }
    fingerprint = sha256_bytes(json.dumps(fingerprint_material, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False).encode("utf-8"))
    return Context(operation_id, output_path, manifest_path, manifest_raw, manifest, manifest_sha,
                   plan_path, plan_raw, reconstructed, canonical_plan_sha, inventory_path, inventory_sha,
                   expected_slots, expected_by_key, fingerprint)


def _geometry(value: Any, expected: dict[str, Any], label: str) -> dict[str, list[int]]:
    geometry = _exact_fields(value, {"canvas_1x", "crop_1x", "origin_1x"}, label)
    result: dict[str, list[int]] = {}
    for name in ("canvas_1x", "crop_1x", "origin_1x"):
        pair = geometry[name]
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(part) is not int for part in pair)):
            raise ProductionError("GEOMETRY_INVALID", f"{label}.{name} must be a two-integer array")
        result[name] = list(pair)
    if (result["canvas_1x"] != expected["canvas"] or result["crop_1x"] != expected["crop"]
            or result["origin_1x"] != expected["anchor"]):
        raise ProductionError("NATIVE_GEOMETRY_MISMATCH", f"{label} differs from its native action-plan slot")
    return result


def _check_metadata(value: Any, label: str, valid_value) -> tuple[Any, bool, list[str]]:
    field = _exact_fields(value, {"value", "provenance"}, label)
    value = field["value"]
    if not valid_value(value):
        raise ProductionError("PROVENANCE_VALUE_INVALID", f"{label}.value is invalid; use explicit \"unknown\" if unresolved")
    unknown = value == "unknown"
    if unknown and field["provenance"] is not None:
        raise ProductionError("PROVENANCE_INVALID", f"{label} must use null provenance when value is unknown")
    if not unknown and field["provenance"] is None:
        raise ProductionError("PROVENANCE_MISSING", f"{label} known value requires a SHA-bound provenance file")
    refs: list[str] = []
    if not unknown:
        refs.append(label)
    return value, unknown, refs


def _known_direction(value: Any) -> bool:
    return value == "unknown" or (isinstance(value, str) and 0 < len(value) <= 32 and value.strip() == value)


def _point_value(value: Any) -> bool:
    return value == "unknown" or (isinstance(value, list) and len(value) == 2
                                    and all(type(part) is int for part in value))


def _duration_value(value: Any) -> bool:
    return value == "unknown" or (type(value) is int and value > 0)


def _loop_value(value: Any) -> bool:
    return value == "unknown" or type(value) is bool


def _validate_png(raw: bytes, expected_pixels: tuple[int, int], label: str) -> tuple[str, tuple[int, int]]:
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise ProductionError("PNG_RGBA_REQUIRED", f"{label} must be a single-frame RGBA PNG")
            if image.width * image.height > MAX_PNG_PIXELS:
                raise ProductionError("PNG_PIXEL_LIMIT", f"{label} exceeds {MAX_PNG_PIXELS} decoded pixels")
            if image.size != expected_pixels:
                raise ProductionError("PNG_GEOMETRY_MISMATCH",
                                      f"{label} is {image.width}x{image.height}, expected {expected_pixels[0]}x{expected_pixels[1]}")
            image.load()
            rgba = image.tobytes()
            return sha256_bytes(rgba), image.size
    except ProductionError:
        raise
    except Exception as error:
        raise ProductionError("PNG_INVALID", f"{label}: {error}") from error


def _alpha_bbox(mask: np.ndarray) -> list[int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    columns = np.flatnonzero(mask.any(axis=0))
    return [int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1]


def _density_measurement(entry: Any, expected: dict[str, Any], index: int) -> dict[str, Any]:
    """Read one bound source and report its body size against the fixed 3X floor.

    This deliberately uses an alpha>=128 envelope for the body measurement. It
    never changes the producer's partition floor, source bytes, or any
    check/run/verify decision; the report is diagnostic evidence only.
    """
    label = f"slots[{index}]"
    key = _slot_key(entry.get("key") if isinstance(entry, dict) else None, f"{label}.key")
    if key != ("def", expected["key"]["identity"], expected["key"]["group"], expected["key"]["frame"]):
        raise ProductionError("SLOT_KEY_MISMATCH", f"{label} key is not in the frozen action plan")
    geometry = _geometry(entry["geometry"], expected, f"{label}.geometry")
    source_value = entry["source"]
    is_action = isinstance(source_value, dict) and source_value.get("kind") == "blender-action-output"
    source = _exact_fields(source_value, {"kind", "path", "sha256", "native_scale", "receipt"}
                           | ({"layout"} if is_action else set()), f"{label}.source")
    if source["kind"] not in {"motion-output", "original-reference", "blender-action-output"}:
        raise ProductionError("SOURCE_KIND_INVALID", f"{label}.source.kind is unsupported")
    if is_action and source["layout"] != "native-canvas":
        raise ProductionError("SOURCE_LAYOUT_INVALID", f"{label} Action output must retain native-canvas layout")
    scale = source["native_scale"]
    if type(scale) is not int or scale not in {1, 3}:
        raise ProductionError("SOURCE_SCALE_INVALID", f"{label}.source.native_scale must be 1 or 3")
    source_path, source_sha = _absolute_file(source["path"], f"{label}.source.path"), source["sha256"]
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise ProductionError("SOURCE_SHA_INVALID", f"{label}.source.sha256 is invalid")
    source_sha = source_sha.lower()
    raw = _read_bounded(source_path, MAX_PNG_BYTES, f"{label} source PNG")
    if sha256_bytes(raw) != source_sha:
        raise ProductionError("SOURCE_SHA_MISMATCH", f"{label} source PNG differs from its pinned SHA256")
    expected_pixels = (geometry["canvas_1x"][0] * scale, geometry["canvas_1x"][1] * scale) if is_action else (
        geometry["crop_1x"][0] * scale, geometry["crop_1x"][1] * scale)
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise ProductionError("PNG_RGBA_REQUIRED", f"{label} source PNG must be a single-frame RGBA PNG")
            if image.width * image.height > MAX_PNG_PIXELS:
                raise ProductionError("PNG_PIXEL_LIMIT", f"{label} source PNG exceeds {MAX_PNG_PIXELS} decoded pixels")
            if image.size != expected_pixels:
                raise ProductionError("PNG_GEOMETRY_MISMATCH",
                                      f"{label} source PNG is {image.width}x{image.height}, expected {expected_pixels[0]}x{expected_pixels[1]}")
            image.load()
            alpha = np.asarray(image.getchannel("A"), dtype=np.uint8).copy()
    except ProductionError:
        raise
    except Exception as error:
        raise ProductionError("PNG_INVALID", f"{label} source PNG: {error}") from error

    if is_action:
        region = [0, 0, expected_pixels[0], expected_pixels[1]]
        region_kind = "native-canvas"
    else:
        region = [0, 0, expected_pixels[0], expected_pixels[1]]
        region_kind = "native-crop"
    body = alpha >= DENSITY_ALPHA_THRESHOLD
    x0, y0, x1, y1 = region
    body_box = _alpha_bbox(body[y0:y1, x0:x1])
    if body_box is not None:
        body_box = [body_box[0] + x0, body_box[1] + y0, body_box[2] + x0, body_box[3] + y0]
    crop = geometry["crop_1x"]
    minimum = [crop[0] * DENSITY_MASTER_SCALE, crop[1] * DENSITY_MASTER_SCALE]
    body_size = [body_box[2] - body_box[0], body_box[3] - body_box[1]] if body_box else [0, 0]
    fill = [round(body_size[0] / minimum[0], 6) if minimum[0] else None,
            round(body_size[1] / minimum[1], 6) if minimum[1] else None]
    k_ratio = max(body_size[0] / crop[0], body_size[1] / crop[1]) if body_box else None
    hints = []
    if body_box is None:
        hints.append("NO_ALPHA128_BODY")
    elif fill[0] < 1 or fill[1] < 1:
        hints.append("BELOW_MASTER_EFFECTIVE_MINIMUM_REVIEW_REQUIRED")
    return {
        "key": [key[0], key[1], key[2], key[3]],
        "status": "MEASURED",
        "source": {"kind": source["kind"], "path": str(source_path), "sha256": source_sha,
                    "native_scale": scale, "pixels": list(expected_pixels)},
        "geometry": geometry,
        "measurement_region": {"kind": region_kind, "bbox_ltrb": region},
        "alpha_threshold": DENSITY_ALPHA_THRESHOLD,
        "body_bbox_px": body_box,
        "body_size_px": body_size,
        "master_effective_minimum_px": minimum,
        "fill_ratio_width": fill[0],
        "fill_ratio_height": fill[1],
        "suggested_common_k": math.ceil(k_ratio) if k_ratio is not None else None,
        "suggested_k_basis": "ceil(alpha>=128 body bbox / native crop dimensions)",
        "positive_alpha_pixels": int(np.count_nonzero(alpha > 0)),
        "alpha128_pixels": int(np.count_nonzero(body)),
        "review_hints": hints,
        "source_pixels_mutated": False,
        "images_written": 0,
    }


def report_density(context: Context) -> dict[str, Any]:
    """Produce a bounded, read-only per-slot density report."""
    entries = context.manifest.get("slots")
    if not isinstance(entries, list) or len(entries) > 10_000:
        raise ProductionError("SLOT_COUNT_INVALID", "manifest slots must be an array with at most 10000 entries")
    expected_by_key = context.expected_by_key
    frames: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for index, entry in enumerate(entries):
        try:
            key = _slot_key(entry.get("key") if isinstance(entry, dict) else None, f"slots[{index}].key")
            expected = expected_by_key.get(key)
            if expected is None:
                raise ProductionError("UNPLANNED_SLOT", f"slots[{index}] is not in the frozen action plan")
            if key in seen:
                raise ProductionError("DUPLICATE_SLOT", f"slots[{index}] repeats a native slot")
            seen.add(key)
            frames.append(_density_measurement(entry, expected, index))
        except (ProductionError, OSError, TypeError, KeyError, ValueError) as error:
            errors.append({"index": index, "status": "BLOCKED", "error": f"{getattr(error, 'code', 'DENSITY_MEASUREMENT_FAILED')}: {error}"})
    missing = [
        ["def", slot["key"]["identity"], slot["key"]["group"], slot["key"]["frame"]]
        for slot in context.expected_slots
        if ("def", slot["key"]["identity"], slot["key"]["group"], slot["key"]["frame"]) not in seen
    ]
    return {
        "schema": DENSITY_SCHEMA,
        "status": "REPORT_ONLY",
        "operation_id": context.operation_id,
        "manifest_sha256": context.manifest_sha256,
        "action_plan_sha256": context.plan_sha256,
        "official_inventory": {"path": str(context.inventory_path), "sha256": context.inventory_sha256},
        "measurement": {
            "alpha_threshold": DENSITY_ALPHA_THRESHOLD,
            "master_effective_minimum_scale": DENSITY_MASTER_SCALE,
            "master_effective_minimum_formula": "crop_1x * 3",
            "suggested_k_formula": "ceil(alpha>=128 body bbox / native crop dimensions)",
            "failure_semantics": "diagnostic-only; no density gate, alpha cleanup, producer/source writer, art approval, native timing or game approval",
        },
        "expected_slots": len(context.expected_slots),
        "measured_slots": len(frames),
        "blocked_slots": len(errors),
        "missing_slots": missing,
        "frames": frames,
        "errors": errors,
        "images_written": 0,
        "source_pixels_mutated": False,
        **AUTHORITY_FALSE,
    }


def _motion_receipt(source_path: Path, source_sha: str, receipt_binding: Any,
                    key: tuple[str, str, int, int], geometry: dict[str, list[int]],
                    source_pixels: tuple[int, int]) -> dict[str, Any]:
    receipt_path, receipt_sha, receipt_raw = _verify_file_binding(
        receipt_binding, "motion receipt", limit=MAX_EVIDENCE_BYTES)
    receipt = parse_json(receipt_raw, "motion receipt")
    if not isinstance(receipt, dict) or receipt.get("schema") != "h3.motion.receipt.v1":
        raise ProductionError("MOTION_RECEIPT_SCHEMA_INVALID", "motion source requires h3.motion.receipt.v1")
    contract_entry = receipt.get("contract", {}).get("entry", {}) if isinstance(receipt.get("contract"), dict) else {}
    slot_id = contract_entry.get("id") if isinstance(contract_entry, dict) else None
    if not isinstance(slot_id, str) or not slot_id:
        raise ProductionError("MOTION_RECEIPT_ID_INVALID", "motion receipt contract entry id is missing")
    preflight = {"id": slot_id, "status": "READY", "fingerprint": receipt.get("job_fingerprint"),
                 "output": str(source_path), "output_pixels": list(source_pixels)}
    result = {"id": slot_id, "status": "VERIFIED", "output": str(source_path), "receipt": str(receipt_path)}
    review = inspect_frame(preflight, result)
    if review.get("status") != "RECEIPT_BOUND_REVIEW_REQUIRED":
        message = "; ".join(review.get("errors", [])) or "existing motion receipt contract rejected"
        raise ProductionError("MOTION_RECEIPT_REJECTED", message)
    if review.get("key") != _slot_key_json(key):
        raise ProductionError("MOTION_SLOT_MISMATCH", "motion receipt is bound to another native slot")
    expected_geometry = {
        "canvas_1x": geometry["canvas_1x"],
        "crop_1x": geometry["crop_1x"],
        "origin_1x": geometry["origin_1x"],
    }
    actual_geometry = {"canvas_1x": review["native_canvas_1x"], "crop_1x": review["native_crop_1x"],
                       "origin_1x": review["native_origin_1x"]}
    if actual_geometry != expected_geometry or review.get("output_sha256") != source_sha:
        raise ProductionError("MOTION_RECEIPT_BINDING_MISMATCH", "motion receipt source or native geometry differs")
    return {"path": str(receipt_path), "sha256": receipt_sha, "job_fingerprint": receipt["job_fingerprint"]}


def _metadata_record(slot: dict[str, Any], geometry: dict[str, list[int]], label: str,
                     file_sha_cache: dict[Path, str]) -> dict[str, Any]:
    """One metadata validation path for preflight and delivered-receipt verification."""
    unknown_fields: list[str] = []
    facing, facing_unknown, _ = _check_metadata(slot["facing"], f"{label}.facing", _known_direction)
    if facing_unknown:
        unknown_fields.append("facing")
    facing_provenance = None if facing_unknown else _cached_binding(
        slot["facing"]["provenance"], f"{label}.facing.provenance", file_sha_cache)
    footpoint, footpoint_unknown, _ = _check_metadata(slot["footpoint"], f"{label}.footpoint", _point_value)
    if footpoint_unknown:
        unknown_fields.append("footpoint")
    elif any(part < 0 or part >= geometry["canvas_1x"][axis] for axis, part in enumerate(footpoint)):
        raise ProductionError("FOOTPOINT_OUTSIDE_CANVAS", f"{label}.footpoint must lie on the native canvas")
    footpoint_provenance = None if footpoint_unknown else _cached_binding(
        slot["footpoint"]["provenance"], f"{label}.footpoint.provenance", file_sha_cache)
    timing = _exact_fields(slot["timing"], {"duration_ms", "duration_provenance", "loop", "loop_provenance"},
                           f"{label}.timing")
    duration, duration_unknown, _ = _check_metadata(
        {"value": timing["duration_ms"], "provenance": timing["duration_provenance"]},
        f"{label}.timing.duration_ms", _duration_value)
    loop, loop_unknown, _ = _check_metadata(
        {"value": timing["loop"], "provenance": timing["loop_provenance"]},
        f"{label}.timing.loop", _loop_value)
    if duration_unknown:
        unknown_fields.append("timing.duration_ms")
    if loop_unknown:
        unknown_fields.append("timing.loop")
    duration_provenance = None if duration_unknown else _cached_binding(
        timing["duration_provenance"], f"{label}.timing.duration_provenance", file_sha_cache)
    loop_provenance = None if loop_unknown else _cached_binding(
        timing["loop_provenance"], f"{label}.timing.loop_provenance", file_sha_cache)
    return {"facing": facing, "footpoint": footpoint,
            "facing_provenance": facing_provenance, "footpoint_provenance": footpoint_provenance,
            "timing": {"duration_ms": duration, "duration_provenance": duration_provenance,
                       "loop": loop, "loop_provenance": loop_provenance}, "open_fields": unknown_fields}


def _recheck_metadata_rows(rows: list[dict[str, Any]]) -> None:
    """Re-read the same receipt-bound evidence after output I/O; never refresh its hash."""
    cache: dict[Path, str] = {}
    for row in rows:
        timing = row["timing"]
        refs = (row.get("facing_provenance"), row.get("footpoint_provenance"),
                timing.get("duration_provenance"), timing.get("loop_provenance"),
                row.get("derivation", {}).get("evidence"))
        for ref in refs:
            if ref is not None:
                try:
                    if _cached_binding(ref, f"final provenance {row['key']}", cache) != ref:
                        raise ProductionError("PROVENANCE_IDENTITY_CHANGED", "provenance identity changed")
                except ProductionError as error:
                    raise ProductionError("PROVENANCE_CHANGED_DURING_RUN", str(error)) from error


def _validate_slot(entry: Any, expected: dict[str, Any],
                   file_sha_cache: dict[Path, str], action_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    expected_key_obj = expected["key"]
    expected_key = ("def", expected_key_obj["identity"], expected_key_obj["group"], expected_key_obj["frame"])
    label = str(expected_key)
    row: dict[str, Any] = {"key": _slot_key_json(expected_key), "status": "BLOCKED", "errors": [], "open_fields": []}
    try:
        slot = _exact_fields(entry, {"key", "geometry", "source", "facing", "footpoint", "timing", "derivation"}, label)
        key = _slot_key(slot["key"], f"{label}.key")
        if key != ("def", expected_key_obj["identity"], expected_key_obj["group"], expected_key_obj["frame"]):
            raise ProductionError("SLOT_KEY_MISMATCH", f"{label} key mismatch")
        geometry = _geometry(slot["geometry"], expected, f"{label}.geometry")
        source_value = slot["source"]
        is_action = isinstance(source_value, dict) and source_value.get("kind") == "blender-action-output"
        source = _exact_fields(source_value, {"kind", "path", "sha256", "native_scale", "receipt"}
                               | ({"layout"} if is_action else set()), f"{label}.source")
        source_kind = source["kind"]
        if source_kind not in {"motion-output", "original-reference", "blender-action-output"}:
            raise ProductionError("SOURCE_KIND_INVALID", f"{label}.source.kind is unsupported")
        if is_action and source["layout"] != "native-canvas":
            raise ProductionError("SOURCE_LAYOUT_INVALID", f"{label} Blender output must retain its native-canvas layout")
        if is_action and source["receipt"] is None:
            raise ProductionError("BLENDER_ACTION_RECEIPT_REQUIRED", f"{label} requires its fixed real Action descriptor")
        scale = source["native_scale"]
        if type(scale) is not int or scale not in {1, 3} or (source_kind != "original-reference" and scale != 3):
            raise ProductionError("SOURCE_SCALE_INVALID", f"{label} source scale must be 1 or 3; motion output must be 3")
        source_path, source_sha = _absolute_file(source["path"], f"{label}.source.path"), source["sha256"]
        if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
            raise ProductionError("SOURCE_SHA_INVALID", f"{label}.source.sha256 is invalid")
        source_sha = source_sha.lower()
        source_raw = _read_bounded(source_path, MAX_PNG_BYTES, f"{label} source PNG")
        if sha256_bytes(source_raw) != source_sha:
            raise ProductionError("SOURCE_SHA_MISMATCH", f"{label} source PNG differs from its pinned SHA256")
        crop = geometry["canvas_1x"] if is_action else geometry["crop_1x"]
        source_pixels = (crop[0] * scale, crop[1] * scale)
        rgba_sha, _ = _validate_png(source_raw, source_pixels, f"{label} source PNG")

        receipt_value = source["receipt"]
        receipt_info = None
        if source_kind == "motion-output":
            if receipt_value is None:
                raise ProductionError("MOTION_RECEIPT_REQUIRED", f"{label} motion output requires its native receipt")
            receipt_info = _motion_receipt(source_path, source_sha, receipt_value, key, geometry, source_pixels)
        elif is_action:
            import action_production_binding as binding
            try:
                receipt_info = binding.verify_source(receipt_value, key, geometry, source_path, source_sha,
                                                     action_cache if action_cache is not None else {},
                                                     timing=slot["timing"])
            except Exception as error:
                raise ProductionError(getattr(error, "code", "BLENDER_ACTION_RECEIPT_REJECTED"), str(error)) from error
        elif receipt_value is not None:
            raise ProductionError("REFERENCE_RECEIPT_INVALID", f"{label} original reference cannot claim a motion receipt")

        metadata = _metadata_record(slot, geometry, label, file_sha_cache)
        unknown_fields = list(metadata["open_fields"])
        facing, footpoint = metadata["facing"], metadata["footpoint"]
        duration, loop = metadata["timing"]["duration_ms"], metadata["timing"]["loop"]
        duration_provenance = metadata["timing"]["duration_provenance"]
        loop_provenance = metadata["timing"]["loop_provenance"]

        derivation = _exact_fields(slot["derivation"], {"kind", "reuse_of", "evidence"}, f"{label}.derivation")
        derivation_kind = derivation["kind"]
        if derivation_kind not in {"keyframe", "original-reference", "exact-copy", "mirror", "loop", "interpolation",
                                   "blender-action-sample"}:
            raise ProductionError("DERIVATION_KIND_INVALID", f"{label}.derivation.kind is unsupported")
        if (derivation_kind == "blender-action-sample" and not is_action
                or is_action and derivation_kind not in {"blender-action-sample", "exact-copy", "keyframe"}):
            raise ProductionError("DERIVATION_SOURCE_MISMATCH", f"{label} Action sample requires a verified Blender source")
        if source_kind == "original-reference" and derivation_kind not in {"original-reference", "exact-copy"}:
            raise ProductionError("DERIVATION_SOURCE_MISMATCH", f"{label} original reference cannot be labeled as generated output")
        if derivation_kind == "original-reference" and source_kind != "original-reference":
            raise ProductionError("DERIVATION_SOURCE_MISMATCH", f"{label} original-reference derivation needs an original source")
        reuse_key = None
        evidence = None
        if derivation_kind == "exact-copy":
            if derivation["reuse_of"] is None or derivation["evidence"] is not None:
                raise ProductionError("EXACT_REUSE_EVIDENCE_INVALID", f"{label} exact-copy requires reuse_of and pixel comparison, with no external claim")
            reuse_key = _slot_key(derivation["reuse_of"], f"{label}.derivation.reuse_of")
            if reuse_key == key:
                raise ProductionError("EXACT_REUSE_CYCLE", f"{label} cannot reuse itself")
        elif derivation_kind in {"mirror", "loop", "interpolation"}:
            if derivation["reuse_of"] is not None or derivation["evidence"] is None:
                raise ProductionError("DERIVATION_EVIDENCE_REQUIRED", f"{label} {derivation_kind} requires explicit SHA-bound evidence")
            ev_path, ev_sha, _ = _verify_file_binding(derivation["evidence"], f"{label}.derivation.evidence",
                                                      limit=MAX_EVIDENCE_BYTES)
            file_sha_cache[ev_path] = ev_sha
            evidence = {"path": str(ev_path), "sha256": ev_sha}
            unknown_fields.append(f"derivation.{derivation_kind}_requires_review")
        elif derivation["reuse_of"] is not None or derivation["evidence"] is not None:
            raise ProductionError("DERIVATION_FIELDS_INVALID", f"{label} {derivation_kind} cannot carry reuse/evidence fields")
        if source_kind == "original-reference":
            unknown_fields.append("source_is_original_reference")

        row.update(key=_slot_key_json(key), status="BOUND", errors=[], source={"kind": source_kind, "path": str(source_path),
                   "sha256": source_sha, "native_scale": scale, "pixels": list(source_pixels),
                   "rgba_sha256": rgba_sha, "receipt": receipt_info},
                   geometry=geometry, facing=facing, footpoint=footpoint,
                   facing_provenance=metadata["facing_provenance"],
                   footpoint_provenance=metadata["footpoint_provenance"],
                   timing={"duration_ms": duration, "duration_provenance": duration_provenance,
                           "loop": loop, "loop_provenance": loop_provenance},
                   derivation={"kind": derivation_kind, "reuse_of": _slot_key_json(reuse_key) if reuse_key else None,
                               "evidence": evidence}, open_fields=unknown_fields,
                   output_relative=f"frames/{key[1]}/g{key[2]:02d}/f{key[3]:03d}.png")
        if is_action:
            row["source"]["layout"] = "native-canvas"
            row["source"]["png_bit_depth"] = source_raw[24]
    except (ProductionError, OSError, TypeError, KeyError, ValueError) as error:
        row["errors"] = [f"{getattr(error, 'code', 'SLOT_INVALID')}: {error}"]
    return row


def preflight(context: Context) -> dict[str, Any]:
    entries = context.manifest.get("slots")
    if not isinstance(entries, list) or len(entries) > 10_000:
        raise ProductionError("SLOT_COUNT_INVALID", "manifest slots must be an array with at most 10000 entries")
    entry_by_key: dict[tuple[str, str, int, int], Any] = {}
    global_errors: list[str] = []
    for index, entry in enumerate(entries):
        try:
            key = _slot_key(entry.get("key") if isinstance(entry, dict) else None, f"slots[{index}].key")
        except ProductionError as error:
            global_errors.append(f"slots[{index}]: {error}")
            continue
        if key in entry_by_key:
            global_errors.append(f"duplicate manifest slot {_slot_key_json(key)}")
        else:
            entry_by_key[key] = entry
        if key not in context.expected_by_key:
            global_errors.append(f"unplanned slot {_slot_key_json(key)}")

    file_sha_cache: dict[Path, str] = {}
    action_cache: dict[str, Any] = {}
    context.action_fingerprints.clear()
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for expected in context.expected_slots:
        key_obj = expected["key"]
        key = ("def", key_obj["identity"], key_obj["group"], key_obj["frame"])
        entry = entry_by_key.get(key)
        if entry is None:
            group = next(group for unit in context.plan["units"] if unit["identity"] == key[1]
                         for group in unit["action_groups"] if group["group"] == key[2])
            missing.append({"key": _slot_key_json(key), "action": group["action"],
                            "status": "NEEDS_KEYFRAME"})
            continue
        rows.append(_validate_slot(entry, expected, file_sha_cache, action_cache))
    context.action_fingerprints.update(action_cache.get("fingerprints", {}))

    # An exact-copy declaration is accepted only when geometry and decoded
    # RGBA pixels agree with the named, separately supplied native slot.
    by_row_key = {_slot_key(row["key"], "internal key"): row
                  for row in rows if row.get("status") == "BOUND"}
    for row in rows:
        reuse_json = row.get("derivation", {}).get("reuse_of")
        if not reuse_json:
            continue
        try:
            owner_key = _slot_key(reuse_json, "reuse_of")
            owner = by_row_key.get(owner_key)
            if owner is None:
                raise ProductionError("EXACT_REUSE_OWNER_MISSING", "exact-copy owner must be a bound actual native slot")
            if (owner["geometry"] != row["geometry"] or owner["source"]["native_scale"] != row["source"]["native_scale"]
                    or owner["source"]["pixels"] != row["source"]["pixels"]
                    or owner["source"].get("layout", "native-crop") != row["source"].get("layout", "native-crop")
                    or owner["source"].get("png_bit_depth", 8) != row["source"].get("png_bit_depth", 8)
                    or (row["source"].get("png_bit_depth", 8) == 16
                        and owner["source"]["sha256"] != row["source"]["sha256"])
                    or owner["source"]["rgba_sha256"] != row["source"]["rgba_sha256"]):
                raise ProductionError("EXACT_REUSE_PIXEL_MISMATCH", "exact-copy slot pixels or native geometry differ from reuse_of")
            row["derivation"]["pixel_equivalence"] = "EXACT_RGBA_PIXELS_AND_NATIVE_GEOMETRY"
            row["derivation"]["canonical_owner"] = _slot_key_json(owner_key)
        except ProductionError as error:
            row["status"] = "BLOCKED"
            row["errors"].append(f"{error.code}: {error}")

    # Pixel equality is insufficient if aliases only point at one another.
    # Resolve the entire reuse chain to a non-alias native slot; reject a
    # cycle or a dependency which has already failed its own validation.
    original_bound_keys = {key for key, value in by_row_key.items() if value["status"] == "BOUND"}
    for row in rows:
        if row["status"] != "BOUND" or not row.get("derivation", {}).get("reuse_of"):
            continue
        visited: set[tuple[str, str, int, int]] = set()
        cursor = row
        while cursor.get("derivation", {}).get("reuse_of"):
            current_key = _slot_key(cursor["key"], "reuse chain slot")
            if current_key in visited:
                row["status"] = "BLOCKED"
                row["errors"].append("EXACT_REUSE_CYCLE: exact-copy dependency chain contains a cycle")
                break
            visited.add(current_key)
            owner_key = _slot_key(cursor["derivation"]["reuse_of"], "reuse chain owner")
            owner = by_row_key.get(owner_key)
            if owner is None or owner_key not in original_bound_keys:
                row["status"] = "BLOCKED"
                row["errors"].append("EXACT_REUSE_DEPENDENCY_BLOCKED: reuse owner is not a valid bound slot")
                break
            cursor = owner

    # R10 is a projection of the existing preflight: no second producer or
    # independent native inventory reader. Preserve original run semantics.
    reviewed_by_key = {_slot_key(row["key"], "review key"): row for row in rows}
    states = {}
    for expected in context.expected_slots:
        key = ("def", expected["key"]["identity"], expected["key"]["group"], expected["key"]["frame"])
        row = reviewed_by_key.get(key)
        if row is None:
            state = "NEEDS_KEYFRAME"
            errors = ["KEYFRAME_SOURCE_MISSING: no pinned source for original action slot"]
            open_fields = ["source_binding", "keyframe"]
        elif row["status"] != "BOUND":
            geometry_errors = ("NATIVE_GEOMETRY_MISMATCH", "PNG_GEOMETRY_MISMATCH",
                               "MOTION_SLOT_MISMATCH", "MOTION_RECEIPT_BINDING_MISMATCH")
            state = ("NEEDS_NATIVE_MAPPING" if any(
                any(code in error for code in geometry_errors) for error in row["errors"]) else "BLOCKED")
            errors = list(row["errors"])
            open_fields = []
        else:
            state = "REVIEW_REQUIRED" if row["open_fields"] else "READY"
            errors = []
            open_fields = list(row["open_fields"])
        states[key] = {"key": _slot_key_json(key), "state": state,
                       "errors": errors, "open_fields": open_fields}
    slot_states = [states[("def", s["key"]["identity"], s["key"]["group"], s["key"]["frame"])]
                   for s in context.expected_slots]
    action_groups = []
    priority = ("BLOCKED", "NEEDS_NATIVE_MAPPING", "NEEDS_KEYFRAME", "REVIEW_REQUIRED", "READY")
    for unit in context.plan["units"]:
        for group in unit["action_groups"]:
            group_states = [
                states[("def", unit["identity"], group["group"], slot["key"]["frame"])]
                for slot in group["slots"]]
            group_status = next((value for value in priority
                                 if any(row["state"] == value for row in group_states)), "ABSENT")
            action_groups.append({
                "identity": unit["identity"], "group": group["group"], "action": group["action"],
                "actual_present": group["actual_present"], "absent": group["absent"],
                "frame_count": group["frame_count"], "status": group_status,
                "slots": group_states, "native_timing_verified": False,
                "art_approved": False, "game_deployed": False,
            })
    blocked = sum(row["status"] != "BOUND" for row in rows) + len(global_errors)
    unknown_count = sum(len(row["open_fields"]) for row in rows if row["status"] == "BOUND")
    if global_errors or any(row["status"] != "BOUND" for row in rows):
        status = "BLOCKED"
    elif missing:
        status = "NEEDS_KEYFRAME"
    elif unknown_count:
        status = "OPEN"
    else:
        status = "READY_FOR_REVIEW"
    return {
        "schema": CHECK_SCHEMA,
        "status": status,
        "operation_id": context.operation_id,
        "operation_fingerprint": context.fingerprint,
        "action_plan_sha256": context.plan_sha256,
        "official_inventory": {"path": str(context.inventory_path), "sha256": context.inventory_sha256},
        "expected_slots": len(context.expected_slots),
        "bound_slots": sum(row["status"] == "BOUND" for row in rows),
        "missing_slots": missing,
        "blocked_slots": sum(row["status"] != "BOUND" for row in rows),
        "global_errors": global_errors,
        "open_metadata_fields": unknown_count,
        "frames": rows,
        "slot_states": slot_states,
        "action_groups": action_groups,
        "review_required": True,
        "art_approved": False,
        "game_deployed": False,
        "images_written": 0,
    }


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _ensure_guarded_directory(path: Path, guard: OutputRootGuard) -> None:
    path = Path(os.path.abspath(path))
    if path != guard.root and not path.is_relative_to(guard.root):
        raise ProductionError("OUTPUT_DIRECTORY_OUTSIDE_GUARD", "output directory escaped its guarded operation root")
    current = guard.root
    for part in path.relative_to(guard.root).parts:
        child = current / part
        guard.hold_directory(current)
        guard.verify_current()
        if not child.exists():
            try:
                child.mkdir()
            except FileExistsError:
                pass
        if not child.is_dir():
            raise ProductionError("OUTPUT_DIRECTORY_INVALID", f"output component is not a directory: {child}")
        guard.hold_directory(child)
        guard.verify_current()
        current = child


def _write_new(path: Path, raw: bytes, guard: OutputRootGuard) -> None:
    try:
        path = guard.verify_target(path)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        guard.verify_current()
    except FileExistsError as error:
        raise ProductionError("OUTPUT_ALREADY_EXISTS", f"refusing to overwrite {path}") from error
    except OSError as error:
        raise ProductionError("OUTPUT_WRITE_FAILED", f"could not write {path}: {error}") from error


def _copy_verified(source: Path, destination: Path, expected_sha: str,
                   guard: OutputRootGuard) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        destination = guard.verify_target(destination)
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            while True:
                chunk = incoming.read(4 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PNG_BYTES:
                    raise ProductionError("SOURCE_SIZE_CHANGED", f"source grew beyond the PNG byte limit: {source}")
                digest.update(chunk)
                guard.verify_current()
                outgoing.write(chunk)
                guard.verify_current()
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except ProductionError:
        raise
    except OSError as error:
        raise ProductionError("OUTPUT_WRITE_FAILED", f"cannot preserve source PNG bytes: {error}") from error
    actual = digest.hexdigest()
    if total == 0 or actual != expected_sha:
        raise ProductionError("SOURCE_DRIFT_DURING_RUN", f"source changed while being copied: {source}")
    return actual, total


def _publish_complete_pointer(pending: Path, path: Path, guard: OutputRootGuard) -> None:
    pending = guard.verify_target(pending, must_exist=True)
    path = guard.verify_target(path)
    guard.verify_current()
    try:
        # On Windows os.rename is atomic on one volume and refuses to replace
        # an existing destination. The operation root is exclusively reserved.
        os.rename(pending, path)
    except FileExistsError as error:
        raise ProductionError("COMPLETE_POINTER_ALREADY_EXISTS", "refusing to replace the complete pointer") from error
    except OSError as error:
        raise ProductionError("COMPLETE_POINTER_PUBLISH_FAILED", f"cannot atomically publish complete pointer: {error}") from error


def _precommit_verify(context: Context, checked: dict[str, Any], receipt: dict[str, Any],
                      receipt_raw: bytes, complete_raw: bytes, pending: Path,
                      guard: OutputRootGuard) -> dict[str, Any]:
    """Read back every owned byte while the output root guard is still held.

    Only a successful return permits the final atomic complete-pointer rename.
    """
    guard.verify_current()
    manifest_path = guard.verify_target(context.outdir / "manifest.json", must_exist=True)
    manifest_copy = _read_bounded(manifest_path, MAX_MANIFEST_BYTES, "precommit delivered manifest")
    if manifest_copy != context.manifest_raw or sha256_bytes(manifest_copy) != context.manifest_sha256:
        raise ProductionError("PRECOMMIT_MANIFEST_MISMATCH", "delivered manifest failed exact-byte readback")
    receipt_path = guard.verify_target(context.outdir / "receipt.json", must_exist=True)
    stored_receipt = _read_bounded(receipt_path, MAX_MANIFEST_BYTES, "precommit delivery receipt")
    if stored_receipt != receipt_raw or parse_json(stored_receipt, "precommit receipt") != receipt:
        raise ProductionError("PRECOMMIT_RECEIPT_MISMATCH", "delivery receipt failed exact-byte readback")
    pending_path = guard.verify_target(pending, must_exist=True)
    pending_raw = _read_bounded(pending_path, 4 * 1024 * 1024, "precommit completion pointer")
    complete = parse_json(complete_raw, "precommit completion pointer")
    if pending_raw != complete_raw or complete.get("schema") != COMPLETE_SCHEMA:
        raise ProductionError("PRECOMMIT_POINTER_MISMATCH", "pending completion pointer failed readback")
    if complete.get("receipt_sha256") != sha256_bytes(receipt_raw):
        raise ProductionError("PRECOMMIT_POINTER_MISMATCH", "pending pointer does not bind the receipt bytes")

    rows = checked["frames"]
    if len(rows) != len(context.expected_slots) or any(row.get("status") != "BOUND" for row in rows):
        raise ProductionError("PRECOMMIT_SLOT_COVERAGE_INVALID", "full native slot coverage is required before delivery")
    output_files: set[str] = set()
    for index, (row, expected) in enumerate(zip(rows, context.expected_slots)):
        key = _slot_key(row.get("key"), f"precommit frame[{index}]")
        plan_key = expected["key"]
        if key != ("def", plan_key["identity"], plan_key["group"], plan_key["frame"]):
            raise ProductionError("PRECOMMIT_SLOT_ORDER_INVALID", "precommit frames differ from action-plan order")
        relative = row["output_relative"]
        output_path = guard.verify_target(context.outdir / Path(relative), must_exist=True)
        raw = _read_bounded(output_path, MAX_PNG_BYTES, f"precommit PNG {key}")
        expected_sha = row["source"]["sha256"]
        if sha256_bytes(raw) != expected_sha:
            raise ProductionError("PRECOMMIT_PNG_SHA_MISMATCH", f"destination PNG bytes changed: {key}")
        rgba_sha, _ = _validate_png(raw, tuple(row["source"]["pixels"]), f"precommit PNG {key}")
        if rgba_sha != row["source"]["rgba_sha256"]:
            raise ProductionError("PRECOMMIT_RGBA_MISMATCH", f"destination decoded RGBA differs: {key}")
        geometry = row["geometry"]
        if (geometry["canvas_1x"] != expected["canvas"] or geometry["crop_1x"] != expected["crop"]
                or geometry["origin_1x"] != expected["anchor"]):
            raise ProductionError("PRECOMMIT_GEOMETRY_MISMATCH", f"destination geometry differs from native slot: {key}")
        output_files.add(Path(relative).as_posix())
        guard.verify_current()
    guard.verify_current()
    for output_path in context.outdir.rglob("*.png"):
        try:
            reject_reparse_components(output_path, allow_missing_leaf=False)
        except Exception as error:
            raise ProductionError("PRECOMMIT_OUTPUT_PATH_UNSAFE", str(error)) from error
        relative = output_path.relative_to(context.outdir).as_posix()
        if relative not in output_files:
            raise ProductionError("PRECOMMIT_UNRECEIPTED_PNG", f"unreceipted PNG exists: {relative}")
    guard.verify_current()
    if context.action_fingerprints:
        import action_production_binding as binding
        binding.recheck_fingerprints(context.action_fingerprints)
    _recheck_metadata_rows(rows)
    return {"verified_slots": len(rows), "manifest_sha256": sha256_bytes(manifest_copy),
            "receipt_sha256": sha256_bytes(stored_receipt), "all_destinations_read_back": True}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _make_receipt(context: Context, checked: dict[str, Any], manifest_copy_sha: str) -> dict[str, Any]:
    frames = []
    for row in checked["frames"]:
        if row["status"] != "BOUND":
            continue
        frames.append({
            "key": row["key"], "output": row["output_relative"],
            "output_sha256": row["source"]["sha256"], "bytes_preserved": True,
            "output_pixels": row["source"]["pixels"], "geometry": row["geometry"],
            "source": row["source"], "facing": row["facing"], "footpoint": row["footpoint"],
            "facing_provenance": row["facing_provenance"], "footpoint_provenance": row["footpoint_provenance"],
            "timing": row["timing"], "derivation": row["derivation"],
            "open_fields": row["open_fields"],
        })
    status = "OPEN" if checked["status"] == "OPEN" else "REVIEW_REQUIRED"
    return {
        "schema": RECEIPT_SCHEMA,
        "operation_id": context.operation_id,
        "operation_fingerprint": context.fingerprint,
        "manifest_sha256": context.manifest_sha256,
        "manifest_copy_sha256": manifest_copy_sha,
        "action_plan_sha256": context.plan_sha256,
        "official_inventory": {"path": str(context.inventory_path), "sha256": context.inventory_sha256},
        "delivery_status": status,
        "expected_slots": len(context.expected_slots),
        "delivered_slots": len(frames),
        "missing_slots": checked["missing_slots"],
        "frames": frames,
        **AUTHORITY_FALSE,
        "review_required": True,
        "game_or_source_assets_modified": False,
    }


def _completion_record(context: Context, receipt_raw: bytes, delivery_status: str, slots: int) -> dict[str, Any]:
    return {
        "schema": COMPLETE_SCHEMA,
        "operation_id": context.operation_id,
        "operation_fingerprint": context.fingerprint,
        "manifest_sha256": context.manifest_sha256,
        "action_plan_sha256": context.plan_sha256,
        "inventory_sha256": context.inventory_sha256,
        "receipt_sha256": sha256_bytes(receipt_raw),
        "delivery_status": delivery_status,
        "delivered_slots": slots,
        "delivery_complete": True,
        **AUTHORITY_FALSE,
    }


def _directory_manifest_and_receipt(directory: Path, context: Context) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    if not directory.exists() or _is_reparse(directory) or not directory.is_dir():
        raise ProductionError("OUTPUT_DIRECTORY_INVALID", "output directory is absent, reparse, or not a directory")
    try:
        reject_reparse_components(directory, allow_missing_leaf=False)
    except Exception as error:
        raise ProductionError("OUTPUT_DIRECTORY_INVALID", f"output directory path is unsafe: {error}") from error
    manifest_file = directory / "manifest.json"
    receipt_file = directory / "receipt.json"
    complete_file = directory / "complete.json"
    try:
        for item in (manifest_file, receipt_file, complete_file):
            reject_reparse_components(item, allow_missing_leaf=False)
        manifest_copy = _read_bounded(manifest_file, MAX_MANIFEST_BYTES, "delivered manifest")
        receipt_raw = _read_bounded(receipt_file, MAX_MANIFEST_BYTES, "delivery receipt")
        complete_raw = _read_bounded(complete_file, 4 * 1024 * 1024, "complete pointer")
    except Exception as error:
        raise ProductionError("PARTIAL_OUTPUT_PRESENT", f"output has no valid complete pointer: {error}") from error
    if sha256_bytes(manifest_copy) != context.manifest_sha256 or manifest_copy != context.manifest_raw:
        raise ProductionError("DELIVERED_MANIFEST_DRIFT", "copied manifest is not byte-identical to the frozen input")
    receipt = parse_json(receipt_raw, "delivery receipt")
    complete = parse_json(complete_raw, "complete pointer")
    return receipt, receipt_raw, complete, manifest_copy


def _verify_directory(directory: Path, context: Context) -> dict[str, Any]:
    if any(isinstance(item.get("source"), dict) and item["source"].get("kind") == "blender-action-output"
           for item in context.manifest["slots"]):
        checked = preflight(context)
        if checked["status"] not in {"OPEN", "READY_FOR_REVIEW"}:
            raise ProductionError("ACTION_SOURCE_VERIFICATION_FAILED", "delivered Action sources/evidence no longer verify")
    receipt, receipt_raw, complete, _ = _directory_manifest_and_receipt(directory, context)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ProductionError("DELIVERY_RECEIPT_INVALID", "unsupported delivery receipt schema")
    if not isinstance(complete, dict) or complete.get("schema") != COMPLETE_SCHEMA:
        raise ProductionError("COMPLETE_POINTER_INVALID", "unsupported complete pointer schema")
    identity = {"operation_id": context.operation_id, "operation_fingerprint": context.fingerprint,
                "manifest_sha256": context.manifest_sha256, "action_plan_sha256": context.plan_sha256}
    for label, value in (("receipt", receipt), ("complete pointer", complete)):
        for field, expected in identity.items():
            if value.get(field) != expected:
                raise ProductionError("OUTPUT_IDENTITY_MISMATCH", f"{label} {field} differs from this invocation")
    if complete.get("receipt_sha256") != sha256_bytes(receipt_raw):
        raise ProductionError("DELIVERY_RECEIPT_SHA_MISMATCH", "complete pointer does not match receipt bytes")
    if (receipt.get("manifest_copy_sha256") != context.manifest_sha256
            or complete.get("inventory_sha256") != context.inventory_sha256
            or receipt.get("official_inventory") != {"path": str(context.inventory_path), "sha256": context.inventory_sha256}):
        raise ProductionError("OUTPUT_SOURCE_IDENTITY_MISMATCH", "delivered source identity differs")
    for field, expected in AUTHORITY_FALSE.items():
        if receipt.get(field) is not expected or complete.get(field) is not expected:
            raise ProductionError("OUTPUT_AUTHORITY_DRIFT", f"{field} must remain false")
    if complete.get("delivery_complete") is not True:
        raise ProductionError("COMPLETE_POINTER_INVALID", "delivery_complete must be true")
    frames = receipt.get("frames")
    if not isinstance(frames, list) or len(frames) != len(context.expected_slots):
        raise ProductionError("OUTPUT_SLOT_COUNT_MISMATCH", "receipt does not cover every actual native slot")
    expected_order = []
    for slot in context.expected_slots:
        key = slot["key"]
        expected_order.append(["def", key["identity"], key["group"], key["frame"]])
    seen: set[tuple[str, str, int, int]] = set()
    output_files: set[str] = set()
    metadata_cache: dict[Path, str] = {}
    for row, expected_key_json in zip(frames, expected_order):
        if not isinstance(row, dict) or row.get("key") != expected_key_json:
            raise ProductionError("OUTPUT_SLOT_ORDER_MISMATCH", "receipt slots differ from the native action-plan order")
        key = _slot_key(row["key"], "receipt frame key")
        if key in seen:
            raise ProductionError("OUTPUT_SLOT_DUPLICATE", "delivery receipt repeats a slot")
        seen.add(key)
        expected_plan = context.expected_by_key[key]
        source = row.get("source")
        if not isinstance(source, dict) or not SHA_RE.fullmatch(str(source.get("sha256", ""))):
            raise ProductionError("OUTPUT_SOURCE_RECORD_INVALID", f"source receipt missing for {key}")
        manifest_slot = next((item for item in context.manifest["slots"]
                              if _slot_key(item.get("key"), "manifest slot key") == key), None)
        if not isinstance(manifest_slot, dict):
            raise ProductionError("OUTPUT_SOURCE_RECORD_INVALID", f"frozen manifest binding missing for {key}")
        metadata = _metadata_record(manifest_slot, {"canvas_1x": expected_plan["canvas"]},
                                    f"delivered metadata {key}", metadata_cache)
        for field in ("facing", "footpoint", "timing", "facing_provenance", "footpoint_provenance"):
            if _json_bytes(row.get(field)) != _json_bytes(metadata[field]):
                raise ProductionError("OUTPUT_METADATA_DRIFT", f"receipt {field} differs from its frozen input: {key}")
        metadata_fields = {"facing", "footpoint", "timing.duration_ms", "timing.loop"}
        actual_open = row.get("open_fields")
        if (not isinstance(actual_open, list)
                or any(not isinstance(value, str) for value in actual_open)
                or set(metadata["open_fields"]) != metadata_fields.intersection(actual_open)):
            raise ProductionError("OUTPUT_METADATA_DRIFT", f"receipt metadata status differs from its frozen input: {key}")
        manifest_source = manifest_slot.get("source")
        if not isinstance(manifest_source, dict):
            raise ProductionError("OUTPUT_SOURCE_RECORD_INVALID", f"frozen source binding missing for {key}")
        if (source.get("sha256", "").lower() != str(manifest_source.get("sha256", "")).lower()
                or source.get("kind") != manifest_source.get("kind")
                or source.get("native_scale") != manifest_source.get("native_scale")):
            raise ProductionError("OUTPUT_SOURCE_RECORD_DRIFT", f"receipt source binding differs from frozen manifest: {key}")
        if source.get("kind") == "blender-action-output" and (
                source.get("layout") != "native-canvas" or manifest_source.get("layout") != "native-canvas"):
            raise ProductionError("OUTPUT_SOURCE_LAYOUT_DRIFT", f"Action canvas layout differs from manifest: {key}")
        try:
            if Path(source.get("path", "")).resolve(strict=False) != Path(manifest_source["path"]).resolve(strict=False):
                raise ProductionError("OUTPUT_SOURCE_RECORD_DRIFT", f"receipt source path differs from manifest: {key}")
        except (KeyError, OSError, TypeError) as error:
            raise ProductionError("OUTPUT_SOURCE_RECORD_INVALID", f"source path invalid for {key}") from error
        manifest_receipt = manifest_source.get("receipt")
        actual_receipt = source.get("receipt")
        if manifest_receipt is None:
            if actual_receipt is not None:
                raise ProductionError("OUTPUT_SOURCE_RECORD_DRIFT", f"unexpected motion receipt for {key}")
        else:
            receipt_binding = _exact_fields(manifest_receipt, {"path", "sha256"}, "manifest motion receipt")
            expected_receipt_path = _path_identity(receipt_binding["path"], "manifest motion receipt path")
            expected_receipt_sha = receipt_binding["sha256"]
            if not isinstance(expected_receipt_sha, str) or not SHA_RE.fullmatch(expected_receipt_sha):
                raise ProductionError("OUTPUT_SOURCE_RECORD_INVALID", f"motion receipt SHA invalid for {key}")
            expected_receipt_sha = expected_receipt_sha.lower()
            if (not isinstance(actual_receipt, dict) or actual_receipt.get("path") != str(expected_receipt_path)
                    or actual_receipt.get("sha256") != expected_receipt_sha):
                raise ProductionError("OUTPUT_SOURCE_RECORD_DRIFT", f"motion receipt differs from manifest for {key}")
        relative = f"frames/{key[1]}/g{key[2]:02d}/f{key[3]:03d}.png"
        if row.get("output") != relative:
            raise ProductionError("OUTPUT_PATH_MISMATCH", f"output path does not match its native slot: {key}")
        output_path = directory / Path(relative)
        try:
            reject_reparse_components(output_path, allow_missing_leaf=False)
            output_resolved = output_path.resolve(strict=True)
            output_resolved.relative_to(directory.resolve(strict=True))
        except Exception as error:
            raise ProductionError("OUTPUT_PATH_ESCAPE", f"output is missing or escapes its operation root: {key}") from error
        if _is_reparse(output_path) or not output_path.is_file():
            raise ProductionError("OUTPUT_FILE_INVALID", f"output is not a regular file: {key}")
        raw = _read_bounded(output_path, MAX_PNG_BYTES, f"delivered PNG {key}")
        actual_sha = sha256_bytes(raw)
        if actual_sha != row.get("output_sha256") or actual_sha != source["sha256"]:
            raise ProductionError("OUTPUT_PNG_SHA_MISMATCH", f"delivered PNG bytes differ from source: {key}")
        geometry = row.get("geometry")
        if not isinstance(geometry, dict):
            raise ProductionError("OUTPUT_GEOMETRY_MISSING", f"delivery receipt has no geometry: {key}")
        if (geometry.get("canvas_1x") != expected_plan["canvas"]
                or geometry.get("crop_1x") != expected_plan["crop"]
                or geometry.get("origin_1x") != expected_plan["anchor"]):
            raise ProductionError("OUTPUT_GEOMETRY_DRIFT", f"delivered geometry differs from native slot: {key}")
        expected_pixels = row.get("output_pixels")
        if (not isinstance(expected_pixels, list) or len(expected_pixels) != 2
                or any(type(part) is not int for part in expected_pixels)):
            raise ProductionError("OUTPUT_PIXELS_INVALID", f"delivery receipt pixel geometry invalid: {key}")
        rgba_sha, _ = _validate_png(raw, tuple(expected_pixels), f"delivered PNG {key}")
        if rgba_sha != source.get("rgba_sha256"):
            raise ProductionError("OUTPUT_RGBA_SHA_MISMATCH", f"decoded RGBA pixels differ from preflight receipt: {key}")
        dimension = "canvas" if source.get("kind") == "blender-action-output" else "crop"
        if expected_pixels != [expected_plan[dimension][0] * source.get("native_scale", 0),
                               expected_plan[dimension][1] * source.get("native_scale", 0)]:
            raise ProductionError("OUTPUT_PIXELS_INVALID", f"pixel size is not the bound slot crop at source scale: {key}")
        output_files.add(relative.replace("\\", "/"))
    for path in directory.rglob("*.png"):
        try:
            reject_reparse_components(path, allow_missing_leaf=False)
        except Exception as error:
            raise ProductionError("OUTPUT_FILE_INVALID", f"delivery tree contains an unsafe PNG path: {error}") from error
        if _is_reparse(path) or not path.is_file():
            raise ProductionError("OUTPUT_FILE_INVALID", "delivery tree contains a reparse or non-file PNG")
        relative = path.relative_to(directory).as_posix()
        if relative not in output_files:
            raise ProductionError("UNRECEIPTED_OUTPUT_FILE", f"delivery contains an unreceipted PNG: {relative}")
    if receipt.get("expected_slots") != len(context.expected_slots) or receipt.get("delivered_slots") != len(frames):
        raise ProductionError("OUTPUT_SLOT_COUNT_MISMATCH", "receipt slot totals do not match frame list")
    if complete.get("delivered_slots") != len(frames) or complete.get("delivery_status") != receipt.get("delivery_status"):
        raise ProductionError("COMPLETE_POINTER_INVALID", "complete pointer totals/status differ from receipt")
    if receipt.get("delivery_status") not in {"OPEN", "REVIEW_REQUIRED"}:
        raise ProductionError("DELIVERY_STATUS_INVALID", "delivery status cannot grant art approval")
    if context.action_fingerprints:
        import action_production_binding as binding
        binding.recheck_fingerprints(context.action_fingerprints)
    _recheck_metadata_rows(frames)
    return {"verified_slots": len(frames), "delivery_status": receipt["delivery_status"],
            "receipt_sha256": sha256_bytes(receipt_raw), "directory": str(directory)}


def verify(context: Context) -> dict[str, Any]:
    if context.outdir.exists():
        directory = context.outdir
    else:
        return {"schema": "h3.creature.production.verify.v1", "status": "NOT_FOUND",
                "operation_id": context.operation_id, "verified_slots": 0,
                "art_approved": False, "game_deployed": False}
    try:
        result = _verify_directory(directory, context)
        return {"schema": "h3.creature.production.verify.v1",
                "status": f"VERIFIED_{result['delivery_status']}",
                "operation_id": context.operation_id, **result, "recovered": True,
                "art_approved": False, "game_deployed": False}
    except ProductionError as error:
        return {"schema": "h3.creature.production.verify.v1",
                "status": "PARTIAL_OUTPUT_PRESENT" if error.code == "PARTIAL_OUTPUT_PRESENT" else "BLOCKED",
                "operation_id": context.operation_id, "code": error.code, "error": str(error),
                "directory": str(directory), "verified_slots": 0,
                "art_approved": False, "game_deployed": False}


def run(context: Context) -> dict[str, Any]:
    # Existing complete output can be resumed without rereading mutable source
    # PNGs. A stable operation identity either verifies as a whole or refuses.
    if context.outdir.exists():
        result = verify(context)
        if result["status"].startswith("VERIFIED_"):
            return {"schema": "h3.creature.production.run.v1", "status": "RECOVERED_COMPLETE",
                    "operation_id": context.operation_id, "verified_slots": result["verified_slots"],
                    "delivery_status": result["delivery_status"], "directory": result["directory"],
                    "receipt_sha256": result["receipt_sha256"], "images_written": 0,
                    "art_approved": False, "game_deployed": False}
        return {"schema": "h3.creature.production.run.v1", "status": result["status"],
                "operation_id": context.operation_id, "code": result.get("code"),
                "error": result.get("error"), "directory": str(context.outdir),
                "images_written": 0, "art_approved": False, "game_deployed": False}

    checked = preflight(context)
    if checked["status"] not in {"OPEN", "READY_FOR_REVIEW"}:
        return {"schema": "h3.creature.production.run.v1", "status": checked["status"],
                "operation_id": context.operation_id, "preflight": checked,
                "images_written": 0, "art_approved": False, "game_deployed": False}
    try:
        guard = OutputRootGuard.create(context.outdir, OUTPUT_BASE)
    except Exception as error:
        if context.outdir.exists():
            prior = verify(context)
            return {"schema": "h3.creature.production.run.v1", "status": prior["status"],
                    "operation_id": context.operation_id, "code": "OUTPUT_ROOT_RESERVATION_FAILED",
                    "error": str(error), "directory": str(context.outdir), "images_written": 0,
                    "art_approved": False, "game_deployed": False}
        raise ProductionError("OUTPUT_ROOT_RESERVATION_FAILED", str(error)) from error
    try:
        for row in checked["frames"]:
            key = _slot_key(row["key"], "preflight frame key")
            entry = next(item for item in context.manifest["slots"] if _slot_key(item["key"], "manifest key") == key)
            source_path = _absolute_file(entry["source"]["path"], "source PNG")
            source_sha = row["source"]["sha256"]
            destination = context.outdir / Path(row["output_relative"])
            _ensure_guarded_directory(destination.parent, guard)
            copied_sha, _ = _copy_verified(source_path, destination, source_sha, guard)
            if copied_sha != source_sha:
                raise ProductionError("SOURCE_DRIFT_DURING_RUN", f"PNG bytes changed during delivery: {key}")
        _write_new(context.outdir / "manifest.json", context.manifest_raw, guard)
        receipt = _make_receipt(context, checked, context.manifest_sha256)
        receipt_raw = _json_bytes(receipt)
        _write_new(context.outdir / "receipt.json", receipt_raw, guard)
        complete = _completion_record(context, receipt_raw, receipt["delivery_status"], receipt["delivered_slots"])
        complete_raw = _json_bytes(complete)
        pending = context.outdir / f".complete-{context.operation_id}.pending"
        _write_new(pending, complete_raw, guard)
        precommit = _precommit_verify(context, checked, receipt, receipt_raw, complete_raw, pending, guard)
        _publish_complete_pointer(pending, context.outdir / "complete.json", guard)
    except Exception as error:
        # Leave the guarded root as evidence. Without its final complete pointer,
        # verify reports partial and the stable identity cannot write again.
        code = getattr(error, "code", "DELIVERY_PARTIAL")
        guard.close()
        return {"schema": "h3.creature.production.run.v1", "status": "PARTIAL_OUTPUT_PRESENT",
                "operation_id": context.operation_id, "code": code,
                "error": f"delivery stopped with a preserved partial tree: {error}",
                "directory": str(context.outdir), "images_written": 0,
                "art_approved": False, "game_deployed": False}
    finally:
        guard.close()

    status = "DELIVERED_OPEN" if receipt["delivery_status"] == "OPEN" else "DELIVERED_REVIEW_REQUIRED"
    return {"schema": "h3.creature.production.run.v1", "status": status,
            "operation_id": context.operation_id, "verified_slots": precommit["verified_slots"],
            "delivery_status": receipt["delivery_status"], "directory": str(context.outdir),
            "receipt_sha256": precommit["receipt_sha256"],
            "all_destinations_read_back": precommit["all_destinations_read_back"],
            "images_written": receipt["delivered_slots"],
            "art_approved": False, "game_deployed": False}


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run", "verify"))
    parser.add_argument("--report-density", action="store_true",
                        help="read-only alpha>=128 body density report; does not write or change delivery decisions")
    parser.add_argument("--manifest", required=True, help="frozen h3.creature.production-manifest.v1 JSON")
    parser.add_argument("--manifest-sha256", required=True, help="SHA256 of exact manifest bytes")
    parser.add_argument("--outdir", required=True, help="build/experiments/creature-production/<operation-id>")
    parser.add_argument("--operation-id", required=True, help="stable operation identity matching the manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if actual_argv and actual_argv[0] in {"prepare-from-actions", "production-manifest"}:
        import action_production_binding as binding
        return binding.main(actual_argv)
    args = make_parser().parse_args(argv)
    try:
        context = _load_context(args.manifest, args.manifest_sha256, args.operation_id, args.outdir)
        if args.report_density:
            report = report_density(context)
            print(json.dumps(report, ensure_ascii=False))
            return 0
        if args.command == "check":
            report = preflight(context)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["status"] in {"OPEN", "READY_FOR_REVIEW"} else 2
        if args.command == "run":
            report = run(context)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["status"] in {"DELIVERED_OPEN", "DELIVERED_REVIEW_REQUIRED", "RECOVERED_COMPLETE"} else 2
        report = verify(context)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["status"].startswith(("VERIFIED_", "RECOVERABLE_COMPLETE")) else 2
    except (ProductionError, creature_action_plan.PlanError, OSError, ValueError) as error:
        code = getattr(error, "code", "PRODUCTION_FAILED")
        report = {"schema": ERROR_SCHEMA, "status": "BLOCKED", "code": code, "error": str(error),
                  "art_approved": False, "game_deployed": False, "images_written": 0}
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
