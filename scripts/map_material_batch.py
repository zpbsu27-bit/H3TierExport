"""Derive a complete native road/river family from shared high-resolution materials.

One generated material parent is phase-registered losslessly; official native
alpha geometry supplies each frame's silhouette. H3TierExport samples the
masked high-resolution parents directly to exact 3X. Original material pixels
are never resized before sampling, and the same texture coordinates are shared
across the complete family so legal tile borders can be checked as a batch.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image
import numpy as np

import map_assembly as assembly
import road_batch_quality as road_quality
import subject_scale_layout as common
from tile_interface_components import rational_native_roi_layer
import map_source_batch as source_batch

PRODUCT=resolve_tool_root(__file__)
OUT_ROOT=PRODUCT/"build/experiments/map-material-family"
SCHEMA="h3.map-material-batch.request.v1"
REPORT="h3.map-material-batch.report.v1"
FLIP_BITS={"none":0,"horizontal":1,"vertical":2,"both":3}
ROAD_FAMILIES={"COBBRD","DIRTRD","GRAVRD"}
RIVER_FAMILIES={"CLRRVR","ICYRVR","LAVRVR","MUDRVR"}


def family_frame_count(identity:str)->int:
    common.need(identity in ROAD_FAMILIES|RIVER_FAMILIES,"MATERIAL_FAMILY_IDENTITY_REQUIRED")
    return 17 if identity in ROAD_FAMILIES else 13


def pin_material_request(binding:dict,operation_id:str)->tuple[Path,dict]:
    path,raw=common.pin(binding);request=json.loads(raw.decode("utf-8-sig"))
    common.need(request.get("schema")==SCHEMA and request.get("operation_id")==operation_id,"MATERIAL_REQUEST_IDENTITY_DIFFERS")
    root=path.parent.resolve();common.need(root.parent.resolve()==OUT_ROOT.resolve(),"MATERIAL_REQUEST_OUTSIDE_EXPERIMENT_ROOT")
    for name in ("native_inventory","map_rule_proof","seam_auditor","texture_source","material_parent","native_contract"):
        common.pin(request[name])
    if request.get("editor_source_proof"):common.pin(request["editor_source_proof"])
    if request.get("bank_texture_source"):common.pin(request["bank_texture_source"])
    if request.get("bank_material_parent"):common.pin(request["bank_material_parent"])
    if request.get("retained_source_audit"):common.pin(request["retained_source_audit"])
    for row in request["source_rows"]:
        common.pin(row["source"]);common.pin(row["native_mask"])
    for frozen in request["motion_specs"]:common.pin(frozen["spec"])
    common.need(len(request["source_rows"])==request["frame_count"] and
                sum(x["item_count"] for x in request["motion_specs"])==request["frame_count"],
                "MATERIAL_FAMILY_SLOT_SET_DIFFERS")
    return root,request


def sha_file(path: Path)->str:return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path: Path)->dict:
    p=Path(path).resolve();return {"path":str(p),"sha256":sha_file(p)}
def write_new(path: Path,value:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as stream:stream.write((json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))


def phase_lock_material(texture: Path,phase_search_offset_xy:tuple[int,int]|None=None)->tuple[np.ndarray,dict]:
    raw=texture.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    with Image.open(texture) as image:
        image.load();common.need(image.format=="PNG","MATERIAL_PARENT_PNG_REQUIRED")
        a=np.asarray(image.convert("RGB"),dtype=np.uint8).copy()
    common.need(a.shape[0]==a.shape[1] and a.shape[0]>=768,"MATERIAL_PARENT_SQUARE_HIGH_RES_REQUIRED")
    def seams(value):
        x=np.abs(value.astype(np.int16)-np.roll(value,1,axis=1).astype(np.int16)).mean(axis=(0,2))
        y=np.abs(value.astype(np.int16)-np.roll(value,1,axis=0).astype(np.int16)).mean(axis=(1,2))
        return x,y
    xcost,ycost=seams(a);cx=int(np.argmin(xcost));cy=int(np.argmin(ycost))
    # Rotate the texture in integer pixels so its least-contrasting natural
    # internal seam falls at the repeat boundary. An optional second integer
    # translation lets the family evaluator search legal mirrored joins while
    # preserving every source pixel and the periodic boundary cost.
    base_x=(-cx)%a.shape[1];base_y=(-cy)%a.shape[0]
    dx,dy=phase_search_offset_xy or (0,0)
    common.need(isinstance(dx,int) and isinstance(dy,int),"MATERIAL_PHASE_SEARCH_OFFSET_MUST_BE_INTEGER")
    a=np.roll(a,shift=(base_y+dy,base_x+dx),axis=(0,1))
    xafter,yafter=seams(a)
    stats={"source_sha256":digest,"pixels":[int(a.shape[1]),int(a.shape[0])],
        "integer_phase_roll_xy":[(base_x+dx)%a.shape[1],(base_y+dy)%a.shape[0]],
        "phase_search_offset_xy":[dx,dy],
        "x_edge_mean_abs_rgb_before":float(xcost[cx]),"y_edge_mean_abs_rgb_before":float(ycost[cy]),
        "x_edge_mean_abs_rgb_after":float(xafter[0]),"y_edge_mean_abs_rgb_after":float(yafter[0]),
        "internal_x_p90_abs_rgb":float(np.percentile(xcost,90)),"internal_y_p90_abs_rgb":float(np.percentile(ycost,90)),
        "resampled":False,"pixels_changed":0,
        "phase_lock":"lowest-gradient periodic phase + legal-neighbor integer search; no interpolation" if (dx or dy) else "lowest-gradient periodic phase; integer roll only"}
    return a,stats


def expand_native_alpha(mask: np.ndarray,width: int,height: int)->np.ndarray:
    mh,mw=mask.shape
    x=np.floor((np.arange(width,dtype=np.float64)+.5)*mw/width).astype(np.int32).clip(0,mw-1)
    y=np.floor((np.arange(height,dtype=np.float64)+.5)*mh/height).astype(np.int32).clip(0,mh-1)
    return mask[y[:,None],x[None,:]].copy()


def native_bank_band(alpha1:np.ndarray,width_native:int)->np.ndarray:
    from collections import deque
    mask=alpha1>0;h,w=mask.shape;distance=np.full((h,w),h+w,dtype=np.int16)
    outside=np.argwhere(~mask);queue=deque()
    for y,x in outside:distance[y,x]=0;queue.append((int(y),int(x)))
    while queue:
        y,x=queue.popleft();next_distance=int(distance[y,x])+1
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                yy,xx=y+dy,x+dx
                if 0<=yy<h and 0<=xx<w and distance[yy,xx]>next_distance:
                    distance[yy,xx]=next_distance;queue.append((yy,xx))
    return (mask&(distance<=width_native)).astype(np.uint8)*255


def masked_material_parent(material:np.ndarray,alpha1:np.ndarray,geometry:dict,
                           bank_material:np.ndarray|None=None,bank_width_native:int=0)->np.ndarray:
    common.need(material.shape[0]==material.shape[1],"MATERIAL_PARENT_MUST_BE_SQUARE")
    alpha=expand_native_alpha(alpha1,material.shape[1],material.shape[0])
    rgb=material.copy()
    if bank_material is not None and bank_width_native>0:
        common.need(bank_material.shape==material.shape,"MATERIAL_BANK_TEXTURE_SIZE_DIFFERS")
        band=expand_native_alpha(native_bank_band(alpha1,bank_width_native),material.shape[1],material.shape[0])>0
        rgb[band]=bank_material[band]
    rgba=np.dstack([rgb,alpha]);rgba[alpha==0,:3]=0
    return rgba


def freeze(operation_id:str,identity:str,texture:Path,inventory:Path,native_frame_root:Path,
           rule_proof:Path,seam_auditor:Path,release_root:Path,master_root:Path,
           retain_frame:int|None=None,retain_audit:Path|None=None,
           bank_texture:Path|None=None,bank_width_native:int=0,
           phase_search_offset_xy:tuple[int,int]|None=None)->dict:
    common.need(re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}",operation_id) is not None,"MATERIAL_OPERATION_ID_INVALID")
    frame_count=family_frame_count(identity)
    out=OUT_ROOT/operation_id;common.need(not out.exists(),"MATERIAL_OPERATION_EXISTS")
    refs={"texture":ref(texture),"native_inventory":ref(inventory),"map_rule_proof":ref(rule_proof),
          "seam_auditor":ref(seam_auditor)}
    rows=assembly.geometry_rows(refs["native_inventory"])
    proof=source_batch.load_ref(refs["map_rule_proof"])
    editor_proof_ref=None;river_neighbor_model_ref=None
    if identity in ROAD_FAMILIES:
        common.need(proof.get("schema")=="h3.map-source-oracle.report.v1" and proof.get("status")=="PASS","MATERIAL_EDITOR_SOURCE_PROOF_REQUIRED")
        common.need(proof.get("native_reference_graph",{}).get("native_inventory",{}).get("sha256")==refs["native_inventory"]["sha256"],"MATERIAL_PROOF_INVENTORY_DIFFERS")
        editor_proof_ref=refs["map_rule_proof"]
    else:
        common.need(proof.get("schema")=="h3.map-tile-seam-diagnostic.v1","RIVER_NATIVE_NEIGHBOR_MODEL_REQUIRED")
        common.need(proof.get("native_inventory",{}).get("sha256")==refs["native_inventory"]["sha256"],"RIVER_NEIGHBOR_INVENTORY_DIFFERS")
        common.need(proof.get("native_confirmed_neighbor_pair_count",0)>0,"RIVER_NATIVE_NEIGHBOR_PAIRS_REQUIRED")
        river_neighbor_model_ref=refs["map_rule_proof"]
    retained=None;retained_ref=None
    if retain_frame is not None:
        common.need(0<=retain_frame<frame_count and retain_audit is not None and retain_audit.is_file(),"MATERIAL_RETAINED_MASTER_EVIDENCE_REQUIRED")
        retained_ref=ref(retain_audit)
        audit=source_batch.load_ref(retained_ref)
        target_key=["def",identity,0,retain_frame]
        audit_row=next((row for row in audit.get("source_rows",[]) if row.get("key")==target_key),None)
        common.need(audit_row and audit_row.get("decision")=="RESTORE_CANDIDATE","MATERIAL_RETAINED_MASTER_DID_NOT_PASS_SOURCE_AUDIT")
        retained=target_key
    material,phase=phase_lock_material(texture,phase_search_offset_xy);ratio_x=__import__("fractions").Fraction(material.shape[1],32)
    ratio_y=__import__("fractions").Fraction(material.shape[0],32)
    common.need(ratio_x==ratio_y and ratio_x>=3,"MATERIAL_GRID_MUST_MATCH_OFFICIAL_32X32_CANVAS")
    bank_material=None;bank_ref=None;bank_source_ref=None;bank_phase=None
    if bank_texture is not None:
        common.need(bank_width_native>=1 and bank_width_native<=8,"MATERIAL_BANK_WIDTH_INVALID")
        bank_material,bank_phase=phase_lock_material(bank_texture)
        common.need(bank_material.shape==material.shape,"MATERIAL_BANK_PARENT_SIZE_DIFFERS")
        bank_source_ref=ref(bank_texture)
    keys=[("def",identity,0,frame) for frame in range(frame_count)]
    common.need(set(keys)<=rows.keys(),"MATERIAL_OFFICIAL_FAMILY_SLOT_SET_REQUIRED")
    out.mkdir(parents=True,exist_ok=False)
    material_path=out/"material-parent-phase-locked.png"
    Image.fromarray(material,"RGB").save(material_path)
    material_ref=ref(material_path);phase["phase_parent_sha256"]=material_ref["sha256"]
    if bank_material is not None:
        bank_path=out/"bank-material-parent-phase-locked.png";Image.fromarray(bank_material,"RGB").save(bank_path)
        bank_ref=ref(bank_path);bank_phase["phase_parent_sha256"]=bank_ref["sha256"]
    contracts=[];items=[];source_rows=[]
    parents=out/"high-resolution-parents"/identity;parents.mkdir(parents=True)
    for key in keys:
        g=rows[key];geometry={"canvas_1x":g["canvas"],"crop_1x":g["crop"],"origin_1x":g["anchor"]}
        native_path=native_frame_root/identity/f"{key[2]}_{key[3]}.png"
        common.need(native_path.is_file(),"MATERIAL_NATIVE_ALPHA_MASK_MISSING: "+str(native_path))
        mask_raw=native_path.read_bytes();mask_sha=hashlib.sha256(mask_raw).hexdigest()
        with Image.open(native_path) as source_mask:
            source_mask.load();common.need(source_mask.size==tuple(g["canvas"]),"MATERIAL_NATIVE_ALPHA_MASK_SIZE_DIFFERS")
            alpha1=np.asarray(source_mask.convert("RGBA"),dtype=np.uint8)[:,:,3]
        ox,oy=g["anchor"];cw,ch=g["crop"]
        outcrop=(alpha1>0).copy();outcrop[oy:oy+ch,ox:ox+cw]=False
        common.need(not np.any(outcrop),"MATERIAL_NATIVE_MASK_HAS_ALPHA_OUTSIDE_OFFICIAL_CROP: "+str(key))
        if retained==list(key):
            parent=Path(master_root)/identity/f"{key[2]}_{key[3]}.png"
            common.need(parent.is_file() and sha_file(parent)==audit_row["source_sha256"],"MATERIAL_RETAINED_MASTER_SHA_DIFFERS")
            with Image.open(parent) as old:old.load();pixels=list(old.size)
            common.need(pixels[0]*geometry["canvas_1x"][1]==pixels[1]*geometry["canvas_1x"][0],"MATERIAL_RETAINED_MASTER_ASPECT_DIFFERS")
            ratio=__import__("fractions").Fraction(pixels[0],geometry["canvas_1x"][0])
            common.need(ratio>=3,"MATERIAL_RETAINED_MASTER_BELOW_3X")
            outcrop=source_batch.alpha_outside_crop(parent,ratio,geometry)
            common.need(outcrop["visible_pixels_alpha8_ge_16"]==0,"MATERIAL_RETAINED_MASTER_HAS_VISIBLE_OUTCROP")
            parent_ref=ref(parent);parent_operation="existing original parent retained by verified native boundary audit"
            ratio_for_item=[ratio.numerator,ratio.denominator]
        else:
            rgba=masked_material_parent(material,alpha1,geometry,bank_material,bank_width_native)
            parent=parents/f"{key[2]}_{key[3]}.png";Image.fromarray(rgba,"RGBA").save(parent)
            parent_ref=ref(parent);parent_operation="shared RGB material + rational-cell nearest alpha geometry; no RGB resize"
            ratio_for_item=[ratio_x.numerator,ratio_x.denominator]
        source_rows.append({"stem":f"{identity}_{key[2]}_{key[3]}","key":list(key),"source_status":"EXACT_GRID_CANDIDATE",
            "source":parent_ref,"texture_parent":material_ref,"texture_parent_original":refs["texture"],
            "bank_texture_parent":bank_ref,"bank_texture_parent_original":bank_source_ref,"bank_width_native":bank_width_native,
            "native_mask":{"path":str(native_path.resolve()),"sha256":mask_sha,"pixels":list(g["canvas"])},
            "geometry":geometry,"ratio":ratio_for_item,"retained_original":retained==list(key),
            "official_master_target":str((master_root/identity/f"{key[2]}_{key[3]}.png").resolve()),
            "parent_operation":parent_operation})
        contracts.append({"key":list(key),"targets":geometry,
                          "equivalence_class":g.get("equivalence_class") or hashlib.sha256(json.dumps(key).encode()).hexdigest()})
        layer=rational_native_roi_layer(parent_ref,list(key),geometry,ratio_for_item,
            [ox,oy,cw,ch])["layer"]
        items.append({"id":f"{identity}_{key[2]}_{key[3]}","key":list(key),"targets":geometry,
            "mode":"mesh","time":0,"layers":[layer],"quality":{"mode":"anchored-surface"},
            "sampling_budget":{"schema":"h3.motion.sampling-budget.v1","max_loop_visits":1<<28}})
    native_contract=out/"native-contract.json"
    write_new(native_contract,{"schema":"h3.map-material-batch.native-contract.v1","entries":contracts,
        "source_inventory":refs["native_inventory"]})
    native_ref=ref(native_contract);batch_root=out/"batches";batch_root.mkdir();specs=[]
    for index in range(0,len(items),2):
        batch_id=f"batch-{index//2:03d}";batch_dir=batch_root/batch_id;batch_dir.mkdir()
        spec={"schema":"h3.motion.spec.v1","output_root":str((batch_dir/"candidate3x").resolve()),
              "native_contracts":[native_ref],"target_scale":3,
              "settings":{"backend":"cpu","space":"linear","filter":"h3-quality","workers":2,"memory_gib":4},
              "items":items[index:index+2]}
        spec_path=batch_dir/"motion-spec.json";write_new(spec_path,spec)
        specs.append({"id":batch_id,"item_count":len(spec["items"]),"spec":ref(spec_path)})
    request={"schema":SCHEMA,"operation_id":operation_id,"identity":identity,"frame_count":frame_count,
        "source_count":frame_count,"exact_grid_candidates":frame_count,"redraw_required_at_source_preflight":0,
        "native_inventory":refs["native_inventory"],"map_rule_proof":refs["map_rule_proof"],
        "editor_source_proof":editor_proof_ref,"river_native_neighbor_model":river_neighbor_model_ref,
        "seam_auditor":refs["seam_auditor"],"release_root":str(release_root.resolve()),
        "native_frame_root":str(native_frame_root.resolve()),"master_root":str(master_root.resolve()),
        "texture_source":refs["texture"],"material_parent":material_ref,"material_phase_lock":phase,
        "bank_texture_source":bank_source_ref,"bank_material_parent":bank_ref,"bank_phase_lock":bank_phase,
        "bank_width_native":bank_width_native,
        "retained_source_audit":retained_ref,"retained_frame":retain_frame,
        "native_contract":native_ref,"motion_specs":specs,"source_rows":source_rows,
        "art_approved":False,"game_runtime_accepted":False,"game_deployed":False}
    request_path=out/"request.json";write_new(request_path,request)
    return {"status":"FROZEN","request":ref(request_path),"frames":frame_count,"motion_batches":len(specs),
            "phase_lock":phase,"images_written":0}


def color_seam_report(root:Path,request:dict,seam:dict)->dict:
    native=assembly.geometry_rows(request["native_inventory"])
    rows={tuple(x["key"]):x for x in request["source_rows"]};arrays={};internal=[]
    for row in seam["frames"]:
        key=tuple(row["key"]);g=native[key]
        sample={"key":list(key),"image":ref(Path(row["candidate_path"]))}
        rgba=assembly.full_crop(sample["image"],g)
        arrays[key]=rgba
        a=rgba[:,:,3]>=128
        horizontal=a[:,:-1]&a[:,1:]
        vertical=a[:-1,:]&a[1:,:]
        if horizontal.any():internal.extend(np.abs(rgba[:,:-1,:3].astype(np.int16)-rgba[:,1:,:3].astype(np.int16))[horizontal].mean(axis=1).tolist())
        if vertical.any():internal.extend(np.abs(rgba[:-1,:,:3].astype(np.int16)-rgba[1:,:,:3].astype(np.int16))[vertical].mean(axis=1).tolist())
    baseline=float(np.percentile(np.asarray(internal,dtype=np.float32),95)) if internal else 0.0
    pairs=[]
    for p in seam.get("candidate_neighbor_checks",[]):
        ka=tuple(p["frame_a"]);kb=tuple(p["frame_b"])
        if ka not in arrays or kb not in arrays:continue
        a=assembly.transform(arrays[ka],FLIP_BITS[p["flip_a"]],0)
        b=assembly.transform(arrays[kb],FLIP_BITS[p["flip_b"]],0)
        side,opposite=p["side_a"],p["side_b"]
        ea={"top":a[0],"bottom":a[-1],"left":a[:,0],"right":a[:,-1]}[side]
        eb={"top":b[0],"bottom":b[-1],"left":b[:,0],"right":b[:,-1]}[opposite]
        valid=(ea[:,3]>=128)&(eb[:,3]>=128)
        mean=float(np.abs(ea[valid,:3].astype(np.int16)-eb[valid,:3].astype(np.int16)).mean()) if valid.any() else None
        pairs.append({"frame_a":p["frame_a"],"flip_a":p["flip_a"],"side_a":side,
            "frame_b":p["frame_b"],"flip_b":p["flip_b"],"side_b":opposite,
            "overlap_pixels":int(valid.sum()),"opaque_rgb_mean_delta":mean,
            "internal_rgb_p95":baseline,"status":"PASS" if mean is not None and mean<=baseline else "FAIL"})
    return {"schema":"h3.map-material-batch.rgb-seams.v1","identity":request["identity"],
        "method":"compare opposite opaque RGB samples against the same candidate family's internal neighboring-pixel p95",
        "alpha_threshold":128,"internal_rgb_p95":baseline,"legal_pairs":len(pairs),
        "failed_pairs":sum(p["status"]=="FAIL" for p in pairs),"pairs":pairs,
        "source_texture_phase":request["material_phase_lock"],"bank_texture_phase":request.get("bank_phase_lock"),
        "bank_width_native":request.get("bank_width_native",0),"automatic_art_approval":False}


def main(argv=None)->int:
    raw_argv=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest="action",required=True)
    f=subs.add_parser("freeze");f.add_argument("--operation-id",required=True);f.add_argument("--identity",required=True)
    f.add_argument("--texture",required=True);f.add_argument("--inventory",required=True);f.add_argument("--native-frame-root",required=True)
    f.add_argument("--rule-proof",required=True);f.add_argument("--seam-auditor",required=True)
    f.add_argument("--release-root",required=True);f.add_argument("--master-root",required=True)
    f.add_argument("--retain-frame",type=int);f.add_argument("--retain-audit");f.add_argument("--retain-audit-sha256")
    f.add_argument("--bank-texture");f.add_argument("--bank-width-native",type=int,default=0)
    f.add_argument("--phase-search-offset-x",type=int,default=0);f.add_argument("--phase-search-offset-y",type=int,default=0)
    for action in ("check","run","verify","restore"):
        p=subs.add_parser(action);p.add_argument("--manifest",required=True);p.add_argument("--manifest-sha256",required=True)
        p.add_argument("--operation-id",required=True)
        p.add_argument("--renderer",required=action!="restore");p.add_argument("--renderer-sha256",required=action!="restore")
    # The companion injects renderer identity even when a task stage is omitted.
    # Preserve the CLI's normal usage error instead of treating the renderer
    # path as an invalid action.
    if raw_argv and raw_argv[0]=="--renderer":
        parser.print_usage(file=sys.stderr)
        print(json.dumps({"schema":"h3.map-material-batch.error.v1","status":"BLOCKED",
            "error":"action is required: freeze, check, run, verify, or restore"},ensure_ascii=False),file=sys.stderr)
        return 2
    args=parser.parse_args(raw_argv)
    try:
        # Native-pixel masks expanded over HQ texture produce visibly blocky
        # silhouettes and are processed candidates, not original model masters.
        # In particular, an old FAMILY_PASS receipt must never bypass this gate.
        if args.action=="restore":
            raise ValueError("MATERIAL_PROCESSED_MASTER_RESTORE_DISABLED: native-mask candidates require visual review and must not be written as original masters")
        if args.action=="freeze":
            common.need((args.retain_frame is None and args.retain_audit is None and args.retain_audit_sha256 is None) or
                        (args.retain_frame is not None and args.retain_audit and args.retain_audit_sha256),
                        "MATERIAL_RETAINED_MASTER_BINDING_INCOMPLETE")
            retained_audit=Path(args.retain_audit) if args.retain_audit else None
            if retained_audit:common.pin({"path":str(retained_audit),"sha256":args.retain_audit_sha256})
            common.need((args.bank_texture is None and args.bank_width_native==0) or
                        (args.bank_texture is not None and 1<=args.bank_width_native<=8),"MATERIAL_BANK_TEXTURE_BINDING_INCOMPLETE")
            result=freeze(args.operation_id,args.identity,Path(args.texture),Path(args.inventory),Path(args.native_frame_root),
                Path(args.rule_proof),Path(args.seam_auditor),Path(args.release_root),Path(args.master_root),
                args.retain_frame,retained_audit,Path(args.bank_texture) if args.bank_texture else None,args.bank_width_native,
                (args.phase_search_offset_x,args.phase_search_offset_y))
            print(json.dumps(result,ensure_ascii=False));return 0
        request_ref={"path":args.manifest,"sha256":args.manifest_sha256}
        root,request=pin_material_request(request_ref,args.operation_id)
        if args.action=="check":
            result=source_batch.stage_check(root,request,source_batch.invoking_exe(args))
        elif args.action=="run":
            source_batch.stage_check(root,request,source_batch.invoking_exe(args))
            runs,verifies,run_refs,verify_refs,run,verify=source_batch.collect_batch_runs(root,request,source_batch.invoking_exe(args))
            seam_path=root/"seam-audit.json";seam=json.loads(seam_path.read_text(encoding="utf-8-sig")) if seam_path.exists() else source_batch.run_auditor(root,request,seam_path)
            road=None;road_ref=None
            if request["identity"] in ROAD_FAMILIES:
                road_spec={"schema":road_quality.SCHEMA,"operation_id":request["operation_id"],"road_families":[request["identity"]],
                    "native_inventory":request["native_inventory"],"editor_source_proof":request["editor_source_proof"],"sampling_runs":run_refs}
                road_spec=road_quality.freeze(road_spec);road=road_quality.analyze(road_spec)
                road_path=root/"road-batch-report.json"
                if road_path.exists():common.need(json.loads(road_path.read_bytes())==road,"MATERIAL_ROAD_REPORT_REPLAY_DIFFERS")
                else:source_batch.write_new(road_path,road)
                gallery=road_quality.gallery(road_spec,road);gallery_path=root/"road-material-review.html"
                if not gallery_path.exists():
                    with gallery_path.open("x",encoding="utf-8") as stream:stream.write(gallery)
                road_ref=ref(road_path)
            if road is None:road={}
            rgb=color_seam_report(root,request,seam)
            decisions=source_batch.candidate_decisions(request,seam,run,verify,road)
            for row in decisions:
                if rgb["failed_pairs"]:row["decision"]="REDRAW_REQUIRED";row["reason"]=(row.get("reason") or [])+["SHARED_MATERIAL_RGB_SEAM_FAIL"]
            report={"schema":REPORT,"operation_id":request["operation_id"],"identity":request["identity"],
                "status":"GEOMETRY_PASS_VISUAL_REVIEW_REQUIRED" if sum(x["decision"]=="RESTORE_CANDIDATE" for x in decisions)==request["frame_count"] else "PILOT_NEEDS_REWORK",
                "request":ref(root/"request.json"),"seam_audit":ref(seam_path),"road_batch_report":road_ref,
                "rgb_seams":rgb,"frames":decisions,"restore_candidates":sum(x["decision"]=="RESTORE_CANDIDATE" for x in decisions),
                "redraw_required":sum(x["decision"]=="REDRAW_REQUIRED" for x in decisions),
                "review_required":sum(x["decision"]=="REVIEW_REQUIRED" for x in decisions),
                "art_approved":False,"game_runtime_accepted":False,"game_deployed":False}
            path=root/"map-material-batch-report.json"
            if path.exists():common.need(json.loads(path.read_bytes())==report,"MATERIAL_REPORT_REPLAY_DIFFERS")
            else:source_batch.write_new(path,report)
            result={"status":report["status"],"report":ref(path),"restore_candidates":report["restore_candidates"],
                    "redraw_required":report["redraw_required"],"review_required":report["review_required"],"rgb_failed_pairs":rgb["failed_pairs"]}
        elif args.action=="verify":
            report_path=root/"map-material-batch-report.json";common.need(report_path.is_file(),"MATERIAL_RUN_FIRST")
            report=json.loads(report_path.read_bytes());exe=source_batch.invoking_exe(args)
            runs,verifies,run_refs,verify_refs,run,verify=source_batch.collect_batch_runs(root,request,exe,"verify-motion.json")
            seam_path=root/"verify-seam-audit.json"
            seam=json.loads(seam_path.read_text(encoding="utf-8-sig")) if seam_path.exists() else source_batch.run_auditor(root,request,seam_path)
            road={}
            if request["identity"] in ROAD_FAMILIES:
                road_spec={"schema":road_quality.SCHEMA,"operation_id":request["operation_id"],"road_families":[request["identity"]],
                    "native_inventory":request["native_inventory"],"editor_source_proof":request["editor_source_proof"],"sampling_runs":run_refs}
                road=road_quality.analyze(road_quality.freeze(road_spec))
            rgb=color_seam_report(root,request,seam)
            decisions=source_batch.candidate_decisions(request,seam,run,verify,road)
            for row in decisions:
                if rgb["failed_pairs"]:row["decision"]="REDRAW_REQUIRED";row["reason"]=(row.get("reason") or [])+["SHARED_MATERIAL_RGB_SEAM_FAIL"]
            prior_road=report.get("road_batch_report")
            if prior_road:
                if prior_road.get("path"):prior_road=json.loads(Path(prior_road["path"]).read_bytes())
                common.need(road==prior_road,"MATERIAL_ROAD_REPORT_REPLAY_DIFFERS")
            common.need(rgb==report["rgb_seams"] and decisions==report["frames"],"MATERIAL_QUALITY_REPLAY_DIFFERS")
            for frame in seam.get("frames",[]):
                p=Path(frame["candidate_path"]);common.need(p.is_file() and sha_file(p)==frame["candidate_sha256"],"MATERIAL_3X_CANDIDATE_SHA_DRIFT")
            receipt={"schema":"h3.map-material-batch.verify.v1","operation_id":request["operation_id"],
                "status":"VERIFIED","report":ref(report_path),"motion_verifications":verify_refs,
                "seam_audit":ref(seam_path),"road_geometry_replayed":True,"shared_rgb_seams_replayed":len(rgb["pairs"]),
                "art_approved":False,"game_runtime_accepted":False,"images_written":0}
            receipt_path=root/"verification.json"
            if receipt_path.exists():common.need(json.loads(receipt_path.read_bytes())==receipt,"MATERIAL_VERIFY_REPLAY_DIFFERS")
            else:write_new(receipt_path,receipt)
            result={"status":"VERIFIED","verification":ref(receipt_path),"images_written":0}
        else:
            report_path=root/"map-material-batch-report.json";verification_path=root/"verification.json"
            common.need(report_path.is_file() and verification_path.is_file(),"MATERIAL_VERIFY_BEFORE_RESTORE")
            report=json.loads(report_path.read_bytes());common.need(report["status"]=="FAMILY_PASS" and report["restore_candidates"]==request["frame_count"],
                "MATERIAL_FAMILY_NOT_FULLY_QUALIFIED")
            common.need(json.loads(verification_path.read_bytes()).get("status")=="VERIFIED","MATERIAL_VERIFICATION_REQUIRED")
            targets=[]
            for row in report["frames"]:
                source=Path(next(x["source"]["path"] for x in request["source_rows"] if x["key"]==row["key"]))
                target=Path(request["master_root"])/request["identity"]/f"{row['key'][2]}_{row['key'][3]}.png"
                raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest();target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():common.need(sha_file(target)==digest,"MATERIAL_MASTER_CONFLICT_NOT_OVERWRITTEN")
                else:
                    with target.open("xb") as stream:stream.write(raw)
                targets.append({"key":row["key"],"path":str(target.resolve()),"sha256":sha_file(target)})
            receipt={"schema":"h3.map-material-batch.restore.v1","operation_id":request["operation_id"],
                "identity":request["identity"],"material_parent":request["material_parent"],"masters":targets,
                "count":len(targets),"source_resampled":False,"game_deployed":False}
            write_new(root/"restore-receipt.json",receipt);result={"status":"RESTORED","count":len(targets),"receipt":ref(root/"restore-receipt.json")}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,OSError,TypeError,subprocess.TimeoutExpired) as error:
        print(json.dumps({"schema":"h3.map-material-batch.error.v1","status":"BLOCKED","error":str(error)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
