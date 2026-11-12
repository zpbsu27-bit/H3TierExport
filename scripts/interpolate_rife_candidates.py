"""Optional, receipt-bound RIFE experiment; never a production slot exporter.

RGB and alpha are inferred separately. This is an approximate sprite adapter,
not alpha-aware learned flow: different inferred masks can cause halos. Every
output remains review-required. No source resampling, auto-trimming, loop or
native timing is inferred. The external MIT engine/weights stay outside Git.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import time

import numpy as np
from PIL import Image


AUTHORITY = {"art_approved": False, "human_approved": False,
             "native_timing_verified": False, "runtime_accepted": False,
             "game_deployed": False, "model_native_output": False}
DEFAULT_BATCH_TIMEOUT_SECONDS = 600
MAX_BATCH_TIMEOUT_SECONDS = 3600
MAX_INFERENCE_TIMEOUT_SECONDS = 120


class PreflightRejected(ValueError):
    """No output directory or inference was created by this attempt."""


class InferenceFailure(RuntimeError):
    """One model process failed, with the process evidence retained."""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


class BatchDeadlineExpired(TimeoutError):
    """The total batch budget expired before another inference could start."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pinned_file(path: str, expected: str, label: str) -> Path:
    if not isinstance(path, str) or not isinstance(expected, str):
        raise ValueError(f"{label}: path and SHA256 strings required")
    result = Path(path).resolve(strict=True)
    if not result.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise ValueError(f"{label}: file and SHA256 required")
    if sha256(result) != expected:
        raise ValueError(f"{label}: SHA256 mismatch")
    return result


def integer_pair(value: object, label: str, minimum: int) -> list[int]:
    if not isinstance(value, list) or len(value) != 2 or any(
        type(v) is not int or v < minimum for v in value
    ):
        raise ValueError(f"{label}: invalid dimensions/position")
    return value


