"""Source-only contracts for the receipt-bound motion review companion."""
from __future__ import annotations

import hashlib
import copy
import importlib.util
import json
import gzip
import sys
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_motion_sequence.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("review_motion_sequence", SCRIPT)
assert spec and spec.loader
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def file_ref(path: Path) -> dict:
    return {"path": str(path), "sha256": reviewer.sha256(path)}


class MotionVisualReviewContract(unittest.TestCase):
    def setUp(self) -> None:
        output_base = SCRIPT.parents[1] / "build/experiments/motion-review"
        output_base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="motion-visual-review-", dir=output_base)
        self.root = Path(self.temp.name)
        self.report = self.root / "run.json"
        self.targets = {"canvas_1x": [8, 8], "crop_1x": [4, 4], "origin_1x": [2, 2]}
        self.preflight = []
        self.results = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_frame(self, frame: int, shift: int = 0, faint_island: bool = False) -> Path:
        identity = f"F{frame}"
        output = self.root / f"{identity}.png"
        rgba = np.zeros((12, 12, 4), dtype=np.uint8)
        rgba[2:10, 2 + shift:9 + shift] = (30, 120, 210, 255)
        if faint_island:
            rgba[0, 11] = (230, 90, 20, 1)
        Image.fromarray(rgba, "RGBA").save(output)
        key = ["def", "TEST", 0, frame]
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        receipt = self.root / f"{identity}.json"
        value = {"schema": "h3.motion.receipt.v1", "job_fingerprint": fingerprint,
                 "contract": {"entry": {"id": identity, "key": key, "targets": self.targets},
                              "native_entry": {"key": key, "targets": self.targets}},
                 "output": str(output), "output_pixels": [12, 12], "origin_3x": [6, 6],
                 "png_sha256": reviewer.sha256(output)}
        value.update({flag: False for flag in reviewer.AUTHORITY_FLAGS})
        save(receipt, value)
        self.preflight.append({"id": identity, "status": "READY", "output": str(output),
                               "output_pixels": [12, 12], "fingerprint": fingerprint})
        self.results.append({"id": identity, "status": "DERIVED_VERIFIED_REVIEW_REQUIRED",
                             "output": str(output), "receipt": str(receipt)})
        return output

    def seal(self) -> None:
        save(self.report, {"schema": "h3.motion.run.v1", "status": "COMPLETED", "failed": 0,
                           "preflight": {"items": self.preflight}, "results": self.results})

    def collection_fixture(self, *, same_ids=False, hold=True, reuse=False):
        self.add_frame(0, faint_island=True)
        self.add_frame(1, shift=1)
        if reuse:
            Path(self.results[1]["output"]).write_bytes(Path(self.results[0]["output"]).read_bytes())
        rows, native_entries = [], []
        for frame in range(3 if hold else 2):
            rows.append({"kind": "def", "identity": "TEST", "group": 0, "frame": frame,
                         "canvas": [8, 8], "crop": [4, 4], "anchor": [2, 2]})
            eq = 0 if reuse and frame == 1 else frame
            native_entries.append({"key": ["def", "TEST", 0, frame], "targets": copy.deepcopy(self.targets),
                                   "equivalence_class": hashlib.sha256(f"native-{eq}".encode()).hexdigest()})
        inventory = self.root / "collection-inventory.json.gz"
        inventory.write_bytes(gzip.compress(json.dumps({"rows": rows}).encode(), mtime=0))
        lod = self.root / "original-container.bin"
        lod.write_bytes(b"synthetic original-container pin; no real game claim")
        parent = self.root / "original-projection.json"
        save(parent, {"schema": "h3.native-slot.projection.v1", "native_lod": file_ref(lod)})
        projection = self.root / "collection-projection.json"
        save(projection, {"schema": "h3.motion.native-slot-projection.v1",
                          "projection_source": file_ref(parent), "entries": native_entries})
        facts = {"schema": "h3.map-ui.phoenix21-qa-candidate-bindings.v1", "native_projection": file_ref(projection),
                 "current_original_lod_sha256_verified": reviewer.sha256(lod), "items": [],
                 "actual_operation_journals": [], "full_animation_art_approved": False,
                 "native_timing_approved": False, "ownership_approved": False, "formal_intake": False,
                 "game_written": False}
        for frame in range(2):
            receipt_path = Path(self.results[frame]["receipt"])
            receipt = reviewer.read_json(receipt_path)
            if same_ids:
                receipt["contract"]["entry"]["id"] = "shared-id"
                self.preflight[frame]["id"] = self.results[frame]["id"] = "shared-id"
            entry = receipt["contract"]["entry"]
            entry["mode"] = "reuse" if reuse and frame == 1 else "mesh"
            if entry["mode"] == "reuse":
                entry["source"] = {**file_ref(Path(self.results[0]["output"])), "key": ["def", "TEST", 0, 0],
                                   "pixels": [12, 12], "native_scale": 3}
            receipt["contract"]["native_entry"] = native_entries[frame]
            receipt["png_sha256"] = reviewer.sha256(Path(self.results[frame]["output"]))
            save(receipt_path, receipt)
            spec_path = self.root / f"collection-spec-{frame}.json"
            save(spec_path, {"schema": "h3.motion.spec.v1", "items": [entry], "native_contracts": [file_ref(projection)]})
            run_path = self.root / f"actual-run-{frame}.json"
            run = {"schema": "h3.motion.run.v1", "status": "COMPLETED",
                   "preflight": {"items": [self.preflight[frame]]}, "results": [self.results[frame]]}
            save(run_path, run)
            stdout = run_path.read_text(encoding="utf-8")
            journal_path = self.root / f"actual-journal-{frame}.json"
            save(journal_path, {"operation_id": f"original-{frame}", "stages": [
                {"stage": stage, "state": "TERMINAL", "exit_code": 0, "stdout_utf8": stdout if stage == "motion-run" else "{}",
                 "stdout_sha256": hashlib.sha256((stdout if stage == "motion-run" else "{}").encode()).hexdigest()}
                for stage in ["motion-plan", "motion-check", "motion-run", "motion-verify"]]})
            png = Path(self.results[frame]["output"])
            with Image.open(png) as image:
                rgba = image.convert("RGBA")
            full = Image.new("RGBA", (24, 24)); full.paste(rgba, (6, 6))
            facts["items"].append({"frame": frame, "key": ["def", "TEST", 0, frame],
                "native_targets": copy.deepcopy(self.targets), "native_equivalence_class": native_entries[frame]["equivalence_class"],
                "kind": "exact-original-equivalent-alias" if reuse and frame == 1 else "keyframe-registration",
                "mode": entry["mode"], "receipt": file_ref(receipt_path), "actual_run_report": file_ref(run_path),
                "source_spec": file_ref(spec_path), "png": {**file_ref(png), "pixels": [12, 12],
                    "rgba_sha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
                    "full_canvas_rgba_sha256": hashlib.sha256(full.tobytes()).hexdigest()}})
            facts["actual_operation_journals"].append(file_ref(journal_path))
        self.collection_facts = facts
        self.collection_facts_path = self.root / "collection-bindings.json"
        self.collection_manifest = self.root / "collection-manifest.json"
        self.collection_out = self.root / "collection-view"
        self.collection_batch = {"schema": "h3.motion.review-collection.v1", "operation_id": self.collection_out.name,
                                 "official_inventory": file_ref(inventory)}
        self.seal_collection()
        return self.collection_manifest

    def seal_collection(self):
        save(self.collection_facts_path, self.collection_facts)
        self.collection_batch["slot_bindings"] = file_ref(self.collection_facts_path)
        save(self.collection_manifest, self.collection_batch)

    def review_collection(self):
        return reviewer.review(self.collection_manifest, self.collection_out, collection=True,
                               expected_run_sha256=reviewer.sha256(self.collection_manifest))

    def test_collection_keeps_real_runs_hold_geometry_and_portable_images(self):
        self.collection_fixture()
        before = {p.name: reviewer.sha256(p) for p in self.root.glob("*.png")}
        result = self.review_collection()
        report = reviewer.read_json(Path(result["report"]))
        self.assertEqual(report["schema"], "h3.motion.collection.visual-review.v1")
        self.assertFalse(report["combined_run_report_created"])
        self.assertEqual(len(report["producer_reports"]), 2)
        self.assertEqual(report["sequence_review"]["summary"]["hold_slots"], 1)
        self.assertEqual(report["sequence_review"]["summary"]["actual_operations"], 2)
        hold = report["sequence_review"]["groups"][0]["frames"][-1]
        self.assertEqual(hold["canvas_1x"], [8, 8])
        self.assertEqual(hold["origin_1x"], [2, 2])
        page = Path(result["html"]).read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", page)
        self.assertNotIn('src="../', page)
        self.assertFalse(report["sequence_review"]["independent_native_1x_exported"])
        self.assertEqual(before, {p.name: reviewer.sha256(p) for p in self.root.glob("*.png")})

    def test_public_cli_cannot_emit_an_unbound_renderer_review(self):
        self.add_frame(0)
        self.seal()
        out = self.root / "unbound-cli"
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--run-report", str(self.report),
                                 "--outdir", str(out)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--renderer", result.stderr)
        self.assertFalse(out.exists())

    def test_public_cli_error_path_serializes_failure_without_name_error(self):
        self.add_frame(0)
        self.seal()
        renderer = self.root / "renderer.exe"
        renderer.write_bytes(b"synthetic renderer identity")
        out = self.root / "error-path"
        result = subprocess.run([
            sys.executable, "-B", str(SCRIPT), "--run-report", str(self.report),
            "--run-report-sha256", reviewer.sha256(self.report),
            "--renderer", str(renderer), "--renderer-sha256", reviewer.sha256(renderer),
            "--outdir", str(out),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('"schema": "h3.motion.visual-review.error.v1"', result.stderr)
        self.assertNotIn("NameError", result.stderr)
        self.assertFalse(out.exists())

    def test_collection_same_id_in_separate_operations_is_not_collapsed(self):
        self.collection_fixture(same_ids=True, hold=False)
        result = self.review_collection()
        report = reviewer.read_json(Path(result["report"]))
        self.assertEqual(len(set(r["review_id"] for r in report["frames"])), 2)
        self.assertEqual([r["id"] for r in report["frames"]], ["shared-id", "shared-id"])
        self.assertEqual(Path(result["html"]).read_text(encoding="utf-8").count('<article class="frame">'), 2)
        self.assertFalse(report["sequence_review"]["groups"][0]["native_loop_verified"])

    def test_collection_requires_pinned_manifest_and_cannot_grant_art_authority(self):
        self.collection_fixture()
        with self.assertRaisesRegex(ValueError, "SHA256_REQUIRED"):
            reviewer.review(self.collection_manifest, self.collection_out, collection=True)
        self.assertFalse(self.collection_out.exists())
        self.collection_facts["full_animation_art_approved"] = True
        self.seal_collection()
        with self.assertRaisesRegex(ValueError, "AUTHORITY_ESCALATION"):
            self.review_collection()
        self.assertFalse(self.collection_out.exists())

    def test_collection_duplicate_or_wrong_geometry_cannot_invent_original_slot(self):
        self.collection_fixture()
        original = copy.deepcopy(self.collection_facts)
        for duplicate in [False, True]:
            self.collection_facts = copy.deepcopy(original)
            if duplicate:
                self.collection_facts["items"].append(copy.deepcopy(self.collection_facts["items"][0]))
            else:
                self.collection_facts["items"][0]["native_targets"]["origin_1x"] = [0, 0]
            self.seal_collection()
            with self.assertRaisesRegex(ValueError, "GEOMETRY|DUPLICATE_NATIVE_SLOT"):
                self.review_collection()
            self.assertFalse(self.collection_out.exists())

    def test_collection_resigned_spec_drift_cannot_rebind_actual_receipt(self):
        self.collection_fixture()
        item = self.collection_facts["items"][0]
        path = Path(item["source_spec"]["path"])
        spec = reviewer.read_json(path); spec["items"][0]["time"] = 0.5; save(path, spec)
        item["source_spec"] = file_ref(path); self.seal_collection()
        with self.assertRaisesRegex(ValueError, "SPEC_RECEIPT_DRIFT"):
            self.review_collection()
        self.assertFalse(self.collection_out.exists())

    def test_collection_failed_journal_and_png_drift_are_blocked(self):
        self.collection_fixture()
        ref = self.collection_facts["actual_operation_journals"][0]
        path = Path(ref["path"]); doc = reviewer.read_json(path); doc["stages"][-1]["exit_code"] = 1; save(path, doc)
        self.collection_facts["actual_operation_journals"][0] = file_ref(path); self.seal_collection()
        with self.assertRaisesRegex(ValueError, "NOT_TERMINAL_EXIT_ZERO"):
            self.review_collection()
        self.assertFalse(self.collection_out.exists())
        doc["stages"][-1]["exit_code"] = 0; save(path, doc)
        self.collection_facts["actual_operation_journals"][0] = file_ref(path); self.seal_collection()
        Path(self.results[0]["output"]).write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "FRAME_BLOCKED"):
            self.review_collection()
        self.assertFalse(self.collection_out.exists())

    def test_collection_exact_alias_uses_declared_original_equivalence(self):
        self.collection_fixture(reuse=True, hold=False)
        result = self.review_collection()
        report = reviewer.read_json(Path(result["report"]))
        self.assertEqual(report["sequence_review"]["summary"]["unique_owner_images"], 1)
        self.assertEqual(report["sequence_review"]["groups"][0]["frames"][1]["owner_key"], ["def", "TEST", 0, 0])
        self.assertEqual(report["sequence_review"]["summary"]["complete_groups"], 1)

    def test_collection_same_png_cannot_replace_declared_native_equivalence(self):
        self.collection_fixture(reuse=True, hold=False)
        projection_path = Path(self.collection_facts["native_projection"]["path"])
        projection = reviewer.read_json(projection_path)
        projection["entries"][1]["equivalence_class"] = "f" * 64
        save(projection_path, projection)
        projection_ref = file_ref(projection_path)
        self.collection_facts["native_projection"] = projection_ref
        for index, item in enumerate(self.collection_facts["items"]):
            spec_path = Path(item["source_spec"]["path"])
            spec = reviewer.read_json(spec_path); spec["native_contracts"] = [projection_ref]; save(spec_path, spec)
            item["source_spec"] = file_ref(spec_path)
            if index == 1:
                receipt_path = Path(item["receipt"]["path"])
                receipt = reviewer.read_json(receipt_path)
                receipt["contract"]["native_entry"] = projection["entries"][1]
                save(receipt_path, receipt)
                item["receipt"] = file_ref(receipt_path)
                item["native_equivalence_class"] = "f" * 64
        self.seal_collection()
        with self.assertRaisesRegex(ValueError, "ALIAS_ORIGINAL_EQUIVALENCE_DRIFT"):
            self.review_collection()
        self.assertFalse(self.collection_out.exists())

    def native_preview_fixture(self, *, hold_slots: bool = True) -> Path:
        a = self.add_frame(0)
        b = self.add_frame(1)
        b.write_bytes(a.read_bytes())
        receipt = reviewer.read_json(Path(self.results[1]["receipt"]))
        receipt["png_sha256"] = reviewer.sha256(b)
        receipt["contract"]["entry"]["targets"]["origin_1x"] = [1, 2]
        receipt["contract"]["native_entry"]["targets"]["origin_1x"] = [1, 2]
        receipt["origin_3x"] = [3, 6]
        save(Path(self.results[1]["receipt"]), receipt)
        self.seal()
        rows = []
        slots = [(0, 0), (0, 1), (0, 2), (1, 0)] if hold_slots else [(0, 0), (0, 1)]
        for group, frame in slots:
            rows.append({"kind": "def", "identity": "TEST", "group": group, "frame": frame,
                         "canvas": [8, 8], "crop": [4, 4], "anchor": [1, 2] if (group, frame) == (0, 1) else [2, 2]})
        inventory = self.root / "native-inventory.json.gz"
        inventory.write_bytes(gzip.compress(json.dumps({"rows": rows}).encode("utf-8")))
        canvas = Image.new("RGBA", (24, 24))
        with Image.open(a) as crop:
            canvas.paste(crop.convert("RGBA"), (6, 6))
        image = self.root / "native-owner-3x.png"
        canvas.save(image)
        report = {"schema": "h3.native-review.batch-report.v1",
                  "official_inventory": {"path": str(inventory), "sha256": reviewer.sha256(inventory)},
                  "authority": {flag: False for flag in ("art_approved", "human_approved", "runtime_accepted", "game_deployed")},
                  "items": [{"key": ["def", "TEST", 0, 0], "targets": self.targets,
                             "source_png": str(a), "source_sha256": reviewer.sha256(a),
                             "source_pixels": [12, 12], "source_rect": [0, 0, 12, 12], "registration": "strict-aspect",
                             "outputs": {"candidate_3x": {"path": str(image), "pixels": [24, 24],
                                                        "png_sha256": reviewer.sha256(image),
                                                        "rgba_sha256": hashlib.sha256(canvas.tobytes()).hexdigest()}}}]}
        native = self.root / "native-review.json"
        save(native, report)
        return native

    def test_native_sequence_tracks_aliases_origin_and_hold_slots_without_new_images(self) -> None:
        native = self.native_preview_fixture()
        images_before = {str(p): reviewer.sha256(p) for p in self.root.glob("*.png")}
        result = reviewer.review(self.report, self.root / "sequence", expected_run_sha256=reviewer.sha256(self.report),
                                 native_report=native, expected_native_sha256=reviewer.sha256(native))
        sequence = reviewer.read_json(Path(result["report"]))["sequence_review"]
        self.assertEqual(sequence["summary"], {"expected_slots": 4, "available_slots": 2, "hold_slots": 2,
                                             "unique_owner_images": 1, "slots_sharing_owner_images": 1, "complete_groups": 0})
        alias = sequence["groups"][0]["frames"][1]
        self.assertEqual(alias["key"], ["def", "TEST", 0, 1])
        self.assertEqual(alias["owner_key"], ["def", "TEST", 0, 0])
        self.assertEqual(alias["origin_1x"], [1, 2])
        self.assertEqual(alias["source_rect_3x"], [6, 6, 12, 12])
        page = Path(result["html"]).read_text("utf-8")
        self.assertIn("data:image/png;base64,", page)
        self.assertIn("HOLD_MISSING_NATIVE_SLOTS", page)
        self.assertIn("Browser display", json.dumps(sequence))
        self.assertFalse(sequence["playback_visually_verified"])
        self.assertEqual({str(p): reviewer.sha256(p) for p in self.root.glob("*.png")}, images_before)
        self.assertEqual({p.name for p in (self.root / "sequence").iterdir()}, {"review.json", "index.html"})

    def test_native_sequence_complete_is_technical_and_stale_image_is_blocked(self) -> None:
        native = self.native_preview_fixture(hold_slots=False)
        result = reviewer.review(self.report, self.root / "complete", expected_run_sha256=reviewer.sha256(self.report),
                                 native_report=native, expected_native_sha256=reviewer.sha256(native))
        sequence = reviewer.read_json(Path(result["report"]))["sequence_review"]
        self.assertEqual(sequence["summary"]["complete_groups"], 1)
        self.assertFalse(sequence["whole_creature_art_accepted"])
        self.assertFalse(sequence["native_timing_verified"])
        image = self.root / "native-owner-3x.png"
        Image.new("RGBA", (24, 24), (9, 8, 7, 1)).save(image)
        with self.assertRaisesRegex(ValueError, "NATIVE_PREVIEW_PNG_SHA256_DRIFT"):
            reviewer.review(self.report, self.root / "stale", expected_run_sha256=reviewer.sha256(self.report),
                            native_report=native, expected_native_sha256=reviewer.sha256(native))
        self.assertFalse((self.root / "stale").exists())
        with self.assertRaisesRegex(ValueError, "RUN_REPORT_SHA256_REQUIRED"):
            reviewer.review(self.report, self.root / "unpinned", native_report=native,
                            expected_native_sha256=reviewer.sha256(native))
        self.assertFalse((self.root / "unpinned").exists())

    def test_native_canvas_onion_layers_and_adjacency_without_image_copy(self) -> None:
        a = self.add_frame(0)
        b = self.add_frame(1, shift=1)
        originals = {path: reviewer.sha256(path) for path in (a, b)}
        self.seal()
        result = reviewer.review(self.report, self.root / "review")
        self.assertEqual((result["status"], result["verified_frames"], result["blocked_frames"]),
                         ("REVIEW_READY", 2, 0))
        record = reviewer.read_json(Path(result["report"]))
        self.assertEqual(record["groups"][0]["frame_ids"], ["F0", "F1"])
        self.assertAlmostEqual(record["adjacent_diagnostics"][0]["alpha_center_delta_1x"], 1 / 3, places=3)
        self.assertLess(record["adjacent_diagnostics"][0]["alpha_ge16_iou_on_native_canvas"], 1)
        page = Path(result["html"]).read_text(encoding="utf-8")
        self.assertIn('width:8px;height:8px', page)
        self.assertIn('left:2px;top:2px;width:4px;height:4px', page)
        self.assertIn('class="before"', page)
        self.assertIn('class="after"', page)
        self.assertEqual({path: reviewer.sha256(path) for path in originals}, originals)
        self.assertEqual(len(list((self.root / "review").iterdir())), 2)
        with self.assertRaises(FileExistsError):
            reviewer.review(self.report, self.root / "review")

    def test_alpha_one_is_reported_but_preserved(self) -> None:
        output = self.add_frame(0, faint_island=True)
        before = reviewer.sha256(output)
        self.seal()
        result = reviewer.review(self.report, self.root / "review")
        row = reviewer.read_json(Path(result["report"]))["frames"][0]
        self.assertEqual(row["alpha_eq1_outside_ge16_box_pixels"], 1)
        self.assertIn("FAINT_ALPHA1_OUTSIDE_MAIN_ART", row["review_hints"])
        self.assertEqual(reviewer.sha256(output), before)

    def test_changed_png_is_blocked_and_not_embedded_in_html(self) -> None:
        output = self.add_frame(0)
        self.seal()
        with Image.open(output) as image:
            modified = image.copy()
        modified.putpixel((0, 0), (255, 255, 255, 255))
        modified.save(output)
        result = reviewer.review(self.report, self.root / "review")
        self.assertEqual((result["status"], result["blocked_frames"]), ("SOURCE_BLOCKED", 1))
        record = reviewer.read_json(Path(result["report"]))
        self.assertIn("SHA256 drift", record["frames"][0]["errors"][0])
        page = Path(result["html"]).read_text(encoding="utf-8")
        self.assertIn("SHA256 drift", page)
        self.assertNotIn('src="../F0.png"', page)

    def test_eight_connected_diagonal_and_island_are_distinct(self) -> None:
        mask = np.zeros((5, 5), dtype=bool)
        mask[0, 0] = mask[1, 1] = mask[4, 4] = True
        self.assertEqual(reviewer.component_sizes(mask), ([2, 1], False))

    def test_static_twenty_one_frames_are_a_review_hint_not_motion_approval(self) -> None:
        for frame in range(21):
            self.add_frame(frame)
        self.seal()
        result = reviewer.review(self.report, self.root / 'review')
        record = reviewer.read_json(Path(result['report']))
        group = record['groups'][0]
        self.assertEqual(group['unique_visible_rgba_native_canvas_frames'], 1)
        self.assertIn('STATIC_SEQUENCE_REVIEW_REQUIRED', group['review_hints'])
        self.assertFalse(record['art_approved'])
        self.assertFalse(record['native_timing_verified'])

    def test_two_pixel_alternation_is_reported_as_near_static(self) -> None:
        paths = []
        for frame in range(21):
            path = self.add_frame(frame)
            if frame % 2:
                with Image.open(path) as image:
                    image.putpixel((3, 3), (220, 10, 40, 255))
                    image.putpixel((4, 3), (220, 10, 40, 255))
                    image.save(path)
                receipt = self.root / f'F{frame}.json'
                doc = reviewer.read_json(receipt)
                doc['png_sha256'] = reviewer.sha256(path)
                save(receipt, doc)
            paths.append(path)
        before = [reviewer.sha256(path) for path in paths]
        self.seal()
        result = reviewer.review(self.report, self.root / 'review')
        record = reviewer.read_json(Path(result['report']))
        group = record['groups'][0]
        self.assertEqual(group['unique_visible_rgba_native_canvas_frames'], 2)
        self.assertEqual(group['max_adjacent_changed_visible_rgba_pixels'], 2)
        self.assertIn('NEAR_STATIC_SEQUENCE_REVIEW_REQUIRED', group['review_hints'])
        self.assertTrue(all(x['changed_visible_rgba_pixels'] == 2 for x in record['adjacent_diagnostics']))
        self.assertEqual(before, [reviewer.sha256(path) for path in paths])

    def test_rgba_identity_uses_native_canvas_and_original_origin(self) -> None:
        a = self.add_frame(0)
        b = self.add_frame(1)
        rgba = np.zeros((12, 15, 4), dtype=np.uint8)
        rgba[2:10, 5:12] = (30, 120, 210, 255)
        Image.fromarray(rgba).save(b)
        receipt = self.root / 'F1.json'; doc = reviewer.read_json(receipt)
        targets = {'canvas_1x': [8, 8], 'crop_1x': [5, 4], 'origin_1x': [1, 2]}
        doc['contract']['entry']['targets'] = targets
        doc['contract']['native_entry']['targets'] = targets
        doc.update(output_pixels=[15, 12], origin_3x=[3, 6], png_sha256=reviewer.sha256(b))
        self.preflight[1]['output_pixels'] = [15, 12]; save(receipt, doc)
        self.seal()
        result = reviewer.review(self.report, self.root / 'review')
        record = reviewer.read_json(Path(result['report']))
        self.assertEqual(record['adjacent_diagnostics'][0]['changed_visible_rgba_pixels'], 0)
        self.assertEqual(record['groups'][0]['unique_visible_rgba_native_canvas_frames'], 1)

    def test_source_diagnostics_keep_alpha1_and_report_body_axis_scale(self) -> None:
        alpha = np.zeros((20, 30), dtype=np.uint8)
        alpha[5:15, 4:20] = 255
        alpha[0, 29] = 1
        native = np.zeros((10, 10), dtype=np.uint8)
        native[1:9, 3:7] = 255
        before = alpha.tobytes()
        geometry = {'canvas': [10, 10], 'crop': [10, 10], 'origin': [0, 0]}
        result = reviewer.source_shape_diagnostics(alpha, geometry, native)
        self.assertEqual(result['alpha128_largest_components'][0], {'pixels': 160, 'bbox_ltrb': [4, 5, 20, 15]})
        self.assertEqual(result['axis_scale_from_alpha128_bbox'], [4, 1.25])
        self.assertEqual(result['alpha1_pixels'], 1)
        self.assertEqual(result['alpha1_outside_alpha128_bbox_pixels'], 1)
        self.assertTrue(result['alpha1_enlarges_required_crop_k'])
        self.assertEqual(result['positive_bbox_ltrb'], [4, 0, 30, 15])
        self.assertFalse(result['semantic_body_or_foot_verified'])
        self.assertFalse(result['k_inferred_or_selected'])
        self.assertEqual(alpha.tobytes(), before)

    def test_source_diagnostics_distinguish_connected_alpha1_and_islands(self) -> None:
        alpha = np.zeros((9, 13), dtype=np.uint8)
        alpha[3:6, 3:6] = 255
        alpha[4, 4] = 1
        alpha[2, 2] = 2
        alpha[1, 0:2] = 1
        alpha[0, 11:13] = 1
        alpha[8, 11] = 1
        before = alpha.tobytes()
        geometry = {'canvas': [50, 40], 'crop': [4, 4], 'origin': [13, 17]}
        result = reviewer.source_shape_diagnostics(alpha, geometry)
        topology = result['alpha1_topology']
        self.assertEqual(topology['connectivity'], 8)
        self.assertEqual(topology['connected_to_alpha_ge2_component_pixels'], 3)
        self.assertEqual(topology['alpha1_only_component_pixels'], 3)
        self.assertEqual(topology['alpha1_only_component_count'], 2)
        self.assertFalse(topology['component_scan_truncated'])
        self.assertEqual(result['alpha1_pixels'], 6)
        self.assertEqual(result['alpha_ge2_proxy']['bbox_ltrb'], [2, 2, 6, 6])
        self.assertEqual(result['alpha_ge2_proxy']['pixels'], 9)
        self.assertFalse(result['semantic_body_or_foot_verified'])
        self.assertFalse(result['k_inferred_or_selected'])
        self.assertEqual(alpha.tobytes(), before)

    def test_source_diagnostics_report_weak_alpha2_support_as_proxy_only(self) -> None:
        alpha = np.zeros((6, 8), dtype=np.uint8)
        alpha[2:4, 3:5] = 255
        alpha[1, 1] = 2
        alpha[4, 6] = 127
        alpha[5, 7] = 1
        geometry = {'canvas': [8, 6], 'crop': [8, 6], 'origin': [0, 0]}
        result = reviewer.source_shape_diagnostics(alpha, geometry)
        proxy = result['alpha_ge2_proxy']
        self.assertEqual(result['alpha128_bbox_ltrb'], [3, 2, 5, 4])
        self.assertEqual(proxy['bbox_ltrb'], [1, 1, 7, 5])
        self.assertEqual(proxy['pixels'], 6)
        self.assertEqual(proxy['component_count'], 3)
        self.assertEqual(proxy['largest_components'][0], {'pixels': 4, 'bbox_ltrb': [3, 2, 5, 4]})
        self.assertEqual(result['alpha1_topology']['connected_to_alpha_ge2_component_pixels'], 1)
        self.assertFalse(result['semantic_body_or_foot_verified'])
        self.assertFalse(result['art_approved'])

    def test_source_diagnostics_keep_all_alpha1_and_empty_support_explicit(self) -> None:
        geometry = {'canvas': [6, 5], 'crop': [6, 5], 'origin': [0, 0]}
        for populated in (False, True):
            with self.subTest(populated=populated):
                alpha = np.zeros((5, 6), dtype=np.uint8)
                if populated:
                    alpha[0, 0] = alpha[1, 1] = alpha[4, 5] = 1
                before = alpha.tobytes()
                result = reviewer.source_shape_diagnostics(alpha, geometry)
                proxy = result['alpha_ge2_proxy']
                topology = result['alpha1_topology']
                self.assertIsNone(proxy['bbox_ltrb'])
                self.assertEqual(proxy['pixels'], 0)
                self.assertEqual(proxy['component_count'], 0)
                self.assertEqual(proxy['largest_components'], [])
                self.assertEqual(topology['connected_to_alpha_ge2_component_pixels'], 0)
                self.assertEqual(topology['alpha1_only_component_pixels'], 3 if populated else 0)
                self.assertEqual(topology['alpha1_only_component_count'], 2 if populated else 0)
                self.assertEqual(result['positive_pixels'], 3 if populated else 0)
                self.assertFalse(result['source_pixels_mutated'])
                self.assertFalse(result['art_approved'])
                self.assertEqual(alpha.tobytes(), before)

    def test_source_diagnostics_bound_new_component_output_to_sixteen(self) -> None:
        alpha = np.zeros((21, 21), dtype=np.uint8)
        alpha[::4, ::4] = 2
        geometry = {'canvas': [21, 21], 'crop': [21, 21], 'origin': [0, 0]}
        result = reviewer.source_shape_diagnostics(alpha, geometry)
        proxy = result['alpha_ge2_proxy']
        self.assertEqual(proxy['component_count'], 36)
        self.assertEqual(len(proxy['largest_components']), 16)
        self.assertTrue(proxy['component_output_truncated'])
        self.assertFalse(proxy['component_scan_truncated'])

    def test_source_diagnostics_mark_budget_exhaustion_unknown(self) -> None:
        alpha = np.array([[2, 0, 1, 0, 2]], dtype=np.uint8)
        geometry = {'canvas': [5, 1], 'crop': [5, 1], 'origin': [0, 0]}
        scanner = reviewer.component_regions

        def one_run(mask, **kwargs):
            return scanner(mask, max_runs=1, **kwargs)

        with mock.patch.object(reviewer, 'component_regions', side_effect=one_run):
            result = reviewer.source_shape_diagnostics(alpha, geometry)
        proxy = result['alpha_ge2_proxy']
        self.assertEqual(proxy['pixels'], 2)
        self.assertEqual(proxy['bbox_ltrb'], [0, 0, 5, 1])
        self.assertTrue(proxy['component_scan_truncated'])
        for field in ('component_count', 'largest_components', 'component_output_truncated'):
            self.assertIsNone(proxy[field])
        topology = result['alpha1_topology']
        self.assertTrue(topology['component_scan_truncated'])
        for field in ('connected_to_alpha_ge2_component_pixels', 'alpha1_only_component_pixels',
                      'alpha1_only_component_count'):
            self.assertIsNone(topology[field])
        self.assertEqual(result['alpha1_pixels'], 1)

    def test_source_diagnostics_do_not_add_owner_padding_or_crop_origin(self) -> None:
        alpha = np.zeros((4, 7), dtype=np.uint8)
        alpha[1:3, 2:5] = 2
        alpha[1, 3] = 255
        alpha[0, 6] = 1
        geometry = {'canvas': [30, 20], 'crop': [7, 4], 'origin': [11, 13]}
        raw = reviewer.source_shape_diagnostics(alpha, geometry)
        padded = reviewer.source_shape_diagnostics(np.pad(alpha, 1), geometry)
        self.assertEqual(raw['source_pixels'], [7, 4])
        self.assertEqual(padded['source_pixels'], [9, 6])
        self.assertEqual(raw['alpha_ge2_proxy']['bbox_ltrb'], [2, 1, 5, 3])
        self.assertEqual(padded['alpha_ge2_proxy']['bbox_ltrb'], [3, 2, 6, 4])
        self.assertEqual(raw['alpha1_topology'], padded['alpha1_topology'])
        native_view = reviewer.source_shape_diagnostics(alpha, geometry, natural_k=3, full_native_view=True)
        self.assertEqual(native_view['alpha_ge2_proxy'], raw['alpha_ge2_proxy'])
        self.assertEqual(native_view['alpha1_topology'], raw['alpha1_topology'])
        self.assertEqual(native_view['native_bottom_proxy_1x'], 1 / 3)
        self.assertIsNone(raw['declared_natural_k'])
        self.assertFalse(raw['semantic_body_or_foot_verified'])

    def test_all_hold_group_keeps_original_canvas_without_image_or_timing(self) -> None:
        sequence = {"groups": [{"identity": "TEST", "group": 0, "available_slots": 0,
            "expected_slots": 1, "hold_slots": 1, "status": "HOLD_MISSING_NATIVE_SLOTS",
            "frames": [{"key": ["def", "TEST", 0, 0], "canvas_1x": [450, 400],
                        "crop_1x": [38, 87], "origin_1x": [179, 180]}]}]}
        page = reviewer.native_sequence_html(sequence, {})
        self.assertIn('data-native-width="450" data-native-height="400"', page)
        self.assertIn("canvas.dataset.painted='false'", page)
        self.assertIn("if(!frame.image)return", page)
        self.assertNotIn("data:image", page)

    def test_component_scan_budget_is_explicit(self) -> None:
        mask = np.array([[True, False, True]], dtype=bool)
        self.assertEqual(reviewer.component_regions(mask, max_runs=1), ([], True))


class SourceCollectionReviewContract(unittest.TestCase):
    """Synthetic Source documents mirror the two actual local-export shapes."""

    def setUp(self):
        base = SCRIPT.parents[1] / "build/experiments/motion-review"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="TEST-source-collection-", dir=base)
        self.root = Path(self.temp.name)
        self.out = self.root / "source-review"
        self.manifest_path = self.root / "collection.json"
        self.root_path = self.root / "source-root.json"
        self.fit_path = self.root / "hold-fit.json"
        save(self.fit_path, {"status": "TEST_SOURCE_HOLD_NOT_A_TOOL_FIT_DECISION"})
        self.geometry = {}
        for frame in range(8):
            origin = [156 + frame, 167]
            if frame == 3:
                origin = [167, 166]
            if frame == 7:
                origin = [159, 168]
            target = {"canvas_1x": [450, 400], "crop_1x": [10 + frame % 2, 8], "origin_1x": origin}
            target.update({name + "_3x": [n * 3 for n in target[name + "_1x"]]
                           for name in ("canvas", "crop", "origin")})
            self.geometry[frame] = target
        self.ready = [0, 1, 2, 3, 6, 7]
        self.pngs = {n: self.root / f"Source-F{n}.png" for n in self.ready}
        for n, path in self.pngs.items():
            image = Image.new("RGBA", tuple(self.geometry[n]["crop_3x"]), (n + 30, 100, 180, 255))
            image.putpixel((0, 0), (255, 30, 40, 1))
            image.save(path)
        logical = [{"key": ["def", "TESTSOURCE", 0, n], "targets": self.geometry[n],
                    "art_owner_slot": [0, n], "equivalence_class": f"TEST-Source-{n}"} for n in range(8)]
        self.cohorts = []
        for attempt, numbers in ((10, [0, 1]), (18, [2, 3]), (15, [6, 7])):
            prefix = self.root / f"a{attempt}"
            original = [{"key": ["def", "TESTSOURCE", 0, n], "targets": copy.deepcopy(self.geometry[n])}
                        for n in numbers]
            cohort = {"attempt": attempt, "numbers": numbers,
                "original_path": prefix.with_name(prefix.name + "-original.json"),
                "request_path": prefix.with_name(prefix.name + "-request.json"),
                "placement_path": prefix.with_name(prefix.name + "-placement.json"),
                "export_path": prefix.with_name(prefix.name + "-export.json"),
                "original": {"entries": copy.deepcopy(original)},
                "request": {"schema": "h3.full-art.request.v1", "identity": "TESTSOURCE", "group": 0,
                    "attempt": attempt, "production_scale": 3, "frame_count": 2, "native_frame_count": 8,
                    "logical_slot_bindings": copy.deepcopy(logical), "original_slots": original},
                "placement": {"schema": "h3.creature.group-placement.v1", "common_scale": 9,
                    "frames": [{"key": ["def", "TESTSOURCE", 0, n],
                        "complete_target_geometry": copy.deepcopy(self.geometry[n]),
                        "common_source_pixels_per_native_pixel": 9, "output": str(self.pngs[n])} for n in numbers]},
                "export": {"schema": "h3.creature.local-batch-execution.v1",
                    "common_integer_k": 9, "target_scale": 3, "art_approved": False, "game_deployed": False,
                    "status": "TEST_SOURCE_LOCAL_EXECUTION_DECLARATION",
                    "stages": [{"argv": ["TEST_ONLY_NEVER_EXECUTE", stage], "exit_code": 0}
                               for stage in ("expand", "expand-verify")],
                    "outputs": [{"path": str(self.pngs[n]), "pixels": self.geometry[n]["crop_3x"]} for n in numbers]}}
            if attempt == 10:
                cohort["export"].update(request={}, placement={}, timing_verified=False, published_3x=False,
                    raw_native_bytes_unchanged=True, all_positive_alpha_partition_preserved=True)
            else:
                cohort["export"].update(inputs=[{"path": str(cohort["request_path"])},
                    {"path": str(cohort["placement_path"])}], native_timing_verified=False, manifest_writes=0)
            self.cohorts.append(cohort)
        self.selection = {"schema": "h3.creature.root-local-art-review.v1", "identity": "TESTSOURCE", "group": 0,
            "native_frames": self.ready.copy(), "frames": [],
            "review_boundary": {"actual_full_3x_frames_seen": self.ready.copy(),
                "actual_css_native_frames_seen": self.ready.copy(), "formal_projection_written": False,
                "whole_group_art_approved": False, "human_art_approved": False,
                "native_timing_verified": False, "game_deployed": False},
            "holds": {"native_frames": [4, 5], "reason": "Frozen TEST Source declaration only",
                      "fit": file_ref(self.fit_path)},
            "review_page": {"path": str(self.root / "MUST_NOT_READ_OLD_PLACEHOLDERS.html"), "sha256": "f" * 64}}
        self.manifest = {"schema": reviewer.SOURCE_COLLECTION_SCHEMA, "operation_id": self.out.name,
                         "source_review": {}}
        self.seal_source()

    def tearDown(self):
        self.temp.cleanup()

    def seal_source(self):
        selected = []
        for cohort in self.cohorts:
            save(cohort["original_path"], cohort["original"])
            cohort["request"].update(source_receipt=str(cohort["original_path"]),
                                     source_receipt_sha256=reviewer.sha256(cohort["original_path"]))
            save(cohort["request_path"], cohort["request"])
            cohort["placement"].update(request_path=str(cohort["request_path"]),
                                      request_sha256=reviewer.sha256(cohort["request_path"]))
            save(cohort["placement_path"], cohort["placement"])
            export = cohort["export"]
            if "inputs" in export:
                for value in export["inputs"]:
                    if isinstance(value, dict) and value.get("path") in (
                            str(cohort["request_path"]), str(cohort["placement_path"])):
                        value.update(file_ref(Path(value["path"])))
            else:
                for name in ("request", "placement"):
                    if isinstance(export.get(name), dict):
                        export[name].update(file_ref(cohort[name + "_path"]))
            for output in export["outputs"]:
                if isinstance(output, dict) and output.get("path") in {str(p) for p in self.pngs.values()}:
                    output.update(file_ref(Path(output["path"])))
            save(cohort["export_path"], export)
            for n in cohort["numbers"]:
                if n not in self.ready:
                    continue
                selected.append({"native_frame": n, "production_3x": file_ref(self.pngs[n]),
                    "pixels": self.geometry[n]["crop_3x"], "placement": file_ref(cohort["placement_path"]),
                    "local_export": file_ref(cohort["export_path"])})
        self.selection["frames"] = selected
        self.save_selection()

    def save_selection(self):
        save(self.root_path, self.selection)
        self.manifest["source_review"] = file_ref(self.root_path)
        save(self.manifest_path, self.manifest)

    def run_review(self):
        return reviewer.review(self.manifest_path, self.out, collection=True,
                               expected_run_sha256=reviewer.sha256(self.manifest_path), output_base=self.root)

    def enable_eight(self):
        for n in (4, 5):
            self.pngs[n] = self.root / f"Source-F{n}.png"
            Image.new("RGBA", tuple(self.geometry[n]["crop_3x"]), (n + 50, 130, 170, 255)).save(self.pngs[n])
        cohort = copy.deepcopy(self.cohorts[1])
        cohort.update(attempt=21, numbers=[4, 5])
        for field in ("original", "request", "placement", "export"):
            cohort[field + "_path"] = self.root / f"a21-{field}.json"
        original = [{"key": ["def", "TESTSOURCE", 0, n], "targets": copy.deepcopy(self.geometry[n])} for n in (4, 5)]
        cohort["original"] = {"entries": copy.deepcopy(original)}
        cohort["request"].update(attempt=21, original_slots=original)
        cohort["placement"].update(common_scale=10, frames=[
            {"key": ["def", "TESTSOURCE", 0, n], "complete_target_geometry": copy.deepcopy(self.geometry[n]),
             "common_source_pixels_per_native_pixel": 10, "output": str(self.pngs[n])} for n in (4, 5)])
        cohort["export"].update(common_integer_k=10, inputs=[
            {"path": str(cohort["request_path"])}, {"path": str(cohort["placement_path"])}],
            outputs=[{"path": str(self.pngs[n]), "pixels": self.geometry[n]["crop_3x"]} for n in (4, 5)])
        self.cohorts.append(cohort)
        self.seal_source()
        self.eight_path = self.root / "source-eight.json"
        self.eight = {"schema": "h3.creature.existing-3x-css-review-bindings.v1",
            "identity": "TESTSOURCE", "group": 0,
            **{flag: False for flag in ("human_art_approved", "whole_group_art_approved",
               "native_timing_verified", "game_deployed", "runtime_accepted", "art_approved")},
            "first_six_binding_authority": file_ref(self.root_path),
            "new_a21_local_export": file_ref(cohort["export_path"]),
            "logical_frame_binding_changes": [4, 5], "frames": [], "html_frame_bindings": [],
            "page": {"path": "MUST_NOT_READ_SOURCE_HTML", "sha256": "f" * 64}}
        for n in range(8):
            parent = next(c for c in self.cohorts if n in c["numbers"])
            png = file_ref(self.pngs[n])
            self.eight["frames"].append({"native_frame": n, "production_3x": png,
                "pixels": self.geometry[n]["crop_3x"], "placement": file_ref(parent["placement_path"]),
                "request": file_ref(parent["request_path"]), "targets": copy.deepcopy(self.geometry[n]),
                "attempt": parent["attempt"], "common_integer_k": parent["placement"]["common_scale"]})
            self.eight["html_frame_bindings"].append({"key": ["def", "TESTSOURCE", 0, n],
                "targets": copy.deepcopy(self.geometry[n]), "candidate": {**png, "url": "IGNORED_SOURCE_URL"},
                "previous": {"path": "MUST_NOT_READ_PREVIOUS_PNG"}, "original": {}})
        self.activate_eight()

    def activate_eight(self):
        save(self.eight_path, self.eight)
        self.manifest["source_review"] = file_ref(self.eight_path)
        save(self.manifest_path, self.manifest)

    def assert_rejected(self, pattern):
        with self.assertRaisesRegex((ValueError, reviewer.native.ReviewError, OSError), pattern):
            self.run_review()
        self.assertFalse(self.out.exists())

    def test_source_exact_existing_bytes_origin_explicit_holds_and_no_producer(self):
        before = {p: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with mock.patch.object(reviewer, "run_frames", side_effect=AssertionError("no fake motion run")), \
             mock.patch.object(reviewer, "inspect_frame", side_effect=AssertionError("no motion coercion")), \
             mock.patch.object(reviewer, "adjacent_stats", side_effect=AssertionError("no new metrics")), \
             mock.patch.object(reviewer, "review_image_urls", side_effect=AssertionError("no old cards")), \
             mock.patch.object(reviewer.native, "official_targets", side_effect=AssertionError("no inventory fallback")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("no producer or renderer")):
            result = self.run_review()
        report = reviewer.read_json(Path(result["report"]))
        sequence = report["sequence_review"]
        slots = sequence["groups"][0]["frames"]
        self.assertEqual(report["schema"], "h3.motion.source-collection.visual-review.v1")
        self.assertEqual(report["status"], "REVIEW_READY")
        self.assertTrue(report["partial"])
        self.assertEqual(sequence["schema"], "h3.motion.source-sequence-review.v1")
        self.assertEqual((sequence["summary"]["expected_slots"], sequence["summary"]["available_slots"],
                          sequence["summary"]["hold_slots"]), (8, 6, 2))
        self.assertEqual([r["key"][3] for r in slots], list(range(8)))
        self.assertEqual([r["key"][3] for r in slots if "image" not in r], [4, 5])
        self.assertEqual(slots[3]["origin_1x"], [167, 166])
        self.assertEqual(slots[7]["origin_1x"], [159, 168])
        self.assertEqual(sequence["groups"][0]["wrap_diagnostic"], {"status": "NOT_MEASURED_SOURCE_DISPLAY"})
        self.assertEqual(report["adjacent_diagnostics"], [])
        self.assertNotIn("producer_reports", report)
        self.assertNotIn("official_inventory", sequence)
        for flag in reviewer.AUTHORITY_FLAGS + ("native_loop_verified", "native_inventory_verified",
                                               "source_formal_admission_verified"):
            self.assertIs(report[flag], False)
        self.assertEqual(result["images_written"], 0)
        self.assertIn("source_sequence", result)
        self.assertNotIn("native_sequence", result)
        self.assertEqual({p.name for p in self.out.iterdir()}, {"review.json", "index.html"})
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        page = Path(result["html"]).read_text("utf-8")
        self.assertNotIn("MUST_NOT_READ_OLD_PLACEHOLDERS", page)
        self.assertNotIn("切换前后帧叠影", page)
        self.assertIn('class="background"', page)
        for value in ("checker", "dark", "white", "grass"):
            self.assertIn(f'value="{value}"', page)
        payload = json.loads(page.split('id="native-sequence-data">', 1)[1].split("</script>", 1)[0])
        import base64
        self.assertEqual({hashlib.sha256(base64.b64decode(v.split(",", 1)[1])).hexdigest()
                          for v in payload["images"].values()}, {reviewer.sha256(p) for p in self.pngs.values()})

    def test_repeated_parent_documents_parse_once_but_each_reference_is_checked(self):
        with mock.patch.object(reviewer.native, "parse_json", wraps=reviewer.native.parse_json) as parser:
            self.run_review()
        self.assertEqual(sum(call.args[1] == "SOURCE_REQUEST" for call in parser.call_args_list), 3)
        self.out.rename(self.root / "completed-test-only")
        self.selection["frames"][1]["placement"]["bytes"] = self.cohorts[0]["placement_path"].stat().st_size + 1
        self.save_selection()
        self.assert_rejected("BYTES_DRIFT")

    def test_strict_source_manifest_and_root_authority(self):
        self.manifest["official_inventory"] = {}
        save(self.manifest_path, self.manifest)
        self.assert_rejected("SOURCE_COLLECTION_FIELDS")
        self.manifest.pop("official_inventory")
        self.selection["review_boundary"]["whole_group_art_approved"] = True
        self.save_selection()
        self.assert_rejected("AUTHORITY_ESCALATION")

    def test_source_ref_types_operation_and_old_schema_are_not_coerced(self):
        self.manifest["source_review"] = {"path": str(self.root_path), "sha256": True}
        save(self.manifest_path, self.manifest)
        self.assert_rejected("REFERENCE_INVALID")
        self.save_selection()
        self.manifest["operation_id"] = "another-operation"
        save(self.manifest_path, self.manifest)
        self.assert_rejected("OPERATION_OUTPUT_MISMATCH")
        self.manifest["operation_id"] = self.out.name
        self.manifest["schema"] = "h3.motion.review-collection.v1"
        save(self.manifest_path, self.manifest)
        self.assert_rejected("COLLECTION_FIELDS_INVALID")

    def test_ready_missing_or_bad_hold_fit_is_failure_not_new_hold(self):
        self.pngs[0].write_bytes(b"not the pinned existing PNG")
        self.assert_rejected("SHA256_DRIFT_OR_LIMIT")
        self.pngs[0].unlink()
        self.assert_rejected("MISSING|exist|No such|cannot find")

    def test_declared_hold_fit_is_a_pinned_input(self):
        self.fit_path.write_text('{"changed": true}', encoding="utf-8")
        self.assert_rejected("SOURCE_DECLARED_HOLD_FIT_SHA256_DRIFT")

    def test_both_export_shapes_require_exact_parent_and_terminal_stages(self):
        self.cohorts[1]["export"]["inputs"].pop()
        self.seal_source()
        self.assert_rejected("PARENT_REFERENCE_MISSING")
        self.cohorts[1]["export"]["inputs"].append(file_ref(self.cohorts[1]["placement_path"]))
        self.cohorts[0]["export"]["stages"][1]["exit_code"] = 1
        self.seal_source()
        self.assert_rejected("STAGES_NOT_VERIFIED")

    def test_export_output_limit_and_duplicate_are_rejected(self):
        export = self.cohorts[0]["export"]
        original = copy.deepcopy(export["outputs"])
        export["outputs"].append(copy.deepcopy(export["outputs"][0]))
        self.seal_source()
        self.assert_rejected("OUTPUT_MISSING_OR_DUPLICATE")
        export["outputs"] = [copy.deepcopy(original[0])] * (reviewer.SOURCE_LIMITS["slots"] + 1)
        self.seal_source()
        self.assert_rejected("OUTPUTS_REQUIRED")

    def test_request_maps_and_explicit_hold_partition_cannot_drift(self):
        self.cohorts[1]["request"]["logical_slot_bindings"][4]["art_owner_slot"] = [0, 3]
        self.seal_source()
        self.assert_rejected("LOGICAL_BINDINGS_DRIFT")
        self.cohorts[1]["request"]["logical_slot_bindings"] = copy.deepcopy(
            self.cohorts[0]["request"]["logical_slot_bindings"])
        self.selection["holds"]["native_frames"] = [4]
        self.seal_source()
        self.assert_rejected("EXPECTED_READY_HOLD_SET_DRIFT")

    def test_geometry_integer_types_and_production_scale_are_strict(self):
        for cohort in self.cohorts:
            for row in cohort["request"]["logical_slot_bindings"]:
                if row["key"][3] == 0:
                    row["targets"]["origin_1x"][0] = 0
                    row["targets"]["origin_3x"][0] = False
        for field in ("original", "request", "placement"):
            rows = self.cohorts[0][field][{"original": "entries", "request": "original_slots",
                                         "placement": "frames"}[field]]
            rows[0]["targets" if field != "placement" else "complete_target_geometry"]["origin_1x"][0] = 0
            rows[0]["targets" if field != "placement" else "complete_target_geometry"]["origin_3x"][0] = False
        self.seal_source()
        self.assert_rejected("invalid origin_3x")
        for cohort in self.cohorts:
            for row in cohort["request"]["logical_slot_bindings"]:
                if row["key"][3] == 0:
                    row["targets"]["origin_3x"][0] = 0
        for field in ("original", "request", "placement"):
            rows = self.cohorts[0][field][{"original": "entries", "request": "original_slots",
                                         "placement": "frames"}[field]]
            rows[0]["targets" if field != "placement" else "complete_target_geometry"]["origin_3x"][0] = 0
        self.cohorts[0]["request"]["production_scale"] = True
        self.seal_source()
        self.assert_rejected("PRODUCTION_SCALE_INVALID")

    def test_source_original_receipt_geometry_and_unsafe_overlap_are_checked(self):
        self.cohorts[0]["original"]["entries"][0]["targets"]["origin_1x"][0] += 1
        self.cohorts[0]["original"]["entries"][0]["targets"]["origin_3x"][0] += 3
        self.seal_source()
        self.assert_rejected("ORIGINAL_RECEIPT_GEOMETRY_DRIFT")
        self.manifest["source_review"]["path"] = str(self.out / "input.json")
        save(self.manifest_path, self.manifest)
        self.assert_rejected("SOURCE_INPUT_OUTPUT_OVERLAP")

    def test_png_must_be_static_rgba_and_actually_decodable(self):
        Image.new("RGB", tuple(self.geometry[0]["crop_3x"]), (1, 2, 3)).save(self.pngs[0])
        self.seal_source()
        self.assert_rejected("STATIC_RGBA_PNG_REQUIRED")
        Image.new("RGBA", tuple(self.geometry[0]["crop_3x"]), (1, 2, 3, 255)).save(self.pngs[0])
        self.pngs[0].write_bytes(self.pngs[0].read_bytes()[:-15])
        self.seal_source()
        self.assert_rejected("truncated|broken|cannot|checksum|Truncated")

    def test_source_budget_failures_precede_output_creation(self):
        limits = reviewer.SOURCE_LIMITS
        changes = [{"slots": 7}, {"json_file_bytes": 1}, {"json_total_bytes": self.manifest_path.stat().st_size},
                   {"png_total_bytes": 1}, {"decoded_pixels": 1}, {"image_pixels": 1}, {"canvas_pixels": 1}]
        for change in changes:
            with self.subTest(change=change), mock.patch.dict(limits, change):
                self.assert_rejected("COUNT|LIMIT|BUDGET")

    def test_source_group_canvas_budget_uses_cross_frame_player_dimensions(self):
        for n in range(8):
            canvas = [10000, 1] if n == 0 else [1, 10000] if n == 1 else [1, 1]
            self.geometry[n] = {"canvas_1x": canvas, "crop_1x": [1, 1], "origin_1x": [0, 0],
                "canvas_3x": [v * 3 for v in canvas], "crop_3x": [3, 3], "origin_3x": [0, 0]}
        for cohort in self.cohorts:
            for rows, field in ((cohort["request"]["logical_slot_bindings"], "targets"),
                                (cohort["request"]["original_slots"], "targets"),
                                (cohort["original"]["entries"], "targets"),
                                (cohort["placement"]["frames"], "complete_target_geometry")):
                for row in rows:
                    row[field] = copy.deepcopy(self.geometry[row["key"][3]])
            for output in cohort["export"]["outputs"]:
                output["pixels"] = [3, 3]
        for path in self.pngs.values():
            Image.new("RGBA", (3, 3), (1, 2, 3, 255)).save(path)
        self.seal_source()
        self.assertTrue(all(v["canvas_1x"][0] * v["canvas_1x"][1] * 9
                            <= reviewer.SOURCE_LIMITS["canvas_pixels"] for v in self.geometry.values()))
        self.assertGreater(10000 * 10000 * 9, reviewer.SOURCE_LIMITS["canvas_pixels"])
        self.assert_rejected("SOURCE_GROUP_CANVAS_PIXEL_BUDGET_EXCEEDED")
        expected = {("def", "TESTSOURCE", 0, n): {} for n in range(2)}
        targets = {key: self.geometry[key[3]] for key in expected}
        with mock.patch.object(reviewer.native, "official_targets", side_effect=lambda row, key: targets[key]):
            old_native = reviewer.native_sequence_record(expected, {}, {}, {}, [], {})
        self.assertEqual(old_native["groups"][0]["expected_slots"], 2)
        self.assertEqual(old_native["schema"], "h3.motion.native-sequence-review.v1")

    def test_base64_is_deduplicated_without_native_alias_authority(self):
        self.pngs[2].write_bytes(self.pngs[0].read_bytes())
        self.seal_source()
        result = self.run_review()
        sequence = reviewer.read_json(Path(result["report"]))["sequence_review"]
        self.assertEqual(sequence["summary"]["unique_owner_images"], 5)
        self.assertTrue(all(r["image_storage_only_not_native_alias_authority"] for r in sequence["images"].values()))
        self.assertFalse(sequence["native_inventory_verified"])

    def test_prewrite_and_postwrite_currentness_keep_failure_and_partial_facts(self):
        original_verify = reviewer.native.BoundInputSet.verify_current
        fired = False
        def drift_before(refs):
            nonlocal fired
            if not fired:
                fired = True
                self.fit_path.write_text("changed before output", encoding="utf-8")
            return original_verify(refs)
        with mock.patch.object(reviewer.native.BoundInputSet, "verify_current", drift_before):
            self.assert_rejected("PINNED_INPUT_CHANGED")
        save(self.fit_path, {"status": "TEST_SOURCE_HOLD_NOT_A_TOOL_FIT_DECISION"})
        self.selection["holds"]["fit"] = file_ref(self.fit_path)
        self.save_selection()
        original_write = reviewer.native._write_new
        def drift_after(path, data, guard):
            original_write(path, data, guard)
            if path.name == "review.json":
                self.root_path.write_bytes(self.root_path.read_bytes()[:-1] + b" ")
        with mock.patch.object(reviewer.native, "_write_new", drift_after), \
             self.assertRaisesRegex(ValueError, "PINNED_INPUT_CHANGED"):
            self.run_review()
        self.assertTrue(self.out.is_dir())
        self.assertEqual({p.name for p in self.out.iterdir()}, {"review.json", "index.html"})

    def test_reparse_input_is_rejected_by_the_shared_reader(self):
        original = reviewer.native.reject_reparse_components
        def reject_png(path, *args, **kwargs):
            if Path(path) == self.pngs[0]:
                raise reviewer.native.ReviewError("TEST_SOURCE_REPARSE_BLOCKED")
            return original(path, *args, **kwargs)
        with mock.patch.object(reviewer.native, "reject_reparse_components", reject_png):
            self.assert_rejected("REPARSE_BLOCKED")

    def test_explicit_eight_selection_reuses_old_six_and_only_fills_declared_holds(self):
        self.enable_eight()
        before = {p: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        result = self.run_review()
        report = reviewer.read_json(Path(result["report"]))
        sequence = report["sequence_review"]
        self.assertFalse(report["partial"])
        self.assertEqual(report["source_selection_schema"], "h3.creature.existing-3x-css-review-bindings.v1")
        self.assertEqual(report["parent_source_selection"]["sha256"], reviewer.sha256(self.root_path))
        self.assertEqual((sequence["summary"]["expected_slots"], sequence["summary"]["available_slots"],
                          sequence["summary"]["hold_slots"]), (8, 8, 0))
        self.assertEqual([r["key"][3] for r in sequence["groups"][0]["frames"]], list(range(8)))
        self.assertNotIn("review_boundary", report)
        for flag in reviewer.AUTHORITY_FLAGS + ("native_loop_verified", "native_inventory_verified",
                                               "source_formal_admission_verified"):
            self.assertIs(report[flag], False)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(result["images_written"], 0)
        page = Path(result["html"]).read_text("utf-8")
        self.assertNotIn("MUST_NOT_READ", page)
        self.assertNotIn("IGNORED_SOURCE_URL", page)

    def test_new_eight_source_files_do_not_change_old_six_carrier_selection(self):
        self.enable_eight()
        self.manifest["source_review"] = file_ref(self.root_path)
        save(self.manifest_path, self.manifest)
        result = self.run_review()
        report = reviewer.read_json(Path(result["report"]))
        self.assertTrue(report["partial"])
        self.assertEqual(report["sequence_review"]["summary"]["hold_slots"], 2)
        self.assertNotIn("parent_source_selection", report)

    def test_eight_wrong_parent_schema_and_explicit_changes_fail_closed(self):
        self.enable_eight()
        self.eight["first_six_binding_authority"]["sha256"] = "0" * 64
        self.activate_eight()
        self.assert_rejected("SOURCE_FIRST_SELECTION_SHA256_DRIFT")
        self.eight["first_six_binding_authority"] = file_ref(self.root_path)
        self.eight["logical_frame_binding_changes"] = [False, 5]
        self.activate_eight()
        self.assert_rejected("EXPLICIT_ADDITIONS_DRIFT")
        self.eight["logical_frame_binding_changes"] = [4, 5]
        old = reviewer.read_json(self.root_path)
        old["schema"] = "h3.creature.existing-3x-css-review-bindings.v1"
        save(self.root_path, old)
        self.eight["first_six_binding_authority"] = file_ref(self.root_path)
        self.activate_eight()
        self.assert_rejected("SOURCE_ROOT_SCHEMA_UNSUPPORTED")

    def test_eight_cannot_change_old_png_placement_or_html_binding(self):
        self.enable_eight()
        baseline = copy.deepcopy(self.eight)
        for field, value, message in (
            ("production_3x", file_ref(self.pngs[2]), "SOURCE_HTML_CANDIDATE|SOURCE_FIRST_SIX"),
            ("placement", file_ref(self.cohorts[-1]["placement_path"]), "SOURCE_FIRST_SIX"),
            ("pixels", [1, 1], "SOURCE_FIRST_SIX_PIXELS")):
            with self.subTest(field=field):
                self.eight = copy.deepcopy(baseline)
                self.eight["frames"][0][field] = value
                self.activate_eight()
                self.assert_rejected(message)
        self.eight = copy.deepcopy(baseline)
        self.eight["html_frame_bindings"][4]["targets"]["origin_1x"][0] += 1
        self.eight["html_frame_bindings"][4]["targets"]["origin_3x"][0] += 3
        self.activate_eight()
        self.assert_rejected("SOURCE_HTML_GEOMETRY_DRIFT")

    def test_eight_declared_request_k_attempt_and_export_must_match_real_parents(self):
        self.enable_eight()
        baseline = copy.deepcopy(self.eight)
        for field, value, message in (
            ("request", file_ref(self.cohorts[0]["request_path"]), "SOURCE_EIGHT_REQUEST_BINDING_DRIFT"),
            ("common_integer_k", 9, "SOURCE_EIGHT_FRAME_BINDING_DRIFT"),
            ("attempt", True, "SOURCE_EIGHT_FRAME_BINDING_DRIFT"),
            ("local_export", file_ref(self.cohorts[-1]["export_path"]), "INLINE_EXPORT_UNSUPPORTED")):
            with self.subTest(field=field):
                self.eight = copy.deepcopy(baseline)
                self.eight["frames"][4][field] = value
                self.activate_eight()
                self.assert_rejected(message)
        self.eight = copy.deepcopy(baseline)
        self.eight["new_a21_local_export"] = file_ref(self.cohorts[1]["export_path"])
        self.activate_eight()
        self.assert_rejected("SOURCE_DECLARED_SCALE_DRIFT")

    def test_eight_unknown_type_and_authority_escalation_are_not_normalized(self):
        self.enable_eight()
        self.eight["art_approved"] = True
        self.activate_eight()
        self.assert_rejected("AUTHORITY_ESCALATION")
        self.eight["art_approved"] = False
        self.eight["schema"] = "h3.creature.existing-3x-css-review-bindings.v2"
        self.activate_eight()
        self.assert_rejected("SOURCE_ROOT_SCHEMA_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
