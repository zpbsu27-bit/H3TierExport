"""Bind a verified Blender Action operation to the existing PNG delivery owner.

This module produces input metadata only. The selected body/shadow/overlay PNG
stays on its native canvas and is subsequently copied byte-for-byte by
creature_production. It never renders, crops, composes layers, or approves art.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import blender_action_pipeline as pipeline
from review_native_batch import OutputRootGuard, reject_reparse_components

SCHEMA = "h3.blender-action.production-binding.v1"
OUTPUT_BASE = pipeline.PRODUCT_ROOT / "build" / "experiments" / "action-production-bindings"
LAYERS = {"body", "shadow", "overlay"}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise pipeline.PipelineError(code, message)


def _ref(path: Path) -> dict[str, str]:
    reject_reparse_components(path, allow_missing_leaf=False)
    return {"path": str(path.resolve(strict=True)), "sha256": pipeline.sha256_file(path)}


def _read_ref(value: Any, label: str, limit: int = pipeline.MAX_PLAN_BYTES) -> tuple[Path, bytes]:
    _require(isinstance(value, dict) and set(value) == {"path", "sha256"},
             "ACTION_BINDING_INVALID", f"{label} requires exact path/sha256")
    lexical = Path(pipeline._string(value["path"], f"{label}.path"))
    _require(lexical.is_absolute(), "ACTION_BINDING_INVALID", f"{label} path must be absolute")
    reject_reparse_components(lexical, allow_missing_leaf=False)
    path = lexical.resolve(strict=True)
    raw = pipeline._read_limited(path, limit, "ACTION_BINDING_UNAVAILABLE")
    _require(pipeline.SHA256_RE.fullmatch(str(value["sha256"])) is not None
             and pipeline.sha256_bytes(raw) == value["sha256"],
             "ACTION_BINDING_SHA_MISMATCH", f"{label} bytes differ from the fixed binding")
    return path, raw


def recheck_fingerprints(fingerprints: dict[str, str]) -> None:
    for path, expected in fingerprints.items():
        reject_reparse_components(Path(path), allow_missing_leaf=False)
        _require(pipeline.sha256_file(Path(path)) == expected,
                 "ACTION_SOURCE_DRIFT", f"Action source/evidence changed: {path}")


def _remember(fingerprints: dict[str, str], path: str, sha: str) -> None:
    _require(path not in fingerprints or fingerprints[path] == sha, "ACTION_BINDING_SHA_CONFLICT",
             "same Action source/evidence path has conflicting fingerprints")
    fingerprints[path] = sha


def _load_operation(descriptor: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(descriptor, dict)
             and set(descriptor) == {"schema", "operation_id", "outdir", "manifest", "operation_start",
                                     "worker_receipt", "operation_complete", "layer"}
             and descriptor.get("schema") == SCHEMA and descriptor.get("layer") in LAYERS,
             "ACTION_BINDING_INVALID", "Action descriptor requires exact evidence and one explicit layer")
    operation_id = pipeline.validate_operation_id(descriptor["operation_id"])
    lexical_outdir = Path(pipeline._string(descriptor["outdir"], "action outdir"))
    reject_reparse_components(lexical_outdir, allow_missing_leaf=False)
    outdir = pipeline._validate_output_path(lexical_outdir, operation_id, exists=True,
                                           output_root=pipeline.ACTION_OUTPUT_ROOT)
    _require(not (outdir / "operation-failed.json").exists(), "ACTION_OPERATION_FAILED",
             "failed Action operations cannot be promoted to production inputs")
    cache_key = json.dumps({k: v for k, v in descriptor.items() if k != "layer"}, sort_keys=True)
    if cache_key in cache.setdefault("operations", {}):
        return cache["operations"][cache_key]
    paths: dict[str, Path] = {}
    raws: dict[str, bytes] = {}
    for name in ("manifest", "operation_start", "worker_receipt", "operation_complete"):
        paths[name], raws[name] = _read_ref(descriptor[name], f"Action {name}")
    expected_paths = {"operation_start": outdir / "operation-start.json",
                      "worker_receipt": outdir / "frames" / "worker-receipt.json",
                      "operation_complete": outdir / "operation-complete.json"}
    _require(all(paths[name] == path for name, path in expected_paths.items()),
             "ACTION_BINDING_PATH_MISMATCH", "Action receipts must belong to the exact existing operation")
    manifest = pipeline._load_json_bytes(raws["manifest"], "Action manifest")
    complete = pipeline._load_json_bytes(raws["operation_complete"], "Action completion")
    _require(isinstance(complete, dict) and complete.get("status") == "COMPLETE",
             "ACTION_COMPLETE_REQUIRED", "a real COMPLETE pointer is required; partial/recovered output is not promoted")
    result = pipeline.verify_operation(manifest, raws["manifest"], descriptor["manifest"]["sha256"],
                                       paths["manifest"], outdir, operation_id)
    _require(result.get("status") == "VERIFIED" and result.get("recovered_from_worker_receipt") is False,
             "ACTION_COMPLETE_REQUIRED", "only a fully verified complete Action operation can be bound")
    # Static configuration is the existing owner projection, not a second slot parser.
    config = pipeline._validate_manifest(manifest)
    start = pipeline._load_json_bytes(raws["operation_start"], "Action start")
    worker = pipeline._load_json_bytes(raws["worker_receipt"], "Action worker receipt")
    fingerprints = cache.setdefault("fingerprints", {})
    for name, path in paths.items():
        _remember(fingerprints, str(path), descriptor[name]["sha256"])
    for ref in [*config["inputs"], *start["runtime_sources"]]:
        path = str(Path(ref["path"]).resolve(strict=True))
        prior = fingerprints.get(path)
        _require(prior is None or prior == ref["sha256"], "ACTION_BINDING_SHA_CONFLICT",
                 "same Action source has conflicting fingerprints")
        _remember(fingerprints, path, ref["sha256"])
    frames = {}
    for frame in worker["frames"]:
        key = ("def", config["identity"], frame["native_group"], frame["native_frame"])
        frames[key] = frame
        for layer in frame["layers"].values():
            path = str((outdir / "frames" / layer["path"]).resolve(strict=True))
            _remember(fingerprints, path, layer["sha256"])
    recheck_fingerprints(fingerprints)
    loaded = {"config": config, "worker": worker, "frames": frames, "outdir": outdir,
              "manifest": manifest, "descriptor": descriptor}
    cache["operations"][cache_key] = loaded
    return loaded


def verify_source(receipt_binding: Any, key: tuple[str, str, int, int], geometry: dict[str, list[int]],
                  source_path: Path, source_sha: str, cache: dict[str, Any], *,
                  timing: dict[str, Any]) -> dict[str, Any]:
    receipt_path, raw = _read_ref(receipt_binding, "Action production descriptor")
    descriptor = pipeline._load_json_bytes(raw, "Action production descriptor")
    loaded = _load_operation(descriptor, cache)
    frame = loaded["frames"].get(key)
    _require(frame is not None, "ACTION_SLOT_BINDING_MISMATCH", "Action operation does not contain this native slot")
    group = next(g for g in loaded["config"]["groups"] if g["native_group"] == key[2])
    slot = next(s for s in group["slots"] if s["native_frame"] == key[3])
    expected_geometry = {"canvas_1x": [n // 3 for n in slot["canvas"]],
                         "crop_1x": [n // 3 for n in slot["crop"]],
                         "origin_1x": [n // 3 for n in slot["anchor"]]}
    layer = frame["layers"][descriptor["layer"]]
    layer_path = (loaded["outdir"] / "frames" / layer["path"]).resolve(strict=True)
    _require(geometry == expected_geometry and source_path == layer_path and source_sha == layer["sha256"],
             "ACTION_SLOT_BINDING_MISMATCH", "native geometry/source PNG differs from the selected verified Action layer")
    # Delivery metadata must not replace values already bound by the Action
    # owner. A file hash alone does not prove a different duration or loop.
    fields = {"duration_ms", "duration_provenance", "loop", "loop_provenance"}
    _require(isinstance(timing, dict) and set(timing) == fields
             and type(timing.get("duration_ms")) is int
             and timing["duration_ms"] == frame["frame_duration_ms"]
             and type(timing.get("loop")) is bool and timing["loop"] is frame["loop"],
             "ACTION_TIMING_BINDING_MISMATCH", "delivery duration/loop differs from the verified Action slot")
    time_ref = {name: group["time_provenance"][name] for name in ("path", "sha256")}
    for name in ("duration_provenance", "loop_provenance"):
        path, raw = _read_ref(timing[name], f"Action {name}")
        _require(path == Path(time_ref["path"]) and pipeline.sha256_bytes(raw) == time_ref["sha256"],
                 "ACTION_TIMING_BINDING_MISMATCH", "delivery timing provenance differs from its Action owner")
        _remember(cache.setdefault("fingerprints", {}), str(path), time_ref["sha256"])
    _remember(cache.setdefault("fingerprints", {}), str(receipt_path), receipt_binding["sha256"])
    return {"path": str(receipt_path), "sha256": receipt_binding["sha256"],
            "timing_binding": {"duration_ms": frame["frame_duration_ms"], "loop": frame["loop"],
                               "provenance": time_ref, "locator": group["time_locator"],
                               "slot_time_ref": slot["time_ref"]},
            "action_operation_id": descriptor["operation_id"], "layer": descriptor["layer"],
            "layout": "native-canvas", "action_manifest_sha256": descriptor["manifest"]["sha256"],
            "worker_receipt_sha256": descriptor["worker_receipt"]["sha256"],
            "operation_complete_sha256": descriptor["operation_complete"]["sha256"]}


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    import creature_production as production

    _require(production.OPERATION_RE.fullmatch(args.operation_id) is not None,
             "OPERATION_ID_INVALID", "production operation ID must be 1..64 safe ASCII letters/digits/_/-")
    outdir = Path(os.path.abspath(args.outdir))
    _require(outdir == Path(os.path.abspath(OUTPUT_BASE / args.operation_id)),
             "OUTPUT_ROOT_OUTSIDE_DEVELOPMENT_ROOT", "preparation output must be the exact new development operation root")
    reject_reparse_components(outdir, allow_missing_leaf=True)
    _require(not outdir.exists(), "OUTPUT_COLLISION", "preparation output already exists; inspect it instead of retrying")
    action_outdir = Path(args.action_outdir)
    descriptor = {"schema": SCHEMA, "operation_id": args.action_operation_id, "outdir": str(action_outdir),
                  "manifest": {"path": args.action_manifest, "sha256": args.action_manifest_sha256},
                  "operation_start": _ref(action_outdir / "operation-start.json"),
                  "worker_receipt": _ref(action_outdir / "frames" / "worker-receipt.json"),
                  "operation_complete": _ref(action_outdir / "operation-complete.json"), "layer": args.layer}
    cache: dict[str, Any] = {}
    loaded = _load_operation(descriptor, cache)
    config = loaded["config"]
    descriptor_path = outdir / "action-binding.json"
    raw_descriptor = pipeline._json_bytes(descriptor)
    descriptor_ref = {"path": str(descriptor_path), "sha256": pipeline.sha256_bytes(raw_descriptor)}
    slots = []
    for group in config["groups"]:
        for slot in group["slots"]:
            key = ("def", config["identity"], group["native_group"], slot["native_frame"])
            layer = loaded["frames"][key]["layers"][args.layer]
            slots.append({"key": list(key),
                          "geometry": {"canvas_1x": [n // 3 for n in slot["canvas"]],
                                       "crop_1x": [n // 3 for n in slot["crop"]],
                                       "origin_1x": [n // 3 for n in slot["anchor"]]},
                          "source": {"kind": "blender-action-output", "path": str(
                              (loaded["outdir"] / "frames" / layer["path"]).resolve(strict=True)),
                                     "sha256": layer["sha256"], "native_scale": 3,
                                     "layout": "native-canvas", "receipt": descriptor_ref},
                          # Camera/facing yaw and projected 3D foot coordinates are not H3 semantic direction/feet.
                          "facing": {"value": "unknown", "provenance": None},
                          "footpoint": {"value": "unknown", "provenance": None},
                          "timing": {"duration_ms": group["frame_duration_ms"],
                                     "duration_provenance": group["time_provenance"], "loop": group["loop"],
                                     "loop_provenance": group["time_provenance"]},
                          "derivation": {"kind": "blender-action-sample", "reuse_of": None, "evidence": None}})
    # Existing production metadata requires exact file references without locator extensions.
    for slot in slots:
        for field in ("duration_provenance", "loop_provenance"):
            slot["timing"][field] = {k: slot["timing"][field][k] for k in ("path", "sha256")}
    manifest = {"schema": production.MANIFEST_SCHEMA, "operation_id": args.operation_id,
                "action_plan": config["action_plan"],
                "official_inventory": {"path": config["action_plan_document"]["inventory"]["path"],
                                       "sha256": config["action_plan_document"]["inventory"]["sha256"]},
                "slots": slots}
    raw_manifest = pipeline._json_bytes(manifest)
    recheck_fingerprints(cache["fingerprints"])
    guard = OutputRootGuard.create(outdir, OUTPUT_BASE)
    try:
        production._write_new(descriptor_path, raw_descriptor, guard)
        production._write_new(outdir / "production-manifest.json", raw_manifest, guard)
        recheck_fingerprints(cache["fingerprints"])
        guard.verify_current()
    finally:
        guard.close()
    return {"schema": "h3.blender-action.production-preparation.v1", "status": "PREPARED_REVIEW_REQUIRED",
            "operation_id": args.operation_id, "action_operation_id": args.action_operation_id,
            "layer": args.layer, "layout": "native-canvas", "slot_count": len(slots),
            "manifest": {"path": str(outdir / "production-manifest.json"),
                         "sha256": pipeline.sha256_bytes(raw_manifest)},
            "action_binding": descriptor_ref, "images_written": 0,
            "art_approved": False, "game_deployed": False, "runtime_accepted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-from-actions", "production-manifest"])
    parser.add_argument("--action-manifest", required=True)
    parser.add_argument("--action-manifest-sha256", required=True)
    parser.add_argument("--action-operation-id", required=True)
    parser.add_argument("--action-outdir", required=True)
    parser.add_argument("--layer", required=True, choices=sorted(LAYERS))
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args)
    except Exception as error:
        result = {"schema": "h3.blender-action.production-preparation.v1", "status": "BLOCKED",
                  "code": getattr(error, "code", "ACTION_PREPARATION_FAILED"), "error": str(error),
                  "operation_id": args.operation_id, "images_written": 0,
                  "art_approved": False, "game_deployed": False, "runtime_accepted": False}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
