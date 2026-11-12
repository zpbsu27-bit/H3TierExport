"""Counterexamples for complete sequence registration, with no model inference."""
import copy
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import subject_scale_layout as layout
import creature_sequence_registration as sequence


def ref(p,pixels=None):
    r={'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    if pixels:r['pixels']=pixels
    return r


def native_def():
    raw=bytearray(struct.pack('<4I',66,60,60,1)+bytes(768))
    raw+=struct.pack('<4I',0,3,0,0)
    raw+=b'A.pcx\0'.ljust(13,b'\0')+b'B.pcx\0'.ljust(13,b'\0')+b'A.pcx\0'.ljust(13,b'\0')
    offset=len(raw)+12
    raw+=struct.pack('<3I',offset,offset+32,offset)
    raw+=struct.pack('<6I2i',32,0,60,60,50,50,5,5)*2
    return bytes(raw)


class SequenceRegistrationContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.definition=self.root/'TEST.def';self.definition.write_bytes(native_def())
        rows=[{'kind':'def','category':'creatures','identity':'TEST','group':0,'frame':i,
               'canvas':[60,60],'crop':[50,50],'anchor':[5,5],
               'resource':('B.pcx' if i==1 else 'A.pcx')} for i in range(3)]
        self.inventory=self.root/'inventory.json.gz'
        self.inventory.write_bytes(gzip.compress(json.dumps({'rows':rows}).encode()))
        self.standard=self.root/'standard.json';self.standard.write_text('{"version":"test"}')
        self.a=self.root/'raw-a.png';self.b=self.root/'raw-b.png'
        for p,wing in [(self.a,False),(self.b,True)]:
            im=Image.new('RGBA',(220,220));im.paste((90,120,200,255),(90,80,130,160))
            if wing:im.paste((20,80,240,255),(50,50,170,70))
            im.putpixel((89,100),(33,22,11,1));im.save(p)
        self.profile=self.root/'profile.json'
        self.pdata={'schema':layout.PROFILE,'review_status':'assistant_scale_profile_pass',
                    'replaces_canonical_art_standard':False,
                    'art_standard':{**ref(self.standard),'version':'test'},
                    'reference_images':[ref(self.a,[220,220])],
                    'directions':{'side':{'pose_group':'standing-side','direction':'right',
                        'anchor_native_canvas':[30,40],'lengths_native':{'torso':10,'head':5},
                        'primary_metric':'torso','primary_tolerance_fraction':.01,
                        'secondary_tolerance_fraction':.02,'clearance_native':1.5}}}
        self.manifest=self.root/'request.json'
        def item(frame,p):
            return {'key':['def','TEST',0,frame],'source':ref(p,[220,220]),
                    'profile_direction':'side','root_offset_native':[0,0],
                    'measurement':{'image_sha256':ref(p)['sha256'],'coordinate_space':'source_pixels',
                        'method':'visual-anatomical-landmarks','annotation_author':'assistant',
                        'pose_group':'standing-side','direction':'right','anchor_source':[110,150],
                        'paths':{'torso':[[110,90],[110,130]],'head':[[110,80],[110,100]]}}}
        self.data={'schema':sequence.SCHEMA,'operation_id':'test-sequence','output_layout':'native-canvas',
                   'implementation':{'schema':'h3.creature.sequence-registration.readers.v1',
                       'sequence_reader':ref(Path(sequence.__file__)),'layout_reader':ref(Path(layout.__file__))},
                   'native_inventory':ref(self.inventory),'profile':None,
                   'expected_slots':[['def','TEST',0,0],['def','TEST',0,1]],
                   'items':[item(0,self.a),item(1,self.b)],
                   'sequence_contract':{'schema':sequence.CONTRACT,'native_def':ref(self.definition),
                       'groups':[0],'logical_scope':[['def','TEST',0,i] for i in range(3)],
                       'scale_policy':'anatomical-targets','camera_policy':'fixed-native-units',
                       'root_semantics':'explicit thorax reference','trajectory_units':'native_canvas_1x',
                       'aliases':[{'key':['def','TEST',0,2],'owner_key':['def','TEST',0,0],
                                   'source':ref(self.a,[220,220])}]}}

    def save(self):
        self.profile.write_text(json.dumps(self.pdata));self.data['profile']=ref(self.profile)
        self.manifest.write_text(json.dumps(self.data));return ref(self.manifest)

    def resolve(self):
        return layout.resolve(self.save(),'test-sequence')[2]

    def test_wing_extent_does_not_change_body_scale_or_root(self):
        rows=self.resolve();a,b=[r for r,_ in rows]
        self.assertEqual(a['editing_scale'],b['editing_scale'])
        self.assertEqual(a['root_target_native_canvas'],b['root_target_native_canvas'])
        self.assertNotEqual(a['source_positive_alpha_bbox'],b['source_positive_alpha_bbox'])
        self.assertFalse(a['pixels_resampled']);self.assertFalse(a['alpha_bbox_used_as_body_measurement'])
        self.assertEqual(rows[0][1].getchannel('A').histogram()[1],1)

    def test_complete_group_and_actual_binary_alias_are_required(self):
        self.data['sequence_contract']['aliases']=[]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_LOGICAL_SLOT_OR_ALIAS_MISSING'):self.resolve()

    def test_false_alias_offset_is_blocked(self):
        self.data['sequence_contract']['aliases'][0]['owner_key']=['def','TEST',0,1]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ALIAS_OFFSET_MISMATCH'):self.resolve()

    def test_stale_alias_source_is_blocked(self):
        self.data['sequence_contract']['aliases'][0]['source']=ref(self.b,[220,220])
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ALIAS_SOURCE_BYTES_DIFFER'):self.resolve()

    def test_inventory_cannot_replace_binary_geometry(self):
        raw=json.loads(gzip.decompress(self.inventory.read_bytes()));raw['rows'][1]['anchor']=[6,5]
        self.inventory.write_bytes(gzip.compress(json.dumps(raw).encode()));self.data['native_inventory']=ref(self.inventory)
        with self.assertRaisesRegex(ValueError,'SEQUENCE_INVENTORY_DIFFERS_FROM_BINARY_DEF'):self.resolve()

    def test_pose_direction_and_explicit_trajectory_are_required(self):
        self.data['items'][1]['measurement']['direction']='left'
        with self.assertRaisesRegex(ValueError,'SEQUENCE_POSE_DIRECTION_NOT_BOUND'):self.resolve()
        self.data['items'][1]['measurement']['direction']='right'
        self.data['items'][1].pop('root_offset_native')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_EXPLICIT_ROOT_TRAJECTORY_REQUIRED'):self.resolve()

    def test_explicit_motion_is_preserved_instead_of_forcing_all_roots_equal(self):
        self.data['items'][1]['root_offset_native']=[2,-1]
        a,b=[r for r,_ in self.resolve()]
        self.assertEqual(b['root_target_native_canvas'],[32,39])
        self.assertEqual(a['editing_scale'],b['editing_scale'])

    def test_missing_secondary_metric_is_not_body_approval(self):
        self.pdata['directions']['side']['lengths_native'].pop('head')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_TWO_ANATOMICAL_METRICS_REQUIRED'):self.resolve()

    def test_fit_conflict_cannot_silently_shrink_or_write_partial_batch(self):
        self.pdata['directions']['side']['anchor_native_canvas']=[53,53]
        request=self.save()
        with patch.object(layout,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/test-sequence'
            with self.assertRaisesRegex(ValueError,'SEQUENCE_FIT_CONFLICT_REVIEW_BEFORE_WRITING'):
                layout.execute('run',request,out,'test-sequence')
            self.assertFalse(out.exists())

    def test_explicit_crop_edge_contact_preserves_alpha_without_implicit_opt_in(self):
        im=Image.open(self.a).convert('RGBA');im.putpixel((10,100),(33,44,55,1));im.save(self.a)
        self.pdata['reference_images']=[ref(self.a,[220,220])]
        self.data['items'][0]['source']=ref(self.a,[220,220])
        self.data['items'][0]['measurement']['image_sha256']=ref(self.a)['sha256']
        self.data['sequence_contract']['aliases'][0]['source']=ref(self.a,[220,220])
        rows=self.resolve()
        self.assertEqual(rows[0][0]['status'],'NEEDS_REDRAW')
        self.pdata['directions']['side']['clearance_native']=0
        with self.assertRaisesRegex(ValueError,'SEQUENCE_CROP_EDGE_CONTACT_ACK_REQUIRED'):self.resolve()
        self.pdata['directions']['side']['allow_native_crop_edge_contact']=True
        result,image=self.resolve()[0]
        self.assertEqual(result['status'],'LOSSLESS_LAYOUT_READY')
        self.assertTrue(result['crop_edge_contact_allowed'])
        self.assertEqual(image.getpixel((20,110)),(33,44,55,1))
        self.assertTrue(result['source_positive_alpha_preserved'])

    def test_legacy_static_schema_still_rejects_multiframe_scope(self):
        self.data['schema']=layout.SCHEMA
        with self.assertRaisesRegex(ValueError,'STATIC_SLOTS_ONLY'):self.resolve()

    def test_run_verify_and_single_3x_handoff_are_replayable(self):
        request=self.save()
        with patch.object(layout,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/test-sequence'
            layout.execute('run',request,out,'test-sequence')
            handoff=sequence.handoff(request,out)
            layout.execute('verify',request,out,'test-sequence')
            sequence.handoff(request,out,create=False)
            rendered=json.loads(Path(handoff['native_review_manifest']).read_bytes())
            self.assertEqual(len(rendered['items']),2)
            self.assertTrue(all(x['render_scales']==[3] and x['registration']=='strict-aspect'
                                and x['output_layout']=='native-crop' for x in rendered['items']))
            record=json.loads((out/'receipt.json').read_bytes())
            self.assertFalse(record['automatic_art_approval']);self.assertFalse(record['formal_3x'])
            self.assertFalse(record['independent_per_frame_contain'])
            (out/'native-review-manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'SEQUENCE_HANDOFF_DRIFT'):
                sequence.handoff(request,out,create=False)

    def test_unknown_group_is_not_silently_omitted(self):
        self.data['sequence_contract']['groups'].append(17)
        with self.assertRaisesRegex(ValueError,'SEQUENCE_SELECTED_GROUP_ABSENT'):self.resolve()

    def test_missing_or_changed_reader_cannot_be_relabelled_current(self):
        saved=self.data.pop('implementation')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_FROZEN_READER_BINDING_REQUIRED'):self.resolve()
        self.data['implementation']=saved
        self.data['implementation']['sequence_reader']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):self.resolve()

    def test_existing_gui_generic_stage_also_emits_the_sequence_handoff(self):
        request=self.save()
        with patch.object(layout,'PRODUCT',self.root),contextlib.redirect_stdout(io.StringIO()):
            out=self.root/'build/experiments/scale-layout/test-sequence'
            args=['--manifest',request['path'],'--manifest-sha256',request['sha256'],
                  '--outdir',str(out),'--operation-id','test-sequence']
            self.assertEqual(layout.main(['run',*args]),0)
            self.assertTrue((out/'native-review-manifest.json').is_file())
            self.assertEqual(layout.main(['verify',*args]),0)


if __name__=='__main__':unittest.main()
