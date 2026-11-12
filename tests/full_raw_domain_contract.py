"""Real EXE contracts for full raw sampling; temporary synthetic inputs only."""
from pathlib import Path
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('H3TIEREXPORT_EXE', ROOT / 'build/candidate/H3TierExport.exe'))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')

def ref(path):
    return {'path': str(path), 'sha256': sha(path)}

class FullRawDomainContract(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'build/tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='fullraw-', dir=parent)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source.png'
        im = Image.new('RGBA', (12, 13), (255, 20, 0, 0))
        for y in range(5, 11):
            for x in range(5, 11):
                im.putpixel((x, y), (90, 160, 240, 180))
        im.putpixel((5, 5), (110, 170, 245, 1))
        im.save(self.source)
        self.geometry = {'canvas_1x': [8, 7], 'crop_1x': [4, 4], 'origin_1x': [1, 1]}
        self.key = ['def', 'TESTFULL', 1, 0]
        self.projection = self.root / 'native-projection.json'
        self.native = self.root / 'native-contract.json'
        self.lineage = self.root / 'caller-placement.json'
        save(self.lineage, {'kind': 'synthetic caller-frozen placement; not approved art'})
        self.spec_path = self.root / 'fullraw-spec.json'
        self.batch_path = self.root / 'batch.json'
        self.output = self.root / 'output'
        self.spec = {'schema': 'h3.master-expand.fullraw-spec.v1', 'target_scale': 3,
                     'native_contracts': [], 'items': [{'id': 'ONE', 'key': self.key,
                     'source': {}, 'natural_k': 5, 'translation': [4, 4],
                     'lineage': [ref(self.lineage)]}]}
        self.refresh()

    def tearDown(self):
        self.tmp.cleanup()

    def refresh(self):
        save(self.projection, {'frames': [{'key': self.key, 'targets': self.geometry,
              'source_equivalence_class': 'a' * 64}]})
        save(self.native, {'projection_source': ref(self.projection), 'entries': [
             {'key': self.key, 'targets': self.geometry, 'equivalence_class': 'a' * 64}]})
        self.spec['native_contracts'] = [ref(self.native)]
        with Image.open(self.source) as im:
            self.spec['items'][0]['source'] = {**ref(self.source), 'pixels': list(im.size),
                   'rgba_sha256': hashlib.sha256(im.convert('RGBA').tobytes()).hexdigest()}
        save(self.spec_path, self.spec)

    def call(self, *args, ok=True):
        p = subprocess.run([str(EXE), *map(str, args)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(p.returncode == 0, ok, (p.stdout + p.stderr)[-5000:])
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def plan(self, ok=True):
        return self.call('expand-plan', '--fullraw-spec', self.spec_path,
                         '--fullraw-spec-sha256', sha(self.spec_path),
                         '--output-root', self.output, '--out', self.batch_path, ok=ok)

    def run_batch(self, command='expand', ok=True):
        return self.call(command, '--batch', self.batch_path, '--workers', 1,
                         '--memory-gib', 1, ok=ok)

    def receipt(self):
        return next((self.output / 'receipts').glob('*.json'))

    def full_canvas_reference(self):
        k = self.spec['items'][0]['natural_k']
        cw, ch = self.geometry['canvas_1x']
        tx, ty = self.spec['items'][0]['translation']
        full = Image.new('RGBA', (cw * k, ch * k))
        with Image.open(self.source) as source:
            for y in range(source.height):
                for x in range(source.width):
                    rgba = source.getpixel((x, y))
                    if rgba[3]:
                        full.putpixel((x + tx, y + ty), rgba)
        path = self.root / 'materialized-reference.png'
        full.save(path)
        batch = self.root / 'reference-batch.json'
        self.call('expand-plan', '--source', path, '--base-width', cw, '--base-height', ch,
                  '--target-scale', 3, '--output-root', self.root / 'reference', '--out', batch)
        self.call('expand', '--batch', batch, '--workers', 1, '--memory-gib', 1)
        return Path(json.loads(batch.read_bytes())['items'][0]['output'])

    def test_public_plan_supports_fullraw_without_source_raster(self):
        before = sha(self.source)
        self.plan()
        self.assertFalse(self.output.exists())
        batch = json.loads(self.batch_path.read_bytes())
        self.assertEqual(batch['schema'], 'h3.master-expand.batch.v2')
        self.assertEqual(batch['sampling_mode'], 'full_raw_sampling_domain')
        self.assertEqual(sha(self.source), before)
        self.assertEqual(len(list(self.root.glob('*.png'))), 1)

    def full_canvas_v2(self, size=(31, 31)):
        self.geometry = {'canvas_1x': [8, 8], 'crop_1x': [4, 4], 'origin_1x': [2, 2]}
        image = Image.new('RGBA', size)
        for y in range(13, 19):
            for x in range(13, 19):
                image.putpixel((x, y), (90, 160, 240, 180))
        image.putpixel((15, 15), (110, 170, 245, 1))
        image.save(self.source)
        self.spec['schema'] = 'h3.master-expand.fullraw-spec.v2'
        item = self.spec['items'][0]
        item.pop('natural_k', None); item.pop('translation', None)
        item['source_layout'] = 'full-native-canvas'
        self.refresh()

    def test_full_canvas_fractional_scale_is_one_same_kernel_sample(self):
        self.full_canvas_v2()
        before = sha(self.source)
        reference_batch = self.root / 'direct-batch.json'
        self.call('expand-plan', '--source', self.source, '--base-width', 8, '--base-height', 8,
                  '--target-scale', 3, '--filter', 'h3-quality', '--output-root', self.root / 'direct',
                  '--out', reference_batch)
        self.call('expand', '--batch', reference_batch, '--workers', 1, '--memory-gib', 1)
        direct = Path(json.loads(reference_batch.read_bytes())['items'][0]['output'])
        self.plan(); self.run_batch(); self.run_batch('expand-verify')
        receipt = json.loads(self.receipt().read_bytes())
        detail = receipt['full_raw_sampling_domain']
        self.assertEqual(detail['source_layout'], 'full-native-canvas')
        self.assertEqual(detail['sampling_stages'], 1)
        self.assertEqual(detail['source_proof']['source_pixels_per_native_pixel'], [31, 8])
        self.assertEqual(detail['source_proof']['alpha1_pixels'], 1)
        with Image.open(direct) as expected, Image.open(receipt['output']) as full:
            self.assertEqual(full.size, (24, 24))
            self.assertEqual(full.tobytes(), expected.tobytes())
            with Image.open(detail['crop']['path']) as crop:
                self.assertEqual(crop.size, (12, 12))
                self.assertEqual(crop.tobytes(), full.crop((6, 6, 18, 18)).tobytes())
        self.assertEqual(sha(self.source), before)
        self.assertEqual(self.run_batch()['results'][0]['status'], 'REUSED_VERIFIED')

    def test_full_canvas_fractional_scale_direct_one_x(self):
        self.full_canvas_v2()
        self.spec['target_scale'] = 1; self.refresh()
        self.plan(); self.run_batch(); self.run_batch('expand-verify')
        receipt = json.loads(self.receipt().read_bytes())
        self.assertEqual(receipt['output_pixels'], [8, 8])
        self.assertEqual(receipt['full_raw_sampling_domain']['crop']['pixels'], [4, 4])
        self.assertEqual(receipt['full_raw_sampling_domain']['sampling_stages'], 1)

    def test_full_canvas_rejects_nonuniform_ratio_and_source_upscale(self):
        self.full_canvas_v2((31, 30))
        self.assertIn('FULLRAW_FULL_CANVAS_ASPECT_MISMATCH', str(self.plan(ok=False)))
        self.assertFalse(self.output.exists())
        self.full_canvas_v2((20, 20))
        self.assertIn('FULLRAW_NO_SOURCE_DOMAIN_UPSCALE', str(self.plan(ok=False)))

    def test_full_canvas_alpha1_outcrop_preserves_full_and_rejects_crop(self):
        self.full_canvas_v2((24, 24))
        image = Image.new('RGBA', (24, 24)); image.putpixel((0, 0), (90, 160, 240, 1)); image.save(self.source)
        self.refresh(); self.plan(); self.run_batch(ok=False)
        receipt = json.loads(self.receipt().read_bytes()); detail = receipt['full_raw_sampling_domain']
        self.assertEqual(detail['diagnostics']['outside_alpha1_pixels'], 1)
        self.assertEqual(detail['source_proof']['positive_outside_native_crop_before_sampling'], 1)
        self.assertIsNone(detail['crop'])
        self.assertFalse(list(self.output.glob('*-crop-*.png')))
        with Image.open(receipt['output']) as full:
            self.assertEqual(full.getpixel((0, 0)), (90, 160, 240, 1))

    def test_full_canvas_layout_cannot_be_resealed_as_padding(self):
        self.full_canvas_v2(); self.plan()
        batch = json.loads(self.batch_path.read_bytes())
        batch['items'][0]['full_raw_sampling_domain']['source_layout'] = 'crop'
        save(self.batch_path, batch)
        self.run_batch('expand-check', ok=False)
        self.assertFalse(self.output.exists())

    def quantized_source_outcrop_must_still_block(self, version):
        if version == 2:
            self.full_canvas_v2((80, 80))
        else:
            self.geometry = {'canvas_1x': [8, 8], 'crop_1x': [4, 4], 'origin_1x': [2, 2]}
            self.spec['items'][0].update(natural_k=10, translation=[0, 0])
        image = Image.new('RGBA', (80, 80)); image.putpixel((0, 0), (90, 160, 240, 1)); image.save(self.source)
        self.refresh(); self.plan(); result = self.run_batch(ok=False)
        self.assertEqual(result['results'][0]['code'], 'FULLRAW_SOURCE_POSITIVE_OUTSIDE_NATIVE_CROP')
        detail = json.loads(self.receipt().read_bytes())['full_raw_sampling_domain']
        self.assertEqual(detail['source_proof']['positive_outside_native_crop_before_sampling'], 1)
        self.assertEqual(detail['diagnostics']['outside_alpha_positive_pixels'], 0)
        self.assertEqual(detail['crop_status'], 'REJECTED_OUTCROP_ALPHA')
        self.assertIsNone(detail['crop'])
        self.assertFalse(list(self.output.glob('*-crop-*.png')))
        self.run_batch('expand-verify', ok=False)
        self.run_batch(ok=False)

    def test_v1_source_outcrop_cannot_be_hidden_by_alpha_quantization(self):
        self.quantized_source_outcrop_must_still_block(1)

    def test_v2_source_outcrop_cannot_be_hidden_by_alpha_quantization(self):
        self.quantized_source_outcrop_must_still_block(2)

    def test_same_kernel_full_canvas_and_exact_zero_outcrop_copy(self):
        reference = self.full_canvas_reference()
        self.plan(); self.run_batch(); self.run_batch('expand-verify')
        r = json.loads(self.receipt().read_bytes())
        detail = r['full_raw_sampling_domain']
        self.assertEqual(detail['diagnostics']['outside_alpha_positive_pixels'], 0)
        self.assertEqual(detail['source_proof']['alpha1_pixels'], 1)
        self.assertFalse(detail['materialized_source_file_written'])
        self.assertEqual(detail['sampling_stages'], 1)
        with Image.open(reference) as a, Image.open(r['output']) as b:
            self.assertEqual(a.tobytes(), b.tobytes())
            ox, oy = self.geometry['origin_1x']; w, h = self.geometry['crop_1x']
            expected = a.crop((ox * 3, oy * 3, (ox + w) * 3, (oy + h) * 3)).tobytes()
        with Image.open(detail['crop']['path']) as crop:
            self.assertEqual(crop.tobytes(), expected)
        self.assertFalse(r['human_approved'])
        self.assertFalse(r['runtime_accepted'])

    def test_alpha1_outcrop_rejects_but_preserves_full_diagnostic(self):
        im = Image.new('RGBA', (12, 13)); im.putpixel((0, 0), (90, 160, 240, 1)); im.save(self.source)
        self.spec['items'][0].update(natural_k=3, translation=[0, 0]); self.refresh()
        self.plan(); result = self.run_batch(ok=False)
        self.assertEqual(result['failed'], 1)
        r = json.loads(self.receipt().read_bytes()); detail = r['full_raw_sampling_domain']
        self.assertEqual(detail['diagnostics']['outside_alpha_positive_pixels'], 1)
        self.assertEqual(detail['diagnostics']['outside_alpha1_pixels'], 1)
        self.assertIsNone(detail['crop'])
        self.assertEqual(detail['crop_status'], 'REJECTED_OUTCROP_ALPHA')
        self.assertFalse(list(self.output.glob('*-crop-*.png')))
        with Image.open(r['output']) as im:
            self.assertEqual(im.getpixel((0, 0)), (90, 160, 240, 1))
        self.run_batch('expand-verify', ok=False)
        self.run_batch(ok=False)

    def test_positive_pixel_outside_full_domain_rejected_at_plan(self):
        self.spec['items'][0]['translation'] = [-20, -20]; self.refresh()
        result = self.plan(ok=False)
        self.assertIn('FULLRAW_POSITIVE_OUTSIDE_DOMAIN', str(result))
        self.assertFalse(self.output.exists())

    def test_raw_rgba_and_frozen_recipe_drift_rejected(self):
        self.spec['items'][0]['source']['rgba_sha256'] = '0' * 64
        save(self.spec_path, self.spec)
        self.plan(ok=False)
        self.refresh(); self.plan()
        batch = json.loads(self.batch_path.read_bytes())
        batch['items'][0]['full_raw_sampling_domain']['translation'][0] += 1
        save(self.batch_path, batch)
        self.run_batch('expand-check', ok=False)
        self.assertFalse(self.output.exists())

    def test_spec_native_and_lineage_drift_reject_before_output(self):
        self.plan()
        for path in (self.spec_path, self.native, self.projection, self.lineage):
            with self.subTest(path=path.name):
                old = path.read_bytes()
                path.write_bytes(old + b' ')
                self.run_batch('expand-check', ok=False)
                self.assertFalse(self.output.exists())
                path.write_bytes(old)

    def test_domain_pixel_budget_is_enforced(self):
        self.geometry['canvas_1x'] = [1000, 1000]
        self.spec['items'][0]['natural_k'] = 64; self.refresh()
        self.plan(ok=False); self.assertFalse(self.output.exists())

    def test_resume_and_independent_replay_detect_resealed_pixel_forgery(self):
        self.plan(); self.run_batch()
        files = {str(p): sha(p) for p in self.output.rglob('*') if p.is_file()}
        result = self.run_batch()
        self.assertEqual(result['results'][0]['status'], 'REUSED_VERIFIED')
        self.assertEqual(files, {str(p): sha(p) for p in self.output.rglob('*') if p.is_file()})
        rp = self.receipt(); r = json.loads(rp.read_bytes()); full = Path(r['output'])
        with Image.open(full) as im:
            im.putpixel((9, 9), (3, 9, 17, 255)); im.save(full)
            r.update(png_sha256=sha(full), rgba_sha256=hashlib.sha256(im.tobytes()).hexdigest(), bytes=full.stat().st_size)
        save(rp, r); self.run_batch('expand-verify', ok=False)

    def test_direct_one_x_is_not_css_or_downsampled_three_x(self):
        self.spec['target_scale'] = 1; self.refresh()
        self.plan(); self.run_batch(); self.run_batch('expand-verify')
        r = json.loads(self.receipt().read_bytes())
        self.assertEqual(r['output_pixels'], self.geometry['canvas_1x'])
        self.assertEqual(r['target_scale'], 1)
        self.assertEqual(r['full_raw_sampling_domain']['sampling_stages'], 1)

    def test_boolean_integer_and_missing_projection_are_rejected(self):
        original = copy.deepcopy(self.spec)
        for field, value in (('natural_k', True), ('translation', [False, 4])):
            self.spec = copy.deepcopy(original); self.spec['items'][0][field] = value
            save(self.spec_path, self.spec); self.plan(ok=False)
        self.spec = original
        native = json.loads(self.native.read_bytes()); native.pop('projection_source'); save(self.native, native)
        self.spec['native_contracts'] = [ref(self.native)]; save(self.spec_path, self.spec)
        self.plan(ok=False)

    def test_conflicting_crop_is_not_overwritten(self):
        self.plan(); self.output.mkdir()
        crop = self.output / 'ONE-crop-3x.png'; crop.write_bytes(b'foreign data')
        self.run_batch(ok=False)
        self.assertEqual(crop.read_bytes(), b'foreign data')
        self.assertFalse(list((self.output / 'receipts').glob('*.json')))

if __name__ == '__main__':
    unittest.main(verbosity=2)
