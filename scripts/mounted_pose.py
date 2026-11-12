"""Mounted-hero review: separate horse and rider model candidates, never an approval."""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time

import numpy as np
from PIL import Image

import creature_auto_pose as common

ANIMAL_SHA = "1cfd1c86e0d9e5d5f95178bcd95ee9a4e8386a624cd3c57519f27ff58cac7f28"
NAMES = ("left_eye", "right_eye", "nose", "neck", "tail_root", "left_shoulder", "left_elbow",
         "left_fore_hoof", "right_shoulder", "right_elbow", "right_fore_hoof", "left_hip", "left_knee",
         "left_hind_hoof", "right_hip", "right_knee", "right_hind_hoof")
FLIP = (1, 0, 2, 3, 4, 8, 9, 10, 5, 6, 7, 14, 15, 16, 11, 12, 13)
EDGES = ((0, 1), (0, 2), (1, 2), (2, 3), (3, 4), (3, 5), (5, 6), (6, 7),
         (3, 8), (8, 9), (9, 10), (4, 11), (11, 12), (12, 13),
         (4, 14), (14, 15), (15, 16))


def freeze(argv):
    parser = argparse.ArgumentParser(description="Freeze one official AH mounted identity and current source PNGs.")
    for field in ("inventory", "inventory-sha256", "identity", "masters-dir", "operation-id"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        common.need(re.fullmatch(r"AH(?:0[0-9]|1[0-7])_", args.identity) is not None, "MOUNTED_AH_IDENTITY_REQUIRED")
        common.need(common.OP_ID.fullmatch(args.operation_id) is not None, "OPERATION_ID_INVALID")
        inv = common.pinned({"path": args.inventory, "sha256": args.inventory_sha256})
        master = Path(args.masters_dir)
        common.need(master.is_absolute() and master.is_dir() and master.name.upper() == args.identity,
                    "MOUNTED_MASTER_DIRECTORY_IDENTITY_MISMATCH")
        master = master.resolve()
        with gzip.open(inv, "rt", encoding="utf-8") as stream:
            inventory = json.load(stream)
        rows = [r for r in inventory.get("rows", []) if r.get("kind") == "def" and r.get("identity") == args.identity]
        common.need(rows, "MOUNTED_NATIVE_IDENTITY_MISSING")
        slots = []
        for row in sorted(rows, key=lambda r: (r["group"], r["frame"])):
            slots.append({"key": ["def", args.identity, row["group"], row["frame"]],
                          "geometry": {"canvas_1x": row["canvas"], "crop_1x": row["crop"],
                                       "origin_1x": row["anchor"]},
                          "resource": row.get("resource"), "native_lod": row.get("native_lod")})
        common.need(len({tuple(s["key"]) for s in slots}) == len(slots), "MOUNTED_NATIVE_DUPLICATE_SLOT")
        sources = [{"path": str(p.resolve()), "sha256": common.sha(p), "bytes": p.stat().st_size}
                   for p in sorted(master.iterdir()) if p.is_file() and p.suffix.lower() == ".png"]
        common.pinned({"path": str(inv), "sha256": args.inventory_sha256})
        for item in sources:
            common.pinned(item)
        doc = {"schema": "h3.hero.pose-manifest.v1", "operation_id": args.operation_id,
               "identity": args.identity, "category": "mounted", "native_inventory":
               {"path": str(inv), "sha256": args.inventory_sha256.lower()}, "native_slots": slots,
               "masters_dir": str(master), "source_snapshot": sources,
               "metadata_unknown_values_are_not_guessed": True}
        path = common.PRODUCT / "build" / "experiments" / "hero-pose-requests" / (args.operation_id + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(doc, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"status": "FROZEN", "manifest": str(path), "sha256": common.sha(path),
                          "native_slots": len(slots), "source_images": len(sources)}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "code": "HERO_POSE_FREEZE", "error": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


def load_provider():
    import onnxruntime as ort
    human = common.load_provider()
    path = common.PROVIDER / "provider-mounted.json"
    config = common.read_json(path)
    common.need(config.get("schema") == "h3.creature.mounted-pose-provider.v1"
                and config.get("model") == "rtmpose-animal-ap10k.onnx"
                and config.get("model_sha256") == ANIMAL_SHA, "MOUNTED_PROVIDER_INVALID")
    model = common.PROVIDER / config["model"]
    common.need(model.is_file() and common.sha(model) == ANIMAL_SHA, "MOUNTED_MODEL_SHA_DRIFT")
    return {"path": str(path.resolve()), "sha256": common.sha(path),
            "model_path": str(model.resolve()), "model_sha256": ANIMAL_SHA,
            "architecture": "RTMPose-m AP10K animal 17 points, with separate human-body7 rider candidate",
            "onnxruntime": ort.__version__, "execution_provider": "CPUExecutionProvider",
            "human_provider": human, "checkpoint_terms": config.get("model_terms", "UNVERIFIED")}


def verify_models(provider):
    common.need(common.sha(provider["model_path"]) == ANIMAL_SHA, "MOUNTED_MODEL_DRIFT")
    common.need(common.sha(provider["human_provider"]["model_path"]) == common.MODEL_SHA,
                "RIDER_MODEL_DRIFT")


def create_sessions(provider, options):
    import onnxruntime as ort
    return {"horse": ort.InferenceSession(provider["model_path"], sess_options=options, providers=["CPUExecutionProvider"]),
            "rider": ort.InferenceSession(provider["human_provider"]["model_path"], sess_options=options,
                                          providers=["CPUExecutionProvider"])}


def decode(outputs, region, *, flipped=False):
    ox, oy = outputs
    common.need(ox.ndim == 3 and oy.ndim == 3 and ox.shape == (1, 17, 512)
                and oy.shape == (1, 17, 512), "ANIMAL_MODEL_OUTPUT_SHAPE_INVALID")
    left, top, side = region
    points = []
    for index, name in enumerate(NAMES):
        i = FLIP[index] if flipped else index
        x, y = float(np.argmax(ox[0, i])) / 2, float(np.argmax(oy[0, i])) / 2
        if flipped:
            x = 255 - x
        score = float(min(np.max(ox[0, i]), np.max(oy[0, i])))
        xy = (left + x * side / 256, top + y * side / 256)
        common.need(all(math.isfinite(v) for v in (*xy, score)), "ANIMAL_MODEL_OUTPUT_NONFINITE")
        points.append({"name": name, "xy_source_pixels": [round(v, 3) for v in xy],
                       "raw_score": round(score, 5)})
    return points


def horse_features(points):
    p = {x["name"]: x for x in points}
    required = ("neck", "tail_root", "left_shoulder", "left_elbow", "left_fore_hoof",
                "right_shoulder", "right_elbow", "right_fore_hoof", "left_hip", "left_knee",
                "left_hind_hoof", "right_hip", "right_knee", "right_hind_hoof")
    common.need(all(k in p and p[k].get("inside_source", False) for k in required), "HORSE_JOINT_OUTSIDE_IMAGE")
    v = {k: p[k]["xy_source_pixels"] for k in required}
    lengths = {"back_neck_to_tail": math.dist(v["neck"], v["tail_root"]),
               "front_upper_median": statistics.median([math.dist(v["left_shoulder"], v["left_elbow"]),
                                                          math.dist(v["right_shoulder"], v["right_elbow"])]),
               "front_lower_median": statistics.median([math.dist(v["left_elbow"], v["left_fore_hoof"]),
                                                          math.dist(v["right_elbow"], v["right_fore_hoof"])]),
               "hind_upper_median": statistics.median([math.dist(v["left_hip"], v["left_knee"]),
                                                         math.dist(v["right_hip"], v["right_knee"])]),
               "hind_lower_median": statistics.median([math.dist(v["left_knee"], v["left_hind_hoof"]),
                                                         math.dist(v["right_knee"], v["right_hind_hoof"])])}
    common.need(min(lengths.values()) > 1, "HORSE_SEGMENT_TOO_SHORT")
    return lengths, common.midpoint(v["neck"], v["tail_root"])


def infer(sessions, record):
    path = Path(record["image_path"])
    common.need(common.sha(path) == record["image_sha256"], "MOUNTED_SOURCE_DRIFT")
    with Image.open(path) as raw:
        common.need(raw.format == "PNG" and raw.mode == "RGBA" and getattr(raw, "n_frames", 1) == 1,
                    "SINGLE_RGBA_PNG_REQUIRED")
        raw.load()
        image = raw.copy()
    common.need(image.width * image.height <= 50_000_000, "SOURCE_PIXEL_LIMIT")
    alpha = np.asarray(image.getchannel("A")); ys, xs = np.where(alpha >= 128)
    common.need(len(xs) >= 64 and np.any(alpha == 0), "TRANSPARENT_SUBJECT_REQUIRED")
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1))
    x0, y0, x1, y1 = bbox
    side = 1.25 * max(x1-x0, y1-y0)
    left, top = (x0+x1-side)/2, (y0+y1-side)/2
    rgb = Image.alpha_composite(Image.new("RGBA", image.size, (238, 243, 250, 255)), image).convert("RGB")
    crop = rgb.transform((256, 256), Image.Transform.AFFINE, (side/256, 0, left, 0, side/256, top),
                         resample=Image.Resampling.BILINEAR, fillcolor=(238, 243, 250))
    tensor = np.transpose((np.asarray(crop, dtype=np.float32)-common.MEAN)/common.STD, (2, 0, 1))[None].copy()
    horse_session = sessions["horse"]; name = horse_session.get_inputs()[0].name
    start = time.perf_counter()
    points = decode(horse_session.run(None, {name: tensor}), (left, top, side))
    flipped = decode(horse_session.run(None, {name: tensor[:, :, :, ::-1].copy()}), (left, top, side), flipped=True)
    for p, f in zip(points, flipped):
        x, y = p["xy_source_pixels"]
        inside = 0 <= x < image.width and 0 <= y < image.height
        p["inside_source"] = inside
        p["alpha_at_point"] = int(alpha[int(y), int(x)]) if inside else None
        p["flip_xy_source_pixels"] = f["xy_source_pixels"]
        p["flip_raw_score"] = f["raw_score"]
        p["flip_disagreement_pixels"] = round(math.dist(p["xy_source_pixels"], f["xy_source_pixels"]), 3)
    flags = ["HORSE_AND_RIDER_MUST_BOTH_BE_REVIEWED"]
    try:
        features, anchor = horse_features(points)
        frac = max(x["flip_disagreement_pixels"] for x in points[3:]) / features["back_neck_to_tail"]
        if frac > .15:
            flags.append("HORSE_FLIP_DISAGREEMENT_REVIEW")
    except ValueError:
        features, anchor, frac = None, [(x0+x1)/2, (y0+y1)/2], None
        flags.append("HORSE_FEATURES_UNAVAILABLE")
    # Rider ROI is only a visual model proposal, not an anatomical measurement.
    byname = {p["name"]: p["xy_source_pixels"] for p in points}
    neck, tail = byname["neck"], byname["tail_root"]
    hooves = [byname[k][1] for k in ("left_fore_hoof", "right_fore_hoof", "left_hind_hoof", "right_hind_hoof")]
    center_x, back_y = (neck[0]+tail[0])/2, (neck[1]+tail[1])/2
    horse_width = abs(neck[0]-tail[0]); horse_height = max(hooves)-back_y
    if horse_width < (x1-x0)*.15 or horse_height < (y1-y0)*.15:
        flags.append("RIDER_ROI_UNRELIABLE")
        roi = [x0, y0, x1, y1]
    else:
        half_width = max(horse_width*.65, (x1-x0)*.15)
        roi = [center_x-half_width, back_y-horse_height*.95,
               center_x+half_width, back_y+horse_height*.50]
    rider, _ = common.infer(sessions["rider"], record, roi_override=roi)
    if rider["flags"]:
        flags.append("RIDER_ESTIMATE_REQUIRES_REVIEW")
    common.need(common.sha(path) == record["image_sha256"], "MOUNTED_SOURCE_DRIFT_AFTER_INFERENCE")
    result = {"source": {"path": str(path), "sha256": record["image_sha256"], "pixels": record["pixels"]},
              "roi_alpha_128": list(bbox), "roi_role": "MODEL_SEARCH_ONLY_NOT_BODY_SCALE",
              "points": points, "alpha_diagnostic": rider["alpha_diagnostic"],
              "max_horse_flip_disagreement_over_back": round(frac, 4) if frac is not None else None,
              "rider_candidate": {"roi_source_pixels": [round(v, 3) for v in roi],
                                  "roi_basis": "uncalibrated horse-landmark heuristic; not a segmentation",
                                  "points": rider["points"], "flags": rider["flags"],
                                  "status": "AUTO_ESTIMATED_REVIEW_REQUIRED"},
              "flags": flags, "seconds": round(time.perf_counter()-start, 4),
              "status": "AUTO_ESTIMATED_REVIEW_REQUIRED"}
    if rider["alpha_diagnostic"]["isolated_alpha1_pixels"]:
        flags.append("ISOLATED_ALPHA1_COMPONENTS_REVIEW")
    return result, rgb


