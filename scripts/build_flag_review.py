from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SCRIPT = Path(__file__).resolve()
TIER_ROOT = SCRIPT.parents[1]
RECORD_ROOT = TIER_ROOT / "records" / "interface-redraw-20261112-r02"
ASSET_ROOT = TIER_ROOT / "build" / "experiments" / "asset-repair"
REVIEW = ASSET_ROOT / "review-r02"
OPS = [
    "cmflag-edge-r01-l00-02",
    "cmflag-edge-r01-l03-05",
    "cmflag-edge-r01-r00-02",
    "cmflag-edge-r01-r03-05",
]
MAX_IMAGE = (330, 360)
CELL_W, CELL_H = 360, 440


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def checkerboard(size: tuple[int, int], tile: int = 12) -> Image.Image:
    w, h = size
    out = Image.new("RGB", size, (238, 238, 238))
    d = ImageDraw.Draw(out)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if ((x // tile) + (y // tile)) % 2:
                d.rectangle((x, y, min(w - 1, x + tile - 1), min(h - 1, y + tile - 1)), fill=(205, 205, 205))
    return out


def fit_rgba(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    rgba = image.convert("RGBA").copy()
    rgba.thumbnail(box, Image.Resampling.LANCZOS)
    canvas_rgba = checkerboard(box).convert("RGBA")
    x = (box[0] - rgba.width) // 2
    y = (box[1] - rgba.height) // 2
    canvas_rgba.alpha_composite(rgba, (x, y))
    return canvas_rgba.convert("RGB")


def fit_mask(red_mask: np.ndarray, yellow_mask: np.ndarray, box: tuple[int, int]) -> Image.Image:
    h, w = red_mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    rgba[:, :, :3] = (38, 42, 50)
    rgba[red_mask & ~yellow_mask, :3] = (255, 78, 78)
    rgba[yellow_mask & ~red_mask, :3] = (70, 205, 255)
    rgba[red_mask & yellow_mask, :3] = (255, 245, 120)
    red_display = np.asarray(Image.fromarray((red_mask.astype(np.uint8) * 255), "L").filter(ImageFilter.MaxFilter(9))) > 0
    yellow_display = np.asarray(Image.fromarray((yellow_mask.astype(np.uint8) * 255), "L").filter(ImageFilter.MaxFilter(9))) > 0
    rgba[red_display & ~yellow_display, :3] = (255, 78, 78)
    rgba[yellow_display & ~red_display, :3] = (70, 205, 255)
    rgba[red_display & yellow_display, :3] = (255, 245, 120)
    image = Image.fromarray(rgba, "RGBA")
    image.thumbnail(box, Image.Resampling.NEAREST)
    canvas = Image.new("RGB", box, (38, 42, 50))
    x = (box[0] - image.width) // 2
    y = (box[1] - image.height) // 2
    canvas.paste(image.convert("RGB"), (x, y))
    return canvas


def comparison_panel(item: dict) -> Image.Image:
    panel = Image.new("RGB", (CELL_W * 4, CELL_H), (248, 249, 251))
    draw = ImageDraw.Draw(panel)
    title_font = load_font(17, True)
    meta_font = load_font(12)
    labels = ["ORIGINAL", "RED-KEY CANDIDATE", "YELLOW-KEY CANDIDATE", "CHANGE MAP (9PX DISPLAY MARKS)"]
    images = [item["source_image"], item["red_image"], item["yellow_image"]]
    previews = [fit_rgba(im, MAX_IMAGE) for im in images]
    previews.append(fit_mask(item["red_mask"], item["yellow_mask"], MAX_IMAGE))
    for index, (label, preview) in enumerate(zip(labels, previews)):
        x0 = index * CELL_W
        draw.text((x0 + 12, 8), label, font=title_font, fill=(35, 42, 52))
        draw.rectangle((x0 + 9, 38, x0 + CELL_W - 10, 408), outline=(205, 210, 218), width=1)
        panel.paste(preview, (x0 + 15, 45))
    draw.text((12, 416), f'{item["id"]}  |  {item["pixels"][0]} x {item["pixels"][1]} px', font=meta_font, fill=(70, 78, 90))
    return panel


def main() -> None:
    if REVIEW.exists():
        raise SystemExit(f"Refusing to overwrite existing review package: {REVIEW}")
    REVIEW.mkdir(parents=True)
    comparisons = REVIEW / "comparisons"
    comparisons.mkdir()
    entries = []

    for op in OPS:
        manifest_path = RECORD_ROOT / f"{op}.json"
        receipt_path = ASSET_ROOT / op / "asset-repair-receipt.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["manifest_sha256"] == sha256(manifest_path)
        receipt_jobs = {x["id"]: x for x in receipt["jobs"]}
        receipt_outputs = {x["id"]: x for x in receipt["outputs"]}
        for job in manifest["jobs"]:
            jid = job["id"]
            stats = receipt_jobs[jid]["statistics"]
            output = receipt_outputs[jid]
            source_path = Path(job["source"]["image"]["path"])
            source_receipt_path = Path(job["source"]["original_source_receipt"]["path"])
            candidate_path = Path(output["path"])
            source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
            assert source_receipt["art_accepted"] is False
            assert source_receipt["source_bytes_unchanged"] is True
            assert sha256(source_receipt_path) == job["source"]["original_source_receipt"]["sha256"]
            assert sha256(source_path) == job["source"]["image"]["sha256"]
            assert sha256(candidate_path) == output["sha256"]
            assert source_receipt["original"]["sha256"] == job["source"]["image"]["sha256"]

            source_image = Image.open(source_path).convert("RGBA")
            candidate_image = Image.open(candidate_path).convert("RGBA")
            assert list(source_image.size) == list(job["source"]["image"]["pixels"])
            assert list(candidate_image.size) == list(output["pixels"])
            assert source_image.size == candidate_image.size
            src = np.asarray(source_image)
            cand = np.asarray(candidate_image)
            changed = np.any(src[:, :, :3] != cand[:, :, :3], axis=2)
            assert np.array_equal(src[:, :, 3], cand[:, :, 3])
            assert int(changed.sum()) == stats["selected_pixels"]
            assert stats["alpha_exact"] and stats["whole_canvas_alpha_exact"]
            assert stats["unselected_RGBA_exact"]
            assert stats["selected_opaque_subject_pixels"] == 0
            assert stats["unresolved_key_pixels_retained"] is True
            assert output["role"] == "raw-original-resolution-derived-candidate-not-master"

            side = "LEFT" if job["native_key"][1] == "CMFLAGL" else "RIGHT"
            key = tuple(job["native_key"])
            existing = next((x for x in entries if tuple(x["native_key"]) == key), None)
            if existing is None:
                existing = {
                    "id": f'{job["native_key"][1]}-{job["native_key"][2]}_{job["native_key"][3]}',
                    "side": side,
                    "native_key": list(job["native_key"]),
                    "pixels": list(source_image.size),
                    "source_path": str(source_path),
                    "source_uri": source_path.as_uri(),
                    "source_sha256": job["source"]["image"]["sha256"],
                    "source_receipt_path": str(source_receipt_path),
                    "source_receipt_sha256": sha256(source_receipt_path),
                    "source_image": source_image,
                    "candidates": {},
                }
                entries.append(existing)

            key_rgb = tuple(stats["selection_key_rgb"])
            color_name = "red" if key_rgb == (255, 0, 0) else "yellow"
            assert color_name not in existing["candidates"]
            existing["candidates"][color_name] = {
                "path": str(candidate_path),
                "uri": candidate_path.as_uri(),
                "sha256": output["sha256"],
                "rgba_sha256": output["rgba_sha256"],
                "selected_pixels": stats["selected_pixels"],
                "unresolved_retained": stats["unresolved_key_pixels_without_local_donors"],
                "image": candidate_image,
                "mask": changed,
            }

    assert len(entries) == 12
    for item in entries:
        assert set(item["candidates"]) == {"red", "yellow"}
        item["red_image"] = item["candidates"]["red"]["image"]
        item["yellow_image"] = item["candidates"]["yellow"]["image"]
        item["red_mask"] = item["candidates"]["red"]["mask"]
        item["yellow_mask"] = item["candidates"]["yellow"]["mask"]
        item["red_selected"] = item["candidates"]["red"]["selected_pixels"]
        item["yellow_selected"] = item["candidates"]["yellow"]["selected_pixels"]
        item["red_unresolved"] = item["candidates"]["red"]["unresolved_retained"]
        item["yellow_unresolved"] = item["candidates"]["yellow"]["unresolved_retained"]
        panel = comparison_panel(item)
        panel_path = comparisons / f'{item["id"]}.png'
        panel.save(panel_path, optimize=True)
        item["panel"] = panel_path.name
        item["panel_sha256"] = sha256(panel_path)

    for side, filename in (("LEFT", "overview-left.png"), ("RIGHT", "overview-right.png")):
        selected = [x for x in entries if x["side"] == side]
        overview = Image.new("RGB", (1440, 660), (245, 247, 250))
        for index, item in enumerate(selected):
            thumb = Image.open(comparisons / item["panel"]).convert("RGB")
            thumb.thumbnail((720, 330), Image.Resampling.LANCZOS)
            x = (index % 2) * 720
            y = (index // 2) * 330
            overview.paste(thumb, (x, y))
        overview.save(REVIEW / filename, optimize=True)

    summary = {
        "schema": "h3.flag-edge-review-package.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "operation_ids": OPS,
        "source_count": 12,
        "candidate_count": 24,
        "changed_pixels_red": sum(x["red_selected"] for x in entries),
        "changed_pixels_yellow": sum(x["yellow_selected"] for x in entries),
        "unresolved_red_retained": sum(x["red_unresolved"] for x in entries),
        "unresolved_yellow_retained": sum(x["yellow_unresolved"] for x in entries),
        "alpha_exact_all": True,
        "unselected_rgba_exact_all": True,
        "opaque_subject_pixels_modified": 0,
        "change_map_display_expansion_radius_px": 4,
        "source_images_copied_or_modified": False,
        "candidate_images_modified": False,
        "art_approved": False,
        "formal_x3_promotion": False,
        "game_deployed": False,
        "entries": [],
    }
    html_sections = []
    for item in entries:
        red = item["candidates"]["red"]
        yellow = item["candidates"]["yellow"]
        summary["entries"].append({
            "id": item["id"],
            "native_key": item["native_key"],
            "pixels": item["pixels"],
            "source_path": item["source_path"],
            "source_sha256": item["source_sha256"],
            "source_receipt_path": item["source_receipt_path"],
            "source_receipt_sha256": item["source_receipt_sha256"],
            "red_candidate_path": red["path"],
            "red_candidate_sha256": red["sha256"],
            "red_changed_pixels": red["selected_pixels"],
            "red_unresolved_retained": red["unresolved_retained"],
            "yellow_candidate_path": yellow["path"],
            "yellow_candidate_sha256": yellow["sha256"],
            "yellow_changed_pixels": yellow["selected_pixels"],
            "yellow_unresolved_retained": yellow["unresolved_retained"],
            "comparison_panel": item["panel"],
            "comparison_panel_sha256": item["panel_sha256"],
            "art_accepted": False,
        })
        html_sections.append(f"""
<section class="asset">
  <div class="asset-head">
    <h3>{html.escape(item["id"])}</h3>
    <span>{item["pixels"][0]} × {item["pixels"][1]} px · {item["side"]}</span>
  </div>
  <img class="comparison" src="comparisons/{html.escape(item["panel"])}" alt="{html.escape(item["id"])} original, red-key candidate, yellow-key candidate and changed-pixel map">
  <div class="stats">
    <span>Red-key changed: <b>{item["red_selected"]:,}</b> px</span>
    <span>Yellow-key changed: <b>{item["yellow_selected"]:,}</b> px</span>
    <span>Red unresolved retained: <b>{item["red_unresolved"]:,}</b></span>
    <span>Yellow unresolved retained: <b>{item["yellow_unresolved"]:,}</b></span>
  </div>
  <div class="links">
    <a href="{html.escape(item["source_uri"])}">Open original full-size</a>
    <a href="{html.escape(red["uri"])}">Open red-key candidate</a>
    <a href="{html.escape(yellow["uri"])}">Open yellow-key candidate</a>
  </div>
  <details>
    <summary>Source and candidate SHA-256</summary>
    <code>source: {item["source_sha256"]}</code>
    <code>red: {red["sha256"]}</code>
    <code>yellow: {yellow["sha256"]}</code>
  </details>
</section>""")

    (REVIEW / "review-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CMFLAGL / CMFLAGR 边缘修复候选审阅</title>
<style>
:root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color: #202938; background: #f4f6f8; }}
body {{ margin: 0; padding: 28px; max-width: 1500px; margin-inline: auto; }}
h1 {{ margin: 0 0 8px; font-size: 26px; }}
h2 {{ margin: 32px 0 12px; font-size: 20px; }}
h3 {{ margin: 0; font-size: 18px; }}
p {{ line-height: 1.55; }}
.notice {{ padding: 14px 16px; background: #fff4d6; border: 1px solid #e6cf8a; border-radius: 8px; }}
.overview {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; }}
.overview img {{ width: 100%; height: auto; border: 1px solid #d5dbe3; background: white; }}
.asset {{ margin: 20px 0; padding: 16px; background: white; border: 1px solid #dce1e7; border-radius: 10px; }}
.asset-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:16px; margin-bottom: 10px; }}
.asset-head span {{ color:#586577; font-size:13px; }}
.comparison {{ width:100%; height:auto; display:block; background:#fafafa; border:1px solid #e3e7ec; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px 22px; padding:12px 0 4px; color:#445064; font-size:13px; }}
.links {{ display:flex; flex-wrap:wrap; gap:16px; padding-top:10px; }}
a {{ color:#175bb5; }}
details {{ margin-top:10px; color:#536071; }}
details code {{ display:block; overflow-wrap:anywhere; margin-top:6px; font-size:12px; }}
small,.muted {{ color:#687587; }}
@media(max-width:900px) {{ .overview {{grid-template-columns:1fr}} body {{padding:14px}} .asset-head {{display:block}} }}
</style>
</head>
<body>
<h1>CMFLAGL / CMFLAGR 原图边缘 RGB 候选人工审阅</h1>
<p class="muted">12 张原图 · 24 张原始分辨率候选 · 4 px 边缘带 · Alpha 保持不变 · 生成于 {summary["created_utc"]}</p>
<div class="notice"><b>验收状态：待人工确认。</b> 本页面仅用于比较原图、红色 key 候选和黄色 key 候选；变化位置图仅为便于观察而向外扩展 4 px，精确改动像素数以统计为准。没有候选被批准为美术成品，没有提升为正式 X3，也没有部署到游戏。请重点检查旗帜边缘、旗面图案、杆体边缘及透明背景下的颜色过渡。</div>
<h2>总览 — 左旗</h2>
<div class="overview"><img src="overview-left.png" alt="Left flag overview"></div>
<h2>总览 — 右旗</h2>
<div class="overview"><img src="overview-right.png" alt="Right flag overview"></div>
<h2>逐项原图与候选比较</h2>
{''.join(html_sections)}
<p class="muted">审阅包不复制或修改原图/候选文件。点击全尺寸链接会打开原始文件位置。完整机器可读记录见 review-summary.json；文件校验和见 SHA256SUMS.txt。</p>
</body></html>
"""
    (REVIEW / "index.html").write_text(page, encoding="utf-8")
    readme = """# CMFLAGL / CMFLAGR edge-candidate review

Open index.html in a local browser.

This package compares 12 original generated flag images with 24 raw-original-resolution RGB edge-decontamination candidates:
- original
- red-key candidate
- yellow-key candidate
- changed-pixel location map

The comparison previews are generated from the exact source and candidate bytes recorded in the receipts. Full-size links point to the original files in their existing locations. No source or candidate image is copied, overwritten, promoted, or deployed.

All candidates remain art_approved=false. The change-location map expands selected pixels by 4 px for visibility only; exact counts remain in review-summary.json. This package is a review aid, not an approval or a formal X3 deliverable.
"""
    (REVIEW / "README.md").write_text(readme, encoding="utf-8")
    files = sorted(p for p in REVIEW.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    sums = "".join(f"{sha256(p)}  {p.relative_to(REVIEW).as_posix()}\n" for p in files)
    (REVIEW / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    print(json.dumps({
        "status": "CREATED",
        "review_dir": str(REVIEW),
        "index": str(REVIEW / "index.html"),
        "source_count": len(entries),
        "candidate_count": sum(len(x["candidates"]) for x in entries),
        "comparison_panels": len(list(comparisons.glob("*.png"))),
        "files": len(list(REVIEW.rglob("*"))),
        "sha256sums": str(REVIEW / "SHA256SUMS.txt"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
