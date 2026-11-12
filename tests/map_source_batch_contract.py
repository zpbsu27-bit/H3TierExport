"""Contracts for byte-pinned, exact-grid road/river source recovery."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import map_source_batch as batch


class MapSourceBatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.geometry = {"canvas_1x": [32, 32], "crop_1x": [32, 32], "origin_1x": [0, 0]}
        self.key = ("def", "COBBRD", 0, 0)

    def make_source(self, name="parent.png", size=(128, 128), paint=None):
        rgba = np.zeros((size[1], size[0], 4), dtype=np.uint8)
        rgba[..., :3] = [90, 70, 50]
        if paint is not None:
            x, y, alpha = paint
            rgba[y, x] = [120, 90, 60, alpha]
        path = self.root / name
        Image.fromarray(rgba).save(path)
        return path

    def test_exact_rational_parent_is_candidate_without_source_rewrite(self):
        path = self.make_source()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        row = batch.make_source_row("road", "TRDC000", self.key, path, before, [128, 128],
            {"historical_sha_matches_copy": True}, {self.key: {"canvas": [32, 32], "crop": [32, 32], "anchor": [0, 0]}})
        self.assertEqual(row["source_status"], "EXACT_GRID_CANDIDATE")
        layer = batch.rational_native_roi_layer(row["source"], row["key"], row["geometry"], row["ratio"], [0, 0, 32, 32])["layer"]
        self.assertEqual(layer["source_a"]["source_layout"], "rational-native-grid")
        self.assertEqual(layer["source_a"]["source_region"]["pixels_per_native_ratio"], [4, 1])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_visible_alpha_outside_native_crop_is_redraw_not_silently_clipped(self):
        path = self.make_source(size=(128, 128), paint=(8, 8, 255))
        key = self.key
        geometry = {"canvas": [32, 32], "crop": [16, 16], "anchor": [8, 8]}
        row = batch.make_source_row("road", "TRDC000", key, path, batch.sha_file(path), [128, 128],
            {}, {key: geometry})
        self.assertEqual(row["source_status"], "REDRAW_REQUIRED")
        self.assertEqual(row["reason"], "VISIBLE_ALPHA_OUTSIDE_NATIVE_CROP")
        self.assertEqual(row["outside_crop_alpha"]["visible_pixels_alpha8_ge_16"], 1)

    def test_subvisible_outcrop_is_recorded_for_sampler_receipt(self):
        path = self.make_source(size=(128, 128), paint=(8, 8, 1))
        key = self.key
        geometry = {"canvas": [32, 32], "crop": [16, 16], "anchor": [8, 8]}
        row = batch.make_source_row("road", "TRDC000", key, path, batch.sha_file(path), [128, 128],
            {}, {key: geometry})
        self.assertEqual(row["source_status"], "EXACT_GRID_CANDIDATE")
        self.assertEqual(row["outside_crop_alpha"]["positive_pixels"], 1)
        self.assertEqual(row["outside_crop_alpha"]["visible_pixels_alpha8_ge_16"], 0)

    def test_nonuniform_parent_aspect_is_redraw_not_stretched(self):
        path = self.make_source(size=(128, 120))
        key = self.key
        row = batch.make_source_row("river", "CLRRVR00", key, path, batch.sha_file(path), [128, 120],
            {}, {key: {"canvas": [32, 32], "crop": [32, 32], "anchor": [0, 0]}})
        self.assertEqual(row["source_status"], "REDRAW_REQUIRED")
        self.assertEqual(row["reason"], "PARENT_ASPECT_DIFFERS_FROM_NATIVE_CANVAS")

    def decision_inputs(self):
        row = {"stem": "TRDC000", "key": ["def", "COBBRD", 0, 0], "kind": "road",
               "source_status": "EXACT_GRID_CANDIDATE", "source": {"sha256": "a" * 64}}
        matched = {"matches": [{"width_delta_candidate_minus_native": 0,
                                 "center_delta_candidate_minus_native": 0}],
                   "missing_native_mouths": [], "new_candidate_mouths": []}
        empty = {"matches": [], "missing_native_mouths": [], "new_candidate_mouths": []}
        frame = {"key": row["key"], "candidate_path": "candidate.png", "registration": {"valid": True},
                 "per_edge_comparison": {"top": empty, "bottom": matched, "left": empty, "right": matched}}
        seam = {"frames": [frame], "issues": [], "candidate_neighbor_checks": [
            {"frame_a": row["key"], "frame_b": ["def", "COBBRD", 0, 1], "candidate_matches_native_profile_on_both_sides": True}],
            "road_functional_port_stress": [], "river_functional_port_stress": []}
        motion = {"schema": "h3.motion.run.v1", "results": [{"status": "VERIFIED"}]}
        verified = {"status": "COMPLETED", "failed": 0}
        road = {"schema":batch.road_quality.REPORT,"items": [{"key": row["key"], "topology": {"status": "PASS"},
                 "shared_interface":{"status":"PASS"}}],"pairs":[{'a':row['key'],'b':['def','COBBRD',0,1],
                    'status':'PASS','shared_interface_verified':True}]}
        return row,seam,motion,verified,road

    def test_native_ports_and_verified_painted_neighbors_are_required_together(self):
        row,seam,motion,verified,road=self.decision_inputs()
        result = batch.candidate_decisions({"source_rows": [row]}, seam, motion, verified, road)
        self.assertEqual(result[0]["decision"], "RESTORE_CANDIDATE")

    def test_bad_neighbor_blocks_restore_even_if_native_port_widths_match(self):
        row,seam,motion,verified,road=self.decision_inputs()
        seam['candidate_neighbor_checks'][0]['candidate_matches_native_profile_on_both_sides']=False
        result=batch.candidate_decisions({'source_rows':[row]},seam,motion,verified,road)[0]
        self.assertEqual(result['decision'],'REVIEW_REQUIRED')
        self.assertIn('LEGAL_NEIGHBOR_PROFILE_NOT_COMPATIBLE',result['reason'])

    def test_missing_or_failed_painted_interface_does_not_turn_into_restore(self):
        for state in ('NOT_MEASURED','FAIL'):
            row,seam,motion,verified,road=self.decision_inputs()
            road['items'][0]['shared_interface']['status']=state
            result=batch.candidate_decisions({'source_rows':[row]},seam,motion,verified,road)[0]
            self.assertEqual(result['decision'],'REVIEW_REQUIRED')
            self.assertIn('ROAD_PAINTED_INTERFACE_NOT_VERIFIED',result['reason'])

    def test_failed_or_missing_legal_pairs_are_not_hidden_by_topology_pass(self):
        for state in ('FAIL','MISSING','ABSENT'):
            row,seam,motion,verified,road=self.decision_inputs()
            if state=='ABSENT':road['pairs']=[]
            else:road['pairs'][0]['status']=state
            result=batch.candidate_decisions({'source_rows':[row]},seam,motion,verified,road)[0]
            self.assertEqual(result['decision'],'REVIEW_REQUIRED')
            self.assertIn('ROAD_LEGAL_PAINTED_NEIGHBORS_NOT_VERIFIED',result['reason'])

    def test_request_sha_is_checked_before_parsing_or_other_source_reads(self):
        p=self.root/'request.json';p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'SHA_DRIFT'):
            batch.pin_request({'path':str(p),'sha256':'0'*64},'fixture')

    def test_old_restore_decision_or_unbound_verification_creates_no_masters(self):
        report_path=self.root/'map-source-batch-report.json';verification_path=self.root/'verification.json'
        master=self.root/'masters';request={'master_root':str(master),'source_rows':[]}
        report_path.write_text(json.dumps({'source_rows':[],'decision_algorithm_sha256':'old'}))
        verification_path.write_text(json.dumps({'status':'VERIFIED'}))
        with self.assertRaisesRegex(ValueError,'ALGORITHM_CHANGED_RERUN'):batch.stage_restore(self.root,request)
        self.assertFalse(master.exists())
        current=batch.sha_file(Path(batch.__file__))
        report_path.write_text(json.dumps({'source_rows':[],'decision_algorithm_sha256':current}))
        verification_path.write_text(json.dumps({'status':'VERIFIED','decision_algorithm_sha256':current,
                                                'decision_report':{'path':str(report_path),'sha256':'0'*64}}))
        with self.assertRaisesRegex(ValueError,'VERIFIED_REPORT_BINDING_DIFFERS'):batch.stage_restore(self.root,request)
        self.assertFalse(master.exists())

    def test_verify_recomputes_road_quality_instead_of_trusting_saved_pass(self):
        row,seam,motion,verified,road=self.decision_inputs()
        road_path=self.root/'road-batch-report.json';road_path.write_text(json.dumps(road))
        (self.root/'verify-seam-audit.json').write_text(json.dumps(seam))
        prior={'decision_algorithm_sha256':batch.sha_file(Path(batch.__file__)),
               'road_batch_report':batch.ref(road_path),'source_rows':[]}
        (self.root/'map-source-batch-report.json').write_text(json.dumps(prior))
        request={'operation_id':'fixture','native_inventory':{},'editor_source_proof':{},'source_rows':[row]}
        with patch.object(batch,'collect_batch_runs',return_value=([],[],[],[],motion,verified)),\
             patch.object(batch.road_quality,'freeze',side_effect=lambda spec:spec),\
             patch.object(batch.road_quality,'analyze',return_value={'changed':'FAIL'}) as replay:
            with self.assertRaisesRegex(ValueError,'ROAD_QUALITY_REPLAY_DIFFERS'):
                batch.stage_verify(self.root,request,self.root/'fixture.exe')
        replay.assert_called_once()
        self.assertFalse((self.root/'verification.json').exists())

    def test_current_bound_restore_preserves_original_bytes(self):
        source=self.make_source(paint=(50,50,255));before=source.read_bytes()
        row={'key':list(self.key),'decision':'RESTORE_CANDIDATE','source_sha256':batch.sha_file(source)}
        report=self.root/'map-source-batch-report.json';report.write_text(json.dumps({
            'decision_algorithm_sha256':batch.sha_file(Path(batch.__file__)),'source_rows':[row]}))
        (self.root/'verification.json').write_text(json.dumps({'status':'VERIFIED',
            'decision_algorithm_sha256':batch.sha_file(Path(batch.__file__)),'decision_report':batch.ref(report)}))
        master=self.root/'masters';request={'operation_id':'fixture','master_root':str(master),
            'source_rows':[{'key':list(self.key),'source':batch.ref(source)}]}
        result=batch.stage_restore(self.root,request)
        self.assertEqual(result['restored'],1)
        self.assertEqual((master/'COBBRD/0_0.png').read_bytes(),before)
        self.assertEqual(source.read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
