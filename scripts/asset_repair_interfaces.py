"""Registered contact transfer and phase derivation; called by asset-repair.

Family/phase relationships require explicit source-owner contracts and a pinned
native inventory. They never relax the original 32-native same-DEF operator.
"""
import gzip
import hashlib
import json
import math
import numpy as np


def native_rows(api,binding):
    raw=api.pin(binding)[1]
    if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
    rows=json.loads(raw)['rows']
    return {(r['kind'],r['identity'],r.get('group'),r.get('frame')):r for r in rows}


def require_slot(api,rows,key,g):
    row=rows.get(tuple(key));api.need(row is not None,'INTERFACE_NATIVE_SLOT_MISSING')
    api.need(row['canvas']==g['canvas_1x'] and row['crop']==g['crop_1x'] and row['anchor']==g['origin_1x'],
        'INTERFACE_NATIVE_GEOMETRY_DIFFERS')


def binding_key(api,binding):
    receipt=json.loads(api.pin(binding['receipt'])[1])
    if receipt.get('schema')==api.RECEIPT_SCHEMA:
        matches=[r for r in receipt['outputs'] if r['sha256']==binding['image']['sha256']
            and api.plain_path(r['path'])==api.plain_path(binding['image']['path'])]
        api.need(len(matches)==1,'INTERFACE_MATERIAL_BINDING_AMBIGUOUS');return matches[0]['native_key']
    return receipt['contract']['native_entry']['key']


def transfer(api,job,rgba,g,scale):
    native=api.logical_mask(job['native_mask'],g,job['native_key'])
    material,mg,ms=api.registered(job,'material');material_key=binding_key(api,job['material'])
    contract=json.loads(api.pin(job['material_family_contract'])[1])
    api.need(contract.get('schema')=='h3.asset-repair.material-family-contract.v1','MATERIAL_FAMILY_CONTRACT_REQUIRED')
    api.need(contract.get('source_key')==job['native_key'] and contract.get('material_key')==material_key,
        'MATERIAL_FAMILY_SLOT_BINDING_DIFFERS')
    members=contract.get('family_members',[])
    api.need(isinstance(members,list) and len(set(members))==len(members) and job['native_key'][1] in members
        and material_key[1] in members and isinstance(contract.get('family_id'),str) and bool(contract['family_id']),
        'MATERIAL_FAMILY_MEMBERS_REQUIRED')
    rows=native_rows(api,contract['native_inventory']);require_slot(api,rows,job['native_key'],g);require_slot(api,rows,material_key,mg)
    api.need(mg['canvas_1x']==[32,32] and ms>=scale,'CONTACT_TRANSFER_REQUIRES_REAL_HIGHER_SCALE_NATIVE32_MATERIAL')
    depth=job['depth_native'];shared=job['shared_native'];sides=job['controlled_sides']
    api.need(type(depth) is int and type(shared) is int and 1<=shared<depth<=4,'INTERFACE_COLLAR_PARAMETERS_INVALID')
    api.need(isinstance(sides,list) and sides and len(set(sides))==len(sides) and set(sides)<={'top','bottom','left','right'},'EXPLICIT_INTERFACE_SIDES_REQUIRED')
    api.need(contract.get('controlled_sides')==sides,'MATERIAL_FAMILY_PORT_BINDING_DIFFERS')
    api.need(min(g['canvas_1x'])>depth*2,'CONTACT_BANDS_OVERLAP')
    donor=api.canonical_strip(material,depth=depth*ms)
    q=np.clip((np.arange(depth*scale)+.5)*ms/scale-.5,0,len(donor)-1)
    a=np.floor(q).astype(int);b=np.minimum(a+1,len(donor)-1);f=(q-a)[:,None,None]
    strip=(donor[a]*(1-f)+donor[b]*f).astype(np.float32)
    result=api.repair_collars(rgba,native,strip,scale,depth,shared,sides);margin=depth*scale
    core=np.array_equal(result[margin:-margin,margin:-margin],rgba[margin:-margin,margin:-margin])
    api.need(core,'UNDECLARED_PAINTING_BODY_CHANGE')
    return [(job['id'],result)],{'unmodified_core_exact':bool(core),'cross_identity_family_contract_verified':True,
        'material_native_key':material_key,'material_scale':ms,'target_scale':scale,'native_RGB_used_as_paint':False}


