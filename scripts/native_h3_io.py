"""Self-contained Heroes III Complete LOD/DEF/PCX native readers for H3TierExport.

Provides read-only native archive, DEF frame, indexed pixel, and PCX decoders
with zero dependency on the H3 C++ project tree.
"""
from __future__ import annotations

import hashlib
import io
import mmap
import os
from pathlib import Path
import struct
from typing import Any
import zlib

from PIL import Image
import homm3data.deffile as deffile
import homm3data.lodfile as lodfile

from build_official_def_frame_map import parse_def, read_lod
from workspace import asset_root

HEADER_BYTES = 92
INDEX_BYTES = 32
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_LOD_ENTRIES = MAX_INDEX_BYTES // INDEX_BYTES
MAX_PIXELS = 16 * 1024 * 1024


def sha256_file(path: Path | str) -> str:
    with Path(path).open("rb", buffering=0) as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _readinto_exact(source, target: bytearray) -> None:
    view = memoryview(target)
    done = 0
    try:
        while done < len(view):
            count = source.readinto(view[done:])
            if not count:
                raise RuntimeError("truncated LOD directory")
            done += count
    finally:
        view.release()


class MappedLOD:
    """Context-managed LOD whose entry raw payloads are zero-copy views."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._file = None
        self._mapping = None
        self._root = None
        self._payload_views: list[memoryview] = []
        self.head: bytes | None = None
        self.entries: list[dict[str, Any]] | None = None
        self.bytes = 0
        self.count = 0
        self.index_end = 0

    def __enter__(self) -> MappedLOD:
        source = self.path.open("rb", buffering=0)
        self._file = source
        try:
            size = os.fstat(source.fileno()).st_size
            if size < HEADER_BYTES:
                raise RuntimeError("truncated LOD header")
            header = bytearray(HEADER_BYTES)
            source.seek(0)
            _readinto_exact(source, header)
            count = struct.unpack_from("<I", header, 8)[0]
            if count > MAX_LOD_ENTRIES:
                raise RuntimeError("LOD entry count exceeds safety limit")
            index_bytes = count * INDEX_BYTES
            if index_bytes > size - HEADER_BYTES:
                raise RuntimeError("truncated LOD index")
            directory = bytearray(index_bytes)
            if index_bytes:
                _readinto_exact(source, directory)
            directory_view = memoryview(directory)
            metadata = []
            seen_names = set()
            try:
                index_end = HEADER_BYTES + index_bytes
                for i in range(count):
                    off = i * INDEX_BYTES
                    rawname = bytes(directory_view[off:off + 16])
                    try:
                        name = rawname.split(b"\0", 1)[0].decode("ascii").upper()
                    except UnicodeDecodeError as error:
                        raise RuntimeError("LOD entry name is not ASCII") from error
                    if not name:
                        raise RuntimeError("LOD entry name is empty")
                    if name in seen_names:
                        raise RuntimeError("duplicate LOD names")
                    seen_names.add(name)
                    pos, decoded_size, typ, packed = struct.unpack_from("<IIII", directory_view, off + 16)
                    stored = packed or decoded_size
                    if pos < index_end or pos + stored > size:
                        raise RuntimeError("invalid LOD entry " + name)
                    metadata.append({
                        "index": i,
                        "name_raw": rawname,
                        "name": name,
                        "offset": pos,
                        "size": decoded_size,
                        "type": typ,
                        "packed": packed,
                        "stored": stored,
                    })
            finally:
                directory_view.release()

            mapping = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
            root = memoryview(mapping)
            payload_views = []
            entries = []
            for item in metadata:
                raw = root[item["offset"]:item["offset"] + item["stored"]]
                payload_views.append(raw)
                entry = dict(item)
                entry["raw"] = raw
                entries.append(entry)

            self._mapping = mapping
            self._root = root
            self._payload_views = payload_views
            self.head = bytes(header)
            self.entries = entries
            self.bytes = size
            self.count = count
            self.index_end = HEADER_BYTES + index_bytes
            return self
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for view in self._payload_views:
            try:
                view.release()
            except ValueError:
                pass
        self._payload_views = []
        if self._root is not None:
            try:
                self._root.release()
            except ValueError:
                pass
            self._root = None
        if self._mapping is not None:
            mapping = self._mapping
            self._mapping = None
            try:
                mapping.close()
            except BufferError:
                pass
        if self._file is not None:
            self._file.close()
            self._file = None

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def mapped_lod(path: Path | str) -> MappedLOD:
    return MappedLOD(path)


def payload(entry: dict[str, Any]) -> bytes:
    if entry["size"] > 64 * 1024 * 1024:
        raise ValueError("LOD member too large")
    raw = zlib.decompress(entry["raw"]) if entry["packed"] else bytes(entry["raw"])
    if len(raw) != entry["size"]:
        raise ValueError("LOD member decoded length mismatch")
    return raw


def decode_body(data: bytes, w: int, h: int, fmt: int) -> bytes:
    if fmt == 1:
        offsets = struct.unpack_from("<" + "I" * h, data)
        sizes = [w] * h
    else:
        n = h if fmt == 2 else h * (w // 32)
        offsets = struct.unpack_from("<" + "H" * n, data)
        sizes = [w if fmt == 2 else 32] * n
    result = bytearray()
    for start, limit in zip(offsets, sizes):
        at = start
        row = bytearray()
        while len(row) < limit:
            c = data[at]
            at += 1
            if fmt == 1:
                ln = data[at] + 1
                at += 1
                literal = c == 255
            else:
                ln = (c & 31) + 1
                c >>= 5
                literal = c == 7
            if literal:
                row.extend(data[at:at + ln])
                at += ln
            else:
                row.extend(bytes([c]) * ln)
        if len(row) != limit:
            raise ValueError("decode overrun")
        result.extend(row)
    return bytes(result)


def decode_native_pixels(def_blob: bytes, frame: dict[str, Any]) -> bytes:
    """Decode one native DEF frame to exact palette indexes."""
    header = frame.get("frame_header")
    if not isinstance(header, (tuple, list)) or len(header) != 8:
        raise ValueError("invalid_frame_header")
    body_size, codec, _, _, width, height, _, _ = map(int, header)
    if body_size < 0 or width <= 0 or height <= 0 or width * height > 512 * 1024 * 1024:
        raise ValueError("invalid_pixel_geometry")
    offset = int(frame.get("offset", -1))
    body_start = offset + 32
    body_end = body_start + body_size
    if offset < 0 or body_start < 32 or body_end > len(def_blob):
        raise ValueError("frame_body_out_of_range")
    body = def_blob[body_start:body_end]
    if codec == 0:
        pixels = body
    elif codec in (1, 2, 3):
        if codec == 3 and width % 32:
            raise ValueError("format3_width_not_multiple_of_32")
        pixels = decode_body(body, width, height, codec)
    else:
        raise ValueError("unsupported_codec")
    if len(pixels) != width * height:
        raise ValueError("decoded_pixel_count_mismatch")
    return pixels


def decode_pcx(raw_payload: bytes, resource_type: int) -> Image.Image:
    if len(raw_payload) < 12:
        raise ValueError("short Heroes III PCX header")
    size, width, height = struct.unpack_from("<III", raw_payload)
    if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= MAX_PIXELS):
        raise ValueError("invalid Heroes III PCX dimensions")
    count = width * height
    if resource_type == 16 and size == count and len(raw_payload) == 12 + count + 768:
        image = Image.frombytes("P", (width, height), raw_payload[12:12 + count])
        image.putpalette(raw_payload[-768:])
        return image
    if resource_type == 17 and size == count * 3 and len(raw_payload) == 12 + count * 3:
        return Image.frombytes("RGB", (width, height), raw_payload[12:], "raw", "BGR")
    raise ValueError("Heroes III PCX payload depth, type or byte count mismatch")


def default_baseline_root() -> Path:
    return asset_root() / "CompleteNativeBaseline/20261006-v1"


BASE = Path(r"{H3-assets}\CompleteNativeBaseline\20261006-v1")


def native_image(row: dict[str, Any], base: Path | None = None) -> tuple[Image.Image, dict[str, Any]]:
    active_base = Path(base) if base is not None else (BASE if BASE.is_dir() else default_baseline_root())
    with mapped_lod(active_base / row["native_lod"]) as lod:
        resource = row["identity"] + (".DEF" if row["kind"] == "def" else ".PCX")
        entry = next(x for x in lod.entries if x["name"].upper() == resource.upper())
        raw = payload(entry)
    if row["kind"] == "pcx":
        im = decode_pcx(raw, entry["type"]).convert("RGBA")
        if list(im.size) != row["canvas"]:
            raise ValueError("Native PCX geometry drift")
        return im, {
            "native_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "palette_semantics": "PCX opaque pixels retained",
        }
    parsed = parse_def(raw)
    slot = next(x for x in parsed["slots"] if x["group_id"] == row["group"] and x["frame_index"] == row["frame"])
    hd = slot["frame_header"]
    if list(hd[2:4]) != row["canvas"] or list(hd[4:6]) != row["crop"] or list(hd[6:8]) != row["anchor"]:
        raise ValueError("Native DEF geometry drift")
    indexed = decode_native_pixels(raw, slot)
    with deffile.open(io.BytesIO(raw)) as decoder:
        raw_map = {(x["group_id"], x["image_id"]): x for x in decoder.get_raw_data()}
        data = raw_map[(row["group"], row["frame"])]["image"]
        if indexed != data["pixeldata"] or bytes(v for rgb in decoder.get_palette() for v in rgb) != raw[16:784]:
            raise ValueError("Independent native palette/index readers disagree")
        if decoder.get_image_name(row["group"], row["frame"]).upper() != row["resource"].upper():
            raise ValueError("Native resource identity differs")
        im = decoder.read_image(how="normal", group_id=row["group"], image_id=row["frame"]).convert("RGBA")
    if list(im.size) != row["canvas"]:
        raise ValueError("Native frame canvas differs")
    return im, {
        "native_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "indexed_pixels_sha256": hashlib.sha256(indexed).hexdigest(),
        "palette_sha256": hashlib.sha256(raw[16:784]).hexdigest(),
    }
