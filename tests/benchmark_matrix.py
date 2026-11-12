"""Measure actual CPU/GPU cooperation on synthetic pixel workloads; retain receipts.
Per-run PNG fixtures are deleted only after successful hash-bearing benchmark
reports have been parsed. Does not write actual master or derivative directories.
"""
from pathlib import Path
import datetime
import hashlib
import json
import statistics
import subprocess
import sys
PRODUCT=Path(__file__).resolve().parents[1]
EXE=PRODUCT/'build/candidate/H3TierExport.exe'


def main():
    base=PRODUCT/'build/benchmarks'
    base.mkdir(parents=True,exist_ok=True)
    runroot=base/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
    runroot.mkdir()
    exe_sha=hashlib.sha256(EXE.read_bytes()).hexdigest()
    rows=[]
    # Alternating order reduces systematic warm-up/order bias; OS cache is not cleared.
    order=[('cpu',8),('hybrid',8),('cpu',16),('hybrid',16),('cpu',32),('hybrid',32)]
    for rep in range(2):
        for mode,workers in (order if rep==0 else list(reversed(order))):
            if hashlib.sha256(EXE.read_bytes()).hexdigest()!=exe_sha:raise RuntimeError('EXE changed during matrix')
            folder=runroot/(f'{mode}-{workers}-r{rep+1}')
            cmd=[str(EXE),'bench','--outdir',str(folder),'--jobs','48','--backend',mode,'--workers',str(workers),'--memory-gib','64']
            p=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            log={'command':cmd,'exit_code':p.returncode,'stderr':p.stderr}
            if p.returncode:
                log['stdout']=p.stdout
                (runroot/'failure.json').write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
                raise RuntimeError('benchmark failed: '+str(folder)+' '+p.stderr[-1000:])
            report=json.loads((folder/'benchmark.json').read_bytes())
            if report['failed'] or report['jobs']!=48:raise RuntimeError('incomplete benchmark')
            row={k:report[k] for k in ['workers','wall_ms','masters_per_second','latency_p50_ms','latency_p95_ms','peak_working_set_bytes','peak_admitted_estimate_bytes','gpu_jobs','cpu_jobs','failed']}
            row.update({'backend':mode,'repeat':rep+1,'directory':str(folder),'report_sha256':hashlib.sha256((folder/'benchmark.json').read_bytes()).hexdigest(),'cleanup_bytes':0,'removed_files':0})
            expected={'large-fixture.png','icon-fixture.png'}|{f'job-{i}-{s}x.png' for i in range(48) for s in (1,2,3)}
            for name in expected:
                target=folder/name
                if target.is_symlink() or not target.is_file():raise RuntimeError('unexpected benchmark payload')
                row['cleanup_bytes']+=target.stat().st_size
                target.unlink();row['removed_files']+=1
            rows.append(row)
            print(json.dumps(row),flush=True)
    summary=[]
    for mode,workers in order:
        selected=[r for r in rows if r['backend']==mode and r['workers']==workers]
        summary.append({'backend':mode,'workers':workers,'runs':len(selected),'median_wall_ms':statistics.median(r['wall_ms'] for r in selected),'median_masters_per_second':statistics.median(r['masters_per_second'] for r in selected),'gpu_jobs_total':sum(r['gpu_jobs'] for r in selected),'max_working_set_bytes':max(r['peak_working_set_bytes'] for r in selected)})
    result={'schema':'h3.tier-export.benchmark-matrix.v1','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exe_sha256':exe_sha,'jobs_per_run':48,'repeats':2,'fixtures':'two-thirds 2048x2048 to 44/88/132; one-third 3840x2160 to 800x450/1600x900/2400x1350; synthetic RGBA only','quality':'linear-light premultiplied Lanczos3; adaptive PNG; flush/readback pixel verification','cache':'OS cache not cleared; fixture creation excluded; order reversed on repeat 2','summary':summary,'runs':rows,'generated_or_approved_artwork':0}
    path=runroot/'matrix.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'matrix':str(path),'summary':summary,'exe_sha256':exe_sha}),flush=True)
    return 0
if __name__=='__main__':sys.exit(main())
