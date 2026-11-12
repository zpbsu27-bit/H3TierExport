"""Additional exact-candidate native evidence: alpha=1 oracle and open-reader rename failure."""
from pathlib import Path
import hashlib,json,sys,ctypes as C,ctypes.wintypes as W,time,unittest,io,copy,os,shutil,tempfile
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from logical_alpha_atomic_contract import LogicalAlphaAtomicContract,MaterialNativeSupport,digest,logical,_run_owned_test_classes
from PIL import Image

class ExtraNativeContract(LogicalAlphaAtomicContract):
    def test_candidate_uncompiled_tool_source_drift_still_blocks_seal(self):
        source = self.product / 'scripts/logical_alpha.py'
        original = source.read_bytes()
        try:
            source.write_bytes(original + b'\n# TEST_ONLY candidate source drift\n')
            process = self.execute()
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b'LOGICAL_ALPHA_SOURCE_CLOSURE_CHANGED_BEFORE_SEAL', process.stderr)
            self.assertFalse(self.stage.exists())
            self.assertFalse(self.final.exists())
        finally:
            source.write_bytes(original)

    def test_alpha1_known_pixel_oracle(self):
        frame=next((r for r in self.batch['frames'] if r['geometry_1x']['origin'][0]!=r['geometry_1x']['origin'][1] or r['source_pixels'][0]!=r['source_pixels'][1]),self.batch['frames'][0])
        w,h=frame['source_pixels'];rawio=io.BytesIO();Image.new('RGBA',(w,h),(255,255,255,1)).save(rawio,format='PNG');raw=rawio.getvalue()
        frame['source'].write_bytes(raw);frame['source_sha256']=hashlib.sha256(raw).hexdigest();frame['alpha_sha256']=hashlib.sha256(bytes([1])*(w*h)).hexdigest();frame['source_positive_pixels']=w*h
        frame['source_rgb_encoding']={'all_rgb_pixels_white':True,'channel_extrema':[[255,255]]*3,'classification':'RGBA_RGB_ALL_WHITE_ALPHA_MASK','visible_rgb_unique_count':1}
        record=next(r for r in self.batch['contract']['frames'] if r['key']==frame['key'])
        record['input_crop_png'].update(sha256=frame['source_sha256'],bytes=len(raw),alpha_plane_sha256=frame['alpha_sha256'],alpha_positive_pixels_pil_verified=w*h,rgb_encoding=frame['source_rgb_encoding'])
        self.contract.write_text(json.dumps(self.batch['contract'],ensure_ascii=False),encoding='utf-8');self.batch['manifest_sha256']=digest(self.contract)
        self.spec.write_text(json.dumps(logical._native_spec(self.batch,self.id),ensure_ascii=False),encoding='utf-8');self.argv[self.argv.index('--spec-sha256')+1]=digest(self.spec)
        result=self.execute();self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        receipt=json.loads((self.final/logical.COMPLETION_NAME).read_bytes());item=next(r for r in receipt['frames'] if r['key']==frame['key'])
        crop=self.final/item['outputs']['crop_3x']['path'];canvas=self.final/item['outputs']['canvas_3x']['path']
        with Image.open(crop) as im:self.assertEqual(im.tobytes(),bytes((255,255,255,1))*(w*h*9))
        cw,ch=frame['geometry_3x']['canvas'];ox,oy=frame['geometry_3x']['origin'];expected=Image.new('RGBA',(cw,ch),(255,255,255,0));expected.paste(Image.new('RGBA',(w*3,h*3),(255,255,255,1)),(ox,oy))
        with Image.open(canvas) as im:self.assertEqual(im.tobytes(),expected.tobytes())

    def test_open_child_reader_prevents_publish_without_partial_final(self):
        k=C.WinDLL('kernel32',use_last_error=True);k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE];k.CreateFileW.restype=W.HANDLE;k.CloseHandle.argtypes=[W.HANDLE]
        handles=[]
        def hold(proc):
            deadline=time.monotonic()+45
            while proc.poll() is None and time.monotonic()<deadline:
                for path in self.stage.rglob('crop-3x.png'):
                    handle=k.CreateFileW(str(path),0x80000000,7,None,3,0x00200000,None)
                    if handle not in (None,C.c_void_p(-1).value):handles.append(handle);return
                time.sleep(0.002)
            self.fail('Did not observe a staged native PNG to hold')
        try:
            code,_,stderr=self.during_output(hold)
            self.assertNotEqual(code,0)
            self.assertFalse(self.final.exists())
            self.assertTrue((self.stage/logical.COMPLETION_NAME).exists())
            self.assertIn(b'REVIEW_BATCH_ATOMIC_RENAME_FAILED',stderr)
        finally:
            for handle in handles:k.CloseHandle(handle)
        retry=self.execute();self.assertNotEqual(retry.returncode,0);self.assertFalse(self.final.exists())

