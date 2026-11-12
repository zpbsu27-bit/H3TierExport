"""Source-bound Town batch PROCESS/REVIEW gate over the existing native inventory.

This is a receipt-only front gate. It never creates art or process frames. A
future eligible frame must still be produced by the existing motion/native
quality pipeline with an independently verified source, mapping and timing.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
from collections import Counter
from io import BytesIO
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageChops

import creature_action_plan
from native_alias import (
    SHA, TOKEN, current_receipt_build, digest, document, exact_fields,
    native_key, ref_path, require
)
from review_native_batch import (
    OutputRootGuard, _lexists, _path_key, absolute_file, read_bounded
)

PRODUCT = resolve_tool_root(__file__)
OUTPUT_BASE = PRODUCT / "build" / "experiments" / "town-process-review"
MANIFEST_SCHEMA = "h3.town.process-review-intake.v1"
MANIFEST_SCHEMA_V2 = "h3.town.process-review-intake.v2"
REPORT_SCHEMA = "h3.town.process-review.v1"
COMPLETE_SCHEMA = "h3.town.process-review.completion.v1"
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
CANDIDATE_FIELDS = {
    "key", "source", "source_role", "request", "raw_receipt", "phase_guide",
    "source_view", "native_mapping", "static_registration",
    "scene_context", "timing", "native_review",
}
V2_CANDIDATE_FIELDS = CANDIDATE_FIELDS | {"source_binding"}
DEPENDENCIES = ("request", "raw_receipt", "phase_guide", "source_view",
                "static_registration", "scene_context", "timing", "native_review")
FEEDBACK_SCHEMA = "h3.art-town.tool-r9-process-review-handoff.v1"
STANDARD_SCHEMA = "h3.art-town.modern-hd-direction-addendum.v1"
SOURCE_BINDING_SCHEMA = "h3.town.source-binding.v1"
STATIC_REGISTRATION_SCHEMA = "h3.town.static-registration.v1"
DERIVED_PREPARATION_SCHEMA = "h3.town.derived-preparation.v1"
DERIVED_MAPPING_SCHEMA = "h3.town.derived-pixel-mapping.v1"
DERIVED_PARENT_CHAIN_SCHEMA = "h3.town.source-parent-chain.v1"
MODERN_INTAKE_SCHEMA = "h3.art-town.modern-hd-six-phase-intake-freeze.v1"
OWNER_FEEDBACK_SCHEMA = "h3.art-town.batch-tool-action-feedback.v2"


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def binding(value: dict, label: str, max_size: int = 16 * 1024 * 1024) -> tuple[Path, bytes, dict]:
    require(isinstance(value, dict), "REFERENCE_OBJECT_REQUIRED:" + label)
    path, raw = ref_path({"path": value.get("path"), "sha256": value.get("sha256")}, label, max_size)
    if "bytes" in value:
        require(type(value["bytes"]) is int and value["bytes"] == len(raw),
                "REFERENCE_BYTES_DRIFT:" + label)
    if path.suffix.lower() == ".png" and any(n in value for n in ("pixels", "format", "mode")):
        with Image.open(BytesIO(raw)) as image:
            for name, actual in (("pixels", list(image.size)), ("format", image.format), ("mode", image.mode)):
                require(name not in value or value[name] == actual,
                        "REFERENCE_PNG_METADATA_DRIFT:" + label + ":" + name)
    return path, raw, {"path": str(path), "sha256": digest(raw)}


def _same_ref(left: dict, right: dict) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict) and
            isinstance(left.get("path"), str) and isinstance(right.get("path"), str) and
            _path_key(Path(left["path"])) == _path_key(Path(right["path"])) and
            left.get("sha256") == right.get("sha256"))


def _key_tuple(value: object, label: str) -> tuple:
    return tuple(native_key(value, label))


def _geometry_ref(geometry: dict) -> dict:
    return {"canvas_1x": geometry["canvas_1x"],
            "crop_1x": geometry["crop_1x"],
            "origin_1x": geometry["origin_1x"]}


def _validate_owner_feedback_intake(value: dict, standard_ref: dict) -> tuple[dict, dict, dict]:
    path, raw, pinned = binding(value, "owner Town source feedback", MAX_REPORT_BYTES)
    feedback = document(raw, "owner Town source feedback")
    require(feedback.get("schema") == OWNER_FEEDBACK_SCHEMA and
            feedback.get("status") == "REVIEW_REQUIRED" and
            feedback.get("selection") == "UNSELECTED" and
            feedback.get("process_frame_or_export_completed") is False,
            "OWNER_SOURCE_FEEDBACK_AUTHORITY_INVALID")
    require(_same_ref(feedback.get("standard"), standard_ref),
            "OWNER_SOURCE_FEEDBACK_STANDARD_DRIFT")
    source = feedback.get("source")
    request = feedback.get("request")
    raw_receipt = feedback.get("raw_receipt")
    guide = feedback.get("guide")
    readback = feedback.get("native_review_readback")
    require(all(isinstance(v, dict) for v in (source, request, raw_receipt, guide, readback)),
            "OWNER_SOURCE_FEEDBACK_BINDINGS_MISSING")
    source_path, source_raw, source_ref = binding(source, "owner source master", MAX_PNG_BYTES)
    require(source_path.suffix.lower() == ".png", "OWNER_SOURCE_MASTER_PNG_REQUIRED")
    _image_metadata(source_path, source_raw)
    request_path, request_raw, request_ref = binding(request, "owner source request")
    request_doc = document(request_raw, "owner source request")
    key = _key_tuple(request_doc.get("key"), "owner source request key")
    raw_path, raw_raw, raw_ref = binding(raw_receipt, "owner source raw receipt")
    receipt = document(raw_raw, "owner source raw receipt")
    require(receipt.get("schema") == "h3.art-town.raw-intake.v1" and
            _same_ref(receipt.get("request"), request_ref) and
            receipt.get("raw", {}).get("sha256") == source_ref["sha256"] and
            receipt.get("master", {}).get("sha256") == source_ref["sha256"] and
            receipt.get("preserved_raw_bytes") is True,
            "OWNER_SOURCE_RAW_BINDING_INVALID")
    _validate_image_refs(receipt, source_ref, "OWNER_SOURCE_RAW")
    guide_path, guide_raw, guide_ref = binding(guide, "owner source guide", MAX_PNG_BYTES)
    require(guide_path.suffix.lower() == ".png", "OWNER_SOURCE_GUIDE_PNG_REQUIRED")
    readback_path, readback_raw, readback_ref = binding(readback, "owner native review readback")
    readback_doc = document(readback_raw, "owner native review readback")
    require(readback_doc.get("schema") == "h3.art-town.modern-hd-master-native-review-readback.v1" and
            readback_doc.get("status") == "REVIEW_REQUIRED" and
            _key_tuple(readback_doc.get("key"), "owner native review key") == key and
            readback_doc.get("source", {}).get("master", {}).get("sha256") == source_ref["sha256"],
            "OWNER_NATIVE_REVIEW_BINDING_INVALID")
    item = {"key": list(key), "request": request_ref, "raw_receipt": raw_ref,
            "master": source_ref, "guide_sha256": guide_ref["sha256"],
            "request_document": request_doc, "receipt_document": receipt,
            "native_review": readback_ref}
    intake = {"schema": OWNER_FEEDBACK_SCHEMA, "identity": key[1], "items": [item]}
    return intake, pinned | {"static_master": source_ref}, {key: item}


def _validate_modern_intake(value: dict, standard_ref: dict) -> tuple[dict, dict, dict]:
    path, raw, pinned = binding(value, "modern source intake", MAX_REPORT_BYTES)
    intake = document(raw, "modern source intake")
    if intake.get("schema") == OWNER_FEEDBACK_SCHEMA:
        return _validate_owner_feedback_intake(value, standard_ref)
    require(intake.get("schema") == MODERN_INTAKE_SCHEMA,
            "MODERN_SOURCE_INTAKE_SCHEMA_INVALID")
    require(intake.get("selection_status") == "ALL_UNSELECTED" and
            intake.get("process_status") == "NOT_EXECUTED" and
            intake.get("game_status") == "NOT_DEPLOYED",
            "MODERN_SOURCE_INTAKE_AUTHORITY_INVALID")
    require(_same_ref(intake.get("modern_hd_standard"), standard_ref),
            "MODERN_SOURCE_INTAKE_STANDARD_DRIFT")
    master = intake.get("source_master_f0")
    require(isinstance(master, dict), "MODERN_SOURCE_INTAKE_STATIC_MASTER_MISSING")
    master_path, master_raw, master_ref = binding(master, "modern F0 static master", MAX_PNG_BYTES)
    require(master_path.suffix.lower() == ".png", "MODERN_STATIC_MASTER_PNG_REQUIRED")
    _image_metadata(master_path, master_raw)
    items = intake.get("items")
    require(isinstance(items, list) and 0 < len(items) <= 64,
            "MODERN_SOURCE_INTAKE_ITEMS_INVALID")
    by_key = {}
    for item in items:
        require(isinstance(item, dict), "MODERN_SOURCE_INTAKE_ITEM_INVALID")
        key = _key_tuple(item.get("key"), "modern source intake item")
        require(key not in by_key, "MODERN_SOURCE_INTAKE_KEY_DUPLICATE")
        for name in ("request", "raw_receipt", "master"):
            require(isinstance(item.get(name), dict),
                    "MODERN_SOURCE_INTAKE_" + name.upper() + "_MISSING")
        require(isinstance(item.get("guide_sha256"), str) and
                SHA.fullmatch(item["guide_sha256"]) is not None,
                "MODERN_SOURCE_INTAKE_GUIDE_SHA_INVALID")
        request_path, request_raw, request_ref = binding(item["request"], "modern intake request")
        request = document(request_raw, "modern intake request")
        require(request.get("schema") == "h3.art-town.model-request.v1" and
                _key_tuple(request.get("key"), "modern intake request key") == key,
                "MODERN_SOURCE_INTAKE_REQUEST_KEY_MISMATCH")
        raw_path, raw_raw, raw_ref = binding(item["raw_receipt"], "modern intake raw receipt")
        receipt = document(raw_raw, "modern intake raw receipt")
        require(receipt.get("schema") == "h3.art-town.raw-intake.v1" and
                _same_ref(receipt.get("request"), request_ref),
                "MODERN_SOURCE_INTAKE_RAW_RECEIPT_MISMATCH")
        master_path_i, master_raw_i, master_ref_i = binding(item["master"], "modern intake master", MAX_PNG_BYTES)
        require(master_path_i.suffix.lower() == ".png", "MODERN_SOURCE_INTAKE_MASTER_PNG_REQUIRED")
        _image_metadata(master_path_i, master_raw_i)
        require(receipt.get("raw", {}).get("sha256") == master_ref_i["sha256"] and
                receipt.get("master", {}).get("sha256") == master_ref_i["sha256"] and
                receipt.get("preserved_raw_bytes") is True,
                "MODERN_SOURCE_INTAKE_MASTER_BINDING_INVALID")
        _validate_image_refs(receipt, master_ref_i, "MODERN_SOURCE_INTAKE")
        require(item.get("master_sha256") == master_ref_i["sha256"],
                "MODERN_SOURCE_INTAKE_MASTER_SHA_MISMATCH")
        by_key[key] = {"key": list(key), "request": request_ref,
                       "raw_receipt": raw_ref, "master": master_ref_i,
                       "guide_sha256": item["guide_sha256"],
                       "request_document": request,
                       "receipt_document": receipt}
    return intake, pinned | {"static_master": master_ref}, by_key


def load_inputs(manifest_path: Path, manifest_sha: str) -> tuple[dict, dict, dict, dict, str, list[dict], dict]:
    _, manifest_raw, _ = binding({"path": str(manifest_path), "sha256": manifest_sha},
                                  "manifest", MAX_REPORT_BYTES)
    manifest = document(manifest_raw, "manifest")
    schema = manifest.get("schema")
    expected_fields = {"schema", "operation_id", "identity", "official_inventory",
                       "town_feedback", "art_standard", "candidates"}
    if schema == MANIFEST_SCHEMA_V2:
        expected_fields.add("source_intake")
    exact_fields(manifest, expected_fields, "manifest")
    require(schema in (MANIFEST_SCHEMA, MANIFEST_SCHEMA_V2), "MANIFEST_SCHEMA_INVALID")
    strict = schema == MANIFEST_SCHEMA_V2
    require(type(manifest["operation_id"]) is str and TOKEN.fullmatch(manifest["operation_id"]) is not None,
            "OPERATION_ID_INVALID")
    identity = manifest["identity"]
    require(type(identity) is str and re.fullmatch("[A-Z0-9_]{1,20}", identity) is not None,
            "IDENTITY_INVALID")

    inventory_path, _, inv_ref = binding(manifest["official_inventory"], "official inventory", 128 * 1024 * 1024)
    catalog, actual_sha = creature_action_plan.load_catalog(inventory_path, inv_ref["sha256"])
    feedback_path, feedback_raw, feedback_ref = binding(manifest["town_feedback"], "frozen Town feedback")
    feedback = document(feedback_raw, "Town feedback")
    legacy_feedback = feedback.get("schema") == FEEDBACK_SCHEMA
    if legacy_feedback:
        require(feedback.get("status") == "TOWN_PROCESS_REVIEW_OPEN_TOOL_OWNER_ACTION_REQUIRED",
                "TOWN_FEEDBACK_NOT_CURRENT")
    else:
        require(strict and feedback.get("schema") == OWNER_FEEDBACK_SCHEMA,
                "TOWN_FEEDBACK_NOT_CURRENT")
    standard_path, standard_raw, standard_ref = binding(manifest["art_standard"], "modern-HD art standard")
    standard = document(standard_raw, "modern-HD art standard")
    require(standard.get("schema") == STANDARD_SCHEMA
            and standard.get("authority") == "EXPLICIT_CURRENT_USER_ART_DIRECTION"
            and standard.get("status") == "CURRENT_USER_STANDARD_FOR_ALL_FUTURE_TOWN_CREATION",
            "MODERN_HD_ART_STANDARD_NOT_CURRENT")
    _, _, canonical_ref = binding(standard.get("parent_canonical"), "current Town canonical parent")
    require(isinstance(standard.get("quality_bar"), dict) and
            isinstance(standard["quality_bar"].get("process_frames"), str) and
            bool(standard["quality_bar"]["process_frames"]), "MODERN_HD_STANDARD_INVALID")

    source_intake = None
    source_intake_ref = None
    intake_items = {}
    static_domains = None
    if strict and manifest.get("source_intake") is not None:
        source_intake, source_intake_ref, intake_items = _validate_modern_intake(
            manifest["source_intake"], standard_ref)
        if source_intake.get("static_dynamic_domains") is not None:
            static_domains = _validate_static_domains(source_intake["static_dynamic_domains"],
                                                      source_intake_ref["static_master"], identity, standard_ref)

    keys = []
    missing_frames = []
    if legacy_feedback:
        town = feedback.get("town_inputs")
        require(type(town) is dict, "TOWN_FEEDBACK_FIELDS_MISSING")
        a03 = town.get("a03_keyframe_candidates", {})
        a04 = town.get("a04_color_candidate", {})
        remaining = town.get("remaining_tbcsext2_slots", {})
        require(a03.get("status") == "UNSELECTED_KEYFRAME_CANDIDATE_EDGE_CONTACT_REVIEW_REQUIRED"
                and a04.get("eligible_as_motion_keyframe") is False
                and a04.get("controlled_color_only") is False
                and remaining.get("status") == "SOURCE_AND_PHASE_INVENTORY_PENDING",
                "TOWN_FEEDBACK_AUTHORITY_CONTRADICTION")
        keyframes = a03.get("keys")
        require(type(keyframes) is list and len(keyframes) > 0, "FROZEN_KEYFRAME_KEYS_MISSING")
        keys = [tuple(native_key(value, "Town a03")) for value in keyframes]
        require(len(keys) == len(set(keys)) and all(key[1] == identity for key in keys),
                "FROZEN_KEYFRAME_KEYS_INVALID")
        missing_frames = remaining.get("frames")
        require(type(missing_frames) is list and len(missing_frames) == len(set(missing_frames))
                and all(type(n) is int and n >= 0 for n in missing_frames),
                "FROZEN_PROCESS_SLOTS_INVALID")
        require(set(missing_frames).isdisjoint({key[3] for key in keys if key[2] == 0}),
                "FROZEN_SLOT_DEPENDENCY_COLLISION")
    else:
        require(intake_items and all(key[1] == identity for key in intake_items),
                "OWNER_SOURCE_IDENTITY_MISMATCH")

    original = {}
    for entry in catalog["rows"]:
        if entry.get("kind") != "def" or entry.get("identity") != identity:
            continue
        require(entry.get("category") == "towns_buildings" and
                entry.get("native_lod") in ("H3sprite.lod", "H3ab_spr.lod"),
                "NATIVE_TOWN_DOMAIN_MISMATCH")
        key = ("def", identity, entry.get("group"), entry.get("frame"))
        require(type(key[2]) is int and type(key[3]) is int and key[2] >= 0 and key[3] >= 0,
                "NATIVE_KEY_INVALID")
        require(key not in original, "NATIVE_KEY_DUPLICATE")
        canvas, crop, anchor = creature_action_plan.validate_geometry(entry, (identity, key[2], key[3]))
        original[key] = {"canvas_1x": canvas, "crop_1x": crop, "origin_1x": anchor,
                         "target_canvas_3x": [3 * n for n in canvas],
                         "target_crop_3x": [3 * n for n in crop],
                         "target_origin_3x": [3 * n for n in anchor]}
    require(original, "NATIVE_TOWN_IDENTITY_NOT_IN_INVENTORY")
    require(all(key in original for key in intake_items), "SOURCE_INTAKE_KEY_NOT_IN_NATIVE_INVENTORY")
    for key, item in intake_items.items():
        geometry = original[key]
        _validate_request_binding({"request": item["request"], "source_binding": {}},
                                  key, geometry, standard_ref)
    if legacy_feedback:
        require(all(key in original for key in keys), "FROZEN_KEYFRAME_NOT_IN_NATIVE_INVENTORY")
        require(all(("def", identity, 0, frame) in original for frame in missing_frames),
                "FROZEN_MISSING_FRAME_NOT_IN_INVENTORY")

    authorized_keys = set(keys)
    if intake_items:
        authorized_keys = set(intake_items)
    return manifest, feedback, standard, {
        "inventory": inv_ref, "feedback": feedback_ref, "standard": standard_ref,
        "canonical_parent": canonical_ref,
        "source_intake": source_intake_ref,
    }, actual_sha, [{"key": list(key), "geometry": geom} for key, geom in original.items()], {
        "strict": strict, "authorized_keys": authorized_keys,
        "source_intake": source_intake, "source_intake_ref": source_intake_ref,
        "intake_items": intake_items,
        "static_domains": static_domains,
    }


def _image_metadata(path: Path, raw: bytes) -> dict:
    try:
        with Image.open(BytesIO(raw)) as picture:
            require(picture.format == "PNG" and picture.mode == "RGBA" and
                    getattr(picture, "n_frames", 1) == 1, "SOURCE_RGBA_PNG_REQUIRED")
            require(picture.width * picture.height <= 24_000_000, "SOURCE_PNG_TOO_LARGE")
            return {"pixels": [picture.width, picture.height],
                    "rgba_sha256": digest(picture.tobytes()),
                    "alpha1_pixels": picture.getchannel("A").histogram()[1]}
    except (OSError, ValueError) as exc:
        raise ValueError("SOURCE_PNG_INVALID: " + str(exc)) from exc


def _document_ref(value: dict, label: str, max_size: int = MAX_REPORT_BYTES) -> tuple[Path, bytes, dict, dict]:
    path, raw, pinned = binding(value, label, max_size)
    return path, raw, pinned, document(raw, label)


def _require_source_ref(document_value: dict, name: str, expected: dict, label: str) -> None:
    require(_same_ref(document_value.get(name), expected),
            label + "_" + name.upper() + "_MISMATCH")


def _validate_request_binding(candidate: dict, key: tuple, geometry: dict,
                              standard_ref: dict) -> tuple[dict, dict]:
    require(candidate.get("request") is not None, "SOURCE_REQUEST_REQUIRED")
    request_path, request_raw, request_ref, request = _document_ref(
        candidate["request"], "Town model request")
    require(request.get("schema") == "h3.art-town.model-request.v1" and
            _key_tuple(request.get("key"), "Town request key") == tuple(key),
            "SOURCE_REQUEST_KEY_MISMATCH")
    require(request.get("native_geometry") == {
        "canvas": geometry["canvas_1x"], "crop": geometry["crop_1x"],
        "origin": geometry["origin_1x"]},
            "SOURCE_REQUEST_GEOMETRY_MISMATCH")
    policy = request.get("art_direction_policy")
    if policy is not None:
        if not _same_ref(policy, standard_ref):
            require(candidate.get("source_binding", {}).get("art_standard_scope") ==
                    "HISTORICAL_CANDIDATE_ONLY", "SOURCE_REQUEST_ART_STANDARD_MISMATCH")
    authorization = request.get("scoped_production_authorization")
    if authorization is not None:
        require(authorization.get("human_art_approval_inferred") is False,
                "SOURCE_REQUEST_FORGED_ART_APPROVAL")
    return request_ref, request


def _validate_raw_binding(candidate: dict, source_ref: dict, request_ref: dict) -> dict:
    _, _, receipt_ref, receipt = _document_ref(candidate["raw_receipt"],
                                                "Town raw intake receipt")
    require(receipt.get("schema") == "h3.art-town.raw-intake.v1",
            "RAW_RECEIPT_SCHEMA_INVALID")
    _require_source_ref(receipt, "request", request_ref, "RAW_RECEIPT")
    _validate_image_refs(receipt, source_ref, "RAW_RECEIPT")
    require(receipt.get("preserved_raw_bytes") is True, "RAW_RECEIPT_SOURCE_MISMATCH")
    require(receipt.get("human_approved") is False and
            receipt.get("game_deployed") is False,
            "RAW_RECEIPT_FORGED_ACCEPTANCE")
    return receipt


def _validate_image_refs(value: dict, expected: dict, label: str) -> None:
    # Raw and master may occupy separate files, but both must really exist and
    # contain the preserved bytes; the canonical master path must also agree.
    require(_same_ref(value.get("master"), expected), label + "_SOURCE_MISMATCH")
    for name in ("raw", "master"):
        path, raw, pinned = binding(value.get(name), label + " " + name, MAX_PNG_BYTES)
        require(pinned["sha256"] == expected["sha256"], label + "_SOURCE_MISMATCH")
        _image_metadata(path, raw)


def _rect_ltrb(value: object, size: tuple | list, label: str) -> list[int]:
    require(type(value) is list and len(value) == 4 and
            all(type(n) is int and n >= 0 for n in value) and
            value[0] < value[2] and value[1] < value[3], label + "_LTRB_INVALID")
    require(value[2] <= size[0] and value[3] <= size[1], label + "_OUTSIDE_IMAGE")
    return value


def _validate_source_view_binding(candidate: dict, source_ref: dict,
                                 request_ref: dict) -> dict:
    _, _, view_ref, view = _document_ref(candidate["source_view"], "Town source view")
    require(view.get("schema") == "h3.art-town.source-view.v1",
            "SOURCE_VIEW_SCHEMA_INVALID")
    _require_source_ref(view, "request", request_ref, "SOURCE_VIEW")
    _validate_image_refs(view, source_ref, "SOURCE_VIEW")
    _, source_raw, _ = binding(source_ref, "source view master", MAX_PNG_BYTES)
    with Image.open(BytesIO(source_raw)) as picture:
        rect = _rect_ltrb(view.get("source_rect"), picture.size, "SOURCE_VIEW")
        alpha = picture.getchannel("A")
        total = alpha.histogram()
        inside = alpha.crop(rect).histogram()
        outside = [a - b for a, b in zip(total, inside)]
        measured = {"max_excluded_alpha": max(i for i, n in enumerate(outside) if n) if sum(outside) else 0,
                    "excluded_nonzero_pixels": sum(outside[1:]),
                    "retained_internal_alpha1_pixels": inside[1],
                    "source_total_alpha1_pixels": total[1]}
        require(measured["max_excluded_alpha"] <= 1, "SOURCE_VIEW_EXCLUDES_ALPHA_GE2")
        for name, number in measured.items():
            require(name not in view or type(view[name]) is int and view[name] == number,
                    "SOURCE_VIEW_MEASUREMENT_DRIFT:" + name)
        if "artifact_and_shadow_rect" in view:
            subject = _rect_ltrb(view["artifact_and_shadow_rect"], picture.size, "SOURCE_SUBJECT")
            require(rect[0] <= subject[0] and rect[1] <= subject[1] and
                    rect[2] >= subject[2] and rect[3] >= subject[3], "SOURCE_VIEW_EXCLUDES_SUBJECT")
        full = rect == [0, 0, *picture.size]
    return {"ref": view_ref, "rect_ltrb": rect, "document": view,
            "full_source": full, "measurements": measured}


def _validate_authorization_binding(candidate: dict, key: tuple, source_ref: dict,
                                    request_ref: dict, view: dict | None = None) -> dict:
    contract = candidate["source_binding"]
    auth_value = contract.get("authorization")
    require(auth_value is not None, "SOURCE_AUTHORIZATION_REQUIRED")
    _, _, auth_ref, authorization = _document_ref(auth_value,
                                                   "Town source authorization")
    require(authorization.get("schema") ==
            "h3.art-town.export-transparent-margin-authorization.v1" and
            _key_tuple(authorization.get("key"), "Town authorization key") == tuple(key),
            "SOURCE_AUTHORIZATION_KEY_MISMATCH")
    _require_source_ref(authorization, "request", request_ref, "SOURCE_AUTHORIZATION")
    _validate_image_refs(authorization, source_ref, "SOURCE_AUTHORIZATION")
    require(authorization.get("art_or_runtime_acceptance_granted") is not True and
            authorization.get("game_deployed") is not True,
            "SOURCE_AUTHORIZATION_FORGED_ACCEPTANCE")
    if view is not None and not view["full_source"]:
        require(authorization.get("status") == "USER_APPROVED_OUTSIDE_ALPHA1_ONLY" and
                authorization.get("source_rect") == view["rect_ltrb"] and
                authorization.get("source_rgba_or_alpha_edit_authorized") is False and
                authorization.get("production_alpha_threshold_applied") is False and
                authorization.get("retain_model_png_bytes") is True,
                "SOURCE_AUTHORIZATION_CROP_SCOPE_INVALID")
        for name in ("max_excluded_alpha", "excluded_nonzero_pixels"):
            require(type(authorization.get(name)) is int and
                    authorization[name] == view["measurements"][name],
                    "SOURCE_AUTHORIZATION_MEASUREMENT_DRIFT:" + name)
        # This is an existing source-specific application of a direct user
        # authority, not a new art approval created by this validator.
        _, _, _, batch = _document_ref(authorization.get("batch_user_authorization"), "outside-alpha1 batch authority")
        require(batch.get("schema") == "h3.art-town.building-batch-alpha1-user-authorization.v1" and
                batch.get("status") == "USER_APPROVED_BUILDING_BATCH_OUTSIDE_ALPHA1_ONLY" and
                batch.get("source") == "DIRECT_HUMAN_USER_MESSAGE" and
                batch.get("max_excluded_alpha") == 1 and
                batch.get("source_rgba_or_alpha_edit_authorized") is False and
                batch.get("production_alpha_threshold_applied") is False,
                "SOURCE_BATCH_AUTHORITY_INVALID")
        _require_source_ref(view["document"], "transparent_margin_authorization", auth_ref,
                            "SOURCE_VIEW")
    return {"ref": auth_ref, "document": authorization}


def _validate_derived_binding(candidate: dict, source_ref: dict, request_ref: dict) -> dict:
    contract = candidate["source_binding"]
    preparation = contract.get("preparation")
    mapping = contract.get("pixel_mapping")
    parent_chain = contract.get("parent_chain")
    require(preparation is not None and mapping is not None and parent_chain is not None,
            "DERIVED_SOURCE_BINDING_INPUTS_MISSING")
    _, _, preparation_ref, preparation_doc = _document_ref(
        preparation, "derived preparation")
    _, _, mapping_ref, mapping_doc = _document_ref(mapping, "derived pixel mapping")
    _, _, chain_ref, chain_doc = _document_ref(parent_chain, "derived parent chain")
    chain = chain_doc.get("chain")
    require(chain_doc.get("schema") == DERIVED_PARENT_CHAIN_SCHEMA and
            isinstance(chain, list) and 2 <= len(chain) <= 16 and
            all(isinstance(v, dict) for v in chain), "DERIVED_PARENT_CHAIN_INVALID")
    hashes = [v.get("sha256") for v in chain]
    require(len(set(hashes)) == len(hashes), "DERIVED_PARENT_CHAIN_CYCLE")
    require(len(chain) == 2, "DERIVED_MAPPING_REQUIRES_DIRECT_ROOT_TO_LEAF")
    refs = []
    for value in chain:
        path, raw, pinned = binding(value, "derived parent image", MAX_PNG_BYTES)
        _image_metadata(path, raw)
        refs.append(pinned)
    require(chain_doc.get("root_sha256") == refs[0]["sha256"] and
            chain_doc.get("leaf_sha256") == refs[-1]["sha256"] and
            _same_ref(refs[-1], source_ref), "DERIVED_PARENT_CHAIN_INVALID")
    require(preparation_doc.get("schema") == DERIVED_PREPARATION_SCHEMA and
            preparation_doc.get("source_sha256") == source_ref["sha256"] and
            preparation_doc.get("request_sha256") == request_ref["sha256"] and
            preparation_doc.get("mutated") is False,
            "DERIVED_PREPARATION_BINDING_INVALID")
    require(mapping_doc.get("schema") == DERIVED_MAPPING_SCHEMA and
            mapping_doc.get("source_sha256") == source_ref["sha256"] and
            mapping_doc.get("parent_sha256") == chain_doc.get("root_sha256") and
            mapping_doc.get("coordinate_semantics") == "ONE_TO_ONE_RGBA" and
            mapping_doc.get("resampled") is False and
            mapping_doc.get("alpha_edited") is False and
            mapping_doc.get("all_pixels_mapped") is True,
            "DERIVED_PIXEL_MAPPING_INVALID")
    _, parent_raw, _ = binding(refs[0], "derived root", MAX_PNG_BYTES)
    _, leaf_raw, _ = binding(source_ref, "derived leaf", MAX_PNG_BYTES)
    with Image.open(BytesIO(parent_raw)) as parent, Image.open(BytesIO(leaf_raw)) as leaf:
        rect = _rect_ltrb(mapping_doc.get("parent_rect_ltrb"), parent.size, "DERIVED_PARENT_RECT")
        dest = _rect_ltrb(mapping_doc.get("destination_rect_ltrb"), leaf.size, "DERIVED_DESTINATION_RECT")
        require([rect[2] - rect[0], rect[3] - rect[1]] ==
                [dest[2] - dest[0], dest[3] - dest[1]], "DERIVED_MAPPING_RESAMPLES")
        expected = Image.new("RGBA", leaf.size, (0, 0, 0, 0))
        expected.paste(parent.crop(rect), dest[:2])
        require(expected.tobytes() == leaf.tobytes(), "DERIVED_RGBA_MAPPING_MISMATCH")
    return {"preparation": preparation_ref, "pixel_mapping": mapping_ref,
            "parent_chain": chain_ref, "root": refs[0], "root_sha256": chain_doc["root_sha256"],
            "parent_rect_ltrb": rect, "pixel_copy_verified": True}


def _validate_source_contract(candidate: dict, key: tuple, geometry: dict,
                             source_ref: dict, standard_ref: dict,
                             intake_item: dict | None) -> dict:
    contract = candidate.get("source_binding")
    missing = [name for name in ("request", "raw_receipt", "phase_guide", "source_view")
               if candidate.get(name) is None]
    result = {"missing": missing}
    if contract is None:
        missing.append("source_binding")
        contract = {}
    else:
        require(isinstance(contract, dict) and contract.get("schema") == SOURCE_BINDING_SCHEMA and
            _key_tuple(contract.get("key"), "source binding key") == tuple(key) and
            contract.get("role") == candidate.get("source_role") and
            contract.get("source_view_rect_semantics") == "HALF_OPEN_LTRB",
            "SOURCE_BINDING_CONTRACT_INVALID")
    request_ref = None
    if candidate.get("request") is not None:
        request_ref, request = _validate_request_binding(candidate, key, geometry, standard_ref)
        result["request"] = request_ref
    else:
        for name in ("raw_receipt", "source_view"):
            if candidate.get(name) is not None:
                _, _, _, doc = _document_ref(candidate[name], "Town " + name)
                inherited = dict(candidate, request=doc.get("request"))
                request_ref, request = _validate_request_binding(inherited, key, geometry, standard_ref)
                break
    root_ref = source_ref
    if candidate["source_role"] == "DERIVED_PIXEL_COPY":
        absent = [name for name in ("preparation", "pixel_mapping", "parent_chain")
                  if contract.get(name) is None]
        missing.extend(absent)
        if not absent and request_ref:
            result["derived"] = _validate_derived_binding(candidate, source_ref, request_ref)
            root_ref = result["derived"]["root"]
    if request_ref and candidate.get("raw_receipt") is not None:
        _validate_raw_binding(candidate, root_ref, request_ref)
        result["raw_receipt"] = candidate["raw_receipt"]
    source_view = None
    if request_ref and candidate.get("source_view") is not None:
        source_view = _validate_source_view_binding(candidate, root_ref, request_ref)
        result.update({"source_view": source_view["ref"],
                       "source_view_rect_ltrb": source_view["rect_ltrb"],
                       "view_measurements": source_view["measurements"]})
        if "derived" in result:
            require(result["derived"]["parent_rect_ltrb"] == source_view["rect_ltrb"],
                    "DERIVED_COPY_OUTSIDE_BOUND_SOURCE_VIEW")
    guide_ref = None
    if candidate.get("phase_guide") is not None:
        guide_path, guide_raw, guide_ref = binding(candidate["phase_guide"], "Town phase guide", MAX_PNG_BYTES)
        require(guide_path.suffix.lower() == ".png" and
                (not contract or contract.get("phase_guide_sha256") == guide_ref["sha256"]),
                "PHASE_GUIDE_BINDING_INVALID")
        result["phase_guide"] = guide_ref
        result["phase_guide_native_payload_verified"] = False
    if intake_item is not None:
        require(_same_ref(root_ref, intake_item["master"]) and
                (request_ref is None or _same_ref(request_ref, intake_item["request"])) and
                (candidate.get("raw_receipt") is None or
                 _same_ref(candidate["raw_receipt"], intake_item["raw_receipt"])) and
                (guide_ref is None or guide_ref["sha256"] == intake_item["guide_sha256"]),
                "MODERN_SOURCE_INTAKE_CANDIDATE_MISMATCH")
    if contract.get("authorization") is not None and request_ref:
        result["authorization"] = _validate_authorization_binding(
            candidate, key, root_ref, request_ref, source_view)["ref"]
    elif source_view is not None and not source_view["full_source"]:
        missing.append("source_authorization")
    if source_view and source_view["document"].get("qualification_receipt_required_before_consumption") is True:
        if contract.get("view_qualification") is None:
            missing.append("source_view_qualification")
        else:
            _, _, pin, qualified = _document_ref(contract["view_qualification"], "source view qualification")
            require(qualified.get("schema") == "h3.art-town.transparent-margin-view-qualification.v1" and
                    qualified.get("status") == "SOURCE_VIEW_GUARD_PASS_METADATA_ONLY" and
                    qualified.get("failure") is None, "SOURCE_VIEW_QUALIFICATION_INVALID")
            _require_source_ref(qualified, "request", request_ref, "SOURCE_QUALIFICATION")
            _require_source_ref(qualified, "source_view", source_view["ref"], "SOURCE_QUALIFICATION")
            _require_source_ref(qualified, "source_specific_authorization", result.get("authorization"), "SOURCE_QUALIFICATION")
            _validate_image_refs(qualified, root_ref, "SOURCE_QUALIFICATION")
            outcome = qualified.get("guard_result", {})
            require(outcome.get("source_rect") == source_view["rect_ltrb"] and
                    outcome.get("retained_rgba_unchanged") is True and
                    outcome.get("production_alpha_threshold_applied") is False and
                    all(outcome.get(n) == source_view["measurements"][n] for n in
                        ("excluded_nonzero_pixels", "max_excluded_alpha")),
                    "SOURCE_VIEW_QUALIFICATION_MEASUREMENT_DRIFT")
            result["view_qualification"] = pin
    return result


def _validate_static_domains(value: dict, body_ref: dict, identity: str, standard_ref: dict) -> dict:
    _, _, pinned, domains = _document_ref(value, "source owner static/dynamic domains")
    require(domains.get("schema") == "h3.town.static-dynamic-domain-ownership.v1" and
            domains.get("key") == ["def", identity, 0, 0] and
            domains.get("authority") == "SOURCE_OWNER_FROZEN_DOMAIN_OWNERSHIP" and
            domains.get("art_approved") is False and domains.get("game_deployed") is False,
            "STATIC_DOMAIN_AUTHORITY_INVALID")
    _require_source_ref(domains, "static_body", body_ref, "STATIC_DOMAIN")
    _require_source_ref(domains, "art_standard", standard_ref, "STATIC_DOMAIN")
    path, raw, _ = binding(body_ref, "domain static body", MAX_PNG_BYTES)
    _image_metadata(path, raw)
    dynamic = domains.get("dynamic_masks")
    require(isinstance(dynamic, dict) and set(dynamic) == {"water", "foam"}, "STATIC_DYNAMIC_DOMAINS_REQUIRED")
    pins = {}
    with Image.open(BytesIO(raw)) as body:
        positive = body.getchannel("A").point(lambda n: 255 if n > 0 else 0)
        union = Image.new("L", body.size)
        for name, reference in [("static", domains.get("static_mask")), *dynamic.items()]:
            _, mask_raw, mask_ref = binding(reference, "owner domain mask " + name, MAX_PNG_BYTES)
            with Image.open(BytesIO(mask_raw)) as mask:
                require(mask.format == "PNG" and mask.mode == "L" and
                        getattr(mask, "n_frames", 1) == 1 and mask.size == body.size and
                        sum(mask.histogram()[1:255]) == 0, "STATIC_DOMAIN_BINARY_MASK_INVALID")
                require(ImageChops.multiply(union, mask).getbbox() is None, "STATIC_DYNAMIC_DOMAINS_OVERLAP")
                if name == "static":
                    require(mask.histogram()[255] > 0, "STATIC_DOMAIN_EMPTY")
                union = ImageChops.lighter(union, mask)
                pins[name] = mask_ref | {"positive_pixels": mask.histogram()[255]}
        require(union.tobytes() == positive.tobytes(), "STATIC_DYNAMIC_DOMAINS_DO_NOT_COVER_ALL_POSITIVE_ALPHA")
    return {"ref": pinned, "masks": pins, "declared_domain_coverage_verified": True,
            "semantic_body_or_scene_acceptance_granted": False}


def _validate_static_registration(candidate: dict, key: tuple, geometry: dict,
                                  standard_ref: dict, intake_context: dict) -> tuple[dict, tuple]:
    value = candidate.get("static_registration")
    require(value is not None, "STATIC_REGISTRATION_REQUIRED")
    path, raw, pinned, registration = _document_ref(value, "Town static registration")
    require(registration.get("schema") == STATIC_REGISTRATION_SCHEMA and
            _key_tuple(registration.get("key"), "static registration key") == tuple(key) and
            _key_tuple(registration.get("static_body_key"), "static body key") ==
            ("def", key[1], 0, 0),
            "STATIC_REGISTRATION_KEY_INVALID")
    require(registration.get("native_geometry") == _geometry_ref(geometry) and
            registration.get("modern_hd_standard_sha256") == standard_ref["sha256"],
            "STATIC_REGISTRATION_GEOMETRY_OR_STANDARD_INVALID")
    static_body = registration.get("static_body")
    require(isinstance(static_body, dict), "STATIC_BODY_REFERENCE_MISSING")
    body_path, body_raw, body_ref = binding(
        {"path": static_body.get("path"), "sha256": static_body.get("sha256")},
        "static body master", MAX_PNG_BYTES)
    require(body_path.suffix.lower() == ".png", "STATIC_BODY_MASTER_PNG_REQUIRED")
    _image_metadata(body_path, body_raw)
    if intake_context.get("source_intake") is not None:
        require(body_ref["sha256"] == intake_context["source_intake_ref"]["static_master"]["sha256"],
                "STATIC_BODY_MASTER_NOT_CURRENT")
    scene_xy = registration.get("fixed_scene_xy")
    canvas = registration.get("registered_canvas_3x")
    anchor = registration.get("static_anchor_ltrb_3x")
    require(type(scene_xy) is list and len(scene_xy) == 2 and all(type(n) is int for n in scene_xy) and
            type(canvas) is list and len(canvas) == 2 and all(type(n) is int and n > 0 for n in canvas) and
            type(anchor) is list and len(anchor) == 4 and all(type(n) is int for n in anchor) and
            anchor[0] < anchor[2] and anchor[1] < anchor[3],
            "STATIC_REGISTRATION_ANCHOR_INVALID")
    _rect_ltrb(anchor, canvas, "STATIC_REGISTRATION_ANCHOR")
    require(canvas == geometry["target_canvas_3x"],
            "STATIC_REGISTRATION_3X_UNITS_MISMATCH")
    require(registration.get("bbox_fit_per_frame") is False and
            registration.get("dynamic_layers") == ["water", "foam"],
            "STATIC_REGISTRATION_DYNAMIC_LAYER_INVALID")
    verified = False
    domains = intake_context.get("static_domains")
    domain_covered = False
    correspondence = registration.get("pixel_correspondence")
    if correspondence is not None:
        require(isinstance(correspondence, dict), "STATIC_PIXEL_CORRESPONDENCE_INVALID")
        if correspondence.get("domain_ownership") is not None:
            require(domains is not None and _same_ref(correspondence["domain_ownership"], domains["ref"]),
                    "STATIC_DOMAIN_NOT_PINNED_BY_SOURCE_OWNER")
        _require_source_ref(correspondence, "source", candidate["source"], "STATIC_REGISTRATION")
        mask_path, mask_raw, mask_ref = binding(correspondence.get("static_mask"), "static pixel mask", MAX_PNG_BYTES)
        source_path, source_raw, source_ref = binding(candidate["source"], "registered source", MAX_PNG_BYTES)
        offset = correspondence.get("body_offset_in_source")
        require(type(offset) is list and len(offset) == 2 and all(type(n) is int and n >= 0 for n in offset),
                "STATIC_PIXEL_OFFSET_INVALID")
        with Image.open(BytesIO(mask_raw)) as mask, Image.open(BytesIO(body_raw)) as body, \
                Image.open(BytesIO(source_raw)) as source:
            require(mask.format == "PNG" and mask.mode == "L" and getattr(mask, "n_frames", 1) == 1 and mask.size == body.size and
                    0 < mask.histogram()[255] and sum(mask.histogram()[1:255]) == 0,
                    "STATIC_MASK_MUST_BE_NONEMPTY_BINARY_L")
            rect = [*offset, offset[0] + body.width, offset[1] + body.height]
            _rect_ltrb(rect, source.size, "STATIC_BODY_IN_SOURCE")
            region = source.crop(rect)
            zero = Image.new("RGBA", body.size)
            require(Image.composite(body, zero, mask).tobytes() ==
                    Image.composite(region, zero, mask).tobytes(), "STATIC_RGBA_CORRESPONDENCE_MISMATCH")
            require(ImageChops.multiply(body.getchannel("A"), mask).getbbox() is not None,
                    "STATIC_MASK_HAS_NO_VISIBLE_BODY")
        verified = True
        if domains is not None and correspondence.get("domain_ownership") is not None:
            require(_same_ref(correspondence["static_mask"], domains["masks"]["static"]),
                    "STATIC_MASK_IS_NOT_ENTIRE_OWNER_STATIC_DOMAIN")
            domain_covered = True
    fingerprint = (body_ref["sha256"], tuple(scene_xy), tuple(canvas), tuple(anchor),
                   json.dumps(_geometry_ref(geometry), sort_keys=True),
                   mask_ref["sha256"] if verified else None)
    return {"ref": pinned, "static_body": body_ref, "fixed_scene_xy": scene_xy,
            "registered_canvas_3x": canvas, "static_anchor_ltrb_3x": anchor,
            "bbox_fit_per_frame": False, "dynamic_layers": ["water", "foam"],
            "pixel_correspondence_verified": verified,
            "static_domain_coverage_verified": domain_covered,
            "domain_ownership": domains["ref"] if domain_covered else None,
            "semantic_body_or_scene_acceptance_granted": False}, fingerprint


def _validate_native_review_binding(candidate: dict, key: tuple, source_ref: dict) -> dict:
    value = candidate.get("native_review")
    require(value is not None, "NATIVE_REVIEW_REQUIRED")
    _, _, pinned, report = _document_ref(value, "Town native review", MAX_REPORT_BYTES)
    require(report.get("schema") == "h3.native-review.batch-report.v1",
            "NATIVE_REVIEW_SCHEMA_INVALID")
    items = report.get("items")
    require(isinstance(items, list), "NATIVE_REVIEW_ITEMS_MISSING")
    matches = [item for item in items if isinstance(item, dict) and
               _key_tuple(item.get("key"), "native review key") == tuple(key)]
    require(len(matches) == 1 and matches[0].get("source_sha256") == source_ref["sha256"],
            "NATIVE_REVIEW_SOURCE_OR_KEY_MISMATCH")
    item = matches[0]
    require(_same_ref({"path": item.get("source_png"), "sha256": item.get("source_sha256")}, source_ref),
            "NATIVE_REVIEW_SOURCE_PATH_MISMATCH")
    for name in ("manifest", "official_inventory", "render_spec", "native_receipt"):
        binding(report.get(name), "native review " + name, MAX_REPORT_BYTES if name != "official_inventory" else 128 * 1024 * 1024)
    require(report.get("status") == "REVIEW_REQUIRED" and isinstance(report.get("authority"), dict) and
            all(report["authority"].get(n) is False for n in
                ("art_approved", "human_approved", "runtime_accepted", "game_deployed")),
            "NATIVE_REVIEW_FORGED_ACCEPTANCE")
    outputs = item.get("outputs")
    require(isinstance(outputs, dict) and "candidate_3x" in outputs,
            "NATIVE_REVIEW_OUTPUTS_MISSING")
    for name, value in outputs.items():
        path, raw, _ = binding({"path": value.get("path"), "sha256": value.get("png_sha256"),
                                "pixels": value.get("pixels")}, "native review output " + name, MAX_PNG_BYTES)
        if value.get("rgba_sha256"):
            require(_image_metadata(path, raw)["rgba_sha256"] == value["rgba_sha256"],
                    "NATIVE_REVIEW_OUTPUT_RGBA_DRIFT")
    return {"ref": pinned, "renderer": report.get("renderer"),
            "official_inventory": report["official_inventory"], "targets": item.get("targets")}


def _v2_dependencies(row: dict, candidate: dict, key: tuple, geometry: dict, source_ref: dict,
                     standard_ref: dict, intake_item: dict | None, context: dict) -> None:
    needs = row["needs"]
    errors = row["errors"]

    def attempt(action):
        try:
            return action()
        except Exception as exc:
            errors.append(str(exc))
            return None

    # A missing earlier input never shields invalid evidence supplied later.
    for name in DEPENDENCIES:
        if candidate[name] is None:
            needs.append(name)
        else:
            pinned = attempt(lambda name=name: binding(candidate[name], name)[2])
            if pinned:
                row.setdefault("evidence", {})[name] = pinned
    if candidate["native_review"] is not None:
        review = attempt(lambda: _validate_native_review_binding(candidate, key, source_ref))
        if review:
            row["native_review"] = review["ref"]
            require_geometry = {"canvas_1x": geometry["canvas_1x"], "crop_1x": geometry["crop_1x"],
                                "origin_1x": geometry["origin_1x"]}
            if review["targets"] != require_geometry:
                errors.append("NATIVE_REVIEW_GEOMETRY_MISMATCH")
            if not _same_ref(review["official_inventory"], context["inventory"]):
                errors.append("NATIVE_REVIEW_INVENTORY_MISMATCH")
            if not _same_ref(review["renderer"], context["renderer"]):
                needs.append("current_native_review")
    source = attempt(lambda: _validate_source_contract(candidate, key, geometry, source_ref, standard_ref, intake_item))
    source_missing = []
    if source:
        row["source_binding"] = source
        source_missing = source["missing"]
        needs.extend(source_missing)
    mapping_ref = None
    if candidate["native_mapping"] is None:
        needs.append("native_mapping")
    else:
        def mapping():
            _, _, pinned, doc = _document_ref(candidate["native_mapping"], "native mapping")
            require(doc.get("schema") == "h3.town.native-mapping.v1" and
                    doc.get("status") == "EXPLICIT_CURRENT_MAPPING", "NATIVE_MAPPING_NOT_CURRENT")
            require(doc.get("key") == key, "NATIVE_MAPPING_KEY_MISMATCH")
            require(doc.get("source_sha256") == source_ref["sha256"], "NATIVE_MAPPING_SOURCE_MISMATCH")
            require(doc.get("geometry") == _geometry_ref(geometry), "NATIVE_MAPPING_GEOMETRY_MISMATCH")
            require(doc.get("native_scale") == 3, "NATIVE_MAPPING_SCALE_MISMATCH")
            return pinned
        mapping_ref = attempt(mapping)
        if mapping_ref:
            row["native_mapping"] = mapping_ref
    registered = False
    if candidate["static_registration"] is not None:
        value = attempt(lambda: _validate_static_registration(candidate, key, geometry, standard_ref, context))
        if value:
            static, fingerprint = value
            row["static_anchor"] = static
            registered = static["pixel_correspondence_verified"] and static["static_domain_coverage_verified"]
            if registered:
                row["static_registration"] = static["ref"]
                row["_static_fingerprint"] = fingerprint
            if not static["pixel_correspondence_verified"]:
                needs.append("static_pixel_correspondence")
            if not static["static_domain_coverage_verified"]:
                needs.append("source_owner_static_dynamic_domains")
    if candidate["timing"] is not None:
        def timing():
            _, _, _, doc = _document_ref(candidate["timing"], "Town timing")
            require(doc.get("schema") == "h3.town.timing-contract.v1" and
                    doc.get("key") == key, "TIMING_CONTRACT_INVALID")
            if doc.get("status") == "UNKNOWN_NOT_GUESSED":
                require(all(doc.get(n) is None for n in ("duration_ms", "loop", "facing", "footpoint")),
                        "TIMING_UNKNOWN_HAS_INVENTED_VALUES")
            # Actual provenance/phase validation belongs to the motion input,
            # not presence of this documentary declaration.
            needs.append("native_timing_provenance")
        attempt(timing)
    row["needs"] = list(dict.fromkeys(needs))
    if errors:
        row["status"] = "BLOCKED"
    elif source_missing:
        row["status"] = "NEEDS_SOURCE_BINDING"
    elif not mapping_ref:
        row["status"] = "NEEDS_NATIVE_MAPPING"
    elif not registered:
        row["status"] = "NEEDS_REGISTRATION"
    else:
        row["status"] = "REVIEW_REQUIRED"
    row["needs"].append("independent_art_and_scene_acceptance")


def _candidate_state(candidate: dict, slot: dict, a04_sha: str, *,
                     strict: bool = False, standard_ref: dict | None = None,
                     intake_item: dict | None = None, intake_context: dict | None = None) -> dict:
    key = slot["key"]
    geometry = slot["geometry"]
    needs = []
    row = {"status": "REVIEW_REQUIRED", "errors": [], "needs": needs}
    source = candidate["source"]
    try:
        source_path, source_raw, source_ref = binding(source, "candidate source", MAX_PNG_BYTES)
        require(source_path.suffix.lower() == ".png", "SOURCE_PNG_REQUIRED")
        require(candidate["source_role"] in ("MODEL_MASTER", "DERIVED_PIXEL_COPY"),
                "SOURCE_ROLE_INVALID")
        row["source"] = source_ref | {"role": candidate["source_role"]}
        row["source_diagnostic"] = _image_metadata(source_path, source_raw)
        if source_ref["sha256"] == a04_sha:
            row["status"] = "BLOCKED"
            row["errors"] = ["A04_NOT_MOTION_KEYFRAME: fixed Town A04 is a color-only diagnostic"]
            return row
        if strict:
            _v2_dependencies(row, candidate, key, geometry, source_ref, standard_ref,
                             intake_item, intake_context or {})
            return row
        for label in DEPENDENCIES:
            if candidate[label] is None:
                needs.append(label)
            else:
                _, _, pinned = binding(candidate[label], label)
                row.setdefault("evidence", {})[label] = pinned
        if candidate["native_mapping"] is None:
            row["status"] = "NEEDS_NATIVE_MAPPING"
            needs.append("native_mapping")
            return row
        _, raw, pinned = binding(candidate["native_mapping"], "native mapping")
        mapping = document(raw, "native mapping")
        require(mapping.get("schema") == "h3.town.native-mapping.v1"
                and mapping.get("status") == "EXPLICIT_CURRENT_MAPPING",
                "NATIVE_MAPPING_NOT_CURRENT")
        require(mapping.get("key") == key, "NATIVE_MAPPING_KEY_MISMATCH")
        require(mapping.get("source_sha256") == source_ref["sha256"],
                "NATIVE_MAPPING_SOURCE_MISMATCH")
        require(mapping.get("geometry") == {
            "canvas_1x": geometry["canvas_1x"],
            "crop_1x": geometry["crop_1x"],
            "origin_1x": geometry["origin_1x"],
        }, "NATIVE_MAPPING_GEOMETRY_MISMATCH")
        require(mapping.get("native_scale") == 3, "NATIVE_MAPPING_SCALE_MISMATCH")
        row["native_mapping"] = pinned
        if candidate["source_role"] == "DERIVED_PIXEL_COPY":
            require(row["source_diagnostic"]["pixels"] == geometry["target_crop_3x"],
                    "DERIVED_SOURCE_NOT_EXACT_NATIVE_3X")
            needs.append("highest_native_model_source")
        # Frozen Town F1/F5/F9 are explicitly unselected. SHA presence cannot grant
        # independent source selection, scene/timing authority or art approval.
        row["status"] = "REVIEW_REQUIRED"
        needs.append("independent_art_and_scene_acceptance")
    except Exception as exc:
        row["status"] = "BLOCKED"
        row["errors"] = [str(exc)]
    return row


def evaluate(manifest_path: Path, manifest_sha: str, renderer: Path, renderer_sha: str) -> dict:
    require(type(manifest_sha) is str and SHA.fullmatch(manifest_sha) is not None, "MANIFEST_SHA_INVALID")
    require(type(renderer_sha) is str and SHA.fullmatch(renderer_sha) is not None, "RENDERER_SHA_INVALID")
    renderer = absolute_file(str(renderer), "renderer")
    build_sha = current_receipt_build(renderer, renderer_sha)
    manifest, feedback, standard, refs, inventory_sha, native, intake_context = load_inputs(
        manifest_path, manifest_sha)
    strict = intake_context["strict"]
    intake_context.update({"inventory": refs["inventory"],
                           "renderer": {"path": str(renderer), "sha256": renderer_sha}})
    authorized_keys = intake_context["authorized_keys"]
    if feedback.get("schema") == FEEDBACK_SCHEMA:
        a03_keys = {tuple(value) for value in
                    feedback["town_inputs"]["a03_keyframe_candidates"]["keys"]}
        blocked_sha = feedback["town_inputs"]["a04_color_candidate"]["source_sha256"]
        require(type(blocked_sha) is str and SHA.fullmatch(blocked_sha) is not None,
                "A04_SOURCE_SHA_INVALID")
    else:
        a03_keys = set()
        blocked_sha = None
    candidates = manifest["candidates"]
    require(type(candidates) is list and len(candidates) <= 256, "CANDIDATE_COUNT_INVALID")
    supplied = {}
    for candidate in candidates:
        exact_fields(candidate, V2_CANDIDATE_FIELDS if strict else CANDIDATE_FIELDS,
                     "Town candidate")
        key = tuple(native_key(candidate["key"], "candidate key"))
        require(key not in supplied, "CANDIDATE_KEY_DUPLICATE")
        require(key in authorized_keys, "CANDIDATE_NOT_AUTHORIZED_BY_FROZEN_FEEDBACK")
        supplied[key] = candidate

    slots = []
    for slot in native:
        key = tuple(slot["key"])
        row = {"key": slot["key"], "geometry": slot["geometry"], "status": "NEEDS_KEYFRAME",
               "needs": ["source_binding", "keyframe", "phase", "timing", "scene_context"],
               "errors": [], "candidate_only": True}
        if key in authorized_keys:
            row["status"] = "NEEDS_NATIVE_MAPPING"
            row["needs"] = ["selected_source_sha", "request", "raw_receipt", "phase_guide",
                            "static_registration", "scene_context", "timing", "native_mapping"]
            row["candidate_declared_by_town_feedback"] = key in a03_keys
            if strict and key in intake_context["intake_items"]:
                row["candidate_declared_by_modern_intake"] = True
        if key in supplied:
            row.update(_candidate_state(
                supplied[key], row, blocked_sha, strict=strict,
                standard_ref=refs["standard"],
                intake_item=intake_context["intake_items"].get(key),
                intake_context=intake_context))
        slots.append(row)
    static_fingerprints = [row.pop("_static_fingerprint") for row in slots
                           if "_static_fingerprint" in row]
    if strict and static_fingerprints and any(value != static_fingerprints[0]
                                              for value in static_fingerprints[1:]):
        for row in slots:
            if "static_anchor" in row:
                row["status"] = "BLOCKED"
                row["errors"].append("STATIC_REGISTRATION_DRIFT")
    totals = Counter(row["status"] for row in slots)
    state = "BLOCKED" if totals["BLOCKED"] else "REVIEW_REQUIRED"
    report_schema = "h3.town.process-review.v2" if strict else REPORT_SCHEMA
    fingerprint = digest(json.dumps({
        "schema": report_schema, "operation_id": manifest["operation_id"],
        "manifest_sha256": manifest_sha, "inventory_sha256": inventory_sha,
        "feedback_sha256": refs["feedback"]["sha256"],
        "standard_sha256": refs["standard"]["sha256"],
        "source_intake_sha256": refs["source_intake"]["sha256"] if refs["source_intake"] else None,
        "renderer_sha256": renderer_sha, "build_receipt_sha256": build_sha,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "schema": report_schema, "status": state, "operation_id": manifest["operation_id"],
        "operation_fingerprint": fingerprint,
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        "official_inventory": refs["inventory"],
        "town_feedback": refs["feedback"], "art_standard": refs["standard"] |
        {"authority": standard["authority"], "canonical_parent": refs["canonical_parent"]},
        "source_intake": refs["source_intake"],
        "renderer_sha256": renderer_sha, "build_receipt_sha256": build_sha,
        "identity": manifest["identity"], "summary": {
            name: totals.get(name, 0) for name in
            ("READY", "NEEDS_KEYFRAME", "NEEDS_SOURCE_BINDING", "NEEDS_NATIVE_MAPPING",
             "NEEDS_REGISTRATION", "REVIEW_REQUIRED", "BLOCKED")
        }, "slot_count": len(slots), "slots": slots,
        "formal_process_export_allowed": False, "png_images_written": 0,
        "model_calls": 0, "native_timing_verified": False,
        "art_approved": False, "game_deployed": False,
        "note": "Native and modern-HD source-bound review only. Explicit art and runtime acceptance are separate.",
    }


def output_root(value: str, operation_id: str) -> Path:
    out = Path(os.path.abspath(value))
    base = Path(os.path.abspath(OUTPUT_BASE))
    require(_path_key(out.parent) == _path_key(base) and out.name == operation_id,
            "OUTPUT_MUST_BE_NEW_OPERATION_CHILD")
    return out


def review_html(report: dict) -> bytes:
    rows = []
    for item in report["slots"]:
        key = item["key"]
        rows.append("<tr><td>" + html.escape(f"{key[1]} G{key[2]}/F{key[3]}") +
                    "</td><td>" + html.escape(item["status"]) +
                    "</td><td>" + html.escape(", ".join(item["needs"])) +
                    "</td><td>" + html.escape(", ".join(item["errors"])) + "</td></tr>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<title>H3TierExport Town batch PROCESS review</title>"
            "<style>body{font:14px system-ui;margin:2rem auto;max-width:1200px}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:.5rem}</style>"
            "<h1>Native Town batch review - no process frames generated</h1><p>Source and geometry "
            "are SHA-bound. Modern-HD art approval, timing, scene and game acceptance remain open.</p>"
            "<table><thead><tr><th>Native slot</th><th>Status</th><th>Missing</th><th>Error</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p>Exact evidence hashes are in review-report.json.</p></html>")
    return page.encode("utf-8")


def store(path: Path, raw: bytes, guard: OutputRootGuard) -> str:
    path = guard.verify_target(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    guard.verify_target(path, must_exist=True)
    require(digest(read_bounded(path, MAX_REPORT_BYTES, "stored output")) == digest(raw),
            "OUTPUT_READBACK_DRIFT")
    return digest(raw)


def completion(report: dict) -> dict:
    return {"schema": COMPLETE_SCHEMA, "operation_id": report["operation_id"],
            "operation_fingerprint": report["operation_fingerprint"],
            "report_sha256": digest(json_bytes(report)),
            "html_sha256": digest(review_html(report)),
            "art_approved": False, "game_deployed": False,
            "images_written": 0, "status": "REVIEW_REQUIRED"}


def verify(report: dict, out: Path) -> dict:
    require(out.is_dir(), "OUTPUT_ROOT_MISSING_OR_PARTIAL")
    guard = OutputRootGuard.open_existing(out, OUTPUT_BASE)
    try:
        for name, raw in (("review-report.json", json_bytes(report)),
                          ("index.html", review_html(report)),
                          ("analysis-complete.json", json_bytes(completion(report)))):
            target = guard.verify_target(out / name, must_exist=True)
            require(read_bounded(target, MAX_REPORT_BYTES, "readback " + name) == raw,
                    "OUTPUT_RECEIPT_DRIFT:" + name)
        require(not any(out.rglob("*.png")), "UNRECEIPTED_PNG_NOT_ALLOWED")
        guard.verify_current()
    finally:
        guard.close()
    return {"schema": "h3.town.process-review.result.v1",
            "status": "VERIFIED_REVIEW_REQUIRED", "operation_id": report["operation_id"],
            "report_sha256": digest(json_bytes(report)), "images_written": 0,
            "art_approved": False, "game_deployed": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("stage", choices=("check", "run", "verify"))
    for field in ("manifest", "manifest-sha256", "operation-id", "outdir",
                  "renderer", "renderer-sha256"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        require(type(args.operation_id) is str and TOKEN.fullmatch(args.operation_id) is not None,
                "OPERATION_ID_INVALID")
        path = absolute_file(args.manifest, "manifest")
        out = output_root(args.outdir, args.operation_id)
        report = evaluate(path, args.manifest_sha256, Path(args.renderer), args.renderer_sha256)
        require(report["operation_id"] == args.operation_id, "OPERATION_ID_MISMATCH")
        if args.stage == "check":
            print(json.dumps(report, ensure_ascii=False))
            return 1 if report["status"] == "BLOCKED" else 0
        if report["status"] == "BLOCKED":
            print(json.dumps(report, ensure_ascii=False))
            return 1
        if args.stage == "verify":
            print(json.dumps(verify(report, out), ensure_ascii=False))
            return 0
        if _lexists(out):
            previous = verify(report, out)
            previous["status"] = "REUSED_VERIFIED"
            print(json.dumps(previous, ensure_ascii=False))
            return 0
        guard = OutputRootGuard.create(out, OUTPUT_BASE)
        try:
            require(evaluate(path, args.manifest_sha256, Path(args.renderer),
                             args.renderer_sha256) == report, "INPUT_DRIFT_BEFORE_RUN")
            store(out / "review-report.json", json_bytes(report), guard)
            store(out / "index.html", review_html(report), guard)
            require(evaluate(path, args.manifest_sha256, Path(args.renderer),
                             args.renderer_sha256) == report, "INPUT_DRIFT_DURING_RUN")
            store(out / "analysis-complete.json", json_bytes(completion(report)), guard)
            guard.verify_current()
        finally:
            guard.close()
        print(json.dumps({"schema": "h3.town.process-review.result.v1",
                          "status": "REVIEW_REQUIRED", "operation_id": args.operation_id,
                          "outdir": str(out), "report_sha256": digest(json_bytes(report)),
                          "summary": report["summary"], "images_written": 0,
                          "art_approved": False, "game_deployed": False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"schema": "h3.town.process-review.error.v1",
                          "status": "BLOCKED", "error": str(exc),
                          "art_approved": False, "images_written": 0,
                          "game_deployed": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
