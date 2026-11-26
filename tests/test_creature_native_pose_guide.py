import sys
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import creature_native_pose_guide as guide


class NativePoseGuideTests(unittest.TestCase):
    def test_shadow_exception_is_narrow(self):
        ref = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        normal = ref.copy()
        ref.putpixel((0, 0), (0, 0, 0, 64))
        self.assertTrue(guide.equivalent_except_shadow(ref, normal))
        ref.putpixel((1, 0), (10, 0, 0, 64))
        self.assertFalse(guide.equivalent_except_shadow(ref, normal))
        ref.putpixel((1, 0), (0, 0, 0, 200))
        self.assertFalse(guide.equivalent_except_shadow(ref, normal))

    def test_alias_uses_offset_not_name(self):
        rows = [{"offset": 10, "resource": "a"},
                {"offset": 20, "resource": "a"},
                {"offset": 10, "resource": "b"}]
        classes = guide.class_groups(rows)
        self.assertEqual([len(classes[10]), len(classes[20])], [2, 1])

    def test_limb_gate_rejects_missing_or_extra_appendages(self):
        annotation = {"appendage_contract":{"schema":"h3.creature.appendage-contract.v1",
                      "expected_counts":{"arms":2,"hands":2,"legs":2,"feet":2},
                      "required_connections":["arms_to_shoulders"]}}
        mark = {"limb_review":{"source_sha256":"sha","reviewer":"assistant",
                "status":"VISUALLY_REVIEWED","counts":{"arms":2,"hands":2,"legs":2,"feet":2},
                "connections":{"arms_to_shoulders":"CONNECTED"}}}
        self.assertEqual(guide.validate_limb_review(annotation,mark,"sha")["status"],
                         "VISUAL_REVIEW_RECORDED_NOT_HUMAN_APPROVED")
        mark["limb_review"]["counts"]["hands"] = 3
        with self.assertRaisesRegex(ValueError,"APPENDAGE_COUNT_MISMATCH"):
            guide.validate_limb_review(annotation,mark,"sha")
        mark["limb_review"]["counts"]["hands"] = 2
        mark["limb_review"]["connections"]["arms_to_shoulders"] = "UNCERTAIN"
        with self.assertRaisesRegex(ValueError,"APPENDAGE_CONNECTIVITY_REVIEW_FAILED"):
            guide.validate_limb_review(annotation,mark,"sha")

    def test_anatomy_median_uses_unique_offset_owners(self):
        def mark(frame, image_sha, length):
            return {"key": ["def", "TEST", 0, frame], "image_sha256": image_sha,
                    "root_anchor_pixels": [5, 5], "pose_group": "same", "direction": "right",
                    "paths": {"head": [[0, 0], [length, 0]],
                              "torso": [[0, 0], [length, 0]],
                              "body": [[0, 0], [length, 0]],
                              "arm_left": [[0,0],[1,0]], "arm_right": [[0,0],[1,0]],
                              "leg_left": [[0,0],[1,0]], "leg_right": [[0,0],[1,0]]},
                    "measurement_definitions": guide.MEASUREMENT_DEFINITIONS}
        a, b = mark(0, "owner-a", 10), mark(2, "owner-b", 30)
        rows = [({"group": 0, "frame": 0, "offset": 10}, a["root_anchor_pixels"]),
                ({"group": 0, "frame": 1, "offset": 10}, a["root_anchor_pixels"]),
                ({"group": 0, "frame": 2, "offset": 20}, b["root_anchor_pixels"])]
        marks = {("def", "TEST", 0, i): m for i, m in enumerate((a, a, b))}
        result = guide.anatomy_metrics_for_group(rows, marks, 1)["same:right"]
        self.assertEqual(result["unique_offset_owner_count"], 2)
        self.assertTrue(result["aliases_excluded_from_median"])
        self.assertAlmostEqual(result["medians_native"]["body"], 10)
        bad = mark(1, "owner-a", 11)
        marks[("def", "TEST", 0, 1)] = bad
        with self.assertRaisesRegex(ValueError, "NATIVE_ALIAS_LANDMARKS_MUST_MATCH_OWNER"):
            guide.anatomy_metrics_for_group(rows, marks, 1)

    def test_single_offset_owner_is_not_a_scale_pass(self):
        row = {"group": 1, "frame": 0, "offset": 123}
        mark = {"key": ["def", "TEST", 1, 0], "image_sha256": "only",
                "root_anchor_pixels": [5, 5], "pose_group": "entry", "direction": "right",
                "paths": {name: [[0, 0], [10, 10]] for name in guide.MEASUREMENT_DEFINITIONS},
                "measurement_definitions": guide.MEASUREMENT_DEFINITIONS}
        result = guide.anatomy_metrics_for_group([(row, [5, 5])], {tuple(mark["key"]): mark}, 3)["entry:right"]
        slot = result["slots"]["1_0"]
        self.assertEqual(slot["status"], "NOT_COMPARABLE_SINGLE_OWNER")
        self.assertFalse(result["comparison_evidence_sufficient"])
        self.assertTrue(all(v is None for v in slot["deviation_percent"].values()))

    def test_root_step_contract_distinguishes_pose_boundary(self):
        delta, status = guide.native_root_step([10.0, 10.0], [10.2, 10.8], tolerance_native=1.0, same_pose=True)
        self.assertEqual(status, "WITHIN_VERTICAL_TOLERANCE")
        delta, status = guide.native_root_step([10.0, 10.0], [10.2, 12.1], tolerance_native=1.0, same_pose=True)
        self.assertEqual(status, "VERTICAL_STEP_REVIEW_REQUIRED")
        delta, status = guide.native_root_step([10.0, 10.0], [12.0, 15.0], tolerance_native=1.0, same_pose=False)
        self.assertEqual(status, "POSE_BOUNDARY_TRANSITION_REVIEW_REQUIRED")

    def test_reference_owner_must_share_actual_offset_and_checkpoint(self):
        native = {(2, 2): {"offset": 10}, (2, 4): {"offset": 10},
                  (2, 3): {"offset": 20}}
        checkpoint = {"images": {"def:TEST:2:2": {"sha256": "expected"}}}
        ref = {"reference_slot": [2, 2], "path": "2_2.png", "sha256": "expected"}
        self.assertTrue(guide.reference_matches_offset(ref, {"offset": 10}, native, checkpoint, "TEST"))
        self.assertFalse(guide.reference_matches_offset(ref, {"offset": 20}, native, checkpoint, "TEST"))
        ref["sha256"] = "different"
        self.assertFalse(guide.reference_matches_offset(ref, {"offset": 10}, native, checkpoint, "TEST"))

    def test_registered_preview_requires_complete_human_annotations(self):
        row = {"group": 0, "frame": 0, "geometry": {"canvas_1x": [20, 24]},
               "master": {"path": "memory.png", "sha256": "expected"}}
        request = {"identity": "TEST", "slots": [row]}
        counts = {"arms": 2, "hands": 2, "legs": 2, "feet": 2}
        links = {name: "CONNECTED" for name in
                 ("arms_to_shoulders", "hands_to_wrists", "legs_to_pelvis", "feet_to_ankles")}
        annotation = {"key": ["def", "TEST", 0, 0], "image_sha256": "expected",
                      "root_anchor_pixels": [10, 12], "pose_group": "test", "direction": "right",
                      "paths": {"head": [[1, 1], [2, 2]], "torso": [[2, 2], [4, 8]],
                                "body": [[1, 1], [4, 8]], "arm_left": [[2,2],[4,4],[5,5]],
                                "arm_right": [[2,2],[3,4],[4,5]], "leg_left": [[4,8],[4,12],[4,16]],
                                "leg_right": [[4,8],[5,12],[6,16]]},
                      "measurement_definitions": guide.MEASUREMENT_DEFINITIONS,
                      "limb_review": {"source_sha256": "expected", "reviewer": "assistant",
                                      "status": "VISUALLY_REVIEWED", "counts": counts,
                                      "connections": links}}
        annotations = {"schema": guide.LANDMARK_SCHEMA, "annotation_author": "assistant",
                       "status": "VISUALLY_REVIEWED",
                       "appendage_contract": {"schema": "h3.creature.appendage-contract.v1",
                         "expected_counts": counts, "required_connections": list(links)},
                       "frames": [annotation]}
        with patch.object(guide.Image, "open", return_value=Image.new("RGBA", (20, 24), (20, 30, 40, 255))), \
             patch.object(guide, "digest_file", return_value="expected"):
            outputs, roots, metrics = guide.render_registered_layout(request, annotations, 3)
        self.assertIn("registered/group-00-K3-REVIEW.png", outputs)
        self.assertIn("sequence/group-00-K3-REVIEW.apng", outputs)
        self.assertEqual(roots["0"]["0_0"]["translation_native"], [0, 0])
        self.assertIn("test:right", metrics["0"])
        with self.assertRaises(ValueError):
            guide.render_registered_layout(request, {**annotations, "frames": []}, 3)

    def test_action_group_uses_one_root_translation_without_frame_fit(self):
        master_a = Image.new("RGBA", (40, 48), (0, 0, 0, 0))
        master_b = Image.new("RGBA", (40, 48), (0, 0, 0, 0))
        for y in range(14, 18):
            for x in range(12, 16):
                master_a.putpixel((x, y), (50, 60, 70, 255))
        for y in range(16, 20):
            for x in range(16, 20):
                master_b.putpixel((x, y), (50, 60, 70, 255))
        request = {"identity": "TEST", "slots": [
            {"group": 0, "frame": 0, "geometry": {"canvas_1x": [20, 24]},
             "master": {"path": "a.png", "sha256": "a"}},
            {"group": 0, "frame": 1, "geometry": {"canvas_1x": [20, 24]},
             "master": {"path": "b.png", "sha256": "b"}},
        ]}
        connections = {name: "CONNECTED" for name in
                       ("arms_to_shoulders", "hands_to_wrists", "legs_to_pelvis", "feet_to_ankles")}
        expected_counts = {"arms": 2, "hands": 2, "legs": 2, "feet": 2}
        def frame_annotation(frame, image_sha, root):
            origin = 12 if frame == 0 else 16
            p0,p1,p2=[origin,origin+2],[origin+1,origin+2],[origin+2,origin+3]
            paths = {"head": [p0,p1], "torso": [p1,p2], "body": [p0,p1,p2],
                     "arm_left": [p0,p1,p2], "arm_right": [p0,p1,p2],
                     "leg_left": [p0,p1,p2], "leg_right": [p0,p1,p2]}
            limbs = {"source_sha256": image_sha, "reviewer": "assistant",
                     "status": "VISUALLY_REVIEWED", "counts": expected_counts,
                     "connections": connections}
            return {"key": ["def", "TEST", 0, frame], "image_sha256": image_sha,
                    "root_anchor_pixels": root, "pose_group": "same", "direction": "right",
                    "paths": paths, "measurement_definitions": guide.MEASUREMENT_DEFINITIONS,
                    "limb_review": limbs}
        annotations = {"schema": guide.LANDMARK_SCHEMA, "annotation_author": "assistant",
                       "status": "VISUALLY_REVIEWED",
                       "appendage_contract": {"schema": "h3.creature.appendage-contract.v1",
                           "expected_counts": expected_counts,
                           "required_connections": list(connections)},
                       "frames": [frame_annotation(0, "a", [10, 12]),
                                  frame_annotation(1, "b", [12, 14])]}
        opened = {"a.png": master_a.copy(), "b.png": master_b.copy()}
        with patch.object(guide.Image, "open", side_effect=lambda p: opened[str(p)].copy()):
            products, roots, metrics = guide.render_registered_layout(request, annotations, 3)
            data = products["registered/group-00-K3-REVIEW.png"]
        output = Image.open(__import__("io").BytesIO(data)).convert("RGBA")
        sheet_w = 40
        # Root source pixels are converted by K before fixed-root registration.
        self.assertEqual(roots["0"]["0_0"]["registered_root_native"],
                         roots["0"]["0_1"]["registered_root_native"])
        self.assertEqual(output.size, (80, 48))
        self.assertEqual(roots["0"]["0_0"]["translation_native"], [0, 0])
        self.assertAlmostEqual(roots["0"]["0_1"]["translation_native"][0], -2/3)
        self.assertAlmostEqual(roots["0"]["0_1"]["translation_native"][1], -2/3)
        self.assertEqual(metrics["0"]["same:right"]["slots"]["0_1"]["status"], "PROVISIONAL_WITHIN_5_PERCENT")
        with Image.open(__import__("io").BytesIO(products["sequence/group-00-K3-REVIEW.apng"])) as anim:
            self.assertEqual(getattr(anim, "n_frames", 1), 2)

    def test_preview_refuses_below_3x_source_capacity(self):
        ann = {"status":"VISUALLY_REVIEWED","annotation_author":"assistant","frames":[]}
        with self.assertRaisesRegex(ValueError, "COMMON_K_MUST_MEET_FORMAL_3X_CAPACITY"):
            guide.render_registered_layout({"identity":"TEST","slots":[]},ann,2.99)

    def test_geometry_checks_all_coordinates(self):
        native = {"frame_header": [1, 1, 450, 400, 39, 84, 178, 183], "resource": "F.PCX"}
        row = {"geometry": {"canvas_1x": [450, 400], "crop_1x": [39, 84], "origin_1x": [178, 183]},
               "resource": "f.pcx"}
        self.assertTrue(guide.same_geometry(native, row))
        row["geometry"]["origin_1x"] = [179, 183]
        self.assertFalse(guide.same_geometry(native, row))

    def test_common_native_origin_is_preserved(self):
        source = Image.new("RGBA", (80, 100), (0, 0, 0, 0))
        for y in range(35, 56):
            for x in range(35, 56):
                source.putpixel((x, y), (11, 88, 177, 255))
        row = {"reference": {"path": "in-memory-reference.png"}}
        box = [20, 20, 70, 80]
        with patch.object(guide.Image, "open", return_value=source.copy()):
            rendered = guide.guide_image(row, box)
        self.assertEqual(rendered.size, (100, 120))
        # An opaque interior pixel lands at (x - shared_origin) * 2.
        self.assertEqual(rendered.getpixel(((45-box[0])*2, (45-box[1])*2)), (11, 88, 177))

    def test_anatomical_point_must_touch_visible_subject_alpha(self):
        alpha=Image.new("L",(20,20),0);alpha.putpixel((10,10),255)
        self.assertTrue(guide.point_near_visible_subject(alpha,[10,10]))
        self.assertFalse(guide.point_near_visible_subject(alpha,[0,0]))


if __name__ == "__main__":
    unittest.main()
