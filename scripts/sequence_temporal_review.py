"""Read-only temporal screening of a replay-verified anatomy layout.

The inputs are registered native-canvas or native-crop candidates with full-canvas
root coordinates. This tool compares only explicitly named, comparable landmark
paths and declared root trajectories; it never infers body dimensions from alpha
boxes or approves/replaces artwork.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import creature_sequence_registration as sequence
import subject_scale_layout as layout

SCHEMA = 'h3.creature.sequence-temporal-review.request.v1'
REPORT = 'h3.creature.sequence-temporal-review.report.v1'
RECEIPT = 'h3.creature.sequence-temporal-review.receipt.v1'


def _ref(p):
    p = layout.path(p)
    return {'path': str(p), 'sha256': layout.sha(p.read_bytes())}


def _number(value, code, low=0, high=1):
    layout.need(type(value) in (int, float) and math.isfinite(value) and low < value <= high, code)
    return float(value)


def _key(value):
    layout.need(isinstance(value, list) and len(value) == 4 and value[0] == 'def'
                and isinstance(value[1], str) and type(value[2]) is int
                and type(value[3]) is int, 'TEMPORAL_SLOT_INVALID')
    return tuple(value)


def compare(receipt, loop_groups, spread_threshold, adjacent_threshold, root_threshold):
    """Compare declared paths; the receipt's layout must already be replay verified."""
    contract = receipt['sequence_contract']
    groups = contract['groups']
    v2 = contract.get('schema') == sequence.CONTRACT_V2
    layout.need(set(loop_groups) <= set(groups), 'TEMPORAL_LOOP_GROUP_NOT_SELECTED')
    owners = {_key(item['key']): item for item in receipt['items']}
    aliases = {_key(item['key']): _key(item['owner_key']) for item in contract['aliases']}
    alias_items = {_key(item['key']): item for item in contract['aliases']}
    logical = [_key(value) for value in contract['logical_scope']]
    layout.need(len(logical) == len(set(logical)), 'TEMPORAL_DUPLICATE_LOGICAL_SLOT')
    layout.need(set(logical) == set(owners) | set(aliases), 'TEMPORAL_SCOPE_INCOMPLETE')
    layout.need(not (set(owners) & set(aliases)), 'TEMPORAL_ALIAS_DUPLICATED')
    expanded = {}
    external_aliases = []
    for key in logical:
        owner = aliases.get(key, key)
        if owner in owners:
            expanded[key] = (owner, owners[owner])
        else:
            alias = alias_items.get(key, {})
            layout.need(v2 and owner not in logical and alias.get('owner_source') is not None,
                        'TEMPORAL_ALIAS_OWNER_MISSING')
            external_aliases.append({'key':list(key),'owner_key':list(owner),
                                     'owner_source_sha256':alias['owner_source'].get('sha256'),
                                     'status':'EXTERNAL_OWNER_NOT_IN_LAYOUT'})
            expanded[key] = (owner, None)

    comparisons, transitions = [], []
    warnings = 0
    incomplete = 0
    for group in groups:
        group_logical = sorted((k for k in logical if k[2] == group), key=lambda k: k[3])
        keys = [k for k in group_logical if expanded[k][1] is not None]
        incomplete += sum(expanded[k][1] is None for k in group_logical)
        partitions = {}
        for key in keys:
            row = expanded[key][1]
            partition = (row.get('profile_direction'), row.get('pose_group'), row.get('direction'))
            partitions.setdefault(partition, []).append(key)
        for partition, subset in partitions.items():
            names = sorted({name for k in subset for name in
                            expanded[k][1].get('body_deviation_fraction', {})})
            if not names:
                incomplete += 1
            for name in names:
                values = []
                for key in subset:
                    row = expanded[key][1]
                    dev = row.get('body_deviation_fraction', {}).get(name)
                    if row.get('status') == 'LOSSLESS_LAYOUT_READY' and type(dev) in (int, float) \
                            and math.isfinite(dev) and 1 + dev > 0:
                        values.append((key, 1 + float(dev)))
                status = 'NOT_MEASURED'
                span = None
                if len(values) >= 2:
                    span = max(v for _, v in values) / min(v for _, v in values) - 1
                    status = 'REVIEW_REQUIRED' if span > spread_threshold else 'NO_NUMERIC_WARNING'
                    warnings += status == 'REVIEW_REQUIRED'
                if len(values) != len(subset) or len(values) < 2:
                    incomplete += 1
                comparisons.append({'group': group, 'profile_direction': partition[0],
                                    'pose_group': partition[1], 'direction': partition[2],
                                    'metric': name, 'count': len(values), 'expected': len(subset),
                                    'values': [{'key': list(k), 'relative_to_reference': round(v, 8)}
                                               for k, v in values],
                                    'spread_fraction': None if span is None else round(span, 8),
                                    'threshold_fraction': spread_threshold, 'status': status})

        pairs = list(zip(keys, keys[1:]))
        if group in loop_groups and len(keys) > 1:
            pairs.append((keys[-1], keys[0]))
        for left, right in pairs:
            a, b = expanded[left][1], expanded[right][1]
            same = all(a.get(field) == b.get(field) and a.get(field) is not None
                       for field in ('profile_direction', 'pose_group', 'direction'))
            lengths = []
            for name in sorted(set(a.get('body_deviation_fraction', {})) |
                               set(b.get('body_deviation_fraction', {}))):
                x = a.get('body_deviation_fraction', {}).get(name)
                y = b.get('body_deviation_fraction', {}).get(name)
                valid = (same and a.get('status') == b.get('status') == 'LOSSLESS_LAYOUT_READY'
                         and type(x) in (int, float) and type(y) in (int, float)
                         and math.isfinite(x) and math.isfinite(y) and 1 + x > 0 and 1 + y > 0)
                delta = max((1+x)/(1+y), (1+y)/(1+x))-1 if valid else None
                status = ('NOT_COMPARABLE' if not valid else 'REVIEW_REQUIRED'
                          if delta > adjacent_threshold else 'NO_NUMERIC_WARNING')
                warnings += status == 'REVIEW_REQUIRED'
                if status == 'NOT_COMPARABLE':
                    incomplete += 1
                lengths.append({'metric': name, 'change_fraction': None if delta is None else round(delta, 8),
                                'status': status})
            av, bv = a.get('root_target_native_canvas'), b.get('root_target_native_canvas')
            valid_root = (same and isinstance(av, list) and isinstance(bv, list)
                          and len(av) == len(bv) == 2 and all(type(x) in (int, float)
                          and math.isfinite(x) for x in av + bv))
            if valid_root:
                dx, dy = bv[0]-av[0], bv[1]-av[1]
                distance = math.hypot(dx, dy)
                root_status = ('NOT_EVALUATED' if root_threshold is None else
                               'REVIEW_REQUIRED' if distance > root_threshold else 'NO_NUMERIC_WARNING')
                warnings += root_status == 'REVIEW_REQUIRED'
                root = {'dx_native': round(dx, 8), 'dy_native': round(dy, 8),
                        'distance_native': round(distance, 8), 'status': root_status}
            else:
                incomplete += 1
                root = {'dx_native': None, 'dy_native': None, 'distance_native': None,
                        'status': 'NOT_COMPARABLE'}
            transitions.append({'from': list(left), 'to': list(right),
                                'explicit_loop_closure': group in loop_groups and left == keys[-1] and right == keys[0],
                                'metric_changes': lengths, 'declared_root_step': root})
    return {'status': 'REVIEW_REQUIRED' if warnings else 'INCOMPLETE_EVIDENCE' if incomplete
            else 'NO_NUMERIC_WARNING', 'warning_count': warnings, 'incomplete_comparisons': incomplete,
            'comparisons': comparisons, 'transitions': transitions,
            'external_alias_dependencies': external_aliases,
            'alpha_bbox_used_as_body_measurement': False, 'automatic_art_approval': False,
            'automatic_redraw': False, 'runtime_verified': False}


