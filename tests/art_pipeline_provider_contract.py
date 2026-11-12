"""Provider lock, catalogue, CLI forwarding and output-boundary contracts."""

from __future__ import annotations

import contextlib
import importlib.util
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    "art_pipeline_provider",
    PRODUCT / "scripts" / "art_pipeline_provider.py",
)
provider = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(provider)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(callable_, token: str) -> str:
    try:
        callable_()
    except provider.ProviderError as error:
        text = str(error)
        require(token in text, f"expected {token!r}; got {text!r}")
        return text
    raise AssertionError(f"expected ProviderError containing {token!r}")


def test_source_hash_drift_refusal() -> None:
    source_root = provider.DEFAULT_PROVIDER_ROOT
    lock = provider.load_lock(source_root)
    require(lock["commit"] == "726856e58e1f219a897823c24616f158f016a103", "wrong pinned upstream commit")
    require(lock["license"] == "GPL-2.0-or-later", "GPL provenance not retained")
    integrity = provider.verify_source_tree(source_root)
    require(integrity["passed"] and integrity["actual_files"] == 152, "provider source tree not fully locked")
    with tempfile.TemporaryDirectory(prefix="h3-art-provider-contract-") as temporary:
        copied = Path(temporary) / "provider"
        shutil.copytree(source_root, copied)
        changed = copied / "upstream" / "creature-art" / "def_extract.py"
        changed.write_bytes(changed.read_bytes() + b"\n# drift fixture\n")
        expect_error(lambda: provider.verify_source_tree(copied), "SOURCE_INTEGRITY_FAILURE")
        calls = []
        expect_error(lambda: provider.execute(
            "creature-art.def_extract.info", ["C:/data/H3sprite.lod", "CREATURE.DEF"], copied,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0)),
            "SOURCE_INTEGRITY_FAILURE")
        require(not calls, "changed upstream source reached a child process")

        self_consistent = Path(temporary) / "self-consistent-provider"
        shutil.copytree(source_root, self_consistent)
        source_file = self_consistent / "upstream" / "README.md"
        source_file.write_bytes(source_file.read_bytes() + b"\nattacker changed both lock and source\n")
        lock_file = self_consistent / "source-lock.json"
        changed_lock = json.loads(lock_file.read_text(encoding="utf-8"))
        entry = next(item for item in changed_lock["files"] if item["path"] == "README.md")
        entry["bytes"] = source_file.stat().st_size
        entry["sha256"] = hashlib.sha256(source_file.read_bytes()).hexdigest()
        lock_file.write_text(json.dumps(changed_lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expect_error(lambda: provider.verify_source_tree(self_consistent), "SOURCE_LOCK_HASH_MISMATCH")


def test_argument_forwarding_is_argv_and_preserves_exit_code() -> None:
    calls = []
    child = ["C:/H3 Data with spaces/H3sprite.lod", "CREATURE.DEF", "--anchor"]

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=17, stdout="", stderr="")

    root = provider.provider_root(None)
    code = provider.main([
        "run", "creature-art.def_extract.info", "--provider-root", str(root),
        "--python", sys.executable, "--", *child,
    ], runner=fake_runner)
    require(code == 17, f"child exit code was not forwarded: {code}")
    require(len(calls) == 1, f"unexpected child invocation count: {len(calls)}")
    argv, kwargs = calls[0]
    source = root / "upstream" / "creature-art" / "def_extract.py"
    require(argv == [str(Path(sys.executable).resolve()), str(source.resolve()), "info", *child],
            f"child argv changed: {argv!r}")
    require(kwargs.get("shell") is False, "subprocess shell mode must stay disabled")
    require(kwargs.get("cwd") is None, "read-only command unexpectedly selected a write directory")
    require(kwargs.get("timeout") == provider.DEFAULT_RUN_TIMEOUT_SECONDS, "child deadline was not explicit")
    require(kwargs["env"].get("PYTHONDONTWRITEBYTECODE") == "1", "upstream source can gain __pycache__ files")


def test_frozen_provider_refuses_live_override_and_never_adds_cache_files() -> None:
    live = provider.DEFAULT_PROVIDER_ROOT
    with tempfile.TemporaryDirectory(prefix="h3-frozen-provider-") as temporary:
        payload = Path(temporary) / "payload"
        packaged = payload / "providers/h3-art-pipeline"
        shutil.copytree(live, packaged)
        require(provider.provider_root(packaged) == packaged.resolve(), "candidate explicit provider root changed")
        before = {p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in payload.rglob("*") if p.is_file()}
        directories = {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_dir()}
        calls = []
        with mock.patch.object(provider, "runtime_resource_path", return_value=packaged), \
                mock.patch.object(provider, "runtime_frozen_root", return_value=payload):
            require(provider.provider_root(None) == packaged.resolve(), "formal provider did not use packaged tree")
            integrity = provider.verify_source_tree(packaged)
            require(integrity["passed"] and integrity["actual_files"] == 152, "packaged source lock changed")
            expect_error(lambda: provider.provider_root(live), "FROZEN_PROVIDER_ROOT_OVERRIDE_REJECTED")
            expect_error(lambda: provider.execute(
                "creature-art.validate_creature_animation", ["--help"], live,
                runner=lambda *args, **kwargs: calls.append((args, kwargs))),
                "FROZEN_PROVIDER_ROOT_OVERRIDE_REJECTED")
            require(not calls, "a foreign provider reached a child process")

            def real_child(argv, **kwargs):
                require(kwargs["env"].get("PYTHONDONTWRITEBYTECODE") == "1", "secondary Python lost cache protection")
                result = provider.subprocess.run(argv, **kwargs, capture_output=True, text=True, encoding="utf-8")
                require(result.returncode == 0, result.stdout + result.stderr)
                return result

            # This existing read-only CLI imports its sibling def_extract.py;
            # the real secondary Python must leave the entire payload unchanged.
            code = provider.execute("creature-art.validate_creature_animation", ["--help"], packaged,
                                    python=sys.executable, runner=real_child)
            require(code == 0, "packaged read-only provider failed")
            require(before == {p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in payload.rglob("*") if p.is_file()}, "provider changed payload files")
            require(directories == {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_dir()},
                    "provider added a cache or other directory")
            (packaged / "source-lock.json").unlink()
            expect_error(lambda: provider.provider_root(None), "PROVIDER_LAYOUT_INVALID")


def test_candidate_explicit_provider_root_does_not_require_the_default_resource() -> None:
    with mock.patch.object(provider, "runtime_frozen_root", return_value=None), \
            mock.patch.object(provider, "runtime_resource_path", side_effect=RuntimeError("DEFAULT_RESOURCE_UNAVAILABLE")) as lookup:
        root = provider.provider_root(provider.DEFAULT_PROVIDER_ROOT)
        require(provider.verify_source_tree(root)["passed"], "candidate explicit provider lost its original source lock")
        lookup.assert_not_called()


def test_missing_dependency_is_reported_clearly() -> None:
    def missing_probe(argv, **kwargs):
        require(kwargs.get("shell") is False, "dependency probe must not use shell")
        require(kwargs.get("timeout") == provider.DEPENDENCY_PROBE_TIMEOUT_SECONDS,
                "dependency probe deadline must be 20 seconds")
        return SimpleNamespace(returncode=1, stdout="MISSING_MODULES:PIL", stderr="")

    expect_error(lambda: provider._probe_modules(
        sys.executable, "python", ["PIL"], runner=missing_probe), "DEPENDENCY_CHECK_FAILED")

    def stalled_probe(argv, **kwargs):
        raise provider.subprocess.TimeoutExpired(argv, kwargs["timeout"])

    expect_error(lambda: provider._probe_modules(
        sys.executable, "python", ["PIL"], runner=stalled_probe), "DEPENDENCY_PROBE_FAILED_TIMEOUT")


def test_blender_stage_real_paths_and_output_isolation() -> None:
    old_development_root = provider.DEVELOPMENT_OUTPUT_ROOT
    try:
        with tempfile.TemporaryDirectory(prefix="h3-art-blender-stage-") as temporary:
            root = Path(temporary).resolve()
            provider.DEVELOPMENT_OUTPUT_ROOT = root / "dev-output"
            provider.DEVELOPMENT_OUTPUT_ROOT.mkdir()
            input_root = provider.DEVELOPMENT_OUTPUT_ROOT / "inputs"
            input_root.mkdir()
            model = input_root / "creature.glb"
            scene = input_root / "binding.blend"
            model.write_bytes(b"synthetic model fixture")
            scene.write_bytes(b"synthetic blender scene fixture")
            model_hash = model.read_bytes()
            scene_hash = scene.read_bytes()
            fake_blender = root / "blender-test.exe"
            fake_blender.write_bytes(b"executable placeholder; subprocess is stubbed")
            work_root = provider.DEVELOPMENT_OUTPUT_ROOT / "runs" / "review-01"
            review_output = work_root / "review"
            binding_output = work_root / "binding"
            calls = []

            def fake_runner(argv, **kwargs):
                calls.append((list(argv), kwargs))
                require(argv.count("--python-expr") == 1, "Blender must disable bytecode in its own interpreter")
                require(argv.index("--python-expr") + 2 == argv.index("--python"),
                        "Blender bytecode protection must execute before the locked provider script")
                return SimpleNamespace(returncode=29, stdout="", stderr="")

            code = provider.execute(
                "creature-art.bootstrap_review",
                [f"--model={model}", f"--out={review_output}", "--height", "1.8", "--samples", "8"],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=work_root,
                timeout_seconds=91, runner=fake_runner)
            require(code == 29, "bootstrap-review child exit code was not propagated")
            require(calls[-1][0][-len(["--", "--model", str(model), "--out", str(review_output), "--height", "1.8", "--samples", "8"]):]
                    == ["--", "--model", str(model), "--out", str(review_output), "--height", "1.8", "--samples", "8"],
                    f"path flag equals form was not normalized: {calls[-1][0]}")
            require(calls[-1][1].get("timeout") == 91 and calls[-1][1].get("shell") is False,
                    "Blender run must use explicit deadline and argv mode")
            require(calls[-1][1].get("cwd") == str(work_root), "Blender cwd escaped the explicit work root")
            require(Path(calls[-1][0][calls[-1][0].index("--python") + 1]) ==
                    provider.DEFAULT_PROVIDER_ROOT / "upstream/creature-art/bootstrap_review.py",
                    "Blender bootstrap changed the locked provider script")
            require(not review_output.exists(), "fake child unexpectedly produced an output")
            require(model.read_bytes() == model_hash, "input model changed during provider invocation")

            code = provider.execute(
                "creature-art.proxy_skin",
                ["--source", str(scene), "--body", "Detailed Body", "--armature", "Rig", "--out", str(binding_output)],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=work_root,
                timeout_seconds=92, runner=fake_runner)
            require(code == 29, "proxy-skin child exit code was not propagated")
            require(scene.read_bytes() == scene_hash, "input Blender scene changed during provider invocation")
            require(not binding_output.exists(), "fake child unexpectedly produced a binding output")
            require("--body" in calls[-1][0] and "Detailed Body" in calls[-1][0],
                    "mesh object identifier was not forwarded as argv")
            require(Path(calls[-1][0][calls[-1][0].index("--python") + 1]) ==
                    provider.DEFAULT_PROVIDER_ROOT / "upstream/creature-art/proxy_skin.py",
                    "Blender bootstrap changed the locked provider script")

            before = len(calls)
            expect_error(lambda: provider.execute(
                "creature-art.bootstrap_review",
                ["--model", str(model), "--out", str(review_output), f"--out={root / 'escape'}"],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=work_root,
                runner=fake_runner), "PATH_FLAG_REPEATED")
            expect_error(lambda: provider.execute(
                "creature-art.bootstrap_review",
                ["--model", str(model), f"--model={scene}", "--out", str(review_output)],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=work_root,
                runner=fake_runner), "PATH_FLAG_REPEATED")
            require(len(calls) == before, "ambiguous duplicate path args reached Blender")

            expected_outputs = provider.validate_output_contract(
                provider.RUNNABLE["creature-art.bootstrap_review"],
                ["--out", str(review_output)], work_root)
            require(expected_outputs == [review_output], "output reservation differs from the declared --out")
            review_output.mkdir(parents=True)
            expect_error(lambda: provider.execute(
                "creature-art.bootstrap_review",
                ["--model", str(model), "--out", str(review_output)],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=work_root,
                runner=fake_runner), "OUTPUT_ALREADY_EXISTS")

            overlapping_work = input_root / "run-inside-input"
            expect_error(lambda: provider.execute(
                "creature-art.bootstrap_review",
                ["--model", str(model), "--out", str(overlapping_work / "review")],
                provider.DEFAULT_PROVIDER_ROOT, blender=str(fake_blender), work_root=overlapping_work,
                runner=fake_runner), "WORK_ROOT_INTERSECTS_INPUT")

    finally:
        provider.DEVELOPMENT_OUTPUT_ROOT = old_development_root


def test_render_model_and_scene_input_contracts() -> None:
    old_development_root = provider.DEVELOPMENT_OUTPUT_ROOT
    try:
        with tempfile.TemporaryDirectory(prefix="h3-art-render-input-contract-") as temporary:
            root = Path(temporary).resolve()
            development_root = root / "dev-output"
            development_root.mkdir()
            provider.DEVELOPMENT_OUTPUT_ROOT = development_root
            model_parent = development_root / "models"
            model_parent.mkdir()
            model = model_parent / "creature.glb"
            model.write_bytes(b"synthetic GLB fixture")
            render_command = provider.RUNNABLE["creature-art.render_sprites"]
            model_spec = render_command["input_path_flags"]["--model"]
            require(model_spec == {
                "kind": "file", "protect_parent": True,
                "suffixes": [".glb", ".gltf", ".fbx"],
            }, f"render_sprites model path policy drifted: {model_spec!r}")

            missing_work = development_root / "runs" / "missing-model"
            expect_error(lambda: provider.validate_execution_paths(
                render_command,
                ["--model", str(model_parent / "missing.glb"), "--out", str(missing_work / "frames")],
                missing_work), "INPUT_PATH_MISSING")

            directory_model = model_parent / "directory.glb"
            directory_model.mkdir()
            expect_error(lambda: provider.validate_execution_paths(
                render_command,
                ["--model", str(directory_model), "--out", str(missing_work / "directory-frames")],
                missing_work), "INPUT_FILE_REQUIRED")

            wrong_type = model_parent / "creature.obj"
            wrong_type.write_bytes(b"synthetic unsupported model fixture")
            expect_error(lambda: provider.validate_execution_paths(
                render_command,
                ["--model", str(wrong_type), "--out", str(missing_work / "wrong-type-frames")],
                missing_work), "INPUT_EXTENSION_UNSUPPORTED")

            inside_model_parent = model_parent / "runs"
            expect_error(lambda: provider.validate_execution_paths(
                render_command,
                ["--model", str(model), "--out", str(inside_model_parent / "frames")],
                inside_model_parent), "WORK_ROOT_INTERSECTS_INPUT")

            isolated_work = development_root / "runs" / "isolated-model"
            inputs, outputs = provider.validate_execution_paths(
                render_command,
                ["--model", str(model), "--out", str(isolated_work / "frames")],
                isolated_work)
            require(inputs == [(model.resolve(), model_parent.resolve())],
                    f"model validation did not protect its parent: {inputs!r}")
            require(outputs == [(isolated_work / "frames").resolve()],
                    f"render output reservation changed: {outputs!r}")

            scene_parent = development_root / "scenes"
            scene_parent.mkdir()
            scene = scene_parent / "portrait.blend"
            scene.write_bytes(b"synthetic Blender scene fixture")
            portrait_command = provider.RUNNABLE["creature-art.render_portrait"]
            scene_spec = portrait_command["input_path_flags"]["--source"]
            require(scene_spec == {
                "kind": "file", "protect_parent": True, "suffixes": [".blend"],
            }, f"render_portrait scene path policy drifted: {scene_spec!r}")
            bad_scene = scene_parent / "portrait.png"
            bad_scene.write_bytes(b"not a Blender scene")
            portrait_work = scene_parent / "runs"
            expect_error(lambda: provider.validate_execution_paths(
                portrait_command,
                ["--source", str(bad_scene), "--out", str(portrait_work / "portrait.png")],
                portrait_work), "INPUT_EXTENSION_UNSUPPORTED")
            expect_error(lambda: provider.validate_execution_paths(
                portrait_command,
                ["--source", str(scene), "--out", str(portrait_work / "portrait.png")],
                portrait_work), "WORK_ROOT_INTERSECTS_INPUT")
            isolated_portrait_work = development_root / "runs" / "isolated-portrait"
            scene_inputs, scene_outputs = provider.validate_execution_paths(
                portrait_command,
                ["--source", str(scene), "--out", str(isolated_portrait_work / "portrait.png")],
                isolated_portrait_work)
            require(scene_inputs == [(scene.resolve(), scene_parent.resolve())],
                    f"portrait validation did not protect its scene parent: {scene_inputs!r}")
            require(scene_outputs == [isolated_portrait_work / "portrait.png",
                                      (isolated_portrait_work / "portrait.json").resolve()],
                    f"portrait output/sidecar reservation changed: {scene_outputs!r}")
    finally:
        provider.DEVELOPMENT_OUTPUT_ROOT = old_development_root


def test_child_timeout_is_bounded_and_not_retried() -> None:
    calls = []

    def stalled_child(argv, **kwargs):
        calls.append((argv, kwargs))
        raise provider.subprocess.TimeoutExpired(argv, kwargs["timeout"])

    message = expect_error(lambda: provider.execute(
        "creature-art.def_extract.info", ["C:/data/H3sprite.lod", "CREATURE.DEF"],
        provider.DEFAULT_PROVIDER_ROOT, python=sys.executable,
        timeout_seconds=123, runner=stalled_child), "RUN_FAILED_TIMEOUT")
    require("direct child" in message and "process-tree cleanup is not guaranteed" in message,
            "standalone Python timeout must describe its process-tree limitation")
    require("Job Object" in message, "timeout must identify packaged EXE process-tree handling")
    require(len(calls) == 1, "timed-out child was retried")
    require(calls[0][1].get("timeout") == 123, "explicit timeout did not reach subprocess")


def test_argparse_abbreviations_cannot_override_protected_paths() -> None:
    # Reproduce Python argparse's real default behavior: abbreviations are
    # enabled, and the later abbreviated option overwrites the earlier value.
    for flag, abbreviated in (
        ("--out", "--ou=OUTSIDE"),
        ("--mod-dir", "--mod=OUTSIDE"),
        ("--source", "--sou=OUTSIDE"),
    ):
        parser = argparse.ArgumentParser()
        parser.add_argument(flag)
        require(parser.allow_abbrev is True, "argparse reproduction must use its default abbreviation mode")
        namespace = parser.parse_args([flag, "SAFE", abbreviated])
        destination = flag[2:].replace("-", "_")
        require(getattr(namespace, destination) == "OUTSIDE",
                f"argparse abbreviation overwrite was not reproduced for {flag}: {namespace}")

    expect_error(lambda: provider.normalize_path_flag_arguments(
        provider.RUNNABLE["creature-art.preview.sheet"],
        ["--out", "SAFE", "--ou=OUTSIDE"]), "PATH_FLAG_ABBREVIATION_FORBIDDEN")
    expect_error(lambda: provider.normalize_path_flag_arguments(
        provider.RUNNABLE["creature-art.new_creature_mod"],
        ["--mod-dir", "SAFE", "--mod=OUTSIDE"]), "PATH_FLAG_ABBREVIATION_FORBIDDEN")
    # Read-side paths need the same guard: abbreviated --source must not
    # replace the validated input model/scene path either.
    expect_error(lambda: provider.normalize_path_flag_arguments(
        provider.RUNNABLE["creature-art.proxy_skin"],
        ["--source", "SAFE.blend", "--sou=OUTSIDE.blend", "--out", "SAFE_OUT"]),
        "PATH_FLAG_ABBREVIATION_FORBIDDEN")
    require(provider.normalize_path_flag_arguments(
        provider.RUNNABLE["creature-art.preview.sheet"], ["--out=SAFE"]) == ["--out", "SAFE"],
        "full protected path option must remain accepted and canonicalized")


def _save_contract_png(path: Path, size: tuple[int, int]) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (80, 120, 160, 255)).save(path, format="PNG")


