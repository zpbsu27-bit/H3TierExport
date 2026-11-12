#!/usr/bin/env python3
"""Safely batch legacy indexed frames through GrayFace H3DefTool.

This adapter invokes the separately licensed upstream executable. It does not
contain or port GrayFace's Pascal implementation, and it never turns RGBA/HD
masters into indexed input. Inputs must be full-canvas legacy 8-bit indexed BMP
frames already prepared at native origin [0, 0]. The vendor can remap palette
colors; structural DEF checks do not establish pixel fidelity, art approval, or
game-runtime behavior.

Input manifest (JSON):
{
  "schema": "h3.deftool.batch-input.v1",
  "batch_id": "skeleton-smoke",
  "entries": [{
    "entry_id": "CSKELE",
    "output_name": "CSKELE",
    "source_kind": "legacy_indexed_frames",
    "canvas": {"width": 64, "height": 64, "native_origin": [0, 0]},
    "options": {"generate_selection": true},
    "groups": [{"native_group": 0, "frames": [
      {"native_frame": 0, "source": "frames/frame0.bmp",
       "resource": "CSKELE0.BMP"}
    ]}]
  }]
}

Commands: plan -> check -> run -> verify. A run is single-use: an immutable
run lock is created before launching the vendor process; a timeout, crash, or
partial batch needs a new reviewed plan rather than a blind rerun.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_INPUT = "h3.deftool.batch-input.v1"
SCHEMA_PLAN = "h3.deftool.batch-plan.v1"
SCHEMA_RUN = "h3.deftool.batch-run.v1"
SCHEMA_VERIFY = "h3.deftool.batch-verify.v1"
SOURCE_REPOSITORY = "https://github.com/GrayFace/Misc"
SOURCE_COMMIT = "c790a96329b2fd06e369641d76405ef0821b5219"
TRUSTED_NATIVE_VALIDATOR_SHA256 = "ab920bd179feabd2963c7240993845df340cffbb47010bab05b240faf15c093d"
PROCESS_TIMEOUT_SECONDS = 30
TOOL_ROOT = resolve_tool_root(__file__)
DEFAULT_EXPERIMENTS_ROOT = TOOL_ROOT / "build" / "experiments" / "deftool"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_SAFE_EXPERIMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SAFE_OUTPUT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$")
_SAFE_RESOURCE = re.compile(r"^[A-Za-z0-9_.-]{1,12}\.BMP$", re.IGNORECASE)
TYPE42_SPECIAL_PALETTE_INDICES = (0, 1, 4, 5, 6, 7)


class ContractError(RuntimeError):
    """Input, provenance, or state failed a pinned contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def _write_json_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".writing")
    if temp.exists():
        raise ContractError(f"incomplete prior write exists; inspect it: {temp}")
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _require_keys(value: dict[str, Any], required: set[str], where: str,
                  optional: set[str] | None = None) -> None:
    optional = optional or set()
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if extra:
            parts.append("unknown=" + ",".join(extra))
        raise ContractError(f"{where}: " + " ".join(parts))


