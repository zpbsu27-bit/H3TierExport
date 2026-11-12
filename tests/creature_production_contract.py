"""CLI contracts for full native creature-slot delivery and recovery."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from PIL import Image


PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "creature_production.py"
OUTPUT_BASE = PRODUCT / "build" / "experiments" / "creature-production"
sys.path.insert(0, str(PRODUCT / "scripts"))
import creature_action_plan  # noqa: E402
import creature_production  # noqa: E402


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def row(group: int, frame: int) -> dict:
    return {"kind": "def", "category": "creatures", "identity": "TESTBEAST",
            "group": group, "frame": frame, "canvas": [20, 12],
            "crop": [8, 6], "anchor": [3, 2]}


class CreatureProductionContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="h3-creature-production-")
        self.root = Path(self.temporary.name)
        self.operation_id = "ctest-" + uuid.uuid4().hex[:20]
        self.outdir = OUTPUT_BASE / self.operation_id
        self.inventory = self.root / "native-inventory.json.gz"
        self.plan_path = self.root / "action-plan.json"
        self.manifest_path = self.root / "production-manifest.json"
        self.image_paths = [self.root / f"source-{i}.png" for i in range(3)]
        self.receipt_path = self.root / "motion-receipt.json"
        self.evidence_path = self.root / "motion-evidence.json"
        self.rows = [row(0, 0), row(0, 1), row(7, 0)]
        with gzip.open(self.inventory, "wt", encoding="utf-8", newline="") as stream:
            json.dump({"schema": "h3.complete-art-catalog.v3", "rows": self.rows}, stream,
                      ensure_ascii=False, separators=(",", ":"))
        self.inventory_sha = sha(self.inventory.read_bytes())
        inventory_doc, actual_sha = creature_action_plan.load_catalog(self.inventory, self.inventory_sha)
        plan = creature_action_plan.build_plan(inventory_doc, actual_sha, ["TESTBEAST"], self.inventory)
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.plan_sha = sha(self.plan_path.read_bytes())
        colors = [(255, 20, 30, 200), (255, 20, 30, 200), (20, 180, 60, 255)]
        for path, color in zip(self.image_paths, colors):
            image = Image.new("RGBA", (8, 6), (0, 0, 0, 0))
            image.putpixel((2, 3), color)
            image.save(path, format="PNG")
        self.write_manifest()

    def tearDown(self) -> None:
        if self.outdir.is_symlink():
            self.outdir.unlink()
        elif self.outdir.exists():
            self.assertEqual(self.outdir.parent.resolve(), OUTPUT_BASE.resolve())
            shutil.rmtree(self.outdir)
        self.temporary.cleanup()

    def binding(self, path: Path) -> dict:
        raw = path.read_bytes()
        return {"path": str(path.resolve()), "sha256": sha(raw)}

    def make_slot(self, index: int, *, derivation: dict | None = None) -> dict:
        native = self.rows[index]
        key = ["def", "TESTBEAST", native["group"], native["frame"]]
        return {
            "key": key,
            "geometry": {"canvas_1x": [20, 12], "crop_1x": [8, 6], "origin_1x": [3, 2]},
            "source": {"kind": "original-reference", **self.binding(self.image_paths[index]),
                       "native_scale": 1, "receipt": None},
            "facing": {"value": "unknown", "provenance": None},
            "footpoint": {"value": "unknown", "provenance": None},
            "timing": {"duration_ms": "unknown", "duration_provenance": None,
                       "loop": "unknown", "loop_provenance": None},
            "derivation": derivation or {"kind": "original-reference", "reuse_of": None, "evidence": None},
        }

    def manifest(self, slots: list[dict] | None = None) -> dict:
        return {
            "schema": "h3.creature.production-manifest.v1",
            "operation_id": self.operation_id,
            "action_plan": self.binding(self.plan_path),
            "official_inventory": {"path": str(self.inventory.resolve()), "sha256": self.inventory_sha},
            "slots": slots if slots is not None else [
                self.make_slot(0),
                self.make_slot(1, derivation={"kind": "exact-copy", "reuse_of": ["def", "TESTBEAST", 0, 0],
                                              "evidence": None}),
                self.make_slot(2),
            ],
        }

    def write_manifest(self, value: dict | None = None) -> str:
        raw = (json.dumps(value if value is not None else self.manifest(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.manifest_path.write_bytes(raw)
        return sha(raw)

    def invoke(self, command: str, *, manifest_sha: str | None = None, ok: bool = True,
               args: list[str] | None = None):
        digest = manifest_sha or sha(self.manifest_path.read_bytes())
        command_args = [command]
        if args:
            command_args.extend(args)
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(SCRIPT), *command_args,
             "--manifest", str(self.manifest_path), "--manifest-sha256", digest,
             "--outdir", str(self.outdir), "--operation-id", self.operation_id],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        self.assertEqual(result.returncode == 0, ok, (result.stdout + result.stderr)[-4000:])
        payload = result.stdout.strip() or result.stderr.strip()
        return json.loads(payload)

    def test_report_density_is_read_only_and_uses_explicit_3x_floor(self) -> None:
        report = self.invoke("check", args=["--report-density"])
        self.assertEqual(report["schema"], "h3.creature.production-density-report.v1")
        self.assertEqual(report["status"], "REPORT_ONLY")
        self.assertEqual(report["measurement"]["alpha_threshold"], 128)
        self.assertEqual(report["measurement"]["master_effective_minimum_scale"], 3)
        self.assertEqual(report["measurement"]["master_effective_minimum_formula"], "crop_1x * 3")
        self.assertEqual((report["expected_slots"], report["measured_slots"], report["blocked_slots"]), (3, 3, 0))
        frame = report["frames"][0]
        self.assertEqual(frame["master_effective_minimum_px"], [24, 18])
        self.assertEqual(frame["body_size_px"], [1, 1])
        self.assertEqual(frame["fill_ratio_width"], round(1 / 24, 6))
        self.assertEqual(frame["fill_ratio_height"], round(1 / 18, 6))
        self.assertEqual(frame["suggested_common_k"], 1)
        self.assertIn("BELOW_MASTER_EFFECTIVE_MINIMUM_REVIEW_REQUIRED", frame["review_hints"])
        self.assertEqual(report["images_written"], 0)
        self.assertFalse(report["source_pixels_mutated"])
        self.assertFalse(report["art_approved"])
        self.assertFalse(report["game_deployed"])
        self.assertFalse(self.outdir.exists())

    def test_check_covers_only_actual_slots_and_unknowns_stay_open(self) -> None:
        report = self.invoke("check")
        self.assertEqual(report["status"], "OPEN")
        self.assertEqual((report["expected_slots"], report["bound_slots"], report["missing_slots"]), (3, 3, []))
        self.assertEqual(report["open_metadata_fields"], 15)
        self.assertFalse(self.outdir.exists())
        self.assertEqual([row["group"] for row in self.rows], [0, 0, 7])

    def test_exact_copy_requires_matching_pixels_geometry_and_scale(self) -> None:
        report = self.invoke("check")
        alias = next(item for item in report["frames"] if item["key"] == ["def", "TESTBEAST", 0, 1])
        self.assertEqual(alias["derivation"]["pixel_equivalence"], "EXACT_RGBA_PIXELS_AND_NATIVE_GEOMETRY")
        manifest = self.manifest()
        with Image.open(self.image_paths[1]) as image:
            image.putpixel((3, 3), (1, 2, 3, 255))
            image.save(self.image_paths[1], format="PNG")
        manifest["slots"][1]["source"] = {"kind": "original-reference", **self.binding(self.image_paths[1]),
                                          "native_scale": 1, "receipt": None}
        self.write_manifest(manifest)
        blocked = self.invoke("check", ok=False)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("EXACT_REUSE_PIXEL_MISMATCH", blocked["frames"][1]["errors"][0])

    def test_r10_review_states_preserve_all_native_groups(self) -> None:
        report = self.invoke("check")
        self.assertEqual(len(report["slot_states"]), 3)
        self.assertEqual(len(report["action_groups"]), 22)
        self.assertEqual([item["state"] for item in report["slot_states"]],
                         ["REVIEW_REQUIRED"] * 3)
        group0 = next(group for group in report["action_groups"] if group["group"] == 0)
        self.assertEqual(group0["status"], "REVIEW_REQUIRED")
        self.assertEqual(group0["frame_count"], 2)
        self.assertEqual(group0["slots"][1]["key"], ["def", "TESTBEAST", 0, 1])
        self.assertEqual(next(g for g in report["action_groups"] if g["group"] == 6)["status"], "ABSENT")

        missing = self.manifest()
        missing["slots"] = missing["slots"][:2]
        self.write_manifest(missing)
        partial = self.invoke("check", ok=False)
        self.assertEqual(partial["slot_states"][-1]["state"], "NEEDS_KEYFRAME")
        self.assertEqual(next(g for g in partial["action_groups"] if g["group"] == 7)["status"], "NEEDS_KEYFRAME")

        bad = self.manifest()
        bad["slots"][0]["geometry"]["origin_1x"] = [1, 1]
        self.write_manifest(bad)
        blocked = self.invoke("check", ok=False)
        self.assertEqual(blocked["slot_states"][0]["state"], "NEEDS_NATIVE_MAPPING")
        self.assertEqual(blocked["slot_states"][1]["state"], "BLOCKED")
        self.assertIn("EXACT_REUSE_OWNER_MISSING", blocked["slot_states"][1]["errors"][0])
        self.assertEqual(next(g for g in blocked["action_groups"] if g["group"] == 0)["status"], "BLOCKED")
        self.assertFalse(self.outdir.exists())

    def test_exact_copy_cycle_is_blocked_without_delivery(self) -> None:
        manifest = self.manifest()
        manifest["slots"][0]["derivation"] = {
            "kind": "exact-copy", "reuse_of": ["def", "TESTBEAST", 0, 1], "evidence": None}
        manifest["slots"][1]["derivation"] = {
            "kind": "exact-copy", "reuse_of": ["def", "TESTBEAST", 0, 0], "evidence": None}
        self.write_manifest(manifest)
        report = self.invoke("check", ok=False)
        self.assertEqual(report["status"], "BLOCKED")
        for frame in report["frames"][:2]:
            self.assertEqual(frame["status"], "BLOCKED")
            self.assertIn("EXACT_REUSE_CYCLE", frame["errors"][0])
        self.assertFalse(self.outdir.exists())

    def test_missing_actual_slot_is_needs_keyframe_and_run_writes_nothing(self) -> None:
        self.write_manifest(self.manifest() | {"slots": self.manifest()["slots"][:2]})
        report = self.invoke("check", ok=False)
        self.assertEqual(report["status"], "NEEDS_KEYFRAME")
        self.assertEqual(report["missing_slots"][0]["key"], ["def", "TESTBEAST", 7, 0])
        run = self.invoke("run", ok=False)
        self.assertEqual(run["status"], "NEEDS_KEYFRAME")
        self.assertEqual(run["images_written"], 0)
        self.assertFalse(self.outdir.exists())

    def test_invalid_geometry_and_hash_are_rejected_by_full_preflight(self) -> None:
        manifest = self.manifest()
        manifest["slots"][1]["geometry"]["origin_1x"] = [2, 2]
        self.write_manifest(manifest)
        report = self.invoke("check", ok=False)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("NATIVE_GEOMETRY_MISMATCH", report["frames"][1]["errors"][0])
        self.assertEqual(report["images_written"], 0)

        manifest = self.manifest()
        manifest["slots"][1]["source"]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        report = self.invoke("check", ok=False)
        self.assertIn("SOURCE_SHA_MISMATCH", report["frames"][1]["errors"][0])

    def test_known_metadata_requires_its_own_provenance(self) -> None:
        manifest = self.manifest()
        manifest["slots"][0]["facing"] = {"value": "left", "provenance": None}
        self.write_manifest(manifest)
        report = self.invoke("check", ok=False)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("PROVENANCE_MISSING", report["frames"][0]["errors"][0])

    def test_run_preserves_png_bytes_and_verify_recovers_same_identity(self) -> None:
        checked = self.invoke("check")
        self.assertEqual(checked["status"], "OPEN")
        source_bytes = [path.read_bytes() for path in self.image_paths]
        delivered = self.invoke("run")
        self.assertEqual(delivered["status"], "DELIVERED_OPEN")
        self.assertEqual(delivered["verified_slots"], 3)
        for index, (group, frame) in enumerate([(0, 0), (0, 1), (7, 0)]):
            output = self.outdir / f"frames/TESTBEAST/g{group:02d}/f{frame:03d}.png"
            self.assertEqual(output.read_bytes(), source_bytes[index])
        package_manifest = self.outdir / "manifest.json"
        self.assertEqual(package_manifest.read_bytes(), self.manifest_path.read_bytes())
        verified = self.invoke("verify")
        self.assertEqual(verified["status"], "VERIFIED_OPEN")
        resumed = self.invoke("run")
        self.assertEqual(resumed["status"], "RECOVERED_COMPLETE")
        self.assertEqual(resumed["images_written"], 0)

    def test_incomplete_output_root_is_reported_and_never_blindly_retried(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        self.outdir.mkdir()
        (self.outdir / "partial-marker.txt").write_text("evidence", encoding="utf-8")
        result = self.invoke("run", ok=False)
        self.assertEqual(result["status"], "PARTIAL_OUTPUT_PRESENT")
        result = self.invoke("verify", ok=False)
        self.assertEqual(result["status"], "PARTIAL_OUTPUT_PRESENT")
        self.assertTrue((self.outdir / "partial-marker.txt").is_file())

    def test_reparse_output_root_is_rejected_before_resolve(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        external = self.root / "external-output"
        external.mkdir()
        marker = external / "foreign.txt"
        marker.write_text("preserve", encoding="utf-8")
        try:
            os.symlink(external, self.outdir, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlink privilege unavailable: {error}")
        result = self.invoke("check", ok=False)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["code"], "OUTPUT_ROOT_PATH_UNSAFE")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_corrupt_destination_fails_precommit_before_complete_pointer(self) -> None:
        manifest_sha = sha(self.manifest_path.read_bytes())
        context = creature_production._load_context(str(self.manifest_path), manifest_sha,
                                                   self.operation_id, str(self.outdir))
        original_copy = creature_production._copy_verified

        def copy_then_corrupt(source, destination, expected_sha, guard):
            result = original_copy(source, destination, expected_sha, guard)
            destination.write_bytes(b"corrupt destination")
            return result

        creature_production._copy_verified = copy_then_corrupt
        try:
            result = creature_production.run(context)
        finally:
            creature_production._copy_verified = original_copy
        self.assertEqual(result["status"], "PARTIAL_OUTPUT_PRESENT")
        self.assertEqual(result["code"], "PRECOMMIT_PNG_SHA_MISMATCH")
        self.assertFalse((self.outdir / "complete.json").exists())
        verified = creature_production.verify(context)
        self.assertEqual(verified["status"], "PARTIAL_OUTPUT_PRESENT")

    def test_motion_output_reuses_existing_receipt_validation(self) -> None:
        manifest = self.manifest()
        motion_png = self.root / "motion-3x.png"
        image = Image.new("RGBA", (24, 18), (0, 0, 0, 0))
        image.putpixel((7, 6), (220, 80, 30, 255))
        image.save(motion_png, format="PNG")
        target = {"canvas_1x": [20, 12], "crop_1x": [8, 6], "origin_1x": [3, 2]}
        native_key = ["def", "TESTBEAST", 0, 0]
        slot_id = "TESTBEAST-g00-f000"
        receipt = {
            "schema": "h3.motion.receipt.v1", "job_fingerprint": "a" * 64,
            "contract": {"entry": {"id": slot_id, "key": native_key, "targets": target},
                         "native_entry": {"key": native_key, "targets": target}},
            "output": str(motion_png.resolve()), "output_pixels": [24, 18], "origin_3x": [9, 6],
            "png_sha256": sha(motion_png.read_bytes()), "rgba_sha256": "b" * 64,
            "bytes": motion_png.stat().st_size,
            "model_inference_performed": False, "model_native_output": False,
            "human_approved": False, "art_approved": False, "game_deployed": False,
            "runtime_accepted": False, "native_timing_verified": False,
        }
        self.receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        manifest["slots"][0]["source"] = {"kind": "motion-output", **self.binding(motion_png),
                                           "native_scale": 3, "receipt": self.binding(self.receipt_path)}
        manifest["slots"][0]["derivation"] = {"kind": "keyframe", "reuse_of": None, "evidence": None}
        manifest["slots"][1]["derivation"] = {"kind": "original-reference", "reuse_of": None, "evidence": None}
        self.write_manifest(manifest)
        report = self.invoke("check")
        self.assertEqual(report["status"], "OPEN")
        self.assertEqual(report["frames"][0]["source"]["receipt"]["job_fingerprint"], "a" * 64)

        receipt["contract"]["native_entry"]["targets"]["origin_1x"] = [4, 2]
        self.receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        manifest["slots"][0]["source"]["receipt"] = self.binding(self.receipt_path)
        self.write_manifest(manifest)
        blocked = self.invoke("check", ok=False)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("MOTION_RECEIPT_REJECTED", blocked["frames"][0]["errors"][0])

    def test_blender_canvas_requires_its_real_action_receipt(self) -> None:
        manifest = self.manifest()
        canvas = self.root / "blender-canvas.png"
        image = Image.new("RGBA", (60, 36), (0, 0, 0, 0))
        image.putpixel((10, 7), (220, 80, 30, 1))
        image.save(canvas, format="PNG")
        manifest["slots"][0]["source"] = {
            "kind": "blender-action-output", **self.binding(canvas),
            "native_scale": 3, "layout": "native-canvas", "receipt": None,
        }
        manifest["slots"][0]["derivation"] = {"kind": "keyframe", "reuse_of": None, "evidence": None}
        manifest["slots"][1]["derivation"] = {"kind": "original-reference", "reuse_of": None, "evidence": None}
        self.write_manifest(manifest)
        blocked = self.invoke("check", ok=False)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("BLENDER_ACTION_RECEIPT_REQUIRED", blocked["frames"][0]["errors"][0])
        self.assertFalse(self.outdir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
