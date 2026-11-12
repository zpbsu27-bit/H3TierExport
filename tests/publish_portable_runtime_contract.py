"""The current portable release must carry its exact companion payload."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/"scripts"))
import publish
import companion_runtime


class PortableRuntimePublish(unittest.TestCase):
    def test_copy_is_content_pinned_and_reusable(self):
        build=json.loads((PRODUCT/"build/candidate/build-receipt.json").read_bytes())
        binding=build["companion_runtime"];sources=build["sources"]
        with tempfile.TemporaryDirectory(prefix="portable-runtime-publish-") as directory:
            root=Path(directory);(root/binding["directory"]).mkdir()
            shutil.rmtree(root/binding["directory"])
            shutil.copytree(PRODUCT/binding["directory"],root/binding["directory"])
            dist=root/"dist";dist.mkdir()
            with patch.object(publish,"PRODUCT",root),patch.object(publish,"DIST",dist):
                first=publish.publish_portable_runtime(binding,sources)
                self.assertTrue(first["verified"])
                second=publish.publish_portable_runtime(binding,sources)
                self.assertEqual(first,second)
                target=dist/binding["directory"]
                self.assertTrue((target/"runtime-manifest.json").is_file())
                self.assertEqual(len(list(target.rglob("*.py"))),len([r for r in json.loads((target/"runtime-manifest.json").read_bytes())["files"] if r["path"].endswith(".py")]))
                # The native loader rejects hardlinks as non-independent runtime inputs.
                for path in target.rglob("*"):
                    if path.is_file():
                        self.assertEqual(path.stat().st_nlink, 1, str(path))

    def test_relative_receipt_paths_and_adjacent_payload_are_portable(self):
        build_path=PRODUCT/"build/candidate/build-receipt.json"
        build_raw=build_path.read_bytes();build=json.loads(build_raw)
        binding=build["companion_runtime"]
        exe_source=PRODUCT/"build/candidate/H3TierExport.exe";exe_raw=exe_source.read_bytes()
        exe_sha=__import__("hashlib").sha256(exe_raw).hexdigest()
        build["executable"]["sha256"]=exe_sha
        tests={"schema":"h3.tier-export.test-suite.v2","passed":True,"source_current":True,
            "exe_sha256":exe_sha,"build_receipt_sha256":"","results":[{"name":"PORTABLE_TEST","exit_code":0}]}
        with tempfile.TemporaryDirectory(prefix="portable-read-release-") as directory:
            root=Path(directory);dist=root/"dist";dist.mkdir()
            exe=dist/"H3TierExport.exe";exe.write_bytes(exe_raw)
            encoded=lambda value:(json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
            build_bytes=encoded(build);(dist/"H3TierExport-build-receipt.json").write_bytes(build_bytes)
            tests["build_receipt_sha256"]=__import__("hashlib").sha256(build_bytes).hexdigest()
            tests_bytes=encoded(tests);(dist/"H3TierExport-tests-receipt.json").write_bytes(tests_bytes)
            shutil.copytree(PRODUCT/binding["directory"],dist/binding["directory"])
            sha=lambda raw:__import__("hashlib").sha256(raw).hexdigest()
            release={"schema":"h3.tier-export.release.v2","exe_sha256":exe_sha,
                "build_receipt_sha256":sha(build_bytes),"test_receipt_sha256":sha(tests_bytes),
                "companion_runtime":binding,"files":[
                    {"path":"H3TierExport.exe","sha256":exe_sha},
                    {"path":"H3TierExport-build-receipt.json","sha256":sha(build_bytes)},
                    {"path":"H3TierExport-tests-receipt.json","sha256":sha(tests_bytes)}]}
            (dist/"H3TierExport-release.json").write_bytes(encoded(release))
            runtime_root=dist/binding["directory"]
            with patch.object(companion_runtime,"PRODUCT",dist),patch.object(companion_runtime,"frozen_root",return_value=runtime_root):
                loaded=companion_runtime.read_release(exe_sha,exe)
            self.assertEqual(Path(loaded["path"]),dist/"H3TierExport-build-receipt.json")
            self.assertEqual(loaded["binding"],binding)


if __name__=="__main__":unittest.main()
