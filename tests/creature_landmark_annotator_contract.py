#!/usr/bin/env python3
"""Focused contract test for the source-bound manual creature landmark UI."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "creature_landmark_annotator.py"
spec = importlib.util.spec_from_file_location("creature_landmark_annotator", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("ANNOTATOR_MODULE_LOAD_FAILED")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main() -> int:
    assert module.INPUT_SCHEMA == "h3.creature.scale-report-manifest.v1"
    result = module.self_test()
    required = {
        "frozen_inventory_and_source_sha",
        "official_key_scope",
        "manual_points_schema",
        "exclusive_output",
        "source_immutability",
        "source_drift_block",
        "common_source_pixel_scale",
        "source_pixel_measurement_without_k_pass",
    }
    if result.get("passed") is not True or not required.issubset(set(result.get("checks", []))):
        raise AssertionError("LANDMARK_ANNOTATOR_CONTRACT_FAILED")
    assert module.parse_key("2_5", "CTROLL") == ("def", "CTROLL", 2, 5)
    try:
        module.parse_key("def_CTROLL_2_5", "CTROLL")
    except module.AnnotationError:
        pass
    else:
        raise AssertionError("NONCANONICAL_KEY_ACCEPTED")
    print(json.dumps({"passed": True, "checks": sorted(required) + ["canonical_key_only"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
