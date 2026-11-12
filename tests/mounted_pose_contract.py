"""Synthetic mounted-model, inventory freeze and explicit non-approval contracts."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import sys
import types
import uuid

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import creature_auto_pose as common
import mounted_pose as mounted
from creature_auto_pose_contract import FakeSession, write_json


class FakeHorse:
    def __init__(self): self.calls = 0
    def get_inputs(self): return [types.SimpleNamespace(name="input")]
    def run(self, names, inputs):
        self.calls += 1
        xs = [200, 204, 220, 180, 70, 170, 165, 160, 185, 183, 180, 90, 80, 75, 105, 100, 95]
        ys = [70, 70, 85, 100, 100, 120, 160, 220, 120, 160, 220, 120, 160, 220, 120, 160, 220]
        ox, oy = np.zeros((1,17,512), np.float32), np.zeros((1,17,512), np.float32)
        for j in range(17):
            i = mounted.FLIP[j] if self.calls % 2 == 0 else j
            x = 255-xs[i] if self.calls % 2 == 0 else xs[i]
            ox[0,j,2*x] = .9; oy[0,j,2*ys[i]] = .9
        return ox, oy


def main():
    root = ROOT / "build" / "experiments" / "mounted-pose-contract" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    product = root / "product"; product.mkdir()
    masters = root / "AH00_"; masters.mkdir()
    image = Image.new("RGBA", (500, 500), (0,0,0,0))
    ImageDraw.Draw(image).rectangle((80,50,450,450), fill=(130,90,40,255))
    source = masters / "0_0.png"
    with source.open("xb") as f: image.save(f, format="PNG")
    source_sha = common.sha(source)
    inv = root / "inventory.json.gz"
    rows = [{"kind":"def", "identity":"AH00_", "group":g, "frame":0,
             "category":"other", "canvas":[96,64], "crop":[32,58], "anchor":[32,6],
             "resource":f"ah00_{g}.pcx", "native_lod":"H3sprite.lod"} for g in range(2)]
    with inv.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write(json.dumps({"rows":rows}).encode())
    common.PRODUCT = product
    argv = ["--inventory",str(inv),"--inventory-sha256",common.sha(inv),"--identity","AH00_",
            "--masters-dir",str(masters),"--operation-id","test-freeze"]
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout): assert mounted.freeze(argv) == 0
    frozen = json.loads(stdout.getvalue())
    assert frozen["native_slots"] == 2 and frozen["source_images"] == 1
    _, records, scope, groups = common.load_input(frozen["manifest"], frozen["sha256"])
    assert scope["missing"] == ["1_0.png"] and not scope["extra_pngs"] and len(records) == 1
    prediction, rgb = mounted.infer({"horse":FakeHorse(), "rider":FakeSession()}, records[0])
    assert "HORSE_AND_RIDER_MUST_BOTH_BE_REVIEWED" in prediction["flags"]
    assert prediction["rider_candidate"]["status"] == "AUTO_ESTIMATED_REVIEW_REQUIRED"
    horse, root_point = mounted.horse_features(prediction["points"])
    assert len(horse) == 5 and all(x > 0 for x in horse.values())
    registration = mounted.relative_registration(prediction, prediction)
    assert registration["uniform_scale"] == 1 and registration["native_registration"] == "NOT_MEASURED"
    assert registration["anchor_semantics"] == "estimated_horse_neck_tail_midpoint_not_ground"
    assert registration["rider_scale_status"] == "AUTO_ESTIMATED_REVIEW_REQUIRED"
    assert common.sha(source) == source_sha
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert mounted.freeze(argv) == 2  # Existing operation is never overwritten.
    print(json.dumps({"passed":True,"fixture":str(root),"checks":["AH_other_inventory_freeze", "missing_slots",
        "separate_horse_rider_models", "animal_flip_pairs", "combined_scale_candidate", "no_native_pass",
        "source_unchanged", "exclusive_freeze"]}))


if __name__ == "__main__": main()
