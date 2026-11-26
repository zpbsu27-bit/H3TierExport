"""Anatomy/root registration of an explicit native creature action sequence.

The existing lossless layout solver owns pixel copying. This module admits an
animation only with its pinned binary DEF, complete selected groups, explicit
pose/direction targets, source-bound anatomy, root trajectory and native aliases.
It emits a request for the existing single-sample native-review renderer. No
model inference, anatomical recognition, resampling, art approval or deployment.
"""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import struct
import sys
from PIL import Image

SCHEMA = 'h3.creature.sequence-registration.manifest.v1'
CONTRACT = 'h3.creature.sequence-registration.contract.v1'
SCHEMA_V2 = 'h3.creature.sequence-registration.manifest.v2'
CONTRACT_V2 = 'h3.creature.sequence-registration.contract.v2'
APPENDAGE_CONTRACT = 'h3.creature.appendage-contract.v1'
PADDING_POLICY = {
    'schema': 'h3.creature.sequence-padding-policy.v1',
    'mode': 'integer-transparent-padding',
    'offset_space': 'full_output_canvas_pixels',
    'alpha_policy': 'preserve-all-positive-alpha; alpha-ge2-core-inside-crop',
    'root_tolerance_native': 1,
}
CREATURE_MASTER_CANDIDATE_ROOT = Path(__file__).resolve().parents[1] / 'build' / 'experiments' / 'creature-master-set'


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
    schema = manifest.get('schema')
    _need(schema in (SCHEMA, SCHEMA_V2), 'SEQUENCE_SCHEMA_REQUIRED')
    v2 = schema == SCHEMA_V2
    implementation=manifest.get('implementation',{})
    _need(implementation.get('schema')=='h3.creature.sequence-registration.readers.v1',
          'SEQUENCE_FROZEN_READER_BINDING_REQUIRED')
    for field,executing in [('sequence_reader',Path(__file__)),('layout_reader',Path(common.__file__))]:
        bound=implementation[field]
        common.pin(bound)
        _need(common.sha(executing.read_bytes())==bound['sha256'],'SEQUENCE_FROZEN_READER_DIFFERS')
    output_layout = manifest.get('output_layout')
    if v2:
        _need(output_layout in ('native-canvas', 'native-crop'),
              'SEQUENCE_NATIVE_LAYOUT_REQUIRED')
    else:
        _need(output_layout == 'native-canvas', 'SEQUENCE_FULL_CANVAS_REGISTRATION_REQUIRED')
    padding_mode = 'padding_policy' in manifest
    if padding_mode:
        _need(manifest.get('padding_policy') == PADDING_POLICY
              and output_layout == 'native-canvas',
              'SEQUENCE_PADDING_POLICY_INVALID')
    contract = manifest.get('sequence_contract', {})
    _need(contract.get('schema') == (CONTRACT_V2 if v2 else CONTRACT),
          'SEQUENCE_CONTRACT_REQUIRED')
    _need(contract.get('scale_policy') == 'anatomical-targets'
          and contract.get('camera_policy') == 'fixed-native-units'
          and contract.get('trajectory_units') == 'native_canvas_1x', 'SEQUENCE_COORDINATE_POLICY_REQUIRED')
    _need(isinstance(contract.get('root_semantics'),str) and contract['root_semantics'].strip(),
          'SEQUENCE_ROOT_SEMANTICS_REQUIRED')
    if v2 and output_layout == 'native-crop':
        _need(contract.get('native_crop_origin_policy') ==
              'official_binary_def_origin_in_native_canvas_coordinates',
              'SEQUENCE_CROP_ORIGIN_POLICY_REQUIRED')
    if v2:
        appendage = contract.get('appendage_contract', {})
        _need(appendage.get('schema') == APPENDAGE_CONTRACT
              and appendage.get('identity') in {key[1] for key in expected}
              and isinstance(appendage.get('scope'), str) and appendage['scope'].strip(),
              'SEQUENCE_APPENDAGE_CONTRACT_REQUIRED')
        counts = appendage.get('expected_counts')
        connections = appendage.get('required_connections')
        skeleton_requirements = appendage.get('required_skeleton_paths')
        _need(isinstance(counts, dict) and counts
              and all(type(value) is int and value >= 0 for value in counts.values())
              and isinstance(connections, list) and connections
              and all(isinstance(value, str) and value for value in connections)
              and len(set(connections)) == len(connections)
              and isinstance(skeleton_requirements, dict) and skeleton_requirements
              and all(isinstance(name, str) and name
                      and type(points) is int and points >= 2
                      for name, points in skeleton_requirements.items()),
              'SEQUENCE_APPENDAGE_CONTRACT_INVALID')
        root_policy = contract.get('root_motion_policy', {})
        _need(isinstance(root_policy, dict) and set(root_policy) ==
              {str(group) for group in contract.get('groups', [])}
              and all(value in ('fixed', 'explicit') for value in root_policy.values()),
              'SEQUENCE_ROOT_MOTION_POLICY_REQUIRED')
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
    common_ks = set()
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
        if padding_mode:
            registration = item.get('padding_registration')
            _need(isinstance(registration, dict)
                  and set(registration) == {'common_k', 'offset'}
                  and type(registration.get('common_k')) is int
                  and 4 <= registration['common_k'] <= 96,
                  'SEQUENCE_PADDING_REGISTRATION_REQUIRED')
            offset = registration.get('offset')
            _need(isinstance(offset, list) and len(offset) == 2
                  and all(type(value) is int for value in offset),
                  'SEQUENCE_PADDING_OFFSET_INVALID')
            common_ks.add(registration['common_k'])
        else:
            _need('padding_registration' not in item,
                  'SEQUENCE_PADDING_REGISTRATION_WITHOUT_POLICY')
        _need(item.get('source') is not None, 'SEQUENCE_SOURCE_REQUIRED')
        if v2:
            if contract['root_motion_policy'][str(key[2])] == 'fixed':
                _need(item['root_offset_native'] == [0, 0],
                      'SEQUENCE_FIXED_ROOT_MUST_NOT_MOVE')
            trajectory = item.get('root_trajectory_review', {})
            _need(trajectory.get('key') == list(key)
                  and trajectory.get('coordinate_space') == 'native_canvas_1x'
                  and trajectory.get('method') == 'manual-official-1x-root-landmark'
                  and trajectory.get('annotation_author') in ('assistant', 'human')
                  and trajectory.get('status') == 'VISUALLY_REVIEWED',
                  'SEQUENCE_ROOT_TRAJECTORY_REVIEW_REQUIRED')
            guide_ref = trajectory.get('source')
            guide_path, guide_bytes = common.pin(guide_ref)
            _need(guide_path.suffix.casefold() == '.png'
                  and guide_path.stem == f'{key[2]}_{key[3]}'
                  and guide_path.parent.name.casefold() == identity.casefold(),
                  'SEQUENCE_ROOT_REFERENCE_MUST_BE_OFFICIAL_NATIVE_FRAME')
            with Image.open(io.BytesIO(guide_bytes)) as guide:
                guide.load()
                _need(guide.format == 'PNG' and list(guide.size) == frames[key[2:]]['canvas']
                      and guide_ref.get('pixels') == list(guide.size),
                      'SEQUENCE_ROOT_REFERENCE_GEOMETRY_MISMATCH')
            base_anchor = profile['directions'][item['profile_direction']]['anchor_native_canvas']
            offset = item['root_offset_native']
            declared_target = common.finite_pair(trajectory.get('root_target_native_canvas'),
                                                 'SEQUENCE_ROOT_TARGET_REVIEW_REQUIRED')
            expected_target = [base_anchor[i] + offset[i] for i in (0, 1)]
            _need(all(abs(declared_target[i] - expected_target[i]) <= 1e-6 for i in (0, 1)),
                  'SEQUENCE_ROOT_TRAJECTORY_TARGET_DIFFERS_FROM_LAYOUT')
            review = item.get('limb_review', {})
            _need(review.get('source_sha256') == item.get('source', {}).get('sha256')
                  and review.get('reviewer') in ('assistant', 'human')
                  and review.get('status') == 'VISUALLY_REVIEWED',
                  'SEQUENCE_APPENDAGE_VISUAL_REVIEW_REQUIRED')
            _need(review.get('counts') == counts,
                  'SEQUENCE_APPENDAGE_COUNT_MISMATCH')
            joint_reviews = review.get('connections', {})
            _need(set(joint_reviews) >= set(connections)
                  and all(joint_reviews.get(name) == 'CONNECTED' for name in connections),
                  'SEQUENCE_APPENDAGE_CONNECTION_UNRESOLVED')
            source_image = common.png(item['source'])
            skeleton_paths = review.get('skeleton_paths', {})
            _need(set(skeleton_paths) == set(skeleton_requirements),
                  'SEQUENCE_SKELETON_PATH_SET_MISMATCH')
            for name, minimum_points in skeleton_requirements.items():
                points = skeleton_paths[name]
                _need(isinstance(points, list) and len(points) >= minimum_points,
                      'SEQUENCE_SKELETON_PATH_INCOMPLETE')
                for point in points:
                    coordinates = common.finite_pair(point, 'SEQUENCE_SKELETON_POINT_INVALID')
                    _need(all(0 <= coordinates[index] < source_image.size[index]
                              for index in (0, 1)),
                          'SEQUENCE_SKELETON_POINT_OUTSIDE_SOURCE')
        source_path = common.path(item['source']['path'])
        source_parts = {p.casefold() for p in source_path.parts}
        _need(not (source_parts & {'codex-prepared','codex-3x','output-3x'}),
              'SEQUENCE_DERIVATIVE_IS_NOT_MASTER')
        if 'experiments' in source_parts:
            resolved_source = source_path.resolve(strict=True)
            candidate_root = CREATURE_MASTER_CANDIDATE_ROOT.resolve()
            flat_operation_file = resolved_source.parent.parent.resolve() == candidate_root
            identity_subdir_file = (resolved_source.parent.name.casefold() == identity.casefold()
                                    and resolved_source.parent.parent.parent.resolve() == candidate_root)
            _need(resolved_source.suffix.casefold() == '.png'
                  and resolved_source.stem == f'{key[2]}_{key[3]}'
                  and (flat_operation_file or identity_subdir_file),
                  'SEQUENCE_CANDIDATE_OUTSIDE_MASTER_SET_OPERATION')
    if padding_mode:
        _need(len(common_ks) == 1, 'SEQUENCE_PADDING_COMMON_K_MISMATCH')
    aliases = contract.get('aliases',[])
    _need(isinstance(aliases,list), 'SEQUENCE_ALIAS_LIST_REQUIRED')
    covered = set(expected)
    for alias in aliases:
        key = tuple(alias['key']); owner = tuple(alias['owner_key'])
        _need(key in scope and key not in covered, 'SEQUENCE_ALIAS_SCOPE_INVALID')
        if owner in bykey:
            owner_source = bykey[owner]['source']
        else:
            _need(v2 and owner not in scope and owner in native,
                  'SEQUENCE_EXTERNAL_ALIAS_OWNER_UNBOUND')
            external = native[owner]
            external_frame = frames.get(owner[2:])
            _need(external_frame is not None
                  and all(external.get(field) == external_frame[field]
                          for field in ('canvas', 'crop', 'anchor'))
                  and str(external.get('resource', '')).casefold() ==
                      external_frame['resource'].casefold(),
                  'SEQUENCE_EXTERNAL_ALIAS_NATIVE_GEOMETRY_MISMATCH')
            owner_source = alias.get('owner_source')
            _need(owner_source is not None, 'SEQUENCE_EXTERNAL_ALIAS_OWNER_UNBOUND')
            common.png(owner_source)
        _need(frames[key[2:]]['offset']==frames[owner[2:]]['offset'], 'SEQUENCE_ALIAS_OFFSET_MISMATCH')
        source = alias['source']
        common.png(source)
        _need(source['sha256']==owner_source['sha256'] and source['pixels']==owner_source['pixels'],
              'SEQUENCE_ALIAS_SOURCE_BYTES_DIFFER')
        covered.add(key)
    _need(covered==scope, 'SEQUENCE_LOGICAL_SLOT_OR_ALIAS_MISSING')
    return {'schema':contract['schema'], 'native_def':contract['native_def'], 'logical_slots':len(scope),
            'unique_offset_owners':len(bykey), 'root_semantics':contract['root_semantics'],
            'scale_from_alpha_bbox':False, 'independent_per_frame_contain':False,
            'pose_direction_targets_explicit':True, 'root_trajectory_explicit':True,
            'limb_count_visual_reviews_verified':len(bykey) if v2 else None,
            'crop_origin_policy':contract.get('native_crop_origin_policy') if v2 else None,
            'integer_transparent_padding':padding_mode,
            'common_k':next(iter(common_ks)) if padding_mode else None,
            'padding_root_tolerance_native':PADDING_POLICY['root_tolerance_native'] if padding_mode else None,
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
    padded = manifest.get('padding_policy') == PADDING_POLICY
    for item in receipt['items']:
        p = root/item['output_name']
        _need(common.sha(p.read_bytes())==item['output_png_sha256'], 'SEQUENCE_PREPARED_SHA_DRIFT')
        key = item['key']
        items.append({'id':f'{key[1]}_{key[2]}_{key[3]}','key':key,
                      'source_png':str(p),'source_sha256':item['output_png_sha256'],
                      'source_layout':manifest.get('output_layout', 'native-crop'),
                      'output_layout':'native-canvas' if padded else 'native-crop',
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
        _need(common.doc(ref).get('schema') in (SCHEMA, SCHEMA_V2), 'SEQUENCE_SCHEMA_REQUIRED')
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
