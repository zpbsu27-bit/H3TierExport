"""GPU-free contract tests for the transparent interpolation evidence CLI."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image


PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import companion_runtime
SCRIPT = PRODUCT / "scripts" / "transparent_interpolation_benchmark.py"
spec = importlib.util.spec_from_file_location("transparent_interpolation_benchmark", SCRIPT)
assert spec and spec.loader
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TransparentInterpolationBenchmarkContract(unittest.TestCase):
    def setUp(self) -> None:
        benchmark.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="contract-", dir=benchmark.OUTPUT_ROOT)
        self.root = Path(self.temp.name).resolve()
        self.operation_id = "contract-alpha-r01"
        self.canvas = [48, 40]
        self.roi = [0, 0, 38, 38]
        self.a = self._frame("a", [8, 8], [12, 14], 0)
        self.b = self._frame("b", [10, 8], [12, 14], 2)
        self.truth = self._frame("truth", [9, 8], [12, 14], 1)
        self.candidate_path = self.root / "candidate.png"
        candidate = np.zeros((38, 38, 4), dtype=np.uint8)
        candidate[8:22, 9:21] = np.asarray(Image.open(self.truth["path"]).convert("RGBA"))
        candidate[11, 12, 3] = 0  # Explicitly measure a lost alpha=1 source pixel.
        candidate[11, 11, :3] = (120, 30, 90)
        Image.fromarray(candidate, "RGBA").save(self.candidate_path)
        self.candidate = {"path": str(self.candidate_path), "sha256": sha256(self.candidate_path),
                          "pixels": [38, 38], "origin_pixels": [0, 0],
                          "key": ["def", "TESTCREATURE", 0, 1]}

        self.engine_path = self.root / "engine.bin"
        self.engine_path.write_bytes(b"pinned synthetic engine; not executed")
        self.param_path = self.root / "model.param"
        self.param_path.write_bytes(b"pinned synthetic model graph; not executed")
        self.weight_path = self.root / "model.bin"
        self.weight_path.write_bytes(b"pinned synthetic model weights; not executed")

        self.batch_path = self.root / "producer-input.json"
        self.batch = {"schema": "h3.interpolation.batch.v1", "pairs": [{
            "id": "TESTCREATURE-G0-heldout-F1",
            "a": self.a, "b": self.b, "truth": {**self.truth, "time": 0.5},
        }]}
        write_json(self.batch_path, self.batch)

        self.report_path = self.root / "producer-report.json"
        write_json(self.report_path, {
            "candidate_sha256": self.candidate["sha256"],
            "input_manifest_sha256": sha256(self.batch_path),
            "source_commit": "fixture-commit",
            "executable_sha256": sha256(self.engine_path),
            "model_sha256": {"model.param": sha256(self.param_path),
                             "model.bin": sha256(self.weight_path)},
        })
        self.evidence = {
            "input_manifest": {"path": str(self.batch_path), "sha256": sha256(self.batch_path)},
            "run_report": {"path": str(self.report_path), "sha256": sha256(self.report_path)},
            "candidate_sha256_pointer": "/candidate_sha256",
            "input_manifest_sha256_pointer": "/input_manifest_sha256",
            "backend_identity_pointers": {
                "source_commit": "/source_commit",
                "executable_sha256": "/executable_sha256",
                "model_sha256": {"model.param": "/model_sha256/model.param",
                                 "model.bin": "/model_sha256/model.bin"},
            },
        }
        self.manifest = {
            "schema": "h3.transparent-interpolation-benchmark.v1",
            "operation_id": self.operation_id,
            "cases": [{
                "id": "TESTCREATURE-G0-heldout-F1",
                "canvas_pixels": self.canvas,
                "evaluation_roi": self.roi,
                "requested_time": 0.5,
                "time_semantics": "POSE_ONLY",
                "endpoints": {"a": self.a, "b": self.b},
                "truth": self.truth,
                "semantic_annotations": {
                    "footpoint": {"status": "PRESENT", "evidence": self._annotation_evidence(),
                                  "mask": self._footpoint_mask()},
                    "shadow": {"status": "N/A", "reason": "Fixture has no owner-labelled shadow region"},
                },
                "review_tiers": {
                    "native_1x": {"status": "N/A", "reason": "No formal native-1X art-review receipt is pinned"},
                    "native_3x": {"status": "N/A", "reason": "No real native-3X artifact is available for this fixture"},
                },
                "backends": [
                    {"id": "split-pass", "status": "CANDIDATE_REVIEW_REQUIRED",
                     "requested_time": 0.5, "image": self.candidate,
                     "backend_identity": self._backend_identity(), "producer": self.evidence},
                    {"id": "common-flow", "status": "N/A",
                     "reason": "No same-pair common-flow candidate in this contract fixture"},
                    {"id": "mesh", "status": "N/A",
                     "reason": "The fixture has no same-pair mesh evidence"},
                ],
            }],
        }
        self.manifest_path = self.root / "benchmark.json"
        write_json(self.manifest_path, self.manifest)
        self.manifest_sha = sha256(self.manifest_path)
        self.outdir = benchmark.OUTPUT_ROOT / f"run-{self.root.name}"

    def tearDown(self) -> None:
        if self.outdir.exists():
            shutil.rmtree(self.outdir)
        self.temp.cleanup()

    def _frame(self, label: str, origin: list[int], pixels: list[int], frame: int) -> dict:
        path = self.root / f"{label}.png"
        rgba = np.zeros((pixels[1], pixels[0], 4), dtype=np.uint8)
        rgba[:, :, :3] = (180, 80 + frame * 10, 40)
        rgba[:, :, 0][::2, ::2] = 120
        rgba[:, :, 3] = 0
        rgba[2:10, 2:10, 3] = 192
        rgba[3, 3, 3] = 1
        rgba[4, 4, 3] = 64
        Image.fromarray(rgba, "RGBA").save(path)
        return {"path": str(path), "sha256": sha256(path), "pixels": pixels,
                "origin_pixels": origin, "key": ["def", "TESTCREATURE", 0, frame]}

    def _backend_identity(self) -> dict:
        return {"source_repo": "fixture/engine", "source_commit": "fixture-commit",
                "executable": {"path": str(self.engine_path), "sha256": sha256(self.engine_path)},
                "model_files": {
                    "model.param": {"path": str(self.param_path), "sha256": sha256(self.param_path)},
                    "model.bin": {"path": str(self.weight_path), "sha256": sha256(self.weight_path)},
                }}

    def _annotation_evidence(self) -> dict:
        path = self.root / "annotation.json"
        write_json(path, {"labeler": "fixture", "category": "footpoint"})
        return {"path": str(path), "sha256": sha256(path)}

    def _footpoint_mask(self) -> dict:
        path = self.root / "footpoint.png"
        mask = np.zeros((38, 38), dtype=np.uint8)
        mask[17:20, 10:20] = 255
        Image.fromarray(mask, "L").save(path)
        return {"path": str(path), "sha256": sha256(path), "pixels": [38, 38],
                "origin_pixels": [0, 0]}

    def _rewrite(self, value: dict) -> str:
        write_json(self.manifest_path, value)
        self.manifest_sha = sha256(self.manifest_path)
        return self.manifest_sha

    def test_check_run_verify_reuses_candidate_without_inference_and_reports_open_categories(self) -> None:
        checked = benchmark.check(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)
        self.assertFalse(checked["output_written"])
        self.assertFalse(self.outdir.exists())

        completed = benchmark.run(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)
        self.assertEqual(completed["status"], "COMPLETED_REVIEW_REQUIRED")
        self.assertFalse(completed["model_inference_performed"])
        report = json.loads((self.outdir / benchmark.REPORT_NAME).read_text(encoding="utf-8"))
        page = (self.outdir / benchmark.REVIEW_HTML_NAME).read_text(encoding="utf-8")
        case = report["measurements"]["cases"][0]
        split = next(value for value in case["backends"] if value["id"] == "split-pass")
        self.assertEqual(split["metrics"]["alpha"]["truth_alpha1_pixels"], 1)
        self.assertEqual(split["metrics"]["alpha"]["truth_alpha1_candidate_zero"], 1)
        self.assertIn("semi_transparent", case["coverage"])
        self.assertEqual(case["coverage"]["footpoint"]["status"], "PRESENT_LABELLED")
        self.assertEqual(case["coverage"]["shadow"]["status"], "OPEN_NA")
        self.assertEqual(case["review_tiers"]["native_1x"]["status"], "OPEN_NA")
        self.assertEqual(case["review_tiers"]["native_3x"]["status"], "OPEN_NA")
        self.assertIn("smoke", report["measurements"]["open_semantic_categories"])
        self.assertEqual(report["review_html"]["sha256"], sha256(self.outdir / benchmark.REVIEW_HTML_NAME))
        self.assertIn("留出真值", page)
        self.assertIn("split-pass · 候选", page)
        self.assertIn("<h3>common-flow</h3>", page)
        self.assertIn("黑底", page)
        self.assertIn("棋盘底", page)
        self.assertIn("onion-toggle", page)
        self.assertIn("native_1x", page)
        self.assertNotIn("<video", page.lower())
        self.assertNotIn("setinterval", page.lower())
        self.assertNotIn("transform:scale", page.replace(" ", "").lower())
        verified = benchmark.verify(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)
        self.assertEqual(verified["status"], "VERIFIED_REVIEW_REQUIRED")
        self.assertEqual(verified["review_html_sha256"], report["review_html"]["sha256"])

    def test_stale_candidate_hash_rejects_before_output_creation(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["cases"][0]["backends"][0]["image"]["sha256"] = "0" * 64
        digest = self._rewrite(invalid)
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "SHA256 mismatch"):
            benchmark.run(self.manifest_path, digest, self.outdir, self.operation_id)
        self.assertFalse(self.outdir.exists())

    def test_candidate_cannot_be_heldout_truth_bytes(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["cases"][0]["backends"][0]["image"] = copy.deepcopy(self.truth)
        digest = self._rewrite(invalid)
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "byte-identical to held-out truth"):
            benchmark.check(self.manifest_path, digest, self.outdir, self.operation_id)

    def test_producer_must_bind_all_pinned_inputs_and_backend_hashes(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["cases"][0]["backends"][0]["producer"]["backend_identity_pointers"]["model_sha256"].pop("model.bin")
        digest = self._rewrite(invalid)
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "pin every model file"):
            benchmark.check(self.manifest_path, digest, self.outdir, self.operation_id)

    def test_verify_detects_candidate_bytes_changed_after_run(self) -> None:
        benchmark.run(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)
        with self.candidate_path.open("ab") as output:
            output.write(b"tamper")
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "SHA256 mismatch"):
            benchmark.verify(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)

    def test_verify_detects_review_html_bytes_changed_after_run(self) -> None:
        benchmark.run(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)
        with (self.outdir / benchmark.REVIEW_HTML_NAME).open("ab") as output:
            output.write(b"<!-- tampered -->")
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "review HTML SHA differs"):
            benchmark.verify(self.manifest_path, self.manifest_sha, self.outdir, self.operation_id)

    def test_raw_3x_review_tier_is_linked_without_browser_rescaling(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        native3x_path = self.root / "native3x.png"
        source = np.asarray(Image.open(self.truth["path"]).convert("RGBA"))
        native3x = np.repeat(np.repeat(source, 3, axis=0), 3, axis=1)
        Image.fromarray(native3x, "RGBA").save(native3x_path)
        evidence_path = self.root / "native3x-receipt.json"
        write_json(evidence_path, {"artifact_sha256": sha256(native3x_path), "kind": "raw-3x-fixture"})
        invalid["cases"][0]["review_tiers"]["native_3x"] = {
            "status": "PRESENT",
            "image": {"path": str(native3x_path), "sha256": sha256(native3x_path),
                      "pixels": [36, 42], "origin_pixels": [27, 24],
                      "key": ["def", "TESTCREATURE", 0, 1]},
            "evidence": {"path": str(evidence_path), "sha256": sha256(evidence_path)},
            "artifact_sha256_pointer": "/artifact_sha256",
        }
        digest = self._rewrite(invalid)
        completed = benchmark.run(self.manifest_path, digest, self.outdir, self.operation_id)
        page = (self.outdir / benchmark.REVIEW_HTML_NAME).read_text(encoding="utf-8")
        self.assertIn(native3x_path.resolve().as_uri(), page)
        self.assertIn("width:36px;height:42px", page)
        self.assertIn("left:27px;top:24px", page)
        self.assertIn("canvas" , page)
        self.assertIn("原生 3X · 原始候选，仍需审图", page)
        self.assertEqual(completed["review_html_sha256"], sha256(self.outdir / benchmark.REVIEW_HTML_NAME))
        self.assertEqual(benchmark.verify(self.manifest_path, digest, self.outdir, self.operation_id)["status"],
                         "VERIFIED_REVIEW_REQUIRED")

    def test_output_directory_must_be_directly_inside_experiment_root(self) -> None:
        outside = benchmark.OUTPUT_ROOT.parent / "escape"
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, "direct child"):
            benchmark.check(self.manifest_path, self.manifest_sha, outside, self.operation_id)


class PackagedBenchmarkModuleContract(unittest.TestCase):
    def test_dynamic_adapters_only_load_packaged_sources_without_live_fallback(self):
        with tempfile.TemporaryDirectory(prefix="alpha-packaged-modules-") as temporary:
            root = Path(temporary)
            live = root / "product"
            live.mkdir()
            contents = {"transparent_interpolation_benchmark.py": b"# TEST_ONLY source lookup fixture\n",
                        "interpolate_rife_candidates.py": b"IMPLEMENTATION = 'FROZEN_METRIC'\n",
                        "review_native_batch.py": b"IMPLEMENTATION = 'FROZEN_GUARD'\n"}
            rows = [{"path": name, "source_path": "scripts/" + name, "bytes": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()} for name, data in contents.items()]
            raw = (json.dumps({"schema": "h3.tier-export.companion-runtime.v1",
                               "entrypoints": {"alpha-benchmark": "transparent_interpolation_benchmark.py"},
                               "files": rows}) + "\n").encode()
            payload = root / (".runtime-" + hashlib.sha256(raw).hexdigest())
            payload.mkdir()
            (payload / "runtime-manifest.json").write_bytes(raw)
            for name, data in contents.items():
                (payload / name).write_bytes(data)
            with mock.patch.object(benchmark, "PRODUCT", live), \
                    mock.patch.object(companion_runtime, "frozen_root", return_value=payload):
                before = list(sys.path)
                self.assertEqual(benchmark._load_metric_adapter().IMPLEMENTATION, "FROZEN_METRIC")
                self.assertEqual(benchmark._load_native_review_module().IMPLEMENTATION, "FROZEN_GUARD")
                self.assertEqual(sys.path, before)
                (live / "scripts").mkdir()
                for name in ("interpolate_rife_candidates.py", "review_native_batch.py"):
                    (live / "scripts" / name).write_bytes(b"raise RuntimeError('UNQUALIFIED_LIVE_ADAPTER')\n")
                self.assertEqual(benchmark._load_metric_adapter().IMPLEMENTATION, "FROZEN_METRIC")
                (payload / "interpolate_rife_candidates.py").unlink()
                with self.assertRaises(OSError):
                    benchmark._load_metric_adapter()
                (payload / "interpolate_rife_candidates.py").write_bytes(contents["interpolate_rife_candidates.py"])
                (payload / "runtime-manifest.json").write_bytes(raw + b" ")
                with self.assertRaisesRegex(RuntimeError, "RUNTIME_MANIFEST_DRIFT"):
                    benchmark._load_metric_adapter()
                with self.assertRaisesRegex(RuntimeError, "RUNTIME_MANIFEST_DRIFT"):
                    benchmark._load_native_review_module()


if __name__ == "__main__":
    unittest.main()
