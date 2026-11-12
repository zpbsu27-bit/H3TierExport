"""Source, geometry, immutable replay and zero-resampling contracts for town scenes."""
from __future__ import annotations
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import town_scene_composer as composer
import review_native_batch as native


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reference(path):
    raw = path.read_bytes()
    return {"path": str(path), "sha256": digest(raw), "bytes": len(raw)}


def save(path, doc):
    path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return reference(path)


class TownSceneComposerContract(unittest.TestCase):
    def setUp(self):
        base = PRODUCT / "build/tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="scene-contract-", dir=base)
        self.root = Path(self.temp.name)
        self.operation = self.root.name
        self.out = composer.OUTPUT_BASE / self.operation
        self.binding = {"qualification": "synthetic_source_contract_only"}
        self.geometry = {"canvas": [4, 3], "crop": [2, 2], "origin": [1, 1]}
        rows = [{"kind": "pcx", "identity": "TESTBG", "canvas": [8, 6], "crop": [8, 6], "anchor": [0, 0]}]
        for name in ("BACK", "FRONT"):
            rows.append({"kind": "def", "identity": name, "group": 0, "frame": 0,
                         "canvas": [4, 3], "crop": [2, 2], "anchor": [1, 1]})
        self.inventory = self.root / "inventory.json.gz"
        self.inventory.write_bytes(gzip.compress(json.dumps({"rows": rows}).encode(), mtime=0))
        art = save(self.root / "art.json", {"schema": "synthetic-art-standard.v1"})
        historical = save(self.root / "historical.json", {"schema": "synthetic-diagnostic-authority.v1"})
        provenance = save(self.root / "provenance.json", {"source": "synthetic full canvas fixture"})
        bg = self.root / "bg.png"
        Image.new("RGBA", (24, 18), (0, 0, 0, 0)).save(bg)
        self.owner = {"schema": composer.OWNER_SCHEMA,
            "authority": {"client_external_tiers": [3], "new_export_tiers": [3], "save_new_1x_files": False,
                          "asset_index_written": False, "source_art_selected": False, "human_approved": False,
                          "game_deployed": False},
            "current_art_standard_reference": art, "historical_diagnostic_authority": historical,
            "native_coordinate_reference_only": [8, 6], "viewport_3x": [24, 18],
            "composition_contract": {"sampling_steps": 0, "verify_all_file_hashes_before_output": True,
                                     "viewport_clipping_only": True},
            "assets": {}, "occurrences": [], "states": [],
            "background": {"key": ["pcx", "TESTBG"], "highest_source": reference(bg),
                "actual_pixels": [24, 18], "required_minimum_pixels": [24, 18],
                "status": "READY_EXACT3X_INPUT", "usable_3x_source": reference(bg),
                "resample_lower_source_to_fake3x": False}}
        for name in ("BACK", "FRONT"):
            image = Image.new("RGBA", (12, 9), (0, 0, 0, 0))
            image.putpixel((4, 4), (0, 0, 200, 255) if name == "BACK" else (200, 100, 50, 128))
            if name == "FRONT":
                image.putpixel((3, 3), (200, 100, 50, 1))
            file = self.root / (name + ".png")
            image.save(file)
            self.owner["assets"]["def:" + name + ":0:0"] = {
                "key": ["def", name, 0, 0], "native_geometry": copy.deepcopy(self.geometry),
                "file": reference(file), "image": {"pixels": [12, 9], "mode": "RGBA",
                    "rgba_sha256": digest(image.tobytes())},
                "source_role": ("CURRENT_MODERN_MASTER_DIRECT3X_DIAGNOSTIC_CANDIDATE" if name == "FRONT"
                                else "EXISTING_EXACT3X_FULL_NATIVE_CANVAS_DELIVERY"),
                "source_layout": "FULL_NATIVE_CANVAS_3X", "origin_already_baked_into_canvas": True,
                "composition_sampling_steps": 0, "art_selected_by_this_operation": False, "provenance": provenance}
        self.facts = {"schema": "h3.art-town.frozen-scene-input-facts.v1", "native_scene_canvas": [8, 6],
                      "occurrences": [], "states": []}
        self.decision = {"schema": "h3.art-town.explicit-diagnostic-layer-set-decision.v1",
                         "operation_id": "UNIT_RECIPE_V1", "game_deployed": False,
                         "new_art_or_personal_approval": False, "states": []}
        for state in ("base", "upgraded"):
            ids = []
            for name, slot, order in (("BACK", 2, 0), ("FRONT", 5, 7)):
                oid = state + ":" + name
                ids.append(oid)
                row = {"occurrence_id": oid, "key": ["def", name, 0, 0], "state": state,
                       "original_building_slot": slot, "original_draw_order_index": order,
                       "native_geometry": copy.deepcopy(self.geometry)}
                self.facts["occurrences"].append({**row, "original_native_global_xy": [1, 1],
                    "replacement": {"replace_existing_placeholder": name == "FRONT"}})
                self.owner["occurrences"].append({**row, "native_global_xy": [1, 1], "placement_xy_3x": [3, 3],
                    "canvas_3x": [12, 9], "crop_3x": [6, 6], "origin_3x": [3, 3],
                    "add_crop_origin_again": False, "asset_id": "def:" + name + ":0:0",
                    "status": "READY_EXACT3X_INPUT", "replacement_identity": ["exact_key", "building_slot", "state"],
                    "source_role": self.owner["assets"]["def:" + name + ":0:0"]["source_role"],
                    "remove_historical_placeholder_before_replacement": name == "FRONT"})
            self.owner["states"].append({"name": state, "occurrence_ids": ids, "layer_count": 2})
            self.facts["states"].append({"name": state, "occurrence_ids": ids, "layer_count": 2})
            self.decision["states"].append({"name": state, "ordered_occurrence_ids": ids,
                "diagnostic_recipe_explicit": True, "game_state_acceptance": False,
                "visible_slot_ids": [2, 5], "native_draw_order_indices": [0, 7]})
        self.manifest = self.root / "manifest.json"
        self.seal()

    def tearDown(self):
        if os.path.lexists(self.out):
            assert self.out.absolute().parent == composer.OUTPUT_BASE.absolute()
            if getattr(self.out.lstat(), "st_file_attributes", 0) & 0x400:
                os.rmdir(self.out)
            else:
                assert self.out.resolve().parent == composer.OUTPUT_BASE.resolve()
                shutil.rmtree(self.out)
        assert self.root.resolve().is_relative_to((PRODUCT / "build/tests").resolve())
        self.temp.cleanup()

    def seal(self):
        self.owner["frozen_scene_facts"] = save(self.root / "facts.json", self.facts)
        self.decision["input_facts"] = self.owner["frozen_scene_facts"]
        self.owner["explicit_diagnostic_state_authority"] = {
            "approved_for_art_diagnostic_by_root": True, "state_version": "UNIT_RECIPE_V1",
            "state_manifest": save(self.root / "decision.json", self.decision)}
        owner_ref = save(self.root / "owner.json", self.owner)
        self.batch = {"schema": composer.SCHEMA, "operation_id": self.operation,
                      "scene_inputs": owner_ref, "official_inventory": reference(self.inventory)}
        self.manifest_ref = save(self.manifest, self.batch)

    def invoke(self, action):
        return composer.execute(action, self.manifest, self.manifest_ref["sha256"],
                                self.out, self.operation, self.binding)

    def file_state(self):
        return {p.name: (digest(p.read_bytes()), p.stat().st_mtime_ns)
                for p in self.out.iterdir() if p.name != ".operation.lock"}

    def test_actual_alpha_composition_order_origin_and_zero_new_1x(self):
        sources = {p.name: digest(p.read_bytes()) for p in self.root.iterdir() if p.suffix == ".png"}
        check = self.invoke("check")
        self.assertFalse(self.out.exists())
        self.assertEqual(check["summary"]["ready_occurrences"], 4)
        result = self.invoke("run")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        for state in ("base", "upgraded"):
            with Image.open(self.out / f"scene-{state}-3x.png") as image:
                self.assertEqual(image.size, (24, 18))
                self.assertEqual(image.getpixel((6, 6)), (200, 100, 50, 1))
                self.assertEqual(image.getpixel((7, 7)), (100, 50, 125, 255))
                self.assertEqual(image.getpixel((10, 10))[3], 0)
        self.assertEqual(len(list(self.out.glob("*.png"))), 2)
        self.assertFalse(list(self.out.glob("*1x*")))
        report = json.loads((self.out / "scene-report.json").read_bytes())
        self.assertEqual(report["sampling_steps"], 0)
        self.assertTrue(all(v is False for v in report["authority"].values()))
        self.assertEqual(sources, {p.name: digest(p.read_bytes()) for p in self.root.iterdir() if p.suffix == ".png"})

    def test_png_or_authority_sha_drift_fails_before_output(self):
        for target in ("BACK.png", "art.json", "decision.json"):
            with self.subTest(target=target):
                path = self.root / target
                before = path.read_bytes()
                path.write_bytes(before + b"drift")
                with self.assertRaisesRegex(native.ReviewError, "SHA256_DRIFT"):
                    self.invoke("run")
                self.assertFalse(self.out.exists())
                path.write_bytes(before)

    def test_byte_preserved_delivery_can_replace_only_the_frozen_placeholder(self):
        role = "EXISTING_EXACT3X_DELIVERY_BYTE_PRESERVED"
        self.owner["assets"]["def:FRONT:0:0"]["source_role"] = role
        for row in self.owner["occurrences"]:
            if row["key"][1] == "FRONT":
                row["source_role"] = role
        self.seal()
        self.assertEqual(self.invoke("run")["status"], "REVIEW_REQUIRED")
        preflight = json.loads((self.out / "preflight.json").read_bytes())
        self.assertTrue(preflight["states"]["base"][1]["replaced_placeholder"])
        self.assertEqual(preflight["assets"]["def:FRONT:0:0"]["source_role"], role)

    def test_manifest_unknown_field_wrong_operation_and_partial_source_rejected(self):
        self.batch["unknown"] = True
        self.manifest_ref = save(self.manifest, self.batch)
        with self.assertRaisesRegex(native.ReviewError, "FIELDS_INVALID"):
            self.invoke("check")
        del self.batch["unknown"]
        self.batch["operation_id"] = "different"
        self.manifest_ref = save(self.manifest, self.batch)
        with self.assertRaisesRegex(native.ReviewError, "OPERATION_ID_MISMATCH"):
            self.invoke("run")
        self.assertFalse(self.out.exists())

    def test_unknown_duplicate_and_shifted_geometry_or_visible_set_rejected(self):
        baseline = copy.deepcopy(self.owner)
        mutations = [
            lambda o: o["occurrences"].append(copy.deepcopy(o["occurrences"][0])),
            lambda o: o["occurrences"][0].update(occurrence_id="base:UNKNOWN"),
            lambda o: o["occurrences"][0].update(native_global_xy=[2, 1]),
            lambda o: o["occurrences"][0].update(add_crop_origin_again=True),
            lambda o: o["occurrences"][0]["native_geometry"].update(origin=[0, 0]),
            lambda o: o["states"][0]["occurrence_ids"].reverse(),
            lambda o: o.update(viewport_3x=[8, 6]),
            lambda o: o["occurrences"][1].update(remove_historical_placeholder_before_replacement=False),
        ]
        for mutation in mutations:
            self.owner = copy.deepcopy(baseline)
            mutation(self.owner)
            self.seal()
            with self.assertRaises(native.ReviewError):
                self.invoke("run")
            self.assertFalse(self.out.exists())

    def test_missing_higher_master_records_exact_holds_and_no_scene_png(self):
        bg = self.root / "low-bg.png"
        Image.new("RGB", (20, 12), "black").save(bg)
        self.owner["background"].update(highest_source=reference(bg), actual_pixels=[20, 12],
                                        status="NEEDS_HIGHER_MASTER", usable_3x_source=None)
        del self.owner["assets"]["def:FRONT:0:0"]
        for row in self.owner["occurrences"]:
            if row["key"][1] == "FRONT":
                row.update(asset_id=None, status="NEEDS_HIGHER_MASTER", source_role="NEEDS_HIGHER_MASTER")
        self.seal()
        check = self.invoke("check")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertEqual(len(check["blockers"]), 3)
        self.assertFalse(self.out.exists())
        run = self.invoke("run")
        self.assertTrue(run["blocked"])
        self.assertEqual(run["summary"]["ready_occurrences"], 2)
        self.assertEqual(run["summary"]["missing_occurrences"], 2)
        self.assertFalse(list(self.out.glob("*.png")))
        self.assertEqual(self.invoke("verify")["status"], "VERIFIED_BLOCKED")

    def test_same_operation_reuses_and_verify_tamper_fails_without_writes(self):
        self.invoke("run")
        before = self.file_state()
        self.assertTrue(self.invoke("run")["reused"])
        self.assertEqual(before, self.file_state())
        self.assertEqual(self.invoke("verify")["status"], "VERIFIED_REVIEW_REQUIRED")
        self.assertEqual(before, self.file_state())
        target = self.out / "scene-base-3x.png"
        target.write_bytes(target.read_bytes()[:-8])
        broken = self.file_state()
        with self.assertRaisesRegex(native.ReviewError, "DIVERGED"):
            self.invoke("run")
        self.assertEqual(broken, self.file_state())

    def test_changed_payload_cannot_reuse_old_operation(self):
        self.invoke("run")
        before = self.file_state()
        self.binding = {"qualification": "different_source_revision"}
        with self.assertRaisesRegex(native.ReviewError, "PAYLOAD_CHANGED"):
            self.invoke("run")
        self.assertEqual(before, self.file_state())

    def test_resuming_only_a_report_counts_zero_new_pngs(self):
        self.invoke("run")
        pngs = {p.name: (digest(p.read_bytes()), p.stat().st_mtime_ns) for p in self.out.glob("*.png")}
        (self.out / "index.html").unlink()
        result = self.invoke("run")
        self.assertEqual(result["files_written"], 1)
        self.assertEqual(result["new_png_files"], 0)
        self.assertEqual(pngs, {p.name: (digest(p.read_bytes()), p.stat().st_mtime_ns) for p in self.out.glob("*.png")})

    def test_foreign_operation_root_is_rejected_without_creating_a_lease(self):
        composer.OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        self.out.mkdir()
        (self.out / "foreign.txt").write_text("unowned", encoding="utf-8")
        before = self.file_state()
        with self.assertRaisesRegex(native.ReviewError, "UNKNOWN_OR_DIVERGENT"):
            self.invoke("run")
        self.assertEqual(set(p.name for p in self.out.iterdir()), {"foreign.txt"})
        self.assertEqual(before, self.file_state())

    def test_interrupted_output_prefix_is_completed_without_rewriting_other_files(self):
        self.invoke("run")
        target = self.out / "scene-upgraded-3x.png"
        raw = target.read_bytes()
        before = self.file_state()
        target.unlink()
        pending = self.out / f".{target.name}.{digest(raw)[:16]}.pending"
        pending.write_bytes(raw[:15])
        with self.assertRaisesRegex(native.ReviewError, "INCOMPLETE"):
            self.invoke("verify")
        self.invoke("run")
        self.assertEqual(target.read_bytes(), raw)
        self.assertFalse(pending.exists())
        after = self.file_state()
        self.assertTrue(all(after[k] == v for k, v in before.items() if k != target.name))
        self.assertEqual(self.invoke("verify")["status"], "VERIFIED_REVIEW_REQUIRED")

    def test_empty_operation_prefix_recovers_and_divergent_prefix_blocks(self):
        preflight, _, images, bg = composer.prepare(self.manifest, self.manifest_ref["sha256"],
                                                    self.operation, self.binding)
        files, _ = composer.artifacts(preflight, images, bg)
        guard = native.OutputRootGuard.create(self.out, composer.OUTPUT_BASE)
        try:
            with native.operation_lease(guard, create=True):
                name = "operation.json"
                pending = self.out / f".{name}.{digest(files[name])[:16]}.pending"
                pending.write_bytes(b"")
        finally:
            guard.close()
        self.assertEqual(self.invoke("run")["status"], "REVIEW_REQUIRED")
        target = self.out / "index.html"; raw = target.read_bytes(); target.unlink()
        pending = self.out / f".index.html.{digest(raw)[:16]}.pending"
        pending.write_bytes(b"foreign")
        before = self.file_state()
        with self.assertRaisesRegex(native.ReviewError, "PENDING_BYTES_DIVERGED"):
            self.invoke("run")
        self.assertEqual(before, self.file_state())

    def test_another_process_cannot_write_same_operation_and_foreign_files_preserved(self):
        self.invoke("run")
        before = self.file_state()
        guard = native.OutputRootGuard.open_existing(self.out, composer.OUTPUT_BASE)
        code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "import town_scene_composer as c; "
                "c.execute('run',Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]),sys.argv[5],"
                "{'qualification':'synthetic_source_contract_only'})")
        try:
            with native.operation_lease(guard, create=False):
                child = subprocess.run([sys.executable, "-c", code, str(PRODUCT / "scripts"),
                    str(self.manifest), self.manifest_ref["sha256"], str(self.out), self.operation],
                    capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn("OPERATION_IN_PROGRESS", child.stderr)
        finally:
            guard.close()
        self.assertEqual(before, self.file_state())
        (self.out / "foreign.txt").write_text("keep me", encoding="utf-8")
        with self.assertRaisesRegex(native.ReviewError, "UNKNOWN_OR_DIVERGENT"):
            self.invoke("run")
        self.assertEqual((self.out / "foreign.txt").read_text(), "keep me")


if __name__ == "__main__":
    unittest.main(verbosity=2)
