"""Fail-closed tests for official full-canvas geometry in VCMI sprite packages."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import json

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import campaign_vcmi_mod as publisher


def ah01_group0_frame0() -> dict:
    return {
        "kind": "def", "identity": "AH01_", "group": 0, "frame": 0,
        "native_lod": "H3sprite.lod", "resource": "ah01_05.pcx",
        "canvas": [96, 64], "crop": [32, 58], "anchor": [32, 6],
    }


class CampaignVcmIModGeometryContract(unittest.TestCase):
    def test_full_canvas_3x_preserves_official_crop_origin(self) -> None:
        native = ah01_group0_frame0()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "0_0.png"
            image = Image.new("RGBA", (288, 192), (0, 0, 0, 0))
            image.putpixel((96, 18), (255, 200, 100, 255))
            image.putpixel((191, 191), (255, 200, 100, 255))
            image.save(path)
            report = publisher.validate_full_canvas_sprite(path, native, "AH01_", 0, 0)
        self.assertEqual(report["canvas_1x"], [96, 64])
        self.assertEqual(report["crop_1x"], [32, 58])
        self.assertEqual(report["origin_1x"], [32, 6])
        self.assertEqual(report["expected_full_canvas_3x"], [288, 192])
        self.assertEqual(report["official_crop_rect_3x"], [96, 18, 192, 192])

    def test_crop_sized_export_is_rejected(self) -> None:
        native = ah01_group0_frame0()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cropped.png"
            image = Image.new("RGBA", (96, 174), (0, 0, 0, 0))
            image.putpixel((0, 0), (255, 255, 255, 255))
            image.save(path)
            with self.assertRaisesRegex(ValueError, "MAP_SPRITE_FULL_CANVAS_3X_REQUIRED"):
                publisher.validate_full_canvas_sprite(path, native, "AH01_", 0, 0)

    def test_wrongly_centered_alpha_is_rejected(self) -> None:
        native = ah01_group0_frame0()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "centered.png"
            image = Image.new("RGBA", (288, 192), (0, 0, 0, 0))
            image.putpixel((10, 50), (255, 255, 255, 255))
            image.save(path)
            with self.assertRaisesRegex(ValueError, "MAP_SPRITE_ALPHA_OUTSIDE_OFFICIAL_CROP"):
                publisher.validate_full_canvas_sprite(path, native, "AH01_", 0, 0)

    def test_mounted_hero_package_requires_all_45_native_slots(self) -> None:
        rows = [
            {"group": group, "frame": frame}
            for group in range(10)
            for frame in range(1 if group < 5 else 8)
        ]
        by_identity = {"AH01_": rows}
        slots = {"AH01_": {(row["group"], row["frame"]) for row in rows}}
        result = publisher.validate_mounted_hero_coverage(slots, by_identity)
        self.assertEqual(result, [{"identity": "AH01_", "official_slots": 45}])

        slots["AH01_"].remove((7, 7))
        with self.assertRaisesRegex(ValueError, "MOUNTED_HERO_PACKAGE_MUST_CONTAIN_ALL_45_OFFICIAL_SLOTS"):
            publisher.validate_mounted_hero_coverage(slots, by_identity)

    def test_single_frame_resource_is_not_misclassified_as_mounted_hero(self) -> None:
        native_rows = [{"group": 0, "frame": frame} for frame in range(10)]
        self.assertFalse(publisher.is_mounted_hero_identity(native_rows))
        self.assertEqual(
            publisher.validate_mounted_hero_coverage({"CAMPSTAR": {(0, 0)}},
                                                      {"CAMPSTAR": native_rows}),
            [],
        )

    def _build_fixture(self, root: Path, size: tuple[int, int]) -> tuple[dict, dict]:
        source_root = root / "source"
        source_root.mkdir()
        source = source_root / "0_0.png"
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        image.putpixel((size[0] // 2, size[1] // 2), (180, 90, 40, 255))
        image.save(source)
        item = {
            "key": "d/test.def/0_0.png", "source": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "width": size[0], "height": size[1], "tier": 3,
            "identity": "TEST", "native_pixels": [8, 8],
        }
        manifest = {"status": "READY", "tier": 3, "items": [item]}
        manifest_path = root / "manifest.json"
        raw_manifest = (json.dumps(manifest, separators=(",", ":")) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw_manifest)
        output_root = root / "candidate"
        config = {
            "manifest_path": str(manifest_path),
            "expected_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
            "expected_key_count": 1,
            "allowed_source_roots": [str(source_root)],
            "candidate_root": str(output_root),
            "mod_id": "test_map_sprites",
            "operation_id": "geometry-contract-test",
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        native = {
            "kind": "def", "identity": "TEST", "group": 0, "frame": 0,
            "native_lod": "H3sprite.lod", "resource": "test.pcx",
            "canvas": [8, 8], "crop": [4, 6], "anchor": [2, 1],
        }
        inventory = ({"path": "synthetic-native-inventory.json.gz",
                      "sha256": "a" * 64, "bytes": 123},
                     {("TEST", 0, 0): native}, {"TEST": [native]})
        return {"path": config_path, "output": output_root}, inventory

    def test_mod_build_rejects_crop_output_before_creating_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, inventory = self._build_fixture(Path(temporary), (12, 18))
            with patch.object(publisher, "load_native_def_authority", return_value=inventory):
                with self.assertRaisesRegex(ValueError, "MAP_SPRITE_FULL_CANVAS_3X_REQUIRED"):
                    publisher.build_mod(fixture["path"])
            self.assertFalse(fixture["output"].exists())

    def test_mod_build_receipt_records_native_canvas_crop_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, inventory = self._build_fixture(Path(temporary), (24, 24))
            source = Path(json.loads(fixture["path"].read_text(encoding="utf-8"))["manifest_path"])
            manifest = json.loads(source.read_text(encoding="utf-8"))
            image_path = Path(manifest["items"][0]["source"])
            image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            image.putpixel((6, 3), (180, 90, 40, 255))
            image.save(image_path)
            manifest["items"][0]["sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
            raw_manifest = (json.dumps(manifest, separators=(",", ":")) + "\n").encode("utf-8")
            source.write_bytes(raw_manifest)
            config = json.loads(fixture["path"].read_text(encoding="utf-8"))
            config["expected_manifest_sha256"] = hashlib.sha256(raw_manifest).hexdigest()
            fixture["path"].write_text(json.dumps(config), encoding="utf-8")
            with patch.object(publisher, "load_native_def_authority", return_value=inventory):
                result = publisher.build_mod(fixture["path"])
            self.assertEqual(result["native_geometry_contract"], "official-full-canvas-3x-alpha-within-crop")
            self.assertEqual(result["sprite_geometry"][0]["official_crop_rect_3x"], [6, 3, 18, 21])


if __name__ == "__main__":
    unittest.main(verbosity=2)
