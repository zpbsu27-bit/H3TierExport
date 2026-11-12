"""Contracts for one-frame adventure-object sequence packaging."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import map_object_sequence as subject


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MapObjectSequenceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h3-map-object-sequence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset = self.root / "assets"
        self.inventory = self.asset / "codex-map" / "native-resource-inventory.json.gz"
        self.native_frame = self.asset / "codex-map" / "sources" / "complete" / "frames" / "TESTOBJ" / "0_0.png"
        self.checkpoint = self.asset / "codex-map" / "sources" / "complete" / "checkpoints" / "def_TESTOBJ__fixture.json"
        self.frames = self.root / "3x-candidates"
        self.frames.mkdir(parents=True)
        self.native_frame.parent.mkdir(parents=True)
        self.checkpoint.parent.mkdir(parents=True)
        self.product = self.root / "tool"
        self.operation_id = "testobj-idle-r01"
        self.outdir = self.product / "build" / "experiments" / "map-object-sequence" / self.operation_id
        self.manifest_root = self.product / "build" / "experiments" / "map-object-sequence-requests"

        native = Image.new("RGBA", (4, 3), (0, 0, 0, 0))
        native.putpixel((1, 1), (130, 80, 30, 255))
        native.save(self.native_frame)
        candidate0 = Image.new("RGBA", (12, 9), (0, 0, 0, 0))
        candidate0.putpixel((3, 3), (150, 90, 35, 255))
        candidate0.save(self.frames / "0_0.png")
        candidate1 = Image.new("RGBA", (12, 9), (0, 0, 0, 0))
        candidate1.putpixel((4, 3), (150, 90, 35, 255))
        candidate1.save(self.frames / "0_1.png")

        rows = [{"kind": "def", "category": "adventure_objects", "identity": "TESTOBJ",
                 "group": 0, "frame": 0, "resource": "TestObj.pcx", "native_lod": "H3sprite.lod",
                 "canvas": [4, 3], "crop": [4, 2], "anchor": [0, 1]}]
        with gzip.open(self.inventory, "wt", encoding="utf-8") as stream:
            json.dump({"schema": "h3.complete-art-catalog.v3", "rows": rows}, stream)
        self.checkpoint.write_text(json.dumps({
            "schema": "h3.reference-library.identity-checkpoint.v1",
            "source_payload_sha256": "a" * 64,
            "images": {"def:TESTOBJ:0:0": {
                "path": "sources/complete/frames/TESTOBJ/0_0.png",
                "sha256": digest(self.native_frame), "width": 4, "height": 3,
            }},
        }), encoding="utf-8")

        self.original = {
            "product": subject.PRODUCT,
            "out_root": subject.OUT_ROOT,
            "request_root": subject.REQUEST_ROOT,
            "asset_root": subject.workspace.asset_root,
        }
        subject.PRODUCT = self.product
        subject.OUT_ROOT = self.product / "build" / "experiments" / "map-object-sequence"
        subject.REQUEST_ROOT = self.manifest_root
        subject.workspace.asset_root = lambda: self.asset

    def tearDown(self) -> None:
        subject.PRODUCT = self.original["product"]
        subject.OUT_ROOT = self.original["out_root"]
        subject.REQUEST_ROOT = self.original["request_root"]
        subject.workspace.asset_root = self.original["asset_root"]

    def freeze(self, *, repeat_each: int = 2) -> tuple[Path, str]:
        result = subject.freeze("TESTOBJ", 0, self.frames, self.operation_id, repeat_each)
        return Path(result["manifest"]), result["manifest_sha256"]

    def execute(self, action: str, manifest: Path, manifest_sha: str) -> dict:
        return subject.execute(action, manifest, manifest_sha, self.outdir, self.operation_id)

    def test_freeze_check_run_and_verify_make_matched_sequences_and_static_1x_fallbacks(self) -> None:
        manifest, manifest_sha = self.freeze()
        request = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(request["official_native"]["row"]["official_group_frame_count"], 1)
        self.assertEqual(request["sequence_frame_count"], 4)
        self.assertEqual(request["cycle_duration_ms"], 720)
        self.assertEqual(self.execute("check", manifest, manifest_sha)["status"], "CHECKED_WITHOUT_WRITING")
        run = self.execute("run", manifest, manifest_sha)
        self.assertEqual(run["status"], "CANDIDATE_PACKAGE_CREATED")
        verified = self.execute("verify", manifest, manifest_sha)
        self.assertEqual(verified["status"], "VERIFIED_CANDIDATE_PACKAGE")
        self.assertEqual(verified["verified_file_count"], 11)

        base_descriptor = json.loads((self.outdir / "content" / "Sprites" / "TESTOBJ.json").read_text(encoding="utf-8"))
        hd_descriptor = json.loads((self.outdir / "content" / "Sprites3x" / "TESTOBJ.json").read_text(encoding="utf-8"))
        self.assertEqual(base_descriptor, hd_descriptor)
        names = base_descriptor["sequences"][0]["frames"]
        self.assertEqual(names, [f"TestObj_g0_f{i}.png" for i in range(4)])
        base_files = self.outdir / "content" / "Sprites" / "TESTOBJ"
        hd_files = self.outdir / "content" / "Sprites3x" / "TESTOBJ"
        self.assertTrue(all((base_files / name).read_bytes() == self.native_frame.read_bytes() for name in names))
        self.assertEqual((hd_files / names[0]).read_bytes(), (self.frames / "0_0.png").read_bytes())
        self.assertEqual((hd_files / names[1]).read_bytes(), (self.frames / "0_0.png").read_bytes())
        self.assertEqual((hd_files / names[2]).read_bytes(), (self.frames / "0_1.png").read_bytes())
        self.assertEqual((hd_files / names[3]).read_bytes(), (self.frames / "0_1.png").read_bytes())

    def test_rejects_multi_frame_native_group_and_noncanonical_or_gapped_input(self) -> None:
        document = json.loads(gzip.decompress(self.inventory.read_bytes()))
        extra = dict(document["rows"][0], frame=1)
        document["rows"].append(extra)
        with gzip.open(self.inventory, "wt", encoding="utf-8") as stream:
            json.dump(document, stream)
        with self.assertRaisesRegex(subject.SequenceError, "NATIVE_GROUP_IS_NOT_SINGLE_FRAME"):
            self.freeze()

        document["rows"] = document["rows"][:1]
        with gzip.open(self.inventory, "wt", encoding="utf-8") as stream:
            json.dump(document, stream)
        (self.frames / "0_0.png").rename(self.frames / "0_00.png")
        with self.assertRaisesRegex(subject.SequenceError, "CANONICAL_DECIMAL_INDEX"):
            self.freeze()

    def test_verify_rejects_output_byte_drift(self) -> None:
        manifest, manifest_sha = self.freeze(repeat_each=1)
        self.execute("run", manifest, manifest_sha)
        target = self.outdir / "content" / "Sprites3x" / "TESTOBJ" / "TestObj_g0_f1.png"
        target.write_bytes(target.read_bytes() + b"tamper")
        with self.assertRaisesRegex(subject.SequenceError, "OUTPUT_SHA_MISMATCH"):
            self.execute("verify", manifest, manifest_sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
