"""Read pinned H3M tile tables and review existing actual-3X crops on that grid.

This is a companion review, not a replacement H3M parser or game renderer.
The verified terrain-table offset is explicit; it is never guessed by scanning.
No PNGs, resized masters, palette DEFs or game deployments are produced.
"""
from __future__ import annotations
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys

from PIL import Image
import numpy as np
import subject_scale_layout as common

TERRAINS = ('DIRTTL','SANDTL','GRASTL','SNOWTL','SWMPTL','ROUGTL','SUBBTL','LAVATL','WATRTL','ROCKTL')
ROADS = (None,'DIRTRD','GRAVRD','COBBRD')
RIVERS = (None,'CLRRVR','ICYRVR','MUDRVR','LAVRVR')
DRAW_CONTRACT = {'logical_cell':[32,32], 'publish_cell':[96,96],
    'road_origin_in_logical_cell':[0,16], 'river_origin_in_logical_cell':[0,0],
    'terrain_origin_in_logical_cell':[0,0], 'road_source_half_height_3x':48,
    'layers':['terrain','river','road'], 'palette_or_water_animation_verified':False,
    'objects_and_runtime_verified':False}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def h3_experiment_root():
    """Resolve persistent review output through this tool's own build/experiments root."""
    from tool_paths import resolve_tool_root
    return resolve_tool_root(__file__).resolve()/'build'/'experiments'

def read_tiles(binding, layout):
    path, raw = common.pin(binding)
    data = gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw
    common.need(digest(data)==layout['body_sha256'],'H3M_BODY_SHA_DRIFT')
    version,players,width,layers = struct.unpack_from('<I?I?',data)
    common.need(version in (14,21,28),'H3M_FORMAT_NOT_SUPPORTED')
    common.need(width==layout['width']==layout['height'] and 1<=width<=252,'H3M_DIMENSIONS_DIFFER')
    common.need(1+int(layers)==layout['layers'],'H3M_LAYER_COUNT_DIFFERS')
    offset=layout['terrain_offset'];size=width*width*layout['layers']*7
    common.need(type(offset) is int and 10<=offset and offset+size<=len(data),'H3M_TERRAIN_TABLE_OUTSIDE_FILE')
    table=data[offset:offset+size]
    common.need(digest(table)==layout['terrain_table_sha256'],'H3M_TERRAIN_TABLE_SHA_DRIFT')
    a=np.frombuffer(table,dtype=np.uint8).reshape(layout['layers'],width,width,7)
    common.need(np.all(a[:,:,:,0]<10) and np.all(a[:,:,:,2]<5) and np.all(a[:,:,:,4]<4),'H3M_TILE_TYPE_INVALID')
    common.need(np.all(a[:,:,:,5][a[:,:,:,4]>0]<17),'H3M_ROAD_FRAME_INVALID')
    return path,raw,data,a.copy()

def transform(array, flags, shift):
    """H3 map bitfields: first bit mirrors X, second mirrors Y."""
    if flags & (1<<shift):array=array[:,::-1]
    if flags & (2<<shift):array=array[::-1,:]
    return array.copy()

def road_position(x,y,scale=3):
    return x*32*scale,(y*32+16)*scale

def tile_draw_commands(tiles,x,y,z,scale=3,offset=(0,0)):
    """Editor consumer geometry, including the prior row at the viewport edge.

    Mirror the complete canvas first, then take its upper or lower half.
    Coordinates in this function are logical pixels multiplied by scale.
    """
    common.need(scale in (1,3),'MAP_DRAW_SCALE_INVALID')
    t,tf,v,vf,r,rf,flags=map(int,tiles[z,y,x]);cx=x*32*scale-offset[0];cy=y*32*scale-offset[1]
    size=32*scale;half=16*scale;draws=[]
    def add(category,family,frame,bits,src,dst,cell):
        draws.append({'category':category,'key':['def',family,0,frame],'flags':bits,
            'source_rect':src,'destination':dst,'source_cell':cell})
    add('terrain',TERRAINS[t],tf,flags&3,[0,0,size,size],[cx,cy],[x,y,z])
    if v:add('river',RIVERS[v],vf,(flags>>2)&3,[0,0,size,size],[cx,cy],[x,y,z])
    if y>0:
        above=tiles[z,y-1,x];ar,af,abits=map(int,above[4:7])
        if ar:add('road',ROADS[ar],af,(abits>>4)&3,[0,half,size,half],[cx,cy],[x,y-1,z])
    if r:add('road',ROADS[r],rf,(flags>>4)&3,[0,0,size,half],[cx,cy+half],[x,y,z])
    return draws

