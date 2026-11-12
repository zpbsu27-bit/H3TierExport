"""Each category must execute a meaningful check without modifying painted pixels."""
import json
import gzip
from pathlib import Path
import struct
import sys
import unittest
import contextlib
import io
from unittest.mock import patch
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import art_batch_quality_contract as fixtures
ref=fixtures.ref
import art_quality_strategies as domain
import art_production_profile as planning
import art_batch_quality as quality


class CategoryStrategies(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.BatchQualityContract();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.p=self.f.pdata;self.keys=self.p['expected_slots']
 def result(self,preset,config):
  self.p.update(preset=preset,strategies=config);return self.f.analyze()
 def test_unconfigured_categories_are_not_false_passes(self):
  for preset in domain.CHECKS:
   r=self.result(preset,{})
   self.assertFalse(r['verification_coverage']['complete_validation'])
   self.assertTrue(any(x['status']=='NOT_MEASURED' for x in r['domain_strategies']['checks'].values()))
 def test_flag_targets_and_an_actual_pole_band_cannot_be_invented_from_bbox(self):
  r=self.result('adventure-flags',{'unrelated_setting':True})
  self.assertFalse(r['verification_coverage']['complete_validation'])
  self.assertEqual(r['domain_strategies']['checks']['flag_pole_axis_alignment']['status'],'NOT_MEASURED')
  a=np.zeros((96,96,4),np.uint8);a[10:30,20:70]=[80,90,100,255]
  m=domain.flag_metrics(a,{'expected_pole_x':40,'pole_rows_y':[60,80]})
  self.assertIsNone(m['pole_x']);self.assertFalse(m['pole_measured'])
  self.assertIsNone(m['connection_purity_pass']);self.assertIsNone(m['purity_pass'])
 def test_boat_bbox_is_not_a_physical_waterline_or_perspective_volume(self):
  r=self.result('boats',{'expected_canvas':[96,96],'expected_keel_y':90})
  checks=r['domain_strategies']['checks']
  self.assertEqual(checks['boat_waterline_alignment']['status'],'NOT_MEASURED')
  self.assertEqual(checks['boat_perspective_volume']['status'],'NOT_MEASURED')
  self.assertFalse(r['verification_coverage']['complete_validation'])
  a=np.zeros((96,96,4),np.uint8);a[10:80,20:75]=[70,80,90,255]
  m=domain.boat_metrics(a,self.keys[0])
  self.assertIsNone(m['canvas_pass']);self.assertIsNone(m['waterline_pass']);self.assertIsNone(m['volume_pass'])
  self.assertFalse(m['physical_waterline_verified']);self.assertFalse(m['three_dimensional_volume_verified'])
 def test_fixed_base_ignores_allowed_dynamic_region_but_catches_base_damage(self):
  mask={'coordinate_space':'source_pixels','pixels':[96,96],'rectangles':[[0,0,96,40]]}
  spec={'keys':self.keys,'reference_key':self.keys[0],'region':mask,'region_role':'dynamic_allowed','exact':True}
  # Make the fixed lower part byte-identical, while the allowed upper half changes.
  a=np.asarray(Image.open(self.f.unit/'0_0.png')).copy()
  for f in (1,2):
   b=a.copy();b[10:30,30:50,:3]=[5,f*40,90];Image.fromarray(b).save(self.f.unit/f'0_{f}.png')
  r=self.result('town-animation',{'static_regions':[spec]})
  self.assertEqual(r['domain_strategies']['checks']['static_region_lock']['status'],'PASS')
  a[60,40,:3]=0;Image.fromarray(a).save(self.f.unit/'0_2.png')
  r=self.result('town-animation',{'static_regions':[spec]})
  self.assertEqual(r['domain_strategies']['checks']['static_region_lock']['status'],'FAIL')
 def test_static_region_requires_declared_coordinate_canvas(self):
  with self.assertRaisesRegex(ValueError,'BOUND_MASK_COORDINATE'):
   self.result('town-animation',{'static_regions':[{'keys':self.keys,'reference_key':self.keys[0],
    'region':{'coordinate_space':'source_pixels','pixels':[95,96],'rectangles':[[0,0,30,30]]}}]})
 def test_partial_body_coverage_cannot_pass_whole_creature(self):
  self.f.add_body_evidence();r=self.result('creature-actions',{})
  c=r['domain_strategies']['checks']['body_scale']
  self.assertEqual(c['covered_slots'],1);self.assertEqual(c['status'],'NOT_MEASURED')
  self.assertNotIn('body_scale',r['items'][0]['unmeasured'])
  self.assertIn('body_scale',r['items'][1]['unmeasured'])
 def test_real_def_offset_aliases_and_inventory_geometry(self):
  blob=bytearray(784);struct.pack_into('<4I',blob,0,66,32,32,1)
  offset=784+16+3*17
  blob.extend(struct.pack('<4I',0,3,0,0)+bytes(3*13)+struct.pack('<3I',offset,offset,offset))
  blob.extend(struct.pack('<6I2i',0,0,32,32,30,30,1,1))
  source=self.f.root/'UNIT.def';source.write_bytes(blob)
  config={'native_defs':[{'identity':'UNIT','source':ref(source)}]}
  r=self.result('creature-actions',config);self.assertEqual(r['domain_strategies']['checks']['native_aliases']['status'],'FAIL')
  for f in (1,2):(self.f.unit/f'0_{f}.png').write_bytes((self.f.unit/'0_0.png').read_bytes())
  r=self.result('creature-actions',config);self.assertEqual(r['domain_strategies']['checks']['native_aliases']['status'],'PASS')
  struct.pack_into('<I',blob,offset+8,33);source.write_bytes(blob);config['native_defs'][0]['source']=ref(source)
  with self.assertRaisesRegex(ValueError,'GEOMETRY_DIFFERS'):self.result('creature-actions',config)
 def test_ground_tile_seams_and_non_tiling_exception(self):
  for f in range(3):Image.new('RGBA',(96,96),(80,100,30,255)).save(self.f.unit/f'0_{f}.png')
  r=self.result('ground',{'seamless_repeat':True})
  self.assertEqual(r['domain_strategies']['checks']['registered_neighbor_seams']['status'],'PASS')
  self.assertNotIn('anatomy_and_action_semantics',r['items'][0]['unmeasured'])
  a=np.array(Image.open(self.f.unit/'0_1.png'));a[:,-1,:3]=[240,240,240];Image.fromarray(a).save(self.f.unit/'0_1.png')
  r=self.result('ground',{'seamless_repeat':True});self.assertEqual(r['domain_strategies']['checks']['registered_neighbor_seams']['status'],'RISK')
  r=self.result('ground',{'seamless_repeat':False});self.assertEqual(r['domain_strategies']['checks']['registered_neighbor_seams']['status'],'NOT_APPLICABLE')
 def test_declared_road_port_gap_is_a_failure_not_an_anatomy_guess(self):
  r=self.result('roads-rivers',{'neighbor_edges':[{'a_key':self.keys[0],'b_key':self.keys[1],
    'a_edge':'east','b_edge':'west','require_opaque_port':True}]})
  self.assertEqual(r['domain_strategies']['checks']['registered_neighbor_seams']['status'],'FAIL')
 def test_nearest_native_alpha_is_risk_and_unbound_slots_remain_unmeasured(self):
  native=np.zeros((32,32,4),np.uint8)
  for y in range(5,27):native[y, max(1,20-y//2):27,3]=255
  mask=self.f.root/'native.png';Image.fromarray(native).save(mask)
  enlarged=np.repeat(np.repeat(native,3,axis=0),3,axis=1)
  enlarged[:,:,:3]=100;Image.fromarray(enlarged).save(self.f.unit/'0_0.png')
  config={'native_alpha_masks':[{'key':self.keys[0],'source':ref(mask)}]}
  r=self.result('roads-rivers',config)
  c=r['domain_strategies']['checks']['native_alpha_pixelation']
  self.assertEqual(c['status'],'RISK');self.assertEqual(c['covered_slots'],1)
  self.assertIn('DOMAIN_NATIVE_ALPHA_PIXELATION',r['items'][0]['risks'])
  self.assertEqual(len(c['unmeasured_keys']),2)
  self.assertFalse(r['art_approved'])
  native[0,0,3]=255;Image.fromarray(native).save(mask)
  with self.assertRaisesRegex(ValueError,'SHA'):self.result('roads-rivers',config)
 def test_init_binds_available_native_masks_without_inventing_missing_ones(self):
  masks=self.f.root/'native'/'UNIT';masks.mkdir(parents=True)
  Image.new('RGBA',(32,32),(0,0,0,0)).save(masks/'0_0.png')
  output=io.StringIO()
  with patch.object(quality,'PRODUCT',self.f.root),contextlib.redirect_stdout(output):
   rc=quality.main(['init','--operation-id','mask-init','--masters-root',str(self.f.masters),
      '--inventory',str(self.f.inv),'--inventory-sha256',ref(self.f.inv)['sha256'],
      '--art-standard',str(self.f.standard),'--art-standard-sha256',ref(self.f.standard)['sha256'],
      '--preset','roads-rivers','--identity','UNIT','--native-mask-root',str(masks.parent)])
  self.assertEqual(rc,0)
  profile=json.loads(Path(json.loads(output.getvalue())['profile']['path']).read_text())
  self.assertEqual(len(profile['strategies']['native_alpha_masks']),1)
  self.assertEqual(profile['strategies']['native_alpha_masks'][0]['key'],self.keys[0])
 def test_road_authoring_brief_keeps_user_natural_edges_and_2_5d(self):
  for preset in ('roads-rivers','road-connection'):
   rules='\n'.join(planning.authoring_rules(preset))
   self.assertIn('零星小石子',rules)
   self.assertIn('刀切',rules)
   self.assertIn('2.5D',rules)
 def test_road_seam_guidance_is_opted_in_and_scoped_to_cobblestone(self):
  profile={'preset':'roads-rivers','expected_slots':[['def','COBBRD',0,1]]}
  self.assertEqual(planning.road_seam_brief(profile),[])
  profile['road_authoring_brief_revision']='road-seam-lessons-20261102-v1'
  lines='\n'.join(planning.road_seam_brief(profile))
  for phrase in ('较大石块','较小的石子','2015 HD 3X','合法镜像','不能保证模型精确遵循'):
   self.assertIn(phrase,lines)
  profile['expected_slots']=[['def','CLRRVR',0,0]]
  self.assertEqual(planning.road_seam_brief(profile),[])
  profile['expected_slots']=[['def','GRAVRD',0,0]]
  self.assertNotIn('COBBRD碎石路风格锁定','\n'.join(planning.road_seam_brief(profile)))
  profile['preset']='ground'
  self.assertEqual(planning.road_seam_brief(profile),[])
 def test_new_road_profile_brief_and_legacy_replay_are_separate(self):
  profile=dict(self.p,preset='roads-rivers',expected_slots=[['def','COBBRD',0,0]])
  old=quality.brief(profile)
  self.assertNotIn('COBBRD碎石路风格锁定',old)
  revision=planning.template('roads-rivers',profile['expected_slots'],{})['road_authoring_brief_revision']
  profile['road_authoring_brief_revision']=revision
  new=quality.brief(profile)
  self.assertIn('COBBRD碎石路风格锁定',new)
  del profile['road_authoring_brief_revision']
  self.assertEqual(quality.brief(profile),old)
 def test_road_guide_binds_slot_role_and_actual_png_hash(self):
  guide=self.f.root/'guide.png';Image.new('RGBA',(24,24),(0,0,0,0)).save(guide)
  key=['def','COBBRD',0,1]
  profile={'preset':'road-connection','expected_slots':[key],
   'road_authoring_brief_revision':'road-seam-lessons-20261102-v1',
   'road_drawing_guides':[{'key':key,'role':'geometry','source':ref(guide)}]}
  brief='\n'.join(planning.road_seam_brief(profile))
  self.assertIn('COBBRD/0_1.png',brief);self.assertIn(ref(guide)['sha256'],brief)
  self.assertIn('不会自动上传',brief)
  profile['road_drawing_guides'].append(dict(profile['road_drawing_guides'][0]))
  with self.assertRaisesRegex(ValueError,'ROAD_GUIDE_DUPLICATE_ROLE'):planning.road_seam_brief(profile)
  profile['road_drawing_guides'].pop();profile['road_drawing_guides'][0]['key']=['def','COBBRD',0,2]
  with self.assertRaisesRegex(ValueError,'ROAD_GUIDE_SLOT_OUTSIDE_SCOPE'):planning.road_seam_brief(profile)
  profile['road_drawing_guides'][0]['key']=key;Image.new('RGBA',(25,25)).save(guide)
  with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):planning.road_seam_brief(profile)
 def test_edge_lengths_are_not_silently_stretched(self):
  a=np.full((8,8,4),255,dtype='uint8');b=np.full((9,8,4),255,dtype='uint8')
  with self.assertRaisesRegex(ValueError,'NO_AUTOMATIC_STRETCH'):domain.compare_edges(a,b,{'a_edge':'east','b_edge':'west'})
 def test_original_frame_order_required(self):
  with self.assertRaisesRegex(ValueError,'ORIGINAL_GROUP_FRAME_ORDER'):
   self.result('creature-actions',{'sequences':[{'ordered_keys':list(reversed(self.keys)),'loop':True}]})
 def test_missing_image_does_not_abort_other_domain_results(self):
  (self.f.unit/'0_1.png').rename(self.f.unit/'missing-source.png')
  r=self.result('town-animation',{'sequences':[{'ordered_keys':self.keys,'loop':True}]})
  self.assertEqual(r['overall'],'ERRORS_FOUND')
  self.assertEqual(r['domain_strategies']['checks']['ordered_motion']['status'],'NOT_MEASURED')
 def test_secondary_body_metric_cannot_hide_behind_primary_metric_pass(self):
  self.f.add_body_evidence();direction=self.p['directions']['side']
  direction['lengths_native']['rider']=20
  self.p['body_measurements'][0]['paths']['rider']=[[30,10],[30,40]]
  r=self.result('creature-actions',{})
  self.assertEqual(r['items'][0]['body_scale']['status'],'BODY_SCALE_REVIEW_REQUIRED')
 def test_native_crop_includes_alpha_one_and_never_erases_it(self):
  self.f.add_body_evidence();receipt=self.f.root/'native-review.json';data=json.loads(receipt.read_text())
  output=self.f.root/'existing-native-3x.png';a=np.array(Image.open(self.f.unit/'0_0.png'));a[0,0]=[60,70,80,1];Image.fromarray(a).save(output)
  data['items'][0]['outputs']['candidate_3x']={'path':str(output),'png_sha256':ref(output)['sha256']}
  receipt.write_text(json.dumps(data));self.p['native_review_report']=ref(receipt)
  before=ref(output);r=self.result('mounted-heroes',{})
  events=r['items'][0]['domain_checks']['native_registration']
  self.assertEqual(events[0]['positive_alpha_outside_crop'],1);self.assertEqual(events[0]['status'],'RISK');self.assertEqual(before,ref(output))
 def test_loop_closure_catches_jump_without_inventing_native_timing(self):
  for f in range(3):Image.new('RGBA',(96,96),(20+f*55,20+f*55,20+f*55,255)).save(self.f.unit/f'0_{f}.png')
  r=self.result('town-animation',{'sequences':[{'ordered_keys':self.keys,'loop':True,'loop_relative_hint':1.5}]})
  c=r['domain_strategies'];self.assertEqual(c['checks']['loop_boundary']['status'],'RISK');self.assertFalse(c['native_timing_verified'])
 def test_no_loop_is_not_applicable_and_unregistered_motion_is_unknown(self):
  Image.new('RGBA',(100,96),(20,40,20,255)).save(self.f.unit/'0_2.png')
  r=self.result('town-animation',{'sequences':[{'ordered_keys':self.keys,'loop':False}]})
  c=r['domain_strategies']['checks'];self.assertEqual(c['ordered_motion']['status'],'NOT_MEASURED');self.assertEqual(c['loop_boundary']['status'],'NOT_APPLICABLE')
 def test_flow_translation_direction_and_texture_ambiguity(self):
  rng=np.random.default_rng(18);a=rng.integers(0,256,(40,40,4),dtype='uint8');a[:,:,3]=255
  b=np.roll(a,3,axis=1);region=np.ones((40,40),bool)
  right=domain.flow_metrics(a,b,region,[1,0]);wrong=domain.flow_metrics(a,b,region,[-1,0])
  self.assertEqual(right['status'],'PASS');self.assertEqual(right['measured_shift_pixels'],[3,0]);self.assertEqual(wrong['status'],'RISK')
  self.assertEqual(domain.flow_metrics(np.full_like(a,255),np.full_like(a,255),region,[1,0])['status'],'NOT_MEASURED')
 def test_templates_are_category_specific_and_never_compete_with_canonical(self):
  creature=planning.template('creature-actions',self.keys,{})
  ground=planning.template('ground',self.keys,{})
  self.assertIn('body_scale',creature['strategy_setup']['required_inputs'])
  self.assertNotIn('body_scale',ground['strategy_setup']['required_inputs'])
  self.assertEqual(creature['strategies'],{});self.assertFalse(creature['drawing_contract']['nonuniform_stretch_allowed'])
 def test_semantic_review_is_bound_to_the_exact_image_and_real_author(self):
  path=self.f.root/'review.json';data={'schema':'h3.art.semantic-review.v1','review_author':'assistant','items':[
    {'key':self.keys[0],'image_sha256':ref(self.f.unit/'0_0.png')['sha256'],
     'checks':dict.fromkeys(['identity','character_count','anatomy','pose'],'PASS')}]}
  path.write_text(json.dumps(data));config={'semantic_reviews':[ref(path)]}
  r=self.result('creature-actions',config)
  self.assertNotIn('anatomy_and_action_semantics',r['items'][0]['unmeasured'])
  self.assertIn('anatomy_and_action_semantics',r['items'][1]['unmeasured'])
  data['items'][0]['image_sha256']='0'*64;path.write_text(json.dumps(data));config={'semantic_reviews':[ref(path)]}
  with self.assertRaisesRegex(ValueError,'SEMANTIC_REVIEW_SOURCE_SHA_DRIFT'):self.result('creature-actions',config)
 def test_background_integrity_is_a_separate_source_bound_manual_check(self):
  path=self.f.root/'background-review.json';data={'schema':'h3.art.semantic-review.v1','review_author':'assistant','items':[
   {'key':self.keys[0],'image_sha256':ref(self.f.unit/'0_0.png')['sha256'],
    'checks':dict(identity='PASS',character_count='PASS',anatomy='PASS',pose='PASS',background_integrity='FAIL')}]}
  path.write_text(json.dumps(data));result=self.result('creature-actions',{'semantic_reviews':[ref(path)]})
  checks=result['domain_strategies']['checks']
  self.assertEqual(checks['background_integrity']['status'],'FAIL')
  self.assertEqual(checks['background_integrity']['covered_slots'],1)
  self.assertIn('DOMAIN_BACKGROUND_INTEGRITY',result['items'][0]['errors'])
  self.assertIn('background_integrity',result['items'][1]['unmeasured'])
  data['items'][0]['image_sha256']='0'*64;path.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'SEMANTIC_REVIEW_SOURCE_SHA_DRIFT'):
   self.result('creature-actions',{'semantic_reviews':[ref(path)]})
 def motion_review(self,loop_mode='loop'):
  frames=[{'key':key,'image_sha256':ref(self.f.unit/f'{key[2]}_{key[3]}.png')['sha256']} for key in self.keys]
  checks={name:'PASS' for name in ('identity_continuity','anatomy_continuity','equipment_continuity','color_material_continuity','action_logic')}
  def edge(a,b):return {'from_key':a['key'],'from_image_sha256':a['image_sha256'],
   'to_key':b['key'],'to_image_sha256':b['image_sha256'],'checks':dict(checks),'notes':''}
  return {'schema':'h3.creature.motion-review.v1','review_author':'user','identity':'UNIT',
   'native_inventory_sha256':self.p['native_inventory']['sha256'],
   'groups':[{'group':0,'loop_mode':loop_mode,'ordered_keys':self.keys,'frames':frames,
    'transitions':[edge(a,b) for a,b in zip(frames,frames[1:])],
    'loop_transition':edge(frames[-1],frames[0])}]}
 def test_action_continuity_is_separate_from_pixel_motion_and_source_bound(self):
  path=self.f.root/'motion-review.json';data=self.motion_review();path.write_text(json.dumps(data))
  result=self.result('creature-actions',{'motion_reviews':[ref(path)]})
  check=result['domain_strategies']['checks']['action_continuity']
  self.assertEqual(check['status'],'PASS');self.assertEqual(check['covered_slots'],3)
  self.assertNotIn('action_continuity',result['items'][0]['unmeasured'])
  data['groups'][0]['transitions'][0]['checks']['action_logic']='FAIL'
  data['groups'][0]['transitions'][0]['notes']='动作阶段从预备直接跳到收势。';path.write_text(json.dumps(data))
  result=self.result('creature-actions',{'motion_reviews':[ref(path)]})
  self.assertEqual(result['domain_strategies']['checks']['action_continuity']['status'],'FAIL')
  self.assertEqual(result['overall'],'ERRORS_FOUND')
  self.assertIn('DOMAIN_ACTION_CONTINUITY',result['items'][0]['errors'])
 def test_motion_review_requires_every_official_edge_and_exact_source_hashes(self):
  path=self.f.root/'motion-review.json';data=self.motion_review()
  data['groups'][0]['transitions'].pop();path.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'MOTION_REVIEW_TRANSITION_COVERAGE_INCOMPLETE'):
   self.result('creature-actions',{'motion_reviews':[ref(path)]})
  data=self.motion_review();data['groups'][0]['transitions'][0]['to_image_sha256']='0'*64;path.write_text(json.dumps(data))
  with self.assertRaisesRegex(ValueError,'MOTION_REVIEW_TRANSITION_SOURCE_SHA_DRIFT'):
   self.result('creature-actions',{'motion_reviews':[ref(path)]})
 def test_non_loop_motion_review_does_not_invent_a_loop_boundary(self):
  path=self.f.root/'motion-review.json';data=self.motion_review('non_loop')
  data['groups'][0]['loop_transition']['checks']={name:'NOT_APPLICABLE' for name in data['groups'][0]['loop_transition']['checks']}
  path.write_text(json.dumps(data));result=self.result('creature-actions',{'motion_reviews':[ref(path)]})
  self.assertEqual(result['domain_strategies']['checks']['action_continuity']['status'],'PASS')
  self.assertTrue(any(e.get('reason')=='REVIEWED_NON_LOOP_GROUP'
                      for e in result['domain_strategies']['events']))
 def test_profile_and_report_never_modify_or_emit_image_sets(self):
  before={p.name:ref(p) for p in self.f.unit.glob('*.png')}
  self.result('town-animation',{'sequences':[{'ordered_keys':self.keys,'loop':True}]})
  self.assertEqual(before,{p.name:ref(p) for p in self.f.unit.glob('*.png')})
 def test_static_road_does_not_require_water_flow_animation(self):
  r=self.result('roads-rivers',{'animation_applicable':False})
  for name in ('ordered_motion','loop_boundary','semantic_flow_direction'):
   self.assertEqual(r['domain_strategies']['checks'][name]['status'],'NOT_APPLICABLE')
 def test_production_target_and_margin_are_validated_before_use(self):
  self.p.update(planning.template('creature-actions',self.keys,{}))
  self.p['drawing_contract'].update(working_canvas_pixels=[512,512],transparent_safety_margin_pixels=300)
  with self.assertRaisesRegex(ValueError,'SAFETY_MARGIN'):self.result('creature-actions',{})
 def test_declared_reference_sha_drift_blocks_preparation(self):
  self.p['reference_images']=[{'path':str(self.f.unit/'0_0.png'),'sha256':'0'*64}]
  with self.assertRaisesRegex(ValueError,'INPUT_SHA_DRIFT'):self.result('creature-actions',{})
 def test_skipping_original_intermediate_frame_is_not_an_adjacent_check(self):
  with self.assertRaisesRegex(ValueError,'SKIP_ORIGINAL'):
   self.result('creature-actions',{'sequences':[{'ordered_keys':[self.keys[0],self.keys[2]],'loop':False}]})
 def test_non_loop_group_cannot_exempt_another_unchecked_group(self):
  data=json.loads(gzip.decompress(self.f.inv.read_bytes()));row=dict(data['rows'][0]);row.update(group=1,frame=0)
  data['rows'].append(row);self.f.inv.write_bytes(gzip.compress(json.dumps(data).encode()))
  self.p['native_inventory']=ref(self.f.inv);self.keys.append(['def','UNIT',1,0])
  (self.f.unit/'1_0.png').write_bytes((self.f.unit/'0_0.png').read_bytes())
  r=self.result('town-animation',{'sequences':[{'ordered_keys':self.keys[:3],'loop':False}]})
  c=r['domain_strategies']['checks']['loop_boundary']
  self.assertEqual(c['status'],'NOT_MEASURED');self.assertEqual(c['covered_slots'],3)
  self.assertIn('loop_boundary',r['items'][3]['unmeasured'])
 def test_one_unmeasured_edge_is_not_hidden_by_another_measured_edge(self):
  Image.new('RGBA',(100,96),(20,40,20,255)).save(self.f.unit/'0_0.png')
  r=self.result('town-animation',{'sequences':[{'ordered_keys':self.keys,'loop':False}]})
  self.assertIn('ordered_motion',r['items'][1]['unmeasured'])
 def test_ground_and_building_briefs_do_not_instruct_character_anatomy(self):
  self.p['preset']='ground';ground=quality.brief(self.p)
  self.assertNotIn('长矛',ground);self.assertNotIn('死亡',ground);self.assertIn('全不透明覆盖',ground)
  self.p['preset']='town-animation';town=quality.brief(self.p)
  self.assertNotIn('死亡',town);self.assertIn('固定底座',town)

if __name__=='__main__':unittest.main()
