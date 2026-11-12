"""Bind source PNGs to the canonical H3 slot inventory and render review tiers.

The companion owns inventory/key/geometry verification and HTML diagnostics.
The native EXE owns all pixel resampling and direct 1X/3X PNG generation.
Nothing produced by this workflow is approved or deployed artwork.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import datetime as dt
import ctypes
from contextlib import contextmanager
import gzip
import hashlib
import html
import io
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from workspace import asset_root, h3_root
import native_h3_io
from companion_runtime import (source_path as _runtime_source_path,
                               frozen_root as runtime_frozen_root, read_release as runtime_read_release)


def runtime_source_path(relative):
    return _runtime_source_path(relative, PRODUCT)

PRODUCT = resolve_tool_root(__file__)
BOUND_INVENTORY_RELATIVE = Path("codex-map/native-resource-inventory.json.gz")
INVENTORY_RELATIVE = Path("CompleteOfficialRelease-20261006-v1/codex-map/native-resource-inventory.json.gz")
LEGACY_INVENTORY_RELATIVE = Path("H3HD/assets/CompleteOfficialRelease-20261006-v1/codex-map/native-resource-inventory.json.gz")


def canonical_inventory_path(root: Path | None = None) -> Path:
    base = root if root is not None else h3_root()
    # The current bound workspace may itself be the reorganized H3 art root,
    # where codex-map is a direct child beside CompleteOfficialRelease. Prefer
    # that explicit path before the older versioned/legacy layouts.
    bound = base / BOUND_INVENTORY_RELATIVE
    if bound.is_file():
        return bound.resolve()
    direct = base / INVENTORY_RELATIVE
    if direct.is_file():
        return direct.resolve(strict=True)
    legacy = base / LEGACY_INVENTORY_RELATIVE
    if legacy.is_file():
        return legacy.resolve(strict=True)
    return direct.resolve(strict=True)
OUTPUT_BASE = PRODUCT / "build" / "experiments" / "native-review"
RENDER_TIMEOUT_SECONDS = 3500
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_INVENTORY_BYTES = 512 * 1024 * 1024
MAX_INVENTORY_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ITEMS = 512
TOWN_FOREGROUND_SCHEMA = "h3.native-review.town-foreground-selection.v1"
TOWN_FOREGROUND_PROOF_SCHEMA = "h3.native-review.town-foreground-selection-proof.v1"
TOWN_FOREGROUND_PIPELINE = "Town authority-bound same-grid foreground OR copy -> original integer padding -> H3Quality single3X sampling"
TOWN_FOREGROUND_JSON_BYTES = 8 * 1024 * 1024
TOWN_FOREGROUND_BATCH_MASK_PIXELS = 64 * 1024 * 1024


class ReviewError(RuntimeError):
    pass


def _pillow():
    # Filesystem guards are also used inside Blender's standard-library-only
    # Python. Pixel inspection still requires the real Pillow dependency.
    try:
        from PIL import Image, ImageChops, ImageStat
    except ModuleNotFoundError as error:
        raise ReviewError("PYTHON_MODULE_MISSING: PIL is required for PNG review") from error
    return Image, ImageChops, ImageStat


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"DUPLICATE_JSON_KEY: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReviewError(f"NONFINITE_JSON_NUMBER: {value}")


def parse_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_duplicate_rejecting_object,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"INVALID_JSON: {label}: {error}") from error


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, limit: int = 1024 * 1024 * 1024) -> str:
    if path.stat().st_size > limit:
        raise ReviewError(f"INPUT_FILE_TOO_LARGE: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_bounded(path: Path, limit: int, label: str) -> bytes:
    if type(limit) is not int or limit <= 0:
        raise ReviewError(f"INPUT_LIMIT_INVALID: {label}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ReviewError(f"INPUT_SIZE_LIMIT: {label}")
    return data


def exact_fields(value: Any, required: set[str], optional: set[str] = frozenset(), label: str = "object") -> None:
    if not isinstance(value, dict):
        raise ReviewError(f"SCHEMA_OBJECT_REQUIRED: {label}")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ReviewError(f"SCHEMA_REQUIRED_FIELDS_MISSING: {label}: {sorted(missing)}")
    if unknown:
        raise ReviewError(f"SCHEMA_UNKNOWN_FIELDS: {label}: {sorted(unknown)}")


def integer(value: Any, label: str, minimum: int = 0, maximum: int = 16384) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReviewError(f"INVALID_INTEGER: {label}")
    return value


def absolute_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ReviewError(f"ABSOLUTE_PATH_REQUIRED: {label}")
    raw = Path(value)
    if not raw.is_absolute():
        raise ReviewError(f"ABSOLUTE_PATH_REQUIRED: {label}")
    reject_reparse_components(raw, allow_missing_leaf=False)
    resolved = raw.resolve(strict=True)
    if not resolved.is_file():
        raise ReviewError(f"REGULAR_FILE_REQUIRED: {label}")
    return resolved


def _normalize_build_source_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ReviewError("BUILD_SOURCE_PATH_INVALID")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise ReviewError("BUILD_SOURCE_PATH_INVALID")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReviewError("BUILD_SOURCE_PATH_INVALID")
    return "/".join(parts)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _absolute_lexical_path(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ReviewError(f"ABSOLUTE_PATH_REQUIRED: {label}")
    return Path(os.path.abspath(path))


def _lexically_within(child: Path, parent: Path) -> bool:
    child = Path(os.path.abspath(child))
    parent = Path(os.path.abspath(parent))
    try:
        common = Path(os.path.commonpath((os.fspath(child), os.fspath(parent))))
    except ValueError:
        return False
    return _path_key(common) == _path_key(parent) and _path_key(child) != _path_key(parent)


def _lexists(path: Path) -> bool:
    try:
        Path(path).lstat()
        return True
    except FileNotFoundError:
        return False


def _path_prefixes(path: Path):
    path = _absolute_lexical_path(path, "directory")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        yield current


_WINDOWS_DIRECTORY_API = None


def _windows_directory_api():
    global _WINDOWS_DIRECTORY_API
    if os.name != "nt":
        raise ReviewError("WINDOWS_OUTPUT_DIRECTORY_LOCK_REQUIRED")
    if _WINDOWS_DIRECTORY_API is None:
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        _WINDOWS_DIRECTORY_API = (kernel32, _ByHandleFileInformation, ctypes.c_void_p(-1).value)
    return _WINDOWS_DIRECTORY_API


def _open_windows_directory(path: Path) -> tuple[Any, tuple[int, int]]:
    kernel32, info_type, invalid_handle = _windows_directory_api()
    from ctypes import wintypes

    flags = 0x02000000 | 0x00200000  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
    share = 0x00000001 | 0x00000002  # READ | WRITE; deliberately exclude DELETE
    handle = kernel32.CreateFileW(
        os.fspath(path), 0x00000081, share, None, 3, flags, None,
    )  # FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY, OPEN_EXISTING
    handle_value = getattr(handle, "value", handle)
    if handle_value is None or handle_value == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, f"cannot lock output directory: {path}")
    info = info_type()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, f"cannot identify output directory: {path}")
    if info.dwFileAttributes & 0x00000400:
        kernel32.CloseHandle(handle)
        raise ReviewError(f"REPARSE_POINT_REJECTED: {path}")
    if not info.dwFileAttributes & 0x00000010:
        kernel32.CloseHandle(handle)
        raise ReviewError(f"DIRECTORY_REQUIRED: {path}")
    file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    return handle, (int(info.dwVolumeSerialNumber), file_index)


class BoundInputSet:
    """One bounded SHA reader for diagnostic consumers; pin only declared current inputs."""

    def __init__(self, *, error_type=ReviewError):
        self.files = {}
        self.raw = {}
        self.error_type = error_type

    @contextmanager
    def _failures(self):
        try:
            yield
        except ReviewError as error:
            if self.error_type is ReviewError:
                raise
            raise self.error_type(str(error)) from error

    def read(self, ref, label, limit=32 * 1024 * 1024):
        with self._failures():
            return self._read(ref, label, limit)

    def _read(self, ref, label, limit):
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ReviewError(label + "_REFERENCE_REQUIRED")
        if not isinstance(ref.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is None:
            raise ReviewError(label + "_SHA256_REQUIRED")
        path = _absolute_lexical_path(Path(ref["path"]), label)
        reject_reparse_components(path, allow_missing_leaf=False)
        key = str(path)
        if key not in self.raw:
            self.raw[key] = read_bounded(path, limit, label)
        raw = self.raw[key]
        if len(raw) > limit or sha256_bytes(raw) != ref["sha256"]:
            raise ReviewError(label + "_SHA256_DRIFT_OR_LIMIT")
        if "bytes" in ref and (type(ref["bytes"]) is not int or len(raw) != ref["bytes"]):
            raise ReviewError(label + "_BYTES_DRIFT")
        self.files[key] = {"path": key, "sha256": ref["sha256"], "bytes": len(raw)}
        return raw

    def document(self, ref, label):
        with self._failures():
            doc = parse_json(self.read(ref, label), label)
            if not isinstance(doc, dict):
                raise ReviewError(label + "_OBJECT_REQUIRED")
            return doc

    def verify_current(self):
        with self._failures():
            for ref in self.files.values():
                path = Path(ref["path"])
                reject_reparse_components(path, allow_missing_leaf=False)
                if sha256_bytes(read_bounded(path, ref["bytes"], "pinned input")) != ref["sha256"]:
                    raise ReviewError("PINNED_INPUT_CHANGED_DURING_OPERATION")


class OutputRootGuard:
    """Pin the ignored output base and new root against Windows rename/junction swaps."""

    def __init__(self, root: Path, base: Path):
        self.root = root
        self.base = base
        self._held: dict[str, tuple[Path, Any, tuple[int, int]]] = {}
        self._closed = False

    @classmethod
    def create(cls, root: Path, base: Path, *, on_root_created=None) -> "OutputRootGuard":
        root = _absolute_lexical_path(root, "output root")
        base = _absolute_lexical_path(base, "ignored output base")
        product = _absolute_lexical_path(PRODUCT, "product root")
        if not _lexically_within(base, product):
            raise ReviewError("OUTPUT_BASE_OUTSIDE_PRODUCT_ROOT")
        if _path_key(root.parent) != _path_key(base):
            raise ReviewError("OUTPUT_ROOT_MUST_BE_A_NEW_CHILD_OF_IGNORED_NATIVE_REVIEW_ROOT")
        reject_reparse_components(root, allow_missing_leaf=True)
        if _lexists(root):
            raise ReviewError("OUTPUT_ROOT_ALREADY_EXISTS")

        guard = cls(root, base)
        try:
            # Lock every existing directory component below the drive root so
            # no ancestor can be renamed while the child renderer is running.
            for prefix in _path_prefixes(product):
                guard._hold_directory(prefix)
            relative_base = base.relative_to(product)
            current = product
            for part in relative_base.parts:
                if part in {"", ".", ".."}:
                    raise ReviewError("OUTPUT_BASE_PATH_INVALID")
                current = current / part
                if not _lexists(current):
                    try:
                        current.mkdir()
                    except FileExistsError as error:
                        raise ReviewError("OUTPUT_BASE_PATH_CHANGED_DURING_CREATE") from error
                guard._hold_directory(current)
            if _lexists(root):
                raise ReviewError("OUTPUT_ROOT_ALREADY_EXISTS")
            try:
                root.mkdir()
            except FileExistsError as error:
                raise ReviewError("OUTPUT_ROOT_ALREADY_EXISTS") from error
            if on_root_created is not None:
                on_root_created()
            guard._hold_directory(root)
            guard.verify_current()
            return guard
        except Exception:
            guard.close()
            raise

    def _hold_directory(self, path: Path) -> None:
        if self._closed:
            raise ReviewError("OUTPUT_ROOT_GUARD_CLOSED")
        path = _absolute_lexical_path(path, "guarded directory")
        key = _path_key(path)
        if key in self._held:
            return
        handle, identity = _open_windows_directory(path)
        self._held[key] = (path, handle, identity)

    @classmethod
    def open_existing(cls, root: Path, base: Path) -> "OutputRootGuard":
        """Hold an existing operation tree during read-only receipt verification."""
        root = _absolute_lexical_path(root, "existing output root")
        base = _absolute_lexical_path(base, "ignored output base")
        product = _absolute_lexical_path(PRODUCT, "product root")
        if not _lexically_within(base, product):
            raise ReviewError("OUTPUT_BASE_OUTSIDE_PRODUCT_ROOT")
        if _path_key(root.parent) != _path_key(base):
            raise ReviewError("OUTPUT_ROOT_MUST_BE_A_CHILD_OF_IGNORED_OUTPUT_BASE")
        reject_reparse_components(root, allow_missing_leaf=False)
        guard = cls(root, base)
        try:
            for prefix in _path_prefixes(root):
                guard._hold_directory(prefix)
            guard.verify_current()
            return guard
        except Exception:
            guard.close()
            raise

    def verify_current(self) -> None:
        if self._closed:
            raise ReviewError("OUTPUT_ROOT_GUARD_CLOSED")
        if not _lexically_within(self.root, self.base) or _path_key(self.root.parent) != _path_key(self.base):
            raise ReviewError("OUTPUT_ROOT_PATH_BINDING_INVALID")
        for expected_path, _held_handle, expected_identity in self._held.values():
            try:
                reject_reparse_components(expected_path, allow_missing_leaf=False)
                handle, actual_identity = _open_windows_directory(expected_path)
            except (OSError, ReviewError) as error:
                raise ReviewError(f"OUTPUT_DIRECTORY_BINDING_LOST: {expected_path}: {error}") from error
            try:
                if actual_identity != expected_identity:
                    raise ReviewError(f"OUTPUT_DIRECTORY_IDENTITY_CHANGED: {expected_path}")
            finally:
                kernel32, _info_type, _invalid = _windows_directory_api()
                kernel32.CloseHandle(handle)

    def hold_directory(self, path: Path) -> None:
        path = _absolute_lexical_path(path, "output directory")
        if _path_key(path) != _path_key(self.root) and not _lexically_within(path, self.root):
            raise ReviewError("OUTPUT_DIRECTORY_OUTSIDE_LOCKED_ROOT")
        self.verify_current()
        relative = path.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            self._hold_directory(current)
        self.verify_current()

    def verify_target(self, path: Path, *, must_exist: bool = False) -> Path:
        path = _absolute_lexical_path(path, "output file")
        if not _lexically_within(path, self.root):
            raise ReviewError("OUTPUT_FILE_OUTSIDE_LOCKED_ROOT")
        self.hold_directory(path.parent)
        reject_reparse_components(path if must_exist else path.parent, allow_missing_leaf=False)
        self.verify_current()
        if must_exist and not path.is_file():
            raise ReviewError(f"OUTPUT_FILE_REQUIRED: {path}")
        if not must_exist and _lexists(path):
            raise ReviewError(f"OUTPUT_ALREADY_EXISTS: {path}")
        return path

    def close(self) -> None:
        if self._closed:
            return
        kernel32, _info_type, _invalid = _windows_directory_api()
        for _path, handle, _identity in reversed(list(self._held.values())):
            kernel32.CloseHandle(handle)
        self._held.clear()
        self._closed = True


def reject_reparse_components(path: Path, *, allow_missing_leaf: bool) -> None:
    current = Path(path.anchor)
    parts = path.parts[1:] if path.anchor else path.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf:
                continue
            raise ReviewError(f"INPUT_PATH_MISSING: {current}")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ReviewError(f"REPARSE_POINT_REJECTED: {current}")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ReviewError(f"PATH_COMPONENT_NOT_DIRECTORY: {current}")


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return child.resolve(strict=False) != parent.resolve(strict=False)
    except ValueError:
        return False


def _overlap(left: Path, right: Path) -> bool:
    left, right = left.resolve(strict=False), right.resolve(strict=False)
    return left == right or _within(left, right) or _within(right, left)


def key_tuple(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ReviewError("NATIVE_KEY_ARRAY_REQUIRED")
    if len(value) == 4 and value[0] == "def":
        identity = value[1]
        if not isinstance(identity, str) or not identity.isascii() or not identity.replace("_", "").replace("-", "").isalnum():
            raise ReviewError("NATIVE_DEF_ID_INVALID")
        return ("def", identity.upper(), integer(value[2], "group", 0, 65535), integer(value[3], "frame", 0, 65535))
    if len(value) == 2 and value[0] == "pcx":
        identity = value[1]
        if not isinstance(identity, str) or not identity.isascii() or not identity.replace("_", "").replace("-", "").isalnum():
            raise ReviewError("NATIVE_PCX_ID_INVALID")
        return ("pcx", identity.upper())
    raise ReviewError("NATIVE_KEY_INVALID")


def row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    kind = row.get("kind")
    identity = row.get("identity")
    if kind == "def":
        return key_tuple(["def", identity, row.get("group"), row.get("frame")])
    if kind == "pcx":
        return key_tuple(["pcx", identity])
    raise ReviewError("INVENTORY_ROW_KIND_UNSUPPORTED")


def checked_pair(value: Any, label: str, minimum: int = 1) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ReviewError(f"GEOMETRY_PAIR_REQUIRED: {label}")
    return [integer(value[0], f"{label}.width", minimum), integer(value[1], f"{label}.height", minimum)]


def official_targets(row: dict[str, Any], key: tuple[Any, ...]) -> dict[str, list[int]]:
    canvas = checked_pair(row.get("canvas"), "canvas")
    crop_value = row.get("crop")
    crop = checked_pair(crop_value, "crop") if crop_value is not None else (canvas.copy() if key[0] == "pcx" else None)
    if crop is None:
        raise ReviewError("INVENTORY_CROP_MISSING")
    anchor_value = row.get("anchor")
    if anchor_value is None and key[0] == "pcx" and crop == canvas:
        anchor = [0, 0]
    else:
        if not isinstance(anchor_value, list) or len(anchor_value) != 2:
            raise ReviewError("INVENTORY_ANCHOR_MISSING")
        anchor = [integer(anchor_value[0], "anchor.x"), integer(anchor_value[1], "anchor.y")]
    if anchor[0] + crop[0] > canvas[0] or anchor[1] + crop[1] > canvas[1]:
        raise ReviewError("INVENTORY_CROP_OUTSIDE_CANVAS")
    return {"canvas_1x": canvas, "crop_1x": crop, "origin_1x": anchor}


def png_size_bytes(raw: bytes, label: str, *, pixel_limit: int = 64 * 1024 * 1024) -> list[int]:
    Image, _, _ = _pillow()
    try:
        buffer = io.BytesIO(raw)
        with Image.open(buffer) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ReviewError(f"STATIC_PNG_REQUIRED: {label}")
            image.verify()
        buffer.seek(0)
        with Image.open(buffer) as image:
            width, height = image.size
            if width < 1 or height < 1 or width > 16384 or height > 16384 or width * height > pixel_limit:
                raise ReviewError(f"PNG_DIMENSION_LIMIT: {label}")
            image.convert("RGBA")
            return [width, height]
    except (OSError, ValueError) as error:
        if isinstance(error, ReviewError):
            raise
        raise ReviewError(f"PNG_DECODE_FAILED: {label}: {error}") from error


def _read_bound_png(path: Path, expected_sha: Any, label: str, *, pixel_limit: int = 64 * 1024 * 1024) -> tuple[bytes, list[int]]:
    if not isinstance(expected_sha, str) or len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise ReviewError(f"INVALID_SHA256: {label}")
    raw = read_bounded(path, 512 * 1024 * 1024, label)
    actual_sha = sha256_bytes(raw)
    if actual_sha != expected_sha:
        raise ReviewError(f"SOURCE_SHA256_MISMATCH: {label}")
    return raw, png_size_bytes(raw, label, pixel_limit=pixel_limit)


def bound_png(path: Path, expected_sha: Any, label: str) -> tuple[str, list[int]]:
    _, pixels = _read_bound_png(path, expected_sha, label)
    return expected_sha, pixels


def load_inventory(path: Path, expected_sha: str) -> tuple[dict[tuple[Any, ...], dict[str, Any]], bytes]:
    raw = read_bounded(path, MAX_INVENTORY_BYTES, "official inventory")
    if sha256_bytes(raw) != expected_sha:
        raise ReviewError("OFFICIAL_INVENTORY_SHA256_MISMATCH_OR_LIMIT")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            parts: list[bytes] = []
            total = 0
            while True:
                part = stream.read(4 * 1024 * 1024)
                if not part:
                    break
                total += len(part)
                if total > MAX_INVENTORY_UNCOMPRESSED_BYTES:
                    raise ReviewError("OFFICIAL_INVENTORY_UNCOMPRESSED_LIMIT")
                parts.append(part)
            decompressed = b"".join(parts)
    except ReviewError:
        raise
    except OSError as error:
        raise ReviewError(f"OFFICIAL_INVENTORY_GZIP_INVALID: {error}") from error
    catalog = parse_json(decompressed, "official inventory")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("rows"), list):
        raise ReviewError("OFFICIAL_INVENTORY_ROWS_MISSING")
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in catalog["rows"]:
        if not isinstance(row, dict):
            raise ReviewError("OFFICIAL_INVENTORY_ROW_INVALID")
        key = row_key(row)
        if key in rows:
            raise ReviewError(f"OFFICIAL_INVENTORY_DUPLICATE_KEY: {key}")
        rows[key] = row
    return rows, raw


def file_sha_matches(path: Path, expected: str, label: str) -> None:
    if not isinstance(expected, str) or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ReviewError(f"INVALID_SHA256: {label}")
    if sha256_file(path) != expected:
        raise ReviewError(f"SOURCE_SHA256_MISMATCH: {label}")


def _canonical_json_sha(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(canonical)


def _positive_rect(value: Any, size: list[int], crop: list[int], item_id: str) -> tuple[list[int] | None, list[int], str, int, bool]:
    if value is None:
        rect = [0, 0, size[0], size[1]]
        explicit = False
    else:
        if not isinstance(value, list) or len(value) != 4:
            raise ReviewError(f"SOURCE_RECT_REQUIRED: {item_id}")
        x, y, width, height = [integer(value[n], f"{item_id}.source_rect[{n}]") for n in range(4)]
        width = integer(width, f"{item_id}.source_rect.width", 1)
        height = integer(height, f"{item_id}.source_rect.height", 1)
        if x + width > size[0] or y + height > size[1]:
            raise ReviewError(f"SOURCE_RECT_OUTSIDE_IMAGE: {item_id}")
        rect = [x, y, width, height]
        explicit = True
    width, height = rect[2], rect[3]
    if width < crop[0] or height < crop[1]:
        raise ReviewError(f"SOURCE_UNDER_NATIVE_1X: {item_id}")
    common_k = max(math.ceil(width / crop[0]), math.ceil(height / crop[1]))
    exact_ratio = width * crop[1] == height * crop[0]
    return (rect if explicit else None), [width, height], "strict-aspect", common_k, exact_ratio


def _resolve_authored_view(item, key, targets, source, source_sha, source_pixels, inventory_sha, crop):
    """Bind the owner's explicit UI view; never infer a crop or weaken object padding."""
    from fractions import Fraction

    def require(condition, code):
        if not condition:
            raise ReviewError(code)

    require(item.get("render_scales") == [3], "AUTHORED_3X_ONLY")
    require(item.get("source_rect") is None, "AUTHORED_SOURCE_RECT_MUST_BE_DERIVED")
    ref = item.get("authored_view")
    exact_fields(ref, {"path", "sha256"}, label="authored_view")
    refs = BoundInputSet()
    owner = refs.document(ref, "AUTHORED_VIEW")
    atlas_mode = item["registration"] == "authored-atlas-window-3x"
    require(owner.get("schema") == ("h3.map-ui.direct-native-review-consumer-feedback.v1" if atlas_mode
            else "h3.map-ui.blue6-author-geometry-qa-plan.v1"),
            "AUTHORED_VIEW_SCHEMA_UNSUPPORTED")
    cases = owner.get("cases" if atlas_mode else "items")
    require(isinstance(cases, list) and 0 < len(cases) <= MAX_ITEMS, "AUTHORED_CASE_COUNT_INVALID")
    by_key = {}
    for case in cases:
        candidate = case.get("key")
        if isinstance(candidate, list) and len(candidate) == 4 and candidate[0] == "pcx" and candidate[2:] == [None, None]:
            candidate = candidate[:2]
        native_key = key_tuple(candidate)
        require(native_key not in by_key, "AUTHORED_DUPLICATE_NATIVE_KEY")
        by_key[native_key] = case
    require(key in by_key, "AUTHORED_NATIVE_KEY_NOT_BOUND")
    case = by_key[key]
    parent = case["source"]
    require(parent.get("sha256") == source_sha and _path_key(Path(parent["path"])) == _path_key(source),
            "AUTHORED_SOURCE_BINDING_DRIFT")
    declared_targets = case["native_targets"]
    require(declared_targets.get("canvas_1x") == targets["canvas_1x"]
            and all(declared_targets.get(k) == targets[k] or (key[0] == "pcx" and declared_targets.get(k) is None)
                    for k in ("crop_1x", "origin_1x"))
            and (case.get("target_frame_pixels_1x") == crop if atlas_mode
                 else case.get("expected_3x_crop") == [n * 3 for n in crop]), "AUTHORED_NATIVE_GEOMETRY_DRIFT")
    if not atlas_mode:
        require(all(owner.get(k) is False for k in ("art_approved", "formal_source_intake", "game_deployed", "runtime_accepted")),
                "AUTHORED_QA_MUST_NOT_GRANT_ART_OR_RUNTIME_APPROVAL")
        measured = owner.get("author_measurement", {})
        require(measured.get("rects_are_explicit_author_choice_not_AI_layout_prediction") is True
                and measured.get("all_selected_source_rects_inside_original") is True,
                "AUTHORED_EXPLICIT_STATE_RECT_REQUIRED")
        require(owner.get("native_evidence", {}).get("inventory", {}).get("sha256") == inventory_sha,
                "AUTHORED_INVENTORY_BINDING_DRIFT")
        rect = case.get("author_source_rect")
        require(isinstance(rect, list) and len(rect) == 4, "AUTHORED_STATE_RECT_REQUIRED")
        rect = [integer(n, "AUTHORED_STATE_RECT", 0 if i < 2 else 1, source_pixels[i % 2])
                for i, n in enumerate(rect)]
        require(rect[0] + rect[2] <= source_pixels[0] and rect[1] + rect[3] <= source_pixels[1],
                "AUTHORED_STATE_RECT_OUTSIDE_SOURCE")
        require(rect[2] >= crop[0] * 3 and rect[3] >= crop[1] * 3, "AUTHORED_SOURCE_UNDER_3X")
        w, h = crop
        require(case.get("target_vertices_native_1x") == [[0, 0], [w, 0], [w, h], [0, h]]
                and case.get("triangles") == [[0, 1, 2], [0, 2, 3]], "AUTHORED_RECTANGLE_TARGET_REQUIRED")
        offset = case.get("padding", {}).get("offset")
        require(isinstance(offset, list) and len(offset) == 2 and all(type(n) is int and n >= 0 for n in offset),
                "AUTHORED_RECTANGLE_CORRESPONDENCE_REQUIRED")
        ox, oy = offset; rw, rh = rect[2:]
        require(case.get("source_vertices_padding_local") == [[ox, oy], [ox + rw, oy], [ox + rw, oy + rh], [ox, oy + rh]],
                "AUTHORED_RECTANGLE_CORRESPONDENCE_DRIFT")
        refs.verify_current()
        return {"authority": ref, "source_rect_xywh": rect, "target_rect_1x": [0, 0, w, h],
                "scope": "author-chosen-ui-state-rectangle"}, None, [
                    {"path": v["path"], "sha256": v["sha256"]} for v in refs.files.values()]
    window = case["authored_atlas_window"]
    whole = window.get("whole_image_source") is True
    sample = [n * 3 for n in crop] if whole else window["whole_sample_pixels_3x"]
    rect = [0, 0, *sample] if whole else window["whole_sample_crop_rect_3x"]
    require(isinstance(sample, list) and len(sample) == 2, "AUTHORED_ATLAS_SIZE_REQUIRED")
    sample = [integer(n, "AUTHORED_ATLAS_AXIS", 1, source_pixels[i]) for i, n in enumerate(sample)]
    require(isinstance(rect, list) and len(rect) == 4, "AUTHORED_ATLAS_RECT_REQUIRED")
    rect = [integer(n, "AUTHORED_ATLAS_RECT", 0 if i < 2 else 1, sample[i % 2]) for i, n in enumerate(rect)]
    require(rect[0] + rect[2] <= sample[0] and rect[1] + rect[3] <= sample[1]
            and rect[2:] == [n * 3 for n in crop], "AUTHORED_ATLAS_RECT_OR_TARGET_DRIFT")
    projected = [Fraction(rect[i] * source_pixels[i % 2], sample[i % 2]) for i in range(4)]
    declared = case["exact_source_rect_raw_rational"]
    require(isinstance(declared, list) and len(declared) == 4 and all(isinstance(v, list) and len(v) == 2
            and type(v[0]) is int and type(v[1]) is int and v[0] >= 0 and v[1] > 0 for v in declared),
            "AUTHORED_RATIONAL_RECT_REQUIRED")
    require(projected == [Fraction(*v) for v in declared], "AUTHORED_RATIONAL_PROJECTION_DRIFT")
    registration = window["existing_native_alpha_registration"] if whole else window["registration"]
    require(registration.get("native_catalog", {}).get("sha256") == inventory_sha,
            "AUTHORED_INVENTORY_BINDING_DRIFT")
    require(registration.get("schema") == ("h3.map-ui.ui-native-alpha-registration.v1" if whole
            else "h3.map-ui.ui-state-row-extraction.v1"), "AUTHORED_REGISTRATION_SCHEMA_INVALID")
    view = {"authority": ref, "sample_pixels_3x": sample, "frame_rect_3x": rect,
            "source_rect_raw_rational": [[v.numerator, v.denominator] for v in projected]}
    cap = None
    if whole:
        require(registration.get("operation") == "min(model_alpha,official_alpha)"
                and registration.get("model_RGB_unchanged") is True and registration.get("opacity_created") is False
                and registration.get("raw_whole_model_unchanged") is True
                and registration.get("required_border_missing_failure") is True, "AUTHORED_ALPHA_OPERATION_UNSUPPORTED")
        mask = registration["official_mask"]
        raw = refs.read(mask, "AUTHORED_ALPHA_MASK", 64 * 1024 * 1024)
        Image, _, _ = _pillow()
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == "PNG" and image.mode == "RGBA" and list(image.size) == rect[2:],
                    "AUTHORED_ALPHA_MASK_GEOMETRY_OR_MODE_DRIFT")
        cap = {"path": mask["path"], "sha256": mask["sha256"], "operation": registration["operation"]}
    refs.verify_current()
    return view, cap, [{"path": v["path"], "sha256": v["sha256"]} for v in refs.files.values()]


