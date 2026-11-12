"""Pixel-preserving public interface components and prepainting references.

Raw model outputs remain untouched. Component construction is an explicit
integer ROI plus pixel permutation, never resizing, RGB averaging or extrusion.
The native motion sampler later places the component by a typed rigid transform.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import numpy as np
from PIL import Image,ImageDraw

from tile_geometry_design import pin,ref,need,write_new

PRODUCT=resolve_tool_root(__file__)


def plain_output(path):
    path=Path(path).resolve()
    need(path.parent==PRODUCT/'build/experiments/tile-interfaces'
         and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',path.name),'INTERFACE_OUTPUT_SCOPE')
    need(not path.exists(),'INTERFACE_NEW_OPERATION_REQUIRED')
    return path


def load_image(binding):
    _,raw=pin(binding)
    with Image.open(io.BytesIO(raw)) as image:
        need(image.format=='PNG','INTERFACE_PNG_REQUIRED');rgba=np.asarray(image.convert('RGBA')).copy()
    if 'pixels' in binding:need(binding['pixels']==[rgba.shape[1],rgba.shape[0]],'INTERFACE_SOURCE_PIXELS_DRIFT')
    return rgba


def geometry_reference(receipt_binding,key,outdir,blueprint):
    _,raw=pin(receipt_binding);receipt=json.loads(raw)
    need(receipt.get('schema')=='h3.tile-geometry.design-receipt.v1','INTERFACE_GEOMETRY_RECEIPT_REQUIRED')
    matches=[r for r in receipt['outputs'] if r['native_key']==key]
    need(len(matches)==1,'INTERFACE_GEOMETRY_SLOT_AMBIGUOUS')
    source=matches[0];rgba=load_image(source['image']);scale=source['work_scale'];g=source['geometry']
    need(source['role']=='NEUTRAL_GEOMETRY_FIXTURE_NOT_ART_MASTER','INTERFACE_GEOMETRY_DATA_ROLE')
    outdir=plain_output(outdir);alpha=rgba[...,3].copy()
    # A grey clay/layout reference avoids teaching the model our colored test
    # markers. Alpha is copied exactly; nothing is a master or painted texture.
    image=Image.new('RGBA',(rgba.shape[1],rgba.shape[0]),(175,175,175,255));draw=ImageDraw.Draw(image)
    if blueprint is not None:
        need(blueprint.get('schema')=='h3.interface.stone-layout.v1','INTERFACE_BLUEPRINT_SCHEMA')
        need(g['canvas_1x']==[32,32] and blueprint['axis']=='vertical','INTERFACE_BLUEPRINT_COORDINATES')
        for stone in blueprint['stones']:
            x,y,w,h=stone['rect_1x'];radius=stone.get('radius_1x',.12)
            need(all(type(v) in (int,float) for v in (x,y,w,h,radius)) and w>0 and h>0,'INTERFACE_STONE_RECT_INVALID')
            draw.rounded_rectangle([x*scale,y*scale,(x+w)*scale-1,(y+h)*scale-1],radius=radius*scale,
                fill=(205,205,205,255),outline=(135,135,135,255),width=max(1,round(.07*scale)))
    image.putalpha(Image.fromarray(alpha));reference=np.asarray(image).copy()
    reference[alpha==0,:3]=0;image=Image.fromarray(reference)
    outdir.mkdir(parents=True);target=outdir/'shape-and-component-reference.png'
    with target.open('xb') as f:image.save(f,format='PNG')
    evidence={'schema':'h3.tile-interface.geometry-reference.v1','source':source['image'],'geometry_receipt':receipt_binding,
        'algorithm_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'native_key':key,'geometry':g,'pixels_per_native':scale,'image':{**ref(target),'pixels':list(image.size)},
        'outline_design_authority':source.get('diagnostics',{}).get('outline_design_authority','LEGACY_PIXEL_CONTOUR_NOT_ART_READY'),
        'alpha_sha256':hashlib.sha256(alpha.tobytes()).hexdigest(),'alpha_exact':bool(np.array_equal(reference[...,3],rgba[...,3])),
        'blueprint':blueprint,'role':'GEOMETRY_REFERENCE_NOT_PAINTING_OR_MASTER','new_model_calls':0,
        'native_RGB_used_as_paint':False,'resampling_performed':False,'art_approved':False}
    write_new(outdir/'geometry-reference.json',evidence);return evidence


def mirror_component(source,half_rect_pixels):
    x,y,w,h=half_rect_pixels
    need(all(type(v) is int for v in (x,y,w,h)) and min(x,y)>=0 and min(w,h)>0,'INTERFACE_INTEGER_ROI_REQUIRED')
    need(x+w<=source.shape[1] and y+h<=source.shape[0],'INTERFACE_ROI_OUTSIDE_SOURCE')
    left=source[y:y+h,x:x+w]
    out=np.concatenate((left,left[:,::-1]),axis=1)
    need(np.array_equal(out,out[:,::-1]),'INTERFACE_TRANSVERSE_SYMMETRY_FAILED')
    return out


def create_component(manifest_binding,outdir):
    _,raw=pin(manifest_binding);m=json.loads(raw)
    need(m.get('schema')=='h3.tile-interface.component-spec.v1','INTERFACE_COMPONENT_SPEC_REQUIRED')
    role=m.get('role');need(role in ('NEUTRAL_TEST_COMPONENT','PAINTED_INTERFACE_COMPONENT'),'INTERFACE_COMPONENT_ROLE_REQUIRED')
    source=load_image(m['source']);k=m['pixels_per_native'];canvas=m['geometry']['canvas_1x']
    need(type(k) is int and 4<=k<=128 and [source.shape[1],source.shape[0]]==[v*k for v in canvas],
         'INTERFACE_REAL_UNIFORM_SOURCE_GRID_REQUIRED')
    need(canvas==[32,32] and m['native_key'][0]=='def','INTERFACE_COMPONENT_NATIVE_GRID')
    sx,sy,w,h=m['half_rect_1x'];target=m['component_rect_1x'];tx,ty,tw,th=target
    need(all(type(v) is int for v in (sx,sy,w,h,tx,ty,tw,th)) and tw==w*2 and th==h,
         'INTERFACE_HALF_TO_WHOLE_DESIGN_INVALID')
    need(min(tx,ty)>=0 and tx+tw<=canvas[0] and ty+th<=canvas[1],'INTERFACE_TARGET_OUTSIDE_CANVAS')
    if role=='PAINTED_INTERFACE_COMPONENT':
        _,evidence_raw=pin(m['original_source_evidence']);evidence=json.loads(evidence_raw)
        need(evidence.get('schema')=='h3.art.original-received-source.v1'
             and evidence.get('original',{}).get('sha256')==m['source']['sha256'],
             'INTERFACE_ORIGINAL_RECEIVED_SOURCE_CUSTODY_REQUIRED')
        text=str(Path(m['source']['path'])).casefold().replace('\\','/')
        need('/codex-3x/' not in text and '/sampler-fixture3x/' not in text and '/neutral-fixtures/' not in text,
             'INTERFACE_TEST_OR_LOW_TIER_IS_NOT_PAINT_SOURCE')
    patch=mirror_component(source,[sx*k,sy*k,w*k,h*k])
    result=np.zeros_like(source);result[ty*k:(ty+th)*k,tx*k:(tx+tw)*k]=patch
    outdir=plain_output(outdir);outdir.mkdir(parents=True);image=outdir/'interface-component.png'
    with image.open('xb') as f:Image.fromarray(result).save(f,format='PNG')
    receipt={'schema':'h3.tile-interface.component-receipt.v1','manifest':manifest_binding,
        'algorithm_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source':{**m['source'],'pixels':[source.shape[1],source.shape[0]]},
        'original_source_evidence':m.get('original_source_evidence'),'native_key':m['native_key'],'geometry':m['geometry'],
        'pixels_per_native':k,'half_rect_1x':m['half_rect_1x'],'component_rect_1x':target,
        'component_image':{**ref(image),'pixels':[result.shape[1],result.shape[0]]},
        'role':role,'source_unchanged':True,'pixel_permutation_only':True,'resampling_performed':False,
        'RGB_averaged':False,'extrusion_performed':False,'transverse_RGBA_symmetric':True,'art_approved':False}
    pin(m['source']);write_new(outdir/'component-receipt.json',receipt);return receipt


def component_layer(receipt_binding,target_rect,turn,identity='shared-interface'):
    """Author a rigid native sampler layer; never sample/resize the source here."""
    _,raw=pin(receipt_binding);r=json.loads(raw)
    need(r.get('schema')=='h3.tile-interface.component-receipt.v1','INTERFACE_COMPONENT_RECEIPT_REQUIRED')
    sx,sy,w,h=r['component_rect_1x'];k=r['pixels_per_native'];tx,ty,tw,th=target_rect
    need(type(turn) is int and 0<=turn<=3 and tw==(h if turn%2 else w) and th==(w if turn%2 else h),
         'INTERFACE_RIGID_TARGET_RECT_REQUIRED')
    vertices=[[0,0],[w*k,0],[0,h*k],[w*k,h*k]];targets=[]
    for u,v in ((0,0),(w,0),(0,h),(w,h)):
        x,y=((u,v),(h-v,u),(w-u,h-v),(v,w-u))[turn];targets.append([tx+x,ty+y])
    return {'id':identity,'kind':'component',
        'source_a':{**r['component_image'],'key':r['native_key'],'source_layout':'explicit-native-roi',
            'source_region':{'pixel_rect':[sx*k,sy*k,w*k,h*k],'native_rect_1x':[sx,sy,w,h],'allow_target_subregion':True}},
        'vertices_a':vertices,'target_vertices_1x':targets,'triangles':[[0,1,2],[1,3,2]],
        'component_contract':{'schema':'h3.motion.rigid-component.v1','receipt':receipt_binding,
                              'quarter_turns_clockwise':turn,'target_rect_1x':target_rect}}


def rational_native_roi_layer(source_binding,key,geometry,pixels_per_native_ratio,native_rect,identity='rational-native-surface'):
    """Build a static surface layer from an exact rational source grid.

    Coordinates are derived from the original canvas and a reduced integer ratio.
    The source PNG is never resized or rewritten; H3TierExport performs the sole
    native 3X sample directly from the pinned parent.
    """
    _,raw=pin(source_binding)
    with Image.open(io.BytesIO(raw)) as image:
        need(image.format=='PNG','INTERFACE_RATIONAL_SOURCE_PNG_REQUIRED')
        pixels=list(image.size)
    need(isinstance(key,list) and len(key)==4 and key[0]=='def','INTERFACE_RATIONAL_NATIVE_KEY_REQUIRED')
    need(isinstance(geometry,dict) and all(k in geometry for k in ('canvas_1x','crop_1x','origin_1x')),
         'INTERFACE_RATIONAL_NATIVE_GEOMETRY_REQUIRED')
    canvas=geometry['canvas_1x'];crop=geometry['crop_1x'];origin=geometry['origin_1x']
    need(all(isinstance(v,list) and len(v)==2 and all(type(n) is int and n>0 for n in v)
             for v in (canvas,crop)) and isinstance(origin,list) and len(origin)==2
             and all(type(n) is int and n>=0 for n in origin),'INTERFACE_RATIONAL_NATIVE_GEOMETRY_INVALID')
    need(isinstance(pixels_per_native_ratio,(list,tuple)) and len(pixels_per_native_ratio)==2
         and all(type(v) is int and v>0 for v in pixels_per_native_ratio),'INTERFACE_RATIONAL_GRID_INVALID')
    numerator,denominator=pixels_per_native_ratio
    need(math.gcd(numerator,denominator)==1,'INTERFACE_RATIONAL_GRID_MUST_BE_REDUCED')
    need(numerator/denominator>=3,'INTERFACE_RATIONAL_SOURCE_BELOW_3X')
    need(all((edge*numerator)%denominator==0 for edge in canvas)
         and pixels==[canvas[0]*numerator//denominator,canvas[1]*numerator//denominator],
         'INTERFACE_RATIONAL_PARENT_CANVAS_MISMATCH')
    need(isinstance(native_rect,(list,tuple)) and len(native_rect)==4
         and all(type(v) is int for v in native_rect),'INTERFACE_RATIONAL_REGION_INVALID')
    x,y,w,h=native_rect
    need(w>0 and h>0 and x>=origin[0] and y>=origin[1]
         and x+w<=origin[0]+crop[0] and y+h<=origin[1]+crop[1],
         'INTERFACE_RATIONAL_REGION_OUTSIDE_NATIVE_CROP')
    need(source_binding.get('sha256') and len(source_binding['sha256'])==64,'INTERFACE_RATIONAL_SOURCE_SHA_REQUIRED')
    scale=numerator/denominator
    left_num=x*numerator;top_num=y*numerator;right_num=(x+w)*numerator;bottom_num=(y+h)*numerator
    left=left_num//denominator;top=top_num//denominator
    right=(right_num+denominator-1)//denominator;bottom=(bottom_num+denominator-1)//denominator
    offset_x=(left_num%denominator)/denominator;offset_y=(top_num%denominator)/denominator
    vertices=[[offset_x,offset_y],[offset_x+w*scale,offset_y],
              [offset_x,offset_y+h*scale],[offset_x+w*scale,offset_y+h*scale]]
    targets=[[x,y],[x+w,y],[x,y+h],[x+w,y+h]]
    source={**source_binding,'key':key,'pixels':pixels,'source_layout':'rational-native-grid',
            'source_region':{'native_rect_1x':[x,y,w,h],
                             'pixels_per_native_ratio':[numerator,denominator],
                             'allow_target_subregion':True}}
    layer={'id':identity,'kind':'surface','source_a':source,'vertices_a':vertices,
           'target_vertices_1x':targets,'triangles':[[0,1,2],[1,3,2]],
           'anchors':[{'vertex':i,'target_1x':point,'tolerance_3x':0} for i,point in enumerate(targets)]}
    return {'schema':'h3.motion.rational-native-roi-layer.v1','native_key':key,'geometry':geometry,
            'source':{**source_binding,'pixels':pixels},'native_rect_1x':[x,y,w,h],
            'pixels_per_native_ratio':[numerator,denominator],
            'pixel_rect_rational':[[left_num//math.gcd(left_num,denominator),denominator//math.gcd(left_num,denominator)],
                                   [top_num//math.gcd(top_num,denominator),denominator//math.gcd(top_num,denominator)],
                                   [w*numerator//math.gcd(w*numerator,denominator),denominator//math.gcd(w*numerator,denominator)],
                                   [h*numerator//math.gcd(h*numerator,denominator),denominator//math.gcd(h*numerator,denominator)]],
            'envelope_pixels':[left,top,right-left,bottom-top],
            'envelope_offset_rational':[[left_num%denominator,denominator],[top_num%denominator,denominator]],
            'layer':layer,'source_unchanged':True,'resampling_before_native_sampler':False,
            'distortion':False,'art_approved':False}


def assembly_layers(body,key,g,body_scale,ports,component_receipt,depth):
    """Declared prepainting joint planes replace endpoints, never fit a bbox."""
    n=g['canvas_1x'][0];need(g['canvas_1x']==[n,n] and type(depth) is int and 1<=depth<=n//4,'INTERFACE_ATTACHMENT_PLAN_INVALID')
    ox,oy=g['origin_1x'];w,h=g['crop_1x'];x0=max(ox,depth) if 'left' in ports else ox
    y0=max(oy,depth) if 'top' in ports else oy;x1=min(ox+w,n-depth) if 'right' in ports else ox+w
    y1=min(oy+h,n-depth) if 'bottom' in ports else oy+h
    need(x1>x0 and y1>y0 and type(body_scale) is int and body_scale>=4,'INTERFACE_PRIMARY_INTERIOR_REQUIRED')
    _,raw=pin(component_receipt);c=json.loads(raw);cw,ch=c['component_rect_1x'][2:]
    need(ch==depth and cw<n and (n-cw)%2==0,'INTERFACE_COMPONENT_ATTACHMENT_SIZE')
    lo=(n-cw)//2;targets=[[x0,y0],[x1,y0],[x0,y1],[x1,y1]]
    surface={'id':'own-slot-interior','kind':'surface',
        'source_a':{**body,'key':key,'source_layout':'explicit-native-roi','source_region':{
            'pixel_rect':[x0*body_scale,y0*body_scale,(x1-x0)*body_scale,(y1-y0)*body_scale],
            'native_rect_1x':[x0,y0,x1-x0,y1-y0],'allow_target_subregion':True}},
        'vertices_a':[[0,0],[(x1-x0)*body_scale,0],[0,(y1-y0)*body_scale],[(x1-x0)*body_scale,(y1-y0)*body_scale]],
        'target_vertices_1x':targets,'triangles':[[0,1,2],[1,3,2]],
        'anchors':[{'vertex':i,'target_1x':xy,'tolerance_3x':0} for i,xy in enumerate(targets)]}
    layers=[surface]
    placements={'top':([lo,0,cw,depth],0),'right':([n-depth,lo,depth,cw],1),
                'bottom':([lo,n-depth,cw,depth],2),'left':([0,lo,depth,cw],3)}
    for side in sorted(ports):
        need(side in placements,'INTERFACE_FUNCTIONAL_SIDE_INVALID');rect,turn=placements[side]
        layers.append(component_layer(component_receipt,rect,turn,'interface-'+side))
    return layers,{'interior_native_rect':[x0,y0,x1-x0,y1-y0],
        'attachment_depth_native':depth,'replacement_regions_declared_before_painting':True,
        'raw_image_modified':False,'source_resampling_steps_before_native_sampler':0,
        'artificial_RGB_blend_or_blur_used':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('reference','component','roi-layer'))
    p.add_argument('--input',type=Path,required=True);p.add_argument('--input-sha256',required=True)
    p.add_argument('--outdir',type=Path,required=True);p.add_argument('--native-key',nargs=4);p.add_argument('--blueprint',type=Path)
    p.add_argument('--geometry-json',type=Path);p.add_argument('--pixels-per-native-ratio',nargs=2,type=int);p.add_argument('--native-rect',nargs=4,type=int)
    a=p.parse_args(argv)
    try:
        binding={'path':str(a.input.resolve()),'sha256':a.input_sha256}
        if a.action=='component':r=create_component(binding,a.outdir)
        elif a.action=='roi-layer':
            need(a.native_key is not None and a.geometry_json is not None and a.pixels_per_native_ratio is not None and a.native_rect is not None,
                 'INTERFACE_RATIONAL_ROI_ARGUMENTS_REQUIRED')
            key=[a.native_key[0],a.native_key[1],int(a.native_key[2]),int(a.native_key[3])]
            geometry=json.loads(a.geometry_json.read_bytes())
            r=rational_native_roi_layer(binding,key,geometry,a.pixels_per_native_ratio,a.native_rect)
            outdir=plain_output(a.outdir);outdir.mkdir(parents=True)
            write_new(outdir/'rational-roi-layer.json',r)
            r={'schema':r['schema'],'role':'SOURCE_BOUND_NATIVE_ROI_LAYER','art_approved':False,
               'output':str(outdir/'rational-roi-layer.json'),'native_rect_1x':r['native_rect_1x'],
               'pixels_per_native_ratio':r['pixels_per_native_ratio'],'envelope_pixels':r['envelope_pixels']}
        else:
            need(a.native_key is not None,'INTERFACE_REFERENCE_NATIVE_KEY_REQUIRED')
            key=[a.native_key[0],a.native_key[1],int(a.native_key[2]),int(a.native_key[3])]
            blueprint=json.loads(a.blueprint.read_bytes()) if a.blueprint else None
            r=geometry_reference(binding,key,a.outdir,blueprint)
        print(json.dumps({k:r[k] for k in ('schema','role','art_approved')},ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as e:
        print(json.dumps({'status':'BLOCKED','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
