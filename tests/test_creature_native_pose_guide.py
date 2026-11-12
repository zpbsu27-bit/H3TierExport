import sys
import unittest
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

    def test_reference_owner_must_share_actual_offset_and_checkpoint(self):
        native = {(2, 2): {"offset": 10}, (2, 4): {"offset": 10},
                  (2, 3): {"offset": 20}}
        checkpoint = {"images": {"def:TEST:2:2": {"sha256": "expected"}}}
        ref = {"reference_slot": [2, 2], "path": "2_2.png", "sha256": "expected"}
        self.assertTrue(guide.reference_matches_offset(ref, {"offset": 10}, native, checkpoint, "TEST"))
        self.assertFalse(guide.reference_matches_offset(ref, {"offset": 20}, native, checkpoint, "TEST"))
        ref["sha256"] = "different"
        self.assertFalse(guide.reference_matches_offset(ref, {"offset": 10}, native, checkpoint, "TEST"))

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


if __name__ == "__main__":
    unittest.main()
