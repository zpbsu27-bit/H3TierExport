"""Contract tests for pinned creature master-set audit and normalization."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "creature_master_set.py"
sys.path.insert(0, str(PRODUCT / "scripts"))
import creature_master_set as subject


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rgba(path: Path, size: tuple[int, int], box: tuple[int, int, int, int]) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(164, 93, 46, 255))
    image.save(path)


class CreatureMasterSetContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h3-creature-master-set-")
        self.root = Path(self.temp.name)
        self.outdir = PRODUCT / "build" / "experiments" / "creature-master-set" / "fixture-01"
        self.addCleanup(shutil.rmtree, self.outdir, ignore_errors=True)
        self.inventory_path = self.root / "codex-map" / "native-resource-inventory.json"
        self.inventory_path.parent.mkdir(parents=True)
        self.master_root = self.root / "CGNOLL_基础形态" / "CGNOLL"
        self.master_root.mkdir(parents=True)
        self.ref_root = self.root / "OfficialHD2015" / "pak_frames" / "common" / "sprite" / "x3" / "CGNOLL" / "frames"
        self.ref_root.mkdir(parents=True)
        rgba(self.master_root / "0_0.png", (100, 120), (25, 20, 74, 99))
        (self.master_root / "0_1.png").write_bytes((self.master_root / "0_0.png").read_bytes())
        rgba(self.ref_root / "CGNOLL00.png", (30, 36), (8, 6, 22, 29))
        descriptor = self.ref_root.parent / "descriptor.txt"
        descriptor.write_text("CGNOLL00 0 0 0 0 0 0 0 30 36 0 1\n", encoding="ascii")
        rows = []
        for frame in range(2):
            rows.append({
                "category": "creatures", "kind": "def", "identity": "CGNOLL", "native_lod": "H3sprite.lod",
                "group": 0, "frame": frame, "resource": "cgnoll00.pcx", "canvas": [450, 400],
                "crop": [12, 15], "anchor": [170, 200],
                "x3": [f"assets/OfficialHD2015/pak_frames/common/sprite/x3/CGNOLL/frames/CGNOLL00.png"],
            })
        self.inventory_path.write_text(json.dumps({"schema": "fixture", "rows": rows}), encoding="utf-8")
        self.official_manifest = self.root / "OfficialHD2015" / "manifests" / "official-hd-x3-selected-extraction-20261020.json"
        self.official_manifest.parent.mkdir(parents=True)
        official_frame = self.ref_root / "CGNOLL00.png"
        self.official_manifest.write_text(json.dumps({"files": [{
            "path": "pak_frames/common/sprite/x3/CGNOLL/frames/CGNOLL00.png",
            "bytes": official_frame.stat().st_size, "sha256": digest(official_frame),
            "already_present_identical": False,
        }]}), encoding="utf-8")
        self.standard_path = self.root / "town-art-direction-canonical.json"
        self.standard_path.write_text(json.dumps({"version": "fixture-r1"}), encoding="utf-8")
        self.plan_path = self.root / "plan.json"
        args = type("Args", (), {
            "inventory": str(self.inventory_path), "inventory_sha256": digest(self.inventory_path),
            "official_manifest": str(self.official_manifest), "official_manifest_sha256": digest(self.official_manifest),
            "identity": "CGNOLL", "faction": "Fortress", "form": "base",
            "masters_dir": str(self.master_root), "art_standard": str(self.standard_path),
            "art_standard_sha256": digest(self.standard_path), "art_standard_version": "fixture-r1",
            "operation_id": "fixture-01", "created_local": "test", "target_common_k": None,
        })()
        manifest = subject.freeze(args)
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.plan_path.write_bytes(raw)
        self.plan_sha = digest(self.plan_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def invoke(self, *args: str, ok: bool = True) -> dict:
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        payload = result.stderr or result.stdout
        self.assertTrue(payload, f"expected structured failure, got exit {result.returncode}")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise AssertionError(f"expected JSON error report, got: {payload}") from error

    def common_args(self) -> list[str]:
        return ["--manifest", str(self.plan_path), "--manifest-sha256", self.plan_sha,
                "--outdir", str(self.outdir), "--operation-id", "fixture-01"]

    def test_freeze_check_normalize_verify_exact_aliases(self) -> None:
        self.assertEqual(subject.validate_manifest(self.plan_path, self.plan_sha)["expected_logical_slots"], 2)
        check = self.invoke("check", *self.common_args())
        self.assertEqual(check["status"], "READY")
        self.assertEqual(check["unique_frame_classes"], 1)
        self.assertEqual(check["upscales"], 0)
        run = self.invoke("run", *self.common_args())
        self.assertEqual(run["status"], "NORMALIZED_CANDIDATE")
        first = self.outdir / "0_0.png"
        alias = self.outdir / "0_1.png"
        self.assertEqual(first.read_bytes(), alias.read_bytes())
        report = self.invoke("verify", *self.common_args())
        self.assertEqual(report["status"], "VISUAL_REVIEW_REQUIRED")
        self.assertEqual(report["logical_slots_verified"], 2)
        self.assertFalse(report["game_ready"])

    def test_visual_review_must_bind_every_unique_output_and_limbs(self) -> None:
        self.invoke("run", *self.common_args())
        out_sha = digest(self.outdir / "0_0.png")
        review = {"schema": subject.REVIEW_SCHEMA, "identity": "CGNOLL", "reviewer": "assistant",
                  "human_reviewed": False, "items": [{"resource": "cgnoll00.pcx", "output_sha256": out_sha,
                    "single_character": True, "arms": 2, "legs": 2, "tail_present": False,
                    "limb_integrity": "PASS", "visual_status": "assistant_art_pass",
                    "official_action_match": "PASS", "alpha_residue_review": "PASS_NO_VISIBLE_CONTAMINATION",
                    "source_low_alpha_preserved": True}]}
        review["review_sha256"] = "test-review-sha"
        path = self.root / "review.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        result = self.invoke("verify", *self.common_args(), "--limb-review", str(path),
                             "--limb-review-sha256", digest(path))
        self.assertEqual(result["status"], "NORMALIZED_SET_WITH_VISUAL_REVIEW")
        self.assertEqual(result["limb_integrity_review"]["reviewed_frame_classes"], 1)
        review["items"][0]["arms"] = 3
        path.write_text(json.dumps(review), encoding="utf-8")
        error = self.invoke("verify", *self.common_args(), "--limb-review", str(path),
                            "--limb-review-sha256", digest(path), ok=False)
        self.assertEqual(error["status"], "BLOCKED")

    def test_rejects_any_upscale_and_source_drift(self) -> None:
        args = type("Args", (), {
            "inventory": str(self.inventory_path), "inventory_sha256": digest(self.inventory_path),
            "official_manifest": str(self.official_manifest), "official_manifest_sha256": digest(self.official_manifest),
            "identity": "CGNOLL", "faction": "Fortress", "form": "base",
            "masters_dir": str(self.master_root), "art_standard": str(self.standard_path),
            "art_standard_sha256": digest(self.standard_path), "art_standard_version": "fixture-r1",
            "operation_id": "fixture-upscale", "created_local": "test", "target_common_k": 10.0,
        })()
        with self.assertRaisesRegex(subject.SetError, "MUST_NOT_UPSCALE"):
            subject.freeze(args)
        self.invoke("run", *self.common_args())
        source = self.master_root / "0_0.png"
        rgba(source, (100, 120), (20, 20, 80, 100))
        self.invoke("verify", *self.common_args(), ok=False)

    def test_normalization_cannot_write_into_or_overwrite_the_master_directory(self) -> None:
        manifest = subject.validate_manifest(self.plan_path, self.plan_sha)
        before = {p.name: p.read_bytes() for p in self.master_root.glob("*.png")}
        with self.assertRaisesRegex(subject.SetError, "ISOLATED_CANDIDATE"):
            subject.execute_normalize(manifest, self.master_root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.master_root.glob("*.png")})

    def test_normalizer_refuses_prepared_or_formal_three_x_source_directory(self) -> None:
        invalid_masters = self.root / "codex-3X" / "CGNOLL"
        invalid_masters.mkdir(parents=True)
        args = type("Args", (), {
            "inventory": str(self.inventory_path), "inventory_sha256": digest(self.inventory_path),
            "official_manifest": str(self.official_manifest), "official_manifest_sha256": digest(self.official_manifest),
            "identity": "CGNOLL", "faction": "Fortress", "form": "base",
            "masters_dir": str(invalid_masters), "art_standard": str(self.standard_path),
            "art_standard_sha256": digest(self.standard_path), "art_standard_version": "fixture-r1",
            "operation_id": "fixture-3x-source", "created_local": "test", "target_common_k": None,
        })()
        with self.assertRaisesRegex(subject.SetError, "MASTER_SOURCE_IS_PREPARED_OR_FORMAL_3X"):
            subject.freeze(args)


if __name__ == "__main__":
    unittest.main()
