"""Exercise the actual CPU expansion EXE with disposable synthetic PNGs."""
from pathlib import Path
import copy, hashlib, json, subprocess, tempfile, unittest
from PIL import Image, ImageDraw

PRODUCT = Path(__file__).resolve().parents[1]
EXE = PRODUCT / 'build/candidate/H3TierExport.exe'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False)+'\r\n', encoding='utf-8')

class ExpansionContract(unittest.TestCase):
    def setUp(self):
        parent = PRODUCT/'build/tests'; parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='expand-', dir=parent)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root/'source.png'
        im = Image.new('RGBA', (30, 24), (255, 0, 0, 0))
        for y in range(3, 21):
            for x in range(4, 14): im.putpixel((x, y), (90, 160, 240, 160))
        im.save(self.source)
        self.original_sha = sha(self.source)
        self.output = self.root/'output'; self.batch_path = self.root/'batch.json'
        self.call('expand-plan', '--source', self.source, '--source-tier', 3,
                  '--target-scale', 4, '--output-root', self.output, '--out', self.batch_path)
        self.batch = json.loads(self.batch_path.read_bytes())

    def tearDown(self): self.temp.cleanup()

    def call(self, *args, ok=True):
        p = subprocess.run([str(EXE), *map(str,args)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(p.returncode == 0, ok, (p.stdout+p.stderr)[-2000:])
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def run_batch(self, command='expand', ok=True):
        save(self.batch_path, self.batch)
        return self.call(command, '--batch', self.batch_path, '--workers', 2,
                         '--memory-gib', 1, ok=ok)

    def receipt(self): return next((self.output/'receipts').glob('*.json'))

    def test_prepare_is_read_only(self):
        self.assertFalse(self.output.exists()); self.assertEqual(sha(self.source), self.original_sha)

    def test_quality_is_default_and_cpu_only(self):
        self.assertEqual(self.batch['settings']['filter'],'h3-quality')
        self.run_batch()
        r=json.loads(self.receipt().read_bytes())
        self.assertEqual(r['algorithm'],'linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4')
        self.assertEqual(r['backend'],'cpu')

    def test_sealed_algorithm_cannot_change_on_execution(self):
        self.call('expand','--batch',self.batch_path,'--filter','lanczos3',ok=False)
        self.assertFalse(self.output.exists())

    def test_old_v2_batch_is_rejected_before_writes_even_with_current_outputs(self):
        self.assertEqual(self.batch['algorithm'],'linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4')
        old_batch=copy.deepcopy(self.batch)
        old_batch['algorithm']='linear-premultiplied-lanczos3-ewa-alpha-ar-v2'
        old_path=self.root/'sealed-v2-batch.json'
        save(old_path,old_batch);old_bytes=old_path.read_bytes()
        for command in ('expand-check','expand','expand-verify'):
            with self.subTest(command=command,existing_outputs=False):
                result=self.call(command,'--batch',old_path,ok=False)
                self.assertEqual(result['status'],'FAILED')
                self.assertIn('unsupported expansion batch',result['error'])
                self.assertNotIn('REUSED_VERIFIED',json.dumps(result))
                self.assertFalse(self.output.exists())
        self.run_batch()
        before={p.relative_to(self.output).as_posix():p.read_bytes()
                for p in self.output.rglob('*') if p.is_file()}
        for command in ('expand-check','expand','expand-verify'):
            with self.subTest(command=command,existing_outputs=True):
                result=self.call(command,'--batch',old_path,ok=False)
                self.assertEqual(result['status'],'FAILED')
                self.assertIn('unsupported expansion batch',result['error'])
                self.assertNotIn('REUSED_VERIFIED',json.dumps(result))
                self.assertEqual(before,{p.relative_to(self.output).as_posix():p.read_bytes()
                                         for p in self.output.rglob('*') if p.is_file()})
        self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertEqual(sha(self.source),self.original_sha)

    def test_legacy_low_quality_sampler_cannot_be_selected_or_produced(self):
        path=self.root/'legacy-identity-batch.json'
        self.call('expand-plan','--source',self.source,'--source-tier',3,
                  '--target-scale',3,'--output-root',self.output,'--out',path,
                  '--filter','lanczos3',ok=False)
        self.assertFalse(path.exists(),'a refused plan must not leave a batch behind')
        self.assertFalse(self.output.exists(),'a refused plan must not create the output root')
        self.assertEqual(sha(self.source),self.original_sha)

    def test_sealed_legacy_batch_verifies_but_never_writes(self):
        current_path=self.root/'current-batch.json'
        self.call('expand-plan','--source',self.source,'--source-tier',3,
                  '--target-scale',3,'--output-root',self.output,'--out',current_path)
        current=json.loads(current_path.read_bytes())
        self.assertEqual(current['algorithm'],'linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4')
        legacy=copy.deepcopy(current)
        legacy['algorithm']='linear-premultiplied-lanczos3-local-range-v1'
        legacy['settings']['filter']='lanczos3'
        legacy_path=self.root/'sealed-legacy-batch.json'
        save(legacy_path,legacy)
        output=Path(current['items'][0]['output'])
        for command in ('expand-check','expand'):
            with self.subTest(command=command):
                r=self.call(command,'--batch',legacy_path,ok=False)
                self.assertEqual(r['status'],'FAILED')
                self.assertIn('legacy',r['error'])
                self.assertIn('h3-quality',r['error'])
        self.assertFalse(output.exists(),'a legacy batch must never produce bytes')
        self.assertFalse((self.output/'receipts').exists())
        self.assertEqual(sha(self.source),self.original_sha)

    def raw(self, *args):
        p = subprocess.run([str(EXE), *map(str,args)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def framed_master(self, name, box, extra=()):
        """plan a 100x100 master whose artwork occupies `box`; base 25x25 keeps the ratio exact"""
        path = self.root/(name+'.png')
        im = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
        for y in range(box[1], box[3]):
            for x in range(box[0], box[2]):
                im.putpixel((x, y), (120, 70, 40, 255))
        im.save(path)
        out = self.root/(name+'-out'); batch = self.root/(name+'-batch.json')
        self.call('expand-plan', '--source', path, '--base-width', 25, '--base-height', 25,
                  '--target-scale', 3, '--output-root', out, '--out', batch, *extra)
        return path, batch, out

    def test_padded_master_is_refused_before_any_write(self):
        path, batch, out = self.framed_master('padded', (10, 0, 90, 100))
        before = sha(path)
        result = self.raw('expand', '--batch', batch, '--workers', 2, '--memory-gib', 1)
        text = json.dumps(result)
        self.assertIn('padded', text)
        self.assertIn('--allow-padded-master', text)
        self.assertEqual([], [p.name for p in out.rglob('*.png')], 'a padded master must not produce bytes')
        self.assertFalse(any((out/'receipts').glob('*.json')), 'a padded master must not write a receipt')
        self.assertEqual(before, sha(path), 'the master stays byte-identical')

    def delivery_master(self, name='0_10'):
        """A master in an identity folder, mirroring how the asset tree is laid out."""
        folder = self.root/('masters-' + name)/'SECSK32'; folder.mkdir(parents=True, exist_ok=True)
        path = folder/(name + '.png')
        Image.new('RGBA', (30, 24), (255, 0, 0, 0)).save(path)
        return path, folder.parent

    def test_delivery_keeps_the_master_name_and_the_folder_marks_the_tier(self):
        item = Path(self.batch['items'][0]['output'])
        self.assertEqual(item.name, self.source.name,
                         'the 3X copy must carry the master file name; the delivery directory marks the tier')
        self.assertNotIn('_3X', item.name)
        self.run_batch()
        written = sorted(p.relative_to(self.output).as_posix() for p in self.output.rglob('*.png'))
        self.assertEqual(['source.png'], written)
        self.assertEqual(self.original_sha, sha(self.source), 'reading the master must not change it')

    def test_writing_onto_the_master_is_refused(self):
        path, masters = self.delivery_master('beside')
        before = sha(path)
        batch = self.root/'beside-batch.json'
        result = self.call('expand-plan', '--source', path, '--base-width', 10, '--base-height', 8,
                           '--target-scale', 3, '--output-root', path.parent, '--out', batch, ok=False)
        self.assertEqual('FAILED', result['status'])
        self.assertIn('MASTER_EXPAND_OUTPUT_', result['error'])
        self.assertFalse(batch.exists(), 'a refused plan must not leave a batch behind')
        self.assertEqual([], [p.name for p in path.parent.glob('*.png') if p.name != path.name])
        self.assertEqual(before, sha(path), 'the master must survive the refused plan')

    def test_delivery_nested_under_the_master_folder_is_still_allowed(self):
        # Review and working trees legitimately write a subfolder next to their masters;
        # only landing on the master itself is refused.
        path, masters = self.delivery_master('nested')
        out = path.parent/'review-3x'
        batch = self.root/'nested-batch.json'
        self.call('expand-plan', '--source', path, '--base-width', 10, '--base-height', 8,
                  '--target-scale', 3, '--output-root', out, '--out', batch)
        self.assertEqual(Path(json.loads(batch.read_bytes())['items'][0]['output']).name, 'nested.png')
        self.call('expand', '--batch', batch, '--workers', 2, '--memory-gib', 1)
        self.assertTrue((out/'nested.png').is_file())
        self.assertEqual(sha(path), sha(path))

    def test_mirror_delivery_onto_the_master_is_refused(self):
        path, masters = self.delivery_master('mirror')
        before = sha(path)
        batch = self.root/'mirror-batch.json'
        result = self.call('expand-plan', '--source', path, '--base-width', 10, '--base-height', 8,
                           '--target-scale', 3, '--mirror-source-root', masters,
                           '--output-root', masters, '--out', batch, ok=False)
        self.assertEqual('FAILED', result['status'])
        self.assertIn('MASTER_EXPAND_OUTPUT_WOULD_REPLACE_MASTER', result['error'])
        self.assertFalse(batch.exists())
        self.assertEqual(before, sha(path))

    def test_mirrored_delivery_outside_the_master_tree_keeps_the_master_name(self):
        path, masters = self.delivery_master('ok')
        batch = self.root/'ok-batch.json'; out = self.root/'delivered-3x'
        self.call('expand-plan', '--source', path, '--base-width', 10, '--base-height', 8, '--target-scale', 3,
                  '--mirror-source-root', masters, '--output-root', out, '--out', batch)
        item = json.loads(batch.read_bytes())['items'][0]
        self.assertEqual(Path(item['output']).as_posix().split('/')[-2:], ['SECSK32', 'ok.png'],
                         'mirror preserves the identity folder and the 3X keeps the master name')
        self.call('expand', '--batch', batch, '--workers', 2, '--memory-gib', 1)
        self.assertTrue(Path(item['output']).is_file())
        self.assertEqual(sha(path), sha(path), 'master bytes are untouched by a successful delivery')

    def test_natural_framing_still_expands_without_an_override(self):
        for name, box in (('grounded', (0, 0, 100, 80)), ('cutout', (20, 20, 80, 80)), ('offcenter', (2, 0, 41, 100))):
            with self.subTest(framing=name):
                path, batch, out = self.framed_master(name, box)
                self.call('expand', '--batch', batch, '--workers', 2, '--memory-gib', 1)
                self.assertEqual(1, len([p for p in out.rglob('*.png')]))
                r = json.loads(next((out/'receipts').glob('*.json')).read_bytes())
                self.assertFalse(r['padded_master_allowed'])
                self.assertAlmostEqual(box[0], r['artwork_bands_pct'][0], places=1)

    def test_padded_override_is_recorded_and_sealed_into_the_identity(self):
        path, batch, out = self.framed_master('sealed', (10, 0, 90, 100), extra=('--allow-padded-master',))
        self.assertTrue(json.loads(batch.read_bytes())['items'][0]['padded_master_allowed'])
        self.call('expand', '--batch', batch, '--workers', 2, '--memory-gib', 1)
        r = json.loads(next((out/'receipts').glob('*.json')).read_bytes())
        self.assertTrue(r['padded_master_allowed'])
        self.assertEqual([10, 10, 0, 0], [round(v) for v in r['artwork_bands_pct']])
        doc = json.loads(batch.read_bytes())
        del doc['items'][0]['padded_master_allowed']
        save(batch, doc)
        self.assertIn('receipt missing', json.dumps(
            self.raw('expand-verify', '--batch', batch, '--workers', 2, '--memory-gib', 1)),
            'dropping the override must not silently re-verify the padded output')

    def test_current_algorithm_reuse_keeps_receipt_identity_stable(self):
        r=self.run_batch()
        self.assertEqual(r['results'][0]['status'],'EXPANDED_VERIFIED')
        receipt=Path(r['results'][0]['receipt'])
        before=json.loads(receipt.read_bytes());receipt_sha=sha(receipt)
        output=Path(self.batch['items'][0]['output']);output_sha=sha(output)
        again=self.call('expand','--batch',self.batch_path)
        self.assertEqual(again['results'][0]['status'],'REUSED_VERIFIED')
        self.assertEqual(again['outputs_written'],0)
        self.assertEqual(sha(output),output_sha)
        self.assertEqual(sha(receipt),receipt_sha)
        self.assertEqual(json.loads(receipt.read_bytes())['contract']['algorithm'],
                         'linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4')
        self.assertEqual(sha(self.source),self.original_sha)

    def test_native_large_image_goes_directly_to_default_exact_3x(self):
        source=self.root/'native-large.png'
        Image.new('RGBA',(120,96),(90,160,240,160)).save(source)
        original=sha(source);p=self.root/'production3.json'
        self.call('expand-plan','--source',source,'--base-width',10,'--base-height',8,
                  '--output-root',self.root/'production3','--out',p)
        batch=json.loads(p.read_bytes());self.assertEqual(batch['items'][0]['target_scale'],3)
        self.call('expand','--batch',p);self.call('expand-verify','--batch',p)
        with Image.open(batch['items'][0]['output']) as im:self.assertEqual(im.size,(30,24))
        self.assertEqual(sha(source),original)

    def test_master_may_be_standardised_to_any_integer_multiple_up_to_36(self):
        # A shipping press is 3X; preparing the master at the largest multiple its own pixels can
        # carry keeps that final step an exact integer downscale, so 3X..36X must plan.
        source=self.root/'std.png'
        Image.new('RGBA',(120,96),(90,160,240,160)).save(source)
        p=self.root/'std12.json'
        self.call('expand-plan','--source',source,'--base-width',10,'--base-height',8,
                  '--target-scale',12,'--output-root',self.root/'std12','--out',p)
        batch=json.loads(p.read_bytes());self.assertEqual(batch['items'][0]['target_scale'],12)
        self.call('expand','--batch',p);self.call('expand-verify','--batch',p)
        with Image.open(batch['items'][0]['output']) as im:self.assertEqual(im.size,(120,96))
        over=self.root/'std40.json'
        self.call('expand-plan','--source',source,'--base-width',10,'--base-height',8,
                  '--target-scale',40,'--output-root',self.root/'std40','--out',over,ok=False)
        self.assertFalse(over.exists())

    def test_legacy_4x_expansion_is_no_longer_selectable(self):
        p=self.root/'legacy.json'
        self.call('expand-plan','--source',self.source,'--source-tier',3,'--target-scale',4,
                  '--output-root',self.root/'legacy','--out',p,'--filter','lanczos3',ok=False)
        self.assertFalse(p.exists())
        self.assertFalse((self.root/'legacy').exists())
        self.assertEqual(sha(self.source),self.original_sha)

    def test_exact_3x_to_4x_and_transparent_edge(self):
        r = self.run_batch(); self.assertEqual(r['outputs_written'],1)
        out = Path(self.batch['items'][0]['output'])
        with Image.open(out) as im:
            self.assertEqual(im.size,(40,32)); self.assertEqual(im.mode,'RGBA')
            self.assertTrue(all(not a or r == 90 and g == 160 and b == 240
                                for r,g,b,a in im.get_flattened_data()))
            self.assertTrue(all((r,g,b)==(0,0,0) for r,g,b,a in im.get_flattened_data() if a==0))
        receipt=json.loads(self.receipt().read_bytes())
        for field in ['model_inference_performed','model_native_output','human_approved','game_deployed','runtime_accepted']:
            self.assertIs(receipt[field],False)
        self.assertEqual(receipt['backend'],'cpu'); self.assertEqual(sha(self.source),self.original_sha)
        self.run_batch('expand-verify')

    def test_2x_source_can_become_4x(self):
        self.batch['items'][0]['base_pixels']=[15,12]
        self.run_batch()
        with Image.open(self.batch['items'][0]['output']) as im:self.assertEqual(im.size,(60,48))

    def test_uniform_custom_original_dimensions(self):
        p=self.root/'custom.json'
        self.call('expand-plan','--source',self.source,'--base-width',10,'--base-height',8,
                  '--target-scale',4,'--output-root',self.root/'custom','--out',p)
        self.assertEqual(json.loads(p.read_bytes())['items'][0]['base_pixels'],[10,8])

    def test_mirror_source_root_preserves_category_subfolders(self):
        source_root=self.root/'masters'
        nested=source_root/'建筑'/'据点城专项'/'城镇建筑母版'/'frame.png'
        nested.parent.mkdir(parents=True)
        Image.open(self.source).save(nested)
        p=self.root/'mirrored.json';output=self.root/'mirrored-output'
        self.call('expand-plan','--source',nested,'--source-tier',3,'--target-scale',4,
                  '--output-root',output,'--mirror-source-root',source_root,'--out',p)
        batch=json.loads(p.read_bytes())
        self.assertEqual(batch['mirror_source_root'],str(source_root.resolve()))
        self.assertEqual(Path(batch['items'][0]['output']),
                         output/'建筑'/'据点城专项'/'城镇建筑母版'/'frame.png',
                         'the mirrored 3X keeps the master name; the folder is what marks the tier')
        self.assertFalse(output.exists())

    def test_mirror_source_root_rejects_outside_source_before_batch_write(self):
        source_root=self.root/'unrelated';source_root.mkdir()
        p=self.root/'outside-mirror.json'
        self.call('expand-plan','--source',self.source,'--source-tier',3,
                  '--output-root',self.root/'blocked-output','--mirror-source-root',source_root,
                  '--out',p,ok=False)
        self.assertFalse(p.exists())
        self.assertFalse((self.root/'blocked-output').exists())

    def test_resume_preserves_bytes(self):
        self.run_batch(); out=Path(self.batch['items'][0]['output']); before=sha(out)
        r=self.run_batch(); self.assertEqual(r['results'][0]['status'],'REUSED_VERIFIED')
        self.assertEqual(r['outputs_written'],0); self.assertEqual(sha(out),before)

    def test_partial_output_recovery(self):
        self.run_batch(); out=Path(self.batch['items'][0]['output']); before=sha(out)
        self.receipt().unlink(); self.run_batch(); self.assertEqual(sha(out),before)

    def test_source_drift_is_blocked_before_writes(self):
        Image.new('RGBA',(30,24),(10,10,10,255)).save(self.source)
        r=self.run_batch(ok=False);self.assertEqual(r['outputs_written'],0)
        self.assertFalse(self.output.exists())

    def test_geometry_drift_is_blocked(self):
        self.batch['items'][0]['source_pixels']=[33,24]
        r=self.run_batch(ok=False);self.assertEqual(r['outputs_written'],0)

    def test_aspect_mismatch_is_blocked(self):
        self.batch['items'][0]['base_pixels']=[10,9]
        self.run_batch(ok=False);self.assertFalse(self.output.exists())

    def test_official_size_is_kept_when_master_ratio_differs_slightly(self):
        """The master stays untouched; the output is still exactly base*scale."""
        Image.new('RGBA',(31,25),(0,255,0,255)).save(self.source)   # ratio 1.24 vs official 1.25
        self.batch['items'][0]['source_sha256']=sha(self.source)
        self.batch['items'][0]['source_pixels']=[31,25]
        self.batch['items'][0]['base_pixels']=[10,8]                # official 10x8 -> 40x32
        before=sha(self.source)
        self.run_batch()
        r=json.loads(self.receipt().read_bytes())
        self.assertEqual(r['output_pixels'],[40,32])
        self.assertEqual(r['official_output_pixels'],[40,32])
        self.assertEqual(r['fit_mode'],'official-size-fit')
        self.assertTrue(r['master_untouched'] and r['source_unchanged'])
        self.assertGreater(r['aspect_error_pct'],0.0)
        self.assertLessEqual(r['aspect_error_pct'],2.0)
        self.assertEqual(sha(self.source),before)                   # master bytes untouched
        self.assertEqual(Image.open(self.batch['items'][0]['output']).size,(40,32))

    def test_gross_aspect_difference_still_blocks_and_touches_nothing(self):
        self.batch['items'][0]['base_pixels']=[10,5]                # far beyond the 5% ceiling
        self.run_batch(ok=False);self.assertFalse(self.output.exists())
        self.assertEqual(sha(self.source),self.original_sha)

    def test_more_than_twofold_is_blocked(self):
        self.batch['items'][0]['base_pixels']=[25,20]
        self.run_batch(ok=False);self.assertFalse(self.output.exists())

    def test_mixed_invalid_batch_writes_nothing(self):
        x=copy.deepcopy(self.batch['items'][0]);x['id']='SECOND';x['source_sha256']='0'*64
        x['output']=str(self.output/'second.png');self.batch['items'].append(x)
        r=self.run_batch(ok=False);self.assertEqual(r['outputs_written'],0)
        self.assertFalse(self.output.exists())

    def test_duplicate_destination_is_blocked(self):
        x=copy.deepcopy(self.batch['items'][0]);x['id']='SECOND';self.batch['items'].append(x)
        self.run_batch(ok=False);self.assertFalse(self.output.exists())

    def test_output_cannot_replace_a_source(self):
        self.batch['output_root']=str(self.root)
        self.batch['receipt_directory']=str(self.root/'receipts')
        self.batch['items'][0]['output']=str(self.source)
        self.run_batch(ok=False);self.assertEqual(sha(self.source),self.original_sha)

    def test_conflicting_output_is_not_replaced(self):
        self.output.mkdir();out=Path(self.batch['items'][0]['output'])
        Image.new('RGBA',(40,32),(50,50,50,255)).save(out);before=sha(out)
        self.run_batch(ok=False);self.assertEqual(sha(out),before)
        self.assertFalse(list((self.output/'receipts').glob('*.json')))

    def test_receipt_cannot_claim_model_or_user_approval(self):
        self.run_batch();p=self.receipt();r=json.loads(p.read_bytes());r['model_native_output']=True
        save(p,r);self.run_batch('expand-verify',ok=False)

    def test_receipt_geometry_tamper_is_rejected(self):
        self.run_batch();p=self.receipt();r=json.loads(p.read_bytes());r['target_scale']=3
        save(p,r);self.run_batch('expand-verify',ok=False)

    def test_independent_pixel_replay_rejects_forged_hash(self):
        self.run_batch();p=self.receipt();r=json.loads(p.read_bytes())
        out=Path(self.batch['items'][0]['output']);im=Image.new('RGBA',(40,32),(10,20,30,255));im.save(out)
        r.update(png_sha256=sha(out),rgba_sha256=hashlib.sha256(im.tobytes()).hexdigest(),bytes=out.stat().st_size)
        save(p,r);self.run_batch('expand-verify',ok=False)

    def test_gpu_request_is_explicitly_rejected(self):
        self.call('expand','--batch',self.batch_path,'--backend','gpu',ok=False)
        self.assertFalse(self.output.exists())

    def test_uneven_tier_divisibility_requires_original_geometry(self):
        Image.new('RGBA',(31,24)).save(self.source)
        self.call('expand-plan','--source',self.source,'--source-tier',3,'--target-scale',4,
                  '--output-root',self.output,'--out',self.root/'bad.json',ok=False)

    def test_missing_receipt_is_not_verified(self):
        self.run_batch('expand-verify',ok=False)

class NativeAuthorityContract(unittest.TestCase):
    """The 1999 native DEF is the only size authority; the 2015 HD pack must not size a 3X."""

    def setUp(self):
        parent = PRODUCT/'build/tests'; parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='authority-', dir=parent)
        self.root = Path(self.temp.name).resolve()
        # masters live in <identity>/<frame>.png: the folder is the identity the table is keyed by
        self.identity = 'CSSARM'
        folder = self.root/self.identity; folder.mkdir()
        self.master = folder/'0_0.png'
        Image.new('RGBA', (30, 24), (255, 0, 0, 0)).save(self.master)
        self.native = [10, 8]                      # what the native LOD says the 1X slot is
        self.hd_pack = [12, 9]                     # a different canvas, like H3ab_spr.lod ships
        self.authority = self.root/'native-1x-authority.json'
        self.output = self.root/'output'; self.batch_path = self.root/'batch.json'
        self.write_authority()

    def tearDown(self): self.temp.cleanup()

    def write_authority(self, sizes=None, lods=None, drop_identity=False):
        doc = {'schema': 'h3.native-1x-authority.v1',
               'source_lods': lods if lods is not None else
                              [{'name': 'H3sprite.lod'}, {'name': 'H3bitmap.lod'}],
               'frames': {} if drop_identity else {self.identity: {'0_0': sizes or self.native}}}
        save(self.authority, doc)

    def plan(self, *extra, ok=True):
        return self.call('expand-plan', '--source', self.master, '--target-scale', 3,
                         '--output-root', self.output, '--out', self.batch_path,
                         '--native-authority', self.authority, *extra, ok=ok)

    def call(self, *args, ok=True):
        p = subprocess.run([str(EXE), *map(str, args)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(p.returncode == 0, ok, (p.stdout+p.stderr)[-2000:])
        return (p.stdout+p.stderr)

    def test_authority_sets_the_1x_size_without_any_declared_base(self):
        self.plan()
        batch = json.loads(self.batch_path.read_bytes())
        item = batch['items'][0]
        self.assertEqual(item['base_pixels'], self.native)
        self.assertEqual(item['native_1x_pixels'], self.native)
        self.assertEqual(item['size_authority'], 'native-1x')
        self.assertEqual(batch['native_1x_authority']['sha256'], sha(self.authority))

    def test_declaring_hd_pack_dimensions_is_refused(self):
        out = self.plan('--base-width', self.hd_pack[0], '--base-height', self.hd_pack[1], ok=False)
        self.assertIn('NATIVE_1X_AUTHORITY_CONFLICT', out)

    def test_reference_frame_from_the_hd_pack_is_refused(self):
        reference = self.root/'references'/self.identity; reference.mkdir(parents=True)
        Image.new('RGBA', tuple(self.hd_pack), (0, 0, 0, 0)).save(reference/'0_0.png')
        out = self.call('expand-plan', '--source', self.master, '--target-scale', 3,
                        '--output-root', self.output, '--out', self.root/'other.json',
                        '--native-authority', self.authority,
                        '--reference-root', self.root/'references', ok=False)
        self.assertIn('is not the 1X slot', out)
        self.assertIn('2015 HD pack', out)

    def test_identity_absent_from_the_authority_is_refused(self):
        self.write_authority(drop_identity=True)
        self.assertIn('NATIVE_1X_AUTHORITY_MISSING_IDENTITY', self.plan(ok=False))

    def test_frame_absent_from_the_authority_is_refused(self):
        save(self.authority, {'schema': 'h3.native-1x-authority.v1',
                              'source_lods': [{'name': 'H3sprite.lod'}],
                              'frames': {self.identity: {'0_1': self.native}}})
        self.assertIn('NATIVE_1X_AUTHORITY_MISSING_FRAME', self.plan(ok=False))

    def test_a_table_built_from_the_hd_pack_is_refused(self):
        self.write_authority(lods=[{'name': 'H3ab_spr.lod'}])
        self.assertIn('NATIVE_1X_AUTHORITY_SOURCE_IS_HD_PACK', self.plan(ok=False))

    def test_a_table_that_names_no_native_lod_is_refused(self):
        save(self.authority, {'schema': 'h3.native-1x-authority.v1', 'frames': {}})
        self.assertIn('NATIVE_1X_AUTHORITY_SOURCE_LODS_MISSING', self.plan(ok=False))

    def test_authority_changed_after_planning_is_refused(self):
        self.plan(); self.write_authority(sizes=[11, 9])
        out = self.call('expand', '--batch', self.batch_path, '--workers', 2, '--memory-gib', 1,
                        ok=False)
        self.assertIn('NATIVE_1X_AUTHORITY_DRIFTED', out)

    def test_edited_base_pixels_cannot_execute(self):
        self.plan()
        batch = json.loads(self.batch_path.read_bytes())
        batch['items'][0]['base_pixels'] = self.hd_pack
        save(self.batch_path, batch)
        out = self.call('expand', '--batch', self.batch_path, '--workers', 2, '--memory-gib', 1,
                        ok=False)
        self.assertIn('NATIVE_1X_AUTHORITY_CONFLICT', out)

    def test_output_geometry_follows_the_native_authority(self):
        self.plan()
        self.call('expand', '--batch', self.batch_path, '--workers', 2, '--memory-gib', 1)
        produced = next(p for p in self.output.rglob('*.png') if p.name == '0_0.png')
        self.assertEqual(list(Image.open(produced).size), [self.native[0]*3, self.native[1]*3])

class StaticFxGroupFitContract(unittest.TestCase):
    def setUp(self):
        parent=PRODUCT/'build/tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='static-fx-group-fit-',dir=parent)
        self.root=Path(self.temp.name).resolve()
        self.identity='TESTFX'
        self.sources=self.root/'masters'/self.identity;self.sources.mkdir(parents=True)
        self.references=self.root/'references'/self.identity;self.references.mkdir(parents=True)
        for frame in range(2):
            ref=Image.new('RGBA',(20,20),(0,0,0,0));draw=ImageDraw.Draw(ref)
            # One fixed body and ground line; only the small smoke plume changes.
            draw.rectangle((2,7,17,18),fill=(92,120,76,255))
            draw.rectangle((0,19,19,19),fill=(68,74,53,255))
            draw.rectangle((9,3+frame,10,6+frame),(206,207,203,220))
            ref.save(self.references/f'0_{frame}.png')
            ref.resize((60,60),Image.Resampling.NEAREST).save(self.sources/f'0_{frame}.png')
        self.batch_path=self.root/'batch.json';self.output=self.root/'output'
        args=['expand-plan']
        for source in sorted(self.sources.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--source-tier','3','--target-scale','3','--reference-root',str(self.root/'references'),
               '--animation-contract','static-structure-dynamic-fx-v2','--output-root',str(self.output),
               '--out',str(self.batch_path)]
        self.call(*args)
        self.batch=json.loads(self.batch_path.read_bytes())

    def tearDown(self):self.temp.cleanup()

    def call(self,*args,ok=True):
        p=subprocess.run([str(EXE),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        self.assertEqual(p.returncode==0,ok,(p.stdout+p.stderr)[-4000:])
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def test_static_fx_uses_one_shared_transform_and_replays_all_stages(self):
        transforms=[item['animation_group_transform'] for item in self.batch['items']]
        self.assertEqual(2,len(transforms))
        self.assertEqual(transforms[0],transforms[1])
        self.assertEqual('one_shared_transform_for_all_frames',transforms[0]['registration'])
        checked=self.call('expand-check','--batch',self.batch_path)
        audit=checked['animation_registration']
        self.assertEqual('PASS',audit['status'])
        self.assertGreaterEqual(audit['groups'][0]['static_base']['stable_pixel_fraction'],0.20)
        self.assertEqual('PASS',audit['groups'][0]['dynamic_fx']['status'])
        self.call('expand','--batch',self.batch_path,'--workers',2,'--memory-gib',1)
        self.call('expand-verify','--batch',self.batch_path,'--workers',2,'--memory-gib',1)
        outputs=sorted((self.output/'0_0.png').parent.glob('0_*.png'))
        self.assertEqual(2,len(outputs))
        self.assertTrue(all(Image.open(path).size==(60,60) for path in outputs))

    def test_dynamic_fx_anchor_is_measured_after_the_shared_group_transform(self):
        # A wide master canvas contains a centered native-size square image
        # padded only on the right. After the shared silhouette fit, its FX
        # anchor exactly matches the official native slot. Raw source-canvas
        # fractions would compare .375 to .75 and falsely block this animation.
        wide_sources=self.root/'wide-canvas-masters'/self.identity;wide_sources.mkdir(parents=True)
        wide_refs=self.root/'wide-canvas-references'/self.identity;wide_refs.mkdir(parents=True)
        for frame in range(2):
            ref=Image.new('RGBA',(20,20),(0,0,0,0));draw=ImageDraw.Draw(ref)
            draw.rectangle((2,7,17,18),fill=(92,120,76,255))
            draw.rectangle((0,19,19,19),fill=(68,74,53,255))
            draw.rectangle((9+frame,3,10+frame,6),fill=(210,214,205,220))
            ref.save(wide_refs/f'0_{frame}.png')
            wide=Image.new('RGBA',(120,60),(0,0,0,0))
            wide.alpha_composite(ref.resize((60,60),Image.Resampling.NEAREST),(0,0))
            wide.save(wide_sources/f'0_{frame}.png')
        batch_path=self.root/'wide-canvas-batch.json';out=self.root/'wide-canvas-output'
        args=['expand-plan']
        for source in sorted(wide_sources.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--base-width','20','--base-height','20','--target-scale','3',
               '--reference-root',str(self.root/'wide-canvas-references'),
               '--animation-contract','static-structure-dynamic-fx-v2',
               '--output-root',str(out),'--out',str(batch_path)]
        self.call(*args)
        checked=self.call('expand-check','--batch',batch_path)
        audit=checked['animation_registration']
        self.assertEqual('PASS',audit['status'])
        fx=audit['groups'][0]['dynamic_fx']
        self.assertEqual('PASS',fx['status'])
        self.assertEqual('shared_group_transform_3x_normalized_to_native_canvas',fx['registration_space'])
        self.assertLessEqual(fx['mean_center_x_delta_percent_of_canvas'],5.0)
        self.assertLessEqual(fx['mean_center_y_delta_percent_of_canvas'],5.0)

    def test_tampered_shared_transform_blocks_before_writes(self):
        for stage in ('expand-check','expand','expand-verify'):
            if stage=='expand':self.call('expand','--batch',self.batch_path,'--workers',2,'--memory-gib',1)
        before={p.relative_to(self.output).as_posix():sha(p) for p in self.output.rglob('*') if p.is_file()}
        self.batch['items'][0]['animation_group_transform']['offset_pixels'][0]+=1
        save(self.batch_path,self.batch)
        result=self.call('expand-check','--batch',self.batch_path,ok=False)
        self.assertIn('ANIMATION_GROUP_TRANSFORM_DRIFT',result['error'])
        after={p.relative_to(self.output).as_posix():sha(p) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_vcmi_base_overlay_reuses_frame_zero_for_geometry_and_fx_checks(self):
        sources=self.root/'base-overlay-masters'/self.identity;sources.mkdir(parents=True)
        references=self.root/'base-overlay-references'/self.identity;references.mkdir(parents=True)
        base=Image.new('RGBA',(20,20),(0,0,0,0));draw=ImageDraw.Draw(base)
        draw.rectangle((2,7,17,18),fill=(92,120,76,253))
        draw.rectangle((0,19,19,19),fill=(68,74,53,253))
        draw.rectangle((9,3,10,6),fill=(206,207,203,253))
        base.save(references/'0_0.png')
        base.resize((60,60),Image.Resampling.NEAREST).save(sources/'0_0.png')
        for index in range(1,10):
            overlay=Image.new('RGBA',(20,20),(0,0,0,0))
            x=8+(index%3);y=3+(index%2)
            ImageDraw.Draw(overlay).rectangle((x,y,x+1,y+3),fill=(206,207,203,220))
            overlay.save(references/f'0_{index}.png')
            overlay.resize((60,60),Image.Resampling.NEAREST).save(sources/f'0_{index}.png')
        batch_path=self.root/'base-overlay-batch.json';output=self.root/'base-overlay-output'
        args=['expand-plan']
        for source in sorted(sources.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--source-tier','3','--target-scale','3','--reference-root',str(self.root/'base-overlay-references'),
               '--animation-contract','static-base-dynamic-overlay-v1','--output-root',str(output),
               '--out',str(batch_path)]
        self.call(*args)
        batch=json.loads(batch_path.read_bytes())
        self.assertEqual('vcmi-base-frame-then-current-frame',batch['animation_gate']['render_semantics'])
        self.assertEqual(0,batch['animation_gate']['base_frame'])
        checked=self.call('expand-check','--batch',batch_path)
        audit=checked['animation_registration']
        self.assertEqual('static-base-dynamic-overlay-v1',audit['contract'])
        self.assertEqual(1,audit['gate_version'])
        self.assertEqual('PASS',audit['status'])
        self.assertGreaterEqual(audit['groups'][0]['static_base']['stable_pixel_fraction'],0.20)
        self.assertEqual('frame0_base_vs_later_composited_scenes_exact_rgba',audit['groups'][0]['static_base']['comparison'])
        self.assertEqual('PASS',audit['groups'][0]['dynamic_fx']['status'])
        self.assertEqual(9,audit['groups'][0]['dynamic_fx']['measured_master_frame_count'])
        self.assertEqual(9,audit['groups'][0]['dynamic_fx']['measured_official_frame_count'])
        self.assertEqual('static-base-with-initial-overlay',audit['groups'][0]['dynamic_fx']['frames'][0]['role'])
        self.call('expand','--batch',batch_path,'--workers',2,'--memory-gib',1)
        verified=self.call('expand-verify','--batch',batch_path,'--workers',2,'--memory-gib',1)
        self.assertEqual('COMPLETED',verified['status'])
        outputs=sorted((output/'0_0.png').parent.glob('0_*.png'))
        self.assertEqual(10,len(outputs))
        self.assertTrue(all(Image.open(path).size==(60,60) for path in outputs))

    def test_base_overlay_v2_uses_official_stability_for_full_cels(self):
        sources=self.root/'base-full-cel-masters'/self.identity;sources.mkdir(parents=True)
        references=self.root/'base-full-cel-references'/self.identity;references.mkdir(parents=True)
        for frame in range(3):
            image=Image.new('RGBA',(20,20))
            for y in range(20):
                for x in range(20):image.putpixel((x,y),((30+x*7+frame*41)%256,(50+y*9+frame*53)%256,(70+x*y+frame*67)%256,255))
            image.save(references/f'0_{frame}.png')
            image.resize((60,60),Image.Resampling.NEAREST).save(sources/f'0_{frame}.png')
        batch_path=self.root/'base-full-cel-batch.json';output=self.root/'base-full-cel-output'
        args=['expand-plan']
        for source in sorted(sources.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--source-tier','3','--target-scale','3','--reference-root',str(self.root/'base-full-cel-references'),
               '--animation-contract','static-base-dynamic-overlay-v2','--output-root',str(output),'--out',str(batch_path)]
        self.call(*args)
        batch=json.loads(batch_path.read_bytes())
        self.assertEqual('h3.animation.static-base-overlay.scale-gate.v2',batch['animation_gate']['schema'])
        checked=self.call('expand-check','--batch',batch_path)
        audit=checked['animation_registration'];group=audit['groups'][0]
        self.assertEqual('static-base-dynamic-overlay-v2',audit['contract'])
        self.assertEqual(2,audit['gate_version'])
        self.assertEqual('PASS',audit['status'])
        self.assertLess(group['static_base']['official_stable_pixel_fraction'],0.20)
        self.assertLessEqual(group['static_base']['native_stability_deficit_percentage_points'],5.0)
        self.assertEqual('PASS',group['dynamic_fx']['status'])
        self.call('expand','--batch',batch_path,'--workers',2,'--memory-gib',1)
        verified=self.call('expand-verify','--batch',batch_path,'--workers',2,'--memory-gib',1)
        self.assertEqual('COMPLETED',verified['status'])
        legacy=self.root/'base-full-cel-legacy.json'
        legacy_args=list(args);legacy_args[legacy_args.index('--animation-contract')+1]='static-base-dynamic-overlay-v1'
        legacy_args[legacy_args.index('--out')+1]=str(legacy);legacy_args[legacy_args.index('--output-root')+1]=str(self.root/'base-full-cel-legacy-output')
        refused=self.call(*legacy_args,ok=False)
        self.assertIn('ANIMATION_STATIC_BASE_NOT_MEASURABLE',refused['error'])

    def test_base_overlay_v2_blocks_absolute_fx_size_mismatch(self):
        sources=self.root/'base-size-masters'/self.identity;sources.mkdir(parents=True)
        references=self.root/'base-size-references'/self.identity;references.mkdir(parents=True)
        base=Image.new('RGBA',(20,20),(0,0,0,0));draw=ImageDraw.Draw(base)
        draw.rectangle((2,2,17,17),fill=(85,90,70,255));base.save(references/'0_0.png')
        base.resize((60,60),Image.Resampling.NEAREST).save(sources/'0_0.png')
        for frame,color in enumerate((70,160,250),1):
            official=Image.new('RGBA',(20,20),(0,0,0,0))
            ImageDraw.Draw(official).rectangle((8,7,15,9),fill=(245,color,70,255))
            official.save(references/f'0_{frame}.png')
            source=Image.new('RGBA',(60,60),(0,0,0,0))
            ImageDraw.Draw(source).rectangle((30,21,31,29),fill=(245,color,70,255))
            source.save(sources/f'0_{frame}.png')
        batch=self.root/'base-size-batch.json';output=self.root/'base-size-output'
        args=['expand-plan']
        for source in sorted(sources.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--source-tier','3','--target-scale','3','--reference-root',str(self.root/'base-size-references'),
               '--animation-contract','static-base-dynamic-overlay-v2','--output-root',str(output),'--out',str(batch)]
        blocked=self.call(*args,ok=False)
        self.assertIn('ANIMATION_DYNAMIC_FX_FRAME_SIZE_MISMATCH',blocked['error'])
        self.assertFalse(batch.exists());self.assertFalse(output.exists())

class FrameSetClassificationContract(unittest.TestCase):
    def setUp(self):
        parent=PRODUCT/'build/tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='frame-set-classification-',dir=parent)
        self.root=Path(self.temp.name).resolve();self.identity='TESTSTATIC'
        self.sources=self.root/'masters'/self.identity;self.sources.mkdir(parents=True)
        self.references=self.root/'references'/self.identity;self.references.mkdir(parents=True)
        for frame in range(2):
            image=Image.new('RGBA',(20,20))
            for y in range(20):
                for x in range(20):image.putpixel((x,y),(70+x,80+y,90+frame,255))
            image.save(self.references/f'0_{frame}.png')
            image.resize((60,60),Image.Resampling.NEAREST).save(self.sources/f'0_{frame}.png')
        self.output=self.root/'output';self.batch=self.root/'batch.json'

    def tearDown(self):self.temp.cleanup()

    def call(self,*args,ok=True):
        p=subprocess.run([str(EXE),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        self.assertEqual(p.returncode==0,ok,(p.stdout+p.stderr)[-3000:])
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def sources_args(self,sources=None):
        args=[]
        for source in sorted((sources or self.sources).glob('0_*.png')):args+=['--source',str(source)]
        return args

    def test_static_catalog_requires_official_scale_reference_root(self):
        output=self.root/'missing-reference-output';batch=self.root/'missing-reference-batch.json'
        result=self.call('expand-plan',*self.sources_args(),'--source-tier','3','--target-scale','3',
                         '--frame-set-classification','independent-static-frame-catalog-v1',
                         '--output-root',output,'--out',batch,ok=False)
        self.assertIn('FRAME_SET_CATALOG_OFFICIAL_REFERENCE_REQUIRED',result['error'])
        self.assertFalse(batch.exists())
        self.assertFalse(output.exists())

    def test_opaque_multiframe_without_classification_blocks_before_creating_outputs(self):
        result=self.call('expand-plan',*self.sources_args(),'--source-tier','3','--target-scale','3',
                         '--reference-root',self.root/'references','--output-root',self.output,'--out',self.batch,ok=False)
        self.assertIn('MULTIFRAME_FRAME_SET_CLASSIFICATION_REQUIRED',result['error'])
        self.assertFalse(self.batch.exists())
        self.assertFalse(self.output.exists())

    def test_independent_static_catalog_binds_complete_official_frames_and_checks(self):
        self.call('expand-plan',*self.sources_args(),'--source-tier','3','--target-scale','3',
                  '--reference-root',self.root/'references','--frame-set-classification',
                  'independent-static-frame-catalog-v1','--output-root',self.output,'--out',self.batch)
        batch=json.loads(self.batch.read_bytes())
        self.assertEqual('independent-static-frame-catalog-v1',batch['frame_set_classification'])
        self.assertEqual(2,len(batch['frame_set_inventory'][0]['frames']))
        self.assertEqual('h3.master-expand.visible-scale-frame-catalog.v4',batch['visible_scale_gate']['schema'])
        self.assertEqual(0.95,batch['visible_scale_gate']['minimum_width_ratio'])
        self.assertEqual(0.95,batch['visible_scale_gate']['minimum_height_ratio'])
        self.assertEqual(0.95,batch['visible_scale_gate']['minimum_post_fit_width_ratio'])
        self.assertEqual(0.95,batch['visible_scale_gate']['minimum_post_fit_height_ratio'])
        self.assertIn('exact predicted crop-fit size',batch['visible_scale_gate']['measurement'])
        missing_gate=self.root/'missing-scale-gate.json';without_gate=json.loads(self.batch.read_bytes());without_gate.pop('visible_scale_gate');save(missing_gate,without_gate)
        gate_blocked=self.call('expand-check','--batch',missing_gate,ok=False)
        self.assertIn('VISIBLE_SCALE_GATE_POLICY_REQUIRED',json.dumps(gate_blocked))
        legacy_v1={'schema':'h3.master-expand.visible-scale-floor.v1','minimum_width_ratio':0.8,'minimum_height_ratio':0.8,
                   'alpha_threshold':8,'measurement':'predicted output artwork box after the official uniform fit versus the official visible box'}
        legacy_v2={'schema':'h3.master-expand.visible-scale-frame-catalog.v2','minimum_width_ratio':0.95,'minimum_height_ratio':0.95,
                   'alpha_threshold':8,'measurement':'normalized source visible width/height fractions versus matching official frame fractions before any resampling'}
        for index,policy in enumerate((legacy_v1,legacy_v2)):
            stale=self.root/f'stale-prefit-policy-{index}.json';stale_batch=json.loads(self.batch.read_bytes());stale_batch['visible_scale_gate']=policy;save(stale,stale_batch)
            blocked=self.call('expand-check','--batch',stale,ok=False)
            self.assertIn('VISIBLE_SCALE_GATE_POLICY_STALE_REPLAN_REQUIRED',json.dumps(blocked))
        checked=self.call('expand-check','--batch',self.batch)
        self.assertEqual(2,checked['ready']);self.assertEqual(0,checked['blocked'])
        self.call('expand','--batch',self.batch,'--workers',2,'--memory-gib',1)
        verified=self.call('expand-verify','--batch',self.batch,'--workers',2,'--memory-gib',1)
        self.assertEqual('COMPLETED',verified['status'])
        scale_rows=[row['post_export_visible_scale_gate'] for row in verified['results']]
        self.assertEqual(2,len(scale_rows));self.assertTrue(all(row['status']=='PASS' for row in scale_rows))
        for index,policy in enumerate((legacy_v1,legacy_v2)):
            legacy=self.root/f'legacy-prefit-policy-{index}.json';legacy_batch=json.loads(self.batch.read_bytes());legacy_batch['visible_scale_gate']=policy;save(legacy,legacy_batch)
            replay=self.call('expand-verify','--batch',legacy,'--workers',2,'--memory-gib',1)
            self.assertEqual('COMPLETED',replay['status'])
            self.assertIn('HISTORICAL_WEAK_POLICY_REPLAY_ONLY',json.dumps(replay))
        del batch['frame_set_classification'];del batch['frame_set_inventory'];save(self.batch,batch)
        blocked=self.call('expand-check','--batch',self.batch,ok=False)
        self.assertGreaterEqual(blocked['blocked'],1)
        self.assertIn('MULTIFRAME_FRAME_SET_CLASSIFICATION_REQUIRED',json.dumps(blocked))

    def test_static_catalog_gate_matches_the_exact_crop_fit_used_by_export(self):
        identity='CHLIKE';masters=self.root/'chlike-masters'/identity;masters.mkdir(parents=True)
        references=self.root/'chlike-references'/identity;references.mkdir(parents=True)
        args=['expand-plan']
        for frame in range(2):
            official=Image.new('RGBA',(150,175),(0,0,0,0))
            ImageDraw.Draw(official).rectangle((42,35,101,145),fill=(80,110,90,255))
            official.save(references/f'0_{frame}.png')
            source=Image.new('RGBA',(1347,1550),(0,0,0,0))
            ImageDraw.Draw(source).rectangle((379,271,967,1301),fill=(90,135,105,255))
            source.save(masters/f'0_{frame}.png')
            args+=['--source',str(masters/f'0_{frame}.png')]
        batch=self.root/'chlike-batch.json';output=self.root/'chlike-output'
        args+=['--base-width','150','--base-height','175','--target-scale','3',
               '--reference-root',str(self.root/'chlike-references'),
               '--frame-set-classification','independent-static-frame-catalog-v1',
               '--output-root',str(output),'--out',str(batch)]
        blocked=self.call(*args,ok=False)
        self.assertIn('MASTER_VISIBLE_SCALE_TOO_SMALL',blocked['error'])
        self.assertFalse(batch.exists());self.assertFalse(output.exists())

    def test_expand_verify_blocks_a_real_3x_readback_below_the_scale_floor(self):
        self.call('expand-plan',*self.sources_args(),'--source-tier','3','--target-scale','3',
                  '--reference-root',self.root/'references','--frame-set-classification',
                  'independent-static-frame-catalog-v1','--output-root',self.output,'--out',self.batch)
        batch_path=self.batch;batch=json.loads(batch_path.read_bytes())
        self.call('expand','--batch',batch_path,'--workers',2,'--memory-gib',1)
        item=batch['items'][0];output=Path(item['output'])
        image=Image.open(output).convert('RGBA')
        for y in range(4):
            for x in range(image.width):image.putpixel((x,y),(0,0,0,0))
        image.save(output)
        rgba=image.tobytes();receipt_path=next((self.output/'receipts').glob(item['id']+'-*.json'))
        receipt=json.loads(receipt_path.read_bytes());png=output.read_bytes()
        receipt.update({'bytes':len(png),'png_sha256':hashlib.sha256(png).hexdigest(),
                        'rgba_sha256':hashlib.sha256(rgba).hexdigest()})
        save(receipt_path,receipt)
        process=subprocess.run([str(EXE),'expand-verify','--batch',str(batch_path),'--workers','2','--memory-gib','1'],
                               capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        verified=json.loads(process.stdout if process.stdout.strip() else process.stderr)
        self.assertEqual('COMPLETED_WITH_FAILURES',verified['status'])
        self.assertEqual(1,verified['failed'])
        self.assertIn('EXPANDED_VISIBLE_SCALE_TOO_SMALL',json.dumps(verified))

    def test_static_catalog_allows_singleton_group_with_complete_official_inventory(self):
        source_dir=self.root/'singleton-masters'/self.identity;source_dir.mkdir(parents=True)
        reference_root=self.root/'singleton-references';reference_dir=reference_root/self.identity;reference_dir.mkdir(parents=True)
        slots=('0_0','1_1','1_2')
        for index,slot in enumerate(slots):
            reference=Image.new('RGBA',(20,20),(70+index,80,90,255))
            reference.save(reference_dir/(slot+'.png'))
            reference.resize((60,60),Image.Resampling.NEAREST).save(source_dir/(slot+'.png'))
        output=self.root/'singleton-output';batch_path=self.root/'singleton-batch.json';args=[]
        for slot in slots:args+=['--source',str(source_dir/(slot+'.png'))]
        self.call('expand-plan',*args,'--base-width','20','--base-height','20','--target-scale','3',
                  '--reference-root',reference_root,'--frame-set-classification',
                  'independent-static-frame-catalog-v1','--output-root',output,'--out',batch_path)
        batch=json.loads(batch_path.read_bytes())
        self.assertEqual(3,len(batch['items']))
        self.assertEqual([1,2],[len(group['frames']) for group in batch['frame_set_inventory']])

    def test_animation_contract_still_rejects_singleton_reference_group(self):
        source_dir=self.root/'animation-singleton-masters'/self.identity;source_dir.mkdir(parents=True)
        reference_root=self.root/'animation-singleton-references';reference_dir=reference_root/self.identity;reference_dir.mkdir(parents=True)
        reference=Image.new('RGBA',(20,20),(80,95,100,255));reference.save(reference_dir/'0_0.png')
        reference.resize((60,60),Image.Resampling.NEAREST).save(source_dir/'0_0.png')
        output=self.root/'animation-singleton-output';batch_path=self.root/'animation-singleton-batch.json'
        result=self.call('expand-plan','--source',source_dir/'0_0.png','--base-width','20','--base-height','20',
                         '--target-scale','3','--reference-root',reference_root,
                         '--animation-contract','static-structure-dynamic-fx-v2',
                         '--output-root',output,'--out',batch_path,ok=False)
        self.assertIn('ANIMATION_SCALE_GROUP_NEEDS_AT_LEAST_2_FRAMES',result['error'])
        self.assertFalse(batch_path.exists())
        self.assertFalse(output.exists())

    def test_catalog_rejects_source_below_official_visible_scale_before_3x(self):
        small=self.root/'undersized-masters'/self.identity;small.mkdir(parents=True)
        for frame in range(2):
            image=Image.new('RGBA',(60,60),(0,0,0,0))
            for y in range(25,35):
                for x in range(4,56):image.putpixel((x,y),(100,120,80,255))
            image.save(small/f'0_{frame}.png')
        output=self.root/'undersized-output';batch=self.root/'undersized-batch.json'
        args=[]
        for frame in range(2):args+=['--source',str(small/f'0_{frame}.png')]
        result=self.call('expand-plan',*args,'--source-tier','3','--target-scale','3',
                         '--reference-root',self.root/'references','--frame-set-classification',
                         'independent-static-frame-catalog-v1','--output-root',output,'--out',batch,ok=False)
        self.assertIn('MASTER_VISIBLE_SCALE_TOO_SMALL',result['error'])
        self.assertFalse(batch.exists());self.assertFalse(output.exists())

    def test_catalog_rejects_post_fit_single_axis_shrink_even_when_raw_fractions_pass(self):
        refs=self.root/'post-fit-references';refdir=refs/self.identity;refdir.mkdir(parents=True)
        masters=self.root/'post-fit-masters'/self.identity;masters.mkdir(parents=True)
        for frame in range(2):
            official=Image.new('RGBA',(20,20),(0,0,0,0))
            source=Image.new('RGBA',(60,60),(0,0,0,0))
            for y in range(2,18):
                for x in range(2,18):official.putpixel((x,y),(70,90,100,255))
            for y in range(6,54):
                for x in range(4,56):source.putpixel((x,y),(100,120,80,255))
            official.save(refdir/f'0_{frame}.png')
            source.save(masters/f'0_{frame}.png')
        output=self.root/'post-fit-output';batch=self.root/'post-fit-batch.json';args=[]
        for frame in range(2):args+=['--source',str(masters/f'0_{frame}.png')]
        result=self.call('expand-plan',*args,'--base-width','20','--base-height','20','--target-scale','3',
                         '--reference-root',refs,'--frame-set-classification','independent-static-frame-catalog-v1',
                         '--output-root',output,'--out',batch,ok=False)
        self.assertIn('MASTER_VISIBLE_SCALE_TOO_SMALL',result['error'])
        self.assertFalse(batch.exists())
        self.assertFalse(output.exists())

    def test_catalog_rejects_uniformly_small_same_aspect_source_before_3x(self):
        small=self.root/'uniformly-small-masters'/self.identity;small.mkdir(parents=True)
        for frame in range(2):
            image=Image.new('RGBA',(60,60),(0,0,0,0))
            for y in range(15,45):
                for x in range(15,45):image.putpixel((x,y),(100,120,80,255))
            image.save(small/f'0_{frame}.png')
        output=self.root/'uniformly-small-output';batch=self.root/'uniformly-small-batch.json'
        args=[]
        for frame in range(2):args+=['--source',str(small/f'0_{frame}.png')]
        result=self.call('expand-plan',*args,'--source-tier','3','--target-scale','3',
                         '--reference-root',self.root/'references','--frame-set-classification',
                         'independent-static-frame-catalog-v1','--output-root',output,'--out',batch,ok=False)
        self.assertIn('MASTER_VISIBLE_SCALE_TOO_SMALL',result['error'])
        self.assertFalse(batch.exists())
        self.assertFalse(output.exists())

    def test_incomplete_static_catalog_coverage_blocks_plan(self):
        one_frame=self.root/'only-frame'/self.identity;one_frame.mkdir(parents=True)
        (one_frame/'0_0.png').write_bytes((self.sources/'0_0.png').read_bytes())
        batch=self.root/'incomplete.json';output=self.root/'incomplete-output'
        result=self.call('expand-plan','--source',one_frame/'0_0.png','--source-tier','3','--target-scale','3',
                         '--reference-root',self.root/'references','--frame-set-classification',
                         'independent-static-frame-catalog-v1','--output-root',output,'--out',batch,ok=False)
        self.assertIn('ANIMATION_OFFICIAL_FRAME_SET_MISMATCH',result['error'])
        self.assertFalse(batch.exists());self.assertFalse(output.exists())

class AuthoredEffectRoiContract(unittest.TestCase):
    def setUp(self):
        parent=PRODUCT/'build/tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='authored-effect-roi-',dir=parent)
        self.root=Path(self.temp.name).resolve();self.identity='TESTROI'
        self.sources=self.root/'masters'/self.identity;self.sources.mkdir(parents=True)
        self.references=self.root/'references'/self.identity;self.references.mkdir(parents=True)
        self.roi=self.root/'cloud-roi.png'
        mask=Image.new('L',(60,60),0);ImageDraw.Draw(mask).rectangle((18,5,42,26),fill=255);mask.save(self.roi)
        base=Image.new('RGBA',(20,20),(0,0,0,0));draw=ImageDraw.Draw(base)
        draw.rectangle((2,10,17,18),fill=(80,95,70,255));draw.rectangle((0,19,19,19),fill=(55,60,50,255))
        base.save(self.references/'0_0.png')
        base.resize((60,60),Image.Resampling.NEAREST).save(self.sources/'0_0.png')
        for frame,color in enumerate((190,190,190),1):
            official=Image.new('RGBA',(20,20),(0,0,0,0))
            x=7+frame%2
            ImageDraw.Draw(official).ellipse((x,3,x+3,6),fill=(235,color,95,255))
            official.save(self.references/f'0_{frame}.png')
            source=Image.new('RGBA',(60,60),(0,0,0,0))
            ImageDraw.Draw(source).ellipse((x*3,9,(x+3)*3,18),fill=(235,color,95,255))
            source.save(self.sources/f'0_{frame}.png')
        self.output=self.root/'output';self.batch=self.root/'batch.json'

    def tearDown(self):self.temp.cleanup()

    def call(self,*args,ok=True):
        p=subprocess.run([str(EXE),*map(str,args)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        self.assertEqual(p.returncode==0,ok,(p.stdout+p.stderr)[-4000:])
        return json.loads(p.stdout if p.stdout.strip() else p.stderr)

    def args(self):
        result=['expand-plan']
        for source in sorted(self.sources.glob('0_*.png')):result+=['--source',str(source)]
        result+=['--source-tier','3','--target-scale','3','--reference-root',self.root/'references',
                 '--animation-contract','static-base-authored-fx-roi-v1','--animation-roi-mask',self.roi,
                 '--output-root',self.output,'--out',self.batch]
        return result

    def test_authored_roi_freezes_base_and_blocks_frame_pulses(self):
        self.call(*self.args())
        batch=json.loads(self.batch.read_bytes())
        self.assertEqual('static-base-authored-fx-roi-v1',batch['animation_contract'])
        self.assertEqual(sha(self.roi),batch['animation_gate']['authored_effect_roi']['sha256'])
        self.assertNotIn('max_roi_fraction',batch['animation_gate']['authored_effect_limits'])
        checked=self.call('expand-check','--batch',self.batch)
        self.assertEqual(0,checked['blocked'])
        group=checked['animation_registration']['groups'][0]
        self.assertEqual(0,group['static_base']['outside_roi_changes'])
        self.assertEqual('PASS',group['dynamic_fx']['status'])
        self.call('expand','--batch',self.batch,'--workers',2,'--memory-gib',1)
        verified=self.call('expand-verify','--batch',self.batch,'--workers',2,'--memory-gib',1)
        self.assertEqual('COMPLETED',verified['status'])

    def _authored_roi_5x_fixture(self, full_frames):
        source_root=self.root/('full-source' if full_frames else 'base-source')/'TESTROI'
        reference_root=self.root/('full-reference' if full_frames else 'base-reference')/'TESTROI'
        source_root.mkdir(parents=True);reference_root.mkdir(parents=True)
        clean_reference_base=Image.open(self.references/'0_0.png').convert('RGBA')
        official_base=clean_reference_base.copy()
        ImageDraw.Draw(official_base).ellipse((2,0,17,8),fill=(72,78,88,255))
        official_base.save(reference_root/'0_0.png')
        source_base_reference=clean_reference_base.copy()
        if full_frames:
            ImageDraw.Draw(source_base_reference).ellipse((8,0,11,5),fill=(110,115,125,220))
        source_base=source_base_reference.resize((100,100),Image.Resampling.NEAREST)
        source_base.save(source_root/'0_0.png')
        for frame in range(1,4):
            native_overlay=Image.new('RGBA',(20,20),(0,0,0,0))
            ImageDraw.Draw(native_overlay).ellipse((7,3,10,6),fill=(235,190,95,255))
            source_overlay=native_overlay.resize((100,100),Image.Resampling.NEAREST)
            if full_frames:
                Image.alpha_composite(official_base,native_overlay).save(reference_root/f'0_{frame}.png')
                Image.alpha_composite(source_base,source_overlay).save(source_root/f'0_{frame}.png')
            else:
                native_overlay.save(reference_root/f'0_{frame}.png')
                source_overlay.save(source_root/f'0_{frame}.png')
        roi=Image.new('L',(100,100),0);ImageDraw.Draw(roi).rectangle((0,0,99,44),fill=255)
        roi_path=self.root/('full-frame-roi.png' if full_frames else 'base-overlay-roi.png');roi.save(roi_path)
        return source_root,reference_root,roi_path

    def test_authored_base_roi_v2_allows_clean_base_against_official_smoke_base(self):
        source_root,reference_root,roi_path=self._authored_roi_5x_fixture(False)
        batch=self.root/'base-v2.json';output=self.root/'base-v2-output';args=['expand-plan']
        for source in sorted(source_root.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--base-width','20','--base-height','20','--target-scale','3','--reference-root',reference_root.parent,
               '--animation-contract','static-base-authored-fx-roi-v2','--animation-roi-mask',roi_path,
               '--output-root',output,'--out',batch]
        self.call(*args)
        sealed=json.loads(batch.read_bytes())
        self.assertEqual('h3.animation.authored-base-overlay.scale-gate.v2',sealed['animation_gate']['schema'])
        checked=self.call('expand-check','--batch',batch)
        self.assertEqual(0,checked['blocked'])
        group=checked['animation_registration']['groups'][0]
        self.assertEqual(0,group['static_base']['outside_roi_changes'])
        self.assertLessEqual(abs(group['frames'][0]['normalized_width_ratio']-1.0)*100,5.0)
        self.assertLessEqual(abs(group['frames'][0]['normalized_height_ratio']-1.0)*100,5.0)
        self.assertEqual('PASS',group['dynamic_fx']['status'])

    def test_composite_authored_roi_v1_checks_complete_frame_semantics(self):
        source_root,reference_root,roi_path=self._authored_roi_5x_fixture(True)
        batch=self.root/'composite.json';output=self.root/'composite-output';args=['expand-plan']
        for source in sorted(source_root.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--base-width','20','--base-height','20','--target-scale','3','--reference-root',reference_root.parent,
               '--animation-contract','static-composite-authored-fx-roi-v1','--animation-roi-mask',roi_path,
               '--output-root',output,'--out',batch]
        self.call(*args)
        sealed=json.loads(batch.read_bytes())
        self.assertEqual('full-frame-per-frame',sealed['animation_gate']['render_semantics'])
        checked=self.call('expand-check','--batch',batch)
        self.assertEqual(0,checked['blocked'])
        group=checked['animation_registration']['groups'][0]
        self.assertEqual('PASS',group['dynamic_fx']['status'])
        self.assertEqual(50,group['frames'][0]['source_structure_box'][1])
        self.assertEqual(10,group['frames'][0]['official_structure_box'][1])
        self.assertIn('outside',group['structure_geometry']['measurement'])

    def test_composite_authored_roi_v2_fits_structure_and_preserves_full_cels(self):
        source_root,reference_root,roi_path=self._authored_roi_5x_fixture(True)
        batch=self.root/'composite-v2.json';output=self.root/'composite-v2-output';args=['expand-plan']
        for source in sorted(source_root.glob('0_*.png')):args+=['--source',str(source)]
        args+=['--base-width','20','--base-height','20','--target-scale','3','--reference-root',reference_root.parent,
               '--animation-contract','static-composite-authored-fx-roi-v2','--animation-roi-mask',roi_path,
               '--output-root',output,'--out',batch]
        self.call(*args)
        sealed=json.loads(batch.read_bytes())
        self.assertEqual('static-composite-authored-fx-roi-v2',sealed['animation_contract'])
        transform=sealed['items'][0]['animation_group_transform']
        self.assertEqual('authored-static-structure-v2',transform['fit_basis'])
        self.assertEqual([0,50,100,100],transform['source_structure_box'])
        self.assertEqual([0,10,20,20],transform['official_structure_box_1x'])
        self.assertEqual('full-frame-per-frame',sealed['animation_gate']['render_semantics'])
        checked=self.call('expand-check','--batch',batch)
        self.assertEqual(0,checked['blocked'])
        group=checked['animation_registration']['groups'][0]
        self.assertEqual('PASS',group['dynamic_fx']['status'])
        for frame in group['frames']:
            self.assertLessEqual(abs(frame['normalized_width_ratio']-1.0)*100,5.0)
            self.assertLessEqual(abs(frame['normalized_height_ratio']-1.0)*100,5.0)
        self.call('expand','--batch',batch,'--workers',2,'--memory-gib',1)
        verified=self.call('expand-verify','--batch',batch,'--workers',2,'--memory-gib',1)
        self.assertEqual('COMPLETED',verified['status'])
        self.assertEqual(0,verified['failed'])

    def test_authored_roi_rejects_motion_outside_mask(self):
        rogue=Image.open(self.sources/'0_2.png').convert('RGBA');ImageDraw.Draw(rogue).point((5,45),fill=(255,255,255,255));rogue.save(self.sources/'0_2.png')
        blocked=self.call(*self.args(),ok=False)
        self.assertIn('ANIMATION_AUTHORED_FX_OUTSIDE_ROI',blocked['error'])
        self.assertFalse(self.batch.exists());self.assertFalse(self.output.exists())

    def test_authored_roi_rejects_luminance_pulse(self):
        for frame,color in enumerate((45,250),2):
            source=Image.new('RGBA',(60,60),(0,0,0,0))
            x=8+frame%2
            ImageDraw.Draw(source).ellipse((x*3,9,(x+3)*3,18),fill=(255,color,65,255))
            source.save(self.sources/f'0_{frame}.png')
        blocked=self.call(*self.args(),ok=False)
        self.assertIn('ANIMATION_AUTHORED_FX_LUMA_PULSE',blocked['error'])
        self.assertFalse(self.batch.exists());self.assertFalse(self.output.exists())

if __name__=='__main__':
    suite=unittest.TestSuite()
    for case in (ExpansionContract, NativeAuthorityContract, StaticFxGroupFitContract, FrameSetClassificationContract, AuthoredEffectRoiContract):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    print(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,'synthetic_only':True,
                      'game_writes':0,'real_user_approvals':0}))
    raise SystemExit(0 if result.wasSuccessful() else 1)

