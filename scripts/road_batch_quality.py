"""Read-only batch road assembly QA from real native slots and sampled 3X.

No image generation, repairs, resampling, master replacement or deletion.
All missing slots and unmeasured shared-interface contracts remain explicit.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import base64
from collections import deque
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
import subject_scale_layout as common
import map_assembly as assembly
import map_source_oracle as oracle
from tile_geometry_sampling import band
from tile_interface_components import load_image,mirror_component

PRODUCT=resolve_tool_root(__file__)
SCHEMA='h3.road-batch-quality.manifest.v1'
REPORT='h3.road-batch-quality.report.v1'
REGISTRY=PRODUCT/'records/map-consumer-model/current.json'
SIDES=('top','right','bottom','left')

def ref(path):
    p=Path(path).resolve();return {'path':str(p),'sha256':assembly.digest(p.read_bytes())}

def canonical(a):
    a=a.copy();a[a[:,:,3]==0,:3]=0;return a


def interface_equivalence(graphs):
    """Derive raw-edge equality/reversal classes from source-qualified pairs.

    This is the sufficient exact-strip model, not an art or rendering approval.
    Horizontal and vertical classes are derived independently; do not force
    their painted lighting/material to be an identical rotated image.
    """
    common.need(set(graphs)=={'east','south'},'ROAD_INTERFACE_GRAPH_DIRECTIONS')
    parents={};parity={};symmetric=set();counts={}
    def find(node):
        if node not in parents:parents[node]=node;parity[node]=0
        if parents[node]!=node:
            root,reverse=find(parents[node]);parity[node]^=reverse;parents[node]=root
        return parents[node],parity[node]
    def join(a,b,reverse):
        ra,pa=find(a);rb,pb=find(b)
        if ra==rb:
            if pa^pb!=reverse:symmetric.add(ra)
        else:parents[rb]=ra;parity[rb]=pa^pb^reverse
    for direction in ('east','south'):
        pairs=graphs[direction]['pairs'];counts[direction]=len(pairs)
        for pair in sorted(pairs):
            common.need(isinstance(pair,list) and len(pair)==4 and all(type(v) is int for v in pair),'ROAD_INTERFACE_PAIR_FIELDS')
            fa,ba,fb,bb=pair
            common.need(0<=fa<17 and 0<=fb<17 and 0<=ba<4 and 0<=bb<4,'ROAD_INTERFACE_PAIR_RANGE')
            if direction=='east':
                a=(fa,'left' if ba&1 else 'right');b=(fb,'right' if bb&1 else 'left')
                reverse=int(bool(ba&2)^bool(bb&2))
            else:
                a=(fa,'top' if ba&2 else 'bottom');b=(fb,'bottom' if bb&2 else 'top')
                reverse=int(bool(ba&1)^bool(bb&1))
            join(a,b,reverse)
    groups={}
    for node in sorted(parents):
        root,reverse=find(node);groups.setdefault(root,[]).append((node,reverse))
    symmetric_roots={find(node)[0] for node in symmetric}
    classes=[]
    for root,members in sorted(groups.items(),key=lambda item:item[1][0][0]):
        reference,reference_reverse=members[0]
        classes.append({'class_id':len(classes),'reference':{'frame':reference[0],'side':reference[1]},
            'reflection_invariant_required':root in symmetric_roots,
            'members':[{'frame':node[0],'side':node[1],'reverse_relative_to_reference':bool(reverse^reference_reverse)}
                       for node,reverse in members]})
    return {'schema':'h3.road-interface-equivalence.v1','legal_pair_counts':counts,
        'source_graph_sha256':assembly.digest(json.dumps(graphs,sort_keys=True,separators=(',',':')).encode()),
        'endpoint_count':len(parents),'class_count':len(classes),'classes':classes,
        'constraint_scope':'Sufficient exact boundary-strip equality under native H/V transforms',
        'painted_interface_validation':'NOT_ESTABLISHED_BY_DERIVATION',
        'native_editor_or_game_execution_proved':False,'images_written':0}

def registered_asset(row,native):
    k=tuple(row['key']);common.need(k in native,'ROAD_SAMPLE_SLOT_OUTSIDE_INVENTORY')
    receipt=common.doc(row['receipt']);g=native[k];entry=receipt['contract']['native_entry']
    common.need(receipt.get('schema')=='h3.motion.receipt.v1' and receipt['target_scale']==3,'ROAD_REAL_3X_RECEIPT_REQUIRED')
    common.need(tuple(entry['key'])==k and entry['targets']=={'canvas_1x':g['canvas'],'crop_1x':g['crop'],'origin_1x':g['anchor']},'ROAD_SAMPLE_NATIVE_GEOMETRY_DIFFERS')
    common.need(receipt['png_sha256']==row['image']['sha256'],'ROAD_SAMPLE_PNG_RECEIPT_DIFFERS')
    image_path,raw=common.pin(row['image'])
    common.need(Path(receipt['output']).resolve()==image_path.resolve(),'ROAD_SAMPLE_OUTPUT_PATH_DIFFERS')
    with Image.open(image_path) as im:
        rgba=np.asarray(im.convert('RGBA'));common.need(assembly.digest(rgba.tobytes())==receipt['rgba_sha256'],'ROAD_SAMPLE_RGBA_RECEIPT_DIFFERS')
    parents=[]
    for layer in receipt['contract']['entry']['layers']:
        for key in ('source_a','source_b'):
            if key in layer:
                parent=layer[key];common.pin(parent);parents.append({'path':parent['path'],'sha256':parent['sha256']})
    common.need(parents,'ROAD_SAMPLE_PAINT_SOURCE_REQUIRED')
    if row.get('sampling_run'):
        run=common.doc(row['sampling_run'])
        common.need(run.get('schema')=='h3.motion.run.v1' and run['target_scale']==3,'ROAD_SAMPLING_RUN_INVALID')
        matches=[x for x in run['results'] if x.get('output')==str(image_path) and x.get('receipt')==row['receipt']['path']]
        common.need(len(matches)==1,'ROAD_SAMPLE_NOT_IN_BOUND_RUN')
        pre=[x for x in run['preflight']['items'] if x['id']==matches[0]['id']]
        common.need(len(pre)==1 and pre[0]['fingerprint']==receipt['job_fingerprint'],'ROAD_SAMPLE_JOB_FINGERPRINT_DIFFERS')
    return assembly.full_crop(row['image'],g),parents

def freeze(spec):
    common.need(spec.get('schema')==SCHEMA,'ROAD_BATCH_MANIFEST_REQUIRED')
    families=spec['road_families'];common.need(families and len(families)==len(set(families)) and set(families)<=set(assembly.ROADS[1:]),'ROAD_BATCH_FAMILY_INVALID')
    native=assembly.geometry_rows(spec['native_inventory']);assets=[];seen=set();errors=[]
    for run_ref in spec.get('sampling_runs',[]):
        run=common.doc(run_ref);common.need(run.get('schema')=='h3.motion.run.v1' and run['target_scale']==3,'ROAD_BATCH_REAL_3X_RUN_REQUIRED')
        for item in run['results']:
            if not item.get('receipt') or not item.get('output'):
                errors.append({'job':item.get('id'),'reason':'SAMPLER_ITEM_HAS_NO_OUTPUT','status':item.get('status')});continue
            try:
                receipt_ref=ref(item['receipt']);receipt=common.doc(receipt_ref);k=tuple(receipt['contract']['native_entry']['key'])
                if len(k)!=4 or k[0]!='def' or k[1] not in families:continue
                common.need(k not in seen,'DUPLICATE_ROAD_SAMPLE_SLOT');seen.add(k)
                row={'key':list(k),'image':ref(item['output']),'receipt':receipt_ref,'sampling_run':run_ref,
                    'role':'SAMPLER_OUTPUT_FOR_INSPECTION_NOT_AUTOMATIC_DELIVERY'}
                _,parents=registered_asset(row,native);row['paint_sources']=parents;assets.append(row)
            except (ValueError,KeyError,OSError,TypeError) as error:
                errors.append({'job':item.get('id'),'reason':str(error)})
    return {**spec,'assets':assets,'input_errors':errors,'images_written':0,'art_approval_created':False}

def topology(a,ports,threshold=128):
    mask=a[:,:,3]>=threshold;contacts={s:np.flatnonzero(band(mask[:,:,None],s)[0,:,0]) for s in SIDES}
    missing=[s for s in ports if not len(contacts[s])];extra=[s for s in SIDES if s not in ports and len(contacts[s])]
    required=set(ports);connected=False;unseen=set(map(tuple,np.argwhere(mask)))
    while unseen and not connected:
        first=unseen.pop();q=deque([first]);hit=set()
        while q:
            y,x=q.popleft()
            if y==0:hit.add('top')
            if y==95:hit.add('bottom')
            if x==0:hit.add('left')
            if x==95:hit.add('right')
            for dy,dx in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                p=(y+dy,x+dx)
                if p in unseen:unseen.remove(p);q.append(p)
        connected=required<=hit
    return {'status':'PASS' if not missing and not extra and connected else 'FAIL',
        'missing_openings':missing,'unexpected_openings':extra,'all_openings_in_one_component':connected,
        'alpha_threshold':threshold,'edge_core_pixels':{s:len(v) for s,v in contacts.items()}}

def interface_reference(binding,native,inventory,direction='south'):
    contract=common.doc(binding)
    common.need(contract.get('schema')=='h3.road-painted-interface.v1' and contract.get('role')=='PAINTED_SHARED_INTERFACE','PAINTED_ROAD_INTERFACE_CONTRACT_REQUIRED')
    common.need(contract['native_inventory']['sha256']==inventory['sha256'],'ROAD_INTERFACE_INVENTORY_DRIFT')
    component=common.doc(contract['component_receipt'])
    common.need(component.get('schema')=='h3.tile-interface.component-receipt.v1' and component.get('role')=='PAINTED_INTERFACE_COMPONENT','NEUTRAL_COMPONENT_CANNOT_APPROVE_PAINTED_SEAMS')
    for key in ('source','original_source_evidence','component_image'):common.pin(component[key])
    evidence=common.doc(component['original_source_evidence'])
    common.need(evidence.get('schema')=='h3.art.original-received-source.v1' and evidence['original']['sha256']==component['source']['sha256'],'ROAD_INTERFACE_REAL_SOURCE_CUSTODY_REQUIRED')
    source=load_image(component['source']);actual=load_image(component['component_image']);k=component['pixels_per_native']
    common.need(type(k) is int and k>=4 and source.shape==actual.shape and list(source.shape[1::-1])==[v*k for v in component['geometry']['canvas_1x']],'ROAD_INTERFACE_SOURCE_GRID_DIFFERS')
    sx,sy,w,h=component['half_rect_1x'];tx,ty,tw,th=component['component_rect_1x']
    patch=mirror_component(source,[sx*k,sy*k,w*k,h*k]);expected=np.zeros_like(source)
    expected[ty*k:(ty+th)*k,tx*k:(tx+tw)*k]=patch
    common.need(np.array_equal(expected,actual),'ROAD_INTERFACE_COMPONENT_PIXEL_REPLAY_DIFFERS')
    a,parents=registered_asset(contract['sampled_reference'],native)
    common.need(component['component_image']['sha256'] in {p['sha256'] for p in parents},'ROAD_INTERFACE_SAMPLER_COMPONENT_BINDING_MISSING')
    depth=contract['band_depth_3x'];common.need(type(depth) is int and 1<=depth<=12,'ROAD_INTERFACE_BAND_DEPTH_INVALID')
    common.need(direction in ('east','south'),'ROAD_INTERFACE_DIRECTION_INVALID')
    side,opposite=('left','right') if direction=='east' else ('top','bottom')
    b=canonical(band(a,side,depth));other=canonical(band(a,opposite,depth))
    common.need(np.array_equal(b,b[:,::-1]) and np.array_equal(b,other),'SHARED_INTERFACE_NOT_INVARIANT_UNDER_ALLOWED_FLIPS')
    common.need((b[0,:,3]>=128).any(),'SHARED_INTERFACE_HAS_NO_CORE_OPENING')
    return b,depth


def interface_references(binding,native,inventory,family):
    """Read legacy common interfaces or independently painted axis contracts."""
    contract=common.doc(binding)
    if contract.get('schema')=='h3.road-painted-interface.v1':
        common.need(contract['sampled_reference']['key'][1]==family,'ROAD_INTERFACE_MATERIAL_FAMILY_DIFFERS')
        reference,depth=interface_reference(binding,native,inventory)
        return {axis:(reference,depth) for axis in ('east','south')}
    common.need(contract.get('schema')=='h3.road-painted-interface.v2'
                and contract.get('role')=='PAINTED_AXIS_INTERFACES','PAINTED_ROAD_AXIS_CONTRACT_REQUIRED')
    common.need(contract.get('identity')==family,'ROAD_INTERFACE_MATERIAL_FAMILY_DIFFERS')
    common.need(contract['native_inventory']==inventory,'ROAD_INTERFACE_AXIS_INVENTORY_DIFFERS')
    common.pin(contract['native_inventory'])
    axes=contract['axis_contracts']
    common.need(isinstance(axes,dict) and set(axes)=={'east','south'},'ROAD_INTERFACE_BOTH_AXES_REQUIRED')
    references={}
    for axis in ('east','south'):
        child=common.doc(axes[axis])
        common.need(child['sampled_reference']['key'][1]==family,'ROAD_INTERFACE_MATERIAL_FAMILY_DIFFERS')
        references[axis]=interface_reference(axes[axis],native,inventory,axis)
    return references

def analyze(spec):
    common.need(spec.get('schema')==SCHEMA,'ROAD_BATCH_MANIFEST_REQUIRED')
    native=assembly.geometry_rows(spec['native_inventory']);families=spec['road_families']
    common.need(families and set(families)<=set(assembly.ROADS[1:]),'ROAD_BATCH_FAMILY_INVALID')
    expected={('def',family,0,i) for family in families for i in range(17)}
    common.need(expected<=native.keys(),'ROAD_BATCH_OFFICIAL_17_SLOT_SCOPE_MISSING')
    proof=oracle.validate_proof(spec['editor_source_proof'],spec['native_inventory'])
    native_refs={tuple(r['key']):r for r in proof.get('native_reference_graph',{}).get('native_frames',[])}
    common.need(expected<=native_refs.keys(),'ROAD_BATCH_NATIVE_ALPHA_PROOF_MISSING')
    arrays={};items=[];errors=list(spec.get('input_errors',[]));seen=set();asset_rows={}
    for row in spec.get('assets',[]):
        k=tuple(row['key']);common.need(k in expected and k not in seen,'ROAD_BATCH_SLOT_OUTSIDE_SCOPE_OR_DUPLICATE');seen.add(k)
        try:
            a,parents=registered_asset(row,native);p,_=common.pin(native_refs[k])
            with Image.open(p) as im:alpha=np.asarray(im.convert('RGBA'))[:,:,3]
            ports=[s for s in SIDES if (band(alpha[:,:,None],s)[0,:,0]>0).any()]
            check=topology(a,ports);arrays[k]=canonical(a);asset_rows[k]=row
            items.append({'key':list(k),'image':row['image'],'receipt':row['receipt'],'paint_sources':parents,'topology':check})
        except (ValueError,KeyError,OSError,TypeError) as error:
            errors.append({'key':list(k),'reason':str(error)})
    references={};interface_errors={};contracts=dict(spec.get('interface_contracts',{}))
    if spec.get('interface_contract'):
        common.need(len(families)==1,'ONE_INTERFACE_CANNOT_APPROVE_MULTIPLE_ROAD_MATERIALS')
        contracts[families[0]]=spec['interface_contract']
    for family,binding in contracts.items():
        common.need(family in families,'ROAD_INTERFACE_FAMILY_OUTSIDE_SCOPE')
        try:references[family]=interface_references(binding,native,spec['native_inventory'],family)
        except (ValueError,KeyError,OSError,TypeError) as error:interface_errors[family]=str(error)
    for item in items:
        k=tuple(item['key']);a=arrays[k];ports=[s for s,c in item['topology']['edge_core_pixels'].items() if c]
        item['shared_interface']={'status':'NOT_MEASURED','reason':'PAINTED_SHARED_INTERFACE_CONTRACT_NOT_BOUND'}
        axis_references=references.get(k[1])
        if axis_references is not None:
            mismatch={}
            for side in ports:
                reference,depth=axis_references['east' if side in ('left','right') else 'south']
                mismatch[side]=int(np.any(canonical(band(a,side,depth))!=reference,axis=2).sum())
            item['shared_interface']={'status':'PASS' if not any(mismatch.values()) else 'FAIL','mismatched_band_pixels':mismatch}
    pairs=[];graphs=proof['model']['editor_emitted_road_neighbors']
    equivalence=interface_equivalence(graphs)
    for family in families:
        axis_references=references.get(family)
        for direction,graph in graphs.items():
            depth=axis_references[direction][1] if axis_references else 3
            for fa,ba,fb,bb in graph['pairs']:
                ka=('def',family,0,fa);kb=('def',family,0,fb)
                row={'family':family,'direction':direction,'a':list(ka),'a_mirror':ba,'b':list(kb),'b_mirror':bb}
                if ka not in arrays or kb not in arrays:row.update(status='MISSING',reason='PAIR_MEMBER_HAS_NO_VALID_3X')
                else:
                    a=assembly.transform(arrays[ka],ba,0);b=assembly.transform(arrays[kb],bb,0)
                    side,opposite=('right','left') if direction=='east' else ('bottom','top')
                    count=int(np.any(canonical(band(a,side,depth))!=canonical(band(b,opposite,depth)),axis=2).sum())
                    row.update(status=('PASS' if count==0 else 'FAIL') if axis_references is not None else 'RISK',
                        mismatched_band_pixels=count,diagnostic=assembly.edge_diagnostics(a,b,direction),
                        shared_interface_verified=axis_references is not None)
                pairs.append(row)
    missing=[list(k) for k in sorted(expected-set(arrays))]
    failed=bool(errors or interface_errors or any(x['topology']['status']=='FAIL' or x['shared_interface']['status']=='FAIL' for x in items) or any(x['status']=='FAIL' for x in pairs))
    all_interfaces=set(references)==set(families)
    status='FAIL' if failed else 'INCOMPLETE' if missing else 'INTERFACE_NOT_MEASURED' if not all_interfaces else 'ASSEMBLY_PASS_RUNTIME_NOT_VERIFIED'
    return {'schema':REPORT,'operation_id':spec['operation_id'],'status':status,'native_inventory':spec['native_inventory'],
        'algorithm_sha256':assembly.digest(Path(__file__).read_bytes()),
        'editor_source_proof':spec['editor_source_proof'],'art_standard':spec.get('art_standard'),'road_families':families,
        'required_slots':len(expected),'valid_actual3x_slots':len(arrays),'missing_keys':missing,'items':items,
        'errors':errors,'interface_contracts':contracts,'interface_errors':interface_errors,
        'interface_equivalence_model':equivalence,
        'interface_status':'FAIL' if interface_errors else 'PASS' if all_interfaces else 'NOT_MEASURED',
        'legal_pairs':len(pairs),'measured_pairs':sum(p['status']!='MISSING' for p in pairs),'pairs':pairs,
        'recommended_actions':(['FIX_INPUT_OR_TOPOLOGY'] if failed else [])+(['QUALIFY_PAINTED_SHARED_INTERFACE'] if not all_interfaces else [])+(['PRODUCE_MISSING_OFFICIAL_SLOTS'] if missing and all_interfaces and not failed else []),
        'technical_generation_prerequisites_ready':all_interfaces and not failed and bool(arrays),
        'source_images_modified':False,'images_written':0,'art_approval_created':False,'game_runtime_accepted':False}

def gallery(spec,report):
    native=assembly.geometry_rows(spec['native_inventory']);assets={}
    for row in spec.get('assets',[]):
        k=tuple(row['key'])
        if not any(i['key']==row['key'] for i in report['items']):continue
        p,raw=common.pin(row['image']);g=native[k]
        assets[':'.join(map(str,k))]={'url':'data:image/png;base64,'+base64.b64encode(raw).decode(),
            'origin':[v*3 for v in g['anchor']]}
    payload=json.dumps({'report':report,'assets':assets},ensure_ascii=False).replace('</','<\\/')
    return PAGE.replace('__DATA__',payload)

PAGE='''<!doctype html><meta charset="utf-8"><title>道路批量拼接检测</title><style>body{font:16px system-ui;background:#eef3fa;color:#172737;margin:20px}select{max-width:90vw}canvas{display:block;margin:16px 0;border:1px solid #8193a5}pre{white-space:pre-wrap}</style><h2>道路批量拼接检测</h2><p id="summary"></p><label>合法配对 <select id="pairs"></select></label><label><input type="checkbox" id="grid" checked>地图格</label><canvas id="view" width="288" height="288"></canvas><pre id="detail"></pre><script>const D=__DATA__,R=D.report,S=document.querySelector('#pairs'),C=document.querySelector('#view'),X=C.getContext('2d'),A={};
document.querySelector('#summary').textContent=`${R.status}：实际3X ${R.valid_actual3x_slots}/${R.required_slots}；已检测配对 ${R.measured_pairs}/${R.legal_pairs}。缺失和未测量不计通过。此图为浅色背景接口检查，完整底图及实机另行验收。`;
R.pairs.forEach((p,i)=>{let o=document.createElement('option');o.value=i;o.textContent=`${p.status} ${p.direction} ${p.a[1]} 0_${p.a[3]} m${p.a_mirror} → 0_${p.b[3]} m${p.b_mirror}`;S.append(o)});
Promise.all(Object.entries(D.assets).map(([k,a])=>new Promise(r=>{let im=new Image;im.onload=()=>{A[k]=im;r()};im.src=a.url}))).then(()=>{S.value=Math.max(0,R.pairs.findIndex(p=>p.status!=='MISSING'));draw()});
function paint(k,flags,x,y){let key=k.join(':'),a=D.assets[key],im=A[key];if(!im){X.fillStyle='#eeaa55';X.fillRect(x,y+48,96,96);return}X.save();X.translate(x+(flags&1?96:0),y+48+(flags&2?96:0));X.scale(flags&1?-1:1,flags&2?-1:1);X.drawImage(im,a.origin[0],a.origin[1]);X.restore()}
function draw(){let p=R.pairs[Number(S.value)];X.clearRect(0,0,C.width,C.height);X.imageSmoothingEnabled=false;if(!p)return;paint(p.a,p.a_mirror,48,0);paint(p.b,p.b_mirror,p.direction==='east'?144:48,p.direction==='south'?96:0);if(document.querySelector('#grid').checked){X.strokeStyle='#b1becb';for(let x=48;x<C.width;x+=96){X.beginPath();X.moveTo(x,0);X.lineTo(x,C.height);X.stroke()}for(let y=0;y<C.height;y+=96){X.beginPath();X.moveTo(0,y);X.lineTo(C.width,y);X.stroke()}}document.querySelector('#detail').textContent=JSON.stringify(p,null,2)}S.onchange=draw;document.querySelector('#grid').onchange=draw;</script>'''

def execute(action,binding,outdir,operation):
    spec=common.doc(binding);common.need(spec['operation_id']==operation,'ROAD_BATCH_OPERATION_DIFFERS')
    report=analyze(spec)
    if action=='check':return report
    out=Path(outdir).resolve();common.need(out.is_relative_to((PRODUCT/'build/experiments').resolve()) and out.name==operation,'ROAD_BATCH_OUTPUT_INVALID')
    raw=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8');page=gallery(spec,report)
    if action=='verify':
        common.need((out/'road-batch-report.json').read_bytes()==raw and (out/'index.html').read_text(encoding='utf-8')==page,'ROAD_BATCH_REPLAY_DIFFERS')
    else:
        out.mkdir(parents=True,exist_ok=False);(out/'road-batch-report.json').write_bytes(raw);(out/'index.html').write_text(page,encoding='utf-8')
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('freeze','check','run','verify','register-model'))
    for name in ('manifest','manifest-sha256','inventory','inventory-sha256','editor-proof','editor-proof-sha256','sampling-run','sampling-run-sha256','interface-contract','interface-contract-sha256','art-standard','art-standard-sha256','outdir','operation-id','renderer','renderer-sha256','expected-current-sha256'):p.add_argument('--'+name)
    p.add_argument('--identity',action='append');a=p.parse_args(argv)
    try:
        if a.renderer:common.pin({'path':a.renderer,'sha256':a.renderer_sha256})
        proof={'path':a.editor_proof,'sha256':a.editor_proof_sha256} if a.editor_proof else None
        if a.action=='register-model':
            prior=json.loads(REGISTRY.read_bytes()) if REGISTRY.exists() else {}
            if proof is None:proof=prior.get('editor_source_proof')
            validated=oracle.validate_proof(proof)
            common.need((not REGISTRY.exists() and not a.expected_current_sha256) or (REGISTRY.exists() and assembly.digest(REGISTRY.read_bytes())==a.expected_current_sha256),'ROAD_MODEL_REGISTRY_CAS_CONFLICT')
            record={**prior,'schema':'h3.map-consumer-model-registry.v1','editor_source_proof':proof,'model_sha256':validated['model']['model_sha256']}
            if a.interface_contract:
                common.need(a.identity and len(a.identity)==1,'ROAD_INTERFACE_REGISTRY_REQUIRES_ONE_FAMILY')
                binding={'path':a.interface_contract,'sha256':a.interface_contract_sha256};inv=validated['native_reference_graph']['native_inventory']
                interface_references(binding,assembly.geometry_rows(inv),inv,a.identity[0])
                record.setdefault('interface_contracts',{})[a.identity[0]]=binding
            if REGISTRY.exists():
                from retire_files import retire
                retire(REGISTRY,PRODUCT,'qualified map consumer model registry replaced with CAS')
            REGISTRY.parent.mkdir(parents=True,exist_ok=True)
            with REGISTRY.open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
            result={'status':'MODEL_REGISTERED','registry':ref(REGISTRY)}
        elif a.action=='freeze':
            common.need(a.operation_id and a.identity and a.sampling_run and a.inventory,'ROAD_BATCH_FREEZE_INPUTS_REQUIRED')
            if proof is None:proof=json.loads(REGISTRY.read_bytes())['editor_source_proof']
            inv={'path':a.inventory,'sha256':a.inventory_sha256}
            out=PRODUCT/'build/experiments/road-batch-requests'/a.operation_id
            common.need(out.parent.resolve()==(PRODUCT/'build/experiments/road-batch-requests').resolve(),'ROAD_BATCH_REQUEST_OPERATION_INVALID')
            out.mkdir(parents=True,exist_ok=False)
            # Requalification is automatic after an algorithm update, but frozen
            # source/native input changes never get silently re-approved.
            saved=common.doc(proof)
            for source in saved['model']['sources'].values():common.pin(source)
            native_ref=saved['native_reference_graph']
            common.need(native_ref['native_inventory']['sha256']==inv['sha256'],'ROAD_MODEL_TARGET_INVENTORY_DIFFERS')
            common.pin(native_ref['native_inventory'])
            for source in native_ref['native_frames']:common.pin(source)
            try:oracle.validate_proof(proof,inv)
            except (ValueError,OSError):
                common.pin(saved['compiler']);source_root=Path(native_ref['native_frames'][0]['path']).parents[1]
                source_spec={'upstream_revision':saved['model']['upstream_revision'],'sources':saved['model']['sources'],
                    'native_reference':{'native_inventory':inv,'native_frame_root':str(source_root),
                        'road_families':list(native_ref['families'])}}
                refreshed=oracle.run(source_spec,out/'model-proof',saved['compiler']['path'])
                common.need(refreshed['status']=='PASS','ROAD_MODEL_AUTO_REQUALIFICATION_FAILED')
                proof=ref(out/'model-proof/report.json')
            spec={'schema':SCHEMA,'operation_id':a.operation_id,'road_families':a.identity,'native_inventory':inv,'editor_source_proof':proof,
                'sampling_runs':[{'path':a.sampling_run,'sha256':a.sampling_run_sha256}]}
            if a.interface_contract:spec['interface_contract']={'path':a.interface_contract,'sha256':a.interface_contract_sha256}
            elif REGISTRY.exists():
                registered=json.loads(REGISTRY.read_bytes()).get('interface_contracts',{})
                spec['interface_contracts']={identity:registered[identity] for identity in a.identity if identity in registered}
            if a.art_standard:
                spec['art_standard']={'path':a.art_standard,'sha256':a.art_standard_sha256};common.pin(spec['art_standard'])
            spec=freeze(spec);target=out/'manifest.json';target.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
            result={'status':'FROZEN','manifest':ref(target),'actual3x_slots':len(spec['assets']),'images_written':0}
        else:
            report=execute(a.action,{'path':a.manifest,'sha256':a.manifest_sha256},a.outdir,a.operation_id)
            result={k:report[k] for k in ('status','required_slots','valid_actual3x_slots','legal_pairs','measured_pairs','images_written')}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,OSError,TypeError) as error:
        print(json.dumps({'status':'BLOCKED','error':str(error)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