def _write_contract_preview_output(directory: Path, names: set[str]) -> None:
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        path = directory / name
        if path.suffix == ".png":
            _save_contract_png(path, (16, 16))
        elif path.suffix == ".gif":
            Image.new("RGB", (16, 16), (32, 64, 96)).save(path, format="GIF")
        elif path.suffix == ".mp4":
            path.write_bytes(b"contract-only mp4 bytes")
        elif path.suffix == ".html":
            path.write_text("<!doctype html><title>contract fixture</title>\n", encoding="utf-8")
        else:
            raise AssertionError(f"unreviewed fake output type: {name}")


def _fake_preview_runner(command_id: str, groups: list[str], has_previous: bool, calls: list):
    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(argv) > 1 and argv[1] == "-c":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        require(kwargs.get("shell") is False, "preview child must not use shell")
        require(kwargs.get("capture_output") is True, "preview output must be captured for path rewriting")
        script = Path(argv[1])
        require(script.name == ("motion_preview.py" if command_id.endswith("motion_preview") else "study_preview.py"),
                f"wrong locked preview entrypoint: {script}")
        require(".preview-stage" in argv[2], "upstream preview was not given a private staged input")
        stage_study = Path(argv[2])
        stage_output = stage_study / "preview" if command_id.endswith("motion_preview") else stage_study
        names = (provider._expected_motion_preview_outputs(groups, has_previous)
                 if command_id.endswith("motion_preview") else {"comparison.png", "native-size.png", "hands.png"})
        _write_contract_preview_output(stage_output, names)
        stdout = str(stage_output / "index.html") + "\n" if command_id.endswith("motion_preview") else "measurement contract fixture\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return fake_runner


