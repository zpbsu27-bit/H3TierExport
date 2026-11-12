"""Prepare a VCMI map-object animation override from an official one-frame DEF group.

This command packages already-authored full-canvas 3X frames. It never draws,
resizes, writes a canonical master, edits a MOD, or changes the original DEF/LOD.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image

import workspace
from tool_paths import resolve_tool_root

PRODUCT = resolve_tool_root(__file__)
OUT_ROOT = PRODUCT / "build" / "experiments" / "map-object-sequence"
REQUEST_ROOT = PRODUCT / "build" / "experiments" / "map-object-sequence-requests"
SCHEMA = "h3.map-object-sequence.request.v1"
REPORT_SCHEMA = "h3.map-object-sequence.report.v1"
RECEIPT_SCHEMA = "h3.map-object-sequence.receipt.v1"
IMPLEMENTATION = Path(__file__).resolve()
IMPLEMENTATION_SHA256 = hashlib.sha256(IMPLEMENTATION.read_bytes()).hexdigest()
FRAME_TIME_MS = 180
MAX_INPUT_FRAMES = 120
MAX_SEQUENCE_FRAMES = 120
FRAME_NAME = re.compile(r"^(?P<group>[0-9]+)_(?P<frame>[0-9]+)\.png$", re.IGNORECASE)
OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
IDENTITY = re.compile(r"^[A-Z0-9_]{1,16}$")


class SequenceError(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise SequenceError(code)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_ref(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve(strict=True)
    need(resolved.is_file(), "INPUT_NOT_A_FILE: " + str(resolved))
    return {"path": str(resolved), "sha256": sha_file(resolved), "bytes": resolved.stat().st_size}


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("xb") as stream:
        stream.write(data)


def _load_inventory(path: Path) -> dict[str, Any]:
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix.casefold() == ".gz" else path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
        raise SequenceError("OFFICIAL_INVENTORY_UNREADABLE: " + str(exc)) from exc
    need(value.get("schema") == "h3.complete-art-catalog.v3", "OFFICIAL_INVENTORY_SCHEMA_UNSUPPORTED")
    need(isinstance(value.get("rows"), list), "OFFICIAL_INVENTORY_ROWS_MISSING")
    return value


def _native_authority(identity: str, group: int, asset_root: Path) -> dict[str, Any]:
    inventory_path = (asset_root / "codex-map" / "native-resource-inventory.json.gz").resolve(strict=True)
    inventory = _load_inventory(inventory_path)
    rows = [row for row in inventory["rows"]
            if row.get("kind") == "def" and row.get("identity") == identity]
    need(bool(rows), "OFFICIAL_IDENTITY_NOT_IN_NATIVE_INVENTORY: " + identity)
    group_rows = sorted((row for row in rows if row.get("group") == group),
                        key=lambda row: row.get("frame", -1))
    need(bool(group_rows), f"OFFICIAL_GROUP_MISSING: {identity} group {group}")
    need(len(group_rows) == 1 and isinstance(group_rows[0].get("frame"), int) and
         group_rows[0]["frame"] >= 0,
         f"NATIVE_GROUP_IS_NOT_SINGLE_FRAME: {identity} group {group} has {len(group_rows)} frames")
    native = group_rows[0]
    need(native.get("category") == "adventure_objects", "MAP_OBJECT_CATEGORY_REQUIRED")
    need(native.get("native_lod") in ("H3sprite.lod", "H3ab_spr.lod"), "OFFICIAL_DEF_LOD_REQUIRED")
    canvas, crop, origin = native.get("canvas"), native.get("crop"), native.get("anchor")
    need(all(isinstance(value, list) and len(value) == 2 and
             all(isinstance(part, int) and part > 0 for part in value)
             for value in (canvas, crop)), "OFFICIAL_GEOMETRY_INVALID")
    need(isinstance(origin, list) and len(origin) == 2 and
         all(isinstance(part, int) for part in origin), "OFFICIAL_ORIGIN_INVALID")

    checkpoint_dir = asset_root / "codex-map" / "sources" / "complete" / "checkpoints"
    checkpoints = sorted(checkpoint_dir.glob(f"def_{identity}__*.json"))
    need(len(checkpoints) == 1, "OFFICIAL_IDENTITY_CHECKPOINT_NOT_UNIQUE: " + identity)
    checkpoint_path = checkpoints[0].resolve(strict=True)
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SequenceError("OFFICIAL_IDENTITY_CHECKPOINT_UNREADABLE: " + str(exc)) from exc
    need(checkpoint.get("schema") == "h3.reference-library.identity-checkpoint.v1",
         "OFFICIAL_IDENTITY_CHECKPOINT_SCHEMA_UNSUPPORTED")
    source_key = f"def:{identity}:{group}:{native['frame']}"
    image_record = (checkpoint.get("images") or {}).get(source_key)
    need(isinstance(image_record, dict), "OFFICIAL_FRAME_NOT_PINNED_IN_CHECKPOINT: " + source_key)
    expected_rel = f"sources/complete/frames/{identity}/{group}_{native['frame']}.png"
    need(str(image_record.get("path", "")).replace("\\", "/") == expected_rel,
         "OFFICIAL_FRAME_CHECKPOINT_PATH_DIFFERS: " + source_key)
    native_frame = (asset_root / "codex-map" / Path(expected_rel)).resolve(strict=True)
    native_ref = file_ref(native_frame)
    need(image_record.get("sha256") == native_ref["sha256"], "OFFICIAL_FRAME_CHECKPOINT_SHA_DIFFERS")
    need([image_record.get("width"), image_record.get("height")] == canvas,
         "OFFICIAL_FRAME_CHECKPOINT_GEOMETRY_DIFFERS")
    with Image.open(native_frame) as image:
        image.load()
        need(image.format == "PNG" and image.mode == "RGBA", "OFFICIAL_1X_FRAME_MUST_BE_RGBA_PNG")
        need(image.size == tuple(canvas), "OFFICIAL_1X_FRAME_CANVAS_DIFFERS")
        alpha_box = image.getchannel("A").getbbox()
        need(alpha_box is not None, "OFFICIAL_1X_FRAME_IS_BLANK")

    return {
        "inventory": file_ref(inventory_path),
        "checkpoint": file_ref(checkpoint_path),
        "checkpoint_source_payload_sha256": checkpoint.get("source_payload_sha256"),
        "row": {
            "kind": native["kind"], "identity": identity, "group": group,
            "frame": native["frame"], "resource": native["resource"],
            "native_lod": native["native_lod"], "category": native["category"],
            "canvas": canvas, "crop": crop, "origin": origin,
            "official_group_frame_count": len(group_rows),
        },
        "native_frame": native_ref,
        "native_frame_alpha_bbox": list(alpha_box),
    }


def _inspect_candidate(path: Path, expected_size: tuple[int, int]) -> dict[str, Any]:
    original = Path(path)
    need(not original.is_symlink(), "CANDIDATE_FRAME_SYMLINK_NOT_ALLOWED: " + str(original))
    resolved = original.resolve(strict=True)
    need(resolved.is_file(), "CANDIDATE_FRAME_NOT_REGULAR_FILE: " + str(resolved))
    try:
        with Image.open(resolved) as image:
            image.load()
            need(image.format == "PNG", "CANDIDATE_FRAME_MUST_BE_PNG: " + resolved.name)
            need(image.mode == "RGBA", "CANDIDATE_FRAME_MUST_BE_RGBA: " + resolved.name)
            need(image.size == expected_size,
                 f"CANDIDATE_FRAME_MUST_BE_FULL_CANVAS_3X: {resolved.name} expected {expected_size}, got {image.size}")
            alpha = image.getchannel("A")
            alpha_box = alpha.getbbox()
            need(alpha_box is not None, "CANDIDATE_FRAME_IS_BLANK: " + resolved.name)
            alpha_extrema = list(alpha.getextrema())
    except (OSError, ValueError) as exc:
        if isinstance(exc, SequenceError):
            raise
        raise SequenceError("CANDIDATE_FRAME_UNREADABLE: " + str(exc)) from exc
    return {"source_index": -1, "source": file_ref(resolved), "pixels": list(expected_size),
            "alpha_bbox": list(alpha_box), "alpha_extrema": alpha_extrema}


def freeze(identity: str, group: int, frames_dir: Path, operation_id: str,
           repeat_each: int) -> dict[str, Any]:
    need(OPERATION_ID.fullmatch(operation_id) is not None, "MAP_SEQUENCE_OPERATION_ID_INVALID")
    identity = identity.upper()
    need(IDENTITY.fullmatch(identity) is not None, "MAP_SEQUENCE_IDENTITY_INVALID")
    need(0 <= group <= 255, "MAP_SEQUENCE_GROUP_OUT_OF_RANGE")
    need(1 <= repeat_each <= 12, "MAP_SEQUENCE_REPEAT_EACH_OUT_OF_RANGE")
    raw_source_root = Path(frames_dir)
    need(not raw_source_root.is_symlink(), "CANDIDATE_FRAMES_DIR_SYMLINK_NOT_ALLOWED")
    source_root = raw_source_root.resolve(strict=True)
    need(source_root.is_dir(), "CANDIDATE_FRAMES_DIR_INVALID")
    asset = workspace.asset_root().resolve(strict=True)
    official = _native_authority(identity, group, asset)
    canvas = official["row"]["canvas"]
    expected_size = (canvas[0] * 3, canvas[1] * 3)

    numbered: dict[int, Path] = {}
    for path in source_root.iterdir():
        if path.suffix.casefold() != ".png":
            continue
        match = FRAME_NAME.fullmatch(path.name)
        need(match is not None, "UNEXPECTED_PNG_IN_CANDIDATE_FRAMES_DIR: " + path.name)
        need(int(match.group("group")) == group, "CANDIDATE_FRAME_GROUP_DIFFERS: " + path.name)
        number = int(match.group("frame"))
        need(path.name.casefold() == f"{group}_{number}.png".casefold(),
             "CANDIDATE_FRAME_NAME_MUST_USE_CANONICAL_DECIMAL_INDEX: " + path.name)
        need(number not in numbered, "DUPLICATE_CANDIDATE_FRAME_INDEX: " + str(number))
        numbered[number] = path
    need(numbered, "NO_CANDIDATE_3X_FRAMES_FOUND")
    numbers = sorted(numbered)
    need(numbers == list(range(len(numbers))), "CANDIDATE_FRAME_INDICES_MUST_BE_CONTIGUOUS_FROM_ZERO")
    need(len(numbers) >= 2, "MAP_SEQUENCE_REQUIRES_TWO_OR_MORE_AUTHORED_KEYFRAMES")
    need(len(numbers) <= MAX_INPUT_FRAMES, "CANDIDATE_FRAME_COUNT_EXCEEDS_LIMIT")
    sequence_count = len(numbers) * repeat_each
    need(sequence_count <= MAX_SEQUENCE_FRAMES, "EXPANDED_SEQUENCE_FRAME_COUNT_EXCEEDS_LIMIT")

    candidate_frames = []
    for number in numbers:
        row = _inspect_candidate(numbered[number], expected_size)
        row["source_index"] = number
        candidate_frames.append(row)

    resource_stem = Path(official["row"]["resource"]).stem
    need(resource_stem and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", resource_stem) is not None,
         "OFFICIAL_RESOURCE_STEM_UNSAFE")
    request = {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "identity": identity,
        "group": group,
        "official_native": official,
        "frames_directory": str(source_root),
        "candidate_3x_keyframes": candidate_frames,
        "repeat_each_keyframe": repeat_each,
        "sequence_frame_count": sequence_count,
        "frame_duration_ms": FRAME_TIME_MS,
        "cycle_duration_ms": FRAME_TIME_MS * sequence_count,
        "animation_timing_source": "VCMI MapRendererAdventureContext::objectImageIndex; 180 ms per adventure-object frame",
        "full_canvas_3x_pixels": list(expected_size),
        "resource_stem": resource_stem,
        "implementation": file_ref(IMPLEMENTATION),
        "sequence_contract": "replace-one-official-single-frame-group-with-a-pinned-contiguous-json-sequence",
        "base_1x_contract": "repeat-the-exact-official-1x-frame-as-static-fallback-for-every-added-slot",
        "production_writes": False,
    }
    need(request["implementation"]["sha256"] == IMPLEMENTATION_SHA256,
         "MAP_SEQUENCE_IMPLEMENTATION_CHANGED_DURING_FREEZE")
    target = REQUEST_ROOT / f"{operation_id}.json"
    raw = json_bytes(request)
    write_new(target, raw)
    return {
        "status": "REQUEST_FROZEN",
        "manifest": str(target.resolve()),
        "manifest_sha256": sha_bytes(raw),
        "official_frame_count": official["row"]["official_group_frame_count"],
        "authored_keyframe_count": len(candidate_frames),
        "sequence_frame_count": sequence_count,
        "repeat_each_keyframe": repeat_each,
        "cycle_duration_ms": FRAME_TIME_MS * sequence_count,
        "candidate_outdir": str((OUT_ROOT / operation_id).resolve()),
        "writes_masters_or_mod": False,
    }


def _load_request(manifest_path: Path, manifest_sha256: str, operation_id: str) -> tuple[Path, dict[str, Any]]:
    path = Path(manifest_path).resolve(strict=True)
    need(path.is_file(), "MAP_SEQUENCE_MANIFEST_NOT_A_FILE")
    raw = path.read_bytes()
    need(re.fullmatch(r"[0-9a-fA-F]{64}", manifest_sha256 or "") is not None,
         "MAP_SEQUENCE_MANIFEST_SHA_REQUIRED")
    need(sha_bytes(raw) == manifest_sha256.lower(), "MAP_SEQUENCE_MANIFEST_SHA_MISMATCH")
    try:
        request = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SequenceError("MAP_SEQUENCE_MANIFEST_UNREADABLE: " + str(exc)) from exc
    need(request.get("schema") == SCHEMA, "MAP_SEQUENCE_MANIFEST_SCHEMA_UNSUPPORTED")
    need(request.get("operation_id") == operation_id and OPERATION_ID.fullmatch(operation_id) is not None,
         "MAP_SEQUENCE_OPERATION_MISMATCH")
    return path, request


def _preflight(request: dict[str, Any], manifest_path: Path, output_root: Path) -> dict[str, Any]:
    need(request.get("implementation") == file_ref(IMPLEMENTATION), "MAP_SEQUENCE_IMPLEMENTATION_DRIFT")
    asset = workspace.asset_root().resolve(strict=True)
    expected_inventory = (asset / "codex-map" / "native-resource-inventory.json.gz").resolve(strict=True)
    official = _native_authority(request["identity"], request["group"], asset)
    need(official["inventory"]["path"] == str(expected_inventory), "BOUND_NATIVE_INVENTORY_PATH_CHANGED")
    need(official == request.get("official_native"), "OFFICIAL_NATIVE_AUTHORITY_DRIFT")
    need(request.get("sequence_contract") ==
         "replace-one-official-single-frame-group-with-a-pinned-contiguous-json-sequence",
         "MAP_SEQUENCE_CONTRACT_UNSUPPORTED")
    need(request.get("base_1x_contract") ==
         "repeat-the-exact-official-1x-frame-as-static-fallback-for-every-added-slot",
         "MAP_SEQUENCE_1X_FALLBACK_CONTRACT_UNSUPPORTED")
    need(request.get("production_writes") is False, "MAP_SEQUENCE_PRODUCTION_WRITE_NOT_ALLOWED")
    need(official["row"]["official_group_frame_count"] == 1, "NATIVE_GROUP_IS_NOT_SINGLE_FRAME")
    need(request.get("frame_duration_ms") == FRAME_TIME_MS, "MAP_SEQUENCE_FRAME_TIME_CONTRACT_DIFFERS")
    need(request.get("animation_timing_source") ==
         "VCMI MapRendererAdventureContext::objectImageIndex; 180 ms per adventure-object frame",
         "MAP_SEQUENCE_TIMING_SOURCE_UNSUPPORTED")
    canvas = official["row"]["canvas"]
    expected_size = (canvas[0] * 3, canvas[1] * 3)
    need(request.get("full_canvas_3x_pixels") == list(expected_size), "MAP_SEQUENCE_3X_CANVAS_CONTRACT_DIFFERS")
    raw_frames_dir = Path(request["frames_directory"])
    need(not raw_frames_dir.is_symlink(), "CANDIDATE_FRAMES_DIR_SYMLINK_NOT_ALLOWED")
    frames_dir = raw_frames_dir.resolve(strict=True)
    need(frames_dir.is_dir(), "CANDIDATE_FRAMES_DIR_CHANGED")
    frames = request.get("candidate_3x_keyframes")
    need(isinstance(frames, list) and 0 < len(frames) <= MAX_INPUT_FRAMES, "MAP_SEQUENCE_KEYFRAME_LIST_INVALID")
    need(len(frames) >= 2, "MAP_SEQUENCE_REQUIRES_TWO_OR_MORE_AUTHORED_KEYFRAMES")
    numbers: dict[int, dict[str, Any]] = {}
    for index, frame in enumerate(frames):
        need(frame.get("source_index") == index, "MAP_SEQUENCE_KEYFRAME_ORDER_INVALID")
        source_ref = frame.get("source")
        need(isinstance(source_ref, dict), "MAP_SEQUENCE_KEYFRAME_SOURCE_MISSING")
        source = Path(source_ref.get("path", "")).resolve(strict=True)
        need(source.parent == frames_dir, "MAP_SEQUENCE_KEYFRAME_OUTSIDE_FROZEN_DIRECTORY")
        match = FRAME_NAME.fullmatch(source.name)
        need(match is not None and int(match.group("group")) == request["group"] and
             int(match.group("frame")) == index, "MAP_SEQUENCE_KEYFRAME_NAME_DIFFERS")
        need(source.name.casefold() == f"{request['group']}_{index}.png".casefold(),
             "MAP_SEQUENCE_KEYFRAME_NAME_MUST_USE_CANONICAL_DECIMAL_INDEX")
        need(file_ref(source) == source_ref, "MAP_SEQUENCE_KEYFRAME_SOURCE_DRIFT: " + source.name)
        inspected = _inspect_candidate(source, expected_size)
        need(inspected["pixels"] == frame.get("pixels") and inspected["alpha_bbox"] == frame.get("alpha_bbox") and
             inspected["alpha_extrema"] == frame.get("alpha_extrema"),
             "MAP_SEQUENCE_KEYFRAME_DIAGNOSTIC_DRIFT: " + source.name)
        numbers[index] = frame
    repeat = request.get("repeat_each_keyframe")
    need(isinstance(repeat, int) and 1 <= repeat <= 12, "MAP_SEQUENCE_REPEAT_EACH_INVALID")
    sequence_count = len(frames) * repeat
    need(sequence_count <= MAX_SEQUENCE_FRAMES and request.get("sequence_frame_count") == sequence_count,
         "MAP_SEQUENCE_FRAME_COUNT_INVALID")
    need(request.get("cycle_duration_ms") == sequence_count * FRAME_TIME_MS,
         "MAP_SEQUENCE_CYCLE_DURATION_INVALID")
    need(request.get("resource_stem") == Path(official["row"]["resource"]).stem,
         "MAP_SEQUENCE_RESOURCE_STEM_DIFFERS")
    need(IDENTITY.fullmatch(request["identity"]) is not None, "MAP_SEQUENCE_IDENTITY_INVALID")
    need(0 <= request["group"] <= 255, "MAP_SEQUENCE_GROUP_OUT_OF_RANGE")

    operation = request["operation_id"]
    expected_output = (OUT_ROOT / operation).resolve()
    target = Path(output_root).resolve()
    need(target == expected_output, "MAP_SEQUENCE_OUTPUT_MUST_USE_ISOLATED_OPERATION_DIRECTORY")
    pinned_sources = [Path(request["official_native"]["native_frame"]["path"]).resolve(),
                      Path(request["official_native"]["inventory"]["path"]).resolve(), manifest_path.resolve(),
                      *(Path(frame["source"]["path"]).resolve() for frame in frames)]
    need(all(not source.is_relative_to(target) for source in pinned_sources),
         "MAP_SEQUENCE_OUTPUT_CONTAINS_PINNED_SOURCE")
    need(not frames_dir.is_relative_to(target) and not target.is_relative_to(frames_dir),
         "MAP_SEQUENCE_OUTPUT_OVERLAPS_CANDIDATE_SOURCE_DIRECTORY")
    return {"manifest": file_ref(manifest_path), "output_root": target, "frames": numbers,
            "repeat_each": repeat, "sequence_count": sequence_count,
            "native_frame": Path(official["native_frame"]["path"]).resolve(),
            "native_frame_sha256": official["native_frame"]["sha256"],
            "expected_size": expected_size}


def _expected_package(request: dict[str, Any], manifest_ref: dict[str, Any],
                      preflight: dict[str, Any]) -> tuple[list[dict[str, Any]], bytes, bytes]:
    identity, group = request["identity"], request["group"]
    resource = request["resource_stem"]
    names = [f"{resource}_g{group}_f{index}.png" for index in range(preflight["sequence_count"])]
    descriptor = {"basepath": f"{identity}/", "sequences": [{"group": group, "frames": names}]}
    descriptor_raw = json_bytes(descriptor)
    outputs: list[dict[str, Any]] = []
    for scale_root in ("Sprites", "Sprites3x"):
        descriptor_path = f"content/{scale_root}/{identity}.json"
        outputs.append({"path": descriptor_path, "sha256": sha_bytes(descriptor_raw), "role": f"{scale_root}_sequence_descriptor"})
    frame_map = []
    candidate_frames = request["candidate_3x_keyframes"]
    for slot, name in enumerate(names):
        source_index = slot // preflight["repeat_each"]
        source = candidate_frames[source_index]["source"]
        frame_map.append({"slot": slot, "filename": name, "held_keyframe_index": source_index,
                          "repeat_slot": slot % preflight["repeat_each"],
                          "1x_source_sha256": preflight["native_frame_sha256"],
                          "3x_source_sha256": source["sha256"]})
        outputs.append({"path": f"content/Sprites/{identity}/{name}",
                        "sha256": preflight["native_frame_sha256"], "role": "official_static_1x_fallback",
                        "slot": slot, "source_path": str(preflight["native_frame"]),
                        "source_sha256": preflight["native_frame_sha256"]})
        outputs.append({"path": f"content/Sprites3x/{identity}/{name}",
                        "sha256": source["sha256"], "role": "authored_3x_animation_frame",
                        "slot": slot, "source_path": source["path"], "source_sha256": source["sha256"]})

    report = {
        "schema": REPORT_SCHEMA,
        "status": "CANDIDATE_PACKAGE",
        "operation_id": request["operation_id"], "identity": identity, "group": group,
        "official_native_frame_count": 1,
        "authored_keyframe_count": len(candidate_frames),
        "sequence_frame_count": preflight["sequence_count"],
        "repeat_each_keyframe": preflight["repeat_each"],
        "frame_duration_ms": FRAME_TIME_MS,
        "cycle_duration_ms": preflight["sequence_count"] * FRAME_TIME_MS,
        "canvas_1x": request["official_native"]["row"]["canvas"],
        "canvas_3x": list(preflight["expected_size"]),
        "official_crop_1x": request["official_native"]["row"]["crop"],
        "official_origin_1x": request["official_native"]["row"]["origin"],
        "native_def_lod_modified": False,
        "canonical_master_or_3x_modified": False,
        "mod_installed": False,
        "game_runtime_accepted": False,
        "art_approved": False,
        "manifest": manifest_ref,
        "frame_map": frame_map,
        "package_files": outputs,
        "review_notes": [
            "The 1X sequence uses the original official still as a static fallback for every slot.",
            "The Sprites and Sprites3x descriptors replace the selected group; their frame names must remain identical.",
            "Image dimensions and hashes are checked mechanically; pose continuity, scale, loop seam and visual quality require human review.",
        ],
    }
    report_raw = json_bytes(report)
    return outputs, descriptor_raw, report_raw


def execute(action: str, manifest_path: Path, manifest_sha256: str,
            outdir: Path, operation_id: str) -> dict[str, Any]:
    path, request = _load_request(manifest_path, manifest_sha256, operation_id)
    preflight = _preflight(request, path, outdir)
    outputs, descriptor_raw, report_raw = _expected_package(request, preflight["manifest"], preflight)
    root: Path = preflight["output_root"]
    if action == "check":
        return {"status": "CHECKED_WITHOUT_WRITING", "operation_id": operation_id,
                "official_frame_count": 1, "authored_keyframe_count": len(request["candidate_3x_keyframes"]),
                "sequence_frame_count": preflight["sequence_count"],
                "repeat_each_keyframe": preflight["repeat_each"],
                "cycle_duration_ms": preflight["sequence_count"] * FRAME_TIME_MS,
                "package_file_count": len(outputs), "outdir": str(root),
                "writes_masters_or_mod": False}

    if action == "run":
        need(not root.exists(), "MAP_SEQUENCE_OPERATION_OUTPUT_ALREADY_EXISTS")
        root.mkdir(parents=True, exist_ok=False)
        for output in outputs:
            target = root / Path(output["path"])
            if output["role"].endswith("sequence_descriptor"):
                write_new(target, descriptor_raw)
                continue
            source = Path(output["source_path"])
            actual = source.read_bytes()
            need(sha_bytes(actual) == output["source_sha256"], "MAP_SEQUENCE_SOURCE_CHANGED_DURING_RUN")
            write_new(target, actual)
        write_new(root / "sequence-report.json", report_raw)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "PACKAGE_CANDIDATE_WRITTEN",
            "operation_id": operation_id,
            "manifest": preflight["manifest"],
            "report": {"path": "sequence-report.json", "sha256": sha_bytes(report_raw)},
            "files": outputs + [{"path": "sequence-report.json", "sha256": sha_bytes(report_raw),
                                  "role": "sequence_report"}],
            "mod_installed": False,
            "game_runtime_accepted": False,
        }
        write_new(root / "receipt.json", json_bytes(receipt))
        return {"status": "CANDIDATE_PACKAGE_CREATED", "operation_id": operation_id,
                "sequence_frame_count": preflight["sequence_count"],
                "outdir": str(root), "report": str(root / "sequence-report.json"),
                "receipt": str(root / "receipt.json"), "writes_masters_or_mod": False}

    need(action == "verify", "MAP_SEQUENCE_ACTION_UNSUPPORTED")
    need(root.is_dir(), "MAP_SEQUENCE_OPERATION_OUTPUT_MISSING")
    expected_receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PACKAGE_CANDIDATE_WRITTEN",
        "operation_id": operation_id,
        "manifest": preflight["manifest"],
        "report": {"path": "sequence-report.json", "sha256": sha_bytes(report_raw)},
        "files": outputs + [{"path": "sequence-report.json", "sha256": sha_bytes(report_raw),
                              "role": "sequence_report"}],
        "mod_installed": False,
        "game_runtime_accepted": False,
    }
    need((root / "sequence-report.json").is_file() and
         (root / "sequence-report.json").read_bytes() == report_raw,
         "MAP_SEQUENCE_REPORT_REPLAY_MISMATCH")
    receipt_path = root / "receipt.json"
    need(receipt_path.is_file(), "MAP_SEQUENCE_RECEIPT_MISSING")
    try:
        actual_receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SequenceError("MAP_SEQUENCE_RECEIPT_UNREADABLE: " + str(exc)) from exc
    need(actual_receipt == expected_receipt, "MAP_SEQUENCE_RECEIPT_MISMATCH")
    for output in outputs:
        target = root / Path(output["path"])
        need(target.is_file(), "MAP_SEQUENCE_OUTPUT_MISSING: " + output["path"])
        actual = target.read_bytes()
        need(sha_bytes(actual) == output["sha256"], "MAP_SEQUENCE_OUTPUT_SHA_MISMATCH: " + output["path"])
        if output["role"].endswith("sequence_descriptor"):
            need(actual == descriptor_raw, "MAP_SEQUENCE_DESCRIPTOR_MISMATCH: " + output["path"])
        else:
            source = Path(output["source_path"])
            need(actual == source.read_bytes(), "MAP_SEQUENCE_OUTPUT_PIXELS_OR_BYTES_DIFFER: " + output["path"])
    return {"status": "VERIFIED_CANDIDATE_PACKAGE", "operation_id": operation_id,
            "sequence_frame_count": preflight["sequence_count"], "verified_file_count": len(outputs) + 1,
            "outdir": str(root), "mod_installed": False, "game_runtime_accepted": False,
            "art_approved": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    frozen = sub.add_parser("freeze", help="pin a single-frame official group and full-canvas 3X candidates")
    frozen.add_argument("--identity", required=True)
    frozen.add_argument("--group", required=True, type=int)
    frozen.add_argument("--frames-dir", required=True, type=Path)
    frozen.add_argument("--operation-id", required=True)
    frozen.add_argument("--repeat-each", type=int, default=1,
                        help="repeat each authored pose in adjacent slots to slow the loop; default 1")
    frozen.set_defaults(handler=None)
    for action in ("check", "run", "verify"):
        stage = sub.add_parser(action, help=f"{action} a frozen map-object sequence request")
        stage.add_argument("--manifest", required=True, type=Path)
        stage.add_argument("--manifest-sha256", required=True)
        stage.add_argument("--outdir", required=True, type=Path)
        stage.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "freeze":
            result = freeze(args.identity, args.group, args.frames_dir, args.operation_id, args.repeat_each)
        else:
            result = execute(args.action, args.manifest, args.manifest_sha256, args.outdir, args.operation_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (SequenceError, OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        print(json.dumps({"status": "BLOCKED", "code": "MAP_OBJECT_SEQUENCE", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
