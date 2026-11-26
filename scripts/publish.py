"""Publish the verified H3TierExport build into this product directory only.
Never touches game LOD/DEF/PCX files or generated artwork.
"""
from pathlib import Path
import argparse, datetime, hashlib, json, os, re, shutil, stat, subprocess, sys
from build import validate_build_sources
from companion_runtime import verify_payload, prune_stale_runtimes, BINDING_SCHEMA
from retire_files import retire

PRODUCT=Path(__file__).resolve().parents[1]
_REAL_PRODUCT=PRODUCT
BUILD=PRODUCT/"build"/"candidate"
TESTS=PRODUCT/"build"/"test-results"/"tests.json"
DIST=PRODUCT/"dist"
REPORTS=PRODUCT/"reports"/"current"
RELEASE_FILES=("H3TierExport.exe","H3TierExport-build-receipt.json","H3TierExport-tests-receipt.json",
               "H3TierExport-使用说明.md","H3TierExport-第三方声明.txt","H3TierExport.ico",
               "H3TierExport-icon.png","H3TierExport-workspace.json")
RELEASE_RECEIPT="H3TierExport-release.json"

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic_copy(src,dst):
    src,dst=Path(src),Path(dst);dst.parent.mkdir(parents=True,exist_ok=True)
    raw=src.read_bytes()
    if dst.exists() and dst.read_bytes()==raw:return {"path":str(dst),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"reused":True}
    tmp=dst.with_name(dst.name+".publishing")
    if tmp.exists():retire(tmp,PRODUCT,"stale publication temporary")
    with tmp.open("xb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    previous=retire(dst,PRODUCT,"replaced by qualified tool release") if dst.exists() else None
    try:os.replace(tmp,dst)
    except Exception:
        if previous is not None and not dst.exists():os.rename(previous,dst)
        raise
    if dst.read_bytes()!=raw:raise RuntimeError("publish readback mismatch: "+str(dst))
    return {"path":str(dst),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"reused":False}
def run(exe,*args):
    p=subprocess.run([str(exe),*[str(x) for x in args]],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180)
    if p.returncode:raise RuntimeError("published EXE failed: "+p.stdout[-2000:]+p.stderr[-2000:])
    return json.loads(p.stdout)

def _runtime_binding_at(root):
    root=Path(root)
    raw=(root/"runtime-manifest.json").read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if root.name!=".runtime-"+digest:raise RuntimeError("RUNTIME_MANIFEST_DIRECTORY_MISMATCH: "+str(root))
    manifest=json.loads(raw)
    rows=manifest.get("files")
    if (not isinstance(rows,list) or not rows
            or any(not isinstance(row,dict) or type(row.get("bytes")) is not int or row["bytes"]<0 for row in rows)):
        raise RuntimeError("RUNTIME_MANIFEST_FILES_INVALID: "+str(root))
    return {"schema":BINDING_SCHEMA,"directory":root.name,"manifest_sha256":digest,
            "file_count":len(rows),"bytes":sum(row["bytes"] for row in rows)}

def _copy_runtime_tree(source,target):
    """Copy a portable sidecar into independent regular files; hardlinks are forbidden."""
    source,target=Path(source),Path(target)
    manifest=json.loads((source/"runtime-manifest.json").read_bytes())
    names=[row["path"] for row in manifest["files"]]+["runtime-manifest.json"]
    target.mkdir()
    for name in names:
        src=source/Path(name);dst=target/Path(name)
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
        if dst.stat().st_nlink != 1:
            raise RuntimeError("portable runtime file is not independent: "+str(dst))

def publish_portable_runtime(binding,sources):
    """Materialize the exact frozen payload beside the EXE, reusing identical local files."""
    source=PRODUCT/binding["directory"];target=DIST/binding["directory"]
    verify_payload(binding,product=PRODUCT,sources=sources)
    if not target.exists():
        try:
            _copy_runtime_tree(source,target)
        except Exception:
            # Another publisher may have created the same content-addressed
            # directory after the existence check. Reuse it only after full
            # verification; otherwise retire the partial tree and propagate.
            if not target.exists():raise
            try:
                verify_payload(binding,product=DIST,sources=sources)
            except Exception:
                retire(target,PRODUCT,"incomplete portable companion runtime materialization")
                raise
    try:
        verify_payload(binding,product=DIST,sources=sources)
    except Exception:
        if target.exists():
            retire(target,PRODUCT,"portable companion runtime failed release verification")
        raise
    return {"directory":binding["directory"],"manifest_sha256":binding["manifest_sha256"],
            "file_count":binding["file_count"],"bytes":binding["bytes"],"verified":True}

def prune_stale_portable_runtime_copies(current_binding):
    """Retire all portable sidecars in dist except the current release's own binding."""
    verify_payload(current_binding, product=DIST)
    current_dir=current_binding["directory"]
    candidates=[p for p in sorted(DIST.iterdir(),key=lambda path:path.name)
                if p.is_dir() and p.name.startswith(".runtime-")]
    stale=[p for p in candidates if p.name!=current_dir and not p.is_symlink()]
    shared_batch=None
    if len(stale)>1 and PRODUCT.resolve()==_REAL_PRODUCT.resolve():
        shared_batch=Path("{VX-root}/H3美术素材/待删除")/("H3TierExport-portable-runtime-prune-"+datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    retired=[];kept=[]
    for candidate in candidates:
        if candidate.name==current_dir:
            kept.append({"directory":candidate.name,"reason":"referenced by current release"})
            continue
        if candidate.is_symlink():
            kept.append({"directory":candidate.name,"reason":"symlink kept"})
            continue
        try:
            target=retire(candidate,PRODUCT,"superseded portable companion runtime",batch=shared_batch)
            retired.append({"directory":candidate.name,"moved_to":str(target)})
        except Exception as error:
            kept.append({"directory":candidate.name,"reason":str(error)})
    return {"retired":retired,"kept":kept}

def _archive_need(value,message):
    if not value:raise RuntimeError("ARCHIVE_"+message)

def _archive_bytes(path,limit=64*1024*1024):
    info=path.lstat()
    _archive_need(stat.S_ISREG(info.st_mode) and not path.is_symlink()
                  and not getattr(info,"st_file_attributes",0)&0x400 and info.st_size<=limit,
                  "FILE_TYPE_OR_LIMIT: "+str(path))
    return path.read_bytes()

def _archive_directory(path):
    info=path.lstat()
    _archive_need(stat.S_ISDIR(info.st_mode) and not path.is_symlink()
                  and not getattr(info,"st_file_attributes",0)&0x400,"DIRECTORY_TYPE_INVALID")

def _archive_snapshot(expected_release_sha256=None):
    """Bind the already-published bytes, without consulting today's source checkout."""
    _archive_directory(DIST);_archive_directory(REPORTS)
    raw=_archive_bytes(DIST/RELEASE_RECEIPT,8*1024*1024)
    release_sha=hashlib.sha256(raw).hexdigest()
    _archive_need(expected_release_sha256 is None or release_sha==expected_release_sha256,"CURRENT_RELEASE_CHANGED")
    _archive_need(_archive_bytes(REPORTS/"release.json",8*1024*1024)==raw,"CURRENT_REPORT_MISMATCH")
    release=json.loads(raw)
    _archive_need(isinstance(release,dict) and release.get("schema")=="h3.tier-export.release.v2","RELEASE_INVALID")
    rows=release.get("files")
    _archive_need(isinstance(rows,list) and len(rows)==len(RELEASE_FILES)
                  and all(isinstance(row,dict) for row in rows),"RELEASE_FILES_INVALID")
    files={RELEASE_RECEIPT:raw}
    for name in RELEASE_FILES:
        matches=[row for row in rows if row.get("path") in (str(DIST/name),name)]
        _archive_need(len(matches)==1,"RELEASE_FILE_IDENTITY: "+name)
        row=matches[0];data=_archive_bytes(DIST/name)
        _archive_need(type(row.get("bytes")) is int and row["bytes"]==len(data)
                      and row.get("sha256")==hashlib.sha256(data).hexdigest(),"RELEASE_FILE_DRIFT: "+name)
        files[name]=data
    build_raw=files["H3TierExport-build-receipt.json"];tests_raw=files["H3TierExport-tests-receipt.json"]
    build=json.loads(build_raw);tests=json.loads(tests_raw)
    exe_sha=hashlib.sha256(files["H3TierExport.exe"]).hexdigest()
    build_sha=hashlib.sha256(build_raw).hexdigest();tests_sha=hashlib.sha256(tests_raw).hexdigest()
    _archive_need(release.get("exe_sha256")==exe_sha and release.get("build_receipt_sha256")==build_sha
                  and release.get("test_receipt_sha256")==tests_sha,"RELEASE_QUALIFICATION_MISMATCH")
    _archive_need(isinstance(build,dict) and build.get("schema")=="h3.tier-export.build.v2"
                  and build.get("passed") is True and build.get("source_current") is True
                  and isinstance(build.get("executable"),dict) and build["executable"].get("sha256")==exe_sha
                  and type(build["executable"].get("bytes")) is int
                  and build["executable"]["bytes"]==len(files["H3TierExport.exe"]),"BUILD_IDENTITY_INVALID")
    _archive_need(isinstance(tests,dict) and tests.get("schema")=="h3.tier-export.test-suite.v2"
                  and tests.get("passed") is True and tests.get("source_current") is True
                  and tests.get("exe_sha256")==exe_sha and tests.get("build_receipt_sha256")==build_sha,
                  "TEST_BUILD_IDENTITY_INVALID")
    results=tests.get("results")
    _archive_need(isinstance(results,list) and results and all(isinstance(row,dict)
                  and type(row.get("exit_code")) is int and row["exit_code"]==0 for row in results),"TEST_RESULTS_INVALID")
    binding=build.get("companion_runtime")
    _archive_need(binding==release.get("companion_runtime"),"RUNTIME_BINDING_MISMATCH")
    # The existing content-addressed product-level payload is the single runtime owner.
    # An archive keeps that exact binding; it must never invent a second runtime layout.
    if binding is not None:
        _archive_need(isinstance(build.get("sources"),list) and build["sources"],"RUNTIME_SOURCE_ROWS_MISSING")
        verify_payload(binding,product=PRODUCT,sources=build["sources"])
    return {"files":files,"release_sha256":release_sha,"exe_sha256":exe_sha,
            "build_receipt_sha256":build_sha,"test_receipt_sha256":tests_sha,
            "companion_runtime":binding}

def _verify_archive(directory,files):
    _archive_directory(directory)
    _archive_need({p.name for p in directory.iterdir()}==set(files),"FILE_SET_CHANGED")
    for name,raw in files.items():
        _archive_need(_archive_bytes(directory/name)==raw,"EXISTING_FILE_CONFLICT: "+name)

def archive_current_release(expected_release_sha256=None):
    snapshot=_archive_snapshot(expected_release_sha256)
    archive=DIST/"archive"/("release-"+snapshot["release_sha256"])
    reused=archive.exists()
    if archive.parent.exists():_archive_directory(archive.parent)
    if not reused:
        archive.parent.mkdir(parents=True,exist_ok=True)
        _archive_directory(archive.parent)
        stage=archive.with_name("."+archive.name+".staging")
        stage.mkdir(exist_ok=True)
        info=stage.lstat()
        _archive_need(stat.S_ISDIR(info.st_mode) and not stage.is_symlink()
                      and not getattr(info,"st_file_attributes",0)&0x400,"STAGE_TYPE_INVALID")
        _archive_need(not ({p.name for p in stage.iterdir()}-set(snapshot["files"])),"STAGE_EXTRA_FILE")
        for name,raw in snapshot["files"].items():
            path=stage/name
            if path.exists():_archive_need(_archive_bytes(path)==raw,"STAGE_FILE_CONFLICT: "+name)
            else:
                with path.open("xb") as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        _verify_archive(stage,snapshot["files"])
        _archive_need(_archive_snapshot(snapshot["release_sha256"])["files"]==snapshot["files"],"CURRENT_CHANGED_DURING_COPY")
        os.rename(stage,archive)
    _verify_archive(archive,snapshot["files"])
    _archive_need(_archive_snapshot(snapshot["release_sha256"])["files"]==snapshot["files"],"CURRENT_CHANGED_DURING_COPY")
    return {"path":str(archive),"reused":reused,**{k:v for k,v in snapshot.items() if k!="files"},
            "file_count":len(snapshot["files"])}

def main(argv=()):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-current",action="store_true",help="Archive the exact current release without publishing a candidate.")
    parser.add_argument("--expected-release-sha256",help="Required exact current release receipt SHA-256 for archive-only mode.")
    args=parser.parse_args(argv)
    if args.archive_current:
        if not args.expected_release_sha256 or not re.fullmatch("[0-9a-f]{64}",args.expected_release_sha256):
            parser.error("--archive-current requires --expected-release-sha256 with a lowercase SHA-256")
        result=archive_current_release(args.expected_release_sha256)
        print(json.dumps({"mode":"archive-current",**result,"real_art_exports":0,"game_writes":0},ensure_ascii=False))
        return 0
    if args.expected_release_sha256:parser.error("--expected-release-sha256 is only valid with --archive-current")
    build_raw=(BUILD/"build-receipt.json").read_bytes();tests_raw=TESTS.read_bytes()
    build_hash=hashlib.sha256(build_raw).hexdigest();tests_hash=hashlib.sha256(tests_raw).hexdigest()
    build=json.loads(build_raw);tests=json.loads(tests_raw)
    if not build.get("passed") or not tests.get("passed"):raise RuntimeError("build/tests not verified")
    results=tests.get("results")
    if (tests.get("schema")!="h3.tier-export.test-suite.v2" or not isinstance(results,list) or not results
            or any(not isinstance(row,dict) or type(row.get("exit_code")) is not int or row["exit_code"]!=0
                   for row in results)):
        raise RuntimeError("qualification test results missing or failed")
    validate_build_sources(build)
    verify_payload(build.get("companion_runtime"), product=PRODUCT, sources=build["sources"])
    if not tests.get("source_current") or tests.get("build_receipt_sha256")!=build_hash:raise RuntimeError("qualification source/build identity changed")
    exe=BUILD/"H3TierExport.exe";exe_sha=sha(exe)
    if exe_sha!=build["executable"]["sha256"] or exe_sha!=tests["exe_sha256"]:raise RuntimeError("tested executable identity mismatch")
    DIST.mkdir(parents=True,exist_ok=True);REPORTS.mkdir(parents=True,exist_ok=True)
    # Preserve a previous current EXE before replacement.
    current=DIST/"H3TierExport.exe"
    archived=None
    if current.exists() and sha(current)!=exe_sha:
        archived=archive_current_release()["path"]
    files=[
      atomic_copy(exe,current),
      # Freeze the tested source closure independently of the mutable next candidate.
      atomic_copy(BUILD/"build-receipt.json",DIST/"H3TierExport-build-receipt.json"),
      atomic_copy(TESTS,DIST/"H3TierExport-tests-receipt.json"),
      atomic_copy(PRODUCT/"docs"/"H3TierExport-用户手册.md",DIST/"H3TierExport-使用说明.md"),
      atomic_copy(PRODUCT/"third_party/LICENSE.MIT",DIST/"H3TierExport-第三方声明.txt"),
      atomic_copy(PRODUCT/"resources/H3TierExport.ico",DIST/"H3TierExport.ico"),
      atomic_copy(PRODUCT/"resources/H3TierExport-icon.png",DIST/"H3TierExport-icon.png"),
      atomic_copy(PRODUCT/"resources/H3TierExport-workspace.json",DIST/"H3TierExport-workspace.json"),
    ]
    portable_runtime=publish_portable_runtime(build["companion_runtime"],build["sources"])
    info=run(current,"info");ui=run(current,"ui-smoke");selftest=run(current,"selftest","--require-gpu")
    if not ui.get("passed") or not ui.get("icon_resource") or not selftest.get("passed"):raise RuntimeError("published UI/GPU smoke failed")
    validate_build_sources(build)
    verify_payload(build["companion_runtime"], product=PRODUCT, sources=build["sources"])
    if (sha(BUILD/"build-receipt.json")!=build_hash or sha(TESTS)!=tests_hash
            or sha(exe)!=exe_sha or sha(current)!=exe_sha
            or sha(DIST/"H3TierExport-build-receipt.json")!=build_hash
            or sha(DIST/"H3TierExport-tests-receipt.json")!=tests_hash):
        raise RuntimeError("qualification or frozen release source closure changed during publication")
    release_files=[{**row,"path":Path(row["path"]).name} for row in files]
    result={"schema":"h3.tier-export.release.v2","utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "version":info["version"],"exe_sha256":exe_sha,"files":release_files,"archived_previous":archived,
            "build_receipt_sha256":build_hash,"test_receipt_sha256":tests_hash,
            "companion_runtime":build["companion_runtime"],
            "portable_companion_runtime":portable_runtime,
            "ui_smoke":ui,"gpu":info["gpu"],"defaults":info["defaults"],"selftest_checks":selftest["checks"],
            "real_art_exports":0,"game_writes":0}
    raw=(json.dumps(result,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    report=REPORTS/"release.json"
    if report.exists():retire(report,PRODUCT,"superseded release report")
    report.write_bytes(raw)
    atomic_copy(report,DIST/"H3TierExport-release.json")
    runtime_prune=prune_stale_runtimes(product=PRODUCT,keep_directories={build["companion_runtime"]["directory"]},keep_limit=1)
    portable_runtime_prune=prune_stale_portable_runtime_copies(build["companion_runtime"])
    print(json.dumps({"dist":str(DIST),"version":result["version"],"exe_sha256":exe_sha,"files":files,
                      "ui_controls":ui["controls"],"icon_resource":ui["icon_resource"],"gpu":info["gpu"].get("description"),
                      "runtime_prune":runtime_prune,"portable_runtime_prune":portable_runtime_prune,"real_art_exports":0},ensure_ascii=False))
    return 0
if __name__=="__main__":sys.exit(main(sys.argv[1:]))
