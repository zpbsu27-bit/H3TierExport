"""GPU-free contracts for the receipt-bound RIFE candidate adapter."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import numpy as np
from PIL import Image


PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "interpolate_rife_candidates.py"
spec = importlib.util.spec_from_file_location("interpolate_rife_candidates", SCRIPT)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class InterpolateRifeCandidatesContract(unittest.TestCase):
    def setUp(self) -> None:
        parent = PRODUCT / "build" / "tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="rife-candidates-", dir=parent)
        self.root = Path(self.temp.name).resolve()
        self.canvas = [80, 80]
        self.engine_path = self.root / "rife.exe"
        self.engine_path.write_bytes(b"synthetic pinned engine; never executed")
        self.model = self.root / "rife-v4.6"
        self.model.mkdir()
        (self.model / "flownet.bin").write_bytes(b"synthetic model weights")
        (self.model / "flownet.param").write_bytes(b"synthetic model graph")
        self.a_path = self.root / "a.png"
        self.b_path = self.root / "b.png"
        self.a = self._source(self.a_path, [20, 20], [25, 24], "a")
        self.b = self._source(self.b_path, [30, 25], [20, 18], "b")
        self.batch_path = self.root / "batch.json"
        self.outdir = self.root / "out"
        self.calls: list[dict] = []
        self.fake_clock_value = 0.0
        self.fake_clock = lambda: self.fake_clock_value
        self.batch = {
            "schema": "h3.interpolation.batch.v1",
            "engine": {
                "executable": str(self.engine_path),
                "sha256": sha256(self.engine_path),
                "model_dir": str(self.model),
                "model_files": {
                    "flownet.bin": sha256(self.model / "flownet.bin"),
                    "flownet.param": sha256(self.model / "flownet.param"),
                },
                "gpu_id": 0,
            },
            "pairs": [self._pair("sprite", self.a, self.b)],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _source(self, path: Path, origin: list[int], pixels: list[int], side: str) -> dict:
        width, height = pixels
        image = Image.new("RGBA", (width, height), (255, 0, 255, 0))

        def mark(global_x: int, global_y: int, color: tuple[int, int, int, int]) -> None:
            for y in range(global_y - 1, global_y + 2):
                for x in range(global_x - 1, global_x + 2):
                    local_x, local_y = x - origin[0], y - origin[1]
                    if 0 <= local_x < width and 0 <= local_y < height:
                        image.putpixel((local_x, local_y), color)

        if side == "a":
            mark(22, 23, (240, 70, 20, 255))       # A-only content outside the overlap
            mark(35, 30, (180, 110, 30, 255))      # shared world-space landmark
        else:
            mark(35, 30, (180, 110, 30, 255))      # must register to A's landmark
            mark(48, 41, (20, 180, 90, 255))       # B-only content outside the overlap
        image.save(path)
        return {"path": str(path), "sha256": sha256(path), "pixels": pixels,
                "origin_pixels": origin,
                "key": ["synthetic", "SYNTHETIC", 0, 0 if side == "a" else 4]}

    def _pair(self, identity: str, a: dict | None = None, b: dict | None = None) -> dict:
        return {"id": identity, "canvas_pixels": self.canvas,
                "a": copy.deepcopy(a if a is not None else self.a),
                "b": copy.deepcopy(b if b is not None else self.b),
                "times": [0.25, 0.5, 0.75]}

    def _attach_receipt(self, frame: dict, canvas: list[int] | None = None) -> None:
        path = Path(frame["path"]).resolve()
        receipt_path = path.with_suffix(".motion-receipt.json")
        receipt = {
            "schema": "h3.motion.receipt.v1",
            "png_sha256": frame["sha256"],
            "output": str(path),
            "output_pixels": frame["pixels"],
            "origin_3x": frame["origin_pixels"],
            "contract": {"entry": {
                "targets": {"canvas_3x": canvas or self.canvas},
                "key": frame["key"],
            }},
        }
        write_json(receipt_path, receipt)
        frame["motion_receipt"] = str(receipt_path)
        frame["receipt_sha256"] = sha256(receipt_path)

    def _make_def_with_receipt(self, frame: dict) -> None:
        frame["key"][0] = "def"
        self._attach_receipt(frame)

    def _persist(self, batch: dict | None = None) -> str:
        write_json(self.batch_path, self.batch if batch is None else batch)
        return sha256(self.batch_path)

    def _runner(self, engine: dict, inputs: tuple[Path, Path], output: Path,
                t: float, timeout_seconds: float = 120) -> dict:
        self.calls.append({"engine": dict(engine), "inputs": tuple(inputs), "t": t,
                           "timeout_seconds": timeout_seconds,
                           "input_sha256": tuple(sha256(path) for path in inputs)})
        if getattr(self, "fail_time", None) == t:
            raise RuntimeError("synthetic backend failure")
        with Image.open(inputs[0]) as first, Image.open(inputs[1]) as second:
            a = np.asarray(first.convert("RGB"), dtype=np.float32)
            b = np.asarray(second.convert("RGB"), dtype=np.float32)
        result = np.rint(a * (1. - t) + b * t).clip(0, 255).astype(np.uint8)
        Image.fromarray(result, "RGB").save(output)
        return {"synthetic_runner": True, "time": t, "process_started": True,
                "process_completed": True, "exit_code": 0, "status": "COMPLETED",
                "timeout_seconds": timeout_seconds, "output": str(output),
                "output_bytes": output.stat().st_size, "output_sha256": sha256(output)}

    def _execute(self, outdir: Path | None = None, batch: dict | None = None,
                 *, timeout_seconds: int | None = None, runner=None) -> dict:
        digest = self._persist(batch)
        return adapter.execute(self.batch_path, digest, outdir or self.outdir,
                               timeout_seconds=timeout_seconds,
                               runner=runner or self._runner, clock=self.fake_clock)

    def _assert_preflight_rejected(self, batch: dict, reason: str) -> None:
        self.calls.clear()
        destination = self.root / ("rejected-" + str(len(list(self.root.glob("rejected-*")))))
        with self.assertRaisesRegex((ValueError, OSError, KeyError), reason):
            self._execute(destination, batch)
        self.assertEqual(self.calls, [])
        self.assertFalse(destination.exists())
        self.assertTrue(self.a_path.is_file())
        self.assertTrue(self.b_path.is_file())

    def test_union_roi_registers_different_crops_and_preserves_both_outer_edges(self) -> None:
        self._make_def_with_receipt(self.batch["pairs"][0]["a"])
        self._make_def_with_receipt(self.batch["pairs"][0]["b"])
        self.batch["pairs"][0]["times"] = [0.5]
        report = self._execute()
        expected_roi = [4, 4, 62, 56]  # union + 16px, clipped only by the canvas
        a_geometry = {"origin": [20, 20], "pixels": [25, 24]}
        b_geometry = {"origin": [30, 25], "pixels": [20, 18]}
        self.assertEqual(adapter.common_roi(a_geometry, b_geometry, self.canvas), expected_roi)
        result = report["results"][0]
        self.assertEqual(result["roi"], expected_roi)
        self.assertEqual(result["sources"]["a"]["origin_pixels"], [20, 20])
        self.assertEqual(result["sources"]["b"]["origin_pixels"], [30, 25])
        self.assertEqual(result["status"], "CANDIDATE_REVIEW_REQUIRED")
        self.assertEqual(len(self.calls), 2)

        staged_a_rgb, staged_b_rgb = [Image.open(path).convert("RGB") for path in self.calls[0]["inputs"]]
        self.assertEqual(staged_a_rgb.size, (62, 56))
        self.assertEqual(staged_b_rgb.size, (62, 56))
        staged_a_alpha = Image.open(self.outdir / "sprite" / "a-alpha.png").convert("RGB")
        staged_b_alpha = Image.open(self.outdir / "sprite" / "b-alpha.png").convert("RGB")
        # The same global landmark is at (35-4, 30-4) in both inputs: no crop-local drift.
        self.assertEqual(staged_a_alpha.getpixel((31, 26)), (255, 255, 255))
        self.assertEqual(staged_b_alpha.getpixel((31, 26)), (255, 255, 255))
        # Endpoints outside their intersection survive the union ROI.
        self.assertEqual(staged_a_alpha.getpixel((18, 19)), (255, 255, 255))
        self.assertEqual(staged_b_alpha.getpixel((44, 37)), (255, 255, 255))
        # Hidden magenta RGB in transparent source pixels is removed before RGB inference.
        self.assertEqual(staged_a_rgb.getpixel((0, 0)), (0, 0, 0))
        with Image.open(result["output"]) as candidate:
            self.assertEqual(candidate.size, (62, 56))
            self.assertGreater(candidate.getpixel((18, 19))[3], 0)
            self.assertEqual(candidate.getpixel((31, 26))[3], 255)
            self.assertGreater(candidate.getpixel((44, 37))[3], 0)
        for flag in ("art_approved", "human_approved", "native_timing_verified",
                     "runtime_accepted", "game_deployed", "model_native_output"):
            self.assertIs(result[flag], False)

    def test_linear_premultiplied_recombine_preserves_alpha_one_and_removes_hidden_rgb(self) -> None:
        rgba = np.array([[[255, 0, 255, 0], [200, 100, 50, 128]]], dtype=np.uint8)
        rgb, alpha = adapter.split_inputs(rgba)
        self.assertEqual(tuple(rgb[0, 0]), (0, 0, 0))
        self.assertEqual(tuple(alpha[0, 0]), (0, 0, 0))
        self.assertEqual(tuple(alpha[0, 1]), (128, 128, 128))

        # These are sRGB encodings of linear premultiplied (.125, .0625, .01),
        # with alpha .25. Correct unpremultiplication returns sRGB (.735, .537, .220).
        black_rgb = np.array([[[99, 71, 25], [1, 1, 1], [0, 0, 0]]], dtype=np.uint8)
        alpha_rgb = np.array([[[64, 64, 64], [1, 1, 1], [0, 0, 0]]], dtype=np.uint8)
        result, diagnostics = adapter.recombine(black_rgb, alpha_rgb)
        self.assertLessEqual(max(abs(int(result[0, 0, channel]) - expected)
                                 for channel, expected in enumerate((188, 137, 56))), 1)
        self.assertEqual(int(result[0, 0, 3]), 64)
        self.assertEqual(tuple(result[0, 1])[3], 1)
        self.assertEqual(tuple(result[0, 2]), (0, 0, 0, 0))
        self.assertEqual(diagnostics["alpha1_pixels_preserved"], 1)

    def test_any_source_hash_failure_preflights_whole_batch_before_execution(self) -> None:
        invalid = copy.deepcopy(self.batch)
        invalid["pairs"].append(self._pair("invalid-later"))
        invalid["pairs"][1]["b"]["sha256"] = "0" * 64
        self._assert_preflight_rejected(invalid, "SHA256 mismatch")

    def test_missing_or_illegal_original_slot_keys_are_preflight_rejected(self) -> None:
        mutations = {
            "missing": lambda key: None,
            "unknown-kind": lambda key: ["unknown", "SYNTHETIC", 0, 0],
            "empty-resource": lambda key: ["def", "", 0, 0],
            "boolean-group": lambda key: ["def", "SYNTHETIC", True, 0],
            "negative-frame": lambda key: ["def", "SYNTHETIC", 0, -1],
        }
        for name, change in mutations.items():
            with self.subTest(name=name):
                invalid = copy.deepcopy(self.batch)
                endpoint = invalid["pairs"][0]["a"]
                replacement = change(endpoint["key"])
                if replacement is None:
                    endpoint.pop("key")
                else:
                    endpoint["key"] = replacement
                self._assert_preflight_rejected(invalid, r"\.key: original slot identity required")

    def test_endpoints_and_heldout_truth_cannot_cross_resource_or_action_groups(self) -> None:
        endpoint_mismatch = copy.deepcopy(self.batch)
        endpoint_mismatch["pairs"][0]["b"]["key"] = ["synthetic", "SYNTHETIC", 1, 4]
        self._assert_preflight_rejected(endpoint_mismatch, "same original resource/action group")

        truth_path = self.root / "cross-action-truth.png"
        Image.new("RGBA", (4, 4), (30, 80, 120, 255)).save(truth_path)
        truth_mismatch = copy.deepcopy(self.batch)
        truth_mismatch["pairs"][0]["truth"] = {
            "path": str(truth_path), "sha256": sha256(truth_path),
            "pixels": [4, 4], "origin_pixels": [30, 30],
            "key": ["synthetic", "SYNTHETIC", 2, 2], "time": 0.5,
        }
        self._assert_preflight_rejected(truth_mismatch, "truth must belong to the same original resource/action group")

        endpoint_truth = copy.deepcopy(self.batch)
        endpoint_truth["pairs"][0]["truth"] = {
            "path": str(truth_path), "sha256": sha256(truth_path),
            "pixels": [4, 4], "origin_pixels": [30, 30],
            "key": copy.deepcopy(endpoint_truth["pairs"][0]["a"]["key"]), "time": 0.5,
        }
        self._assert_preflight_rejected(endpoint_truth, "truth must be a distinct held-out frame")

    def test_receipt_hash_and_geometry_mismatch_are_preflight_rejected(self) -> None:
        invalid_hash = copy.deepcopy(self.batch)
        self._make_def_with_receipt(invalid_hash["pairs"][0]["a"])
        receipt_path = Path(invalid_hash["pairs"][0]["a"]["motion_receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["origin_3x"] = [21, 20]
        write_json(receipt_path, receipt)
        self._assert_preflight_rejected(invalid_hash, "SHA256 mismatch")

        invalid_geometry = copy.deepcopy(self.batch)
        self._make_def_with_receipt(invalid_geometry["pairs"][0]["a"])
        receipt_path = Path(invalid_geometry["pairs"][0]["a"]["motion_receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["origin_3x"] = [21, 20]
        write_json(receipt_path, receipt)
        invalid_geometry["pairs"][0]["a"]["receipt_sha256"] = sha256(receipt_path)
        self._assert_preflight_rejected(invalid_geometry, "motion receipt geometry/key mismatch")

    def test_malformed_motion_receipt_objects_are_structured_preflight_rejections(self) -> None:
        cases = (
            (None, "motion receipt contract must be an object"),
            ({"entry": None}, "motion receipt entry must be an object"),
            ({"entry": {"targets": None}}, "motion receipt targets must be an object"),
        )
        for index, (contract, message) in enumerate(cases):
            with self.subTest(index=index):
                invalid = copy.deepcopy(self.batch)
                endpoint = invalid["pairs"][0]["a"]
                self._make_def_with_receipt(endpoint)
                receipt_path = Path(endpoint["motion_receipt"])
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt["contract"] = contract
                if isinstance(contract, dict) and isinstance(contract.get("entry"), dict):
                    receipt["contract"]["entry"]["targets"] = None
                write_json(receipt_path, receipt)
                endpoint["receipt_sha256"] = sha256(receipt_path)
                self._assert_preflight_rejected(invalid, message)

    def test_cli_reports_malformed_receipt_as_json_preflight_rejection(self) -> None:
        invalid = copy.deepcopy(self.batch)
        endpoint = invalid["pairs"][0]["a"]
        self._make_def_with_receipt(endpoint)
        receipt_path = Path(endpoint["motion_receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["contract"] = None
        write_json(receipt_path, receipt)
        endpoint["receipt_sha256"] = sha256(receipt_path)
        digest = self._persist(invalid)
        destination = self.root / "malformed-receipt-cli"
        args = ["interpolate_rife_candidates.py", "--batch", str(self.batch_path),
                "--batch-sha256", digest, "--outdir", str(destination)]
        stdout = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(stdout):
            exit_code = adapter.main()
        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "PREFLIGHT_REJECTED")
        self.assertIn("motion receipt contract must be an object", result["error"])
        self.assertFalse(destination.exists())

    def test_manifest_batch_timeout_caps_explicit_override(self) -> None:
        limited = copy.deepcopy(self.batch)
        limited["max_wall_seconds"] = 3
        self.calls.clear()
        destination = self.root / "timeout-cap-rejected"
        with self.assertRaisesRegex(adapter.PreflightRejected, "exceeds manifest max_wall_seconds"):
            self._execute(destination, limited, timeout_seconds=4)
        self.assertFalse(destination.exists())
        self.assertEqual(self.calls, [])

        limited["max_wall_seconds"] = 2
        report = self._execute(self.root / "timeout-default-capped", limited)
        self.assertEqual(report["batch_timeout_seconds"], 2)
        self.assertEqual(report["manifest_max_wall_seconds"], 2)
        self.assertTrue(all(call["timeout_seconds"] <= 2 for call in self.calls))

    def test_completed_rgb_call_evidence_survives_alpha_process_failure(self) -> None:
        self.batch["pairs"][0]["times"] = [0.5]

        def fail_alpha(engine, inputs, output, t, timeout_seconds=120):
            if "-alpha" in inputs[0].stem:
                self.calls.append({"channel": "alpha", "t": t,
                                   "timeout_seconds": timeout_seconds})
                raise adapter.InferenceFailure(
                    "synthetic alpha backend exit",
                    {"process_started": True, "process_completed": True,
                     "exit_code": 17, "status": "EXIT_NONZERO",
                     "timed_out": False, "timeout_seconds": timeout_seconds,
                     "stderr_tail": "alpha failed"},
                )
            return self._runner(engine, inputs, output, t, timeout_seconds)

        report = self._execute(runner=fail_alpha)
        result = report["results"][0]
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(result["status"], "BACKEND_FAILED")
        self.assertTrue(report["model_inference_performed"])
        self.assertEqual(len(result["inferences"]), 2)
        self.assertEqual(result["inferences"][0]["channel"], "rgb")
        self.assertTrue(result["inferences"][0]["process_started"])
        self.assertTrue(result["inferences"][0]["process_completed"])
        self.assertEqual(result["inferences"][0]["exit_code"], 0)
        self.assertEqual(result["inferences"][1]["channel"], "alpha")
        self.assertEqual(result["inferences"][1]["exit_code"], 17)
        self.assertIn("alpha failed", result["inferences"][1]["stderr_tail"])

    def test_total_batch_timeout_preserves_started_call_evidence_and_marks_unstarted_samples(self) -> None:
        self.batch["pairs"][0]["times"] = [0.25, 0.5, 0.75]
        self.fake_clock_value = 0.0

        def bounded_runner(engine, inputs, output, t, timeout_seconds=120):
            channel = "alpha" if "-alpha" in inputs[0].stem else "rgb"
            self.calls.append({"t": t, "channel": channel, "timeout_seconds": timeout_seconds})
            if timeout_seconds < 0.81:
                self.fake_clock_value += timeout_seconds
                raise adapter.InferenceFailure(
                    "synthetic batch timeout",
                    {"process_started": True, "process_completed": False,
                     "timed_out": True, "status": "TIMED_OUT",
                     "timeout_seconds": timeout_seconds},
                )
            self.fake_clock_value += 0.6
            with Image.open(inputs[0]) as first:
                Image.fromarray(np.asarray(first.convert("RGB"))).save(output)
            return {"process_started": True, "process_completed": True,
                    "exit_code": 0, "status": "COMPLETED",
                    "timeout_seconds": timeout_seconds,
                    "output": str(output), "output_bytes": output.stat().st_size,
                    "output_sha256": sha256(output)}

        report = self._execute(timeout_seconds=2, runner=bounded_runner)
        self.assertEqual(report["status"], "PARTIAL")
        self.assertTrue(report["batch_timed_out"])
        self.assertEqual([row["status"] for row in report["results"]],
                         ["CANDIDATE_REVIEW_REQUIRED", "BATCH_TIMEOUT", "NOT_STARTED_BATCH_TIMEOUT"])
        self.assertTrue(report["model_inference_performed"])
        self.assertEqual(len(report["results"][1]["inferences"]), 1)
        self.assertTrue(report["results"][1]["inferences"][0]["timed_out"])
        self.assertEqual(report["results"][2]["inferences"], [])
        self.assertEqual([call["channel"] for call in self.calls], ["rgb", "alpha", "rgb"])

    def test_original_h3_slot_requires_its_motion_receipt(self) -> None:
        invalid = copy.deepcopy(self.batch)
        invalid["pairs"][0]["a"]["key"][0] = "def"
        self._assert_preflight_rejected(invalid, "original H3 slot requires a pinned motion receipt")

    def test_engine_and_both_model_files_are_sha_pinned(self) -> None:
        for target in ("engine", "flownet.bin", "flownet.param"):
            with self.subTest(target=target):
                invalid = copy.deepcopy(self.batch)
                if target == "engine":
                    invalid["engine"]["sha256"] = "0" * 64
                else:
                    invalid["engine"]["model_files"][target] = "0" * 64
                self._assert_preflight_rejected(invalid, "SHA256 mismatch")

    def test_existing_output_or_source_path_is_never_replaced(self) -> None:
        source_before = self.a_path.read_bytes()
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "outdir already exists"):
            self._execute(self.root)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.a_path.read_bytes(), source_before)

        occupied = self.root / "occupied"
        occupied.mkdir()
        marker = occupied / "keep.bin"
        marker.write_bytes(b"existing output must survive")
        with self.assertRaisesRegex(ValueError, "outdir already exists"):
            self._execute(occupied)
        self.assertEqual(self.calls, [])
        self.assertEqual(marker.read_bytes(), b"existing output must survive")
        self.assertEqual(self.a_path.read_bytes(), source_before)

    def test_backend_sample_failure_isolated_and_reported_as_partial(self) -> None:
        self.fail_time = 0.5
        report = self._execute()
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual((report["candidates"], report["failed"]), (2, 1))
        self.assertEqual([row["status"] for row in report["results"]],
                         ["CANDIDATE_REVIEW_REQUIRED", "BACKEND_FAILED", "CANDIDATE_REVIEW_REQUIRED"])
        self.assertTrue(Path(report["results"][0]["output"]).is_file())
        self.assertFalse((self.outdir / "sprite" / "t01-candidate.png").exists())
        self.assertTrue(Path(report["results"][2]["output"]).is_file())
        self.assertTrue((self.outdir / "sprite" / "t01-receipt.json").is_file())
        self.assertEqual(len(self.calls), 5)  # failed first channel stops only that sample
        self.assertTrue(report["model_inference_performed"])
        self.assertTrue(report["model_inference_start_unknown"])
        for row in report["results"]:
            for flag in ("art_approved", "native_timing_verified", "runtime_accepted"):
                self.assertIs(row[flag], False)

    def test_truth_is_held_out_for_metrics_and_never_enters_inference(self) -> None:
        truth_path = self.root / "truth.png"
        truth = Image.new("RGBA", (12, 10), (0, 255, 255, 255))
        truth.save(truth_path)
        pair = self.batch["pairs"][0]
        pair["truth"] = {"path": str(truth_path), "sha256": sha256(truth_path),
                          "pixels": [12, 10], "origin_pixels": [28, 29],
                          "key": ["synthetic", "SYNTHETIC", 0, 2], "time": 0.5}
        first_report = self._execute(self.root / "truth-run-1")
        first_inputs = [row["input_sha256"] for row in self.calls]
        first_candidate_hash = first_report["results"][1]["png_sha256"]
        self.assertTrue(first_report["results"][1]["heldout_pose_metrics"]["rife"]["truth_is_heldout_pose_not_proof_of_linear_timing"])
        self.assertTrue(all(str(truth_path) not in str(path) for call in self.calls for path in call["inputs"]))

        self.calls.clear()
        changed = Image.new("RGBA", (12, 10), (255, 255, 0, 255))
        changed.save(truth_path)
        pair["truth"]["sha256"] = sha256(truth_path)
        second_report = self._execute(self.root / "truth-run-2")
        second_inputs = [row["input_sha256"] for row in self.calls]
        second_candidate_hash = second_report["results"][1]["png_sha256"]
        self.assertEqual(first_inputs, second_inputs)
        self.assertEqual(first_candidate_hash, second_candidate_hash)

    def test_post_inference_report_write_failure_is_run_failed_with_partial_artifact_evidence(self) -> None:
        self.batch["pairs"][0]["times"] = [0.5]
        digest = self._persist()
        destination = self.outdir
        args = ["interpolate_rife_candidates.py", "--batch", str(self.batch_path),
                "--batch-sha256", digest, "--outdir", str(destination)]
        original_execute = adapter.execute
        original_write_json = adapter.write_json

        def execute_with_synthetic_runner(batch_path: Path, batch_sha256: str,
                                           outdir: Path, **kwargs) -> dict:
            return original_execute(batch_path, batch_sha256, outdir,
                                    runner=self._runner, **kwargs)

        def fail_only_run_report(path: Path, value: dict) -> None:
            if Path(path).name == "report.json":
                raise OSError("synthetic report disk failure")
            original_write_json(path, value)

        stdout = io.StringIO()
        with patch.object(adapter, "execute", side_effect=execute_with_synthetic_runner), \
                patch.object(adapter, "write_json", side_effect=fail_only_run_report), \
                patch.object(sys, "argv", args), redirect_stdout(stdout):
            exit_code = adapter.main()

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "RUN_FAILED")
        self.assertTrue(result["partial_artifacts_may_exist"])
        self.assertTrue(result["retry_requires_inspecting_existing_artifacts"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue((destination / "sprite" / "t00-candidate.png").is_file())
        self.assertTrue((destination / "sprite" / "t00-receipt.json").is_file())
        self.assertFalse((destination / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
