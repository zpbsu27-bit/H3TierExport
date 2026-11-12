"""Prepainting tile geometry and explicit neutral sampling fixtures.

Native alpha is a geometry witness, never painted RGB. The smooth polygon is
designed before painting. No completed painting is resized, warped, averaged,
extruded or clipped by this module. Outputs cannot be admitted as art masters.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image

PRODUCT=resolve_tool_root(__file__)
SCHEMA='h3.tile-geometry.design-manifest.v1'
SIDES=('top','bottom','left','right')


def need(condition,code):
    if not condition:raise ValueError(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def pin(binding):
    p=Path(binding['path']);need(p.is_absolute(),'TILE_ABSOLUTE_PIN_REQUIRED')
    for q in (p,*p.parents):
        if q.exists():need(not q.is_symlink() and not getattr(q.lstat(),'st_file_attributes',0)&0x400,'TILE_REPARSE_BLOCKED')
    raw=p.read_bytes();need(sha(raw)==binding['sha256'],'TILE_INPUT_DRIFT')
    return p,raw


def ref(path):return {'path':str(Path(path).resolve()),'sha256':sha(Path(path).read_bytes())}


def write_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def port_mask(mask,ports,interval,depth,pad):
    """Modify geometry only: pin a real rectangular entrance before smoothing."""
    n=mask.shape[0];need(mask.shape==(n,n),'TILE_SQUARE_GRID_REQUIRED')
    lo,hi=interval
    need(0<lo<hi<n and type(depth) is int and 1<=depth<=n//4,'TILE_PORT_DESIGN_INVALID')
    out=np.pad(mask,pad)
    for side in ports:
        need(side in SIDES,'TILE_PORT_SIDE_INVALID')
        if side=='top':
            out[:pad+depth,pad:pad+n]=False;out[:pad+depth,pad+lo:pad+hi]=True
        elif side=='bottom':
            out[pad+n-depth:,pad:pad+n]=False;out[pad+n-depth:,pad+lo:pad+hi]=True
        elif side=='left':
            out[pad:pad+n,:pad+depth]=False;out[pad+lo:pad+hi,:pad+depth]=True
        else:
            out[pad:pad+n,pad+n-depth:]=False;out[pad+lo:pad+hi,pad+n-depth:]=True
    return out


def area(poly):
    a=np.asarray(poly,dtype=float);b=np.roll(a,-1,axis=0)
    return float(np.sum(a[:,0]*b[:,1]-b[:,0]*a[:,1])*.5)


def trace_contours(mask):
    """Trace cell-union boundaries, retaining holes and separate components."""
    edges=set();h,w=mask.shape
    for y,x in zip(*np.nonzero(mask)):
        x=int(x);y=int(y)
        for empty,a,b in ((y==0 or not mask[y-1,x],(x,y),(x+1,y)),
                          (x==w-1 or not mask[y,x+1],(x+1,y),(x+1,y+1)),
                          (y==h-1 or not mask[y+1,x],(x+1,y+1),(x,y+1)),
                          (x==0 or not mask[y,x-1],(x,y+1),(x,y))):
            if empty:edges.add((a,b))
    graph=defaultdict(set)
    for a,b in edges:graph[a].add(b)
    remaining=set(edges);polys=[]
    directions={(1,0):0,(0,1):1,(-1,0):2,(0,-1):3}
    while remaining:
        first=min(remaining);a,b=first;poly=[a];remaining.remove(first)
        while b!=poly[0]:
            poly.append(b);options=[q for q in graph[b] if (b,q) in remaining]
            need(options,'TILE_CONTOUR_NOT_CLOSED')
            previous=directions[b[0]-a[0],b[1]-a[1]]
            # Keep the occupied face on the right at diagonally touching cells.
            def rank(q):
                turn=(directions[q[0]-b[0],q[1]-b[1]]-previous)%4
                return {1:0,0:1,3:2,2:3}[turn]
            q=min(options,key=rank);remaining.remove((b,q));a,b=b,q
            need(len(poly)<=mask.size*8,'TILE_CONTOUR_LOOP_LIMIT')
        need(len(poly)>=4,'TILE_DEGENERATE_CONTOUR');polys.append(poly)
    return sorted(polys,key=lambda p:abs(area(p)),reverse=True)


def smooth(poly,iterations=2):
    need(type(iterations) is int and 0<=iterations<=4,'TILE_SMOOTHING_BUDGET')
    p=np.asarray(poly,dtype=float)
    for _ in range(iterations):
        q=np.roll(p,-1,axis=0);out=np.empty((len(p)*2,2),float)
        out[0::2]=.75*p+.25*q;out[1::2]=.25*p+.75*q;p=out
    return p


def neutral_pattern(n,scale):
    """Sharp two-dimensional markers; transpose/reflection invariant by design.

    This is mathematical TEST data, not a blurred/extruded painted collar.
    No native RGB or existing artwork enters this function.
    """
    size=n*scale;y,x=np.indices((size,size));x=np.minimum(x,size-1-x);y=np.minimum(y,size-1-y)
    a=(x//(2*scale)+y//(2*scale))%2
    b=(x//scale+y//scale)%3
    out=np.empty((size,size,3),np.uint8)
    out[...,0]=50+150*a;out[...,1]=70+40*b;out[...,2]=190-90*a
    return out


def rasterize(polys,n,scale):
    """Pixel-center scan conversion, not inclusive integer polygon endpoints."""
    aa=2;k=scale*aa;size=n*k;mask=np.zeros((size,size),np.uint8)
    for poly in polys:
        p=np.asarray(poly);q=np.roll(p,-1,axis=0);fill=255 if area(p)>0 else 0
        lo=max(0,int(np.floor(p[:,1].min()*k)));hi=min(size,int(np.ceil(p[:,1].max()*k)))
        for yy in range(lo,hi):
            y=(yy+.5)/k
            active=(p[:,1]>y)!=(q[:,1]>y)
            a=p[active];b=q[active]
            crosses=np.sort(a[:,0]+(y-a[:,1])*(b[:,0]-a[:,0])/(b[:,1]-a[:,1]))
            need(len(crosses)%2==0,'TILE_SCANLINE_ODD_BOUNDARY')
            for left,right in zip(crosses[0::2],crosses[1::2]):
                start=max(0,int(np.ceil(left*k-.5)));end=min(size,int(np.ceil(right*k-.5)))
                if end>start:mask[yy,start:end]=fill
    return mask.reshape(n*scale,aa,n*scale,aa).mean(axis=(1,3)).round().astype(np.uint8)


def planned_outline(plan):
    """Explicit prepainting geometry is not an enlarged pixel silhouette."""
    need(isinstance(plan,dict) and plan.get('schema')=='h3.tile-geometry.functional-outline.v1'
         and plan.get('role')=='PREPAINTING_FUNCTIONAL_GEOMETRY','TILE_OUTLINE_PLAN_REQUIRED')
    values=plan.get('contours_1x');need(isinstance(values,list) and 1<=len(values)<=16,'TILE_OUTLINE_CONTOURS_INVALID')
    polys=[]
    for contour in values:
        need(isinstance(contour,list) and 3<=len(contour)<=1024,'TILE_OUTLINE_VERTEX_LIMIT')
        need(all(isinstance(xy,list) and len(xy)==2 and all(type(v) in (int,float) for v in xy) for xy in contour),
             'TILE_OUTLINE_COORDINATES_INVALID')
        p=np.asarray(contour,dtype=float)
        need(np.isfinite(p).all() and abs(area(p))>1e-6,'TILE_OUTLINE_FINITE_AREA_REQUIRED')
        polys.append(p)
    need(area(polys[0])>0,'TILE_OUTLINE_OUTER_WINDING_REQUIRED')
    return polys


def design(native_alpha,geometry,ports,interval,work_scale=32,depth=4,smoothing=2,outline_plan=None):
    n=geometry['canvas_1x'][0]
    need(geometry['canvas_1x']==[n,n] and native_alpha.shape==(n,n) and 8<=n<=128,'TILE_NATIVE_CANVAS_INVALID')
    need(type(work_scale) is int and 4<=work_scale<=64,'TILE_WORK_SCALE_INVALID')
    ox,oy=geometry['origin_1x'];cw,ch=geometry['crop_1x']
    need(all(type(v) is int for v in (ox,oy,cw,ch)) and min(ox,oy)>=0 and min(cw,ch)>0
         and ox+cw<=n and oy+ch<=n,'TILE_NATIVE_CROP_INVALID')
    need(len(set(ports))==len(ports) and set(ports)<=set(SIDES),'TILE_PORT_SET_INVALID')
    for side in ports:
        need({'top':oy==0,'bottom':oy+ch==n,'left':ox==0,'right':ox+cw==n}[side],'TILE_PORT_NOT_ON_GAME_GRID')
    mask=native_alpha>=250;inside=np.zeros_like(mask);inside[oy:oy+ch,ox:ox+cw]=True
    need(not (mask&~inside).any(),'TILE_NATIVE_ALPHA_EXCEEDS_CROP')
    if outline_plan is None:
        pad=depth+2;padded=port_mask(mask,ports,interval,depth,pad)
        polys=[smooth(np.asarray(p)-pad,smoothing) for p in trace_contours(padded)]
    else:polys=planned_outline(outline_plan)
    # Rasterize geometry at twice the declared work grid for antialiasing.
    # This is not a native alpha nearest-neighbor enlargement.
    alpha=rasterize(polys,n,work_scale)
    # A geometry bug must not be hidden by cropping the rendered result.
    allowed=np.zeros_like(alpha,bool);allowed[oy*work_scale:(oy+ch)*work_scale,ox*work_scale:(ox+cw)*work_scale]=True
    need(not (alpha[~allowed]>0).any(),'TILE_DESIGNED_GEOMETRY_EXCEEDS_NATIVE_CROP')
    if outline_plan is not None:
        lo,hi=interval;profile=np.zeros(n*work_scale,np.uint8);profile[lo*work_scale:hi*work_scale]=255
        edges={'top':alpha[0,:],'bottom':alpha[-1,:],'left':alpha[:,0],'right':alpha[:,-1]}
        for side,edge in edges.items():
            need(np.array_equal(edge,profile if side in ports else np.zeros_like(profile)),
                 'TILE_PLANNED_OUTLINE_FUNCTIONAL_PORT_DIFFERS')
    rgb=neutral_pattern(n,work_scale);rgba=np.dstack((rgb,alpha));rgba[alpha==0,:3]=0
    sampled=(alpha.reshape(n,work_scale,n,work_scale).mean(axis=(1,3))>=128)
    union=int((mask|sampled).sum());intersection=int((mask&sampled).sum())
    return rgba,{'contours':[[[round(float(v),6) for v in xy] for xy in p] for p in polys],
        'smooth_iterations':smoothing if outline_plan is None else 0,
        'outline_design_authority':'LEGACY_PIXEL_CONTOUR_NOT_ART_READY' if outline_plan is None else 'PLANNED_FUNCTIONAL_OUTLINE',
        'outline_design':outline_plan,'shared_port_depth_1x':depth,'port_interval_1x':interval,
        'macro_foreground_IoU_diagnostic':intersection/max(1,union),
        'crop_contains_designed_subject_without_post_crop':True,'neutral_fixture':True,
        'native_RGB_used':False,'finished_painting_warped':False,'art_approved':False}


def execute(action,manifest,expected,outdir,operation):
    p,raw=pin({'path':str(Path(manifest).resolve()),'sha256':expected});doc=json.loads(raw)
    need(doc.get('schema')==SCHEMA and doc.get('operation_id')==operation,'TILE_MANIFEST_OR_OPERATION_INVALID')
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',operation) is not None,'TILE_OPERATION_ID_INVALID')
    outdir=Path(outdir).resolve()
    need(outdir.parent==PRODUCT/'build/experiments/tile-geometry' and outdir.name==operation,'TILE_OUTPUT_SCOPE_INVALID')
    need(isinstance(doc['items'],list) and 1<=len(doc['items'])<=256,'TILE_ITEM_LIMIT')
    ids=set();rendered=[]
    for item in doc['items']:
        name=item['id'];need(isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',name) and name not in ids,'TILE_ID_INVALID_OR_DUPLICATE');ids.add(name)
        _,source=pin(item['native_geometry_reference'])
        with Image.open(io.BytesIO(source)) as image:
            need(image.format=='PNG','TILE_PNG_REFERENCE_REQUIRED');alpha=np.asarray(image.convert('RGBA'))[...,3]
        try:
            rgba,diagnostics=design(alpha,item['native_geometry'],item['functional_ports'],doc['port_interval_1x'],
                doc['work_scale'],doc['port_depth_1x'],doc['smooth_iterations'],item.get('outline_design'))
        except ValueError as error:raise ValueError(name+': '+str(error)) from error
        rendered.append((item,rgba,diagnostics))
    if action=='check':return {'status':'READY_GEOMETRY_TEST_ONLY','items':len(rendered),'images_written':0,'art_approved':False}
    receipt_path=outdir/'geometry-receipt.json'
    if action=='verify':
        receipt=json.loads(receipt_path.read_bytes())
        need(receipt['manifest_sha256']==expected and receipt['algorithm_sha256']==sha(Path(__file__).read_bytes()),'TILE_REPLAY_CONTRACT_DRIFT')
        outputs={r['id']:r for r in receipt['outputs']};need(len(outputs)==len(rendered),'TILE_REPLAY_ITEM_COUNT')
        for item,rgba,diag in rendered:
            recorded=outputs.pop(item['id']);_,png=pin(recorded['image'])
            with Image.open(io.BytesIO(png)) as image:actual=np.asarray(image.convert('RGBA'))
            need(np.array_equal(actual,rgba) and recorded['diagnostics']==diag,'TILE_GEOMETRY_REPLAY_DIFFERS')
        return {'status':'VERIFIED_GEOMETRY_TEST_ONLY','items':len(rendered),'images_written':0,'art_approved':False}
    need(action=='run' and not outdir.exists(),'TILE_NEW_OPERATION_REQUIRED')
    outdir.mkdir(parents=True);outputs=[]
    for item,rgba,diag in rendered:
        target=outdir/'neutral-fixtures'/(item['id']+'.png');target.parent.mkdir(exist_ok=True)
        with target.open('xb') as f:Image.fromarray(rgba).save(f,format='PNG')
        outputs.append({'id':item['id'],'native_key':item['native_key'],'geometry':item['native_geometry'],
            'image':{**ref(target),'pixels':[rgba.shape[1],rgba.shape[0]]},'work_scale':doc['work_scale'],
            'role':'NEUTRAL_GEOMETRY_FIXTURE_NOT_ART_MASTER','diagnostics':diag})
    pin({'path':str(p),'sha256':expected})
    receipt={'schema':'h3.tile-geometry.design-receipt.v1','manifest_path':str(p),'manifest_sha256':expected,
        'algorithm_sha256':sha(Path(__file__).read_bytes()),'outputs':outputs,'new_model_calls':0,
        'native_RGB_used_as_paint':False,'art_approved':False,'formal3X_exported':False,'game_deployed':False}
    write_new(receipt_path,receipt)
    return {'status':'GEOMETRY_TEST_FIXTURES_CREATED','items':len(rendered),'receipt':str(receipt_path),'art_approved':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('check','run','verify'))
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--outdir',type=Path,required=True);p.add_argument('--operation-id',required=True)
    a=p.parse_args(argv)
    try:result=execute(a.action,a.manifest,a.manifest_sha256,a.outdir,a.operation_id)
    except (ValueError,KeyError,OSError,TypeError) as e:
        print(json.dumps({'status':'BLOCKED','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
