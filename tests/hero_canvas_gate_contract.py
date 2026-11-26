"""Mandatory mounted-hero (AH*) canvas x3 + origin x3 gate contracts.

Covers the standalone companion gate (read-only verify, fail-closed collapse /
wrong-size / alpha-outside-crop, lossless run normalization, non-AH* ignored) and
the mandatory C++ wiring: expand / expand-verify must run the gate for every AH*
batch and fail closed when the export is not full canvas x3 with alpha inside the
official crop. Fixtures use the built-in 1999 DEF geometry for AH00_ slot 0_0
(canvas 96x64, crop 32x58, origin 32x6 -> canvas x3 288x192, crop x3 96x174,
origin x3 (96,18), official crop rect x3 [96,18,192,192]).
"""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
EXE = PRODUCT / "build/candidate/H3TierExport.exe"

# AH00_ group 0 frame 0, from the built-in flag-pole-reference-1999 table.
CANVAS = (96, 64)
CROP = (32, 58)
ORIGIN = (32, 6)
FULL = (CANVAS[0] * 3, CANVAS[1] * 3)          # 288 x 192
COLLAPSED = (CROP[0] * 3, CROP[1] * 3)         # 96 x 174
CROP_RECT = (ORIGIN[0] * 3, ORIGIN[1] * 3,
             (ORIGIN[0] + CROP[0]) * 3, (ORIGIN[1] + CROP[1]) * 3)  # 96,18,192,192


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p: Path, value) -> None:
    p.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def opaque_rect(size, box, color=(120, 70, 40, 255)):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            im.putpixel((x, y), color)
    return im


class HeroCanvasGateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not EXE.is_file():
            raise RuntimeError(f"candidate executable missing: {EXE}")

    def setUp(self):
        parent = PRODUCT / "build/tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="hero-gate-", dir=parent)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "out"
        self.out.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_exe(self, *args):
        return subprocess.run([str(EXE), *map(str, args)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)

    def parse(self, p):
        text = p.stdout if p.stdout.strip() else p.stderr
        return json.loads(text)

    def write_batch(self, items, name="batch.json"):
        path = self.root / name
        save(path, {"schema": "h3.master-expand.batch.v1", "output_root": str(self.out),
                    "items": items})
        return path

    def item(self, identity, output, group=0, frame=0):
        source = self.root / identity / f"{group}_{frame}.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            opaque_rect(CANVAS, (0, 0, CANVAS[0], CANVAS[1])).save(source)
        return {"id": f"{identity}_{group}_{frame}", "source": str(source), "output": str(output)}

    def gate(self, batch, mode="verify", outdir=None, ok=True, expect_exit=None):
        args = ["hero-canvas-gate", "--batch", batch, "--batch-sha256", sha(batch), "--mode", mode]
        if outdir is not None:
            args += ["--outdir", outdir]
        p = self.run_exe(*args)
        if expect_exit is not None:
            self.assertEqual(p.returncode, expect_exit, (p.stdout + p.stderr)[-2000:])
        elif ok:
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        else:
            self.assertNotEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        return self.parse(p)

    # --- standalone gate -------------------------------------------------
    def test_verify_pass_on_conforming_full_canvas(self):
        output = self.out / "0_0.png"
        opaque_rect(FULL, (100, 30, 181, 181)).save(output)
        batch = self.write_batch([self.item("AH00_", output)])
        receipt = self.gate(batch, "verify")
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["counts"]["pass"], 1)
        self.assertEqual(receipt["hero_items"], 1)
        self.assertTrue(receipt["master_untouched"])
        self.assertFalse(receipt["game_deployed"])

    def test_verify_blocks_crop_x3_collapse(self):
        output = self.out / "0_0.png"
        opaque_rect(COLLAPSED, (10, 20, 86, 154)).save(output)
        batch = self.write_batch([self.item("AH00_", output)])
        receipt = self.gate(batch, "verify", ok=False, expect_exit=2)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["counts"]["collapsed"], 1)
        self.assertIn("HERO_CANVAS_X3_REQUIRED", receipt["items"][0]["error"])

    def test_run_normalizes_collapse_losslessly(self):
        output = self.out / "0_0.png"
        collapsed = opaque_rect(COLLAPSED, (10, 20, 86, 154))
        collapsed.save(output)
        before = sha(output)
        batch = self.write_batch([self.item("AH00_", output)])
        outdir = self.root / "normalized"
        receipt = self.gate(batch, "run", outdir=outdir)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["counts"]["normalized"], 1)
        row = receipt["items"][0]
        self.assertTrue(row["normalized"])
        normalized = Path(row["normalized_path"])
        self.assertEqual(normalized.parent, outdir)
        with Image.open(normalized) as im:
            self.assertEqual(im.size, FULL)
            self.assertEqual(im.mode, "RGBA")
            # The crop region of the normalized canvas must equal the collapsed
            # source verbatim (no resampling, no premultiplied edge darkening).
            region = im.crop(CROP_RECT)
        self.assertEqual(region.tobytes(), collapsed.tobytes())
        self.assertEqual(sha(output), before, "run mode must not edit the source output")
        self.assertFalse(output.parent.joinpath("normalized").exists())

    def test_run_clips_only_tiny_low_alpha_crop_fringe_losslessly(self):
        output = self.out / "0_0.png"
        source = opaque_rect(FULL, (108, 30, 180, 180))
        source.putpixel((156, 17), (140, 110, 40, 3))
        source.putpixel((157, 17), (140, 110, 40, 8))
        source.save(output)
        before = sha(output)
        crop_bytes = source.crop(CROP_RECT).tobytes()
        batch = self.write_batch([self.item("AH00_", output)])
        outdir = self.root / "clip-normalized"
        receipt = self.gate(batch, "run", outdir=outdir)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["counts"]["normalized"], 1)
        row = receipt["items"][0]
        self.assertTrue(row["normalized"])
        self.assertEqual(row["normalization"], "ZERO_TINY_ALPHA_OUTSIDE_OFFICIAL_CROP")
        self.assertEqual(row["normalized_alpha_pixels_removed"], 2)
        self.assertEqual(row["maximum_alpha_removed"], 8)
        normalized = Path(row["normalized_path"])
        with Image.open(normalized) as im:
            self.assertEqual(im.size, FULL)
            self.assertEqual(im.mode, "RGBA")
            self.assertEqual(im.crop(CROP_RECT).tobytes(), crop_bytes)
            self.assertEqual(im.getpixel((156, 17))[3], 0)
            self.assertEqual(im.getpixel((157, 17))[3], 0)
        self.assertEqual(sha(output), before, "clip normalization must not edit source output")

    def test_run_refuses_visible_alpha_outside_crop(self):
        output = self.out / "0_0.png"
        source = opaque_rect(FULL, (108, 30, 180, 180))
        source.putpixel((156, 17), (140, 110, 40, 9))
        source.save(output)
        batch = self.write_batch([self.item("AH00_", output)])
        outdir = self.root / "unsafe-clip"
        receipt = self.gate(batch, "run", outdir=outdir, ok=False, expect_exit=2)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("HERO_ALPHA_OUTSIDE_CROP_NOT_SAFE_TO_CLIP", receipt["items"][0]["error"])
        self.assertFalse((outdir / "0_0.png").exists())

    def test_verify_blocks_alpha_outside_official_crop(self):
        output = self.out / "0_0.png"
        opaque_rect(FULL, (0, 0, FULL[0], FULL[1])).save(output)  # fills whole canvas
        batch = self.write_batch([self.item("AH00_", output)])
        receipt = self.gate(batch, "verify", ok=False, expect_exit=2)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("MAP_SPRITE_ALPHA_OUTSIDE_OFFICIAL_CROP", receipt["items"][0]["error"])

    def test_verify_blocks_wrong_size(self):
        output = self.out / "0_0.png"
        opaque_rect((100, 100), (10, 10, 90, 90)).save(output)
        batch = self.write_batch([self.item("AH00_", output)])
        receipt = self.gate(batch, "verify", ok=False, expect_exit=2)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("HERO_OUTPUT_WRONG_SIZE", receipt["items"][0]["error"])

    def test_non_hero_items_are_ignored(self):
        output = self.out / "0_0.png"
        opaque_rect(FULL, (0, 0, FULL[0], FULL[1])).save(output)  # would block if gated
        batch = self.write_batch([self.item("SECSK32", output)])
        receipt = self.gate(batch, "verify")
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["hero_items"], 0)

    def test_batch_sha_drift_is_refused(self):
        output = self.out / "0_0.png"
        opaque_rect(FULL, (100, 30, 181, 181)).save(output)
        batch = self.write_batch([self.item("AH00_", output)])
        p = self.run_exe("hero-canvas-gate", "--batch", batch,
                         "--batch-sha256", "0" * 64, "--mode", "verify")
        self.assertEqual(p.returncode, 2)
        self.assertIn("HERO_BATCH_SHA256_DRIFT", p.stderr)

    # --- mandatory C++ wiring (expand / expand-verify) -------------------
    def make_master(self, identity, box):
        folder = self.root / identity
        folder.mkdir(parents=True, exist_ok=True)
        master = folder / "0_0.png"
        # The exporter caps enlargement at 2x per edge. Supply a 2X master so
        # the integration fixture reaches the post-export AH* gate instead of
        # failing the resampling preflight first.
        source_scale = 2
        source_canvas = (CANVAS[0] * source_scale, CANVAS[1] * source_scale)
        source_box = tuple(value * source_scale for value in box)
        opaque_rect(source_canvas, source_box).save(master)
        return master

    def plan(self, master, name):
        out = self.root / (name + "-out")
        batch = self.root / (name + "-batch.json")
        p = self.run_exe("expand-plan", "--source", master, "--source-tier", 2,
                         "--target-scale", 3,
                         "--output-root", out, "--out", batch)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        return batch

    def test_expand_verify_fails_closed_on_canvas_fill_hero(self):
        # A fully opaque master fills the whole canvas x3; alpha escapes the
        # official crop, so the mandatory gate must block the export.
        master = self.make_master("AH00_", (0, 0, CANVAS[0], CANVAS[1]))
        batch = self.plan(master, "hero-block")
        exported = self.run_exe("expand", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        export_report = self.parse(exported)
        self.assertEqual(export_report.get("hero_canvas_gate"), "BLOCKED")
        self.assertNotEqual(exported.returncode, 0)
        p = self.run_exe("expand-verify", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        report = self.parse(p)
        self.assertEqual(report.get("hero_canvas_gate"), "BLOCKED")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(report.get("status"), "COMPLETED_WITH_FAILURES")

    def test_expand_verify_passes_on_conforming_hero(self):
        # Artwork inset well inside the official crop with transparent margins;
        # canvas x3 keeps it inside the crop rect, so the gate passes.
        master = self.make_master("AH00_", (40, 20, 56, 50))
        batch = self.plan(master, "hero-pass")
        exported = self.run_exe("expand", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        export_report = self.parse(exported)
        self.assertEqual(exported.returncode, 0, (exported.stdout + exported.stderr)[-2000:])
        self.assertEqual(export_report.get("hero_canvas_gate"), "PASS")
        p = self.run_exe("expand-verify", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        report = self.parse(p)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertEqual(report.get("hero_canvas_gate"), "PASS")

    def test_expand_verify_does_not_gate_non_hero(self):
        master = self.make_master("SECSK32", (0, 0, CANVAS[0], CANVAS[1]))
        batch = self.plan(master, "nonhero")
        exported = self.run_exe("expand", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        export_report = self.parse(exported)
        self.assertEqual(exported.returncode, 0, (exported.stdout + exported.stderr)[-2000:])
        self.assertNotIn("hero_canvas_gate", export_report)
        p = self.run_exe("expand-verify", "--batch", batch, "--workers", 2, "--memory-gib", 1)
        report = self.parse(p)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertNotIn("hero_canvas_gate", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
