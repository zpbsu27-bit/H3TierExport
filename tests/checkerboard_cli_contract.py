"""End-to-end contract for offline PNG/JPEG checkerboard-alpha removal."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

PRODUCT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("H3TIEREXPORT_EXE", PRODUCT / "dist" / "H3TierExport.exe"))
TEST_ROOT = PRODUCT / "build" / "tests"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_checkerboard(path: Path, fmt: str) -> None:
    width, height, tile = 256, 320, 16
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            v = 206 if ((x // tile + y // tile) & 1) else 238
            pixels[x, y] = (v, v, v)
    draw = ImageDraw.Draw(image)
    draw.ellipse((82, 87, 156, 253), fill=(101, 111, 43))
    draw.ellipse((88, 43, 145, 112), fill=(164, 151, 78))
    draw.rectangle((139, 177, 174, 187), fill=(139, 28, 24))
    if fmt == "JPEG":
        image.save(path, format="JPEG", quality=98, subsampling=0)
    else:
        image.save(path, format="PNG")


def make_solid_gray(path: Path) -> None:
    Image.new("RGB", (256, 320), (225, 225, 225)).save(path, format="PNG")


class CheckerboardCliContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not EXE.is_file():
            raise RuntimeError(f"executable missing: {EXE}")
        TEST_ROOT.mkdir(parents=True, exist_ok=True)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="checkerboard-cli-", dir=TEST_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(EXE), "checkerboard-alpha", *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_png_input_becomes_rgba_without_mutating_source(self) -> None:
        source = self.inputs / "troll.png"
        make_checkerboard(source, "PNG")
        source_sha = sha(source)
        outdir = self.root / "out-png"
        result = self.invoke("--input", str(source), "--outdir", str(outdir))
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 0)
        row = payload["items"][0]
        self.assertEqual(row["status"], "OUTPUT_WRITTEN_REVIEW_REQUIRED")
        self.assertEqual(row["input_sha256"], source_sha)
        self.assertEqual(sha(source), source_sha)
        self.assertTrue((outdir / "checkerboard-removal-receipt.json").is_file())
        output = Path(row["output_path"])
        with Image.open(output) as image:
            self.assertEqual(image.mode, "RGBA")
            rgba = image.load()
            self.assertEqual(rgba[0, 0][3], 0)
            self.assertEqual(rgba[255, 319][3], 0)
            core = rgba[118, 170]
            self.assertEqual(core[3], 255)
            self.assertGreater(core[1], core[0])
        self.assertEqual(row["output_sha256"], sha(output))

    def test_jpeg_gemini_download_is_supported_and_source_is_immutable(self) -> None:
        source = self.inputs / "Gemini_Generated_Image.jpg"
        make_checkerboard(source, "JPEG")
        source_sha = sha(source)
        outdir = self.root / "out-jpeg"
        result = self.invoke("--input", str(source), "--outdir", str(outdir))
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        row = payload["items"][0]
        self.assertEqual(row["status"], "OUTPUT_WRITTEN_REVIEW_REQUIRED")
        self.assertEqual(row["input_sha256"], source_sha)
        self.assertEqual(sha(source), source_sha)
        self.assertTrue(row["output_path"].endswith("_transparent.png"))
        with Image.open(row["output_path"]) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.size, (256, 320))
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((118, 170))[3], 255)

    def test_unconfirmed_solid_gray_background_is_rejected_without_output(self) -> None:
        source = self.inputs / "not-a-checkerboard.png"
        make_solid_gray(source)
        source_sha = sha(source)
        outdir = self.root / "must-not-exist"
        result = self.invoke("--input", str(source), "--outdir", str(outdir))
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["succeeded"], 0)
        self.assertEqual(payload["failed"], 1)
        self.assertFalse(outdir.exists())
        self.assertEqual(sha(source), source_sha)

    def test_existing_output_directory_is_never_overwritten(self) -> None:
        source = self.inputs / "troll.png"
        make_checkerboard(source, "PNG")
        outdir = self.root / "existing-output"
        outdir.mkdir()
        sentinel = outdir / "do-not-overwrite.txt"
        sentinel.write_text("owned by another task\n", encoding="utf-8")
        result = self.invoke("--input", str(source), "--outdir", str(outdir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHECKERBOARD_OUTPUT_DIRECTORY_ALREADY_EXISTS", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "owned by another task\n")
        self.assertEqual([p.name for p in outdir.iterdir()], [sentinel.name])

    def test_existing_report_is_rejected_before_any_output(self) -> None:
        source = self.inputs / "troll.png"
        make_checkerboard(source, "PNG")
        outdir = self.root / "must-not-be-created"
        report = self.root / "existing-report.json"
        report.write_text("owned by another task\n", encoding="utf-8")
        result = self.invoke("--input", str(source), "--outdir", str(outdir), "--report", str(report))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHECKERBOARD_REPORT_ALREADY_EXISTS", result.stderr)
        self.assertFalse(outdir.exists())
        self.assertEqual(report.read_text(encoding="utf-8"), "owned by another task\n")

    def test_report_inside_output_directory_is_rejected_before_any_output(self) -> None:
        source = self.inputs / "troll.png"
        make_checkerboard(source, "PNG")
        outdir = self.root / "must-not-be-created"
        report = outdir / "extra-report.json"
        result = self.invoke("--input", str(source), "--outdir", str(outdir), "--report", str(report))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHECKERBOARD_REPORT_MUST_BE_OUTSIDE_OUTPUT_DIRECTORY", result.stderr)
        self.assertFalse(outdir.exists())

    def test_external_report_is_written_after_success(self) -> None:
        source = self.inputs / "troll.png"
        make_checkerboard(source, "PNG")
        outdir = self.root / "output-with-report"
        report = self.root / "separate-report.json"
        result = self.invoke("--input", str(source), "--outdir", str(outdir), "--report", str(report))
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue(report.is_file())
        self.assertTrue((outdir / "checkerboard-removal-receipt.json").is_file())
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), json.loads(result.stdout))

    def test_mixed_batch_records_success_and_failure_per_input(self) -> None:
        good = self.inputs / "good.png"
        bad = self.inputs / "bad.png"
        make_checkerboard(good, "PNG")
        make_solid_gray(bad)
        good_sha, bad_sha = sha(good), sha(bad)
        outdir = self.root / "mixed-output"
        result = self.invoke("--input", str(good), "--input", str(bad), "--outdir", str(outdir))
        self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual({row["status"] for row in payload["items"]},
                         {"OUTPUT_WRITTEN_REVIEW_REQUIRED", "FAILED"})
        self.assertTrue((outdir / "checkerboard-removal-receipt.json").is_file())
        self.assertEqual(sha(good), good_sha)
        self.assertEqual(sha(bad), bad_sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
