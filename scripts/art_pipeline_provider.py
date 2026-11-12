#!/usr/bin/env python3
"""Discover and run the pinned, local-only yzh119 H3 art-pipeline provider.

Only audited commands are executable. Upstream source is kept byte-for-byte in
providers/h3-art-pipeline/upstream and is verified against source-lock.json before
catalogue or execution. Outputs from executable commands must be named explicitly
and remain below build/experiments/art-pipeline.
"""

from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Callable, Iterable

from companion_runtime import frozen_root as runtime_frozen_root, runtime_resource_path


PRODUCT_ROOT = resolve_tool_root(__file__)
DEFAULT_PROVIDER_ROOT = PRODUCT_ROOT / "providers" / "h3-art-pipeline"
DEVELOPMENT_OUTPUT_ROOT = PRODUCT_ROOT / "build" / "experiments" / "art-pipeline"
LOCK_NAME = "source-lock.json"
PINNED_SOURCE_COMMIT = "726856e58e1f219a897823c24616f158f016a103"
PINNED_SOURCE_LOCK_SHA256 = "432a5e19578e04cded8ca96dbd4cdc11240fae2b13046be7baea967c41c0cd63"
UPSTREAM_DIR_NAME = "upstream"
DEFAULT_RUN_TIMEOUT_SECONDS = 4 * 60 * 60
DEPENDENCY_PROBE_TIMEOUT_SECONDS = 20


class ProviderError(RuntimeError):
    """A provider command could not be trusted or safely invoked."""


# Read-only operations or operations whose complete output surface is explicit,
# isolated and audited. The remaining upstream CLI scripts stay discoverable but
# blocked until their particular I/O contract has an adapter.
RUNNABLE: dict[str, dict] = {
    "creature-art.def_extract.info": {
        "path": "creature-art/def_extract.py", "prefix": ["info"],
        "runtime": "python", "read_flags": ["lod", "definition"],
        "write_flags": [], "read_path_flags": [], "python_modules": [], "external_tools": [],
        "required_inputs": ["lod", "definition"], "optional_inputs": ["--anchor"],
        "description": "Read DEF groups, frame counts, canvas and optional anchors from a local LOD.",
    },
    "creature-art.def_extract.export": {
        "path": "creature-art/def_extract.py", "prefix": ["export"],
        "runtime": "python", "read_flags": ["lod", "definition"],
        "write_flags": ["--out"], "read_path_flags": [], "python_modules": [], "external_tools": [],
        "required_inputs": ["lod", "definition", "--out"], "optional_inputs": ["--groups", "--body-only"],
        "description": "Export one local DEF as body, shadow and overlay PNG references plus layout.json.",
    },
    "creature-art.validate_creature_animation": {
        "path": "creature-art/validate_creature_animation.py", "prefix": [],
        "runtime": "python", "read_flags": ["mod_dir", "--lod"],
        "write_flags": [], "read_path_flags": ["--lod"], "python_modules": [], "external_tools": [],
        "required_inputs": ["mod_dir"], "optional_inputs": ["--shooter", "--lod", "--json", "--quiet"],
        "description": "Read-only VCMI creature animation validation; nonzero exit means validation errors.",
    },
    "creature-art.preview.sheet": {
        "path": "creature-art/preview.py", "prefix": ["sheet"],
        "runtime": "python", "read_flags": ["--lod", "--def", "--mod", "--creature", "--group"],
        "write_flags": ["--out"], "read_path_flags": ["--lod", "--mod"], "python_modules": ["PIL"], "external_tools": [],
        "required_inputs": ["--group", "one source set: --lod+--def OR --mod+--creature", "--out"],
        "optional_inputs": ["--layer", "--scale", "--columns", "--anchor", "--numbers"],
        "description": "Render a fixed-crop contact sheet for one original or replacement animation group.",
    },
    "creature-art.preview.layers": {
        "path": "creature-art/preview.py", "prefix": ["layers"],
        "runtime": "python", "read_flags": ["--lod", "--def", "--mod", "--creature", "--group"],
        "write_flags": ["--out"], "read_path_flags": ["--lod", "--mod"], "python_modules": ["PIL"], "external_tools": [],
        "required_inputs": ["--group", "one source set: --lod+--def OR --mod+--creature", "--out"],
        "optional_inputs": ["--frame", "--scale"],
        "description": "Render one selected frame as body, shadow and overlay layers.",
    },
    "creature-art.preview.anim": {
        "path": "creature-art/preview.py", "prefix": ["anim"],
        "runtime": "python", "read_flags": ["--lod", "--def", "--mod", "--creature", "--group"],
        "write_flags": ["--out"], "read_path_flags": ["--lod", "--mod"], "python_modules": ["PIL"], "external_tools": [],
        "required_inputs": ["--group", "one source set: --lod+--def OR --mod+--creature", "--out"],
        "optional_inputs": ["--scale", "--fps", "--anchor"],
        "description": "Render an animated GIF for one original or replacement animation group.",
    },
    "creature-art.preview.readability": {
        "path": "creature-art/preview.py", "prefix": ["readability"],
        "runtime": "python", "read_flags": ["image", "--lod", "--against"],
        "write_flags": ["--out"], "read_path_flags": ["--lod"], "python_modules": ["PIL"], "external_tools": [],
        "required_inputs": ["image", "--out"],
        "optional_inputs": ["--height", "--lod", "--against", "--against-group", "--against-frame", "--background", "--bg-tolerance"],
        "description": "Inspect concept readability at the game sprite size, optionally against an original frame.",
    },
    "creature-art.preview.compare": {
        "path": "creature-art/preview.py", "prefix": ["compare"],
        "runtime": "python", "read_flags": ["--lod", "--def", "--mod", "--creature", "--group"],
        "write_flags": ["--out"], "read_path_flags": ["--lod", "--mod"], "python_modules": ["PIL"], "external_tools": [],
        "required_inputs": ["--group", "--lod+--def+--mod+--creature", "--out"],
        "optional_inputs": ["--scale-out"],
        "description": "Compare original and replacement frames side by side for one animation group.",
    },
    "creature-art.motion_preview": {
        "path": "creature-art/motion_preview.py", "prefix": [],
        "runtime": "python", "adapter": "isolated-motion-preview", "reject_reparse_inputs": True,
        "read_flags": ["--study-dir", "--reference-dir", "--layout", "--previous-dir"],
        "write_flags": ["--out"],
        "read_path_flags": ["--study-dir", "--reference-dir", "--layout", "--previous-dir"],
        "input_path_flags": {
            "--study-dir": {"kind": "directory"},
            "--reference-dir": {"kind": "directory"},
            "--layout": {"kind": "file", "suffixes": [".json"]},
            "--previous-dir": {"kind": "directory", "required": False},
        },
        "python_modules": ["PIL"], "external_tools": ["ffmpeg"],
        "output_kind": "new_directory", "output_direct_child_only": True,
        "required_inputs": [
            "--study-dir (upstream skeleton-motion export with manifest, 1x/2x sprites and review frames)",
            "--reference-dir (body directory from a DEF export)",
            "--layout (that export's sibling layout.json)", "--out (fresh directory directly under --work-root)",
        ],
        "optional_inputs": ["--previous-dir (prior upstream skeleton-motion export; used only for a moving clip)"],
        "description": "Isolated QA preview for the pinned upstream skeleton-motion/CSKELE study format; not a universal H3 action renderer or timing approval.",
    },
    "creature-art.study_preview": {
        "path": "creature-art/study_preview.py", "prefix": [],
        "runtime": "python", "adapter": "isolated-study-preview", "reject_reparse_inputs": True,
        "read_flags": ["--study-dir", "--reference-dir"],
        "write_flags": ["--out"],
        "read_path_flags": ["--study-dir", "--reference-dir"],
        "input_path_flags": {
            "--study-dir": {"kind": "directory"},
            "--reference-dir": {"kind": "directory"},
        },
        "python_modules": ["PIL"], "external_tools": [],
        "output_kind": "new_directory", "output_direct_child_only": True,
        "required_inputs": [
            "--study-dir (pinned four-pose skeleton-study export, including before/ and hand PNGs)",
            "--reference-dir (body directory from a DEF export)",
            "--out (fresh directory directly under --work-root)",
        ],
        "optional_inputs": [],
        "description": "Isolated four-pose QA preview for the pinned upstream skeleton study; it is not a generic H3 creature-action review or art approval.",
    },
    "creature-art.new_creature_mod": {
        "path": "creature-art/new_creature_mod.py", "prefix": [],
        "runtime": "python", "read_flags": ["--from-def", "--layout"],
        "write_flags": ["--mod-dir"], "read_path_flags": ["--from-def", "--layout"], "python_modules": [], "external_tools": [],
        "required_inputs": ["--mod-dir", "--creature", "--from-def OR (--canvas and --layout)"],
        "optional_inputs": ["--scales", "--generate-shadow", "--generate-overlay", "--force"],
        "description": "Create a new empty VCMI creature-mod scaffold and render manifest from a local DEF or explicit layout.",
    },
    "creature-art.render_sprites": {
        "path": "creature-art/render_sprites.py", "prefix": [],
        "runtime": "blender", "read_flags": ["--model", "--lod", "--def"],
        "write_flags": ["--out"], "read_path_flags": ["--model", "--lod"],
        "input_path_flags": {"--model": {"kind": "file", "protect_parent": True, "suffixes": [".glb", ".gltf", ".fbx"]}},
        "python_modules": [], "external_tools": [],
        "required_inputs": ["--model (existing .glb/.gltf/.fbx file)", "--out", "--group to render a configured animation"],
        "optional_inputs": ["--name", "--canvas", "--ground", "--height", "--elevation", "--azimuth", "--frames", "--samples", "--creature", "--lod", "--def", "--rebind-weapon", "--key-energy", "--ambient", "--body-only", "--scale"],
        "description": "Render one selected upstream-authored pose group from a rigged model; H3 group profiles are specific to this upstream rig work, not a universal one-image full-animation generator.",
    },
    "creature-art.render_portrait": {
        "path": "creature-art/render_portrait.py", "prefix": [],
        "runtime": "blender", "read_flags": ["--source"],
        "write_flags": ["--out"], "read_path_flags": ["--source"],
        "input_path_flags": {"--source": {"kind": "file", "protect_parent": True, "suffixes": [".blend"]}},
        "sidecar_suffixes": [".json"],
        "python_modules": [], "external_tools": [],
        "required_inputs": ["--source", "--out"],
        "optional_inputs": ["--frame", "--width", "--height", "--samples"],
        "description": "Render one static portrait from an existing editable Blender scene, plus a SHA receipt.",
    },
    "creature-art.render_saved_creature": {
        "path": "creature-art/render_saved_creature.py", "prefix": [],
        "runtime": "blender", "read_flags": ["--source", "--check"],
        "write_flags": ["--out"], "read_path_flags": ["--source", "--check"], "python_modules": [], "external_tools": [],
        "required_inputs": ["--source", "--check", "--out"], "optional_inputs": [],
        "description": "Render already-authored native creature frames from checked Blender scenes.",
    },
    "creature-art.render_ui_portraits": {
        "path": "creature-art/render_ui_portraits.py", "prefix": [],
        "runtime": "blender", "read_flags": ["--manifest"],
        "write_flags": ["--out"], "read_path_flags": ["--manifest"], "python_modules": [], "external_tools": [],
        "required_inputs": ["--manifest", "--out"], "optional_inputs": [],
        "description": "Batch render static UI portraits from scenes named in an explicit manifest.",
    },
    "creature-art.roster_preview": {
        "path": "creature-art/roster_preview.py", "prefix": [],
        "runtime": "python", "read_flags": ["--mod", "--unit"],
        "write_flags": ["--out"], "read_path_flags": ["--mod"], "python_modules": ["PIL"], "external_tools": ["ffmpeg"],
        "required_inputs": ["--mod", "--unit (repeatable)", "--out", "--title"],
        "optional_inputs": ["--panel-top", "--double-wide"],
        "description": "Create offline pose sheets, review videos and a roster review page from installed-style resources.",
    },
    "creature-art.bootstrap_review": {
        "path": "creature-art/bootstrap_review.py", "prefix": [],
        "runtime": "blender", "runtime_requirements": "Blender; upstream tested with 5.2.1",
        "read_flags": ["--model"], "read_path_flags": ["--model"], "input_path_flags": {"--model": {"kind": "file", "protect_parent": True, "suffixes": [".glb", ".gltf", ".fbx"]}},
        "write_flags": ["--out"], "output_kind": "new_directory",
        "python_modules": [], "external_tools": [],
        "required_inputs": ["--model (existing .glb/.gltf/.fbx file)", "--out (new directory)"],
        "optional_inputs": ["--height", "--samples", "--exclude-object (repeatable)"],
        "output_contents": ["view-000.png through view-315.png", "normalized.blend", "audit.json", "bootstrap_review.py"],
        "description": "Normalize one local 3D mesh and create eight fixed-scale turnaround views, an editable scene and geometry/material audit; this is static model intake, not animation generation.",
    },
    "creature-art.proxy_skin": {
        "path": "creature-art/proxy_skin.py", "prefix": [],
        "runtime": "blender", "runtime_requirements": "Blender; upstream tested with 5.2.1",
        "read_flags": ["--source", "--body", "--armature"], "read_path_flags": ["--source"],
        "input_path_flags": {"--source": {"kind": "file", "protect_parent": True, "suffixes": [".blend"]}},
        "write_flags": ["--out"], "output_kind": "new_directory",
        "python_modules": [], "external_tools": [],
        "required_inputs": ["--source (existing .blend scene)", "--body (mesh object name)", "--armature (existing armature object name)", "--out (new directory)"],
        "optional_inputs": ["--voxel", "--exclude-bone (repeatable)", "--exclude-prefix (repeatable)", "--max-fill-fraction", "--max-fill-distance"],
        "output_contents": ["source.blend", "audit.json"],
        "description": "Transfer voxel-proxy skin weights onto a selected mesh under an already-fitting armature; saves a separate scene and audit, and does not create or place a rig.",
    },
}


