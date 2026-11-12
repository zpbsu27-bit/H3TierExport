"""Compare receipt-pinned transparent interpolation candidates without inference.

The benchmark consumes candidates produced by existing backends. It never runs
RIFE or the mesh engine, never edits source PNGs, and never grants art approval.
All reports are written below build/experiments/transparent-benchmark.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import stat
from typing import Any
import html

import numpy as np
from PIL import Image
from companion_runtime import source_path as runtime_source_path


PRODUCT = resolve_tool_root(__file__)
OUTPUT_ROOT = PRODUCT / "build" / "experiments" / "transparent-benchmark"
REPORT_NAME = "benchmark-report.json"
REPORT_SHA_NAME = "benchmark-report.sha256"
REVIEW_HTML_NAME = "index.html"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PNG_BYTES = 256 * 1024 * 1024
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 32 * 1024 * 1024
REQUIRED_SEMANTIC_CATEGORIES = ("shadow", "smoke", "fire", "turning", "occlusion", "footpoint")
MASK_CATEGORIES = frozenset(("shadow", "smoke", "fire", "footpoint"))
GOOD_CANDIDATE_STATUS = "CANDIDATE_REVIEW_REQUIRED"
NO_OUTPUT_STATUSES = frozenset(("N/A", "NEEDS_KEYFRAME", "BACKEND_FAILED", "NOT_AVAILABLE"))


def _load_metric_adapter():
    path = runtime_source_path("scripts/interpolate_rife_candidates.py", PRODUCT)
    spec = importlib.util.spec_from_file_location("h3_interpolate_rife_candidates", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the existing RIFE metric/premultiply implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rife = _load_metric_adapter()


def _load_native_review_module():
    """Reuse the native review workflow's Windows output identity guard."""
    path = runtime_source_path("scripts/review_native_batch.py", PRODUCT)
    scripts_directory = str(path.parent)
    inserted = scripts_directory not in sys.path
    if inserted:
        sys.path.insert(0, scripts_directory)
    try:
        spec = importlib.util.spec_from_file_location("h3_review_native_batch_guard", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load the existing output-root identity guard")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            try:
                sys.path.remove(scripts_directory)
            except ValueError:
                pass


native_review = _load_native_review_module()
OutputRootGuard = native_review.OutputRootGuard
reject_reparse_components = native_review.reject_reparse_components


class BenchmarkRejected(ValueError):
    """The frozen benchmark inputs or evidence do not satisfy the contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved_file(path_value: object, expected_sha: object, label: str, limit: int) -> tuple[Path, bytes]:
    if not isinstance(path_value, str) or not isinstance(expected_sha, str):
        raise BenchmarkRejected(f"{label}: path and SHA256 required")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise BenchmarkRejected(f"{label}: malformed SHA256")
    try:
        path = Path(path_value).resolve(strict=True)
        if not path.is_file():
            raise BenchmarkRejected(f"{label}: regular file required")
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError as error:
        raise BenchmarkRejected(f"{label}: cannot read file: {error}") from error
    if len(data) > limit:
        raise BenchmarkRejected(f"{label}: file exceeds {limit} byte limit")
    if sha256_bytes(data) != expected_sha:
        raise BenchmarkRejected(f"{label}: SHA256 mismatch")
    return path, data


def _read_pinned_json(ref: object, label: str) -> tuple[Path, dict]:
    if not isinstance(ref, dict):
        raise BenchmarkRejected(f"{label}: pinned JSON reference required")
    path, data = _resolved_file(ref.get("path"), ref.get("sha256"), label, MAX_JSON_BYTES)
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkRejected(f"{label}: invalid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise BenchmarkRejected(f"{label}: JSON object required")
    return path, value


def _json_pointer(value: Any, pointer: object, label: str) -> Any:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise BenchmarkRejected(f"{label}: JSON pointer must be empty or start with '/'")
    current = value
    if not pointer:
        return current
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdecimal() or int(token) >= len(current):
                raise BenchmarkRejected(f"{label}: JSON pointer target is missing")
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise BenchmarkRejected(f"{label}: JSON pointer target is missing")
    return current


def _int_pair(value: object, label: str, minimum: int = 0) -> list[int]:
    if not isinstance(value, list) or len(value) != 2 or any(
        type(part) is not int or part < minimum for part in value
    ):
        raise BenchmarkRejected(f"{label}: invalid integer pair")
    return value


def _int_quad(value: object, label: str, minimum: int = 0) -> list[int]:
    if not isinstance(value, list) or len(value) != 4 or any(
        type(part) is not int or part < minimum for part in value
    ):
        raise BenchmarkRejected(f"{label}: invalid integer rectangle")
    return value


def _key(value: object, label: str) -> list[object]:
    if (not isinstance(value, list) or len(value) != 4
            or value[0] not in ("def", "synthetic")
            or not isinstance(value[1], str) or not 1 <= len(value[1]) <= 80
            or any(type(part) is not int or part < 0 for part in value[2:])):
        raise BenchmarkRejected(f"{label}: invalid H3 slot key")
    return value


def _load_rgba_frame(spec: object, canvas: list[int], label: str) -> dict:
    if not isinstance(spec, dict):
        raise BenchmarkRejected(f"{label}: RGBA frame object required")
    path, data = _resolved_file(spec.get("path"), spec.get("sha256"), label, MAX_PNG_BYTES)
    pixels = _int_pair(spec.get("pixels"), label + ".pixels", 1)
    origin = _int_pair(spec.get("origin_pixels"), label + ".origin_pixels", 0)
    key = _key(spec.get("key"), label + ".key")
    if any(origin[i] + pixels[i] > canvas[i] for i in range(2)):
        raise BenchmarkRejected(f"{label}: frame exceeds frozen canvas")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.mode != "RGBA" or list(image.size) != pixels:
                raise BenchmarkRejected(f"{label}: exact-size RGBA PNG required")
            rgba = np.asarray(image).copy()
    except OSError as error:
        raise BenchmarkRejected(f"{label}: invalid PNG: {error}") from error
    return {"path": path, "sha256": spec["sha256"], "pixels": pixels,
            "origin": origin, "key": key, "rgba": rgba}


def _load_l_mask(spec: object, canvas: list[int], label: str) -> dict:
    if not isinstance(spec, dict):
        raise BenchmarkRejected(f"{label}: region mask reference required")
    path, data = _resolved_file(spec.get("path"), spec.get("sha256"), label, MAX_PNG_BYTES)
    pixels = _int_pair(spec.get("pixels"), label + ".pixels", 1)
    origin = _int_pair(spec.get("origin_pixels"), label + ".origin_pixels", 0)
    if any(origin[i] + pixels[i] > canvas[i] for i in range(2)):
        raise BenchmarkRejected(f"{label}: region mask exceeds frozen canvas")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.mode != "L" or list(image.size) != pixels:
                raise BenchmarkRejected(f"{label}: exact-size grayscale PNG mask required")
            mask = np.asarray(image).copy()
    except OSError as error:
        raise BenchmarkRejected(f"{label}: invalid PNG mask: {error}") from error
    return {"path": path, "sha256": spec["sha256"], "pixels": pixels,
            "origin": origin, "mask": mask}


def _place_roi(frame: dict, roi: list[int]) -> np.ndarray:
    x, y, width, height = roi
    result = np.zeros((height, width, 4), dtype=np.uint8)
    ox, oy = frame["origin"]
    fw, fh = frame["pixels"]
    left, top = ox - x, oy - y
    if left < 0 or top < 0 or left + fw > width or top + fh > height:
        raise BenchmarkRejected("frame does not fit entirely inside the frozen evaluation ROI")
    result[top:top + fh, left:left + fw] = frame["rgba"]
    return result


def _place_mask_roi(frame: dict, roi: list[int]) -> np.ndarray:
    x, y, width, height = roi
    result = np.zeros((height, width), dtype=np.uint8)
    ox, oy = frame["origin"]
    fw, fh = frame["pixels"]
    left, top = ox - x, oy - y
    if left < 0 or top < 0 or left + fw > width or top + fh > height:
        raise BenchmarkRejected("semantic mask does not fit inside the frozen evaluation ROI")
    result[top:top + fh, left:left + fw] = frame["mask"]
    return result


def _compare_input_frames(actual: dict, expected: dict, label: str) -> None:
    fields = {"sha256": "sha256", "pixels": "pixels", "origin_pixels": "origin", "key": "key"}
    for actual_field, expected_field in fields.items():
        actual_value = actual.get(actual_field)
        expected_value = expected.get(expected_field)
        if actual_value != expected_value:
            raise BenchmarkRejected(f"{label}: producer input {actual_field} differs from frozen case")


def _validate_producer_input(ref: object, case: dict, loaded: dict, label: str) -> tuple[Path, str]:
    path, producer = _read_pinned_json(ref, label)
    schema = producer.get("schema")
    if schema == "h3.rife-rgba-native-cee-g1-input.v1":
        if producer.get("pair_id") != case["id"]:
            raise BenchmarkRejected(f"{label}: pair identity differs from benchmark case")
        frames = producer.get("frames")
        if not isinstance(frames, dict):
            raise BenchmarkRejected(f"{label}: frames object missing")
        for side in ("a", "b", "truth"):
            frame = frames.get(side)
            expected = loaded[side]
            if not isinstance(frame, dict):
                raise BenchmarkRejected(f"{label}: {side} frame missing")
            if (frame.get("source_sha256") != expected["sha256"]
                    or frame.get("source_pixels") != expected["pixels"]
                    or frame.get("origin_pixels") != expected["origin"]):
                raise BenchmarkRejected(f"{label}: {side} differs from frozen benchmark input")
    elif schema == "h3.interpolation.batch.v1":
        pairs = producer.get("pairs")
        if not isinstance(pairs, list):
            raise BenchmarkRejected(f"{label}: pairs array missing")
        pair = next((item for item in pairs if isinstance(item, dict) and item.get("id") == case["id"]), None)
        if pair is None:
            raise BenchmarkRejected(f"{label}: requested pair is absent")
        _compare_input_frames(pair.get("a", {}), loaded["a"], label + ".a")
        _compare_input_frames(pair.get("b", {}), loaded["b"], label + ".b")
        truth = pair.get("truth")
        if not isinstance(truth, dict):
            raise BenchmarkRejected(f"{label}: held-out truth is absent")
        _compare_input_frames(truth, loaded["truth"], label + ".truth")
        if truth.get("time") != case["requested_time"]:
            raise BenchmarkRejected(f"{label}: requested time differs from frozen case")
    else:
        raise BenchmarkRejected(f"{label}: unsupported producer input schema {schema!r}")
    return path, str(ref["sha256"])


# Bound diagnostic-only tensor memory before reading/allocating arrays.
MAX_ALPHA_FLOW_PIXELS = 1024 * 1024


def _alpha_flow_measurements(a: np.ndarray, b: np.ndarray, truth: np.ndarray,
                             candidate: np.ndarray, flow: np.ndarray,
                             mask: np.ndarray) -> dict:
    """Decompose L1 alpha error for fixed flow; oracle is never exported as art.

    Any convex blend lies between the two warped alpha values. Clamping the
    held-out alpha to that interval is the pointwise minimum achievable L1
    error with these fixed endpoints/flow. The remainder is mask excess,
    not proof that a usable mask can be inferred without held-out data.
    """
    frames = (a, b, truth, candidate)
    if (not all(isinstance(x, np.ndarray) and x.dtype == np.uint8 and x.ndim == 3
                and x.shape[-1] == 4 for x in frames)
            or any(x.shape != a.shape for x in frames)):
        raise BenchmarkRejected('alpha-flow: matching RGBA8 frames required')
    h, w = a.shape[:2]
    if (h < 1 or w < 1 or not isinstance(flow, np.ndarray) or flow.ndim != 3
            or flow.shape[0] != 4 or not isinstance(mask, np.ndarray) or mask.ndim != 2
            or flow.shape[1:] != mask.shape):
        raise BenchmarkRejected('alpha-flow: planar flow/mask shape mismatch')
    fh, fw = mask.shape
    if (fh < h or fw < w or max(fh, fw) > MAX_DIMENSION
            or fh * fw > MAX_ALPHA_FLOW_PIXELS):
        raise BenchmarkRejected('alpha-flow: bounded tensor geometry required')
    if (not np.issubdtype(flow.dtype, np.floating)
            or not np.issubdtype(mask.dtype, np.floating)
            or not np.isfinite(flow).all() or not np.isfinite(mask).all()
            or np.any(np.abs(flow) > 100000) or np.any(mask < 0) or np.any(mask > 1)):
        raise BenchmarkRejected('alpha-flow: invalid flow or blend mask values')
    yy, xx = np.mgrid[:h, :w].astype(np.float64)

    def warp(alpha, dx, dy):
        source = np.zeros((fh, fw), np.float64)
        source[:h, :w] = alpha.astype(np.float64) / 255.0
        # Backward bilinear sampling, zero padding then clamped tensor border.
        sx = np.clip(xx + dx, 0, fw - 1)
        sy = np.clip(yy + dy, 0, fh - 1)
        x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
        x1, y1 = np.minimum(x0 + 1, fw - 1), np.minimum(y0 + 1, fh - 1)
        ux, uy = sx - x0, sy - y0
        return ((1 - ux) * source[y0, x0] + ux * source[y0, x1]) * (1 - uy) + (
            (1 - ux) * source[y1, x0] + ux * source[y1, x1]) * uy

    wa = warp(a[:, :, 3], flow[0, :h, :w], flow[1, :h, :w])
    wb = warp(b[:, :, 3], flow[2, :h, :w], flow[3, :h, :w])
    m = mask[:h, :w].astype(np.float64)
    mixed = np.clip(m * wa + (1 - m) * wb, 0, 1)
    quantized = np.floor(mixed * 255 + 0.5).astype(np.uint8)
    replay_delta = np.abs(quantized.astype(np.int16) - candidate[:, :, 3].astype(np.int16))
    # One LSB permits float32/float64 rounding only; the caller cannot loosen it.
    if int(replay_delta.max()) > 1:
        raise BenchmarkRejected('alpha-flow: candidate alpha replay differs by more than one LSB')
    target = truth[:, :, 3].astype(np.float64) / 255.0
    lower, upper = np.minimum(wa, wb), np.maximum(wa, wb)
    best = np.clip(target, lower, upper)
    error = np.abs(mixed - target)
    bound = np.abs(best - target)
    excess = error - bound
    if float(excess.min()) < -1e-12:
        raise BenchmarkRejected('alpha-flow: inconsistent convex-blend error decomposition')
    excess = np.maximum(excess, 0)
    total = float(error.sum())
    return {
        'status': 'DIAGNOSTIC_ONLY_REFERENCE_BOUND',
        'continuous_alpha_mae_8bit': float(error.mean() * 255),
        'candidate_alpha_mae_8bit': float(np.abs(candidate[:, :, 3].astype(np.int16)
                                                - truth[:, :, 3].astype(np.int16)).mean()),
        'fixed_flow_oracle_lower_bound_mae_8bit': float(bound.mean() * 255),
        'blend_weight_excess_mae_8bit': float(excess.mean() * 255),
        'fixed_flow_lower_bound_error_fraction': float(bound.sum() / total) if total > 0 else None,
        'candidate_alpha_replay_max_abs_u8': int(replay_delta.max()),
        'candidate_alpha_replay_nonidentical_pixels': int(np.count_nonzero(replay_delta)),
        'truth_positive_both_warp_zero': int(((target > 0) & (upper == 0)).sum()),
        'truth_zero_both_warp_positive': int(((target == 0) & (lower > 0)).sum()),
        'tensor_pixels': [fw, fh], 'roi_pixels': [w, h],
        'uses_heldout_reference': True, 'production_candidate_generated': False,
        'art_approved': False, 'native_timing_verified': False,
        'limit': 'Reference-oracle lower bound only; not a production candidate or a causal proof of occlusion. '
                 'Fixed-flow bound includes endpoint-alpha and pose/timing mismatch. No image is generated.',
    }


def _validate_alpha_flow_diagnostic(value: object, case: dict, loaded: dict,
                                     artifact: dict, report: dict,
                                     backend_id: str, label: str) -> dict:
    fields = {'schema', 'layout', 'sampling', 'padding', 'tensor_pixels',
              'tensor_pixels_pointer', 'roi_pointer', 'endpoint_rgba_sha256_pointers', 'flow', 'mask'}
    if not isinstance(value, dict) or set(value) != fields or backend_id != 'common-flow':
        raise BenchmarkRejected(f'{label}: exact common-flow diagnostic contract required')
    if (value['schema'] != 'h3.alpha-flow-diagnostic.v1'
            or value['layout'] != 'planar-f32-le-f0x-f0y-f1x-f1y'
            or value['sampling'] != 'backward-bilinear-border'
            or value['padding'] != 'zero-bottom-right'):
        raise BenchmarkRejected(f'{label}: unsupported alpha-flow layout/sampling/padding')
    fw, fh = _int_pair(value['tensor_pixels'], label + '.tensor_pixels', 1)
    roi = case['evaluation_roi']
    if (fw < roi[2] or fh < roi[3] or max(fw, fh) > MAX_DIMENSION
            or fw * fh > MAX_ALPHA_FLOW_PIXELS):
        raise BenchmarkRejected(f'{label}: alpha-flow tensor size exceeds bounded geometry')
    if (_json_pointer(report, value['tensor_pixels_pointer'], label) != [fw, fh]
            or _json_pointer(report, value['roi_pointer'], label) != roi):
        raise BenchmarkRejected(f'{label}: producer tensor geometry/ROI differs')
    pointers = value['endpoint_rgba_sha256_pointers']
    if not isinstance(pointers, dict) or set(pointers) != {'a', 'b'}:
        raise BenchmarkRejected(f'{label}: endpoint RGBA hashes must be bound by the producer')
    endpoints = {side: _place_roi(loaded[side], roi) for side in ('a', 'b')}
    for side, image in endpoints.items():
        if _json_pointer(report, pointers[side], label) != sha256_bytes(image.tobytes()):
            raise BenchmarkRejected(f'{label}: producer endpoint RGBA differs for {side}')
    tensors = {}
    paths = {}
    for name, channels in (('flow', 4), ('mask', 1)):
        ref = value[name]
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'report_sha256_pointer'}:
            raise BenchmarkRejected(f'{label}: exact pinned {name} reference required')
        if _json_pointer(report, ref['report_sha256_pointer'], label) != ref['sha256']:
            raise BenchmarkRejected(f'{label}: producer does not bind {name} tensor SHA')
        size = fw * fh * channels * 4
        path, data = _resolved_file(ref['path'], ref['sha256'], label + '.' + name, size)
        if len(data) != size:
            raise BenchmarkRejected(f'{label}: exact tensor byte length required for {name}')
        tensors[name] = np.frombuffer(data, dtype='<f4').reshape((4, fh, fw) if channels == 4 else (fh, fw))
        paths[name] = str(path)
    result = _alpha_flow_measurements(endpoints['a'], endpoints['b'],
                                      _place_roi(loaded['truth'], roi), _place_roi(artifact, roi),
                                      tensors['flow'], tensors['mask'])
    result.update(flow_path=paths['flow'], flow_sha256=value['flow']['sha256'],
                  mask_path=paths['mask'], mask_sha256=value['mask']['sha256'],
                  tensor_layout=value['layout'], sampling=value['sampling'], padding=value['padding'])
    return result


def _validate_backend_candidate(candidate: object, case: dict, loaded: dict, label: str) -> dict:
    if not isinstance(candidate, dict):
        raise BenchmarkRejected(f"{label}: backend candidate object required")
    backend_id = candidate.get("id")
    if not isinstance(backend_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", backend_id):
        raise BenchmarkRejected(f"{label}: invalid backend id")
    status = candidate.get("status")
    if "alpha_flow_diagnostic" in candidate and status != GOOD_CANDIDATE_STATUS:
        raise BenchmarkRejected(f"{label}: a no-output backend cannot carry flow diagnostics")
    if status == GOOD_CANDIDATE_STATUS:
        artifact = _load_rgba_frame(candidate.get("image"), case["canvas_pixels"], label + ".image")
        if artifact["key"] != loaded["truth"]["key"]:
            raise BenchmarkRejected(f"{label}: candidate slot differs from held-out target")
        if artifact["sha256"] == loaded["truth"]["sha256"]:
            raise BenchmarkRejected(f"{label}: candidate is byte-identical to held-out truth")
        _place_roi(artifact, case["evaluation_roi"])

        identity = candidate.get("backend_identity")
        if not isinstance(identity, dict):
            raise BenchmarkRejected(f"{label}: backend identity required")
        if not isinstance(identity.get("source_repo"), str) or not identity["source_repo"]:
            raise BenchmarkRejected(f"{label}: source repository required")
        source_commit = identity.get("source_commit")
        if not isinstance(source_commit, str) or not source_commit:
            raise BenchmarkRejected(f"{label}: pinned source commit required")
        executable = identity.get("executable")
        if not isinstance(executable, dict):
            raise BenchmarkRejected(f"{label}: executable path and SHA256 required")
        executable_path, _ = _resolved_file(executable.get("path"), executable.get("sha256"),
                                            label + ".executable", MAX_PNG_BYTES)
        engine_sha = executable["sha256"]
        raw_models = identity.get("model_files")
        if not isinstance(raw_models, dict) or not raw_models:
            raise BenchmarkRejected(f"{label}: at least one pinned model file is required")
        model_shas = {}
        model_paths = {}
        for name, model_ref in raw_models.items():
            if not isinstance(name, str) or not isinstance(model_ref, dict):
                raise BenchmarkRejected(f"{label}: malformed model file reference")
            model_path, _ = _resolved_file(model_ref.get("path"), model_ref.get("sha256"),
                                           label + ".model." + name, MAX_PNG_BYTES)
            model_paths[name] = model_path
            model_shas[name] = model_ref["sha256"]
        raw_sources = identity.get("implementation_sources", {})
        if not isinstance(raw_sources, dict):
            raise BenchmarkRejected(f"{label}: implementation_sources must be an object")
        source_shas = {}
        source_paths = {}
        for name, source_ref in raw_sources.items():
            if not isinstance(name, str) or not isinstance(source_ref, dict):
                raise BenchmarkRejected(f"{label}: malformed implementation source reference")
            source_path, _ = _resolved_file(source_ref.get("path"), source_ref.get("sha256"),
                                            label + ".source." + name, MAX_PNG_BYTES)
            source_paths[name] = source_path
            source_shas[name] = source_ref["sha256"]

        producer = candidate.get("producer")
        if not isinstance(producer, dict):
            raise BenchmarkRejected(f"{label}: producer evidence binding required")
        input_path, input_sha = _validate_producer_input(
            producer.get("input_manifest"), case, loaded, label + ".producer_input")
        report_path, report = _read_pinned_json(producer.get("run_report"), label + ".run_report")
        if _json_pointer(report, producer.get("candidate_sha256_pointer"), label + ".candidate_sha256") != artifact["sha256"]:
            raise BenchmarkRejected(f"{label}: run report does not bind this candidate PNG")
        if _json_pointer(report, producer.get("input_manifest_sha256_pointer"), label + ".input_manifest_sha256") != input_sha:
            raise BenchmarkRejected(f"{label}: run report does not bind the frozen input manifest")
        if producer.get("status_pointer") is not None:
            report_status = _json_pointer(report, producer["status_pointer"], label + ".status")
            if report_status not in (GOOD_CANDIDATE_STATUS, "REPRODUCED", "CANDIDATE_REVIEW_REQUIRED"):
                raise BenchmarkRejected(f"{label}: producer report status is not a candidate")
        pointers = producer.get("backend_identity_pointers", {})
        if not isinstance(pointers, dict):
            raise BenchmarkRejected(f"{label}: backend identity pointers must be an object")
        if pointers.get("source_commit") is not None and _json_pointer(
                report, pointers["source_commit"], label + ".source_commit") != source_commit:
            raise BenchmarkRejected(f"{label}: producer source commit differs")
        if pointers.get("executable_sha256") is None or _json_pointer(
                report, pointers["executable_sha256"], label + ".executable_sha256") != engine_sha:
            raise BenchmarkRejected(f"{label}: producer executable SHA differs")
        model_pointers = pointers.get("model_sha256", {})
        if not isinstance(model_pointers, dict) or set(model_pointers) != set(model_shas):
            raise BenchmarkRejected(f"{label}: model identity pointers must pin every model file")
        for name, pointer in model_pointers.items():
            if name not in model_shas or _json_pointer(report, pointer, label + ".model_sha256") != model_shas[name]:
                raise BenchmarkRejected(f"{label}: producer model SHA differs for {name}")
        source_pointers = pointers.get("implementation_sources", {})
        if not isinstance(source_pointers, dict) or set(source_pointers) != set(source_shas):
            raise BenchmarkRejected(f"{label}: producer must pin every implementation source")
        for name, pointer in source_pointers.items():
            if _json_pointer(report, pointer, label + ".implementation_sources") != source_shas[name]:
                raise BenchmarkRejected(f"{label}: producer source SHA differs for {name}")

        requested_time = candidate.get("requested_time")
        if (type(requested_time) not in (int, float) or not math.isfinite(requested_time)
                or float(requested_time) != case["requested_time"]):
            raise BenchmarkRejected(f"{label}: candidate requested_time differs from the held-out case")
        if producer.get("requested_time_pointer") is not None and _json_pointer(
                report, producer["requested_time_pointer"], label + ".requested_time") != requested_time:
            raise BenchmarkRejected(f"{label}: producer report requested_time differs")

        return {"id": backend_id, "status": status, "image": artifact,
                "alpha_flow_diagnostic": (_validate_alpha_flow_diagnostic(
                    candidate["alpha_flow_diagnostic"], case, loaded, artifact, report, backend_id, label)
                    if "alpha_flow_diagnostic" in candidate else None),
                "backend_identity": {"source_repo": identity["source_repo"], "source_commit": source_commit,
                                     "executable": {"path": str(executable_path), "sha256": engine_sha},
                                     "model_files": {name: {"path": str(model_paths[name]), "sha256": digest}
                                                     for name, digest in sorted(model_shas.items())},
                                     "implementation_sources": {
                                         name: {"path": str(source_paths[name]), "sha256": digest}
                                         for name, digest in sorted(source_shas.items())}},
                "producer": {
                    "input_manifest": str(input_path), "input_manifest_sha256": input_sha,
                    "run_report": str(report_path), "run_report_sha256": producer["run_report"]["sha256"]}}

    if status not in NO_OUTPUT_STATUSES:
        raise BenchmarkRejected(f"{label}: unsupported backend status {status!r}")
    reason = candidate.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BenchmarkRejected(f"{label}: no-output status requires a reason")
    if candidate.get("image") is not None:
        raise BenchmarkRejected(f"{label}: no-output status cannot name an image")
    evidence = candidate.get("evidence")
    evidence_path = None
    if evidence is not None:
        evidence_path, evidence_json = _read_pinned_json(evidence, label + ".evidence")
        status_pointer = candidate.get("evidence_status_pointer")
        if status_pointer is not None and _json_pointer(
                evidence_json, status_pointer, label + ".evidence_status") != status:
            raise BenchmarkRejected(f"{label}: evidence status differs from declared backend status")
    elif status in ("NEEDS_KEYFRAME", "BACKEND_FAILED"):
        raise BenchmarkRejected(f"{label}: {status} requires pinned failure evidence")
    if status in ("NEEDS_KEYFRAME", "BACKEND_FAILED") and candidate.get("evidence_status_pointer") is None:
        raise BenchmarkRejected(f"{label}: failure evidence must identify the status field")
    return {"id": backend_id, "status": status, "reason": reason.strip(),
            "evidence": ({"path": str(evidence_path), "sha256": evidence["sha256"]}
                         if evidence_path is not None else None)}


def _validate_annotations(value: object, canvas: list[int], roi: list[int], label: str) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(key not in REQUIRED_SEMANTIC_CATEGORIES for key in value):
        raise BenchmarkRejected(f"{label}: semantic annotations must use the supported category names")
    result = {}
    for category in REQUIRED_SEMANTIC_CATEGORIES:
        annotation = value.get(category)
        if annotation is None:
            result[category] = {"status": "OPEN_NA", "reason": "No SHA-pinned semantic annotation"}
            continue
        if not isinstance(annotation, dict):
            raise BenchmarkRejected(f"{label}.{category}: annotation object required")
        status = annotation.get("status")
        if status == "N/A":
            reason = annotation.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise BenchmarkRejected(f"{label}.{category}: N/A requires a reason")
            result[category] = {"status": "OPEN_NA", "reason": reason.strip()}
            continue
        if status != "PRESENT":
            raise BenchmarkRejected(f"{label}.{category}: status must be PRESENT or N/A")
        evidence_path, evidence = _read_pinned_json(annotation.get("evidence"), label + "." + category + ".evidence")
        del evidence
        category_result = {"status": "PRESENT_LABELLED", "evidence": str(evidence_path),
                           "evidence_sha256": annotation["evidence"]["sha256"]}
        mask_spec = annotation.get("mask")
        if category in MASK_CATEGORIES and mask_spec is None:
            raise BenchmarkRejected(f"{label}.{category}: a SHA-pinned region mask is required")
        if mask_spec is not None:
            mask = _load_l_mask(mask_spec, canvas, label + "." + category + ".mask")
            roi_mask = _place_mask_roi(mask, roi)
            category_result["mask"] = roi_mask
            category_result["mask_sha256"] = mask["sha256"]
        result[category] = category_result
    return result


def _validate_review_tiers(value: object, canvas: list[int], truth: dict, label: str) -> dict:
    """Load optional, explicitly pinned raw native-tier review artifacts."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or value.keys() - {"native_1x", "native_3x"}:
        raise BenchmarkRejected(f"{label}: only native_1x and native_3x review tiers are supported")
    result = {}
    for tier, scale in (("native_1x", 1), ("native_3x", 3)):
        entry = value.get(tier)
        if entry is None:
            result[tier] = {"status": "OPEN_NA", "reason": "No SHA-pinned artifact and evidence"}
            continue
        if not isinstance(entry, dict):
            raise BenchmarkRejected(f"{label}.{tier}: review tier object required")
        status = entry.get("status")
        if status == "N/A":
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise BenchmarkRejected(f"{label}.{tier}: N/A requires a reason")
            result[tier] = {"status": "OPEN_NA", "reason": reason.strip()}
            continue
        required = {"status", "image", "evidence", "artifact_sha256_pointer"}
        if tier == "native_1x":
            required.add("review_status_pointer")
        if status != "PRESENT" or entry.keys() != required:
            raise BenchmarkRejected(f"{label}.{tier}: PRESENT requires exactly {sorted(required)}")
        scaled_canvas = [canvas[0] * scale, canvas[1] * scale]
        artifact = _load_rgba_frame(entry.get("image"), scaled_canvas, f"{label}.{tier}.image")
        if artifact["key"] != truth["key"]:
            raise BenchmarkRejected(f"{label}.{tier}: artifact H3 slot differs from held-out truth")
        if artifact["pixels"] != [value * scale for value in truth["pixels"]]:
            raise BenchmarkRejected(f"{label}.{tier}: raw PNG dimensions are not exactly {scale}x held-out geometry")
        if artifact["origin"] != [value * scale for value in truth["origin"]]:
            raise BenchmarkRejected(f"{label}.{tier}: raw origin is not exactly {scale}x held-out origin")
        evidence_path, evidence = _read_pinned_json(entry.get("evidence"), f"{label}.{tier}.evidence")
        artifact_pointer = entry.get("artifact_sha256_pointer")
        if _json_pointer(evidence, artifact_pointer, f"{label}.{tier}.artifact_sha256") != artifact["sha256"]:
            raise BenchmarkRejected(f"{label}.{tier}: evidence does not bind this exact PNG SHA")
        if tier == "native_1x":
            review_pointer = entry.get("review_status_pointer")
            if _json_pointer(evidence, review_pointer, f"{label}.{tier}.review_status") != "HUMAN_ART_APPROVED":
                raise BenchmarkRejected(f"{label}.{tier}: formal human art-review approval evidence is required")
        result[tier] = {"status": "HUMAN_ART_APPROVED" if tier == "native_1x" else "RAW_3X_REVIEW_REQUIRED",
                        "image": artifact, "image_sha256": artifact["sha256"],
                        "evidence": str(evidence_path), "evidence_sha256": entry["evidence"]["sha256"],
                        "canvas_pixels": scaled_canvas}
    return result


def _validate_manifest(path_value: Path, expected_sha: str, operation_id: str) -> tuple[Path, dict, list[dict]]:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", operation_id):
        raise BenchmarkRejected("operationId must be 1..80 ASCII letters, digits, '_' or '-'")
    manifest_path, data = _resolved_file(str(path_value), expected_sha, "benchmark manifest", MAX_JSON_BYTES)
    base = OUTPUT_ROOT.resolve(strict=False)
    # Manifests are experiment inputs owned by this isolated evidence directory.
    resolved_base = base
    if not _inside(manifest_path, resolved_base):
        raise BenchmarkRejected("manifest must live below build/experiments/transparent-benchmark")
    try:
        manifest = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkRejected(f"benchmark manifest is invalid UTF-8 JSON: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != "h3.transparent-interpolation-benchmark.v1":
        raise BenchmarkRejected("unsupported transparent benchmark manifest schema")
    if manifest.get("operation_id") != operation_id:
        raise BenchmarkRejected("operationId differs from the frozen manifest")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        raise BenchmarkRejected("manifest requires 1..32 cases")
    ids: set[str] = set()
    validated_cases = []
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            raise BenchmarkRejected(f"{label}: case object required")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case_id) or case_id in ids:
            raise BenchmarkRejected(f"{label}: invalid or duplicate case id")
        ids.add(case_id)
        canvas = _int_pair(case.get("canvas_pixels"), label + ".canvas_pixels", 1)
        if max(canvas) > MAX_DIMENSION or canvas[0] * canvas[1] > MAX_PIXELS:
            raise BenchmarkRejected(f"{label}: canvas exceeds bounded benchmark size")
        roi = _int_quad(case.get("evaluation_roi"), label + ".evaluation_roi", 0)
        if (roi[2] <= 0 or roi[3] <= 0
                or roi[0] + roi[2] > canvas[0]
                or roi[1] + roi[3] > canvas[1]):
            raise BenchmarkRejected(f"{label}: invalid frozen evaluation ROI")
        requested_time = case.get("requested_time")
        if (type(requested_time) not in (float, int) or not math.isfinite(requested_time)
                or not 0 < requested_time < 1):
            raise BenchmarkRejected(f"{label}: requested_time must be finite and between 0 and 1")
        time_semantics = case.get("time_semantics")
        if time_semantics not in ("POSE_ONLY", "TIMING_VERIFIED"):
            raise BenchmarkRejected(f"{label}: explicit time_semantics required")
        if time_semantics == "TIMING_VERIFIED":
            timing_evidence_path, _ = _read_pinned_json(case.get("timing_evidence"), label + ".timing_evidence")
        else:
            timing_evidence_path = None

        endpoints = case.get("endpoints")
        if not isinstance(endpoints, dict):
            raise BenchmarkRejected(f"{label}: endpoints object required")
        loaded = {side: _load_rgba_frame(endpoints.get(side), canvas, label + "." + side)
                  for side in ("a", "b")}
        truth = _load_rgba_frame(case.get("truth"), canvas, label + ".truth")
        if loaded["a"]["key"][:3] != loaded["b"]["key"][:3] or truth["key"][:3] != loaded["a"]["key"][:3]:
            raise BenchmarkRejected(f"{label}: endpoints and held-out truth must share one resource/action group")
        if truth["key"] in (loaded["a"]["key"], loaded["b"]["key"]):
            raise BenchmarkRejected(f"{label}: held-out truth must be distinct from both endpoints")
        computed_roi = rife.common_roi(
            {"origin": loaded["a"]["origin"], "pixels": loaded["a"]["pixels"]},
            {"origin": loaded["b"]["origin"], "pixels": loaded["b"]["pixels"]}, canvas)
        if computed_roi != roi:
            raise BenchmarkRejected(f"{label}: evaluation_roi does not equal endpoint union plus the existing 16px margin")
        loaded["truth"] = truth
        for name, frame in loaded.items():
            if frame["origin"][0] < roi[0] or frame["origin"][1] < roi[1] or (
                    frame["origin"][0] + frame["pixels"][0] > roi[0] + roi[2]
                    or frame["origin"][1] + frame["pixels"][1] > roi[1] + roi[3]):
                raise BenchmarkRejected(f"{label}.{name}: source frame is outside evaluation ROI")

        annotations = _validate_annotations(case.get("semantic_annotations"), canvas, roi, label + ".semantic_annotations")
        review_tiers = _validate_review_tiers(case.get("review_tiers"), canvas, truth, label + ".review_tiers")
        candidate_values = case.get("backends")
        if not isinstance(candidate_values, list) or not candidate_values:
            raise BenchmarkRejected(f"{label}: at least one backend entry required")
        candidate_ids: set[str] = set()
        candidates = []
        for candidate_index, candidate in enumerate(candidate_values):
            item = _validate_backend_candidate(candidate, case, loaded, f"{label}.backends[{candidate_index}]")
            if item["id"] in candidate_ids:
                raise BenchmarkRejected(f"{label}: duplicate backend id {item['id']}")
            candidate_ids.add(item["id"])
            candidates.append(item)
        required_backends = {"split-pass", "common-flow", "mesh"}
        if not required_backends.issubset(candidate_ids):
            raise BenchmarkRejected(f"{label}: split-pass, common-flow, and mesh entries are required (use N/A when absent)")
        validated_cases.append({"id": case_id, "canvas_pixels": canvas, "evaluation_roi": roi,
                                "requested_time": float(requested_time), "time_semantics": time_semantics,
                                "timing_evidence": str(timing_evidence_path) if timing_evidence_path else None,
                                "frames": loaded, "annotations": annotations, "review_tiers": review_tiers,
                                "backends": candidates})
    return manifest_path, manifest, validated_cases


def _alpha_bin_metrics(candidate: np.ndarray, truth: np.ndarray) -> dict:
    ca = candidate[:, :, 3]
    ta = truth[:, :, 3]
    bins = {"alpha_1": (ta == 1), "alpha_2_15": ((ta >= 2) & (ta <= 15)),
            "alpha_16_127": ((ta >= 16) & (ta <= 127)),
            "alpha_128_254": ((ta >= 128) & (ta <= 254)), "alpha_255": (ta == 255)}
    result = {}
    for name, mask in bins.items():
        count = int(mask.sum())
        result[name] = {"truth_pixels": count,
                        "alpha_mae_8bit": float(np.abs(ca.astype(np.int16) - ta.astype(np.int16))[mask].mean()) if count else None,
                        "truth_nonzero_candidate_zero": int(((ca == 0) & mask).sum()) if count else 0,
                        "candidate_alpha1": int(((ca == 1) & mask).sum()) if count else 0}
    return result


def _composite_metrics(candidate: np.ndarray, truth: np.ndarray) -> dict:
    ca = candidate[:, :, 3:4].astype(np.float32) / 255.0
    ta = truth[:, :, 3:4].astype(np.float32) / 255.0
    crgb = rife.srgb_to_linear(candidate[:, :, :3].astype(np.float32) / 255.0)
    trgb = rife.srgb_to_linear(truth[:, :, :3].astype(np.float32) / 255.0)
    visible = (candidate[:, :, 3] > 0) | (truth[:, :, 3] > 0)
    output = {}
    for name, background in (("black", 0.0), ("white", 1.0)):
        c = crgb * ca + background * (1.0 - ca)
        t = trgb * ta + background * (1.0 - ta)
        error = np.abs(c - t)
        samples = int(visible.sum())
        output[name] = {"linear_rgb_mae_visible_union": float(error[visible].mean()) if samples else 0.0,
                        "linear_rgb_max_abs_visible_union": float(error[visible].max()) if samples else 0.0,
                        "visible_pixels": samples}
    return output


def _metrics(candidate: np.ndarray, truth: np.ndarray) -> dict:
    ca, ta = candidate[:, :, 3], truth[:, :, 3]
    all_error = np.abs(ca.astype(np.int16) - ta.astype(np.int16))
    return {
        "existing_rife_metric": rife.metric(candidate, truth),
        "alpha": {
            "mae_8bit_full_roi": float(all_error.mean()),
            "truth_nonzero_pixels": int((ta > 0).sum()),
            "candidate_nonzero_pixels": int((ca > 0).sum()),
            "truth_nonzero_candidate_zero": int(((ta > 0) & (ca == 0)).sum()),
            "truth_alpha1_pixels": int((ta == 1).sum()),
            "candidate_alpha1_pixels": int((ca == 1).sum()),
            "truth_alpha1_candidate_zero": int(((ta == 1) & (ca == 0)).sum()),
            "truth_alpha1_candidate_one": int(((ta == 1) & (ca == 1)).sum()),
            "truth_alpha_distribution": {
                "zero": int((ta == 0).sum()), "one": int((ta == 1).sum()),
                "two_to_fifteen": int(((ta >= 2) & (ta <= 15)).sum()),
                "sixteen_to_127": int(((ta >= 16) & (ta <= 127)).sum()),
                "128_to_254": int(((ta >= 128) & (ta <= 254)).sum()),
                "255": int((ta == 255).sum()),
            },
            "truth_bin_errors": _alpha_bin_metrics(candidate, truth),
        },
        "linear_composite_rgb": _composite_metrics(candidate, truth),
    }


def _region_metrics(candidate: np.ndarray, truth: np.ndarray, mask: np.ndarray) -> dict:
    selected = mask > 0
    count = int(selected.sum())
    if count == 0:
        return {"status": "N/A_EMPTY_SEMANTIC_MASK", "pixels": 0}
    ca, ta = candidate[:, :, 3], truth[:, :, 3]
    inter = selected & (ca > 16) & (ta > 16)
    union = selected & ((ca > 16) | (ta > 16))
    return {"status": "MEASURED_DIAGNOSTIC", "pixels": count,
            "alpha_iou_gt16": float(inter.sum() / max(1, union.sum())),
            "alpha_mae_8bit": float(np.abs(ca.astype(np.int16) - ta.astype(np.int16))[selected].mean()),
            "truth_nonzero_candidate_zero": int((selected & (ta > 0) & (ca == 0)).sum())}


def _build_measurements(manifest_path: Path, manifest_sha: str, manifest: dict, cases: list[dict]) -> dict:
    results = []
    open_categories: set[str] = set()
    for case in cases:
        roi = case["evaluation_roi"]
        truth = _place_roi(case["frames"]["truth"], roi)
        alpha = truth[:, :, 3]
        coverage = {
            "semi_transparent": {"status": "MEASURED_SOURCE_ALPHA" if np.any((alpha > 0) & (alpha < 255)) else "N/A_NO_SEMITRANSPARENT_PIXELS",
                                 "truth_pixels": int(((alpha > 0) & (alpha < 255)).sum())},
            "alpha1": {"status": "MEASURED_SOURCE_ALPHA1" if np.any(alpha == 1) else "N/A_NO_ALPHA1_PIXELS",
                       "truth_pixels": int((alpha == 1).sum())},
        }
        for category in REQUIRED_SEMANTIC_CATEGORIES:
            annotation = case["annotations"][category]
            if annotation["status"] == "OPEN_NA":
                coverage[category] = {"status": "OPEN_NA", "reason": annotation["reason"]}
                open_categories.add(category)
            else:
                coverage[category] = {"status": "PRESENT_LABELLED", "evidence": annotation["evidence"],
                                      "evidence_sha256": annotation["evidence_sha256"]}

        review_tiers = {}
        for tier, artifact in case["review_tiers"].items():
            if artifact["status"] == "OPEN_NA":
                review_tiers[tier] = {"status": artifact["status"], "reason": artifact["reason"]}
            else:
                frame = artifact["image"]
                review_tiers[tier] = {
                    "status": artifact["status"], "path": str(frame["path"]),
                    "image_sha256": artifact["image_sha256"], "pixels": frame["pixels"],
                    "origin_pixels": frame["origin"], "canvas_pixels": artifact["canvas_pixels"],
                    "evidence": artifact["evidence"], "evidence_sha256": artifact["evidence_sha256"],
                }

        backend_results = []
        for backend in case["backends"]:
            item = {"id": backend["id"], "status": backend["status"]}
            if backend["status"] == GOOD_CANDIDATE_STATUS:
                candidate = _place_roi(backend["image"], roi)
                if candidate.shape != truth.shape:
                    raise BenchmarkRejected(f"{case['id']}/{backend['id']}: registered candidate geometry mismatch")
                item.update({"candidate_png_sha256": backend["image"]["sha256"],
                             "origin_pixels": backend["image"]["origin"],
                             "pixels": backend["image"]["pixels"],
                             "backend_identity": backend["backend_identity"],
                             "producer": backend["producer"],
                             "metrics": _metrics(candidate, truth)})
                if backend.get("alpha_flow_diagnostic") is not None:
                    item["alpha_flow_diagnostic"] = backend["alpha_flow_diagnostic"]
                for category in REQUIRED_SEMANTIC_CATEGORIES:
                    annotation = case["annotations"][category]
                    if annotation["status"] != "PRESENT_LABELLED":
                        continue
                    mask = annotation.get("mask")
                    if mask is None:
                        item.setdefault("semantic_metrics", {})[category] = {
                            "status": "LABELLED_SAMPLE_ONLY", "metrics_source": "whole frozen evaluation ROI"}
                    else:
                        item.setdefault("semantic_metrics", {})[category] = _region_metrics(candidate, truth, mask)
            else:
                item["reason"] = backend["reason"]
                if backend.get("evidence"):
                    item["evidence"] = backend["evidence"]
            backend_results.append(item)
        results.append({"id": case["id"], "resource_group_key": case["frames"]["truth"]["key"][:3],
                        "canvas_pixels": case["canvas_pixels"], "evaluation_roi": roi,
                        "requested_time": case["requested_time"], "time_semantics": case["time_semantics"],
                        "truth_is_pose_only_when_time_unverified": case["time_semantics"] == "POSE_ONLY",
                        "frame_sha256": {name: case["frames"][name]["sha256"] for name in ("a", "truth", "b")},
                        "coverage": coverage, "review_tiers": review_tiers, "backends": backend_results})
    measurements = {
        "schema": "h3.transparent-interpolation-benchmark.measurements.v1",
        "operation_id": manifest["operation_id"],
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "case_count": len(results),
        "cases": results,
        "open_semantic_categories": sorted(open_categories),
        "authority": {"candidate_generation_performed": False, "model_inference_performed": False,
                      "human_approved": False, "art_approved": False,
                      "native_timing_verified": all(case["time_semantics"] == "TIMING_VERIFIED" for case in cases),
                      "runtime_accepted": False, "game_deployed": False,
                      "model_native_output": False},
        "status": "COMPLETED_REVIEW_REQUIRED",
        "production_qualification": "OPEN",
    }
    return measurements


def _format_metric(value: object, digits: int = 6) -> str:
    if type(value) in (float, int) and math.isfinite(float(value)):
        return f"{float(value):.{digits}f}"
    return "N/A"


def _positioned_image(frame: dict, *, css_class: str, alt: str) -> str:
    uri = html.escape(frame["path"].as_uri(), quote=True)
    title = html.escape(str(frame["path"]), quote=True)
    ox, oy = frame["origin"]
    width, height = frame["pixels"]
    return (f'<img class="{css_class}" src="{uri}" alt="{html.escape(alt, quote=True)}" '
            f'title="{title}" draggable="false" '
            f'style="left:{ox}px;top:{oy}px;width:{width}px;height:{height}px">')


def _review_tile(frame: dict, canvas: list[int], title: str, *, truth_overlay: dict | None = None,
                 tier_note: str | None = None) -> str:
    width, height = canvas
    pieces = [f'<article class="tile"><h3>{html.escape(title)}</h3>',
              '<div class="viewport"><div class="canvas" '
              f'style="width:{width}px;height:{height}px">',
              _positioned_image(frame, css_class="sprite", alt=title)]
    if truth_overlay is not None:
        pieces.append(_positioned_image(truth_overlay, css_class="sprite onion-overlay",
                                        alt="Held-out truth overlay"))
    pieces.extend(['</div></div>',
                   f'<p class="geometry">原始 PNG {frame["pixels"][0]}×{frame["pixels"][1]}，'
                   f'原点 ({frame["origin"][0]},{frame["origin"][1]})，'
                   f'画布 {width}×{height} CSS px；不重采样。</p>',
                   f'<p class="digest">SHA-256 {frame["sha256"]}</p>',
                   f'<p class="source"><a href="{html.escape(frame["path"].as_uri(), quote=True)}">'
                   f'打开冻结 PNG</a> · {html.escape(str(frame["path"]))}</p>'])
    if tier_note:
        pieces.append(f'<p class="tier-note">{html.escape(tier_note)}</p>')
    pieces.append('</article>')
    return ''.join(pieces)


def _build_review_html(cases: list[dict], measurements: dict) -> bytes:
    """Build an unscaled, source-linked HTML review view; source PNGs stay untouched."""
    measured_by_id = {case["id"]: case for case in measurements["cases"]}
    cards = []
    for case in cases:
        measured = measured_by_id[case["id"]]
        canvas = case["canvas_pixels"]
        frames = case["frames"]
        truth = frames["truth"]
        case_id = html.escape(case["id"])
        if case["time_semantics"] == "POSE_ONLY":
            timing_note = (f"t={case['requested_time']:.6g} 仅用于姿态留出比较；原生时序未验证，页面不提供动作播放。")
        else:
            timing_note = f"t={case['requested_time']:.6g} 已绑定时序证据；页面不提供动作播放。"

        tiles = [_review_tile(frames["a"], canvas, "端点 A"),
                 _review_tile(truth, canvas, "留出真值"),
                 _review_tile(frames["b"], canvas, "端点 B")]
        backend_measurements = {item["id"]: item for item in measured["backends"]}
        for backend_id in ("split-pass", "common-flow", "mesh"):
            backend = next(item for item in case["backends"] if item["id"] == backend_id)
            measured_backend = backend_measurements[backend_id]
            if backend["status"] == GOOD_CANDIDATE_STATUS:
                tiles.append(_review_tile(backend["image"], canvas, f"{backend_id} · 候选",
                                          truth_overlay=truth,
                                          tier_note="可使用上方叠影控件对照留出真值。"))
            else:
                reason = html.escape(backend["reason"])
                tiles.append(f'<article class="tile unavailable"><h3>{html.escape(backend_id)}</h3>'
                             f'<p>{html.escape(backend["status"])} · {reason}</p></article>')

        tier_tiles = []
        for tier in ("native_1x", "native_3x"):
            artifact = case["review_tiers"][tier]
            if artifact["status"] == "OPEN_NA":
                tier_tiles.append(f'<article class="tile unavailable"><h3>{html.escape(tier)}</h3>'
                                  f'<p>N/A · {html.escape(artifact["reason"])}</p></article>')
                continue
            scale = 1 if tier == "native_1x" else 3
            tier_canvas = [value * scale for value in canvas]
            label = "原生 1X · 已绑定人工审图收据" if tier == "native_1x" else "原生 3X · 原始候选，仍需审图"
            tier_tiles.append(_review_tile(artifact["image"], tier_canvas, label,
                                           tier_note=f"Tier 收据 SHA-256 {artifact['evidence_sha256']}"))

        rows = []
        for backend_id in ("split-pass", "common-flow", "mesh"):
            item = backend_measurements[backend_id]
            metrics = item.get("metrics", {})
            existing = metrics.get("existing_rife_metric", {})
            alpha = metrics.get("alpha", {})
            composite = metrics.get("linear_composite_rgb", {})
            coverage = measured["coverage"]
            status = item["status"]
            rows.append(
                '<tr><th>' + html.escape(backend_id) + '</th>'
                + '<td>' + html.escape(status) + '</td>'
                + '<td>' + _format_metric(existing.get("alpha_iou_at_16")) + '</td>'
                + '<td>' + _format_metric(existing.get("alpha_mae_visible_union")) + '</td>'
                + '<td>' + _format_metric(alpha.get("mae_8bit_full_roi"), 4) + '</td>'
                + '<td>' + str(coverage["semi_transparent"]["truth_pixels"]) + '</td>'
                + '<td>' + str(alpha.get("truth_alpha1_pixels", "N/A")) + '</td>'
                + '<td>' + str(alpha.get("truth_alpha1_candidate_zero", "N/A")) + '</td>'
                + '<td>' + _format_metric(composite.get("black", {}).get("linear_rgb_mae_visible_union")) + '</td>'
                + '<td>' + _format_metric(composite.get("white", {}).get("linear_rgb_mae_visible_union")) + '</td></tr>')

        diagnostic_section = ''
        diagnostic = backend_measurements['common-flow'].get('alpha_flow_diagnostic')
        if diagnostic is not None:
            diagnostic_section = (
                '<aside class="oracle-diagnostic"><h3>固定光流 / 混合权重误差诊断</h3>'
                '<p>使用留出姿态计算下限；not a production candidate。'
                '这不是可用于生产的混合 mask，也不证明原生时序或遮挡语义正确。</p>'
                '<p>连续 alpha MAE: ' + _format_metric(diagnostic['continuous_alpha_mae_8bit'])
                + ' /255；固定光流理论下限: '
                + _format_metric(diagnostic['fixed_flow_oracle_lower_bound_mae_8bit'])
                + ' /255；混合权重超额误差: '
                + _format_metric(diagnostic['blend_weight_excess_mae_8bit'])
                + ' /255；原候选重建最大差: '
                + str(diagnostic['candidate_alpha_replay_max_abs_u8']) + ' LSB。</p></aside>')

        cards.append(
            f'<section class="case"><h2>{case_id}</h2>'
            f'<p>资源/动作槽：{html.escape(json.dumps(truth["key"], ensure_ascii=False))}；'
            f'画布 {canvas[0]}×{canvas[1]}；评估区域 {html.escape(json.dumps(case["evaluation_roi"]))}。</p>'
            f'<p class="timing">{html.escape(timing_note)}</p>'
            '<h3>诊断数值（不自动批准美术）</h3>'
            '<div class="table-wrap"><table><thead><tr><th>后端</th><th>状态</th><th>Alpha IoU@16</th>'
            '<th>可见区 Alpha MAE</th><th>全 ROI Alpha MAE（8 位）</th><th>真值半透明像素</th>'
            '<th>真值 Alpha=1</th><th>候选丢失 Alpha=1</th><th>黑底合成 MAE</th>'
            '<th>白底合成 MAE</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            + diagnostic_section +
            '<h3>冻结的 1X 输入与候选 PNG</h3><div class="tiles">' + ''.join(tiles) + '</div>'
            '<h3>原生分辨率审图材料</h3><div class="tiles">' + ''.join(tier_tiles) + '</div>'
            '<p class="status">候选仍需审图。未执行推理；不代表人工批准、运行接收或游戏部署。</p></section>')

    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>透明补帧基准审图</title>'
            '<style>'
            'body{font:14px/1.45 Segoe UI,Arial,sans-serif;margin:16px;color:#20242a;background:#f3f5f7}'
            'h1{font-size:24px}h2{font-size:20px;margin-top:0}h3{font-size:16px;margin:18px 0 8px}'
            '.toolbar{position:sticky;top:0;z-index:10;background:#fff;border:1px solid #aab2bd;padding:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}'
            '.case{background:#fff;border:1px solid #aab2bd;padding:16px;margin:18px 0}'
            '.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px;align-items:start}'
            '.tile{border:1px solid #c4cbd3;padding:10px;min-width:0}.unavailable{min-height:120px;background:#f7f7f7}'
            '.viewport{max-width:100%;max-height:70vh;overflow:auto;border:1px solid #666}'
            '.canvas{position:relative;overflow:hidden;margin:0;background:#bbb}'
            '.sprite{position:absolute;display:block;max-width:none;max-height:none;padding:0;margin:0;border:0}'
            '.onion-overlay{display:none;opacity:var(--onion-opacity,.5);pointer-events:none}'
            'body.show-onion .onion-overlay{display:block}'
            'body[data-bg="black"] .canvas{background:#000}'
            'body[data-bg="white"] .canvas{background:#fff}'
            'body[data-bg="checker"] .canvas{background-color:#fff;background-image:repeating-conic-gradient(#bbb 0% 25%,#fff 0% 50%);background-size:16px 16px}'
            '.table-wrap{max-width:100%;overflow:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid #98a1aa;padding:6px;text-align:left;white-space:nowrap}'
            '.timing,.status,.tier-note{font-weight:600}.geometry,.digest,.source{overflow-wrap:anywhere;font-size:12px}'
            '.digest{font-family:Consolas,monospace}.status{border-left:4px solid #ca7b00;padding-left:8px}'
            '</style></head><body data-bg="checker">'
            '<h1>透明补帧基准 · 待人工审图</h1>'
            '<p>PNG 按原始尺寸、记录原点放置在同画布。CSS 像素不是正式原生 1X 审图；浏览器 100% 缩放时可查看 1:1 CSS 像素。'
            '页面不重写、不缩放、不滤波或嵌入源 PNG。底色和叠影控件只改变显示。</p>'
            '<div class="toolbar"><strong>显示：</strong>'
            '<button type="button" data-bg="black">黑底</button>'
            '<button type="button" data-bg="white">白底</button>'
            '<button type="button" data-bg="checker">棋盘底</button>'
            '<label><input id="onion-toggle" type="checkbox">在 split/common 候选上叠加留出真值</label>'
            '<label>真值不透明度 <input id="onion-opacity" type="range" min="0" max="100" value="50"></label></div>'
            + ''.join(cards) +
            '<script>document.querySelectorAll("button[data-bg]").forEach((button)=>button.addEventListener("click",()=>{document.body.dataset.bg=button.dataset.bg;}));'
            'document.getElementById("onion-toggle").addEventListener("change",(event)=>document.body.classList.toggle("show-onion",event.target.checked));'
            'document.getElementById("onion-opacity").addEventListener("input",(event)=>document.documentElement.style.setProperty("--onion-opacity",(Number(event.target.value)/100).toString()));'
            '</script></body></html>').encode("utf-8")
    if len(page) > MAX_HTML_BYTES:
        raise BenchmarkRejected(f"review HTML exceeds {MAX_HTML_BYTES} byte limit")
    return page


