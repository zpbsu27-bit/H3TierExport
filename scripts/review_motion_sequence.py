"""Read-only visual QA for motion runs and frozen Source selections.

The motion engine or the bound Source owner owns geometry and pixels. This
companion writes an HTML/JSON review only; it never produces or admits artwork.
Diagnostics are review hints, never art approval or native timing.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import base64
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote
import review_native_batch as native

import numpy as np
from PIL import Image


AUTHORITY_FLAGS = (
    "model_inference_performed", "model_native_output", "human_approved",
    "art_approved", "game_deployed", "runtime_accepted", "native_timing_verified",
)
GOOD_RESULTS = {"DERIVED_VERIFIED_REVIEW_REQUIRED", "REUSED_VERIFIED", "VERIFIED"}
SOURCE_COLLECTION_SCHEMA = "h3.motion.source-review-collection.v1"
SOURCE_GEOMETRY_AUTHORITY = "owner-source-placement"
SOURCE_LIMITS = {
    "slots": 512,
    "json_file_bytes": 32 * 1024 * 1024,
    "json_total_bytes": 64 * 1024 * 1024,
    "png_total_bytes": 64 * 1024 * 1024,
    "decoded_pixels": 64 * 1024 * 1024,
    "image_pixels": 16 * 1024 * 1024,
    "canvas_pixels": 16 * 1024 * 1024,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"JSON exceeds 64 MiB: {path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def pair(value: object, name: str, minimum: int = 0) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or any(
        type(x) is not int or x < minimum for x in value
    ):
        raise ValueError(f"invalid {name}")
    return value[0], value[1]


def alpha_box(mask: np.ndarray) -> list[int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        return None
    columns = np.flatnonzero(mask.any(axis=0))
    return [int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1]


def component_regions(mask: np.ndarray, max_runs: int = 200_000, *,
                      alpha1_mask: np.ndarray | None = None) -> tuple[list[dict], bool]:
    """Count 8-connected foreground runs; bound pathological noisy inputs."""
    if alpha1_mask is not None and (alpha1_mask.shape != mask.shape or alpha1_mask.dtype != bool):
        raise ValueError('COMPONENT_ALPHA1_MASK_SHAPE_OR_TYPE')
    parents = [0]
    areas = [0]
    boxes = [[0, 0, 0, 0]]
    alpha1_counts = [0] if alpha1_mask is not None else None

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    previous: list[tuple[int, int, int]] = []
    for y, row in enumerate(mask):
        changes = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        current = []
        probe = 0
        for start, end in zip(starts, ends):
            if len(parents) > max_runs:
                return [], True
            start, end = int(start), int(end)
            index = len(parents)
            parents.append(index)
            areas.append(end - start)
            boxes.append([start, y, end, y + 1])
            if alpha1_counts is not None:
                alpha1_counts.append(int(np.count_nonzero(alpha1_mask[y, start:end])))
            while probe < len(previous) and previous[probe][1] < start:
                probe += 1
            neighbor = probe
            while neighbor < len(previous) and previous[neighbor][0] <= end:
                other = root(previous[neighbor][2])
                own = root(index)
                if own != other:
                    parents[other] = own
                neighbor += 1
            current.append((start, end, index))
        previous = current
    totals: dict[int, dict] = {}
    for index in range(1, len(parents)):
        owner = root(index)
        box = boxes[index]
        if owner not in totals:
            totals[owner] = {'pixels': 0, 'bbox_ltrb': list(box)}
        totals[owner]['pixels'] += areas[index]
        if alpha1_counts is not None:
            totals[owner]['alpha1_pixels'] = totals[owner].get('alpha1_pixels', 0) + alpha1_counts[index]
        bounds = totals[owner]['bbox_ltrb']
        bounds[:] = [min(bounds[0], box[0]), min(bounds[1], box[1]),
                     max(bounds[2], box[2]), max(bounds[3], box[3])]
    return sorted(totals.values(), key=lambda v: (-v['pixels'], v['bbox_ltrb'])), False


def component_sizes(mask: np.ndarray, max_runs: int = 200_000) -> tuple[list[int], bool]:
    regions, truncated = component_regions(mask, max_runs)
    return [region['pixels'] for region in regions], truncated


def source_shape_diagnostics(alpha: np.ndarray, geometry: dict, native_alpha=None,
                             natural_k: int | None = None, *, full_native_view=False) -> dict:
    """Bounded source measurements; thresholded envelopes are not body semantics."""
    if alpha.ndim != 2 or alpha.size > 64_000_000 or alpha.dtype != np.uint8:
        raise ValueError('SOURCE_DIAGNOSTIC_ALPHA_LIMIT_OR_TYPE')
    positive = alpha_box(alpha > 0)
    body = alpha_box(alpha >= 128)
    regions, truncated = component_regions(alpha >= 128)
    ge2 = alpha >= 2
    ge2_regions, ge2_truncated = component_regions(ge2)
    ge2_proxy = {
        'threshold': 2, 'pixels': int(np.count_nonzero(ge2)), 'bbox_ltrb': alpha_box(ge2),
        'component_count': None if ge2_truncated else len(ge2_regions),
        'largest_components': None if ge2_truncated else ge2_regions[:16],
        'component_scan_truncated': ge2_truncated,
        'component_output_truncated': None if ge2_truncated else len(ge2_regions) > 16,
        'measurement_scope': 'Source alpha>=2 support only; not semantic body segmentation or a FIT/contact decision.',
    }
    del ge2
    alpha1_mask = alpha == 1
    positive_regions, positive_truncated = component_regions(alpha > 0, alpha1_mask=alpha1_mask)
    connected = sum(r['alpha1_pixels'] for r in positive_regions if r['pixels'] > r['alpha1_pixels'])
    isolated = [r for r in positive_regions if r['pixels'] == r['alpha1_pixels']]
    alpha1_topology = {
        'connectivity': 8,
        'connected_to_alpha_ge2_component_pixels': None if positive_truncated else connected,
        'alpha1_only_component_pixels': None if positive_truncated else sum(r['pixels'] for r in isolated),
        'alpha1_only_component_count': None if positive_truncated else len(isolated),
        'component_scan_truncated': positive_truncated,
        'measurement_scope': 'Alpha1 within alpha>0 components containing alpha>=2 versus alpha1-only components; no pixels are excluded.',
    }
    native = alpha_box(native_alpha >= 128) if native_alpha is not None else None
    crop = geometry['crop']
    aspect = lambda box: (box[2] - box[0]) / (box[3] - box[1]) if box else None
    minimum_k = max((positive[2] - positive[0] + crop[0] - 1) // crop[0],
                    (positive[3] - positive[1] + crop[1] - 1) // crop[1]) if positive else None
    axis_scale = [(body[2] - body[0]) / (native[2] - native[0]),
                  (body[3] - body[1]) / (native[3] - native[1])] if body and native else None
    alpha1 = int(np.count_nonzero(alpha1_mask))
    outside = alpha1 - int(np.count_nonzero(alpha[body[1]:body[3], body[0]:body[2]] == 1)) if body else alpha1
    body_k = max((body[2] - body[0] + crop[0] - 1) // crop[0],
                 (body[3] - body[1] + crop[1] - 1) // crop[1]) if body else None
    return {'source_pixels': list(alpha.shape[::-1]), 'native_geometry': geometry,
            'declared_natural_k': natural_k,
            'minimum_integer_k_for_positive_bbox_in_native_crop': minimum_k,
            'k_inferred_or_selected': False,
            'positive_bbox_ltrb': positive, 'alpha1_bbox_ltrb': alpha_box(alpha == 1),
            'positive_pixels': int(np.count_nonzero(alpha)),
            'alpha1_pixels': alpha1, 'alpha1_outside_alpha128_bbox_pixels': outside,
            'minimum_integer_k_for_alpha128_bbox_in_native_crop': body_k,
            'alpha1_enlarges_required_crop_k': bool(body_k and minimum_k and minimum_k > body_k),
            'review_hints': ['POSITIVE_ALPHA1_OUTSIDE_OPAQUE_PROXY_REQUIRES_SOURCE_OWNER_REVIEW'] if outside else [],
            'alpha128_bbox_ltrb': body, 'alpha128_component_count': None if truncated else len(regions),
            'alpha128_largest_components': regions[:16],
            'component_scan_truncated': truncated, 'component_output_truncated': len(regions) > 16,
            'alpha_ge2_proxy': ge2_proxy, 'alpha1_topology': alpha1_topology,
            'source_alpha128_bbox_aspect': aspect(body), 'native_alpha128_bbox_aspect': aspect(native),
            'axis_scale_from_alpha128_bbox': axis_scale,
            'positive_bottom_y_source_pixel': positive[3] - 1 if positive else None,
            'alpha128_bottom_y_source_pixel': body[3] - 1 if body else None,
            'native_bottom_proxy_1x': (body[3] - 1) / natural_k if body and natural_k and full_native_view else None,
            'semantic_body_or_foot_verified': False, 'per_cell_containment_verified': False,
            'measurement_scope': 'Alpha128 component/envelope proxy only; all alpha>0 including alpha1 is preserved.',
            'source_pixels_mutated': False, 'art_approved': False}


def alpha_stats(alpha: np.ndarray, origin: tuple[int, int]) -> dict:
    visible = alpha > 0
    strong = alpha >= 16
    strong_box = alpha_box(strong)
    faint_outside = alpha == 1
    if strong_box:
        x0, y0, x1, y1 = strong_box
        faint_outside[y0:y1, x0:x1] = False
    sizes, truncated = component_sizes(strong)
    weights = alpha.astype(np.float64)
    total = float(weights.sum())
    center = None
    if total:
        center = [round((origin[0] + float(np.dot(np.arange(alpha.shape[1]), weights.sum(axis=0)) / total)) / 3, 3),
                  round((origin[1] + float(np.dot(np.arange(alpha.shape[0]), weights.sum(axis=1)) / total)) / 3, 3)]
    return {
        "alpha_gt0_box_3x": alpha_box(visible),
        "alpha_ge16_box_3x": strong_box,
        "alpha_gt0_pixels": int(np.count_nonzero(visible)),
        "alpha_eq1_pixels": int(np.count_nonzero(alpha == 1)),
        "alpha_eq1_outside_ge16_box_pixels": int(np.count_nonzero(faint_outside)),
        "alpha_ge16_components_8connected": None if truncated else len(sizes),
        "alpha_ge16_small_components_1_to_9_pixels": None if truncated else sum(size <= 9 for size in sizes),
        "alpha_ge16_pixels_outside_largest_component": None if truncated else sum(sizes[1:]),
        "component_scan_truncated": truncated,
        "alpha_weighted_center_canvas_1x": center,
    }


def alpha_array(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("motion output is not PNG")
        if image.width * image.height > 64_000_000:
            raise ValueError("motion output exceeds 64 million pixels")
        return np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8).copy()


def native_rgba_canvas(row: dict) -> np.ndarray:
    """Compare visible pixels at the original canvas/origin, including alpha1."""
    width, height = (n * 3 for n in row['native_canvas_1x'])
    if width * height > 16_000_000:
        raise ValueError('NATIVE_CANVAS_RGBA_DIAGNOSTIC_PIXEL_LIMIT')
    with Image.open(row['output']) as image:
        rgba = np.asarray(image.convert('RGBA'), dtype=np.uint8).copy()
    # Only the comparison buffer normalizes invisible RGB; source bytes and
    # every nonzero alpha are preserved.
    rgba[rgba[:, :, 3] == 0] = 0
    canvas = np.zeros((height, width, 4), dtype=np.uint8)
    x, y = (n * 3 for n in row['native_origin_1x'])
    canvas[y:y + rgba.shape[0], x:x + rgba.shape[1]] = rgba
    return canvas


def inspect_frame(preflight: dict, result: dict) -> dict:
    identity = str(preflight.get("id", result.get("id", "?")))
    row: dict = {"id": identity, "status": "BLOCKED", "errors": [], "review_hints": []}
    try:
        if result.get("id") != identity or preflight.get("status") != "READY":
            raise ValueError("run result/preflight ID or readiness mismatch")
        if result.get("status") not in GOOD_RESULTS:
            raise ValueError(f"motion run status: {result.get('status')}")
        receipt_path = Path(result["receipt"]).resolve(strict=True)
        output = Path(result["output"]).resolve(strict=True)
        receipt = read_json(receipt_path)
        if receipt.get("schema") != "h3.motion.receipt.v1":
            raise ValueError("unsupported motion receipt")
        if any(receipt.get(flag) is not False for flag in AUTHORITY_FLAGS):
            raise ValueError("motion receipt authority flags changed")
        if receipt.get("job_fingerprint") != preflight.get("fingerprint"):
            raise ValueError("motion job fingerprint drift")
        if Path(preflight["output"]).resolve() != output or Path(receipt["output"]).resolve() != output:
            raise ValueError("motion output path drift")
        contract = receipt["contract"]
        entry = contract["entry"]
        native = contract["native_entry"]
        key = entry["key"]
        if entry["id"] != identity or native["key"] != key or native["targets"] != entry["targets"]:
            raise ValueError("original slot/geometry binding drift")
        if not isinstance(key, list) or len(key) != 4 or key[0] != "def":
            raise ValueError("motion review requires a four-part DEF key")
        targets = entry["targets"]
        canvas = pair(targets["canvas_1x"], "canvas_1x", 1)
        crop = pair(targets["crop_1x"], "crop_1x", 1)
        origin = pair(targets["origin_1x"], "origin_1x")
        if any(origin[n] + crop[n] > canvas[n] for n in range(2)):
            raise ValueError("native crop exceeds canvas")
        expected_pixels = [crop[0] * 3, crop[1] * 3]
        if receipt.get("output_pixels") != expected_pixels or preflight.get("output_pixels") != expected_pixels:
            raise ValueError("native 3X crop dimensions drift")
        if receipt.get("origin_3x") != [origin[0] * 3, origin[1] * 3]:
            raise ValueError("native 3X origin drift")
        if sha256(output) != receipt.get("png_sha256"):
            raise ValueError("receipt-bound PNG SHA256 drift")
        alpha = alpha_array(output)
        if list(alpha.shape[::-1]) != expected_pixels:
            raise ValueError("decoded PNG dimensions drift")
        stats = alpha_stats(alpha, (origin[0] * 3, origin[1] * 3))
        if not stats["alpha_gt0_pixels"]:
            row["review_hints"].append("EMPTY_ALPHA")
        if stats["alpha_eq1_outside_ge16_box_pixels"]:
            row["review_hints"].append("FAINT_ALPHA1_OUTSIDE_MAIN_ART")
        if stats["component_scan_truncated"]:
            row["review_hints"].append("COMPONENT_SCAN_LIMIT_REACHED")
        elif stats["alpha_ge16_small_components_1_to_9_pixels"]:
            row["review_hints"].append("DETACHED_SMALL_ALPHA_COMPONENTS_CHECK_INTENT")
        row.update(status="RECEIPT_BOUND_REVIEW_REQUIRED", key=key, group=[key[1], key[2]],
                   native_canvas_1x=list(canvas), native_crop_1x=list(crop), native_origin_1x=list(origin),
                   output=str(output), output_sha256=receipt["png_sha256"],
                   receipt=str(receipt_path), receipt_sha256=sha256(receipt_path), **stats)
        try:
            canvas_rgba = native_rgba_canvas(row)
            header = json.dumps(list(canvas_rgba.shape[:2])).encode('ascii')
            row['visible_rgba_native_canvas_sha256'] = hashlib.sha256(header + canvas_rgba.tobytes()).hexdigest()
        except ValueError as error:
            row['visible_rgba_native_canvas_sha256'] = None
            row['review_hints'].append(str(error))
    except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
        row["errors"].append(str(error))
    return row


def adjacent_stats(left: dict, right: dict) -> dict:
    """Compare next frame in the original 3X canvas, without assuming timing."""
    a = alpha_array(Path(left["output"])) >= 16
    b = alpha_array(Path(right["output"])) >= 16
    ax, ay = (value * 3 for value in left["native_origin_1x"])
    bx, by = (value * 3 for value in right["native_origin_1x"])
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + a.shape[1], bx + b.shape[1]), min(ay + a.shape[0], by + b.shape[0])
    intersection = 0
    if x1 > x0 and y1 > y0:
        intersection = int(np.count_nonzero(
            a[y0 - ay:y1 - ay, x0 - ax:x1 - ax] & b[y0 - by:y1 - by, x0 - bx:x1 - bx]))
    union = int(np.count_nonzero(a) + np.count_nonzero(b) - intersection)
    ca, cb = left["alpha_weighted_center_canvas_1x"], right["alpha_weighted_center_canvas_1x"]
    motion = round(float(np.hypot(ca[0] - cb[0], ca[1] - cb[1])), 3) if ca and cb else None
    result = {"from": left["id"], "to": right["id"],
            "alpha_ge16_iou_on_native_canvas": round(intersection / union, 4) if union else None,
            "alpha_center_delta_1x": motion,
            "native_canvas_changed": left["native_canvas_1x"] != right["native_canvas_1x"],
            "timing_assumed": False}
    result['changed_visible_rgba_pixels'] = None
    result['visible_rgba_identical'] = None
    if not result['native_canvas_changed']:
        try:
            aa, bb = native_rgba_canvas(left), native_rgba_canvas(right)
            changed = int(np.count_nonzero(np.any(aa != bb, axis=2)))
            result['changed_visible_rgba_pixels'] = changed
            result['visible_rgba_identical'] = changed == 0
        except ValueError as error:
            result['rgba_diagnostic_unavailable'] = str(error)
    return result


def image_url(outdir: Path, image: Path) -> str:
    relative = os.path.relpath(image, outdir).replace("\\", "/")
    return quote(relative, safe="/.-_~")


def render_html(report: dict, outdir: Path, image_urls: dict | None = None, *, sequence_only: bool = False) -> str:
    if sequence_only:
        source = report["source_review"]
        summary = report["sequence_review"]["summary"]
        rows = "".join(
            f'<tr><td>F{row["key"][3]}</td><td>{html.escape(str(row["output"]))}</td>'
            f'<td>{html.escape(row["output_sha256"])}</td></tr>' for row in report["frames"])
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>H3 Source 既有3X局部动作审图</title><style>'
            'body{font:15px system-ui;background:#17222d;color:#e7ebef;margin:24px}'
            'h1,h2{color:#f0c76b}button{padding:8px 14px}table{border-collapse:collapse}'
            'td,th{border:1px solid #536879;padding:6px;overflow-wrap:anywhere}</style>'
            '<h1>Source 既有3X局部动作审图</h1>'
            f'<p>Source 声明槽 {summary["available_slots"]}/{summary["expected_slots"]}；'
            f'HOLD {summary["hold_slots"]}。局部审图不表示完整动作、美术或原生时序已验收。</p>'
            f'<p>冻结 Source 选择：{html.escape(source["path"])}<br>'
            f'SHA256 {html.escape(source["sha256"])}</p>'
            '<p>几何来自所绑定的 Source placement/request，不代表工具独立核验原生库存。'
            '仅显示同一批已有3X字节，不生成1X、2X、整画布或拼板文件。</p>'
            '<details><summary>本次绑定的已有文件</summary><table>'
            '<tr><th>Source 槽</th><th>PNG path</th><th>SHA256</th></tr>'
            + rows + '</table></details></html>')
    sections = []
    verified = {row.get("review_id", row["id"]): row for row in report["frames"] if row["status"] == "RECEIPT_BOUND_REVIEW_REQUIRED"}
    for group in report["groups"]:
        cards = []
        rows = [verified[identity] for identity in group["frame_ids"]]
        for index, row in enumerate(rows):
            title = html.escape(f"{row['key'][1]} / group {row['key'][2]} / frame {row['key'][3]} / {row['id']}")
            cw, ch = row["native_canvas_1x"]
            layers = []
            for offset, kind in ((-1, "before"), (1, "after"), (0, "current")):
                other = rows[index + offset] if 0 <= index + offset < len(rows) else None
                if not other or other["status"] != "RECEIPT_BOUND_REVIEW_REQUIRED":
                    continue
                ox, oy = other["native_origin_1x"]
                w, h = other["native_crop_1x"]
                src = html.escape((image_urls or {}).get(other["output"]) or image_url(outdir, Path(other["output"])), quote=True)
                layers.append(f'<img class="{kind}" src="{src}" style="left:{ox}px;top:{oy}px;width:{w}px;height:{h}px" '
                              f'loading="lazy" alt="{html.escape(other["id"], quote=True)}">')
            hints = ", ".join(row["review_hints"]) or "无自动提示；仍须人工看图"
            src = html.escape((image_urls or {}).get(row["output"]) or image_url(outdir, Path(row["output"])), quote=True)
            cards.append(f'<article class="frame"><h3>{title}</h3><div class="stage" style="width:{cw}px;height:{ch}px">'
                         f'{"".join(layers)}</div><p>原生画布 {cw}×{ch}；锚点 {row["native_origin_1x"]}</p>'
                         f'<p>alpha1 图外 {row["alpha_eq1_outside_ge16_box_pixels"]}；小连通块 '
                         f'{row["alpha_ge16_small_components_1_to_9_pixels"]}</p><p>{html.escape(hints)}</p>'
                         f'<details><summary>实际 3X 裁片</summary><img src="{src}" alt="3X crop" loading="lazy"></details></article>')
        title = html.escape(f"{group['identity']} / group {group['group']}")
        summary = html.escape('; '.join(group.get('review_hints', [])) or '整组变化仍需人工审核')
        sections.append(f'<section><h2>{title}</h2><p>原画布可见RGBA类别 {group.get("unique_visible_rgba_native_canvas_frames")}；'
                        f'相邻帧最多变化像素 {group.get("max_adjacent_changed_visible_rgba_pixels")}。{summary}</p>'
                        f'<div class="strip">{"".join(cards)}</div></section>')
    blocked = [f'<li>{html.escape(row["id"])}: {html.escape("; ".join(row["errors"]))}</li>'
               for row in report["frames"] if row["status"] == "BLOCKED"]
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>H3TierExport 动作序列审核</title><style>
body{{font:15px system-ui;background:#17222d;color:#e7ebef;margin:24px}}h1,h2{{color:#f0c76b}}button{{padding:8px 14px}}
.strip{{display:flex;gap:14px;overflow-x:auto;padding:10px 0 20px}}.frame{{flex:none;background:#243340;padding:12px;border:1px solid #536879;max-width:90vw}}
.frame h3{{margin:0 0 8px}}.frame p{{max-width:460px}}.blocked{{border-color:#d06060}}.stage{{position:relative;max-width:80vw;overflow:hidden;
background:repeating-conic-gradient(#46515c 0% 25%,#303b47 0% 50%) 0/16px 16px}}.stage img{{position:absolute;max-width:none}}
.before{{opacity:.34;filter:brightness(0) saturate(100%) invert(38%) sepia(95%) saturate(3000%) hue-rotate(340deg)}}
.after{{opacity:.34;filter:brightness(0) saturate(100%) invert(73%) sepia(57%) saturate(960%) hue-rotate(136deg)}}
body.no-ghosts .before,body.no-ghosts .after{{display:none}}details img{{max-width:none}}small{{color:#b9cad8}}
</style><h1>H3TierExport 动作序列审核</h1><p>源报告：{html.escape(report.get('input_source', report.get('run_report', '')))}；{report['verified_frames']} 帧收据绑定；
{report['blocked_frames']} 帧阻断。已有3X裁片按原生画布与原点缩小显示，不产生独立1X文件，红色为前帧、青色为后帧。叠影只是审核辅助；不推断时序或自动批准美术。</p>
<button onclick="document.body.classList.toggle('no-ghosts')">切换前后帧叠影</button>
<p><small>PNG 未被修改。alpha1、碎片、轮廓位移仅供人工判断；正常火星或光点也可能触发提示。浏览器显示并非游戏实机验收。</small></p>
<section><h2>阻断项</h2><ul>{''.join(blocked) or '<li>无</li>'}</ul></section>
{''.join(sections)}</html>'''


def run_frames(document: dict) -> list[dict]:
    """Inspect actual entries without synthesizing a producer report."""
    if document.get("schema") != "h3.motion.run.v1":
        raise ValueError("h3.motion.run.v1 required")
    preflight, results = document.get("preflight", {}).get("items"), document.get("results")
    if not isinstance(preflight, list) or not isinstance(results, list) or not 0 < len(preflight) <= 512:
        raise ValueError("motion run preflight/results missing or excessive")
    if not all(isinstance(r, dict) for r in preflight + results):
        raise ValueError("motion run rows must be objects")
    ids = [r.get("id") for r in preflight]
    if any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("duplicate or invalid preflight IDs")
    by_id = {r.get("id"): r for r in results}
    if len(by_id) != len(results) or set(by_id) != set(ids):
        raise ValueError("motion results differ from preflight slots")
    return [inspect_frame(r, by_id[r["id"]]) for r in preflight]


def native_sequence_record(expected, available, image_records, bindings, errors, metrics, *, source_targets=None):
    """One native-slot/HOLD assembly for single-run and collection views."""
    source_scope = bindings.get("geometry_authority") == SOURCE_GEOMETRY_AUTHORITY
    if source_scope != (source_targets is not None) or (
            source_scope and set(source_targets) != set(expected)):
        raise ValueError("SOURCE_EXPECTED_GEOMETRY_SET_DRIFT")
    groups = {}
    for key in expected:
        groups.setdefault((key[1], key[2]), []).append(key)
    sequences = []
    for (identity, group), keys in sorted(groups.items()):
        keys.sort(key=lambda key: key[3])
        slots = []
        for key in keys:
            geometry = source_targets[key] if source_scope else native.official_targets(expected[key], key)
            slots.append(available.get(key, {"key": list(key), "status":
                "HOLD_DECLARED_SOURCE_SLOT" if source_scope else "HOLD_MISSING_NATIVE_SLOT",
                "canvas_1x": geometry["canvas_1x"], "crop_1x": geometry["crop_1x"], "origin_1x": geometry["origin_1x"]}))
        holds = sum("image" not in r for r in slots)
        if source_scope:
            if (max(row["canvas_1x"][0] for row in slots)
                    * max(row["canvas_1x"][1] for row in slots) * 9 > SOURCE_LIMITS["canvas_pixels"]):
                raise ValueError("SOURCE_GROUP_CANVAS_PIXEL_BUDGET_EXCEEDED")
            wrap = {"status": "NOT_MEASURED_SOURCE_DISPLAY"}
        else:
            wrap = (adjacent_stats(metrics[keys[-1]], metrics[keys[0]])
                    if not holds and len(keys) > 1 and all(k in metrics for k in keys)
                    else {"status": "HOLD_OR_INSUFFICIENT_NATIVE_SLOTS"})
        sequences.append({"identity": identity, "group": group, "expected_slots": len(keys),
            "available_slots": len(keys) - holds, "hold_slots": holds,
            "status": (("PARTIAL_SOURCE_SEQUENCE_REVIEW_REQUIRED" if holds else "COMPLETE_SOURCE_SLOTSET_REVIEW_REQUIRED")
                       if source_scope else "HOLD_MISSING_NATIVE_SLOTS" if holds else "COMPLETE_TECHNICAL_SEQUENCE_REVIEW_REQUIRED"),
            "frames": slots, "wrap_diagnostic": wrap, "native_loop_verified": False,
            "art_approved": False, "native_timing_verified": False, "game_deployed": False,
            **({"geometry_authority": SOURCE_GEOMETRY_AUTHORITY} if source_scope else {})})
    used = {r["image"] for r in available.values()}
    return {"schema": "h3.motion.source-sequence-review.v1" if source_scope else "h3.motion.native-sequence-review.v1", **bindings,
        "summary": {"expected_slots": len(expected), "available_slots": len(available),
            "hold_slots": len(expected) - len(available), "unique_owner_images": len(used),
            "slots_sharing_owner_images": len(available) - len(used),
            "complete_groups": sum(g["hold_slots"] == 0 for g in sequences)},
        "groups": sequences, "images": {key: image_records[key] for key in used},
        "errors": errors, "new_png_files": 0, "stored_image_scale": 3,
        "native_size_view": "Browser display of existing 3X images; no new 1X artifact",
        "playback_timing": "Manual preview cadence, not original native timing or loop proof",
        "playback_visually_verified": False, "whole_creature_art_accepted": False,
        "native_timing_verified": False, "game_deployed": False,
        **({"native_inventory_verified": False, "native_loop_verified": False,
            "human_approved": False, "runtime_accepted": False,
            "partial": len(available) != len(expected),
            "expected_slot_scope": "Exact frozen Source logical-slot set only"} if source_scope else {})}


def source_collection_frames(manifest, manifest_ref, outdir, refs):
    """Read a frozen Source selection; never execute or project Source state."""
    def require(value, code):
        if not value:
            raise ValueError(code)

    def file_ref(value, label, *, metadata=False):
        require(isinstance(value, dict), label + "_REFERENCE_REQUIRED")
        require(metadata or set(value) <= {"path", "sha256", "bytes"}, label + "_REFERENCE_FIELDS_INVALID")
        require(isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), label + "_REFERENCE_INVALID")
        if "bytes" in value:
            require(type(value["bytes"]) is int and value["bytes"] >= 0, label + "_BYTES_INVALID")
        path = native._absolute_lexical_path(Path(value["path"]), label)
        require(not native._overlap(path, outdir), "SOURCE_INPUT_OUTPUT_OVERLAP")
        return {"path": str(path), "sha256": value["sha256"],
                **({"bytes": value["bytes"]} if "bytes" in value else {})}

    def path_key(ref):
        return native._path_key(Path(ref["path"]))

    documents, png_seen = {}, set()
    usage = {"json_bytes": 0, "png_bytes": 0, "decoded_pixels": 0}

    def document(value, label):
        ref = file_ref(value, label)
        key = ref["path"]
        remaining = SOURCE_LIMITS["json_total_bytes"] - usage["json_bytes"]
        limit = min(SOURCE_LIMITS["json_file_bytes"], remaining) if key not in documents else SOURCE_LIMITS["json_file_bytes"]
        require(limit >= 0, "SOURCE_JSON_TOTAL_BUDGET_EXCEEDED")
        raw = refs.read(ref, label, limit)
        if key not in documents:
            usage["json_bytes"] += len(raw)
            documents[key] = refs.document(ref, label)
        return documents[key], ref

    def same_ref(value, expected, label):
        ref = file_ref(value, label)
        require(path_key(ref) == path_key(expected) and ref["sha256"] == expected["sha256"]
                and ("bytes" not in ref or ref["bytes"] == refs.files[expected["path"]]["bytes"]),
                label + "_BINDING_DRIFT")

    def key_tuple(value):
        require(isinstance(value, list) and len(value) == 4 and value[0] == "def"
                and isinstance(value[1], str) and 0 < len(value[1]) <= 96
                and all(type(x) is int and x >= 0 for x in value[2:]), "SOURCE_SLOT_KEY_INVALID")
        return tuple(value)

    def geometry(value):
        require(isinstance(value, dict), "SOURCE_TARGETS_REQUIRED")
        target = {name: list(pair(value.get(name), name, 0 if name == "origin_1x" else 1))
                  for name in ("canvas_1x", "crop_1x", "origin_1x")}
        require(all(target["origin_1x"][n] + target["crop_1x"][n] <= target["canvas_1x"][n]
                    for n in range(2)), "SOURCE_CROP_OUTSIDE_CANVAS")
        for name in ("canvas", "crop", "origin"):
            scaled = pair(value.get(name + "_3x"), name + "_3x", 0 if name == "origin" else 1)
            require(list(scaled) == [n * 3 for n in target[name + "_1x"]],
                    "SOURCE_3X_GEOMETRY_DRIFT")
        require(target["canvas_1x"][0] * target["canvas_1x"][1] * 9 <= SOURCE_LIMITS["canvas_pixels"],
                "SOURCE_CANVAS_PIXEL_BUDGET_EXCEEDED")
        return target

    def indexed(rows, label):
        require(isinstance(rows, list) and 0 < len(rows) <= SOURCE_LIMITS["slots"], label + "_COUNT_INVALID")
        result = {}
        for row in rows:
            require(isinstance(row, dict), label + "_ROW_INVALID")
            key = key_tuple(row.get("key"))
            require(key[:3] == ("def", identity, group) and key not in result, label + "_KEY_DRIFT")
            result[key] = row
        return result

    def root_selection(value, *, include_hold_evidence=True):
        require(value.get("schema") == "h3.creature.root-local-art-review.v1", "SOURCE_ROOT_SCHEMA_UNSUPPORTED")
        require(value.get("identity") == identity and type(value.get("group")) is int and value["group"] == group,
                "SOURCE_PARENT_SELECTION_GROUP_DRIFT")
        selected, native_frames = value.get("frames"), value.get("native_frames")
        require(isinstance(selected, list) and 0 < len(selected) <= SOURCE_LIMITS["slots"]
                and isinstance(native_frames, list) and len(native_frames) == len(selected)
                and all(type(n) is int and n >= 0 for n in native_frames)
                and native_frames == sorted(set(native_frames)), "SOURCE_SELECTED_SLOTS_INVALID")
        require(all(isinstance(row, dict) and type(row.get("native_frame")) is int for row in selected)
                and [row["native_frame"] for row in selected] == native_frames, "SOURCE_REVIEW_SELECTION_DRIFT")
        boundary = value.get("review_boundary")
        require(isinstance(boundary, dict) and all(boundary.get(k) is False for k in
                ("formal_projection_written", "whole_group_art_approved", "human_art_approved",
                 "native_timing_verified", "game_deployed")), "SOURCE_REVIEW_AUTHORITY_ESCALATION")
        require(all(isinstance(boundary.get(k), list) and all(type(n) is int for n in boundary[k])
                and boundary[k] == native_frames for k in
                ("actual_full_3x_frames_seen", "actual_css_native_frames_seen")), "SOURCE_REVIEW_SELECTION_DRIFT")
        holds = value.get("holds")
        require(isinstance(holds, dict), "SOURCE_HOLD_DECLARATION_REQUIRED")
        held = holds.get("native_frames")
        require(isinstance(held, list) and len(held) <= SOURCE_LIMITS["slots"]
                and all(type(n) is int and n >= 0 for n in held) and held == sorted(set(held))
                and not set(held).intersection(native_frames), "SOURCE_HOLD_SLOTS_INVALID")
        context = {"native_frames": held}
        if held:
            require(isinstance(holds.get("reason"), str), "SOURCE_HOLD_REASON_REQUIRED")
            context["reason"] = holds["reason"]
            if include_hold_evidence and "fit" in holds:
                _, context["fit"] = document(holds["fit"], "SOURCE_DECLARED_HOLD_FIT")
        return selected, native_frames, held, context

    def same_declaration(value, expected, label, *, metadata=False):
        ref, parent = file_ref(value, label, metadata=metadata), file_ref(expected, label)
        require(path_key(ref) == path_key(parent) and ref["sha256"] == parent["sha256"]
                and ("bytes" not in ref or "bytes" not in parent or ref["bytes"] == parent["bytes"]),
                label + "_BINDING_DRIFT")

    require(set(manifest) == {"schema", "operation_id", "source_review"}, "SOURCE_COLLECTION_FIELDS_INVALID")
    require(isinstance(manifest["source_review"], dict)
            and set(manifest["source_review"]) == {"path", "sha256"}, "SOURCE_REVIEW_REFERENCE_FIELDS_INVALID")
    op = manifest["operation_id"]
    require(isinstance(op, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", op)
            and outdir.name == op, "COLLECTION_OPERATION_OUTPUT_MISMATCH")
    document(manifest_ref, "SOURCE_COLLECTION_MANIFEST")
    root, root_ref = document(manifest["source_review"], "SOURCE_ROOT_REVIEW")
    identity, group = root.get("identity"), root.get("group")
    require(isinstance(identity, str) and 0 < len(identity) <= 96
            and type(group) is int and group >= 0, "SOURCE_ROOT_GROUP_INVALID")
    source_schema = root.get("schema")
    parent_selection = None
    if source_schema == "h3.creature.root-local-art-review.v1":
        selected, native_frames, held, hold_context = root_selection(root)
    else:
        require(source_schema == "h3.creature.existing-3x-css-review-bindings.v1", "SOURCE_ROOT_SCHEMA_UNSUPPORTED")
        require(all(root.get(flag) is False for flag in ("human_art_approved", "whole_group_art_approved",
                "native_timing_verified", "game_deployed", "runtime_accepted", "art_approved")),
                "SOURCE_REVIEW_AUTHORITY_ESCALATION")
        parent, parent_selection = document(root.get("first_six_binding_authority"), "SOURCE_FIRST_SELECTION")
        previous, previous_numbers, previous_holds, _ = root_selection(parent, include_hold_evidence=False)
        require(len(previous) == 6 and len(previous_holds) == 2, "SOURCE_FIRST_SIX_SELECTION_INVALID")
        previous_by_frame = {row["native_frame"]: row for row in previous}
        rows = root.get("frames")
        require(isinstance(rows, list) and len(rows) == 8 and len(rows) <= SOURCE_LIMITS["slots"]
                and all(isinstance(row, dict) and type(row.get("native_frame")) is int
                        and row["native_frame"] >= 0 for row in rows), "SOURCE_EIGHT_SELECTION_INVALID")
        native_frames = [row["native_frame"] for row in rows]
        changes = root.get("logical_frame_binding_changes")
        require(native_frames == sorted(set(native_frames)) and set(native_frames) == set(previous_numbers) | set(previous_holds)
                and isinstance(changes, list) and all(type(n) is int for n in changes)
                and changes == previous_holds, "SOURCE_EXPLICIT_ADDITIONS_DRIFT")
        html_rows = indexed(root.get("html_frame_bindings"), "SOURCE_HTML_BINDINGS")
        require(set(html_rows) == {("def", identity, group, n) for n in native_frames},
                "SOURCE_HTML_BINDING_SLOT_SET_DRIFT")
        new_export = file_ref(root.get("new_a21_local_export"), "SOURCE_EXPLICIT_PAIR_EXPORT")
        selected = []
        for row in rows:
            n = row["native_frame"]
            key = ("def", identity, group, n)
            require("local_export" not in row, "SOURCE_EIGHT_INLINE_EXPORT_UNSUPPORTED")
            file_ref(row.get("request"), "SOURCE_EIGHT_REQUEST")
            target = geometry(row.get("targets"))
            require(geometry(html_rows[key].get("targets")) == target, "SOURCE_HTML_GEOMETRY_DRIFT")
            same_declaration(html_rows[key].get("candidate"), row.get("production_3x"),
                             "SOURCE_HTML_CANDIDATE", metadata=True)
            if n in previous_by_frame:
                old = previous_by_frame[n]
                for name in ("production_3x", "placement"):
                    same_declaration(row.get(name), old.get(name), "SOURCE_FIRST_SIX_" + name.upper())
                require(row.get("pixels") == old.get("pixels"), "SOURCE_FIRST_SIX_PIXELS_DRIFT")
                export_ref = old.get("local_export")
            else:
                export_ref = new_export
            selected.append({**row, "local_export": export_ref})
        held, hold_context = [], {"native_frames": []}

    expected, targets, logical_snapshot = None, {}, None
    frames, available, images, image_records = [], {}, {}, {}
    for selected_row in selected:
        require(isinstance(selected_row, dict) and type(selected_row.get("native_frame")) is int,
                "SOURCE_SELECTED_ROW_INVALID")
        frame_number = selected_row["native_frame"]
        key = ("def", identity, group, frame_number)
        require(frame_number in native_frames and key not in available, "SOURCE_SELECTED_KEY_DRIFT")
        placement, placement_ref = document(selected_row.get("placement"), "SOURCE_PLACEMENT")
        export, export_ref = document(selected_row.get("local_export"), "SOURCE_LOCAL_EXPORT")
        require(placement.get("schema") == "h3.creature.group-placement.v1"
                and export.get("schema") == "h3.creature.local-batch-execution.v1", "SOURCE_PARENT_SCHEMA_UNSUPPORTED")
        request, request_ref = document({"path": placement.get("request_path"),
            "sha256": placement.get("request_sha256")}, "SOURCE_REQUEST")
        require(request.get("schema") == "h3.full-art.request.v1" and request.get("identity") == identity
                and type(request.get("group")) is int and request["group"] == group, "SOURCE_REQUEST_GROUP_DRIFT")
        require(type(request.get("production_scale")) is int and request["production_scale"] == 3,
                "SOURCE_REQUEST_PRODUCTION_SCALE_INVALID")
        require(type(request.get("attempt")) is int and request["attempt"] > 0, "SOURCE_REQUEST_ATTEMPT_INVALID")
        if source_schema == "h3.creature.existing-3x-css-review-bindings.v1":
            same_ref(selected_row["request"], request_ref, "SOURCE_EIGHT_REQUEST")
        logical = indexed(request.get("logical_slot_bindings"), "SOURCE_LOGICAL_SLOTS")
        if logical_snapshot is None:
            logical_snapshot, expected = request["logical_slot_bindings"], logical
            targets = {k: geometry(row.get("targets")) for k, row in logical.items()}
        require(request["logical_slot_bindings"] == logical_snapshot, "SOURCE_LOGICAL_BINDINGS_DRIFT")
        require(type(request.get("native_frame_count")) is int and request["native_frame_count"] == len(expected),
                "SOURCE_NATIVE_SLOT_COUNT_DRIFT")
        original = indexed(request.get("original_slots"), "SOURCE_ORIGINAL_SLOTS")
        placed = indexed(placement.get("frames"), "SOURCE_PLACED_SLOTS")
        require(set(original) == set(placed) and set(placed) <= set(expected)
                and type(request.get("frame_count")) is int and request["frame_count"] == len(placed),
                "SOURCE_COHORT_SLOT_SET_DRIFT")
        common_k = placement.get("common_scale")
        require(type(common_k) is int and common_k > 0 and type(export.get("common_integer_k")) is int
                and export["common_integer_k"] == common_k and type(export.get("target_scale")) is int
                and export["target_scale"] == 3, "SOURCE_DECLARED_SCALE_DRIFT")
        if source_schema == "h3.creature.existing-3x-css-review-bindings.v1":
            require(type(selected_row.get("common_integer_k")) is int and selected_row["common_integer_k"] == common_k
                    and type(selected_row.get("attempt")) is int and selected_row["attempt"] == request.get("attempt")
                    and geometry(selected_row.get("targets")) == targets[key], "SOURCE_EIGHT_FRAME_BINDING_DRIFT")
        for placed_key, row in placed.items():
            require(geometry(row.get("complete_target_geometry")) == targets[placed_key]
                    == geometry(original[placed_key].get("targets"))
                    and type(row.get("common_source_pixels_per_native_pixel")) is int
                    and row["common_source_pixels_per_native_pixel"] == common_k, "SOURCE_PLACEMENT_GEOMETRY_DRIFT")
        original_receipt, original_ref = document(
            {"path": request.get("source_receipt"), "sha256": request.get("source_receipt_sha256")},
            "SOURCE_ORIGINAL_RECEIPT")
        source_original = indexed(original_receipt.get("entries"), "SOURCE_ORIGINAL_RECEIPT_SLOTS")
        require(set(source_original) == set(original) and all(
                geometry(source_original[k].get("targets")) == targets[k]
                for k in source_original), "SOURCE_ORIGINAL_RECEIPT_GEOMETRY_DRIFT")
        if "inputs" in export:
            require("request" not in export and "placement" not in export and isinstance(export["inputs"], list)
                    and 0 < len(export["inputs"]) <= SOURCE_LIMITS["slots"],
                    "SOURCE_EXPORT_REFERENCE_SHAPE_INVALID")
            input_refs = [file_ref(value, "SOURCE_EXPORT_INPUT") for value in export["inputs"]]
            for parent_ref in (request_ref, placement_ref):
                matches = [ref for ref in input_refs if path_key(ref) == path_key(parent_ref)]
                require(len(matches) == 1, "SOURCE_EXPORT_PARENT_REFERENCE_MISSING_OR_DUPLICATE")
                same_ref(matches[0], parent_ref, "SOURCE_EXPORT_PARENT")
        else:
            same_ref(export.get("request"), request_ref, "SOURCE_EXPORT_REQUEST")
            same_ref(export.get("placement"), placement_ref, "SOURCE_EXPORT_PLACEMENT")
        require(all(export.get(flag) is False for flag in ("art_approved", "game_deployed")),
                "SOURCE_EXPORT_AUTHORITY_ESCALATION")
        timing_flags = [flag for flag in ("timing_verified", "native_timing_verified") if flag in export]
        require(timing_flags and all(export[flag] is False for flag in timing_flags)
                and all(export[flag] is False for flag in ("human_approved", "runtime_accepted", "published_3x")
                        if flag in export)
                and ("manifest_writes" not in export or type(export["manifest_writes"]) is int
                     and export["manifest_writes"] == 0), "SOURCE_EXPORT_AUTHORITY_ESCALATION")
        require(all(type(export.get(field)) is int and export[field] == value
                    for field, value in (("group", group), ("attempt", request.get("attempt"))) if field in export),
                "SOURCE_EXPORT_COHORT_DRIFT")
        stages = export.get("stages")
        require(isinstance(stages, list) and len(stages) == 2 and all(isinstance(s, dict)
                and type(s.get("exit_code")) is int and s["exit_code"] == 0
                and isinstance(s.get("argv"), list) and len(s["argv"]) >= 2
                and all(isinstance(arg, str) for arg in s["argv"]) for s in stages)
                and [s["argv"][1] for s in stages] == ["expand", "expand-verify"], "SOURCE_EXPORT_STAGES_NOT_VERIFIED")
        require(key in placed, "SOURCE_READY_SLOT_MISSING")
        png_ref = file_ref(selected_row.get("production_3x"), "SOURCE_SELECTED_PNG")
        output_path = file_ref({"path": placed[key].get("output"), "sha256": png_ref["sha256"]}, "SOURCE_PLACED_OUTPUT")
        require(path_key(output_path) == path_key(png_ref), "SOURCE_PLACED_OUTPUT_DRIFT")
        outputs = export.get("outputs")
        require(isinstance(outputs, list) and 0 < len(outputs) <= SOURCE_LIMITS["slots"],
                "SOURCE_EXPORT_OUTPUTS_REQUIRED")
        output_refs = [file_ref(value, "SOURCE_EXPORT_OUTPUT", metadata=True) for value in outputs]
        matches = [i for i, value in enumerate(output_refs) if path_key(value) == path_key(png_ref)]
        require(len(matches) == 1, "SOURCE_EXPORT_OUTPUT_MISSING_OR_DUPLICATE")
        output = outputs[matches[0]]
        require(output_refs[matches[0]]["sha256"] == png_ref["sha256"], "SOURCE_EXPORT_PNG_SHA256_DRIFT")
        pixel_size = [n * 3 for n in targets[key]["crop_1x"]]
        require(selected_row.get("pixels") == output.get("pixels") == pixel_size, "SOURCE_PNG_GEOMETRY_DRIFT")
        png_key = png_ref["path"]
        remaining = SOURCE_LIMITS["png_total_bytes"] - usage["png_bytes"]
        raw = refs.read(png_ref, "SOURCE_SELECTED_PNG", remaining if png_key not in png_seen else SOURCE_LIMITS["png_total_bytes"])
        if png_key not in png_seen:
            png_seen.add(png_key)
            usage["png_bytes"] += len(raw)
        same_ref(output_refs[matches[0]], png_ref, "SOURCE_EXPORT_OUTPUT")
        image_id = png_ref["sha256"]
        if image_id not in images:
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    require(image.format == "PNG" and image.mode == "RGBA" and getattr(image, "n_frames", 1) == 1,
                            "SOURCE_STATIC_RGBA_PNG_REQUIRED")
                    width, height = image.size
                    require([width, height] == pixel_size and width * height <= SOURCE_LIMITS["image_pixels"],
                            "SOURCE_PNG_PIXEL_BUDGET_OR_GEOMETRY")
                    require(usage["decoded_pixels"] + width * height <= SOURCE_LIMITS["decoded_pixels"],
                            "SOURCE_DECODED_PIXEL_BUDGET_EXCEEDED")
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
            except (OSError, SyntaxError, Image.DecompressionBombError) as error:
                raise ValueError("SOURCE_PNG_DECODE_FAILED: " + str(error)) from error
            usage["decoded_pixels"] += width * height
            images[image_id] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            image_records[image_id] = {**png_ref, "encoded_pixels": pixel_size,
                "image_storage_only_not_native_alias_authority": True}
        require(image_records[image_id]["encoded_pixels"] == pixel_size, "SOURCE_SHARED_PNG_GEOMETRY_DRIFT")
        binding = {"placement": placement_ref, "local_export": export_ref, "request": request_ref,
                   "original_source_receipt": original_ref}
        frames.append({"id": f"SOURCE_{identity}_G{group}_F{frame_number}", "key": list(key),
            "status": "SOURCE_BOUND_REVIEW_REQUIRED", "output": png_ref["path"],
            "output_sha256": png_ref["sha256"], "pixels": pixel_size, "source_bindings": binding})
        available[key] = {"key": list(key), "status": "SOURCE_BOUND_REVIEW_REQUIRED", "image": image_id,
            "source_rect_3x": [0, 0, *pixel_size], **targets[key], "source_bindings": binding,
            "binding": "Frozen Source selection/placement/request/export output SHA; no independent native inventory claim"}
    require(set(available) == {("def", identity, group, n) for n in native_frames}
            and set(expected) == set(available) | {("def", identity, group, n) for n in held},
            "SOURCE_EXPECTED_READY_HOLD_SET_DRIFT")
    sequence = native_sequence_record(expected, available, image_records,
        {"collection_manifest": manifest_ref, "source_review": root_ref,
         "source_selection_schema": source_schema,
         **({"parent_source_selection": parent_selection} if parent_selection is not None else {}),
         "geometry_authority": SOURCE_GEOMETRY_AUTHORITY, "source_hold_context": hold_context,
         "input_usage": usage}, [], {}, source_targets=targets)
    refs.verify_current()
    return frames, sequence, images, manifest


def collection_frames(path: Path, expected_sha: str, outdir: Path, refs):
    """Bind real operation reports/specs/receipts to selected original slots."""
    def require(value, code):
        if not value:
            raise ValueError(code)
    manifest_ref = {"path": str(path), "sha256": expected_sha}
    manifest = refs.document(manifest_ref, "COLLECTION_MANIFEST")
    if manifest.get("schema") == SOURCE_COLLECTION_SCHEMA:
        return source_collection_frames(manifest, manifest_ref, outdir, refs)
    require(set(manifest) == {"schema", "operation_id", "official_inventory", "slot_bindings"}, "COLLECTION_FIELDS_INVALID")
    require(manifest["schema"] == "h3.motion.review-collection.v1", "COLLECTION_SCHEMA_INVALID")
    op = manifest["operation_id"]
    require(isinstance(op, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", op)
            and outdir.name == op, "COLLECTION_OPERATION_OUTPUT_MISMATCH")
    inventory = manifest["official_inventory"]
    refs.read(inventory, "COLLECTION_INVENTORY", 512 * 1024 * 1024)
    catalog, _ = native.load_inventory(Path(inventory["path"]), inventory["sha256"])
    facts = refs.document(manifest["slot_bindings"], "COLLECTION_SLOT_BINDINGS")
    require(facts.get("schema") == "h3.map-ui.phoenix21-qa-candidate-bindings.v1", "COLLECTION_BINDING_SCHEMA_UNSUPPORTED")
    require(all(facts.get(k) is False for k in ("full_animation_art_approved", "native_timing_approved",
            "ownership_approved", "formal_intake", "game_written")), "COLLECTION_AUTHORITY_ESCALATION")
    projection_ref = facts["native_projection"]
    projection = refs.document(projection_ref, "COLLECTION_NATIVE_PROJECTION")
    require(projection.get("schema") == "h3.motion.native-slot-projection.v1", "COLLECTION_PROJECTION_SCHEMA_INVALID")
    projected = {}
    for row in projection["entries"]:
        key = native.key_tuple(row["key"])
        require(key not in projected, "COLLECTION_DUPLICATE_PROJECTED_KEY")
        projected[key] = row
    parent = refs.document(projection["projection_source"], "COLLECTION_ORIGINAL_PROJECTION")
    require(parent.get("schema") == "h3.native-slot.projection.v1", "COLLECTION_ORIGINAL_PROJECTION_SCHEMA_INVALID")
    require(parent["native_lod"]["sha256"] == facts["current_original_lod_sha256_verified"], "COLLECTION_NATIVE_CONTAINER_BINDING_DRIFT")
    refs.read(parent["native_lod"], "COLLECTION_NATIVE_CONTAINER", 512 * 1024 * 1024)
    items = facts["items"]
    require(isinstance(items, list) and 0 < len(items) <= 512, "COLLECTION_ITEM_COUNT_INVALID")
    runs, run_rows, frames, available, images, image_records, metrics = {}, {}, [], {}, {}, {}, {}
    encoded_budget = decoded_budget = 0

    def pin_entry_sources(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                refs.read(value, "COLLECTION_RENDER_SOURCE", 512 * 1024 * 1024)
            for child in value.values():
                pin_entry_sources(child)
        elif isinstance(value, list):
            for child in value:
                pin_entry_sources(child)

    for item in items:
        key = native.key_tuple(item["key"])
        require(key in catalog and key in projected and key[0] == "def", "COLLECTION_UNKNOWN_NATIVE_SLOT")
        require(key not in available, "COLLECTION_DUPLICATE_NATIVE_SLOT")
        require(type(item["frame"]) is int and item["frame"] == key[3], "COLLECTION_FRAME_KEY_DRIFT")
        run_ref = item["actual_run_report"]
        run_path = str(native._absolute_lexical_path(Path(run_ref["path"]), "collection run"))
        run = refs.document(run_ref, "COLLECTION_ACTUAL_RUN")
        require(run.get("status") == "COMPLETED", "COLLECTION_RUN_NOT_COMPLETED")
        if run_path not in runs:
            runs[run_path], run_rows[run_path] = run, run_frames(run)
        receipt = refs.document(item["receipt"], "COLLECTION_ACTUAL_RECEIPT")
        actual_result = next((r for r in run["results"] if native._path_key(Path(r["receipt"]))
                              == native._path_key(Path(item["receipt"]["path"]))), None)
        frame = next((r.copy() for r in run_rows[run_path] if actual_result is not None
                      and r["id"] == actual_result["id"]), None)
        require(frame is not None, "COLLECTION_RECEIPT_NOT_BOUND_TO_ACTUAL_RUN")
        require(frame["status"] == "RECEIPT_BOUND_REVIEW_REQUIRED",
                "COLLECTION_FRAME_BLOCKED: " + str(key) + " " + "; ".join(frame["errors"]))
        require(frame["key"] == list(key) and frame["receipt_sha256"] == item["receipt"]["sha256"], "COLLECTION_RUN_RECEIPT_KEY_DRIFT")
        targets = native.official_targets(catalog[key], key)
        require(item["native_targets"] == targets == {"canvas_1x": frame["native_canvas_1x"],
            "crop_1x": frame["native_crop_1x"], "origin_1x": frame["native_origin_1x"]}, "COLLECTION_ORIGINAL_GEOMETRY_DRIFT")
        require(projected[key] == receipt["contract"]["native_entry"]
                and item["native_equivalence_class"] == projected[key]["equivalence_class"], "COLLECTION_PROJECTED_NATIVE_CONTRACT_DRIFT")
        spec = refs.document(item["source_spec"], "COLLECTION_SOURCE_SPEC")
        require(spec.get("schema") == "h3.motion.spec.v1", "COLLECTION_SOURCE_SPEC_SCHEMA_INVALID")
        spec_entry = next((x for x in spec["items"] if x["id"] == frame["id"]), None)
        entry = receipt["contract"]["entry"]
        require(spec_entry == entry and item["mode"] == entry["mode"], "COLLECTION_SPEC_RECEIPT_DRIFT")
        require(any(ref.get("sha256") == projection_ref["sha256"] and native._path_key(Path(ref["path"]))
                    == native._path_key(Path(projection_ref["path"])) for ref in spec["native_contracts"]), "COLLECTION_SPEC_PROJECTION_DRIFT")
        pin_entry_sources(entry)
        require(native._path_key(Path(item["png"]["path"])) == native._path_key(Path(frame["output"]))
                and item["png"]["sha256"] == frame["output_sha256"], "COLLECTION_PNG_NOT_FROM_ACTUAL_RUN")
        raw = refs.read(item["png"], "COLLECTION_ACTUAL_PNG", 64 * 1024 * 1024)
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == "PNG" and image.mode == "RGBA", "COLLECTION_RGBA_PNG_REQUIRED")
            rgba = image.copy()
        crop, origin, canvas = targets["crop_1x"], targets["origin_1x"], targets["canvas_1x"]
        require(list(rgba.size) == item["png"]["pixels"] == [n * 3 for n in crop]
                and native.sha256_bytes(rgba.tobytes()) == item["png"]["rgba_sha256"], "COLLECTION_RGBA_OR_PIXELS_DRIFT")
        require(canvas[0] * canvas[1] * 9 <= 16 * 1024 * 1024, "COLLECTION_CANVAS_PIXEL_LIMIT")
        full = Image.new("RGBA", tuple(n * 3 for n in canvas))
        full.paste(rgba, tuple(n * 3 for n in origin))
        require(native.sha256_bytes(full.tobytes()) == item["png"]["full_canvas_rgba_sha256"], "COLLECTION_FULL_CANVAS_RGBA_DRIFT")
        owner_key = key
        if entry["mode"] == "reuse":
            owner_key = native.key_tuple(entry["source"]["key"])
            require(owner_key in projected and projected[owner_key]["targets"] == targets
                    and projected[owner_key]["equivalence_class"] == projected[key]["equivalence_class"]
                    and entry["source"]["sha256"] == item["png"]["sha256"], "COLLECTION_ALIAS_ORIGINAL_EQUIVALENCE_DRIFT")
        image_id = item["png"]["sha256"]
        if image_id not in images:
            encoded_budget += len(raw); decoded_budget += rgba.width * rgba.height
            require(encoded_budget <= 64 * 1024 * 1024 and decoded_budget <= 64 * 1024 * 1024, "COLLECTION_PREVIEW_BUDGET_EXCEEDED")
            images[image_id] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            image_records[image_id] = {**item["png"], "encoded_pixels": list(rgba.size), "image_storage_only_not_native_alias_authority": True}
        frame.update(review_id=run_ref["sha256"] + ":" + frame["id"], source_run=run_ref,
                     source_spec=item["source_spec"], declared_kind=item["kind"],
                     declared_manual_roi_diagnostics=item.get("manual_flag_region_diagnostic"), region_ownership_verified=False)
        frames.append(frame); metrics[key] = frame
        available[key] = {"key": list(key), "id": frame["id"], "image": image_id, "owner_key": list(owner_key),
            "source_rect_3x": [0, 0, rgba.width, rgba.height], "canvas_1x": canvas, "crop_1x": crop,
            "origin_1x": origin, "source_run": run_ref, "full_canvas_rgba_sha256": item["png"]["full_canvas_rgba_sha256"],
            "binding": "actual run receipt plus pinned spec, projection and original inventory"}

    journal_run_digests = set()
    semantic_hash = lambda document: native.sha256_bytes(json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for ref in facts["actual_operation_journals"]:
        journal = refs.document(ref, "COLLECTION_OPERATION_JOURNAL")
        stages = journal["stages"]
        require(isinstance(stages, list) and stages and all(type(s.get("exit_code")) is int
                and s["exit_code"] == 0 for s in stages), "COLLECTION_OPERATION_NOT_TERMINAL_EXIT_ZERO")
        require({"motion-plan", "motion-check", "motion-run", "motion-verify"}.issubset(
                {s.get("stage") for s in stages}), "COLLECTION_INCOMPLETE_OPERATION_JOURNAL")
        for stage in stages:
            if stage.get("stage") == "motion-run":
                stdout = stage["stdout_utf8"].encode("utf-8")
                require(native.sha256_bytes(stdout) == stage["stdout_sha256"], "COLLECTION_JOURNAL_STDOUT_DRIFT")
                journal_run_digests.add(semantic_hash(native.parse_json(stdout, "journal run stdout")))
    require(journal_run_digests == {semantic_hash(r) for r in runs.values()}, "COLLECTION_JOURNAL_RUN_SET_DRIFT")
    identities = {key[1] for key in available}
    expected = {key: row for key, row in catalog.items() if key[0] == "def" and key[1] in identities}
    sequence = native_sequence_record(expected, available, image_records,
        {"collection_manifest": manifest_ref, "slot_bindings": manifest["slot_bindings"], "official_inventory": inventory}, [], metrics)
    sequence["summary"].update(actual_operations=len(runs),
        unique_full_canvas_rgba_classes=len({r["full_canvas_rgba_sha256"] for r in available.values()}))
    sequence.update(native_index5_ownership_verified=False, independent_native_1x_exported=False,
                    collection_runs=[refs.files[key] for key in sorted(runs)])
    refs.verify_current()
    return frames, sequence, images, manifest


def native_sequence_review(frames: list[dict], native_report_path: Path,
                           expected_sha256: str) -> tuple[dict, dict[str, str]]:
    """Bind existing 3X review images to actual run slots; write no image files."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise ValueError("NATIVE_REVIEW_REPORT_SHA256_REQUIRED")
    native_report_path = native_report_path.resolve(strict=True)
    if sha256(native_report_path) != expected_sha256:
        raise ValueError("NATIVE_REVIEW_REPORT_SHA256_DRIFT")
    report = read_json(native_report_path)
    if report.get("schema") != "h3.native-review.batch-report.v1":
        raise ValueError("NATIVE_REVIEW_REPORT_SCHEMA_INVALID")
    if any(report.get("authority", {}).get(flag) is not False
           for flag in ("art_approved", "human_approved", "runtime_accepted", "game_deployed")):
        raise ValueError("NATIVE_REVIEW_AUTHORITY_INVALID")
    inventory = report["official_inventory"]
    inventory_path = Path(inventory["path"]).resolve(strict=True)
    catalog, _ = native.load_inventory(inventory_path, inventory["sha256"])
    verified = [row for row in frames if row["status"] == "RECEIPT_BOUND_REVIEW_REQUIRED"]
    identities = {row["key"][1] for row in verified}
    if not identities:
        raise ValueError("NO_VERIFIED_RUN_SLOTS_FOR_NATIVE_PLAYBACK")
    expected = {key: row for key, row in catalog.items() if key[0] == "def" and key[1] in identities}
    owners: dict[str, list[dict]] = {}
    image_data: dict[str, str] = {}
    image_records: dict[str, dict] = {}
    seen = set()
    png_bytes = 0
    decoded_pixels = 0
    for row in report["items"]:
        key = native.key_tuple(row["key"])
        if key[0] != "def" or key[1] not in identities:
            continue
        if key in seen or key not in expected:
            raise ValueError("NATIVE_PREVIEW_DUPLICATE_OR_UNKNOWN_OWNER_KEY")
        seen.add(key)
        geometry = native.official_targets(expected[key], key)
        if row["targets"] != geometry:
            raise ValueError("NATIVE_PREVIEW_GEOMETRY_DRIFT")
        source = Path(row["source_png"]).resolve(strict=True)
        if sha256(source) != row["source_sha256"]:
            raise ValueError("NATIVE_PREVIEW_SOURCE_SHA256_DRIFT")
        source_pixels = [n * 3 for n in geometry["crop_1x"]]
        if (row.get("source_pixels") != source_pixels
                or row.get("source_rect") != [0, 0, *source_pixels]
                or row.get("registration") != "strict-aspect"
                or row.get("source_layout", "native-crop") != "native-crop"):
            raise ValueError("NATIVE_PREVIEW_REQUIRES_COMPLETE_RUN_CROP_SOURCE")
        output = row["outputs"].get("candidate_3x")
        if not output:
            continue
        image_path = Path(output["path"]).resolve(strict=True)
        raw = native.read_bounded(image_path, 64 * 1024 * 1024, "native 3X preview")
        image_sha = hashlib.sha256(raw).hexdigest()
        if image_sha != output["png_sha256"]:
            raise ValueError("NATIVE_PREVIEW_PNG_SHA256_DRIFT")
        if image_sha not in image_data:
            with Image.open(io.BytesIO(raw)) as image:
                canvas_pixels = [n * 3 for n in geometry["canvas_1x"]]
                if image.format != "PNG" or list(image.size) != canvas_pixels or output["pixels"] != canvas_pixels:
                    raise ValueError("NATIVE_PREVIEW_CANVAS_SIZE_DRIFT")
                if image.width * image.height > 16_000_000 or decoded_pixels + image.width * image.height > 64_000_000:
                    raise ValueError("NATIVE_PREVIEW_IMAGE_BUDGET_EXCEEDED")
                rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
                if hashlib.sha256(rgba.tobytes()).hexdigest() != output.get("rgba_sha256"):
                    raise ValueError("NATIVE_PREVIEW_RGBA_SHA256_DRIFT")
                x, y = (n * 3 for n in geometry["origin_1x"])
                w, h = source_pixels
                outside = rgba[:, :, 3].copy()
                outside[y:y + h, x:x + w] = 0
                if np.any(outside):
                    raise ValueError("NATIVE_PREVIEW_HAS_ALPHA_OUTSIDE_OWNER_CROP")
                decoded_pixels += image.width * image.height
            png_bytes += len(raw)
            if png_bytes > 64 * 1024 * 1024 or decoded_pixels > 64_000_000:
                raise ValueError("NATIVE_PREVIEW_IMAGE_BUDGET_EXCEEDED")
            image_data[image_sha] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            image_records[image_sha] = {"path": str(image_path), "sha256": image_sha,
                                        "pixels": output["pixels"], "rgba_sha256": output["rgba_sha256"]}
        owner = {"key": list(key), "image": image_sha, "geometry": geometry,
                 "source_png": str(source), "source_sha256": row["source_sha256"]}
        owners.setdefault(row["source_sha256"], []).append(owner)
    available = {}
    native_errors = []
    for frame in verified:
        key = native.key_tuple(frame["key"])
        if key not in expected or native.official_targets(expected[key], key) != {
                "canvas_1x": frame["native_canvas_1x"], "crop_1x": frame["native_crop_1x"],
                "origin_1x": frame["native_origin_1x"]}:
            raise ValueError("RUN_SLOT_NATIVE_INVENTORY_DRIFT")
        options = [owner for owner in owners.get(frame["output_sha256"], [])
                   if owner["key"][1] == key[1]
                   and owner["geometry"]["crop_1x"] == frame["native_crop_1x"]]
        options.sort(key=lambda owner: (owner["key"] != list(key), owner["key"]))
        if not options:
            native_errors.append({"key": list(key), "reason": "NEEDS_VERIFIED_NATIVE_3X_VIEW"})
            continue
        owner = options[0]
        crop = owner["geometry"]["crop_1x"]
        origin = owner["geometry"]["origin_1x"]
        available[key] = {"key": list(key), "id": frame["id"], "image": owner["image"],
                          "owner_key": owner["key"], "source_rect_3x": [origin[0] * 3, origin[1] * 3, crop[0] * 3, crop[1] * 3],
                          "canvas_1x": frame["native_canvas_1x"], "origin_1x": frame["native_origin_1x"],
                          "crop_1x": frame["native_crop_1x"],
                          "binding": "actual run receipt output SHA to verified native-review input SHA"}
    if sha256(native_report_path) != expected_sha256 or sha256(inventory_path) != inventory["sha256"]:
        raise ValueError("NATIVE_PLAYBACK_BINDING_CHANGED_DURING_REVIEW")
    for owner_list in owners.values():
        for owner in owner_list:
            if sha256(Path(owner["source_png"])) != owner["source_sha256"]:
                raise ValueError("NATIVE_PREVIEW_SOURCE_CHANGED_DURING_REVIEW")
    for record in image_records.values():
        if sha256(Path(record["path"])) != record["sha256"]:
            raise ValueError("NATIVE_PREVIEW_PNG_CHANGED_DURING_REVIEW")
    record = native_sequence_record(expected, available, image_records,
        {"native_review": {"path": str(native_report_path), "sha256": expected_sha256},
         "official_inventory": {"path": str(inventory_path), "sha256": inventory["sha256"]}},
        native_errors, {tuple(row["key"]): row for row in verified})
    return record, {key: image_data[key] for key in record["images"]}


