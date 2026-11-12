#!/usr/bin/env python3
"""Contract checks for source-bound creature motion review templates."""
from pathlib import Path
import json
import sys

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
sys.path.insert(0, str(PRODUCT / "tests"))
import art_batch_quality_contract as fixtures
from creature_landmark_annotator import INPUT_SCHEMA
import creature_motion_review as review


def main() -> int:
    fixture = fixtures.BatchQualityContract("test_exact_names_do_not_imply_anatomy_or_art_pass")
    fixture.setUp()
    try:
        records = []
        keys = [["def", "UNIT", 0, frame] for frame in range(3)]
        for key in keys:
            path = fixture.unit / f"0_{key[3]}.png"
            records.append({"path": str(path), "sha256": fixtures.ref(path)["sha256"], "pixels": [96, 96]})
        manifest = {
            "schema": INPUT_SCHEMA,
            "identity": "UNIT",
            "category": "large_creature",
            "masters_dir": str(fixture.unit),
            "native_inventory": fixtures.ref(fixture.inv),
            "native_slots": [{"key": key} for key in keys],
            "source_snapshot": records,
        }
        manifest_path = fixture.root / "scale-report-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        before = {path.name: fixtures.ref(path)["sha256"] for path in fixture.unit.glob("*.png")}
        template = review.make_template(manifest_path)
        assert template["schema"] == "h3.creature.motion-review.v1"
        assert template["identity"] == "UNIT"
        assert template["groups"][0]["ordered_keys"] == [["def", "UNIT", 0, frame] for frame in range(3)]
        assert template["groups"][0]["loop_mode"] == "NOT_MEASURED"
        assert all(value == "NOT_MEASURED"
                   for edge in template["groups"][0]["transitions"]
                   for value in edge["checks"].values())
        after = {path.name: fixtures.ref(path)["sha256"] for path in fixture.unit.glob("*.png")}
        assert before == after
        print("CREATURE_MOTION_REVIEW_TEMPLATE_PASS: official order, hashes, draft status, source immutability")
        return 0
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
