"""Actual source-plane regression contracts; no painted PNGs are written."""
import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import interface_port_geometry as geometry


class InterfacePortGeometryTests(unittest.TestCase):
    def fixture(self):
        source=np.zeros((256,256),np.uint8)
        source[:,80:176]=255
        source[:5]=0;source[-5:]=0
        native=np.zeros((32,32),np.uint8);native[:,10:22]=255
        return source,native

    def test_rectangular_sampling_preserves_original_and_covers_retained_source(self):
        source,native=self.fixture();before=source.copy()
        roi,vertices,targets,triangles,proof=geometry.rectangular_registration(
            source,[0,0,256,256],native,[0,0,32,32],['top','bottom'])
        np.testing.assert_array_equal(source,before)
        self.assertGreater(roi[1],0);self.assertLess(roi[3],256)
        for corner in ([0.,0.],[float(roi[2]),0.],[float(roi[2]),float(roi[3])],[0.,float(roi[3])]):
            self.assertIn(corner,vertices)
        self.assertFalse(proof['source_alpha_modified'])
        geometry.validate_formal_mesh(roi,[0,0,32,32],vertices,targets,triangles)

    def test_disconnected_opaque_banks_cannot_fake_a_filled_opening(self):
        source=np.zeros((160,160),np.uint8)
        source[:,45:50]=255;source[:,110:115]=255
        native=np.zeros((32,32),np.uint8);native[0,10:22]=255
        with self.assertRaisesRegex(ValueError,'NO_OPAQUE_PORT_CROSS_SECTION'):
            geometry.planes(source,[0,0,160,160],native,[0,0,32,32],['top'])

    def test_low_alpha_outlier_does_not_expand_subject_roi(self):
        source=np.zeros((100,100),np.uint8);source[20:80,30:70]=255;source[0,0]=1
        self.assertEqual(geometry.initial_roi(source),[28,18,44,64])
        self.assertEqual(int(source[0,0]),1)

    def test_reorientation_is_exact_pixel_permutation_without_interpolation(self):
        source=np.arange(8*12*4,dtype=np.uint8).reshape(8,12,4)
        original=source.copy();rotated=geometry.reorient_rgba(source,1)
        np.testing.assert_array_equal(rotated,np.rot90(source))
        np.testing.assert_array_equal(geometry.reorient_rgba(rotated,3),original)
        np.testing.assert_array_equal(source,original)
        with self.assertRaises(ValueError):geometry.reorient_rgba(source,True)

    def test_fold_or_excessive_stretch_remains_rejected(self):
        vertices=[[0.,0.],[100.,0.],[0.,100.]]
        with self.assertRaises(ValueError):
            geometry.validate_formal_mesh([0,0,100,100],[0,0,32,32],vertices,[[0.,0.],[0.,32.],[32.,0.]],[[0,1,2]])
        with self.assertRaises(ValueError):
            geometry.validate_formal_mesh([0,0,100,100],[0,0,32,32],vertices,[[0.,0.],[1.,0.],[0.,1.]],[[0,1,2]])

    def test_absent_native_functional_port_is_not_inferred(self):
        source,native=self.fixture()
        with self.assertRaisesRegex(ValueError,'NATIVE_FUNCTIONAL_PORT_MISSING'):
            geometry.planes(source,[0,0,256,256],native,[0,0,32,32],['left'])


if __name__=='__main__':unittest.main()
