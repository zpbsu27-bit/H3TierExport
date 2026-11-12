"""Read-only 3X sampling proof for explicitly neutral tile fixtures.

All opposing functional ports are tested, even when native edge profiles differ.
This proves the declared geometry/marker construction, not painted art or a game
hook. No source, candidate, formal asset, or saved preview image is changed.
"""
from __future__ import annotations
import argparse
from collections import deque
import hashlib
import io
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image

from tile_geometry_design import SIDES, need, pin, ref, write_new

FLIPS=('none','horizontal','vertical','both')


def flip(array,which):
    need(which in FLIPS,'TILE_FLIP_INVALID')
    if which in ('horizontal','both'):array=array[:,::-1]
    if which in ('vertical','both'):array=array[::-1]
    return array


def ports_after(ports,which):
    result=set(ports)
    for enabled,a,b in ((which in ('horizontal','both'),'left','right'),
                         (which in ('vertical','both'),'top','bottom')):
        if enabled:result={b if p==a else a if p==b else p for p in result}
    return result


def band(array,side,depth=1):
    if side=='top':return array[:depth]
    if side=='bottom':return array[-depth:][::-1]
    if side=='left':return array[:,:depth].transpose(1,0,2)
    return array[:,-depth:][:,::-1].transpose(1,0,2)


def connected(mask,points):
    if not points or any(not mask[y,x] for y,x in points):return False
    seen={points[0]};q=deque(seen);h,w=mask.shape
    while q:
        y,x=q.popleft()
        for dy,dx in ((0,1),(0,-1),(1,0),(-1,0),(1,1),(1,-1),(-1,1),(-1,-1)):
            p=(y+dy,x+dx)
            if 0<=p[0]<h and 0<=p[1]<w and p not in seen and mask[p]:seen.add(p);q.append(p)
    return all(p in seen for p in points)


def analyze(tiles,interval,scale=3):
    need(tiles and scale==3,'TILE_ACTUAL3X_REQUIRED');states=[];topology=[];profiles=[];bad_edges=[]
    for tile in tiles:
        image=tile['rgba'];n=tile['geometry']['canvas_1x'][0];size=n*scale
        need(image.shape==(size,size,4) and image.dtype==np.uint8,'TILE_REGISTERED_CANVAS_INVALID')
        ports=tile['functional_ports'];points=[];center=int((interval[0]+interval[1])*scale/2)
        expected=np.zeros(size,np.uint8);expected[interval[0]*scale:interval[1]*scale]=255
        for side in SIDES:
            edge=band(image,side)[0];target=expected if side in ports else np.zeros_like(expected)
            if not np.array_equal(edge[:,3],target):bad_edges.append({'id':tile['id'],'side':side})
            if side in ports:
                points.append({'top':(0,center),'bottom':(size-1,center),'left':(center,0),'right':(center,size-1)}[side])
                profiles.append({'id':tile['id'],'side':side,'RGBA_sha256':hashlib.sha256(edge.tobytes()).hexdigest(),
                    'opaque_pixels':int((edge[:,3]==255).sum())})
        if not connected(image[...,3]>=128,points):topology.append(tile['id'])
        for which in FLIPS:states.append({'id':tile['id'],'flip':which,'rgba':flip(image,which),'ports':ports_after(ports,which)})
    pairs=0;failures=[];maximum=0;maximum_band=0
    for side_a,side_b in (('right','left'),('bottom','top')):
        for a in states:
            if side_a not in a['ports']:continue
            for b in states:
                if side_b not in b['ports']:continue
                pairs+=1
                aa=band(a['rgba'],side_a,3).astype(np.int16);bb=band(b['rgba'],side_b,3).astype(np.int16)
                delta=np.abs(aa-bb);edge=int(delta[0].max());near=int(delta.max())
                maximum=max(maximum,edge);maximum_band=max(maximum_band,near)
                if edge or near:failures.append({'a':[a['id'],a['flip'],side_a],
                    'b':[b['id'],b['flip'],side_b],'max_edge_RGBA_difference':edge,'max_three_row_RGBA_difference':near})
    return {'native_slots':len(tiles),'flip_states':len(states),'opposing_functional_pairs':pairs,
        'edge_RGBA_max_difference':maximum,'three_row_interface_band_RGBA_max_difference':maximum_band,
        'mismatched_pair_count':len(failures),'first_mismatched_pairs':failures[:20],
        'bad_declared_edge_profiles':bad_edges,'topology_failures':topology,'functional_port_profiles':profiles,
        'passed':not(failures or bad_edges or topology),'native_equal_profile_pair_filter_used':False,
        'scope':'neutral fixtures: actual3X coordinates, full RGBA and near-edge markers, all four flip flags',
        'art_or_runtime_acceptance':False}