def _validate_output_directory(outdir: Path, *, must_exist: bool) -> Path:
    base = Path(os.path.abspath(OUTPUT_ROOT))
    requested = outdir if outdir.is_absolute() else PRODUCT / outdir
    requested = Path(os.path.abspath(requested))
    if os.path.normcase(os.path.normpath(os.fspath(requested.parent))) != os.path.normcase(os.path.normpath(os.fspath(base))):
        raise BenchmarkRejected("outdir must be a direct child of build/experiments/transparent-benchmark")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", requested.name):
        raise BenchmarkRejected("outdir leaf must contain only ASCII letters, digits, '_' or '-'")
    try:
        reject_reparse_components(requested, allow_missing_leaf=not must_exist)
    except RuntimeError as error:
        raise BenchmarkRejected(f"unsafe outdir path: {error}") from error
    if must_exist:
        if not requested.is_dir():
            raise BenchmarkRejected("existing outdir must be a direct child directory of the experiment root")
        return requested
    try:
        requested.lstat()
    except FileNotFoundError:
        pass
    else:
        raise BenchmarkRejected("run outdir already exists; inspect or verify it before any new operation")
    return requested


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))


def _read_guarded_output_file(guard, path: Path, limit: int, label: str) -> bytes:
    path = guard.verify_target(path, must_exist=True)
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise BenchmarkRejected(f"{label}: regular file required")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(before) != _file_identity(opened):
                raise BenchmarkRejected(f"{label}: file identity changed before read")
            data = stream.read(limit + 1)
            after_open = os.fstat(stream.fileno())
        after_path = path.stat()
    except OSError as error:
        raise BenchmarkRejected(f"{label}: cannot read guarded output: {error}") from error
    guard.verify_target(path, must_exist=True)
    guard.verify_current()
    if len(data) > limit:
        raise BenchmarkRejected(f"{label}: exceeds {limit} byte limit")
    if not (_file_identity(before) == _file_identity(after_open) == _file_identity(after_path)):
        raise BenchmarkRejected(f"{label}: file identity or bytes changed during guarded read")
    return data