def relative_registration(reference, target):
    a, ref_anchor = horse_features(reference["points"])
    b, target_anchor = horse_features(target["points"])
    ratios = {k: a[k]/b[k] for k in a}
    scale = statistics.median(ratios.values())
    proposal = {"uniform_scale": round(scale, 6),
                "translation_source_reference_pixels": [round(ref_anchor[i]-scale*target_anchor[i], 3) for i in (0, 1)],
                "reference_anchor": ref_anchor, "target_anchor": target_anchor,
                "anchor_semantics": "estimated_horse_neck_tail_midpoint_not_ground",
                "segment_scale_candidates": {k: round(v, 6) for k, v in ratios.items()},
                "segment_max_disagreement_fraction": round(max(abs(v/scale-1) for v in ratios.values()), 6),
                "candidate_only": True, "native_registration": "NOT_MEASURED",
                "status": "REVIEW_REQUIRED", "scale_is_native_k": False,
                "rider_scale_status": "NOT_MEASURED"}
    try:
        rider_a, _ = common.body_features(reference["rider_candidate"]["points"])
        rider_b, _ = common.body_features(target["rider_candidate"]["points"])
        rider_scale = rider_a["torso"] / rider_b["torso"]
        proposal["rider_torso_scale_candidate"] = round(rider_scale, 6)
        proposal["rider_scale_disagreement_fraction"] = round(abs(rider_scale/scale-1), 6)
        proposal["rider_scale_status"] = "AUTO_ESTIMATED_REVIEW_REQUIRED"
        if abs(rider_scale/scale-1) > .15:
            proposal["rider_warning"] = "RIDER_HORSE_ESTIMATES_DISAGREE"
    except (ValueError, KeyError, ZeroDivisionError):
        pass
    return proposal
