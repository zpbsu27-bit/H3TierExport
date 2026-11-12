"""Contracts for full-canvas map hero MOD packaging."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PRODUCT / "scripts"))
import map_sprite_mod_packager as packager


def native_slot() -> dict:
    return {
        "kind": "def", "identity": "TEST", "group": 0, "frame": 0,
        "native_lod": "H3sprite.lod", "resource": "test_0.pcx",
        "canvas": [8, 8], "crop": [4, 6], "anchor": [2, 1],
    }


def package_request(root: Path, source_size=(24, 24)) -> dict:
    source = root / "expanded.png"
    image = Image.new("RGBA", source_size, (0, 0, 0, 0))
    image.putpixel((6, 3), (220, 120, 50, 255))
    image.save(source)
    return {
        "schema": packager.SCHEMA,
        "operation_id": "test-map-sprite-r01",
        "identity": "TEST",
        "mod_id": "test_map_sprite",
        "candidate_root": str(root / "candidate"),
        "native_slots": 1,
        "expand_batch": {"path": "batch.json", "sha256": "b" * 64, "bytes": 1},
        "expand_verify_report": {"path": "verify.json", "sha256": "c" * 64, "bytes": 1},
        "native_1x_authority": {"path": "authority.json", "sha256": "d" * 64, "bytes": 1},
        "native_inventory": {"path": "inventory.json.gz", "sha256": "e" * 64, "bytes": 1},
        "identity_checkpoint": {"path": "checkpoint.json", "sha256": "f" * 64, "bytes": 1},
        "slots": [{
            "group": 0, "frame": 0, "resource": "test_0.pcx",
            "vcmi_filename": "test_0_g0_f0.png",
            "native_geometry": {
                "canvas_1x": [8, 8], "crop_1x": [4, 6], "origin_1x": [2, 1],
                "canvas_3x": [24, 24], "crop_rect_3x": [6, 3, 18, 21],
            },
            "expanded": {
                "path": str(source), "bytes": source.stat().st_size,
                "sha256": packager.sha256(source), "pixels": [24, 24], "alpha_bbox": [6, 3, 7, 4],
            },
        }],
    }


class MapSpriteModPackagerContract(unittest.TestCase):
    def test_full_canvas_and_exact_crop_origin_are_bound(self) -> None:
        native = native_slot()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "0_0.png"
            image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            image.putpixel((6, 3), (255, 255, 255, 255))
            image.save(source)
            result = packager.validate_full_canvas_sprite(source, native, "TEST", 0, 0)
        self.assertEqual(result["expected_full_canvas_3x"], [24, 24])
        self.assertEqual(result["official_crop_rect_3x"], [6, 3, 18, 21])

    def test_crop_only_and_wrongly_centered_exports_fail_closed(self) -> None:
        native = native_slot()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crop_only = root / "crop.png"
            Image.new("RGBA", (12, 18), (255, 255, 255, 255)).save(crop_only)
            with self.assertRaisesRegex(ValueError, "MAP_SPRITE_FULL_CANVAS_3X_REQUIRED"):
                packager.validate_full_canvas_sprite(crop_only, native, "TEST", 0, 0)

            centered = root / "centered.png"
            image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            image.putpixel((1, 4), (255, 255, 255, 255))
            image.save(centered)
            with self.assertRaisesRegex(ValueError, "MAP_SPRITE_ALPHA_OUTSIDE_OFFICIAL_CROP"):
                packager.validate_full_canvas_sprite(centered, native, "TEST", 0, 0)

    def test_failed_preflight_writes_no_candidate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = package_request(root, source_size=(12, 18))
            crop = Path(request["slots"][0]["expanded"]["path"])

            def refuse(_request):
                packager.validate_full_canvas_sprite(crop, native_slot(), "TEST", 0, 0)

            with patch.object(packager, "verify_request_current", side_effect=refuse):
                with self.assertRaisesRegex(ValueError, "MAP_SPRITE_FULL_CANVAS_3X_REQUIRED"):
                    packager.build_candidate(request, {"path": "request.json", "sha256": "a" * 64, "bytes": 1})
            self.assertFalse(Path(request["candidate_root"]).exists())

    def test_package_build_copies_bytes_and_writes_matching_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = package_request(root)
            source = Path(request["slots"][0]["expanded"]["path"])
            source_bytes = source.read_bytes()
            with patch.object(packager, "verify_request_current"):
                receipt = packager.build_candidate(
                    request, {"path": str(root / "request.json"), "sha256": "a" * 64, "bytes": 1})
            target = Path(receipt["mod_root"]) / "content" / "Sprites3x" / "TEST" / "test_0_g0_f0.png"
            descriptor = json_load(Path(receipt["mod_root"]) / "content" / "Sprites3x" / "TEST.json")
            self.assertEqual(target.read_bytes(), source_bytes)
            self.assertEqual(descriptor, {
                "basepath": "TEST/",
                "images": [{"group": 0, "frame": 0, "file": "test_0_g0_f0.png"}],
            })
            self.assertEqual(receipt["status"], "MAP_SPRITE_MOD_CANDIDATE_BUILT_READBACK_VERIFIED")


def json_load(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
