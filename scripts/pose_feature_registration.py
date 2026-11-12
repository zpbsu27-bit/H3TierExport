"""Optional, SHA-bound texture matching for explicit same-pose review regions.

The returned uniform transform is a proposal in source pixels only. It is not
an anatomical measurement, native registration, or permission to alter art.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image


def provider():
    try:
        import cv2
    except ImportError:
        return {"status": "UNAVAILABLE", "reason": "OPENCV_NOT_INSTALLED"}
    if not hasattr(cv2, "SIFT_create") or not hasattr(cv2, "estimateAffinePartial2D"):
        return {"status": "UNAVAILABLE", "reason": "OPENCV_FEATURES_UNAVAILABLE"}
    build = cv2.getBuildInformation().encode("utf-8")
    return {"status": "AVAILABLE", "opencv_version": cv2.__version__,
            "opencv_build_info_sha256": hashlib.sha256(build).hexdigest()}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _load(source, region):
    import cv2
    path = Path(source["path"])
    _need(path.is_absolute() and path.is_file() and _sha(path) == source["sha256"], "FEATURE_SOURCE_SHA_DRIFT")
    with Image.open(path) as image:
        _need(image.format == "PNG" and image.mode == "RGBA" and getattr(image, "n_frames", 1) == 1,
              "FEATURE_SINGLE_RGBA_PNG_REQUIRED")
        _need(image.width * image.height <= 50_000_000, "FEATURE_SOURCE_PIXEL_LIMIT")
        pixels = np.asarray(image.copy())
        width, height = image.size
    _need(isinstance(region, list) and len(region) == 4 and all(type(x) is int for x in region),
          "FEATURE_EXPLICIT_REGION_REQUIRED")
    x0, y0, x1, y1 = region
    _need(0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, "FEATURE_REGION_OUTSIDE_IMAGE")
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y0:y1, x0:x1] = np.where(pixels[y0:y1, x0:x1, 3] >= 128, 255, 0).astype(np.uint8)
    _need(np.count_nonzero(mask) >= 64, "FEATURE_REGION_HAS_TOO_FEW_OPAQUE_PIXELS")
    gray = cv2.cvtColor(pixels[:, :, :3], cv2.COLOR_RGB2GRAY)
    factor = min(1.0, 1600.0 / max(width, height))
    if factor < 1:
        w, h = max(1, round(width * factor)), max(1, round(height * factor))
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        w, h = width, height
    _need(_sha(path) == source["sha256"], "FEATURE_SOURCE_CHANGED_DURING_READ")
    return gray, mask, (width / w, height / h), (width, height)


def estimate(reference, target, reference_region, target_region):
    """Estimate target-to-reference rotation-free scale+translation, for review."""
    base = {"status": "REVIEW_REQUIRED", "candidate_only": True,
            "method": "SIFT_MUTUAL_RATIO_RANSAC_SIMILARITY",
            "native_registration": "NOT_MEASURED", "body_scale": "NOT_MEASURED",
            "same_identity_proven": False, "pixels_changed": False,
            "reference_sha256": reference["sha256"], "target_sha256": target["sha256"],
            "reference_region_source_pixels": reference_region,
            "target_region_source_pixels": target_region}
    info = provider()
    if info["status"] != "AVAILABLE":
        return {**base, "status": "UNAVAILABLE", "reason": info["reason"]}
    import cv2
    rg, rm, rf, rdims = _load(reference, reference_region)
    tg, tm, tf, tdims = _load(target, target_region)
    sift = cv2.SIFT_create(nfeatures=4000)
    rk, ra = sift.detectAndCompute(rg, rm)
    tk, ta = sift.detectAndCompute(tg, tm)
    if ra is None or ta is None or len(ra) < 2 or len(ta) < 2:
        return {**base, "status": "NOT_COMPARABLE", "reason": "INSUFFICIENT_TEXTURE_FEATURES"}
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    fw = matcher.knnMatch(ta, ra, k=2)
    bw = matcher.knnMatch(ra, ta, k=2)
    reverse = {a.queryIdx: a.trainIdx for pair in bw if len(pair) == 2
               for a, b in [pair] if a.distance < .75 * b.distance}
    matches = [a for pair in fw if len(pair) == 2 for a, b in [pair]
               if a.distance < .75 * b.distance and reverse.get(a.trainIdx) == a.queryIdx]
    if len(matches) < 6:
        return {**base, "status": "NOT_COMPARABLE", "reason": "INSUFFICIENT_MUTUAL_MATCHES", "matches": len(matches)}
    src = np.float32([[tk[m.queryIdx].pt[0] * tf[0], tk[m.queryIdx].pt[1] * tf[1]] for m in matches])
    dst = np.float32([[rk[m.trainIdx].pt[0] * rf[0], rk[m.trainIdx].pt[1] * rf[1]] for m in matches])
    threshold = max(3.0, .003 * max(rdims))
    cv2.setRNGSeed(7)
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
        ransacReprojThreshold=threshold, maxIters=5000, confidence=.999)
    if matrix is None or inliers is None or int(inliers.sum()) < 6:
        return {**base, "status": "NOT_COMPARABLE", "reason": "NO_STABLE_SIMILARITY", "matches": len(matches)}
    inside = inliers[:, 0].astype(bool)
    factor = float(math.hypot(matrix[0, 0], matrix[1, 0]))
    _need(math.isfinite(factor) and factor > 0, "FEATURE_SCALE_INVALID")
    rotation = float(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
    trans = np.median(dst[inside] - factor * src[inside], axis=0)
    residual = np.linalg.norm(dst[inside] - (src[inside] * factor + trans), axis=1)
    extents = src[inside].max(axis=0) - src[inside].min(axis=0)
    coverage = [float(extents[0] / (target_region[2] - target_region[0])),
                float(extents[1] / (target_region[3] - target_region[1]))]
    count = int(inside.sum()); fraction = count / len(matches)
    warnings = []
    if count < 12 or fraction < .5: warnings.append("FEW_OR_INCONSISTENT_MATCHES")
    if min(coverage) < .12: warnings.append("MATCHES_SPATIALLY_CONCENTRATED")
    if abs(rotation) > 2.5: warnings.append("ESTIMATED_ROTATION_NOT_APPLIED")
    if float(np.percentile(residual, 90)) > 2 * threshold: warnings.append("ROTATION_FREE_RESIDUAL_HIGH")
    if not .25 <= factor <= 4: warnings.append("EXTREME_SCALE")
    _need(_sha(reference["path"]) == reference["sha256"] and _sha(target["path"]) == target["sha256"],
          "FEATURE_SOURCE_CHANGED_DURING_MATCH")
    return {**base, "uniform_scale_target_to_reference": round(factor, 8),
            "translation_reference_source_pixels": [round(float(x), 3) for x in trans],
            "estimated_rotation_degrees_not_applied": round(rotation, 5),
            "mutual_matches": len(matches), "inliers": count, "inlier_fraction": round(fraction, 5),
            "target_region_inlier_extent_fraction_xy": [round(x, 5) for x in coverage],
            "median_rotation_free_residual_pixels": round(float(np.median(residual)), 3),
            "p90_rotation_free_residual_pixels": round(float(np.percentile(residual, 90)), 3),
            "ransac_threshold_reference_pixels": round(threshold, 3), "warnings": warnings,
            "interpretation": "Texture correspondence only; verify the selected body regions and motion manually."}
