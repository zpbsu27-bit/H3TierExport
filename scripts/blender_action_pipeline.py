#!/usr/bin/env python3
"""Plan, run, and verify explicit Blender Action sampling for native H3 slots.

The manifest is the caller-owned binding between a SHA-pinned creature action
plan, a rigged model, Blender Actions, and every present native frame. This
companion does not author poses, infer timing, or approve art. Run its check
command before rendering; run never reuses an existing output directory.

Manifest schema ``h3.blender-action-pipeline.manifest.v1`` (all paths absolute):

* ``identity`` and ``action_plan:{path,sha256}`` bind the canonical 22-group
  queue produced by creature_action_plan.py.
* ``model:{path,sha256,dependencies:[{path,sha256}]}`` pins the GLB/GLTF/FBX
  and every external file the importer may read.
* ``blender:{path,sha256,version}`` pins the Blender executable and version.
* ``armature_object``, ``model_transform``, ``facing``, ``camera``,
  ``footpoint``, ``render``, and ``lighting`` are explicit per creature.
* ``groups`` contains exactly one row for each present native group. Each row
  binds one Action and contains every native frame with an explicit Blender
  frame/subframe, playback timing, and a SHA-bound time-provenance locator.

All image passes render into one fixed 3x canvas. Crop and anchor stay in the
plan and are checked against every pass; no alpha cleanup or post-trim occurs.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from companion_runtime import source_path as runtime_source_path


PRODUCT_ROOT = resolve_tool_root(__file__)
SCRIPT_ROOT = Path(__file__).resolve().parent
ACTION_OUTPUT_ROOT = PRODUCT_ROOT / "build" / "experiments" / "action-pipeline"
WORKER_PATH = SCRIPT_ROOT / "blender_action_worker.py"
PIPELINE_SCHEMA = "h3.blender-action-pipeline.manifest.v1"
RESULT_SCHEMA = "h3.blender-action-pipeline.result.v1"
WORKER_RECEIPT_SCHEMA = "h3.blender-action-pipeline.worker-receipt.v1"
OPERATION_SCHEMA = "h3.blender-action-pipeline.operation.v1"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CAPTURE_BYTES = 256 * 1024
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$", re.ASCII)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
IDENTITY_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$", re.ASCII)

if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

try:
    import creature_action_plan as action_plan_module
    import art_pipeline_provider as provider_module
except ImportError as exc:  # pragma: no cover - import is covered by CLI contract
    raise RuntimeError(f"PIPELINE_IMPORT_FAILED: {exc}") from exc


class PipelineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PipelineError("JSON_DUPLICATE_KEY", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PipelineError("JSON_NONFINITE_NUMBER", f"non-finite JSON value is forbidden: {value}")


def _load_json_bytes(data: bytes, label: str) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object_no_duplicates,
                          parse_constant=_reject_constant)
    except PipelineError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("JSON_INVALID", f"{label} is not valid UTF-8 JSON: {exc}") from exc


def _read_limited(path: Path, limit: int, code: str) -> bytes:
    try:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise PipelineError(code, f"not a regular file: {path}")
        size = resolved.stat().st_size
        if size > limit:
            raise PipelineError("INPUT_TOO_LARGE", f"{path} is {size} bytes; limit is {limit}")
        with resolved.open("rb") as stream:
            data = stream.read(limit + 1)
    except PipelineError:
        raise
    except OSError as exc:
        raise PipelineError(code, f"cannot read {path}: {exc}") from exc
    if len(data) > limit:
        raise PipelineError("INPUT_TOO_LARGE", f"{path} grew beyond the {limit}-byte limit while reading")
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, limit: int = MAX_SOURCE_BYTES) -> str:
    try:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise PipelineError("INPUT_FILE_REQUIRED", f"not a regular file: {path}")
        if resolved.stat().st_size > limit:
            raise PipelineError("INPUT_TOO_LARGE", f"{path} exceeds {limit} bytes")
        digest = hashlib.sha256()
        total = 0
        with resolved.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(block)
                if total > limit:
                    raise PipelineError("INPUT_TOO_LARGE", f"{path} grew beyond {limit} bytes")
                digest.update(block)
        return digest.hexdigest()
    except PipelineError:
        raise
    except OSError as exc:
        raise PipelineError("INPUT_UNAVAILABLE", f"cannot hash {path}: {exc}") from exc


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise PipelineError(code, message)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    _require(isinstance(value, dict), "MANIFEST_SCHEMA_INVALID", f"{field} must be an object")
    return value


def _string(value: Any, field: str, *, nonempty: bool = True) -> str:
    _require(isinstance(value, str) and (not nonempty or bool(value.strip())),
             "MANIFEST_SCHEMA_INVALID", f"{field} must be a {'nonempty ' if nonempty else ''}string")
    return value


def _finite_number(value: Any, field: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(float(value)),
             "MANIFEST_SCHEMA_INVALID", f"{field} must be a finite number")
    return float(value)


def _number_vector(value: Any, field: str, size: int, *, positive: bool = False) -> list[float]:
    _require(isinstance(value, list) and len(value) == size,
             "MANIFEST_SCHEMA_INVALID", f"{field} must contain {size} numbers")
    result = [_finite_number(part, f"{field}[{index}]") for index, part in enumerate(value)]
    if positive:
        _require(all(part > 0 for part in result), "MANIFEST_SCHEMA_INVALID",
                 f"{field} values must be positive")
    return result


def _positive_int(value: Any, field: str, maximum: int = 1_000_000) -> int:
    _require(type(value) is int and 0 < value <= maximum,
             "MANIFEST_SCHEMA_INVALID", f"{field} must be an integer from 1 to {maximum}")
    return value


def _absolute_path(value: Any, field: str) -> Path:
    raw = Path(_string(value, field)).expanduser()
    _require(raw.is_absolute(), "INPUT_PATH_MUST_BE_ABSOLUTE", f"{field} must be absolute: {raw}")
    try:
        return raw.resolve(strict=True)
    except OSError as exc:
        raise PipelineError("INPUT_UNAVAILABLE", f"{field} is unavailable: {raw}: {exc}") from exc


def _file_ref(value: Any, field: str, *, expected_suffixes: tuple[str, ...] = ()) -> dict[str, Any]:
    ref = _mapping(value, field)
    path = _absolute_path(ref.get("path"), f"{field}.path")
    _require(path.is_file(), "INPUT_FILE_REQUIRED", f"{field}.path is not a file: {path}")
    expected = _string(ref.get("sha256"), f"{field}.sha256").lower()
    _require(SHA256_RE.fullmatch(expected) is not None,
             "SHA256_INVALID", f"{field}.sha256 must be 64 lowercase hexadecimal characters")
    _require(not expected_suffixes or path.suffix.lower() in expected_suffixes,
             "INPUT_EXTENSION_UNSUPPORTED",
             f"{field} suffix {path.suffix!r} is unsupported; expected {', '.join(expected_suffixes)}")
    actual = sha256_file(path)
    _require(actual == expected, "INPUT_SHA256_MISMATCH",
             f"{field} SHA256 differs from its frozen value: {path}")
    return {"path": str(path), "sha256": actual}


def _validate_action_plan(ref: Any, identity: str) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_ref = _file_ref(ref, "action_plan", expected_suffixes=(".json",))
    plan_data = _read_limited(Path(plan_ref["path"]), MAX_PLAN_BYTES, "ACTION_PLAN_UNAVAILABLE")
    plan = _load_json_bytes(plan_data, "action plan")
    _require(isinstance(plan, dict) and plan.get("schema") == action_plan_module.OUTPUT_SCHEMA,
             "ACTION_PLAN_SCHEMA_UNSUPPORTED", "action plan schema is not h3.creature.action-plan.v1")
    _require(plan.get("status") == "PLANNED", "ACTION_PLAN_NOT_PLANNED", "action plan must be PLANNED")
    _require(plan.get("unit_count") == 1 and isinstance(plan.get("units"), list)
             and len(plan["units"]) == 1,
             "ACTION_PLAN_UNIT_COUNT_INVALID", "one Blender operation must target exactly one creature")
    _require(plan["units"][0].get("identity") == identity,
             "ACTION_PLAN_IDENTITY_MISMATCH", "manifest identity does not match the action plan")
    inventory_ref = _mapping(plan.get("inventory"), "action_plan.inventory")
    inventory_path = _absolute_path(inventory_ref.get("path"), "action_plan.inventory.path")
    inventory_sha = _string(inventory_ref.get("sha256"), "action_plan.inventory.sha256").lower()
    _require(SHA256_RE.fullmatch(inventory_sha) is not None,
             "ACTION_PLAN_INVENTORY_SHA_INVALID", "action plan inventory SHA256 is malformed")
    try:
        document, actual_inventory_sha = action_plan_module.load_catalog(inventory_path, inventory_sha)
        canonical = action_plan_module.build_plan(document, actual_inventory_sha, [identity], inventory_path)
    except action_plan_module.PlanError as exc:
        raise PipelineError(f"ACTION_PLAN_{exc.code}", str(exc)) from exc
    _require(plan == canonical, "ACTION_PLAN_NOT_CANONICAL",
             "action plan does not exactly match a fresh build from its SHA-pinned inventory")
    return plan_ref, canonical


def _provider_validate_inputs(files: list[dict[str, str]]) -> list[tuple[Path, Path]]:
    """Reuse provider input validation for every source; outputs stay in our ignored root."""
    command = copy.deepcopy(provider_module.RUNNABLE["creature-art.render_sprites"])
    command["read_path_flags"] = []
    command["input_path_flags"] = {}
    command["write_flags"] = []
    arguments: list[str] = []
    for index, ref in enumerate(files):
        flag = f"--source-{index}"
        command["input_path_flags"][flag] = {"kind": "file", "protect_parent": True}
        arguments.extend((flag, ref["path"]))
    try:
        return provider_module.validate_input_contract(command, arguments)
    except provider_module.ProviderError as exc:
        raise PipelineError("PROVIDER_INPUT_BOUNDARY", str(exc)) from exc


def _validate_output_path(outdir_value: str | Path, operation_id: str, *, exists: bool,
                          output_root: Path = ACTION_OUTPUT_ROOT) -> Path:
    raw = Path(outdir_value).expanduser()
    _require(raw.is_absolute(), "OUTPUT_PATH_MUST_BE_ABSOLUTE", "--outdir must be absolute")
    output_root = Path(os.path.abspath(output_root))
    outdir = Path(os.path.abspath(raw))
    try:
        relative = outdir.relative_to(output_root)
    except ValueError as exc:
        raise PipelineError("OUTPUT_OUTSIDE_DEVELOPMENT_ROOT",
                            f"--outdir must be below {output_root}") from exc
    _require(bool(relative.parts) and len(relative.parts) == 1,
             "OUTPUT_PATH_INVALID", "--outdir must be one operation directory directly under action-pipeline")
    _require(outdir.name == operation_id, "OUTPUT_OPERATION_ID_MISMATCH",
             "the final --outdir component must equal --operation-id")
    if exists:
        _require(outdir.is_dir(), "OPERATION_NOT_FOUND", f"operation directory does not exist: {outdir}")
    else:
        _require(not outdir.exists(), "OPERATION_ALREADY_EXISTS",
                 f"refusing to reuse an existing operation directory: {outdir}")
    _reject_reparse_ancestors(outdir, output_root)
    return outdir


def _reject_reparse_ancestors(path: Path, boundary: Path) -> None:
    """Reject links/reparse points along paths the operation will write or trust."""
    boundary = Path(os.path.abspath(boundary))
    candidate = Path(os.path.abspath(path))
    try:
        candidate.relative_to(boundary)
    except ValueError as exc:
        raise PipelineError("PATH_OUTSIDE_BOUNDARY", f"{path} is outside {boundary}") from exc
    chain: list[Path] = []
    current = candidate
    while current != current.parent:
        chain.append(current)
        current = current.parent
    chain.append(current)
    for item in reversed(chain):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        try:
            reparse = item.is_symlink()
            if os.name == "nt":
                import stat
                reparse = reparse or bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            _require(not reparse, "REPARSE_PATH_FORBIDDEN", f"reparse/symlink path is forbidden: {item}")
            if item == candidate and item.is_file():
                continue
            _require(item.is_dir(), "OUTPUT_PARENT_NOT_DIRECTORY", f"path component is not a directory: {item}")
        except PipelineError:
            raise
        except OSError as exc:
            raise PipelineError("PATH_IDENTITY_UNAVAILABLE", f"cannot inspect {item}: {exc}") from exc


def _validate_numeric_config(manifest: dict[str, Any]) -> dict[str, Any]:
    model_transform = _mapping(manifest.get("model_transform"), "model_transform")
    transform = {
        "location": _number_vector(model_transform.get("location"), "model_transform.location", 3),
        "rotation_euler_degrees": _number_vector(
            model_transform.get("rotation_euler_degrees"), "model_transform.rotation_euler_degrees", 3),
        "scale": _number_vector(model_transform.get("scale"), "model_transform.scale", 3, positive=True),
    }
    facing = _mapping(manifest.get("facing"), "facing")
    forward_axis = _string(facing.get("model_forward_axis"), "facing.model_forward_axis")
    _require(forward_axis in {"+X", "-X", "+Y", "-Y"}, "FACING_AXIS_INVALID",
             "facing.model_forward_axis must be +X, -X, +Y, or -Y")
    mirror_policy = _string(facing.get("mirror_policy"), "facing.mirror_policy")
    _require(mirror_policy in {"none", "engine-mirrors", "separate-native-facing"},
             "FACING_POLICY_INVALID", "facing.mirror_policy is unsupported")
    transform["facing_yaw_degrees"] = _finite_number(
        facing.get("yaw_degrees"), "facing.yaw_degrees")
    transform["model_forward_axis"] = forward_axis
    transform["mirror_policy"] = mirror_policy

    camera = _mapping(manifest.get("camera"), "camera")
    _require(camera.get("projection") == "ORTHO", "CAMERA_PROJECTION_UNSUPPORTED",
             "camera.projection must be ORTHO")
    camera_spec = {
        "location": _number_vector(camera.get("location"), "camera.location", 3),
        "rotation_euler_degrees": _number_vector(
            camera.get("rotation_euler_degrees"), "camera.rotation_euler_degrees", 3),
        "orthographic_scale": _finite_number(camera.get("orthographic_scale"), "camera.orthographic_scale"),
        "shift_x": _finite_number(camera.get("shift_x", 0.0), "camera.shift_x"),
        "shift_y": _finite_number(camera.get("shift_y", 0.0), "camera.shift_y"),
        "clip_start": _finite_number(camera.get("clip_start", 0.1), "camera.clip_start"),
        "clip_end": _finite_number(camera.get("clip_end", 1000.0), "camera.clip_end"),
    }
    _require(camera_spec["orthographic_scale"] > 0 and camera_spec["clip_start"] > 0
             and camera_spec["clip_end"] > camera_spec["clip_start"],
             "CAMERA_CONFIG_INVALID", "orthographic_scale/clip range is invalid")

    render = _mapping(manifest.get("render"), "render")
    engine = _string(render.get("engine"), "render.engine")
    _require(engine == "CYCLES", "RENDER_ENGINE_UNSUPPORTED",
             "render.engine must be CYCLES for the transparent shadow-catcher contract")
    depth = _string(render.get("color_depth"), "render.color_depth")
    _require(depth in {"8", "16"}, "RENDER_COLOR_DEPTH_UNSUPPORTED",
             "render.color_depth must be 8 or 16")
    _require(render.get("transparent") is True, "TRANSPARENT_OUTPUT_REQUIRED",
             "render.transparent must be true")
    render_spec = {
        "engine": engine,
        "samples": _positive_int(render.get("samples"), "render.samples", 4096),
        "color_depth": depth,
        "view_transform": _string(render.get("view_transform"), "render.view_transform"),
        "look": _string(render.get("look"), "render.look", nonempty=False),
    }

    world = _mapping(manifest.get("world"), "world")
    world_spec = {
        "color": _number_vector(world.get("color"), "world.color", 3),
        "strength": _finite_number(world.get("strength"), "world.strength"),
    }
    _require(world_spec["strength"] >= 0 and all(0 <= color <= 1 for color in world_spec["color"]),
             "LIGHTING_CONFIG_INVALID", "world color must be within 0..1 and strength nonnegative")

    lights = manifest.get("lights")
    _require(isinstance(lights, list) and 1 <= len(lights) <= 8,
             "LIGHTING_CONFIG_INVALID", "lights must contain 1..8 explicit lights")
    light_specs = []
    seen_light_names: set[str] = set()
    for index, value in enumerate(lights):
        light = _mapping(value, f"lights[{index}]")
        name = _string(light.get("name"), f"lights[{index}].name")
        kind = _string(light.get("type"), f"lights[{index}].type")
        _require(name not in seen_light_names and kind in {"AREA", "SUN", "POINT", "SPOT"},
                 "LIGHTING_CONFIG_INVALID", f"light name/type is duplicate or unsupported: {name}/{kind}")
        seen_light_names.add(name)
        spec = {
            "name": name, "type": kind,
            "location": _number_vector(light.get("location"), f"lights[{index}].location", 3),
            "rotation_euler_degrees": _number_vector(
                light.get("rotation_euler_degrees"), f"lights[{index}].rotation_euler_degrees", 3),
            "energy": _finite_number(light.get("energy"), f"lights[{index}].energy"),
        }
        _require(spec["energy"] >= 0, "LIGHTING_CONFIG_INVALID", "light energy must be nonnegative")
        if kind == "AREA":
            spec["size"] = _finite_number(light.get("size"), f"lights[{index}].size")
            _require(spec["size"] > 0, "LIGHTING_CONFIG_INVALID", "AREA size must be positive")
        elif kind == "SUN":
            spec["angle_degrees"] = _finite_number(light.get("angle_degrees"),
                                                    f"lights[{index}].angle_degrees")
            _require(0 <= spec["angle_degrees"] <= 180,
                     "LIGHTING_CONFIG_INVALID", "SUN angle must be in 0..180 degrees")
        else:
            spec["radius"] = _finite_number(light.get("radius"), f"lights[{index}].radius")
            _require(spec["radius"] > 0, "LIGHTING_CONFIG_INVALID", "light radius must be positive")
        light_specs.append(spec)
    return {"model_transform": transform, "camera": camera_spec, "render": render_spec,
            "world": world_spec, "lights": light_specs}


def _validate_cloud_provenance(value: Any, model: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Read existing cloud evidence; never refresh status, fetch, or submit."""
    if value is None:
        return {"kind": "LOCAL_MODEL_SOURCE", "cloud_origin_verified": False}, []
    import cloud_animation as cloud

    provenance = _mapping(value, "cloud_provenance")
    _require(set(provenance) == {"operation_id", "provider_job_id", "plan", "state", "fetch_receipt"},
             "CLOUD_PROVENANCE_INVALID", "cloud provenance requires exact operation/job and three evidence bindings")
    operation_id = _string(provenance["operation_id"], "cloud_provenance.operation_id")
    job_id = _string(provenance["provider_job_id"], "cloud_provenance.provider_job_id")
    refs = {name: _file_ref(provenance[name], f"cloud_provenance.{name}")
            for name in ("plan", "state", "fetch_receipt")}
    for name, ref in refs.items():
        _reject_reparse_ancestors(Path(provenance[name]["path"]), cloud.DEVELOPMENT_ROOT)
    try:
        plan, operation_dir = cloud.validate_plan(Path(refs["plan"]["path"]))
    except cloud.CloudAnimationError as exc:
        raise PipelineError("CLOUD_PROVENANCE_PLAN_INVALID", str(exc)) from exc
    _require(plan["operation_id"] == operation_id
             and Path(refs["state"]["path"]) == operation_dir / "state.json",
             "CLOUD_PROVENANCE_IDENTITY_MISMATCH", "cloud plan/state path differs from its real operation")
    output_dir = Path(plan["output_directory"]).resolve(strict=True)
    _require(Path(refs["fetch_receipt"]["path"]) == output_dir / "fetch-receipt.json",
             "CLOUD_PROVENANCE_FETCH_PATH_INVALID", "fetch receipt must be the existing operation output receipt")
    state = _load_json_bytes(_read_limited(Path(refs["state"]["path"]), MAX_MANIFEST_BYTES,
                                         "CLOUD_PROVENANCE_STATE_INVALID"), "cloud state")
    fetch = _load_json_bytes(_read_limited(Path(refs["fetch_receipt"]["path"]), MAX_MANIFEST_BYTES,
                                         "CLOUD_PROVENANCE_FETCH_INVALID"), "cloud fetch receipt")
    _require(isinstance(state, dict) and state.get("schema") == cloud.STATE_SCHEMA
             and state.get("phase") == "FETCHED_CANDIDATE_REVIEW_REQUIRED"
             and isinstance(fetch, dict) and fetch.get("schema") == cloud.RECEIPT_SCHEMA,
             "CLOUD_PROVENANCE_INCOMPLETE", "cloud source needs completed existing fetch evidence")
    for evidence in (state, fetch):
        _require(evidence.get("operation_id") == operation_id
                 and evidence.get("provider_job_id") == job_id
                 and evidence.get("provider") == plan["provider"] and evidence.get("stage") == plan["stage"],
                 "CLOUD_PROVENANCE_IDENTITY_MISMATCH", "cloud operation/job/provider/stage differs between evidence")
    _require(state.get("fetch_receipt_sha256") == refs["fetch_receipt"]["sha256"],
             "CLOUD_PROVENANCE_FETCH_SHA_MISMATCH", "cloud writer state does not bind the selected fetch receipt")
    files = fetch.get("files")
    _require(isinstance(files, list), "CLOUD_PROVENANCE_FETCH_INVALID", "cloud fetch files are missing")
    matches = [item for item in files if isinstance(item, dict)
               and isinstance(item.get("path"), str)
               and Path(item["path"]).resolve(strict=False) == Path(model["path"])]
    _require(len(matches) == 1 and matches[0].get("sha256") == model["sha256"]
             and matches[0].get("bytes") == Path(model["path"]).stat().st_size
             and matches[0].get("candidate_only") is True
             and _is_relative(Path(model["path"]), output_dir),
             "CLOUD_MODEL_FILE_MISMATCH", "model must be one exact fetched candidate file inside the operation output")
    _require(all(fetch.get(field) is False for field in
                 ("art_approved", "alpha_verified", "h3_action_group_complete", "native_frame_timing_verified",
                  "runtime_accepted", "game_deployed")),
             "CLOUD_PROVENANCE_CLAIM_INVALID", "cloud fetch cannot carry local production acceptance")
    inputs = list(refs.values())
    inputs.append(_file_ref({"path": str(operation_dir / "plan.sha256"),
                             "sha256": sha256_file(operation_dir / "plan.sha256")}, "cloud plan digest"))
    for item in plan["sources"]:
        inputs.append({"path": item["path"], "sha256": item["sha256"]})
    for item in (plan.get("native_action_plan"), (plan.get("scenario_workflow") or {}).get("discovery")):
        if item:
            inputs.append({"path": item["path"], "sha256": item["sha256"]})
    return {"kind": "CLOUD_FETCHED_MODEL_SOURCE", "cloud_origin_verified": True,
            "operation_id": operation_id, "provider_job_id": job_id,
            "references": refs}, inputs


