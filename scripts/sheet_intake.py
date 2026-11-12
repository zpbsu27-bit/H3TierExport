"""SHA-bound whole-sheet frame views and nonzero-alpha ownership diagnostics.

No tier resampling, PNG extraction, frame approval, timing inference or game writes.
The sheet remains one immutable input; only JSON/HTML evidence is emitted.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import gzip
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from review_native_batch import (
    ReviewError, OutputRootGuard, absolute_file, read_bounded, parse_json,
    reject_reparse_components, _lexists, _path_key, _lexically_within,
    _read_build_receipt, sha256_file, runtime_source_path,
)
from build import validate_build_sources
import numpy as np
from review_motion_sequence import source_shape_diagnostics

PRODUCT=resolve_tool_root(__file__)
OUTPUT_BASE=PRODUCT/"build/experiments/sheet-intake"
MAX_SOURCE=128*1024*1024
MAX_PIXELS=16*1024*1024
MAX_REFS=20
MAX_VIEWS=64
SHA=re.compile(r"^[a-f0-9]{64}$")
TOKEN=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
ROLE=re.compile(r"^[a-z][a-z0-9_]{0,63}$")

def need(ok:bool,code:str)->None:
    if not ok:raise ReviewError(code)
def exact(value:Any,keys:set[str],label:str)->None:
    need(isinstance(value,dict) and set(value)==keys,"FIELDS_INVALID:"+label)
def number(v:Any,label:str,low:int=0,high:int=16384)->int:
    need(type(v) is int and low<=v<=high,"INTEGER_INVALID:"+label)
    return v
def pair(v:Any,label:str,low:int=0)->tuple[int,int]:
    need(type(v) is list and len(v)==2,"PAIR_INVALID:"+label)
    return (number(v[0],label+".x",low),number(v[1],label+".y",low))
def rect(v:Any,label:str)->tuple[int,int,int,int]:
    need(type(v) is list and len(v)==4,"RECT_INVALID:"+label)
    x,y,w,h=[number(q,label) for q in v]
    need(w>0 and h>0,"RECT_EMPTY:"+label)
    return x,y,w,h
def digest(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def reference(ref:Any,label:str,limit:int=MAX_SOURCE)->tuple[Path,bytes]:
    exact(ref,{"path","sha256"},label)
    need(isinstance(ref["sha256"],str) and SHA.fullmatch(ref["sha256"]) is not None,"SHA_INVALID:"+label)
    p=absolute_file(ref["path"],label)
    raw=read_bounded(p,limit,label)
    need(digest(raw)==ref["sha256"],"REFERENCE_DRIFT:"+label)
    return p,raw
def fresh(path:Path,sha:str)->None:
    reject_reparse_components(path,allow_missing_leaf=False)
    need(digest(read_bounded(path,MAX_SOURCE,"recheck"))==sha,"REFERENCE_DRIFT_DURING_OPERATION:"+str(path))
def load_doc(raw:bytes,label:str)->dict:
    value=parse_json(raw,label)
    need(isinstance(value,dict),"OBJECT_REQUIRED:"+label)
    return value
def key(value:Any)->tuple[str,str,int,int]:
    need(type(value) is list and len(value)==4 and value[0] in ("def","pcx") and
         isinstance(value[1],str) and 0<len(value[1])<=80 and
         type(value[2]) is int and 0<=value[2]<=21 and
         type(value[3]) is int and 0<=value[3]<=4096,"NATIVE_KEY_INVALID")
    return tuple(value)

def render_source(source:dict)->tuple[Path,bytes,bytes,int,int]:
    exact(source,{"path","sha256","pixels","rgba_sha256"},"source")
    p,raw=reference({"path":source["path"],"sha256":source["sha256"]},"source",MAX_SOURCE)
    rgba,w,h=decode_source(raw,source["pixels"])
    need(isinstance(source["rgba_sha256"],str) and digest(rgba)==source["rgba_sha256"],"SOURCE_RGBA_SHA_DRIFT")
    return p,raw,rgba,w,h

def decode_source(raw:bytes,pixels:list)->tuple[bytes,int,int]:
    from PIL import Image
    need(len(raw)>=33 and raw[:8]==b"\x89PNG\r\n\x1a\n" and raw[24]==8 and raw[25]==6,
         "SOURCE_REQUIRES_8BIT_RGBA_PNG")
    try:
        import io
        with Image.open(io.BytesIO(raw)) as im:
            need(im.format=="PNG" and im.mode=="RGBA","PNG_RGBA_REQUIRED")
            w,h=im.size
            need(0<w<=8192 and 0<h<=8192 and w*h<=MAX_PIXELS,"SHEET_PIXEL_BUDGET")
            need(pixels==[w,h],"SHEET_PIXELS_DRIFT")
            rgba=im.tobytes()
    except (OSError,ValueError) as error:
        raise ReviewError("PNG_DECODE_FAILED:"+str(error)) from error
    return rgba,w,h

def prepared_partition_report(manifest:dict,manifest_path:Path,expected_sha:str,
                              exe:Path,exe_sha:str,build_sha:str)->dict:
    """Read the owner's immutable label proof; never create or repair a partition."""
    from PIL import Image
    import creature_action_plan
    exact(manifest,{"schema","operation_id","prepared_proof","official_inventory","source_status"},"prepared manifest")
    need(manifest["source_status"] in ("NEEDS_KEYFRAME","NEEDS_NATIVE_MAPPING"),"PREPARED_DIAGNOSTIC_CANNOT_GRANT_READINESS")
    pp,pr=reference(manifest["prepared_proof"],"prepared proof",8*1024*1024)
    proof=load_doc(pr,"prepared proof")
    proof_schema=proof.get("schema")
    if proof_schema=="h3.ai-sprite.exact-positive-alpha-partition.v2":
        partition_alpha_floor=1
        need(type(proof.get("alpha_threshold")) is int and proof["alpha_threshold"]==1 and
             proof.get("all_positive_alpha_pixels_assigned_exactly_once") is True,
             "PREPARED_POSITIVE_ALPHA_CONTRACT_INVALID")
    elif proof_schema=="h3.ai-sprite.exact-alpha-partition.v1":
        # The producer keeps the historical floor-2 proof/schema.  It has no
        # v3 omission accounting because alpha=1 was already outside the
        # owned-body contract; verify the exact eligible mask below instead.
        partition_alpha_floor=2
        need(type(proof.get("alpha_threshold")) is int and proof["alpha_threshold"]==2 and
             proof.get("all_alpha_ge2_pixels_assigned_exactly_once") is True,
             "PREPARED_ALPHA_FLOOR_CONTRACT_INVALID")
    elif proof_schema=="h3.ai-sprite.alpha-floor-partition.v3":
        partition_alpha_floor=proof.get("partition_alpha_floor")
        need(type(partition_alpha_floor) is int and partition_alpha_floor in (2,8,16),
             "PREPARED_ALPHA_FLOOR_INVALID")
        need(proof.get("all_partition_eligible_alpha_pixels_assigned_exactly_once") is True and
             type(proof.get("sub_floor_positive_pixels_omitted")) is int and
             proof["sub_floor_positive_pixels_omitted"]>=0 and
             type(proof.get("sub_floor_positive_components_omitted")) is int and
             proof["sub_floor_positive_components_omitted"]>=0 and
             type(proof.get("sub_floor_maximum_alpha")) is int and
             0<=proof["sub_floor_maximum_alpha"]<partition_alpha_floor and
             proof.get("sub_floor_semantics")==
             "unmodified/unmoved/unredrawn pixels; below-floor positive pixels are excluded from ownership only",
             "PREPARED_ALPHA_FLOOR_CONTRACT_INVALID")
    else:
        raise ReviewError("PREPARED_PARTITION_SCHEMA_UNSUPPORTED")
    need(proof.get("human_approved") is False and proof.get("raw_png_unchanged") is True and
         all(proof.get(n) is False for n in ("resampling_performed","new_art_pixels_created","motion_inference")),
         "PREPARED_POSITIVE_ALPHA_CONTRACT_INVALID")
    rp,rr=reference({"path":proof.get("request_path"),"sha256":proof.get("request_sha256")},"prepared request",16*1024*1024)
    request=load_doc(rr,"prepared request")
    count=number(proof.get("sprite_count"),"sprite_count",1,MAX_VIEWS)
    need(request.get("schema")=="h3.full-art.request.v1" and request.get("frame_count")==count,
         "PREPARED_REQUEST_FRAME_COUNT_MISMATCH")
    slots=request.get("original_slots")
    need(isinstance(slots,list) and len(slots)==count,"PREPARED_ORIGINAL_SLOTS_MISSING")
    sp,sr=reference({"path":proof.get("raw_output"),"sha256":proof.get("raw_sha256")},"prepared raw")
    rgba,w,h=decode_source(sr,proof.get("raw_pixels"))
    image=np.frombuffer(rgba,dtype=np.uint8).reshape(h,w,4)
    lp,lr=reference({"path":proof.get("label_map"),"sha256":proof.get("label_map_sha256")},"owner labels",32*1024*1024)
    with gzip.GzipFile(fileobj=io.BytesIO(lr)) as stream:
        labels_raw=stream.read(w*h+1)
    need(len(labels_raw)==w*h,"PREPARED_LABEL_LENGTH_INVALID")
    labels=np.frombuffer(labels_raw,dtype=np.uint8).reshape(h,w)
    alpha_channel=image[:,:,3]
    positive=alpha_channel>0
    eligible=alpha_channel>=partition_alpha_floor
    omitted=positive & ~eligible
    need(np.array_equal(labels!=255,eligible) and not np.any((labels!=255)&(labels>=count)),
         "PREPARED_POSITIVE_OWNERSHIP_MISMATCH")
    need(proof.get("visible_pixels")==int(eligible.sum()),"PREPARED_VISIBLE_COUNT_DRIFT")
    if proof_schema=="h3.ai-sprite.alpha-floor-partition.v3":
        omitted_count=int(omitted.sum())
        omitted_max=int(alpha_channel[omitted].max()) if omitted_count else 0
        need(proof["sub_floor_positive_pixels_omitted"]==omitted_count and
             proof["sub_floor_maximum_alpha"]==omitted_max,
             "PREPARED_ALPHA_FLOOR_OMISSION_DRIFT")
        need((proof["sub_floor_positive_components_omitted"]==0)==(omitted_count==0),
             "PREPARED_ALPHA_FLOOR_COMPONENT_COUNT_INVALID")
    ip,ir=reference(manifest["official_inventory"],"prepared inventory")
    catalog,inventory_sha=creature_action_plan.load_catalog(ip,digest(ir))
    native={}
    for row in catalog["rows"]:
        if row.get("kind")=="def":
            k=("def",row.get("identity"),row.get("group"),row.get("frame"))
            need(k not in native,"PREPARED_NATIVE_KEY_DUPLICATE");native[k]=row
    cols,rows=pair(request.get("grid"),"prepared grid",1)
    need(cols*rows>=count and proof.get("grid")==[cols,rows],"PREPARED_GRID_INVALID")
    cells=proof.get("observed_major_grid_cells")
    need(isinstance(cells,list) and len(cells)==count and len(set(cells))==count and
         all(type(n) is int and 0<=n<cols*rows for n in cells),"PREPARED_CELL_MAPPING_INVALID")
    declared_counts=proof.get("sprite_pixels")
    need(isinstance(declared_counts,list) and len(declared_counts)==count,"PREPARED_COUNTS_MISSING")
    dependencies=[{"path":str(q),"sha256":digest(raw)} for q,raw in ((pp,pr),(rp,rr),(sp,sr),(lp,lr),(ip,ir))]
    frames=[];keys=set()
    for index,slot in enumerate(slots):
        k=key(slot.get("key"));need(k not in keys and k in native,"PREPARED_SLOT_KEY_MISMATCH");keys.add(k)
        row=native[k];need(row.get("category")=="creatures","PREPARED_NATIVE_DOMAIN_MISMATCH")
        canvas,crop,origin=creature_action_plan.validate_geometry(row,(k[1],k[2],k[3]))
        targets={"canvas_1x":canvas,"crop_1x":crop,"origin_1x":origin}
        need(isinstance(slot.get("targets"),dict) and all(slot["targets"].get(n)==v for n,v in targets.items()),
             "PREPARED_NATIVE_GEOMETRY_MISMATCH")
        cp,cr=reference({"path":slot.get("crop_path"),"sha256":slot.get("crop_sha256")},"native crop guide")
        with Image.open(io.BytesIO(cr)) as picture:
            need(picture.format=="PNG" and list(picture.size)==crop,"PREPARED_NATIVE_GUIDE_GEOMETRY_MISMATCH")
            native_alpha=np.asarray(picture.convert("RGBA").getchannel("A"),dtype=np.uint8)
        dependencies.append({"path":str(cp),"sha256":digest(cr)})
        owned=labels==index;pixels=int(owned.sum())
        need(type(declared_counts[index]) is int and pixels==declared_counts[index],"PREPARED_SPRITE_COUNT_DRIFT")
        alpha=np.where(owned,alpha_channel,0).astype(np.uint8)
        geometry={"canvas":canvas,"crop":crop,"origin":origin}
        metrics=source_shape_diagnostics(alpha,geometry,native_alpha)
        cell=cells[index];left=w*(cell%cols)//cols;right=w*(cell%cols+1)//cols
        top=h*(cell//cols)//rows;bottom=h*(cell//cols+1)//rows
        outside=pixels-int(owned[top:bottom,left:right].sum())
        metrics["per_cell_containment_verified"]=outside==0
        frames.append({"id":"OWNER_"+str(index),"role":"frame","key":list(k),
            "rect_px":[0,0,w,h],"declared_grid_cell_ltrb":[left,top,right,bottom],
            "owned_positive_outside_declared_cell":outside,"geometry":geometry,
            "natural_k":None,"status":manifest["source_status"],"art_approved":False,
            "native_reference_payload_identity_verified":False,
            "native_reference_scope":"Request-pinned PNG geometry; native payload/index/palette identity is not independently decoded by this diagnostic.",
            "source_diagnostics":metrics,"partition_alpha_floor":partition_alpha_floor,
            "display_scope":"Whole raw sheet; owner labels are measured, not a cropped frame preview."})
    return {"schema":"h3.sheet-intake.prepared-partition-review.v1","intake_kind":"PREPARED_POSITIVE_ALPHA_PARTITION",
        "status":"BLOCKED","operation_id":manifest["operation_id"],
        "source":{"path":str(sp),"sha256":digest(sr),"pixels":[w,h],"rgba_sha256":digest(rgba)},
        "manifest":{"path":str(manifest_path),"sha256":expected_sha},"dependencies":dependencies,
        "renderer":{"path":str(exe),"sha256":exe_sha},"build_receipt_sha256":build_sha,
        "summary":{"blocked_views":count,"ownership_partition_verified":True,
                   "per_cell_containment_verified":all(f["owned_positive_outside_declared_cell"]==0 for f in frames),
                   "cell_scope":"Exact membership in frozen declared grid cells; no semantic body or placement approval",
                   "common_action_scale_verified":False,"k_inferred_or_selected":False,
                   "partition_alpha_floor":partition_alpha_floor,
                   "partition_eligible_pixels":int(eligible.sum()),
                   "positive_total":int(positive.sum()),
                   "positive_omitted_by_floor":int(omitted.sum()),
                   "alpha1_total":int(np.count_nonzero(alpha_channel==1))},
        "frames":frames,"no_pixels_mutated":True,"source_png_written":False,
        "art_approved":False,"native_timing_verified":False,"game_deployed":False}

def semantic_authority(docs:dict[str,dict],roles:dict[str,dict],manifest:dict,views:list[dict])->dict:
    proof={"town_request_receipt_bound":False,"map_slots_checked":0,"source_view_frozen":True,
           "artist_approval_inferred":False,"native_timing_inferred":False}
    if "town_raw_receipt" in docs:
        need("town_request" in docs,"TOWN_REQUEST_REQUIRED")
        receipt=docs["town_raw_receipt"];request=docs["town_request"]
        need(receipt.get("schema")=="h3.art-town.raw-intake.v1","TOWN_RAW_RECEIPT_SCHEMA")
        need(receipt.get("request",{}).get("sha256")==roles["town_request"]["sha256"] and
             receipt.get("request",{}).get("path")==roles["town_request"]["path"],
             "TOWN_RAW_REQUEST_REFERENCE_MISMATCH")
        master=receipt.get("master",{})
        need(master.get("sha256")==manifest["source"]["sha256"] and
             master.get("path")==manifest["source"]["path"] and
             master.get("pixels")==manifest["source"]["pixels"],"TOWN_MASTER_REFERENCE_MISMATCH")
        need(len([v for v in views if v["role"]=="frame"])==1,"TOWN_EXACT_ONE_FRAME_PER_RAW_RECEIPT")
        frame=next(v for v in views if v["role"]=="frame")
        need(request.get("key")==frame["key"],"TOWN_KEY_MISMATCH")
        geo=request.get("native_geometry",{})
        need(geo.get("canvas")==frame["geometry"]["canvas"] and
             geo.get("crop")==frame["geometry"]["crop"] and
             geo.get("origin")==frame["geometry"]["origin"],"TOWN_NATIVE_GEOMETRY_MISMATCH")
        if "town_guide" in roles and isinstance(request.get("native_input_proof"),dict):
            guide=request["native_input_proof"]
            need(guide.get("sha256")==roles["town_guide"]["sha256"] and
                 guide.get("path")==roles["town_guide"]["path"],"TOWN_GUIDE_DRIFT")
        proof["town_request_receipt_bound"]=True
    if "map_original_request" in docs:
        request=docs["map_original_request"]
        slots=request.get("original_slots")
        need(isinstance(slots,list) and 1<=len(slots)<=4096,"MAP_ORIGINAL_SLOTS_INVALID")
        known={}
        for slot in slots:
            row=slot.get("row",{}) if isinstance(slot,dict) else {}
            k=(row.get("kind"),row.get("identity"),row.get("group"),row.get("frame"))
            need(k not in known,"MAP_DUPLICATE_ORIGINAL_KEY")
            known[k]=row
        for v in views:
            if v["role"]!="frame":continue
            k=key(v["key"])
            need(k in known,"MAP_FRAME_NOT_IN_ORIGINAL")
            row=known[k]
            geo=v["geometry"]
            need(row.get("canvas")==geo["canvas"] and row.get("crop")==geo["crop"] and
                 row.get("anchor")==geo["origin"],"NATIVE_GEOMETRY_MISMATCH")
            proof["map_slots_checked"]+=1
    return proof

def evaluate(manifest_path:Path,expected_sha:str,exe:Path,exe_sha:str)->dict:
    need(TOKEN.fullmatch(manifest_path.stem) is not None or manifest_path.is_file(),"MANIFEST_NOT_FOUND")
    _manifest_path,raw=reference({"path":str(manifest_path),"sha256":expected_sha},"manifest",8*1024*1024)
    manifest=load_doc(raw,"manifest")
    prepared=manifest.get("schema")=="h3.sheet-intake.prepared-partition.v1"
    if not prepared:
        exact(manifest,{"schema","operation_id","source","references","views"},"manifest")
        need(manifest["schema"]=="h3.sheet-intake.v1","SHEET_INTAKE_SCHEMA")
    operation=manifest["operation_id"]
    need(isinstance(operation,str) and TOKEN.fullmatch(operation) is not None,"OPERATION_ID_INVALID")
    e=absolute_file(str(exe),"renderer")
    need(digest(read_bounded(e,16*1024*1024,"renderer"))==exe_sha,"RENDERER_SHA_DRIFT")
    from build import sha
    binding=_read_build_receipt(exe_sha,e)
    buildpath=Path(binding["path"])
    build=load_doc(read_bounded(buildpath,16*1024*1024,"build receipt"),"build")
    if binding.get("authority")=="formal_release":
        expected={row["path"].replace(chr(92),"/"):row["sha256"] for row in build["sources"]}
        name="scripts/sheet_intake.py"
        need(expected.get(name)==sha256_file(runtime_source_path(name),16*1024*1024),
             "RELEASE_COMPANION_SOURCE_DRIFT: "+name)
    else:
        validate_build_sources(build)
    need(build["executable"]["sha256"]==exe_sha,"BUILD_RENDERER_NOT_CURRENT")
    if prepared:
        return prepared_partition_report(manifest,manifest_path,expected_sha,e,exe_sha,sha(buildpath))
    sheet_path,_raw,rgba,w,h=render_source(manifest["source"])
    references=manifest["references"]
    need(type(references) is list and 1<=len(references)<=MAX_REFS,"REFERENCES_REQUIRED")
    docs={};roles={};deps=[]
    for item in references:
        exact(item,{"role","ref"},"reference")
        role=item["role"]
        need(isinstance(role,str) and ROLE.fullmatch(role) is not None and role not in roles,
             "REFERENCE_ROLE_DUPLICATE_OR_INVALID")
        path,raw=reference(item["ref"],role,32*1024*1024)
        need(path != sheet_path,"SHEET_MUST_NOT_BE_OWN_AUTHORITY")
        roles[role]=item["ref"]
        docs[role]=load_doc(raw,role) if role in ("town_request","town_raw_receipt","map_original_request") else {}
        deps.append({"role":role,"path":str(path),"sha256":digest(raw)})
    views=manifest["views"]
    need(type(views) is list and 1<=len(views)<=MAX_VIEWS,"FRAME_VIEW_COUNT_INVALID")
    owner=bytearray(w*h);idset=set();frames=[];specs=[]
    for ordinal,v in enumerate(views,1):
        need(isinstance(v,dict),"VIEW_OBJECT_REQUIRED")
        role=v.get("role")
        if role=="empty":
            exact(v,{"id","role","rect_px"},"empty frame view")
        elif role=="frame":
            exact(v,{"id","role","rect_px","key","geometry","natural_k","art_status"},"frame view")
        else:raise ReviewError("VIEW_ROLE_INVALID")
        id=v["id"]
        need(isinstance(id,str) and TOKEN.fullmatch(id) is not None and id not in idset,
             "DUPLICATE_OR_INVALID_FRAME_ID")
        idset.add(id)
        x,y,cw,ch=rect(v["rect_px"],"frame rectangle")
        need(x+cw<=w and y+ch<=h,"FRAME_RECT_OUTSIDE_SHEET")
        for py in range(y,y+ch):
            start=py*w+x
            need(not any(owner[start:start+cw]),"FRAME_RECT_OVERLAP")
            owner[start:start+cw]=bytes((ordinal,))*cw
        s={"id":id,"role":role,"rect_px":[x,y,cw,ch]}
        if role=="frame":
            k=key(v["key"])
            geo=v["geometry"]
            exact(geo,{"canvas","crop","origin"},"native geometry")
            wc,hc=pair(geo["canvas"],"native.canvas",1)
            cropw,croph=pair(geo["crop"],"native.crop",1)
            ox,oy=pair(geo["origin"],"native.origin")
            need(ox+cropw<=wc and oy+croph<=hc,"NATIVE_CROP_OUTSIDE_CANVAS")
            kscale=v["natural_k"]
            if kscale is not None:
                kval=number(kscale,"natural_k",1,64)
                need(cw==wc*kval and ch==hc*kval,"FRAME_NATIVE_INTEGER_SCALE_GEOMETRY")
            else:kval=None
            need(isinstance(v["art_status"],str) and 0<len(v["art_status"])<=150,
                 "ART_STATUS_REQUIRED")
            s.update({"key":list(k),"geometry":geo,"natural_k":kval,
              "mapping_status":"INTEGER_NATIVE_VIEW" if kval else "NEEDS_NATIVE_MAPPING",
              "art_status":v["art_status"],"art_approved":False})
        specs.append(s)
    need(not any(value==0 for value in owner),"FRAME_PARTITION_INCOMPLETE")
    authority=semantic_authority(docs,roles,manifest,views)
    summary={"positive_total":0,"alpha1_total":0,"positive_unowned":0,
             "unused_positive":0,"unused_alpha1":0,
             "outside_native_crop_positive":0,"outside_native_crop_alpha1":0,
             "frame_views":sum(v["role"]=="frame" for v in views),
             "empty_views":sum(v["role"]=="empty" for v in views),
             "mapping_unknown":sum(v["role"]=="frame" and v["natural_k"] is None for v in views)}
    counters=[{"positive":0,"alpha1":0,"outside":0,"outside_alpha1":0,"touch_top":0,
               "touch_bottom":0,"touch_left":0,"touch_right":0,
               "outside_first_xy_alpha":[],"alpha_histogram":[0]*256} for _ in views]
    for n in range(w*h):
        alpha=rgba[n*4+3]
        if alpha==0:continue
        i=owner[n]-1
        need(i>=0,"FRAME_UNASSIGNED_POSITIVE")
        x,y=n%w,n//w
        v=specs[i];st=counters[i]
        summary["positive_total"]+=1
        summary["alpha1_total"]+=(alpha==1)
        st["positive"]+=1;st["alpha1"]+=(alpha==1);st["alpha_histogram"][alpha]+=1
        left,top,width,height=v["rect_px"]
        st["touch_top"]+=(y==top);st["touch_bottom"]+=(y==top+height-1)
        st["touch_left"]+=(x==left);st["touch_right"]+=(x==left+width-1)
        if v["role"]=="empty":
            summary["unused_positive"]+=1;summary["unused_alpha1"]+=(alpha==1)
        elif v["natural_k"] is not None:
            k=v["natural_k"];ox,oy=v["geometry"]["origin"]
            cw,ch=v["geometry"]["crop"]
            localx=x-left;localy=y-top
            if not (ox*k<=localx<(ox+cw)*k and oy*k<=localy<(oy+ch)*k):
                st["outside"]+=1;st["outside_alpha1"]+=(alpha==1)
                summary["outside_native_crop_positive"]+=1
                summary["outside_native_crop_alpha1"]+=(alpha==1)
                if len(st["outside_first_xy_alpha"])<256:
                    st["outside_first_xy_alpha"].append([x,y,alpha])
    for v,st in zip(specs,counters):
        if v["role"]=="frame":
            left,top,cw,ch=v["rect_px"]
            alpha=np.frombuffer(rgba,dtype=np.uint8).reshape(h,w,4)[top:top+ch,left:left+cw,3]
            v["source_diagnostics"]=source_shape_diagnostics(alpha,v["geometry"],natural_k=v["natural_k"],full_native_view=True)
        v["alpha"]={"positive":st["positive"],"alpha1":st["alpha1"],
           "outside_native_crop_positive":st["outside"],
           "outside_native_crop_alpha1":st["outside_alpha1"],
           "outside_first_xy_alpha":st["outside_first_xy_alpha"],
           "outside_coordinates_truncated":st["outside"]>len(st["outside_first_xy_alpha"]),
           "touch":{"top":st["touch_top"],"bottom":st["touch_bottom"],
                    "left":st["touch_left"],"right":st["touch_right"]},
           "histogram":st["alpha_histogram"]}
        if v["role"]=="empty":v["status"]="BLOCKED_UNUSED_CELL_ALPHA" if st["positive"] else "VERIFIED_EMPTY"
        elif v["natural_k"] is None:v["status"]="NEEDS_NATIVE_MAPPING"
        elif st["positive"]==0:v["status"]="NEEDS_KEYFRAME"
        elif st["outside"]:v["status"]="BLOCKED_OUTSIDE_NATIVE_CROP"
        else:v["status"]="REVIEW_REQUIRED"
    blocked=sum(v["status"] not in ("REVIEW_REQUIRED","VERIFIED_EMPTY") for v in specs)
    summary["blocked_views"]=blocked
    return {"schema":"h3.sheet-intake.review.v1",
     "status":"BLOCKED" if blocked else "REVIEW_REQUIRED","operation_id":operation,
     "source":{"path":str(sheet_path),"sha256":digest(_raw),"pixels":[w,h],
               "rgba_sha256":manifest["source"]["rgba_sha256"]},
     "manifest":{"path":str(manifest_path),"sha256":expected_sha},
     "renderer":{"path":str(e),"sha256":exe_sha},
     "build_receipt_sha256":sha(buildpath),"dependencies":deps,
     "authority":authority,"summary":summary,"frames":specs,
     "no_pixels_mutated":True,"source_png_written":False,
     "art_approved":False,"native_timing_verified":False,"game_deployed":False}

def create_html(report:dict)->bytes:
    if report.get("intake_kind")=="PREPARED_POSITIVE_ALPHA_PARTITION":
        source=html.escape(report["source"]["path"])
        rows=[]
        for frame in report["frames"]:
            d=frame["source_diagnostics"]
            cells=[frame["key"],frame["status"],d["positive_bbox_ltrb"],d["alpha1_pixels"],
                   d["alpha128_bbox_ltrb"],d["axis_scale_from_alpha128_bbox"],
                   frame["owned_positive_outside_declared_cell"]]
            rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in cells)+'</tr>')
        return ('<!doctype html><meta charset="utf-8"><title>Prepared source diagnostics</title>'
            '<style>body{font:15px system-ui;padding:24px}td,th{padding:8px;border:1px solid #bbb}table{border-collapse:collapse}</style>'
            '<h1>Prepared positive-alpha source diagnostics</h1><p>Source: '+source+'</p>'
            '<p>Owner labels preserve every positive alpha. Ownership is separate from cell containment, K, placement and art approval. '
            'Alpha128 is an envelope/component proxy; foot semantics and native timing are unverified. No image is extracted or changed.</p>'
            '<table><tr><th>Native slot</th><th>State</th><th>Positive bounds</th><th>Alpha1</th><th>Alpha128 bounds</th>'
            '<th>Body-proxy axis scales</th><th>Owned positive outside declared cell</th></tr>'+''.join(rows)+'</table>').encode('utf-8')
    source=report["source"]
    w,h=source["pixels"]
    image=html.escape(Path(source["path"]).as_uri(),quote=True)
    overlays=[]
    for frame in report["frames"]:
        x,y,cw,ch=frame["rect_px"]
        label=html.escape(frame["id"]+" "+frame["status"])
        overlays.append('<div class="frame" style="left:'+str(x/w*100)+'%;top:'+
          str(y/h*100)+'%;width:'+str(cw/w*100)+'%;height:'+str(ch/h*100)+'%">'+label+"</div>")
    content=('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
      '<title>H3TierExport sheet intake — review only</title>'
      '<style>body{font:14px system-ui;max-width:1200px;margin:24px auto}'
      '.sheet{position:relative;width:min(100%,1024px)}.sheet img{width:100%;display:block}'
      '.frame{position:absolute;border:2px solid #ff7744;box-sizing:border-box;color:white;'
      'text-shadow:0 1px black;background:#0004;overflow:hidden;font-size:11px}'
      '</style><h1>Frozen whole-sheet alpha ownership</h1><p>Diagnostic only. '
      'No crop, model edit, art acceptance, native timing or game deployment.</p>'
      '<p>Status: '+html.escape(report["status"])+'</p><div class="sheet"><img src="'+image+
      '" alt="Original unchanged sheet">'+''.join(overlays)+'</div><p>Exact per-frame '
      'counts and SHA bindings: review-report.json</p></html>')
    return content.encode("utf-8")

