"""Synthetic public-CLI contracts for frozen sheet/frame-view intake. No approved art is changed."""
from __future__ import annotations
from pathlib import Path
import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from PIL import Image
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import sheet_intake as sheet_module
import companion_runtime
EXE=Path(os.environ.get("H3TIEREXPORT_EXE",ROOT/"build/candidate/H3TierExport.exe"))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False)+"\n",encoding="utf-8")
def ref(p):return {"path":str(p.resolve()),"sha256":sha(p)}

class SheetIntakeContract(unittest.TestCase):
 def setUp(self):
  (ROOT/"build/tests").mkdir(parents=True,exist_ok=True)
  self.tmp=tempfile.TemporaryDirectory(prefix="sheet-intake-",dir=ROOT/"build/tests")
  self.d=Path(self.tmp.name).resolve()
  self.sheet=self.d/"sheet.png";self.proof=self.d/"frozen-parent.json"
  save(self.proof,{"purpose":"fixture; no user art approval"})
  self.manifest=self.d/"manifest.json";self.output=ROOT/"build/experiments/sheet-intake"/("contract-"+self.d.name.replace(".","-"))
  self.geometry={"canvas":[6,4],"crop":[4,3],"origin":[1,1]}
  self.views=[]
  for i,(x,y) in enumerate(((0,0),(12,0),(0,8))):
   self.views.append({"id":"F"+str(i),"role":"frame","rect_px":[x,y,12,8],
      "key":["def","SYNTH",5,i],"geometry":copy.deepcopy(self.geometry),
      "natural_k":2,"art_status":"UNSELECTED"})
  self.views.append({"id":"EMPTY","role":"empty","rect_px":[12,8,12,8]})
  self.pix=Image.new("RGBA",(24,16),(0,0,0,0))
  for x,y in ((4,3),(16,3),(4,11)):self.pix.putpixel((x,y),(120,30,200,1))
  self.pix.save(self.sheet)
  self.refresh()
 def tearDown(self):self.tmp.cleanup()
 def refresh(self):
  with Image.open(self.sheet) as im:rgbhash=hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest()
  self.data={"schema":"h3.sheet-intake.v1","operation_id":self.output.name,
   "source":{**ref(self.sheet),"pixels":[24,16],"rgba_sha256":rgbhash},
   "references":[{"role":"parent_frozen","ref":ref(self.proof)}],
   "views":self.views}
  save(self.manifest,self.data)
 def call(self,stage,good=True,out=None):
  out=self.output if out is None else out
  command=[str(EXE),"sheet-intake",stage,"--manifest",str(self.manifest),
    "--manifest-sha256",sha(self.manifest),"--outdir",str(out),
    "--operation-id",self.data["operation_id"]]
  p=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",
                   errors="replace",timeout=90)
  self.assertEqual(p.returncode==0,good,(p.stdout+p.stderr)[-4500:])
  data=json.loads(p.stdout if p.stdout.strip() else p.stderr)
  return data
 def test_good_three_views_unused_empty_no_image_outputs(self):
  a=self.call("check");self.assertEqual(a["status"],"REVIEW_REQUIRED")
  self.assertEqual(a["summary"]["positive_unowned"],0)
  self.assertFalse(self.output.exists())
  self.call("run")
  self.assertTrue((self.output/"review-report.json").is_file())
  self.assertTrue((self.output/"index.html").is_file())
  self.assertTrue((self.output/"analysis-complete.json").is_file())
  self.assertFalse(list(self.output.glob("*.png")))
  self.assertEqual(self.call("verify")["status"],"VERIFIED_REVIEW_REQUIRED")

 def prepared_fixture(self):
  native=self.d/'official.json.gz';rows=[];slots=[]
  for frame in range(3):
   guide=self.d/('native-'+str(frame)+'.png');Image.new('RGBA',(4,3),(80,90,100,255)).save(guide)
   rows.append({'kind':'def','identity':'SYNTH','category':'creatures','group':5,'frame':frame,
                'canvas':[6,4],'crop':[4,3],'anchor':[1,1],'native_lod':'H3sprite.lod'})
   slots.append({'key':['def','SYNTH',5,frame],'crop_path':str(guide),'crop_sha256':sha(guide),
                 'targets':{'canvas_1x':[6,4],'crop_1x':[4,3],'origin_1x':[1,1]}})
  native.write_bytes(gzip.compress(json.dumps({'schema':'h3.complete-art-catalog.v3','rows':rows}).encode(),mtime=0))
  request=self.d/'prepared-request.json';save(request,{'schema':'h3.full-art.request.v1','frame_count':3,
                                                    'grid':[2,2],'original_slots':slots})
  self.labels=self.d/'owner-labels.bin.gz';labels=np.full((16,24),255,dtype=np.uint8)
  labels[3,4]=0;labels[3,16]=1;labels[11,4]=2
  self.labels.write_bytes(gzip.compress(labels.tobytes(),mtime=0))
  self.prepared=self.d/'prepared-proof.json'
  self.prepared_data={'schema':'h3.ai-sprite.exact-positive-alpha-partition.v2','sprite_count':3,
    'request_path':str(request),'request_sha256':sha(request),'raw_output':str(self.sheet),
    'raw_sha256':sha(self.sheet),'raw_pixels':[24,16],'label_map':str(self.labels),
    'label_map_sha256':sha(self.labels),'grid':[2,2],'observed_major_grid_cells':[0,1,2],
    'visible_pixels':3,'sprite_pixels':[1,1,1],'human_approved':False,'alpha_threshold':1,
    'raw_png_unchanged':True,'all_positive_alpha_pixels_assigned_exactly_once':True,
    'resampling_performed':False,'new_art_pixels_created':False,'motion_inference':False}
  save(self.prepared,self.prepared_data)
  self.data={'schema':'h3.sheet-intake.prepared-partition.v1','operation_id':self.output.name,
             'prepared_proof':ref(self.prepared),'official_inventory':ref(native),'source_status':'NEEDS_KEYFRAME'}
  save(self.manifest,self.data)

 def reseal_prepared(self):
  self.prepared_data['label_map_sha256']=sha(self.labels);save(self.prepared,self.prepared_data)
  self.data['prepared_proof']=ref(self.prepared);save(self.manifest,self.data)

 def test_formal_prepared_input_uses_frozen_source_and_keeps_owner_hash_gates(self):
  self.prepared_fixture()
  source=b'# TEST_ONLY frozen sheet companion\n'
  row={'path':'sheet_intake.py','source_path':'scripts/sheet_intake.py',
       'bytes':len(source),'sha256':hashlib.sha256(source).hexdigest()}
  raw=(json.dumps({'schema':'h3.tier-export.companion-runtime.v1',
                   'entrypoints':{'sheet-intake':'sheet_intake.py'},'files':[row]})+'\n').encode()
  payload=self.d/('.runtime-'+hashlib.sha256(raw).hexdigest());payload.mkdir()
  (payload/'runtime-manifest.json').write_bytes(raw)
  frozen=payload/'sheet_intake.py';frozen.write_bytes(source)
  live=self.d/'scripts/sheet_intake.py';live.parent.mkdir();live.write_bytes(b'# unrelated WIP\n')
  renderer=self.d/'TEST-renderer.exe';renderer.write_bytes(b'TEST_ONLY renderer identity; not executed')
  build=self.d/'TEST-build.json'
  save(build,{'executable':{'sha256':sha(renderer)},
              'sources':[{'path':'scripts\\sheet_intake.py','sha256':row['sha256']}]})
  with mock.patch.object(sheet_module,'_read_build_receipt',return_value={'authority':'formal_release','path':str(build)}), \
       mock.patch.object(companion_runtime,'frozen_root',return_value=payload):
   result=sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer))
   self.assertTrue(result['summary']['ownership_partition_verified'])
   self.assertFalse(self.output.exists())
   live.write_bytes(b'# later unrelated WIP\n')
   self.assertEqual(sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer)),result)
   frozen.write_bytes(source+b'# tamper\n')
   with self.assertRaisesRegex(sheet_module.ReviewError,'RELEASE_COMPANION_SOURCE_DRIFT'):
    sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer))
   frozen.write_bytes(source)
   self.labels.write_bytes(self.labels.read_bytes()+b'owner drift')
   with self.assertRaises(sheet_module.ReviewError):
    sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer))
   self.assertFalse(self.output.exists())
   frozen.unlink()
   with self.assertRaises(OSError):
    sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer))

 def test_candidate_prepared_input_still_rejects_live_build_source_drift(self):
  self.prepared_fixture()
  renderer=self.d/'TEST-renderer.exe';renderer.write_bytes(b'TEST_ONLY renderer; never executed')
  build=self.d/'TEST-build.json';save(build,{'executable':{'sha256':sha(renderer)}})
  with mock.patch.object(sheet_module,'_read_build_receipt',return_value={'path':str(build)}), \
       mock.patch.object(sheet_module,'validate_build_sources',side_effect=RuntimeError('BUILD_SOURCE_DRIFT')) as check:
   with self.assertRaisesRegex(RuntimeError,'BUILD_SOURCE_DRIFT'):
    sheet_module.evaluate(self.manifest,sha(self.manifest),renderer,sha(renderer))
   check.assert_called_once()

 def test_prepared_proof_measures_sources_and_preserves_missing_keyframes(self):
  self.prepared_fixture();before=sha(self.sheet)
  result=self.call('check',good=False)
  self.assertTrue(result['summary']['ownership_partition_verified'])
  self.assertTrue(result['summary']['per_cell_containment_verified'])
  self.assertFalse(result['summary']['common_action_scale_verified'])
  self.assertTrue(all(f['status']=='NEEDS_KEYFRAME' for f in result['frames']))
  self.assertEqual([f['source_diagnostics']['alpha1_pixels'] for f in result['frames']],[1,1,1])
  self.call('run',good=False)
  self.assertEqual(self.call('verify',good=False)['status'],'VERIFIED_BLOCKED')
  self.assertFalse((self.output/'analysis-complete.json').exists())
  self.assertFalse(list(self.output.glob('*.png')))
  self.assertEqual(sha(self.sheet),before)

 def test_prepared_proof_all_positive_ownership_is_independently_checked(self):
  self.prepared_fixture();labels=np.full((16,24),255,dtype=np.uint8)
  self.labels.write_bytes(gzip.compress(labels.tobytes(),mtime=0));self.reseal_prepared()
  result=self.call('check',good=False)
  self.assertIn('PREPARED_POSITIVE_OWNERSHIP_MISMATCH',result['error'])
  self.assertFalse(self.output.exists())

 def test_prepared_label_bomb_and_native_geometry_are_rejected(self):
  self.prepared_fixture();self.labels.write_bytes(gzip.compress(b'0'*385,mtime=0));self.reseal_prepared()
  result=self.call('check',good=False);self.assertIn('PREPARED_LABEL_LENGTH_INVALID',result['error'])
  self.prepared_fixture()
  request=Path(self.prepared_data['request_path']);doc=json.loads(request.read_bytes())
  doc['original_slots'][0]['targets']['origin_1x']=[0,0];save(request,doc)
  self.prepared_data['request_sha256']=sha(request);self.reseal_prepared()
  result=self.call('check',good=False);self.assertIn('PREPARED_NATIVE_GEOMETRY_MISMATCH',result['error'])

 def test_prepared_alpha_floor_v3_is_explicit_and_preserves_below_floor_pixels(self):
  self.prepared_fixture()
  self.pix.putpixel((4,3),(120,30,200,8));self.pix.putpixel((16,3),(120,30,200,16));self.pix.putpixel((4,11),(120,30,200,7));self.pix.save(self.sheet)
  self.labels=np.full((16,24),255,dtype=np.uint8);self.labels[3,4]=0;self.labels[3,16]=1
  self.labels_file=self.d/'owner-labels-floor.bin.gz';self.labels_file.write_bytes(gzip.compress(self.labels.tobytes(),mtime=0))
  self.prepared_data.update({'schema':'h3.ai-sprite.alpha-floor-partition.v3','partition_alpha_floor':8,
    'all_partition_eligible_alpha_pixels_assigned_exactly_once':True,
    'sub_floor_positive_pixels_omitted':1,'sub_floor_positive_components_omitted':1,
    'sub_floor_maximum_alpha':7,
    'sub_floor_semantics':'unmodified/unmoved/unredrawn pixels; below-floor positive pixels are excluded from ownership only',
    'visible_pixels':2,'sprite_pixels':[1,1,0],
    'raw_sha256':sha(self.sheet),
    'label_map':str(self.labels_file),'label_map_sha256':sha(self.labels_file)})
  save(self.prepared,self.prepared_data);self.data['prepared_proof']=ref(self.prepared);save(self.manifest,self.data)
  result=self.call('check',good=False)
  self.assertEqual(result['summary']['partition_alpha_floor'],8)
  self.assertEqual(result['summary']['partition_eligible_pixels'],2)
  self.assertEqual(result['summary']['positive_omitted_by_floor'],1)
  self.assertEqual([frame['partition_alpha_floor'] for frame in result['frames']],[8,8,8])
  self.call('run',good=False)
  self.assertEqual(self.call('verify',good=False)['status'],'VERIFIED_BLOCKED')
  self.assertEqual(self.pix.getpixel((4,11))[3],7)

 def test_prepared_alpha_floor_v1_accepts_producer_floor_two_and_preserves_alpha_one(self):
  self.prepared_fixture()
  self.pix.putpixel((4,3),(120,30,200,2));self.pix.putpixel((16,3),(120,30,200,2));self.pix.putpixel((4,11),(120,30,200,1));self.pix.save(self.sheet)
  self.labels=np.full((16,24),255,dtype=np.uint8);self.labels[3,4]=0;self.labels[3,16]=1
  self.labels_file=self.d/'owner-labels-floor-two.bin.gz';self.labels_file.write_bytes(gzip.compress(self.labels.tobytes(),mtime=0))
  self.prepared_data.update({'schema':'h3.ai-sprite.exact-alpha-partition.v1','alpha_threshold':2,
    'all_alpha_ge2_pixels_assigned_exactly_once':True,
    'visible_pixels':2,'sprite_pixels':[1,1,0],
    'raw_sha256':sha(self.sheet),
    'label_map':str(self.labels_file),'label_map_sha256':sha(self.labels_file)})
  save(self.prepared,self.prepared_data);self.data['prepared_proof']=ref(self.prepared);save(self.manifest,self.data)
  result=self.call('check',good=False)
  self.assertEqual(result['summary']['partition_alpha_floor'],2)
  self.assertEqual(result['summary']['partition_eligible_pixels'],2)
  self.assertEqual(result['summary']['positive_omitted_by_floor'],1)
  self.assertEqual([frame['partition_alpha_floor'] for frame in result['frames']],[2,2,2])
  self.call('run',good=False)
  self.assertEqual(self.call('verify',good=False)['status'],'VERIFIED_BLOCKED')
  self.assertEqual(self.pix.getpixel((4,11))[3],1)

 def test_prepared_alpha_floor_v3_rejects_omission_count_drift(self):
  self.prepared_fixture()
  labels=np.full((16,24),255,dtype=np.uint8);self.labels.write_bytes(gzip.compress(labels.tobytes(),mtime=0))
  self.prepared_data.update({'schema':'h3.ai-sprite.alpha-floor-partition.v3','partition_alpha_floor':8,
    'all_partition_eligible_alpha_pixels_assigned_exactly_once':True,
    'sub_floor_positive_pixels_omitted':2,'sub_floor_positive_components_omitted':1,
    'sub_floor_maximum_alpha':1,
    'sub_floor_semantics':'unmodified/unmoved/unredrawn pixels; below-floor positive pixels are excluded from ownership only',
    'visible_pixels':0,'sprite_pixels':[0,0,0],
    'label_map_sha256':sha(self.labels)})
  save(self.prepared,self.prepared_data);self.data['prepared_proof']=ref(self.prepared);save(self.manifest,self.data)
  result=self.call('check',good=False);self.assertIn('PREPARED_ALPHA_FLOOR_OMISSION_DRIFT',result['error'])
 def test_unused_fourth_cell_alpha1_refused(self):
  self.pix.putpixel((20,13),(40,90,120,1));self.pix.save(self.sheet);self.refresh()
  check=self.call("check",good=False)
  self.assertEqual(check["summary"]["unused_positive"],1)
  self.call("run",good=False)
  self.assertFalse((self.output/"analysis-complete.json").exists())
  self.assertTrue((self.output/"review-report.json").is_file())
  self.assertEqual(self.call("verify",good=False)["status"],"VERIFIED_BLOCKED")
 def test_one_alpha1_outside_native_crop_refused(self):
  self.pix.putpixel((1,0),(70,2,0,1));self.pix.save(self.sheet);self.refresh()
  out=self.call("check",good=False)
  self.assertEqual(out["summary"]["outside_native_crop_positive"],1)
  self.call("run",good=False)
  self.assertFalse((self.output/"analysis-complete.json").exists())
 def test_native_scale_unknown_is_explicitly_blocked(self):
  self.views=self.views[:1]
  self.views[0]["rect_px"]=[0,0,24,16]
  self.views[0]["natural_k"]=None
  self.refresh()
  r=self.call("check",good=False)
  self.assertEqual(r["frames"][0]["mapping_status"],"NEEDS_NATIVE_MAPPING")
  self.assertEqual(r["summary"]["positive_unowned"],0)
 def test_overlapping_views_are_rejected_before_output(self):
  self.views[1]["rect_px"]=[0,0,12,8];self.refresh()
  self.assertIn("FRAME_RECT_OVERLAP",str(self.call("check",good=False)))
  self.assertFalse(self.output.exists())
 def test_uncovered_sheet_region_is_rejected(self):
  self.views.pop();self.refresh()
  self.assertIn("FRAME_PARTITION_INCOMPLETE",str(self.call("check",good=False)))
 def test_referenced_parent_and_image_hashes_cannot_drift(self):
  self.proof.write_bytes(self.proof.read_bytes()+b" ")
  self.assertIn("REFERENCE_DRIFT",str(self.call("check",good=False)))
  self.assertFalse(self.output.exists())
 def test_source_rgba_hash_rejected(self):
  self.data["source"]["rgba_sha256"]="0"*64;save(self.manifest,self.data)
  self.assertIn("RGBA_SHA",str(self.call("check",good=False)))
 def test_bool_k_and_bad_geometry_fail(self):
  self.views[0]["natural_k"]=True;self.refresh()
  self.assertIn("INTEGER",str(self.call("check",good=False)))
  self.views[0]["natural_k"]=2
  self.views[0]["geometry"]["crop"]=[5,4];self.refresh()
  self.assertIn("CROP_OUTSIDE",str(self.call("check",good=False)))
 def test_completed_reuse_and_source_change_rejected(self):
  self.call("run");before={p.name:sha(p) for p in self.output.iterdir() if p.is_file()}
  self.assertEqual(self.call("run")["status"],"REUSED_VERIFIED")
  self.assertEqual(before,{p.name:sha(p) for p in self.output.iterdir() if p.is_file()})
  self.pix.putpixel((0,0),(1,2,3,1));self.pix.save(self.sheet)
  self.call("verify",good=False)
 def test_town_raw_receipt_validates_master_request_key_and_geometry(self):
  self.views=self.views[:1];self.views[0]["rect_px"]=[0,0,24,16]
  self.views[0]["natural_k"]=None
  self.refresh()
  request=self.d/"request.json";receipt=self.d/"raw.json"
  save(request,{"key":["def","SYNTH",5,0],
    "native_geometry":{"canvas":[6,4],"crop":[4,3],"origin":[1,1]}})
  save(receipt,{"schema":"h3.art-town.raw-intake.v1","request":ref(request),
                "master":{**ref(self.sheet),"pixels":[24,16]}})
  self.data["references"]=[{"role":"town_request","ref":ref(request)},
                            {"role":"town_raw_receipt","ref":ref(receipt)}]
  save(self.manifest,self.data)
  check=self.call("check",good=False)
  self.assertTrue(check["authority"]["town_request_receipt_bound"])
  request.write_bytes(request.read_bytes()+b" ")
  self.assertIn("REFERENCE_DRIFT",str(self.call("check",good=False)))
 def test_map_native_slot_contract_checked(self):
  request=self.d/"map.json"
  original=[]
  for i in range(3):
   original.append({"row":{"kind":"def","identity":"SYNTH","group":5,"frame":i,
      "canvas":[6,4],"crop":[4,3],"anchor":[1,1]}})
  save(request,{"schema":"synthetic map original", "original_slots":original})
  self.data["references"]=[{"role":"map_original_request","ref":ref(request)}]
  save(self.manifest,self.data)
  self.assertEqual(self.call("check")["authority"]["map_slots_checked"],3)
  original[0]["row"]["anchor"]=[0,0];save(request,{"original_slots":original})
  self.data["references"]=[{"role":"map_original_request","ref":ref(request)}];save(self.manifest,self.data)
  self.assertIn("NATIVE_GEOMETRY_MISMATCH",str(self.call("check",good=False)))

if __name__=="__main__":unittest.main(verbosity=2)