NETWORK_SCRIPTS = {
    "creature-art/gen_concept.py": "Uses a hosted FLUX API; network asset generation and secret handling are not enabled in this local provider.",
    "creature-art/gen_mesh.py": "Uses the hosted Meshy API; network upload, account credits and secret handling are not enabled in this local provider.",
    "creature-art/rig_mesh.py": "Uses the hosted Meshy API; network upload, account credits and secret handling are not enabled in this local provider.",
    "creature-art/remesh_mesh.py": "Uses the hosted Meshy API; network upload, account credits and secret handling are not enabled in this local provider.",
}


SUBCOMMANDS = {
    "creature-art/def_extract.py": ["info", "export"],
    "creature-art/preview.py": ["sheet", "layers", "anim", "readability", "compare"],
}


# Keep the non-runnable upstream entries useful to AI discovery. A purpose is
# taken from each upstream docstring; this table records the actual reason the
# command is outside the generic H3TierExport execution surface.
ENTRY_DISPOSITIONS: dict[str, tuple[str, str]] = {}


def _assign_disposition(paths: Iterable[str], status: str, reason: str) -> None:
    for relative in paths:
        if relative in ENTRY_DISPOSITIONS:
            raise RuntimeError(f"duplicate upstream disposition: {relative}")
        ENTRY_DISPOSITIONS[relative] = (status, reason)


_assign_disposition([
    "creature-art/check_nonhumanoid_motion.py",
], "requires_adapter",
   "Reads one staged manifest and its Blender scenes, but assumes the mesh object is literally `Mesh_0`; its --out JSON may overwrite an existing report. Next adapter step: add a required body-object selector and no-overwrite report reservation. It measures tears/ground only, not art quality.")
_assign_disposition([
    "creature-art/assemble_mod.py", "creature-art/roster_mod.py",
    "creature-art/offset_animation.py", "creature-art/package_backdrop.py",
    "creature-art/stabilize_shadows.py",
], "scope_vcmi_creature_mod",
   "VCMI creature-mod PNG/JSON packaging or post-processing, not a Heroes III `.def` writer. Only consider behind an explicit VCMI target. Some commands clone to --out; assemble_mod writes into --mod in place, and roster_mod --replace-existing deletes selected resources inside its output clone.")
