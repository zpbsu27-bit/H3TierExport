# Campaign 3X package comparison and VCMI delivery

The artwork pipeline lives in this H3TierExport software directory and does not import the H3 source tree. The current VCMI publisher is `scripts/campaign_vcmi_mod.py`; it creates a native VCMI Graphical mod using `content/Data3x` and `content/Sprites3x` and installs it below the configured VCMI `Mods` root.

`scripts/campaign_art_release.py` is retained only to reproduce the earlier H3PNGA1 size comparison. It has **no install command**. That format is not a VCMI MOD and must not be copied into HoMM 3 Complete `H3Mods`.

The final campaign manifest contains 1,739 resource keys (1,714 PCX and 25 DEF frame keys), backed by 1,736 distinct PNG files. Three CAMPSTAR frame aliases reuse existing image files.

## Build the VCMI MOD

```powershell
python -B "{H3TierExport}\scripts\campaign_vcmi_mod.py" build --config "{H3TierExport}\build\experiments\campaign-art-mod-pack\CAMPAIGN-ART-IMAGES3X-PACK-20261112-R02\vcmi-campaign-mod-operation\vcmi-campaign-mod-config.json"
```

The candidate is built under the configured operation directory. Every source SHA and exact 3X geometry is checked; each staged PNG and JSON descriptor is read back before install.

VCMI configs may set `mod_name` and `mod_description` for a dedicated graphical asset mod. If omitted, the publisher keeps its H3HD Campaign Art defaults.

For every DEF sprite, the build also reads the official 1999 native resource inventory and requires an RGBA PNG at the full logical canvas ×3. The receipt records canvas, crop, origin, and the expected 3X crop rectangle; alpha outside that rectangle is rejected. Crop-only dimensions such as 96×174 for a 96×64 canvas are refused before the candidate directory is created. A mounted hero is recognized from its official five static plus five eight-frame group layout and must include all 45 slots; partial sets are refused.

## Install

```powershell
python -B "{H3TierExport}\scripts\campaign_vcmi_mod.py" install --config "{H3TierExport}\build\experiments\campaign-art-mod-pack\CAMPAIGN-ART-IMAGES3X-PACK-20261112-R02\vcmi-campaign-mod-operation\vcmi-campaign-mod-config.json"
```

Install creates a new mod folder under `{VX-root}\VCMI-branch-develop\Mods` and refuses to overwrite an existing mod. By default it also refuses to run while VCMI is active. For an explicitly user-authorized hot installation of a new, absent mod directory, config may set `allow_hot_install: true` and a non-empty `hot_install_authorization`; the receipt records the observed VCMI processes. This only installs files atomically and does not claim the running client reloaded or displayed the new mod. The installed folder contains the `mod.json`, `content/Data3x`, and `content/Sprites3x` structures used by the existing VCMI H3HD mods.

## Update an existing VCMI MOD

```powershell
python -B "{H3TierExport}\scripts\campaign_vcmi_mod.py" update --config "<frozen-update-config.json>"
```

The update config pins the currently installed MOD inventory and its SHA-256, a same-volume backup path outside `Mods`, and new journal and receipt paths. The updater rechecks the installed tree and candidate immediately before replacing directories, moves the old MOD intact to the backup, installs the candidate by same-volume directory rename, then reads back both trees. The default remains idle-gated. To authorize an update while VCMI is running, the frozen config must set `allow_hot_update: true` and provide a non-empty `hot_update_authorization`. This permits `VCMI_client.exe` only; a running launcher or server still blocks the operation. If Windows refuses a directory rename because files are in use, the updater rolls back and records the outcome in the journal.

Hot update means the MOD files were replaced while the client process remained open. It does not prove that the running session reloaded or displayed changed images; `current_session_reload_verified` and `runtime_accepted` remain false until those facts are observed in VCMI.

A successful install and SHA readback confirms the local VCMI mod files. VCMI launch and visual acceptance remain separate checks.
