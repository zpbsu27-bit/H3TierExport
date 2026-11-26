"""Repackage a static VCMI Sprites3x sequence from a verified full-canvas 3X batch.

The check stage rejects a live/working MOD frame set that differs from the
verified expansion source. The run stage rebuilds an isolated overlay candidate
from that source; it never edits the installed MOD, masters, or formal 3X tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from PIL import Image

import workspace
from campaign_vcmi_mod import load_native_def_authority
from tool_paths import resolve_tool_root

PRODUCT = resolve_tool_root(__file__)
REQUEST_SCHEMA = "h3.vcmi.sprites3x-reprocess.request.v1"
RECEIPT_SCHEMA = "h3.vcmi.sprites3x-reprocess.receipt.v1"
IDENTITY = re.compile(r"^[A-Z0-9_-]{1,15}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
FRAME = re.compile(r"^(0|[1-9][0-9]*)_(0|[1-9][0-9]*)\.png$")
PNG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.png$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SyncError(ValueError):
    pass


def need(value: bool, message: str) -> None:
    if not value:
        raise SyncError(message)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def assert_plain(path: Path, *, must_exist: bool = True) -> Path:
    path = Path(path).absolute()
    nodes = (path, *path.parents)
    for node in nodes:
        try:
            if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
                raise SyncError("PATH_REPARSE_POINT: " + str(node))
            info = node.lstat()
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise SyncError("PATH_REPARSE_POINT: " + str(node))
        except FileNotFoundError:
            if must_exist:
                raise
    if must_exist and not path.exists():
        raise FileNotFoundError(str(path))
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_ref(path: Path) -> dict[str, Any]:
    path = assert_plain(path)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    need(stat.S_ISREG(info.st_mode), "INPUT_NOT_A_REGULAR_FILE: " + str(resolved))
    return {"path": str(resolved), "bytes": info.st_size, "sha256": sha256(resolved)}


def read_pinned_json(path: Path, expected_sha256: str, label: str) -> tuple[dict, dict]:
    need(isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256) is not None,
         label + "_SHA256_INVALID")
    ref = file_ref(path)
    need(ref["sha256"] == expected_sha256, label + "_SHA256_DRIFT")
    try:
        value = json.loads(Path(ref["path"]).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError(label + "_JSON_UNREADABLE: " + str(exc)) from exc
    need(isinstance(value, dict), label + "_JSON_NOT_AN_OBJECT")
    return value, ref


def resolve_inside(root: Path, path: Path, label: str) -> Path:
    root = assert_plain(root).resolve(strict=True)
    path = assert_plain(path).resolve(strict=True)
    need(path.is_relative_to(root), label + "_OUTSIDE_AUTHORIZED_ROOT: " + str(path))
    return path


def resolve_new_inside(root: Path, path: Path, label: str) -> Path:
    root = assert_plain(root).resolve(strict=True)
    path = assert_plain(path, must_exist=False)
    parent = path.parent.resolve(strict=True)
    resolved = parent / path.name
    need(resolved.is_relative_to(root), label + "_OUTSIDE_AUTHORIZED_ROOT: " + str(resolved))
    need(not resolved.exists(), label + "_ALREADY_EXISTS: " + str(resolved))
    return resolved


def image_info(path: Path) -> dict[str, Any]:
    ref = file_ref(path)
    try:
        with Image.open(ref["path"]) as source:
            source.load()
            need(source.format == "PNG" and source.mode == "RGBA",
                 "SPRITES3X_FRAME_MUST_BE_RGBA_PNG: " + ref["path"])
            rgba = source.copy()
    except (OSError, ValueError) as exc:
        raise SyncError("SPRITES3X_FRAME_UNREADABLE: " + ref["path"] + ": " + str(exc)) from exc
    pixels = np.asarray(rgba, dtype=np.uint8)
    visible = pixels.copy()
    visible[visible[:, :, 3] == 0, :3] = 0
    return {
        **ref,
        "pixels": list(rgba.size),
        "alpha_bbox": list(rgba.getchannel("A").getbbox()) if rgba.getchannel("A").getbbox() else None,
        "rgba_pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
        "visible_pixel_sha256": hashlib.sha256(visible.tobytes()).hexdigest(),
    }


def compare_visible_pixels(source_path: Path, target_path: Path) -> dict[str, Any]:
    source = Image.open(source_path).convert("RGBA")
    target = Image.open(target_path).convert("RGBA")
    if source.size != target.size:
        return {"matches": False, "reason": "CANVAS_MISMATCH", "source_pixels": list(source.size),
                "target_pixels": list(target.size), "different_visible_pixels": None}
    a = np.asarray(source, dtype=np.uint8)
    b = np.asarray(target, dtype=np.uint8)
    alpha_changed = a[:, :, 3] != b[:, :, 3]
    visible = (a[:, :, 3] != 0) | (b[:, :, 3] != 0)
    rgb_changed = np.any(a[:, :, :3] != b[:, :, :3], axis=2) & visible
    changed = alpha_changed | rgb_changed
    count = int(np.count_nonzero(changed))
    return {"matches": count == 0, "reason": "PIXELS_MATCH" if count == 0 else "VISIBLE_PIXEL_MISMATCH",
            "source_pixels": list(source.size), "target_pixels": list(target.size),
            "different_visible_pixels": count}


def safe_descriptor(identity: str, sprites3x_root: Path) -> tuple[dict, bytes, dict, list[dict]]:
    sprites3x_root = assert_plain(sprites3x_root).resolve(strict=True)
    need(sprites3x_root.is_dir(), "SPRITES3X_ROOT_NOT_A_DIRECTORY")
    descriptor_path = sprites3x_root / f"{identity}.json"
    descriptor_ref = file_ref(descriptor_path)
    raw = Path(descriptor_ref["path"]).read_bytes()
    try:
        descriptor = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError("SPRITES3X_DESCRIPTOR_UNREADABLE: " + str(exc)) from exc
    need(isinstance(descriptor, dict), "SPRITES3X_DESCRIPTOR_NOT_AN_OBJECT")
    need(descriptor.get("basepath") == identity + "/", "SPRITES3X_BASEPATH_MUST_MATCH_IDENTITY")
    images = descriptor.get("images")
    need(isinstance(images, list) and images, "SPRITES3X_DESCRIPTOR_IMAGES_MISSING")
    rows = []
    seen_slots = set()
    for row in images:
        need(isinstance(row, dict), "SPRITES3X_DESCRIPTOR_ROW_INVALID")
        group, frame, filename = row.get("group"), row.get("frame"), row.get("file")
        need(type(group) is int and group >= 0 and type(frame) is int and frame >= 0,
             "SPRITES3X_DESCRIPTOR_SLOT_INVALID")
        need(isinstance(filename, str) and PNG_NAME.fullmatch(filename) is not None,
             "SPRITES3X_DESCRIPTOR_FILENAME_INVALID")
        slot = (group, frame)
        need(slot not in seen_slots, "SPRITES3X_DESCRIPTOR_SLOT_DUPLICATE: " + repr(slot))
        seen_slots.add(slot)
        rows.append({"group": group, "frame": frame, "file": filename})
    rows.sort(key=lambda item: (item["group"], item["frame"]))
    return descriptor, raw, descriptor_ref, rows


def verified_expansion(identity: str, batch_path: Path, batch_sha256: str,
                       verify_report_path: Path, verify_report_sha256: str) -> tuple[dict, dict, dict, dict, list[dict]]:
    batch, batch_ref = read_pinned_json(batch_path, batch_sha256, "EXPAND_BATCH")
    report, report_ref = read_pinned_json(verify_report_path, verify_report_sha256, "EXPAND_VERIFY_REPORT")
    need(batch.get("schema") == "h3.master-expand.batch.v1", "SPRITES3X_REQUIRES_STANDARD_EXPAND_BATCH")
    gate = batch.get("animation_gate", {})
    animation_contract = batch.get("animation_contract")
    contract_rows = {
        "static-structure-dynamic-fx-v2": ("h3.animation.static-fx.scale-gate.v2", 2),
        "static-base-dynamic-overlay-v1": ("h3.animation.static-base-overlay.scale-gate.v1", 1),
        "static-base-dynamic-overlay-v2": ("h3.animation.static-base-overlay.scale-gate.v2", 2),
        "static-base-authored-fx-roi-v1": ("h3.animation.authored-base-overlay.scale-gate.v1", 1),
        "static-base-authored-fx-roi-v2": ("h3.animation.authored-base-overlay.scale-gate.v2", 2),
        "static-composite-authored-fx-roi-v1": ("h3.animation.authored-composite.scale-gate.v1", 1),
        "static-composite-authored-fx-roi-v2": ("h3.animation.authored-composite.scale-gate.v2", 2),
    }
    expected_gate = contract_rows.get(animation_contract)
    need(expected_gate is not None
         and gate.get("schema") == expected_gate[0]
         and isinstance(gate.get("official_frame_inventory"), list),
         "SPRITES3X_STATIC_ANIMATION_CONTRACT_REQUIRED")
    if animation_contract in {"static-base-dynamic-overlay-v1", "static-base-dynamic-overlay-v2", "static-base-authored-fx-roi-v1", "static-base-authored-fx-roi-v2"}:
        need(gate.get("render_semantics") == "vcmi-base-frame-then-current-frame"
             and gate.get("base_frame") == 0,
             "SPRITES3X_BASE_OVERLAY_RENDER_SEMANTICS_REQUIRED")
    need(report.get("schema") == "h3.master-expand.run.v1" and report.get("status") == "COMPLETED"
         and report.get("failed") == 0 and report.get("outputs_written") == 0,
         "SPRITES3X_REQUIRES_SUCCESSFUL_EXPAND_VERIFY_REPORT")
    registration = report.get("preflight", {}).get("animation_registration", {})
    need(registration.get("contract") == animation_contract
         and registration.get("gate_version") == expected_gate[1] and registration.get("status") == "PASS"
         and not registration.get("failures")
         and registration.get("official_frame_inventory") == gate.get("official_frame_inventory"),
         "SPRITES3X_ANIMATION_GEOMETRY_NOT_PASSED")
    if animation_contract in {"static-composite-authored-fx-roi-v1", "static-composite-authored-fx-roi-v2"}:
        need(gate.get("render_semantics") == "full-frame-per-frame",
             "SPRITES3X_FULL_FRAME_RENDER_SEMANTICS_REQUIRED")
    if animation_contract in {"static-base-authored-fx-roi-v1", "static-base-authored-fx-roi-v2", "static-composite-authored-fx-roi-v1", "static-composite-authored-fx-roi-v2"}:
        roi = gate.get("authored_effect_roi")
        need(isinstance(roi, dict) and isinstance(roi.get("sha256"), str)
             and isinstance(roi.get("pixels"), list) and roi.get("alpha_threshold") == 1,
             "SPRITES3X_AUTHORED_EFFECT_ROI_BINDING_REQUIRED")
        groups = registration.get("groups", [])
        checked_roi = groups[0].get("authored_effect_roi") if groups and isinstance(groups[0], dict) else None
        need(isinstance(checked_roi, dict)
             and checked_roi.get("sha256") == roi.get("sha256")
             and checked_roi.get("pixels") == roi.get("pixels")
             and checked_roi.get("path") == roi.get("path"),
             "SPRITES3X_AUTHORED_EFFECT_ROI_DRIFT")

    inventory_ref, native_by_slot, native_by_identity = load_native_def_authority()
    native_rows = sorted(native_by_identity.get(identity, []), key=lambda row: (row["group"], row["frame"]))
    need(native_rows, "OFFICIAL_NATIVE_IDENTITY_NOT_FOUND: " + identity)
    native_slots = {(row["group"], row["frame"]): row for row in native_rows}
    expected_slots = set(native_slots)
    output_root = Path(batch.get("output_root", "")).resolve(strict=True)
    experiments_root = (PRODUCT / "build" / "experiments").resolve(strict=True)
    need(output_root.is_relative_to(experiments_root), "EXPAND_OUTPUT_ROOT_OUTSIDE_TOOL_EXPERIMENTS")
    items = batch.get("items")
    results = report.get("results")
    need(isinstance(items, list) and items and isinstance(results, list), "EXPAND_BATCH_OR_RESULTS_MISSING")
    results_by_id = {}
    for result in results:
        need(isinstance(result, dict) and isinstance(result.get("id"), str)
             and result["id"] not in results_by_id, "EXPAND_VERIFY_RESULT_DUPLICATE_ID")
        need(result.get("status") == "VERIFIED", "EXPAND_OUTPUT_NOT_VERIFIED: " + str(result.get("id")))
        results_by_id[result["id"]] = result
    need(set(results_by_id) == {item.get("id") for item in items if isinstance(item, dict)},
         "EXPAND_VERIFY_RESULT_SET_DIFFERS_FROM_BATCH")

    frames = {}
    for item in items:
        need(isinstance(item, dict) and isinstance(item.get("id"), str), "EXPAND_BATCH_ITEM_INVALID")
        source_path = Path(item.get("source", "")).resolve(strict=True)
        need(source_path.parent.name.upper() == identity, "EXPAND_SOURCE_IDENTITY_MISMATCH: " + str(source_path))
        match = FRAME.fullmatch(source_path.name)
        need(match is not None, "EXPAND_SOURCE_FILENAME_NOT_CANONICAL: " + str(source_path))
        group, frame = int(match.group(1)), int(match.group(2))
        slot = (group, frame)
        need(slot in expected_slots and slot not in frames, "EXPAND_SLOT_EXTRA_OR_DUPLICATE: " + repr(slot))
        native = native_slots[slot]
        canvas = native.get("canvas")
        need(isinstance(canvas, list) and len(canvas) == 2 and item.get("base_pixels") == canvas
             and item.get("target_scale") == 3,
             "EXPAND_MUST_TARGET_OFFICIAL_FULL_CANVAS_3X: " + f"{identity} {group}_{frame}")
        source_ref = file_ref(source_path)
        need(source_ref["sha256"] == item.get("source_sha256"), "EXPAND_MASTER_SHA_DIFFERS_FROM_BATCH")
        output_path = resolve_inside(output_root, Path(item.get("output", "")), "EXPAND_OUTPUT")
        expected_pixels = [canvas[0] * 3, canvas[1] * 3]
        output_info = image_info(output_path)
        need(output_info["pixels"] == expected_pixels, "EXPAND_OUTPUT_NOT_FULL_CANVAS_3X: " + str(slot))
        result = results_by_id[item["id"]]
        receipt_path = resolve_inside(output_root / "receipts", Path(result.get("receipt", "")), "EXPAND_RECEIPT")
        receipt_ref = file_ref(receipt_path)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SyncError("EXPAND_RECEIPT_UNREADABLE: " + str(exc)) from exc
        need(receipt.get("schema") == "h3.master-expand.receipt.v1"
             and receipt.get("contract", {}).get("entry") == item
             and receipt.get("contract", {}).get("output_root") == batch["output_root"]
             and receipt.get("png_sha256") == output_info["sha256"],
             "EXPAND_OUTPUT_RECEIPT_MISMATCH: " + item["id"])
        frames[slot] = {
            "group": group, "frame": frame, "native_canvas": canvas,
            "expected_pixels": expected_pixels, "source_master": source_ref,
            "expand_output": output_info, "expand_receipt": receipt_ref,
            "batch_item_id": item["id"],
        }
    need(set(frames) == expected_slots,
         "EXPAND_BATCH_MUST_COVER_EVERY_OFFICIAL_SLOT: "
         f"missing={sorted(expected_slots-set(frames))}, extra={sorted(set(frames)-expected_slots)}")
    return batch, batch_ref, report_ref, inventory_ref, [frames[slot] for slot in sorted(frames)]


def current_snapshot(sprites3x_root: Path, identity: str, descriptor_ref: dict,
                     mappings: list[dict], frames: list[dict]) -> tuple[dict, list[dict]]:
    root = assert_plain(sprites3x_root).resolve(strict=True)
    descriptor_path = root / f"{identity}.json"
    actual_descriptor = file_ref(descriptor_path)
    need(actual_descriptor["sha256"] == descriptor_ref["sha256"],
         "SPRITES3X_DESCRIPTOR_CHANGED_AFTER_FREEZE")
    folder = root / identity
    if folder.exists():
        folder = resolve_inside(root, folder, "SPRITES3X_IDENTITY_FOLDER")
        need(folder.is_dir(), "SPRITES3X_IDENTITY_PATH_NOT_DIRECTORY")
    unique_mapping = {}
    for mapping, frame in zip(mappings, frames):
        slot = f"{mapping['group']}_{mapping['frame']}"
        row = unique_mapping.setdefault(mapping["file"], {"file": mapping["file"], "slots": [], "source": frame["expand_output"]})
        if row["source"]["visible_pixel_sha256"] != frame["expand_output"]["visible_pixel_sha256"]:
            raise SyncError("DESCRIPTOR_REUSES_FILENAME_FOR_DIFFERENT_SOURCE_PIXELS: " + mapping["file"])
        row["slots"].append(slot)
    files = []
    mismatches = []
    expected_names = {name.casefold() for name in unique_mapping}
    for filename, row in sorted(unique_mapping.items()):
        target = folder / filename
        slots = row["slots"]
        if not target.exists():
            files.append({"file": filename, "exists": False, "slots": slots})
            mismatches.append({"file": filename, "slots": slots, "reason": "MISSING_MOD_FRAME"})
            continue
        target_info = image_info(target)
        comparison = compare_visible_pixels(Path(row["source"]["path"]), target)
        files.append({"file": filename, "exists": True, "slots": slots, "image": target_info,
                      "source_matches": comparison["matches"]})
        if not comparison["matches"]:
            mismatches.append({"file": filename, "slots": slots, "reason": comparison["reason"],
                               "source_pixels": comparison["source_pixels"],
                               "target_pixels": comparison["target_pixels"],
                               "different_visible_pixels": comparison["different_visible_pixels"],
                               "source_sha256": row["source"]["sha256"],
                               "target_sha256": target_info["sha256"]})
    extra_pngs = []
    if folder.exists():
        for path in sorted(folder.rglob("*.png"), key=lambda value: value.relative_to(folder).as_posix().casefold()):
            assert_plain(path)
            relative_name = path.relative_to(folder).as_posix()
            if relative_name.casefold() not in expected_names:
                ref = file_ref(path)
                extra_pngs.append({"file": relative_name, "sha256": ref["sha256"], "bytes": ref["bytes"]})
                mismatches.append({"file": relative_name, "reason": "UNMAPPED_MOD_PNG", "target_sha256": ref["sha256"]})
    return {"descriptor": actual_descriptor, "files": files, "extra_pngs": extra_pngs}, mismatches


def collect_request_inputs(identity: str, sprites3x_root: Path, batch_path: Path, batch_sha256: str,
                           verify_report_path: Path, verify_report_sha256: str) -> dict:
    identity = identity.upper()
    need(IDENTITY.fullmatch(identity) is not None, "SPRITES3X_IDENTITY_INVALID")
    descriptor, descriptor_raw, descriptor_ref, mappings = safe_descriptor(identity, sprites3x_root)
    batch, batch_ref, report_ref, inventory_ref, frames = verified_expansion(
        identity, batch_path, batch_sha256, verify_report_path, verify_report_sha256)
    slots = {(row["group"], row["frame"]): row for row in frames}
    need(set(slots) == {(row["group"], row["frame"]) for row in mappings},
         "SPRITES3X_DESCRIPTOR_FRAME_SET_DIFFERS_FROM_EXPAND_BATCH")
    mapping_rows = []
    for mapping in mappings:
        frame = slots[(mapping["group"], mapping["frame"])]
        mapping_rows.append({**mapping, "source": frame})
    snapshot, mismatches = current_snapshot(Path(sprites3x_root), identity, descriptor_ref, mappings, frames)
    return {
        "identity": identity,
        "sprites3x_root": str(Path(sprites3x_root).resolve(strict=True)),
        "descriptor": {**descriptor_ref, "basepath": descriptor.get("basepath"), "images": mappings},
        "descriptor_raw_sha256": hashlib.sha256(descriptor_raw).hexdigest(),
        "expand_batch": batch_ref,
        "expand_verify_report": report_ref,
        "native_inventory": inventory_ref,
        "frames": mapping_rows,
        "current_snapshot": snapshot,
        "current_mismatches": mismatches,
    }


def request_path(path: Path, expected_sha256: str) -> tuple[dict, dict]:
    doc, ref = read_pinned_json(path, expected_sha256, "SPRITES3X_REQUEST")
    need(doc.get("schema") == REQUEST_SCHEMA, "SPRITES3X_REQUEST_SCHEMA_UNSUPPORTED")
    return doc, ref


def validate_frozen_request(doc: dict, *, candidate_exists: bool) -> dict:
    fresh = collect_request_inputs(
        doc["identity"], Path(doc["sprites3x_root"]),
        Path(doc["expand_batch"]["path"]), doc["expand_batch"]["sha256"],
        Path(doc["expand_verify_report"]["path"]), doc["expand_verify_report"]["sha256"],
    )
    for key in ("identity", "sprites3x_root", "descriptor", "descriptor_raw_sha256", "expand_batch",
                "expand_verify_report", "native_inventory", "frames"):
        need(fresh[key] == doc[key], "SPRITES3X_FROZEN_INPUT_DRIFT: " + key)
    need(fresh["current_snapshot"] == doc["current_snapshot"],
         "SPRITES3X_CURRENT_MOD_CHANGED_AFTER_FREEZE")
    experiments_root = (PRODUCT / "build" / "experiments").resolve(strict=True)
    candidate = Path(doc["candidate_root"])
    if candidate_exists:
        candidate = resolve_inside(experiments_root, candidate, "SPRITES3X_CANDIDATE_ROOT")
    else:
        candidate = resolve_new_inside(experiments_root, candidate, "SPRITES3X_CANDIDATE_ROOT")
    need(candidate == Path(doc["candidate_root"]), "SPRITES3X_CANDIDATE_PATH_DRIFT")
    return fresh


def write_new(path: Path, raw: bytes) -> None:
    path = assert_plain(path, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        import os
        os.fsync(stream.fileno())


def freeze(args: argparse.Namespace) -> dict:
    operation_id = args.operation_id
    need(TOKEN.fullmatch(operation_id) is not None, "SPRITES3X_OPERATION_ID_INVALID")
    experiments_root = (PRODUCT / "build" / "experiments").resolve(strict=True)
    operation_root = resolve_new_inside(experiments_root, args.outdir, "SPRITES3X_OPERATION_ROOT")
    need(operation_root.name == operation_id, "SPRITES3X_OPERATION_ROOT_MUST_MATCH_OPERATION_ID")
    collected = collect_request_inputs(args.identity, args.sprites3x_root, args.batch, args.batch_sha256,
                                      args.verify_report, args.verify_report_sha256)
    doc = {
        "schema": REQUEST_SCHEMA,
        "operation_id": operation_id,
        **collected,
        "candidate_root": str(operation_root / "candidate"),
        "candidate_layout": "content/Sprites3x/<identity>.json plus content/Sprites3x/<identity>/<mapped pngs>",
        "write_policy": "new isolated candidate only; copy verified expansion PNG bytes unchanged; never edit installed MOD or formal assets",
        "runtime_accepted": False,
        "game_deployed": False,
    }
    operation_root.mkdir(parents=True, exist_ok=False)
    manifest = operation_root / "sprites3x-reprocess.json"
    write_new(manifest, json_bytes(doc))
    status = "FROZEN_REPROCESS_REQUIRED" if doc["current_mismatches"] else "FROZEN_CURRENT_MOD_MATCHES_SOURCE"
    return {"status": status, "manifest": str(manifest), "manifest_sha256": sha256(manifest),
            "identity": doc["identity"], "official_frames": len(doc["frames"]),
            "mismatch_frames": len(doc["current_mismatches"]),
            "mismatches": doc["current_mismatches"], "candidate_root": doc["candidate_root"]}


def check(args: argparse.Namespace) -> dict:
    doc, ref = request_path(args.manifest, args.manifest_sha256)
    need(doc.get("operation_id") == args.operation_id, "SPRITES3X_OPERATION_ID_DIFFERS_FROM_REQUEST")
    fresh = validate_frozen_request(doc, candidate_exists=Path(doc["candidate_root"]).exists())
    mismatches = fresh["current_mismatches"]
    status = "REPROCESS_REQUIRED" if mismatches else "CURRENT_MOD_MATCHES_VERIFIED_SOURCE"
    return {"status": status, "manifest": ref, "identity": doc["identity"],
            "official_frames": len(doc["frames"]), "mismatch_frames": len(mismatches),
            "mismatches": mismatches, "deploy_allowed": not mismatches,
            "next_action": "run to rebuild an isolated candidate from the verified expand outputs" if mismatches else "none"}


def candidate_files(doc: dict) -> tuple[list[dict], list[dict]]:
    seen = {}
    for row in doc["frames"]:
        filename = row["file"]
        source = row["source"]["expand_output"]
        previous = seen.get(filename)
        if previous is not None:
            need(previous["source"]["visible_pixel_sha256"] == source["visible_pixel_sha256"]
                 and previous["source"]["pixels"] == source["pixels"],
                 "SPRITES3X_DESCRIPTOR_ALIAS_SOURCES_DIFFER: " + filename)
            previous["slots"].append([row["group"], row["frame"]])
        else:
            seen[filename] = {"file": filename, "slots": [[row["group"], row["frame"]]], "source": source}
    return [{"path": f"content/Sprites3x/{doc['identity']}.json", "kind": "descriptor",
             "source": doc["descriptor"]["path"], "source_sha256": doc["descriptor"]["sha256"],
             "bytes": doc["descriptor"]["bytes"]}], [
             {"path": f"content/Sprites3x/{doc['identity']}/{row['file']}", "kind": "frame",
              "file": row["file"], "slots": row["slots"], "source": row["source"]["path"],
              "source_sha256": row["source"]["sha256"], "source_pixels": row["source"]["pixels"],
              "source_visible_pixel_sha256": row["source"]["visible_pixel_sha256"]}
             for row in sorted(seen.values(), key=lambda item: item["file"].casefold())]


def run(args: argparse.Namespace) -> dict:
    doc, manifest_ref = request_path(args.manifest, args.manifest_sha256)
    need(doc.get("operation_id") == args.operation_id, "SPRITES3X_OPERATION_ID_DIFFERS_FROM_REQUEST")
    fresh = validate_frozen_request(doc, candidate_exists=False)
    candidate_root = Path(doc["candidate_root"])
    candidate_root.mkdir(parents=True, exist_ok=False)
    descriptor_rows, frame_rows = candidate_files(doc)
    for row in descriptor_rows + frame_rows:
        source = Path(row["source"])
        need(sha256(source) == row["source_sha256"], "SPRITES3X_SOURCE_SHA_CHANGED_DURING_RUN: " + str(source))
        target = candidate_root / Path(*PurePosixPath(row["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_new(target, source.read_bytes())
        need(sha256(target) == row["source_sha256"], "SPRITES3X_CANDIDATE_READBACK_SHA_MISMATCH: " + str(target))
    mismatches = fresh["current_mismatches"]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation_id": doc["operation_id"],
        "identity": doc["identity"],
        "status": "REPROCESS_CANDIDATE_BUILT",
        "request": manifest_ref,
        "source_expand_batch": doc["expand_batch"],
        "source_expand_verify_report": doc["expand_verify_report"],
        "native_inventory": doc["native_inventory"],
        "descriptor": doc["descriptor"],
        "candidate_root": str(candidate_root.resolve(strict=True)),
        "official_frames": len(doc["frames"]),
        "reprocessed_frames": sorted({frame for row in mismatches for frame in row.get("slots", [])}),
        "mismatches_at_freeze": mismatches,
        "file_manifest": sorted(descriptor_rows + frame_rows, key=lambda item: item["path"].casefold()),
        "installed_mod_modified": False,
        "formal_assets_modified": False,
        "runtime_accepted": False,
        "game_deployed": False,
    }
    receipt_path = candidate_root / "sprites3x-reprocess-receipt.json"
    write_new(receipt_path, json_bytes(receipt))
    return {"status": receipt["status"], "identity": doc["identity"], "candidate_root": str(candidate_root),
            "receipt": str(receipt_path), "receipt_sha256": sha256(receipt_path),
            "official_frames": len(doc["frames"]), "reprocessed_frames": len(receipt["reprocessed_frames"]),
            "installed_mod_modified": False}


def verify(args: argparse.Namespace) -> dict:
    doc, manifest_ref = request_path(args.manifest, args.manifest_sha256)
    need(doc.get("operation_id") == args.operation_id, "SPRITES3X_OPERATION_ID_DIFFERS_FROM_REQUEST")
    validate_frozen_request(doc, candidate_exists=True)
    receipt, receipt_ref = read_pinned_json(args.receipt, args.receipt_sha256, "SPRITES3X_REPROCESS_RECEIPT")
    candidate_root = Path(doc["candidate_root"]).resolve(strict=True)
    need(receipt.get("schema") == RECEIPT_SCHEMA and receipt.get("operation_id") == doc["operation_id"]
         and receipt.get("request") == manifest_ref and receipt.get("candidate_root") == str(candidate_root),
         "SPRITES3X_REPROCESS_RECEIPT_DOES_NOT_BIND_TO_REQUEST")
    descriptor_rows, frame_rows = candidate_files(doc)
    expected = sorted(descriptor_rows + frame_rows, key=lambda item: item["path"].casefold())
    need(receipt.get("file_manifest") == expected, "SPRITES3X_RECEIPT_FILE_MANIFEST_DRIFT")
    expected_paths = {row["path"] for row in expected}
    candidate_sprites = candidate_root / "content" / "Sprites3x"
    actual_paths = set()
    for path in sorted(candidate_sprites.rglob("*"), key=lambda value: value.as_posix().casefold()):
        assert_plain(path)
        if path.is_dir():
            continue
        actual_paths.add(path.relative_to(candidate_root).as_posix())
    need(actual_paths == expected_paths,
         "SPRITES3X_CANDIDATE_FILE_SET_MISMATCH: "
         f"missing={sorted(expected_paths-actual_paths)}, extra={sorted(actual_paths-expected_paths)}")
    for row in expected:
        target = candidate_root / Path(*PurePosixPath(row["path"]).parts)
        source = Path(row["source"])
        need(sha256(target) == row["source_sha256"] and target.read_bytes() == source.read_bytes(),
             "SPRITES3X_CANDIDATE_BYTES_DIFFER_FROM_VERIFIED_SOURCE: " + row["path"])
        if row["kind"] == "frame":
            info = image_info(target)
            need(info["pixels"] == row["source_pixels"] and
                 info["visible_pixel_sha256"] == row["source_visible_pixel_sha256"],
                 "SPRITES3X_CANDIDATE_PIXEL_OR_GEOMETRY_DRIFT: " + row["path"])
    return {"status": "REPROCESS_CANDIDATE_VERIFIED", "identity": doc["identity"],
            "manifest": manifest_ref, "receipt": receipt_ref, "official_frames": len(doc["frames"]),
            "candidate_root": str(candidate_root), "installed_mod_modified": False,
            "game_deployed": False}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="action", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--identity", required=True)
    freeze_parser.add_argument("--sprites3x-root", type=Path, required=True)
    freeze_parser.add_argument("--batch", type=Path, required=True)
    freeze_parser.add_argument("--batch-sha256", required=True)
    freeze_parser.add_argument("--verify-report", type=Path, required=True)
    freeze_parser.add_argument("--verify-report-sha256", required=True)
    freeze_parser.add_argument("--operation-id", required=True)
    freeze_parser.add_argument("--outdir", type=Path, required=True)
    for name in ("check", "run", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--manifest", type=Path, required=True)
        cmd.add_argument("--manifest-sha256", required=True)
        cmd.add_argument("--operation-id", required=True)
        if name == "verify":
            cmd.add_argument("--receipt", type=Path, required=True)
            cmd.add_argument("--receipt-sha256", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "freeze":
            result = freeze(args)
        elif args.action == "check":
            result = check(args)
        elif args.action == "run":
            result = run(args)
        else:
            result = verify(args)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if args.action == "check" and result.get("status") == "REPROCESS_REQUIRED" else 0
    except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