def _motion_preview_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    study = root / "motion-study"
    reference_root = root / "reference-export"
    reference = reference_root / "body"
    previous = root / "previous-study"
    groups = ["holding", "moving", "attack"]
    manifest = {
        "clips": {name.upper(): {} for name in groups},
        "profile": {
            "label": "Contract fixture",
            "fps": 30,
            "clips": {name.upper(): {"sprite_frames": 1} for name in groups},
        },
    }
    layout = {"groups": [{"name": name.upper(), "frames": [{}]} for name in groups]}
    study.mkdir(parents=True)
    (study / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (reference_root / "layout.json").parent.mkdir(parents=True, exist_ok=True)
    (reference_root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    for name in groups:
        for scale, size in ((1, (450, 400)), (2, (900, 800))):
            _save_contract_png(study / f"sprites{scale}x" / f"{name}_00.png", size)
        _save_contract_png(study / "review" / name / "000.png", (900, 800))
        _save_contract_png(reference / f"{name}_00.png", (450, 400))
    for name in ("holding_00.png", "moving_00.png"):
        _save_contract_png(previous / "sprites2x" / name, (900, 800))
    return study, reference, reference_root / "layout.json", previous


def _study_preview_fixture(root: Path) -> tuple[Path, Path]:
    study = root / "study-preview"
    reference = root / "reference-body"
    study.mkdir(parents=True)
    measurements = {
        "holding": {
            "blade_length": 1.0,
            "socket_error": 0.25,
            "hand": {"target_error": 0.5},
        },
    }
    (study / "manifest.json").write_text(json.dumps({"measurements": measurements}), encoding="utf-8")
    for name in provider.STUDY_PREVIEW_FRAMES:
        _save_contract_png(study / f"{name}.png", (900, 800))
        _save_contract_png(study / "before" / f"{name}.png", (900, 800))
        _save_contract_png(reference / provider.STUDY_PREVIEW_FRAMES[name], (450, 400))
    for side in ("right", "left"):
        _save_contract_png(study / f"{side}-hand.png", (128, 128))
    return study, reference


def test_isolated_motion_preview_stages_inputs_and_publishes_whitelisted_files() -> None:
    old_development_root = provider.DEVELOPMENT_OUTPUT_ROOT
    old_which = provider.shutil.which
    calls = []
    try:
        with tempfile.TemporaryDirectory(prefix="h3-art-motion-preview-contract-") as temporary:
            root = Path(temporary).resolve()
            provider.DEVELOPMENT_OUTPUT_ROOT = root / "dev-output"
            provider.DEVELOPMENT_OUTPUT_ROOT.mkdir()
            study, reference, layout, previous = _motion_preview_fixture(provider.DEVELOPMENT_OUTPUT_ROOT / "inputs")
            source_files = [item for base in (study, reference, layout, previous)
                            for item in ([base] if base.is_file() else base.rglob("*")) if item.is_file()]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
            work_root = provider.DEVELOPMENT_OUTPUT_ROOT / "runs" / "motion-preview"
            output = work_root / "review"
            provider.shutil.which = lambda value: "C:/contract-tools/ffmpeg.exe" if value == "ffmpeg" else old_which(value)
            stdout_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture):
                code = provider.execute(
                    "creature-art.motion_preview",
                    ["--study-dir", str(study), "--reference-dir", str(reference), "--layout", str(layout),
                     "--previous-dir", str(previous), "--out", str(output)],
                    provider.DEFAULT_PROVIDER_ROOT, python=sys.executable, work_root=work_root,
                    runner=_fake_preview_runner("creature-art.motion_preview", ["holding", "moving", "attack"], True, calls),
                )
            require(code == 0, f"staged motion preview returned {code}")
            expected = provider._expected_motion_preview_outputs(["holding", "moving", "attack"], True)
            require({path.name for path in output.iterdir()} == expected, "motion preview output escaped its fixed whitelist")
            require(not (work_root / ".preview-stage").exists(), "successful motion preview left a temporary input copy")
            require((output / "walk-comparison.gif").is_file(), "explicit previous study was not included in preview")
            require(all(hashlib.sha256(path.read_bytes()).hexdigest() == before[path] for path in source_files),
                    "upstream preview changed a source study/reference file")
            child_calls = [call for call in calls if len(call[0]) > 1 and call[0][1] != "-c"]
            require(len(child_calls) == 1, f"motion preview retried or skipped its upstream child: {len(child_calls)}")
            require(str(output / "index.html") in stdout_capture.getvalue(), "preview path rewriting contract failed")
            require(".preview-stage" not in stdout_capture.getvalue(), "temporary stage path leaked in CLI output")
    finally:
        provider.DEVELOPMENT_OUTPUT_ROOT = old_development_root
        provider.shutil.which = old_which


def test_isolated_study_preview_rejects_overlap_and_symlinks() -> None:
    old_development_root = provider.DEVELOPMENT_OUTPUT_ROOT
    calls = []
    try:
        with tempfile.TemporaryDirectory(prefix="h3-art-study-preview-contract-") as temporary:
            root = Path(temporary).resolve()
            provider.DEVELOPMENT_OUTPUT_ROOT = root / "dev-output"
            provider.DEVELOPMENT_OUTPUT_ROOT.mkdir()
            study, reference = _study_preview_fixture(provider.DEVELOPMENT_OUTPUT_ROOT / "inputs")
            work_root = provider.DEVELOPMENT_OUTPUT_ROOT / "runs" / "study-preview"
            output = work_root / "review"
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                      for base in (study, reference) for path in base.rglob("*") if path.is_file()}
            code = provider.execute(
                "creature-art.study_preview",
                ["--study-dir", str(study), "--reference-dir", str(reference), "--out", str(output)],
                provider.DEFAULT_PROVIDER_ROOT, python=sys.executable, work_root=work_root,
                runner=_fake_preview_runner("creature-art.study_preview", [], False, calls),
            )
            require(code == 0, f"staged study preview returned {code}")
            require({path.name for path in output.iterdir()} == {"comparison.png", "native-size.png", "hands.png"},
                    "study preview output escaped its fixed whitelist")
            require(not (work_root / ".preview-stage").exists(), "successful study preview left a temporary input copy")
            after = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for base in (study, reference) for path in base.rglob("*") if path.is_file()}
            require(after == before, "study preview changed a source study/reference file")

            overlap_root = study / "nested-run"
            overlap_output = overlap_root / "review"
            expect_error(lambda: provider.validate_execution_paths(
                provider.RUNNABLE["creature-art.study_preview"],
                ["--study-dir", str(study), "--reference-dir", str(reference), "--out", str(overlap_output)],
                overlap_root), "WORK_ROOT_INTERSECTS_INPUT")
            require(not overlap_root.exists(), "overlap refusal created a work directory")
            study_command = provider.RUNNABLE["creature-art.study_preview"]
            expect_error(lambda: provider.normalize_path_flag_arguments(
                study_command, ["--study-dir", str(study), "--study-dir", str(study)]), "PATH_FLAG_REPEATED")
            expect_error(lambda: provider.normalize_path_flag_arguments(
                study_command, ["--study-dir", str(study), "--reference-dir", str(reference),
                                "--out", str(output), "--ref=" + str(reference)]),
                "PATH_FLAG_ABBREVIATION_FORBIDDEN")

            source = study / "holding.png"
            link = study / "linked-holding.png"
            try:
                os.symlink(source, link)
            except (OSError, NotImplementedError):
                original_check = provider._is_reparse_point
                provider._is_reparse_point = lambda path: path == link or original_check(path)
                try:
                    expect_error(lambda: provider._assert_no_reparse_components(link, "symlink fixture"),
                                 "REPARSE_PATH_FORBIDDEN")
                finally:
                    provider._is_reparse_point = original_check
            else:
                try:
                    expect_error(lambda: provider._assert_no_reparse_components(link, "symlink fixture"),
                                 "REPARSE_PATH_FORBIDDEN")
                finally:
                    link.unlink()

            rogue_work = provider.DEVELOPMENT_OUTPUT_ROOT / "runs" / "rogue-output"
            rogue_output = rogue_work / "review"
            def rogue_runner(argv, **kwargs):
                calls.append((argv, kwargs))
                if len(argv) > 1 and argv[1] == "-c":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                stage_study = Path(argv[2])
                _write_contract_preview_output(
                    stage_study, {"comparison.png", "native-size.png", "hands.png"})
                _save_contract_png(stage_study / "unexpected.png", (16, 16))
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            expect_error(lambda: provider.execute(
                "creature-art.study_preview",
                ["--study-dir", str(study), "--reference-dir", str(reference), "--out", str(rogue_output)],
                provider.DEFAULT_PROVIDER_ROOT, python=sys.executable, work_root=rogue_work,
                runner=rogue_runner), "PREVIEW_OUTPUT_SET_MISMATCH")
            require(not rogue_output.exists(), "unexpected upstream output was published")
            require((rogue_work / ".preview-stage").is_dir(), "failed output verification did not retain the private stage")
    finally:
        provider.DEVELOPMENT_OUTPUT_ROOT = old_development_root


