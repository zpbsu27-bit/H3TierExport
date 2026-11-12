"""Freeze and verify qualified companion code; H3 facts remain owner inputs.

The generated payload is a release artifact, never an editable source checkout.
Its one-level location preserves the existing product/output-root calculation.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

from tool_paths import resolve_tool_root

PRODUCT = resolve_tool_root(__file__)
MANIFEST = "runtime-manifest.json"
SCHEMA_V1 = "h3.tier-export.companion-runtime.v1"
SCHEMA = "h3.tier-export.companion-runtime.v2"
BINDING_SCHEMA = "h3.tier-export.companion-runtime-binding.v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:py|json)\Z")
TOKEN = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
MAX_FILES = 512
MAX_BYTES = 32 * 1024 * 1024


def _need(value, code):
    if not value:
        raise RuntimeError(code)


def _bytes(path, limit=32 * 1024 * 1024):
    path = Path(path)
    info = _plain(path)
    _need(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "RUNTIME_FILE_LIMIT_OR_TYPE")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _need(len(raw) <= limit, "RUNTIME_FILE_LIMIT_OR_TYPE")
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _plain(path):
    info = Path(path).lstat()
    _need(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
          "RUNTIME_REPARSE_FORBIDDEN")
    return info


def _relative(value, code="RUNTIME_PATH_INVALID"):
    _need(isinstance(value, str) and value and not value.startswith("/")
          and "\\" not in value and str(PurePosixPath(value)) == value, code)
    for part in value.split("/"):
        _need(part not in ("", ".", "..") and part == part.rstrip(". ")
              and not any(ord(c) < 32 or c in '<>:"|?*' for c in part)
              and part.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3",
                  "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
                  "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}, code)
    return value


def _components(root, relative):
    path = Path(root)
    _need(stat.S_ISDIR(_plain(path).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    for part in _relative(relative).split("/"):
        path = path / part
        _plain(path)
    return path


def _mkdir_relative(root, relative):
    path = Path(root)
    _need(stat.S_ISDIR(_plain(path).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    for part in _relative(relative).split("/"):
        path = path / part
        if not os.path.lexists(path):
            path.mkdir()
        _need(stat.S_ISDIR(_plain(path).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    return path


def _path_namespace(paths):
    seen = {}
    for value in paths:
        path = PurePosixPath(value)
        for part, kind in [(p.as_posix(), "directory") for p in path.parents if p.as_posix() != "."] + [(value, "file")]:
            prior = seen.get(part.casefold())
            _need(prior is None or prior == (part, kind), "RUNTIME_FILE_NAME_INVALID")
            seen[part.casefold()] = (part, kind)


def _cpp_fields(value):
    fields, start, depth, quoted, escaped = [], 0, 0, False, False
    for offset, char in enumerate(value):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            _need(depth >= 0, "RUNTIME_REGISTRY_SYNTAX_INVALID")
        elif char == "," and depth == 0:
            fields.append(value[start:offset].strip())
            start = offset + 1
    _need(not quoted and depth == 0, "RUNTIME_REGISTRY_SYNTAX_INVALID")
    last = value[start:].strip()
    if last:
        fields.append(last)
    return fields


def _cpp_string(value):
    if value.startswith('L"'):
        value = value[1:]
    _need(value.startswith('"') and value.endswith('"'), "RUNTIME_REGISTRY_LITERAL_INVALID")
    result = json.loads(value)
    _need(isinstance(result, str), "RUNTIME_REGISTRY_LITERAL_INVALID")
    return result


def _cpp_list(value):
    _need(value.startswith("{") and value.endswith("}"), "RUNTIME_REGISTRY_LIST_INVALID")
    return [_cpp_string(v) for v in _cpp_fields(value[1:-1])]


def _registry_rows(header, typename, function, variable, fields):
    declaration = re.search(r"std::array<" + typename + r",\s*(\d+)>\s*&\s*" + function
                            + r"\(\)\s*\{", header)
    _need(declaration is not None, "RUNTIME_REGISTRY_DECLARATION_MISSING")
    body = re.search(r"\b" + variable + r"\s*=\s*\{\{(.*?)\}\};", header[declaration.end():], re.S)
    _need(body is not None, "RUNTIME_REGISTRY_INITIALIZER_MISSING")
    rows = []
    for value in _cpp_fields(body.group(1)):
        _need(value.startswith("{") and value.endswith("}"), "RUNTIME_REGISTRY_ROW_INVALID")
        row = _cpp_fields(value[1:-1])
        _need(len(row) == fields, "RUNTIME_REGISTRY_FIELD_COUNT_INVALID")
        rows.append(row)
    _need(len(rows) == int(declaration.group(1)), "RUNTIME_REGISTRY_ROW_COUNT_INVALID")
    return rows


def runtime_registry(product=PRODUCT):
    """Read only the literal declarations owned by the native command registry."""
    header = _bytes(_components(product, "src/companion_cli.hpp")).decode("utf-8")
    resources = {}
    for row in _registry_rows(header, "CompanionRuntimeResource", "companion_runtime_resources", "resources", 5):
        identifier, path, source, role = [_cpp_string(v) for v in row[:4]]
        _need(TOKEN.fullmatch(identifier) and identifier not in resources and row[4] in ("true", "false"),
              "RUNTIME_RESOURCE_REGISTRY_INVALID")
        resources[identifier] = {"path": _relative(path), "source_path": _relative(source),
                                 "role": role, "tree": row[4] == "true"}
    entries, entry_resources, all_commands, retired = {}, {}, {}, {}
    rows = _registry_rows(header, "CompanionCommand", "companion_commands", "commands", 9)
    for row in rows:
        name, script = _cpp_string(row[0]), _cpp_string(row[1])
        _cpp_list(row[2]); _cpp_string(row[3]); _cpp_string(row[4])
        ids, replacement = _cpp_list(row[6]), _cpp_string(row[8])
        _need(TOKEN.fullmatch(name) and NAME.fullmatch(script) and script.endswith(".py")
              and name not in all_commands and script.casefold() not in {v.casefold() for v in all_commands.values()}
              and row[5] in ("true", "false") and row[7] in ("true", "false")
              and len(ids) == len(set(ids)) and all(i in resources for i in ids), "RUNTIME_ENTRYPOINT_REGISTRY_INVALID")
        all_commands[name] = script
        if row[7] == "true":
            _need(row[5] == "false" and not ids and TOKEN.fullmatch(replacement), "RUNTIME_RETIRED_REGISTRY_INVALID")
            retired[name] = replacement
        else:
            _need(row[5] == "true" and not replacement, "RUNTIME_ACTIVE_ENTRYPOINT_NOT_FROZEN")
            entries[name], entry_resources[name] = script, ids
    _need(entries and all(v in entries for v in retired.values())
          and {i for ids in entry_resources.values() for i in ids} == set(resources), "RUNTIME_RESOURCE_REGISTRY_INVALID")
    return {"entrypoints": dict(sorted(entries.items())),
            "entrypoint_resources": dict(sorted(entry_resources.items())), "resources": dict(sorted(resources.items()))}


def runtime_entries(product=PRODUCT):
    return runtime_registry(product)["entrypoints"]


def _manifest_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def freeze_payload(sources, product=PRODUCT):
    product = Path(product)
    expected = _source_rows(sources)
    registry = runtime_registry(product)
    entries, resources = registry["entrypoints"], registry["resources"]
    available = {p.stem for p in (product / "scripts").glob("*.py")}
    pending = list(entries.values()) + [r["path"] for r in resources.values() if r["role"] == "python-worker"]
    modules = set()
    while pending:
        name = pending.pop()
        if name in modules:
            continue
        _need(NAME.fullmatch(name), "RUNTIME_MODULE_NAME_INVALID")
        modules.add(name)
        tree = ast.parse(_bytes(_components(product, "scripts/" + name)), filename=name)
        for node in ast.walk(tree):
            imports = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                       else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
            pending.extend(n + ".py" for n in imports if n in available and n + ".py" not in modules)
    mapping = {n: ("scripts/" + n, "python-module") for n in modules}
    mapping["H3TierExport-workspace.json"] = ("resources/H3TierExport-workspace.json", "configuration")
    for resource in resources.values():
        source_root = _components(product, resource["source_path"])
        if resource["tree"]:
            for relative in _tree_files(source_root, skip_bytecode=True):
                name = resource["path"] + "/" + relative
                _need(name not in mapping, "RUNTIME_RESOURCE_PATH_CONFLICT")
                mapping[name] = (resource["source_path"] + "/" + relative, resource["role"])
        else:
            name = resource["path"]
            _need(name not in entries.values(), "RUNTIME_RESOURCE_PATH_CONFLICT")
            _need(name not in mapping or mapping[name][0] == resource["source_path"], "RUNTIME_RESOURCE_PATH_CONFLICT")
            mapping[name] = (resource["source_path"], resource["role"])
    contents, rows, total = {}, [], 0
    for name, (source, role) in sorted(mapping.items()):
        raw = _bytes(_components(product, source))
        _need(source in expected and expected[source]["sha256"] == _sha(raw)
              and expected[source]["bytes"] == len(raw), "RUNTIME_BUILD_SOURCE_DRIFT: " + source)
        total += len(raw)
        _need(len(rows) < MAX_FILES and total <= MAX_BYTES, "RUNTIME_TOTAL_MISMATCH")
        contents[name] = raw
        rows.append({"path": name, "source_path": source, "role": role, "bytes": len(raw), "sha256": _sha(raw)})
    manifest = {"schema": SCHEMA, **registry, "files": rows}
    _manifest_rows(manifest)
    raw = _manifest_bytes(manifest)
    digest = _sha(raw)
    binding = {"schema": BINDING_SCHEMA, "directory": ".runtime-" + digest,
               "manifest_sha256": digest, "file_count": len(rows),
               "bytes": sum(r["bytes"] for r in rows)}
    target = product / binding["directory"]
    if target.exists():
        verify_payload(binding, product, sources=sources)
        prune_stale_runtimes(product=product, keep_directories={binding["directory"]})
        return binding
    stage = _mkdir_relative(product, "build/candidate/runtime-staging-" + digest)
    contents[MANIFEST] = raw
    existing = _tree_files(stage)
    _need(not (set(existing) - set(contents)), "RUNTIME_STAGE_EXTRA_FILE")
    for name, data in contents.items():
        path = stage / name
        if "/" in name:
            _mkdir_relative(stage, name.rsplit("/", 1)[0])
        if path.exists():
            _need(_bytes(path) == data, "RUNTIME_STAGE_CONFLICT")
        else:
            # Native runtime validation requires each payload to be an independent
            # regular file (nNumberOfLinks == 1). Never hard-link across source or
            # historical runtime trees, even when the content hash is identical.
            with path.open("xb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
    _exact_tree(stage, set(contents))
    for name, data in contents.items():
        _need(_bytes(stage / name) == data, "RUNTIME_STAGE_CONFLICT")
    os.rename(stage, target)
    verify_payload(binding, product, sources=sources)
    prune_stale_runtimes(product=product, keep_directories={binding["directory"]})
    return binding


def _runtime_reuse_index(product):
    """Index prior frozen manifests once so future builds can share identical files."""
    product = Path(product)
    index = {}
    for runtime in sorted(product.iterdir(), key=lambda path: path.name):
        if not runtime.name.startswith(".runtime-") or not runtime.is_dir():
            continue
        try:
            manifest = _execution_manifest(runtime)
            for row in manifest["files"]:
                index.setdefault((row["path"], row["sha256"]), []).append(
                    (runtime, row["bytes"]))
        except (OSError, RuntimeError, ValueError):
            # A stale or concurrently retired runtime is not a reuse source.
            continue
    return index


def _reusable_payload_file(product, relative, expected_bytes, index):
    """Find and re-hash an identical immutable file from the one-time index."""
    expected_sha = _sha(expected_bytes)
    for runtime, expected_size in index.get((relative, expected_sha), []):
        if expected_size != len(expected_bytes):
            continue
        try:
            candidate = _components(runtime, relative)
            info = _plain(candidate)
            if (stat.S_ISREG(info.st_mode) and info.st_size == expected_size
                    and _sha(_bytes(candidate)) == expected_sha):
                return candidate
        except (OSError, RuntimeError):
            continue
    return None


def _source_rows(sources):
    expected, seen = {}, set()
    for row in sources:
        _need(isinstance(row, dict) and isinstance(row.get("path"), str), "RUNTIME_SOURCE_ROWS_INVALID")
        name = _relative(row["path"].replace("\\", "/"), "RUNTIME_SOURCE_PATH_INVALID")
        _need(name.casefold() not in seen, "RUNTIME_SOURCE_PATH_DUPLICATE")
        seen.add(name.casefold()); expected[name] = row
    _path_namespace(expected)
    return expected


def _tree_files(root, skip_bytecode=False):
    root = Path(root)
    _need(stat.S_ISDIR(_plain(root).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    pending, result, seen = [root], [], set()
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if skip_bytecode and path.name == "__pycache__":
                continue
            info = _plain(path); relative = _relative(path.relative_to(root).as_posix())
            _need(relative.casefold() not in seen, "RUNTIME_FILE_NAME_INVALID")
            seen.add(relative.casefold())
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            else:
                _need(stat.S_ISREG(info.st_mode), "RUNTIME_FILE_LIMIT_OR_TYPE")
                result.append(relative)
                _need(len(result) <= MAX_FILES + 1, "RUNTIME_FILE_COUNT_INVALID")
    return result


def _exact_tree(root, files):
    expected_dirs = {p.as_posix() for name in files for p in PurePosixPath(name).parents if p.as_posix() != "."}
    pending, seen_files, seen_dirs = [Path(root)], set(), set()
    while pending:
        directory = pending.pop()
        _need(stat.S_ISDIR(_plain(directory).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
        for path in directory.iterdir():
            info = _plain(path); relative = _relative(path.relative_to(root).as_posix())
            if stat.S_ISDIR(info.st_mode):
                _need(relative in expected_dirs and relative not in seen_dirs, "RUNTIME_EXTRA_FILE")
                seen_dirs.add(relative); pending.append(path)
            else:
                _need(stat.S_ISREG(info.st_mode) and relative in files and relative not in seen_files, "RUNTIME_EXTRA_FILE")
                seen_files.add(relative)
    _need(seen_files == set(files) and seen_dirs == expected_dirs, "RUNTIME_EXTRA_FILE")


def _manifest_rows(manifest):
    _need(isinstance(manifest, dict) and manifest.get("schema") in (SCHEMA_V1, SCHEMA)
          and isinstance(manifest.get("entrypoints"), dict), "RUNTIME_MANIFEST_INVALID")
    v2 = manifest["schema"] == SCHEMA
    _need(set(manifest) == ({"schema", "entrypoints", "entrypoint_resources", "resources", "files"} if v2
                           else {"schema", "entrypoints", "files"}), "RUNTIME_MANIFEST_INVALID")
    rows, entries = manifest.get("files"), manifest["entrypoints"]
    _need(isinstance(rows, list) and 1 <= len(rows) <= (MAX_FILES if v2 else 64), "RUNTIME_FILE_COUNT_INVALID")
    _need(entries and all(isinstance(k, str) and TOKEN.fullmatch(k) and isinstance(v, str)
          and NAME.fullmatch(v) and v.endswith(".py") for k, v in entries.items())
          and len({v.casefold() for v in entries.values()}) == len(entries), "RUNTIME_ENTRYPOINT_MISSING")
    resources = manifest.get("resources", {})
    if v2:
        links = manifest.get("entrypoint_resources")
        _need(isinstance(resources, dict) and isinstance(links, dict) and set(links) == set(entries), "RUNTIME_RESOURCES_INVALID")
        _need(all(isinstance(k, str) and TOKEN.fullmatch(k) and isinstance(v, dict)
                  and set(v) == {"path", "source_path", "role", "tree"} for k, v in resources.items()), "RUNTIME_RESOURCES_INVALID")
        for resource in resources.values():
            _relative(resource["path"]); _relative(resource["source_path"])
            _need(type(resource["tree"]) is bool and ((resource["role"] == "python-worker" and not resource["tree"]
                  and NAME.fullmatch(resource["path"]) and resource["path"].endswith(".py")
                  and resource["source_path"] == "scripts/" + resource["path"])
                  or (resource["role"] == "reference-data" and not resource["tree"]
                  and resource["path"].startswith("scripts/data/") and resource["path"].endswith((".json", ".json.gz"))
                  and resource["source_path"] == resource["path"])
                  or (resource["role"] == "provider-source" and resource["tree"]
                  and resource["path"].startswith("providers/") and resource["source_path"] == resource["path"])),
                  "RUNTIME_RESOURCE_ROLE_INVALID")
        _need(all(isinstance(ids, list) and all(isinstance(i, str) and i in resources for i in ids)
                  and len(ids) == len(set(ids)) for ids in links.values())
              and {i for ids in links.values() for i in ids} == set(resources), "RUNTIME_ENTRYPOINT_RESOURCES_INVALID")
        paths = [v["path"].casefold() for v in resources.values()]
        _need(len(paths) == len(set(paths)) and not any(a.startswith(b + "/") for a in paths for b in paths if a != b),
              "RUNTIME_RESOURCE_PATH_CONFLICT")
    seen, sources, total = {}, set(), 0
    for row in rows:
        _need(isinstance(row, dict) and set(row) == ({"path", "source_path", "role", "bytes", "sha256"} if v2
              else {"path", "source_path", "bytes", "sha256"}), "RUNTIME_FILE_ROW_INVALID")
        name = _relative(row["path"], "RUNTIME_FILE_NAME_INVALID")
        source = _relative(row["source_path"], "RUNTIME_SOURCE_PATH_INVALID")
        _need(name != MANIFEST and name.casefold() not in {n.casefold() for n in seen}
              and source.casefold() not in sources, "RUNTIME_FILE_NAME_INVALID")
        if not v2:
            _need(NAME.fullmatch(name) and (source == "scripts/" + name or (name == "H3TierExport-workspace.json"
                  and source == "resources/H3TierExport-workspace.json")), "RUNTIME_SOURCE_PATH_INVALID")
        else:
            role = row["role"]
            owners = [r for r in resources.values() if (name.startswith(r["path"] + "/") if r["tree"] else name == r["path"])]
            if role in ("python-module", "configuration"):
                _need(not owners and NAME.fullmatch(name) and ((role == "python-module" and name.endswith(".py")
                      and source == "scripts/" + name) or (role == "configuration" and name == "H3TierExport-workspace.json"
                      and source == "resources/" + name)), "RUNTIME_SOURCE_PATH_INVALID")
            else:
                _need(len(owners) == 1 and role == owners[0]["role"] and source == owners[0]["source_path"]
                      + name[len(owners[0]["path"]):], "RUNTIME_RESOURCE_FILE_BINDING_INVALID")
        _need(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_BYTES and isinstance(row["sha256"], str)
              and SHA.fullmatch(row["sha256"]), "RUNTIME_FILE_ROW_INVALID")
        seen[name] = row; sources.add(source.casefold()); total += row["bytes"]
    _need(total <= MAX_BYTES, "RUNTIME_TOTAL_MISMATCH")
    _path_namespace(set(seen) | {MANIFEST})
    _path_namespace(row["source_path"] for row in rows)
    _need(all(name in seen and (not v2 or seen[name]["role"] == "python-module") for name in entries.values()),
          "RUNTIME_ENTRYPOINT_MISSING")
    if v2:
        _need("H3TierExport-workspace.json" in seen and seen["H3TierExport-workspace.json"]["role"] == "configuration",
              "RUNTIME_CONFIGURATION_MISSING")
        _need(all(any(n.startswith(r["path"] + "/") if r["tree"] else n == r["path"] for n in seen)
                  for r in resources.values()), "RUNTIME_RESOURCE_FILE_MISSING")
    return seen


def verify_payload(binding, product=PRODUCT, sources=None):
    _need(isinstance(binding, dict) and binding.get("schema") == BINDING_SCHEMA,
          "RUNTIME_BINDING_INVALID")
    digest = binding.get("manifest_sha256")
    _need(isinstance(digest, str) and SHA.fullmatch(digest)
          and binding.get("directory") == ".runtime-" + digest, "RUNTIME_BINDING_ID_INVALID")
    root = _components(product, binding["directory"])
    _need(stat.S_ISDIR(_plain(root).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    raw = _bytes(root / MANIFEST, 1024 * 1024)
    _need(_sha(raw) == digest, "RUNTIME_MANIFEST_DRIFT")
    manifest = json.loads(raw)
    rows = _manifest_rows(manifest)
    _exact_tree(root, set(rows) | {MANIFEST})
    total = 0
    expected = _source_rows(sources) if sources is not None else None
    for name, row in rows.items():
        source = row["source_path"]
        data = _bytes(root / name)
        _need(type(row.get("bytes")) is int and len(data) == row["bytes"]
              and _sha(data) == row.get("sha256"), "RUNTIME_FILE_DRIFT: " + name)
        if expected is not None:
            _need(source in expected and expected[source]["sha256"] == row["sha256"]
                  and expected[source]["bytes"] == row["bytes"], "RUNTIME_SOURCE_BINDING_MISMATCH")
        total += len(data)
    _need(type(binding.get("file_count")) is int and binding["file_count"] == len(rows)
          and type(binding.get("bytes")) is int and binding["bytes"] == total, "RUNTIME_TOTAL_MISMATCH")
    return manifest


def frozen_root():
    here = Path(__file__).resolve().parent
    return here if here.name.startswith(".runtime-") else None


def source_path(relative, product=None):
    """Return the physical code being executed, never today's substitute code."""
    root = frozen_root()
    relative = _relative(str(relative).replace("\\", "/"))
    if root is None:
        return Path(product or PRODUCT) / relative
    manifest = _execution_manifest(root)
    matches = [r for r in manifest["files"] if r["source_path"] == relative]
    _need(len(matches) == 1, "RUNTIME_SOURCE_NOT_PACKAGED: " + relative)
    return _components(root, matches[0]["path"])


