"""Read-only category scale drafts and complete native-slot scale reports.

No body-size inference from alpha bboxes. No image resampling or art approval.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
from datetime import datetime,timezone
import gzip
import hashlib
import html
import json
import math
from pathlib import Path
import re
import statistics
import sys
from PIL import Image
import creature_motion_postcheck

PRODUCT=resolve_tool_root(__file__)
MANIFEST='h3.creature.scale-report-manifest.v1'
REPORT='h3.creature.scale-report.v1'
LANDMARKS='h3.creature.scale-landmarks.v1'
CATEGORIES={
 'infantry':{'label':'步兵','measurement_scope':'whole_subject','anchor_role':'supporting_foot'},
 'mage':{'label':'法师','measurement_scope':'whole_subject','anchor_role':'supporting_foot_or_root'},
 'mounted':{'label':'骑乘单位','measurement_scope':'rider_mount_and_equipment','anchor_role':'mount_ground_contact'},
 'large_creature':{'label':'大型生物','measurement_scope':'whole_subject','anchor_role':'feet_or_body_root'},
}


def need(condition,code):
    if not condition:raise ValueError(code)


def token(value):
    need(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',value),'INVALID_SCALE_ID')
    return value


def path(value):
    p=Path(value);need(p.is_absolute(),'ABSOLUTE_SCALE_PATH_REQUIRED')
    for item in (p,*p.parents):
        if item.exists():need(not item.is_symlink() and not getattr(item.lstat(),'st_file_attributes',0)&0x400,'SCALE_REPARSE_PATH_BLOCKED')
    return p.resolve()


def digest(raw):return hashlib.sha256(raw).hexdigest()


def pin(value):
    p=path(value['path']);need(p.is_file() and p.stat().st_size<=256*1024*1024,'SCALE_INPUT_FILE_INVALID')
    raw=p.read_bytes();need(digest(raw)==value['sha256'],'SCALE_INPUT_SHA_DRIFT')
    return p,raw


def reference(p):
    p=path(str(p));raw=p.read_bytes();return {'path':str(p),'sha256':digest(raw),'bytes':len(raw)}


def document(ref):return json.loads(pin(ref)[1])


def write_new(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def native_slots(ref,identity):
    p,raw=pin(ref);value=json.loads(gzip.decompress(raw) if p.suffix.lower()=='.gz' else raw)
    rows=[r for r in value['rows'] if r.get('kind')=='def' and r.get('category')=='creatures' and r.get('identity')==identity]
    need(rows,'SCALE_IDENTITY_HAS_NO_CREATURE_SLOTS');out=[];seen=set()
    for r in sorted(rows,key=lambda x:(x['group'],x['frame'])):
        key=['def',identity,r['group'],r['frame']];need(tuple(key) not in seen,'AMBIGUOUS_NATIVE_SCALE_SLOT');seen.add(tuple(key))
        g={'canvas_1x':r['canvas'],'crop_1x':r['crop'],'origin_1x':r['anchor']}
        need(all(isinstance(v,list) and len(v)==2 and all(type(n) is int for n in v) for v in g.values()),'INVALID_NATIVE_SCALE_GEOMETRY')
        need(min(g['canvas_1x']+g['crop_1x'])>0,'INVALID_NATIVE_SCALE_DIMENSIONS')
        out.append({'key':key,'geometry':g,'resource':r.get('resource'),'native_lod':r.get('native_lod'),
            'reference_1x':r.get('reference_1x'),'references_3x':r.get('x3',[]),
            'identity_label':r.get('label') or identity,'faction':r.get('faction'),'form':r.get('form')})
    return out


def png_layout(ref):
    _,raw=pin(ref)
    import io
    with Image.open(io.BytesIO(raw)) as im:
        need(im.format=='PNG' and im.width*im.height<=64*1024*1024 and getattr(im,'n_frames',1)==1,'SCALE_SINGLE_PNG_REQUIRED')
        im.load();has_alpha='A' in im.getbands() or 'transparency' in im.info
        alpha=im.convert('RGBA').getchannel('A');box=alpha.point(lambda x:255 if x>=16 else 0).getbbox()
        strong=alpha.point(lambda x:255 if x>=128 else 0).getbbox()
        margins=[box[0],box[1],im.width-box[2],im.height-box[3]] if box else None
        return {'pixels':list(im.size),'has_alpha':has_alpha,'alpha_bbox16':list(box) if box else None,
            'alpha_bbox128':list(strong) if strong else None,'transparent_margins_alpha16':margins,
            'touches_canvas_edge':bool(margins is not None and min(margins)==0),
            'bbox_is_body_measurement':False}


def source_key(path,identity,masters_dir=None):
    path=Path(path); name=path.name
    match=re.fullmatch(r'(\d+)_(\d+)\.png',name,re.I)
    if match and (any(parent.name.upper()==identity.upper() for parent in path.parents)
                  or masters_dir is not None and path.parent==Path(masters_dir)):
        return ['def',identity,int(match[1]),int(match[2])]
    # Older slot/master naming is detected for migration reporting only; it is
    # never accepted as the current canonical filename.
    match=re.fullmatch(r'(?:def_)?'+re.escape(identity)+r'(?:\.DEF)?_(\d+)_(\d+)(?:__[0-9a-fA-F]{12})?\.png',name,re.I)
    return ['def',identity,int(match[1]),int(match[2])] if match else None


def input_context(ref):
    if ref is None:return {}
    value=document(ref);need(value.get('schema')=='h3.creature.scale-context.v1','SCALE_CONTEXT_SCHEMA_INVALID')
    for field in ('landmarks','approved_sample','art_standard','motion_review'):
        if field in value and field!='approved_sample':pin(value[field])
    source_reference=value.get('source_pixel_reference')
    if source_reference is not None:
        need(isinstance(source_reference,dict) and set(source_reference)=={'key','image_sha256'},
            'SCALE_SOURCE_PIXEL_REFERENCE_INVALID')
        key=source_reference.get('key');sha=source_reference.get('image_sha256')
        need(isinstance(key,list) and len(key)==4 and key[0]=='def' and isinstance(key[1],str)
            and all(type(v) is int and v>=0 for v in key[2:]),'SCALE_SOURCE_PIXEL_REFERENCE_KEY_INVALID')
        need(isinstance(sha,str) and re.fullmatch(r'[0-9a-f]{64}',sha),'SCALE_SOURCE_PIXEL_REFERENCE_SHA_INVALID')
    return value


def geometry_binding(image_ref,slot,receipt_ref,required_scale=None):
    if receipt_ref is None:return {'status':'NOT_BOUND','K':None}
    receipt=document(receipt_ref);g=slot['geometry'];scale=receipt.get('target_scale')
    need(receipt.get('schema')=='h3.motion.receipt.v1','SCALE_REGISTRATION_RECEIPT_UNSUPPORTED')
    need(type(scale) is int and scale>=3 and (required_scale is None or scale==required_scale),'SCALE_REGISTRATION_FACTOR_INVALID')
    need(receipt.get('png_sha256')==image_ref['sha256'],'SCALE_REGISTRATION_IMAGE_MISMATCH')
    entry=receipt['contract']['native_entry']
    need(entry['key']==slot['key'] and entry['targets']==g,'SCALE_REGISTRATION_NATIVE_KEY_OR_GEOMETRY_MISMATCH')
    origin=receipt.get('origin_3x') if scale==3 else receipt.get('origin_at_target_scale')
    need(origin==[n*scale for n in g['origin_1x']],'SCALE_REGISTRATION_ORIGIN_MISMATCH')
    layout=png_layout(image_ref)
    need(receipt.get('output_pixels')==layout['pixels']==[n*scale for n in g['crop_1x']],'SCALE_REGISTRATION_CROP_SIZE_MISMATCH')
    return {'status':'RECEIPT_BOUND_DIMENSIONS_MATCH','K':scale,'receipt':receipt_ref,
        'binding_scope':'tool receipt, PNG SHA and native geometry; this report does not replay the renderer'}


def length(points,pixels):
    need(isinstance(points,list) and len(points)>=2,'SCALE_ANATOMICAL_PATH_MISSING')
    for xy in points:
        need(isinstance(xy,list) and len(xy)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in xy),'SCALE_LANDMARK_POINT_INVALID')
        need(0<=xy[0]<pixels[0] and 0<=xy[1]<pixels[1],'SCALE_LANDMARK_OUTSIDE_IMAGE')
    result=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(points,points[1:]))
    need(result>0,'SCALE_ANATOMICAL_PATH_EMPTY');return result


def landmarks_for(entry,image_ref,layout,category,K):
    if entry is None:return {'status':'NOT_MEASURED','reason':'No SHA-bound anatomical landmarks','ratio_to_approved_reference':None}
    need(entry.get('image_sha256')==image_ref['sha256'] and entry.get('coordinate_space')=='source_pixels','SCALE_LANDMARK_IMAGE_BINDING_INVALID')
    need(entry.get('measurement_scope')==CATEGORIES[category]['measurement_scope'],'SCALE_MEASUREMENT_SCOPE_MISMATCH')
    paths=entry.get('paths',{});names=['head','torso','body']
    if category=='mounted':names+=['rider_axis','mount_axis','equipment_extent']
    missing=[name for name in names if not isinstance(paths.get(name),list) or len(paths[name])<2]
    if missing:return {'status':'NOT_MEASURED','reason':'Missing anatomical/component paths: '+', '.join(missing),'ratio_to_approved_reference':None}
    if not (isinstance(entry.get('pose_group'),str) and entry['pose_group'] and isinstance(entry.get('direction'),str) and entry['direction']):
        return {'status':'NOT_MEASURED','reason':'Explicit comparable pose group and direction are missing','ratio_to_approved_reference':None}
    values={name:length(paths.get(name),layout['pixels']) for name in names}
    root=entry.get('root_anchor_pixels')
    if root is not None:
        need(isinstance(root,list) and len(root)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in root)
            and 0<=root[0]<layout['pixels'][0] and 0<=root[1]<layout['pixels'][1],'SCALE_ROOT_ANCHOR_INVALID')
    return {'status':'MEASURED' if K is not None else 'MEASURED_SOURCE_PIXELS_ONLY',
        'pose_group':entry['pose_group'],'direction':entry['direction'],'measurement_scope':entry['measurement_scope'],
        'anatomical_lengths_source_pixels':values,'anatomical_lengths_native_units':{k:v/K for k,v in values.items()} if K else None,
        'head_to_body_ratio':values['head']/values['body'],'root_anchor_pixels':root,
        'ratio_to_approved_reference':None,'comparability':'K_UNBOUND' if K is None else 'REFERENCE_NOT_YET_COMPARED'}


def select_sources(snapshot,identity,masters_dir=None):
    by_key={};extras=[]
    for image in snapshot:
        name=Path(image['path']).name;key=source_key(image['path'],identity,masters_dir)
        if key is None:extras.append(image);continue
        by_key.setdefault(tuple(key),[]).append(image)
    selected={}
    for key,images in by_key.items():
        expected=f'{key[2]}_{key[3]}.png';canonical=[x for x in images if Path(x['path']).name==expected]
        candidates=canonical or images
        selected[key]={'status':'PRESENT' if canonical else 'NONCANONICAL_NAME' if len(candidates)==1 else 'AMBIGUOUS',
            'image':candidates[0] if len(candidates)==1 else None,'candidates':images,'expected_name':expected}
    return selected,extras


def approved_reference(context,identity,slots,category,annotations):
    sample=context.get('approved_sample')
    if sample is None:return None
    key=sample['key'];need(key[1]==identity and key in [x['key'] for x in slots],'SCALE_SAMPLE_IDENTITY_OR_SLOT_INVALID')
    image=sample['image'];layout=png_layout(image);approval=document(sample['approval'])
    need(approval.get('schema')=='h3.creature.scale-sample-approval.v1' and approval.get('key')==key
        and approval.get('image_sha256')==image['sha256'] and approval.get('status') in ('assistant_art_pass','human_approved'), 'SCALE_APPROVED_SAMPLE_EVIDENCE_INVALID')
    slot=next(x for x in slots if x['key']==key)
    registration=geometry_binding(image,slot,sample.get('registration'))
    body=landmarks_for(annotations.get((tuple(key),image['sha256'])),image,layout,category,registration['K'])
    return {'key':key,'image':image,'approval':sample['approval'],'layout':layout,'sampling':registration,'body':body}


def comparable(a,b):
    return a.get('status')=='MEASURED' and b.get('status')=='MEASURED' and all(a.get(k)==b.get(k) for k in ('pose_group','direction','measurement_scope'))


def source_pixel_comparison(current,reference):
    """Compare manually marked body paths only on a shared source-pixel grid.

    This is a relative diagnostic, never K, an absolute scale result, or art approval.
    """
    if reference is None:return {'status':'NOT_REQUESTED','ratio_to_source_pixel_reference':None}
    body=current['body'];reference_body=reference['body']
    reference_key=reference['key'];reference_sha=reference['image']['sha256'] if reference.get('image') else None
    common={'reference_key':reference_key,'reference_image_sha256':reference_sha,'K_bound':False,
        'automatic_resize':False,'art_approval':False,'diagnostic_only':True}
    if tuple(current['key'])==tuple(reference_key):
        measured=reference_body.get('status')=='MEASURED_SOURCE_PIXELS_ONLY'
        return {**common,'status':'SOURCE_PIXEL_REFERENCE' if measured else 'REFERENCE_LANDMARKS_NOT_MEASURED',
            'ratio_to_source_pixel_reference':1.0 if measured else None,
            'deviation_percent_from_source_pixel_reference':0.0 if measured else None}
    if body.get('status')!='MEASURED_SOURCE_PIXELS_ONLY' or reference_body.get('status')!='MEASURED_SOURCE_PIXELS_ONLY':
        return {**common,'status':'NOT_COMPARABLE_SOURCE_ONLY_LANDMARKS_REQUIRED','ratio_to_source_pixel_reference':None}
    current_layout=current.get('layout');reference_layout=reference.get('layout')
    if not current_layout or not reference_layout or current_layout.get('pixels')!=reference_layout.get('pixels'):
        return {**common,'status':'NOT_COMPARABLE_SOURCE_RESOLUTION_DIFFERS','ratio_to_source_pixel_reference':None,
            'candidate_source_pixels':current_layout.get('pixels') if current_layout else None,
            'reference_source_pixels':reference_layout.get('pixels') if reference_layout else None}
    if not all(body.get(k)==reference_body.get(k) for k in ('pose_group','direction','measurement_scope')):
        return {**common,'status':'NOT_COMPARABLE_POSE_DIRECTION_OR_SCOPE_DIFFERS','ratio_to_source_pixel_reference':None}
    values=body.get('anatomical_lengths_source_pixels',{});reference_values=reference_body.get('anatomical_lengths_source_pixels',{})
    current_length=values.get('body');reference_length=reference_values.get('body')
    need(isinstance(current_length,(int,float)) and current_length>0 and isinstance(reference_length,(int,float))
        and reference_length>0,'SCALE_SOURCE_PIXEL_BODY_LENGTH_INVALID')
    ratios={name:values[name]/reference_values[name] for name in ('head','torso','body')
        if name in values and name in reference_values and reference_values[name]>0}
    root=body.get('root_anchor_pixels');reference_root=reference_body.get('root_anchor_pixels')
    root_delta=[root[i]-reference_root[i] for i in range(2)] if root is not None and reference_root is not None else None
    ratio=current_length/reference_length
    return {**common,'status':'DIAGNOSTIC_ONLY_COMPARABLE_SOURCE_PIXELS',
        'ratio_to_source_pixel_reference':ratio,'deviation_percent_from_source_pixel_reference':(ratio-1)*100,
        'path_length_ratios':ratios,'root_anchor_delta_source_pixels':root_delta,
        'source_pixels':current_layout['pixels']}


def make_report(manifest):
    identity=manifest['identity'];category=manifest['category'];slots=native_slots(manifest['native_inventory'],identity)
    need(slots==manifest['native_slots'],'SCALE_FROZEN_NATIVE_SLOTS_CHANGED')
    context=input_context(manifest.get('context'));annotations={}
    if 'landmarks' in context:
        document_value=document(context['landmarks']);need(document_value.get('schema')==LANDMARKS,'SCALE_LANDMARK_SCHEMA_INVALID')
        for entry in document_value['frames']:
            key=(tuple(entry['key']),entry.get('image_sha256'));need(key not in annotations,'DUPLICATE_SCALE_LANDMARK_IMAGE_SLOT');annotations[key]=entry
    baseline=None;baseline_error=None
    try:baseline=approved_reference(context,identity,slots,category,annotations)
    except ValueError as error:
        if 'DRIFT' in str(error):raise
        baseline_error=str(error)
    selected,unmatched=select_sources(manifest['source_snapshot'],identity,manifest.get('masters_dir'))
    motion_review_ref=context.get('motion_review')
    motion_review_doc=document(motion_review_ref) if motion_review_ref else None
    motion_review=creature_motion_postcheck.evaluate(motion_review_doc,identity,
        manifest['native_inventory']['sha256'],slots,selected)
    motion_review_groups={row['group']:row['status'] for row in motion_review['groups']}
    registrations={tuple(x['key']):x['receipt'] for x in context.get('working_registrations',[])}
    deliveries={tuple(x['key']):x for x in context.get('deliveries_3x',[])}
    records=[]
    for slot in slots:
        key=tuple(slot['key']);source=selected.get(key,{'status':'MISSING','image':None,'candidates':[],
            'expected_name':f'{key[2]}_{key[3]}.png'})
        row={**slot,'expected_name':source['expected_name'],'master_status':source['status'],'image':source['image'],
            'candidate_files':source['candidates'],'layout':None,'sampling':{'status':'NOT_BOUND','K':None},
            'body':{'status':'NOT_MEASURED','ratio_to_approved_reference':None},
            'native_geometry_status':'INVENTORY_BOUND','formal_3x':{'status':'NOT_PROVIDED'},'runtime_status':'NOT_VERIFIED',
            'source_provenance_status':'NOT_EVALUATED_BY_THIS_READ_ONLY_REPORT_EXISTING_ADMISSION_UNCHANGED','automatic_art_pass':False,'automatic_resize':False}
        if source['image'] is not None:
            try:row['layout']=png_layout(source['image'])
            except (ValueError,OSError) as error:
                if 'DRIFT' in str(error):raise
                row['master_status']='DECODE_ERROR';row['decode_error']=str(error)
            if row['layout'] is not None:
                try:row['sampling']=geometry_binding(source['image'],slot,registrations.get(key))
                except ValueError as error:
                    if 'DRIFT' in str(error):raise
                    row['sampling']={'status':'INVALID_REGISTRATION_EVIDENCE','K':None,'error':str(error)}
                try:row['body']=landmarks_for(annotations.get((key,source['image']['sha256'])),source['image'],row['layout'],category,row['sampling']['K'])
                except ValueError as error:
                    if 'DRIFT' in str(error):raise
                    row['body']={'status':'NOT_MEASURED','reason':str(error),'ratio_to_approved_reference':None}
            if baseline and comparable(row['body'],baseline['body']):
                ratio=row['body']['anatomical_lengths_native_units']['body']/baseline['body']['anatomical_lengths_native_units']['body']
                row['body'].update(ratio_to_approved_reference=ratio,deviation_percent_from_reference=(ratio-1)*100,comparability='SAME_EXPLICIT_POSE_DIRECTION_AND_SCOPE')
            if row['sampling']['K'] is not None and row['body'].get('root_anchor_pixels') is not None:
                row['body']['root_anchor_native_canvas']=[row['body']['root_anchor_pixels'][i]/row['sampling']['K']+slot['geometry']['origin_1x'][i] for i in range(2)]
        if key in deliveries:
            delivery=deliveries[key]
            try:row['formal_3x']=geometry_binding(delivery['image'],slot,delivery['receipt'],3)
            except ValueError as error:
                if 'DRIFT' in str(error):raise
                row['formal_3x']={'status':'INVALID_3X_EVIDENCE','error':str(error)}
        records.append(row)
    source_reference=context.get('source_pixel_reference')
    source_reference_row=None
    if source_reference is not None:
        source_reference_key=tuple(source_reference['key'])
        source_reference_row=next((r for r in records if tuple(r['key'])==source_reference_key),None)
        need(source_reference_row is not None and source_reference_row.get('image') is not None,
            'SCALE_SOURCE_PIXEL_REFERENCE_SLOT_MISSING')
        need(source_reference_row['image']['sha256']==source_reference['image_sha256'],
            'SCALE_SOURCE_PIXEL_REFERENCE_SHA_DRIFT')
    for row in records:
        if source_reference is not None:
            row['source_pixel_comparison']=source_pixel_comparison(row,source_reference_row)
    known_keys={tuple(x['key']) for x in slots}
    extras=unmatched+[{'key':list(key),**x} for key,x in selected.items() if key not in known_keys]
    groups=[]
    for group in sorted({r['key'][2] for r in records}):
        rows=[r for r in records if r['key'][2]==group];ratios=[r['body']['ratio_to_approved_reference'] for r in rows if r['body'].get('ratio_to_approved_reference') is not None]
        source_pixel_deltas=[r['source_pixel_comparison']['deviation_percent_from_source_pixel_reference'] for r in rows
            if r.get('source_pixel_comparison',{}).get('status')=='DIAGNOSTIC_ONLY_COMPARABLE_SOURCE_PIXELS']
        measured=sum(r['body']['status'] in ('MEASURED','MEASURED_SOURCE_PIXELS_ONLY') for r in rows)
        boxes=[r['layout']['alpha_bbox16'] for r in rows if r['layout'] and r['layout']['alpha_bbox16']]
        groups.append({'group':group,'slots':len(rows),'missing':sum(r['master_status']=='MISSING' for r in rows),
            'K_values_from_bound_receipts':sorted({r['sampling']['K'] for r in rows if r['sampling']['K'] is not None}),
            'body_scale_status':'MEASURED' if measured else 'NOT_MEASURED','measured_landmark_slots':measured,
            'reference_comparison_status':'COMPARABLE' if ratios else 'NOT_COMPARABLE' if measured else 'NOT_MEASURED','comparable_landmark_slots':len(ratios),
            'median_body_ratio_to_approved_reference':statistics.median(ratios) if ratios else None,
            'median_deviation_percent':(statistics.median(ratios)-1)*100 if ratios else None,
            'source_pixel_comparable_landmark_slots':len(source_pixel_deltas),
            'source_pixel_median_deviation_percent':statistics.median(source_pixel_deltas) if source_pixel_deltas else None,
            'source_pixel_comparison_is_diagnostic_only':True,
            'source_canvas_pixels_set':sorted({tuple(r['layout']['pixels']) for r in rows if r['layout']}),
            'alpha_bbox16_width_range':[min(b[2]-b[0] for b in boxes),max(b[2]-b[0] for b in boxes)] if boxes else None,
            'alpha_bbox16_height_range':[min(b[3]-b[1] for b in boxes),max(b[3]-b[1] for b in boxes)] if boxes else None,
            'bbox_variation_is_layout_only':True,
            'motion_transition_review_status':motion_review_groups.get(group,'NOT_PROVIDED')})
    proposed=context.get('proposed_layout',{})
    profile={'category':category,**CATEGORIES[category],'status':'DRAFT','body_status':'NOT_MEASURED',
        'work_canvas_pixels':proposed.get('work_canvas_pixels'),'subject_occupancy_target':proposed.get('subject_occupancy_target'),
        'root_anchor_pixels':proposed.get('root_anchor_pixels'),'transparent_margin_target':proposed.get('transparent_margin_target'),
        'layout_source':'USER_PROPOSAL_NOT_MEASURED' if proposed else 'NOT_SPECIFIED',
        'approved_reference':baseline,'approved_reference_error':baseline_error,'final_native_geometry_overridden':False}
    if baseline:
        profile.update(work_canvas_pixels=baseline['layout']['pixels'],layout_source='APPROVED_SAMPLE_OBSERVED_LAYOUT',
            observed_alpha_bbox16=baseline['layout']['alpha_bbox16'],observed_transparent_margins=baseline['layout']['transparent_margins_alpha16'],
            root_anchor_pixels=baseline['body'].get('root_anchor_pixels'),body_status=baseline['body']['status'])
        if baseline['body']['status']=='MEASURED':profile['status']='MEASURED_REFERENCE_DRAFT'
        b=baseline['layout']['alpha_bbox16'];w,h=baseline['layout']['pixels']
        profile['observed_composition_bbox_area_fraction']=(b[2]-b[0])*(b[3]-b[1])/(w*h) if b else None
        profile['composition_fraction_is_body_scale']=False
    return {'schema':REPORT,'mode':manifest['mode'],'identity':identity,'faction':manifest.get('faction'),'form':manifest.get('form'),
        'inventory':manifest['native_inventory'],'project_art_standard_reference':context.get('art_standard'),
        'source_pixel_reference':({'key':source_reference['key'],'image_sha256':source_reference['image_sha256'],
            'status':'DIAGNOSTIC_ONLY_NOT_APPROVED_REFERENCE','K_bound':False} if source_reference else None),
        'profile':profile,'slots':records,'action_groups':groups,'extra_or_non_slot_images':extras,
        'postproduction_motion_review':motion_review,
        'summary':{'expected_slots':len(records),'present_slots':sum(r['image'] is not None for r in records),
            'missing_slots':sum(r['master_status']=='MISSING' for r in records),'noncanonical_slots':sum(r['master_status']=='NONCANONICAL_NAME' for r in records),
            'ambiguous_slots':sum(r['master_status']=='AMBIGUOUS' for r in records),
            'body_slots_not_measured':sum(r['body']['status']=='NOT_MEASURED' for r in records),
            'motion_review_status':motion_review['status'],
            'motion_review_incomplete_groups':motion_review['summary']['incomplete_groups'],
            'motion_review_risk_groups':motion_review['summary']['risk_groups'],
            'motion_review_failed_groups':motion_review['summary']['failed_groups']},
        'sampling_K_from_alpha_bbox':False,'native_canvas_crop_origin_modified':False,'images_written':0,
        'automatic_image_resize':False,'automatic_art_approval':False,'runtime_executed':False}


def all_refs(value):
    if isinstance(value,dict):
        if 'path' in value and 'sha256' in value:yield value
        for v in value.values():yield from all_refs(v)
    elif isinstance(value,list):
        for v in value:yield from all_refs(v)


def image_files(root):
    return sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower()=='.png'),key=lambda p:p.name.casefold())


def validate_manifest(ref,mode,operation):
    manifest=document(ref)
    need(manifest.get('schema')==MANIFEST and manifest.get('mode')==mode and manifest.get('operation_id')==operation,'SCALE_MANIFEST_MODE_OR_ID_MISMATCH')
    token(manifest['identity']);need(manifest['category'] in CATEGORIES,'SCALE_CATEGORY_NOT_SUPPORTED')
    for pinned in all_refs(manifest):pin(pinned)
    if manifest.get('context'):
        for pinned in all_refs(document(manifest['context'])):pin(pinned)
    source_root=manifest.get('masters_dir')
    if source_root:
        root=path(source_root);need(root.is_dir(),'SCALE_MASTERS_DIRECTORY_MISSING')
        need({str(p.resolve()) for p in image_files(root)}=={x['path'] for x in manifest['source_snapshot']},'SCALE_SOURCE_DIRECTORY_CHANGED_REFREEZE')
        need(all(path(x['path']).parent==root for x in manifest['source_snapshot']),'SCALE_SOURCE_OUTSIDE_SELECTED_DIRECTORY')
    need(len(manifest['source_snapshot'])<=10000,'SCALE_SOURCE_COUNT_LIMIT')
    context=input_context(manifest.get('context'))
    for field in ('working_registrations','deliveries_3x'):
        entries=context.get(field,[]);need(isinstance(entries,list) and len({tuple(x['key']) for x in entries})==len(entries),'DUPLICATE_SCALE_CONTEXT_SLOT')
    proposed=context.get('proposed_layout',{})
    need(isinstance(proposed,dict) and set(proposed)<={'work_canvas_pixels','subject_occupancy_target','root_anchor_pixels','transparent_margin_target'},'SCALE_PROPOSED_LAYOUT_FIELDS_INVALID')
    if 'work_canvas_pixels' in proposed:
        c=proposed['work_canvas_pixels'];need(isinstance(c,list) and len(c)==2 and all(type(v) is int and 0<v<=32768 for v in c),'SCALE_PROPOSED_CANVAS_INVALID')
    if 'subject_occupancy_target' in proposed:
        v=proposed['subject_occupancy_target'];need(type(v) in (int,float) and math.isfinite(v) and 0<v<=1,'SCALE_COMPOSITION_TARGET_INVALID')
    if 'transparent_margin_target' in proposed:
        v=proposed['transparent_margin_target'];need(isinstance(v,list) and len(v)==4 and all(type(n) is int and n>=0 for n in v),'SCALE_PROPOSED_MARGINS_INVALID')
    if 'root_anchor_pixels' in proposed:
        v=proposed['root_anchor_pixels'];need(isinstance(v,list) and len(v)==2 and all(type(n) in (int,float) and math.isfinite(n) and n>=0 for n in v),'SCALE_PROPOSED_ROOT_INVALID')
    return manifest


def freeze(args,mode):
    identity=token(args.identity);token(args.operation_id);need(args.category in CATEGORIES,'SCALE_CATEGORY_REQUIRED')
    inventory={'path':str(path(args.inventory)),'sha256':args.inventory_sha256}
    slots=native_slots(inventory,identity);sources=[];root=path(args.masters_dir) if args.masters_dir else None
    if root:
        need(root.is_dir(),'SCALE_MASTERS_DIRECTORY_MISSING');sources=[reference(p) for p in image_files(root)]
    context={'path':str(path(args.context)),'sha256':args.context_sha256} if args.context else None
    if context:
        for pinned in all_refs(document(context)):pin(pinned)
    out=PRODUCT/'build/experiments/scale-requests'/(args.operation_id+'.json')
    manifest={'schema':MANIFEST,'mode':mode,'operation_id':args.operation_id,'identity':identity,'category':args.category,
        'faction':args.faction or slots[0].get('faction'),'form':args.form or slots[0].get('form'),
        'metadata_unknown_values_are_not_guessed':True,'native_inventory':inventory,'native_slots':slots,
        'masters_dir':str(root) if root else None,'source_snapshot':sources,'context':context,
        'created_utc':datetime.now(timezone.utc).isoformat()}
    if out.exists():raise ValueError('SCALE_REQUEST_EXISTS_USE_NEW_OPERATION')
    write_new(out,manifest)
    return {'status':'FROZEN','manifest':reference(out),'native_slots':len(slots),'image_files_written':0,
        'next_command':f'scale-{mode} check|run|verify','output_root':str(PRODUCT/'build/experiments'/('scale-'+mode)/args.operation_id)}


def markdown(report):
    summary=report['summary'];profile=report['profile']
    rows=['# 类别尺度草案与全槽检查报告','',f"身份：`{report['identity']}`；类别：{profile['label']}；档案：`{profile['status']}`。",
        '','bbox只描述透明留边／裁切与FIT。没有哈希绑定解剖标点时为NOT_MEASURED；采样K未绑定时不比较绝对身体尺度。',
        '可选源像素参考只在分辨率、姿势组、方向和衡量范围一致时报告相对路径差异；它不是K、绝对尺度结论或美术审批。',
        '不修改图片，不自动缩放或批准美术。正式3X与运行状态另列。','',
        f"原生槽 {summary['expected_slots']}；已有 {summary['present_slots']}；缺少 {summary['missing_slots']}；命名不规范 {summary['noncanonical_slots']}；歧义 {summary['ambiguous_slots']}。",'',
        '## 类别工作档案','',f"- 衡量范围：`{profile['measurement_scope']}`。骑乘单位须覆盖骑手、坐骑、装备整体。",
        f"- 工作画幅：`{profile['work_canvas_pixels']}`；主体构图占比目标：`{profile['subject_occupancy_target']}`。未知字段不猜。",
        f"- 脚底／根部：`{profile['root_anchor_pixels']}`；透明留边目标：`{profile['transparent_margin_target']}`。",
        '- 以上是工作档案，不覆盖逐槽原生canvas/crop/origin，不强制所有帧同画布或同高度。','',
        '## 全部原生槽','',
        '| DEF／组／帧 | 名称与文件状态 | SHA-256 | 实际像素 | 原生canvas / crop / origin | K及依据 | 透明主体框16 | 身体尺度 | 相对核准参考偏差 | 正式3X | 运行 |',
        '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in report['slots']:
        g=r['geometry'];layout=r['layout'] or {};body=r['body'];image=r['image'] or {}
        values=[str(r['key']),r['expected_name']+' / '+r['master_status'],image.get('sha256','—'),str(layout.get('pixels','—')),
            f"{g['canvas_1x']} / {g['crop_1x']} / {g['origin_1x']}",str(r['sampling']['K'])+' / '+r['sampling']['status'],
            str(layout.get('alpha_bbox16','—')),body['status'],str(body.get('deviation_percent_from_reference','NOT_MEASURED')),
            r['formal_3x']['status'],r['runtime_status']]
        rows.append('| '+' | '.join(v.replace('|','\\|').replace('\n',' ') for v in values)+' |')
    rows+=['','## 动作组差异','',
        '| 组 | 槽数 | 缺少 | 绑定K | 可比解剖帧 | 相对核准参考中位偏差% | 源像素可比帧 | 源像素中位偏差%（仅诊断） |', '|---|---:|---:|---|---:|---|---:|---|']
    for g in report['action_groups']:
        rows.append(f"| {g['group']} | {g['slots']} | {g['missing']} | {g['K_values_from_bound_receipts']} | {g['comparable_landmark_slots']} | {g['median_deviation_percent'] if g['median_deviation_percent'] is not None else 'NOT_MEASURED'} | {g['source_pixel_comparable_landmark_slots']} | {g['source_pixel_median_deviation_percent'] if g['source_pixel_median_deviation_percent'] is not None else 'NOT_COMPARABLE'} |")
    rows+=['','缺漏、命名和bbox提示不自动触发重绘或缩放。source来源资格、原生几何、K、身体尺度、正式3X、实机各自独立。','',
        '## 动作连续性事后复核','',
        f"- 状态：`{report['postproduction_motion_review']['status']}`；未完成 {report['postproduction_motion_review']['summary']['incomplete_groups']} 组；风险 {report['postproduction_motion_review']['summary']['risk_groups']} 组；失败 {report['postproduction_motion_review']['summary']['failed_groups']} 组。",
        '- 核验 SHA 绑定、官方帧顺序、相邻转场、loop/non-loop 分类，以及身份、解剖、装备、材质和动作逻辑审查记录完整性。此项不自动判断动作含义或批准美术。','']
    return '\n'.join(rows)


def html_report(report):
    esc=lambda value:html.escape(str(value),quote=True)
    rows=[]
    for r in report['slots']:
        image=r['image'];g=r['geometry'];layout=r['layout'] or {};body=r['body']
        name=esc(r['expected_name'])
        if image:name='<a href="'+esc(Path(image['path']).as_uri())+'">'+name+'</a>'
        pixel_comparison=r.get('source_pixel_comparison',{})
        cells=[name,esc(r['key'][2]),esc(r['key'][3]),esc(r['master_status']),esc(image['sha256'] if image else '—'),
            esc(layout.get('pixels','—')),esc(g['canvas_1x']),esc(g['crop_1x']),esc(g['origin_1x']),
            esc(str(r['sampling']['K'])+' / '+r['sampling']['status']),esc(layout.get('alpha_bbox16','—')),
            '<details><summary>'+esc(body['status'])+'</summary><pre>'+esc(json.dumps(body,ensure_ascii=False,indent=2))+'</pre></details>',
            esc(body.get('deviation_percent_from_reference','NOT_MEASURED')),
            esc(pixel_comparison.get('status','NOT_REQUESTED')),
            esc(pixel_comparison.get('deviation_percent_from_source_pixel_reference','—')),
            esc(r['formal_3x']['status']),esc(r['runtime_status'])]
        rows.append('<tr data-group="'+esc(r['key'][2])+'">'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    headings=['文件名','组','帧','文件状态','SHA-256','像素','canvas','crop','origin','K / 依据','透明框16（仅FIT）','身体尺度','参考偏差%','源像素对比','源像素偏差%（仅诊断）','正式3X','运行']
    style='body{font:14px system-ui;margin:24px;background:#f3f5f7;color:#233348}table{border-collapse:collapse;background:white}td,th{padding:8px;border:1px solid #ccd5df;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#dae5ee}td:nth-child(5){max-width:220px;overflow:auto;font:11px monospace}.scroll{overflow:auto;max-height:75vh}input{padding:8px;min-width:320px}'
    motion=report['postproduction_motion_review'];motion_rows=[]
    for group in motion['groups']:
        motion_rows.append('<tr><td>'+esc(group['group'])+'</td><td>'+esc(group['status'])+'</td><td>'+esc(group['loop_mode'])+'</td><td>'+esc(group['transition_count'])+'</td><td>'+esc('; '.join(group['errors']) or '—')+'</td></tr>')
    motion_table='<table><thead><tr><th>动作组</th><th>转场复核</th><th>循环类型</th><th>转场数</th><th>待处理项</th></tr></thead><tbody>'+''.join(motion_rows)+'</tbody></table>'
    return '<!doctype html><meta charset="utf-8"><title>全槽尺度报告</title><style>'+style+'</style><h1>'+esc(report['identity'])+' · 类别尺度与全槽报告</h1><p>'+esc(report['profile']['status'])+'；bbox不是身体尺度；缺标点为NOT_MEASURED。报告不缩放、不审批图片。</p><p>源像素比较只作相同分辨率、姿势组、方向和衡量范围下的相对诊断，不代表K绑定或绝对尺度通过。</p><p>文件名链接打开原图；不生成缩略图副本。</p><h2>动作连续性事后复核：'+esc(motion['status'])+'</h2><p>只核验SHA绑定、帧顺序、转场和loop/non-loop审查记录完整性；不自动判断动作语义或批准美术。</p>'+motion_table+'<input id="filter" placeholder="筛选文件名、动作组或状态"><div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headings)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div><script>document.getElementById("filter").oninput=function(){let v=this.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(v))}</script>'


def report_payloads(report):
    profiles={name:{'category':name,**description,'status':'DRAFT','work_canvas_pixels':None,'subject_occupancy_target':None,
        'root_anchor_pixels':None,'transparent_margin_target':None,'body_status':'NOT_MEASURED'} for name,description in CATEGORIES.items()}
    profiles[report['profile']['category']]=report['profile']
    encode=lambda obj:(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
    return {'scale-report.json':encode(report),'scale-report.md':markdown(report).encode('utf-8'),
        'scale-report.html':html_report(report).encode('utf-8'),
        'category-scale-profiles-draft.json':encode({'schema':'h3.creature.category-scale-drafts.v1','profiles':profiles,
            'replaces_project_art_standard':False})}


def execute(mode,action,manifest_ref,outdir,operation,renderer=None,renderer_sha=None):
    token(operation);root=path(str(outdir))
    need(root.parent==(PRODUCT/'build/experiments'/('scale-'+mode)).resolve() and root.name==operation,'SCALE_OUTPUT_MUST_BE_NEW_PRODUCT_OPERATION')
    if renderer:pin({'path':renderer,'sha256':renderer_sha})
    manifest=validate_manifest(manifest_ref,mode,operation);report=make_report(manifest);payloads=report_payloads(report)
    if action=='check':return {'status':'READY_FOR_READ_ONLY_REPORT','summary':report['summary'],'images_written':0}
    receipt_path=root/'scale-report-receipt.json'
    if action=='run' and root.exists():need(receipt_path.is_file(),'PARTIAL_OR_FOREIGN_SCALE_OPERATION');action='verify'
    if action=='verify':
        need(receipt_path.is_file(),'SCALE_REPORT_RECEIPT_MISSING');receipt=json.loads(receipt_path.read_bytes())
        need(receipt.get('schema')=='h3.creature.scale-report-receipt.v1' and receipt.get('manifest')==manifest_ref,'SCALE_RECEIPT_BINDING_INVALID')
        need(receipt.get('mode')==mode and receipt.get('operation_id')==operation and receipt.get('images_written')==0
            and receipt.get('automatic_resize') is False and receipt.get('art_approved') is False
            and receipt.get('game_deployed') is False,'SCALE_RECEIPT_CANNOT_GRANT_RESIZE_ART_OR_RUNTIME_APPROVAL')
        need(set(receipt['outputs'])==set(payloads),'SCALE_REPORT_OUTPUT_SET_CHANGED')
        for name,raw in payloads.items():
            actual=receipt['outputs'][name];pin(actual)
            need(path(actual['path'])==root/name and digest(raw)==actual['sha256'],'SCALE_REPORT_REPLAY_MISMATCH')
        return {'status':'VERIFIED_READ_ONLY_REPORT','report':str(root/'scale-report.html'),'images_written':0}
    need(action=='run' and not root.exists(),'SCALE_OPERATION_ALREADY_EXISTS');root.mkdir(parents=True)
    outputs={}
    for name,raw in payloads.items():
        p=root/name
        with p.open('xb') as stream:stream.write(raw)
        outputs[name]=reference(p)
    validate_manifest(manifest_ref,mode,operation)
    write_new(receipt_path,{'schema':'h3.creature.scale-report-receipt.v1','manifest':manifest_ref,'mode':mode,
        'operation_id':operation,'outputs':outputs,'renderer':{'path':renderer,'sha256':renderer_sha},
        'created_utc':datetime.now(timezone.utc).isoformat(),'images_written':0,'automatic_resize':False,'art_approved':False,'game_deployed':False})
    return {'status':'READ_ONLY_REPORT_CREATED','report':str(root/'scale-report.html'),'summary':report['summary'],'images_written':0}


def main(mode,argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('catalog','freeze','check','run','verify'))
    for name in ('inventory','inventory-sha256','identity','category','faction','form','masters-dir','context','context-sha256','operation-id','manifest','manifest-sha256','outdir','renderer','renderer-sha256'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    try:
        if args.action=='catalog':result={'categories':CATEGORIES,'default_profile_status':'DRAFT','default_body_status':'NOT_MEASURED','automatic_image_resize':False}
        elif args.action=='freeze':
            need(all((args.inventory,args.inventory_sha256,args.identity,args.category,args.operation_id)),'SCALE_FREEZE_ARGUMENTS_REQUIRED')
            need(not args.context or args.context_sha256,'SCALE_CONTEXT_SHA_REQUIRED');result=freeze(args,mode)
        else:
            need(all((args.manifest,args.manifest_sha256,args.outdir,args.operation_id)),'SCALE_FROZEN_REQUEST_REQUIRED')
            result=execute(mode,args.action,{'path':str(path(args.manifest)),'sha256':args.manifest_sha256},args.outdir,args.operation_id,args.renderer,args.renderer_sha256)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as error:
        print(json.dumps({'status':'BLOCKED','code':'SCALE_REPORT_CONTRACT','error':str(error)},ensure_ascii=False),file=sys.stderr);return 2
