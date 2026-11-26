"""Meaningful counterexamples for temporal landmark screening; no file writes."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import sequence_temporal_review as temporal


def sample():
    def frame(index, deviation, root):
        return {'key': ['def', 'TEST', 6, index], 'status': 'LOSSLESS_LAYOUT_READY',
                'profile_direction': 'rear', 'pose_group': 'walking', 'direction': 'right',
                'body_deviation_fraction': {'horse_head': deviation},
                'root_target_native_canvas': root,
                'source_positive_alpha_bbox': [0, 0, 10+index*1000, 100]}
    items = [frame(0, -.06, [10, 20]), frame(1, 0, [10, 20.2]), frame(2, .06, [10, 21.3])]
    return {'items': items, 'sequence_contract': {'groups': [6],
            'logical_scope': [x['key'] for x in items], 'aliases': []}}


class SequenceTemporalReview(unittest.TestCase):
    def test_range_catches_hidden_spread_even_when_each_frame_is_near_anchor(self):
        r = temporal.compare(sample(), [], .10, .10, None)
        self.assertEqual(r['status'], 'REVIEW_REQUIRED')
        self.assertGreater(r['comparisons'][0]['spread_fraction'], .12)
        self.assertTrue(all(c['status'] == 'NO_NUMERIC_WARNING'
                            for t in r['transitions'] for c in t['metric_changes']))
        self.assertFalse(r['alpha_bbox_used_as_body_measurement'])
        self.assertFalse(r['automatic_art_approval'])

    def test_loop_is_compared_only_when_explicit_and_root_is_only_a_warning(self):
        raw = sample()
        one = temporal.compare(raw, [], .20, .20, 1)
        loop = temporal.compare(raw, [6], .20, .20, 1)
        self.assertEqual(len(one['transitions']), 2)
        self.assertEqual(len(loop['transitions']), 3)
        self.assertEqual(one['transitions'][1]['declared_root_step']['status'], 'REVIEW_REQUIRED')
        self.assertTrue(loop['transitions'][-1]['explicit_loop_closure'])
        self.assertEqual(loop['transitions'][-1]['declared_root_step']['status'], 'REVIEW_REQUIRED')
        self.assertFalse(loop['automatic_redraw'])

    def test_unrelated_pose_and_direction_are_never_compared(self):
        raw = sample()
        raw['items'][1]['pose_group'] = 'different-pose'
        raw['items'][1]['direction'] = 'left'
        r = temporal.compare(raw, [], .50, .50, None)
        self.assertEqual(r['status'], 'INCOMPLETE_EVIDENCE')
        self.assertTrue(all(x['status'] == 'NOT_COMPARABLE'
                            for t in r['transitions'] for x in t['metric_changes']))
        self.assertTrue(all(t['declared_root_step']['status'] == 'NOT_COMPARABLE'
                            for t in r['transitions']))

    def test_alias_retains_logical_slot_and_uses_owner_measurement(self):
        raw = sample()
        removed = raw['items'].pop()
        raw['sequence_contract']['aliases'] = [{'key': removed['key'],
                                                'owner_key': raw['items'][0]['key']}]
        r = temporal.compare(raw, [6], .50, .50, None)
        values = r['comparisons'][0]['values']
        self.assertEqual([x['key'][3] for x in values], [0, 1, 2])
        self.assertEqual(values[0]['relative_to_reference'], values[2]['relative_to_reference'])

    def test_v2_cross_group_alias_is_reported_as_external_and_excluded(self):
        raw=sample();removed=raw['items'].pop()
        raw['sequence_contract']['schema']='h3.creature.sequence-registration.contract.v2'
        raw['sequence_contract']['aliases']=[{'key':removed['key'],
            'owner_key':['def','TEST',5,0],
            'owner_source':{'path':'D:/source/TEST_5_0.png','sha256':'a'*64,'pixels':[220,220]}}]
        r=temporal.compare(raw,[],.50,.50,.25)
        self.assertEqual(r['status'],'INCOMPLETE_EVIDENCE')
        self.assertEqual(len(r['external_alias_dependencies']),1)
        self.assertEqual(r['external_alias_dependencies'][0]['status'],'EXTERNAL_OWNER_NOT_IN_LAYOUT')
        self.assertEqual([x['key'][3] for x in r['comparisons'][0]['values']],[0,1])
        self.assertEqual(len(r['transitions']),1)
        self.assertEqual(r['transitions'][0]['declared_root_step']['status'],'NO_NUMERIC_WARNING')

    def test_missing_metric_and_unselected_loop_are_not_silently_accepted(self):
        raw = sample()
        raw['items'][1]['body_deviation_fraction'] = {}
        r = temporal.compare(raw, [], .50, .50, None)
        self.assertEqual(r['status'], 'INCOMPLETE_EVIDENCE')
        self.assertEqual(r['comparisons'][0]['count'], 2)
        with self.assertRaisesRegex(ValueError, 'TEMPORAL_LOOP_GROUP_NOT_SELECTED'):
            temporal.compare(raw, [9], .50, .50, None)

    def test_missing_logical_slot_fails_closed(self):
        raw = copy.deepcopy(sample())
        raw['items'].pop()
        with self.assertRaisesRegex(ValueError, 'TEMPORAL_SCOPE_INCOMPLETE'):
            temporal.compare(raw, [], .10, .10, None)


if __name__ == '__main__':
    unittest.main()
