#!/usr/bin/env python3
"""SHA-bound official DEF pose guides. Review only; never writes art masters."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
import homm3data.deffile as deffile

from native_h3_io import mapped_lod, payload, parse_def, decode_native_pixels
from creature_master_set import read_json as read_standard

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "build" / "experiments" / "creature-pose-guide"
CREATURE_MASTER_SET_ROOT = ROOT / "build" / "experiments" / "creature-master-set"
REQUESTS = EXPERIMENTS / "requests"
SCHEMA = "h3.creature.native-pose-guide.request.v1"
LANDMARK_SCHEMA = "h3.creature.scale-landmarks.v1"
MEASUREMENT_DEFINITIONS = {
    "head": "cranium_top_through_brow_to_chin",
    "torso": "shoulder_midpoint_to_pelvis_center",
    "body": "cranium_top_through_torso_and_leg_to_support_ankle",
    "arm_left": "left_shoulder_through_elbow_wrist_to_hand",
    "arm_right": "right_shoulder_through_elbow_wrist_to_hand",
    "leg_left": "left_hip_through_knee_ankle_to_support_foot",
    "leg_right": "right_hip_through_knee_ankle_to_support_foot",
}


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_bound(path, sha):
    path = Path(path).resolve(strict=True)
    if digest_file(path) != sha.lower():
        raise ValueError("SOURCE_SHA_DRIFT: " + str(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bind(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": digest_file(path)}


def ensure_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("OPERATION_ID_INVALID")


def class_groups(slots):
    by_offset = defaultdict(list)
    for row in slots:
        by_offset[row["offset"]].append(row)
    return by_offset


def equivalent_except_shadow(reference, normal):
    a, b = np.asarray(reference.convert("RGBA")), np.asarray(normal.convert("RGBA"))
    if a.shape != b.shape:
        return False
    diff = np.any(a != b, axis=2)
    # The existing official 1X reader preserves palette shadows which
    # homm3data's normal renderer drops. Do not discard reference shadows.
    allowed = ((a[:, :, 0] == 0) & (a[:, :, 1] == 0) &
               (a[:, :, 2] == 0) & np.isin(a[:, :, 3], [64, 128]) &
               (b[:, :, 3] == 0))
    return bool(np.all(~diff | allowed))


def same_geometry(native, row):
    head = native["frame_header"]
    expected = row["geometry"]
    return (list(head[2:4]) == expected["canvas_1x"] and
            list(head[4:6]) == expected["crop_1x"] and
            list(head[6:8]) == expected["origin_1x"] and
            native["resource"].lower() == row["resource"].lower())


def reference_matches_offset(ref, row, native_slots, checkpoint, identity):
    slot = ref.get("reference_slot")
    if not isinstance(slot, list) or len(slot) != 2:
        return False
    owner = native_slots.get(tuple(slot))
    ck = checkpoint.get("images", {}).get(f"def:{identity}:{slot[0]}:{slot[1]}")
    return bool(owner and owner["offset"] == row["offset"] and ck and
                ck.get("sha256") == ref.get("sha256") and
                Path(ref.get("path", "")).name == f"{slot[0]}_{slot[1]}.png")


def native_blob(lod_path, identity):
    with mapped_lod(lod_path) as archive:
        entry = next((e for e in archive.entries if e["name"] == identity + ".DEF"), None)
        if entry is None:
            raise ValueError("DEF_NOT_IN_LOD")
        return payload(entry)


def freeze(args):
    ensure_id(args.operation_id)
    audit = read_bound(args.audit_manifest, args.audit_sha256)
    checkpoint = read_bound(args.checkpoint, args.checkpoint_sha256)
    standard, standard_sha = read_standard(Path(args.art_standard), args.art_standard_sha256)
    identity = audit["identity"]
    if audit.get("schema") != "h3.creature.scale-report-manifest.v1" or not re.fullmatch(r"[A-Z0-9_]+", identity):
        raise ValueError("AUDIT_MANIFEST_INVALID")
    lod = bind(args.lod)
    if lod["sha256"] != args.lod_sha256.lower():
        raise ValueError("LOD_SHA_DRIFT")
    inventory = audit["native_inventory"]
    if digest_file(inventory["path"]) != inventory["sha256"]:
        raise ValueError("INVENTORY_SHA_DRIFT")
    blob = native_blob(lod["path"], identity)
    if digest_bytes(blob) != checkpoint["source_payload_sha256"]:
        raise ValueError("CHECKPOINT_DEF_SHA_DRIFT")
    lod_input = [x for x in checkpoint["inputs"] if Path(x["path"]).name.lower() == Path(lod["path"]).name.lower()]
    if len(lod_input) != 1 or lod_input[0]["sha256"] != lod["sha256"]:
        raise ValueError("CHECKPOINT_LOD_SHA_DRIFT")
    parsed = parse_def(blob)
    parsed_by_key = {(s["group_id"], s["frame_index"]): s for s in parsed["slots"]}
    audit_slots = audit["native_slots"]
    if len(parsed_by_key) != len(audit_slots):
        raise ValueError("OFFICIAL_SLOT_COUNT_DRIFT")
    entries = []
    snapshot = {Path(x["path"]).name: x for x in audit["source_snapshot"]}
    for row in audit_slots:
        key = row["key"]
        if key[:2] != ["def", identity] or Path(row["native_lod"]).name.lower() != Path(lod["path"]).name.lower():
            raise ValueError("SLOT_IDENTITY_OR_LOD_DRIFT")
        official = parsed_by_key.get((key[2], key[3]))
        if official is None or not same_geometry(official, row):
            raise ValueError("OFFICIAL_GEOMETRY_OR_NAME_DRIFT")
        name = f"{key[2]}_{key[3]}.png"
        master = snapshot.get(name)
        if master and digest_file(master["path"]) != master["sha256"]:
            raise ValueError("MASTER_SHA_DRIFT")
        entries.append({"group": key[2], "frame": key[3], "offset": official["offset"],
                        "resource": official["resource"], "geometry": row["geometry"],
                        "master": master, "reference": None})
    classes = class_groups(entries)
    refs = Path(args.refs_dir).resolve(strict=True)
    with deffile.open(io.BytesIO(blob)) as decoder:
        raw_map = {(x["group_id"], x["image_id"]): x for x in decoder.get_raw_data()}
        for offset, members in classes.items():
            choices = []
            for row in members:
                p = refs / f"{row['group']}_{row['frame']}.png"
                ck = checkpoint["images"].get(f"def:{identity}:{row['group']}:{row['frame']}")
                if p.is_file() and ck:
                    if digest_file(p) != ck["sha256"]:
                        raise ValueError("REFERENCE_SHA_DRIFT")
                    choices.append((p, row))
            if not choices:
                raise ValueError(f"NO_CHECKPOINT_REFERENCE_FOR_OFFSET: {offset}")
            path, owner = choices[0]
            original = parsed_by_key[(owner["group"], owner["frame"])]
            if decode_native_pixels(blob, original) != raw_map[(owner["group"], owner["frame"])]["image"]["pixeldata"]:
                raise ValueError("INDEPENDENT_INDEX_DECODE_MISMATCH")
            with Image.open(path) as source:
                rgba = source.convert("RGBA")
            comparison = decoder.read_image(how="normal", group_id=owner["group"], image_id=owner["frame"]).convert("RGBA")
            if not equivalent_except_shadow(rgba, comparison):
                raise ValueError("REFERENCE_PIXELS_DISAGREE_WITH_DEF")
            ref = {**bind(path), "reference_slot": [owner["group"], owner["frame"]],
                   "pixel_comparison": "EQUAL_EXCEPT_PRESERVED_BLACK_SHADOW_ALPHA"}
            for member in members:
                member["reference"] = ref
    request = {"schema": SCHEMA, "operation_id": args.operation_id, "identity": identity,
               "audit": bind(args.audit_manifest), "inventory": inventory, "lod": lod,
               "def_sha256": digest_bytes(blob), "checkpoint": bind(args.checkpoint),
               "art_standard": {"path": str(Path(args.art_standard).resolve()), "sha256": standard_sha,
                                "version": standard["version"]},
               "implementation": [bind(__file__), bind(Path(__file__).with_name("native_h3_io.py")),
                                  bind(Path(__file__).with_name("build_official_def_frame_map.py"))],
               "slots": entries, "output_status": "REVIEW_ONLY",
               "body_landmarks": "NOT_MEASURED", "native_K": "NOT_MEASURED",
               "automatic_art_approval": False}
    REQUESTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).resolve()
    if out.parent != REQUESTS.resolve() or out.name != args.operation_id + ".json":
        raise ValueError("REQUEST_PATH_OUTSIDE_OPERATION_ROOT")
    with out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(request, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "FROZEN", "manifest": str(out), "sha256": digest_file(out),
            "slots": len(entries), "native_offset_classes": len(classes)}


def validate(args):
    ensure_id(args.operation_id)
    request = read_bound(args.manifest, args.manifest_sha256)
    if request.get("schema") != SCHEMA or request.get("operation_id") != args.operation_id:
        raise ValueError("REQUEST_ID_OR_SCHEMA_INVALID")
    out = Path(args.outdir).resolve()
    if out.parent != EXPERIMENTS.resolve() or out.name != args.operation_id:
        raise ValueError("OUTPUT_PATH_OUTSIDE_OPERATION_ROOT")
    for item in [request["audit"], request["inventory"], request["lod"], request["checkpoint"],
                 request["art_standard"], *request["implementation"]]:
        if digest_file(item["path"]) != item["sha256"]:
            raise ValueError("BOUND_INPUT_SHA_DRIFT: " + str(item["path"]))
    blob = native_blob(request["lod"]["path"], request["identity"])
    if digest_bytes(blob) != request["def_sha256"]:
        raise ValueError("DEF_SHA_DRIFT")
    raw_slots = {(r["group_id"], r["frame_index"]): r for r in parse_def(blob)["slots"]}
    checkpoint = read_bound(request["checkpoint"]["path"], request["checkpoint"]["sha256"])
    audit = read_bound(request["audit"]["path"], request["audit"]["sha256"])
    audit_slots = {(r["key"][2], r["key"][3]): r for r in audit["native_slots"]}
    snapshots = {Path(x["path"]).name: x for x in audit["source_snapshot"]}
    if len(raw_slots) != len(request["slots"]):
        raise ValueError("DEF_SLOT_COUNT_DRIFT")
    refs, masters, seen = set(), set(), set()
    candidate_overrides = {tuple(x["key"]): x for x in request.get("candidate_overrides", [])}
    candidate_root = None
    if candidate_overrides:
        candidate_set_ref = request.get("candidate_set")
        if not candidate_set_ref:
            raise ValueError("CANDIDATE_SET_BINDING_REQUIRED")
        candidate_doc = read_bound(candidate_set_ref["path"], candidate_set_ref["sha256"])
        if (candidate_doc.get("schema") != "h3.creature.candidate-set.v2" or
                candidate_doc.get("identity") != request["identity"]):
            raise ValueError("CANDIDATE_SET_SCHEMA_OR_IDENTITY_INVALID")
        candidate_operation_id = candidate_doc.get("candidate_operation_id")
        if not isinstance(candidate_operation_id, str):
            raise ValueError("CANDIDATE_OPERATION_ID_REQUIRED")
        ensure_id(candidate_operation_id)
        candidate_root = Path(candidate_doc.get("candidate_root", "")).resolve(strict=True)
        expected_candidate_root = (CREATURE_MASTER_SET_ROOT / candidate_operation_id).resolve(strict=True)
        if (candidate_root != expected_candidate_root or
                candidate_root.parent.resolve() != CREATURE_MASTER_SET_ROOT.resolve()):
            raise ValueError("CANDIDATE_OPERATION_ROOT_INVALID")
        candidate_owners = {tuple(x["key"]): x for x in candidate_doc.get("owners", [])}
        for override in candidate_overrides.values():
            owner = candidate_owners.get(tuple(override.get("owner_key", [])))
            if (not owner or Path(owner["path"]).resolve(strict=True) != Path(override["path"]).resolve(strict=True)
                    or owner["sha256"] != override["sha256"]):
                raise ValueError("CANDIDATE_OWNER_BINDING_DRIFT")
    for row in request["slots"]:
        key = (row["group"], row["frame"])
        if key in seen or key not in audit_slots:
            raise ValueError("DUPLICATE_OR_UNFROZEN_SLOT")
        seen.add(key)
        source = raw_slots.get(key)
        expected = {"geometry": row["geometry"], "resource": row["resource"]}
        if (source is None or source["offset"] != row["offset"] or not same_geometry(source, expected)
                or row["geometry"] != audit_slots[key]["geometry"]
                or row["resource"].lower() != audit_slots[key]["resource"].lower()):
            raise ValueError("DEF_SLOT_DRIFT")
        ref = row["reference"]
        if not reference_matches_offset(ref, row, raw_slots, checkpoint, request["identity"]):
            raise ValueError("REFERENCE_OFFSET_OR_CHECKPOINT_DRIFT")
        if ref["path"] not in refs:
            if digest_file(ref["path"]) != ref["sha256"]:
                raise ValueError("REFERENCE_SHA_DRIFT")
            refs.add(ref["path"])
        master = row["master"]
        official_master = snapshots.get(f"{row['group']}_{row['frame']}.png")
        if master != official_master:
            override = candidate_overrides.get(("def", request["identity"], row["group"], row["frame"]))
            if not override or master != {"path": override["path"], "sha256": override["sha256"],
                                        "bytes": Path(override["path"]).stat().st_size}:
                raise ValueError("MASTER_SNAPSHOT_OR_CANDIDATE_BINDING_DRIFT")
            override_path = Path(override["path"]).resolve(strict=True)
            if candidate_root is None or override_path.parent != candidate_root:
                raise ValueError("CANDIDATE_OUTSIDE_MASTER_SET_OPERATION")
            with Image.open(override_path) as image:
                if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("CANDIDATE_SINGLE_RGBA_PNG_REQUIRED")
                image.load()
        if master and master["path"] not in masters:
            if digest_file(master["path"]) != master["sha256"]:
                raise ValueError("MASTER_SHA_DRIFT")
            masters.add(master["path"])
    return request, out


def derive_candidates(args):
    """Create a new review request with candidates bound by native offset class."""
    ensure_id(args.operation_id)
    parent_path = Path(args.manifest).resolve(strict=True)
    parent = read_bound(parent_path, args.manifest_sha256)
    candidate_path = Path(args.candidates).resolve(strict=True)
    candidate_doc = read_bound(candidate_path, args.candidates_sha256)
    if candidate_doc.get("schema") != "h3.creature.candidate-set.v2":
        raise ValueError("CANDIDATE_SET_SCHEMA_V2_REQUIRED")
    if (Path(candidate_doc.get("parent_request", "")).resolve() != parent_path or
            candidate_doc.get("parent_request_sha256") != args.manifest_sha256.lower()):
        raise ValueError("CANDIDATE_SET_PARENT_REQUEST_DRIFT")
    if candidate_doc.get("identity") != parent["identity"]:
        raise ValueError("CANDIDATE_SET_IDENTITY_MISMATCH")
    candidate_operation_id = candidate_doc.get("candidate_operation_id")
    if not isinstance(candidate_operation_id, str):
        raise ValueError("CANDIDATE_OPERATION_ID_REQUIRED")
    ensure_id(candidate_operation_id)
    candidate_root = Path(candidate_doc.get("candidate_root", "")).resolve(strict=True)
    expected_candidate_root = (CREATURE_MASTER_SET_ROOT / candidate_operation_id).resolve(strict=True)
    if (candidate_root != expected_candidate_root or
            candidate_root.parent.resolve() != CREATURE_MASTER_SET_ROOT.resolve()):
        raise ValueError("CANDIDATE_OPERATION_ROOT_INVALID")
    blob = native_blob(parent["lod"]["path"], parent["identity"])
    native = {(s["group_id"], s["frame_index"]): s for s in parse_def(blob)["slots"]}
    requested = {}
    for item in candidate_doc.get("owners", []):
        key = tuple(item.get("key", []))
        if len(key) != 4 or key[:2] != ("def", parent["identity"]):
            raise ValueError("CANDIDATE_OWNER_KEY_INVALID")
        slot = native.get((key[2], key[3]))
        if not slot or item.get("native_offset") != slot["offset"]:
            raise ValueError("CANDIDATE_OWNER_OFFSET_MISMATCH")
        path = Path(item["path"]).resolve(strict=True)
        if (path.parent != candidate_root or not path.is_relative_to(candidate_root) or
                digest_file(path) != item.get("sha256")):
            raise ValueError("CANDIDATE_PATH_OR_SHA_INVALID")
        with Image.open(path) as image:
            if image.format != "PNG" or image.mode != "RGBA" or getattr(image, "n_frames", 1) != 1:
                raise ValueError("CANDIDATE_SINGLE_RGBA_PNG_REQUIRED")
        requested[slot["offset"]] = {"owner_key": list(key), "path": str(path),
                                     "sha256": item["sha256"].lower(),
                                     "sampling_K": item.get("sampling_K")}
    derived = json.loads(json.dumps(parent))
    derived["operation_id"] = args.operation_id
    derived["parent_request"] = bind(parent_path)
    derived["candidate_set"] = bind(candidate_path)
    applied = []
    for row in derived["slots"]:
        offset = row["offset"]
        candidate = requested.get(offset)
        if not candidate:
            continue
        alias_path = Path(candidate["path"])
        row["master"] = {"path": str(alias_path), "sha256": candidate["sha256"],
                         "bytes": alias_path.stat().st_size}
        row["candidate_owner_key"] = candidate["owner_key"]
        row["candidate_sampling_K"] = candidate["sampling_K"]
        applied.append(["def", derived["identity"], row["group"], row["frame"]])
    if not applied:
        raise ValueError("CANDIDATE_SET_HAS_NO_REQUEST_SLOTS")
    derived["candidate_overrides"] = [
        {"key": key, "path": row["master"]["path"], "sha256": row["master"]["sha256"],
         "owner_key": row["candidate_owner_key"], "native_offset": row["offset"],
         "sampling_K": row["candidate_sampling_K"]}
        for key in applied for row in derived["slots"]
        if ["def", derived["identity"], row["group"], row["frame"]] == key]
    derived["candidate_review_only"] = True
    out = Path(args.out).resolve()
    if out.parent != REQUESTS.resolve() or out.name != args.operation_id + ".json":
        raise ValueError("REQUEST_PATH_OUTSIDE_OPERATION_ROOT")
    with out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(derived, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "CANDIDATE_REVIEW_REQUEST_DERIVED", "manifest": str(out),
            "sha256": digest_file(out), "candidate_owner_classes": len(requested),
            "logical_slots_overridden": len(applied), "art_approved": False}


def common_box(request):
    boxes = []
    seen = set()
    for row in request["slots"]:
        ref = row["reference"]["path"]
        if ref in seen:
            continue
        seen.add(ref)
        with Image.open(ref) as im:
            box = im.convert("RGBA").getchannel("A").getbbox()
            if box:
                boxes.append(box)
    if not boxes:
        raise ValueError("EMPTY_OFFICIAL_REFERENCES")
    width, height = request["slots"][0]["geometry"]["canvas_1x"]
    if any(r["geometry"]["canvas_1x"] != [width, height] for r in request["slots"]):
        raise ValueError("MULTIPLE_NATIVE_CANVAS_SIZES")
    return [max(0, min(b[0] for b in boxes)-5), max(0, min(b[1] for b in boxes)-5),
            min(width, max(b[2] for b in boxes)+5), min(height, max(b[3] for b in boxes)+5)]


def guide_image(row, box):
    with Image.open(row["reference"]["path"]) as src:
        ref = src.convert("RGBA")
    # Grid and silhouette are visualization aids in a fixed native coordinate
    # system, not landmarks, an art master, or a measurement.
    grid = Image.new("RGBA", ref.size, "#eef3fa")
    dr = ImageDraw.Draw(grid)
    for x in range(0, ref.width, 10):
        dr.line((x, 0, x, ref.height), fill=(202, 213, 227, 255))
    for y in range(0, ref.height, 10):
        dr.line((0, y, ref.width, y), fill=(202, 213, 227, 255))
    grid.alpha_composite(ref)
    mask = ref.getchannel("A").point(lambda x: 255 if x >= 200 else 0)
    edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3)))
    tint = Image.new("RGBA", ref.size, (215, 50, 50, 255))
    grid.paste(tint, (0, 0), edge)
    guide = grid.crop(tuple(box))
    return guide.resize((guide.width * 2, guide.height * 2), Image.Resampling.NEAREST).convert("RGB")


def png_bytes(im):
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def apng_bytes(frames, duration_ms=125):
    if not frames:
        raise ValueError("REGISTERED_SEQUENCE_EMPTY")
    out = io.BytesIO()
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:],
                    duration=[duration_ms]*len(frames), loop=0, disposal=[1]*len(frames),
                    blend=[0]*len(frames))
    return out.getvalue()


def path_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def native_root_step(previous, current, tolerance_native=1.0, same_pose=True):
    if previous is None:
        return None, "FIRST_FRAME"
    delta = [current[0]-previous[0], current[1]-previous[1]]
    if not same_pose:
        return delta, "POSE_BOUNDARY_TRANSITION_REVIEW_REQUIRED"
    if abs(delta[1]) > tolerance_native:
        return delta, "VERTICAL_STEP_REVIEW_REQUIRED"
    return delta, "WITHIN_VERTICAL_TOLERANCE"


def point_near_visible_subject(alpha, point, radius=5, minimum_alpha=8):
    x, y = point
    left, top = max(0, x-radius), max(0, y-radius)
    right, bottom = min(alpha.width, x+radius+1), min(alpha.height, y+radius+1)
    return alpha.crop((left, top, right, bottom)).getextrema()[1] >= minimum_alpha


def anatomy_metrics_for_group(rows, bykey, common_k):
    """Measure provisional paths once per native offset owner; retain aliases."""
    groups = defaultdict(dict)
    alias_owner_signature = {}
    alias_owner_mark = {}
    for entry in rows:
        row = entry[0]
        key = ("def", row.get("identity", ""), row["group"], row["frame"])
        # Callers may use a request without an identity field in unit fixtures;
        # the annotation key still resolves by group/frame below.
        mark = bykey.get(key)
        if mark is None:
            mark = next((m for k, m in bykey.items() if k[-2:] == (row["group"], row["frame"])), None)
        if mark is None:
            raise ValueError("ANATOMY_MARK_MISSING")
        signature = (mark.get("pose_group"), mark.get("direction"))
        offset = row.get("offset", (row["group"], row["frame"]))
        if offset in alias_owner_signature and alias_owner_signature[offset] != signature:
            raise ValueError("NATIVE_ALIAS_POSE_GROUP_MISMATCH")
        alias_owner_signature[offset] = signature
        prior_mark = alias_owner_mark.get(offset)
        if prior_mark and (prior_mark["image_sha256"] != mark["image_sha256"] or
                           prior_mark["root_anchor_pixels"] != mark["root_anchor_pixels"] or
                           prior_mark["paths"] != mark["paths"]):
            raise ValueError("NATIVE_ALIAS_LANDMARKS_MUST_MATCH_OWNER")
        alias_owner_mark[offset] = mark
        if mark.get("measurement_definitions") != MEASUREMENT_DEFINITIONS:
            raise ValueError("ANATOMICAL_METRIC_DEFINITIONS_INVALID")
        lengths = {name: path_length(mark["paths"][name])/common_k for name in MEASUREMENT_DEFINITIONS}
        leg_left, leg_right = mark["paths"].get("leg_left"), mark["paths"].get("leg_right")
        if (leg_left is None) != (leg_right is None):
            raise ValueError("BIPED_LEG_PATHS_REQUIRE_PAIR")
        if leg_left is not None:
            lengths["leg_left"] = path_length(leg_left)/common_k
            lengths["leg_right"] = path_length(leg_right)/common_k
            lengths["legs_mean"] = (lengths["leg_left"]+lengths["leg_right"])/2
        groups[signature].setdefault(offset, {"mark": mark, "lengths": lengths})
        existing = groups[signature][offset]
        if existing["mark"]["image_sha256"] != mark["image_sha256"] or existing["mark"]["root_anchor_pixels"] != mark["root_anchor_pixels"] or existing["mark"]["paths"] != mark["paths"]:
            raise ValueError("NATIVE_ALIAS_LANDMARKS_MUST_MATCH_OWNER")
    result = {}
    for signature, owners in groups.items():
        owner_rows = list(owners.items())
        metric_names = sorted(owner_rows[0][1]["lengths"])
        if any(set(value["lengths"]) != set(metric_names) for _, value in owner_rows):
            raise ValueError("ANATOMICAL_METRIC_SET_MISMATCH_WITHIN_POSE_GROUP")
        medians = {name: sorted(value["lengths"][name] for _, value in owner_rows)[(len(owner_rows)-1)//2]
                   for name in metric_names}
        slots = {}
        for _offset, value in owner_rows:
            mark, lengths = value["mark"], value["lengths"]
            frame = mark["key"][-2:]
            if len(owner_rows) < 2:
                deviations = {name: None for name in lengths}
                max_deviation = None
                status = "NOT_COMPARABLE_SINGLE_OWNER"
            else:
                deviations = {name: (length/medians[name]-1)*100 if medians[name] else None
                              for name, length in lengths.items()}
                max_deviation = max(abs(v) for v in deviations.values() if v is not None)
                status = ("PROVISIONAL_REVIEW_OVER_8_PERCENT" if max_deviation > 8 else
                          "PROVISIONAL_REVIEW_5_TO_8_PERCENT" if max_deviation > 5 else
                          "PROVISIONAL_WITHIN_5_PERCENT")
            slots[f"{frame[0]}_{frame[1]}"] = {"native_lengths": lengths,
                "deviation_percent": deviations,
                "max_abs_deviation_percent": round(max_deviation,3) if max_deviation is not None else None,
                "status": status}
        result[f"{signature[0]}:{signature[1]}"] = {"slots": slots,"medians_native": medians,
            "unique_offset_owner_count": len(owner_rows),"aliases_excluded_from_median": True,
            "comparison_evidence_sufficient": len(owner_rows) >= 2,
            "measurement_author": "assistant_or_human_annotation",
            "approval": "NOT_GRANTED_BY_PREVIEW"}
    return result


def validate_limb_review(annotation, mark, image_sha):
    """Require a source-bound visual appendage count before sequence preview."""
    contract = annotation.get("appendage_contract")
    review = mark.get("limb_review")
    if not isinstance(contract, dict) or contract.get("schema") != "h3.creature.appendage-contract.v1":
        raise ValueError("APPENDAGE_CONTRACT_REQUIRED")
    expected = contract.get("expected_counts")
    connections = contract.get("required_connections")
    if (not isinstance(expected, dict) or not expected or
            not isinstance(connections, list) or not connections):
        raise ValueError("APPENDAGE_CONTRACT_INCOMPLETE")
    if (not isinstance(review, dict) or review.get("source_sha256") != image_sha or
            review.get("reviewer") not in ("assistant", "human") or
            review.get("status") != "VISUALLY_REVIEWED"):
        raise ValueError("APPENDAGE_VISUAL_REVIEW_REQUIRED")
    if review.get("counts") != expected:
        raise ValueError("APPENDAGE_COUNT_MISMATCH")
    observed_connections = review.get("connections")
    if (not isinstance(observed_connections, dict) or
            set(observed_connections) != set(connections) or
            any(value not in ("CONNECTED", "OCCLUDED_CONTINUITY_VISUALLY_CONFIRMED")
                for value in observed_connections.values())):
        raise ValueError("APPENDAGE_CONNECTIVITY_REVIEW_FAILED")
    return {"counts": expected, "connections": observed_connections,
            "status": "VISUAL_REVIEW_RECORDED_NOT_HUMAN_APPROVED"}


def render_outputs(request):
    box = common_box(request)
    groups = defaultdict(list)
    generated = {}
    registered_roots = {}
    for row in sorted(request["slots"], key=lambda x: (x["group"], x["frame"])):
        image = guide_image(row, box)
        name = f"guides/{row['group']}_{row['frame']}.png"
        generated[name] = png_bytes(image)
        groups[row["group"]].append((row, image))
    for group, items in groups.items():
        width = max(img.width for _, img in items)
        height = max(img.height for _, img in items)
        sheet = Image.new("RGB", (width * len(items), height + 22), "#eef3fa")
        draw = ImageDraw.Draw(sheet)
        for i, (row, img) in enumerate(items):
            draw.text((i * width + 3, 3), f"{group}_{row['frame']} native 1X x2", fill="#202020")
            sheet.paste(img, (i * width, 22))
        generated[f"groups/{group}.png"] = png_bytes(sheet)
    return box, generated


def render_registered_layout(request, annotations, common_k):
    """Create a human-annotated, common-K fixed-origin sequence review.

    This is still a review artifact. It does not sample a 3X asset or accept art.
    """
    if not isinstance(common_k, (int, float)) or not math.isfinite(common_k) or common_k < 3:
        raise ValueError("COMMON_K_MUST_MEET_FORMAL_3X_CAPACITY")
    if (annotations.get("status") != "VISUALLY_REVIEWED"
            or annotations.get("annotation_author") not in ("assistant", "human")):
        raise ValueError("VISUALLY_REVIEWED_SHA_BOUND_LANDMARKS_REQUIRED")
    bykey = {tuple(row["key"]): row for row in annotations["frames"]}
    if not bykey:
        raise ValueError("ANNOTATION_FRAME_COVERAGE_EMPTY")
    offset_reviews = {}
    groups = defaultdict(list)
    for row in request["slots"]:
        key = ("def", request["identity"], row["group"], row["frame"])
        mark = bykey.get(key)
        if not mark or mark.get("image_sha256") != (row.get("master") or {}).get("sha256"):
            raise ValueError("HUMAN_LANDMARK_SHA_OR_SLOT_DRIFT")
        limb_review = validate_limb_review(annotations, mark, row["master"]["sha256"])
        if mark.get("measurement_definitions") != MEASUREMENT_DEFINITIONS:
            raise ValueError("ANATOMICAL_METRIC_DEFINITIONS_INVALID")
        source_path = Path(row["master"]["path"])
        with Image.open(source_path) as source_image:
            source_image = source_image.convert("RGBA")
            source_width, source_height = source_image.size
            source_alpha = source_image.getchannel("A")
        root_point = mark.get("root_anchor_pixels")
        if (not isinstance(root_point, list) or len(root_point) != 2 or
                any(type(v) is not int for v in root_point) or
                not (0 <= root_point[0] < source_width and 0 <= root_point[1] < source_height)):
            raise ValueError("ROOT_ANNOTATION_OUTSIDE_SHA_BOUND_SOURCE")
        for path_name, line in mark.get("paths", {}).items():
            if (not isinstance(line, list) or len(line) < 2 or
                    any(not isinstance(point, list) or len(point) != 2 or
                        any(type(v) is not int for v in point) or
                        not (0 <= point[0] < source_width and 0 <= point[1] < source_height)
                        for point in line)):
                raise ValueError("ANATOMICAL_PATH_OUTSIDE_SHA_BOUND_SOURCE: " + path_name)
            if any(not point_near_visible_subject(source_alpha, point) for point in line):
                raise ValueError("ANATOMICAL_POINT_NOT_ON_VISIBLE_SUBJECT: " + path_name)
        offset = row.get("offset", (row["group"], row["frame"]))
        alias_signature = (mark["image_sha256"], mark.get("root_anchor_pixels"), mark.get("paths"),
                           mark.get("measurement_definitions"), limb_review)
        if offset in offset_reviews and offset_reviews[offset] != alias_signature:
            raise ValueError("NATIVE_ALIAS_VISUAL_REVIEW_MUST_MATCH_OWNER_ACROSS_GROUPS")
        offset_reviews[offset] = alias_signature
        root = mark["root_anchor_pixels"]
        expected_paths = list(MEASUREMENT_DEFINITIONS)
        for name in expected_paths:
            line = mark.get("paths", {}).get(name)
            if not isinstance(line, list) or len(line) < 2:
                raise ValueError("ANATOMICAL_PATH_MISSING: " + name)
        groups[row["group"]].append((row, root, limb_review))
    generated = {}
    registered_roots = {}
    anatomy_metrics = {}
    for group, rows in groups.items():
        w, h = rows[0][0]["geometry"]["canvas_1x"]
        native_roots = {f"{row['group']}_{row['frame']}": [root[0]/common_k, root[1]/common_k]
                        for row, root, _limb in rows}
        prepared = []
        feasible_x, feasible_y = [], []
        for row, _root, _limb in rows:
            image = Image.open(row["master"]["path"]).convert("RGBA")
            alpha_box = image.getchannel("A").getbbox()
            if not alpha_box:
                raise ValueError("REGISTERED_MASTER_EMPTY_ALPHA")
            key = f"{row['group']}_{row['frame']}"
            root_native = native_roots[key]
            feasible_x.append((root_native[0]-alpha_box[0]/common_k,
                               root_native[0]+w-alpha_box[2]/common_k))
            feasible_y.append((root_native[1]-alpha_box[1]/common_k,
                               root_native[1]+h-alpha_box[3]/common_k))
            prepared.append((image, alpha_box))
        x_low, x_high = max(x[0] for x in feasible_x), min(x[1] for x in feasible_x)
        y_low, y_high = max(y[0] for y in feasible_y), min(y[1] for y in feasible_y)
        if x_low > x_high or y_low > y_high:
            raise ValueError("NO_COMMON_ROOT_ANCHOR_WITHOUT_ALPHA_CROP")
        median_x = sorted(root[0] for root in native_roots.values())[(len(rows)-1)//2]
        median_y = sorted(root[1] for root in native_roots.values())[(len(rows)-1)//2]
        root_target = [min(max(median_x, x_low), x_high), min(max(median_y, y_low), y_high)]
        registered_roots[str(group)] = {
            f"{row['group']}_{row['frame']}": {
                "source_root_pixels": root,
                "source_to_native_K": common_k,
                "source_root_native": native_roots[f"{row['group']}_{row['frame']}"],
                "registered_root_native": root_target,
                "translation_native": [root_target[0]-native_roots[f"{row['group']}_{row['frame']}"][0],
                                        root_target[1]-native_roots[f"{row['group']}_{row['frame']}"][1]],
            }
            for row, root, _limb in rows
        }
        previous_native_root = None
        previous_pose = None
        for row, _root, _limb in rows:
            key = f"{row['group']}_{row['frame']}"
            current = native_roots[key]
            mark = bykey[("def", request["identity"], row["group"], row["frame"])]
            pose_signature = (mark.get("pose_group"), mark.get("direction"))
            delta, step_status = native_root_step(previous_native_root, current,
                                                  same_pose=(previous_pose is None or pose_signature == previous_pose))
            registered_roots[str(group)][key]["source_root_delta_from_previous_native"] = delta
            registered_roots[str(group)][key]["source_vertical_step_status"] = step_status
            previous_native_root = current
            previous_pose = pose_signature
        registered_roots[str(group)]["limb_reviews"] = {
            f"{row['group']}_{row['frame']}": limb
            for row, _root, limb in rows
        }
        anatomy_metrics[str(group)] = anatomy_metrics_for_group(rows, bykey, common_k)
        display_scale = 2
        sheet = Image.new("RGBA", (w * display_scale * len(rows), h * display_scale), "#eef3fa")
        sequence_frames = []
        for i, ((row, root, _limb), (source_image, alpha_box)) in enumerate(zip(rows, prepared)):
            slot_key = f"{row['group']}_{row['frame']}"
            native_root = native_roots[slot_key]
            dx = round((root_target[0]-native_root[0]) * display_scale)
            dy = round((root_target[1]-native_root[1]) * display_scale)
            x = i * w * display_scale + dx
            y = dy
            a = common_k / display_scale
            c = root[0] - common_k * root_target[0]
            f = root[1] - common_k * root_target[1]
            native = source_image.transform((w*display_scale,h*display_scale), Image.Transform.AFFINE,
                (a,0,c,0,a,f), resample=Image.Resampling.BICUBIC, fillcolor=(0,0,0,0))
            alpha_box_out = native.getchannel("A").getbbox()
            if alpha_box_out is None:
                raise ValueError("REGISTERED_TRANSFORM_EMPTY")
            left, top, right, bottom = alpha_box_out
            if left < 0 or top < 0 or right > w*display_scale or bottom > h*display_scale:
                raise ValueError("REGISTERED_POSITIVE_ALPHA_OUT_OF_CANVAS")
            sheet.paste(native, (i*w*display_scale, 0))
            frame = Image.new("RGBA", (w*display_scale, h*display_scale), "#eef3fa")
            frame.alpha_composite(native)
            sequence_frames.append(frame.convert("RGB"))
            generated[f"frames/group-{group:02d}/{slot_key}.png"] = png_bytes(frame.convert("RGB"))
            path = f"registered/group-{group:02d}-K{common_k}-REVIEW.png"
        generated[path] = png_bytes(sheet.convert("RGB"))
        generated[f"sequence/group-{group:02d}-K{common_k}-REVIEW.apng"] = apng_bytes(sequence_frames)
    return generated, registered_roots, anatomy_metrics


def run(request, out):
    if out.exists():
        raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
    box, outputs = render_outputs(request)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for rel, data in sorted(outputs.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        records.append({"path": rel, "sha256": digest_bytes(data), "bytes": len(data)})
    receipt = {"schema": "h3.creature.native-pose-guide.receipt.v1", "operation_id": request["operation_id"],
               "identity": request["identity"], "native_window_1x": box, "display_scale": 2,
               "guides": len(request["slots"]), "groups": len({s["group"] for s in request["slots"]}),
               "outputs": records, "body_root": "NOT_MEASURED", "landmarks": "NOT_MEASURED",
               "master_K": "NOT_MEASURED", "review_only": True, "art_approved": False,
               "source_images_modified": False}
    with (out / "receipt.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "REVIEW_GUIDES_CREATED", "receipt": str(out / "receipt.json"),
            "receipt_sha256": digest_file(out / "receipt.json"), "guides": receipt["guides"],
            "groups": receipt["groups"], "root_and_landmarks": "NOT_MEASURED"}


def prepare_layout(args):
    request, _ = validate(args)
    annotation_path = Path(args.landmarks).resolve(strict=True)
    if digest_file(annotation_path) != args.landmarks_sha256.lower():
        raise ValueError("LANDMARK_FILE_SHA_DRIFT")
    annotations = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
    if annotations.get("schema") != LANDMARK_SCHEMA or annotations.get("annotation_author") not in ("assistant", "human"):
        raise ValueError("VISUALLY_REVIEWED_SHA_BOUND_LANDMARKS_REQUIRED")
    if annotations.get("status") != "VISUALLY_REVIEWED":
        raise ValueError("VISUAL_REVIEW_STATUS_REQUIRED")
    selected_groups = set(args.group or sorted({row["group"] for row in request["slots"]}))
    selected_slots = [row for row in request["slots"] if row["group"] in selected_groups]
    expected = {("def", request["identity"], row["group"], row["frame"]): row for row in selected_slots}
    provided = {tuple(row.get("key", [])): row for row in annotations.get("frames", [])}
    if set(provided) != set(expected):
        raise ValueError("LANDMARK_LOGICAL_SLOT_COVERAGE_MISMATCH")
    for key, mark in provided.items():
        source = expected[key].get("master")
        if not source or digest_file(source["path"]) != mark.get("image_sha256"):
            raise ValueError("LANDMARK_MASTER_SHA_DRIFT")
        if mark.get("coordinate_space") != "source_pixels" or mark.get("measurement_scope") != "whole_subject":
            raise ValueError("LANDMARK_COORDINATE_SCOPE_INVALID")
        w, h = Image.open(source["path"]).size
        for x, y in [mark.get("root_anchor_pixels", [])] + sum(mark.get("paths", {}).values(), []):
            if type(x) is not int or type(y) is not int or not 0 <= x < w or not 0 <= y < h:
                raise ValueError("LANDMARK_POINT_OUTSIDE_SHA_BOUND_SOURCE")
    out = Path(args.outdir).resolve()
    if out.parent != EXPERIMENTS.resolve() or out.name != args.operation_id:
        raise ValueError("OUTPUT_PATH_OUTSIDE_OPERATION_ROOT")
    scoped_request = {**request, "slots": selected_slots}
    scoped_annotations = {**annotations, "frames": [row for row in annotations["frames"]
                                                     if tuple(row["key"]) in expected]}
    products, registered_roots, anatomy_metrics = render_registered_layout(scoped_request, scoped_annotations, args.common_k)
    outputs = []
    for relative, data in sorted(products.items()):
        outputs.append({"path": relative, "sha256": digest_bytes(data), "bytes": len(data)})
    receipt = {"schema": "h3.creature.registered-sequence-preview.v1",
               "operation_id": args.operation_id, "request": bind(args.manifest),
               "selected_groups": sorted(selected_groups),
               "landmarks": bind(annotation_path), "common_K": args.common_k,
               "pose_scale_group": "preserved separately per annotation; no cross-group normalization",
               "registration": "human root anchor, native-canvas 1X display, body root translated without rotation",
               "registered_roots": registered_roots, "outputs": outputs,
               "anatomy_metrics": anatomy_metrics,
               "3X": "NOT_CREATED", "art_approved": False,
               "human_annotation": True, "source_images_modified": False}
    return out, products, receipt


def layout_check(args):
    out, products, receipt = prepare_layout(args)
    if out.exists():
        raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
    return {"status": "READY_REVIEW_ONLY", "operation_id": args.operation_id,
            "selected_groups": receipt["selected_groups"], "frames": sum(
                len(rows)-1 for rows in receipt["registered_roots"].values()),
            "outputs": len(products), "common_K": args.common_k, "art_approved": False}


def layout_run(args):
    out, products, receipt = prepare_layout(args)
    if out.exists():
        raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
    out.mkdir(parents=True, exist_ok=False)
    for relative, data in sorted(products.items()):
        dest = out / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream:
            stream.write(data)
    receipt_path = out / "receipt.json"
    with receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "REVIEW_SEQUENCE_CREATED", "operation_id": args.operation_id,
            "receipt": str(receipt_path), "receipt_sha256": digest_file(receipt_path),
            "selected_groups": receipt["selected_groups"], "outputs": len(products),
            "common_K": args.common_k, "art_approved": False}


def layout_verify(args):
    out, products, expected_receipt = prepare_layout(args)
    receipt_path = out / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if receipt != expected_receipt:
        raise ValueError("RECEIPT_REPLAY_MISMATCH")
    for relative, data in products.items():
        path = (out / relative).resolve()
        if not path.is_relative_to(out) or not path.is_file():
            raise ValueError("OUTPUT_PATH_INVALID")
        if digest_bytes(data) != digest_file(path):
            raise ValueError("OUTPUT_REPLAY_MISMATCH: " + relative)
        if relative.endswith(".apng"):
            with Image.open(path) as image:
                group_token = relative.split("group-", 1)[1].split("-", 1)[0]
                group_key = str(int(group_token))
                expected_frames = len([f for f in receipt["registered_roots"][group_key].keys()
                                       if f != "limb_reviews"])
                if getattr(image, "n_frames", 1) != expected_frames:
                    raise ValueError("APNG_FRAME_COUNT_MISMATCH")
    return {"status": "VERIFIED_REVIEW_ONLY", "operation_id": args.operation_id,
            "selected_groups": receipt["selected_groups"], "outputs": len(products),
            "receipt_sha256": digest_file(receipt_path), "art_approved": False}


def verify(request, out):
    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("schema") != "h3.creature.native-pose-guide.receipt.v1" or receipt.get("operation_id") != request["operation_id"]:
        raise ValueError("RECEIPT_INVALID")
    box, outputs = render_outputs(request)
    expected = {r["path"]: r for r in receipt["outputs"]}
    if box != receipt["native_window_1x"] or set(expected) != set(outputs):
        raise ValueError("OUTPUT_SET_DRIFT")
    for rel, data in outputs.items():
        path = (out / rel).resolve()
        if not path.is_relative_to(out) or not path.is_file():
            raise ValueError("OUTPUT_PATH_INVALID")
        if digest_bytes(data) != expected[rel]["sha256"] or digest_file(path) != expected[rel]["sha256"]:
            raise ValueError("OUTPUT_REPLAY_MISMATCH: " + rel)
    return {"status": "VERIFIED_REVIEW_ONLY", "slots": len(request["slots"]),
            "outputs": len(outputs), "receipt_sha256": digest_file(out / "receipt.json"),
            "art_approved": False, "root_and_landmarks": "NOT_MEASURED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    for name in ("audit-manifest", "audit-sha256", "lod", "lod-sha256", "checkpoint", "checkpoint-sha256",
                 "refs-dir", "art-standard", "art-standard-sha256", "operation-id", "out"):
        f.add_argument("--" + name, required=True)
    dc = sub.add_parser("derive-candidates")
    for name in ("manifest", "manifest-sha256", "candidates", "candidates-sha256", "operation-id", "out"):
        dc.add_argument("--" + name, required=True)
    for action in ("check", "run", "verify"):
        p = sub.add_parser(action)
        for name in ("manifest", "manifest-sha256", "outdir", "operation-id"):
            p.add_argument("--" + name, required=True)
    for action in ("layout-check", "layout-run", "layout-verify", "layout-preview"):
        lp = sub.add_parser(action)
        for name in ("manifest", "manifest-sha256", "landmarks", "landmarks-sha256", "outdir", "operation-id"):
            lp.add_argument("--" + name, required=True)
        lp.add_argument("--common-k", type=float, required=True)
        lp.add_argument("--group", type=int, action="append",
                        help="Optional action group subset; input request remains fully SHA-validated")
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            result = freeze(args)
        elif args.action == "derive-candidates":
            result = derive_candidates(args)
        elif args.action == "layout-check":
            result = layout_check(args)
        elif args.action in ("layout-run", "layout-preview"):
            result = layout_run(args)
        elif args.action == "layout-verify":
            result = layout_verify(args)
        else:
            request, out = validate(args)
            if args.action == "check":
                if out.exists():
                    raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
                result = {"status": "READY_REVIEW_ONLY", "slots": len(request["slots"]),
                          "root_and_landmarks": "NOT_MEASURED"}
            elif args.action == "run":
                result = run(request, out)
            else:
                result = verify(request, out)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, StopIteration) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