def _native_layer_references(document: dict, root: Path) -> list[dict]:
    refs = {}
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                path = absolute_file(value["path"], "native layer input")
                reject_reparse_components(path, allow_missing_leaf=False)
                if not _lexically_within(path, root):
                    raise ReviewError("NATIVE_LAYER_INPUT_OUTSIDE_H3")
                digest = value["sha256"]
                size = value.get("bytes")
                if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                        or type(size) is not int or not 0 <= size <= 512 * 1024 * 1024):
                    raise ReviewError("NATIVE_LAYER_INPUT_REFERENCE_INVALID")
                ref = {"path": str(path), "sha256": digest, "bytes": size}
                token = _path_key(path)
                if token in refs and refs[token] != ref:
                    raise ReviewError("NATIVE_LAYER_INPUT_CONFLICT")
                refs[token] = ref
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return list(refs.values())


def _verify_native_layer_inputs(refs: list[dict]) -> None:
    for ref in refs:
        path = absolute_file(ref["path"], "native layer input")
        reject_reparse_components(path, allow_missing_leaf=False)
        if path.stat().st_size != ref["bytes"] or sha256_file(path) != ref["sha256"]:
            raise ReviewError("NATIVE_LAYER_INPUT_DRIFT")


def _native_layer_reader(bindings: dict, root: Path):
    assets = asset_root(root)
    standalone_scripts = PRODUCT / "scripts"
    expected_candidates = {
        "existing_adapter": (standalone_scripts / "native_h3_io.py", root / "tools/native_h3_io.py"),
        "existing_decoder": (Path(native_h3_io.deffile.__file__), root / "tools/homm3data/deffile.py"),
        "existing_lod_reader": (Path(native_h3_io.lodfile.__file__), root / "tools/homm3data/lodfile.py"),
        "existing_index_reader": (standalone_scripts / "native_h3_io.py", root / "tools/native_h3_io.py"),
        "existing_def_parser": (standalone_scripts / "build_official_def_frame_map.py", root / "tools/build_official_def_frame_map.py"),
        "complete_sprite_lod": (assets / "CompleteNativeBaseline/20261006-v1/H3sprite.lod",),
    }
    matched: dict[str, Path] = {}
    for name, candidates in expected_candidates.items():
        ref = bindings.get(name)
        if not isinstance(ref, dict):
            raise ReviewError("NATIVE_LAYER_READER_OR_CONTAINER_NOT_CANONICAL: " + name)
        ref_key = _path_key(Path(ref.get("path", "")))
        hit = next((c for c in candidates if _path_key(c) == ref_key), None)
        if hit is None:
            raise ReviewError("NATIVE_LAYER_READER_OR_CONTAINER_NOT_CANONICAL: " + name)
        matched[name] = hit
    native_h3_io.BASE = matched["complete_sprite_lod"].parent
    return native_h3_io