_assign_disposition([
    "creature-art/assemble_vampire.py", "creature-art/bat_study.py",
    "creature-art/bootstrap_bind.py", "creature-art/lich_study.py",
    "creature-art/mounted_dragon_motion.py", "creature-art/repair_knight_attacks.py",
    "creature-art/settle_equipment.py", "creature-art/skeleton_motion.py",
    "creature-art/skeleton_study.py", "creature-art/upgrade_study.py",
    "creature-art/vampire_flight.py", "creature-art/vampire_study.py",
    "creature-art/wight_motion.py", "creature-art/zombie_study.py",
], "scope_character_study",
   "Single-creature study/repair with fixed anatomy, bone names, mesh landmarks, equipment or profile assumptions. Preserve as source examples; do not expose as a roster-wide action generator without a new generic rig/profile contract.")
_assign_disposition([
    "creature-art/bootstrap_material_still.py", "creature-art/bootstrap_portrait.py",
    "creature-art/bootstrap_showcase.py", "creature-art/build_joint_colliders.py",
    "creature-art/check_shape_key_isolation.py", "creature-art/check_skin.py",
    "creature-art/restore_rig_materials.py", "creature-art/transfer_rig.py",
], "scope_specialist_utility",
   "A useful but narrow Blender review/repair utility (static material/portrait/calibration, explicit cloth segments, shape-key masks, humanoid tear checks, unchanged-UV material restoration, or donor-rig transfer). It has no general roster action contract; retain for a future opt-in specialist surface rather than the initial batch lane.")
_assign_disposition([
    "creature-art/export_roster_reference.py",
], "excluded_duplicate_authority",
   "Extracts canvas/bbox/group counts using a second DEF parser and defaults to a Necropolis-only 14-creature roster. Reuse H3TierExport's existing native inventory/DEF authority; do not establish a second count source.")
_assign_disposition([
    "creature-art/cache_creature_scale.py", "environment-art/build_adventure_assets.py",
], "fallback_upscale_only",
   "Creates display-scale caches or geometry-preserving 2x/3x/4x adventure overrides from existing pixels. It does not add authored detail; cache_creature_scale writes directly into the supplied mod. Keep as an explicitly named fallback, not the modern redraw/action producer.")
_assign_disposition([
    "creature-art/make_final_four_test_map.py",
], "scope_test_fixture",
   "Builds a diagnostic map from VCMI's MiniTest fixture for a particular integration test. Not an art-production capability.")
_assign_disposition([
    "creature-art/settle_nonhumanoid.py",
], "blocked_in_place_mutator",
   "Writes into its source study/export tree rather than a separate output: settle_nonhumanoid rewrites scene/PNG/manifest data. Keep unavailable until a separate staged copy or explicit --out contract prevents changing approved inputs.")
_assign_disposition([
    "creature-art/spectral_study.py",
], "excluded_rejected_experiment",
   "Upstream marks this procedural ghost/lich appearance prototype REJECTED and says not to install its exports. Preserve only as historical research; never expose as a production action.")

_assign_disposition([
    "environment-art/adapt_creature_sequence.py",
    "environment-art/register_animated_scenery.py",
    "environment-art/register_landmarks.py",
    "environment-art/repaint_terrain.py",
], "blocked_vcmi_in_place_mutator",
   "Edits existing VCMI adventure-map mod PNGs in place (`--mod`) and can overwrite approved resources. These are separate map-object/terrain operations, not battle-creature animation; any future adapter must first stage a fresh mod copy and emit an audit.")
_assign_disposition([
    "environment-art/audit_customization.py", "environment-art/verify_adventure_assets.py",
], "scope_vcmi_map_validation",
   "Read-only structural/customization audits for VCMI adventure-map overrides and their audit manifests. Useful only in a separately selected VCMI map-art mode; they do not validate battle-creature timing or legacy DEF packaging.")
_assign_disposition([
    "environment-art/build_battlefields.py", "environment-art/build_map.py",
], "scope_vcmi_environment_packaging",
   "Packages generated common battlefield or adventure-map town art into VCMI resource trees. build_map is hard-coded to three Necropolis map-town resources; neither operation authors creature battle actions or legacy DEFs.")
_assign_disposition([
    "environment-art/export_references.py",
], "excluded_duplicate_reader",
   "Extracts common battlefield PCX references from H3bitmap.lod using its own parser/config. Reuse the product's native resource inventory/extraction authority instead of adding another archive reader; this is a VCMI battlefield reference job.")
_assign_disposition([
    "environment-art/font_probe.py",
], "scope_font_rendering",
   "Offline SDL3_ttf Chinese-font comparison, unrelated to sprite actions; it also carries platform-specific SDL library assumptions.")
_assign_disposition([
    "town-art/build_layered_town.py", "town-art/build_ui_thumbnails.py",
    "town-art/derive_necropolis_ui_buildings.py", "town-art/export_necropolis_reference.py",
    "town-art/extract_ui_references.py", "town-art/prepare_building_inputs.py",
    "town-art/refine_buildings.py",
], "scope_necropolis_town_pipeline",
   "Necropolis town-only VCMI layout/reference/UI pipeline with fixed resource IDs and building slots. These operations may inform a future faction-config-driven town-art module, but they are not generic seven-faction tools or creature action generation.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def provider_root(path: str | Path | None) -> Path:
    frozen = runtime_frozen_root()
    declared = None
    if frozen is not None or not path:
        try:
            declared = runtime_resource_path("h3-art-pipeline", PRODUCT_ROOT)
        except OSError as error:
            raise ProviderError(f"PROVIDER_ROOT_MISSING: {error}") from error
        except RuntimeError as error:
            raise ProviderError(str(error)) from error
    root = Path(path).expanduser() if path else declared
    if (frozen is not None
            and os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(declared))):
        raise ProviderError("FROZEN_PROVIDER_ROOT_OVERRIDE_REJECTED: use the declared packaged provider")
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise ProviderError(f"PROVIDER_ROOT_MISSING: {root}: {error}") from error
    if not (root / LOCK_NAME).is_file() or not (root / UPSTREAM_DIR_NAME).is_dir():
        raise ProviderError(f"PROVIDER_LAYOUT_INVALID: expected {LOCK_NAME} and {UPSTREAM_DIR_NAME}/ under {root}")
    return root


def load_lock(root: Path) -> dict:
    lock_path = root / LOCK_NAME
    if _sha256(lock_path) != PINNED_SOURCE_LOCK_SHA256:
        raise ProviderError("SOURCE_LOCK_HASH_MISMATCH: pinned source-lock.json changed")
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderError(f"SOURCE_LOCK_UNREADABLE: {error}") from error
    if data.get("schema") != "h3tierexport.upstream-source-lock.v1":
        raise ProviderError("SOURCE_LOCK_SCHEMA_UNSUPPORTED")
    if data.get("repository") != "https://github.com/yzh119/h3-art-pipeline":
        raise ProviderError("SOURCE_LOCK_REPOSITORY_MISMATCH")
    if data.get("commit") != PINNED_SOURCE_COMMIT:
        raise ProviderError("SOURCE_LOCK_COMMIT_MISMATCH")
    return data


