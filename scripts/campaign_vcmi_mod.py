"""Build and publish the campaign 3X assets as a native VCMI Graphical mod.

This utility is kept in H3TierExport and has no imports from the H3 source tree.
It converts the frozen campaign image manifest into VCMI's Data3x and
Sprites3x folder layout; it does not install H3PNGA1 packs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

from PIL import Image

import workspace

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = re.compile(r"[A-Z0-9_-]{1,15}\Z")
PCX_KEY = re.compile(r"p/(?P<id>[a-z0-9_-]+)\.png\Z")
DEF_KEY = re.compile(r"d/(?P<id>[a-z0-9_-]+)\.def/(?P<group>0|[1-9][0-9]*)_(?P<frame>0|[1-9][0-9]*)\.png\Z")
BLOCKED_NAMES = {"vcmi_client.exe", "vcmi_server.exe", "vcmi_launcher.exe"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def assert_plain(path: Path, *, must_exist: bool = True) -> None:
    path = Path(path).absolute()
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("path traverses a filesystem link: " + str(node))
    if must_exist and not path.exists():
        raise FileNotFoundError(str(path))


def png_geometry(path: Path) -> tuple[int, int]:
    with Path(path).open("rb") as stream:
        head = stream.read(24)
    if (len(head) != 24 or head[:8] != b"\x89PNG\r\n\x1a\n"
            or head[8:12] != b"\0\0\0\r" or head[12:16] != b"IHDR"):
        raise ValueError("bad PNG header: " + str(path))
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def load_native_def_authority() -> tuple[dict, dict[tuple[str, int, int], dict], dict[str, list[dict]]]:
    inventory_path = (workspace.asset_root() / "codex-map" / "native-resource-inventory.json.gz").resolve(strict=True)
    assert_plain(inventory_path)
    raw = inventory_path.read_bytes()
    try:
        inventory = json.loads(gzip.decompress(raw).decode("utf-8-sig"))
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("official native inventory is unreadable: " + str(exc)) from exc
    if inventory.get("schema") != "h3.complete-art-catalog.v3" or not isinstance(inventory.get("rows"), list):
        raise ValueError("official native inventory schema is unsupported")
    by_slot: dict[tuple[str, int, int], dict] = {}
    by_identity: dict[str, list[dict]] = {}
    for native in inventory["rows"]:
        if native.get("kind") != "def":
            continue
        identity, group, frame = native.get("identity"), native.get("group"), native.get("frame")
        if not isinstance(identity, str) or type(group) is not int or type(frame) is not int:
            raise ValueError("official native DEF slot identity is invalid")
        key = (identity.upper(), group, frame)
        if key in by_slot:
            raise ValueError("official native DEF slot is duplicated: " + repr(key))
        by_slot[key] = native
        by_identity.setdefault(identity.upper(), []).append(native)
    inventory_ref = {"path": str(inventory_path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return inventory_ref, by_slot, by_identity


def validate_full_canvas_sprite(source: Path, native: dict, identity: str, group: int, frame: int) -> dict:
    canvas, crop, origin = native.get("canvas"), native.get("crop"), native.get("anchor")
    if (not isinstance(canvas, list) or len(canvas) != 2 or
            not isinstance(crop, list) or len(crop) != 2 or
            not isinstance(origin, list) or len(origin) != 2 or
            any(type(value) is not int for value in canvas + crop + origin) or
            any(value <= 0 for value in canvas + crop) or any(value < 0 for value in origin) or
            origin[0] + crop[0] > canvas[0] or origin[1] + crop[1] > canvas[1]):
        raise ValueError(f"official DEF geometry is invalid: {identity} {group}_{frame}")
    expected = (canvas[0] * 3, canvas[1] * 3)
    crop_rect = (origin[0] * 3, origin[1] * 3,
                 (origin[0] + crop[0]) * 3, (origin[1] + crop[1]) * 3)
    try:
        with Image.open(source) as image:
            image.load()
            if image.format != "PNG" or image.mode != "RGBA":
                raise ValueError(f"map sprite must be RGBA PNG: {identity} {group}_{frame}")
            if image.size != expected:
                raise ValueError(
                    f"MAP_SPRITE_FULL_CANVAS_3X_REQUIRED: {identity} {group}_{frame} "
                    f"expected {expected} from canvas {canvas}, got {image.size}; "
                    "crop-sized output loses the original canvas and anchor"
                )
            alpha_box = image.getchannel("A").getbbox()
            if alpha_box is None:
                raise ValueError(f"map sprite is fully transparent: {identity} {group}_{frame}")
            if (alpha_box[0] < crop_rect[0] or alpha_box[1] < crop_rect[1] or
                    alpha_box[2] > crop_rect[2] or alpha_box[3] > crop_rect[3]):
                raise ValueError(
                    f"MAP_SPRITE_ALPHA_OUTSIDE_OFFICIAL_CROP: {identity} {group}_{frame} "
                    f"alpha {alpha_box}, official crop at origin {origin} is {crop_rect}"
                )
    except OSError as exc:
        raise ValueError(f"map sprite PNG is unreadable: {identity} {group}_{frame}: {exc}") from exc
    return {
        "identity": identity, "group": group, "frame": frame,
        "native_lod": native.get("native_lod"), "resource": native.get("resource"),
        "canvas_1x": canvas, "crop_1x": crop, "origin_1x": origin,
        "expected_full_canvas_3x": list(expected), "official_crop_rect_3x": list(crop_rect),
        "source_alpha_bbox_3x": list(alpha_box),
    }


def is_mounted_hero_identity(native_rows: list[dict]) -> bool:
    counts: dict[int, int] = {}
    for row in native_rows:
        group = row.get("group")
        if type(group) is not int:
            return False
        counts[group] = counts.get(group, 0) + 1
    return counts == {**{group: 1 for group in range(5)}, **{group: 8 for group in range(5, 10)}}


def validate_mounted_hero_coverage(sprite_slots: dict[str, set[tuple[int, int]]],
                                   native_by_identity: dict[str, list[dict]]) -> list[dict]:
    batches = []
    for identity, actual_slots in sprite_slots.items():
        native_rows = native_by_identity.get(identity, [])
        if not is_mounted_hero_identity(native_rows):
            continue
        expected_slots = {(row["group"], row["frame"]) for row in native_rows}
        if actual_slots != expected_slots:
            missing = sorted(expected_slots - actual_slots)
            extra = sorted(actual_slots - expected_slots)
            raise ValueError(
                f"MOUNTED_HERO_PACKAGE_MUST_CONTAIN_ALL_45_OFFICIAL_SLOTS: {identity}; "
                f"missing={missing}, extra={extra}"
            )
        batches.append({"identity": identity, "official_slots": len(expected_slots)})
    return batches


def load_config(path: Path) -> tuple[dict, dict, list[dict]]:
    path = Path(path)
    assert_plain(path)
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    manifest_path = Path(config["manifest_path"]).resolve()
    assert_plain(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if (manifest_sha != config.get("expected_manifest_sha256")
            or config.get("expected_key_count") != len(json.loads(manifest_bytes)["items"])):
        raise ValueError("campaign manifest identity/count changed")
    manifest = json.loads(manifest_bytes)
    if manifest.get("status") != "READY" or manifest.get("tier") != 3:
        raise ValueError("campaign source manifest is not a ready 3X manifest")
    native_inventory_ref, native_by_slot, native_by_identity = load_native_def_authority()
    allowed = [Path(x).resolve() for x in config["allowed_source_roots"]]
    if not allowed:
        raise ValueError("source root allowlist is empty")
    rows = []
    seen_keys = set()
    seen_sprite_slots = set()
    for item in manifest["items"]:
        key = item["key"]
        pcx = PCX_KEY.fullmatch(key)
        sprite = DEF_KEY.fullmatch(key)
        if not pcx and not sprite:
            raise ValueError("unsupported VCMI image key: " + str(key))
        if key.casefold() in seen_keys:
            raise ValueError("duplicate asset key: " + key)
        seen_keys.add(key.casefold())
        source = Path(item["source"]).resolve()
        if not any(source.is_relative_to(root) for root in allowed):
            raise ValueError("source outside codex-3X: " + str(source))
        assert_plain(source)
        expected = item.get("sha256")
        if not isinstance(expected, str) or not SHA256.fullmatch(expected) or sha256(source) != expected:
            raise ValueError("source SHA drift: " + str(source))
        width, height = png_geometry(source)
        native = item.get("native_pixels")
        row = dict(item)
        row["source_resolved"] = source
        row["width"] = width
        row["height"] = height
        row["pcx_match"] = pcx
        row["def_match"] = sprite
        if sprite:
            identity = str(row.get("identity") or sprite.group("id")).upper()
            group = int(sprite.group("group"))
            frame = int(sprite.group("frame"))
            native_key = (identity, group, frame)
            if native_key in seen_sprite_slots:
                raise ValueError(f"duplicate native sprite slot in campaign manifest: {identity} {group}_{frame}")
            seen_sprite_slots.add(native_key)
            authority = native_by_slot.get((identity, group, frame))
            if authority is None:
                raise ValueError(f"sprite slot is absent from official native inventory: {identity} {group}_{frame}")
            geometry = validate_full_canvas_sprite(source, authority, identity, group, frame)
            if native != geometry["canvas_1x"]:
                raise ValueError(
                    f"SPRITE_NATIVE_PIXELS_MUST_MATCH_OFFICIAL_CANVAS: {identity} {group}_{frame}; "
                    f"expected {geometry['canvas_1x']}, got {native}"
                )
            if ((width, height) != tuple(geometry["expected_full_canvas_3x"]) or
                    (width, height) != (item.get("width"), item.get("height"))):
                raise ValueError("sprite manifest dimensions differ from official full canvas 3X: " + key)
            row["identity"] = identity
            row["native_geometry"] = geometry
        elif (not isinstance(native, list) or len(native) != 2
              or (width, height) != (native[0] * 3, native[1] * 3)
              or (width, height) != (item.get("width"), item.get("height"))):
            raise ValueError("source is not exact native 3X: " + key)
        rows.append(row)
    if len(rows) != config["expected_key_count"]:
        raise ValueError("campaign key count changed during validation")
    sprite_slots: dict[str, set[tuple[int, int]]] = {}
    for row in rows:
        if row["def_match"]:
            identity = row["identity"]
            sprite_slots.setdefault(identity, set()).add((int(row["def_match"].group("group")),
                                                         int(row["def_match"].group("frame"))))
    hero_batches = validate_mounted_hero_coverage(sprite_slots, native_by_identity)
    config["_native_inventory_ref"] = native_inventory_ref
    config["_mounted_hero_batches"] = hero_batches
    return config, manifest, rows


def add_new_file(root: Path, relative: str, data: bytes, entries: list[dict]) -> None:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("unsafe VCMI mod relative path")
    out = root.joinpath(*rel.parts)
    if out.exists():
        raise FileExistsError("duplicate VCMI mod output: " + str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    entries.append({"path": rel.as_posix(), "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kind": "json" if out.suffix.lower() == ".json" else "png"})


def build_mod(config_path: Path) -> dict:
    config, manifest, rows = load_config(config_path)
    output_root = Path(config["candidate_root"]).absolute()
    if output_root.exists():
        raise FileExistsError("VCMI mod candidate root exists: " + str(output_root))
    mod_id = config["mod_id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", mod_id):
        raise ValueError("invalid VCMI mod id")
    output_root.mkdir(parents=True, exist_ok=False)
    mod_root = output_root / mod_id
    mod_root.mkdir()
    entries = []
    soft_depends = config.get("soft_depends", [])
    if (not isinstance(soft_depends, list)
            or any(not isinstance(x, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", x) for x in soft_depends)
            or len(set(soft_depends)) != len(soft_depends)):
        raise ValueError("invalid VCMI soft dependency list")
    mod_name = config.get("mod_name", "H3HD Campaign Art 3X")
    mod_description = config.get(
        "mod_description",
        "3X campaign territory tiles, story and transition images, campaign controls, combination rewards, and campaign frame art from the H3HD campaign masters. Requires HD textures and 3x scaling.",
    )
    if (not isinstance(mod_name, str) or not mod_name.strip()
            or not isinstance(mod_description, str) or not mod_description.strip()):
        raise ValueError("VCMI mod_name and mod_description must be non-empty strings")
    mod_json = {
        "name": mod_name,
        "description": mod_description,
        "version": "0.1",
        "author": "H3HD",
        "contact": "",
        "modType": "Graphical",
    }
    if soft_depends:
        mod_json["softDepends"] = soft_depends
    add_new_file(mod_root, "mod.json", (json.dumps(mod_json, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), entries)

    data_names = set()
    sprite_definitions: dict[str, list[dict]] = {}
    sprite_sources: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["pcx_match"]:
            identity = str(row.get("identity") or row["pcx_match"].group("id")).upper()
            if not IDENTITY.fullmatch(identity):
                raise ValueError("invalid Data3x identity: " + identity)
            target_name = identity + ".png"
            if target_name.casefold() in data_names:
                raise ValueError("duplicate Data3x filename: " + target_name)
            data_names.add(target_name.casefold())
            raw = Path(row["source_resolved"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("source changed while assembling: " + row["key"])
            add_new_file(mod_root, "content/Data3x/" + target_name, raw, entries)
            continue

        identity = str(row.get("identity") or row["def_match"].group("id")).upper()
        if not IDENTITY.fullmatch(identity):
            raise ValueError("invalid Sprites3x identity: " + identity)
        group = int(row["def_match"].group("group"))
        frame = int(row["def_match"].group("frame"))
        source = Path(row["source_resolved"])
        source_key = (identity, str(source))
        filename = sprite_sources.get(source_key)
        if filename is None:
            filename = f"{source.stem}_g{group}_f{frame}.png"
            sprite_sources[source_key] = filename
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("sprite source changed while assembling: " + row["key"])
            add_new_file(mod_root, f"content/Sprites3x/{identity}/{filename}", raw, entries)
        sprite_definitions.setdefault(identity, []).append({"group": group, "frame": frame, "file": filename})

    descriptor_paths = []
    for identity, images in sorted(sprite_definitions.items()):
        images.sort(key=lambda row: (row["group"], row["frame"]))
        if len({(x["group"], x["frame"]) for x in images}) != len(images):
            raise ValueError("duplicate VCMI sprite slot: " + identity)
        descriptor = {"basepath": identity + "/", "images": images}
        path = f"content/Sprites3x/{identity}.json"
        add_new_file(mod_root, path, (json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), entries)
        descriptor_paths.append(path)

    files = [mod_root / Path(*PurePosixPath(x["path"]).parts) for x in entries]
    if any(not p.is_file() for p in files):
        raise RuntimeError("staged VCMI mod file missing")
    for entry, path in zip(entries, files):
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise RuntimeError("staged VCMI mod readback failed: " + str(path))
    receipt = {
        "schema": "h3.campaign-vcmi-mod-build-receipt.v2",
        "operation_id": config["operation_id"],
        "status": "BUILT_AND_READBACK_VERIFIED",
        "mod_id": mod_id,
        "mod_root": str(mod_root),
        "manifest_sha256": config["expected_manifest_sha256"],
        "native_inventory": config["_native_inventory_ref"],
        "native_geometry_contract": "official-full-canvas-3x-alpha-within-crop",
        "mounted_hero_batches": config["_mounted_hero_batches"],
        "sprite_geometry": [row["native_geometry"] for row in rows if row["def_match"]],
        "asset_keys": len(rows),
        "data3x_pngs": len(data_names),
        "sprite3x_unique_pngs": len(sprite_sources),
        "sprite3x_descriptors": len(descriptor_paths),
        "soft_depends": soft_depends,
        "files": len(entries),
        "png_bytes": sum(x["bytes"] for x in entries if x["kind"] == "png"),
        "total_bytes": sum(x["bytes"] for x in entries),
        "file_manifest": entries,
        "runtime_accepted": False,
    }
    receipt_path = output_root / "vcmi-mod-build-receipt.json"
    with receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def query_vcmi_processes(vcmi_root: Path) -> list[str]:
    query = ("$names=@('VCMI_client.exe','VCMI_server.exe','VCMI_launcher.exe'); "
             "Get-CimInstance Win32_Process -ErrorAction Stop | "
             "Where-Object { $names -contains $_.Name } | "
             "Select-Object Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError("cannot establish VCMI idle state; install refused")
    raw = result.stdout.strip()
    if not raw:
        return []
    procs = json.loads(raw)
    if isinstance(procs, dict):
        procs = [procs]
    root = vcmi_root.resolve()
    active = []
    for proc in procs:
        exe = proc.get("ExecutablePath") or ""
        cmd = str(proc.get("CommandLine") or "").casefold()
        if not exe:
            active.append(str(proc.get("Name") or "VCMI process") + " (path unavailable)")
        elif Path(exe).resolve().is_relative_to(root) or str(root).casefold() in cmd:
            active.append(str(proc.get("Name") or exe))
    return sorted(set(active))


def install_mod(config_path: Path) -> dict:
    config_path = Path(config_path)
    config, _, _ = load_config(config_path)
    receipt_path = Path(config["candidate_root"]) / "vcmi-mod-build-receipt.json"
    assert_plain(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if (receipt.get("schema") != "h3.campaign-vcmi-mod-build-receipt.v2"
            or receipt.get("status") != "BUILT_AND_READBACK_VERIFIED"
            or receipt.get("manifest_sha256") != config.get("expected_manifest_sha256")
            or receipt.get("native_inventory") != config.get("_native_inventory_ref")
            or receipt.get("native_geometry_contract") != "official-full-canvas-3x-alpha-within-crop"):
        raise ValueError("VCMI mod candidate receipt mismatch")
    source = Path(receipt["mod_root"]).resolve()
    vcmi_root = Path(config["vcmi_root"]).resolve()
    mods_root = vcmi_root / "Mods"
    target = mods_root / receipt["mod_id"]
    if target.exists():
        raise FileExistsError("VCMI mod target already exists; refusing overwrite: " + str(target))
    receipt_out = Path(config["install_receipt_path"]).absolute()
    if receipt_out.exists():
        raise FileExistsError("VCMI install receipt already exists; preserve and inspect: " + str(receipt_out))
    running = query_vcmi_processes(vcmi_root)
    allow_hot_install = config.get("allow_hot_install", False)
    hot_install_authorization = config.get("hot_install_authorization")
    if type(allow_hot_install) is not bool:
        raise ValueError("allow_hot_install must be a boolean")
    if allow_hot_install and (not isinstance(hot_install_authorization, str)
                              or not hot_install_authorization.strip()):
        raise ValueError("hot_install_authorization is required when allow_hot_install is true")
    if running and not allow_hot_install:
        raise RuntimeError("VCMI_INSTALL_BLOCKED_GAME_RUNNING: " + ", ".join(running))
    if not source.is_dir() or not source.is_relative_to(Path(config["candidate_root"]).resolve()):
        raise ValueError("candidate mod root is missing or outside the isolated tool operation")
    if source.drive.casefold() != target.drive.casefold():
        raise ValueError("atomic VCMI mod install requires candidate and Mods on one volume")
    # Verify every staged source before the single directory rename.
    for entry in receipt["file_manifest"]:
        p = source / Path(*PurePosixPath(entry["path"]).parts)
        if not p.is_file() or p.stat().st_size != entry["bytes"] or sha256(p) != entry["sha256"]:
            raise RuntimeError("VCMI candidate SHA mismatch before install: " + str(p))
    if target.exists():
        raise FileExistsError("VCMI mod target appeared during install")
    os.rename(source, target)
    # Read back the installed tree after the atomic move.
    for entry in receipt["file_manifest"]:
        p = target / Path(*PurePosixPath(entry["path"]).parts)
        if not p.is_file() or p.stat().st_size != entry["bytes"] or sha256(p) != entry["sha256"]:
            raise RuntimeError("VCMI installed mod SHA mismatch: " + str(p))
    result = {
        "schema": "h3.campaign-vcmi-mod-install-receipt.v1",
        "operation_id": config["operation_id"],
        "status": "VCMI_MOD_INSTALLED_SHA_READBACK_PASS",
        "mod_id": receipt["mod_id"],
        "mod_root": str(target),
        "files": receipt["files"],
        "asset_keys": receipt["asset_keys"],
        "data3x_pngs": receipt["data3x_pngs"],
        "sprite3x_unique_pngs": receipt["sprite3x_unique_pngs"],
        "sprite3x_descriptors": receipt["sprite3x_descriptors"],
        "total_bytes": receipt["total_bytes"],
        "native_inventory_sha256": receipt["native_inventory"]["sha256"],
        "native_geometry_contract": receipt["native_geometry_contract"],
        "mounted_hero_batches": receipt["mounted_hero_batches"],
        "hot_install_requested": allow_hot_install,
        "installed_while_vcmi_running": bool(running),
        "vcmi_processes_observed": running,
        "hot_install_authorization": hot_install_authorization if running else None,
        "current_session_reload_verified": False,
        "runtime_accepted": False,
    }
    with receipt_out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return result


def directory_inventory(root: Path) -> list[dict]:
    root = Path(root).resolve()
    assert_plain(root)
    rows = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix().casefold()):
        if path.is_dir():
            assert_plain(path)
            continue
        if not path.is_file():
            raise ValueError("unexpected non-file in VCMI mod tree: " + str(path))
        assert_plain(path)
        rows.append({"path": path.relative_to(root).as_posix(),
                     "bytes": path.stat().st_size, "sha256": sha256(path)})
    return rows


def inventory_digest(rows: list[dict]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def update_mod(config_path: Path) -> dict:
    """Replace an installed campaign mod transactionally after verifying its prior tree."""
    config_path = Path(config_path)
    assert_plain(config_path)
    config, _, _ = load_config(config_path)
    allow_hot_update = config.get("allow_hot_update", False)
    hot_update_authorization = config.get("hot_update_authorization")
    if type(allow_hot_update) is not bool:
        raise ValueError("allow_hot_update must be a boolean")
    if allow_hot_update and (not isinstance(hot_update_authorization, str)
                             or not hot_update_authorization.strip()):
        raise ValueError("hot_update_authorization is required when allow_hot_update is true")
    receipt_path = Path(config["candidate_root"]) / "vcmi-mod-build-receipt.json"
    assert_plain(receipt_path)
    build_receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if (build_receipt.get("schema") != "h3.campaign-vcmi-mod-build-receipt.v2"
            or build_receipt.get("status") != "BUILT_AND_READBACK_VERIFIED"
            or build_receipt.get("manifest_sha256") != config.get("expected_manifest_sha256")
            or build_receipt.get("native_inventory") != config.get("_native_inventory_ref")
            or build_receipt.get("native_geometry_contract") != "official-full-canvas-3x-alpha-within-crop"):
        raise ValueError("VCMI update candidate receipt mismatch")

    vcmi_root = Path(config["vcmi_root"]).resolve()
    mods_root = vcmi_root / "Mods"
    mod_id = build_receipt["mod_id"]
    target = mods_root / mod_id
    source = Path(build_receipt["mod_root"]).resolve()
    backup = Path(config["previous_mod_backup_root"]).absolute()
    expected_inventory_path = Path(config["previous_mod_inventory_path"])
    assert_plain(expected_inventory_path)
    expected_inventory_bytes = expected_inventory_path.read_bytes()
    expected_inventory_sha = hashlib.sha256(expected_inventory_bytes).hexdigest()
    if expected_inventory_sha != config.get("previous_mod_inventory_sha256"):
        raise ValueError("previous VCMI mod inventory receipt SHA mismatch")
    expected_inventory_doc = json.loads(expected_inventory_bytes)
    expected_rows = expected_inventory_doc.get("files")
    if (expected_inventory_doc.get("mod_id") != mod_id
            or expected_inventory_doc.get("schema") != "h3.campaign-vcmi-installed-mod-inventory.v1"
            or not isinstance(expected_rows, list)
            or inventory_digest(expected_rows) != expected_inventory_doc.get("inventory_sha256")):
        raise ValueError("previous VCMI mod inventory contract mismatch")

    if not target.is_dir():
        raise FileNotFoundError("installed campaign mod is missing: " + str(target))
    assert_plain(target)
    old_mod_json = json.loads((target / "mod.json").read_text(encoding="utf-8-sig"))
    expected_mod_name = config.get("mod_name", "H3HD Campaign Art 3X")
    if old_mod_json.get("name") != expected_mod_name or old_mod_json.get("modType") != "Graphical":
        raise ValueError("installed mod identity does not match campaign update target")
    actual_old_rows = directory_inventory(target)
    if actual_old_rows != expected_rows:
        raise ValueError("installed campaign mod changed since its pinned inventory")
    if inventory_digest(actual_old_rows) != expected_inventory_doc["inventory_sha256"]:
        raise ValueError("installed campaign mod inventory digest mismatch")

    assert_plain(source)
    candidate_root = Path(config["candidate_root"]).resolve()
    if not source.is_relative_to(candidate_root):
        raise ValueError("VCMI update candidate is outside isolated operation root")
    if source.drive.casefold() != target.drive.casefold() or backup.drive.casefold() != target.drive.casefold():
        raise ValueError("VCMI mod update requires same-volume candidate, backup, and Mods")
    if backup.is_relative_to(mods_root):
        raise ValueError("previous mod backup cannot remain inside VCMI Mods")
    if backup.exists():
        raise FileExistsError("previous mod backup path already exists: " + str(backup))

    running_preflight = query_vcmi_processes(vcmi_root)
    concurrent_writers = [name for name in running_preflight if name != "VCMI_client.exe"]
    if concurrent_writers:
        raise RuntimeError("VCMI_UPDATE_BLOCKED_CONCURRENT_WRITER: " + ", ".join(concurrent_writers))
    if running_preflight and not allow_hot_update:
        raise RuntimeError("VCMI_UPDATE_BLOCKED_GAME_RUNNING: " + ", ".join(running_preflight))

    expected_new_rows = sorted(
        ({"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
         for item in build_receipt["file_manifest"]),
        key=lambda value: value["path"].casefold(),
    )
    actual_candidate_rows = directory_inventory(source)
    if actual_candidate_rows != expected_new_rows:
        raise ValueError("VCMI update candidate changed after build receipt")
    update_receipt_path = Path(config["update_receipt_path"]).absolute()
    journal_path = Path(config["update_journal_path"]).absolute()
    if update_receipt_path.exists() or journal_path.exists():
        raise FileExistsError("VCMI update receipt or journal already exists")

    backup.parent.mkdir(parents=True, exist_ok=True)
    journal = {
        "schema": "h3.campaign-vcmi-mod-update-journal.v1",
        "operation_id": config["operation_id"],
        "status": "PREPARED",
        "target": str(target),
        "candidate": str(source),
        "previous_backup": str(backup),
        "previous_inventory_path": str(expected_inventory_path),
        "previous_inventory_sha256": expected_inventory_sha,
        "candidate_manifest_sha256": build_receipt["manifest_sha256"],
        "hot_update_requested": allow_hot_update,
        "hot_update_authorization": hot_update_authorization if allow_hot_update else None,
        "vcmi_processes_observed_at_preflight": running_preflight,
        "vcmi_processes_observed_before_commit": [],
        "updated_while_vcmi_running": False,
        "current_session_reload_verified": False,
    }
    with journal_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(journal, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    old_moved = False
    new_moved = False
    try:
        # Recheck process ownership and both trees immediately before the two
        # same-volume directory renames. VCMI_client may stay active only when
        # the user explicitly enabled hot update in the frozen config.
        running_before_commit = query_vcmi_processes(vcmi_root)
        concurrent_writers = [name for name in running_before_commit if name != "VCMI_client.exe"]
        if concurrent_writers:
            raise RuntimeError("VCMI_UPDATE_BLOCKED_CONCURRENT_WRITER: " + ", ".join(concurrent_writers))
        if running_before_commit and not allow_hot_update:
            raise RuntimeError("VCMI_UPDATE_BLOCKED_GAME_RUNNING: " + ", ".join(running_before_commit))
        if directory_inventory(target) != expected_rows:
            raise ValueError("installed campaign mod changed immediately before update")
        if directory_inventory(source) != expected_new_rows:
            raise ValueError("VCMI update candidate changed immediately before update")
        journal["vcmi_processes_observed_before_commit"] = running_before_commit
        journal["updated_while_vcmi_running"] = bool(running_before_commit)
        journal["commit_mode"] = "HOT_ATOMIC_DIRECTORY_REPLACE" if running_before_commit else "IDLE_ATOMIC_DIRECTORY_REPLACE"
        journal_temp = journal_path.with_name(journal_path.name + ".precommit")
        with journal_temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(journal, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(journal_temp, journal_path)
        os.rename(target, backup)
        old_moved = True
        os.rename(source, target)
        new_moved = True

        actual_new_rows = directory_inventory(target)
        if actual_new_rows != expected_new_rows:
            raise RuntimeError("installed VCMI campaign update readback mismatch")
        archived_rows = directory_inventory(backup)
        if archived_rows != expected_rows:
            raise RuntimeError("previous VCMI campaign mod backup readback mismatch")

        result = {
            "schema": "h3.campaign-vcmi-mod-update-receipt.v1",
            "operation_id": config["operation_id"],
            "status": "VCMI_MOD_UPDATED_SHA_READBACK_PASS",
            "mod_id": mod_id,
            "mod_root": str(target),
            "previous_mod_backup": str(backup),
            "previous_mod_inventory_sha256": expected_inventory_sha,
            "previous_files": len(archived_rows),
            "previous_bytes": sum(row["bytes"] for row in archived_rows),
            "files": len(actual_new_rows),
            "asset_keys": build_receipt["asset_keys"],
            "data3x_pngs": build_receipt["data3x_pngs"],
            "sprite3x_unique_pngs": build_receipt["sprite3x_unique_pngs"],
            "sprite3x_descriptors": build_receipt["sprite3x_descriptors"],
            "total_bytes": sum(row["bytes"] for row in actual_new_rows),
            "soft_depends": build_receipt.get("soft_depends", []),
            "manifest_sha256": build_receipt["manifest_sha256"],
            "native_inventory_sha256": build_receipt["native_inventory"]["sha256"],
            "native_geometry_contract": build_receipt["native_geometry_contract"],
            "mounted_hero_batches": build_receipt["mounted_hero_batches"],
            "hot_update_requested": allow_hot_update,
            "updated_while_vcmi_running": bool(running_before_commit),
            "vcmi_processes_observed_at_preflight": running_preflight,
            "vcmi_processes_observed_before_commit": running_before_commit,
            "hot_update_authorization": hot_update_authorization if running_before_commit else None,
            "current_session_reload_verified": False,
            "runtime_accepted": False,
        }
        with update_receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        journal["status"] = result["status"]
        journal["update_receipt"] = str(update_receipt_path)
        journal["updated_mod_inventory_sha256"] = inventory_digest(actual_new_rows)
        journal_temp = journal_path.with_name(journal_path.name + ".new")
        with journal_temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(journal, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(journal_temp, journal_path)
        return result
    except Exception as update_error:
        # Preserve both trees and leave a terminal journal state on failure.
        rollback_error = None
        try:
            if new_moved and target.exists() and not source.exists():
                os.rename(target, source)
            if old_moved and backup.exists() and not target.exists():
                os.rename(backup, target)
        except OSError as exc:
            rollback_error = exc
        journal["error"] = str(update_error)[:500]
        if rollback_error is None:
            journal["status"] = "UPDATE_FAILED_ROLLED_BACK"
            journal["rollback_complete"] = True
        else:
            journal["status"] = "ROLLBACK_INCOMPLETE_PRESERVE_ALL"
            journal["rollback_error"] = str(rollback_error)[:500]
        journal_temp = journal_path.with_name(journal_path.name + (".failed" if rollback_error is None else ".rollback"))
        with journal_temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(journal, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(journal_temp, journal_path)
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("build", "install", "update"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()
    try:
        if args.command == "build":
            result = build_mod(args.config)
        elif args.command == "install":
            result = install_mod(args.config)
        else:
            result = update_mod(args.config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, FileExistsError) as error:
        print("CAMPAIGN_VCMI_MOD_REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
