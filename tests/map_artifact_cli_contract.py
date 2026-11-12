"""Map artifact geometry, source identity and transparent color regression."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


def pin(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class MapArtifactContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = Path(sys.argv[1]).resolve()

    def fixture(self, root, *, scale=5):
        source = root / "original-ai-mother.png"
        picture = Image.new("RGBA", (16, 32), (0, 0, 0, 0))
        for y in range(2, 30):
            for x in range(2, 14):
                picture.putpixel((x, y), (220, 92 if x < 8 else 180, 28, 255))
        picture.save(source)
        reference = root / "official-reference.png"
        native = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
        for y in range(6, 15):
            for x in range(5, 21):
                if x >= y - 3:
                    native.putpixel((x, y), (0, 0, 0, 128))
        for y in range(10, 17):
            for x in range(22, 36):
                native.putpixel((x, y), (30, 90, 210, 255))
        native.save(reference)
        payload = root / "AVA0007.DEF"
        offset = 784 + 16 + 13 + 4
        header = struct.pack("<4I", 67, 40, 20, 1) + bytes(768)
        group = struct.pack("<4I", 0, 1, 0, 0) + b"AVA0007.pcx\0\0" + struct.pack("<I", offset)
        frame = struct.pack("<8I", 33, 0, 40, 20, 40, 20, 0, 0) + b"\0"
        payload.write_bytes(header + group + frame)
        objects = root / "OBJECTS.TXT"
        objects.write_text("AVA0007.def 0 0 0 0 5 7 4 0\n", encoding="ascii")
        inventory = root / "inventory.json"
        inventory.write_text('{"fixture":"native inventory"}', encoding="ascii")
        manifest = {
            "schema": "h3.map-artifact-master-manifest.v1",
            "algorithm": "official-shadow-uniform-ai-master-v2",
            "official_inventory": pin(inventory), "object_tables": [pin(objects)],
            "items": [{"id": "AVA0007", "key": ["def", "AVA0007", 0, 0],
                       "resource": "AVA0007.pcx", "output_filename": "AVA0007.png",
                       "object_type": 5, "object_subtype": 7, "source_artifact_frame": 7,
                       "source": pin(source), "reference": pin(reference), "native_def": pin(payload),
                       "native_canvas": [40, 20], "native_crop": [0, 0, 40, 20],
                       "working_scale": scale}],
        }
        return manifest, source

    def run_cli(self, root, manifest, command, out):
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = subprocess.run([str(self.exe), command, "--manifest", str(path),
                                 "--manifest-sha256", digest, "--outdir", str(out)],
                                capture_output=True, text=True, encoding="utf-8")
        return result, json.loads(result.stdout or result.stderr)

    def test_preserves_tall_source_in_wide_official_body_and_exact_shadow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, source = self.fixture(root)
            reference = Image.open(manifest["items"][0]["reference"]["path"]).convert("RGBA")
            shadow = Image.new("RGBA", reference.size)
            for y in range(reference.height):
                for x in range(reference.width):
                    alpha = reference.getpixel((x, y))[3]
                    if 0 < alpha < 255:
                        shadow.putpixel((x, y), (0, 0, 0, alpha))
            reference.close()
            shadow_path = root / "official-separate-shadow.png"
            # Atlas edges can contain neutral white RGB in a shadow mask.
            # Only its pinned coverage should reach the black ground shadow.
            shadow.putpixel((7, 8), (255, 255, 255, 128))
            shadow.resize((80, 40), Image.Resampling.NEAREST).save(shadow_path)
            manifest["items"][0]["shadow_reference"] = pin(shadow_path)
            manifest["items"][0]["shadow_reference_scale"] = 2
            out = root / "output"
            checked, report = self.run_cli(root, manifest, "map-artifact-check", out)
            self.assertEqual(checked.returncode, 0, report)
            self.assertFalse(out.exists())
            exported, report = self.run_cli(root, manifest, "map-artifact-export", out)
            self.assertEqual(exported.returncode, 0, report)
            receipt = json.loads((out / "map-artifact-receipt.json").read_text())
            render = receipt["outputs"][0]["render"]
            box = render["drawn_body_bbox"]
            self.assertEqual([box[2] - box[0], box[3] - box[1]], [12, 28])
            self.assertEqual(render["source_scale_x"], render["source_scale_y"])
            self.assertFalse(render["source_resampled_once"])
            self.assertFalse(render["shadow_derived_from_ai_glow"])
            self.assertEqual(render["shadow_method"], "high-resolution-subject-alpha-official-centroid-affine-gaussian")
            self.assertGreater(render["official_shadow_angle_degrees"], 90)
            with Image.open(out / "AVA0007.png") as image, Image.open(source) as original:
                self.assertEqual(image.size, (200, 100))
                self.assertEqual(image.crop(box).tobytes(), original.crop((2, 2, 14, 30)).tobytes())
                self.assertEqual(image.getpixel((35, 40))[:3], (0, 0, 0))
                self.assertEqual(render["shadow_projection"]["source"], "high-resolution-subject-alpha")
                self.assertFalse(render["shadow_projection"]["native_shadow_pixels_enlarged"])
                self.assertEqual(image.getpixel((1, 1))[3], 0)
            verified, report = self.run_cli(root, manifest, "map-artifact-verify", out)
            self.assertEqual(verified.returncode, 0, report)
            self.assertTrue(report["independent_cpu_replay"])
            self.assertEqual(report["outputs_written"], 0)

    def test_reduction_uses_existing_quality_kernel_without_stretch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = self.fixture(root, scale=1)
            out = root / "output"
            result, report = self.run_cli(root, manifest, "map-artifact-export", out)
            self.assertEqual(result.returncode, 0, report)
            render = json.loads((out / "map-artifact-receipt.json").read_text())["outputs"][0]["render"]
            self.assertTrue(render["source_resampled_once"])
            self.assertIn("linear-premultiplied-lanczos3", render["filter_algorithm"])
            self.assertLessEqual(render["aspect_error"], 1 / 12)
            with Image.open(out / "AVA0007.png") as image:
                box = render["drawn_body_bbox"]
                raw = image.crop(box).tobytes()
                pixels = [tuple(raw[index:index + 4]) for index in range(0, len(raw), 4)]
                self.assertTrue(any(r > 180 and r > b * 3 and a > 200 for r, g, b, a in pixels))

    def test_geometry_identity_and_source_drift_are_blocked_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, source = self.fixture(root)
            out = root / "output"
            manifest["items"][0]["source_artifact_frame"] = 6
            result, _ = self.run_cli(root, manifest, "map-artifact-check", out)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

            manifest["items"][0]["source_artifact_frame"] = 7
            manifest["items"][0]["native_crop"] = [0, 0, 39, 20]
            result, _ = self.run_cli(root, manifest, "map-artifact-check", out)
            self.assertNotEqual(result.returncode, 0)
            manifest["items"][0]["native_crop"] = [0, 0, 40, 20]
            source.write_bytes(source.read_bytes() + b"drift")
            result, _ = self.run_cli(root, manifest, "map-artifact-check", out)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

    def test_lod_only_ai_source_does_not_forge_an_object_table_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = self.fixture(root)
            objects = root / "OBJECTS.TXT"
            objects.write_text("AVA0008.def 0 0 0 0 5 8 4 0\n", encoding="ascii")
            manifest["object_tables"] = [pin(objects)]
            item = manifest["items"][0]
            item.update(object_type=None, object_subtype=None, source_artifact_frame=None,
                        source_role="map-only-ai-mother")
            result, report = self.run_cli(root, manifest, "map-artifact-check", root / "output")
            self.assertEqual(result.returncode, 0, report)
            objects.write_text("AVA0007.def 0 0 0 0 5 7 4 0\n", encoding="ascii")
            manifest["object_tables"] = [pin(objects)]
            result, _ = self.run_cli(root, manifest, "map-artifact-check", root / "output")
            self.assertNotEqual(result.returncode, 0)

    def test_spell_scroll_special_object_type_uses_inventory_frame_one(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = self.fixture(root)
            objects = root / "OBJECTS.TXT"
            objects.write_text("AVA0007.def 0 0 0 0 93 0 4 0\n", encoding="ascii")
            manifest["object_tables"] = [pin(objects)]
            manifest["items"][0].update(object_type=93, object_subtype=0, source_artifact_frame=1)
            result, report = self.run_cli(root, manifest, "map-artifact-check", root / "output")
            self.assertEqual(result.returncode, 0, report)
            manifest["items"][0]["source_artifact_frame"] = 0
            result, _ = self.run_cli(root, manifest, "map-artifact-check", root / "output")
            self.assertNotEqual(result.returncode, 0)

    def test_official_placeholder_keeps_label_pixels_and_cannot_bypass_ai_alpha(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = self.fixture(root)
            item = manifest["items"][0]
            # Opaque yellow belongs to the label itself; it is not an AI background.
            native = Image.new("RGBA", (40, 20), (255, 255, 66, 255))
            native.putpixel((4, 5), (255, 36, 0, 255))
            reference = root / "official-reference.png"
            native.save(reference)
            objects = root / "OBJECTS.TXT"
            objects.write_text("AVA0008.def 0 0 0 0 5 8 4 0\n", encoding="ascii")
            manifest["object_tables"] = [pin(objects)]
            item.update(id="AVA0003", key=["def", "AVA0003", 0, 0],
                        resource="AVA0003.pcx", output_filename="AVA0003.png",
                        object_type=None, object_subtype=None, source_artifact_frame=None,
                        source_role="official-placeholder", source=pin(reference),
                        reference=pin(reference), working_scale=8)
            out = root / "output"
            result, report = self.run_cli(root, manifest, "map-artifact-export", out)
            self.assertEqual(result.returncode, 0, report)
            with Image.open(out / "AVA0003.png") as image:
                self.assertEqual(image.size, (320, 160))
                self.assertEqual(image.getpixel((32, 40)), (255, 36, 0, 255))
                self.assertEqual(image.getpixel((31, 40)), (255, 255, 66, 255))
            item["source_role"] = "map-only-ai-mother"
            result, _ = self.run_cli(root, manifest, "map-artifact-check", root / "other")
            self.assertNotEqual(result.returncode, 0)
            item["source_role"] = "official-placeholder"
            item["key"][1] = "AVA0007"
            result, _ = self.run_cli(root, manifest, "map-artifact-check", root / "other")
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
