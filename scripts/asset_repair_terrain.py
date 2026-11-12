"""Shared HQ terrain families; no native RGB, upscaling or automatic approval."""
from collections import deque
import hashlib
import json
import numpy as np

from asset_repair_interfaces import native_rows, require_slot
from terrain_texture_core import texture_variant, join_fixed_frame


def selected_pixels(api, rgba, rule):
    api.need(set(rule) == {'minimum_rgb', 'maximum_rgb', 'minimum_channel_differences'}, 'TERRAIN_ACCENT_RULE_FIELDS')
    lo, hi = np.asarray(rule['minimum_rgb']), np.asarray(rule['maximum_rgb'])
    api.need(lo.shape == hi.shape == (3,) and np.isfinite(lo).all() and np.isfinite(hi).all()
             and np.all((lo >= 0) & (lo <= hi) & (hi <= 255)), 'TERRAIN_ACCENT_RGB_RANGE')
    rgb = rgba[..., :3].astype(np.int16)
    mask = ((rgb >= lo) & (rgb <= hi)).all(axis=-1)
    channels = {'r': 0, 'g': 1, 'b': 2}
    for key, threshold in rule['minimum_channel_differences'].items():
        api.need(key in ('r-g', 'r-b', 'g-r', 'g-b', 'b-r', 'b-g')
                 and type(threshold) is int and -255 <= threshold <= 255, 'TERRAIN_ACCENT_CHANNEL_DIFFERENCE')
        mask &= rgb[..., channels[key[0]]] - rgb[..., channels[key[2]]] >= threshold
    return mask


