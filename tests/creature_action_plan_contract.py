"""CLI contracts for the native creature action-slot planning companion."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "creature_action_plan.py"


def row(group: int, frame: int, *, identity: str = "TESTBEAST") -> dict:
    return {
        "kind": "def", "category": "creatures", "identity": identity,
        "group": group, "frame": frame, "canvas": [100, 80],
        "crop": [40, 30], "anchor": [12, 9],
    }


class CreatureActionPlanContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="h3-action-plan-")
        self.root = Path(self.temporary.name)
        self.inventory = self.root / "inventory.json.gz"
        self.output = self.root / "action-plan.json"
        self.rows = [row(2, 0), row(0, 0), row(2, 1), row(2, 2)]
        self.write_inventory(self.rows)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_inventory(self, rows: list[dict], *, schema: str = "h3.complete-art-catalog.v3") -> str:
        document = {"schema": schema, "rows": rows}
        with gzip.open(self.inventory, "wt", encoding="utf-8", newline="") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(self.inventory.read_bytes()).hexdigest()

    def invoke(self, *, pinned_sha: str | None = None, output: Path | None = None,
               identities: tuple[str, ...] = ("TESTBEAST",)) -> subprocess.CompletedProcess[str]:
        digest = pinned_sha or hashlib.sha256(self.inventory.read_bytes()).hexdigest()
        args = [sys.executable, str(SCRIPT), "--inventory", str(self.inventory),
                "--inventory-sha256", digest]
        for identity in identities:
            args.extend(("--identity", identity))
        args.extend(("--out", str(output or self.output)))
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20)

    def refusal(self, result: subprocess.CompletedProcess[str], code: str) -> None:
        self.assertEqual(result.returncode, 2, result.stderr)
        error = json.loads(result.stderr)
        self.assertEqual(error["schema"], "h3.creature.action-plan.error.v1")
        self.assertEqual(error["status"], "REFUSED")
        self.assertEqual(error["code"], code)

    def test_variable_frame_counts_missing_groups_and_native_read_order(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(plan["schema"], "h3.creature.action-plan.v1")
        unit = plan["units"][0]
        self.assertEqual(len(unit["action_groups"]), 22)
        self.assertEqual(unit["action_groups"][0]["frame_count"], 1)
        self.assertEqual(unit["action_groups"][2]["frame_count"], 3)
        self.assertEqual([slot["key"]["frame"] for slot in unit["action_groups"][2]["slots"]], [0, 1, 2])
        self.assertEqual([slot["native_order"] for slot in unit["action_groups"][2]["slots"]], [0, 2, 3])
        for group_index in set(range(22)) - {0, 2}:
            group = unit["action_groups"][group_index]
            self.assertTrue(group["absent"])
            self.assertFalse(group["actual_present"])
            self.assertEqual(group["frame_count"], 0)
            self.assertEqual(group["slots"], [])
        self.assertFalse(plan["limits"]["vcmi_extension_groups_added"])

    def test_exact_three_x_targets_and_unresolved_provider_fields(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.output.read_text(encoding="utf-8"))
        slot = plan["units"][0]["action_groups"][2]["slots"][0]
        self.assertEqual(slot["canvas"], [100, 80])
        self.assertEqual(slot["crop"], [40, 30])
        self.assertEqual(slot["anchor"], [12, 9])
        self.assertEqual(slot["target_3x"], {
            "canvas": [300, 240], "crop": [120, 90], "anchor": [36, 27],
        })
        self.assertIsNone(slot["source_binding"])
        self.assertIsNone(slot["keyframe"])
        self.assertIsNone(plan["units"][0]["action_groups"][2]["frame_duration_ms"])
        self.assertIsNone(plan["units"][0]["action_groups"][2]["loop"])

    def test_duplicate_native_key_is_refused_without_output(self) -> None:
        self.write_inventory([row(0, 0), row(0, 0)])
        result = self.invoke()
        self.refusal(result, "NATIVE_KEY_DUPLICATE")
        self.assertFalse(self.output.exists())

    def test_inventory_hash_mismatch_is_refused_without_output(self) -> None:
        result = self.invoke(pinned_sha="0" * 64)
        self.refusal(result, "INVENTORY_SHA_MISMATCH")
        self.assertFalse(self.output.exists())

    def test_existing_output_is_refused_and_bytes_are_preserved(self) -> None:
        original = b"keep existing bytes\n"
        self.output.write_bytes(original)
        result = self.invoke()
        self.refusal(result, "OUTPUT_ALREADY_EXISTS")
        self.assertEqual(self.output.read_bytes(), original)

    def test_bad_geometry_and_wrong_inventory_schema_are_refused(self) -> None:
        malformed = row(0, 0)
        malformed["crop"] = [101, 30]
        self.write_inventory([malformed])
        result = self.invoke()
        self.refusal(result, "GEOMETRY_INVALID")
        self.assertFalse(self.output.exists())

        self.write_inventory([row(0, 0)], schema="h3.unknown.v99")
        result = self.invoke()
        self.refusal(result, "INVENTORY_SCHEMA_UNSUPPORTED")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