def read_json(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("JSON exceeds 16 MiB")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def read_frame(spec: dict, canvas: list[int], label: str) -> dict:
    if not isinstance(spec, dict):
        raise ValueError(f"{label}: frame object required")
    key = spec.get("key")
    if (not isinstance(key, list) or len(key) != 4
            or key[0] not in ("def", "synthetic")
            or not isinstance(key[1], str) or not 1 <= len(key[1]) <= 80
            or any(type(v) is not int or v < 0 for v in key[2:])):
        raise ValueError(f"{label}.key: original slot identity required")
    path = pinned_file(spec["path"], spec["sha256"], label)
    pixels = integer_pair(spec["pixels"], label + ".pixels", 1)
    origin = integer_pair(spec["origin_pixels"], label + ".origin_pixels", 0)
    if any(origin[i] + pixels[i] > canvas[i] for i in range(2)):
        raise ValueError(f"{label}: crop exceeds canvas")
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode != "RGBA" or list(image.size) != pixels:
            raise ValueError(f"{label}: exact RGBA PNG geometry required")
        rgba = np.asarray(image).copy()
    if key[0] != "synthetic" and "motion_receipt" not in spec:
        raise ValueError(f"{label}: original H3 slot requires a pinned motion receipt")
    if "motion_receipt" in spec:
        receipt_path = pinned_file(spec["motion_receipt"], spec["receipt_sha256"], label + ".receipt")
        receipt = read_json(receipt_path)
        contract = receipt.get("contract")
        if not isinstance(contract, dict):
            raise ValueError(f"{label}: motion receipt contract must be an object")
        entry = contract.get("entry")
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: motion receipt entry must be an object")
        targets = entry.get("targets")
        if not isinstance(targets, dict):
            raise ValueError(f"{label}: motion receipt targets must be an object")
        if (receipt.get("schema") != "h3.motion.receipt.v1"
                or receipt.get("png_sha256") != spec["sha256"]
                or Path(receipt.get("output", "")).resolve() != path
                or receipt.get("output_pixels") != pixels
                or receipt.get("origin_3x") != origin
                or targets.get("canvas_3x") != canvas
                or entry.get("key") != spec.get("key")):
            raise ValueError(f"{label}: motion receipt geometry/key mismatch")
    return {"spec": spec, "path": path, "rgba": rgba,
            "origin": origin, "pixels": pixels}


def validate_batch(path: Path, digest: str) -> tuple[dict, list[dict]]:
    batch_path = pinned_file(str(path), digest, "batch")
    value = read_json(batch_path)
    if value.get("schema") != "h3.interpolation.batch.v1":
        raise ValueError("unsupported batch schema")
    engine = value["engine"]
    if not isinstance(engine, dict):
        raise ValueError("engine: object required")
    pinned_file(engine["executable"], engine["sha256"], "engine")
    model = Path(engine["model_dir"]).resolve(strict=True)
    if model.name != "rife-v4.6":
        raise ValueError("this experiment requires pinned rife-v4.6 arbitrary-time model")
    if not isinstance(engine["model_files"], dict) or set(engine["model_files"]) != {"flownet.bin", "flownet.param"}:
        raise ValueError("model: both fixed model files required")
    for name, expected in engine["model_files"].items():
        pinned_file(str(model / name), expected, "model." + name)
    pairs = value["pairs"]
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 32:
        raise ValueError("batch requires 1..32 pairs")
    loaded, identities = [], set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError("pair: object required")
        identity = pair["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or identity in identities:
            raise ValueError("pair.id: invalid or duplicate")
        identities.add(identity)
        canvas = integer_pair(pair["canvas_pixels"], identity + ".canvas", 1)
        if max(canvas) > 8192 or canvas[0] * canvas[1] > 32 * 1024 * 1024:
            raise ValueError("canvas exceeds bounded experiment size")
        times = pair["times"]
        if (not isinstance(times, list) or not 1 <= len(times) <= 7
                or any(type(t) not in (int, float) or not 0 < t < 1 for t in times)
                or len(set(times)) != len(times)):
            raise ValueError("times: unique finite values strictly between 0 and 1 required")
        # All manifests and PNGs preflight before writing or executing any job.
        item = {"spec": pair, "a": read_frame(pair["a"], canvas, identity + ".a"),
                "b": read_frame(pair["b"], canvas, identity + ".b")}
        if pair["a"]["key"][:3] != pair["b"]["key"][:3]:
            raise ValueError("a/b must belong to the same original resource/action group")
        if "truth" in pair:
            truth = pair["truth"]
            if not isinstance(truth, dict):
                raise ValueError("truth: frame object required")
            if truth.get("time") not in times:
                raise ValueError("truth.time must match an explicitly requested sample")
            item["truth"] = read_frame(truth, canvas, identity + ".truth")
            if truth["key"][:3] != pair["a"]["key"][:3]:
                raise ValueError("truth must belong to the same original resource/action group")
            if truth["key"] in (pair["a"]["key"], pair["b"]["key"]):
                raise ValueError("truth must be a distinct held-out frame, not an endpoint")
        loaded.append(item)
    return value, loaded


def resolve_batch_timeout(value: dict, requested_seconds: int | None) -> tuple[int, int | None]:
    manifest_limit = value.get("max_wall_seconds")
    if "max_wall_seconds" in value and (
        type(manifest_limit) is not int or not 1 <= manifest_limit <= MAX_BATCH_TIMEOUT_SECONDS
    ):
        raise ValueError(f"max_wall_seconds must be an integer from 1 to {MAX_BATCH_TIMEOUT_SECONDS}")
    if requested_seconds is not None and (
        type(requested_seconds) is not int or not 1 <= requested_seconds <= MAX_BATCH_TIMEOUT_SECONDS
    ):
        raise ValueError(f"--timeout-seconds must be an integer from 1 to {MAX_BATCH_TIMEOUT_SECONDS}")
    effective = DEFAULT_BATCH_TIMEOUT_SECONDS if requested_seconds is None else requested_seconds
    if manifest_limit is not None and requested_seconds is None:
        effective = min(effective, manifest_limit)
    elif manifest_limit is not None and effective > manifest_limit:
        raise ValueError("--timeout-seconds exceeds manifest max_wall_seconds")
    return effective, manifest_limit


def common_roi(a: dict, b: dict, canvas: list[int], margin: int = 16) -> list[int]:
    # Union, never intersection: no endpoint content may be discarded.
    x = max(0, min(a["origin"][0], b["origin"][0]) - margin)
    y = max(0, min(a["origin"][1], b["origin"][1]) - margin)
    right = min(canvas[0], max(f["origin"][0] + f["pixels"][0] for f in (a, b)) + margin)
    bottom = min(canvas[1], max(f["origin"][1] + f["pixels"][1] for f in (a, b)) + margin)
    return [x, y, right - x, bottom - y]


def place_roi(frame: dict, roi: list[int]) -> np.ndarray:
    x, y, w, h = roi
    result = np.zeros((h, w, 4), dtype=np.uint8)
    left, top = frame["origin"][0] - x, frame["origin"][1] - y
    if left < 0 or top < 0 or left + frame["pixels"][0] > w or top + frame["pixels"][1] > h:
        raise ValueError("source outside ROI")
    result[top:top + frame["pixels"][1], left:left + frame["pixels"][0]] = frame["rgba"]
    return result


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    return np.where(value <= .04045, value / 12.92, ((value + .055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0., 1.)
    return np.where(value <= .0031308, value * 12.92, 1.055 * value ** (1. / 2.4) - .055)


def split_inputs(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.
    premult = srgb_to_linear(rgba[:, :, :3].astype(np.float32) / 255.) * alpha
    black = np.rint(linear_to_srgb(premult) * 255.).astype(np.uint8)
    return black, np.repeat(rgba[:, :, 3:4], 3, axis=2)


def recombine(black_rgb: np.ndarray, alpha_rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    if black_rgb.shape != alpha_rgb.shape or black_rgb.ndim != 3 or black_rgb.shape[2] != 3:
        raise ValueError("RGB/alpha output shape mismatch")
    alpha = alpha_rgb.astype(np.float32).mean(axis=2, keepdims=True) / 255.
    premult = srgb_to_linear(black_rgb.astype(np.float32) / 255.)
    # Source mask is not cleaned. Clamping straight color is a representation bound,
    # and the amount of invalid premult color is exposed for review.
    visible = alpha[:, :, 0] > 0
    overshoot = np.any(premult > alpha + 2. / 255., axis=2)
    outside_energy = np.any(black_rgb > 1, axis=2) & ~visible
    straight = np.divide(premult, alpha, out=np.zeros_like(premult), where=alpha > 0)
    rgba = np.concatenate([np.rint(linear_to_srgb(straight) * 255.), np.rint(alpha * 255.)], axis=2).astype(np.uint8)
    return rgba, {
        "split_mask_is_approximate": True,
        "premult_color_exceeds_alpha_pixels": int(overshoot.sum()),
        "rgb_energy_outside_inferred_alpha_pixels": int(outside_energy.sum()),
        "alpha_channel_spread_max": int(np.ptp(alpha_rgb.astype(np.int16), axis=2).max()),
        "alpha1_pixels_preserved": int((rgba[:, :, 3] == 1).sum()),
    }


def crossfade(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    aa, ba = a[:, :, 3:4] / 255., b[:, :, 3:4] / 255.
    alpha = aa * (1 - t) + ba * t
    premult = srgb_to_linear(a[:, :, :3] / 255.) * aa * (1 - t) + srgb_to_linear(b[:, :, :3] / 255.) * ba * t
    rgb = np.divide(premult, alpha, out=np.zeros_like(premult), where=alpha > 0)
    return np.concatenate([np.rint(linear_to_srgb(rgb) * 255.), np.rint(alpha * 255.)], axis=2).astype(np.uint8)


def _tail_text(value: bytes | str | None, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-limit:]
    return str(value)[-limit:]


def inference(engine: dict, inputs: tuple[Path, Path], output: Path, t: float,
              timeout_seconds: float = MAX_INFERENCE_TIMEOUT_SECONDS) -> dict:
    command = [engine["executable"], "-0", str(inputs[0]), "-1", str(inputs[1]),
               "-o", str(output), "-s", format(t, ".9g"), "-m", engine["model_dir"],
               "-g", str(engine.get("gpu_id", 0)), "-j", "1:1:1"]
    started = time.monotonic()
    evidence = {"command": command, "timeout_seconds": timeout_seconds,
                "process_started": False, "process_completed": False,
                "timed_out": False, "output": str(output)}
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout_seconds, shell=False)
    except subprocess.TimeoutExpired as error:
        evidence.update({"process_started": True, "process_completed": False,
                         "timed_out": True, "status": "TIMED_OUT",
                         "wall_seconds": round(time.monotonic() - started, 4),
                         "stdout_tail": _tail_text(error.stdout), "stderr_tail": _tail_text(error.stderr)})
        raise InferenceFailure(f"RIFE exceeded its {timeout_seconds:g}s process budget", evidence) from error
    except OSError as error:
        evidence.update({"status": "LAUNCH_FAILED", "error": str(error)[:2000],
                         "wall_seconds": round(time.monotonic() - started, 4)})
        raise InferenceFailure(f"RIFE process could not start: {error}", evidence) from error
    evidence.update({"process_started": True, "process_completed": True,
                     "exit_code": completed.returncode, "status": "COMPLETED",
                     "wall_seconds": round(time.monotonic() - started, 4),
                     "stdout_tail": _tail_text(completed.stdout),
                     "stderr_tail": _tail_text(completed.stderr)})
    if completed.returncode != 0:
        evidence["status"] = "EXIT_NONZERO"
        raise InferenceFailure(f"RIFE failed exit={completed.returncode}: {evidence['stderr_tail']}", evidence)
    if not output.is_file():
        evidence["status"] = "OUTPUT_MISSING"
        raise InferenceFailure("RIFE exited successfully without creating its output", evidence)
    evidence.update({"output_bytes": output.stat().st_size, "output_sha256": sha256(output)})
    return evidence


def metric(candidate: np.ndarray, truth: np.ndarray) -> dict:
    visible = (candidate[:, :, 3] > 16) | (truth[:, :, 3] > 16)
    intersection = (candidate[:, :, 3] > 16) & (truth[:, :, 3] > 16)
    ap, bp = candidate[:, :, 3:4] / 255., truth[:, :, 3:4] / 255.
    mae = np.abs(srgb_to_linear(candidate[:, :, :3] / 255.) * ap - srgb_to_linear(truth[:, :, :3] / 255.) * bp)
    return {"alpha_iou_at_16": float(intersection.sum() / max(1, visible.sum())),
            "linear_premult_rgb_mae_visible_union": float(mae[visible].mean()) if visible.any() else 0.,
            "alpha_mae_visible_union": float(np.abs(ap - bp)[:, :, 0][visible].mean()) if visible.any() else 0.,
            "truth_is_heldout_pose_not_proof_of_linear_timing": True}


def full_canvas(frame: dict, canvas: list[int]) -> np.ndarray:
    result = np.zeros((canvas[1], canvas[0], 4), dtype=np.uint8)
    x, y = frame["origin"]
    w, h = frame["pixels"]
    result[y:y + h, x:x + w] = frame["rgba"]
    return result


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_review(report: dict, loaded: list[dict], outdir: Path) -> None:
    """Refresh only our review HTML/held-out thumbnails, without inference."""
    sections = []
    for item in loaded:
        pair = item["spec"]
        rows = [r for r in report["results"] if r["id"] == pair["id"]]
        roi = rows[0]["roi"]
        def view(name: str, caption: str) -> str:
            return f'<figure><img data-native-width="{roi[2]}" src="{pair["id"]}/{name}"><figcaption>{html.escape(caption)}</figcaption></figure>'
        views = [view("a.png", "A · 原注册点")]
        if "truth" in item:
            # Ground truth is never fed to the model; cropped thumbnail is review-only.
            truth = full_canvas(item["truth"], pair["canvas_pixels"])
            x, y, w, h = roi
            Image.fromarray(truth[y:y + h, x:x + w]).save(outdir / pair["id"] / "heldout-review.png")
            views.append(view("heldout-review.png", "留出的已有中间姿态 · 仅评价，原时序未核验"))
        for index, row in enumerate(rows):
            if row["status"] != "CANDIDATE_REVIEW_REQUIRED":
                continue
            views += [view(f"t{index:02d}-candidate.png", f'RIFE t={row["time"]:g} · 待审'),
                      view(f"t{index:02d}-crossfade.png", f'淡入淡出对照 t={row["time"]:g}')]
        views.append(view("b.png", "B · 原注册点"))
        sections.append(f'<section><h2>{html.escape(pair["id"])}</h2><p>ROI {roi}；原画布 {pair["canvas_pixels"]}，端点未缩放、未改锚点。</p><div class="frames">{"".join(views)}</div></section>')
    controls = '<button onclick="size(1)">实际像素</button> <button onclick="size(3)">3X素材按游戏1X显示</button> <button onclick="bg(0)">棋盘底</button> <button onclick="bg(1)">黑底</button> <button onclick="bg(2)">白底</button>'
    script = '<script>function size(s){document.querySelectorAll("img[data-native-width]").forEach(e=>e.style.width=(Number(e.dataset.nativeWidth)/s)+"px")}function bg(n){document.querySelectorAll("figure").forEach(e=>e.style.background=n===0?"":""+(n===1?"#000":"#fff"))}</script>'
    (outdir / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>H3 中间帧候选试验</title><style>body{background:#202b35;color:#eef2f4;font:16px sans-serif;margin:24px}section{border-top:1px solid #687889;padding:16px 0}.frames{display:flex;gap:12px;flex-wrap:wrap}figure{margin:0;padding:8px;background:repeating-conic-gradient(#44515e 0% 25%,#35414d 0% 50%) 0/16px 16px}img{max-width:none}figcaption{background:#202b35;padding:8px;max-width:300px}</style><h1>H3 中间帧候选</h1><p>RGB/alpha 独立推理，透明边缘近似；全部待审。此试验不证明美术、原版时序或实机接收；原始 PNG 未改。</p>' + controls + ''.join(sections) + script, encoding="utf-8")


def execute(batch_path: Path, batch_sha256: str, outdir: Path, *,
            timeout_seconds: int | None = None, runner=inference,
            clock=time.monotonic) -> dict:
    # Freeze/validate before any output or subprocess, including the held-out source.
    batch_started = clock()
    try:
        value, loaded = validate_batch(batch_path, batch_sha256)
        timeout_seconds, manifest_limit = resolve_batch_timeout(value, timeout_seconds)
        if clock() - batch_started >= timeout_seconds:
            raise PreflightRejected("batch time budget expired during input preflight; no output was created")
        outdir = outdir.resolve()
        if outdir.exists():
            raise ValueError("outdir already exists; refuse replacement/resume ambiguity")
    except (ValueError, KeyError, OSError, TypeError) as error:
        raise PreflightRejected(str(error)) from error
    outdir.mkdir(parents=True)
    report = {"schema": "h3.interpolation.run.v1", "batch": str(batch_path.resolve()),
              "batch_sha256": batch_sha256, "engine": value["engine"],
              "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "algorithm": "rife-v4.6-independent-rgb-alpha-linear-premult-adapter-v1",
              "batch_timeout_seconds": timeout_seconds,
              "manifest_max_wall_seconds": manifest_limit,
              "batch_timed_out": False,
              "model_inference_performed": False,
              "model_inference_start_unknown": False,
              **AUTHORITY, "results": []}
    review_loaded = []
    stop_batch = False
    for item_index, item in enumerate(loaded):
        pair, a, b = item["spec"], item["a"], item["b"]
        roi = common_roi(a, b, pair["canvas_pixels"])
        if stop_batch:
            for index, t in enumerate(pair["times"]):
                result = _sample_result(item, t, roi, "NOT_STARTED_BATCH_TIMEOUT", [])
                report["results"].append(result)
            continue
        ar, br = place_roi(a, roi), place_roi(b, roi)
        folder = outdir / pair["id"]
        folder.mkdir()
        review_loaded.append(item)
        for name, pixels in (("a", ar), ("b", br)):
            Image.fromarray(pixels).save(folder / f"{name}.png")
            black, alpha = split_inputs(pixels)
            Image.fromarray(black).save(folder / f"{name}-rgb.png")
            Image.fromarray(alpha).save(folder / f"{name}-alpha.png")
        for index, t in enumerate(pair["times"]):
            label = f"t{index:02d}"
            calls = []
            if stop_batch or clock() - batch_started >= timeout_seconds:
                result = _sample_result(item, t, roi, "NOT_STARTED_BATCH_TIMEOUT", calls)
                report["results"].append(result)
                write_json(folder / f"{label}-receipt.json", result)
                report["batch_timed_out"] = True
                stop_batch = True
                continue
            try:
                for channel in ("rgb", "alpha"):
                    remaining = timeout_seconds - (clock() - batch_started)
                    if remaining <= 0:
                        raise BatchDeadlineExpired("total batch time budget expired before the next inference")
                    process_timeout = min(MAX_INFERENCE_TIMEOUT_SECONDS, remaining)
                    output = folder / f"{label}-{channel}.png"
                    call = {"channel": channel, "process_started": None,
                            "process_completed": False, "timed_out": False,
                            "status": "INVOKING", "timeout_seconds": process_timeout}
                    calls.append(call)
                    try:
                        evidence = runner(value["engine"],
                                          (folder / f"a-{channel}.png", folder / f"b-{channel}.png"),
                                          output, t, timeout_seconds=process_timeout)
                    except InferenceFailure as error:
                        call.update(error.evidence)
                        call["channel"] = channel
                        call["error"] = str(error)[:4000]
                        raise
                    except subprocess.TimeoutExpired as error:
                        call.update({"process_started": True, "process_completed": False,
                                     "timed_out": True, "status": "TIMED_OUT",
                                     "stdout_tail": _tail_text(error.stdout),
                                     "stderr_tail": _tail_text(error.stderr),
                                     "error": str(error)[:4000]})
                        raise InferenceFailure("inference runner exceeded its process budget", call) from error
                    except (OSError, RuntimeError, ValueError) as error:
                        call.update({"status": "FAILED_START_UNKNOWN", "error": str(error)[:4000]})
                        raise
                    if isinstance(evidence, dict):
                        call.update(evidence)
                    else:
                        call.update({"status": "COMPLETED_EVIDENCE_MISSING"})
                    call["channel"] = channel
                with Image.open(folder / f"{label}-rgb.png") as raw:
                    rgb = np.asarray(raw.convert("RGB")).copy()
                with Image.open(folder / f"{label}-alpha.png") as raw:
                    alpha = np.asarray(raw.convert("RGB")).copy()
                if rgb.shape != ar[:, :, :3].shape or alpha.shape != rgb.shape:
                    raise ValueError("engine resized output; exact ROI required")
                candidate, diagnostics = recombine(rgb, alpha)
                candidate_path = folder / f"{label}-candidate.png"
                baseline = crossfade(ar, br, t)
                Image.fromarray(candidate).save(candidate_path)
                Image.fromarray(baseline).save(folder / f"{label}-crossfade.png")
                result = _sample_result(item, t, roi, "CANDIDATE_REVIEW_REQUIRED", calls)
                result.update({"output": str(candidate_path),
                               "png_sha256": sha256(candidate_path), "diagnostics": diagnostics, "inferences": calls})
                if "truth" in item and pair["truth"]["time"] == t:
                    canvas = pair["canvas_pixels"]
                    truth = full_canvas(item["truth"], canvas)
                    model = full_canvas({"origin": roi[:2], "pixels": roi[2:], "rgba": candidate}, canvas)
                    fade = full_canvas({"origin": roi[:2], "pixels": roi[2:], "rgba": baseline}, canvas)
                    result["heldout_pose_metrics"] = {"rife": metric(model, truth), "crossfade": metric(fade, truth)}
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                batch_expired = (isinstance(error, BatchDeadlineExpired)
                                 or clock() - batch_started >= timeout_seconds)
                result = _sample_result(item, t, roi,
                                        "BATCH_TIMEOUT" if batch_expired else "BACKEND_FAILED", calls)
                result["error"] = str(error)[:4000]
                # No hidden retry or fallback; the independent samples continue.
                if batch_expired:
                    report["batch_timed_out"] = True
                    stop_batch = True
            write_json(folder / f"{label}-receipt.json", result)
            report["results"].append(result)
    failures = sum(r["status"] in ("BACKEND_FAILED", "BATCH_TIMEOUT") for r in report["results"])
    not_started = sum(r["status"] == "NOT_STARTED_BATCH_TIMEOUT" for r in report["results"])
    candidates = sum(r["status"] == "CANDIDATE_REVIEW_REQUIRED" for r in report["results"])
    all_calls = [call for result in report["results"] for call in result.get("inferences", [])]
    report.update({"failed": failures, "not_started": not_started, "candidates": candidates,
                   "inference_calls": len(all_calls),
                   "model_inference_performed": any(call.get("process_started") is True for call in all_calls),
                   "model_inference_start_unknown": any(call.get("process_started") is None for call in all_calls),
                   "elapsed_wall_seconds": round(clock() - batch_started, 4),
                   "status": "COMPLETED_REVIEW_REQUIRED" if candidates == len(report["results"])
                   else "PARTIAL" if candidates else "FAILED"})
    write_json(outdir / "report.json", report)
    render_review(report, review_loaded, outdir)
    report["elapsed_wall_seconds"] = round(clock() - batch_started, 4)
    write_json(outdir / "report.json", report)
    return report


def _sample_result(item: dict, t: float, roi: list[int], status: str, calls: list[dict]) -> dict:
    pair = item["spec"]
    return {"id": pair["id"], "time": t, "roi": roi,
            "canvas_pixels": pair["canvas_pixels"],
            "sources": {"a": pair["a"], "b": pair["b"]},
            "status": status, "inferences": calls,
            "model_inference_performed": any(call.get("process_started") is True for call in calls),
            "model_inference_start_unknown": any(call.get("process_started") is None for call in calls),
            **AUTHORITY}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--batch-sha256", required=True)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int,
                        help=f"total wall-clock budget (default {DEFAULT_BATCH_TIMEOUT_SECONDS}s; max {MAX_BATCH_TIMEOUT_SECONDS}s; manifest max_wall_seconds may lower it)")
    args = parser.parse_args()
    try:
        report = execute(args.batch, args.batch_sha256, args.outdir,
                         timeout_seconds=args.timeout_seconds)
    except PreflightRejected as error:
        print(json.dumps({"status": "PREFLIGHT_REJECTED", "error": str(error)}, ensure_ascii=False))
        return 2
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"status": "RUN_FAILED", "error": str(error),
                          "outdir": str(args.outdir.resolve()),
                          "partial_artifacts_may_exist": args.outdir.exists(),
                          "retry_requires_inspecting_existing_artifacts": True}, ensure_ascii=False))
        return 2
    print(json.dumps({k: report[k] for k in ("status", "candidates", "failed", "not_started",
                                               "batch_timeout_seconds", "elapsed_wall_seconds",
                                               "model_inference_performed", "runtime_accepted")}, ensure_ascii=False))
    return 0 if report["status"] == "COMPLETED_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
