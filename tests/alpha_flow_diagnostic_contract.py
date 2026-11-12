"""Reference-oracle diagnostics are evidence, never a production interpolator."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('existing_benchmark_contract', HERE / 'transparent_interpolation_benchmark_contract.py')
assert spec and spec.loader
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
benchmark = fixture_module.benchmark
sha256 = fixture_module.sha256
write_json = fixture_module.write_json


def rgba(alpha: int, width: int = 2, height: int = 2) -> np.ndarray:
    value = np.zeros((height, width, 4), np.uint8)
    value[:, :, :3] = (150, 75, 25)
    value[:, :, 3] = alpha
    return value


class AlphaFlowMathContract(unittest.TestCase):
    def measure(self, a, b, truth, candidate, flow=None, mask=None):
        h, w = a.shape[:2]
        if flow is None:
            flow = np.zeros((4, h, w), np.float32)
        if mask is None:
            mask = np.full((h, w), 0.5, np.float32)
        return benchmark._alpha_flow_measurements(a, b, truth, candidate, flow, mask)

    def test_blend_only_error_has_zero_fixed_flow_lower_bound(self):
        result = self.measure(rgba(255), rgba(0), rgba(255), rgba(51),
                              mask=np.full((2, 2), 0.2, np.float32))
        self.assertAlmostEqual(result['continuous_alpha_mae_8bit'], 204, places=5)
        self.assertEqual(result['fixed_flow_oracle_lower_bound_mae_8bit'], 0)
        self.assertAlmostEqual(result['blend_weight_excess_mae_8bit'], 204, places=5)
        self.assertTrue(result['uses_heldout_reference'])
        self.assertFalse(result['production_candidate_generated'])
        self.assertFalse(result['art_approved'])

    def test_missing_support_cannot_be_repaired_by_any_blend_mask(self):
        result = self.measure(rgba(0), rgba(0), rgba(255), rgba(0))
        self.assertEqual(result['fixed_flow_oracle_lower_bound_mae_8bit'], 255)
        self.assertEqual(result['blend_weight_excess_mae_8bit'], 0)
        self.assertEqual(result['truth_positive_both_warp_zero'], 4)
        self.assertEqual(result['fixed_flow_lower_bound_error_fraction'], 1)

    def test_alpha_one_and_zero_error_do_not_create_nan_or_clear_alpha(self):
        source = rgba(1)
        before = source.copy()
        result = self.measure(source, source, source, source)
        self.assertEqual(result['candidate_alpha_replay_max_abs_u8'], 0)
        self.assertEqual(result['continuous_alpha_mae_8bit'], 0)
        self.assertIsNone(result['fixed_flow_lower_bound_error_fraction'])
        np.testing.assert_array_equal(source, before)
        json.dumps(result, allow_nan=False)

    def test_border_sampling_clamps_coordinates_before_interpolation(self):
        a = rgba(255)
        flow = np.full((4, 2, 2), -0.75, np.float32)
        result = self.measure(a, a, a, a, flow=flow)
        self.assertEqual(result['candidate_alpha_replay_max_abs_u8'], 0)
        self.assertEqual(result['continuous_alpha_mae_8bit'], 0)

    def test_nonfinite_flow_and_invalid_mask_are_rejected(self):
        for name, value in [('flow', np.nan), ('mask', 1.01), ('mask', np.inf)]:
            with self.subTest(name=name, value=value):
                flow = np.zeros((4, 2, 2), np.float32)
                mask = np.full((2, 2), 0.5, np.float32)
                (flow if name == 'flow' else mask).flat[0] = value
                with self.assertRaises(benchmark.BenchmarkRejected):
                    self.measure(rgba(1), rgba(1), rgba(1), rgba(1), flow, mask)

    def test_wrong_candidate_alpha_is_not_a_valid_replay(self):
        with self.assertRaisesRegex(benchmark.BenchmarkRejected, 'alpha replay'):
            self.measure(rgba(255), rgba(255), rgba(255), rgba(0))


class AlphaFlowEvidenceContract(unittest.TestCase):
    def setUp(self):
        self.fx = fixture_module.TransparentInterpolationBenchmarkContract()
        self.fx.setUp()
        f = self.fx
        self.value = copy.deepcopy(f.manifest)
        case = self.value['cases'][0]
        w, h = case['evaluation_roi'][2:]
        a = benchmark._place_roi(benchmark._load_rgba_frame(case['endpoints']['a'], f.canvas, 'a'), f.roi)
        b = benchmark._place_roi(benchmark._load_rgba_frame(case['endpoints']['b'], f.canvas, 'b'), f.roi)
        candidate = rgba(0, w, h)
        candidate[:, :, 3] = np.floor((a[:, :, 3].astype(np.float64) + b[:, :, 3]) * 0.5 + 0.5).astype(np.uint8)
        Image.fromarray(candidate, 'RGBA').save(f.candidate_path)
        self.flow = f.root / 'flow.f32'
        self.mask = f.root / 'mask.f32'
        np.zeros((4, h, w), dtype='<f4').tofile(self.flow)
        np.full((h, w), 0.5, dtype='<f4').tofile(self.mask)
        report = json.loads(f.report_path.read_text(encoding='utf-8'))
        report.update(candidate_sha256=sha256(f.candidate_path), flow_sha256=sha256(self.flow),
                      mask_sha256=sha256(self.mask), tensor_pixels=[w, h], roi=f.roi,
                      endpoint_rgba_sha256={'a': hashlib.sha256(a.tobytes()).hexdigest(),
                                            'b': hashlib.sha256(b.tobytes()).hexdigest()})
        write_json(f.report_path, report)
        common = copy.deepcopy(case['backends'][0])
        common['id'] = 'common-flow'
        common['image']['sha256'] = sha256(f.candidate_path)
        common['producer']['run_report']['sha256'] = sha256(f.report_path)
        common['alpha_flow_diagnostic'] = {
            'schema': 'h3.alpha-flow-diagnostic.v1',
            'layout': 'planar-f32-le-f0x-f0y-f1x-f1y',
            'sampling': 'backward-bilinear-border', 'padding': 'zero-bottom-right',
            'tensor_pixels': [w, h], 'tensor_pixels_pointer': '/tensor_pixels',
            'roi_pointer': '/roi',
            'endpoint_rgba_sha256_pointers': {'a': '/endpoint_rgba_sha256/a', 'b': '/endpoint_rgba_sha256/b'},
            'flow': {'path': str(self.flow), 'sha256': sha256(self.flow), 'report_sha256_pointer': '/flow_sha256'},
            'mask': {'path': str(self.mask), 'sha256': sha256(self.mask), 'report_sha256_pointer': '/mask_sha256'},
        }
        case['backends'][0] = {'id': 'split-pass', 'status': 'N/A', 'reason': 'Common-flow diagnostic fixture only'}
        case['backends'][1] = common
        self.common = common

    def tearDown(self):
        self.fx.tearDown()

    def test_check_run_verify_and_html_use_same_bound_diagnostic(self):
        f = self.fx
        digest = f._rewrite(self.value)
        benchmark.check(f.manifest_path, digest, f.outdir, f.operation_id)
        self.assertFalse(f.outdir.exists())
        benchmark.run(f.manifest_path, digest, f.outdir, f.operation_id)
        report = json.loads((f.outdir / benchmark.REPORT_NAME).read_text(encoding='utf-8'))
        common = next(x for x in report['measurements']['cases'][0]['backends'] if x['id'] == 'common-flow')
        diagnostic = common['alpha_flow_diagnostic']
        self.assertEqual(diagnostic['candidate_alpha_replay_max_abs_u8'], 0)
        self.assertEqual(diagnostic['flow_sha256'], sha256(self.flow))
        self.assertTrue(diagnostic['uses_heldout_reference'])
        page = (f.outdir / benchmark.REVIEW_HTML_NAME).read_text(encoding='utf-8')
        self.assertIn('oracle-diagnostic', page)
        self.assertIn('not a production candidate', page)
        self.assertEqual(benchmark.verify(f.manifest_path, digest, f.outdir, f.operation_id)['status'], 'VERIFIED_REVIEW_REQUIRED')

    def test_tensor_sha_drift_is_refused_before_output_creation(self):
        f = self.fx
        digest = f._rewrite(self.value)
        self.flow.write_bytes(self.flow.read_bytes() + b'tamper')
        with self.assertRaises(benchmark.BenchmarkRejected):
            benchmark.run(f.manifest_path, digest, f.outdir, f.operation_id)
        self.assertFalse(f.outdir.exists())

    def test_wrong_layout_and_unbound_tensor_and_endpoint_are_rejected(self):
        f = self.fx
        for mutation in ('layout', 'pointer', 'endpoint', 'shape'):
            value = copy.deepcopy(self.value)
            diagnostic = value['cases'][0]['backends'][1]['alpha_flow_diagnostic']
            if mutation == 'layout':
                diagnostic['layout'] = 'interleaved-random'
            elif mutation == 'pointer':
                diagnostic['flow']['report_sha256_pointer'] = '/mask_sha256'
            elif mutation == 'endpoint':
                diagnostic['endpoint_rgba_sha256_pointers']['a'] = '/endpoint_rgba_sha256/b'
            else:
                diagnostic['tensor_pixels'] = [100000, 100000]
            with self.subTest(mutation=mutation):
                digest = f._rewrite(value)
                with self.assertRaises(benchmark.BenchmarkRejected):
                    benchmark.check(f.manifest_path, digest, f.outdir, f.operation_id)
                self.assertFalse(f.outdir.exists())

    def test_verify_rechecks_tensor_bytes_without_replaying_inference(self):
        f = self.fx
        digest = f._rewrite(self.value)
        benchmark.run(f.manifest_path, digest, f.outdir, f.operation_id)
        data = bytearray(self.mask.read_bytes())
        data[0] ^= 1
        self.mask.write_bytes(data)
        with self.assertRaises(benchmark.BenchmarkRejected):
            benchmark.verify(f.manifest_path, digest, f.outdir, f.operation_id)


if __name__ == '__main__':
    unittest.main()