def output_root(path:Path,operation:str)->Path:
    path=Path(os.path.abspath(path))
    base=Path(os.path.abspath(OUTPUT_BASE))
    need(_path_key(path.parent)==_path_key(base) and path.name==operation,
         "OUTPUT_MUST_BE_OPERATION_CHILD")
    return path
def store(path:Path,data:bytes,guard:OutputRootGuard)->str:
    path=guard.verify_target(path)
    with path.open("xb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    guard.verify_target(path,must_exist=True)
    need(digest(read_bounded(path,16*1024*1024,"output"))==digest(data),"OUTPUT_READBACK_MISMATCH")
    return digest(data)
def verify(report:dict,out:Path,expected_report:dict,renderer_sha:str)->dict:
    need(out.is_dir(),"OUTPUT_ROOT_MISSING")
    guard=OutputRootGuard.open_existing(out,OUTPUT_BASE)
    try:
        rp=guard.verify_target(out/"review-report.json",must_exist=True)
        hp=guard.verify_target(out/"index.html",must_exist=True)
        actual=load_doc(read_bounded(rp,16*1024*1024,"report"),"report")
        need(actual==expected_report,"DIAGNOSTIC_DRIFT")
        need(digest(read_bounded(hp,4*1024*1024,"html"))==digest(create_html(report)),"HTML_DRIFT")
        receipt=out/"analysis-complete.json"
        if report["status"]=="BLOCKED":
            need(not _lexists(receipt),"BLOCKED_HAS_FORBIDDEN_COMPLETION")
        else:
            cr=guard.verify_target(receipt,must_exist=True)
            complete=load_doc(read_bounded(cr,1024*1024,"completion"),"completion")
            need(complete=={"schema":"h3.sheet-intake.completion.v1","status":"REVIEW_REQUIRED",
                "operation_id":report["operation_id"],"report_sha256":digest(read_bounded(rp,16*1024*1024,"report")),
                "html_sha256":digest(read_bounded(hp,4*1024*1024,"html")),
                "renderer_sha256":renderer_sha,"art_approved":False},"COMPLETION_DRIFT")
        guard.verify_current()
    finally:guard.close()
    return {"schema":"h3.sheet-intake.result.v1",
      "status":"VERIFIED_BLOCKED" if report["status"]=="BLOCKED" else "VERIFIED_REVIEW_REQUIRED",
      "operation_id":report["operation_id"],"diagnostic_sha256":digest(read_bounded(out/"review-report.json",16*1024*1024,"report")),
      "summary":report["summary"],"art_approved":False}

def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=("check","run","verify"))
    for option in ("manifest","manifest-sha256","outdir","operation-id","renderer","renderer-sha256"):
        parser.add_argument("--"+option,required=True)
    args=parser.parse_args(argv)
    try:
        need(isinstance(args.operation_id,str) and TOKEN.fullmatch(args.operation_id) is not None,"OPERATION_ID_INVALID")
        need(isinstance(args.manifest_sha256,str) and SHA.fullmatch(args.manifest_sha256) is not None,"MANIFEST_SHA_INVALID")
        need(isinstance(args.renderer_sha256,str) and SHA.fullmatch(args.renderer_sha256) is not None,"RENDERER_SHA_INVALID")
        mp=absolute_file(args.manifest,"manifest")
        out=output_root(Path(args.outdir),args.operation_id)
        report=evaluate(mp,args.manifest_sha256,Path(args.renderer),args.renderer_sha256)
        need(report["operation_id"]==args.operation_id,"OPERATION_ID_MISMATCH")
        if args.stage=="check":
            print(json.dumps(report,ensure_ascii=False))
            return 1 if report["status"]=="BLOCKED" else 0
        if args.stage=="verify":
            result=verify(report,out,report,args.renderer_sha256)
            print(json.dumps(result,ensure_ascii=False))
            return 1 if report["status"]=="BLOCKED" else 0
        if _lexists(out):
            result=verify(report,out,report,args.renderer_sha256)
            result["status"]="REUSED_VERIFIED" if report["status"]!="BLOCKED" else "BLOCKED_EXISTING_DIAGNOSTIC"
            print(json.dumps(result,ensure_ascii=False))
            return 1 if report["status"]=="BLOCKED" else 0
        guard=OutputRootGuard.create(out,OUTPUT_BASE)
        try:
            # Check all source evidence a second time after taking directory locks.
            newer=evaluate(mp,args.manifest_sha256,Path(args.renderer),args.renderer_sha256)
            need(newer==report,"INPUT_CHANGED_BEFORE_OUTPUT")
            report_bytes=(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode("utf-8")
            report_sha=store(out/"review-report.json",report_bytes,guard)
            html_sha=store(out/"index.html",create_html(report),guard)
            need(evaluate(mp,args.manifest_sha256,Path(args.renderer),args.renderer_sha256)==report,
                 "INPUT_CHANGED_DURING_OUTPUT")
            if report["status"]!="BLOCKED":
                complete={"schema":"h3.sheet-intake.completion.v1","status":"REVIEW_REQUIRED",
                     "operation_id":args.operation_id,"report_sha256":report_sha,
                     "html_sha256":html_sha,"renderer_sha256":args.renderer_sha256,
                     "art_approved":False}
                store(out/"analysis-complete.json",(json.dumps(complete,sort_keys=True,indent=2)+"\n").encode("utf-8"),guard)
            guard.verify_current()
        finally:guard.close()
        print(json.dumps({"schema":"h3.sheet-intake.result.v1","status":report["status"],
             "operation_id":args.operation_id,"summary":report["summary"],
             "report_sha256":digest(report_bytes),"review_required":True,
             "art_approved":False,"output":str(out)},ensure_ascii=False))
        return 1 if report["status"]=="BLOCKED" else 0
    except Exception as error:
        print(json.dumps({"schema":"h3.sheet-intake.error.v1","status":"BLOCKED",
          "error":str(error),"art_approved":False},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
