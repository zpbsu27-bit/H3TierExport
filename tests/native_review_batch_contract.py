"""Synthetic contracts for manifest binding and safe native review dispatch."""
from __future__ import annotations

import contextlib
import copy
from fractions import Fraction
import gzip
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from PIL import Image
from companion_cli_contract import ACTIVE_COMPANIONS

PRODUCT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT / "scripts" / "review_native_batch.py"
sys.path.insert(0, str(PRODUCT / "scripts"))
spec = importlib.util.spec_from_file_location("review_native_batch_contract_target", SCRIPT)
assert spec and spec.loader
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch
spec.loader.exec_module(batch)


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_png(path: Path, size: tuple[int, int], seed: int = 0) -> None:
    image = Image.new("RGBA", size)
    pixels = []
    for y in range(size[1]):
        for x in range(size[0]):
            pixels.append(((x * 37 + seed) % 256, (y * 41 + seed * 3) % 256,
                           ((x + y) * 19 + seed * 7) % 256, (1, 16, 128, 255)[(x + y) % 4]))
    image.putdata(pixels)
    image.save(path, format="PNG")


def canonical_binding(inventory_sha: str, key: list, targets: dict) -> str:
    raw = json.dumps({"inventory_sha256": inventory_sha, "key": key, "targets": targets},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha_bytes(raw)


class NativeReviewBatchContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-review-batch-contract-")
        self.root = Path(self.temp.name)
        self.h3root = self.root / "h3-fixture"
        self.inventory_path = self.h3root / batch.INVENTORY_RELATIVE
        self.inventory_path.parent.mkdir(parents=True)
        self.targets = {"canvas": [4, 4], "crop": [2, 2], "anchor": [1, 1]}
        self.key = ["def", "REVIEWFIXTURE", 0, 0]
        row = {"kind": "def", "identity": "REVIEWFIXTURE", "group": 0, "frame": 0,
               "resource": "reviewfixture.pcx", "category": "contract", "canvas": self.targets["canvas"],
               "crop": self.targets["crop"], "anchor": self.targets["anchor"], "label": "fixture"}
        pcx_row = {"kind": "pcx", "identity": "REVIEWFIXTURE", "group": None, "frame": None,
                   "resource": "reviewfixture.pcx", "category": "contract", "canvas": self.targets["canvas"],
                   "crop": self.targets["crop"], "anchor": self.targets["anchor"], "label": "fixture"}
        self.inventory_path.write_bytes(gzip.compress(json.dumps({"rows": [row, pcx_row]}).encode("utf-8")))
        self.inventory_sha = sha(self.inventory_path)

        self.source = self.root / "source.png"
        self.reference = self.root / "reference.png"
        write_png(self.source, (6, 6), 3)
        write_png(self.reference, (4, 4), 7)
        self.source_sha = sha(self.source)
        self.reference_sha = sha(self.reference)
        self.manifest_path = self.root / "manifest.json"
        self.renderer = self.root / "renderer.exe"
        self.renderer.write_bytes(b"synthetic renderer identity; never executed as an OS image")
        self.renderer_sha = sha(self.renderer)
        self._write_manifest(with_reference=True)
        batch.OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

    def test_canonical_inventory_prefers_direct_bound_source_root(self):
        direct = self.h3root / batch.BOUND_INVENTORY_RELATIVE
        direct.parent.mkdir(parents=True)
        direct.write_bytes(gzip.compress(json.dumps({"rows": []}).encode("utf-8")))
        self.assertEqual(batch.canonical_inventory_path(self.h3root), direct.resolve())

    def test_canonical_inventory_keeps_versioned_layout_compatibility(self):
        self.assertEqual(batch.canonical_inventory_path(self.h3root), self.inventory_path.resolve())

    def test_canonical_inventory_keeps_legacy_layout_compatibility(self):
        root = self.root / "legacy-root"
        legacy = root / batch.LEGACY_INVENTORY_RELATIVE
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(gzip.compress(json.dumps({"rows": []}).encode("utf-8")))
        self.assertEqual(batch.canonical_inventory_path(root), legacy.resolve())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_manifest(self, *, with_reference: bool, source_sha: str | None = None,
                        reference_path: Path | None = None) -> str:
        item = {"id": "REVIEW_FIXTURE", "key": self.key,
                "source_png": str(self.source), "source_sha256": source_sha or self.source_sha}
        if with_reference:
            path = reference_path or self.reference
            item["reference"] = {"path": str(path), "sha256": sha(path), "layout": "native-canvas"}
        manifest = {"schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha},
                    "items": [item]}
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.manifest_path.write_bytes(raw)
        return sha_bytes(raw)

    def _new_output(self, label: str) -> Path:
        path = batch.OUTPUT_BASE / f"contract-{label}-{uuid.uuid4().hex}"
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _authored_view_fixture(self, *, pcx=False):
        write_png(self.source, (61, 61), 11)
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        key = ["pcx", "REVIEWFIXTURE"] if pcx else self.key
        targets = batch.official_targets(rows[tuple(key)], tuple(key))
        sample = [n * 3 for n in targets["crop_1x"]] if pcx else [15, 15]
        rect = [0, 0, *sample] if pcx else [1, 2, 6, 6]
        projection = [Fraction(rect[i] * 61, sample[i % 2]) for i in range(4)]
        registration = {"schema": "h3.map-ui.ui-state-row-extraction.v1",
                        "native_catalog": {"path": str(self.inventory_path), "sha256": self.inventory_sha}}
        window = {"whole_sample_pixels_3x": sample, "whole_sample_crop_rect_3x": rect,
                  "registration": registration}
        if pcx:
            mask = self.root / "functional-mask.png"
            Image.new("RGBA", tuple(sample), (9, 27, 41, 128)).save(mask)
            registration.update(schema="h3.map-ui.ui-native-alpha-registration.v1",
                                official_mask={"path": str(mask), "sha256": sha(mask), "pixels": sample},
                                operation="min(model_alpha,official_alpha)", model_RGB_unchanged=True,
                                opacity_created=False, raw_whole_model_unchanged=True,
                                required_border_missing_failure=True)
            window = {"whole_image_source": True, "existing_native_alpha_registration": registration}
        case = {"key": [*key, None, None] if pcx else key,
                "source": {"path": str(self.source), "sha256": sha(self.source)},
                "native_targets": {"canvas_1x": targets["canvas_1x"],
                                   "crop_1x": None if pcx else targets["crop_1x"],
                                   "origin_1x": None if pcx else targets["origin_1x"]},
                "target_frame_pixels_1x": targets["crop_1x"],
                "exact_source_rect_raw_rational": [[v.numerator, v.denominator] for v in projection],
                "authored_atlas_window": window}
        authority = self.root / "authored-view.json"
        document = {"schema": "h3.map-ui.direct-native-review-consumer-feedback.v1", "cases": [case]}
        authority.write_text(json.dumps(document), encoding="utf-8")
        item = {"id": "AUTHORED_VIEW", "key": key, "source_png": str(self.source),
                "source_sha256": sha(self.source), "registration": "authored-atlas-window-3x",
                "render_scales": [3], "authored_view": {"path": str(authority), "sha256": sha(authority)}}
        return item, rows, authority, document

    def test_authored_atlas_keeps_exact_projection_and_official_slot(self):
        item, rows, _, doc = self._authored_view_fixture()
        spec, metadata = batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
        view = spec["atlas_view"]
        self.assertEqual(view["sample_pixels_3x"], [15, 15])
        self.assertEqual(view["frame_rect_3x"], [1, 2, 6, 6])
        self.assertEqual(view["source_rect_raw_rational"], doc["cases"][0]["exact_source_rect_raw_rational"])
        self.assertEqual(spec["targets"], {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]})
        self.assertEqual(spec["render_scales"], [3])
        self.assertIn(item["authored_view"], metadata["authored_inputs"])

    def _authored_state_fixture(self):
        item, rows, path, _ = self._authored_view_fixture()
        targets = batch.official_targets(rows[tuple(self.key)], tuple(self.key))
        case = {"key": self.key, "source": {"path": str(self.source), "sha256": sha(self.source)},
                "native_targets": targets, "author_source_rect": [2, 4, 11, 10],
                "padding": {"offset": [1, 2]},
                "source_vertices_padding_local": [[1, 2], [12, 2], [12, 12], [1, 12]],
                "target_vertices_native_1x": [[0, 0], [2, 0], [2, 2], [0, 2]],
                "triangles": [[0, 1, 2], [0, 2, 3]], "expected_3x_crop": [6, 6]}
        doc = {"schema": "h3.map-ui.blue6-author-geometry-qa-plan.v1", "items": [case],
               "author_measurement": {"rects_are_explicit_author_choice_not_AI_layout_prediction": True,
                                      "all_selected_source_rects_inside_original": True},
               "native_evidence": {"inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha}},
               "art_approved": False, "formal_source_intake": False, "game_deployed": False, "runtime_accepted": False}
        path.write_text(json.dumps(doc), encoding="utf-8")
        item.update(registration="authored-source-view-3x", authored_view={"path": str(path), "sha256": sha(path)})
        return item, rows, path, doc

    def test_author_bound_state_view_uses_exact_declared_roi_without_whole_object_padding(self):
        item, rows, _, _ = self._authored_state_fixture()
        spec, meta = batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
        self.assertEqual(spec["source_rect"], [2, 4, 11, 10])
        self.assertEqual(spec["state_view"]["source_rect_xywh"], [2, 4, 11, 10])
        self.assertEqual(spec["render_scales"], [3])
        self.assertIsNone(meta["common_k"])
        self.assertEqual(meta["source_view_proof"]["retained_positive_pixels"], 110)
        self.assertEqual(meta["source_view_proof"]["excluded_positive_pixels"], 61 * 61 - 110)

    def test_author_bound_state_view_refuses_inferred_geometry_and_insufficient_source(self):
        item, rows, path, original = self._authored_state_fixture()
        batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
        changes = [lambda d: d["author_measurement"].__setitem__("rects_are_explicit_author_choice_not_AI_layout_prediction", False),
                   lambda d: d.__setitem__("art_approved", True),
                   lambda d: d["items"][0]["author_source_rect"].__setitem__(2, 5),
                   lambda d: d["items"][0]["target_vertices_native_1x"][1].__setitem__(0, 3)]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                doc = copy.deepcopy(original); change(doc)
                path.write_text(json.dumps(doc), encoding="utf-8")
                item["authored_view"]["sha256"] = sha(path)
                with self.assertRaisesRegex(batch.ReviewError, "AUTHORED_"):
                    batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)

    def test_authored_atlas_rejects_wrong_authority_window_source_or_tier(self):
        item, rows, path, original = self._authored_view_fixture()
        changes = [lambda d: d["cases"][0]["key"].__setitem__(1, "OTHER"),
                   lambda d: d["cases"].append(copy.deepcopy(d["cases"][0])),
                   lambda d: d["cases"][0]["exact_source_rect_raw_rational"][0].__setitem__(0, 62),
                   lambda d: d["cases"][0]["authored_atlas_window"]["whole_sample_crop_rect_3x"].__setitem__(2, 5),
                   lambda d: d["cases"][0]["authored_atlas_window"]["whole_sample_pixels_3x"].__setitem__(0, 62),
                   lambda d: d["cases"][0]["source"].__setitem__("sha256", "0" * 64)]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                doc = copy.deepcopy(original); change(doc)
                path.write_text(json.dumps(doc), encoding="utf-8")
                trial = copy.deepcopy(item); trial["authored_view"]["sha256"] = sha(path)
                with self.assertRaisesRegex(batch.ReviewError, "AUTHORED_|ATLAS_"):
                    batch._resolve_entry(trial, rows, self.inventory_path, self.inventory_sha)
        path.write_text(json.dumps(original), encoding="utf-8")
        item["authored_view"]["sha256"] = sha(path)
        item["render_scales"] = [1, 3]
        with self.assertRaisesRegex(batch.ReviewError, "AUTHORED_3X_ONLY"):
            batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)

    def test_authored_pcx_alpha_cap_is_explicit_and_pins_the_existing_mask(self):
        item, rows, path, doc = self._authored_view_fixture(pcx=True)
        spec, metadata = batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
        self.assertEqual(spec["key"], ["pcx", "REVIEWFIXTURE"])
        self.assertEqual(spec["alpha_cap_3x"]["operation"], "min(model_alpha,official_alpha)")
        mask = doc["cases"][0]["authored_atlas_window"]["existing_native_alpha_registration"]["official_mask"]
        self.assertEqual(spec["alpha_cap_3x"]["sha256"], mask["sha256"])
        self.assertIn({"path": mask["path"], "sha256": mask["sha256"]}, metadata["authored_inputs"])
        doc["cases"][0]["authored_atlas_window"]["existing_native_alpha_registration"]["operation"] = "replace-alpha"
        path.write_text(json.dumps(doc), encoding="utf-8")
        item["authored_view"]["sha256"] = sha(path)
        with self.assertRaisesRegex(batch.ReviewError, "AUTHORED_ALPHA_"):
            batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)

    def test_bounded_reader_never_requests_more_than_limit_plus_one(self) -> None:
        class RecordingReader(io.BytesIO):
            def __init__(self, content: bytes):
                super().__init__(content)
                self.requests = []

            def read(self, size: int = -1) -> bytes:
                self.requests.append(size)
                return super().read(size)

        over_limit = RecordingReader(b"12345")
        with mock.patch.object(Path, "open", return_value=over_limit), \
             mock.patch.object(Path, "stat", side_effect=AssertionError("stat-then-unbounded-read is forbidden")):
            with self.assertRaisesRegex(batch.ReviewError, "INPUT_SIZE_LIMIT"):
                batch.read_bounded(Path("unused.bin"), 4, "bounded fixture")
        self.assertEqual(over_limit.requests, [5])

        at_limit = RecordingReader(b"1234")
        with mock.patch.object(Path, "open", return_value=at_limit):
            self.assertEqual(batch.read_bounded(Path("unused.bin"), 4, "bounded fixture"), b"1234")
        self.assertEqual(at_limit.requests, [5])

    def test_build_receipt_source_paths_normalize_strictly_and_keep_required_hashes(self) -> None:
        product = self.root / "build-receipt-product"
        required = ("src/native_review.hpp", "src/resample.hpp", "src/wic.hpp", "src/main.cpp")
        entries = []
        for relative in required:
            source = product / Path(relative)
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes((relative + " fixture").encode("utf-8"))
            entries.append({"path": relative.replace("/", "\\"), "sha256": sha(source)})
        executable_sha = "a" * 64
        receipt_path = product / "build" / "candidate" / "build-receipt.json"
        receipt_path.parent.mkdir(parents=True)

        def write_receipt(rows: list[dict], *, source_current: bool = True) -> None:
            receipt_path.write_text(json.dumps({
                "schema": "h3.tier-export.build.v2", "passed": True,
                "source_current": source_current, "executable": {"sha256": executable_sha},
                "sources": rows,
            }), encoding="utf-8")

        with mock.patch.object(batch, "PRODUCT", product):
            write_receipt(entries)
            result = batch._read_build_receipt(executable_sha)
            self.assertEqual([row["path"] for row in result["kernel_sources"]], list(required))
            self.assertEqual([row["sha256"] for row in result["kernel_sources"]],
                             [entry["sha256"] for entry in entries])

            duplicate = entries + [{**entries[0], "path": entries[0]["path"].replace("\\", "/")}]
            write_receipt(duplicate)
            with self.assertRaisesRegex(batch.ReviewError, "BUILD_SOURCE_PATH_DUPLICATE"):
                batch._read_build_receipt(executable_sha)

            traversal = [dict(entry) for entry in entries]
            traversal[0]["path"] = r"src\..\native_review.hpp"
            write_receipt(traversal)
            with self.assertRaisesRegex(batch.ReviewError, "BUILD_SOURCE_PATH_INVALID"):
                batch._read_build_receipt(executable_sha)

            write_receipt(entries[1:])
            with self.assertRaisesRegex(batch.ReviewError, "BUILD_SOURCE_ROW_MISSING"):
                batch._read_build_receipt(executable_sha)

            write_receipt(entries, source_current=False)
            with self.assertRaisesRegex(batch.ReviewError, "BUILD_RECEIPT_NOT_CURRENT"):
                batch._read_build_receipt(executable_sha)

    def test_formal_release_source_closure_survives_candidate_rebuild(self) -> None:
        product = self.root / "formal-stable-product"
        required = ("src/native_review.hpp", "src/resample.hpp", "src/wic.hpp", "src/main.cpp",
                    "scripts/review_native_batch.py", "scripts/workspace.py")
        entries = []
        for relative in required:
            current=product/relative
            current.parent.mkdir(parents=True,exist_ok=True)
            current.write_bytes((relative+" published").encode("utf-8"))
            entries.append({"path":relative,"sha256":sha(current)})
        formal=product/"dist"/"H3TierExport.exe"
        formal.parent.mkdir(parents=True,exist_ok=True)
        formal.write_bytes(b"formal executable frozen at release")
        formal_sha=sha(formal)
        receipt={"schema":"h3.tier-export.build.v2","passed":True,"source_current":True,
                 "executable":{"sha256":formal_sha},"sources":entries}
        frozen=product/"dist"/"H3TierExport-build-receipt.json"
        frozen.write_text(json.dumps(receipt)+"\n",encoding="utf-8")
        build_path=product/"build"/"candidate"/"build-receipt.json"
        build_path.parent.mkdir(parents=True,exist_ok=True)
        candidate={**receipt,"executable":{"sha256":"b"*64}}
        build_path.write_text(json.dumps(candidate)+"\n",encoding="utf-8")
        tests_path=product/"dist"/"H3TierExport-tests-receipt.json"
        tests_path.write_text(json.dumps({"schema":"h3.tier-export.test-suite.v2","passed":True,
            "source_current":True,"exe_sha256":formal_sha,"build_receipt_sha256":sha(frozen),
            "results":[{"name":"synthetic-fixture-only","exit_code":0}]})+"\n",encoding="utf-8")
        tests_raw=tests_path.read_bytes()
        release={"schema":"h3.tier-export.release.v2","exe_sha256":formal_sha,
                 "build_receipt_sha256":sha(frozen),"test_receipt_sha256":sha(tests_path),"files":[
                  {"path":str(formal),"sha256":formal_sha},
                  {"path":str(frozen),"sha256":sha(frozen)},
                  {"path":str(tests_path),"sha256":sha(tests_path)}]}
        release_raw=(json.dumps(release,ensure_ascii=False)+"\n").encode("utf-8")
        release_path=product/"dist"/"H3TierExport-release.json"
        release_path.write_bytes(release_raw)
        mirror=product/"reports"/"current"/"release.json"
        mirror.parent.mkdir(parents=True,exist_ok=True)
        mirror.write_bytes(release_raw)
        with mock.patch.object(batch,"PRODUCT",product):
            result=batch._read_build_receipt(formal_sha)
            self.assertEqual(result["authority"],"formal_release")
            self.assertEqual(result["sha256"],sha(frozen))
            self.assertEqual(len(result["kernel_sources"]),4)
            # The released executable remains usable when a new, unrelated candidate builds.
            candidate["executable"]["sha256"]="c"*64
            build_path.write_text(json.dumps(candidate)+"\n",encoding="utf-8")
            self.assertEqual(batch._read_build_receipt(formal_sha)["sha256"],sha(frozen))
            tests_path.write_bytes(tests_raw+b" ")
            with self.assertRaisesRegex(batch.ReviewError,"RELEASE_FROZEN_TEST_RECEIPT_DRIFT"):
                batch._read_build_receipt(formal_sha)
            tests_path.write_bytes(tests_raw)
            frozen.write_bytes(frozen.read_bytes()+b" ")
            with self.assertRaisesRegex(batch.ReviewError,"RELEASE_FROZEN_BUILD_RECEIPT_DRIFT"):
                batch._read_build_receipt(formal_sha)
            frozen.write_text(json.dumps(receipt)+"\n",encoding="utf-8")
            script=product/"scripts"/"review_native_batch.py"
            before=script.read_bytes()
            script.write_bytes(before+b"\n# unqualified edit")
            with self.assertRaisesRegex(batch.ReviewError,"RELEASE_COMPANION_SOURCE_DRIFT"):
                batch._read_build_receipt(formal_sha)
            script.write_bytes(before)
            mirror.write_bytes(release_raw+b" ")
            with self.assertRaisesRegex(batch.ReviewError,"RELEASE_REPORT_COPY_MISMATCH"):
                batch._read_build_receipt(formal_sha)
    def test_source_view_conservation_and_full_canvas_contracts(self) -> None:
        self._write_manifest(with_reference=False)
        manifest=json.loads(self.manifest_path.read_bytes())
        rows,_=batch.load_inventory(self.inventory_path,self.inventory_sha)
        item=manifest["items"][0]
        item.update(source_rect=[1,1,4,3],registration="integer-transparent-padding")
        with self.assertRaisesRegex(batch.ReviewError,"POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT"):
            batch._resolve_entry(item,rows,self.inventory_path,self.inventory_sha)
        item["registration"]="full-viewport-axis-normalization"
        with self.assertRaisesRegex(batch.ReviewError,"FULL_VIEWPORT_REQUIRES_WHOLE_SOURCE"):
            batch._resolve_entry(item,rows,self.inventory_path,self.inventory_sha)
        write_png(self.source,(12,12),31)
        item.update(source_sha256=sha(self.source),source_rect=None,source_layout="native-canvas",
                    registration="strict-aspect",render_scales=[1])
        spec,meta=batch._resolve_entry(item,rows,self.inventory_path,self.inventory_sha)
        self.assertEqual(spec["targets"],{"canvas_1x":[4,4],"crop_1x":[2,2],"origin_1x":[1,1]})
        self.assertEqual((spec["source_layout"],spec["render_scales"],meta["common_k"]),
                         ("native-canvas",[1],3))
        self.assertTrue(meta["source_view_proof"]["all_positive_preserved"])
        item["render_scales"]=[True]
        with self.assertRaisesRegex(batch.ReviewError,"RENDER_SCALES"):
            batch._resolve_entry(item,rows,self.inventory_path,self.inventory_sha)

    def test_publisher_freezes_tests_and_archives_the_previous_qualification(self) -> None:
        import publish as publisher
        product=self.root/"publication"
        build=product/"build/candidate";build.mkdir(parents=True)
        tests=product/"build/test-results/tests.json";tests.parent.mkdir(parents=True)
        qualified=json.loads((PRODUCT/"build/candidate/build-receipt.json").read_bytes())
        runtime_binding=qualified["companion_runtime"]
        runtime_root=product/runtime_binding["directory"]
        shutil.copytree(PRODUCT/runtime_binding["directory"],runtime_root)
        for name in ("README.md","third_party/LICENSE.MIT","resources/H3TierExport.ico",
                     "resources/H3TierExport-icon.png","resources/H3TierExport-workspace.json"):
            p=product/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode("ascii"))
        manual=product/"docs"/"H3TierExport-用户手册.md";manual.parent.mkdir(parents=True,exist_ok=True)
        manual.write_text("TEST publication manual",encoding="utf-8")
        exe=build/"H3TierExport.exe"
        def prepare(payload):
            exe.write_bytes(payload)
            receipt={"schema":"h3.tier-export.build.v2","passed":True,"source_current":True,
                     "executable":{"sha256":sha(exe),"bytes":exe.stat().st_size},
                     "sources":qualified["sources"],"companion_runtime":runtime_binding}
            (build/"build-receipt.json").write_text(json.dumps(receipt),encoding="utf-8")
            tests.write_text(json.dumps({"schema":"h3.tier-export.test-suite.v2","passed":True,"source_current":True,
                "exe_sha256":sha(exe),"build_receipt_sha256":sha(build/"build-receipt.json"),
                "results":[{"name":"synthetic-publication-fixture-only","exit_code":0}]}),encoding="utf-8")
        def run_fixture(executable,command,*args):
            if command=="info":return {"version":"fixture","gpu":{"description":"fixture"},"defaults":{}}
            if command=="ui-smoke":return {"passed":True,"icon_resource":True,"controls":1}
            return {"passed":True,"checks":1}
        with contextlib.ExitStack() as stack:
            for name,value in (("PRODUCT",product),("BUILD",build),("TESTS",tests),
                               ("DIST",product/"dist"),("REPORTS",product/"reports/current")):
                stack.enter_context(mock.patch.object(publisher,name,value))
            stack.enter_context(mock.patch.object(publisher,"validate_build_sources"))
            stack.enter_context(mock.patch.object(publisher,"run",side_effect=run_fixture))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            prepare(b"synthetic publisher exe A")
            tests_a=tests.read_bytes()
            self.assertEqual(publisher.main(),0)
            frozen=product/"dist/H3TierExport-tests-receipt.json"
            release_a=json.loads((product/"dist/H3TierExport-release.json").read_bytes())
            self.assertEqual(frozen.read_bytes(),tests_a)
            self.assertEqual(release_a["test_receipt_sha256"],sha(frozen))
            self.assertEqual(release_a["companion_runtime"],runtime_binding)
            tests.write_bytes(tests_a+b" ")
            self.assertEqual(frozen.read_bytes(),tests_a,"next qualification must not rewrite the release proof")
            current_before={p.name:p.read_bytes() for p in (product/"dist").iterdir() if p.is_file()}
            self.assertEqual(publisher.main(["--archive-current","--expected-release-sha256",
                                            sha(product/"dist/H3TierExport-release.json")]),0)
            stable_archive=product/"dist/archive"/("release-"+sha(product/"dist/H3TierExport-release.json"))
            self.assertEqual({p.name:p.read_bytes() for p in stable_archive.iterdir()},current_before)
            self.assertEqual({p.name:p.read_bytes() for p in (product/"dist").iterdir() if p.is_file()},current_before)
            prepare(b"synthetic publisher exe B")
            self.assertEqual(publisher.main(),0)
            release_b=json.loads((product/"dist/H3TierExport-release.json").read_bytes())
            archived=Path(release_b["archived_previous"])
            self.assertEqual(archived,stable_archive,"normal publication must reuse the archive-only locator")
            self.assertEqual(len(list((product/"dist/archive").iterdir())),1)
            self.assertEqual((archived/"H3TierExport-tests-receipt.json").read_bytes(),tests_a)
            self.assertEqual(json.loads((archived/"H3TierExport-release.json").read_bytes()),release_a)
            self.assertEqual(release_b["test_receipt_sha256"],sha(frozen))
            self.assertEqual(frozen.read_bytes(),tests.read_bytes())
            published={p.name:p.read_bytes() for p in (product/"dist").glob("H3TierExport*.json")}
            runtime_script=runtime_root/"workspace.py"
            runtime_script.write_bytes(runtime_script.read_bytes()+b"\n# TEST payload drift\n")
            prepare(b"synthetic publisher exe C must not publish")
            with self.assertRaisesRegex(RuntimeError,"RUNTIME_FILE_DRIFT"):
                publisher.main()
            self.assertEqual((product/"dist/H3TierExport.exe").read_bytes(),b"synthetic publisher exe B")
            self.assertEqual({p.name:p.read_bytes() for p in (product/"dist").glob("H3TierExport*.json")},published)

    @unittest.skipUnless(os.name == "nt", "Windows directory share-lock contract")
    def test_windows_output_root_handle_blocks_directory_rename(self) -> None:
        OUTPUT_BASE = batch.OUTPUT_BASE
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        root = self._new_output("held-root")
        moved = root.with_name(root.name + "-moved")
        guard = batch.OutputRootGuard.create(root, OUTPUT_BASE)
        try:
            with self.assertRaises(OSError):
                root.rename(moved)
            guard.verify_current()
            self.assertTrue(root.is_dir())
            self.assertFalse(moved.exists())
        finally:
            guard.close()
            if moved.exists() and not root.exists():
                moved.rename(root)
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows read-only output identity contract")
    def test_existing_output_verification_holds_root_and_subdirectory(self) -> None:
        base = batch.OUTPUT_BASE
        base.mkdir(parents=True, exist_ok=True)
        root = self._new_output("existing-read-guard")
        child = root / "frames"
        child.mkdir(parents=True)
        record = child / "receipt.json"
        record.write_bytes(b"{}")
        guard = batch.OutputRootGuard.open_existing(root, base)
        try:
            self.assertEqual(guard.verify_target(record, must_exist=True), record)
            for directory in (root, child):
                with self.assertRaises(OSError):
                    directory.rename(directory.with_name(directory.name + "-moved"))
            guard.verify_current()
        finally:
            guard.close()
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows ancestor directory share-lock contract")
    def test_windows_output_base_handle_blocks_ancestor_rename(self) -> None:
        OUTPUT_BASE = batch.OUTPUT_BASE
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        base = OUTPUT_BASE / f"contract-ancestor-{uuid.uuid4().hex}"
        root = base / "run"
        moved = base.with_name(base.name + "-moved")
        guard = batch.OutputRootGuard.create(root, base)
        try:
            with self.assertRaises(OSError):
                base.rename(moved)
            guard.verify_current()
            self.assertTrue(root.is_dir())
            self.assertFalse(moved.exists())
        finally:
            guard.close()
            if moved.exists() and not base.exists():
                moved.rename(base)
            shutil.rmtree(base, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows no-follow directory identity contract")
    def test_reparse_subdirectory_is_rejected_before_external_write(self) -> None:
        OUTPUT_BASE = batch.OUTPUT_BASE
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        root = self._new_output("reparse-root")
        external = batch.OUTPUT_BASE / f"contract-external-{uuid.uuid4().hex}"
        external.mkdir()
        guard = batch.OutputRootGuard.create(root, OUTPUT_BASE)
        link = root / "redirect"
        try:
            try:
                os.symlink(external, link, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlink creation is unavailable: {error}")
            with self.assertRaisesRegex(batch.ReviewError, "REPARSE_POINT_REJECTED"):
                guard.verify_target(link / "review-report.json")
            self.assertFalse((external / "review-report.json").exists())
        finally:
            guard.close()
            if link.is_symlink():
                link.unlink()
            shutil.rmtree(external, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows output directory identity contract")
    def test_relocated_root_identity_failure_never_writes_to_other_root(self) -> None:
        OUTPUT_BASE = batch.OUTPUT_BASE
        OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
        root = self._new_output("identity-root")
        other = self._new_output("identity-other")
        other.mkdir()
        guard = batch.OutputRootGuard.create(root, OUTPUT_BASE)
        real_open = batch._open_windows_directory

        def report_other_identity(path: Path):
            handle, identity = real_open(path)
            if batch._path_key(path) == batch._path_key(root):
                other_handle, other_identity = real_open(other)
                batch._windows_directory_api()[0].CloseHandle(other_handle)
                return handle, other_identity
            return handle, identity

        try:
            with mock.patch.object(batch, "_open_windows_directory", side_effect=report_other_identity):
                with self.assertRaisesRegex(batch.ReviewError, "OUTPUT_DIRECTORY_IDENTITY_CHANGED"):
                    batch._write_new(root / "review-report.json", b"must not be written", guard)
            self.assertFalse((root / "review-report.json").exists())
            self.assertFalse((other / "review-report.json").exists())
        finally:
            guard.close()

    def _fake_render(self, args, **kwargs):
        self.assertIs(kwargs.get("shell"), False, "native renderer must be spawned without a shell")
        self.assertEqual(kwargs.get("timeout"), batch.RENDER_TIMEOUT_SECONDS)
        self.assertEqual(args[0], str(self.renderer.resolve()))
        self.assertEqual(args[1], "review-render")
        self.assertNotIn("--renderer", args)
        spec_path = Path(args[args.index("--spec") + 1])
        spec_sha = args[args.index("--spec-sha256") + 1]
        native_outdir = Path(args[args.index("--outdir") + 1])
        self.assertEqual(sha(spec_path), spec_sha)
        spec_value = json.loads(spec_path.read_bytes())
        self.assertEqual(len(spec_value["items"]), 1)
        bound = spec_value["items"][0]
        self.assertTrue(Path(bound["source_png"]).is_file())
        self.assertEqual(sha(Path(bound["source_png"])), bound["source_sha256"])
        self.assertEqual(bound["targets"], {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]})
        self.assertEqual(args, [str(self.renderer.resolve()), "review-render", "--spec", str(spec_path),
                                "--spec-sha256", spec_sha, "--outdir", str(native_outdir)])

        item_root = native_outdir / bound["id"]
        item_root.mkdir(parents=True)
        reg = bound["registration"]
        outputs = {}
        if 1 in bound["render_scales"]:
            candidate_1x = item_root / "candidate-1x.png"
            write_png(candidate_1x, (4, 4), 13)
            outputs["candidate_1x"] = {"path": str(candidate_1x), "pixels": [4, 4],
                                     "png_sha256": sha(candidate_1x), "crop_pixels": [2, 2], "origin_pixels": [1, 1]}
        if 3 in bound["render_scales"]:
            candidate_3x = item_root / "candidate-3x.png"
            crop_output = bound.get("output_layout") == "native-crop"
            pixels = [6, 6] if crop_output else [12, 12]
            write_png(candidate_3x, tuple(pixels), 17)
            outputs["candidate_3x"] = {"path": str(candidate_3x), "pixels": pixels,
                                       "png_sha256": sha(candidate_3x), "crop_pixels": [6, 6], "origin_pixels": [3, 3]}
            if crop_output:
                outputs["candidate_3x"]["layout"] = "native-crop"
                if bound["source_layout"] == "native-canvas":
                    with Image.open(candidate_3x) as image:
                        histogram = image.convert("RGBA").getchannel("A").histogram()
                    outputs["candidate_3x"]["canvas_crop_proof"] = {
                        "operation": "single-native-canvas-sample-then-exact-rgba8-crop",
                        "sampled_canvas_pixels": [12, 12], "crop_rect_3x": [3, 3, 6, 6],
                        "positive_alpha_pixels_before_crop": sum(histogram[1:]),
                        "positive_alpha_pixels_after_crop": sum(histogram[1:]),
                        "alpha1_pixels_before_crop": histogram[1], "alpha1_pixels_after_crop": histogram[1],
                        "excluded_positive_alpha_pixels": 0, "sampling_calls": 1, "intermediate_png_files": 0,
                    }
        if reg == "integer-transparent-padding" and 1 in bound["render_scales"]:
            diag = item_root / "registration-padding-diagnostic.png"
            write_png(diag, (6, 6), 19)
            outputs["registration_padding_diagnostic"] = {
                "path": str(diag), "pixels": [6, 6], "png_sha256": sha(diag),
                "operation": "diagnostic preview only", "source_encoding_preserved": False, "source_png_modified": False
            }
        expected_reference_provenance = "caller-bound comparison" if bound["reference"] else None
        if bound["reference"] and 1 in bound["render_scales"]:
            reference_1x = item_root / "reference-1x.png"
            with Image.open(bound["reference"]["path"]) as reference:
                reference.convert("RGBA").save(reference_1x, format="PNG")
            outputs["reference_1x"] = {"path": str(reference_1x), "pixels": [4, 4],
                                       "png_sha256": sha(reference_1x),
                                       "source_sha256": bound["reference"]["sha256"],
                                       "layout": "native-canvas",
                                       "operation": "native 1X pixels copied/placed; no resampling"}
            expected_reference_provenance = "caller-bound comparison"
        pipeline = ("whole native canvas -> H3Quality single sampling -> exact RGBA8 native crop; zero positive alpha excluded"
                    if bound["source_layout"] == "native-canvas" and bound.get("output_layout") == "native-crop"
                    else "whole native canvas -> H3Quality direct sampling; native crop origin is not applied again"
                    if bound["source_layout"] == "native-canvas"
                    else "source crop -> alpha-zero integer canvas pixel-copy -> H3Quality direct sampling from the same registered input for each tier"
                    if reg == "integer-transparent-padding"
                    else "source crop -> full viewport axis normalization -> H3Quality direct sampling for 1X review"
                    if reg == "full-viewport-axis-normalization"
                    else "whole source -> full viewport axis normalization -> H3Quality direct sampling for 3X review"
                    if reg == "full-viewport-axis-normalization-3x"
                    else "source crop -> H3Quality direct sampling from the same source input for each tier")
        rendered_tiers = bound["render_scales"]
        with Image.open(bound["source_png"]) as image:
            alpha = image.convert("RGBA").getchannel("A")
            total = sum(alpha.histogram()[1:])
            x, y, w, h = bound["source_rect"] or [0, 0, image.width, image.height]
            retained = sum(alpha.crop((x, y, x + w, y + h)).histogram()[1:])
        native_receipt = {
            "schema": "h3.native-review.render-receipt.v1", "status": "REVIEW_REQUIRED",
            "spec": {"path": str(spec_path), "sha256": spec_sha},
            "official_inventory": spec_value["official_inventory"],
            "official_geometry_verified_by_renderer": False,
            "items": [{"id": bound["id"], "key": bound["key"], "targets": bound["targets"],
                       "geometry_binding_sha256": bound["geometry_binding_sha256"],
                       "source_sha256": bound["source_sha256"], "reference_provenance": expected_reference_provenance,
                       "source_layout": bound["source_layout"],
                       "source_view_proof": {"source_positive_pixels": total, "retained_positive_pixels": retained,
                                             "excluded_positive_pixels": total - retained, "all_positive_preserved": total == retained},
                       "render_input_pipeline": pipeline, "rendered_tiers": rendered_tiers, "outputs": outputs}]
        }
        if "output_layout" in bound:
            native_receipt["items"][0]["output_layout"] = bound["output_layout"]
        if "padding_registration" in bound:
            native_receipt["items"][0]["padding_registration"] = bound["padding_registration"]
            native_receipt["items"][0]["registration_offset"] = bound["padding_registration"]["offset"]
            common_k = bound["padding_registration"]["common_k"]
            sampling_base = bound["targets"]["canvas_1x" if bound["source_layout"] == "native-canvas" else "crop_1x"]
            native_receipt["items"][0]["common_k"] = common_k
            native_receipt["items"][0]["registered_pixels"] = [n * common_k for n in sampling_base]
        receipt_path = native_outdir / "render-receipt.json"
        receipt_path.write_text(json.dumps(native_receipt), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, json.dumps(native_receipt), "")

    def _patched_batch(self, run_side_effect):
        return (
            mock.patch.object(batch, "h3_root", return_value=self.h3root),
            mock.patch.object(batch, "_read_build_receipt", return_value={"sha256": "build-receipt-fixture"}),
            mock.patch.object(batch, "_renderer_capabilities", return_value={"version": "fixture", "native_commands": [{"name": "review-render"}]}),
            mock.patch.object(batch.subprocess, "run", side_effect=run_side_effect),
        )

    def _enter_patches(self, stack: contextlib.ExitStack, run_side_effect):
        for patcher in self._patched_batch(run_side_effect):
            stack.enter_context(patcher)

    def test_end_to_end_dispatch_pins_sources_geometry_and_provenance(self) -> None:
        self._write_manifest(with_reference=True)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0]["render_scales"] = [1, 3]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_sha = sha(self.manifest_path)
        before = {path: sha(path) for path in (self.source, self.reference, self.inventory_path, self.manifest_path)}
        output = self._new_output("success")
        child_calls = []

        def run(args, **kwargs):
            child_calls.append((args, kwargs))
            return self._fake_render(args, **kwargs)

        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, run)
            result = batch.run_batch(self.manifest_path, manifest_sha, self.renderer, self.renderer_sha, output)

        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(child_calls), 1)
        report = json.loads(Path(result["report"]).read_bytes())
        item = report["items"][0]
        self.assertEqual(item["reference_provenance"], "caller-bound comparison")
        self.assertEqual(item["reference"]["sha256"], self.reference_sha)
        self.assertTrue(item["reference_comparison"]["all_metrics_are_diagnostic"])
        self.assertEqual(item["rendered_tiers"], [1, 3])
        self.assertEqual(report["execution_diagnostics"]["settlement"], {
            "input_items": 1, "verified_items": 1, "output_pngs": 3,
            "rendered_items_by_tier": {"1": 1, "3": 1}})
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertTrue(report["official_inventory"]["geometry_verified_by_companion"])
        self.assertFalse(report["authority"]["art_approved"])
        self.assertFalse(report["authority"]["effective_pixels_verified"])
        self.assertTrue((output / "index.html").is_file())
        html = (output / "index.html").read_text(encoding="utf-8")
        self.assertIn("alpha≥2", html)
        self.assertIn("白底合成 SHA256", html)
        self.assertIn("深底合成 SHA256", html)
        self.assertEqual({path: sha(path) for path in before}, before, "review must not mutate bound inputs")
        self.assertIs(child_calls[0][1]["shell"], False)

    def test_alpha_aware_metrics_preserve_attached_alpha1_and_emit_background_evidence(self) -> None:
        image = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        image.putpixel((0, 0), (255, 20, 240, 1))  # attached/detached alpha1 is diagnostic only
        image.putpixel((4, 4), (20, 180, 70, 2))  # alpha>=2 inside the native crop
        image.putpixel((1, 1), (180, 30, 10, 3))  # high alpha outside the crop
        image.putpixel((11, 5), (10, 80, 220, 200))  # high alpha on the canvas edge
        image.putpixel((5, 5), (30, 90, 160, 255))  # opaque sample inside the native crop
        stream = io.BytesIO()
        image.save(stream, format="PNG")

        metrics = batch._metrics(stream.getvalue(), 3,
                                 {"crop_1x": [2, 2], "origin_1x": [1, 1]})
        self.assertEqual(metrics["alpha1_pixels"], 1)
        self.assertEqual(metrics["alpha1_outside_native_crop_pixels"], 1)
        geometry = metrics["alpha_ge2_geometry"]
        self.assertEqual(geometry["pixels"], 4)
        self.assertTrue(geometry["touches_canvas_border"])
        self.assertEqual(geometry["edge_pixels"], 1)
        self.assertEqual(geometry["outside_native_crop_pixels"], 2)
        self.assertEqual(geometry["review_hint"], "ALPHA_GE2_EDGE_OR_EXTERNAL_REVIEW_REQUIRED")
        halo = metrics["low_alpha_halo"]
        self.assertEqual(halo["schema"], "h3.native-review.low-alpha-halo-diagnostic.v1")
        self.assertEqual(halo["low_alpha_pixels"], 3)
        self.assertEqual(halo["low_alpha_outside_alpha128_bbox_pixels"], 3)
        self.assertEqual(halo["dark_rgb_low_alpha_pixels"], 0)
        self.assertEqual(halo["review_hint"], "LOW_ALPHA_OUTSIDE_ALPHA128_REVIEW_REQUIRED")
        self.assertIn("diagnostic-only", halo["failure_semantics"])

        backgrounds = metrics["background_composite_evidence"]["backgrounds"]
        self.assertEqual(set(backgrounds), {"white", "dark", "grass"})
        self.assertTrue(metrics["background_composite_evidence"]["source_rgba_preserved"])
        for name, rgb in (("white", (255, 255, 255)), ("dark", (0, 0, 0)), ("grass", (74, 94, 62))):
            expected = Image.alpha_composite(Image.new("RGBA", image.size, (*rgb, 255)), image)
            self.assertEqual(backgrounds[name]["rgba_sha256"], sha_bytes(expected.tobytes()))

        # RGB content in an alpha1 pixel is never a hard failure by itself.
        faint = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        faint.putpixel((0, 0), (255, 0, 255, 1))
        faint_stream = io.BytesIO()
        faint.save(faint_stream, format="PNG")
        faint_metrics = batch._metrics(faint_stream.getvalue(), 3,
                                       {"crop_1x": [2, 2], "origin_1x": [1, 1]})
        self.assertEqual(faint_metrics["alpha_ge2_geometry"]["pixels"], 0)
        self.assertEqual(faint_metrics["alpha_ge2_geometry"]["review_hint"],
                         "NO_ALPHA_GE2_EDGE_OR_EXTERNAL_PIXELS")
        self.assertEqual(faint_metrics["low_alpha_halo"]["review_hint"],
                         "NO_ALPHA128_REFERENCE_FOR_LOW_ALPHA_HALO")

    def test_explicit_padding_binding_checks_domain_and_freezes_group_contract(self) -> None:
        write_png(self.source, (8, 6), 3)
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        item = {"id": "PAD", "key": self.key, "source_png": str(self.source),
                "source_sha256": sha(self.source), "registration": "integer-transparent-padding",
                "padding_registration": {"common_k": 5, "offset": [1, 3]},
                "output_layout": "native-crop"}
        spec, metadata = batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
        self.assertEqual(spec["padding_registration"], item["padding_registration"])
        self.assertEqual(metadata["common_k"], 5)
        self.assertTrue(metadata["source_view_proof"]["all_positive_preserved"])
        for k, offset in ((3, [0, 0]), (5, [3, 0]), (True, [0, 0])):
            bad = {**item, "padding_registration": {"common_k": k, "offset": offset}}
            with self.assertRaises(batch.ReviewError):
                batch._resolve_entry(bad, rows, self.inventory_path, self.inventory_sha)
        for second in ({**spec, "id": "B", "key": ["def", "REVIEWFIXTURE", 0, 1],
                        "padding_registration": {"common_k": 6, "offset": [1, 3]}},
                       {k: v for k, v in {**spec, "id": "B", "key": ["def", "REVIEWFIXTURE", 0, 1]}.items()
                        if k != "padding_registration"}):
            with self.assertRaisesRegex(batch.ReviewError, "PADDING_GROUP_CONTRACT_MISMATCH"):
                batch._validate_padding_groups([spec, second])

    def test_explicit_padding_readback_rejects_conflicting_kernel_domain(self) -> None:
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0].update(registration="integer-transparent-padding",
                                    padding_registration={"common_k": 5, "offset": [1, 2]},
                                    output_layout="native-crop")
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        for field, value in (("common_k", 4), ("registered_pixels", [8, 10]),
                             ("registration_offset", [2, 2])):
            with self.subTest(field=field):
                def conflicting_receipt(args, **kwargs):
                    result = self._fake_render(args, **kwargs)
                    receipt = json.loads(result.stdout)
                    receipt["items"][0][field] = value
                    receipt_path = Path(args[args.index("--outdir") + 1]) / "render-receipt.json"
                    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                    return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")

                output = self._new_output("conflicting-padding-" + field)
                with contextlib.ExitStack() as stack:
                    self._enter_patches(stack, conflicting_receipt)
                    with self.assertRaisesRegex(batch.ReviewError, "PADDING_REGISTRATION_MISMATCH"):
                        batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                        self.renderer_sha, output)
                self.assertFalse((output / "review-report.json").exists())

    def test_crop_dispatch_uses_one_png_and_virtual_origin_in_html(self) -> None:
        self._write_manifest(with_reference=True)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0]["output_layout"] = "native-crop"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self._new_output("crop-dispatch")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                     self.renderer_sha, output)
        report = json.loads(Path(result["report"]).read_bytes())
        row = report["items"][0]
        self.assertEqual(row["output_layout"], "native-crop")
        self.assertEqual(row["outputs"]["candidate_3x"]["pixels"], [6, 6])
        self.assertEqual(len(list(output.rglob("*.png"))), 1)
        page = (output / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="virtual-canvas" data-native-width="4" data-native-height="4"', page)
        self.assertIn('data-native-left="1" data-native-top="1"', page)
        self.assertIn('id="review-background"', page)
        for value in ("checker", "dark", "white", "grass"):
            self.assertIn(f'<option value="{value}">', page)
        self.assertIn('grass:"#4a5e3e"', page)
        self.assertIn('草地色合成 SHA256', page)
        self.assertEqual(row["outputs"]["candidate_3x"]["diagnostics"]["alpha_ge2_geometry"]["outside_native_crop_pixels"], 0)

    def test_crop_metrics_use_virtual_canvas_and_the_same_encoded_pixels(self) -> None:
        crop = Image.new("RGBA", (6, 6), (59, 101, 149, 128))
        crop.putpixel((0, 0), (17, 29, 43, 1))
        canvas = Image.new("RGBA", (12, 12))
        canvas.paste(crop, (3, 3))
        encode = lambda image: self._encode_metric_png(image)
        targets = {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]}
        virtual = batch._metrics(encode(crop), 3, targets, "native-crop")
        original = batch._metrics(encode(canvas), 3, targets)
        self.assertEqual(virtual, original)
        with self.assertRaisesRegex(batch.ReviewError, "CROP_OUTPUT_GEOMETRY_MISMATCH"):
            batch._metrics(encode(canvas), 3, targets, "native-crop")

    def test_canvas_crop_preflight_keeps_complete_panels_and_original_targets(self):
        write_png(self.source, (26, 13), 31)
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        proofs = []
        for x in (0, 13):
            item = {"id": "PANEL", "key": self.key, "source_png": str(self.source),
                    "source_sha256": sha(self.source), "source_rect": [x, 0, 13, 13],
                    "source_layout": "native-canvas", "output_layout": "native-crop",
                    "registration": "strict-aspect", "render_scales": [3]}
            spec, meta = batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha)
            self.assertEqual(spec["targets"], {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]})
            self.assertEqual(spec["source_rect"], [x, 0, 13, 13])
            self.assertEqual(spec["output_layout"], "native-crop")
            self.assertFalse(meta["source_view_proof"]["all_positive_preserved"])
            proofs.append(meta["source_view_proof"])
        self.assertEqual(sum(p["retained_positive_pixels"] for p in proofs), proofs[0]["source_positive_pixels"])
        with self.assertRaisesRegex(batch.ReviewError, "CROP_OUTPUT_REQUIRES_NATIVE_CROP_3X_ONLY"):
            batch._resolve_entry({**item, "render_scales": [1, 3]}, rows, self.inventory_path, self.inventory_sha)
        with self.assertRaisesRegex(batch.ReviewError, "CANVAS_CROP_OUTPUT_REQUIRES_STRICT_ASPECT_3X_ONLY"):
            batch._resolve_entry({**item, "source_rect": None, "registration": "full-viewport-axis-normalization-3x"},
                                 rows, self.inventory_path, self.inventory_sha)

    def _write_canvas_crop_manifest(self):
        write_png(self.source, (13, 13), 31)
        self._write_manifest(with_reference=False, source_sha=sha(self.source))
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0].update(source_layout="native-canvas", output_layout="native-crop",
                                     registration="strict-aspect", render_scales=[3])
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_canvas_crop_dispatch_requires_native_proof_and_writes_one_crop_png(self):
        self._write_canvas_crop_manifest()
        output = self._new_output("canvas-crop-dispatch")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                     self.renderer_sha, output)
        report = json.loads(Path(result["report"]).read_bytes())
        row = report["items"][0]
        product = row["outputs"]["candidate_3x"]
        self.assertEqual(product["pixels"], [6, 6])
        self.assertEqual(product["canvas_crop_proof"]["crop_rect_3x"], [3, 3, 6, 6])
        self.assertEqual(product["canvas_crop_proof"]["alpha1_pixels_after_crop"], product["diagnostics"]["alpha1_pixels"])
        self.assertEqual(product["canvas_crop_proof"]["positive_alpha_pixels_after_crop"],
                         product["diagnostics"]["alpha_envelope_by_threshold"]["1"]["visible_pixels"])
        self.assertEqual(len(list(output.rglob("*.png"))), 1)
        self.assertEqual(row["targets"], {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]})

    def test_canvas_crop_readback_refuses_missing_forged_and_wrongly_typed_proofs(self):
        self._write_canvas_crop_manifest()
        for field, value in (("missing", None), ("excluded_positive_alpha_pixels", 1),
                             ("sampling_calls", 2), ("sampling_calls", True),
                             ("alpha1_pixels_before_crop", -1), ("positive_alpha_pixels_before_crop", -1),
                             ("sampled_canvas_pixels", [12.0, 12]), ("crop_rect_3x", [0, 0, 6, 6]),
                             ("extra", 0), ("wrong-owner", None)):
            with self.subTest(field=field, value=value):
                if field == "wrong-owner":
                    manifest = json.loads(self.manifest_path.read_bytes())
                    manifest["items"][0]["source_layout"] = "native-crop"
                    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                def bad_proof(args, **kwargs):
                    result = self._fake_render(args, **kwargs)
                    receipt = json.loads(result.stdout)
                    product = receipt["items"][0]["outputs"]["candidate_3x"]
                    if field == "missing":
                        product.pop("canvas_crop_proof")
                    elif field == "wrong-owner":
                        product["canvas_crop_proof"] = {"invented": True}
                    else:
                        product["canvas_crop_proof"][field] = value
                    receipt_path = Path(args[args.index("--outdir") + 1]) / "render-receipt.json"
                    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                    return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")
                output = self._new_output("bad-canvas-crop-proof")
                with contextlib.ExitStack() as stack:
                    self._enter_patches(stack, bad_proof)
                    with self.assertRaisesRegex(batch.ReviewError, "NATIVE_RENDERER_CANVAS_CROP_PROOF_MISMATCH"):
                        batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                         self.renderer_sha, output)
                self.assertFalse((output / "review-report.json").exists())

    @staticmethod
    def _encode_metric_png(image):
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    def _visual_reference_fixture(self, layout="native-crop"):
        path = self.root / "官方 3X & reference.png"
        crop = Image.new("RGBA", (6, 6), (31, 53, 97, 128))
        crop.putpixel((0, 0), (11, 19, 29, 1))
        image = crop
        if layout == "native-canvas":
            image = Image.new("RGBA", (12, 12))
            image.paste(crop, (3, 3))
        image.save(path, format="PNG")
        return {"role": "visual-only", "scale": 3, "layout": layout,
                "path": str(path), "sha256": sha(path)}

    def test_visual_reference_keeps_native_spec_pixels_metrics_and_settlement(self):
        for legacy, scales, layout in ((False, [3], "native-crop"),
                                        (False, [3], "native-canvas"),
                                        (True, [1, 3], "native-crop")):
            with self.subTest(legacy=legacy, scales=scales, layout=layout):
                self._write_manifest(with_reference=legacy)
                manifest = json.loads(self.manifest_path.read_bytes())
                manifest["items"][0]["render_scales"] = scales
                if scales == [3]:
                    manifest["items"][0]["output_layout"] = "native-crop"
                visual = self._visual_reference_fixture(layout)
                before = {path: sha(path) for path in (self.source, self.reference, Path(visual["path"]))}
                reports, specs = [], []
                child = mock.Mock(side_effect=self._fake_render)
                for include in (False, True):
                    if include:
                        manifest["items"][0]["visual_reference"] = visual
                    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    output = self._new_output("visual-reference")
                    with contextlib.ExitStack() as stack:
                        self._enter_patches(stack, child)
                        result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                                 self.renderer_sha, output)
                    reports.append(json.loads(Path(result["report"]).read_bytes()))
                    specs.append(json.loads((output / "render-spec.json").read_bytes())["items"])
                    expected_outputs = 3 if legacy else 1
                    self.assertEqual(len(list(output.rglob("*.png"))), expected_outputs)
                    self.assertEqual(reports[-1]["execution_diagnostics"]["settlement"]["output_pngs"], expected_outputs)
                    if include:
                        record = reports[-1]["items"][0]["visual_reference"]
                        self.assertEqual({key: record[key] for key in visual}, visual)
                        self.assertEqual(record["pixels"], [6, 6] if layout == "native-crop" else [12, 12])
                        for key, value in {"canvas_1x": [4, 4], "crop_1x": [2, 2], "origin_1x": [1, 1]}.items():
                            self.assertEqual(record[key], value)
                        page = (output / "index.html").read_text(encoding="utf-8")
                        uri = Path(visual["path"]).as_uri()
                        self.assertIn(f'<a href="{uri}">', page)
                        self.assertIn(f'<img src="{uri}"', page)
                        self.assertIn('data-role="visual-only"', page)
                self.assertEqual(child.call_count, 2, "display reference must not add renderer calls")
                self.assertEqual(specs[0], specs[1])
                self.assertNotIn("visual_reference", specs[1][0])
                self.assertEqual(reports[0]["execution_diagnostics"]["settlement"],
                                 reports[1]["execution_diagnostics"]["settlement"])
                first, second = (report["items"][0] for report in reports)
                self.assertEqual(first["geometry_binding_sha256"], second["geometry_binding_sha256"])
                self.assertEqual(first["reference_comparison"], second["reference_comparison"])
                self.assertEqual(first["reference_provenance"], second["reference_provenance"])
                self.assertEqual(bool(second["reference_comparison"]), legacy)
                self.assertNotIn("visual_reference", second["outputs"])
                self.assertEqual(set(first["outputs"]), set(second["outputs"]))
                for key in first["outputs"]:
                    self.assertEqual(first["outputs"][key]["png_sha256"], second["outputs"][key]["png_sha256"])
                    self.assertEqual(first["outputs"][key]["diagnostics"], second["outputs"][key]["diagnostics"])
                self.assertEqual({path: sha(path) for path in before}, before)

    def test_visual_reference_strict_schema_and_png_fail_before_dispatch(self):
        self._write_manifest(with_reference=False)
        manifest = json.loads(self.manifest_path.read_bytes())
        visual = self._visual_reference_fixture()
        cases = [(f"missing-{key}", {k: v for k, v in visual.items() if k != key}, "SCHEMA_REQUIRED_FIELDS_MISSING")
                 for key in visual]
        cases += [("unknown", {**visual, "origin_1x": [1, 1]}, "SCHEMA_UNKNOWN_FIELDS"),
                  ("null", None, "SCHEMA_OBJECT_REQUIRED"),
                  ("role", {**visual, "role": "native-comparison"}, "VISUAL_REFERENCE_ROLE_OR_SCALE_INVALID")]
        cases += [(f"scale-{value}", {**visual, "scale": value}, "VISUAL_REFERENCE_ROLE_OR_SCALE_INVALID")
                  for value in (True, False, 1, 2, 3.0, "3")]
        cases += [("layout", {**visual, "layout": "infer"}, "VISUAL_REFERENCE_LAYOUT_INVALID"),
                  ("layout-array", {**visual, "layout": []}, "VISUAL_REFERENCE_LAYOUT_INVALID"),
                  ("wrong-geometry", {**visual, "layout": "native-canvas"}, "VISUAL_REFERENCE_GEOMETRY_MISMATCH"),
                  ("invalid-sha", {**visual, "sha256": "G" * 64}, "INVALID_SHA256"),
                  ("sha-drift", {**visual, "sha256": "0" * 64}, "SOURCE_SHA256_MISMATCH"),
                  ("relative-path", {**visual, "path": "reference.png"}, "ABSOLUTE_PATH_REQUIRED")]
        non_png = self.root / "not-png.png"
        Image.new("RGB", (6, 6), (1, 3, 7)).save(non_png, format="JPEG")
        wrong_extension = self.root / "wrong-extension.jpg"
        wrong_extension.write_bytes(Path(visual["path"]).read_bytes())
        animated = self.root / "animated.png"
        Image.new("RGBA", (6, 6), (1, 3, 7, 255)).save(
            animated, format="PNG", save_all=True,
            append_images=[Image.new("RGBA", (6, 6), (11, 13, 17, 128))], duration=10, loop=0)
        for label, path, error in (("non-png", non_png, "STATIC_PNG_REQUIRED"),
                                   ("extension", wrong_extension, "VISUAL_REFERENCE_PNG_EXTENSION_REQUIRED"),
                                   ("animated", animated, "STATIC_PNG_REQUIRED")):
            cases.append((label, {**visual, "path": str(path), "sha256": sha(path)}, error))
        child = mock.Mock(side_effect=self._fake_render)
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, child)
            for label, value, error in cases:
                with self.subTest(case=label):
                    manifest["items"][0]["visual_reference"] = value
                    self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    output = self._new_output("visual-invalid")
                    with self.assertRaisesRegex(batch.ReviewError, error):
                        batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                        self.renderer_sha, output)
                    self.assertFalse(output.exists())
        child.assert_not_called()

    def test_visual_reference_reuses_overlap_guard_and_keeps_legacy_geometry_rejection(self):
        self._write_manifest(with_reference=True)
        manifest = json.loads(self.manifest_path.read_bytes())
        visual = self._visual_reference_fixture()
        manifest["items"][0]["visual_reference"] = visual
        manifest["items"][0]["reference"] = {"path": visual["path"], "sha256": visual["sha256"],
                                              "layout": "native-canvas"}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        child = mock.Mock(side_effect=self._fake_render)
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, child)
            output = self._new_output("legacy-three-reference-rejected")
            with self.assertRaisesRegex(batch.ReviewError, "REFERENCE_GEOMETRY_MISMATCH"):
                batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
            self.assertFalse(output.exists())
            del manifest["items"][0]["reference"]
            self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            original_overlap = batch._overlap
            with mock.patch.object(batch, "_overlap", side_effect=lambda path, root:
                                   path == Path(visual["path"]) or original_overlap(path, root)) as overlap:
                output = self._new_output("visual-overlap")
                with self.assertRaisesRegex(batch.ReviewError, "INPUT_OUTPUT_OVERLAP"):
                    batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                self.assertTrue(any(call.args[0] == Path(visual["path"]) for call in overlap.call_args_list))
                self.assertFalse(output.exists())
        child.assert_not_called()

    def test_visual_reference_hash_drift_at_each_boundary_refuses_success_without_retry(self):
        for phase in ("before render", "after render", "after page"):
            with self.subTest(phase=phase):
                self._write_manifest(with_reference=False)
                visual = self._visual_reference_fixture()
                manifest = json.loads(self.manifest_path.read_bytes())
                manifest["items"][0]["visual_reference"] = visual
                self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                output = self._new_output("visual-drift")
                original = Path(visual["path"]).read_bytes()
                real_verify = batch._verify_visual_references
                checks = []

                def mutate_at_boundary(items, current_phase):
                    checks.append(current_phase)
                    if current_phase == phase:
                        Path(visual["path"]).write_bytes(original + b" changed at visual boundary")
                    return real_verify(items, current_phase)

                child = mock.Mock(side_effect=self._fake_render)
                stderr = io.StringIO()
                try:
                    with contextlib.ExitStack() as stack:
                        self._enter_patches(stack, child)
                        stack.enter_context(mock.patch.object(batch, "_verify_visual_references", side_effect=mutate_at_boundary))
                        with contextlib.redirect_stderr(stderr):
                            code = batch.main(["--manifest", str(self.manifest_path), "--manifest-sha256", sha(self.manifest_path),
                                               "--renderer", str(self.renderer), "--renderer-sha256", self.renderer_sha,
                                               "--outdir", str(output)])
                    failure = json.loads(stderr.getvalue())
                    self.assertEqual(code, 2)
                    self.assertIn("SOURCE_SHA256_MISMATCH", failure["error"])
                    self.assertIn(phase, failure["error"])
                    self.assertEqual(failure["partial_output_root"], str(output))
                    self.assertEqual(failure["partial_output_disposition"], "inspect before removal; no cleanup is claimed")
                    self.assertEqual(child.call_count, 0 if phase == "before render" else 1)
                    self.assertEqual(checks[-1], phase)
                    self.assertEqual((output / "review-report.json").exists(), phase == "after page")
                    self.assertEqual((output / "index.html").exists(), phase == "after page")
                finally:
                    Path(visual["path"]).write_bytes(original)

    @unittest.skipUnless(os.name == "nt", "Windows reparse input contract")
    def test_visual_reference_reparse_is_rechecked_at_all_success_boundaries(self):
        import _winapi
        for phase in ("preflight", "before render", "after render", "after page"):
            with self.subTest(phase=phase):
                self._write_manifest(with_reference=False)
                original_visual = self._visual_reference_fixture()
                directory = self.root / ("visual-directory-" + phase.replace(" ", "-"))
                directory.mkdir()
                visual_path = directory / "official.png"
                visual_path.write_bytes(Path(original_visual["path"]).read_bytes())
                visual = {**original_visual, "path": str(visual_path)}
                saved = directory.with_name(directory.name + "-saved")
                manifest = json.loads(self.manifest_path.read_bytes())
                manifest["items"][0]["visual_reference"] = visual
                self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                real_verify = batch._verify_visual_references

                def replace_with_junction():
                    directory.rename(saved)
                    _winapi.CreateJunction(str(saved), str(directory))

                def mutate_at_boundary(items, current_phase):
                    if current_phase == phase:
                        replace_with_junction()
                    return real_verify(items, current_phase)

                child = mock.Mock(side_effect=self._fake_render)
                output = self._new_output("visual-reparse")
                try:
                    if phase == "preflight":
                        replace_with_junction()
                    with contextlib.ExitStack() as stack:
                        self._enter_patches(stack, child)
                        stack.enter_context(mock.patch.object(batch, "_verify_visual_references", side_effect=mutate_at_boundary))
                        with self.assertRaisesRegex(batch.ReviewError, "REPARSE_POINT_REJECTED"):
                            batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                            self.renderer_sha, output)
                    self.assertEqual(child.call_count, 0 if phase in ("preflight", "before render") else 1)
                    self.assertEqual(output.exists(), phase != "preflight")
                    self.assertEqual(sha(saved / "official.png"), visual["sha256"])
                finally:
                    if saved.exists():
                        os.rmdir(directory)
                        saved.rename(directory)

    @unittest.skipUnless(shutil.which("node"), "Node is used only for the original HTML event oracle")
    def test_visual_reference_crop_canvas_share_the_original_display_and_background_events(self):
        class PageNodes(HTMLParser):
            def __init__(self):
                super().__init__()
                self.nodes, self.figures = [], []

            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                if tag == "figure":
                    self.figures.append({"style": {}, "role": attributes.get("data-role")})
                if "data-native-width" in attributes:
                    dataset = {}
                    for key, value in attributes.items():
                        if key.startswith("data-"):
                            words = key[5:].split("-")
                            dataset[words[0] + "".join(word.title() for word in words[1:])] = value
                    self.nodes.append({"tagName": tag.upper(), "dataset": dataset, "style": {}})

        node_oracle = r"""
const vm=require('node:vm'), fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const scale={value:'1'}, background={value:'checker'};
const document={getElementById:id=>id==='review-display-scale'?scale:background,
 querySelectorAll:selector=>selector==='[data-native-width]'?input.nodes:input.figures};
vm.runInNewContext(input.script,{document});
const snapshots=[];
for(const value of ['1','3']) for(const color of ['checker','dark','white','grass']){
 scale.value=value;scale.onchange();background.value=color;background.onchange();
 snapshots.push(JSON.parse(JSON.stringify({scale:Number(value),color,nodes:input.nodes,figures:input.figures})));
}
process.stdout.write(JSON.stringify(snapshots));
"""
        for layout in ("native-crop", "native-canvas"):
            with self.subTest(layout=layout):
                self._write_manifest(with_reference=False)
                visual = self._visual_reference_fixture(layout)
                manifest = json.loads(self.manifest_path.read_bytes())
                manifest["items"][0].update(output_layout="native-crop", visual_reference=visual)
                self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                output = self._new_output("visual-html-events")
                with contextlib.ExitStack() as stack:
                    self._enter_patches(stack, self._fake_render)
                    batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                page = (output / "index.html").read_text(encoding="utf-8")
                parser = PageNodes()
                parser.feed(page)
                payload = {"nodes": parser.nodes, "figures": parser.figures,
                           "script": page.split("<script>", 1)[1].split("</script>", 1)[0]}
                oracle = subprocess.run([shutil.which("node"), "-e", node_oracle], input=json.dumps(payload),
                                        capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
                snapshots = json.loads(oracle.stdout)
                self.assertEqual(len(snapshots), 8)
                self.assertEqual(len(parser.figures), 2)
                self.assertEqual(sum(figure["role"] == "visual-only" for figure in parser.figures), 1)
                for state in snapshots:
                    scale = state["scale"]
                    visual_image = state["nodes"][1] if layout == "native-crop" else state["nodes"][0]
                    self.assertEqual(visual_image["width"], (2 if layout == "native-crop" else 4) * scale)
                    self.assertEqual(visual_image["height"], (2 if layout == "native-crop" else 4) * scale)
                    if layout == "native-crop":
                        self.assertEqual(visual_image["style"]["left"], f"{scale}px")
                        self.assertEqual(visual_image["style"]["top"], f"{scale}px")
                        self.assertEqual(state["nodes"][0]["style"]["width"], f"{4 * scale}px")
                    else:
                        self.assertNotIn("nativeLeft", visual_image["dataset"])
                        self.assertNotIn("left", visual_image["style"])
                    expected_background = {"checker": "repeating-conic-gradient(#536170 0% 25%,#3d4a58 0% 50%) 0/16px 16px",
                                           "dark": "#000", "white": "#fff", "grass": "#4a5e3e"}[state["color"]]
                    self.assertTrue(all(figure["style"]["background"] == expected_background for figure in state["figures"]))
                self.assertEqual(len(list(output.rglob("*.png"))), 1)

    def test_default_three_only_matches_explicit_without_copying_one_x_reference(self) -> None:
        hashes = []
        for scales in (None, [3]):
            self._write_manifest(with_reference=True)
            manifest = json.loads(self.manifest_path.read_bytes())
            if scales is not None:
                manifest["items"][0]["render_scales"] = scales
            self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = self._new_output("default-three-only")
            with contextlib.ExitStack() as stack:
                self._enter_patches(stack, self._fake_render)
                result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                         self.renderer_sha, output)
            report = json.loads(Path(result["report"]).read_bytes())
            row = report["items"][0]
            self.assertEqual(set(row["outputs"]), {"candidate_3x"})
            self.assertEqual(row["rendered_tiers"], [3])
            self.assertEqual(len(list(output.rglob("*.png"))), 1)
            hashes.append(row["outputs"]["candidate_3x"]["png_sha256"])
        self.assertEqual(hashes[0], hashes[1])

    def test_default_three_only_rejects_under3x_before_renderer_or_output(self) -> None:
        write_png(self.source, (4, 4), 3)
        self._write_manifest(with_reference=True, source_sha=sha(self.source))
        output = self._new_output("default-three-under-capacity")
        child = mock.Mock(side_effect=self._fake_render)
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, child)
            with self.assertRaisesRegex(batch.ReviewError, "REQUESTED_3X_UNAVAILABLE"):
                batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                self.renderer_sha, output)
        child.assert_not_called()
        self.assertFalse(output.exists())

    def test_explicit_three_only_does_not_copy_one_x_reference(self) -> None:
        self._write_manifest(with_reference=True)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0]["render_scales"] = [3]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                     self.renderer_sha, self._new_output("three-only"))
        report = json.loads(Path(result["report"]).read_bytes())
        self.assertEqual(set(report["items"][0]["outputs"]), {"candidate_3x"})
        self.assertEqual(report["items"][0]["rendered_tiers"], [3])

    def test_execution_diagnostics_include_child_work_without_changing_spec_or_pixels(self) -> None:
        self._write_manifest(with_reference=False)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0]["render_scales"] = [3]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        expected_spec, _ = batch._resolve_entry(manifest["items"][0], rows, self.inventory_path, self.inventory_sha)
        output = self._new_output("execution-diagnostics")
        observed = {}

        def delayed_render(args, **kwargs):
            child_start = time.perf_counter()
            time.sleep(0.02)
            result = self._fake_render(args, **kwargs)
            observed["child_seconds"] = time.perf_counter() - child_start
            observed["png_sha256"] = sha(output / "rendered/REVIEW_FIXTURE/candidate-3x.png")
            return result

        outer_start = time.perf_counter()
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, delayed_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
        outer_seconds = time.perf_counter() - outer_start
        report = json.loads(Path(result["report"]).read_bytes())
        diagnostics = report["execution_diagnostics"]
        phases = diagnostics["phase_wall_seconds"]
        self.assertTrue(all(value >= 0 for value in phases.values()))
        self.assertGreaterEqual(phases["native_renderer"], observed["child_seconds"])
        self.assertAlmostEqual(sum(phases.values()), diagnostics["elapsed_through_verified_readback_seconds"], places=8)
        self.assertLessEqual(diagnostics["elapsed_through_verified_readback_seconds"], outer_seconds)
        self.assertIn("excludes outer CLI startup and final report/HTML writes", diagnostics["measurement_scope"])
        self.assertIn("not isolated sampling time", diagnostics["native_renderer_scope"])
        self.assertEqual(diagnostics["settlement"], {"input_items": 1, "verified_items": 1,
                         "output_pngs": 1, "rendered_items_by_tier": {"1": 0, "3": 1}})
        self.assertEqual(json.loads((output / "render-spec.json").read_bytes())["items"], [expected_spec])
        self.assertEqual(sha(Path(report["items"][0]["outputs"]["candidate_3x"]["path"])), observed["png_sha256"])
        self.assertFalse(report["authority"]["art_approved"])

    def test_parser_disables_abbreviated_options_and_requires_renderer_binding(self) -> None:
        parser = batch.build_parser()
        with self.assertRaises(SystemExit) as caught:
            parser.parse_args(["--man", "manifest.json"])
        self.assertEqual(caught.exception.code, 2)
        with self.assertRaises(SystemExit) as caught:
            parser.parse_args(["--manifest", "manifest.json", "--manifest-sha256", "a" * 64,
                               "--outdir", str(self._new_output("missing-renderer"))])
        self.assertEqual(caught.exception.code, 2)

    def test_source_reference_and_output_collisions_fail_before_child_execution(self) -> None:
        broken_sha = self._write_manifest(with_reference=True, source_sha="0" * 64)
        output = self._new_output("bad-source")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, mock.Mock())
            with self.assertRaisesRegex(batch.ReviewError, "SOURCE_SHA256_MISMATCH"):
                batch.run_batch(self.manifest_path, broken_sha, self.renderer, self.renderer_sha, output)
        self.assertFalse(output.exists(), "input preflight failure must happen before creating the output root")

        write_png(self.reference, (3, 4), 9)
        self.reference_sha = sha(self.reference)
        bad_reference_sha = self._write_manifest(with_reference=True)
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, mock.Mock())
            with self.assertRaisesRegex(batch.ReviewError, "REFERENCE_GEOMETRY_MISMATCH"):
                batch.run_batch(self.manifest_path, bad_reference_sha, self.renderer, self.renderer_sha,
                                self._new_output("bad-reference"))

        collided = self._new_output("existing")
        collided.mkdir()
        marker = collided / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, mock.Mock())
            with self.assertRaisesRegex(batch.ReviewError, "OUTPUT_ROOT_ALREADY_EXISTS"):
                batch.run_batch(self.manifest_path, self._write_manifest(with_reference=False),
                                self.renderer, self.renderer_sha, collided)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(batch._overlap(collided / "source.png", collided), "nested inputs must overlap destination")

        outside = self.root / "outside-output"
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, mock.Mock())
            with self.assertRaisesRegex(batch.ReviewError, "OUTPUT_ROOT_MUST_BE_A_NEW_CHILD"):
                batch.run_batch(self.manifest_path, self._write_manifest(with_reference=False),
                                self.renderer, self.renderer_sha, outside)
        self.assertFalse(outside.exists())

    def test_source_sha_drift_after_renderer_is_reported_as_partial_without_retry(self) -> None:
        manifest_sha = self._write_manifest(with_reference=False)
        original = self.source.read_bytes()
        output = self._new_output("source-drift")
        calls = []

        def render_then_mutate(args, **kwargs):
            calls.append((args, kwargs))
            result = self._fake_render(args, **kwargs)
            self.source.write_bytes(original + b" changed during render")
            return result

        try:
            with contextlib.ExitStack() as stack:
                self._enter_patches(stack, render_then_mutate)
                with self.assertRaisesRegex(batch.ReviewError, "SOURCE_SHA256_MISMATCH"):
                    batch.run_batch(self.manifest_path, manifest_sha, self.renderer, self.renderer_sha, output)
            self.assertEqual(len(calls), 1, "source drift must not retry renderer work")
            self.assertTrue((output / "rendered" / "render-receipt.json").is_file())
            self.assertFalse((output / "review-report.json").exists())
        finally:
            self.source.write_bytes(original)

    def test_native_nonzero_exit_and_partial_root_are_reported_without_retry(self) -> None:
        manifest_sha = self._write_manifest(with_reference=False)
        output = self._new_output("failed-child")
        calls = []

        def fail(args, **kwargs):
            calls.append((args, kwargs))
            self.assertIs(kwargs.get("shell"), False)
            partial = Path(args[args.index("--outdir") + 1])
            partial.mkdir(parents=True)
            (partial / "partial-output.bin").write_bytes(b"partial evidence")
            return subprocess.CompletedProcess(args, 17, "", "fixture child failure")

        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, fail)
            with contextlib.redirect_stderr(stderr):
                code = batch.main(["--manifest", str(self.manifest_path), "--manifest-sha256", manifest_sha,
                                   "--renderer", str(self.renderer), "--renderer-sha256", self.renderer_sha,
                                   "--outdir", str(output)])
        failure = json.loads(stderr.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1, "failed child must not be retried")
        self.assertIn("NATIVE_RENDERER_FAILED(17)", failure["error"])
        self.assertEqual(Path(failure["partial_output_root"]), output)
        self.assertEqual(failure["partial_output_disposition"], "inspect before removal; no cleanup is claimed")
        self.assertTrue((output / "rendered" / "partial-output.bin").is_file())
        self.assertFalse((output / "review-report.json").exists())

    def test_integer_transparent_padding_aspect_mismatch_dispatch(self) -> None:
        source = self.root / "source_padding.png"
        write_png(source, (6, 8), 5)
        source_sha = sha(source)
        item = {"id": "REVIEW_PADDING", "key": self.key,
                "source_png": str(source), "source_sha256": source_sha,
                "registration": "integer-transparent-padding", "render_scales": [1, 3]}
        manifest = {"schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha},
                    "items": [item]}
        manifest_path = self.root / "manifest_padding.json"
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw)
        manifest_sha = sha_bytes(raw)

        output = self._new_output("padding-success")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(manifest_path, manifest_sha, self.renderer, self.renderer_sha, output)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        report = json.loads(Path(result["report"]).read_bytes())
        item_out = report["items"][0]["outputs"]
        self.assertIn("registration_padding_diagnostic", item_out)
        self.assertIn("candidate_1x", item_out)
        self.assertIn("candidate_3x", item_out)

    def test_full_viewport_3x_dispatch_accepts_sufficient_master_without_one_x(self) -> None:
        write_png(self.source, (13, 12), 23)
        self._write_manifest(with_reference=False, source_sha=sha(self.source))
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["items"][0].update(key=["pcx", "REVIEWFIXTURE"],
                                    registration="full-viewport-axis-normalization-3x", render_scales=[3])
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self._new_output("viewport-three-success")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                     self.renderer_sha, output)
        report = json.loads(Path(result["report"]).read_bytes())
        self.assertIn("candidate_3x", report["items"][0]["outputs"])
        self.assertNotIn("candidate_1x", report["items"][0]["outputs"])
        self.assertEqual(report["items"][0]["render_scales"], [3])

    def test_full_viewport_axis_normalization_dispatch(self) -> None:
        town_item = {
            "canonical_key": ["pcx", "REVIEWFIXTURE", None, None],
            "public_native_key": ["pcx", "REVIEWFIXTURE"],
            "source": {
                "path": str(self.source),
                "sha256": self.source_sha,
                "pixels": [6, 6]
            },
            "source_rect_xywh": [0, 0, 6, 6],
            "mapping": {"kind": "ROOT_EXPLICIT_FULL_VIEWPORT_AXIS_NORMALIZATION_FOR_1X_REVIEW"}
        }
        manifest = {
            "schema": "h3.art-town.background-native1x-diagnostic-decisions.v1",
            "items": [town_item]
        }
        manifest_path = self.root / "manifest_town.json"
        raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw)
        manifest_sha = sha_bytes(raw)

        output = self._new_output("town-success")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            result = batch.run_batch(manifest_path, manifest_sha, self.renderer, self.renderer_sha, output)
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        report = json.loads(Path(result["report"]).read_bytes())
        item_out = report["items"][0]["outputs"]
        self.assertIn("candidate_1x", item_out)
        self.assertNotIn("candidate_3x", item_out)
        self.assertEqual(report["items"][0]["registration"], "full-viewport-axis-normalization")


    def _native_layer_fixture(self):
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        rows[tuple(self.key)]["native_lod"] = "H3sprite.lod"
        inventory = json.loads(gzip.decompress(self.inventory_path.read_bytes()))
        for value in inventory["rows"]:
            if value["kind"] == "def":
                value["native_lod"] = "H3sprite.lod"
        self.inventory_path.write_bytes(gzip.compress(json.dumps(inventory).encode("utf-8")))
        self.inventory_sha = sha(self.inventory_path)
        normal = Image.new("RGBA", (4, 4))
        normal.putpixel((1, 1), (11, 37, 59, 255))
        shadow = Image.new("RGBA", (4, 4))
        shadow.putpixel((1, 2), (0, 0, 0, 64))
        shadow.putpixel((2, 2), (0, 0, 0, 128))
        overlay = Image.new("RGBA", (4, 4))
        overlay.putpixel((2, 1), (255, 255, 255, 255))
        layers = {"normal": normal, "shadow": shadow, "overlay": overlay,
                  "combined": Image.alpha_composite(normal, shadow)}
        indexed = bytes([224, 5, 1, 4])
        palette = bytearray(768)
        palette[224 * 3:224 * 3 + 3] = bytes([11, 37, 59])
        payload = bytes(16) + bytes(palette) + indexed
        proof = {"native_payload_sha256": sha_bytes(payload), "indexed_pixels_sha256": sha_bytes(indexed),
                 "palette_sha256": sha_bytes(palette)}
        base = self.h3root / "CompleteNativeBaseline/20261006-v1"
        paths = {"complete_sprite_lod": base / "H3sprite.lod",
                 "existing_adapter": self.h3root / "tools/native_h3_io.py",
                 "existing_decoder": self.h3root / "tools/homm3data/deffile.py",
                 "existing_lod_reader": self.h3root / "tools/homm3data/lodfile.py",
                 "existing_index_reader": self.h3root / "tools/native_h3_io.py",
                 "existing_def_parser": self.h3root / "tools/build_official_def_frame_map.py"}
        bindings = {}
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload if name == "complete_sprite_lod" else b"inert source fingerprint fixture")
            bindings[name] = {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}
        reference = self.h3root / "native-normal.png"
        normal.save(reference)
        geometry = {"canvas": [4, 4], "crop": [2, 2], "origin": [1, 1]}
        counts = {str(i): indexed.count(i) for i in range(8)}
        counts["224_255"] = 1
        resource = {"key": self.key, "native_geometry": geometry, "native_lod": "H3sprite.lod",
                    "native_member": "REVIEWFIXTURE.DEF", "native_frame_resource": "reviewfixture.pcx",
                    "native_member_payload_sha256": proof["native_payload_sha256"],
                    "indexed_pixels_sha256": proof["indexed_pixels_sha256"],
                    "palette_sha256": proof["palette_sha256"], "normal_rgba_sha256": sha_bytes(normal.tobytes()),
                    "normal_reference": {"path": str(reference), "sha256": sha(reference), "bytes": reference.stat().st_size},
                    "index_counts": counts}
        doc = {"schema": "h3.art-town.native-flag-shadow-era-source-readback.v1",
               "scope": {"source_or_art_or_runtime_completion_claimed": False},
               "source_bindings": bindings, "resources": [resource]}
        authority = self.h3root / "native-layer-readback.json"
        authority.write_text(json.dumps(doc), encoding="utf-8")
        item = {"id": "NATIVE_LAYERS", "key": self.key, "source_png": str(self.source),
                "source_sha256": self.source_sha, "render_scales": [3],
                "native_layer_evidence": {"path": str(authority), "sha256": sha(authority)}}

        @contextlib.contextmanager
        def mapped_lod(_):
            yield SimpleNamespace(entries=[{"name": "REVIEWFIXTURE.DEF"}])

        @contextlib.contextmanager
        def open_decoder(_):
            yield SimpleNamespace(read_image=lambda how, **kwargs: layers[how])

        reader = SimpleNamespace(BASE=base, native_image=lambda _: (normal.copy(), dict(proof)),
                                 mapped_lod=mapped_lod, payload=lambda _: payload,
                                 parse_def=lambda _: {"slots": [{"group_id": 0, "frame_index": 0}]},
                                 decode_native_pixels=lambda *_: indexed, deffile=SimpleNamespace(open=open_decoder))
        return item, rows, authority, doc, reader, layers

    def _native_layer_resolve(self, item, rows, reader, **kwargs):
        with mock.patch.object(batch, "h3_root", return_value=self.h3root), \
                mock.patch.object(batch, "_native_layer_reader", return_value=reader):
            return batch._resolve_entry(item, rows, self.inventory_path, self.inventory_sha, **kwargs)

    def test_native_layers_bind_reference_facts_without_changing_render_spec_or_approvals(self):
        item, rows, _, _, reader, _ = self._native_layer_fixture()
        spec, metadata = self._native_layer_resolve(item, rows, reader)
        plain = copy.deepcopy(item)
        del plain["native_layer_evidence"]
        old_spec, _ = batch._resolve_entry(plain, rows, self.inventory_path, self.inventory_sha)
        self.assertEqual(spec, old_spec)
        bound = metadata["native_reference_layers"]
        self.assertTrue(bound["native_reference_layers_verified"])
        self.assertEqual(bound["layers"]["normal"]["positive_alpha_pixels"], 1)
        self.assertEqual(bound["layers"]["shadow"]["alpha_levels"], [0, 64, 128])
        self.assertEqual(bound["layers"]["overlay"]["positive_alpha_pixels"], 1)
        self.assertEqual(bound["index_counts"]["224_255"], 1)
        self.assertEqual(bound["runtime_owner_color_mapping"], "NOT_ESTABLISHED")
        for name in ("generated_source_functional_layers_verified", "pixel_rendering_changed", "art_approved", "runtime_accepted", "game_deployed"):
            self.assertIs(bound[name], False)
        batch._verify_native_layer_inputs(metadata["native_layer_inputs"])

    def test_native_layers_reject_forged_payload_index_palette_or_native_rgba(self):
        item, rows, path, original, reader, _ = self._native_layer_fixture()
        for field in ("native_member_payload_sha256", "indexed_pixels_sha256", "palette_sha256", "normal_rgba_sha256"):
            with self.subTest(field=field):
                doc = copy.deepcopy(original)
                doc["resources"][0][field] = "f" * 64
                path.write_text(json.dumps(doc), encoding="utf-8")
                item["native_layer_evidence"]["sha256"] = sha(path)
                with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_PAYLOAD_INDEX_PALETTE_OR_RGBA_MISMATCH"):
                    self._native_layer_resolve(item, rows, reader)

    def test_native_layers_reject_wrong_key_geometry_resource_or_duplicate_key(self):
        item, rows, path, original, reader, _ = self._native_layer_fixture()
        changes = [lambda d: d["resources"][0].update(key=["def", "OTHER", 0, 0]),
                   lambda d: d["resources"][0]["native_geometry"].update(origin=[0, 0]),
                   lambda d: d["resources"][0].update(native_member="OTHER.DEF"),
                   lambda d: d["resources"].append(copy.deepcopy(d["resources"][0]))]
        for change in changes:
            doc = copy.deepcopy(original)
            change(doc)
            path.write_text(json.dumps(doc), encoding="utf-8")
            item["native_layer_evidence"]["sha256"] = sha(path)
            with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_KEY_NOT_UNIQUE|NATIVE_LAYER_SLOT_OR_GEOMETRY_MISMATCH"):
                self._native_layer_resolve(item, rows, reader)

    def test_native_layers_reject_missing_shadow_and_overlay_loss(self):
        item, rows, _, _, reader, layers = self._native_layer_fixture()
        shadow = layers["shadow"]
        layers["shadow"] = None
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_MISSING_OR_GEOMETRY_MISMATCH"):
            self._native_layer_resolve(item, rows, reader)
        layers["shadow"] = shadow
        layers["overlay"].putpixel((2, 1), (255, 255, 0, 0))
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_INDEX5_OVERLAY_MISMATCH"):
            self._native_layer_resolve(item, rows, reader)

    def test_native_layers_reject_source_drift_before_reader_or_output_creation(self):
        item, rows, _, doc, reader, _ = self._native_layer_fixture()
        lod = Path(doc["source_bindings"]["complete_sprite_lod"]["path"])
        lod.write_bytes(lod.read_bytes() + b"drift")
        out = self._new_output("native-layer-drift")
        manifest = {"schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha}, "items": [item]}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.object(batch, "h3_root", return_value=self.h3root), \
                mock.patch.object(batch, "_native_layer_reader") as load_reader, \
                mock.patch.object(batch.subprocess, "run") as child:
            with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_INPUT_DRIFT"):
                batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, out)
            load_reader.assert_not_called()
            child.assert_not_called()
        self.assertFalse(out.exists())

    def test_native_layers_refuse_unknown_authority_fields_and_noncanonical_reader(self):
        item, rows, _, doc, reader, _ = self._native_layer_fixture()
        item["native_layer_evidence"]["runtime_owner_color"] = [255, 255, 0]
        with self.assertRaisesRegex(batch.ReviewError, "SCHEMA_UNKNOWN_FIELDS"):
            self._native_layer_resolve(item, rows, reader)
        bindings = copy.deepcopy(doc["source_bindings"])
        bindings["existing_adapter"]["path"] = str(self.source)
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_READER_OR_CONTAINER_NOT_CANONICAL"):
            batch._native_layer_reader(bindings, self.h3root)

    def test_native_layers_keep_ordinary_palette_rgb_even_with_a_coherent_bad_normal_reference(self):
        item, rows, path, doc, reader, layers = self._native_layer_fixture()
        layers["normal"].putpixel((1, 1), (101, 3, 5, 255))
        layers["combined"] = Image.alpha_composite(layers["normal"], layers["shadow"])
        resource = doc["resources"][0]
        reference = Path(resource["normal_reference"]["path"])
        layers["normal"].save(reference)
        resource["normal_rgba_sha256"] = sha_bytes(layers["normal"].tobytes())
        resource["normal_reference"].update(sha256=sha(reference), bytes=reference.stat().st_size)
        path.write_text(json.dumps(doc), encoding="utf-8")
        item["native_layer_evidence"]["sha256"] = sha(path)
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_ORDINARY_PALETTE_RGB_OR_ALPHA_MISMATCH"):
            self._native_layer_resolve(item, rows, reader)

    def test_native_layers_translate_missing_native_slot_into_a_specific_reader_failure(self):
        item, rows, _, _, reader, _ = self._native_layer_fixture()
        def missing_slot(_):
            raise StopIteration("native slot missing")
        reader.native_image = missing_slot
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_NATIVE_READER_REJECTED: StopIteration"):
            self._native_layer_resolve(item, rows, reader)

    def test_native_layers_verify_late_drift_and_deduplicate_before_after_inputs(self):
        item, rows, _, _, reader, _ = self._native_layer_fixture()
        _, metadata = self._native_layer_resolve(item, rows, reader)
        refs = batch._native_layer_inputs([metadata, copy.deepcopy(metadata)])
        self.assertEqual(len(refs), len(metadata["native_layer_inputs"]))
        path = Path(refs[0]["path"])
        path.write_bytes(path.read_bytes() + b"late drift")
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_INPUT_DRIFT"):
            batch._verify_native_layer_inputs(refs)
        conflict = copy.deepcopy(metadata)
        conflict["native_layer_inputs"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_INPUT_CONFLICT"):
            batch._native_layer_inputs([metadata, conflict])

    def test_native_layers_survive_the_public_batch_report_without_new_pixel_routes(self):
        item, _, _, _, reader, _ = self._native_layer_fixture()
        manifest = {"schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha}, "items": [item]}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self._new_output("native-layers-report")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, self._fake_render)
            stack.enter_context(mock.patch.object(batch, "_native_layer_reader", return_value=reader))
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
        report = json.loads(Path(result["report"]).read_bytes())
        row = report["items"][0]
        self.assertEqual(row["rendered_tiers"], [3])
        self.assertEqual(set(row["outputs"]), {"candidate_3x"})
        self.assertTrue(row["native_reference_layers"]["native_reference_layers_verified"])
        self.assertEqual(set(row["native_reference_layers"]["layers"]), {"normal", "shadow", "overlay", "combined"})
        self.assertEqual(row["native_reference_layers"]["runtime_owner_color_mapping"], "NOT_ESTABLISHED")
        spec = json.loads((output / "render-spec.json").read_bytes())
        self.assertNotIn("native_reference_layers", spec["items"][0])
        self.assertNotIn("native_layer_evidence", spec["items"][0])
        self.assertFalse(report["authority"]["art_approved"])

    def test_native_layers_changed_during_renderer_leave_partial_without_success_or_retry(self):
        item, _, _, doc, reader, _ = self._native_layer_fixture()
        manifest = {"schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha}, "items": [item]}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self._new_output("native-layers-late-drift")
        lod = Path(doc["source_bindings"]["complete_sprite_lod"]["path"])
        calls = []
        def render_then_drift(args, **kwargs):
            calls.append(args)
            result = self._fake_render(args, **kwargs)
            lod.write_bytes(lod.read_bytes() + b"native source changed during renderer")
            return result
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, render_then_drift)
            stack.enter_context(mock.patch.object(batch, "_native_layer_reader", return_value=reader))
            with self.assertRaisesRegex(batch.ReviewError, "NATIVE_LAYER_INPUT_DRIFT"):
                batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
        self.assertEqual(len(calls), 1)
        self.assertTrue((output / "rendered/render-receipt.json").is_file())
        self.assertFalse((output / "review-report.json").exists())


    def _foreground_fixture(self):
        # Six hand-declared pixels lie in [3,6)x[1,3); the second ring overlaps
        # two of them. These explicit coordinates are the oracle, not a mask
        # implementation in Python.
        rgba = Image.new("RGBA", (12, 8), (19, 29, 39, 0))
        selected = [(3, 1), (4, 1), (5, 1), (3, 2), (4, 2), (5, 2)]
        for i, xy in enumerate(selected):
            rgba.putpixel(xy, (17 + i, 71 + i, 133 - i, 1 if i == 0 else 255))
        rgba.putpixel((0, 0), (7, 11, 13, 1))
        rgba.putpixel((10, 6), (23, 31, 47, 255))
        rgba.save(self.source)
        parent_sha = sha(self.source)
        parent_bytes = self.source.stat().st_size
        refs = {}
        for name, value in (("request", {"schema": "h3.art-town.generation-request.v1", "key": ["pcx", "BODY"]}),
                            ("art_standard", {"schema": "h3.art-town.canonical-art-direction.v1"}),
                            ("native_proof", {"schema": "h3.art-town.original-complete-main-keep-guard-default-and-MANC-contact.v1"})):
            path = self.root / ("foreground-" + name + ".json")
            path.write_text(json.dumps(value), encoding="utf-8")
            refs[name] = {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}
        history = {"path": str(self.root / "retired-never-opened-G.png"), "sha256": parent_sha,
                   "bytes": parent_bytes, "pixels": [12, 8], "whole_RGBA_sha256": sha_bytes(rgba.tobytes())}
        polygons = [{"id": "front", "closed": True, "points": [[3, 1], [6, 1], [6, 3], [3, 3], [3, 1]]},
                    {"id": "overlap", "closed": True, "points": [[4, 1], [6, 1], [6, 2], [4, 2], [4, 1]]}]
        vector = {"target_identity": "REVIEWFIXTURE", "actual_parent_only": history,
                  "actual_parent_request": refs["request"], "art_standard_evidence_only": refs["art_standard"],
                  "source_coordinates": {"pixels": [12, 8], "integer_vertices_are_pixel_corners": True,
                                         "no_source_crop_center_resize_warp": True},
                  "native_function": {"proof": refs["native_proof"], "resource": "REVIEWFIXTURE.PCX",
                                      "canvas": [4, 4], "crop": [2, 2], "origin": [1, 1], "native_XY": [720, 158]},
                  "reference_geometry_polygons": polygons}
        vector_path = self.root / "foreground-vector.json"
        vector_path.write_text(json.dumps(vector), encoding="utf-8")
        authority = {"schema": "h3.art-town.root-component-candidate-input-authority.v1",
                     "execution_authority": {
                         "frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation": True,
                         "formal_Source_intake_or_index_or_deployment_authorized_here": False},
                     "frozen_vector": {"path": str(vector_path), "sha256": sha(vector_path),
                                       "bytes": vector_path.stat().st_size, "polygons": 2, "polygon_ids": ["front", "overlap"]},
                     "parent": {"path": str(self.source), "sha256": parent_sha, "bytes": parent_bytes,
                                "pixels": [12, 8], "whole_RGBA_sha256": sha_bytes(rgba.tobytes()),
                                "same_exact_original_as_G": True},
                     "art_standard": refs["art_standard"],
                     "approved_shared_parent_geometry_for_candidate": {
                         "source_view_half_open": [3, 1, 9, 7], "source_rect_XYWH": [3, 1, 6, 6],
                         "K": 4, "pad_offset": [1, 2], "registered_source_pixels": [8, 8],
                         "native_canvas": [4, 4], "native_crop": [2, 2], "native_origin": [1, 1],
                         "native_world_XY": [720, 158], "render_scales": [3], "candidate_pixels": [6, 6],
                         "selected_bbox_fit_or_recenter_allowed": False}}
        authority_path = self.root / "foreground-authority.json"
        authority_path.write_text(json.dumps(authority), encoding="utf-8")
        wrapper = {"schema": "h3.native-review.town-foreground-selection.v1",
                   "authority": {"path": str(authority_path), "sha256": sha(authority_path)}}
        item = {"id": "TOWN_FRONT", "key": ["pcx", "REVIEWFIXTURE"],
                "source_png": str(self.source), "source_sha256": parent_sha,
                "source_rect": [3, 1, 6, 6], "source_layout": "native-crop", "output_layout": "native-crop",
                "registration": "integer-transparent-padding", "padding_registration": {"common_k": 4, "offset": [1, 2]},
                "render_scales": [3], "foreground_selection": wrapper}
        selected_rgba = Image.new("RGBA", rgba.size)
        for xy in selected:
            selected_rgba.putpixel(xy, rgba.getpixel(xy))
        proof = {"schema": "h3.native-review.town-foreground-selection-proof.v1",
                 "operation": "same-grid-positive-ring-union-copy-before-one-quality-sample",
                 "inputs": {"authority": wrapper["authority"], "vector": {"path": str(vector_path), "sha256": sha(vector_path)},
                            **{k: {"path": v["path"], "sha256": v["sha256"]} for k, v in refs.items()}},
                 "parent_custody": {"policy": "explicit-root-approved-stable-parent-rebind.v1",
                                    "stable_parent": {"path": str(self.source), "sha256": parent_sha,
                                                      "bytes": parent_bytes, "pixels": [12, 8]},
                                    "vector_parent_history": {k: history[k] for k in ("path", "sha256", "bytes", "pixels")},
                                    "historical_path_is_runtime_dependency": False},
                 "source_pixels": [12, 8], "source_rect_xywh": [3, 1, 6, 6],
                 "registered_pixels": [8, 8], "common_k": 4, "offset": [1, 2],
                 "polygons": 2, "edges": 8, "raster_work_bound": 32,
                 "source_rgba8_sha256": sha_bytes(rgba.tobytes()),
                 "selected_source_rgba8_sha256": sha_bytes(selected_rgba.tobytes()),
                 "parent_positive_pixels": 8, "selected_positive_pixels": 6, "excluded_positive_pixels": 2,
                 "parent_alpha1_pixels": 2, "selected_alpha1_pixels": 1, "excluded_alpha1_pixels": 1,
                 "selected_positive_outside_view": 0, "selected_alpha1_outside_view": 0,
                 "selected_linear_premultiplied_samples_bit_identical": True,
                 "sampling_calls": 1, "intermediate_png_files": 0, "Source_art_approved": False}
        rows, _ = batch.load_inventory(self.inventory_path, self.inventory_sha)
        fixture = {"item": item, "rows": rows, "authority": authority, "authority_path": authority_path,
                   "vector": vector, "vector_path": vector_path, "proof": proof}
        self._foreground_test_proof = proof
        return fixture

    def _rewrite_foreground_binding(self, fixture):
        vector_path, authority_path = fixture["vector_path"], fixture["authority_path"]
        vector_path.write_text(json.dumps(fixture["vector"]), encoding="utf-8")
        fixture["authority"]["frozen_vector"].update(sha256=sha(vector_path), bytes=vector_path.stat().st_size)
        authority_path.write_text(json.dumps(fixture["authority"]), encoding="utf-8")
        fixture["item"]["foreground_selection"]["authority"]["sha256"] = sha(authority_path)

    def _write_foreground_manifest(self, fixture):
        self.manifest_path.write_text(json.dumps({
            "schema": "h3.native-review.batch-manifest.v1",
            "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha},
            "items": [fixture["item"]]}), encoding="utf-8")

    def _foreground_fake_render(self, args, **kwargs):
        result = self._fake_render(args, **kwargs)
        receipt = json.loads(result.stdout)
        spec_path = Path(args[args.index("--spec") + 1])
        bound = json.loads(spec_path.read_bytes())["items"][0]
        row = receipt["items"][0]
        row.update(foreground_selection=bound["foreground_selection"],
                   foreground_selection_proof=copy.deepcopy(self._foreground_test_proof),
                   render_input_pipeline="Town authority-bound same-grid foreground OR copy -> original integer padding -> H3Quality single3X sampling")
        path = Path(args[args.index("--outdir") + 1]) / "render-receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")

    def _enter_foreground_patches(self, stack, side_effect):
        self._enter_patches(stack, side_effect)
        build = self.root / "foreground-frozen-build.json"
        build.write_text('{"synthetic_contract_build":true}', encoding="utf-8")
        stack.enter_context(mock.patch.object(batch, "_read_build_receipt",
                                             return_value={"path": str(build), "sha256": sha(build)}))
        return build

    def test_town_foreground_binds_stable_parent_and_keeps_whole_parent_proof_honest(self):
        fixture = self._foreground_fixture()
        item = fixture["item"]
        spec, row = batch._resolve_entry(item, fixture["rows"], self.inventory_path, self.inventory_sha)
        self.assertEqual(spec["foreground_selection"], item["foreground_selection"])
        self.assertEqual(spec["source_rect"], [3, 1, 6, 6])
        self.assertEqual(spec["padding_registration"], {"common_k": 4, "offset": [1, 2]})
        self.assertEqual(row["source_view_proof"], {"source_positive_pixels": 8, "retained_positive_pixels": 6,
                                                   "excluded_positive_pixels": 2, "all_positive_preserved": False})
        expected = row["_foreground_selection_expected_proof"]
        self.assertEqual(expected["parent_positive_pixels"], 8)
        self.assertEqual(expected["parent_alpha1_pixels"], 2)
        self.assertEqual((expected["polygons"], expected["edges"], expected["raster_work_bound"]), (2, 8, 32))
        self.assertNotIn("selected_source_rgba8_sha256", expected, "Python must not rasterize/reconstruct the selector")
        history = fixture["vector"]["actual_parent_only"]["path"]
        self.assertFalse(Path(history).exists())
        self.assertNotIn(history, [ref["path"] for ref in row["foreground_inputs"]])
        ordinary = {k: v for k, v in item.items() if k != "foreground_selection"}
        with self.assertRaisesRegex(batch.ReviewError, "POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT"):
            batch._resolve_entry(ordinary, fixture["rows"], self.inventory_path, self.inventory_sha)

    def test_town_foreground_dispatch_reads_exact_native_proof_and_one_3x_without_approval(self):
        fixture = self._foreground_fixture()
        self._write_foreground_manifest(fixture)
        output = self._new_output("town-foreground")
        before = sha(self.source)
        child = mock.Mock(side_effect=self._foreground_fake_render)
        with contextlib.ExitStack() as stack:
            self._enter_foreground_patches(stack, child)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
        report = json.loads(Path(result["report"]).read_bytes())
        row = report["items"][0]
        self.assertEqual(row["foreground_selection_proof"], fixture["proof"])
        self.assertNotIn("_foreground_selection_expected_proof", row)
        self.assertEqual(set(row["outputs"]), {"candidate_3x"})
        self.assertEqual(row["outputs"]["candidate_3x"]["pixels"], [6, 6])
        self.assertEqual(len(list(output.rglob("*.png"))), 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(sha(self.source), before)
        self.assertFalse(row["source_view_proof"]["all_positive_preserved"])
        self.assertTrue(all(value is False for value in report["authority"].values()))
        self.assertEqual(row["foreground_selection_proof"]["selected_alpha1_pixels"], 1)
        self.assertEqual(row["foreground_selection_proof"]["excluded_alpha1_pixels"], 1)

    def test_town_foreground_uses_root_geometry_standard_and_same_canonical_parent_for_35_rings(self):
        fixture = self._foreground_fixture()
        vector, authority = fixture["vector"], fixture["authority"]
        vector["actual_parent_only"]["path"] = str(self.source).replace("\\", "/")
        authority["parent"].pop("same_exact_original_as_G")
        vector.pop("art_standard_evidence_only")
        vector["native_function"] = {k: vector["native_function"][k] for k in ("resource", "proof")}
        vector["source_coordinates"] = {"domain": "original parent integer pixel-corner grid",
                                        "bounds_half_open": [0, 0, 12, 8]}
        polygons = [copy.deepcopy(vector["reference_geometry_polygons"][0]) for _ in range(35)]
        for i, polygon in enumerate(polygons):
            polygon["id"] = "ARC_" + str(i)
        vector["reference_geometry_polygons"] = polygons
        authority["frozen_vector"].update(polygons=35, polygon_ids=[p["id"] for p in polygons])
        self._rewrite_foreground_binding(fixture)
        proof = fixture["proof"]
        proof["inputs"]["vector"]["sha256"] = sha(fixture["vector_path"])
        proof["parent_custody"].update(policy="same-canonical-parent-path.v1", historical_path_is_runtime_dependency=True)
        proof["parent_custody"]["vector_parent_history"]["path"] = vector["actual_parent_only"]["path"]
        proof.update(polygons=35, edges=140, raster_work_bound=840)
        self._write_foreground_manifest(fixture)
        output = self._new_output("town-same-parent-35")
        with contextlib.ExitStack() as stack:
            self._enter_foreground_patches(stack, self._foreground_fake_render)
            result = batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
        actual = json.loads(Path(result["report"]).read_bytes())["items"][0]
        self.assertEqual(actual["foreground_selection_proof"], proof)
        self.assertEqual(len(actual["foreground_inputs"]), 6, "same parent does not create a second runtime input")

    def test_town_foreground_rejects_wrong_role_wrapper_and_source_geometry_before_output(self):
        edits = [("wrapper", lambda f: f["item"]["foreground_selection"].update(extra=True)),
                 ("authority-ref", lambda f: f["item"]["foreground_selection"]["authority"].update(bytes=1)),
                 ("def", lambda f: f["item"].update(key=self.key)),
                 ("ui", lambda f: f["item"].update(authored_view={})),
                 ("canvas", lambda f: f["item"].update(source_layout="native-canvas")),
                 ("output", lambda f: f["item"].update(output_layout="native-canvas")),
                 ("tier", lambda f: f["item"].update(render_scales=[1])),
                 ("float-tier", lambda f: f["item"].update(render_scales=[3.0])),
                 ("padding", lambda f: f["item"].pop("padding_registration")),
                 ("rect", lambda f: f["item"].update(source_rect=None)),
                 ("formal", lambda f: f["authority"]["execution_authority"].update(formal_Source_intake_or_index_or_deployment_authorized_here=True)),
                 ("not-approved", lambda f: f["authority"]["execution_authority"].update(frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation=False)),
                 ("custody", lambda f: f["authority"]["parent"].update(same_exact_original_as_G=False)),
                 ("history", lambda f: f["vector"]["actual_parent_only"].update(sha256="0" * 64)),
                 ("resource", lambda f: f["vector"]["native_function"].update(resource="OTHER.PCX")),
                 ("bool-origin", lambda f: f["authority"]["approved_shared_parent_geometry_for_candidate"].update(native_origin=[True, 1])),
                 ("world", lambda f: f["authority"]["approved_shared_parent_geometry_for_candidate"].update(native_world_XY=[True, 158])),
                 ("xyxy", lambda f: f["authority"]["approved_shared_parent_geometry_for_candidate"].update(source_view_half_open=[3, 1, 8, 7]))]
        for label, edit in edits:
            with self.subTest(label=label):
                fixture = self._foreground_fixture()
                edit(fixture)
                if label not in {"wrapper", "authority-ref"}:
                    self._rewrite_foreground_binding(fixture)
                self._write_foreground_manifest(fixture)
                output = self._new_output("town-invalid-role")
                child = mock.Mock(side_effect=self._foreground_fake_render)
                with contextlib.ExitStack() as stack:
                    self._enter_foreground_patches(stack, child)
                    with self.assertRaises(batch.ReviewError):
                        batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                self.assertFalse(output.exists())
                child.assert_not_called()

    def test_town_foreground_bounds_refs_rings_and_rejects_non_rgba8_without_rasterization(self):
        def resolve(fixture):
            return batch._resolve_entry(fixture["item"], fixture["rows"], self.inventory_path, self.inventory_sha)
        for label, edit in [
                ("bool", lambda f: f["vector"]["reference_geometry_polygons"][0]["points"][0].__setitem__(0, True)),
                ("float", lambda f: f["vector"]["reference_geometry_polygons"][0]["points"][0].__setitem__(0, 3.0)),
                ("outside", lambda f: f["vector"]["reference_geometry_polygons"][0]["points"][1].__setitem__(0, 13)),
                ("unclosed", lambda f: f["vector"]["reference_geometry_polygons"][0]["points"].__setitem__(-1, [4, 1])),
                ("too-many", lambda f: f["vector"].update(reference_geometry_polygons=f["vector"]["reference_geometry_polygons"] * 33)),
                ("vertices", lambda f: f["vector"]["reference_geometry_polygons"][0].update(points=[[3, 1], [6, 1]] * 129)),
                ("count", lambda f: f["authority"]["frozen_vector"].update(polygons=True)),
                ("ids", lambda f: f["authority"]["frozen_vector"].update(polygon_ids=["overlap", "front"])),
                ("bytes", lambda f: f["vector"]["actual_parent_request"].update(bytes=True))]:
            with self.subTest(label=label):
                fixture = self._foreground_fixture()
                edit(fixture)
                self._rewrite_foreground_binding(fixture)
                with self.assertRaises(batch.ReviewError):
                    resolve(fixture)
        fixture = self._foreground_fixture()
        Image.new("RGB", (12, 8), "red").save(self.source)
        fixture["item"]["source_sha256"] = sha(self.source)
        with self.assertRaisesRegex(batch.ReviewError, "RGBA8_PNG_REQUIRED"):
            resolve(fixture)
        fixture = self._foreground_fixture()
        request = Path(fixture["vector"]["actual_parent_request"]["path"])
        request.write_bytes(b" " * (8 * 1024 * 1024 + 1))
        fixture["vector"]["actual_parent_request"].update(sha256=sha(request), bytes=request.stat().st_size)
        self._rewrite_foreground_binding(fixture)
        with self.assertRaisesRegex(batch.ReviewError, "INPUT_SIZE_LIMIT"):
            resolve(fixture)

    def test_town_foreground_readback_refuses_missing_forged_or_wrongly_typed_proof(self):
        changes = [("missing", None), ("extra", 0), ("sampling_calls", True),
                   ("selected_positive_pixels", 6.0), ("selected_positive_pixels", 7),
                   ("selected_alpha1_pixels", 2), ("parent_alpha1_pixels", True),
                   ("selected_positive_outside_view", 1), ("selected_alpha1_outside_view", 1),
                   ("selected_linear_premultiplied_samples_bit_identical", 1),
                   ("source_rect_xywh", [3.0, 1, 6, 6]),
                   ("selected_source_rgba8_sha256", "wrong"), ("source_rgba8_sha256", "0" * 64),
                   ("wrong-ref", None), ("wrapper", None)]
        fixture = self._foreground_fixture()
        self._write_foreground_manifest(fixture)
        for field, value in changes:
            with self.subTest(field=field, value=value):
                def bad(args, **kwargs):
                    result = self._foreground_fake_render(args, **kwargs)
                    receipt = json.loads(result.stdout)
                    row = receipt["items"][0]
                    if field == "missing":
                        row.pop("foreground_selection_proof")
                    elif field == "wrapper":
                        row["foreground_selection"]["schema"] = "h3.map-ui.forged"
                    elif field == "wrong-ref":
                        row["foreground_selection_proof"]["inputs"]["request"]["sha256"] = "0" * 64
                    else:
                        row["foreground_selection_proof"][field] = value
                    path = Path(args[args.index("--outdir") + 1]) / "render-receipt.json"
                    path.write_text(json.dumps(receipt), encoding="utf-8")
                    return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")
                output = self._new_output("town-bad-proof")
                with contextlib.ExitStack() as stack:
                    self._enter_foreground_patches(stack, bad)
                    with self.assertRaisesRegex(batch.ReviewError, "NATIVE_RENDERER_FOREGROUND_PROOF_MISMATCH"):
                        batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                self.assertFalse((output / "review-report.json").exists())

    def test_town_foreground_proof_cannot_be_attached_to_a_legacy_item(self):
        self._write_manifest(with_reference=False)
        def wrong_role(args, **kwargs):
            result = self._fake_render(args, **kwargs)
            receipt = json.loads(result.stdout)
            receipt["items"][0]["foreground_selection_proof"] = {"invented": True}
            path = Path(args[args.index("--outdir") + 1]) / "render-receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, json.dumps(receipt), "")
        with contextlib.ExitStack() as stack:
            self._enter_patches(stack, wrong_role)
            with self.assertRaisesRegex(batch.ReviewError, "NATIVE_RENDERER_FOREGROUND_PROOF_WRONG_ROLE"):
                batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer,
                                self.renderer_sha, self._new_output("town-wrong-proof-role"))

    def test_town_foreground_currentness_reads_only_admitted_sizes_and_rejects_growing_json(self):
        fixture = self._foreground_fixture()
        _, row = batch._resolve_entry(fixture["item"], fixture["rows"], self.inventory_path, self.inventory_sha)
        original_read = batch.read_bounded
        observed = []
        def bounded(path, limit, label):
            observed.append((str(path), limit))
            return original_read(path, limit, label)
        with mock.patch.object(batch, "read_bounded", side_effect=bounded):
            batch._verify_foreground_inputs([row], "unit")
        self.assertEqual(observed, [(ref["path"], ref["bytes"]) for ref in row["foreground_inputs"]])
        request = Path(fixture["vector"]["actual_parent_request"]["path"])
        request.write_bytes(request.read_bytes() + b" larger")
        with mock.patch.object(batch, "read_bounded", side_effect=bounded):
            with self.assertRaisesRegex(batch.ReviewError, "SOURCE_SHA256_MISMATCH.*INPUT_SIZE_LIMIT"):
                batch._verify_foreground_inputs([row], "unit-growing")
        request_reads = [limit for path, limit in observed if path == str(request)]
        expected = next(ref["bytes"] for ref in row["foreground_inputs"] if ref["path"] == str(request))
        self.assertEqual(request_reads, [expected, expected])

    def test_town_foreground_batch_mask_budget_precedes_renderer_and_leaves_legacy_unlimited(self):
        class BudgetAccepted(Exception):
            pass
        # Mock only the already-bound rows, avoiding large images or any native
        # execution. Four 16Mi masks are exactly the batch boundary.
        for town_count, legacy_count, accepted in ((5, 0, False), (4, 0, True),
                                                   (4, 3, True), (0, 5, True)):
            with self.subTest(town=town_count, legacy=legacy_count):
                items = [{"id": "MASK_" + str(i), "key": ["pcx", "MASK_" + str(i)],
                          "source_png": str(self.source), "source_sha256": sha(self.source)}
                         for i in range(town_count + legacy_count)]
                for item in items[:town_count]:
                    item["foreground_selection"] = {
                        "schema": "h3.native-review.town-foreground-selection.v1",
                        "authority": {"path": str(self.root / "mock-already-bound-authority.json"), "sha256": "a" * 64}}
                self.manifest_path.write_text(json.dumps({
                    "schema": "h3.native-review.batch-manifest.v1",
                    "official_inventory": {"path": str(self.inventory_path), "sha256": self.inventory_sha},
                    "items": items}), encoding="utf-8")
                def already_bound(item, *args, **kwargs):
                    bound = {**item, "source_pixels": [4096, 4096], "reference": None,
                             "source_layout": "native-crop", "registration": "strict-aspect"}
                    return bound, dict(bound)
                output = self._new_output("town-mask-budget")
                child = mock.Mock(side_effect=AssertionError("budget test must never execute a renderer"))
                capabilities = mock.Mock(side_effect=BudgetAccepted)
                build = mock.Mock(return_value={"sha256": "mock-already-bound-build"})
                with mock.patch.object(batch, "h3_root", return_value=self.h3root), \
                     mock.patch.object(batch, "_resolve_entry", side_effect=already_bound) as resolver, \
                     mock.patch.object(batch, "_read_build_receipt", build), \
                     mock.patch.object(batch, "_renderer_capabilities", capabilities), \
                     mock.patch.object(batch.subprocess, "run", child):
                    if accepted:
                        with self.assertRaises(BudgetAccepted):
                            batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                        capabilities.assert_called_once()
                        build.assert_called_once()
                    else:
                        with self.assertRaisesRegex(batch.ReviewError, "TOWN_FOREGROUND_BATCH_MASK_PIXEL_LIMIT"):
                            batch.run_batch(self.manifest_path, sha(self.manifest_path), self.renderer, self.renderer_sha, output)
                        capabilities.assert_not_called()
                        build.assert_not_called()
                self.assertEqual(resolver.call_count, len(items))
                child.assert_not_called()
                self.assertFalse(output.exists())

    def test_town_foreground_current_refs_recheck_before_after_and_after_page_without_retry(self):
        for phase in ("before render", "after render", "after page"):
            for target in ("authority", "vector", "request", "art_standard", "native_proof", "parent", "manifest", "inventory", "build", "spec"):
                with self.subTest(phase=phase, target=target):
                    fixture = self._foreground_fixture()
                    self._write_foreground_manifest(fixture)
                    output = self._new_output("town-drift")
                    original_verify = batch._verify_foreground_inputs
                    original_hash_check = batch.file_sha_matches
                    originals = {}
                    child = mock.Mock(side_effect=self._foreground_fake_render)
                    stderr, stdout = io.StringIO(), io.StringIO()
                    with contextlib.ExitStack() as stack:
                        build = self._enter_foreground_patches(stack, child)
                        path = {"authority": fixture["authority_path"], "vector": fixture["vector_path"],
                                "parent": self.source, "manifest": self.manifest_path,
                                "inventory": self.inventory_path, "build": build}
                        path.update({name: Path(fixture["proof"]["inputs"][name]["path"])
                                     for name in ("request", "art_standard", "native_proof")})
                        def drift(rows, boundary):
                            if boundary == phase and target != "spec":
                                changed = path[target]
                                originals[changed] = changed.read_bytes()
                                changed.write_bytes(originals[changed] + b" late drift")
                            return original_verify(rows, boundary)
                        def drift_spec(value, expected_sha, label):
                            if target == "spec" and label == "Town spec " + phase:
                                originals[value] = value.read_bytes()
                                value.write_bytes(originals[value] + b" late drift")
                            return original_hash_check(value, expected_sha, label)
                        stack.enter_context(mock.patch.object(batch, "_verify_foreground_inputs", side_effect=drift))
                        stack.enter_context(mock.patch.object(batch, "file_sha_matches", side_effect=drift_spec))
                        try:
                            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                                code = batch.main(["--manifest", str(self.manifest_path), "--manifest-sha256", sha(self.manifest_path),
                                                   "--renderer", str(self.renderer), "--renderer-sha256", self.renderer_sha,
                                                   "--outdir", str(output)])
                            self.assertEqual(code, 2)
                            failure = json.loads(stderr.getvalue())
                            self.assertIn("SHA256_MISMATCH", failure["error"])
                            self.assertEqual(stdout.getvalue(), "")
                            self.assertEqual(child.call_count, 0 if phase == "before render" else 1)
                            self.assertEqual((output / "review-report.json").exists(), phase == "after page")
                            self.assertEqual(failure["partial_output_disposition"], "inspect before removal; no cleanup is claimed")
                        finally:
                            for changed, raw in originals.items():
                                if changed.exists():
                                    changed.write_bytes(raw)


class RuntimePayloadContract(unittest.TestCase):
    """Synthetic package/resource contracts, independent of a newly compiled EXE."""

    def setUp(self):
        import companion_runtime as runtime
        self.runtime=runtime
        self.temp=tempfile.TemporaryDirectory(prefix="runtime-payload-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.product=self.root/"product";self.product.mkdir()
        self._put("src/companion_cli.hpp",b'''inline const std::array<CompanionRuntimeResource,2>& companion_runtime_resources(){
 static const std::array<CompanionRuntimeResource,2> resources={{
  {"worker","worker.py","scripts/worker.py","python-worker",false},
  {"provider","providers/fixture","providers/fixture","provider-source",true}
 }};return resources;
}
inline const std::array<CompanionCommand,3>& companion_commands(){
 static const std::array<CompanionCommand,3> commands={{
  {"fixture",L"entry.py",{},"command, usage","purpose",true,{"worker","provider"},false,""},
  {"source-audit",L"audit_native_3x_capacity.py",{},"audit","purpose",true,{},false,""},
  {"retired",L"retired.py",{},"retired","purpose",false,{},true,"fixture"}
 }};return commands;
}
''')
        self._put("scripts/entry.py",b"import shared\nfrom companion_runtime import runtime_resource_path\n")
        self._put("scripts/audit_native_3x_capacity.py",b"import shared\n")
        self._put("scripts/worker.py",b"import shared\n")
        self._put("scripts/shared.py",b"# TEST synthetic helper\n")
        self._put("scripts/retired.py",b"# TEST retired writer must not be packaged\n")
        self._put("scripts/companion_runtime.py",Path(runtime.__file__).read_bytes())
        self._put("resources/H3TierExport-workspace.json",b'{"h3_root":"TEST-only"}\n')
        self._put("providers/fixture/README.md",b"TEST provider source custody\n")
        self._put("providers/fixture/nested/__init__.py",b"")
        self._put("providers/fixture/nested/tool.py",b"# TEST provider subprocess source\n")

    def _put(self,relative,raw):
        path=self.product/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        return path

    def _sources(self):
        return [{"path":p.relative_to(self.product).as_posix(),"bytes":p.stat().st_size,"sha256":sha(p)}
                for base in ("src","scripts","resources","providers") for p in sorted((self.product/base).rglob("*")) if p.is_file()]

    def _freeze(self):
        sources=self._sources();binding=self.runtime.freeze_payload(sources,product=self.product)
        return binding,self.runtime.verify_payload(binding,product=self.product,sources=sources),sources

    def _install_manifest(self,manifest,contents):
        raw=self.runtime._manifest_bytes(manifest);digest=sha_bytes(raw)
        root=self.product/(".runtime-"+digest);root.mkdir(exist_ok=True)
        for name,data in contents.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        (root/"runtime-manifest.json").write_bytes(raw)
        return {"schema":self.runtime.BINDING_SCHEMA,"directory":root.name,"manifest_sha256":digest,
                "file_count":len(manifest["files"]),"bytes":sum(r["bytes"] for r in manifest["files"])}

    def test_freeze_uses_native_registry_digit_script_and_typed_transitive_resources(self):
        binding,manifest,sources=self._freeze()
        self.assertEqual(manifest["schema"],"h3.tier-export.companion-runtime.v2")
        self.assertEqual(manifest["entrypoints"],{"fixture":"entry.py","source-audit":"audit_native_3x_capacity.py"})
        self.assertEqual(manifest["entrypoint_resources"],{"fixture":["worker","provider"],"source-audit":[]})
        rows={r["path"]:r for r in manifest["files"]}
        self.assertEqual(rows["worker.py"]["role"],"python-worker")
        self.assertEqual(rows["shared.py"]["role"],"python-module")
        self.assertEqual(rows["H3TierExport-workspace.json"]["role"],"configuration")
        self.assertEqual(rows["providers/fixture/nested/__init__.py"]["role"],"provider-source")
        self.assertEqual(rows["providers/fixture/nested/__init__.py"]["bytes"],0)
        self.assertNotIn("retired.py",rows)
        self.assertEqual(self.runtime.freeze_payload(sources,product=self.product),binding)

    def test_real_registry_covers_all_active_commands_and_only_declared_resources(self):
        registry=self.runtime.runtime_registry(PRODUCT)
        self.assertEqual(set(registry["entrypoints"]), ACTIVE_COMPANIONS)
        self.assertNotIn("quality-review",registry["entrypoints"])
        self.assertEqual(registry["entrypoints"]["source-audit"],"audit_native_3x_capacity.py")
        self.assertEqual(set(registry["entrypoint_resources"]),set(registry["entrypoints"]))
        self.assertEqual(set(registry["resources"]),{"blender-worker","flag-pole-reference","h3-art-pipeline"})

    def test_legacy_v1_payload_and_archive_verification_do_not_require_current_registry(self):
        data=b"# TEST old immutable module\n"
        manifest={"schema":"h3.tier-export.companion-runtime.v1","entrypoints":{"old":"old.py"},
                  "files":[{"path":"old.py","source_path":"scripts/old.py","bytes":len(data),"sha256":sha_bytes(data)}]}
        binding=self._install_manifest(manifest,{"old.py":data})
        (self.product/"src/companion_cli.hpp").unlink()
        self.assertEqual(self.runtime.verify_payload(binding,product=self.product),manifest)
        with mock.patch.object(self.runtime,"frozen_root",return_value=self.product/binding["directory"]):
            self.assertEqual(self.runtime.source_path("scripts/old.py").read_bytes(),data)
            with self.assertRaisesRegex(RuntimeError,"RUNTIME_RESOURCE_NOT_PACKAGED"):
                self.runtime.runtime_resource_path("provider",self.product)

    def test_frozen_resource_and_source_lookup_survive_removed_checkout_without_fallback(self):
        binding,manifest,sources=self._freeze();root=self.product/binding["directory"]
        expected=(root/"providers/fixture/README.md").read_bytes()
        self._put("providers/fixture/README.md",b"TEST later mutable provider bytes\n")
        self._put("scripts/shared.py",b"# TEST later mutable module\n")
        (self.product/"src/companion_cli.hpp").unlink()
        self.assertEqual(self.runtime.verify_payload(binding,product=self.product,sources=sources),manifest)
        with mock.patch.object(self.runtime,"frozen_root",return_value=root):
            self.assertEqual(self.runtime.runtime_resource_path("provider",self.product),root/"providers/fixture")
            self.assertEqual((self.runtime.runtime_resource_path("provider")/"README.md").read_bytes(),expected)
            self.assertEqual(self.runtime.runtime_resource_path("worker"),root/"worker.py")
            self.assertEqual(self.runtime.source_path("scripts/shared.py"),root/"shared.py")
            with self.assertRaisesRegex(RuntimeError,"RUNTIME_RESOURCE_NOT_PACKAGED"):
                self.runtime.runtime_resource_path("unbound")

    def test_candidate_resource_lookup_uses_its_own_declared_original_tree(self):
        self.assertEqual(self.runtime.runtime_resource_path("provider",self.product),self.product/"providers/fixture")
        self.assertEqual(self.runtime.runtime_resource_path("worker",self.product),self.product/"scripts/worker.py")
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_RESOURCE_UNDECLARED"):
            self.runtime.runtime_resource_path("unbound",self.product)

    def test_extra_nested_file_or_empty_directory_is_rejected(self):
        binding,_,_=self._freeze();root=self.product/binding["directory"]
        extra=root/"providers/fixture/extra.py";extra.write_bytes(b"# TEST unexpected\n")
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_EXTRA_FILE"):
            self.runtime.verify_payload(binding,product=self.product)
        extra.unlink();empty=root/"providers/fixture/unlisted-directory";empty.mkdir()
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_EXTRA_FILE"):
            self.runtime.verify_payload(binding,product=self.product)

    def test_changed_frozen_resource_never_substitutes_the_live_provider(self):
        binding,_,_=self._freeze();root=self.product/binding["directory"]
        (root/"providers/fixture/nested/tool.py").write_bytes(b"# TEST drift\n")
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_FILE_DRIFT"):
            self.runtime.verify_payload(binding,product=self.product)

    def test_paths_roles_resource_graph_and_directory_case_aliases_fail_closed(self):
        _,manifest,_=self._freeze()
        def alter(role=None,path=None,source=None):
            value=copy.deepcopy(manifest)
            row=next(r for r in value["files"] if r["path"]=="providers/fixture/nested/tool.py")
            if role is not None:row["role"]=role
            if path is not None:row["path"]=path
            if source is not None:row["source_path"]=source
            return value
        cases=[alter(path="../outside.py"),alter(path="providers\\fixture\\tool.py"),
               alter(path="providers/fixture/./tool.py"),alter(path="providers/fixture/NESTED/tool.py"),
               alter(source="providers/fixture/../outside.py"),alter(role="python-module")]
        bad=copy.deepcopy(manifest);bad["entrypoint_resources"].pop("source-audit");cases.append(bad)
        bad=copy.deepcopy(manifest);bad["entrypoint_resources"]["fixture"]=["missing"];cases.append(bad)
        bad=copy.deepcopy(manifest);bad["resources"]["provider"]["tree"]=False;cases.append(bad)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):self.runtime._manifest_rows(value)

    def test_manifest_count_and_total_limits_apply_before_payload_reads(self):
        _,manifest,_=self._freeze()
        value=copy.deepcopy(manifest);value["files"]=[value["files"][0]]*513
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_FILE_COUNT_INVALID"):self.runtime._manifest_rows(value)
        value=copy.deepcopy(manifest);value["files"][0]["bytes"]=32*1024*1024
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_TOTAL_MISMATCH"):self.runtime._manifest_rows(value)

    def test_runtime_reparse_directory_is_rejected_without_following_it(self):
        binding,_,_=self._freeze();root=self.product/binding["directory"]
        outside=self.root/"outside";outside.mkdir();marker=outside/"unowned.txt";marker.write_bytes(b"TEST outside marker")
        link=root/"providers/fixture/linked"
        if os.name=="nt":
            result=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(link),str(outside)],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        else:link.symlink_to(outside,target_is_directory=True)
        try:
            with self.assertRaisesRegex(RuntimeError,"RUNTIME_REPARSE_FORBIDDEN"):
                self.runtime.verify_payload(binding,product=self.product)
            self.assertEqual(marker.read_bytes(),b"TEST outside marker")
        finally:
            if os.name=="nt":link.rmdir()
            else:link.unlink()


