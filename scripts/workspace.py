"""Read the explicit H3 art asset workspace; the tool directory is independent."""
import json
import os
from pathlib import Path
from companion_runtime import source_path
from tool_paths import resolve_tool_root

PRODUCT = resolve_tool_root(__file__)


def _is_valid_workspace(root: Path) -> bool:
    return (
        (root / 'CompleteOfficialRelease-20261006-v1').is_dir()
        or (root / 'CompleteNativeBaseline').is_dir()
        or (
            (root / 'codex-map' / 'native-resource-inventory.json.gz').is_file()
            and (root / 'codex-高清母版').is_dir()
            and (root / 'codex-3X').is_dir()
        )
    )


def h3_root() -> Path:
    override = os.environ.get('H3_TIER_EXPORT_H3_ROOT', '').strip()
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            raise ValueError('H3_TIER_EXPORT_H3_ROOT must be an absolute path')
    else:
        binding = json.loads(source_path('resources/H3TierExport-workspace.json', PRODUCT).read_bytes())
        root = Path(binding['h3_root'])
        if binding.get('schema') != 'h3.tier-export.workspace.v1' or not root.is_absolute():
            raise ValueError('Invalid H3TierExport workspace binding')
    if not _is_valid_workspace(root):
        raise FileNotFoundError('Bound H3 workspace missing: ' + str(root))
    return root.resolve()


def asset_root(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else h3_root()
    if (base / 'CompleteOfficialRelease-20261006-v1').exists() or (base / 'CompleteNativeBaseline').exists():
        return base
    if _is_valid_workspace(base):
        return base
    legacy = base / 'H3HD' / 'assets'
    if legacy.is_dir():
        return legacy
    return base
