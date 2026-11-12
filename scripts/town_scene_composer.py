"""Compose explicitly authorized town diagnostic recipes from existing exact 3X canvases.

No resampling, source selection, native game-state inference or game writes occur here.
The public batch pins the owner inputs and original inventory; check never writes.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import sys

from PIL import Image
import review_native_batch as native

PRODUCT = resolve_tool_root(__file__)
OUTPUT_BASE = PRODUCT / "build/experiments/town-scene"
SCHEMA = "h3.town-scene.batch.v1"
OWNER_SCHEMA = "h3.art-town.current-modern-3x-scene-inputs.v1"
MAX_PIXELS = 64 * 1024 * 1024
AUTHORITY = {"art_approved": False, "human_approved": False, "scene_accepted": False,
             "native_timing_verified": False, "runtime_accepted": False, "game_deployed": False}


def require(condition, code):
    if not condition:
        raise native.ReviewError(code)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def pair(value, label, *, coordinate=False):
    require(isinstance(value, list) and len(value) == 2, label + "_PAIR_REQUIRED")
    return [native.integer(x, label, -65535 if coordinate else 1, 65535) for x in value]


class Inputs(native.BoundInputSet):
    """Pin only current consumed files and declared fact documents, never retired image descendants."""

    def __init__(self):
        super().__init__()
        self.decoded_pixels = 0

    def png(self, ref, label, size=None, rgba_sha=None, rgba_only=False):
        raw = self.read(ref, label, 512 * 1024 * 1024)
        pixels = native.png_size_bytes(raw, label)
        require(pixels[0] * pixels[1] <= 16 * 1024 * 1024, "SCENE_IMAGE_PIXEL_LIMIT")
        self.decoded_pixels += pixels[0] * pixels[1]
        require(self.decoded_pixels <= MAX_PIXELS, "SCENE_TOTAL_PIXEL_LIMIT")
        if size is not None:
            require(pixels == size, label + "_PIXELS_DRIFT")
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == "PNG" and image.mode in (("RGBA",) if rgba_only else ("RGB", "RGBA")),
                    label + "_PNG_MODE_INVALID")
            result = image.convert("RGBA")
        if rgba_sha is not None:
            require(hashlib.sha256(result.tobytes()).hexdigest() == rgba_sha, label + "_RGBA_SHA256_DRIFT")
        return result

def prepare(manifest_path, expected_sha256, operation_id, tool_binding):
    refs = Inputs()
    manifest_ref = {"path": str(manifest_path), "sha256": expected_sha256}
    manifest = refs.document(manifest_ref, "SCENE_MANIFEST")
    require(set(manifest) == {"schema", "operation_id", "scene_inputs", "official_inventory"},
            "SCENE_MANIFEST_FIELDS_INVALID")
    require(manifest["schema"] == SCHEMA, "SCENE_MANIFEST_SCHEMA_INVALID")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", operation_id or "") is not None,
            "SCENE_OPERATION_ID_INVALID")
    require(manifest["operation_id"] == operation_id, "SCENE_OPERATION_ID_MISMATCH")
    catalog, _ = native.load_inventory(Path(manifest["official_inventory"]["path"]),
                                                   manifest["official_inventory"]["sha256"])
    refs.read(manifest["official_inventory"], "SCENE_INVENTORY", 512 * 1024 * 1024)
    owner = refs.document(manifest["scene_inputs"], "SCENE_OWNER_INPUTS")
    require(owner.get("schema") == OWNER_SCHEMA, "SCENE_OWNER_SCHEMA_INVALID")
    authority = owner["authority"]
    require(authority.get("client_external_tiers") == [3] and authority.get("new_export_tiers") == [3]
            and authority.get("save_new_1x_files") is False, "SCENE_REQUIRES_3X_ONLY_AUTHORITY")
    require(all(authority.get(k) is False for k in ("asset_index_written", "source_art_selected",
                "human_approved", "game_deployed")), "SCENE_AUTHORITY_ESCALATION_REJECTED")
    facts = refs.document(owner["frozen_scene_facts"], "SCENE_FROZEN_FACTS")
    require(facts.get("schema") == "h3.art-town.frozen-scene-input-facts.v1", "SCENE_FACTS_SCHEMA_INVALID")
    refs.document(owner["current_art_standard_reference"], "SCENE_ART_STANDARD")
    refs.document(owner["historical_diagnostic_authority"], "SCENE_HISTORICAL_AUTHORITY")
    explicit = owner["explicit_diagnostic_state_authority"]
    require(explicit.get("approved_for_art_diagnostic_by_root") is True, "SCENE_DIAGNOSTIC_AUTHORITY_REQUIRED")
    decision = refs.document(explicit["state_manifest"], "SCENE_STATE_AUTHORITY")
    require(decision.get("schema") == "h3.art-town.explicit-diagnostic-layer-set-decision.v1"
            and decision.get("operation_id") == explicit.get("state_version"), "SCENE_STATE_AUTHORITY_INVALID")
    require(decision.get("input_facts", {}).get("sha256") == owner["frozen_scene_facts"]["sha256"],
            "SCENE_STATE_FACTS_BINDING_MISMATCH")
    require(decision.get("game_deployed") is False and decision.get("new_art_or_personal_approval") is False,
            "SCENE_STATE_AUTHORITY_ESCALATION_REJECTED")
    native_size = pair(owner["native_coordinate_reference_only"], "SCENE_NATIVE_SIZE")
    require(facts.get("native_scene_canvas") == native_size, "SCENE_FACTS_VIEWPORT_DRIFT")
    viewport = pair(owner["viewport_3x"], "SCENE_VIEWPORT")
    require(viewport == [n * 3 for n in native_size] and viewport[0] * viewport[1] <= 16 * 1024 * 1024,
            "SCENE_VIEWPORT_MUST_BE_EXACT3X")

    def occurrences(rows, label):
        require(isinstance(rows, list) and 0 < len(rows) <= 512, label + "_COUNT_INVALID")
        by_id = {}
        for row in rows:
            require(isinstance(row, dict) and isinstance(row.get("occurrence_id"), str), label + "_ROW_INVALID")
            require(row["occurrence_id"] not in by_id, label + "_DUPLICATE_OCCURRENCE")
            by_id[row["occurrence_id"]] = row
        return by_id

    frozen = occurrences(facts["occurrences"], "SCENE_FACTS")
    current = occurrences(owner["occurrences"], "SCENE_CURRENT")
    require(set(current) == set(frozen), "SCENE_OCCURRENCE_SET_DRIFT")
    state_sets = {}
    for states, label in ((facts["states"], "facts"), (owner["states"], "owner"), (decision["states"], "authority")):
        require(isinstance(states, list) and 0 < len(states) <= 16, "SCENE_STATE_COUNT_INVALID")
        by_name = {x["name"]: x for x in states}
        require(len(by_name) == len(states), "SCENE_DUPLICATE_STATE")
        require(all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", n) for n in by_name), "SCENE_STATE_NAME_INVALID")
        state_sets[label] = by_name
    require(set(state_sets["facts"]) == set(state_sets["owner"]) == set(state_sets["authority"]),
            "SCENE_STATE_SET_DRIFT")
    assets = owner["assets"]
    require(isinstance(assets, dict) and len(assets) <= 512, "SCENE_ASSET_COUNT_INVALID")
    images, asset_proofs, blockers, layers, used = {}, {}, [], [], set()
    for oid, row in current.items():
        old = frozen[oid]
        key = native.key_tuple(row["key"])
        require(key[0] == "def" and key in catalog, "SCENE_UNKNOWN_NATIVE_KEY")
        for field in ("key", "state", "original_building_slot", "original_draw_order_index", "native_geometry"):
            require(row.get(field) == old.get(field), "SCENE_OCCURRENCE_FACT_DRIFT: " + field)
        require(row["state"] in state_sets["owner"], "SCENE_UNKNOWN_OCCURRENCE_STATE")
        slot = native.integer(row["original_building_slot"], "building slot", 0, 65535)
        order = native.integer(row["original_draw_order_index"], "draw order", 0, 65535)
        geometry = native.official_targets(catalog[key], key)
        require(row["native_geometry"] == {"canvas": geometry["canvas_1x"], "crop": geometry["crop_1x"],
                "origin": geometry["origin_1x"]}, "SCENE_NATIVE_GEOMETRY_DRIFT")
        xy = pair(row["native_global_xy"], "SCENE_XY", coordinate=True)
        require(xy == old["original_native_global_xy"], "SCENE_NATIVE_XY_DRIFT")
        require(row.get("placement_xy_3x") == [v * 3 for v in xy]
                and row.get("canvas_3x") == [v * 3 for v in geometry["canvas_1x"]]
                and row.get("crop_3x") == [v * 3 for v in geometry["crop_1x"]]
                and row.get("origin_3x") == [v * 3 for v in geometry["origin_1x"]]
                and row.get("add_crop_origin_again") is False, "SCENE_DOUBLE_ORIGIN_OR_3X_GEOMETRY_DRIFT")
        require(row.get("replacement_identity") == ["exact_key", "building_slot", "state"],
                "SCENE_EXACT_REPLACEMENT_IDENTITY_REQUIRED")
        table = old.get("native_table_binding")
        if table:
            refs.document(table["source"], "SCENE_NATIVE_TABLE_FACTS")
        asset_id = row.get("asset_id")
        if asset_id is None:
            require(row.get("status") == "NEEDS_HIGHER_MASTER", "SCENE_MISSING_ASSET_STATUS_INVALID")
            blockers.append({"code": "NEEDS_HIGHER_MASTER", "occurrence_id": oid, "key": list(key)})
        else:
            require(asset_id == ":".join(map(str, key)) and asset_id in assets, "SCENE_ASSET_KEY_MISMATCH")
            used.add(asset_id)
            asset = assets[asset_id]
            require(asset.get("key") == list(key) and asset.get("native_geometry") == row["native_geometry"],
                    "SCENE_ASSET_GEOMETRY_DRIFT")
            require(asset.get("source_layout") == "FULL_NATIVE_CANVAS_3X"
                    and asset.get("origin_already_baked_into_canvas") is True
                    and asset.get("composition_sampling_steps") == 0
                    and asset.get("art_selected_by_this_operation") is False, "SCENE_ASSET_LAYOUT_INVALID")
            role = asset.get("source_role")
            require(role in ("EXISTING_EXACT3X_FULL_NATIVE_CANVAS_DELIVERY",
                             "CURRENT_MODERN_MASTER_DIRECT3X_DIAGNOSTIC_CANDIDATE",
                             "EXISTING_EXACT3X_DELIVERY_BYTE_PRESERVED"), "SCENE_SOURCE_ROLE_INVALID")
            require(row.get("source_role") == role and row.get("status") == "READY_EXACT3X_INPUT",
                    "SCENE_OCCURRENCE_SOURCE_ROLE_DRIFT")
            # Replacing a historical placeholder is recipe authority; whether
            # the image is newly sampled or byte-preserved is separate lineage.
            replacement = old.get("replacement", {}).get("replace_existing_placeholder") is True
            require(row.get("remove_historical_placeholder_before_replacement") is replacement,
                    "SCENE_PLACEHOLDER_REPLACEMENT_REQUIRED")
            if asset_id not in images:
                refs.document(asset["provenance"], "SCENE_ASSET_PROVENANCE")
                pixels = asset["image"]
                image = refs.png(asset["file"], "SCENE_LAYER", row["canvas_3x"],
                                 pixels["rgba_sha256"], rgba_only=True)
                require(pixels.get("mode") == "RGBA" and pixels.get("pixels") == list(image.size),
                        "SCENE_LAYER_METADATA_DRIFT")
                alpha = image.getchannel("A")
                box = alpha.getbbox()
                ox, oy = row["origin_3x"]; cw, ch = row["crop_3x"]
                require(box is None or (box[0] >= ox and box[1] >= oy and box[2] <= ox + cw
                        and box[3] <= oy + ch), "SCENE_POSITIVE_ALPHA_OUTSIDE_NATIVE_CROP")
                images[asset_id] = image
                asset_proofs[asset_id] = {"file": refs.files[str(native._absolute_lexical_path(
                    Path(asset["file"]["path"]), "layer"))], "rgba_sha256": pixels["rgba_sha256"],
                    "pixels": list(image.size), "source_role": role, "composition_sampling_steps": 0}
        layers.append({"occurrence_id": oid, "key": list(key), "state": row["state"], "building_slot": slot,
                       "draw_order": order, "xy_3x": [v * 3 for v in xy], "native_geometry": row["native_geometry"],
                       "asset_id": asset_id, "replaced_placeholder": asset_id is not None and
                       row["remove_historical_placeholder_before_replacement"]})
    require(used == set(assets), "SCENE_UNREFERENCED_ASSET_REJECTED")
    state_layers = {}
    for name, state in state_sets["owner"].items():
        rows = sorted((x for x in layers if x["state"] == name), key=lambda x: x["draw_order"])
        require(rows and len({x["building_slot"] for x in rows}) == len(rows)
                and len({x["draw_order"] for x in rows}) == len(rows), "SCENE_DUPLICATE_SLOT_OR_ORDER")
        ids = [x["occurrence_id"] for x in rows]
        declared = state_sets["authority"][name]
        require(state.get("occurrence_ids") == ids == state_sets["facts"][name].get("occurrence_ids")
                == declared.get("ordered_occurrence_ids"), "SCENE_ORDER_OR_VISIBLE_SET_DRIFT")
        require(declared.get("diagnostic_recipe_explicit") is True
                and declared.get("game_state_acceptance") is False
                and declared.get("visible_slot_ids") == [x["building_slot"] for x in rows]
                and declared.get("native_draw_order_indices") == [x["draw_order"] for x in rows],
                "SCENE_STATE_SLOT_OR_ORDER_AUTHORITY_DRIFT")
        require(state.get("layer_count") == len(rows), "SCENE_STATE_LAYER_COUNT_DRIFT")
        state_layers[name] = rows
    background = owner["background"]
    bg_key = native.key_tuple(background["key"])
    require(bg_key[0] == "pcx" and bg_key in catalog
            and native.official_targets(catalog[bg_key], bg_key)["canvas_1x"] == native_size,
            "SCENE_NATIVE_BACKGROUND_GEOMETRY_DRIFT")
    highest = refs.png(background["highest_source"], "SCENE_BACKGROUND_MASTER", background["actual_pixels"])
    require(background.get("required_minimum_pixels") == viewport
            and background.get("resample_lower_source_to_fake3x") is False, "SCENE_BACKGROUND_MAPPING_INVALID")
    bg = None
    if background.get("usable_3x_source") is None:
        require(background.get("status") == "NEEDS_HIGHER_MASTER", "SCENE_BACKGROUND_STATUS_INVALID")
        blockers.insert(0, {"code": "NEEDS_HIGHER_MASTER", "key": list(bg_key),
                           "actual_pixels": list(highest.size), "required_pixels": viewport})
    else:
        require(background.get("status") == "READY_EXACT3X_INPUT", "SCENE_BACKGROUND_READY_STATUS_REQUIRED")
        bg = refs.png(background["usable_3x_source"], "SCENE_BACKGROUND_3X", viewport,
                      background["usable_3x_source"].get("rgba_sha256"))
        require(highest.width >= viewport[0] and highest.height >= viewport[1], "SCENE_BACKGROUND_MASTER_BELOW3X")
    contract = owner["composition_contract"]
    require(contract.get("sampling_steps") == 0 and contract.get("verify_all_file_hashes_before_output") is True
            and contract.get("viewport_clipping_only") is True, "SCENE_ZERO_RESAMPLING_CONTRACT_REQUIRED")
    summary = {"unique_ready_3x_assets": len(images), "occurrences": len(layers),
               "ready_occurrences": sum(x["asset_id"] is not None for x in layers),
               "missing_occurrences": sum(x["asset_id"] is None for x in layers),
               "background_ready": bg is not None, "states": len(state_layers), "new_1x_files": 0}
    report = {"schema": "h3.town-scene.preflight.v1", "operation_id": operation_id,
              "status": "BLOCKED" if blockers else "REVIEW_REQUIRED", "blockers": blockers,
              "manifest": manifest_ref, "scene_inputs": manifest["scene_inputs"],
              "official_inventory": manifest["official_inventory"], "viewport_3x": viewport,
              "summary": summary, "states": state_layers, "assets": asset_proofs,
              "sources": sorted(refs.files.values(), key=lambda x: x["path"]), "tool": tool_binding,
              "composition": "existing full native canvases at native XY*3; straight-alpha encoded-sRGB source-over",
              "sampling_steps": 0, "authority": AUTHORITY.copy()}
    refs.verify_current()
    return report, refs, images, bg


def artifacts(preflight, images, background):
    files = {"preflight.json": encoded(preflight)}
    outputs = {}
    if not preflight["blockers"]:
        for name, layers in preflight["states"].items():
            scene = background.copy()
            for layer in layers:
                scene.alpha_composite(images[layer["asset_id"]], tuple(layer["xy_3x"]))
            buffer = io.BytesIO()
            scene.save(buffer, format="PNG")
            raw = buffer.getvalue()
            filename = f"scene-{name}-3x.png"
            files[filename] = raw
            outputs[name] = {"file": filename, "sha256": hashlib.sha256(raw).hexdigest(),
                             "rgba_sha256": hashlib.sha256(scene.tobytes()).hexdigest(),
                             "pixels": list(scene.size), "layers": len(layers)}
    report = {"schema": "h3.town-scene.diagnostic-composite-report.v2", "status": preflight["status"],
              "operation_id": preflight["operation_id"], "preflight_sha256": hashlib.sha256(files["preflight.json"]).hexdigest(),
              "outputs": outputs, "new_png_files": len(outputs), "new_1x_files": 0,
              "sampling_steps": 0, "blockers": preflight["blockers"], "authority": AUTHORITY.copy()}
    files["scene-report.json"] = encoded(report)
    cards = "".join(f'<figure><figcaption>{html.escape(name)} · 3X 文件，原生尺寸显示</figcaption>'
                    f'<img src="{row["file"]}" width="{preflight["viewport_3x"][0] // 3}" '
                    f'height="{preflight["viewport_3x"][1] // 3}"></figure>' for name, row in outputs.items())
    files["index.html"] = ('<!doctype html><meta charset="utf-8"><title>Town 场景审核</title>'
        '<style>body{background:#17212b;color:#eee;font:15px system-ui}img{max-width:none}</style>'
        f'<h1>{preflight["status"]}</h1><p>仅技术诊断；美术、原生可达状态及游戏验收未通过。'
        '现有3X画布按XY×3放置，零再采样，无新1X文件。</p>'
        f'<pre>{html.escape(json.dumps(preflight["blockers"],ensure_ascii=False,indent=2))}</pre>{cards}').encode("utf-8")
    recipe = {"schema": "h3.town-scene.operation.v1", "operation_id": preflight["operation_id"],
              "preflight_sha256": hashlib.sha256(files["preflight.json"]).hexdigest(),
              "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}}
    files["operation.json"] = encoded(recipe)
    return files, report


def execute(action, manifest_path, expected_sha256, outdir, operation_id, tool_binding):
    require(action in ("check", "run", "verify"), "SCENE_ACTION_INVALID")
    outdir = native._absolute_lexical_path(Path(outdir), "scene output")
    require(native._path_key(outdir.parent) == native._path_key(OUTPUT_BASE)
            and outdir.name == operation_id, "SCENE_OUTPUT_MUST_MATCH_OPERATION_CHILD")
    native.reject_reparse_components(outdir, allow_missing_leaf=True)
    preflight, refs, images, background = prepare(manifest_path, expected_sha256, operation_id, tool_binding)
    if action == "check":
        return {**preflight, "files_written": 0}
    files, report = artifacts(preflight, images, background)
    pending = {f".{name}.{hashlib.sha256(raw).hexdigest()[:16]}.pending" for name, raw in files.items()}
    allowed = set(files) | pending | {".operation.lock"}
    exists = native._lexists(outdir)
    if exists:
        guard = native.OutputRootGuard.open_existing(outdir, OUTPUT_BASE)
    else:
        require(action == "run", "SCENE_OPERATION_NOT_FOUND")
        guard = native.OutputRootGuard.create(outdir, OUTPUT_BASE)
    written = 0
    new_png_files = 0
    try:
        require(all(x.name in allowed for x in outdir.iterdir()), "SCENE_UNKNOWN_OR_DIVERGENT_OUTPUT")
        with native.operation_lease(guard, create=action == "run"):
            require(all(x.name in allowed for x in outdir.iterdir()), "SCENE_UNKNOWN_OR_DIVERGENT_OUTPUT")
            operation = outdir / "operation.json"
            if operation.exists():
                require(native.read_bounded(operation, len(files["operation.json"]), "operation") == files["operation.json"],
                        "SCENE_OPERATION_PAYLOAD_CHANGED")
            elif action == "verify":
                raise native.ReviewError("SCENE_OPERATION_INCOMPLETE")
            elif any(x.name not in {".operation.lock"} | {n for n in pending if n.startswith(".operation.json.")}
                     for x in outdir.iterdir()):
                raise native.ReviewError("SCENE_OPERATION_IDENTITY_MISSING")
            # Validate every completed file and pending prefix before adding any new bytes.
            for name, raw in files.items():
                target = outdir / name
                part = outdir / f".{name}.{hashlib.sha256(raw).hexdigest()[:16]}.pending"
                if native._lexists(target):
                    guard.verify_target(target, must_exist=True)
                    require(native.read_bounded(target, len(raw), "scene output") == raw, "SCENE_OUTPUT_BYTES_DIVERGED")
                elif action == "verify":
                    raise native.ReviewError("SCENE_OPERATION_INCOMPLETE")
                if native._lexists(part):
                    guard.verify_target(part, must_exist=True)
                    prefix = native.read_bounded(part, len(raw), "pending scene output") if part.stat().st_size else b""
                    require(raw.startswith(prefix),
                            "SCENE_PENDING_BYTES_DIVERGED")
            refs.verify_current()
            if action == "run":
                for name in ["operation.json"] + [n for n in files if n != "operation.json"]:
                    created = native.write_exact_atomic(outdir / name, files[name], guard)
                    written += created
                    new_png_files += bool(created and name.endswith(".png"))
            refs.verify_current()
            guard.verify_current()
    finally:
        guard.close()
    blocked = bool(preflight["blockers"])
    status = ("VERIFIED_BLOCKED" if blocked else "VERIFIED_REVIEW_REQUIRED") if action == "verify" else preflight["status"]
    return {"schema": "h3.town-scene.result.v1", "status": status, "operation_id": operation_id,
            "blocked": blocked, "verified": True, "reused": action == "run" and written == 0,
            "files_written": written, "new_png_files": new_png_files,
            "report": str(outdir / "scene-report.json"),
            "report_sha256": hashlib.sha256(files["scene-report.json"]).hexdigest(),
            "summary": preflight["summary"], "blockers": preflight["blockers"], "authority": AUTHORITY.copy()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("check", "run", "verify"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--renderer", required=True, type=Path)
    parser.add_argument("--renderer-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        binding = native._read_build_receipt(args.renderer_sha256, args.renderer,
                                             additional_companions=("scripts/town_scene_composer.py",))
        result = execute(args.action, args.manifest, args.manifest_sha256, args.outdir, args.operation_id,
                         {"executable": {"path": str(args.renderer), "sha256": args.renderer_sha256}, "build": binding})
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("blocked") or result["status"] == "BLOCKED" else 0
    except (native.ReviewError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"schema": "h3.town-scene.error.v1", "status": "FAILED", "error": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
