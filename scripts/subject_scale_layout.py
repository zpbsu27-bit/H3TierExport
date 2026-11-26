"""Lossless static-slot scale layout from explicit anatomical measurements.

No resize, alpha cleanup, learned repair or new painted pixels. A pinned profile
controls display scale; every positive-alpha source pixel is copied unchanged.
The qualified exporter remains the only sampler and formal 3X writer.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import gzip
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import sys
from fractions import Fraction
from PIL import Image

PRODUCT = resolve_tool_root(__file__)
SCHEMA = 'h3.subject-scale-layout.manifest.v1'
PROFILE = 'h3.art.production-profile.v1'
RECEIPT = 'h3.subject-scale-layout.receipt.v1'


def need(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def path(value):
    p = Path(value)
    need(p.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    for q in (p, *p.parents):
        if q.exists():
            need(not q.is_symlink() and not getattr(q.lstat(), 'st_file_attributes', 0) & 0x400,
                 'REPARSE_PATH_BLOCKED')
    return p.resolve()


def pin(ref):
    need(isinstance(ref, dict) and re.fullmatch('[0-9a-f]{64}', ref.get('sha256', '')), 'PIN_REQUIRED')
    p = path(ref['path'])
    need(p.is_file() and p.stat().st_size <= 256 * 1024 * 1024, 'INPUT_MISSING_OR_TOO_LARGE')
    raw = p.read_bytes()
    need(sha(raw) == ref['sha256'], 'INPUT_SHA_DRIFT')
    return p, raw


def doc(ref):
    return json.loads(pin(ref)[1])


def png(ref):
    p, raw = pin(ref)
    need('codex-3x' not in str(p).lower() and 'candidate-3x' not in p.name.lower(),
         'DELIVERY_OR_PREVIEW_IS_NOT_AN_HQ_PARENT')
    with Image.open(io.BytesIO(raw)) as im:
        need(im.format == 'PNG' and im.mode == 'RGBA' and getattr(im, 'n_frames', 1) == 1,
             'SINGLE_RGBA_PNG_REQUIRED')
        need(im.width * im.height <= 64 * 1024 * 1024, 'SOURCE_PIXEL_BUDGET')
        im.load()
        need(list(im.size) == ref['pixels'], 'SOURCE_PIXEL_PIN_DRIFT')
        return im.copy()


def finite_pair(value, code):
    need(isinstance(value, list) and len(value) == 2 and all(type(x) in (int, float)
         and math.isfinite(x) for x in value), code)
    return value


def measure(points, pixels):
    need(isinstance(points, list) and len(points) >= 2, 'ANATOMICAL_PATH_REQUIRED')
    for point in points:
        finite_pair(point, 'INVALID_LANDMARK')
        need(all(0 <= point[i] < pixels[i] for i in (0, 1)), 'LANDMARK_OUTSIDE_SOURCE')
    value = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    need(value > 0, 'EMPTY_ANATOMICAL_PATH')
    return value


def resolve(manifest_ref, operation):
    manifest = doc(manifest_ref)
    sequence_mode=manifest.get('schema') in (
        'h3.creature.sequence-registration.manifest.v1',
        'h3.creature.sequence-registration.manifest.v2')
    need((manifest.get('schema') == SCHEMA or sequence_mode) and manifest.get('operation_id') == operation,
         'MANIFEST_SCHEMA_OR_OPERATION_MISMATCH')
    invpath, raw = pin(manifest['native_inventory'])
    inventory = json.loads(gzip.decompress(raw) if invpath.suffix == '.gz' else raw)
    native = {}
    for row in inventory['rows']:
        if row.get('kind') == 'def':
            key = ('def', row['identity'], row['group'], row['frame'])
            need(key not in native, 'AMBIGUOUS_INVENTORY_SLOT')
            native[key] = row
    profile = doc(manifest['profile'])
    need(profile.get('schema') == PROFILE and profile.get('review_status') == 'assistant_scale_profile_pass',
         'REVIEWED_EXECUTION_PROFILE_REQUIRED')
    need(profile.get('replaces_canonical_art_standard') is False, 'PROFILE_IS_NOT_A_NEW_ART_STANDARD')
    standard = doc(profile['art_standard'])
    need(standard.get('version') == profile['art_standard']['version'], 'ART_STANDARD_VERSION_DRIFT')
    for ref in profile['reference_images']:
        png(ref)
    need(profile['reference_images'], 'PROFILE_REFERENCE_REQUIRED')
    expected = {tuple(k) for k in manifest['expected_slots']}
    items = manifest['items']
    output_layout=manifest.get('output_layout','native-crop')
    need(output_layout in ('native-crop','native-canvas'),'INVALID_OUTPUT_LAYOUT')
    padding_mode = sequence_mode and manifest.get('padding_policy') is not None
    if not sequence_mode:
        need('padding_policy' not in manifest, 'PADDING_POLICY_SEQUENCE_ONLY')
    need(0 < len(expected) == len(manifest['expected_slots']) <= 1024, 'EXPLICIT_UNIQUE_SCOPE_REQUIRED')
    need(len(items) == len(expected) and {tuple(x['key']) for x in items} == expected,
         'EXACT_SCOPE_COVERAGE_MISMATCH')
    if sequence_mode:
        import creature_sequence_registration
        creature_sequence_registration.validate(manifest,profile,native,expected)
        padding_root_tolerance = (creature_sequence_registration.PADDING_POLICY['root_tolerance_native']
                                  if padding_mode else None)
    else:
        padding_root_tolerance = None
        need(len({tuple(x['key'][:3]) for x in items}) == len(items),
             'STATIC_SLOTS_ONLY_USE_EXISTING_SEQUENCE_PIPELINE_FOR_ANIMATION')
    need(expected <= native.keys(), 'OFFICIAL_SLOT_NOT_FOUND')
    results = []; output_pixels_total = 0
    for item in items:
        key = tuple(item['key']); row = native[key]
        geometry = {'canvas_1x': row['canvas'], 'crop_1x': row['crop'], 'origin_1x': row['anchor']}
        cw, ch = row['canvas']; w, h = row['crop']; ox, oy = row['anchor']
        need(all(type(v) is int for v in [cw, ch, w, h, ox, oy]) and min(cw, ch, w, h) > 0
             and min(ox, oy) >= 0 and ox + w <= cw and oy + h <= ch, 'INVALID_NATIVE_GEOMETRY')
        rule = profile['directions'][item['profile_direction']]
        target = finite_pair(rule['anchor_native_canvas'], 'TARGET_ANCHOR_REQUIRED')
        if sequence_mode:
            offset=finite_pair(item['root_offset_native'],'SEQUENCE_EXPLICIT_ROOT_TRAJECTORY_REQUIRED')
            target=[target[i]+offset[i] for i in (0,1)]
        need(ox <= target[0] < ox + w and oy <= target[1] < oy + h, 'TARGET_ANCHOR_OUTSIDE_CROP')
        result = {'key': list(key), 'geometry': geometry, 'source': item.get('source'),
                  'profile_direction': item['profile_direction'], 'status': 'NEEDS_REDRAW',
                  'art_approved': False, 'runtime_verified': False, 'formal_3x': False}
        if sequence_mode:
            result.update(root_target_native_canvas=target,root_offset_native=item['root_offset_native'],
                          pose_group=rule['pose_group'],direction=rule['direction'],
                          crop_edge_contact_allowed=rule['clearance_native']==0)
            if output_layout == 'native-crop':
                result['root_target_native_crop'] = [target[0]-ox, target[1]-oy]
        if item.get('source') is None:
            result['reason'] = 'MISSING_HQ_MASTER'; results.append((result, None)); continue
        source = png(item['source']); box = source.getchannel('A').getbbox()
        need(box is not None, 'EMPTY_MASTER')
        annotation = item['measurement']
        need(annotation.get('image_sha256') == item['source']['sha256']
             and annotation.get('coordinate_space') == 'source_pixels'
             and annotation.get('method') == 'visual-anatomical-landmarks', 'SOURCE_BOUND_MANUAL_LANDMARKS_REQUIRED')
        anchor = finite_pair(annotation['anchor_source'], 'SOURCE_ANCHOR_REQUIRED')
        need(all(0 <= anchor[i] < source.size[i] for i in (0, 1)), 'SOURCE_ANCHOR_OUTSIDE_IMAGE')
        lengths = {name: measure(annotation['paths'][name], source.size) for name in rule['lengths_native']}
        need(rule['primary_metric'] in lengths, 'PRIMARY_BODY_METRIC_REQUIRED')
        targets = rule['lengths_native']
        need(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in targets.values()), 'INVALID_NATIVE_BODY_TARGET')
        primary = rule['primary_metric']; desired_k = lengths[primary] / targets[primary]
        tolerance = rule['primary_tolerance_fraction']; guard = rule['secondary_tolerance_fraction']
        need(0 < tolerance <= .15 and 0 < guard <= .35, 'BODY_TOLERANCE_OUT_OF_RANGE')
        allowed = []
        if padding_mode:
            registration = item['padding_registration']
            k = registration['common_k']
            offset = registration['offset']
            deviation = {name: lengths[name] / k / targets[name] - 1 for name in lengths}
            if (abs(deviation[primary]) > tolerance
                    or any(abs(v) > guard for n, v in deviation.items() if n != primary)):
                result.update(reason='PADDING_COMMON_K_BODY_TARGET_CONFLICT',
                              common_k=k, padding_offset=offset,
                              body_deviation_fraction=deviation)
                results.append((result, None)); continue
            # offset maps source pixel (0, 0) into the full native-canvas*K
            # output. It is supplied by the frozen sequence owner; do not
            # infer a per-frame fit or move the image after this point.
            dx, dy = offset[0] - ox*k, offset[1] - oy*k
            placed = [box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy]
            canvas_pixels = [cw*k, ch*k]
            canvas_bbox = [box[0]+offset[0], box[1]+offset[1],
                           box[2]+offset[0], box[3]+offset[1]]
            need(min(canvas_bbox[:2]) >= 0 and canvas_bbox[2] <= canvas_pixels[0]
                 and canvas_bbox[3] <= canvas_pixels[1],
                 'PADDING_POSITIVE_ALPHA_OUTSIDE_CANVAS')
            alpha = source.getchannel('A')
            core_box = alpha.point(lambda value: 255 if value >= 2 else 0).getbbox()
            need(core_box is not None, 'PADDING_ALPHA_GE2_CORE_REQUIRED')
            crop_bounds = [ox*k, oy*k, (ox+w)*k, (oy+h)*k]
            core_output = [core_box[0]+offset[0], core_box[1]+offset[1],
                           core_box[2]+offset[0], core_box[3]+offset[1]]
            margin = rule['clearance_native'] * k
            need(core_output[0] >= crop_bounds[0]+margin
                 and core_output[1] >= crop_bounds[1]+margin
                 and core_output[2] <= crop_bounds[2]-margin
                 and core_output[3] <= crop_bounds[3]-margin,
                 'PADDING_ALPHA_GE2_CORE_OUTSIDE_CROP_CLEARANCE')
            source_crop = (max(0, min(source.width, crop_bounds[0]-offset[0])),
                           max(0, min(source.height, crop_bounds[1]-offset[1])),
                           max(0, min(source.width, crop_bounds[2]-offset[0])),
                           max(0, min(source.height, crop_bounds[3]-offset[1])))
            alpha_histogram = alpha.histogram()
            inside_histogram = (alpha.crop(source_crop).histogram()
                                if source_crop[2] > source_crop[0]
                                and source_crop[3] > source_crop[1]
                                else [0]*256)
            core_outside = sum(alpha_histogram[2:]) - sum(inside_histogram[2:])
            need(core_outside == 0, 'PADDING_ALPHA_GE2_PIXEL_OUTSIDE_CROP')
            alpha1_outside = alpha_histogram[1] - inside_histogram[1]
            body_root = [(anchor[i] + offset[i]) / k for i in (0, 1)]
            root_error = [body_root[i] - target[i] for i in (0, 1)]
            need(all(abs(value) <= padding_root_tolerance for value in root_error),
                 'PADDING_ROOT_ALIGNMENT_EXCEEDS_ONE_NATIVE_UNIT')
            allowed.append((0, k, dx, dy, placed, deviation))

        def consider(k):
            if padding_mode:
                return
            if (cw*ch if output_layout=='native-canvas' else w*h)*k*k > 64 * 1024 * 1024:
                return
            deviation = {name: lengths[name] / k / targets[name] - 1 for name in lengths}
            if abs(deviation[primary]) > tolerance or any(abs(v) > guard for n, v in deviation.items() if n != primary):
                return
            if output_layout=='native-canvas':
                dx=round(target[0]*k-anchor[0])-ox*k
                dy=round(target[1]*k-anchor[1])-oy*k
            else:
                dx = round((target[0] - ox) * k - anchor[0]); dy = round((target[1] - oy) * k - anchor[1])
            placed = [box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy]
            # Alpha=1 is data too. No threshold, erasure or clipping concession.
            # A zero native clearance is a separately acknowledged sequence
            # policy. It may touch the native crop boundary but never discard
            # any positive-alpha pixel; the static contract keeps its margin.
            base_margin = 0 if sequence_mode and rule['clearance_native']==0 else 1
            if min(placed[:2]) < base_margin or placed[2] > w*k-base_margin or placed[3] > h*k-base_margin:
                return
            if sequence_mode:
                margin=rule['clearance_native']*k
                if (min(placed[:2])<margin or placed[2]>w*k-margin or placed[3]>h*k-margin):
                    return
            allowed.append((abs(math.log(k / desired_k)), k, dx, dy, placed, deviation))
        if not padding_mode:
            for k in range(4,97):consider(k)
        # Keep existing integer results stable. If no integer works, a full
        # native canvas may use an exact rational density whose two dimensions
        # are still integral. Copying the source pixels remains lossless.
        if not padding_mode and not allowed and output_layout=='native-canvas':
            denominator=math.gcd(cw,ch)
            for numerator in range(4*denominator,96*denominator+1):
                if numerator%denominator:consider(Fraction(numerator,denominator))
        result.update(source_pixels=list(source.size), source_positive_alpha_bbox=list(box),
                      measured_lengths_source=lengths, desired_k=desired_k,
                      alpha_bbox_used_as_body_measurement=False, pixels_resampled=False)
        if not allowed:
            result['reason'] = 'BODY_TARGET_OR_ANATOMY_CONFLICT_WITH_POSITIVE_ALPHA_FIT'
            results.append((result, None)); continue
        _, k, dx, dy, placed, deviation = min(allowed)
        output_pixels_total += (cw*ch if output_layout=='native-canvas' else w*h)*k*k
        need(output_pixels_total <= 64*1024*1024, 'BATCH_OUTPUT_PIXEL_BUDGET_SPLIT_OPERATION')
        if output_layout=='native-canvas':
            tx,ty=int(dx+ox*k),int(dy+oy*k)
            width,height=int(cw*k),int(ch*k)
            need(width*ch==height*cw,'EXACT_NATIVE_CANVAS_ASPECT_REQUIRED')
            out=Image.new('RGBA',(width,height));out.paste(source.crop(box),(box[0]+tx,box[1]+ty))
            placed=[box[0]+tx,box[1]+ty,box[2]+tx,box[3]+ty]
        else:
            tx,ty=dx,dy
            out = Image.new('RGBA', (w*k, h*k));out.paste(source.crop(box), (box[0]+dx, box[1]+dy))
        copied = out.crop(tuple(placed))
        need(copied.tobytes() == source.crop(box).tobytes(), 'PAINTED_RGBA_CHANGED')
        need(sum(out.getchannel('A').histogram()[1:]) == sum(source.getchannel('A').histogram()[1:]),
             'POSITIVE_ALPHA_NOT_PRESERVED')
        exact_scale={'numerator':k.numerator,'denominator':k.denominator} if isinstance(k,Fraction) else None
        if isinstance(k,Fraction):k=float(k);dx=float(dx);dy=float(dy)
        encoded = io.BytesIO(); out.save(encoded, format='PNG')
        result.update(status='LOSSLESS_LAYOUT_READY', reason=None, editing_scale=k,
                      output_layout=output_layout,translation_source_to_output=[tx,ty],
                      output_pixels=list(out.size), translation_source_to_crop=[dx,dy],
                      output_png_sha256=sha(encoded.getvalue()), output_rgba_sha256=sha(out.tobytes()),
                      positive_alpha_bbox_output=placed, body_deviation_fraction=deviation,
                      anchor_rounding_error_native=[(anchor[i]+[dx,dy][i])/k+[ox,oy][i]-target[i] for i in (0,1)],
                      source_positive_alpha_preserved=True, output_name=f'{key[1]}/{key[2]}_{key[3]}.png')
        if padding_mode:
            result.update(common_k=k, padding_offset=offset,
                          padding_root_error_native=root_error,
                          positive_alpha_within_full_canvas=True,
                          alpha_ge2_outside_native_crop_pixels=core_outside,
                          alpha1_outside_native_crop_pixels=alpha1_outside,
                          alpha_ge2_core_clearance_native=rule['clearance_native'])
        if exact_scale:result['editing_scale_exact']=exact_scale
        results.append((result, out))
    return manifest, profile, results


def brief(profile, results):
    lines = ['# 单人物尺度与重绘执行要求', '', '本文件是绑定当前美术规范的执行档案，不是第二份美术标准。',
             'GPT无需输出精确像素尺寸。必须保持单图单人物、真实透明Alpha、完整身体和装备；沿用身份父图及本槽官方动作/朝向。',
             '生成后重新标注身体与接地点，再运行同一尺度档案。画布尺寸或Alpha框接近不能代替身体尺度通过。',
             '本地只用透明画布像素复制调整布局；不增补缺肢、不擦Alpha、不插值放大母版。正式导出仍只采样一次。', '']
    for result, _ in results:
        rule = profile['directions'][result['profile_direction']]
        lines += [f"## {result['key']}", f"状态：{result['status']}；原因：{result.get('reason') or '布局可无损处理'}。",
                  f"身体基准（原生单位）：{rule['lengths_native']}；接地点：{rule['anchor_native_canvas']}。",
                  f"原生画布/裁片/原点：{result['geometry']}。",
                  '若身体目标与装备边界不能同时满足，应在重绘时调整具体构图/装备姿态，不能缩小全人物来掩盖或裁掉边缘。', '']
        if result.get('common_k') is not None:
            lines[-1:-1] = [f"全组固定K：{result['common_k']}；透明留白偏移：{result['padding_offset']}；",
                            f"根点误差（原生单位）：{result['padding_root_error_native']}；裁片外Alpha=1像素：{result['alpha1_outside_native_crop_pixels']}。"]
    return '\n'.join(lines)


def execute(action, manifest_ref, outdir, operation):
    need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,95}', operation or ''), 'INVALID_OPERATION_ID')
    root = path(outdir)
    need(root.parent == (PRODUCT/'build/experiments/scale-layout').resolve() and root.name == operation,
         'OUTPUT_MUST_BE_OWNED_NEW_SCALE_LAYOUT_OPERATION')
    manifest, profile, results = resolve(manifest_ref, operation)
    sequence_mode=manifest.get('schema') in (
        'h3.creature.sequence-registration.manifest.v1',
        'h3.creature.sequence-registration.manifest.v2')
    if sequence_mode and action=='run':
        need(all(image is not None for _,image in results),'SEQUENCE_FIT_CONFLICT_REVIEW_BEFORE_WRITING')
    for result, _ in results:
        if result.get('source'):
            need(not path(result['source']['path']).is_relative_to(root), 'INPUT_OUTPUT_OVERLAP')
    report = {'schema': RECEIPT, 'operation_id': operation, 'manifest': manifest_ref,
              'profile': manifest['profile'], 'native_inventory': manifest['native_inventory'],
              'items': [r for r, _ in results], 'pixels_resampled': False,
              'automatic_art_approval': False, 'formal_3x': False, 'game_deployed': False}
    if sequence_mode:
        report.update(sequence_contract=manifest['sequence_contract'],
                      anatomical_targets_and_root_trajectory_explicit=True,
                      body_semantics_automatically_recognized=False,
                      independent_per_frame_contain=False)
        if manifest.get('padding_policy') is not None:
            report['padding_policy'] = manifest['padding_policy']
    summary = dict(ready=sum(im is not None for _, im in results), needs_redraw=sum(im is None for _, im in results))
    if action == 'check':
        return {'status': 'CHECKED_WITHOUT_WRITING', **summary, 'items': report['items']}
    receipt = root/'receipt.json'
    if action == 'run' and root.exists():
        need(receipt.is_file(), 'FOREIGN_OR_PARTIAL_OPERATION'); action = 'verify'
    if action == 'verify':
        actual = json.loads(receipt.read_bytes())
        need(actual == report, 'RECEIPT_REPLAY_MISMATCH')
        for result, image in results:
            if image is not None:
                p = root/result['output_name']
                need(sha(p.read_bytes()) == result['output_png_sha256'], 'OUTPUT_PNG_SHA_DRIFT')
                with Image.open(p) as stored:
                    stored.load()
                    need(stored.mode == 'RGBA' and stored.size == image.size and stored.tobytes() == image.tobytes(),
                         'OUTPUT_PIXEL_REPLAY_MISMATCH')
        need((root/'generation-brief.md').read_text(encoding='utf-8') == brief(profile, results), 'BRIEF_REPLAY_MISMATCH')
        return {'status': 'VERIFIED_LOSSLESS_LAYOUT', **summary, 'receipt': str(receipt)}
    need(action == 'run' and not root.exists(), 'OUTPUT_ALREADY_EXISTS')
    root.mkdir(parents=True)
    for result, image in results:
        if image is not None:
            p = root/result['output_name']; p.parent.mkdir(parents=True, exist_ok=True)
            with p.open('xb') as stream:
                image.save(stream, format='PNG')
    # Re-pin all dependencies after writing. Never silently accept a source/profile change.
    resolve(manifest_ref, operation)
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    (root/'generation-brief.md').write_text(brief(profile, results), encoding='utf-8')
    return {'status': 'LOSSLESS_LAYOUT_CANDIDATES_CREATED', **summary, 'receipt': str(receipt)}


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('sequence-qa','sequence-stabilize'):
        import creature_sequence_qa
        return creature_sequence_qa.main(argv)
    if argv and argv[0].startswith('sequence-'):
        import creature_sequence_registration
        return creature_sequence_registration.main([argv[0][len('sequence-'):]]+argv[1:])
    if argv and argv[0].startswith('road-batch-'):
        import road_batch_quality
        return road_batch_quality.main([argv[0][len('road-batch-'):]]+argv[1:])
    if argv and argv[0]=='road-generation-packet':
        import road_generation_packet
        return road_generation_packet.main(argv[1:])
    if argv and argv[0]=='map-source-proof':
        import map_source_oracle
        return map_source_oracle.main(argv[1:])
    if argv and argv[0] in ('map-fixture','map-review'):
        import map_assembly
        return map_assembly.main([argv[0][len('map-'):]]+argv[1:])
    if argv and argv[0].startswith('quality-'):
        import art_batch_quality
        return art_batch_quality.main([argv[0][len('quality-'):]]+argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('catalog', 'check', 'run', 'verify'))
    for name in ('manifest', 'manifest-sha256', 'outdir', 'operation-id', 'renderer', 'renderer-sha256'):
        parser.add_argument('--'+name)
    args = parser.parse_args(argv)
    try:
        if args.action == 'catalog':
            from art_batch_quality import PRESETS
            result = {'schema': SCHEMA, 'scope': 'explicit static slots or binary-bound complete creature action groups', 'pixels_resampled': False,
                      'sequence_schemas':['h3.creature.sequence-registration.manifest.v1',
                                          'h3.creature.sequence-registration.manifest.v2'],
                      'sequence_stages':['sequence-check','sequence-run','sequence-verify'],
                      'quality_presets': PRESETS, 'quality_setup': 'quality-init / quality-freeze',
                      'needs': ['pinned HQ master', 'reviewed per-direction anatomical profile', 'source-bound manual landmarks'],
                      'redraw_on': ['missing master', 'anatomy/fit conflict'], 'automatic_art_approval': False}
        else:
            need(all((args.manifest, args.manifest_sha256, args.outdir, args.operation_id)), 'FROZEN_REQUEST_REQUIRED')
            if args.renderer:
                pin({'path': args.renderer, 'sha256': args.renderer_sha256})
            manifest_ref={'path':str(path(args.manifest)),'sha256':args.manifest_sha256}
            manifest_schema=doc(manifest_ref).get('schema')
            if manifest_schema=='h3.art.batch-quality.manifest.v1':
                import art_batch_quality
                result=art_batch_quality.execute(args.action,manifest_ref,args.outdir,args.operation_id)
            else:
                result=execute(args.action,manifest_ref,args.outdir,args.operation_id)
                if (manifest_schema in ('h3.creature.sequence-registration.manifest.v1',
                                        'h3.creature.sequence-registration.manifest.v2')
                        and args.action!='check' and not result.get('needs_redraw')):
                    import creature_sequence_registration
                    result.update(creature_sequence_registration.handoff(
                        manifest_ref,args.outdir,create=args.action=='run'))
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'status': 'BLOCKED', 'code': 'SCALE_LAYOUT_CONTRACT', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
