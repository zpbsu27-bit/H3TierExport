"""Regression for batch post-production motion-review completeness checks."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import creature_motion_postcheck as postcheck
from creature_motion_review import CHECK_NAMES


class CreatureMotionPostcheck(unittest.TestCase):
    def setUp(self):
        self.identity = "UNIT"
        self.inventory_sha = "a" * 64
        self.keys = [("def", self.identity, 2, frame) for frame in (0, 1, 2)]
        self.slots = [{"key": list(key)} for key in self.keys]
        self.sources = {key: {"image": {"path": f"{key[2]}_{key[3]}.png", "sha256": str(key[3] + 1) * 64}}
                        for key in self.keys}
        frames = [{"key": list(key), "image_sha256": self.sources[key]["image"]["sha256"]} for key in self.keys]
        self.review = {
            "schema": postcheck.SCHEMA, "identity": self.identity,
            "native_inventory_sha256": self.inventory_sha, "review_author": "qa",
            "groups": [{"group": 2, "loop_mode": "loop", "ordered_keys": [list(key) for key in self.keys],
                "frames": frames,
                "transitions": [self.edge(self.keys[i], self.keys[i + 1]) for i in range(2)],
                "loop_transition": self.edge(self.keys[-1], self.keys[0])}],
        }

    @staticmethod
    def edge(left, right):
        return {"from_key": list(left), "from_image_sha256": str(left[3] + 1) * 64,
            "to_key": list(right), "to_image_sha256": str(right[3] + 1) * 64,
            "checks": {name: "PASS" for name in CHECK_NAMES}, "notes": ""}

    def test_no_review_is_explicitly_unmeasured(self):
        report = postcheck.evaluate(None, self.identity, self.inventory_sha, self.slots, self.sources)
        self.assertEqual(report["status"], "NOT_PROVIDED")
        self.assertFalse(report["automatic_art_approval"])

    def test_complete_review_checks_all_slots_adjacent_edges_and_loop(self):
        report = postcheck.evaluate(self.review, self.identity, self.inventory_sha, self.slots, self.sources)
        self.assertEqual(report["status"], "REVIEW_COMPLETE")
        self.assertEqual(report["summary"]["transitions"], 3)
        self.assertEqual(report["groups"][0]["status"], "REVIEW_COMPLETE")
        self.assertEqual(report["groups"][0]["loop_transition"]["status"], "PASS")

    def test_non_loop_review_does_not_require_or_count_loop_transition(self):
        review = {**self.review, "groups": [dict(self.review["groups"][0])]}
        review["groups"][0]["loop_mode"] = "non_loop"
        review["groups"][0].pop("loop_transition")

        report = postcheck.evaluate(review, self.identity, self.inventory_sha, self.slots, self.sources)

        self.assertEqual(report["status"], "REVIEW_COMPLETE")
        self.assertEqual(report["summary"]["transitions"], 2)
        self.assertEqual(report["groups"][0]["transition_count"], 2)
        self.assertEqual(report["groups"][0]["loop_transition"]["status"], "NOT_APPLICABLE")
        self.assertFalse(report["groups"][0]["loop_transition"]["input_present"])

    def test_non_loop_review_does_not_score_a_stale_loop_transition(self):
        review = {**self.review, "groups": [dict(self.review["groups"][0])]}
        group = review["groups"][0]
        group["loop_mode"] = "non_loop"
        group["loop_transition"] = dict(group["loop_transition"])
        group["loop_transition"]["checks"] = dict(group["loop_transition"]["checks"])
        group["loop_transition"]["checks"]["action_logic"] = "RISK"
        group["loop_transition"]["notes"] = "Stale loop-only review from template"

        report = postcheck.evaluate(review, self.identity, self.inventory_sha, self.slots, self.sources)

        self.assertEqual(report["status"], "REVIEW_COMPLETE")
        self.assertEqual(report["summary"]["transitions"], 2)
        loop_result = report["groups"][0]["loop_transition"]
        self.assertEqual(loop_result["status"], "NOT_APPLICABLE")
        self.assertTrue(loop_result["supplied_but_not_applicable"])
        self.assertEqual(loop_result["supplied_review"]["checks"]["action_logic"], "RISK")

    def test_changed_png_sha_blocks_review_reuse(self):
        changed = {**self.sources, self.keys[1]: {"image": {"path": "2_1.png", "sha256": "f" * 64}}}
        report = postcheck.evaluate(self.review, self.identity, self.inventory_sha, self.slots, changed)
        self.assertEqual(report["status"], "SOURCE_BINDING_INVALID")
        self.assertTrue(any("SOURCE_SHA_MISMATCH" in e for e in report["groups"][0]["errors"]))

    def test_risk_or_failure_requires_notes_and_never_auto_approves(self):
        review = {**self.review, "groups": [dict(self.review["groups"][0])]}
        review["groups"][0]["transitions"] = [dict(edge) for edge in self.review["groups"][0]["transitions"]]
        review["groups"][0]["transitions"][0]["checks"]["action_logic"] = "RISK"
        report = postcheck.evaluate(review, self.identity, self.inventory_sha, self.slots, self.sources)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertIn("RISK_OR_FAIL_REQUIRES_NOTES", report["groups"][0]["errors"][0])
        review["groups"][0]["transitions"][0]["notes"] = "Visible anticipation jump"
        report = postcheck.evaluate(review, self.identity, self.inventory_sha, self.slots, self.sources)
        self.assertEqual(report["status"], "REVIEWED_WITH_RISK")
        self.assertFalse(report["automatic_art_approval"])

    def test_missing_pose_transition_and_loop_classification_stay_incomplete(self):
        review = {**self.review, "groups": [dict(self.review["groups"][0])]}
        review["groups"][0]["loop_mode"] = "NOT_MEASURED"
        review["groups"][0]["transitions"] = review["groups"][0]["transitions"][:1]
        report = postcheck.evaluate(review, self.identity, self.inventory_sha, self.slots, self.sources)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertTrue(any("TRANSITION_COVERAGE_MISMATCH" in e for e in report["groups"][0]["errors"]))
        self.assertTrue(any("LOOP_MODE_NOT_CLASSIFIED" in e for e in report["groups"][0]["errors"]))


if __name__ == "__main__":
    unittest.main()
