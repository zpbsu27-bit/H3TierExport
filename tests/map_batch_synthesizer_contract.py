#!/usr/bin/env python3
"""Contract tests for H3TierExport map-synth (map_batch_synthesizer.py)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image


PRODUCT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("H3TIEREXPORT_EXE", PRODUCT / "build" / "candidate" / "H3TierExport.exe"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_test_png(path: Path, arr: np.ndarray) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype(np.uint8), mode="RGBA").save(path, format="PNG")
    return {"path": str(path), "sha256": sha256_file(path)}


class MapBatchSynthesizerContract(unittest.TestCase):
    def invoke_map_synth(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(EXE), "map-synth", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def test_all_five_synthesis_operators_check_run_and_verify(self) -> None:
        exp_root = PRODUCT / "build" / "experiments" / "map-synth"
        exp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="op_map_synth_", dir=exp_root) as tmp:
            tmp_dir = Path(tmp)
            inputs_dir = tmp_dir / "inputs"
            op_id = "op_map_synth_test_01"
            outdir = tmp_dir / op_id

            # 1. Base & overlay terrain textures (96x96 = 32x32 * 3)
            yy, xx = np.meshgrid(np.arange(96), np.arange(96), indexing="ij")
            grass = np.zeros((96, 96, 4), dtype=np.uint8)
            grass[..., 0] = 45 + ((xx * 7 + yy * 13) % 30)
            grass[..., 1] = 135 + ((xx * 11 + yy * 5) % 45)
            grass[..., 2] = 40
            grass[..., 3] = 255
            grass_pin = save_test_png(inputs_dir / "grass_3x.png", grass)

            dirt = np.zeros((96, 96, 4), dtype=np.uint8)
            dirt[..., 0] = 140 + ((xx * 5 + yy * 9) % 35)
            dirt[..., 1] = 100 + ((xx * 3 + yy * 7) % 25)
            dirt[..., 2] = 55
            dirt[..., 3] = 255
            dirt_pin = save_test_png(inputs_dir / "dirt_3x.png", dirt)

            mask = np.zeros((32, 32, 4), dtype=np.uint8)
            my, mx = np.meshgrid(np.arange(32), np.arange(32), indexing="ij")
            mask[..., 0] = np.clip(mx * 8, 0, 255).astype(np.uint8)
            mask[..., 1] = mask[..., 0]
            mask[..., 2] = mask[..., 0]
            mask[..., 3] = 255
            mask_pin = save_test_png(inputs_dir / "mask_1x.png", mask)

            # 2. Flag master (36x24 = 12x8 * 3) with metallic pole at x=0..2 and red cloth at x=3..35
            flag = np.zeros((24, 36, 4), dtype=np.uint8)
            fy, fx = np.meshgrid(np.arange(24), np.arange(36), indexing="ij")
            flag[:, :3, :3] = 180  # neutral silver pole
            flag[:, :3, 3] = 255
            shade = 120 + ((fx * 9 + fy * 5) % 90)
            flag[:, 3:, 0] = shade[:, 3:]
            flag[:, 3:, 1] = (shade[:, 3:] * 0.2).astype(np.uint8)
            flag[:, 3:, 2] = (shade[:, 3:] * 0.2).astype(np.uint8)
            flag[:, 3:, 3] = 255
            flag_pin = save_test_png(inputs_dir / "flag_master_3x.png", flag)

            # 3. UI Button Normal state (48x36)
            btn = np.zeros((36, 48, 4), dtype=np.uint8)
            btn[..., 0] = 160
            btn[..., 1] = 130
            btn[..., 2] = 75
            btn[..., 3] = 255
            btn[6:30, 6:42, 0] = 210
            btn[6:30, 6:42, 1] = 175
            btn[6:30, 6:42, 2] = 95
            btn_pin = save_test_png(inputs_dir / "btn_normal_3x.png", btn)

            manifest = {
                "schema": "h3.tier-export.map-synth-manifest.v1",
                "operation_id": op_id,
                "jobs": [
                    {
                        "job_id": "terrain_grass_dirt_01",
                        "type": "terrain-synthesize",
                        "canvas_3x": [96, 96],
                        "base_texture": grass_pin,
                        "overlay_texture": dirt_pin,
                        "transition_mask": mask_pin,
                        "tile_offset_3x": [24, 12],
                        "splat_sharpness": 4.0,
                    },
                    {
                        "job_id": "river_curve_flow",
                        "type": "road-river-synth",
                        "source_strip": grass_pin,
                        "transform": "curve_bend",
                        "flow_frames": 3,
                        "flow_amplitude_px": 2.0,
                        "flow_direction": [1.0, 0.5],
                    },
                    {
                        "job_id": "hero_banner_8p",
                        "type": "flag-batch-synth",
                        "master_flag": flag_pin,
                        "player_colors": ["red", "blue", "green", "purple"],
                        "wave_frames": 2,
                        "pole_anchor_x": 1,
                        "wave_amplitude_px": 2.5,
                    },
                    {
                        "job_id": "dialog_ok_btn",
                        "type": "ui-state-synth",
                        "normal_button": btn_pin,
                        "bevel_margin_px": 6,
                        "pressed_shift_px": 3,
                        "states": ["normal", "pressed", "disabled", "hover"],
                    },
                    {
                        "job_id": "boat_with_flag_socket",
                        "type": "layered-sprite-compose",
                        "canvas_3x": [96, 96],
                        "layers": [
                            {"path": dirt_pin["path"], "sha256": dirt_pin["sha256"], "origin_3x": [0, 0], "z_order": 0},
                            {"path": flag_pin["path"], "sha256": flag_pin["sha256"], "origin_3x": [12, 9], "socket_offset_3x": [6, 3], "z_order": 10},
                        ],
                    },
                ],
            }
            manifest_path = tmp_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            manifest_sha = sha256_file(manifest_path)

            # 1. check mode writes zero files
            check_res = self.invoke_map_synth(
                "check",
                "--manifest", str(manifest_path),
                "--manifest-sha256", manifest_sha,
                "--outdir", str(outdir),
                "--operation-id", op_id,
            )
            self.assertEqual(check_res.returncode, 0, check_res.stderr)
            self.assertFalse(outdir.exists(), "check mode must not create output directory")

            # 2. run mode synthesizes all outputs and receipt
            run_res = self.invoke_map_synth(
                "run",
                "--manifest", str(manifest_path),
                "--manifest-sha256", manifest_sha,
                "--outdir", str(outdir),
                "--operation-id", op_id,
            )
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            receipt = json.loads((outdir / "map-synth-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema"], "h3.tier-export.map-synth-receipt.v1")
            self.assertEqual(len(receipt["jobs"]), 5)
            self.assertFalse(receipt["automatic_art_approval"])

            # Verify blue and green flags differ in color while preserving alpha
            blue_flag = np.asarray(Image.open(outdir / "hero_banner_8p_blue_f00.png"))
            green_flag = np.asarray(Image.open(outdir / "hero_banner_8p_green_f00.png"))
            self.assertGreater(int(np.abs(blue_flag.astype(int) - green_flag.astype(int)).max()), 25)

            # Verify pressed button differs from normal button
            btn_norm = np.asarray(Image.open(outdir / "dialog_ok_btn_normal.png"))
            btn_press = np.asarray(Image.open(outdir / "dialog_ok_btn_pressed.png"))
            self.assertFalse(np.array_equal(btn_norm, btn_press))

            # 3. verify mode confirms receipt and output hashes
            verify_res = self.invoke_map_synth(
                "verify",
                "--manifest", str(manifest_path),
                "--manifest-sha256", manifest_sha,
                "--outdir", str(outdir),
                "--operation-id", op_id,
            )
            self.assertEqual(verify_res.returncode, 0, verify_res.stderr)


if __name__ == "__main__":
    unittest.main()
