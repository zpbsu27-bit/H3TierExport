"""Geometry-first creation and bad-seam rejection, synthetic fixtures only."""
from pathlib import Path
import sys
import unittest
import numpy as np

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'scripts'))
import tile_geometry_design as geometry
import tile_geometry_sampling as sampling


class TileGeometryTests(unittest.TestCase):
    def test_straight_functional_outline_does_not_turn_native_pixel_jitter_into_curb_notches(self):
        a=np.zeros((32,32),np.uint8);a[:,10:22]=255
        a[9:13,9]=255;a[22:25,21]=0;a[5:7,10]=0
        original=a.copy();g={'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]}
        plan={'schema':'h3.tile-geometry.functional-outline.v1','role':'PREPAINTING_FUNCTIONAL_GEOMETRY',
              'contours_1x':[[[10,0],[22,0],[22,32],[10,32]]]}
        image,proof=geometry.design(a,g,['top','bottom'],[10,22],8,outline_plan=plan)
        self.assertTrue((image[:,80:176,3]==255).all())
        self.assertFalse(image[:,:80,3].any());self.assertFalse(image[:,176:,3].any())
        np.testing.assert_array_equal(a,original)
        self.assertEqual(proof['outline_design_authority'],'PLANNED_FUNCTIONAL_OUTLINE')
        self.assertEqual(proof['smooth_iterations'],0)
        wrong={**plan,'contours_1x':[[[10,1],[22,1],[22,32],[10,32]]]}
        with self.assertRaisesRegex(ValueError,'FUNCTIONAL_PORT_DIFFERS'):
            geometry.design(a,g,['top','bottom'],[10,22],8,outline_plan=wrong)
        outside={**plan,'contours_1x':[[[8,0],[22,0],[22,32],[8,32]]]}
        with self.assertRaisesRegex(ValueError,'EXCEEDS_NATIVE_CROP'):
            geometry.design(a,g,['top','bottom'],[10,22],8,outline_plan=outside)

    def straight(self,scale=8):
        a=np.zeros((32,32),np.uint8);a[:,10:22]=255
        g={'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]}
        image,proof=geometry.design(a,g,['top','bottom'],[10,22],scale)
        return a,g,image,proof

    def test_geometry_keeps_source_unchanged_and_stays_within_native_crop(self):
        a,g,image,proof=self.straight();self.assertEqual(image.shape,(256,256,4))
        self.assertFalse(image[:,:9*8,3].any());self.assertFalse(image[:,23*8:,3].any())
        self.assertTrue((a[:,10:22]==255).all());self.assertTrue(proof['crop_contains_designed_subject_without_post_crop'])
        self.assertFalse(proof['native_RGB_used']);self.assertFalse(proof['finished_painting_warped'])

    def test_pixel_center_raster_does_not_leak_half_a_work_pixel_outside_crop(self):
        polygon=np.array([[9,0],[22,0],[22,32],[9,32]],float)
        alpha=geometry.rasterize([polygon],32,8)
        self.assertFalse(alpha[:,:72].any());self.assertTrue((alpha[:,72:176]==255).all())
        self.assertFalse(alpha[:,176:].any())

    def test_cell_union_holes_and_separate_components_are_not_discarded(self):
        a=np.zeros((12,12),bool);a[1:11,1:11]=True;a[4:8,4:8]=False;a[0,0]=True
        contours=geometry.trace_contours(a)
        self.assertEqual(len(contours),3)
        self.assertEqual(sum(geometry.area(p) for p in contours),int(a.sum()))

    def test_marker_is_two_dimensional_transpose_and_flip_invariant(self):
        a=geometry.neutral_pattern(32,8)
        np.testing.assert_array_equal(a,a.transpose(1,0,2))
        np.testing.assert_array_equal(a,a[::-1]);np.testing.assert_array_equal(a,a[:,::-1])
        self.assertGreater(np.unique(a.reshape(-1,3),axis=0).shape[0],2)

    def test_missing_native_crop_capacity_is_not_hidden_by_post_crop(self):
        a=np.zeros((32,32),np.uint8);a[:,10:22]=255
        with self.assertRaisesRegex(ValueError,'EXCEEDS_CROP'):
            geometry.design(a,{'canvas_1x':[32,32],'crop_1x':[2,32],'origin_1x':[15,0]},['top','bottom'],[10,22],8)
        with self.assertRaisesRegex(ValueError,'PORT_NOT_ON_GAME_GRID'):
            geometry.design(np.zeros((32,32),np.uint8),{'canvas_1x':[32,32],'crop_1x':[20,20],'origin_1x':[5,5]},['top'],[10,22],8)


class SamplingProofTests(unittest.TestCase):
    def tiles(self):
        image=np.zeros((96,96,4),np.uint8)
        image[:,30:66,:3]=geometry.neutral_pattern(32,3)[:,30:66]
        image[:,30:66,3]=255
        return [{'id':'synthetic','rgba':image,'geometry':{'canvas_1x':[32,32],'crop_1x':[14,32],'origin_1x':[9,0]},
                 'functional_ports':['top','bottom']}]

    def test_actual3X_RGBA_and_three_rows_are_checked_for_all_opposing_states(self):
        report=sampling.analyze(self.tiles(),[10,22]);self.assertTrue(report['passed'])
        self.assertEqual(report['flip_states'],4);self.assertEqual(report['opposing_functional_pairs'],16)
        self.assertFalse(report['native_equal_profile_pair_filter_used'])
        bad=self.tiles();bad[0]['rgba'][1,40,0]^=60
        failed=sampling.analyze(bad,[10,22]);self.assertFalse(failed['passed'])
        self.assertGreater(failed['three_row_interface_band_RGBA_max_difference'],0)

    def test_severed_interior_cannot_pass_with_perfect_outer_edges(self):
        tiles=self.tiles();tiles[0]['rgba'][47:49,:,3]=0
        report=sampling.analyze(tiles,[10,22]);self.assertFalse(report['passed'])
        self.assertEqual(report['edge_RGBA_max_difference'],0);self.assertEqual(report['topology_failures'],['synthetic'])

    def test_reference_half_tile_shift_is_equivalent_and_not_runtime_claim(self):
        result=sampling.half_tile_reference(self.tiles(),16,16)
        self.assertTrue(result['passed']);self.assertFalse(result['game_runtime_verified'])
        self.assertEqual(result['actual3X_offset'],48)
        with self.assertRaises(ValueError):sampling.half_tile_reference(self.tiles(),0,16)

    def test_wrong_crop_or_unexpected_port_is_not_accepted(self):
        tiles=self.tiles();tiles[0]['rgba'][0,29,3]=255
        report=sampling.analyze(tiles,[10,22]);self.assertFalse(report['passed'])
        self.assertTrue(report['bad_declared_edge_profiles'])


if __name__=='__main__':unittest.main()
