"""Deterministic pose/review contracts; retained synthetic fixtures, no network."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys
import types
import uuid

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import creature_auto_pose as pose


def write_json(path, document):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False)


def must_fail(fn, code):
    try:
        fn()
    except (ValueError, OSError) as error:
        assert code in str(error), (code, str(error))
    else:
        raise AssertionError("expected failure: " + code)


class FakeSession:
    """Flip-consistent synthetic model outputs, independent of real provider."""
    def __init__(self, *args, **kwargs):
        self.calls = 0

    def get_inputs(self):
        return [types.SimpleNamespace(name="input")]

    def run(self, names, inputs):
        self.calls += 1
        xs = [96, 92, 100, 88, 104, 75, 115, 65, 125, 60, 130, 80, 110, 80, 110, 80, 110]
        ys = [30, 28, 28, 30, 30, 70, 70, 100, 100, 125, 125, 130, 130, 170, 170, 220, 220]
        ox, oy = np.zeros((1, 17, 384), np.float32), np.zeros((1, 17, 512), np.float32)
        for j in range(17):
            i = pose.FLIP_INDEX[j] if self.calls % 2 == 0 else j
            x = 191 - xs[i] if self.calls % 2 == 0 else xs[i]
            ox[0, j, 2*x] = 0.9
            oy[0, j, 2*ys[i]] = 0.9
        return ox, oy


def main():
    alpha = np.zeros((6, 9), dtype=np.uint8)
    alpha[1:4, 1:3] = 255
    alpha[4, 3] = 1  # Diagonally connected antialiasing is retained.
    alpha[1, 7] = 1
    alpha[4, 8] = 1
    alpha_before = alpha.copy()
    dust = pose.detached_alpha1_diagnostic(alpha)
    assert dust["isolated_alpha1_components"] == 2 and dust["isolated_alpha1_pixels"] == 2
    assert dust["positive_alpha_components"] == 3 and np.array_equal(alpha, alpha_before)
    assert dust["other_positive_component_bbox"] == [1, 1, 4, 5]
    fixture = ROOT / "build" / "experiments" / "creature-auto-pose-contract" / uuid.uuid4().hex
    fixture.mkdir(parents=True, exist_ok=False)
    product = fixture / "product"
    product.mkdir()
    masters = fixture / "masters" / "TEST"
    masters.mkdir(parents=True)
    image = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((130, 70, 270, 550), fill=(180, 120, 70, 255))
    for name in ("0_0.png", "0_1.png", "extra.png"):
        with (masters / name).open("xb") as stream:
            image.save(stream, format="PNG")
    before = {p.name: pose.sha(p) for p in masters.iterdir()}
    inv = fixture / "inventory.json.gz"
    rows = [{"kind": "def", "identity": "TEST", "group": 0, "frame": i,
             "canvas": [100, 100], "crop": [30, 70], "anchor": [20, 20],
             "resource": f"test{i}.pcx", "native_lod": "H3sprite.lod"} for i in range(3)]
    with inv.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write(json.dumps({"rows": rows}).encode())
    slots = [{"key": ["def", "TEST", 0, i],
              "geometry": {"canvas_1x": [100, 100], "crop_1x": [30, 70], "origin_1x": [20, 20]},
              "resource": f"test{i}.pcx", "native_lod": "H3sprite.lod"} for i in range(3)]
    snapshot = [{"path": str(p), "sha256": pose.sha(p), "bytes": p.stat().st_size} for p in masters.iterdir()]
    audit = fixture / "audit.json"
    write_json(audit, {"schema": "h3.creature.scale-report-manifest.v1", "identity": "TEST",
                       "category": "infantry", "masters_dir": str(masters),
                       "native_inventory": {"path": str(inv), "sha256": pose.sha(inv)},
                       "native_slots": slots, "source_snapshot": snapshot})
    request = fixture / "request.json"
    write_json(request, {"schema": "h3.creature.auto-pose.request.v1",
                         "audit_manifest": {"path": str(audit), "sha256": pose.sha(audit)},
                         "comparison_groups": [{"id": "standing", "pose_group": "standing",
                                                "direction": "right", "reference": "0_0",
                                                "slots": ["0_0", "0_1"],
                                                "visual_feature_alignment": {"mode": "full_opaque_foreground"}}]})
    ref, records, scope, groups = pose.load_input(request, pose.sha(request))
    assert len(records) == 2 and scope["native_slots"] == 3
    assert scope["missing"] == ["0_2.png"] and scope["extra_pngs"] == ["extra.png"]
    must_fail(lambda: pose.load_input(request, "0" * 64), "PIN_SHA_DRIFT")
    must_fail(lambda: pose.load_input(masters / "0_0.png", "0" * 64), "PIN_SHA_DRIFT")

    model = fixture / "model.onnx"
    model.write_bytes(b"synthetic model pin; never inference")
    pose.PRODUCT = product
    pose.MODEL_SHA = pose.sha(model)
    pose.load_provider = lambda: {"model_path": str(model), "model_sha256": pose.MODEL_SHA,
                                 "architecture": "test only", "onnxruntime": "test"}
    fake = types.SimpleNamespace(SessionOptions=lambda: types.SimpleNamespace(), InferenceSession=FakeSession)
    sys.modules["onnxruntime"] = fake
    out = product / "build" / "experiments" / "creature-auto-pose" / "unit-r01"
    args = (str(request), pose.sha(request), str(out), "unit-r01")
    assert pose.execute("check", *args)["status"] == "READY_FOR_LOCAL_INFERENCE"
    assert not out.exists()
    run = pose.execute("run", *args)
    assert run["logical_slots"] == 2 and run["unique_source_images"] == 1
    report = pose.read_json(out / "pose-report.json")
    assert report["status"] == "AUTO_ESTIMATED_REVIEW_REQUIRED"
    assert not report["anatomical_accuracy_proven"] and not report["native_registration_proven"]
    assert not report["output_is_master"] and not report["automatic_art_approval"]
    assert all(abs(x["uniform_scale"] - 1) < 1e-6 for x in report["comparisons"][0]["items"])
    assert all(x["native_registration"] == "NOT_MEASURED" for x in report["comparisons"][0]["items"])
    assert report["feature_provider"]["status"] in ("AVAILABLE", "UNAVAILABLE")
    assert all(x["visual_feature_alignment"]["status"] in ("REVIEW_REQUIRED", "NOT_COMPARABLE", "UNAVAILABLE")
               and x["visual_feature_alignment"]["candidate_only"]
               and x["visual_feature_alignment"]["body_scale"] == "NOT_MEASURED"
               for x in report["comparisons"][0]["items"])
    assert pose.execute("verify", *args)["status"] == "VERIFIED_REVIEW_REQUIRED"
    must_fail(lambda: pose.execute("run", *args), "OUTPUT_ALREADY_EXISTS")
    must_fail(lambda: pose.execute("check", str(request), pose.sha(request), str(out), "../bad"), "OPERATION_ID_INVALID")
    assert before == {p.name: pose.sha(p) for p in masters.iterdir()}

    # A corrupted frozen slot geometry is rejected even with the actual inventory SHA.
    bad = json.loads(audit.read_text(encoding="utf-8"))
    bad["native_slots"][0]["geometry"]["crop_1x"][0] += 1
    bad_path = fixture / "bad-audit.json"
    write_json(bad_path, bad)
    must_fail(lambda: pose.load_input(bad_path, pose.sha(bad_path)), "AUDIT_NATIVE_GEOMETRY_DRIFT")
    invalid_regions = json.loads(request.read_text(encoding="utf-8"))
    invalid_regions["comparison_groups"][0]["visual_feature_alignment"] = {
        "mode": "explicit_source_regions", "regions": {"0_0": [0, 0, 400, 600]}}
    invalid_path = fixture / "invalid-regions.json"
    write_json(invalid_path, invalid_regions)
    must_fail(lambda: pose.load_input(invalid_path, pose.sha(invalid_path)), "FEATURE_REGION_SCOPE_INVALID")
    print(json.dumps({"passed": True, "fixture": str(fixture), "checks": [
        "current_inventory_and_geometry", "missing_and_extra", "source_hash_pin", "duplicate_sha_logical_bindings",
        "horizontal_flip_reconciliation", "uniform_relative_transform", "preview_not_master",
        "source_unchanged", "isolated_alpha1_detection_no_pixel_change", "explicit_feature_regions",
        "feature_candidates_do_not_prove_body_scale", "exclusive_output", "run_verify"]}))


if __name__ == "__main__":
    main()
