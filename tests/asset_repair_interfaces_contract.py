"""Contact-family and native phase binding regressions, synthetic pixels only."""
import json
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import asset_repair_contract as original_contracts
import asset_repair as api


class RegisteredInterfaces(unittest.TestCase):
    def setUp(self):
        self.f=original_contracts.AssetRepairContract();self.f.setUp();self.addCleanup(self.f.tearDown)

    def document(self,name,value):
        p=self.f.root/name;p.write_text(json.dumps(value),encoding='utf-8');return self.f.ref(p)

    def inventory(self,pairs):
        rows=[]
        for key,g in pairs:
            rows.append({'kind':key[0],'identity':key[1],'group':key[2],'frame':key[3],
                'canvas':g['canvas_1x'],'crop':g['crop_1x'],'anchor':g['origin_1x']})
        return self.document('native inventory.json',{'rows':rows})

    def contact(self):
        f=self.f;g={'canvas_1x':[96,96],'crop_1x':[96,96],'origin_1x':[0,0]};key=['def','TEST_DELTA',0,0]
        rgba=np.zeros((384,384,4),np.uint8);rgba[16:,:,:]=[60,120,180,255]
        image=f.png('delta registered.png',rgba)
        r={'schema':'h3.motion.receipt.v1','target_scale':4,'png_sha256':image['sha256'],'output_pixels':[384,384],
            'origin_at_target_scale':[0,0],'contract':{'native_entry':{'key':key,'targets':g},'entry':{'layers':[{'source_a':image}]}}}
        source={'image':image,'receipt':self.document('delta receipt.json',r),'geometry':g,'editing_scale':4}
        mask=np.zeros((96,96,4),np.uint8);mask[:,42:54,3]=255
        native=f.png('delta native mask.png',mask);native.update(data_role='native-logical-alpha-geometry',
            alpha_sha256=api.digest(mask[:,:,3].tobytes()),native_key=key,native_geometry=g)
        contract={'schema':'h3.asset-repair.material-family-contract.v1','source_key':key,'material_key':f.key,
            'family_id':'TEST_WATER','family_members':['TEST_DELTA','TEST_ROAD'],'controlled_sides':['top'],
            'native_inventory':self.inventory([(key,g),(f.key,f.g)])}
        return {'id':'delta','operator':'registered-contact-transfer','operator_version':1,'native_key':key,'source':source,
            'native_mask':native,'material':f.source,'material_family_contract':self.document('family.json',contract),
            'controlled_sides':['top'],'depth_native':3,'shared_native':1},rgba

    def phases(self,flat=False):
        f=self.f;frames=[{'id':'phase'+str(i),'native_key':['def','TEST_ROAD',0,i],'geometry':f.g} for i in range(4)]
        contract={'schema':'h3.asset-repair.phase-contract.v1','source_key':f.key,'phases':frames,
            'native_inventory':self.inventory([(x['native_key'],f.g) for x in frames])}
        job=f.job('registered-flow-phases');job.update(mask=f.selection,phases=frames,
            phase_contract=self.document('phases.json',contract),amplitude_pixels=2,direction=[0,1])
        if flat:
            im=f.png('flat painting.png',np.full((128,128,4),255,np.uint8))
            r=json.loads(Path(f.source['receipt']['path']).read_text());r['png_sha256']=im['sha256']
            job['source']={**f.source,'image':im,'receipt':self.document('flat receipt.json',r)}
        return job

    def test_explicit_cross_identity_contract_repairs_native96_without_touching_core(self):
        job,original=self.contact();out,stats=api.dispatch(job);result=out[0][1]
        self.assertTrue(stats['cross_identity_family_contract_verified'])
        np.testing.assert_array_equal(result[12:-12,12:-12],original[12:-12,12:-12])
        self.assertTrue((result[0,42*4:54*4,3]==255).all())

    def test_wrong_family_key_or_unlisted_member_is_blocked(self):
        job,_=self.contact();contract=json.loads(Path(job['material_family_contract']['path']).read_text())
        contract['family_members']=['UNRELATED'];job['material_family_contract']=self.document('wrong-family.json',contract)
        with self.assertRaisesRegex(ValueError,'MATERIAL_FAMILY_MEMBERS'):api.dispatch(job)

    def test_native_geometry_cannot_be_waived_by_family_contract(self):
        job,_=self.contact();job['material']['geometry']={**self.f.g,'origin_1x':[1,0]}
        with self.assertRaises(ValueError):api.dispatch(job)

    def test_complete_phase_group_has_distinct_pixels_and_exact_slot_bindings(self):
        job=self.phases();out,stats=api.dispatch(job)
        self.assertEqual(stats['distinct_phase_pixel_hashes'],4);self.assertEqual(stats['static_max_RGBA_difference_measured'],0)
        for name,image in out:
            np.testing.assert_array_equal(image[:,:,3],self.f.rgba[:,:,3])
            np.testing.assert_array_equal(image[:32],self.f.rgba[:32])
            self.assertEqual(stats['output_geometry'][name]['native_key'][1],'TEST_ROAD')
        self.assertEqual([stats['output_geometry'][name]['native_key'][3] for name,_ in out],list(range(4)))

    def test_missing_native_phase_is_not_a_complete_group(self):
        job=self.phases();job['phases']=job['phases'][:-1]
        contract=json.loads(Path(job['phase_contract']['path']).read_text());contract['phases']=job['phases']
        job['phase_contract']=self.document('incomplete.json',contract)
        with self.assertRaisesRegex(ValueError,'GROUP_INCOMPLETE'):api.dispatch(job)

    def test_duplicate_flat_phases_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'PHASES_NOT_VISIBLY_DISTINCT_DATA'):api.dispatch(self.phases(flat=True))

    def test_full_contact_recipe_replays_pixels_and_statistics(self):
        job,_=self.contact();p,h,out=self.f.manifest(job)
        api.execute('run',p,h,out,'synthetic');self.assertEqual(api.execute('verify',p,h,out,'synthetic')['status'],'VERIFIED')

    def test_full_phase_recipe_retains_each_native_output_key(self):
        p,h,out=self.f.manifest(self.phases());api.execute('run',p,h,out,'synthetic')
        receipt=json.loads((out/'asset-repair-receipt.json').read_text());self.assertEqual([x['native_key'][3] for x in receipt['outputs']],list(range(4)))
        self.assertEqual(api.execute('verify',p,h,out,'synthetic')['status'],'VERIFIED')


if __name__=='__main__':unittest.main()
