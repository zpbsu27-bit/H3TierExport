"""H3 root selection for installed and portable H3TierExport use."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import sys

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/"scripts"))
import workspace


class WorkspaceContract(unittest.TestCase):
    def test_pinned_workspace_remains_default(self):
        with tempfile.TemporaryDirectory(prefix="workspace-default-") as directory:
            root=Path(directory);(root/"CompleteOfficialRelease-20261006-v1").mkdir()
            config=root/"workspace.json"
            config.write_text(json.dumps({"schema":"h3.tier-export.workspace.v1","h3_root":str(root)}),encoding="utf-8")
            with patch.dict(os.environ,{},clear=False):
                os.environ.pop("H3_TIER_EXPORT_H3_ROOT",None)
                with patch.object(workspace,"source_path",return_value=config):
                    self.assertEqual(workspace.h3_root(),root.resolve())

    def test_explicit_environment_root_supports_portable_release(self):
        with tempfile.TemporaryDirectory(prefix="workspace-portable-") as directory:
            root=Path(directory);(root/"CompleteOfficialRelease-20261006-v1").mkdir()
            with patch.dict(os.environ,{"H3_TIER_EXPORT_H3_ROOT":str(root)}):
                self.assertEqual(workspace.h3_root(),root.resolve())

    def test_flat_codex_material_root_is_valid_and_used_directly(self):
        with tempfile.TemporaryDirectory(prefix="workspace-flat-material-") as directory:
            root=Path(directory)
            (root/"codex-map").mkdir()
            (root/"codex-map"/"native-resource-inventory.json.gz").write_bytes(b"fixture")
            (root/"codex-高清母版").mkdir()
            (root/"codex-3X").mkdir()
            with patch.dict(os.environ,{"H3_TIER_EXPORT_H3_ROOT":str(root)}):
                self.assertEqual(workspace.h3_root(),root.resolve())
            self.assertEqual(workspace.asset_root(root),root)

    def test_relative_override_and_missing_config_fail_clearly(self):
        with patch.dict(os.environ,{"H3_TIER_EXPORT_H3_ROOT":"relative/path"}):
            with self.assertRaisesRegex(ValueError,"absolute path"):
                workspace.h3_root()
        with tempfile.TemporaryDirectory(prefix="workspace-missing-") as directory:
            with patch.dict(os.environ,{"H3_TIER_EXPORT_H3_ROOT":directory}):
                with self.assertRaisesRegex(FileNotFoundError,"Bound H3 workspace missing"):
                    workspace.h3_root()


if __name__=="__main__":unittest.main()