def test_no_default_or_unbounded_writes() -> None:
    work_root = provider.DEVELOPMENT_OUTPUT_ROOT / "contract-check"
    sample = work_root / "nested" / "output.png"
    for command_id, command in provider.RUNNABLE.items():
        flags = command["write_flags"]
        require(command["runtime"] in {"python", "blender"}, f"invalid runtime: {command_id}")
        if flags:
            require(not command.get("read_only"), f"write command marked read-only: {command_id}")
            require(command.get("required_inputs"), f"missing concrete required input contract: {command_id}")
            if flags == ["--out"]:
                selected_sample = work_root / "output" if command.get("output_direct_child_only") else sample
                provider.validate_output_contract(command, ["--out", str(selected_sample)], work_root)
                require(provider.normalize_path_flag_arguments(command, [f"--out={selected_sample}"]) == [
                    "--out", str(selected_sample)], "equals-form path flag was not normalized")
                expect_error(lambda c=command: provider.validate_output_contract(c, [], work_root),
                             "OUTPUT_FLAG_REQUIRED_ONCE")
                expect_error(lambda c=command: provider.validate_output_contract(
                    c, ["--out", "relative-output.png"], work_root), "OUTPUT_PATH_MUST_BE_ABSOLUTE")
                expect_error(lambda c=command: provider.validate_output_contract(
                    c, ["--out", str(selected_sample), f"--out={selected_sample}"], work_root), "PATH_FLAG_REPEATED")
            else:
                require(flags == ["--mod-dir"], f"unreviewed output flag {flags!r} on {command_id}")
                provider.validate_output_contract(command, ["--mod-dir", str(work_root / "new-mod")], work_root)
        else:
            require(command_id in {
                "creature-art.def_extract.info",
                "creature-art.validate_creature_animation",
            }, f"unclassified no-output command {command_id}")
            require(command.get("read_flags"), f"read-only operation lacks input flags: {command_id}")
    outside = provider.DEVELOPMENT_OUTPUT_ROOT.parent / "outside.png"
    expect_error(lambda: provider.validate_output_contract(
        provider.RUNNABLE["creature-art.preview.sheet"], ["--out", str(outside)], work_root),
        "OUTPUT_PATH_OUTSIDE_WORK_ROOT")


