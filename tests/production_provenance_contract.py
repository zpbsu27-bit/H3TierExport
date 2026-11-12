"""Regressions for Action timing and semantic provenance; real local delivery,
synthetic Action receipt fixture, no Blender/cloud inference or art approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'scripts'))
sys.path.insert(0, str(PRODUCT / 'tests'))
import action_production_binding_contract as fixtures
import creature_production as production


class ProductionProvenanceContract(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ActionProductionBindingContract()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.create_action()
        self.prepared = self.f.prepare()
        self.path = Path(self.prepared['manifest']['path'])
        self.original = json.loads(self.path.read_bytes())
        self.proof = self.f.fixture.root / 'semantic-evidence.json'
        self.proof.write_text(json.dumps({'kind': 'synthetic-semantic-reference',
                                         'facing': 'east', 'footpoint': [1, 2]}), encoding='utf-8')
        self.ref = {'path': str(self.proof), 'sha256': fixtures.digest(self.proof)}

    def context(self, manifest):
        self.path.write_bytes(production._json_bytes(manifest))
        return production._load_context(str(self.path), fixtures.digest(self.path),
                                        self.f.delivery_id, str(self.f.delivery_outdir))

    def known_manifest(self):
        value = copy.deepcopy(self.original)
        value['slots'][0]['facing'] = {'value': 'east', 'provenance': self.ref.copy()}
        value['slots'][0]['footpoint'] = {'value': [1, 2], 'provenance': self.ref.copy()}
        return value

    def test_action_duration_loop_and_provenance_must_match_source(self):
        timing = self.original['slots'][0]['timing']
        other = self.f.fixture.root / 'unrelated-timing.json'
        other.write_bytes(Path(timing['duration_provenance']['path']).read_bytes())
        alternate = {'path': str(other), 'sha256': fixtures.digest(other)}
        mutations = [('duration_ms', 1099), ('loop', not timing['loop']),
                     ('duration_provenance', alternate), ('loop_provenance', alternate)]
        for field, changed in mutations:
            with self.subTest(field=field):
                value = copy.deepcopy(self.original)
                value['slots'][0]['timing'][field] = changed
                result = production.preflight(self.context(value))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertIn('ACTION_TIMING_BINDING_MISMATCH', result['frames'][0]['errors'][0])
                self.assertFalse(self.f.delivery_outdir.exists())

    def test_semantic_references_require_existing_exact_path_and_sha(self):
        missing = {'path': str(self.f.fixture.root / 'no-such-evidence.json'), 'sha256': '0' * 64}
        for field, known in [('facing', 'east'), ('footpoint', [1, 2])]:
            for label, invalid in [('missing', missing), ('malformed', {'note': 'not a file binding'}),
                                   ('bad-sha', {**self.ref, 'sha256': '0' * 64})]:
                with self.subTest(field=field, reason=label):
                    value = copy.deepcopy(self.original)
                    value['slots'][0][field] = {'value': known, 'provenance': invalid}
                    result = production.preflight(self.context(value))
                    self.assertEqual(result['status'], 'BLOCKED')
                    self.assertFalse(self.f.delivery_outdir.exists())

    def test_known_semantics_roundtrip_preserves_refs_and_rejects_later_drift(self):
        context = self.context(self.known_manifest())
        result = production.run(context)
        self.assertEqual(result['status'], 'DELIVERED_OPEN')
        receipt = json.loads((self.f.delivery_outdir / 'receipt.json').read_bytes())
        row = receipt['frames'][0]
        self.assertEqual(row.get('facing_provenance'), self.ref)
        self.assertEqual(row.get('footpoint_provenance'), self.ref)
        self.assertFalse(receipt['art_approved'])
        self.assertEqual(production.verify(context)['status'], 'VERIFIED_OPEN')
        self.proof.write_bytes(b'changed semantic evidence, not original bytes')
        self.assertEqual(production.verify(context)['status'], 'BLOCKED')
        self.assertEqual(production.run(context)['status'], 'BLOCKED')

    def test_semantic_drift_during_copy_does_not_publish_completion(self):
        context = self.context(self.known_manifest())
        original_copy = production._copy_verified
        def mutate_after_copy(*args, **kwargs):
            copied = original_copy(*args, **kwargs)
            self.proof.write_bytes(b'changed during delivery')
            return copied
        with patch.object(production, '_copy_verified', side_effect=mutate_after_copy):
            result = production.run(context)
        self.assertEqual(result['status'], 'PARTIAL_OUTPUT_PRESENT')
        self.assertEqual(result['code'], 'PROVENANCE_CHANGED_DURING_RUN')
        self.assertFalse((self.f.delivery_outdir / 'complete.json').exists())

    def test_resealed_delivery_receipt_cannot_change_metadata(self):
        context = self.context(self.known_manifest())
        self.assertEqual(production.run(context)['status'], 'DELIVERED_OPEN')
        receipt_path = self.f.delivery_outdir / 'receipt.json'
        complete_path = self.f.delivery_outdir / 'complete.json'
        old_receipt = receipt_path.read_bytes()
        old_complete = complete_path.read_bytes()
        mutations = ['duration', 'loop', 'facing-ref', 'footpoint-ref', 'known-to-unknown', 'loop-as-int', 'malformed-open-fields']
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                receipt = json.loads(old_receipt)
                row = receipt['frames'][0]
                if mutation == 'duration': row['timing']['duration_ms'] += 1
                elif mutation == 'loop': row['timing']['loop'] = not row['timing']['loop']
                elif mutation == 'facing-ref': row['facing_provenance'] = None
                elif mutation == 'footpoint-ref': row['footpoint_provenance'] = None
                elif mutation == 'loop-as-int': row['timing']['loop'] = int(row['timing']['loop'])
                elif mutation == 'malformed-open-fields': row['open_fields'] = [{}]
                else: row['facing'] = 'unknown'
                raw = production._json_bytes(receipt)
                complete = json.loads(old_complete)
                complete['receipt_sha256'] = hashlib.sha256(raw).hexdigest()
                receipt_path.write_bytes(raw)
                complete_path.write_bytes(production._json_bytes(complete))
                self.assertEqual(production.verify(context)['status'], 'BLOCKED')
        receipt_path.write_bytes(old_receipt)
        complete_path.write_bytes(old_complete)
        self.assertEqual(production.verify(context)['status'], 'VERIFIED_OPEN')


if __name__ == '__main__':
    unittest.main(verbosity=2)
