"""Offline and optional Blender integration contracts for native Action sampling."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from types import SimpleNamespace
from unittest import mock

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPTS = PRODUCT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import blender_action_pipeline as pipeline
import blender_action_worker as worker
import creature_action_plan
import companion_runtime


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _worker_bootstrap_payload(root: Path) -> Path:
    """Stage the complete worker with a TEST-only sibling that cannot load assets."""
    root.mkdir()
    worker_path = root / "blender_action_worker.py"
    worker_path.write_bytes((SCRIPTS / worker_path.name).read_bytes())
    (root / "blender_action_pipeline.py").write_text(
        "# TEST_ONLY bootstrap sibling; no rig, model, image, or production pipeline.\n"
        "import json, os, sys\n"
        "if sys.dont_write_bytecode is not True:\n"
        "    raise RuntimeError('TEST_WORKER_CAN_WRITE_BYTECODE_BEFORE_SIBLING_IMPORT')\n"
        "print('H3_TEST_WORKER_BOOTSTRAP=' + json.dumps({\n"
        "    'test_only': True, 'ignore_environment': sys.flags.ignore_environment,\n"
        "    'dont_write_bytecode': sys.dont_write_bytecode,\n"
        "    'PYTHONDONTWRITEBYTECODE': os.getenv('PYTHONDONTWRITEBYTECODE')}))\n",
        encoding="utf-8")
    return worker_path


def _worker_bootstrap_tree(root: Path) -> dict:
    paths = list(root.rglob("*"))
    return {"files": {str(p.relative_to(root)): sha(p) for p in paths if p.is_file()},
            "directories": sorted(str(p.relative_to(root)) for p in paths if p.is_dir())}


class BlenderWorkerBootstrapContract(unittest.TestCase):
    def test_worker_disables_bytecode_before_sibling_import_when_environment_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="h3-worker-bootstrap-") as temp:
            payload = Path(temp) / "TEST-payload"
            worker_path = _worker_bootstrap_payload(payload)
            before = _worker_bootstrap_tree(payload)
            result = subprocess.run(
                [sys.executable, "-E", str(worker_path), "--", "--help"],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, cwd=temp,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker = "H3_TEST_WORKER_BOOTSTRAP="
            flags = json.loads(next(line[len(marker):] for line in result.stdout.splitlines()
                                    if line.startswith(marker)))
            self.assertEqual(flags, {"test_only": True, "ignore_environment": 1,
                                     "dont_write_bytecode": True,
                                     "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertIn("--check", result.stdout)
            self.assertEqual(_worker_bootstrap_tree(payload), before)


def _row(group: int, frame: int, identity: str = "TESTBEAST") -> dict:
    return {"kind": "def", "category": "creatures", "identity": identity,
            "group": group, "frame": frame, "canvas": [100, 80],
            "crop": [40, 30], "anchor": [12, 9]}


def _png_rgba(width: int, height: int, *, opaque_pixel: tuple[int, int] | None) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        row = bytearray(width * 4)
        if opaque_pixel is not None and opaque_pixel[1] == y:
            x = opaque_pixel[0]
            row[x * 4:x * 4 + 4] = bytes((240, 160, 40, 255))
        raw.extend(row)

    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


class BlenderActionPipelineContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="h3-blender-action-")
        self.root = Path(self.temporary.name)
        self.input_root = self.root / "inputs"
        self.input_root.mkdir()
        self.output_root = self.root / "action-pipeline"
        self.plan_path = self.input_root / "action-plan.json"
        self.model_path = self.input_root / "creature.glb"
        self.time_path = self.input_root / "timing.json"
        self.blender_path = self.input_root / ("blender.exe" if os.name == "nt" else "blender")
        self.model_path.write_bytes(b"synthetic GLB identity for offline contract only")
        self.time_path.write_text('{"authored_timing":"fixture"}\n', encoding="utf-8")
        self.blender_path.write_bytes(b"synthetic Blender identity for offline contract only")
        self.inventory_path = self.input_root / "inventory.json.gz"
        rows = [_row(0, 0), _row(0, 1), _row(2, 0)]
        with gzip.open(self.inventory_path, "wt", encoding="utf-8", newline="") as stream:
            json.dump({"schema": creature_action_plan.INVENTORY_SCHEMA, "rows": rows},
                      stream, ensure_ascii=False, separators=(",", ":"))
        document, inventory_sha = creature_action_plan.load_catalog(self.inventory_path, sha(self.inventory_path))
        plan = creature_action_plan.build_plan(document, inventory_sha, ["TESTBEAST"], self.inventory_path)
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.manifest = self._manifest()
        self.manifest_path = self.input_root / "manifest.json"
        self.manifest_bytes = (json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.manifest_path.write_bytes(self.manifest_bytes)
        self.manifest_sha = hashlib.sha256(self.manifest_bytes).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self) -> dict:
        timing_ref = {"path": str(self.time_path), "sha256": sha(self.time_path)}
        groups = []
        for group_id, action_name, frames, loop in (
                (0, "Walk", [(0, 1, 0.0), (1, 3, 0.5)], True),
                (2, "Idle", [(0, 1, 0.0)], False)):
            groups.append({
                "native_group": group_id,
                "action_name": action_name,
                "action_slot": None,
                "loop": loop,
                "frame_duration_ms": 100,
                "time_provenance": {**timing_ref, "locator": f"timing/groups/{group_id}"},
                "slots": [{"native_frame": native_frame, "action_frame": action_frame,
                           "subframe": subframe, "time_ref": f"group-{group_id}/frame-{native_frame}"}
                          for native_frame, action_frame, subframe in frames],
            })
        return {
            "schema": pipeline.PIPELINE_SCHEMA,
            "operation_id": "synthetic-action-r01",
            "identity": "TESTBEAST",
            "action_plan": {"path": str(self.plan_path), "sha256": sha(self.plan_path)},
            "model": {"path": str(self.model_path), "sha256": sha(self.model_path), "dependencies": []},
            "blender": {"path": str(self.blender_path), "sha256": sha(self.blender_path), "version": "4.5.9"},
            "armature_object": "Armature",
            "model_transform": {"location": [0, 0, 0], "rotation_euler_degrees": [0, 0, 0],
                                "scale": [1, 1, 1]},
            "facing": {"model_forward_axis": "-Y", "yaw_degrees": 0,
                       "mirror_policy": "engine-mirrors"},
            "camera": {"projection": "ORTHO", "location": [3, -5, 4],
                       "rotation_euler_degrees": [55, 0, 30], "orthographic_scale": 3,
                       "shift_x": 0, "shift_y": 0, "clip_start": 0.1, "clip_end": 100},
            "footpoint": {"bone_name": "Foot", "bone_local_position": [0, 0, 0],
                          "ground_plane_z": 0, "expected_pixel_1x": [50, 40], "tolerance_px": 2,
                          "calibration_native_group": 0, "calibration_native_frame": 0},
            "render": {"engine": "CYCLES", "samples": 1, "color_depth": "8",
                       "transparent": True, "view_transform": "Standard", "look": "None"},
            "world": {"color": [0, 0, 0], "strength": 0.04},
            "lights": [{"name": "Key", "type": "SUN", "location": [3, -4, 5],
                        "rotation_euler_degrees": [25, 0, 15], "energy": 3, "angle_degrees": 8}],
            "groups": groups,
        }

    def _validate(self) -> dict:
        return pipeline._validate_manifest(self.manifest)

    def _expect_code(self, code: str) -> None:
        with self.assertRaises(pipeline.PipelineError) as caught:
            self._validate()
        self.assertEqual(caught.exception.code, code)

    def test_manifest_binds_every_present_slot_and_fixed_geometry(self) -> None:
        config = self._validate()
        self.assertEqual(config["dimensions"]["slot_count"], 3)
        self.assertEqual(config["dimensions"]["present_group_count"], 2)
        self.assertEqual(config["dimensions"]["absent_groups"],
                         [group for group in range(22) if group not in {0, 2}])
        self.assertEqual(config["dimensions"]["canvas_3x"], [300, 240])
        self.assertEqual(config["groups"][0]["slots"][1]["subframe"], 0.5)
        self.assertEqual(config["groups"][0]["time_provenance"]["sha256"], sha(self.time_path))

    def test_binding_for_absent_native_group_is_refused(self) -> None:
        self.manifest["groups"].append({"native_group": 1})
        self._expect_code("BINDING_FOR_ABSENT_GROUP")

    def test_every_native_frame_must_have_an_explicit_action_sample(self) -> None:
        self.manifest["groups"][0]["slots"].pop()
        self._expect_code("SLOT_MAP_INCOMPLETE")

    def test_subframe_and_time_source_hash_are_strict(self) -> None:
        self.manifest["groups"][0]["slots"][1]["subframe"] = 1.0
        self._expect_code("ACTION_SAMPLE_INVALID")
        self.manifest = self._manifest()
        self.manifest["groups"][0]["time_provenance"]["sha256"] = "0" * 64
        self._expect_code("INPUT_SHA256_MISMATCH")

    def test_model_and_blender_absence_have_specific_block_codes(self) -> None:
        self.manifest["model"]["path"] = str(self.input_root / "missing.glb")
        with self.assertRaises(pipeline.PipelineError) as caught:
            self._validate()
        self.assertEqual(caught.exception.code, "MODEL_NOT_FOUND")
        self.manifest = self._manifest()
        self.manifest["blender"]["path"] = str(self.input_root / "missing-blender.exe")
        with self.assertRaises(pipeline.PipelineError) as caught:
            self._validate()
        self.assertEqual(caught.exception.code, "BLENDER_NOT_FOUND")

    def test_blender_lts_version_string_is_an_exactly_pinned_form(self) -> None:
        self.manifest["blender"]["version"] = "4.5.9 LTS"
        config = self._validate()
        self.assertEqual(config["blender"]["version"], "4.5.9 LTS")
        self.manifest["blender"]["version"] = "4.5.9 LTS beta"
        self._expect_code("BLENDER_VERSION_INVALID")

    def test_output_must_be_new_scoped_and_outside_source_parents(self) -> None:
        operation = self.output_root / self.manifest["operation_id"]
        result = pipeline.check_static(self.manifest, self.manifest["operation_id"], operation,
                                       output_root=self.output_root)
        self.assertEqual(result["output_path"], operation)
        self.output_root.mkdir()
        operation.mkdir()
        with self.assertRaises(pipeline.PipelineError) as caught:
            pipeline.check_static(self.manifest, self.manifest["operation_id"], operation,
                                  output_root=self.output_root)
        self.assertEqual(caught.exception.code, "OPERATION_ALREADY_EXISTS")

    def test_incomplete_operation_is_reported_without_retry(self) -> None:
        operation = self.output_root / self.manifest["operation_id"]
        operation.mkdir(parents=True)
        (operation / "manifest.json").write_bytes(self.manifest_bytes)
        (operation / "operation-start.json").write_text(json.dumps({
            "schema": pipeline.OPERATION_SCHEMA, "status": "RUNNING",
            "operation_id": self.manifest["operation_id"],
            "manifest_path": str(self.manifest_path), "manifest_sha256": self.manifest_sha,
            "runtime_sources": pipeline.runtime_source_bindings(),
        }), encoding="utf-8")
        with self.assertRaises(pipeline.PipelineError) as caught:
            pipeline.verify_operation(self.manifest, self.manifest_bytes, self.manifest_sha,
                                      self.manifest_path, operation, self.manifest["operation_id"],
                                      output_root=self.output_root)
        self.assertEqual(caught.exception.code, "OPERATION_INCOMPLETE_NO_RERUN")

    def test_historical_start_without_runtime_sources_is_not_current(self) -> None:
        with self.assertRaises(pipeline.PipelineError) as caught:
            pipeline._verify_runtime_sources({"schema": pipeline.OPERATION_SCHEMA, "status": "RUNNING"})
        self.assertEqual(caught.exception.code, "RUNTIME_SOURCE_BINDING_MISSING")

    def test_runtime_source_closure_rejects_worker_hash_and_list_drift(self) -> None:
        current = pipeline.runtime_source_bindings()
        pipeline._verify_runtime_sources({"runtime_sources": current})
        altered = [dict(ref) for ref in current]
        next(ref for ref in altered if ref["path"].endswith("blender_action_worker.py"))["sha256"] = "0" * 64
        for records in (altered, current[:-1]):
            with self.assertRaises(pipeline.PipelineError) as caught:
                pipeline._verify_runtime_sources({"runtime_sources": records})
            self.assertEqual(caught.exception.code, "RUNTIME_SOURCE_BINDING_DRIFT")

    def test_packaged_worker_sources_and_secondary_process_do_not_use_live_checkout(self) -> None:
        names = [Path(row["path"]).name for row in pipeline.runtime_source_bindings()]
        contents = {name: b"# TEST_ONLY packaged dependency\n" for name in names}
        contents["blender_action_pipeline.py"] = (SCRIPTS / "blender_action_pipeline.py").read_bytes()
        contents["blender_action_worker.py"] = (
            b"import sys\nfrom pathlib import Path\nimport creature_action_plan\n"
            b"Path(sys.argv[1]).write_text('TEST_ONLY worker launcher', encoding='utf-8')\n")
        rows = [{"path": name, "source_path": "resources/" + name if name.endswith(".json") else "scripts/" + name,
                 "role": "configuration" if name.endswith(".json") else "python-worker" if name == "blender_action_worker.py" else "python-module",
                 "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                for name, raw in contents.items()]
        manifest = {"schema": "h3.tier-export.companion-runtime.v2",
                    "entrypoints": {"blender-actions": "blender_action_pipeline.py"},
                    "entrypoint_resources": {"blender-actions": ["blender-worker"]},
                    "resources": {"blender-worker": {"path": "blender_action_worker.py",
                                  "source_path": "scripts/blender_action_worker.py", "role": "python-worker", "tree": False}},
                    "files": rows}
        raw = (json.dumps(manifest, sort_keys=True) + "\n").encode()
        payload = self.root / (".runtime-" + hashlib.sha256(raw).hexdigest())
        payload.mkdir()
        (payload / "runtime-manifest.json").write_bytes(raw)
        for name, data in contents.items():
            (payload / name).write_bytes(data)
        with mock.patch.object(companion_runtime, "frozen_root", return_value=payload), \
                mock.patch.object(sys, "path", list(sys.path)):
            spec = importlib.util.spec_from_file_location("test_packaged_action_pipeline", payload / "blender_action_pipeline.py")
            frozen = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(frozen)
            bindings = frozen.runtime_source_bindings()
            self.assertTrue(all(Path(row["path"]).is_relative_to(payload) for row in bindings))
            self.assertEqual(len(bindings), len(contents))
            frozen._verify_runtime_sources({"runtime_sources": bindings})
            argv = frozen._worker_argv(Path(sys.executable), "--check", manifest_path=self.manifest_path,
                                       manifest_sha256=self.manifest_sha, outdir=self.output_root,
                                       operation_id="TEST_PACKAGED_WORKER")
            self.assertEqual(Path(argv[argv.index("--python") + 1]), payload / "blender_action_worker.py")
            before = {p.name: sha(p) for p in payload.iterdir()}
            operation = self.root / "worker-operation"
            operation.mkdir()
            result = operation / "launcher.txt"
            code, tail, timed_out = frozen._bounded_process(
                [sys.executable, str(frozen.WORKER_PATH), str(result)], timeout_seconds=30, cwd=str(operation))
            self.assertEqual(code, 0, tail)
            self.assertFalse(timed_out)
            self.assertTrue(result.is_file())
            self.assertEqual({p.name: sha(p) for p in payload.iterdir()}, before)
            self.assertEqual(list(operation.iterdir()), [result])
            worker_path = payload / "blender_action_worker.py"
            worker_path.write_bytes(contents[worker_path.name] + b"\n# drift\n")
            with self.assertRaises(frozen.PipelineError) as caught:
                frozen._verify_runtime_sources({"runtime_sources": bindings})
            self.assertEqual(caught.exception.code, "RUNTIME_SOURCE_BINDING_DRIFT")
            worker_path.unlink()
            with self.assertRaises(OSError):
                frozen.runtime_source_bindings()

    def test_local_model_source_never_claims_cloud_origin(self) -> None:
        self.assertEqual(self._validate()["model_provenance"],
                         {"kind": "LOCAL_MODEL_SOURCE", "cloud_origin_verified": False})

    def test_cloud_provenance_is_exact_read_only_existing_evidence(self) -> None:
        import cloud_animation as cloud
        from unittest.mock import patch
        cloud_root = self.root / "cloud-animation"
        operation = cloud_root / "operations" / "cloud-fixture"
        output = operation / "candidates"
        output.mkdir(parents=True)
        model = output / "creature.glb"
        model.write_bytes(self.model_path.read_bytes())
        plan = {"schema": cloud.PLAN_SCHEMA, "provider": "meshy", "stage": "animation",
                "operation_id": "cloud-fixture", "operation_directory": str(operation),
                "output_directory": str(output), "sources": [], "payload": {},
                "payload_sha256": cloud.sha256_bytes(cloud.canonical_json({}))}
        plan_path = operation / "plan.json"
        plan_path.write_bytes(cloud.canonical_json(plan))
        (operation / "plan.sha256").write_text(sha(plan_path), encoding="ascii")
        fetch = {"schema": cloud.RECEIPT_SCHEMA, "operation_id": "cloud-fixture",
                 "provider_job_id": "offline-test-job", "provider": "meshy", "stage": "animation",
                 "files": [{"path": str(model), "sha256": sha(model), "bytes": model.stat().st_size,
                            "candidate_only": True}], **{key: False for key in
                  ("art_approved", "alpha_verified", "h3_action_group_complete", "native_frame_timing_verified",
                   "runtime_accepted", "game_deployed")}}
        fetch_path = output / "fetch-receipt.json"
        fetch_path.write_bytes(cloud.canonical_json(fetch))
        state = {"schema": cloud.STATE_SCHEMA, "operation_id": "cloud-fixture",
                 "provider_job_id": "offline-test-job", "provider": "meshy", "stage": "animation",
                 "phase": "FETCHED_CANDIDATE_REVIEW_REQUIRED", "fetch_receipt_sha256": sha(fetch_path)}
        state_path = operation / "state.json"
        state_path.write_bytes(cloud.canonical_json(state))
        provenance = {"operation_id": "cloud-fixture", "provider_job_id": "offline-test-job",
                      **{name: {"path": str(path), "sha256": sha(path)} for name, path in
                         (("plan", plan_path), ("state", state_path), ("fetch_receipt", fetch_path))}}
        source = {"path": str(model), "sha256": sha(model)}
        before = {p: p.read_bytes() for p in (plan_path, state_path, fetch_path)}
        with patch.object(cloud, "DEVELOPMENT_ROOT", cloud_root), patch.object(
                cloud, "default_transport", side_effect=AssertionError("no network permitted")):
            result, refs = pipeline._validate_cloud_provenance(provenance, source)
            self.assertEqual(result["kind"], "CLOUD_FETCHED_MODEL_SOURCE")
            self.assertEqual(result["operation_id"], "cloud-fixture")
            self.assertEqual(len(refs), 4)
            for p, raw in before.items():
                self.assertEqual(p.read_bytes(), raw)
            bad = dict(provenance, provider_job_id="different-job")
            with self.assertRaises(pipeline.PipelineError) as caught:
                pipeline._validate_cloud_provenance(bad, source)
            self.assertEqual(caught.exception.code, "CLOUD_PROVENANCE_IDENTITY_MISMATCH")
            with self.assertRaises(pipeline.PipelineError) as caught:
                pipeline._validate_cloud_provenance(provenance, {"path": str(self.model_path), "sha256": sha(self.model_path)})
            self.assertEqual(caught.exception.code, "CLOUD_MODEL_FILE_MISMATCH")

    def test_action_slot_ambiguity_is_rejected(self) -> None:
        action = SimpleNamespace(name="Walk", slots=[SimpleNamespace(identifier="slot-a", name="A"),
                                                       SimpleNamespace(identifier="slot-b", name="B")])
        with self.assertRaises(worker.WorkerError) as caught:
            worker._resolve_action_slot(action, None, group_id=0)
        self.assertEqual(caught.exception.code, "ACTION_SLOT_AMBIGUOUS")
        selected = worker._resolve_action_slot(action, "slot-b", group_id=0)
        self.assertEqual(selected.identifier, "slot-b")

    def test_glTF_importer_nla_carriers_are_cleared_only_when_exact(self) -> None:
        action = SimpleNamespace(name="move", frame_range=(1.0, 3.0))
        strip = SimpleNamespace(action=action, action_frame_start=1.0,
                                action_frame_end=3.0, frame_start=1.0,
                                frame_end=3.0, blend_type="REPLACE",
                                extrapolation="HOLD")
        track = SimpleNamespace(name="move", strips=[strip])
        tracks = [track]
        animation_data = SimpleNamespace(nla_tracks=tracks)
        armature = SimpleNamespace(name="Armature", type="ARMATURE",
                                   animation_data=animation_data)
        report = worker._prepare_explicit_action_source(
            None, [armature], {"groups": [{"action_name": "move"}]})
        self.assertTrue(report["cleared_importer_nla"])
        self.assertEqual(report["carriers"][0]["action"], "move")
        self.assertEqual(tracks, [])

        bad_strip = SimpleNamespace(action=action, action_frame_start=1.0,
                                    action_frame_end=3.0, frame_start=1.0,
                                    frame_end=4.0, blend_type="REPLACE",
                                    extrapolation="HOLD")
        bad_armature = SimpleNamespace(name="Armature", type="ARMATURE",
                                       animation_data=SimpleNamespace(
                                           nla_tracks=[SimpleNamespace(name="bad", strips=[bad_strip])]))
        with self.assertRaises(worker.WorkerError) as caught:
            worker._prepare_explicit_action_source(
                None, [bad_armature], {"groups": [{"action_name": "move"}]})
        self.assertEqual(caught.exception.code, "NLA_TRACKS_AMBIGUOUS")

    def test_worker_receipt_verifies_all_layers_hashes_and_geometry(self) -> None:
        config = self._validate()
        config["manifest_sha256"] = self.manifest_sha
        frames_dir = self.root / "frames"
        frames_dir.mkdir()
        frames = []
        for group in config["groups"]:
            for slot in group["slots"]:
                layers = {}
                for name in ("body", "shadow", "overlay"):
                    path = frames_dir / f"g{group['native_group']:02d}-f{slot['native_frame']:02d}-{name}.png"
                    opaque = None if name == "shadow" else (slot["anchor"][0] + 1, slot["anchor"][1] + 1)
                    path.write_bytes(_png_rgba(300, 240, opaque_pixel=opaque))
                    bounds = None if opaque is None else [opaque[0], opaque[1], opaque[0] + 1, opaque[1] + 1]
                    layers[name] = {"path": path.name, "sha256": sha(path), "width": 300,
                                    "height": 240, "bit_depth": 8, "color_type": 6,
                                    "alpha_bounds": bounds,
                                    "nonzero_alpha_pixels": 0 if opaque is None else 1}
                slot_map = next(item for item in group["slots"] if item["native_frame"] == slot["native_frame"])
                frames.append({"native_group": group["native_group"], "native_frame": slot["native_frame"],
                               "native_key": slot["native_key"], "action_name": group["action_name"],
                               "action_slot": None, "action_frame": slot_map["action_frame"],
                               "subframe": slot_map["subframe"], "frame_duration_ms": group["frame_duration_ms"],
                               "loop": group["loop"], "time_provenance": group["time_provenance"],
                               "time_locator": group["time_locator"], "time_ref": slot_map["time_ref"],
                               "layers": layers})
        group_rows = [{"native_group": group["native_group"], "action_name": group["action_name"],
                       "action_slot_requested": group["action_slot"], "resolved_action_slot": None,
                       "loop": group["loop"], "frame_duration_ms": group["frame_duration_ms"],
                       "time_provenance": group["time_provenance"], "time_locator": group["time_locator"]}
                      for group in config["groups"]]
        receipt = {"schema": pipeline.WORKER_RECEIPT_SCHEMA, "status": "PASS",
                   "operation_id": self.manifest["operation_id"], "manifest_sha256": self.manifest_sha,
                   "identity": "TESTBEAST", "blender_version": "4.5.9", "groups": group_rows,
                   "frames": frames, "output_count": len(frames) * 3,
                   "game_written": False, "def_written": False}
        verified = pipeline._verify_worker_receipt(receipt, config, self.manifest["operation_id"], frames_dir)
        self.assertEqual(verified, {"status": "PASS", "slot_count": 3, "output_count": 9})


class BlenderSyntheticRigIntegration(unittest.TestCase):
    def test_real_blender_samples_two_synthetic_actions(self) -> None:
        blender = os.environ.get("H3TIEREXPORT_BLENDER")
        if not blender:
            self.skipTest("set H3TIEREXPORT_BLENDER to run Blender's synthetic-rig integration")
        blender_path = Path(blender).resolve(strict=True)
        if not blender_path.is_file():
            self.fail(f"H3TIEREXPORT_BLENDER is not a file: {blender_path}")
        with tempfile.TemporaryDirectory(prefix="h3-action-blender-selftest-") as temp:
            output = Path(temp) / "synthetic-rig"
            result = subprocess.run(
                [str(blender_path), "--background", "--factory-startup", "--python-exit-code", "1",
                 "--python", str(SCRIPTS / "blender_action_worker.py"), "--", "--self-test",
                 "--outdir", str(output)], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600, cwd=temp)
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
            report = json.loads((output / "synthetic-self-test.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["synthetic_rig"])
            self.assertFalse(report["real_creature"])
            self.assertFalse(report["real_creature_action_pipeline_accepted"])
            self.assertEqual(report["action_names"], ["SyntheticWalk", "SyntheticAttack"])
            self.assertEqual(report["output_count"], 2)
            self.assertGreater(abs(report["sampled_rotation_radians"][0]
                                   - report["sampled_rotation_radians"][1]), 0.5)
            self.assertTrue(all(Path(output / item["path"]).is_file() for item in report["outputs"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
