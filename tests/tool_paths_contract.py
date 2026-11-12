"""The source checkout and frozen portable runtime resolve the same product root."""
from pathlib import Path
import sys
import tempfile
import unittest

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
from tool_paths import resolve_tool_root


class ToolPathContract(unittest.TestCase):
    def marker(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    def test_source_script_resolves_h3tierexport_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tool-root-source-") as raw:
            root = Path(raw) / "H3TierExport"
            self.marker(root / "src" / "main.cpp")
            self.marker(root / "scripts" / "subject_scale_layout.py")
            self.assertEqual(resolve_tool_root(root / "scripts" / "subject_scale_layout.py"),
                             root.resolve())

    def test_frozen_runtime_under_product_dist_resolves_parent_product(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tool-root-dist-") as raw:
            product = Path(raw) / "H3TierExport"
            self.marker(product / "src" / "main.cpp")
            self.marker(product / "scripts" / "subject_scale_layout.py")
            dist = product / "dist"
            self.marker(dist / "H3TierExport.exe")
            frozen = dist / (".runtime-" + "a" * 64)
            self.marker(frozen / "subject_scale_layout.py")
            self.assertEqual(resolve_tool_root(frozen / "subject_scale_layout.py"),
                             product.resolve())

    def test_portable_dist_only_install_resolves_executable_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tool-root-portable-") as raw:
            portable = Path(raw) / "H3TierExport"
            self.marker(portable / "H3TierExport.exe")
            frozen = portable / (".runtime-" + "b" * 64)
            self.marker(frozen / "subject_scale_layout.py")
            self.assertEqual(resolve_tool_root(frozen / "subject_scale_layout.py"),
                             portable.resolve())

    def test_synthetic_frozen_fixture_without_exe_uses_its_isolated_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tool-root-frozen-fixture-") as raw:
            sandbox = Path(raw) / "TEST-runtime"
            frozen = sandbox / (".runtime-" + "c" * 64)
            self.marker(frozen / "subject_scale_layout.py")
            self.assertEqual(resolve_tool_root(frozen / "subject_scale_layout.py"),
                             sandbox.resolve())


if __name__ == "__main__":
    unittest.main()
