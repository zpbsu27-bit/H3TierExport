#!/usr/bin/env python3
"""Manual, SHA-bound anatomy landmark annotation for H3 creature masters.

This companion reads a frozen H3TierExport scale-audit manifest and writes only
h3.creature.scale-landmarks.v1 JSON. It does not edit pixels or infer anatomy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

INPUT_SCHEMA = "h3.creature.scale-report-manifest.v1"
OUTPUT_SCHEMA = "h3.creature.scale-landmarks.v1"
CATEGORY_SCOPE = {
    "infantry": "whole_subject",
    "mage": "whole_subject",
    "mounted": "rider_mount_and_equipment",
    "large_creature": "whole_subject",
}
SLOT_NAME = re.compile(r"^(\d+)_(\d+)\.png$", re.IGNORECASE)


class AnnotationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AnnotationError(f"无法读取 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise AnnotationError("JSON 根必须是对象")
    return value


def parse_key(value: str, identity: str) -> tuple[str, str, int, int]:
    match = SLOT_NAME.fullmatch(value.strip() + ".png") if not value.lower().endswith(".png") else SLOT_NAME.fullmatch(value)
    if not match:
        raise AnnotationError(f"槽位必须使用官方 group_frame 名称，例如 2_5: {value}")
    return ("def", identity, int(match.group(1)), int(match.group(2)))


def load_manifest(path: Path, requested_keys: list[str] | None = None) -> tuple[dict, list[dict]]:
    path = path.resolve(strict=True)
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA:
        raise AnnotationError(f"需要冻结的 {INPUT_SCHEMA} 清单")
    identity = manifest.get("identity")
    category = manifest.get("category")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Z0-9_]{1,20}", identity):
        raise AnnotationError("官方 DEF 身份无效")
    if category not in CATEGORY_SCOPE:
        raise AnnotationError(f"未支持或未核准的类别: {category}")
    masters = Path(manifest.get("masters_dir", "")).resolve(strict=True)
    if not masters.is_dir():
        raise AnnotationError("母版目录不存在")
    native_inventory = manifest.get("native_inventory") or {}
    inv_path = Path(native_inventory.get("path", "")).resolve(strict=True)
    if sha256_file(inv_path) != native_inventory.get("sha256"):
        raise AnnotationError("官方 inventory SHA 漂移；请重新冻结 scale-audit")

    slots = {}
    for slot in manifest.get("native_slots", []):
        key = slot.get("key")
        if isinstance(key, list) and len(key) == 4 and key[0] == "def" and key[1] == identity:
            slots[tuple(key)] = slot
    if not slots:
        raise AnnotationError("冻结清单中没有该身份的官方槽")

    snapshots = {}
    duplicates = set()
    for row in manifest.get("source_snapshot", []):
        source = Path(row.get("path", "")).resolve(strict=True)
        match = SLOT_NAME.fullmatch(source.name)
        if not match or not any(parent.name.upper() == identity for parent in source.parents):
            continue
        key = ("def", identity, int(match.group(1)), int(match.group(2)))
        if key not in slots:
            continue
        if key in snapshots:
            duplicates.add(key)
        snapshots[key] = row
    for key in duplicates:
        snapshots.pop(key, None)

    if requested_keys:
        keys = [parse_key(value, identity) for value in requested_keys]
        if len(set(keys)) != len(keys):
            raise AnnotationError("同一官方槽重复指定")
    else:
        keys = sorted(snapshots, key=lambda key: (key[2], key[3]))
    if not keys:
        raise AnnotationError("没有可标注的母版图片")

    records = []
    from PIL import Image
    for key in keys:
        if key not in slots:
            raise AnnotationError(f"官方 inventory 不含槽位 {key[2]}_{key[3]}")
        row = snapshots.get(key)
        if row is None:
            raise AnnotationError(f"槽位 {key[2]}_{key[3]} 缺图或有多个候选；请重新核对冻结清单")
        image_path = Path(row["path"]).resolve(strict=True)
        if not image_path.is_relative_to(masters):
            raise AnnotationError(f"图片位于所选母版目录之外: {image_path}")
        expected_name = f"{key[2]}_{key[3]}.png"
        if image_path.name != expected_name:
            raise AnnotationError(f"非官方槽位名: {image_path.name}; 应为 {expected_name}")
        expected_sha = row.get("sha256")
        actual_sha = sha256_file(image_path)
        if actual_sha != expected_sha:
            raise AnnotationError(f"槽位 {expected_name} 的 SHA 与冻结清单不符；请重新冻结")
        try:
            with Image.open(image_path) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise AnnotationError(f"{expected_name} 必须是单帧 PNG")
                image.load()
                pixels = list(image.size)
        except OSError as error:
            raise AnnotationError(f"PNG 无法读取 {image_path}: {error}") from error
        records.append({
            "key": list(key),
            "image_path": str(image_path),
            "image_sha256": expected_sha,
            "pixels": pixels,
            "slot": slots[key],
            "measurement_scope": CATEGORY_SCOPE[category],
        })
    return manifest, records


def validate_annotation(record: dict, value: dict) -> dict:
    width, height = record["pixels"]
    pose_group = value.get("pose_group")
    direction = value.get("direction")
    if not isinstance(pose_group, str) or not pose_group.strip():
        raise AnnotationError("必须明确填写可比较姿势组")
    if not isinstance(direction, str) or not direction.strip():
        raise AnnotationError("必须明确填写朝向")

    def point(xy, label):
        if (not isinstance(xy, (list, tuple)) or len(xy) != 2
                or any(type(coord) is not int for coord in xy)
                or not 0 <= xy[0] < width or not 0 <= xy[1] < height):
            raise AnnotationError(f"{label} 标点超出源图像素范围")
        return [xy[0], xy[1]]

    paths = value.get("paths", {})
    normalized_paths = {}
    for name in ("head", "torso", "body"):
        path = paths.get(name)
        if not isinstance(path, list) or len(path) < 2:
            raise AnnotationError(f"{name} 路径至少需要两个手工点")
        normalized_paths[name] = [point(xy, name) for xy in path]
    root_anchor = point(value.get("root_anchor_pixels"), "root anchor")
    return {
        "key": record["key"],
        "image_sha256": record["image_sha256"],
        "coordinate_space": "source_pixels",
        "measurement_scope": record["measurement_scope"],
        "pose_group": pose_group.strip(),
        "direction": direction.strip(),
        "root_anchor_pixels": root_anchor,
        "paths": normalized_paths,
    }


def build_document(records: list[dict], annotations: dict) -> dict:
    frames = []
    for record in records:
        key = tuple(record["key"])
        if key not in annotations:
            continue
        frames.append(validate_annotation(record, annotations[key]))
    if not frames:
        raise AnnotationError("没有完整标注可写入")
    return {"schema": OUTPUT_SCHEMA, "frames": frames}


def common_source_pixel_scale(records: list[dict], viewport_pixels: tuple[int, int]) -> float:
    """Return one view-only pixels-per-screen-pixel scale for the full selection."""
    if not records:
        raise AnnotationError("没有可比较的官方槽位")
    if (not isinstance(viewport_pixels, (list, tuple)) or len(viewport_pixels) != 2
            or any(type(value) is not int or value <= 0 for value in viewport_pixels)):
        raise AnnotationError("预览窗口尺寸无效")
    sizes = [record.get("pixels") for record in records]
    if any(not isinstance(size, (list, tuple)) or len(size) != 2
           or any(type(value) is not int or value <= 0 for value in size) for size in sizes):
        raise AnnotationError("源 PNG 尺寸无效")
    max_width = max(size[0] for size in sizes)
    max_height = max(size[1] for size in sizes)
    return min(viewport_pixels[0] / max_width, viewport_pixels[1] / max_height)


def source_path_length(points: list, pixels: tuple[int, int] | list[int]) -> float:
    width, height = pixels
    if not isinstance(points, list) or len(points) < 2:
        raise AnnotationError("解剖路径至少需要两个手工点")
    for point in points:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(type(value) is not int for value in point)
                or not 0 <= point[0] < width or not 0 <= point[1] < height):
            raise AnnotationError("解剖路径包含无效源像素坐标")
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def source_pixel_measurements(state: dict, pixels: tuple[int, int] | list[int]) -> dict:
    """Summarize manually marked anatomy in source pixels; never imply a K/scale pass."""
    lengths = {}
    paths = state.get("paths", {})
    for name in ("head", "torso", "body"):
        points = paths.get(name, [])
        lengths[name] = source_path_length(points, pixels) if isinstance(points, list) and len(points) >= 2 else None
    ratios = {
        "head_to_torso": lengths["head"] / lengths["torso"]
        if lengths["head"] is not None and lengths["torso"] not in (None, 0) else None,
        "head_to_body": lengths["head"] / lengths["body"]
        if lengths["head"] is not None and lengths["body"] not in (None, 0) else None,
    }
    complete = all(lengths[name] is not None for name in ("head", "torso", "body"))
    return {
        "lengths_source_pixels": lengths,
        "ratios": ratios,
        "status": "MEASURED_SOURCE_PIXELS_ONLY" if complete else "NOT_MEASURED",
        "absolute_body_scale_status": "NOT_MEASURED",
        "reason": "有效注册 K 与可比姿势组仍需在 scale-audit 中绑定；预览倍率不代表身体已归一化。",
    }


def format_source_pixel_measurements(state: dict, pixels: tuple[int, int] | list[int],
                                     pose_group: str = "", direction: str = "") -> str:
    result = source_pixel_measurements(state, pixels)
    lengths = result["lengths_source_pixels"]
    ratios = result["ratios"]
    lines = [f"可比姿势/朝向：{pose_group or '未标注'} / {direction or '未标注'}"]
    labels = {"head": "头部", "torso": "躯干", "body": "骨架路径"}
    for name in ("head", "torso", "body"):
        value = lengths[name]
        lines.append(f"{labels[name]}：{value:.1f} 源像素" if value is not None
                     else f"{labels[name]}：NOT_MEASURED")
    if ratios["head_to_torso"] is not None:
        lines.append(f"头/躯干：{ratios['head_to_torso']:.3f}")
    if ratios["head_to_body"] is not None:
        lines.append(f"头/全身路径：{ratios['head_to_body']:.3f}")
    lines.append(f"身体尺度：{result['status']}；绝对归一化：NOT_MEASURED（未绑定有效 K）")
    lines.append("整批同源像素倍率仅用于看图，不代表各源采样倍率相同或身体大小已通过。")
    return "\n".join(lines)


def write_new_document(path: Path, document: dict, records: list[dict]) -> str:
    path = path.resolve()
    if path.exists():
        raise AnnotationError(f"拒绝覆盖既有标注文件: {path}")
    expected = {tuple(record["key"]): record["image_sha256"] for record in records}
    for frame in document.get("frames", []):
        key = tuple(frame["key"])
        record = next((row for row in records if tuple(row["key"]) == key), None)
        if record is None or expected[key] != frame["image_sha256"]:
            raise AnnotationError("标注槽位或源 SHA 不属于本次冻结清单")
        if sha256_file(Path(record["image_path"])) != expected[key]:
            raise AnnotationError(f"保存前源图发生变化: {record['image_path']}")
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise AnnotationError(f"拒绝覆盖既有标注文件: {path}") from error
    return hashlib.sha256(payload).hexdigest()


class Annotator:
    COLORS = {"head": "#22aaff", "torso": "#ffc928", "body": "#82e34a", "root": "#ff4f5e"}
    TITLES = {
        "head": "头部轴线：头顶 → 下颌",
        "torso": "躯干轴线：肩胸 → 骨盆",
        "body": "全身轴线：头顶 → 骨盆 → 根部",
        "root": "脚底／身体根部接地点",
    }

    def __init__(self, manifest: dict, records: list[dict], output_path: Path | None,
                 default_pose_group: str = "", default_direction: str = ""):
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog
        from PIL import Image, ImageTk
        self.tk, self.ttk, self.messagebox, self.filedialog = tk, ttk, messagebox, filedialog
        self.Image, self.ImageTk = Image, ImageTk
        self.manifest, self.records, self.output_path = manifest, records, output_path
        self.index = 0
        self.active = "body"
        self.states = {}
        for record in records:
            self.states[tuple(record["key"])] = {
                "pose_group": default_pose_group, "direction": default_direction,
                "paths": {"head": [], "torso": [], "body": []}, "root_anchor_pixels": None,
            }
        self.root = tk.Tk()
        self.root.title("H3 生物解剖标点（源图只读）")
        self.root.geometry("1500x980")
        self.root.minsize(1150, 760)
        self._build()
        self.root.after(100, self._show_current)

    def _build(self):
        ttk = self.ttk
        header = ttk.Frame(self.root, padding=8); header.pack(fill="x")
        self.title_var = self.tk.StringVar(); ttk.Label(header, textvariable=self.title_var, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.meta_var = self.tk.StringVar(); ttk.Label(header, textvariable=self.meta_var, wraplength=1450).pack(anchor="w", pady=(3, 0))
        fields = ttk.Frame(self.root, padding=(8, 0, 8, 6)); fields.pack(fill="x")
        ttk.Label(fields, text="可比较姿势组（手工填写）").pack(side="left")
        self.pose_var = self.tk.StringVar(); self.pose_entry = ttk.Entry(fields, textvariable=self.pose_var, width=30); self.pose_entry.pack(side="left", padx=(5, 18))
        ttk.Label(fields, text="朝向（手工填写）").pack(side="left")
        self.direction_var = self.tk.StringVar(); self.direction_entry = ttk.Entry(fields, textvariable=self.direction_var, width=22); self.direction_entry.pack(side="left", padx=5)
        self.pose_entry.bind("<KeyRelease>", lambda _event: self._update_summary())
        self.direction_entry.bind("<KeyRelease>", lambda _event: self._update_summary())
        self.common_scale_var = self.tk.BooleanVar(value=True)
        ttk.Checkbutton(fields, text="整批同源像素倍率", variable=self.common_scale_var,
                        command=self._toggle_scale_view).pack(side="left", padx=(10, 2))
        ttk.Label(fields, text="仅供可视比较；Alpha框不作身体尺度，K未绑定时不判归一化。").pack(side="left", padx=8)

        body = ttk.Frame(self.root, padding=(8, 0, 8, 0)); body.pack(fill="both", expand=True)
        self.canvas = self.tk.Canvas(body, width=1120, height=800, background="#d7dce1", highlightthickness=1)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.side = ttk.Frame(body, width=320, padding=8); self.side.pack(side="right", fill="y")
        ttk.Label(self.side, text="点选内容", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
        self.mode_var = self.tk.StringVar(value="body")
        for name in ("head", "torso", "body", "root"):
            ttk.Radiobutton(self.side, text=self.TITLES[name], variable=self.mode_var, value=name,
                            command=self._select_mode).pack(anchor="w", pady=2)
        self.mode_note = self.tk.StringVar(); ttk.Label(self.side, textvariable=self.mode_note, wraplength=290).pack(anchor="w", pady=(5, 8))
        ttk.Button(self.side, text="撤销当前类别最后一点", command=self._undo).pack(fill="x", pady=3)
        ttk.Button(self.side, text="清除当前类别点位", command=self._clear_active).pack(fill="x", pady=3)
        ttk.Button(self.side, text="清除当前帧全部点位", command=self._clear_frame).pack(fill="x", pady=3)
        self.coords_var = self.tk.StringVar(value="源像素坐标：移动鼠标查看")
        ttk.Label(self.side, textvariable=self.coords_var).pack(anchor="w", pady=(9, 4))
        self.summary_var = self.tk.StringVar(); ttk.Label(self.side, textvariable=self.summary_var, justify="left", wraplength=290).pack(anchor="w", pady=(4, 10))
        nav = ttk.Frame(self.root, padding=8); nav.pack(fill="x")
        self.out_var = self.tk.StringVar(value=str(self.output_path) if self.output_path else "")
        ttk.Label(nav, text="新 landmarks JSON").pack(side="left")
        ttk.Entry(nav, textvariable=self.out_var, width=88).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(nav, text="选择…", command=self._choose_output).pack(side="left", padx=3)
        self.progress_var = self.tk.StringVar(); ttk.Label(nav, textvariable=self.progress_var, width=20).pack(side="left", padx=8)
        ttk.Button(nav, text="上一槽", command=lambda: self._move(-1)).pack(side="left", padx=3)
        ttk.Button(nav, text="下一槽", command=lambda: self._move(1)).pack(side="left", padx=3)
        ttk.Button(nav, text="保存标注", command=self._save).pack(side="left", padx=3)

    def _current(self):
        return self.records[self.index], self.states[tuple(self.records[self.index]["key"])]

    def _show_current(self):
        record, state = self._current()
        self.pose_var.set(state["pose_group"]); self.direction_var.set(state["direction"])
        key = record["key"]; slot = record["slot"]; w, h = record["pixels"]
        self.title_var.set(f"{key[1]}  {key[2]}_{key[3]}.png    {w}×{h}px    类别={self.manifest['category']} / 范围={record['measurement_scope']}")
        geometry = slot.get("geometry", {})
        self.meta_var.set(f"SHA-256 {record['image_sha256']}    原生 canvas/crop/origin: {geometry.get('canvas_1x')} / {geometry.get('crop_1x')} / {geometry.get('origin_1x')}    {record['image_path']}")
        try:
            with self.Image.open(record["image_path"]) as source:
                source.load(); image = source.convert("RGBA")
        except OSError as error:
            self.messagebox.showerror("无法读取源图", str(error)); return
        self.source_image = image
        self.canvas.delete("all")
        self.root.update_idletasks()
        cw=max(100,self.canvas.winfo_width()-20); ch=max(100,self.canvas.winfo_height()-20)
        if self.common_scale_var.get():
            scale=common_source_pixel_scale(self.records,(cw,ch))
        else:
            scale=min(cw/image.width,ch/image.height)
        dw=max(1,round(image.width*scale)); dh=max(1,round(image.height*scale))
        preview=image.resize((dw,dh),self.Image.Resampling.LANCZOS)
        self.preview= self.ImageTk.PhotoImage(preview)
        self.scale=dw/image.width
        self.offset_x=(self.canvas.winfo_width()-dw)/2
        self.offset_y=(self.canvas.winfo_height()-dh)/2
        self.image_box=(self.offset_x,self.offset_y,self.offset_x+dw,self.offset_y+dh)
        cell=18
        for y in range(int(self.offset_y),int(self.offset_y+dh),cell):
            for x in range(int(self.offset_x),int(self.offset_x+dw),cell):
                shade="#f1f1f1" if ((x-int(self.offset_x))//cell+(y-int(self.offset_y))//cell)%2==0 else "#cfd5db"
                self.canvas.create_rectangle(x,y,min(x+cell,self.offset_x+dw),min(y+cell,self.offset_y+dh),fill=shade,outline="")
        self.image_id=self.canvas.create_image(self.offset_x,self.offset_y,image=self.preview,anchor="nw")
        self._draw_marks(state)
        self._select_mode()
        self.progress_var.set(f"{self.index+1}/{len(self.records)}")
        self._update_summary()

    def _update_summary(self):
        if not hasattr(self,"summary_var") or not self.records:
            return
        record,state=self._current()
        self.summary_var.set(format_source_pixel_measurements(
            state,record["pixels"],self.pose_var.get().strip(),self.direction_var.get().strip()))

    def _toggle_scale_view(self):
        self._store_fields()
        self._show_current()

    def _draw_marks(self, state):
        for name in ("head", "torso", "body"):
            points=state["paths"][name]
            display=[(self.offset_x+x*self.scale,self.offset_y+y*self.scale) for x,y in points]
            color=self.COLORS[name]
            for a,b in zip(display,display[1:]): self.canvas.create_line(*a,*b,fill=color,width=3)
            for i,(x,y) in enumerate(display):
                self.canvas.create_oval(x-5,y-5,x+5,y+5,fill=color,outline="#202020",width=1)
                self.canvas.create_text(x+9,y-9,text=str(i+1),fill=color,anchor="sw",font=("Segoe UI",9,"bold"))
        root=state["root_anchor_pixels"]
        if root is not None:
            x=self.offset_x+root[0]*self.scale; y=self.offset_y+root[1]*self.scale; c=self.COLORS["root"]
            self.canvas.create_line(x-9,y,x+9,y,fill=c,width=3);self.canvas.create_line(x,y-9,x,y+9,fill=c,width=3)
            self.canvas.create_text(x+10,y+10,text="ROOT",fill=c,anchor="nw",font=("Segoe UI",9,"bold"))

    def _select_mode(self):
        self.active=self.mode_var.get();self.mode_note.set("当前模式："+self.TITLES[self.active]+("；点击一次设置接地点" if self.active=="root" else "；连续点击添加折线点"))

    def _on_motion(self,event):
        if not hasattr(self,"image_box"): return
        x0,y0,x1,y1=self.image_box
        x=self.canvas.canvasx(event.x); y=self.canvas.canvasy(event.y)
        if x0<=x<x1 and y0<=y<y1:
            ix=max(0,min(self.source_image.width-1,int((x-x0)/self.scale)))
            iy=max(0,min(self.source_image.height-1,int((y-y0)/self.scale)))
            self.coords_var.set(f"源像素坐标: x={ix}, y={iy}")

    def _on_click(self,event):
        record,state=self._current();x=self.canvas.canvasx(event.x);y=self.canvas.canvasy(event.y)
        x0,y0,x1,y1=self.image_box
        if not (x0<=x<x1 and y0<=y<y1): return
        ix=max(0,min(record["pixels"][0]-1,int((x-x0)/self.scale)))
        iy=max(0,min(record["pixels"][1]-1,int((y-y0)/self.scale)))
        if self.active=="root": state["root_anchor_pixels"]=[ix,iy]
        else: state["paths"][self.active].append([ix,iy])
        self._show_current()

    def _undo(self):
        _,state=self._current()
        if self.active=="root": state["root_anchor_pixels"]=None
        elif state["paths"][self.active]: state["paths"][self.active].pop()
        self._show_current()

    def _clear_active(self):
        _,state=self._current()
        if self.active=="root": state["root_anchor_pixels"]=None
        else: state["paths"][self.active]=[]
        self._show_current()

    def _clear_frame(self):
        _,state=self._current();state["paths"]={"head":[],"torso":[],"body":[]};state["root_anchor_pixels"]=None
        self._show_current()

    def _store_fields(self):
        _,state=self._current();state["pose_group"]=self.pose_var.get().strip();state["direction"]=self.direction_var.get().strip()

    def _move(self,delta):
        self._store_fields();next_index=self.index+delta
        if 0<=next_index<len(self.records): self.index=next_index;self._show_current()

    def _choose_output(self):
        path=self.filedialog.asksaveasfilename(title="另存新的 landmarks JSON",defaultextension=".json",filetypes=[("JSON","*.json")])
        if path:self.out_var.set(path)

    def _save(self):
        self._store_fields()
        try:
            annotations={tuple(record["key"]):self.states[tuple(record["key"])] for record in self.records}
            document=build_document(self.records,annotations)
            target=Path(self.out_var.get().strip())
            if not str(target): raise AnnotationError("请选择新的 JSON 输出路径")
            result_sha=write_new_document(target,document,self.records)
            self.messagebox.showinfo("标注已保存",f"frames={len(document['frames'])}\nSHA-256={result_sha}\n\n源 PNG 未修改。没有 K/注册回执时，报告仍只会给出源像素长度。")
        except (AnnotationError,OSError) as error:
            self.messagebox.showerror("标注未保存",str(error))

    def run(self): self.root.mainloop()


def self_test() -> dict:
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="h3-landmark-selftest-") as temp:
        root=Path(temp);masters=root/"masters"/"TEST";masters.mkdir(parents=True)
        image_path=masters/"0_0.png";Image.new("RGBA",(40,60),(0,0,0,0)).save(image_path)
        inventory=root/"inventory.json.gz";inventory.write_bytes(b"inventory fixture")
        sha=sha256_file(image_path);inv_sha=sha256_file(inventory)
        manifest={"schema":"h3.creature.scale-report-manifest.v1","identity":"TEST","category":"large_creature","masters_dir":str(masters),
                  "native_inventory":{"path":str(inventory),"sha256":inv_sha},
                  "native_slots":[{"key":["def","TEST",0,0],"geometry":{"canvas_1x":[100,80],"crop_1x":[20,30],"origin_1x":[40,20]},"resource":"test.pcx"}],
                  "source_snapshot":[{"path":str(image_path),"sha256":sha,"bytes":image_path.stat().st_size}]}
        manifest_path=root/"audit.json";manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
        loaded,records=load_manifest(manifest_path,["0_0"])
        annotation={tuple(records[0]["key"]):{"pose_group":"standing","direction":"right","paths":{"head":[[20,2],[20,12]],"torso":[[20,12],[20,35]],"body":[[20,2],[20,35],[20,58]]},"root_anchor_pixels":[20,58]}}
        doc=build_document(records,annotation)
        out=root/"landmarks.json";written=write_new_document(out,doc,records)
        assert sha256_file(image_path)==sha
        assert json.loads(out.read_text(encoding="utf-8"))["schema"]==OUTPUT_SCHEMA
        assert written==sha256_file(out)
        view_scale=common_source_pixel_scale([{"pixels":[200,100]},{"pixels":[400,200]}],(200,100))
        assert abs(view_scale-.5)<1e-9
        sample_state={"paths":{"head":[[10,10],[10,20]],"torso":[[10,20],[10,40]],
                               "body":[[10,10],[10,40],[10,60]]}}
        measurement=source_pixel_measurements(sample_state,[40,64])
        assert measurement["lengths_source_pixels"]=={"head":10.0,"torso":20.0,"body":50.0}
        assert measurement["ratios"]=={"head_to_torso":.5,"head_to_body":.2}
        assert measurement["status"]=="MEASURED_SOURCE_PIXELS_ONLY"
        assert measurement["absolute_body_scale_status"]=="NOT_MEASURED"
        try: write_new_document(out,doc,records)
        except AnnotationError: pass
        else: raise AssertionError("output overwrite guard did not fire")
        image_path.write_bytes(b"changed")
        try: write_new_document(root/"drift.json",doc,records)
        except AnnotationError: pass
        else: raise AssertionError("source drift guard did not fire")
    return {"passed":True,"checks":["frozen_inventory_and_source_sha","official_key_scope","manual_points_schema",
        "exclusive_output","source_immutability","source_drift_block","common_source_pixel_scale",
        "source_pixel_measurement_without_k_pass"]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--key",action="append",help="official group_frame slot; repeat to select several")
    parser.add_argument("--pose-group",default="",help="explicit comparable pose class; not guessed")
    parser.add_argument("--direction",default="",help="explicit facing direction; not guessed")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(),ensure_ascii=False));return 0
    if args.manifest is None:
        parser.error("--manifest is required unless --self-test is used")
    try:
        manifest,records=load_manifest(args.manifest,args.key)
        app=Annotator(manifest,records,args.out,args.pose_group,args.direction)
        app.run();return 0
    except AnnotationError as error:
        print(json.dumps({"status":"BLOCKED","code":"CREATURE_LANDMARK_ANNOTATION","error":str(error)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
