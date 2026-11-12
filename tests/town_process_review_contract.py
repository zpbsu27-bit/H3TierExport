"""Offline contracts for native Town PROCESS/REVIEW intake, without image production."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import shutil
import uuid
import unittest
from unittest import mock

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import town_process_review as review


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class TownProcessReviewContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h3-town-process-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out_base = PRODUCT / "build" / "experiments" / "town-process-review"
        self.out_base.mkdir(parents=True, exist_ok=True)
        self.operation_id = "town-r10-contract-" + uuid.uuid4().hex[:16]
        self.addCleanup(lambda: shutil.rmtree(self.outdir) if self.outdir.is_dir() and not self.outdir.is_symlink() else None)
        self.outdir = self.out_base / self.operation_id
        self.inventory = self.root / "native.json.gz"
        rows = [{"kind": "def", "category": "towns_buildings",
                 "identity": "TBCSEXT2", "group": 0, "frame": i,
                 "native_lod": "H3sprite.lod", "canvas": [24, 38],
                 "crop": [24, 38], "anchor": [0, 0],
                 "resource": f"TACSWF{i + 1:02}.pcx"} for i in range(10)]
        with gzip.open(self.inventory, "wt", encoding="utf-8") as handle:
            json.dump({"schema": "h3.complete-art-catalog.v3", "rows": rows}, handle)
        self.parent = self.root / "canonical.json"
        self.parent.write_text('{"schema":"canonical"}', encoding="utf-8")
        self.standard = self.root / "standard.json"
        self.feedback = self.root / "handoff.json"
        self.source = self.root / "candidate.png"
        Image.new("RGBA", (72, 114), (50, 70, 90, 1)).save(self.source)
        self.feedback_data = {
            "schema": "h3.art-town.tool-r9-process-review-handoff.v1",
            "status": "TOWN_PROCESS_REVIEW_OPEN_TOOL_OWNER_ACTION_REQUIRED",
            "town_inputs": {
                "a03_keyframe_candidates": {
                    "keys": [["def", "TBCSEXT2", 0, i] for i in (1, 5, 9)],
                    "status": "UNSELECTED_KEYFRAME_CANDIDATE_EDGE_CONTACT_REVIEW_REQUIRED"},
                "a04_color_candidate": {
                    "source_sha256": "a" * 64,
                    "eligible_as_motion_keyframe": False,
                    "controlled_color_only": False},
                "remaining_tbcsext2_slots": {
                    "frames": [2, 3, 4, 6, 7, 8],
                    "status": "SOURCE_AND_PHASE_INVENTORY_PENDING"},
            },
        }
        self.standard_data = {
            "schema": "h3.art-town.modern-hd-direction-addendum.v1",
            "authority": "EXPLICIT_CURRENT_USER_ART_DIRECTION",
            "status": "CURRENT_USER_STANDARD_FOR_ALL_FUTURE_TOWN_CREATION",
            "parent_canonical": self.ref(self.parent),
            "quality_bar": {"process_frames": "One pinned modern-HD static body"},
        }
        self.save_inputs()
        self.renderer = self.root / "H3TierExport.exe"
        self.renderer.write_bytes(b"stub executable")
        self.manifest_file = self.root / "batch.json"
        self.manifest_data = {
            "schema": "h3.town.process-review-intake.v1",
            "operation_id": self.operation_id,
            "identity": "TBCSEXT2",
            "official_inventory": self.ref(self.inventory),
            "town_feedback": self.ref(self.feedback),
            "art_standard": self.ref(self.standard),
            "candidates": [],
        }
        self.save_manifest()

    def ref(self, path: Path) -> dict:
        return {"path": str(path.resolve()), "sha256": sha(path.read_bytes())}

    def save_inputs(self) -> None:
        self.feedback.write_text(json.dumps(self.feedback_data), encoding="utf-8")
        self.standard.write_text(json.dumps(self.standard_data), encoding="utf-8")

    def save_manifest(self) -> None:
        self.manifest_file.write_text(json.dumps(self.manifest_data), encoding="utf-8")

    def candidate(self, source: Path, **changes) -> dict:
        record = {
            "key": ["def", "TBCSEXT2", 0, 5],
            "source": self.ref(source),
            "source_role": "MODEL_MASTER",
            "request": None,
            "raw_receipt": None,
            "phase_guide": None,
            "source_view": None,
            "native_mapping": None,
            "static_registration": None,
            "scene_context": None,
            "timing": None,
            "native_review": None,
        }
        record.update(changes)
        return record

    def json_ref(self, name: str, value: dict) -> dict:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return self.ref(path)

    def strict_candidate(self, role: str = "MODEL_MASTER", *, key=None,
                         omit_static: bool = False, view_semantics: str = "HALF_OPEN_LTRB",
                         authorization_source: Path | None = None) -> dict:
        key = key or ["def", "TBCSEXT2", 0, 5]
        guide = self.root / "phase-guide.png"
        Image.new("RGBA", (24, 38), (3, 4, 5, 255)).save(guide)
        static = self.root / "static-f0.png"
        Image.new("RGBA", (96, 152), (8, 9, 10, 1)).save(static)
        request_value = {
            "schema": "h3.art-town.model-request.v1", "key": key,
            "native_geometry": {"canvas": [24, 38], "crop": [24, 38], "origin": [0, 0]},
            "art_direction_policy": self.ref(self.standard),
            "scoped_production_authorization": {"human_art_approval_inferred": False},
        }
        request = self.json_ref("strict-request.json", request_value)
        raw_receipt = self.json_ref("strict-raw-receipt.json", {
            "schema": "h3.art-town.raw-intake.v1", "request": request,
            "raw": self.ref(self.source), "master": self.ref(self.source),
            "preserved_raw_bytes": True, "human_approved": False, "game_deployed": False,
        })
        source_view = self.json_ref("strict-source-view.json", {
            "schema": "h3.art-town.source-view.v1", "request": request,
            "raw": self.ref(self.source), "master": self.ref(self.source),
            "source_rect": [0, 0, 72, 114],
        })
        auth_source = authorization_source or self.source
        authorization = self.json_ref("strict-authorization.json", {
            "schema": "h3.art-town.export-transparent-margin-authorization.v1",
            "key": key, "request": request, "raw": self.ref(auth_source),
            "master": self.ref(auth_source), "art_or_runtime_acceptance_granted": False,
            "game_deployed": False,
        })
        mapping = self.json_ref("strict-native-mapping.json", {
            "schema": "h3.town.native-mapping.v1", "status": "EXPLICIT_CURRENT_MAPPING",
            "key": key, "source_sha256": self.ref(self.source)["sha256"], "native_scale": 3,
            "geometry": {"canvas_1x": [24, 38], "crop_1x": [24, 38], "origin_1x": [0, 0]},
        })
        static_registration = self.json_ref("strict-static-registration.json", {
            "schema": "h3.town.static-registration.v1", "key": key,
            "static_body_key": ["def", "TBCSEXT2", 0, 0],
            "native_geometry": {"canvas_1x": [24, 38], "crop_1x": [24, 38], "origin_1x": [0, 0]},
            "modern_hd_standard_sha256": self.ref(self.standard)["sha256"],
            "static_body": self.ref(static), "fixed_scene_xy": [46, 119],
            "registered_canvas_3x": [72, 114], "static_anchor_ltrb_3x": [0, 0, 72, 114],
            "bbox_fit_per_frame": False, "dynamic_layers": ["water", "foam"],
        })
        scene = self.json_ref("strict-scene.json", {"schema": "h3.town.scene-context.v1"})
        timing = self.json_ref("strict-timing.json", {"schema": "h3.town.timing-contract.v1", "key": key,
                                                    "status": "UNKNOWN_NOT_GUESSED"})
        binding = {
            "schema": "h3.town.source-binding.v1", "role": role, "key": key,
            "source_view_rect_semantics": view_semantics,
            "phase_guide_sha256": self.ref(guide)["sha256"],
        }
        if role == "MODEL_MASTER":
            binding["authorization"] = authorization
        record = {
            "key": key, "source": self.ref(self.source), "source_role": role,
            "request": request, "raw_receipt": raw_receipt, "phase_guide": self.ref(guide),
            "source_view": source_view, "native_mapping": mapping,
            "static_registration": None if omit_static else static_registration,
            "scene_context": scene, "timing": timing, "native_review": None,
            "source_binding": binding,
        }
        return record

    def use_v2(self, candidates: list[dict]) -> None:
        self.manifest_data = {
            "schema": "h3.town.process-review-intake.v2", "operation_id": self.operation_id,
            "identity": "TBCSEXT2", "official_inventory": self.ref(self.inventory),
            "town_feedback": self.ref(self.feedback), "art_standard": self.ref(self.standard),
            "source_intake": None, "candidates": candidates,
        }
        self.save_manifest()

    def use_owner_feedback_v2(self, candidate: dict) -> None:
        guide = self.root / "owner-guide.png"
        Image.new("RGBA", (24, 38), (7, 8, 9, 255)).save(guide)
        request = self.json_ref("owner-request.json", {
            "schema": "h3.art-town.model-request.v1", "key": candidate["key"],
            "native_geometry": {"canvas": [24, 38], "crop": [24, 38], "origin": [0, 0]},
            "art_direction_policy": self.ref(self.standard),
        })
        raw = self.json_ref("owner-raw.json", {
            "schema": "h3.art-town.raw-intake.v1", "request": request,
            "raw": self.ref(self.source), "master": self.ref(self.source),
            "preserved_raw_bytes": True, "human_approved": False, "game_deployed": False,
        })
        readback = self.json_ref("owner-readback.json", {
            "schema": "h3.art-town.modern-hd-master-native-review-readback.v1",
            "status": "REVIEW_REQUIRED", "key": candidate["key"],
            "source": {"master": self.ref(self.source)},
        })
        feedback = self.json_ref("owner-feedback.json", {
            "schema": "h3.art-town.batch-tool-action-feedback.v2",
            "status": "REVIEW_REQUIRED", "selection": "UNSELECTED",
            "process_frame_or_export_completed": False,
            "source": self.ref(self.source), "request": request,
            "raw_receipt": raw, "guide": self.ref(guide),
            "standard": self.ref(self.standard), "native_review_readback": readback,
        })
        candidate["request"] = request
        candidate["raw_receipt"] = raw
        candidate["phase_guide"] = self.ref(guide)
        candidate["source_binding"]["phase_guide_sha256"] = self.ref(guide)["sha256"]
        candidate["source_binding"]["authorization"] = None
        candidate["source_view"] = None
        candidate["static_registration"] = None
        self.manifest_data = {
            "schema": "h3.town.process-review-intake.v2", "operation_id": self.operation_id,
            "identity": candidate["key"][1], "official_inventory": self.ref(self.inventory),
            "town_feedback": feedback, "art_standard": self.ref(self.standard),
            "source_intake": feedback, "candidates": [candidate],
        }
        self.save_manifest()

    def invoke(self, stage: str, good: bool = True) -> dict:
        out, err = io.StringIO(), io.StringIO()
        args = [stage, "--manifest", str(self.manifest_file),
                "--manifest-sha256", sha(self.manifest_file.read_bytes()),
                "--operation-id", self.operation_id, "--outdir", str(self.outdir),
                "--renderer", str(self.renderer),
                "--renderer-sha256", sha(self.renderer.read_bytes())]
        with mock.patch.object(review, "OUTPUT_BASE", self.out_base), \
             mock.patch.object(review, "current_receipt_build", return_value="b" * 64), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.main(args)
        self.assertEqual(code == 0, good, (out.getvalue() + err.getvalue())[-1600:])
        return json.loads(out.getvalue() or err.getvalue())

    def test_real_shape_all_native_slots_and_modern_standard_remain_open(self) -> None:
        report = self.invoke("check")
        self.assertEqual(len(report["slots"]), 10)
        self.assertEqual(report["summary"]["NEEDS_NATIVE_MAPPING"], 3)
        self.assertEqual(report["summary"]["NEEDS_KEYFRAME"], 7)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual([r["key"][3] for r in report["slots"]], list(range(10)))
        self.assertEqual(report["slots"][5]["geometry"]["canvas_1x"], [24, 38])
        self.assertEqual(report["slots"][5]["geometry"]["target_crop_3x"], [72, 114])
        self.assertEqual(report["art_standard"]["sha256"], self.ref(self.standard)["sha256"])
        self.assertFalse(report["formal_process_export_allowed"])
        self.assertFalse(report["art_approved"])
        self.assertFalse(self.outdir.exists())

    def test_exact_a04_source_is_never_accepted_as_f5_keyframe(self) -> None:
        self.feedback_data["town_inputs"]["a04_color_candidate"]["source_sha256"] = self.ref(self.source)["sha256"]
        self.save_inputs()
        self.manifest_data["town_feedback"] = self.ref(self.feedback)
        self.manifest_data["candidates"] = [self.candidate(self.source)]
        self.save_manifest()
        report = self.invoke("check", good=False)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["slots"][5]["status"], "BLOCKED")
        self.assertIn("A04_NOT_MOTION_KEYFRAME", report["slots"][5]["errors"][0])
        self.assertFalse(self.outdir.exists())

    def test_wrong_source_sha_and_parent_standard_drift_fail_before_output(self) -> None:
        self.manifest_data["town_feedback"]["sha256"] = "0" * 64
        self.save_manifest()
        blocked = self.invoke("check", good=False)
        self.assertIn("REFERENCE_DRIFT", blocked["error"])
        self.assertFalse(self.outdir.exists())
        self.manifest_data["town_feedback"] = self.ref(self.feedback)
        self.standard_data["parent_canonical"]["sha256"] = "0" * 64
        self.save_inputs()
        self.manifest_data["art_standard"] = self.ref(self.standard)
        self.save_manifest()
        blocked = self.invoke("check", good=False)
        self.assertIn("REFERENCE_DRIFT", blocked["error"])

    def test_forged_native_mapping_blocks_without_producing_images(self) -> None:
        mapping = self.root / "native-mapping.json"
        mapping.write_text(json.dumps({
            "schema": "h3.town.native-mapping.v1",
            "key": ["def", "TBCSEXT2", 0, 1],
            "source_sha256": self.ref(self.source)["sha256"],
            "geometry": {"canvas_1x": [24, 38], "crop_1x": [24, 38], "origin_1x": [0, 0]},
            "native_scale": 3, "status": "EXPLICIT_CURRENT_MAPPING",
        }), encoding="utf-8")
        self.manifest_data["candidates"] = [self.candidate(self.source, native_mapping=self.ref(mapping))]
        self.save_manifest()
        report = self.invoke("check", good=False)
        self.assertIn("NATIVE_MAPPING_KEY_MISMATCH", report["slots"][5]["errors"][0])
        self.assertFalse(self.outdir.exists())

    def test_pinned_a03_source_and_mapping_remain_review_only_and_keep_alpha1(self) -> None:
        mapping = self.root / "mapping.json"
        mapping.write_text(json.dumps({
            "schema": "h3.town.native-mapping.v1", "status": "EXPLICIT_CURRENT_MAPPING",
            "key": ["def", "TBCSEXT2", 0, 5],
            "source_sha256": self.ref(self.source)["sha256"], "native_scale": 3,
            "geometry": {"canvas_1x": [24, 38], "crop_1x": [24, 38],
                         "origin_1x": [0, 0]},
        }), encoding="utf-8")
        self.manifest_data["candidates"] = [
            self.candidate(self.source, native_mapping=self.ref(mapping))]
        self.save_manifest()
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "REVIEW_REQUIRED")
        self.assertEqual(slot["source_diagnostic"]["alpha1_pixels"], 72 * 114)
        self.assertIn("scene_context", slot["needs"])
        self.assertIn("independent_art_and_scene_acceptance", slot["needs"])
        self.assertFalse(report["formal_process_export_allowed"])
        self.assertFalse(self.outdir.exists())

    def test_invalid_derived_source_scale_and_output_png_tamper(self) -> None:
        self.manifest_data["candidates"] = [
            self.candidate(self.source, source_role="DERIVED_PIXEL_COPY")]
        self.save_manifest()
        check = self.invoke("check")
        self.assertEqual(check["slots"][5]["status"], "NEEDS_NATIVE_MAPPING")
        self.manifest_data["candidates"] = []
        self.save_manifest()
        self.invoke("run")
        (self.outdir / "forged-frame.png").write_bytes(self.source.read_bytes())
        result = self.invoke("verify", good=False)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("UNRECEIPTED_PNG_NOT_ALLOWED", result["error"])

    def test_run_verify_reuse_and_tamper_never_retry_partial(self) -> None:
        created = self.invoke("run")
        self.assertEqual(created["status"], "REVIEW_REQUIRED")
        self.assertTrue((self.outdir / "review-report.json").is_file())
        self.assertTrue((self.outdir / "index.html").is_file())
        self.assertTrue((self.outdir / "analysis-complete.json").is_file())
        self.assertFalse(list(self.outdir.rglob("*.png")))
        verified = self.invoke("verify")
        self.assertEqual(verified["status"], "VERIFIED_REVIEW_REQUIRED")
        again = self.invoke("run")
        self.assertEqual(again["status"], "REUSED_VERIFIED")
        (self.outdir / "review-report.json").write_bytes(b"altered")
        failure = self.invoke("verify", good=False)
        self.assertEqual(failure["status"], "BLOCKED")
        self.assertFalse(list(self.outdir.rglob("*.png")))

    def test_v2_requires_explicit_model_source_authorization(self) -> None:
        candidate = self.strict_candidate()
        candidate["source_binding"].pop("authorization")
        self.replace_document(candidate, "source_view", source_rect=[1, 0, 72, 114])
        self.use_v2([candidate])
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "NEEDS_SOURCE_BINDING")
        self.assertIn("source_authorization", slot["needs"])

    def test_v2_rejects_xywh_and_cross_source_authorization(self) -> None:
        candidate = self.strict_candidate(view_semantics="XYWH")
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_BINDING_CONTRACT_INVALID", report["slots"][5]["errors"][0])
        candidate = self.strict_candidate(authorization_source=self.root / "static-f0.png")
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_AUTHORIZATION_SOURCE_MISMATCH", report["slots"][5]["errors"][0])

    def test_v2_missing_static_registration_is_not_process_ready(self) -> None:
        self.use_v2([self.strict_candidate(omit_static=True)])
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "NEEDS_REGISTRATION")
        self.assertIn("static_registration", slot["needs"])

    def test_v2_same_size_source_cannot_bind_another_native_key(self) -> None:
        candidate = self.strict_candidate(key=["def", "TBCSEXT2", 0, 1])
        candidate["request"] = self.json_ref("wrong-key-request.json", {
            "schema": "h3.art-town.model-request.v1", "key": ["def", "TBCSEXT2", 0, 5],
            "native_geometry": {"canvas": [24, 38], "crop": [24, 38], "origin": [0, 0]},
            "art_direction_policy": self.ref(self.standard),
        })
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_REQUEST_KEY_MISMATCH", report["slots"][1]["errors"][0])

    def test_v2_derived_copy_requires_preparation_mapping_and_parent_chain(self) -> None:
        candidate = self.strict_candidate(role="DERIVED_PIXEL_COPY")
        self.use_v2([candidate])
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "NEEDS_SOURCE_BINDING")
        self.assertTrue({"preparation", "pixel_mapping", "parent_chain"}.issubset(slot["needs"]))

        source_ref = self.ref(self.source)
        request_ref = candidate["request"]
        prep = self.json_ref("derived-preparation.json", {
            "schema": "h3.town.derived-preparation.v1",
            "source_sha256": source_ref["sha256"],
            "request_sha256": request_ref["sha256"], "mutated": False,
        })
        chain = self.json_ref("derived-parent-chain.json", {
            "schema": "h3.town.source-parent-chain.v1", "root_sha256": source_ref["sha256"],
            "leaf_sha256": source_ref["sha256"],
            "chain": [source_ref, source_ref],
        })
        mapping = self.json_ref("derived-pixel-mapping.json", {
            "schema": "h3.town.derived-pixel-mapping.v1",
            "source_sha256": source_ref["sha256"], "parent_sha256": source_ref["sha256"],
            "coordinate_semantics": "ONE_TO_ONE_RGBA", "resampled": False,
            "alpha_edited": False, "all_pixels_mapped": True,
        })
        candidate["source_binding"].update({
            "preparation": prep, "pixel_mapping": mapping, "parent_chain": chain,
        })
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("DERIVED_PARENT_CHAIN_CYCLE", report["slots"][5]["errors"][0])

    def replace_document(self, candidate: dict, field: str, **changes) -> None:
        path = Path(candidate[field]["path"])
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update(changes)
        path.write_text(json.dumps(doc), encoding="utf-8")
        candidate[field] = self.ref(path)

    def test_v2_full_source_view_does_not_require_crop_permission(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        candidate["source_binding"].pop("authorization")
        self.use_v2([candidate])
        report = self.invoke("check")
        self.assertEqual(report["slots"][5]["status"], "NEEDS_REGISTRATION")

    def test_native_review_accepts_only_three_x_and_keeps_missing_or_drift_guards(self) -> None:
        source_ref = self.ref(self.source)
        output = {"path": str(self.source), "png_sha256": source_ref["sha256"],
                  "pixels": [72, 114]}
        key = ("def", "TBCSEXT2", 0, 5)
        report = {"schema": "h3.native-review.batch-report.v1", "status": "REVIEW_REQUIRED",
                  "authority": {name: False for name in
                                ("art_approved", "human_approved", "runtime_accepted", "game_deployed")},
                  "manifest": self.ref(self.parent), "official_inventory": self.ref(self.inventory),
                  "render_spec": self.ref(self.parent), "native_receipt": self.ref(self.parent),
                  "items": [{"key": list(key), "source_png": source_ref["path"],
                             "source_sha256": source_ref["sha256"], "outputs": {"candidate_3x": output}}]}
        candidate = {"native_review": self.json_ref("native-three-only.json", report)}
        bound = review._validate_native_review_binding(candidate, key, source_ref)
        self.assertEqual(bound["ref"], candidate["native_review"])

        report["items"][0]["outputs"] = {"candidate_1x": output}
        candidate["native_review"] = self.json_ref("native-three-only.json", report)
        with self.assertRaisesRegex(Exception, "NATIVE_REVIEW_OUTPUTS_MISSING"):
            review._validate_native_review_binding(candidate, key, source_ref)

        report["items"][0]["outputs"] = {"candidate_3x": {**output, "rgba_sha256": "0" * 64}}
        candidate["native_review"] = self.json_ref("native-three-only.json", report)
        with self.assertRaisesRegex(Exception, "NATIVE_REVIEW_OUTPUT_RGBA_DRIFT"):
            review._validate_native_review_binding(candidate, key, source_ref)

    def test_v2_missing_view_cannot_hide_supplied_wrong_native_review(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        candidate["source_view"] = None
        candidate["native_review"] = self.json_ref("wrong-review.json", {
            "schema": "h3.native-review.batch-report.v1", "items": [{
                "key": candidate["key"], "source_sha256": "0" * 64}]})
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("NATIVE_REVIEW_SOURCE_OR_KEY_MISMATCH", report["slots"][5]["errors"][0])

    def test_v2_view_bounds_and_opaque_exclusion_are_measured(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        self.replace_document(candidate, "source_view", source_rect=[0, 0, 73, 114])
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_VIEW_OUTSIDE_IMAGE", report["slots"][5]["errors"][0])
        Image.new("RGBA", (72, 114), (50, 70, 90, 255)).save(self.source)
        candidate = self.strict_candidate(omit_static=True)
        self.replace_document(candidate, "source_view", source_rect=[1, 0, 72, 114])
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_VIEW_EXCLUDES_ALPHA_GE2", report["slots"][5]["errors"][0])

    def test_v2_static_declaration_is_not_measured_registration(self) -> None:
        self.use_v2([self.strict_candidate()])
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "NEEDS_REGISTRATION")
        self.assertIn("static_pixel_correspondence", slot["needs"])

    def test_v2_registered_working_k_canvas_is_not_native_3x(self) -> None:
        candidate = self.strict_candidate()
        candidate["native_mapping"] = None
        self.replace_document(candidate, "static_registration", registered_canvas_3x=[1008, 1596],
                              static_anchor_ltrb_3x=[0, 0, 1008, 1596])
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("STATIC_REGISTRATION_3X_UNITS_MISMATCH", report["slots"][5]["errors"])

    def test_v2_metadata_refs_are_checked_without_requiring_exact_shape(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        self.use_v2([candidate])
        self.manifest_data["official_inventory"]["bytes"] = self.inventory.stat().st_size
        self.save_manifest()
        self.invoke("check")
        self.manifest_data["official_inventory"]["bytes"] += 1
        self.save_manifest()
        failure = self.invoke("check", good=False)
        self.assertIn("REFERENCE_BYTES_DRIFT", failure["error"])

    def test_v2_real_pixel_copy_has_distinct_root_and_detects_rgba_tamper(self) -> None:
        candidate = self.strict_candidate(role="DERIVED_PIXEL_COPY", omit_static=True)
        root = self.ref(self.source)
        leaf = self.root / "padded-copy.png"
        with Image.open(self.source) as parent:
            padded = Image.new("RGBA", (86, 126))
            padded.paste(parent, (7, 6))
            padded.save(leaf)

        def bind_leaf():
            current = self.ref(leaf)
            candidate["source"] = current
            candidate["source_binding"].update({
                "preparation": self.json_ref("copy-prep.json", {
                    "schema": review.DERIVED_PREPARATION_SCHEMA, "mutated": False,
                    "source_sha256": current["sha256"], "request_sha256": candidate["request"]["sha256"]}),
                "parent_chain": self.json_ref("copy-chain.json", {
                    "schema": review.DERIVED_PARENT_CHAIN_SCHEMA, "root_sha256": root["sha256"],
                    "leaf_sha256": current["sha256"], "chain": [root, current]}),
                "pixel_mapping": self.json_ref("copy-mapping.json", {
                    "schema": review.DERIVED_MAPPING_SCHEMA, "source_sha256": current["sha256"],
                    "parent_sha256": root["sha256"], "coordinate_semantics": "ONE_TO_ONE_RGBA",
                    "resampled": False, "alpha_edited": False, "all_pixels_mapped": True,
                    "parent_rect_ltrb": [0, 0, 72, 114], "destination_rect_ltrb": [7, 6, 79, 120]})})
            self.replace_document(candidate, "native_mapping", source_sha256=current["sha256"])
            self.use_v2([candidate])

        bind_leaf()
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(slot["status"], "NEEDS_REGISTRATION")
        self.assertTrue(slot["source_binding"]["derived"]["pixel_copy_verified"])
        self.assertEqual(slot["source_binding"]["derived"]["root"], root)
        with Image.open(leaf) as image:
            image.putpixel((7, 6), (50, 70, 90, 2))
            image.save(leaf)
        bind_leaf()
        report = self.invoke("check", good=False)
        self.assertIn("DERIVED_RGBA_MAPPING_MISMATCH", report["slots"][5]["errors"])

    def test_v2_measured_static_mask_correspondence_and_tamper(self) -> None:
        candidate = self.strict_candidate()
        body = self.root / "copied-static.png"
        with Image.open(self.source) as source:
            source.crop((0, 0, 20, 30)).save(body)
        mask = self.root / "static-mask.png"
        Image.new("L", (20, 30), 255).save(mask)
        self.replace_document(candidate, "static_registration", static_body=self.ref(body),
                              pixel_correspondence={"source": candidate["source"],
                                                    "static_mask": self.ref(mask),
                                                    "body_offset_in_source": [0, 0]})
        self.use_v2([candidate])
        report = self.invoke("check")
        self.assertTrue(report["slots"][5]["static_anchor"]["pixel_correspondence_verified"])
        self.assertFalse(report["slots"][5]["static_anchor"]["static_domain_coverage_verified"])
        self.assertEqual(report["slots"][5]["status"], "NEEDS_REGISTRATION")
        self.assertIn("source_owner_static_dynamic_domains", report["slots"][5]["needs"])
        Image.new("RGBA", (20, 30), (51, 70, 90, 1)).save(body)
        self.replace_document(candidate, "static_registration", static_body=self.ref(body))
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("STATIC_RGBA_CORRESPONDENCE_MISMATCH", report["slots"][5]["errors"])

    def test_owner_static_dynamic_domains_must_cover_every_positive_pixel(self) -> None:
        body = self.root / 'domain-body.png'
        Image.new('RGBA', (4, 3), (20, 30, 40, 1)).save(body)
        static = self.root / 'static-domain.png'; water = self.root / 'water-domain.png'; foam = self.root / 'foam-domain.png'
        Image.new('L', (4, 3), 255).save(static)
        Image.new('L', (4, 3), 0).save(water); Image.new('L', (4, 3), 0).save(foam)
        doc = {'schema': 'h3.town.static-dynamic-domain-ownership.v1', 'key': ['def','TBCSEXT2',0,0],
               'authority': 'SOURCE_OWNER_FROZEN_DOMAIN_OWNERSHIP', 'static_body': self.ref(body),
               'art_standard': self.ref(self.standard), 'static_mask': self.ref(static),
               'dynamic_masks': {'water': self.ref(water), 'foam': self.ref(foam)},
               'art_approved': False, 'game_deployed': False}
        pin = self.json_ref('owner-domains.json', doc)
        result = review._validate_static_domains(pin, self.ref(body), 'TBCSEXT2', self.ref(self.standard))
        self.assertTrue(result['declared_domain_coverage_verified'])
        self.assertFalse(result['semantic_body_or_scene_acceptance_granted'])
        with Image.open(static) as mask:
            mask.putpixel((0, 0), 0); mask.save(static)
        doc['static_mask'] = self.ref(static)
        pin = self.json_ref('owner-domains.json', doc)
        with self.assertRaisesRegex(RuntimeError, 'STATIC_DYNAMIC_DOMAINS_DO_NOT_COVER_ALL_POSITIVE_ALPHA'):
            review._validate_static_domains(pin, self.ref(body), 'TBCSEXT2', self.ref(self.standard))

    def test_one_pixel_static_match_cannot_register_without_owner_domain(self) -> None:
        candidate = self.strict_candidate()
        body = self.root / 'one-pixel-body.png'
        with Image.open(self.source) as source:
            source.crop((0, 0, 20, 30)).save(body)
        mask = self.root / 'one-pixel-mask.png'
        image = Image.new('L', (20, 30)); image.putpixel((0, 0), 255); image.save(mask)
        correspondence = {'source': candidate['source'], 'static_mask': self.ref(mask),
                          'body_offset_in_source': [0, 0]}
        self.replace_document(candidate, 'static_registration', static_body=self.ref(body),
                              pixel_correspondence=correspondence)
        self.use_v2([candidate]); report = self.invoke('check')
        slot = report['slots'][5]
        self.assertTrue(slot['static_anchor']['pixel_correspondence_verified'])
        self.assertEqual(slot['status'], 'NEEDS_REGISTRATION')
        self.assertFalse(slot['static_anchor']['static_domain_coverage_verified'])
        correspondence['domain_ownership'] = self.json_ref('self-asserted-domain.json', {'status': 'trust me'})
        self.replace_document(candidate, 'static_registration', pixel_correspondence=correspondence)
        self.use_v2([candidate]); report = self.invoke('check', good=False)
        self.assertIn('STATIC_DOMAIN_NOT_PINNED_BY_SOURCE_OWNER', report['slots'][5]['errors'])

    def test_v2_unknown_timing_cannot_hide_invented_duration(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        candidate["source_view"] = None
        self.replace_document(candidate, "timing", duration_ms=100)
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("TIMING_UNKNOWN_HAS_INVENTED_VALUES", report["slots"][5]["errors"])

    def test_v2_missing_request_does_not_hide_bad_view(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        candidate["request"] = None
        self.replace_document(candidate, "source_view", source_rect=[0, 0, 72, 115])
        self.use_v2([candidate])
        report = self.invoke("check", good=False)
        self.assertIn("SOURCE_VIEW_OUTSIDE_IMAGE", report["slots"][5]["errors"])

    def test_v2_owned_receipts_replay_as_v2_and_detect_dependency_drift(self) -> None:
        candidate = self.strict_candidate(omit_static=True)
        self.use_v2([candidate])
        self.assertEqual(self.invoke("check")["schema"], "h3.town.process-review.v2")
        self.invoke("run")
        self.invoke("verify")
        Path(candidate["timing"]["path"]).write_text('{}', encoding="utf-8")
        self.invoke("verify", good=False)

    def test_v2_accepts_owner_feedback_identity_without_r9_handoff(self) -> None:
        candidate = self.strict_candidate()
        self.use_owner_feedback_v2(candidate)
        report = self.invoke("check")
        slot = report["slots"][5]
        self.assertEqual(report["identity"], "TBCSEXT2")
        self.assertEqual(slot["status"], "NEEDS_SOURCE_BINDING")
        self.assertIn("source_view", slot["needs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