def _execution_manifest(root):
    _need(stat.S_ISDIR(_plain(root).st_mode), "RUNTIME_DIRECTORY_MISSING_OR_REPARSE")
    raw = _bytes(root / MANIFEST, 1024 * 1024)
    _need(root.name == ".runtime-" + _sha(raw), "RUNTIME_MANIFEST_DRIFT")
    manifest = json.loads(raw)
    _manifest_rows(manifest)
    return manifest


def runtime_resource_path(resource_id, product=None):
    """Resolve one declared resource in the executing package, never a newer substitute."""
    _need(isinstance(resource_id, str) and TOKEN.fullmatch(resource_id), "RUNTIME_RESOURCE_ID_INVALID")
    root = frozen_root()
    if root is None:
        product = Path(product or PRODUCT)
        resources = runtime_registry(product)["resources"]
        _need(resource_id in resources, "RUNTIME_RESOURCE_UNDECLARED: " + resource_id)
        resource = resources[resource_id]
        path = _components(product, resource["source_path"])
    else:
        resources = _execution_manifest(root).get("resources", {})
        _need(resource_id in resources, "RUNTIME_RESOURCE_NOT_PACKAGED: " + resource_id)
        resource = resources[resource_id]
        path = _components(root, resource["path"])
    info = _plain(path)
    _need(stat.S_ISDIR(info.st_mode) if resource["tree"] else stat.S_ISREG(info.st_mode), "RUNTIME_RESOURCE_TYPE_INVALID")
    return path


