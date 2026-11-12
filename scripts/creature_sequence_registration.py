"""Anatomy/root registration of an explicit native creature action sequence.

The existing lossless layout solver owns pixel copying. This module admits an
animation only with its pinned binary DEF, complete selected groups, explicit
pose/direction targets, source-bound anatomy, root trajectory and native aliases.
It emits a request for the existing single-sample native-review renderer. No
model inference, anatomical recognition, resampling, art approval or deployment.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import sys

SCHEMA = 'h3.creature.sequence-registration.manifest.v1'
CONTRACT = 'h3.creature.sequence-registration.contract.v1'


def _need(value, code):
    if not value:
        raise ValueError(code)


def native_frames(raw):
    """Read actual group/frame headers and equivalence offsets; never clamp margins."""
    _need(len(raw) >= 784, 'SEQUENCE_NATIVE_DEF_TRUNCATED')
    _, _, _, count = struct.unpack_from('<4I', raw)
    _need(0 < count <= 64, 'SEQUENCE_NATIVE_DEF_GROUP_COUNT')
    at = 784
    result = {}
    seen_groups = set()
    for _ in range(count):
        _need(at+16 <= len(raw), 'SEQUENCE_NATIVE_DEF_TRUNCATED')
        group, n, _, _ = struct.unpack_from('<4I', raw, at)
        _need(group not in seen_groups and 0 < n <= 1024, 'SEQUENCE_NATIVE_DEF_GROUP_INVALID')
        seen_groups.add(group)
        at += 16
        _need(at+n*17 <= len(raw), 'SEQUENCE_NATIVE_DEF_TRUNCATED')
        names = [raw[at+i*13:at+(i+1)*13].split(b'\0',1)[0].decode('ascii') for i in range(n)]
        at += n*13
        offsets = struct.unpack_from('<'+'I'*n, raw, at)
        at += n*4
        for frame, (offset, name) in enumerate(zip(offsets, names)):
            _need(offset+32 <= len(raw), 'SEQUENCE_NATIVE_DEF_FRAME_TRUNCATED')
            _, _, cw, ch, w, h, ox, oy = struct.unpack_from('<6I2i', raw, offset)
            _need(min(cw,ch,w,h)>0 and min(ox,oy)>=0 and ox+w<=cw and oy+h<=ch,
                  'SEQUENCE_NATIVE_DEF_GEOMETRY_INVALID')
            result[(group,frame)] = {'offset':offset,'canvas':[cw,ch], 'crop':[w,h],
                                     'anchor':[ox,oy], 'resource':name}
    return result


def validate(manifest, profile, native, expected):
    import subject_scale_layout as common
    _need(manifest.get('schema') == SCHEMA, 'SEQUENCE_SCHEMA_REQUIRED')
    implementation=manifest.get('implementation',{})
    _need(implementation.get('schema')=='h3.creature.sequence-registration.readers.v1',
          'SEQUENCE_FROZEN_READER_BINDING_REQUIRED')
    for field,executing in [('sequence_reader',Path(__file__)),('layout_reader',Path(common.__file__))]:
        bound=implementation[field]
        common.pin(bound)
        _need(common.sha(executing.read_bytes())==bound['sha256'],'SEQUENCE_FROZEN_READER_DIFFERS')
    _need(manifest.get('output_layout') == 'native-canvas', 'SEQUENCE_FULL_CANVAS_REGISTRATION_REQUIRED')
    contract = manifest.get('sequence_contract', {})
    _need(contract.get('schema') == CONTRACT, 'SEQUENCE_CONTRACT_REQUIRED')
    _need(contract.get('scale_policy') == 'anatomical-targets'
          and contract.get('camera_policy') == 'fixed-native-units'
          and contract.get('trajectory_units') == 'native_canvas_1x', 'SEQUENCE_COORDINATE_POLICY_REQUIRED')
    _need(isinstance(contract.get('root_semantics'),str) and contract['root_semantics'].strip(),
          'SEQUENCE_ROOT_SEMANTICS_REQUIRED')
    _, raw = common.pin(contract['native_def'])
    frames = native_frames(raw)
    groups = contract.get('groups')
    _need(isinstance(groups,list) and groups and all(type(g) is int for g in groups)
          and len(set(groups)) == len(groups), 'SEQUENCE_EXPLICIT_GROUP_SCOPE_REQUIRED')
    _need(set(groups)<={g for g,_ in frames},'SEQUENCE_SELECTED_GROUP_ABSENT')
    identities = {key[1] for key in expected}
    _need(len(identities)==1 and all(key[0]=='def' for key in expected), 'SEQUENCE_ONE_IDENTITY_REQUIRED')
    identity = next(iter(identities))
    _need(Path(contract['native_def']['path']).name.casefold()==(identity+'.def').casefold(),
          'SEQUENCE_BINARY_DEF_IDENTITY_PATH_MISMATCH')
    scope = {('def',identity,g,f) for g,f in frames if g in groups}
    declared = contract.get('logical_scope',[])
    _need(scope and len(scope)==len(declared) and scope=={tuple(k) for k in declared},
          'SEQUENCE_COMPLETE_GROUP_SCOPE_REQUIRED')
    _need(expected <= scope, 'SEQUENCE_OWNER_OUTSIDE_SCOPE')
    for key in scope:
        _need(key in native, 'SEQUENCE_INVENTORY_SLOT_MISSING')
        r = native[key]; frame = frames[key[2:]]
        _need(all(r.get(field)==frame[field] for field in ('canvas','crop','anchor'))
              and str(r.get('resource','')).casefold()==frame['resource'].casefold(),
              'SEQUENCE_INVENTORY_DIFFERS_FROM_BINARY_DEF')
    bykey = {tuple(item['key']):item for item in manifest['items']}
    offsets = [frames[key[2:]]['offset'] for key in bykey]
    _need(len(offsets)==len(set(offsets)), 'SEQUENCE_ONE_OWNER_PER_NATIVE_OFFSET_REQUIRED')
    for key, item in bykey.items():
        rule = profile['directions'][item['profile_direction']]
        measurement = item.get('measurement',{})
        _need(len(rule.get('lengths_native',{}))>=2, 'SEQUENCE_TWO_ANATOMICAL_METRICS_REQUIRED')
        clearance=rule.get('clearance_native')
        _need(type(clearance) in (int,float) and (clearance == 0 or 1<=clearance<=16),
              'SEQUENCE_NATIVE_SAMPLING_CLEARANCE_REQUIRED')
        if clearance == 0:
            _need(rule.get('allow_native_crop_edge_contact') is True,
                  'SEQUENCE_CROP_EDGE_CONTACT_ACK_REQUIRED')
        _need(all(isinstance(rule.get(field),str) and rule[field]
                  and measurement.get(field)==rule[field] for field in ('pose_group','direction')),
              'SEQUENCE_POSE_DIRECTION_NOT_BOUND')
        _need(measurement.get('annotation_author') in ('assistant','human'),
              'SEQUENCE_ANNOTATION_AUTHOR_REQUIRED')
        common.finite_pair(item.get('root_offset_native'), 'SEQUENCE_EXPLICIT_ROOT_TRAJECTORY_REQUIRED')
        _need(item.get('source') is not None, 'SEQUENCE_SOURCE_REQUIRED')
        source_path = common.path(item['source']['path'])
        _need(not ({p.casefold() for p in source_path.parts} &
                   {'codex-prepared','codex-3x','output-3x','creature-master-set'}),
              'SEQUENCE_DERIVATIVE_IS_NOT_MASTER')
    aliases = contract.get('aliases',[])
    _need(isinstance(aliases,list), 'SEQUENCE_ALIAS_LIST_REQUIRED')
    covered = set(expected)
    for alias in aliases:
        key = tuple(alias['key']); owner = tuple(alias['owner_key'])
        _need(key in scope and key not in covered and owner in bykey, 'SEQUENCE_ALIAS_SCOPE_INVALID')
        _need(frames[key[2:]]['offset']==frames[owner[2:]]['offset'], 'SEQUENCE_ALIAS_OFFSET_MISMATCH')
        owner_source = bykey[owner]['source']; source = alias['source']
        common.png(source)
        _need(source['sha256']==owner_source['sha256'] and source['pixels']==owner_source['pixels'],
              'SEQUENCE_ALIAS_SOURCE_BYTES_DIFFER')
        covered.add(key)
    _need(covered==scope, 'SEQUENCE_LOGICAL_SLOT_OR_ALIAS_MISSING')
    return {'schema':CONTRACT, 'native_def':contract['native_def'], 'logical_slots':len(scope),
            'unique_offset_owners':len(bykey), 'root_semantics':contract['root_semantics'],
            'scale_from_alpha_bbox':False, 'independent_per_frame_contain':False,
            'pose_direction_targets_explicit':True, 'root_trajectory_explicit':True,
            'body_semantics_automatically_recognized':False, 'art_approved':False}


def handoff(manifest_ref, outdir, *, create=True):
    import subject_scale_layout as common
    manifest = common.doc(manifest_ref)
    root = common.path(outdir)
    receipt_path = root/'receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    _need(receipt['manifest']==manifest_ref, 'SEQUENCE_LAYOUT_RECEIPT_BINDING_CHANGED')
    _need(all(item['status']=='LOSSLESS_LAYOUT_READY' for item in receipt['items']),
          'SEQUENCE_FIT_CONFLICT_NO_EXPORT_REQUEST')
    items = []
    for item in receipt['items']:
        p = root/item['output_name']
        _need(common.sha(p.read_bytes())==item['output_png_sha256'], 'SEQUENCE_PREPARED_SHA_DRIFT')
        key = item['key']
        items.append({'id':f'{key[1]}_{key[2]}_{key[3]}','key':key,
                      'source_png':str(p),'source_sha256':item['output_png_sha256'],
                      'source_layout':'native-canvas','output_layout':'native-crop',
                      'registration':'strict-aspect','render_scales':[3]})
    review = {'schema':'h3.native-review.batch-manifest.v1',
              'official_inventory':manifest['native_inventory'],'items':items}
    aliases = {'schema':'h3.creature.sequence-registration.export-aliases.v1',
               'layout_manifest':manifest_ref,'layout_receipt':{'path':str(receipt_path),
                   'sha256':common.sha(receipt_path.read_bytes())},
               'logical_scope':manifest['sequence_contract']['logical_scope'],
               'aliases':[{k:a[k] for k in ('key','owner_key')} for a in manifest['sequence_contract']['aliases']],
               'source_resampled':False,'formal_3x':False,'art_approved':False}
    for name, value in [('native-review-manifest.json',review),('export-aliases.json',aliases)]:
        p = root/name
        if p.exists():
            _need(json.loads(p.read_bytes())==value,'SEQUENCE_HANDOFF_DRIFT')
        else:
            _need(create,'SEQUENCE_HANDOFF_MISSING')
            with p.open('x',encoding='utf-8') as f:
                json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    return {'native_review_manifest':str(root/'native-review-manifest.json'),
            'export_aliases':str(root/'export-aliases.json')}


def main(argv=None):
    import subject_scale_layout as common
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','run','verify'))
    for name in ('manifest','manifest-sha256','outdir','operation-id','renderer','renderer-sha256'):
        parser.add_argument('--'+name,required=name not in ('renderer','renderer-sha256'))
    args = parser.parse_args(argv)
    try:
        ref = {'path':str(common.path(args.manifest)),'sha256':args.manifest_sha256}
        _need(common.doc(ref).get('schema')==SCHEMA,'SEQUENCE_SCHEMA_REQUIRED')
        if args.renderer:
            common.pin({'path':args.renderer,'sha256':args.renderer_sha256})
        result = common.execute(args.action,ref,args.outdir,args.operation_id)
        if args.action!='check' and not result.get('needs_redraw'):
            result.update(handoff(ref,args.outdir,create=args.action=='run'))
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError,struct.error,UnicodeError) as error:
        print(json.dumps({'status':'BLOCKED','code':'SEQUENCE_REGISTRATION_CONTRACT',
                          'error':str(error)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