def _replay(req):
    layout.need(req.get('schema') == SCHEMA, 'TEMPORAL_SCHEMA_REQUIRED')
    impl = req['implementation']
    layout.pin(impl)
    layout.need(layout.sha(Path(__file__).read_bytes()) == impl['sha256'], 'TEMPORAL_READER_DIFFERS')
    receipt_ref = req['layout_receipt']
    receipt_path, receipt_raw = layout.pin(receipt_ref)
    receipt = json.loads(receipt_raw)
    layout.need(receipt.get('schema') == layout.RECEIPT and 'sequence_contract' in receipt,
                'TEMPORAL_SEQUENCE_RECEIPT_REQUIRED')
    manifest_ref = receipt['manifest']
    manifest = layout.doc(manifest_ref)
    layout.need(manifest.get('schema') in (sequence.SCHEMA, sequence.SCHEMA_V2),
                'TEMPORAL_SEQUENCE_MANIFEST_REQUIRED')
    layout.need(receipt_path.name == 'receipt.json', 'TEMPORAL_LAYOUT_RECEIPT_NAME_INVALID')
    operation = receipt['operation_id']
    result = layout.execute('verify', manifest_ref, receipt_path.parent, operation)
    layout.need(result['status'] == 'VERIFIED_LOSSLESS_LAYOUT' and not result['needs_redraw'],
                'TEMPORAL_LAYOUT_NOT_VERIFIED')
    sequence.handoff(manifest_ref, receipt_path.parent, create=False)
    loop_groups = req['loop_groups']
    layout.need(isinstance(loop_groups, list) and all(type(g) is int for g in loop_groups)
                and len(loop_groups) == len(set(loop_groups)), 'TEMPORAL_LOOP_GROUPS_INVALID')
    spread = _number(req['spread_warn_fraction'], 'TEMPORAL_SPREAD_THRESHOLD_INVALID')
    adjacent = _number(req['adjacent_warn_fraction'], 'TEMPORAL_ADJACENT_THRESHOLD_INVALID')
    root = req['root_step_warn_native']
    if root is not None:
        root = _number(root, 'TEMPORAL_ROOT_THRESHOLD_INVALID', high=1000)
    data = compare(receipt, loop_groups, spread, adjacent, root)
    return {'schema': REPORT, 'layout_receipt': receipt_ref, 'layout_manifest': manifest_ref,
            'layout_verified': True, 'loop_groups': loop_groups,
            'spread_warn_fraction': spread, 'adjacent_warn_fraction': adjacent,
            'root_step_warn_native': root, 'analysis': data,
            'limits': 'Review flags only; pose/projection and landmark uncertainty need visual inspection. '
                      'No identity, anatomy, motion, formal 3X or game approval.'}


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _markdown(report):
    data = report['analysis']
    lines = ['# 动画尺度与轨迹筛查', '', f"状态：`{data['status']}`；提醒 {data['warning_count']} 项。",
             '本报告只比较已配准的同姿态、同朝向、同名描点及显式根部轨迹；'
             '误差、遮挡和动作变化需结合画面判断。不会自动改图或判定美术通过。', '',
             '| 组 | 描点 | 覆盖 | 全组最大相对跨度 | 结果 |', '|---|---|---:|---:|---|']
    for row in data['comparisons']:
        span = '未测' if row['spread_fraction'] is None else f"{row['spread_fraction']*100:.2f}%"
        lines.append(f"| {row['group']} | {row['metric']} | {row['count']}/{row['expected']} | {span} | {row['status']} |")
    lines += ['', '| 相邻槽 | 根部变化（原生像素） | 根部提示 | 描点提示 |', '|---|---:|---|---|']
    for row in data['transitions']:
        step = row['declared_root_step']
        distance = '不可比较' if step['distance_native'] is None else f"{step['distance_native']:.3f}"
        marks = ', '.join(x['metric'] for x in row['metric_changes'] if x['status'] == 'REVIEW_REQUIRED') or '—'
        lines.append(f"| {row['from'][2]}_{row['from'][3]} → {row['to'][2]}_{row['to'][3]} | {distance} | {step['status']} | {marks} |")
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _repin(req, manifest_ref):
    layout.pin(manifest_ref)
    layout.pin(req['layout_receipt'])
    layout.pin(req['implementation'])
    receipt = layout.doc(req['layout_receipt'])
    manifest = layout.doc(receipt['manifest'])
    for ref in [manifest['profile'], manifest['native_inventory'], manifest['sequence_contract']['native_def']]:
        layout.pin(ref)
    for name in ('sequence_reader', 'layout_reader'):
        layout.pin(manifest['implementation'][name])
    for item in manifest['items']:
        layout.pin(item['source'])
    for item in manifest['sequence_contract']['aliases']:
        layout.pin(item['source'])


