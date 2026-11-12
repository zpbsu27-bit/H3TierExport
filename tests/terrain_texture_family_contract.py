from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import itertools
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import asset_repair as repair
import asset_repair_terrain as terrain
from terrain_texture_core import vertical_cut, patch_quilt, boundary_template, join_fixed_frame, texture_variant


def source(size=512):
    rng = np.random.default_rng(17)
    result = rng.integers(0, 256, (size, size, 4), dtype=np.uint8)
    result[..., 3] = 255
    return result


class TerrainTextureTests(unittest.TestCase):
    def test_minimum_cut_matches_exhaustive_oracle(self):
        error = np.asarray([[3, 2, 8], [1, 9, 4], [5, 2, 3], [7, 4, 1]], float)
        for start, end in ((None, None), (0, 2), (2, 0)):
            path = vertical_cut(error, start, end)
            costs = [sum(error[i, x] for i, x in enumerate(xs)) for xs in itertools.product(range(3), repeat=4)
                     if all(abs(xs[i]-xs[i-1]) <= 1 for i in range(1, 4))
                     and (start is None or xs[0] == start) and (end is None or xs[-1] == end)]
            self.assertEqual(sum(error[i, x] for i, x in enumerate(path)), min(costs))

    def test_impossible_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            vertical_cut(np.ones((2, 5)), 0, 4)

    def test_quilt_is_exact_source_pixel_selection_not_rgb_averaging(self):
        src = source()
        out, coords, patches = patch_quilt(src, 512, 19, patch=128, overlap=24, candidates=8)
        self.assertTrue(np.array_equal(out, src[coords[..., 1], coords[..., 0]]))
        self.assertEqual(len(patches), 25)
        self.assertTrue((out[..., 3] == 255).all())

    def test_variant_replay_and_seed_diversity(self):
        src = source()
        a, _ = texture_variant(src, size=512, seed=7, minimum=32, maximum=80)
        b, _ = texture_variant(src, size=512, seed=7, minimum=32, maximum=80)
        c, _ = texture_variant(src, size=512, seed=23, minimum=32, maximum=80)
        self.assertTrue(np.array_equal(a, b))
        self.assertFalse(np.array_equal(a[80:-80, 80:-80], c[80:-80, 80:-80]))

    def test_all_flip_neighbors_share_frozen_two_dimensional_profiles(self):
        src = source()
        templates = []
        variants = []
        for seed in (4, 12):
            a, _ = texture_variant(src, size=512, seed=seed, minimum=32, maximum=80)
            templates.append(a[:32])
            variants.extend((a, a[::-1], a[:, ::-1], a[::-1, ::-1]))
        self.assertTrue(np.array_equal(templates[0], templates[1]))
        for a, b in itertools.product(variants, repeat=2):
            self.assertTrue(np.array_equal(a[:, -1], b[:, 0]))
            self.assertTrue(np.array_equal(a[-1], b[0]))

    def test_frame_only_selects_input_pixels_and_leaves_centre_exact(self):
        a, b = source(), source()[::-1].copy()
        out, mask = join_fixed_frame(a, b, 32, 80)
        self.assertTrue(np.array_equal(out[mask], b[mask]))
        self.assertTrue(np.array_equal(out[~mask], a[~mask]))
        self.assertTrue(np.array_equal(out[80:-80, 80:-80], a[80:-80, 80:-80]))

    def test_transparent_or_low_capacity_material_is_rejected(self):
        src = source()
        src[0, 0, 3] = 0
        with self.assertRaises(ValueError):
            patch_quilt(src, 512, 1)
        with self.assertRaises(ValueError):
            patch_quilt(source(96), 512, 1)


class RegisteredTerrainFamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='terrain-family-synthetic-')
        self.root = Path(self.temp.name)
        self.product = patch.object(repair, 'PRODUCT', self.root)
        self.product.start()
        self.g = {'canvas_1x': [32, 32], 'crop_1x': [32, 32], 'origin_1x': [0, 0]}
        self.keys = [['def', 'TEST_TERRAIN', 0, 49], ['def', 'TEST_TERRAIN', 0, 50]]
        self.parent = source()
        self.registered = self.binding('parent', self.parent, self.keys[0])
        inv = self.root / 'inventory.json'
        inv.write_text(json.dumps({'rows': [{'kind': 'def', 'identity': key[1], 'group': key[2], 'frame': key[3],
                                             'canvas': [32, 32], 'crop': [32, 32], 'anchor': [0, 0]} for key in self.keys]}))
        contract = self.root / 'family.json'
        contract.write_text(json.dumps({'schema': 'h3.asset-repair.terrain-family.v1', 'parent_key': self.keys[0],
                                        'material_id': 'test-material', 'native_inventory': self.ref(inv), 'target_keys': self.keys}))
        self.job = {'id': 'test-family', 'native_key': self.keys[0], 'source': self.registered,
                    'operator': 'registered-terrain-family', 'operator_version': 1, 'family_contract': self.ref(contract),
                    'frame_native': [2, 4], 'patch_native': 8, 'overlap_native': 1.5,
                    'variants': [{'id': f'centre-{i}', 'native_key': key, 'seed': i+5} for i, key in enumerate(self.keys)]}

    def tearDown(self):
        self.product.stop()
        self.temp.cleanup()

    def ref(self, p):
        return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}

    def binding(self, name, rgba, key):
        image = self.root / (name + '.png')
        Image.fromarray(rgba).save(image)
        im = {**self.ref(image), 'pixels': [512, 512]}
        proof = self.root / (name + '-registration.json')
        proof.write_text(json.dumps({'schema': 'h3.motion.receipt.v1', 'target_scale': 16,
            'png_sha256': im['sha256'], 'output_pixels': [512, 512], 'origin_at_target_scale': [0, 0],
            'contract': {'native_entry': {'key': key, 'targets': self.g}, 'entry': {'layers': [{'source_a': im}]}}}))
        return {'image': im, 'receipt': self.ref(proof), 'geometry': self.g, 'editing_scale': 16}

    def test_registered_family_check_run_and_pixel_replay(self):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'schema': repair.MANIFEST_SCHEMA, 'operation_id': 'family', 'jobs': [self.job]}))
        digest = repair.digest(manifest.read_bytes())
        out = self.root / 'build/experiments/asset-repair/family'
        self.assertEqual(repair.execute('check', manifest, digest, out, 'family')['status'], 'READY')
        self.assertFalse(out.exists())
        self.assertEqual(repair.execute('run', manifest, digest, out, 'family')['outputs'], 2)
        self.assertEqual(repair.execute('verify', manifest, digest, out, 'family')['status'], 'VERIFIED')
        receipt = json.loads((out / 'asset-repair-receipt.json').read_bytes())
        self.assertFalse(receipt['art_approved'])
        self.assertFalse(receipt['native_RGB_used_as_paint'])
        self.assertEqual([o['native_key'] for o in receipt['outputs']], self.keys)

    def test_declared_painted_accent_is_retained_without_changing_rim(self):
        donor = np.full((512, 512, 4), [150, 120, 80, 255], dtype=np.uint8)
        donor[235:277, 235:277] = [30, 110, 20, 255]
        spec = {'seed_rule': {'minimum_rgb': [0, 0, 0], 'maximum_rgb': [255, 255, 255],
                              'minimum_channel_differences': {'g-r': 20, 'g-b': 30}},
                'padding_pixels': 32, 'minimum_component_pixels': 16}
        result, stats = terrain.retain_accents(repair, self.parent, donor, spec, 64)
        self.assertTrue(np.array_equal(result[235:277, 235:277], donor[235:277, 235:277]))
        self.assertTrue(np.array_equal(result[:64], self.parent[:64]))
        self.assertEqual(stats['component_patch_count'], 1)
        self.assertGreater(stats['protected_painted_pixels_retained_exactly'], 0)
        empty = dict(spec, seed_rule={'minimum_rgb': [250, 250, 250], 'maximum_rgb': [255, 255, 255],
                                      'minimum_channel_differences': {}})
        with self.assertRaisesRegex(ValueError, 'DECLARED_ACCENT_NOT_FOUND'):
            terrain.retain_accents(repair, self.parent, donor, empty, 64)

    def test_wrong_family_and_three_x_promotion_are_blocked(self):
        self.job['variants'][1]['native_key'] = ['def', 'ANOTHER', 0, 50]
        with self.assertRaisesRegex(ValueError, 'TARGET_KEYS_DIFFER'):
            repair.dispatch(self.job)
        self.job['source'] = dict(self.registered, editing_scale=3)
        with self.assertRaisesRegex(ValueError, 'HQ_EDITING_SCALE_REQUIRED'):
            repair.dispatch(self.job)


if __name__ == '__main__':
    unittest.main()
