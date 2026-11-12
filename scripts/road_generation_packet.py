"""Prepare per-slot road web-batch tasks from an explicitly pinned profile.

This produces JSON only: no upload, generation, source binding or art approval.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import art_batch_quality as quality
import art_production_profile as production
import subject_scale_layout as common


def make_packet(profile_ref, output_root):
    profile = common.doc(profile_ref)
    common.need(profile.get('schema') == common.PROFILE, 'ROAD_PACKET_PROFILE_SCHEMA_REQUIRED')
    common.need(profile.get('replaces_canonical_art_standard') is False,
                'ROAD_PACKET_PROFILE_NOT_A_STANDARD')
    standard_ref=profile.get('art_standard')
    standard=common.doc(standard_ref)
    common.need(standard.get('version') == standard_ref.get('version'), 'ART_STANDARD_VERSION_DRIFT')
    native=quality.slots(profile.get('native_inventory'))
    common.need(profile.get('preset') in ('road-connection','roads-rivers'), 'ROAD_PACKET_PRESET_REQUIRED')
    common.need(profile.get('road_authoring_brief_revision') == 'road-seam-lessons-20261102-v1',
                'ROAD_PACKET_CURRENT_BRIEF_REQUIRED')
    lines = production.road_seam_brief(profile)  # Checks PNGs, roles, slots and SHA.
    common.need(lines, 'ROAD_PACKET_ROAD_SCOPE_REQUIRED')
    scope = profile.get('expected_slots', [])
    common.need(scope and all(isinstance(k,list) and len(k)==4 and k[0]=='def'
        and k[1] in production.ROAD_FAMILIES and type(k[2]) is int and type(k[3]) is int for k in scope),
        'ROAD_PACKET_ONLY_ROAD_DEF_SLOTS')
    common.need(len({tuple(k) for k in scope}) == len(scope), 'ROAD_PACKET_DUPLICATE_SLOT')
    common.need(all(tuple(k) in native for k in scope), 'ROAD_PACKET_NATIVE_SLOT_NOT_FOUND')
    root = Path(output_root)
    common.need(root.is_absolute(), 'ROAD_PACKET_ABSOLUTE_OUTPUT_ROOT_REQUIRED')
    root = root.resolve()
    guides = {(tuple(g['key']),g['role']):g['source'] for g in profile.get('road_drawing_guides',[])}
    tasks = []
    for key in scope:
        geometry=guides.get((tuple(key),'geometry'));style=guides.get((tuple(key),'style'))
        common.need(geometry is not None and style is not None, 'ROAD_PACKET_SLOT_GUIDES_REQUIRED')
        target = root / key[1] / f'{key[2]}_{key[3]}.png'
        common.need(not target.exists(), 'ROAD_PACKET_OUTPUT_EXISTS')
        per_slot = {**profile, 'expected_slots':[key], 'road_drawing_guides':[
            g for g in profile.get('road_drawing_guides',[]) if g['key']==key]}
        slot_lines=production.road_seam_brief(per_slot)
        base_lines=slot_lines[:slot_lines.index('## 已冻结的逐槽生图参考（辅助，不是像素锁定）')]
        prompt = ('\n'.join(base_lines).strip() + '\n\n本次仅绘制一个槽位：'
            f'{key[1]}/{key[2]}_{key[3]}.png。参考图1是本槽描点/轮廓，参考图2是材质风格；'
            '参考图不是像素级保证，需在生成后检查与其他槽位的拼接。')
        tasks.append({'id':f'{key[1]}_{key[2]}_{key[3]}','prompt':prompt,'output':str(target),
            'reference_images':[geometry['path'],style['path']],
            'expected_reference_sha256':[geometry['sha256'],style['sha256']],
            'background_mode':'transparent'})
    return {'schema':'h3.road-web-batch-tasks.v1','profile':profile_ref,'tasks':tasks,
        'status':'PREPARED_NOT_SUBMITTED','automatic_upload':False,'art_approved':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('profile','profile-sha256','output-root','out'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--renderer');parser.add_argument('--renderer-sha256')
    a=parser.parse_args(argv)
    try:
        common.need(bool(a.renderer)==bool(a.renderer_sha256),'ROAD_PACKET_RENDERER_PIN_REQUIRED')
        if a.renderer:common.pin({'path':a.renderer,'sha256':a.renderer_sha256})
        profile={'path':str(common.path(a.profile)),'sha256':a.profile_sha256}
        packet=make_packet(profile,a.output_root)
        out=common.path(a.out)
        common.need(out.parent.is_dir(),'ROAD_PACKET_PARENT_MISSING')
        with out.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(packet,stream,ensure_ascii=False,indent=2);stream.write('\n')
        print(json.dumps({'status':packet['status'],'tasks':len(packet['tasks']),'out':str(out)},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
