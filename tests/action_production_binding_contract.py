"""Offline real-receipt contracts for Action canvas delivery (no Blender/cloud inference)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid
import zlib

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
sys.path.insert(0, str(PRODUCT / "tests"))
import action_production_binding as binding
import blender_action_pipeline as pipeline
import blender_action_pipeline_contract as fixture_module
import creature_production as production


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png16(width: int, height: int, point: tuple[int, int] | None) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray(width * 8)
        if point is not None and y == point[1]:
            row[point[0] * 8:(point[0] + 1) * 8] = struct.pack(">4H", 50001, 20001, 10001, 1)
        rows.append(b"\0" + row)
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


class ActionProductionBindingContract(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixture_module.BlenderActionPipelineContract()
        self.fixture.setUp()
        self.action_id = "action-contract-" + uuid.uuid4().hex[:12]
        self.delivery_id = "delivery-contract-" + uuid.uuid4().hex[:12]
        self.action_outdir = pipeline.ACTION_OUTPUT_ROOT / self.action_id
        self.prepare_outdir = binding.OUTPUT_BASE / self.delivery_id
        self.delivery_outdir = production.OUTPUT_BASE / self.delivery_id
        self.manifest = self.fixture.manifest
        self.manifest["operation_id"] = self.action_id
        self.depth = 8
        self.new_roots = [self.action_outdir, self.prepare_outdir, self.delivery_outdir]

    def tearDown(self) -> None:
        for path in self.new_roots:
            if path.exists():
                self.assertTrue(path.resolve().is_relative_to(PRODUCT / "build" / "experiments"))
                self.assertFalse(path.is_symlink())
                shutil.rmtree(path)
        self.fixture.tearDown()

    def create_action(self, depth: int = 8) -> None:
        self.depth = depth
        self.manifest["render"]["color_depth"] = str(depth)
        self.raw_manifest = pipeline._json_bytes(self.manifest)
        self.fixture.manifest_path.write_bytes(self.raw_manifest)
        self.manifest_sha = digest(self.fixture.manifest_path)
        config = pipeline._validate_manifest(self.manifest)
        def stub_process(*args, **kwargs):
            frames_dir = self.action_outdir / "frames"
            frames = []
            groups = []
            for group in config["groups"]:
                groups.append({"native_group": group["native_group"], "action_name": group["action_name"],
                               "action_slot_requested": group["action_slot"], "resolved_action_slot": None,
                               "loop": group["loop"], "frame_duration_ms": group["frame_duration_ms"],
                               "time_provenance": group["time_provenance"], "time_locator": group["time_locator"]})
                for slot in group["slots"]:
                    layers = {}
                    for name in sorted(binding.LAYERS):
                        path = frames_dir / f"g{group['native_group']:02d}-f{slot['native_frame']:02d}-{name}.png"
                        point = None if name == "shadow" else (slot["anchor"][0] + 1, slot["anchor"][1] + 1)
                        width, height = config["dimensions"]["canvas_3x"]
                        path.write_bytes(png16(width, height, point) if depth == 16 else
                                         fixture_module._png_rgba(width, height, opaque_pixel=point))
                        layers[name] = {"path": path.name, "sha256": digest(path), "width": width,
                                        "height": height, "bit_depth": depth, "color_type": 6,
                                        "alpha_bounds": None if point is None else
                                        [point[0], point[1], point[0] + 1, point[1] + 1],
                                        "nonzero_alpha_pixels": 0 if point is None else 1}
                    frames.append({"native_group": group["native_group"], "native_frame": slot["native_frame"],
                                   "native_key": slot["native_key"], "action_name": group["action_name"],
                                   "action_slot": None, "action_frame": slot["action_frame"], "subframe": slot["subframe"],
                                   "frame_duration_ms": group["frame_duration_ms"], "loop": group["loop"],
                                   "time_provenance": group["time_provenance"], "time_locator": group["time_locator"],
                                   "time_ref": slot["time_ref"], "layers": layers})
            receipt = {"schema": pipeline.WORKER_RECEIPT_SCHEMA, "status": "PASS", "identity": "TESTBEAST",
                       "operation_id": self.action_id, "manifest_sha256": self.manifest_sha,
                       "blender_version": config["blender"]["version"], "groups": groups, "frames": frames,
                       "output_count": len(frames) * 3, "game_written": False, "def_written": False}
            (frames_dir / "worker-receipt.json").write_bytes(pipeline._json_bytes(receipt))
            return 0, "offline contract process stub; no Blender executed", False
        with patch.object(pipeline, "_bounded_process", side_effect=stub_process):
            result = pipeline.run_operation(self.manifest, self.raw_manifest, self.manifest_sha,
                                            self.fixture.manifest_path, self.action_outdir, self.action_id)
        self.assertEqual(result["status"], "COMPLETE")

    def invoke(self, argv: list[str], ok: bool = True) -> dict:
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(PRODUCT / "scripts" / "creature_production.py"),
                                 *argv], text=True, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode == 0, ok, (result.stdout + result.stderr)[-5000:])
        return json.loads(result.stdout or result.stderr)

    def preparation_args(self) -> list[str]:
        return ["prepare-from-actions", "--action-manifest", str(self.fixture.manifest_path),
                "--action-manifest-sha256", self.manifest_sha, "--action-operation-id", self.action_id,
                "--action-outdir", str(self.action_outdir), "--layer", "body", "--operation-id", self.delivery_id,
                "--outdir", str(self.prepare_outdir)]

    def prepare(self) -> dict:
        result = self.invoke(self.preparation_args())
        self.assertEqual(result["status"], "PREPARED_REVIEW_REQUIRED")
        return result

    def delivery_args(self, command: str, prepared: dict) -> list[str]:
        return [command, "--manifest", prepared["manifest"]["path"], "--manifest-sha256", prepared["manifest"]["sha256"],
                "--operation-id", self.delivery_id, "--outdir", str(self.delivery_outdir)]

    def test_prepare_check_run_verify_preserves_16bit_canvas_and_unknown_semantics(self) -> None:
        self.create_action(depth=16)
        prepared = self.prepare()
        self.assertEqual(prepared["images_written"], 0)
        manifest = json.loads(Path(prepared["manifest"]["path"]).read_bytes())
        for slot in manifest["slots"]:
            self.assertEqual(slot["source"]["layout"], "native-canvas")
            self.assertEqual(slot["facing"]["value"], "unknown")
            self.assertEqual(slot["footpoint"]["value"], "unknown")
            self.assertEqual(slot["timing"]["duration_ms"], 100)
        self.assertEqual(self.invoke(self.delivery_args("check", prepared))["status"], "OPEN")
        self.assertEqual(self.invoke(self.delivery_args("run", prepared))["status"], "DELIVERED_OPEN")
        receipt = json.loads((self.delivery_outdir / "receipt.json").read_bytes())
        for row in receipt["frames"]:
            raw = (self.delivery_outdir / row["output"]).read_bytes()
            self.assertEqual(raw, Path(row["source"]["path"]).read_bytes())
            self.assertEqual(raw[24], 16)
            self.assertEqual(row["output_pixels"], [300, 240])
            self.assertFalse(receipt["art_approved"])
        self.assertEqual(self.invoke(self.delivery_args("verify", prepared))["status"], "VERIFIED_OPEN")

    def test_operation_is_verified_once_per_full_preflight_and_drift_stops_completion(self) -> None:
        self.create_action()
        prepared = self.prepare()
        context = production._load_context(prepared["manifest"]["path"], prepared["manifest"]["sha256"],
                                           self.delivery_id, str(self.delivery_outdir))
        with patch.object(pipeline, "verify_operation", wraps=pipeline.verify_operation) as verifier:
            self.assertEqual(production.preflight(context)["bound_slots"], 3)
            self.assertEqual(verifier.call_count, 1)
        original = production._copy_verified
        def mutate_after_copy(*args, **kwargs):
            result = original(*args, **kwargs)
            self.fixture.time_path.write_bytes(b"changed after Action source preflight")
            return result
        with patch.object(production, "_copy_verified", side_effect=mutate_after_copy):
            result = production.run(context)
        self.assertEqual(result["status"], "PARTIAL_OUTPUT_PRESENT")
        self.assertEqual(result["code"], "ACTION_SOURCE_DRIFT")
        self.assertFalse((self.delivery_outdir / "complete.json").exists())

    def test_missing_complete_and_runtime_drift_block_preparation_without_output(self) -> None:
        self.create_action()
        start_path = self.action_outdir / "operation-start.json"
        start = json.loads(start_path.read_bytes())
        start["runtime_sources"][0]["sha256"] = "0" * 64
        start_path.write_bytes(pipeline._json_bytes(start))
        blocked = self.invoke(self.preparation_args(), ok=False)
        self.assertEqual(blocked["code"], "RUNTIME_SOURCE_BINDING_DRIFT")
        self.assertFalse(self.prepare_outdir.exists())
        (self.action_outdir / "operation-complete.json").unlink()
        blocked = self.invoke(self.preparation_args(), ok=False)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertFalse(self.prepare_outdir.exists())

    def test_wrong_slot_source_and_descriptor_sha_are_rejected(self) -> None:
        self.create_action()
        prepared = self.prepare()
        path = Path(prepared["manifest"]["path"])
        manifest = json.loads(path.read_bytes())
        manifest["slots"][0]["source"]["path"] = manifest["slots"][2]["source"]["path"]
        manifest["slots"][0]["source"]["sha256"] = manifest["slots"][2]["source"]["sha256"]
        path.write_bytes(pipeline._json_bytes(manifest))
        bad_prepared = {"manifest": {"path": str(path), "sha256": digest(path)}}
        blocked = self.invoke(self.delivery_args("check", bad_prepared), ok=False)
        self.assertIn("ACTION_SLOT_BINDING_MISMATCH", blocked["frames"][0]["errors"][0])
        descriptor_path = Path(prepared["action_binding"]["path"])
        descriptor = json.loads(descriptor_path.read_bytes())
        descriptor["layer"] = "shadow"
        descriptor_path.write_bytes(pipeline._json_bytes(descriptor))
        blocked = self.invoke(self.delivery_args("check", bad_prepared), ok=False)
        self.assertIn("ACTION_BINDING_SHA_MISMATCH", blocked["frames"][0]["errors"][0])
        self.assertFalse(self.delivery_outdir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