class PublisherArchiveContract(unittest.TestCase):
    """Synthetic release custody: no fixture executable is launched or artwork written."""

    def setUp(self):
        import publish as publisher
        self.publisher=publisher
        self.temp=tempfile.TemporaryDirectory(prefix="publisher-archive-contract-")
        self.addCleanup(self.temp.cleanup)
        self.product=Path(self.temp.name)/"product"
        self.dist=self.product/"dist";self.dist.mkdir(parents=True)
        self.reports=self.product/"reports/current";self.reports.mkdir(parents=True)
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        for name,value in (("PRODUCT",self.product),("DIST",self.dist),("REPORTS",self.reports),
                           ("BUILD",self.product/"missing-next-candidate"),("TESTS",self.product/"missing-next-tests.json")):
            stack.enter_context(mock.patch.object(publisher,name,value))
        self.source_guard=stack.enter_context(mock.patch.object(publisher,"validate_build_sources",
            side_effect=AssertionError("archive-only must not validate mutable checkout sources")))
        self.runner=stack.enter_context(mock.patch.object(publisher,"run",
            side_effect=AssertionError("archive-only must not execute a release or candidate")))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        for name in publisher.RELEASE_FILES:(self.dist/name).write_bytes(("TEST archive "+name).encode("utf-8"))
        executable=self.dist/"H3TierExport.exe"
        self.build={"schema":"h3.tier-export.build.v2","passed":True,"source_current":True,
                    "executable":{"sha256":sha(executable),"bytes":executable.stat().st_size},
                    "sources":[{"path":"src/historical-and-absent.cpp","bytes":19,"sha256":"0"*64}]}
        self.tests={"schema":"h3.tier-export.test-suite.v2","passed":True,"source_current":True,
                    "exe_sha256":sha(executable),"results":[{"name":"TEST historical qualification","exit_code":0}]}
        self._seal()

    def _seal(self):
        (self.dist/"H3TierExport-build-receipt.json").write_text(json.dumps(self.build),encoding="utf-8")
        self.tests["build_receipt_sha256"]=sha(self.dist/"H3TierExport-build-receipt.json")
        (self.dist/"H3TierExport-tests-receipt.json").write_text(json.dumps(self.tests),encoding="utf-8")
        self._seal_release()

    def _seal_release(self):
        release={"schema":"h3.tier-export.release.v2","version":"TEST-only",
                 "exe_sha256":sha(self.dist/"H3TierExport.exe"),
                 "build_receipt_sha256":sha(self.dist/"H3TierExport-build-receipt.json"),
                 "test_receipt_sha256":sha(self.dist/"H3TierExport-tests-receipt.json"),
                 "files":[{"path":name,"bytes":(self.dist/name).stat().st_size,
                           "sha256":sha(self.dist/name)} for name in self.publisher.RELEASE_FILES]}
        if "companion_runtime" in self.build:release["companion_runtime"]=self.build["companion_runtime"]
        raw=json.dumps(release).encode("utf-8")
        (self.dist/self.publisher.RELEASE_RECEIPT).write_bytes(raw)
        (self.reports/"release.json").write_bytes(raw)

    def _current(self):
        return {p.name:p.read_bytes() for p in self.dist.iterdir() if p.is_file()}

    def _archive(self):
        digest=sha(self.dist/self.publisher.RELEASE_RECEIPT)
        self.assertEqual(self.publisher.main(["--archive-current","--expected-release-sha256",digest]),0)
        return self.dist/"archive"/("release-"+digest)

    def test_archive_only_needs_no_checkout_candidate_or_runtime_for_legacy_release(self):
        before=self._current();archive=self._archive()
        self.assertEqual({p.name:p.read_bytes() for p in archive.iterdir()},before)
        self.assertEqual(len(before),9)
        self.assertEqual(self._archive(),archive)
        self.assertEqual(len(list(archive.parent.iterdir())),1)
        self.assertEqual(self._current(),before)
        self.source_guard.assert_not_called();self.runner.assert_not_called()

    def test_archive_refuses_expected_pin_or_current_mirror_drift_without_writes(self):
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_CURRENT_RELEASE_CHANGED"):
            self.publisher.main(["--archive-current","--expected-release-sha256","0"*64])
        (self.reports/"release.json").write_bytes(b"TEST mirror mismatch")
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_CURRENT_REPORT_MISMATCH"):self._archive()
        self.assertFalse((self.dist/"archive").exists())

    def test_archive_refuses_mixed_test_build_even_with_matching_release_file_rows(self):
        self.tests["build_receipt_sha256"]="0"*64
        (self.dist/"H3TierExport-tests-receipt.json").write_text(json.dumps(self.tests),encoding="utf-8")
        self._seal_release()
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_TEST_BUILD_IDENTITY_INVALID"):self._archive()
        self.assertFalse((self.dist/"archive").exists())

    def test_archive_refuses_a_failed_group_even_when_passed_flag_is_true(self):
        self.tests["results"][0]["exit_code"]=1;self._seal()
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_TEST_RESULTS_INVALID"):self._archive()
        self.assertFalse((self.dist/"archive").exists())

    def test_archive_requires_every_original_release_file_and_rejects_drift(self):
        (self.dist/"H3TierExport-workspace.json").write_bytes(b"TEST later workspace bytes")
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_RELEASE_FILE_DRIFT"):self._archive()
        self.assertFalse((self.dist/"archive").exists())

    def test_archive_never_overwrites_an_existing_conflicting_archive(self):
        archive=self._archive();before=self._current()
        changed=archive/"H3TierExport.exe";changed.write_bytes(b"TEST archive tamper")
        with self.assertRaisesRegex(RuntimeError,"ARCHIVE_EXISTING_FILE_CONFLICT"):self._archive()
        self.assertEqual(changed.read_bytes(),b"TEST archive tamper")
        self.assertEqual(self._current(),before)

    def test_current_drift_leaves_identifiable_staging_without_partial_final_archive(self):
        digest=sha(self.dist/self.publisher.RELEASE_RECEIPT)
        final=self.dist/"archive"/("release-"+digest)
        original_verify=self.publisher._verify_archive
        def verify_then_drift(directory,files):
            original_verify(directory,files)
            if directory.name.endswith(".staging"):
                raw=(self.dist/self.publisher.RELEASE_RECEIPT).read_bytes()+b"\n"
                (self.dist/self.publisher.RELEASE_RECEIPT).write_bytes(raw)
                (self.reports/"release.json").write_bytes(raw)
        with mock.patch.object(self.publisher,"_verify_archive",side_effect=verify_then_drift):
            with self.assertRaisesRegex(RuntimeError,"ARCHIVE_CURRENT_RELEASE_CHANGED"):
                self.publisher.main(["--archive-current","--expected-release-sha256",digest])
        self.assertFalse(final.exists())
        self.assertEqual(len(list(final.with_name("."+final.name+".staging").iterdir())),9)

    def test_frozen_runtime_is_verified_at_its_single_original_location(self):
        data=b"# TEST-only immutable runtime; never executed\n"
        row={"path":"fixture.py","source_path":"scripts/fixture.py","bytes":len(data),"sha256":sha_bytes(data)}
        manifest={"schema":"h3.tier-export.companion-runtime.v1","entrypoints":{"fixture":"fixture.py"},"files":[row]}
        raw=json.dumps(manifest).encode("utf-8");digest=sha_bytes(raw)
        binding={"schema":"h3.tier-export.companion-runtime-binding.v1","directory":".runtime-"+digest,
                 "manifest_sha256":digest,"file_count":1,"bytes":len(data)}
        root=self.product/binding["directory"];root.mkdir()
        (root/"runtime-manifest.json").write_bytes(raw);(root/"fixture.py").write_bytes(data)
        self.build["companion_runtime"]=binding
        self.build["sources"].append({"path":row["source_path"],"bytes":row["bytes"],"sha256":row["sha256"]})
        self._seal();before=self._current();archive=self._archive()
        self.assertEqual(len(list(archive.iterdir())),9)
        self.assertEqual(list(self.product.glob(".runtime-*")),[root])
        (root/"fixture.py").write_bytes(data+b"# TEST drift\n")
        with self.assertRaisesRegex(RuntimeError,"RUNTIME_FILE_DRIFT"):self._archive()
        self.assertEqual(self._current(),before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
