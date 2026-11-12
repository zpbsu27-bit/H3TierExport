"""GPU-free contracts for the native CLI directory and companion dispatcher."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PRODUCT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("H3TIEREXPORT_EXE", PRODUCT / "build" / "candidate" / "H3TierExport.exe"))
NATIVE = {
    "help", "capabilities", "info", "selftest", "ui-smoke", "checkerboard-alpha", "plan", "check", "approve",
    "run", "verify", "bench", "expand-plan", "expand-check", "expand", "expand-verify",
    "motion-plan", "motion-check", "motion-probe", "motion-run", "motion-verify", "review-render", "logical-alpha-render",
    "skill-variant-check", "skill-variant-export", "skill-variant-verify",
    "map-artifact-check", "map-artifact-export", "map-artifact-verify",
}
COMPANIONS = {
    "tile-design", "tile-sampling-proof", "tile-interface",
    "source-audit", "motion-review", "native-review", "interpolate", "motion-prepare",
    "official-motion-prepare", "quality-review",
    "creature-actions", "art-pipeline", "defpack", "cloud-animation",
    "alpha-benchmark", "blender-actions", "creature-production", "creature-master-set", "creature-auto-pose", "hero-mounted-pose", "logical-alpha", "sheet-intake", "native-alias", "town-process-review", "town-master-prepare", "town-flag-alignment", "town-flag-review", "flag-pole-audit", "town-scene", "map-synth", "map-source-batch", "map-material-family", "asset-repair", "scale-draft", "scale-audit", "scale-layout", "sequence-temporal", "map-object-sequence", "map-sprite-mod", "creature-sequence-qa",
}
ACTIVE_COMPANIONS = COMPANIONS - {"quality-review"}


class CompanionCliContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not EXE.is_file():
            raise RuntimeError(f"candidate executable missing: {EXE}")

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(EXE), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)

    def test_capabilities_directory_covers_native_and_companion_commands(self) -> None:
        result = self.invoke("capabilities")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "h3.tier-export.capabilities.v1")
        self.assertEqual({item["name"] for item in payload["native_commands"]}, NATIVE)
        companions = {item["name"]: item for item in payload["companion_commands"]}
        self.assertEqual(set(companions), COMPANIONS)
        self.assertTrue(all(item["runtime"] == "external-python" for item in companions.values()))
        self.assertTrue(companions["interpolate"]["required_python_modules"])
        self.assertEqual({name for name, row in companions.items() if row["qualified_runtime_payload"]}, ACTIVE_COMPANIONS)
        self.assertTrue(all(companions[name]["status"] == "active" for name in ACTIVE_COMPANIONS))
        self.assertEqual(companions["quality-review"]["status"], "retired")
        self.assertEqual(companions["quality-review"]["replacement"], "native-review")
        self.assertEqual(companions["blender-actions"]["runtime_resources"], ["blender-worker"])
        self.assertEqual(companions["art-pipeline"]["runtime_resources"], ["h3-art-pipeline"])
        self.assertEqual(companions["flag-pole-audit"]["runtime_resources"], ["flag-pole-reference"])
        self.assertFalse(payload["automatic_art_approval"])
        self.assertFalse(payload["game_deployment"])
        self.assertIn("no learned model inference", payload["native_mesh_interpolation"])

    def test_help_names_every_companion_and_external_runtime(self) -> None:
        result = self.invoke("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in COMPANIONS | {"capabilities"}:
            self.assertIn(command, result.stdout)
        self.assertIn("external RIFE executable/model", result.stdout)

    def test_renderer_wrapper_help_names_actual_arguments_and_output_root(self) -> None:
        for command in ('native-review', 'sheet-intake', 'native-alias', 'town-process-review', 'town-flag-alignment', 'town-scene', 'map-synth', 'map-source-batch', 'map-material-family', 'motion-review', 'asset-repair', 'scale-draft', 'scale-audit', 'scale-layout'):
            with self.subTest(command=command):
                result = self.invoke(command, '--help', '--python', 'missing-python.exe')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Usage: H3TierExport.exe '+command, result.stdout)
                self.assertNotIn('--renderer', result.stdout)
                self.assertIn(str(PRODUCT/'build'/'experiments'/command), result.stdout)
                self.assertIn('new direct child', result.stdout)
                self.assertIn('supplies renderer identity automatically', result.stdout)

    def test_wrapper_help_maps_dist_root_to_product_build_root(self) -> None:
        capability = json.loads(self.invoke("capabilities").stdout)
        runtime_sha = capability["companion_runtime_manifest_sha256"]
        with tempfile.TemporaryDirectory(prefix="companion-product-root-") as temporary:
            product = Path(temporary) / "H3TierExport"
            (product / "src").mkdir(parents=True)
            (product / "src" / "main.cpp").write_text("source marker", encoding="utf-8")
            (product / "scripts").mkdir()
            dist = product / "dist"
            runtime = dist / (".runtime-" + runtime_sha)
            runtime.mkdir(parents=True)
            (runtime / "runtime-manifest.json").write_text("{}", encoding="utf-8")
            result = self.invoke("scale-layout", "--tool-root", str(dist), "--help")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(product / "build" / "experiments" / "scale-layout"), result.stdout)
            self.assertNotIn(str(dist / "build" / "experiments" / "scale-layout"), result.stdout)

    def test_ui_smoke_reports_the_resolved_product_root(self) -> None:
        result = self.invoke("ui-smoke")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["task_tool_root"]), PRODUCT.resolve())

    def test_info_exposes_the_same_command_directory(self) -> None:
        result = self.invoke("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = json.loads(result.stdout)["cli"]
        self.assertEqual({item["name"] for item in directory["native_commands"]}, NATIVE)
        self.assertEqual({item["name"] for item in directory["companion_commands"]}, COMPANIONS)

    def test_dispatch_preserves_quoted_unicode_arguments_and_child_exit_code(self) -> None:
        test_root = PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="companion cli 中文 ", dir=test_root) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text("test root marker\n", encoding="utf-8")
            scripts = root / "scripts"
            scripts.mkdir()
            fake = scripts / "interpolate_rife_candidates.py"
            fake.write_text(
                "import json, sys\n"
                "print(json.dumps({'argv': sys.argv[1:]}, ensure_ascii=False))\n"
                "print('child-stderr-marker', file=sys.stderr)\n"
                "raise SystemExit(37)\n", encoding="utf-8")
            result = self.invoke("interpolate", "--tool-root", str(root),
                                 "--python=" + sys.executable, "--batch", "素材 路径.json",
                                 "--label", "帧间候选 雪")
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertIn("child-stderr-marker", result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                "argv": ["--batch", "素材 路径.json", "--label", "帧间候选 雪"]})

    def test_native_review_binds_the_invoking_renderer_bytes(self) -> None:
        test_root = PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native review 中文 ", dir=test_root) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text("test root marker\n", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "review_native_batch.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
                encoding="utf-8")
            result = self.invoke("native-review", "--tool-root", str(root),
                                 "--python", sys.executable, "--manifest", "母版 审图.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [
                "--manifest", "母版 审图.json", "--renderer", str(EXE.resolve()),
                "--renderer-sha256", hashlib.sha256(EXE.read_bytes()).hexdigest(),
            ])

    def test_scene_and_sequence_bind_actual_renderer_and_reject_override(self) -> None:
        test_root = PRODUCT / "build/tests"
        test_root.mkdir(parents=True, exist_ok=True)
        for command, script in (("town-scene", "town_scene_composer.py"),
                                ("motion-review", "review_motion_sequence.py")):
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="scene-sequence-cli-", dir=test_root) as tmp:
                root = Path(tmp)
                (root / "src").mkdir()
                (root / "src/main.cpp").write_text("root marker", encoding="utf-8")
                (root / "scripts").mkdir()
                (root / "scripts" / script).write_text(
                    "import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n", encoding="utf-8")
                args = ["check", "--manifest", "真实 场景.json"] if command == "town-scene" else ["--run-report", "真实 动作.json"]
                result = self.invoke(command, "--tool-root", str(root), "--python", sys.executable, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), args + ["--renderer", str(EXE.resolve()),
                    "--renderer-sha256", hashlib.sha256(EXE.read_bytes()).hexdigest()])
                if command == "motion-review":
                    collection_args = ["--collection", "真实 8作业集合.json", "--collection-sha256", "a" * 64]
                    result = self.invoke(command, "--tool-root", str(root), "--python", sys.executable, *collection_args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), collection_args + ["--renderer", str(EXE.resolve()),
                        "--renderer-sha256", hashlib.sha256(EXE.read_bytes()).hexdigest()])
                result = self.invoke(command, "--renderer=other.exe")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)["code"], "RENDERER_OVERRIDE_REJECTED")

    def test_native_review_rejects_renderer_override_before_dispatch(self) -> None:
        for option in ("--renderer", "--renderer-sha256", "--renderer=other.exe",
                       "--renderer-sha256=" + "0" * 64):
            with self.subTest(option=option):
                result = self.invoke("native-review", option, "ignored")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)["code"], "RENDERER_OVERRIDE_REJECTED")

    def test_missing_tool_root_is_structured_and_nonzero(self) -> None:
        absent = PRODUCT / "build" / "tests" / "no such companion root"
        result = self.invoke("motion-review", "--tool-root", str(absent),
                             "--python", sys.executable)
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["schema"], "h3.tier-export.companion-error.v1")
        self.assertEqual(payload["status"], "DEPENDENCY_MISSING")
        self.assertEqual(payload["code"], "TOOL_ROOT_UNAVAILABLE")

    def test_timeout_stops_only_the_owned_process_tree(self) -> None:
        test_root = PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="companion-timeout-", dir=test_root) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text("test root marker\n", encoding="utf-8")
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "creature_action_plan.py").write_text(
                "import json, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "print(json.dumps({'owned_child_pid': child.pid}), flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8")
            result = self.invoke("creature-actions", "--tool-root", str(root),
                                 "--python", sys.executable, "--companion-timeout-seconds", "2")
            self.assertEqual(result.returncode, 124, result.stderr)
            payload = json.loads(result.stderr)
            self.assertEqual(payload["status"], "TIMED_OUT")
            self.assertEqual(payload["code"], "COMPANION_TIMED_OUT")
            child_pid = json.loads(result.stdout)["owned_child_pid"]
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel.OpenProcess(0x1000, False, child_pid)
            if handle:
                try:
                    exit_code = ctypes.c_ulong()
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                    self.assertNotEqual(exit_code.value, 259, "owned descendant remained running")
                finally:
                    kernel.CloseHandle(handle)

    def test_timeout_range_and_duplicate_options_are_rejected_before_start(self) -> None:
        for value in ("0", "86401", "-1", "1.5", ""):
            with self.subTest(value=value):
                result = self.invoke("creature-actions", "--companion-timeout-seconds=" + value)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)["code"], "COMPANION_TIMEOUT_INVALID")
        result = self.invoke("creature-actions", "--companion-timeout-seconds", "1",
                             "--companion-timeout-seconds=2")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["code"], "DUPLICATE_COMPANION_TIMEOUT")

    def test_missing_python_is_structured_and_nonzero(self) -> None:
        result = self.invoke("motion-prepare", "--python",
                             str(PRODUCT / "build" / "tests" / "missing-python.exe"))
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["status"], "DEPENDENCY_MISSING")
        self.assertEqual(payload["code"], "PYTHON_UNAVAILABLE")

    def test_unknown_command_is_structured_and_nonzero(self) -> None:
        result = self.invoke("motion-review-unlisted")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["status"], "UNKNOWN_COMMAND")


    def test_sheet_intake_binds_invoking_executable_and_rejects_override(self) -> None:
        test_root=PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sheet dispatch 中文 ",dir=test_root) as tmp:
            root=Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text("tool marker",encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "sheet_intake.py").write_text(
                "import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n",encoding="utf-8")
            p=self.invoke("sheet-intake","--tool-root",str(root),"--python",sys.executable,
                          "check","--manifest","整图 身份.json")
            self.assertEqual(p.returncode,0,p.stderr)
            args=json.loads(p.stdout)
            self.assertEqual(args,["check","--manifest","整图 身份.json","--renderer",
                str(EXE.resolve()),"--renderer-sha256",hashlib.sha256(EXE.read_bytes()).hexdigest()])
            p=self.invoke("sheet-intake","--renderer","other.exe")
            self.assertEqual(p.returncode,2)
            self.assertEqual(json.loads(p.stderr)["code"],"RENDERER_OVERRIDE_REJECTED")

    def test_town_process_review_binds_exact_invoking_binary_and_denies_override(self) -> None:
        test_root = PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="town review dispatch-", dir=test_root) as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.cpp").write_text("tool marker", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "town_process_review.py").write_text(
                "import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n",
                encoding="utf-8")
            result = self.invoke("town-process-review", "--tool-root", str(root),
                                 "--python", sys.executable, "check", "--manifest", "town-source.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [
                "check", "--manifest", "town-source.json", "--renderer", str(EXE.resolve()),
                "--renderer-sha256", hashlib.sha256(EXE.read_bytes()).hexdigest()])
            for option in ("--renderer", "--renderer-sha256", "--renderer=other.exe"):
                with self.subTest(option=option):
                    error = self.invoke("town-process-review", option, "anything")
                    self.assertEqual(error.returncode, 2)
                    self.assertEqual(json.loads(error.stderr)["code"], "RENDERER_OVERRIDE_REJECTED")


    def test_candidate_commands_keep_explicit_pythonpath_and_formal_refuses_unqualified_checkout(self) -> None:
        test_root = PRODUCT / "build/tests"
        test_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="TEST-pythonpath-", dir=test_root) as temporary:
            root = Path(temporary).resolve()
            (root / "src").mkdir()
            (root / "src/main.cpp").write_text("TEST root marker", encoding="utf-8")
            (root / "scripts").mkdir()
            dependencies = root / "explicit-dependencies"
            dependencies.mkdir()
            (dependencies / "h3_explicit_dependency.py").write_text("VALUE = 'explicit-runtime-dependency'\n", encoding="utf-8")
            for script in ("creature_action_plan.py", "review_native_batch.py"):
                (root / "scripts" / script).write_text("import h3_explicit_dependency as d\nprint(d.VALUE)\n", encoding="utf-8")
            (root / "dist").mkdir()
            legacy_formal = root / "dist/H3TierExport.exe"
            shutil.copy2(EXE, legacy_formal)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(dependencies)
            for executable, command in ((EXE, "creature-actions"), (EXE, "native-review")):
                with self.subTest(executable=str(executable), command=command):
                    result = subprocess.run([str(executable), command, "--tool-root", str(root),
                                             "--python", sys.executable], capture_output=True, text=True,
                                            encoding="utf-8", env=env, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "explicit-runtime-dependency")
            formal = subprocess.run([str(legacy_formal), "creature-actions", "--tool-root", str(root),
                                     "--python", sys.executable], capture_output=True, text=True,
                                    encoding="utf-8", env=env, timeout=30)
            self.assertEqual(formal.returncode, 2)
            self.assertEqual(json.loads(formal.stderr)["code"], "RELEASE_RUNTIME_INVALID")
            self.assertNotIn("explicit-runtime-dependency", formal.stdout)

    def test_retired_writer_rejects_before_python_root_or_output_side_effects(self) -> None:
        for arguments in (("--help",), ("--native-only", "--python", "missing.exe", "--tool-root", "missing-root")):
            with self.subTest(arguments=arguments):
                result = self.invoke("quality-review", *arguments)
                self.assertEqual(result.returncode, 2)
                error = json.loads(result.stderr)
                self.assertEqual(error["status"], "RETIRED")
                self.assertEqual(error["code"], "COMPANION_RETIRED")
                self.assertEqual(error["replacement"], "native-review")
        direct = subprocess.run([sys.executable, "-B", str(PRODUCT / "scripts/quality_review.py"), "--help"],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(direct.returncode, 2)
        self.assertEqual(json.loads(direct.stderr)["status"], "RETIRED")

    def test_atomic_controller_death_stops_both_owned_children_and_grandchildren(self) -> None:
        import ctypes
        import time
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        parent = PRODUCT / "build/tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="TEST-atomic-job-", dir=parent) as temporary:
            root = Path(temporary)
            child = root / "child.py"
            child.write_text(
                "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
                "grand=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'])\n"
                "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),grand.pid]))\n"
                "time.sleep(90)\n", encoding="utf-8")
            controller = root / "controller.py"
            controller.write_text(
                "import sys,time\nfrom pathlib import Path\n"
                f"sys.path.insert(0,{str(PRODUCT / 'tests')!r})\n"
                "from logical_alpha_atomic_contract import _AtomicChildJob\n"
                "with _AtomicChildJob() as job:\n"
                f"    job.start_script({str(child)!r},[{str(root / 'one.json')!r}])\n"
                f"    job.start_script({str(child)!r},[{str(root / 'two.json')!r}])\n"
                "    time.sleep(90)\n", encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(controller)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            handles = []
            try:
                deadline = time.monotonic() + 25
                records = None
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        records = [json.loads((root / name).read_bytes()) for name in ("one.json", "two.json")]
                        break
                    except (FileNotFoundError, json.JSONDecodeError):
                        time.sleep(0.02)
                self.assertIsNotNone(records, "owned test children did not reach the start gate")
                self.assertIsNone(process.poll())
                for pid in [pid for pair in records for pid in pair]:
                    handle = kernel.OpenProcess(0x1001, False, pid)  # query + terminate only our exact live process
                    self.assertTrue(handle, f"owned process not alive: {pid}")
                    handles.append(handle)
                    code = ctypes.c_ulong()
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertEqual(code.value, 259)
                process.kill()
                process.wait(timeout=10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    codes = []
                    for handle in handles:
                        code = ctypes.c_ulong()
                        self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                        codes.append(code.value)
                    if all(code != 259 for code in codes):
                        break
                    time.sleep(0.02)
                self.assertTrue(all(code != 259 for code in codes), "controller death left an owned descendant running")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
                for handle in handles:
                    code = ctypes.c_ulong()
                    if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259:
                        kernel.TerminateProcess(handle, 1)
                    kernel.CloseHandle(handle)


class FrozenCompanionRuntimeContract(unittest.TestCase):
    """Actual-EXE TEST release fixtures, never art or production qualification."""

    def setUp(self):
        test_root = PRODUCT / "build/tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="TEST-runtime-", dir=test_root)
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src/main.cpp").write_text("TEST root marker", encoding="utf-8")
        scripts = self.root / "scripts"
        scripts.mkdir()
        for leaf in ("logical_alpha.py", "review_native_batch.py", "workspace.py"):
            (scripts / leaf).write_text("raise RuntimeError('MUTABLE_CHECKOUT_EXECUTED')\n", encoding="utf-8")
        # Bind the receipt to the exact tested candidate, including isolated builds.
        self.build = json.loads((EXE.parent / "build-receipt.json").read_bytes())
        binding = self.build["companion_runtime"]
        self.payload = self.root / binding["directory"]
        shutil.copytree(PRODUCT / binding["directory"], self.payload)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.exe = self.dist / "H3TierExport.exe"
        shutil.copy2(EXE, self.exe)
        self.exe_sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.build["executable"]["sha256"] = self.exe_sha
        self.write_release()

    def tearDown(self):
        self.temp.cleanup()

    def write_release(self, *, exit_code=0):
        def encode(doc):
            return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        build_path = self.dist / "H3TierExport-build-receipt.json"
        build_path.write_bytes(encode(self.build))
        build_sha = hashlib.sha256(build_path.read_bytes()).hexdigest()
        tests = {"schema": "h3.tier-export.test-suite.v2", "passed": True, "source_current": True,
                 "exe_sha256": self.exe_sha, "build_receipt_sha256": build_sha,
                 "TEST_FIXTURE_ONLY_NOT_QUALIFICATION": True,
                 "results": [{"name": "TEST_ONLY", "exit_code": exit_code}]}
        tests_path = self.dist / "H3TierExport-tests-receipt.json"
        tests_path.write_bytes(encode(tests))
        self.release = {"schema": "h3.tier-export.release.v2", "exe_sha256": self.exe_sha,
                        "build_receipt_sha256": build_sha,
                        "test_receipt_sha256": hashlib.sha256(tests_path.read_bytes()).hexdigest(),
                        "companion_runtime": self.build["companion_runtime"],
                        "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                  for p in (self.exe, build_path, tests_path)]}
        self.write_release_document()

    def write_release_document(self):
        raw = (json.dumps(self.release, indent=2) + "\n").encode()
        (self.dist / "H3TierExport-release.json").write_bytes(raw)
        mirror = self.root / "reports/current/release.json"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(raw)

    def invoke(self, *args, exe=None):
        return subprocess.run([str(exe or self.exe), *args, "--python", sys.executable],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

    def assert_blocked(self, text):
        result = self.invoke("logical-alpha", "--help")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        error = json.loads(result.stderr)
        self.assertEqual(error["code"], "RELEASE_RUNTIME_INVALID")
        self.assertIn(text, error["error"])
        self.assertNotIn("MUTABLE_CHECKOUT_EXECUTED", result.stdout + result.stderr)
        self.assertFalse(list(self.root.rglob("*.png")))

    def test_formal_dispatch_executes_qualified_payload_without_live_scripts(self):
        shutil.rmtree(self.root / "scripts")
        (self.root / "src/main.cpp").unlink()
        result = self.invoke("logical-alpha", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Produce receipt-bound 3X logical-alpha", result.stdout)
        cap = subprocess.run([str(self.exe), "capabilities"], capture_output=True, check=True)
        directory = json.loads(cap.stdout)
        frozen = {r["name"] for r in directory["companion_commands"] if r["qualified_runtime_payload"]}
        self.assertEqual(frozen, ACTIVE_COMPANIONS)
        self.assertEqual(directory["companion_runtime_manifest_sha256"], self.build["companion_runtime"]["manifest_sha256"])

    def test_portable_dist_folder_runs_from_its_adjacent_frozen_payload(self):
        shutil.copytree(self.payload,self.dist/self.build["companion_runtime"]["directory"])
        shutil.rmtree(self.payload)
        shutil.rmtree(self.root/"scripts")
        shutil.rmtree(self.root/"src")
        self.release["files"]=[{**row,"path":Path(row["path"]).name} for row in self.release["files"]]
        self.write_release_document()
        result=self.invoke("logical-alpha","--help")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("Produce receipt-bound 3X logical-alpha",result.stdout)
        self.assertNotIn("MUTABLE_CHECKOUT_EXECUTED",result.stdout+result.stderr)
        smoke=subprocess.run([str(self.exe),"ui-smoke"],capture_output=True,text=True,encoding="utf-8",timeout=45)
        self.assertEqual(smoke.returncode,0,smoke.stderr)
        self.assertTrue(json.loads(smoke.stdout)["task_root_resolved"])

    def test_all_active_actual_handlers_enter_without_live_checkout_and_never_write_outputs(self):
        shutil.rmtree(self.root / "scripts")
        shutil.rmtree(self.root / "src")
        before = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        def enter_handler(name):
            # Independent, read-only actual handlers; never --help or run/submit.
            args = (("catalog", "--json") if name == "art-pipeline" else
                    ("catalog",) if name == "cloud-animation" else
                    ("check",) if name == "logical-alpha" else ())
            return name, self.invoke(name, *args)

        # Keep the existing qualification deadline while checking every handler.
        # Shared payload readers remain pinned; none of these calls writes output.
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(enter_handler, sorted(ACTIVE_COMPANIONS)))
        for name, result in results:
            with self.subTest(command=name):
                if name in ("art-pipeline", "cloud-animation"):
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIsInstance(json.loads(result.stdout), dict)
                else:
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("usage:", result.stderr.lower())
                    self.assertIn("required", result.stderr.lower())
                for forbidden in ("MUTABLE_CHECKOUT_EXECUTED", "RELEASE_RUNTIME_INVALID", "PYTHON_MODULE_MISSING", "ModuleNotFoundError", "Traceback"):
                    self.assertNotIn(forbidden, result.stdout + result.stderr)
        after = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(list(self.root.rglob("*.png")))

    def test_nonimport_worker_and_provider_files_are_part_of_native_gate(self):
        manifest = json.loads((self.payload / "runtime-manifest.json").read_bytes())
        self.assertEqual(manifest["schema"], "h3.tier-export.companion-runtime.v2")
        provider_rows = [row for row in manifest["files"] if row["role"] == "provider-source"]
        self.assertGreater(len(provider_rows), 100)
        worker = self.payload / manifest["resources"]["blender-worker"]["path"]
        original = worker.read_bytes()
        worker.unlink()
        self.assert_blocked("RUNTIME_MISSING_OR_REPARSE")
        worker.write_bytes(original)
        provider = self.payload / provider_rows[0]["path"]
        original = provider.read_bytes()
        provider.write_bytes(original + b"\n# TEST provider drift\n")
        self.assert_blocked("RUNTIME_FILE_DRIFT")

    def test_flag_pole_reference_is_frozen_as_read_only_data(self):
        manifest = json.loads((self.payload / "runtime-manifest.json").read_bytes())
        resource = manifest["resources"]["flag-pole-reference"]
        self.assertEqual(resource["role"], "reference-data")
        self.assertFalse(resource["tree"])
        self.assertEqual(resource["path"], "scripts/data/flag_pole_reference_1999.json.gz")
        row = next(value for value in manifest["files"] if value["path"] == resource["path"])
        self.assertEqual(row["role"], "reference-data")
        table = json.loads(gzip.decompress((self.payload / resource["path"]).read_bytes()))
        ids = {item["identity"] for item in table["entries"]}
        self.assertEqual(len(ids), 2565)
        self.assertEqual(table["counts"]["mine_identities"], 49)
        self.assertEqual(table["counts"]["mine_logical_frames"], 474)
        self.assertEqual(table["provenance"]["archive_sha256"],
                         "57caf2c50573f33a0d91e4222e51d3a73c136d44decf59dde21cacad88fe5d66")
        self.assertTrue({"AVMGOLD0", "AVGMINO0", "AVXSHYD0", "AVCHFORX", "AVMABMG"} <= ids)

    def test_reference_data_rejects_unapproved_extension(self):
        manifest = json.loads((self.payload / "runtime-manifest.json").read_bytes())
        bad = copy.deepcopy(manifest)
        resource = bad["resources"]["flag-pole-reference"]
        resource["path"] = resource["source_path"] = "scripts/data/not-a-json.zip"
        spec = importlib.util.spec_from_file_location("companion_runtime_contract", PRODUCT / "scripts" / "companion_runtime.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(RuntimeError, "RUNTIME_RESOURCE_ROLE_INVALID"):
            module._manifest_rows(bad)

    def test_nested_unlisted_file_and_empty_directory_are_rejected(self):
        root = self.payload / "providers/h3-art-pipeline"
        extra = root / "TEST-unqualified.txt"
        extra.write_text("TEST extra resource", encoding="utf-8")
        self.assert_blocked("RUNTIME_EXTRA_FILE")
        extra.unlink()
        (root / "TEST-empty-directory").mkdir()
        self.assert_blocked("RUNTIME_EXTRA_FILE")

    def test_nested_provider_junction_is_rejected(self):
        import _winapi
        provider = self.payload / "providers/h3-art-pipeline"
        renamed = self.payload / "TEST-real-provider"
        provider.rename(renamed)
        try:
            _winapi.CreateJunction(str(renamed), str(provider))
            self.assert_blocked("RUNTIME_MISSING_OR_REPARSE")
        finally:
            if provider.exists():
                provider.rmdir()
            renamed.rename(provider)

    def test_missing_payload_module_refuses_without_live_fallback(self):
        (self.payload / "logical_alpha.py").unlink()
        self.assert_blocked("RUNTIME_MISSING_OR_REPARSE")

    def test_modified_payload_module_refuses_without_live_fallback(self):
        path = self.payload / "workspace.py"
        path.write_bytes(path.read_bytes() + b"\n# TEST drift\n")
        self.assert_blocked("RUNTIME_FILE_DRIFT")

    def test_manifest_and_extra_import_surface_are_rejected(self):
        path = self.payload / "runtime-manifest.json"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        self.assert_blocked("RUNTIME_MANIFEST_DRIFT")
        path.write_bytes(original)
        (self.payload / "unqualified.py").write_bytes(b"# TEST extra import surface\n")
        self.assert_blocked("RUNTIME_EXTRA_FILE")

    def test_partial_publication_and_mixed_receipts_refuse(self):
        tests_path = self.dist / "H3TierExport-tests-receipt.json"
        original = tests_path.read_bytes()
        tests_path.write_bytes(original + b"\n")
        self.assert_blocked("RUNTIME_FROZEN_RECEIPT_DRIFT")
        tests_path.write_bytes(original)
        recovered = self.invoke("logical-alpha", "--help")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.release["exe_sha256"] = "0" * 64
        self.write_release_document()
        self.assert_blocked("RELEASE_EXE_IDENTITY_MISMATCH")

    def test_failed_qualification_and_compiled_payload_substitution_refuse(self):
        self.write_release(exit_code=1)
        self.assert_blocked("RUNTIME_TEST_RESULT_FAILED")
        self.build["companion_runtime"] = dict(self.build["companion_runtime"], manifest_sha256="0" * 64)
        self.write_release()
        self.assert_blocked("RUNTIME_COMPILED_BINDING_MISMATCH")

    def test_explicit_other_tool_root_cannot_substitute_published_payload(self):
        result = self.invoke("logical-alpha", "--help", "--tool-root", str(PRODUCT))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)["code"], "RELEASE_RUNTIME_INVALID")

    def test_archive_uses_its_own_receipts_while_current_is_incomplete(self):
        archive = self.dist / "archive/TEST-original"
        archive.mkdir(parents=True)
        for path in self.dist.iterdir():
            if path.is_file():
                shutil.copy2(path, archive / path.name)
        (self.dist / "H3TierExport-release.json").write_bytes(b'{"TEST_partial_publication":true}')
        result = self.invoke("logical-alpha", "--help", exe=archive / self.exe.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Produce receipt-bound 3X logical-alpha", result.stdout)
        code = ("import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "import review_native_batch as n;print(json.dumps(n._read_build_receipt(sys.argv[3],Path(sys.argv[2]))))")
        readback = subprocess.run([sys.executable, "-B", "-E", "-X", "utf8", "-c", code,
                                  str(self.payload), str(archive / self.exe.name), self.exe_sha],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(readback.returncode, 0, readback.stderr)
        binding = json.loads(readback.stdout)
        self.assertEqual(binding["authority"], "formal_release")
        self.assertEqual(Path(binding["path"]), archive / "H3TierExport-build-receipt.json")

    def test_prune_stale_runtimes_removes_unreferenced_and_preserves_active(self):
        sys.path.insert(0, str(PRODUCT / "scripts"))
        from companion_runtime import active_runtime_directories, prune_stale_runtimes

        active_dir = self.root / ".runtime-active111111111111111111111111111111111111111111111111111111111111"
        active_dir.mkdir(exist_ok=True)
        stale_dir = self.root / ".runtime-stale222222222222222222222222222222222222222222222222222222222222"
        stale_dir.mkdir(exist_ok=True)
        (stale_dir / "dummy.txt").write_text("temporary stale payload", encoding="utf-8")

        self.release["companion_runtime"] = {"directory": active_dir.name}
        self.write_release_document()

        active = active_runtime_directories(self.root)
        self.assertIn(active_dir.name, active)
        self.assertNotIn(stale_dir.name, active)

        result = prune_stale_runtimes(self.root)
        self.assertIn(stale_dir.name, result["pruned"])
        self.assertFalse(stale_dir.exists())
        self.assertTrue(active_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
