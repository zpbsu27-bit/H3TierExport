"""Physical scale is checked independently of projected pose and root motion."""
import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import blender_motion_consistency as mod


class MotionConsistencyContract(unittest.TestCase):
    def setUp(self):
        self.groups=[{'native_group':0,'slots':[{'native_frame':0,'action_frame':1,'subframe':0.0},
                                               {'native_frame':1,'action_frame':3,'subframe':0.5}]}]
        self.contract={'schema':mod.SCHEMA,'calibration_key':[0,0],
            'rigid_segments':[{'id':name,'bone':name,'a':[0,0,0],'b':[0,0,1],
                               'max_relative_change':.02} for name in ('head','torso')],
            'root':{'bone':'root','point':[0,0,0],'max_error_native':.1,
                'track':[{'native_group':0,'native_frame':0,'expected_pixel_1x':[10,20]},
                         {'native_group':0,'native_frame':1,'expected_pixel_1x':[12,19]}]}}
        self.samples=[{'camera':[1,0,0,1],'lengths':{'head':1,'torso':3},'root_native':[10,20]},
                      {'camera':[1,0,0,1],'lengths':{'head':1,'torso':3},'root_native':[12,19]}]

    def evaluate(self):
        return mod.evaluate(self.contract,self.groups,lambda row:self.samples[row['native_frame']])

    def test_authored_root_motion_is_accepted_with_fixed_physical_scale(self):
        result=self.evaluate();self.assertTrue(result['rigid_segment_scale_pass']);self.assertTrue(result['root_track_pass'])
        self.assertFalse(result['action_semantics_verified']);self.assertFalse(result['art_approved'])

    def test_no_contract_never_becomes_body_scale_pass(self):
        result=mod.evaluate(None,self.groups,lambda r:None)
        self.assertEqual(result['status'],'NOT_MEASURED');self.assertFalse(result['rigid_segment_scale_pass'])

    def test_whole_character_breathing_scale_is_refused(self):
        self.samples[1]['lengths']={'head':1.2,'torso':3.6}
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_RIGID_SEGMENT_SCALE_DRIFT'):self.evaluate()

    def test_one_body_part_scaling_is_also_refused(self):
        self.samples[1]['lengths']['head']=1.2
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_RIGID_SEGMENT_SCALE_DRIFT'):self.evaluate()

    def test_camera_zoom_cannot_mask_a_scale_difference(self):
        self.samples[1]['camera'][-1]=1.2
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_CAMERA_DRIFT'):self.evaluate()

    def test_unplanned_root_shift_is_refused(self):
        self.samples[1]['root_native']=[12,20]
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_ROOT_TRACK_MISMATCH'):self.evaluate()

    def test_full_root_track_and_calibration_slot_are_required(self):
        self.contract['root']['track'].pop()
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_COMPLETE_ROOT_TRACK_REQUIRED'):self.evaluate()

    def test_nonfinite_measurements_are_not_treated_as_consistent(self):
        self.samples[1]['lengths']['head']=float('nan')
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_SEGMENT_MEASUREMENT_INVALID'):self.evaluate()

    def test_receipt_replay_rejects_omitted_samples_and_fabricated_metrics(self):
        report=self.evaluate();mod.replay_report(self.contract,self.groups,report)
        report['samples'].pop()
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_CONSISTENCY_RECEIPT_COVERAGE'):
            mod.replay_report(self.contract,self.groups,report)
        report=self.evaluate();report['samples'][1]['root_error_native']=999
        with self.assertRaisesRegex(mod.ConsistencyError,'MOTION_CONSISTENCY_RECEIPT_REPLAY_MISMATCH'):
            mod.replay_report(self.contract,self.groups,report)

    def test_actual_blender_evaluation_rejects_scale_root_and_camera_defects(self):
        blender=os.environ.get('H3TIEREXPORT_BLENDER')
        if not blender:self.skipTest('set H3TIEREXPORT_BLENDER for actual Blender evaluation')
        with tempfile.TemporaryDirectory(prefix='h3-motion-guard-') as tmp:
            out=Path(tmp)/'diagnostic'
            command=[blender,'--background','--factory-startup','--python-exit-code','1','--python',
                     str(Path(__file__).with_name('blender_motion_consistency_blender.py')),'--',str(out)]
            run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
            self.assertEqual(run.returncode,0,run.stdout[-3000:]+run.stderr[-3000:])
            report=json.loads((out/'motion-consistency-test.json').read_bytes())
            self.assertEqual(report['status'],'PASS');self.assertFalse(report['real_creature'])
            self.assertEqual(report['injected_defects_rejected'],['MOTION_RIGID_SEGMENT_SCALE_DRIFT',
                             'MOTION_ROOT_TRACK_MISMATCH','MOTION_CAMERA_DRIFT',
                             'MOTION_RIGID_SEGMENT_SCALE_DRIFT'])


if __name__=='__main__':unittest.main()
