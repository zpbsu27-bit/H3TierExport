"""Read-only complete-slot scale reporting and draft protocol regression."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'scripts'))
import creature_scale_reports as scale


class ScaleReports(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scale report synthetic ');self.root=Path(self.temp.name)
        self.product=patch.object(scale,'PRODUCT',self.root);self.product.start()
        self.identity='TEST_UNIT';self.masters=self.root/self.identity;self.masters.mkdir();self.keys=[['def',self.identity,g,0] for g in (0,1)]
        self.geometry={'canvas_1x':[32,32],'crop_1x':[16,16],'origin_1x':[8,8]}
        rows=[{'kind':'def','category':'creatures','identity':self.identity,'group':g,'frame':0,'canvas':[32,32],
            'crop':[16,16],'anchor':[8,8],'resource':f'test{g}.pcx','native_lod':'H3ab_spr.lod'} for g in (0,1)]
        self.inventory=self.json('native inventory.json',{'rows':rows});self.images=[]
        for g in (0,1):
            p=self.masters/f'{g}_0.png';im=Image.new('RGBA',(64,64));
            for y in range(8,40+g*8):
                for x in range(12,32):im.putpixel((x,y),(120,80+g,50,255))
            im.save(p);self.images.append(scale.reference(p))

    def tearDown(self):self.product.stop();self.temp.cleanup()

    def json(self,name,obj):
        p=self.root/name;p.write_text(json.dumps(obj),encoding='utf-8');return scale.reference(p)

    def freeze(self,mode='audit',context=None,operation='synthetic',category='infantry',masters=True):
        args=argparse.Namespace(identity=self.identity,operation_id=operation,category=category,inventory=self.inventory['path'],
            inventory_sha256=self.inventory['sha256'],masters_dir=str(self.masters) if masters else None,
            faction=None,form=None,context=context['path'] if context else None,context_sha256=context['sha256'] if context else None)
        result=scale.freeze(args,mode);return result['manifest'],self.root/'build/experiments'/('scale-'+mode)/operation

    def context(self,landmarks=True,scope='whole_subject',sampling=True,pose='standing'):
        registrations=[];frames=[]
        for g in (0,1):
            receipt=self.json(f'work receipt {g}.json',{'schema':'h3.motion.receipt.v1','target_scale':4,
                'png_sha256':self.images[g]['sha256'],'output_pixels':[64,64],'origin_at_target_scale':[32,32],
                'contract':{'native_entry':{'key':self.keys[g],'targets':self.geometry}}})
            registrations.append({'key':self.keys[g],'receipt':receipt})
            frames.append({'key':self.keys[g],'image_sha256':self.images[g]['sha256'],'coordinate_space':'source_pixels',
                'measurement_scope':scope,'pose_group':pose if g else 'standing','direction':'right','root_anchor_pixels':[20,40+g*8],
                'paths':{'head':[[20,8],[20,16]],'torso':[[20,16],[20,32]],'body':[[20,8],[20,40+g*8]]}})
        approval=self.json('sample approval.json',{'schema':'h3.creature.scale-sample-approval.v1','key':self.keys[0],
            'image_sha256':self.images[0]['sha256'],'status':'assistant_art_pass'})
        ctx={'schema':'h3.creature.scale-context.v1','approved_sample':{'key':self.keys[0],'image':self.images[0],'approval':approval}}
        if sampling:
            ctx['working_registrations']=registrations;ctx['approved_sample']['registration']=registrations[0]['receipt']
        if landmarks:ctx['landmarks']=self.json('anatomical landmarks.json',{'schema':scale.LANDMARKS,'frames':frames})
        return self.json('context.json',ctx)

    def test_draft_without_reference_does_not_guess_work_canvas_or_body(self):
        ref,_=self.freeze(mode='draft',masters=False);report=scale.make_report(scale.document(ref))
        self.assertEqual(report['profile']['status'],'DRAFT');self.assertIsNone(report['profile']['work_canvas_pixels'])
        self.assertTrue(all(x['body']['status']=='NOT_MEASURED' for x in report['slots']))
        self.assertEqual(report['summary']['missing_slots'],2)
        self.assertEqual(report['slots'][0]['native_lod'],'H3ab_spr.lod')

    def test_bbox_and_canvas_differences_do_not_become_K_or_body_scale(self):
        ref,_=self.freeze();r=scale.make_report(scale.document(ref))
        self.assertTrue(all(x['sampling']['K'] is None and x['body']['status']=='NOT_MEASURED' for x in r['slots']))
        self.assertNotEqual(r['slots'][0]['layout']['alpha_bbox16'],r['slots'][1]['layout']['alpha_bbox16'])
        self.assertFalse(r['automatic_image_resize']);self.assertFalse(r['sampling_K_from_alpha_bbox'])

    def test_postproduction_transition_review_is_explicitly_separate_from_scale_and_art_pass(self):
        ref,_=self.freeze();r=scale.make_report(scale.document(ref))
        self.assertEqual(r['postproduction_motion_review']['status'],'NOT_PROVIDED')
        self.assertEqual(r['summary']['motion_review_status'],'NOT_PROVIDED')
        self.assertFalse(r['postproduction_motion_review']['automatic_art_approval'])
        self.assertIn('动作连续性事后复核',scale.html_report(r))

    def test_bound_landmarks_and_K_compare_whole_group_to_shared_reference(self):
        ref,_=self.freeze(context=self.context());r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][1]['body']['ratio_to_approved_reference'],1.25)
        self.assertEqual(r['action_groups'][1]['median_deviation_percent'],25)
        self.assertEqual(r['action_groups'][1]['measured_landmark_slots'],1)
        self.assertEqual(r['profile']['status'],'MEASURED_REFERENCE_DRAFT');self.assertFalse(r['automatic_art_approval'])

    def test_landmarks_without_sampling_do_not_compare_absolute_body_size(self):
        ref,_=self.freeze(context=self.context(sampling=False));r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][0]['body']['status'],'MEASURED_SOURCE_PIXELS_ONLY')
        self.assertIsNone(r['slots'][1]['body']['ratio_to_approved_reference'])

    def test_source_pixel_reference_compares_same_resolution_as_diagnostic_only(self):
        context=self.context(sampling=False);data=scale.document(context)
        data['source_pixel_reference']={'key':self.keys[0],'image_sha256':self.images[0]['sha256']}
        context=self.json('source-pixel reference context.json',data)
        ref,_=self.freeze(context=context);r=scale.make_report(scale.document(ref));row=r['slots'][1]
        comparison=row['source_pixel_comparison']
        self.assertEqual(row['body']['status'],'MEASURED_SOURCE_PIXELS_ONLY')
        self.assertIsNone(row['body']['ratio_to_approved_reference'])
        self.assertEqual(comparison['status'],'DIAGNOSTIC_ONLY_COMPARABLE_SOURCE_PIXELS')
        self.assertEqual(comparison['ratio_to_source_pixel_reference'],1.25)
        self.assertEqual(comparison['deviation_percent_from_source_pixel_reference'],25)
        self.assertFalse(comparison['K_bound']);self.assertFalse(comparison['automatic_resize'])
        self.assertFalse(comparison['art_approval'])
        self.assertEqual(r['action_groups'][1]['source_pixel_comparable_landmark_slots'],1)
        self.assertEqual(r['action_groups'][1]['source_pixel_median_deviation_percent'],25)
        self.assertIn('源像素偏差%（仅诊断）',scale.html_report(r))
        self.assertIn('DIAGNOSTIC_ONLY_COMPARABLE_SOURCE_PIXELS',scale.html_report(r))
        self.assertIn('源像素中位偏差%（仅诊断）',scale.markdown(r))

    def test_source_pixel_reference_does_not_compare_different_resolutions(self):
        context=self.context(sampling=False);data=scale.document(context)
        image=Path(self.images[1]['path']);larger=Image.new('RGBA',(128,128))
        larger.paste((120,81,50,255),(24,16,64,96));larger.save(image);self.images[1]=scale.reference(image)
        marks=scale.document(data['landmarks']);marks['frames'][1]['image_sha256']=self.images[1]['sha256']
        data['landmarks']=self.json('different-resolution landmarks.json',marks)
        data['source_pixel_reference']={'key':self.keys[0],'image_sha256':self.images[0]['sha256']}
        context=self.json('different-resolution source-pixel context.json',data)
        ref,_=self.freeze(context=context);r=scale.make_report(scale.document(ref));comparison=r['slots'][1]['source_pixel_comparison']
        self.assertEqual(r['slots'][1]['body']['status'],'MEASURED_SOURCE_PIXELS_ONLY')
        self.assertEqual(comparison['status'],'NOT_COMPARABLE_SOURCE_RESOLUTION_DIFFERS')
        self.assertIsNone(comparison['ratio_to_source_pixel_reference'])

    def test_source_pixel_reference_requires_matching_pose_direction_and_scope(self):
        context=self.context(sampling=False,pose='attacking');data=scale.document(context)
        data['source_pixel_reference']={'key':self.keys[0],'image_sha256':self.images[0]['sha256']}
        context=self.json('different-pose source-pixel context.json',data)
        ref,_=self.freeze(context=context);r=scale.make_report(scale.document(ref));comparison=r['slots'][1]['source_pixel_comparison']
        self.assertEqual(comparison['status'],'NOT_COMPARABLE_POSE_DIRECTION_OR_SCOPE_DIFFERS')
        self.assertIsNone(comparison['ratio_to_source_pixel_reference'])

    def test_source_pixel_reference_sha_is_bound_to_current_slot(self):
        context=self.context(sampling=False);data=scale.document(context)
        data['source_pixel_reference']={'key':self.keys[0],'image_sha256':'0'*64}
        ref,_=self.freeze(context=self.json('stale source-pixel reference context.json',data))
        with self.assertRaisesRegex(ValueError,'SCALE_SOURCE_PIXEL_REFERENCE_SHA_DRIFT'):
            scale.make_report(scale.document(ref))

    def test_different_pose_does_not_generate_a_body_scale_warning(self):
        ref,_=self.freeze(context=self.context(pose='attacking'));r=scale.make_report(scale.document(ref))
        self.assertIsNone(r['slots'][1]['body']['ratio_to_approved_reference'])
        self.assertEqual(r['action_groups'][1]['reference_comparison_status'],'NOT_COMPARABLE')

    def test_mounted_unit_cannot_be_measured_from_rider_only(self):
        ref,_=self.freeze(context=self.context(scope='rider_mount_and_equipment'),category='mounted');r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][0]['body']['status'],'NOT_MEASURED')
        self.assertIn('mount_axis',r['slots'][0]['body']['reason']);self.assertEqual(r['profile']['status'],'DRAFT')

    def test_missing_and_noncanonical_slots_remain_in_full_report(self):
        (self.masters/'1_0.png').rename(self.masters/f'def_{self.identity}_1_0__abcdef123456.png')
        ref,_=self.freeze();r=scale.make_report(scale.document(ref));self.assertEqual(r['summary']['noncanonical_slots'],1)
        self.assertEqual(r['summary']['expected_slots'],2)

    def test_reports_verify_without_modifying_images_or_creating_preview_PNGs(self):
        before={p.name:p.read_bytes() for p in self.masters.iterdir()};ref,out=self.freeze()
        result=scale.execute('audit','run',ref,out,'synthetic')
        self.assertEqual(result['images_written'],0)
        self.assertEqual(scale.execute('audit','verify',ref,out,'synthetic')['status'],'VERIFIED_READ_ONLY_REPORT')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.masters.iterdir()});self.assertEqual(list(out.glob('*.png')),[])
        self.assertIn('NOT_MEASURED',(out/'scale-report.md').read_text(encoding='utf-8'))

    def test_source_drift_and_directory_growth_require_new_snapshot(self):
        ref,out=self.freeze();Image.new('RGBA',(4,4)).save(self.masters/'unexpected.png')
        with self.assertRaisesRegex(ValueError,'DIRECTORY_CHANGED'):scale.execute('audit','check',ref,out,'synthetic')
        self.assertFalse(out.exists())

    def test_foreign_operation_and_changed_report_are_never_accepted(self):
        ref,out=self.freeze();out.mkdir(parents=True);(out/'user.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'FOREIGN_SCALE_OPERATION'):scale.execute('audit','run',ref,out,'synthetic')
        self.assertEqual((out/'user.txt').read_text(),'keep')

    def test_report_tampering_is_caught_by_verify(self):
        ref,out=self.freeze();scale.execute('audit','run',ref,out,'synthetic');(out/'scale-report.md').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):scale.execute('audit','verify',ref,out,'synthetic')

    def test_mode_and_output_directory_cannot_be_confused(self):
        ref,out=self.freeze()
        with self.assertRaisesRegex(ValueError,'OUTPUT_MUST'):scale.execute('draft','run',ref,out,'synthetic')
        with self.assertRaisesRegex(ValueError,'MODE_OR_ID'):scale.execute('draft','run',ref,self.root/'build/experiments/scale-draft/synthetic','synthetic')

    def test_native_frame_canvases_and_crops_are_independent(self):
        data=scale.document(self.inventory);data['rows'][1].update(canvas=[48,64],crop=[20,24],anchor=[10,22])
        self.inventory=self.json('native inventory.json',data)
        ref,_=self.freeze();r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][1]['geometry'],{'canvas_1x':[48,64],'crop_1x':[20,24],'origin_1x':[10,22]})
        self.assertNotEqual(r['slots'][0]['geometry'],r['slots'][1]['geometry'])

    def test_3x_status_is_reported_even_if_the_master_is_missing(self):
        Path(self.images[0]['path']).rename(self.root/'preserved raw parent.png')
        image=self.root/'formal3x fixture.png';Image.new('RGBA',(48,48),(120,80,50,255)).save(image);image_ref=scale.reference(image)
        receipt=self.json('formal 3x receipt.json',{'schema':'h3.motion.receipt.v1','target_scale':3,'png_sha256':image_ref['sha256'],
            'output_pixels':[48,48],'origin_3x':[24,24],'contract':{'native_entry':{'key':self.keys[0],'targets':self.geometry}}})
        context=self.json('context.json',{'schema':'h3.creature.scale-context.v1','deliveries_3x':[{'key':self.keys[0],'image':image_ref,'receipt':receipt}]})
        ref,_=self.freeze(context=context);r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][0]['master_status'],'MISSING')
        self.assertEqual(r['slots'][0]['formal_3x']['K'],3)
        self.assertIsNone(r['slots'][0]['sampling']['K'])

    def test_stale_landmarks_are_not_measured_or_silently_scaled(self):
        context=self.context();data=scale.document(context);marks=scale.document(data['landmarks'])
        marks['frames'][1]['image_sha256']='0'*64;data['landmarks']=self.json('anatomical landmarks.json',marks)
        context=self.json('context.json',data);ref,_=self.freeze(context=context);r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][1]['body']['status'],'NOT_MEASURED')
        self.assertIsNone(r['slots'][1]['body']['ratio_to_approved_reference'])

    def test_different_generated_resolution_is_not_confused_with_body_size(self):
        context=self.context();data=scale.document(context);marks=scale.document(data['landmarks'])
        p=Path(self.images[1]['path']);Image.new('RGBA',(128,128),(120,80,50,255)).save(p);self.images[1]=scale.reference(p)
        base=marks['frames'][0];entry=json.loads(json.dumps(base));entry.update(key=self.keys[1],image_sha256=self.images[1]['sha256'])
        entry['paths']={name:[[x*2,y*2] for x,y in points] for name,points in base['paths'].items()}
        entry['root_anchor_pixels']=[x*2 for x in base['root_anchor_pixels']];marks['frames'][1]=entry
        data['landmarks']=self.json('anatomical landmarks.json',marks)
        receipt=scale.document(data['working_registrations'][1]['receipt']);receipt.update(target_scale=8,
            output_pixels=[128,128],origin_at_target_scale=[64,64],png_sha256=self.images[1]['sha256'])
        data['working_registrations'][1]['receipt']=self.json('work receipt 1.json',receipt)
        ref,_=self.freeze(context=self.json('context.json',data));r=scale.make_report(scale.document(ref))
        self.assertEqual(r['slots'][1]['sampling']['K'],8)
        self.assertEqual(r['slots'][1]['body']['ratio_to_approved_reference'],1)

    def test_user_proposed_work_canvas_remains_a_draft_and_does_not_resize(self):
        context=self.json('context.json',{'schema':'h3.creature.scale-context.v1','proposed_layout':{
            'work_canvas_pixels':[2048,2048],'subject_occupancy_target':.7,'transparent_margin_target':[32,32,32,32]}})
        ref,_=self.freeze(mode='draft',context=context);r=scale.make_report(scale.document(ref))
        self.assertEqual(r['profile']['status'],'DRAFT');self.assertEqual(r['profile']['layout_source'],'USER_PROPOSAL_NOT_MEASURED')
        self.assertEqual(r['slots'][0]['layout']['pixels'],[64,64]);self.assertFalse(r['automatic_image_resize'])

    def test_report_receipt_cannot_be_edited_into_art_approval(self):
        ref,out=self.freeze();scale.execute('audit','run',ref,out,'synthetic')
        receipt=out/'scale-report-receipt.json';data=json.loads(receipt.read_bytes());data['art_approved']=True;receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'CANNOT_GRANT'):scale.execute('audit','verify',ref,out,'synthetic')

    def test_explicit_selected_form_directory_accepts_official_numeric_names(self):
        for folder in ('CGNOLL_基础形态','CGNOLM_升级形态','TEST_UNIT-custom-form'):
            with self.subTest(folder=folder):
                old=self.masters;new=self.root/folder;old.rename(new);self.masters=new
                try:
                    ref,_=self.freeze(operation='readable-'+str(len(folder))+str(len(list(self.root.iterdir()))))
                    report=scale.make_report(scale.document(ref))
                    self.assertEqual(report['summary']['present_slots'],2)
                    self.assertEqual(report['summary']['missing_slots'],0)
                    self.assertTrue(all(x['sampling']['K'] is None for x in report['slots']))
                finally:
                    new.rename(old);self.masters=old

    def test_selected_directory_does_not_admit_neighbor_or_nested_numeric_files(self):
        selected=self.root/'CGNOLL_基础形态'
        self.assertEqual(scale.source_key(selected/'0_0.png',self.identity,selected),self.keys[0])
        for other in (self.root/'OTHER'/'0_0.png',selected/'官方阴影'/'0_0.png'):
            self.assertIsNone(scale.source_key(other,self.identity,selected))

    def test_native_cli_exposes_both_draft_and_audit(self):
        exe=os.environ.get('H3TIEREXPORT_EXE')
        if not exe:self.skipTest('native CLI exercised by product qualification')
        for command in ('scale-draft','scale-audit'):
            result=subprocess.run([exe,command,'catalog','--python',sys.executable],capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['default_body_status'],'NOT_MEASURED')


if __name__=='__main__':unittest.main()
