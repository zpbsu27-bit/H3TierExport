"""Town-master integer-multiple preparation contracts; original pixels stay immutable."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageChops

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "town_master_prepare.py"
spec = importlib.util.spec_from_file_location("town_master_prepare", SCRIPT)
assert spec and spec.loader
prepare_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_module)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TownMasterPrepareContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="town-master-prepare-")
        self.root = Path(self.temp.name)
        self.masters = self.root / "masters"
        self.complete = self.root / "complete"
        self.archive = self.root / "archive"
        self.masters.mkdir(); self.complete.mkdir(); self.archive.mkdir()
        self.inventory = self.root / "inventory.json.gz"
        self.master_dir = self.masters / "SYNTH"
        self.master_dir.mkdir()
        self.native_dir = self.complete / "SYNTH"
        self.native_dir.mkdir()
        self.native = self.native_dir / "0_0.png"
        native = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
        # Official crop starts at (2,3); its visible baseline is y=8 and center x=10.
        for y in range(3 + 2, 3 + 8):
            for x in range(2 + 5, 2 + 15):
                native.putpixel((x, y), (80, 90, 100, 255))
        native.save(self.native)
        self.row = {"kind": "def", "identity": "SYNTH", "group": 0, "frame": 0,
                    "resource": "synthetic.pcx", "category": "towns_buildings",
                    "canvas": [24, 16], "crop": [20, 10], "anchor": [2, 3]}
        self._write_inventory()
        self.master = self.master_dir / "0_0.png"
        self._write_master((10, 10, 90, 50))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_inventory(self) -> None:
        self.inventory.write_bytes(gzip.compress(json.dumps({"rows": [self.row]}).encode("utf-8")))
        self.inventory_sha = sha(self.inventory)

    def _write_master(self, box: tuple[int, int, int, int]) -> None:
        image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        for y in range(box[1], box[3]):
            for x in range(box[0], box[2]):
                image.putpixel((x, y), (120, 70, 40, 255))
        image.save(self.master)
        image.close()
        self.master_sha = sha(self.master)

    def run_prepare(self, name: str = "out") -> dict:
        return prepare_module.prepare(self.inventory, self.inventory_sha, self.masters,
                                      self.complete, self.archive, self.root / name,
                                      identities=("SYNTH",))

    def test_lossless_integer_multiple_and_one_resample_batch_contract(self) -> None:
        result = self.run_prepare()
        out = self.root / "out"
        self.assertEqual((result["frames"], result["ready_3x"], result["api_required"], result["blocked"]),
                         (1, 1, 0, 0))
        row = json.loads((out / "frame-audit.json").read_text(encoding="utf-8"))["frames"][0]
        # Alignment to official visible baseline needs 5X, not 4X; no image pixels are resampled.
        self.assertEqual(row["fit_canvas_multiple"], 5)
        self.assertEqual(row["normalized_pixels"], [100, 50])
        self.assertEqual(row["normalized_offset"], [10, 0])
        normalized = Path(row["normalized_path"])
        with Image.open(self.master) as source, Image.open(normalized) as output:
            source_rgba = source.convert("RGBA").crop((10, 10, 90, 50))
            pasted = output.convert("RGBA").crop((10, 0, 90, 40))
            self.assertIsNone(ImageChops.difference(source_rgba, pasted).getbbox())
            self.assertEqual(output.size, (100, 50))
        batch = json.loads((out / "expand-batch.json").read_text(encoding="utf-8"))
        item = batch["items"][0]
        self.assertEqual(item["base_pixels"], [20, 10])
        self.assertEqual(item["target_scale"], 3)
        self.assertEqual(item["source_pixels"], [100, 50])
        self.assertTrue(item["padded_master_allowed"])
        self.assertEqual(sha(self.master), self.master_sha)
        self.assertEqual(sha(self.native), row["official_native_sha256"])
        self.assertEqual(result["original_master_files_modified"], 0)

    def test_opaque_official_crop_uses_center_fallback_not_false_baseline(self) -> None:
        native = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
        for y in range(3, 13):
            for x in range(2, 22):
                native.putpixel((x, y), (80, 90, 100, 255))
        native.save(self.native)
        result = self.run_prepare("opaque-out")
        self.assertEqual(result["ready_3x"], 1)
        row = json.loads((self.root / "opaque-out" / "frame-audit.json").read_text(encoding="utf-8"))["frames"][0]
        self.assertEqual(row["official_alignment_mode"], "FULL_CROP_OPAQUE_NO_ALPHA_ANCHOR")
        self.assertEqual(row["fit_canvas_multiple"], 4)
        self.assertEqual(row["normalized_offset"], [0, 0])
        self.assertEqual(row["normalized_pixels"], [80, 40])
        self.assertEqual(sha(self.master), self.master_sha)

    def test_below_three_x_is_filtered_to_api_and_not_exported(self) -> None:
        self._write_master((20, 20, 70, 45))  # 2.5X on both axes.
        result = self.run_prepare("api-out")
        out = self.root / "api-out"
        self.assertEqual((result["ready_3x"], result["api_required"]), (0, 1))
        self.assertFalse((out / "expand-batch.json").exists())
        self.assertEqual(len(list((out / "normalized-masters").rglob("*.png"))), 0)
        lines = (out / "api-needed.csv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("2.5", lines[1])
        self.assertEqual(sha(self.master), self.master_sha)

    def test_alpha_halo_that_reduces_core_needs_visual_review(self) -> None:
        image = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
        for y in range(10, 50):
            for x in range(60, 140):
                image.putpixel((x, y), (120, 70, 40, 255))
        image.putpixel((0, 10), (120, 70, 40, 1))
        image.putpixel((199, 49), (120, 70, 40, 1))
        image.save(self.master)
        image.close()
        self.master_sha = sha(self.master)

        result = self.run_prepare("alpha-halo-warning-out")
        out = self.root / "alpha-halo-warning-out"
        row = json.loads((out / "frame-audit.json").read_text(encoding="utf-8"))["frames"][0]
        self.assertEqual((result["ready_3x"], result["warning_3x"], result["blocked"]), (1, 1, 0))
        self.assertEqual(result["status"], "PREPARED_WITH_REVIEW_GATES")
        self.assertEqual(row["status"], "WARNING_CORE_SHRINK_REQUIRES_ART_REVIEW")
        self.assertTrue(row["review_required"])
        self.assertEqual(row["fit_canvas_multiple"], 10)
        self.assertEqual(row["projected_core_pixels_3x"], [24.0, 12.0])
        self.assertEqual(row["required_core_pixels_3x"], [30, 18])
        self.assertTrue((out / "expand-batch.json").exists())
        self.assertEqual(len(list((out / "normalized-masters").rglob("*.png"))), 1)
        self.assertEqual(sha(self.master), self.master_sha)

    def test_extreme_positive_alpha_halo_blocks_core_shrink_below_half_3x(self) -> None:
        self._write_master((10, 10, 90, 50))
        with Image.open(self.master) as image_file:
            image = image_file.convert("RGBA")
            image.putpixel((0, 0), (120, 70, 40, 1))
            image.putpixel((99, 79), (120, 70, 40, 1))
            image.save(self.master)
            image.close()
        self.master_sha = sha(self.master)

        result = self.run_prepare("alpha-halo-out")
        out = self.root / "alpha-halo-out"
        row = json.loads((out / "frame-audit.json").read_text(encoding="utf-8"))["frames"][0]
        self.assertEqual((result["ready_3x"], result["blocked"]), (0, 1))
        self.assertEqual(result["status"], "BLOCKED_NO_3X_READY_FRAMES")
        self.assertEqual(row["status"], "BLOCKED_CORE_SHRINK_BELOW_HALF_OF_OFFICIAL_3X")
        self.assertEqual(row["fit_canvas_multiple"], 15)
        self.assertEqual(row["projected_core_pixels_3x"], [16.0, 8.0])
        self.assertEqual(row["required_core_pixels_3x"], [30, 18])
        self.assertFalse((out / "expand-batch.json").exists())
        self.assertEqual(len(list((out / "normalized-masters").rglob("*.png"))), 0)
        self.assertEqual(sha(self.master), self.master_sha)

    def test_inventory_hash_mismatch_writes_nothing(self) -> None:
        with self.assertRaisesRegex(ValueError, "INVENTORY_SHA256_CHANGED"):
            prepare_module.prepare(self.inventory, "0" * 64, self.masters,
                                   self.complete, self.archive, self.root / "bad",
                                   identities=("SYNTH",))
        self.assertFalse((self.root / "bad").exists())
        self.assertEqual(sha(self.master), self.master_sha)

    def test_missing_or_duplicate_slot_coverage_is_blocked_before_writes(self) -> None:
        original = self.master.read_bytes()
        self.master.unlink()
        with self.assertRaisesRegex(ValueError, "TOWN_MASTER_SLOT_COVERAGE_BLOCKED"):
            self.run_prepare("coverage-out")
        self.assertFalse((self.root / "coverage-out").exists())
        self.master.write_bytes(original)
        duplicate_dir = self.masters / "nested" / "SYNTH"
        duplicate_dir.mkdir(parents=True)
        Image.open(self.master).save(duplicate_dir / "0_0.png")
        with self.assertRaisesRegex(ValueError, "TOWN_MASTER_SLOT_COVERAGE_BLOCKED"):
            self.run_prepare("duplicate-out")
        self.assertFalse((self.root / "duplicate-out").exists())

    def test_official_canvas_or_crop_drift_is_rejected_before_writes(self) -> None:
        Image.new("RGBA", (25, 16), (0, 0, 0, 0)).save(self.native)
        with self.assertRaisesRegex(ValueError, "OFFICIAL_NATIVE_CANVAS_MISMATCH"):
            self.run_prepare("native-drift-out")
        self.assertFalse((self.root / "native-drift-out").exists())
        self.assertEqual(sha(self.master), self.master_sha)


if __name__ == "__main__":
    unittest.main()
