"""Counterexamples for lossless layout: body scale is not the alpha envelope."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import subject_scale_layout as mod


def ref(p, pixels=None):
    r={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    if pixels:r['pixels']=pixels
    return r


class LayoutContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.inventory=self.root/'native.json.gz'
        self.inventory.write_bytes(gzip.compress(json.dumps({'rows':[{'kind':'def','identity':'AHFIX_',
            'group':0,'frame':0,'category':'other','canvas':[30,30],'crop':[28,28],'anchor':[1,1]}]}).encode()))
        self.source=self.root/'genuine-master.png';im=Image.new('RGBA',(180,180))
        im.paste((120,50,10,255),(70,30,110,150));im.putpixel((68,35),(17,31,42,1));im.save(self.source)
        self.reference=self.root/'reference-master.png';self.reference.write_bytes(self.source.read_bytes())
        self.standard=self.root/'standard.json';self.standard.write_text('{"version":"test-standard"}')
        self.profile=self.root/'profile.json'
        self.profile_data={'schema':mod.PROFILE,'review_status':'assistant_scale_profile_pass',
            'replaces_canonical_art_standard':False,'art_standard':{**ref(self.standard),'version':'test-standard'},
            'reference_images':[ref(self.reference,[180,180])],
            'directions':{'front':{'anchor_native_canvas':[15,28],
                'lengths_native':{'body':20,'torso':10},'primary_metric':'body',
                'primary_tolerance_fraction':.1,'secondary_tolerance_fraction':.25}}}
        self.profile.write_text(json.dumps(self.profile_data))
        self.manifest=self.root/'request.json'
        self.data={'schema':mod.SCHEMA,'operation_id':'fixture','native_inventory':ref(self.inventory),
            'profile':ref(self.profile),'expected_slots':[['def','AHFIX_',0,0]],'items':[
            {'key':['def','AHFIX_',0,0],'profile_direction':'front','source':ref(self.source,[180,180]),
             'measurement':{'image_sha256':ref(self.source)['sha256'],'coordinate_space':'source_pixels',
                 'method':'visual-anatomical-landmarks','anchor_source':[90,149],
                 'paths':{'body':[[90,30],[90,149]],'torso':[[90,60],[90,119]]}}}]}
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.data));return ref(self.manifest)

    def resolve(self):return mod.resolve(self.save(),'fixture')[2]

    def test_hero_category_and_all_positive_alpha_are_preserved_without_resize(self):
        result,out=self.resolve()[0]
        self.assertEqual(result['status'],'LOSSLESS_LAYOUT_READY')
        self.assertFalse(result['pixels_resampled']);self.assertFalse(result['alpha_bbox_used_as_body_measurement'])
        self.assertEqual(out.getchannel('A').histogram()[1],1)
        self.assertEqual(sum(out.getchannel('A').histogram()[1:]),4801)
        self.assertLessEqual(abs(result['body_deviation_fraction']['body']),.1)

    def test_long_weapon_cannot_be_mistaken_for_a_larger_body(self):
        base=self.resolve()[0][0]['desired_k']
        im=Image.open(self.source);im.putpixel((90,10),(80,80,80,255));im.save(self.source)
        self.data['items'][0]['source']=ref(self.source,[180,180]);self.data['items'][0]['measurement']['image_sha256']=ref(self.source)['sha256']
        result,_=self.resolve()[0]
        self.assertEqual(result['desired_k'],base)

    def test_native_canvas_is_registered_padding_not_a_second_sample(self):
        crop_result,crop=self.resolve()[0]
        self.data['output_layout']='native-canvas';result,canvas=self.resolve()[0];k=result['editing_scale']
        self.assertEqual(canvas.size,(30*k,30*k))
        self.assertEqual(canvas.crop((k,k,29*k,29*k)).tobytes(),crop.tobytes())
        self.assertEqual(result['translation_source_to_output'],[v+k for v in crop_result['translation_source_to_crop']])
        self.assertFalse(result['pixels_resampled'])

    def test_fit_conflict_requests_redraw_and_does_not_squeeze_or_erase(self):
        im=Image.open(self.source);im.putpixel((0,0),(1,2,3,1));im.save(self.source)
        self.data['items'][0]['source']=ref(self.source,[180,180]);self.data['items'][0]['measurement']['image_sha256']=ref(self.source)['sha256']
        result,out=self.resolve()[0]
        self.assertEqual(result['status'],'NEEDS_REDRAW');self.assertIsNone(out)

    def test_fractional_canvas_density_recovers_a_real_gap_between_integer_choices(self):
        self.profile_data['directions']['front']['primary_tolerance_fraction']=.002
        self.profile_data['directions']['front']['lengths_native']['body']=20.3
        self.profile.write_text(json.dumps(self.profile_data));self.data['profile']=ref(self.profile)
        result,out=self.resolve()[0]
        self.assertEqual(result['status'],'NEEDS_REDRAW');self.assertIsNone(out)
        self.data['output_layout']='native-canvas';result,out=self.resolve()[0]
        self.assertEqual(result['status'],'LOSSLESS_LAYOUT_READY')
        self.assertIn('editing_scale_exact',result)
        self.assertEqual(out.size[0]*30,out.size[1]*30)
        self.assertEqual(sum(out.getchannel('A').histogram()[1:]),4801)
        self.assertEqual(out.getchannel('A').histogram()[1],1)
        self.assertFalse(result['pixels_resampled'])
        self.assertLessEqual(abs(result['body_deviation_fraction']['body']),.002)

    def test_fractional_density_replays_exact_pixels_and_does_not_widen_fit(self):
        self.profile_data['directions']['front']['primary_tolerance_fraction']=.002
        self.profile_data['directions']['front']['lengths_native']['body']=20.3
        self.profile.write_text(json.dumps(self.profile_data));self.data['profile']=ref(self.profile)
        self.data['output_layout']='native-canvas'
        with patch.object(mod,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/fixture';frozen=self.save()
            mod.execute('run',frozen,str(out),'fixture')
            self.assertEqual(mod.execute('verify',frozen,str(out),'fixture')['status'],'VERIFIED_LOSSLESS_LAYOUT')
        image=Image.open(self.source);image.putpixel((0,0),(1,2,3,1));image.save(self.source)
        self.data['items'][0]['source']=ref(self.source,[180,180]);self.data['items'][0]['measurement']['image_sha256']=ref(self.source)['sha256']
        result,image=self.resolve()[0];self.assertEqual(result['status'],'NEEDS_REDRAW');self.assertIsNone(image)

    def test_conflicting_anatomy_is_not_corrected_by_nonuniform_stretch(self):
        self.data['items'][0]['measurement']['paths']['torso']=[[80,60],[85,70]]
        result,out=self.resolve()[0]
        self.assertEqual(result['status'],'NEEDS_REDRAW');self.assertIsNone(out)

    def test_missing_master_gets_explicit_redraw_brief(self):
        self.data['items'][0]['source']=None
        result,out=self.resolve()[0]
        self.assertEqual(result['reason'],'MISSING_HQ_MASTER')
        self.assertIn('GPT',mod.brief(self.profile_data,[(result,out)]))

    def test_source_sha_drift_and_missing_annotations_are_rejected(self):
        self.data['items'][0]['source']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):self.resolve()

    def test_annotations_must_be_for_exact_source(self):
        self.data['items'][0]['measurement']['image_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'SOURCE_BOUND_MANUAL'):self.resolve()

    def test_expected_scope_missing_or_duplicate_slot_is_rejected(self):
        self.data['expected_slots'].append(['def','AHFIX_',0,1])
        with self.assertRaisesRegex(ValueError,'EXACT_SCOPE_COVERAGE'):self.resolve()

    def test_output_receipt_replay_and_tamper(self):
        with patch.object(mod,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/fixture'
            frozen=self.save();before=self.source.read_bytes()
            checked=mod.execute('check',frozen,str(out),'fixture');self.assertFalse(out.exists())
            self.assertEqual(checked['ready'],1)
            mod.execute('run',frozen,str(out),'fixture')
            self.assertEqual(mod.execute('verify',frozen,str(out),'fixture')['status'],'VERIFIED_LOSSLESS_LAYOUT')
            self.assertEqual(self.source.read_bytes(),before)
            p=out/'AHFIX_/0_0.png';p.write_bytes(p.read_bytes()+b'tamper')
            with self.assertRaisesRegex(ValueError,'OUTPUT_PNG_SHA_DRIFT'):mod.execute('verify',frozen,str(out),'fixture')

    def test_existing_foreign_directory_is_never_overwritten(self):
        with patch.object(mod,'PRODUCT',self.root):
            out=self.root/'build/experiments/scale-layout/fixture';out.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,'FOREIGN_OR_PARTIAL'):mod.execute('run',self.save(),str(out),'fixture')


if __name__=='__main__':unittest.main()
