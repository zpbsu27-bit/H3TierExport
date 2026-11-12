"""Freeze a 5-10-slot motion pilot from an existing adapter frame proof.

Consumes native bindings and prepared native-scale PNGs. Does not edit image
pixels, approve artwork, or write the shared material manifest. Optional grid
landmarks are explicit normalized source control points, supplied by the owner.
"""
import argparse, hashlib, json
from pathlib import Path
from PIL import Image

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:stream.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def opaque_box(path):
    with Image.open(path) as image:
        alpha=image.convert('RGBA').getchannel('A')
        box=alpha.point(lambda value:255 if value>=128 else 0).getbbox()
        if not box:raise ValueError('Empty reference body: '+str(path))
        return box

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof',type=Path,required=True)
    parser.add_argument('--frames',default='0,1,2,3,4')
    parser.add_argument('--keyframes',default='0,2,4')
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--native-out',type=Path,required=True)
    parser.add_argument('--landmarks',type=Path)
    args=parser.parse_args();proof=read(args.proof)
    frames=[int(n) for n in args.frames.split(',')];keys=[int(n) for n in args.keyframes.split(',')]
    if not 5<=len(frames)<=10 or frames!=sorted(set(frames)) or keys!=sorted(set(keys)) or not set(keys)<=set(frames) or keys[0]!=frames[0] or keys[-1]!=frames[-1]:
        raise ValueError('Pilot requires5-10 ordered original slots and bracketing keyframes')
    selected={f['key'][3]:f for f in proof['frames'] if f['key'][3] in frames}
    if set(selected)!=set(frames):raise ValueError('Original proof does not contain every requested frame')
    identities={(f['key'][0],f['key'][1],f['key'][2]) for f in selected.values()}
    if len(identities)!=1:raise ValueError('Pilot must belong to one original DEF group')
    native={'schema':'h3.motion.native-slot-projection.v1','projection_source':{'path':str(args.proof.resolve()),'sha256':digest(args.proof)},
            'entries':[{'key':selected[n]['key'],'targets':selected[n]['targets'],'equivalence_class':selected[n]['source_equivalence_class']} for n in frames]}
    sources={};uv={};landmarks=read(args.landmarks) if args.landmarks else {}
    dependencies=[];columns=5;rows=6
    for frame in keys:
        binding=selected[frame];receipt_path=Path(binding['resampling_receipt']);receipt=read(receipt_path);entry=receipt['contract']['entry']
        source=Path(entry['source']);source_sha=entry['source_sha256']
        if digest(source)!=source_sha:raise ValueError('Prepared source drift: '+str(source))
        scale=binding['source_pixels_per_native_pixel'];w,h=entry['source_pixels']
        sources[frame]={'path':str(source),'sha256':source_sha,'pixels':[w,h],'key':binding['key'],'native_scale':scale}
        if str(frame) in landmarks:
            normalized=landmarks[str(frame)]
            if len(normalized)!=columns*rows:raise ValueError('Landmark grid must contain30 normalized vertices')
        else:normalized=[[x/(columns-1),y/(rows-1)] for y in range(rows) for x in range(columns)]
        if any(len(p)!=2 or not all(0<=v<=1 for v in p) for p in normalized):raise ValueError('Normalized landmark bounds')
        uv[frame]=[[p[0]*w,p[1]*h] for p in normalized]
        dependencies.append({'path':str(receipt_path),'sha256':digest(receipt_path),'prepared_png':str(source),'prepared_png_sha256':source_sha})
    triangles=[]
    for y in range(rows-1):
        for x in range(columns-1):
            index=y*columns+x;triangles.extend([[index,index+1,index+columns],[index+1,index+columns+1,index+columns]])
    items=[]
    for frame in frames:
        binding=selected[frame];geometry=binding['targets'];w,h=geometry['crop_1x'];origin=geometry['origin_1x']
        original=Path(binding['original_crop']);reference=opaque_box(original)
        if frame in keys:left=right=frame;time=0;mode='mesh'
        else:
            left=max(n for n in keys if n<frame);right=min(n for n in keys if n>frame);time=(frame-left)/(right-left);mode='interpolate'
        a=sources[left];b=sources[right]
        target=[]
        for index in range(columns*rows):
            x=uv[left][index][0]/a['pixels'][0]*(1-time)+uv[right][index][0]/b['pixels'][0]*time
            y=uv[left][index][1]/a['pixels'][1]*(1-time)+uv[right][index][1]/b['pixels'][1]*time
            target.append([origin[0]+x*w,origin[1]+y*h])
        layer={'id':'body','kind':'body','source_a':a,'vertices_a':uv[left],'target_vertices_1x':target,'triangles':triangles}
        if mode=='interpolate':layer.update(source_b=b,vertices_b=uv[right])
        key=binding['key'];items.append({'id':f'{key[1]}_G{key[2]}_F{frame}_PILOT','key':key,'targets':geometry,'mode':mode,'time':time,'layers':[layer],
            'quality':{'reference_body_height_1x':reference[3]-reference[1],'min_height_fraction':.85,'max_height_fraction':1.2,
                       'foot_y_1x':origin[1]+reference[3]-1,'foot_tolerance_1x':2/3}})
    write_new(args.native_out,native)
    spec={'schema':'h3.motion.spec.v1','output_root':str(args.output_root.resolve()),'native_contracts':[{'path':str(args.native_out.resolve()),'sha256':digest(args.native_out)}],
          'settings':{'backend':'cpu','space':'linear','filter':'h3-quality','workers':4,'memory_gib':2},'items':items,
          'pilot_source_dependencies':dependencies,'preview_fps':8,'native_timing_verified':False,
          'note':'Small original-slot tool pilot; no art approval or shared-manifest registration. Grid correspondences require visual review.'}
    if args.landmarks:spec['landmarks_source']={'path':str(args.landmarks.resolve()),'sha256':digest(args.landmarks)}
    write_new(args.spec,spec)
    print(json.dumps({'status':'MOTION_PILOT_SPEC_FROZEN','frames':len(frames),'keyframes':len(keys),'spec':str(args.spec),'native':str(args.native_out),'images_written':0,'manifest_writes':0},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
