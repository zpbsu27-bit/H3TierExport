"""Build H3TierExport x64 from the self-contained product directory.
Uses already-installed MSVC/Windows SDK. No package installation or game mutation.
"""
from pathlib import Path
import datetime, hashlib, json, os, subprocess, sys
from companion_runtime import freeze_payload, verify_payload

PRODUCT = Path(__file__).resolve().parents[1]
ROOT = PRODUCT
SRC = PRODUCT / "src"
RES = PRODUCT / "resources"
THIRD = PRODUCT / "third_party"
BUILD = PRODUCT / "build" / "candidate"
VSWHERE = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
PINNED_JSON = "9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6"

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def source_inventory():
    roots=[SRC,RES,PRODUCT/"scripts",PRODUCT/"tests",THIRD,PRODUCT/"providers"]
    files=sorted({p for base in roots for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
                  | {PRODUCT/"README.md",PRODUCT/"rebuild.cmd",PRODUCT/"docs"/"H3TierExport-用户手册.md",
                     PRODUCT/"docs"/"旗杆坐标与杆长批量核对.md"})
    return [{"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"sha256":sha(p)} for p in files]

def validate_build_sources(receipt):
    # The inventory is compared, not the spelling of the separator: a companion spawned with a
    # POSIX-path interpreter (msys python) reports "a/b" where the build recorded "a\b" for the
    # same file, and refusing that is a false drift. Both sides are normalised instead.
    def _norm(rows):
        return {str(r["path"]).replace("\\", "/"): r.get("sha256") for r in rows}
    if not receipt.get("passed") or not receipt.get("source_current") or receipt.get("source_path_base")!="product_root" or _norm(source_inventory())!=_norm(receipt.get("sources") or []):
        raise RuntimeError("BUILD_SOURCE_DRIFT: rebuild the current product sources before qualification/publication")

def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    sources_before=source_inventory()
    companion_runtime=freeze_payload(sources_before,product=PRODUCT)
    (BUILD/"companion_runtime_id.h").write_bytes(
        ('#pragma once\n#define H3_COMPANION_RUNTIME_SHA "'+companion_runtime["manifest_sha256"]+'"\n').encode("ascii"))
    vendor = THIRD / "json.hpp"
    if sha(vendor) != PINNED_JSON:
        raise RuntimeError("pinned JSON header identity changed")
    # -prerelease is required: the current MSVC instance is an Insiders channel install, which
    # vswhere hides by default, and without the flag resolution lands on a BuildTools instance
    # that has no VC/ directory at all (no cl.exe) and the build dies in the environment step.
    proc = subprocess.run([str(VSWHERE), "-prerelease", "-latest", "-products", "*", "-property", "installationPath"],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    candidates = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    allproc = subprocess.run([str(VSWHERE), "-prerelease", "-all", "-products", "*", "-property", "installationPath"],
                             capture_output=True, text=True, encoding="utf-8", check=True)
    for line in allproc.stdout.splitlines():
        if line.strip() and line.strip() not in candidates:
            candidates.append(line.strip())
    vcvars = next((Path(c) / "VC/Auxiliary/Build/vcvars64.bat" for c in candidates
                   if (Path(c) / "VC/Auxiliary/Build/vcvars64.bat").is_file()), None)
    if vcvars is None:
        raise RuntimeError("NO_MSVC_TOOLCHAIN: vswhere reported %s but none has VC/Auxiliary/Build/vcvars64.bat; install the 'Desktop development with C++' workload" % candidates)
    vs = vcvars.parents[3]
    sdk = Path("C:/Program Files (x86)/Windows Kits/10/bin")
    sdk_ver = next(p for p in sorted(sdk.iterdir(), reverse=True) if (p/"x64/fxc.exe").is_file() and (p/"x64/rc.exe").is_file())
    fxc, rc = sdk_ver/"x64/fxc.exe", sdk_ver/"x64/rc.exe"
    envcmd = BUILD / "vc-environment.cmd"
    envcmd.write_text('@echo off\ncall "'+str(vcvars)+'" >nul\nif errorlevel 1 exit /b 1\nset\n', encoding="ascii")
    envproc = subprocess.run(["cmd.exe","/d","/c",str(envcmd)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    env={k.upper():v for k,v in os.environ.items()}
    for line in envproc.stdout.splitlines():
        if "=" in line and not line.startswith("="):
            k,v=line.split("=",1);env[k.upper()]=v
    cl=next(Path(p)/"cl.exe" for p in env["PATH"].split(";") if (Path(p)/"cl.exe").is_file())
    env["VSLANG"]="1033"
    common=["/nologo","/std:c++20","/O2","/MT","/fp:precise","/EHsc","/utf-8","/permissive-","/W4","/DNDEBUG","/D_WIN32_WINNT=0x0A00","/DUNICODE","/D_UNICODE",
            "/I"+str(BUILD),"/I"+str(SRC),"/I"+str(RES),"/I"+str(PRODUCT)]
    commands=[
      [str(fxc),"/nologo","/T","cs_5_0","/E","main","/Gis","/O3","/Fh",str(BUILD/"axis_shader.h"),"/Vn","g_axis_shader",str(RES/"axis.hlsl")],
      [str(rc),"/nologo","/fo",str(BUILD/"app.res"),"/i",str(RES),str(RES/"app.rc")],
      [str(cl),*common,"/Fe:"+str(BUILD/"H3TierExport.exe"),str(SRC/"main.cpp"),str(SRC/"gui.cpp"),
       "/link",str(BUILD/"app.res"),"/INCREMENTAL:NO","/OPT:REF","/OPT:ICF","/DYNAMICBASE","/NXCOMPAT","/HIGHENTROPYVA","/MANIFEST:EMBED","/MANIFESTINPUT:"+str(RES/"app.manifest")],
      [str(cl),"/nologo","/std:c++20","/O2","/MT","/fp:precise","/EHsc","/utf-8","/W4","/DNDEBUG","/I"+str(SRC),"/I"+str(PRODUCT),
       "/Fe:"+str(BUILD/"core-tests.exe"),"/Fo:"+str(BUILD/"core-tests.obj"),str(PRODUCT/"tests/core_tests.cpp")],
      [str(cl),*common,"/Fe:"+str(BUILD/"quality-tests.exe"),"/Fo:"+str(BUILD/"quality-tests.obj"),str(PRODUCT/"tests/quality_resample.cpp")],
      [str(cl),"/nologo","/std:c++20","/O2","/MT","/fp:precise","/EHsc","/utf-8","/W4","/DNDEBUG","/I"+str(SRC),"/I"+str(PRODUCT),
       "/Fe:"+str(BUILD/"checkerboard-tests.exe"),"/Fo:"+str(BUILD/"checkerboard-tests.obj"),str(PRODUCT/"tests/checkerboard_alpha.cpp")],
    ]
    runs=[]
    for command in commands:
        p=subprocess.run(command,cwd=BUILD,env=env,capture_output=True,text=True,encoding="utf-8",errors="replace")
        runs.append({"command":command,"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr})
        if p.returncode: break
    sources_after=source_inventory()
    source_current=sources_before==sources_after
    before_by_path={row["path"]:row for row in sources_before}
    after_by_path={row["path"]:row for row in sources_after}
    source_drift=[{"path":name,"before":before_by_path.get(name),"after":after_by_path.get(name)}
                  for name in sorted(before_by_path.keys()|after_by_path.keys())
                  if before_by_path.get(name)!=after_by_path.get(name)]
    result={"schema":"h3.tier-export.build.v2","utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "product_root":str(PRODUCT),"source_path_base":"product_root","passed":source_current and len(runs)==len(commands) and all(r["exit_code"]==0 for r in runs),
            "source_current":source_current,"source_error":None if source_current else "BUILD_SOURCE_DRIFT_DURING_COMPILATION","source_drift":source_drift,
            "runs":runs,"sources":sources_after,"companion_runtime":companion_runtime,"executable":None}
    if result["passed"]:
        verify_payload(companion_runtime, product=PRODUCT, sources=sources_after)
        exe=BUILD/"H3TierExport.exe";result["executable"]={"path":str(exe),"bytes":exe.stat().st_size,"sha256":sha(exe)}
    (BUILD/"build-receipt.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":result["passed"],"executable":result["executable"],"last_exit":runs[-1]["exit_code"],
                      "last_stdout":runs[-1]["stdout"][-5000:],"last_stderr":runs[-1]["stderr"][-5000:]},ensure_ascii=True))
    return 0 if result["passed"] else 1

if __name__=="__main__":
    sys.exit(main())
