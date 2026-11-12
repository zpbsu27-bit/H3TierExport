"""Inspect artwork against built-in 1X map-flag anchors and measure pole landmarks.

The tool never edits an input image. Official flag anchors are automatic; the
artwork's pole head/foot are semantic landmarks, so the review page asks for two
quick clicks per pole instead of guessing from decorative architecture.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import sys
from typing import Any

from PIL import Image


def default_reference_path() -> Path:
    module_dir = Path(__file__).resolve().parent
    source_path = module_dir / "data" / "flag_pole_reference_1999.json.gz"
    frozen_path = module_dir / "scripts" / "data" / "flag_pole_reference_1999.json.gz"
    return source_path if source_path.is_file() or not frozen_path.is_file() else frozen_path


REFERENCE = default_reference_path()
MANIFEST_SCHEMA = "h3.flag-pole-audit-manifest.v1"
LAYOUT_SCHEMA = "h3.flag-pole-audit-layout.v1"
IDENTITY_RE = re.compile(r"^[A-Z0-9_]{3,24}$")
SCALE_MAX = 64
MAX_ITEMS = 512


class AuditError(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_reference(path: Path = REFERENCE) -> tuple[dict[str, dict], str, dict]:
    need(path.is_file(), "FLAG_REFERENCE_DATABASE_MISSING")
    raw = path.read_bytes()
    document = json.loads(gzip.decompress(raw))
    need(document.get("schema") == "h3.flag-pole-reference-1999.v2", "FLAG_REFERENCE_SCHEMA_UNSUPPORTED")
    rows = document.get("entries")
    need(isinstance(rows, list) and rows, "FLAG_REFERENCE_ENTRIES_INVALID")
    table: dict[str, dict] = {}
    for row in rows:
        identity = row.get("identity")
        need(isinstance(identity, str) and IDENTITY_RE.fullmatch(identity) is not None,
             "FLAG_REFERENCE_IDENTITY_INVALID")
        need(identity not in table, f"FLAG_REFERENCE_IDENTITY_DUPLICATE:{identity}")
        frames = row.get("frames")
        need(isinstance(frames, list), f"NATIVE_FRAMES_INVALID:{identity}")
        slots = set()
        for frame in frames:
            group, index = frame.get("group"), frame.get("frame")
            need(type(group) is int and group >= 0 and type(index) is int and index >= 0
                 and (group, index) not in slots, f"NATIVE_FRAME_SLOT_INVALID:{identity}")
            slots.add((group, index))
            for key in ("canvas_1x", "crop_1x", "origin_1x"):
                value = frame.get(key)
                need(isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value),
                     f"NATIVE_FRAME_GEOMETRY_INVALID:{identity}:{key}")
            flags = frame.get("flags")
            need(isinstance(flags, dict) and isinstance(flags.get("anchors"), list)
                 and isinstance(flags.get("partial_flags"), list), f"NATIVE_FLAG_DATA_INVALID:{identity}")
            for anchor in flags["anchors"]:
                edge = anchor.get("attachment_edge_pixel_1x")
                need(type(anchor.get("x")) is int and type(anchor.get("y")) is int
                     and type(anchor.get("mirrored")) is bool and isinstance(edge, list)
                     and len(edge) == 2 and all(type(v) is int and v >= 0 for v in edge),
                     f"FLAG_REFERENCE_ANCHOR_INVALID:{identity}")
        table[identity] = row
    provenance = document.get("provenance")
    need(isinstance(provenance, dict) and re.fullmatch(r"[0-9a-f]{64}",
         str(provenance.get("archive_sha256", ""))) is not None, "NATIVE_ARCHIVE_PROVENANCE_INVALID")
    return table, sha_bytes(raw), document


def missing_anchor_error(identity: str, reference_doc: dict) -> str:
    return f"NO_1999_DEF_IDENTITY:{identity}"


def native_flag_edge_candidates(anchor: dict) -> list[list[int]]:
    # A visible flag-edge pixel is evidence from the 1999 DEF, not a proven pole tip.
    return [anchor["attachment_edge_pixel_1x"]]


def selected_frame(row: dict, group: int, frame: int) -> dict:
    need(type(group) is int and group >= 0 and type(frame) is int and frame >= 0,
         f"{row['identity']}:NATIVE_SLOT_INVALID")
    found = [item for item in row["frames"] if item["group"] == group and item["frame"] == frame]
    need(len(found) == 1, f"{row['identity']}:NATIVE_SLOT_NOT_FOUND:{group}_{frame}")
    return found[0]


def image_metadata(path: Path) -> dict:
    need(path.is_absolute() and path.is_file(), f"IMAGE_NOT_FOUND:{path}")
    raw_sha = sha(path)
    try:
        with Image.open(path) as image:
            need(image.format == "PNG", f"IMAGE_NOT_PNG:{path.name}")
            size, mode = image.size, image.mode
            image.verify()
    except (OSError, ValueError) as error:
        raise AuditError(f"IMAGE_READ_FAILED:{path}:{error}") from error
    need(size[0] > 0 and size[1] > 0 and size[0] <= 32768 and size[1] <= 32768,
         f"IMAGE_DIMENSIONS_INVALID:{path.name}")
    return {"image_path": str(path.resolve()), "image_sha256": raw_sha,
            "image_pixels": [size[0], size[1]], "image_mode": mode}


def parse_ratio(value: Any, identity: str) -> tuple[int, int]:
    if value == "auto" or value is None:
        return 1, 1
    if isinstance(value, dict):
        n, d = value.get("numerator"), value.get("denominator")
    elif isinstance(value, list) and len(value) == 2:
        n, d = value
    elif type(value) is int:
        n, d = value, 1
    else:
        raise AuditError(f"{identity}:SCALE_INVALID")
    need(type(n) is int and type(d) is int and 1 <= n <= SCALE_MAX and 1 <= d <= SCALE_MAX,
         f"{identity}:SCALE_INVALID")
    from math import gcd
    common = gcd(n, d)
    return n // common, d // common


def infer_ratio(item: dict, geometry: dict) -> tuple[int, int, str, list[int]]:
    canvas, crop, crop_origin = (geometry.get("canvas_1x"), geometry.get("crop_1x"),
                                 geometry.get("origin_1x"))
    pixels = item["image_pixels"]
    if geometry.get("geometry_status") == "VALID":
        matches = []
        for k in range(1, SCALE_MAX + 1):
            if pixels == [canvas[0] * k, canvas[1] * k]:
                matches.append((k, 1, "exact_native_canvas_integer_multiple", [0, 0]))
            if pixels == [crop[0] * k, crop[1] * k]:
                candidate = (k, 1, "exact_native_crop_integer_multiple", [-crop_origin[0] * k, -crop_origin[1] * k])
                if not any((m[0], m[1], m[3]) == (candidate[0], candidate[1], candidate[3]) for m in matches):
                    matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
    return 1, 1, "registration_required", [0, 0]


def checked_manifest(path: Path, expected_sha: str, reference: dict[str, dict], reference_sha: str) -> dict:
    need(path.is_absolute() and path.is_file(), "MANIFEST_NOT_FOUND")
    need(sha(path) == expected_sha.lower(), "MANIFEST_SHA_MISMATCH")
    document = json.loads(path.read_text(encoding="utf-8"))
    need(document.get("schema") == MANIFEST_SCHEMA, "MANIFEST_SCHEMA_UNSUPPORTED")
    need(document.get("reference_database_sha256") == reference_sha, "REFERENCE_DATABASE_SHA_MISMATCH")
    values = document.get("items")
    need(isinstance(values, list) and 1 <= len(values) <= MAX_ITEMS, "MANIFEST_ITEMS_INVALID")
    seen: set[tuple[str, int, int]] = set()
    prepared = []
    for value in values:
        identity = value.get("identity")
        need(isinstance(identity, str) and IDENTITY_RE.fullmatch(identity) is not None,
             "MANIFEST_IDENTITY_INVALID")
        need(identity in reference, missing_anchor_error(identity, document))
        native_group, native_frame = value.get("group", 0), value.get("frame", 0)
        native = selected_frame(reference[identity], native_group, native_frame)
        need(native["geometry_status"] == "VALID", f"{identity}:NATIVE_GEOMETRY_UNRESOLVED")
        slot_key = (identity, native_group, native_frame)
        need(slot_key not in seen, f"MANIFEST_SLOT_DUPLICATE:{identity}:{native_group}_{native_frame}")
        seen.add(slot_key)
        path_value = value.get("image_path")
        need(isinstance(path_value, str), f"{identity}:IMAGE_PATH_REQUIRED")
        image_path = Path(path_value)
        metadata = image_metadata(image_path)
        need(metadata["image_sha256"] == str(value.get("image_sha256", "")).lower(),
             f"{identity}:IMAGE_SHA_MISMATCH")
        scale_n, scale_d = parse_ratio(value.get("pixels_per_1x"), identity)
        origin = value.get("native_origin_px", [0, 0])
        need(isinstance(origin, list) and len(origin) == 2
             and all(type(v) is int and abs(v) <= 32768 for v in origin),
             f"{identity}:NATIVE_ORIGIN_INVALID")
        registration = value.get("registration_status", "explicit_manifest_registration")
        if registration.startswith("exact_native_"):
            inferred_n, inferred_d, inferred_status, inferred_origin = infer_ratio(metadata, native)
            need((scale_n, scale_d, registration, origin) ==
                 (inferred_n, inferred_d, inferred_status, inferred_origin),
                 f"{identity}:NATIVE_REGISTRATION_MISMATCH")
        group = value.get("length_group", identity)
        need(isinstance(group, str) and 1 <= len(group) <= 64, f"{identity}:LENGTH_GROUP_INVALID")
        target = value.get("target_length_1x")
        need(target is None or (type(target) in (int, float) and math.isfinite(target) and 0 < target <= 4096),
             f"{identity}:TARGET_LENGTH_INVALID")
        item = {**value, **metadata, "scale_num": scale_n, "scale_den": scale_d,
                "registration_status": registration,
                "native_origin_px": origin, "length_group": group,
                "target_length_1x": target,
                "group": native_group, "frame": native_frame,
                "anchors": native["flags"]["anchors"], "flag_status": native["flags"]["status"],
                "partial_flags": native["flags"]["partial_flags"],
                "anchor_source": "1999 original DEF palette index 5",
                "native_geometry": {k: v for k, v in native.items() if k != "flags"},
                "native_def_sha256": reference[identity]["source_def_sha256"]}
        prepared.append(item)
    document["items"] = prepared
    return document


def freeze_images(images: list[str], identity: str | None, expected_image_sha: str | None,
                  scale: str, length_group: str | None, target_length: float | None,
                  operation_id: str | None, outdir: Path, reference: dict[str, dict], reference_sha: str,
                  reference_doc: dict, item_overrides: list[dict] | None = None,
                  native_group: int | None = None, native_frame: int | None = None) -> dict:
    need(bool(images), "IMAGE_REQUIRED")
    need(outdir.is_absolute() and not outdir.exists(), "OUTPUT_MUST_BE_NEW_ABSOLUTE_PATH")
    if operation_id is not None:
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", operation_id) is not None,
             "OPERATION_ID_INVALID")
    rows = []
    seen = set()
    parsed_scale: Any = "auto" if scale == "auto" else [int(scale.partition("/")[0]), int(scale.partition("/")[2] or "1")]
    for position, supplied in enumerate(images):
        explicit_id, sep, raw_path = supplied.partition("=")
        source = Path(raw_path if sep else supplied)
        slot_name = re.fullmatch(r"([0-9]+)_([0-9]+)", source.stem)
        current_id = (explicit_id if sep else identity or (source.parent.name if slot_name else source.stem)).upper()
        need(IDENTITY_RE.fullmatch(current_id) is not None, f"IDENTITY_INVALID:{current_id}")
        need(current_id in reference, missing_anchor_error(current_id, reference_doc))
        info = image_metadata(source)
        if expected_image_sha and len(images) == 1:
            need(info["image_sha256"] == expected_image_sha.lower(), "IMAGE_SHA_MISMATCH")
        ref_row = reference[current_id]
        override = item_overrides[position] if item_overrides is not None else None
        group_id = override.get("group", 0) if override else (native_group if native_group is not None else
                   int(slot_name.group(1)) if slot_name else 0)
        frame_id = override.get("frame", 0) if override else (native_frame if native_frame is not None else
                   int(slot_name.group(2)) if slot_name else 0)
        native = selected_frame(ref_row, group_id, frame_id)
        need(native["geometry_status"] == "VALID", f"{current_id}:NATIVE_GEOMETRY_UNRESOLVED")
        slot_key = (current_id, group_id, frame_id)
        need(slot_key not in seen, f"SLOT_DUPLICATE:{current_id}:{group_id}_{frame_id}")
        seen.add(slot_key)
        if override:
            num, den = override["scale_num"], override["scale_den"]
            registration = override["registration_status"]
            native_origin = override["native_origin_px"]
            row_group = override["length_group"]
            row_target = override["target_length_1x"]
        elif parsed_scale == "auto":
            num, den, registration, native_origin = infer_ratio(info, native)
            row_group = length_group or ("towns" if current_id.startswith("AVC") else current_id)
            row_target = target_length
        else:
            num, den = parse_ratio(parsed_scale, current_id)
            registration = "explicit_scale_origin_zero"
            native_origin = [0, 0]
            row_group = length_group or ("towns" if current_id.startswith("AVC") else current_id)
            row_target = target_length
        rows.append({"identity": current_id, "group": group_id, "frame": frame_id,
                     "slot_key": f"{current_id}:{group_id}:{frame_id}", **info,
                     "pixels_per_1x": {"numerator": num, "denominator": den},
                     "scale_num": num, "scale_den": den,
                     "native_origin_px": native_origin, "registration_status": registration,
                     "length_group": row_group, "target_length_1x": row_target})
        rows[-1]["anchors"] = native["flags"]["anchors"]
        rows[-1]["flag_status"] = native["flags"]["status"]
        rows[-1]["partial_flags"] = native["flags"]["partial_flags"]
        rows[-1]["anchor_source"] = "1999 original DEF palette index 5"
        rows[-1]["native_geometry"] = {k: v for k, v in native.items() if k != "flags"}
        rows[-1]["native_def_sha256"] = ref_row["source_def_sha256"]
    manifest = {"schema": MANIFEST_SCHEMA, "operation_id": operation_id,
                "reference_database_sha256": reference_sha,
                "reference_database_provenance": reference_doc["provenance"],
                "length_baseline_policy": "explicit target, otherwise median of human-marked poles within each length_group",
                "items": rows,
                "claim_boundary": "1999 DEF crop origin, visible palette-5 flag-face bbox and inferred flag attachment-edge pixel are distinct. The flag edge does not prove a pole tip or length. No source image is changed."}
    outdir.parent.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=False)
    (outdir / "images").mkdir()
    copied_rows = []
    for row in rows:
        source = Path(row["image_path"])
        leaf = f"{row['identity']}_{row['group']}_{row['frame']}.png"
        destination = outdir / "images" / leaf
        with source.open("rb") as inp, destination.open("xb") as out:
            shutil.copyfileobj(inp, out)
        need(sha(destination) == row["image_sha256"], f"{row['identity']}:IMAGE_COPY_SHA_MISMATCH")
        copied_rows.append({**row, "image_path": str(destination), "image_url": "images/" + leaf})
    manifest["items"] = copied_rows
    manifest_path = outdir / "manifest.json"
    raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with manifest_path.open("xb") as stream:
        stream.write(raw); stream.flush()
    manifest_sha = sha_bytes(raw)
    payload = json.dumps({"manifest": manifest, "manifest_sha256": manifest_sha},
                         ensure_ascii=False).replace("</", "<\\/")
    (outdir / "index.html").write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    report = {"schema": "h3.flag-pole-audit-report.v1", "status": "MARKS_REQUIRED",
              "operation_id": operation_id, "manifest": str(manifest_path), "manifest_sha256": manifest_sha,
              "reference_database_sha256": reference_sha, "items": [
                  {"identity": row["identity"], "group": row["group"], "frame": row["frame"],
                   "source_sha256": row["image_sha256"], "native_geometry": row["native_geometry"],
                   "flag_status": row["flag_status"], "partial_flags": row["partial_flags"],
                   "image_pixels": row["image_pixels"], "scale": [row["scale_num"], row["scale_den"]],
                   "registration_status": row["registration_status"],
                   "official_anchors_1x": row["anchors"],
                   "native_flag_edge_candidates_1x": [native_flag_edge_candidates(a) for a in row["anchors"]],
                   "poles_measured": 0,
                   "position_delta": "NOT_MEASURED", "pole_length_delta": "NOT_MEASURED"}
                  for row in copied_rows],
              "html_review": str(outdir / "index.html"), "source_images_modified": 0,
              "masters_written": 0, "three_x_written": 0, "mod_written": 0, "game_tested": False}
    report_path = outdir / "audit-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "REVIEW_REQUIRED", "manifest": str(manifest_path), "manifest_sha256": manifest_sha,
            "html_review": str(outdir / "index.html"), "report": str(report_path),
            "items": len(copied_rows), "reference_identities": len(reference), "source_images_modified": 0}


def verify_layout(manifest_path: Path, manifest_sha: str, layout_path: Path, layout_sha: str,
                  outdir: Path, reference: dict[str, dict], reference_sha: str) -> dict:
    manifest = checked_manifest(manifest_path, manifest_sha, reference, reference_sha)
    need(layout_path.is_absolute() and layout_path.is_file(), "LAYOUT_NOT_FOUND")
    need(sha(layout_path) == layout_sha.lower(), "LAYOUT_SHA_MISMATCH")
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    need(layout.get("schema") == LAYOUT_SCHEMA and layout.get("manifest_sha256") == manifest_sha.lower(),
         "LAYOUT_MANIFEST_MISMATCH")
    layout_items = layout.get("items")
    need(isinstance(layout_items, list), "LAYOUT_ITEMS_INVALID")
    by_slot = {(row["identity"], row.get("group", 0), row.get("frame", 0)): row for row in layout_items}
    need(len(by_slot) == len(layout_items) and set(by_slot) ==
         {(x["identity"], x["group"], x["frame"]) for x in manifest["items"]}, "LAYOUT_SLOTS_INVALID")
    measured = []
    for item in manifest["items"]:
        annotation = by_slot[(item["identity"], item["group"], item["frame"])]
        need(annotation.get("image_sha256") == item["image_sha256"], f"{item['identity']}:LAYOUT_IMAGE_SHA_MISMATCH")
        scale_n, scale_d = parse_ratio(annotation.get("pixels_per_1x", [item["scale_num"], item["scale_den"]]), item["identity"])
        origin = annotation.get("native_origin_px", item["native_origin_px"])
        need(isinstance(origin, list) and len(origin) == 2
             and all(type(v) is int and abs(v) <= 32768 for v in origin),
             f"{item['identity']}:LAYOUT_ORIGIN_INVALID")
        points = annotation.get("poles")
        need(isinstance(points, list) and len(points) == len(item["anchors"]),
             f"{item['identity']}:POLE_COUNT_MISMATCH")
        if any(pair is not None for pair in points):
            need(annotation.get("registration_confirmed") is True, f"{item['identity']}:REGISTRATION_REQUIRED")
        with Image.open(item["image_path"]) as im:
            width, height = im.size
        for index, pair in enumerate(points):
            if pair is None:
                continue
            head, foot = pair.get("head_px"), pair.get("foot_px")
            for label, point in (("head", head), ("foot", foot)):
                need(isinstance(point, list) and len(point) == 2
                     and all(type(v) in (int, float) and math.isfinite(v) for v in point),
                     f"{item['identity']}:POLE_{index}_{label.upper()}_INVALID")
                need(0 <= point[0] < width and 0 <= point[1] < height,
                     f"{item['identity']}:POLE_{index}_{label.upper()}_OUT_OF_BOUNDS")
            need(math.dist(head, foot) > 0, f"{item['identity']}:POLE_{index}_ZERO_LENGTH")
            scale = scale_n / scale_d
            native_head = [(head[0] - origin[0]) / scale, (head[1] - origin[1]) / scale]
            native_foot = [(foot[0] - origin[0]) / scale, (foot[1] - origin[1]) / scale]
            official = item["anchors"][index]
            flag_edge = native_flag_edge_candidates(official)[0]
            target = annotation.get("target_length_1x", item["target_length_1x"])
            need(target is None or (type(target) in (int, float) and math.isfinite(target) and 0 < target <= 4096),
                 f"{item['identity']}:LAYOUT_TARGET_LENGTH_INVALID")
            observed_length = math.dist(native_head, native_foot)
            measured.append({"identity": item["identity"], "group": item["group"], "frame": item["frame"],
                             "anchor_index": index, "native_geometry": item["native_geometry"],
                             "native_def_sha256": item["native_def_sha256"],
                             "image_sha256": item["image_sha256"], "length_group": annotation.get("length_group", item["length_group"]),
                             "scale": [scale_n, scale_d], "native_origin_px": origin,
                             "native_flag_face_bbox_1x": official["bbox_1x"],
                             "native_flag_attachment_edge_pixel_1x": flag_edge,
                             "flag_edge_interpretation": "inferred flag attachment side; not a proven pole tip",
                             "observed_pole_head_1x": native_head, "observed_pole_foot_1x": native_foot,
                             "observed_pole_length_1x": observed_length,
                             "explicit_target_length_1x": target})
    medians: dict[str, float] = {}
    grouped: dict[str, list[float]] = {}
    for row in measured:
        if row["explicit_target_length_1x"] is None:
            grouped.setdefault(row["length_group"], []).append(row["observed_pole_length_1x"])
    for group, values in grouped.items():
        if len(values) >= 2:
            medians[group] = float(statistics.median(values))
    results = []
    for row in measured:
        head_x, head_y = row["observed_pole_head_1x"]
        flag_edge = row["native_flag_attachment_edge_pixel_1x"]
        move = [flag_edge[0] - head_x, flag_edge[1] - head_y]
        target = row["explicit_target_length_1x"]
        baseline_source = "explicit"
        if target is None:
            target = medians.get(row["length_group"])
            baseline_source = "marked_batch_median" if target is not None else "NOT_SET"
        length_delta = row["observed_pole_length_1x"] - target if target is not None else None
        suggested_foot = None
        if target is not None:
            dx = row["observed_pole_foot_1x"][0] - head_x
            dy = row["observed_pole_foot_1x"][1] - head_y
            current = row["observed_pole_length_1x"]
            suggested_foot = [head_x + dx * target / current, head_y + dy * target / current]
        instruction = (f"{row['identity']} 的 1999 旗面挂接侧可见像素参考为 "
                       f"({flag_edge[0]},{flag_edge[1]})；标记杆头相对该点的差为 "
                       f"({-move[0]:+.2f},{-move[1]:+.2f}) 个 1X 像素。旗面边缘不等于已证杆顶，"
                       "须结合原图和游戏实测判断；杆长不由旗面尺寸推定。")
        if target is not None:
            instruction += f" 当前标记杆长 {row['observed_pole_length_1x']:.2f}，人工目标 {target:.2f}。"
        delta = [head_x - flag_edge[0], head_y - flag_edge[1]]
        results.append({**row, "position_delta_observed_minus_flag_edge_1x": delta,
                        "reference_move_to_flag_edge_1x": move, "target_pole_length_1x": target,
                        "length_baseline_source": baseline_source,
                        "pole_length_delta_observed_minus_target_1x": length_delta,
                        "suggested_foot_1x": suggested_foot,
                        "measurement_source": "reviewer_marked_canvas_points",
                        "ai_edit_instruction": instruction})
    complete = bool(results) and all(i["flag_status"] == "COMPLETE" for i in manifest["items"]) and \
        len(results) == sum(len(i["anchors"]) for i in manifest["items"])
    status = "REVIEW_REQUIRED" if not complete or any(r["target_pole_length_1x"] is None
                                                        for r in results) else "MEASURED_RECOMMENDATIONS"
    report = {"schema": "h3.flag-pole-audit-report.v1", "status": status,
              "manifest": str(manifest_path.resolve()), "manifest_sha256": manifest_sha.lower(),
              "layout": str(layout_path.resolve()), "layout_sha256": layout_sha.lower(),
              "reference_database_sha256": reference_sha,
              "items": results,
              "batch_length_baselines_1x": medians,
              "official_pole_length": None,
              "claim_boundary": "1999 DEF crop origin and observed flag-face bbox are separate. A flag-edge pixel is an inferred attachment-side reference, not proof of the support-pole tip or length.",
              "source_images_modified": 0, "masters_written": 0, "three_x_written": 0,
              "mod_written": False, "game_tested": False}
    need(outdir.is_absolute() and not outdir.exists(), "OUTPUT_MUST_BE_NEW_ABSOLUTE_PATH")
    outdir.parent.mkdir(parents=True, exist_ok=True); outdir.mkdir(parents=True, exist_ok=False)
    output = outdir / "flag-pole-measurement-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report"] = str(output)
    return report


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>1999 原版旗杆坐标与长度核对</title><style>
body{font:15px system-ui;background:#182123;color:#edf2f2;margin:18px}p,small{color:#c8d5d5}button,input,select{font:inherit}button{margin:3px;padding:5px 9px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:14px}article{background:#273235;padding:12px;border-radius:6px}canvas{display:block;width:100%;max-width:900px;background:#202b2d;touch-action:none;cursor:crosshair;border:1px solid #607174}.mono{font:13px ui-monospace,monospace;white-space:pre-wrap}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.warn{color:#ffd477}
</style><h1>1999 原版旗杆与帧几何核对</h1><p id="intro"></p><p class="warn">蓝框为原版裁片，黄框为原版旗色像素框，绿点为根据旗面形状判断的挂接侧可见像素；它不是已证实的杆顶。橙框为残缺旗面的可见范围，不能当作精确旗位。标杆头/杆脚前须确认图像已注册到原版画布。</p><p><button id="save">导出标注布局 JSON</button><label>载入布局 <input id="load" type="file" accept="application/json"></label><span id="msg"></span></p><main class="grid" id="items"></main>
<script>
const data=__DATA__, root=document.getElementById('items'), state={};
document.getElementById('intro').textContent=`1999 Complete H3sprite.lod 原始 DEF；数据库 SHA ${data.manifest.reference_database_sha256.slice(0,16)}…；本次 ${data.manifest.items.length} 帧。裁片原点和旗面框分别来自原始帧头、palette-5 像素。`;
function xy(e,c,w,h){const r=c.getBoundingClientRect();return [Math.max(0,Math.min(w-1,Math.round((e.clientX-r.left)*w/r.width))),Math.max(0,Math.min(h-1,Math.round((e.clientY-r.top)*h/r.height)))]}
function median(v){v=v.filter(Number.isFinite).sort((a,b)=>a-b);if(!v.length)return null;return v.length%2?v[(v.length-1)/2]:(v[v.length/2-1]+v[v.length/2])/2}
function targetFor(s){const explicit=Number(s.target.value);if(s.target.value!==''&&Number.isFinite(explicit)&&explicit>0)return explicit;const vals=[];for(const q of Object.values(state)){if((q.group.value.trim()||q.item.identity)!==(s.group.value.trim()||s.item.identity))continue;const k=q.scaleN/q.scaleD;for(const p of q.poles)if(p&&p.head&&p.foot)vals.push(Math.hypot(p.foot[0]-p.head[0],p.foot[1]-p.head[1])/k)}return median(vals)}
function box(ctx,b,k,ox,oy,color,dash=[]){ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.lineWidth=Math.max(1,k/3);ctx.strokeRect(ox+b[0]*k,oy+b[1]*k,(b[2]-b[0])*k,(b[3]-b[1])*k);ctx.setLineDash([])}
function render(s){const {canvas,ctx,item,img,sel,info}=s;if(!img.complete||!img.naturalWidth)return;const w=img.naturalWidth,h=img.naturalHeight;if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}ctx.clearRect(0,0,w,h);ctx.drawImage(img,0,0);const k=s.scaleN/s.scaleD,ox=s.origin[0],oy=s.origin[1],g=item.native_geometry;
 if(s.confirmed.checked||s.assumed){const [cx,cy]=g.origin_1x,[cw,ch]=g.crop_1x;box(ctx,[cx,cy,cx+cw,cy+ch],k,ox,oy,'#51a9ff');for(const a of item.anchors){box(ctx,a.bbox_1x,k,ox,oy,'#ffdc4c');const [ex,ey]=a.attachment_edge_pixel_1x;ctx.strokeStyle='#37fa8a';ctx.lineWidth=Math.max(1,k/2);ctx.beginPath();ctx.arc(ox+ex*k,oy+ey*k,Math.max(2,k),0,Math.PI*2);ctx.stroke()}for(const a of item.partial_flags)box(ctx,a.observed_bbox_1x,k,ox,oy,'#ff9e43',[4,3]);}
 const i=Number(sel.value),a=item.anchors[i],p=s.poles[i],lines=[`${item.identity} · 组 ${item.group} 帧 ${item.frame}`,`1999 画布 ${g.canvas_1x.join('×')} / 裁片 ${g.crop_1x.join('×')} / 裁片原点 (${g.origin_1x.join(', ')})`,`图片注册：倍率 ${s.scaleN}/${s.scaleD}，画布左上偏移 (${ox}, ${oy})，${s.confirmed.checked?'已确认':s.assumed?'按尺寸推定，待确认':'未确认'}`,`原版旗面状态：${item.flag_status}`];
 if(item.partial_flags.length)lines.push(`残缺旗面与候选：${JSON.stringify(item.partial_flags)}`);if(a){lines.push(`旗面框 1X：${a.bbox_1x.join(', ')}；挂接侧可见像素：${a.attachment_edge_pixel_1x.join(', ')}`);if(p&&p.head&&s.confirmed.checked){const [ex,ey]=a.attachment_edge_pixel_1x;lines.push(`标记杆头相对旗面边缘：dx=${((p.head[0]-ox)/k-ex).toFixed(2)}, dy=${((p.head[1]-oy)/k-ey).toFixed(2)} 1X 像素`)}}if(p&&p.head&&p.foot){const len=Math.hypot(p.foot[0]-p.head[0],p.foot[1]-p.head[1])/k,target=targetFor(s);lines.push(`标记杆长 ${len.toFixed(2)} 1X；${target===null?'尚未设置杆长参考':`人工/同组参考 ${target.toFixed(2)} 1X`}`)}else if(a)lines.push('请标记杆头与杆脚；旗面边缘不等于杆顶。');info.textContent=lines.join('\n');
 if(p){for(const [pt,color] of [[p.head,'#ff6677'],[p.foot,'#fff']])if(pt){ctx.fillStyle=color;ctx.beginPath();ctx.arc(pt[0],pt[1],Math.max(2,k),0,Math.PI*2);ctx.fill()}}}
for(const item of data.manifest.items){const art=document.createElement('article');art.innerHTML=`<h2></h2><p class="mono"></p><div class="row"><label>旗位 <select></select></label><button data-mode="head">标杆头</button><button data-mode="foot">标杆脚</button><label>每 1X 像素 <input class="scaleN" type="number" min="1" max="64" step="1"></label>/<input class="scaleD" type="number" min="1" max="64" step="1"><label>图像注册偏移 X/Y <input class="originX" type="number" step="1"></label><input class="originY" type="number" step="1"><label><input class="confirmed" type="checkbox">已确认注册</label><label>长度组 <input class="group" type="text"></label><label>人工杆长 1X <input class="target" type="number" min="0" step="0.25" placeholder="留空=同组中位数"></label></div><canvas></canvas>`;art.querySelector('h2').textContent=`${item.identity} ${item.group}_${item.frame} · ${item.image_pixels.join('×')}`;const sel=art.querySelector('select');item.anchors.forEach((a,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`旗面 ${i+1} (${a.x},${a.y})`;sel.appendChild(o)});const canvas=art.querySelector('canvas'),ctx=canvas.getContext('2d'),img=new Image();img.src=item.image_url;const s={item,canvas,ctx,img,sel,mode:'head',info:art.querySelector('p.mono'),poles:item.anchors.map(()=>null),scaleN:item.scale_num,scaleD:item.scale_den,origin:[...item.native_origin_px],scaleNInput:art.querySelector('.scaleN'),scaleDInput:art.querySelector('.scaleD'),originXInput:art.querySelector('.originX'),originYInput:art.querySelector('.originY'),confirmed:art.querySelector('.confirmed'),group:art.querySelector('.group'),target:art.querySelector('.target')};s.scaleNInput.value=s.scaleN;s.scaleDInput.value=s.scaleD;s.originXInput.value=s.origin[0];s.originYInput.value=s.origin[1];s.assumed=item.registration_status.startsWith('exact_native_');s.confirmed.checked=false;s.group.value=item.length_group;s.target.value=item.target_length_1x??'';state[item.slot_key]=s;img.onload=()=>render(s);sel.onchange=()=>render(s);for(const el of [s.scaleNInput,s.scaleDInput,s.originXInput,s.originYInput])el.onchange=()=>{s.scaleN=Math.max(1,Number(s.scaleNInput.value));s.scaleD=Math.max(1,Number(s.scaleDInput.value));s.origin=[Number(s.originXInput.value)||0,Number(s.originYInput.value)||0];s.confirmed.checked=false;s.assumed=false;render(s)};s.confirmed.onchange=()=>render(s);for(const el of [s.group,s.target])el.onchange=()=>Object.values(state).forEach(render);for(const b of art.querySelectorAll('button[data-mode]'))b.onclick=()=>{s.mode=b.dataset.mode};canvas.onclick=e=>{if(!item.anchors.length)return;const i=Number(sel.value),pt=xy(e,canvas,img.naturalWidth,img.naturalHeight);if(!s.poles[i])s.poles[i]={head:null,foot:null};s.poles[i][s.mode]=pt;Object.values(state).forEach(render)};root.appendChild(art)}
document.getElementById('save').onclick=()=>{const items=[];for(const item of data.manifest.items){const s=state[item.slot_key];items.push({identity:item.identity,group:item.group,frame:item.frame,image_sha256:item.image_sha256,pixels_per_1x:[s.scaleN,s.scaleD],native_origin_px:s.origin,registration_confirmed:s.confirmed.checked,length_group:s.group.value.trim()||item.identity,target_length_1x:s.target.value===''?null:Number(s.target.value),poles:s.poles.map(p=>p?{head_px:p.head,foot_px:p.foot}:null)})}const layout={schema:'h3.flag-pole-audit-layout.v1',manifest_sha256:data.manifest_sha256,reference_database_sha256:data.manifest.reference_database_sha256,items};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(layout,null,2)],{type:'application/json'}));a.download='flag-pole-layout.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);document.getElementById('msg').textContent='已导出标注布局。'};
document.getElementById('load').onchange=async e=>{try{const l=JSON.parse(await e.target.files[0].text());if(l.schema!=='h3.flag-pole-audit-layout.v1'||l.manifest_sha256!==data.manifest_sha256)throw Error('布局与当前冻结输入不匹配');for(const row of l.items){const s=state[`${row.identity}:${row.group??0}:${row.frame??0}`];if(!s||row.image_sha256!==s.item.image_sha256)throw Error('图像身份或 SHA 不匹配');s.poles=row.poles.map(p=>p?{head:p.head_px,foot:p.foot_px}:null);s.group.value=row.length_group||'';s.target.value=row.target_length_1x??'';s.scaleN=row.pixels_per_1x[0];s.scaleD=row.pixels_per_1x[1];s.origin=row.native_origin_px;s.scaleNInput.value=s.scaleN;s.scaleDInput.value=s.scaleD;s.originXInput.value=s.origin[0];s.originYInput.value=s.origin[1];s.confirmed.checked=row.registration_confirmed===true;s.assumed=false}Object.values(state).forEach(render);document.getElementById('msg').textContent='已载入布局，仍须复核。'}catch(x){document.getElementById('msg').textContent=x.message}};
</script></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="Query one 1999 DEF identity without creating an audit directory")
    parser.add_argument("--image", action="append", help="PNG path or ID=PNG path; may be repeated")
    parser.add_argument("--identity", help="Identity for one PNG; defaults to filename stem")
    parser.add_argument("--group", type=int, help="Original DEF group; inferred from group_frame.png when omitted")
    parser.add_argument("--frame", type=int, help="Original DEF frame; default 0 for image audit, all group frames for --catalog")
    parser.add_argument("--image-sha256")
    parser.add_argument("--scale", default="auto", help="pixels per native 1X pixel, integer or N/D; auto uses exact native-canvas multiples")
    parser.add_argument("--length-group", help="Group whose confirmed pole lengths may form a median baseline")
    parser.add_argument("--target-length-1x", type=float, help="Explicit support-pole target length in 1X pixels")
    parser.add_argument("--operation-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--layout-sha256")
    parser.add_argument("--verify-layout", action="store_true")
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--reference-database", type=Path, default=REFERENCE)
    args = parser.parse_args()
    if not args.catalog and not args.image and args.manifest is None:
        parser.error("one of --catalog, --image or --manifest is required")
    try:
        reference, reference_sha, reference_doc = load_reference(args.reference_database)
        if args.catalog:
            need(not args.image and args.manifest is None and not args.verify_layout and args.outdir is None,
                 "CATALOG_QUERY_MUST_BE_READ_ONLY")
            identity = args.catalog.upper()
            need(identity in reference, missing_anchor_error(identity, reference_doc))
            row = reference[identity]
            group_id = args.group if args.group is not None else 0
            frames = ([selected_frame(row, group_id, args.frame)] if args.frame is not None else
                      [frame for frame in row["frames"] if frame["group"] == group_id])
            need(bool(frames), f"{identity}:NATIVE_GROUP_NOT_FOUND:{group_id}")
            result = {"status": "1999_NATIVE_CATALOG", "identity": identity,
                      "source_def_sha256": row["source_def_sha256"],
                      "reference_database_sha256": reference_sha,
                      "provenance": reference_doc["provenance"], "frames": frames}
        elif args.image:
            need(args.outdir is not None, "OUTPUT_DIRECTORY_REQUIRED")
            need(args.manifest is None and not args.verify_layout, "CHOOSE_IMAGE_OR_MANIFEST_MODE")
            result = freeze_images(args.image, args.identity, args.image_sha256, args.scale,
                                   args.length_group, args.target_length_1x, args.operation_id,
                                   args.outdir, reference, reference_sha, reference_doc,
                                   native_group=args.group, native_frame=args.frame)
        else:
            need(args.outdir is not None, "OUTPUT_DIRECTORY_REQUIRED")
            need(args.manifest is not None and args.manifest_sha256, "MANIFEST_AND_SHA_REQUIRED")
            manifest = checked_manifest(args.manifest, args.manifest_sha256, reference, reference_sha)
            if args.verify_layout:
                need(args.layout is not None and args.layout_sha256, "LAYOUT_AND_SHA_REQUIRED")
                result = verify_layout(args.manifest, args.manifest_sha256, args.layout,
                                       args.layout_sha256, args.outdir, reference, reference_sha)
            else:
                need(not args.outdir.exists(), "OUTPUT_MUST_BE_NEW_ABSOLUTE_PATH")
                result = freeze_images([row["identity"] + "=" + row["image_path"] for row in manifest["items"]],
                                       None, None, "auto", None, None,
                                       manifest.get("operation_id"), args.outdir, reference, reference_sha,
                                       reference_doc, manifest["items"])
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (AuditError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False)); return 2


if __name__ == "__main__":
    sys.exit(main())
