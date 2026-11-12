"""End-to-end EXE tests. All masters, approvals and PNGs are synthetic fixtures.

The test never reads user master files or writes actual gemini-Pcx directories.

"""

from pathlib import Path

import copy

import hashlib

import json

import subprocess

import tempfile

import unittest

from PIL import Image



PRODUCT = Path(__file__).resolve().parents[1]

EXE = PRODUCT/'build/candidate/H3TierExport.exe'





def digest(data): return hashlib.sha256(data).hexdigest()

def save(path, value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')





class NativeExeTests(unittest.TestCase):

    def setUp(self):

        parent=PRODUCT/'build/tests';parent.mkdir(parents=True,exist_ok=True)

        self.temp=tempfile.TemporaryDirectory(prefix='synthetic-',dir=parent)

        self.root=Path(self.temp.name)

        self.master=self.root/'master.png'

        im=Image.new('RGBA',(512,512),(140,70,210,200));im.save(self.master)

        self.geometry={}

        for s in (1,2,3):

            self.geometry.update({'canvas_'+str(s)+'x':[66*s,67*s],'crop_'+str(s)+'x':[64*s,64*s],'origin_'+str(s)+'x':[s,2*s]})

        self.item={'id':'TEST_ARTIFACT','key':['def','TESTART',0,7],'category':'artifact','output_master':str(self.master),'complete_target_geometry':self.geometry}

        self.batch={'schema':'synthetic_batch_fixture','output_root':str(self.root),'items':[self.item]}

        for s in (1,2,3):self.batch['pcx'+str(s)+'x_output_dir']=str(self.root/('out'+str(s)))

        self.batchpath=self.root/'batch.json';save(self.batchpath,self.batch)

        self.planpath=self.root/'pending.json'

        c,r=self.call('plan','--batch',self.batchpath,'--out',self.planpath)

        self.assertEqual(c,0,r);self.plan=json.loads(self.planpath.read_bytes())

        self.default_plan=copy.deepcopy(self.plan)
        self.plan=self.as_legacy_plan(self.plan)
        self.plan['settings'].update({'backend':'cpu','workers':4,'memory_gib':2})

        save(self.planpath,self.plan)



    def tearDown(self): self.temp.cleanup()



    def call(self,*args):

        p=subprocess.run([str(EXE),*[str(a) for a in args]],capture_output=True,text=True,encoding='utf-8',timeout=90)

        text=(p.stdout or p.stderr).strip()

        try:r=json.loads(text)

        except Exception:r={'output':text,'stderr':p.stderr}

        return p.returncode,r



    def approve(self,plan=None,filename='approved.json'):

        if plan is not None:
            plan=self.as_legacy_plan(plan);self.plan=plan;save(self.planpath,plan)

        out=self.root/filename

        code,r=self.call('approve','--plan',self.planpath,'--id','TEST_ARTIFACT','--evidence','SYNTHETIC regression fixture only; not a real user art approval','--out',out)

        self.assertEqual(code,0,r)

        return out,json.loads(out.read_bytes())



    def as_legacy_plan(self,plan):

        plan=copy.deepcopy(plan)
        for job in plan['jobs']:
            batch=json.loads(Path(job['source_batch']).read_bytes())
            for view in job['views']:
                if set(view['outputs'])!={'3'}:continue
                key=view['key'];kind,ident=key[0],key[1]
                if kind=='pcx':native='Pcx/'+ident+'.png'
                else:native='Defs/'+ident+'.DEF/'+str(key[2])+'_'+str(key[3])+'.png'
                view['outputs']={str(scale):str(Path(batch['pcx'+str(scale)+'x_output_dir'])/'tier-v1'/native) for scale in (1,2,3)}
        return plan

    def make_3x_plan_without_lower_dirs(self,batch_name='batch-3x-only.json',plan_name='pending-3x-only.json'):

        batch=copy.deepcopy(self.batch)
        batch.pop('pcx1x_output_dir',None);batch.pop('pcx2x_output_dir',None)
        batchpath=self.root/batch_name;save(batchpath,batch)
        planpath=self.root/plan_name
        code,r=self.call('plan','--batch',batchpath,'--out',planpath)
        self.assertEqual(code,0,r)
        plan=json.loads(planpath.read_bytes())
        self.assertTrue(all(set(view['outputs'])=={'3'} for job in plan['jobs'] for view in job['views']))
        self.planpath=planpath;self.plan=plan;self.batchpath=batchpath
        return planpath,plan

    def call_legacy(self,*args):

        if not args or args[0]!='run':raise AssertionError('call_legacy is only for explicit legacy run fixtures')
        return self.call(*args,'--legacy-multitier-replay')

    def assert_no_outputs(self):

        for s in (1,2,3):self.assertFalse((self.root/('out'+str(s))).exists())



    def test_pending_does_not_write_tiers(self):

        code,r=self.call_legacy('run','--plan',self.planpath)

        self.assertEqual(code,2,r);self.assertEqual(r['preflight']['pending'],1);self.assert_no_outputs()



    def test_independent_tiers_native_origin_alpha_and_resume(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap)

        self.assertEqual(code,0,r);self.assertEqual(r['exported_jobs'],1)

        for s in (1,2,3):

            p=Path(plan['jobs'][0]['views'][0]['outputs'][str(s)])

            with Image.open(p) as im:

                self.assertEqual(im.size,(66*s,67*s));self.assertEqual(im.getpixel((0,0))[3],0)

                self.assertEqual(im.getpixel((30*s,30*s))[3],200)

                self.assertEqual(im.getpixel((30*s,30*s))[:3],(140,70,210))

        code,r=self.call_legacy('run','--plan',ap)

        self.assertEqual(code,0,r);self.assertEqual(r['reused_jobs'],1);self.assertEqual(r['exported_jobs'],0)

        code,r=self.call('verify','--plan',ap);self.assertEqual(code,0,r)



    def test_assistant_cannot_approve(self):

        ap,plan=self.approve();plan['jobs'][0]['approval']['by']='assistant';save(ap,plan)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,2,r);self.assertEqual(r['preflight']['blocked'],1);self.assert_no_outputs()



    def test_unbound_geometry_cannot_run(self):

        ap,plan=self.approve();plan['jobs'][0]['views'][0]['source_rect']=[0,0,256,256];save(ap,plan)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,2,r);self.assertEqual(r['preflight']['blocked'],1);self.assert_no_outputs()



    def test_master_byte_drift_is_rejected(self):

        ap,plan=self.approve();Image.new('RGBA',(512,512),(10,20,30,255)).save(self.master)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,2,r);self.assertEqual(r['preflight']['blocked'],1);self.assert_no_outputs()



    def test_source_batch_drift_is_rejected(self):

        ap,plan=self.approve();self.batch['drift']=True;save(self.batchpath,self.batch)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,2,r);self.assert_no_outputs()



    def test_new_target_does_not_replace_existing_foreign_png(self):

        ap,plan=self.approve();p=Path(plan['jobs'][0]['views'][0]['outputs']['2']);p.parent.mkdir(parents=True);p.write_bytes(b'existing foreign file')

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,1,r);self.assertEqual(p.read_bytes(),b'existing foreign file')

        self.assertFalse(Path(plan['jobs'][0]['views'][0]['outputs']['1']).exists())

        self.assertFalse(Path(plan['jobs'][0]['views'][0]['outputs']['3']).exists())



    def test_modified_output_detected_by_verify(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        p=Path(plan['jobs'][0]['views'][0]['outputs']['1']);p.write_bytes(b'corrupt')

        code,r=self.call('verify','--plan',ap);self.assertEqual(code,1,r);self.assertEqual(r['failed'],1)



    def test_partial_exact_outputs_resume_without_clobber(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        receipt=Path(r['results'][0]['receipt']);receipt.unlink()

        one=Path(plan['jobs'][0]['views'][0]['outputs']['1']);before=one.read_bytes()

        Path(plan['jobs'][0]['views'][0]['outputs']['3']).unlink()

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,1,r)
        self.assertIn('LEGACY_MULTITIER_REPLAY_REFUSES_PARTIAL_OR_FOREIGN_OUTPUTS',r['error'])
        self.assertEqual(one.read_bytes(),before)
        self.assertFalse(Path(plan['jobs'][0]['views'][0]['outputs']['3']).exists())



    def test_effective_subject_rejects_transparent_padding(self):

        im=Image.new('RGBA',(512,512),(255,0,255,0));im.paste((255,255,255,255),(100,100,150,150));im.save(self.master)

        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes());ap,plan=self.approve(self.plan)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,1,r);self.assert_no_outputs()



    def test_bad_aspect_never_approved(self):

        self.plan['jobs'][0]['views'][0]['source_rect']=[0,0,512,256];save(self.planpath,self.plan)

        code,r=self.call('approve','--plan',self.planpath,'--id','TEST_ARTIFACT','--evidence','synthetic','--out',self.root/'no.json')

        self.assertEqual(code,1,r);self.assertFalse((self.root/'no.json').exists());self.assert_no_outputs()



    def test_target_geometry_cannot_drift_from_batch(self):

        self.plan['jobs'][0]['views'][0]['crop_1x']=[63,64];save(self.planpath,self.plan)

        code,r=self.call('approve','--plan',self.planpath,'--id','TEST_ARTIFACT','--evidence','synthetic','--out',self.root/'no.json')

        self.assertEqual(code,1,r);self.assert_no_outputs()



    def test_output_escape_rejected(self):

        self.plan['jobs'][0]['views'][0]['outputs']['1']=str(self.root/'escaped.png');save(self.planpath,self.plan)

        code,r=self.call('approve','--plan',self.planpath,'--id','TEST_ARTIFACT','--evidence','synthetic','--out',self.root/'no.json')

        self.assertEqual(code,1,r);self.assertFalse((self.root/'escaped.png').exists())



    def test_json_duplicate_keys_fail(self):

        p=self.root/'duplicate.json';p.write_text('{"schema":"a","schema":"b"}')

        code,r=self.call('check','--plan',p);self.assertEqual(code,1,r);self.assertIn('duplicate',r['error'])



    def test_output_receipt_cannot_grant_runtime_approval(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        p=Path(r['results'][0]['receipt']);receipt=json.loads(p.read_bytes());receipt['runtime_accepted']=True;save(p,receipt)

        code,r=self.call('verify','--plan',ap);self.assertEqual(code,1,r)



    def test_gpu_outputs_parity_with_cpu(self):

        # GPU parity belongs to the unchanged separable kernel, not CPU quality.
        self.plan['settings']['filter']='lanczos3';save(self.planpath,self.plan)

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap,'--backend','gpu');self.assertEqual(code,0,r)

        self.assertEqual(r['gpu_views'],1);gpu_pixels=[]

        for s in (1,2,3):

            with Image.open(plan['jobs'][0]['views'][0]['outputs'][str(s)]) as im:gpu_pixels.append(list(im.getdata()))

        # A separate output version avoids overwriting GPU results.

        second=copy.deepcopy(self.plan)

        for s in (1,2,3):second['jobs'][0]['views'][0]['outputs'][str(s)]=str(self.root/('out'+str(s))/'cpu'/('view'+str(s)+'.png'))

        cp,cpplan=self.approve(second,'cpu-approved.json');code,r=self.call_legacy('run','--plan',cp,'--backend','cpu');self.assertEqual(code,0,r)

        for s in (1,2,3):

            with Image.open(cpplan['jobs'][0]['views'][0]['outputs'][str(s)]) as im:

                for a,b in zip(gpu_pixels[s-1],im.getdata()):self.assertLessEqual(max(abs(x-y) for x,y in zip(a,b)),1)



    def test_portrait_separate_large_small_crops(self):

        g1={};g2={}

        for s in (1,2,3):

            for g,dims in [(g1,[58,64]),(g2,[48,32])]:g.update({'canvas_'+str(s)+'x':[x*s for x in dims],'crop_'+str(s)+'x':[x*s for x in dims],'origin_'+str(s)+'x':[0,0]})

        self.item.update({'category':'hero_portrait','key':['pcx','HPLTEST',None,None],'complete_target_views':[{'key':['pcx','HPLTEST',None,None],'targets':g1},{'key':['pcx','HPSTEST',None,None],'targets':g2}]});save(self.batchpath,self.batch)

        p=self.root/'portrait-pending.json';code,r=self.call('plan','--batch',self.batchpath,'--out',p);self.assertEqual(code,0,r);self.planpath=p;self.plan=json.loads(p.read_bytes())

        self.plan['settings'].update({'backend':'cpu','memory_gib':2})

        self.plan['jobs'][0]['views'][0].update({'source_rect':[24,0,488,512],'effective_rect':[0,0,464,512]})

        self.plan['jobs'][0]['views'][1].update({'source_rect':[16,64,496,384],'effective_rect':[0,0,480,320]})

        ap,plan=self.approve(self.plan);code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        for vi,dims in [(0,(58,64)),(1,(48,32))]:

            with Image.open(plan['jobs'][0]['views'][vi]['outputs']['3']) as im:self.assertEqual(im.size,(dims[0]*3,dims[1]*3))



    def test_four_skill_variants_share_one_master(self):

        specs=[('SECSK32',69,[32,32],[0,0,384,384]),('SECSKILL',69,[44,44],[16,16,412,412]),

               ('SECSK82',69,[82,93],[0,0,328,372]),('SSKILBON',66,[58,64],[0,0,348,384])]

        views=[]

        for ident,frame,dims,rect in specs:

            g={}

            for scale in (1,2,3):

                g.update({'canvas_'+str(scale)+'x':[x*scale for x in dims],

                          'crop_'+str(scale)+'x':[x*scale for x in dims],

                          'origin_'+str(scale)+'x':[0,0]})

            views.append({'key':['def',ident,0,frame],'targets':g})

        self.item.update({'id':'TEST_ARTIFACT','category':'skill','key':views[0]['key'],'complete_target_views':views})

        save(self.batchpath,self.batch)

        p=self.root/'skill-four-view-pending.json';code,r=self.call('plan','--batch',self.batchpath,'--out',p)

        self.assertEqual(code,0,r);self.planpath=p;self.plan=json.loads(p.read_bytes())

        self.plan['settings'].update({'backend':'cpu','memory_gib':2})

        for view,(_,_,_,rect) in zip(self.plan['jobs'][0]['views'],specs):

            view['source_rect']=rect;view['effective_rect']=[0,0,rect[2]-rect[0],rect[3]-rect[1]]

        ap,plan=self.approve(self.plan);code,r=self.call_legacy('run','--plan',ap)

        self.assertEqual(code,0,r);self.assertEqual(len(plan['jobs'][0]['views']),4)

        self.assertEqual(r['cpu_views'],4)

        for vi,(_,_,dims,_) in enumerate(specs):

            with Image.open(plan['jobs'][0]['views'][vi]['outputs']['3']) as im:

                self.assertEqual(im.size,(dims[0]*3,dims[1]*3))



    def test_skill_shared_master_four_native_views(self):

        dims=[('SECSK32',[32,32]),('SECSKILL',[44,44]),('SECSK82',[82,93]),('SSKILBON',[58,64])]
        views=[]
        for index,(identity,size) in enumerate(dims):
            g={}
            for scale in (1,2,3):
                g.update({'canvas_'+str(scale)+'x':[x*scale for x in size],
                          'crop_'+str(scale)+'x':[x*scale for x in size],
                          'origin_'+str(scale)+'x':[0,0]})
            views.append({'key':['def',identity,0,69 if identity!='SSKILBON' else 66],'targets':g})
        self.item.update({'id':'TEST_ARTIFACT','category':'skill','key':['def','SECSK82',0,69],
                          'complete_target_views':views})
        save(self.batchpath,self.batch)
        pending=self.root/'skill-four-pending.json'
        code,r=self.call('plan','--batch',self.batchpath,'--out',pending);self.assertEqual(code,0,r)
        self.planpath=pending;self.plan=json.loads(pending.read_bytes())
        self.plan['settings'].update({'backend':'cpu','memory_gib':2})
        rects=[([0,0,512,512],[0,0,512,512]),
               ([0,0,512,512],[0,0,512,512]),
               ([51,23,461,488],[0,0,410,465]),
               ([53,32,459,480],[0,0,406,448])]
        for view,(source,effective) in zip(self.plan['jobs'][0]['views'],rects):
            view['source_rect']=source;view['effective_rect']=effective
        ap,plan=self.approve(self.plan,'skill-four-approved.json')
        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)
        self.assertEqual(len(plan['jobs'][0]['views']),4)
        receipt=json.loads(Path(r['results'][0]['receipt']).read_bytes())
        self.assertEqual(len(receipt['outputs']),12)
        for view,(_,size) in zip(plan['jobs'][0]['views'],dims):
            for scale in (1,2,3):
                with Image.open(view['outputs'][str(scale)]) as im:
                    self.assertEqual(im.size,(size[0]*scale,size[1]*scale))

    def test_16bit_grayscale_input_does_not_fail(self):

        im=Image.new('I;16',(512,512),32000);im.save(self.master);self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())

        ap,plan=self.approve(self.plan);code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)



    def test_bad_png_gamma_is_not_silently_ignored(self):

        from PIL.PngImagePlugin import PngInfo

        import struct

        meta=PngInfo();meta.add(b'gAMA',struct.pack('>I',100000));Image.new('RGBA',(512,512),(120,120,120,255)).save(self.master,pnginfo=meta)

        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes());ap,plan=self.approve(self.plan)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,1,r);self.assert_no_outputs()



    def test_receipt_metadata_cannot_mislabel_native_key(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        p=Path(r['results'][0]['receipt']);receipt=json.loads(p.read_bytes());receipt['outputs'][0]['key']=['def','WRONG',0,99];save(p,receipt)

        code,r=self.call('verify','--plan',ap);self.assertEqual(code,1,r)



    def test_receipt_cannot_claim_game_deployment(self):

        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)

        p=Path(r['results'][0]['receipt']);receipt=json.loads(p.read_bytes());receipt['game_deployed']=True;save(p,receipt)

        code,r=self.call('verify','--plan',ap);self.assertEqual(code,1,r)



    def test_opaque_master_rejected_for_native_transparent_reference(self):

        self.item['reference_images']=[{'alpha_extrema':[0,255]}]

        save(self.batchpath,self.batch)

        self.plan['jobs'][0]['source_batch_sha256']=digest(self.batchpath.read_bytes())

        Image.new('RGBA',(512,512),(120,80,220,255)).save(self.master)

        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())

        ap,plan=self.approve(self.plan)

        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,1,r);self.assert_no_outputs()



    def test_explicit_opaque_output_overrides_transparent_detail_reference(self):
        self.item['reference_images']=[{'alpha_extrema':[0,255],'role':'identity_detail_reference'}]
        self.item['output_alpha_required']=False
        save(self.batchpath,self.batch)
        self.plan['jobs'][0]['source_batch_sha256']=digest(self.batchpath.read_bytes())
        Image.new('RGBA',(512,512),(120,80,220,255)).save(self.master)
        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())
        ap,plan=self.approve(self.plan)
        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)
        with Image.open(plan['jobs'][0]['views'][0]['outputs']['1']) as im:
            self.assertEqual(im.getchannel('A').getextrema(),(0,255))  # transparent target canvas padding remains allowed
            self.assertEqual(im.getpixel((30,30))[3],255)

    def test_output_alpha_contract_must_be_boolean(self):
        ap,plan=self.approve(self.plan)
        self.item['output_alpha_required']='false'
        save(self.batchpath,self.batch)
        self.plan['jobs'][0]['source_batch_sha256']=digest(self.batchpath.read_bytes())
        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())
        save(self.planpath,self.plan)
        code,r=self.call('approve','--plan',self.planpath,'--id','TEST_ARTIFACT','--evidence','synthetic test','--out',self.root/'invalid-approved.json')
        self.assertEqual(code,1,r);self.assertIn('must be boolean',r['error'])
        plan['jobs'][0]['source_batch_sha256']=digest(self.batchpath.read_bytes())
        save(ap,plan)
        code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,2,r);self.assertEqual(r['preflight']['blocked'],1)
        self.assert_no_outputs()

    def test_valid_srgb_icc_is_color_managed(self):
        from PIL import ImageCms
        profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        Image.new('RGBA',(512,512),(140,70,210,200)).save(self.master,icc_profile=profile)
        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())
        ap,plan=self.approve(self.plan);code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)
        with Image.open(plan['jobs'][0]['views'][0]['outputs']['3']) as im:
            value=im.getpixel((90,90))
            self.assertLessEqual(max(abs(x-y) for x,y in zip(value,(140,70,210,200))),1)
        receipt=json.loads(Path(r['results'][0]['receipt']).read_bytes())
        self.assertIn('color-managed',receipt['color_handling'])

    def test_corrupt_icc_cannot_be_ignored(self):
        Image.new('RGBA',(512,512),(140,70,210,200)).save(self.master,icc_profile=b'not a real ICC profile')
        self.plan['jobs'][0]['master_sha256']=digest(self.master.read_bytes())
        ap,plan=self.approve(self.plan);code,r=self.call_legacy('run','--plan',ap)
        self.assertEqual(code,1,r);self.assert_no_outputs()


    def test_default_plan_is_3x_only_without_lower_output_directories(self):
        planpath,plan=self.make_3x_plan_without_lower_dirs()
        self.assertTrue(all(set(view['outputs'])=={'3'} for job in plan['jobs'] for view in job['views']))
        code,r=self.call('run','--plan',planpath)
        self.assertEqual(code,2,r);self.assertEqual(r['preflight']['pending'],1)
        self.assertFalse((self.root/'out1').exists());self.assertFalse((self.root/'out2').exists());self.assertFalse((self.root/'out3').exists())

    def test_3x_only_approved_export_and_receipt(self):
        planpath,plan=self.make_3x_plan_without_lower_dirs()
        approved=self.root/'approved-3x-only.json'
        code,r=self.call('approve','--plan',planpath,'--id','TEST_ARTIFACT','--evidence','SYNTHETIC regression fixture only; not a real user art approval','--out',approved)
        self.assertEqual(code,0,r);approved_plan=json.loads(approved.read_bytes())
        code,r=self.call('run','--plan',approved)
        self.assertEqual(code,0,r);self.assertEqual(r['exported_jobs'],1);self.assertEqual(r['reused_jobs'],0)
        view=approved_plan['jobs'][0]['views'][0];self.assertEqual(set(view['outputs']),{'3'})
        png=Path(view['outputs']['3']);self.assertTrue(png.is_file());self.assertFalse((self.root/'out1').exists());self.assertFalse((self.root/'out2').exists())
        receipt=json.loads(Path(r['results'][0]['receipt']).read_bytes());self.assertEqual(len(receipt['outputs']),1);self.assertEqual(receipt['outputs'][0]['tier'],3)
        code,r=self.call('verify','--plan',approved);self.assertEqual(code,0,r)

    def test_legacy_default_run_refuses_missing_receipt_before_writes(self):
        ap,plan=self.approve()
        code,r=self.call('run','--plan',ap)
        self.assertEqual(code,2,r);self.assertEqual(r['status'],'NO_EXPORT_PREFLIGHT_BLOCKED_OR_PENDING')
        self.assertEqual(r['outputs_written'],0);self.assertEqual(r['preflight']['legacy_multitier_gate']['status'],'BLOCKED');self.assert_no_outputs()
        self.assertFalse((self.root/'gemini-logs').exists())

    def test_legacy_intact_receipt_is_reused_by_default_run(self):
        ap,plan=self.approve();code,r=self.call_legacy('run','--plan',ap);self.assertEqual(code,0,r)
        before={scale:Path(plan['jobs'][0]['views'][0]['outputs'][str(scale)]).read_bytes() for scale in (1,2,3)}
        code,r=self.call('run','--plan',ap);self.assertEqual(code,0,r);self.assertEqual(r['reused_jobs'],1);self.assertEqual(r['exported_jobs'],0)
        for scale in (1,2,3):self.assertEqual(Path(plan['jobs'][0]['views'][0]['outputs'][str(scale)]).read_bytes(),before[scale])

    def test_legacy_replay_flag_grammar_rejects_before_writes(self):
        ap,plan=self.approve()
        invalid=[('--plan',ap,'--legacy-multitier-replay','--legacy-multitier-replay'),('--plan',ap,'--legacy-multitier-replay=true'),('--plan','--legacy-multitier-replay',ap),('--plan',ap,'--legacy-multitier-replay','unexpected-value')]
        for args in invalid:
            code,r=self.call('run',*args);self.assertEqual(code,1,r);self.assert_no_outputs()
        code,r=self.call('verify','--plan',ap,'--legacy-multitier-replay');self.assertEqual(code,1,r);self.assert_no_outputs()

    def test_legacy_replay_flag_rejects_3x_only_plan(self):
        planpath,plan=self.make_3x_plan_without_lower_dirs()
        approved=self.root/'approved-3x-only.json'
        code,r=self.call('approve','--plan',planpath,'--id','TEST_ARTIFACT','--evidence','Synthetic only','--out',approved);self.assertEqual(code,0,r)
        code,r=self.call('run','--plan',approved,'--legacy-multitier-replay');self.assertEqual(code,1,r);self.assert_no_outputs()

    def test_mixed_plan_missing_legacy_receipt_blocks_all_writes(self):
        second_master=self.root/'second-master.png';Image.new('RGBA',(512,512),(20,100,180,220)).save(second_master)
        item1=copy.deepcopy(self.item);item1.update({'id':'LEGACY_FIXTURE','output_master':str(self.master)})
        item2=copy.deepcopy(self.item);item2.update({'id':'NEW_3X_FIXTURE','output_master':str(second_master)})
        batch=copy.deepcopy(self.batch);batch['items']=[item1,item2]
        batchpath=self.root/'mixed-batch.json';save(batchpath,batch)
        pending=self.root/'mixed-pending.json';code,r=self.call('plan','--batch',batchpath,'--out',pending);self.assertEqual(code,0,r)
        plan=json.loads(pending.read_bytes());legacy=next(j for j in plan['jobs'] if j['id']=='LEGACY_FIXTURE')
        for view in legacy['views']:
            key=view['key'];native='Defs/'+key[1]+'.DEF/'+str(key[2])+'_'+str(key[3])+'.png'
            view['outputs']={str(scale):str(Path(batch['pcx'+str(scale)+'x_output_dir'])/'tier-v1'/native) for scale in (1,2,3)}
        save(pending,plan)
        approved1=self.root/'mixed-approved1.json';code,r=self.call('approve','--plan',pending,'--id','LEGACY_FIXTURE','--evidence','Synthetic legacy fixture only','--out',approved1);self.assertEqual(code,0,r)
        approved2=self.root/'mixed-approved2.json';code,r=self.call('approve','--plan',approved1,'--id','NEW_3X_FIXTURE','--evidence','Synthetic 3X fixture only','--out',approved2);self.assertEqual(code,0,r)
        code,r=self.call('run','--plan',approved2);self.assertEqual(code,2,r);self.assertEqual(r['outputs_written'],0)
        for scale in (1,2,3):self.assertFalse((self.root/('out'+str(scale))).exists())
        self.assertFalse((self.root/'gemini-logs').exists())

if __name__=='__main__':unittest.main(verbosity=2)
