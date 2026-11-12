"""Test-only reader binding for the existing native-alias assertions.

The owner cue remains immutable and is never refreshed or admitted by this
fixture. All original alias assertions run against a temporary test document;
the real LOD, native proofs, selected F0 and parent receipts remain unchanged.
"""
from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import shutil
import unittest

PRODUCT = Path(os.environ.get("H3TIEREXPORT_CONTRACT_PRODUCT",
                              Path(__file__).resolve().parents[1])).resolve()
spec = importlib.util.spec_from_file_location(
    "native_alias_original_contract", PRODUCT / "tests/native_alias_contract.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


class NativeAliasFixtureContract(original.NativeAliasContract):
    def setUp(self):
        super().setUp()
        self.owner_cue_path = self.cuepath
        self.owner_cue_bytes = self.cuepath.read_bytes()
        self.owner_cue = copy.deepcopy(self.cue)
        self.addCleanup(self._assert_owner_cue_unchanged)

        # Only fresh test outputs are owned by this fixture. Existing output
        # directories and the historical live integration module are untouched.
        self.operation = "fixture-" + self.operation
        self.output = self.output.with_name(self.operation)
        self.collision = self.output.with_name(self.operation + "-wrong")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.collision.exists())
        self.addCleanup(self._clean_test_outputs)
        self.data["operation_id"] = self.operation

        reader = Path(self.cue["authority_inputs"]["native_reader_source"]["path"])
        self.reader_bytes = reader.read_bytes()
        self.reader_sha = original.sha(reader)
        self.cue["authority_inputs"]["native_reader_source"].update(
            sha256=self.reader_sha, bytes=len(self.reader_bytes))
        self.cue["_tool_test_fixture"] = {
            "purpose": "TEST_ONLY_NOT_OWNER_HANDOFF",
            "owner_cue_sha256": original.CUE_SHA,
            "reader_binding": "current test dependency; no owner approval",
        }
        self.cuepath = self.sandbox / "test-only-native-cue.json"
        original.save_new(self.cuepath, self.cue)
        self.data["native_cue"] = {"path": str(self.cuepath),
                                   "sha256": original.sha(self.cuepath)}
        self.seal()

    def _assert_owner_cue_unchanged(self):
        self.assertEqual(self.owner_cue_path.read_bytes(), self.owner_cue_bytes)

    def _clean_test_outputs(self):
        base = (PRODUCT / "build/experiments/native-alias").resolve()
        for path in (self.output, self.collision):
            self.assertEqual(path.parent.resolve(), base)
            self.assertFalse(path.is_symlink())
            if path.exists():
                shutil.rmtree(path)

    def test_reader_hash_drift_is_rejected_before_output(self):
        cue = copy.deepcopy(self.cue)
        cue["authority_inputs"]["native_reader_source"]["sha256"] = "0" * 64
        altered = self.sandbox / "test-only-stale-reader.json"
        original.save_new(altered, cue)
        self.data["native_cue"] = {"path": str(altered),
                                   "sha256": original.sha(altered)}
        self.seal()
        self.assertIn("REFERENCE_DRIFT:native_reader_source",
                      str(self.invoke("check", False)))
        self.assertFalse(self.output.exists())

    def test_unmodified_owner_cue_keeps_its_own_currentness_result(self):
        self.data["native_cue"] = {"path": str(self.owner_cue_path),
                                   "sha256": original.CUE_SHA}
        self.seal()
        frozen = self.owner_cue["authority_inputs"]["native_reader_source"]
        current = frozen["sha256"] == self.reader_sha
        result = self.invoke("check", current)
        if current:
            self.assertEqual(result["status"], "CANDIDATE_REVIEW_REQUIRED")
        else:
            self.assertIn("REFERENCE_DRIFT:native_reader_source", str(result))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