class MaterialNativePixelsContract(MaterialNativeSupport, unittest.TestCase):
    all_family_cases = frozenset({"test_material_pixels_alpha1_single_crop_virtual_origin_and_verified_replay"})

    def test_material_pixels_alpha1_single_crop_virtual_origin_and_verified_replay(self):
        process = self.execute()
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        receipt = json.loads((self.final / logical.COMPLETION_NAME).read_bytes())
        self.assertEqual(receipt['data_role'], 'road-material-3x')
        self.assertEqual(receipt['algorithm']['resampler_count'], 0)
        self.assertTrue(receipt['candidate_generation_performed'])
        self.assertFalse(receipt['new_art_created'])
        self.assertFalse(receipt['art_approved'])
        self.assertEqual(len(list(self.final.rglob('*.png'))), 51)
        expected = {tuple(frame['key']): frame for frame in self.batch['frames']}
        alpha1 = 0
        for row in receipt['frames']:
            frame = expected[tuple(row['key'])]
            self.assertEqual(set(row['outputs']), {'crop_3x'})
            output = row['outputs']['crop_3x']
            self.assertEqual(output['layout'], 'native-crop')
            self.assertEqual(output['canvas_pixels'], [96, 96])
            self.assertEqual(output['origin_pixels'], frame['geometry_3x']['origin'])
            with Image.open(self.final / output['path']) as image:
                self.assertEqual(image.tobytes(), frame['material_expected_rgba'])
                alpha1 += image.tobytes()[3::4].count(1)
        self.assertGreater(alpha1, 0)
        before = {path: digest(path) for path in self.final.rglob('*') if path.is_file()}
        real_run = logical.subprocess.run

        def only_readonly_renderer_calls(args, **kwargs):
            self.assertNotEqual(args[1], 'logical-alpha-render', 'verified replay must not dispatch a second render')
            return real_run(args, **kwargs)

        with self.fixture.activated(), mock.patch.object(logical, 'OUTPUT_BASE', self.outputs), \
                mock.patch.object(logical.subprocess, 'run', side_effect=only_readonly_renderer_calls):
            replay = logical.run(self.contract, digest(self.contract), self.final, self.id, None, self.exe, digest(self.exe))
        self.assertEqual(replay['status'], 'REUSED_VERIFIED')
        self.assertEqual({path: digest(path) for path in before}, before)

    def test_forged_material_png_and_receipt_hash_cannot_replace_bound_rgb(self):
        process = self.execute()
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        path = self.final / logical.COMPLETION_NAME
        receipt = json.loads(path.read_bytes())
        output = receipt['frames'][0]['outputs']['crop_3x']
        png = self.final / output['path']
        with Image.open(png) as source:
            image = source.copy()
        position = next((x, y) for y in range(image.height) for x in range(image.width)
                        if image.getpixel((x, y))[3] > 0)
        r, g, b, a = image.getpixel(position)
        image.putpixel(position, (r ^ 1, g, b, a))
        image.save(png, format='PNG')
        output.update(sha256=digest(png), bytes=png.stat().st_size)
        path.write_bytes((json.dumps(receipt, ensure_ascii=False) + '\n').encode('utf-8'))
        renderer = {'path': str(self.exe), 'sha256': digest(self.exe),
                    'build_receipt': {'sha256': digest(self.exe.parent / 'build-receipt.json')}}
        with mock.patch.object(logical, 'OUTPUT_BASE', self.outputs):
            with self.assertRaisesRegex(logical.LogicalAlphaError, 'material RGB/alpha bytes differ'):
                logical._verify_operation(self.batch, renderer, self.final, self.id)

    def test_native_family_cell17_source_mask_and_geometry_reject_before_output(self):
        original = logical._native_spec(self.batch, self.id)
        cases = [('family', lambda row: row['key'].__setitem__(1, 'WRONG'), b'LOGICAL_ALPHA_FRAME_ID_KEY_MISMATCH'),
                 ('cell17', lambda row: row['material'].update(cell_3x=[480, 192, 96, 96]), b'MATERIAL_FRAME_BINDING_MISMATCH'),
                 ('source', lambda row: row.update(source_sha256='0' * 64), b'LOGICAL_ALPHA_FRAME_SOURCE_BINDING_MISMATCH'),
                 ('mask', lambda row: row['material']['mask_crop_3x'].update(sha256='0' * 64), b'MATERIAL_FRAME_BINDING_MISMATCH'),
                 ('geometry', lambda row: row['geometry_3x']['origin'].__setitem__(0, 1), b'LOGICAL_ALPHA_GEOMETRY_NOT_EXACTLY_3X')]
        for name, mutation, error in cases:
            with self.subTest(case=name):
                spec = copy.deepcopy(original)
                mutation(spec['frames'][0])
                self.spec.write_bytes((json.dumps(spec, ensure_ascii=False) + '\n').encode('utf-8'))
                self.argv[self.argv.index('--spec-sha256') + 1] = digest(self.spec)
                process = self.execute()
                self.assertNotEqual(process.returncode, 0)
                self.assertIn(error, process.stderr)
                self.assertFalse(self.final.exists())
                self.assertFalse(self.stage.exists())


    def test_current_wire_cannot_admit_unbound_same_bytes_file(self):
        spec = logical._native_spec(self.batch, self.id)
        pins = spec['material_inputs']['current_files']
        original = copy.deepcopy(pins[0])
        foreign = self.fixture.root / 'foreign-same-bytes-input'
        shutil.copyfile(original['path'], foreign)
        self.assertEqual(digest(foreign), original['sha256'])
        pins[0] = {**original, 'path': str(foreign)}
        self.spec.write_bytes((json.dumps(spec, ensure_ascii=False) + '\n').encode('utf-8'))
        self.argv[self.argv.index('--spec-sha256') + 1] = digest(self.spec)
        process = self.execute()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b'MATERIAL_CURRENT_FILES_MISMATCH', process.stderr)
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.final.exists())
        self.assertEqual(digest(foreign), original['sha256'])
        self.assertEqual(digest(original['path']), original['sha256'])


