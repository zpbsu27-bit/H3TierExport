"""Losslessly fit town-building masters to integer multiples of official DEF crops.

This step never resamples or edits original masters. It crops only fully transparent
outer margins, then copies every remaining RGBA pixel unchanged into an exact integer
multiple of the official crop canvas. The native H3TierExport expansion engine performs
the only resampling step (linear-light, premultiplied-alpha H3 quality) directly to 3X.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from collections import Counter, defaultdict

from PIL import Image

RESOURCE_IDENTITIES = tuple("""
TBCSMAGE TBCSDOCK TBTWMAG3 TBTWHAL3 TBTWHAL4 TBTWSILO TBTWBLAK
TBTWEXT0 TBTWEXT1 TBTWEXT2 TBNCHAL2 TBINEXT0 TBINDW_4 TBINDW_5
TBINUP_6 TBDNHAL2 TBDNMARK TBDNEXT0 TBSTEXT1 TBFRHAL4 TBFRBOAT
TBFREXT0 TBFRDW_1 TBELMAGE TBELMAG2 TBELMAG4 TBELTVRN TBELDOCK
TBELCSTL TBELHALL TBELHAL2 TBELHAL3 TBELEXT6
""".split())
BATCH_SCHEMA = "h3.master-expand.batch.v1"
BATCH_ALGORITHM = "linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4"
FRAME_NAME = re.compile(r"^(\d+)_(\d+)$")
MAX_AXIS = 32768
MAX_PIXELS = 100_000_000
CORE_SHRINK_BLOCK_FRACTION = 0.5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def alpha_bbox(image: Image.Image, threshold: int = 1) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A")
    if threshold > 1:
        alpha = alpha.point(lambda value: 255 if value >= threshold else 0)
    return alpha.getbbox()


def bbox_size(box: tuple[int, int, int, int]) -> tuple[int, int]:
    return box[2] - box[0], box[3] - box[1]


def native_frame_path(identity: str, filename: str, complete_root: Path,
                       archive_root: Path) -> Path:
    candidates = [archive_root / identity / filename, complete_root / identity / filename]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise FileNotFoundError(f"OFFICIAL_NATIVE_FRAME_MISSING: {identity}/{filename}")
    if len(existing) > 1 and sha256(existing[0]) != sha256(existing[1]):
        raise ValueError(f"OFFICIAL_NATIVE_FRAME_AMBIGUOUS: {identity}/{filename}")
    return existing[0]


def frame_key(identity: str, group: int, frame: int) -> tuple[str, int, int]:
    return identity.upper(), group, frame


def load_inventory(path: Path, expected_sha: str) -> tuple[dict, dict[tuple, dict]]:
    actual = sha256(path)
    if actual != expected_sha.lower():
        raise ValueError("NATIVE_INVENTORY_SHA256_CHANGED")
    catalog = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8-sig"))
    if not isinstance(catalog.get("rows"), list):
        raise ValueError("NATIVE_INVENTORY_ROWS_MISSING")
    rows: dict[tuple, dict] = {}
    for row in catalog["rows"]:
        if str(row.get("kind", "")).lower() != "def":
            continue
        identity = str(row.get("identity", "")).upper()
        if row.get("group") is None or row.get("frame") is None:
            continue
        key = frame_key(identity, int(row["group"]), int(row["frame"]))
        if key in rows:
            raise ValueError(f"DUPLICATE_NATIVE_INVENTORY_SLOT: {key}")
        rows[key] = row
    return catalog, rows


def _pair(row: dict, key: str) -> tuple[int, int]:
    value = row.get(key)
    if (not isinstance(value, list) or len(value) != 2
            or any(type(v) is not int or v <= 0 for v in value)):
        raise ValueError(f"INVALID_NATIVE_{key.upper()}: {row.get('identity')}")
    return value[0], value[1]


def _anchor(row: dict) -> tuple[int, int]:
    value = row.get("anchor")
    if (not isinstance(value, list) or len(value) != 2
            or any(type(v) is not int or v < 0 for v in value)):
        raise ValueError(f"INVALID_NATIVE_ANCHOR: {row.get('identity')}")
    return value[0], value[1]


def _official_core_bbox(native_path: Path, canvas: tuple[int, int],
                        crop: tuple[int, int], anchor: tuple[int, int]) -> tuple[tuple[int, int, int, int], str]:
    with Image.open(native_path) as source:
        if source.format != "PNG":
            raise ValueError(f"OFFICIAL_NATIVE_NOT_PNG: {native_path}")
        native = source.convert("RGBA")
        if native.size != canvas:
            raise ValueError(f"OFFICIAL_NATIVE_CANVAS_MISMATCH: {native_path}: {native.size} != {canvas}")
        x, y = anchor
        w, h = crop
        if x + w > native.width or y + h > native.height:
            raise ValueError(f"OFFICIAL_NATIVE_CROP_OUT_OF_BOUNDS: {native_path}")
        official_crop = native.crop((x, y, x + w, y + h))
        box = alpha_bbox(official_crop, 16)
        mode = "ALPHA_GE_16"
        if box is None:
            box = alpha_bbox(official_crop, 1)
            mode = "ALPHA_GT_0_FALLBACK"
        if box is None:
            box = (0, 0, w, h)
            mode = "FULL_CROP_FALLBACK"
        elif box == (0, 0, w, h):
            # An opaque extracted LOD frame has no alpha-derived baseline cue.
            # Do not pretend its bottom edge is the building's contact point.
            mode = "FULL_CROP_OPAQUE_NO_ALPHA_ANCHOR"
        official_crop.close()
        native.close()
        return box, mode


def _fit_registration(preserve_box: tuple[int, int, int, int],
                      core_box: tuple[int, int, int, int],
                      official_core_box: tuple[int, int, int, int],
                      crop: tuple[int, int], min_multiple: int,
                      alignment_mode: str = "ALPHA_GE_16",
                      max_multiple: int = 64) -> tuple[int, int, int, int]:
    preserve_w, preserve_h = bbox_size(preserve_box)
    core_cx = ((core_box[0] + core_box[2]) / 2) - preserve_box[0]
    core_cy = ((core_box[1] + core_box[3]) / 2) - preserve_box[1]
    core_bottom = core_box[3] - preserve_box[1]
    official_cx = (official_core_box[0] + official_core_box[2]) / 2
    official_cy = (official_core_box[1] + official_core_box[3]) / 2
    official_bottom = official_core_box[3]
    crop_w, crop_h = crop
    first = max(min_multiple, math.ceil(preserve_w / crop_w), math.ceil(preserve_h / crop_h))
    for multiple in range(first, max_multiple + 1):
        canvas_w, canvas_h = crop_w * multiple, crop_h * multiple
        if alignment_mode.startswith("FULL_CROP"):
            # Extracted native LOD frames may be opaque, so alpha cannot identify
            # a trustworthy ground baseline. Center the redraw's core in the crop.
            left = int(round(official_cx * multiple - core_cx))
            top = int(round(official_cy * multiple - core_cy))
        else:
            left = int(round(official_cx * multiple - core_cx))
            top = int(round(official_bottom * multiple - core_bottom))
        if (left >= 0 and top >= 0 and left + preserve_w <= canvas_w
                and top + preserve_h <= canvas_h):
            return multiple, left, top, 0
    raise ValueError("OFFICIAL_BASELINE_ALIGNMENT_CANNOT_FIT_WITHIN_64X")


def prepare(inventory: Path, inventory_sha256: str, masters_root: Path,
            complete_frames_root: Path, native_archive_root: Path,
            outdir: Path, identities: tuple[str, ...] = RESOURCE_IDENTITIES,
            min_3x: int = 3) -> dict:
    inventory = inventory.resolve(strict=True)
    masters_root = masters_root.resolve(strict=True)
    complete_frames_root = complete_frames_root.resolve(strict=True)
    native_archive_root = native_archive_root.resolve(strict=True)
    outdir = outdir.resolve()
    if outdir.exists():
        raise FileExistsError("TOWN_MASTER_PREPARE_OUTDIR_ALREADY_EXISTS; choose a new operation root")
    if min_3x != 3:
        raise ValueError("CURRENT_TOWN_MASTER_EXPORT_TARGET_IS_EXACTLY_3X")
    selected = tuple(dict.fromkeys(name.upper() for name in identities))
    if not selected:
        raise ValueError("NO_TOWN_RESOURCE_IDENTITIES_SELECTED")
    catalog, inventory_rows = load_inventory(inventory, inventory_sha256)
    expected = {key for key in inventory_rows if key[0] in selected}
    if not expected:
        raise ValueError("NO_SELECTED_DEF_SLOTS_IN_NATIVE_INVENTORY")

    source_files: dict[tuple, Path] = {}
    unmatched: list[str] = []
    duplicates: list[str] = []
    for path in sorted(masters_root.rglob("*.png")):
        identity = path.parent.name.upper()
        if identity not in selected:
            continue
        match = FRAME_NAME.fullmatch(path.stem)
        if not match:
            unmatched.append(str(path))
            continue
        key = frame_key(identity, int(match.group(1)), int(match.group(2)))
        if key not in expected:
            unmatched.append(str(path))
            continue
        if key in source_files:
            duplicates.append(f"{key}: {source_files[key]} | {path}")
            continue
        source_files[key] = path.resolve(strict=True)
    missing = sorted(expected - set(source_files))
    if missing or duplicates:
        raise ValueError(json.dumps({"code": "TOWN_MASTER_SLOT_COVERAGE_BLOCKED",
                                     "missing_slots": [list(key) for key in missing],
                                     "duplicate_slots": duplicates,
                                     "unmatched_files": unmatched}, ensure_ascii=False))

    # Freeze and validate all source/official bindings before creating any output.
    frozen: list[dict] = []
    for key in sorted(expected):
        identity, group, frame = key
        source_path = source_files[key]
        row = inventory_rows[key]
        crop = _pair(row, "crop")
        canvas = _pair(row, "canvas")
        anchor = _anchor(row)
        official_path = native_frame_path(identity, f"{group}_{frame}.png",
                                           complete_frames_root, native_archive_root)
        source_sha = sha256(source_path)
        official_sha = sha256(official_path)
        with Image.open(source_path) as master_file:
            if master_file.format != "PNG":
                raise ValueError(f"MASTER_NOT_PNG: {source_path}")
            master = master_file.convert("RGBA")
            preserve_box = alpha_bbox(master, 1)
            if preserve_box is None:
                raise ValueError(f"MASTER_ALPHA_EMPTY: {source_path}")
            core_box = alpha_bbox(master, 16) or preserve_box
            preserve_w, preserve_h = bbox_size(preserve_box)
            core_w, core_h = bbox_size(core_box)
            icc_profile = master_file.info.get("icc_profile")
            dpi = master_file.info.get("dpi")
            master_pixels = list(master.size)
            # Save pixel data and metadata only; the original master remains read-only.
            preserved = master.crop(preserve_box)
            master.close()
        official_core, official_alignment_mode = _official_core_bbox(official_path, canvas, crop, anchor)
        official_core_w, official_core_h = bbox_size(official_core)
        detail_x = core_w / crop[0]
        detail_y = core_h / crop[1]
        detail_min = min(detail_x, detail_y)
        enough_3x = core_w >= crop[0] * 3 and core_h >= crop[1] * 3
        record = {"resource": identity, "group": group, "frame": frame,
                  "master_path": str(source_path), "master_sha256": source_sha,
                  "master_pixels": master_pixels, "preserve_alpha_bbox": list(preserve_box),
                  "preserve_pixels": [preserve_w, preserve_h], "core_alpha_threshold": 16,
                  "core_alpha_bbox": list(core_box), "core_pixels": [core_w, core_h],
                  "official_native_path": str(official_path), "official_native_sha256": official_sha,
                  "official_canvas_pixels": list(canvas), "official_crop_pixels": list(crop),
                  "official_anchor": list(anchor), "official_core_bbox": list(official_core),
                  "official_core_pixels": [official_core_w, official_core_h],
                  "official_alignment_mode": official_alignment_mode,
                  "detail_multiple_x": round(detail_x, 4), "detail_multiple_y": round(detail_y, 4),
                  "effective_detail_multiple": round(detail_min, 4),
                  "required_3x_pixels": [crop[0] * 3, crop[1] * 3],
                  "required_core_pixels_3x": [official_core_w * 3, official_core_h * 3],
                  "status": "READY_3X" if enough_3x else "API_REQUIRED_BELOW_3X"}
        record["_preserved_image"] = preserved
        if enough_3x:
            multiple, left, top, _ = _fit_registration(preserve_box, core_box, official_core,
                                                        crop, 3, official_alignment_mode)
            normalized_size = [crop[0] * multiple, crop[1] * multiple]
            projected_core = [core_w * 3 / multiple, core_h * 3 / multiple]
            record.update({"fit_canvas_multiple": multiple,
                           "normalized_pixels": normalized_size,
                           "normalized_offset": [left, top],
                           "projected_core_pixels_3x": [round(value, 4) for value in projected_core],
                           "projected_core_fraction_of_official_3x": [
                               round(projected_core[0] / max(1, official_core_w * 3), 4),
                               round(projected_core[1] / max(1, official_core_h * 3), 4)]})
            core_fractions = [projected_core[0] / max(1, official_core_w * 3),
                              projected_core[1] / max(1, official_core_h * 3)]
            if min(core_fractions) < CORE_SHRINK_BLOCK_FRACTION:
                record["status"] = "BLOCKED_CORE_SHRINK_BELOW_HALF_OF_OFFICIAL_3X"
                record["error"] = ("PRESERVING_ALL_POSITIVE_ALPHA_WOULD_SHRINK_THE_NATIVE_CORE_BELOW_HALF_3X: "
                                   f"projected={record['projected_core_pixels_3x']}, "
                                   f"required={record['required_core_pixels_3x']}; "
                                   "remove or redraw the extraneous alpha in the source, never stretch the core")
            else:
                if min(core_fractions) < 1.0 - 1e-9:
                    record["status"] = "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW"
                    record["review_required"] = True
                    record["warning"] = ("PRESERVED_ALPHA_EXTENT_REDUCES_THE_NATIVE_CORE_BELOW_3X: "
                                         f"fraction={record['projected_core_fraction_of_official_3x']}; "
                                         "candidate output requires actual 3X visual review before promotion")
                if (normalized_size[0] > MAX_AXIS or normalized_size[1] > MAX_AXIS
                    or normalized_size[0] * normalized_size[1] > MAX_PIXELS):
                    record["status"] = "BLOCKED_NORMALIZED_CANVAS_LIMIT"
                    record["error"] = "NORMALIZED_CANVAS_EXCEEDS_H3TIEREXPORT_LIMIT"
                else:
                    relative = Path(identity) / f"{group}_{frame}_{multiple}X.png"
                    normalized_path = outdir / "normalized-masters" / relative
                    output_path = outdir / "candidate-3x" / identity / f"{group}_{frame}_3X.png"
                    record.update({"normalized_path": str(normalized_path),
                                   "candidate_3x_path": str(output_path)})
                    record["_icc_profile"] = icc_profile
                    record["_dpi"] = dpi
        else:
            record["shortfall_pixels_to_3x"] = [max(0, crop[0] * 3 - core_w),
                                                max(0, crop[1] * 3 - core_h)]
            record["api_action"] = "REGENERATE_OR_ENHANCE_MASTER_TO_AT_LEAST_3X_PER_AXIS; PRESERVE_ASPECT; DO_NOT_STRETCH"
        frozen.append(record)

    outdir.mkdir(parents=True, exist_ok=False)
    normalized_root = outdir / "normalized-masters"
    candidate_root = outdir / "candidate-3x"
    normalized_root.mkdir()
    candidate_root.mkdir()
    (outdir / "reports").mkdir()
    batch_items = []
    frame_rows = []
    api_rows = []
    blocked_rows = []
    try:
        for record in frozen:
            image = record.pop("_preserved_image", None)
            icc_profile = record.pop("_icc_profile", None)
            dpi = record.pop("_dpi", None)
            if record["status"] in {"READY_3X", "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW"}:
                identity, group, frame = record["resource"], record["group"], record["frame"]
                normalized_path = Path(record["normalized_path"])
                normalized_path.parent.mkdir(parents=True, exist_ok=True)
                normalized = Image.new("RGBA", tuple(record["normalized_pixels"]), (0, 0, 0, 0))
                # Paste copies RGBA values; no resize, interpolation, alpha blending or color transform.
                normalized.paste(image, tuple(record["normalized_offset"]))
                save_kwargs = {"format": "PNG", "optimize": False, "compress_level": 9}
                if icc_profile:
                    save_kwargs["icc_profile"] = icc_profile
                if dpi:
                    save_kwargs["dpi"] = dpi
                normalized.save(normalized_path, **save_kwargs)
                normalized.close()
                image.close()
                normalized_sha = sha256(normalized_path)
                record["normalized_sha256"] = normalized_sha
                crop = record["official_crop_pixels"]
                batch_items.append({"id": f"{identity}_{group}_{frame}",
                                    "source": str(normalized_path),
                                    "source_sha256": normalized_sha,
                                    "source_pixels": record["normalized_pixels"],
                                    "base_pixels": crop, "target_scale": 3,
                                    "output": record["candidate_3x_path"],
                                    "padded_master_allowed": True})
            elif record["status"] == "API_REQUIRED_BELOW_3X":
                if image is not None:
                    image.close()
                api_rows.append({"resource": record["resource"], "group": record["group"],
                                 "frame": record["frame"], "source": record["master_path"],
                                 "source_sha256": record["master_sha256"],
                                 "core_pixels": record["core_pixels"],
                                 "required_3x_pixels": record["required_3x_pixels"],
                                 "shortfall_pixels": record["shortfall_pixels_to_3x"],
                                 "effective_detail_multiple": record["effective_detail_multiple"],
                                 "action": record["api_action"]})
            else:
                if image is not None:
                    image.close()
                blocked_rows.append({"resource": record["resource"], "group": record["group"],
                                     "frame": record["frame"], "source": record["master_path"],
                                     "status": record["status"], "error": record.get("error", "UNKNOWN")})
            frame_rows.append(record)
    except Exception:
        # The output root belongs exclusively to this new operation; preserve it for diagnosis.
        raise

    for record in frozen:
        if sha256(Path(record["master_path"])) != record["master_sha256"]:
            raise RuntimeError(f"ORIGINAL_MASTER_CHANGED_DURING_PREPARE: {record['master_path']}")
        if sha256(Path(record["official_native_path"])) != record["official_native_sha256"]:
            raise RuntimeError(f"OFFICIAL_NATIVE_CHANGED_DURING_PREPARE: {record['official_native_path']}")

    output_root = candidate_root.resolve()
    batch = {"schema": BATCH_SCHEMA, "algorithm": BATCH_ALGORITHM,
             "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "output_root": str(output_root), "receipt_directory": str(output_root / "receipts"),
             "settings": {"adapter": -1, "backend": "cpu", "filter": "h3-quality",
                          "gpu_memory_mib": 0, "memory_gib": 64, "png_filter": "adaptive",
                          "space": "linear", "workers": 32},
             "items": batch_items,
             "note": "Lossless alpha crop and transparent padding only. Normalized inputs are exact integer multiples of each official DEF crop. H3TierExport performs the only resampling directly to 3X. Original masters and official native sources remain unchanged. WARNING frames produce candidates for manual review only and are not art-approved."}
    if batch_items:
        (outdir / "expand-batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    warning_rows = [r for r in frame_rows if r["status"] == "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW"]
    warning_count = len(warning_rows)
    summary = {"schema": "h3.town-master-prepare.report.v1",
               "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "inventory": str(inventory), "inventory_sha256": inventory_sha256.lower(),
               "masters_root": str(masters_root), "selected_identities": list(selected),
               "resource_count": len({r["resource"] for r in frame_rows}), "frame_count": len(frame_rows),
               "ready_3x": len(batch_items), "warning_3x": warning_count,
               "api_required_below_3x": len(api_rows),
               "blocked": len(blocked_rows),
               "minimum_effective_detail_multiple": min((r["effective_detail_multiple"] for r in frame_rows), default=None),
               "median_effective_detail_multiple": (statistics.median(r["effective_detail_multiple"] for r in frame_rows)
                   if frame_rows else None),
               "maximum_effective_detail_multiple": max((r["effective_detail_multiple"] for r in frame_rows), default=None),
               "fit_canvas_multiple_counts": dict(sorted(Counter(str(r.get("fit_canvas_multiple")) for r in frame_rows
                                                                  if r["status"] in {"READY_3X", "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW"}).items(), key=lambda x:int(x[0]))),
               "borderline_below_4x": sum(r["effective_detail_multiple"] < 4 for r in frame_rows),
               "resampling_steps_before_h3tierexport": 0, "original_master_files_modified": 0,
               "official_native_files_modified": 0, "art_approved": False, "game_deployed": False,
               "runtime_accepted": False,
               "status": "PREPARED_WITH_REVIEW_GATES" if batch_items and
                         (blocked_rows or api_rows or warning_count) else
                         "PREPARED_3X_BATCH" if batch_items else
                         "BLOCKED_NO_3X_READY_FRAMES" if blocked_rows else "NO_3X_ELIGIBLE_FRAMES"}
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (outdir / "frame-audit.json").write_text(json.dumps({"schema": "h3.town-master-prepare.frames.v1",
                                                          "frames": frame_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (outdir / "frame-audit.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        fieldnames = sorted({key for row in frame_rows for key in row if not key.startswith("_")})
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(frame_rows)
    with (outdir / "api-needed.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["resource", "group", "frame", "source", "source_sha256", "core_w", "core_h",
                         "required_3x_w", "required_3x_h", "shortfall_w", "shortfall_h",
                         "effective_detail_multiple", "action"])
        for row in api_rows:
            writer.writerow([row["resource"], row["group"], row["frame"], row["source"], row["source_sha256"],
                             *row["core_pixels"], *row["required_3x_pixels"], *row["shortfall_pixels"],
                             row["effective_detail_multiple"], row["action"]])
    with (outdir / "blocked.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["resource", "group", "frame", "source", "status", "error"])
        writer.writeheader()
        writer.writerows(blocked_rows)
    with (outdir / "review-required.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["resource", "group", "frame", "source", "status",
                         "projected_core_pixels_3x", "required_core_pixels_3x", "warning"])
        for row in warning_rows:
            writer.writerow([row["resource"], row["group"], row["frame"], row["master_path"], row["status"],
                             "x".join(map(str, row["projected_core_pixels_3x"])),
                             "x".join(map(str, row["required_core_pixels_3x"])), row["warning"]])
    with (outdir / "resource-summary.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["resource", "frames", "ready_3x_candidates", "warning_3x", "api_required", "blocked",
                         "minimum_detail_multiple", "maximum_fit_canvas_multiple"])
        by_resource: dict[str, list[dict]] = defaultdict(list)
        for row in frame_rows:
            by_resource[row["resource"]].append(row)
        for identity, rows in sorted(by_resource.items()):
            writer.writerow([identity, len(rows), sum(r["status"] in {"READY_3X", "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW"} for r in rows),
                             sum(r["status"] == "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW" for r in rows),
                             sum(r["status"] == "API_REQUIRED_BELOW_3X" for r in rows),
                             sum(r["status"].startswith("BLOCKED") for r in rows),
                             min(r["effective_detail_multiple"] for r in rows),
                             max((r.get("fit_canvas_multiple", 0) for r in rows), default=0)])
    (outdir / "README.txt").write_text(
        "H3 town master normalization\n\n"
        f"Frames: {len(frame_rows)}; 3X-ready: {len(batch_items)}; API-required: {len(api_rows)}; blocked: {len(blocked_rows)}.\n\n"
        "The normalized-masters are exact integer multiples of the official DEF crop. Preparation crops only fully transparent outer margins and copies every remaining RGBA pixel unchanged onto a transparent canvas; it does not resize or stretch artwork.\n"
        "The effective detail multiple is measured per axis from alpha>=16 content against the official crop. A frame below 3X on either axis is excluded from the expansion batch and listed in api-needed.csv.\n\n"
        "After reviewing summary.json and frame-audit.csv, run:\n"
        "  H3TierExport.exe expand-check --batch expand-batch.json --report reports\\expand-check.json\n"
        "  H3TierExport.exe expand --batch expand-batch.json --report reports\\expand-run.json\n"
        "  H3TierExport.exe expand-verify --batch expand-batch.json --report reports\\expand-verify.json\n\n"
        "H3TierExport performs the only resampling step using h3-quality in linear light with premultiplied alpha, directly to exact official crop x3. Original masters and official native frames are read-only. Outputs are candidates, not approved or deployed assets.\n",
        encoding="utf-8")
    return {"status": summary["status"], "summary": str(outdir / "summary.json"),
            "batch": str(outdir / "expand-batch.json") if batch_items else None,
            "frames": len(frame_rows), "ready_3x": len(batch_items), "warning_3x": warning_count,
            "api_required": len(api_rows), "blocked": len(blocked_rows),
            "normalized_masters": len(batch_items), "original_master_files_modified": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepare", nargs="?", choices=["prepare"], default="prepare")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--masters-root", type=Path, required=True)
    parser.add_argument("--complete-frames-root", type=Path, required=True)
    parser.add_argument("--native-archive-root", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--identity", action="append", default=None,
                        help="DEF identity to include; repeatable. Defaults to the 33 town resources audited in this workflow.")
    parser.add_argument("--min-3x", type=int, default=3)
    args = parser.parse_args()
    result = prepare(args.inventory, args.inventory_sha256, args.masters_root,
                     args.complete_frames_root, args.native_archive_root, args.outdir,
                     tuple(args.identity) if args.identity else RESOURCE_IDENTITIES, args.min_3x)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