def native_sequence_html(sequence: dict, images: dict[str, str]) -> str:
    source_scope = sequence.get("geometry_authority") == SOURCE_GEOMETRY_AUTHORITY
    cards = []
    for index, group in enumerate(sequence["groups"]):
        canvases = [row["canvas_1x"] for row in group["frames"]]
        width = max((size[0] for size in canvases), default=1)
        height = max((size[1] for size in canvases), default=1)
        title = html.escape(f'{"Source " if source_scope else ""}{group["identity"]} G{group["group"]}')
        wrap = html.escape(json.dumps(group.get("wrap_diagnostic", {}), ensure_ascii=False, indent=2))
        background_control = ('<label> 审图背景 <select class="background"><option value="checker">棋盘</option>'
            '<option value="dark">深色</option><option value="white">白色</option>'
            '<option value="grass">草地色</option></select></label> ' if source_scope else '')
        wrap_title = "未计算数值或循环诊断" if source_scope else "首尾过渡诊断（非原生循环验收）"
        cards.append(f'<article class="sequence-player" data-sequence="{index}" data-native-width="{width}" data-native-height="{height}">'
                     f'<h3>{title}</h3><p>{group["available_slots"]}/{group["expected_slots"]}；HOLD {group["hold_slots"]}。'
                     f'{html.escape(group["status"])}</p><details><summary>{wrap_title}</summary>'
                     f'<pre>{wrap}</pre></details><button class="play">循环预览</button> '
                     '<button class="step">下一槽</button> <select class="display"><option value="1">3X图按原生尺寸显示</option>'
                     '<option value="3">3X原尺寸</option></select> '
                     + background_control +
                     '<label>审图间隔 <input class="cadence" type="number" min="50" max="3000" value="150"> ms</label>'
                     '<p class="frame-label"></p><div class="sequence-scroll"><canvas></canvas></div></article>')
    payload = json.dumps({"groups": sequence["groups"], "images": images}, ensure_ascii=False).replace("<", "\\u003c")
    introduction = ('''<section><h2>按 Source 声明槽位回放</h2><p>仅使用冻结 Source 选择绑定的已有3X图与Source几何。
原生尺寸模式仅缩小显示，不写新1X图；缺槽仅显示声明HOLD，不补帧。手动间隔不证明原生时序或循环。</p>''' if source_scope else
        '''<section><h2>按原生槽位的整组回放</h2><p>使用收据绑定的已有3X图。原生尺寸模式仅缩小显示，
不写新1X图；间隔是人工审图节奏，不代表原生时序或循环。缺槽显示HOLD，完整技术组仍须美术审核。</p>
''')
    return introduction + '''<style>.sequence-player{border:1px solid #667;padding:12px;margin:12px 0}.sequence-scroll{overflow:auto}
.sequence-player canvas{background:repeating-conic-gradient(#ddd 0 25%,#fff 0 50%) 0/16px 16px}
.sequence-player input{width:65px}</style>''' + "".join(cards) + '''
<script type="application/json" id="native-sequence-data">''' + payload + '''</script>
<script>(function(){
const data=JSON.parse(document.getElementById('native-sequence-data').textContent);
const cache=new Map();let active=null;
function image(key){if(!cache.has(key)){const im=new Image();im.src=data.images[key];cache.set(key,im);}return cache.get(key);}
document.querySelectorAll('.sequence-player').forEach(function(panel){
const group=data.groups[Number(panel.dataset.sequence)],canvas=panel.querySelector('canvas'),label=panel.querySelector('.frame-label');
let position=0,timer=null,serial=0;
function stop(){if(timer!==null)clearInterval(timer);timer=null;panel.querySelector('.play').textContent='循环预览';}
function draw(){const token=++serial,frame=group.frames[position],scale=Number(panel.querySelector('.display').value);
canvas.width=Number(panel.dataset.nativeWidth)*scale;canvas.height=Number(panel.dataset.nativeHeight)*scale;
canvas.style.width=canvas.width+'px';canvas.style.height=canvas.height+'px';
label.textContent=(group.geometry_authority==='owner-source-placement'?'Source 槽 F':'原槽 F')+frame.key[3]+' · '+(position+1)+'/'+group.frames.length+(frame.image?' · REVIEW_REQUIRED':' · HOLD');
canvas.dataset.frame=String(frame.key[3]);canvas.dataset.hold=frame.image?'false':'true';
canvas.dataset.painted='false';
if(!frame.image)return;
const im=image(frame.image);function paint(){if(token!==serial)return;const r=frame.source_rect_3x,ctx=canvas.getContext('2d');
ctx.imageSmoothingEnabled=true;ctx.drawImage(im,r[0],r[1],r[2],r[3],frame.origin_1x[0]*scale,frame.origin_1x[1]*scale,frame.crop_1x[0]*scale,frame.crop_1x[1]*scale);
canvas.dataset.painted='true';}if(im.complete&&im.naturalWidth)paint();else im.addEventListener('load',paint,{once:true});}
panel.querySelector('.step').onclick=function(){stop();position=(position+1)%group.frames.length;draw();};
panel.querySelector('.display').onchange=draw;
const background=panel.querySelector('.background');
if(background)background.onchange=function(){const colors={checker:'repeating-conic-gradient(#ddd 0 25%,#fff 0 50%) 0/16px 16px',dark:'#000',white:'#fff',grass:'#4a5e3e'};canvas.style.background=colors[this.value];};
panel.querySelector('.play').onclick=function(){if(timer!==null){stop();return;}if(active)active();active=stop;
let ms=Number(panel.querySelector('.cadence').value);if(!Number.isFinite(ms))ms=150;ms=Math.max(50,Math.min(3000,ms));
timer=setInterval(function(){position=(position+1)%group.frames.length;draw();},ms);panel.querySelector('.play').textContent='暂停';};
draw();});})();</script></section>'''