def geometry_rows(binding):
    p,raw=common.pin(binding)
    inventory=json.loads(gzip.decompress(raw) if p.suffix=='.gz' else raw)
    return {('def',r['identity'],r['group'],r['frame']):r for r in inventory['rows'] if r.get('kind')=='def'}

def full_crop(ref,geometry):
    p,_=common.pin(ref)
    with Image.open(p) as image:
        image.load();common.need(image.format=='PNG','MAP_3X_SOURCE_NOT_PNG')
        common.need(list(image.size)==[v*3 for v in geometry['crop']],'MAP_3X_NATIVE_CROP_DIFFERS')
        a=np.asarray(image.convert('RGBA')).copy()
    w,h=(v*3 for v in geometry['canvas']);x,y=(v*3 for v in geometry['anchor'])
    common.need(0<=x and 0<=y and x+a.shape[1]<=w and y+a.shape[0]<=h,'MAP_3X_ORIGIN_OUTSIDE_CANVAS')
    out=np.zeros((h,w,4),dtype=np.uint8);out[y:y+a.shape[0],x:x+a.shape[1]]=a
    return out

def edge_diagnostics(a,b,direction):
    ea,eb=(a[-1],b[0]) if direction=='south' else (a[:,-1],b[:,0])
    support=(ea[:,3]>0)|(eb[:,3]>0)
    opaque=(ea[:,3]>=250)&(eb[:,3]>=250)
    return {'alpha_support_xor_pixels':int(np.count_nonzero((ea[:,3]>0)!=(eb[:,3]>0))),
        'alpha_max_delta':int(np.abs(ea[:,3].astype(int)-eb[:,3].astype(int)).max(initial=0)),
        'rgba_max_delta':int(np.abs(ea[support].astype(int)-eb[support].astype(int)).max(initial=0)),
        'opaque_rgb_mean_delta':float(np.abs(ea[opaque,:3].astype(float)-eb[opaque,:3]).mean()) if opaque.any() else None,
        'surface_art_acceptance':'NOT_MEASURED',
        'reason':'DIAGNOSTIC_ONLY_REQUIRES_BOUND_SHARED_INTERFACE_AND_SCENE_REVIEW'}