def check(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str) -> dict:
    output_path = _validate_output_directory(outdir, must_exist=False)
    resolved_manifest, manifest, cases = _validate_manifest(manifest_path, manifest_sha256, operation_id)
    return {"status": "CHECKED_WITH_OPEN_NA" if cases else "CHECKED",
            "operation_id": operation_id, "manifest": str(resolved_manifest),
            "manifest_sha256": manifest_sha256, "case_count": len(cases),
            "backend_count": sum(len(case["backends"]) for case in cases),
            "outdir": str(output_path),
            "model_inference_performed": False, "output_written": False}


def run(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str) -> dict:
    output_path = _validate_output_directory(outdir, must_exist=False)
    resolved_manifest, manifest, cases = _validate_manifest(manifest_path, manifest_sha256, operation_id)
    measurements = _build_measurements(resolved_manifest, manifest_sha256, manifest, cases)
    html_payload = _build_review_html(cases, measurements)
    html_digest = sha256_bytes(html_payload)
    report = {"schema": "h3.transparent-interpolation-benchmark.report.v1",
              "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "measurements": measurements,
              "review_html": {"file": REVIEW_HTML_NAME, "sha256": html_digest},
              "measurement_sha256": sha256_bytes(canonical_json(measurements))}
    # Pin the accepted experiment base and fresh operation directory only after
    # every input, candidate, and metric has been materialized from SHA-checked bytes.
    try:
        output_guard = OutputRootGuard.create(output_path, OUTPUT_ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        raise BenchmarkRejected(f"cannot acquire output-root identity guard: {error}") from error
    report_path = output_path / REPORT_NAME
    sidecar_path = output_path / REPORT_SHA_NAME
    partial_sidecar_path = output_path / (REPORT_SHA_NAME + ".partial")
    html_path = output_path / REVIEW_HTML_NAME
    payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    digest = sha256_bytes(payload)
    try:
        html_path = output_guard.verify_target(html_path)
        with html_path.open("xb") as target:
            target.write(html_payload)
            target.flush()
            os.fsync(target.fileno())
        if _read_guarded_output_file(output_guard, html_path, MAX_HTML_BYTES, "review HTML") != html_payload:
            raise BenchmarkRejected("review HTML readback differs before completion pointer commit")

        report_path = output_guard.verify_target(report_path)
        with report_path.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        if _read_guarded_output_file(output_guard, report_path, MAX_JSON_BYTES, "benchmark report") != payload:
            raise BenchmarkRejected("report bytes changed before completion pointer commit")
        partial_sidecar_path = output_guard.verify_target(partial_sidecar_path)
        with partial_sidecar_path.open("xb") as target:
            target.write((digest + "\n").encode("ascii"))
            target.flush()
            os.fsync(target.fileno())
        pointer_bytes = _read_guarded_output_file(output_guard, partial_sidecar_path, 4096, "partial completion pointer")
        if pointer_bytes != (digest + "\n").encode("ascii"):
            raise BenchmarkRejected("completion pointer readback mismatch")
        output_guard.verify_target(sidecar_path)
        os.rename(partial_sidecar_path, sidecar_path)
        output_guard.verify_target(sidecar_path, must_exist=True)
        output_guard.verify_current()
    except (OSError, RuntimeError, ValueError) as error:
        raise BenchmarkRejected(f"report write failed; inspect existing operation directory before retry: {error}") from error
    finally:
        output_guard.close()
    return {"status": "COMPLETED_REVIEW_REQUIRED", "operation_id": operation_id,
            "outdir": str(output_path), "report": str(report_path), "report_sha256": digest,
            "review_html": str(html_path), "review_html_sha256": html_digest,
            "measurement_sha256": report["measurement_sha256"],
            "open_semantic_categories": measurements["open_semantic_categories"],
            "model_inference_performed": False, "candidate_generation_performed": False,
            "art_approved": False, "native_timing_verified": measurements["authority"]["native_timing_verified"],
            "runtime_accepted": False, "game_deployed": False}


def verify(manifest_path: Path, manifest_sha256: str, outdir: Path, operation_id: str) -> dict:
    output_path = _validate_output_directory(outdir, must_exist=True)
    resolved_manifest, manifest, cases = _validate_manifest(manifest_path, manifest_sha256, operation_id)
    try:
        output_guard = OutputRootGuard.open_existing(output_path, OUTPUT_ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        raise BenchmarkRejected(f"cannot acquire existing output identity guard: {error}") from error
    report_path = output_path / REPORT_NAME
    sidecar_path = output_path / REPORT_SHA_NAME
    html_path = output_path / REVIEW_HTML_NAME
    try:
        report_bytes = _read_guarded_output_file(output_guard, report_path, MAX_JSON_BYTES, "benchmark report")
        pointer_bytes = _read_guarded_output_file(output_guard, sidecar_path, 4096, "completion pointer")
        try:
            expected_digest = pointer_bytes.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise BenchmarkRejected("completion pointer is not ASCII") from error
        digest = sha256_bytes(report_bytes)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or digest != expected_digest:
            raise BenchmarkRejected("report SHA completion pointer mismatch")
        try:
            stored = json.loads(report_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BenchmarkRejected(f"invalid benchmark report JSON: {error}") from error
        if not isinstance(stored, dict) or stored.get("schema") != "h3.transparent-interpolation-benchmark.report.v1":
            raise BenchmarkRejected("unsupported benchmark report schema")
        html_record = stored.get("review_html")
        if (not isinstance(html_record, dict) or html_record.keys() != {"file", "sha256"}
                or html_record.get("file") != REVIEW_HTML_NAME
                or not isinstance(html_record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", html_record["sha256"])):
            raise BenchmarkRejected("report does not bind the expected review HTML artifact")
        html_bytes = _read_guarded_output_file(output_guard, html_path, MAX_HTML_BYTES, "review HTML")
        html_digest = sha256_bytes(html_bytes)
        if html_digest != html_record["sha256"]:
            raise BenchmarkRejected("review HTML SHA differs from report binding")

        fresh = _build_measurements(resolved_manifest, manifest_sha256, manifest, cases)
        fresh_digest = sha256_bytes(canonical_json(fresh))
        if stored.get("measurement_sha256") != fresh_digest or stored.get("measurements") != fresh:
            raise BenchmarkRejected("report measurements differ from current pinned sources/candidates")
        if fresh.get("operation_id") != operation_id:
            raise BenchmarkRejected("verified operationId differs")
        expected_html = _build_review_html(cases, fresh)
        if html_bytes != expected_html:
            raise BenchmarkRejected("review HTML differs from current pinned PNGs and measurements")
        output_guard.verify_current()
    finally:
        output_guard.close()
    return {"status": "VERIFIED_REVIEW_REQUIRED", "operation_id": operation_id,
            "outdir": str(output_path), "report": str(report_path), "report_sha256": digest,
            "review_html": str(html_path), "review_html_sha256": html_digest,
            "measurement_sha256": fresh_digest, "model_inference_performed": False,
            "art_approved": False, "native_timing_verified": fresh["authority"]["native_timing_verified"],
            "runtime_accepted": False, "game_deployed": False}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "run", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--manifest", required=True, type=Path)
        command.add_argument("--manifest-sha256", required=True)
        command.add_argument("--outdir", required=True, type=Path)
        command.add_argument("--operation-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = {"check": check, "run": run, "verify": verify}[args.command](
            args.manifest, args.manifest_sha256, args.outdir, args.operation_id)
    except (BenchmarkRejected, OSError, ValueError, TypeError, KeyError) as error:
        status = "VERIFY_FAILED" if args.command == "verify" else "PREFLIGHT_REJECTED"
        print(json.dumps({"status": status, "command": args.command,
                          "operation_id": args.operation_id, "error": str(error),
                          "output_written": False if args.command != "run" else None}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
