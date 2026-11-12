"""Exercise the actual CPU expansion EXE with disposable synthetic PNGs."""
from pathlib import Path
import copy, hashlib, json, subprocess, tempfile, unittest
from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
EXE = PRODUCT / 'build/candidate/H3TierExport.exe'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False)+'\n', encoding='utf-8')

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

if __name__=='__main__':
    suite=unittest.TestSuite()
    for case in (ExpansionContract, NativeAuthorityContract):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    print(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,'synthetic_only':True,
                      'game_writes':0,'real_user_approvals':0}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