def review_image_urls(frames, refs):
    """Embed exact existing PNG bytes so HTTP/file previews share one portable result."""
    urls, cache, total = {}, {}, 0
    for row in frames:
        if row["status"] != "RECEIPT_BOUND_REVIEW_REQUIRED":
            continue
        ref = {"path": row["output"], "sha256": row["output_sha256"]}
        raw = refs.read(ref, "REVIEW_EXISTING_PNG", 64 * 1024 * 1024)
        if ref["sha256"] not in cache:
            total += len(raw)
            if total > 64 * 1024 * 1024:
                raise ValueError("REVIEW_EMBEDDED_PNG_BUDGET_EXCEEDED")
            cache[ref["sha256"]] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        urls[row["output"]] = cache[ref["sha256"]]
        refs.read({"path": row["receipt"], "sha256": row["receipt_sha256"]}, "REVIEW_EXISTING_RECEIPT")
    return urls


def review(run_report: Path, outdir: Path, *, expected_run_sha256: str | None = None,
           native_report: Path | None = None, expected_native_sha256: str | None = None,
           output_base: Path | None = None, tool_binding: dict | None = None,
           collection: bool = False) -> dict:
    run_report = Path(os.path.abspath(run_report))
    outdir = Path(os.path.abspath(outdir))
    if os.path.lexists(outdir):
        raise FileExistsError("review output directory already exists; use a new destination")
    refs = native.BoundInputSet(error_type=ValueError)
    sequence, image_data = None, {}
    source_collection = False
    if collection:
        if native_report is not None or expected_native_sha256 is not None:
            raise ValueError("COLLECTION_CANNOT_USE_SINGLE_NATIVE_REVIEW_REPORT")
        frames, sequence, image_data, collection_manifest = collection_frames(run_report, expected_run_sha256, outdir, refs)
        source_collection = collection_manifest["schema"] == SOURCE_COLLECTION_SCHEMA
        source_completed = True
        input_bindings = {"collection_manifest": {"path": str(run_report), "sha256": expected_run_sha256},
                          "combined_run_report_created": False}
        if source_collection:
            input_bindings.update(source_review=sequence["source_review"], geometry_authority=SOURCE_GEOMETRY_AUTHORITY,
                                  native_inventory_verified=False, source_formal_admission_verified=False,
                                  partial=sequence["partial"], source_selection_schema=sequence["source_selection_schema"])
            if "parent_source_selection" in sequence:
                input_bindings["parent_source_selection"] = sequence["parent_source_selection"]
        else:
            input_bindings["producer_reports"] = sequence["collection_runs"]
    else:
        run_sha = sha256(run_report)
        if expected_run_sha256 is not None and run_sha != expected_run_sha256:
            raise ValueError("RUN_REPORT_SHA256_DRIFT")
        if native_report is not None and expected_run_sha256 is None:
            raise ValueError("RUN_REPORT_SHA256_REQUIRED_FOR_NATIVE_PLAYBACK")
        document = refs.document({"path": str(run_report), "sha256": run_sha}, "REVIEW_RUN_REPORT")
        frames = run_frames(document)
        source_completed = document.get("status") == "COMPLETED"
        input_bindings = {"run_report": str(run_report), "run_report_sha256": run_sha}
        if native_report is not None:
            native_report = Path(os.path.abspath(native_report))
            sequence, image_data = native_sequence_review(frames, native_report, expected_native_sha256)
            bound_report = refs.document({"path": str(native_report), "sha256": expected_native_sha256},
                                         "REVIEW_NATIVE_REPORT")
            refs.read(bound_report["official_inventory"], "REVIEW_NATIVE_INVENTORY", 512 * 1024 * 1024)
            for owner in bound_report["items"]:
                refs.read({"path": owner["source_png"], "sha256": owner["source_sha256"]},
                          "REVIEW_NATIVE_SOURCE", 512 * 1024 * 1024)
                image = owner["outputs"].get("candidate_3x")
                if image is not None:
                    refs.read({"path": image["path"], "sha256": image["png_sha256"]},
                              "REVIEW_NATIVE_3X_PNG", 64 * 1024 * 1024)
    groups = {}
    for row in ([] if source_collection else frames):
        if row["status"] == "RECEIPT_BOUND_REVIEW_REQUIRED":
            groups.setdefault((str(row["key"][1]), int(row["key"][2])), []).append(row)
    ordered_groups, adjacency = [], []
    for (identity, group_number), rows in groups.items():
        rows.sort(key=lambda row: (int(row["key"][3]), row["id"]))
        comparisons = [adjacent_stats(a, b) for a, b in zip(rows, rows[1:])]
        fingerprints = [row.get("visible_rgba_native_canvas_sha256") for row in rows]
        complete = all(fingerprints)
        unique = len(set(fingerprints)) if complete else None
        changes = [v["changed_visible_rgba_pixels"] for v in comparisons]
        largest = max(changes) if changes and all(v is not None for v in changes) else None
        hints = []
        if len(rows) > 1 and complete:
            if unique == 1:
                hints.append("STATIC_SEQUENCE_REVIEW_REQUIRED")
            elif largest is not None and largest <= 2:
                hints.append("NEAR_STATIC_SEQUENCE_REVIEW_REQUIRED")
        ordered_groups.append({"identity": identity, "group": group_number,
            "frame_ids": [row.get("review_id", row["id"]) for row in rows],
            "unique_visible_rgba_native_canvas_frames": unique,
            "max_adjacent_changed_visible_rgba_pixels": largest,
            "near_static_hint_max_changed_pixels": 2, "review_hints": hints,
            "scope": "Visible RGBA only; native aliases, indexed effects, intent and timing require owner review."})
        adjacency.extend(comparisons)
    blocked = sum(row["status"] == "BLOCKED" for row in frames)
    report = {"schema": ("h3.motion.source-collection.visual-review.v1" if source_collection else
                        "h3.motion.collection.visual-review.v1" if collection else "h3.motion.visual-review.v1"),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "SOURCE_BLOCKED" if blocked or not source_completed else "REVIEW_READY",
        "input_source": str(run_report), **input_bindings,
        "verified_frames": len(frames) - blocked, "blocked_frames": blocked, "frames": frames,
        "groups": ordered_groups, "adjacent_diagnostics": adjacency, "metrics_are_review_hints": True,
        "original_pngs_modified": 0, "art_approved": False, "native_timing_verified": False,
        "game_deployed": False, "runtime_accepted": False}
    if source_collection:
        report.update(human_approved=False, native_loop_verified=False, model_inference_performed=False,
                      model_native_output=False, input_bindings=list(refs.files.values()),
                      metrics_computed=False)
    if tool_binding is not None:
        report["reviewer_tool"] = tool_binding
    if sequence is not None:
        report["sequence_review"] = sequence
        if sequence["errors"]:
            report["status"] = "SOURCE_BLOCKED"
    page = render_html(report, outdir, {} if source_collection else review_image_urls(frames, refs),
                       sequence_only=source_collection)
    if sequence is not None:
        page = page.replace("</html>", native_sequence_html(sequence, image_data) + "</html>")
    refs.verify_current()
    guard = native.OutputRootGuard.create(outdir, output_base or outdir.parent)
    try:
        native._write_new(outdir / "review.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), guard)
        native._write_new(outdir / "index.html", page.encode("utf-8"), guard)
        guard.verify_current()
        refs.verify_current()
    finally:
        guard.close()
    result = {"status": report["status"], "verified_frames": report["verified_frames"],
        "blocked_frames": blocked, "html": str(outdir / "index.html"), "report": str(outdir / "review.json"),
        "report_sha256": sha256(outdir / "review.json"), "images_written": 0}
    if sequence is not None:
        result["source_sequence" if source_collection else "native_sequence"] = sequence["summary"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--run-report", type=Path)
    inputs.add_argument("--collection", type=Path)
    parser.add_argument("--collection-sha256")
    parser.add_argument("--run-report-sha256")
    parser.add_argument("--native-review-report", type=Path)
    parser.add_argument("--native-review-report-sha256")
    parser.add_argument("--renderer", required=True, type=Path)
    parser.add_argument("--renderer-sha256", required=True)
    parser.add_argument("--outdir", required=True, type=Path)
    args = parser.parse_args()
    binding = native._read_build_receipt(args.renderer_sha256, args.renderer,
                                        additional_companions=("scripts/review_motion_sequence.py",))
    tool_binding = {"executable": {"path": str(args.renderer), "sha256": args.renderer_sha256}, "build": binding}
    output_base = resolve_tool_root(__file__) / "build/experiments/motion-review"
    if native._path_key(Path(os.path.abspath(args.outdir)).parent) != native._path_key(output_base):
        raise ValueError("MOTION_REVIEW_OUTPUT_MUST_BE_OPERATION_CHILD")
    if args.collection is not None and args.run_report_sha256 is not None:
        raise ValueError("COLLECTION_CANNOT_USE_RUN_REPORT_SHA256")
    if args.run_report is not None and args.collection_sha256 is not None:
        raise ValueError("RUN_REPORT_CANNOT_USE_COLLECTION_SHA256")
    result = review(args.collection or args.run_report, args.outdir,
                    expected_run_sha256=args.collection_sha256 if args.collection is not None else args.run_report_sha256,
                    native_report=args.native_review_report, expected_native_sha256=args.native_review_report_sha256,
                    output_base=output_base, tool_binding=tool_binding, collection=args.collection is not None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "REVIEW_READY" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as error:
        print(json.dumps({"schema": "h3.motion.visual-review.error.v1", "status": "FAILED", "error": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
