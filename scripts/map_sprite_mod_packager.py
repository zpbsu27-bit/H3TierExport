"""Build a read-only VCMI map-sprite MOD candidate from a verified full-canvas 3X batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

from PIL import Image

import workspace
from campaign_vcmi_mod import (
    assert_plain,
    is_mounted_hero_identity,
    load_native_def_authority,
    sha256,
    validate_full_canvas_sprite,
)

PRODUCT = Path(__file__).resolve().parents[1]
SCHEMA = "h3.map-sprite-mod-package.request.v1"
RECEIPT_SCHEMA = "h3.map-sprite-mod-package.receipt.v1"
IDENTITY = re.compile(r"^[A-Z0-9_-]{1,15}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
MOD_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FRAME = re.compile(r"^(0|[1-9][0-9]*)_(0|[1-9][0-9]*)\.png$")


class PackageError(ValueError):
    pass


def need(value: bool, message: str) -> None:
    if not value:
        raise PackageError(message)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def file_ref(path: Path) -> dict:
    path = Path(path)
    assert_plain(path)
    resolved = path.resolve(strict=True)
    need(resolved.is_file(), "INPUT_NOT_A_FILE: " + str(resolved))
    return {"path": str(resolved), "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}


def read_pinned_json(path: Path, expected_sha256: str, label: str) -> tuple[dict, dict]:
    need(isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256) is not None,
         label + "_SHA256_INVALID")
    ref = file_ref(path)
    need(ref["sha256"] == expected_sha256, label + "_SHA256_DRIFT")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(label + "_JSON_UNREADABLE: " + str(exc)) from exc
    need(isinstance(value, dict), label + "_JSON_NOT_AN_OBJECT")
    return value, ref


def resolve_inside(root: Path, path: Path, label: str) -> Path:
    root = Path(root).resolve(strict=True)
    assert_plain(path)
    resolved = Path(path).resolve(strict=True)
    need(resolved.is_relative_to(root), label + "_OUTSIDE_AUTHORIZED_ROOT: " + str(resolved))
    return resolved


def resolve_new_inside(root: Path, path: Path, label: str) -> Path:
    root = Path(root).resolve(strict=True)
    path = Path(path).absolute()
    assert_plain(path, must_exist=False)
    need(path.name not in ("", ".", ".."), label + "_NEW_PATH_NAME_INVALID")
    parent = path.parent.resolve(strict=True)
    resolved = parent / path.name
    need(resolved.is_relative_to(root), label + "_OUTSIDE_AUTHORIZED_ROOT: " + str(resolved))
    need(not resolved.exists(), label + "_ALREADY_EXISTS: " + str(resolved))
    return resolved


def frame_slot(path: Path, identity: str) -> tuple[int, int]:
    need(path.parent.name == identity, "MASTER_DIRECTORY_IDENTITY_MISMATCH: " + str(path))
    match = FRAME.fullmatch(path.name)
    need(match is not None, "MASTER_FILENAME_MUST_BE_CANONICAL_GROUP_FRAME: " + path.name)
    return int(match.group(1)), int(match.group(2))


def native_checkpoint(identity: str, native_rows: list[dict], asset_root: Path) -> tuple[dict, dict]:
    checkpoint_dir = asset_root / "codex-map" / "sources" / "complete" / "checkpoints"
    checkpoints = sorted(checkpoint_dir.glob(f"def_{identity}__*.json"))
    need(len(checkpoints) == 1, "OFFICIAL_IDENTITY_CHECKPOINT_NOT_UNIQUE: " + identity)
    checkpoint_path = checkpoints[0].resolve(strict=True)
    checkpoint_ref = file_ref(checkpoint_path)
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("OFFICIAL_IDENTITY_CHECKPOINT_UNREADABLE: " + str(exc)) from exc
    need(checkpoint.get("schema") == "h3.reference-library.identity-checkpoint.v1",
         "OFFICIAL_IDENTITY_CHECKPOINT_SCHEMA_UNSUPPORTED")
    image_index = checkpoint.get("images")
    need(isinstance(image_index, dict), "OFFICIAL_IDENTITY_CHECKPOINT_IMAGES_MISSING")
    checked = {}
    for native in native_rows:
        group, frame = native["group"], native["frame"]
        canvas, crop, origin = native.get("canvas"), native.get("crop"), native.get("anchor")
        need(native.get("native_lod") in ("H3sprite.lod", "H3ab_spr.lod"),
             f"OFFICIAL_DEF_LOD_REQUIRED: {identity} {group}_{frame}")
        key = f"def:{identity}:{group}:{frame}"
        image_record = image_index.get(key)
        expected_rel = f"sources/complete/frames/{identity}/{group}_{frame}.png"
        need(isinstance(image_record, dict) and
             str(image_record.get("path", "")).replace("\\", "/") == expected_rel,
             "OFFICIAL_FRAME_CHECKPOINT_PATH_DIFFERS: " + key)
        native_frame = (asset_root / "codex-map" / Path(expected_rel)).resolve(strict=True)
        native_ref = file_ref(native_frame)
        need(image_record.get("sha256") == native_ref["sha256"],
             "OFFICIAL_FRAME_CHECKPOINT_SHA_DIFFERS: " + key)
        need([image_record.get("width"), image_record.get("height")] == canvas,
             "OFFICIAL_FRAME_CHECKPOINT_GEOMETRY_DIFFERS: " + key)
        with Image.open(native_frame) as image:
            image.load()
            need(image.format == "PNG" and image.mode == "RGBA" and image.size == tuple(canvas),
                 "OFFICIAL_1X_FRAME_MUST_BE_CANVAS_RGBA_PNG: " + key)
            alpha_box = image.getchannel("A").getbbox()
            need(alpha_box is not None, "OFFICIAL_1X_FRAME_IS_BLANK: " + key)
        checked[(group, frame)] = {
            "native_frame": native_ref,
            "native_frame_alpha_bbox": list(alpha_box),
            "canvas_1x": canvas,
            "crop_1x": crop,
            "origin_1x": origin,
            "resource": native.get("resource"),
            "native_lod": native.get("native_lod"),
        }
    return checkpoint_ref, checked


def source_geometry(path: Path, native: dict, identity: str, group: int, frame: int,
                    expected_pixels: list[int]) -> dict:
    ref = file_ref(path)
    with Image.open(path) as image:
        image.load()
        need(image.format == "PNG" and image.mode == "RGBA",
             f"MAP_MASTER_MUST_BE_RGBA_PNG: {identity} {group}_{frame}")
        need(list(image.size) == expected_pixels,
             f"MAP_MASTER_PIXELS_DRIFT: {identity} {group}_{frame}")
        canvas = native["canvas"]
        need(image.width % canvas[0] == 0 and image.height % canvas[1] == 0,
             f"MAP_MASTER_NOT_ON_INTEGER_NATIVE_GRID: {identity} {group}_{frame}")
        kx, ky = image.width // canvas[0], image.height // canvas[1]
        need(kx == ky and kx >= 3, f"MAP_MASTER_REQUIRES_COMMON_INTEGER_K_AT_LEAST_3: {identity} {group}_{frame}")
        alpha_box = image.getchannel("A").getbbox()
        need(alpha_box is not None, f"MAP_MASTER_IS_BLANK: {identity} {group}_{frame}")
        origin, crop = native["anchor"], native["crop"]
        crop_rect = (origin[0] * kx, origin[1] * ky,
                     (origin[0] + crop[0]) * kx, (origin[1] + crop[1]) * ky)
        need(alpha_box[0] >= crop_rect[0] and alpha_box[1] >= crop_rect[1] and
             alpha_box[2] <= crop_rect[2] and alpha_box[3] <= crop_rect[3],
             f"MAP_MASTER_ALPHA_OUTSIDE_OFFICIAL_CROP: {identity} {group}_{frame}; "
             f"alpha={alpha_box}, crop_at_K={crop_rect}")
    return {"path": ref["path"], "bytes": ref["bytes"], "sha256": ref["sha256"],
            "pixels": expected_pixels, "pixels_per_native": kx, "alpha_bbox": list(alpha_box)}


def inspect_expanded(path: Path, native: dict, identity: str, group: int, frame: int) -> dict:
    ref = file_ref(path)
    geometry = validate_full_canvas_sprite(path, native, identity, group, frame)
    return {**ref, "pixels": geometry["expected_full_canvas_3x"],
            "alpha_bbox": geometry["source_alpha_bbox_3x"], "geometry": geometry}


def collect_request(*, identity: str, batch_path: Path, batch_sha256: str,
                    verify_report_path: Path, verify_report_sha256: str,
                    operation_id: str, candidate_root: Path, mod_id: str,
                    allow_candidate_exists: bool = False) -> dict:
    identity = identity.upper()
    need(IDENTITY.fullmatch(identity) is not None, "MAP_SPRITE_IDENTITY_INVALID")
    need(TOKEN.fullmatch(operation_id) is not None, "MAP_SPRITE_OPERATION_ID_INVALID")
    need(MOD_ID.fullmatch(mod_id) is not None, "MAP_SPRITE_MOD_ID_INVALID")
    candidate_root = Path(candidate_root).absolute()
    experiments_root = (PRODUCT / "build" / "experiments").resolve(strict=True)
    if candidate_root.exists():
        need(allow_candidate_exists, "MAP_SPRITE_CANDIDATE_ROOT_ALREADY_EXISTS")
        candidate_root = resolve_inside(experiments_root, candidate_root, "CANDIDATE_ROOT")
    else:
        candidate_root = resolve_new_inside(experiments_root, candidate_root, "CANDIDATE_ROOT")

    batch, batch_ref = read_pinned_json(batch_path, batch_sha256, "EXPAND_BATCH")
    report, verify_ref = read_pinned_json(verify_report_path, verify_report_sha256, "EXPAND_VERIFY_REPORT")
    need(batch.get("schema") == "h3.master-expand.batch.v1", "MAP_SPRITE_REQUIRES_STANDARD_EXPAND_BATCH")
    need(report.get("schema") == "h3.master-expand.run.v1" and report.get("status") == "COMPLETED"
         and report.get("failed") == 0 and report.get("outputs_written") == 0,
         "MAP_SPRITE_REQUIRES_SUCCESSFUL_EXPAND_VERIFY_REPORT")
    need(batch.get("algorithm") == "linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4",
         "MAP_SPRITE_EXPAND_QUALITY_ALGORITHM_MISMATCH")
    need(isinstance(batch.get("items"), list) and batch["items"], "EXPAND_BATCH_ITEMS_MISSING")
    need(batch.get("native_1x_authority") is not None, "EXPAND_BATCH_NATIVE_1X_AUTHORITY_MISSING")

    asset_root = workspace.asset_root().resolve(strict=True)
    inventory_ref, native_by_slot, native_by_identity = load_native_def_authority()
    native_rows = sorted(native_by_identity.get(identity, []), key=lambda row: (row["group"], row["frame"]))
    need(native_rows, "OFFICIAL_DEF_IDENTITY_NOT_FOUND: " + identity)
    need(len({(row["group"], row["frame"]) for row in native_rows}) == len(native_rows),
         "OFFICIAL_NATIVE_SLOTS_DUPLICATED")
    expected_slots = {(row["group"], row["frame"]) for row in native_rows}
    need(is_mounted_hero_identity(native_rows),
         "MAP_SPRITE_MOD_PACKAGER_CURRENTLY_REQUIRES_A_COMPLETE_45_SLOT_MOUNTED_HERO")
    checkpoint_ref, checkpoint_slots = native_checkpoint(identity, native_rows, asset_root)

    authority_doc, authority_ref = read_pinned_json(
        Path(batch["native_1x_authority"]["path"]), batch["native_1x_authority"]["sha256"],
        "EXPAND_NATIVE_1X_AUTHORITY")
    need(authority_doc.get("schema") == "h3.native-1x-authority.v1"
         and isinstance(authority_doc.get("frames"), dict),
         "EXPAND_NATIVE_1X_AUTHORITY_SCHEMA_INVALID")
    authority_slots = authority_doc["frames"].get(identity)
    need(isinstance(authority_slots, dict), "EXPAND_NATIVE_1X_AUTHORITY_IDENTITY_MISSING: " + identity)
    for native in native_rows:
        slot_name = f"{native['group']}_{native['frame']}"
        need(authority_slots.get(slot_name) == native["canvas"],
             "EXPAND_CANVAS_DIFFERS_FROM_OFFICIAL_INVENTORY: " + identity + " " + slot_name)

    output_root = Path(batch["output_root"]).resolve(strict=True)
    experiments_root = (PRODUCT / "build" / "experiments").resolve(strict=True)
    need(output_root.is_relative_to(experiments_root), "EXPAND_OUTPUT_ROOT_OUTSIDE_TOOL_EXPERIMENTS")
    verify_preflight = report.get("preflight", {})
    need(verify_preflight.get("blocked") == 0 and isinstance(verify_preflight.get("items"), list)
         and len(verify_preflight["items"]) == len(batch["items"]),
         "EXPAND_VERIFY_PREFLIGHT_INCOMPLETE")
    preflight_by_id = {}
    for row in verify_preflight["items"]:
        need(isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("id") not in preflight_by_id,
             "EXPAND_VERIFY_PREFLIGHT_DUPLICATE_ID")
        preflight_by_id[row["id"]] = row
        need(row.get("status") == "READY", "EXPAND_VERIFY_PREFLIGHT_NOT_READY: " + str(row.get("id")))

    result_by_id = {}
    for result in report.get("results", []):
        need(isinstance(result, dict) and isinstance(result.get("id"), str)
             and result["id"] not in result_by_id, "EXPAND_VERIFY_RESULT_DUPLICATE_ID")
        result_by_id[result["id"]] = result
    need(set(result_by_id) == {item.get("id") for item in batch["items"]},
         "EXPAND_VERIFY_RESULT_SET_DIFFERS_FROM_BATCH")

    batch_items = {}
    for item in batch["items"]:
        need(isinstance(item, dict) and isinstance(item.get("id"), str), "EXPAND_BATCH_ITEM_INVALID")
        need(item["id"] not in batch_items, "EXPAND_BATCH_ITEM_ID_DUPLICATE")
        batch_items[item["id"]] = item
    need(set(preflight_by_id) == set(batch_items), "EXPAND_VERIFY_PREFLIGHT_SET_DIFFERS_FROM_BATCH")

    slots = []
    seen_slots = set()
    for item in batch["items"]:
        need(item.get("target_scale") == 3 and item.get("size_authority") == "native-1x",
             "MAP_SPRITE_REQUIRES_NATIVE_AUTHORITY_3X_EXPORT: " + item["id"])
        source_path = Path(item["source"])
        group, frame = frame_slot(source_path, identity)
        slot = (group, frame)
        need(slot in expected_slots, "EXPAND_SOURCE_SLOT_NOT_IN_OFFICIAL_INVENTORY: " + repr(slot))
        need(slot not in seen_slots, "EXPAND_BATCH_HAS_DUPLICATE_NATIVE_SLOT: " + repr(slot))
        seen_slots.add(slot)
        native = native_by_slot.get((identity, group, frame))
        need(native is not None, "OFFICIAL_NATIVE_SLOT_MISSING: " + repr(slot))
        canvas = native["canvas"]
        need(item.get("base_pixels") == canvas and item.get("native_1x_pixels") == canvas,
             "EXPAND_BATCH_BASE_PIXELS_MUST_EQUAL_OFFICIAL_CANVAS: " + f"{identity} {group}_{frame}")
        source_path = resolve_inside(source_path.parent, source_path, "MAP_MASTER")
        master = source_geometry(source_path, native, identity, group, frame, item.get("source_pixels"))
        need(master["sha256"] == item.get("source_sha256"), "EXPAND_MASTER_SHA_DIFFERS_FROM_BATCH")

        result = result_by_id[item["id"]]
        need(result.get("status") == "VERIFIED", "EXPAND_RESULT_NOT_VERIFIED: " + item["id"])
        receipt_path = Path(result.get("receipt", ""))
        receipt_root = output_root / "receipts"
        receipt_path = resolve_inside(receipt_root, receipt_path, "EXPAND_RECEIPT")
        receipt_ref = file_ref(receipt_path)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PackageError("EXPAND_RECEIPT_UNREADABLE: " + str(exc)) from exc
        need(receipt.get("schema") == "h3.master-expand.receipt.v1"
             and receipt.get("contract", {}).get("entry") == item
             and receipt.get("contract", {}).get("output_root") == batch["output_root"],
             "EXPAND_RECEIPT_DOES_NOT_BIND_TO_BATCH_ITEM: " + item["id"])
        need(receipt.get("target_scale") == 3 and receipt.get("source_sha256") == master["sha256"],
             "EXPAND_RECEIPT_SOURCE_OR_SCALE_MISMATCH: " + item["id"])

        output_path = resolve_inside(output_root, Path(item["output"]), "EXPAND_OUTPUT")
        need(output_path.name == f"{group}_{frame}.png", "EXPAND_OUTPUT_NAME_DIFFERS_FROM_NATIVE_SLOT")
        expected_size = [canvas[0] * 3, canvas[1] * 3]
        need(receipt.get("output_pixels") == expected_size and
             receipt.get("official_output_pixels") == expected_size,
             "EXPAND_RECEIPT_NOT_OFFICIAL_FULL_CANVAS_3X: " + item["id"])
        expanded = inspect_expanded(output_path, native, identity, group, frame)
        need(expanded["sha256"] == receipt.get("png_sha256") and
             expanded["pixels"] == expected_size and
             result_by_id[item["id"]].get("status") == "VERIFIED",
             "EXPAND_OUTPUT_DOES_NOT_MATCH_VERIFIED_RECEIPT: " + item["id"])
        resource = str(native.get("resource") or "")
        resource_stem = Path(resource).stem
        need(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", resource_stem) is not None,
             "OFFICIAL_RESOURCE_NAME_INVALID: " + resource)
        slots.append({
            "group": group, "frame": frame, "key": ["def", identity, group, frame],
            "resource": resource, "vcmi_filename": f"{resource_stem.lower()}_g{group}_f{frame}.png",
            "native_geometry": {
                "canvas_1x": native["canvas"], "crop_1x": native["crop"], "origin_1x": native["anchor"],
                "canvas_3x": expected_size,
                "crop_rect_3x": [
                    native["anchor"][0] * 3, native["anchor"][1] * 3,
                    (native["anchor"][0] + native["crop"][0]) * 3,
                    (native["anchor"][1] + native["crop"][1]) * 3,
                ],
            },
            "official_frame": checkpoint_slots[slot]["native_frame"],
            "master": master,
            "expanded": expanded,
            "expand_receipt": receipt_ref,
        })
    need(seen_slots == expected_slots,
         "MAP_SPRITE_BATCH_MUST_CONTAIN_EVERY_OFFICIAL_SLOT: "
         f"missing={sorted(expected_slots-seen_slots)}, extra={sorted(seen_slots-expected_slots)}")
    slots.sort(key=lambda row: (row["group"], row["frame"]))
    return {
        "schema": SCHEMA,
        "operation_id": operation_id,
        "identity": identity,
        "mod_id": mod_id,
        "candidate_root": str(candidate_root),
        "expand_batch": batch_ref,
        "expand_verify_report": verify_ref,
        "native_1x_authority": authority_ref,
        "native_inventory": inventory_ref,
        "identity_checkpoint": checkpoint_ref,
        "native_slots": len(native_rows),
        "slots": slots,
        "write_policy": "new-isolated-candidate-only; source PNG bytes copied exactly; no crop, pad, center, resize, install, or overwrite",
        "runtime_accepted": False,
        "game_deployed": False,
    }


def load_request(path: Path, expected_sha256: str) -> tuple[dict, dict]:
    doc, ref = read_pinned_json(path, expected_sha256, "MAP_SPRITE_REQUEST")
    need(doc.get("schema") == SCHEMA, "MAP_SPRITE_REQUEST_SCHEMA_UNSUPPORTED")
    return doc, ref


def current_request(doc: dict) -> dict:
    expand = doc["expand_batch"]
    report = doc["expand_verify_report"]
    return collect_request(
        identity=doc["identity"], batch_path=Path(expand["path"]), batch_sha256=expand["sha256"],
        verify_report_path=Path(report["path"]), verify_report_sha256=report["sha256"],
        operation_id=doc["operation_id"], candidate_root=Path(doc["candidate_root"]), mod_id=doc["mod_id"],
        allow_candidate_exists=Path(doc["candidate_root"]).exists(),
    )


def verify_request_current(doc: dict) -> None:
    actual = current_request(doc)
    need(actual == doc, "MAP_SPRITE_REQUEST_INPUT_OR_GEOMETRY_DRIFT")


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def package_descriptor(identity: str, slots: list[dict]) -> dict:
    return {
        "basepath": identity + "/",
        "images": [{"group": row["group"], "frame": row["frame"], "file": row["vcmi_filename"]}
                   for row in slots],
    }


def package_mod_json(identity: str, mod_id: str) -> dict:
    return {
        "name": f"H3HD Map Sprite {identity}",
        "description": f"Full-canvas 3X map sprite package for {identity}; geometry is bound to the 1999 native DEF inventory.",
        "version": "1.0",
        "author": "H3HD",
        "contact": "",
        "modType": "Graphical",
    }


def add_file(root: Path, relative: str, raw: bytes, entries: list[dict]) -> None:
    rel = PurePosixPath(relative)
    need(not rel.is_absolute() and ".." not in rel.parts and rel.parts,
         "UNSAFE_VCMI_MOD_RELATIVE_PATH")
    target = root.joinpath(*rel.parts)
    need(not target.exists(), "MAP_SPRITE_PACKAGE_DUPLICATE_OUTPUT: " + str(target))
    write_new(target, raw)
    entries.append({"path": rel.as_posix(), "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()})


def build_candidate(doc: dict, request_ref: dict) -> dict:
    verify_request_current(doc)
    candidate_root = Path(doc["candidate_root"])
    need(not candidate_root.exists(), "MAP_SPRITE_CANDIDATE_ROOT_ALREADY_EXISTS")
    candidate_root.mkdir(parents=True, exist_ok=False)
    mod_root = candidate_root / doc["mod_id"]
    mod_root.mkdir()
    entries = []
    mod_raw = json_bytes(package_mod_json(doc["identity"], doc["mod_id"]))
    add_file(mod_root, "mod.json", mod_raw, entries)
    descriptor = package_descriptor(doc["identity"], doc["slots"])
    descriptor_rel = f"content/Sprites3x/{doc['identity']}.json"
    add_file(mod_root, descriptor_rel, json_bytes(descriptor), entries)
    for row in doc["slots"]:
        source = Path(row["expanded"]["path"])
        assert_plain(source)
        raw = source.read_bytes()
        need(hashlib.sha256(raw).hexdigest() == row["expanded"]["sha256"],
             "EXPANDED_PNG_SHA_CHANGED_DURING_PACKAGE: " + str(source))
        with Image.open(source) as image:
            image.load()
            need(list(image.size) == row["native_geometry"]["canvas_3x"],
                 "EXPANDED_PNG_DIMENSIONS_CHANGED_DURING_PACKAGE: " + str(source))
        add_file(mod_root, f"content/Sprites3x/{doc['identity']}/{row['vcmi_filename']}", raw, entries)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation_id": doc["operation_id"],
        "status": "MAP_SPRITE_MOD_CANDIDATE_BUILT_READBACK_VERIFIED",
        "identity": doc["identity"],
        "mod_id": doc["mod_id"],
        "mod_root": str(mod_root),
        "request": request_ref,
        "expand_batch": doc["expand_batch"],
        "expand_verify_report": doc["expand_verify_report"],
        "native_1x_authority": doc["native_1x_authority"],
        "native_inventory": doc["native_inventory"],
        "identity_checkpoint": doc["identity_checkpoint"],
        "native_geometry_contract": "official-full-canvas-3x-alpha-within-origin-crop",
        "slot_count": len(doc["slots"]),
        "file_manifest": entries,
        "runtime_accepted": False,
        "game_deployed": False,
    }
    receipt_path = candidate_root / "map-sprite-mod-build-receipt.json"
    write_new(receipt_path, json_bytes(receipt))
    audit_candidate(doc, receipt)
    return receipt


def directory_inventory(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if path.is_dir():
            assert_plain(path)
            continue
        assert_plain(path)
        need(path.is_file(), "MAP_SPRITE_PACKAGE_NON_FILE")
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                     "sha256": sha256(path)})
    return rows


def audit_candidate(doc: dict, receipt: dict) -> dict:
    root = Path(receipt["mod_root"]).resolve(strict=True)
    candidate_root = Path(doc["candidate_root"]).resolve(strict=True)
    need(root.is_relative_to(candidate_root) and root.name == doc["mod_id"],
         "MAP_SPRITE_MOD_ROOT_OUTSIDE_CANDIDATE")
    actual = directory_inventory(root)
    expected = sorted(receipt["file_manifest"], key=lambda row: row["path"].casefold())
    need(actual == expected, "MAP_SPRITE_PACKAGE_FILE_SET_OR_SHA_MISMATCH")
    descriptor_path = root / "content" / "Sprites3x" / f"{doc['identity']}.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    need(descriptor == package_descriptor(doc["identity"], doc["slots"]),
         "MAP_SPRITE_DESCRIPTOR_DOES_NOT_MATCH_NATIVE_SLOTS")
    for row in doc["slots"]:
        png_path = root / "content" / "Sprites3x" / doc["identity"] / row["vcmi_filename"]
        native = {
            "canvas": row["native_geometry"]["canvas_1x"],
            "crop": row["native_geometry"]["crop_1x"],
            "anchor": row["native_geometry"]["origin_1x"],
            "native_lod": "H3sprite.lod",
            "resource": row["resource"],
        }
        result = validate_full_canvas_sprite(png_path, native, doc["identity"], row["group"], row["frame"])
        need(result["expected_full_canvas_3x"] == row["native_geometry"]["canvas_3x"] and
             result["official_crop_rect_3x"] == row["native_geometry"]["crop_rect_3x"],
             "MAP_SPRITE_OUTPUT_GEOMETRY_DRIFT")
        source = Path(row["expanded"]["path"])
        need(png_path.read_bytes() == source.read_bytes(),
             "MAP_SPRITE_PACKAGE_MODIFIED_EXPANDED_SOURCE_BYTES")
    return {"status": "VERIFIED", "files": len(actual), "slots": len(doc["slots"]),
            "mod_root": str(root), "file_manifest_sha256": hashlib.sha256(json_bytes(actual)).hexdigest()}


def freeze(args: argparse.Namespace) -> dict:
    doc = collect_request(
        identity=args.identity, batch_path=args.batch, batch_sha256=args.batch_sha256,
        verify_report_path=args.verify_report, verify_report_sha256=args.verify_report_sha256,
        operation_id=args.operation_id, candidate_root=args.candidate_root, mod_id=args.mod_id,
    )
    target = Path(args.out).absolute()
    assert_plain(target, must_exist=False)
    need(not target.exists(), "MAP_SPRITE_REQUEST_OUTPUT_ALREADY_EXISTS")
    write_new(target, json_bytes(doc))
    return {"status": "FROZEN", "manifest": str(target), "manifest_sha256": sha256(target),
            "identity": doc["identity"], "slot_count": len(doc["slots"]),
            "candidate_root": doc["candidate_root"]}


def run_request(manifest: Path, manifest_sha256: str, operation_id: str) -> dict:
    doc, request_ref = load_request(manifest, manifest_sha256)
    need(doc.get("operation_id") == operation_id, "MAP_SPRITE_OPERATION_ID_MISMATCH")
    return build_candidate(doc, request_ref)


def verify_request(manifest: Path, manifest_sha256: str, operation_id: str,
                   receipt_path: Path, receipt_sha256: str) -> dict:
    doc, _ = load_request(manifest, manifest_sha256)
    need(doc.get("operation_id") == operation_id, "MAP_SPRITE_OPERATION_ID_MISMATCH")
    verify_request_current(doc)
    receipt, receipt_ref = read_pinned_json(receipt_path, receipt_sha256, "MAP_SPRITE_BUILD_RECEIPT")
    need(receipt.get("schema") == RECEIPT_SCHEMA
         and receipt.get("status") == "MAP_SPRITE_MOD_CANDIDATE_BUILT_READBACK_VERIFIED"
         and receipt.get("operation_id") == operation_id
         and receipt.get("identity") == doc["identity"]
         and receipt.get("request", {}).get("sha256") == manifest_sha256,
         "MAP_SPRITE_BUILD_RECEIPT_IDENTITY_MISMATCH")
    audit = audit_candidate(doc, receipt)
    return {"status": audit["status"], "receipt": receipt_ref, "audit": audit,
            "runtime_accepted": False, "game_deployed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--identity", required=True)
    freeze_parser.add_argument("--batch", type=Path, required=True)
    freeze_parser.add_argument("--batch-sha256", required=True)
    freeze_parser.add_argument("--verify-report", type=Path, required=True)
    freeze_parser.add_argument("--verify-report-sha256", required=True)
    freeze_parser.add_argument("--operation-id", required=True)
    freeze_parser.add_argument("--candidate-root", type=Path, required=True)
    freeze_parser.add_argument("--mod-id", required=True)
    freeze_parser.add_argument("--out", type=Path, required=True)
    for command in ("check", "run"):
        current = sub.add_parser(command)
        current.add_argument("--manifest", type=Path, required=True)
        current.add_argument("--manifest-sha256", required=True)
        current.add_argument("--operation-id", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--manifest-sha256", required=True)
    verify_parser.add_argument("--operation-id", required=True)
    verify_parser.add_argument("--receipt", type=Path, required=True)
    verify_parser.add_argument("--receipt-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze(args)
        elif args.command == "check":
            doc, _ = load_request(args.manifest, args.manifest_sha256)
            need(doc.get("operation_id") == args.operation_id, "MAP_SPRITE_OPERATION_ID_MISMATCH")
            verify_request_current(doc)
            result = {"status": "READY", "identity": doc["identity"], "slot_count": len(doc["slots"]),
                      "candidate_root": doc["candidate_root"], "outputs_written": 0}
        elif args.command == "run":
            result = run_request(args.manifest, args.manifest_sha256, args.operation_id)
        else:
            result = verify_request(args.manifest, args.manifest_sha256, args.operation_id,
                                    args.receipt, args.receipt_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PackageError, ValueError, OSError, KeyError, TypeError) as error:
        print("MAP_SPRITE_MOD_PACKAGE_REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