def half_tile_reference(tiles,offset_1x,split_1x,scale=3):
    """Independent whole-overlay vs half-tile dispatch, all geometry states.

    This tests the declared community/reference coordinate rule. It is not an
    execution trace from the original game and must never be labeled as such.
    """
    n=tiles[0]['geometry']['canvas_1x'][0]
    need(offset_1x==split_1x==n//2 and n%2==0,'TILE_HALF_SPLIT_CONTRACT_INVALID')
    states=[flip(t['rgba'],f) for t in tiles for f in FLIPS];size=n*scale;cut=split_1x*scale
    width=4;height=(len(states)+width-1)//width;blank=np.zeros_like(states[0])
    cells=[states[i] if i<len(states) else blank for i in range(width*height)]
    whole=np.zeros((height*size,width*size,4),np.uint8);parts=np.zeros_like(whole)
    for i,image in enumerate(cells):
        x=(i%width)*size;y=(i//width)*size+cut;bottom=min(y+size,len(whole))
        if bottom>y:whole[y:bottom,x:x+size]=image[:bottom-y]
    for i,image in enumerate(cells):
        x=(i%width)*size;y=(i//width)*size
        if i>=width:parts[y:y+cut,x:x+size]=cells[i-width][cut:]
        parts[y+cut:y+size,x:x+size]=image[:cut]
    mismatch=int(np.any(whole!=parts,axis=2).sum())
    return {'flip_states':len(states),'simulated_map_cells':len(cells),'offset_native':offset_1x,
        'split_native':split_1x,'actual3X_offset':cut,'mismatched_RGBA_pixels':mismatch,'passed':mismatch==0,
        'scope':'reference coordinate simulation only','game_runtime_verified':False}


def load_tiles(context,run):
    need(run.get('schema')=='h3.motion.run.v1' and run.get('failed')==0 and run.get('target_scale')==3,'TILE_SAMPLING_RUN_INVALID')
    outputs={r['id']:r for r in run['results']};items=context['items']
    need(len(outputs)==len(items) and set(outputs)=={r['id'] for r in items},'TILE_SAMPLING_SLOT_SET_DIFFERS')
    tiles=[]
    for item in items:
        row=outputs[item['id']];receipt_path=Path(row['receipt']);receipt=json.loads(receipt_path.read_bytes());g=item['geometry']
        source=pin(item['fixture']);output=Path(row['output']);raw=output.read_bytes()
        need(receipt['target_scale']==3 and receipt['png_sha256']==hashlib.sha256(raw).hexdigest(),'TILE_SAMPLED_FILE_OR_SCALE_DRIFT')
        need(receipt['contract']['native_entry']['key']==item['native_key']
             and receipt['contract']['native_entry']['targets']==g,'TILE_SAMPLER_NATIVE_BINDING_DIFFERS')
        layers=receipt['contract']['entry']['layers']
        if context.get('role')=='NEUTRAL_COMPONENT_COMPOSITION_PROOF':
            _,component_raw=pin(context['component_receipt']);component=json.loads(component_raw)
            need(component.get('role')=='NEUTRAL_TEST_COMPONENT','TILE_SAMPLER_TEST_COMPONENT_ROLE_REQUIRED')
            primary=[l for l in layers if l['kind']=='surface'];parts=[l for l in layers if l['kind']=='component']
            need(len(primary)==1 and len(parts)==len(item['functional_ports']) and len(layers)==len(parts)+1,
                 'TILE_SAMPLER_COMPONENT_LAYER_SET_DIFFERS')
            need(primary[0]['source_a']['sha256']==item['fixture']['sha256']
                 and primary[0]['source_a']['key']==item['native_key'],'TILE_SAMPLER_OWN_INTERIOR_BINDING_DIFFERS')
            for part in parts:
                need(part['source_a']['sha256']==component['component_image']['sha256']
                     and part['component_contract']['receipt']==context['component_receipt'],
                     'TILE_SAMPLER_PUBLIC_COMPONENT_BINDING_DIFFERS')
        else:
            need(len(layers)==1 and layers[0]['source_a']['sha256']==item['fixture']['sha256'],'TILE_SAMPLER_FIXTURE_BINDING_DIFFERS')
        with Image.open(io.BytesIO(raw)) as im:crop=np.asarray(im.convert('RGBA'))
        x,y=g['origin_1x'];w,h=g['crop_1x'];cw,ch=g['canvas_1x']
        need(crop.shape==(h*3,w*3,4) and receipt['origin_3x']==[x*3,y*3],'TILE_EXACT3X_CROP_OR_ORIGIN_DIFFERS')
        full=np.zeros((ch*3,cw*3,4),np.uint8);full[y*3:(y+h)*3,x*3:(x+w)*3]=crop
        tiles.append({**item,'rgba':full,'sampled_image':ref(output),'sampling_receipt':ref(receipt_path)})
    return tiles


def verify(context_binding,run_binding,out):
    _,raw=pin(context_binding);context=json.loads(raw);_,raw=pin(run_binding);run=json.loads(raw)
    need(context.get('schema') in ('h3.map.geometry-probe-context.v1','h3.tile-geometry.sampling-context.v1')
         and context.get('art_approved') is False,'TILE_NEUTRAL_CONTEXT_REQUIRED')
    _,raw=pin(context['geometry_fixture_receipt']);fixture=json.loads(raw)
    _,raw=pin({'path':fixture['manifest_path'],'sha256':fixture['manifest_sha256']});manifest=json.loads(raw)
    tiles=load_tiles(context,run);report=analyze(tiles,manifest['port_interval_1x'])
    rule=context.get('consumer_projection')
    if rule is None:
        # Read-only compatibility for the first development fixture context.
        old=context['reference_consumer'];rule={'kind':'vertical-half-tile',
            'y_offset_1x':old['road_y_offset_1x'],'source_split_y_1x':old['split_source_y_1x']}
    need(rule.get('kind')=='vertical-half-tile','TILE_CONSUMER_PROJECTION_UNSUPPORTED')
    simulation=half_tile_reference(tiles,rule['y_offset_1x'],rule['source_split_y_1x'])
    report.update(schema='h3.tile-geometry.actual3X-proof.v1',context=context_binding,sampling_run=run_binding,
        reference_consumer=simulation,actual_images=[{'id':r['id'],'image':r['sampled_image'],'receipt':r['sampling_receipt']} for r in tiles],
        passed=report['passed'] and simulation['passed'],new_model_calls=0,images_written=0,game_deployed=False)
    pin(context_binding);pin(run_binding);write_new(Path(out).resolve(),report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',type=Path,required=True)
    p.add_argument('--context-sha256',required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--run-sha256',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args(argv)
    try:r=verify({'path':str(a.context.resolve()),'sha256':a.context_sha256},{'path':str(a.run.resolve()),'sha256':a.run_sha256},a.out)
    except (ValueError,KeyError,OSError,TypeError) as e:
        print(json.dumps({'status':'BLOCKED','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
    print(json.dumps({k:r[k] for k in ('passed','native_slots','flip_states','opposing_functional_pairs','edge_RGBA_max_difference','three_row_interface_band_RGBA_max_difference','reference_consumer')},ensure_ascii=False))
    return 0 if r['passed'] else 2


if __name__=='__main__':raise SystemExit(main())