def components(mask, minimum):
    """4-connected painted feature components; no image filtering/resizing."""
    visited = np.zeros(mask.shape, bool)
    h, w = mask.shape
    result = []
    for y, x in zip(*np.where(mask)):
        if visited[y, x]:
            continue
        queue = deque([(int(y), int(x))])
        visited[y, x] = True
        pixels = []
        while queue:
            yy, xx = queue.popleft()
            pixels.append((yy, xx))
            for ny, nx in ((yy-1, xx), (yy+1, xx), (yy, xx-1), (yy, xx+1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    queue.append((ny, nx))
        if len(pixels) >= minimum:
            result.append(np.asarray(pixels, np.int32))
    return result


def retain_accents(api, base, donor, spec, frame):
    api.need(base.shape == donor.shape and np.all(donor[..., 3] == 255), 'TERRAIN_ACCENT_HQ_GEOMETRY')
    seeds = selected_pixels(api, donor, spec['seed_rule'])
    original_count = int(seeds.sum())
    h, w = seeds.shape
    padding = spec['padding_pixels']
    minimum = spec['minimum_component_pixels']
    api.need(type(padding) is int and 16 <= padding <= 96 and type(minimum) is int
             and 4 <= minimum <= 4096, 'TERRAIN_ACCENT_COMPONENT_PARAMETERS')
    allowed = np.zeros((h, w), bool)
    allowed[frame+padding:h-frame-padding, frame+padding:w-frame-padding] = True
    seeds &= allowed
    groups = components(seeds, minimum)
    api.need(bool(groups), 'TERRAIN_DECLARED_ACCENT_NOT_FOUND')
    api.need(len(groups) <= 512, 'TERRAIN_ACCENT_COMPONENT_CAPACITY')
    result = base.copy()
    retained = np.zeros(seeds.shape, bool)
    patches = []
    for group in groups:
        y0, x0 = group.min(axis=0)-padding
        y1, x1 = group.max(axis=0)+padding+1
        y0, x0 = max(frame, int(y0)), max(frame, int(x0))
        y1, x1 = min(h-frame, int(y1)), min(w-frame, int(x1))
        # The complete protected component lies strictly inside these cuts.
        # All introduced background pixels are real HQ donor pixels too.
        a = max(2, padding//4)
        b = max(a+1, padding//2)
        tile, _ = join_fixed_frame(donor[y0:y1, x0:x1], result[y0:y1, x0:x1], a, b)
        result[y0:y1, x0:x1] = tile
        retained[group[:, 0], group[:, 1]] = True
        patches.append([x0, y0, x1-x0, y1-y0])
    api.need(not retained.any() or np.array_equal(result[retained], donor[retained]),
             'TERRAIN_ACCENT_PROTECTED_PIXELS_CHANGED')
    api.need(np.array_equal(result[:frame], base[:frame]) and np.array_equal(result[-frame:], base[-frame:])
             and np.array_equal(result[:, :frame], base[:, :frame]) and np.array_equal(result[:, -frame:], base[:, -frame:]),
             'TERRAIN_ACCENT_CHANGED_SHARED_BOUNDARY')
    return result, {'seed_pixels_before_boundary_policy': original_count,
                    'seed_pixels_in_allowed_centre': int(seeds.sum()),
                    'protected_painted_pixels_retained_exactly': int(retained.sum()),
                    'component_patch_count': len(groups), 'patch_rects': patches,
                    'boundary_clipping_explicit': True, 'art_review_required': True}


def family(api, job, parent, g, scale):
    api.need(g == {'canvas_1x': [32, 32], 'crop_1x': [32, 32], 'origin_1x': [0, 0]}
             and 16 <= scale <= 32 and np.all(parent[..., 3] == 255), 'TERRAIN_FULL_OPAQUE_HQ_NATIVE32_REQUIRED')
    contract = json.loads(api.pin(job['family_contract'])[1])
    api.need(contract.get('schema') == 'h3.asset-repair.terrain-family.v1'
             and contract.get('parent_key') == job['native_key'], 'TERRAIN_FAMILY_PARENT_BINDING')
    api.token(contract['material_id'])
    rows = native_rows(api, contract['native_inventory'])
    require_slot(api, rows, job['native_key'], g)
    variants = job['variants']
    api.need(isinstance(variants, list) and 1 <= len(variants) <= 32, 'TERRAIN_VARIANT_CAPACITY')
    keys = [v['native_key'] for v in variants]
    api.need(keys == contract['target_keys'] and len({tuple(k) for k in keys}) == len(keys), 'TERRAIN_TARGET_KEYS_DIFFER')
    minimum, maximum = job['frame_native']
    patch, overlap = job['patch_native'], job['overlap_native']
    api.need(all(type(x) in (int, float) and np.isfinite(x) and float(x*scale).is_integer()
                 for x in (minimum, maximum, patch, overlap))
             and 1.5 <= minimum < maximum <= 6 and 4 <= patch <= 12 and .5 <= overlap < patch/2,
             'TERRAIN_EXPLICIT_TEXTURE_SAMPLING_PARAMETERS')
    outputs, bindings, measured, digests = [], {}, [], set()
    for variant in variants:
        api.need(set(variant) <= {'id', 'native_key', 'seed', 'accent'} and set(variant) >= {'id', 'native_key', 'seed'},
                 'TERRAIN_VARIANT_FIELDS')
        api.token(variant['id'])
        key = variant['native_key']
        api.need(key[:3] == job['native_key'][:3] and type(variant['seed']) is int and 0 <= variant['seed'] < 2**32,
                 'TERRAIN_VARIANT_FAMILY_OR_SEED')
        require_slot(api, rows, key, g)
        result, stats = texture_variant(parent, size=32*scale, seed=variant['seed'], minimum=int(minimum*scale),
                                        maximum=int(maximum*scale), patch=int(patch*scale), overlap=int(overlap*scale))
        if 'accent' in variant:
            accent = variant['accent']
            api.need(set(accent) == {'source', 'seed_rule', 'padding_pixels', 'minimum_component_pixels'}, 'TERRAIN_ACCENT_FIELDS')
            donor, dg, ds = api.registered({'source': accent['source'], 'native_key': key})
            api.need(dg == g and ds == scale, 'TERRAIN_ACCENT_REGISTRATION_DIFFERS')
            result, accent_stats = retain_accents(api, result, donor, accent, int(maximum*scale))
            stats['base_before_accent_pixels_exactly_from_parent'] = stats.pop('all_pixels_exactly_from_source')
            stats['final_pixel_sources'] = ['registered_material_parent', 'registered_slot_accent_parent']
            stats['accent'] = accent_stats
        digest = hashlib.sha256(result.tobytes()).hexdigest()
        api.need(digest not in digests, 'TERRAIN_VARIANTS_ARE_IDENTICAL')
        digests.add(digest)
        outputs.append((variant['id'], result))
        bindings[variant['id']] = {'native_key': key, 'geometry': g, 'editing_scale': scale, 'layout': 'full-canvas'}
        measured.append({'id': variant['id'], 'statistics': stats})
    return outputs, {'material_id': contract['material_id'], 'shared_painted_parent': True,
                     'all_RGB_from_real_HQ_parents': True, 'native_RGB_used_as_paint': False,
                     'output_geometry': bindings, 'variants': measured, 'automatic_art_approval': False,
                     'complete_legal_map_adjacency_verified': False, 'runtime_verified': False}
