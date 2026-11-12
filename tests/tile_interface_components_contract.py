"""Lossless component construction plus typed native one-pass placement."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'scripts'))
import tile_interface_components as components


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False)+'\n',encoding='utf-8')
def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}


class ComponentDataTests(unittest.TestCase):
    def test_mirror_is_exact_pixel_copy_and_original_is_unchanged(self):
        a=np.arange(80*120*4,dtype=np.uint8).reshape(80,120,4);before=a.copy()
        result=components.mirror_component(a,[20,15,30,20])
        np.testing.assert_array_equal(result[:,:30],a[15:35,20:50])
        np.testing.assert_array_equal(result[:,30:],a[15:35,20:50][:,::-1])
        np.testing.assert_array_equal(a,before)
        self.assertGreater(np.unique(result.reshape(-1,4),axis=0).shape[0],8)
    def test_roi_does_not_stretch_or_guess_fractional_positions(self):
        a=np.zeros((40,40,4),np.uint8)
        for rect in ([0,0,1.5,4],[0,0,0,4],[35,0,10,4]):
            with self.assertRaises(ValueError):components.mirror_component(a,rect)
    def test_shape_reference_preserves_alpha_and_does_not_mutate_test_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=np.zeros((96,96,4),np.uint8);a[:,30:66]=[30,110,190,255]
            image=root/'fixture.png';Image.fromarray(a).save(image);before=sha(image)
            key=['def','TEST',0,10];receipt=root/'geometry.json'
            save(receipt,{'schema':'h3.tile-geometry.design-receipt.v1','outputs':[{'native_key':key,
                'image':{**ref(image),'pixels':[96,96]},'work_scale':3,
                'geometry':{'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]},
                'role':'NEUTRAL_GEOMETRY_FIXTURE_NOT_ART_MASTER'}]})
            with patch.object(components,'PRODUCT',root):
                result=components.geometry_reference(ref(receipt),key,root/'build/experiments/tile-interfaces/reference',None)
            actual=np.asarray(Image.open(result['image']['path']))
            np.testing.assert_array_equal(actual[...,3],a[...,3]);self.assertEqual(sha(image),before)
            self.assertFalse(actual[a[...,3]==0,:3].any())
            self.assertTrue(result['alpha_exact']);self.assertIn('NOT_PAINTING',result['role'])
    def test_rational_native_roi_binds_exact_fractional_grid_without_resampling(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);y,x=np.indices((102,102));a=np.zeros((102,102,4),np.uint8)
            a[...,0]=x%251;a[...,1]=y%251;a[...,2]=(x+y)%251;a[...,3]=255
            image=root/'rational-parent.png';Image.fromarray(a).save(image);before=sha(image)
            geometry={'canvas_1x':[24,24],'crop_1x':[12,20],'origin_1x':[3,1]}
            result=components.rational_native_roi_layer(ref(image),['def','TEST',0,0],geometry,[17,4],[3,1,12,20])
            self.assertEqual(result['pixel_rect_rational'],[[51,4],[17,4],[51,1],[85,1]])
            self.assertEqual(result['envelope_pixels'],[12,4,52,86])
            self.assertEqual(result['envelope_offset_rational'],[[3,4],[1,4]])
            self.assertEqual(result['layer']['vertices_a'],[[.75,.25],[51.75,.25],[.75,85.25],[51.75,85.25]])
            self.assertEqual(result['layer']['target_vertices_1x'],[[3,1],[15,1],[3,21],[15,21]])
            self.assertTrue(result['source_unchanged']);self.assertFalse(result['resampling_before_native_sampler'])
            self.assertFalse(result['distortion']);self.assertFalse(result['art_approved']);self.assertEqual(sha(image),before)
    def test_rational_native_roi_rejects_unreduced_ratio_and_wrong_canvas(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'parent.png';Image.new('RGBA',(102,102),(50,70,90,255)).save(image)
            geometry={'canvas_1x':[24,24],'crop_1x':[12,20],'origin_1x':[3,1]}
            with self.assertRaisesRegex(ValueError,'MUST_BE_REDUCED'):
                components.rational_native_roi_layer(ref(image),['def','TEST',0,0],geometry,[34,8],[3,1,12,20])
            with self.assertRaisesRegex(ValueError,'CANVAS_MISMATCH'):
                components.rational_native_roi_layer(ref(image),['def','TEST',0,0],geometry,[627,16],[3,1,12,20])
    def test_rational_roi_cli_writes_pinned_operation_record(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'parent.png';Image.new('RGBA',(102,102),(50,70,90,255)).save(image)
            geometry=root/'geometry.json';save(geometry,{'canvas_1x':[24,24],'crop_1x':[12,20],'origin_1x':[3,1]})
            outdir=root/'build/experiments/tile-interfaces/rational-roi-cli';stdout=io.StringIO()
            argv=['roi-layer','--input',str(image),'--input-sha256',sha(image),'--outdir',str(outdir),
                  '--native-key','def','TEST','0','0','--geometry-json',str(geometry),
                  '--pixels-per-native-ratio','17','4','--native-rect','3','1','12','20']
            with patch.object(components,'PRODUCT',root),contextlib.redirect_stdout(stdout):code=components.main(argv)
            self.assertEqual(code,0);summary=json.loads(stdout.getvalue());self.assertFalse(summary['art_approved'])
            receipt=json.loads((outdir/'rational-roi-layer.json').read_bytes())
            self.assertEqual(receipt['native_rect_1x'],[3,1,12,20]);self.assertEqual(receipt['pixels_per_native_ratio'],[17,4])
            self.assertEqual(receipt['source']['sha256'],sha(image));self.assertEqual(receipt['envelope_pixels'],[12,4,52,86])


class NativeRigidComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rigid-component-',dir=PRODUCT/'build/tests')
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.k=8
        y,x=np.indices((256,256));a=np.zeros((256,256,4),np.uint8)
        a[...,0]=x%251;a[...,1]=y%251;a[...,2]=100;a[:,80:176,3]=255
        self.parent=self.root/'parent.png';self.body=self.root/'body.png'
        Image.fromarray(a).save(self.parent);Image.fromarray(a).save(self.body)
        self.g={'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]}
        patch=components.mirror_component(a,[80,96,48,32]);full=np.zeros_like(a);full[96:128,80:176]=patch
        self.component=self.root/'component.png';Image.fromarray(full).save(self.component)
        self.receipt=self.root/'component.json';self.receipt_value={
            'schema':'h3.tile-interface.component-receipt.v1','native_key':['def','TEST',0,10],
            'geometry':self.g,'source':{**ref(self.parent),'pixels':[256,256]},
            'pixels_per_native':8,'half_rect_1x':[10,12,6,4],'component_rect_1x':[10,12,12,4],
            'component_image':{**ref(self.component),'pixels':[256,256]},
            'pixel_permutation_only':True,'resampling_performed':False,'RGB_averaged':False,'extrusion_performed':False}
        save(self.receipt,self.receipt_value)
        self.native=self.root/'native.json';save(self.native,{'entries':[{'key':['def','TEST',0,f],'targets':self.g,
            'equivalence_class':hashlib.sha256(str(f).encode()).hexdigest()} for f in (10,11)]})
        def source(p,rect):
            x,y,w,h=rect;return {**ref(p),'pixels':[256,256],'key':['def','TEST',0,10],
                'source_layout':'explicit-native-roi','source_region':{'pixel_rect':[v*8 for v in rect],
                    'native_rect_1x':rect,'allow_target_subregion':True}}
        va=[[0,0],[112,0],[0,192],[112,192]];target=[[9,4],[23,4],[9,28],[23,28]]
        body={'id':'body','kind':'surface','source_a':source(self.body,[9,4,14,24]),'vertices_a':va,
            'target_vertices_1x':target,'triangles':[[0,1,2],[1,3,2]],
            'anchors':[{'vertex':i,'target_1x':v,'tolerance_3x':0} for i,v in enumerate(target)]}
        shared={'id':'top','kind':'component','source_a':source(self.component,[10,12,12,4]),
            'vertices_a':[[0,0],[96,0],[0,32],[96,32]],'target_vertices_1x':[[10,0],[22,0],[10,4],[22,4]],
            'triangles':[[0,1,2],[1,3,2]],'component_contract':{'schema':'h3.motion.rigid-component.v1',
                'receipt':ref(self.receipt),'quarter_turns_clockwise':0,'target_rect_1x':[10,0,12,4]}}
        self.item={'id':'composed','key':['def','TEST',0,11],'targets':self.g,'mode':'mesh',
            'quality':{'mode':'anchored-surface'},'sampling_budget':{'schema':'h3.motion.sampling-budget.v1','max_loop_visits':1<<27},
            'layers':[body,shared]}
        self.spec={'schema':'h3.motion.spec.v1','target_scale':3,'output_root':str(self.root/'out'),
            'native_contracts':[ref(self.native)],'settings':{'backend':'cpu','space':'linear','filter':'h3-quality','workers':1,'memory_gib':1},'items':[self.item]}
        self.exe=Path(os.environ.get('H3TIEREXPORT_EXE',PRODUCT/'build/candidate/H3TierExport.exe'))
    def call(self,*args):
        return subprocess.run([str(self.exe),*map(str,args)],capture_output=True,text=True,encoding='utf-8',timeout=90)
    def plan(self,destination=None):
        p=self.root/'spec.json';save(p,self.spec);return self.call('motion-plan','--spec',p,'--out',destination or self.root/'batch.json')
    def reject(self,text):
        r=self.plan();self.assertNotEqual(r.returncode,0);self.assertIn(text,r.stdout+r.stderr)
        self.assertFalse((self.root/'out').exists())
    def test_native_plan_run_verify_preserves_parent_and_has_rigid_evidence(self):
        before=sha(self.parent);p=self.plan();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        r=self.call('motion-run','--batch',self.root/'batch.json','--workers',1,'--memory-gib',1)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        run=json.loads(r.stdout);entry=run['results'][0];receipt=json.loads(Path(entry['receipt']).read_bytes())
        self.assertTrue(any(x.get('rigid_component_one_to_one_verified') for x in receipt['diagnostics']['layers']))
        self.assertEqual(self.call('motion-verify','--batch',self.root/'batch.json').returncode,0)
        self.assertEqual(sha(self.parent),before)
    def test_nonuniform_stretch_and_local_warp_are_rejected(self):
        self.item['layers'][1]['component_contract']['target_rect_1x'][2]=13
        self.reject('NONUNIFORM_OR_SCALED')
    def test_same_bounds_but_changed_internal_vertex_is_rejected(self):
        self.item['layers'][1]['target_vertices_1x'][3][0]-=.1
        self.reject('RIGID_PLACEMENT_DIFFERS')
    def test_rewritten_image_and_receipt_hashes_do_not_hide_repainted_component(self):
        a=np.asarray(Image.open(self.component)).copy();a[100,90,0]^=30;Image.fromarray(a).save(self.component)
        self.receipt_value['component_image']['sha256']=sha(self.component);save(self.receipt,self.receipt_value)
        layer=self.item['layers'][1];layer['source_a']['sha256']=sha(self.component);layer['component_contract']['receipt']=ref(self.receipt)
        self.reject('PIXEL_PERMUTATION_REPLAY_DIFFERS')
    def test_component_receipt_on_effect_layer_cannot_bypass_contract(self):
        self.item['layers'][1]['kind']='effect';self.reject('CONTRACT_ON_NONCOMPONENT')
    def test_components_cannot_replace_the_required_primary_surface(self):
        self.item['layers']=self.item['layers'][1:];self.reject('PRIMARY_SURFACE')
    def test_all_four_quarter_turns_use_one_to_one_native_geometry(self):
        for turn in range(4):
            layer=self.item['layers'][1];w,h=12,4;xy=(10,8);points=[]
            for u,v in ((0,0),(w,0),(0,h),(w,h)):
                rx,ry=((u,v),(h-v,u),(w-u,h-v),(v,w-u))[turn];points.append([xy[0]+rx,xy[1]+ry])
            layer['target_vertices_1x']=points
            layer['component_contract'].update(quarter_turns_clockwise=turn,target_rect_1x=[*xy,h if turn%2 else w,w if turn%2 else h])
            self.spec['output_root']=str(self.root/f'out-turn{turn}');batch=self.root/f'batch-turn{turn}.json'
            result=self.plan(batch);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=self.call('motion-run','--batch',batch,'--workers',1,'--memory-gib',1)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_different_genuine_source_pixel_densities_do_not_require_hq_resampling(self):
        k=12;y,x=np.indices((32*k,32*k));a=np.zeros((32*k,32*k,4),np.uint8)
        a[...,0]=x%251;a[...,1]=y%251;a[...,2]=100;a[:,10*k:22*k,3]=255;Image.fromarray(a).save(self.parent)
        whole=np.zeros_like(a);whole[12*k:16*k,10*k:22*k]=components.mirror_component(a,[10*k,12*k,6*k,4*k])
        Image.fromarray(whole).save(self.component)
        self.receipt_value.update(source={**ref(self.parent),'pixels':[32*k,32*k]},pixels_per_native=k,
            component_image={**ref(self.component),'pixels':[32*k,32*k]});save(self.receipt,self.receipt_value)
        layer=self.item['layers'][1];layer['source_a'].update(sha256=sha(self.component),pixels=[32*k,32*k])
        layer['source_a']['source_region']['pixel_rect']=[10*k,12*k,12*k,4*k]
        layer['vertices_a']=[[0,0],[12*k,0],[0,4*k],[12*k,4*k]];layer['component_contract']['receipt']=ref(self.receipt)
        result=self.plan();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=self.call('motion-run','--batch',self.root/'batch.json','--workers',1,'--memory-gib',1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        output=json.loads(result.stdout)['results'][0];record=json.loads(Path(output['receipt']).read_bytes())
        self.assertEqual({r['source_region']['scale_xy'][0] for r in record['contract']['source_geometry']},{8,12})
    def test_legacy_v5_cannot_smuggle_new_component_type(self):
        result=self.plan();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        batch=json.loads((self.root/'batch.json').read_bytes());batch['algorithm']='linear-premultiplied-correspondence-mesh-ewa-time-v5'
        save(self.root/'legacy.json',batch);r=self.call('motion-check','--batch',self.root/'legacy.json')
        self.assertNotEqual(r.returncode,0);self.assertIn('REQUIRES_V6_STATIC_SURFACE',r.stdout+r.stderr)


if __name__=='__main__':unittest.main()
