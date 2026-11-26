"""Synchronous creature sequence quality assurance and static-body stabilization.

Consolidates transparent-background & anti-fogging checks, 1999 Complete slot &
VCMI animation metadata verification, 44px-opened core body scale/spatial lock
verification, floating-fragment/limb anomaly detection, and ghost-free lower-body
stabilization into H3TierExport.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import numpy as np
from PIL import Image, ImageFilter

from review_motion_sequence import component_regions

QA_SCHEMA = 'h3.creature.sequence-qa.report.v1'
STABILIZE_SCHEMA = 'h3.creature.sequence-stabilize.receipt.v1'
SLOT_RE = re.compile(r'^(\d+)_(\d+)\.png$', re.IGNORECASE)
ART_TRASH_ROOT = Path(r'{VX-root}\H3美术素材\待删除')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def cie_lab(rgb: np.ndarray) -> np.ndarray:
    if len(rgb) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    srgb = rgb.astype(np.float64) / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array([
        [0.4124564, 0.2126729, 0.0193339],
        [0.3575761, 0.7151522, 0.1191920],
        [0.1804375, 0.0721750, 0.9503041],
    ])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.column_stack([116.0 * f[:, 1] - 16.0, 500.0 * (f[:, 0] - f[:, 1]), 200.0 * (f[:, 1] - f[:, 2])])


def interior_core_opacity_metrics(alpha: np.ndarray) -> dict:
    """Measure eroded interior core alpha to catch semi-transparent fogging."""
    fg_u8 = ((alpha >= 16).astype(np.uint8)) * 255
    pil_fg = Image.fromarray(fg_u8, mode='L')
    min_dim = min(alpha.shape)
    erode_size = 11 if min_dim >= 96 else (5 if min_dim >= 32 else 3)
    eroded = np.array(pil_fg.filter(ImageFilter.MinFilter(erode_size))) > 128
    if int(eroded.sum()) < 32:
        eroded = alpha >= 128
    if not np.any(eroded):
        return {
            'core_pixels': 0,
            'core_median_alpha': 0.0,
            'core_mean_alpha': 0.0,
            'core_sub250_fraction': 1.0,
            'status': 'EMPTY_CORE',
            'diagnostic_only': True,
            'alpha_modified': False,
        }
    core_vals = alpha[eroded]
    med = float(np.median(core_vals))
    mean = float(core_vals.mean())
    sub200 = float((core_vals < 200).mean())
    sub250 = float((core_vals < 250).mean())
    foggy = med < 225.0 or (mean < 215.0 and sub200 > 0.20)
    return {
        'core_pixels': int(eroded.sum()),
        'core_median_alpha': round(med, 2),
        'core_mean_alpha': round(mean, 2),
        'core_sub250_fraction': round(sub250, 4),
        'status': 'FOGGY_SEMI_TRANSPARENT_CORE_RISK' if foggy else 'SOLID_OPAQUE_CORE',
        'diagnostic_only': True,
        'alpha_modified': False,
    }


def get_core_body_mask_44px(im: Image.Image) -> np.ndarray:
    """Compute morphological core body mask stripping thin weapons, pikes, and wing tips."""
    arr = np.array(im.convert('RGBA'))
    alpha = arr[:, :, 3]
    h, w = alpha.shape
    mask_u8 = ((alpha >= 128).astype(np.uint8)) * 255
    pil_m = Image.fromarray(mask_u8, mode='L')
    if min(w, h) >= 320:
        dw, dh = max(1, w // 4), max(1, h // 4)
        small = pil_m.resize((dw, dh), Image.Resampling.BILINEAR)
        opened_small = small.filter(ImageFilter.MinFilter(11)).filter(ImageFilter.MaxFilter(11))
        opened = np.array(opened_small.resize((w, h), Image.Resampling.BILINEAR)) >= 112
        if int(opened.sum()) >= 200:
            return opened & (alpha >= 128)
    k = 9 if min(w, h) >= 96 else 3
    opened = np.array(pil_m.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))) > 128
    if int(opened.sum()) < 50:
        return alpha >= 128
    return opened & (alpha >= 128)


def get_core_anchor_and_bbox(core_mask: np.ndarray, full_alpha: np.ndarray = None):
    ys, xs = np.nonzero(core_mask)
    if len(ys) == 0:
        return None
    ymin, ymax_raw = int(ys.min()), int(ys.max())
    xmin, xmax = int(xs.min()), int(xs.max())
    ch = max(1, ymax_raw - ymin + 1)
    cw = max(1, xmax - xmin + 1)
    # Use lower-torso + legs band (35%..85% height) for stable horizontal center,
    # avoiding low wing-tip or sword-tip jumps at the very bottom.
    torso_legs_band = (ys >= (ymin + int(ch * 0.35))) & (ys <= (ymin + int(ch * 0.88)))
    x_center = float(np.median(xs[torso_legs_band])) if np.any(torso_legs_band) else float(np.median(xs))
    # Measure foot ground anchor:
    # Ground contact anchor for standing sprites: measure bottom-most extent of where the lower body
    # (boots, paws, hooves, tail, contact points) touches the ground plane.
    # Taking the 99.2th percentile of the lower 30% of pixels captures the ground plane robustly
    # without depending on arbitrary central column widths or falling between spaced feet.
    if full_alpha is not None and np.any(full_alpha >= 64):
        ys_a = np.nonzero(full_alpha >= 64)[0]
        lower_a = ys_a[ys_a >= int(ys_a.min() + (ys_a.max() - ys_a.min()) * 0.70)]
        ymax_ground = float(np.percentile(lower_a, 99.2)) if len(lower_a) > 0 else float(ys_a.max())
    else:
        lower_body = ys >= (ymin + int(ch * 0.70))
        ymax_ground = float(np.percentile(ys[lower_body], 99.2)) if np.any(lower_body) else float(ymax_raw)
    return {
        'bbox_ltrb': [xmin, ymin, xmax + 1, ymax_raw + 1],
        'width': cw,
        'height': ch,
        'x_foot': x_center,
        'ymax': ymax_ground,
        'area': int(core_mask.sum()),
    }


def solidify_rgba_image(im: Image.Image) -> Image.Image:
    """Eliminate semi-transparent interior fogging and background noise without binary cutout."""
    arr = np.array(im.convert('RGBA'), dtype=np.uint8)
    alpha = arr[:, :, 3]
    arr[alpha <= 15] = [0, 0, 0, 0]
    solid = alpha >= 242
    arr[solid, 3] = 255
    fg_u8 = ((alpha >= 64).astype(np.uint8)) * 255
    eroded = np.array(Image.fromarray(fg_u8, mode='L').filter(ImageFilter.MinFilter(7))) > 128
    arr[eroded, 3] = 255
    return Image.fromarray(arr, mode='RGBA')


def place_scaled_rgba_on_canvas(im: Image.Image, target_size, scale: float, dx: int, dy: int) -> Image.Image:
    """Place scaled RGBA onto target_size canvas using direct slice assignment (never self-masked paste)."""
    tw, th = target_size
    if abs(scale - 1.0) < 1e-4:
        scaled = im.convert('RGBA')
    else:
        nw = max(1, int(round(im.width * scale)))
        nh = max(1, int(round(im.height * scale)))
        scaled = im.convert('RGBA').resize((nw, nh), Image.Resampling.LANCZOS)
    # Rigid placement only: never rewrite alpha, never cut out, never composite other frames.
    src = np.array(scaled, dtype=np.uint8)
    sh, sw = src.shape[:2]
    dst = np.zeros((th, tw, 4), dtype=np.uint8)

    sx0 = max(0, -dx)
    sy0 = max(0, -dy)
    dx0 = max(0, dx)
    dy0 = max(0, dy)
    copy_w = min(sw - sx0, tw - dx0)
    copy_h = min(sh - sy0, th - dy0)
    if copy_w > 0 and copy_h > 0:
        dst[dy0:dy0 + copy_h, dx0:dx0 + copy_w] = src[sy0:sy0 + copy_h, sx0:sx0 + copy_w]
    return Image.fromarray(dst, mode='RGBA')


def prune_isolated_floating_noise(im_reg: Image.Image, im0: Image.Image, max_px: int = 5000) -> Image.Image:
    """Prune small disconnected floating debris (leaves, sparkles, dust) outside character footprint."""
    arr = np.array(im_reg)
    vis0 = np.array(im0)[..., 3] >= 16
    overall_modified = False
    for _ in range(3):
        vis = arr[..., 3] >= 16
        comps, _ = component_regions(vis, max_runs=200000)
        if len(comps) <= 1:
            break
        main_area = comps[0]['pixels']
        pass_modified = False
        for comp in comps[1:]:
            px = comp['pixels']
            if 0 < px <= max_px and px < main_area * 0.05:
                bx0, by0, bx1, by1 = comp['bbox_ltrb']
                sub_vis = vis[by0:by1, bx0:bx1]
                ov = float(vis0[by0:by1, bx0:bx1][sub_vis].mean()) if np.any(sub_vis) else 0.0
                if ov < 0.15:
                    arr[by0:by1, bx0:bx1, 3] = np.where(sub_vis, 0, arr[by0:by1, bx0:bx1, 3])
                    pass_modified = True
                    overall_modified = True
        if not pass_modified:
            break
    return Image.fromarray(arr, mode='RGBA') if overall_modified else im_reg


def register_frame_to_f0_core(im0: Image.Image, imi: Image.Image, lock_y_frac: float = 0.50):
    """Find optimal (scale, dx, dy) maximizing 44px-opened lower-body & full-body core IoU."""
    w0, h0 = im0.size
    alpha0 = np.array(im0)[:, :, 3]
    alphai = np.array(imi)[:, :, 3]
    core0 = get_core_body_mask_44px(im0)
    info0 = get_core_anchor_and_bbox(core0, alpha0)
    corei_raw = get_core_body_mask_44px(imi)
    infoi = get_core_anchor_and_bbox(corei_raw, alphai)
    if not info0 or not infoi:
        return imi.convert('RGBA').resize((w0, h0), Image.Resampling.LANCZOS), {
            'scale': 1.0, 'dx': 0, 'dy': 0, 'lower_core_iou': 0.0, 'all_core_iou': 0.0
        }

    ymin0, ymax0 = info0['bbox_ltrb'][1], info0['bbox_ltrb'][3] - 1
    split_y = int(ymin0 + (ymax0 - ymin0) * lock_y_frac)
    lower0 = core0.copy()
    lower0[:split_y, :] = False
    if int(lower0.sum()) < 50:
        lower0 = core0

    pil_corei = Image.fromarray((corei_raw.astype(np.uint8)) * 255, mode='L')
    scaled_cache = {}

    def get_scaled_core(s_val: float) -> np.ndarray:
        key = round(float(s_val), 4)
        if key not in scaled_cache:
            if abs(key - 1.0) < 1e-4:
                scaled_cache[key] = corei_raw
            else:
                nw = max(1, int(round(imi.width * key)))
                nh = max(1, int(round(imi.height * key)))
                scaled_cache[key] = np.array(pil_corei.resize((nw, nh), Image.Resampling.NEAREST)) > 128
        return scaled_cache[key]

    def eval_placement(s_val: float, dx: int, dy: int):
        sm = get_scaled_core(s_val)
        sh, sw = sm.shape
        sx0, sy0 = max(0, -dx), max(0, -dy)
        dx0, dy0 = max(0, dx), max(0, dy)
        cw = min(sw - sx0, w0 - dx0)
        ch = min(sh - sy0, h0 - dy0)
        if cw <= 0 or ch <= 0:
            return -1.0, 0.0, 0.0
        ci = np.zeros((h0, w0), dtype=bool)
        ci[dy0:dy0 + ch, dx0:dx0 + cw] = sm[sy0:sy0 + ch, sx0:sx0 + cw]
        low_i = ci.copy()
        low_i[:split_y, :] = False
        inter_low = int((lower0 & low_i).sum())
        u_low = int((lower0 | low_i).sum())
        min_low = min(int(lower0.sum()), int(low_i.sum()))
        iou_low = float(inter_low) / u_low if u_low > 0 else 0.0
        cont_low = float(inter_low) / min_low if min_low > 0 else 0.0
        eff_low = max(iou_low, 0.5 * iou_low + 0.5 * cont_low)
        u_all = int((core0 | ci).sum())
        iou_all = float((core0 & ci).sum()) / u_all if u_all > 0 else 0.0
        reg_penalty = 0.15 * abs(s_val - 1.0)
        return 0.72 * eff_low + 0.28 * iou_all - reg_penalty, eff_low, iou_all

    candidates = []
    if imi.size == im0.size:
        for dy0 in range(-12, 13, 3):
            for dx0 in range(-12, 13, 3):
                sc, ilow, iall = eval_placement(1.0, dx0, dy0)
                candidates.append((sc + (0.015 if (dx0 == 0 and dy0 == 0) else 0.0), 1.0, dx0, dy0, ilow, iall))

    s_h = info0['height'] / max(1.0, float(infoi['height']))
    s_w = info0['width'] / max(1.0, float(infoi['width']))
    s_area = np.sqrt(info0['area'] / max(1.0, float(infoi['area'])))
    s_priors = sorted({
        1.0,
        float(np.clip(s_h, 0.90, 1.10)),
        float(np.clip(0.5 * s_h + 0.5 * s_area, 0.90, 1.10)),
        float(np.clip(0.5 * s_h + 0.5 * s_w, 0.90, 1.10)),
    })
    scale_grid = set()
    for sp in s_priors:
        for delta in np.linspace(-0.04, 0.04, 5):
            scale_grid.add(round(float(np.clip(sp + delta, 0.88, 1.12)), 4))

    for s in sorted(scale_grid):
        base_dx = int(round(info0['x_foot'] - infoi['x_foot'] * s))
        base_dy = int(round(info0['ymax'] - infoi['ymax'] * s))
        for ddy in range(-16, 17, 4):
            for ddx in range(-20, 21, 4):
                dx = base_dx + ddx
                dy = base_dy + ddy
                sc, ilow, iall = eval_placement(s, dx, dy)
                candidates.append((sc, s, dx, dy, ilow, iall))

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_sc, best_s, best_dx, best_dy, best_ilow, best_iall = candidates[0]

    for ds in (-0.005, 0.0, 0.005):
        s = round(float(np.clip(best_s + ds, 0.88, 1.12)), 4)
        for ddy in range(-3, 4):
            for ddx in range(-3, 4):
                dx = best_dx + ddx
                dy = best_dy + ddy
                sc, ilow, iall = eval_placement(s, dx, dy)
                if sc > best_sc:
                    best_sc, best_s, best_dx, best_dy, best_ilow, best_iall = sc, s, dx, dy, ilow, iall

    placed = place_scaled_rgba_on_canvas(imi, (w0, h0), best_s, best_dx, best_dy)
    return placed, {
        'scale': round(float(best_s), 4),
        'dx': int(best_dx),
        'dy': int(best_dy),
        'lower_core_iou': round(float(best_ilow), 4),
        'all_core_iou': round(float(best_iall), 4),
    }


STABILIZER_ID = 'rigid-register-v6'
# Rigid-only contract: the generated frame is only shifted (and at most resampled once
# for scale); pixels of Frame 0 are never blended in, alpha is never rewritten and no
# component is removed. The v5 lower-body lock caused ghost legs, sword residue and
# broken weapon shafts at its blend seam, so it is permanently retired.
RIGID_PIPELINE_CONTRACT = {
    'stabilizer': STABILIZER_ID,
    'pixel_composite_from_f0': False,
    'alpha_modified': False,
    'component_removal': False,
    'local_matting': False,
}

# Choreography red lines (user ruling 2026-11-11): nodding / head shaking / looking
# up-down loops look silly; the creature stays in place but the whole body moves
# in coordination, and weapons stay rigidly attached to the hand that holds them.
BANNED_HEAD_MOTION_RE = re.compile(r'点头|摇头|抬头|低头|仰头|垂头|摆头|晃头|nod|head[ -]?shake|head[ -]?bob', re.IGNORECASE)
NEGATION_PREFIX_RE = re.compile(r'(禁止|严禁|不要|不得|不准|避免|无|不)\s*$')


def apply_static_lower_body_lock_v5(*_args, **_kwargs):
    """Retired: compositing Frame 0 pixels into generated frames is forbidden."""
    raise RuntimeError('F0_PIXEL_COMPOSITE_FORBIDDEN:lower-body lock retired (ghosting/seam breaks)')


def find_banned_head_motion(text: str) -> list:
    hits = []
    for m in BANNED_HEAD_MOTION_RE.finditer(text or ''):
        prefix = (text or '')[max(0, m.start() - 4):m.start()]
        if NEGATION_PREFIX_RE.search(prefix):
            continue
        hits.append(m.group(0))
    return hits


def _hue_cell_hist(arr: np.ndarray, cell: int, bins: int = 12) -> np.ndarray:
    h, w = arr.shape[:2]
    gh, gw = max(1, h // cell), max(1, w // cell)
    hsv = np.array(Image.fromarray(arr[:gh * cell, :gw * cell, :3], mode='RGB').convert('HSV'))
    alpha = arr[:gh * cell, :gw * cell, 3]
    sat = (hsv[:, :, 1] >= 90) & (hsv[:, :, 2] >= 50) & (alpha >= 128)
    hb = (hsv[:, :, 0].astype(np.int32) * bins) // 256
    yy, xx = np.nonzero(sat)
    out = np.zeros((gh, gw, bins), dtype=np.int32)
    np.add.at(out, (yy // cell, xx // cell, hb[yy, xx]), 1)
    return out


def novel_color_regions(arr0: np.ndarray, arri: np.ndarray, radius_cells: int = 3) -> dict:
    """Find saturated hue clusters in frame i that have no counterpart near them in Frame 0.

    Catches identity drift such as a blue plume appearing on a helmet, gold trims
    appearing on a plain shield, or recolored props.  Diagnostic only.
    """
    h, w = arr0.shape[:2]
    cell = max(8, min(h, w) // 48)
    c0 = _hue_cell_hist(arr0, cell)
    ci = _hue_cell_hist(arri, cell)
    present0 = c0 >= max(2, int(cell * cell * 0.03))
    gh, gw, nb = present0.shape
    pad = np.pad(present0, ((radius_cells, radius_cells), (radius_cells, radius_cells), (0, 0)))
    dil = np.zeros_like(present0)
    for oy in range(0, 2 * radius_cells + 1):
        for ox in range(0, 2 * radius_cells + 1):
            dil |= pad[oy:oy + gh, ox:ox + gw]
    novel = (ci >= int(cell * cell * 0.25)) & ~dil
    novel_cells = novel.any(axis=2)
    fg_px = max(1, int((arri[:, :, 3] >= 128).sum()))
    largest = 0
    bbox = None
    if novel_cells.any():
        comps, _ = component_regions(novel_cells, max_runs=200000)
        if comps:
            largest = int(comps[0]['pixels']) * cell * cell
            bx0, by0, bx1, by1 = comps[0]['bbox_ltrb']
            bbox = [bx0 * cell, by0 * cell, bx1 * cell, by1 * cell]
    return {
        'cell_px': cell,
        'novel_cell_count': int(novel_cells.sum()),
        'largest_cluster_fraction_of_fg': round(largest / fg_px, 5),
        'largest_cluster_bbox_ltrb': bbox,
    }


def write_unit_manifest(unit_dir: Path, identity: str, faction: str, chinese_name: str,
                        choreography: dict, stabilize_receipt: dict, backup_dir: Path = None) -> dict:
    """Write <IDENTITY>.json (VCMI playback contract) and <IDENTITY>_动作编排.md.

    choreography = {
      'action_archetype_id': str, 'action_family': str, 'concept': str,
      'keyframes': [{'frame': '0_0.png', 'phase': str, 'action': str}, ...],
      'playback_sequence_indices': [...], 'frame_durations_ms': [...]
    }
    """
    unit_dir = Path(unit_dir)
    backups = []
    seq = choreography.get('playback_sequence_indices') or [0, 1, 2, 3, 4, 3, 2, 1]
    dur = choreography.get('frame_durations_ms') or [450, 130, 130, 130, 260, 130, 130, 130]
    vdata = {
        'schema': 'h3.creature.vcmi-animation-manifest.v2',
        'identity': identity,
        'faction': faction,
        'chinese_name': chinese_name,
        'action_archetype_id': choreography.get('action_archetype_id'),
        'action_family': choreography.get('action_family'),
        'concept': choreography.get('concept'),
        'total_keyframes': len(choreography.get('keyframes') or []),
        'keyframes': choreography.get('keyframes') or [],
        'playback_sequence_indices': seq,
        'frame_durations_ms': dur,
        'pipeline': dict(RIGID_PIPELINE_CONTRACT),
        'keyframe_sha256': {w['frame']: w['sha256'] for w in stabilize_receipt.get('written_frames', [])},
        'keyframe_registration': {w['frame']: w.get('registration') for w in stabilize_receipt.get('written_frames', [])},
        'preview_gif_sha256': (stabilize_receipt.get('preview_gif') or {}).get('sha256'),
        'preview_gif_size_bytes': (stabilize_receipt.get('preview_gif') or {}).get('size_bytes'),
    }
    jpath = unit_dir / f'{identity}.json'
    bk = safe_backup_existing(jpath, backup_dir, f'{identity}/{jpath.name}')
    if bk:
        backups.append(bk)
    jpath.write_text(json.dumps(vdata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    lines = [f'# {identity} {chinese_name} 动作编排', '',
             f'- 阵营：{faction}',
             f'- 动作原型：`{vdata["action_archetype_id"]}` {vdata["action_family"] or ""}',
             f'- 设计思路：{vdata["concept"] or ""}',
             '- 原则：原地不位移、整体比例不变；全身各部位协调联动；武器始终与持握的手一体运动；禁止点头/摇头式循环。',
             f'- 稳定器：`{STABILIZER_ID}`（仅刚性平移/单次缩放，不拼贴 0_0 像素、不改 Alpha、不抠图）',
             f'- 播放序列：`{seq}`，帧时长(ms)：`{dur}`', '',
             '| 帧 | 阶段 | 动作 |', '|---|---|---|']
    for kf in vdata['keyframes']:
        lines.append(f"| `{kf.get('frame')}` | {kf.get('phase', '')} | {kf.get('action', '')} |")
    mpath = unit_dir / f'{identity}_动作编排.md'
    bk = safe_backup_existing(mpath, backup_dir, f'{identity}/{mpath.name}')
    if bk:
        backups.append(bk)
    mpath.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'json': str(jpath), 'md': str(mpath), 'backups_moved': backups}


def build_hq_preview_apng(frames_rgba, out_apng_path: Path, frame_durations_ms=None):
    """Build full 32-bit RGBA animated PNG (APNG) preview with silky smooth alpha edges and zero color reduction."""
    if not frames_rgba:
        return None
    cleaned_frames = []
    for im in frames_rgba:
        arr = np.array(im.convert('RGBA'))
        # Clean rogue alpha noise (<= 2) to prevent any hidden matte fringing
        arr[arr[:, :, 3] <= 2] = [0, 0, 0, 0]
        cleaned_frames.append(Image.fromarray(arr, mode='RGBA'))

    if len(cleaned_frames) == 5:
        seq = [cleaned_frames[0], cleaned_frames[1], cleaned_frames[2], cleaned_frames[3], cleaned_frames[4], cleaned_frames[3], cleaned_frames[2], cleaned_frames[1]]
        durations = [500, 140, 140, 140, 280, 140, 140, 140]
    else:
        seq = cleaned_frames
        durations = frame_durations_ms or [140] * len(seq)

    out_apng_path.parent.mkdir(parents=True, exist_ok=True)
    seq[0].save(
        out_apng_path,
        format='PNG',
        save_all=True,
        append_images=seq[1:],
        duration=durations,
        loop=0,
    )
    return {
        'path': str(out_apng_path),
        'sha256': sha256_file(out_apng_path),
        'size_bytes': out_apng_path.stat().st_size,
        'frames': len(seq),
        'dimensions': list(seq[0].size),
        'format': 'APNG',
    }


def build_hq_preview_gif(frames_rgba, out_gif_path: Path, frame_durations_ms=None):
    """Backward-compatible fallback preview generator; prefers APNG if out_gif_path ends with .png."""
    if out_gif_path.suffix.lower() == '.png':
        return build_hq_preview_apng(frames_rgba, out_gif_path, frame_durations_ms)
    if not frames_rgba:
        return None
    w, h = frames_rgba[0].size
    scale = min(1.0, 560.0 / max(w, h))
    gw, gh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    bg_color = (26, 30, 38)
    gif_frames = []
    for im in frames_rgba:
        arr_raw = np.array(im.convert('RGBA'))
        arr_raw[arr_raw[:, :, 3] <= 2] = [0, 0, 0, 0]
        im_clean = Image.fromarray(arr_raw, mode='RGBA')
        im_s = im_clean.resize((gw, gh), Image.Resampling.LANCZOS)
        arr = np.array(im_s, dtype=np.float32)
        alpha = arr[:, :, 3:4] / 255.0
        bg = np.full((gh, gw, 3), bg_color, dtype=np.float32)
        comp = np.clip(np.round(arr[:, :, :3] * alpha + bg * (1.0 - alpha)), 0, 255).astype(np.uint8)
        gif_frames.append(Image.fromarray(comp, mode='RGB').quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG))

    if len(gif_frames) == 5:
        seq = [gif_frames[0], gif_frames[1], gif_frames[2], gif_frames[3], gif_frames[4], gif_frames[3], gif_frames[2], gif_frames[1]]
        durations = [450, 130, 130, 130, 260, 130, 130, 130]
    else:
        seq = gif_frames
        durations = frame_durations_ms or [150] * len(seq)

    out_gif_path.parent.mkdir(parents=True, exist_ok=True)
    seq[0].save(
        out_gif_path,
        format='GIF',
        save_all=True,
        append_images=seq[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    return {
        'path': str(out_gif_path),
        'sha256': sha256_file(out_gif_path),
        'size_bytes': out_gif_path.stat().st_size,
        'frames': len(seq),
        'dimensions': [gw, gh],
    }


def safe_backup_existing(target_path: Path, backup_root: Path, rel_key: str):
    """Move existing file to backup_root before writing, enforcing NO DIRECT OVERWRITE."""
    if not target_path.exists():
        return None
    if backup_root is None:
        raise ValueError(f'TARGET_EXISTS_BACKUP_DIR_REQUIRED:{target_path}')
    backup_root = Path(backup_root).resolve()
    art_trash_root = ART_TRASH_ROOT.resolve()
    if backup_root == art_trash_root or art_trash_root not in backup_root.parents:
        raise ValueError(f'ART_BACKUP_DIR_OUTSIDE_H3_ART_TRASH:{backup_root}')
    before_sha = sha256_file(target_path)
    dest = backup_root / rel_key
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        dest = dest.with_name(f'{stem}_{before_sha[:8]}{suf}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target_path), str(dest))
    return {
        'original_path': str(target_path),
        'backup_path': str(dest),
        'sha256': before_sha,
        'disposition': 'MOVED_NOT_DELETED',
    }


def inspect_unit_sequence_qa(unit_dir: Path, identity: str = None, expected_keyframes: int = 5,
                             lock_y_frac: float = 0.50, scale_tol: float = 0.03,
                             min_lower_iou: float = 0.60, max_lab_dist: float = 20.0,
                             novel_color_risk_fraction: float = None) -> dict:
    """Run all 3 mandatory local QA checks on a creature sequence folder."""
    unit_dir = Path(unit_dir).resolve()
    identity = identity or unit_dir.name
    errors = []
    risks = []

    expected_names = [f'0_{i}.png' for i in range(expected_keyframes)]
    existing_pngs = sorted(p.name for p in unit_dir.glob('*.png'))
    slot_pngs = [n for n in existing_pngs if SLOT_RE.match(n)]
    missing_frames = [n for n in expected_names if not (unit_dir / n).is_file()]
    unexpected_slot_pngs = [n for n in slot_pngs if n not in expected_names]
    extra_non_slot_pngs = [n for n in existing_pngs if n not in slot_pngs]

    if missing_frames:
        errors.append('MISSING_REQUIRED_KEYFRAMES:' + ','.join(missing_frames))
    if unexpected_slot_pngs:
        errors.append('UNEXPECTED_EXTRA_SLOT_PNGS:' + ','.join(unexpected_slot_pngs))
    if extra_non_slot_pngs:
        for name in extra_non_slot_pngs:
            if name.lower().startswith('avw_preview_'):
                continue  # APNG preview is valid, supported, and encouraged
            elif name in ('00.png', '01.png', '02.png', '03.png', '04.png'):
                errors.append('INVALID_LEGACY_OR_FAKE_PREVIEW_PNG:' + name)
            else:
                risks.append('EXTRA_NON_SLOT_PNG:' + name)

    vcmi_json_path = unit_dir / f'{identity}.json'
    choreo_md_path = unit_dir / f'{identity}_动作编排.md'
    preview_apng_path = unit_dir / f'avw_preview_{identity}.png'
    preview_gif_path = unit_dir / f'avw_preview_{identity}.gif'

    vcmi_check = {
        'json_exists': vcmi_json_path.is_file(),
        'md_exists': choreo_md_path.is_file(),
        'apng_exists': preview_apng_path.is_file(),
        'gif_exists': preview_gif_path.is_file()
    }
    if vcmi_json_path.is_file():
        try:
            vdata = json.loads(vcmi_json_path.read_text(encoding='utf-8'))
            total_1999 = int(vdata.get('total_1999_slots', 0))
            slot_map = vdata.get('slot_to_keyframe_map', {})
            seq_idx = vdata.get('playback_sequence_indices', [])
            vcmi_check.update(
                total_1999_slots=total_1999,
                mapped_slot_count=len(slot_map),
                playback_sequence_length=len(seq_idx),
                action_family=vdata.get('action_family'),
                action_archetype_id=vdata.get('action_archetype_id'),
            )
            if total_1999 > 0 and len(slot_map) != total_1999:
                errors.append(f'VCMI_SLOT_MAP_INCOMPLETE:{len(slot_map)}/{total_1999}')
            pipeline = vdata.get('pipeline') or {}
            vcmi_check['pipeline'] = pipeline
            if (pipeline.get('pixel_composite_from_f0') is not False or pipeline.get('alpha_modified') is not False
                    or pipeline.get('component_removal') is not False):
                errors.append('LEGACY_F0_PIXEL_COMPOSITE_OR_ALPHA_EDIT_PIPELINE:regenerate_with_' + STABILIZER_ID)
            kfs = vdata.get('keyframes') or []
            if not kfs:
                errors.append('CHOREOGRAPHY_KEYFRAME_ACTIONS_MISSING')
            for kf in kfs:
                hits = find_banned_head_motion(str(kf.get('action', '')) + ' ' + str(kf.get('phase', '')))
                if hits:
                    errors.append(f"BANNED_HEAD_NOD_OR_SHAKE_CHOREOGRAPHY:{kf.get('frame')}:{'/'.join(hits)}")
        except Exception as e:
            errors.append(f'VCMI_JSON_INVALID:{e}')
    else:
        risks.append('MISSING_VCMI_ANIMATION_JSON')

    if preview_apng_path.is_file():
        try:
            with Image.open(preview_apng_path) as aim:
                n_frames = getattr(aim, 'n_frames', 1)
                a_fmt = aim.format
            a_size = preview_apng_path.stat().st_size
            vcmi_check.update(preview_format=a_fmt, preview_frames=n_frames, preview_size_bytes=a_size)
            if a_fmt != 'PNG' or n_frames < 2:
                errors.append('PREVIEW_APNG_NOT_ANIMATED_PNG')
        except Exception as e:
            errors.append(f'PREVIEW_APNG_CORRUPT:{e}')
    elif preview_gif_path.is_file():
        try:
            with Image.open(preview_gif_path) as gim:
                n_frames = getattr(gim, 'n_frames', 1)
                g_fmt = gim.format
            g_size = preview_gif_path.stat().st_size
            vcmi_check.update(gif_format=g_fmt, gif_frames=n_frames, gif_size_bytes=g_size)
            if g_fmt != 'GIF' or n_frames < 2:
                errors.append('PREVIEW_GIF_NOT_ANIMATED_GIF')
        except Exception as e:
            errors.append(f'PREVIEW_GIF_CORRUPT:{e}')

    frame_reports = []
    loaded_images = {}
    for name in expected_names:
        fpath = unit_dir / name
        if not fpath.is_file():
            continue
        raw = fpath.read_bytes()
        f_sha = sha256_bytes(raw)
        im = Image.open(io.BytesIO(raw))
        im.load()
        f_err = []
        f_risk = []
        if im.mode != 'RGBA':
            f_err.append('RGBA_MODE_REQUIRED')
        rgba = im.convert('RGBA')
        arr = np.array(rgba)
        alpha = arr[:, :, 3]
        h, w = alpha.shape
        alpha0_cnt = int((alpha == 0).sum())
        if alpha0_cnt == 0:
            f_err.append('NO_TRUE_TRANSPARENT_BACKGROUND')
        corners = np.r_[
            alpha[:min(8, h), :min(8, w)].ravel(),
            alpha[:min(8, h), -min(8, w):].ravel(),
            alpha[-min(8, h):, :min(8, w)].ravel(),
            alpha[-min(8, h):, -min(8, w):].ravel(),
        ]
        corner_mean = float(corners.mean())
        if corner_mean > 2.0:
            f_err.append(f'NON_TRANSPARENT_CORNERS:{corner_mean:.2f}')

        core_op = interior_core_opacity_metrics(alpha)
        if core_op['status'] != 'SOLID_OPAQUE_CORE':
            f_err.append(core_op['status'])

        loaded_images[name] = rgba
        frame_reports.append({
            'frame': name,
            'sha256': f_sha,
            'pixels': [w, h],
            'mode': im.mode,
            'alpha0_pixels': alpha0_cnt,
            'corner_alpha_mean': round(corner_mean, 4),
            'interior_core_opacity': core_op,
            'errors': f_err,
            'risks': f_risk,
        })

    if '0_0.png' in loaded_images and len(loaded_images) >= 2:
        im0 = loaded_images['0_0.png']
        arr0 = np.array(im0)
        alpha0 = arr0[:, :, 3]
        vis0 = alpha0 >= 16
        core0 = get_core_body_mask_44px(im0)
        info0 = get_core_anchor_and_bbox(core0, alpha0)
        lab0 = cie_lab(arr0[core0, :3][::max(1, int(core0.sum()) // 50000)]).mean(axis=0) if np.any(core0) else np.zeros(3)

        ymin0, ymax0 = info0['bbox_ltrb'][1], info0['bbox_ltrb'][3] - 1
        split_y = int(ymin0 + (ymax0 - ymin0) * lock_y_frac)
        lower0 = core0.copy()
        lower0[:split_y, :] = False
        if int(lower0.sum()) < 50:
            lower0 = core0

        seen_shas = {frame_reports[0]['sha256']: '0_0.png'}
        for rep in frame_reports[1:]:
            fname = rep['frame']
            imi = loaded_images[fname]
            arri = np.array(imi)
            alphai = arri[:, :, 3]
            visi = alphai >= 16
            if rep['sha256'] in seen_shas:
                rep['errors'].append(f"DUPLICATE_KEYFRAME_SHA_WITH_{seen_shas[rep['sha256']]}")
            else:
                seen_shas[rep['sha256']] = fname

            if imi.size != im0.size:
                rep['errors'].append(f'CANVAS_SIZE_MISMATCH:{ list(imi.size) }_VS_{ list(im0.size) }')
                continue

            corei = get_core_body_mask_44px(imi)
            infoi = get_core_anchor_and_bbox(corei, alphai)
            if not infoi:
                rep['errors'].append('EMPTY_CORE_BODY_MASK')
                continue

            loweri = corei.copy()
            loweri[:split_y, :] = False
            if int(loweri.sum()) < 50:
                loweri = corei

            inter_low = int((lower0 & loweri).sum())
            u_low = int((lower0 | loweri).sum())
            min_low = min(int(lower0.sum()), int(loweri.sum()))
            iou_low = float(inter_low) / u_low if u_low > 0 else 0.0
            cont_low = float(inter_low) / min_low if min_low > 0 else 0.0
            eff_low = max(iou_low, 0.5 * iou_low + 0.5 * cont_low)
            inter_all = int((core0 & corei).sum())
            u_all = int((core0 | corei).sum())
            min_all = min(int(core0.sum()), int(corei.sum()))
            iou_all = float(inter_all) / u_all if u_all > 0 else 0.0
            cont_all = float(inter_all) / min_all if min_all > 0 else 0.0
            eff_all = max(iou_all, 0.5 * iou_all + 0.5 * cont_all)

            h_ratio = infoi['height'] / max(1.0, float(info0['height']))
            w_ratio = infoi['width'] / max(1.0, float(info0['width']))
            low_area_ratio = float(loweri.sum()) / max(1.0, float(lower0.sum()))
            dx_foot = round(float(infoi['x_foot'] - info0['x_foot']), 2)
            dy_foot = round(float(infoi['ymax'] - info0['ymax']), 2)

            labi = cie_lab(arri[corei, :3][::max(1, int(corei.sum()) // 50000)]).mean(axis=0) if np.any(corei) else np.zeros(3)
            lab_dist = float(np.linalg.norm(labi - lab0))
            novel = novel_color_regions(arr0, arri)
            if novel_color_risk_fraction is not None and novel['largest_cluster_fraction_of_fg'] >= novel_color_risk_fraction:
                rep['risks'].append(
                    f"NOVEL_COLOR_REGION_IDENTITY_DRIFT_RISK:frac={novel['largest_cluster_fraction_of_fg']}"
                    f",bbox={novel['largest_cluster_bbox_ltrb']}")

            comps_i, _ = component_regions(visi, max_runs=200000)
            detached_anomalies = []
            if len(comps_i) > 1:
                main_area = comps_i[0]['pixels']
                for comp in comps_i[1:]:
                    if comp['pixels'] >= 60:
                        bx0, by0, bx1, by1 = comp['bbox_ltrb']
                        sub_vis = visi[by0:by1, bx0:bx1]
                        overlap_f0 = float(vis0[by0:by1, bx0:bx1][sub_vis].mean()) if np.any(sub_vis) else 0.0
                        if overlap_f0 < 0.15:
                            detached_anomalies.append({
                                'pixels': comp['pixels'],
                                'bbox_ltrb': comp['bbox_ltrb'],
                                'overlap_with_f0': round(overlap_f0, 4),
                            })

            rep['sequence_metrics_vs_f0'] = {
                'core_height_ratio': round(h_ratio, 4),
                'core_width_ratio': round(w_ratio, 4),
                'lower_core_area_ratio': round(low_area_ratio, 4),
                'lower_core_iou': round(eff_low, 4),
                'lower_core_raw_iou': round(iou_low, 4),
                'lower_core_containment': round(cont_low, 4),
                'all_core_iou': round(eff_all, 4),
                'foot_anchor_drift_px': [dx_foot, dy_foot],
                'core_lab_distance': round(lab_dist, 2),
                'novel_color_regions': novel,
                'detached_floating_fragments': detached_anomalies,
            }

            scaled_up = (h_ratio > 1.25 and w_ratio > 1.25)
            scaled_down = (h_ratio < 0.75 and w_ratio < 0.75)
            if scaled_up or scaled_down or (abs(low_area_ratio - 1.0) > 0.45 and (abs(h_ratio - 1.0) > 0.22 and abs(w_ratio - 1.0) > 0.22)):
                rep['errors'].append(f'SCALE_INCONSISTENCY_ERROR:lower_area_ratio={low_area_ratio:.4f},h={h_ratio:.4f},w={w_ratio:.4f}')
            if eff_low < min_lower_iou and cont_low < 0.75:
                rep['errors'].append(f'SPATIAL_POSITION_DRIFT_ERROR:lower_core_iou={eff_low:.4f}')
            if (abs(dy_foot) > 28.0 and eff_low < 0.60 and cont_low < 0.70) or abs(dy_foot) > 64.0:
                rep['errors'].append(f'FOOT_ANCHOR_VERTICAL_DRIFT_ERROR:dy={dy_foot:.1f}px')
            if detached_anomalies:
                rep['errors'].append(f'DETACHED_FLOATING_FRAGMENT_OR_LIMB_ANOMALY:count={len(detached_anomalies)}')
            if eff_all < 0.48 or lab_dist > max_lab_dist:
                rep['errors'].append(f'SEVERE_CONTENT_OR_PALETTE_DRIFT_ERROR:all_iou={eff_all:.4f},lab={lab_dist:.1f}')

    for rep in frame_reports:
        for e in rep['errors']:
            errors.append(f"{rep['frame']}:{e}")
        for r in rep['risks']:
            risks.append(f"{rep['frame']}:{r}")

    status = 'PASS' if not errors and not risks else ('ERROR' if errors else 'RISK')
    return {
        'schema': QA_SCHEMA,
        'identity': identity,
        'unit_dir': str(unit_dir),
        'status': status,
        'error_count': len(errors),
        'risk_count': len(risks),
        'errors': errors,
        'risks': risks,
        'naming_and_1999_contract': {
            'expected_keyframes': expected_names,
            'found_slot_pngs': slot_pngs,
            'missing_frames': missing_frames,
            'unexpected_slot_pngs': unexpected_slot_pngs,
            'extra_non_slot_pngs': extra_non_slot_pngs,
            'vcmi_and_preview_contract': vcmi_check,
        },
        'frames': frame_reports,
    }


def stabilize_unit_sequence(raw_unit_dir: Path, target_unit_dir: Path, identity: str = None,
                            backup_dir: Path = None, expected_keyframes: int = 5,
                            lock_y_frac: float = 0.50) -> dict:
    """Stabilize a creature sequence from raw frames and run synchronous QA verification."""
    raw_unit_dir = Path(raw_unit_dir).resolve()
    target_unit_dir = Path(target_unit_dir).resolve()
    identity = identity or target_unit_dir.name
    backup_root = Path(backup_dir).resolve() if backup_dir else None

    f0_path = raw_unit_dir / '0_0.png'
    if not f0_path.is_file():
        raise ValueError(f'MISSING_RAW_FRAME_0:{f0_path}')

    im0 = Image.open(f0_path).convert('RGBA')
    stabilized_frames = [('0_0.png', im0, {'scale': 1.0, 'dx': 0, 'dy': 0, 'lower_core_iou': 1.0, 'all_core_iou': 1.0})]

    for idx in range(1, expected_keyframes):
        fname = f'0_{idx}.png'
        fpath = raw_unit_dir / fname
        if not fpath.is_file():
            raise ValueError(f'MISSING_RAW_FRAME:{fpath}')
        imi_raw = Image.open(fpath).convert('RGBA')
        imi_reg, reg_meta = register_frame_to_f0_core(im0, imi_raw, lock_y_frac=lock_y_frac)
        imi_clean = prune_isolated_floating_noise(imi_reg, im0)
        reg_meta['method'] = STABILIZER_ID
        stabilized_frames.append((fname, imi_clean, reg_meta))

    target_unit_dir.mkdir(parents=True, exist_ok=True)
    backups = []
    written = []
    for fname, im_out, reg_meta in stabilized_frames:
        out_path = target_unit_dir / fname
        bk = safe_backup_existing(out_path, backup_root, f'{identity}/{fname}')
        if bk:
            backups.append(bk)
        im_out.save(out_path, format='PNG')
        written.append({
            'frame': fname,
            'path': str(out_path),
            'sha256': sha256_file(out_path),
            'pixels': list(im_out.size),
            'registration': reg_meta,
        })

    apng_path = target_unit_dir / f'avw_preview_{identity}.png'
    bk_apng = safe_backup_existing(apng_path, backup_root, f'{identity}/{apng_path.name}')
    if bk_apng:
        backups.append(bk_apng)
    preview_info = build_hq_preview_apng([im for _, im, _ in stabilized_frames], apng_path)

    qa_report = inspect_unit_sequence_qa(target_unit_dir, identity=identity, expected_keyframes=expected_keyframes, lock_y_frac=lock_y_frac)
    return {
        'schema': STABILIZE_SCHEMA,
        'identity': identity,
        'raw_unit_dir': str(raw_unit_dir),
        'target_unit_dir': str(target_unit_dir),
        'pipeline': dict(RIGID_PIPELINE_CONTRACT),
        'backups_moved': backups,
        'written_frames': written,
        'preview_apng': preview_info,
        'qa_report': qa_report,
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('sequence-qa', 'sequence-stabilize', 'qa', 'stabilize'))
    parser.add_argument('--unit-dir', help='Single unit directory to inspect or target')
    parser.add_argument('--masters-root', help='Root directory containing multiple unit directories')
    parser.add_argument('--raw-root', help='Raw/pristine frames root (for stabilize)')
    parser.add_argument('--backup-dir', help='Backup directory under {VX-root}\\H3美术素材\\待删除/<unique-batch> before writing art frames')
    parser.add_argument('--identity', action='append', help='Specific unit identity name(s)')
    parser.add_argument('--expected-keyframes', type=int, default=5)
    parser.add_argument('--lock-y-frac', type=float, default=0.50)
    parser.add_argument('--report-out', help='Optional JSON file path to write full report')
    args = parser.parse_args(argv)

    try:
        is_qa = args.action in ('sequence-qa', 'qa')
        units = []
        if args.unit_dir:
            u = Path(args.unit_dir).resolve()
            ident = args.identity[0] if args.identity else u.name
            units.append((ident, u))
        elif args.masters_root:
            root = Path(args.masters_root).resolve()
            if args.identity:
                for ident in args.identity:
                    units.append((ident, root / ident))
            else:
                for child in sorted(root.iterdir()):
                    if child.is_dir() and any(SLOT_RE.match(p.name) for p in child.glob('*.png')):
                        units.append((child.name, child))
        else:
            raise ValueError('UNIT_DIR_OR_MASTERS_ROOT_REQUIRED')

        if is_qa:
            reports = [
                inspect_unit_sequence_qa(
                    u_dir, identity=ident,
                    expected_keyframes=args.expected_keyframes,
                    lock_y_frac=args.lock_y_frac,
                )
                for ident, u_dir in units
            ]
            overall = 'PASS' if all(r['status'] == 'PASS' for r in reports) else (
                'ERROR' if any(r['status'] == 'ERROR' for r in reports) else 'RISK'
            )
            result = {
                'schema': QA_SCHEMA,
                'overall': overall,
                'unit_count': len(reports),
                'pass_count': sum(r['status'] == 'PASS' for r in reports),
                'error_unit_count': sum(r['status'] == 'ERROR' for r in reports),
                'risk_unit_count': sum(r['status'] == 'RISK' for r in reports),
                'units': reports,
            }
        else:
            if not args.raw_root:
                raise ValueError('RAW_ROOT_REQUIRED_FOR_STABILIZE')
            raw_root = Path(args.raw_root).resolve()
            backup_dir = Path(args.backup_dir).resolve() if args.backup_dir else None
            receipts = []
            for ident, u_dir in units:
                r_dir = raw_root / ident if (raw_root / ident).is_dir() else raw_root
                receipts.append(
                    stabilize_unit_sequence(
                        r_dir, u_dir, identity=ident,
                        backup_dir=backup_dir,
                        expected_keyframes=args.expected_keyframes,
                        lock_y_frac=args.lock_y_frac,
                    )
                )
            overall = 'PASS' if all(r['qa_report']['status'] == 'PASS' for r in receipts) else 'ERROR'
            result = {
                'schema': STABILIZE_SCHEMA,
                'overall': overall,
                'unit_count': len(receipts),
                'units': receipts,
            }

        if args.report_out:
            out_p = Path(args.report_out).resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['overall'] == 'PASS' else 1
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'code': 'CREATURE_SEQUENCE_QA_CONTRACT', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
