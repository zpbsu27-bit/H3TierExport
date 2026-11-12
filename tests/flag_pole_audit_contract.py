"""Contracts for 1999 Complete DEF geometry and native flag-face review."""
from __future__ import annotations
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / 'scripts' / 'flag_pole_audit.py'
spec = importlib.util.spec_from_file_location('flag_pole_audit', SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)

class Native1999FlagAuditContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refs, cls.reference_sha, cls.doc = audit.load_reference()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h3-1999-flag-audit-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, size):
        p = self.root / name
        Image.new('RGBA', size, (0, 0, 0, 0)).save(p)
        return p

    def freeze(self, identity, size, group=0, frame=0, name='candidate.png'):
        p = self.image(name, size)
        result = audit.freeze_images([identity + '=' + str(p)], None, None, 'auto', None, 16.0,
                                    'test-1999', self.root / ('review-' + str(len(list(self.root.glob('review-*'))))),
                                    self.refs, self.reference_sha, self.doc,
                                    native_group=group, native_frame=frame)
        manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        return p, result, manifest

    def layout(self, result, item, poles, confirmed=False):
        value = {'schema': audit.LAYOUT_SCHEMA, 'manifest_sha256': result['manifest_sha256'],
                 'reference_database_sha256': self.reference_sha,
                 'items': [{'identity': item['identity'], 'group': item['group'], 'frame': item['frame'],
                            'image_sha256': item['image_sha256'], 'pixels_per_1x': [item['scale_num'], item['scale_den']],
                            'native_origin_px': item['native_origin_px'], 'registration_confirmed': confirmed,
                            'target_length_1x': 16.0, 'poles': poles}]}
        path = self.root / ('layout-' + str(len(list(self.root.glob('layout-*')))) + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_original_archive_scope_and_mine_examples(self):
        self.assertEqual(self.doc['schema'], 'h3.flag-pole-reference-1999.v2')
        self.assertEqual(self.doc['counts']['identities'], 2565)
        self.assertEqual(self.doc['counts']['logical_frames'], 34324)
        self.assertEqual(self.doc['counts']['mine_identities'], 49)
        self.assertEqual(self.doc['counts']['mine_logical_frames'], 474)
        self.assertEqual(self.doc['provenance']['archive_sha256'], '57caf2c50573f33a0d91e4222e51d3a73c136d44decf59dde21cacad88fe5d66')
        self.assertNotIn('flag_glyph_canvas_at_3x', self.doc['provenance'])
        for ident, origin, crop in [('AVMSAWD0',[0,30],[160,66]), ('AVMSAWG0',[0,30],[160,66]),
                                    ('AVMSAWL0',[0,23],[160,73]), ('AVMSAWR0',[0,22],[160,74])]:
            frame = audit.selected_frame(self.refs[ident],0,0)
            self.assertEqual(frame['canvas_1x'], [160,96]); self.assertEqual(frame['crop_1x'],crop)
            self.assertEqual(frame['origin_1x'],origin)
            self.assertEqual(frame['flags']['anchors'][0]['bbox_1x'],[92,78,106,83])
            self.assertEqual(frame['flags']['anchors'][0]['attachment_edge_pixel_1x'],[105,78])
        self.assertIn('AVMABMG', self.refs)
        self.assertNotIn('AVMABMG0', self.refs)
        self.assertEqual(len(self.refs['AVMGESW0']['frames']),16)
        self.assertEqual(audit.selected_frame(self.refs['AVMGESW0'],0,15)['origin_1x'],[0,1])

    def test_all_mine_frames_match_separately_extracted_native_inventory(self):
        source = Path(r'{H3-assets}\codex-map\native-resource-inventory.json.gz')
        if not source.is_file(): self.skipTest('local 1999 inventory unavailable')
        data = json.loads(gzip.decompress(source.read_bytes()))['rows']
        rows = {(r['identity'],r['group'],r['frame']):r for r in data
                if r.get('kind')=='def' and r.get('native_lod','').lower()=='h3sprite.lod'}
        checked = 0
        for ident,entry in self.refs.items():
            if not ident.startswith('AVM'): continue
            for f in entry['frames']:
                r=rows[(ident,f['group'],f['frame'])]
                self.assertEqual((f['canvas_1x'],f['crop_1x'],f['origin_1x']),
                                 (r['canvas'],r['crop'],r['anchor']), (ident,f['frame']))
                checked += 1
        self.assertEqual(checked,474)

    def test_registration_distinguishes_full_canvas_from_cropped_frame(self):
        source,result,manifest=self.freeze('AVMSAWR0',(480,288))
        item=manifest['items'][0]
        self.assertEqual(item['registration_status'],'exact_native_canvas_integer_multiple')
        self.assertEqual((item['scale_num'],item['native_origin_px']),(3,[0,0]))
        self.assertEqual(item['native_geometry']['origin_1x'],[0,22])
        _,_,cropped=self.freeze('AVMSAWR0',(480,222),name='crop.png')
        ci=cropped['items'][0]
        self.assertEqual(ci['registration_status'],'exact_native_crop_integer_multiple')
        self.assertEqual((ci['scale_num'],ci['native_origin_px']),(3,[0,-66]))
        self.assertEqual(audit.sha(source),item['image_sha256'])

    def test_1999_flag_edge_is_not_claimed_as_pole_tip(self):
        _,result,manifest=self.freeze('AVMSAWD0',(480,288))
        item=manifest['items'][0]; layout=self.layout(result,item,[{'head_px':[315,234],'foot_px':[315,282]}],confirmed=True)
        report=audit.verify_layout(Path(result['manifest']),result['manifest_sha256'],layout,audit.sha(layout),
                                   self.root/'measured',self.refs,self.reference_sha)
        row=report['items'][0]
        self.assertEqual(row['native_flag_face_bbox_1x'],[92,78,106,83])
        self.assertEqual(row['native_flag_attachment_edge_pixel_1x'],[105,78])
        self.assertEqual(row['position_delta_observed_minus_flag_edge_1x'],[0.0,0.0])
        self.assertIn('不等于已证杆顶',row['ai_edit_instruction'])
        self.assertIsNone(report['official_pole_length'])
        self.assertFalse(report['mod_written'])

    def test_partial_original_flag_is_never_promoted_to_exact(self):
        frame=audit.selected_frame(self.refs['AVMSWSN0'],0,0)
        self.assertEqual(frame['flags']['status'],'PARTIAL')
        self.assertEqual(frame['flags']['anchors'],[])
        self.assertEqual(frame['flags']['partial_flags'][0]['observed_bbox_1x'],[110,5,123,10])
        self.assertEqual(len(frame['flags']['partial_flags'][0]['face_candidates']),2)
        _,result,manifest=self.freeze('AVMSWSN0',(480,192))
        item=manifest['items'][0]; layout=self.layout(result,item,[])
        report=audit.verify_layout(Path(result['manifest']),result['manifest_sha256'],layout,audit.sha(layout),
                                   self.root/'partial-measure',self.refs,self.reference_sha)
        self.assertEqual(report['status'],'REVIEW_REQUIRED'); self.assertEqual(report['items'],[])

    def test_non_native_sized_master_requires_explicit_registration_confirmation(self):
        _,result,manifest=self.freeze('AVMSAWD0',(1952,806))
        item=manifest['items'][0]; self.assertEqual(item['registration_status'],'registration_required')
        layout=self.layout(result,item,[{'head_px':[100,100],'foot_px':[100,150]}])
        with self.assertRaisesRegex(audit.AuditError,'REGISTRATION_REQUIRED'):
            audit.verify_layout(Path(result['manifest']),result['manifest_sha256'],layout,audit.sha(layout),
                                self.root/'must-fail',self.refs,self.reference_sha)
        value=json.loads(layout.read_text(encoding='utf-8'));value['items'][0]['registration_confirmed']=True
        layout.write_text(json.dumps(value),encoding='utf-8')
        audit.verify_layout(Path(result['manifest']),result['manifest_sha256'],layout,audit.sha(layout),
                            self.root/'confirmed',self.refs,self.reference_sha)

    def test_frame_selection_and_unresolved_geometry_fail_closed(self):
        _,result,manifest=self.freeze('AVMGESW0',(288,192),frame=15)
        self.assertEqual(manifest['items'][0]['frame'],15)
        self.assertEqual(manifest['items'][0]['native_geometry']['crop_1x'],[96,63])
        p=self.image('invalid.png',(960,960))
        with self.assertRaisesRegex(audit.AuditError,'NATIVE_GEOMETRY_UNRESOLVED'):
            audit.freeze_images(['SGTWMTA='+str(p)],None,None,'auto',None,None,None,self.root/'invalid',
                                self.refs,self.reference_sha,self.doc)
        with self.assertRaisesRegex(audit.AuditError,'NO_1999_DEF_IDENTITY'):
            audit.freeze_images(['AVMABMG0='+str(p)],None,None,'auto',None,None,None,self.root/'unknown',
                                self.refs,self.reference_sha,self.doc)

    def test_manifest_can_bind_multiple_native_frames_of_one_def(self):
        a=self.image('frame0.png',(288,192)); b=self.image('frame15.png',(288,192))
        manifest={'schema':audit.MANIFEST_SCHEMA,'reference_database_sha256':self.reference_sha,
                  'items':[{'identity':'AVMGESW0','group':0,'frame':n,'image_path':str(p),
                            'image_sha256':audit.sha(p),'pixels_per_1x':3,'native_origin_px':[0,0]}
                           for n,p in [(0,a),(15,b)]]}
        path=self.root/'batch.json';path.write_text(json.dumps(manifest),encoding='utf-8')
        checked=audit.checked_manifest(path,audit.sha(path),self.refs,self.reference_sha)
        result=audit.freeze_images([r['identity']+'='+r['image_path'] for r in checked['items']],None,None,
                                   'auto',None,None,None,self.root/'batch-review',self.refs,self.reference_sha,
                                   self.doc,checked['items'])
        frozen=json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        self.assertEqual([(r['group'],r['frame']) for r in frozen['items']],[(0,0),(0,15)])
        self.assertNotEqual(frozen['items'][0]['image_url'],frozen['items'][1]['image_url'])

    def test_native_slot_filename_is_resolved_from_parent_def_directory(self):
        folder=self.root/'AVMGESW0';folder.mkdir()
        source=folder/'0_15.png';Image.new('RGBA',(288,192),(0,0,0,0)).save(source)
        result=audit.freeze_images([str(source)],None,None,'auto',None,None,None,self.root/'named-slot',
                                   self.refs,self.reference_sha,self.doc)
        item=json.loads(Path(result['manifest']).read_text(encoding='utf-8'))['items'][0]
        self.assertEqual((item['identity'],item['group'],item['frame']),('AVMGESW0',0,15))
        self.assertEqual(item['native_geometry']['origin_1x'],[0,1])

    def test_forged_exact_registration_is_rejected(self):
        p=self.image('wrong-size.png',(400,200))
        manifest={'schema':audit.MANIFEST_SCHEMA,'reference_database_sha256':self.reference_sha,
                  'items':[{'identity':'AVMSAWD0','image_path':str(p),'image_sha256':audit.sha(p),
                            'pixels_per_1x':3,'native_origin_px':[0,0],
                            'registration_status':'exact_native_canvas_integer_multiple'}]}
        path=self.root/'forged.json';path.write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaisesRegex(audit.AuditError,'NATIVE_REGISTRATION_MISMATCH'):
            audit.checked_manifest(path,audit.sha(path),self.refs,self.reference_sha)

    def test_runtime_resolves_compressed_native_catalog(self):
        runtime=self.root/'runtime'; data=runtime/'scripts'/'data';data.mkdir(parents=True)
        shutil.copy2(SCRIPT,runtime/'flag_pole_audit.py')
        shutil.copy2(audit.REFERENCE,data/'flag_pole_reference_1999.json.gz')
        rs=importlib.util.spec_from_file_location('flag_pole_runtime_fixture',runtime/'flag_pole_audit.py')
        module=importlib.util.module_from_spec(rs);rs.loader.exec_module(module)
        refs,sha,doc=module.load_reference()
        self.assertEqual(len(refs),2565);self.assertEqual(sha,self.reference_sha)
        self.assertEqual(doc['counts']['mine_logical_frames'],474)

    def test_no_arguments_preserves_dispatcher_usage_contract(self):
        result=subprocess.run([sys.executable,'-B',str(SCRIPT)],capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('usage:',result.stderr.lower())
        self.assertIn('required',result.stderr.lower())

if __name__=='__main__': unittest.main()
