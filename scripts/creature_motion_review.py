#!/usr/bin/env python3
"""Freeze an official-slot, SHA-bound creature action-review template.

The template records review evidence only. It does not infer action meaning,
modify PNGs, or decide whether a motion sequence should loop.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import hashlib
import json
from pathlib import Path
import re

from creature_landmark_annotator import AnnotationError, INPUT_SCHEMA, load_manifest, sha256_file

OUTPUT_SCHEMA = "h3.creature.motion-review.v1"
CHECK_NAMES = (
    "identity_continuity",
    "anatomy_continuity",
    "equipment_continuity",
    "color_material_continuity",
    "action_logic",
)


def make_template(manifest_path: Path, selected_group: int | None = None) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    manifest, records = load_manifest(manifest_path)
    groups: dict[int, list[dict]] = {}
    for record in records:
        key = record["key"]
        groups.setdefault(key[2], []).append(record)
    if selected_group is not None and selected_group not in groups:
        raise AnnotationError(f"冻结清单不含官方组 {selected_group}")
    selected = [selected_group] if selected_group is not None else sorted(groups)
    out_groups = []
    for group in selected:
        rows = sorted(groups[group], key=lambda row: row["key"][3])
        if len(rows) < 2:
            raise AnnotationError(f"官方组 {group} 少于两张当前母版，无法构建转场模板")
        keys = [row["key"] for row in rows]
        frame_refs = [{"key": row["key"], "image_sha256": row["image_sha256"]} for row in rows]

        def edge(left: dict, right: dict) -> dict:
            return {
                "from_key": left["key"],
                "from_image_sha256": left["image_sha256"],
                "to_key": right["key"],
                "to_image_sha256": right["image_sha256"],
                "checks": {name: "NOT_MEASURED" for name in CHECK_NAMES},
                "notes": "",
            }

        out_groups.append({
            "group": group,
            "loop_mode": "NOT_MEASURED",
            "ordered_keys": keys,
            "frames": frame_refs,
            "transitions": [edge(a, b) for a, b in zip(rows, rows[1:])],
            "loop_transition": edge(rows[-1], rows[0]),
        })
    return {
        "schema": OUTPUT_SCHEMA,
        "status": "DRAFT / NOT_MEASURED",
        "review_author": None,
        "identity": manifest["identity"],
        "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "native_inventory_sha256": manifest["native_inventory"]["sha256"],
        "review_guidance": (
            "按动作自然顺序查看同组全部官方帧。动作无需复刻官方姿势；判断人物身份、解剖、装备/材质"
            "是否连贯，以及动作自身是否合理。loop_mode 必须人工填写 loop 或 non_loop。"
            "每个 PASS/RISK/FAIL 都绑定相邻帧 SHA；RISK/FAIL 必须写明观察到的问题。"
            "模板中的 NOT_MEASURED 不代表通过。"
        ),
        "groups": out_groups,
    }


def freeze(manifest: Path, operation_id: str, selected_group: int | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", operation_id):
        raise AnnotationError("operation-id 仅允许 1–64 个字母、数字、下划线或短横线")
    product = resolve_tool_root(__file__)
    base = product / "build" / "experiments" / "motion-review-requests"
    operation = base / operation_id
    template = make_template(manifest, selected_group)
    base.mkdir(parents=True, exist_ok=True)
    operation.mkdir(exist_ok=False)
    output = operation / "motion-review-template.json"
    payload = (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with output.open("xb") as stream:
        stream.write(payload)
    return {
        "status": "DRAFT_NOT_MEASURED",
        "operation_id": operation_id,
        "identity": template["identity"],
        "groups": [row["group"] for row in template["groups"]],
        "transition_count": sum(len(row["transitions"]) + 1 for row in template["groups"]),
        "template": str(output),
        "manifest_sha256": template["manifest"]["sha256"],
        "template_sha256": hashlib.sha256(payload).hexdigest(),
        "source_images_modified": False,
        "images_written": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help=f"SHA-frozen {INPUT_SCHEMA} produced by scale-audit freeze")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--group", type=int,
                        help="Optional official DEF group; omitted means all groups in this identity")
    args = parser.parse_args(argv)
    try:
        result = freeze(args.manifest, args.operation_id, args.group)
    except (AnnotationError, FileExistsError, OSError) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