def verify_source_tree(root: Path) -> dict:
    root = provider_root(root)
    lock = load_lock(root)
    upstream = (root / UPSTREAM_DIR_NAME).resolve(strict=True)
    expected = {entry["path"]: entry for entry in lock.get("files", [])}
    actual_paths: set[str] = set()
    for path in (root / UPSTREAM_DIR_NAME).rglob("*"):
        if path.is_symlink():
            raise ProviderError(f"SOURCE_LINK_FORBIDDEN: {path.relative_to(root / UPSTREAM_DIR_NAME).as_posix()}")
        if path.is_file():
            try:
                relative = path.resolve(strict=True).relative_to(upstream).as_posix()
            except (OSError, ValueError) as error:
                raise ProviderError(f"SOURCE_PATH_ESCAPE: {path}") from error
            actual_paths.add(relative)
    missing = sorted(set(expected) - actual_paths)
    extra = sorted(actual_paths - set(expected))
    drift = []
    for relative in sorted(set(expected) & actual_paths):
        path = root / UPSTREAM_DIR_NAME / Path(relative)
        entry = expected[relative]
        if path.stat().st_size != entry.get("bytes") or _sha256(path) != entry.get("sha256"):
            drift.append(relative)
    result = {"passed": not (missing or extra or drift), "expected_files": len(expected),
              "actual_files": len(actual_paths), "missing": missing, "extra": extra, "drift": drift,
              "commit": lock.get("commit"), "license": lock.get("license")}
    if not result["passed"]:
        raise ProviderError("SOURCE_INTEGRITY_FAILURE: " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _argparse_inventory(tree: ast.Module) -> tuple[list[dict], bool]:
    arguments = []
    has_parser = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name == "ArgumentParser":
            has_parser = True
        if call_name != "add_argument" or not node.args:
            continue
        names = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if not names:
            continue
        required = any(keyword.arg == "required" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                       for keyword in node.keywords)
        path_type = any(keyword.arg == "type" and _call_name(keyword.value) in {"Path", "FilePath"}
                        for keyword in node.keywords)
        action = next((keyword.value.value for keyword in node.keywords
                       if keyword.arg == "action" and isinstance(keyword.value, ast.Constant)), None)
        is_option = any(name.startswith("-") for name in names)
        arguments.append({"names": names, "required": required or not is_option,
                          "path_typed": path_type, "action": action,
                          "positional": not is_option})
    return arguments, has_parser


def _python_dependencies(tree: ast.Module, script_path: Path, upstream: Path,
                         local_modules: set[str] | None = None) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    names.discard("__future__")
    names.discard("bpy")
    if local_modules is None:
        local_modules = {local.stem for local in upstream.rglob("*.py")}
    names.difference_update(local_modules)
    stdlib = getattr(sys, "stdlib_module_names", set())
    external = sorted(name for name in names if name not in stdlib and name not in {"mathutils"})
    return external


def _looks_like_output(argument: dict) -> bool:
    text = " ".join(argument["names"]).lower().lstrip("-")
    tokens = text.replace("_", "-").split("-")
    return bool(set(tokens) & {"out", "output", "report", "audit", "export"})


def _catalog_with_integrity(root: Path) -> tuple[list[dict], dict]:
    integrity = verify_source_tree(root)
    upstream = root / UPSTREAM_DIR_NAME
    lock = load_lock(root)
    locked = {entry["path"] for entry in lock["files"]}
    local_modules = {source.stem for source in upstream.rglob("*.py")}
    found = []
    for relative in sorted(locked):
        if not relative.endswith(".py") or Path(relative).name.startswith("test_"):
            continue
        source = upstream / Path(relative)
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError):
            continue
        arguments, has_parser = _argparse_inventory(tree)
        main_guard = any(isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
                         for node in tree.body)
        if not (has_parser or main_guard):
            continue
        doc = ast.get_docstring(tree) or ""
        summary = " ".join(part.strip() for part in doc.splitlines() if part.strip())
        script_runtime = "blender" if any(isinstance(node, ast.Name) and node.id == "bpy"
                                           for node in ast.walk(tree)) else "python"
        external_dependencies = _python_dependencies(tree, source, upstream, local_modules)
        if relative in NETWORK_SCRIPTS:
            status = "disabled_network"
            reason = NETWORK_SCRIPTS[relative]
        elif relative in {entry["path"] for entry in RUNNABLE.values()}:
            status = "run"
            reason = "Audited local command; explicit input contract and output isolation are enforced by this adapter."
        elif relative in ENTRY_DISPOSITIONS:
            status, reason = ENTRY_DISPOSITIONS[relative]
        else:
            raise ProviderError(f"UPSTREAM_ENTRYPOINT_UNCLASSIFIED: {relative}")
        common = {"script": relative, "runtime": script_runtime,
                  "dependencies": external_dependencies,
                  "required_inputs": [name for arg in arguments if arg["required"]
                                      for name in arg["names"]],
                  "optional_arguments": [name for arg in arguments if not arg["required"]
                                         for name in arg["names"]],
                  "write_candidates_unverified": [name for arg in arguments if _looks_like_output(arg)
                                                   for name in arg["names"]],
                  "status": status, "io_status": "catalogued_scope",
                  "read_only": None, "read_flags": [], "write_flags": [], "adapter_gap": reason,
                  "summary": summary[:700], "source_integrity": "sha256-locked"}
        subcommands = SUBCOMMANDS.get(relative)
        if subcommands:
            for subcommand in subcommands:
                operation_id = f"{Path(relative).parent.as_posix().replace('/', '.')}.{Path(relative).stem}.{subcommand}"
                details = dict(common)
                details.update({"id": operation_id, "subcommand": subcommand})
                if operation_id in RUNNABLE:
                    details.update(RUNNABLE[operation_id])
                    details["io_status"] = "audited"
                    details["read_only"] = not bool(details["write_flags"])
                found.append(details)
        else:
            operation_id = Path(relative).with_suffix("").as_posix().replace("/", ".")
            details = dict(common)
            details.update({"id": operation_id})
            if operation_id in RUNNABLE:
                details.update(RUNNABLE[operation_id])
                details["io_status"] = "audited"
                details["read_only"] = not bool(details["write_flags"])
            found.append(details)
    return found, integrity


def discover_catalog(root: Path) -> list[dict]:
    return _catalog_with_integrity(root)[0]


def _find_flag_value(arguments: list[str], flag: str) -> str:
    matches = [index for index, item in enumerate(arguments) if item == flag]
    if len(matches) != 1 or matches[0] + 1 >= len(arguments):
        raise ProviderError(f"OUTPUT_FLAG_REQUIRED_ONCE: {flag}")
    value = arguments[matches[0] + 1]
    if value.startswith("-"):
        raise ProviderError(f"OUTPUT_PATH_MISSING: {flag}")
    return value


def _find_input_flag_value(arguments: list[str], flag: str) -> str:
    matches = [index for index, item in enumerate(arguments) if item == flag]
    if len(matches) != 1 or matches[0] + 1 >= len(arguments):
        raise ProviderError(f"INPUT_FLAG_REQUIRED_ONCE: {flag}")
    value = arguments[matches[0] + 1]
    if value.startswith("-"):
        raise ProviderError(f"INPUT_PATH_MISSING: {flag}")
    return value


def _flag_is_present(arguments: list[str], flag: str) -> bool:
    return any(item == flag or item.startswith(flag + "=") for item in arguments)


def _protected_path_flags(command: dict) -> list[str]:
    flags = set(command.get("write_flags", []))
    flags.update(command.get("read_path_flags", []))
    flags.update(command.get("input_path_flags", {}).keys())
    return sorted(flags, key=lambda flag: (-len(flag), flag))


