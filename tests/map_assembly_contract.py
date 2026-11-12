"""Real-grid semantics, provenance failures and incomplete-art rejection."""
import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import subprocess
import unittest
import uuid
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import map_assembly as m

def ref(p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

class AssemblyContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        table=np.zeros((1,4,4,7),np.uint8);table[:,:,:,0]=2;table[0,1,2,2]=1;table[0,2,2,2]=1
        raw=struct.pack('<I?I?',28,True,4,False)+table.tobytes()+b'NON_TERRAIN_SENTINEL'
        self.map=self.root/'synthetic.h3m';self.map.write_bytes(gzip.compress(raw,mtime=0))
        self.layout={'width':4,'height':4,'layers':1,'terrain_offset':10,
            'body_sha256':m.digest(raw),'terrain_table_sha256':m.digest(table.tobytes())}
        rows=[{'kind':'def','identity':i,'group':0,'frame':f,'canvas':[32,32],'crop':crop,'anchor':origin}
            for i,f,crop,origin in [('GRASTL',0,[32,32],[0,0]),('COBBRD',10,[14,32],[9,0]),('COBBRD',14,[14,32],[9,0]),('CLRRVR',0,[29,32],[3,0])]]
        self.inv=self.root/'inventory.json';self.inv.write_text(json.dumps({'rows':rows}))
        self.native=self.root/'native'
        for row in rows:
            p=self.native/row['identity']/('0_'+str(row['frame'])+'.png');p.parent.mkdir(exist_ok=True,parents=True)
            Image.new('RGBA',(32,32),(50,70,90,255)).save(p)
        self.hd=self.root/'synthetic-actual3x.png';Image.new('RGBA',(42,96),(70,90,110,255)).save(self.hd)
        self.river_hd=self.root/'synthetic-river-actual3x.png';Image.new('RGBA',(87,96),(40,120,190,255)).save(self.river_hd)
        self.spec={'map':ref(self.map),'map_layout':self.layout,'native_inventory':ref(self.inv),
            'native_frame_root':str(self.native),'viewport':[0,0,4,4],
            'placements':[{'cell':[1,0],'frame':10},{'cell':[1,1],'frame':10},{'cell':[1,2],'frame':14}],
            'actual3x_layers':['road','river'],
            'actual3x':[{'key':['def','COBBRD',0,10],'image':ref(self.hd),'master_source':ref(self.hd),'role':'SYNTHETIC_TEST_ONLY'},
                {'key':['def','CLRRVR',0,0],'image':ref(self.river_hd),'master_source':ref(self.river_hd),'role':'SYNTHETIC_TEST_ONLY'}]}

    def test_map_uses_exact_header_table_and_preserves_other_bytes(self):
        spec=m.make_fixture(self.spec,self.root/'fixture');_,_,body,tiles=m.read_tiles(spec['map'],spec['map_layout'])
        self.assertTrue(body.endswith(b'NON_TERRAIN_SENTINEL'));self.assertEqual(int(tiles[0,1,1,5]),10)
        self.assertEqual(spec['fixture_parent'],ref(self.map))
        self.assertTrue(spec['only_terrain_table_changed']);self.assertFalse(spec['game_runtime_accepted'])

    def test_table_drift_and_wrong_dimensions_block(self):
        bad={**self.layout,'terrain_table_sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'TABLE_SHA'):m.read_tiles(ref(self.map),bad)
        with self.assertRaisesRegex(ValueError,'DIMENSIONS'):m.read_tiles(ref(self.map),{**self.layout,'width':3})

    def test_duplicate_placement_blocks_without_writing_output(self):
        spec={**self.spec,'placements':self.spec['placements']*2}
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):m.make_fixture(spec,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_hv_are_independent_and_do_not_rotate_or_resample(self):
        a=np.arange(4*6*4,dtype=np.uint8).reshape(4,6,4)
        np.testing.assert_array_equal(m.transform(a,16,4),a[:,::-1])
        np.testing.assert_array_equal(m.transform(a,32,4),a[::-1])
        np.testing.assert_array_equal(m.transform(a,48,4),a[::-1,::-1])
        self.assertEqual(m.road_position(2,3),(192,336))

    def test_actual3x_crop_is_registered_without_resizing(self):
        a=m.full_crop(ref(self.hd),{'canvas':[32,32],'crop':[14,32],'anchor':[9,0]})
        self.assertEqual(a.shape,(96,96,4));self.assertTrue((a[:,27:69,3]==255).all())
        self.assertTrue((a[:,:27]==0).all());self.assertTrue((a[:,69:]==0).all())
        with self.assertRaisesRegex(ValueError,'CROP_DIFFERS'):m.full_crop(ref(self.hd),{'canvas':[32,32],'crop':[13,32],'anchor':[9,0]})

    def test_actual3x_river_crop_is_registered_without_resizing(self):
        a=m.full_crop(ref(self.river_hd),{'canvas':[32,32],'crop':[29,32],'anchor':[3,0]})
        self.assertEqual(a.shape,(96,96,4));self.assertTrue((a[:,:9,3]==0).all())
        self.assertTrue((a[:,9:,3]==255).all())
        _,metadata=m.render_offscreen(self.spec)
        self.assertEqual(metadata['missing_river_source_states'],[])
        result=m.review(self.spec,self.root/'river-seam-review')
        self.assertEqual(result['river_placements'],2)
        self.assertEqual(result['river_placements_with_actual3x'],2)
        self.assertEqual(len(result['measured_river_seams']),1)

    def test_missing_artwork_is_visible_and_cannot_pass(self):
        spec=m.make_fixture(self.spec,self.root/'fixture');before=ref(self.hd)
        result=m.review(spec,self.root/'review')
        self.assertEqual(result['status'],'INCOMPLETE');self.assertFalse(result['art_accepted'])
        self.assertEqual(result['road_placements'],3);self.assertEqual(result['road_placements_with_actual3x'],2)
        self.assertEqual(len(result['missing_road_placements']),1);self.assertEqual(result['images_written'],0)
        self.assertEqual(len(result['measured_seams']),1);self.assertEqual(before,ref(self.hd))
        self.assertFalse(list((self.root/'review').glob('*.png')))

    def test_actual3x_river_is_rendered_when_the_layer_is_requested(self):
        result=m.review(self.spec,self.root/'river-review')
        self.assertEqual(result['road_placements'],0)
        self.assertEqual(result['river_placements'],2)
        self.assertEqual(result['river_placements_with_actual3x'],2)
        self.assertEqual(result['missing_river_placements'],[])
        self.assertEqual(result['offscreen_raster']['missing_river_source_states'],[])

    def test_requested_3x_river_layer_blocks_missing_frame(self):
        spec={**self.spec,'actual3x':[self.spec['actual3x'][0]]}
        result=m.review(spec,self.root/'missing-river-review')
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['river_placements_with_actual3x'],0)
        self.assertEqual(len(result['missing_river_placements']),2)

    def test_seam_diagnostics_detect_gap_without_inventing_art_acceptance(self):
        a=np.zeros((96,96,4),np.uint8);a[:,27:69]=[50,70,90,255];b=a.copy();b[0,27:30,3]=0
        d=m.edge_diagnostics(a,b,'south');self.assertEqual(d['alpha_support_xor_pixels'],3)
        self.assertEqual(d['surface_art_acceptance'],'NOT_MEASURED')

    def test_viewport_top_fetches_prior_road_and_mirrors_before_half_slice(self):
        spec=m.make_fixture(self.spec,self.root/'fixture');spec['viewport']=[0,1,4,3]
        result=m.review(spec,self.root/'review')
        self.assertEqual(result['context_road_cells_above_viewport'],[[1,0,0]])
        _,_,_,tiles=m.read_tiles(spec['map'],spec['map_layout'])
        draws=[d for d in m.tile_draw_commands(tiles,1,1,0,3,(0,96)) if d['category']=='road']
        self.assertEqual([d['source_rect'] for d in draws],[[0,48,96,48],[0,0,96,48]])
        self.assertEqual([d['destination'] for d in draws],[[96,0],[96,48]])
        image,metadata=m.render_offscreen(spec)
        self.assertEqual(image.getpixel((123,0)),(70,90,110,255))
        self.assertEqual(image.getpixel((123,47)),(70,90,110,255))
        self.assertEqual(metadata['images_written'],0)

    def test_missing_prior_context_cannot_be_reported_complete(self):
        spec={**self.spec,'placements':[{'cell':[1,0],'frame':14},{'cell':[1,1],'frame':10}]}
        fixture=m.make_fixture(spec,self.root/'fixture');fixture['viewport']=[0,1,4,2]
        result=m.review(fixture,self.root/'review')
        self.assertEqual(result['missing_road_placements'],[])
        self.assertEqual(len(result['missing_context_road_draws']),1)
        self.assertEqual(result['status'],'INCOMPLETE')
        image,_=m.render_offscreen(fixture)
        self.assertNotEqual(image.getpixel((96,0)),(50,70,90,255))

    def test_map_top_does_not_wrap_to_last_row(self):
        _,_,_,tiles=m.read_tiles(ref(self.map),self.layout);tiles[0,-1,1,4:7]=[3,10,0]
        draws=m.tile_draw_commands(tiles,1,0,0)
        self.assertFalse(any(d['category']=='road' for d in draws))

    def test_formal_exe_dispatches_its_pinned_runtime_with_renderer_identity(self):
        product=Path(__file__).resolve().parents[1];exe=product/'build/candidate/H3TierExport.exe'
        if not exe.is_file():self.skipTest('qualified candidate EXE is not built')
        spec=m.make_fixture(self.spec,self.root/'fixture');path=self.root/'scene.json'
        path.write_text(json.dumps(spec),encoding='utf-8');operation='map-assembly-cli-'+uuid.uuid4().hex
        target=product/'build/experiments/map-assembly-tests'/operation
        result=subprocess.run([str(exe),'scale-layout','map-review','--spec',str(path),'--spec-sha256',ref(path)['sha256'],
            '--outdir',str(target),'--operation-id',operation],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads((target/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'INCOMPLETE');self.assertEqual(report['images_written'],0)
        self.assertFalse(report['art_accepted']);self.assertFalse(list(target.glob('*.png')))

if __name__=='__main__':unittest.main()
