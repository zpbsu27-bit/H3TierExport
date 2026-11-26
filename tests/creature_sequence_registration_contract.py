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
    raw=bytearray(struct.pack('<4I',66,60,60,2)+bytes(768))
    # Two groups: group 1 frame 0 is an offset alias to group 0 frame 0.
    group0_at=784
    group0_size=16+3*13+3*4
    group1_at=group0_at+group0_size
    group1_size=16+2*13+2*4
    offset=group1_at+group1_size
    raw+=struct.pack('<4I',0,3,0,0)
    raw+=b'A.pcx\0'.ljust(13,b'\0')+b'B.pcx\0'.ljust(13,b'\0')+b'A.pcx\0'.ljust(13,b'\0')
    raw+=struct.pack('<3I',offset,offset+32,offset)
    raw+=struct.pack('<4I',1,2,0,0)
    raw+=b'A.pcx\0'.ljust(13,b'\0')+b'C.pcx\0'.ljust(13,b'\0')
    raw+=struct.pack('<2I',offset,offset+64)
    raw+=struct.pack('<6I2i',32,0,60,60,50,50,5,5)*3
    return bytes(raw)


class SequenceRegistrationContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.definition=self.root/'TEST.def';self.definition.write_bytes(native_def())
        rows=[{'kind':'def','category':'creatures','identity':'TEST','group':0,'frame':i,
               'canvas':[60,60],'crop':[50,50],'anchor':[5,5],
               'resource':('B.pcx' if i==1 else 'A.pcx')} for i in range(3)]
        rows.extend([{'kind':'def','category':'creatures','identity':'TEST','group':1,'frame':0,
                      'canvas':[60,60],'crop':[50,50],'anchor':[5,5],'resource':'A.pcx'},
                     {'kind':'def','category':'creatures','identity':'TEST','group':1,'frame':1,
                      'canvas':[60,60],'crop':[50,50],'anchor':[5,5],'resource':'C.pcx'}])
        self.inventory=self.root/'inventory.json.gz'
        self.inventory.write_bytes(gzip.compress(json.dumps({'rows':rows}).encode()))
        self.standard=self.root/'standard.json';self.standard.write_text('{"version":"test"}')
        self.a=self.root/'raw-a.png';self.b=self.root/'raw-b.png'
        for p,wing in [(self.a,False),(self.b,True)]:
            im=Image.new('RGBA',(220,220));im.paste((90,120,200,255),(90,80,130,160))
            if wing:im.paste((20,80,240,255),(50,50,170,70))
            im.putpixel((89,100),(33,22,11,1));im.save(p)
        self.native_guides=self.root/'TEST';self.native_guides.mkdir()
        for frame in range(2):
            guide=Image.new('RGBA',(60,60),(0,0,0,0));guide.paste((90,120,200,255),(20,10,40,50))
            guide.save(self.native_guides/f'0_{frame}.png')
        guide=Image.new('RGBA',(60,60),(0,0,0,0));guide.paste((90,120,200,255),(20,10,40,50))
        guide.save(self.native_guides/'1_1.png')
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

    def upgrade_to_v2_native_crop(self):
        self.data['schema']=sequence.SCHEMA_V2
        self.data['output_layout']='native-crop'
        contract=self.data['sequence_contract']
        contract['schema']=sequence.CONTRACT_V2
        contract['native_crop_origin_policy']='official_binary_def_origin_in_native_canvas_coordinates'
        contract['root_motion_policy']={'0':'fixed'}
        contract['appendage_contract']={
            'schema':sequence.APPENDAGE_CONTRACT,'identity':'TEST',
            'scope':'test humanoid standing frames',
            'expected_counts':{'arms':2,'hands':2,'legs':2,'feet':2},
            'required_skeleton_paths':{'left_arm':4,'right_arm':4,
                                       'left_leg':5,'right_leg':5},
            'required_connections':['arms_to_shoulders','hands_to_wrists',
                                    'legs_to_pelvis','feet_to_ankles']}
        for item in self.data['items']:
            item['limb_review']={
                'source_sha256':item['source']['sha256'],'reviewer':'assistant',
                'status':'VISUALLY_REVIEWED',
                'counts':{'arms':2,'hands':2,'legs':2,'feet':2},
                'connections':{'arms_to_shoulders':'CONNECTED','hands_to_wrists':'CONNECTED',
                               'legs_to_pelvis':'CONNECTED','feet_to_ankles':'CONNECTED'},
                'skeleton_paths':{'left_arm':[[80,90],[90,100],[95,110],[100,115]],
                                  'right_arm':[[140,90],[130,100],[125,110],[120,115]],
                                  'left_leg':[[100,120],[95,130],[90,145],[90,165],[100,170]],
                                  'right_leg':[[120,120],[125,130],[130,145],[130,165],[120,170]]}}
            frame=item['key'][3]
            item['root_trajectory_review']={
                'key':item['key'],'source':ref(self.native_guides/f'0_{frame}.png',[60,60]),
                'coordinate_space':'native_canvas_1x','method':'manual-official-1x-root-landmark',
                'annotation_author':'assistant','status':'VISUALLY_REVIEWED',
                'root_target_native_canvas':[30,40]}

    def test_native_crop_uses_official_origin_and_keeps_canvas_root_targets(self):
        self.upgrade_to_v2_native_crop()
        rows=self.resolve()
        for result,_ in rows:
            self.assertEqual(result['root_target_native_canvas'],[30,40])
            self.assertEqual(result['root_target_native_crop'],[25,35])
            self.assertEqual(result['geometry']['origin_1x'],[5,5])
            self.assertFalse(result['pixels_resampled'])

    def test_native_crop_handoff_is_not_mislabelled_as_full_canvas(self):
        self.upgrade_to_v2_native_crop();request=self.save()
        with patch.object(layout,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/test-sequence'
            layout.execute('run',request,out,'test-sequence')
            rendered=json.loads(Path(sequence.handoff(request,out)['native_review_manifest']).read_bytes())
            self.assertTrue(all(x['source_layout']=='native-crop' for x in rendered['items']))
            self.assertTrue(all(x['output_layout']=='native-crop' and x['render_scales']==[3]
                                for x in rendered['items']))

    def test_native_crop_requires_explicit_origin_and_anatomy_contracts(self):
        self.upgrade_to_v2_native_crop()
        self.data['sequence_contract'].pop('native_crop_origin_policy')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_CROP_ORIGIN_POLICY_REQUIRED'):self.resolve()
        self.upgrade_to_v2_native_crop()
        self.data['items'][0]['limb_review']['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'SEQUENCE_APPENDAGE_VISUAL_REVIEW_REQUIRED'):self.resolve()

    def test_native_crop_blocks_limb_count_connection_and_idle_root_errors(self):
        self.upgrade_to_v2_native_crop()
        self.data['items'][0]['limb_review']['counts']['arms']=3
        with self.assertRaisesRegex(ValueError,'SEQUENCE_APPENDAGE_COUNT_MISMATCH'):self.resolve()
        self.upgrade_to_v2_native_crop()
        self.data['items'][0]['limb_review']['connections']['feet_to_ankles']='UNCERTAIN'
        with self.assertRaisesRegex(ValueError,'SEQUENCE_APPENDAGE_CONNECTION_UNRESOLVED'):self.resolve()
        self.upgrade_to_v2_native_crop()
        self.data['items'][0]['limb_review']['skeleton_paths'].pop('left_arm')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_SKELETON_PATH_SET_MISMATCH'):self.resolve()
        self.upgrade_to_v2_native_crop()
        self.data['items'][1]['root_offset_native']=[0,-1]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_FIXED_ROOT_MUST_NOT_MOVE'):self.resolve()

    def test_fixed_root_requires_official_1x_landmark_at_the_shared_anchor(self):
        self.upgrade_to_v2_native_crop()
        self.data['items'][1].pop('root_trajectory_review')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ROOT_TRAJECTORY_REVIEW_REQUIRED'):self.resolve()
        self.upgrade_to_v2_native_crop()
        self.data['items'][1]['root_trajectory_review']['root_target_native_canvas']=[31,40]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ROOT_TRAJECTORY_TARGET_DIFFERS_FROM_LAYOUT'):self.resolve()

    def test_native_crop_allows_sha_bound_cross_group_offset_aliases(self):
        self.upgrade_to_v2_native_crop()
        owner=json.loads(json.dumps(self.data['items'][1]))
        owner['key']=['def','TEST',1,1]
        owner['source']=ref(self.b,[220,220])
        owner['measurement']['image_sha256']=owner['source']['sha256']
        owner['limb_review']['source_sha256']=owner['source']['sha256']
        owner['root_trajectory_review']['key']=owner['key']
        owner['root_trajectory_review']['source']=ref(self.native_guides/'1_1.png',[60,60])
        self.data['expected_slots']=[['def','TEST',1,1]]
        self.data['items']=[owner]
        contract=self.data['sequence_contract']
        contract['groups']=[1]
        contract['logical_scope']=[['def','TEST',1,0],['def','TEST',1,1]]
        contract['root_motion_policy']={'1':'fixed'}
        contract['aliases']=[{'key':['def','TEST',1,0],
                              'owner_key':['def','TEST',0,0],
                              'source':ref(self.a,[220,220]),
                              'owner_source':ref(self.a,[220,220])}]
        result=self.resolve()[0][0]
        self.assertEqual(result['root_target_native_canvas'],[30,40])
        self.assertEqual(result['root_target_native_crop'],[25,35])

    def test_native_crop_rejects_unbound_external_alias_sources(self):
        self.upgrade_to_v2_native_crop()
        owner=json.loads(json.dumps(self.data['items'][1]));owner['key']=['def','TEST',1,1]
        owner['source']=ref(self.b,[220,220]);owner['measurement']['image_sha256']=owner['source']['sha256']
        owner['limb_review']['source_sha256']=owner['source']['sha256']
        owner['root_trajectory_review']['key']=owner['key']
        owner['root_trajectory_review']['source']=ref(self.native_guides/'1_1.png',[60,60])
        self.data['expected_slots']=[['def','TEST',1,1]];self.data['items']=[owner]
        contract=self.data['sequence_contract'];contract['groups']=[1]
        contract['logical_scope']=[['def','TEST',1,0],['def','TEST',1,1]]
        contract['root_motion_policy']={'1':'fixed'}
        contract['aliases']=[{'key':['def','TEST',1,0],
                              'owner_key':['def','TEST',0,0],
                              'source':ref(self.a,[220,220])}]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_EXTERNAL_ALIAS_OWNER_UNBOUND'):self.resolve()

    def test_explicit_root_requires_sha_bound_official_1x_native_landmarks(self):
        self.upgrade_to_v2_native_crop()
        self.data['sequence_contract']['root_motion_policy']={'0':'explicit'}
        for index,item in enumerate(self.data['items']):
            item['root_offset_native']=[index,0]
            item['root_trajectory_review']={
                'key':item['key'],'source':ref(self.native_guides/f'0_{index}.png',[60,60]),
                'coordinate_space':'native_canvas_1x','method':'manual-official-1x-root-landmark',
                'annotation_author':'assistant','status':'VISUALLY_REVIEWED',
                'root_target_native_canvas':[30+index,40]}
        rows=self.resolve()
        self.assertEqual(rows[0][0]['root_target_native_canvas'],[30,40])
        self.assertEqual(rows[1][0]['root_target_native_canvas'],[31,40])
        self.data['items'][1].pop('root_trajectory_review')
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ROOT_TRAJECTORY_REVIEW_REQUIRED'):self.resolve()

    def test_explicit_root_annotation_must_equal_frozen_offset(self):
        self.upgrade_to_v2_native_crop()
        self.data['sequence_contract']['root_motion_policy']={'0':'explicit'}
        for index,item in enumerate(self.data['items']):
            item['root_trajectory_review']={
                'key':item['key'],'source':ref(self.native_guides/f'0_{index}.png',[60,60]),
                'coordinate_space':'native_canvas_1x','method':'manual-official-1x-root-landmark',
                'annotation_author':'assistant','status':'VISUALLY_REVIEWED',
                'root_target_native_canvas':[30,40]}
        self.data['items'][1]['root_offset_native']=[0,1]
        with self.assertRaisesRegex(ValueError,'SEQUENCE_ROOT_TRAJECTORY_TARGET_DIFFERS_FROM_LAYOUT'):self.resolve()

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

    def test_candidate_master_supports_operation_identity_subdirectory(self):
        self.upgrade_to_v2_native_crop()
        candidate_root=self.root/'build/experiments/creature-master-set'
        source_path=candidate_root/'candidate-op'/'TEST'/'0_0.png'
        source_path.parent.mkdir(parents=True)
        Image.open(self.a).save(source_path)
        source_ref=ref(source_path,[220,220])
        self.data['items'][0]['source']=source_ref
        self.data['items'][0]['measurement']['image_sha256']=source_ref['sha256']
        self.data['items'][0]['limb_review']['source_sha256']=source_ref['sha256']
        self.data['sequence_contract']['aliases'][0]['source']=source_ref
        with patch.object(sequence,'CREATURE_MASTER_CANDIDATE_ROOT',candidate_root):
            self.assertEqual(self.resolve()[0][0]['status'],'LOSSLESS_LAYOUT_READY')

    def test_candidate_master_requires_exact_logical_png_name(self):
        self.upgrade_to_v2_native_crop()
        candidate_root=self.root/'build/experiments/creature-master-set'
        source_path=candidate_root/'candidate-op'/'TEST'/'redraw.png'
        source_path.parent.mkdir(parents=True)
        Image.open(self.a).save(source_path)
        source_ref=ref(source_path,[220,220])
        self.data['items'][0]['source']=source_ref
        self.data['items'][0]['measurement']['image_sha256']=source_ref['sha256']
        self.data['items'][0]['limb_review']['source_sha256']=source_ref['sha256']
        self.data['sequence_contract']['aliases'][0]['source']=source_ref
        with patch.object(sequence,'CREATURE_MASTER_CANDIDATE_ROOT',candidate_root):
            with self.assertRaisesRegex(ValueError,'SEQUENCE_CANDIDATE_OUTSIDE_MASTER_SET_OPERATION'):
                self.resolve()

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
