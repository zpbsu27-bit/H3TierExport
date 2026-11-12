"""Source-bound native cross-key alias into the existing motion reuse kernel.

This produces exactly one candidate picture + one byte-exact native-slot alias.
No source art, H3 producer output, human approval, timing or game files are changed.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from PIL import Image
from review_native_batch import (
    ReviewError, OutputRootGuard, absolute_file, read_bounded, parse_json,
    _lexists, _path_key, reject_reparse_components, _read_build_receipt, sha256_file, runtime_source_path
)
from build import validate_build_sources
from workspace import asset_root, h3_root
from prepare_official_motion_reuse import original_targets

PRODUCT = resolve_tool_root(__file__)
OUTPUT_BASE = PRODUCT / "build/experiments/native-alias"
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PNG_BYTES = 24 * 1024 * 1024
MAX_RENDER_BYTES = 16 * 1024 * 1024
MAX_INV_DECOMPRESSED = 128 * 1024 * 1024

def require(ok: bool, error: str) -> None:
    if not ok:
        raise ReviewError(error)

def digest(raw:bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def exact_fields(value:Any, fields:set[str], label:str) -> None:
    require(type(value) is dict and set(value)==fields,"FIELDS_INVALID:"+label)

def ref_path(value:Any, label:str, max_size:int=MAX_SOURCE_BYTES) -> tuple[Path,bytes]:
    exact_fields(value,{"path","sha256"},label)
    require(type(value["sha256"]) is str and SHA.fullmatch(value["sha256"]) is not None,"REFERENCE_SHA_REQUIRED:"+label)
    path = absolute_file(value["path"],label)
    data = read_bounded(path,max_size,label)
    require(digest(data)==value["sha256"],"REFERENCE_DRIFT:"+label)
    return path,data

def document(raw:bytes,label:str) -> dict:
    result = parse_json(raw,label)
    require(type(result) is dict,"OBJECT_REQUIRED:"+label)
    return result

def native_key(value:Any,label:str) -> list[Any]:
    require(type(value) is list and len(value)==4 and
            value[0]=="def" and type(value[1]) is str and bool(re.fullmatch(r"[A-Z0-9_]{1,20}",value[1])) and
            type(value[2]) is int and 0<=value[2]<=21 and
            type(value[3]) is int and 0<=value[3]<=4096,
            "NATIVE_KEY_INVALID:"+label)
    return value

def single_frame(value:dict, wanted:list[Any]) -> dict:
    require(type(value) is dict and native_key(value.get("key"),"cue.original")==wanted,
            "NATIVE_KEY_MISMATCH")
    for k in ("canvas","crop","origin"):
        x=value.get(k)
        require(type(x) is list and len(x)==2 and all(type(i) is int and 0<=i<=8192 for i in x),
                "NATIVE_GEOMETRY_INVALID:"+k)
    require(all(i>0 for i in value["canvas"]+value["crop"]),"NATIVE_GEOMETRY_EMPTY")
    require(all(value["origin"][i]+value["crop"][i]<=value["canvas"][i] for i in range(2)),
            "NATIVE_GEOMETRY_CROP_OUTSIDE")
    proof=value.get("original_native_proof")
    exact_fields(proof,{"native_payload_sha256","indexed_pixels_sha256","palette_sha256"},"native proof")
    for v in list(proof.values())+[value.get("RGBA_sha256"),value.get("alpha_sha256")]:
        require(type(v) is str and SHA.fullmatch(v) is not None,"NATIVE_DIGEST_INVALID")
    require(type(value.get("resource")) is str and value["resource"].lower().endswith(".pcx"),
            "NATIVE_RESOURCE_MISSING")
    return value

def bound_png(selected:Any,cue:dict) -> tuple[Path,bytes,str]:
    exact_fields(selected,{"path","sha256","pixels","rgba_sha256"},"selected3x")
    pins=cue["current_F0_pins"]["selected_exact3x_output"]
    need=cue["current_F0_pins"]
    require(selected["path"]==pins["path"] and selected["sha256"]==pins["sha256"],
            "SOURCE_NOT_SELECTED_F0_3X")
    require(selected["pixels"]==pins["pixels"],"SOURCE_PIXEL_GEOMETRY_DRIFT")
    require(type(selected["rgba_sha256"]) is str and SHA.fullmatch(selected["rgba_sha256"]) is not None,
            "SOURCE_RGBA_SHA_INVALID")
    path,raw=ref_path({"path":selected["path"],"sha256":selected["sha256"]},"selected F0 3X",MAX_PNG_BYTES)
    require(path.suffix.lower()==".png","SOURCE_PNG_REQUIRED")
    import io
    try:
        with Image.open(io.BytesIO(raw)) as im:
            require(im.format=="PNG" and im.mode=="RGBA","SOURCE_RGBA_PNG_REQUIRED")
            require(list(im.size)==selected["pixels"],"SOURCE_PNG_SIZE_DRIFT")
            rgba_sha=digest(im.tobytes())
    except (OSError,ValueError) as exc:
        raise ReviewError("SOURCE_PNG_DECODE_FAIL:"+str(exc)) from exc
    require(rgba_sha==selected["rgba_sha256"],"SOURCE_RGBA_SHA_DRIFT")
    return path,raw,rgba_sha

def current_receipt_build(renderer:Path,expected_sha:str) -> str:
    require(digest(read_bounded(renderer,MAX_RENDER_BYTES,"renderer"))==expected_sha,
            "RENDERER_SHA_DRIFT")
    binding=_read_build_receipt(expected_sha,renderer)
    raw=read_bounded(Path(binding["path"]),32*1024*1024,"build receipt")
    receipt=document(raw,"build")
    if binding.get("authority")=="formal_release":
        rows=receipt.get("sources",[])
        expected={row["path"].replace(chr(92),"/"):row.get("sha256") for row in rows}
        name="scripts/native_alias.py"
        require(expected.get(name)==sha256_file(runtime_source_path(name),16*1024*1024),
                "RELEASE_COMPANION_SOURCE_DRIFT: "+name)
    else:
        validate_build_sources(receipt)
    require(receipt.get("passed") is True and receipt.get("source_current") is True and
            receipt.get("executable",{}).get("sha256")==expected_sha,
            "CURRENT_BUILD_RENDERER_MISMATCH")
    return binding["sha256"]

def source_owner(cue:dict) -> tuple[list[dict],dict]:
    needed=("canonical_inventory","original_lod","native_reader_source")
    refs=cue["authority_inputs"]
    require(type(refs) is dict and all(name in refs for name in needed),"NATIVE_AUTHORITY_REFERENCES_MISSING")
    h3=h3_root()
    assets=asset_root(h3)
    reader_candidate=source_path("scripts/native_h3_io.py", PRODUCT)
    legacy_reader=h3/"tools/art_town_batch_r16.py"
    required={
        "canonical_inventory": assets/"CompleteOfficialRelease-20261006-v1/codex-map/native-resource-inventory.json.gz",
        "original_lod": assets/"CompleteNativeBaseline/20261006-v1/H3sprite.lod",
        "native_reader_source": legacy_reader if legacy_reader.is_file() else reader_candidate,
    }
    authorities={}
    for name,path in required.items():
        uri,raw=ref_path({"path":refs[name]["path"],"sha256":refs[name]["sha256"]},name,128*1024*1024)
        require(uri==path.resolve(),"NATIVE_AUTHORITY_PATH_DRIFT:"+name)
        require(type(refs[name].get("bytes")) is int and len(raw)==refs[name]["bytes"],
                "NATIVE_AUTHORITY_SIZE_DRIFT:"+name)
        authorities[name]=(uri,raw)
    # Preserve all selected F0 parent source and receipt bindings; never fabricate
    # a distinct F10 authoring event by copying F0's source into a new master.
    pins=cue["current_F0_pins"]
    parent_documents={}
    for name in ("selection","request","delivery","raw_intake","preparation","export_contract",
                 "current_highest_source","selected_exact3x_output"):
        value=pins.get(name)
        require(type(value) is dict,"F0_PARENT_REFERENCE_MISSING:"+name)
        path,raw=ref_path({"path":value.get("path"),"sha256":value.get("sha256")},
                          "F0 parent "+name,MAX_SOURCE_BYTES)
        require(type(value.get("bytes")) is int and len(raw)==value["bytes"],
                "REFERENCE_BYTES_DRIFT:"+name)
        if name in ("selection","request","delivery","raw_intake"):
            parent_documents[name]=document(raw,name)
    selected=pins["selected_exact3x_output"]
    selection=parent_documents["selection"]
    delivery=parent_documents["delivery"]
    request=parent_documents["request"]
    require(selection.get("key")==cue["source_key"] and request.get("key")==cue["source_key"],
            "F0_SELECTION_KEY_DRIFT")
    require(selection.get("status")=="ASSISTANT_SELECTED_FOR_DELIVERY",
            "F0_SOURCE_NOT_SELECTED_FOR_DELIVERY")
    for label,record in (("selection",selection),("delivery",delivery)):
        stated=record.get("output",{})
        require(type(stated) is dict and
                stated.get("path")==selected["path"] and
                stated.get("sha256")==selected["sha256"] and
                stated.get("pixels")==selected["pixels"],
                "F0_SELECTION_OUTPUT_MISMATCH:"+label)
        require(record.get("request",{}).get("sha256")==pins["request"]["sha256"] and
                record.get("request",{}).get("path")==pins["request"]["path"],
                "F0_SELECTION_PARENT_REQUEST_MISMATCH:"+label)
    require(selection.get("delivery",{}).get("sha256")==pins["delivery"]["sha256"],
            "F0_SELECTION_DELIVERY_SHA_DRIFT")
    require(delivery.get("game_deployed") is False and
            delivery.get("human_approved") is False,
            "F0_SELECTION_FORGED_GAME_OR_HUMAN_APPROVAL")
    require(parent_documents["raw_intake"].get("request",{}).get("sha256")==pins["request"]["sha256"],
            "F0_RAW_INTAKE_PARENT_DRIFT")
    require(request.get("native_geometry")==pins["native_geometry"],
            "F0_NATIVE_REQUEST_GEOMETRY_DRIFT")
    # Re-check inventory facts against a separate frozen catalogue.
    try:
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(authorities["canonical_inventory"][1]),mode="rb") as f:
            raw=f.read(MAX_INV_DECOMPRESSED+1)
        require(len(raw)<=MAX_INV_DECOMPRESSED,"NATIVE_INVENTORY_DECOMPRESS_LIMIT")
        catalog=document(raw,"native inventory")
    except (OSError,ValueError) as exc:
        raise ReviewError("NATIVE_INVENTORY_READ_FAIL:"+str(exc)) from exc
    rows=[]
    for entry in cue["original_native_frames"]:
        candidates=[row for row in catalog.get("rows",[]) if row.get("kind")=="def"
                    and row.get("identity")==entry["key"][1]
                    and row.get("group")==entry["key"][2] and row.get("frame")==entry["key"][3]]
        require(len(candidates)==1,"NATIVE_ORIGINAL_ROW_NOT_UNIQUE")
        row=candidates[0]
        require(row.get("resource")==entry["resource"] and row.get("canvas")==entry["canvas"] and
                row.get("crop")==entry["crop"] and row.get("anchor")==entry["origin"] and
                row.get("native_lod")=="H3sprite.lod",
                "NATIVE_INVENTORY_GEOMETRY_DRIFT")
        rows.append(row)
    return rows,{"reader_path":authorities["native_reader_source"][0],
                 "canonical_inventory_sha256":digest(authorities["canonical_inventory"][1]),
                 "original_lod_sha256":digest(authorities["original_lod"][1]),
                 "native_reader_sha256":digest(authorities["native_reader_source"][1])}

def independent_native(rows:list[dict],cue:dict,proof:dict)->None:
    # The H3 native parser is already the sole source reader. Import the exact
    # pinned source read-only; do not clone the DEF/index/palette decoder.
    reader=proof["reader_path"]
    sys.path.insert(0,str(reader.parent))
    spec=importlib.util.spec_from_file_location("h3tier_native_alias_readonly_source",reader)
    require(spec is not None and spec.loader is not None,"NATIVE_READER_LOAD_ERROR")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hashes=[]
    for row,expected in zip(rows,cue["original_native_frames"]):
        img,raw_proof=module.native_image(row)
        rgba_sha=digest(img.tobytes())
        alpha_sha=digest(img.getchannel("A").tobytes())
        require(rgba_sha==expected["RGBA_sha256"] and alpha_sha==expected["alpha_sha256"],
                "NATIVE_RGBA_OR_ALPHA_DRIFT")
        require(raw_proof==expected["original_native_proof"],"NATIVE_INDEX_OR_PALETTE_DRIFT")
        hashes.append((rgba_sha,alpha_sha,raw_proof))
    require(hashes[0]==hashes[1],"NATIVE_NOT_EXACTLY_EQUIVALENT")

def evaluate(manifest_path:Path,manifest_sha:str,renderer:Path,renderer_sha:str)->dict:
    mp,raw=ref_path({"path":str(manifest_path),"sha256":manifest_sha},"manifest",8*1024*1024)
    m=document(raw,"manifest")
    exact_fields(m,{"schema","operation_id","native_cue","selected_3x","owner_key","target_key","intent"},"manifest")
    require(m["schema"]=="h3.native-alias.intake.v1","NATIVE_ALIAS_SCHEMA")
    op=m["operation_id"]
    require(type(op) is str and TOKEN.fullmatch(op) is not None,"OPERATION_ID_INVALID")
    require(m["intent"]=="CANDIDATE_ONLY_NO_ART_APPROVAL","ART_APPROVAL_NOT_ALLOWED")
    owner=native_key(m["owner_key"],"owner")
    target=native_key(m["target_key"],"target")
    require(owner != target and owner[1:3]==target[1:3],"NATIVE_KEY_NOT_COMPATIBLE")
    cuepath,cueraw=ref_path(m["native_cue"],"native cue",3*1024*1024)
    cue=document(cueraw,"native cue")
    require(cue.get("schema")=="h3.art-town.native-exact-alias-cue.v1" and
            cue.get("status")=="CURRENT_CUE_EVIDENCE_ONLY","NATIVE_CUE_SCHEMA_OR_STATUS")
    require(cue.get("source_key")==owner and cue.get("candidate_key")==target,
            "NATIVE_KEY_CUE_MISMATCH")
    require(cue.get("native_RGBA_byte_equal") is True and cue.get("native_full_geometry_equal") is True,
            "NATIVE_EQUIVALENCE_UNVERIFIED")
    accepted=cue.get("acceptance",{})
    for k in ("cue_or_exact_equivalence_is_art_approval","new_key_human_approved",
              "animation_timing_known","scene_accepted","runtime_accepted","game_deployed","ALL15_complete"):
        require(type(accepted) is dict and accepted.get(k) is False,"ART_OR_RUNTIME_APPROVAL_CANNOT_BE_INFERRED:"+k)
    frames=cue.get("original_native_frames")
    require(type(frames) is list and len(frames)==2,"NATIVE_FRAME_PAIR_REQUIRED")
    first=single_frame(frames[0],owner)
    second=single_frame(frames[1],target)
    for name in ("canvas","crop","origin","original_native_proof","RGBA_sha256","alpha_sha256"):
        require(first[name]==second[name],"NATIVE_EQUIVALENCE_DIFFERS:"+name)
    selected,selected_bytes,selected_rgba=bound_png(m["selected_3x"],cue)
    require(m["selected_3x"]["pixels"]==[first["crop"][0]*3,first["crop"][1]*3],
            "SOURCE_NOT_EXACT_NATIVE_3X_CROP")
    rows,authority=source_owner(cue)
    independent_native(rows,cue,authority)
    renderer=absolute_file(str(renderer),"renderer")
    build_sha=current_receipt_build(renderer,renderer_sha)
    targets=original_targets({"canvas":first["canvas"],"crop":first["crop"],"anchor":first["origin"]})
    equivalence=digest(json.dumps({
        "schema":"h3.native-alias.equivalence.v1",
        "identity":owner[1],
        "native_indexed_pixels_sha256":first["original_native_proof"]["indexed_pixels_sha256"],
        "palette_sha256":first["original_native_proof"]["palette_sha256"],
        "rgba_sha256":first["RGBA_sha256"],"alpha_sha256":first["alpha_sha256"],
        "targets":targets,"selected_source_3x_sha256":digest(selected_bytes),
    },ensure_ascii=True,sort_keys=True,separators=(",",":")).encode("utf-8"))
    return {
      "schema":"h3.native-alias.triage.v1","status":"CANDIDATE_REVIEW_REQUIRED",
      "operation_id":op,
      "manifest":{"path":str(mp),"sha256":manifest_sha},
      "cue":{"path":str(cuepath),"sha256":digest(cueraw)},
      "native_frame_count":2,"native_equivalent":True,
      "source_key":owner,"target_key":target,"selected_source_3x_sha256":digest(selected_bytes),
      "selected_source_3x_rgba_sha256":selected_rgba,
      "selected_source_3x":{"path":str(selected),"sha256":digest(selected_bytes),
                             "pixels":m["selected_3x"]["pixels"],"key":owner,"native_scale":3},
      "canonical_inventory_sha256":authority["canonical_inventory_sha256"],
      "native_lod_sha256":authority["original_lod_sha256"],
      "native_reader_source_sha256":authority["native_reader_sha256"],
      "renderer_sha256":renderer_sha,"build_receipt_sha256":build_sha,
      "targets":targets,"equivalence_class":equivalence,
      "frames":[{"key":name,"targets":targets,"source_equivalence_class":equivalence}
                for name in (owner,target)],
      "owner_id":owner[1]+"_G"+str(owner[2])+"_F"+str(owner[3])+"_NATIVE_ALIAS_OWNER",
      "alias_id":target[1]+"_G"+str(target[2])+"_F"+str(target[3])+"_NATIVE_ALIAS",
      "native_timing_verified":False,"artist_approved_source_alias":False,
      "art_approved":False,"game_deployed":False,"new_model_calls":0,
      "producer_pointer_written":False,
      "note":"Native original equivalence is not independent human F10 art approval or game reception."
    }

def derived(triage:dict,out:Path)->tuple[dict,dict]:
    entries=[{"key":frame["key"],"targets":frame["targets"],
              "equivalence_class":frame["source_equivalence_class"]}
             for frame in triage["frames"]]
    native={"schema":"h3.motion.native-slot-projection.v1",
            "projection_source":{"path":str(out/"native-alias-triage.json"),
                                 "sha256":digest(write_bytes(triage))},
            "entries":entries}
    spec={"schema":"h3.motion.spec.v1",
          "output_root":str(out/"motion-candidates"),
          "native_contracts":[{"path":str(out/"motion-native-contracts.json"),
                               "sha256":digest(write_bytes(native))}],
          "settings":{"backend":"cpu","space":"linear","filter":"h3-quality",
                      "workers":1,"memory_gib":1},
          "items":[
            {"id":triage["owner_id"],"key":triage["source_key"],"targets":triage["targets"],
             "mode":"reuse","source":triage["selected_source_3x"]},
            {"id":triage["alias_id"],"key":triage["target_key"],"targets":triage["targets"],
             "mode":"reuse","reuse_from":triage["owner_id"]},
          ],
          "triage":{"path":str(out/"native-alias-triage.json"),
                    "sha256":digest(write_bytes(triage))},
          "note":"Strict candidate only; F10 has no separate art approval or native timing."
    }
    return native,spec

def write_bytes(value:dict)->bytes:
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode("utf-8")

def new_file(path:Path,content:bytes,guard:OutputRootGuard)->str:
    target=guard.verify_target(path)
    with target.open("xb") as f:
        f.write(content);f.flush();os.fsync(f.fileno())
    guard.verify_target(path,must_exist=True)
    require(digest(read_bounded(path,32*1024*1024,"output readback"))==digest(content),
            "OUTPUT_READBACK_MISMATCH")
    return digest(content)

def motion(exe:Path,command:str,batch:Path|None=None,spec:Path|None=None,out:Path|None=None)->dict:
    args=[str(exe),command]
    if spec is not None:args+=["--spec",str(spec),"--out",str(out)]
    if batch is not None:args+=["--batch",str(batch)]
    p=subprocess.run(args,cwd=PRODUCT,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=180)
    require(p.returncode==0,"MOTION_NATIVE_"+command.upper().replace("-","_")+"_FAILED:"+str(p.returncode)+":"+(p.stdout+p.stderr)[-800:])
    return document(p.stdout.encode("utf-8"),command+" result")

def output_path(path:Path,operation:str)->Path:
    path=Path(os.path.abspath(path))
    base=Path(os.path.abspath(OUTPUT_BASE))
    require(_path_key(path.parent)==_path_key(base) and path.name==operation,
            "OUTPUT_MUST_BE_OPERATION_CHILD")
    return path

def check_candidate_files(report:dict,out:Path,guard:OutputRootGuard,exe:Path,verify_motion:bool=True)->dict:
    guard.verify_current()
    triage_path=guard.verify_target(out/"native-alias-triage.json",must_exist=True)
    frozen=document(read_bounded(triage_path,4*1024*1024,"triage"),"triage")
    require(frozen==report,"TRIAGE_DRIFT")
    native,spec=derived(report,out)
    for name,expected in (("motion-native-contracts.json",native),("motion-spec.json",spec)):
        p=guard.verify_target(out/name,must_exist=True)
        require(read_bounded(p,4*1024*1024,name)==write_bytes(expected),"FROZEN_MOTION_SPEC_DRIFT:"+name)
    for name in ("motion-batch.json","motion-check.json","motion-probe.json",
                 "motion-run-report.json","motion-verify-report.json"):
        guard.verify_target(out/name,must_exist=True)
    if verify_motion:
        checked=motion(exe,"motion-verify",batch=out/"motion-batch.json")
        # This performs the original native motion receipt + PNG pixel-bound verifier.
        saved=document(read_bounded(out/"motion-verify-report.json",8*1024*1024,"verify report"),"verify report")
        # Run reports contain UTC/wall-clock and process memory telemetry.
        # Repeated verified executions need not have identical timings.
        for field in ("schema","algorithm","status","unique_pictures","duplicate_slots",
                      "failed","model_native_output","game_deployed","art_approved",
                      "human_approved","native_timing_verified",
                      "model_inference_performed","runtime_accepted"):
            require(checked.get(field)==saved.get(field),
                    "MOTION_VERIFY_STABLE_FIELD_DRIFT:"+field)
        require(len(checked.get("results",[]))==len(saved.get("results",[]))==2,
                "MOTION_VERIFY_RESULT_COUNT_DRIFT")
        for now,prior in zip(checked["results"],saved["results"]):
            for field in ("id","output","receipt","status"):
                require(now.get(field)==prior.get(field),
                        "MOTION_VERIFY_IDENTITY_DRIFT:"+field)
            require(now["status"]=="VERIFIED","MOTION_VERIFY_SLOT_NOT_VERIFIED")
    run=document(read_bounded(out/"motion-run-report.json",8*1024*1024,"run report"),"run report")
    require(run["failed"]==0 and run["duplicate_slots"]==1 and
            len(run["results"])==2,"MOTION_EXACT_ALIAS_RUN_NOT_QUALIFIED")
    reports={x["id"]:x for x in run["results"]}
    require(set(reports)=={report["owner_id"],report["alias_id"]},"MOTION_RESULT_SLOT_MISMATCH")
    picture_paths=[]
    for id in (report["owner_id"],report["alias_id"]):
        path=Path(reports[id]["output"])
        guard.verify_target(path,must_exist=True)
        picture_paths.append(path)
    owner_bytes=read_bounded(picture_paths[0],MAX_PNG_BYTES,"owner candidate")
    alias_bytes=read_bounded(picture_paths[1],MAX_PNG_BYTES,"alias candidate")
    original=read_bounded(Path(report["selected_source_3x"]["path"]),MAX_PNG_BYTES,"selected original")
    require(owner_bytes==alias_bytes==original,"MOTION_ALIAS_NOT_BYTE_EXACT")
    require(digest(owner_bytes)==report["selected_source_3x_sha256"],"MOTION_OWNER_PNG_SHA_DRIFT")
    guard.verify_current()
    return {"schema":"h3.native-alias.result.v1","status":"VERIFIED_REVIEW_REQUIRED",
            "operation_id":report["operation_id"],"source_key":report["source_key"],
            "target_key":report["target_key"],"owner_png_sha256":digest(owner_bytes),
            "alias_png_sha256":digest(alias_bytes),"native_equivalent":True,
            "artist_approved_source_alias":False,"art_approved":False,"native_timing_verified":False,
            "game_deployed":False,"new_model_calls":0,"producer_pointer_written":False}

def verify(report:dict,out:Path,exe:Path)->dict:
    require(out.is_dir(),"OUTPUT_ROOT_MISSING")
    guard=OutputRootGuard.open_existing(out,OUTPUT_BASE)
    try:
        result=check_candidate_files(report,out,guard,exe)
        complete=guard.verify_target(out/"analysis-complete.json",must_exist=True)
        saved=document(read_bounded(complete,1024*1024,"completion"),"completion")
        target={"schema":"h3.native-alias.completion.v1",
                "operation_id":report["operation_id"],
                "triage_sha256":digest(write_bytes(report)),
                "motion_batch_sha256":digest(read_bounded(out/"motion-batch.json",8*1024*1024,"motion batch")),
                "motion_owner_and_alias_png_sha256":result["owner_png_sha256"],
                "renderer_sha256":report["renderer_sha256"],"review_required":True,
                "native_timing_verified":False,"art_approved":False,"game_deployed":False}
        require(saved==target,"COMPLETION_DRIFT")
        guard.verify_current()
        return result
    finally:
        guard.close()

def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=("check","run","verify"))
    for field in ("manifest","manifest-sha256","outdir","operation-id","renderer","renderer-sha256"):
        parser.add_argument("--"+field,required=True)
    args=parser.parse_args(argv)
    try:
        require(type(args.operation_id) is str and TOKEN.fullmatch(args.operation_id) is not None,
                "OPERATION_ID_INVALID")
        require(SHA.fullmatch(args.manifest_sha256) is not None and
                SHA.fullmatch(args.renderer_sha256) is not None,"SOURCE_SHA_REQUIRED")
        mp=absolute_file(args.manifest,"manifest")
        exe=absolute_file(args.renderer,"renderer")
        out=output_path(Path(args.outdir),args.operation_id)
        report=evaluate(mp,args.manifest_sha256,exe,args.renderer_sha256)
        require(args.operation_id==report["operation_id"],"OPERATION_ID_MISMATCH")
        if args.stage=="check":
            print(json.dumps(report,ensure_ascii=False))
            return 0
        if args.stage=="verify":
            print(json.dumps(verify(report,out,exe),ensure_ascii=False))
            return 0
        if _lexists(out):
            old=verify(report,out,exe)
            old["status"]="REUSED_VERIFIED"
            print(json.dumps(old,ensure_ascii=False))
            return 0
        guard=OutputRootGuard.create(out,OUTPUT_BASE)
        try:
            fresh=evaluate(mp,args.manifest_sha256,exe,args.renderer_sha256)
            require(fresh==report,"INPUT_CHANGED_BEFORE_ALIAS_RUN")
            triage_path=out/"native-alias-triage.json"
            new_file(triage_path,write_bytes(report),guard)
            native,spec=derived(report,out)
            new_file(out/"motion-native-contracts.json",write_bytes(native),guard)
            new_file(out/"motion-spec.json",write_bytes(spec),guard)
            motion(exe,"motion-plan",spec=out/"motion-spec.json",out=out/"motion-batch.json")
            guard.verify_target(out/"motion-batch.json",must_exist=True)
            for command,name in (("motion-check","motion-check.json"),
                                 ("motion-probe","motion-probe.json"),
                                 ("motion-run","motion-run-report.json"),
                                 ("motion-verify","motion-verify-report.json")):
                result=motion(exe,command,batch=out/"motion-batch.json")
                new_file(out/name,write_bytes(result),guard)
            fresh=evaluate(mp,args.manifest_sha256,exe,args.renderer_sha256)
            require(fresh==report,"SOURCE_DRIFT_AFTER_MOTION")
            checked=check_candidate_files(report,out,guard,exe)
            complete={"schema":"h3.native-alias.completion.v1",
                      "operation_id":report["operation_id"],
                      "triage_sha256":digest(write_bytes(report)),
                      "motion_batch_sha256":digest(read_bounded(out/"motion-batch.json",8*1024*1024,"batch")),
                      "motion_owner_and_alias_png_sha256":checked["owner_png_sha256"],
                      "renderer_sha256":report["renderer_sha256"],"review_required":True,
                      "native_timing_verified":False,"art_approved":False,"game_deployed":False}
            new_file(out/"analysis-complete.json",write_bytes(complete),guard)
            guard.verify_current()
        finally:
            guard.close()
        result=verify(report,out,exe)
        result["status"]="CANDIDATE_REVIEW_REQUIRED"
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"schema":"h3.native-alias.error.v1","status":"BLOCKED",
              "error":str(exc),"art_approved":False},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
