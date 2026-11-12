"""Actual-EXE motion tests with independent pixel and silhouette expectations."""
from pathlib import Path
import copy, hashlib, json, subprocess, sys, tempfile, unittest
import numpy as np
from PIL import Image

PRODUCT=Path(__file__).resolve().parents[1]
EXE=PRODUCT/'build/candidate/H3TierExport.exe'
sys.path.insert(0,str(PRODUCT/'scripts'))
from tile_interface_components import rational_native_roi_layer

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False)+'\n',encoding='utf-8')

class MotionContract(unittest.TestCase):
    def setUp(self):
        parent=PRODUCT/'build/tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='motion-',dir=parent)
        self.root=Path(self.temp.name).resolve();self.output=self.root/'output'
        self.a=self.root/'a.png';self.b=self.root/'b.png'
        for path,left,color in [(self.a,8,(30,90,210,255)),(self.b,20,(60,160,230,255))]:
            image=Image.new('RGBA',(48,48),(255,0,0,0))
            for y in range(6,42):
                for x in range(left,left+16):image.putpixel((x,y),color)
            image.save(path)
        self.hashes={str(p):sha(p) for p in (self.a,self.b)}
        self.geometry={'canvas_1x':[24,24],'crop_1x':[16,16],'origin_1x':[4,4]}
        self.native=self.root/'native.json'
        entries=[{'key':['def','TEST',0,n],'targets':self.geometry,'equivalence_class':hashlib.sha256(str(n).encode()).hexdigest()} for n in range(5)]
        entries[2]['equivalence_class']=entries[0]['equivalence_class']
        save(self.native,{'entries':entries})
        self.va=[[x,y] for y in [0,6,42,48] for x in [0,8,24,48]]
        self.vb=[[x,y] for y in [0,6,42,48] for x in [0,20,36,48]]
        self.triangles=[]
        for y in range(3):
            for x in range(3):
                i=y*4+x;self.triangles.extend([[i,i+1,i+4],[i+1,i+5,i+4]])
        self.spec={'schema':'h3.motion.spec.v1','output_root':str(self.output),'native_contracts':[{'path':str(self.native)}],
                   'settings':{'backend':'cpu','space':'linear','filter':'h3-quality','workers':2,'memory_gib':1},
                   'items':[self.item(0,'mesh'),self.item(1,'interpolate')]}
        self.spec_path=self.root/'spec.json';self.batch_path=self.root/'batch.json'
        self.seal()

    def tearDown(self):self.temp.cleanup()
    def item(self,frame,mode):
        layer={'id':'body','kind':'body','source_a':{'path':str(self.a),'key':['def','TEST',0,0],'native_scale':3},
               'vertices_a':self.va,'triangles':self.triangles}
        if mode=='interpolate':
            layer.update(source_b={'path':str(self.b),'key':['def','TEST',0,4],'native_scale':3},vertices_b=self.vb)
        else:layer['target_vertices_1x']=[[x/3+4,y/3+4] for x,y in self.va]
        return {'id':f'F{frame}','key':['def','TEST',0,frame],'targets':copy.deepcopy(self.geometry),'mode':mode,'time':.5,
                'layers':[layer],'quality':{'reference_body_height_1x':12,'min_height_fraction':.85,'max_height_fraction':1.15,
                                           'foot_y_1x':53/3,'foot_tolerance_1x':.25}}
    def call(self,*args,ok=True):
        result=subprocess.run([str(EXE),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
        self.assertEqual(result.returncode==0,ok,(result.stdout+result.stderr)[-2500:])
        return json.loads(result.stdout if result.stdout.strip() else result.stderr)
    def seal(self,out=None,ok=True):
        save(self.spec_path,self.spec);destination=out or self.batch_path
        result=self.call('motion-plan','--spec',self.spec_path,'--out',destination,ok=ok)
        if ok:self.batch=json.loads(destination.read_bytes())
        return result
    def run_batch(self,command='motion-run',ok=True):
        save(self.batch_path,self.batch)
        return self.call(command,'--batch',self.batch_path,'--workers',2,'--memory-gib',1,ok=ok)
    def output_for(self,result,identity):return Path(next(x['output'] for x in result['results'] if x['id']==identity))
    def exact_high_k_fixture(self, native_scale):
        self.spec['items'] = [self.item(0, 'mesh')]
        frame = self.spec['items'][0]
        layer = frame['layers'][0]
        source = self.root / ('native-k'+str(native_scale)+'.png')
        with Image.open(self.a) as im:
            im.resize((16*native_scale, 16*native_scale), Image.Resampling.NEAREST).save(source)
        source_size_factor = native_scale / 3
        layer['source_a'] = {'path': str(source), 'key': ['def','TEST',0,0],
                             'native_scale': native_scale}
        layer['vertices_a'] = [[x*source_size_factor,y*source_size_factor] for x,y in self.va]
        return source

    def test_native_scale17_exact_source_can_plan_check_run_verify(self):
        source = self.exact_high_k_fixture(17)
        original = sha(source)
        self.seal(self.root / 'native-k17-plan.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result = self.run_batch()
        with Image.open(self.output_for(result,'F0')) as frame:
            self.assertEqual(frame.size,(48,48))
            self.assertEqual(frame.getpixel((20,20))[3],255)
        self.run_batch('motion-verify')
        self.assertEqual(sha(source),original)

    def test_native_scale32_is_bounded_and_scale33_is_rejected(self):
        source = self.exact_high_k_fixture(32)
        self.seal(self.root / 'native-k32-plan.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        self.assertFalse(self.output.exists())
        self.spec['items'][0]['layers'][0]['source_a']['native_scale']=33
        failure=self.seal(self.root / 'native-k33-rejected.json',ok=False)
        self.assertIn('source native scale',str(failure))
        self.assertFalse(self.output.exists())

    def test_legacy_v5_retains_its_explicit_high_target_scale(self):
        self.exact_high_k_fixture(17)
        self.spec['target_scale']=17
        self.seal(self.root/'v5-source-batch.json')
        self.batch['algorithm']='linear-premultiplied-correspondence-mesh-ewa-time-v5'
        result=self.run_batch()
        record=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        self.assertEqual(record['target_scale'],17)
        self.assertEqual(record['output_pixels'],[272,272])
        self.run_batch('motion-verify')
    def skeleton_fixture(self, translation=(1, 0)):
        self.exact_high_k_fixture(24)
        self.spec['target_scale']=24
        frame=self.spec['items'][0];frame['mode']='skeleton'
        layer=frame['layers'][0];layer.pop('target_vertices_1x')
        layer['vertices_a']=[[64,48],[192,48],[64,336],[192,336]]
        layer['triangles']=[[0,1,2],[1,3,2]]
        layer['rest_vertices_1x']=[[x/24+4,y/24+4] for x,y in layer['vertices_a']]
        layer['skeleton']={'bones':[{'id':'root','parent':None,'pivot_1x':[12,12]}],
                           'vertex_weights':[[1] for _ in layer['vertices_a']]}
        frame['pose']={'root':{'translation_1x':list(translation)}}
        return frame

    def test_skeleton_batch_writes_high_resolution_master_candidate(self):
        self.skeleton_fixture()
        self.seal(self.root/'skeleton-master-plan.json')
        self.assertEqual(self.batch['target_scale'],24)
        self.assertEqual(self.batch['algorithm'],'linear-premultiplied-correspondence-mesh-ewa-time-v6')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result=self.run_batch()
        output=self.output_for(result,'F0')
        with Image.open(output) as frame:
            self.assertEqual(frame.size,(384,384))
            self.assertEqual(frame.getchannel('A').getbbox(),(88,48,216,336))
        self.run_batch('motion-verify')
        receipt=json.loads(Path(result['results'][0]['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['target_scale'],24)
        self.assertEqual(receipt['origin_at_target_scale'],[96,96])
        self.assertAlmostEqual(receipt['diagnostics']['foot_y_1x'],53/3,places=6)
        self.assertFalse(receipt['art_approved'])

    def test_skeleton_rejects_invalid_weights_and_crop_exit_before_render(self):
        self.skeleton_fixture()
        self.spec['items'][0]['layers'][0]['skeleton']['vertex_weights'][0]=[.75]
        failure=self.seal(self.root/'skeleton-invalid-weights.json',ok=False)
        self.assertIn('skeleton_vertex_weights_must_sum_to_one',str(failure).lower())
        self.assertFalse(self.output.exists())
        self.skeleton_fixture(translation=(20,0))
        failure=self.seal(self.root/'skeleton-crop-exit.json',ok=False)
        self.assertIn('deformed mesh exceeds original crop',str(failure))
        self.assertFalse(self.output.exists())

    def test_legacy_v4_motion_batch_remains_exact3x(self):
        self.spec['items']=[self.item(0,'mesh')]
        self.seal(self.root/'legacy-v4-plan.json')
        self.batch['algorithm']='linear-premultiplied-correspondence-mesh-ewa-time-v4'
        self.assertEqual(self.batch['target_scale'],3)
        result=self.run_batch()
        with Image.open(self.output_for(result,'F0')) as frame:self.assertEqual(frame.size,(48,48))
        self.run_batch('motion-verify')
        receipt=json.loads(Path(result['results'][0]['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['algorithm'],'linear-premultiplied-correspondence-mesh-ewa-time-v4')
        self.assertEqual(receipt['origin_3x'],[12,12])
        self.assertNotIn('target_scale',receipt)

    def strict_roi_source(self, outside_alpha=0):
        raw=self.root/'strict-full-padded.png'
        with Image.open(self.a) as img:
            full=Image.new('RGBA',(48,52),(0,0,0,0))
            full.paste(img,(0,4))
        if outside_alpha:full.putpixel((0,0),(123,45,67,outside_alpha))
        full.save(raw)
        self.spec['items']=[self.item(0,'mesh')]
        source=self.spec['items'][0]['layers'][0]['source_a']
        source.update(path=str(raw),native_scale=3,source_rect=[0,4,48,48])
        return raw

    def test_strict_source_roi_runs_with_full_source_pixel_identity(self):
        full=self.strict_roi_source()
        original=sha(full)
        self.seal(self.root/'strict-roi-batch.json')
        self.assertEqual(self.batch['items'][0]['layers'][0]['source_a']['pixels'],[48,52])
        self.assertEqual(self.batch['items'][0]['layers'][0]['source_a']['source_rect'],[0,4,48,48])
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result=self.run_batch()
        with Image.open(self.a) as original_crop,Image.open(self.output_for(result,'F0')) as output:
            expected=[p if p[3] else (0,0,0,0) for p in original_crop.get_flattened_data()]
            self.assertEqual(list(output.get_flattened_data()),expected)
        self.run_batch('motion-verify')
        self.assertEqual(sha(full),original)
        receipt=json.loads(Path(result['results'][0]['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['contract']['entry']['layers'][0]['source_a']['source_rect'],[0,4,48,48])
        self.assertFalse(receipt['art_approved'])

    def test_strict_source_roi_rejects_alpha_one_outside_without_writing(self):
        source=self.strict_roi_source(outside_alpha=1)
        prior=sha(source)
        failure=self.seal(self.root/'strict-roi-alpha1-rejected.json',ok=False)
        self.assertIn('positive alpha outside strict source_rect',str(failure))
        self.assertFalse(self.output.exists())
        self.assertEqual(sha(source),prior)

    def test_strict_source_roi_requires_exact_integer_native_crop(self):
        self.strict_roi_source()
        self.spec['items'][0]['layers'][0]['source_a']['source_rect']=[0,4,47,48]
        failure=self.seal(self.root/'strict-roi-geometry-rejected.json',ok=False)
        self.assertIn('source_rect must equal exact native scaled crop',str(failure))
        self.assertFalse(self.output.exists())

    def explicit_native_roi_fixture(self,pixel_rect=(12,12,48,48),native_rect=(4,4,16,16),parent_pixels=(72,72)):
        parent=self.root/'explicit-native-parent.png'
        full=Image.new('RGBA',parent_pixels,(0,0,0,0))
        with Image.open(self.a) as source:
            image=source.convert('RGBA')
            if image.size!=(pixel_rect[2],pixel_rect[3]):image=image.resize((pixel_rect[2],pixel_rect[3]),Image.Resampling.BICUBIC)
            if pixel_rect[0]+pixel_rect[2]<=parent_pixels[0] and pixel_rect[1]+pixel_rect[3]<=parent_pixels[1]:
                full.paste(image,(pixel_rect[0],pixel_rect[1]))
        # Deliberately opaque parent pixels outside the selected native ROI must never leak into the child frame.
        full.putpixel((0,0),(250,30,10,255));full.save(parent)
        native=json.loads(self.native.read_bytes())
        native['entries'][0]['targets']={'canvas_1x':[24,24],'crop_1x':[24,24],'origin_1x':[0,0]}
        native['entries'][1]['targets']=copy.deepcopy(self.geometry)
        save(self.native,native)
        frame=self.item(1,'mesh');layer=frame['layers'][0]
        layer['source_a']={'path':str(parent),'sha256':sha(parent),'key':['def','TEST',0,0],'pixels':list(parent_pixels),
                           'source_layout':'explicit-native-roi','source_region':{'pixel_rect':list(pixel_rect),'native_rect_1x':list(native_rect)}}
        frame['sampling_budget']={'schema':'h3.motion.sampling-budget.v1','max_loop_visits':2**26}
        if (pixel_rect[2],pixel_rect[3])!=(48,48):
            sx=pixel_rect[2]/16;sy=pixel_rect[3]/16
            layer['vertices_a']=[[x*sx/3,y*sy/3] for x,y in self.va]
            layer['target_vertices_1x']=[[4+x/3,4+y/3] for x,y in self.va]
        self.spec['items']=[frame]
        self.output=self.root/'explicit-native-roi-output';self.spec['output_root']=str(self.output)
        return parent,frame

    def rational_native_grid_fixture(self,ratio=(17,4),parent_pixels=(102,102)):
        self.geometry={'canvas_1x':[24,24],'crop_1x':[12,20],'origin_1x':[3,1]}
        native=json.loads(self.native.read_bytes());native['entries'][0]['targets']=copy.deepcopy(self.geometry);save(self.native,native)
        parent=self.root/'rational-grid-parent.png';yy,xx=np.indices((parent_pixels[1],parent_pixels[0]));rgba=np.zeros((parent_pixels[1],parent_pixels[0],4),np.uint8)
        rgba[...,0]=xx%251;rgba[...,1]=yy%251;rgba[...,2]=(xx+yy)%251
        inside_x=(2*xx+1)*4>=2*3*17;inside_x&=(2*xx+1)*4<2*15*17
        inside_y=(2*yy+1)*4>=2*1*17;inside_y&=(2*yy+1)*4<2*21*17
        rgba[...,3][inside_y&inside_x]=255;Image.fromarray(rgba).save(parent)
        frame=self.item(0,'mesh');key=['def','TEST',0,0]
        canvas=self.geometry['canvas_1x']
        if __import__('math').gcd(*ratio)==1 and list(parent_pixels)==[canvas[0]*ratio[0]//ratio[1],canvas[1]*ratio[0]//ratio[1]]:
            layer=rational_native_roi_layer({'path':str(parent),'sha256':sha(parent)},key,self.geometry,list(ratio),[3,1,12,20])['layer']
        else:
            layer={'id':'rational-native-surface','kind':'surface','source_a':{'path':str(parent),'sha256':sha(parent),'key':key,'pixels':list(parent_pixels),
                'source_layout':'rational-native-grid','source_region':{'native_rect_1x':[3,1,12,20],
                    'pixels_per_native_ratio':list(ratio),'allow_target_subregion':True}},
                'vertices_a':[[.75,.25],[51.75,.25],[.75,85.25],[51.75,85.25]],
                'target_vertices_1x':[[3,1],[15,1],[3,21],[15,21]],'triangles':[[0,1,2],[1,3,2]],
                'anchors':[{'vertex':i,'target_1x':p,'tolerance_3x':0} for i,p in enumerate([[3,1],[15,1],[3,21],[15,21]])]}
        frame['layers']=[layer]
        frame['quality']={'mode':'anchored-surface'};frame['sampling_budget']={'schema':'h3.motion.sampling-budget.v1','max_loop_visits':2**26}
        self.spec['items']=[frame];self.output=self.root/'rational-grid-output';self.spec['output_root']=str(self.output)
        return parent,frame

    def test_rational_native_grid_runs_directly_from_fractional_parent_coordinates(self):
        parent,_=self.rational_native_grid_fixture();original=sha(parent)
        self.seal(self.root/'rational-grid-plan.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        probe=self.run_batch('motion-probe');self.assertEqual((probe['ready'],probe['failed'],probe['outputs_written']),(1,0,0))
        result=self.run_batch();output=self.output_for(result,'F0')
        with Image.open(output) as image:self.assertEqual(image.size,(36,60))
        self.run_batch('motion-verify')
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        region=receipt['contract']['source_geometry'][0]['source_region']
        self.assertEqual(region['source_layout'],'rational-native-grid')
        self.assertEqual(region['pixels_per_native_ratio'],[17,4])
        self.assertEqual(region['pixel_rect_rational'],[[51,4],[17,4],[51,1],[85,1]])
        self.assertEqual(region['envelope_pixels'],[12,4,52,86])
        self.assertEqual(region['envelope_offset_rational'],[[3,4],[1,4]])
        self.assertEqual(region['outside_crop_alpha_positive_pixels'],0)
        self.assertEqual(region['outside_crop_visible_threshold8'],16)
        self.assertEqual(sha(parent),original)

    def test_rational_native_grid_rejects_ambiguous_or_mismatched_grids(self):
        self.rational_native_grid_fixture(ratio=(34,8))
        failure=self.seal(self.root/'rational-grid-not-reduced.json',ok=False)
        self.assertIn('MOTION_RATIONAL_GRID_MUST_BE_REDUCED',str(failure));self.assertFalse(self.output.exists())
        self.rational_native_grid_fixture(parent_pixels=(103,102))
        failure=self.seal(self.root/'rational-grid-parent-mismatch.json',ok=False)
        self.assertIn('MOTION_RATIONAL_GRID_PARENT_SIZE_MISMATCH',str(failure));self.assertFalse(self.output.exists())

    def test_rational_native_grid_refuses_to_crop_positive_paint_outside_official_slot(self):
        parent,frame=self.rational_native_grid_fixture();rgba=np.asarray(Image.open(parent).convert('RGBA')).copy()
        rgba[0,0]=[180,40,20,16];Image.fromarray(rgba).save(parent)
        frame['layers'][0]['source_a']['sha256']=sha(parent)
        failure=self.seal(self.root/'rational-grid-positive-outcrop.json',ok=False)
        self.assertIn('MOTION_RATIONAL_SOURCE_HAS_VISIBLE_ALPHA_OUTSIDE_NATIVE_CROP',str(failure))
        self.assertFalse(self.output.exists())

    def test_rational_native_grid_records_and_suppresses_only_subvisible_outcrop_alpha(self):
        parent,frame=self.rational_native_grid_fixture();rgba=np.asarray(Image.open(parent).convert('RGBA')).copy()
        rgba[0,0]=[180,40,20,1];Image.fromarray(rgba).save(parent)
        frame['layers'][0]['source_a']['sha256']=sha(parent);self.seal(self.root/'rational-grid-alpha1-outcrop.json')
        result=self.run_batch();self.run_batch('motion-verify')
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        region=receipt['contract']['source_geometry'][0]['source_region']
        self.assertEqual(region['outside_crop_alpha_positive_pixels'],1);self.assertEqual(region['outside_crop_alpha_max8'],1)
        self.assertEqual(region['outside_crop_visible_pixels_rejected'],True)

    def test_explicit_native_roi_samples_parent_crop_and_preserves_parent_identity(self):
        parent,_=self.explicit_native_roi_fixture()
        parent_sha=sha(parent);self.seal(self.root/'explicit-native-roi.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        probe=self.run_batch('motion-probe')
        self.assertEqual((probe['ready'],probe['failed'],probe['outputs_written']),(1,0,0))
        result=self.run_batch();output=self.output_for(result,'F1')
        with Image.open(self.a) as expected,Image.open(output) as actual:
            pixels=list(expected.get_flattened_data());pixels=[p if p[3] else (0,0,0,0) for p in pixels]
            self.assertEqual(actual.size,(48,48));self.assertEqual(list(actual.get_flattened_data()),pixels)
            self.assertEqual(actual.getpixel((0,0))[3],0)
        self.run_batch('motion-verify')
        self.assertEqual(sha(parent),parent_sha)
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        region=receipt['contract']['source_geometry'][0]['source_region']
        self.assertEqual(region['pixel_rect'],[12,12,48,48]);self.assertEqual(region['native_rect_1x'],[4,4,16,16])
        self.assertFalse(receipt['art_approved'])

    def test_explicit_native_roi_supports_pinned_anisotropic_source_mesh(self):
        self.explicit_native_roi_fixture(pixel_rect=(12,12,64,48),native_rect=(4,4,16,16),parent_pixels=(96,72))
        self.seal(self.root/'explicit-native-roi-anisotropic.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result=self.run_batch('motion-probe')
        self.assertEqual((result['ready'],result['failed'],result['outputs_written']),(1,0,0))
        result=self.run_batch();output=self.output_for(result,'F1')
        with Image.open(output) as actual:
            self.assertEqual(actual.size,(48,48));self.assertGreater(actual.getchannel('A').getbbox()[2],actual.getchannel('A').getbbox()[0])
        self.run_batch('motion-verify')
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        self.assertEqual(receipt['contract']['source_geometry'][0]['source_region']['scale_xy'],[4.0,3.0])

    def test_explicit_native_roi_rejects_outside_or_misregistered_regions(self):
        parent,frame=self.explicit_native_roi_fixture(pixel_rect=(40,40,48,48))
        result=self.seal(self.root/'explicit-native-roi-outside.json',ok=False)
        self.assertIn('pixel_bounds_invalid',str(result).lower())
        self.assertFalse(self.output.exists());self.assertTrue(parent.exists())
        parent,frame=self.explicit_native_roi_fixture()
        frame['layers'][0]['source_a']['source_region']['native_rect_1x']=[5,4,15,16]
        result=self.seal(self.root/'explicit-native-roi-misaligned.json',ok=False)
        self.assertIn('does_not_match_target_native_crop',str(result).lower())
        self.assertFalse(self.output.exists())
        self.explicit_native_roi_fixture(pixel_rect=(12,12,32,32))
        result=self.seal(self.root/'explicit-native-roi-low-resolution.json',ok=False)
        self.assertIn('source_below_3x',str(result).lower())
        self.assertFalse(self.output.exists())

    def test_plan_and_check_are_read_only(self):
        self.assertFalse(self.output.exists());self.run_batch('motion-check');self.assertFalse(self.output.exists())
        self.assertEqual(self.batch['target_scale'],3)
        for path,digest in self.hashes.items():self.assertEqual(sha(path),digest)
    def test_pixel_probe_reports_run_readiness_without_writing_candidates(self):
        report=self.root/'probe.json'
        result=self.call('motion-probe','--batch',self.batch_path,'--report',report)
        self.assertEqual(result['schema'],'h3.motion.probe.v1')
        self.assertEqual((result['ready'],result['failed'],result['outputs_written']),(2,0,0))
        self.assertEqual([x['status'] for x in result['results']],['READY_FOR_RUN','READY_FOR_RUN'])
        self.assertEqual(json.loads(report.read_bytes()),result)
        self.assertFalse(self.output.exists())
    def test_pixel_probe_rejects_incompatible_pair_without_writing_candidates(self):
        self.batch['items'][1]['layers'][0]['vertices_b']=copy.deepcopy(self.va)
        result=self.run_batch('motion-probe',ok=False)
        self.assertEqual((result['ready'],result['failed'],result['outputs_written']),(1,1,0))
        failed=next(x for x in result['results'] if x['id']=='F1')
        self.assertEqual(failed['status'],'NEEDS_KEYFRAME')
        self.assertIn('incompatible aligned body pair',failed['error'])
        self.assertFalse(self.output.exists())
    def test_identity_retains_pixels_and_clears_invisible_rgb(self):
        result=self.run_batch()
        with Image.open(self.a) as source,Image.open(self.output_for(result,'F0')) as output:
            expected=[p if p[3] else (0,0,0,0) for p in source.get_flattened_data()]
            self.assertEqual(list(output.get_flattened_data()),expected);self.assertEqual(output.size,(48,48))
        self.run_batch('motion-verify')
    def test_aligned_intermediate_is_one_silhouette_without_ghosts(self):
        result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as image:
            self.assertEqual(image.getchannel('A').getbbox(),(14,6,30,42))
            self.assertEqual(sum(1 for p in image.get_flattened_data() if p[3]),16*36)
            self.assertEqual(image.getpixel((8,20))[3],0)
            self.assertEqual(image.getpixel((35,20))[3],0)
            self.assertEqual(image.getpixel((20,20))[3],255)
        self.run_batch('motion-verify')
    def test_receipts_do_not_grant_art_native_or_game_approval(self):
        result=self.run_batch()
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        for field in ('model_inference_performed','model_native_output','human_approved','art_approved','game_deployed','runtime_accepted','native_timing_verified'):
            self.assertIs(receipt[field],False)
        self.assertEqual(receipt['diagnostics']['foot_error_1x'],0)
        self.assertEqual(receipt['diagnostics']['height_fraction'],1)
    def test_true_endpoints_remain_the_aligned_keyframe(self):
        self.batch['items'][1]['time']=1
        result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as image:
            self.assertEqual(image.getchannel('A').getbbox(),(20,6,36,42))
            self.assertEqual(image.getpixel((24,20)),(60,160,230,255))
    def test_unaligned_silhouette_is_rejected_before_publishing(self):
        self.batch['items'][1]['layers'][0]['vertices_b']=copy.deepcopy(self.va)
        result=self.run_batch(ok=False)
        failed=next(x for x in result['results'] if x['id']=='F1')
        self.assertEqual(failed['status'],'NEEDS_KEYFRAME');self.assertIn('incompatible aligned body pair',failed['error'])
        self.assertFalse((self.output/'frames/F1').exists())
    def pose_batch(self):
        self.spec['items']=[self.item(1,'mesh')];layer=self.spec['items'][0]['layers'][0];layer.pop('target_vertices_1x')
        layer.update(pose_a_1x=[[x/3+4,y/3+4] for x,y in self.va],pose_b_1x=[[x/3+4,y/3+4] for x,y in self.vb],pose_a_key=['def','TEST',0,0],pose_b_key=['def','TEST',0,4])
        return layer
    def test_same_texture_pose_interpolation_has_one_sharp_body(self):
        self.pose_batch();self.seal(self.root/'poses.json');result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as image:
            self.assertEqual(image.getchannel('A').getbbox(),(14,6,30,42));self.assertEqual(image.getpixel((20,20)),(30,90,210,255))
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes());self.assertTrue(receipt['diagnostics']['layers'][0]['pose_interpolated']);self.run_batch('motion-verify')
    def test_same_texture_pose_key_cannot_invent_an_original_slot(self):
        layer=self.pose_batch();layer['pose_b_key']=['def','TEST',0,999];self.seal(self.root/'bad-pose.json',ok=False)
        self.assertFalse(self.output.exists())
    def test_hidden_pose_fold_is_rejected_even_if_intermediate_is_valid(self):
        layer=self.pose_batch();layer['pose_b_1x'][5]=[19,19];self.seal(self.root/'folded-pose.json',ok=False)
        self.assertFalse(self.output.exists())
    def test_incompatible_internal_texture_is_rejected(self):
        for path,color in [(self.a,(255,255,255,255)),(self.b,(0,0,0,255))]:
            with Image.open(path) as image:
                image.putdata([color if p[3] else p for p in image.get_flattened_data()]);image.save(path)
        self.seal(self.root/'texture.json');result=self.run_batch(ok=False)
        self.assertIn('color_error=1.000000',next(x['error'] for x in result['results'] if x['id']=='F1'))
    def test_pair_alignment_is_reported_without_art_approval(self):
        result=self.run_batch();receipt=json.loads(Path(next(x['receipt'] for x in result['results'] if x['id']=='F1')).read_bytes())
        alignment=receipt['diagnostics']['layers'][0]['alignment']
        self.assertAlmostEqual(alignment['weighted_alpha_overlap'],1)
        self.assertLess(alignment['mean_straight_linear_rgb_error'],.18)
        self.assertTrue(alignment['visual_review_required']);self.assertFalse(receipt['art_approved'])
    def test_incompatible_pair_does_not_reject_true_endpoint(self):
        self.batch['items'][1]['layers'][0]['vertices_b']=copy.deepcopy(self.va)
        self.batch['items'][1]['time']=1;result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as output:self.assertEqual(output.getchannel('A').getbbox(),(20,6,36,42))
    def test_source_hash_drift_blocks_before_writing(self):
        Image.new('RGBA',(48,48),(0,0,0,0)).save(self.a)
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_native_contract_drift_blocks_before_writing(self):
        native=json.loads(self.native.read_bytes());native['entries'][0]['targets']['origin_1x'][0]+=1;save(self.native,native)
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_forged_target_geometry_is_rejected(self):
        self.batch['items'][0]['targets']['crop_1x'][0]+=1
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_rejected_spec_reports_the_frame_and_actionable_cause(self):
        self.spec['items'][0]['targets']['crop_1x'][0]+=1
        result=self.seal(self.root/'bad-spec.json',ok=False)
        self.assertIn('F0: original target geometry changed',result['error']);self.assertFalse(self.output.exists())
    def test_folded_mesh_requires_a_keyframe(self):
        self.batch['items'][0]['layers'][0]['target_vertices_1x'][5]=[19,19]
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_visible_pixels_outside_mesh_are_rejected(self):
        self.batch['items'][0]['layers'][0]['triangles']=[[0,1,4],[1,5,4]]
        result=self.run_batch(ok=False)
        self.assertEqual(next(x['status'] for x in result['results'] if x['id']=='F0'),'NEEDS_KEYFRAME')
    def _probe_body_hull_with_outside_alpha(self,alpha,ok):
        with Image.open(self.a) as image:
            image.putpixel((1,1),(220,90,30,alpha));image.save(self.a)
        self.spec['items']=self.spec['items'][:1]
        layer=self.spec['items'][0]['layers'][0]
        layer['vertices_a']=[[8,6],[24,6],[8,42],[24,42]]
        layer['target_vertices_1x']=[[x/3+4,y/3+4] for x,y in layer['vertices_a']]
        layer['triangles']=[[0,1,2],[1,3,2]]
        self.seal(self.root/'outside-alpha.json')
        result=self.run_batch('motion-probe',ok=ok)
        self.assertEqual(result['outputs_written'],0);self.assertFalse(self.output.exists())
        return result['results'][0]
    def test_unmapped_alpha_one_pixel_requires_keyframe_before_output(self):
        result=self._probe_body_hull_with_outside_alpha(1,False)
        self.assertEqual(result['status'],'NEEDS_KEYFRAME')
        self.assertIn('mesh hull discards visible source pixels',result['error'])
    def test_unmapped_alpha_zero_pixel_is_exempt_from_source_coverage(self):
        self.assertEqual(self._probe_body_hull_with_outside_alpha(0,True)['status'],'READY_FOR_RUN')
    def test_v3_motion_contract_cannot_reuse_v4_algorithm(self):
        self.assertTrue(self.batch['algorithm'].endswith('-v6'))
        self.batch['algorithm']='linear-premultiplied-correspondence-mesh-ewa-time-v3'
        result=self.run_batch('motion-check',ok=False)
        self.assertIn('unsupported motion contract',result['error']);self.assertFalse(self.output.exists())
    def test_pose_anchor_movement_is_rejected(self):
        self.batch['items'][0]['layers'][0]['anchors']=[{'vertex':5,'target_1x':[4,4],'tolerance_3x':.25}]
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_foot_position_is_measured_and_can_fail(self):
        self.batch['items'][0]['quality']['foot_y_1x']=16
        result=self.run_batch(ok=False)
        self.assertIn('foot position drift',next(x['error'] for x in result['results'] if x['id']=='F0'))
    def test_body_volume_is_measured_and_can_fail(self):
        self.batch['items'][0]['quality']['reference_body_height_1x']=16
        result=self.run_batch(ok=False)
        self.assertIn('body volume',next(x['error'] for x in result['results'] if x['id']=='F0'))
    def test_original_duplicate_is_a_byte_copy(self):
        self.batch['items'].append({'id':'F2','key':['def','TEST',0,2],'targets':self.geometry,'mode':'reuse','reuse_from':'F0'})
        result=self.run_batch();self.assertEqual(result['duplicate_slots'],1)
        self.assertEqual(self.output_for(result,'F0').read_bytes(),self.output_for(result,'F2').read_bytes())
        self.run_batch('motion-verify')
    def test_non_equivalent_reuse_is_rejected(self):
        self.batch['items'].append({'id':'F3','key':['def','TEST',0,3],'targets':self.geometry,'mode':'reuse','reuse_from':'F0'})
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_exact3x_reuse_keeps_original_png_bytes(self):
        self.batch['items']=[{'id':'F0','key':['def','TEST',0,0],'targets':self.geometry,'mode':'reuse','source':self.batch['items'][0]['layers'][0]['source_a']}]
        result=self.run_batch();self.assertEqual(self.output_for(result,'F0').read_bytes(),self.a.read_bytes())
        self.run_batch('motion-verify')
    def test_transparent_reframe_preserves_all_visible_source_pixels(self):
        padded=self.root/'padded.png'
        with Image.open(self.a) as original:
            image=Image.new('RGBA',(51,48),(0,0,0,0));image.paste(original,(3,0));image.save(padded)
        self.spec['items']=[{'id':'F0','key':['def','TEST',0,0],'targets':self.geometry,'mode':'reframe',
                             'source':{'path':str(padded),'key':['def','TEST',0,0],'native_scale':3},'source_rect':[3,0,48,48]},
                            {'id':'F2','key':['def','TEST',0,2],'targets':self.geometry,'mode':'reuse','reuse_from':'F0'}]
        self.seal(self.root/'reframe-batch.json')
        probe=self.run_batch('motion-probe');self.assertEqual((probe['ready'],probe['outputs_written']),(2,0));self.assertFalse(self.output.exists())
        result=self.run_batch()
        with Image.open(self.a) as original,Image.open(self.output_for(result,'F0')) as output:
            self.assertEqual(list(output.get_flattened_data()),list(original.get_flattened_data()))
        self.assertEqual(self.output_for(result,'F0').read_bytes(),self.output_for(result,'F2').read_bytes())
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        self.assertTrue(receipt['diagnostics']['all_visible_source_pixels_preserved'])
        self.assertFalse(receipt['diagnostics']['resampling_performed'])
        self.run_batch('motion-verify')
    def test_reframe_rejects_even_one_visible_pixel_outside_crop(self):
        padded=self.root/'padded-with-pixel.png'
        with Image.open(self.a) as original:
            image=Image.new('RGBA',(51,48),(0,0,0,0));image.paste(original,(3,0));image.putpixel((0,20),(200,100,40,1));image.save(padded)
        self.spec['items']=[{'id':'F0','key':['def','TEST',0,0],'targets':self.geometry,'mode':'reframe',
                             'source':{'path':str(padded),'key':['def','TEST',0,0],'native_scale':3},'source_rect':[3,0,48,48]}]
        self.seal(self.root/'bad-reframe-batch.json')
        probe=self.run_batch('motion-probe',ok=False)
        self.assertEqual(probe['results'][0]['status'],'NEEDS_KEYFRAME')
        self.assertEqual(probe['outputs_written'],0)
        result=self.run_batch(ok=False)
        self.assertEqual(result['results'][0]['status'],'NEEDS_KEYFRAME')
        self.assertFalse((self.output/'frames/F0').exists())
    def test_cross_creature_texture_is_rejected(self):
        self.batch['items'][1]['layers'][0]['source_b']['key'][1]='OTHER'
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_unchanged_frame_reuses_after_other_keyframe_changes(self):
        first=self.run_batch();first_path=self.output_for(first,'F0');first_hash=sha(first_path)
        with Image.open(self.b) as image:image.putpixel((24,20),(80,190,210,255));image.save(self.b)
        self.seal(self.root/'batch2.json');second=self.run_batch()
        self.assertEqual(next(x['status'] for x in second['results'] if x['id']=='F0'),'REUSED_VERIFIED')
        self.assertEqual(self.output_for(second,'F0'),first_path);self.assertEqual(sha(first_path),first_hash)
        self.assertNotEqual(self.output_for(first,'F1'),self.output_for(second,'F1'))
        self.run_batch('motion-verify')
    def test_different_existing_output_is_never_overwritten(self):
        checked=self.run_batch('motion-check');path=Path(checked['items'][0]['output']);path.parent.mkdir(parents=True)
        Image.new('RGBA',(48,48),(1,2,3,255)).save(path);before=sha(path)
        self.run_batch(ok=False);self.assertEqual(sha(path),before)
    def test_authority_tampering_is_rejected_on_replay(self):
        result=self.run_batch();path=Path(result['results'][0]['receipt']);receipt=json.loads(path.read_bytes());receipt['human_approved']=True;save(path,receipt)
        self.run_batch('motion-verify',ok=False)
    def test_temporal_fraction_and_exact3x_are_checked(self):
        self.batch['items'][1]['time']=1.01
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
        self.batch['items'][1]['time']=.5;self.batch['target_scale']=33
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
    def test_original_requested_order_survives_parallel_execution(self):
        self.batch['items']=list(reversed(self.batch['items']))
        result=self.run_batch();self.assertEqual([x['id'] for x in result['results']],['F1','F0'])
    def test_gui_exposes_motion_without_approval(self):
        result=self.call('ui-smoke');self.assertEqual(result['controls'],result['expected_controls'])
        self.assertGreaterEqual(result['controls'],21)
        self.assertTrue(result['motion_approval_disabled']);self.assertTrue(result['motion_batch_reseal_disabled'])
        self.assertTrue(result['shared_companion_route']);self.assertTrue(result['unicode_path_quoted'])
        self.assertGreater(result['new_task_routes'],0);self.assertGreaterEqual(result['review_task_routes'],2)
    def test_preflight_hashes_only_the_two_distinct_source_paths(self):
        result=self.run_batch('motion-check');self.assertEqual(result['unique_sources_checked'],2)
    def test_effect_sparks_do_not_move_the_body_foot_measurement(self):
        effect=self.root/'spark.png';image=Image.new('RGBA',(48,48),(0,0,0,0));image.putpixel((15,46),(250,120,20,255));image.save(effect)
        layer=copy.deepcopy(self.spec['items'][0]['layers'][0]);layer['id']='spark';layer['kind']='effect';layer['source_a']['path']=str(effect)
        self.spec['items'][0]['layers'].append(layer);self.seal(self.root/'effects.json');result=self.run_batch()
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        self.assertEqual(receipt['diagnostics']['foot_y_1x'],53/3)
        with Image.open(self.output_for(result,'F0')) as output:self.assertEqual(output.getpixel((15,46)),(250,120,20,255))
    def test_partial_alpha_interpolates_in_premultiplied_space(self):
        for path,alpha in [(self.a,128),(self.b,64)]:
            with Image.open(path) as image:
                pixels=[(*p[:3],alpha) if p[3] else p for p in image.get_flattened_data()];image.putdata(pixels);image.save(path)
        self.seal(self.root/'partial.json');result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as output:self.assertEqual(output.getpixel((20,20))[3],96)
        self.run_batch('motion-verify')
    def registered_surface_fixture(self, k=111, raw_size=220, offset=(1,1), crop=2):
        self.geometry={'canvas_1x':[crop+2,crop+2],'crop_1x':[crop,crop],'origin_1x':[1,1]}
        native=json.loads(self.native.read_bytes())
        for row in native['entries']:row['targets']=copy.deepcopy(self.geometry)
        save(self.native,native)
        source=self.root/'TEST_ONLY-registered-raw.png'
        image=Image.new('RGBA',(raw_size,raw_size),(64,120,200,128))
        image.putpixel((0,0),(64,120,200,1));image.putpixel((raw_size-1,raw_size-1),(64,120,200,1));image.save(source)
        x,y=offset;vertices=[[x,y],[x+raw_size,y],[x,y+raw_size],[x+raw_size,y+raw_size]]
        targets=[[a/k+1,b/k+1] for a,b in vertices]
        layer={'id':'surface','kind':'surface','source_a':{'path':str(source),'native_scale':k,'key':['def','TEST',0,0],
               'source_layout':'integer-transparent-padding','padding_registration':{'common_k':k,'offset':list(offset)}},
               'vertices_a':vertices,'target_vertices_1x':targets,'triangles':[[0,1,2],[1,3,2]],
               'anchors':[{'vertex':i,'target_1x':targets[i],'tolerance_3x':0} for i in (0,1)]}
        self.spec['items']=[{'id':'F0','key':['def','TEST',0,0],'targets':copy.deepcopy(self.geometry),'mode':'mesh',
                            'sampling_budget':{'schema':'h3.motion.sampling-budget.v1','max_loop_visits':1<<28},
                            'quality':{'mode':'anchored-surface'},'layers':[layer]}]
        return source,layer

    def test_registered111_surface_padding_is_exact_and_replay_binds_work(self):
        source,layer=self.registered_surface_fixture();original=sha(source)
        self.seal(self.root/'registered111.json')
        checked=self.run_batch('motion-check');admitted=checked['items'][0]['sampling_work_admission']
        self.assertGreater(admitted['maximum_lambda_JJt'],256)
        self.assertLessEqual(admitted['total_visits'],1<<28)
        result=self.run_batch();path=self.output_for(result,'F0');receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes())
        diagnostics=receipt['diagnostics'];self.assertEqual(diagnostics['sampling_work'],admitted)
        self.assertEqual(diagnostics['quality_mode'],'anchored-surface');self.assertNotIn('foot_error_1x',diagnostics)
        coverage=diagnostics['layers'][0]['source_a_coverage']
        self.assertEqual(coverage['positive_pixels_covered'],220*220)
        self.assertEqual(coverage['alpha_le_1_over_255_pixels'],2)
        self.assertEqual(coverage['positive_pixels_missing'],0)
        with Image.open(path) as image:self.assertEqual(image.size,(6,6));expected=image.tobytes()
        self.assertEqual(len(list(self.output.rglob('*.png'))),1)
        self.run_batch('motion-verify');self.assertEqual(self.run_batch()['results'][0]['status'],'REUSED_VERIFIED')
        self.assertEqual(sha(source),original)
        # Independent integer-copy oracle: explicit transparent padding has
        # identical samples to the virtual registered raw, without a resize.
        padded=self.root/'TEST_ONLY-explicit-padding.png'
        with Image.open(source) as raw:
            image=Image.new('RGBA',(222,222));image.paste(raw,(1,1));image.save(padded)
        layer['source_a'].update(path=str(padded));layer['source_a']['padding_registration']['offset']=[0,0]
        self.spec['output_root']=str(self.root/'explicit-padding-output')
        self.seal(self.root/'explicit-padding.json');second=self.run_batch()
        with Image.open(self.output_for(second,'F0')) as image:self.assertEqual(image.tobytes(),expected)
        before=sha(self.output_for(second,'F0'));padded.write_bytes(padded.read_bytes()+b'changed')
        self.assertEqual(self.run_batch('motion-check',ok=False)['blocked'],1)
        self.assertEqual(sha(self.output_for(second,'F0')),before)

    def test_registered_lowalpha_uncovered_mesh_rejects_without_png(self):
        source,layer=self.registered_surface_fixture(42,82)
        image=Image.new('RGBA',(82,82))
        for y in range(12,68):
            for x in range(12,68):image.putpixel((x,y),(50,120,210,255))
        image.putpixel((81,81),(50,120,210,1));image.save(source)
        layer['vertices_a']=[[1,1],[82,1],[1,82],[82,82]]
        layer['target_vertices_1x']=[[x/42+1,y/42+1] for x,y in layer['vertices_a']]
        layer['anchors']=[{'vertex':i,'target_1x':layer['target_vertices_1x'][i],'tolerance_3x':0} for i in (0,1)]
        self.seal(self.root/'uncovered-alpha1.json');result=self.run_batch('motion-probe',ok=False)
        self.assertEqual(result['failed'],1);self.assertIn('mesh hull discards',str(result))
        self.assertFalse(self.output.exists())

    def test_registered_limits_and_declarations_reject_before_outputs(self):
        self.spec['items']=[self.item(0,'mesh')]
        self.spec['items'][0]['layers'][0]['source_a']['source_layout']='unrecognised-layout'
        self.assertIn('MOTION_SOURCE_LAYOUT_UNSUPPORTED',str(self.seal(self.root/'legacy-unknown-layout-rejected.json',ok=False)))
        self.assertFalse(self.output.exists())
        self.registered_surface_fixture(3,6,(0,0));original=copy.deepcopy(self.spec)
        cases=[('clip',lambda j:j['layers'][0]['source_a']['padding_registration'].update(offset=[1,0]),'WOULD_CLIP_SOURCE'),
               ('unknown-layout',lambda j:j['layers'][0]['source_a'].update(source_layout='unrecognised-layout'),'MOTION_SOURCE_LAYOUT_UNSUPPORTED'),
               ('boolean-layout',lambda j:j['layers'][0]['source_a'].update(source_layout=True),'MOTION_SOURCE_LAYOUT_UNSUPPORTED'),
               ('echo',lambda j:j['layers'][0]['source_a'].update(native_scale=4),'SCALE_ECHO_MISMATCH'),
               ('zero',lambda j:j['sampling_budget'].update(max_loop_visits=0),'max_loop_visits'),
               ('bool',lambda j:j['sampling_budget'].update(max_loop_visits=True),'max_loop_visits'),
               ('excess',lambda j:j['sampling_budget'].update(max_loop_visits=(1<<28)+1),'max_loop_visits'),
               ('layout',lambda j:j['layers'][0]['source_a'].pop('source_layout'),'REQUIRES_EXPLICIT_LAYOUT')]
        for name,mutate,error in cases:
            with self.subTest(case=name):
                self.spec=copy.deepcopy(original);mutate(self.spec['items'][0])
                self.assertIn(error,str(self.seal(self.root/(name+'-rejected.json'),ok=False)))
                self.assertFalse(self.output.exists())
        self.registered_surface_fixture()
        layer=self.spec['items'][0]['layers'][0];top=layer['target_vertices_1x'][0][1]
        layer['target_vertices_1x']=[[x,top+(y-top)*.25] for x,y in layer['target_vertices_1x']]
        self.assertIn('SINGLE_SAMPLE_WORK_LIMIT',str(self.seal(self.root/'footprint-work-rejected.json',ok=False)))
        self.assertFalse(self.output.exists())

    def test_sampling_batch_work_limit_refuses_combined_jobs(self):
        source,layer=self.registered_surface_fixture(45,1,(0,0),32)
        layer['vertices_a']=[[0,0],[1440,0],[0,1440],[1440,1440]]
        layer['target_vertices_1x']=[[1,1],[33,1],[1,33],[33,33]]
        layer['anchors']=[{'vertex':i,'target_1x':layer['target_vertices_1x'][i],'tolerance_3x':0} for i in (0,1)]
        first=copy.deepcopy(self.spec['items'][0]);native=json.loads(self.native.read_bytes());row=copy.deepcopy(native['entries'][0]);native['entries']=[];self.spec['items']=[]
        for frame in range(9):
            slot=copy.deepcopy(row);slot['key'][3]=frame;native['entries'].append(slot)
            item=copy.deepcopy(first);item['id']='F'+str(frame);item['key'][3]=frame;self.spec['items'].append(item)
        save(self.native,native)
        self.assertIn('BATCH_SAMPLING_WORK_LIMIT',str(self.seal(self.root/'batch-budget-rejected.json',ok=False)))
        self.assertFalse(self.output.exists())

    def test_surface_mount_contract_rejects_fake_or_missing_mounts(self):
        self.registered_surface_fixture(3,6,(0,0));original=copy.deepcopy(self.spec)
        def move_unbound(j):
            layer=j['layers'][0];layer['target_vertices_1x'][0][0]+=.1
            layer['anchors'][0]['target_1x']=list(layer['target_vertices_1x'][0])
        def unregistered(j):
            source=j['layers'][0]['source_a'];source.pop('source_layout');source.pop('padding_registration')
        cases=[('missing',lambda j:j['layers'][0].update(anchors=[]),'FIXED_ANCHORS_REQUIRED'),
               ('duplicate',lambda j:j['layers'][0]['anchors'].append(copy.deepcopy(j['layers'][0]['anchors'][0])),'DUPLICATE_ANCHOR'),
               ('unbound',move_unbound,'ANCHOR_NOT_SOURCE_BOUND'),
               ('body',lambda j:j['layers'][0].update(kind='body'),'SURFACE_LAYER_KIND_REQUIRED'),
               ('unregistered',unregistered,'SURFACE_REQUIRES_REGISTERED_SOURCE'),
               ('fakefoot',lambda j:j['quality'].update(foot_y_1x=2),'UNKNOWN_FIELD')]
        for name,mutate,error in cases:
            with self.subTest(case=name):
                self.spec=copy.deepcopy(original);mutate(self.spec['items'][0])
                self.assertIn(error,str(self.seal(self.root/(name+'-mount-rejected.json'),ok=False)))
                self.assertFalse(self.output.exists())

    def test_registered_surface_encoded_primary_support_is_required_without_erasing_alpha1(self):
        for visible_effect in (False, True):
            with self.subTest(visible_effect=visible_effect):
                source,layer=self.registered_surface_fixture()
                image=Image.new('RGBA',(220,220));image.putpixel((110,110),(64,120,200,1));image.save(source)
                self.output=self.root/('encoded-empty-with-effect' if visible_effect else 'encoded-empty-primary')
                self.spec['output_root']=str(self.output)
                if visible_effect:
                    effect_source=self.root/'TEST_ONLY-visible-effect.png'
                    Image.new('RGBA',(220,220),(60,120,200,128)).save(effect_source)
                    effect=copy.deepcopy(layer);effect.update(id='effect',kind='effect',anchors=[])
                    effect['source_a']['path']=str(effect_source);self.spec['items'][0]['layers'].append(effect)
                self.seal(self.root/('encoded-empty-'+str(visible_effect)+'.json'))
                probe=self.run_batch('motion-probe',ok=False)
                self.assertEqual(probe['failed'],1)
                self.assertIn('primary surface empty after alpha quantization',str(probe))
                self.assertFalse(self.output.exists())
                failed=self.run_batch(ok=False)
                self.assertEqual(failed['failed'],1)
                self.assertEqual(list(self.output.rglob('*.png')),[])
        source,layer=self.registered_surface_fixture()
        Image.new('RGBA',(220,220),(64,120,200,1)).save(source)
        self.output=self.root/'encoded-faint-primary';self.spec['output_root']=str(self.output)
        self.seal(self.root/'encoded-faint-primary.json');result=self.run_batch()
        receipt=json.loads(Path(result['results'][0]['receipt']).read_bytes());metrics=receipt['diagnostics']
        self.assertGreater(metrics['positive_support_after_quantization'],0)
        self.assertEqual(metrics['positive_support_after_quantization'],metrics['alpha1_pixels_after_quantization'])
        self.assertEqual(metrics['layers'][0]['source_a_coverage']['positive_pixels_covered'],220*220)
        with Image.open(self.output_for(result,'F0')) as output:
            self.assertEqual(set(output.getchannel('A').get_flattened_data()),{1})
        self.run_batch('motion-verify')

    def test_projection_cannot_change_its_original_frozen_binding(self):
        native=json.loads(self.native.read_bytes());proof=self.root/'original-proof.json'
        save(proof,{'frames':[{'key':x['key'],'targets':x['targets'],'source_equivalence_class':x['equivalence_class']} for x in native['entries']]})
        native['projection_source']={'path':str(proof),'sha256':sha(proof)};native['entries'][1]['targets']=copy.deepcopy(self.geometry);native['entries'][1]['targets']['origin_1x'][0]+=1
        save(self.native,native);self.seal(self.root/'bad-projection.json',ok=False);self.assertFalse(self.output.exists())

    def test_skeleton_bone_rgb_gain_modulates_brightness_and_bounds(self):
        self.skeleton_fixture(translation=(0,0))
        self.spec['items'][0]['pose']['root']['rgb_gain']=[1.5,1.5,1.5]
        self.seal(self.root/'skeleton-rgb-gain.json')
        result=self.run_batch()
        with Image.open(self.output_for(result,'F0')) as gained:
            p_gain=gained.getpixel((100,100))
        self.assertEqual(p_gain[3],255)
        self.assertGreater(p_gain[2],210)
        self.run_batch('motion-verify')
        self.spec['items'][0]['pose']['root']['rgb_gain']=[2.5,1.0,1.0]
        self.assertIn('SKELETON_RGB_GAIN_OUT_OF_RANGE',str(self.seal(self.root/'skeleton-rgb-gain-rejected.json',ok=False)))

    def test_paired_skeleton_blends_two_sources_and_poses(self):
        frame=self.skeleton_fixture(translation=(0,0))
        source_b=self.root/'native-k24-b.png'
        with Image.open(self.a) as im:
            im.resize((16*24,16*24),Image.Resampling.NEAREST).save(source_b)
        layer=frame['layers'][0]
        layer['source_b']={'path':str(source_b),'key':['def','TEST',0,4],'native_scale':24}
        layer['vertices_b']=copy.deepcopy(layer['vertices_a'])
        layer['rest_vertices_b_1x']=copy.deepcopy(layer['rest_vertices_1x'])
        frame['time']=0.5
        frame['pose_b']={'root':{'translation_1x':[0,0],'rgb_gain':[0.6,0.6,0.6]}}
        self.seal(self.root/'paired-skeleton.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result=self.run_batch()
        with Image.open(self.output_for(result,'F0')) as out_im:
            self.assertEqual(out_im.size,(384,384))
            self.assertEqual(out_im.getpixel((100,100))[3],255)
        self.run_batch('motion-verify')

    def test_explicit_native_roi_allow_target_subregion_permits_overlapping_parent_crop(self):
        parent,frame=self.explicit_native_roi_fixture(pixel_rect=(12,12,48,48),native_rect=(5,4,15,16),parent_pixels=(72,72))
        layer=frame['layers'][0]
        layer['source_a']['source_region']['allow_target_subregion']=True
        layer['vertices_a']=[[0,0],[48,0],[0,48],[48,48]]
        layer['triangles']=[[0,1,2],[1,3,2]]
        layer['target_vertices_1x']=[[5,4],[20,4],[5,20],[20,20]]
        self.seal(self.root/'explicit-roi-subregion.json')
        self.assertEqual(self.run_batch('motion-check')['ready'],1)
        result=self.run_batch()
        with Image.open(self.output_for(result,'F1')) as out_im:
            self.assertEqual(out_im.size,(48,48))
        self.run_batch('motion-verify')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(MotionContract)
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    print(json.dumps({'schema':'h3.motion.contract-tests.v1','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
