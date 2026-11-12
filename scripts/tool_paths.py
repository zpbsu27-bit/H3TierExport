"""Resolve the installed H3TierExport root from source or frozen companions."""
from __future__ import annotations

from pathlib import Path


def resolve_tool_root(module_file: str | Path) -> Path:
    path = Path(module_file).resolve()
    runtime = path.parent
    if runtime.name.startswith(".runtime-"):
        host = runtime.parent
        if (host.name.casefold() == "dist"
                and (host.parent / "src" / "main.cpp").is_file()
                and (host.parent / "scripts").is_dir()):
            return host.parent.resolve()
        return host.resolve()
    for parent in path.parents:
        if (parent.name.casefold() == "scripts"
                and (parent.parent / "src" / "main.cpp").is_file()
                and (parent.parent / "scripts").is_dir()):
            return parent.parent.resolve()
    for parent in path.parents:
        if (parent / "src" / "main.cpp").is_file() and (parent / "scripts").is_dir():
            return parent.resolve()
    if len(path.parents) >= 2:
        return path.parents[1]
    raise ValueError("Cannot resolve H3TierExport root from " + str(path))