def phases(api,job,rgba,g,scale):
    contract=json.loads(api.pin(job['phase_contract'])[1])
    api.need(contract.get('schema')=='h3.asset-repair.phase-contract.v1','REGISTERED_PHASE_CONTRACT_REQUIRED')
    api.need(contract.get('source_key')==job['native_key'],'REGISTERED_PHASE_PARENT_SLOT_DIFFERS')
    rows=native_rows(api,contract['native_inventory']);require_slot(api,rows,job['native_key'],g)
    frames=job['phases'];api.need(isinstance(frames,list) and 2<=len(frames)<=120,'REGISTERED_PHASES_REQUIRED')
    api.need(contract.get('phases')==frames,'REGISTERED_PHASE_LAYOUT_DIFFERS')
    keys=set();ids=set();bindings={}
    for frame in frames:
        api.need(set(frame)=={'id','native_key','geometry'},'REGISTERED_PHASE_FIELDS_INVALID')
        api.token(frame['id']);key=frame['native_key'];target=api.geometry(frame['geometry'])
        api.need(key[:3]==job['native_key'][:3] and target==g,'REGISTERED_PHASE_GROUP_GEOMETRY_DIFFERS')
        require_slot(api,rows,key,target)
        api.need(tuple(key) not in keys and frame['id'] not in ids,'REGISTERED_PHASE_SLOT_DUPLICATE');keys.add(tuple(key));ids.add(frame['id'])
        bindings[frame['id']]={'native_key':key,'geometry':target,'editing_scale':scale,'layout':'full-canvas'}
    expected={key for key,row in rows.items() if key[:3]==tuple(job['native_key'][:3])}
    api.need(keys==expected,'REGISTERED_PHASE_GROUP_INCOMPLETE')
    mask=api.selection_mask(job['mask'],rgba.shape[:2]);inside=mask>0
    amplitude=job['amplitude_pixels'];direction=np.asarray(job['direction'],np.float32)
    api.need(np.isfinite(amplitude) and 0<amplitude<=scale*2 and direction.shape==(2,) and np.isfinite(direction).all()
        and np.linalg.norm(direction)>0,'REGISTERED_PHASE_FLOW_PARAMETERS_INVALID')
    api.need(bool(inside.any()),'REGISTERED_PHASE_MASK_EMPTY')
    direction/=np.linalg.norm(direction);perpendicular=np.array([-direction[1],direction[0]])
    yy,xx=np.indices(rgba.shape[:2],dtype=np.float32);base=rgba.astype(np.float32)/255;outputs=[];digests=set();deltas=[]
    for i,frame in enumerate(frames):
        phase=2*math.pi*i/len(frames)
        # Travelling spatial waves provide distinct phases, unlike a scalar sin
        # translation which duplicates sin(t) and sin(pi-t) frame pairs.
        along=np.sin(phase+yy/(8*scale));across=np.cos(phase-xx/(8*scale))*.5
        x=xx+amplitude*(direction[0]*along+perpendicular[0]*across)
        y=yy+amplitude*(direction[1]*along+perpendicular[1]*across)
        api.need(np.all((x[inside]>=0)&(x[inside]<=rgba.shape[1]-1)&(y[inside]>=0)&(y[inside]<=rgba.shape[0]-1)),
            'REGISTERED_PHASE_MASK_REACHES_CANVAS_BOUNDARY')
        warped=api.bilinear_sample_toroidal(base,x,y)
        api.need(np.all(warped[inside,3]>=.5),'FLOW_MASK_REACHES_TRANSPARENCY')
        result=rgba.copy();colors=base[:,:,:3]*(1-mask[:,:,None])+warped[:,:,:3]*mask[:,:,None]
        result[inside,:3]=np.rint(np.clip(colors[inside],0,1)*255).astype(np.uint8)
        api.need(np.array_equal(result[:,:,3],rgba[:,:,3]) and np.array_equal(result[~inside],rgba[~inside]),'FLOW_STATIC_LAYER_CHANGED')
        digest=hashlib.sha256(result.tobytes()).hexdigest();api.need(digest not in digests,'REGISTERED_PHASES_NOT_VISIBLY_DISTINCT_DATA')
        digests.add(digest);outputs.append((frame['id'],result))
    for i,(_,result) in enumerate(outputs):
        previous=outputs[i-1][1];deltas.append(float(np.abs(result[inside,:3].astype(float)-previous[inside,:3]).mean()))
    return outputs,{'output_geometry':bindings,'alpha_exact':True,'outside_mask_exact':True,'static_max_RGBA_difference_measured':0,
        'distinct_phase_pixel_hashes':len(digests),'adjacent_masked_mean_RGB_difference_including_loop':deltas,'native_timing_verified':False}
