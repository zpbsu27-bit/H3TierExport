#!/usr/bin/env python3
"""SHA-bound official DEF pose guides. Review only; never writes art masters."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
import homm3data.deffile as deffile

from native_h3_io import mapped_lod, payload, parse_def, decode_native_pixels
from creature_master_set import read_json as read_standard

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "build" / "experiments" / "creature-pose-guide"
REQUESTS = EXPERIMENTS / "requests"
SCHEMA = "h3.creature.native-pose-guide.request.v1"


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_bound(path, sha):
    path = Path(path).resolve(strict=True)
    if digest_file(path) != sha.lower():
        raise ValueError("SOURCE_SHA_DRIFT: " + str(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bind(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": digest_file(path)}


def ensure_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("OPERATION_ID_INVALID")


def class_groups(slots):
    by_offset = defaultdict(list)
    for row in slots:
        by_offset[row["offset"]].append(row)
    return by_offset


def equivalent_except_shadow(reference, normal):
    a, b = np.asarray(reference.convert("RGBA")), np.asarray(normal.convert("RGBA"))
    if a.shape != b.shape:
        return False
    diff = np.any(a != b, axis=2)
    # The existing official 1X reader preserves palette shadows which
    # homm3data's normal renderer drops. Do not discard reference shadows.
    allowed = ((a[:, :, 0] == 0) & (a[:, :, 1] == 0) &
               (a[:, :, 2] == 0) & np.isin(a[:, :, 3], [64, 128]) &
               (b[:, :, 3] == 0))
    return bool(np.all(~diff | allowed))


def same_geometry(native, row):
    head = native["frame_header"]
    expected = row["geometry"]
    return (list(head[2:4]) == expected["canvas_1x"] and
            list(head[4:6]) == expected["crop_1x"] and
            list(head[6:8]) == expected["origin_1x"] and
            native["resource"].lower() == row["resource"].lower())


def reference_matches_offset(ref, row, native_slots, checkpoint, identity):
    slot = ref.get("reference_slot")
    if not isinstance(slot, list) or len(slot) != 2:
        return False
    owner = native_slots.get(tuple(slot))
    ck = checkpoint.get("images", {}).get(f"def:{identity}:{slot[0]}:{slot[1]}")
    return bool(owner and owner["offset"] == row["offset"] and ck and
                ck.get("sha256") == ref.get("sha256") and
                Path(ref.get("path", "")).name == f"{slot[0]}_{slot[1]}.png")


def native_blob(lod_path, identity):
    with mapped_lod(lod_path) as archive:
        entry = next((e for e in archive.entries if e["name"] == identity + ".DEF"), None)
        if entry is None:
            raise ValueError("DEF_NOT_IN_LOD")
        return payload(entry)


def freeze(args):
    ensure_id(args.operation_id)
    audit = read_bound(args.audit_manifest, args.audit_sha256)
    checkpoint = read_bound(args.checkpoint, args.checkpoint_sha256)
    standard, standard_sha = read_standard(Path(args.art_standard), args.art_standard_sha256)
    identity = audit["identity"]
    if audit.get("schema") != "h3.creature.scale-report-manifest.v1" or not re.fullmatch(r"[A-Z0-9_]+", identity):
        raise ValueError("AUDIT_MANIFEST_INVALID")
    lod = bind(args.lod)
    if lod["sha256"] != args.lod_sha256.lower():
        raise ValueError("LOD_SHA_DRIFT")
    inventory = audit["native_inventory"]
    if digest_file(inventory["path"]) != inventory["sha256"]:
        raise ValueError("INVENTORY_SHA_DRIFT")
    blob = native_blob(lod["path"], identity)
    if digest_bytes(blob) != checkpoint["source_payload_sha256"]:
        raise ValueError("CHECKPOINT_DEF_SHA_DRIFT")
    lod_input = [x for x in checkpoint["inputs"] if Path(x["path"]).name.lower() == Path(lod["path"]).name.lower()]
    if len(lod_input) != 1 or lod_input[0]["sha256"] != lod["sha256"]:
        raise ValueError("CHECKPOINT_LOD_SHA_DRIFT")
    parsed = parse_def(blob)
    parsed_by_key = {(s["group_id"], s["frame_index"]): s for s in parsed["slots"]}
    audit_slots = audit["native_slots"]
    if len(parsed_by_key) != len(audit_slots):
        raise ValueError("OFFICIAL_SLOT_COUNT_DRIFT")
    entries = []
    snapshot = {Path(x["path"]).name: x for x in audit["source_snapshot"]}
    for row in audit_slots:
        key = row["key"]
        if key[:2] != ["def", identity] or Path(row["native_lod"]).name.lower() != Path(lod["path"]).name.lower():
            raise ValueError("SLOT_IDENTITY_OR_LOD_DRIFT")
        official = parsed_by_key.get((key[2], key[3]))
        if official is None or not same_geometry(official, row):
            raise ValueError("OFFICIAL_GEOMETRY_OR_NAME_DRIFT")
        name = f"{key[2]}_{key[3]}.png"
        master = snapshot.get(name)
        if master and digest_file(master["path"]) != master["sha256"]:
            raise ValueError("MASTER_SHA_DRIFT")
        entries.append({"group": key[2], "frame": key[3], "offset": official["offset"],
                        "resource": official["resource"], "geometry": row["geometry"],
                        "master": master, "reference": None})
    classes = class_groups(entries)
    refs = Path(args.refs_dir).resolve(strict=True)
    with deffile.open(io.BytesIO(blob)) as decoder:
        raw_map = {(x["group_id"], x["image_id"]): x for x in decoder.get_raw_data()}
        for offset, members in classes.items():
            choices = []
            for row in members:
                p = refs / f"{row['group']}_{row['frame']}.png"
                ck = checkpoint["images"].get(f"def:{identity}:{row['group']}:{row['frame']}")
                if p.is_file() and ck:
                    if digest_file(p) != ck["sha256"]:
                        raise ValueError("REFERENCE_SHA_DRIFT")
                    choices.append((p, row))
            if not choices:
                raise ValueError(f"NO_CHECKPOINT_REFERENCE_FOR_OFFSET: {offset}")
            path, owner = choices[0]
            original = parsed_by_key[(owner["group"], owner["frame"])]
            if decode_native_pixels(blob, original) != raw_map[(owner["group"], owner["frame"])]["image"]["pixeldata"]:
                raise ValueError("INDEPENDENT_INDEX_DECODE_MISMATCH")
            with Image.open(path) as source:
                rgba = source.convert("RGBA")
            comparison = decoder.read_image(how="normal", group_id=owner["group"], image_id=owner["frame"]).convert("RGBA")
            if not equivalent_except_shadow(rgba, comparison):
                raise ValueError("REFERENCE_PIXELS_DISAGREE_WITH_DEF")
            ref = {**bind(path), "reference_slot": [owner["group"], owner["frame"]],
                   "pixel_comparison": "EQUAL_EXCEPT_PRESERVED_BLACK_SHADOW_ALPHA"}
            for member in members:
                member["reference"] = ref
    request = {"schema": SCHEMA, "operation_id": args.operation_id, "identity": identity,
               "audit": bind(args.audit_manifest), "inventory": inventory, "lod": lod,
               "def_sha256": digest_bytes(blob), "checkpoint": bind(args.checkpoint),
               "art_standard": {"path": str(Path(args.art_standard).resolve()), "sha256": standard_sha,
                                "version": standard["version"]},
               "implementation": [bind(__file__), bind(Path(__file__).with_name("native_h3_io.py")),
                                  bind(Path(__file__).with_name("build_official_def_frame_map.py"))],
               "slots": entries, "output_status": "REVIEW_ONLY",
               "body_landmarks": "NOT_MEASURED", "native_K": "NOT_MEASURED",
               "automatic_art_approval": False}
    REQUESTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).resolve()
    if out.parent != REQUESTS.resolve() or out.name != args.operation_id + ".json":
        raise ValueError("REQUEST_PATH_OUTSIDE_OPERATION_ROOT")
    with out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(request, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "FROZEN", "manifest": str(out), "sha256": digest_file(out),
            "slots": len(entries), "native_offset_classes": len(classes)}


def validate(args):
    ensure_id(args.operation_id)
    request = read_bound(args.manifest, args.manifest_sha256)
    if request.get("schema") != SCHEMA or request.get("operation_id") != args.operation_id:
        raise ValueError("REQUEST_ID_OR_SCHEMA_INVALID")
    out = Path(args.outdir).resolve()
    if out.parent != EXPERIMENTS.resolve() or out.name != args.operation_id:
        raise ValueError("OUTPUT_PATH_OUTSIDE_OPERATION_ROOT")
    for item in [request["audit"], request["inventory"], request["lod"], request["checkpoint"],
                 request["art_standard"], *request["implementation"]]:
        if digest_file(item["path"]) != item["sha256"]:
            raise ValueError("BOUND_INPUT_SHA_DRIFT: " + str(item["path"]))
    blob = native_blob(request["lod"]["path"], request["identity"])
    if digest_bytes(blob) != request["def_sha256"]:
        raise ValueError("DEF_SHA_DRIFT")
    raw_slots = {(r["group_id"], r["frame_index"]): r for r in parse_def(blob)["slots"]}
    checkpoint = read_bound(request["checkpoint"]["path"], request["checkpoint"]["sha256"])
    audit = read_bound(request["audit"]["path"], request["audit"]["sha256"])
    audit_slots = {(r["key"][2], r["key"][3]): r for r in audit["native_slots"]}
    snapshots = {Path(x["path"]).name: x for x in audit["source_snapshot"]}
    if len(raw_slots) != len(request["slots"]):
        raise ValueError("DEF_SLOT_COUNT_DRIFT")
    refs, masters, seen = set(), set(), set()
    for row in request["slots"]:
        key = (row["group"], row["frame"])
        if key in seen or key not in audit_slots:
            raise ValueError("DUPLICATE_OR_UNFROZEN_SLOT")
        seen.add(key)
        source = raw_slots.get(key)
        expected = {"geometry": row["geometry"], "resource": row["resource"]}
        if (source is None or source["offset"] != row["offset"] or not same_geometry(source, expected)
                or row["geometry"] != audit_slots[key]["geometry"]
                or row["resource"].lower() != audit_slots[key]["resource"].lower()):
            raise ValueError("DEF_SLOT_DRIFT")
        ref = row["reference"]
        if not reference_matches_offset(ref, row, raw_slots, checkpoint, request["identity"]):
            raise ValueError("REFERENCE_OFFSET_OR_CHECKPOINT_DRIFT")
        if ref["path"] not in refs:
            if digest_file(ref["path"]) != ref["sha256"]:
                raise ValueError("REFERENCE_SHA_DRIFT")
            refs.add(ref["path"])
        master = row["master"]
        if master != snapshots.get(f"{row['group']}_{row['frame']}.png"):
            raise ValueError("MASTER_SNAPSHOT_DRIFT")
        if master and master["path"] not in masters:
            if digest_file(master["path"]) != master["sha256"]:
                raise ValueError("MASTER_SHA_DRIFT")
            masters.add(master["path"])
    return request, out


def common_box(request):
    boxes = []
    seen = set()
    for row in request["slots"]:
        ref = row["reference"]["path"]
        if ref in seen:
            continue
        seen.add(ref)
        with Image.open(ref) as im:
            box = im.convert("RGBA").getchannel("A").getbbox()
            if box:
                boxes.append(box)
    if not boxes:
        raise ValueError("EMPTY_OFFICIAL_REFERENCES")
    width, height = request["slots"][0]["geometry"]["canvas_1x"]
    if any(r["geometry"]["canvas_1x"] != [width, height] for r in request["slots"]):
        raise ValueError("MULTIPLE_NATIVE_CANVAS_SIZES")
    return [max(0, min(b[0] for b in boxes)-5), max(0, min(b[1] for b in boxes)-5),
            min(width, max(b[2] for b in boxes)+5), min(height, max(b[3] for b in boxes)+5)]


def guide_image(row, box):
    with Image.open(row["reference"]["path"]) as src:
        ref = src.convert("RGBA")
    # Grid and silhouette are visualization aids in a fixed native coordinate
    # system, not landmarks, an art master, or a measurement.
    grid = Image.new("RGBA", ref.size, "#eef3fa")
    dr = ImageDraw.Draw(grid)
    for x in range(0, ref.width, 10):
        dr.line((x, 0, x, ref.height), fill=(202, 213, 227, 255))
    for y in range(0, ref.height, 10):
        dr.line((0, y, ref.width, y), fill=(202, 213, 227, 255))
    grid.alpha_composite(ref)
    mask = ref.getchannel("A").point(lambda x: 255 if x >= 200 else 0)
    edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3)))
    tint = Image.new("RGBA", ref.size, (215, 50, 50, 255))
    grid.paste(tint, (0, 0), edge)
    guide = grid.crop(tuple(box))
    return guide.resize((guide.width * 2, guide.height * 2), Image.Resampling.NEAREST).convert("RGB")


def png_bytes(im):
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def render_outputs(request):
    box = common_box(request)
    groups = defaultdict(list)
    generated = {}
    for row in sorted(request["slots"], key=lambda x: (x["group"], x["frame"])):
        image = guide_image(row, box)
        name = f"guides/{row['group']}_{row['frame']}.png"
        generated[name] = png_bytes(image)
        groups[row["group"]].append((row, image))
    for group, items in groups.items():
        width = max(img.width for _, img in items)
        height = max(img.height for _, img in items)
        sheet = Image.new("RGB", (width * len(items), height + 22), "#eef3fa")
        draw = ImageDraw.Draw(sheet)
        for i, (row, img) in enumerate(items):
            draw.text((i * width + 3, 3), f"{group}_{row['frame']} native 1X x2", fill="#202020")
            sheet.paste(img, (i * width, 22))
        generated[f"groups/{group}.png"] = png_bytes(sheet)
    return box, generated


def run(request, out):
    if out.exists():
        raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
    box, outputs = render_outputs(request)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for rel, data in sorted(outputs.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        records.append({"path": rel, "sha256": digest_bytes(data), "bytes": len(data)})
    receipt = {"schema": "h3.creature.native-pose-guide.receipt.v1", "operation_id": request["operation_id"],
               "identity": request["identity"], "native_window_1x": box, "display_scale": 2,
               "guides": len(request["slots"]), "groups": len({s["group"] for s in request["slots"]}),
               "outputs": records, "body_root": "NOT_MEASURED", "landmarks": "NOT_MEASURED",
               "master_K": "NOT_MEASURED", "review_only": True, "art_approved": False,
               "source_images_modified": False}
    with (out / "receipt.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": "REVIEW_GUIDES_CREATED", "receipt": str(out / "receipt.json"),
            "receipt_sha256": digest_file(out / "receipt.json"), "guides": receipt["guides"],
            "groups": receipt["groups"], "root_and_landmarks": "NOT_MEASURED"}


def verify(request, out):
    receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("schema") != "h3.creature.native-pose-guide.receipt.v1" or receipt.get("operation_id") != request["operation_id"]:
        raise ValueError("RECEIPT_INVALID")
    box, outputs = render_outputs(request)
    expected = {r["path"]: r for r in receipt["outputs"]}
    if box != receipt["native_window_1x"] or set(expected) != set(outputs):
        raise ValueError("OUTPUT_SET_DRIFT")
    for rel, data in outputs.items():
        path = (out / rel).resolve()
        if not path.is_relative_to(out) or not path.is_file():
            raise ValueError("OUTPUT_PATH_INVALID")
        if digest_bytes(data) != expected[rel]["sha256"] or digest_file(path) != expected[rel]["sha256"]:
            raise ValueError("OUTPUT_REPLAY_MISMATCH: " + rel)
    return {"status": "VERIFIED_REVIEW_ONLY", "slots": len(request["slots"]),
            "outputs": len(outputs), "receipt_sha256": digest_file(out / "receipt.json"),
            "art_approved": False, "root_and_landmarks": "NOT_MEASURED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    for name in ("audit-manifest", "audit-sha256", "lod", "lod-sha256", "checkpoint", "checkpoint-sha256",
                 "refs-dir", "art-standard", "art-standard-sha256", "operation-id", "out"):
        f.add_argument("--" + name, required=True)
    for action in ("check", "run", "verify"):
        p = sub.add_parser(action)
        for name in ("manifest", "manifest-sha256", "outdir", "operation-id"):
            p.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            result = freeze(args)
        else:
            request, out = validate(args)
            if args.action == "check":
                if out.exists():
                    raise ValueError("OUTPUT_OPERATION_ALREADY_EXISTS")
                result = {"status": "READY_REVIEW_ONLY", "slots": len(request["slots"]),
                          "root_and_landmarks": "NOT_MEASURED"}
            elif args.action == "run":
                result = run(request, out)
            else:
                result = verify(request, out)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, StopIteration) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
