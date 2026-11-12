#!/usr/bin/env python3
"""Deterministic 3X map/terrain/road/river/flag/UI/socket-sprite batch synthesizer."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np
from PIL import Image


SCHEMA = "h3.tier-export.map-synth-receipt.v1"
MANIFEST_SCHEMA = "h3.tier-export.map-synth-manifest.v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

# Standard Heroes III 8-player banner target colors in OKLCh:
# (Target Hue in radians, Target Chroma scale, Target Lightness offset)
H3_PLAYER_OKLCH: dict[str, tuple[float, float, float]] = {
    "red":    (math.radians(28.0),  0.195,  0.00),
    "blue":   (math.radians(258.0), 0.165, -0.02),
    "tan":    (math.radians(72.0),  0.085,  0.08),
    "green":  (math.radians(142.0), 0.155, -0.01),
    "orange": (math.radians(52.0),  0.185,  0.04),
    "purple": (math.radians(308.0), 0.165, -0.03),
    "teal":   (math.radians(196.0), 0.135,  0.01),
    "pink":   (math.radians(350.0), 0.150,  0.07),
}


def fail(code: str, message: str, **extra: Any) -> int:
    payload: dict[str, Any] = {
        "schema": "h3.tier-export.companion-error.v1",
        "status": "BLOCKED",
        "code": code,
        "error": message,
    }
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 2


def is_reparse_point(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()


def verify_pinned_file(path_str: str, expected_sha: str, role: str) -> tuple[Path, bytes]:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError(f"{role}_PATH_MISSING: empty path")
    if not isinstance(expected_sha, str) or not HEX64.match(expected_sha):
        raise ValueError(f"{role}_SHA256_INVALID: expected 64 lowercase hex chars")
    p = Path(path_str)
    if not p.is_absolute():
        raise ValueError(f"{role}_PATH_NOT_ABSOLUTE: {path_str}")
    for candidate in (p, *p.parents):
        if candidate.exists() and is_reparse_point(candidate):
            raise ValueError(f"{role}_REPARSE_BLOCKED: {candidate}")
    if not p.is_file():
        raise ValueError(f"{role}_FILE_MISSING: {p}")
    raw = p.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha:
        raise ValueError(f"{role}_SHA256_MISMATCH: expected {expected_sha}, got {actual} for {p}")
    return p, raw


def load_rgba_image(path_str: str, expected_sha: str, role: str) -> tuple[np.ndarray, Path]:
    p, _ = verify_pinned_file(path_str, expected_sha, role)
    with Image.open(p) as im:
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
    return rgba, p


def save_rgba_png(arr: np.ndarray, dest: Path) -> str:
    clamped = np.clip(np.rint(arr * 255.0), 0.0, 255.0).astype(np.uint8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and is_reparse_point(dest):
        raise ValueError(f"OUTPUT_REPARSE_BLOCKED: {dest}")
    im = Image.fromarray(clamped, mode="RGBA")
    im.save(dest, format="PNG", optimize=False)
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, np.power(np.clip((c + 0.055) / 1.055, 0.0, 1.0), 2.4))


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def srgb_to_oklab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lin = srgb_to_linear(rgb)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_ = np.cbrt(np.maximum(l, 0.0))
    m_ = np.cbrt(np.maximum(m, 0.0))
    s_ = np.cbrt(np.maximum(s, 0.0))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_out = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_out


def oklab_to_srgb(L: np.ndarray, a: np.ndarray, b_in: np.ndarray) -> np.ndarray:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b_in
    m_ = L - 0.1055613458 * a - 0.0638541728 * b_in
    s_ = L - 0.0894841775 * a - 1.2914855480 * b_in
    l = np.maximum(l_, 0.0) ** 3
    m = np.maximum(m_, 0.0) ** 3
    s = np.maximum(s_, 0.0) ** 3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    lin = np.stack([r, g, b], axis=-1)
    return linear_to_srgb(lin)


def bilinear_sample_toroidal(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x_mod = np.mod(x, w)
    y_mod = np.mod(y, h)
    x0 = np.floor(x_mod).astype(np.int32) % w
    y0 = np.floor(y_mod).astype(np.int32) % h
    x1 = (x0 + 1) % w
    y1 = (y0 + 1) % h
    wx = (x_mod - np.floor(x_mod))[..., None]
    wy = (y_mod - np.floor(y_mod))[..., None]
    c00 = img[y0, x0]
    c10 = img[y0, x1]
    c01 = img[y1, x0]
    c11 = img[y1, x1]
    return (c00 * (1.0 - wx) + c10 * wx) * (1.0 - wy) + (c01 * (1.0 - wx) + c11 * wx) * wy


def bilinear_sample_clamped(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x_c = np.clip(x, 0.0, float(w - 1))
    y_c = np.clip(y, 0.0, float(h - 1))
    x0 = np.floor(x_c).astype(np.int32)
    y0 = np.floor(y_c).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    wx = (x_c - x0)[..., None]
    wy = (y_c - y0)[..., None]
    c00 = img[y0, x0]
    c10 = img[y0, x1]
    c01 = img[y1, x0]
    c11 = img[y1, x1]
    return (c00 * (1.0 - wx) + c10 * wx) * (1.0 - wy) + (c01 * (1.0 - wx) + c11 * wx) * wy


def alpha_over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    sa = src[..., 3:4]
    da = dst[..., 3:4]
    out_a = sa + da * (1.0 - sa)
    safe_a = np.where(out_a > 1e-6, out_a, 1.0)
    out_rgb = (src[..., :3] * sa + dst[..., :3] * da * (1.0 - sa)) / safe_a
    out_rgb = np.where(out_a > 1e-6, out_rgb, 0.0)
    return np.concatenate([np.clip(out_rgb, 0.0, 1.0), np.clip(out_a, 0.0, 1.0)], axis=-1)


def run_terrain_synthesize(job: dict[str, Any], outdir: Path, write_files: bool) -> dict[str, Any]:
    job_id = str(job["job_id"])
    cw, ch = job.get("canvas_3x", [96, 96])
    if cw <= 0 or ch <= 0 or cw % 3 != 0 or ch % 3 != 0:
        raise ValueError(f"TERRAIN_CANVAS_INVALID: {cw}x{ch} must be positive multiples of 3")
    base_rgba, _ = load_rgba_image(job["base_texture"]["path"], job["base_texture"]["sha256"], "TERRAIN_BASE")
    ox, oy = job.get("tile_offset_3x", [0, 0])
    yy, xx = np.meshgrid(np.arange(ch, dtype=np.float32) + oy, np.arange(cw, dtype=np.float32) + ox, indexing="ij")
    canvas = bilinear_sample_toroidal(base_rgba, xx, yy)

    if "overlay_texture" in job and job["overlay_texture"] is not None:
        over_rgba, _ = load_rgba_image(job["overlay_texture"]["path"], job["overlay_texture"]["sha256"], "TERRAIN_OVERLAY")
        if "transition_mask" not in job or job["transition_mask"] is None:
            raise ValueError(f"TERRAIN_MASK_REQUIRED: overlay_texture requires transition_mask in {job_id}")
        mask_rgba, _ = load_rgba_image(job["transition_mask"]["path"], job["transition_mask"]["sha256"], "TERRAIN_MASK")
        if mask_rgba.shape[1] != cw or mask_rgba.shape[0] != ch:
            mx = np.linspace(0.0, float(mask_rgba.shape[1] - 1), cw, dtype=np.float32)
            my = np.linspace(0.0, float(mask_rgba.shape[0] - 1), ch, dtype=np.float32)
            gy, gx = np.meshgrid(my, mx, indexing="ij")
            mask_resized = bilinear_sample_clamped(mask_rgba, gx, gy)
        else:
            mask_resized = mask_rgba
        mask_val = mask_resized[..., 0] * mask_resized[..., 3]
        over_sampled = bilinear_sample_toroidal(over_rgba, xx, yy)
        # Height-field modulated splatting so stone/grass transitions follow relief crevices
        base_lum = 0.2126 * canvas[..., 0] + 0.7152 * canvas[..., 1] + 0.0722 * canvas[..., 2]
        over_lum = 0.2126 * over_sampled[..., 0] + 0.7152 * over_sampled[..., 1] + 0.0722 * over_sampled[..., 2]
        sharpness = float(job.get("splat_sharpness", 3.5))
        height_diff = (over_lum - base_lum) * 0.35
        weight = np.clip((mask_val + height_diff - 0.5) * sharpness + 0.5, 0.0, 1.0)[..., None]
        canvas[..., :3] = canvas[..., :3] * (1.0 - weight) + over_sampled[..., :3] * weight
        canvas[..., 3:4] = np.maximum(canvas[..., 3:4], over_sampled[..., 3:4] * weight)

    if "cutout_mask" in job and job["cutout_mask"] is not None:
        cut_rgba, _ = load_rgba_image(job["cutout_mask"]["path"], job["cutout_mask"]["sha256"], "TERRAIN_CUTOUT")
        if cut_rgba.shape[1] != cw or cut_rgba.shape[0] != ch:
            cx = np.linspace(0.0, float(cut_rgba.shape[1] - 1), cw, dtype=np.float32)
            cy = np.linspace(0.0, float(cut_rgba.shape[0] - 1), ch, dtype=np.float32)
            gy, gx = np.meshgrid(cy, cx, indexing="ij")
            cut_rgba = bilinear_sample_clamped(cut_rgba, gx, gy)
        canvas[..., 3] *= cut_rgba[..., 3]
        canvas[..., :3] = np.where(canvas[..., 3:4] > 1e-6, canvas[..., :3], 0.0)

    for idx, decal in enumerate(job.get("decal_layers", [])):
        dec_rgba, _ = load_rgba_image(decal["path"], decal["sha256"], f"TERRAIN_DECAL_{idx}")
        dx, dy = decal.get("origin_3x", [0, 0])
        dh, dw = dec_rgba.shape[:2]
        x0, y0 = max(0, dx), max(0, dy)
        x1, y1 = min(cw, dx + dw), min(ch, dy + dh)
        if x1 > x0 and y1 > y0:
            sub_dst = canvas[y0:y1, x0:x1]
            sub_src = dec_rgba[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
            canvas[y0:y1, x0:x1] = alpha_over(sub_dst, sub_src)

    rel_out = job.get("output_relpath", f"{job_id}.png")
    dest = outdir / rel_out
    sha = save_rgba_png(canvas, dest) if write_files else "0" * 64
    return {
        "job_id": job_id,
        "type": "terrain-synthesize",
        "outputs": [{"path": str(dest), "relpath": rel_out, "width": cw, "height": ch, "sha256": sha}],
    }


def run_road_river_synth(job: dict[str, Any], outdir: Path, write_files: bool) -> dict[str, Any]:
    job_id = str(job["job_id"])
    strip_rgba, _ = load_rgba_image(job["source_strip"]["path"], job["source_strip"]["sha256"], "ROAD_RIVER_SOURCE")
    transform = job.get("transform", "identity")
    base = strip_rgba.copy()
    if transform == "flip_h":
        base = base[:, ::-1, :]
    elif transform == "flip_v":
        base = base[::-1, :, :]
    elif transform == "rot90":
        base = np.rot90(base, 1)
    elif transform == "rot180":
        base = np.rot90(base, 2)
    elif transform == "rot270":
        base = np.rot90(base, 3)
    elif transform == "curve_bend":
        h, w = base.shape[:2]
        yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
        nx = (xx / max(1.0, float(w - 1))) - 0.5
        ny = (yy / max(1.0, float(h - 1))) - 0.5
        r = np.sqrt(nx * nx + ny * ny) * float(w)
        theta = np.arctan2(ny, nx)
        u = np.clip((theta / (0.5 * math.pi)) * w, 0.0, float(w - 1))
        v = np.clip(r, 0.0, float(h - 1))
        base = bilinear_sample_clamped(base, u, v)
    elif transform != "identity":
        raise ValueError(f"ROAD_RIVER_TRANSFORM_UNKNOWN: {transform}")

    h, w = base.shape[:2]
    flow_frames = int(job.get("flow_frames", 1))
    if flow_frames < 1 or flow_frames > 64:
        raise ValueError(f"ROAD_RIVER_FLOW_FRAMES_INVALID: {flow_frames}")
    amp = float(job.get("flow_amplitude_px", 2.5))
    vx, vy = job.get("flow_direction", [1.0, 0.0])

    bank_mask = np.ones((h, w), dtype=np.float32)
    if "flow_mask" in job and job["flow_mask"] is not None:
        m_rgba, _ = load_rgba_image(job["flow_mask"]["path"], job["flow_mask"]["sha256"], "ROAD_RIVER_MASK")
        bank_mask = m_rgba[..., 0] * m_rgba[..., 3]
    else:
        # Automatically pin outer 2px border if alpha < 1 at edges so tile seams never tear
        yy_idx, xx_idx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
        edge_dist = np.minimum(np.minimum(xx_idx, w - 1.0 - xx_idx), np.minimum(yy_idx, h - 1.0 - yy_idx))
        bank_mask = np.clip(edge_dist / 6.0, 0.0, 1.0)

    outputs = []
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    for f_idx in range(flow_frames):
        if flow_frames == 1:
            frame = base
            rel_out = job.get("output_relpath", f"{job_id}.png")
        else:
            phase = (2.0 * math.pi * f_idx) / float(flow_frames)
            # Dual-harmonic cyclic flow field: strictly periodic over f_idx in 0..flow_frames-1
            dx = amp * bank_mask * (
                vx * math.sin(phase) * np.cos(2.0 * math.pi * yy / float(h))
                + 0.5 * math.cos(phase) * np.sin(4.0 * math.pi * xx / float(w))
            )
            dy = amp * bank_mask * (
                vy * math.sin(phase) * np.cos(2.0 * math.pi * xx / float(w))
                + 0.5 * math.cos(phase) * np.sin(4.0 * math.pi * yy / float(h))
            )
            warped = bilinear_sample_toroidal(base, xx - dx, yy - dy)
            # Subtle specular ripple modulation along flow crest
            ripple = 1.0 + 0.045 * bank_mask * np.sin(phase + 2.0 * math.pi * (xx * vx + yy * vy) / float(max(w, h)))
            frame = warped.copy()
            frame[..., :3] = np.clip(warped[..., :3] * ripple[..., None], 0.0, 1.0)
            frame[..., 3] = base[..., 3]
            rel_out = f"{job_id}_f{f_idx:02d}.png"
        dest = outdir / rel_out
        sha = save_rgba_png(frame, dest) if write_files else "0" * 64
        outputs.append({"path": str(dest), "relpath": rel_out, "frame_index": f_idx, "width": w, "height": h, "sha256": sha})

    return {"job_id": job_id, "type": "road-river-synth", "outputs": outputs}


def recolor_oklch(rgba: np.ndarray, mask: np.ndarray, target_hue: float, target_chroma: float, l_offset: float) -> np.ndarray:
    L, a, b = srgb_to_oklab(rgba[..., :3])
    C = np.sqrt(a * a + b * b)
    # Preserve specular highlights (high L, low C) and deep shadows while rotating cloth hue in OKLCh
    highlight_guard = np.clip((0.92 - L) / 0.25, 0.15, 1.0)
    new_L = np.clip(L + l_offset * mask * highlight_guard, 0.0, 1.0)
    # Modulate chroma proportionally to source local shading so folds and fabric sheen remain 3D
    shading_envelope = np.clip(4.0 * new_L * (1.0 - new_L), 0.20, 1.0)
    new_C = target_chroma * shading_envelope * highlight_guard + C * 0.22
    new_a = new_C * math.cos(target_hue)
    new_b = new_C * math.sin(target_hue)
    blend = (mask * (rgba[..., 3] > 1e-4))[..., None]
    recolored_rgb = oklab_to_srgb(new_L, new_a, new_b)
    out_rgb = rgba[..., :3] * (1.0 - blend) + recolored_rgb * blend
    return np.concatenate([np.clip(out_rgb, 0.0, 1.0), rgba[..., 3:4]], axis=-1)


def apply_cloth_wind_wave(rgba: np.ndarray, frame_idx: int, total_frames: int, pole_x: int, max_amp_px: float) -> np.ndarray:
    if total_frames <= 1 or max_amp_px <= 0.0:
        return rgba
    h, w = rgba.shape[:2]
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    # Distance from flagpole normalized to [0, 1]; pole column (xx == pole_x) has strictly 0 displacement
    span = max(1.0, float(max(pole_x, w - 1 - pole_x)))
    dist = np.abs(xx - float(pole_x)) / span
    env = np.clip(dist ** 1.35, 0.0, 1.0)
    phase = (2.0 * math.pi * frame_idx) / float(total_frames)
    k = 2.0 * math.pi * 1.35 / span
    wave_arg = k * (xx - float(pole_x)) - phase
    dy = max_amp_px * env * np.sin(wave_arg)
    dx = -0.25 * max_amp_px * env * np.cos(wave_arg)
    warped = bilinear_sample_clamped(rgba, xx - dx, yy - dy)
    # Normal-derivative cloth lighting: wave slope d(dy)/dx modulates fabric highlight and trough shadow
    slope = env * np.cos(wave_arg)
    shade = 1.0 + 0.14 * slope[..., None]
    warped[..., :3] = np.clip(warped[..., :3] * shade, 0.0, 1.0)
    warped[..., :3] = np.where(warped[..., 3:4] > 1e-6, warped[..., :3], 0.0)
    return warped


def run_flag_batch_synth(job: dict[str, Any], outdir: Path, write_files: bool) -> dict[str, Any]:
    job_id = str(job["job_id"])
    flag_rgba, _ = load_rgba_image(job["master_flag"]["path"], job["master_flag"]["sha256"], "FLAG_MASTER")
    h, w = flag_rgba.shape[:2]
    if "color_mask" in job and job["color_mask"] is not None:
        m_rgba, _ = load_rgba_image(job["color_mask"]["path"], job["color_mask"]["sha256"], "FLAG_COLOR_MASK")
        mask = m_rgba[..., 0] * m_rgba[..., 3]
    else:
        # Default: recolor chromatic pixels (chroma > 0.04) so neutral silver/wooden poles stay untouched
        _, a, b = srgb_to_oklab(flag_rgba[..., :3])
        chroma = np.sqrt(a * a + b * b)
        mask = np.clip((chroma - 0.035) / 0.045, 0.0, 1.0)

    players = job.get("player_colors", list(H3_PLAYER_OKLCH.keys()))
    wave_frames = int(job.get("wave_frames", 1))
    pole_x = int(job.get("pole_anchor_x", 0))
    wave_amp = float(job.get("wave_amplitude_px", 3.0))

    outputs = []
    for color_name in players:
        if color_name not in H3_PLAYER_OKLCH:
            raise ValueError(f"FLAG_PLAYER_COLOR_UNKNOWN: {color_name}")
        th, tc, lo = H3_PLAYER_OKLCH[color_name]
        colored = recolor_oklch(flag_rgba, mask, th, tc, lo)
        for f_idx in range(wave_frames):
            waved = apply_cloth_wind_wave(colored, f_idx, wave_frames, pole_x, wave_amp)
            rel_out = f"{job_id}_{color_name}_f{f_idx:02d}.png" if wave_frames > 1 else f"{job_id}_{color_name}.png"
            dest = outdir / rel_out
            sha = save_rgba_png(waved, dest) if write_files else "0" * 64
            outputs.append({
                "path": str(dest),
                "relpath": rel_out,
                "player_color": color_name,
                "frame_index": f_idx,
                "width": w,
                "height": h,
                "sha256": sha,
            })
    return {"job_id": job_id, "type": "flag-batch-synth", "outputs": outputs}


def nine_slice_expand(rgba: np.ndarray, target_w: int, target_h: int, corner: int) -> np.ndarray:
    h, w = rgba.shape[:2]
    if target_w == w and target_h == h:
        return rgba.copy()
    if corner * 2 >= min(w, h, target_w, target_h):
        raise ValueError(f"UI_NINE_SLICE_CORNER_INVALID: corner={corner} for {w}x{h} -> {target_w}x{target_h}")
    out = np.zeros((target_h, target_w, 4), dtype=np.float32)
    # 4 corners
    out[:corner, :corner] = rgba[:corner, :corner]
    out[:corner, -corner:] = rgba[:corner, -corner:]
    out[-corner:, :corner] = rgba[-corner:, :corner]
    out[-corner:, -corner:] = rgba[-corner:, -corner:]
    # Top/bottom edges tiled toroidally
    mid_src_w = w - 2 * corner
    mid_dst_w = target_w - 2 * corner
    for x in range(mid_dst_w):
        sx = corner + (x % mid_src_w)
        out[:corner, corner + x] = rgba[:corner, sx]
        out[-corner:, corner + x] = rgba[-corner:, sx]
    # Left/right edges and interior tiled toroidally
    mid_src_h = h - 2 * corner
    mid_dst_h = target_h - 2 * corner
    for y in range(mid_dst_h):
        sy = corner + (y % mid_src_h)
        out[corner + y, :corner] = rgba[sy, :corner]
        out[corner + y, -corner:] = rgba[sy, -corner:]
        for x in range(mid_dst_w):
            sx = corner + (x % mid_src_w)
            out[corner + y, corner + x] = rgba[sy, sx]
    return out


def run_ui_state_synth(job: dict[str, Any], outdir: Path, write_files: bool) -> dict[str, Any]:
    job_id = str(job["job_id"])
    btn_rgba, _ = load_rgba_image(job["normal_button"]["path"], job["normal_button"]["sha256"], "UI_NORMAL_BUTTON")
    if "nine_slice_target" in job and job["nine_slice_target"] is not None:
        ns = job["nine_slice_target"]
        btn_rgba = nine_slice_expand(btn_rgba, int(ns["width_3x"]), int(ns["height_3x"]), int(ns.get("corner_px", 12)))
    h, w = btn_rgba.shape[:2]
    margin = int(job.get("bevel_margin_px", 6))
    shift = int(job.get("pressed_shift_px", 3))
    states = job.get("states", ["normal", "pressed", "disabled", "hover"])

    outputs = []
    for state in states:
        if state == "normal":
            out = btn_rgba.copy()
        elif state == "pressed":
            out = btn_rgba.copy()
            if h > 2 * margin + shift and w > 2 * margin + shift:
                interior = btn_rgba[margin:h - margin - shift, margin:w - margin - shift, :3].copy()
                out[margin + shift:h - margin, margin + shift:w - margin, :3] = interior * 0.92
                # Cast inner shadow along top and left bevel edges
                out[margin:margin + shift, margin:w - margin, :3] *= 0.48
                out[margin:h - margin, margin:margin + shift, :3] *= 0.48
        elif state == "disabled":
            out = btn_rgba.copy()
            L, a, b = srgb_to_oklab(out[..., :3])
            L_dis = np.clip(0.65 * L + 0.14, 0.0, 1.0)
            out[..., :3] = oklab_to_srgb(L_dis, a * 0.18, b * 0.18)
            out[..., :3] = np.where(out[..., 3:4] > 1e-6, out[..., :3], 0.0)
        elif state == "hover":
            out = btn_rgba.copy()
            L, a, b = srgb_to_oklab(out[..., :3])
            yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
            edge_dist = np.minimum(np.minimum(xx, w - 1.0 - xx), np.minimum(yy, h - 1.0 - yy))
            rim = np.exp(-((edge_dist - float(margin)) ** 2) / (2.0 * (float(max(2, margin // 2)) ** 2)))
            L_hov = np.clip(L + 0.055 + 0.08 * rim, 0.0, 1.0)
            out[..., :3] = oklab_to_srgb(L_hov, a + 0.01 * rim, b + 0.035 * rim)
            out[..., :3] = np.where(out[..., 3:4] > 1e-6, out[..., :3], 0.0)
        else:
            raise ValueError(f"UI_STATE_UNKNOWN: {state}")

        rel_out = f"{job_id}_{state}.png"
        dest = outdir / rel_out
        sha = save_rgba_png(out, dest) if write_files else "0" * 64
        outputs.append({"path": str(dest), "relpath": rel_out, "state": state, "width": w, "height": h, "sha256": sha})
    return {"job_id": job_id, "type": "ui-state-synth", "outputs": outputs}


def run_layered_sprite_compose(job: dict[str, Any], outdir: Path, write_files: bool) -> dict[str, Any]:
    job_id = str(job["job_id"])
    cw, ch = job["canvas_3x"]
    if cw <= 0 or ch <= 0 or cw % 3 != 0 or ch % 3 != 0:
        raise ValueError(f"LAYERED_CANVAS_INVALID: {cw}x{ch} must be positive multiples of 3")
    layers = list(job.get("layers", []))
    if not layers:
        raise ValueError(f"LAYERED_EMPTY: {job_id} has no layers")
    layers_sorted = sorted(enumerate(layers), key=lambda item: (int(item[1].get("z_order", 0)), item[0]))
    canvas = np.zeros((ch, cw, 4), dtype=np.float32)

    for idx, layer in layers_sorted:
        l_rgba, _ = load_rgba_image(layer["path"], layer["sha256"], f"LAYER_{idx}")
        ox, oy = layer.get("origin_3x", [0, 0])
        sx, sy = layer.get("socket_offset_3x", [0, 0])
        px, py = int(ox) + int(sx), int(oy) + int(sy)
        lh, lw = l_rgba.shape[:2]
        if px < 0 or py < 0 or px + lw > cw or py + lh > ch:
            raise ValueError(f"LAYER_OUT_OF_CANVAS: layer {idx} at ({px},{py}) size {lw}x{lh} exceeds {cw}x{ch}")
        opacity = float(layer.get("opacity", 1.0))
        if not (0.0 < opacity <= 1.0):
            raise ValueError(f"LAYER_OPACITY_INVALID: {opacity}")
        src = l_rgba.copy()
        src[..., 3] *= opacity
        sub = canvas[py:py + lh, px:px + lw]
        canvas[py:py + lh, px:px + lw] = alpha_over(sub, src)

    if not np.any(canvas[..., 3] > 1e-4):
        raise ValueError(f"LAYERED_RESULT_EMPTY_ALPHA: {job_id}")
    rel_out = job.get("output_relpath", f"{job_id}.png")
    dest = outdir / rel_out
    sha = save_rgba_png(canvas, dest) if write_files else "0" * 64
    return {
        "job_id": job_id,
        "type": "layered-sprite-compose",
        "outputs": [{"path": str(dest), "relpath": rel_out, "width": cw, "height": ch, "sha256": sha}],
    }


DISPATCH = {
    "terrain-synthesize": run_terrain_synthesize,
    "road-river-synth": run_road_river_synth,
    "flag-batch-synth": run_flag_batch_synth,
    "ui-state-synth": run_ui_state_synth,
    "layered-sprite-compose": run_layered_sprite_compose,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="map_batch_synthesizer.py",
        description="Deterministic 3X terrain/road/river/flag/UI/socket-sprite batch synthesizer.",
    )
    parser.add_argument("mode", choices=("check", "run", "verify"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--renderer-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if not OP_ID_RE.match(args.operation_id):
            return fail("OPERATION_ID_INVALID", f"Invalid operation-id: {args.operation_id}")
        outdir = Path(args.outdir)
        if not outdir.is_absolute():
            return fail("OUTDIR_NOT_ABSOLUTE", f"--outdir must be absolute: {outdir}")
        if outdir.name != args.operation_id:
            return fail("OUTDIR_OPERATION_ID_MISMATCH", f"--outdir leaf {outdir.name} must equal --operation-id {args.operation_id}")
        for candidate in (outdir, *outdir.parents):
            if candidate.exists() and is_reparse_point(candidate):
                return fail("OUTDIR_REPARSE_BLOCKED", f"Reparse point blocked: {candidate}")

        renderer_path, _ = verify_pinned_file(args.renderer, args.renderer_sha256, "RENDERER")
        manifest_path, manifest_raw = verify_pinned_file(args.manifest, args.manifest_sha256, "MANIFEST")
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if manifest.get("schema") != MANIFEST_SCHEMA:
            return fail("MANIFEST_SCHEMA_INVALID", f"Expected schema {MANIFEST_SCHEMA}")
        if manifest.get("operation_id") != args.operation_id:
            return fail("MANIFEST_OPERATION_ID_MISMATCH", "Manifest operation_id does not match --operation-id")
        jobs = manifest.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            return fail("MANIFEST_JOBS_EMPTY", "Manifest requires a non-empty jobs list")

        receipt_path = outdir / "map-synth-receipt.json"
        if args.mode == "verify":
            if not receipt_path.is_file():
                return fail("RECEIPT_MISSING", f"Receipt missing: {receipt_path}")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("schema") != SCHEMA or receipt.get("manifest_sha256") != args.manifest_sha256:
                return fail("RECEIPT_DRIFT", "Receipt schema or manifest_sha256 mismatch")
            if receipt.get("renderer_sha256") != args.renderer_sha256:
                return fail("RECEIPT_RENDERER_MISMATCH", "Receipt renderer_sha256 mismatch")
            for job_row in receipt.get("jobs", []):
                for out_row in job_row.get("outputs", []):
                    p, _ = verify_pinned_file(out_row["path"], out_row["sha256"], "OUTPUT")
                    with Image.open(p) as im:
                        if im.size != (out_row["width"], out_row["height"]):
                            return fail("OUTPUT_DIMENSION_MISMATCH", f"Size mismatch for {p}")
            print(json.dumps({"schema": SCHEMA, "mode": "verify", "status": "PASSED", "operation_id": args.operation_id, "receipt": str(receipt_path)}))
            return 0

        write_files = args.mode == "run"
        job_results = []
        seen_ids: set[str] = set()
        for job in jobs:
            jid = str(job.get("job_id", "")).strip()
            if not OP_ID_RE.match(jid) or jid in seen_ids:
                return fail("JOB_ID_INVALID_OR_DUPLICATE", f"Invalid or duplicate job_id: {jid}")
            seen_ids.add(jid)
            jtype = str(job.get("type", ""))
            if jtype not in DISPATCH:
                return fail("JOB_TYPE_UNKNOWN", f"Unsupported job type: {jtype}")
            job_results.append(DISPATCH[jtype](job, outdir, write_files))

        receipt_payload = {
            "schema": SCHEMA,
            "operation_id": args.operation_id,
            "mode": args.mode,
            "status": "PASSED",
            "manifest": str(manifest_path),
            "manifest_sha256": args.manifest_sha256,
            "renderer": str(renderer_path),
            "renderer_sha256": args.renderer_sha256,
            "jobs": job_results,
            "publish_scale": 3,
            "automatic_art_approval": False,
            "game_deployment": False,
        }
        if write_files:
            outdir.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt_payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        return fail("MAP_SYNTH_FAILED", str(exc))


if __name__ == "__main__":
    sys.exit(main())
