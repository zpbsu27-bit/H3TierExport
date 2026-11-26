"""Regression coverage for VCMI Sprites3x drift rejection and reprocessing."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import sprites3x_mod_sync as sync


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def frame(color: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 20, 20), fill=(112, 78, 60, 255))
    draw.rectangle((8, 7, 15, 12), fill=color)
    return image


class Sprites3xModSyncContract(unittest.TestCase):
    def setUp(self) -> None:
        experiments = PRODUCT / "build" / "experiments"
        experiments.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sprites3x-mod-sync-", dir=experiments)
        self.root = Path(self.temp.name)
        self.operation_root = self.root / "sprites3x-mod-contract"
        self.identity = "TESTSPRITE"
        self.old_h3_root = os.environ.get("H3_TIER_EXPORT_H3_ROOT")
        self.asset_root = self.root / "test-h3-assets"
        (self.asset_root / "codex-map").mkdir(parents=True)
        (self.asset_root / "codex-高清母版").mkdir()
        (self.asset_root / "codex-3X").mkdir()
        inventory = {
            "schema": "h3.complete-art-catalog.v3",
            "rows": [
                {"kind": "def", "identity": self.identity, "group": 0, "frame": i,
                 "native_lod": "H3sprite.lod", "resource": f"{self.identity}_{i}.pcx",
                 "canvas": [8, 8], "crop": [8, 8], "anchor": [0, 0]}
                for i in range(2)
            ],
        }
        with gzip.open(self.asset_root / "codex-map" / "native-resource-inventory.json.gz", "wb") as stream:
            stream.write(json.dumps(inventory).encode("utf-8"))
        os.environ["H3_TIER_EXPORT_H3_ROOT"] = str(self.asset_root)

        master_root = self.asset_root / "codex-高清母版" / self.identity
        master_root.mkdir()
        self.expand_root = self.root / "expand"
        (self.expand_root / "receipts").mkdir(parents=True)
        self.source_outputs = []
        items, results = [], []
        for i, color in enumerate(((245, 113, 44, 255), (244, 135, 50, 255))):
            source = master_root / f"0_{i}.png"
            frame(color).save(source)
            output = self.expand_root / self.identity / f"0_{i}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            frame(color).save(output)
            item = {
                "id": f"MASTER_{i + 1}", "source": str(source),
                "source_sha256": digest(source), "source_pixels": [24, 24],
                "base_pixels": [8, 8], "target_scale": 3, "output": str(output),
            }
            items.append(item)
            receipt_path = self.expand_root / "receipts" / f"MASTER_{i + 1}.json"
            save_json(receipt_path, {
                "schema": "h3.master-expand.receipt.v1",
                "contract": {"entry": item, "output_root": str(self.expand_root)},
                "png_sha256": digest(output),
            })
            results.append({"id": item["id"], "status": "VERIFIED", "receipt": str(receipt_path)})
            self.source_outputs.append(output)

        self.batch_path = self.root / "expand-batch.json"
        official_dir = self.root / "official" / self.identity
        official_dir.mkdir(parents=True)
        official_frames = []
        for i in range(2):
            reference = official_dir / f"0_{i}.png"
            Image.new("RGBA", (8, 8), (18 + i, 28, 38, 255)).save(reference)
            official_frames.append({"name": reference.name, "path": str(reference),
                                    "sha256": digest(reference), "pixels": [8, 8]})
        frame_inventory = [{"identity": self.identity, "group": "0",
                            "reference_directory": str(official_dir), "frames": official_frames}]
        save_json(self.batch_path, {
            "schema": "h3.master-expand.batch.v1",
            "animation_contract": "static-structure-dynamic-fx-v2",
            "animation_gate": {"schema": "h3.animation.static-fx.scale-gate.v2",
                               "official_frame_inventory": frame_inventory},
            "output_root": str(self.expand_root), "items": items,
        })
        self.verify_path = self.root / "expand-verify.json"
        save_json(self.verify_path, {
            "schema": "h3.master-expand.run.v1", "status": "COMPLETED",
            "failed": 0, "outputs_written": 0,
            "preflight": {"animation_registration": {
                "contract": "static-structure-dynamic-fx-v2", "gate_version": 2,
                "status": "PASS", "failures": [],
                "official_frame_inventory": frame_inventory}},
            "results": results,
        })

        self.sprites3x_root = self.root / "mod" / "content" / "Sprites3x"
        identity_folder = self.sprites3x_root / self.identity
        identity_folder.mkdir(parents=True)
        descriptor = {
            "basepath": self.identity + "/",
            "images": [
                {"group": 0, "frame": 0, "file": "TESTA_g0_f0.png"},
                {"group": 0, "frame": 1, "file": "TESTB_g0_f1.png"},
            ],
        }
        save_json(self.sprites3x_root / f"{self.identity}.json", descriptor)
        # Mimic a same-canvas frame that was resized/shifted after the verified
        # export. Frame 1 remains the exact formal 3X output.
        good = Image.open(self.source_outputs[0]).convert("RGBA")
        resized = good.resize((22, 24), Image.Resampling.LANCZOS)
        altered = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
        altered.alpha_composite(resized, (1, 0))
        altered.save(identity_folder / "TESTA_g0_f0.png")
        Image.open(self.source_outputs[1]).save(identity_folder / "TESTB_g0_f1.png")

    def tearDown(self) -> None:
        if self.old_h3_root is None:
            os.environ.pop("H3_TIER_EXPORT_H3_ROOT", None)
        else:
            os.environ["H3_TIER_EXPORT_H3_ROOT"] = self.old_h3_root
        self.temp.cleanup()

    def freeze(self) -> dict:
        return sync.freeze(SimpleNamespace(
            identity=self.identity,
            sprites3x_root=self.sprites3x_root,
            batch=self.batch_path,
            batch_sha256=digest(self.batch_path),
            verify_report=self.verify_path,
            verify_report_sha256=digest(self.verify_path),
            operation_id=self.operation_root.name,
            outdir=self.operation_root,
        ))

    def test_frame_drift_blocks_check_then_run_rebuilds_verified_candidate(self) -> None:
        frozen = self.freeze()
        self.assertEqual(frozen["status"], "FROZEN_REPROCESS_REQUIRED")
        self.assertEqual(frozen["mismatch_frames"], 1)
        manifest = Path(frozen["manifest"])

        stdout = io.StringIO()
        with patch("sys.argv", ["sprites3x_mod_sync", "check", "--manifest", str(manifest),
                                 "--manifest-sha256", frozen["manifest_sha256"],
                                 "--operation-id", self.operation_root.name]), contextlib.redirect_stdout(stdout):
            exit_code = sync.main()
        self.assertEqual(exit_code, 2)
        rejected = json.loads(stdout.getvalue())
        self.assertEqual(rejected["status"], "REPROCESS_REQUIRED")
        self.assertEqual(rejected["mismatches"][0]["file"], "TESTA_g0_f0.png")

        request_args = SimpleNamespace(manifest=manifest, manifest_sha256=frozen["manifest_sha256"],
                                       operation_id=self.operation_root.name)
        built = sync.run(request_args)
        self.assertEqual(built["status"], "REPROCESS_CANDIDATE_BUILT")
        self.assertEqual(built["reprocessed_frames"], 1)
        verified = sync.verify(SimpleNamespace(**vars(request_args), receipt=Path(built["receipt"]),
                                               receipt_sha256=built["receipt_sha256"]))
        self.assertEqual(verified["status"], "REPROCESS_CANDIDATE_VERIFIED")

        candidate = Path(built["candidate_root"]) / "content" / "Sprites3x" / self.identity
        self.assertEqual((candidate / "TESTA_g0_f0.png").read_bytes(), self.source_outputs[0].read_bytes())
        self.assertEqual((candidate / "TESTB_g0_f1.png").read_bytes(), self.source_outputs[1].read_bytes())
        # The reprocess operation only creates an isolated candidate.
        self.assertNotEqual((self.sprites3x_root / self.identity / "TESTA_g0_f0.png").read_bytes(),
                            self.source_outputs[0].read_bytes())

    def test_failed_animation_geometry_contract_blocks_freeze(self) -> None:
        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        report["preflight"]["animation_registration"]["status"] = "BLOCKED"
        report["preflight"]["animation_registration"]["failures"] = ["frame 1 scale drift"]
        save_json(self.verify_path, report)
        args = SimpleNamespace(
            identity=self.identity, sprites3x_root=self.sprites3x_root,
            batch=self.batch_path, batch_sha256=digest(self.batch_path),
            verify_report=self.verify_path, verify_report_sha256=digest(self.verify_path),
            operation_id=self.operation_root.name, outdir=self.operation_root,
        )
        with self.assertRaisesRegex(sync.SyncError, "SPRITES3X_ANIMATION_GEOMETRY_NOT_PASSED"):
            sync.freeze(args)
        self.assertFalse(self.operation_root.exists())

    def test_vcmi_base_overlay_gate_is_accepted_when_frozen_and_passed(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-base-dynamic-overlay-v1"
        batch["animation_gate"]["schema"] = "h3.animation.static-base-overlay.scale-gate.v1"
        batch["animation_gate"]["render_semantics"] = "vcmi-base-frame-then-current-frame"
        batch["animation_gate"]["base_frame"] = 0
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-base-dynamic-overlay-v1"
        registration["gate_version"] = 1
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])

    def test_vcmi_base_overlay_v2_gate_is_accepted_when_frozen_and_passed(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-base-dynamic-overlay-v2"
        batch["animation_gate"]["schema"] = "h3.animation.static-base-overlay.scale-gate.v2"
        batch["animation_gate"]["render_semantics"] = "vcmi-base-frame-then-current-frame"
        batch["animation_gate"]["base_frame"] = 0
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-base-dynamic-overlay-v2"
        registration["gate_version"] = 2
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])

    def test_vcmi_base_authored_roi_contract_is_frozen_with_its_mask_sha(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-base-authored-fx-roi-v1"
        batch["animation_gate"]["schema"] = "h3.animation.authored-base-overlay.scale-gate.v1"
        batch["animation_gate"]["render_semantics"] = "vcmi-base-frame-then-current-frame"
        batch["animation_gate"]["base_frame"] = 0
        roi = self.root/"authored-roi.png"
        roi.write_bytes(b"sealed ROI placeholder")
        batch["animation_gate"]["authored_effect_roi"] = {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32], "alpha_threshold": 1,
        }
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-base-authored-fx-roi-v1"
        registration["gate_version"] = 1
        registration["groups"] = [{"authored_effect_roi": {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32],
        }}]
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])


    def test_vcmi_base_authored_roi_v2_contract_is_frozen_with_its_mask_sha(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-base-authored-fx-roi-v2"
        batch["animation_gate"]["schema"] = "h3.animation.authored-base-overlay.scale-gate.v2"
        batch["animation_gate"]["render_semantics"] = "vcmi-base-frame-then-current-frame"
        batch["animation_gate"]["base_frame"] = 0
        roi = self.root/"authored-roi-v2.png"
        roi.write_bytes(b"sealed ROI v2 placeholder")
        batch["animation_gate"]["authored_effect_roi"] = {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32], "alpha_threshold": 1,
        }
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-base-authored-fx-roi-v2"
        registration["gate_version"] = 2
        registration["groups"] = [{"authored_effect_roi": {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32],
        }}]
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])

    def test_full_frame_authored_roi_v2_contract_is_frozen_with_structure_fit(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-composite-authored-fx-roi-v2"
        batch["animation_gate"]["schema"] = "h3.animation.authored-composite.scale-gate.v2"
        batch["animation_gate"]["render_semantics"] = "full-frame-per-frame"
        roi = self.root/"composite-authored-roi-v2.png"
        roi.write_bytes(b"sealed full-frame structure-fit ROI v2 placeholder")
        batch["animation_gate"]["authored_effect_roi"] = {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32], "alpha_threshold": 1,
        }
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-composite-authored-fx-roi-v2"
        registration["gate_version"] = 2
        registration["groups"] = [{"authored_effect_roi": {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32],
        }}]
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])

    def test_full_frame_authored_roi_contract_is_frozen_with_full_frame_semantics(self) -> None:
        batch = json.loads(self.batch_path.read_text(encoding="utf-8"))
        batch["animation_contract"] = "static-composite-authored-fx-roi-v1"
        batch["animation_gate"]["schema"] = "h3.animation.authored-composite.scale-gate.v1"
        batch["animation_gate"]["render_semantics"] = "full-frame-per-frame"
        batch["animation_gate"].pop("base_frame", None)
        roi = self.root/"composite-authored-roi.png"
        roi.write_bytes(b"sealed full-frame ROI placeholder")
        batch["animation_gate"]["authored_effect_roi"] = {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32], "alpha_threshold": 1,
        }
        save_json(self.batch_path, batch)

        report = json.loads(self.verify_path.read_text(encoding="utf-8"))
        registration = report["preflight"]["animation_registration"]
        registration["contract"] = "static-composite-authored-fx-roi-v1"
        registration["gate_version"] = 1
        registration["groups"] = [{"authored_effect_roi": {
            "path": str(roi), "sha256": digest(roi), "pixels": [32, 32],
        }}]
        save_json(self.verify_path, report)

        frozen = self.freeze()
        self.assertEqual("FROZEN_REPROCESS_REQUIRED", frozen["status"])
        self.assertEqual(1, frozen["mismatch_frames"])


if __name__ == "__main__":
    unittest.main()