def make_fixture(spec,outdir):
    _,_,data,a=read_tiles(spec['map'],spec['map_layout']);layout=dict(spec['map_layout'])
    x0,y0,x1,y1=spec['viewport'];z=spec.get('level',0)
    common.need(0<=x0<x1<=layout['width'] and 0<=y0<y1<=layout['height'] and 0<=z<layout['layers'],'MAP_VIEWPORT_INVALID')
    a[z,y0:y1,x0:x1]=[2,0,0,0,0,0,0]
    used=set()
    for row in spec['placements']:
        x,y=row['cell'];f=row['frame'];flags=row.get('mirror_bits',0)
        common.need(x0<=x<x1 and y0<=y<y1 and (x,y) not in used,'MAP_PLACEMENT_INVALID_OR_DUPLICATE')
        common.need(type(f) is int and 0<=f<17 and type(flags) is int and 0<=flags<4,'MAP_ROAD_STATE_INVALID')
        used.add((x,y));a[z,y,x,4:7]=[3,f,flags<<4]
    table=a.tobytes();offset=layout['terrain_offset']
    body=data[:offset]+table+data[offset+len(table):]
    common.need(body[:offset]==data[:offset] and body[offset+len(table):]==data[offset+len(table):],'MAP_NON_TERRAIN_BYTES_CHANGED')
    outdir.mkdir(parents=True,exist_ok=False)
    target=outdir/'road-grid-fixture.h3m';raw=gzip.compress(body,mtime=0);target.write_bytes(raw)
    layout.update(body_sha256=digest(body),terrain_table_sha256=digest(table))
    result={**spec,'schema':'h3.map-assembly-review.v1','map':{'path':str(target.resolve()),'sha256':digest(raw)},'map_layout':layout,
        'fixture_parent':spec['map'],'only_terrain_table_changed':True,'game_runtime_accepted':False}
    (outdir/'scene.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def review(spec,outdir):
    _,_,_,tiles=read_tiles(spec['map'],spec['map_layout']);native=geometry_rows(spec['native_inventory'])
    x0,y0,x1,y1=spec['viewport'];z=spec.get('level',0);w=spec['map_layout']['width']
    common.need(0<=x0<x1<=w and 0<=y0<y1<=w and 0<=z<tiles.shape[0],'MAP_VIEWPORT_INVALID')
    common.need((x1-x0)*(y1-y0)<=4096,'MAP_REVIEW_VIEWPORT_LIMIT')
    hd_layers=spec.get('actual3x_layers',['road'])
    common.need(isinstance(hd_layers,list) and hd_layers and set(hd_layers).issubset({'road','river'}),'MAP_ACTUAL3X_LAYER_SELECTION_INVALID')
    root=Path(spec['native_frame_root']);assets={};paint={};road_cache={};river_cache={};missing=[];missing_rivers=[];cells=[];native_pins=[];draws=[];context=set()
    for row in spec.get('actual3x',[]):
        k=tuple(row['key']);common.need(k in native and k not in paint,'MAP_3X_SLOT_INVALID_OR_DUPLICATE')
        paint[k]=(row,full_crop(row['image'],native[k]))
    def image_ref(k):
        ident=f'{k[1]}/{k[2]}_{k[3]}'
        if ident not in assets:
            p=root/k[1]/f'{k[2]}_{k[3]}.png';raw=p.read_bytes()
            with Image.open(p) as im:common.need(list(im.size)==native[k]['canvas'],'MAP_NATIVE_IMAGE_GEOMETRY_DIFFERS')
            native_pins.append({'path':str(p.resolve()),'sha256':digest(raw),'key':list(k)})
            assets[ident]={'native':'data:image/png;base64,'+base64.b64encode(raw).decode(),'native_size':native[k]['canvas']}
            if k in paint:
                row,_=paint[k];_,hd=common.pin(row['image']);g=native[k]
                assets[ident].update(hd='data:image/png;base64,'+base64.b64encode(hd).decode(),origin=[v*3 for v in g['anchor']],crop=[v*3 for v in g['crop']])
        return ident
    for y in range(y0,y1):
        for x in range(x0,x1):
            terrain,tf,river,rf,road,rr,flags=map(int,tiles[z,y,x]);layers=[]
            for category,family,frame,shift in (('terrain',TERRAINS[terrain],tf,0),('river',RIVERS[river],rf,2),('road',ROADS[road],rr,4)):
                if family is None:continue
                k=('def',family,0,frame);common.need(k in native,'MAP_TILE_SLOT_NOT_IN_INVENTORY')
                layers.append({'category':category,'image':image_ref(k),'flags':(flags>>shift)&3,'key':list(k)})
                if category in hd_layers:
                    target_cache=road_cache if category=='road' else river_cache
                    if k in paint:target_cache[x,y]=transform(paint[k][1],flags,shift)
                    elif category=='road':missing.append({'cell':[x,y,z],'key':list(k)})
                    else:missing_rivers.append({'cell':[x,y,z],'key':list(k)})
            cells.append({'x':x,'y':y,'layers':layers})
            for draw in tile_draw_commands(tiles,x,y,z,offset=(x0*96,y0*96)):
                draw['image']=image_ref(tuple(draw['key']));draws.append(draw)
                if draw['source_cell'][1]<y0:context.add(tuple(draw['source_cell']))
    def measure(cache,category):
        rows=[]
        for (x,y),a in cache.items():
            for dx,dy,direction in ((1,0,'east'),(0,1,'south')):
                if (x+dx,y+dy) in cache:rows.append({'category':category,'cells':[[x,y],[x+dx,y+dy]],
                    'direction':direction,**edge_diagnostics(a,cache[x+dx,y+dy],direction)})
        return rows
    road_seams=measure(road_cache,'road');river_seams=measure(river_cache,'river')
    context_missing=[d for d in draws if d['category']=='road' and d['source_cell'][1]<y0 and tuple(d['key']) not in paint]
    report={'schema':'h3.map-assembly-review.report.v1','map':spec['map'],'map_layout':spec['map_layout'],
        'viewport':spec['viewport'],'level':z,'draw_contract':DRAW_CONTRACT,'native_inventory':spec['native_inventory'],
        'actual3x':spec.get('actual3x',[]),'native_reference_pins':native_pins,'road_placements':sum(any(l['category']=='road' for l in c['layers']) for c in cells),
        'river_placements':sum(any(l['category']=='river' for l in c['layers']) for c in cells),
        'actual3x_layers':hd_layers,'road_placements_with_actual3x':len(road_cache),
        'missing_road_placements':missing,'measured_seams':road_seams,
        'river_placements_with_actual3x':len(river_cache),'missing_river_placements':missing_rivers,
        'measured_road_seams':road_seams,'measured_river_seams':river_seams,
        'context_road_cells_above_viewport':[list(c) for c in sorted(context)],
        'missing_context_road_draws':context_missing,
        'native_editor_hd_injection_performed':False,'game_runtime_accepted':False,'art_accepted':False,
        'images_written':0,'source_images_modified':False,'status':'INCOMPLETE' if missing or missing_rivers or context_missing else 'SCENE_RENDERED_REQUIRES_INTERFACE_AND_VISUAL_ACCEPTANCE'}
    raster,offscreen=render_offscreen(spec)
    report['offscreen_raster']={**offscreen,'pixels':list(raster.size),'rgba_sha256':digest(raster.tobytes()),
        'png_written':False,'desktop_control_used':False}
    payload={'viewport':spec['viewport'],'cells':cells,'draws':draws,'assets':assets,
        'actual3xLayers':hd_layers,'report':report}
    outdir.mkdir(parents=True,exist_ok=False)
    (outdir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    page=PAGE.replace('__PAYLOAD__',json.dumps(payload,ensure_ascii=False).replace('</','<\\/'))
    (outdir/'index.html').write_text(page,encoding='utf-8')
    return report

def render_offscreen(spec,use_hd=True):
    """Render in memory. Never save preview PNGs or mutate source art."""
    _,_,_,tiles=read_tiles(spec['map'],spec['map_layout']);native=geometry_rows(spec['native_inventory'])
    x0,y0,x1,y1=spec['viewport'];z=spec.get('level',0);w=spec['map_layout']['width']
    common.need(0<=x0<x1<=w and 0<=y0<y1<=w and 0<=z<tiles.shape[0],'MAP_VIEWPORT_INVALID')
    common.need((x1-x0)*(y1-y0)<=4096,'MAP_REVIEW_VIEWPORT_LIMIT')
    hd_layers=set(spec.get('actual3x_layers',['road']));painted={tuple(row['key']):row for row in spec.get('actual3x',[])}
    cache={};missing=[];missing_road=[];missing_river=[]
    canvas=Image.new('RGBA',((x1-x0)*96,(y1-y0)*96),(238,243,250,255))
    commands=[d for y in range(y0,y1) for x in range(x0,x1)
        for d in tile_draw_commands(tiles,x,y,z,offset=(x0*96,y0*96))]
    for category in DRAW_CONTRACT['layers']:
        for d in commands:
            if d['category']!=category:continue
            k=tuple(d['key']);is_hd=use_hd and category in hd_layers;ck=(k,d['flags'],is_hd)
            if ck not in cache:
                if is_hd:
                    if k not in painted:
                        row={'key':list(k),'source_cell':d['source_cell']};missing.append(row);cache[ck]=None
                        (missing_road if category=='road' else missing_river).append(row)
                    else:cache[ck]=Image.fromarray(transform(full_crop(painted[k]['image'],native[k]),d['flags'],0))
                else:
                    p=Path(spec['native_frame_root'])/k[1]/f'{k[2]}_{k[3]}.png'
                    with Image.open(p) as image:
                        common.need(image.size==(32,32),'MAP_NATIVE_CELL_SIZE_DIFFERS')
                        array=np.repeat(np.repeat(np.asarray(image.convert('RGBA')),3,axis=0),3,axis=1)
                    cache[ck]=Image.fromarray(transform(array,d['flags'],0))
            image=cache[ck];sx,sy,sw,sh=d['source_rect'];dx,dy=d['destination']
            if image is None:
                canvas.alpha_composite(Image.new('RGBA',(sw,sh),(240,135,20,160)),(dx,dy));continue
            canvas.alpha_composite(image.crop((sx,sy,sx+sw,sy+sh)),(dx,dy))
    return canvas,{'draw_calls':len(commands),'missing_source_states':missing,'missing_road_source_states':missing_road,
        'missing_river_source_states':missing_river,'actual3x_layers':sorted(hd_layers),'images_written':0,
        'reference_ground_is_native_display_only':True,'game_runtime_accepted':False}

PAGE='''<!doctype html><meta charset="utf-8"><title>H3 实际地图格装配检查</title>
<style>body{font:16px system-ui;background:#eef3fa;color:#172737;margin:18px}button,label{margin:8px}canvas{background:#eef3fa;max-width:none}#wrap{max-height:78vh;overflow:auto;border:1px solid #8193a5}#info{white-space:pre-wrap} .warn{color:#8a3200}</style>
<h2>H3 实际地图格装配检查</h2><p>同一地图帧号与镜像位。原版参考地面临时显示放大；要求的道路／河流高清层直接使用现有 3X 裁片，不缩放、不拉伸。</p>
<button id="mode">当前：3X 道路／河流候选</button><label><input id="grid" type="checkbox" checked>网格</label><label><input id="labels" type="checkbox">帧号／镜像</label><label>显示倍率 <select id="zoom"><option value="1">3X像素 100%</option><option value="0.3333333333333333">游戏逻辑尺寸</option></select></label>
<p id="summary" class="warn"></p><div id="wrap"><canvas id="canvas"></canvas></div><pre id="info">点击地图格查看来源。</pre>
<script>const D=__PAYLOAD__, C=document.querySelector('canvas'),ctx=C.getContext('2d'),imgs={};let hd=true;
const [x0,y0,x1,y1]=D.viewport;C.width=(x1-x0)*96;C.height=(y1-y0)*96;
Promise.all(Object.entries(D.assets).flatMap(([k,v])=>['native','hd'].filter(m=>v[m]).map(m=>new Promise(r=>{let im=new Image;im.onload=()=>{imgs[k+'|'+m]=im;r()};im.src=v[m]})))).then(draw);
function draw(){ctx.clearRect(0,0,C.width,C.height);ctx.imageSmoothingEnabled=false;
const prepared={};for(const category of ['terrain','river','road'])for(const d of D.draws.filter(d=>d.category===category)){
let a=D.assets[d.image],isHD=hd&&(D.actual3xLayers||['road']).includes(d.category),im=imgs[d.image+'|'+(isHD?'hd':'native')],x=d.destination[0],y=d.destination[1],[sx,sy,sw,sh]=d.source_rect;
if(!im){ctx.save();ctx.fillStyle='rgba(240,135,20,.55)';ctx.fillRect(x,y,sw,sh);ctx.fillStyle='#000';ctx.fillText(d.key[1]+':'+d.key[3]+' 缺3X',x+2,y+25);ctx.restore();continue}
let pk=d.image+'|'+isHD+'|'+d.flags;if(!prepared[pk]){let b=document.createElement('canvas');b.width=b.height=96;let q=b.getContext('2d');q.imageSmoothingEnabled=false;q.translate(d.flags&1?96:0,d.flags&2?96:0);q.scale(d.flags&1?-1:1,d.flags&2?-1:1);if(isHD)q.drawImage(im,a.origin[0],a.origin[1]);else q.drawImage(im,0,0,96,96);prepared[pk]=b}ctx.drawImage(prepared[pk],sx,sy,sw,sh,x,y,sw,sh);}
if(document.querySelector('#grid').checked){ctx.strokeStyle='rgba(20,30,40,.6)';ctx.lineWidth=1;ctx.beginPath();for(let x=0;x<=C.width;x+=96){ctx.moveTo(x+.5,0);ctx.lineTo(x+.5,C.height)}for(let y=0;y<=C.height;y+=96){ctx.moveTo(0,y+.5);ctx.lineTo(C.width,y+.5)}ctx.stroke()}
if(document.querySelector('#labels').checked){ctx.font='12px monospace';for(const c of D.cells){let l=c.layers.find(l=>l.category==='road');if(l){let x=(c.x-x0)*96,y=(c.y-y0)*96;ctx.fillStyle='#fff';ctx.fillRect(x+1,y+1,92,16);ctx.fillStyle='#172737';ctx.fillText(`${c.x},${c.y}:0_${l.key[3]} m${l.flags}`,x+2,y+13)}}}
document.querySelector('#summary').textContent=`道路 ${D.report.road_placements} 格（3X ${D.report.road_placements_with_actual3x}，缺 ${D.report.missing_road_placements.length}）；河流 ${D.report.river_placements||0} 格（3X ${D.report.river_placements_with_actual3x||0}，缺 ${D.report.missing_river_placements?.length||0}）；上方缺接入片 ${D.report.missing_context_road_draws.length}。画面渲染不等于全套拼接或游戏运行验收。`}
document.querySelector('#mode').onclick=()=>{hd=!hd;document.querySelector('#mode').textContent='当前：'+(hd?'3X 道路／河流候选':'原版参考');draw()};
for(const k of ['grid','labels'])document.querySelector('#'+k).onchange=draw;
document.querySelector('#zoom').onchange=e=>{let z=Number(e.target.value);C.style.width=C.width*z+'px';C.style.height=C.height*z+'px'};
C.onclick=e=>{let r=C.getBoundingClientRect(),x=x0+Math.floor((e.clientX-r.left)*C.width/r.width/96),y=y0+Math.floor((e.clientY-r.top)*C.height/r.height/96);document.querySelector('#info').textContent=JSON.stringify(D.cells.find(c=>c.x===x&&c.y===y),null,2)};
</script>'''

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['fixture','review'])
    p.add_argument('--spec',required=True);p.add_argument('--spec-sha256',required=True);p.add_argument('--outdir',required=True)
    p.add_argument('--operation-id')
    p.add_argument('--renderer');p.add_argument('--renderer-sha256')
    a=p.parse_args(argv);spec=common.doc({'path':a.spec,'sha256':a.spec_sha256})
    common.need(bool(a.renderer)==bool(a.renderer_sha256),'MAP_DISPATCHER_RENDERER_PIN_REQUIRED')
    if a.renderer:
        ref={'path':a.renderer,'sha256':a.renderer_sha256};common.pin(ref);spec={**spec,'dispatcher_renderer':ref}
    target=Path(a.outdir).resolve();allowed=h3_experiment_root()
    common.need(target.is_relative_to(allowed.resolve()),'MAP_REVIEW_OUTPUT_MUST_BE_TOOL_EXPERIMENT')
    common.need(not a.operation_id or target.name==a.operation_id,'MAP_REVIEW_OPERATION_ID_DIFFERS')
    result=(make_fixture if a.command=='fixture' else review)(spec,Path(a.outdir))
    print(json.dumps({'status':result.get('status','FIXTURE_WRITTEN'),'outdir':str(Path(a.outdir).resolve()),'images_written':0},ensure_ascii=False))

if __name__=='__main__':main()
