# yzh119 H3 art-pipeline provider

This provider pins `https://github.com/yzh119/h3-art-pipeline` at commit
`726856e58e1f219a897823c24616f158f016a103`. The copied upstream tree under
`upstream/` is kept byte-for-byte, including its `LICENSE` and attribution. Its
license is GPL-2.0-or-later; this code is not merged into the H3TierExport MIT
core. `source-lock.json` records the SHA-256 and byte length of every upstream
file. The adapter refuses to show or run the provider if a file is missing,
changed, added, or replaced by a link.

## CLI

From the H3TierExport directory:

```powershell
python -B scripts/art_pipeline_provider.py catalog --json
python -B scripts/art_pipeline_provider.py run creature-art.def_extract.info -- --help
```

For a real output-producing call, supply `--work-root` before the `--` that
separates adapter options from upstream options. Output paths passed to the
upstream command must be absolute, new paths below that work root, and the work
root must be below `build/experiments/art-pipeline`. For example:

```powershell
python -B scripts/art_pipeline_provider.py run creature-art.def_extract.export `
  --work-root {H3TierExport}\build\experiments\art-pipeline\def-export-01 `
  -- {H3-game}\Data\H3sprite.lod CREATURE.DEF --out `
  {H3TierExport}\build\experiments\art-pipeline\def-export-01\reference
```

Use `--python` for a selected Python executable and `--blender` for Blender
commands. The adapter uses argument arrays with `shell=False`; it does not
interpret a shell command string. Python bytecode writes are disabled while
running the locked source tree.

The catalogue scans actual upstream Python entrypoints and their declared
argument names. `run` is limited to commands with reviewed read/write behavior.
Other entrypoints remain discoverable with `requires_adapter` and a specific
reason. API-backed FLUX/Meshy commands are visible as `disabled_network`; this
local adapter never uploads source images or calls generation APIs.

`render_sprites` is an upstream Blender pose renderer. It renders the requested
configured group/profile from a rigged model; it does not turn one still image
into a complete, validated set of every H3 action for arbitrary creatures.
Scaffold, render, validation and in-game review remain separate steps.

The catalogue is an adapter inventory, not an art-quality or game-runtime
approval. Original H3 data and derived art stay in the user's local workspace.
