"""Reusable repair contracts: synthetic inputs only, no user art approval."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'scripts'))
import asset_repair as repair


class AssetRepairContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='asset repair synthetic ');self.root=Path(self.temp.name)
        self.override=patch.object(repair,'PRODUCT',self.root);self.override.start()
        self.g={'canvas_1x':[32,32],'crop_1x':[32,32],'origin_1x':[0,0]}
        self.key=['def','TEST_ROAD',0,0]
        yy,xx=np.indices((128,128));rgba=np.full((128,128,4),255,np.uint8)
        rgba[:,:,0]=xx;rgba[:,:,1]=yy;rgba[:,:,2]=100
        self.rgba=rgba;self.raw=self.png('original painting.png',rgba);self.im=self.png('registered HQ.png',rgba)
        receipt={'schema':'h3.motion.receipt.v1','target_scale':4,'png_sha256':self.im['sha256'],'output_pixels':[128,128],
                 'origin_at_target_scale':[0,0],'contract':{'native_entry':{'key':self.key,'targets':self.g},
                 'entry':{'layers':[{'source_a':self.raw}]}}}
        p=self.root/'upstream synthetic tool receipt.json';p.write_text(json.dumps(receipt),encoding='utf-8')
        self.source={'image':self.im,'receipt':self.ref(p),'geometry':self.g,'editing_scale':4}
        mask=np.zeros((32,32,4),np.uint8);mask[:,:,:3]=255;mask[:,10:22,3]=255
        self.mask=self.png('native logical alpha.png',mask)
        self.mask.update(data_role='native-logical-alpha-geometry',alpha_sha256=repair.digest(mask[:,:,3].tobytes()),native_key=self.key,native_geometry=self.g)
        select=np.zeros_like(rgba);select[32:96,32:96]=255
        self.selection=self.png('selection weights.png',select);self.selection['data_role']='selection-weight-mask'

    def tearDown(self):self.override.stop();self.temp.cleanup()

    def ref(self,p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

    def png(self,name,a):
        p=self.root/name;Image.fromarray(a).save(p);return {**self.ref(p),'pixels':[a.shape[1],a.shape[0]]}

    def job(self,operator):return {'id':'example','operator':operator,'operator_version':1,'native_key':self.key,'source':self.source}

    def manifest(self,job,operation='synthetic'):
        p=self.root/'manifest.json';p.write_text(json.dumps({'schema':repair.MANIFEST_SCHEMA,'operation_id':operation,'jobs':[job]}),encoding='utf-8')
        return p,repair.digest(p.read_bytes()),self.root/'build/experiments/asset-repair'/operation

    def test_shared_collar_preserves_core_and_native_alpha_edge(self):
        job=self.job('shared-interface-collar');job.update(native_mask=self.mask,material=self.source,controlled_sides=['top','bottom'],depth_native=3,shared_native=1)
        out,stats=repair.dispatch(job);a=out[0][1]
        self.assertTrue(stats['unmodified_core_exact'])
        np.testing.assert_array_equal(a[0,:,3],np.repeat(np.asarray(Image.open(self.mask['path']))[0,:,3],4))
        np.testing.assert_array_equal(a[12:-12,12:-12],self.rgba[12:-12,12:-12])
        np.testing.assert_array_equal(a[20:-20,0],self.rgba[20:-20,0])

    def test_check_writes_nothing_and_repeat_run_replays_exact_pixels(self):
        job=self.job('registered-alpha');job.update(native_mask=self.mask,mode='intersect');p,h,out=self.manifest(job)
        self.assertEqual(repair.execute('check',p,h,out,'synthetic')['status'],'READY');self.assertFalse(out.exists())
        repair.execute('run',p,h,out,'synthetic');before={x.name:x.read_bytes() for x in (out/'images').iterdir()}
        self.assertEqual(repair.execute('run',p,h,out,'synthetic')['status'],'VERIFIED')
        self.assertEqual(before,{x.name:x.read_bytes() for x in (out/'images').iterdir()})

    def test_input_sha_drift_and_foreign_output_are_blocked(self):
        job=self.job('registered-alpha');job['native_mask']=self.mask;p,h,out=self.manifest(job)
        Path(self.raw['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'INPUT_DRIFT'):repair.execute('check',p,h,out,'synthetic')
        self.assertFalse(out.exists())

    def test_three_x_cannot_be_promoted_to_hq_master(self):
        job=self.job('registered-alpha');job['source']={**self.source,'editing_scale':3};job['native_mask']=self.mask
        with self.assertRaisesRegex(ValueError,'HQ_EDITING_SCALE'):repair.dispatch(job)

    def test_native3x_crop_assembly_keeps_exact_origin_without_upscaling(self):
        job=self.job('native-canvas-assembly');g={'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]}
        pixels=self.png('native3x delivery crop.png',np.full((96,42,4),255,np.uint8))
        receipt=json.loads(Path(self.source['receipt']['path']).read_text());receipt.pop('origin_at_target_scale');receipt.update(target_scale=3,png_sha256=pixels['sha256'],output_pixels=[42,96],origin_3x=[27,0]);receipt['contract']['native_entry']['targets']=g
        path=self.root/'delivery receipt.json';path.write_text(json.dumps(receipt))
        job['source']={'image':pixels,'receipt':self.ref(path),'geometry':g,'editing_scale':3}
        result,stats=repair.dispatch(job);a=result[0][1]
        self.assertEqual(a.shape,(96,96,4));self.assertFalse(a[:,:27].any());self.assertTrue((a[:,27:69]==255).all());self.assertFalse(stats['painted_RGB_resampled'])

    def test_raw_original_non_integer_density_red_fringe_cleanup_preserves_alpha_and_interior(self):
        from copy import deepcopy
        rgb=np.zeros((120,120,4),np.uint8)
        rgb[12:108,20:100]=[132,110,82,255]
        rgb[12:108,17:20]=[255,0,0,8]
        rgb[46:50,52:56]=[255,0,0,255]  # matching subject pixels remain outside the alpha-zero band
        source=self.png('raw model output variable resolution.png',rgb)
        key=['def','TEST_ROAD',0,10]
        original={'schema':'h3.art.original-received-source.v1','native_key':key,
                  'original':{'path':source['path'],'sha256':source['sha256'],'pixels':source['pixels']},
                  'source_bytes_unchanged':True,'art_accepted':False}
        receipt=self.root/'raw original-source receipt.json';receipt.write_text(json.dumps(original),encoding='utf-8')
        source_ref={'image':source,'original_source_receipt':self.ref(receipt)}
        job={'id':'raw-fringe','operator':'bounded-key-decontaminate','operator_version':1,
             'native_key':key,'source':source_ref,'source_scope':'original-model-output',
             'key_rgb':[255,0,0],'mode':'fringe-rgb','radius_pixels':4,'key_tolerance':8}
        outputs,stats=repair.dispatch(job);clean=outputs[0][1]
        self.assertEqual(clean.shape,rgb.shape)
        np.testing.assert_array_equal(clean[:,:,3],rgb[:,:,3])
        np.testing.assert_array_equal(clean[46:50,52:56],rgb[46:50,52:56])
        self.assertGreater(stats['selected_pixels'],0)
        self.assertEqual(stats['selected_opaque_subject_pixels'],0)
        self.assertTrue(stats['whole_canvas_alpha_exact'])
        self.assertEqual(stats['unresolved_key_pixels_without_local_donors'],0)
        self.assertFalse(stats['original_png_geometry_resampled'])
        self.assertEqual(stats['output_geometry']['raw-fringe']['layout'],'raw-original-resolution-candidate')
        manifest,manifest_sha,outdir=self.manifest(job,'raw-original-fringe')
        self.assertEqual(repair.execute('check',manifest,manifest_sha,outdir,'raw-original-fringe')['status'],'READY')
        self.assertEqual(repair.execute('run',manifest,manifest_sha,outdir,'raw-original-fringe')['status'],'COMPLETED_REVIEW_REQUIRED')
        self.assertEqual(repair.execute('verify',manifest,manifest_sha,outdir,'raw-original-fringe')['status'],'VERIFIED')
        job['source_scope']='candidate3x'
        with self.assertRaisesRegex(ValueError,'ROLE_REQUIRED'):
            repair.dispatch(job)

    def test_raw_fringe_retains_key_pixels_without_a_bounded_clean_donor(self):
        rgba=np.zeros((32,32,4),np.uint8);rgba[8:24,15:31]=[130,100,75,255]
        rgba[8:24,9:15]=[255,0,0,1]
        result,stats=repair.decontaminate_transparent_edge_key(rgba,[255,0,0],radius=4,tolerance=0)
        self.assertGreater(stats['selected_pixels'],0)
        self.assertGreater(stats['unresolved_key_pixels_without_local_donors'],0)
        self.assertTrue(stats['unresolved_key_pixels_retained'])
        np.testing.assert_array_equal(result[8:24,9],rgba[8:24,9])
        np.testing.assert_array_equal(result[:,:,3],rgba[:,:,3])

    def test_raw_low_alpha_key_removal_only_clears_exact_edge_colors(self):
        rgba=np.zeros((40,40,4),np.uint8);rgba[10:30,18:32]=[130,100,75,255]
        rgba[10:30,16:18]=[255,0,0,3];rgba[19,20]=[255,0,0,255]
        keycolors=[[255,0,0],[170,85,85]]
        result,stats=repair.remove_transparent_edge_low_alpha_keys(rgba,keycolors,max_alpha=4,radius=8)
        self.assertEqual(stats['removed_pixels'],40)
        self.assertEqual(stats['maximum_alpha_removed'],3)
        self.assertEqual(result[19,20,3],255)
        np.testing.assert_array_equal(result[10:30,18:32],rgba[10:30,18:32])
        with self.assertRaisesRegex(ValueError,'NO_LOW_ALPHA_KEY'):
            repair.remove_transparent_edge_low_alpha_keys(rgba,[[12,34,56]],max_alpha=4,radius=8)
        raw=self.png('low alpha red original.png',rgba);key=['def','TEST_ROAD',0,10]
        original={'schema':'h3.art.original-received-source.v1','native_key':key,
            'original':{'path':raw['path'],'sha256':raw['sha256'],'pixels':raw['pixels']},
            'source_bytes_unchanged':True,'art_accepted':False}
        source_receipt=self.root/'low alpha source receipt.json';source_receipt.write_text(json.dumps(original),encoding='utf-8')
        job={'id':'raw-alpha-edge','operator':'bounded-key-decontaminate','operator_version':1,'native_key':key,
            'source':{'image':raw,'original_source_receipt':self.ref(source_receipt)},
            'source_scope':'original-model-output','mode':'transparent-edge-low-alpha-key',
            'key_colors':keycolors,'maximum_alpha':4,'radius_pixels':8}
        manifest,manifest_sha,outdir=self.manifest(job,'raw-alpha-edge')
        self.assertEqual(repair.execute('check',manifest,manifest_sha,outdir,'raw-alpha-edge')['status'],'READY')
        self.assertEqual(repair.execute('run',manifest,manifest_sha,outdir,'raw-alpha-edge')['status'],'COMPLETED_REVIEW_REQUIRED')
        self.assertEqual(repair.execute('verify',manifest,manifest_sha,outdir,'raw-alpha-edge')['status'],'VERIFIED')

    def test_raw_player_flag_variants_share_exact_alpha_and_only_change_cloth(self):
        g={'canvas_1x':[32,32],'crop_1x':[32,32],'origin_1x':[0,0]}
        rgba=np.zeros((128,128,4),np.uint8)
        rgba[42:82,28:104]=[190,22,28,255]
        rgba[42:82,28:30]=[220,220,220,255]
        rgba[50,22:28]=[255,0,0,2]
        raw=self.png('received red player flag.png',rgba);key=['def','AF00E',0,0]
        original={'schema':'h3.art.original-received-source.v1','native_key':key,
            'original':{'path':raw['path'],'sha256':raw['sha256'],'pixels':raw['pixels']},
            'source_bytes_unchanged':True,'art_accepted':False}
        source_receipt=self.root/'received flag source receipt.json';source_receipt.write_text(json.dumps(original),encoding='utf-8')
        colors=[('AF00E','red',28,.195,0,'diagonal'),('AF01E','blue',258,.165,-.02,'waves'),
            ('AF02E','tan',72,.085,.08,'diamond'),('AF03E','green',142,.155,-.01,'interlace'),
            ('AF04E','orange',52,.185,.04,'chevron'),('AF05E','purple',308,.165,-.03,'lozenge'),
            ('AF06E','teal',196,.135,.01,'loops'),('AF07E','pink',350,.15,.07,'scallop')]
        variants=[{'id':f'{name}_0_0','native_key':['def',name,0,0],'hue_degrees':hue,
            'chroma':chroma,'lightness_offset':offset,'pattern_kind':pattern}
            for name,_,hue,chroma,offset,pattern in colors]
        job={'id':'flag-variants','operator':'raw-player-flag-variants','operator_version':1,
            'native_key':key,'source':{'image':raw,'original_source_receipt':self.ref(source_receipt)},
            'source_scope':'original-model-output','native_geometry':g,'editing_scale':4,
            'transparent_max_alpha':4,'variants':variants}
        outputs,stats=repair.dispatch(job)
        self.assertEqual(len(outputs),8);self.assertTrue(stats['common_alpha_exact'])
        alpha=outputs[0][1][:,:,3]
        self.assertFalse(np.any((alpha>0)&(alpha<=4)))
        for _,image in outputs[1:]:np.testing.assert_array_equal(image[:,:,3],alpha)
        gold=np.zeros(alpha.shape,bool);gold[42:82,28:30]=True
        base=np.asarray(Image.open(raw['path']).convert('RGBA'))
        for _,image in outputs:
            np.testing.assert_array_equal(image[gold],base[gold])
            np.testing.assert_array_equal(image[alpha==0,3],np.zeros(int((alpha==0).sum()),dtype=np.uint8))
        self.assertFalse(np.array_equal(outputs[0][1][42:82,30:104],outputs[1][1][42:82,30:104]))
        red_mean=outputs[0][1][42:82,30:104,:3].mean(axis=(0,1))
        blue_mean=outputs[1][1][42:82,30:104,:3].mean(axis=(0,1))
        self.assertGreater(red_mean[0],red_mean[2]+40)
        self.assertGreater(blue_mean[2],blue_mean[0]+30)
        self.assertEqual(stats['output_geometry']['AF07E_0_0']['editing_scale'],4)

    def test_raw_dynamic_player_flags_remove_thin_leading_tip_whiskers_once_for_all_colors(self):
        g={'canvas_1x':[32,32],'crop_1x':[32,32],'origin_1x':[0,0]}
        rgba=np.zeros((128,128,4),np.uint8)
        rgba[42:82,28:104]=[20,70,190,255]
        rgba[42:82,28:30]=[220,220,220,255]
        rgba[60:62,18:28]=[20,70,190,255]
        # A thin attached fringe extends the source bbox past the cloth body.
        # Opening removes its far edge, so anchoring on the final bbox column
        # would fail even though the main flag body is intact.
        rgba[60:62,104:112]=[20,70,190,255]
        raw=self.png('received blue animated flag.png',rgba);key=['def','AF01',1,2]
        original={'schema':'h3.art.original-received-source.v1','native_key':key,
            'original':{'path':raw['path'],'sha256':raw['sha256'],'pixels':raw['pixels']},
            'source_bytes_unchanged':True,'art_accepted':False}
        source_receipt=self.root/'received animated flag source receipt.json';source_receipt.write_text(json.dumps(original),encoding='utf-8')
        colors=[('AF01',258,.165,-.02,'waves'),('AF02',72,.085,.08,'diamond'),
            ('AF03',142,.155,-.01,'interlace'),('AF04',52,.185,.04,'chevron'),
            ('AF05',308,.165,-.03,'lozenge'),('AF06',196,.135,.01,'loops'),('AF07',350,.15,.07,'scallop')]
        variants=[{'id':f'{name}_1_2','native_key':['def',name,1,2],'hue_degrees':hue,
            'chroma':chroma,'lightness_offset':offset,'pattern_kind':pattern}
            for name,hue,chroma,offset,pattern in colors]
        job={'id':'dynamic-flag-frame','operator':'raw-player-flag-variants','operator_version':1,
            'native_key':key,'source':{'image':raw,'original_source_receipt':self.ref(source_receipt)},
            'source_scope':'original-model-output','native_geometry':g,'editing_scale':4,
            'transparent_max_alpha':4,'tip_open_size':5,'variants':variants}
        outputs,stats=repair.dispatch(job)
        self.assertEqual(len(outputs),7);self.assertGreater(stats['tip_alpha_pixels_removed'],0)
        self.assertEqual(outputs[0][1][60,20,3],0)
        self.assertEqual(outputs[0][1][60,40,3],255)
        for _,image in outputs[1:]:np.testing.assert_array_equal(image[:,:,3],outputs[0][1][:,:,3])
        np.testing.assert_array_equal(outputs[0][1][:,36:,3],rgba[:,36:,3])
        self.assertEqual(stats['output_geometry']['AF07_1_2']['native_key'],['def','AF07',1,2])

    def test_wrong_native_mask_slot_is_rejected(self):
        job=self.job('registered-alpha');job['native_mask']={**self.mask,'native_key':['def','OTHER',0,0]}
        with self.assertRaisesRegex(ValueError,'NATIVE_MASK_SLOT'):repair.dispatch(job)

    def test_genuine_hq_crop_assembly_is_exact_copy_and_rejects_3x_promotion(self):
        job=self.job('registered-hq-canvas');g={'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]}
        crop=self.rgba[:,36:92].copy();pixels=self.png('genuine HQ crop.png',crop)
        receipt=json.loads(Path(self.source['receipt']['path']).read_text());receipt.update(png_sha256=pixels['sha256'],output_pixels=[56,128],origin_at_target_scale=[36,0]);receipt['contract']['native_entry']['targets']=g
        path=self.root/'genuine HQ crop receipt.json';path.write_text(json.dumps(receipt))
        job['source']={'image':pixels,'receipt':self.ref(path),'geometry':g,'editing_scale':4}
        result,stats=repair.dispatch(job);a=result[0][1]
        self.assertEqual(a.shape,(128,128,4));np.testing.assert_array_equal(a[:,36:92],crop)
        self.assertFalse(a[:,:36].any());self.assertFalse(a[:,92:].any())
        self.assertFalse(stats['painted_RGB_resampled']);self.assertEqual(stats['output_geometry']['example']['editing_scale'],4)
        job['source']['editing_scale']=3
        with self.assertRaisesRegex(ValueError,'HQ_EDITING_SCALE'):repair.dispatch(job)

    def test_reslice_preserves_parent_rgb_coordinates(self):
        job=self.job('registered-parent-reslice');alpha=np.full((8,10,4),255,np.uint8)
        target={'canvas_1x':[10,8],'crop_1x':[10,8],'origin_1x':[0,0]}
        mask=self.png('slice mask.png',alpha);mask.update(data_role='native-logical-alpha-geometry',alpha_sha256=repair.digest(alpha[:,:,3].tobytes()),native_key=['pcx','PIECE',None,None],native_geometry=target)
        job['parts']=[{'id':'piece','rect_native':[5,7,10,8],'mask':mask,'native_key':['pcx','PIECE',None,None],
                       'target_geometry':target}]
        out,stats=repair.dispatch(job);np.testing.assert_array_equal(out[0][1],self.rgba[28:60,20:60]);self.assertFalse(stats['painted_RGB_resampled'])

    def test_alpha_replace_refuses_unpainted_rgb(self):
        rgba=self.rgba.copy();rgba[:,:,3]=0;p=self.png('empty.png',rgba)
        job=self.job('registered-alpha');job.update(native_mask=self.mask,mode='replace')
        receipt=json.loads(Path(self.source['receipt']['path']).read_text());receipt['png_sha256']=p['sha256']
        q=self.root/'empty synthetic registration.json';q.write_text(json.dumps(receipt))
        job['source']={**self.source,'image':p,'receipt':self.ref(q)}
        with self.assertRaisesRegex(ValueError,'WOULD_REVEAL_UNPAINTED'):repair.dispatch(job)

    def test_repairs_can_chain_through_prior_receipt_without_master_promotion(self):
        job=self.job('registered-alpha');job['native_mask']=self.mask;p,h,out=self.manifest(job)
        repair.execute('run',p,h,out,'synthetic');receipt=json.loads((out/'asset-repair-receipt.json').read_text())
        item=receipt['outputs'][0];source={'image':{k:item[k] for k in ('path','sha256','pixels')},
            'receipt':self.ref(out/'asset-repair-receipt.json'),'geometry':self.g,'editing_scale':4}
        next_job=self.job('registered-alpha');next_job.update(source=source,native_mask=self.mask)
        images,_=repair.dispatch(next_job)
        np.testing.assert_array_equal(images[0][1],np.asarray(Image.open(item['path'])))
        collar=self.job('shared-interface-collar')
        collar.update(source=source,material=source,native_mask=self.mask,controlled_sides=['top','bottom'])
        _,statistics=repair.dispatch(collar)
        self.assertTrue(statistics['unmodified_core_exact'])

    def test_foreign_existing_operation_is_never_overwritten(self):
        job=self.job('registered-alpha');job['native_mask']=self.mask;p,h,out=self.manifest(job)
        out.mkdir(parents=True);marker=out/'existing.txt';marker.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'PARTIAL_OR_FOREIGN'):repair.execute('run',p,h,out,'synthetic')
        self.assertEqual(marker.read_bytes(),b'keep')

    def test_empty_atlas_and_missing_declared_piece_are_not_complete(self):
        job=self.job('registered-parent-reslice');job['parts']=[]
        with self.assertRaisesRegex(ValueError,'NONEMPTY_REGISTERED_PART'):repair.dispatch(job)
        job['parts']=[{'id':'partial','rect_native':[0,0,32,32],'mask':self.mask,
            'native_key':self.key,'target_geometry':self.g}]
        job.update(expected_coverage_mask=self.selection,required_part_ids=['partial','missing'])
        with self.assertRaisesRegex(ValueError,'COVERAGE_SLOT_SET'):repair.dispatch(job)
        job['required_part_ids']=['partial']
        with self.assertRaisesRegex(ValueError,'PARENT_PIXELS_UNCOVERED'):repair.dispatch(job)

    def test_material_lab_shift_is_bounded_and_does_not_repaint_decoration(self):
        mask=np.zeros((128,128),np.float32);mask[20:100,20:100]=1
        target=repair.color_statistics(self.rgba,mask)+np.array([30,40,-30])
        out,stats=repair.bounded_lab_shift(self.rgba,mask,target,4,4)
        np.testing.assert_array_equal(out[mask==0],self.rgba[mask==0])
        np.testing.assert_array_equal(out[:,:,3],self.rgba[:,:,3])
        self.assertLessEqual(abs(stats['applied_lab_shift'][0]),4)
        self.assertLessEqual(np.linalg.norm(stats['applied_lab_shift'][1:]),4.00001)
        self.assertFalse(stats['redraw_decision_made']);self.assertFalse(stats['legal_terrain_adjacency_verified'])

    def test_material_reference_and_mask_chain_are_pinned(self):
        job=self.job('material-lab-harmonize');job.update(material_id='sand',mask=self.selection,
            references=[{'native_key':self.key,'source':self.source,'mask':self.selection,'material_id':'sand'}])
        p,h,out=self.manifest(job);repair.execute('run',p,h,out,'synthetic')
        r=json.loads((out/'asset-repair-receipt.json').read_bytes())
        self.assertIn(self.raw['sha256'],{x['sha256'] for x in r['inputs']})
        self.assertFalse(r['art_approved'])

    def test_static_base_retains_every_unmasked_pixel_even_next_to_water(self):
        frame=np.full_like(self.rgba,255);mask=np.zeros((128,128),np.float32);mask[:,64:]=1
        mask[:,64]=.01
        out,stats=repair.lock_static_rgb(self.rgba,frame,mask)
        np.testing.assert_array_equal(out[:,:64],self.rgba[:,:64])
        np.testing.assert_array_equal(out[:,:,3],self.rgba[:,:,3])
        self.assertEqual(stats['static_max_RGBA_difference_measured'],0)

    def test_static_group_dispatch_uses_registered_frames(self):
        job=self.job('locked-static-base');job.update(mask=self.selection,
            frames=[{'id':'frame1','native_key':self.key,'source':self.source}])
        p,h,out=self.manifest(job);repair.execute('run',p,h,out,'synthetic')
        self.assertEqual(repair.execute('verify',p,h,out,'synthetic')['status'],'VERIFIED')

    def test_key_cleanup_requires_explicit_mask_and_preserves_cyan_subject(self):
        source=np.zeros((64,64,4),np.uint8);source[16:48,16:48]=[160,110,70,255]
        source[16,16:48]=[0,255,255,255];source[25:35,25:35]=[0,255,255,255]
        mask=np.zeros((64,64),np.float32);mask[16,16:48]=1
        out,stats=repair.decontaminate_selected_key(source,mask,[0,255,255])
        np.testing.assert_array_equal(out[mask==0],source[mask==0]);self.assertTrue(stats['alpha_exact'])
        self.assertLess(out[16,20,1],200)
        mask[28,28]=1
        with self.assertRaisesRegex(ValueError,'SUBJECT_INTERIOR'):repair.decontaminate_selected_key(source,mask,[0,255,255])

    def test_exterior_key_floodfill_never_removes_disconnected_material(self):
        source=np.full((32,32,4),[0,255,255,255],np.uint8);source[4:28,4:28]=[120,90,50,255]
        source[15,15]=[0,255,255,255];mask=np.ones((32,32),np.float32);mask[4:28,4:28]=0
        out,stats=repair.decontaminate_selected_key(source,mask,[0,255,255],mode='exterior-key')
        self.assertEqual(out[15,15,3],255);self.assertEqual(out[0,0,3],0)
        mask[15,15]=1
        with self.assertRaisesRegex(ValueError,'DISCONNECTED_SUBJECT'):repair.decontaminate_selected_key(source,mask,[0,255,255],mode='exterior-key')

    def test_material_labels_detect_missing_transition_and_wrong_edge_material(self):
        expected=np.ones((128,128),np.uint8);expected[:,64:]=2
        labels=[{'id':1,'material_id':'snow'},{'id':2,'material_id':'dirt'}]
        flat=np.ones_like(expected)
        result=repair.diagnose_material_labels(expected,flat,labels,4)
        self.assertEqual(result['required_material_ids_absent'],[2])
        self.assertGreater(result['mismatched_material_pixels'],0)
        wrong=expected.copy();wrong[:,:4]=2;wrong[:,-4:]=1
        result=repair.diagnose_material_labels(expected,wrong,labels,4)
        self.assertGreater(result['edges']['left']['mismatched_material_pixels'],0)
        self.assertGreater(result['edges']['right']['mismatched_material_pixels'],0)
        self.assertFalse(result['automatic_art_or_redraw_decision'])
        single=repair.diagnose_material_labels(np.ones((4,4),np.uint8),np.full((4,4),2,np.uint8),labels,1)
        self.assertEqual(json.loads(json.dumps(single))['required_material_ids_absent'],[1])

    def test_diagnostic_statistics_are_replayed_and_cannot_be_falsified(self):
        job=self.job('registered-alpha');job['native_mask']=self.mask;p,h,out=self.manifest(job)
        repair.execute('run',p,h,out,'synthetic');receipt=out/'asset-repair-receipt.json'
        data=json.loads(receipt.read_bytes());data['jobs'][0]['statistics']['visible_RGB_exact']=False
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'DIAGNOSTIC_REPLAY'):repair.execute('verify',p,h,out,'synthetic')

    def test_label_diagnostic_uses_pinned_data_and_writes_no_art(self):
        native=np.full((32,32,4),255,np.uint8);native[:,:,:3]=1;native[:,16:,:3]=2
        target=np.repeat(np.repeat(native,4,axis=0),4,axis=1)
        n=self.png('native labels.png',native);c=self.png('candidate labels.png',target)
        for value,scale,parent_sha in [(n,1,self.mask['sha256']),(c,4,self.im['sha256'])]:
            value.update(data_role='material-label-data',native_key=self.key,native_geometry=self.g,
                coordinate_scale=scale,source_sha256=parent_sha)
        job=self.job('material-label-diagnostic');job.update(native_reference=self.mask,native_labels=n,candidate_labels=c,
            labels=[{'id':1,'material_id':'snow'},{'id':2,'material_id':'dirt'}])
        p,h,out=self.manifest(job);result=repair.execute('run',p,h,out,'synthetic')
        self.assertEqual(result['outputs'],0);self.assertEqual(list((out/'images').iterdir()),[])
        self.assertEqual(repair.execute('verify',p,h,out,'synthetic')['status'],'VERIFIED')
        job['candidate_labels']['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'LABEL_SOURCE_BINDING'):repair.dispatch(job)

    def test_hatch_uses_world_coordinates_across_separately_cut_regions(self):
        parent=np.full((64,128,4),[140,110,70,255],np.uint8);mask=np.ones((64,128),np.float32)
        expected=repair.global_hatch(parent,mask,[0,0],4)
        a=repair.global_hatch(parent[:,:64],mask[:,:64],[0,0],4)
        b=repair.global_hatch(parent[:,64:],mask[:,64:],[16,0],4)
        np.testing.assert_array_equal(np.concatenate([a,b],axis=1),expected)
        np.testing.assert_array_equal(expected[:,:,3],parent[:,:,3])

    def test_state_effect_requires_explicit_native_shape_and_preserves_paint(self):
        job=self.job('coordinate-state-effects');state_key=['def','TEST_ROAD',0,1]
        clip=self.job('registered-alpha');clip['native_mask']=self.mask;p,h,out=self.manifest(clip)
        repair.execute('run',p,h,out,'synthetic');receipt=json.loads((out/'asset-repair-receipt.json').read_bytes());item=receipt['outputs'][0]
        job['source']={'image':{k:item[k] for k in ('path','sha256','pixels')},'receipt':self.ref(out/'asset-repair-receipt.json'),
            'geometry':self.g,'editing_scale':4}
        alpha=np.asarray(Image.open(self.mask['path'])).copy();alpha[:,9:23,3]=255
        state_mask=self.png('selected state mask.png',alpha);state_mask.update(data_role='native-logical-alpha-geometry',
            native_key=state_key,native_geometry=self.g,alpha_sha256=repair.digest(alpha[:,:,3].tobytes()))
        job.update(mask=self.selection,base_mask=self.mask,states=[{'id':'selected','native_key':state_key,
            'geometry':self.g,'native_mask':state_mask,'mode':'outline','outline_rgb':[240,200,40]}])
        output,stats=repair.dispatch(job);a=output[0][1]
        np.testing.assert_array_equal(a[:,40:88],self.rgba[:,40:88])
        self.assertFalse(stats['button_state_count_or_offsets_guessed'])
        job['states'][0]['mode']='hatch';job['states'][0]['coordinate_origin_native']=[0,0]
        with self.assertRaisesRegex(ValueError,'DISTINCT_NATIVE_GEOMETRY'):repair.dispatch(job)

    def test_masked_variants_and_flow_keep_unmasked_pixels_and_alpha_exact(self):
        job=self.job('masked-color-variants');job.update(mask=self.selection,variants=[{'id':'red','hue_degrees':28,'chroma':.15}])
        output,_=repair.dispatch(job)
        flow=self.job('locked-rgb-flow');flow.update(mask=self.selection,frames=4,amplitude_pixels=1,direction=[0,1])
        output+=repair.dispatch(flow)[0]
        inside=np.array(Image.open(self.selection['path']))[:,:,3]>0
        for _,a in output:
            np.testing.assert_array_equal(a[:,:,3],self.rgba[:,:,3]);np.testing.assert_array_equal(a[~inside],self.rgba[~inside])

    def test_unknown_recipe_field_and_output_escape_are_rejected(self):
        job=self.job('registered-alpha');job.update(native_mask=self.mask,moed='replace');p,h,out=self.manifest(job)
        with self.assertRaisesRegex(ValueError,'UNKNOWN_REPAIR_JOB_FIELD'):repair.execute('check',p,h,out,'synthetic')
        with self.assertRaisesRegex(ValueError,'OUTPUT_MUST_BE_NEW_PRODUCT_OPERATION'):repair.execute('check',p,h,self.root/'elsewhere','synthetic')

    def test_output_tampering_fails_verify(self):
        job=self.job('registered-alpha');job['native_mask']=self.mask;p,h,out=self.manifest(job)
        repair.execute('run',p,h,out,'synthetic');(out/'images/example.png').write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'INPUT_DRIFT'):repair.execute('verify',p,h,out,'synthetic')

    def test_native_entry_point_exposes_qualified_operator_catalog(self):
        executable=os.environ.get('H3TIEREXPORT_EXE')
        if not executable:self.skipTest('Native CLI tested during product qualification')
        result=subprocess.run([executable,'asset-repair','catalog','--python',sys.executable],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(result.stdout);self.assertEqual(payload['operators'],repair.OPERATORS)
        self.assertFalse(payload['automatic_art_approval'])


if __name__=='__main__':unittest.main()
