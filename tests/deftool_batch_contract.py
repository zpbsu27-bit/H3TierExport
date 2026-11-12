"""Contract tests for the isolated H3DefTool batch adapter."""
from __future__ import annotations

import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
SCRIPT = PRODUCT / "scripts" / "deftool_batch.py"
SPEC = importlib.util.spec_from_file_location("deftool_batch_under_test", SCRIPT)
assert SPEC and SPEC.loader
deftool_batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deftool_batch)


def write_indexed_bmp(path: Path, width: int = 16, height: int = 16) -> None:
    palette = bytearray()
    for index in range(256):
        palette.extend((index, (index * 3) & 255, (index * 7) & 255, 0))
    row_bytes = ((width + 3) // 4) * 4
    pixels = bytearray(row_bytes * height)
    for y in range(height):
        for x in range(width):
            pixels[y * row_bytes + x] = 0 if (x + y) % 3 else 9
    offset = 14 + 40 + len(palette)
    size = offset + len(pixels)
    file_header = struct.pack("<2sIHHI", b"BM", size, 0, 0, offset)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0,
                      len(pixels), 2835, 2835, 256, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_header + dib + palette + pixels)


class FakeVendorProcess:
    calls: list[tuple[list[str], Path]] = []
    timeout_mode = False
    crop_outside_canvas = False
    killed = False

    def __init__(self, command, **kwargs):
        self.command = list(command)
        self.cwd = Path(kwargs["cwd"])
        self.pid = 5000 + len(self.calls)
        self.wait_calls = 0
        self.__class__.calls.append((self.command, self.cwd))

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.timeout_mode and self.wait_calls == 1:
            raise subprocess.TimeoutExpired(self.command, timeout)
        lines = (self.cwd / self.command[-1]).read_text(encoding="ascii").splitlines()
        data = {key: value for line in lines if "=" in line
                for key, value in [line.split("=", 1)]}
        width = height = 16
        crop_left = 15 if self.crop_outside_canvas else 0
        crop_width = width if not self.crop_outside_canvas else 2
        groups = []
        for key in sorted((key for key in data if key.startswith("Group") and key[5:].isdigit()),
                          key=lambda value: int(value[5:])):
            group_id = int(key[5:])
            names = [Path(name.replace("\\", "/")).name
                     for name in data[key].split("|") if name]
            frames = [{
                "group_id": group_id,
                "frame_index": frame_index,
                "resource": name,
                "frame_header": [20, 1, width, height, crop_width, height, crop_left, 0],
            } for frame_index, name in enumerate(names)]
            groups.append({"group_id": group_id, "frame_count": len(frames),
                           "frames": frames})
        out_rel = data["Def Path"].replace("\\", "/")
        output = self.cwd / out_rel
        output.parent.mkdir(parents=True, exist_ok=True)
        first_resource = groups[0]["frames"][0]["resource"]
        bmp = (self.cwd / "inputs" / first_resource).read_bytes()
        palette = bmp[54:54 + 1024]
        rgb = bytearray()
        for index in range(256):
            blue, green, red, _reserved = palette[index * 4:index * 4 + 4]
            rgb.extend((red, green, blue))
        header = struct.pack("<4I", 0x42, width, height, len(groups)) + rgb
        metadata = json.dumps({"type": 0x42, "width": width, "height": height,
                               "groups": groups}).encode("ascii")
        output.write_bytes(header + metadata)
        # GrayFace's /g return code is treated as advisory; native parse decides.
        return 7

    def kill(self):
        self.__class__.killed = True


class DeftoolBatchContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deftool-batch-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.experiments = self.root / "build" / "experiments" / "deftool"
        self.old_experiments = deftool_batch.DEFAULT_EXPERIMENTS_ROOT
        deftool_batch.DEFAULT_EXPERIMENTS_ROOT = self.experiments
        self.addCleanup(setattr, deftool_batch, "DEFAULT_EXPERIMENTS_ROOT", self.old_experiments)
        self.tool_dir = self.root / "tool"
        self.tool_dir.mkdir()
        (self.tool_dir / "h3deftool.exe").write_bytes(b"test vendor executable placeholder")
        (self.tool_dir / "h3deftool.ini").write_text(
            "[Data]\nLod Path=D:\\formal-game\\Data\\H3sprite.lod\n",
            encoding="ascii")
        (self.tool_dir / "Language").mkdir()
        (self.tool_dir / "Language" / "english.txt").write_text("fixture", encoding="ascii")
        self.validator_root = self.root / "validator"
        validator_dir = self.validator_root / "tools"
        validator_dir.mkdir(parents=True)
        (validator_dir / "build_official_def_frame_map.py").write_text(
            "import json\ndef parse_def(blob):\n    return json.loads(blob[784:].decode('ascii'))\n",
            encoding="ascii")
        self.old_validator_allowlist = deftool_batch.TRUSTED_NATIVE_VALIDATOR_SHA256
        deftool_batch.TRUSTED_NATIVE_VALIDATOR_SHA256 = deftool_batch._sha256_file(
            validator_dir / "build_official_def_frame_map.py")
        self.addCleanup(setattr, deftool_batch, "TRUSTED_NATIVE_VALIDATOR_SHA256",
                        self.old_validator_allowlist)
        self.frames = self.root / "frames"
        write_indexed_bmp(self.frames / "walk-a.bmp")
        write_indexed_bmp(self.frames / "walk-b.bmp")
        self.job_path = self.root / "job.json"
        self._write_job()
        self._original_popen = deftool_batch.subprocess.Popen
        FakeVendorProcess.calls = []
        FakeVendorProcess.timeout_mode = False
        FakeVendorProcess.crop_outside_canvas = False
        FakeVendorProcess.killed = False

    def tearDown(self):
        deftool_batch.subprocess.Popen = self._original_popen

    def _write_job(self):
        job = {
            "schema": "h3.deftool.batch-input.v1",
            "batch_id": "contract-batch",
            "entries": [
                {
                    "entry_id": "CREA",
                    "output_name": "CREA",
                    "source_kind": "legacy_indexed_frames",
                    "canvas": {"width": 16, "height": 16, "native_origin": [0, 0]},
                    "options": {"generate_selection": True},
                    "groups": [{"native_group": 0, "frames": [
                        {"native_frame": 0, "source": "frames/walk-a.bmp",
                         "resource": "CREA000.BMP"},
                        {"native_frame": 1, "source": "frames/walk-b.bmp",
                         "resource": "CREA001.BMP"},
                    ]}],
                },
                {
                    "entry_id": "CREB",
                    "output_name": "CREB",
                    "source_kind": "legacy_indexed_frames",
                    "canvas": {"width": 16, "height": 16, "native_origin": [0, 0]},
                    "options": {"generate_selection": False},
                    "groups": [{"native_group": 2, "frames": [
                        {"native_frame": 0, "source": "frames/walk-a.bmp",
                         "resource": "CREB000.BMP"},
                    ]}],
                },
            ],
        }
        self.job_path.write_text(json.dumps(job), encoding="utf-8")

    def _plan(self):
        return deftool_batch.make_plan(self.job_path, self.tool_dir,
                                       self.validator_root, self.experiments)

    def test_plan_pins_every_frame_and_emits_explicit_safe_hdl(self):
        plan_path = self._plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["tool"]["sha256"],
                         deftool_batch._sha256_file(self.tool_dir / "h3deftool.exe"))
        self.assertEqual(len(plan["entries"]), 2)
        self.assertEqual(plan["entries"][0]["expected_native_slots"], [
            {"group": 0, "frame": 0, "resource": "CREA000.BMP"},
            {"group": 0, "frame": 1, "resource": "CREA001.BMP"},
        ])
        self.assertTrue(plan["entries"][0]["not_hd_rgba_master"])
        self.assertEqual(plan["upstream"]["repository"], "https://github.com/GrayFace/Misc")
        self.assertTrue(plan["output_policy"]["palette_remapping"])
        self.assertFalse(plan["output_policy"]["pixel_color_fidelity_verified"])
        self.assertIn("Type=2\r\n", plan["entries"][0]["hdl"]["text"])
        self.assertIn("Lod Path Check=0\r\n", plan["entries"][0]["hdl"]["text"])
        self.assertIn("Bitmaps Check=0\r\n", plan["entries"][0]["hdl"]["text"])
        self.assertIn("ColorsBox.ColorChecks=1|1|0|0|1|1|1|1|0|\r\n",
                      plan["entries"][0]["hdl"]["text"])
        self.assertFalse(any(".lod" in row["path"].lower()
                             for row in plan["tool"]["runtime_files"]))
        checked = deftool_batch.check_plan(plan_path, self.experiments)
        self.assertEqual(checked["status"], "PASS")
        self.assertFalse((self.experiments / plan["experiment_relative_path"] /
                          "run").exists())

    def test_nonzero_native_origin_is_rejected(self):
        job = json.loads(self.job_path.read_text(encoding="utf-8"))
        job["entries"][0]["canvas"]["native_origin"] = [1, 0]
        self.job_path.write_text(json.dumps(job), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, r"native_origin \[0, 0\]"):
            self._plan()

    def test_native_parser_must_match_fixed_allowlist(self):
        validator = self.validator_root / "tools" / "build_official_def_frame_map.py"
        validator.write_text(validator.read_text(encoding="ascii") + "# altered\n",
                             encoding="ascii")
        with self.assertRaisesRegex(deftool_batch.ContractError, "not the reviewed"):
            self._plan()

    def test_tampered_plan_cannot_self_pin_a_different_validator(self):
        plan_path = self._plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["native_validator"]["sha256"] = "0" * 64
        deftool_batch._seal_plan(plan)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, "allowlisted"):
            deftool_batch._load_plan(plan_path)

    def test_changed_indexed_input_invalidates_plan(self):
        plan_path = self._plan()
        source = self.frames / "walk-a.bmp"
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(deftool_batch.ContractError,
                                    "normalized entry contract changed"):
            deftool_batch.check_plan(plan_path, self.experiments)

    def test_rgba_or_truecolor_input_is_refused_before_vendor_call(self):
        source = self.frames / "walk-a.bmp"
        blob = bytearray(source.read_bytes())
        struct.pack_into("<H", blob, 28, 24)
        source.write_bytes(blob)
        with self.assertRaisesRegex(deftool_batch.ContractError, "8-bit indexed BMP"):
            self._plan()

    def test_def_resource_limit_includes_extension(self):
        job = json.loads(self.job_path.read_text(encoding="utf-8"))
        job["entries"][0]["groups"][0]["frames"][0]["resource"] = "123456789.BMP"
        self.job_path.write_text(json.dumps(job), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, "12-byte DEF frame-name limit"):
            self._plan()

    def test_self_sealed_hdl_cannot_enable_lod_or_change_def_path(self):
        plan_path = self._plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        entry = plan["entries"][0]
        entry["hdl"]["text"] = entry["hdl"]["text"].replace(
            "Lod Path Check=0", "Lod Path Check=1").replace(
            "Def Path=..\\output\\CREA.def",
            "Def Path=D:\\formal-game\\Data\\EVIL.def").replace(
            "Lod Path=\r\n", "Lod Path=D:\\formal-game\\Data\\H3sprite.lod\r\n")
        entry["hdl"]["sha256"] = deftool_batch._sha256_bytes(
            entry["hdl"]["text"].encode("utf-8"))
        stable = dict(plan)
        for key in ("created_utc", "plan_fingerprint", "experiment_relative_path",
                    "plan_integrity_sha256"):
            stable.pop(key, None)
        fingerprint = deftool_batch._sha256_bytes(deftool_batch._canonical_bytes(stable))
        plan["plan_fingerprint"] = fingerprint
        new_name = f"{plan['batch_id']}-{fingerprint[:16]}"
        plan["experiment_relative_path"] = new_name
        deftool_batch._seal_plan(plan)
        new_dir = plan_path.parent.parent / new_name
        plan_path.parent.rename(new_dir)
        attacked_plan = new_dir / "plan.json"
        attacked_plan.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, "canonical safety contract"):
            deftool_batch._load_plan(attacked_plan)

    def test_plan_fingerprint_is_bound_to_its_directory(self):
        plan_path = self._plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["plan_fingerprint"] = "a" * 64
        plan["experiment_relative_path"] = f"{plan['batch_id']}-{'a' * 16}"
        deftool_batch._seal_plan(plan)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, "plan_fingerprint"):
            deftool_batch._load_plan(plan_path)

    def test_manifest_contract_is_rechecked_before_check_passes(self):
        plan_path = self._plan()
        job = json.loads(self.job_path.read_text(encoding="utf-8"))
        job["entries"][0]["groups"][0]["frames"][1]["resource"] = "OTHER001.BMP"
        self.job_path.write_text(json.dumps(job), encoding="utf-8")
        with self.assertRaisesRegex(deftool_batch.ContractError, "input manifest changed"):
            deftool_batch.check_plan(plan_path, self.experiments)

    def test_fake_vendor_runs_one_process_per_entry_and_native_parser_closes(self):
        plan_path = self._plan()
        deftool_batch.subprocess.Popen = FakeVendorProcess
        run = deftool_batch.run_plan(plan_path)
        self.assertEqual(run["state"], "awaiting_verification", run)
        self.assertEqual(len(FakeVendorProcess.calls), 2)
        self.assertTrue(all(command[-2] == "/g" for command, _ in FakeVendorProcess.calls))
        self.assertTrue(all(entry["exit_code"] == 7 for entry in run["entries"]))
        self.assertTrue(all(entry["state"] == "candidate_created" for entry in run["entries"]))
        verification = deftool_batch.verify_plan(plan_path)
        self.assertEqual(verification["state"], "structurally_verified")
        self.assertFalse(verification["pixel_color_fidelity_verified"])
        self.assertFalse(verification["art_reviewed"])
        self.assertFalse(verification["native_runtime_verified"])
        self.assertEqual([entry["status"] for entry in verification["entries"]],
                         ["STRUCTURE_PASS", "STRUCTURE_PASS"])
        self.assertEqual(verification["entries"][1]["groups"][0]["native_group"], 2)
        with self.assertRaisesRegex(deftool_batch.ContractError, "run already started"):
            deftool_batch.run_plan(plan_path)
        self.assertEqual(len(FakeVendorProcess.calls), 2)

    def test_frame_crop_must_stay_inside_full_canvas(self):
        plan_path = self._plan()
        FakeVendorProcess.crop_outside_canvas = True
        deftool_batch.subprocess.Popen = FakeVendorProcess
        run = deftool_batch.run_plan(plan_path)
        self.assertEqual(run["state"], "awaiting_verification")
        with self.assertRaisesRegex(deftool_batch.ContractError, "crop rectangle outside full canvas"):
            deftool_batch.verify_plan(plan_path)
        verification = json.loads((plan_path.parent / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["state"], "rejected")
        entry_root = plan_path.parent / "run" / "CREA"
        staged_ini = (entry_root / "worker" / "h3deftool.ini").read_text(encoding="ascii")
        self.assertNotIn("formal-game", staged_ini)
        self.assertFalse(any((entry_root / "worker").rglob("*.lod")))

    def test_timeout_kills_only_current_vendor_pid_and_seals_run(self):
        plan_path = self._plan()
        FakeVendorProcess.timeout_mode = True
        deftool_batch.subprocess.Popen = FakeVendorProcess
        run = deftool_batch.run_plan(plan_path)
        self.assertEqual(run["state"], "timed_out")
        self.assertTrue(FakeVendorProcess.killed)
        self.assertEqual(len(FakeVendorProcess.calls), 1)
        with self.assertRaisesRegex(deftool_batch.ContractError, "run already started"):
            deftool_batch.run_plan(plan_path)
        self.assertEqual(len(FakeVendorProcess.calls), 1)


if __name__ == "__main__":
    unittest.main()