def normalize_path_flag_arguments(command: dict, arguments: list[str]) -> list[str]:
    """Normalize protected `--path VALUE`/`--path=VALUE` forms and reject repeats.

    Argparse accepts both spellings and applies the last occurrence. This pass
    handles both forms before any path check, then forwards a canonical argv so
    a second spelling cannot override a validated value downstream.
    """
    path_flags = _protected_path_flags(command)
    if not path_flags:
        return list(arguments)
    # argparse defaults to allow_abbrev=True. A non-exact long option can still
    # mutate a protected path (`--ou=...` for `--out`, `--mod=...` for
    # `--mod-dir`), so reject such spellings before canonicalizing exact flags.
    for token in arguments:
        if token == "--":
            raise ProviderError("CHILD_ARGUMENT_SEPARATOR_FORBIDDEN: use only the provider's outer -- separator")
        if not token.startswith("--"):
            continue
        option = token.split("=", 1)[0]
        if option in path_flags:
            continue
        abbreviated = [flag for flag in path_flags if len(option) < len(flag) and flag.startswith(option)]
        if abbreviated:
            raise ProviderError(f"PATH_FLAG_ABBREVIATION_FORBIDDEN: {option} abbreviates {', '.join(abbreviated)}")
    counts = {flag: 0 for flag in path_flags}
    normalized = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        matched = next((flag for flag in path_flags
                        if token == flag or token.startswith(flag + "=")), None)
        if matched is None:
            normalized.append(token)
            index += 1
            continue
        counts[matched] += 1
        if counts[matched] > 1:
            raise ProviderError(f"PATH_FLAG_REPEATED: {matched}")
        if token == matched:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                raise ProviderError(f"PATH_FLAG_VALUE_REQUIRED: {matched}")
            value = arguments[index + 1]
            index += 2
        else:
            value = token[len(matched) + 1:]
            if not value:
                raise ProviderError(f"PATH_FLAG_VALUE_REQUIRED: {matched}")
            index += 1
        normalized.extend((matched, value))
    return normalized


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve(strict=False)
    right = right.resolve(strict=False)
    return left == right or _path_within(left, right) or _path_within(right, left)


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProviderError(f"PATH_INSPECTION_FAILED: {path}: {error}") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _assert_no_reparse_components(path: Path, label: str) -> None:
    """Reject symlinks/junctions in an absolute path without resolving through them."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if _is_reparse_point(current):
            raise ProviderError(f"REPARSE_PATH_FORBIDDEN: {label}={current}")


def validate_input_contract(command: dict, script_arguments: list[str]) -> list[tuple[Path, Path]]:
    """Return (input path, protected input scope) pairs after checking real inputs.

    For a file input whose parent is protected, output work cannot be placed in
    the input directory. This keeps generated files beside neither source models
    nor source scenes.
    """
    script_arguments = normalize_path_flag_arguments(command, script_arguments)
    inputs = []
    for flag, spec in command.get("input_path_flags", {}).items():
        if not _flag_is_present(script_arguments, flag) and not spec.get("required", True):
            continue
        value = _find_input_flag_value(script_arguments, flag)
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ProviderError(f"INPUT_PATH_MUST_BE_ABSOLUTE: {flag}={value}")
        if command.get("reject_reparse_inputs"):
            _assert_no_reparse_components(path, flag)
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise ProviderError(f"INPUT_PATH_MISSING: {flag}={value}: {error}") from error
        kind = spec.get("kind", "file")
        if kind == "file" and not path.is_file():
            raise ProviderError(f"INPUT_FILE_REQUIRED: {flag}={path}")
        if kind == "directory" and not path.is_dir():
            raise ProviderError(f"INPUT_DIRECTORY_REQUIRED: {flag}={path}")
        suffixes = spec.get("suffixes", [])
        if suffixes and path.suffix.lower() not in suffixes:
            raise ProviderError(f"INPUT_EXTENSION_UNSUPPORTED: {flag}={path.suffix}; expected {', '.join(suffixes)}")
        scope = path.parent if kind == "file" and spec.get("protect_parent") else path
        inputs.append((path, scope.resolve(strict=True)))
    return inputs


def validate_output_contract(command: dict, script_arguments: list[str], work_root: Path) -> list[Path]:
    script_arguments = normalize_path_flag_arguments(command, script_arguments)
    expected_root = DEVELOPMENT_OUTPUT_ROOT.resolve(strict=False)
    work_root = work_root.expanduser().resolve(strict=False)
    if not _path_within(work_root, expected_root) or work_root == expected_root:
        raise ProviderError(f"WORK_ROOT_MUST_BE_NESTED_IN_DEVELOPMENT_AREA: {expected_root}")
    reserved = []
    for flag in command.get("write_flags", []):
        value = _find_flag_value(script_arguments, flag)
        target = Path(value).expanduser()
        if not target.is_absolute():
            raise ProviderError(f"OUTPUT_PATH_MUST_BE_ABSOLUTE: {flag}={value}")
        if command.get("reject_reparse_inputs"):
            _assert_no_reparse_components(target, flag)
        target = target.resolve(strict=False)
        if not _path_within(target, work_root) or target == work_root:
            raise ProviderError(f"OUTPUT_PATH_OUTSIDE_WORK_ROOT: {flag}={target}")
        if command.get("output_direct_child_only"):
            if target.parent != work_root or target.name in {".preview-stage", ".preview-publish"}:
                raise ProviderError(f"OUTPUT_PATH_MUST_BE_FRESH_WORK_ROOT_CHILD: {target}")
            _assert_no_reparse_components(target.parent, "--out parent")
        if target.exists():
            raise ProviderError(f"OUTPUT_ALREADY_EXISTS: {target}")
        reserved.append(target)
        for suffix in command.get("sidecar_suffixes", []):
            sidecar = target.with_suffix(suffix)
            if sidecar.exists():
                raise ProviderError(f"OUTPUT_SIDECAR_ALREADY_EXISTS: {sidecar}")
            reserved.append(sidecar)
    return reserved


def validate_execution_paths(command: dict, script_arguments: list[str], work_root: Path) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Validate existing source files, isolated work root and fresh outputs."""
    script_arguments = normalize_path_flag_arguments(command, script_arguments)
    input_paths = validate_input_contract(command, script_arguments)
    output_paths = validate_output_contract(command, script_arguments, work_root)
    normalized_work_root = work_root.expanduser().resolve(strict=False)
    if command.get("reject_reparse_inputs"):
        _assert_no_reparse_components(work_root.expanduser(), "--work-root")
    for path, scope in input_paths:
        if _paths_overlap(normalized_work_root, scope):
            raise ProviderError(f"WORK_ROOT_INTERSECTS_INPUT: {normalized_work_root} overlaps {scope}")
        for output in output_paths:
            if _paths_overlap(output, scope):
                raise ProviderError(f"OUTPUT_INTERSECTS_INPUT: {output} overlaps {scope}")
    return input_paths, output_paths



MAX_PREVIEW_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_INPUT_FILES = 20_000
MAX_PREVIEW_INPUT_FILE_BYTES = 512 * 1024 * 1024
MAX_PREVIEW_INPUT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
STUDY_PREVIEW_FRAMES = {
    "holding": "holding_00.png",
    "walk_contact": "moving_00.png",
    "windup": "attack_front_01.png",
    "strike": "attack_front_03.png",
}


