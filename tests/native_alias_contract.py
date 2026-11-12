"""R9 real native F0/F10 typed alias contracts. No external producer mutations."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_alias as alias_module
import companion_runtime
EXE = Path(os.environ.get("H3TIEREXPORT_EXE",ROOT/"build/candidate/H3TierExport.exe"))
CUE_SHA = "f8f79291ff8727739044630601a6f48a037479c0365665e676de32aca6797923"

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save_new(path,object):
    with Path(path).open("x",encoding="utf-8") as f:
        json.dump(object,f,ensure_ascii=False,indent=2)
        f.write("\n")

def fixed_root():
    config=json.loads((ROOT/"resources/H3TierExport-workspace.json").read_bytes())
    return Path(config["h3_root"])

def cue_location():
    return fixed_root()/("CompleteOfficialRelease-20261006-v1/codex-\u9ad8\u6e05\u6bcd\u7248/"
                         "\u5236\u4f5c\u8d44\u6599/\u5b9d\u7269\u57ce\u5821/\u6279\u91cf\u8fed\u4ee3/"
                         "TBFREXT2-native-exact-alias-20261014-r01.json")

class NativeAliasContract(unittest.TestCase):
    def setUp(self):
        (ROOT/"build/tests").mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix="alias-frozen-",dir=ROOT/"build/tests")
        self.addCleanup(self.temp.cleanup)
        self.sandbox=Path(self.temp.name).resolve()
        self.cuepath=cue_location()
        if not self.cuepath.is_file():
            self.skipTest("historical TBFREXT2 native-alias integration cue is unavailable")
        self.cue=json.loads(self.cuepath.read_bytes())
        pins=self.cue.get("current_F0_pins",{})
        missing=[value.get("path") for value in pins.values()
                 if type(value) is dict and type(value.get("path")) is str
                 and not Path(value["path"]).is_file()]
        if missing:
            self.skipTest("historical TBFREXT2 native-alias integration inputs are unavailable: "
                          + ", ".join(Path(path).name for path in missing))
        self.assertEqual(sha(self.cuepath),CUE_SHA)
        self.master=Path(self.cue["current_F0_pins"]["selected_exact3x_output"]["path"])
        self.assertEqual(sha(self.master),self.cue["current_F0_pins"]["selected_exact3x_output"]["sha256"])
        with Image.open(self.master) as im:
            self.assertEqual(im.mode,"RGBA")
            rgba_sha=hashlib.sha256(im.tobytes()).hexdigest()
            wh=list(im.size)
        self.operation="alias-"+self.sandbox.name
        self.manifest=self.sandbox/"manifest.json"
        self.output=ROOT/"build/experiments/native-alias"/self.operation
        self.data={
            "schema":"h3.native-alias.intake.v1",
            "operation_id":self.operation,
            "native_cue":{"path":str(self.cuepath),"sha256":CUE_SHA},
            "selected_3x":{"path":str(self.master),
                           "sha256":sha(self.master),"pixels":wh,"rgba_sha256":rgba_sha},
            "owner_key":["def","TBFREXT2",0,0],
            "target_key":["def","TBFREXT2",0,10],
            "intent":"CANDIDATE_ONLY_NO_ART_APPROVAL"
        }
        self.seal()

    def seal(self):
        if self.manifest.exists():
            self.manifest.unlink()
        save_new(self.manifest,self.data)

    def invoke(self,stage,success=True,out=None):
        output=out or self.output
        p=subprocess.run([str(EXE),"native-alias",stage,"--manifest",str(self.manifest),
                          "--manifest-sha256",sha(self.manifest),"--outdir",str(output),
                          "--operation-id",self.operation],cwd=ROOT,
                          capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=240)
        self.assertEqual(p.returncode==0,success,(p.stdout+p.stderr)[-4500:])
        raw=p.stdout.strip() or p.stderr.strip()
        result=json.loads(raw)
        return result

    def test_original_f0f10_candidate_reuses_one_exact_3x_png_but_no_authority(self):
        checked=self.invoke("check")
        self.assertEqual(checked["status"],"CANDIDATE_REVIEW_REQUIRED")
        self.assertTrue(checked["native_equivalent"])
        self.assertFalse(self.output.exists())
        generated=self.invoke("run")
        self.assertEqual(generated["status"],"CANDIDATE_REVIEW_REQUIRED")
        triage=json.loads((self.output/"native-alias-triage.json").read_bytes())
        self.assertEqual(triage["native_frame_count"],2)
        self.assertFalse(triage["art_approved"])
        self.assertFalse(triage["native_timing_verified"])
        self.assertEqual(triage["selected_source_3x_sha256"],sha(self.master))
        native=json.loads((self.output/"motion-native-contracts.json").read_bytes())
        self.assertEqual(len(native["entries"]),2)
        self.assertEqual(native["entries"][0]["equivalence_class"],native["entries"][1]["equivalence_class"])
        spec=json.loads((self.output/"motion-spec.json").read_bytes())
        self.assertEqual(spec["items"][0]["mode"],"reuse")
        self.assertEqual(spec["items"][1]["reuse_from"],spec["items"][0]["id"])
        report=json.loads((self.output/"motion-run-report.json").read_bytes())
        self.assertEqual(report["failed"],0)
        self.assertEqual(report["duplicate_slots"],1)
        pictures=[Path(v["output"]) for v in report["results"]]
        self.assertEqual(len(pictures),2)
        self.assertEqual(sha(pictures[0]),sha(pictures[1]))
        self.assertEqual(pictures[0].read_bytes(),self.master.read_bytes())
        self.assertEqual(self.invoke("verify")["status"],"VERIFIED_REVIEW_REQUIRED")
        before={p.relative_to(self.output).as_posix():sha(p) for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(self.invoke("run")["status"],"REUSED_VERIFIED")
        self.assertEqual(before,{p.relative_to(self.output).as_posix():sha(p) for p in self.output.rglob("*") if p.is_file()})

    def test_changed_selected_source_sha_rejected_before_output(self):
        self.data["selected_3x"]["sha256"]="0"*64
        self.seal()
        self.assertIn("SOURCE",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_wrong_target_native_key_never_invents_slot(self):
        self.data["target_key"]=["def","TBFREXT2",0,11]
        self.seal()
        self.assertIn("NATIVE_KEY",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_foreign_identity_never_reuses_another_art(self):
        self.data["target_key"]=["def","TBCSEXT2",0,10]
        self.seal()
        self.assertIn("NATIVE_KEY",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_cue_native_palette_tampering_even_with_resigned_json_ref_fails(self):
        cue=copy.deepcopy(self.cue)
        cue["original_native_frames"][1]["original_native_proof"]["palette_sha256"]="f"*64
        tampered=self.sandbox/"altered-native-cue.json"
        save_new(tampered,cue)
        self.data["native_cue"]={"path":str(tampered),"sha256":sha(tampered)}
        self.seal()
        self.assertIn("NATIVE",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_both_native_palettes_resealed_identically_cannot_bypass_real_def_reader(self):
        cue=copy.deepcopy(self.cue)
        for frame in cue["original_native_frames"]:
            frame["original_native_proof"]["palette_sha256"]="f"*64
        altered=self.sandbox/"both-false-native.json"
        save_new(altered,cue)
        self.data["native_cue"]={"path":str(altered),"sha256":sha(altered)}
        self.seal()
        self.assertIn("NATIVE_INDEX_OR_PALETTE_DRIFT",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_new_selected_3x_image_with_resigned_cue_rejected_by_actual_f0_selection(self):
        cue=copy.deepcopy(self.cue)
        alternative=self.sandbox/"alternate-selected.png"
        with Image.open(self.master) as im:
            substitute=im.copy()
        substitute.putpixel((0,0),(31,29,27,1))
        substitute.save(alternative)
        alt_bytes=sha(alternative)
        with Image.open(alternative) as im:
            alt_rgba=hashlib.sha256(im.tobytes()).hexdigest()
        cue["current_F0_pins"]["selected_exact3x_output"].update(
            {"path":str(alternative),"sha256":alt_bytes,"bytes":alternative.stat().st_size})
        forged=self.sandbox/"forged-selected-source.json"
        save_new(forged,cue)
        self.data["native_cue"]={"path":str(forged),"sha256":sha(forged)}
        self.data["selected_3x"].update(
            {"path":str(alternative),"sha256":alt_bytes,"rgba_sha256":alt_rgba})
        self.seal()
        self.assertIn("F0_SELECTION_OUTPUT_MISMATCH",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_invalid_claim_of_f10_art_approval_rejected(self):
        cue=copy.deepcopy(self.cue)
        cue["acceptance"]["new_key_human_approved"]=True
        altered=self.sandbox/"false-art.json"
        save_new(altered,cue)
        self.data["native_cue"]={"path":str(altered),"sha256":sha(altered)}
        self.seal()
        self.assertIn("ART",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_stale_parent_receipt_sha_rejected(self):
        cue=copy.deepcopy(self.cue)
        cue["current_F0_pins"]["raw_intake"]["sha256"]="e"*64
        altered=self.sandbox/"stale-parent.json"
        save_new(altered,cue)
        self.data["native_cue"]={"path":str(altered),"sha256":sha(altered)}
        self.seal()
        self.assertIn("REFERENCE",str(self.invoke("check",False)))
        self.assertFalse(self.output.exists())

    def test_operation_id_collision_never_overwrites_existing_output(self):
        collision=self.output.parent/(self.operation+"-wrong")
        collision.mkdir(parents=True,exist_ok=False)
        self.assertIn("OUTPUT",str(self.invoke("run",False,out=collision)))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(collision.iterdir()),[])

    def test_rejects_bool_frame_index_and_cross_group(self):
        self.data["target_key"]=["def","TBFREXT2",True,10]
        self.seal()
        self.assertIn("NATIVE_KEY",str(self.invoke("check",False)))
        self.data["target_key"]=["def","TBFREXT2",1,10]
        self.seal()
        self.assertIn("NATIVE_KEY",str(self.invoke("check",False)))

class NativeAliasRuntimeSourceContract(unittest.TestCase):
    """Synthetic common-admission fixtures, not formal release qualification."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alias-runtime-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.live = self.root / "scripts/native_alias.py"
        self.live.parent.mkdir()
        self.live.write_bytes(b"# unrelated live checkout\n")
        source = b"# TEST_ONLY frozen companion source\n"
        row = {"path": "native_alias.py", "source_path": "scripts/native_alias.py",
               "bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()}
        raw = (json.dumps({"schema": "h3.tier-export.companion-runtime.v1",
                           "entrypoints": {"native-alias": "native_alias.py"}, "files": [row]}) + "\n").encode()
        self.payload = self.root / (".runtime-" + hashlib.sha256(raw).hexdigest())
        self.payload.mkdir()
        (self.payload / "runtime-manifest.json").write_bytes(raw)
        self.frozen = self.payload / "native_alias.py"
        self.frozen.write_bytes(source)
        self.renderer = self.root / "TEST-renderer.exe"
        self.renderer.write_bytes(b"TEST_ONLY renderer identity; never executed")
        self.receipt = {"passed": True, "source_current": True,
                        "executable": {"sha256": sha(self.renderer)},
                        "sources": [{"path": "scripts\\native_alias.py", "sha256": row["sha256"]}]}
        self.build = self.root / "build.json"
        save_new(self.build, self.receipt)

    def test_formal_alias_source_is_frozen_and_missing_or_drifted_payload_never_falls_back(self):
        binding = {"authority": "formal_release", "path": str(self.build), "sha256": sha(self.build)}
        with mock.patch.object(alias_module, "_read_build_receipt", return_value=binding), \
                mock.patch.object(companion_runtime, "frozen_root", return_value=self.payload):
            self.assertEqual(alias_module.current_receipt_build(self.renderer, sha(self.renderer)), sha(self.build))
            self.live.write_bytes(b"# later live source\n")
            self.assertEqual(alias_module.current_receipt_build(self.renderer, sha(self.renderer)), sha(self.build))
            self.frozen.write_bytes(self.frozen.read_bytes() + b"# tamper\n")
            with self.assertRaisesRegex(alias_module.ReviewError, "RELEASE_COMPANION_SOURCE_DRIFT"):
                alias_module.current_receipt_build(self.renderer, sha(self.renderer))
            self.frozen.unlink()
            with self.assertRaises(OSError):
                alias_module.current_receipt_build(self.renderer, sha(self.renderer))

    def test_candidate_alias_still_requires_live_build_source_qualification(self):
        with mock.patch.object(alias_module, "_read_build_receipt", return_value={"path": str(self.build)}), \
                mock.patch.object(alias_module, "validate_build_sources", side_effect=RuntimeError("BUILD_SOURCE_DRIFT")) as check:
            with self.assertRaisesRegex(RuntimeError, "BUILD_SOURCE_DRIFT"):
                alias_module.current_receipt_build(self.renderer, sha(self.renderer))
            check.assert_called_once_with(self.receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