def execute(action, manifest_ref, outdir, operation):
    layout.need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,95}', operation or ''), 'TEMPORAL_OPERATION_INVALID')
    root = layout.path(outdir)
    layout.need(root.parent == (layout.PRODUCT/'build/experiments/sequence-temporal').resolve()
                and root.name == operation, 'TEMPORAL_OUTPUT_SCOPE_INVALID')
    req = layout.doc(manifest_ref)
    layout.need(req['operation_id'] == operation, 'TEMPORAL_OPERATION_MISMATCH')
    report = _replay(req)
    report['request'] = manifest_ref
    report_raw, md_raw = _encode(report), _markdown(report)
    receipt = {'schema': RECEIPT, 'request': manifest_ref, 'operation_id': operation,
               'report_sha256': layout.sha(report_raw), 'markdown_sha256': layout.sha(md_raw),
               'automatic_art_approval': False, 'formal_3x': False, 'game_deployed': False}
    if action == 'check':
        _repin(req, manifest_ref)
        return {'status': 'CHECKED_WITHOUT_WRITING', 'analysis': report['analysis']}
    if action == 'run' and root.exists():
        layout.need((root/'receipt.json').is_file(), 'TEMPORAL_FOREIGN_OR_PARTIAL_OPERATION')
        action = 'verify'
    if action == 'verify':
        layout.need(json.loads((root/'receipt.json').read_bytes()) == receipt, 'TEMPORAL_RECEIPT_MISMATCH')
        layout.need((root/'report.json').read_bytes() == report_raw and (root/'report.md').read_bytes() == md_raw,
                    'TEMPORAL_REPORT_DRIFT')
        _repin(req, manifest_ref)
        return {'status': 'VERIFIED_REVIEW_REPORT', 'analysis_status': report['analysis']['status'],
                'warnings': report['analysis']['warning_count'], 'receipt': str(root/'receipt.json')}
    layout.need(action == 'run' and not root.exists(), 'TEMPORAL_OUTPUT_ALREADY_EXISTS')
    _repin(req, manifest_ref)
    root.mkdir(parents=True)
    for name, data in [('report.json', report_raw), ('report.md', md_raw), ('receipt.json', _encode(receipt))]:
        with (root/name).open('xb') as f:
            f.write(data)
    _repin(req, manifest_ref)
    return {'status': 'REVIEW_REPORT_CREATED', 'analysis_status': report['analysis']['status'],
            'warnings': report['analysis']['warning_count'], 'receipt': str(root/'receipt.json')}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('freeze', 'check', 'run', 'verify'))
    for name in ('manifest', 'manifest-sha256', 'outdir', 'operation-id',
                 'layout-receipt', 'layout-receipt-sha256', 'spread-warn-fraction',
                 'adjacent-warn-fraction', 'root-step-warn-native'):
        p.add_argument('--'+name)
    p.add_argument('--loop-group', action='append', type=int, default=[])
    a = p.parse_args(argv)
    try:
        if a.action == 'freeze':
            layout.need(a.operation_id and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,95}', a.operation_id),
                        'TEMPORAL_OPERATION_INVALID')
            layout.need(a.layout_receipt and a.layout_receipt_sha256, 'TEMPORAL_RECEIPT_PIN_REQUIRED')
            req = {'schema': SCHEMA, 'operation_id': a.operation_id,
                   'layout_receipt': {'path': str(layout.path(a.layout_receipt)),
                                      'sha256': a.layout_receipt_sha256.lower()},
                   'implementation': _ref(Path(__file__)), 'loop_groups': a.loop_group,
                   'spread_warn_fraction': float(a.spread_warn_fraction or .10),
                   'adjacent_warn_fraction': float(a.adjacent_warn_fraction or .10),
                   'root_step_warn_native': float(a.root_step_warn_native) if a.root_step_warn_native else None}
            _replay(req)
            dst = layout.PRODUCT/'build/experiments/sequence-temporal-requests'/(a.operation_id+'.json')
            layout.need(not dst.exists(), 'TEMPORAL_REQUEST_ALREADY_EXISTS')
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = _encode(req)
            with dst.open('xb') as f:
                f.write(data)
            layout.pin(req['layout_receipt'])
            result = {'status': 'REQUEST_FROZEN', 'manifest': str(dst), 'manifest_sha256': layout.sha(data),
                      'outdir': str(layout.PRODUCT/'build/experiments/sequence-temporal'/a.operation_id)}
        else:
            layout.need(all((a.manifest, a.manifest_sha256, a.outdir, a.operation_id)), 'TEMPORAL_REQUEST_REQUIRED')
            result = execute(a.action, {'path': str(layout.path(a.manifest)), 'sha256': a.manifest_sha256.lower()},
                             a.outdir, a.operation_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as e:
        print(json.dumps({'status': 'BLOCKED', 'code': 'SEQUENCE_TEMPORAL_REVIEW', 'error': str(e)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