def read_release(renderer_sha, renderer_path=None):
    """Resolve current or explicitly invoked archive; never search by hash."""
    runtime_root = frozen_root()
    product_root = runtime_root.parent if runtime_root is not None else PRODUCT
    dist_root = product_root if product_root.name.casefold() == "dist" else product_root / "dist"
    current = dist_root / "H3TierExport.exe"
    renderer = Path(renderer_path or current).resolve(strict=True)
    portable = runtime_root is not None and renderer.parent == runtime_root.parent
    archived = renderer.name == "H3TierExport.exe" and renderer.parent.parent == dist_root / "archive"
    _need(renderer == current or portable or archived, "RUNTIME_RENDERER_LOCATION_INVALID")
    directory = renderer.parent
    raw = _bytes(directory / "H3TierExport-release.json", 8 * 1024 * 1024)
    report_mirror = product_root / "reports/current/release.json"
    if renderer == current and report_mirror.is_file():
        _need(_bytes(report_mirror, 8 * 1024 * 1024) == raw,
              "RELEASE_REPORT_COPY_MISMATCH")
    release = json.loads(raw)
    _need(release.get("schema") == "h3.tier-export.release.v2"
          and release.get("exe_sha256") == renderer_sha and _sha(_bytes(renderer)) == renderer_sha,
          "RELEASE_EXE_IDENTITY_MISMATCH")
    build_path = directory / "H3TierExport-build-receipt.json"
    build_raw = _bytes(build_path)
    tests_path = directory / "H3TierExport-tests-receipt.json"
    tests_raw = _bytes(tests_path, 64 * 1024 * 1024)
    _need(_sha(build_raw) == release.get("build_receipt_sha256"), "RELEASE_FROZEN_BUILD_RECEIPT_DRIFT")
    _need(_sha(tests_raw) == release.get("test_receipt_sha256"), "RELEASE_FROZEN_TEST_RECEIPT_DRIFT")
    build, tests = json.loads(build_raw), json.loads(tests_raw)
    _need(build.get("schema") == "h3.tier-export.build.v2" and build.get("passed") is True
          and build.get("source_current") is True and build.get("executable", {}).get("sha256") == renderer_sha,
          "BUILD_RECEIPT_NOT_CURRENT")
    _need(tests.get("schema") == "h3.tier-export.test-suite.v2" and tests.get("passed") is True
          and tests.get("source_current") is True and tests.get("exe_sha256") == renderer_sha
          and tests.get("build_receipt_sha256") == _sha(build_raw), "RELEASE_TEST_BUILD_IDENTITY_MISMATCH")
    results = tests.get("results")
    _need(isinstance(results, list) and results and all(isinstance(r, dict)
          and type(r.get("exit_code")) is int and r["exit_code"] == 0 for r in results), "RELEASE_TEST_RESULTS_INVALID")
    files = release.get("files")
    _need(isinstance(files, list) and all(isinstance(r, dict) for r in files), "RELEASE_FILE_ROWS_INVALID")
    for leaf, digest in [("H3TierExport.exe", renderer_sha),
                         (build_path.name, _sha(build_raw)), (tests_path.name, _sha(tests_raw))]:
        allowed_paths = {leaf, str(directory / leaf), str(dist_root / leaf)}
        _need(sum(r.get("path") in allowed_paths and r.get("sha256") == digest
                  for r in files) == 1, "RELEASE_FILE_IDENTITY_MISMATCH")
    binding = build.get("companion_runtime")
    _need(binding == release.get("companion_runtime") and isinstance(binding, dict), "RUNTIME_RELEASE_BINDING_MISMATCH")
    if runtime_root is not None:
        _need(runtime_root == product_root / binding.get("directory", ""), "RUNTIME_MODULE_OUTSIDE_PAYLOAD")
    manifest = verify_payload(binding, product=product_root, sources=build.get("sources"))
    cap = subprocess.run([str(renderer), "capabilities"], capture_output=True, timeout=30, check=True)
    _need(json.loads(cap.stdout).get("companion_runtime_manifest_sha256") == binding["manifest_sha256"],
          "RUNTIME_COMPILED_BINDING_MISMATCH")
    return {"receipt": build, "raw": build_raw, "path": build_path,
            "tests": {"path": str(tests_path), "sha256": _sha(tests_raw)},
            "binding": binding, "manifest": manifest}


