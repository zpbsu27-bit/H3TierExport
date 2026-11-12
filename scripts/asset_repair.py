"""Versioned reusable local HQ asset-repair operators. Never approve art or deploy.

Source RGB always comes from pinned HQ paintings. Native masks are geometry
data. Exact3X export remains a separate qualified native tool operation.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
from datetime import datetime,timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import numpy as np
from PIL import Image,ImageFilter

SCALE=32
PRODUCT=resolve_tool_root(__file__)
MANIFEST_SCHEMA='h3.asset-repair.manifest.v1'
RECEIPT_SCHEMA='h3.asset-repair.receipt.v1'
OPERATORS={'shared-interface-collar':1,'registered-parent-reslice':1,'registered-alpha':1,'masked-color-variants':1,'raw-player-flag-variants':1,'locked-rgb-flow':1,'native-canvas-assembly':1,
    'material-lab-harmonize':1,'locked-static-base':1,'bounded-key-decontaminate':1,'material-label-diagnostic':1,'coordinate-state-effects':1,
    'registered-contact-transfer':1,'registered-flow-phases':1,'registered-hq-canvas':1,'registered-terrain-family':1}

def restore(crop,geometry,scale):
    out=np.zeros((geometry['canvas_1x'][1]*scale,geometry['canvas_1x'][0]*scale,4),dtype=np.uint8)
    x,y=geometry['origin_1x'];w,h=geometry['crop_1x']
    if crop.shape!=(h*scale,w*scale,4):raise ValueError('Exact crop geometry mismatch')
    out[y*scale:(y+h)*scale,x*scale:(x+w)*scale]=crop
    return out

def linear(rgb):
    v=rgb.astype(np.float32)/255
    return np.where(v<=.04045,v/12.92,((v+.055)/1.055)**2.4)

def encode(rgb):
    v=np.clip(rgb,0,1)
    return np.rint(np.where(v<=.0031308,v*12.92,1.055*v**(1/2.4)-.055)*255).astype(np.uint8)

def canonical_strip(rgba,depth=3*SCALE,samples=512):
    """Sample a real painted straight-road interior; symmetrize only the collar.

    Symmetry makes runtime flips share the same boundary material. The image
    body remains the original painting; no native RGB is read by this function.
    """
    h,w=rgba.shape[:2];strip=[]
    for distance in range(depth):
        y=min(h-1,h//2+distance)
        xs=np.flatnonzero(rgba[y,:,3]>=200)
        if len(xs)<w*.1:raise ValueError('Canonical straight-road painting has no stable interior')
        lo,hi=int(xs[0]),int(xs[-1]);q=np.linspace(lo,hi,samples)
        row=linear(rgba[y,:,:3]);a=np.floor(q).astype(int);b=np.minimum(a+1,w-1);f=(q-a)[:,None]
        values=row[a]*(1-f)+row[b]*f
        strip.append((values+values[::-1])*.5)
    return np.asarray(strip,dtype=np.float32)

def repair_collars(rgba,native_alpha,strip,scale=SCALE,depth_native=3,shared_native=1,controlled_sides=None):
    if rgba.shape[:2]!=(native_alpha.shape[0]*scale,native_alpha.shape[1]*scale):raise ValueError('HQ geometry differs')
    alpha=rgba[:,:,3].astype(np.float32)/255;pre=linear(rgba[:,:,:3])*alpha[:,:,None]
    logical=np.repeat(np.repeat(native_alpha>=250,scale,axis=0),scale,axis=1)
    height,width=alpha.shape;depth=depth_native*scale;shared=shared_native*scale
    touched=np.zeros((height,width),dtype=bool)
    profiles={'top':native_alpha[0,:],'bottom':native_alpha[-1,:],'left':native_alpha[:,0],'right':native_alpha[:,-1]}
    for side,profile in profiles.items():
        if controlled_sides is not None and side not in controlled_sides:continue
        on=np.flatnonzero(profile>=250);lo=int(on[0])*scale if len(on) else 0;hi=int(on[-1]+1)*scale if len(on) else 0
        length=width if side in ('top','bottom') else height
        u=np.clip((np.arange(length)+.5-lo)/max(1,hi-lo),0,1)*(strip.shape[1]-1)
        a=np.floor(u).astype(int);b=np.minimum(a+1,strip.shape[1]-1);f=(u-a)[:,None]
        for distance in range(depth):
            ramp=np.clip((distance-shared)/max(1,depth-shared-1),0,1);weight=1-ramp*ramp*(3-2*ramp)
            material=strip[min(distance,len(strip)-1),a]*(1-f)+strip[min(distance,len(strip)-1),b]*f
            if side=='top':idx=(distance,slice(None))
            elif side=='bottom':idx=(height-1-distance,slice(None))
            elif side=='left':idx=(slice(None),distance)
            else:idx=(slice(None),width-1-distance)
            touched[idx]=True
            target=logical[idx].astype(np.float32)
            pre[idx]=pre[idx]*(1-weight)+material*target[:,None]*weight
            alpha[idx]=alpha[idx]*(1-weight)+target*weight
    rgb=np.divide(pre,alpha[:,:,None],out=np.zeros_like(pre),where=alpha[:,:,None]>1e-8)
    result=rgba.copy()
    result[touched]=np.dstack([encode(rgb),np.rint(alpha*255).astype(np.uint8)])[touched]
    return result


def digest(raw):return hashlib.sha256(raw).hexdigest()


def need(value,code):
    if not value:raise ValueError(code)


def plain_path(value):
    need(isinstance(value,str) and Path(value).is_absolute(),'ABSOLUTE_PATH_REQUIRED')
    p=Path(value)
    for q in (p,*p.parents):
        if q.exists():
            st=q.lstat();need(not q.is_symlink() and not getattr(st,'st_file_attributes',0)&0x400,'REPARSE_PATH_BLOCKED')
    return p.resolve()


def pin(ref):
    need(isinstance(ref,dict) and isinstance(ref.get('sha256'),str) and re.fullmatch('[0-9a-f]{64}',ref['sha256']),'PIN_REQUIRED')
    p=plain_path(ref['path']);need(p.is_file(),'PINNED_FILE_MISSING')
    before=p.stat();raw=p.read_bytes();after=p.stat()
    need(before.st_mtime_ns==after.st_mtime_ns and before.st_size==after.st_size and digest(raw)==ref['sha256'],'INPUT_DRIFT')
    need(len(raw)<=256*1024*1024,'INPUT_SIZE_LIMIT')
    return p,raw


def image(ref):
    _,raw=pin(ref)
    with Image.open(io.BytesIO(raw)) as im:
        need(im.format=='PNG' and im.width*im.height<=64*1024*1024,'PNG_CAPACITY_INVALID')
        need(list(im.size)==ref.get('pixels'),'IMAGE_PIXEL_PIN_REQUIRED')
        return np.array(im.convert('RGBA'))


def geometry(value):
    need(isinstance(value,dict) and set(value)=={'canvas_1x','crop_1x','origin_1x'},'NATIVE_GEOMETRY_FIELDS_INVALID')
    for name,pair in value.items():
        need(isinstance(pair,list) and len(pair)==2 and all(type(x) is int and x>=(0 if name=='origin_1x' else 1) for x in pair),'NATIVE_GEOMETRY_INVALID')
    cw,ch=value['canvas_1x'];w,h=value['crop_1x'];x,y=value['origin_1x']
    need(x+w<=cw and y+h<=ch,'NATIVE_CROP_OUTSIDE_CANVAS')
    return value


def registered(job,field='source',delivery=False):
    value=job[field];rgba=image(value['image']);g=geometry(value['geometry']);scale=value['editing_scale']
    need(type(scale) is int and ((scale==3) if delivery else (4<=scale<=32)),'HQ_EDITING_SCALE_REQUIRED_3X_IS_NOT_A_MASTER')
    _,raw=pin(value['receipt']);receipt=json.loads(raw)
    if receipt.get('schema')==RECEIPT_SCHEMA:
        matches=[x for x in receipt['outputs'] if x['sha256']==value['image']['sha256'] and x['pixels']==value['image']['pixels']
            and plain_path(x['path'])==plain_path(value['image']['path'])]
        need(len(matches)==1,'REPAIR_PARENT_OUTPUT_BINDING_AMBIGUOUS');entry=matches[0]
        need(entry['geometry']==g and entry['editing_scale']==scale,'REPAIR_PARENT_GEOMETRY_MISMATCH')
        if field=='source':need(entry['native_key']==job['native_key'],'REPAIR_PARENT_SLOT_MISMATCH')
        for source in receipt['inputs']:pin(source)
        if entry['layout']=='full-canvas':
            need([rgba.shape[1],rgba.shape[0]]==[x*scale for x in g['canvas_1x']],'REPAIR_PARENT_CANVAS_MISMATCH')
            return rgba,g,scale
        need(entry['layout']=='native-crop','REPAIR_PARENT_LAYOUT_INVALID')
        return restore(rgba,g,scale),g,scale
    need(receipt.get('schema')=='h3.motion.receipt.v1' and receipt.get('target_scale')==scale,'REGISTERED_HQ_TOOL_RECEIPT_REQUIRED')
    need(receipt.get('png_sha256')==value['image']['sha256'] and receipt.get('output_pixels')==value['image']['pixels'],'REGISTERED_RECEIPT_IMAGE_MISMATCH')
    contract=receipt['contract'];native=contract['native_entry']
    origin_field='origin_3x' if scale==3 else 'origin_at_target_scale'
    need(native['targets']==g and receipt[origin_field]==[x*scale for x in g['origin_1x']],'REGISTERED_NATIVE_GEOMETRY_MISMATCH')
    if field=='source':need(native['key']==job['native_key'],'REGISTERED_SLOT_KEY_MISMATCH')
    # These are real upstream painted sources, not the original native mask.
    sources=contract['entry'].get('layers',[])
    need(bool(sources),'REGISTERED_PAINTING_SOURCE_CHAIN_MISSING')
    for layer in sources:
        for name in ('source_a','source_b'):
            if name not in layer:continue
            source=layer[name];p,_=pin(source)
            need('codex-3x' not in str(p).lower() and 'candidate3x' not in str(p).lower(),'LOW_RESOLUTION_DERIVATIVE_NOT_PAINTING_PARENT')
            with Image.open(p) as im:need(list(im.size)==source['pixels'],'UPSTREAM_PAINTING_PIXELS_CHANGED')
    return restore(rgba,g,scale),g,scale


def logical_mask(ref,g,key):
    need(ref.get('data_role')=='native-logical-alpha-geometry','MASK_DATA_ROLE_REQUIRED')
    need(ref.get('native_key')==key and ref.get('native_geometry')==g,'NATIVE_MASK_SLOT_BINDING_MISMATCH')
    rgba=image(ref);need([rgba.shape[1],rgba.shape[0]]==g['canvas_1x'],'NATIVE_MASK_CANVAS_MISMATCH')
    alpha=rgba[:,:,3];need(digest(alpha.tobytes())==ref.get('alpha_sha256'),'NATIVE_ALPHA_PLANE_PIN_REQUIRED')
    x,y=g['origin_1x'];w,h=g['crop_1x'];outside=alpha.copy();outside[y:y+h,x:x+w]=0
    need(not np.any(outside),'NATIVE_MASK_OUTSIDE_CROP')
    return alpha


def selection_mask(ref,shape):
    need(ref.get('data_role')=='selection-weight-mask','SELECTION_MASK_ROLE_REQUIRED')
    rgba=image(ref);need(rgba.shape[:2]==shape,'SELECTION_MASK_GEOMETRY_MISMATCH')
    return rgba[:,:,0].astype(np.float32)/255*rgba[:,:,3].astype(np.float32)/255


def cie_lab(rgb):
    """D65 CIE Lab for a bounded color adjustment, never a geometry validator."""
    xyz=linear(rgb)@np.array([[.4124564,.3575761,.1804375],[.2126729,.7151522,.0721750],[.0193339,.1191920,.9503041]],np.float32).T
    xyz/=np.array([.95047,1,1.08883],np.float32)
    f=np.where(xyz>.008856,np.cbrt(np.maximum(xyz,0)),(903.3*xyz+16)/116)
    return np.stack([116*f[...,1]-16,500*(f[...,0]-f[...,1]),200*(f[...,1]-f[...,2])],axis=-1)


def from_cie_lab(lab):
    L,a,b=np.moveaxis(lab,-1,0);fy=(L+16)/116
    f=np.stack([fy+a/500,fy,fy-b/200],axis=-1)
    xyz=np.where(f**3>.008856,f**3,(116*f-16)/903.3)*np.array([.95047,1,1.08883],np.float32)
    rgb=xyz@np.array([[3.2404542,-1.5371385,-.4985314],[-.9692660,1.8760108,.0415560],[.0556434,-.2040259,1.0572252]],np.float32).T
    return encode(rgb)


def color_statistics(rgba,mask):
    need(np.isfinite(mask).all() and mask.shape==rgba.shape[:2],'COLOR_MASK_INVALID')
    active=(mask>=.5)&(rgba[:,:,3]>=200)
    need(int(active.sum())>=16,'COLOR_REFERENCE_REGION_TOO_SMALL')
    return cie_lab(rgba[:,:,:3])[active].mean(axis=0)


def bounded_lab_shift(rgba,mask,target,max_lightness=4,max_chroma=4):
    """Adopt damped Lab alignment, retaining texture and an explicit material mask.

    A large palette difference is residual evidence, never an automatic redraw.
    Center decorations, natural shadows and other materials need not be flattened.
    """
    need(np.isfinite([*target,max_lightness,max_chroma]).all() and 0<=max_lightness<=6 and 0<=max_chroma<=8,'COLOR_SHIFT_BOUNDS_INVALID')
    current=color_statistics(rgba,mask);delta=(np.asarray(target)-current)*.7
    delta[0]=np.clip(delta[0],-max_lightness,max_lightness)
    length=float(np.linalg.norm(delta[1:]));delta[1:]*=min(1,max_chroma/max(length,1e-9))
    lab=cie_lab(rgba[:,:,:3]);shifted=lab+mask[:,:,None]*delta
    inside=mask>0;out=rgba.copy();out[inside,:3]=from_cie_lab(shifted)[inside]
    need(np.array_equal(out[:,:,3],rgba[:,:,3]) and np.array_equal(out[~inside],rgba[~inside]),'HARMONIZATION_STATIC_PIXELS_CHANGED')
    return out,{'source_region_lab_mean':current.tolist(),'target_region_lab_mean':np.asarray(target).tolist(),
        'applied_lab_shift':delta.tolist(),'residual_mean_delta_lab':(np.asarray(target)-color_statistics(out,mask)).tolist(),
        'alpha_exact':True,'outside_material_mask_exact':True,'texture_geometry_resampled':False,
        'legal_terrain_adjacency_verified':False,'redraw_decision_made':False}


def neighbors(values):
    """Four-neighbor views with a constant exterior, never np.roll wraparound."""
    pad=((1,1),(1,1))+((0,0),)*(values.ndim-2)
    p=np.pad(values,pad,mode='constant')
    return (p[:-2,1:-1],p[2:,1:-1],p[1:-1,:-2],p[1:-1,2:])


def border_connected(mask):
    from collections import deque
    result=np.zeros_like(mask,dtype=bool);h,w=mask.shape;pending=deque()
    for y,x in [(0,x) for x in range(w)]+[(h-1,x) for x in range(w)]+[(y,0) for y in range(h)]+[(y,w-1) for y in range(h)]:
        if mask[y,x] and not result[y,x]:result[y,x]=True;pending.append((y,x))
    while pending:
        y,x=pending.popleft()
        for yy,xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
            if 0<=yy<h and 0<=xx<w and mask[yy,xx] and not result[yy,xx]:result[yy,xx]=True;pending.append((yy,xx))
    return result


def seeded_component(mask,seeds):
    """Four-connected part of a binary mask touched by explicit seed pixels."""
    from collections import deque
    h,w=mask.shape;result=(mask&seeds).copy();pending=deque(zip(*np.where(result)))
    while pending:
        y,x=pending.popleft()
        for yy,xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
            if 0<=yy<h and 0<=xx<w and mask[yy,xx] and not result[yy,xx]:
                result[yy,xx]=True;pending.append((yy,xx))
    return result


def decontaminate_selected_key(rgba,selection,key_rgb,mode='fringe-rgb',radius=3,tolerance=64,allow_seeded_edge_component=False):
    need(type(radius) is int and 1<=radius<=8 and 0<=tolerance<=128,'KEY_DECONTAMINATION_BOUNDS_INVALID')
    key=np.asarray(key_rgb)
    need(key.shape==(3,) and np.isfinite(key).all() and ((key>=0)&(key<=255)).all(),'BACKGROUND_KEY_RGB_INVALID')
    chosen=(selection>0)&(rgba[:,:,3]>0)
    need(bool(chosen.any()),'EXPLICIT_NONEMPTY_POLLUTION_MASK_REQUIRED')
    matches=np.max(np.abs(rgba[:,:,:3].astype(float)-key),axis=-1)<=tolerance
    need(not np.any(chosen&~matches),'POLLUTION_MASK_CONTAINS_NONKEY_MATERIAL')
    out=rgba.copy()
    if mode=='exterior-key':
        exterior=border_connected(chosen&matches)
        need(np.array_equal(exterior,chosen),'KEY_MASK_INCLUDES_DISCONNECTED_SUBJECT_INTERIOR')
        out[exterior,3]=0
        return out,{'selected_pixels':int(chosen.sum()),'exterior_connectivity_verified':True,'subject_RGB_unchanged':True,'unselected_RGBA_exact':True}
    need(mode=='fringe-rgb','KEY_DECONTAMINATION_MODE_INVALID')
    exterior=border_connected(rgba[:,:,3]==0);ring=exterior.copy()
    for _ in range(radius):ring|=np.logical_or.reduce(neighbors(ring))
    need(allow_seeded_edge_component or not np.any(chosen&~ring),'POLLUTION_MASK_REACHES_SUBJECT_INTERIOR')
    clean=(rgba[:,:,3]>=180)&~chosen;colors=linear(rgba[:,:,:3]);recovered=np.zeros_like(chosen)
    # Simultaneous wavefronts prevent scan-order propagation and opposite-edge borrowing.
    for _ in range(radius+1):
        count=np.zeros(chosen.shape,np.float32);total=np.zeros_like(colors)
        for valid,sample in zip(neighbors(clean),neighbors(colors)):
            count+=valid;total+=sample*valid[:,:,None]
        fresh=chosen&~clean&(count>0)
        colors[fresh]=total[fresh]/count[fresh,None];clean[fresh]=True;recovered|=fresh
    need(np.array_equal(recovered,chosen),'NO_LOCAL_CLEAN_PAINT_DONOR_FOR_KEY_FRINGE')
    out[chosen,:3]=encode(colors)[chosen]
    need(np.array_equal(out[:,:,3],rgba[:,:,3]) and np.array_equal(out[~chosen],rgba[~chosen]),'KEY_REPAIR_OUTSIDE_EXPLICIT_MASK')
    return out,{'selected_pixels':int(chosen.sum()),'alpha_exact':True,'unselected_RGBA_exact':True,
        'color_alone_classified_as_contamination':False,'opposite_image_edges_wrap':False}


def decontaminate_transparent_edge_key(rgba,key_rgb,radius=4,tolerance=64):
    """Remove a pinned color fringe only within a measured transparent-edge band."""
    need(rgba.ndim==3 and rgba.shape[2]==4 and rgba.dtype==np.uint8,'RAW_SOURCE_RGBA_REQUIRED')
    need(type(radius) is int and 1<=radius<=8 and type(tolerance) is int and 0<=tolerance<=128,
         'KEY_DECONTAMINATION_BOUNDS_INVALID')
    key=np.asarray(key_rgb);need(key.shape==(3,) and np.isfinite(key).all() and ((key>=0)&(key<=255)).all(),
        'BACKGROUND_KEY_RGB_INVALID')
    exterior=border_connected(rgba[:,:,3]==0);band=exterior.copy()
    for _ in range(radius):band|=np.logical_or.reduce(neighbors(band))
    matches=np.max(np.abs(rgba[:,:,:3].astype(np.int16)-key.astype(np.int16)),axis=-1)<=tolerance
    selected=band&matches&(rgba[:,:,3]>0)
    need(selected.any(),'NO_PINNED_KEY_PIXELS_IN_TRANSPARENT_EDGE_BAND')
    need(not np.any(selected&(rgba[:,:,3]>=128)),'KEY_COLOR_REACHES_OPAQUE_SUBJECT_EDGE')
    # Use all low-alpha pixels in the transparent-edge band as propagation
    # paths. Their RGB may already be contaminated; only opaque non-key paint
    # can donate a color. Unresolved matching pixels remain byte exact.
    candidate=band&(rgba[:,:,3]>0)&(rgba[:,:,3]<180)
    donors=(rgba[:,:,3]>=180)&~matches;fixed=donors.copy();colors=linear(rgba[:,:,:3])
    for _ in range(radius+1):
        count=np.zeros(selected.shape,np.float32);total=np.zeros_like(colors)
        for valid,sample in zip(neighbors(fixed),neighbors(colors)):
            count+=valid;total+=sample*valid[:,:,None]
        fresh=candidate&~fixed&(count>0)
        if not fresh.any():break
        colors[fresh]=total[fresh]/count[fresh,None];fixed[fresh]=True
    eligible=selected&fixed;excluded=selected&~fixed
    need(eligible.any(),'NO_SAFE_LOCAL_PAINT_DONOR_FOR_KEY_FRINGE')
    result=rgba.copy();result[eligible,:3]=encode(colors)[eligible]
    need(np.array_equal(result[:,:,3],rgba[:,:,3]) and np.array_equal(result[~eligible],rgba[~eligible]),
         'KEY_REPAIR_OUTSIDE_EXPLICIT_MASK')
    stats={'selected_pixels':int(eligible.sum()),'alpha_exact':True,'unselected_RGBA_exact':True,
        'color_alone_classified_as_contamination':False,'opposite_image_edges_wrap':False,
        'selection_method':'pinned-rgb-key-within-actual-alpha-zero-edge-band',
        'selection_radius_pixels':radius,'selection_key_rgb':key.astype(int).tolist(),
        'selected_opaque_subject_pixels':0,'unresolved_key_pixels_without_local_donors':int(excluded.sum()),
        'unresolved_key_pixels_retained':True,'whole_canvas_alpha_exact':np.array_equal(result[:,:,3],rgba[:,:,3])}
    return result,stats


def remove_transparent_edge_low_alpha_keys(rgba,key_colors,max_alpha=4,radius=8):
    """Make explicitly identified, very-low-alpha edge key colors transparent."""
    need(rgba.ndim==3 and rgba.shape[2]==4 and rgba.dtype==np.uint8,'RAW_SOURCE_RGBA_REQUIRED')
    need(type(radius) is int and 1<=radius<=8 and type(max_alpha) is int and 1<=max_alpha<=8,
         'KEY_ALPHA_REMOVAL_BOUNDS_INVALID')
    keys=np.asarray(key_colors)
    need(keys.ndim==2 and keys.shape[1]==3 and 1<=len(keys)<=32 and np.isfinite(keys).all()
         and ((keys>=0)&(keys<=255)).all(),'BACKGROUND_KEY_RGB_INVALID')
    keys=keys.astype(np.uint8);exterior=border_connected(rgba[:,:,3]==0);band=exterior.copy()
    for _ in range(radius):band|=np.logical_or.reduce(neighbors(band))
    matches=np.any(np.all(rgba[:,:,:3][:,:,None,:]==keys[None,None,:,:],axis=-1),axis=-1)
    alpha=rgba[:,:,3];selected=band&matches&(alpha>0)&(alpha<=max_alpha)
    need(selected.any(),'NO_LOW_ALPHA_KEY_PIXELS_IN_TRANSPARENT_EDGE_BAND')
    result=rgba.copy();result[selected]=0
    need(np.array_equal(result[~selected],rgba[~selected]),'EDGE_KEY_ALPHA_REMOVAL_OUTSIDE_BAND')
    colors,counts=np.unique(rgba[selected,:3],axis=0,return_counts=True)
    return result,{'removed_pixels':int(selected.sum()),'maximum_alpha_removed':int(alpha[selected].max()),
        'alpha_pixels_removed':{str(int(v)):int((alpha[selected]==v).sum()) for v in np.unique(alpha[selected])},
        'key_color_counts':{','.join(map(str,c.tolist())):int(n) for c,n in zip(colors,counts)},
        'band_radius_pixels':radius,'selected_opaque_subject_pixels':0,
        'alpha_removed_only_from_explicit_low_alpha_edge_pixels':True,'unselected_RGBA_exact':True}


def raw_original_source(job):
    """Read a true received AI output at its actual, possibly non-integer grid density."""
    source=job['source'];need(set(source)=={'image','original_source_receipt'},'RAW_ORIGINAL_SOURCE_BINDING_INVALID')
    path,_=pin(source['image']);role=job.get('source_scope')
    need(role=='original-model-output','RAW_ORIGINAL_SOURCE_ROLE_REQUIRED')
    text=str(path).replace('\\','/').casefold()
    need('/codex-3x/' not in text and '/candidate3x/' not in text and '/build/experiments/' not in text
         and '/待删除/' not in text,'RAW_ORIGINAL_SOURCE_DERIVATIVE_REJECTED')
    _,receipt_raw=pin(source['original_source_receipt']);receipt=json.loads(receipt_raw)
    original=receipt.get('original',{})
    need(receipt.get('schema')=='h3.art.original-received-source.v1'
         and receipt.get('native_key')==job.get('native_key')
         and original.get('path')==str(path) and original.get('sha256')==source['image']['sha256']
         and original.get('pixels')==source['image']['pixels']
         and receipt.get('source_bytes_unchanged') is True and receipt.get('art_accepted') is False,
         'RAW_ORIGINAL_SOURCE_RECEIPT_MISMATCH')
    rgba=image(source['image'])
    return rgba


def lock_static_rgb(anchor,frame,mask):
    need(frame.shape==anchor.shape and mask.shape==anchor.shape[:2],'STATIC_GROUP_REGISTERED_GEOMETRY_MISMATCH')
    inside=mask>0
    need(not np.any(inside&(anchor[:,:,3]>0)&(frame[:,:,3]<128)),'DYNAMIC_SOURCE_HAS_UNPAINTED_PIXELS')
    out=anchor.copy();mixed=linear(anchor[:,:,:3])*(1-mask[:,:,None])+linear(frame[:,:,:3])*mask[:,:,None]
    out[inside,:3]=encode(mixed)[inside]
    difference=int(np.abs(out[~inside].astype(int)-anchor[~inside].astype(int)).max()) if (~inside).any() else 0
    need(difference==0 and np.array_equal(out[:,:,3],anchor[:,:,3]),'STATIC_BASE_OR_ALPHA_CHANGED')
    return out,{'static_max_RGBA_difference_measured':difference,'alpha_exact':True,'outside_dynamic_mask_exact':True}


def material_label_data(ref,g,key,scale,source_sha):
    need(ref.get('data_role')=='material-label-data' and ref.get('native_key')==key
        and ref.get('native_geometry')==g and ref.get('coordinate_scale')==scale
        and ref.get('source_sha256')==source_sha,'MATERIAL_LABEL_SOURCE_BINDING_INVALID')
    a=image(ref);need([a.shape[1],a.shape[0]]==[v*scale for v in g['canvas_1x']],'MATERIAL_LABEL_CANVAS_MISMATCH')
    need(np.all(a[:,:,3]==255),'MATERIAL_LABEL_DATA_MUST_BE_OPAQUE_ID_DATA')
    return a[:,:,0]


def diagnose_material_labels(expected,actual,labels,scale):
    """Compare declared material categories, not their incidental lighting RGB.

    This diagnoses pinned classification data only. It cannot prove a producer's
    segmentation is correct or that a composed legal game scene has passed.
    """
    need(expected.shape==actual.shape,'MATERIAL_LABEL_GEOMETRY_MISMATCH')
    ids={x['id'] for x in labels}
    need(len(ids)==len(labels) and all(type(x) is int and 1<=x<=255 for x in ids),'MATERIAL_LABEL_DICTIONARY_INVALID')
    for label in labels:token(label['material_id'])
    need(set(np.unique(expected))<=ids|{0} and set(np.unique(actual))<=ids|{0},'UNDECLARED_MATERIAL_LABEL_ID')
    known=(expected!=0)&(actual!=0);different=known&(expected!=actual);edges={}
    for name,a,b in [('top',expected[:scale,:],actual[:scale,:]),('bottom',expected[-scale:,:],actual[-scale:,:]),
                     ('left',expected[:,:scale],actual[:,:scale]),('right',expected[:,-scale:],actual[:,-scale:])]:
        valid=(a!=0)&(b!=0)
        edges[name]={'known_pixels':int(valid.sum()),'mismatched_material_pixels':int(((a!=b)&valid).sum()),
            'unknown_pixels':int((~valid).sum())}
    union_ids=set(np.unique(expected))&ids;actual_ids=set(np.unique(actual))&ids
    return {'required_material_ids_absent':sorted(int(x) for x in union_ids-actual_ids),
        'unknown_reference_pixels':int((expected==0).sum()),'unknown_candidate_pixels':int((actual==0).sum()),
        'known_compared_pixels':int(known.sum()),'mismatched_material_pixels':int(different.sum()),'edges':edges,
        'lighting_correlation_used_for_geometry':False,'classified_masks_checked_against_actual_painting_by_this_tool':False,
        'legal_neighbor_scene_verified':False,'automatic_art_or_redraw_decision':False}


def global_hatch(rgba,mask,origin,scale,period_native=6,width_native=1,gain=.55):
    need(len(origin)==2 and all(type(v) is int for v in origin) and type(period_native) is int
        and type(width_native) is int and 0<width_native<period_native<=32 and 0<=gain<=1,'GLOBAL_HATCH_PARAMETERS_INVALID')
    yy,xx=np.indices(rgba.shape[:2]);stripe=(xx+origin[0]*scale+yy+origin[1]*scale)%(period_native*scale)<width_native*scale
    weight=mask*stripe*(rgba[:,:,3]>0);out=rgba.copy();inside=weight>0
    colors=linear(rgba[:,:,:3])*(1-weight[:,:,None]*(1-gain));out[inside,:3]=encode(colors)[inside]
    return out


# Shared image mathematics retained from the existing map-synth implementation.
# Kept inside this qualified repair payload; there is no dependency on a mutable project script.
def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, np.power(np.clip((c + 0.055) / 1.055, 0.0, 1.0), 2.4))

def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)

def srgb_to_oklab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lin = srgb_to_linear(rgb)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_ = np.cbrt(np.maximum(l, 0.0))
    m_ = np.cbrt(np.maximum(m, 0.0))
    s_ = np.cbrt(np.maximum(s, 0.0))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_out = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_out

def oklab_to_srgb(L: np.ndarray, a: np.ndarray, b_in: np.ndarray) -> np.ndarray:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b_in
    m_ = L - 0.1055613458 * a - 0.0638541728 * b_in
    s_ = L - 0.0894841775 * a - 1.2914855480 * b_in
    l = np.maximum(l_, 0.0) ** 3
    m = np.maximum(m_, 0.0) ** 3
    s = np.maximum(s_, 0.0) ** 3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    lin = np.stack([r, g, b], axis=-1)
    return linear_to_srgb(lin)

def bilinear_sample_toroidal(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x_mod = np.mod(x, w)
    y_mod = np.mod(y, h)
    x0 = np.floor(x_mod).astype(np.int32) % w
    y0 = np.floor(y_mod).astype(np.int32) % h
    x1 = (x0 + 1) % w
    y1 = (y0 + 1) % h
    wx = (x_mod - np.floor(x_mod))[..., None]
    wy = (y_mod - np.floor(y_mod))[..., None]
    c00 = img[y0, x0]
    c10 = img[y0, x1]
    c01 = img[y1, x0]
    c11 = img[y1, x1]
    return (c00 * (1.0 - wx) + c10 * wx) * (1.0 - wy) + (c01 * (1.0 - wx) + c11 * wx) * wy

def recolor_oklch(rgba: np.ndarray, mask: np.ndarray, target_hue: float, target_chroma: float, l_offset: float) -> np.ndarray:
    L, a, b = srgb_to_oklab(rgba[..., :3])
    C = np.sqrt(a * a + b * b)
    # Preserve specular highlights (high L, low C) and deep shadows while rotating cloth hue in OKLCh
    highlight_guard = np.clip((0.92 - L) / 0.25, 0.15, 1.0)
    new_L = np.clip(L + l_offset * mask * highlight_guard, 0.0, 1.0)
    # Modulate chroma proportionally to source local shading so folds and fabric sheen remain 3D
    shading_envelope = np.clip(4.0 * new_L * (1.0 - new_L), 0.20, 1.0)
    new_C = target_chroma * shading_envelope * highlight_guard + C * 0.22
    new_a = new_C * math.cos(target_hue)
    new_b = new_C * math.sin(target_hue)
    blend = (mask * (rgba[..., 3] > 1e-4))[..., None]
    recolored_rgb = oklab_to_srgb(new_L, new_a, new_b)
    out_rgb = rgba[..., :3] * (1.0 - blend) + recolored_rgb * blend
    return np.concatenate([np.clip(out_rgb, 0.0, 1.0), rgba[..., 3:4]], axis=-1)

def textile_pattern(kind, u, v):
    """Return a deterministic, non-heraldic woven field in normalized cloth space."""
    tau=2.0*math.pi
    if kind=='diagonal':field=np.sin(tau*(5*u+2.5*v))
    elif kind=='waves':field=np.sin(tau*(3.5*v+0.22*np.sin(tau*2*u)))
    elif kind=='diamond':field=np.sin(tau*5*u)*np.sin(tau*3*v)
    elif kind=='interlace':field=.5*(np.sin(tau*(4*u+3*v))+np.sin(tau*(4*u-3*v)))
    elif kind=='chevron':field=np.sin(tau*(5*u+3*np.abs(v-.5)))
    elif kind=='lozenge':field=np.cos(tau*4*u)*np.cos(tau*2.5*v)
    elif kind=='loops':field=np.sin(tau*3.5*u+.55*np.cos(tau*2.5*v))*np.sin(tau*2.5*v+.55*np.cos(tau*3.5*u))
    elif kind=='scallop':field=np.sin(tau*(3*v+1.1*np.sin(tau*3*u)))
    else:raise ValueError('FLAG_TEXTILE_PATTERN_INVALID')
    return np.clip(field,-1.0,1.0).astype(np.float32)

def smooth_flag_leading_tips(rgba,size):
    """Open only the free left edge of a flag alpha mask to remove thin AI whiskers."""
    need(type(size) is int and size in (0,3,5,7),'FLAG_TIP_OPEN_SIZE_INVALID')
    if size==0:return rgba.copy(),0,0
    alpha=rgba[:,:,3];ys,xs=np.where(alpha>0);need(len(xs)>0,'FLAG_TIP_REPAIR_ALPHA_EMPTY')
    x0,x1=int(xs.min()),int(xs.max()+1);width=max(1,x1-x0)
    roi_width=max(size*2,int(math.ceil(width*.20)));roi=np.zeros(alpha.shape,dtype=bool)
    roi[:,x0:min(alpha.shape[1],x0+roi_width)]=True
    y0,y1=int(ys.min()),int(ys.max()+1);crop=alpha[y0:y1,x0:x1]
    hard=Image.fromarray(((crop>=128).astype(np.uint8)*255),'L')
    opened=np.asarray(hard.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size)))>=128
    # Keep only open-mask pixels connected to the pole-side body. The original
    # alpha bbox may include a narrow fringe beyond the cloth, so the exact last
    # column can disappear during opening. Anchor on the densest column in the
    # pole-side half instead of requiring pixels at that fragile boundary.
    from collections import deque
    seed_start=min(opened.shape[1]-1,max(roi_width,opened.shape[1]//2))
    pole_side=opened[:,seed_start:]
    need(pole_side.size>0 and bool(pole_side.any()),'FLAG_TIP_REPAIR_BODY_SEED_MISSING')
    seed_x=seed_start+int(np.argmax(pole_side.sum(axis=0)))
    seeds=np.flatnonzero(opened[:,seed_x]);need(len(seeds)>0,'FLAG_TIP_REPAIR_BODY_SEED_MISSING')
    connected=np.zeros(opened.shape,dtype=bool);queue=deque()
    for y in seeds:
        connected[y,seed_x]=True;queue.append((int(y),seed_x))
    while queue:
        y,x=queue.popleft()
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                if not (dy or dx):continue
                yy,xx=y+dy,x+dx
                if 0<=yy<opened.shape[0] and 0<=xx<opened.shape[1] and opened[yy,xx] and not connected[yy,xx]:
                    connected[yy,xx]=True;queue.append((yy,xx))
    crop_roi=np.zeros(crop.shape,dtype=bool);crop_roi[:, :min(crop.shape[1],roi_width)]=True
    remove_local=crop_roi&~connected&(crop>0);remove=np.zeros(alpha.shape,dtype=bool)
    remove[y0:y1,x0:x1]=remove_local
    need(int(remove.sum())<=max(64,int((alpha>0).sum()*.08)),'FLAG_TIP_REPAIR_EXCESSIVE')
    result=rgba.copy();result[remove]=0
    return result,int(remove.sum()),roi_width

def raw_player_flag_variants(rgba,job):
    """Derive eight registered player-color flags from one received HD cloth source.

    The shared alpha and folds are held exactly; only the player dye and a quiet,
    non-heraldic woven texture change. Pixels at or below alpha 4 are the
    model's faint transparent-background glow and are cleared before any variant.
    """
    g=geometry(job['native_geometry']);scale=job['editing_scale']
    need(type(scale) is int and 4<=scale<=32,'HQ_EDITING_SCALE_REQUIRED_3X_IS_NOT_A_MASTER')
    need([rgba.shape[1],rgba.shape[0]]==[x*scale for x in g['canvas_1x']],
         'FLAG_VARIANT_SOURCE_CANVAS_MISMATCH')
    cutoff=job.get('transparent_max_alpha',4)
    need(type(cutoff) is int and 1<=cutoff<=4,'FLAG_TRANSPARENT_ALPHA_CUTOFF_INVALID')
    base=rgba.copy();low=(base[:,:,3]>0)&(base[:,:,3]<=cutoff);base[low]=0
    tip_size=job.get('tip_open_size',0);base,tip_removed,tip_roi_width=smooth_flag_leading_tips(base,tip_size)
    support=base[:,:,3]>0;need(int(support.sum())>=128,'FLAG_SOURCE_ALPHA_EMPTY')
    ys,xs=np.where(support);x0,x1=int(xs.min()),int(xs.max()+1);y0,y1=int(ys.min()),int(ys.max()+1)
    rgb=base[:,:,:3].astype(np.float32)/255.0
    _,lab_a,lab_b=srgb_to_oklab(rgb);chroma=np.sqrt(lab_a*lab_a+lab_b*lab_b)
    # Use only chromatic cloth pixels; neutral highlight/seam pixels stay intact.
    cloth=support&(chroma>.035)
    need(int(cloth.sum())>=128,'FLAG_CLOTH_COLOR_MASK_TOO_SMALL')
    yy,xx=np.indices(base.shape[:2],dtype=np.float32)
    u=np.clip((xx-x0)/max(1,x1-x0-1),0,1);v=np.clip((yy-y0)/max(1,y1-y0-1),0,1)
    outputs=[];bindings={};alpha=base[:,:,3].copy()
    variants=job['variants'];need(isinstance(variants,list) and len(variants) in (7,8),'FLAG_VARIANT_COUNT_INVALID')
    ids=set()
    for variant in variants:
        need(set(variant)=={'id','native_key','hue_degrees','chroma','lightness_offset','pattern_kind'},
             'FLAG_VARIANT_FIELDS_INVALID')
        identity=variant['id'];token(identity);need(identity not in ids,'DUPLICATE_REPAIR_OUTPUT');ids.add(identity)
        key=variant['native_key'];need(isinstance(key,list) and len(key)==4 and key[0]=='def'
             and key[2:]==job['native_key'][2:] and identity==f'{key[1]}_{key[2]}_{key[3]}','FLAG_VARIANT_NATIVE_KEY_INVALID')
        hue=variant['hue_degrees'];chroma=variant['chroma'];offset=variant['lightness_offset']
        need(0<=hue<=360 and 0<=chroma<=.4 and abs(offset)<=.25,'COLOR_VARIANT_PARAMETER_INVALID')
        pattern=textile_pattern(variant['pattern_kind'],u,v)
        recolored=recolor_oklch(base.astype(np.float32)/255.0,cloth.astype(np.float32),math.radians(hue),chroma,offset)
        L,a1,b1=srgb_to_oklab(recolored[:,:,:3])
        L=np.clip(L+pattern*.026*cloth,0,1)
        patterned=oklab_to_srgb(L,a1,b1)
        result=base.copy();result[cloth,:3]=np.rint(np.clip(patterned[cloth],0,1)*255).astype(np.uint8)
        need(np.array_equal(result[:,:,3],alpha),'FLAG_VARIANT_ALPHA_DRIFT')
        need(np.array_equal(result[~cloth],base[~cloth]),'FLAG_VARIANT_OUTSIDE_CLOTH_CHANGED')
        outputs.append((identity,result))
        bindings[identity]={'native_key':key,'geometry':g,'editing_scale':scale,'layout':'full-canvas'}
    statistics={'output_geometry':bindings,'source_scale':scale,'source_alpha_sha256':digest(alpha.tobytes()),
        'common_alpha_exact':True,'transparent_background_pixels_removed':int(low.sum()),
        'transparent_alpha_cutoff':cutoff,'tip_open_size':tip_size,'tip_roi_width_pixels':tip_roi_width,
        'tip_alpha_pixels_removed':tip_removed,'cloth_pixels_recolored_and_patterned':int(cloth.sum()),
        'player_colors_preserved_as_distinct_hues':True,'patterns_are_non_heraldic':True,
        'positions_and_flag_silhouette_identical_across_variants':True,'new_model_inference':False}
    return outputs,statistics

def dispatch(job):
    operator=job['operator'];need(OPERATORS.get(operator)==job.get('operator_version'),'REPAIR_OPERATOR_VERSION_UNSUPPORTED')
    scope=job.get('source_scope')
    need(scope is None or (operator in ('bounded-key-decontaminate','raw-player-flag-variants') and scope=='original-model-output'),
         'RAW_ORIGINAL_SOURCE_ROLE_REQUIRED')
    raw_key=(operator in ('bounded-key-decontaminate','raw-player-flag-variants') and scope=='original-model-output')
    if raw_key:
        rgba=raw_original_source(job);g=job.get('native_geometry');scale=None
        if g is not None:geometry(g)
        need(job.get('native_key') is not None,'RAW_SOURCE_NATIVE_KEY_REQUIRED')
        if operator=='raw-player-flag-variants':return raw_player_flag_variants(rgba,job)
    else:rgba,g,scale=registered(job,delivery=operator=='native-canvas-assembly')
    identity=job['id'];statistics={}
    count=len(job['phases']) if operator=='registered-flow-phases' else len(job['states']) if operator=='coordinate-state-effects' else len(job['frames']) if operator=='locked-static-base' else job.get('frames',len(job.get('variants',[])) if operator in ('masked-color-variants','raw-player-flag-variants','registered-terrain-family') else 1)
    need(type(count) is int and count>0 and rgba.shape[0]*rgba.shape[1]*count<=64*1024*1024,'REPAIR_JOB_OUTPUT_CAPACITY_EXCEEDED')
    if operator in ('registered-contact-transfer','registered-flow-phases'):
        import asset_repair_interfaces
        return (asset_repair_interfaces.transfer if operator=='registered-contact-transfer' else asset_repair_interfaces.phases)(sys.modules[__name__],job,rgba,g,scale)
    if operator=='registered-terrain-family':
        import asset_repair_terrain
        return asset_repair_terrain.family(sys.modules[__name__],job,rgba,g,scale)
    if operator in ('native-canvas-assembly','registered-hq-canvas'):
        statistics.update(painted_RGB_resampled=False,native_crop_origin_exact=True,
            output_geometry={identity:{'native_key':job['native_key'],'geometry':g,'editing_scale':scale,'layout':'full-canvas'}})
        return [(identity,rgba)],statistics
    if operator=='material-lab-harmonize':
        token(job['material_id']);mask=selection_mask(job['mask'],rgba.shape[:2]);targets=[]
        refs=job['references'];need(isinstance(refs,list) and 1<=len(refs)<=32,'MATERIAL_REFERENCES_REQUIRED')
        for reference in refs:
            need(set(reference)=={'native_key','source','mask','material_id'} and reference['material_id']==job['material_id'],'MATERIAL_REFERENCE_BINDING_INVALID')
            parent,pg,ps=registered(reference)
            need(reference['native_key'][:2]==job['native_key'][:2] and ps==scale,'MATERIAL_REFERENCE_FAMILY_OR_SCALE_MISMATCH')
            targets.append(color_statistics(parent,selection_mask(reference['mask'],parent.shape[:2])))
        result,statistics=bounded_lab_shift(rgba,mask,np.median(targets,axis=0),job.get('max_lightness_shift',4),job.get('max_chroma_shift',4))
        statistics.update(material_id=job['material_id'],reference_count=len(refs),material_region_semantics_provided_by_owner=True)
        return [(identity,result)],statistics
    if operator=='bounded-key-decontaminate':
        if raw_key:
            if job.get('mode')=='fringe-rgb':
                result,statistics=decontaminate_transparent_edge_key(rgba,job['key_rgb'],
                    job.get('radius_pixels',4),job.get('key_tolerance',64))
            elif job.get('mode')=='transparent-edge-low-alpha-key':
                result,statistics=remove_transparent_edge_low_alpha_keys(rgba,job['key_colors'],
                    job.get('maximum_alpha',4),job.get('radius_pixels',8))
            else:need(False,'RAW_SOURCE_KEY_REPAIR_MODE_UNSUPPORTED')
            output_binding={'native_key':job['native_key'],'layout':'raw-original-resolution-candidate',
                'pixels':[rgba.shape[1],rgba.shape[0]],'source_original_sha256':job['source']['image']['sha256']}
            if g is not None:output_binding['geometry']=g
            statistics['output_geometry']={identity:output_binding}
            statistics['original_png_geometry_resampled']=False
        else:
            mask=selection_mask(job['mask'],rgba.shape[:2])
            result,statistics=decontaminate_selected_key(rgba,mask,job['key_rgb'],job.get('mode','fringe-rgb'),job.get('radius_pixels',3),job.get('key_tolerance',64))
        return [(identity,result)],statistics
    if operator=='material-label-diagnostic':
        native=image(job['native_reference']);need([native.shape[1],native.shape[0]]==g['canvas_1x'],'MATERIAL_NATIVE_REFERENCE_CANVAS_INVALID')
        expected=material_label_data(job['native_labels'],g,job['native_key'],1,job['native_reference']['sha256'])
        expected=np.repeat(np.repeat(expected,scale,axis=0),scale,axis=1)
        actual=material_label_data(job['candidate_labels'],g,job['native_key'],scale,job['source']['image']['sha256'])
        return [],diagnose_material_labels(expected,actual,job['labels'],scale)
    if operator=='coordinate-state-effects':
        base=logical_mask(job['base_mask'],g,job['native_key']);selection=selection_mask(job['mask'],rgba.shape[:2])
        base_support=np.repeat(np.repeat(base>0,scale,axis=0),scale,axis=1)
        need(not np.any((rgba[:,:,3]>0)&~base_support),'STATE_SOURCE_REGION_NOT_BOUND_TO_NATIVE_BASE_ALPHA')
        outputs=[];bindings={}
        for state in job['states']:
            need(set(state)<={'id','native_key','geometry','native_mask','mode','coordinate_origin_native','period_native','width_native','gain','outline_rgb','max_outline_native'},'UNKNOWN_STATE_EFFECT_FIELD')
            target=geometry(state['geometry']);need(target==g,'STATE_EFFECT_REQUIRES_SHARED_CANVAS_CROP_ORIGIN')
            alpha=logical_mask(state['native_mask'],target,state['native_key'])
            if state['mode']=='hatch':
                need(np.array_equal(alpha,base),'STATE_HATCH_HAS_DISTINCT_NATIVE_GEOMETRY')
                result=global_hatch(rgba,selection,state['coordinate_origin_native'],scale,state.get('period_native',6),state.get('width_native',1),state.get('gain',.55))
            else:
                need(state['mode']=='outline','UNSUPPORTED_STATE_EFFECT_MODE')
                need(not np.any((base>0)&(alpha<base)),'SELECTED_STATE_REMOVES_BASE_GEOMETRY')
                width=state.get('max_outline_native',1);need(type(width) is int and 1<=width<=2,'OUTLINE_WIDTH_INVALID')
                support=base>0
                for _ in range(width):support|=np.logical_or.reduce(neighbors(support))
                extra=(alpha>0)&(base==0);need(not np.any(extra&~support),'SELECTED_OUTLINE_OUTSIDE_NATIVE_BASE_RIM')
                color=np.asarray(state['outline_rgb']);need(color.shape==(3,) and np.isfinite(color).all()
                    and ((color>=0)&(color<=255)).all(),'OUTLINE_RGB_INVALID')
                expanded=np.repeat(np.repeat(extra,scale,axis=0),scale,axis=1)
                added_alpha=np.repeat(np.repeat(alpha,scale,axis=0),scale,axis=1)
                result=rgba.copy();result[expanded,:3]=color.astype(np.uint8)
                result[expanded,3]=np.maximum(result[expanded,3],added_alpha[expanded])
                need(np.array_equal(result[~expanded],rgba[~expanded]),'OUTLINE_CHANGED_BASE_PAINT')
            outputs.append((state['id'],result));bindings[state['id']]={'native_key':state['native_key'],
                'geometry':target,'editing_scale':scale,'layout':'full-canvas'}
        return outputs,{'output_geometry':bindings,'global_coordinate_phase_used':True,'button_state_count_or_offsets_guessed':False,'native_consumer_runtime_verified':False}
    if operator=='locked-static-base':
        mask=selection_mask(job['mask'],rgba.shape[:2]);outputs=[];bindings={};measured=[]
        for frame in job['frames']:
            need(set(frame)=={'id','native_key','source'},'STATIC_FRAME_FIELDS_INVALID')
            candidate,cg,cs=registered(frame)
            need(frame['native_key'][:3]==job['native_key'][:3] and cg==g and cs==scale,'STATIC_GROUP_REQUIRES_SHARED_REGISTRATION')
            result,measures=lock_static_rgb(rgba,candidate,mask);outputs.append((frame['id'],result));measured.append(measures)
            bindings[frame['id']]={'native_key':frame['native_key'],'geometry':cg,'editing_scale':cs,'layout':'full-canvas'}
        return outputs,{'output_geometry':bindings,'measured_frames':measured,'native_timing_verified':False}
    if operator=='shared-interface-collar':
        native=logical_mask(job['native_mask'],g,job['native_key'])
        material,mg,ms=registered(job,'material')
        need(g['canvas_1x']==[32,32] and mg['canvas_1x']==[32,32] and scale==ms,'INTERFACE_V1_NATIVE32_MATCHED_SCALE_REQUIRED')
        material_receipt=json.loads(pin(job['material']['receipt'])[1])
        if material_receipt.get('schema')==RECEIPT_SCHEMA:
            material_key=next(x['native_key'] for x in material_receipt['outputs']
                if x['sha256']==job['material']['image']['sha256'] and x['pixels']==job['material']['image']['pixels']
                and plain_path(x['path'])==plain_path(job['material']['image']['path']))
        else:material_key=material_receipt['contract']['native_entry']['key']
        need(job['native_key'][1]==material_key[1],'MATERIAL_FAMILY_MISMATCH')
        depth=job.get('depth_native',3);shared=job.get('shared_native',1)
        need(type(depth) is int and type(shared) is int and 1<=shared<depth<=4,'INTERFACE_COLLAR_PARAMETERS_INVALID')
        sides=job['controlled_sides'];need(isinstance(sides,list) and bool(sides) and len(set(sides))==len(sides) and set(sides)<={'top','bottom','left','right'},'EXPLICIT_INTERFACE_SIDES_REQUIRED')
        strip=canonical_strip(material,depth=depth*scale)
        result=repair_collars(rgba,native,strip,scale,depth,shared,sides);margin=depth*scale
        statistics['unmodified_core_exact']=bool(np.array_equal(result[margin:-margin,margin:-margin],rgba[margin:-margin,margin:-margin]))
        need(statistics['unmodified_core_exact'],'UNDECLARED_PAINTING_BODY_CHANGE')
        return [(identity,result)],statistics
    if operator=='registered-alpha':
        alpha=logical_mask(job['native_mask'],g,job['native_key'])
        alpha=np.repeat(np.repeat(alpha,scale,axis=0),scale,axis=1)
        mode=job.get('mode','intersect');need(mode in ('intersect','replace'),'ALPHA_MODE_INVALID')
        result=rgba.copy()
        if mode=='replace':
            need(not np.any((alpha>0)&(rgba[:,:,3]==0)),'ALPHA_REPLACE_WOULD_REVEAL_UNPAINTED_PIXELS')
            result[:,:,3]=alpha
        else:result[:,:,3]=np.rint(rgba[:,:,3].astype(float)*alpha/255).astype(np.uint8)
        statistics['visible_RGB_exact']=bool(np.array_equal(result[:,:,:3],rgba[:,:,:3]))
        return [(identity,result)],statistics
    if operator=='registered-parent-reslice':
        need(isinstance(job['parts'],list) and bool(job['parts']),'NONEMPTY_REGISTERED_PART_SET_REQUIRED')
        outputs=[];bindings={};pixels=0;covered=np.zeros(rgba.shape[:2],bool)
        for part in job['parts']:
            x,y,w,h=part['rect_native'];need(all(type(v) is int for v in (x,y,w,h)) and min(x,y)>=0 and min(w,h)>0,'SLICE_RECT_INVALID')
            need(x+w<=g['canvas_1x'][0] and y+h<=g['canvas_1x'][1],'SLICE_OUTSIDE_REGISTERED_PARENT')
            pixels+=w*h*scale*scale;need(pixels<=64*1024*1024,'SLICE_OUTPUT_CAPACITY_EXCEEDED')
            target=geometry(part['target_geometry']);need(target['crop_1x']==[w,h] and isinstance(part['native_key'],list),'SLICE_NATIVE_TARGET_BINDING_INVALID')
            cut=rgba[y*scale:(y+h)*scale,x*scale:(x+w)*scale].copy()
            mask=image(part['mask']);need(part['mask'].get('data_role')=='native-logical-alpha-geometry' and mask.shape[:2]==(h,w),'SLICE_MASK_INVALID')
            alpha=mask[:,:,3];need(digest(alpha.tobytes())==part['mask'].get('alpha_sha256'),'SLICE_ALPHA_PIN_REQUIRED')
            need(part['mask'].get('native_key')==part['native_key'] and part['mask'].get('native_geometry')==target,'SLICE_MASK_NATIVE_BINDING_MISMATCH')
            mask=np.repeat(np.repeat(alpha,scale,axis=0),scale,axis=1)
            cut[:,:,3]=np.rint(cut[:,:,3].astype(float)*mask/255).astype(np.uint8)
            covered[y*scale:(y+h)*scale,x*scale:(x+w)*scale]|=(mask>0)&(cut[:,:,3]>0)
            outputs.append((part['id'],cut))
            bindings[part['id']]={'native_key':part['native_key'],'geometry':target,'editing_scale':scale,'layout':'native-crop','source_rect_native':[x,y,w,h]}
        complete=False
        if 'expected_coverage_mask' in job:
            need(set(job['required_part_ids'])==set(bindings) and len(job['required_part_ids'])==len(bindings),'REGISTERED_PART_COVERAGE_SLOT_SET_MISMATCH')
            expected=selection_mask(job['expected_coverage_mask'],rgba.shape[:2])>0
            need(expected.any() and not np.any(expected&~covered),'REGISTERED_PARTS_LEAVE_REQUIRED_PARENT_PIXELS_UNCOVERED')
            complete=True
        statistics.update(painted_RGB_resampled=False,shared_coordinate_transform=True,output_geometry=bindings,
            complete_declared_coverage_verified=complete,coverage_pixels=int(covered.sum()),native_game_assembly_verified=False)
        return outputs,statistics
    if operator=='masked-color-variants':
        mask=selection_mask(job['mask'],rgba.shape[:2]);outputs=[]
        for variant in job['variants']:
            need(0<=variant['hue_degrees']<=360 and 0<=variant['chroma']<=.4 and abs(variant.get('lightness_offset',0))<=.25,'COLOR_VARIANT_PARAMETER_INVALID')
            colors=recolor_oklch(rgba.astype(np.float32)/255,mask,math.radians(variant['hue_degrees']),variant['chroma'],variant.get('lightness_offset',0))
            result=rgba.copy();inside=mask>0
            result[inside,:3]=np.rint(np.clip(colors[inside,:3],0,1)*255).astype(np.uint8)
            need(np.array_equal(result[:,:,3],rgba[:,:,3]) and np.array_equal(result[~inside],rgba[~inside]),'COLOR_STATIC_PIXELS_CHANGED')
            outputs.append((variant['id'],result))
        statistics.update(alpha_exact=True,outside_mask_exact=True)
        return outputs,statistics
    if operator=='locked-rgb-flow':
        mask=selection_mask(job['mask'],rgba.shape[:2]);inside=mask>0
        frames=job['frames'];amplitude=job['amplitude_pixels'];direction=np.asarray(job['direction'],dtype=np.float32)
        need(type(frames) is int and 2<=frames<=120 and 0<=amplitude<=scale*2 and direction.shape==(2,) and np.isfinite(direction).all() and np.linalg.norm(direction)>0,'FLOW_PARAMETERS_INVALID')
        direction/=np.linalg.norm(direction);yy,xx=np.indices(rgba.shape[:2],dtype=np.float32);outputs=[];base=rgba.astype(np.float32)/255
        for i in range(frames):
            delta=math.sin(i*2*math.pi/frames)*amplitude
            warped=bilinear_sample_toroidal(base,xx+direction[0]*delta,yy+direction[1]*delta)
            need(not np.any(warped[inside,3]<.5),'FLOW_MASK_REACHES_TRANSPARENCY')
            result=rgba.copy();colors=base[:,:,:3]*(1-mask[:,:,None])+warped[:,:,:3]*mask[:,:,None]
            result[inside,:3]=np.rint(np.clip(colors[inside],0,1)*255).astype(np.uint8)
            need(np.array_equal(result[:,:,3],rgba[:,:,3]) and np.array_equal(result[~inside],rgba[~inside]),'FLOW_STATIC_LAYER_CHANGED')
            outputs.append((identity+f'_f{i:03}',result))
        statistics.update(alpha_exact=True,outside_mask_exact=True)
        return outputs,statistics
    raise ValueError('OPERATOR_NOT_IMPLEMENTED')


def token(value):
    need(isinstance(value,str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,95}',value),'UNSAFE_REPAIR_ID')
    return value


def references(document):
    if isinstance(document,dict):
        if 'path' in document and 'sha256' in document:yield document
        for value in document.values():yield from references(value)
    elif isinstance(document,list):
        for value in document:yield from references(value)


def registered_bindings(document):
    if isinstance(document,dict):
        if {'image','receipt','geometry','editing_scale'}<=set(document):yield document
        for value in document.values():yield from registered_bindings(value)
    elif isinstance(document,list):
        for value in document:yield from registered_bindings(value)


def preflight(manifest,expected,operation):
    p,raw=pin({'path':str(manifest),'sha256':expected});document=json.loads(raw)
    need(document.get('schema')==MANIFEST_SCHEMA and document.get('operation_id')==operation,'REPAIR_MANIFEST_ID_OR_SCHEMA_INVALID')
    need(set(document)<={'schema','operation_id','jobs','notes'},'UNKNOWN_REPAIR_MANIFEST_FIELD')
    need(isinstance(document.get('jobs'),list) and 1<=len(document['jobs'])<=10000,'REPAIR_BATCH_EMPTY_OR_TOO_LARGE')
    seen=set();output_names=set();inputs={};total_pixels=0
    for ref in references(document):
        path,data=pin(ref);inputs[str(path)]={'path':str(path),'sha256':digest(data),'bytes':len(data)}
    for value in registered_bindings(document):
        need(set(value)=={'image','receipt','geometry','editing_scale'},'REGISTERED_SOURCE_FIELDS_INVALID')
        _,upstream=pin(value['receipt']);parent=json.loads(upstream)
        if parent.get('schema')==RECEIPT_SCHEMA:
            for ref in parent['inputs']:
                p,data=pin(ref);inputs[str(p)]={'path':str(p),'sha256':digest(data),'bytes':len(data)}
        for layer in parent.get('contract',{}).get('entry',{}).get('layers',[]):
            for source_name in ('source_a','source_b'):
                if source_name in layer:
                    p,data=pin(layer[source_name]);inputs[str(p)]={'path':str(p),'sha256':digest(data),'bytes':len(data)}
    for job in document['jobs']:
        token(job['id']);need(job['id'] not in seen,'DUPLICATE_REPAIR_JOB');seen.add(job['id'])
        fields={'shared-interface-collar':{'native_mask','material','depth_native','shared_native','controlled_sides'},
                'registered-alpha':{'native_mask','mode'},'registered-parent-reslice':{'parts','required_part_ids','expected_coverage_mask'},
                'masked-color-variants':{'mask','variants'},'raw-player-flag-variants':{'native_geometry','editing_scale','variants','transparent_max_alpha','tip_open_size','source_scope'},
                'locked-rgb-flow':{'mask','frames','amplitude_pixels','direction'},'native-canvas-assembly':set(),'registered-hq-canvas':set(),
                'material-lab-harmonize':{'mask','references','material_id','max_lightness_shift','max_chroma_shift'},
                'locked-static-base':{'mask','frames'},'bounded-key-decontaminate':{'mask','key_rgb','key_colors','mode','radius_pixels','key_tolerance','maximum_alpha','source_scope'},
                'material-label-diagnostic':{'native_reference','native_labels','candidate_labels','labels'},
                'coordinate-state-effects':{'mask','base_mask','states'},
                'registered-contact-transfer':{'native_mask','material','material_family_contract','controlled_sides','depth_native','shared_native'},
                'registered-flow-phases':{'mask','phases','phase_contract','amplitude_pixels','direction'}}
        fields['registered-terrain-family']={'family_contract','variants','frame_native','patch_native','overlap_native'}
        need(job.get('operator') in fields,'REPAIR_OPERATOR_UNSUPPORTED')
        need(set(job)<={'id','operator','operator_version','native_key','source','notes'}|fields[job['operator']],'UNKNOWN_REPAIR_JOB_FIELD')
        outputs,statistics=dispatch(job)
        json.dumps(statistics,allow_nan=False)
        for name,rgba in outputs:
            token(name);need(name not in output_names,'DUPLICATE_REPAIR_OUTPUT');output_names.add(name)
            need(rgba.ndim==3 and rgba.shape[2]==4 and rgba.dtype==np.uint8,'OPERATOR_OUTPUT_INVALID')
            total_pixels+=rgba.shape[0]*rgba.shape[1]
            need(total_pixels<=512*1024*1024,'REPAIR_BATCH_OUTPUT_CAPACITY_EXCEEDED')
    return document,inputs


def execute(action,manifest,expected,outdir,operation,renderer=None,renderer_sha=None):
    token(operation);root=plain_path(str(outdir))
    safe=(PRODUCT/'build/experiments/asset-repair').resolve()
    need(root.parent==safe and root.name==operation,'REPAIR_OUTPUT_MUST_BE_NEW_PRODUCT_OPERATION')
    if renderer:pin({'path':renderer,'sha256':renderer_sha})
    document,inputs=preflight(manifest,expected,operation)
    receipt_path=root/'asset-repair-receipt.json'
    if action=='check':return {'status':'READY','jobs':len(document['jobs']),'outputs_written':0,'art_approved':False}
    if action=='run' and root.exists():
        need(receipt_path.is_file(),'PARTIAL_OR_FOREIGN_REPAIR_OPERATION_EXISTS');action='verify'
    if action=='verify':
        need(receipt_path.is_file(),'REPAIR_RECEIPT_MISSING');receipt=json.loads(receipt_path.read_bytes())
        need(receipt.get('schema')==RECEIPT_SCHEMA and receipt.get('manifest_sha256')==expected and receipt.get('operation_id')==operation,'REPAIR_RECEIPT_BINDING_MISMATCH')
        actual_outputs={x['id']:x for x in receipt['outputs']}
        actual_jobs={x['id']:x for x in receipt['jobs']}
        need(len(actual_outputs)==len(receipt['outputs']) and len(actual_jobs)==len(receipt['jobs']),'DUPLICATE_RECEIPT_OUTPUT_OR_JOB')
        for job in document['jobs']:
            outputs,statistics=dispatch(job)
            need(job['id'] in actual_jobs,'REPAIR_JOB_DIAGNOSTIC_MISSING')
            recorded=actual_jobs.pop(job['id'])
            need(recorded['operator']==job['operator'] and recorded['operator_version']==job['operator_version']
                and recorded['statistics']==statistics,'REPAIR_DIAGNOSTIC_REPLAY_MISMATCH')
            for name,rgba in outputs:
                need(name in actual_outputs,'REPAIR_OUTPUT_MISSING');entry=actual_outputs.pop(name)
                path,data=pin(entry);need(path.parent==root/'images' and path.name==name+'.png','REPAIR_OUTPUT_PATH_CHANGED')
                with Image.open(io.BytesIO(data)) as im:need(np.array_equal(np.asarray(im.convert('RGBA')),rgba),'REPAIR_PIXEL_REPLAY_MISMATCH')
        need(not actual_outputs and not actual_jobs,'UNEXPECTED_REPAIR_OUTPUTS_OR_DIAGNOSTICS')
        return {'status':'VERIFIED','jobs':len(document['jobs']),'outputs':len(receipt['outputs']),'receipt':str(receipt_path),'art_approved':False}
    need(action=='run' and not root.exists(),'REPAIR_OPERATION_EXISTS')
    root.mkdir(parents=True);(root/'images').mkdir();outputs=[];stats=[]
    for job in document['jobs']:
        results,statistics=dispatch(job)
        for name,rgba in results:
            path=root/'images'/(name+'.png')
            with path.open('xb') as stream:Image.fromarray(rgba).save(stream,format='PNG')
            binding=statistics.get('output_geometry',{}).get(name)
            if binding is None:
                binding={'native_key':job['native_key'],'geometry':job['source']['geometry'],
                    'editing_scale':job['source']['editing_scale'],'layout':'full-canvas'}
            role='native3x_canvas_not_high_resolution_master' if job['operator']=='native-canvas-assembly' else (
                'raw-original-resolution-derived-candidate-not-master' if statistics.get('original_png_geometry_resampled') is False
                else 'derived_high_resolution_candidate_not_new_model_output')
            outputs.append({'id':name,'path':str(path),'sha256':digest(path.read_bytes()),'pixels':[rgba.shape[1],rgba.shape[0]],'rgba_sha256':digest(rgba.tobytes()),'role':role,**binding})
        stats.append({'id':job['id'],'operator':job['operator'],'operator_version':job['operator_version'],'statistics':statistics})
    # Inputs cannot change between checking and the final immutable receipt.
    for ref in inputs.values():pin(ref)
    receipt={'schema':RECEIPT_SCHEMA,'operation_id':operation,'manifest_path':str(manifest),'manifest_sha256':expected,
        'created_utc':datetime.now(timezone.utc).isoformat(),'operator_versions':OPERATORS,'script_sha256':digest(Path(__file__).read_bytes()),
        'renderer':{'path':renderer,'sha256':renderer_sha},'inputs':list(inputs.values()),'jobs':stats,'outputs':outputs,
        'native_RGB_used_as_paint':False,'new_model_inference':False,'art_approved':False,'native_timing_verified':False,'game_deployed':False}
    with receipt_path.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return {'status':'COMPLETED_REVIEW_REQUIRED','outputs':len(outputs),'receipt':str(receipt_path),'art_approved':False}


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('catalog','check','run','verify'))
    parser.add_argument('--manifest',type=Path);parser.add_argument('--manifest-sha256');parser.add_argument('--outdir',type=Path);parser.add_argument('--operation-id')
    parser.add_argument('--renderer');parser.add_argument('--renderer-sha256');args=parser.parse_args(argv)
    try:
        if args.action=='catalog':result={'schema':'h3.asset-repair.catalog.v1','operators':OPERATORS,'registration_commands':['motion-plan','motion-run','motion-verify'],'automatic_art_approval':False,'automatic_game_deployment':False}
        else:
            need(all((args.manifest,args.manifest_sha256,args.outdir,args.operation_id)),'FROZEN_MANIFEST_AND_OPERATION_REQUIRED')
            result=execute(args.action,args.manifest,args.manifest_sha256,args.outdir,args.operation_id,args.renderer,args.renderer_sha256)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as error:
        print(json.dumps({'status':'BLOCKED','code':'ASSET_REPAIR_CONTRACT','error':str(error)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
