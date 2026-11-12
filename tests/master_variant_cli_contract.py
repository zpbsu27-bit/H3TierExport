"""Exercise native-size skill derivation through the built H3TierExport CLI."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from PIL import Image
from pathlib import Path


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def rgba_png(width: int, height: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 31) & 255, (y * 37) & 255, ((x + y) * 19) & 255, 255))
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", zlib.compress(b"".join(rows))) + png_chunk(b"IEND", b"")


def png_dimensions(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    return struct.unpack(">II", raw[16:24])


class SkillMasterVariantCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(sys.argv) < 2:
            raise RuntimeError("pass the candidate H3TierExport.exe path")
        cls.exe = Path(sys.argv[1]).resolve()

    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        result = subprocess.run([str(self.exe), *args], capture_output=True, text=True, encoding="utf-8")
        payload = json.loads(result.stdout or result.stderr)
        return result, payload

    def test_native_size_export_replay_and_resume(self):
        with tempfile.TemporaryDirectory(prefix="h3-skill-variant-") as temp:
            root = Path(temp)
            source = root / "mother.png"
            source.write_bytes(rgba_png(64, 96))
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            inventory_path = root / "inventory.bin"
            inventory_path.write_bytes(b"official native inventory fixture")
            mapping_path = root / "mother-mapping.json"
            mapping_path.write_bytes(b"current skill mother mapping fixture")
            manifest = {
                "schema": "h3.skill-master-variant-manifest.v2",
                "algorithm": "h3-quality-high-resolution-master-variant-v2",
                "registration": "proportional-contain",
                "scale_semantics": "source-resolution-master-no-tier-export",
                "official_inventory": {"path": str(inventory_path), "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest()},
                "master_mapping": {"path": str(mapping_path), "sha256": hashlib.sha256(mapping_path.read_bytes()).hexdigest()},
                "items": [{
                    "id": "SKILL_SECSK32_0_3",
                    "semantic_id": "secondary_skill/12/a",
                    "variant": "icon32",
                    "key": ["def", "SECSK32", 0, 3],
                    "resource": "skl3212a.pcx",
                    "output_filename": "skl3212a.png",
                    "source_png": str(source),
                    "source_sha256": source_sha,
                    "source_pixels": [64, 96],
                    "native_pixels": [32, 32],
                    "target_pixels": [96, 96],
                }],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
            manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            output = root / "native-output"

            checked, check_report = self.run_cli("skill-variant-check", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(checked.returncode, 0, check_report)
            self.assertEqual(check_report["status"], "READY")
            self.assertEqual(check_report["outputs_written"], 0)
            self.assertFalse(output.exists())

            exported, export_report = self.run_cli("skill-variant-export", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(exported.returncode, 0, export_report)
            self.assertEqual(export_report["status"], "EXPORTED_VERIFIED")
            png = output / "skl3212a.png"
            self.assertTrue(png.is_file())
            self.assertEqual(png_dimensions(png), (96, 96))
            with Image.open(png) as derived, Image.open(source) as original:
                self.assertEqual(derived.getbbox(), (16, 0, 80, 96))
                self.assertEqual(derived.crop((16, 0, 80, 96)).tobytes(), original.tobytes())
            self.assertEqual(export_report["target_scale"], None)

            verified, verify_report = self.run_cli("skill-variant-verify", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(verified.returncode, 0, verify_report)
            self.assertEqual(verify_report["status"], "VERIFIED")
            self.assertTrue(verify_report["independent_cpu_replay"])

            resumed, resume_report = self.run_cli("skill-variant-export", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(resumed.returncode, 0, resume_report)
            self.assertEqual(resume_report["status"], "REUSED_VERIFIED")

            drifted, drift_report = self.run_cli("skill-variant-check", "--manifest", str(manifest_path), "--manifest-sha256", "0" * 64, "--outdir", str(output))
            self.assertNotEqual(drifted.returncode, 0)
            self.assertEqual(drift_report["status"], "FAILED")

    def test_duplicate_official_resource_names_use_def_slot_suffixes(self):
        with tempfile.TemporaryDirectory(prefix="h3-skill-duplicate-name-") as temp:
            root = Path(temp)
            source = root / "mother.png"
            source.write_bytes(rgba_png(64, 96))
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            inventory_path = root / "inventory.bin"
            inventory_path.write_bytes(b"official native inventory fixture")
            mapping_path = root / "mother-mapping.json"
            mapping_path.write_bytes(b"current skill mother mapping fixture")
            items = []
            for frame, suffix, semantic in ((79, "_0", "secondary_skill/27/b"), (82, "_1", "secondary_skill/28/b")):
                items.append({
                    "id": f"SKILL_SSKILBON_0_{frame}", "semantic_id": semantic,
                    "variant": "bonus58x64", "key": ["def", "SSKILBON", 0, frame],
                    "resource": "SSB026b.pcx", "output_filename": f"SSB026b{suffix}.png",
                    "source_png": str(source), "source_sha256": source_sha,
                    "source_pixels": [64, 96], "native_pixels": [58, 64],
                    "target_pixels": [87, 96],
                })
            manifest = {
                "schema": "h3.skill-master-variant-manifest.v2",
                "algorithm": "h3-quality-high-resolution-master-variant-v2",
                "registration": "proportional-contain",
                "scale_semantics": "source-resolution-master-no-tier-export",
                "official_inventory": {"path": str(inventory_path), "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest()},
                "master_mapping": {"path": str(mapping_path), "sha256": hashlib.sha256(mapping_path.read_bytes()).hexdigest()},
                "items": items,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
            manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            output = root / "native-output"
            exported, report = self.run_cli("skill-variant-export", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(exported.returncode, 0, report)
            self.assertEqual(report["outputs_written"], 2)
            self.assertTrue((output / "SSB026b_0.png").is_file())
            self.assertTrue((output / "SSB026b_1.png").is_file())
            verified, verify_report = self.run_cli("skill-variant-verify", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha, "--outdir", str(output))
            self.assertEqual(verified.returncode, 0, verify_report)
            self.assertEqual(verify_report["verified"], 2)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