def _resolve_native_layers(ref: dict, key: tuple, row: dict, targets: dict,
                           context: dict) -> tuple[dict, list[dict]]:
    exact_fields(ref, {"path", "sha256"}, label="native_layer_evidence")
    if key[0] != "def" or targets["canvas_1x"][0] * targets["canvas_1x"][1] > 4 * 1024 * 1024:
        raise ReviewError("NATIVE_LAYER_DEF_REFERENCE_SCOPE_REQUIRED")
    path = absolute_file(ref["path"], "native layer readback")
    root = h3_root()
    reject_reparse_components(path, allow_missing_leaf=False)
    if not _lexically_within(path, root):
        raise ReviewError("NATIVE_LAYER_READBACK_OUTSIDE_H3")
    raw = read_bounded(path, 1024 * 1024, "native layer readback")
    if (not isinstance(ref["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is None
            or sha256_bytes(raw) != ref["sha256"]):
        raise ReviewError("NATIVE_LAYER_READBACK_SHA256_DRIFT")
    identity = (str(path), ref["sha256"])
    if context.get("identity") != identity:
        owner = parse_json(raw, "native layer readback")
        if (not isinstance(owner, dict)
                or owner.get("schema") != "h3.art-town.native-flag-shadow-era-source-readback.v1"
                or owner.get("scope", {}).get("source_or_art_or_runtime_completion_claimed") is not False):
            raise ReviewError("NATIVE_LAYER_READBACK_SCHEMA_OR_AUTHORITY_INVALID")
        resources = owner.get("resources")
        if not isinstance(resources, list) or not 1 <= len(resources) <= MAX_ITEMS:
            raise ReviewError("NATIVE_LAYER_RESOURCE_SET_INVALID")
        refs = _native_layer_references(owner, root)
        refs.append({"path": str(path), "sha256": ref["sha256"], "bytes": len(raw)})
        _verify_native_layer_inputs(refs)
        reader = _native_layer_reader(owner.get("source_bindings", {}), root)
        # Retain one bounded readback/context, never a batch of large LOD bytes.
        context.clear()
        context.update(identity=identity, owner=owner, refs=refs, reader=reader)
    owner, reader = context["owner"], context["reader"]
    matches = [value for value in owner["resources"] if isinstance(value, dict) and value.get("key") == list(key)]
    if len(matches) != 1:
        raise ReviewError("NATIVE_LAYER_KEY_NOT_UNIQUE")
    expected = matches[0]
    geometry = {"canvas": targets["canvas_1x"], "crop": targets["crop_1x"], "origin": targets["origin_1x"]}
    if (expected.get("native_geometry") != geometry
            or expected.get("native_lod", "").casefold() != "h3sprite.lod"
            or row.get("native_lod", "").casefold() != "h3sprite.lod"
            or expected.get("native_member", "").upper() != key[1] + ".DEF"
            or expected.get("native_frame_resource", "").upper() != row.get("resource", "").upper()):
        raise ReviewError("NATIVE_LAYER_SLOT_OR_GEOMETRY_MISMATCH")
    try:
        normal, proof = reader.native_image(row)
    except (KeyError, ValueError, StopIteration) as error:
        raise ReviewError("NATIVE_LAYER_NATIVE_READER_REJECTED: " + type(error).__name__) from error
    expected_proof = {"native_payload_sha256": expected.get("native_member_payload_sha256"),
                      "indexed_pixels_sha256": expected.get("indexed_pixels_sha256"),
                      "palette_sha256": expected.get("palette_sha256")}
    if proof != expected_proof or sha256_bytes(normal.tobytes()) != expected.get("normal_rgba_sha256"):
        raise ReviewError("NATIVE_LAYER_PAYLOAD_INDEX_PALETTE_OR_RGBA_MISMATCH")
    Image, _, _ = _pillow()
    with Image.open(expected["normal_reference"]["path"]) as reference:
        if reference.size != tuple(geometry["canvas"]) or reference.convert("RGBA").tobytes() != normal.tobytes():
            raise ReviewError("NATIVE_LAYER_NORMAL_REFERENCE_MISMATCH")
    with reader.mapped_lod(reader.BASE / row["native_lod"]) as lod:
        entries = [entry for entry in lod.entries if entry["name"].upper() == expected["native_member"].upper()]
        if len(entries) != 1:
            raise ReviewError("NATIVE_LAYER_MEMBER_NOT_UNIQUE")
        payload = reader.payload(entries[0])
    parsed = reader.parse_def(payload)
    slots = [slot for slot in parsed["slots"] if (slot["group_id"], slot["frame_index"]) == key[2:]]
    if len(slots) != 1:
        raise ReviewError("NATIVE_LAYER_FRAME_NOT_UNIQUE")
    indexed = reader.decode_native_pixels(payload, slots[0])
    if sha256_bytes(payload) != expected_proof["native_payload_sha256"] or sha256_bytes(indexed) != expected_proof["indexed_pixels_sha256"] or sha256_bytes(payload[16:784]) != expected_proof["palette_sha256"]:
        raise ReviewError("NATIVE_LAYER_SOURCE_CHANGED_DURING_DECODE")
    counts = {str(i): indexed.count(i) for i in range(8)}
    counts["224_255"] = sum(value >= 224 for value in indexed)
    if any(expected.get("index_counts", {}).get(name) != count for name, count in counts.items()):
        raise ReviewError("NATIVE_LAYER_INDEX_COUNTS_MISMATCH")
    if counts["6"] or counts["7"]:
        raise ReviewError("NEEDS_NATIVE_OVERLAY_ROLE_CONTRACT")
    layers, images = {}, {}
    with reader.deffile.open(io.BytesIO(payload)) as decoder:
        for role in ("normal", "shadow", "overlay", "combined"):
            image = decoder.read_image(how=role, group_id=key[2], image_id=key[3])
            if image is None or image.size != tuple(geometry["canvas"]):
                raise ReviewError("NATIVE_LAYER_MISSING_OR_GEOMETRY_MISMATCH: " + role)
            image = image.convert("RGBA")
            histogram = image.getchannel("A").histogram()
            images[role] = image
            layers[role] = {"pixels": list(image.size), "rgba_sha256": sha256_bytes(image.tobytes()),
                            "positive_alpha_pixels": image.width * image.height - histogram[0],
                            "alpha_levels": [i for i, count in enumerate(histogram) if count]}
    if layers["normal"]["rgba_sha256"] != expected["normal_rgba_sha256"]:
        raise ReviewError("NATIVE_LAYER_NORMAL_DECODER_MISMATCH")
    mask = Image.new("L", tuple(geometry["canvas"]), 0)
    mask.paste(Image.frombytes("L", tuple(geometry["crop"]), bytes(255 if value == 5 else 0 for value in indexed)), tuple(geometry["origin"]))
    if (images["overlay"].getchannel("A").tobytes() != mask.tobytes()
            or any(value for value, selected in zip(images["normal"].getchannel("A").tobytes(), mask.tobytes()) if selected)
            or any(value for value, selected in zip(images["combined"].getchannel("A").tobytes(), mask.tobytes()) if selected)):
        raise ReviewError("NATIVE_LAYER_INDEX5_OVERLAY_MISMATCH")
    if Image.alpha_composite(images["normal"], images["shadow"]).tobytes() != images["combined"].tobytes():
        raise ReviewError("NATIVE_LAYER_COMBINED_MISMATCH")
    ox, oy = geometry["origin"]
    crop = images["normal"].crop((ox, oy, ox + geometry["crop"][0], oy + geometry["crop"][1])).tobytes()
    for i, value in enumerate(indexed):
        if value >= 224 and (crop[i * 4:i * 4 + 3] != payload[16 + value * 3:19 + value * 3] or crop[i * 4 + 3] != 255):
            raise ReviewError("NATIVE_LAYER_ORDINARY_PALETTE_RGB_OR_ALPHA_MISMATCH")
    return {"schema": "h3.native-review.native-reference-layers.v1",
            "readback": {"path": str(path), "sha256": ref["sha256"]}, "key": list(key),
            "native_geometry": geometry, "native_proof": proof, "layers": layers, "index_counts": counts,
            "native_reference_layers_verified": True, "generated_source_functional_layers_verified": False,
            "runtime_owner_color_mapping": "NOT_ESTABLISHED", "pixel_rendering_changed": False,
            "art_approved": False, "runtime_accepted": False, "game_deployed": False}, context["refs"]


def _native_layer_inputs(metadata: list[dict]) -> list[dict]:
    refs = {}
    for row in metadata:
        for ref in row.get("native_layer_inputs", []):
            key = _path_key(Path(ref["path"]))
            if key in refs and refs[key] != ref:
                raise ReviewError("NATIVE_LAYER_INPUT_CONFLICT")
            refs[key] = ref
    return list(refs.values())


def _foreground_equal(actual, expected):
    """The Town proof never treats JSON booleans/floats as integer evidence."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_foreground_equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_foreground_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _resolve_foreground_selection(item, key, targets, source, source_sha, source_pixels,
                                  source_raw, source_rect, padding, registration):
    """Bind frozen Town vectors only; membership and pixel copies belong to C++."""
    def require(condition, code):
        if not condition:
            raise ReviewError("TOWN_FOREGROUND_" + code)

    wrapper = item["foreground_selection"]
    exact_fields(wrapper, {"schema", "authority"}, label="foreground_selection")
    require(wrapper["schema"] == TOWN_FOREGROUND_SCHEMA, "SCHEMA_UNSUPPORTED")
    exact_fields(wrapper["authority"], {"path", "sha256"}, label="foreground_selection.authority")
    require(key[0] == "pcx" and "authored_view" not in item
            and item.get("source_layout") == item.get("output_layout") == "native-crop"
            and registration == "integer-transparent-padding" and padding is not None
            and item.get("source_rect") is not None and item.get("render_scales") == [3]
            and all(type(n) is int for n in item["render_scales"]), "ROLE_CONTRACT_INVALID")
    require(source_pixels[0] * source_pixels[1] <= 16 * 1024 * 1024, "SOURCE_PIXEL_LIMIT")
    require(len(source_raw) >= 33 and source_raw[:8] == b"\x89PNG\r\n\x1a\n"
            and source_raw[12:16] == b"IHDR" and source_raw[24:26] == b"\x08\x06", "RGBA8_PNG_REQUIRED")
    refs = BoundInputSet()

    def ref(value, label):
        require(isinstance(value, dict), label + "_REFERENCE_REQUIRED")
        path = absolute_file(value.get("path"), "Town " + label)
        result = {"path": str(path), "sha256": value.get("sha256")}
        if "bytes" in value:
            result["bytes"] = integer(value["bytes"], "Town " + label + " bytes", 1, 512 * 1024 * 1024)
        return result

    def document(value, label):
        binding = ref(value, label)
        raw = refs.read(binding, "TOWN_FOREGROUND_" + label, TOWN_FOREGROUND_JSON_BYTES)
        doc = parse_json(raw, "Town " + label)
        require(isinstance(doc, dict), label + "_OBJECT_REQUIRED")
        return {"path": binding["path"], "sha256": binding["sha256"]}, doc

    authority_ref, authority = document(wrapper["authority"], "AUTHORITY")
    require(authority.get("schema") == "h3.art-town.root-component-candidate-input-authority.v1",
            "AUTHORITY_SCHEMA_UNSUPPORTED")
    execution = authority.get("execution_authority", {})
    require(isinstance(execution, dict)
            and execution.get("frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation") is True
            and execution.get("formal_Source_intake_or_index_or_deployment_authorized_here") is False,
            "PRIVATE_CANDIDATE_AUTHORITY_REQUIRED")
    vector_binding = authority.get("frozen_vector")
    vector_ref, vector = document(vector_binding, "VECTOR")
    parent = authority.get("parent")
    require(isinstance(parent, dict), "STABLE_PARENT_REQUIRED")
    parent_ref = ref(parent, "PARENT")
    parent_bytes = refs.read(parent_ref, "TOWN_FOREGROUND_PARENT", 512 * 1024 * 1024)
    require(_path_key(Path(parent_ref["path"])) == _path_key(source) and parent_ref["sha256"] == source_sha
            and parent_bytes == source_raw, "STABLE_PARENT_BINDING_DRIFT")
    pixels = checked_pair(parent.get("pixels"), "Town parent pixels")
    require(pixels == source_pixels and parent.get("bytes") == len(source_raw), "PARENT_SIZE_DRIFT")
    historical = vector.get("actual_parent_only")
    require(isinstance(historical, dict), "VECTOR_PARENT_REQUIRED")
    history_path = historical.get("path")
    require(isinstance(history_path, str) and history_path and "\0" not in history_path
            and Path(history_path).is_absolute(), "HISTORICAL_PARENT_PATH_REQUIRED")
    history_pixels = checked_pair(historical.get("pixels"), "Town historical parent pixels")
    history_bytes = integer(historical.get("bytes"), "Town historical parent bytes", 1, 512 * 1024 * 1024)
    require(historical.get("sha256") == source_sha and history_bytes == len(source_raw)
            and history_pixels == source_pixels, "HISTORICAL_PARENT_CUSTODY_DRIFT")
    same_parent_path = _path_key(Path(history_path)) == _path_key(source)
    require(same_parent_path or parent.get("same_exact_original_as_G") is True,
            "EXPLICIT_STABLE_PARENT_CUSTODY_REQUIRED")
    # The old G path is provenance, never an optional runtime fallback or input.
    custody = {"policy": ("same-canonical-parent-path.v1" if same_parent_path
                          else "explicit-root-approved-stable-parent-rebind.v1"),
               "stable_parent": {"path": str(source), "sha256": source_sha, "bytes": len(source_raw), "pixels": source_pixels},
               "vector_parent_history": {"path": history_path, "sha256": source_sha,
                                         "bytes": history_bytes, "pixels": history_pixels},
               "historical_path_is_runtime_dependency": same_parent_path}
    request_ref, _ = document(vector.get("actual_parent_request"), "REQUEST")
    standard_ref, _ = document(authority.get("art_standard"), "ART_STANDARD")
    if "art_standard_evidence_only" in vector:
        vector_standard = ref(vector["art_standard_evidence_only"], "VECTOR_ART_STANDARD")
        require({k: vector_standard[k] for k in ("path", "sha256")} == standard_ref, "ART_STANDARD_BINDING_DRIFT")
        refs.read(vector_standard, "TOWN_FOREGROUND_VECTOR_ART_STANDARD", TOWN_FOREGROUND_JSON_BYTES)
    native = vector.get("native_function")
    require(isinstance(native, dict), "NATIVE_FUNCTION_REQUIRED")
    native_ref, _ = document(native.get("proof"), "NATIVE_PROOF")
    require(vector.get("target_identity") == key[1]
            and isinstance(native.get("resource"), str)
            and native["resource"].upper() == key[1] + ".PCX", "NATIVE_RESOURCE_DRIFT")
    approved = authority.get("approved_shared_parent_geometry_for_candidate")
    require(isinstance(approved, dict), "APPROVED_GEOMETRY_REQUIRED")
    for field, expected in (("native_canvas", targets["canvas_1x"]),
                             ("native_crop", targets["crop_1x"]), ("native_origin", targets["origin_1x"])):
        require(checked_pair(approved.get(field), "Town " + field, 0 if field == "native_origin" else 1) == expected,
                "NATIVE_GEOMETRY_DRIFT")
    view = approved.get("source_view_half_open")
    require(isinstance(view, list) and len(view) == 4, "SOURCE_VIEW_REQUIRED")
    view = [integer(n, "Town source view") for n in view]
    rect = approved.get("source_rect_XYWH")
    require(isinstance(rect, list) and len(rect) == 4, "SOURCE_RECT_REQUIRED")
    rect = [integer(n, "Town source rect", 0 if i < 2 else 1) for i, n in enumerate(rect)]
    require(rect == source_rect and view == [rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]],
            "SOURCE_RECT_GEOMETRY_DRIFT")
    k = integer(approved.get("K"), "Town K", 1)
    offset = checked_pair(approved.get("pad_offset"), "Town offset", 0)
    registered = checked_pair(approved.get("registered_source_pixels"), "Town registered pixels")
    world = approved.get("native_world_XY")
    require(isinstance(world, list) and len(world) == 2, "NATIVE_WORLD_XY_REQUIRED")
    world = [integer(n, "Town native world coordinate", -16384) for n in world]
    require(k == padding["common_k"] and offset == padding["offset"]
            and registered == [n * k for n in targets["crop_1x"]]
            and _foreground_equal(approved.get("render_scales"), [3])
            and checked_pair(approved.get("candidate_pixels"), "Town candidate pixels")
            == [n * 3 for n in targets["crop_1x"]]
            and approved.get("selected_bbox_fit_or_recenter_allowed") is False, "PADDING_GEOMETRY_DRIFT")
    polygons = vector.get("reference_geometry_polygons")
    require(isinstance(polygons, list) and 1 <= len(polygons) <= 64, "POLYGON_COUNT_LIMIT")
    ids, edges, work = [], 0, 0
    for polygon in polygons:
        require(isinstance(polygon, dict) and isinstance(polygon.get("id"), str) and polygon["id"]
                and polygon.get("closed") is True, "CLOSED_RING_REQUIRED")
        ids.append(polygon["id"])
        points = polygon.get("points")
        require(isinstance(points, list) and 4 <= len(points) <= 257, "RING_VERTEX_LIMIT")
        for point in points:
            require(isinstance(point, list) and len(point) == 2, "VERTEX_PAIR_REQUIRED")
            for axis, n in enumerate(point):
                integer(n, "Town vertex", 0, source_pixels[axis])
        require(points[0] == points[-1] and all(a != b for a, b in zip(points, points[1:])),
                "RING_NOT_CLOSED_OR_ZERO_EDGE")
        edge_count = len(points) - 1
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        require(area > 0, "EMPTY_RING_BOUNDS")
        edges += edge_count
        work += area * edge_count
        require(edges <= 2048 and work <= 100_000_000, "RASTER_WORK_LIMIT")
    require(len(set(ids)) == len(ids) and vector_binding.get("polygon_ids") == ids
            and type(vector_binding.get("polygons")) is int and vector_binding["polygons"] == len(polygons),
            "FROZEN_POLYGON_SET_DRIFT")
    Image, _, _ = _pillow()
    with Image.open(io.BytesIO(source_raw)) as image:
        rgba = image.convert("RGBA")
        histogram = rgba.getchannel("A").histogram()
        rgba_sha = sha256_bytes(rgba.tobytes())
    if not same_parent_path:
        require(parent.get("whole_RGBA_sha256") == historical.get("whole_RGBA_sha256") == rgba_sha,
                "HISTORICAL_PARENT_RGBA_CUSTODY_DRIFT")
    expected = {"schema": TOWN_FOREGROUND_PROOF_SCHEMA,
                "operation": "same-grid-positive-ring-union-copy-before-one-quality-sample",
                "inputs": {"authority": authority_ref, "vector": vector_ref, "request": request_ref,
                           "art_standard": standard_ref, "native_proof": native_ref},
                "parent_custody": custody, "source_pixels": source_pixels, "source_rect_xywh": rect,
                "registered_pixels": registered, "common_k": k, "offset": offset,
                "polygons": len(polygons), "edges": edges, "raster_work_bound": work,
                "source_rgba8_sha256": rgba_sha,
                "parent_positive_pixels": sum(histogram[1:]), "parent_alpha1_pixels": histogram[1],
                "selected_positive_outside_view": 0, "selected_alpha1_outside_view": 0,
                "selected_linear_premultiplied_samples_bit_identical": True,
                "sampling_calls": 1, "intermediate_png_files": 0, "Source_art_approved": False}
    return expected, list(refs.files.values())


def _verify_foreground_proof(native_row, bound):
    if "foreground_selection" not in bound:
        if "foreground_selection" in native_row or "foreground_selection_proof" in native_row:
            raise ReviewError("NATIVE_RENDERER_FOREGROUND_PROOF_WRONG_ROLE")
        return
    expected = bound["_foreground_selection_expected_proof"]
    proof = native_row.get("foreground_selection_proof")
    measured = {"selected_source_rgba8_sha256", "selected_positive_pixels", "excluded_positive_pixels",
                "selected_alpha1_pixels", "excluded_alpha1_pixels"}
    if (not _foreground_equal(native_row.get("foreground_selection"), bound["foreground_selection"])
            or not isinstance(proof, dict) or proof.keys() != expected.keys() | measured
            or not _foreground_equal({k: proof[k] for k in expected}, expected)):
        raise ReviewError("NATIVE_RENDERER_FOREGROUND_PROOF_MISMATCH")
    digest = proof["selected_source_rgba8_sha256"]
    if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise ReviewError("NATIVE_RENDERER_FOREGROUND_PROOF_MISMATCH")
    for key in measured - {"selected_source_rgba8_sha256"}:
        if type(proof[key]) is not int or not 0 <= proof[key] <= bound["source_pixels"][0] * bound["source_pixels"][1]:
            raise ReviewError("NATIVE_RENDERER_FOREGROUND_PROOF_MISMATCH")
    if (proof["selected_positive_pixels"] + proof["excluded_positive_pixels"] != proof["parent_positive_pixels"]
            or proof["selected_alpha1_pixels"] + proof["excluded_alpha1_pixels"] != proof["parent_alpha1_pixels"]
            or proof["selected_alpha1_pixels"] > proof["selected_positive_pixels"]
            or proof["excluded_alpha1_pixels"] > proof["excluded_positive_pixels"]):
        raise ReviewError("NATIVE_RENDERER_FOREGROUND_PROOF_MISMATCH")


def _verify_foreground_inputs(metadata, phase):
    for row in metadata:
        for ref in row.get("foreground_inputs", []):
            path = absolute_file(ref["path"], "Town foreground input " + phase)
            label = "Town foreground input " + phase
            try:
                raw = read_bounded(path, ref["bytes"], label)
            except ReviewError as error:
                raise ReviewError("SOURCE_SHA256_MISMATCH: " + label + "; " + str(error)) from error
            if sha256_bytes(raw) != ref["sha256"]:
                raise ReviewError("SOURCE_SHA256_MISMATCH: " + label)


def _resolve_entry(item: dict[str, Any], rows: dict[tuple[Any, ...], dict[str, Any]],
                   inventory_path: Path, inventory_sha: str, *, native_context: dict | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    exact_fields(item, {"id", "key", "source_png", "source_sha256"},
                 {"source_rect", "registration", "reference", "visual_reference", "source_layout", "render_scales", "authored_view", "native_layer_evidence", "padding_registration", "output_layout", "foreground_selection"}, "manifest item")
    item_id = item["id"]
    if not isinstance(item_id, str) or not item_id.isascii() or not item_id.replace("_", "").replace("-", "").isalnum() or len(item_id) >= 100:
        raise ReviewError("ITEM_ID_INVALID")
    key = key_tuple(item["key"])
    if key not in rows:
        raise ReviewError(f"OFFICIAL_SLOT_NOT_FOUND: {key}")
    targets = official_targets(rows[key], key)
    source = absolute_file(item["source_png"], f"{item_id}.source_png")
    if source.suffix.lower() != ".png":
        raise ReviewError(f"SOURCE_PNG_EXTENSION_REQUIRED: {item_id}")
    source_sha = item["source_sha256"]
    source_raw, source_pixels = (_read_bound_png(source, source_sha, f"{item_id}.source_png", pixel_limit=16 * 1024 * 1024)
                                 if "foreground_selection" in item
                                 else _read_bound_png(source, source_sha, f"{item_id}.source_png"))
    source_layout = item.get("source_layout", "native-crop")
    if source_layout not in {"native-crop", "native-canvas"}:
        raise ReviewError(f"SOURCE_LAYOUT_UNSUPPORTED: {item_id}")
    output_layout = item.get("output_layout", "native-canvas")
    if output_layout not in {"native-canvas", "native-crop"}:
        raise ReviewError(f"OUTPUT_LAYOUT_UNSUPPORTED: {item_id}")
    crop = targets["canvas_1x"] if source_layout == "native-canvas" else targets["crop_1x"]
    authored = item.get("registration") in {"authored-atlas-window-3x", "authored-source-view-3x"}
    if "authored_view" in item and not authored:
        raise ReviewError("AUTHORED_VIEW_REQUIRES_EXPLICIT_ATLAS_MODE")
    authored_view, alpha_cap, authored_inputs = _resolve_authored_view(
        item, key, targets, source, source_sha, source_pixels, inventory_sha, crop) if authored else (None, None, [])
    state_view = authored_view if item.get("registration") == "authored-source-view-3x" else None
    atlas_view = authored_view if item.get("registration") == "authored-atlas-window-3x" else None
    source_rect, raw_rect_size, registration, common_k, exact_ratio = _positive_rect(
        state_view["source_rect_xywh"] if state_view else item.get("source_rect"), source_pixels, crop, item_id)
    if "registration" in item:
        if item["registration"] not in {"strict-aspect", "integer-transparent-padding", "full-viewport-axis-normalization", "full-viewport-axis-normalization-3x", "authored-atlas-window-3x", "authored-source-view-3x"}:
            raise ReviewError(f"REGISTRATION_MODE_INVALID: {item_id}")
        registration = item["registration"]
    full_rect = [0, 0, *source_pixels]
    viewport_mode = registration in {"full-viewport-axis-normalization", "full-viewport-axis-normalization-3x"}
    padding = None
    if "padding_registration" in item:
        if registration != "integer-transparent-padding":
            raise ReviewError(f"PADDING_REGISTRATION_REQUIRES_PADDING_MODE: {item_id}")
        value = item["padding_registration"]
        exact_fields(value, {"common_k", "offset"}, label=f"{item_id}.padding_registration")
        k = integer(value["common_k"], "padding common_k", 1)
        offset = value["offset"]
        if not isinstance(offset, list) or len(offset) != 2:
            raise ReviewError(f"PADDING_OFFSET_REQUIRED: {item_id}")
        offset = [integer(n, "padding offset") for n in offset]
        width, height = (crop[axis] * k for axis in range(2))
        if (k < common_k or width > 16384 or height > 16384 or width * height > 64 * 1024 * 1024
                or any(offset[axis] + raw_rect_size[axis] > (width, height)[axis] for axis in range(2))):
            raise ReviewError(f"PADDING_REGISTRATION_DOMAIN_INVALID: {item_id}")
        padding = {"common_k": k, "offset": offset}
        common_k = k
    if viewport_mode and source_rect not in (None, full_rect):
        raise ReviewError(f"FULL_VIEWPORT_REQUIRES_WHOLE_SOURCE: {item_id}")
    Image, _, _ = _pillow()
    with Image.open(io.BytesIO(source_raw)) as image:
        alpha = image.convert("RGBA").getchannel("A")
        source_positive = sum(alpha.histogram()[1:])
        x, y, width, height = source_rect or full_rect
        retained_positive = sum(alpha.crop((x, y, x + width, y + height)).histogram()[1:])
    source_view_proof = {"source_positive_pixels": source_positive,
                         "retained_positive_pixels": retained_positive,
                         "excluded_positive_pixels": source_positive - retained_positive,
                         "all_positive_preserved": source_positive == retained_positive}
    foreground = None
    if "foreground_selection" in item:
        foreground = _resolve_foreground_selection(item, key, targets, source, source_sha, source_pixels,
                                                   source_raw, source_rect, padding, registration)
    if registration == "integer-transparent-padding" and foreground is None and source_positive != retained_positive:
        raise ReviewError(f"POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT: {item_id}")
    if exact_ratio:
        if registration == "integer-transparent-padding" and padding is None:
            raise ReviewError(f"PADDING_ONLY_ALLOWED_FOR_ASPECT_MISMATCH: {item_id}")
        if not viewport_mode and not authored and registration != "integer-transparent-padding":
            registration = "strict-aspect"
    else:
        if registration != "integer-transparent-padding" and not viewport_mode and not authored:
            if source_rect is not None:
                raise ReviewError(f"SOURCE_RECT_ASPECT_MISMATCH: {item_id}")
            raise ReviewError(f"FULL_IMAGE_ASPECT_MISMATCH: {item_id}; explicit integer-transparent-padding opt-in required")
    capacity3x = raw_rect_size[0] >= crop[0] * 3 and raw_rect_size[1] >= crop[1] * 3
    if registration == "full-viewport-axis-normalization-3x" and not capacity3x:
        raise ReviewError(f"NEEDS_HIGHER_MASTER_FOR_3X: {item_id}")
    scales = item.get("render_scales", [3])
    if (not isinstance(scales, list) or any(type(n) is not int for n in scales)
            or scales not in ([1], [3], [1, 3])):
        raise ReviewError(f"RENDER_SCALES_MUST_BE_1_3_OR_1_3: {item_id}")
    if output_layout == "native-crop" and scales != [3]:
        raise ReviewError(f"CROP_OUTPUT_REQUIRES_NATIVE_CROP_3X_ONLY: {item_id}")
    if output_layout == "native-crop" and source_layout == "native-canvas" and registration != "strict-aspect":
        raise ReviewError(f"CANVAS_CROP_OUTPUT_REQUIRES_STRICT_ASPECT_3X_ONLY: {item_id}")
    if registration == "full-viewport-axis-normalization-3x" and scales != [3]:
        raise ReviewError(f"VIEWPORT_3X_MODE_REQUIRES_3X_ONLY: {item_id}")
    if 3 in scales and (not capacity3x or registration == "full-viewport-axis-normalization"):
        raise ReviewError(f"REQUESTED_3X_UNAVAILABLE: {item_id}")
    reference_spec = None
    if "reference" in item:
        reference = item["reference"]
        exact_fields(reference, {"path", "sha256", "layout"}, label=f"{item_id}.reference")
        reference_path = absolute_file(reference["path"], f"{item_id}.reference.path")
        if reference_path.suffix.lower() != ".png":
            raise ReviewError(f"REFERENCE_PNG_EXTENSION_REQUIRED: {item_id}")
        reference_sha = reference["sha256"]
        _, reference_pixels = bound_png(reference_path, reference_sha, f"{item_id}.reference")
        layout = reference["layout"]
        if layout == "native-crop":
            expected = targets["crop_1x"]
        elif layout == "native-canvas":
            expected = targets["canvas_1x"]
        else:
            raise ReviewError(f"REFERENCE_LAYOUT_INVALID: {item_id}")
        if reference_pixels != expected:
            raise ReviewError(f"REFERENCE_GEOMETRY_MISMATCH: {item_id}")
        reference_spec = {"path": str(reference_path), "sha256": reference_sha,
                          "layout": layout, "source_pixels": reference_pixels}
    visual_reference = None
    if "visual_reference" in item:
        visual = item["visual_reference"]
        exact_fields(visual, {"role", "scale", "layout", "path", "sha256"}, label=f"{item_id}.visual_reference")
        if visual["role"] != "visual-only" or type(visual["scale"]) is not int or visual["scale"] != 3:
            raise ReviewError(f"VISUAL_REFERENCE_ROLE_OR_SCALE_INVALID: {item_id}")
        layout = visual["layout"]
        if layout not in ("native-crop", "native-canvas"):
            raise ReviewError(f"VISUAL_REFERENCE_LAYOUT_INVALID: {item_id}")
        visual_path = absolute_file(visual["path"], f"{item_id}.visual_reference.path")
        if visual_path.suffix.lower() != ".png":
            raise ReviewError(f"VISUAL_REFERENCE_PNG_EXTENSION_REQUIRED: {item_id}")
        _, visual_pixels = _read_bound_png(visual_path, visual["sha256"], f"{item_id}.visual_reference")
        expected = targets["crop_1x" if layout == "native-crop" else "canvas_1x"]
        if visual_pixels != [value * 3 for value in expected]:
            raise ReviewError(f"VISUAL_REFERENCE_GEOMETRY_MISMATCH: {item_id}")
        visual_reference = {"role": "visual-only", "scale": 3, "layout": layout,
                            "path": str(visual_path), "sha256": visual["sha256"],
                            "pixels": visual_pixels, **targets}
    binding = {"inventory_sha256": inventory_sha, "key": list(key), "targets": targets}
    geometry_binding_sha = _canonical_json_sha(binding)
    spec_item = {"id": item_id, "key": list(key), "source_png": str(source), "source_sha256": source_sha,
                 "source_pixels": source_pixels, "source_rect": source_rect,
                 "registration": registration, "source_layout": source_layout,
                 "render_scales": scales, "targets": targets,
                 "geometry_binding_sha256": geometry_binding_sha, "reference": reference_spec}
    if padding is not None:
        spec_item["padding_registration"] = padding
    if "output_layout" in item:
        spec_item["output_layout"] = output_layout
    if foreground is not None:
        spec_item["foreground_selection"] = item["foreground_selection"]
    if authored:
        spec_item["state_view" if state_view else "atlas_view"] = authored_view
        if alpha_cap is not None:
            spec_item["alpha_cap_3x"] = alpha_cap
    if registration == "full-viewport-axis-normalization":
        capacity_3x = "FULL_VIEWPORT_AXIS_NORMALIZATION_1X_DIAGNOSTIC_ONLY"
    elif registration == "full-viewport-axis-normalization-3x":
        capacity_3x = "FULL_VIEWPORT_AXIS_NORMALIZATION_3X_SOURCE_DIMENSIONS_ONLY"
    elif raw_rect_size[0] >= crop[0] * 3 and raw_rect_size[1] >= crop[1] * 3:
        capacity_3x = "POSSIBLE_3X_EFFECTIVE_RECT_UNVERIFIED"
    else:
        capacity_3x = "UNDER_3X_SOURCE_RECT_DIMENSION_UPPER_BOUND"
    metadata = {"id": item_id, "key": list(key), "label": rows[key].get("label", ""),
                "resource": rows[key].get("resource", ""), "source_png": str(source),
                "source_sha256": source_sha, "source_pixels": source_pixels,
                "source_rect": source_rect if source_rect is not None else [0, 0, *source_pixels],
                "source_rect_pixels": raw_rect_size, "registration": registration, "common_k": common_k,
                "source_layout": source_layout, "render_scales": scales, "source_view_proof": source_view_proof,
                "source_capacity_1x": "SOURCE_AT_LEAST_NATIVE_CROP_DIMENSIONS",
                "source_capacity_3x": capacity_3x,
                "source_rect_effective_pixels_verified": False,
                "targets": targets, "geometry_binding_sha256": geometry_binding_sha,
                "reference": reference_spec}
    if visual_reference is not None:
        metadata["visual_reference"] = visual_reference
    if padding is not None:
        metadata["padding_registration"] = padding
    if "output_layout" in item:
        metadata["output_layout"] = output_layout
    if foreground is not None:
        metadata.update(foreground_selection=item["foreground_selection"],
                        _foreground_selection_expected_proof=foreground[0], foreground_inputs=foreground[1])
    if authored:
        metadata["state_view" if state_view else "atlas_view"] = authored_view
        metadata.update(alpha_cap_3x=alpha_cap, authored_inputs=authored_inputs,
                        common_k=None,
                        source_capacity_3x="AUTHORED_STATE_3X_SOURCE_DIMENSIONS_ONLY" if state_view else "AUTHORED_3X_ATLAS_SOURCE_DIMENSIONS_ONLY",
                        source_view_proof_scope="declared UI-state sampling input; other atlas states excluded" if state_view
                        else "whole-master sampling input; output is the declared authored state only")
    if "native_layer_evidence" in item:
        binding, inputs = _resolve_native_layers(item["native_layer_evidence"], key, rows[key], targets,
                                                 native_context if native_context is not None else {})
        metadata.update(native_reference_layers=binding, native_layer_inputs=inputs)
    return spec_item, metadata


def _verify_visual_references(items: list[dict[str, Any]], phase: str) -> None:
    for item in items:
        if "visual_reference" in item:
            visual = item["visual_reference"]
            label = f"{item['id']}.visual_reference {phase}"
            path = absolute_file(visual["path"], label)
            file_sha_matches(path, visual["sha256"], label)


def _validate_padding_groups(items: list[dict[str, Any]]) -> None:
    groups: dict[tuple, list[dict]] = {}
    for item in items:
        key = item["key"]
        groups.setdefault(tuple(key[:3] if key[0] == "def" else key), []).append(item)
    for rows in groups.values():
        explicit = [row for row in rows if "padding_registration" in row]
        if not explicit:
            continue
        expected = (explicit[0]["padding_registration"]["common_k"], explicit[0].get("source_layout", "native-crop"))
        if any("padding_registration" not in row or
               (row["padding_registration"]["common_k"], row.get("source_layout", "native-crop")) != expected
               for row in rows):
            raise ReviewError("PADDING_GROUP_CONTRACT_MISMATCH")


def _verify_canvas_crop_proof(proof: Any, targets: dict[str, list[int]], diagnostics: dict) -> None:
    positive = diagnostics["alpha_envelope_by_threshold"]["1"]["visible_pixels"]
    alpha1 = diagnostics["alpha1_pixels"]
    expected = {
        "operation": "single-native-canvas-sample-then-exact-rgba8-crop",
        "sampled_canvas_pixels": [n * 3 for n in targets["canvas_1x"]],
        "crop_rect_3x": [n * 3 for n in targets["origin_1x"] + targets["crop_1x"]],
        "positive_alpha_pixels_before_crop": positive, "positive_alpha_pixels_after_crop": positive,
        "alpha1_pixels_before_crop": alpha1, "alpha1_pixels_after_crop": alpha1,
        "excluded_positive_alpha_pixels": 0, "sampling_calls": 1, "intermediate_png_files": 0,
    }
    if (not isinstance(proof, dict) or proof != expected
            or any(type(proof[key]) is not type(value) for key, value in expected.items())
            or any(type(n) is not int for key in ("sampled_canvas_pixels", "crop_rect_3x") for n in proof[key])):
        raise ReviewError("NATIVE_RENDERER_CANVAS_CROP_PROOF_MISMATCH")


def _metrics(raw: bytes, scale: int, targets: dict[str, list[int]],
             output_layout: str = "native-canvas") -> dict[str, Any]:
    Image, ImageChops, _ = _pillow()
    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGBA")
    if output_layout == "native-crop":
        if list(image.size) != [n * scale for n in targets["crop_1x"]]:
            raise ReviewError("CROP_OUTPUT_GEOMETRY_MISMATCH")
        canvas = Image.new("RGBA", tuple(n * scale for n in targets["canvas_1x"]))
        canvas.paste(image, tuple(n * scale for n in targets["origin_1x"]))
        image = canvas
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    alpha_sum = sum(index * count for index, count in enumerate(histogram))
    partial_alpha_pixels = sum(histogram[1:255])
    alpha1_pixels = histogram[1]

    # Alpha=1 remains attached source data and is never treated as a color
    # failure. The geometry diagnostic starts at alpha>=2 so owners can
    # review high-alpha edge/external residue without losing faint pixels or
    # rejecting legitimate wood/metal/rock colors.
    high_alpha = alpha.point(lambda value: 255 if value >= 2 else 0)
    high_alpha_pixels = high_alpha.histogram()[255]
    high_alpha_box = high_alpha.getbbox()
    width, height = image.size
    crop_x, crop_y = (int(value) * scale for value in targets["origin_1x"])
    crop_w, crop_h = (int(value) * scale for value in targets["crop_1x"])
    crop_rect = (max(0, crop_x), max(0, crop_y),
                 min(width, crop_x + crop_w), min(height, crop_y + crop_h))
    crop_mask = Image.new("L", image.size, 0)
    if crop_rect[2] > crop_rect[0] and crop_rect[3] > crop_rect[1]:
        crop_mask.paste(255, crop_rect)
    outside_mask = ImageChops.multiply(high_alpha, ImageChops.invert(crop_mask))
    outside_alpha1 = ImageChops.multiply(
        alpha.point(lambda value: 255 if value == 1 else 0),
        ImageChops.invert(crop_mask),
    )
    border_mask = Image.new("L", image.size, 0)
    border_mask.paste(255, (0, 0, width, 1))
    border_mask.paste(255, (0, height - 1, width, height))
    border_mask.paste(255, (0, 0, 1, height))
    border_mask.paste(255, (width - 1, 0, width, height))
    high_alpha_edge_pixels = ImageChops.multiply(high_alpha, border_mask).histogram()[255]

    def _box(value: tuple[int, int, int, int] | None) -> list[int] | None:
        return list(value) if value is not None else None

    def _composite(background: tuple[int, int, int]) -> dict[str, Any]:
        base = Image.new("RGBA", image.size, (*background, 255))
        composited = Image.alpha_composite(base, image)
        return {"rgb": list(background), "pixels": [width, height],
                "rgba_sha256": sha256_bytes(composited.tobytes())}

    outside_high_alpha_pixels = outside_mask.histogram()[255]
    alpha_ge2_geometry = {
        "threshold": 2,
        "pixels": high_alpha_pixels,
        "bbox_canvas_pixels": _box(high_alpha_box),
        "touches_canvas_border": bool(high_alpha_edge_pixels),
        "edge_pixels": high_alpha_edge_pixels,
        "outside_native_crop_pixels": outside_high_alpha_pixels,
        "outside_native_crop_bbox_canvas_pixels": _box(outside_mask.getbbox()),
        "edge_or_external": bool(high_alpha_edge_pixels or outside_high_alpha_pixels),
        "review_hint": ("ALPHA_GE2_EDGE_OR_EXTERNAL_REVIEW_REQUIRED"
                        if high_alpha_edge_pixels or outside_high_alpha_pixels
                        else "NO_ALPHA_GE2_EDGE_OR_EXTERNAL_PIXELS"),
    }
    # Keep low-alpha/background concerns as a bounded, read-only diagnostic.
    # The native sampler and writer never consume this field, and alpha=1 is
    # retained as source data.  A body bbox is required before calling a faint
    # region a halo; an all-faint image has no opaque reference for that claim.
    body_alpha = alpha.point(lambda value: 255 if value >= 128 else 0)
    body_box = body_alpha.getbbox()
    low_alpha = alpha.point(lambda value: 255 if 1 <= value < 128 else 0)
    rgb = image.convert("RGB")
    channels = rgb.split()
    max_rgb = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    dark_low_alpha = ImageChops.multiply(
        low_alpha, max_rgb.point(lambda value: 255 if value <= 32 else 0))
    low_alpha_outside = None
    dark_low_alpha_outside = None
    if body_box is not None:
        body_mask = Image.new("L", image.size, 0)
        body_mask.paste(255, body_box)
        outside_body = ImageChops.invert(body_mask)
        low_alpha_outside = ImageChops.multiply(low_alpha, outside_body)
        dark_low_alpha_outside = ImageChops.multiply(dark_low_alpha, outside_body)
    low_alpha_pixels = low_alpha.histogram()[255]
    dark_low_alpha_pixels = dark_low_alpha.histogram()[255]
    low_alpha_halo = {
        "schema": "h3.native-review.low-alpha-halo-diagnostic.v1",
        "body_alpha_threshold": 128,
        "low_alpha_range": [1, 127],
        "dark_rgb_max": 32,
        "body_alpha128_bbox_canvas_pixels": _box(body_box),
        "low_alpha_pixels": low_alpha_pixels,
        "dark_rgb_low_alpha_pixels": dark_low_alpha_pixels,
        "dark_rgb_low_alpha_fraction": (dark_low_alpha_pixels / low_alpha_pixels
                                         if low_alpha_pixels else 0.0),
        "low_alpha_outside_alpha128_bbox_pixels": (None if low_alpha_outside is None
                                                    else low_alpha_outside.histogram()[255]),
        "dark_rgb_low_alpha_outside_alpha128_bbox_pixels": (
            None if dark_low_alpha_outside is None
            else dark_low_alpha_outside.histogram()[255]),
        "review_hint": (
            "NO_ALPHA128_REFERENCE_FOR_LOW_ALPHA_HALO"
            if body_box is None else
            "LOW_ALPHA_OUTSIDE_ALPHA128_REVIEW_REQUIRED"
            if low_alpha_outside is not None and low_alpha_outside.histogram()[255]
            else "LOW_ALPHA_DARK_RGB_REVIEW_REQUIRED"
            if dark_low_alpha_pixels else "NO_LOW_ALPHA_HALO_REVIEW_HINT"),
        "failure_semantics": "diagnostic-only; no automatic alpha cleanup, art approval, or writer/sampler path",
    }
    thresholds: dict[str, Any] = {}
    for threshold in (1, 16, 128):
        mask = alpha.point(lambda value, t=threshold: 255 if value >= t else 0)
        box = mask.getbbox()
        visible_pixels = sum(histogram[threshold:])
        if box is None:
            thresholds[str(threshold)] = {"bbox_canvas_pixels": None, "height_1x": 0.0,
                                          "bottom_y_1x": None, "visible_pixels": 0,
                                          "touches_canvas_border": False}
            continue
        left, top, right, bottom = box
        thresholds[str(threshold)] = {
            "bbox_canvas_pixels": [left, top, right, bottom],
            "height_1x": (bottom - top) / scale,
            "bottom_y_1x": (bottom - 1) / scale,
            "visible_pixels": visible_pixels,
            "touches_canvas_border": left == 0 or top == 0 or right == image.width or bottom == image.height,
        }
    return {
        "measured_scope": "whole rendered alpha envelope; not semantic body segmentation",
        "alpha_coverage_area_1x_equivalent": alpha_sum / 255.0 / (scale * scale),
        "partial_alpha_pixels": partial_alpha_pixels,
        "alpha1_pixels": alpha1_pixels,
        "alpha1_outside_native_crop_pixels": outside_alpha1.histogram()[255],
        "alpha_ge2_geometry": alpha_ge2_geometry,
        "low_alpha_halo": low_alpha_halo,
        "background_composite_evidence": {
            "schema": "h3.native-review.background-composite-evidence.v1",
            "algorithm": "straight-alpha-over-solid-rgb-v1",
            "source_rgba_preserved": True,
            "backgrounds": {"white": _composite((255, 255, 255)),
                              "dark": _composite((0, 0, 0)),
                              "grass": _composite((74, 94, 62))},
            "review_only": True,
        },
        "alpha_envelope_by_threshold": thresholds,
        "foot_semantics": "not validated; bottom edge of whole-image alpha envelope only",
        "canvas_pixels": [image.width, image.height],
        "crop_1x": targets["crop_1x"], "origin_1x": targets["origin_1x"],
    }


def _comparison(left_raw: bytes, right_raw: bytes) -> dict[str, Any]:
    Image, ImageChops, ImageStat = _pillow()
    with Image.open(io.BytesIO(left_raw)) as image:
        left = image.convert("RGBA")
    with Image.open(io.BytesIO(right_raw)) as image:
        right = image.convert("RGBA")
    if left.size != right.size:
        raise ReviewError("REFERENCE_COMPARISON_GEOMETRY_MISMATCH")
    la, ra = left.getchannel("A"), right.getchannel("A")
    result: dict[str, Any] = {"pixels": list(left.size), "all_metrics_are_diagnostic": True}
    for threshold in (1, 16, 128):
        lm = la.point(lambda value, t=threshold: 255 if value >= t else 0)
        rm = ra.point(lambda value, t=threshold: 255 if value >= t else 0)
        intersection = ImageChops.multiply(lm, rm).histogram()[255]
        union = ImageChops.lighter(lm, rm).histogram()[255]
        result[f"binary_alpha_iou_at_{threshold}"] = intersection / union if union else 1.0
    alpha_diff = ImageChops.difference(la, ra)
    result["alpha_mae"] = ImageStat.Stat(alpha_diff).mean[0] / 255.0
    rgb_diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    result["straight_rgb_mae"] = sum(ImageStat.Stat(rgb_diff).mean) / (3.0 * 255.0)
    return result


def _read_build_receipt(renderer_sha: str, renderer_path: Path | None = None, *,
                        additional_companions: tuple[str, ...] = ()) -> dict[str, Any]:
    # Published executables use the immutable release source closure, never the
    # mutable build/candidate receipt being rewritten by a later build.
    released = PRODUCT / "dist" / "H3TierExport.exe"
    published = (renderer_path is None or _path_key(renderer_path) == _path_key(released)) and released.is_file() and sha256_file(released) == renderer_sha
    authority = "formal_release" if published else "candidate_build"
    frozen_tests = None
    runtime_release = None
    if runtime_frozen_root() is not None:
        runtime_release = runtime_read_release(renderer_sha, renderer_path)
    if runtime_release is not None:
        published, authority = True, "formal_release"
        receipt_path, raw = runtime_release["path"], runtime_release["raw"]
        frozen_tests = runtime_release["tests"]
    elif published:
        released_report = PRODUCT / "dist" / "H3TierExport-release.json"
        current_report = PRODUCT / "reports" / "current" / "release.json"
        release_raw = read_bounded(released_report, 8 * 1024 * 1024, "published release")
        if read_bounded(current_report, 8 * 1024 * 1024, "release mirror") != release_raw:
            raise ReviewError("RELEASE_REPORT_COPY_MISMATCH")
        release = parse_json(release_raw, "published release")
        if (release.get("schema") != "h3.tier-export.release.v2"
                or release.get("exe_sha256") != renderer_sha):
            raise ReviewError("RELEASE_EXE_IDENTITY_MISMATCH")
        receipt_path = PRODUCT / "dist" / "H3TierExport-build-receipt.json"
        raw = read_bounded(receipt_path, 32 * 1024 * 1024, "frozen build receipt")
        if sha256_bytes(raw) != release.get("build_receipt_sha256"):
            raise ReviewError("RELEASE_FROZEN_BUILD_RECEIPT_DRIFT")
        rows = release.get("files")
        if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
            raise ReviewError("RELEASE_FILE_ROWS_INVALID")
        expected = {str(released): renderer_sha, str(receipt_path): sha256_bytes(raw)}
        tests_path = PRODUCT / "dist" / "H3TierExport-tests-receipt.json"
        tests_raw = read_bounded(tests_path, 64 * 1024 * 1024, "frozen test receipt")
        tests_sha = sha256_bytes(tests_raw)
        if tests_sha != release.get("test_receipt_sha256"):
            raise ReviewError("RELEASE_FROZEN_TEST_RECEIPT_DRIFT")
        tests = parse_json(tests_raw, "frozen test receipt")
        if (tests.get("schema") != "h3.tier-export.test-suite.v2" or tests.get("passed") is not True
                or tests.get("source_current") is not True or tests.get("exe_sha256") != renderer_sha
                or tests.get("build_receipt_sha256") != sha256_bytes(raw)):
            raise ReviewError("RELEASE_TEST_BUILD_IDENTITY_MISMATCH")
        results = tests.get("results")
        if (not isinstance(results, list) or not results
                or any(not isinstance(row, dict) or type(row.get("exit_code")) is not int
                       or row["exit_code"] != 0 for row in results)):
            raise ReviewError("RELEASE_TEST_RESULTS_INVALID")
        expected[str(tests_path)] = tests_sha
        frozen_tests = {"path": str(tests_path), "sha256": tests_sha}
        for file_path, expected_sha in expected.items():
            allowed_paths={file_path,Path(file_path).name}
            if sum(1 for record in rows if record.get("path") in allowed_paths
                   and record.get("sha256") == expected_sha) != 1:
                raise ReviewError("RELEASE_FILE_IDENTITY_MISMATCH")
    else:
        receipt_path = PRODUCT / "build" / "candidate" / "build-receipt.json"
        raw = read_bounded(receipt_path, 32 * 1024 * 1024, "candidate build receipt")
    receipt = parse_json(raw, "build receipt")
    if (receipt.get("schema") != "h3.tier-export.build.v2"
            or not receipt.get("passed") or not receipt.get("source_current")):
        raise ReviewError("BUILD_RECEIPT_NOT_CURRENT")
    if receipt.get("executable", {}).get("sha256") != renderer_sha:
        raise ReviewError("RENDERER_SHA_DIFFERS_FROM_CURRENT_BUILD")
    if authority == "candidate_build" and receipt.get("companion_runtime") is not None:
        from build import validate_build_sources
        try:
            validate_build_sources(receipt)
        except RuntimeError as error:
            raise ReviewError(str(error)) from error
    source_entries = receipt.get("sources")
    if not isinstance(source_entries, list):
        raise ReviewError("BUILD_SOURCE_ROWS_INVALID")
    rows: dict[str, str] = {}
    for entry in source_entries:
        if not isinstance(entry, dict):
            raise ReviewError("BUILD_SOURCE_ROW_INVALID")
        relative = _normalize_build_source_path(entry.get("path"))
        if relative in rows:
            raise ReviewError(f"BUILD_SOURCE_PATH_DUPLICATE: {relative}")
        source_sha = entry.get("sha256")
        if (not isinstance(source_sha, str) or len(source_sha) != 64
                or any(char not in "0123456789abcdef" for char in source_sha)):
            raise ReviewError(f"BUILD_SOURCE_SHA256_INVALID: {relative}")
        rows[relative] = source_sha
    source_rows = []
    for relative in ("src/native_review.hpp", "src/resample.hpp", "src/wic.hpp", "src/main.cpp"):
        if relative not in rows:
            raise ReviewError(f"BUILD_SOURCE_ROW_MISSING: {relative}")
        if authority == "candidate_build":
            if rows[relative] != sha256_file(PRODUCT / relative):
                raise ReviewError(f"BUILD_SOURCE_STALE: {relative}")
        source_rows.append({"path": relative, "sha256": rows[relative]})
    if authority == "formal_release":
        # Bind the physical modules executing this operation. A frozen release
        # must never substitute modules from the next development checkout.
        for relative in ("scripts/review_native_batch.py", "scripts/workspace.py"):
            if (relative not in rows or
                    sha256_file(runtime_source_path(relative), 32 * 1024 * 1024) != rows[relative]):
                raise ReviewError(f"RELEASE_COMPANION_SOURCE_DRIFT: {relative}")
    companions = []
    for relative in additional_companions:
        if relative not in rows or sha256_file(runtime_source_path(relative), 32 * 1024 * 1024) != rows[relative]:
            raise ReviewError(f"COMPANION_SOURCE_DRIFT: {relative}")
        companions.append({"path": relative, "sha256": rows[relative]})
    return {"path": str(receipt_path.resolve()), "sha256": sha256_bytes(raw),
            "executable_sha256": renderer_sha, "authority": authority, "test_receipt": frozen_tests,
            "kernel_sources": source_rows, "companion_sources": companions}


def _renderer_capabilities(renderer: Path) -> dict[str, Any]:
    try:
        process = subprocess.run([str(renderer), "capabilities"], shell=False, check=False,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired as error:
        raise ReviewError("RENDERER_CAPABILITIES_TIMEOUT") from error
    if process.returncode != 0:
        raise ReviewError(f"RENDERER_CAPABILITIES_FAILED: {process.stderr[-2000:]}")
    result = parse_json(process.stdout.encode("utf-8"), "renderer capabilities")
    native = result.get("native_commands", [])
    if not any(item.get("name") == "review-render" for item in native if isinstance(item, dict)):
        raise ReviewError("RENDERER_CAPABILITY_MISSING: review-render")
    return result


def _write_new(path: Path, data: bytes, output_guard: OutputRootGuard) -> None:
    path = output_guard.verify_target(path)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def operation_lease(output_guard: OutputRootGuard, *, create: bool):
    """Exclude cooperating operation writers while retaining the existing directory guard."""
    output_guard.verify_current()
    path = output_guard.root / ".operation.lock"
    reject_reparse_components(path, allow_missing_leaf=create)
    kernel, info_type, invalid = _windows_directory_api()
    handle = kernel.CreateFileW(os.fspath(path), 0xC0000000, 0, None,
                                4 if create else 3, 0x00200080, None)
    if getattr(handle, "value", handle) in (None, invalid):
        error = ctypes.get_last_error()
        raise ReviewError("OPERATION_IN_PROGRESS" if error in (32, 33)
                          else f"OPERATION_LEASE_FAILED: {error}")
    try:
        info = info_type()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ReviewError("OPERATION_LEASE_IDENTITY_UNAVAILABLE")
        if info.dwFileAttributes & (0x400 | 0x10) or info.nNumberOfLinks != 1:
            raise ReviewError("OPERATION_LEASE_FILE_INVALID")
        output_guard.verify_current()
        yield
    finally:
        kernel.CloseHandle(handle)


def write_exact_atomic(path: Path, data: bytes, output_guard: OutputRootGuard) -> bool:
    """Commit immutable bytes, or finish a matching prefix left by an interrupted write."""
    if _lexists(path):
        output_guard.verify_target(path, must_exist=True)
        if read_bounded(path, len(data), "existing immutable output") != data:
            raise ReviewError(f"OUTPUT_BYTES_DIVERGED: {path.name}")
        return False
    output_guard.verify_target(path)
    pending = path.with_name(f".{path.name}.{sha256_bytes(data)[:16]}.pending")
    if _lexists(pending):
        output_guard.verify_target(pending, must_exist=True)
        if pending.stat().st_nlink != 1:
            raise ReviewError("OUTPUT_PENDING_HARDLINK_REJECTED")
        prefix = (read_bounded(pending, len(data), "interrupted immutable output")
                  if pending.stat().st_size else b"")
        if not data.startswith(prefix):
            raise ReviewError(f"OUTPUT_PENDING_BYTES_DIVERGED: {path.name}")
        mode = "ab"
    else:
        output_guard.verify_target(pending)
        prefix, mode = b"", "xb"
    with pending.open(mode) as stream:
        stream.write(data[len(prefix):])
        stream.flush()
        os.fsync(stream.fileno())
    output_guard.verify_target(pending, must_exist=True)
    if read_bounded(pending, len(data), "pending readback") != data:
        raise ReviewError("OUTPUT_PENDING_READBACK_MISMATCH")
    output_guard.verify_target(path)
    os.rename(pending, path)  # Windows rename refuses an existing destination.
    output_guard.verify_target(path, must_exist=True)
    if read_bounded(path, len(data), "committed readback") != data:
        raise ReviewError("OUTPUT_COMMIT_READBACK_MISMATCH")
    return True


def _write_html(outdir: Path, items: list[dict[str, Any]], rows: list[dict[str, Any]],
                output_guard: OutputRootGuard) -> None:
    cards = []
    for item, row in zip(items, rows, strict=True):
        slots = []
        for key, label in (("reference_1x", "参考 1X"), ("visual_reference", "视觉参考 3X（仅显示）"), ("candidate_1x", "新图 1X"),
                           ("candidate_3x", "新图 3X"), ("registration_padding_diagnostic", "透明填充诊断（非母版）")):
            visual = key == "visual_reference"
            record = item.get(key) if visual else row["outputs"].get(key)
            if not record:
                continue
            image_path = Path(record["path"])
            rel = html.escape(image_path.as_uri() if visual else os.path.relpath(image_path, outdir).replace("\\", "/"), quote=True)
            width, height = record["pixels"]
            three_x = visual or key == "candidate_3x"
            display = f' data-native-width="{width // 3}" data-native-height="{height // 3}"' if three_x else ""
            shown_width, shown_height = (width // 3, height // 3) if three_x else (width, height)
            picture = f'<img src="{rel}" width="{shown_width}" height="{shown_height}"{display} alt="{label}">'
            if three_x and record.get("layout") == "native-crop":
                canvas_w, canvas_h = item["targets"]["canvas_1x"]
                left, top = item["targets"]["origin_1x"]
                picture = (f'<span class="virtual-canvas" data-native-width="{canvas_w}" data-native-height="{canvas_h}" '
                           f'style="display:block;position:relative;width:{canvas_w}px;height:{canvas_h}px">'
                           f'<img src="{rel}" width="{shown_width}" height="{shown_height}"{display} '
                           f'data-native-left="{left}" data-native-top="{top}" '
                           f'style="position:absolute;left:{left}px;top:{top}px" alt="{label}"></span>')
            role = ' data-role="visual-only"' if visual else ""
            slots.append(f'<figure{role}><figcaption>{label} · 文件{width}×{height}</figcaption><a href="{rel}">{picture}</a></figure>')
        title = html.escape(str(item.get("label") or item["id"]))
        key_text = html.escape(json.dumps(item["key"], ensure_ascii=False))
        capacity = html.escape(item["source_capacity_3x"])
        note = "源稿不足3X，本项未生成3X；透明填充只用于位置诊断，不增加绘画细节。" if capacity.startswith("UNDER_3X") else "像素尺寸上界达到3X；有效绘画区域未核验，不代表素材通过。"
        source_uri = html.escape(Path(item["source_png"]).as_uri(), quote=True)
        rect_text = html.escape(json.dumps(item["source_rect"], ensure_ascii=False))
        k_text = "不适用（作者视图）" if item["common_k"] is None else str(item["common_k"])
        full_rect = [0, 0, *item["source_pixels"]]
        region_note = ("本项使用作者冻结的独立UI状态窗口，单次直采3X；图版其他状态不属于本项，源稿未改。"
                       if item.get("state_view") else "整母图按作者3X排版单次采样，仅复制声明的状态窗口；其他状态未获本槽审核批准。"
                       if item.get("atlas_view") else "本项使用完整母图。" if item["source_rect"] == full_rect
                       else "本项只审阅该裁片；框外内容未进入候选，也未获审阅通过。")
        region_html = f'<p><a href="{source_uri}">查看绑定母图</a>（源文件字节未修改）；source_rect [x,y,w,h]={rect_text}。{region_note}</p>'
        review_record = row["outputs"].get("candidate_3x") or row["outputs"].get("candidate_1x")
        evidence_html = ""
        if review_record and isinstance(review_record.get("diagnostics"), dict):
            diagnostics = review_record["diagnostics"]
            high_alpha = diagnostics.get("alpha_ge2_geometry", {})
            composites = diagnostics.get("background_composite_evidence", {}).get("backgrounds", {})
            white_sha = html.escape(str(composites.get("white", {}).get("rgba_sha256", "")))
            dark_sha = html.escape(str(composites.get("dark", {}).get("rgba_sha256", "")))
            grass_sha = html.escape(str(composites.get("grass", {}).get("rgba_sha256", "")))
            evidence_html = (
                f'<p>alpha≥2 几何诊断：像素 {high_alpha.get("pixels", 0)}，'
                f'触画布边缘 {bool(high_alpha.get("touches_canvas_border"))}，'
                f'原生裁片外 {high_alpha.get("outside_native_crop_pixels", 0)}；'
                f'提示 {html.escape(str(high_alpha.get("review_hint", "")))}。'
                f'白底合成 SHA256 {white_sha}，深底合成 SHA256 {dark_sha}，草地色合成 SHA256 {grass_sha}。'
                '这些字段仅供 alpha-aware 人工复核，不构成自动美术通过。</p>'
            )
        slots_html = "".join(slots)
        cards.append(f'<article><h2>{title} · {html.escape(item["id"])}</h2><p>官方槽：{key_text}；原槽像素：{item["targets"]["crop_1x"]}，画布：{item["targets"]["canvas_1x"]}，原点：{item["targets"]["origin_1x"]}。</p><p>common K={k_text}；源容量：{capacity}。{note}</p>{region_html}{evidence_html}<div class="strip">{slots_html}</div></article>')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>H3 原生尺寸批量审图</title><style>body{background:#17212d;color:#edf3fa;font:15px system-ui;margin:20px}article{background:#222f3d;border:1px solid #405367;border-radius:8px;padding:16px;margin:18px 0}.strip{display:flex;align-items:flex-start;gap:18px;overflow:auto;padding:8px 0 16px}figure{flex:none;margin:0;padding:8px;background:repeating-conic-gradient(#536170 0% 25%,#3d4a58 0% 50%) 0/16px 16px}figcaption{position:sticky;left:0;background:#17212d;padding:5px;white-space:nowrap}img{display:block;max-width:none;height:auto;image-rendering:auto}a{color:#9fcfff}</style>'
            '<h1>H3 原生尺寸批量审图</h1><p>3X文件可按原生尺寸或3X像素查看；切换只改变浏览器显示，不生成或冒充独立1X文件。数值只作观察线索，素材和游戏仍须独立验收。</p>'
            '<label>3X显示 <select id="review-display-scale"><option value="1">原生尺寸</option><option value="3">3X像素</option></select></label>'
             '<label> 审图背景 <select id="review-background"><option value="checker">棋盘</option><option value="dark">深色</option><option value="white">白色</option><option value="grass">草地色</option></select></label>'
             '<p>背景切换仅影响显示，图像文件保持原样。</p>'
             + "".join(cards) + '<script>document.getElementById("review-display-scale").onchange=function(){const s=Number(this.value);document.querySelectorAll("[data-native-width]").forEach(im=>{const w=Number(im.dataset.nativeWidth)*s,h=Number(im.dataset.nativeHeight)*s;if(im.tagName==="IMG"){im.width=w;im.height=h;}else{im.style.width=w+"px";im.style.height=h+"px";}if(im.dataset.nativeLeft!==undefined){im.style.left=Number(im.dataset.nativeLeft)*s+"px";im.style.top=Number(im.dataset.nativeTop)*s+"px";}});};document.getElementById("review-background").onchange=function(){const colors={checker:"repeating-conic-gradient(#536170 0% 25%,#3d4a58 0% 50%) 0/16px 16px",dark:"#000",white:"#fff",grass:"#4a5e3e"};document.querySelectorAll(".strip figure").forEach(stage=>{stage.style.background=colors[this.value];});};</script></html>').encode("utf-8")
    _write_new(outdir / "index.html", page, output_guard)


def _renderer_output_file(record: dict[str, Any], label: str, native_outdir: Path,
                          output_guard: OutputRootGuard) -> Path:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise ReviewError(f"NATIVE_RENDERER_OUTPUT_PATH_INVALID: {label}")
    path = _absolute_lexical_path(Path(record["path"]), label)
    if not _lexically_within(path, native_outdir):
        raise ReviewError("NATIVE_RENDERER_OUTPUT_ESCAPED_ROOT")
    output_guard.verify_target(path, must_exist=True)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReviewError(f"NATIVE_RENDERER_OUTPUT_MISSING: {label}") from error
    if _path_key(resolved) != _path_key(path):
        raise ReviewError("NATIVE_RENDERER_OUTPUT_PATH_CHANGED")
    return path


def _run_batch_after_preflight(manifest_path: Path, manifest_sha: str, renderer_path: Path,
                               renderer_sha256: str, outdir: Path, manifest: dict[str, Any],
                               canonical_inventory: Path, inventory_sha: str, metadata: list[dict[str, Any]],
                               spec_raw: bytes, spec_sha: str, build_binding: dict[str, Any],
                               capabilities: dict[str, Any], output_guard: OutputRootGuard,
                               started_at: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=".native-review-spec-", dir=OUTPUT_BASE) as temporary:
        def verify_foreground_transaction(phase, spec_path):
            if not any("foreground_selection" in row for row in metadata):
                return
            _verify_foreground_inputs(metadata, phase)
            for value, digest, label in ((manifest_path, manifest_sha, "manifest"),
                                         (canonical_inventory, inventory_sha, "inventory"),
                                         (renderer_path, renderer_sha256, "renderer"),
                                         (spec_path, spec_sha, "spec"),
                                         (build_binding.get("path"), build_binding["sha256"], "build receipt")):
                path = absolute_file(str(value) if value is not None else None, "Town " + label + " " + phase)
                file_sha_matches(path, digest, "Town " + label + " " + phase)
            if build_binding.get("test_receipt"):
                bound_tests = build_binding["test_receipt"]
                path = absolute_file(bound_tests["path"], "Town frozen tests " + phase)
                file_sha_matches(path, bound_tests["sha256"], "Town frozen tests " + phase)

        _verify_native_layer_inputs(_native_layer_inputs(metadata))
        for bound in metadata:
            for ref in bound.get("authored_inputs", []):
                file_sha_matches(Path(ref["path"]), ref["sha256"], "authored input before render")
        spec_path = Path(temporary) / "render-spec.json"
        with spec_path.open("xb") as stream:
            stream.write(spec_raw)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_visual_references(manifest["items"], "before render")
        verify_foreground_transaction("before render", spec_path)
        native_outdir = output_guard.root / "rendered"
        renderer_started_at = time.perf_counter()
        try:
            process = subprocess.run([str(renderer_path), "review-render", "--spec", str(spec_path),
                                      "--spec-sha256", spec_sha, "--outdir", str(native_outdir)],
                                     shell=False, check=False, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=RENDER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise ReviewError("NATIVE_RENDERER_TIMEOUT: no retry; inspect the requested output root for partial files") from error
        renderer_finished_at = time.perf_counter()
        if process.returncode != 0:
            raise ReviewError(f"NATIVE_RENDERER_FAILED({process.returncode}): {process.stderr[-4000:]}")
        output_guard.hold_directory(native_outdir)
        native_receipt = parse_json(process.stdout.encode("utf-8"), "native renderer stdout")
        if native_receipt.get("status") != "REVIEW_REQUIRED" or native_receipt.get("spec", {}).get("sha256") != spec_sha:
            raise ReviewError("NATIVE_RENDERER_RECEIPT_MISMATCH")
        if native_receipt.get("official_geometry_verified_by_renderer") is not False:
            raise ReviewError("RENDERER_MUST_NOT_CLAIM_INDEPENDENT_INVENTORY_VERIFICATION")
        if native_receipt.get("official_inventory") != {"path": str(canonical_inventory), "sha256": inventory_sha}:
            raise ReviewError("NATIVE_RENDERER_INVENTORY_BINDING_MISMATCH")
        receipt_path = output_guard.verify_target(native_outdir / "render-receipt.json", must_exist=True)
        receipt_raw = read_bounded(receipt_path, MAX_MANIFEST_BYTES, "native render receipt")
        if parse_json(receipt_raw, "native render receipt file") != native_receipt:
            raise ReviewError("NATIVE_RENDERER_RECEIPT_FILE_MISMATCH")

        expected_by_key = {tuple(row["key"]): row for row in metadata}
        native_rows = native_receipt.get("items")
        if not isinstance(native_rows, list) or len(native_rows) != len(metadata):
            raise ReviewError("NATIVE_RENDERER_ITEM_COUNT_MISMATCH")
        enriched = []
        for native_row in native_rows:
            key = tuple(native_row.get("key", []))
            if key not in expected_by_key:
                raise ReviewError("NATIVE_RENDERER_INVENTED_ORIGINAL_KEY")
            bound = expected_by_key[key]
            if native_row.get("targets") != bound["targets"] or native_row.get("geometry_binding_sha256") != bound["geometry_binding_sha256"]:
                raise ReviewError("NATIVE_RENDERER_GEOMETRY_DIFFERS_FROM_INVENTORY")
            if native_row.get("source_sha256") != bound["source_sha256"]:
                raise ReviewError("NATIVE_RENDERER_SOURCE_SHA_DIFFERS_FROM_MANIFEST")
            if bound.get("atlas_view") and (native_row.get("atlas_view") != bound["atlas_view"]
                    or native_row.get("alpha_cap_3x") != bound.get("alpha_cap_3x")
                    or native_row.get("common_k") is not None):
                raise ReviewError("NATIVE_RENDERER_AUTHORED_VIEW_MISMATCH")
            if bound.get("state_view") and (native_row.get("state_view") != bound["state_view"]
                    or native_row.get("common_k") is not None):
                raise ReviewError("NATIVE_RENDERER_AUTHORED_STATE_VIEW_MISMATCH")
            if (native_row.get("source_layout") != bound["source_layout"]
                    or native_row.get("source_view_proof") != bound["source_view_proof"]
                    or native_row.get("rendered_tiers") != bound["render_scales"]):
                raise ReviewError("NATIVE_RENDERER_SOURCE_LAYOUT_OR_VIEW_PROOF_MISMATCH")
            _verify_foreground_proof(native_row, bound)
            expected_reference_provenance = "caller-bound comparison" if bound.get("reference") else None
            if native_row.get("reference_provenance") != expected_reference_provenance:
                raise ReviewError("NATIVE_RENDERER_REFERENCE_PROVENANCE_MISMATCH")
            expected_pipeline = (TOWN_FOREGROUND_PIPELINE if "foreground_selection" in bound
                                 else "author-bound state pixel copy -> H3Quality explicit axis normalization directly to3X -> native placement"
                                 if bound.get("state_view") else "whole source -> H3Quality authored 3X atlas -> integer state pixel copy -> native placement; no state resampling"
                                 if bound.get("atlas_view") else "whole native canvas -> H3Quality single sampling -> exact RGBA8 native crop; zero positive alpha excluded"
                                 if bound["source_layout"] == "native-canvas" and bound.get("output_layout") == "native-crop"
                                 else "whole native canvas -> H3Quality direct sampling; native crop origin is not applied again"
                                 if bound["source_layout"] == "native-canvas"
                                 else "source crop -> alpha-zero integer canvas pixel-copy -> H3Quality direct sampling from the same registered input for each tier"
                                 if bound["registration"] == "integer-transparent-padding"
                                 else "source crop -> full viewport axis normalization -> H3Quality direct sampling for 1X review"
                                 if bound["registration"] == "full-viewport-axis-normalization"
                                 else "whole source -> full viewport axis normalization -> H3Quality direct sampling for 3X review"
                                 if bound["registration"] == "full-viewport-axis-normalization-3x"
                                 else "source crop -> H3Quality direct sampling from the same source input for each tier")
            if native_row.get("render_input_pipeline") != expected_pipeline:
                raise ReviewError("NATIVE_RENDERER_SAMPLING_PIPELINE_MISMATCH")
            outputs: dict[str, Any] = {}
            output_layout = bound.get("output_layout", "native-canvas")
            if native_row.get("output_layout", "native-canvas") != output_layout:
                raise ReviewError("NATIVE_RENDERER_OUTPUT_LAYOUT_MISMATCH")
            if "padding_registration" in bound:
                padding = bound["padding_registration"]
                sampling_base = bound["targets"]["canvas_1x" if bound["source_layout"] == "native-canvas" else "crop_1x"]
                if (native_row.get("padding_registration") != padding
                        or native_row.get("registration_offset") != padding["offset"]
                        or native_row.get("common_k") != padding["common_k"]
                        or native_row.get("registered_pixels") != [n * padding["common_k"] for n in sampling_base]):
                    raise ReviewError("NATIVE_RENDERER_PADDING_REGISTRATION_MISMATCH")
            native_outputs = native_row.get("outputs", {})
            if bound["render_scales"] == [3] and set(native_outputs) != {"candidate_3x"}:
                raise ReviewError("NATIVE_RENDERER_3X_ONLY_OUTPUTS_MISMATCH")
            for key_name in ("reference_1x", "candidate_1x", "candidate_3x", "registration_padding_diagnostic"):
                record = native_outputs.get(key_name)
                if not record:
                    continue
                image_path = _renderer_output_file(record, key_name, native_outdir, output_guard)
                image_raw = read_bounded(image_path, 512 * 1024 * 1024, key_name)
                image_sha = sha256_bytes(image_raw)
                if image_sha != record["png_sha256"]:
                    raise ReviewError("NATIVE_RENDERER_OUTPUT_SHA_MISMATCH")
                dimensions = png_size_bytes(image_raw, key_name)
                if dimensions != record["pixels"]:
                    raise ReviewError("NATIVE_RENDERER_OUTPUT_GEOMETRY_MISMATCH")
                layout = record.get("layout", "native-canvas")
                if key_name == "candidate_3x" and layout != output_layout:
                    raise ReviewError("NATIVE_RENDERER_OUTPUT_LAYOUT_MISMATCH")
                if layout == "native-crop" and dimensions != [n * 3 for n in bound["targets"]["crop_1x"]]:
                    raise ReviewError("CROP_OUTPUT_GEOMETRY_MISMATCH")
                outputs[key_name] = {**record, "path": str(image_path), "png_sha256": image_sha,
                                     "diagnostics": _metrics(image_raw, 1 if key_name != "candidate_3x" else 3,
                                                             bound["targets"], layout)}
                if key_name == "candidate_3x" and bound["source_layout"] == "native-canvas" and output_layout == "native-crop":
                    _verify_canvas_crop_proof(record.get("canvas_crop_proof"), bound["targets"],
                                              outputs[key_name]["diagnostics"])
                elif "canvas_crop_proof" in record:
                    raise ReviewError("NATIVE_RENDERER_CANVAS_CROP_PROOF_MISMATCH")
            if (bool(bound.get("reference")) and 1 in bound["render_scales"]) != ("reference_1x" in outputs):
                raise ReviewError("NATIVE_RENDERER_REFERENCE_OUTPUT_PRESENCE_MISMATCH")
            if (1 in bound["render_scales"]) != ("candidate_1x" in outputs):
                raise ReviewError("NATIVE_RENDERER_1X_OUTPUT_MISSING")
            if (bound["source_capacity_3x"].startswith("UNDER_3X") or bound["source_capacity_3x"] == "FULL_VIEWPORT_AXIS_NORMALIZATION_1X_DIAGNOSTIC_ONLY") and "candidate_3x" in outputs:
                raise ReviewError("UNDER_3X_SOURCE_MUST_NOT_PRODUCE_3X")
            if (3 in bound["render_scales"]) != ("candidate_3x" in outputs):
                raise ReviewError("3X_CAPABLE_UPPER_BOUND_EXPECTED_RENDER")
            comparisons = None
            if "reference_1x" in outputs and "candidate_1x" in outputs:
                reference_path = _renderer_output_file(outputs["reference_1x"], "reference output", native_outdir,
                                                       output_guard)
                candidate_path = _renderer_output_file(outputs["candidate_1x"], "candidate 1X output", native_outdir,
                                                       output_guard)
                reference_raw = read_bounded(reference_path, 512 * 1024 * 1024, "reference output")
                candidate_raw = read_bounded(candidate_path, 512 * 1024 * 1024, "candidate 1X output")
                if sha256_bytes(reference_raw) != outputs["reference_1x"]["png_sha256"] or sha256_bytes(candidate_raw) != outputs["candidate_1x"]["png_sha256"]:
                    raise ReviewError("RENDERED_OUTPUT_CHANGED_DURING_DIAGNOSTICS")
                comparisons = _comparison(reference_raw, candidate_raw)
            enriched_item = {**bound, "registration_offset": native_row.get("registration_offset"),
                             "registered_pixels": native_row.get("registered_pixels"),
                             "rendered_tiers": native_row.get("rendered_tiers"),
                             "outputs": outputs, "reference_comparison": comparisons,
                             "reference_provenance": "caller-bound comparison" if bound.get("reference") else None,
                             "metrics_role": "diagnostics_only_no_global_threshold"}
            enriched_item.pop("_foreground_selection_expected_proof", None)
            if "foreground_selection" in bound:
                enriched_item["foreground_selection_proof"] = native_row["foreground_selection_proof"]
            enriched.append(enriched_item)

        file_sha_matches(renderer_path, renderer_sha256, "renderer after render")
        if sha256_file(canonical_inventory) != inventory_sha or sha256_file(manifest_path) != manifest_sha:
            raise ReviewError("MANIFEST_OR_INVENTORY_CHANGED_DURING_RENDER")
        for item, row in zip(manifest["items"], metadata, strict=True):
            file_sha_matches(Path(item["source_png"]).resolve(strict=True), item["source_sha256"], "source after render")
            for ref in row.get("authored_inputs", []):
                file_sha_matches(Path(ref["path"]), ref["sha256"], "authored input after render")
            if item.get("reference"):
                file_sha_matches(Path(item["reference"]["path"]).resolve(strict=True), item["reference"]["sha256"], "reference after render")
        _verify_visual_references(manifest["items"], "after render")
        verify_foreground_transaction("after render", spec_path)
        output_guard.verify_current()
        _verify_native_layer_inputs(_native_layer_inputs(metadata))
        _write_new(outdir / "render-spec.json", spec_raw, output_guard)
        verified_at = time.perf_counter()
        execution_diagnostics = {
            "schema": "h3.native-review.execution-diagnostics.v1",
            "clock": "time.perf_counter",
            "measurement_scope": "run_batch entry through verified readback and render-spec evidence; excludes outer CLI startup and final report/HTML writes",
            "native_renderer_scope": "whole child process, including startup, decoding, rendering and receipt output; not isolated sampling time",
            "phase_wall_seconds": {"preflight": renderer_started_at - started_at,
                                   "native_renderer": renderer_finished_at - renderer_started_at,
                                   "readback": verified_at - renderer_finished_at},
            "elapsed_through_verified_readback_seconds": verified_at - started_at,
            "settlement": {"input_items": len(metadata), "verified_items": len(enriched),
                           "output_pngs": sum(len(row["outputs"]) for row in enriched),
                           "rendered_items_by_tier": {str(tier): sum(tier in row["rendered_tiers"] for row in enriched)
                                                      for tier in (1, 3)}},
            "settlement_scope": "successful native-review batch only; action aliases, HOLD and failed batches are not settled by this report",
        }
        report = {"schema": "h3.native-review.batch-report.v1",
                  "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "status": "REVIEW_REQUIRED", "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
                  "official_inventory": {"path": str(canonical_inventory), "sha256": inventory_sha,
                                         "geometry_verified_by_companion": True},
                  "renderer": {"path": str(renderer_path), "sha256": renderer_sha256,
                               "version": capabilities["version"], "build": build_binding,
                               "algorithm": native_receipt.get("algorithm")},
                  "render_spec": {"path": str(outdir / "render-spec.json"), "sha256": spec_sha},
                  "native_receipt": {"path": str(receipt_path), "sha256": sha256_bytes(receipt_raw)},
                  "items": enriched, "execution_diagnostics": execution_diagnostics,
                  "authority": {"art_approved": False, "human_approved": False,
                                "runtime_accepted": False, "game_deployed": False,
                                "automatic_art_score": False, "effective_pixels_verified": False}}
        _write_new(outdir / "review-report.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), output_guard)
        _write_html(outdir, enriched, enriched, output_guard)
        _verify_visual_references(manifest["items"], "after page")
        verify_foreground_transaction("after page", spec_path)
    return {"status": "REVIEW_REQUIRED", "items": len(enriched), "output_root": str(outdir),
            "report": str(outdir / "review-report.json"), "html": str(outdir / "index.html"),
            "official_geometry_verified_by_companion": True, "art_approved": False, "game_deployed": False}


def run_batch(manifest_path: Path, manifest_sha: str, renderer_path: Path, renderer_sha: str,
              outdir: Path, *, execution_state: dict[str, Any] | None = None) -> dict[str, Any]:
    started_at = time.perf_counter()
    manifest_path = absolute_file(str(manifest_path), "manifest")
    renderer_path = absolute_file(str(renderer_path), "renderer")
    outdir = _absolute_lexical_path(Path(outdir), "output root")
    base = _absolute_lexical_path(OUTPUT_BASE, "ignored output base")
    if _path_key(outdir.parent) != _path_key(base):
        raise ReviewError("OUTPUT_ROOT_MUST_BE_A_NEW_CHILD_OF_IGNORED_NATIVE_REVIEW_ROOT")
    reject_reparse_components(outdir, allow_missing_leaf=True)
    if _lexists(outdir):
        raise ReviewError("OUTPUT_ROOT_ALREADY_EXISTS")
    if not _lexically_within(outdir, base):
        raise ReviewError("OUTPUT_ROOT_OUTSIDE_IGNORED_NATIVE_REVIEW_ROOT")
    if len(manifest_sha) != 64 or any(char not in "0123456789abcdef" for char in manifest_sha):
        raise ReviewError("MANIFEST_SHA256_INVALID")
    raw_manifest = read_bounded(manifest_path, MAX_MANIFEST_BYTES, "batch manifest")
    if sha256_bytes(raw_manifest) != manifest_sha:
        raise ReviewError("MANIFEST_SHA256_MISMATCH_OR_LIMIT")
    manifest = parse_json(raw_manifest, "batch manifest")
    if manifest.get("schema") == "h3.art-town.background-native1x-diagnostic-decisions.v1":
        canonical_inventory = canonical_inventory_path()
        inventory_sha = sha256_file(canonical_inventory)
        adapted_items = []
        for item in manifest["items"]:
            key = item.get("public_native_key") or ["pcx", item["canonical_key"][1]]
            item_id = f"{key[0]}_{key[1]}"
            src = item["source"]
            ref = item.get("native_reference_from_request")
            adapted_item = {
                "id": item_id,
                "key": key,
                "source_png": src["path"],
                "source_sha256": src["sha256"],
                "source_rect": item.get("source_rect_xywh"),
                "registration": "full-viewport-axis-normalization",
                "render_scales": [1],
            }
            if ref and "path" in ref and "sha256" in ref:
                adapted_item["reference"] = {
                    "path": ref["path"],
                    "sha256": ref["sha256"],
                    "layout": "native-crop"
                }
            adapted_items.append(adapted_item)
        manifest = {
            "schema": "h3.native-review.batch-manifest.v1",
            "official_inventory": {"path": str(canonical_inventory), "sha256": inventory_sha},
            "items": adapted_items
        }
    else:
        exact_fields(manifest, {"schema", "official_inventory", "items"}, label="batch manifest")
        if manifest["schema"] != "h3.native-review.batch-manifest.v1":
            raise ReviewError("BATCH_MANIFEST_SCHEMA_UNSUPPORTED")
        exact_fields(manifest["official_inventory"], {"path", "sha256"}, label="official_inventory")
        canonical_inventory = canonical_inventory_path()
        supplied_inventory = absolute_file(manifest["official_inventory"]["path"], "official_inventory.path")
        if supplied_inventory != canonical_inventory:
            raise ReviewError("OFFICIAL_INVENTORY_PATH_NOT_CANONICAL")
        inventory_sha = manifest["official_inventory"]["sha256"]
        if not isinstance(inventory_sha, str) or len(inventory_sha) != 64:
            raise ReviewError("OFFICIAL_INVENTORY_SHA256_REQUIRED")
    rows, raw_inventory = load_inventory(canonical_inventory, inventory_sha)
    if not isinstance(manifest["items"], list) or not 1 <= len(manifest["items"]) <= MAX_ITEMS:
        raise ReviewError("BATCH_ITEM_COUNT_RANGE")
    ids: set[str] = set()
    keys: set[tuple[Any, ...]] = set()
    spec_items, metadata = [], []
    native_context = {}
    input_paths: list[Path] = [manifest_path, canonical_inventory]
    for item in manifest["items"]:
        spec_item, row = _resolve_entry(item, rows, canonical_inventory, inventory_sha, native_context=native_context)
        key = key_tuple(item["key"])
        if spec_item["id"] in ids or key in keys:
            raise ReviewError("DUPLICATE_ITEM_ID_OR_OFFICIAL_KEY")
        ids.add(spec_item["id"])
        keys.add(key)
        spec_items.append(spec_item)
        metadata.append(row)
        input_paths.append(Path(spec_item["source_png"]))
        input_paths.extend(Path(ref["path"]) for ref in row.get("authored_inputs", []))
        input_paths.extend(Path(ref["path"]) for ref in row.get("native_layer_inputs", []))
        input_paths.extend(Path(ref["path"]) for ref in row.get("foreground_inputs", []))
        if spec_item["reference"]:
            input_paths.append(Path(spec_item["reference"]["path"]))
        if "visual_reference" in row:
            input_paths.append(Path(row["visual_reference"]["path"]))
    town_mask_pixels = sum(row["source_pixels"][0] * row["source_pixels"][1]
                           for row in spec_items if "foreground_selection" in row)
    if town_mask_pixels > TOWN_FOREGROUND_BATCH_MASK_PIXELS:
        raise ReviewError("TOWN_FOREGROUND_BATCH_MASK_PIXEL_LIMIT")
    _validate_padding_groups(spec_items)
    for path in input_paths:
        if _overlap(path, outdir):
            raise ReviewError(f"INPUT_OUTPUT_OVERLAP: {path}")

    renderer_sha256 = renderer_sha
    file_sha_matches(renderer_path, renderer_sha256, "renderer")
    build_binding = _read_build_receipt(renderer_sha256, renderer_path)
    capabilities = _renderer_capabilities(renderer_path)
    if capabilities.get("version") is None:
        raise ReviewError("RENDERER_VERSION_MISSING")
    spec = {"schema": "h3.native-review.render-spec.v1",
            "source_manifest": {"path": str(manifest_path), "sha256": manifest_sha},
            "official_inventory": {"path": str(canonical_inventory), "sha256": inventory_sha},
            "items": spec_items}
    spec_raw = (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    spec_sha = sha256_bytes(spec_raw)

    def mark_created() -> None:
        if execution_state is not None:
            execution_state["output_root_created"] = True

    output_guard = OutputRootGuard.create(outdir, base, on_root_created=mark_created)
    try:
        return _run_batch_after_preflight(
            manifest_path, manifest_sha, renderer_path, renderer_sha256, outdir, manifest,
            canonical_inventory, inventory_sha, metadata, spec_raw, spec_sha, build_binding,
            capabilities, output_guard, started_at,
        )
    finally:
        output_guard.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--renderer", type=Path, required=True)
    parser.add_argument("--renderer-sha256", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    execution_state: dict[str, Any] = {"output_root_created": False}
    try:
        result = run_batch(args.manifest, args.manifest_sha256, args.renderer,
                           args.renderer_sha256, args.outdir, execution_state=execution_state)
    except Exception as error:
        failure = {"schema": "h3.native-review.error.v1", "status": "FAILED", "error": str(error)}
        if execution_state["output_root_created"]:
            output = Path(os.path.abspath(args.outdir))
            base = Path(os.path.abspath(OUTPUT_BASE))
            failure["partial_output_root"] = str(output)
            failure["partial_output_disposition"] = "inspect before removal; no cleanup is claimed"
            try:
                if _path_key(output.parent) != _path_key(base):
                    raise ReviewError("OUTPUT_ROOT_PATH_BINDING_INVALID")
                reject_reparse_components(output, allow_missing_leaf=False)
                if output.is_dir():
                    failure["partial_output_state"] = "nonempty" if next(output.iterdir(), None) is not None else "empty"
                else:
                    failure["partial_output_state"] = "not-a-directory"
            except (OSError, ReviewError):
                failure["partial_output_state"] = "path changed or reparse; do not follow"
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
