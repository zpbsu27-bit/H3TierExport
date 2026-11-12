"""Geometry and no-resampling contracts for shared map material families."""
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
import io
import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import map_material_batch as material


class MapMaterialBatch(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_restore_is_blocked_before_loading_even_a_historical_receipt(self):
        stderr=io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result=material.main(['restore','--manifest',str(self.root/'old.json'),
                                  '--manifest-sha256','a'*64,'--operation-id','old'])
        self.assertEqual(result,2)
        self.assertIn('MATERIAL_PROCESSED_MASTER_RESTORE_DISABLED',stderr.getvalue())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_phase_lock_is_integer_translation_and_preserves_every_rgb_pixel(self):
        y,x=np.indices((768,768));a=np.stack(((x*13+y*5)%256,(x*3+y*17)%256,(x*11+y*7)%256),axis=2).astype(np.uint8)
        path=self.root/"texture.png";Image.fromarray(a).save(path)
        rolled,receipt=material.phase_lock_material(path)
        for channel in range(3):self.assertTrue(np.array_equal(np.bincount(rolled[:,:,channel].ravel(),minlength=256),
                                                               np.bincount(a[:,:,channel].ravel(),minlength=256)))
        self.assertEqual(receipt["pixels_changed"],0)
        self.assertFalse(receipt["resampled"])
        self.assertGreaterEqual(receipt["x_edge_mean_abs_rgb_before"],receipt["x_edge_mean_abs_rgb_after"])
        self.assertGreaterEqual(receipt["y_edge_mean_abs_rgb_before"],receipt["y_edge_mean_abs_rgb_after"])

    def test_legal_neighbor_phase_search_is_integer_only_and_changes_registration_not_pixels(self):
        y,x=np.indices((768,768));a=np.stack(((x*13+y*5)%256,(x*3+y*17)%256,(x*11+y*7)%256),axis=2).astype(np.uint8)
        path=self.root/"texture-phase-search.png";Image.fromarray(a).save(path)
        base,base_receipt=material.phase_lock_material(path)
        shifted,receipt=material.phase_lock_material(path,(17,-9))
        self.assertTrue(np.array_equal(shifted,np.roll(base,shift=(-9,17),axis=(0,1))))
        for channel in range(3):self.assertTrue(np.array_equal(np.bincount(shifted[:,:,channel].ravel(),minlength=256),
                                                               np.bincount(a[:,:,channel].ravel(),minlength=256)))
        self.assertEqual(receipt["pixels_changed"],0)
        self.assertFalse(receipt["resampled"])
        self.assertEqual(receipt["phase_search_offset_xy"],[17,-9])
        self.assertNotEqual(receipt["integer_phase_roll_xy"],base_receipt["integer_phase_roll_xy"])

    def test_official_alpha_becomes_exact_high_res_mask_without_rgb_resampling(self):
        material_rgb=np.zeros((64,64,3),dtype=np.uint8);material_rgb[:]=[90,120,150]
        mask=np.zeros((32,32),dtype=np.uint8);mask[8:24,5:27]=255
        parent=material.masked_material_parent(material_rgb,mask,{"canvas_1x":[32,32]})
        self.assertEqual(parent.shape,(64,64,4))
        self.assertTrue(np.all(parent[0,:,3]==0))
        self.assertTrue(np.all(parent[16,10:54,3]==255))
        self.assertEqual(tuple(parent[16,16,:3]),(90,120,150))

    def test_native_mask_expansion_preserves_rational_pixel_center_ownership(self):
        mask=np.zeros((32,32),dtype=np.uint8);mask[:,16:]=255
        expanded=material.expand_native_alpha(mask,1254,1254)
        x=(np.arange(1254)+.5)*32/1254
        owners=np.floor(x).astype(int)
        self.assertTrue(np.all(expanded[:,owners<16]==0))
        self.assertTrue(np.all(expanded[:,owners>=16]==255))

    def test_road_and_river_family_scopes_use_official_frame_counts(self):
        self.assertEqual(material.family_frame_count("COBBRD"),17)
        self.assertEqual(material.family_frame_count("CLRRVR"),13)
        with self.assertRaises(ValueError):material.family_frame_count("TEST")

    def test_river_bank_material_stays_in_a_fixed_native_edge_band(self):
        material_rgb=np.zeros((64,64,3),dtype=np.uint8);material_rgb[:]=[20,80,200]
        bank_rgb=np.zeros_like(material_rgb);bank_rgb[:]=[110,90,60]
        mask=np.zeros((32,32),dtype=np.uint8);mask[8:24,5:27]=255
        parent=material.masked_material_parent(material_rgb,mask,{"canvas_1x":[32,32]},bank_rgb,2)
        self.assertEqual(tuple(parent[32,32,:3]),(20,80,200))
        self.assertEqual(tuple(parent[33,11,:3]),(110,90,60))
        self.assertEqual(tuple(parent[32,32,3:]),(255,))
        self.assertEqual(tuple(parent[0,0,:]),(0,0,0,0))


if __name__=="__main__":unittest.main()
