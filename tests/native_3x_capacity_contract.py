"""Whole-original-inventory 3X capacity audit contracts, with no image edits."""
from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_native_3x_capacity.py"
spec = importlib.util.spec_from_file_location("audit_native_3x_capacity", SCRIPT)
assert spec and spec.loader
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


class CapacityContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-3x-capacity-")
        self.root = Path(self.temp.name)
        self.masters = self.root / "masters"
        self.masters.mkdir()
        rows = [
            {"kind": "def", "identity": "WIDE", "group": 0, "frame": 0,
             "resource": "wide.pcx", "category": "towns_buildings", "canvas": [500, 200],
             "crop": [400, 100], "anchor": [0, 0]},
            {"kind": "def", "identity": "SMALL", "group": 0, "frame": 1,
             "resource": "small.pcx", "category": "creatures", "canvas": [100, 100],
             "crop": [50, 50], "anchor": [0, 0]},
            {"kind": "pcx", "identity": "WIDEUI", "group": None, "frame": None,
             "resource": "wideui.pcx", "category": "other", "canvas": [800, 600]},
            {"kind": "def", "identity": "NONE", "group": 0, "frame": 0,
             "resource": "none.pcx", "category": "other", "canvas": [64, 64],
             "crop": [64, 64], "anchor": [0, 0]},
        ]
        self.inventory = self.root / "inventory.json.gz"
        self.inventory.write_bytes(gzip.compress(json.dumps({"rows": rows}).encode("utf-8")))
        self.wide = self.masters / "def_WIDE_0_0__proof.png"
        self.small = self.masters / "SMALL-g00-f01-native-master__proof.png"
        self.ui = self.masters / "WIDEUI-continuous-material__proof.png"
        Image.new("RGBA", (1100, 1100), (1, 2, 3, 255)).save(self.wide)
        Image.new("RGBA", (300, 300), (1, 2, 3, 255)).save(self.small)
        Image.new("RGBA", (1700, 2000), (1, 2, 3, 255)).save(self.ui)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_audit(self, name: str = "audit", bindings: Path | None = None,
                  receipt_root: Path | None = None) -> dict:
        return auditor.audit(self.inventory, auditor.sha256(self.inventory), self.masters,
                             self.root / name, bindings, auditor.sha256(bindings) if bindings else None,
                             receipt_root)

    def test_each_axis_and_original_def_crop_vs_pcx_canvas(self) -> None:
        originals = {p: auditor.sha256(p) for p in (self.wide, self.small, self.ui)}
        result = self.run_audit()
        self.assertEqual((result["original_slots"], result["api_6k_rows"]), (4, 2))
        rows = json.loads((self.root / "audit" / "all-original-slots.json").read_text(encoding="utf-8"))
        by_id = {row["key"][1]: row for row in rows}
        self.assertEqual(by_id["WIDE"]["required_3x_pixels"], [1200, 300])
        self.assertEqual(by_id["WIDE"]["sources"][0]["shortfall_pixels"], [100, 0])
        self.assertEqual(by_id["SMALL"]["status"], "POSSIBLE_3X_EFFECTIVE_RECT_UNVERIFIED")
        self.assertEqual(by_id["WIDEUI"]["sources"][0]["shortfall_pixels"], [700, 0])
        self.assertEqual(by_id["NONE"]["status"], "NO_IDENTIFIED_MODEL_SOURCE")
        self.assertEqual({p: auditor.sha256(p) for p in originals}, originals)
        self.assertEqual(len((self.root / "audit" / "api-6k-redraw-list.csv").read_text(encoding="utf-8-sig").splitlines()), 3)

    def test_explicit_effective_pixels_can_prove_shortfall_inside_large_png(self) -> None:
        binding = self.root / "binding.json"
        binding.write_text(json.dumps({"schema": "h3.native3x.source-bindings.v1", "bindings": [
            {"key": ["def", "SMALL", 0, 1], "path": str(self.small),
             "sha256": auditor.sha256(self.small), "effective_pixels": [120, 180]}
        ]}), encoding="utf-8")
        result = self.run_audit("explicit", binding)
        self.assertEqual(result["api_6k_rows"], 3)
        rows = json.loads((self.root / "explicit" / "api-6k-redraw-list.json").read_text(encoding="utf-8"))
        small = next(row for row in rows if row["key"][1] == "SMALL")
        self.assertEqual((small["shortfall_pixels"], small["confidence"]),
                         ([30, 0], "EXPLICIT_CURRENT_SOURCE"))

    def test_unpinned_binding_or_existing_destination_is_rejected(self) -> None:
        binding = self.root / "binding.json"
        binding.write_text('{"schema":"h3.native3x.source-bindings.v1","bindings":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            auditor.audit(self.inventory, auditor.sha256(self.inventory), self.masters,
                          self.root / "bad", binding, "0" * 64)
        self.assertFalse((self.root / "bad").exists())
        self.run_audit()
        with self.assertRaises(FileExistsError):
            self.run_audit()

    def test_shared_frame_range_is_not_assigned_to_first_frame(self) -> None:
        shared = self.masters / "SMALL-g00-f01-04-master__proof.png"
        Image.new("RGBA", (1000, 1000), (1, 2, 3, 255)).save(shared)
        self.run_audit()
        unmatched = json.loads((self.root / "audit" / "unmapped-master-files.json").read_text(encoding="utf-8"))
        self.assertIn(str(shared), unmatched)

    def test_pinned_raw_receipt_can_link_an_unnamed_retained_source(self) -> None:
        shared = self.masters / "shared-phase__proof.png"
        Image.new("RGBA", (100, 100), (1, 2, 3, 255)).save(shared)
        review = self.root / "review" / "map"
        requests = review / "requests"
        receipts = review / "receipts"
        requests.mkdir(parents=True)
        receipts.mkdir()
        request = requests / "shared.json"
        request.write_text(json.dumps({"original_slots": [{"row": {"kind": "def", "identity": "NONE",
                                                            "group": 0, "frame": 0}}]}), encoding="utf-8")
        receipt = receipts / "shared-raw.json"
        receipt.write_text(json.dumps({"schema": "h3.map-ui.raw-intake.v1",
                                       "request": {"path": str(request), "sha256": auditor.sha256(request)},
                                       "raw": {"sha256": auditor.sha256(shared)}}), encoding="utf-8")
        result = self.run_audit("receipt-audit", receipt_root=self.root / "review")
        self.assertEqual(result["api_6k_rows"], 3)
        summary = json.loads((self.root / "receipt-audit" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["receipt_linked_slots"], 1)
        rows = json.loads((self.root / "receipt-audit" / "all-original-slots.json").read_text(encoding="utf-8"))
        none = next(row for row in rows if row["key"][1] == "NONE")
        self.assertEqual(none["sources"][0]["provenance"], "PINNED_RAW_RECEIPT_RETAINED_FILE_CANDIDATE")

    def test_creature_grid_cell_is_upper_bound_not_whole_sheet(self) -> None:
        sheet = self.masters / "creature-group__proof.png"
        Image.new("RGBA", (300, 300), (1, 2, 3, 255)).save(sheet)
        review = self.root / "review" / "creature"
        receipts = review / "receipts"
        receipts.mkdir(parents=True)
        request = review / "group.json"
        request.write_text(json.dumps({"grid": [2, 2], "original_slots": [
            {"key": ["def", "NONE", 0, 0]}]}), encoding="utf-8")
        receipt = receipts / "creature-sequence-NONE-group0.json"
        receipt.write_text(json.dumps({"schema": "h3.creature.sequence.raw.v2",
                                       "raw_sha256": auditor.sha256(sheet), "raw_info": {"pixels": [300, 300]},
                                       "request_path": str(request),
                                       "request_sha256": auditor.sha256(request)}), encoding="utf-8")
        result = self.run_audit("creature-audit", receipt_root=self.root / "review")
        self.assertEqual(result["api_6k_rows"], 3)
        rows = json.loads((self.root / "creature-audit" / "all-original-slots.json").read_text(encoding="utf-8"))
        none = next(row for row in rows if row["key"][1] == "NONE")
        self.assertEqual(none["sources"][0]["measured_pixels"], [150, 150])
        self.assertEqual(none["sources"][0]["capacity_status"], "UNDER_3X_CELL_UPPER_BOUND")


if __name__ == "__main__":
    unittest.main()
