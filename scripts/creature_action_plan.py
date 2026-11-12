"""Build a source-bound queue of native Heroes III creature action slots.

This companion does not render, interpolate, decode, or package art. It uses
the SHA-pinned native resource inventory as its only slot authority and leaves
all source bindings and keyframes unresolved for a later motion provider.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


INVENTORY_SCHEMA = "h3.complete-art-catalog.v3"
OUTPUT_SCHEMA = "h3.creature.action-plan.v1"
ERROR_SCHEMA = "h3.creature.action-plan.error.v1"
IDENTITY_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$", re.ASCII)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$", re.ASCII)

# The native DEF action group indexes are fixed. Group 6 remains the native
# unused-death slot (VCMI calls it remote death); 9 and 10 remain native turn
# groups even though VCMI does not use them. No VCMI-only groups are invented.
ACTION_NAMES = (
    "move",
    "pointer_over",
    "stand",
    "hit",
    "defend",
    "death",
    "unused_death",
    "turn_left",
    "turn_right",
    "turn_left_alt",
    "turn_right_alt",
    "attack_up",
    "attack_front",
    "attack_down",
    "shoot_up",
    "shoot_front",
    "shoot_down",
    "double_hex_or_spell_attack_up",
    "double_hex_or_spell_attack_front",
    "double_hex_or_spell_attack_down",
    "move_start",
    "move_end",
)


class PlanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_identity(value: str) -> str:
    if not IDENTITY_RE.fullmatch(value):
        raise PlanError("IDENTITY_INVALID", f"unsafe creature identity: {value!r}")
    return value.upper()


def integer_pair(value: Any, field: str, *, positive: bool) -> list[int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(part) is not int for part in value)
            or (positive and any(part <= 0 for part in value))):
        qualifier = "positive " if positive else "integer "
        raise PlanError("GEOMETRY_INVALID", f"{field} must be a 2-item {qualifier}array")
    return list(value)


def validate_geometry(row: dict[str, Any], key: tuple[str, int, int]) -> tuple[list[int], list[int], list[int]]:
    canvas = integer_pair(row.get("canvas"), f"{key}.canvas", positive=True)
    crop = integer_pair(row.get("crop"), f"{key}.crop", positive=True)
    anchor = integer_pair(row.get("anchor"), f"{key}.anchor", positive=False)
    if any(crop[n] > canvas[n] for n in range(2)):
        raise PlanError("GEOMETRY_INVALID", f"crop exceeds canvas for {key}")
    if any(anchor[n] < 0 or anchor[n] + crop[n] > canvas[n] for n in range(2)):
        raise PlanError("GEOMETRY_INVALID", f"crop anchor is outside canvas for {key}")
    return canvas, crop, anchor


def load_catalog(inventory_path: Path, expected_sha: str) -> tuple[dict[str, Any], str]:
    if not SHA256_RE.fullmatch(expected_sha):
        raise PlanError("INVENTORY_SHA_INVALID", "--inventory-sha256 must contain 64 hexadecimal characters")
    try:
        inventory = inventory_path.resolve(strict=True)
    except OSError as exc:
        raise PlanError("INVENTORY_UNAVAILABLE", f"inventory is unavailable: {exc}") from exc
    if not inventory.is_file():
        raise PlanError("INVENTORY_UNAVAILABLE", "inventory path is not a file")
    try:
        actual_sha = sha256_file(inventory)
    except OSError as exc:
        raise PlanError("INVENTORY_UNREADABLE", f"cannot hash inventory bytes: {exc}") from exc
    if actual_sha.lower() != expected_sha.lower():
        raise PlanError("INVENTORY_SHA_MISMATCH", "inventory bytes do not match the pinned SHA256")
    try:
        with gzip.open(inventory, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError("INVENTORY_UNREADABLE", f"cannot read gzip JSON inventory: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != INVENTORY_SCHEMA:
        raise PlanError("INVENTORY_SCHEMA_UNSUPPORTED", f"inventory schema must be {INVENTORY_SCHEMA}")
    if not isinstance(document.get("rows"), list):
        raise PlanError("INVENTORY_SCHEMA_INVALID", "inventory rows must be an array")
    return document, actual_sha.lower()


def build_plan(document: dict[str, Any], inventory_sha: str,
               requested: list[str], inventory_path: Path) -> dict[str, Any]:
    identities = [safe_identity(value) for value in requested]
    if not 1 <= len(identities) <= 32:
        raise PlanError("IDENTITY_COUNT_INVALID", "provide between 1 and 32 --identity values")
    if len(set(identities)) != len(identities):
        raise PlanError("IDENTITY_DUPLICATE", "duplicate --identity values are not allowed")

    selected = set(identities)
    rows_by_identity: dict[str, list[tuple[int, dict[str, Any]]]] = {name: [] for name in identities}
    seen_keys: set[tuple[str, int, int]] = set()
    for native_order, row in enumerate(document["rows"]):
        if not isinstance(row, dict):
            continue
        if row.get("category") != "creatures" or row.get("kind") != "def":
            continue
        raw_identity = row.get("identity")
        if not isinstance(raw_identity, str):
            continue
        identity = safe_identity(raw_identity)
        if identity not in selected:
            continue
        group, frame = row.get("group"), row.get("frame")
        if type(group) is not int or not 0 <= group <= 21:
            raise PlanError("ACTION_GROUP_UNSUPPORTED", f"native group must be in 0..21 for {identity}")
        if type(frame) is not int or frame < 0:
            raise PlanError("FRAME_INDEX_INVALID", f"native frame must be a nonnegative integer for {identity}")
        key = (identity, group, frame)
        if key in seen_keys:
            raise PlanError("NATIVE_KEY_DUPLICATE", f"duplicate native DEF key: {key}")
        seen_keys.add(key)
        canvas, crop, anchor = validate_geometry(row, key)
        rows_by_identity[identity].append((native_order, {
            "key": {"kind": "def", "identity": identity, "group": group, "frame": frame},
            "native_order": native_order,
            "canvas": canvas,
            "crop": crop,
            "anchor": anchor,
            "target_3x": {
                "canvas": [part * 3 for part in canvas],
                "crop": [part * 3 for part in crop],
                "anchor": [part * 3 for part in anchor],
            },
            "source_binding": None,
            "keyframe": None,
        }))

    missing = [identity for identity in identities if not rows_by_identity[identity]]
    if missing:
        raise PlanError("IDENTITY_NOT_FOUND", "no category=creatures kind=def rows for: " + ", ".join(missing))

    units = []
    for identity in identities:
        ordered_slots = rows_by_identity[identity]
        by_group: dict[int, list[dict[str, Any]]] = {group: [] for group in range(22)}
        for _, slot in ordered_slots:
            by_group[slot["key"]["group"]].append(slot)
        action_groups = []
        for group in range(22):
            slots = by_group[group]
            present = bool(slots)
            action_groups.append({
                "group": group,
                "action": ACTION_NAMES[group],
                "actual_present": present,
                "absent": not present,
                "frame_count": len(slots),
                "slots": slots,
                "frame_duration_ms": None,
                "loop": None,
            })
        units.append({"identity": identity, "action_groups": action_groups})

    return {
        "schema": OUTPUT_SCHEMA,
        "status": "PLANNED",
        "inventory": {
            "path": str(inventory_path.resolve()),
            "sha256": inventory_sha,
            "schema": document["schema"],
        },
        "unit_count": len(units),
        "native_action_group_count": 22,
        "units": units,
        "limits": {
            "source_binding_required": True,
            "keyframe_required": True,
            "frame_duration_known": False,
            "loop_known": False,
            "group_6_note": "native unused-death slot; VCMI names it remote death",
            "groups_9_10_note": "native turn groups; VCMI does not use these groups",
            "vcmi_extension_groups_added": False,
            "frames_or_images_generated": False,
            "def_or_game_written": False,
        },
    }


def prepare_output_path(output_arg: str, inventory_path: Path) -> Path:
    output = Path(output_arg).expanduser()
    if output.suffix.lower() != ".json":
        raise PlanError("OUTPUT_EXTENSION_INVALID", "--out must name a .json file")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as exc:
        raise PlanError("OUTPUT_PARENT_UNAVAILABLE", f"output parent directory is unavailable: {exc}") from exc
    if not parent.is_dir():
        raise PlanError("OUTPUT_PARENT_UNAVAILABLE", "output parent is not a directory")
    candidate = parent / output.name
    if candidate.exists():
        raise PlanError("OUTPUT_ALREADY_EXISTS", "refusing to overwrite an existing output path")
    if candidate.resolve(strict=False) == inventory_path.resolve(strict=True):
        raise PlanError("OUTPUT_EQUALS_INVENTORY", "output path must differ from inventory path")
    return candidate


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan native DEF action slots for creature motion providers")
    parser.add_argument("--inventory", required=True, help="gzip JSON native resource inventory")
    parser.add_argument("--inventory-sha256", required=True, help="pinned SHA256 of inventory bytes")
    parser.add_argument("--identity", action="append", required=True,
                        help="creature DEF identity; repeat once per unit (1..32 total)")
    parser.add_argument("--out", required=True, help="new .json output path; existing files are refused")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        document, actual_sha = load_catalog(Path(args.inventory), args.inventory_sha256)
        output_path = prepare_output_path(args.out, Path(args.inventory))
        plan = build_plan(document, actual_sha, args.identity, Path(args.inventory))
        payload = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        # Exclusive creation closes the preflight/write race without ever
        # replacing a file another process created after our initial check.
        try:
            with output_path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError as exc:
            raise PlanError("OUTPUT_ALREADY_EXISTS", "refusing to overwrite an existing output path") from exc
        except OSError as exc:
            raise PlanError("OUTPUT_WRITE_FAILED", f"could not write new output JSON: {exc}") from exc
    except PlanError as exc:
        error = {"schema": ERROR_SCHEMA, "status": "REFUSED", "code": exc.code, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps({"schema": OUTPUT_SCHEMA, "status": "PLANNED", "out": str(output_path),
                      "unit_count": plan["unit_count"],
                      "slot_count": sum(group["frame_count"] for unit in plan["units"]
                                        for group in unit["action_groups"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