class FormalMaterialNativeContract(MaterialNativeSupport, unittest.TestCase):
    """Actual native child without a Tool checkout; sidecars are TEST fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory(prefix='formal-logical-native-', dir=ROOT / 'build/experiments')
        cls.root = Path(cls.base.name)
        cls.product = cls.root / 'product'
        cls.exe = cls.product / 'dist/H3TierExport.exe'
        cls.exe.parent.mkdir(parents=True)
        source_exe = Path(os.environ.get('H3TIEREXPORT_EXE', ROOT / 'build/candidate/H3TierExport.exe'))
        build_raw = (ROOT / 'build/candidate/build-receipt.json').read_bytes()
        build_sha = hashlib.sha256(build_raw).hexdigest()
        if json.loads(build_raw)['executable']['sha256'] != digest(source_exe):
            raise RuntimeError('EXACT_TESTED_EXE_REQUIRED')
        shutil.copyfile(source_exe, cls.exe)
        tests_raw = (json.dumps({'schema': 'h3.tier-export.test-suite.v2', 'passed': True,
                                'source_current': True, 'exe_sha256': digest(cls.exe),
                                'build_receipt_sha256': build_sha,
                                'results': [{'name': 'TEST_ONLY synthetic sidecars', 'exit_code': 0}]}) + '\n').encode()
        cls.frozen = {'H3TierExport-build-receipt.json': build_raw,
                      'H3TierExport-tests-receipt.json': tests_raw}
        release = {'schema': 'h3.tier-export.release.v2', 'exe_sha256': digest(cls.exe),
                   'build_receipt_sha256': build_sha,
                   'test_receipt_sha256': hashlib.sha256(tests_raw).hexdigest(),
                   'files': [{'path': str(cls.exe), 'sha256': digest(cls.exe)}] +
                            [{'path': str(cls.exe.parent / name), 'sha256': hashlib.sha256(raw).hexdigest()}
                             for name, raw in cls.frozen.items()]}
        cls.frozen['H3TierExport-release.json'] = (json.dumps(release) + '\n').encode()
        cls.outputs = cls.product / 'build/experiments/logical-alpha'
        cls.outputs.mkdir(parents=True)

    def setUp(self):
        MaterialNativeSupport.setUp(self)
        for name, raw in self.frozen.items():
            (self.exe.parent / name).write_bytes(raw)
        self.mirror = self.product / 'reports/current/release.json'
        self.mirror.parent.mkdir(parents=True, exist_ok=True)
        self.mirror.write_bytes(self.frozen['H3TierExport-release.json'])

    def assert_material_pixels(self):
        receipt = json.loads((self.final / logical.COMPLETION_NAME).read_bytes())
        self.assertEqual(receipt['frame_count'], 17)
        self.assertEqual(len(list(self.final.rglob('*.png'))), 17)
        expected = {tuple(row['key']): row for row in self.batch['frames']}
        for row in receipt['frames']:
            self.assertEqual(set(row['outputs']), {'crop_3x'})
            with Image.open(self.final / row['outputs']['crop_3x']['path']) as image:
                self.assertEqual(image.tobytes(), expected[tuple(row['key'])]['material_expected_rgba'])
        return receipt

    def test_formal_native_material_has_no_dependency_on_tool_checkout(self):
        self.assertFalse((self.product / 'src').exists())
        self.assertFalse((self.product / 'scripts').exists())
        self.assertFalse((self.product / '.gitignore').exists())
        process = self.execute()
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        receipt = self.assert_material_pixels()
        self.assertEqual(Path(receipt['renderer']['build_binding']['build_receipt']['path']),
                         self.exe.parent / 'H3TierExport-build-receipt.json')

    def test_explicit_archive_material_ignores_unrelated_current_sidecars(self):
        archive = self.exe.parent / 'archive' / self.id
        archive.mkdir(parents=True)
        archived_exe = archive / self.exe.name
        shutil.copyfile(self.exe, archived_exe)
        for name, raw in self.frozen.items():
            (archive / name).write_bytes(raw)
        self.argv[0] = str(archived_exe)
        (self.exe.parent / 'H3TierExport-tests-receipt.json').write_bytes(b'unrelated next release')
        self.mirror.write_bytes(b'unrelated next release')
        process = self.execute()
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        receipt = self.assert_material_pixels()
        binding = receipt['renderer']['build_binding']
        self.assertEqual(Path(binding['build_receipt']['path']), archive / 'H3TierExport-build-receipt.json')
        self.assertEqual(Path(binding['test_receipt']['path']), archive / 'H3TierExport-tests-receipt.json')

    def test_formal_material_still_rejects_external_producer_drift(self):
        source = self.fixture.map_source
        code, _, stderr = self.during_output(lambda process: source.write_bytes(b'!' + source.read_bytes()[1:]))
        self.assertNotEqual(code, 0)
        self.assertIn(b'MATERIAL_INPUT_CHANGED_BEFORE_SEAL', stderr)
        self.assertFalse(self.final.exists())


if __name__=='__main__':
    classes = ('ExtraNativeContract', 'MaterialNativePixelsContract', 'FormalMaterialNativeContract')
    if len(sys.argv) == 1:
        raise SystemExit(_run_owned_test_classes(__file__, classes))
    if len(sys.argv) != 2 or sys.argv[1] not in classes:
        raise SystemExit('NATIVE_PIXEL_TEST_CLASS_REQUIRED')
    if sys.argv[1] == 'ExtraNativeContract':
        suite=unittest.TestSuite(ExtraNativeContract(name) for name in ['test_alpha1_known_pixel_oracle','test_open_child_reader_prevents_publish_without_partial_final','test_candidate_uncompiled_tool_source_drift_still_blocks_seal'])
    else:
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(globals()[sys.argv[1]])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
