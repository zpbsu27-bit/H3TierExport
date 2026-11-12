"""Final native stress / Unicode / cross-backend pixel verification.
Only private synthetic benchmark inputs and test directories are written.
"""
from pathlib import Path
import datetime
import hashlib
import importlib.util
import json
import subprocess
import sys

PRODUCT=Path(__file__).resolve().parents[1]
EXE=PRODUCT/'build/candidate/H3TierExport.exe'


def main():
    base=PRODUCT/'build/final-probes'
    folder=base/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
    folder.mkdir(parents=True,exist_ok=False)
    exe_sha=hashlib.sha256(EXE.read_bytes()).hexdigest()
    results=[]; baselines={}
    for mode in ('cpu','hybrid','auto','gpu'):
        out=folder/mode
        p=subprocess.run([str(EXE),'bench','--outdir',str(out),'--jobs','48','--backend',mode,'--workers','32','--memory-gib','64','--filter','lanczos3'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        if not (out/'benchmark.json').is_file():
            raise RuntimeError('benchmark did not yield terminal report: '+p.stdout[-1000:]+p.stderr[-1000:])
        report=json.loads((out/'benchmark.json').read_bytes())
        if hashlib.sha256(EXE.read_bytes()).hexdigest()!=exe_sha:raise RuntimeError('EXE changed during verification')
        successes=[r for r in report['results'] if r['status']=='OK']
        for row in successes:
            if mode=='cpu':baselines[row['job']]=row['pixel_hashes']
            elif row['pixel_hashes']!=baselines[row['job']]:raise RuntimeError('CPU/GPU decoded pixel identity differs for '+mode+' job '+str(row['job']))
        errors=[{'job':r['job'],'error':r.get('error')} for r in report['results'] if r['status']!='OK']
        summary={k:report[k] for k in ('jobs','gpu_jobs','cpu_jobs','failed','wall_ms','masters_per_second','peak_working_set_bytes','peak_admitted_estimate_bytes')}
        summary.update({'backend':mode,'exit_code':p.returncode,'pixel_hashes_equal_to_cpu':True,'successful_pngs_pixel_verified':len(successes)*3,'errors':errors,'report_path':str(out/'benchmark.json'),'report_sha256':hashlib.sha256((out/'benchmark.json').read_bytes()).hexdigest()})
        # Remove only named benchmark images after reports and pixel identities were checked.
        allowed={'large-fixture.png','icon-fixture.png'}|{f'job-{i}-{s}x.png' for i in range(48) for s in (1,2,3)}
        removed=0;removed_bytes=0
        for child in out.iterdir():
            if child.name in allowed:
                if child.is_symlink() or not child.is_file():raise RuntimeError('unexpected benchmark payload type')
                removed_bytes+=child.stat().st_size;child.unlink();removed+=1
        summary.update({'removed_synthetic_pngs':removed,'removed_synthetic_bytes':removed_bytes})
        results.append(summary);print(json.dumps(summary),flush=True)
        if mode in ('cpu','hybrid','auto') and (p.returncode or report['failed']):raise RuntimeError('non-forced backend failed')
    # Exercise real UTF-16 paths, not ASCII aliases, with a fully synthetic approved job.
    mod_path=Path(__file__).parent/'integration.py'
    spec=importlib.util.spec_from_file_location('tier_synthetic_tests',mod_path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    test=mod.NativeExeTests(methodName='test_pending_does_not_write_tiers');test.setUp()
    try:
        new=test.root/'\u4e2d\u6587\u6bcd\u7248.png';test.master.rename(new);test.master=new;test.item['output_master']=str(new);mod.save(test.batchpath,test.batch)
        test.plan['jobs'][0]['master']=str(new)
        test.plan['jobs'][0]['source_batch_sha256']=mod.digest(test.batchpath.read_bytes())
        for s in (1,2,3):test.plan['jobs'][0]['views'][0]['outputs'][str(s)]=str(test.root/('out'+str(s))/'\u4e2d\u6587\u8def\u5f84'/'\u6d4b\u8bd5.png')
        ap,plan=test.approve(test.plan)
        code,r=test.call_legacy('run','--plan',ap,'--backend','auto');test.assertEqual(code,0,r)
        code,v=test.call('verify','--plan',ap);test.assertEqual(code,0,v)
        unicode_result={'passed':True,'synthetic_master_and_output_names':'actual Chinese Unicode paths','run':r,'verification':v,'cleaned_by_temporary_directory':True}
    finally:test.tearDown()
    result={'schema':'h3.tier-export.final-probes.v1','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exe_sha256':exe_sha,'results':results,'unicode':unicode_result,'default_nonforced_modes_passed':all(r['failed']==0 for r in results if r['backend']!='gpu'),'forced_gpu_batch_passed':next(r['failed']==0 for r in results if r['backend']=='gpu'),'all_successful_backends_pixel_equal':True,'real_user_approvals_created':0,'real_art_exports':0}
    target=folder/'final-probes.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(target),'exe_sha256':exe_sha,'forced_gpu_batch_passed':result['forced_gpu_batch_passed'],'unicode_passed':True}),flush=True)
    return 0

if __name__=='__main__':sys.exit(main())
