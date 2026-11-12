"""Focused static creature portrait contract. Animated inputs must stay rejected."""
import datetime, hashlib, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw

def main():
    exe=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();assert not out.exists();out.mkdir(parents=True)
    checks=[]
    def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    def write(p,x):p.write_text(json.dumps(x,indent=2),encoding='utf8')
    def call(*args):
        p=subprocess.run([str(exe),*map(str,args)],capture_output=True,text=True,encoding='utf8',errors='replace')
        return p,p.stdout+p.stderr
    def fixture(name,category,alpha=False,bad_boolean=False):
        root=out/name;root.mkdir();master=root/'master.png';size=(128,128) if alpha else (232,256)
        im=Image.new('RGBA',size,(0,0,0,0) if alpha else (30,60,90,255));d=ImageDraw.Draw(im)
        if alpha:d.ellipse((0,0,127,127),fill=(70,130,190,255))
        else:
            for x in range(size[0]):d.line((x,0,x,size[1]-1),fill=(x%256,140,190,255))
        im.save(master);canvas=[32,32] if alpha else [58,64];crop=[30,32] if alpha else canvas;origin=[1,0] if alpha else [0,0]
        batch=root/'batch.json';key=['def','CPRSMALL' if alpha else 'TWCRPORT',0,120]
        item=dict(id='SYNTHETIC_CREATURE_PORTRAIT',category=category,key=key,output_master=str(master),output_alpha_required='true' if bad_boolean else alpha,complete_target_geometry=dict(canvas_1x=canvas,crop_1x=crop,origin_1x=origin))
        b=dict(output_root=str(root),items=[item])
        b['pcx3x_output_dir']=str(root/'tier3')
        write(batch,b);view=dict(key=key,canvas_1x=canvas,crop_1x=crop,origin_1x=origin,source_rect=[4,0,124,128] if alpha else [0,0,232,256],effective_rect=[0,0,120,128] if alpha else [0,0,232,256],outputs={'3':str(root/'tier3/image.png')})
        job=dict(id=item['id'],category=category,source_batch=str(batch),source_batch_sha256=sha(batch),master=str(master),master_sha256=sha(master),master_pixels=list(size),approval=dict(approved=False,by='user',evidence=None,master_sha256=None,view_contract_sha256=None),views=[view])
        plan=root/'pending.json';write(plan,dict(schema='h3.tier-export.plan.v1',output_root=str(root),receipt_directory=str(root/'receipts'),settings=dict(backend='cpu',workers=1,memory_gib=1),jobs=[job],reuse=[]))
        return root,master,plan,canvas
    for name,alpha in [('opaque',False),('transparent',True)]:
        root,master,plan,canvas=fixture(name,'creature_portrait',alpha);before=sha(master)
        pending=root/'pending-run-report.json';p,text=call('run','--plan',plan,'--report',pending)
        report=json.loads(pending.read_text(encoding='utf8'));assert report['status']=='NO_EXPORT_PREFLIGHT_BLOCKED_OR_PENDING' and not list(root.glob('tier*/*.png')),text
        accepted=root/'approved.json';p,text=call('approve','--plan',plan,'--id','SYNTHETIC_CREATURE_PORTRAIT','--evidence','Synthetic contract fixture, not production art acceptance','--out',accepted);assert p.returncode==0,text
        report_path=root/'run-report.json';p,text=call('run','--plan',accepted,'--report',report_path);assert p.returncode==0,text
        report=json.loads(report_path.read_text(encoding='utf8'));assert report['status']=='COMPLETED' and report['failed']==0,report
        im=Image.open(root/'tier3/image.png');assert im.size==(canvas[0]*3,canvas[1]*3)
        if alpha:assert im.convert('RGBA').getchannel('A').getextrema()[0]==0
        assert not (root/'tier1').exists() and not (root/'tier2').exists()
        verify=root/'verify-report.json';p,text=call('verify','--plan',accepted,'--report',verify);assert p.returncode==0,text
        assert sha(master)==before
        checks.append(dict(case=name,status='PASS',unapproved_export_blocked=True,only_3x_and_geometry_verified=True,master_bytes_unchanged=True))
    for category in ['battle_creature_frame','creature_animation','unknown_category']:
        root,master,plan,_=fixture(category,category);accepted=root/'approved.json'
        p,text=call('approve','--plan',plan,'--id','SYNTHETIC_CREATURE_PORTRAIT','--evidence','Synthetic negative contract fixture','--out',accepted)
        assert p.returncode!=0 and 'unsupported/animated asset category' in text and not accepted.exists() and not list(root.glob('tier*/*.png')),text
        checks.append(dict(case=category,status='PASS',rejected_before_approval=True,outputs_written=0))
    root,master,plan,_=fixture('invalid_boolean','creature_portrait',True,True);accepted=root/'approved.json'
    p,text=call('approve','--plan',plan,'--id','SYNTHETIC_CREATURE_PORTRAIT','--evidence','Synthetic negative contract fixture','--out',accepted)
    assert p.returncode!=0 and 'output_alpha_required must be boolean' in text and not accepted.exists(),text
    checks.append(dict(case='invalid_boolean',status='PASS',rejected_before_approval=True,outputs_written=0))
    report=dict(schema='h3.tier-export-static-creature-contract-tests.r07.v1',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),passed=True,synthetic_only=True,exe=dict(path=str(exe),sha256=sha(exe)),checks=checks,animation_approval_supported=False,game_deployed=False,runtime_accepted=False)
    write(out/'report.json',report);print(json.dumps(dict(passed=True,cases=len(checks),report=str(out/'report.json'),exe_sha256=sha(exe))))

if __name__=='__main__':main()
