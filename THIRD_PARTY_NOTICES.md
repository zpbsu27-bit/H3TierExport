# Third-Party Notices

This repository contains or adapts third-party components. Each component
remains under its own license. The MIT license in `LICENSE` applies to the
original H3TierExport code only.

| Component | Location | License |
| --- | --- | --- |
| nlohmann/json 3.11.3 | `third_party/json.hpp`, `third_party/LICENSE.MIT` | MIT |
| yzh119/h3-art-pipeline @ `726856e58e1f219a897823c24616f158f016a103` | `providers/h3-art-pipeline/upstream` | GPL-2.0-or-later |
| Optional Python runtime dependencies (`numpy`, `Pillow`, `onnxruntime`) | installed by the user, not vendored | upstream licenses |
| Blender, RIFE, VCMI and other external tools | installed by the user, not vendored | upstream licenses |

## GPL provider boundary

`providers/h3-art-pipeline/upstream` is a byte-for-byte snapshot of the pinned
GPL-2.0-or-later upstream tree. Its `LICENSE` and `source-lock.json` are
preserved. H3TierExport executes it as a separate subprocess; it is not linked
into the MIT core. If you redistribute this directory, comply with
GPL-2.0-or-later, including source and notice requirements.

## Game assets

No Heroes of Might and Magic III game assets, LOD/DEF files, official HD
artwork, models, generated art, or runtime binaries are included. Users must
obtain the game and assets legally. "Heroes of Might and Magic III" and related
marks belong to their respective owners; this project is not affiliated with or
endorsed by Ubisoft or New World Computing.
