"""Offline source-contract tests; native rendering is qualified by the build gate."""
from __future__ import annotations

import hashlib
import io
import copy
import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
import logical_alpha as logical
from workspace import h3_root

RELEASE_ROOT = h3_root()
CONTRACT_SOURCE = RELEASE_ROOT / "codex-高清母版" / "制作资料" / "地图界面" / "道路逻辑遮罩" / "source-data-contract-r01.json"
CONTRACT_SOURCE_SHA = "62885fbe813b75e72de1cc90e31e65c4b7bd81c61825badb6bb3cfb847fb602a"
contract_document = json.loads(CONTRACT_SOURCE.read_bytes())
old_release_prefix = r"{H3-workspace}\H3HD\assets\CompleteOfficialRelease-20261006-v1"
new_release_prefix = str(RELEASE_ROOT)

def rebase_paths(value):
    if isinstance(value, str) and value.startswith(old_release_prefix):
        return new_release_prefix + value[len(old_release_prefix):]
    if isinstance(value, list):
        return [rebase_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: rebase_paths(item) for key, item in value.items()}
    return value

contract_document = rebase_paths(contract_document)
contract_test_root = PRODUCT / "build" / "tests"
contract_test_root.mkdir(parents=True, exist_ok=True)
contract_temp = tempfile.TemporaryDirectory(prefix="logical-alpha-rebased-", dir=contract_test_root)
contract_root = Path(contract_temp.name)
CONTRACT = contract_root / "source-data-contract-r01.json"
CONTRACT.write_text(json.dumps(contract_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
CONTRACT_SHA = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
if hashlib.sha256(CONTRACT_SOURCE.read_bytes()).hexdigest() != CONTRACT_SOURCE_SHA:
    raise RuntimeError("logical-alpha source contract bytes changed")
MATERIAL_BASE = RELEASE_ROOT / "codex-高清母版" / "制作资料" / "地图界面" / "道路逻辑遮罩" / "material-logical-road51-intake-20261015-r01" / "manifest.json"


class MaterialInputFixture:
    """TEST-only sources/provenance; borrow unchanged native mask data, never owner art."""

    def __init__(self):
        logical.OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix=".material-input-fixture-", dir=logical.OUTPUT_BASE)
        self.root = Path(self.temporary.name)
        self.h3root = self.root / "h3"
        self.map_source = self.h3root / "tools/map_ui_batch_r16.py"
        self.reader = self.h3root / "tools/art_town_batch_r16.py"
        self.map_source.parent.mkdir(parents=True)
        self.map_source.write_bytes(b"# TEST_ONLY producer binding; never executed\n")
        self.reader.write_bytes(b"# TEST_ONLY reader binding; never executed\n")
        self.legacy = json.loads(CONTRACT.read_bytes())
        catalog = self.h3root / logical.native.INVENTORY_RELATIVE
        catalog.parent.mkdir(parents=True)
        current_catalog = RELEASE_ROOT / "codex-map" / "native-resource-inventory.json.gz"
        self.legacy["source_authority"]["native_catalog"]["path"] = str(current_catalog)
        catalog.write_bytes(current_catalog.read_bytes())
        self.lod = self.root / "fixture-lod.data"
        self.lod.write_bytes(b"TEST_ONLY source identity; no native decoder verification is claimed\n")
        authority = self.legacy["source_authority"]
        authority["native_catalog"] = self.pin(catalog)
        authority["native_lod"] = self.pin(self.lod)
        authority["map_ui_producer"] = self.pin(self.map_source)
        authority["native_image_reader"] = {**self.pin(self.reader), "symbol": "art_town_batch_r16.py::native_image"}
        self.legacy_path = self.root / "legacy-mask-contract.json"
        self.write(self.legacy_path, self.legacy)
        original = rebase_paths(json.loads(MATERIAL_BASE.read_bytes()))
        self.base = {"schema": original["schema"], "fixture_scope": "TEST_ONLY; no source/art/writer approval",
                     "source_key_order": original["source_key_order"], "frames": original["frames"],
                     "board_layout": original["board_layout"], "provenance": []}
        self.base_path = self.root / "base.json"
        self.handoff_path = self.root / "handoff.json"
        self.facts_path = self.root / "facts.json"
        self.decision_path = self.root / "decision.json"
        self.manifest_path = self.root / "material-input.json"
        materials = []
        for index, family in enumerate(logical.FAMILY_ORDER):
            image = Image.new("RGBA", (576, 288))
            image.putdata([((x * 7 + index * 11) % 256, (y * 13 + x) % 256,
                            (x + y * 3 + index * 31) % 256, 255)
                           for y in range(288) for x in range(576)])
            raw = self.root / (family + "-synthetic-raw.png")
            board = self.root / (family + "-synthetic-board-3x.png")
            image.convert("RGB").save(raw)
            image.save(board)
            request = self.root / (family + "-synthetic-request.json")
            self.write(request, {"schema": "test-only.synthetic-source-request", "family": family,
                                 "model_inference_performed": False, "art_approved": False})
            raw_pin, board_pin = self.png_pin(raw), self.png_pin(board)
            receipt = self.root / (family + "-synthetic-identity-sampling.json")
            self.write(receipt, {"schema": "h3.master-expand.receipt.v1", "fixture_scope": "TEST_ONLY identity 3X source",
                                "target_scale": 3, "source_sha256": raw_pin["sha256"], "source_pixels": [576, 288],
                                "contract": {"entry": {"source_sha256": raw_pin["sha256"]}},
                                "png_sha256": board_pin["sha256"], "output_pixels": [576, 288],
                                "rgba_sha256": board_pin["rgba_sha256"], "model_inference_performed": False})
            materials.append({"identity": family, "raw_png": raw_pin, "generation_request": self.pin(request),
                              "sampled_board_3x_png": board_pin, "qualified_sampling_receipt": self.pin(receipt)})
        self.handoff = {"schema": "h3.map-ui.road-material-consumer-handoff-proposal.v1",
                        "material_epoch": "TEST_ONLY", "material_sources": materials}
        self.facts = {"schema": "h3.map-ui.road51-source-native-facts-readback.v1", "fixture_scope": "TEST_ONLY",
                      "current_source": {"map": self.pin(self.map_source), "town": self.pin(self.reader)},
                      "immutable_inputs": {"native_catalog": self.pin(catalog), "native_lod": self.pin(self.lod)},
                      "historical_sampling": {"fixture_scope": "TEST_ONLY; no old mutable dist or cache"},
                      "current_material_inputs": [{"family": row["identity"], "raw": row["raw_png"],
                                                   "generation_request": row["generation_request"],
                                                   "board3x": row["sampled_board_3x_png"],
                                                   "frozen_sampling_receipt": row["qualified_sampling_receipt"]}
                                                  for row in materials]}
        self.decision = {"schema": "h3.map-ui.road51.Root-readonly-data-decision.v1", "fixture_scope": "TEST_ONLY",
                         "status": "READONLY_ROAD51_INPUT_FACTS_ACCEPTED_FOR_TOOL_MATERIAL_ROLE_REVIEW",
                         "boundaries": {"this_is_not_Map_writer_authority": True},
                         "historical_source_data_semantics": {"Tool_independent_lod_verified": False}}
        self.save()

    @staticmethod
    def write(path, value):
        Path(path).write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    @staticmethod
    def pin(path):
        path = Path(path)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}

    @classmethod
    def png_pin(cls, path):
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            return {**cls.pin(path), "pixels": list(image.size), "stored_mode": image.mode,
                    "rgba_sha256": hashlib.sha256(rgba.tobytes()).hexdigest(),
                    "alpha_extrema": list(rgba.getchannel("A").getextrema())}

    def save(self):
        self.base["provenance"] = [{**self.pin(self.legacy_path), "role": "source-data-contract-r01.json"}]
        self.write(self.base_path, self.base)
        self.handoff["base_manifest"] = self.pin(self.base_path)
        self.write(self.handoff_path, self.handoff)
        self.facts["immutable_inputs"].update(base_manifest=self.pin(self.base_path),
                                             consumer_handoff=self.pin(self.handoff_path))
        self.write(self.facts_path, self.facts)
        self.decision.update(fact_readback=self.pin(self.facts_path), base_manifest=self.pin(self.base_path),
                             consumer_handoff=self.pin(self.handoff_path), current_source=self.pin(self.map_source),
                             current_native_reader=self.pin(self.reader))
        self.write(self.decision_path, self.decision)
        refs = {"base_manifest": self.base_path, "material_handoff": self.handoff_path,
                "source_facts": self.facts_path, "source_decision": self.decision_path}
        self.write(self.manifest_path, {"schema": logical.MATERIAL_MANIFEST_SCHEMA, "data_role": logical.MATERIAL_ROLE,
                                       **{name: {k: v for k, v in self.pin(path).items() if k != "bytes"}
                                          for name, path in refs.items()}})

    @contextlib.contextmanager
    def activated(self):
        with mock.patch.object(logical.native, "h3_root", return_value=self.h3root):
            yield self

    def batch(self, identities=None):
        with self.activated():
            return logical.validate_contract(self.manifest_path, self.pin(self.manifest_path)["sha256"], identities)

    def close(self):
        self.temporary.cleanup()


class MaterialInputContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MaterialInputFixture()
        self.addCleanup(self.fixture.close)

    def test_single_crop_spec_and_independent_positive_alpha_rgb_oracle(self):
        batch = self.fixture.batch()
        spec = logical._native_spec(batch, "material-contract-r01")
        self.assertEqual(spec["data_role"], "road-material-3x")
        self.assertEqual(len(spec["frames"]), 51)
        self.assertIn("mask_contract", spec["material_inputs"])
        alpha1 = 0
        for frame in batch["frames"]:
            self.assertEqual(frame["material"]["output_layout"], "native-crop")
            x, y = frame["geometry_3x"]["origin"]
            w, h = frame["geometry_3x"]["crop"]
            cell = frame["material"]["cell_3x"]
            with Image.open(frame["material"]["board"]["path"]) as board, \
                    Image.open(frame["material"]["mask_crop_3x"]["path"]) as mask:
                rgb = board.convert("RGB").crop((cell[0] + x, cell[1] + y, cell[0] + x + w, cell[1] + y + h))
                alpha = mask.getchannel("A")
                positive = alpha.point(lambda value: 255 if value else 0)
                visible = Image.composite(rgb, Image.new("RGB", rgb.size), positive)
                expected = Image.merge("RGBA", (*visible.split(), alpha)).tobytes()
            self.assertEqual(frame["material_expected_rgba"], expected)
            alpha1 += expected[3::4].count(1)
        self.assertGreater(alpha1, 0, "the independent fixture must cover alpha1 without RGB thresholding")

    def test_family_key_cell17_geometry_and_mask_drift_are_preflight_failures(self):
        original = copy.deepcopy(self.fixture.base)
        mutations = [lambda row: row.update(material_identity="WRONG"),
                     lambda row: row.update(key=["def", "DIRTRD", 0, 17]),
                     lambda row: row["material_cell"].update(frame=17),
                     lambda row: row["geometry_3x"]["origin"].__setitem__(0, 0),
                     lambda row: row["logical_masks"]["crop_3x"].update(sha256="0" * 64)]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                self.fixture.base = copy.deepcopy(original)
                mutation(self.fixture.base["frames"][0])
                self.fixture.save()
                with self.assertRaises(logical.LogicalAlphaError):
                    self.fixture.batch()

    def test_current_source_drift_is_not_resealed_or_reinterpreted(self):
        source = self.fixture.map_source
        raw = source.read_bytes()
        source.write_bytes(b"!" + raw[1:])
        with self.assertRaisesRegex(logical.LogicalAlphaError, "MATERIAL_INPUT_DRIFT"):
            self.fixture.batch()


class LogicalAlphaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.batch = logical.validate_contract(CONTRACT, CONTRACT_SHA)

    def test_full_frozen_contract_and_inventory_binding(self) -> None:
        batch = self.batch
        self.assertEqual(batch["identities"], ["DIRTRD", "GRAVRD", "COBBRD"])
        self.assertEqual(len(batch["frames"]), 51)
        self.assertEqual(batch["inventory_sha256"],
                         batch["contract"]["source_authority"]["native_catalog"]["sha256"])
        self.assertEqual(batch["source_png_bytes"], 8328)
        self.assertEqual(batch["source_alpha_cells"], 27993)
        self.assertEqual(batch["source_alpha_positive_pixels"], 18836)
        self.assertFalse(batch["independent_lod_verified"])
        for index, frame in enumerate(batch["frames"]):
            family_index, frame_index = divmod(index, 17)
            identity = logical.FAMILY_ORDER[family_index]
            self.assertEqual(frame["id"], f"{identity}_g0_f{frame_index}")
            self.assertEqual(frame["key"], ["def", identity, 0, frame_index])
            self.assertEqual(frame["geometry_3x"]["canvas"], [value * 3 for value in frame["geometry_1x"]["canvas"]])
            self.assertEqual(frame["geometry_3x"]["crop"], [value * 3 for value in frame["geometry_1x"]["crop"]])
            self.assertEqual(frame["geometry_3x"]["origin"], [value * 3 for value in frame["geometry_1x"]["origin"]])

    def test_explicit_family_selection_preserves_producer_order(self) -> None:
        batch = logical.validate_contract(CONTRACT, CONTRACT_SHA, ["COBBRD", "DIRTRD"])
        self.assertEqual(batch["identities"], ["DIRTRD", "COBBRD"])
        self.assertEqual(len(batch["frames"]), 34)
        self.assertEqual(batch["frames"][17]["id"], "COBBRD_g0_f0")
        self.assertEqual(batch["source_alpha_positive_pixels"], 12465)

    def test_frozen_manifest_sha_is_required(self) -> None:
        with self.assertRaisesRegex(logical.LogicalAlphaError, "MANIFEST_SHA256_MISMATCH"):
            logical.validate_contract(CONTRACT, "0" * 64)

    def test_logical_alpha_input_tracks_rgb_and_keeps_alpha_authoritative(self) -> None:
        def png(rgb: tuple[int, int, int]) -> bytes:
            image = Image.new("RGBA", (2, 1))
            image.putdata([(*rgb, 1), (*rgb, 0)])
            target = io.BytesIO()
            image.save(target, format="PNG")
            return target.getvalue()

        white = png((255, 255, 255))
        alpha_sha = hashlib.sha256(bytes([1, 0])).hexdigest()
        _alpha, white_info = logical._decode_logical_png(white, [2, 1], alpha_sha, 1, "fixture")
        self.assertTrue(white_info["rgb_encoding"]["all_rgb_pixels_white"])
        _alpha, colored_info = logical._decode_logical_png(png((254, 255, 255)), [2, 1], alpha_sha, 1, "fixture")
        self.assertFalse(colored_info["rgb_encoding"]["all_rgb_pixels_white"])
        self.assertEqual(colored_info["rgb_encoding"]["classification"], "RGBA_RGB_NONWHITE_ALPHA_MASK")

    def test_native_spec_carries_role_identity_and_non_lod_claim(self) -> None:
        spec = logical._native_spec(self.batch, "road-mask-r01")
        self.assertEqual(spec["schema"], logical.SPEC_SCHEMA)
        self.assertEqual(spec["operation_id"], "road-mask-r01")
        self.assertEqual(spec["data_role"], "logical-alpha-mask")
        self.assertFalse(spec["independent_lod_verified"])
        self.assertEqual(len(spec["frames"]), 51)
        self.assertEqual(spec["frames"][0]["source_sha256"], self.batch["frames"][0]["source_sha256"])

    def test_output_must_be_a_named_child_of_ignored_root(self) -> None:
        valid = logical.OUTPUT_BASE / "road-mask-r01"
        self.assertEqual(logical._output_path(valid, "road-mask-r01", must_exist=False),
                         Path(__import__("os").path.abspath(valid)))
        with self.assertRaisesRegex(logical.LogicalAlphaError, "OUTPUT_MUST_BE_OPERATION_ID_CHILD"):
            logical._output_path(logical.PRODUCT / "outside", "road-mask-r01", must_exist=False)
        with self.assertRaisesRegex(logical.LogicalAlphaError, "OPERATION_ID_INVALID"):
            logical._output_path(logical.OUTPUT_BASE / "bad/name", "bad/name", must_exist=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