def runtime_source_bindings() -> list[dict[str, str]]:
    """Closed local execution sources, independent of the model's provenance."""
    names = ("blender_action_pipeline.py", "blender_action_worker.py", "creature_action_plan.py",
             "art_pipeline_provider.py", "review_native_batch.py", "workspace.py", "cloud_animation.py",
             "action_production_binding.py", "blender_motion_consistency.py")
    paths = [runtime_source_path("scripts/" + name, PRODUCT_ROOT) for name in names]
    paths.append(runtime_source_path("resources/H3TierExport-workspace.json", PRODUCT_ROOT))
    return [{"path": str(path.resolve(strict=True)), "sha256": sha256_file(path)} for path in paths]


def _verify_runtime_sources(start: dict[str, Any]) -> None:
    recorded = start.get("runtime_sources")
    _require(isinstance(recorded, list) and bool(recorded), "RUNTIME_SOURCE_BINDING_MISSING",
             "historical Action operation has no execution-source closure; preserve it and create a new operation")
    _require(recorded == runtime_source_bindings(), "RUNTIME_SOURCE_BINDING_DRIFT",
             "Action execution-source paths or SHA256 changed since this operation")


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    _require(isinstance(manifest, dict) and manifest.get("schema") == PIPELINE_SCHEMA,
             "MANIFEST_SCHEMA_UNSUPPORTED", f"manifest schema must be {PIPELINE_SCHEMA}")
    identity = _string(manifest.get("identity"), "identity").upper()
    _require(IDENTITY_RE.fullmatch(identity) is not None, "IDENTITY_INVALID", "identity is unsafe")
    action_plan_ref, plan = _validate_action_plan(manifest.get("action_plan"), identity)

    model = _mapping(manifest.get("model"), "model")
    model_path_value = Path(_string(model.get("path"), "model.path")).expanduser()
    _require(model_path_value.is_absolute(), "INPUT_PATH_MUST_BE_ABSOLUTE", "model.path must be absolute")
    _require(model_path_value.is_file(), "MODEL_NOT_FOUND", f"rigged model file is missing: {model_path_value}")
    model_ref = _file_ref({"path": model.get("path"), "sha256": model.get("sha256")},
                          "model", expected_suffixes=(".glb", ".gltf", ".fbx"))
    dependencies_value = model.get("dependencies", [])
    _require(isinstance(dependencies_value, list), "MANIFEST_SCHEMA_INVALID",
             "model.dependencies must be an array")
    dependencies = [_file_ref(item, f"model.dependencies[{index}]")
                    for index, item in enumerate(dependencies_value)]
    model_provenance, provenance_refs = _validate_cloud_provenance(
        manifest.get("cloud_provenance"), model_ref)
    dependency_paths = [item["path"] for item in dependencies]
    _require(len(dependency_paths) == len(set(dependency_paths))
             and model_ref["path"] not in dependency_paths,
             "MODEL_DEPENDENCY_DUPLICATE", "model dependency files must be unique and differ from the model")

    blender = _mapping(manifest.get("blender"), "blender")
    blender_path_value = Path(_string(blender.get("path"), "blender.path")).expanduser()
    _require(blender_path_value.is_absolute(), "BLENDER_PATH_MUST_BE_ABSOLUTE",
             "blender.path must be absolute")
    _require(blender_path_value.is_file(), "BLENDER_NOT_FOUND",
             f"manifest-pinned Blender executable is missing: {blender_path_value}")
    blender_ref = _file_ref({"path": blender.get("path"), "sha256": blender.get("sha256")},
                            "blender executable", expected_suffixes=(".exe",) if os.name == "nt" else ())
    blender_version = _string(blender.get("version"), "blender.version")
    # Blender exposes ``bpy.app.version_string`` with release labels on
    # official builds (for example ``4.5.9 LTS``), while some portable/custom
    # builds expose only the numeric version.  Keep the pin exact at runtime,
    # but allow the bounded release-label form needed to bind the executable's
    # actual identity instead of rejecting every LTS build before preflight.
    _require(re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:\s+[A-Za-z0-9][A-Za-z0-9._-]*)?", blender_version)
             is not None, "BLENDER_VERSION_INVALID",
             "blender.version must be an exact numeric version with an optional release label")

    armature_name = _string(manifest.get("armature_object"), "armature_object")
    numeric = _validate_numeric_config(manifest)

    footpoint = _mapping(manifest.get("footpoint"), "footpoint")
    footpoint_spec = {
        "bone_name": _string(footpoint.get("bone_name"), "footpoint.bone_name"),
        "bone_local_position": _number_vector(footpoint.get("bone_local_position"),
                                                "footpoint.bone_local_position", 3),
        "ground_plane_z": _finite_number(footpoint.get("ground_plane_z"), "footpoint.ground_plane_z"),
        "expected_pixel_1x": _number_vector(footpoint.get("expected_pixel_1x"),
                                              "footpoint.expected_pixel_1x", 2),
        "tolerance_px": _finite_number(footpoint.get("tolerance_px"), "footpoint.tolerance_px"),
        "calibration_native_group": footpoint.get("calibration_native_group"),
        "calibration_native_frame": footpoint.get("calibration_native_frame"),
    }
    _require(footpoint_spec["tolerance_px"] >= 0,
             "FOOTPOINT_CONFIG_INVALID", "footpoint.tolerance_px must be nonnegative")

    groups_value = manifest.get("groups")
    _require(isinstance(groups_value, list), "MANIFEST_SCHEMA_INVALID", "groups must be an array")
    plan_groups = plan["units"][0]["action_groups"]
    present = {group["group"]: group for group in plan_groups if group["actual_present"]}
    group_specs: list[dict[str, Any]] = []
    seen_groups: set[int] = set()
    for group_index, value in enumerate(groups_value):
        group = _mapping(value, f"groups[{group_index}]")
        native_group = group.get("native_group")
        _require(type(native_group) is int and 0 <= native_group <= 21,
                 "NATIVE_GROUP_INVALID", f"groups[{group_index}].native_group must be 0..21")
        _require(native_group not in seen_groups, "NATIVE_GROUP_DUPLICATE",
                 f"native group {native_group} appears more than once")
        seen_groups.add(native_group)
        _require(native_group in present, "BINDING_FOR_ABSENT_GROUP",
                 f"native group {native_group} is absent in the canonical plan")
        source_group = present[native_group]
        action_name = _string(group.get("action_name"), f"groups[{group_index}].action_name")
        action_slot = group.get("action_slot")
        if action_slot is not None:
            action_slot = _string(action_slot, f"groups[{group_index}].action_slot")
        loop = group.get("loop")
        _require(type(loop) is bool, "GROUP_TIMING_INVALID", f"native group {native_group} loop must be explicit")
        duration = _positive_int(group.get("frame_duration_ms"),
                                 f"groups[{group_index}].frame_duration_ms", 60_000)
        timing_ref = _file_ref(group.get("time_provenance"),
                               f"groups[{group_index}].time_provenance")
        locator = _string(group["time_provenance"].get("locator"),
                          f"groups[{group_index}].time_provenance.locator")
        slots_value = group.get("slots")
        _require(isinstance(slots_value, list), "SLOT_MAP_INVALID",
                 f"native group {native_group} slots must be an array")
        expected_slots = source_group["slots"]
        _require(len(slots_value) == len(expected_slots), "SLOT_MAP_INCOMPLETE",
                 f"native group {native_group} must map every one of its {len(expected_slots)} actual slots")
        slot_specs = []
        for slot_index, slot_value in enumerate(slots_value):
            slot = _mapping(slot_value, f"groups[{group_index}].slots[{slot_index}]")
            native_frame = slot.get("native_frame")
            expected_frame = expected_slots[slot_index]["key"]["frame"]
            _require(type(native_frame) is int and native_frame == expected_frame,
                     "SLOT_MAP_NOT_CANONICAL",
                     f"native group {native_group} slot {slot_index} must map native frame {expected_frame}")
            action_frame = slot.get("action_frame")
            _require(type(action_frame) is int, "ACTION_SAMPLE_INVALID",
                     f"native slot {native_group}/{native_frame} action_frame must be an integer")
            subframe = _finite_number(slot.get("subframe"),
                                     f"native slot {native_group}/{native_frame} subframe")
            _require(0 <= subframe < 1, "ACTION_SAMPLE_INVALID",
                     f"native slot {native_group}/{native_frame} subframe must be in [0,1)")
            time_ref = _string(slot.get("time_ref"),
                               f"native slot {native_group}/{native_frame} time_ref")
            slot_specs.append({"native_frame": native_frame, "action_frame": action_frame,
                               "subframe": subframe, "time_ref": time_ref,
                               "canvas": expected_slots[slot_index]["target_3x"]["canvas"],
                               "crop": expected_slots[slot_index]["target_3x"]["crop"],
                               "anchor": expected_slots[slot_index]["target_3x"]["anchor"],
                               "native_key": expected_slots[slot_index]["key"]})
        group_specs.append({"native_group": native_group, "action_name": action_name,
                            "action_slot": action_slot, "loop": loop,
                            "frame_duration_ms": duration, "time_provenance": timing_ref,
                            "time_locator": locator, "slots": slot_specs})
    _require(seen_groups == set(present), "GROUP_BINDINGS_INCOMPLETE",
             f"groups must bind exactly the present native groups {sorted(present)}")

    all_slots = [slot for group in group_specs for slot in group["slots"]]
    canvas_sizes = {tuple(slot["canvas"]) for slot in all_slots}
    _require(len(canvas_sizes) == 1, "CANVAS_VARIANT_UNSUPPORTED",
             "all native slots in one creature plan must share one fixed 3x canvas")
    canvas = list(next(iter(canvas_sizes)))
    foot_group = footpoint.get("calibration_native_group")
    foot_frame = footpoint.get("calibration_native_frame")
    _require(type(foot_group) is int and type(foot_frame) is int,
             "FOOTPOINT_CONFIG_INVALID", "footpoint calibration native group/frame must be explicit")
    foot_slot = next((slot for group in group_specs if group["native_group"] == foot_group
                      for slot in group["slots"] if slot["native_frame"] == foot_frame), None)
    _require(foot_slot is not None, "FOOTPOINT_CALIBRATION_SLOT_MISSING",
             "footpoint calibration must reference a mapped present native slot")
    _require(0 <= footpoint_spec["expected_pixel_1x"][0] <= canvas[0] / 3
             and 0 <= footpoint_spec["expected_pixel_1x"][1] <= canvas[1] / 3,
             "FOOTPOINT_CONFIG_INVALID", "footpoint expected pixel lies outside the native canvas")

    source_refs = [action_plan_ref, blender_ref, {"path": str(_absolute_path(
        plan["inventory"]["path"], "action_plan.inventory.path")), "sha256": plan["inventory"]["sha256"].lower()},
        model_ref, *dependencies, *provenance_refs]
    for group in group_specs:
        source_refs.append(group["time_provenance"])
    unique_refs: dict[str, dict[str, str]] = {}
    for ref in source_refs:
        prior = unique_refs.get(ref["path"])
        _require(prior is None or prior["sha256"] == ref["sha256"], "INPUT_SHA256_CONFLICT",
                 f"same source path has conflicting hashes: {ref['path']}")
        unique_refs[ref["path"]] = ref
    checked_inputs = [{"path": path, "sha256": ref["sha256"]}
                      for path, ref in sorted(unique_refs.items())]
    protected = _provider_validate_inputs(checked_inputs)

    dimensions = {
        "canvas_3x": canvas,
        "native_canvas": [canvas[0] // 3, canvas[1] // 3],
        "slot_count": len(all_slots),
        "present_group_count": len(group_specs),
        "absent_groups": [group["group"] for group in plan_groups if group["absent"]],
    }
    config = _validate_numeric_config(manifest)
    import blender_motion_consistency
    try:
        motion_consistency=blender_motion_consistency.validate(manifest.get('motion_consistency'),group_specs)
    except blender_motion_consistency.ConsistencyError as error:
        raise PipelineError(error.code,str(error)) from error
    return {"schema": PIPELINE_SCHEMA, "identity": identity,
            "action_plan": action_plan_ref, "model": model_ref,
            "model_dependencies": dependencies, "blender": {**blender_ref, "version": blender_version},
             "armature_object": armature_name, "model_provenance": model_provenance, **config,
            "footpoint": footpoint_spec, "groups": group_specs, "motion_consistency":motion_consistency,
            "dimensions": dimensions, "inputs": checked_inputs,
            "provider_protected_inputs": [{"path": str(path), "scope": str(scope)}
                                           for path, scope in protected],
            "action_plan_document": plan}


def load_manifest(path: str | Path, expected_sha256: str) -> tuple[dict[str, Any], bytes, str]:
    manifest_path = Path(path).expanduser()
    _require(manifest_path.is_absolute(), "MANIFEST_PATH_MUST_BE_ABSOLUTE",
             "--manifest must be absolute")
    expected = _string(expected_sha256, "--manifest-sha256").lower()
    _require(SHA256_RE.fullmatch(expected) is not None, "MANIFEST_SHA256_INVALID",
             "--manifest-sha256 must contain 64 hexadecimal characters")
    raw = _read_limited(manifest_path, MAX_MANIFEST_BYTES, "MANIFEST_UNAVAILABLE")
    digest = sha256_bytes(raw)
    _require(digest == expected, "MANIFEST_SHA256_MISMATCH",
             "manifest bytes do not match --manifest-sha256")
    manifest = _load_json_bytes(raw, "manifest")
    return manifest, raw, digest


def validate_operation_id(value: str) -> str:
    _require(isinstance(value, str) and OPERATION_ID_RE.fullmatch(value) is not None,
             "OPERATION_ID_INVALID", "operation id must use 1..96 safe ASCII letters, digits, _, ., or -")
    return value


def check_static(manifest: dict[str, Any], operation_id: str, outdir: str | Path,
                 *, output_root: Path = ACTION_OUTPUT_ROOT, exists: bool = False,
                 manifest_path: Path | None = None, manifest_sha: str | None = None) -> dict[str, Any]:
    operation_id = validate_operation_id(operation_id)
    config = _validate_manifest(manifest)
    _require(manifest.get("operation_id") == operation_id, "OPERATION_IDENTITY_MISMATCH",
             "manifest operation_id must exactly match --operation-id")
    output_path = _validate_output_path(outdir, operation_id, exists=exists, output_root=output_root)
    if manifest_path is not None:
        manifest_file = manifest_path.resolve(strict=True)
        manifest_hash = manifest_sha or sha256_file(manifest_file, limit=MAX_MANIFEST_BYTES)
        _require(sha256_file(manifest_file, limit=MAX_MANIFEST_BYTES) == manifest_hash,
                 "MANIFEST_SHA256_MISMATCH", "manifest changed after it was loaded")
        config["manifest_path"] = str(manifest_file)
        config["manifest_sha256"] = manifest_hash
        config["inputs"].append({"path": str(manifest_file), "sha256": manifest_hash})
    elif manifest_sha is not None:
        config["manifest_sha256"] = manifest_sha
    for ref in config["inputs"]:
        input_path = Path(ref["path"]).resolve(strict=True)
        if _paths_overlap(output_path, input_path.parent):
            raise PipelineError("OUTPUT_INTERSECTS_INPUT",
                                f"operation output overlaps protected source parent: {input_path.parent}")
    return {"configuration": config, "output_path": output_path}


_OUTPUT_GUARD_MODULE = None


def _output_guard_module():
    """Load the existing native-review directory guard instead of inventing another one."""
    global _OUTPUT_GUARD_MODULE
    if _OUTPUT_GUARD_MODULE is not None:
        return _OUTPUT_GUARD_MODULE
    try:
        import review_native_batch
        _OUTPUT_GUARD_MODULE = review_native_batch
        return review_native_batch
    except Exception as exc:  # pragma: no cover - depends on packaged runtime dependencies
        raise PipelineError("OUTPUT_ROOT_GUARD_UNAVAILABLE",
                            f"could not load the existing OutputRootGuard: {exc}") from exc


def create_output_root_guard(root: Path, base: Path = ACTION_OUTPUT_ROOT):
    guard_module = _output_guard_module()
    try:
        return guard_module.OutputRootGuard.create(Path(root), Path(base))
    except Exception as exc:
        raise PipelineError("OUTPUT_ROOT_GUARD_FAILED", str(exc)) from exc


def attach_output_root_guard(root: Path, base: Path = ACTION_OUTPUT_ROOT):
    """Attach the existing guard to an already-reserved operation root for worker/verify."""
    guard_module = _output_guard_module()
    try:
        return guard_module.OutputRootGuard.open_existing(Path(root), Path(base))
    except Exception as exc:
        raise PipelineError("OUTPUT_ROOT_GUARD_FAILED", str(exc)) from exc


def _create_guarded_subdirectory(guard: Any, path: Path) -> None:
    try:
        path.mkdir(parents=False, exist_ok=False)
        guard.hold_directory(path)
    except Exception as exc:
        raise PipelineError("OUTPUT_DIRECTORY_CREATE_FAILED", f"could not create and lock {path}: {exc}") from exc


def _guarded_write_exclusive(guard: Any, path: Path, data: bytes) -> None:
    try:
        guard.verify_target(path, must_exist=False)
        _write_exclusive(path, data)
        guard.verify_target(path, must_exist=True)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("GUARDED_OUTPUT_WRITE_FAILED", f"could not safely write {path}: {exc}") from exc


def _publish_complete_pointer(guard: Any, path: Path, data: bytes) -> None:
    """Publish the completion marker as a final same-directory, no-replace file rename."""
    pending = path.with_name(".operation-complete.json.pending")
    try:
        guard.verify_target(pending, must_exist=False)
        with pending.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        guard.verify_target(pending, must_exist=True)
        guard.verify_target(path, must_exist=False)
        # On Windows os.rename is atomic within the same volume and refuses to replace an existing target.
        os.rename(pending, path)
        guard.verify_target(path, must_exist=True)
        guard.verify_current()
    except Exception as exc:
        raise PipelineError("COMPLETE_POINTER_PUBLISH_FAILED",
                            f"could not atomically publish the final completion pointer: {exc}") from exc


def _paths_overlap(left: Path, right: Path) -> bool:
    left, right = left.resolve(strict=False), right.resolve(strict=False)
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _resolve_blender(config: dict[str, Any]) -> tuple[Path, str]:
    path = Path(config["blender"]["path"]).resolve(strict=True)
    _require(path.is_file(), "BLENDER_NOT_FOUND", f"Blender executable is missing: {path}")
    actual_sha = sha256_file(path)
    _require(actual_sha == config["blender"]["sha256"], "BLENDER_SHA256_MISMATCH",
             "Blender executable differs from the manifest SHA256")
    return path, actual_sha


def _bounded_process(argv: list[str], *, timeout_seconds: float,
                     cwd: str | None = None,
                     environment: dict[str, str] | None = None) -> tuple[int, str, bool]:
    """Drain child output while retaining only a bounded tail for receipts."""
    _require(math.isfinite(timeout_seconds) and 0 < timeout_seconds <= 24 * 60 * 60,
             "TIMEOUT_INVALID", "timeout must be in (0, 86400] seconds")
    try:
        child_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if environment:
            child_env.update(environment)
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   shell=False, env=child_env)
    except OSError as exc:
        raise PipelineError("BLENDER_START_FAILED", f"could not start Blender: {exc}") from exc
    tail = bytearray()

    def drain() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > MAX_CAPTURE_BYTES:
                del tail[:len(tail) - MAX_CAPTURE_BYTES]

    reader = threading.Thread(target=drain, name="blender-log-reader", daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    reader.join(timeout=10)
    if reader.is_alive():
        try:
            process.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass
        reader.join(timeout=1)
    return int(process.returncode if process.returncode is not None else -1), tail.decode("utf-8", "replace"), timed_out


def _worker_argv(blender_path: Path, mode: str, *, manifest_path: Path,
                 manifest_sha256: str, outdir: Path, operation_id: str) -> list[str]:
    return [str(blender_path), "--background", "--factory-startup", "--python-exit-code", "1",
            "--python", str(WORKER_PATH), "--", mode,
            "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha256,
            "--outdir", str(outdir), "--operation-id", operation_id]


def _blender_runtime_environment(directories: dict[str, Path]) -> dict[str, str]:
    mapping = {
        "BLENDER_USER_CONFIG": "config",
        "BLENDER_USER_SCRIPTS": "scripts",
        "BLENDER_USER_DATAFILES": "datafiles",
        "BLENDER_USER_EXTENSIONS": "extensions",
        "TMP": "temp",
        "TEMP": "temp",
        "TMPDIR": "temp",
    }
    return {variable: str(directories[name]) for variable, name in mapping.items()}


def _read_worker_probe(log_tail: str) -> dict[str, Any] | None:
    for line in reversed(log_tail.splitlines()):
        line = line.strip()
        if not line.startswith("H3_ACTION_PIPELINE_JSON="):
            continue
        try:
            result = json.loads(line.split("=", 1)[1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            return None
    return None


def run_check(manifest: dict[str, Any], manifest_sha: str, manifest_path: Path,
              outdir: Path, operation_id: str, *, timeout_seconds: float = 300,
              output_root: Path = ACTION_OUTPUT_ROOT) -> dict[str, Any]:
    static = check_static(manifest, operation_id, outdir, output_root=output_root, exists=False,
                          manifest_path=manifest_path, manifest_sha=manifest_sha)
    config = static["configuration"]
    blender_path, blender_sha = _resolve_blender(config)
    with tempfile.TemporaryDirectory(prefix="h3-action-check-") as temp_root:
        temp = Path(temp_root)
        directories = {}
        for name in ("config", "scripts", "datafiles", "extensions", "temp", "cwd"):
            directory = temp / name
            directory.mkdir()
            directories[name] = directory
        code, output, timed_out = _bounded_process(
            _worker_argv(blender_path, "--check", manifest_path=manifest_path,
                         manifest_sha256=manifest_sha, outdir=Path(outdir), operation_id=operation_id),
            timeout_seconds=timeout_seconds, cwd=str(directories["cwd"]),
            environment=_blender_runtime_environment(directories))
    probe = _read_worker_probe(output)
    _require(not timed_out, "BLENDER_CHECK_TIMEOUT", "Blender scene/action preflight timed out; no render was started")
    _require(code == 0 and probe is not None and probe.get("status") == "PASS",
             (probe or {}).get("code", "BLENDER_SCENE_CHECK_FAILED"),
             (probe or {}).get("message", output[-2000:] or f"Blender preflight exited {code}"))
    return {"schema": RESULT_SCHEMA, "status": "READY", "operation_id": operation_id,
            "manifest_sha256": manifest_sha, "blender_sha256": blender_sha,
            "identity": config["identity"], "slot_count": config["dimensions"]["slot_count"],
            "present_group_count": config["dimensions"]["present_group_count"],
            "absent_groups": config["dimensions"]["absent_groups"],
            "scene_preflight": probe, "output_path": str(static["output_path"]),
            "outputs_created": False}


def _write_exclusive(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise PipelineError("RECEIPT_ALREADY_EXISTS", f"refusing to replace existing receipt: {path}") from exc
    except OSError as exc:
        raise PipelineError("RECEIPT_WRITE_FAILED", f"could not write {path}: {exc}") from exc


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def run_operation(manifest: dict[str, Any], manifest_bytes: bytes, manifest_sha: str,
                  manifest_path: Path, outdir: Path, operation_id: str, *,
                  timeout_seconds: float = 4 * 60 * 60,
                  output_root: Path = ACTION_OUTPUT_ROOT) -> dict[str, Any]:
    static = check_static(manifest, operation_id, outdir, output_root=output_root, exists=False,
                          manifest_path=manifest_path, manifest_sha=manifest_sha)
    config = static["configuration"]
    blender_path, blender_sha = _resolve_blender(config)
    operation_root: Path = static["output_path"]
    runtime_sources = runtime_source_bindings()
    guard = create_output_root_guard(operation_root, output_root)
    frames_dir = operation_root / "frames"
    runtime = {name: operation_root / f"blender-{name}" for name in
               ("config", "scripts", "datafiles", "extensions")}
    runtime["temp"] = operation_root / "blender-temp"
    runtime["cwd"] = operation_root / "blender-cwd"
    error: PipelineError | None = None
    code = -1
    log_tail = ""
    timed_out = False
    try:
        try:
            _create_guarded_subdirectory(guard, frames_dir)
            for directory in runtime.values():
                _create_guarded_subdirectory(guard, directory)
            frozen_manifest = operation_root / "manifest.json"
            _guarded_write_exclusive(guard, frozen_manifest, manifest_bytes)
            config["inputs"].append({"path": str(frozen_manifest.resolve(strict=True)),
                                     "sha256": manifest_sha})
            started = {
                "schema": OPERATION_SCHEMA, "status": "RUNNING", "operation_id": operation_id,
                "manifest_path": str(manifest_path.resolve(strict=True)), "manifest_sha256": manifest_sha,
                "manifest_snapshot": "manifest.json", "blender_path": str(blender_path),
                "blender_sha256": blender_sha, "blender_version": config["blender"]["version"],
                "action_plan_sha256": config["action_plan"]["sha256"],
                "inventory_sha256": config["action_plan_document"]["inventory"]["sha256"],
                "model_sha256": config["model"]["sha256"], "identity": config["identity"],
                "slot_count": config["dimensions"]["slot_count"],
                "present_group_count": config["dimensions"]["present_group_count"],
                "absent_groups": config["dimensions"]["absent_groups"],
                "outputs_created": False, "game_written": False, "def_written": False,
                "runtime_sources": runtime_sources,
                "model_provenance": config["model_provenance"],
                "started_unix_ms": int(time.time() * 1000),
            }
            _guarded_write_exclusive(guard, operation_root / "operation-start.json", _json_bytes(started))
            _recheck_source_hashes(config)
            _verify_runtime_sources(started)
            _require(_resolve_blender(config) == (blender_path, blender_sha), "BLENDER_IDENTITY_CHANGED",
                     "manifest-pinned Blender changed while reserving the output operation")
            code, log_tail, timed_out = _bounded_process(
                _worker_argv(blender_path, "--run", manifest_path=frozen_manifest,
                             manifest_sha256=manifest_sha, outdir=operation_root, operation_id=operation_id),
                timeout_seconds=timeout_seconds, cwd=str(runtime["cwd"]),
                environment=_blender_runtime_environment(runtime))
            log_data = log_tail.encode("utf-8", "replace")
            _guarded_write_exclusive(guard, operation_root / "worker-log-tail.txt", log_data)
            guard.verify_current()
            _require(not timed_out, "BLENDER_RUN_TIMEOUT",
                     "Blender timed out. The operation is frozen and may not be rerun; inspect with verify.")
            if code != 0:
                probe = _read_worker_probe(log_tail)
                raise PipelineError((probe or {}).get("code", "BLENDER_RUN_FAILED"),
                                    (probe or {}).get("message",
                                                      f"Blender exited {code}; operation is frozen and may not be rerun"))
            receipt_path = frames_dir / "worker-receipt.json"
            guard.verify_target(receipt_path, must_exist=True)
            receipt_data = _read_limited(receipt_path, MAX_PLAN_BYTES, "WORKER_RECEIPT_MISSING")
            receipt = _load_json_bytes(receipt_data, "worker receipt")
            config["manifest_sha256"] = manifest_sha
            _verify_worker_receipt(receipt, config, operation_id, frames_dir, output_guard=guard)
            guard.verify_target(receipt_path, must_exist=True)
            _require(sha256_file(receipt_path, limit=MAX_PLAN_BYTES) == sha256_bytes(receipt_data),
                     "WORKER_RECEIPT_CHANGED", "worker receipt changed during verification")
            _recheck_source_hashes(config)
            _verify_runtime_sources(started)
            _require(_resolve_blender(config) == (blender_path, blender_sha), "BLENDER_IDENTITY_CHANGED",
                     "manifest-pinned Blender changed during the render")
            completed = {
                "schema": OPERATION_SCHEMA, "status": "COMPLETE", "operation_id": operation_id,
                "manifest_sha256": manifest_sha, "blender_sha256": blender_sha,
                "worker_receipt_sha256": sha256_bytes(receipt_data),
                "worker_log_tail_sha256": sha256_bytes(log_data),
                "worker_log_tail_truncated": len(output_bytes(log_tail)) >= MAX_CAPTURE_BYTES,
                "slot_count": config["dimensions"]["slot_count"],
                "verified_output_count": receipt["output_count"],
                "outputs_created": True, "game_written": False, "def_written": False,
                "completed_unix_ms": int(time.time() * 1000),
            }
            _publish_complete_pointer(guard, operation_root / "operation-complete.json", _json_bytes(completed))
            return {"schema": RESULT_SCHEMA, "status": "RENDERED", **completed,
                    "outdir": str(operation_root)}
        except PipelineError as exc:
            error = exc
        except Exception as exc:
            error = PipelineError("BLENDER_RUN_UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}")
        failure = {"schema": OPERATION_SCHEMA, "status": "FAILED", "operation_id": operation_id,
                   "manifest_sha256": manifest_sha, "blender_sha256": blender_sha,
                   "failure_code": error.code if error else "UNKNOWN_FAILURE",
                   "failure": str(error) if error else "unknown worker failure",
                   "blender_exit_code": code, "timed_out": timed_out,
                   "worker_log_tail": log_tail[-8000:], "rerun_allowed": False,
                   "outputs_may_be_partial": frames_dir.exists(),
                   "failed_unix_ms": int(time.time() * 1000)}
        try:
            _guarded_write_exclusive(guard, operation_root / "operation-failed.json", _json_bytes(failure))
        except Exception:
            pass
        raise error or PipelineError("BLENDER_RUN_FAILED", "unknown failure")
    finally:
        guard.close()


def output_bytes(text: str) -> bytes:
    return text.encode("utf-8", "replace")


def _recheck_source_hashes(config: dict[str, Any]) -> None:
    for ref in config["inputs"]:
        actual = sha256_file(Path(ref["path"]))
        _require(actual == ref["sha256"], "INPUT_CHANGED_DURING_RUN",
                 f"source changed during Blender operation: {ref['path']}")


def _png_header(path: Path) -> dict[str, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(33)
    except OSError as exc:
        raise PipelineError("OUTPUT_MISSING", f"cannot read output PNG {path}: {exc}") from exc
    _require(len(header) == 33 and header[:8] == b"\x89PNG\r\n\x1a\n"
             and header[12:16] == b"IHDR", "OUTPUT_PNG_INVALID", f"invalid PNG header: {path}")
    width, height, bit_depth, color_type = __import__("struct").unpack(">IIBB", header[16:26])
    return {"width": width, "height": height, "bit_depth": bit_depth, "color_type": color_type}


def _inside_crop(bounds: Any, slot: dict[str, Any], field: str) -> bool:
    if bounds is None:
        return False
    _require(isinstance(bounds, list) and len(bounds) == 4
             and all(type(part) is int for part in bounds),
             "WORKER_RECEIPT_INVALID", f"{field} must be [left,top,right,bottom] or null")
    ax, ay = slot["anchor"]
    cw, ch = slot["crop"]
    left, top, right, bottom = bounds
    return left >= ax and top >= ay and right <= ax + cw and bottom <= ay + ch


def _verify_worker_receipt(receipt: Any, config: dict[str, Any], operation_id: str,
                           frames_dir: Path, *, output_guard: Any | None = None) -> dict[str, Any]:
    _require(isinstance(receipt, dict) and receipt.get("schema") == WORKER_RECEIPT_SCHEMA,
             "WORKER_RECEIPT_INVALID", "worker receipt schema is invalid")
    _require(receipt.get("status") == "PASS" and receipt.get("operation_id") == operation_id,
             "WORKER_RECEIPT_INVALID", "worker did not report a passing receipt for this operation")
    _require(receipt.get("identity") == config["identity"]
             and receipt.get("manifest_sha256") == config.get("manifest_sha256", receipt.get("manifest_sha256")),
             "WORKER_RECEIPT_IDENTITY_MISMATCH", "worker receipt identity/manifest binding differs")
    _require(receipt.get("blender_version") == config["blender"]["version"],
             "WORKER_RECEIPT_RUNTIME_MISMATCH", "worker receipt Blender version differs from manifest")
    if config.get('motion_consistency') is not None:
        import blender_motion_consistency
        try:
            blender_motion_consistency.replay_report(config['motion_consistency'],config['groups'],
                                                       receipt.get('motion_consistency'))
        except blender_motion_consistency.ConsistencyError as error:
            raise PipelineError(error.code,str(error)) from error
    elif receipt.get('motion_consistency') is not None:
        _require(receipt['motion_consistency'].get('status')=='NOT_MEASURED',
                 'MOTION_UNBOUND_RECEIPT_CANNOT_PASS','no motion contract was supplied')
    expected_groups = {group["native_group"]: group for group in config["groups"]}
    receipt_groups = receipt.get("groups")
    _require(isinstance(receipt_groups, list) and len(receipt_groups) == len(expected_groups),
             "WORKER_RECEIPT_GROUPS_INVALID", "worker receipt must bind every present native group")
    seen_groups: set[int] = set()
    resolved_slots: dict[int, Any] = {}
    for row in receipt_groups:
        _require(isinstance(row, dict), "WORKER_RECEIPT_GROUPS_INVALID", "group receipt must be an object")
        group_id = row.get("native_group")
        _require(type(group_id) is int and group_id in expected_groups and group_id not in seen_groups,
                 "WORKER_RECEIPT_GROUPS_INVALID", f"unexpected or duplicate native group: {group_id}")
        seen_groups.add(group_id)
        expected_group = expected_groups[group_id]
        _require(row.get("action_name") == expected_group["action_name"]
                 and row.get("action_slot_requested") == expected_group["action_slot"]
                 and row.get("loop") is expected_group["loop"]
                 and row.get("frame_duration_ms") == expected_group["frame_duration_ms"]
                 and row.get("time_provenance") == expected_group["time_provenance"]
                 and row.get("time_locator") == expected_group["time_locator"],
                 "WORKER_RECEIPT_GROUP_MISMATCH", f"worker receipt group {group_id} differs from manifest")
        resolved_slots[group_id] = row.get("resolved_action_slot")
    _require(seen_groups == set(expected_groups), "WORKER_RECEIPT_GROUPS_INVALID",
             "worker receipt omitted a present native group")
    expected_by_key = {}
    for group in config["groups"]:
        for slot in group["slots"]:
            key = (group["native_group"], slot["native_frame"])
            expected_by_key[key] = (group, slot)
    frames = receipt.get("frames")
    _require(isinstance(frames, list) and len(frames) == len(expected_by_key),
             "WORKER_RECEIPT_COVERAGE_INVALID", "worker receipt must contain every actual native slot exactly once")
    seen = set()
    output_count = 0
    expected_size = tuple(config["dimensions"]["canvas_3x"])
    expected_depth = int(config["render"]["color_depth"])
    for frame in frames:
        _require(isinstance(frame, dict), "WORKER_RECEIPT_INVALID", "frame receipt must be an object")
        group_id, native_frame = frame.get("native_group"), frame.get("native_frame")
        key = (group_id, native_frame)
        _require(key in expected_by_key and key not in seen, "WORKER_RECEIPT_COVERAGE_INVALID",
                 f"unexpected or duplicate worker frame {key}")
        seen.add(key)
        group, slot = expected_by_key[key]
        _require(frame.get("action_name") == group["action_name"]
                 and frame.get("action_slot") == resolved_slots[group_id]
                 and frame.get("action_frame") == next(s["action_frame"] for s in group["slots"]
                                                        if s["native_frame"] == native_frame)
                 and frame.get("native_key") == slot["native_key"]
                 and frame.get("frame_duration_ms") == group["frame_duration_ms"]
                 and frame.get("loop") is group["loop"]
                 and frame.get("time_provenance") == group["time_provenance"]
                 and frame.get("time_locator") == group["time_locator"]
                 and frame.get("time_ref") == slot["time_ref"]
                 and abs(float(frame.get("subframe", -1)) - next(s["subframe"] for s in group["slots"]
                                                                  if s["native_frame"] == native_frame)) < 1e-9,
                 "WORKER_RECEIPT_ACTION_MAPPING_MISMATCH", f"worker slot mapping differs for {key}")
        layers = frame.get("layers")
        _require(isinstance(layers, dict) and set(layers) == {"body", "shadow", "overlay"},
                 "WORKER_RECEIPT_LAYERS_INVALID", f"frame {key} must have body/shadow/overlay layers")
        for layer_name, layer in layers.items():
            _require(isinstance(layer, dict), "WORKER_RECEIPT_INVALID", f"{key}/{layer_name} receipt invalid")
            relative = PurePosixPath(_string(layer.get("path"), f"{key}/{layer_name}.path"))
            _require(not relative.is_absolute() and ".." not in relative.parts,
                     "WORKER_RECEIPT_PATH_INVALID", f"unsafe worker output path: {relative}")
            target_path = frames_dir / Path(*relative.parts)
            if output_guard is not None:
                output_guard.verify_target(target_path, must_exist=True)
            else:
                _reject_reparse_ancestors(target_path, frames_dir)
            target = target_path.resolve(strict=True)
            _require(_is_relative(target, frames_dir.resolve(strict=True)),
                     "WORKER_RECEIPT_PATH_INVALID", f"worker output escapes frame root: {relative}")
            expected_sha = _string(layer.get("sha256"), f"{key}/{layer_name}.sha256").lower()
            _require(SHA256_RE.fullmatch(expected_sha) is not None and sha256_file(target) == expected_sha,
                     "OUTPUT_SHA256_MISMATCH", f"output bytes changed: {target}")
            header = _png_header(target)
            _require((header["width"], header["height"]) == expected_size
                     and header["bit_depth"] == expected_depth and header["color_type"] == 6,
                     "OUTPUT_PNG_GEOMETRY_INVALID",
                     f"{key}/{layer_name} must be {expected_size[0]}x{expected_size[1]} RGBA {expected_depth}-bit PNG")
            _require((layer.get("width"), layer.get("height")) == expected_size,
                     "WORKER_RECEIPT_GEOMETRY_MISMATCH", f"worker dimensions disagree for {key}/{layer_name}")
            alpha_count = layer.get("nonzero_alpha_pixels")
            _require(type(alpha_count) is int and alpha_count >= 0,
                     "WORKER_RECEIPT_INVALID", f"{key}/{layer_name} alpha count is invalid")
            bounds = layer.get("alpha_bounds")
            if layer_name == "shadow" and bounds is None and alpha_count == 0:
                pass
            else:
                _require(_inside_crop(bounds, slot, f"{key}/{layer_name}.alpha_bounds"),
                         "RENDER_CROP_OVERFLOW",
                         f"nonzero alpha is absent or outside native crop for {key}/{layer_name}")
            if layer_name in {"body", "overlay"}:
                _require(alpha_count > 0, "RENDER_LAYER_EMPTY", f"{key}/{layer_name} rendered no nonzero alpha")
            output_count += 1
    _require(seen == set(expected_by_key), "WORKER_RECEIPT_COVERAGE_INVALID",
             "worker receipt omitted one or more native slots")
    _require(receipt.get("output_count") == output_count,
             "WORKER_RECEIPT_COUNT_MISMATCH", "worker output_count differs from the verified output files")
    _require(receipt.get("game_written") is False and receipt.get("def_written") is False,
             "WORKER_RECEIPT_SCOPE_VIOLATION", "Blender action pipeline may not write a game or DEF")
    return {"status": "PASS", "slot_count": len(seen), "output_count": output_count}


def _is_relative(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def verify_operation(manifest: dict[str, Any], manifest_bytes: bytes, manifest_sha: str,
                     manifest_path: Path, outdir: Path, operation_id: str, *,
                     output_root: Path = ACTION_OUTPUT_ROOT) -> dict[str, Any]:
    static = check_static(manifest, operation_id, outdir, output_root=output_root, exists=True,
                          manifest_path=manifest_path, manifest_sha=manifest_sha)
    operation_root: Path = static["output_path"]
    output_guard = None
    if Path(os.path.abspath(output_root)) == Path(os.path.abspath(ACTION_OUTPUT_ROOT)):
        output_guard = attach_output_root_guard(operation_root, output_root)
    try:
        def read_metadata(path: Path, limit: int, code: str) -> bytes:
            if output_guard is not None:
                output_guard.verify_target(path, must_exist=True)
            else:
                _reject_reparse_ancestors(path, operation_root)
            return _read_limited(path, limit, code)

        start_path = operation_root / "operation-start.json"
        start = _load_json_bytes(read_metadata(start_path, 1_000_000, "OPERATION_START_MISSING"),
                                 "operation start receipt")
        _require(isinstance(start, dict) and start.get("schema") == OPERATION_SCHEMA
                 and start.get("operation_id") == operation_id,
                 "OPERATION_START_INVALID", "operation start receipt is invalid")
        _verify_runtime_sources(start)
        _require(start.get("manifest_sha256") == manifest_sha,
                 "OPERATION_MANIFEST_MISMATCH", "operation was started with different manifest bytes")
        _require(start.get("manifest_path") == str(manifest_path.resolve(strict=True)),
                 "OPERATION_MANIFEST_PATH_MISMATCH", "verify must use the original frozen manifest path")
        frozen_path = operation_root / "manifest.json"
        frozen_bytes = read_metadata(frozen_path, MAX_MANIFEST_BYTES, "FROZEN_MANIFEST_MISSING")
        _require(sha256_bytes(frozen_bytes) == manifest_sha and frozen_bytes == manifest_bytes,
                 "FROZEN_MANIFEST_MISMATCH", "operation manifest snapshot differs from current frozen input")
        config = static["configuration"]
        _recheck_source_hashes(config)
        frames_dir = operation_root / "frames"
        try:
            frames_info = frames_dir.lstat()
        except FileNotFoundError:
            frames_info = None
        if output_guard is not None and frames_info is not None:
            output_guard.hold_directory(frames_dir)
        receipt_path = frames_dir / "worker-receipt.json"
        if frames_info is None or not receipt_path.exists():
            failure_path = operation_root / "operation-failed.json"
            failure_exists = failure_path.exists()
            if failure_exists:
                read_metadata(failure_path, 1_000_000, "OPERATION_FAILED_INVALID")
            code = "OPERATION_FAILED_NO_RERUN" if failure_exists else "OPERATION_INCOMPLETE_NO_RERUN"
            raise PipelineError(code,
                                "no complete worker receipt exists; inspect partial outputs and do not rerun this identity")
        receipt_data = read_metadata(receipt_path, MAX_PLAN_BYTES, "WORKER_RECEIPT_MISSING")
        receipt = _load_json_bytes(receipt_data, "worker receipt")
        config["manifest_sha256"] = manifest_sha
        verified = _verify_worker_receipt(receipt, config, operation_id, frames_dir,
                                          output_guard=output_guard)
        _verify_runtime_sources(start)
        _recheck_source_hashes(config)
        _require(sha256_file(receipt_path, limit=MAX_PLAN_BYTES) == sha256_bytes(receipt_data),
                 "WORKER_RECEIPT_CHANGED", "worker receipt changed during verification")
        complete_path = operation_root / "operation-complete.json"
        if complete_path.exists():
            complete_data = read_metadata(complete_path, 1_000_000, "OPERATION_COMPLETE_INVALID")
            complete = _load_json_bytes(complete_data, "operation complete receipt")
            _require(isinstance(complete, dict) and complete.get("schema") == OPERATION_SCHEMA
                     and complete.get("status") == "COMPLETE"
                     and complete.get("operation_id") == operation_id
                     and complete.get("manifest_sha256") == manifest_sha
                     and complete.get("blender_sha256") == config["blender"]["sha256"]
                     and complete.get("worker_receipt_sha256") == sha256_bytes(receipt_data),
                     "OPERATION_COMPLETE_INVALID", "completion receipt does not bind the current worker receipt")
            status = "VERIFIED"
        else:
            failure_path = operation_root / "operation-failed.json"
            failure_exists = failure_path.exists()
            if failure_exists:
                read_metadata(failure_path, 1_000_000, "OPERATION_FAILED_INVALID")
            _require(not failure_exists, "OPERATION_FAILED_NO_RERUN",
                     "operation has a durable failure receipt and cannot be retried under this identity")
            status = "RECOVERED_COMPLETE"
        return {"schema": RESULT_SCHEMA, "status": status, "operation_id": operation_id,
                "manifest_sha256": manifest_sha, "identity": config["identity"],
                "verified": verified, "outdir": str(operation_root),
                "recovered_from_worker_receipt": status == "RECOVERED_COMPLETE",
                "game_written": False, "def_written": False}
    finally:
        if output_guard is not None:
            output_guard.close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "run", "verify"):
        command = sub.add_parser(action)
        command.add_argument("--manifest", required=True, help="SHA-pinned action-pipeline manifest")
        command.add_argument("--manifest-sha256", required=True, help="SHA256 of manifest bytes")
        command.add_argument("--outdir", required=True, help="operation directory under build/experiments/action-pipeline")
        command.add_argument("--operation-id", required=True, help="stable operation identity; equals outdir basename")
        if action in {"check", "run"}:
            command.add_argument("--timeout-seconds", type=float,
                                 default=300 if action == "check" else 4 * 60 * 60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        manifest_path = Path(args.manifest).expanduser().resolve(strict=True)
        manifest, manifest_bytes, manifest_sha = load_manifest(manifest_path, args.manifest_sha256)
        outdir = Path(args.outdir).expanduser()
        operation_id = validate_operation_id(args.operation_id)
        if args.action == "check":
            result = run_check(manifest, manifest_sha, manifest_path, outdir, operation_id,
                               timeout_seconds=args.timeout_seconds)
        elif args.action == "run":
            result = run_operation(manifest, manifest_bytes, manifest_sha, manifest_path,
                                   outdir, operation_id, timeout_seconds=args.timeout_seconds)
        else:
            result = verify_operation(manifest, manifest_bytes, manifest_sha, manifest_path,
                                      outdir, operation_id)
    except PipelineError as exc:
        blocked_codes = {"BLENDER_NOT_FOUND", "BLENDER_SHA256_MISMATCH", "BLENDER_VERSION_MISMATCH",
                         "BLENDER_SCENE_CHECK_FAILED", "BLENDER_CHECK_TIMEOUT", "MODEL_NOT_FOUND",
                         "ARMATURE_NOT_UNIQUE", "ACTION_NOT_FOUND", "ACTION_SLOT_AMBIGUOUS",
                         "ACTION_SLOT_NOT_FOUND", "NLA_TRACKS_AMBIGUOUS", "MODEL_IMPORT_FAILED",
                         "GLTF_IMPORTER_UNAVAILABLE", "FBX_IMPORTER_UNAVAILABLE"}
        status = "BLOCKED" if exc.code in blocked_codes else "REFUSED"
        print(json.dumps({"schema": RESULT_SCHEMA, "status": status, "code": exc.code,
                          "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3 if status == "BLOCKED" else 2
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": RESULT_SCHEMA, "status": "REFUSED",
                          "code": "UNEXPECTED_INPUT_ERROR", "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
