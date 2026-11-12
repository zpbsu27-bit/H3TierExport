"""Focused checks for the town flag overlay review and candidate exporter."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "town_flag_review.py"
spec = importlib.util.spec_from_file_location("town_flag_review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class TownFlagReviewContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="town-flag-review-")
        self.root = Path(self.temp.name)
        self.src = self.root / "input"; self.src.mkdir()
        self.body = self.src / "body.png"; self.overlay = self.src / "overlay.png"
        self.native = self.src / "native.png"; self.descriptor = self.src / "AVCTOWX0.json"
        Image.new("RGBA", (576, 576), (40, 80, 20, 255)).save(self.body)
        flags = Image.new("RGBA", (576, 576), (255, 255, 255, 0)); d = ImageDraw.Draw(flags)
        d.rectangle((231, 501, 273, 525), fill=(255, 255, 255, 255))
        d.rectangle((396, 504, 438, 528), fill=(255, 255, 255, 255)); flags.save(self.overlay)
        Image.new("RGBA", (192, 192), (40, 80, 20, 255)).save(self.native)
        self.descriptor.write_text('{}', encoding="utf-8")
        self.row = {"identity": "AVCTOWX0", "flag_split_x": 334, "flag_source": "1999_DEF_palette5"}
        for key, path in (("body", self.body), ("overlay", self.overlay),
                          ("native_reference", self.native), ("descriptor", self.descriptor)):
            self.row[key + "_path"] = str(path); self.row[key + "_sha256"] = review.sha(path)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def layout(self, left=(0, 0), right=(0, 0)) -> Path:
        item = {"identity": self.row["identity"], "body_sha256": self.row["body_sha256"],
                "overlay_sha256": self.row["overlay_sha256"], "left_offset_3x": list(left),
                "right_offset_3x": list(right)}
        path = self.root / "layout.json"
        path.write_text(json.dumps({"schema": review.LAYOUT_SCHEMA, "manifest_sha256": self.manifest_sha,
                                    "items": [item]}), encoding="utf-8")
        return path

    def test_snapshot_preserves_source_bytes_and_reports_review_required(self) -> None:
        out = self.root / "review"
        report = review.build_review(self.manifest, self.manifest_sha, out)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse(report["mod_written"])
        self.assertEqual(review.sha(out / "bodies" / "AVCTOWX0.png"), review.sha(self.body))
        self.assertEqual(review.sha(out / "overlays" / "AVCTOWX0.png"), review.sha(self.overlay))
        self.assertIn("拖左旗", (out / "index.html").read_text(encoding="utf-8"))

    def test_candidate_is_marked_separately_from_live_mod(self) -> None:
        self.row["review_status"] = "CANDIDATE"
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)
        result = review.build_review(self.manifest, self.manifest_sha, self.root / "candidate-review")
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["items"][0]["review_status"], "CANDIDATE")
        html = Path(result["html_review"]).read_text(encoding="utf-8")
        self.assertIn("新候选（未安装）", html)
        self.assertIn("候选尚未自动写入 MOD", html)
        self.assertIn("70% 预览", html)
        self.assertIn("只影响页面中的建筑显示，不改图片", html)

    def test_each_flag_moves_independently_without_losing_alpha(self) -> None:
        layout = self.layout((26, -30), (42, -43))
        out = self.root / "candidate"
        result = review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), out)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["mod_written"])
        with Image.open(self.overlay) as source, Image.open(out / "AVCTOWX0-OVERLAY.png") as candidate:
            source_a = source.getchannel("A"); expected = Image.new("L", (576, 576), 0)
            expected.paste(source_a.crop((0, 0, 334, 576)), (26, -30))
            expected.paste(source_a.crop((334, 0, 576, 576)), (376, -43))
            self.assertIsNone(ImageChops.difference(expected, candidate.getchannel("A")).getbbox())

    def test_changed_source_and_clipped_flag_are_rejected(self) -> None:
        layout = self.layout((-300, 0), (0, 0))
        with self.assertRaisesRegex(ValueError, "FLAG_OUT_OF_BOUNDS"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), self.root / "clipped")
        Image.new("RGBA", (576, 576), (1, 2, 3, 255)).save(self.body)
        with self.assertRaisesRegex(ValueError, "BODY_SHA_DRIFT"):
            review.build_review(self.manifest, self.manifest_sha, self.root / "drift")

    def test_overlapping_flags_and_wrong_layout_binding_are_rejected(self) -> None:
        layout = self.layout((170, 0), (0, 0))
        with self.assertRaisesRegex(ValueError, "FLAG_OVERLAP"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), self.root / "overlap")
        data = json.loads(layout.read_text(encoding="utf-8")); data["manifest_sha256"] = "0" * 64
        layout.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "LAYOUT_MANIFEST_MISMATCH"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), self.root / "wrong")

    def test_body_preview_offset_cannot_be_silently_discarded(self) -> None:
        layout = self.layout((1, 2), (3, 4))
        data = json.loads(layout.read_text(encoding="utf-8"))
        data["items"][0]["body_offset_3x"] = [12, 0]
        layout.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "BODY_SHIFT_REQUIRES_SEPARATE_NATIVE_EXPORT"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), self.root / "shifted")

    def test_explicit_body_translation_preserves_pixels_and_native_bounds(self) -> None:
        body = Image.new("RGBA", (576, 576), (0, 0, 0, 0))
        ImageDraw.Draw(body).rectangle((180, 160, 400, 530), fill=(40, 80, 20, 255))
        body.putpixel((179, 160), (11, 22, 33, 1))
        body.save(self.body)
        native = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
        ImageDraw.Draw(native).rectangle((10, 40, 191, 191), fill=(100, 80, 30, 255))
        native.save(self.native)
        self.row["body_sha256"] = review.sha(self.body)
        self.row["native_reference_sha256"] = review.sha(self.native)
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)
        layout = self.layout((4, -5), (-6, -7))
        data = json.loads(layout.read_text(encoding="utf-8")); data["items"][0]["body_offset_3x"] = [-20, 0]
        layout.write_text(json.dumps(data), encoding="utf-8")
        out = self.root / "body-candidate"
        result = review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), out,
                                        allow_body_translation=True)
        self.assertFalse(result["mod_written"])
        saved = result["items"][0]["body_candidate"]
        self.assertEqual(saved["body_offset_3x"], [-20, 0])
        self.assertFalse(saved["pixel_resampling"])
        self.assertEqual(saved["native_alpha_bbox_3x"], [30, 120, 576, 576])
        expected = Image.new("RGBA", (576, 576), (0, 0, 0, 0)); expected.paste(body, (-20, 0))
        with Image.open(out / "bodies" / "AVCTOWX0" / "0_0.png") as candidate:
            self.assertEqual(candidate.tobytes(), expected.tobytes())
        self.assertEqual(review.sha(self.body), self.row["body_sha256"])

    def test_explicit_body_translation_rejects_native_or_canvas_overflow(self) -> None:
        body = Image.new("RGBA", (576, 576), (0, 0, 0, 0))
        ImageDraw.Draw(body).rectangle((150, 180, 300, 500), fill=(40, 80, 20, 255))
        body.putpixel((150, 180), (20, 30, 40, 1)); body.save(self.body)
        native = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
        ImageDraw.Draw(native).rectangle((50, 40, 191, 191), fill=(100, 80, 30, 255))
        native.save(self.native)
        self.row["body_sha256"] = review.sha(self.body)
        self.row["native_reference_sha256"] = review.sha(self.native)
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)
        layout = self.layout()
        data = json.loads(layout.read_text(encoding="utf-8")); data["items"][0]["body_offset_3x"] = [-1, 0]
        layout.write_text(json.dumps(data), encoding="utf-8")
        out = self.root / "outside-native"
        with self.assertRaisesRegex(ValueError, "BODY_SHIFT_OUTSIDE_NATIVE_ALPHA_BOX"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), out, True)
        self.assertFalse(out.exists())
        data["items"][0]["body_offset_3x"] = [-151, 0]
        layout.write_text(json.dumps(data), encoding="utf-8")
        out = self.root / "clipped-body"
        with self.assertRaisesRegex(ValueError, "BODY_SHIFT_CLIPS_ALPHA"):
            review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), out, True)
        self.assertFalse(out.exists())

    def test_distant_upper_flag_is_fixed_and_legacy_unseparated_manifest_rejected(self) -> None:
        with Image.open(self.overlay) as image:
            image = image.convert("RGBA")
        draw = ImageDraw.Draw(image)
        draw.rectangle((240, 9, 260, 21), fill=(255, 255, 255, 255))
        draw.point((262, 30), fill=(255, 255, 255, 255))
        image.save(self.overlay)
        self.row["overlay_sha256"] = review.sha(self.overlay)
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)
        with self.assertRaisesRegex(ValueError, "ADDITIONAL_OVERLAY_REQUIRES_GATE_REGION"):
            review.build_review(self.manifest, self.manifest_sha, self.root / "unsafe")
        with Image.open(self.overlay) as image:
            boundary = review.extra_overlay_boundary(image.getchannel("A"))
        self.assertGreater(boundary, 30)
        self.assertLess(boundary, 501)
        self.row["gate_flag_y_min_3x"] = boundary
        self.manifest.write_text(json.dumps({"schema": review.SCHEMA, "items": [self.row]}), encoding="utf-8")
        self.manifest_sha = review.sha(self.manifest)
        layout = self.layout((20, -15), (-10, -12))
        out = self.root / "separated"
        review.export_overlays(self.manifest, self.manifest_sha, layout, review.sha(layout), out)
        with Image.open(self.overlay) as source, Image.open(out / "AVCTOWX0-OVERLAY.png") as candidate:
            upper = (0, 0, 576, boundary)
            self.assertIsNone(ImageChops.difference(source.getchannel("A").crop(upper),
                                                   candidate.getchannel("A").crop(upper)).getbbox())
        review_doc = review.build_review(self.manifest, self.manifest_sha, self.root / "safe-review")
        result = json.loads(Path(review_doc["report"]).read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(result["fixed_extra_overlay_bbox_3x"], [240, 9, 263, 31])
        self.assertEqual(result["flag_boxes"][0], [231, 501, 274, 526])

    def test_extra_identity_uses_native_reference_and_mod_without_inventing_master(self) -> None:
        identity = "AVCHFORX"; sprites = self.root / "sprites"; folder = sprites / identity
        folder.mkdir(parents=True); (folder / "frame.png").write_bytes(self.body.read_bytes())
        (folder / "frame-OVERLAY.png").write_bytes(self.overlay.read_bytes())
        (sprites / (identity + ".json")).write_text(json.dumps({"basepath": identity + "/",
            "images": [{"group": 0, "frame": 0, "file": "frame.png"}]}), encoding="utf-8")
        frames = self.root / "native"; (frames / identity).mkdir(parents=True)
        (frames / identity / "0_0.png").write_bytes(self.native.read_bytes())
        defs = self.root / "defs"; defs.mkdir(); (defs / (identity + ".DEF")).write_bytes(b"pinned def")
        table = self.root / "flag-info.txt"; table.write_text("", encoding="utf-8")
        ref = self.root / "reference.json"; ref.write_text(json.dumps({
            "schema": "h3.town-flag-alignment-request.v3", "native_frames_root": str(frames),
            "native_def_root": str(defs), "items": [], "flag_authority": {
                "sprite_flags_info": str(table), "sprite_flags_info_sha256": review.sha(table)}}), encoding="utf-8")
        fake = types.SimpleNamespace(_native_palette5=lambda path: (Image.new("L", (192, 192)), {"mask_sha256": "abc"}),
                                     _read_sprite_flags=lambda path, ids: {})
        with patch.dict(sys.modules, {"town_flag_alignment": fake}):
            result = review.freeze_mod(ref, review.sha(ref), sprites, "extra-r01", self.root / "extra.json", [identity])
        frozen = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual([r["identity"] for r in frozen["items"]], [identity])
        self.assertNotIn("source_relpath", frozen["items"][0])
        self.assertIn("1999 DEF", frozen["items"][0]["flag_source"])

    def test_default_town_mask_is_sha_pinned_and_uses_exact_visitable_tile(self) -> None:
        mask = self.root / "moddables.json"
        mask.write_text('{"town":{"index":98,"base":{"mask":['
                        '"VVVVVV","VVVVVV","VVVVVV","VVBBBV","VBBBBB","VBBABB"]}}}', encoding="utf-8")
        digest = review.sha(mask)
        result = review.read_default_town_mask(mask, digest)
        self.assertEqual(result["bounds_3x"], [288, 480, 384, 576])
        self.assertEqual(result["center_3x"], [336, 528])
        mask.write_text(mask.read_text(encoding="utf-8").replace("VBBABB", "VBBBBB"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "TOWN_MASK_CONFIG_SHA_DRIFT"):
            review.read_default_town_mask(mask, digest)
        with self.assertRaisesRegex(ValueError, "TOWN_MASK_VISIT_TILE_AMBIGUOUS"):
            review.read_default_town_mask(mask, review.sha(mask))

    def test_even_faint_alpha_overlap_is_not_lost_to_rounding(self) -> None:
        left = Image.new("L", (3, 3), 0); right = Image.new("L", (3, 3), 0)
        left.putpixel((1, 1), 1); right.putpixel((1, 1), 1)
        self.assertTrue(review.any_alpha_overlap(left, right))
        right.putpixel((1, 1), 0)
        self.assertFalse(review.any_alpha_overlap(left, right))


if __name__ == "__main__":
    unittest.main(verbosity=2)
