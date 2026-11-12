"""Contract tests for the shipping EXE's read-only native 1X/3X review renderer."""
from __future__ import annotations

import hashlib
import copy
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
OUTPUT_BASE = PRODUCT / "build" / "experiments" / "native-review"
EXE = Path(os.environ.get("H3TIEREXPORT_EXE", PRODUCT / "build" / "candidate" / "H3TierExport.exe"))
QUALITY_PROBE = PRODUCT / "build" / "candidate" / "quality-tests.exe"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_png(path: Path, size: tuple[int, int], seed: int = 0) -> None:
    image = Image.new("RGBA", size)
    pixels = []
    for y in range(size[1]):
        for x in range(size[0]):
            alpha = (1, 64, 128, 255)[(x + y + seed) % 4]
            pixels.append(((x * 37 + seed * 11) % 256,
                           (y * 43 + seed * 17) % 256,
                           ((x + y) * 29 + seed * 7) % 256, alpha))
    image.putdata(pixels)
    image.save(path, format="PNG")


def canonical_binding(inventory_sha: str, key: list, targets: dict) -> str:
    value = {"inventory_sha256": inventory_sha, "key": key, "targets": targets}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class NativeReviewRenderContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not EXE.is_file() or not QUALITY_PROBE.is_file():
            raise RuntimeError("build H3TierExport.exe and quality-tests.exe before this contract")

    def render(self, spec: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.render_with(EXE, spec, output, *extra)

    def render_with(self, executable: Path, spec: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(executable), "review-render", "--spec", str(spec), "--spec-sha256", sha(spec),
                               "--outdir", str(output), *extra], shell=False, check=False,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)

    def isolated_product(self, label: str) -> tuple[Path, Path, dict[str, dict]]:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix=f"native-build-binding-{label}-", dir=OUTPUT_BASE))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".gitignore").write_text("build/\n", encoding="ascii")
        exe = root / "H3TierExport.exe"
        shutil.copyfile(EXE, exe)
        source_bytes: dict[str, bytes] = {}
        for relative in ("src/native_review.hpp", "src/resample.hpp", "src/wic.hpp", "src/main.cpp"):
            payload = (PRODUCT / relative).read_bytes()
            source_bytes[relative] = payload
            target = root / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        receipt_dir = root / "build" / "candidate"
        receipt_dir.mkdir(parents=True)
        rows = {relative: {"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                for relative, payload in source_bytes.items()}
        # This is a synthetic fixture receipt used only to exercise the EXE's
        # path normalization and missing/drift guards; it is never build evidence.
        rows["src/native_review.hpp"]["path"] = "src\\native_review.hpp"
        self.write_isolated_receipt(root, exe, list(rows.values()))
        return root, exe, rows

    @staticmethod
    def write_isolated_receipt(root: Path, exe: Path, sources: list[dict]) -> None:
        receipt = {"schema": "h3.tier-export.build.v2", "passed": True, "source_current": True,
                   "executable": {"sha256": sha(exe)}, "sources": sources}
        path = root / "build" / "candidate" / "build-receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def make_spec(self, root: Path, items: list[dict], targets: dict | None = None) -> Path:
        inventory = root / "official-inventory.fixture.gz"
        inventory.write_bytes(b"opaque inventory fixture bytes; renderer must not parse it")
        inventory_sha = sha(inventory)
        source_manifest = root / "source-manifest.fixture.json"
        source_manifest.write_text('{"schema":"fixture"}\n', encoding="utf-8")
        targets = targets or {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]}
        full = []
        for row in items:
            key = row["key"]
            with Image.open(row["source"]) as source:
                source_pixels = list(source.size)
            full.append({"id": row["id"], "key": key, "source_png": str(row["source"]),
                         "source_sha256": sha(row["source"]), "source_pixels": source_pixels,
                         "source_rect": row.get("source_rect"), "registration": row.get("registration", "strict-aspect"),
                         "targets": targets,
                         "geometry_binding_sha256": canonical_binding(inventory_sha, key, targets),
                         "reference": row.get("reference")})
            full[-1].update({k: row[k] for k in ("source_layout", "render_scales", "atlas_view", "state_view", "alpha_cap_3x", "padding_registration", "output_layout", "foreground_selection") if k in row})
        spec = {"schema": "h3.native-review.render-spec.v1",
                "source_manifest": {"path": str(source_manifest), "sha256": sha(source_manifest)},
                "official_inventory": {"path": str(inventory), "sha256": inventory_sha}, "items": full}
        path = root / "render-spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def probe(self, source: Path, out: Path, width: int, height: int) -> None:
        process = subprocess.run([str(QUALITY_PROBE), str(source), str(out), str(width), str(height), "h3-quality"],
                                 shell=False, check=False, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=180)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def _atlas_spec(self, root: Path, *, cap=False):
        source = root / "atlas-master.png"
        write_png(source, (61, 61), 17)
        spec = self.make_spec(root, [{"id": "STATE_A", "key": ["def", "RENDERTEST", 0, 0],
                                      "source": source, "registration": "authored-atlas-window-3x",
                                      "render_scales": [3]},
                                     {"id": "STATE_B", "key": ["def", "RENDERTEST", 0, 1],
                                      "source": source, "registration": "authored-atlas-window-3x",
                                      "render_scales": [3]}])
        doc = json.loads(spec.read_bytes())
        for index, item in enumerate(doc["items"]):
            rect = [1, 2, 6, 6] if cap or index == 0 else [7, 8, 6, 6]
            projection = [Fraction(rect[i] * 61, 15) for i in range(4)]
            item["atlas_view"] = {"authority": doc["source_manifest"], "sample_pixels_3x": [15, 15],
                                   "frame_rect_3x": rect,
                                   "source_rect_raw_rational": [[v.numerator, v.denominator] for v in projection]}
        if cap:
            mask = root / "functional-mask.png"
            image = Image.new("RGBA", (6, 6))
            image.putdata([(13, 29, 43, (0, 1, 127, 254)[i % 4]) for i in range(36)])
            image.save(mask)
            doc["items"][1]["alpha_cap_3x"] = {"path": str(mask), "sha256": sha(mask),
                                                "operation": "min(model_alpha,official_alpha)"}
        spec.write_text(json.dumps(doc), encoding="utf-8")
        return spec, doc

    def test_authored_views_equal_existing_kernel_atlas_copy_and_share_one_sample(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="authored-atlas-copy-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            spec, doc = self._atlas_spec(root)
            # Independent reference uses the existing quality probe on the whole
            # master; the view must only copy those pixels into its official slot.
            reference = root / "whole-atlas.png"
            self.probe(root / "atlas-master.png", reference, 15, 15)
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(process.stdout)
            self.assertEqual(report["authored_atlas_sampling_calls"], 1)
            self.assertEqual([r["atlas_sample_reused"] for r in report["items"]], [False, True])
            for declared, actual in zip(doc["items"], report["items"], strict=True):
                x, y, w, h = declared["atlas_view"]["frame_rect_3x"]
                with Image.open(reference) as atlas, Image.open(actual["outputs"]["candidate_3x"]["path"]) as image:
                    expected = Image.new("RGBA", (12, 12))
                    expected.paste(atlas.convert("RGBA").crop((x, y, x + w, y + h)), (3, 3))
                    self.assertEqual(image.convert("RGBA").tobytes(), expected.tobytes())
                self.assertIsNone(actual["common_k"])
                self.assertNotIn("candidate_1x", actual["outputs"])

    def _cache_bound_spec(self, root: Path, order: list[str], source_size: tuple[int, int],
                          atlas_size: tuple[int, int]) -> tuple[Path, dict[str, tuple[int, int, int, int]]]:
        colors = {"A": (13, 47, 91, 255), "B": (201, 29, 61, 255)}
        sources: dict[str, Path] = {}
        for label in set(order):
            source = root / f"{label}-master.png"
            Image.new("RGBA", source_size, colors[label]).save(source)
            sources[label] = source
        spec = self.make_spec(root, [{"id": f"STATE_{index}", "key": ["def", "CACHEBOUND", 0, index],
                                     "source": sources[label], "registration": "authored-atlas-window-3x",
                                     "render_scales": [3]}
                                    for index, label in enumerate(order)])
        document = json.loads(spec.read_bytes())
        rect = [1, 2, 6, 6]
        projection = [Fraction(rect[i] * source_size[i % 2], atlas_size[i % 2]) for i in range(4)]
        for item in document["items"]:
            item["atlas_view"] = {"authority": document["source_manifest"],
                                   "sample_pixels_3x": list(atlas_size), "frame_rect_3x": rect,
                                   "source_rect_raw_rational": [[value.numerator, value.denominator]
                                                                  for value in projection]}
        spec.write_text(json.dumps(document), encoding="utf-8")
        return spec, colors

    def _assert_authored_cache_bound(self, order: list[str], source_size: tuple[int, int],
                                     atlas_size: tuple[int, int], sampling_calls: int) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="authored-cache-bound-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            spec, colors = self._cache_bound_spec(root, order, source_size, atlas_size)
            process = self.render(spec, root / "rendered")
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(process.stdout)
            self.assertEqual(report["authored_atlas_sampling_calls"], sampling_calls)
            self.assertEqual(report["authored_atlas_cache_limit_pixels"], 2 * 1024 * 1024)
            self.assertLessEqual(report["authored_atlas_peak_cached_pixels"],
                                 report["authored_atlas_cache_limit_pixels"])
            oversized = atlas_size[0] * atlas_size[1] > report["authored_atlas_cache_limit_pixels"]
            self.assertEqual(report["authored_atlas_peak_cached_pixels"] == 0, oversized)
            self.assertEqual([row["atlas_sample_reused"] for row in report["items"]], [False] * len(order))
            for label, row in zip(order, report["items"], strict=True):
                expected = Image.new("RGBA", (12, 12))
                expected.paste(Image.new("RGBA", (6, 6), colors[label]), (3, 3))
                with Image.open(row["outputs"]["candidate_3x"]["path"]) as actual:
                    self.assertEqual(actual.convert("RGBA").tobytes(), expected.tobytes())
                self.assertNotIn("candidate_1x", row["outputs"])

    def test_authored_cache_evicts_interleaved_large_atlases_without_changing_pixels(self):
        self._assert_authored_cache_bound(["A", "B", "A"], (1200, 1200), (1000, 1100), 3)

    def test_authored_cache_accepts_oversized_atlas_without_retaining_it(self):
        self._assert_authored_cache_bound(["A", "A"], (1500, 1500), (1450, 1450), 2)

    def test_authored_state_view_matches_one_existing_kernel_sample_and_keeps_other_states_untouched(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="authored-state-view-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "whole-ui-sheet.png"
            write_png(source, (61, 61), 17); source_sha = sha(source)
            spec = self.make_spec(root, [{"id": "STATE", "key": ["def", "RENDERTEST", 0, 0],
                                         "source": source, "source_rect": [2, 4, 11, 10],
                                         "registration": "authored-source-view-3x", "render_scales": [3]}])
            doc = json.loads(spec.read_bytes())
            doc["items"][0]["state_view"] = {"authority": doc["source_manifest"],
                "source_rect_xywh": [2, 4, 11, 10], "target_rect_1x": [0, 0, 2, 2],
                "scope": "author-chosen-ui-state-rectangle"}
            spec.write_text(json.dumps(doc), encoding="utf-8")
            copy_path = root / "independent-pixel-copy.png"
            with Image.open(source) as image:
                image.crop((2, 4, 13, 14)).save(copy_path)
            probe = root / "probe.png"; self.probe(copy_path, probe, 6, 6)
            process = self.render(spec, root / "rendered")
            self.assertEqual(process.returncode, 0, process.stderr)
            row = json.loads(process.stdout)["items"][0]
            with Image.open(probe) as reference, Image.open(row["outputs"]["candidate_3x"]["path"]) as actual:
                expected = Image.new("RGBA", (12, 12)); expected.paste(reference.convert("RGBA"), (3, 3))
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.tobytes())
            self.assertEqual(sha(source), source_sha)
            self.assertEqual(row["source_view_proof"]["retained_positive_pixels"], 110)
            self.assertNotIn("candidate_1x", row["outputs"])
            self.assertIsNone(row["common_k"])
            for index, change in enumerate([lambda d: d["items"][0].__setitem__("render_scales", [1, 3]),
                                           lambda d: d["items"][0]["state_view"].__setitem__("scope", "whole-object")]):
                trial = copy.deepcopy(doc); change(trial)
                spec.write_text(json.dumps(trial), encoding="utf-8")
                output = root / f"rejected-{index}"
                result = self.render(spec, output)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_authored_alpha_cap_changes_only_encoded_alpha_without_creating_opacity(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="authored-alpha-cap-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            spec, _ = self._atlas_spec(root, cap=True)
            process = self.render(spec, root / "rendered")
            self.assertEqual(process.returncode, 0, process.stderr)
            rows = json.loads(process.stdout)["items"]
            with Image.open(rows[0]["outputs"]["candidate_3x"]["path"]) as original, \
                 Image.open(rows[1]["outputs"]["candidate_3x"]["path"]) as result, \
                 Image.open(root / "functional-mask.png") as mask:
                expected = bytearray(original.convert("RGBA").tobytes())
                alpha = mask.getchannel("A")
                for y in range(6):
                    for x in range(6):
                        position = ((y + 3) * 12 + x + 3) * 4 + 3
                        expected[position] = min(expected[position], alpha.getpixel((x, y)))
                self.assertEqual(result.convert("RGBA").tobytes(), bytes(expected))
                self.assertGreater(result.getchannel("A").histogram()[1], 0)

    def test_authored_invalid_projection_tier_and_frame_are_rejected_before_output(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="authored-view-refusals-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            spec, original = self._atlas_spec(root)
            changes = [lambda d: d["items"][0]["atlas_view"]["source_rect_raw_rational"][0].__setitem__(0, 62),
                       lambda d: d["items"][0].__setitem__("render_scales", [1, 3]),
                       lambda d: d["items"][0]["atlas_view"]["frame_rect_3x"].__setitem__(2, 5),
                       lambda d: d["items"][0]["atlas_view"]["sample_pixels_3x"].__setitem__(0, 62)]
            for index, change in enumerate(changes):
                doc = copy.deepcopy(original); change(doc)
                spec.write_text(json.dumps(doc), encoding="utf-8")
                output = root / f"rejected-{index}"
                process = self.render(spec, output)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(output.exists())

    def test_direct_1x_3x_same_source_reference_and_under3x_rules(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-render-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source3 = root / "source-3x.png"
            source2 = root / "source-2x.png"
            reference = root / "reference-canvas.png"
            write_png(source3, (6, 6), 3)
            write_png(source2, (4, 4), 7)
            write_png(reference, (4, 4), 11)
            ref_image = Image.open(reference).convert("RGBA")
            ref_image.putpixel((0, 0), (23, 41, 59, 1))
            ref_image.save(reference, format="PNG")
            ref = {"path": str(reference), "sha256": sha(reference), "layout": "native-canvas",
                   "source_pixels": [4, 4]}
            spec = self.make_spec(root, [
                {"id": "RENDER_A", "key": ["def", "RENDERTEST", 0, 0], "source": source3, "reference": ref,
                 "render_scales": [1, 3]},
                {"id": "RENDER_B", "key": ["def", "RENDERTEST", 0, 1], "source": source2, "render_scales": [1]},
            ])
            source_hash, reference_hash = sha(source3), sha(reference)
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            receipt = json.loads(process.stdout)
            self.assertEqual(receipt["status"], "REVIEW_REQUIRED")
            self.assertFalse(receipt["official_geometry_verified_by_renderer"])
            self.assertFalse(any(receipt["authority"].values()))
            rows = {row["id"]: row for row in receipt["items"]}
            self.assertEqual(rows["RENDER_A"]["rendered_tiers"], [1, 3])
            self.assertEqual(rows["RENDER_B"]["rendered_tiers"], [1])
            self.assertEqual(rows["RENDER_B"]["source_rect_capacity_3x"], "UNDER_3X_SOURCE_RECT_DIMENSION_UPPER_BOUND")
            self.assertFalse((output / "RENDER_B" / "candidate-3x.png").exists())
            self.assertEqual(sha(source3), source_hash)
            self.assertEqual(sha(reference), reference_hash)

            oracle1, oracle3 = root / "oracle-1x.png", root / "oracle-3x.png"
            self.probe(source3, oracle1, 2, 2)
            self.probe(source3, oracle3, 6, 6)
            candidate1 = Image.open(output / "RENDER_A" / "candidate-1x.png").convert("RGBA")
            candidate3 = Image.open(output / "RENDER_A" / "candidate-3x.png").convert("RGBA")
            crop1, crop3 = Image.open(oracle1).convert("RGBA"), Image.open(oracle3).convert("RGBA")
            self.assertEqual(list(candidate1.crop((1, 1, 3, 3)).getdata()), list(crop1.getdata()))
            self.assertEqual(list(candidate3.crop((3, 3, 9, 9)).getdata()), list(crop3.getdata()))
            self.assertTrue(all(pixel[3] == 0 for x, y, pixel in ((x, y, candidate1.getpixel((x, y)))
                                                                  for y in range(4) for x in range(4)
                                                                  if not (1 <= x < 3 and 1 <= y < 3))))
            copied_reference = Image.open(output / "RENDER_A" / "reference-1x.png").convert("RGBA")
            self.assertEqual(list(copied_reference.getdata()), list(Image.open(reference).convert("RGBA").getdata()))
            self.assertEqual(copied_reference.getpixel((0, 0))[3], 1, "native reference alpha=1 must survive pixel copy")

    def test_explicit_padding_is_pixel_copy_and_never_makes_a_3x_source(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-padding-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "nonmatching-aspect.png"
            write_png(source, (6, 4), 5)
            spec = self.make_spec(root, [{"id": "PAD_A", "key": ["def", "PADTEST", 0, 0],
                                          "source": source, "registration": "integer-transparent-padding", "render_scales": [1]}])
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            receipt = json.loads(process.stdout)
            row = receipt["items"][0]
            self.assertEqual(row["common_k"], 3)
            self.assertEqual(row["registration_offset"], [0, 1])
            self.assertEqual(row["rendered_tiers"], [1])
            self.assertTrue((output / "PAD_A" / "registration-padding-diagnostic.png").is_file())
            self.assertFalse((output / "PAD_A" / "candidate-3x.png").exists())
            original = Image.open(source).convert("RGBA")
            padded = Image.open(output / "PAD_A" / "registration-padding-diagnostic.png").convert("RGBA")
            self.assertEqual(padded.size, (6, 6))
            self.assertEqual(list(padded.crop((0, 1, 6, 5)).getdata()), list(original.getdata()))
            self.assertTrue(all(padded.getpixel((x, y))[3] == 0 for x in range(6) for y in (0, 5)))

    def test_full_viewport_axis_normalization_renders_1x_only(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-axis-norm-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "axis-norm-source.png"
            write_png(source, (6, 5), 7)
            spec = self.make_spec(root, [{"id": "NORM_A", "key": ["pcx", "NORMA"],
                                          "source": source, "registration": "full-viewport-axis-normalization", "render_scales": [1]}],
                                  targets={"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [0, 0]})
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            receipt = json.loads(process.stdout)
            row = receipt["items"][0]
            self.assertEqual(row["registration"], "full-viewport-axis-normalization")
            self.assertEqual(row["rendered_tiers"], [1])
            self.assertEqual(row["source_rect_capacity_3x"], "FULL_VIEWPORT_AXIS_NORMALIZATION_1X_DIAGNOSTIC_ONLY")
            self.assertTrue((output / "NORM_A" / "candidate-1x.png").is_file())
            self.assertFalse((output / "NORM_A" / "candidate-3x.png").exists())

    def test_padding_refuses_positive_alpha_outside_view_before_creating_output(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-padding-alpha-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            image = Image.new("RGBA", (9, 9))
            image.putpixel((0, 0), (17, 29, 43, 1))
            image.putpixel((3, 3), (90, 70, 50, 255))
            image.save(source)
            before = sha(source)
            spec = self.make_spec(root, [{"id": "PAD", "key": ["def", "PAD", 0, 0],
                                        "source": source, "source_rect": [1, 1, 7, 6],
                                        "registration": "integer-transparent-padding"}])
            output = root / "rejected"
            p = self.render(spec, output)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT", p.stdout + p.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(sha(source), before)
            image.putpixel((0, 0), (17, 29, 43, 0))
            image.save(source)
            spec = self.make_spec(root, [{"id": "PAD", "key": ["def", "PAD", 0, 0],
                                        "source": source, "source_rect": [1, 1, 7, 6],
                                        "registration": "integer-transparent-padding"}])
            p = self.render(spec, root / "retained")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(json.loads(p.stdout)["items"][0]["source_view_proof"],
                             {"source_positive_pixels": 1, "retained_positive_pixels": 1,
                              "excluded_positive_pixels": 0, "all_positive_preserved": True})

    def test_native_canvas_is_sampled_once_without_applying_crop_origin_twice(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-canvas-layout-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "full-canvas-3x.png"
            Image.new("RGBA", (12, 12), (43, 97, 151, 255)).save(source)
            spec = self.make_spec(root, [{"id": "CANVAS", "key": ["def", "CANVAS", 0, 0],
                                        "source": source, "source_layout": "native-canvas",
                                        "render_scales": [1]}])
            output = root / "rendered"
            p = self.render(spec, output)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            row = json.loads(p.stdout)["items"][0]
            self.assertEqual(row["targets"], {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]})
            self.assertEqual(row["rendered_tiers"], [1])
            self.assertEqual(row["source_layout"], "native-canvas")
            with Image.open(output / "CANVAS/candidate-1x.png") as picture:
                self.assertEqual(picture.size, (4, 4))
                self.assertEqual(picture.convert("RGBA").tobytes(), bytes((43, 97, 151, 255)) * 16)
            self.assertFalse((output / "CANVAS/candidate-3x.png").exists())
            with Image.open(source) as picture:
                self.assertEqual(picture.size, (12, 12))

    def test_whole_viewport_rejects_partial_source_rect_and_unavailable_tier(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-whole-viewport-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (8, 8), 2)
            spec = self.make_spec(root, [{"id": "PART", "key": ["pcx", "PART"],
                                        "source": source, "source_rect": [1, 1, 6, 6],
                                        "registration": "full-viewport-axis-normalization"}])
            output = root / "partial"
            p = self.render(spec, output)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("FULL_VIEWPORT_REQUIRES_WHOLE_SOURCE", p.stdout + p.stderr)
            self.assertFalse(output.exists())
            spec = self.make_spec(root, [{"id": "TIER", "key": ["pcx", "TIER"],
                                        "source": source, "registration": "full-viewport-axis-normalization",
                                        "render_scales": [1, 3]}])
            p = self.render(spec, root / "bad-tier")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("REQUESTED_3X_UNAVAILABLE", p.stdout + p.stderr)
            self.assertFalse((root / "bad-tier").exists())

    def test_three_x_only_preserves_full_canvas_and_never_writes_one_x(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-three-x-only-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGBA", (12, 12), (43, 97, 151, 255)).save(source)
            reference = root / "reference.png"
            write_png(reference, (4, 4), 19)
            spec = self.make_spec(root, [{"id": "THREE", "key": ["def", "THREE", 0, 0],
                "source": source, "source_layout": "native-canvas", "render_scales": [3],
                "reference": {"path": str(reference), "sha256": sha(reference), "layout": "native-canvas",
                              "source_pixels": [4, 4]}}])
            output = root / "three-only"
            p = self.render(spec, output)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            row = json.loads(p.stdout)["items"][0]
            self.assertEqual(row["rendered_tiers"], [3])
            self.assertEqual(set(row["outputs"]), {"candidate_3x"})
            with Image.open(output / "THREE/candidate-3x.png") as picture:
                self.assertEqual(picture.size, (12, 12))
                self.assertEqual(picture.convert("RGBA").tobytes(), bytes((43, 97, 151, 255)) * 144)
            self.assertFalse((output / "THREE/candidate-1x.png").exists())
            self.assertFalse((output / "THREE/reference-1x.png").exists())

    def test_default_three_x_matches_explicit_and_never_writes_one_x(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-default-three-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source, reference = root / "source.png", root / "reference.png"
            write_png(source, (12, 12), 11)
            write_png(reference, (4, 4), 19)
            item = {"id": "THREE", "key": ["def", "THREE", 0, 0],
                    "source": source, "source_layout": "native-canvas",
                    "reference": {"path": str(reference), "sha256": sha(reference),
                                  "layout": "native-canvas", "source_pixels": [4, 4]}}
            hashes = []
            for index, scales in enumerate((None, [3])):
                declared = copy.deepcopy(item)
                if scales is not None:
                    declared["render_scales"] = scales
                spec = self.make_spec(root, [declared])
                output = root / f"rendered-{index}"
                p = self.render(spec, output)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                row = json.loads(p.stdout)["items"][0]
                self.assertEqual(row["rendered_tiers"], [3])
                self.assertEqual(set(row["outputs"]), {"candidate_3x"})
                self.assertEqual(len(list(output.rglob("*.png"))), 1)
                record = row["outputs"]["candidate_3x"]
                hashes.append((record["png_sha256"], record["rgba_sha256"]))
            self.assertEqual(hashes[0], hashes[1])

    def test_three_x_padding_keeps_pixels_without_saving_a_source_copy(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-three-padding-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (8, 6), 11)
            original_sha = sha(source)
            hashes = []
            for index, scales in enumerate(([1, 3], [3])):
                spec = self.make_spec(root, [{"id": "PAD", "key": ["def", "PAD", 0, 0],
                                             "source": source, "registration": "integer-transparent-padding",
                                             "render_scales": scales}])
                output = root / f"rendered-{index}"
                p = self.render(spec, output)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                row = json.loads(p.stdout)["items"][0]
                self.assertTrue(row["source_view_proof"]["all_positive_preserved"])
                self.assertEqual(row["source_view_proof"]["source_positive_pixels"], 48)
                self.assertEqual(row["registered_pixels"], [8, 8])
                self.assertEqual(row["registration_offset"], [0, 1])
                record = row["outputs"]["candidate_3x"]
                hashes.append((record["png_sha256"], record["rgba_sha256"]))
                if scales == [3]:
                    self.assertEqual(set(row["outputs"]), {"candidate_3x"})
                    self.assertEqual(len(list(output.rglob("*.png"))), 1)
                else:
                    self.assertIn("registration_padding_diagnostic", row["outputs"])
            self.assertEqual(hashes[0], hashes[1])
            self.assertEqual(sha(source), original_sha)

    def test_explicit_padding_and_crop_output_match_the_existing_kernel(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-explicit-padding-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (8, 6), 11)
            original_sha = sha(source)
            # Independent reference supplies the declared transparent domain to
            # the already-qualified kernel, without a second sampler.
            with Image.open(source) as raw:
                padded = Image.new("RGBA", (10, 10))
                padded.paste(raw.convert("RGBA"), (1, 3))
                padded.save(root / "oracle-domain.png")
            self.probe(root / "oracle-domain.png", root / "oracle-3x.png", 6, 6)
            spec = self.make_spec(root, [{"id": "PAD", "key": ["def", "PAD", 0, 0],
                "source": source, "registration": "integer-transparent-padding", "render_scales": [3],
                "padding_registration": {"common_k": 5, "offset": [1, 3]},
                "output_layout": "native-crop"}])
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            row = json.loads(process.stdout)["items"][0]
            self.assertEqual(row["common_k"], 5)
            self.assertEqual(row["registration_offset"], [1, 3])
            self.assertEqual(row["registered_pixels"], [10, 10])
            self.assertTrue(row["source_view_proof"]["all_positive_preserved"])
            self.assertEqual(row["outputs"]["candidate_3x"]["layout"], "native-crop")
            self.assertEqual(row["outputs"]["candidate_3x"]["origin_pixels"], [3, 3])
            with Image.open(output / "PAD/candidate-3x.png") as actual, Image.open(root / "oracle-3x.png") as oracle:
                self.assertEqual(actual.size, (6, 6))
                self.assertEqual(actual.convert("RGBA").tobytes(), oracle.convert("RGBA").tobytes())
            self.assertEqual(len(list(output.rglob("*.png"))), 1)
            self.assertEqual(sha(source), original_sha)

    def test_explicit_padding_rejects_invalid_domains_and_mixed_group_scale(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-padding-reject-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (8, 6), 11)
            item = {"id": "A", "key": ["def", "PAD", 0, 0], "source": source,
                    "registration": "integer-transparent-padding",
                    "padding_registration": {"common_k": 5, "offset": [1, 3]}}
            for index, registration in enumerate(({"common_k": 3, "offset": [0, 0]},
                                                  {"common_k": 5, "offset": [3, 0]},
                                                  {"common_k": True, "offset": [0, 0]})):
                declared = copy.deepcopy(item)
                declared["padding_registration"] = registration
                process = self.render(self.make_spec(root, [declared]), root / f"bad-{index}")
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse((root / f"bad-{index}").exists())
            for index, second_registration in enumerate((None, {"common_k": 6, "offset": [1, 3]})):
                other = {**item, "id": "B", "key": ["def", "PAD", 0, 1]}
                if second_registration is None:
                    other.pop("padding_registration")
                else:
                    other["padding_registration"] = second_registration
                output = root / f"mixed-{index}"
                process = self.render(self.make_spec(root, [item, other]), output)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("PADDING_GROUP_CONTRACT_MISMATCH", process.stdout + process.stderr)
                self.assertFalse(output.exists())

    def test_crop_output_equals_original_canvas_window_and_refuses_unsafe_modes(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-crop-layout-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (6, 6), 17)
            item = {"id": "CROP", "key": ["def", "CROP", 0, 0], "source": source}
            full = root / "full"
            result = self.render(self.make_spec(root, [item]), full)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cropped = root / "crop"
            result = self.render(self.make_spec(root, [{**item, "output_layout": "native-crop"}]), cropped)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with Image.open(full / "CROP/candidate-3x.png") as canvas, Image.open(cropped / "CROP/candidate-3x.png") as crop:
                self.assertEqual(canvas.convert("RGBA").crop((3, 3, 9, 9)).tobytes(), crop.convert("RGBA").tobytes())
            for index, (extra, error) in enumerate((
                    ({"render_scales": [1, 3]}, "CROP_OUTPUT_REQUIRES_NATIVE_CROP_3X_ONLY"),
                    ({"source_layout": "native-canvas"}, "REQUESTED_3X_UNAVAILABLE"))):
                bad = {**item, "output_layout": "native-crop", **extra}
                output = root / f"bad-{index}"
                result = self.render(self.make_spec(root, [bad]), output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stdout + result.stderr)
                self.assertFalse(output.exists())

    @staticmethod
    def _canvas_supported_fixture(seed=0):
        image = Image.new("RGBA", (53, 53))
        for y in range(18, 35):
            for x in range(18, 35):
                alpha = 1 if min(x - 18, y - 18, 34 - x, 34 - y) < 3 else 255
                image.putpixel((x, y), ((x * 7 + seed) % 256, (y * 9 + seed * 3) % 256, 83, alpha))
        return image

    def test_canvas_crop_matches_full_grid_with_fractional_source_scale_and_alpha1(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-canvas-crop-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            self._canvas_supported_fixture().save(source)
            original = sha(source)
            targets = {"canvas_1x": [8, 8], "crop_1x": [4, 4], "origin_1x": [2, 2]}
            item = {"id": "CANVAS", "key": ["def", "CANVAS", 0, 0], "source": source,
                    "source_layout": "native-canvas", "render_scales": [3]}
            full = root / "full-oracle"
            result = self.render(self.make_spec(root, [item], targets), full)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with Image.open(full / "CANVAS/candidate-3x.png") as image:
                canvas = image.convert("RGBA")
                oracle = canvas.crop((6, 6, 18, 18))
            histogram = canvas.getchannel("A").histogram()
            self.assertEqual(sum(histogram[1:]), sum(oracle.getchannel("A").histogram()[1:]))
            self.assertGreater(histogram[1], 0)
            output = root / "crop"
            result = self.render(self.make_spec(root, [{**item, "output_layout": "native-crop"}], targets), output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            row = json.loads(result.stdout)["items"][0]
            product = row["outputs"]["candidate_3x"]
            with Image.open(output / "CANVAS/candidate-3x.png") as image:
                self.assertEqual(image.size, (12, 12))
                self.assertEqual(image.convert("RGBA").tobytes(), oracle.tobytes())
            self.assertEqual(row["targets"], targets)
            self.assertTrue(row["source_view_proof"]["all_positive_preserved"])
            proof = product["canvas_crop_proof"]
            self.assertEqual(proof["sampled_canvas_pixels"], [24, 24])
            self.assertEqual(proof["crop_rect_3x"], [6, 6, 12, 12])
            self.assertEqual(proof["positive_alpha_pixels_before_crop"], sum(histogram[1:]))
            self.assertEqual(proof["positive_alpha_pixels_after_crop"], sum(histogram[1:]))
            self.assertEqual(proof["alpha1_pixels_before_crop"], histogram[1])
            self.assertEqual(proof["alpha1_pixels_after_crop"], histogram[1])
            self.assertEqual(proof["excluded_positive_alpha_pixels"], 0)
            self.assertEqual(proof["sampling_calls"], 1)
            self.assertEqual(proof["intermediate_png_files"], 0)
            self.assertEqual(len(list(output.rglob("*.png"))), 1)
            self.assertEqual(sha(source), original)

    def test_canvas_crop_double_panel_keeps_exact_source_views_and_distinct_crops(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-canvas-panels-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "two-panels.png"
            atlas = Image.new("RGBA", (106, 53))
            atlas.paste(self._canvas_supported_fixture(7), (0, 0))
            atlas.paste(self._canvas_supported_fixture(113), (53, 0))
            atlas.save(source)
            targets = {"canvas_1x": [8, 8], "crop_1x": [4, 4], "origin_1x": [2, 2]}
            items = [{"id": f"PANEL_{i}", "key": ["def", "PANELS", 0, i], "source": source,
                      "source_rect": [i * 53, 0, 53, 53], "source_layout": "native-canvas",
                      "output_layout": "native-crop", "render_scales": [3]} for i in range(2)]
            result = self.render(self.make_spec(root, items, targets), root / "crop")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(result.stdout)["items"]
            self.assertEqual([r["source_rect"] for r in rows], [[0, 0, 53, 53], [53, 0, 53, 53]])
            views = [r["source_view_proof"] for r in rows]
            self.assertTrue(all(not r["all_positive_preserved"] for r in views))
            self.assertEqual(sum(r["retained_positive_pixels"] for r in views), views[0]["source_positive_pixels"])
            self.assertNotEqual(rows[0]["outputs"]["candidate_3x"]["rgba_sha256"],
                                rows[1]["outputs"]["candidate_3x"]["rgba_sha256"])
            self.assertTrue(all(r["outputs"]["candidate_3x"]["canvas_crop_proof"]["excluded_positive_alpha_pixels"] == 0 for r in rows))
            self.assertEqual(len(list((root / "crop").rglob("*.png"))), 2)

    def test_canvas_crop_refuses_alpha1_only_loss_and_other_registration_modes(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-canvas-refuse-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "all-alpha1.png"
            Image.new("RGBA", (53, 53), (31, 47, 83, 1)).save(source)
            targets = {"canvas_1x": [8, 8], "crop_1x": [4, 4], "origin_1x": [2, 2]}
            item = {"id": "FAINT", "key": ["def", "FAINT", 0, 0], "source": source,
                    "source_layout": "native-canvas", "output_layout": "native-crop", "render_scales": [3]}
            result = self.render(self.make_spec(root, [item], targets), root / "loss")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CROP_OUTPUT_POSITIVE_ALPHA_OUTSIDE_NATIVE_CROP", result.stdout + result.stderr)
            self.assertIn("pixels=432 alpha1=432", result.stdout + result.stderr)
            self.assertFalse((root / "loss").exists())
            for i, registration in enumerate(("full-viewport-axis-normalization-3x", "integer-transparent-padding")):
                bad = {**item, "registration": registration}
                output = root / f"registration-{i}"
                result = self.render(self.make_spec(root, [bad], targets), output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CANVAS_CROP_OUTPUT_REQUIRES_STRICT_ASPECT_3X_ONLY", result.stdout + result.stderr)
                self.assertFalse(output.exists())

    def test_default_three_x_rejects_under3x_without_creating_output(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-default-under3x-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (4, 4), 11)
            spec = self.make_spec(root, [{"id": "UNDER", "key": ["def", "UNDER", 0, 0],
                                          "source": source}])
            output = root / "rejected"
            p = self.render(spec, output)
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("REQUESTED_3X_UNAVAILABLE", p.stdout + p.stderr)
            self.assertFalse(output.exists())

    def test_explicit_three_x_viewport_refuses_undersized_master(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-viewport-three-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (14, 11), 21)
            targets = {"canvas_1x": [4, 3], "crop_1x": [4, 3], "origin_1x": [0, 0]}
            item = {"id": "VIEW", "key": ["pcx", "VIEW"], "source": source,
                    "registration": "full-viewport-axis-normalization-3x", "render_scales": [3]}
            spec = self.make_spec(root, [item], targets)
            p = self.render(spec, root / "large-enough")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            row = json.loads(p.stdout)["items"][0]
            self.assertEqual(row["rendered_tiers"], [3])
            self.assertEqual(row["outputs"]["candidate_3x"]["pixels"], [12, 9])
            self.assertNotIn("candidate_1x", row["outputs"])
            write_png(source, (8, 7), 23)
            spec = self.make_spec(root, [item], targets)
            p = self.render(spec, root / "too-small")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("NEEDS_HIGHER_MASTER_FOR_3X", p.stdout + p.stderr)
            self.assertFalse((root / "too-small").exists())

    def test_rejects_hash_geometry_duplicate_and_output_escape_before_writing(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-reject-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (6, 6), 2)
            spec_path = self.make_spec(root, [{"id": "ONE", "key": ["def", "REJECTTEST", 0, 0], "source": source}])
            valid = json.loads(spec_path.read_text(encoding="utf-8"))
            output = root / "bad-sha"
            changed = json.loads(json.dumps(valid))
            changed["items"][0]["source_sha256"] = "0" * 64
            spec_path.write_text(json.dumps(changed), encoding="utf-8")
            process = self.render(spec_path, output)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(output.exists())

            changed = json.loads(json.dumps(valid))
            changed["items"][0]["geometry_binding_sha256"] = "0" * 64
            spec_path.write_text(json.dumps(changed), encoding="utf-8")
            process = self.render(spec_path, root / "bad-geometry")
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root / "bad-geometry").exists())

            changed = json.loads(json.dumps(valid))
            duplicate = json.loads(json.dumps(changed["items"][0]))
            duplicate["id"] = "TWO"
            changed["items"].append(duplicate)
            spec_path.write_text(json.dumps(changed), encoding="utf-8")
            process = self.render(spec_path, root / "duplicate-key")
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root / "duplicate-key").exists())

            process = self.render(spec_path, OUTPUT_BASE.parent / "escaped-output")
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((OUTPUT_BASE.parent / "escaped-output").exists())

            existing = root / "existing"
            existing.mkdir()
            marker = existing / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            changed = json.loads(json.dumps(valid))
            spec_path.write_text(json.dumps(changed), encoding="utf-8")
            process = self.render(spec_path, existing)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_under_native_1x_rejected_and_duplicate_cli_option_rejected(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-arg-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "too-small.png"
            write_png(source, (1, 2), 4)
            spec = self.make_spec(root, [{"id": "SMALL", "key": ["def", "SMALLTEST", 0, 0], "source": source}])
            out = root / "under-native"
            process = self.render(spec, out)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(out.exists())
            process = self.render(spec, root / "duplicate-args", "--outdir", str(root / "other"))
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root / "duplicate-args").exists())

    @unittest.skipUnless(os.name == "nt", "Windows directory share-mode contract")
    def test_windows_directory_lock_access_and_share_modes(self) -> None:
        import ctypes
        from ctypes import wintypes

        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-directory-share-contract-", dir=OUTPUT_BASE) as temporary:
            directory = Path(temporary) / "locked"
            directory.mkdir()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            invalid = ctypes.c_void_p(-1).value
            flags = 0x02000000 | 0x00200000  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            share_rw = 0x1 | 0x2
            share_rwd = share_rw | 0x4
            read_attributes = 0x80
            list_directory = 0x1
            add_subdirectory = 0x4
            delete_access = 0x00010000
            synchronize = 0x00100000

            def open_directory(access: int, share: int):
                ctypes.set_last_error(0)
                handle = kernel32.CreateFileW(str(directory), access, share, None, 3, flags, None)
                value = getattr(handle, "value", handle)
                return (None, ctypes.get_last_error()) if value is None or value == invalid else (handle, 0)

            def assert_rename_blocked(label: str) -> None:
                parked = directory.with_name(directory.name + "-" + label)
                try:
                    directory.rename(parked)
                except OSError as error:
                    self.assertEqual(getattr(error, "winerror", None), 32, str(error))
                else:
                    parked.rename(directory)
                    self.fail(f"persistent directory handle allowed rename: {label}")

            # Attributes-only handles are metadata probes and do not pin names.
            attributes_handle, error = open_directory(read_attributes, share_rw)
            self.assertIsNotNone(attributes_handle, f"attrs-only open failed: winerr={error}")
            moved = directory.with_name(directory.name + "-attrs-only")
            try:
                directory.rename(moved)
                self.assertTrue(moved.is_dir())
                moved.rename(directory)
            finally:
                kernel32.CloseHandle(attributes_handle)

            # Existing-directory locks need LIST_DIRECTORY, not just attributes.
            existing_lock, error = open_directory(list_directory | read_attributes, share_rw)
            self.assertIsNotNone(existing_lock, f"existing lock open failed: winerr={error}")
            try:
                assert_rename_blocked("existing-lock")
            finally:
                kernel32.CloseHandle(existing_lock)

            # This access/share pair models the persistent NtCreateFile handle.
            created_lock, error = open_directory(delete_access | list_directory | read_attributes | synchronize,
                                                 share_rw)
            self.assertIsNotNone(created_lock, f"created lock open failed: winerr={error}")
            try:
                attributes_probe, error = open_directory(read_attributes, share_rw)
                self.assertIsNotNone(attributes_probe,
                                     f"attributes probe conflicted with created lock: winerr={error}")
                kernel32.CloseHandle(attributes_probe)

                external_delete, error = open_directory(delete_access | read_attributes, share_rwd)
                self.assertIsNone(external_delete)
                self.assertEqual(error, 32, "persistent FILE_SHARE_READ|WRITE lock must reject delete access")

                add_without_delete_share, error = open_directory(add_subdirectory | read_attributes, share_rw)
                self.assertIsNone(add_without_delete_share)
                self.assertEqual(error, 32, "FILE_ADD_SUBDIRECTORY must conflict without FILE_SHARE_DELETE")

                add_with_delete_share, error = open_directory(add_subdirectory | read_attributes, share_rwd)
                self.assertIsNotNone(add_with_delete_share, f"compatible parent access failed: winerr={error}")
                kernel32.CloseHandle(add_with_delete_share)
                assert_rename_blocked("created-lock")
            finally:
                kernel32.CloseHandle(created_lock)

    @unittest.skipUnless(os.name == "nt", "Windows FILE_RENAME_INFO RootDirectory contract")
    def test_atomic_publish_succeeds_with_relative_root_directory_handle(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-rootdirectory-rename-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (6, 6), 47)
            spec = self.make_spec(root, [{"id": "ROOTDIR", "key": ["def", "ROOTDIRTEST", 0, 0],
                                          "source": source}])
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            receipt = json.loads(process.stdout)
            record = receipt["items"][0]["outputs"]["candidate_3x"]
            candidate = output / "ROOTDIR" / "candidate-3x.png"
            self.assertTrue(candidate.is_file())
            self.assertEqual(sha(candidate), record["png_sha256"])

    def test_many_sibling_outputs_keep_exact_rgba_and_reject_republication(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-sibling-batch-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            write_png(source, (6, 6), 73)
            before = sha(source)
            with Image.open(source) as image:
                expected_rgba = image.convert("RGBA").tobytes()
            items = [{"id": f"SLOT_{index:02d}", "key": ["def", "SIBLINGBATCH", 0, index],
                      "source": source, "render_scales": [3]} for index in range(51)]
            spec = self.make_spec(root, items, targets={"canvas_1x": [2, 2], "crop_1x": [2, 2],
                                                       "origin_1x": [0, 0]})
            output = root / "rendered"
            process = self.render(spec, output)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            receipt = json.loads(process.stdout)
            self.assertEqual(receipt["status"], "REVIEW_REQUIRED")
            self.assertEqual([row["id"] for row in receipt["items"]], [item["id"] for item in items])
            self.assertEqual(sorted(path.name for path in output.iterdir() if path.is_dir()),
                             [item["id"] for item in items])
            self.assertEqual(len(list(output.rglob("*.png"))), 51)
            for row in receipt["items"]:
                self.assertEqual(set(row["outputs"]), {"candidate_3x"})
                self.assertEqual(row["rendered_tiers"], [3])
                candidate = output / row["id"] / "candidate-3x.png"
                self.assertEqual(sha(candidate), row["outputs"]["candidate_3x"]["png_sha256"])
                with Image.open(candidate) as image:
                    self.assertEqual(image.size, (6, 6))
                    self.assertEqual(image.convert("RGBA").tobytes(), expected_rgba)
            sealed = {path: sha(path) for path in output.rglob("*") if path.is_file()}
            retry = self.render(spec, output)
            self.assertNotEqual(retry.returncode, 0)
            self.assertIn("REVIEW_OUTPUT_ALREADY_EXISTS", retry.stdout + retry.stderr)
            self.assertEqual({path: sha(path) for path in sealed}, sealed)
            self.assertEqual(sha(source), before)

    def test_build_binding_normalizes_windows_rows_and_rejects_missing_or_drifted_sources(self) -> None:
        root, exe, rows = self.isolated_product("path-normalization")
        source = root / "source.png"
        write_png(source, (6, 6), 23)
        spec = self.make_spec(root, [{"id": "BINDING", "key": ["def", "BINDINGTEST", 0, 0], "source": source}])
        base = root / "build" / "experiments" / "native-review"
        accepted = self.render_with(exe, spec, base / "windows-backslash-row")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        accepted_receipt = json.loads(accepted.stdout)
        source_bindings = accepted_receipt["build_binding"]["kernel_sources"]
        self.assertEqual({row["path"] for row in source_bindings},
                         {"src/native_review.hpp", "src/resample.hpp", "src/wic.hpp", "src/main.cpp"})

        copy_rows = lambda: json.loads(json.dumps(list(rows.values())))
        receipt_sources = copy_rows()
        receipt_sources = [row for row in receipt_sources if row["path"].replace("\\", "/") != "src/native_review.hpp"]
        self.write_isolated_receipt(root, exe, receipt_sources)
        missing = self.render_with(exe, spec, base / "missing-required-source")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("REVIEW_BUILD_SOURCE_MISSING: src/native_review.hpp", missing.stdout + missing.stderr)
        self.assertFalse((base / "missing-required-source").exists())

        receipt_sources = copy_rows()
        drifted = next(row for row in receipt_sources if row["path"].replace("\\", "/") == "src/native_review.hpp")
        drifted["sha256"] = "0" * 64
        self.write_isolated_receipt(root, exe, receipt_sources)
        drift = self.render_with(exe, spec, base / "required-source-hash-drift")
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("REVIEW_BUILD_SOURCE_STALE: src/native_review.hpp", drift.stdout + drift.stderr)
        self.assertFalse((base / "required-source-hash-drift").exists())

        for unsafe in ("/src/native_review.hpp", "src/../src/native_review.hpp"):
            unsafe_rows = copy_rows()
            target = next(row for row in unsafe_rows if row["path"].replace("\\", "/") == "src/native_review.hpp")
            target["path"] = unsafe
            self.write_isolated_receipt(root, exe, unsafe_rows)
            rejected = self.render_with(exe, spec, base / f"unsafe-{len(unsafe)}")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("REVIEW_BUILD_SOURCE_PATH_", rejected.stdout + rejected.stderr)
            self.assertFalse((base / f"unsafe-{len(unsafe)}").exists())

    def test_frozen_formal_source_survives_candidate_rebuild_without_allowing_candidate_bypass(self) -> None:
        root, candidate_exe, rows = self.isolated_product("formal-frozen-identity")
        formal = root / "dist" / "H3TierExport.exe"
        formal.parent.mkdir(parents=True)
        shutil.copyfile(candidate_exe, formal)
        frozen = root / "dist" / "H3TierExport-build-receipt.json"
        candidate_receipt = root / "build" / "candidate" / "build-receipt.json"
        prior = candidate_receipt.read_bytes()
        frozen.write_bytes(prior)
        tests_path = root / "dist" / "H3TierExport-tests-receipt.json"
        tests_path.write_text(json.dumps({"schema": "h3.tier-export.test-suite.v2",
            "passed": True, "source_current": True, "exe_sha256": sha(formal),
            "build_receipt_sha256": sha(frozen), "results": [{"name": "synthetic-fixture-only", "exit_code": 0}]}) + "\n",
            encoding="utf-8")
        tests_prior = tests_path.read_bytes()
        report = {
            "schema": "h3.tier-export.release.v2",
            "exe_sha256": sha(formal),
            "build_receipt_sha256": sha(frozen),
            "test_receipt_sha256": sha(tests_path),
            "files": [
                {"path": formal.name, "sha256": sha(formal)},
                {"path": frozen.name, "sha256": sha(frozen)},
                {"path": tests_path.name, "sha256": sha(tests_path)}
            ]
        }
        release_raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (root / "dist" / "H3TierExport-release.json").write_bytes(release_raw)
        mirror = root / "reports" / "current" / "release.json"
        mirror.parent.mkdir(parents=True)
        mirror.write_bytes(release_raw)
        image = root / "source.png"
        write_png(image, (6, 6), 61)
        spec = self.make_spec(root, [{"id": "FORMAL", "key": ["def", "FORMAL", 0, 0],
                                      "source": image}])
        base = root / "build" / "experiments" / "native-review"
        good = self.render_with(formal, spec, base / "formal-baseline")
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        binding = json.loads(good.stdout)["build_binding"]
        self.assertEqual(Path(binding["build_receipt"]["path"]), frozen)
        self.assertEqual(binding["build_receipt"]["sha256"], sha(frozen))
        self.assertEqual(binding["test_receipt"]["sha256"], sha(tests_path))

        candidate = json.loads(prior)
        candidate["executable"]["sha256"] = "0" * 64
        candidate_receipt.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        formally_stable = self.render_with(formal, spec, base / "formal-after-candidate-drift")
        self.assertEqual(formally_stable.returncode, 0, formally_stable.stdout + formally_stable.stderr)
        same_binary_candidate = self.render_with(candidate_exe, spec, base / "candidate-sha-drift")
        self.assertNotEqual(same_binary_candidate.returncode, 0)
        self.assertIn("REVIEW_EXECUTABLE_DIFFERS_FROM_CURRENT_BUILD",
                      same_binary_candidate.stdout + same_binary_candidate.stderr)
        self.assertFalse((base / "candidate-sha-drift").exists())

        candidate_receipt.write_bytes(prior)
        cpp = root / "src" / "native_review.hpp"
        cpp.write_bytes(cpp.read_bytes() + b"\n// changed checkout after formal publication")
        formal_after_source_edit = self.render_with(formal, spec, base / "formal-after-source-edit")
        self.assertEqual(formal_after_source_edit.returncode, 0,
                         formal_after_source_edit.stdout + formal_after_source_edit.stderr)
        stale_candidate = self.render_with(candidate_exe, spec, base / "candidate-source-drift")
        self.assertNotEqual(stale_candidate.returncode, 0)
        self.assertIn("REVIEW_BUILD_SOURCE_STALE: src/native_review.hpp",
                      stale_candidate.stdout + stale_candidate.stderr)
        self.assertFalse((base / "candidate-source-drift").exists())

        tests_path.write_bytes(tests_prior + b" ")
        broken_tests = self.render_with(formal, spec, base / "formal-tamper-tests")
        self.assertNotEqual(broken_tests.returncode, 0)
        self.assertIn("REVIEW_RELEASE_FROZEN_TEST_RECEIPT_DRIFT", broken_tests.stdout + broken_tests.stderr)
        self.assertFalse((base / "formal-tamper-tests").exists())
        tests_path.write_bytes(tests_prior)
        frozen.write_bytes(frozen.read_bytes() + b" ")
        broken_frozen = self.render_with(formal, spec, base / "formal-tamper-frozen")
        self.assertNotEqual(broken_frozen.returncode, 0)
        self.assertIn("REVIEW_RELEASE_FROZEN_BUILD_RECEIPT_DRIFT",
                      broken_frozen.stdout + broken_frozen.stderr)
        self.assertFalse((base / "formal-tamper-frozen").exists())
        frozen.write_bytes(prior)
        mirror.write_bytes(release_raw + b" ")
        broken_mirror = self.render_with(formal, spec, base / "formal-tamper-report-copy")
        self.assertNotEqual(broken_mirror.returncode, 0)
        self.assertIn("REVIEW_RELEASE_REPORT_COPY_MISMATCH",
                      broken_mirror.stdout + broken_mirror.stderr)
        self.assertFalse((base / "formal-tamper-report-copy").exists())

        # Formal kernels are in the EXE: neither layout discovery nor their
        # frozen build identity may depend on a mutable development checkout.
        mirror.write_bytes(release_raw)
        (root / "src").rename(root / "src-offline")
        (root / ".gitignore").rename(root / ".gitignore-offline")
        self.assertFalse((root / "src" / "main.cpp").exists())
        self.assertFalse((root / ".gitignore").exists())
        self.assertFalse((root / "scripts").exists())
        source_free = self.render_with(formal, spec, base / "formal-without-checkout")
        self.assertEqual(source_free.returncode, 0, source_free.stdout + source_free.stderr)

        archive = root / "dist" / "archive" / "explicit-frozen-fixture"
        archive.mkdir(parents=True)
        names = ("H3TierExport.exe", "H3TierExport-build-receipt.json",
                 "H3TierExport-tests-receipt.json", "H3TierExport-release.json")
        for name in names:
            shutil.copyfile(formal.parent / name, archive / name)
        archived_exe = archive / "H3TierExport.exe"
        archived_build = archive / "H3TierExport-build-receipt.json"
        archived_tests = archive / "H3TierExport-tests-receipt.json"
        archived_release = archive / "H3TierExport-release.json"
        # The publisher records release rows by leaf name. Archive selection
        # supplies the exact directory and still verifies the matching hashes.
        archived_rows = json.loads(archived_release.read_bytes())["files"]
        self.assertEqual({Path(row["path"]).name for row in archived_rows},
                         {formal.name, frozen.name, tests_path.name})
        self.assertTrue(all(not Path(row["path"]).is_absolute() for row in archived_rows))
        for name in names:
            current = formal.parent / name
            current.rename(current.with_name(current.name + ".current-offline"))
        mirror.rename(mirror.with_name("release.json.current-offline"))
        archive_good = self.render_with(archived_exe, spec, base / "archive-without-current-or-checkout")
        self.assertEqual(archive_good.returncode, 0, archive_good.stdout + archive_good.stderr)
        archived_binding = json.loads(archive_good.stdout)["build_binding"]
        self.assertEqual(Path(archived_binding["build_receipt"]["path"]), archived_build)
        self.assertEqual(archived_binding["build_receipt"]["sha256"], sha(archived_build))
        self.assertEqual(Path(archived_binding["test_receipt"]["path"]), archived_tests)
        self.assertEqual(archived_binding["test_receipt"]["sha256"], sha(archived_tests))

        # A byte-identical receipt in current dist cannot satisfy a missing
        # sidecar next to the explicitly selected archived executable.
        archived_build.rename(archive / "build-receipt.offline")
        frozen.write_bytes(prior)
        missing_output = base / "archive-missing-local-build"
        missing_local = self.render_with(archived_exe, spec, missing_output)
        self.assertNotEqual(missing_local.returncode, 0)
        self.assertFalse(missing_output.exists())
        (archive / "build-receipt.offline").rename(archived_build)

        for case, expected_error in (
            ("tests-unbound-bytes", "REVIEW_RELEASE_FROZEN_TEST_RECEIPT_DRIFT"),
            ("build-unbound-bytes", "REVIEW_RELEASE_FROZEN_BUILD_RECEIPT_DRIFT"),
            ("tests-wrong-exe", "REVIEW_RELEASE_TEST_BUILD_IDENTITY_MISMATCH"),
            ("build-wrong-exe", "REVIEW_RELEASE_TEST_BUILD_IDENTITY_MISMATCH"),
            ("release-foreign-file", "REVIEW_RELEASE_FILE_IDENTITY_MISMATCH"),
            ("release-wrong-exe", "REVIEW_RELEASE_EXE_IDENTITY_MISMATCH"),
        ):
            with self.subTest(archive_case=case):
                build_raw, tests_raw = prior, tests_prior
                release_doc = json.loads(release_raw)
                if case == "tests-unbound-bytes":
                    tests_raw += b" "
                elif case == "build-unbound-bytes":
                    build_raw += b" "
                elif case == "tests-wrong-exe":
                    tests_doc = json.loads(tests_raw)
                    tests_doc["exe_sha256"] = "0" * 64
                    tests_raw = (json.dumps(tests_doc) + "\n").encode("utf-8")
                    digest = hashlib.sha256(tests_raw).hexdigest()
                    release_doc["test_receipt_sha256"] = digest
                    next(row for row in release_doc["files"] if Path(row["path"]).name == tests_path.name)["sha256"] = digest
                elif case == "build-wrong-exe":
                    build_doc = json.loads(build_raw)
                    build_doc["executable"]["sha256"] = "0" * 64
                    build_raw = (json.dumps(build_doc) + "\n").encode("utf-8")
                    digest = hashlib.sha256(build_raw).hexdigest()
                    release_doc["build_receipt_sha256"] = digest
                    next(row for row in release_doc["files"] if Path(row["path"]).name == frozen.name)["sha256"] = digest
                elif case == "release-foreign-file":
                    next(row for row in release_doc["files"] if Path(row["path"]).name == frozen.name)["path"] = str(root / "unrelated-build.json")
                else:
                    release_doc["exe_sha256"] = "0" * 64
                archived_build.write_bytes(build_raw)
                archived_tests.write_bytes(tests_raw)
                archived_release.write_text(json.dumps(release_doc) + "\n", encoding="utf-8")
                rejected_output = base / f"archive-{case}"
                rejected = self.render_with(archived_exe, spec, rejected_output)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(expected_error, rejected.stdout + rejected.stderr)
                self.assertFalse(rejected_output.exists())
        archived_build.write_bytes(prior)
        archived_tests.write_bytes(tests_prior)
        archived_release.write_bytes(release_raw)

    def test_spec_file_paths_must_be_absolute(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-review-absolute-path-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            source = root / "source.png"
            reference = root / "reference.png"
            write_png(source, (6, 6), 31)
            write_png(reference, (4, 4), 37)
            reference_binding = {"path": str(reference), "sha256": sha(reference), "layout": "native-canvas",
                                 "source_pixels": [4, 4]}
            spec = self.make_spec(root, [{"id": "ABSOLUTE", "key": ["def", "ABSPATHTEST", 0, 0],
                                          "source": source, "reference": reference_binding}])
            original = json.loads(spec.read_text(encoding="utf-8"))
            mutations = [
                lambda value: value["source_manifest"].update(path="source-manifest.fixture.json"),
                lambda value: value["official_inventory"].update(path="official-inventory.fixture.gz"),
                lambda value: value["items"][0].update(source_png="source.png"),
                lambda value: value["items"][0]["reference"].update(path="reference.png"),
            ]
            for index, mutate in enumerate(mutations):
                changed = json.loads(json.dumps(original))
                mutate(changed)
                spec.write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                output = root / f"relative-path-{index}"
                process = self.render(spec, output)
                self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertIn("REVIEW_PATH_MUST_BE_ABSOLUTE", process.stdout + process.stderr)
                self.assertFalse(output.exists())

    def test_jpeg_content_with_png_extension_is_rejected_for_source_and_reference(self) -> None:
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-review-png-container-contract-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary)
            valid_source = root / "valid-source.png"
            fake_source = root / "fake-source.png"
            fake_reference = root / "fake-reference.png"
            write_png(valid_source, (6, 6), 41)
            Image.new("RGB", (6, 6), (20, 60, 100)).save(fake_source, format="JPEG")
            Image.new("RGB", (4, 4), (100, 60, 20)).save(fake_reference, format="JPEG")

            spec = self.make_spec(root, [{"id": "JPEG_SOURCE", "key": ["def", "JPEGTEST", 0, 0],
                                          "source": fake_source}])
            source_out = root / "jpeg-source-output"
            process = self.render(spec, source_out)
            self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertIn("REVIEW_PNG_REQUIRED: source_png", process.stdout + process.stderr)
            self.assertFalse(source_out.exists())

            reference_binding = {"path": str(fake_reference), "sha256": sha(fake_reference),
                                 "layout": "native-canvas", "source_pixels": [4, 4]}
            spec = self.make_spec(root, [{"id": "JPEG_REFERENCE", "key": ["def", "JPEGTEST", 0, 1],
                                          "source": valid_source, "reference": reference_binding}])
            reference_out = root / "jpeg-reference-output"
            process = self.render(spec, reference_out)
            self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertIn("REVIEW_PNG_REQUIRED: reference", process.stdout + process.stderr)
            self.assertFalse(reference_out.exists())

    @unittest.skipUnless(os.name == "nt", "Windows directory handle and reparse contract")
    def test_output_directory_handles_block_live_rename_at_each_level(self) -> None:
        root, exe, _ = self.isolated_product("directory-rename-lock")
        base = root / "build" / "experiments" / "native-review"
        base.parent.mkdir(parents=True, exist_ok=True)
        source = root / "large-source.png"
        Image.new("RGBA", (4095, 4095), (74, 113, 186, 255)).save(source, format="PNG")
        spec = self.make_spec(root, [{"id": "LOCKED", "key": ["def", "LOCKTEST", 0, 0], "source": source}],
                              targets={"canvas_1x": [1365, 1365], "crop_1x": [1365, 1365],
                                       "origin_1x": [0, 0]})
        output = base / "rename-race"
        command = [str(exe), "review-render", "--spec", str(spec), "--spec-sha256", sha(spec),
                   "--outdir", str(output)]
        process = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
        item = output / "LOCKED"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and process.poll() is None:
            if base.is_dir() and output.is_dir() and item.is_dir():
                break
            time.sleep(0.002)
        self.assertIsNone(process.poll(), "renderer completed before live directory locks could be exercised")
        self.assertTrue(item.is_dir(), "renderer did not create the locked item directory")

        # root/build and build/experiments existed before render; base, output,
        # and item are created by this render and must all stay pinned.
        for directory in (root / "build", base.parent, base, output, item):
            parked = directory.with_name(directory.name + "-rename-probe")
            try:
                directory.rename(parked)
            except OSError as error:
                self.assertIn(getattr(error, "winerror", None), (5, 32), str(error))
            else:
                parked.rename(directory)
                self.fail(f"live renderer allowed directory replacement: {directory}")

        stdout, stderr = process.communicate(timeout=120)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertTrue((output / "LOCKED" / "candidate-3x.png").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point contract")
    def test_preexisting_safe_base_junction_is_rejected_without_touching_target(self) -> None:
        root, exe, _ = self.isolated_product("safe-base-junction")
        base = root / "build" / "experiments" / "native-review"
        base.parent.mkdir(parents=True, exist_ok=True)
        external = root / "junction-target"
        external.mkdir()
        marker = external / "preserve.txt"
        marker.write_text("external marker", encoding="utf-8")
        try:
            os.symlink(external, base, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlink creation is unavailable: {error}")

        source = root / "source.png"
        write_png(source, (6, 6), 71)
        spec = self.make_spec(root, [{"id": "JUNCTION", "key": ["def", "LINKTEST", 0, 0], "source": source}])
        output = base / "must-not-appear"
        process = self.render_with(exe, spec, output)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("REVIEW_OUTPUT_REPARSE_POINT", process.stdout + process.stderr)
        self.assertFalse((external / "must-not-appear").exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "external marker")

    @unittest.skipUnless(os.name == "nt", "Windows exact-created-object cleanup contract")
    def test_failed_render_removes_only_its_files_and_keeps_unowned_marker_and_junction(self) -> None:
        root, exe, _ = self.isolated_product("exact-cleanup")
        base = root / "build" / "experiments" / "native-review"
        base.mkdir(parents=True, exist_ok=True)
        source_first = root / "first-large.png"
        source_second = root / "second-large.png"
        Image.new("RGBA", (4095, 4095), (93, 141, 57, 255)).save(source_first, format="PNG")
        Image.new("RGBA", (4095, 4095), (33, 101, 179, 255)).save(source_second, format="PNG")
        targets = {"canvas_1x": [1365, 1365], "crop_1x": [1365, 1365], "origin_1x": [0, 0]}
        spec = self.make_spec(root, [
            {"id": "FIRST", "key": ["def", "CLEANUPTEST", 0, 0], "source": source_first},
            {"id": "SECOND", "key": ["def", "CLEANUPTEST", 0, 1], "source": source_second},
        ], targets=targets)
        output = base / "failed-render"
        external = root / "cleanup-target"
        external.mkdir()
        external_marker = external / "preserve.txt"
        external_marker.write_text("external marker", encoding="utf-8")
        command = [str(exe), "review-render", "--spec", str(spec), "--spec-sha256", sha(spec),
                   "--outdir", str(output)]
        process = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
        first_output = output / "FIRST" / "candidate-3x.png"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and process.poll() is None and not first_output.is_file():
            time.sleep(0.002)
        self.assertIsNone(process.poll(), "renderer completed before the injected input failure")
        self.assertTrue(first_output.is_file(), "first item did not reach its output write")

        marker = output / "keep-user-file.txt"
        marker.write_text("user-owned", encoding="utf-8")
        link = output / "external-link"
        try:
            os.symlink(external, link, target_is_directory=True)
        except OSError as error:
            process.terminate()
            process.communicate(timeout=30)
            self.skipTest(f"directory symlink creation is unavailable: {error}")
        moved_source = root / "second-large.png.moved"
        source_second.rename(moved_source)

        stdout, stderr = process.communicate(timeout=120)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertIn("REVIEW_CLEANUP_PARTIAL", stdout + stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "user-owned")
        self.assertTrue(link.is_symlink())
        self.assertEqual(external_marker.read_text(encoding="utf-8"), "external marker")
        self.assertEqual(sorted(entry.name for entry in output.iterdir()), ["external-link", "keep-user-file.txt"])


    @staticmethod
    def town_fixture(root, label, source, targets, rings, rect=None, k=3, offset=(0, 0), rebind=False):
        """Synthetic owner declarations only; never actual art/Source approval."""
        def write(name, value):
            p = root / (label + "-" + name + ".json")
            p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return p
        def ref(p):
            return {"path": str(p), "sha256": sha(p), "bytes": p.stat().st_size}
        with Image.open(source) as image:
            pixels = list(image.size)
            rgba_sha = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        rect = rect or [0, 0, *pixels]
        identity = "TOWN_" + label.upper()
        parent = {**ref(source), "pixels": pixels, "whole_RGBA_sha256": rgba_sha}
        history = copy.deepcopy(parent)
        if rebind:
            history["path"] = str(root / "not-a-live-generated-parent.png")
            parent["same_exact_original_as_G"] = True
        standard = write("standard", {"schema": "TEST_ONLY_ART_STANDARD"})
        request = write("parent-request", {"schema": "TEST_ONLY_HISTORICAL_PARENT_REQUEST", "key": "BODY"})
        native = write("native-proof", {"schema": "TEST_ONLY_NATIVE_REFERENCE"})
        polygons = [{"id": f"R{i}", "closed": True, "points": points} for i, points in enumerate(rings)]
        vector_data = {"document_kind": "TEST_ONLY_COMPONENT_VECTOR",
                       "target_identity": identity, "actual_parent_only": history,
                       "actual_parent_request": ref(request),
                       "native_function": {"resource": identity + ".PCX", "proof": ref(native)},
                       "reference_geometry_polygons": polygons}
        vector = write("vector", vector_data)
        cw, ch = targets["crop_1x"]
        geometry = {"source_rect_XYWH": rect,
                    "source_view_half_open": [rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]],
                    "K": k, "pad_offset": list(offset), "registered_source_pixels": [cw * k, ch * k],
                    "native_canvas": targets["canvas_1x"], "native_crop": targets["crop_1x"],
                    "native_origin": targets["origin_1x"], "native_world_XY": [0, 0],
                    "render_scales": [3], "candidate_pixels": [cw * 3, ch * 3],
                    "selected_bbox_fit_or_recenter_allowed": False}
        authority_data = {"schema": "h3.art-town.root-component-candidate-input-authority.v1",
                          "frozen_vector": {**ref(vector), "polygons": len(polygons),
                                            "polygon_ids": [p["id"] for p in polygons]},
                          "parent": parent, "art_standard": ref(standard),
                          "approved_shared_parent_geometry_for_candidate": geometry,
                          "execution_authority": {
                              "frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation": True,
                              "formal_Source_intake_or_index_or_deployment_authorized_here": False}}
        authority = write("authority", authority_data)
        item = {"id": label, "key": ["pcx", identity], "source": source, "source_rect": rect,
                "registration": "integer-transparent-padding", "padding_registration": {"common_k": k, "offset": list(offset)},
                "source_layout": "native-crop", "output_layout": "native-crop", "render_scales": [3],
                "foreground_selection": {"schema": "h3.native-review.town-foreground-selection.v1",
                                         "authority": {"path": str(authority), "sha256": sha(authority)}}}
        return item, {"authority": authority, "vector": vector, "standard": standard,
                      "authority_data": authority_data, "vector_data": vector_data}

    @staticmethod
    def town_anchor_png(path, size=(12, 12)):
        anchors = {(0, 0): (80, 120, 200, 1), (1, 1): (200, 100, 50, 128),
                   (2, 1): (20, 80, 140, 64), (4, 0): (90, 150, 210, 255),
                   (5, 1): (40, 90, 170, 1), (0, 3): (33, 44, 55, 1),
                   (10, 10): (12, 45, 90, 255)}
        image = Image.new("RGBA", size)
        for point, pixel in anchors.items():
            image.putpixel(point, pixel)
        image.save(path)
        return anchors

    def test_town_foreground_union_is_one_same_grid_copy_including_alpha1(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-union-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"
            anchors = self.town_anchor_png(source)
            targets = {"canvas_1x": [4, 4], "crop_1x": [4, 4], "origin_1x": [0, 0]}
            rings = [[[0, 0], [4, 0], [0, 4], [0, 0]],
                     [[1, 0], [6, 0], [6, 3], [1, 3], [1, 0]]]
            item, packet = self.town_fixture(root, "UNION", source, targets, rings, rebind=True)
            spec = self.make_spec(root, [item], targets); out = root / "output"
            result = self.render(spec, out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((out / "render-receipt.json").read_bytes()); row = report["items"][0]
            proof = row["foreground_selection_proof"]
            # Hand-selected anchors: diagonal center (0,3) is strictly outside,
            # overlapping (1,1)/(2,1) are copied once, never alpha-composited.
            expected = Image.new("RGBA", (12, 12))
            selected = [(0, 0), (1, 1), (2, 1), (4, 0), (5, 1)]
            for point in selected:
                expected.putpixel(point, anchors[point])
            with Image.open(row["outputs"]["candidate_3x"]["path"]) as image:
                self.assertEqual(image.convert("RGBA").tobytes(), expected.tobytes())
            self.assertEqual(proof["parent_positive_pixels"], 7)
            self.assertEqual(proof["selected_positive_pixels"], 5)
            self.assertEqual(proof["excluded_positive_pixels"], 2)
            self.assertEqual((proof["parent_alpha1_pixels"], proof["selected_alpha1_pixels"], proof["excluded_alpha1_pixels"]), (3, 2, 1))
            self.assertEqual(proof["selected_source_rgba8_sha256"], hashlib.sha256(expected.tobytes()).hexdigest())
            self.assertEqual(proof["sampling_calls"], 1)
            self.assertEqual(proof["intermediate_png_files"], 0)
            self.assertFalse(proof["parent_custody"]["historical_path_is_runtime_dependency"])
            self.assertFalse((root / "not-a-live-generated-parent.png").exists())
            self.assertEqual(len(list(out.rglob("*.png"))), 1)
            self.assertEqual(sha(source), packet["authority_data"]["parent"]["sha256"])

    def test_town_foreground_keeps_parent_registration_without_bbox_fit(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-padding-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"
            anchors = self.town_anchor_png(source)
            targets = {"canvas_1x": [2, 2], "crop_1x": [2, 2], "origin_1x": [0, 0]}
            ring = [[3, 0], [9, 0], [9, 8], [3, 8], [3, 0]]
            item, _ = self.town_fixture(root, "PAD", source, targets, [ring], rect=[3, 0, 9, 10], k=6, offset=(0, 2))
            spec = self.make_spec(root, [item], targets); out = root / "selected"
            result = self.render(spec, out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            row = json.loads((out / "render-receipt.json").read_bytes())["items"][0]
            self.assertFalse(row["source_view_proof"]["all_positive_preserved"])
            proof = row["foreground_selection_proof"]
            self.assertEqual(proof["selected_positive_pixels"], 2)
            self.assertEqual(proof["selected_alpha1_pixels"], 1)
            self.assertEqual(proof["source_rect_xywh"], [3, 0, 9, 10])
            self.assertEqual(proof["offset"], [0, 2])
            self.assertEqual(proof["registered_pixels"], [12, 12])
            self.assertTrue(proof["parent_custody"]["historical_path_is_runtime_dependency"])
            # Independent, hand-built registered input; one original quality
            # sample is the oracle. No polygon rasterizer exists in Python.
            control = Image.new("RGBA", (12, 12))
            for point in [(4, 0), (5, 1)]:
                control.putpixel((point[0] - 3, point[1] + 2), anchors[point])
            control_path = root / "control.png"; control.save(control_path)
            control_spec = self.make_spec(root, [{"id": "CONTROL", "key": ["pcx", "CONTROL"], "source": control_path,
                                                 "render_scales": [3], "output_layout": "native-crop"}], targets)
            control_out = root / "control-output"; control_result = self.render(control_spec, control_out)
            self.assertEqual(control_result.returncode, 0, control_result.stdout + control_result.stderr)
            control_row = json.loads((control_out / "render-receipt.json").read_bytes())["items"][0]
            with Image.open(row["outputs"]["candidate_3x"]["path"]) as actual, Image.open(control_row["outputs"]["candidate_3x"]["path"]) as expected:
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes())
            self.assertEqual(len(list(out.rglob("*.png"))), 1)

    def test_town_foreground_refuses_selected_positive_outside_explicit_roi(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-outside-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"; self.town_anchor_png(source)
            targets = {"canvas_1x": [2, 4], "crop_1x": [2, 4], "origin_1x": [0, 0]}
            item, _ = self.town_fixture(root, "OUTSIDE", source, targets,
                                       [[[0, 0], [4, 0], [0, 4], [0, 0]]], rect=[3, 0, 6, 12])
            spec = self.make_spec(root, [item], targets); out = root / "output"
            result = self.render(spec, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REVIEW_TOWN_SELECTED_POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT", result.stdout + result.stderr)
            self.assertFalse(out.exists())

    def test_town_foreground_refuses_wrong_authority_custody_rings_and_modes(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        changes = ["unapproved", "Source-grant", "missing-rebind", "wrong-parent", "open", "bool", "float",
                   "bowtie", "duplicate", "too-many-points", "wrong-resource", "wrong-grid", "unknown", "multiscale",
                   "float-scale", "missing-scales", "missing-source-layout", "missing-output-layout", "RGB"]
        for number, change in enumerate(changes):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix="town-reject-", dir=OUTPUT_BASE) as temporary:
                root = Path(temporary); source = root / "parent.png"; self.town_anchor_png(source)
                if change == "RGB":
                    with Image.open(source) as image:
                        image.convert("RGB").save(source)
                targets = {"canvas_1x": [4, 4], "crop_1x": [4, 4], "origin_1x": [0, 0]}
                item, packet = self.town_fixture(root, "NEG", source, targets,
                                                [[[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]]], rebind=True)
                a, v = packet["authority_data"], packet["vector_data"]
                points = v["reference_geometry_polygons"][0]["points"]
                if change == "unapproved": a["execution_authority"]["frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation"] = False
                elif change == "Source-grant": a["execution_authority"]["formal_Source_intake_or_index_or_deployment_authorized_here"] = True
                elif change == "missing-rebind": a["parent"].pop("same_exact_original_as_G")
                elif change == "wrong-parent": v["actual_parent_only"]["sha256"] = "0" * 64
                elif change == "open": points[-1] = [1, 0]
                elif change == "bool": points[0][0] = False
                elif change == "float": points[0][0] = 0.0
                elif change == "bowtie": v["reference_geometry_polygons"][0]["points"] = [[0, 0], [6, 6], [0, 6], [6, 0], [0, 0]]
                elif change == "duplicate": points.insert(1, points[0][:])
                elif change == "too-many-points": v["reference_geometry_polygons"][0]["points"] = [[0, 0]] * 258
                elif change == "wrong-resource": v["native_function"]["resource"] = "UNOWNED.PCX"
                elif change == "wrong-grid": a["approved_shared_parent_geometry_for_candidate"]["pad_offset"] = [1, 0]
                elif change == "unknown": item["foreground_selection"]["mask"] = []
                elif change == "multiscale": item["render_scales"] = [1, 3]
                elif change == "float-scale": item["render_scales"] = [3.0]
                elif change == "missing-scales": item.pop("render_scales")
                elif change == "missing-source-layout": item.pop("source_layout")
                elif change == "missing-output-layout": item.pop("output_layout")
                packet["vector"].write_text(json.dumps(v) + "\n", encoding="utf-8")
                a["frozen_vector"].update(sha256=sha(packet["vector"]), bytes=packet["vector"].stat().st_size)
                packet["authority"].write_text(json.dumps(a) + "\n", encoding="utf-8")
                item["foreground_selection"]["authority"]["sha256"] = sha(packet["authority"])
                spec = self.make_spec(root, [item], targets); out = root / "output"
                result = self.render(spec, out)
                self.assertNotEqual(result.returncode, 0, change + result.stdout + result.stderr)
                self.assertFalse(out.exists(), change)

    def test_town_foreground_bound_document_drift_rolls_back_all_owned_outputs(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-currentness-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"; self.town_anchor_png(source, (768, 768))
            targets = {"canvas_1x": [128, 128], "crop_1x": [128, 128], "origin_1x": [0, 0]}
            ring = [[0, 0], [768, 0], [768, 768], [0, 768], [0, 0]]
            items, packets = [], []
            for n in range(4):
                item, packet = self.town_fixture(root, f"DRIFT_{n}", source, targets, [ring], k=6)
                items.append(item); packets.append(packet)
            spec = self.make_spec(root, items, targets); out = root / "output"
            process = subprocess.Popen([str(EXE), "review-render", "--spec", str(spec), "--spec-sha256", sha(spec),
                                        "--outdir", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            deadline = time.monotonic() + 40
            first = out / items[0]["id"] / "candidate-3x.png"
            # Attribute probes only: do not enumerate or hold output handles,
            # which would interfere with the renderer's rename/rollback guard.
            while process.poll() is None and not first.is_file() and time.monotonic() < deadline:
                time.sleep(0.002)
            self.assertTrue(first.is_file(), "first PNG must exist before the deliberate input mutation")
            packets[0]["standard"].write_bytes(b'{"schema":"TEST_ONLY_STANDARD_DRIFT"}\n')
            stdout, stderr = process.communicate(timeout=120)
            self.assertNotEqual(process.returncode, 0, (stdout + stderr).decode("utf-8", errors="replace"))
            self.assertIn(b"final input currentness", stdout + stderr)
            self.assertFalse(out.exists())
            self.assertEqual(sha(source), packets[0]["authority_data"]["parent"]["sha256"])

    def test_town_foreground_work_budget_fails_before_raster_or_outputs(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-budget-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"; self.town_anchor_png(source, (1024, 1024))
            targets = {"canvas_1x": [2, 2], "crop_1x": [2, 2], "origin_1x": [0, 0]}
            ring = [[0, 0], [1024, 0], [1024, 1024], [0, 1024], [0, 0]]
            item, _ = self.town_fixture(root, "BUDGET", source, targets, [copy.deepcopy(ring) for _ in range(32)], k=512)
            spec = self.make_spec(root, [item], targets); out = root / "output"
            result = self.render(spec, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REVIEW_TOWN_SELECTION_WORK_BUDGET", result.stdout + result.stderr)
            self.assertFalse(out.exists())

    def test_town_foreground_aggregate_mask_budget_precedes_source_decode(self):
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town-batch-budget-", dir=OUTPUT_BASE) as temporary:
            root = Path(temporary); source = root / "parent.png"; self.town_anchor_png(source)
            targets = {"canvas_1x": [4, 4], "crop_1x": [4, 4], "origin_1x": [0, 0]}
            ring = [[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]]
            items = [self.town_fixture(root, f"BATCH_{n}", source, targets, [ring])[0] for n in range(5)]
            spec = self.make_spec(root, items, targets)
            doc = json.loads(spec.read_bytes())
            for row in doc["items"]:
                row["source_pixels"] = [4096, 4096]
            source.unlink()
            # This is a declared-budget admission test, not a large allocation
            # benchmark. A missing source proves that no PNG is opened first.
            spec.write_text(json.dumps(doc), encoding="utf-8")
            out = root / "output"
            result = self.render(spec, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REVIEW_TOWN_SELECTION_BATCH_MASK_BUDGET", result.stdout + result.stderr)
            self.assertFalse(out.exists())
            doc["items"].pop()
            spec.write_text(json.dumps(doc), encoding="utf-8")
            result = self.render(spec, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("REVIEW_TOWN_SELECTION_BATCH_MASK_BUDGET", result.stdout + result.stderr)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
