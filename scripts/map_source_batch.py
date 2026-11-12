"""Recover old road/river HQ parents by exact native-grid sampling and map QA.

The operation pins the historical parent copies, samples each candidate directly
at its reduced rational source ratio, then runs the native motion receipt and
the H3 map-tile seam auditor. It never stretches, paints collars, or rewrites a
source parent. Only source parents passing every measured geometry/neighbor
check may be copied byte-for-byte into official master slots by the explicit
restore action; artistic approval remains separate.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from PIL import Image
import numpy as np

import map_assembly as assembly
import road_batch_quality as road_quality
import subject_scale_layout as common
from tile_interface_components import rational_native_roi_layer

PRODUCT = resolve_tool_root(__file__)
OUT_ROOT = PRODUCT / "build/experiments/map-source-batch"
REPORT_SCHEMA = "h3.map-source-batch.report.v1"
REQUEST_SCHEMA = "h3.map-source-batch.request.v1"
ROAD_IDS = {"COBBRD", "DIRTRD", "GRAVRD"}
RIVER_IDS = {"CLRRVR", "ICYRVR", "LAVRVR", "MUDRVR"}
VISIBLE_ALPHA8 = 16


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path: Path) -> dict:
    p = path.resolve()
    return {"path": str(p), "sha256": sha_file(p)}


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def load_ref(binding: dict) -> dict:
    p, raw = common.pin(binding)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"MAP_SOURCE_JSON_REQUIRED: {p}") from error


def alpha_outside_crop(path: Path, ratio: Fraction, geometry: dict) -> dict:
    with Image.open(path) as image:
        image.load()
        alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[:, :, 3]
    ox, oy = geometry["origin_1x"]
    cw, ch = geometry["crop_1x"]
    yy, xx = np.nonzero(alpha > 0)
    x = (xx.astype(np.float64) + 0.5) / float(ratio)
    y = (yy.astype(np.float64) + 0.5) / float(ratio)
    outside = (x < ox) | (x >= ox + cw) | (y < oy) | (y >= oy + ch)
    values = alpha[yy[outside], xx[outside]]
    return {"positive_pixels": int(values.size),
            "visible_pixels_alpha8_ge_16": int(np.count_nonzero(values >= VISIBLE_ALPHA8)),
            "max_alpha8": int(values.max()) if values.size else 0,
            "visible_threshold_alpha8": VISIBLE_ALPHA8}


def make_source_row(kind: str, stem: str, key: tuple, source: Path, expected_sha: str,
                    pixels: list[int], lineage: dict, native: dict) -> dict:
    common.need(source.is_file(), "MAP_SOURCE_PARENT_MISSING: " + stem)
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    common.need(digest == expected_sha, "MAP_SOURCE_PARENT_SHA_DIFFERS: " + stem)
    common.need(key in native, "MAP_SOURCE_NATIVE_SLOT_MISSING: " + stem)
    g = native[key]
    geometry = {"canvas_1x": g["canvas"], "crop_1x": g["crop"], "origin_1x": g["anchor"]}
    with Image.open(source) as image:
        image.load()
        common.need(image.format == "PNG", "MAP_SOURCE_PARENT_NOT_PNG: " + stem)
        actual_pixels = list(image.size)
    common.need(actual_pixels == pixels, "MAP_SOURCE_PARENT_PIXELS_DRIFT: " + stem)
    cw, ch = geometry["canvas_1x"]
    ratio_x, ratio_y = Fraction(actual_pixels[0], cw), Fraction(actual_pixels[1], ch)
    if ratio_x != ratio_y:
        return {"stem": stem, "key": list(key), "kind": kind, "source": {"path": str(source.resolve()), "sha256": digest,
                "pixels": actual_pixels}, "geometry": geometry, "lineage": lineage,
                "source_status": "REDRAW_REQUIRED", "reason": "PARENT_ASPECT_DIFFERS_FROM_NATIVE_CANVAS"}
    ratio = ratio_x
    if ratio < 3:
        return {"stem": stem, "key": list(key), "kind": kind, "source": {"path": str(source.resolve()), "sha256": digest,
                "pixels": actual_pixels}, "geometry": geometry, "lineage": lineage,
                "source_status": "REDRAW_REQUIRED", "reason": "SOURCE_DENSITY_BELOW_3X"}
    outside = alpha_outside_crop(source, ratio, geometry)
    row = {"stem": stem, "key": list(key), "kind": kind,
           "source": {"path": str(source.resolve()), "sha256": digest, "pixels": actual_pixels},
           "geometry": geometry, "ratio": [ratio.numerator, ratio.denominator],
           "lineage": lineage, "outside_crop_alpha": outside}
    if outside["visible_pixels_alpha8_ge_16"]:
        row.update(source_status="REDRAW_REQUIRED", reason="VISIBLE_ALPHA_OUTSIDE_NATIVE_CROP")
    else:
        row.update(source_status="EXACT_GRID_CANDIDATE", reason=None)
    return row


def collect_sources(road_prepared_ref: dict, river_prepared_ref: dict,
                    native_ref: dict, master_root: Path) -> tuple[list[dict], dict, list[dict]]:
    road = load_ref(road_prepared_ref)
    river_prepared = load_ref(river_prepared_ref)
    common.need(road.get("schema") == "h3.road.reusable-repair.prepared.v1", "ROAD_SOURCE_PREPARED_REQUIRED")
    common.need(river_prepared.get("schema") == "h3.river.reusable-repair.prepared.v1", "RIVER_SOURCE_PREPARED_REQUIRED")
    preflight_ref = river_prepared.get("whole_batch_preflight")
    common.need(isinstance(preflight_ref, dict), "RIVER_SOURCE_PREFLIGHT_REQUIRED")
    preflight = load_ref(preflight_ref)
    common.need(preflight.get("schema") == "h3.river.interface.preflight.v1", "RIVER_SOURCE_PREFLIGHT_SCHEMA_INVALID")
    native = assembly.geometry_rows(native_ref)
    rows = []
    for source in road["original_sources"]:
        key = tuple(source["native_key"])
        stem = source["stem"]
        rows.append(make_source_row("road", stem, key, Path(source["frozen_parent"]["path"]),
            source["frozen_parent"]["sha256"], source["frozen_parent"]["pixels"],
            {"historical_master_path": source["original_master"]["path"],
             "historical_master_sha256": source["original_master"]["sha256"],
             "historical_path_exists": Path(source["original_master"]["path"]).is_file(),
             "historical_sha_matches_copy": source["frozen_parent"]["sha256"] == source["original_master"]["sha256"],
             "model_origin_newly_verified": source.get("model_origin_newly_verified", False)}, native))
    river_root = Path(river_prepared["material_root"]) / "原始高清父图"
    for source in preflight["records"]:
        key = tuple(source["native_key"])
        stem = source["stem"]
        p = river_root / (stem + ".png")
        historical = source.get("original_master") or {}
        rows.append(make_source_row("river", stem, key, p, source["source"]["sha256"],
            source["source"]["pixels"],
            {"historical_master_path": historical.get("path", source["source"]["path"]),
             "historical_master_sha256": historical.get("sha256", source["source"]["sha256"]),
             "historical_path_exists": bool(historical.get("path") and Path(historical["path"]).is_file()),
             "historical_sha_matches_copy": source["source"]["sha256"] == historical.get("sha256", source["source"]["sha256"]),
             "source_lineage_status": source.get("source_lineage_status"),
             "generation_receipt": source.get("generation_receipt")}, native))
    keys = [tuple(row["key"]) for row in rows]
    common.need(len(rows) == 103 and len(set(keys)) == 103, "ROAD_RIVER_SOURCE_SET_MUST_BE_EXACTLY_103_UNIQUE_SLOTS")
    common.need(sum(row["kind"] == "road" for row in rows) == 51 and
                sum(row["kind"] == "river" for row in rows) == 52, "ROAD_RIVER_SOURCE_COUNTS_DIFFER")
    common.need(all((row["kind"] == "road" and row["key"][1] in ROAD_IDS) or
                    (row["kind"] == "river" and row["key"][1] in RIVER_IDS) for row in rows),
                "ROAD_RIVER_SOURCE_IDENTITY_OUTSIDE_SCOPE")
    return rows, native, preflight


def targets(row: dict) -> dict:
    return {"canvas_1x": row["canvas"], "crop_1x": row["crop"], "origin_1x": row["anchor"]}


def freeze(operation_id: str, road_prepared: Path, river_prepared: Path,
           inventory: Path, editor_proof: Path, seam_auditor: Path,
           release_root: Path, master_root: Path) -> dict:
    common.need(re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", operation_id) is not None,
                "MAP_SOURCE_OPERATION_ID_INVALID")
    out = OUT_ROOT / operation_id
    common.need(not out.exists(), "MAP_SOURCE_OPERATION_ALREADY_EXISTS")
    refs = {"road_prepared": ref(road_prepared), "river_prepared": ref(river_prepared),
            "native_inventory": ref(inventory), "editor_source_proof": ref(editor_proof),
            "seam_auditor": ref(seam_auditor)}
    river_prepared_doc = load_ref(refs["river_prepared"])
    rows, native, preflight = collect_sources(refs["road_prepared"], refs["river_prepared"],
                                               refs["native_inventory"], master_root)
    proof_doc = load_ref(refs["editor_source_proof"])
    common.need(proof_doc.get("schema") == "h3.map-source-oracle.report.v1" and proof_doc.get("status") == "PASS",
                "ROAD_EDITOR_SOURCE_PROOF_REQUIRED")
    common.need(proof_doc.get("native_reference_graph", {}).get("native_inventory", {}).get("sha256") ==
                refs["native_inventory"]["sha256"], "ROAD_EDITOR_PROOF_INVENTORY_DIFFERS")
    common.need(seam_auditor.is_file(), "MAP_SEAM_AUDITOR_MISSING")
    # Ensure the official native reference tree and inventory agree before freezing.
    for row in rows:
        g = native[tuple(row["key"])]
        row["native_inventory_geometry"] = {"canvas": g["canvas"], "crop": g["crop"], "anchor": g["anchor"]}
        row["official_master_target"] = str((master_root / row["key"][1] / f"{row['key'][2]}_{row['key'][3]}.png").resolve())
    contracts = []
    items = []
    for row in rows:
        if row["source_status"] != "EXACT_GRID_CANDIDATE":
            continue
        k = tuple(row["key"]); g = native[k]
        contracts.append({"key": row["key"], "targets": targets(g),
                          "equivalence_class": g.get("equivalence_class") or hashlib.sha256(json.dumps(row["key"]).encode()).hexdigest()})
        layer = rational_native_roi_layer(row["source"], row["key"], targets(g), row["ratio"],
                                         [g["anchor"][0], g["anchor"][1], g["crop"][0], g["crop"][1]])["layer"]
        items.append({"id": f"{row['key'][1]}_{row['key'][2]}_{row['key'][3]}", "key": row["key"],
                      "targets": targets(g), "mode": "mesh", "time": 0, "layers": [layer],
                      "quality": {"mode": "anchored-surface"},
                      "sampling_budget": {"schema": "h3.motion.sampling-budget.v1", "max_loop_visits": 1 << 28}})
    common.need(items, "MAP_SOURCE_NO_EXACT_GRID_CANDIDATES")
    out.mkdir(parents=True, exist_ok=False)
    native_contract_path = out / "native-contract.json"
    write_new(native_contract_path, {"schema": "h3.map-source-batch.native-contract.v1", "entries": contracts,
        "source_inventory": refs["native_inventory"]})
    native_contract_ref = ref(native_contract_path)
    batch_root = out / "batches"
    batch_root.mkdir()
    motion_specs = []
    # Keep each operation comfortably under the sampler's aggregate work cap.
    # The 103 parents vary substantially in source density, so a few small
    # batches isolate expensive frames without changing any frame's sampling.
    for index in range(0, len(items), 2):
        batch_id = f"batch-{index // 2:03d}"
        batch_dir = batch_root / batch_id
        batch_dir.mkdir()
        spec = {"schema": "h3.motion.spec.v1", "output_root": str((batch_dir / "candidate3x").resolve()),
                "native_contracts": [native_contract_ref], "target_scale": 3,
                "settings": {"backend": "cpu", "space": "linear", "filter": "h3-quality", "workers": 4, "memory_gib": 4},
                "items": items[index:index + 2]}
        spec_path = batch_dir / "motion-spec.json"
        write_new(spec_path, spec)
        motion_specs.append({"id": batch_id, "item_count": len(spec["items"]), "spec": ref(spec_path)})
    prepared_ref = preflight.get("native_inventory", {}).get("sha256")
    common.need(prepared_ref == refs["native_inventory"]["sha256"], "RIVER_NATIVE_INVENTORY_DRIFT")
    interfaces={}
    if road_quality.REGISTRY.is_file():
        registry=load_ref(ref(road_quality.REGISTRY))
        for family,binding in registry.get("interface_contracts",{}).items():
            if family in ROAD_IDS:
                road_quality.interface_references(binding,native,refs["native_inventory"],family)
                interfaces[family]=binding
    request = {"schema": REQUEST_SCHEMA, "operation_id": operation_id, "created_by": "H3TierExport map-source-batch",
        "native_inventory": refs["native_inventory"], "road_prepared": refs["road_prepared"],
        "river_prepared": refs["river_prepared"], "river_preflight": river_prepared_doc["whole_batch_preflight"],
        "editor_source_proof": refs["editor_source_proof"], "seam_auditor": refs["seam_auditor"],
        "release_root": str(release_root.resolve()), "master_root": str(master_root.resolve()),
        "source_count": len(rows), "road_count": 51, "river_count": 52,
        "exact_grid_candidates": len(items), "redraw_required_at_source_preflight": len(rows) - len(items),
        "source_rows": rows, "native_contract": native_contract_ref, "motion_specs": motion_specs,
        "interface_contracts":interfaces,
        "source_writes": 0, "official_masters_written": 0, "art_approved": False, "game_deployed": False}
    request_path = out / "request.json"
    write_new(request_path, request)
    return {"status": "FROZEN", "request": ref(request_path), "exact_grid_candidates": len(items),
            "redraw_required_at_source_preflight": len(rows) - len(items), "images_written": 0}


def invoking_exe(args) -> Path:
    common.need(args.renderer and args.renderer_sha256, "MAP_SOURCE_INVOKING_EXE_REQUIRED")
    p, _ = common.pin({"path": args.renderer, "sha256": args.renderer_sha256})
    return p


def run_json(exe: Path, *argv: str, timeout: int = 7200) -> dict:
    completed = subprocess.run([str(exe), *map(str, argv)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, cwd=PRODUCT)
    output = completed.stdout.strip() or completed.stderr.strip()
    try:
        data = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError(f"MAP_SOURCE_TOOL_JSON_INVALID exit={completed.returncode}: {output[-2000:]}") from error
    if completed.returncode:
        raise ValueError(f"MAP_SOURCE_TOOL_FAILED exit={completed.returncode}: {output[-2000:]}")
    return data


def pin_request(binding: dict, operation_id: str) -> tuple[Path, dict]:
    # Recover the pinned request path from its immutable binding.
    request_path,raw = common.pin(binding)
    request_path=request_path.resolve()
    request = json.loads(raw.decode("utf-8-sig"))
    common.need(request.get("schema") == REQUEST_SCHEMA and request.get("operation_id") == operation_id,
                "MAP_SOURCE_REQUEST_IDENTITY_DIFFERS")
    common.need(request_path.parent.resolve() == (OUT_ROOT / operation_id).resolve(),
                "MAP_SOURCE_REQUEST_OUTSIDE_EXPERIMENT_ROOT")
    common.pin(request["road_prepared"]);common.pin(request["river_prepared"])
    common.pin(request["river_preflight"])
    expected = {tuple(row["key"]): row for row in request["source_rows"]}
    common.need(len(expected) == len(request["source_rows"]) == 103, "MAP_SOURCE_SOURCE_SET_DRIFTED")
    for key, row in expected.items():
        common.need(tuple(row["key"]) == key, "MAP_SOURCE_KEY_DUPLICATE")
        common.pin(row["source"])
    common.pin(request["editor_source_proof"]);common.pin(request["seam_auditor"])
    common.pin(request["native_inventory"])
    common.pin(request["native_contract"])
    for binding in request.get("interface_contracts",{}).values():common.pin(binding)
    common.need(sum(row["item_count"] for row in request["motion_specs"]) == request["exact_grid_candidates"],
                "MAP_SOURCE_BATCH_PARTITION_DIFFERS")
    for batch in request["motion_specs"]: common.pin(batch["spec"])
    return request_path.parent, request


def stage_check(root: Path, request: dict, exe: Path) -> dict:
    states = [];ready_total=0
    state_path = root / "stage-state.json"
    prior_state = json.loads(state_path.read_bytes()) if state_path.exists() else None
    prior_batches = {row["id"]: row for row in (prior_state or {}).get("batches", [])}
    for frozen in request["motion_specs"]:
        batch_dir = root / "batches" / frozen["id"]
        spec = Path(frozen["spec"]["path"]);batch = batch_dir / "motion-batch.json"
        if batch.exists():
            prior = prior_batches.get(frozen["id"])
            if prior:
                common.need(sha_file(batch) == prior["motion_batch"]["sha256"],
                            "MAP_SOURCE_BATCH_OUTPUT_DRIFT: " + frozen["id"])
            else:
                replay_path=batch_dir/"motion-plan-replay.json"
                if not replay_path.exists(): run_json(exe,"motion-plan","--spec",str(spec),"--out",str(replay_path))
                old_batch=json.loads(batch.read_bytes());replay=json.loads(replay_path.read_bytes())
                core=lambda value:(value.get("schema"),value.get("algorithm"),value.get("target_scale"),
                    value.get("output_root"),value.get("native_contracts"),
                    [{k:item.get(k) for k in ("id","key","targets","fingerprint","output")} for item in value.get("items",[])])
                common.need(core(old_batch)==core(replay),"MAP_SOURCE_BATCH_PLAN_REPLAY_DIFFERS: "+frozen["id"])
        else:
            run_json(exe, "motion-plan", "--spec", str(spec), "--out", str(batch))
        checked = run_json(exe, "motion-check", "--batch", str(batch))
        probe = run_json(exe, "motion-probe", "--batch", str(batch))
        ready = checked.get("ready", 0);ready_total += ready
        common.need(ready == frozen["item_count"] and checked.get("blocked", 0) == 0 and checked.get("pending", 0) == 0,
                    "MAP_SOURCE_MOTION_CHECK_NOT_READY: " + frozen["id"])
        check_path = batch_dir / "motion-check.json";probe_path=batch_dir / "motion-probe.json"
        if check_path.exists():
            prior_check=json.loads(check_path.read_bytes())
            common.need((prior_check.get("ready"),prior_check.get("blocked"),prior_check.get("failed")) ==
                        (checked.get("ready"),checked.get("blocked"),checked.get("failed")), "MAP_SOURCE_CHECK_REPLAY_DIFFERS")
        else: write_new(check_path, checked)
        if probe_path.exists():
            prior_probe=json.loads(probe_path.read_bytes())
            common.need((prior_probe.get("status"),prior_probe.get("ready"),prior_probe.get("failed")) ==
                        (probe.get("status"),probe.get("ready"),probe.get("failed")), "MAP_SOURCE_PROBE_REPLAY_DIFFERS")
        else: write_new(probe_path, probe)
        states.append({"id": frozen["id"], "motion_batch": ref(batch),
                       "motion_check": ref(check_path), "motion_probe": ref(probe_path)})
    common.need(ready_total == request["exact_grid_candidates"], "MAP_SOURCE_TOTAL_READY_COUNT_DIFFERS")
    state = {"schema": "h3.map-source-batch.stage-state.v1", "batches": states}
    if state_path.exists(): common.need(json.loads(state_path.read_bytes()) == state, "MAP_SOURCE_STAGE_STATE_DRIFT")
    else: write_new(state_path, state)
    return {"status": "CHECKED", "ready": ready_total, "blocked": 0, "outputs_written": 0}


def run_auditor(root: Path, request: dict, report_path: Path) -> dict:
    auditor = Path(request["seam_auditor"]["path"])
    common.pin(request["seam_auditor"])
    args = [sys.executable, "-B", "-X", "utf8", str(auditor), "--batch-root", str(root),
            "--release-root", request["release_root"], "--inventory", request["native_inventory"]["path"],
            "--report", str(report_path)]
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=7200, cwd=Path(request["release_root"]).parents[1])
    common.need(report_path.is_file(), "MAP_SOURCE_SEAM_REPORT_NOT_WRITTEN: " + (completed.stderr or completed.stdout)[-1200:])
    return json.loads(report_path.read_text(encoding="utf-8-sig"))


def candidate_decisions(request: dict, seam: dict, motion_run: dict, motion_verify: dict,
                        road_report: dict) -> list[dict]:
    common.need(motion_run.get("schema") == "h3.motion.run.v1" and motion_verify.get("status") == "COMPLETED",
                "MAP_SOURCE_MOTION_VERIFICATION_REQUIRED")
    common.need(motion_verify.get("failed") == 0, "MAP_SOURCE_MOTION_VERIFY_FAILED")
    frames = {tuple(row["key"]): row for row in seam.get("frames", [])}
    invalid = {}
    for issue in seam.get("issues", []):
        key = issue.get("key")
        if key: invalid.setdefault(tuple(key), []).append(issue)
    stress_failures = set()
    for section in ("road_functional_port_stress", "river_functional_port_stress"):
        for stress in seam.get(section, []):
            for finding in stress.get("port_connectivity_failures", []) + stress.get("native_port_rule_mismatches", []):
                if finding.get("key"):
                    stress_failures.add(tuple(finding["key"]))
    road_items = {tuple(row["key"]): row for row in road_report.get("items", [])}
    decisions = []
    for source in request["source_rows"]:
        key = tuple(source["key"])
        if source["source_status"] != "EXACT_GRID_CANDIDATE":
            decisions.append({"key": source["key"], "stem": source["stem"], "decision": "REDRAW_REQUIRED",
                              "reason": source.get("reason"), "source_sha256": source["source"]["sha256"]})
            continue
        reasons = []
        review_reasons = []
        frame = frames.get(key)
        if frame is None: reasons.append("NO_VERIFIED_3X_FRAME")
        elif not frame.get("registration", {}).get("valid"): reasons.append("NATIVE_REGISTRATION_INVALID")
        edge = frame.get("per_edge_comparison", {}) if frame else {}
        for side, audit in edge.items():
            if audit.get("missing_native_mouths") or audit.get("new_candidate_mouths"):
                reasons.append("NATIVE_" + side.upper() + "_MOUTH_PROFILE_DIFFERS")
            for match in audit.get("matches", []):
                if match.get("width_delta_candidate_minus_native") != 0:
                    reasons.append("NATIVE_" + side.upper() + "_MOUTH_WIDTH_DIFFERS")
                if abs(match.get("center_delta_candidate_minus_native", 0)) > 1e-9:
                    reasons.append("NATIVE_" + side.upper() + "_MOUTH_CENTER_DIFFERS")
        if key in invalid: reasons.append("SEAM_AUDIT_ISSUE")
        if key in stress_failures: reasons.append("FUNCTIONAL_PORT_CONNECTIVITY_FAIL")
        neighbors=[p for p in seam.get("candidate_neighbor_checks", [])
                   if tuple(p["frame_a"])==key or tuple(p["frame_b"])==key]
        if any(p.get("candidate_matches_native_profile_on_both_sides") is False for p in neighbors):
            review_reasons.append("LEGAL_NEIGHBOR_PROFILE_NOT_COMPATIBLE")
        road_item = road_items.get(key)
        if key[1] in ROAD_IDS:
            if road_report.get("schema")!=road_quality.REPORT:
                review_reasons.append("ROAD_PAINTED_REPORT_SCHEMA_NOT_VERIFIED")
            if any(not issue.get('key') or tuple(issue['key'])==key for issue in road_report.get('errors',[])):
                review_reasons.append("ROAD_PAINTED_REPORT_INPUT_ERROR")
            if road_item is None:
                review_reasons.append("ROAD_TOPOLOGY_NOT_MEASURED")
            elif road_item.get("topology", {}).get("status") != "PASS":
                reasons.append("ROAD_TOPOLOGY_FAIL")
            if road_item is None or road_item.get("shared_interface",{}).get("status")!="PASS":
                review_reasons.append("ROAD_PAINTED_INTERFACE_NOT_VERIFIED")
            pairs=[p for p in road_report.get("pairs",[]) if tuple(p.get("a",[]))==key or tuple(p.get("b",[]))==key]
            if not pairs or any(p.get("status")!="PASS" or p.get("shared_interface_verified") is not True for p in pairs):
                review_reasons.append("ROAD_LEGAL_PAINTED_NEIGHBORS_NOT_VERIFIED")
            if road_report.get("interface_errors",{}).get(key[1]):
                review_reasons.append("ROAD_INTERFACE_CONTRACT_FAILED")
        decisions.append({"key": source["key"], "stem": source["stem"],
            "decision": "REDRAW_REQUIRED" if reasons else "REVIEW_REQUIRED" if review_reasons else "RESTORE_CANDIDATE",
            "reason": reasons+review_reasons or None, "source_sha256": source["source"]["sha256"],
            "candidate_3x": frame.get("candidate_path") if frame else None,
            "legal_native_neighbors_checked":len(neighbors)})
    return decisions


def latest_decision_report(root: Path) -> Path:
    reports=sorted(root.glob("map-source-batch-report*.json"),key=lambda p:p.stat().st_mtime_ns)
    common.need(reports,"MAP_SOURCE_RUN_FIRST")
    return reports[-1]


def collect_batch_runs(root: Path, request: dict, exe: Path, verify_leaf: str = "motion-verify.json") -> tuple:
    runs=[];verifications=[];run_refs=[];verify_refs=[]
    for frozen in request["motion_specs"]:
        batch_dir=root/"batches"/frozen["id"];batch=batch_dir/"motion-batch.json"
        run_path=batch_dir/"run.json";verify_path=batch_dir/verify_leaf
        if not run_path.exists():
            run_json(exe,"motion-run","--batch",str(batch),"--workers","4","--memory-gib","4","--report",str(run_path),timeout=7200)
        run=json.loads(run_path.read_text(encoding="utf-8-sig"))
        common.need(run.get("schema")=="h3.motion.run.v1" and run.get("failed",0)==0 and
                    len(run.get("results",[]))==frozen["item_count"],"MAP_SOURCE_MOTION_RUN_INCOMPLETE: "+frozen["id"])
        if not verify_path.exists():
            run_json(exe,"motion-verify","--batch",str(batch),"--report",str(verify_path))
        verified=json.loads(verify_path.read_text(encoding="utf-8-sig"))
        common.need(verified.get("status")=="COMPLETED" and verified.get("failed")==0 and
                    len(verified.get("results",[]))==frozen["item_count"],"MAP_SOURCE_MOTION_VERIFY_FAILED: "+frozen["id"])
        common.need(all(item.get("status")=="VERIFIED" for item in verified["results"]),"MAP_SOURCE_SLOT_NOT_VERIFIED: "+frozen["id"])
        runs.append(run);verifications.append(verified);run_refs.append(ref(run_path));verify_refs.append(ref(verify_path))
    run_results=[item for row in runs for item in row["results"]]
    verify_results=[item for row in verifications for item in row["results"]]
    combined_run={"schema":"h3.motion.run.v1","status":"COMPLETED","target_scale":3,"failed":0,
                  "results":run_results,"batch_runs":run_refs}
    combined_verify={"schema":"h3.motion.run.v1","status":"COMPLETED","failed":0,
                     "results":verify_results,"batch_verifications":verify_refs}
    return runs,verifications,run_refs,verify_refs,combined_run,combined_verify


def stage_run(root: Path, request: dict, exe: Path) -> dict:
    state_path=root/"stage-state.json"
    if state_path.exists():
        state=json.loads(state_path.read_bytes());verified_ids=set()
        for row in state.get("batches",[]):
            batch=row["motion_batch"];check=row["motion_check"];probe=row["motion_probe"]
            common.need(sha_file(Path(batch["path"]))==batch["sha256"],"MAP_SOURCE_BATCH_OUTPUT_DRIFT")
            common.pin(check);common.pin(probe);verified_ids.add(row["id"])
        common.need(len(verified_ids)==len(request["motion_specs"]),"MAP_SOURCE_CHECK_STATE_INCOMPLETE")
    else:
        stage_check(root, request, exe)
    runs,verifications,run_refs,verify_refs,combined_run,combined_verify=collect_batch_runs(root,request,exe)
    run_path=root/"combined-run.json";verify_path=root/"combined-verify.json"
    if run_path.exists():common.need(json.loads(run_path.read_bytes())==combined_run,"MAP_SOURCE_RUN_AGGREGATE_REPLAY_DIFFERS")
    else:write_new(run_path,combined_run)
    if verify_path.exists():common.need(json.loads(verify_path.read_bytes())==combined_verify,"MAP_SOURCE_VERIFY_AGGREGATE_REPLAY_DIFFERS")
    else:write_new(verify_path,combined_verify)
    seam_path = root / "seam-audit.json"
    seam = json.loads(seam_path.read_text(encoding="utf-8-sig")) if seam_path.exists() else run_auditor(root, request, seam_path)
    road_spec={"schema":road_quality.SCHEMA,"operation_id":request["operation_id"],
        "road_families":["COBBRD","DIRTRD","GRAVRD"],"native_inventory":request["native_inventory"],
        "editor_source_proof":request["editor_source_proof"],"sampling_runs":run_refs,
        "interface_contracts":request.get("interface_contracts",{})}
    road_spec=road_quality.freeze(road_spec);road=road_quality.analyze(road_spec)
    road_path=root/"road-batch-report.json"
    if road_path.exists():common.need(json.loads(road_path.read_bytes())==road,"MAP_SOURCE_ROAD_REPORT_REPLAY_DIFFERS")
    else:write_new(road_path,road)
    gallery=road_quality.gallery(road_spec,road);html_path=root/"road-batch-review.html"
    if html_path.exists():common.need(html_path.read_text(encoding="utf-8")==gallery,"MAP_SOURCE_ROAD_GALLERY_REPLAY_DIFFERS")
    else:
        with html_path.open("x",encoding="utf-8") as stream:stream.write(gallery)
    decisions=candidate_decisions(request,seam,combined_run,combined_verify,road)
    final={"schema":REPORT_SCHEMA,"operation_id":request["operation_id"],
        "status":"AUDITED_RESTORE_CANDIDATES_READY" if any(x["decision"]=="RESTORE_CANDIDATE" for x in decisions) else "NO_RESTORE_CANDIDATES",
        "request":ref(root/"request.json"),"motion_run":ref(run_path),"motion_verify":ref(verify_path),
        "motion_batch_runs":run_refs,"motion_batch_verifications":verify_refs,
        "seam_audit":ref(seam_path),"road_batch_report":ref(road_path),"source_count":len(decisions),
        "restore_candidates":sum(x["decision"]=="RESTORE_CANDIDATE" for x in decisions),
        "redraw_required":sum(x["decision"]=="REDRAW_REQUIRED" for x in decisions),
        "review_required":sum(x["decision"]=="REVIEW_REQUIRED" for x in decisions),
        "decision_algorithm_sha256":sha_file(Path(__file__)),
        "decision_basis":["historical source SHA","exact rational parent grid","official crop/origin 3X receipt",
            "original opaque edge profiles and legal neighbor transforms","reviewed functional port topology",
            "painted shared interfaces and all applicable legal road pairs; missing evidence remains REVIEW_REQUIRED"],
        "source_rows":decisions,"art_approved":False,"game_runtime_accepted":False,"game_deployed":False}
    final_path=root/"map-source-batch-report.json"
    if final_path.exists() and json.loads(final_path.read_bytes())!=final:
        final_path=root/("map-source-batch-report-"+final["decision_algorithm_sha256"][:12]+".json")
    if final_path.exists():common.need(json.loads(final_path.read_bytes())==final,"MAP_SOURCE_REPORT_REPLAY_DIFFERS")
    else:write_new(final_path,final)
    return {"status":final["status"],"report":ref(final_path),"restore_candidates":final["restore_candidates"],
        "redraw_required":final["redraw_required"],"review_required":final["review_required"],"images_written":len(combined_run["results"])}


def stage_verify(root: Path, request: dict, exe: Path) -> dict:
    report_path=latest_decision_report(root)
    prior=json.loads(report_path.read_bytes())
    common.need(prior.get("decision_algorithm_sha256")==sha_file(Path(__file__)),"MAP_SOURCE_DECISION_ALGORITHM_CHANGED_RERUN")
    runs,verifications,run_refs,verify_refs,combined_run,combined_verify=collect_batch_runs(root,request,exe,"verify-motion.json")
    semantic=root/"verify-seam-audit.json"
    seam=json.loads(semantic.read_text(encoding="utf-8-sig")) if semantic.exists() else run_auditor(root,request,semantic)
    road=load_ref(prior["road_batch_report"])
    road_spec={"schema":road_quality.SCHEMA,"operation_id":request["operation_id"],
        "road_families":["COBBRD","DIRTRD","GRAVRD"],"native_inventory":request["native_inventory"],
        "editor_source_proof":request["editor_source_proof"],"sampling_runs":run_refs,
        "interface_contracts":request.get("interface_contracts",{})}
    current_road=road_quality.analyze(road_quality.freeze(road_spec))
    common.need(current_road==road,"MAP_SOURCE_ROAD_QUALITY_REPLAY_DIFFERS")
    replayed=candidate_decisions(request,seam,combined_run,combined_verify,road)
    common.need(replayed==prior["source_rows"],"MAP_SOURCE_DECISIONS_CHANGED_ON_REPLAY")
    for frame in seam.get("frames",[]):
        candidate=Path(frame["candidate_path"])
        common.need(candidate.is_file() and sha_file(candidate)==frame.get("candidate_sha256"),"MAP_SOURCE_3X_CANDIDATE_SHA_DRIFT")
    recorded=sum(x.get("decision")=="RESTORE_CANDIDATE" for x in replayed)
    out={"schema":"h3.map-source-batch.verify.v1","operation_id":request["operation_id"],"status":"VERIFIED",
        "decision_report":ref(report_path),
        "decision_algorithm_sha256":sha_file(Path(__file__)),
        "motion_verifications":verify_refs,"seam_audit":ref(semantic),"restore_candidates_replayed":recorded,
        "images_written":0,"art_approved":False,"game_runtime_accepted":False}
    path=root/"verification.json"
    if path.exists():common.need(json.loads(path.read_bytes())==out,"MAP_SOURCE_VERIFICATION_REPLAY_DIFFERS")
    else:write_new(path,out)
    return {"status":out["status"],"verification":ref(path),"images_written":0}


def stage_restore(root: Path, request: dict) -> dict:
    report_path = latest_decision_report(root)
    verification_path = root / "verification.json"
    common.need(report_path.is_file() and verification_path.is_file(), "MAP_SOURCE_VERIFY_BEFORE_RESTORE")
    report = json.loads(report_path.read_bytes());verification = json.loads(verification_path.read_bytes())
    common.need(verification.get("status") == "VERIFIED", "MAP_SOURCE_VERIFICATION_REQUIRED")
    common.need(report.get("decision_algorithm_sha256")==sha_file(Path(__file__))
        and verification.get("decision_algorithm_sha256")==sha_file(Path(__file__)),
        "MAP_SOURCE_DECISION_ALGORITHM_CHANGED_RERUN")
    common.need(verification.get("decision_report")==ref(report_path),"MAP_SOURCE_VERIFIED_REPORT_BINDING_DIFFERS")
    master_root = Path(request["master_root"]).resolve();results=[]
    for row in report["source_rows"]:
        if row["decision"] != "RESTORE_CANDIDATE": continue
        key = row["key"];source = Path(next(x["source"]["path"] for x in request["source_rows"] if x["key"] == key)).resolve()
        target = master_root / key[1] / f"{key[2]}_{key[3]}.png"
        raw = source.read_bytes();digest = hashlib.sha256(raw).hexdigest()
        common.need(digest == row["source_sha256"], "MAP_SOURCE_CHANGED_BEFORE_RESTORE")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            status = "ALREADY_IDENTICAL" if sha_file(target) == digest else "CONFLICT_NOT_OVERWRITTEN"
        else:
            with target.open("xb") as stream: stream.write(raw)
            common.need(sha_file(target) == digest, "MAP_SOURCE_RESTORE_SHA_MISMATCH")
            status = "RESTORED_BYTE_IDENTICAL"
        results.append({"key": key, "source": str(source), "source_sha256": digest,
                        "target": str(target.resolve()), "target_sha256": sha_file(target) if target.exists() else None,
                        "status": status})
    receipt = {"schema": "h3.map-source-batch.restore.v1", "operation_id": request["operation_id"],
        "verification": ref(verification_path), "master_root": str(master_root), "results": results,
        "restored": sum(x["status"] == "RESTORED_BYTE_IDENTICAL" for x in results),
        "conflicts": sum(x["status"] == "CONFLICT_NOT_OVERWRITTEN" for x in results),
        "source_pixels_resampled": False, "source_images_modified": False, "game_deployed": False}
    path = root / "restore-receipt.json"
    if path.exists(): common.need(json.loads(path.read_bytes()) == receipt, "MAP_SOURCE_RESTORE_REPLAY_DIFFERS")
    else: write_new(path, receipt)
    return {"status": "RESTORED" if not receipt["conflicts"] else "RESTORED_WITH_CONFLICTS",
            "restored": receipt["restored"], "conflicts": receipt["conflicts"], "receipt": ref(path)}


def main(argv=None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--operation-id", required=True);f.add_argument("--road-prepared", required=True)
    f.add_argument("--river-prepared", required=True);f.add_argument("--inventory", required=True)
    f.add_argument("--editor-proof", required=True);f.add_argument("--seam-auditor", required=True)
    f.add_argument("--release-root", required=True);f.add_argument("--master-root", required=True)
    for name in ("check", "run", "verify", "restore"):
        stage = sub.add_parser(name);stage.add_argument("--manifest", required=True);stage.add_argument("--manifest-sha256", required=True)
        stage.add_argument("--renderer", required=name!="restore");stage.add_argument("--renderer-sha256", required=name!="restore")
        stage.add_argument("--operation-id", required=True)
    # The companion injects renderer identity even when its required stage was
    # omitted. Preserve the machine error and the normal CLI usage contract.
    if raw_argv and raw_argv[0] == "--renderer":
        parser.print_usage(file=sys.stderr)
        print(json.dumps({"schema": "h3.map-source-batch.error.v1", "status": "BLOCKED",
                          "error": "action is required: freeze, check, run, verify, or restore"}, ensure_ascii=False), file=sys.stderr)
        return 2
    args = parser.parse_args(raw_argv)
    try:
        if args.action == "freeze":
            result = freeze(args.operation_id, Path(args.road_prepared), Path(args.river_prepared),
                Path(args.inventory), Path(args.editor_proof), Path(args.seam_auditor),
                Path(args.release_root), Path(args.master_root))
        else:
            root, request = pin_request({"path": args.manifest, "sha256": args.manifest_sha256}, args.operation_id)
            if args.action == "check": result = stage_check(root, request, invoking_exe(args))
            elif args.action == "run": result = stage_run(root, request, invoking_exe(args))
            elif args.action == "verify": result = stage_verify(root, request, invoking_exe(args))
            else: result = stage_restore(root, request)
        print(json.dumps(result, ensure_ascii=False));return 0
    except (ValueError, KeyError, OSError, TypeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"schema": "h3.map-source-batch.error.v1", "status": "BLOCKED",
                          "operation_id": getattr(args, "operation_id", None), "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
