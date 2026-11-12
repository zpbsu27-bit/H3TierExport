"""Batch QA must separate deterministic faults, texture risks and unknown anatomy."""
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import art_batch_quality as mod

def ref(p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

class BatchQualityContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.masters=self.root/'masters';self.unit=self.masters/'UNIT';self.unit.mkdir(parents=True)
        self.inv=self.root/'inventory.json.gz';rows=[]
        for f in range(3):
            rows.append({'kind':'def','identity':'UNIT','group':0,'frame':f,'canvas':[32,32],'crop':[30,30],'anchor':[1,1]})
            im=Image.new('RGBA',(96,96));im.paste((150+f,45,15,255),(20,10,76,86));im.save(self.unit/f'0_{f}.png')
        self.inv.write_bytes(gzip.compress(json.dumps({'rows':rows}).encode()))
        self.standard=self.root/'standard.json';self.standard.write_text('{"version":"fixture"}')
        self.profile=self.root/'profile.json';self.pdata={'schema':mod.common.PROFILE,'preset':'creature-actions',
            'replaces_canonical_art_standard':False,'native_inventory':ref(self.inv),
            'art_standard':{**ref(self.standard),'version':'fixture'},
            'expected_slots':[['def','UNIT',0,f] for f in range(3)],'rules':{}}
        self.manifest=self.root/'manifest.json'

    def freeze(self):
        self.profile.write_text(json.dumps(self.pdata))
        self.manifest.write_text(json.dumps(mod.freeze(ref(self.profile),self.masters,'fixture')))
        return ref(self.manifest)

    def analyze(self):return mod.analyze(self.freeze())

    def test_exact_names_do_not_imply_anatomy_or_art_pass(self):
        r=self.analyze();self.assertEqual(r['scope_count'],3);self.assertFalse(r['art_approved'])
        self.assertEqual(r['overall'],'TECHNICAL_CHECKS_FINISHED')
        self.assertIn('body_scale',r['items'][0]['unmeasured'])
        self.assertIn('anatomy_and_action_semantics',r['items'][0]['unmeasured'])

    def test_selected_creature_form_directory_uses_root_level_official_slots(self):
        form=self.root/'UNIT_基础形态';self.unit.rename(form);self.unit=form;self.masters=form
        report=self.analyze()
        self.assertEqual(report['scope_count'],3)
        self.assertTrue(all(row['source'] is not None for row in report['items']))
        self.assertTrue(all('MISSING_OFFICIAL_SLOT_FILE' not in row['errors'] for row in report['items']))

    def test_prepared_or_formal_three_x_folder_is_not_a_mother_source(self):
        with self.assertRaisesRegex(ValueError,'MASTER_SOURCE_IS_PREPARED_OR_FORMAL_3X'):
            mod.source_identity_directory(self.root/'codex-3X','TEST_UNIT')
        with self.assertRaisesRegex(ValueError,'MASTER_SOURCE_IS_PREPARED_OR_FORMAL_3X'):
            mod.source_identity_directory(self.root/'codex-prepared'/'frame-set','TEST_UNIT')

    def test_missing_and_extra_official_names_are_reported_together(self):
        (self.unit/'0_0.png').rename(self.unit/'def_UNIT_0_0.png')
        r=self.analyze();self.assertEqual(r['overall'],'ERRORS_FOUND')
        self.assertIn('MISSING_OFFICIAL_SLOT_FILE',r['items'][0]['errors'])
        self.assertEqual(len(r['extra_pngs']),1)

    def test_directory_change_after_freeze_blocks_replay(self):
        pin=self.freeze();Image.new('RGBA',(32,32)).save(self.unit/'wrong.png')
        with self.assertRaisesRegex(ValueError,'REFREEZE'):mod.analyze(pin)

    def test_explicit_subset_does_not_label_other_official_frames_as_extra(self):
        self.pdata['expected_slots']=self.pdata['expected_slots'][:1]
        r=self.analyze();self.assertEqual(r['extra_pngs'],[])
        self.assertEqual(len(r['out_of_scope_official_pngs']),2)
        self.assertEqual(r['overall'],'TECHNICAL_CHECKS_FINISHED')

    def test_non_slot_references_are_risks_but_impossible_slot_names_are_errors(self):
        (self.unit/'approved_static_master.png').write_bytes((self.unit/'0_0.png').read_bytes())
        r=self.analyze();self.assertEqual(r['overall'],'RISKS_FOUND');self.assertEqual(len(r['extra_pngs']),1)
        (self.unit/'90_90.png').write_bytes((self.unit/'0_0.png').read_bytes())
        r=self.analyze();self.assertEqual(r['overall'],'ERRORS_FOUND');self.assertEqual(len(r['unexpected_official_frame_pngs']),1)

    def test_rgba_with_all_opaque_pixels_is_not_transparent(self):
        Image.new('RGBA',(96,96),(240,240,240,255)).save(self.unit/'0_0.png')
        r=self.analyze()['items'][0]
        self.assertIn('NO_TRUE_TRANSPARENT_BACKGROUND',r['errors'])
        self.assertNotIn('POSSIBLE_BAKED_CHECKERBOARD',r['risks'])

    def test_baked_grid_and_viewer_grid_are_distinct(self):
        yy,xx=np.indices((128,128));rgb=np.where(((xx//8+yy//8)%2)[:,:,None],210,250)
        a=np.concatenate([np.repeat(rgb,3,axis=2),np.full((128,128,1),255)],axis=2).astype('uint8')
        self.assertEqual(mod.checkerboard_background_diagnostic(a)['status'],'POSSIBLE_BAKED_CHECKERBOARD')
        a[:,:,3]=0
        self.assertEqual(mod.checkerboard_background_diagnostic(a)['status'],'NO_REPEATED_CORNER_PATTERN_DETECTED')

    def test_stripes_are_not_a_checkerboard(self):
        yy,xx=np.indices((128,128));v=np.where(xx//8%2,210,250)
        a=np.dstack([v,v,v,np.full_like(v,255)]).astype('uint8')
        self.assertEqual(mod.checkerboard_background_diagnostic(a)['status'],'NO_REPEATED_CORNER_PATTERN_DETECTED')

    def test_elongated_detached_alpha_component_is_review_only_and_read_only(self):
        image=Image.new('RGBA',(96,96))
        image.paste((150,45,15,255),(20,10,76,86))
        image.paste((180,70,25,255),(3,22,8,72))
        path=self.unit/'0_1.png';image.save(path)
        before=ref(path)
        item=self.analyze()['items'][1]
        self.assertEqual(item['errors'],[])
        self.assertIn('ELONGATED_DETACHED_ALPHA_COMPONENT_REVIEW',item['risks'])
        candidate=item['detached_component_candidates'][0]
        self.assertEqual(candidate['pixels'],250)
        self.assertEqual(candidate['bbox_ltrb'],[3,22,8,72])
        self.assertGreaterEqual(candidate['aspect_ratio'],3.0)
        self.assertTrue(candidate['diagnostic_only'])
        self.assertTrue(candidate['requires_visual_review'])
        self.assertFalse(candidate['automatic_rejection'])
        page=mod.quality_review_html(self.analyze())
        self.assertIn('detached_component_candidates',page)
        self.assertIn('ELONGATED_DETACHED_ALPHA_COMPONENT_REVIEW',page)
        self.assertEqual(ref(path),before)

    def test_corrupt_png_does_not_hide_other_items(self):
        (self.unit/'0_1.png').write_bytes(b'not png')
        r=self.analyze();self.assertEqual(len(r['items']),3)
        self.assertTrue(any(x.startswith('PNG_DECODE') for x in r['items'][1]['errors']))
        self.assertIn('pixels',r['items'][2])

    def test_ground_requires_opaque_not_creature_transparency(self):
        self.pdata['preset']='ground';r=self.analyze()
        self.assertIn('GROUND_NOT_FULLY_OPAQUE',r['items'][0]['errors'])
        self.assertNotIn('NO_TRUE_TRANSPARENT_BACKGROUND',r['items'][0]['errors'])

    def test_aliases_must_preserve_exact_bytes(self):
        self.pdata['exact_alias_groups']=[self.pdata['expected_slots'][:2]]
        r=self.analyze();self.assertIn('EXACT_NATIVE_ALIAS_BYTES_DIFFER',r['items'][0]['errors'])

    def test_colour_risk_does_not_modify_images(self):
        im=Image.new('RGBA',(96,96));im.paste((10,220,250,255),(20,10,76,86));im.save(self.unit/'0_2.png')
        before={p.name:ref(p) for p in self.unit.glob('*.png')}
        r=self.analyze();self.assertIn('COLOR_FAMILY_OUTLIER_DIAGNOSTIC',r['items'][2]['risks'])
        self.assertEqual(before,{p.name:ref(p) for p in self.unit.glob('*.png')})

    def test_material_reference_catches_whole_batch_colour_drift_hidden_by_its_median(self):
        approved=self.root/'material-reference.png';approved.write_bytes((self.unit/'0_0.png').read_bytes())
        for f in range(3):
            im=Image.new('RGBA',(96,96));im.paste((10,210+f,250,255),(20,10,76,86));im.save(self.unit/f'0_{f}.png')
        self.pdata['color_references']=[{'identity':'UNIT','source':ref(approved),'selection_author':'assistant'}]
        result=self.analyze()
        for row in result['items']:
            self.assertNotIn('COLOR_FAMILY_OUTLIER_DIAGNOSTIC',row['risks'])
            self.assertIn('REFERENCE_MATERIAL_COLOR_MISMATCH',row['risks'])
            self.assertTrue(row['reference_material_color']['identity_semantics_not_measured'])

    def test_multiple_approved_material_references_do_not_force_one_palette(self):
        self.pdata['color_references']=[{'identity':'UNIT','source':ref(self.unit/'0_0.png'),'selection_author':'user'},
            {'identity':'UNIT','source':ref(self.unit/'0_1.png'),'selection_author':'assistant'}]
        result=self.analyze()
        self.assertTrue(all('REFERENCE_MATERIAL_COLOR_MISMATCH' not in row['risks'] for row in result['items']))

    def test_material_reference_sha_drift_blocks_instead_of_guessing_new_palette(self):
        approved=self.root/'material-reference.png';approved.write_bytes((self.unit/'0_0.png').read_bytes())
        self.pdata['color_references']=[{'identity':'UNIT','source':ref(approved),'selection_author':'assistant'}]
        approved.write_bytes((self.unit/'0_1.png').read_bytes())
        with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):self.analyze()

    def test_check_writes_nothing_run_and_verify_do_not_create_pngs(self):
        pin=self.freeze();out=self.root/'build/experiments/batch-quality/fixture'
        with patch.object(mod,'PRODUCT',self.root):
            self.assertEqual(mod.execute('check',pin,out,'fixture')['status'],'CHECKED_NO_WRITES');self.assertFalse(out.exists())
            mod.execute('run',pin,out,'fixture');mod.execute('verify',pin,out,'fixture')
        self.assertEqual(list(out.rglob('*.png')),[])

    def test_quality_review_uses_one_global_pixel_scale_for_original_canvases(self):
        Image.new('RGBA',(128,64),(120,80,50,255)).save(self.unit/'0_1.png')
        pin=self.freeze();out=self.root/'build/experiments/batch-quality/fixture'
        before={p.name:ref(p) for p in self.unit.glob('*.png')}
        with patch.object(mod,'PRODUCT',self.root):
            mod.execute('run',pin,out,'fixture')
            page=(out/'quality-report.html').read_text(encoding='utf-8')
            self.assertIn('id="review-scale"',page)
            self.assertIn('data-native-width="96"',page)
            self.assertIn('data-native-width="128"',page)
            self.assertIn('data-native-height="64"',page)
            self.assertIn('所有帧使用同一个显示倍率',page)
            self.assertIn('身份/性别',page)
            self.assertIn('body_scale',page)
            self.assertIn('NOT_MEASURED',page)
            self.assertNotIn('object-fit:contain',page)
            self.assertNotIn('height:260px',page)
            self.assertEqual(mod.execute('verify',pin,out,'fixture')['status'],
                             'VERIFIED_READ_ONLY_QUALITY_REPORT')
        self.assertEqual(before,{p.name:ref(p) for p in self.unit.glob('*.png')})
        self.assertEqual(list(out.rglob('*.png')),[])

    def test_review_html_tamper_is_rejected_by_verify(self):
        pin=self.freeze();out=self.root/'build/experiments/batch-quality/fixture'
        with patch.object(mod,'PRODUCT',self.root):
            mod.execute('run',pin,out,'fixture')
            page=out/'quality-report.html'
            page.write_text(page.read_text(encoding='utf-8')+'<!-- changed -->',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'QUALITY_HTML_REPLAY_MISMATCH'):
                mod.execute('verify',pin,out,'fixture')

    def add_body_evidence(self):
        receipt=self.root/'native-review.json'
        receipt.write_text(json.dumps({'schema':'h3.native-review.batch-report.v1',
            'official_inventory':{**ref(self.inv),'geometry_verified_by_companion':True},'items':[
            {'key':['def','UNIT',0,0],'source_sha256':ref(self.unit/'0_0.png')['sha256'],
             'source_pixels':[96,96],'registered_pixels':[96,96],'registration_offset':[0,0],
             'targets':{'canvas_1x':[32,32],'crop_1x':[30,30],'origin_1x':[1,1]},
             'outputs':{'candidate_3x':{'path':str(self.unit/'0_0.png'),'png_sha256':ref(self.unit/'0_0.png')['sha256']}},
             'source_view_proof':{'all_positive_preserved':True}}]}))
        self.pdata['native_review_report']=ref(receipt)
        self.pdata['directions']={'side':{'pose_class':'standing_side','primary_metric':'torso',
            'lengths_native':{'torso':25},'primary_tolerance_fraction':.08}}
        self.pdata['body_measurements']=[{'key':['def','UNIT',0,0],
            'image_sha256':ref(self.unit/'0_0.png')['sha256'],'profile_direction':'side',
            'pose_class':'standing_side','coordinate_space':'source_pixels',
            'method':'visual-anatomical-landmarks','paths':{'torso':[[48,10],[48,85]]}}]

    def test_body_scale_uses_actual_registration_and_anatomy_not_canvas_bbox(self):
        self.add_body_evidence();r=self.analyze()['items'][0]
        self.assertEqual(r['body_scale']['status'],'MEASURED_WITHIN_PROFILE')
        self.assertNotIn('body_scale',r['unmeasured'])
        self.assertIn('anatomy_and_action_semantics',r['unmeasured'])

    def test_stale_anatomical_annotations_cannot_silently_pass(self):
        self.add_body_evidence();self.pdata['body_measurements'][0]['image_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'BODY_MEASUREMENT_SOURCE_SHA_DRIFT'):self.analyze()

    def test_cross_pose_measurements_cannot_be_forced_equal(self):
        self.add_body_evidence();self.pdata['body_measurements'][0]['pose_class']='lying_down'
        with self.assertRaisesRegex(ValueError,'COMPARABLE_POSE'):self.analyze()

    def test_foggy_semi_transparent_core_is_detected_as_risk(self):
        im=Image.new('RGBA',(96,96),(0,0,0,0))
        im.paste((150,45,15,210),(20,10,76,86))
        im.save(self.unit/'0_0.png')
        r=self.analyze()['items'][0]
        self.assertIn('FOGGY_SEMI_TRANSPARENT_CORE_RISK',r['risks'])
        self.assertEqual(r['interior_core_opacity_diagnostic']['status'],'FOGGY_SEMI_TRANSPARENT_CORE_RISK')

    def test_sequence_qa_and_ghost_free_stabilizer_contract(self):
        import creature_sequence_qa as cqa
        raw_dir=self.root/'raw_unit';target_dir=self.root/'target_unit';backup_dir=self.root/'backup_batch'
        raw_dir.mkdir(parents=True)
        for i in range(5):
            im=Image.new('RGBA',(128,128),(0,0,0,0))
            # Main torso+legs body (x=40..88, y=24..116)
            im.paste((140,80,40,255),(40,24,88,116))
            if i==0:
                # Frame 0 has a thin lowered weapon tip attached to lower-left hip (x=34..40, y=75..105)
                im.paste((200,200,220,255),(34,75,40,105))
            else:
                # Frames 1..4 lift the weapon up to upper-left shoulder (x=34..40, y=24+i..50+i)
                im.paste((200,200,220,255),(34,24+i,40,50+i))
            im.save(raw_dir/f'0_{i}.png')
        receipt=cqa.stabilize_unit_sequence(raw_dir,target_dir,identity='TESTAVW',backup_dir=backup_dir,expected_keyframes=5)
        self.assertEqual(receipt['qa_report']['error_count'],0)
        self.assertEqual(receipt['qa_report']['status'],'RISK')  # Only MISSING_VCMI_ANIMATION_JSON risk before JSON is written
        self.assertIs(receipt['pipeline']['pixel_composite_from_f0'],False)
        self.assertIs(receipt['pipeline']['alpha_modified'],False)
        with self.assertRaisesRegex(RuntimeError,'F0_PIXEL_COMPOSITE_FORBIDDEN'):
            cqa.apply_static_lower_body_lock_v5()
        # Rigid registration must not alter pixel values of an unshifted generated frame
        raw1=np.array(Image.open(raw_dir/'0_1.png').convert('RGBA'))
        out1=np.array(Image.open(target_dir/'0_1.png').convert('RGBA'))
        self.assertTrue(np.array_equal(raw1,out1) or receipt['written_frames'][1]['registration']['scale']!=1.0
                        or receipt['written_frames'][1]['registration']['dx']!=0 or receipt['written_frames'][1]['registration']['dy']!=0)
        # Manifest with banned nod choreography is rejected; clean choreography passes
        choreo={'action_archetype_id':'t','action_family':'t','concept':'t',
                'keyframes':[{'frame':f'0_{i}.png','phase':'p','action':'举起武器'} for i in range(5)]}
        choreo['keyframes'][2]['action']='骑士点头致意'
        cqa.write_unit_manifest(target_dir,'TESTAVW','测试','测试',choreo,receipt,backup_dir)
        qa_nod=cqa.inspect_unit_sequence_qa(target_dir,identity='TESTAVW',expected_keyframes=5)
        self.assertTrue(any('BANNED_HEAD_NOD_OR_SHAKE_CHOREOGRAPHY' in e for e in qa_nod['errors']))
        choreo['keyframes'][2]['action']='剑举过头顶（禁止点头）'
        cqa.write_unit_manifest(target_dir,'TESTAVW','测试','测试',choreo,receipt,backup_dir)
        qa_ok=cqa.inspect_unit_sequence_qa(target_dir,identity='TESTAVW',expected_keyframes=5)
        self.assertFalse(any('BANNED_HEAD' in e or 'LEGACY_F0' in e for e in qa_ok['errors']))
        # Legacy manifests without the rigid pipeline contract are rejected
        legacy=json.loads((target_dir/'TESTAVW.json').read_text(encoding='utf-8'));legacy.pop('pipeline')
        (target_dir/'TESTAVW.json').write_text(json.dumps(legacy,ensure_ascii=False),encoding='utf-8')
        qa_legacy=cqa.inspect_unit_sequence_qa(target_dir,identity='TESTAVW',expected_keyframes=5)
        self.assertTrue(any('LEGACY_F0_PIXEL_COMPOSITE' in e for e in qa_legacy['errors']))
        # Verify Frame 1 does NOT have Frame 0's lowered weapon ghosting at x=36, y=90
        f1=np.array(Image.open(target_dir/'0_1.png'))
        self.assertEqual(int(f1[90,36,3]),0)
        # Re-run stabilize to verify existing files are moved to backup_dir (NO DIRECT OVERWRITE)
        receipt2=cqa.stabilize_unit_sequence(raw_dir,target_dir,identity='TESTAVW',backup_dir=backup_dir,expected_keyframes=5)
        self.assertGreaterEqual(len(receipt2['backups_moved']),6)
        # Verify detached floating fragment anomaly is caught by inspect_unit_sequence_qa
        bad=Image.open(target_dir/'0_2.png')
        bad.paste((250,50,50,255),(4,10,16,30))
        bad.save(target_dir/'0_2.png')
        qa_bad=cqa.inspect_unit_sequence_qa(target_dir,identity='TESTAVW',expected_keyframes=5)
        self.assertEqual(qa_bad['status'],'ERROR')
        self.assertTrue(any('DETACHED_FLOATING_FRAGMENT_OR_LIMB_ANOMALY' in e for e in qa_bad['errors']))

if __name__=='__main__':unittest.main()
