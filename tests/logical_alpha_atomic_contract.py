"""Real native publication tests on an exact executable/source fixture, never game data.

Only fixture PNGs/contracts are mutated. The original producer's 51-frame
contract supplies geometry; a bounded subset exercises the native transaction.
"""
from __future__ import annotations
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'scripts'))
sys.path.insert(0, str(PRODUCT / 'tests'))
import logical_alpha as logical
from logical_alpha_contract import CONTRACT, CONTRACT_SHA, MaterialInputFixture


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _AtomicChildJob:
    """Own gated test Python processes and every native descendant on Windows."""

    _gate = (
        "import sys\n"
        "if sys.stdin.buffer.read(1) != b'R': raise SystemExit('ATOMIC_START_GATE_CLOSED')\n"
        "import runpy\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )

    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Native Windows job ownership is required')
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('minimum_working_set', ctypes.c_size_t),
                        ('maximum_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_operations', 'write_operations', 'other_operations',
                         'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        api.SetHandleInformation.restype = wintypes.BOOL
        api.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetHandleInformation.restype = wintypes.BOOL
        api.GetCurrentProcess.argtypes = []
        api.GetCurrentProcess.restype = wintypes.HANDLE
        api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        api.IsProcessInJob.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self._ctypes, self._api, self._handle, self._children = ctypes, api, None, []
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.SetHandleInformation(handle, 1, 0):  # HANDLE_FLAG_INHERIT
                raise ctypes.WinError(ctypes.get_last_error())
            flags, inside = wintypes.DWORD(), wintypes.BOOL()
            if not api.GetHandleInformation(handle, ctypes.byref(flags)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.IsProcessInJob(api.GetCurrentProcess(), handle, ctypes.byref(inside)):
                raise ctypes.WinError(ctypes.get_last_error())
            if flags.value & 1 or inside.value:
                raise RuntimeError('ATOMIC_JOB_MUST_EXCLUDE_CONTROLLER_AND_HANDLE_INHERITANCE')
            self._handle = handle
        except BaseException:
            api.CloseHandle(handle)
            raise

    def start_script(self, script, arguments, *, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
        """Do not execute the target script until its Python process joins our job."""
        if self._handle is None:
            raise RuntimeError('ATOMIC_CHILD_JOB_CLOSED')
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, '-B', '-X', 'utf8', '-c', self._gate,
                 str(Path(script).resolve(strict=True)), *arguments],
                stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, close_fds=True)
            self._children.append(process)
            if not self._api.AssignProcessToJobObject(self._handle, int(process._handle)):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            if process.stdin.write(b'R') != 1:
                raise RuntimeError('ATOMIC_START_GATE_WRITE_FAILED')
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            return process
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill()  # An unassigned process is still waiting at the gate.
            self.close()
            raise

    def close(self):
        handle, self._handle = self._handle, None
        error = None
        if handle is not None and not self._api.CloseHandle(handle):
            error = self._ctypes.WinError(self._ctypes.get_last_error())
        for process in self._children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                error = error or RuntimeError('ATOMIC_JOB_CHILD_DID_NOT_EXIT_ON_CLOSE')
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _run_owned_test_classes(script, class_names):
    # Each class retains its own sequential fixtures and original native timeouts.
    with contextlib.ExitStack() as captures, _AtomicChildJob() as job:
        children = []
        for name in class_names:
            stdout = captures.enter_context(tempfile.TemporaryFile())
            stderr = captures.enter_context(tempfile.TemporaryFile())
            process = job.start_script(script, [name], stdout=stdout, stderr=stderr)
            children.append((name, process, stdout, stderr))
        for _, process, _, _ in children:
            process.wait()
        for name, process, stdout, stderr in children:
            print(f'{name} exit {process.returncode}', flush=True)
            stdout.seek(0)
            stderr.seek(0)
            sys.stdout.write(stdout.read().decode('utf-8', 'replace'))
            sys.stderr.write(stderr.read().decode('utf-8', 'replace'))
        sys.stdout.flush()
        sys.stderr.flush()
        return 0 if all(process.returncode == 0 for _, process, _, _ in children) else 1


def _run_atomic_classes():
    return _run_owned_test_classes(__file__, ('LogicalAlphaAtomicContract', 'MaterialAtomicContract'))


class LogicalAlphaAtomicContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise RuntimeError('Native Windows transaction qualification is required')
        cls.base = tempfile.TemporaryDirectory(prefix='logical-atomic-', dir=PRODUCT / 'build' / 'experiments')
        cls.root = Path(cls.base.name)
        cls.product = cls.root / 'product'
        receipt = json.loads((PRODUCT / 'build/candidate/build-receipt.json').read_bytes())
        cls.exe = cls.product / 'build/candidate/H3TierExport.exe'
        cls.exe.parent.mkdir(parents=True)
        src_exe = Path(os.environ.get('H3TIEREXPORT_EXE', str(PRODUCT / 'build/candidate/H3TierExport.exe')))
        if digest(src_exe) != receipt['executable']['sha256']:
            raise RuntimeError('EXACT_TESTED_EXE_REQUIRED')
        shutil.copyfile(src_exe, cls.exe)
        # This is a read-only runtime fixture, not another build/source owner.
        for row in receipt['sources']:
            src = PRODUCT / row['path']
            raw = src.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise RuntimeError('FIXTURE_SOURCE_DRIFT:' + row['path'])
            dst = cls.product / row['path']
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(raw)
        (cls.product / '.gitignore').write_text('build/\n', encoding='ascii')
        (cls.exe.parent / 'build-receipt.json').write_bytes((PRODUCT / 'build/candidate/build-receipt.json').read_bytes())
        cls.batch = logical.validate_contract(CONTRACT, CONTRACT_SHA, ['DIRTRD'])
        cls.outputs = cls.product / 'build/experiments/logical-alpha'
        cls.outputs.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        cls.base.cleanup()

    def setUp(self):
        self.id = 'atomic-' + self._testMethodName.replace('test_', '')
        self.final = self.outputs / self.id
        self.stage = self.outputs / ('.' + self.id + '.staging')
        self.inputs = self.root / self.id
        self.inputs.mkdir()
        self.batch = copy.deepcopy(type(self).batch)
        contract = self.batch['contract']
        by_key = {tuple(r['key']): r for r in contract['frames']}
        for frame in self.batch['frames']:
            src = self.inputs / (frame['id'] + '.png')
            shutil.copyfile(frame['source'], src)
            frame['source'] = src
            record = by_key[tuple(frame['key'])]['input_crop_png']
            record['archive_path'] = str(src)
            record['archive_relative_path'] = src.name
        contract['archive_root'] = str(self.inputs)
        self.contract = self.inputs / 'contract.json'
        self.contract.write_text(json.dumps(contract, ensure_ascii=False), encoding='utf-8')
        self.batch['manifest_path'] = self.contract
        self.batch['manifest_sha256'] = digest(self.contract)
        self.spec = self.inputs / 'spec.json'
        self.spec.write_text(json.dumps(logical._native_spec(self.batch, self.id), ensure_ascii=False), encoding='utf-8')
        self.argv = [str(self.exe), 'logical-alpha-render', '--spec', str(self.spec), '--spec-sha256', digest(self.spec),
                     '--outdir', str(self.final), '--operation-id', self.id]

    def execute(self):
        return subprocess.run(self.argv, capture_output=True, timeout=90)

    def during_output(self, effect):
        proc = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        observed = False
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and proc.poll() is None:
                if self.stage.exists() or self.final.exists():
                    observed = True
                    effect(proc)
                    break
                time.sleep(0.002)
            stdout, stderr = proc.communicate(timeout=90)
            self.assertTrue(observed, (stdout + stderr).decode('utf-8', 'replace')[-2500:])
            return proc.returncode, stdout, stderr
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    def test_interruption_never_exposes_final_and_same_id_is_not_replayed(self):
        self.during_output(lambda p: p.kill())
        self.assertFalse(self.final.exists(), 'Final operation directory was exposed before the complete batch')
        self.assertTrue(self.stage.exists(), 'Interrupted work must retain its own staging evidence')
        retry = self.execute()
        self.assertNotEqual(retry.returncode, 0)
        self.assertFalse(self.final.exists())

    def test_source_drift_before_seal_is_rejected(self):
        source = self.batch['frames'][0]['source']
        code, _, _ = self.during_output(lambda p: source.write_bytes(source.read_bytes() + b'changed'))
        self.assertNotEqual(code, 0)
        self.assertFalse(self.final.exists())

    def test_contract_drift_before_seal_is_rejected(self):
        code, _, _ = self.during_output(lambda p: self.contract.write_bytes(self.contract.read_bytes() + b' '))
        self.assertNotEqual(code, 0)
        self.assertFalse(self.final.exists())

    def test_build_drift_before_seal_is_rejected(self):
        build = self.exe.parent / 'build-receipt.json'
        original = build.read_bytes()
        try:
            code, _, _ = self.during_output(lambda p: build.write_bytes(original + b' '))
            self.assertNotEqual(code, 0)
            self.assertFalse(self.final.exists())
        finally:
            build.write_bytes(original)

    def test_existing_target_is_never_overwritten(self):
        self.final.mkdir()
        marker = self.final / 'foreign-marker.txt'
        marker.write_bytes(b'not owned by this renderer')
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_bytes(), b'not owned by this renderer')

    def test_late_target_collision_retains_complete_staging(self):
        def collide(proc):
            self.final.mkdir(exist_ok=True)
            (self.final / 'foreign-marker.txt').write_bytes(b'other operation')
        code, _, _ = self.during_output(collide)
        self.assertNotEqual(code, 0)
        self.assertEqual((self.final / 'foreign-marker.txt').read_bytes(), b'other operation')
        self.assertFalse((self.final / 'logical-alpha-completion.json').exists())

    def test_success_publishes_all_pngs_and_completion_together(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertFalse(self.stage.exists())
        receipt = json.loads((self.final / logical.COMPLETION_NAME).read_bytes())
        self.assertEqual(receipt['publication']['mode'], 'same-volume-no-overwrite-directory-rename')
        self.assertEqual(receipt['publication']['input_rechecked_before_seal'], True)
        self.assertEqual(receipt['frame_count'], 17)
        self.assertEqual(len(list(self.final.rglob('*.png'))), 34)
        for frame in receipt['frames']:
            for output in frame['outputs'].values():
                self.assertEqual(digest(self.final / output['path']), output['sha256'])
        self.assertFalse(receipt['art_approved'])
        self.assertFalse(receipt['game_deployed'])


class MaterialNativeSupport:
    """Reuse the existing exact-source runtime fixture and transaction observer."""

    # Atomic failures need one complete family; the pixel contract explicitly
    # reserves a full 51-slot case for cross-family composition and replay.
    all_family_cases = frozenset()

    @classmethod
    def setUpClass(cls):
        LogicalAlphaAtomicContract.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        LogicalAlphaAtomicContract.tearDownClass.__func__(cls)

    def setUp(self):
        self.fixture = MaterialInputFixture()
        self.addCleanup(self.fixture.close)
        self.id = 'material-' + hashlib.sha256(self._testMethodName.encode()).hexdigest()[:18]
        self.final = self.outputs / self.id
        self.stage = self.outputs / ('.' + self.id + '.staging')
        families = None if self._testMethodName in self.all_family_cases else ["DIRTRD"]
        self.batch = self.fixture.batch(families)
        self.contract = self.fixture.manifest_path
        self.spec = self.fixture.root / 'native-spec.json'
        self.spec.write_bytes(logical._native_spec_bytes(self.batch, self.id))
        self.argv = [str(self.exe), 'logical-alpha-render', '--spec', str(self.spec), '--spec-sha256', digest(self.spec),
                     '--outdir', str(self.final), '--operation-id', self.id]

    execute = LogicalAlphaAtomicContract.execute
    during_output = LogicalAlphaAtomicContract.during_output


class MaterialAtomicContract(MaterialNativeSupport, unittest.TestCase):
    def test_interruption_preserves_staging_and_never_exposes_partial_materials(self):
        self.during_output(lambda process: process.kill())
        self.assertFalse(self.final.exists())
        self.assertTrue(self.stage.exists())
        retry = self.execute()
        self.assertNotEqual(retry.returncode, 0)
        self.assertFalse(self.final.exists())

    def test_current_producer_drift_before_seal_blocks_the_material_batch(self):
        source = self.fixture.map_source
        code, _, stderr = self.during_output(lambda process: source.write_bytes(b'!' + source.read_bytes()[1:]))
        self.assertNotEqual(code, 0)
        self.assertFalse(self.final.exists())
        self.assertIn(b'MATERIAL_INPUT_CHANGED_BEFORE_SEAL', stderr)

    def test_late_collision_keeps_foreign_target_and_candidate_staging(self):
        def collide(process):
            self.final.mkdir(exist_ok=True)
            (self.final / 'foreign-marker.txt').write_bytes(b'foreign operation')
        code, _, _ = self.during_output(collide)
        self.assertNotEqual(code, 0)
        self.assertEqual((self.final / 'foreign-marker.txt').read_bytes(), b'foreign operation')
        self.assertFalse((self.final / logical.COMPLETION_NAME).exists())
        self.assertTrue(self.stage.exists())


if __name__ == '__main__':
    if len(sys.argv) == 1:
        raise SystemExit(_run_atomic_classes())
    unittest.main(verbosity=2)