def test_catalogue_truth() -> None:
    root = provider.provider_root(None)
    catalog = {entry["id"]: entry for entry in provider.discover_catalog(root)}
    expected = set(provider.RUNNABLE)
    require(expected <= set(catalog), f"runnable capabilities missing from catalogue: {sorted(expected-set(catalog))}")
    for command_id in expected:
        entry = catalog[command_id]
        require(entry["status"] == "run" and entry["io_status"] == "audited", f"incorrect run status: {command_id}")
        require(entry["script"] in {item["path"] for item in provider.load_lock(root)["files"]},
                f"catalogue points outside source lock: {command_id}")
        require(entry["read_flags"] == provider.RUNNABLE[command_id]["read_flags"], f"read flags mismatch: {command_id}")
        require(entry["write_flags"] == provider.RUNNABLE[command_id]["write_flags"], f"write flags mismatch: {command_id}")

    for command_id in (
        "creature-art.def_extract.info", "creature-art.def_extract.export",
        "creature-art.preview.sheet", "creature-art.preview.layers",
        "creature-art.preview.anim", "creature-art.preview.compare",
        "creature-art.preview.readability", "creature-art.validate_creature_animation",
        "creature-art.render_sprites", "creature-art.new_creature_mod",
    ):
        require(command_id in catalog, f"key H3 operation absent: {command_id}")
    for script in provider.NETWORK_SCRIPTS:
        command_id = Path(script).with_suffix("").as_posix().replace("/", ".")
        require(catalog[command_id]["status"] == "disabled_network", f"network command not disabled: {script}")
        require(command_id not in provider.RUNNABLE, f"network command accidentally executable: {script}")
    require(any(entry["status"] == "requires_adapter" for entry in catalog.values()),
            "unreviewed upstream commands were incorrectly presented as ready")
    pending = [entry for entry in catalog.values() if entry["status"] == "requires_adapter"]
    require({entry["script"] for entry in pending} == {
        "creature-art/check_nonhumanoid_motion.py",
    }, "motion preview should be isolated; unrelated specialist validation remains pending")
    require(catalog["creature-art.motion_preview"]["description"].find("CSKELE") >= 0,
            "motion preview must state its upstream skeleton-only scope")
    require(catalog["creature-art.study_preview"]["description"].find("not a generic H3") >= 0,
            "study preview must not overclaim general H3 action support")
    require(catalog["creature-art.spectral_study"]["status"] == "excluded_rejected_experiment",
            "rejected experiment must be explicitly excluded")
    require(catalog["creature-art.cache_creature_scale"]["status"] == "fallback_upscale_only",
            "scale-only Lanczos output must be identified as a fallback")
    require(catalog["creature-art.assemble_mod"]["status"] == "scope_vcmi_creature_mod",
            "VCMI packaging must remain separate from legacy DEF output")
    require("Necropolis" in catalog["town-art.build_layered_town"]["adapter_gap"],
            "Necropolis-only town tooling must show its scope")
    require(all("unreviewed" not in entry["adapter_gap"].lower() for entry in catalog.values()
                if entry["status"] not in {"run", "disabled_network"}),
            "non-executable catalogue entries need a real purpose/scope reason")
    render = catalog["creature-art.render_sprites"]
    require("not a universal one-image full-animation generator" in render["description"],
            "catalogue overclaims automatic complete action generation")


