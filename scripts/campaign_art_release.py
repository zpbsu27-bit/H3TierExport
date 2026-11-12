"""Build and verify a campaign H3PNGA1 size-comparison candidate.

This tool has no imports from the H3 source tree. Its output is a comparison
artifact only; VCMI MOD delivery uses campaign_vcmi_mod.py and Data3x/Sprites3x.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

HEADER = struct.Struct("<8s4I3Q")
BLOB = struct.Struct("<Q5I")
KEY = struct.Struct("<HI")
MAGIC = b"H3PNGA1\0"
MAX_KEYS = 100_000
MAX_INDEX = 16 * 1024 * 1024
MAX_MEMBER = 64 * 1024 * 1024
MAX_PACK = 2 * 1024 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PCX_KEY = re.compile(r"p/[a-z0-9_-]+\.png\Z")
DEF_KEY = re.compile(r"d/[a-z0-9_-]+\.def/(?:0|[1-9][0-9]*)_(?:0|[1-9][0-9]*)\.png\Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def assert_plain(path: Path, *, missing: bool = False) -> None:
    path = Path(path).absolute()
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("path traverses a filesystem link: " + str(node))
    if not missing or path.exists():
        if not path.is_file():
            raise ValueError("expected a regular file: " + str(path))


def png_geometry(path: Path) -> tuple[int, int]:
    with Path(path).open("rb") as stream:
        header = stream.read(24)
    if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[8:12] != b"\0\0\0\r" or header[12:16] != b"IHDR"):
        raise ValueError("invalid PNG header: " + str(path))
    width, height = struct.unpack_from(">II", header, 16)
    if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > MAX_PIXELS:
        raise ValueError("PNG dimensions exceed pack-reader limits: " + str(path))
    return width, height


def validate_key(value: str) -> str:
    if not isinstance(value, str) or len(value) > 240 or not (PCX_KEY.fullmatch(value) or DEF_KEY.fullmatch(value)):
        raise ValueError("invalid campaign pack key: " + repr(value))
    return value


def load_json(path: Path, maximum: int = 64 * 1024 * 1024) -> tuple[dict, bytes]:
    assert_plain(path)
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise ValueError("JSON exceeds bounded input budget: " + str(path))
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: " + str(path))
    return value, raw


def validate_config(config_path: Path) -> tuple[dict, dict, list[dict]]:
    config, _ = load_json(config_path)
    manifest_path = Path(config["manifest_path"]).resolve()
    manifest, manifest_raw = load_json(manifest_path)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_sha != config.get("expected_manifest_sha256"):
        raise ValueError("campaign manifest SHA mismatch")
    if (manifest.get("schema") != "h3.campaign-art-images3x-pack.v1"
            or manifest.get("status") != "READY" or manifest.get("tier") != 3
            or manifest.get("asset_count") != len(manifest.get("items", []))
            or manifest.get("asset_count") != config.get("expected_asset_count")):
        raise ValueError("campaign manifest contract mismatch")
    allowed_roots = [Path(p).resolve() for p in config.get("allowed_source_roots", [])]
    if not allowed_roots:
        raise ValueError("at least one pinned art source root is required")
    rows = []
    folded = set()
    for entry in manifest["items"]:
        key = validate_key(entry["key"])
        if key.casefold() in folded:
            raise ValueError("duplicate pack key: " + key)
        folded.add(key.casefold())
        source = Path(entry["source"]).resolve()
        if not any(source.is_relative_to(root) for root in allowed_roots):
            raise ValueError("image source is outside configured art roots: " + str(source))
        assert_plain(source)
        expected_sha = entry.get("sha256")
        if not isinstance(expected_sha, str) or not SHA256.fullmatch(expected_sha):
            raise ValueError("invalid source SHA: " + str(source))
        if digest(source) != expected_sha:
            raise ValueError("campaign PNG SHA drift: " + str(source))
        width, height = png_geometry(source)
        native = entry.get("native_pixels")
        if (not isinstance(native, list) or len(native) != 2
                or any(type(n) is not int or n <= 0 for n in native)
                or (width, height) != (native[0] * 3, native[1] * 3)
                or (width, height) != (entry.get("width"), entry.get("height"))):
            raise ValueError("campaign PNG is not exact native 3X: " + str(source))
        size = source.stat().st_size
        if not 0 < size <= MAX_MEMBER:
            raise ValueError("campaign PNG size outside pack limits: " + str(source))
        rows.append({"key": key, "source": source, "sha256": expected_sha,
                     "bytes": size, "width": width, "height": height})
    return config, manifest, rows


def build_pack(output: Path, rows: list[dict]) -> dict:
    output = Path(output).absolute()
    assert_plain(output, missing=True)
    if output.exists():
        raise FileExistsError("refusing to overwrite pack: " + str(output))
    if not rows or len(rows) > MAX_KEYS:
        raise ValueError("invalid campaign image count")
    ordered = sorted(rows, key=lambda row: row["key"])
    blobs = []
    indexes = {}
    key_records = []
    for row in ordered:
        signature = (row["sha256"], row["bytes"], row["width"], row["height"])
        blob_id = indexes.get(signature)
        if blob_id is None:
            blob_id = len(blobs)
            indexes[signature] = blob_id
            blobs.append((row, zlib.crc32(Path(row["source"]).read_bytes())))
        key_records.append((row["key"].encode("ascii"), blob_id))
    index_bytes = len(blobs) * BLOB.size + sum(KEY.size + len(key) for key, _ in key_records)
    data_offset = HEADER.size + index_bytes
    total_bytes = data_offset + sum(row["bytes"] for row, _ in blobs)
    if index_bytes > MAX_INDEX or total_bytes > MAX_PACK:
        raise ValueError("campaign image pack exceeds reader limits")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        raise FileExistsError("partial pack already exists; preserve and inspect: " + str(temporary))
    try:
        with temporary.open("xb") as stream:
            stream.write(HEADER.pack(MAGIC, 1, len(key_records), len(blobs), 0,
                                     index_bytes, data_offset, total_bytes))
            offset = data_offset
            for row, crc in blobs:
                stream.write(BLOB.pack(offset, row["bytes"], row["width"], row["height"], crc, 0))
                offset += row["bytes"]
            for key, blob_id in key_records:
                stream.write(KEY.pack(len(key), blob_id))
                stream.write(key)
            for row, _ in blobs:
                source = Path(row["source"])
                h = hashlib.sha256()
                copied = 0
                with source.open("rb") as incoming:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        stream.write(block)
                        h.update(block)
                        copied += len(block)
                if copied != row["bytes"] or h.hexdigest() != row["sha256"]:
                    raise ValueError("campaign source changed while packing: " + str(source))
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.stat().st_size != total_bytes:
            raise ValueError("pack output byte count mismatch")
        verify_pack(temporary, ordered)
        if output.exists():
            raise FileExistsError("pack destination appeared during build")
        os.rename(temporary, output)
    except BaseException:
        # Retain any partial file as evidence; do not unlink source evidence.
        raise
    return {"format": "H3PNGA1", "version": 1, "keys": len(key_records),
            "blobs": len(blobs), "bytes": output.stat().st_size,
            "sha256": digest(output), "outer_compression": "none",
            "png_reencoded": False, "tier": 3}


def verify_pack(path: Path, rows: list[dict]) -> dict:
    path = Path(path)
    assert_plain(path)
    wanted = {row["key"]: row for row in rows}
    with path.open("rb") as stream:
        length = path.stat().st_size
        raw = stream.read(HEADER.size)
        if len(raw) != HEADER.size:
            raise ValueError("truncated H3PNGA1 header")
        magic, version, keys, blobs, reserved, index_bytes, data_offset, total = HEADER.unpack(raw)
        if (magic != MAGIC or version != 1 or reserved or keys != len(wanted)
                or not blobs or blobs > keys or total != length
                or index_bytes > MAX_INDEX or data_offset != HEADER.size + index_bytes
                or data_offset > length):
            raise ValueError("H3PNGA1 pack header contract mismatch")
        index = stream.read(index_bytes)
        if len(index) != index_bytes or blobs * BLOB.size > len(index):
            raise ValueError("H3PNGA1 index truncated")
        blob_rows = []
        end = data_offset
        for i in range(blobs):
            record = BLOB.unpack_from(index, i * BLOB.size)
            offset, size, width, height, crc, flags = record
            if (offset != end or not 0 < size <= MAX_MEMBER or not 0 < width <= 4096
                    or not 0 < height <= 4096 or width * height > MAX_PIXELS or flags
                    or size > length - offset):
                raise ValueError("H3PNGA1 blob bounds invalid")
            end += size
            blob_rows.append(record)
        if end != length:
            raise ValueError("H3PNGA1 trailing bytes")
        at = blobs * BLOB.size
        found = {}
        for _ in range(keys):
            if at + KEY.size > len(index):
                raise ValueError("H3PNGA1 key record truncated")
            key_bytes, blob_id = KEY.unpack_from(index, at)
            at += KEY.size
            if not key_bytes or key_bytes > 240 or blob_id >= blobs or key_bytes > len(index) - at:
                raise ValueError("H3PNGA1 key record bounds invalid")
            key = index[at:at + key_bytes].decode("ascii")
            at += key_bytes
            validate_key(key)
            if key in found:
                raise ValueError("duplicate H3PNGA1 key")
            found[key] = blob_id
        if at != len(index) or found.keys() != wanted.keys():
            raise ValueError("H3PNGA1 keys differ from frozen campaign manifest")
        verified_blobs = set()
        for key, blob_id in found.items():
            row = wanted[key]
            offset, size, width, height, crc, _ = blob_rows[blob_id]
            if (size, width, height) != (row["bytes"], row["width"], row["height"]):
                raise ValueError("packed image geometry/size mismatch: " + key)
            if blob_id in verified_blobs:
                continue
            stream.seek(offset)
            h = hashlib.sha256()
            remaining = size
            checksum = 0
            head = bytearray()
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("packed image truncated: " + key)
                if len(head) < 24:
                    head.extend(block[:24 - len(head)])
                h.update(block)
                checksum = zlib.crc32(block, checksum)
                remaining -= len(block)
            if h.hexdigest() != row["sha256"] or checksum != crc:
                raise ValueError("packed image SHA/CRC mismatch: " + key)
            if bytes(head[:8]) != b"\x89PNG\r\n\x1a\n" or struct.unpack_from(">II", head, 16) != (width, height):
                raise ValueError("packed PNG signature/geometry mismatch: " + key)
            verified_blobs.add(blob_id)
    return {"verified_keys": len(found), "verified_blobs": len(verified_blobs),
            "bytes": path.stat().st_size, "sha256": digest(path), "passed": True}


def write_json_new(path: Path, value: dict) -> None:
    path = Path(path).absolute()
    assert_plain(path, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_command(config_path: Path) -> dict:
    config, manifest, rows = validate_config(config_path)
    outdir = Path(config["output_dir"]).absolute()
    if outdir.exists():
        raise FileExistsError("campaign pack output directory already exists: " + str(outdir))
    outdir.mkdir(parents=True, exist_ok=False)
    pack_path = outdir / "Images3x.pack"
    result = build_pack(pack_path, rows)
    verify_result = verify_pack(pack_path, rows)
    receipt = {
        "schema": "h3.campaign-art-pack-build-receipt.v1",
        "operation_id": config["operation_id"],
        "status": "PACKED_AND_VERIFIED",
        "manifest_path": str(Path(config["manifest_path"]).resolve()),
        "manifest_sha256": config["expected_manifest_sha256"],
        "asset_count": len(rows),
        "kind_counts": manifest["kind_counts"],
        "class_counts": manifest["class_counts"],
        "pack_path": str(pack_path),
        "pack_relative_mod_path": config["target_mod_relative_path"],
        "pack": result,
        "verification": verify_result,
        "runtime_accepted": False,
        "game_deployed": False,
    }
    write_json_new(outdir / "campaign-pack-receipt.json", receipt)
    return receipt


def verify_command(config_path: Path, pack_path: Path) -> dict:
    config, manifest, rows = validate_config(config_path)
    verified = verify_pack(pack_path, rows)
    return {
        "schema": "h3.campaign-art-pack-verification.v1",
        "operation_id": config["operation_id"],
        "status": "VERIFIED" if verified.get("passed") else "FAILED",
        "manifest_sha256": config["expected_manifest_sha256"],
        "asset_count": len(rows),
        "kind_counts": manifest["kind_counts"],
        "pack_path": str(Path(pack_path).resolve()),
        "pack": verified,
        "runtime_accepted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a SHA-pinned campaign Images3x.pack size-comparison candidate")
    build.add_argument("--config", type=Path, required=True)
    verify = sub.add_parser("verify", help="read back the candidate against the frozen campaign manifest")
    verify.add_argument("--config", type=Path, required=True)
    verify.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_command(args.config) if args.command == "build" else verify_command(args.config, args.pack)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, FileExistsError) as error:
        print("CAMPAIGN_ART_PACK_REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
