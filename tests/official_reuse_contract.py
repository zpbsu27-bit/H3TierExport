"""Small source-pinned contracts for official 3X reuse triage."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_official_motion_reuse.py"
spec = importlib.util.spec_from_file_location("prepare_official_motion_reuse", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OfficialReuseContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h3-official-reuse-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.official = self.root / "assets" / "OfficialHD2015"
        self.official.mkdir(parents=True)
        self.rows = []

    def add(self, frame: int, size: tuple[int, int], visible: list[tuple[int, int]]) -> None:
        name = f"CEELEM{frame}.png"
        path = self.official / name
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        for x, y in visible:
            image.putpixel((x, y), (110, 80, 55, 1))
        image.save(path)
        self.rows.append({"kind": "def", "identity": "CEELEM", "group": 0, "frame": frame,
                          "resource": name, "category": "creatures", "canvas": [4, 4],
                          "crop": [2, 2], "anchor": [1, 1], "classification": "official_direct",
                          "x3": [f"assets/OfficialHD2015/{name}"]})

    def inventory(self) -> tuple[Path, str]:
        path = self.root / "inventory.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump({"rows": self.rows}, stream)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_unique_lossless_reframe_and_ambiguous_or_visible_overflow_holds(self) -> None:
        self.add(0, (6, 6), [(1, 1)])
        self.add(1, (9, 6), [(8, 2)])  # Only x=3 preserves even alpha=1.
        self.add(2, (9, 6), [(4, 2)])  # Several fully lossless placements.
        self.add(3, (9, 6), [(0, 2), (8, 2)])  # No 6-pixel crop can contain both.
        duplicate = dict(self.rows[0])
        duplicate["frame"] = 4
        self.rows.append(duplicate)
        path, sha = self.inventory()
        result = module.prepare(path, sha, self.root, "CEELEM", self.root / "out")
        self.assertEqual((result["full_slots"], result["candidate_slots"], result["direct_reuse"],
                          result["transparent_reframe"], result["held"], result["alias_slots"]),
                         (5, 3, 2, 1, 2, 1))
        audit = json.loads(Path(result["audit"]).read_text(encoding="utf-8"))
        self.assertEqual([d["status"] for d in audit["decisions"]],
                         ["CANDIDATE_PENDING_ART_REVIEW", "CANDIDATE_PENDING_ART_REVIEW",
                          "HOLD_AMBIGUOUS_TRANSPARENT_PLACEMENT",
                          "HOLD_VISIBLE_ART_OR_SIZE_EXCEEDS_ORIGINAL_CROP",
                          "CANDIDATE_PENDING_ART_REVIEW"])
        batch = json.loads(Path(result["spec"]).read_text(encoding="utf-8"))
        self.assertEqual([item["mode"] for item in batch["items"]], ["reuse", "reframe", "reuse"])
        self.assertEqual(batch["items"][1]["source_rect"], [3, 0, 6, 6])
        self.assertEqual(batch["items"][2]["reuse_from"], batch["items"][0]["id"])
        self.assertNotIn("source", batch["items"][2])
        self.assertFalse((self.root / "out" / "candidates").exists())
        with self.assertRaises(FileExistsError):
            module.prepare(path, sha, self.root, "CEELEM", self.root / "out")

    def test_inventory_revision_mismatch_writes_nothing(self) -> None:
        self.add(0, (6, 6), [(1, 1)])
        path, _ = self.inventory()
        with self.assertRaisesRegex(ValueError, "SOURCE_INVENTORY_REVISION_CHANGED"):
            module.prepare(path, "0" * 64, self.root, "CEELEM", self.root / "out")
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
