"""Batch automation gates use synthetic pixels; no real art is approved."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import road_batch_quality as batch
import map_assembly as assembly

class RoadBatch(unittest.TestCase):
    def test_interface_classes_keep_axes_separate_and_detect_reflection_cycles(self):
        graphs={'east':{'pairs':[[10,0,10,1],[10,0,10,3]]},
                'south':{'pairs':[[11,0,11,2]]}}
        model=batch.interface_equivalence(graphs)
        self.assertEqual(model['class_count'],2)
        self.assertEqual(model['endpoint_count'],2)
        horizontal=next(c for c in model['classes'] if c['reference']['side']=='right')
        vertical=next(c for c in model['classes'] if c['reference']['side']=='bottom')
        self.assertTrue(horizontal['reflection_invariant_required'])
        self.assertFalse(vertical['reflection_invariant_required'])
        self.assertEqual(model['painted_interface_validation'],'NOT_ESTABLISHED_BY_DERIVATION')
        self.assertFalse(model['native_editor_or_game_execution_proved'])

    def test_interface_model_rejects_unqualified_pair_fields(self):
        for bad in ([0,4,0,0],[17,0,0,0],[True,0,0,0]):
            with self.assertRaises(ValueError):batch.interface_equivalence({'east':{'pairs':[bad]},'south':{'pairs':[]}})

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.inv=self.root/'inventory.json';rows=[];pins=[]
        for frame in range(17):
            rows.append({'kind':'def','identity':'COBBRD','group':0,'frame':frame,'canvas':[32,32],'crop':[12,32],'anchor':[10,0]})
            a=np.zeros((32,32,4),np.uint8);a[:,10:22]=[70,80,90,255];p=self.root/f'native-{frame}.png';Image.fromarray(a).save(p)
            pins.append({'key':['def','COBBRD',0,frame],**batch.ref(p)})
        self.inv.write_text(json.dumps({'rows':rows}));self.inventory=batch.ref(self.inv)
        self.proof={'native_reference_graph':{'native_frames':pins},'model':{'editor_emitted_road_neighbors':{
            'east':{'pairs':[]},'south':{'pairs':[[10,0,10,0]]}}}}
        self.source=self.root/'synthetic-hq.png';Image.new('RGBA',(128,128),(70,80,90,255)).save(self.source)
        evidence=self.root/'evidence.json';evidence.write_text(json.dumps({'schema':'h3.art.original-received-source.v1','original':batch.ref(self.source)}))
        component=np.zeros((128,128,4),np.uint8);component[48:64,40:88]=[70,80,90,255]
        self.component_image=self.root/'synthetic-component.png';Image.fromarray(component).save(self.component_image)
        self.component=self.root/'component.json';self.component.write_text(json.dumps({
            'schema':'h3.tile-interface.component-receipt.v1','role':'PAINTED_INTERFACE_COMPONENT',
            'source':batch.ref(self.source),'original_source_evidence':batch.ref(evidence),'component_image':batch.ref(self.component_image),
            'geometry':{'canvas_1x':[32,32]},'pixels_per_native':4,'half_rect_1x':[10,0,6,4],'component_rect_1x':[10,12,12,4]}))
        self.rows={frame:self.asset(frame) for frame in range(17)}
        self.spec={'schema':batch.SCHEMA,'operation_id':'synthetic-road-test','road_families':['COBBRD'],
            'native_inventory':self.inventory,'editor_source_proof':{'path':'synthetic-proof','sha256':'0'*64},'assets':[self.rows[10]]}
        self.patch=patch.object(batch.oracle,'validate_proof',return_value=self.proof);self.patch.start();self.addCleanup(self.patch.stop)

    def asset(self,frame,array=None):
        a=np.full((96,36,4),[70,80,90,255],np.uint8) if array is None else array
        image=self.root/f'sample-{frame}.png';Image.fromarray(a).save(image)
        receipt=self.root/f'receipt-{frame}.json';receipt.write_text(json.dumps({
            'schema':'h3.motion.receipt.v1','target_scale':3,'output':str(image),'png_sha256':batch.ref(image)['sha256'],
            'rgba_sha256':assembly.digest(a.tobytes()),'job_fingerprint':f'{frame:064x}',
            'contract':{'native_entry':{'key':['def','COBBRD',0,frame],'targets':{'canvas_1x':[32,32],'crop_1x':[12,32],'origin_1x':[10,0]}},
                'entry':{'layers':[{'source_a':batch.ref(self.component_image)}]}}}))
        return {'key':['def','COBBRD',0,frame],'image':batch.ref(image),'receipt':batch.ref(receipt)}

    def interface(self,role=None):
        if role:
            d=json.loads(self.component.read_text());d['role']=role;self.component.write_text(json.dumps(d))
        p=self.root/'interface.json';p.write_text(json.dumps({'schema':'h3.road-painted-interface.v1','role':'PAINTED_SHARED_INTERFACE',
            'native_inventory':self.inventory,'component_receipt':batch.ref(self.component),'sampled_reference':self.rows[10],'band_depth_3x':3}))
        return batch.ref(p)

    def axis_fixture(self):
        inventory=json.loads(self.inv.read_text())
        for row in inventory['rows']:row.update(crop=[32,32],anchor=[0,0])
        self.inv.write_text(json.dumps(inventory));self.inventory=batch.ref(self.inv)
        for pin in self.proof['native_reference_graph']['native_frames']:
            a=np.zeros((32,32,4),np.uint8);a[:,10:22,3]=255;a[10:22,:,3]=255
            path=Path(pin['path']);Image.fromarray(a).save(path);pin.update(batch.ref(path))
        source=self.root/'horizontal-source.png';Image.new('RGBA',(128,128),(110,90,30,255)).save(source)
        evidence=self.root/'horizontal-evidence.json';evidence.write_text(json.dumps({
            'schema':'h3.art.original-received-source.v1','original':batch.ref(source)}))
        a=np.zeros((128,128,4),np.uint8);a[48:64,40:88]=[110,90,30,255]
        image=self.root/'horizontal-component.png';Image.fromarray(a).save(image)
        record=json.loads(self.component.read_text());record.update(source=batch.ref(source),
            original_source_evidence=batch.ref(evidence),component_image=batch.ref(image))
        component=self.root/'horizontal-component.json';component.write_text(json.dumps(record))
        rows={}
        for f in range(17):
            a=np.zeros((96,96,4),np.uint8);a[:,30:66]=[70,80,90,255];a[30:66,:]=[110,90,30,255]
            image_path=self.root/f'axis-sample-{f}.png';Image.fromarray(a).save(image_path)
            receipt_path=self.root/f'axis-receipt-{f}.json';receipt=json.loads(Path(self.rows[f]['receipt']['path']).read_text())
            receipt.update(output=str(image_path),png_sha256=batch.ref(image_path)['sha256'],rgba_sha256=assembly.digest(a.tobytes()))
            receipt['contract']['native_entry']['targets'].update(crop_1x=[32,32],origin_1x=[0,0])
            receipt['contract']['entry']['layers']=[{'source_a':batch.ref(self.component_image)},{'source_a':batch.ref(image)}]
            receipt_path.write_text(json.dumps(receipt))
            rows[f]={'key':['def','COBBRD',0,f],'image':batch.ref(image_path),'receipt':batch.ref(receipt_path)}
        axes={}
        for direction,component_path in [('east',component),('south',self.component)]:
            path=self.root/(direction+'-interface.json')
            path.write_text(json.dumps({'schema':'h3.road-painted-interface.v1','role':'PAINTED_SHARED_INTERFACE',
                'native_inventory':self.inventory,'component_receipt':batch.ref(component_path),
                'sampled_reference':rows[0],'band_depth_3x':3 if direction=='east' else 4}))
            axes[direction]=batch.ref(path)
        parent=self.root/'axis-interfaces.json';parent.write_text(json.dumps({'schema':'h3.road-painted-interface.v2',
            'role':'PAINTED_AXIS_INTERFACES','identity':'COBBRD','native_inventory':self.inventory,'axis_contracts':axes}))
        self.proof['model']['editor_emitted_road_neighbors']={'east':{'pairs':[[0,0,1,0],[0,2,1,2]]},
            'south':{'pairs':[[0,0,1,0],[0,1,1,1]]}}
        spec={**self.spec,'assets':list(rows.values()),'native_inventory':self.inventory,'interface_contract':batch.ref(parent)}
        return spec,parent,rows

    def test_one_sample_cannot_pass_seventeen_slots(self):
        before=batch.ref(self.source);r=batch.analyze(self.spec)
        self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['valid_actual3x_slots'],1)
        self.assertEqual(len(r['missing_keys']),16);self.assertEqual(r['measured_pairs'],1)
        self.assertEqual(r['items'][0]['topology']['status'],'PASS')
        self.assertEqual(r['interface_status'],'NOT_MEASURED');self.assertFalse(r['technical_generation_prerequisites_ready'])
        self.assertNotIn('PRODUCE_MISSING_OFFICIAL_SLOTS',r['recommended_actions'])
        self.assertEqual(before,batch.ref(self.source));self.assertEqual(r['images_written'],0)

    def test_complete_files_without_shared_contract_still_cannot_pass(self):
        r=batch.analyze({**self.spec,'assets':list(self.rows.values())})
        self.assertEqual(r['status'],'INTERFACE_NOT_MEASURED');self.assertFalse(r['art_approval_created'])

    def test_verified_component_and_matching_bands_can_pass_assembly_only(self):
        r=batch.analyze({**self.spec,'assets':list(self.rows.values()),'interface_contract':self.interface()})
        self.assertEqual(r['status'],'ASSEMBLY_PASS_RUNTIME_NOT_VERIFIED')
        self.assertEqual(r['pairs'][0]['status'],'PASS');self.assertFalse(r['game_runtime_accepted'])
        self.assertFalse(r['art_approval_created'])

    def test_independent_axis_materials_pass_and_legacy_common_material_rejects(self):
        spec,parent,_=self.axis_fixture();report=batch.analyze(spec)
        self.assertEqual(report['status'],'ASSEMBLY_PASS_RUNTIME_NOT_VERIFIED')
        self.assertTrue(all(p['status']=='PASS' for p in report['pairs']))
        axes=json.loads(parent.read_text())['axis_contracts']
        legacy=batch.analyze({**spec,'interface_contract':axes['south']})
        self.assertEqual(legacy['status'],'FAIL')
        self.assertFalse(report['art_approval_created'])

    def test_axis_contract_requires_both_axes_and_correct_family(self):
        spec,parent,_=self.axis_fixture();data=json.loads(parent.read_text())
        data['identity']='GRAVRD';parent.write_text(json.dumps(data))
        report=batch.analyze({**spec,'interface_contract':batch.ref(parent)})
        self.assertIn('MATERIAL_FAMILY_DIFFERS',report['interface_errors']['COBBRD'])
        data['identity']='COBBRD';del data['axis_contracts']['east'];parent.write_text(json.dumps(data))
        report=batch.analyze({**spec,'interface_contract':batch.ref(parent)})
        self.assertIn('BOTH_AXES_REQUIRED',report['interface_errors']['COBBRD'])

    def test_one_axis_3x_pixel_damage_is_not_hidden_by_the_other_axis(self):
        spec,parent,rows=self.axis_fixture();row=rows[1];p=Path(row['image']['path']);a=np.array(Image.open(p))
        a[40,0,:3]=[250,0,0];Image.fromarray(a).save(p)
        receipt=Path(row['receipt']['path']);data=json.loads(receipt.read_text())
        data.update(png_sha256=batch.ref(p)['sha256'],rgba_sha256=assembly.digest(a.tobytes()));receipt.write_text(json.dumps(data))
        row.update(image=batch.ref(p),receipt=batch.ref(receipt))
        report=batch.analyze({**spec,'interface_contract':batch.ref(parent)})
        self.assertEqual(report['status'],'FAIL')
        self.assertTrue(any(p['direction']=='east' and p['status']=='FAIL' for p in report['pairs']))

    def test_neutral_fixture_cannot_certify_painted_interface(self):
        r=batch.analyze({**self.spec,'interface_contract':self.interface('NEUTRAL_TEST_COMPONENT')})
        self.assertEqual(r['status'],'FAIL');self.assertIn('NEUTRAL_COMPONENT',r['interface_errors']['COBBRD'])

    def test_component_receipt_label_cannot_hide_modified_pixels(self):
        binding=self.interface();im=Image.open(self.component_image).convert('RGBA');im.putpixel((40,48),(250,0,0,255));im.save(self.component_image)
        c=json.loads(self.component.read_text());c['component_image']=batch.ref(self.component_image);self.component.write_text(json.dumps(c))
        i=json.loads(Path(binding['path']).read_text());i['component_receipt']=batch.ref(self.component);Path(binding['path']).write_text(json.dumps(i))
        r=batch.analyze({**self.spec,'interface_contract':batch.ref(Path(binding['path']))})
        self.assertEqual(r['status'],'FAIL');self.assertIn('PIXEL_REPLAY',r['interface_errors']['COBBRD'])

    def test_missing_interior_link_fails_even_if_both_openings_exist(self):
        a=np.full((96,36,4),[70,80,90,255],np.uint8);a[48,:,3]=0
        r=batch.analyze({**self.spec,'assets':[self.asset(10,a)]})
        self.assertEqual(r['status'],'FAIL');self.assertFalse(r['items'][0]['topology']['all_openings_in_one_component'])

    def test_source_drift_is_recorded_without_repair_or_overwrite(self):
        image=Path(self.rows[10]['image']['path']);Image.new('RGBA',(36,96),(255,0,0,255)).save(image);before=image.read_bytes()
        r=batch.analyze(self.spec);self.assertEqual(r['status'],'FAIL');self.assertTrue(r['errors'])
        self.assertEqual(image.read_bytes(),before);self.assertEqual(r['images_written'],0)

    def test_3x_geometry_mismatch_is_not_fixed_by_resizing(self):
        row=self.asset(10,np.full((95,36,4),[70,80,90,255],np.uint8));r=batch.analyze({**self.spec,'assets':[row]})
        self.assertEqual(r['status'],'FAIL');self.assertIn('CROP_DIFFERS',r['errors'][0]['reason'])

    def test_unexpected_branch_is_detected(self):
        a=np.zeros((96,96,4),np.uint8);a[:,30:66]=[70,80,90,255];a[30:66,48:]=[70,80,90,255]
        r=batch.topology(a,['top','bottom']);self.assertEqual(r['status'],'FAIL');self.assertEqual(r['unexpected_openings'],['right'])

if __name__=='__main__':unittest.main()
