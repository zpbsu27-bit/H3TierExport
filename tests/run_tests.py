"""Execute tool contracts with synthetic and read-only native-reference fixtures."""
from pathlib import Path
import datetime, hashlib, json, os, subprocess, sys

PRODUCT=Path(__file__).resolve().parents[1]
BUILD=PRODUCT/"build"/"candidate"
OUT=PRODUCT/"build"/"test-results"
sys.path.insert(0,str(PRODUCT/"scripts"))
from build import validate_build_sources

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    exe=BUILD/"H3TierExport.exe"
    build_path=BUILD/"build-receipt.json"
    build_raw=build_path.read_bytes()
    build_hash=hashlib.sha256(build_raw).hexdigest()
    build=json.loads(build_raw);validate_build_sources(build)
    exe_hash=hashlib.sha256(exe.read_bytes()).hexdigest()
    if build['executable']['sha256']!=exe_hash:raise RuntimeError('BUILD_EXE_DRIFT')
    commands=[
      ["core",str(BUILD/"core-tests.exe")],
      ["quality-edges",str(BUILD/"quality-tests.exe")],
      ["checkerboard-alpha",str(BUILD/"checkerboard-tests.exe")],
      ["checkerboard-cli",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"checkerboard_cli_contract.py")],
      ["device",str(exe),"info"],
      ["companion-cli",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"companion_cli_contract.py")],
      ["campaign-vcmi-mod-geometry",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"campaign_vcmi_mod_geometry_contract.py")],
      ["workspace-binding",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"workspace_contract.py")],
      ["tool-paths",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"tool_paths_contract.py")],
      ["portable-runtime-publish",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"publish_portable_runtime_contract.py")],
      ["source-capacity",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"native_3x_capacity_contract.py")],
      ["town-master-prepare",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"town_master_prepare_contract.py")],
      ["motion-visual-review",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"review_motion_sequence_contract.py")],
      ["native-review-render",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"native_review_render_contract.py")],
      ["native-review-batch",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"native_review_batch_contract.py")],
      ["interpolation-adapter",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"interpolate_rife_candidates_contract.py")],
      ["creature-actions",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_action_plan_contract.py")],
      ["transparent-interpolation-benchmark",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"transparent_interpolation_benchmark_contract.py")],
      ["alpha-flow-diagnostic",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"alpha_flow_diagnostic_contract.py")],
      ["blender-action-pipeline",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"blender_action_pipeline_contract.py")],
      ["blender-motion-consistency",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"blender_motion_consistency_contract.py")],
      ["creature-production",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_production_contract.py")],
      ["creature-master-set",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_master_set_contract.py")],
      ["asset-repair",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"asset_repair_contract.py")],
      ["subject-scale-layout",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"subject_scale_layout_contract.py")],
      ["creature-sequence-registration",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_sequence_registration_contract.py")],
      ["art-batch-quality",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"art_batch_quality_contract.py")],
      ["art-quality-strategies",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"art_quality_strategies_contract.py")],
      ["road-generation-packet",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"road_generation_packet_contract.py")],
      ["map-assembly",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_assembly_contract.py")],
      ["map-source-oracle",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_source_oracle_contract.py")],
      ["road-batch-quality",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"road_batch_quality_contract.py")],
      ["map-source-batch",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_source_batch_contract.py")],
      ["map-material-batch",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_material_batch_contract.py")],
      ["asset-repair-interfaces",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"asset_repair_interfaces_contract.py")],
      ["terrain-texture-family",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"terrain_texture_family_contract.py")],
      ["interface-port-geometry",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"interface_port_geometry_contract.py")],
      ["tile-geometry-design",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"tile_geometry_design_contract.py")],
      ["tile-interface-components",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"tile_interface_components_contract.py")],
      ["creature-scale-reports",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_scale_reports_contract.py")],
      ["creature-motion-postcheck",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_motion_postcheck_contract.py")],
      ["creature-landmark-annotator",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_landmark_annotator_contract.py")],
      ["creature-auto-pose",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"creature_auto_pose_contract.py")],
      ["mounted-pose",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"mounted_pose_contract.py")],
      ["pose-feature-registration",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"pose_feature_registration_contract.py")],
      ["action-production-binding",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"action_production_binding_contract.py")],
      ["production-provenance",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"production_provenance_contract.py")],
      ["logical-alpha-native-pixels",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"logical_alpha_native_pixels_contract.py")],
      ["logical-alpha-atomic",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"logical_alpha_atomic_contract.py")],
      ["logical-alpha",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"logical_alpha_contract.py")],
      ["art-pipeline-provider",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"art_pipeline_provider_contract.py")],
      ["defpack-adapter",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"deftool_batch_contract.py")],
      ["cloud-animation",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"cloud_animation_contract.py")],
      ["ui-smoke",str(exe),"ui-smoke"],
      ["quality-profile-window-events",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"quality_profile_ui_contract.py")],
      ["apng-window-events",str(exe),"apng-selftest"],
      ["skill-master-variants",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"master_variant_cli_contract.py"),str(exe)],
      ["map-artifact-masters",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_artifact_cli_contract.py"),str(exe)],
      ["flag-alignment-guard",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"flag_alignment_guard_contract.py")],
      ["town-flag-review",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"town_flag_review_contract.py")],
      ["flag-pole-audit",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"flag_pole_audit_contract.py")],
      ["selftest",str(exe),"selftest","--require-gpu"],
      ["integration",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"integration.py")],
      ["full-raw-domain",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"full_raw_domain_contract.py")],
      ["sheet-intake",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"sheet_intake_contract.py")],
      ["town-process-review",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"town_process_review_contract.py")],
      ["town-scene-composer",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"town_scene_composer_contract.py")],
      ["map-batch-synthesizer",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_batch_synthesizer_contract.py")],
      ["native-alias",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"native_alias_fixture_contract.py")],
      ["master-expansion",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"master_expansion_contract.py")],
      ["hero-canvas-gate",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"hero_canvas_gate_contract.py")],
      ["motion-contract",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"motion_contract.py")],
      ["map-object-sequence",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_object_sequence_contract.py")],
      ["map-sprite-mod-packager",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"map_sprite_mod_packager_contract.py")],
      ["sprites3x-mod-sync",sys.executable,"-B","-X","utf8",str(Path(__file__).parent/"sprites3x_mod_sync_contract.py")],
    ]
    results=[]
    env=os.environ.copy();env["H3TIEREXPORT_EXE"]=str(exe)
    for name,*cmd in commands:
        p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=240,env=env)
        result={"name":name,"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
        try: result["result"]=json.loads(p.stdout)
        except ValueError: pass
        results.append(result)
        print(name,p.returncode,(p.stderr or p.stdout)[-1600:],flush=True)
        if p.returncode: break
    source_error=None
    try:
        validate_build_sources(build)
        if hashlib.sha256(build_path.read_bytes()).hexdigest()!=build_hash or hashlib.sha256(exe.read_bytes()).hexdigest()!=exe_hash:raise RuntimeError('BUILD_CHANGED_DURING_QUALIFICATION')
    except Exception as error:source_error=str(error)
    report={"schema":"h3.tier-export.test-suite.v2","utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "passed":source_error is None and len(results)==len(commands) and all(r["exit_code"]==0 for r in results),
            "exe_sha256":exe_hash,"build_receipt_sha256":build_hash,"source_current":source_error is None,"source_error":source_error,"results":results,
            "synthetic_only":False,
            "fixture_scope":"synthetic contracts and read-only native references; native-alias uses an explicit temporary TEST-only reader binding and retains the original owner-cue currentness check",
            "real_user_art_approvals_created":0,"game_writes":0}
    path=OUT/"tests.json";path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"report":str(path),"passed":report["passed"],"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}))
    return 0 if report["passed"] else 1

if __name__=="__main__":
    sys.exit(main())
