"""Freeze, normalize, and verify a complete creature mother-image set.

Normalization is a deterministic, source-pinned resample of existing RGBA
masters. It never invents pixels, changes animation meaning, registers native
anchors, or claims 3X/game approval. Its normalized subject scale is bounded by
the smallest real input scale so no source is enlarged.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from PIL import Image

PRODUCT = resolve_tool_root(__file__)
SCHEMA = "h3.creature.master-normalization-manifest.v1"
RECEIPT_SCHEMA = "h3.creature.master-normalization-receipt.v1"
REVIEW_SCHEMA = "h3.creature.limb-visual-review.v1"
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 64_000_000
CANONICAL_RE = re.compile(r"^(?P<group>\d+)_(?P<frame>\d+)\.png$")


class SetError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise SetError(f"FILE_TOO_LARGE: {path}")
            h.update(chunk)
    if total == 0:
        raise SetError(f"EMPTY_FILE: {path}")
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path, expected_sha: str | None = None) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    if expected_sha and digest.lower() != expected_sha.lower():
        raise SetError(f"SHA256_MISMATCH: {path}")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SetError(f"JSON_INVALID: {path}: {error}") from error
    if not isinstance(value, dict):
        raise SetError(f"JSON_OBJECT_REQUIRED: {path}")
    return value, digest


def read_inventory(path: Path, expected_sha: str) -> tuple[dict[str, Any], str, Path]:
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    if digest.lower() != expected_sha.lower():
        raise SetError("NATIVE_INVENTORY_SHA256_MISMATCH")
    try:
        payload = gzip.decompress(raw) if path.suffix.lower() == ".gz" else raw
        inventory = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SetError(f"NATIVE_INVENTORY_INVALID: {error}") from error
    if not isinstance(inventory, dict) or not isinstance(inventory.get("rows"), list):
        raise SetError("NATIVE_INVENTORY_ROWS_REQUIRED")
    return inventory, digest, path.parent.parent


def official_reference(root: Path, row: dict[str, Any]) -> Path:
    refs = row.get("x3")
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str):
        raise SetError("OFFICIAL_X3_REFERENCE_REQUIRED")
    parts = PurePosixPath(refs[0]).parts
    if parts and parts[0] == "assets":
        parts = parts[1:]
    return root.joinpath(*parts)


def png_image(path: Path) -> Image.Image:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SetError(f"PNG_TOO_LARGE: {path}")
        with Image.open(path) as opened:
            if opened.format != "PNG" or opened.mode != "RGBA" or getattr(opened, "n_frames", 1) != 1:
                raise SetError(f"PNG_SINGLE_FRAME_RGBA_REQUIRED: {path}")
            if opened.width * opened.height > MAX_PIXELS:
                raise SetError(f"PNG_PIXEL_LIMIT: {path}")
            opened.load()
            return opened.copy()
    except SetError:
        raise
    except Exception as error:
        raise SetError(f"PNG_INVALID: {path}: {error}") from error


def alpha_box(image: Image.Image, threshold: int) -> list[int] | None:
    return list(image.getchannel("A").point(lambda value: 255 if value >= threshold else 0).getbbox() or ()) or None


def box_size(box: list[int] | None) -> tuple[int, int] | None:
    if box is None:
        return None
    return box[2] - box[0], box[3] - box[1]


def native_slots(inventory: dict[str, Any], identity: str) -> list[dict[str, Any]]:
    rows = [row for row in inventory["rows"]
            if row.get("category") == "creatures" and row.get("kind") == "def"
            and row.get("identity") == identity]
    # Complete's expansion archive replaces whole DEF identities. The
    # selected inventory row set already records that choice; keep the
    # explicit fallback for older snapshots that contain both archives.
    ab_rows = [row for row in rows if row.get("native_lod") == "H3ab_spr.lod"]
    main_rows = [row for row in rows if row.get("native_lod") == "H3sprite.lod"]
    rows = ab_rows or main_rows
    rows.sort(key=lambda row: (row["group"], row["frame"]))
    if not rows:
        raise SetError("IDENTITY_HAS_NO_EXACT_NATIVE_DEF_SLOTS")
    keys = [(row.get("group"), row.get("frame")) for row in rows]
    if len(keys) != len(set(keys)):
        raise SetError("DUPLICATE_NATIVE_SLOT_IN_INVENTORY")
    return rows


def official_manifest_rows(path: Path, expected_sha: str) -> tuple[dict[str, dict[str, Any]], str]:
    manifest, digest = read_json(path, expected_sha)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SetError("OFFICIAL_EXTRACTION_FILE_INDEX_REQUIRED")
    indexed: dict[str, dict[str, Any]] = {}
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise SetError("OFFICIAL_EXTRACTION_FILE_ROW_INVALID")
        key = row["path"].replace("\\", "/").casefold()
        if key in indexed:
            raise SetError(f"OFFICIAL_EXTRACTION_DUPLICATE_PATH: {key}")
        indexed[key] = row
    return indexed, digest


def descriptor_entry(reference: Path, expected_stem: str) -> tuple[Path, str, int, str]:
    descriptor = reference.parent.parent / "descriptor.txt"
    if not descriptor.is_file():
        raise SetError(f"OFFICIAL_DESCRIPTOR_MISSING: {descriptor}")
    digest = sha256_file(descriptor)
    try:
        lines = descriptor.read_text(encoding="ascii", errors="strict").splitlines()
    except UnicodeError as error:
        raise SetError(f"OFFICIAL_DESCRIPTOR_ENCODING_INVALID: {descriptor}") from error
    matches = [(line_no, line.strip()) for line_no, line in enumerate(lines, 1)
               if line.strip() and line.strip().split()[0].casefold() == expected_stem.casefold()]
    if len(matches) != 1:
        raise SetError(f"OFFICIAL_DESCRIPTOR_ENTRY_COUNT_INVALID: {expected_stem}")
    line_no, line = matches[0]
    fields = line.split()
    if len(fields) < 12:
        raise SetError(f"OFFICIAL_DESCRIPTOR_ENTRY_INVALID: {expected_stem}")
    return descriptor, digest, line_no, line


def source_entries(inventory: dict[str, Any], root: Path, masters: Path,
                   identity: str, extraction_rows: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    forbidden_tiers = {"codex-3x", "codex-prepared", "output-3x", "3x"}
    if any(part.casefold() in forbidden_tiers for part in masters.parts):
        raise SetError("MASTER_SOURCE_IS_PREPARED_OR_FORMAL_3X")
    rows = native_slots(inventory, identity)
    by_resource: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    wrong_names: list[dict[str, str]] = []
    row_images: dict[tuple[int, int], tuple[Path, Image.Image, str]] = {}
    for row in rows:
        group, frame = row["group"], row["frame"]
        name = f"{group}_{frame}.png"
        match = CANONICAL_RE.fullmatch(name)
        if not match or (int(match["group"]), int(match["frame"])) != (group, frame):
            wrong_names.append({"expected": name, "error": "OFFICIAL_SLOT_NAME_INVALID"})
        path = masters / name
        if not path.is_file():
            missing.append(name)
            continue
        image = png_image(path)
        if alpha_box(image, 128) is None:
            raise SetError(f"EMPTY_ALPHA128_SUBJECT: {path}")
        digest = sha256_file(path)
        row_images[(group, frame)] = (path, image, digest)
        resource = str(row.get("resource", "")).casefold()
        if not resource:
            raise SetError(f"NATIVE_RESOURCE_NAME_MISSING: {name}")
        by_resource.setdefault(resource, []).append(row)
    if missing:
        raise SetError("MISSING_CANONICAL_SLOTS: " + ", ".join(missing[:20]))
    if wrong_names:
        raise SetError("NONCANONICAL_SLOT_NAME")

    classes: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    for resource, aliases in sorted(by_resource.items(), key=lambda item: (item[1][0]["group"], item[1][0]["frame"])):
        aliases.sort(key=lambda row: (row["group"], row["frame"]))
        representative = aliases[0]
        crop = representative.get("crop")
        anchor = representative.get("anchor")
        if not (isinstance(crop, list) and len(crop) == 2 and all(type(x) is int and x > 0 for x in crop)):
            raise SetError(f"NATIVE_CROP_INVALID: {resource}")
        for alias in aliases[1:]:
            if alias.get("crop") != crop or alias.get("anchor") != anchor:
                raise SetError(f"EXACT_ALIAS_GEOMETRY_MISMATCH: {resource}")
        ref = official_reference(root, representative)
        if not ref.is_file():
            raise SetError(f"OFFICIAL_X3_REFERENCE_MISSING: {ref}")
        rel_ref = ref.relative_to(root / "OfficialHD2015").as_posix().casefold()
        index_row = extraction_rows.get(rel_ref)
        if index_row is None:
            raise SetError(f"OFFICIAL_EXTRACTION_MANIFEST_ROW_MISSING: {rel_ref}")
        ref_sha = sha256_file(ref)
        if index_row.get("sha256") != ref_sha or index_row.get("bytes") != ref.stat().st_size:
            raise SetError(f"OFFICIAL_EXTRACTION_MANIFEST_SHA_OR_SIZE_MISMATCH: {ref}")
        expected_stem = Path(str(representative["resource"])).stem.upper()
        if ref.stem.upper() != expected_stem:
            raise SetError(f"OFFICIAL_RESOURCE_FILENAME_MISMATCH: {ref.stem} vs {expected_stem}")
        descriptor, descriptor_sha, descriptor_line, descriptor_text = descriptor_entry(ref, expected_stem)
        ref_image = png_image(ref)
        ref_box = alpha_box(ref_image, 16)
        source_path, source_image, source_sha = row_images[(representative["group"], representative["frame"])]
        source_box = alpha_box(source_image, 128)
        assert source_box is not None
        measured = box_size(source_box)
        assert measured is not None
        alpha1_box = alpha_box(source_image, 1)
        alpha2_box = alpha_box(source_image, 2)
        alpha1_size = box_size(alpha1_box) or (0, 0)
        alpha2_size = box_size(alpha2_box) or (0, 0)
        source_alpha = np.asarray(source_image.getchannel("A"), dtype=np.uint8)
        alpha1_count = int(np.count_nonzero(source_alpha == 1))
        edge_max = max(int(source_alpha[0, :].max()), int(source_alpha[-1, :].max()),
                       int(source_alpha[:, 0].max()), int(source_alpha[:, -1].max()))
        fringe_ratio = max(alpha1_size[0] / max(measured[0], 1), alpha1_size[1] / max(measured[1], 1))
        k = max(measured[0] / crop[0], measured[1] / crop[1])
        klass = {
            "resource": representative["resource"],
            "representative_slot": ["def", identity, representative["group"], representative["frame"]],
            "logical_slots": [["def", identity, alias["group"], alias["frame"]] for alias in aliases],
            "source_path": str(source_path), "source_sha256": source_sha,
            "source_pixels": list(source_image.size), "source_alpha128_bbox": source_box,
            "source_positive_alpha_bbox": alpha1_box, "source_alpha2_bbox": alpha2_box,
            "source_alpha1_pixel_count": alpha1_count, "source_canvas_edge_alpha_max": edge_max,
            "positive_alpha_extent_ratio_vs_alpha128": round(fringe_ratio, 6),
            "alpha_residue_review_required": fringe_ratio > 1.2 or edge_max > 16,
            "source_visible_size": list(measured), "native_crop": crop, "native_anchor": anchor,
            "source_effective_scale": round(k, 8),
            "official_x3_reference": str(ref),
            "official_x3_sha256": ref_sha,
            "official_extraction_manifest_path": str(root / "OfficialHD2015" / "manifests" / "official-hd-x3-selected-extraction-20261020.json"),
            "official_extraction_manifest_row": index_row,
            "official_descriptor_path": str(descriptor), "official_descriptor_sha256": descriptor_sha,
            "official_descriptor_line_number": descriptor_line, "official_descriptor_entry": descriptor_text,
            "official_x3_pixels": list(ref_image.size),
            "official_x3_alpha16_bbox": ref_box,
            "official_action_ratio": ((ref_box[2]-ref_box[0])/(ref_box[3]-ref_box[1])) if ref_box else None,
        }
        classes.append(klass)
        for alias in aliases:
            path, image, digest = row_images[(alias["group"], alias["frame"])]
            if digest != source_sha:
                raise SetError(f"EXACT_NATIVE_ALIAS_MOTHER_PIXELS_DIFFER: {resource}")
            slots.append({"key": ["def", identity, alias["group"], alias["frame"]],
                          "resource": alias["resource"], "output_name": f"{alias['group']}_{alias['frame']}.png",
                          "source_sha256": digest})
    return classes, slots, missing


def extra_root_pngs(masters: Path, identity: str) -> list[dict[str, Any]]:
    expected = set()
    for path in masters.glob("*.png"):
        if CANONICAL_RE.fullmatch(path.name):
            expected.add(path.resolve())
    extras = []
    for path in sorted(masters.glob("*.png")):
        if path.resolve() in expected:
            continue
        role = "legacy_source_parent" if path.name.startswith(f"def_{identity}_") else "unexpected_png"
        extras.append({"path": str(path), "name": path.name, "role": role, "sha256": sha256_file(path),
                       "bytes": path.stat().st_size})
    return extras


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    inventory_path = Path(args.inventory).resolve(strict=True)
    inventory, inventory_sha, official_root = read_inventory(inventory_path, args.inventory_sha256)
    extraction_path = Path(args.official_manifest).resolve(strict=True)
    extraction_rows, extraction_sha = official_manifest_rows(extraction_path, args.official_manifest_sha256)
    standard_path = Path(args.art_standard).resolve(strict=True)
    standard, standard_sha = read_json(standard_path, args.art_standard_sha256)
    if standard.get("version") != args.art_standard_version:
        raise SetError("ART_STANDARD_VERSION_MISMATCH")
    identity = args.identity
    if not re.fullmatch(r"[A-Z0-9_]+", identity):
        raise SetError("IDENTITY_INVALID")
    masters = Path(args.masters_dir).resolve(strict=True)
    classes, slots, _ = source_entries(inventory, official_root, masters, identity, extraction_rows)
    min_k = min(item["source_effective_scale"] for item in classes)
    target_k = args.target_common_k if args.target_common_k is not None else math.floor(min_k * 100) / 100
    if target_k < 3 or target_k > min_k + 1e-9:
        raise SetError(f"TARGET_SCALE_MUST_BE_AT_LEAST_3_AND_MUST_NOT_UPSCALE_ANY_SOURCE (min={min_k:.4f})")
    names = [item["output_name"] for item in slots]
    if len(names) != len(set(names)):
        raise SetError("OUTPUT_NAME_COLLISION")
    if len(slots) != len(native_slots(inventory, identity)):
        raise SetError("LOGICAL_SLOT_COVERAGE_MISMATCH")
    form = masters.name
    manifest = {
        "schema": SCHEMA,
        "identity": identity,
        "faction": args.faction,
        "form": args.form,
        "operation_id": args.operation_id,
        "created_local": args.created_local,
        "native_inventory": {"path": str(inventory_path), "sha256": inventory_sha},
        "official_extraction_manifest": {"path": str(extraction_path), "sha256": extraction_sha},
        "official_root": str(official_root),
        "masters_dir": str(masters),
        "art_standard": {"path": str(standard_path), "version": standard["version"], "sha256": standard_sha},
        "measurement": {"alpha_threshold": 128, "metric": "max(alpha128_bbox_width/native_crop_width, alpha128_bbox_height/native_crop_height)",
                        "target_common_k": target_k, "normalization_policy": "downscale_only_to_smallest_source_class_scale",
                        "min_source_k": min_k, "upsampling_allowed": False,
                        "native_fit_scale": math.ceil(target_k),
                        "visual_anatomy": "requires per-frame AI/human visual-review receipt; alpha geometry cannot prove limb count"},
        "frame_classes": classes,
        "logical_slots": slots,
        "legacy_or_unexpected_root_pngs": extra_root_pngs(masters, identity),
        "expected_logical_slots": len(slots),
        "unique_frame_classes": len(classes),
        "source_pixels_mutated": False,
        "model_inference_performed": False,
        "formal_native_registration": False,
        "runtime_accepted": False,
        "game_deployed": False,
    }
    return manifest


def validate_manifest(path: Path, expected_sha: str) -> dict[str, Any]:
    manifest, digest = read_json(path, expected_sha)
    if manifest.get("schema") != SCHEMA:
        raise SetError("MANIFEST_SCHEMA_UNSUPPORTED")
    if manifest.get("expected_logical_slots") != len(manifest.get("logical_slots", [])):
        raise SetError("MANIFEST_SLOT_COUNT_DRIFT")
    if manifest.get("unique_frame_classes") != len(manifest.get("frame_classes", [])):
        raise SetError("MANIFEST_CLASS_COUNT_DRIFT")
    if manifest.get("measurement", {}).get("upsampling_allowed") is not False:
        raise SetError("UPSAMPLING_POLICY_MUST_BE_DISABLED")
    return manifest


def revalidate_sources(manifest: dict[str, Any]) -> list[tuple[dict[str, Any], Image.Image]]:
    checked = []
    for item in manifest["frame_classes"]:
        path = Path(item["source_path"])
        image = png_image(path)
        digest = sha256_file(path)
        if digest != item["source_sha256"] or list(image.size) != item["source_pixels"]:
            raise SetError(f"SOURCE_DRIFT: {path}")
        ref = Path(item["official_x3_reference"])
        if sha256_file(ref) != item["official_x3_sha256"]:
            raise SetError(f"OFFICIAL_REFERENCE_DRIFT: {ref}")
        box = alpha_box(image, 128)
        size = box_size(box)
        if box != item["source_alpha128_bbox"] or size != tuple(item["source_visible_size"]):
            raise SetError(f"SOURCE_ALPHA_GEOMETRY_DRIFT: {path}")
        crop_w, crop_h = item["native_crop"]
        source_k = max(size[0] / crop_w, size[1] / crop_h)
        if source_k + 1e-8 < manifest["measurement"]["target_common_k"]:
            raise SetError(f"NORMALIZATION_WOULD_UPSCALE: {path}")
        checked.append((item, image))
    inventory = Path(manifest["native_inventory"]["path"])
    _, digest, _ = read_inventory(inventory, manifest["native_inventory"]["sha256"])
    if digest != manifest["native_inventory"]["sha256"]:
        raise SetError("NATIVE_INVENTORY_DRIFT")
    art_standard = Path(manifest["art_standard"]["path"])
    standard, _ = read_json(art_standard, manifest["art_standard"]["sha256"])
    if standard.get("version") != manifest["art_standard"]["version"]:
        raise SetError("ART_STANDARD_DRIFT")
    official_path = Path(manifest["official_extraction_manifest"]["path"])
    extraction_rows, extraction_sha = official_manifest_rows(
        official_path, manifest["official_extraction_manifest"]["sha256"])
    for item, _ in checked:
        ref = Path(item["official_x3_reference"])
        rel = ref.relative_to(Path(manifest["official_root"]) / "OfficialHD2015").as_posix().casefold()
        row = extraction_rows.get(rel)
        if row != item["official_extraction_manifest_row"]:
            raise SetError(f"OFFICIAL_EXTRACTION_ROW_DRIFT: {rel}")
        descriptor, descriptor_sha, line_no, line = descriptor_entry(ref, Path(item["resource"]).stem.upper())
        if (str(descriptor) != item["official_descriptor_path"] or descriptor_sha != item["official_descriptor_sha256"]
                or line_no != item["official_descriptor_line_number"] or line != item["official_descriptor_entry"]):
            raise SetError(f"OFFICIAL_DESCRIPTOR_DRIFT: {ref}")
    return checked


def _resize_premultiplied(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Lanczos resize in premultiplied-alpha space to avoid transparent-edge halos."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3] / 255.0
    resized_alpha = Image.fromarray(np.uint8(np.clip(rgba[:, :, 3], 0, 255)), "L").resize(size, Image.Resampling.LANCZOS)
    a_out = np.asarray(resized_alpha, dtype=np.float32) / 255.0
    channels = []
    for channel in range(3):
        premul = rgba[:, :, channel] * alpha
        resized = Image.fromarray(np.uint8(np.clip(np.rint(premul), 0, 255)), "L").resize(size, Image.Resampling.LANCZOS)
        channels.append(np.asarray(resized, dtype=np.float32))
    out = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    safe = a_out > (1.0 / 255.0)
    for channel in range(3):
        values = np.zeros_like(a_out)
        values[safe] = channels[channel][safe] / a_out[safe]
        out[:, :, channel] = np.uint8(np.clip(np.rint(values), 0, 255))
    out[:, :, 3] = np.uint8(np.clip(np.rint(a_out * 255.0), 0, 255))
    out[out[:, :, 3] == 0, :3] = 0
    return Image.fromarray(out, "RGBA")


def normalize_one(item: dict[str, Any], image: Image.Image, target_k: float) -> tuple[Image.Image, dict[str, Any]]:
    all_box = image.getchannel("A").getbbox()
    body_box = alpha_box(image, 128)
    if all_box is None or body_box is None:
        raise SetError(f"EMPTY_ALPHA_SUBJECT: {item['source_path']}")
    body_size = box_size(body_box)
    assert body_size is not None
    crop_w, crop_h = item["native_crop"]
    source_k = max(body_size[0] / crop_w, body_size[1] / crop_h)
    factor = target_k / source_k
    if factor > 1.00000001:
        raise SetError(f"NORMALIZATION_WOULD_UPSCALE: {item['source_path']}")
    pad = 2
    left = max(0, all_box[0] - pad)
    top = max(0, all_box[1] - pad)
    right = min(image.width, all_box[2] + pad)
    bottom = min(image.height, all_box[3] + pad)
    content = image.crop((left, top, right, bottom))
    scaled_size = (max(1, round(content.width * factor)), max(1, round(content.height * factor)))
    resized = _resize_premultiplied(content, scaled_size)
    output = Image.new("RGBA", (scaled_size[0] + 4, scaled_size[1] + 4), (0, 0, 0, 0))
    output.alpha_composite(resized, (2, 2))
    output_box = alpha_box(output, 128)
    output_size = box_size(output_box)
    if output_size is None:
        raise SetError("NORMALIZED_ALPHA128_EMPTY")
    output_k = max(output_size[0] / crop_w, output_size[1] / crop_h)
    if abs(output_k - target_k) > 0.04:
        raise SetError(f"NORMALIZED_SCALE_OUT_OF_TOLERANCE: {output_k:.4f} vs {target_k:.4f}")
    edge_alpha = np.asarray(output.getchannel("A"), dtype=np.uint8)
    edge_max = max(int(edge_alpha[0, :].max()), int(edge_alpha[-1, :].max()),
                   int(edge_alpha[:, 0].max()), int(edge_alpha[:, -1].max()))
    if edge_max != 0:
        raise SetError("NORMALIZED_CANVAS_EDGE_ALPHA_NOT_ZERO")
    record = {
        "source_path": item["source_path"], "source_sha256": item["source_sha256"],
        "source_pixels": item["source_pixels"], "source_alpha128_bbox": item["source_alpha128_bbox"],
        "native_crop": item["native_crop"], "source_effective_scale": source_k,
        "scale_factor": factor, "resampling": "premultiplied-alpha-lanczos", "source_resampled_once": factor < 0.999999,
        "target_common_k": target_k, "output_pixels": list(output.size), "output_alpha128_bbox": output_box,
        "output_effective_scale": output_k, "outer_edge_alpha_max": edge_max,
    }
    return output, record


def execute_normalize(manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    operation_id = manifest["operation_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", operation_id):
        raise SetError("OPERATION_ID_INVALID")
    output_root = Path(output_root).resolve(strict=False)
    isolated_root = (PRODUCT / "build/experiments/creature-master-set").resolve()
    source_root = Path(manifest["masters_dir"]).resolve()
    if (output_root.parent != isolated_root or output_root.name != operation_id
            or output_root == source_root or output_root.is_relative_to(source_root)):
        raise SetError("NORMALIZATION_OUTPUT_MUST_BE_ISOLATED_CANDIDATE")
    if output_root.exists() and not output_root.is_dir():
        raise SetError("OUTPUT_ROOT_NOT_DIRECTORY")
    expected_names = {row["output_name"] for row in manifest["logical_slots"]}
    conflicts = sorted(name for name in expected_names if (output_root / name).exists())
    receipt_path = output_root / "normalization-receipt.json"
    if conflicts or receipt_path.exists():
        raise SetError("OUTPUT_ROOT_CONTAINS_TARGET_FILES: " + ", ".join(conflicts[:10]))
    staging = output_root / f".{operation_id}.staging"
    if staging.exists():
        raise SetError("STAGING_ROOT_ALREADY_EXISTS")
    checked = revalidate_sources(manifest)
    target_k = manifest["measurement"]["target_common_k"]
    classes = {item["resource"].casefold(): (item, image) for item, image in checked}
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=False, exist_ok=False)
    rows_by_resource: dict[str, dict[str, Any]] = {}
    for resource, (item, image) in classes.items():
        output, record = normalize_one(item, image, target_k)
        aliases = [row for row in manifest["logical_slots"] if row["resource"].casefold() == resource]
        if not aliases:
            raise SetError(f"RESOURCE_HAS_NO_LOGICAL_SLOT: {resource}")
        unique_png = staging / aliases[0]["output_name"]
        output.save(unique_png, format="PNG", optimize=False, compress_level=9)
        record.update({"resource": item["resource"], "representative_key": item["representative_slot"],
                       "official_reference": item["official_x3_reference"],
                       "official_reference_sha256": item["official_x3_sha256"],
                       "output_path": str(output_root / unique_png.name), "output_sha256": sha256_file(unique_png),
                       "output_bytes": unique_png.stat().st_size,
                       "aliases": [row["key"] for row in aliases]})
        rows_by_resource[resource] = record
        for alias in aliases[1:]:
            path = staging / alias["output_name"]
            path.write_bytes(unique_png.read_bytes())
    output_rows = []
    for slot in manifest["logical_slots"]:
        staged_path = staging / slot["output_name"]
        final_path = output_root / slot["output_name"]
        row = {**slot, "output_path": str(final_path), "output_sha256": sha256_file(staged_path),
               "output_pixels": list(png_image(staged_path).size),
               "normalization_resource_record": rows_by_resource[slot["resource"].casefold()]["representative_key"]}
        output_rows.append(row)
    receipt = {
        "schema": RECEIPT_SCHEMA, "operation_id": manifest["operation_id"], "identity": manifest["identity"],
        "manifest_sha256": manifest["manifest_sha256"], "output_root": str(output_root),
        "expected_slots": manifest["expected_logical_slots"], "written_slots": len(output_rows),
        "unique_frame_classes": len(rows_by_resource), "target_common_k": target_k,
        "normalization_algorithm": "uniform-downscale-only-to-lowest-source-effective-scale-v1",
        "source_mutation_count": 0, "model_inference_performed": False,
        "formal_native_registration": False, "native_timing_verified": False,
        "formal_3x_export_verified": False, "runtime_accepted": False, "game_deployed": False,
        "visual_anatomy_review": "REQUIRED",
        "frame_classes": list(rows_by_resource.values()), "logical_slots": output_rows,
    }
    staged_receipt = staging / "normalization-receipt.json"
    staged_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    moved: list[Path] = []
    try:
        for name in sorted(expected_names):
            source = staging / name
            destination = output_root / name
            if destination.exists():
                raise SetError(f"OUTPUT_APPEARED_DURING_INSTALL: {destination}")
            source.replace(destination)
            moved.append(destination)
        if receipt_path.exists():
            raise SetError("RECEIPT_APPEARED_DURING_INSTALL")
        staged_receipt.replace(receipt_path)
        staging.rmdir()
    except Exception:
        for destination in reversed(moved):
            if destination.exists():
                destination.replace(staging / destination.name)
        raise
    return {"status": "NORMALIZED_CANDIDATE", "receipt": str(receipt_path),
            "receipt_sha256": sha256_file(receipt_path), "expected_slots": len(manifest["logical_slots"]),
            "unique_frame_classes": len(rows_by_resource), "output_root": str(output_root),
            "formal_native_registration": False, "game_ready": False}


def verify_normalized(manifest: dict[str, Any], output_root: Path,
                      limb_review_path: Path | None = None, limb_review_sha: str | None = None) -> dict[str, Any]:
    checked = revalidate_sources(manifest)
    source_by_resource = {item["resource"].casefold(): (item, image) for item, image in checked}
    receipt_path = output_root / "normalization-receipt.json"
    receipt, receipt_sha = read_json(receipt_path)
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise SetError("NORMALIZATION_RECEIPT_BINDING_MISMATCH")
    if receipt.get("target_common_k") != manifest["measurement"]["target_common_k"]:
        raise SetError("NORMALIZATION_TARGET_SCALE_MISMATCH")
    expected_names = {row["output_name"] for row in manifest["logical_slots"]}
    actual_pngs = {p.name for p in output_root.glob("*.png") if p.is_file()}
    if actual_pngs != expected_names:
        raise SetError(f"OUTPUT_SLOT_COVERAGE_MISMATCH: missing={sorted(expected_names-actual_pngs)[:10]} extra={sorted(actual_pngs-expected_names)[:10]}")
    classes_report = []
    expected_alias_sha: dict[str, str] = {}
    for item, source in source_by_resource.values():
        output, record = normalize_one(item, source, manifest["measurement"]["target_common_k"])
        aliases = [row for row in manifest["logical_slots"] if row["resource"].casefold() == item["resource"].casefold()]
        expected_bytes = _encode_png(output)
        alias_hash = sha256_bytes(expected_bytes)
        for alias in aliases:
            path = output_root / alias["output_name"]
            actual = path.read_bytes()
            if actual != expected_bytes:
                raise SetError(f"NORMALIZATION_REPLAY_MISMATCH: {alias['output_name']}")
            expected_alias_sha[alias["output_name"]] = alias_hash
        classes_report.append({"resource": item["resource"], "representative_slot": item["representative_slot"],
                               "output_k": record["output_effective_scale"], "output_sha256": alias_hash,
                               "positive_alpha_extent_ratio_vs_alpha128": item["positive_alpha_extent_ratio_vs_alpha128"],
                               "alpha1_pixel_count": item["source_alpha1_pixel_count"],
                               "alpha_residue_review_required": item["alpha_residue_review_required"],
                               "aliases": [row["output_name"] for row in aliases]})
    limb_report = {"status": "VISUAL_REVIEW_REQUIRED", "reviewed_frame_classes": 0,
                   "limb_integrity": "not machine-verified"}
    if limb_review_path is not None:
        review, review_digest = read_json(limb_review_path, limb_review_sha)
        limb_report = validate_limb_review(review, manifest, expected_alias_sha)
        limb_report["review_path"] = str(limb_review_path)
        limb_report["review_sha256"] = review_digest
    return {
        "schema": "h3.creature.master-set-completion-report.v1",
        "status": "NORMALIZED_SET_WITH_VISUAL_REVIEW" if limb_report["status"] == "AI_VISUAL_PASS" else "VISUAL_REVIEW_REQUIRED",
        "identity": manifest["identity"], "operation_id": manifest["operation_id"],
        "logical_slots_expected": len(manifest["logical_slots"]), "logical_slots_verified": len(expected_names),
        "missing_slots": [], "extra_pngs": [], "names_official": True,
        "unique_native_frame_classes": len(classes_report), "aliases_byte_identical": True,
        "target_common_k": manifest["measurement"]["target_common_k"],
        "normalization_range": [min(row["output_k"] for row in classes_report), max(row["output_k"] for row in classes_report)],
        "all_source_resamples_down_or_equal": True, "all_normalized_canvas_edges_alpha_zero": True,
        "alpha_residue_review_required_classes": [row["resource"] for row in classes_report if row["alpha_residue_review_required"]],
        "native_canvas_crop_origin_registered": False, "formal_3x_verified": False,
        "native_timing_verified": False, "game_ready": False,
        "limb_integrity_review": limb_report,
        "receipt": str(receipt_path), "receipt_sha256": receipt_sha,
        "frame_classes": classes_report,
    }


def _encode_png(image: Image.Image) -> bytes:
    from io import BytesIO
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def validate_limb_review(review: dict[str, Any], manifest: dict[str, Any],
                         output_hashes: dict[str, str]) -> dict[str, Any]:
    if review.get("schema") != REVIEW_SCHEMA or review.get("identity") != manifest["identity"]:
        raise SetError("LIMB_REVIEW_SCHEMA_OR_IDENTITY_MISMATCH")
    items = review.get("items")
    if not isinstance(items, list):
        raise SetError("LIMB_REVIEW_ITEMS_REQUIRED")
    by_resource = {item.get("resource", "").casefold(): item for item in items if isinstance(item, dict)}
    resources = {item["resource"].casefold() for item in manifest["frame_classes"]}
    if set(by_resource) != resources:
        raise SetError("LIMB_REVIEW_FRAME_CLASS_COVERAGE_MISMATCH")
    for frame_class in manifest["frame_classes"]:
        item = by_resource[frame_class["resource"].casefold()]
        output_name = next(row["output_name"] for row in manifest["logical_slots"]
                           if row["resource"].casefold() == frame_class["resource"].casefold())
        if item.get("output_sha256") != output_hashes[output_name]:
            raise SetError(f"LIMB_REVIEW_OUTPUT_SHA_MISMATCH: {frame_class['resource']}")
        if (item.get("single_character") is not True or item.get("arms") != 2 or item.get("legs") != 2
                or item.get("tail_present") is not False or item.get("limb_integrity") != "PASS"
                or item.get("visual_status") != "assistant_art_pass"
                or item.get("official_action_match") != "PASS"
                or item.get("alpha_residue_review") != "PASS_NO_VISIBLE_CONTAMINATION"
                or item.get("source_low_alpha_preserved") is not True):
            raise SetError(f"LIMB_REVIEW_NOT_PASS: {frame_class['resource']}")
    return {"status": "AI_VISUAL_PASS", "reviewer": review.get("reviewer"),
            "review_sha256": review.get("review_sha256"), "reviewed_frame_classes": len(resources),
            "single_character_all": True, "two_arms_two_legs_all": True, "no_tail_all": True,
            "official_action_match_all": True, "alpha_residue_review_all": True,
            "human_reviewed": review.get("human_reviewed") is True}


def freeze_command(args: argparse.Namespace) -> int:
    manifest = freeze(args)
    out = Path(args.manifest_out).resolve(strict=False)
    if out.exists():
        raise SetError("MANIFEST_OUTPUT_ALREADY_EXISTS")
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    digest = sha256_bytes(raw)
    print(json.dumps({"status": "FROZEN", "manifest": str(out), "manifest_sha256": digest,
                      "identity": manifest["identity"], "logical_slots": manifest["expected_logical_slots"],
                      "unique_frame_classes": manifest["unique_frame_classes"],
                      "target_common_k": manifest["measurement"]["target_common_k"],
                      "legacy_or_unexpected_root_pngs": len(manifest["legacy_or_unexpected_root_pngs"]),
                      "source_images_written": 0}, ensure_ascii=False))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    frozen = sub.add_parser("freeze", help="pin every native slot, source, reference, and art standard")
    frozen.add_argument("--inventory", required=True)
    frozen.add_argument("--inventory-sha256", required=True)
    frozen.add_argument("--official-manifest", required=True)
    frozen.add_argument("--official-manifest-sha256", required=True)
    frozen.add_argument("--identity", required=True)
    frozen.add_argument("--faction", required=True)
    frozen.add_argument("--form", required=True)
    frozen.add_argument("--masters-dir", required=True)
    frozen.add_argument("--art-standard", required=True)
    frozen.add_argument("--art-standard-sha256", required=True)
    frozen.add_argument("--art-standard-version", required=True)
    frozen.add_argument("--operation-id", required=True)
    frozen.add_argument("--created-local", required=True)
    frozen.add_argument("--target-common-k", type=float)
    frozen.add_argument("--manifest-out", required=True)
    frozen.set_defaults(func=freeze_command)

    for action in ("check", "run", "verify"):
        item = sub.add_parser(action)
        item.add_argument("--manifest", required=True)
        item.add_argument("--manifest-sha256", required=True)
        item.add_argument("--outdir", required=True)
        item.add_argument("--operation-id", required=True)
        if action == "verify":
            item.add_argument("--limb-review")
            item.add_argument("--limb-review-sha256")
        item.set_defaults(func=run_manifest_command, action=action)
    return parser


def run_manifest_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve(strict=True)
    manifest, digest = read_json(manifest_path, args.manifest_sha256)
    validate_manifest(manifest_path, args.manifest_sha256)
    manifest["manifest_sha256"] = digest
    if manifest.get("operation_id") != args.operation_id:
        raise SetError("OPERATION_ID_MISMATCH")
    outdir = Path(args.outdir).resolve(strict=False)
    if args.action == "check":
        source_rows = revalidate_sources(manifest)
        print(json.dumps({"status": "READY", "expected_slots": len(manifest["logical_slots"]),
                          "unique_frame_classes": len(source_rows), "manifest_sha256": digest,
                          "target_common_k": manifest["measurement"]["target_common_k"],
                          "upscales": 0, "source_pixels_mutated": False, "images_written": 0,
                          "formal_native_registration": False, "game_ready": False}, ensure_ascii=False))
        return 0
    if args.action == "run":
        report = execute_normalize(manifest, outdir)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.limb_review and not args.limb_review_sha256:
        raise SetError("LIMB_REVIEW_SHA256_REQUIRED")
    review_path = Path(args.limb_review).resolve(strict=True) if args.limb_review else None
    report = verify_normalized(manifest, outdir, review_path, args.limb_review_sha256 if review_path else None)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] != "VISUAL_REVIEW_REQUIRED" else 0


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SetError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"schema": "h3.creature.master-normalization.error.v1", "status": "BLOCKED",
                          "code": str(error).split(":", 1)[0], "error": str(error),
                          "images_written": 0, "source_pixels_mutated": False,
                          "game_ready": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