def active_runtime_directories(product=PRODUCT):
    """Find runtime directories that are legitimately referenced by dist releases or archives."""
    product = Path(product)
    active = set()

    # 1. Active current release in dist/
    release_path = product / "dist" / "H3TierExport-release.json"
    if release_path.exists():
        try:
            rel = json.loads(release_path.read_bytes())
            runtime_dir = rel.get("companion_runtime", {}).get("directory")
            if runtime_dir and (product / runtime_dir).is_dir():
                active.add(runtime_dir)
        except Exception:
            pass

    # 2. Historical archive releases in dist/archive/
    archive_dir = product / "dist" / "archive"
    if archive_dir.is_dir():
        for sub in archive_dir.iterdir():
            if sub.is_dir() and sub.name.startswith("release-"):
                sub_rel = sub / "H3TierExport-release.json"
                if sub_rel.exists():
                    try:
                        arch_rel = json.loads(sub_rel.read_bytes())
                        arch_rt = arch_rel.get("companion_runtime", {}).get("directory")
                        if arch_rt and (product / arch_rt).is_dir():
                            active.add(arch_rt)
                    except Exception:
                        pass
    return active


def prune_stale_runtimes(product=PRODUCT, keep_directories=None, keep_limit=None, batch=None):
    """Safely remove unreferenced and stale .runtime-* directories from the product root.

    Without keep_limit this keeps the original behaviour: only directories that no
    release binding references are retired. keep_limit=N additionally caps the total
    retained sidecars at N (the current release's own runtime first, then the most
    recently written referenced ones), because every archived release JSON keeps
    pointing at a live product-root directory and would otherwise pin one sidecar per
    release forever.
    """
    product = Path(product)
    active = active_runtime_directories(product)
    if keep_directories:
        active.update(keep_directories)

    present = [item for item in (product.iterdir() if product.is_dir() else [])
               if item.is_dir() and item.name.startswith(".runtime-") and not item.is_symlink()]

    current = None
    release_path = product / "dist" / "H3TierExport-release.json"
    if release_path.exists():
        try:
            current = (json.loads(release_path.read_bytes()).get("companion_runtime") or {}).get("directory")
        except Exception:
            current = None

    over_limit = []
    if keep_limit is not None:
        allowed = set()
        if current and (product / current).is_dir():
            allowed.add(current)
        rest = sorted((item for item in present if item.name in active and item.name not in allowed),
                      key=lambda item: item.stat().st_mtime, reverse=True)
        allowed.update(item.name for item in rest[:max(0, int(keep_limit) - len(allowed))])
        over_limit = [item for item in present if item.name in active and item.name not in allowed]

    removed = []
    detail = []
    kept = []
    for item in [p for p in present if p.name not in active] + over_limit:
        reason = ("unreferenced companion runtime" if item.name not in active
                  else "companion runtime above --keep limit")
        try:
            from retire_files import retire
            retire(item, product, reason, batch=batch if over_limit else None)
            if not item.exists():
                removed.append(item.name)
                detail.append({"directory": item.name, "reason": reason})
        except Exception:
            kept.append({"directory": item.name, "status": "RETIRE_FAILED_KEPT"})
    return {"pruned": removed, "pruned_detail": detail,
            "kept": sorted(item.name for item in present if item.name not in removed),
            "keep_limit": keep_limit, "current_release_runtime": current,
            "shared_batch": str(batch) if batch else None}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Companion runtime manager and pruner.")
    parser.add_argument("--prune", action="store_true", help="Prune unreferenced .runtime-* directories.")
    parser.add_argument("--list", action="store_true", help="List active and stale runtime directories.")
    parser.add_argument("--keep", type=int, default=None, metavar="N",
                        help="With --prune: also cap retained sidecars at N (current release first).")
    parser.add_argument("--batch-name", default=None,
                        help="Directory name under the review area that holds all receipts for one --keep sweep.")
    args = parser.parse_args(argv)

    if args.prune or not (args.prune or args.list):
        batch = None
        if args.keep is not None:
            import datetime as _dt
            name = args.batch_name or ("H3TierExport-runtime-keep-sweep-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
            batch = Path("{VX-root}/H3美术素材/待删除") / name
        print(json.dumps(prune_stale_runtimes(PRODUCT, keep_limit=args.keep, batch=batch), ensure_ascii=False, indent=2))
    elif args.list:
        active = active_runtime_directories(PRODUCT)
        all_runtimes = [p.name for p in PRODUCT.iterdir() if p.is_dir() and p.name.startswith(".runtime-")]
        stale = [name for name in all_runtimes if name not in active]
        print(json.dumps({"active": sorted(active), "stale": sorted(stale)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
