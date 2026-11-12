#!/usr/bin/env python3
"""Blender-side worker for blender_action_pipeline.py.

This file is launched by Blender's background Python runner. The only
production modes are --check and --run; --self-test builds an explicitly
synthetic two-Action rig for integration testing and never claims a creature
asset passed.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sys
# Blender ignores Python environment flags; keep frozen sibling imports read-only.
sys.dont_write_bytecode = True
import time
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import blender_action_pipeline as pipeline


class WorkerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkerError(code, message)


def _json_result(document: dict[str, Any]) -> None:
    print("H3_ACTION_PIPELINE_JSON=" + json.dumps(document, ensure_ascii=False, sort_keys=True))


def _blender() -> Any:
    try:
        import bpy
    except ImportError as exc:  # pragma: no cover - only runs inside Blender
        raise WorkerError("BLENDER_PYTHON_REQUIRED", "worker must run inside Blender") from exc
    return bpy


def _vector(value: list[float]) -> tuple[float, ...]:
    return tuple(float(item) for item in value)


def _radians(value: list[float]) -> tuple[float, ...]:
    return tuple(math.radians(float(item)) for item in value)


def _load_model(bpy: Any, model_path: Path) -> list[Any]:
    scene = bpy.context.scene
    before = {obj.as_pointer() for obj in scene.objects}
    suffix = model_path.suffix.lower()
    try:
        if suffix in {".glb", ".gltf"}:
            if not hasattr(bpy.ops.import_scene, "gltf"):
                raise WorkerError("GLTF_IMPORTER_UNAVAILABLE", "this Blender build has no glTF importer")
            result = bpy.ops.import_scene.gltf(filepath=str(model_path))
        elif suffix == ".fbx":
            if not hasattr(bpy.ops.import_scene, "fbx"):
                raise WorkerError("FBX_IMPORTER_UNAVAILABLE", "this Blender build has no FBX importer")
            result = bpy.ops.import_scene.fbx(filepath=str(model_path))
        else:
            raise WorkerError("MODEL_FORMAT_UNSUPPORTED", f"unsupported rigged model suffix: {suffix}")
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError("MODEL_IMPORT_FAILED", f"Blender could not import {model_path}: {exc}") from exc
    _require("FINISHED" in result, "MODEL_IMPORT_FAILED", f"Blender importer did not finish: {model_path}")
    imported = [obj for obj in scene.objects if obj.as_pointer() not in before]
    _require(bool(imported), "MODEL_IMPORT_EMPTY", "model import produced no scene objects")
    return imported


def _check_external_images(bpy: Any, config: dict[str, Any], imported: list[Any]) -> None:
    pinned = {str(Path(ref["path"]).resolve(strict=True)) for ref in config["inputs"]}
    imported_ptrs = {obj.as_pointer() for obj in imported}
    for image in bpy.data.images:
        if image.source != "FILE" or not getattr(image, "filepath", ""):
            continue
        if getattr(image, "packed_file", None) is not None:
            continue
        try:
            path = Path(bpy.path.abspath(image.filepath)).resolve(strict=True)
        except OSError as exc:
            raise WorkerError("MODEL_IMAGE_UNAVAILABLE", f"image dependency is missing: {image.filepath}") from exc
        if str(path) not in pinned:
            raise WorkerError("MODEL_DEPENDENCY_NOT_PINNED",
                              f"imported image is absent from model.dependencies: {path}")
    # Keep the local reference to make it explicit that only images reached by
    # this import are considered; no image or material is rewritten here.
    _ = imported_ptrs


def _prepare_explicit_action_source(bpy: Any, imported: list[Any], config: dict[str, Any]) -> dict[str, Any]:
    """Accept only deterministic importer NLA carriers, then clear them.

    Blender's glTF importer stores each exported Action in one full-range NLA
    strip even when the source file contains explicit Actions.  Those strips
    are an import carrier, not the source selected by this worker.  We accept
    that bounded shape only when every strip is a one-to-one, full-range,
    REPLACE/HOLD strip for a manifest-bound Action.  Arbitrary NLA blending,
    duplicate strips, non-armature strips, and unbound Actions remain a hard
    ambiguity refusal.
    """
    expected_names = {group["action_name"] for group in config["groups"]}
    carriers: list[dict[str, Any]] = []
    for obj in imported:
        animation_data = getattr(obj, "animation_data", None)
        tracks = list(getattr(animation_data, "nla_tracks", ())) if animation_data else []
        nonempty = [track for track in tracks if len(track.strips)]
        if not nonempty:
            continue
        _require(obj.type == "ARMATURE", "NLA_TRACKS_AMBIGUOUS",
                 f"NLA strips on non-armature object are not an explicit Action source: {obj.name}")
        seen: set[str] = set()
        for track in nonempty:
            _require(len(track.strips) == 1, "NLA_TRACKS_AMBIGUOUS",
                     f"armature track {track.name!r} contains multiple strips")
            strip = track.strips[0]
            action = getattr(strip, "action", None)
            action_name = str(getattr(action, "name", ""))
            _require(action is not None and action_name in expected_names,
                     "NLA_TRACKS_AMBIGUOUS",
                     f"NLA strip {track.name!r} is not bound to a manifest Action")
            _require(action_name not in seen, "NLA_TRACKS_AMBIGUOUS",
                     f"manifest Action {action_name!r} appears in multiple NLA strips")
            action_start, action_end = (float(action.frame_range[0]), float(action.frame_range[1]))
            _require(abs(float(strip.action_frame_start) - action_start) <= 1e-6
                     and abs(float(strip.action_frame_end) - action_end) <= 1e-6
                     and abs(float(strip.frame_start) - action_start) <= 1e-6
                     and abs(float(strip.frame_end) - action_end) <= 1e-6
                     and str(strip.blend_type) == "REPLACE"
                     and str(strip.extrapolation) == "HOLD",
                     "NLA_TRACKS_AMBIGUOUS",
                     f"NLA strip {track.name!r} is not a full-range REPLACE/HOLD importer carrier")
            seen.add(action_name)
            carriers.append({"object": obj.name, "track": track.name, "action": action_name})
        # The following explicit _bind_action calls become the sole source of
        # sampled animation.  Removing importer carrier strips preserves the
        # Actions and prevents hidden NLA blending from affecting evaluation.
        for track in nonempty:
            animation_data.nla_tracks.remove(track)
    return {"cleared_importer_nla": bool(carriers), "carriers": carriers}


def _resolve_action_slot(action: Any, requested: str | None, *, group_id: int) -> Any | None:
    slots = list(getattr(action, "slots", ()))
    if not slots:
        _require(requested is None, "ACTION_SLOT_UNAVAILABLE",
                 f"group {group_id} requested an Action slot but Action has no slots")
        return None
    if requested is None:
        _require(len(slots) == 1, "ACTION_SLOT_AMBIGUOUS",
                 f"Action {action.name!r} has {len(slots)} slots; manifest must select one explicitly")
        return slots[0]
    matches = [slot for slot in slots if requested in {
        str(getattr(slot, "identifier", "")), str(getattr(slot, "name", ""))}]
    _require(len(matches) == 1, "ACTION_SLOT_NOT_FOUND",
             f"Action {action.name!r} has no unique slot {requested!r}")
    return matches[0]


def _bind_action(bpy: Any, armature: Any, binding: dict[str, Any]) -> tuple[Any, Any | None]:
    action = bpy.data.actions.get(binding["action_name"])
    _require(action is not None, "ACTION_NOT_FOUND",
             f"native group {binding['native_group']} Action not found: {binding['action_name']!r}")
    slot = _resolve_action_slot(action, binding.get("action_slot"), group_id=binding["native_group"])
    animation_data = armature.animation_data_create()
    animation_data.action = action
    if slot is not None:
        _require(hasattr(animation_data, "action_slot"), "ACTION_SLOT_API_UNAVAILABLE",
                 "Blender runtime cannot bind the selected Action slot")
        animation_data.action_slot = slot
    return action, slot


def _validate_action_range(action: Any, binding: dict[str, Any]) -> None:
    start, end = (float(action.frame_range[0]), float(action.frame_range[1]))
    for slot in binding["slots"]:
        position = float(slot["action_frame"]) + float(slot["subframe"])
        _require(start - 1e-9 <= position <= end + 1e-9, "ACTION_SAMPLE_OUT_OF_RANGE",
                 f"group {binding['native_group']} slot {slot['native_frame']} samples {position:g}, "
                 f"outside Action {action.name!r} range [{start:g}, {end:g}]")


def _sample_action(bpy: Any, scene: Any, armature: Any, binding: dict[str, Any],
                   frame: int, subframe: float) -> tuple[Any, Any | None]:
    action, slot = _bind_action(bpy, armature, binding)
    scene.frame_set(frame, subframe=subframe)
    bpy.context.view_layer.update()
    return action, slot


def _preflight_scene(bpy: Any, config: dict[str, Any]) -> tuple[list[Any], Any, dict[int, tuple[Any, Any | None]]]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imported = _load_model(bpy, Path(config["model"]["path"]))
    _check_external_images(bpy, config, imported)
    armatures = [obj for obj in imported if obj.type == "ARMATURE"
                 and obj.name == config["armature_object"]]
    _require(len(armatures) == 1, "ARMATURE_NOT_UNIQUE",
             f"model must contain exactly one armature named {config['armature_object']!r}")
    armature = armatures[0]
    importer_nla = _prepare_explicit_action_source(bpy, imported, config)
    other_actions = [obj.name for obj in imported if obj is not armature
                     and getattr(getattr(obj, "animation_data", None), "action", None) is not None]
    _require(not other_actions, "NON_ARMATURE_ACTION_AMBIGUOUS",
             "only the explicit armature Action is supported; animated objects: "
             + ", ".join(other_actions[:12]))
    foot_bone = armature.pose.bones.get(config["footpoint"]["bone_name"])
    _require(foot_bone is not None, "FOOTPOINT_BONE_NOT_FOUND",
             f"footpoint bone not found: {config['footpoint']['bone_name']!r}")
    actions: dict[int, tuple[Any, Any | None]] = {}
    for binding in config["groups"]:
        action, slot = _bind_action(bpy, armature, binding)
        _validate_action_range(action, binding)
        actions[binding["native_group"]] = (action, slot)
    # Restore a deterministic initial evaluated state after inspecting Actions.
    first_binding = config["groups"][0]
    _bind_action(bpy, armature, first_binding)
    first_slot = first_binding["slots"][0]
    bpy.context.scene.frame_set(first_slot["action_frame"], subframe=first_slot["subframe"])
    bpy.context.view_layer.update()
    return imported, armature, actions


def _create_root(bpy: Any, imported: list[Any], config: dict[str, Any]) -> Any:
    scene = bpy.context.scene
    root = bpy.data.objects.new("H3ActionModelRoot", None)
    scene.collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.25
    for obj in imported:
        if obj.parent is not None:
            continue
        world = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_world = world
    transform = config["model_transform"]
    root.location = _vector(transform["location"])
    rotation = list(transform["rotation_euler_degrees"])
    rotation[2] += transform["facing_yaw_degrees"]
    root.rotation_mode = "XYZ"
    root.rotation_euler = _radians(rotation)
    root.scale = _vector(transform["scale"])
    for obj in imported:
        if obj.type in {"CAMERA", "LIGHT"}:
            obj.hide_render = True
    return root


def _configure_render(bpy: Any, config: dict[str, Any], canvas: list[int]) -> tuple[Any, Any, list[Any]]:
    scene = bpy.context.scene
    camera_data = bpy.data.cameras.new("H3ActionCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = config["camera"]["orthographic_scale"]
    camera_data.shift_x = config["camera"]["shift_x"]
    camera_data.shift_y = config["camera"]["shift_y"]
    camera_data.clip_start = config["camera"]["clip_start"]
    camera_data.clip_end = config["camera"]["clip_end"]
    camera_obj = bpy.data.objects.new("H3ActionCamera", camera_data)
    scene.collection.objects.link(camera_obj)
    camera_obj.location = _vector(config["camera"]["location"])
    camera_obj.rotation_mode = "XYZ"
    camera_obj.rotation_euler = _radians(config["camera"]["rotation_euler_degrees"])
    scene.camera = camera_obj

    scene.render.resolution_x, scene.render.resolution_y = canvas
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1
    scene.render.pixel_aspect_y = 1
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = config["render"]["color_depth"]
    scene.render.engine = config["render"]["engine"]
    scene.view_settings.view_transform = config["render"]["view_transform"]
    scene.view_settings.look = config["render"]["look"]
    if scene.render.engine == "CYCLES":
        scene.cycles.device = "CPU"
        scene.cycles.samples = config["render"]["samples"]
        scene.cycles.use_denoising = False

    world = bpy.data.worlds.new("H3ActionWorld")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs[0].default_value = (*config["world"]["color"], 1.0)
    background.inputs[1].default_value = config["world"]["strength"]
    scene.world = world
    lights = []
    for spec in config["lights"]:
        light_data = bpy.data.lights.new(spec["name"], type=spec["type"])
        light_data.energy = spec["energy"]
        if spec["type"] == "AREA":
            light_data.shape = "DISK"
            light_data.size = spec["size"]
        elif spec["type"] == "SUN":
            light_data.angle = math.radians(spec["angle_degrees"])
        else:
            light_data.shadow_soft_size = spec["radius"]
        light_obj = bpy.data.objects.new(spec["name"], light_data)
        scene.collection.objects.link(light_obj)
        light_obj.location = _vector(spec["location"])
        light_obj.rotation_mode = "XYZ"
        light_obj.rotation_euler = _radians(spec["rotation_euler_degrees"])
        lights.append(light_obj)
    return scene, camera_obj, lights


def _make_shadow_catcher(bpy: Any, ground_z: float, size: float = 200.0) -> Any:
    _require(bpy.context.scene.render.engine == "CYCLES", "SHADOW_RENDER_ENGINE_UNSUPPORTED",
             "a transparent shadow-catcher pass requires the explicitly selected CYCLES engine")
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0.0, 0.0, ground_z))
    plane = bpy.context.object
    plane.name = "H3ActionShadowCatcher"
    plane.is_shadow_catcher = True
    return plane


def _camera_pixel(bpy: Any, camera: Any, scene: Any, world_position: Any) -> list[float]:
    from bpy_extras.object_utils import world_to_camera_view
    projected = world_to_camera_view(scene, camera, world_position)
    return [float(projected.x * scene.render.resolution_x),
            float((1.0 - projected.y) * scene.render.resolution_y)]


def _footpoint_position(armature: Any, spec: dict[str, Any], depsgraph: Any = None) -> Any:
    from mathutils import Vector
    if depsgraph is not None:
        armature=armature.evaluated_get(depsgraph)
    bone = armature.pose.bones.get(spec["bone_name"])
    _require(bone is not None, "FOOTPOINT_BONE_NOT_FOUND", f"footpoint bone disappeared: {spec['bone_name']}")
    return armature.matrix_world @ bone.matrix @ Vector(spec["bone_local_position"])


def _analyze_png(bpy: Any, path: Path, *, max_pixels: int = 16_000_000) -> dict[str, Any]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = int(image.size[0]), int(image.size[1])
        _require(width > 0 and height > 0 and width * height <= max_pixels,
                 "OUTPUT_PIXEL_LIMIT", f"rendered image pixel count {width * height} exceeds {max_pixels}")
        values = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(values)
        left, top, right, bottom = width, height, -1, -1
        count = 0
        try:
            import numpy as np
        except ImportError:  # Blender builds may omit NumPy; keep a bounded exact fallback.
            y_indices = range(height)
            for y_bottom in y_indices:
                top_y = height - 1 - y_bottom
                row = y_bottom * width * 4
                for x in range(width):
                    if values[row + x * 4 + 3] > 0.0:
                        count += 1
                        if x < left: left = x
                        if x + 1 > right: right = x + 1
                        if top_y < top: top = top_y
                        if top_y + 1 > bottom: bottom = top_y + 1
        else:
            alpha = np.frombuffer(values, dtype=np.float32)[3::4].reshape((height, width))
            ys, xs = np.nonzero(alpha > 0.0)
            count = int(xs.size)
            if count:
                left, right = int(xs.min()), int(xs.max()) + 1
                top = height - 1 - int(ys.max())
                bottom = height - int(ys.min())
        return {"width": width, "height": height,
                "alpha_bounds": None if count == 0 else [left, top, right, bottom],
                "nonzero_alpha_pixels": count,
                "sha256": pipeline.sha256_file(path)}
    finally:
        bpy.data.images.remove(image)


def _validate_alpha_inside_crop(analysis: dict[str, Any], slot: dict[str, Any], layer: str) -> None:
    bounds = analysis["alpha_bounds"]
    if layer == "shadow" and bounds is None:
        return
    _require(bounds is not None, "RENDER_LAYER_EMPTY", f"rendered {layer} layer is empty")
    ax, ay = slot["anchor"]
    cw, ch = slot["crop"]
    left, top, right, bottom = bounds
    _require(left >= ax and top >= ay and right <= ax + cw and bottom <= ay + ch,
             "RENDER_CROP_OVERFLOW",
             f"{layer} nonzero alpha bbox {bounds} exceeds fixed crop {[ax, ay, ax + cw, ay + ch]}")


def _render_still(bpy: Any, scene: Any, path: Path, output_guard: Any) -> None:
    output_guard.verify_target(path, must_exist=False)
    scene.render.filepath = str(path)
    try:
        result = bpy.ops.render.render(write_still=True)
    except Exception as exc:
        raise WorkerError("BLENDER_RENDER_FAILED", f"render failed for {path.name}: {exc}") from exc
    output_guard.verify_target(path, must_exist=True)
    _require("FINISHED" in result and path.is_file(), "BLENDER_RENDER_FAILED",
             f"render did not produce {path.name}")


def _render_sample(bpy: Any, scene: Any, camera: Any, lights: list[Any], meshes: list[Any],
                   armature: Any, plane: Any, binding: dict[str, Any], slot: dict[str, Any],
                   output_dir: Path, config: dict[str, Any], output_guard: Any) -> dict[str, Any]:
    action, action_slot = _sample_action(bpy, scene, armature, binding,
                                         slot["action_frame"], slot["subframe"])
    footpoint = _footpoint_position(armature, config["footpoint"],bpy.context.evaluated_depsgraph_get())
    footpoint_px = _camera_pixel(bpy, camera, scene, footpoint)
    if (binding["native_group"] == config["footpoint"].get("calibration_native_group")
            and slot["native_frame"] == config["footpoint"].get("calibration_native_frame")):
        expected = config["footpoint"]["expected_pixel_1x"]
        error = math.dist([point / 3.0 for point in footpoint_px], expected)
        _require(error <= config["footpoint"]["tolerance_px"], "FOOTPOINT_CALIBRATION_MISMATCH",
                 f"calibration footpoint differs by {error:.3f}px, tolerance "
                 f"{config['footpoint']['tolerance_px']:.3f}px")

    stem = f"g{binding['native_group']:02d}_f{slot['native_frame']:04d}"
    paths = {name: output_dir / f"{stem}-{name}.png" for name in ("body", "shadow", "overlay")}
    for path in paths.values():
        output_guard.verify_target(path, must_exist=False)

    plane.hide_render = True
    _render_still(bpy, scene, paths["body"], output_guard)
    plane.hide_render = False
    for obj in meshes:
        obj.visible_camera = False
    _render_still(bpy, scene, paths["shadow"], output_guard)
    for obj in meshes:
        obj.visible_camera = True
    plane.hide_render = True

    material = bpy.data.materials.new("H3ActionOverlayWhite")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    emission.inputs[1].default_value = 1.0
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emission.outputs[0], output.inputs[0])
    view_layer = scene.view_layers[0]
    old_override = view_layer.material_override
    view_layer.material_override = material
    light_states = [(light, light.hide_render) for light in lights]
    for light in lights:
        light.hide_render = True
    try:
        _render_still(bpy, scene, paths["overlay"], output_guard)
    finally:
        view_layer.material_override = old_override
        for light, state in light_states:
            light.hide_render = state
        bpy.data.materials.remove(material, do_unlink=True)

    dimensions = config["dimensions"]["canvas_3x"]
    layer_receipts = {}
    for name, path in paths.items():
        output_guard.verify_target(path, must_exist=True)
        analysis = _analyze_png(bpy, path)
        _require([analysis["width"], analysis["height"]] == dimensions,
                 "OUTPUT_CANVAS_MISMATCH", f"{name} render dimensions differ from fixed native canvas")
        _validate_alpha_inside_crop(analysis, slot, name)
        layer_receipts[name] = {"path": path.name, **analysis}
    return {"native_group": binding["native_group"], "native_frame": slot["native_frame"],
            "native_key": slot["native_key"], "action_name": action.name,
            "action_slot": (getattr(action_slot, "identifier", None) if action_slot else None),
            "action_frame": slot["action_frame"], "subframe": slot["subframe"],
            "frame_duration_ms": binding["frame_duration_ms"], "loop": binding["loop"],
            "time_provenance": binding["time_provenance"], "time_locator": binding["time_locator"],
            "time_ref": slot["time_ref"],
            "footpoint_canvas_px_3x": footpoint_px, "layers": layer_receipts}


def _motion_consistency_probe(bpy, scene, camera, armature, config):
    import blender_motion_consistency as consistency
    from mathutils import Vector
    contract=config.get('motion_consistency')
    if contract is None:
        return consistency.evaluate(None,config['groups'],lambda row:None)
    bindings={g['native_group']:g for g in config['groups']}
    def observed(row):
        _sample_action(bpy,scene,armature,bindings[row['native_group']],row['action_frame'],row['subframe'])
        evaluated_rig=armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
        def point(bone,local):
            target=evaluated_rig.pose.bones.get(bone)
            _require(target is not None,'MOTION_MEASUREMENT_BONE_MISSING',bone)
            return evaluated_rig.matrix_world @ target.matrix @ Vector(local)
        def segment_length(metric):
            if metric.get('space','bone_local')=='bone_local':
                return (point(metric['bone'],metric['b'])-point(metric['bone'],metric['a'])).length
            obj=bpy.data.objects.get(metric['object'])
            _require(obj is not None and obj.type=='MESH','MOTION_MEASUREMENT_MESH_MISSING',metric['object'])
            evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            vertices=evaluated.data.vertices
            _require(max(metric['vertices'])<len(vertices),'MOTION_MEASUREMENT_VERTEX_MISSING',metric['id'])
            a,b=[evaluated.matrix_world @ vertices[i].co for i in metric['vertices']]
            return (b-a).length
        lengths={m['id']:segment_length(m) for m in contract['rigid_segments']}
        p=point(contract['root']['bone'],contract['root']['point'])
        projected=_camera_pixel(bpy,camera,scene,p)
        snapshot=[float(v) for matrix_row in camera.matrix_world for v in matrix_row]
        snapshot += [float(camera.data.ortho_scale),float(camera.data.shift_x),float(camera.data.shift_y),
                     float(scene.render.resolution_x),float(scene.render.resolution_y),
                     float(scene.render.resolution_percentage),float(scene.render.pixel_aspect_x),
                     float(scene.render.pixel_aspect_y),float(scene.camera==camera),float(camera.data.type=='ORTHO')]
        return {'lengths':lengths,'root_native':[v/3 for v in projected],'camera':snapshot}
    try:
        return consistency.evaluate(contract,config['groups'],observed)
    except consistency.ConsistencyError as error:
        raise WorkerError(error.code,str(error)) from error


def _check_mode(bpy: Any, manifest_path: Path, manifest_sha: str,
                outdir: Path, operation_id: str) -> dict[str, Any]:
    manifest, _, actual = pipeline.load_manifest(manifest_path, manifest_sha)
    _require(actual == manifest_sha, "MANIFEST_SHA256_MISMATCH", "manifest digest changed")
    checked = pipeline.check_static(manifest, operation_id, outdir,
                                    manifest_sha=manifest_sha, exists=False)
    config = checked["configuration"]
    _require(bpy.app.version_string == config["blender"]["version"], "BLENDER_VERSION_MISMATCH",
             f"running Blender {bpy.app.version_string}; manifest requires {config['blender']['version']}")
    imported, armature, actions = _preflight_scene(bpy, config)
    motion_consistency={'status':'NOT_MEASURED','reason':'No explicit motion-consistency contract'}
    if config.get('motion_consistency') is not None:
        _create_root(bpy,imported,config)
        scene,camera,_=_configure_render(bpy,config,config['dimensions']['canvas_3x'])
        motion_consistency=_motion_consistency_probe(bpy,scene,camera,armature,config)
    return {"schema": "h3.blender-action-pipeline.scene-check.v1", "status": "PASS",
            "motion_consistency":motion_consistency,
            "operation_id": operation_id, "identity": config["identity"],
            "blender_version": bpy.app.version_string,
            "armature_object": armature.name,
            "groups": [{"native_group": group, "action_name": actions[group][0].name,
                        "action_slot": (getattr(actions[group][1], "identifier", None)
                                        if actions[group][1] else None),
                        "frame_count": len(next(item for item in config["groups"]
                                                 if item["native_group"] == group)["slots"])}
                       for group in sorted(actions)],
            "imported_object_count": len(imported), "outputs_created": False}


def _run_mode(bpy: Any, manifest_path: Path, manifest_sha: str,
              outdir: Path, operation_id: str) -> dict[str, Any]:
    manifest, _, actual = pipeline.load_manifest(manifest_path, manifest_sha)
    _require(actual == manifest_sha, "MANIFEST_SHA256_MISMATCH", "manifest digest changed")
    checked = pipeline.check_static(manifest, operation_id, outdir,
                                    manifest_sha=manifest_sha, exists=True)
    config = checked["configuration"]
    frozen_manifest_path = manifest_path.resolve(strict=True)
    config["inputs"].append({"path": str(frozen_manifest_path), "sha256": manifest_sha})
    output_guard = pipeline.attach_output_root_guard(outdir, pipeline.ACTION_OUTPUT_ROOT)
    try:
        frames_dir = outdir / "frames"
        output_guard.hold_directory(frames_dir)
        for name in ("blender-config", "blender-scripts", "blender-datafiles",
                     "blender-extensions", "blender-temp", "blender-cwd"):
            output_guard.hold_directory(outdir / name)
        _require(bpy.app.version_string == config["blender"]["version"], "BLENDER_VERSION_MISMATCH",
                 f"running Blender {bpy.app.version_string}; manifest requires {config['blender']['version']}")
        _require(not (frames_dir / "worker-receipt.json").exists(), "OUTPUT_ALREADY_EXISTS",
                 "operation already has a worker receipt; inspect with verify and do not rerun")
        _require(not any(frames_dir.iterdir()), "OUTPUT_DIRECTORY_NOT_EMPTY",
                 "parent-precreated frames directory must be empty before the first Action render")
        imported, armature, actions = _preflight_scene(bpy, config)
        meshes = [obj for obj in imported if obj.type == "MESH"]
        _require(bool(meshes), "MODEL_HAS_NO_MESH", "rigged model contains no mesh objects")
        _create_root(bpy, imported, config)
        canvas = config["dimensions"]["canvas_3x"]
        scene, camera, lights = _configure_render(bpy, config, canvas)
        motion_consistency=_motion_consistency_probe(bpy,scene,camera,armature,config)
        plane = _make_shadow_catcher(bpy, config["footpoint"]["ground_plane_z"])
        try:
            frame_receipts = []
            for binding in config["groups"]:
                # Recheck the exact Action object retained by preflight before the group.
                action, slot_obj = actions[binding["native_group"]]
                _require(bpy.data.actions.get(binding["action_name"]) is action,
                         "ACTION_BINDING_CHANGED", f"Action changed after preflight: {binding['action_name']}")
                if binding.get("action_slot") is not None:
                    _require(slot_obj is not None, "ACTION_SLOT_CHANGED", "selected Action slot disappeared")
                for slot in binding["slots"]:
                    frame_receipts.append(_render_sample(
                        bpy, scene, camera, lights, meshes, armature, plane, binding, slot,
                        frames_dir, config, output_guard))
            pipeline._recheck_source_hashes(config)
            receipt = {"schema": pipeline.WORKER_RECEIPT_SCHEMA, "status": "PASS",
                       "motion_consistency":motion_consistency,
                       "operation_id": operation_id, "manifest_sha256": manifest_sha,
                       "identity": config["identity"], "model_sha256": config["model"]["sha256"],
                       "inventory_sha256": config["action_plan_document"]["inventory"]["sha256"],
                       "blender_version": bpy.app.version_string,
                       "armature_object": armature.name,
                       "groups": [{"native_group": item["native_group"], "action_name": item["action_name"],
                                   "action_slot_requested": item["action_slot"],
                                   "resolved_action_slot": (getattr(actions[item["native_group"]][1],
                                                                    "identifier", None)
                                                             if actions[item["native_group"]][1] else None),
                                   "loop": item["loop"],
                                   "frame_duration_ms": item["frame_duration_ms"],
                                   "time_provenance": item["time_provenance"],
                                   "time_locator": item["time_locator"]} for item in config["groups"]],
                       "frames": frame_receipts,
                       "output_count": sum(len(frame["layers"]) for frame in frame_receipts),
                       "game_written": False, "def_written": False,
                       "art_approved": False, "started_unix_ms": int(time.time() * 1000)}
            path = frames_dir / "worker-receipt.json"
            output_guard.verify_target(path, must_exist=False)
            pipeline._write_exclusive(path, pipeline._json_bytes(receipt))
            output_guard.verify_target(path, must_exist=True)
            return {"schema": pipeline.WORKER_RECEIPT_SCHEMA, "status": "PASS",
                    "operation_id": operation_id, "identity": config["identity"],
                    "frame_count": len(frame_receipts), "output_count": receipt["output_count"],
                    "receipt_path": str(path)}
        finally:
            if plane.name in bpy.data.objects:
                bpy.data.objects.remove(plane, do_unlink=True)
    finally:
        output_guard.close()


def _self_test_mode(bpy: Any, outdir: Path) -> dict[str, Any]:
    """Exercise real Action slot assignment, subframe sampling, and RGBA render."""
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    outdir = outdir.resolve(strict=False)
    _require(not outdir.exists(), "OUTPUT_ALREADY_EXISTS", "synthetic self-test output must be new")
    outdir.mkdir(parents=True, exist_ok=False)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 48
    scene.render.resolution_y = 48
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    world = bpy.data.worlds.new("SyntheticWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0
    scene.world = world

    armature_data = bpy.data.armatures.new("SyntheticRig")
    armature = bpy.data.objects.new("SyntheticRig", armature_data)
    scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature_data.edit_bones.new("root")
    bone.head = (0, 0, 0)
    bone.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")

    vertices = [(-0.45, -0.45, 0), (0.45, -0.45, 0), (0.45, 0.45, 0), (-0.45, 0.45, 0),
                (-0.45, -0.45, 0.9), (0.45, -0.45, 0.9), (0.45, 0.45, 0.9), (-0.45, 0.45, 0.9)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2),
             (2, 6, 7, 3), (4, 0, 3, 7)]
    mesh_data = bpy.data.meshes.new("SyntheticMesh")
    mesh_data.from_pydata(vertices, [], faces)
    mesh_obj = bpy.data.objects.new("SyntheticMesh", mesh_data)
    scene.collection.objects.link(mesh_obj)
    mesh_obj.parent = armature
    group = mesh_obj.vertex_groups.new(name="root")
    group.add(list(range(len(vertices))), 1.0, "REPLACE")
    modifier = mesh_obj.modifiers.new("SyntheticSkin", "ARMATURE")
    modifier.object = armature
    material = bpy.data.materials.new("SyntheticMaterial")
    material.diffuse_color = (0.8, 0.2, 0.1, 1.0)
    mesh_obj.data.materials.append(material)

    target_bone = armature.pose.bones["root"]
    target_bone.rotation_mode = "XYZ"
    actions = []
    for action_name, axis, first, second in (
            ("SyntheticWalk", 2, 0.0, 0.8), ("SyntheticAttack", 0, 0.0, -0.9)):
        action = bpy.data.actions.new(action_name)
        slot = None
        if hasattr(action, "slots"):
            slot = action.slots.new(id_type="OBJECT", name="SyntheticRig")
        animation_data = armature.animation_data_create()
        animation_data.action = action
        if slot is not None:
            if not hasattr(animation_data, "action_slot"):
                raise WorkerError("SELF_TEST_ACTION_SLOT_API_MISSING", "Blender lacks Action slot assignment")
            animation_data.action_slot = slot
        for frame, angle in ((1, first), (3, second)):
            rotation = [0.0, 0.0, 0.0]
            rotation[axis] = angle
            target_bone.rotation_euler = rotation
            target_bone.keyframe_insert(data_path="rotation_euler", frame=frame, group="root")
        actions.append((action, slot, axis))

    camera_data = bpy.data.cameras.new("SyntheticCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 3.0
    camera_obj = bpy.data.objects.new("SyntheticCamera", camera_data)
    scene.collection.objects.link(camera_obj)
    camera_obj.location = (3.5, -5.0, 3.5)
    camera_obj.rotation_euler = (Vector((0, 0, 0.45)) - camera_obj.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera_obj
    light_data = bpy.data.lights.new("SyntheticKey", type="AREA")
    light_data.energy = 350
    light_data.size = 4
    light_obj = bpy.data.objects.new("SyntheticKey", light_data)
    scene.collection.objects.link(light_obj)
    light_obj.location = (3, -3, 5)
    light_obj.rotation_euler = (Vector((0, 0, 0.4)) - light_obj.location).to_track_quat("-Z", "Y").to_euler()

    values = []
    outputs = []
    for index, (action, slot, axis) in enumerate(actions):
        binding = {"native_group": index, "action_name": action.name,
                   "action_slot": (getattr(slot, "identifier", None) if slot else None)}
        _sample_action(bpy, scene, armature, binding, 3, 0.0)
        observed = float(target_bone.rotation_euler[axis])
        values.append(observed)
        path = outdir / f"synthetic-action-{index}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        _require(path.is_file(), "SELF_TEST_RENDER_FAILED", f"synthetic render missing: {path}")
        info = _analyze_png(bpy, path, max_pixels=10_000)
        _require(info["width"] == 48 and info["height"] == 48 and info["alpha_bounds"] is not None,
                 "SELF_TEST_RENDER_INVALID", "synthetic Action render is not nonempty RGBA output")
        outputs.append({"path": path.name, **info})
    _require(abs(values[0] - values[1]) > 0.5, "SELF_TEST_ACTIONS_NOT_DISTINCT",
             "synthetic Actions did not produce distinct sampled poses")
    report = {"schema": "h3.blender-action-pipeline.synthetic-self-test.v1", "status": "PASS",
              "blender_version": bpy.app.version_string, "synthetic_rig": True,
              "real_creature": False, "real_creature_action_pipeline_accepted": False,
              "action_names": [action.name for action, _, _ in actions],
              "sampled_rotation_radians": values, "outputs": outputs,
              "output_count": len(outputs), "game_written": False, "def_written": False}
    pipeline._write_exclusive(outdir / "synthetic-self-test.json", pipeline._json_bytes(report))
    return report


def _argv_after_separator() -> list[str]:
    try:
        index = sys.argv.index("--")
        return sys.argv[index + 1:]
    except ValueError:
        return []


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--self-test", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--operation-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(_argv_after_separator() if argv is None else argv)
    try:
        bpy = _blender()
        outdir = Path(args.outdir).expanduser()
        if args.self_test:
            result = _self_test_mode(bpy, outdir)
        else:
            _require(args.manifest and args.manifest_sha256 and args.operation_id,
                     "WORKER_ARGUMENTS_REQUIRED", "manifest, manifest SHA, and operation id are required")
            manifest_path = Path(args.manifest).expanduser().resolve(strict=True)
            if args.check:
                result = _check_mode(bpy, manifest_path, args.manifest_sha256,
                                     outdir, args.operation_id)
            else:
                result = _run_mode(bpy, manifest_path, args.manifest_sha256,
                                   outdir, args.operation_id)
        _json_result(result)
        return 0
    except (WorkerError, pipeline.PipelineError) as exc:
        code = exc.code if isinstance(exc, (WorkerError, pipeline.PipelineError)) else "WORKER_FAILED"
        result = {"schema": "h3.blender-action-pipeline.worker-result.v1", "status": "FAIL",
                  "code": code, "message": str(exc)}
        _json_result(result)
        print(f"{code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        result = {"schema": "h3.blender-action-pipeline.worker-result.v1", "status": "FAIL",
                  "code": "WORKER_UNEXPECTED_ERROR", "message": str(exc)}
        _json_result(result)
        print(f"WORKER_UNEXPECTED_ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
