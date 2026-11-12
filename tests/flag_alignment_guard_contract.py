"""Fail-closed contracts for town flag geometry and expansion provenance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flag_alignment_guard.py"
spec = importlib.util.spec_from_file_location("flag_alignment_guard", SCRIPT)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FlagAlignmentGuardContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="flag-alignment-guard-")
        self.root = Path(self.temp.name)
        self.identity = "AVCRAMZ0"
        self.reference = self.root / self.identity / "0_0.png"
        self.reference.parent.mkdir(parents=True)
        Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(self.reference)
        self.candidate = self.root / "candidate-3x.png"
        canvas = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.line((42, 41, 42, 59), fill=(140, 92, 40, 255), width=3)
        draw.line((150, 41, 150, 59), fill=(140, 92, 40, 255), width=3)
        draw.rectangle((34, 51, 50, 67), fill=(70, 110, 55, 255))
        draw.rectangle((142, 51, 158, 67), fill=(70, 110, 55, 255))
        canvas.save(self.candidate)

        self.flag_sprite = self.root / "flag_grey.png"
        flag_sprite = Image.new("RGBA", (43, 25), (0, 0, 0, 0))
        ImageDraw.Draw(flag_sprite).line((42, 2, 42, 20), fill=(90, 90, 90, 255), width=1)
        flag_sprite.save(self.flag_sprite)
        self.coordinate_authority = self.root / "spriteFlagsInfo.txt"
        self.coordinate_authority.write_text("AVCRAMZ0 2 0 13 0 50 13 1\n", encoding="utf-8")
        self.anchor_audit = self.root / "flagpole-entry-axis-audit.json"
        self.anchor_audit_doc = {
            "schema": guard.FLAG_ANCHOR_AUDIT_SCHEMA,
            "operation_id": "synthetic-test",
            "coordinate_authority": str(self.coordinate_authority),
            "coordinate_authority_sha256": sha(self.coordinate_authority),
            "flag_grey_path": str(self.flag_sprite),
            "flag_grey_sha256": sha(self.flag_sprite),
            "flag_red_alpha_geometry_matches_grey": True,
            "flag_sprite_pixels_3x": [43, 25],
            "pole_foot_pixel_unmirrored": [42, 20],
            "pole_foot_pixel_mirrored": [0, 20],
            "assets": [{
                "identity": self.identity,
                "status": "EXACT_FLAG_KEY",
                "fixed_flagpole_ground_points_3x": [
                    {"native_flag_anchor": [0, 13], "mirrored_across_vertical_axis": False,
                     "flagpole_ground_3x": [42, 59]},
                    {"native_flag_anchor": [50, 13], "mirrored_across_vertical_axis": True,
                     "flagpole_ground_3x": [150, 59]},
                ],
            }],
        }
        self._write_anchor_audit()

        self.flag_face = self.root / "flag-face-mask.png"
        face = Image.new("RGBA", (14, 5), (0, 0, 0, 0))
        mask_draw = ImageDraw.Draw(face)
        rows = [
            range(9, 14),   # 5
            range(6, 14),   # 8
            range(0, 11),   # 11; includes the measured hang endpoint
            range(1, 12),   # 11
            range(0, 7),    # 7
        ]
        for y, xs in enumerate(rows):
            for x in xs:
                face.putpixel((x, y), (255, 255, 255, 255))
        self.assertEqual(sum(1 for p in face.getchannel("A").tobytes() if p), 42)
        face.save(self.flag_face)

        self.spec_path = self.root / "rampart-spec.json"
        self.spec_doc = {
            "schema": guard.SPEC_SCHEMA,
            "identity": self.identity,
            "native_reference": {
                "path": str(self.reference),
                "sha256": sha(self.reference),
                "canvas_px": [64, 64],
            },
            "fit_anchor": "bottom_right",
            "target_scale": 3,
            "alpha_threshold": 1,
            "flag_status": "EXACT_FLAG_KEY",
            "flag_anchor_authority": {
                "path": str(self.anchor_audit),
                "sha256": sha(self.anchor_audit),
            },
            "flag_face": {
                "path": str(self.flag_face),
                "sha256": sha(self.flag_face),
                "source_image": {"path": str(self.flag_sprite), "sha256": sha(self.flag_sprite)},
                "hang_point_local_1x": [0, 2],
            },
            "flags": [
                {
                    "side": "left",
                    "pole_top_3x": [42, 41],
                    "pole_foot_3x": [42, 59],
                    "pole_centerline_3x": [[42, 41], [42, 59]],
                    "flag_origin_3x": [42, 35],
                    "ground_support_rect_3x": [34, 51, 51, 68],
                },
                {
                    "side": "right",
                    "pole_top_3x": [150, 41],
                    "pole_foot_3x": [150, 59],
                    "pole_centerline_3x": [[150, 41], [150, 59]],
                    "flag_origin_3x": [150, 35],
                    "ground_support_rect_3x": [142, 51, 159, 68],
                },
            ],
        }
        self._write_spec()
        self.receipt = self.root / "MASTER_1-test.json"
        self.verify_report = self.root / "expand-verify.json"
        self._write_receipt()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_spec(self) -> None:
        self.spec_path.write_text(json.dumps(self.spec_doc), encoding="utf-8")

    def _write_anchor_audit(self) -> None:
        self.anchor_audit_doc["coordinate_authority_sha256"] = sha(self.coordinate_authority)
        self.anchor_audit_doc["flag_grey_sha256"] = sha(self.flag_sprite)
        self.anchor_audit.write_text(json.dumps(self.anchor_audit_doc), encoding="utf-8")
        if hasattr(self, "spec_doc"):
            self.spec_doc["flag_anchor_authority"]["sha256"] = sha(self.anchor_audit)

    def _write_receipt(self, fit_anchor: str = "bottom_right") -> None:
        self._write_receipt_for(self.identity, self.reference, self.candidate,
                                self.receipt, self.verify_report, fit_anchor)

    def _write_receipt_for(self, identity: str, reference: Path, candidate: Path,
                           receipt_path: Path, verify_report_path: Path,
                           fit_anchor: str = "bottom_right") -> None:
        digest = sha(reference)
        receipt_path.write_text(json.dumps({
            "schema": guard.RECEIPT_SCHEMA,
            "target_scale": 3,
            "output": str(candidate),
            "output_pixels": [192, 192],
            "official_output_pixels": [192, 192],
            "png_sha256": sha(candidate),
            "contract": {
                "entry": {
                    "fit_anchor": fit_anchor,
                    "target_scale": 3,
                    "reference": str(reference),
                    "reference_sha256": digest,
                    "reference_pixels": [64, 64],
                    "base_pixels": [64, 64],
                    "output": str(candidate),
                    "source": str(self.root / identity / "0_0-mother.png"),
                }
            },
            "silhouette": {
                "fit_anchor": fit_anchor,
                "reference": str(reference),
                "reference_sha256": digest,
                "reference_pixels": [64, 64],
                "overflow": "NONE",
            },
        }), encoding="utf-8")
        verify_report_path.write_text(json.dumps({
            "schema": guard.VERIFY_SCHEMA,
            "status": "COMPLETED",
            "failed": 0,
            "results": [{"receipt": str(receipt_path), "status": "VERIFIED"}],
        }), encoding="utf-8")

    def verify(self) -> dict:
        return guard.verify_building_flag_alignment(self.candidate, self.spec_path, self.receipt,
                                                    self.verify_report)

    def test_verified_pair_requires_both_distinct_flag_tips_and_feet(self) -> None:
        result = self.verify()
        self.assertEqual(result["status"], "REVIEW_REQUIRED", result)
        self.assertEqual([item["side"] for item in result["flags"]], ["left", "right"])
        self.assertEqual([item["pole_top_3x"] for item in result["flags"]], [[42, 41], [150, 41]])
        self.assertEqual([item["flag_hangpoint_3x"] for item in result["flags"]], [[42, 41], [150, 41]])
        self.assertEqual([item["ground_support_ratio"] for item in result["flags"]], [1.0, 1.0])

    def test_center_anchor_receipt_is_rejected_even_when_pixels_are_supported(self) -> None:
        self._write_receipt(fit_anchor="center")
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "RECEIPT_FIT_ANCHOR_NOT_BOTTOM_RIGHT")

    def test_flag_hang_endpoint_cannot_use_center_or_shifted_point(self) -> None:
        self.spec_doc["flags"][0]["flag_origin_3x"] = [45, 35]
        self._write_spec()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "FLAG_HANGPOINT_MISALIGNED")

    def test_flag_foot_cannot_be_copied_from_another_town_identity(self) -> None:
        self.spec_doc["flags"][0]["pole_foot_3x"] = [45, 59]
        self._write_spec()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "FLAG_FOOT_DOES_NOT_MATCH_AUTHORITY")

    def test_flag_anchor_authority_is_sha_pinned(self) -> None:
        self.anchor_audit.write_text(self.anchor_audit.read_text(encoding="utf-8") + " ", encoding="utf-8")
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "FLAG_ANCHOR_AUTHORITY_SHA_DRIFT")

    def test_17_by_17_support_patch_must_be_fully_inside_and_fully_opaque(self) -> None:
        with Image.open(self.candidate) as image:
            candidate = image.convert("RGBA")
        candidate.putpixel((34, 51), (0, 0, 0, 0))
        candidate.save(self.candidate)
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "RECEIPT_OUTPUT_SHA_MISMATCH")
        self._write_receipt()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "GROUND_SUPPORT_INCOMPLETE")

    def test_support_window_may_not_be_clamped_at_canvas_edge(self) -> None:
        self.spec_doc["flags"][1]["ground_support_rect_3x"] = [176, 180, 193, 197]
        self._write_spec()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "GROUND_SUPPORT_RECT_OUT_OF_BOUNDS")

    def test_pole_shaft_gap_fails_even_when_top_and_foot_are_present(self) -> None:
        with Image.open(self.candidate) as image:
            candidate = image.convert("RGBA")
        for y in range(44, 50):
            for x in range(41, 44):
                candidate.putpixel((x, y), (0, 0, 0, 0))
        candidate.save(self.candidate)
        self._write_receipt()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "POLE_SHAFT_DISCONNECTED")

    def test_flag_face_requires_exact_native_14_by_5_and_42_pixels(self) -> None:
        with Image.open(self.flag_face) as image:
            changed = image.convert("RGBA")
        changed.putpixel((0, 2), (0, 0, 0, 0))
        changed.save(self.flag_face)
        self.spec_doc["flag_face"]["sha256"] = sha(self.flag_face)
        self._write_spec()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "FLAG_FACE_SHAPE_MISMATCH")

    def test_one_shared_pole_top_cannot_replace_two_sided_landmarks(self) -> None:
        self.spec_doc["flags"][1]["pole_top_3x"] = [42, 41]
        self._write_spec()
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "FLAG_HANGPOINT_MISALIGNED")

    def test_proof_contains_individually_placed_faces_and_support_windows(self) -> None:
        proof = self.root / "proof.png"
        result = guard.verify_building_flag_alignment(
            self.candidate, self.spec_path, self.receipt, self.verify_report, proof)
        self.assertEqual(result["status"], "REVIEW_REQUIRED", result)
        self.assertTrue(proof.is_file())
        with Image.open(proof) as image:
            self.assertEqual(image.size, (192, 192))

    def test_unknown_or_unregistered_asset_cannot_be_implicitly_passed(self) -> None:
        result = guard.verify_building_flag_alignment(
            self.candidate, self.root / "missing-spec.json", self.receipt, self.verify_report)
        self.assertEqual(result["status"], "FAIL")

    def test_batch_reports_each_item_and_fails_closed_on_missing_spec(self) -> None:
        batch = self.root / "batch.json"
        batch.write_text(json.dumps({
            "schema": guard.BATCH_SCHEMA,
            "items": [{
                "identity": self.identity,
                "building_3x": str(self.candidate),
                "spec": str(self.spec_path),
                "receipt": str(self.receipt),
                "verify_report": str(self.verify_report),
            }, {
                "identity": self.identity,
                "building_3x": str(self.candidate),
                "receipt": str(self.receipt),
                "verify_report": str(self.verify_report),
            }],
        }), encoding="utf-8")
        result = guard.verify_batch(batch)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual((result["items"], result["passed"], result["review_required"], result["failed"]), (2, 0, 1, 1))

    def test_batch_identity_must_match_the_pinned_spec(self) -> None:
        batch = self.root / "identity-mismatch-batch.json"
        batch.write_text(json.dumps({
            "schema": guard.BATCH_SCHEMA,
            "items": [{
                "identity": "AVCTOWX0",
                "building_3x": str(self.candidate),
                "spec": str(self.spec_path),
                "receipt": str(self.receipt),
                "verify_report": str(self.verify_report),
            }],
        }), encoding="utf-8")
        result = guard.verify_batch(batch)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["results"][0]["errors"][-1]["code"], "BATCH_IDENTITY_MISMATCH")

    def test_receipt_batch_audit_exposes_center_anchor_without_calling_it_flag_pass(self) -> None:
        self._write_receipt(fit_anchor="center")
        batch = self.root / "receipt-batch.json"
        batch.write_text(json.dumps({
            "schema": guard.RECEIPT_BATCH_SCHEMA,
            "items": [{
                "identity": self.identity,
                "building_3x": str(self.candidate),
                "receipt": str(self.receipt),
                "verify_report": str(self.verify_report),
            }],
        }), encoding="utf-8")
        result = guard.audit_receipt_batch(batch)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["results"][0]["fit_anchor"], "center")
        self.assertEqual(result["results"][0]["errors"][0]["code"],
                         "RECEIPT_FIT_ANCHOR_NOT_BOTTOM_RIGHT")
        self.assertIn("geometry still requires verify", result["claim_boundary"])

    def test_receipt_only_audit_passes_provenance_but_makes_no_flag_geometry_claim(self) -> None:
        result = guard.audit_expansion_receipt(
            self.candidate, self.receipt, self.verify_report, self.identity)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["fit_anchor"], "bottom_right")
        self.assertTrue((result["candidate_sha256"]))

    def test_full_gate_requires_matching_successful_expand_verify_report(self) -> None:
        self.verify_report.write_text(json.dumps({
            "schema": guard.VERIFY_SCHEMA,
            "status": "COMPLETED",
            "failed": 1,
            "results": [{"receipt": str(self.receipt), "status": "FAILED"}],
        }), encoding="utf-8")
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["errors"][0]["code"], "EXPAND_VERIFY_REPORT_FAILED")

    def test_official_no_flag_key_is_explicitly_not_applicable_not_a_false_pass(self) -> None:
        no_flag_id = "AVCHFOR0"
        self.anchor_audit_doc["assets"].append({
            "identity": no_flag_id,
            "status": "NO_EXACT_FLAG_KEY",
            "fixed_flagpole_ground_points_3x": [],
        })
        self._write_anchor_audit()
        reference = self.root / no_flag_id / "0_0.png"
        reference.parent.mkdir(parents=True)
        Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(reference)
        candidate = self.root / no_flag_id / "0_0_3X.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (192, 192), (0, 0, 0, 0)).save(candidate)
        receipt = self.root / "no-flag-MASTER_1.json"
        verify_report = self.root / "no-flag-expand-verify.json"
        self._write_receipt_for(no_flag_id, reference, candidate, receipt, verify_report)
        spec_doc = json.loads(json.dumps(self.spec_doc))
        spec_doc["identity"] = no_flag_id
        spec_doc["native_reference"].update({"path": str(reference), "sha256": sha(reference)})
        spec_doc["flag_status"] = "NO_EXACT_FLAG_KEY"
        spec_doc["flags"] = []
        spec_doc.pop("flag_face")
        spec_doc["flag_anchor_authority"]["sha256"] = sha(self.anchor_audit)
        no_flag_spec = self.root / "no-flag-spec.json"
        no_flag_spec.write_text(json.dumps(spec_doc), encoding="utf-8")
        result = guard.verify_building_flag_alignment(candidate, no_flag_spec, receipt, verify_report)
        self.assertEqual(result["status"], "REVIEW_REQUIRED", result)
        self.assertEqual(result["flag_status"], "NO_EXACT_FLAG_KEY")
        batch = self.root / "no-flag-batch.json"
        batch.write_text(json.dumps({
            "schema": guard.BATCH_SCHEMA,
            "items": [{
                "identity": no_flag_id,
                "building_3x": str(candidate),
                "spec": str(no_flag_spec),
                "receipt": str(receipt),
                "verify_report": str(verify_report),
            }],
        }), encoding="utf-8")
        batch_result = guard.verify_batch(batch)
        self.assertEqual(batch_result["status"], "REVIEW_REQUIRED", batch_result)
        self.assertEqual((batch_result["passed"], batch_result["review_required"],
                          batch_result["failed"]), (0, 1, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