def _safe_identifier(value: Any, where: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractError(f"{where} must be a safe ASCII identifier")
    return value


def _resolve_source(raw: Any, job_path: Path, where: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(f"{where} must be a nonempty path")
    path = Path(raw)
    if not path.is_absolute():
        path = job_path.parent / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{where} does not exist: {path}") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ContractError(f"{where} must resolve to a regular non-symlink file")
    return resolved


def _inspect_indexed_bmp(path: Path) -> dict[str, Any]:
    """Read only the BMP header/palette needed to refuse implicit conversion."""
    data = path.read_bytes()
    if len(data) < 14 + 40 or data[:2] != b"BM":
        raise ContractError(f"legacy input must be an uncompressed 8-bit BMP: {path}")
    file_size, pixel_offset = struct.unpack_from("<IxxxxI", data, 2)
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40 or len(data) < 14 + dib_size:
        raise ContractError(f"unsupported BMP DIB header: {path}")
    width, signed_height, planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    colors_used = struct.unpack_from("<I", data, 46)[0]
    palette_count = colors_used or 256
    palette_start = 14 + dib_size
    palette_end = palette_start + palette_count * 4
    if (width <= 0 or signed_height == 0 or planes != 1 or bpp != 8 or
            compression != 0 or palette_count != 256 or pixel_offset < palette_end or
            pixel_offset > len(data)):
        raise ContractError(
            f"{path}: expected 8-bit indexed BMP, 256 palette entries, no compression"
        )
    height = abs(signed_height)
    row_bytes = ((width + 3) // 4) * 4
    if pixel_offset + row_bytes * height > len(data):
        raise ContractError(f"truncated BMP pixel plane: {path}")
    palette = data[palette_start:palette_end]
    special_rgb = {}
    for index in TYPE42_SPECIAL_PALETTE_INDICES:
        blue, green, red, _reserved = palette[index * 4:index * 4 + 4]
        special_rgb[str(index)] = [red, green, blue]
    return {
        "format": "BMP-8bit-indexed",
        "width": width,
        "height": height,
        "palette_entries": palette_count,
        "palette_sha256": _sha256_bytes(palette),
        "special_palette_rgb": special_rgb,
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
    }


def _runtime_files(tool_dir: Path) -> list[dict[str, Any]]:
    """Pin only the EXE and its adjacent runtime data, never the user's INI."""
    exe = tool_dir / "h3deftool.exe"
    if not exe.is_file() or exe.is_symlink():
        raise ContractError(f"missing regular h3deftool.exe: {exe}")
    candidates = [exe]
    for name in ("mondata.dat", "grid.pcx"):
        item = tool_dir / name
        if item.exists():
            if not item.is_file() or item.is_symlink():
                raise ContractError(f"unexpected runtime item: {item}")
            candidates.append(item)
    for name in ("Language", "GameLanguage"):
        root = tool_dir / name
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise ContractError(f"unexpected runtime directory: {root}")
            for item in sorted(root.rglob("*")):
                if item.is_symlink():
                    raise ContractError(f"runtime symlink is not allowed: {item}")
                if item.is_file():
                    candidates.append(item)
    rows = []
    for item in candidates:
        relative = item.relative_to(tool_dir).as_posix()
        if Path(relative).suffix.lower() == ".lod":
            raise ContractError("runtime bundle must never include a LOD archive")
        rows.append({"path": relative, "bytes": item.stat().st_size,
                     "sha256": _sha256_file(item)})
    return sorted(rows, key=lambda row: row["path"].casefold())


def _validator_path(root: Path) -> Path:
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"native validator root does not exist: {root}") from exc
    script = root / "tools" / "build_official_def_frame_map.py"
    if not script.is_file():
        alt = root / "scripts" / "build_official_def_frame_map.py"
        if alt.is_file():
            script = alt
        else:
            raise ContractError(f"native DEF parser not found: {script}")
    script = script.resolve(strict=True)
    actual = _sha256_file(script)
    if actual != TRUSTED_NATIVE_VALIDATOR_SHA256:
        raise ContractError(
            "native DEF validator is not the reviewed build_official_def_frame_map.py; "
            f"expected {TRUSTED_NATIVE_VALIDATOR_SHA256}, got {actual}"
        )
    return script


def _normalize_job(job_path: Path) -> dict[str, Any]:
    job_path = job_path.resolve(strict=True)
    job = _read_json(job_path)
    _require_keys(job, {"schema", "batch_id", "entries"}, "input")
    if job["schema"] != SCHEMA_INPUT:
        raise ContractError(f"input schema must be {SCHEMA_INPUT}")
    batch_id = _safe_identifier(job["batch_id"], "batch_id", _SAFE_ID)
    if not isinstance(job["entries"], list) or not job["entries"]:
        raise ContractError("entries must be a nonempty array")
    normalized_entries = []
    entry_ids: set[str] = set()
    for entry_no, entry in enumerate(job["entries"]):
        where = f"entries[{entry_no}]"
        if not isinstance(entry, dict):
            raise ContractError(f"{where} must be an object")
        _require_keys(entry, {"entry_id", "output_name", "source_kind", "canvas",
                              "options", "groups"}, where)
        entry_id = _safe_identifier(entry["entry_id"], f"{where}.entry_id", _SAFE_ID)
        if entry_id.casefold() in entry_ids:
            raise ContractError(f"duplicate entry_id: {entry_id}")
        entry_ids.add(entry_id.casefold())
        output_name = _safe_identifier(entry["output_name"], f"{where}.output_name",
                                       _SAFE_OUTPUT)
        if entry["source_kind"] != "legacy_indexed_frames":
            raise ContractError(f"{where}: only legacy_indexed_frames are accepted")
        canvas = entry["canvas"]
        if not isinstance(canvas, dict):
            raise ContractError(f"{where}.canvas must be an object")
        _require_keys(canvas, {"width", "height", "native_origin"}, f"{where}.canvas")
        width, height = canvas["width"], canvas["height"]
        origin = canvas["native_origin"]
        if (not isinstance(width, int) or not isinstance(height, int) or
                isinstance(width, bool) or isinstance(height, bool) or
                width <= 0 or height <= 0 or width > 8192 or height > 8192):
            raise ContractError(f"{where}.canvas width/height must be positive bounded integers")
        if (not isinstance(origin, list) or len(origin) != 2 or
                any(not isinstance(v, int) or isinstance(v, bool) for v in origin)):
            raise ContractError(f"{where}.canvas.native_origin must be [integer, integer]")
        if origin != [0, 0]:
            raise ContractError(
                f"{where}: only full-canvas inputs already prepared at native_origin [0, 0] are supported"
            )
        options = entry["options"]
        if not isinstance(options, dict):
            raise ContractError(f"{where}.options must be an object")
        _require_keys(options, {"generate_selection"}, f"{where}.options")
        if not isinstance(options["generate_selection"], bool):
            raise ContractError(f"{where}.options.generate_selection must be boolean")
        groups = entry["groups"]
        if not isinstance(groups, list) or not groups:
            raise ContractError(f"{where}.groups must be a nonempty array")
        normalized_groups = []
        group_ids: set[int] = set()
        resources: set[str] = set()
        entry_palette_hashes: set[str] = set()
        for group_no, group in enumerate(groups):
            gwhere = f"{where}.groups[{group_no}]"
            if not isinstance(group, dict):
                raise ContractError(f"{gwhere} must be an object")
            _require_keys(group, {"native_group", "frames"}, gwhere)
            gid = group["native_group"]
            if not isinstance(gid, int) or isinstance(gid, bool) or not 0 <= gid <= 21:
                raise ContractError(f"{gwhere}.native_group must be in 0..21")
            if gid in group_ids:
                raise ContractError(f"duplicate native group id {gid}")
            group_ids.add(gid)
            frames = group["frames"]
            if not isinstance(frames, list) or not frames or len(frames) > 256:
                raise ContractError(f"{gwhere}.frames must contain 1..256 frames")
            normalized_frames = []
            for frame_no, frame in enumerate(frames):
                fwhere = f"{gwhere}.frames[{frame_no}]"
                if not isinstance(frame, dict):
                    raise ContractError(f"{fwhere} must be an object")
                _require_keys(frame, {"native_frame", "source", "resource"}, fwhere)
                native_frame = frame["native_frame"]
                if (not isinstance(native_frame, int) or isinstance(native_frame, bool) or
                        native_frame != frame_no):
                    raise ContractError(
                        f"{fwhere}.native_frame must match its zero-based ordered position"
                    )
                resource = frame["resource"]
                if (not isinstance(resource, str) or not resource.isascii() or
                        not _SAFE_RESOURCE.fullmatch(resource)):
                    raise ContractError(
                        f"{fwhere}.resource must be a basename of at most 12 ASCII chars ending .BMP"
                    )
                if len(resource.encode("ascii")) > 12:
                    raise ContractError(
                        f"{fwhere}.resource exceeds the 12-byte DEF frame-name limit including extension"
                    )
                resource_key = resource.upper()
                if resource_key in resources:
                    raise ContractError(f"duplicate DEF resource name: {resource}")
                resources.add(resource_key)
                source = _resolve_source(frame["source"], job_path, f"{fwhere}.source")
                image = _inspect_indexed_bmp(source)
                if (image["width"], image["height"]) != (width, height):
                    raise ContractError(
                        f"{source}: canvas {image['width']}x{image['height']} does not match "
                        f"expected {width}x{height}"
                    )
                normalized_frames.append({
                    "native_frame": native_frame,
                    "source_path": str(source),
                    "source_sha256": image["sha256"],
                    "source_bytes": image["bytes"],
                    "source_format": image["format"],
                    "palette_sha256": image["palette_sha256"],
                    "special_palette_rgb": image["special_palette_rgb"],
                    "resource": resource,
                })
            palette_hashes = {frame["palette_sha256"] for frame in normalized_frames}
            if len(palette_hashes) != 1:
                raise ContractError(f"{gwhere}: indexed palette must match across frames")
            entry_palette_hashes.update(palette_hashes)
            normalized_groups.append({"native_group": gid,
                                      "frames": normalized_frames})
        if len(entry_palette_hashes) != 1:
            raise ContractError(f"{where}: indexed palette must match across every DEF group")
        if normalized_groups != sorted(normalized_groups,
                                       key=lambda group: group["native_group"]):
            raise ContractError(f"{where}.groups must be ordered by native_group")
        normalized_entries.append({
            "entry_id": entry_id,
            "output_name": output_name,
            "source_kind": "legacy_indexed_frames",
            "not_hd_rgba_master": True,
            "canvas": {"width": width, "height": height,
                       "native_origin": [origin[0], origin[1]]},
            "indexed_palette_special_rgb": normalized_groups[0]["frames"][0]["special_palette_rgb"],
            "options": {"generate_selection": options["generate_selection"]},
            "groups": normalized_groups,
        })
    return {"batch_id": batch_id, "entries": normalized_entries,
            "job_path": str(job_path), "job_sha256": _sha256_file(job_path)}


def _render_hdl(entry: dict[str, Any], worker_relative: str = "") -> str:
    """Render the exact, source-checked creature HDL, with all side-effect toggles explicit."""
    del worker_relative  # paths intentionally stay relative to the HDL's folder
    max_group = max(group["native_group"] for group in entry["groups"])
    rows = ["[Data]", "Type=2", f"Groups Number={max_group + 1}"]
    by_id = {group["native_group"]: group for group in entry["groups"]}
    for group_id in range(max_group + 1):
        group = by_id.get(group_id)
        if group is None:
            continue
        names = [f"inputs\\{frame['resource']}" for frame in group["frames"]]
        rows.append(f"Group{group_id}=" + "|".join(names) + "|")
    rows.extend([
        "Def Path Check=1",
        f"Def Path=..\\output\\{entry['output_name']}.def",
        "Lod Path Check=0",
        "Lod Path=",
        "Bitmaps Check=0",
        "Bitmaps Lod Check=0",
        "Bitmaps Path=",
        "Bitmaps Lod Path=",
        "Standard Special Colors=0",
        "Generate Mask Files=0",
        "Pictures Extension=bmp",
        "Generate Selection=" + ("1" if entry["options"]["generate_selection"] else "0"),
        "Delete Contained Shadow=0",
        "Faint Shadow=0",
        "Shadow Type=0",
        "Shadow Shift X=0",
        "Shadow Shift Y=0",
        "ColorsBox.ColorChecks=1|1|0|0|1|1|1|1|0|",
        "ShadowColorsBox.ColorChecks=1|1|1|1|1|1|1|1|",
        "",
    ])
    return "\r\n".join(rows)


def _seal_plan(plan: dict[str, Any]) -> dict[str, Any]:
    plan.pop("plan_integrity_sha256", None)
    plan["plan_integrity_sha256"] = _sha256_bytes(_canonical_bytes(plan))
    return plan


def _validate_plan_entry_contract(entry: dict[str, Any]) -> None:
    base_keys = {"entry_id", "output_name", "source_kind", "not_hd_rgba_master",
                 "canvas", "indexed_palette_special_rgb", "options", "groups"}
    _require_keys(entry, base_keys | {"expected_native_slots", "hdl",
                                     "output_relative_path", "working_relative_path"},
                  "plan entry")
    entry_id = _safe_identifier(entry["entry_id"], "plan entry_id", _SAFE_ID)
    output_name = _safe_identifier(entry["output_name"], "plan output_name", _SAFE_OUTPUT)
    if entry["source_kind"] != "legacy_indexed_frames" or entry["not_hd_rgba_master"] is not True:
        raise ContractError(f"plan entry {entry_id} is not an explicit legacy indexed-frame entry")
    canvas = entry["canvas"]
    if (not isinstance(canvas, dict) or
            set(canvas) != {"width", "height", "native_origin"} or
            not isinstance(canvas["width"], int) or isinstance(canvas["width"], bool) or
            not isinstance(canvas["height"], int) or isinstance(canvas["height"], bool) or
            not 1 <= canvas["width"] <= 8192 or not 1 <= canvas["height"] <= 8192 or
            not isinstance(canvas["native_origin"], list) or
            len(canvas["native_origin"]) != 2 or
            any(not isinstance(v, int) or isinstance(v, bool) for v in canvas["native_origin"]) or
            canvas["native_origin"] != [0, 0]):
        raise ContractError(f"plan entry {entry_id} must pin a full canvas at native_origin [0, 0]")
    options = entry["options"]
    if (not isinstance(options, dict) or set(options) != {"generate_selection"} or
            not isinstance(options["generate_selection"], bool)):
        raise ContractError(f"plan entry {entry_id} has invalid explicit options")
    groups = entry["groups"]
    if not isinstance(groups, list) or not groups:
        raise ContractError(f"plan entry {entry_id} has no native groups")
    expected_slots = []
    group_ids: set[int] = set()
    resource_names: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"native_group", "frames"}:
            raise ContractError(f"plan entry {entry_id} has malformed group data")
        gid = group["native_group"]
        frames = group["frames"]
        if (not isinstance(gid, int) or isinstance(gid, bool) or not 0 <= gid <= 21 or
                gid in group_ids or not isinstance(frames, list) or not 1 <= len(frames) <= 256):
            raise ContractError(f"plan entry {entry_id} has invalid group id or frames")
        group_ids.add(gid)
        for frame_index, frame in enumerate(frames):
            frame_keys = {"native_frame", "source_path", "source_sha256", "source_bytes",
                          "source_format", "palette_sha256", "special_palette_rgb", "resource"}
            if not isinstance(frame, dict) or set(frame) != frame_keys:
                raise ContractError(f"plan entry {entry_id} has malformed frame data")
            resource = frame["resource"]
            if (not isinstance(frame["native_frame"], int) or isinstance(frame["native_frame"], bool) or
                    frame["native_frame"] != frame_index or
                    not isinstance(resource, str) or not resource.isascii() or
                    not _SAFE_RESOURCE.fullmatch(resource) or
                    len(resource.encode("ascii")) > 12 or
                    resource.upper() in resource_names):
                raise ContractError(f"plan entry {entry_id} has unsafe resource or frame order")
            resource_names.add(resource.upper())
            if not isinstance(frame["source_path"], str) or not frame["source_path"]:
                raise ContractError(f"plan entry {entry_id} frame source path must be a nonempty string")
            source_path = Path(frame["source_path"])
            if not source_path.is_absolute():
                raise ContractError(f"plan entry {entry_id} frame source path must be absolute")
            if (frame["source_format"] != "BMP-8bit-indexed" or
                    not isinstance(frame["source_bytes"], int) or
                    isinstance(frame["source_bytes"], bool) or frame["source_bytes"] <= 0):
                raise ContractError(f"plan entry {entry_id} frame is not an indexed BMP")
            for hash_key in ("source_sha256", "palette_sha256"):
                value = frame[hash_key]
                if (not isinstance(value, str) or len(value) != 64 or
                        any(ch not in "0123456789abcdef" for ch in value)):
                    raise ContractError(f"plan entry {entry_id} has invalid {hash_key}")
            rgb_map = frame["special_palette_rgb"]
            wanted_indices = {str(i) for i in TYPE42_SPECIAL_PALETTE_INDICES}
            if (not isinstance(rgb_map, dict) or set(rgb_map) != wanted_indices or
                    any(not isinstance(rgb, list) or len(rgb) != 3 or
                        any(not isinstance(c, int) or isinstance(c, bool) or not 0 <= c <= 255
                            for c in rgb) for rgb in rgb_map.values())):
                raise ContractError(f"plan entry {entry_id} has invalid reserved palette slots")
            expected_slots.append({"group": gid, "frame": frame_index, "resource": resource})
    if [g["native_group"] for g in groups] != sorted(group_ids):
        raise ContractError(f"plan entry {entry_id} groups are not in native order")
    if entry["expected_native_slots"] != expected_slots:
        raise ContractError(f"plan entry {entry_id} slot map differs from its group/frame list")
    expected_palette = groups[0]["frames"][0]["special_palette_rgb"]
    if entry["indexed_palette_special_rgb"] != expected_palette:
        raise ContractError(f"plan entry {entry_id} reserved palette slot contract changed")
    if entry["output_relative_path"] != f"run/{entry_id}/output/{output_name}.def":
        raise ContractError(f"plan entry {entry_id} has a noncanonical DEF output path")
    if entry["working_relative_path"] != f"run/{entry_id}/worker":
        raise ContractError(f"plan entry {entry_id} has a noncanonical worker path")
    hdl = entry["hdl"]
    if (not isinstance(hdl, dict) or set(hdl) != {"relative_path", "text", "sha256"} or
            hdl["relative_path"] != "job.hdl" or not isinstance(hdl["text"], str) or
            hdl["sha256"] != _sha256_bytes(hdl["text"].encode("utf-8"))):
        raise ContractError(f"plan entry {entry_id} has invalid pinned HDL")
    canonical_hdl = _render_hdl(entry)
    if hdl["text"] != canonical_hdl:
        raise ContractError(f"plan entry {entry_id} HDL differs from the canonical safety contract")


def _check_plan_integrity(plan: dict[str, Any]) -> None:
    digest = plan.get("plan_integrity_sha256")
    if not isinstance(digest, str):
        raise ContractError("plan is missing plan_integrity_sha256")
    unsigned = dict(plan)
    unsigned.pop("plan_integrity_sha256", None)
    if _sha256_bytes(_canonical_bytes(unsigned)) != digest:
        raise ContractError("plan integrity digest mismatch")


def make_plan(job_path: Path, tool_dir: Path, native_validator_root: Path,
              experiments_root: Path = DEFAULT_EXPERIMENTS_ROOT) -> Path:
    job_path = job_path.resolve(strict=True)
    tool_dir = tool_dir.resolve(strict=True)
    validator_script = _validator_path(native_validator_root)
    normalized = _normalize_job(job_path)
    runtime = _runtime_files(tool_dir)
    exe_row = next(row for row in runtime if row["path"].casefold() == "h3deftool.exe")
    validator_hash = _sha256_file(validator_script)
    unsigned = {
        "schema": SCHEMA_PLAN,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_id": normalized["batch_id"],
        "input_manifest": {"path": normalized["job_path"],
                           "sha256": normalized["job_sha256"]},
        "upstream": {"repository": SOURCE_REPOSITORY,
                     "commit": SOURCE_COMMIT,
                     "license": "GNU GPL-2.0-or-later WITH upstream-author distribution exception; adapter invokes binary and copies no Pascal code"},
        "tool": {"path": str(tool_dir / "h3deftool.exe"),
                 "sha256": exe_row["sha256"],
                 "runtime_files": runtime,
                 "user_ini_copied": False},
        "native_validator": {"path": str(validator_script),
                             "sha256": validator_hash,
                             "callable": "parse_def(bytes)"},
        "execution": {"argument_contract": ["/g", "<entry.hdl>"],
                      "timeout_seconds": PROCESS_TIMEOUT_SECONDS,
                      "one_process_per_entry": True,
                      "exit_code_is_advisory": True,
                      "retry_policy": "never reuse a run lock; create a new reviewed plan"},
        "output_policy": {"root": "build/experiments/deftool", "lod_write": False,
                          "bitmap_lod_write": False, "outside_root_write": False,
                          "legacy_output_only": True, "hd_rgba_master": False,
                          "input_geometry": "full-canvas indexed BMP at native_origin [0, 0]",
                          "palette_remapping": True,
                          "pixel_color_fidelity_verified": False,
                          "art_reviewed": False,
                          "native_runtime_verified": False,
                          "validation_scope": "DEF structure and slot mapping only"},
        "entries": [],
    }
    for entry in normalized["entries"]:
        hdl = _render_hdl(entry)
        unsigned["entries"].append({
            **entry,
            "expected_native_slots": [
                {"group": group["native_group"],
                 "frame": frame["native_frame"],
                 "resource": frame["resource"]}
                for group in entry["groups"] for frame in group["frames"]
            ],
            "hdl": {"relative_path": "job.hdl", "text": hdl,
                    "sha256": _sha256_bytes(hdl.encode("utf-8"))},
            "output_relative_path": f"run/{entry['entry_id']}/output/{entry['output_name']}.def",
            "working_relative_path": f"run/{entry['entry_id']}/worker",
        })
    stable_plan_inputs = dict(unsigned)
    stable_plan_inputs.pop("created_utc", None)
    fingerprint = _sha256_bytes(_canonical_bytes(stable_plan_inputs))
    experiment_root = experiments_root.resolve()
    experiment_name = f"{normalized['batch_id']}-{fingerprint[:16]}"
    experiment_dir = experiment_root / experiment_name
    if experiment_dir.exists():
        raise ContractError(f"experiment directory already exists; refusing overwrite: {experiment_dir}")
    plan = dict(unsigned)
    plan["plan_fingerprint"] = fingerprint
    plan["experiment_relative_path"] = experiment_name
    _seal_plan(plan)
    experiment_dir.mkdir(parents=True, exist_ok=False)
    plan_path = experiment_dir / "plan.json"
    _write_json_new(plan_path, plan)
    return plan_path


def _load_plan(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.resolve(strict=True)
    plan = _read_json(path)
    _require_keys(plan, {
        "schema", "created_utc", "batch_id", "input_manifest", "upstream", "tool",
        "native_validator", "execution", "output_policy", "entries", "plan_fingerprint",
        "experiment_relative_path", "plan_integrity_sha256",
    }, "plan")
    if plan.get("schema") != SCHEMA_PLAN:
        raise ContractError(f"plan schema must be {SCHEMA_PLAN}")
    _check_plan_integrity(plan)
    manifest = plan["input_manifest"]
    if (not isinstance(manifest, dict) or set(manifest) != {"path", "sha256"} or
            not isinstance(manifest["path"], str) or not manifest["path"] or
            not isinstance(manifest["sha256"], str) or len(manifest["sha256"]) != 64 or
            any(ch not in "0123456789abcdef" for ch in manifest["sha256"])):
        raise ContractError("plan input_manifest must pin an absolute path and SHA-256")
    if not Path(manifest["path"]).is_absolute():
        raise ContractError("plan input_manifest path must be absolute")
    validator = plan.get("native_validator")
    if (not isinstance(validator, dict) or
            validator.get("sha256") != TRUSTED_NATIVE_VALIDATOR_SHA256 or
            Path(str(validator.get("path", ""))).name != "build_official_def_frame_map.py"):
        raise ContractError("plan does not pin the allowlisted native DEF parser")
    batch_id = _safe_identifier(plan.get("batch_id"), "plan batch_id", _SAFE_ID)
    fingerprint = plan.get("plan_fingerprint")
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64 or
            any(ch not in "0123456789abcdef" for ch in fingerprint)):
        raise ContractError("plan has an invalid plan_fingerprint")
    stable = dict(plan)
    for key in ("created_utc", "plan_fingerprint", "experiment_relative_path",
                "plan_integrity_sha256"):
        stable.pop(key, None)
    if _sha256_bytes(_canonical_bytes(stable)) != fingerprint:
        raise ContractError("plan_fingerprint does not match the frozen plan contents")
    expected_directory = f"{batch_id}-{fingerprint[:16]}"
    if plan.get("experiment_relative_path") != expected_directory:
        raise ContractError("plan experiment directory is not bound to its frozen fingerprint")
    experiment_dir = path.parent.resolve(strict=True)
    rel = Path(plan.get("experiment_relative_path", ""))
    if len(rel.parts) != 1 or rel.name != plan["experiment_relative_path"]:
        raise ContractError("invalid experiment_relative_path")
    _safe_identifier(rel.name, "experiment_relative_path", _SAFE_EXPERIMENT)
    if experiment_dir.name != rel.name:
        raise ContractError("plan moved outside its pinned experiment directory")
    expected_root = DEFAULT_EXPERIMENTS_ROOT.resolve()
    # Production CLI outputs must stay under this repository-owned experiment root.
    if not experiment_dir.is_relative_to(expected_root):
        raise ContractError(f"plan is outside the owned experiment root: {experiment_dir}")
    upstream = plan.get("upstream")
    if not isinstance(upstream, dict) or upstream.get("commit") != SOURCE_COMMIT:
        raise ContractError("plan does not pin the reviewed H3DefTool source commit")
    output_policy = plan.get("output_policy")
    safe_output_policy = {"root": "build/experiments/deftool", "lod_write": False,
                          "bitmap_lod_write": False, "outside_root_write": False,
                          "legacy_output_only": True, "hd_rgba_master": False}
    if (not isinstance(output_policy, dict) or
            any(output_policy.get(key) != value for key, value in safe_output_policy.items())):
        raise ContractError("plan output policy does not preserve the isolated legacy-DEF boundary")
    execution = plan.get("execution")
    if (not isinstance(execution, dict) or
            execution.get("argument_contract") != ["/g", "<entry.hdl>"] or
            execution.get("timeout_seconds") != PROCESS_TIMEOUT_SECONDS or
            execution.get("one_process_per_entry") is not True or
            execution.get("exit_code_is_advisory") is not True):
        raise ContractError("plan execution contract differs from the fixed /g worker policy")
    if not isinstance(plan.get("entries"), list) or not plan["entries"]:
        raise ContractError("plan entries must be a nonempty array")
    seen_entry_ids: set[str] = set()
    for entry in plan["entries"]:
        if not isinstance(entry, dict):
            raise ContractError("plan entries must contain objects")
        entry_id = _safe_identifier(entry.get("entry_id"), "plan entry_id", _SAFE_ID)
        if entry_id.casefold() in seen_entry_ids:
            raise ContractError(f"duplicate plan entry_id: {entry_id}")
        seen_entry_ids.add(entry_id.casefold())
        _validate_plan_entry_contract(entry)
    return plan, experiment_dir


def _check_pins(plan: dict[str, Any], experiment_dir: Path,
                require_unstarted: bool) -> dict[str, Any]:
    job = plan["input_manifest"]
    job_path = Path(job["path"])
    if not job_path.is_file():
        raise ContractError("input manifest changed or disappeared after planning")
    normalized = _normalize_job(job_path)
    if normalized["job_sha256"] != job["sha256"]:
        raise ContractError("input manifest changed after planning")
    if normalized["batch_id"] != plan["batch_id"] or len(normalized["entries"]) != len(plan["entries"]):
        raise ContractError("source manifest no longer matches the pinned entry set")
    base_keys = ("entry_id", "output_name", "source_kind", "not_hd_rgba_master",
                 "canvas", "indexed_palette_special_rgb", "options", "groups")
    for expected, actual in zip(plan["entries"], normalized["entries"]):
        if any(expected.get(key) != actual.get(key) for key in base_keys):
            raise ContractError(
                f"source manifest inputs or normalized entry contract changed for {expected['entry_id']}"
            )
    tool_exe = Path(plan["tool"]["path"])
    tool_dir = tool_exe.parent
    current_runtime = _runtime_files(tool_dir)
    if current_runtime != plan["tool"]["runtime_files"]:
        raise ContractError("vendor EXE or adjacent runtime data changed after planning")
    validator = Path(plan["native_validator"]["path"])
    if (plan["native_validator"].get("sha256") != TRUSTED_NATIVE_VALIDATOR_SHA256 or
            not validator.is_file() or _sha256_file(validator) != TRUSTED_NATIVE_VALIDATOR_SHA256):
        raise ContractError("native DEF validator changed or disappeared after planning")
    run_lock = experiment_dir / "run.lock"
    run_path = experiment_dir / "run.json"
    if require_unstarted and (run_lock.exists() or run_path.exists()):
        raise ContractError("run already started; do not retry this plan")
    return {"input_manifest_sha256": job["sha256"],
            "tool_exe_sha256": plan["tool"]["sha256"],
            "validator_sha256": plan["native_validator"]["sha256"],
            "input_frame_count": sum(len(group["frames"])
                                      for entry in normalized["entries"]
                                      for group in entry["groups"])}


def check_plan(plan_path: Path, experiments_root: Path = DEFAULT_EXPERIMENTS_ROOT) -> dict[str, Any]:
    plan, experiment_dir = _load_plan(plan_path)
    expected = experiments_root.resolve()
    if not experiment_dir.is_relative_to(expected):
        raise ContractError(f"plan is outside selected experiment root: {experiment_dir}")
    pins = _check_pins(plan, experiment_dir, require_unstarted=True)
    return {"status": "PASS", "plan": str(plan_path.resolve()),
            "plan_fingerprint": plan["plan_fingerprint"], **pins,
            "all_outputs_inside_experiment_root": True,
            "lod_write": False, "bitmap_lod_write": False,
            "rgba_conversion": False}


def _write_worker_ini(worker: Path) -> None:
    # App-level paths are blank and backups disabled. The original INI is never copied.
    ini = "\r\n".join([
        "[Data]", "Lod Path=", "Bitmaps Lod Path=", "Frames Dir=inputs",
        "Shadow Dir=inputs", "[Options]", "Recent Files=",
        "Backup Files From Lods=0", "Ovewrite Last Backup=0",
        "Backup Pictures Before Changing=0", "Language=chinese",
        "GameLanguage=chinese", "Create Msk Files But Not Msg=1", "",
    ])
    (worker / "h3deftool.ini").write_text(ini, encoding="ascii", newline="")


def _copy_runtime(tool_exe: Path, runtime_files: list[dict[str, Any]], worker: Path) -> None:
    source_root = tool_exe.parent
    worker.mkdir(parents=True, exist_ok=False)
    for row in runtime_files:
        relative = Path(row["path"])
        source = source_root / relative
        destination = worker / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _sha256_file(destination) != row["sha256"]:
            raise ContractError(f"runtime copy digest mismatch: {relative}")
    _write_worker_ini(worker)
    if any(worker.rglob("*.lod")) or any(worker.rglob("*.LOD")):
        raise ContractError("LOD files must not be present in the private worker")


def _write_entry_hdl(entry: dict[str, Any], worker: Path) -> Path:
    hdl_text = entry["hdl"]["text"]
    if _sha256_bytes(hdl_text.encode("utf-8")) != entry["hdl"]["sha256"]:
        raise ContractError(f"pinned HDL digest mismatch: {entry['entry_id']}")
    path = worker / entry["hdl"]["relative_path"]
    path.write_text(hdl_text, encoding="ascii", newline="")
    return path


def _stage_inputs(entry: dict[str, Any], worker: Path) -> dict[str, str]:
    inputs = worker / "inputs"
    inputs.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for group in entry["groups"]:
        for frame in group["frames"]:
            destination = inputs / frame["resource"]
            shutil.copyfile(frame["source_path"], destination)
            digest = _sha256_file(destination)
            if digest != frame["source_sha256"]:
                raise ContractError(f"staged frame digest mismatch: {frame['resource']}")
            hashes[frame["resource"]] = digest
    return hashes


def _child_command(exe: Path, hdl: Path) -> list[str]:
    if os.name == "nt":
        if exe.suffix.casefold() in {".bat", ".cmd"}:
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/d", "/c", str(exe), "/g", str(hdl)]
    return [str(exe), "/g", str(hdl)]


def run_plan(plan_path: Path) -> dict[str, Any]:
    plan, experiment_dir = _load_plan(plan_path)
    pins = _check_pins(plan, experiment_dir, require_unstarted=True)
    lock_path = experiment_dir / "run.lock"
    with lock_path.open("x", encoding="ascii") as stream:
        stream.write(f"pid={os.getpid()}\ncreated_utc={datetime.now(timezone.utc).isoformat()}\n")
        stream.flush()
        os.fsync(stream.fileno())
    receipt: dict[str, Any] = {
        "schema": SCHEMA_RUN, "state": "running",
        "plan_fingerprint": plan["plan_fingerprint"],
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": pins, "timeout_seconds": PROCESS_TIMEOUT_SECONDS,
        "entries": [],
    }
    run_path = experiment_dir / "run.json"
    _write_json_new(run_path, receipt)
    tool_exe = Path(plan["tool"]["path"])
    for entry in plan["entries"]:
        result: dict[str, Any] = {"entry_id": entry["entry_id"], "state": "starting"}
        receipt["entries"].append(result)
        _write_json_atomic(run_path, receipt)
        entry_root = experiment_dir / "run" / entry["entry_id"]
        worker = entry_root / "worker"
        output_dir = entry_root / "output"
        if entry_root.exists():
            result.update(state="failed", error="entry run directory already exists")
            receipt["state"] = "failed"
            _write_json_atomic(run_path, receipt)
            raise ContractError("entry run directory already exists; refusing overwrite")
        output_dir.mkdir(parents=True, exist_ok=False)
        try:
            _copy_runtime(tool_exe, plan["tool"]["runtime_files"], worker)
            result["staged_input_sha256"] = _stage_inputs(entry, worker)
            hdl = _write_entry_hdl(entry, worker)
            command = _child_command(worker / tool_exe.name, hdl)
            result["command"] = command
            result["pid"] = None
            result["exit_code"] = None
            result["exit_code_semantics"] = "advisory; native parse is authoritative"
            _write_json_atomic(run_path, receipt)
            stdout_path = entry_root / "stdout.log"
            stderr_path = entry_root / "stderr.log"
            with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
                process = subprocess.Popen(command, cwd=worker, stdin=subprocess.DEVNULL,
                                           stdout=stdout_stream, stderr=stderr_stream,
                                           shell=False)
                result["pid"] = process.pid
                _write_json_atomic(run_path, receipt)
                try:
                    code = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()  # only the PID created directly above
                    process.wait()
                    result.update(state="timed_out", timeout_seconds=PROCESS_TIMEOUT_SECONDS)
                    receipt["state"] = "timed_out"
                    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    _write_json_atomic(run_path, receipt)
                    return receipt
            result["exit_code"] = code
            output = experiment_dir / entry["output_relative_path"]
            if not output.is_file() or output.stat().st_size == 0:
                result.update(state="failed", error="vendor process produced no nonempty DEF")
                receipt["state"] = "failed"
                receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _write_json_atomic(run_path, receipt)
                return receipt
            result.update(state="candidate_created", output_path=str(output),
                          output_bytes=output.stat().st_size,
                          output_sha256=_sha256_file(output))
            _write_json_atomic(run_path, receipt)
        except Exception as exc:
            result.update(state="failed", error=f"{type(exc).__name__}: {exc}")
            receipt["state"] = "failed"
            receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _write_json_atomic(run_path, receipt)
            raise
    receipt["state"] = "awaiting_verification"
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_json_atomic(run_path, receipt)
    return receipt


def _load_native_parser(path: Path):
    actual = _sha256_file(path)
    if actual != TRUSTED_NATIVE_VALIDATOR_SHA256:
        raise ContractError("refusing to execute a non-allowlisted native DEF parser")
    spec = importlib.util.spec_from_file_location("h3_native_def_validator", path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load native parser module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = getattr(module, "parse_def", None)
    if not callable(parser):
        raise ContractError("native validator has no callable parse_def(bytes)")
    return parser


def verify_plan(plan_path: Path) -> dict[str, Any]:
    plan, experiment_dir = _load_plan(plan_path)
    _check_pins(plan, experiment_dir, require_unstarted=False)
    run_path = experiment_dir / "run.json"
    if not run_path.is_file():
        raise ContractError("run receipt is missing")
    run = _read_json(run_path)
    if (run.get("schema") != SCHEMA_RUN or
            run.get("plan_fingerprint") != plan["plan_fingerprint"]):
        raise ContractError("run receipt does not match this plan")
    run_state = run.get("state")
    if run_state not in {"awaiting_verification", "verified", "structurally_verified"}:
        raise ContractError(f"run is not ready for verification: {run.get('state')}")
    read_only_recheck = run_state != "awaiting_verification"
    parser = _load_native_parser(Path(plan["native_validator"]["path"]))
    run_entries = {entry.get("entry_id"): entry for entry in run.get("entries", [])}
    checks = []
    error_text = None
    try:
        for expected in plan["entries"]:
            entry_result = run_entries.get(expected["entry_id"])
            if not entry_result or entry_result.get("state") != "candidate_created":
                raise ContractError(f"entry lacks candidate output: {expected['entry_id']}")
            output = experiment_dir / expected["output_relative_path"]
            if (not output.is_file() or _sha256_file(output) != entry_result.get("output_sha256")):
                raise ContractError(f"DEF output changed since run: {output}")
            output_bytes = output.read_bytes()
            if _sha256_bytes(output_bytes) != entry_result.get("output_sha256"):
                raise ContractError(f"DEF output changed while reading: {output}")
            if len(output_bytes) < 784:
                raise ContractError(f"{output}: missing 784-byte DEF header and palette")
            parsed = parser(output_bytes)
            if parsed.get("type") != 0x42:
                raise ContractError(f"{output}: expected DEF type 0x42 creature")
            canvas = expected["canvas"]
            palette_bytes = output_bytes[16:784]
            for index, rgb in expected["indexed_palette_special_rgb"].items():
                offset = int(index) * 3
                if list(palette_bytes[offset:offset + 3]) != rgb:
                    raise ContractError(f"{output}: legacy special palette index {index} changed")
            if [parsed.get("width"), parsed.get("height")] != [canvas["width"], canvas["height"]]:
                raise ContractError(f"{output}: full canvas differs from pinned native canvas")
            groups = parsed.get("groups")
            if not isinstance(groups, list):
                raise ContractError(f"{output}: parser did not return groups")
            wanted_groups = expected["groups"]
            if [group.get("group_id") for group in groups] != [g["native_group"] for g in wanted_groups]:
                raise ContractError(f"{output}: native group sequence differs from the plan")
            group_checks = []
            for actual, wanted in zip(groups, wanted_groups):
                frames = actual.get("frames")
                expected_frames = wanted["frames"]
                if actual.get("frame_count") != len(expected_frames) or not isinstance(frames, list):
                    raise ContractError(f"{output}: frame count differs for group {wanted['native_group']}")
                if len(frames) != len(expected_frames):
                    raise ContractError(f"{output}: parser frame list length mismatch")
                for index, (actual_frame, wanted_frame) in enumerate(zip(frames, expected_frames)):
                    if actual_frame.get("group_id") != wanted["native_group"] or actual_frame.get("frame_index") != index:
                        raise ContractError(f"{output}: frame order mismatch in group {wanted['native_group']}")
                    if actual_frame.get("resource", "").casefold() != wanted_frame["resource"].casefold():
                        raise ContractError(f"{output}: frame resource/name mismatch at group/frame "
                                            f"{wanted['native_group']}/{index}")
                    header = actual_frame.get("frame_header")
                    if not isinstance(header, list) or len(header) < 8:
                        raise ContractError(f"{output}: missing parsed frame header")
                    if header[1] != 1:
                        raise ContractError(f"{output}: expected compression 1, got {header[1]}")
                    if header[2:4] != [canvas["width"], canvas["height"]]:
                        raise ContractError(f"{output}: frame canvas differs at "
                                            f"{wanted['native_group']}/{index}")
                    frame_width, frame_height, frame_left, frame_top = header[4:8]
                    if (any(not isinstance(v, int) or isinstance(v, bool)
                            for v in (frame_width, frame_height, frame_left, frame_top)) or
                            min(frame_width, frame_height, frame_left, frame_top) < 0 or
                            frame_left + frame_width > canvas["width"] or
                            frame_top + frame_height > canvas["height"]):
                        raise ContractError(
                            f"{output}: frame crop rectangle outside full canvas at "
                            f"group/frame {wanted['native_group']}/{index}"
                        )
                group_checks.append({"native_group": wanted["native_group"],
                                     "frame_count": len(expected_frames),
                                     "compression": 1,
                                     "frame_crop_rectangles_inside_canvas": True})
            checks.append({"entry_id": expected["entry_id"], "status": "STRUCTURE_PASS",
                           "type": "0x42", "canvas": [canvas["width"], canvas["height"]],
                           "native_origin": [0, 0],
                           "groups": group_checks, "output_sha256": _sha256_file(output)})
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
    adapter_sha = _sha256_file(Path(__file__).resolve())
    verification = {
        "schema": SCHEMA_VERIFY,
        "state": "rejected" if error_text else "structurally_verified",
        "status": "FAILED" if error_text else "STRUCTURE_PASS",
        "scope": "DEF structure, slot mapping and crop geometry only",
        "palette_remapping": True,
        "reserved_palette_slots_structurally_checked": True,
        "pixel_color_fidelity_verified": False,
        "art_reviewed": False,
        "native_runtime_verified": False,
        "full_canvas_native_origin": [0, 0],
        "plan_fingerprint": plan["plan_fingerprint"],
        "upstream_repository": SOURCE_REPOSITORY,
        "upstream_repository_in_historical_plan": plan["upstream"].get("repository"),
        "upstream_source_commit": plan["upstream"]["commit"],
        "validator_path": plan["native_validator"]["path"],
        "validator_sha256": plan["native_validator"]["sha256"],
        "adapter_sha256": adapter_sha,
        "read_only_recheck": read_only_recheck,
        "entries": checks,
        "verified_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if error_text:
        verification["error"] = error_text
    if read_only_recheck:
        # Preserve the historical run and verification receipts byte-for-byte.
        audit_key = _sha256_bytes(_canonical_bytes({
            "plan_fingerprint": plan["plan_fingerprint"],
            "state": verification["state"],
            "error": verification.get("error"),
            "entries": checks,
        }))[:10]
        receipt_name = f"verification-recheck-{adapter_sha[:12]}-{audit_key}.json"
        receipt_path = experiment_dir / receipt_name
        if not receipt_path.exists():
            verification["receipt_path"] = receipt_name
            verification["prior_run_sha256"] = _sha256_file(run_path)
            historical = experiment_dir / str(run.get("verification", "verification.json"))
            if historical.is_file():
                verification["prior_verification_sha256"] = _sha256_file(historical)
            _write_json_new(receipt_path, verification)
        else:
            existing = _read_json(receipt_path)
            semantic_keys = ("plan_fingerprint", "adapter_sha256", "state", "status",
                             "error", "entries", "palette_remapping",
                             "pixel_color_fidelity_verified", "art_reviewed",
                             "native_runtime_verified")
            if any(existing.get(key) != verification.get(key) for key in semantic_keys):
                raise ContractError("current read-only recheck receipt conflicts with prior data")
            verification = existing
    else:
        receipt_name = "verification.json"
        verification["receipt_path"] = receipt_name
        _write_json_new(experiment_dir / receipt_name, verification)
        run["state"] = verification["state"]
        run["verification"] = receipt_name
        _write_json_atomic(run_path, run)
    if error_text:
        raise ContractError(error_text)
    return verification


def _print_result(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="pin source files, vendor binary, HDL and validator")
    plan.add_argument("--job", type=Path, required=True)
    plan.add_argument("--tool-dir", type=Path, required=True,
                      help="H3DefTool folder containing h3deftool.exe")
    plan.add_argument("--native-validator-root", type=Path, required=True,
                      help="H3HD root containing tools/build_official_def_frame_map.py")
    for name in ("check", "run", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = {"status": "PLANNED", "plan": str(make_plan(
                args.job, args.tool_dir, args.native_validator_root))}
        elif args.command == "check":
            result = check_plan(args.plan)
        elif args.command == "run":
            result = run_plan(args.plan)
        else:
            result = verify_plan(args.plan)
        _print_result(result)
        return 0 if result.get("status") in {None, "PASS", "PLANNED", "STRUCTURE_PASS"} and result.get("state") not in {"failed", "timed_out", "rejected"} else 2
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError as exc:
        print(json.dumps({"status": "FAILED", "error": f"OSError: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
