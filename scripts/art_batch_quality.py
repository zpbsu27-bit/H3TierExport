"""One batch, one production profile: pre-drawing brief and offline QA.

Deterministic findings never pretend to establish anatomy or action semantics.
No source image is modified. No preview PNGs or extra scale tiers are written.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import collections
import gzip
import html
import io
import json
import re
from pathlib import Path
import statistics
import sys
import numpy as np
from PIL import Image
import subject_scale_layout as common
import art_quality_strategies as strategies
import art_production_profile as production
from asset_repair import cie_lab
from review_motion_sequence import component_regions

PRODUCT = resolve_tool_root(__file__)
SCHEMA = 'h3.art.batch-quality.manifest.v1'
REPORT = 'h3.art.batch-quality.report.v1'
PRESETS = {
    'creature-actions': {'label': '生物动作', 'transparent': True, 'family': 'action', 'needs': ['body_scale','anatomy_and_action_semantics','native_aliases','ordered_motion']},
    'town-animation': {'label': '动态建筑', 'transparent': True, 'family': 'identity', 'needs': ['static_region_lock','dynamic_phase_and_loop','native_registration']},
    'mounted-heroes': {'label': '骑乘人物', 'transparent': True, 'family': 'identity', 'needs': ['rider_and_mount_body_scale','anatomy_and_facing_semantics','ground_anchor']},
    'roads-rivers': {'label': '河流道路', 'transparent': True, 'family': 'identity', 'needs': ['registered_neighbor_seams','material_interfaces','flow_phase_and_loop']},
    'road-connection': {'label': '道路网格拼接', 'transparent': True, 'family': 'identity', 'needs': ['road_native_model','road_legal_neighbors','road_actual3x_ports','road_pairwise_assembly','road_scene_underlay','road_surface_seams']},
    'ground': {'label': '地面', 'transparent': False, 'family': 'identity', 'needs': ['registered_neighbor_seams','material_consistency']},
    'adventure-flags': {'label': '冒险地图旗帜', 'transparent': True, 'family': 'identity', 'needs': ['flag_canvas_uniformity','flag_pole_axis_alignment','flag_pole_connection_purity','flag_size_consistency','flag_alpha_transparency_purity']},
    'boats': {'label': '水面战船', 'transparent': True, 'family': 'identity', 'needs': ['boat_canvas_uniformity','boat_waterline_alignment','boat_perspective_volume','boat_alpha_transparency_purity']},
}
for _name,_preset in PRESETS.items():_preset['needs']=list(strategies.CHECKS[_name])


def key(row):
    return ['def',row['identity'],row['group'],row['frame']] if row['kind']=='def' else [row['kind'],row['identity']]


def token(k):
    return ':'.join(map(str,k))


def slots(ref):
    p,raw=common.pin(ref)
    inv=json.loads(gzip.decompress(raw) if p.suffix=='.gz' else raw)
    result={}
    for row in inv['rows']:
        if row.get('kind') not in ('def','pcx','bitmap'):continue
        k=tuple(key(row));common.need(k not in result,'AMBIGUOUS_NATIVE_SLOT');result[k]=row
    return result


def expected_path(root,k,row):
    if k[0]=='def':return source_identity_directory(root,k[1])/f'{k[2]}_{k[3]}.png'
    return root/(Path(row.get('resource') or k[1]).stem.upper()+'.png')


def identity_master_directory(root,identity):
    """Accept the exact identity folder or one explicitly selected form folder."""
    root=Path(root);name=root.name.upper();identity=identity.upper()
    if name==identity or name.startswith(identity+'_') or name.startswith(identity+'-'):
        return root
    return root/identity


def source_identity_directory(root,identity):
    """Reject prepared and published 3X artifacts as source masters."""
    path=identity_master_directory(root,identity)
    if {part.casefold() for part in path.parts} & {'codex-3x','codex-prepared','output-3x','3x'}:
        raise ValueError('MASTER_SOURCE_IS_PREPARED_OR_FORMAL_3X')
    return path


def file_ref(p):
    raw=p.read_bytes();return {'path':str(p),'sha256':common.sha(raw)}


def freeze(profile_ref,root,operation):
    profile=common.doc(profile_ref);common.need(profile.get('schema')==common.PROFILE,'SHARED_PRODUCTION_PROFILE_REQUIRED')
    native=slots(profile['native_inventory']);scope=profile['expected_slots']
    common.need(scope and len({tuple(k) for k in scope})==len(scope),'EXPLICIT_UNIQUE_SCOPE_REQUIRED')
    root=common.path(root);items=[];expected=set();actual=set()
    for k in scope:
        common.need(tuple(k) in native,'OFFICIAL_SLOT_NOT_FOUND');p=expected_path(root,k,native[tuple(k)])
        expected.add(p);items.append({'key':k,'source':file_ref(p) if p.is_file() else None,'expected_path':str(p)})
        if k[0]=='def':actual.update(source_identity_directory(root,k[1]).glob('*.png'))
    for p in root.glob('*.png'):
        if p in expected:actual.add(p)
    selected_ids={k[1] for k in scope}
    official={expected_path(root,list(k),row) for k,row in native.items() if k[1] in selected_ids}
    outside=(actual-expected)&official
    extra=actual-expected-outside
    unexpected=[p for p in sorted(extra) if re.fullmatch(r'\d+_\d+',p.stem)]
    return {'schema':SCHEMA,'operation_id':operation,'profile':profile_ref,'native_inventory':profile['native_inventory'],
            'masters_root':str(root),'expected_slots':scope,'items':items,
            'extra_pngs':[file_ref(p) for p in sorted(extra)],
            'unexpected_official_frame_pngs':[file_ref(p) for p in unexpected],
            'out_of_scope_official_pngs':[file_ref(p) for p in sorted(outside)]}


def longest_run(mask):
    changes=np.diff(np.r_[False,mask,False].astype(np.int8))
    starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
    return int(max(ends-starts,default=0))


def checkerboard_background_diagnostic(rgba):
    """Bounded corner texture test, including irregular/warped baked grids.

    A positive result is a texture risk, never a foreground mask or anatomy
    verdict. Transparent viewer grids cannot enter the opaque sample set.
    """
    h,w=rgba.shape[:2];size=min(128,h//2,w//2)
    hits=[]
    if size<32:return {'status':'NOT_MEASURED_TOO_SMALL','matching_corners':[]}
    for name,y,x in [('top_left',0,0),('top_right',0,w-size),
                     ('bottom_left',h-size,0),('bottom_right',h-size,w-size)]:
        patch=rgba[y:y+size,x:x+size]
        rgb=patch[:,:,:3].astype(np.float32)
        neutral=(rgb.max(2)-rgb.min(2)<=30)&(rgb.mean(2)>=100)&(patch[:,:,3]>=220)
        if neutral.mean()<.65:continue
        lum=rgb.mean(2);values=lum[neutral]
        lo,hi=np.percentile(values,[20,80])
        if hi-lo<14:continue
        binary=lum>(lo+hi)/2
        bright=(binary&neutral).sum()/max(1,neutral.sum())
        if not .15<bright<.85:continue
        # Checkerboards alternate repeatedly in both axes. Flat backgrounds,
        # two-colour stripes and ordinary gradients do not meet this test.
        tx=((binary[:,1:]!=binary[:,:-1])&neutral[:,1:]&neutral[:,:-1]).sum(1)
        ty=((binary[1:,:]!=binary[:-1,:])&neutral[1:,:]&neutral[:-1,:]).sum(0)
        if np.median(tx)<3 or np.median(ty)<3:continue
        best=None
        for lag in range(3,min(49,size//3)):
            mx=neutral[:,lag:]&neutral[:,:-lag];my=neutral[lag:,:]&neutral[:-lag,:]
            md=neutral[lag:,lag:]&neutral[:-lag,:-lag]
            if min(mx.sum(),my.sum(),md.sum())<size*size*.35:continue
            ax=float((binary[:,lag:]!=binary[:,:-lag])[mx].mean())
            ay=float((binary[lag:,:]!=binary[:-lag,:])[my].mean())
            diag=float((binary[lag:,lag:]==binary[:-lag,:-lag])[md].mean())
            score=min(ax,ay,diag)
            if best is None or score>best['score']:best={'lag':lag,'score':score}
        if best and best['score']>=.67:
            hits.append({'corner':name,'neutral_opaque_fraction':float(neutral.mean()),
                         'luma_contrast':float(hi-lo),**best})
    return {'status':'POSSIBLE_BAKED_CHECKERBOARD' if len(hits)>=2 else 'NO_REPEATED_CORNER_PATTERN_DETECTED',
            'matching_corners':hits,'diagnostic_only':True,'image_modified':False}


def inspect_png(ref,row,preset,rules):
    _,raw=common.pin(ref)
    result={'errors':[],'risks':[],'unmeasured':['anatomy_and_action_semantics'],
            'semantic_model_used':False,'body_scale_from_alpha_bbox':False}
    try:
        with Image.open(io.BytesIO(raw)) as im:
            common.need(im.format=='PNG' and getattr(im,'n_frames',1)==1,'SINGLE_PNG_REQUIRED')
            common.need(im.width*im.height<=64*1024*1024,'IMAGE_PIXEL_BUDGET')
            im.load();result.update(pixels=list(im.size),mode=im.mode)
            if preset['transparent'] and im.mode!='RGBA':result['errors'].append('RGBA_REQUIRED')
            a=np.array(im.convert('RGBA'));alpha=a[:,:,3];h,w=alpha.shape
    except (OSError,ValueError) as error:
        result['errors'].append('PNG_DECODE_OR_CAPACITY:'+str(error));return result
    visible=alpha>=16;strong=alpha>=128
    box=Image.fromarray(alpha).getbbox()
    result.update(positive_alpha_pixels=int((alpha>0).sum()),alpha1_pixels=int((alpha==1).sum()),
        alpha0_pixels=int((alpha==0).sum()),positive_alpha_bbox=list(box) if box else None)
    if not box:result['errors'].append('EMPTY_IMAGE');return result
    if preset['transparent'] and not np.any(alpha==0):result['errors'].append('NO_TRUE_TRANSPARENT_BACKGROUND')
    if preset['transparent']:
        result['checkerboard_background']=checkerboard_background_diagnostic(a)
        if result['checkerboard_background']['status']=='POSSIBLE_BAKED_CHECKERBOARD':
            result['risks'].append('POSSIBLE_BAKED_CHECKERBOARD')
    if not preset['transparent'] and np.any(alpha<255):result['errors'].append('GROUND_NOT_FULLY_OPAQUE')
    corner=np.r_[alpha[:min(8,h),:min(8,w)].ravel(),alpha[:min(8,h),-min(8,w):].ravel(),
                 alpha[-min(8,h):,:min(8,w)].ravel(),alpha[-min(8,h):,-min(8,w):].ravel()]
    result['corner_alpha_mean']=float(corner.mean())
    boundaries={'left':visible[:,0],'right':visible[:,-1],'top':visible[0,:],'bottom':visible[-1,:]}
    runs={name:longest_run(v) for name,v in boundaries.items()};result['boundary_longest_runs']=runs
    span={'left':box[3]-box[1],'right':box[3]-box[1],'top':box[2]-box[0],'bottom':box[2]-box[0]}
    if preset['transparent'] and any(v>=max(12,span[n]*rules.get('flat_boundary_fraction',.25)) for n,v in runs.items()):
        result['risks'].append('POSSIBLE_TRUNCATED_SUBJECT_LONG_BOUNDARY_RUN')
    if strong.any():
        ys,xs=np.nonzero(strong);b=[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
        result['alpha128_bbox']=b;result['alpha128_height_fraction']=(b[3]-b[1])/h
        result['alpha128_aspect']=(b[2]-b[0])/max(1,b[3]-b[1])
        crop=row.get('crop') or row.get('canvas')
        if crop and crop[1] and preset['transparent']:
            ratio=result['alpha128_aspect']/(crop[0]/crop[1]);result['aspect_ratio_vs_native_crop']=ratio
            if (ratio<.45 or ratio>2.2) and any(runs.values()):result['risks'].append('POSSIBLE_FRAGMENT_ASPECT_AND_EDGE_CONTACT')
        sample=a[strong,:3][::max(1,int(strong.sum())//150000)]
        lab=cie_lab(sample);result['visible_lab_mean']=lab.mean(axis=0).tolist()
        result['visible_lab_std']=lab.std(axis=0).tolist()
    regions,truncated=component_regions(visible,max_runs=100000)
    result['component_scan_truncated']=truncated
    result['largest_alpha16_components']=regions[:8]
    if truncated:result['unmeasured'].append('component_topology_budget_exceeded')
    elif len(regions)>1:
        if regions[1]['pixels']>=max(100,regions[0]['pixels']*.20):result['risks'].append('MULTIPLE_LARGE_COMPONENTS_NOT_A_PROVEN_CHARACTER_COUNT')
        result['small_island_pixels']=sum(x['pixels'] for x in regions[1:] if x['pixels']<max(4,regions[0]['pixels']*.0001))
        elongated=[]
        for component in regions[1:]:
            x0,y0,x1,y1=component['bbox_ltrb'];width=max(0,x1-x0);height=max(0,y1-y0)
            aspect=max(width,height)/max(1,min(width,height))
            if component['pixels']>=100 and aspect>=3.0:
                elongated.append({'pixels':component['pixels'],'bbox_ltrb':component['bbox_ltrb'],
                    'aspect_ratio':round(float(aspect),4),'diagnostic_only':True,
                    'requires_visual_review':True,'automatic_rejection':False})
        if elongated:
            result['detached_component_candidates']=elongated[:16]
            result['risks'].append('ELONGATED_DETACHED_ALPHA_COMPONENT_REVIEW')
    result['alpha_geometry_is_not_limb_count']=True
    low=(alpha>0)&(alpha<128);dark=a[:,:,:3].max(axis=2)<=32
    outside=np.ones(alpha.shape,dtype=bool)
    if strong.any():outside[b[1]:b[3],b[0]:b[2]]=False
    result['low_alpha_diagnostic']={'partial_alpha_pixels':int(((alpha>0)&(alpha<255)).sum()),
        'alpha1_pixels':int((alpha==1).sum()),'low_alpha_outside_body_envelope_pixels':int((low&outside).sum()),
        'dark_low_alpha_outside_body_envelope_pixels':int((low&outside&dark).sum()),
        'diagnostic_only':True,'not_a_semantic_body_mask':True,'alpha_modified':False}
    import creature_sequence_qa
    core_op=creature_sequence_qa.interior_core_opacity_metrics(alpha)
    result['interior_core_opacity_diagnostic']=core_op
    if preset['transparent'] and core_op['status']=='FOGGY_SEMI_TRANSPARENT_CORE_RISK':
        result['risks'].append('FOGGY_SEMI_TRANSPARENT_CORE_RISK')
    return result


def measure_body_scale(profile,items):
    """Use source-bound anatomical paths and a real native registration receipt.

    K is derived from the receipt's actual registered canvas, never ceil(common_k)
    or the alpha envelope. Source coordinates remain unchanged by padding.
    """
    measurements=profile.get('body_measurements',[])
    if not measurements:return
    receipt=common.doc(profile['native_review_report'])
    common.need(receipt.get('schema')=='h3.native-review.batch-report.v1','NATIVE_REVIEW_RECEIPT_REQUIRED')
    binding=receipt['official_inventory']
    common.need(binding['sha256']==profile['native_inventory']['sha256'] and binding.get('geometry_verified_by_companion') is True,'BODY_REGISTRATION_NATIVE_INVENTORY_MISMATCH')
    registered={tuple(r['key']):r for r in receipt['items']}
    bykey={tuple(r['key']):r for r in items};seen=set()
    for measurement in measurements:
        k=tuple(measurement['key']);common.need(k in bykey and k not in seen,'BODY_MEASUREMENT_SCOPE_OR_DUPLICATE');seen.add(k)
        result=bykey[k];reg=registered[k];source=result['source']
        common.need(source and measurement['image_sha256']==source['sha256']==reg['source_sha256'],'BODY_MEASUREMENT_SOURCE_SHA_DRIFT')
        common.need(reg.get('source_view_proof',{}).get('all_positive_preserved') is True,'BODY_REGISTRATION_MUST_PRESERVE_SOURCE')
        common.need(reg['source_pixels']==result['pixels'],'BODY_REGISTRATION_PIXEL_SIZE_DRIFT')
        cw,ch=reg['targets']['canvas_1x'];rw,rh=reg['registered_pixels'];kx,ky=rw/cw,rh/ch
        common.need(abs(kx-ky)<1e-7 and kx>0,'ISOTROPIC_REGISTRATION_REQUIRED')
        rule=profile['directions'][measurement['profile_direction']]
        common.need(measurement['pose_class']==rule['pose_class'],'COMPARABLE_POSE_CLASS_REQUIRED')
        common.need(measurement.get('coordinate_space')=='source_pixels' and measurement.get('method')=='visual-anatomical-landmarks','BODY_LANDMARK_METHOD_REQUIRED')
        lengths={name:common.measure(points,result['pixels'])/kx for name,points in measurement['paths'].items()}
        targets=rule['lengths_native'];common.need(set(targets)<=lengths.keys(),'REQUIRED_BODY_PATH_MISSING')
        deviations={name:lengths[name]/target-1 for name,target in targets.items() if target>0}
        common.need(len(deviations)==len(targets),'POSITIVE_BODY_TARGET_REQUIRED')
        tolerance=rule.get('primary_tolerance_fraction',.08)
        primary=rule['primary_metric'];common.need(primary in deviations and 0<tolerance<=.15,'BODY_TOLERANCE_REQUIRES_PROFILE_REVIEW')
        tolerances={name:rule.get('metric_tolerances',{}).get(name,tolerance) for name in targets}
        common.need(all(0<v<=.15 for v in tolerances.values()),'BODY_TOLERANCE_REQUIRES_PROFILE_REVIEW')
        status='MEASURED_WITHIN_PROFILE' if all(abs(deviations[n])<=tolerances[n] for n in deviations) else 'BODY_SCALE_REVIEW_REQUIRED'
        result['body_scale']={'status':status,'pose_class':measurement['pose_class'],'source_pixels_per_native_unit':kx,
            'lengths_native':lengths,'targets_native':targets,'deviation_fraction':deviations,'metric_tolerances':tolerances,
            'anatomical_paths_source':measurement['paths'],'measurement_author':'assistant_visual_landmarks',
            'not_an_automatic_landmark_model':True,'not_an_art_verdict':True}
        if status=='BODY_SCALE_REVIEW_REQUIRED':result['risks'].append(status)


def analyze(manifest_ref):
    m=common.doc(manifest_ref);common.need(m.get('schema')==SCHEMA,'QUALITY_MANIFEST_SCHEMA_INVALID')
    profile=common.doc(m['profile']);common.need(profile.get('schema')==common.PROFILE,'SHARED_PRODUCTION_PROFILE_REQUIRED')
    common.need(profile['native_inventory']==m['native_inventory'] and profile['expected_slots']==m['expected_slots'], 'PROFILE_SCOPE_OR_NATIVE_PIN_MISMATCH')
    common.need(profile.get('replaces_canonical_art_standard') is False,'PROFILE_IS_NOT_A_NEW_ART_STANDARD')
    standard=common.doc(profile['art_standard']);common.need(standard.get('version')==profile['art_standard']['version'],'ART_STANDARD_VERSION_DRIFT')
    preset=PRESETS[profile['preset']];rules=profile.get('rules',{})
    current=freeze(m['profile'],m['masters_root'],m['operation_id'])
    common.need(current==m,'CURRENT_DIRECTORY_OR_SOURCE_SHA_CHANGED_REFREEZE_REQUIRED')
    native=slots(m['native_inventory']);items=[];families=collections.defaultdict(list);bykey={};counts=collections.Counter()
    for entry in m['items']:
        k=entry['key'];result={**entry,'errors':[],'risks':[],'unmeasured':[]}
        if entry['source'] is None:result['errors'].append('MISSING_OFFICIAL_SLOT_FILE')
        else:
            result.update(inspect_png(entry['source'],native[tuple(k)],preset,rules))
            if profile['preset'] not in ('creature-actions','mounted-heroes'):
                result['unmeasured']=[n for n in result['unmeasured'] if n!='anatomy_and_action_semantics']
            family=token(k[:3] if preset['family']=='action' and k[0]=='def' else k[:2])
            families[family].append(result)
        items.append(result);bykey[tuple(k)]=result
    allowed_aliases=set()
    alias_groups=list(profile.get('exact_alias_groups',[]))
    for binding in profile.get('strategies',{}).get('native_defs',[]):
        alias_groups.extend([selected for group in strategies.def_aliases(binding['source'],binding['identity'],native)
            if len(selected:=[k for k in group if tuple(k) in bykey])>1])
    for group in alias_groups:
        members=[bykey[tuple(k)] for k in group];digests={v['source']['sha256'] for v in members if v['source']}
        allowed_aliases.update(tuple(k) for k in group)
        if len(digests)>1:
            for result in members:result['errors'].append('EXACT_NATIVE_ALIAS_BYTES_DIFFER')
    same=collections.defaultdict(list)
    for result in items:
        if result['source']:same[result['source']['sha256']].append(result)
    for group in same.values():
        if len(group)>1 and not any({tuple(r['key']) for r in group}<={tuple(k) for k in aliases} for aliases in alias_groups):
            for result in group:result['risks'].append('DUPLICATE_CONTENT_WITHOUT_BOUND_NATIVE_ALIAS')
    for family,group in families.items():
        measured=[r for r in group if 'visible_lab_mean' in r]
        if len(measured)>=3:
            median=np.median([r['visible_lab_mean'] for r in measured],axis=0)
            for result in measured:
                delta=float(np.linalg.norm(np.asarray(result['visible_lab_mean'])-median));result['lab_mean_distance_from_family_median']=delta
                if delta>rules.get('color_lab_distance_hint',25):result['risks'].append('COLOR_FAMILY_OUTLIER_DIAGNOSTIC')
        heights=[r['alpha128_height_fraction'] for r in group if 'alpha128_height_fraction' in r]
        if len(heights)>=3:
            median=statistics.median(heights)
            for result in group:
                if 'alpha128_height_fraction' in result:
                    delta=result['alpha128_height_fraction']/median-1;result['alpha_envelope_height_deviation_fraction']=delta
                    if abs(delta)>rules.get('alpha_envelope_height_hint',.15):result['risks'].append('LAYOUT_ENVELOPE_OUTLIER_NOT_ANATOMICAL_SCALE')
    # A mixed or gradually drifting batch can pull its own median away from
    # the intended material. Explicit reference pins complement that median;
    # they do not turn a colour distance into an identity/model verdict.
    references=collections.defaultdict(list)
    for binding in profile.get('color_references',[]):
        common.need(binding.get('identity') in {k[1] for k in m['expected_slots']},'COLOR_REFERENCE_OUTSIDE_SCOPE')
        common.need(binding.get('selection_author') in ('assistant','user'),'COLOR_REFERENCE_SELECTION_REQUIRED')
        observed=inspect_png(binding['source'],{},preset,rules)
        common.need(not observed['errors'] and 'visible_lab_mean' in observed,'COLOR_REFERENCE_INVALID_OR_EMPTY')
        references[binding['identity']].append((binding,observed['visible_lab_mean']))
    for result in items:
        if 'visible_lab_mean' not in result:continue
        refs=references[result['key'][1]]
        if not refs:continue
        distances=[float(np.linalg.norm(np.asarray(result['visible_lab_mean'])-mean)) for _,mean in refs]
        closest=int(np.argmin(distances));reference=refs[closest][0]
        result['reference_material_color']={'nearest_reference':reference['source'],
            'selection_author':reference['selection_author'],'lab_mean_distance':distances[closest],
            'diagnostic_threshold':rules.get('color_lab_distance_hint',25),'identity_semantics_not_measured':True}
        if distances[closest]>rules.get('color_lab_distance_hint',25):result['risks'].append('REFERENCE_MATERIAL_COLOR_MISMATCH')
    measure_body_scale(profile,items)
    domain=strategies.apply(profile,native,items)
    for result in items:
        unmet=[name for name,check in domain['checks'].items() if result['key'] in check['unmeasured_keys']]
        if result['key'] not in domain['checks'].get('semantic_structure',{}).get('unmeasured_keys',[result['key']]):
            result['unmeasured']=[n for n in result['unmeasured'] if n!='anatomy_and_action_semantics']
        result['unmeasured']=sorted(set(result['unmeasured']+unmet))
        result['status']='ERROR' if result['errors'] else 'RISK' if result['risks'] else 'TECHNICAL_CHECKS_ONLY'
        counts[result['status']]+=1
    return {'schema':REPORT,'manifest':manifest_ref,'profile':m['profile'],'preset':profile['preset'],
            'operation_id':m['operation_id'],'scope':m['expected_slots'],'scope_count':len(items),
            'extra_pngs':m['extra_pngs'],'unexpected_official_frame_pngs':m['unexpected_official_frame_pngs'],
            'out_of_scope_official_pngs':m['out_of_scope_official_pngs'],
            'summary':dict(counts),'items':items,'domain_strategies':domain,
            'production_readiness':production.readiness(profile),
            'verification_coverage':{'fully_measured_slots':sum(not r['unmeasured'] for r in items),
                'slots_with_unmeasured_checks':sum(bool(r['unmeasured']) for r in items),
                'complete_validation':not any(r['unmeasured'] for r in items)},
            'overall':'ERRORS_FOUND' if counts['ERROR'] or m['unexpected_official_frame_pngs'] else 'RISKS_FOUND' if counts['RISK'] or m['extra_pngs'] else 'TECHNICAL_CHECKS_FINISHED',
            'missing_semantic_checks_are_not_passes':True,'art_approved':False,'runtime_verified':False,
            'images_written':0,'source_images_modified':False}


def brief(profile):
    lines=['# 画前与画后共用的制作档案', '', f"模式：{PRESETS[profile['preset']]['label']}。",
        f"官方槽位：{len(profile['expected_slots'])} 项。规范版本：{profile['art_standard']['version']}。",
        f"唯一规范路径：{profile['art_standard']['path']}；SHA-256：{profile['art_standard']['sha256']}。",
        f"档案状态：{profile.get('review_status','DRAFT')}。缺少本类别必需依据时保留草案/未测量，不声称整套通过。",
        *production.authoring_rules(profile['preset']),
        f"本模式还需的检测依据：{PRESETS[profile['preset']]['needs']}。缺少依据记未测量，不自动判合格或重绘。",
        '确定技术错误先用已有正式本地算子修复；真实缺画、结构错误或质量不足再针对该张重绘。数值诊断不冒充语义判断或实机验证。', '']
    for name,rule in profile.get('directions',{}).items():
        lines.extend([f'## 尺度家族／朝向 {name}',
            f"可比姿势：{rule.get('pose_class','未核准')}；主身体指标：{rule.get('primary_metric','未填写')}。",
            f"身体目标（原生显示单位）：{rule.get('lengths_native',{})}；主指标容差：{rule.get('primary_tolerance_fraction','未填写')}。",
            f"物理接地点（独立于DEF origin）：{rule.get('anchor_native_canvas','未标注')}。",
            '这些指标用于相同姿势／朝向的画后测量。不能因长矛、翅膀或法术伸展而挤小身体；不同姿势不强制像素1:1。',''])
    lines.extend(['## 冻结的真实形象参考'])
    for r in profile.get('reference_images',[]):lines.append(f"- {r['path']}；SHA-256 {r['sha256']}。")
    if not profile.get('reference_images'):lines.append('尚未冻结形象参考；生成前须补齐，不能拿低清导出代替。')
    if profile.get('color_references'):
        lines.extend(['','## 材质配色参照'])
        for r in profile['color_references']:
            lines.append(f"- {r['identity']}：{r['source']['path']}；SHA-256 {r['source']['sha256']}；选择者 {r['selection_author']}。")
        lines.append('制作后同时比较已选配色参照与同组中位色；整组一起漂色时，中位色接近不能证明材质一致。差异仍只作为诊断，不证明人物身份错误。')
    lines.extend(production.brief_lines(profile))
    lines.extend(production.road_seam_brief(profile))
    lines.extend(['','## 本次官方槽位（每次只画其中一张）'])
    for k in profile['expected_slots']:
        lines.append(f"- {k[1]}/{k[2]}_{k[3]}.png" if k[0]=='def' else f'- {k[0]}:{k[1]}（按该LOD资源的官方路径）')
    return '\n'.join(lines)+ '\n'


def quality_review_html(report):
    """Render original frames at one shared display scale for visual triage.

    The page never creates preview PNGs, fits individual frames, crops canvas
    margins, guesses a ground line, resamples a source, or grants art approval.
    """
    items=report.get('items',[])
    sha_counts=collections.Counter(x.get('source',{}).get('sha256') for x in items if x.get('source'))
    identities=sorted({str(x['key'][1]) for x in items if len(x.get('key',[]))>1})
    default_scale=0.18
    cards=[]
    for item in items:
        k=item.get('key',[])
        identity=str(k[1]) if len(k)>1 else ''
        is_def=bool(k and k[0]=='def' and len(k)>=4)
        group=str(k[2]) if is_def else ''
        frame=str(k[3]) if is_def else ''
        label=(f'{identity}/{group}_{frame}.png' if is_def else token(k))
        source=item.get('source')
        pixels=item.get('pixels') or [0,0]
        try:width,height=int(pixels[0]),int(pixels[1])
        except (TypeError,ValueError,IndexError):width,height=0,0
        sha=source.get('sha256','') if source else ''
        source_url=Path(source['path']).resolve().as_uri() if source and source.get('path') else ''
        alias_checks=(item.get('domain_checks') or {}).get('native_aliases') or []
        alias_class_size=alias_checks[0].get('native_offset_class_size') if alias_checks else None
        alias_status=alias_checks[0].get('status') if alias_checks else 'NOT_PROVIDED'
        same_sha_count=sha_counts.get(sha,0) if sha else 0
        errors='; '.join(item.get('errors') or [])
        risks='; '.join(item.get('risks') or [])
        unmeasured='; '.join(item.get('unmeasured') or [])
        body_status='NOT_MEASURED'
        if isinstance(item.get('body_scale'),dict):body_status=item['body_scale'].get('status','NOT_MEASURED')
        detail={name:item.get(name) for name in ('checkerboard_background','corner_alpha_mean',
            'boundary_longest_runs','alpha128_bbox','largest_alpha16_components','detached_component_candidates','low_alpha_diagnostic',
            'lab_mean_distance_from_family_median','alpha_envelope_height_deviation_fraction','domain_checks')
            if name in item}
        detail['body_scale_status']=body_status
        image_html='<div class="missing">缺少该槽PNG</div>'
        if source_url and width>0 and height>0:
            display_w=max(1,round(width*default_scale));display_h=max(1,round(height*default_scale))
            image_html=(f'<div class="canvas-viewport"><img class="frame-image" loading="lazy" '
                f'data-native-width="{width}" data-native-height="{height}" '
                f'style="width:{display_w}px;height:{display_h}px" '
                f'src="{html.escape(source_url,quote=True)}" alt="{html.escape(label,quote=True)}"></div>')
        diagnostics=[]
        if errors:diagnostics.append('<b class="error">'+html.escape(errors)+'</b>')
        if risks:diagnostics.append('<b class="risk">'+html.escape(risks)+'</b>')
        if unmeasured:diagnostics.append('<span class="unmeasured">未测：'+html.escape(unmeasured)+'</span>')
        if alias_class_size is not None:
            diagnostics.append('<span>原生偏移类：'+html.escape(str(alias_status))+' / '+html.escape(str(alias_class_size))+' 槽</span>')
        if same_sha_count>1:
            diagnostics.append('<span>报告范围内同SHA出现 '+str(same_sha_count)+' 次；请结合原生偏移类判断，不自动判多图。</span>')
        search=' '.join((label,sha,item.get('status',''),errors,risks,unmeasured))
        cards.append(
            '<article class="frame-card" data-identity="'+html.escape(identity,quote=True)+'" '
            'data-group="'+html.escape(group,quote=True)+'" data-status="'+html.escape(str(item.get('status','')),quote=True)+'" '
            'data-search="'+html.escape(search,quote=True)+'">'
            '<h3>'+html.escape(label)+'</h3>'
            '<p class="source-meta">'+html.escape(str([width,height]))+' px · '+html.escape(sha or '无源SHA')+'</p>'
            +image_html+
            '<div class="diagnostics">'+'<br>'.join(diagnostics or ['当前自动诊断无错误/风险；这不是美术通过。'])+'</div>'
            '<details><summary>透明、颜色、别名与诊断指标</summary><pre>'+html.escape(json.dumps(detail,ensure_ascii=False,indent=2))+'</pre></details>'
            '</article>')
    identity_options='<option value="">全部身份</option>'+''.join(
        '<option value="'+html.escape(name,quote=True)+'">'+html.escape(name)+'</option>' for name in identities)
    summary=html.escape(json.dumps(report.get('summary',{}),ensure_ascii=False))
    check_rows=''.join('<tr><td>'+html.escape(name)+'</td><td>'+html.escape(str(value.get('status','NOT_MEASURED')))+'</td><td>'+
        str(value.get('covered_slots',0))+'/'+str(value.get('total_slots',report.get('scope_count',0)))+'</td></tr>'
        for name,value in report.get('domain_strategies',{}).get('checks',{}).items())
    if not check_rows:check_rows='<tr><td colspan="3">没有专属检查结果</td></tr>'
    directory_summary=(
        '<p>本次槽位 '+str(report.get('scope_count',0))+'；原生清单不存在的组帧 '+
        str(len(report.get('unexpected_official_frame_pngs',[])))+'；非槽PNG '+str(len(report.get('extra_pngs',[])))+
        '；范围外合法官方帧 '+str(len(report.get('out_of_scope_official_pngs',[])))+'（不算本次多图）。</p>')
    for field,label_text in (
            ('extra_pngs','非槽PNG：检查是否参考/来源资料，不能据此自动删除'),
            ('unexpected_official_frame_pngs','原生不存在的组帧名称'),
            ('out_of_scope_official_pngs','本次范围外的合法官方帧')):
        directory_summary+='<details><summary>'+label_text+'</summary><pre>'+html.escape(
            json.dumps(report.get(field,[]),ensure_ascii=False,indent=2))+'</pre></details>'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>批量成品质量检测 · 同像素审阅</title>
<style>
:root{--canvas-bg:#dedede;--panel:#fff;--border:#9aa7b5;--text:#203040}*{box-sizing:border-box}
body{font:14px system-ui,"Microsoft YaHei",sans-serif;margin:18px;background:#eef3fa;color:var(--text)}
h1{margin:.2em 0}.notice{padding:12px;background:#fff6d8;border:1px solid #ddc77f;max-width:1100px}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:14px 0;padding:10px;background:#fff;border:1px solid var(--border)}
.toolbar label{display:flex;gap:6px;align-items:center}.toolbar input[type=search]{min-width:220px;padding:6px}
.domain-checks{border-collapse:collapse;margin:12px 0;background:#fff}.domain-checks th,.domain-checks td{padding:6px 10px;border:1px solid var(--border);text-align:left}
.gallery{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px;overflow:auto;padding:4px}
.frame-card{display:flex;flex:0 0 auto;flex-direction:column;align-items:flex-start;width:max-content;max-width:none;margin:0;padding:8px;background:var(--panel);border:1px solid var(--border)}
.frame-card h3{font-size:13px;margin:0 0 4px}.source-meta,.diagnostics{font-size:11px;max-width:420px;overflow-wrap:anywhere;margin:4px 0}
.canvas-viewport{display:block;flex:none;overflow:auto;background:var(--canvas-bg);border:1px solid #999;max-width:calc(100vw - 64px)}
.frame-image{display:block;flex:none;max-width:none;max-height:none;object-fit:none;image-rendering:auto}
.diagnostics{display:flex;flex-direction:column;gap:3px}.error{color:#9b1c1c}.risk{color:#875100}.unmeasured{color:#3d536b}
details{max-width:420px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto}.missing{padding:30px;background:#f4dddd;color:#7b1111}
td{padding:6px;border:1px solid var(--border)}
</style>
<h1>批量成品质量检测 · 同像素审阅</h1>
<div class="notice"><b>所有帧使用同一个显示倍率，展示完整原PNG画布；不逐帧fit，不按Alpha框缩放，不猜脚底线。</b><br>
此视图用于快速目视身份/性别、肢体与装备、同姿势体量、色调材质、动作衔接及透明边缘。body_scale缺少哈希绑定landmarks/K时仍为NOT_MEASURED；Alpha bbox、颜色统计、连通域和别名诊断不自动批准或要求重绘。</div>
__DIRECTORY_SUMMARY__
<p>自动汇总：__SUMMARY__</p>
<table class="domain-checks"><thead><tr><th>专属检查</th><th>结果</th><th>覆盖/总槽</th></tr></thead><tbody>__CHECKS__</tbody></table>
<div class="toolbar">
<label>身份 <select id="identity-filter">__IDENTITY_OPTIONS__</select></label>
<label>组 <select id="group-filter"><option value="">全部组</option></select></label>
<label>状态 <select id="status-filter"><option value="">全部状态</option><option value="ERROR">ERROR</option><option value="RISK">RISK</option><option value="TECHNICAL_CHECKS_ONLY">TECHNICAL_CHECKS_ONLY</option></select></label>
<label>搜索 <input id="text-filter" type="search" placeholder="DEF/组_帧、SHA、风险"></label>
<label>统一显示倍率 <input id="review-scale" type="range" min="0.06" max="0.40" step="0.01" value="0.18"><span id="scale-value">0.18×</span></label>
<label>透明预览底色 <select id="background-filter"><option value="#dedede">中性灰</option><option value="#ffffff">白</option><option value="#30343a">深灰</option></select></label>
</div>
<p id="visible-count"></p><div id="gallery" class="gallery">__CARDS__</div>
<script>
const cards=[...document.querySelectorAll('.frame-card')],identityFilter=document.getElementById('identity-filter'),groupFilter=document.getElementById('group-filter'),statusFilter=document.getElementById('status-filter'),textFilter=document.getElementById('text-filter'),scaleControl=document.getElementById('review-scale');
function refreshGroups(){const selected=groupFilter.value,id=identityFilter.value;const groups=[...new Set(cards.filter(c=>!id||c.dataset.identity===id).map(c=>c.dataset.group).filter(Boolean))].sort((a,b)=>Number(a)-Number(b));groupFilter.innerHTML='<option value="">全部组</option>'+groups.map(g=>'<option value="'+g+'">组 '+g+'</option>').join('');if(groups.includes(selected))groupFilter.value=selected;}
function filterCards(){const id=identityFilter.value,group=groupFilter.value,status=statusFilter.value,q=textFilter.value.toLowerCase();let n=0;for(const c of cards){const show=(!id||c.dataset.identity===id)&&(!group||c.dataset.group===group)&&(!status||c.dataset.status===status)&&(!q||c.dataset.search.toLowerCase().includes(q));c.hidden=!show;if(show)n++;}document.getElementById('visible-count').textContent='当前显示 '+n+' / '+cards.length+' 帧';}
function applyCommonScale(){const s=Number(scaleControl.value);document.getElementById('scale-value').textContent=s.toFixed(2)+'×';for(const img of document.querySelectorAll('.frame-image')){img.style.width=Math.max(1,Math.round(Number(img.dataset.nativeWidth)*s))+'px';img.style.height=Math.max(1,Math.round(Number(img.dataset.nativeHeight)*s))+'px';}}
identityFilter.addEventListener('change',()=>{refreshGroups();filterCards();});groupFilter.addEventListener('change',filterCards);statusFilter.addEventListener('change',filterCards);textFilter.addEventListener('input',filterCards);scaleControl.addEventListener('input',applyCommonScale);document.getElementById('background-filter').addEventListener('change',e=>document.documentElement.style.setProperty('--canvas-bg',e.target.value));refreshGroups();applyCommonScale();filterCards();
</script></html>'''
    page=(page.replace('__DIRECTORY_SUMMARY__',directory_summary)
        .replace('__SUMMARY__',summary)
        .replace('__CHECKS__',check_rows)
        .replace('__IDENTITY_OPTIONS__',identity_options)
        .replace('__CARDS__',''.join(cards)))
    return page


def execute(action,mref,outdir,operation):
    root=common.path(outdir);common.need(root.parent in ((PRODUCT/'build/experiments/batch-quality').resolve(),(PRODUCT/'build/experiments/scale-layout').resolve())
        and root.name==operation,'OUTPUT_MUST_BE_NEW_BATCH_QUALITY_OPERATION')
    report=analyze(mref);common.need(report['operation_id']==operation,'OPERATION_ID_MISMATCH')
    profile=common.doc(report['profile'])
    if action=='check':return {'status':'CHECKED_NO_WRITES','overall':report['overall'],'summary':report['summary'],'scope_count':report['scope_count']}
    encoded=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8');receipt=root/'quality-report.json'
    if action=='run' and root.exists():common.need(receipt.is_file(),'FOREIGN_OR_PARTIAL_OPERATION');action='verify'
    if action=='verify':
        common.need(receipt.read_bytes()==encoded,'QUALITY_REPORT_REPLAY_MISMATCH')
        common.need((root/'generation-brief.md').read_text(encoding='utf-8')==brief(profile),'QUALITY_BRIEF_REPLAY_MISMATCH')
        common.need((root/'quality-report.html').read_text(encoding='utf-8')==quality_review_html(report),'QUALITY_HTML_REPLAY_MISMATCH')
        return {'status':'VERIFIED_READ_ONLY_QUALITY_REPORT','report':str(receipt)}
    common.need(action=='run' and not root.exists(),'OUTPUT_ALREADY_EXISTS');root.mkdir(parents=True)
    with receipt.open('xb') as stream:stream.write(encoded)
    (root/'generation-brief.md').write_text(brief(profile),encoding='utf-8')
    (root/'quality-report.html').write_text(quality_review_html(report),encoding='utf-8')
    analyze(mref)
    return {'status':'READ_ONLY_BATCH_QUALITY_CREATED','overall':report['overall'],'summary':report['summary'],'report':str(receipt),'html':str(root/'quality-report.html'),'images_written':0}


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('sequence-qa','sequence-stabilize'):
        import creature_sequence_qa
        return creature_sequence_qa.main(argv)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('catalog','init','freeze','check','run','verify'))
    for name in ('profile','profile-sha256','manifest','manifest-sha256','masters-root','inventory','inventory-sha256','preset','art-standard','art-standard-sha256','operation-id','outdir','groups','frames','renderer','renderer-sha256','native-mask-root'):p.add_argument('--'+name)
    p.add_argument('--identity',action='append');a=p.parse_args(argv)
    try:
        if a.action=='catalog':result={'presets':PRESETS,'semantic_anatomy_model':'NOT_INSTALLED_BY_THIS_MODULE','images_written':0}
        elif a.action in ('init','freeze'):
            common.need(a.operation_id and a.masters_root,'ROOT_AND_OPERATION_REQUIRED')
            out=PRODUCT/'build/experiments/quality-requests'/a.operation_id
            common.need(out.parent.resolve()==(PRODUCT/'build/experiments/quality-requests').resolve() and not out.exists(),'NEW_REQUEST_OPERATION_REQUIRED')
            if a.action=='init':
                common.need(a.identity and a.inventory and a.inventory_sha256 and a.preset in PRESETS and a.art_standard and a.art_standard_sha256,'INIT_INPUTS_REQUIRED')
                inv={'path':str(common.path(a.inventory)),'sha256':a.inventory_sha256};native=slots(inv);groups={int(v) for v in a.groups.split(',')} if a.groups else None
                frames={int(v) for v in a.frames.split(',')} if a.frames else None
                scope=[list(k) for k in native if k[1] in a.identity and (groups is None or k[0]=='def' and k[2] in groups) and (frames is None or k[0]=='def' and k[3] in frames)]
                common.need(scope and all(any(k[1]==ident for k in scope) for ident in a.identity),'IDENTITY_SCOPE_NOT_FOUND')
                standard={'path':str(common.path(a.art_standard)),'sha256':a.art_standard_sha256};standard['version']=common.doc(standard)['version']
                profile={'schema':common.PROFILE,'preset':a.preset,'review_status':'DRAFT','replaces_canonical_art_standard':False,
                    'native_inventory':inv,'art_standard':standard,'expected_slots':scope,'directions':{},'reference_images':[],
                    'rules':{'flat_boundary_fraction':.25,'color_lab_distance_hint':25,'alpha_envelope_height_hint':.15}}
                profile.update(production.template(a.preset,scope,native,inv))
                if a.native_mask_root:
                    common.need(a.preset in ('roads-rivers','road-connection'),'NATIVE_MASK_ROOT_REQUIRES_ROAD_RIVER_PRESET')
                    mask_root=common.path(a.native_mask_root)
                    bindings=[]
                    for k in scope:
                        if k[0]!='def':continue
                        candidate=mask_root/k[1]/f'{k[2]}_{k[3]}.png'
                        if candidate.is_file():bindings.append({'key':k,'source':file_ref(candidate)})
                    profile['strategies']['native_alpha_masks']=bindings
                out.mkdir(parents=True);pp=out/'production-profile.json';pp.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');pref=file_ref(pp)
            else:
                common.need(a.profile and a.profile_sha256,'PINNED_PROFILE_REQUIRED');pref={'path':str(common.path(a.profile)),'sha256':a.profile_sha256}
                frozen_profile=common.doc(pref)
                production.road_seam_brief(frozen_profile)  # Validate guide pins before creating a request directory.
                out.mkdir(parents=True)
            manifest=freeze(pref,a.masters_root,a.operation_id);mp=out/'manifest.json';mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            (out/'generation-brief.md').write_text(brief(common.doc(pref)),encoding='utf-8')
            result={'status':'FROZEN','manifest':file_ref(mp),'profile':pref,'scope_count':len(manifest['items'])}
        else:
            common.need(all((a.manifest,a.manifest_sha256,a.outdir,a.operation_id)),'FROZEN_REQUEST_REQUIRED')
            if a.renderer:common.pin({'path':a.renderer,'sha256':a.renderer_sha256})
            result=execute(a.action,{'path':str(common.path(a.manifest)),'sha256':a.manifest_sha256},a.outdir,a.operation_id)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as e:
        print(json.dumps({'status':'BLOCKED','code':'BATCH_QUALITY_CONTRACT','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