def test_catalog_verifies_once_and_rechecks_changed_source() -> None:
    with tempfile.TemporaryDirectory(prefix="h3-catalog-integrity-") as temporary:
        copied = Path(temporary) / "provider"
        shutil.copytree(provider.DEFAULT_PROVIDER_ROOT, copied)
        arguments = ["catalog", "--provider-root", str(copied), "--json"]
        with mock.patch.object(provider, "verify_source_tree", wraps=provider.verify_source_tree) as verify:
            expected = provider.discover_catalog(copied)
            require(isinstance(expected, list), "public catalogue result must remain a list")
            require(verify.call_count == 1, "direct catalogue must verify the complete tree once")
            verify.reset_mock()
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = provider.main(arguments)
            require(code == 0 and not errors.getvalue(), "catalogue CLI failed on locked source")
            require(verify.call_count == 1, "catalogue CLI repeated the complete source-tree verification")
            catalog = json.loads(output.getvalue())
            require(set(catalog) == {"schema", "provider", "integrity", "commands"}
                    and catalog["schema"] == "h3tierexport.art-pipeline-catalog.v1"
                    and catalog["provider"] == "yzh119/h3-art-pipeline", "catalogue envelope changed")
            require(catalog["commands"] == expected, "catalogue CLI changed the public entries")
            require(catalog["integrity"]["passed"] and catalog["integrity"]["actual_files"] == 152,
                    "catalogue CLI lost complete source integrity facts")

            changed = copied / "upstream" / "README.md"
            original = changed.read_bytes()
            changed.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            verify.reset_mock()
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = provider.main(arguments)
            require(code == 2 and "SOURCE_INTEGRITY_FAILURE" in errors.getvalue(),
                    "a later catalogue call reused stale source integrity")
            require(not output.getvalue(), "changed source emitted a catalogue before refusal")
            require(verify.call_count == 1, "later catalogue call did not perform its own complete verification")
            verify.reset_mock()
            expect_error(lambda: provider.discover_catalog(copied), "SOURCE_INTEGRITY_FAILURE")
            require(verify.call_count == 1, "public catalogue bypassed source drift after another invocation")


def main() -> int:
    checks = [
        test_source_hash_drift_refusal,
        test_argument_forwarding_is_argv_and_preserves_exit_code,
        test_frozen_provider_refuses_live_override_and_never_adds_cache_files,
        test_candidate_explicit_provider_root_does_not_require_the_default_resource,
        test_missing_dependency_is_reported_clearly,
        test_blender_stage_real_paths_and_output_isolation,
        test_render_model_and_scene_input_contracts,
        test_child_timeout_is_bounded_and_not_retried,
        test_argparse_abbreviations_cannot_override_protected_paths,
        test_isolated_motion_preview_stages_inputs_and_publishes_whitelisted_files,
        test_isolated_study_preview_rejects_overlap_and_symlinks,
        test_no_default_or_unbounded_writes,
        test_catalogue_truth,
        test_catalog_verifies_once_and_rechecks_changed_source,
    ]
    results = []
    for check in checks:
        check()
        results.append(check.__name__)
        print("PASS", check.__name__)
    print(json.dumps({"passed": True, "checks": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