def _read_preview_json(path: Path, label: str) -> dict:
    _assert_no_reparse_components(path, label)
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PREVIEW_MANIFEST_BYTES:
            raise ProviderError(f"PREVIEW_MANIFEST_INVALID: {label}={path}")
        with path.open("rb") as stream:
            raw = stream.read(MAX_PREVIEW_MANIFEST_BYTES + 1)
    except ProviderError:
        raise
    except OSError as error:
        raise ProviderError(f"PREVIEW_INPUT_MISSING: {label}={path}: {error}") from error
    if len(raw) > MAX_PREVIEW_MANIFEST_BYTES:
        raise ProviderError(f"PREVIEW_MANIFEST_TOO_LARGE: {label}={path}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderError(f"PREVIEW_MANIFEST_INVALID: {label}={path}: {error}") from error
    if not isinstance(value, dict):
        raise ProviderError(f"PREVIEW_MANIFEST_INVALID: {label} must be a JSON object")
    return value


def _parse_preview_path_arguments(command: dict, script_arguments: list[str]) -> dict[str, str]:
    arguments = normalize_path_flag_arguments(command, script_arguments)
    allowed = set(command.get("input_path_flags", {})) | set(command.get("write_flags", []))
    parsed = {}
    index = 0
    while index < len(arguments):
        flag = arguments[index]
        if flag not in allowed or index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
            raise ProviderError(f"PREVIEW_ARGUMENT_UNSUPPORTED_OR_INCOMPLETE: {flag}")
        parsed[flag] = arguments[index + 1]
        index += 2
    for flag, spec in command.get("input_path_flags", {}).items():
        if spec.get("required", True) and flag not in parsed:
            raise ProviderError(f"INPUT_FLAG_REQUIRED_ONCE: {flag}")
    for flag in command.get("write_flags", []):
        if flag not in parsed:
            raise ProviderError(f"OUTPUT_FLAG_REQUIRED_ONCE: {flag}")
    return parsed


def _preview_path(arguments: dict[str, str], flag: str, *, required: bool = True) -> Path | None:
    value = arguments.get(flag)
    if value is None:
        if required:
            raise ProviderError(f"INPUT_FLAG_REQUIRED_ONCE: {flag}")
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ProviderError(f"INPUT_PATH_MUST_BE_ABSOLUTE: {flag}={value}")
    _assert_no_reparse_components(path, flag)
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise ProviderError(f"INPUT_PATH_MISSING: {flag}={value}: {error}") from error


def _require_preview_directory(path: Path, label: str) -> Path:
    _assert_no_reparse_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProviderError(f"PREVIEW_DIRECTORY_MISSING: {label}={path}: {error}") from error
    if not resolved.is_dir():
        raise ProviderError(f"PREVIEW_DIRECTORY_REQUIRED: {label}={resolved}")
    return resolved


def _require_preview_file(path: Path, label: str) -> Path:
    _assert_no_reparse_components(path, label)
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise ProviderError(f"PREVIEW_INPUT_MISSING: {label}={path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise ProviderError(f"PREVIEW_INPUT_FILE_REQUIRED: {label}={resolved}")
    return resolved


def _new_preview_plan(command_id: str, output: Path) -> dict:
    return {"command_id": command_id, "output": output, "files": [],
            "stage_file_names": set(), "total_bytes": 0}


def _bind_preview_file(plan: dict, source: Path, stage_relative: Path, label: str) -> None:
    _assert_no_reparse_components(source, label)
    try:
        source = source.resolve(strict=True)
        metadata = source.stat()
    except OSError as error:
        raise ProviderError(f"PREVIEW_INPUT_MISSING: {label}={source}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ProviderError(f"PREVIEW_INPUT_FILE_REQUIRED: {label}={source}")
    if stage_relative.is_absolute() or ".." in stage_relative.parts or not stage_relative.parts:
        raise ProviderError(f"PREVIEW_STAGE_PATH_INVALID: {stage_relative}")
    relative = stage_relative.as_posix()
    if relative.casefold() in plan["stage_file_names"]:
        raise ProviderError(f"PREVIEW_STAGE_PATH_DUPLICATED: {relative}")
    if len(plan["files"]) >= MAX_PREVIEW_INPUT_FILES:
        raise ProviderError("PREVIEW_INPUT_FILE_COUNT_LIMIT_EXCEEDED")
    if metadata.st_size > MAX_PREVIEW_INPUT_FILE_BYTES:
        raise ProviderError(f"PREVIEW_INPUT_FILE_SIZE_INVALID: {label}={source}")
    if plan["total_bytes"] + metadata.st_size > MAX_PREVIEW_INPUT_TOTAL_BYTES:
        raise ProviderError("PREVIEW_INPUT_TOTAL_SIZE_LIMIT_EXCEEDED")
    digest = _sha256(source)
    after = source.stat()
    if (after.st_size != metadata.st_size or after.st_mtime_ns != metadata.st_mtime_ns):
        raise ProviderError(f"PREVIEW_INPUT_CHANGED_DURING_PREFLIGHT: {source}")
    plan["stage_file_names"].add(relative.casefold())
    plan["files"].append({"source": source, "relative": stage_relative,
                          "label": label, "sha256": digest})
    plan["total_bytes"] += metadata.st_size


def _motion_groups(manifest: dict, layout: dict) -> tuple[list[str], dict[str, int]]:
    clips = manifest.get("clips")
    names = list(clips) if isinstance(clips, dict) else clips
    if (not isinstance(names, list) or len(names) < 3 or len(names) > 64
            or any(not isinstance(name, str) or not name or not name.isascii()
                   or not name[0].isalnum()
                   or any(not (character.isalnum() or character in "_-") for character in name)
                   for name in names)):
        raise ProviderError("MOTION_PREVIEW_GROUP_LIST_INVALID: expected 3..64 safe upstream group names")
    groups = [name.lower() for name in names]
    if len(set(groups)) != len(groups) or "holding" not in groups:
        raise ProviderError("MOTION_PREVIEW_GROUP_LIST_INVALID: groups must be unique and include holding")
    profile = manifest.get("profile")
    profile_clips = profile.get("clips") if isinstance(profile, dict) else None
    layout_groups = layout.get("groups")
    if not isinstance(profile_clips, dict) or not isinstance(layout_groups, list):
        raise ProviderError("MOTION_PREVIEW_LAYOUT_INVALID: manifest profile or reference groups missing")
    layout_counts = {}
    for item in layout_groups:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not isinstance(item.get("frames"), list) or item["name"] in layout_counts):
            raise ProviderError("MOTION_PREVIEW_LAYOUT_INVALID: each group needs a unique name and frame list")
        layout_counts[item["name"]] = len(item["frames"])
    counts = {}
    for group in groups:
        upper = group.upper()
        count = layout_counts.get(upper)
        clip = profile_clips.get(upper)
        if (not isinstance(count, int) or count <= 0 or not isinstance(clip, dict)
                or clip.get("sprite_frames") != count):
            raise ProviderError(f"MOTION_PREVIEW_FRAME_COUNT_MISMATCH: {upper}")
        counts[group] = count
    return groups, counts


def _expected_motion_preview_outputs(groups: list[str], has_previous: bool) -> set[str]:
    names = {"native-frames.png", "frames-2x.png", "index.html"}
    for group in groups:
        names.update({f"{group}.gif", f"{group}.mp4", f"{group}-frames.png"})
    if has_previous and "moving" in groups:
        names.add("walk-comparison.gif")
    if "turn_l" in groups and "turn_r" in groups:
        names.add("turn-order.gif")
    return names


def _prepare_preview_plan(command_id: str, command: dict, script_arguments: list[str]) -> dict:
    arguments = _parse_preview_path_arguments(command, script_arguments)
    study = _require_preview_directory(_preview_path(arguments, "--study-dir"), "--study-dir")
    reference = _require_preview_directory(_preview_path(arguments, "--reference-dir"), "--reference-dir")
    output = Path(arguments["--out"]).expanduser().resolve(strict=False)
    if _paths_overlap(study, reference):
        raise ProviderError("PREVIEW_INPUT_SCOPES_OVERLAP: --study-dir overlaps --reference-dir")
    plan = _new_preview_plan(command_id, output)
    if command_id == "creature-art.motion_preview":
        layout_path = _preview_path(arguments, "--layout")
        if layout_path is None or layout_path.name.lower() != "layout.json" or layout_path.parent != reference.parent:
            raise ProviderError("MOTION_PREVIEW_LAYOUT_MUST_BE_REFERENCE_SIBLING: pass sibling layout.json")
        manifest_path = _require_preview_file(study / "manifest.json", "study manifest")
        layout_data = _read_preview_json(layout_path, "--layout")
        manifest = _read_preview_json(manifest_path, "study manifest")
        groups, counts = _motion_groups(manifest, layout_data)
        _bind_preview_file(plan, manifest_path, Path("study/manifest.json"), "study manifest")
        _bind_preview_file(plan, layout_path, Path("reference/layout.json"), "reference layout")
        for group in groups:
            count = counts[group]
            for scale in (1, 2):
                folder = _require_preview_directory(study / f"sprites{scale}x", f"sprites{scale}x")
                expected = {f"{group}_{index:02d}.png" for index in range(count)}
                actual = {path.name for path in folder.glob(f"{group}_*.png")}
                if actual != expected:
                    raise ProviderError(f"MOTION_PREVIEW_SPRITE_SET_MISMATCH: {group} sprites{scale}x")
                for name in sorted(expected):
                    _bind_preview_file(plan, folder / name, Path(f"study/sprites{scale}x/{name}"),
                                       f"{group} {scale}x sprite")
            review_dir = _require_preview_directory(study / "review" / group, f"review/{group}")
            review_frames = sorted(review_dir.glob("*.png"), key=lambda path: path.name)
            if not review_frames:
                raise ProviderError(f"MOTION_PREVIEW_REVIEW_FRAMES_MISSING: {group}")
            for index, path in enumerate(review_frames):
                if path.name != f"{index:03d}.png":
                    raise ProviderError(f"MOTION_PREVIEW_REVIEW_FRAME_SEQUENCE_INVALID: {group}/{path.name}")
                _bind_preview_file(plan, path, Path(f"study/review/{group}/{path.name}"),
                                   f"{group} review frame")
            for index in range(count):
                name = f"{group}_{index:02d}.png"
                _bind_preview_file(plan, reference / name, Path(f"reference/body/{name}"),
                                   f"reference {group} frame")
        previous = _preview_path(arguments, "--previous-dir", required=False)
        if previous is not None:
            previous = _require_preview_directory(previous, "--previous-dir")
            if _paths_overlap(previous, study) or _paths_overlap(previous, reference):
                raise ProviderError("PREVIEW_INPUT_SCOPES_OVERLAP: --previous-dir overlaps another input")
            if "moving" not in groups:
                raise ProviderError("MOTION_PREVIEW_PREVIOUS_REQUIRES_MOVING_GROUP")
            previous_sprites = _require_preview_directory(previous / "sprites2x", "previous sprites2x")
            for name in ["holding_00.png", *[f"moving_{index:02d}.png" for index in range(counts["moving"])]]:
                _bind_preview_file(plan, previous_sprites / name, Path(f"previous/sprites2x/{name}"),
                                   "previous motion sprite")
        if previous is not None:
            pass
        plan["groups"] = groups
        plan["output_names"] = _expected_motion_preview_outputs(groups, previous is not None)
        plan["stage_output"] = Path("study") / "preview"
        plan["stage_args"] = [Path("study"), "--reference", Path("reference") / "body"]
        if previous is not None:
            plan["stage_args"].extend(["--previous", Path("previous")])
    elif command_id == "creature-art.study_preview":
        manifest_path = _require_preview_file(study / "manifest.json", "study manifest")
        _read_preview_json(manifest_path, "study manifest")
        _bind_preview_file(plan, manifest_path, Path("study/manifest.json"), "study manifest")
        for name, original in STUDY_PREVIEW_FRAMES.items():
            _bind_preview_file(plan, study / f"{name}.png", Path(f"study/{name}.png"), f"study pose {name}")
            _bind_preview_file(plan, study / "before" / f"{name}.png", Path(f"study/before/{name}.png"),
                               f"previous pose {name}")
            _bind_preview_file(plan, reference / original, Path(f"reference/{original}"), f"reference pose {name}")
        for side in ("right", "left"):
            name = f"{side}-hand.png"
            _bind_preview_file(plan, study / name, Path(f"study/{name}"), f"{side} hand")
        plan["output_names"] = {"comparison.png", "native-size.png", "hands.png"}
        plan["stage_output"] = Path("study")
        plan["stage_args"] = [Path("study"), "--reference", Path("reference")]
    else:
        raise ProviderError(f"PREVIEW_ADAPTER_UNKNOWN: {command_id}")
    plan["input_stage_files"] = {item["relative"].as_posix() for item in plan["files"]}
    plan["expected_stage_files"] = set(plan["input_stage_files"])
    prefix = plan["stage_output"].as_posix() + "/"
    plan["expected_stage_files"].update(prefix + name for name in plan["output_names"])
    if len(plan["expected_stage_files"]) != len(plan["files"]) + len(plan["output_names"]):
        raise ProviderError("PREVIEW_INPUT_OUTPUT_PATH_COLLISION")
    return plan


def _collect_preview_stage_files(root: Path) -> set[str]:
    _assert_no_reparse_components(root, "preview stage")
    root = root.resolve(strict=True)
    files = set()
    pending = [root]
    entries_seen = 0
    while pending:
        directory = pending.pop()
        try:
            children = list(os.scandir(directory))
        except OSError as error:
            raise ProviderError(f"PREVIEW_STAGE_READ_FAILED: {directory}: {error}") from error
        for child in children:
            entries_seen += 1
            if entries_seen > MAX_PREVIEW_INPUT_FILES * 2:
                raise ProviderError("PREVIEW_STAGE_ENTRY_COUNT_LIMIT_EXCEEDED")
            path = Path(child.path)
            if _is_reparse_point(path):
                raise ProviderError(f"REPARSE_PATH_FORBIDDEN: preview stage={path}")
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(path.relative_to(root).as_posix())
            else:
                raise ProviderError(f"PREVIEW_STAGE_ENTRY_TYPE_UNSUPPORTED: {path}")
    return files


def _verify_preview_inputs_unchanged(plan: dict) -> None:
    for item in plan["files"]:
        path = item["source"]
        _assert_no_reparse_components(path, item["label"])
        try:
            if not path.is_file() or _sha256(path) != item["sha256"]:
                raise ProviderError(f"PREVIEW_INPUT_CHANGED_DURING_RUN: {item['label']}={path}")
        except OSError as error:
            raise ProviderError(f"PREVIEW_INPUT_UNAVAILABLE_AFTER_RUN: {item['label']}={path}: {error}") from error


def _run_isolated_preview(command_id: str, plan: dict, script: Path, program: str,
                          work_root: Path, timeout_seconds: float,
                          runner: Callable = subprocess.run) -> int:
    work_root = work_root.expanduser().resolve(strict=False)
    _assert_no_reparse_components(work_root, "--work-root")
    work_root.mkdir(parents=True, exist_ok=True)
    stage_root = work_root / ".preview-stage"
    publish_root = work_root / ".preview-publish"
    output = plan["output"]
    if stage_root.exists() or publish_root.exists() or output.exists():
        raise ProviderError("PREVIEW_WORK_ROOT_NOT_FRESH: inspect prior stage/output before retrying")
    stage_root.mkdir()
    _assert_no_reparse_components(stage_root, "preview stage")
    for item in plan["files"]:
        destination = stage_root / item["relative"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_reparse_components(destination.parent, "preview stage destination")
        shutil.copyfile(item["source"], destination)
        if _sha256(destination) != item["sha256"]:
            raise ProviderError(f"PREVIEW_STAGE_HASH_MISMATCH: {item['label']}")
    if _collect_preview_stage_files(stage_root) != plan["input_stage_files"]:
        raise ProviderError("PREVIEW_STAGE_INPUT_SET_MISMATCH")
    stage_args = []
    for argument in plan["stage_args"]:
        stage_args.append(argument if isinstance(argument, str) else str(stage_root / argument))
    argv = [program, str(script), *stage_args]
    child_env = os.environ.copy()
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = runner(argv, cwd=str(work_root), env=child_env, shell=False, check=False,
                      timeout=timeout_seconds, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as error:
        raise ProviderError(
            f"PREVIEW_FAILED_TIMEOUT: {command_id} exceeded {timeout_seconds:g}s; no retry was attempted; "
            "the private stage is retained for inspection"
        ) from error
    except OSError as error:
        raise ProviderError(f"PREVIEW_PROCESS_START_FAILED: {command_id}: {error}") from error
    _verify_preview_inputs_unchanged(plan)
    stage_output = stage_root / plan["stage_output"]
    stdout = (proc.stdout or "").replace(str(stage_output), str(output))
    if stdout:
        sys.stdout.write(stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if int(proc.returncode):
        return int(proc.returncode)
    if _collect_preview_stage_files(stage_root) != plan["expected_stage_files"]:
        raise ProviderError("PREVIEW_OUTPUT_SET_MISMATCH: upstream created or omitted files outside its whitelist")
    for name in plan["output_names"]:
        path = stage_output / name
        _assert_no_reparse_components(path, name)
        try:
            metadata = path.stat()
        except OSError as error:
            raise ProviderError(f"PREVIEW_OUTPUT_MISSING: {name}: {error}") from error
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise ProviderError(f"PREVIEW_OUTPUT_INVALID: {name}")
    publish_root.mkdir()
    for name in sorted(plan["output_names"]):
        shutil.copyfile(stage_output / name, publish_root / name)
    if _collect_preview_stage_files(publish_root) != plan["output_names"]:
        raise ProviderError("PREVIEW_PUBLISH_SET_MISMATCH")
    _verify_preview_inputs_unchanged(plan)
    shutil.rmtree(stage_root)
    _assert_no_reparse_components(output, "--out")
    if output.exists():
        raise ProviderError(f"OUTPUT_ALREADY_EXISTS: {output}")
    try:
        os.rename(publish_root, output)
    except OSError as error:
        raise ProviderError(f"PREVIEW_PUBLISH_FAILED: {output}: {error}") from error
    if _collect_preview_stage_files(output) != plan["output_names"]:
        raise ProviderError("PREVIEW_FINAL_OUTPUT_SET_MISMATCH")
    if str(stage_output) not in (proc.stdout or ""):
        target = output / "index.html" if command_id == "creature-art.motion_preview" else output
        sys.stdout.write(f"Preview output: {target}\n")
    return 0


def _resolve_program(value: str | None, label: str, fallback: str | None = None) -> str:
    selected = value or fallback
    if not selected:
        raise ProviderError(f"{label.upper()}_REQUIRED: pass --{label}")
    resolved = shutil.which(selected)
    if resolved:
        return str(Path(resolved).resolve())
    path = Path(selected).expanduser()
    if path.is_file():
        return str(path.resolve())
    raise ProviderError(f"{label.upper()}_NOT_FOUND: {selected}")


def _probe_modules(program: str, runtime: str, modules: Iterable[str],
                   runner: Callable = subprocess.run) -> None:
    modules = list(modules)
    if not modules:
        return
    source = "import importlib.util,sys; missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; print('MISSING_MODULES:'+','.join(missing) if missing else 'DEPENDENCIES_OK'); sys.exit(bool(missing))"
    if runtime == "python":
        argv = [program, "-c", source, *modules]
    else:
        expression = "import importlib.util,sys; missing=[m for m in " + repr(modules) + " if importlib.util.find_spec(m) is None]; print('MISSING_MODULES:'+','.join(missing) if missing else 'DEPENDENCIES_OK'); sys.exit(bool(missing))"
        argv = [program, "--background", "--factory-startup", "--python-expr", expression]
    try:
        proc = runner(argv, shell=False, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=DEPENDENCY_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ProviderError(f"DEPENDENCY_PROBE_FAILED_TIMEOUT: exceeded {DEPENDENCY_PROBE_TIMEOUT_SECONDS}s") from error
    if proc.returncode:
        detail = (proc.stdout or "") + (proc.stderr or "")
        missing = detail.strip()[-1200:] or f"dependency probe exited {proc.returncode}"
        raise ProviderError(f"DEPENDENCY_CHECK_FAILED: {missing}")


def execute(command_id: str, script_arguments: list[str], root: Path, *,
            python: str | None = None, blender: str | None = None,
            work_root: str | Path | None = None,
            timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
            runner: Callable = subprocess.run) -> int:
    verify_source_tree(root)
    command = RUNNABLE.get(command_id)
    if command is None:
        catalog_by_id = {item["id"]: item for item in discover_catalog(root)}
        command = catalog_by_id.get(command_id)
        if command is None:
            raise ProviderError(f"UNKNOWN_PROVIDER_COMMAND: {command_id}")
        raise ProviderError(f"COMMAND_NOT_ADAPTED: {command_id}: {command.get('adapter_gap', 'no audited adapter')}")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 24 * 60 * 60:
        raise ProviderError("TIMEOUT_SECONDS_INVALID: pass a value between 0 and 86400")
    runtime = RUNNABLE[command_id]["runtime"]
    program = (_resolve_program(python, "python", sys.executable) if runtime == "python"
               else _resolve_program(blender, "blender"))
    prefix = RUNNABLE[command_id].get("prefix", [])
    child_arguments = normalize_path_flag_arguments(RUNNABLE[command_id], [*prefix, *script_arguments])
    write_flags = RUNNABLE[command_id].get("write_flags", [])
    selected_work_root = None
    if write_flags:
        if work_root is None:
            raise ProviderError("WORK_ROOT_REQUIRED: pass --work-root under build/experiments/art-pipeline")
        selected_work_root = Path(work_root).expanduser()
        if not selected_work_root.is_absolute():
            raise ProviderError("WORK_ROOT_MUST_BE_ABSOLUTE")
        validate_execution_paths(RUNNABLE[command_id], child_arguments, selected_work_root)
    else:
        validate_input_contract(RUNNABLE[command_id], child_arguments)
    for tool in RUNNABLE[command_id].get("external_tools", []):
        if not shutil.which(tool):
            raise ProviderError(f"EXTERNAL_TOOL_REQUIRED: {tool} (install it and put it on PATH)")
    _probe_modules(program, runtime, RUNNABLE[command_id].get("python_modules", []), runner)
    script = root / UPSTREAM_DIR_NAME / Path(RUNNABLE[command_id]["path"])
    lock = load_lock(root)
    locked_entry = next((entry for entry in lock["files"]
                         if entry["path"] == RUNNABLE[command_id]["path"]), None)
    if locked_entry is None or _sha256(script) != locked_entry["sha256"]:
        raise ProviderError(f"EXECUTABLE_SOURCE_NOT_LOCKED: {RUNNABLE[command_id]['path']}")
    if RUNNABLE[command_id].get("adapter"):
        if selected_work_root is None:
            raise ProviderError("WORK_ROOT_REQUIRED: pass --work-root under build/experiments/art-pipeline")
        plan = _prepare_preview_plan(command_id, RUNNABLE[command_id], child_arguments)
        return _run_isolated_preview(command_id, plan, script, program, selected_work_root,
                                     timeout_seconds, runner)
    cwd = None
    if write_flags:
        selected_work_root.mkdir(parents=True, exist_ok=True)
        cwd = str(selected_work_root.resolve())
    if runtime == "blender":
        argv = [program, "--background", "--python-exit-code", "1",
                "--python-expr", "import sys; sys.dont_write_bytecode = True",
                "--python", str(script), "--", *child_arguments]
    else:
        argv = [program, str(script), *child_arguments]
    child_env = os.environ.copy()
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = runner(argv, cwd=cwd, env=child_env, shell=False, check=False,
                      timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise ProviderError(
            f"RUN_FAILED_TIMEOUT: {command_id} exceeded {timeout_seconds:g}s; no retry was attempted; "
            "standalone Python timeout only terminates the direct child, so process-tree cleanup is not "
            "guaranteed here (the packaged EXE uses a Job Object for its own process tree)"
        ) from error
    return int(proc.returncode)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    catalog_parser = sub.add_parser("catalog", help="list locked upstream operations and adapter status")
    catalog_parser.add_argument("--provider-root", type=Path)
    catalog_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    run_parser = sub.add_parser("run", help="run one audited operation; separate child args with --")
    run_parser.add_argument("command_id")
    run_parser.add_argument("--provider-root", type=Path)
    run_parser.add_argument("--python", help="Python executable for Python-provider commands")
    run_parser.add_argument("--blender", help="Blender executable for Blender-provider commands")
    run_parser.add_argument("--work-root", type=Path, help="new output paths must remain under this development directory")
    run_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_RUN_TIMEOUT_SECONDS,
                            help=f"child process deadline in seconds (default {DEFAULT_RUN_TIMEOUT_SECONDS}; max 86400)")
    return parser


def main(argv: list[str] | None = None, *, runner: Callable = subprocess.run) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        _parser().print_help()
        return 2
    if raw[0] == "run":
        try:
            first_separator = raw.index("--")
        except ValueError:
            first_separator = len(raw)
        if any(token in {"--help", "-h"} for token in raw[:first_separator]):
            _parser().parse_args(raw[:first_separator])
            return 0
        separator = first_separator
        control, child = raw[:separator], raw[separator + 1:] if separator < len(raw) else []
        if not child:
            print("CHILD_ARGUMENTS_REQUIRED: put provider script args after --", file=sys.stderr)
            return 2
        args = _parser().parse_args(control)
        try:
            root = provider_root(args.provider_root)
            return execute(args.command_id, child, root, python=args.python, blender=args.blender,
                           work_root=args.work_root, timeout_seconds=args.timeout_seconds, runner=runner)
        except ProviderError as error:
            print(str(error), file=sys.stderr)
            return 2
    args = _parser().parse_args(raw)
    try:
        root = provider_root(args.provider_root)
        entries, integrity = _catalog_with_integrity(root)
    except ProviderError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"schema": "h3tierexport.art-pipeline-catalog.v1",
                          "provider": "yzh119/h3-art-pipeline", "integrity": integrity,
                          "commands": entries}, ensure_ascii=False, indent=2))
    else:
        print(f"yzh119/h3-art-pipeline @ {integrity['commit']} ({integrity['license']})")
        print(f"locked files: {integrity['actual_files']}; catalogue entries: {len(entries)}")
        for entry in entries:
            print(f"{entry['status']:18} {entry['id']} [{entry['runtime']}] — {entry['summary'][:150]}")
            if entry["status"] != "run":
                print(f"  {entry['adapter_gap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
