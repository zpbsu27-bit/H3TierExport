"""SHA-bound local human-pose candidates and explicit relative registration previews.

This is a review aid. It never changes a source, infers a native ground anchor,
passes anatomical accuracy, or writes an art master or a 3X game asset.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

import pose_feature_registration
from tool_paths import resolve_tool_root

PRODUCT = resolve_tool_root(__file__)
PROVIDER = PRODUCT / "build" / "creature-auto-pose-provider"
MODEL_SHA = "5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c"
MODEL_SIZE = (192, 256)
JOINTS = ("nose", "left_eye", "right_eye", "left_ear", "right_ear",
          "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
          "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
          "right_knee", "left_ankle", "right_ankle")
FLIP_INDEX = (0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15)
EDGES = ((5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11),
         (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16))
REQUIRED_BODY = ("left_shoulder", "right_shoulder", "left_hip", "right_hip",
                 "left_knee", "right_knee", "left_ankle", "right_ankle")
MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)
OP_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z")
SLOT = re.compile(r"(0|[1-9][0-9]*)_(0|[1-9][0-9]*)\.png\Z", re.I)


def need(condition, code):
    if not condition:
        raise ValueError(code)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        result = json.load(stream)
    need(isinstance(result, dict), "JSON_OBJECT_REQUIRED")
    return result


def pinned(ref):
    need(isinstance(ref, dict) and isinstance(ref.get("path"), str)
         and isinstance(ref.get("sha256"), str), "PIN_REQUIRED")
    p = Path(ref["path"])
    need(p.is_absolute() and p.is_file(), "PIN_FILE_MISSING")
    need(sha(p) == ref["sha256"].lower(), "PIN_SHA_DRIFT:" + str(p))
    return p.resolve()


def load_provider():
    config_path = PROVIDER / "provider.json"
    config = read_json(config_path)
    need(config.get("schema") == "h3.creature.auto-pose-provider.v1", "PROVIDER_SCHEMA_INVALID")
    need(config.get("model") == "rtmpose-human-body7.onnx"
         and config.get("model_sha256") == MODEL_SHA, "PROVIDER_MODEL_NOT_SUPPORTED")
    model = PROVIDER / config["model"]
    need(model.is_file() and sha(model) == MODEL_SHA, "PROVIDER_MODEL_SHA_DRIFT")
    import onnxruntime as ort
    need(ort.__version__ == config.get("onnxruntime"), "PROVIDER_RUNTIME_VERSION_DRIFT")
    need("CPUExecutionProvider" in ort.get_available_providers(), "PROVIDER_CPU_UNAVAILABLE")
    return {"path": str(config_path.resolve()), "sha256": sha(config_path),
            "model_path": str(model.resolve()), "model_sha256": MODEL_SHA,
            "architecture": "RTMPose-m human body7; 17 COCO points",
            "onnxruntime": ort.__version__, "execution_provider": "CPUExecutionProvider",
            "numpy": np.__version__, "pillow": Image.__version__,
            "checkpoint_terms": config.get("model_terms", "UNVERIFIED")}


def audit_slots(audit, audit_path):
    """Reconcile the frozen inventory and snapshot before accepting its slots."""
    from creature_landmark_annotator import load_manifest
    inv_path = pinned(audit.get("native_inventory"))
    with gzip.open(inv_path, "rt", encoding="utf-8") as stream:
        inventory = json.load(stream)
    identity = audit.get("identity")
    native = {}
    for row in inventory.get("rows", []):
        if row.get("kind") == "def" and row.get("identity") == identity:
            key = ("def", identity, row.get("group"), row.get("frame"))
            need(key not in native, "INVENTORY_DUPLICATE_SLOT")
            native[key] = row
    declared = {}
    for row in audit.get("native_slots", []):
        key = tuple(row.get("key", []))
        need(key not in declared, "AUDIT_DUPLICATE_SLOT")
        declared[key] = row
    need(native and set(native) == set(declared), "AUDIT_NATIVE_SLOT_SCOPE_DRIFT")
    for key, row in declared.items():
        orig = native[key]
        geometry = row.get("geometry", {})
        need(geometry.get("canvas_1x") == orig.get("canvas")
             and geometry.get("crop_1x") == orig.get("crop")
             and geometry.get("origin_1x") == orig.get("anchor")
             and str(row.get("resource", "")).casefold() == str(orig.get("resource", "")).casefold()
             and str(row.get("native_lod", "")).casefold() == str(orig.get("native_lod", "")).casefold(),
             "AUDIT_NATIVE_GEOMETRY_DRIFT:" + str(key))
    masters = Path(audit.get("masters_dir", "")).resolve(strict=True)
    actual = {p.name.casefold(): p for p in masters.iterdir() if p.is_file() and p.suffix.lower() == ".png"}
    snapshot = {}
    for row in audit.get("source_snapshot", []):
        p = Path(row.get("path", ""))
        if p.suffix.lower() != ".png":
            continue
        need(p.is_absolute() and p.parent.resolve() == masters, "SNAPSHOT_PATH_OUTSIDE_SOURCE_DIR")
        name = p.name.casefold()
        need(name not in snapshot, "SNAPSHOT_DUPLICATE_NAME")
        snapshot[name] = row
    need(set(actual) == set(snapshot), "SOURCE_DIRECTORY_CHANGED_SINCE_FREEZE")
    for name, row in snapshot.items():
        need(actual[name].stat().st_size == row.get("bytes")
             and sha(actual[name]) == row.get("sha256"), "SOURCE_SNAPSHOT_DRIFT:" + name)
    if audit.get("schema") == "h3.hero.pose-manifest.v1":
        records = []
        for key, slot in sorted(declared.items(), key=lambda item: (item[0][2], item[0][3])):
            filename = f"{key[2]}_{key[3]}.png".casefold()
            if filename not in actual:
                continue
            image_path = actual[filename]
            with Image.open(image_path) as image:
                need(image.format == "PNG" and image.mode == "RGBA" and getattr(image, "n_frames", 1) == 1,
                     "SINGLE_RGBA_PNG_REQUIRED")
                pixels = list(image.size)
            records.append({"key": list(key), "image_path": str(image_path.resolve()),
                            "image_sha256": snapshot[filename]["sha256"], "pixels": pixels,
                            "slot": slot, "measurement_scope": "rider_mount_and_equipment"})
    else:
        _, records = load_manifest(audit_path)
    names = {f"{r['key'][2]}_{r['key'][3]}.png".casefold() for r in records}
    extra = sorted(set(actual) - {f"{k[2]}_{k[3]}.png".casefold() for k in native})
    missing = sorted(f"{k[2]}_{k[3]}.png" for k in native
                     if f"{k[2]}_{k[3]}.png".casefold() not in names)
    return records, {"identity": identity, "native_slots": len(native),
                     "available_slots": len(records), "missing": missing, "extra_pngs": extra,
                     "inventory": audit["native_inventory"]}


def load_input(path, expected_sha):
    path = pinned({"path": str(path), "sha256": expected_sha})
    ref = {"path": str(path), "sha256": expected_sha.lower()}
    if path.suffix.lower() == ".png":
        with Image.open(path) as im:
            need(im.format == "PNG" and im.mode == "RGBA" and getattr(im, "n_frames", 1) == 1,
                 "SINGLE_RGBA_PNG_REQUIRED")
            pixels = list(im.size)
        return ref, [{"key": None, "image_path": str(path), "image_sha256": ref["sha256"],
                      "pixels": pixels}], {"identity": None, "official_binding": "NOT_PROVIDED"}, []
    document = read_json(path)
    schema = document.get("schema")
    groups = []
    if schema == "h3.creature.auto-pose.request.v1":
        audit_ref = document.get("audit_manifest")
        audit_path = pinned(audit_ref)
        audit = read_json(audit_path)
        groups = document.get("comparison_groups", [])
        need(isinstance(groups, list), "COMPARISON_GROUP_LIST_REQUIRED")
    else:
        need(schema in ("h3.creature.scale-report-manifest.v1", "h3.hero.pose-manifest.v1"), "INPUT_SCHEMA_INVALID")
        audit_path, audit = path, document
        audit_ref = ref
    need(audit.get("schema") in ("h3.creature.scale-report-manifest.v1", "h3.hero.pose-manifest.v1"),
         "AUDIT_SCHEMA_REQUIRED")
    records, scope = audit_slots(audit, audit_path)
    pinned(audit_ref)
    scope["audit_manifest"] = {"path": str(audit_path), "sha256": audit_ref["sha256"].lower()}
    valid = {f"{r['key'][2]}_{r['key'][3]}" for r in records}
    dimensions = {f"{r['key'][2]}_{r['key'][3]}": r["pixels"] for r in records}
    used = set()
    for group in groups:
        need(isinstance(group, dict) and isinstance(group.get("id"), str)
             and group["id"] and isinstance(group.get("pose_group"), str)
             and group["pose_group"] and isinstance(group.get("direction"), str)
             and group["direction"], "EXPLICIT_POSE_DIRECTION_REQUIRED")
        slots = group.get("slots")
        need(isinstance(slots, list) and slots and len(slots) == len(set(slots))
             and set(slots) <= valid and group.get("reference") in slots, "COMPARISON_SLOTS_INVALID")
        need(not (set(slots) & used), "COMPARISON_SLOT_OVERLAP")
        feature = group.get("visual_feature_alignment")
        if feature is not None:
            need(isinstance(feature, dict) and feature.get("mode") in
                 ("explicit_source_regions", "full_opaque_foreground"), "FEATURE_ALIGNMENT_MODE_INVALID")
            if feature["mode"] == "explicit_source_regions":
                regions = feature.get("regions")
                need(isinstance(regions, dict) and set(regions) == set(slots), "FEATURE_REGION_SCOPE_INVALID")
                for slot, region in regions.items():
                    w, h = dimensions[slot]
                    need(isinstance(region, list) and len(region) == 4
                         and all(type(v) is int for v in region)
                         and 0 <= region[0] < region[2] <= w and 0 <= region[1] < region[3] <= h,
                         "FEATURE_REGION_OUTSIDE_IMAGE")
        used.update(slots)
    return ref, records, scope, groups


def preprocess(image, roi_override=None):
    need(image.mode == "RGBA" and image.width > 0 and image.height > 0, "RGBA_REQUIRED")
    need(image.width * image.height <= 50_000_000, "SOURCE_PIXEL_LIMIT")
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha >= 128)
    need(len(xs) >= 64 and np.any(alpha == 0), "TRANSPARENT_SUBJECT_REQUIRED")
    bbox = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
    rgb = Image.alpha_composite(Image.new("RGBA", image.size, (238, 243, 250, 255)), image).convert("RGB")
    x0, y0, x1, y1 = bbox if roi_override is None else roi_override
    need(all(math.isfinite(x) for x in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0,
         "MODEL_ROI_INVALID")
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    width, height = (x1 - x0) * 1.25, (y1 - y0) * 1.25
    if width > height * MODEL_SIZE[0] / MODEL_SIZE[1]:
        height = width * MODEL_SIZE[1] / MODEL_SIZE[0]
    else:
        width = height * MODEL_SIZE[0] / MODEL_SIZE[1]
    left, top = center[0] - width / 2, center[1] - height / 2
    crop = rgb.transform(MODEL_SIZE, Image.Transform.AFFINE,
                         (width / MODEL_SIZE[0], 0, left, 0, height / MODEL_SIZE[1], top),
                         resample=Image.Resampling.BILINEAR, fillcolor=(238, 243, 250))
    tensor = np.transpose((np.asarray(crop, dtype=np.float32) - MEAN) / STD, (2, 0, 1))[None].copy()
    return tensor, bbox, (left, top, width, height), rgb, alpha


def decode(outputs, region, *, flipped=False):
    ox, oy = outputs
    need(ox.ndim == 3 and oy.ndim == 3 and ox.shape[:2] == (1, 17)
         and oy.shape[:2] == (1, 17) and ox.shape[2] == 384 and oy.shape[2] == 512,
         "MODEL_OUTPUT_SHAPE_INVALID")
    left, top, width, height = region
    result = []
    for index, name in enumerate(JOINTS):
        i = FLIP_INDEX[index] if flipped else index
        x, y = float(np.argmax(ox[0, i])) / 2, float(np.argmax(oy[0, i])) / 2
        if flipped:
            x = (MODEL_SIZE[0] - 1) - x
        score = float(min(np.max(ox[0, i]), np.max(oy[0, i])))
        point = (left + x * width / MODEL_SIZE[0], top + y * height / MODEL_SIZE[1])
        need(all(math.isfinite(v) for v in (*point, score)), "MODEL_OUTPUT_NONFINITE")
        result.append({"name": name, "xy_source_pixels": [round(v, 3) for v in point],
                       "raw_score": round(score, 5)})
    return result


def midpoint(a, b):
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]


def detached_alpha1_diagnostic(alpha):
    """Count 8-connected components whose *entire* positive alpha is one.

    This only reports possible dust. It cannot decide subject ownership and never
    clears pixels. Run-length union/find avoids copying the original image.
    """
    need(alpha.ndim == 2 and alpha.dtype == np.uint8, "ALPHA_U8_REQUIRED")
    parent, rank, spans = [], [], []

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = root(a), root(b)
        if a != b:
            if rank[a] < rank[b]:
                a, b = b, a
            parent[b] = a
            if rank[a] == rank[b]:
                rank[a] += 1

    previous = []
    for y, row in enumerate(alpha):
        positive = row > 0
        changes = np.diff(np.concatenate(([False], positive, [False])).astype(np.int8))
        starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        current = []
        k = 0
        for start, end in zip(starts, ends):
            start, end = int(start), int(end)
            i = len(parent)
            need(i < 2_000_000, "ALPHA_COMPONENT_COMPLEXITY_LIMIT")
            parent.append(i); rank.append(0)
            maximum = int(row[start:end].max())
            spans.append((y, start, end, i, maximum))
            while k < len(previous) and previous[k][1] < start:
                k += 1
            j = k
            while j < len(previous) and previous[j][0] <= end:
                union(i, previous[j][2])
                j += 1
            current.append((start, end, i))
        previous = current
    components = {}
    for y, start, end, index, maximum in spans:
        key = root(index)
        value = components.setdefault(key, {"maximum_alpha": 0, "pixels": 0, "bbox": [start, y, end, y+1]})
        value["maximum_alpha"] = max(value["maximum_alpha"], maximum)
        value["pixels"] += end - start
        value["bbox"] = [min(value["bbox"][0], start), min(value["bbox"][1], y),
                         max(value["bbox"][2], end), max(value["bbox"][3], y+1)]
    isolated = [c for c in components.values() if c["maximum_alpha"] == 1]
    retained = [c for c in components.values() if c["maximum_alpha"] > 1]
    bbox = ([min(c["bbox"][0] for c in isolated), min(c["bbox"][1] for c in isolated),
             max(c["bbox"][2] for c in isolated), max(c["bbox"][3] for c in isolated)] if isolated else None)
    retained_bbox = ([min(c["bbox"][0] for c in retained), min(c["bbox"][1] for c in retained),
                      max(c["bbox"][2] for c in retained), max(c["bbox"][3] for c in retained)] if retained else None)
    return {"positive_alpha_components": len(components), "isolated_alpha1_components": len(isolated),
            "isolated_alpha1_pixels": sum(c["pixels"] for c in isolated), "isolated_alpha1_bbox": bbox,
            "other_positive_component_bbox": retained_bbox,
            "method": "8_connected_positive_alpha_component_max_equals_one",
            "ownership_verified": False, "pixels_changed": 0}


def body_features(points):
    p = {v["name"]: v for v in points}
    need(all(k in p and p[k].get("inside_source", False) for k in REQUIRED_BODY), "BODY_JOINT_OUTSIDE_IMAGE")
    v = {k: p[k]["xy_source_pixels"] for k in REQUIRED_BODY}
    shoulder = midpoint(v["left_shoulder"], v["right_shoulder"])
    hip = midpoint(v["left_hip"], v["right_hip"])
    segments = {"torso": math.dist(shoulder, hip),
                "thigh_median": statistics.median([math.dist(v["left_hip"], v["left_knee"]),
                                                     math.dist(v["right_hip"], v["right_knee"])]),
                "shin_median": statistics.median([math.dist(v["left_knee"], v["left_ankle"]),
                                                    math.dist(v["right_knee"], v["right_ankle"])])}
    need(min(segments.values()) > 1, "BODY_SEGMENT_TOO_SHORT")
    return segments, hip


def relative_registration(reference, target):
    a, ref_hip = body_features(reference)
    b, target_hip = body_features(target)
    ratios = {k: a[k] / b[k] for k in a}
    factor = statistics.median(ratios.values())
    spread = max(abs(v / factor - 1) for v in ratios.values())
    return {"uniform_scale": round(factor, 6),
            "translation_source_reference_pixels": [round(ref_hip[i] - factor * target_hip[i], 3) for i in (0, 1)],
            "reference_anchor": ref_hip, "target_anchor": target_hip,
            "anchor_semantics": "estimated_hip_midpoint_not_ground",
            "segment_scale_candidates": {k: round(v, 6) for k, v in ratios.items()},
            "segment_max_disagreement_fraction": round(spread, 6),
            "candidate_only": True, "native_registration": "NOT_MEASURED",
            "status": "REVIEW_REQUIRED", "scale_is_native_k": False}


def infer(session, record, roi_override=None):
    path = Path(record["image_path"])
    need(sha(path) == record["image_sha256"], "SOURCE_DRIFT_BEFORE_INFERENCE")
    with Image.open(path) as source:
        need(source.format == "PNG" and source.mode == "RGBA" and getattr(source, "n_frames", 1) == 1,
             "SINGLE_RGBA_PNG_REQUIRED")
        source.load()
        tensor, bbox, region, rgb, alpha = preprocess(source, roi_override)
    started = time.perf_counter()
    input_name = session.get_inputs()[0].name
    normal = decode(session.run(None, {input_name: tensor}), region)
    flipped = decode(session.run(None, {input_name: tensor[:, :, :, ::-1].copy()}), region, flipped=True)
    elapsed = time.perf_counter() - started
    for p, q in zip(normal, flipped):
        x, y = p["xy_source_pixels"]
        inside = 0 <= x < rgb.width and 0 <= y < rgb.height
        p["inside_source"] = inside
        p["alpha_at_point"] = int(alpha[min(int(y), rgb.height - 1), min(int(x), rgb.width - 1)]) if inside else None
        p["flip_xy_source_pixels"] = q["xy_source_pixels"]
        p["flip_raw_score"] = q["raw_score"]
        p["flip_disagreement_pixels"] = round(math.dist(p["xy_source_pixels"], q["xy_source_pixels"]), 3)
    need(sha(path) == record["image_sha256"], "SOURCE_DRIFT_DURING_INFERENCE")
    flags = []
    try:
        segments, _ = body_features(normal)
        torso = segments["torso"]
        flip_fraction = max(p["flip_disagreement_pixels"] / torso for p in normal if p["name"] in REQUIRED_BODY)
        if flip_fraction > 0.15:
            flags.append("BODY_FLIP_DISAGREEMENT_REVIEW")
    except ValueError:
        flip_fraction = None
        flags.append("BODY_FEATURES_UNAVAILABLE")
    if any(not p["inside_source"] for p in normal):
        flags.append("JOINT_OUTSIDE_IMAGE_REVIEW")
    if any(p["raw_score"] < 0.3 for p in normal if p["name"] in REQUIRED_BODY):
        flags.append("LOW_RAW_SCORE_REVIEW")
    dust = detached_alpha1_diagnostic(alpha)
    if dust["isolated_alpha1_pixels"]:
        flags.append("ISOLATED_ALPHA1_COMPONENTS_REVIEW")
    return {"source": {"path": str(path), "sha256": record["image_sha256"], "pixels": record["pixels"]},
            "roi_alpha_128": list(bbox), "roi_role": "MODEL_SEARCH_ONLY_NOT_BODY_SCALE",
            "model_region_source_pixels": [round(v, 3) for v in region],
            "points": normal, "alpha_diagnostic": dust,
            "max_body_flip_disagreement_over_torso": round(flip_fraction, 4) if flip_fraction is not None else None,
            "flags": flags, "seconds": round(elapsed, 4), "status": "AUTO_ESTIMATED_REVIEW_REQUIRED"}, rgb


def save_overlay(rgb, points, dest, *, edges=EDGES, secondary=None):
    factor = min(1.0, 640.0 / max(rgb.size))
    im = rgb.resize((max(1, round(rgb.width * factor)), max(1, round(rgb.height * factor))), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(im)
    for a, b in edges:
        points_pair = [points[a], points[b]]
        if all(p["inside_source"] for p in points_pair):
            draw.line([tuple(v * factor for v in p["xy_source_pixels"]) for p in points_pair], fill=(232, 38, 45), width=2)
    for i, p in enumerate(points):
        if p["inside_source"]:
            x, y = [v * factor for v in p["xy_source_pixels"]]
            fx, fy = [v * factor for v in p["flip_xy_source_pixels"]]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(232, 38, 45))
            draw.ellipse((fx - 2, fy - 2, fx + 2, fy + 2), fill=(25, 110, 230))
            if i in (3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16):
                draw.text((x + 3, y + 2), str(i), fill=(20, 20, 20), stroke_width=1, stroke_fill=(255, 255, 255))
    if secondary:
        for a, b in EDGES:
            pair = [secondary[a], secondary[b]]
            if all(p["inside_source"] for p in pair):
                draw.line([tuple(v * factor for v in p["xy_source_pixels"]) for p in pair], fill=(0, 150, 85), width=2)
        for p in secondary:
            if p["inside_source"]:
                x, y = [v * factor for v in p["xy_source_pixels"]]
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 150, 85))
    with dest.open("xb") as stream:
        im.save(stream, format="PNG")


def create_preview(group, bindings, predictions, outdir, registration=None):
    items = []
    ref_key = group["reference"]
    ref = predictions[bindings[ref_key]]
    for slot in group["slots"]:
        target = predictions[bindings[slot]]
        feature = group.get("visual_feature_alignment")
        alignment = None
        if feature is not None:
            if feature["mode"] == "explicit_source_regions":
                reference_region = feature["regions"][ref_key]
                target_region = feature["regions"][slot]
            else:
                reference_region = [0, 0, *ref["source"]["pixels"]]
                target_region = [0, 0, *target["source"]["pixels"]]
            alignment = pose_feature_registration.estimate(ref["source"], target["source"],
                                                           reference_region, target_region)
            alignment["region_mode"] = feature["mode"]
        try:
            proposal = (registration(ref, target) if registration else
                        relative_registration(ref["points"], target["points"]))
        except ValueError as error:
            item = {"slot": slot, "status": "REVIEW_REQUIRED", "error": str(error)}
            if alignment is not None:
                item["visual_feature_alignment"] = alignment
            items.append(item)
            continue
        if proposal["segment_max_disagreement_fraction"] > 0.15:
            proposal["warning"] = "SEGMENT_ESTIMATES_DISAGREE"
        if abs(proposal["uniform_scale"] - 1) >= 0.1:
            proposal["scale_warning"] = "SOURCE_PIXEL_DIFFERENCE_REQUIRES_REVIEW"
        item = {"slot": slot, **proposal}
        if alignment is not None:
            item["visual_feature_alignment"] = alignment
        items.append(item)
    result = {"id": group["id"], "pose_group": group["pose_group"], "direction": group["direction"],
              "reference": ref_key, "items": items,
              "policy": "Comparable source-pixel candidate only; body-anchor preview hides real trajectory.",
              "native_registration": "NOT_MEASURED", "art_approved": False}
    # APNG is an optional review visualization, never a registered master.
    if len(items) > 16 or any("uniform_scale" not in x or not 0.25 <= x["uniform_scale"] <= 4 for x in items):
        result["preview_status"] = "NOT_RENDERED_UNSAFE_OR_TOO_MANY_FRAMES"
        return result
    extents = []
    for item in items:
        frame = predictions[bindings[item["slot"]]]
        width, height = frame["source"]["pixels"]
        hx, hy = item["target_anchor"]
        for factor in (1., item["uniform_scale"]):
            extents.append((-hx * factor, -hy * factor, (width - hx) * factor, (height - hy) * factor))
    minx, miny = min(e[0] for e in extents), min(e[1] for e in extents)
    maxx, maxy = max(e[2] for e in extents), max(e[3] for e in extents)
    anchor = (math.ceil(-minx) + 6, math.ceil(-miny) + 6)
    size = (math.ceil(maxx - minx) + 14, math.ceil(maxy - miny) + 14)
    if size[0] * size[1] > 8_000_000:
        result["preview_status"] = "NOT_RENDERED_PIXEL_LIMIT"
        return result
    previews = {"before": [], "after": []}
    for item in items:
        source = predictions[bindings[item["slot"]]]["source"]
        path = Path(source["path"])
        need(sha(path) == source["sha256"], "SOURCE_DRIFT_BEFORE_PREVIEW")
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
        hx, hy = item["target_anchor"]
        for label, factor in (("before", 1.), ("after", item["uniform_scale"])):
            transform = (1 / factor, 0, hx - anchor[0] / factor, 0, 1 / factor, hy - anchor[1] / factor)
            previews[label].append(image.transform(size, Image.Transform.AFFINE, transform,
                                                    resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0)))
        need(sha(path) == source["sha256"], "SOURCE_DRIFT_DURING_PREVIEW")
    # Group IDs are displayed text; only generated safe ordinal is used in paths.
    index = len(list(outdir.glob("preview-*-before.apng")))
    result["preview"] = {}
    for label, images in previews.items():
        target = outdir / f"preview-{index:03d}-{label}.apng"
        with target.open("xb") as stream:
            images[0].save(stream, format="PNG", save_all=True, append_images=images[1:],
                           duration=[125] * len(images), loop=0, disposal=[1] * len(images), blend=[0] * len(images))
        with Image.open(target) as check:
            need(check.size == size and getattr(check, "n_frames", 1) == len(images), "PREVIEW_APNG_VERIFY_FAILED")
        result["preview"][label] = {"path": target.name, "sha256": sha(target)}
    result["preview_status"] = "SOURCE_PIXEL_COMPARISON_ONLY"
    return result


def report_html(report):
    esc = lambda x: html.escape(str(x), quote=True)
    sections = []
    for g in report["comparisons"]:
        previews = g.get("preview", {})
        links = " ".join(f'<a href="{esc(v["path"])}">{esc(k)} APNG</a>' for k, v in previews.items())
        sections.append(f'<h2>比较组 {esc(g["id"])} / {esc(g["pose_group"])}</h2><p>{links} '
                        '预览仅按估计髋部对齐，不能用来判定游戏位移或正式注册。</p>')
        sections.append("<ul>" + "".join(f'<li>{esc(i["slot"])}: 等比 {esc(i.get("uniform_scale", "未计算"))}, '
                                        f'骨段分歧 {esc(i.get("segment_max_disagreement_fraction", "未计算"))} '
                                        f'{esc(i.get("warning", ""))} {esc(i.get("scale_warning", ""))}；'
                                        f'纹理配准候选 {esc(i.get("visual_feature_alignment", {}).get("status", "未启用"))} '
                                        f'倍率 {esc(i.get("visual_feature_alignment", {}).get("uniform_scale_target_to_reference", "未计算"))}</li>'
                                        for i in g["items"]) + "</ul>")
    for b in report["bindings"]:
        p = report["predictions"][b["source_sha256"]]
        title = "单张未绑定槽位" if b["key"] is None else f'{b["key"][1]} {b["key"][2]}_{b["key"][3]}'
        sections.append(f'<article><h3>{esc(title)}</h3><img loading="lazy" src="{esc(p["overlay"]["path"])}">'
                        f'<p>{esc(", ".join(p["flags"]) or "无自动警示；仍需逐帧复核")}</p></article>')
    profile_note = ("红色为马体、绿色为骑手候选，蓝色为马体水平翻转复测点；骑手区域也是模型推测。"
                    if report.get("model_profile") == "mounted" else
                    "红色为原图模型点，蓝色为水平翻转复测映回的点。")
    return ("<!doctype html><meta charset='utf-8'><title>生物自动描点候选</title>"
            "<style>body{font:15px system-ui;margin:24px;background:#eef3fa;color:#263447}"
            "article{display:inline-block;vertical-align:top;width:310px;margin:6px;padding:8px;background:white}"
            "img{max-width:100%;height:auto}li{margin:4px}</style>"
            "<h1>生物自动描点与相对注册候选</h1><p>" + profile_note +
            "分值未校准；点位、缺肢、身份、原生位置、K、正式3X与实际游戏均未自动验收。"
            "预览是派生审图，不是母版。</p>" + "".join(sections))


def execute(action, input_path, input_sha, outdir, operation_id, profile="human"):
    need(OP_ID.fullmatch(operation_id) is not None, "OPERATION_ID_INVALID")
    need(profile in ("human", "mounted"), "MODEL_PROFILE_INVALID")
    output_kind = "hero-mounted-pose" if profile == "mounted" else "creature-auto-pose"
    expected_out = PRODUCT / "build" / "experiments" / output_kind / operation_id
    need(Path(outdir).resolve() == expected_out.resolve(), "OUTPUT_OPERATION_ROOT_INVALID")
    source_ref, records, scope, groups = load_input(input_path, input_sha)
    feature_enabled = any(g.get("visual_feature_alignment") is not None for g in groups)
    feature_provider = pose_feature_registration.provider() if feature_enabled else None
    mounted = None
    if profile == "mounted":
        import mounted_pose
        mounted = mounted_pose
        provider = mounted.load_provider()
    else:
        provider = load_provider()
    script_dir = Path(__file__).resolve().parent
    script_names = ["creature_auto_pose.py", "creature_landmark_annotator.py", "tool_paths.py"]
    if feature_enabled:
        script_names.append("pose_feature_registration.py")
    if mounted:
        script_names += ["mounted_pose.py", "hero_mounted_pose.py"]
    implementation = {name: {"path": str((script_dir / name).resolve()), "sha256": sha(script_dir / name)}
                      for name in script_names}
    result_path = expected_out / "pose-report.json"
    if action == "check":
        need(not expected_out.exists(), "OUTPUT_ALREADY_EXISTS")
        return {"status": "READY_FOR_LOCAL_INFERENCE", "records": len(records), "scope": scope,
                "outdir": str(expected_out), "automatic_art_approval": False}
    if action == "verify":
        result = read_json(result_path)
        need(result.get("input") == source_ref and result.get("provider") == provider
             and result.get("model_profile") == profile
             and result.get("feature_provider") == feature_provider
             and result.get("implementation") == implementation
             and result.get("scope") == scope and result.get("operation_id") == operation_id,
             "REPORT_INPUT_BINDING_DRIFT")
        outputs = result.get("outputs", [])
        need(isinstance(outputs, list) and outputs, "OUTPUT_LIST_MISSING")
        expected_files = {p.relative_to(expected_out).as_posix() for p in expected_out.rglob("*")
                          if p.is_file() and p != result_path}
        need(len(outputs) == len(expected_files) and {v.get("path") for v in outputs} == expected_files,
             "OUTPUT_FILE_SCOPE_DRIFT")
        for entry in outputs:
            p = (expected_out / entry["path"]).resolve()
            need(p.is_relative_to(expected_out.resolve()) and p.is_file()
                 and sha(p) == entry["sha256"], "OUTPUT_SHA_DRIFT")
        need({r["image_sha256"] for r in records} == set(result.get("predictions", {})), "PREDICTION_SCOPE_DRIFT")
        expected_bindings = [(r["key"], r["image_path"], r["image_sha256"]) for r in records]
        actual_bindings = [(b.get("key"), b.get("source_path"), b.get("source_sha256"))
                           for b in result.get("bindings", [])]
        need(expected_bindings == actual_bindings, "LOGICAL_BINDINGS_DRIFT")
        return {"status": "VERIFIED_REVIEW_REQUIRED", "report": str(result_path),
                "report_sha256": sha(result_path), "records": len(records), "automatic_art_approval": False}
    need(not expected_out.exists(), "OUTPUT_ALREADY_EXISTS")
    expected_out.mkdir(parents=True, exist_ok=False)
    overlay_dir = expected_out / "overlays"
    overlay_dir.mkdir()
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = (mounted.create_sessions(provider, options) if mounted else
               ort.InferenceSession(provider["model_path"], sess_options=options, providers=["CPUExecutionProvider"]))
    predictions, bindings, slot_map = {}, [], {}
    for record in records:
        digest = record["image_sha256"]
        if digest not in predictions:
            prediction, rgb = mounted.infer(session, record) if mounted else infer(session, record)
            overlay = overlay_dir / (digest + ".png")
            save_overlay(rgb, prediction["points"], overlay,
                         edges=mounted.EDGES if mounted else EDGES,
                         secondary=prediction.get("rider_candidate", {}).get("points") if mounted else None)
            prediction["overlay"] = {"path": "overlays/" + overlay.name, "sha256": sha(overlay)}
            predictions[digest] = prediction
        key = record["key"]
        bindings.append({"key": key, "source_path": record["image_path"], "source_sha256": digest,
                         "official_geometry": record.get("slot", {}).get("geometry")})
        if key is not None:
            slot_map[f"{key[2]}_{key[3]}"] = digest
    comparisons = [create_preview(g, slot_map, predictions, expected_out,
                                  registration=mounted.relative_registration if mounted else None) for g in groups]
    # Re-hash all bound files (including duplicate logical slots), not just unique pictures.
    for record in records:
        need(sha(record["image_path"]) == record["image_sha256"], "SOURCE_DRIFT_AFTER_INFERENCE")
    pinned(source_ref)
    for field in ("inventory", "audit_manifest"):
        if field in scope:
            pinned(scope[field])
    if mounted:
        mounted.verify_models(provider)
    else:
        need(sha(provider["model_path"]) == MODEL_SHA, "MODEL_DRIFT_DURING_INFERENCE")
    report = {"schema": "h3.creature.auto-pose.report.v1", "operation_id": operation_id,
              "status": "AUTO_ESTIMATED_REVIEW_REQUIRED", "model_profile": profile, "input": source_ref, "scope": scope,
              "provider": provider, "implementation": implementation, "bindings": bindings, "predictions": predictions,
              "feature_provider": feature_provider,
              "comparisons": comparisons, "scores_are_calibrated_probabilities": False,
              "anatomical_accuracy_proven": False, "native_registration_proven": False,
              "body_scale_proven": False, "automatic_art_approval": False, "source_images_modified": False,
              "output_is_master": False, "outputs": []}
    html_path = expected_out / "review.html"
    with html_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(report_html(report))
    paths = sorted([p for p in expected_out.rglob("*") if p.is_file()])
    report["outputs"] = [{"path": p.relative_to(expected_out).as_posix(), "sha256": sha(p)} for p in paths]
    with result_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": report["status"], "report": str(result_path), "report_sha256": sha(result_path),
            "review_html": str(html_path), "logical_slots": len(bindings),
            "unique_source_images": len(predictions), "automatic_art_approval": False}


def main(argv=None, profile="human"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run", "verify"))
    for name in ("manifest", "manifest-sha256", "outdir", "operation-id"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(args.action, args.manifest, args.manifest_sha256, args.outdir, args.operation_id, profile)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, ImportError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "BLOCKED", "code": "CREATURE_AUTO_POSE", "error": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
