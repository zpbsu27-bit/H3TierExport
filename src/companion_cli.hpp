#pragma once

#include <windows.h>
#include <algorithm>
#include <array>
#include <cctype>
#include <cwctype>
#include <string>
#include <vector>
#include "companion_runtime_id.h"

namespace h3tier {

struct CompanionCommand {
 std::string name;
 std::wstring script;
 std::vector<std::wstring> modules;
 std::string usage;
 std::string purpose;
 bool frozenRuntime=false;
 std::vector<std::string> runtimeResources;
 bool retired=false;
 std::string replacement;
};

struct CompanionRuntimeResource {
 std::string id,path,sourcePath,role;
 bool tree=false;
};
inline const std::array<CompanionRuntimeResource,3>& companion_runtime_resources(){
 static const std::array<CompanionRuntimeResource,3> resources={{
  {"blender-worker","blender_action_worker.py","scripts/blender_action_worker.py","python-worker",false},
  {"flag-pole-reference","scripts/data/flag_pole_reference_1999.json.gz","scripts/data/flag_pole_reference_1999.json.gz","reference-data",false},
  {"h3-art-pipeline","providers/h3-art-pipeline","providers/h3-art-pipeline","provider-source",true}
 }};
 return resources;
}

inline const std::array<CompanionCommand,42>& companion_commands(){
 static const std::array<CompanionCommand,42> commands={{
  {"creature-auto-pose",L"creature_auto_pose.py",{L"numpy",L"PIL"},"check|run|verify --manifest PNG-OR-FROZEN-JSON --manifest-sha256 SHA --outdir NEW-PATH --operation-id ID","Local human-pose and explicitly grouped proportional registration candidates with source/model SHA bindings and flip consistency. Review only; no source edits, native K, art approval, master or 3X publication.",true,{},false,""},
  {"hero-mounted-pose",L"hero_mounted_pose.py",{L"numpy",L"PIL"},"freeze --inventory PATH --inventory-sha256 SHA --identity AH00_ --masters-dir PATH --operation-id ID | check|run|verify --manifest PNG-OR-FROZEN-JSON --manifest-sha256 SHA --outdir NEW-PATH --operation-id ID","Local separate horse and rider pose candidates and grouped proportional registration previews; does not infer native ground or approve/modify artwork.",true,{},false,""},
  {"sequence-temporal",L"sequence_temporal_review.py",{L"numpy",L"PIL"},"freeze --layout-receipt PATH --layout-receipt-sha256 SHA --operation-id ID [--loop-group N --root-step-warn-native N] | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-PATH --operation-id ID","Replay verified anatomy layout and flag within-group landmark spread, adjacent change and explicitly declared loop/root steps. Review only; no bbox sizing, automatic art approval or image changes.",true,{},false,""},
  {"scale-layout",L"subject_scale_layout.py",{L"numpy",L"PIL"},"catalog | quality-init|quality-freeze | road-batch-freeze|road-batch-check|road-batch-run|road-batch-verify|road-batch-register-model | map-source-proof | map-fixture|map-review | check|run|verify","Category QA and automated road batch matching: native 17-slot coverage, sampled-3X provenance, connected openings, all source-qualified neighbors, shared painted-interface bands and pair gallery. Native H3M half-cell rendering and headless source-unit proof are reusable. No image repairs, deletion, resampling or automatic art/runtime approval.",true,{},false,""},
  {"scale-draft",L"scale_draft.py",{L"PIL"},"catalog | freeze --inventory PATH --inventory-sha256 SHA --identity DEF --category CATEGORY --operation-id ID [--masters-dir PATH --context PATH --context-sha256 SHA] | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-PATH --operation-id ID","Generate source-pinned category scale drafts from every actual native slot. Missing approved samples or anatomical landmarks remain DRAFT/NOT_MEASURED; no image scaling or art approval.",true,{},false,""},
  {"scale-audit",L"scale_audit.py",{L"PIL"},"catalog | freeze --inventory PATH --inventory-sha256 SHA --identity DEF --category CATEGORY --operation-id ID [--masters-dir PATH --context PATH --context-sha256 SHA] | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-PATH --operation-id ID","Report every native slot, names, SHA, geometry, receipt-bound K, alpha FIT and SHA-bound anatomy; optional motion_review context rechecks official frame order, adjacent/loop transitions, loop mode, decision coverage and risk notes. 3X/runtime remain separate; no resize or automatic art approval.",true,{},false,""},
  {"asset-repair",L"asset_repair.py",{L"numpy",L"PIL"},"catalog | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-OPERATION-ROOT --operation-id ID","Versioned HQ-source local repair: shared interfaces, parent slicing, bounded Lab material alignment, locked static layers, explicit key cleanup, coordinate state effects, non-heraldic eight-color flag variants and material-label diagnostics; pinned inputs and pixel/statistic replay, including bounded RGB fringe cleanup and exact low-alpha edge-key removal from a receipted raw source on the actual pixel grid; no art approval or game deployment.",true,{},false,""},
  {"tile-design",L"tile_geometry_design.py",{L"numpy",L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-OPERATION-ROOT --operation-id ID","Prepainting smooth geometry templates with pinned native crop and explicit shared port design. Creates neutral two-dimensional sampling fixtures only; no native RGB paint, image warp, art master, approval or deployment.",true,{},false,""},
  {"tile-sampling-proof",L"tile_geometry_sampling.py",{L"numpy",L"PIL"},"--context PATH --context-sha256 SHA --run PATH --run-sha256 SHA --out NEW-REPORT.json","Read-only proof of actual3X neutral fixture sampling: full RGBA and near-edge bands, every opposing functional port under four flips, interior connectivity and declared half-tile coordinate simulation. Never claims painted-art or game-runtime acceptance.",true,{},false,""},
  {"tile-interface",L"tile_interface_components.py",{L"numpy",L"PIL"},"reference|component|roi-layer --input PATH --input-sha256 SHA --outdir NEW-ROOT [--native-key KIND DEF GROUP FRAME --blueprint PATH] [--geometry-json PATH --pixels-per-native-ratio NUM DEN --native-rect X Y W H]","Prepare shape-only references, lossless shared components, or exact rational-grid native ROI layers. Rational density stays as a reduced integer ratio; no pre-resize/stretch is written, and the pinned native sampler makes the only 3X reduction. No master, art approval or deployment is created.",true,{},false,""},
  {"source-audit",L"audit_native_3x_capacity.py",{L"PIL"},"--inventory PATH --inventory-sha256 SHA --masters-root PATH --outdir PATH [--bindings PATH --bindings-sha256 SHA] [--receipt-root PATH]","Audit original-slot 3X source capacity.",true,{},false,""},
  {"motion-review",L"review_motion_sequence.py",{L"numpy",L"PIL"},"(--run-report PATH [--run-report-sha256 SHA --native-review-report PATH --native-review-report-sha256 SHA] | --collection PATH --collection-sha256 SHA) --outdir NEW-PATH","Review receipt-bound slots across real operations, including HOLD gaps and manual wrap diagnostics; display existing 3X images without writing 1X files.",true,{},false,""},
  {"town-scene",L"town_scene_composer.py",{L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir OPERATION-ROOT --operation-id ID","Compose SHA-bound full-canvas 3X layers at original XY*3; missing 3X background or layer masters are blocked.",true,{},false,""},
  {"native-review",L"review_native_batch.py",{L"numpy",L"PIL"},"--manifest PATH --manifest-sha256 SHA --outdir NEW-PATH","Compare arbitrary SHA-bound sources at their official native slot geometry using this executable's quality kernel; diagnostics never approve art.",true,{},false,""},
  {"interpolate",L"interpolate_rife_candidates.py",{L"numpy",L"PIL"},"--batch PATH --batch-sha256 SHA --outdir NEW-PATH [--timeout-seconds 1..3600]","Run receipt-bound optional RIFE interpolation candidates.",true,{},false,""},
  {"motion-prepare",L"prepare_motion_pilot.py",{L"PIL"},"--proof PATH [--frames LIST] [--keyframes LIST] --output-root PATH --spec NEW.json --native-out NEW.json [--landmarks PATH]","Freeze a small motion pilot spec from prepared source frames.",true,{},false,""},
  {"official-motion-prepare",L"prepare_official_motion_reuse.py",{L"PIL"},"--inventory PATH --inventory-sha256 SHA --h3hd-root PATH --identity ID --outdir NEW-PATH","Prepare official-frame reuse/reframe candidates.",true,{},false,""},
  {"quality-review",L"quality_review.py",{},"RETIRED; use native-review with pinned 3X sources","Retired legacy multi-scale writer; shared quality probe and numeric tests remain supported.",false,{},true,"native-review"},
  {"creature-actions",L"creature_action_plan.py",{},"--inventory PATH --inventory-sha256 SHA --identity ID [--identity ...] --out NEW.json","Freeze each creature's actual H3 action groups and complete original frame queue.",true,{},false,""},
  {"art-pipeline",L"art_pipeline_provider.py",{},"catalog|run [provider options]","Discover or invoke the pinned independent GPL H3 Art Pipeline provider.",true,{"h3-art-pipeline"},false,""},
  {"defpack",L"deftool_batch.py",{L"PIL"},"plan --job PATH --tool-dir PATH --native-validator-root PATH | check|run|verify --plan PATH","Batch legacy indexed frames through isolated H3DefTool processes and verify native DEF geometry; no RGBA master conversion.",true,{},false,""},
  {"cloud-animation",L"cloud_animation.py",{},"catalog|discover|plan|check|dry-run|submit|status|fetch [provider options]","Plan optional Meshy or Scenario cloud candidates; upload and paid submission require explicit flags, credentials stay in environment variables.",true,{},false,""},
  {"alpha-benchmark",L"transparent_interpolation_benchmark.py",{L"numpy",L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir PATH --operation-id ID","Compare pinned transparent interpolation candidates against held-out poses; missing categories or backends remain open.",true,{},false,""},
  {"blender-actions",L"blender_action_pipeline.py",{L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir PATH --operation-id ID","Sample explicitly bound armature Actions in one Blender process into actual native slots; missing rig, Action or timing bindings are rejected.",true,{"blender-worker"},false,""},
  {"creature-production",L"creature_production.py",{L"numpy",L"PIL"},"check|run|verify [--report-density] --manifest PATH --manifest-sha256 SHA --outdir PATH --operation-id ID","Validate and deliver all actual creature action slots as source-bound RGBA PNGs; --report-density is a read-only alpha>=128 body-size diagnostic and changes no delivery decision.",true,{"blender-worker"},false,""},
  {"native-alias",L"native_alias.py",{L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","Typed frozen native F0/F10 equality and SHA lineage; generate exact 3X motion reuse candidates only, never approve art, native timing or game.",true,{},false,""},
  {"sheet-intake",L"sheet_intake.py",{L"numpy",L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","Verify whole-sheet or typed prepared-partition alpha>0 ownership; report bounded K/body/foot proxies, refuse unknown mappings, and preserve all source pixels.",true,{},false,""},
  {"town-process-review",L"town_process_review.py",{L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","Review SHA-pinned original Town process slots and modern-HD standard with per-slot blockers; no process frames or art approvals.",true,{},false,""},
  {"town-master-prepare",L"town_master_prepare.py",{L"PIL"},"prepare --inventory PATH --inventory-sha256 SHA --masters-root PATH --complete-frames-root PATH --native-archive-root PATH --outdir NEW-ROOT [--identity DEF ...]","Losslessly normalize town-building masters to integer multiples of official DEF crop geometry, register visible baseline/center to the official crop, screen below-3X sources for API regeneration, and freeze a batch for the native H3 quality resampler. No source edits or pre-resampling.",true,{},false,""},
  {"town-flag-alignment",L"town_flag_alignment.py",{L"PIL"},"freeze --source-root PATH --native-frames-root PATH --native-def-root PATH --sprite-flags-info PATH --flag-grey PATH --operation-id ID --out NEW.json [--identity DEF ... --master ID=PATH ...] | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","Batch town flag alignment assistant: export official 1X-bound town masters to 3X with bottom_right registration, overlay fixed VCMI flags, and generate per-town previews plus replayable receipts. Cross-check spriteFlagsInfo against the 1999 DEF palette-index-5 owner flag mask; use that original mask when no exact VCMI key exists. No master, formal 3X, MOD, or game writes.",true,{},false,""},
  {"town-flag-review",L"town_flag_review.py",{L"PIL"},"--freeze --reference-request JSON --reference-request-sha256 SHA --sprites3x-root PATH --operation-id ID --out NEW.json [--extra-identity DEF --town-mask-config JSON] | --manifest JSON --manifest-sha256 SHA --outdir NEW-PATH [--layout JSON --layout-sha256 SHA [--export-body-translation]]","Snapshot actual SHA-pinned town bodies, VCMI flag overlays, native 1X references and optional default visitable tile; inspect poles and export review-only flag and explicit losslessly translated body candidates. Never writes a MOD or approves art.",true,{},false,""},
  {"flag-pole-audit",L"flag_pole_audit.py",{L"PIL"},"--catalog DEF [--group N --frame N] | --image PNG [--identity DEF --group N --frame N --scale N|N/D] --outdir NEW-PATH | --manifest JSON --manifest-sha256 SHA --outdir NEW-PATH | --verify-layout --manifest JSON --manifest-sha256 SHA --layout JSON --layout-sha256 SHA --outdir NEW-PATH","Read built-in 1999 Complete DEF canvas/crop/origin and actual palette-5 flag-face pixels per original frame. Flag-edge reference is not a proven pole tip; partial faces and unregistered images require review. Never modifies art or MOD.",true,{"flag-pole-reference"},false,""},
  {"logical-alpha",L"logical_alpha.py",{L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir PATH --operation-id ID","Generate explicit logical-alpha data at exact 3X with this executable's shared quality kernel; not painted artwork.",true,{},false,""},
  {"map-synth",L"map_batch_synthesizer.py",{L"numpy",L"PIL"},"check|run|verify --manifest PATH --manifest-sha256 SHA --outdir OPERATION-ROOT --operation-id ID","Synthesize deterministic 3X terrain tiles, road/river flow frames, 8-player OKLCh flags, UI button states and socket-anchored sprites.",true,{},false,""},
  {"map-source-batch",L"map_source_batch.py",{L"numpy",L"PIL"},"freeze --operation-id ID --road-prepared PATH --river-prepared PATH --inventory PATH --editor-proof PATH --seam-auditor PATH --release-root PATH --master-root PATH | check|run|verify|restore --manifest PATH --manifest-sha256 SHA --operation-id ID","Recover existing road/river high-resolution parents only through exact rational native-grid 3X sampling, original editor neighbor rules, per-port seam audit and source SHA. No warp, collar painting or source mutation; restore is byte-identical and only for slots passing measured checks.",true,{},false,""},
  {"map-material-family",L"map_material_batch.py",{L"numpy",L"PIL"},"freeze --operation-id ID --identity DEF --texture PATH [--bank-texture PATH --bank-width-native N] --inventory PATH --native-frame-root PATH --rule-proof PATH --seam-auditor PATH --release-root PATH --master-root PATH | check|run|verify|restore --manifest PATH --manifest-sha256 SHA --operation-id ID","Generate a complete 17-slot road or 13-slot river family from SHA-pinned shared high-resolution material parents and official alpha geometry, sample exact 3X, verify legal editor/native joins and shared-material RGB seams, and restore only a complete passing family. No per-frame model stretch is used.",true,{},false,""},
  {"creature-master-set",L"creature_master_set.py",{L"numpy",L"PIL"},"check|run|verify --manifest PLAN.json --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","Audit exact native DEF-slot coverage, official extraction/descriptor references and source geometry; normalize pinned genuine creature masters by uniform downscale only; keep anatomy review and native/game approval separate.",true,{},false,""},
  {"map-object-sequence",L"map_object_sequence.py",{L"PIL"},"freeze --identity DEF --group N --frames-dir FULL-CANVAS-3X-DIR --operation-id ID [--repeat-each N] | check|run|verify --manifest PATH --manifest-sha256 SHA --outdir NEW-ROOT --operation-id ID","For an official single-frame adventure-object DEF group, freeze authored full-canvas 3X frames and the 1999 native authority; emit matched Sprites/Sprites3x sequences plus static 1X fallbacks into an isolated MOD-overlay candidate. Never edits masters, formal 3X, MODs, or the original DEF/LOD; human animation review remains required.",true,{},false,""},
  {"map-sprite-mod",L"map_sprite_mod_packager.py",{L"PIL"},"freeze --identity ID --batch PATH --batch-sha256 SHA --verify-report PATH --verify-report-sha256 SHA --operation-id ID --candidate-root NEW-PATH --mod-id ID --out NEW.json | check|run --manifest PATH --manifest-sha256 SHA --operation-id ID | verify --manifest PATH --manifest-sha256 SHA --operation-id ID --receipt PATH --receipt-sha256 SHA","Package a VERIFIED native-authority 3X mounted-hero expansion as an isolated VCMI MOD candidate. Recheck the 1999 inventory, identity checkpoint, exact 45 slots, full canvas ×3, crop/origin Alpha bounds, and source/output receipts; copy PNG bytes unchanged. No crop, pad, centering, resampling, install, or overwrite.",true,{},false,""},
  {"sprites3x-mod",L"sprites3x_mod_sync.py",{L"numpy",L"PIL"},"freeze --identity DEF --sprites3x-root PATH --batch PATH --batch-sha256 SHA --verify-report PATH --verify-report-sha256 SHA --operation-id ID --outdir NEW-ROOT | check|run --manifest PATH --manifest-sha256 SHA --operation-id ID | verify --manifest PATH --manifest-sha256 SHA --operation-id ID --receipt PATH --receipt-sha256 SHA","Gate static VCMI Sprites3x sequences against successful full-canvas 3X expansion using static-structure-dynamic-fx-v2, static-base-dynamic-overlay-v1/v2 or the sealed authored-effect-ROI contract. BASE contracts validate frame0 followed by the current frame; v2 checks official fixed-pixel stability and per-frame FX size/anchor, while authored ROI limits changes to a SHA-pinned effect region and checks brightness/size/anchor pulses. A frame or descriptor drift blocks check as REPROCESS_REQUIRED; run rebuilds an isolated candidate from verified source bytes. Never overwrites or installs the live MOD.",true,{},false,""},
  {"hero-canvas-gate",L"hero_canvas_gate.py",{L"PIL"},"--batch PATH --batch-sha256 SHA --mode check|verify|run [--outdir NEW-DIR] [--report NEW.json]","Mandatory post-export full-canvas x3 + origin x3 gate for mounted-hero AH* DEFs against the built-in 1999 Complete DEF geometry. check/verify are read-only and fail closed on crop x3 collapse, wrong size, or alpha outside the official crop; run losslessly re-pads collapsed crop x3 or clamps only <=64 outside-crop pixels with alpha <=8 into a new isolated candidate directory. Larger/visible out-of-crop artwork remains blocked. CLI and GUI expand/expand-verify run the read-only gate automatically for every AH* export batch. Never edits masters, formal 3X, MOD, or the original DEF/LOD in place.",true,{"flag-pole-reference"},false,""},
  {"creature-sequence-qa",L"creature_sequence_qa.py",{L"numpy",L"PIL"},"inspect <unit-dir> [--identity ID] [--expected-keyframes N] | stabilize <raw-dir> <target-dir> [--identity ID] [--backup-dir PATH]","Run full creature 5-frame sequence quality gate (1999 naming, alpha/transparency, rigid registration, scale/ground-anchor consistency, novel color, banned head nod/shake, VCMI animation manifest).",true,{},false,""}
 }};
 return commands;
}
inline Json companion_capabilities(){
 Json native=Json::array();
 for(auto item:std::array<std::pair<const char*,const char*>,29>{{
  {"help","Show command help."},{"capabilities","Emit this machine-readable command directory."},
  {"info","Report version, hardware, defaults and native capabilities."},{"selftest","Run native device/kernel checks."},
   {"ui-smoke","Check native UI construction without user interaction."},{"checkerboard-alpha","Remove an edge-confirmed Gemini checkerboard into a new RGBA PNG evidence directory."},{"plan","Freeze one or more source batches into a plan."},
  {"check","Preflight a plan without writing image outputs."},{"approve","Record explicit approval for one exact master and crop contract."},
  {"run","Export approved master tiers."},{"verify","Verify existing export outputs and receipts."},
  {"bench","Run native resampling benchmarks."},{"expand-plan","Freeze a CPU master-expansion batch; static-FX v2 requires a complete official 1X frame inventory, one shared group transform, a measurable static base, and an official-bounded FX motion envelope."},
  {"expand-check","Preflight a master-expansion batch."},{"expand","Run a master-expansion batch."},
  {"expand-verify","Verify a master-expansion batch."},{"motion-plan","Freeze an explicit motion spec into a batch."},
  {"motion-check","Preflight a motion batch."},{"motion-probe","Probe motion dependencies and estimates without writing frames."},
  {"motion-run","Generate explicit mesh/keyframe motion candidates."},{"motion-verify","Verify generated motion candidates."},
  {"review-render","Render caller-supplied review geometry at 1X/3X with the existing quality kernel; no official geometry or art approval is inferred."},
  {"logical-alpha-render","Render explicit logical-alpha data from 1X to exact 3X with the same quality kernel; caller owns original-LOD provenance."},
  {"skill-variant-check","Check official-named skill mother derivatives."},{"skill-variant-export","Derive the distinct skill specifications from existing AI mothers."},{"skill-variant-verify","Replay official-named skill derivatives."},
  {"map-artifact-check","Check official object IDs, DEF geometry, source pins and transparent AI mothers."},{"map-artifact-export","Compose proportional AI mothers with the exact official ground-shadow mask and animation glints."},{"map-artifact-verify","Independently replay map artifact mother composition and verify pixels."}
 }}) native.push_back({{"name",item.first},{"purpose",item.second},{"runtime","native-exe"}});
 Json companions=Json::array();
 for(const auto& item:companion_commands()){
  Json modules=Json::array();for(const auto& module:item.modules)modules.push_back(utf8(module));
  companions.push_back({{"name",item.name},{"script",utf8(item.script)},{"usage",item.usage},{"purpose",item.purpose},
   {"runtime","external-python"},{"qualified_runtime_payload",item.frozenRuntime},{"required_python_modules",modules},
   {"status",item.retired?"retired":"active"},{"replacement",item.replacement},{"runtime_resources",item.runtimeResources},
   {"wrapper_options",Json::array({"--tool-root PATH","--python EXE","--companion-timeout-seconds 1..86400 (default 3600)"})}});
 }
 return {{"schema","h3.tier-export.capabilities.v1"},{"version",VERSION},
  {"native_commands",native},{"companion_commands",companions},{"companion_runtime_manifest_sha256",H3_COMPANION_RUNTIME_SHA},
  {"native_mesh_interpolation","Explicit correspondence-driven image geometry; no learned model inference."},
  {"rife","Optional external executable and pinned model files are required by interpolate batches."},
  {"automatic_art_approval",false},{"game_deployment",false}};
}

inline int companion_error(const std::string& status,const std::string& code,const std::string& message,
                           const std::string& dependency="",const std::wstring& file=L""){
 Json result={{"schema","h3.tier-export.companion-error.v1"},{"status",status},{"code",code},{"error",message}};
 if(!dependency.empty())result["dependency"]=dependency;
 if(!file.empty())result["path"]=utf8(file);
 std::cerr<<result.dump()<<"\n";
 return 2;
}

inline std::wstring companion_quote_argument(const std::wstring& value){
 if(!value.empty()&&value.find_first_of(L" \t\n\v\"")==std::wstring::npos)return value;
 std::wstring quoted=L"\"";size_t slashes=0;
 for(wchar_t c:value){
  if(c==L'\\'){++slashes;continue;}
  if(c==L'\"'){quoted.append(slashes*2+1,L'\\');quoted.push_back(c);slashes=0;continue;}
  quoted.append(slashes,L'\\');slashes=0;quoted.push_back(c);
 }
 quoted.append(slashes*2,L'\\');quoted.push_back(L'\"');return quoted;
}

inline DWORD companion_run_process(const std::wstring& executable,const std::vector<std::wstring>& arguments,
                                   DWORD& winError,DWORD timeoutMs){
 std::wstring command=companion_quote_argument(executable);
 for(const auto& argument:arguments){command.push_back(L' ');command+=companion_quote_argument(argument);}
 std::vector<wchar_t> mutableCommand(command.begin(),command.end());mutableCommand.push_back(L'\0');
 STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESTDHANDLES;
 startup.hStdInput=GetStdHandle(STD_INPUT_HANDLE);startup.hStdOutput=GetStdHandle(STD_OUTPUT_HANDLE);startup.hStdError=GetStdHandle(STD_ERROR_HANDLE);
 PROCESS_INFORMATION process{};
 HANDLE job=CreateJobObjectW(nullptr,nullptr);
 if(!job){winError=GetLastError();return 0;}
 JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
 limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
 if(!SetInformationJobObject(job,JobObjectExtendedLimitInformation,&limits,sizeof(limits))){
  winError=GetLastError();CloseHandle(job);return 0;
 }
 if(!CreateProcessW(executable.c_str(),mutableCommand.data(),nullptr,nullptr,TRUE,CREATE_SUSPENDED,nullptr,nullptr,&startup,&process)){
  winError=GetLastError();CloseHandle(job);return 0;
 }
 if(!AssignProcessToJobObject(job,process.hProcess)){
  winError=GetLastError();TerminateProcess(process.hProcess,1);CloseHandle(process.hThread);CloseHandle(process.hProcess);CloseHandle(job);return 0;
 }
 if(ResumeThread(process.hThread)==(DWORD)-1){
  winError=GetLastError();CloseHandle(process.hThread);CloseHandle(job);CloseHandle(process.hProcess);return 0;
 }
 CloseHandle(process.hThread);
 DWORD waited=WaitForSingleObject(process.hProcess,timeoutMs);
 if(waited!=WAIT_OBJECT_0){
  winError=waited==WAIT_TIMEOUT?ERROR_TIMEOUT:GetLastError();
  CloseHandle(job);WaitForSingleObject(process.hProcess,5000);CloseHandle(process.hProcess);return 0;
 }
 DWORD exitCode=1;
 if(!GetExitCodeProcess(process.hProcess,&exitCode)){
  winError=GetLastError();CloseHandle(process.hProcess);CloseHandle(job);return 0;
 }
 CloseHandle(process.hProcess);CloseHandle(job);winError=ERROR_SUCCESS;return exitCode;
}

struct CompanionRuntime {std::wstring executable;bool pyLauncher=false;};
inline bool companion_file(const fs::path& path){std::error_code error;return fs::is_regular_file(path,error)&&!error;}
inline fs::path companion_product_root(const fs::path& toolRoot){
 if(_wcsicmp(toolRoot.filename().c_str(),L"dist")==0){
  auto product=toolRoot.parent_path();
  if(companion_file(product/L"src"/L"main.cpp")&&fs::is_directory(product/L"scripts"))return product;
 }
 return toolRoot;
}

inline fs::path companion_tool_root(const std::wstring& explicitRoot,const CompanionCommand& command){
 auto valid=[&](const fs::path& root){
  return fs::is_directory(root)&&
    ((companion_file(root/L"CMakeLists.txt")||companion_file(root/L"src"/L"main.cpp"))&&
     companion_file(root/L"scripts"/command.script)||
     companion_file(root/path(std::string(".runtime-")+H3_COMPANION_RUNTIME_SHA)/L"runtime-manifest.json"));
 };
 if(!explicitRoot.empty()){
  auto root=fs::absolute(fs::path(explicitRoot)).lexically_normal();
  need(valid(root),"COMPANION_TOOL_ROOT_INVALID: expected a tool root with src/main.cpp or CMakeLists.txt and scripts/"+utf8(command.script));
  return root;
 }
 std::array<wchar_t,32768> buffer{};DWORD length=GetModuleFileNameW(nullptr,buffer.data(),(DWORD)buffer.size());
 need(length>0&&length<(DWORD)buffer.size(),"COMPANION_EXECUTABLE_PATH_UNAVAILABLE");
 fs::path root=fs::path(std::wstring(buffer.data(),length)).parent_path();
 for(int depth=0;depth<9&&!root.empty();++depth,root=root.parent_path())if(valid(root))return root;
 throw std::runtime_error("COMPANION_TOOL_ROOT_NOT_FOUND: pass --tool-root PATH to a H3TierExport source tree");
}

inline std::wstring companion_search_path(const std::wstring& name){
 std::array<wchar_t,32768> buffer{};DWORD length=SearchPathW(nullptr,name.c_str(),nullptr,(DWORD)buffer.size(),buffer.data(),nullptr);
 if(length==0||length>=(DWORD)buffer.size())return L"";
 return std::wstring(buffer.data(),length);
}

inline CompanionRuntime companion_python(const std::wstring& requested,const fs::path& root){
 if(!requested.empty()){
  fs::path candidate(requested);if(companion_file(candidate))return {fs::absolute(candidate).lexically_normal().wstring(),_wcsicmp(candidate.filename().c_str(),L"py.exe")==0};
  auto located=companion_search_path(requested);if(!located.empty())return {located,_wcsicmp(fs::path(located).filename().c_str(),L"py.exe")==0};
  return {};
 }
 for(const auto& candidate:{root/L"runtime"/L"python"/L"python.exe",companion_product_root(root)/L"venv-win"/L"Scripts"/L"python.exe",root/L"venv-win"/L"Scripts"/L"python.exe",root/L".venv"/L"Scripts"/L"python.exe",root/L"venv"/L"Scripts"/L"python.exe"})
  if(companion_file(candidate))return {candidate.wstring(),false};
 auto python=companion_search_path(L"python.exe");if(!python.empty())return {python,false};
 auto launcher=companion_search_path(L"py.exe");if(!launcher.empty())return {launcher,true};
 return {};
}

inline DWORD companion_run_python(const CompanionRuntime& runtime,const std::vector<std::wstring>& args,DWORD& winError,DWORD timeoutMs){
 std::vector<std::wstring> command;
 if(runtime.pyLauncher)command.push_back(L"-3");
 command.insert(command.end(),args.begin(),args.end());
 return companion_run_process(runtime.executable,command,winError,timeoutMs);
}

inline fs::path companion_executable(){
 std::array<wchar_t,32768> buffer{};DWORD length=GetModuleFileNameW(nullptr,buffer.data(),(DWORD)buffer.size());
 need(length>0&&length<(DWORD)buffer.size(),"RUNTIME_EXECUTABLE_PATH_UNAVAILABLE");
 return clean_path(fs::path(std::wstring(buffer.data(),length)));
}

inline bool companion_candidate(const fs::path& executable){
 return executable.parent_path().filename()==L"candidate"&&executable.parent_path().parent_path().filename()==L"build";
}

inline fs::path companion_frozen_script(const fs::path& root,const CompanionCommand& command,const fs::path& executable,std::vector<Handle>& pins){
 auto current=root/L"dist"/L"H3TierExport.exe";
 auto portable=root/L"H3TierExport.exe";
 // Historical publication paths are metadata; validate the actual execution
 // files below without probing an unrelated current executable for an archive.
 bool isCurrent=_wcsicmp(executable.c_str(),current.c_str())==0;
 bool isPortable=_wcsicmp(executable.c_str(),portable.c_str())==0;
 bool rootIsDist=_wcsicmp(root.filename().c_str(),L"dist")==0;
 auto distRoot=rootIsDist?root:root/L"dist";
 auto archive=distRoot/L"archive";
 need(isCurrent||isPortable||(executable.filename()==L"H3TierExport.exe"&&_wcsicmp(executable.parent_path().parent_path().c_str(),archive.c_str())==0),"RUNTIME_RENDERER_LOCATION_INVALID");
 auto pinned=[&](const fs::path& file,std::uint64_t limit){
  try{pins.push_back(open_read(file));}
  catch(const std::runtime_error& error){throw std::runtime_error("RUNTIME_MISSING_OR_REPARSE: "+std::string(error.what()));}
  auto& handle=pins.back();LARGE_INTEGER size{};
  // Empty provider modules are legal. This reader is local to qualified runtime
  // files; image/JSON readers retain their existing nonempty-input contract.
  need(GetFileSizeEx(handle,&size)&&size.QuadPart>=0&&(std::uint64_t)size.QuadPart<=limit,"RUNTIME_FILE_SIZE_INVALID");
  std::vector<std::uint8_t> raw((size_t)size.QuadPart);size_t offset=0;
  while(offset<raw.size()){
   DWORD got=0,ask=(DWORD)std::min<size_t>(raw.size()-offset,4*1024*1024);
   need(ReadFile(handle,raw.data()+offset,ask,&got,nullptr)&&got==ask,"RUNTIME_FILE_READ_FAILED");offset+=got;
  }
  return raw;
 };
 auto directory=executable.parent_path();
 auto releaseRaw=pinned(directory/L"H3TierExport-release.json",8*1024*1024);
 if(isCurrent)need(pinned(root/L"reports"/L"current"/L"release.json",8*1024*1024)==releaseRaw,"RELEASE_REPORT_COPY_MISMATCH");
 auto release=parse_json(releaseRaw);auto executableRaw=pinned(executable,32*1024*1024);auto exeSha=sha(executableRaw);
 need(release.at("schema")=="h3.tier-export.release.v2"&&release.at("exe_sha256")==exeSha,"RELEASE_EXE_IDENTITY_MISMATCH");
 auto buildRaw=pinned(directory/L"H3TierExport-build-receipt.json",32*1024*1024),testsRaw=pinned(directory/L"H3TierExport-tests-receipt.json",64*1024*1024);
 need(release.at("build_receipt_sha256")==sha(buildRaw)&&release.at("test_receipt_sha256")==sha(testsRaw),"RUNTIME_FROZEN_RECEIPT_DRIFT");
 auto build=parse_json(buildRaw),tests=parse_json(testsRaw);
 need(build.at("schema")=="h3.tier-export.build.v2"&&build.at("passed")==true&&build.at("source_current")==true&&build.at("executable").at("sha256")==exeSha,"RUNTIME_BUILD_IDENTITY_MISMATCH");
 need(tests.at("schema")=="h3.tier-export.test-suite.v2"&&tests.at("passed")==true&&tests.at("source_current")==true&&tests.at("exe_sha256")==exeSha&&tests.at("build_receipt_sha256")==sha(buildRaw),"RUNTIME_TEST_BUILD_IDENTITY_MISMATCH");
 need(tests.at("results").is_array()&&!tests.at("results").empty(),"RUNTIME_TEST_RESULTS_MISSING");
 for(const auto& result:tests.at("results"))need(result.at("exit_code").is_number_integer()&&result.at("exit_code")==0,"RUNTIME_TEST_RESULT_FAILED");
 for(auto [leaf,digest]:std::vector<std::pair<std::wstring,std::string>>{{L"H3TierExport.exe",exeSha},{L"H3TierExport-build-receipt.json",sha(buildRaw)},{L"H3TierExport-tests-receipt.json",sha(testsRaw)}}){
  int count=0;for(const auto& row:release.at("files"))count+=(row.at("path")==str(directory/leaf)||row.at("path")==utf8(leaf)||row.at("path")==str(distRoot/leaf))&&row.at("sha256")==digest;
  need(count==1,"RELEASE_FILE_IDENTITY_MISMATCH");
 }
 auto binding=build.at("companion_runtime");need(binding==release.at("companion_runtime")&&binding.at("schema")=="h3.tier-export.companion-runtime-binding.v1"&&binding.at("manifest_sha256")==H3_COMPANION_RUNTIME_SHA,"RUNTIME_COMPILED_BINDING_MISMATCH");
 std::string leaf=std::string(".runtime-")+H3_COMPANION_RUNTIME_SHA;need(binding.at("directory")==leaf,"RUNTIME_DIRECTORY_ID_MISMATCH");
 auto payload=root/path(leaf);auto manifestRaw=pinned(payload/L"runtime-manifest.json",1024*1024);need(sha(manifestRaw)==H3_COMPANION_RUNTIME_SHA,"RUNTIME_MANIFEST_DRIFT");auto manifest=parse_json(manifestRaw);
 need(manifest.is_object()&&manifest.size()==5&&manifest.contains("schema")&&manifest.contains("entrypoints")&&manifest.contains("entrypoint_resources")&&manifest.contains("resources")&&manifest.contains("files")&&manifest.at("schema")=="h3.tier-export.companion-runtime.v2","RUNTIME_MANIFEST_SCHEMA_INVALID");
 Json entries=Json::object(),edges=Json::object(),resources=Json::object();
 for(const auto& entry:companion_commands())if(entry.frozenRuntime&&!entry.retired){entries[entry.name]=utf8(entry.script);edges[entry.name]=entry.runtimeResources;}
 for(const auto& resource:companion_runtime_resources())resources[resource.id]={{"path",resource.path},{"source_path",resource.sourcePath},{"role",resource.role},{"tree",resource.tree}};
 need(manifest.at("entrypoints")==entries&&entries.at(command.name)==utf8(command.script),"RUNTIME_ENTRYPOINT_REGISTRY_MISMATCH");
 need(manifest.at("entrypoint_resources")==edges&&manifest.at("resources")==resources,"RUNTIME_RESOURCE_REGISTRY_MISMATCH");
 std::map<std::string,Json> sources;for(const auto& row:build.at("sources")){auto name=row.at("path").get<std::string>();std::replace(name.begin(),name.end(),'\\','/');need(sources.emplace(name,row).second,"RUNTIME_BUILD_SOURCE_DUPLICATE");}
 auto folded=[](std::wstring value){std::transform(value.begin(),value.end(),value.begin(),[](wchar_t c){return (wchar_t)std::towlower(c);});return value;};
 auto relative=[&](const std::string& value){
  need(!value.empty()&&value.front()!='/'&&value.find('\\')==std::string::npos,"RUNTIME_PATH_INVALID");
  size_t at=0;
  while(at<value.size()){
   size_t end=value.find('/',at);if(end==std::string::npos)end=value.size();auto part=value.substr(at,end-at);
   need(!part.empty()&&part!="."&&part!=".."&&part.back()!='.'&&part.back()!=' '&&std::all_of(part.begin(),part.end(),[](unsigned char c){return c>=32&&std::string("<>:\"|?*").find((char)c)==std::string::npos;}),"RUNTIME_PATH_INVALID");
   auto stem=part.substr(0,part.find('.'));std::transform(stem.begin(),stem.end(),stem.begin(),[](unsigned char c){return (char)std::toupper(c);});
   need(stem!="CON"&&stem!="PRN"&&stem!="AUX"&&stem!="NUL"&&!(stem.size()==4&&(stem.substr(0,3)=="COM"||stem.substr(0,3)=="LPT")&&stem[3]>='1'&&stem[3]<='9'),"RUNTIME_PATH_INVALID");
   need(end==value.size()||end+1<value.size(),"RUNTIME_PATH_INVALID");at=end+1;
  }
  need(path(value).is_relative()&&path(value).generic_wstring()==wide(value),"RUNTIME_PATH_INVALID");
 };
 auto flat=[](const std::string& value){return !value.empty()&&std::all_of(value.begin(),value.end(),[](unsigned char c){return c>='a'&&c<='z'||c>='A'&&c<='Z'||c>='0'&&c<='9'||c=='_'||c=='-'||c=='.';});};
 const auto& files=manifest.at("files");need(files.is_array()&&!files.empty()&&files.size()<=512,"RUNTIME_FILE_COUNT_INVALID");
 std::map<std::wstring,std::pair<std::wstring,bool>> names,sourceNames;
 auto admit=[&](auto& space,const std::string& name){
  relative(name);auto item=path(name);
  for(bool file=true;!item.empty();item=item.parent_path(),file=false){
   auto exact=item.generic_wstring();auto key=folded(exact);auto found=space.find(key);
   need(found==space.end()||(!file&&found->second==std::make_pair(exact,false)),"RUNTIME_FILE_NAME_INVALID");
   space[key]={exact,file};
  }
 };
 admit(names,"runtime-manifest.json");std::uint64_t total=0;
 std::map<std::string,std::string> roles;std::map<std::string,size_t> resourceCounts;
 for(const auto& row:files){
  need(row.is_object()&&row.size()==5&&row.contains("path")&&row.contains("source_path")&&row.contains("role")&&row.contains("bytes")&&row.contains("sha256"),"RUNTIME_FILE_ROW_INVALID");
  auto name=row.at("path").get<std::string>(),source=row.at("source_path").get<std::string>(),role=row.at("role").get<std::string>();
  admit(names,name);admit(sourceNames,source);roles[name]=role;
  const CompanionRuntimeResource* owner=nullptr;
  for(const auto& resource:companion_runtime_resources())if(resource.tree?name.rfind(resource.path+"/",0)==0:name==resource.path){need(owner==nullptr,"RUNTIME_RESOURCE_PATH_CONFLICT");owner=&resource;}
  if(role=="python-module"||role=="configuration"){
   need(!owner&&flat(name)&&((role=="python-module"&&name.size()>3&&name.substr(name.size()-3)==".py"&&source=="scripts/"+name)||(role=="configuration"&&name=="H3TierExport-workspace.json"&&source=="resources/"+name)),"RUNTIME_SOURCE_PATH_INVALID");
  }else{
   need(owner&&role==owner->role&&source==owner->sourcePath+name.substr(owner->path.size()),"RUNTIME_RESOURCE_FILE_BINDING_INVALID");++resourceCounts[owner->id];
  }
  need(row.at("bytes").is_number_integer()&&row.at("bytes")>=0&&row.at("bytes")<=32*1024*1024&&row.at("sha256").is_string(),"RUNTIME_FILE_ROW_INVALID");
  auto digest=row.at("sha256").get<std::string>();need(digest.size()==64&&std::all_of(digest.begin(),digest.end(),[](unsigned char c){return c>='0'&&c<='9'||c>='a'&&c<='f';}),"RUNTIME_FILE_ROW_INVALID");
  total+=row.at("bytes").get<std::uint64_t>();need(total<=32*1024*1024,"RUNTIME_TOTAL_MISMATCH");
  auto raw=pinned(payload/path(name),32*1024*1024);need(row.at("bytes")==raw.size()&&row.at("sha256")==sha(raw),"RUNTIME_FILE_DRIFT: "+name);
  need(sources.contains(source)&&sources.at(source).at("sha256")==row.at("sha256")&&sources.at(source).at("bytes")==row.at("bytes"),"RUNTIME_SOURCE_BINDING_MISMATCH");
 }
 need(binding.at("file_count")==files.size()&&binding.at("bytes")==total&&total<=32*1024*1024,"RUNTIME_TOTAL_MISMATCH");
 for(const auto& entry:entries.items())need(roles.contains(entry.value().get<std::string>())&&roles.at(entry.value().get<std::string>())=="python-module","RUNTIME_ENTRYPOINT_MISSING");
 need(roles.contains("H3TierExport-workspace.json")&&roles.at("H3TierExport-workspace.json")=="configuration","RUNTIME_CONFIGURATION_MISSING");
 for(const auto& resource:companion_runtime_resources())need(resourceCounts[resource.id]>0,"RUNTIME_RESOURCE_FILE_MISSING");
 for(const auto& entry:fs::recursive_directory_iterator(payload)){
  auto exact=entry.path().lexically_relative(payload).generic_wstring();auto found=names.find(folded(exact));
  auto attrs=GetFileAttributesW(entry.path().c_str());need(attrs!=INVALID_FILE_ATTRIBUTES&&!(attrs&FILE_ATTRIBUTE_REPARSE_POINT),"RUNTIME_MISSING_OR_REPARSE");
  need(found!=names.end()&&found->second.first==exact&&found->second.second==!(attrs&FILE_ATTRIBUTE_DIRECTORY),"RUNTIME_EXTRA_FILE");names.erase(found);
 }
 need(names.empty(),"RUNTIME_FILE_MISSING");
 return payload/command.script;
}

inline int run_companion_command(const std::string& name,const std::vector<std::string>& inputArgs){
 auto found=std::find_if(companion_commands().begin(),companion_commands().end(),[&](const auto& command){return command.name==name;});
 if(found==companion_commands().end())return companion_error("UNKNOWN_COMMAND","UNKNOWN_COMPANION_COMMAND","Unknown companion command: "+name);
 if(found->retired){
  std::cerr<<Json({{"schema","h3.tier-export.companion-error.v1"},{"status","RETIRED"},{"code","COMPANION_RETIRED"},{"command",name},{"replacement",found->replacement},{"error",found->purpose}}).dump()<<"\n";
  return 2;
 }
 std::wstring rootOption,pythonOption;std::string timeoutOption="3600";
 std::vector<std::wstring> forwarded;bool sawRoot=false,sawPython=false,sawTimeout=false;
 for(size_t i=0;i<inputArgs.size();++i){
  const auto& value=inputArgs[i];
  if((name=="native-review"||name=="sheet-intake"||name=="native-alias"||name=="town-process-review"||name=="town-flag-alignment"||name=="motion-review"||name=="town-scene"||name=="map-synth"||name=="map-source-batch"||name=="map-material-family"||name=="asset-repair"||name=="scale-draft"||name=="scale-audit"||name=="scale-layout")&&(value=="--renderer"||value=="--renderer-sha256"||
      value.rfind("--renderer=",0)==0||value.rfind("--renderer-sha256=",0)==0))
   return companion_error("INVALID_ARGUMENTS","RENDERER_OVERRIDE_REJECTED","This command uses the invoking executable; renderer overrides are available only through the direct Python script");
  const std::string timeoutPrefix="--companion-timeout-seconds=";
  if(value=="--companion-timeout-seconds"||value.rfind(timeoutPrefix,0)==0){
   if(sawTimeout)return companion_error("INVALID_ARGUMENTS","DUPLICATE_COMPANION_TIMEOUT","wrapper options may appear only once");
   sawTimeout=true;
   if(value=="--companion-timeout-seconds"){
    if(i+1>=inputArgs.size())return companion_error("INVALID_ARGUMENTS","COMPANION_TIMEOUT_VALUE_REQUIRED","--companion-timeout-seconds requires seconds");
    timeoutOption=inputArgs[++i];
   }else timeoutOption=value.substr(timeoutPrefix.size());
   continue;
  }
  if(value=="--tool-root"||value=="--python"){
   bool root=value=="--tool-root";if(i+1>=inputArgs.size())return companion_error("INVALID_ARGUMENTS",root?"TOOL_ROOT_VALUE_REQUIRED":"PYTHON_VALUE_REQUIRED",value+" requires a path");
   bool& seen=root?sawRoot:sawPython;if(seen)return companion_error("INVALID_ARGUMENTS",root?"DUPLICATE_TOOL_ROOT":"DUPLICATE_PYTHON","wrapper options may appear only once");seen=true;
   (root?rootOption:pythonOption)=wide(inputArgs[++i]);continue;
  }
  if(value.rfind("--tool-root=",0)==0){if(sawRoot)return companion_error("INVALID_ARGUMENTS","DUPLICATE_TOOL_ROOT","wrapper options may appear only once");sawRoot=true;rootOption=wide(value.substr(12));continue;}
  if(value.rfind("--python=",0)==0){if(sawPython)return companion_error("INVALID_ARGUMENTS","DUPLICATE_PYTHON","wrapper options may appear only once");sawPython=true;pythonOption=wide(value.substr(9));continue;}
  forwarded.push_back(wide(value));
 }
 if((sawRoot&&rootOption.empty())||(sawPython&&pythonOption.empty()))return companion_error("INVALID_ARGUMENTS","EMPTY_WRAPPER_PATH","--tool-root and --python need non-empty paths");
 DWORD timeoutMs=0;
 try{
  size_t used=0;unsigned long seconds=std::stoul(timeoutOption,&used);
  if(used!=timeoutOption.size()||seconds<1||seconds>86400||timeoutOption.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("range");
  timeoutMs=(DWORD)seconds*1000;
 }catch(const std::exception&){return companion_error("INVALID_ARGUMENTS","COMPANION_TIMEOUT_INVALID","--companion-timeout-seconds must be 1..86400");}
 fs::path root;
 try{root=companion_tool_root(rootOption,*found);}catch(const std::exception& error){return companion_error("DEPENDENCY_MISSING","TOOL_ROOT_UNAVAILABLE",error.what(),"H3TierExport source scripts");}
 if((name=="native-review"||name=="sheet-intake"||name=="native-alias"||name=="town-process-review"||name=="town-flag-alignment"||name=="motion-review"||name=="town-scene"||name=="map-synth"||name=="map-source-batch"||name=="map-material-family"||name=="asset-repair"||name=="scale-draft"||name=="scale-audit"||name=="scale-layout")&&
    std::any_of(forwarded.begin(),forwarded.end(),[](const auto& arg){return arg==L"--help"||arg==L"-h";})){
  std::cout<<"Usage: H3TierExport.exe "<<name<<" "<<found->usage<<"\n";
  std::cout<<"The invoking EXE supplies renderer identity automatically.\n";
   const auto& leaf=name;
   auto outputRoot=companion_product_root(root);
   std::cout<<"Output root: "<<str(outputRoot/L"build"/L"experiments"/path(leaf))<<"\n";
  std::cout<<"Use a new direct child of this output root; archive necessary results in the owner material directory after verification.\n";
  if(name=="town-scene"||name=="map-synth"||name=="map-source-batch"||name=="map-material-family"||name=="asset-repair"||name=="scale-draft"||name=="scale-audit"||name=="scale-layout") std::cout<<"The output leaf must equal --operation-id. Reuse or verify the same operation without overwriting divergent files. check writes no files.\n";
  std::cout<<"Diagnostic output grants no art, native timing or game approval.\n";
  return 0;
 }
 auto script=root/L"scripts"/found->script;
 std::vector<Handle> runtimePins;
 bool frozenDispatch=false;
 if(found->frozenRuntime){
  try{auto executable=companion_executable();if(!companion_candidate(executable)){script=companion_frozen_script(root,*found,executable,runtimePins);frozenDispatch=true;}}
  catch(const std::exception& error){return companion_error("BLOCKED","RELEASE_RUNTIME_INVALID",error.what());}
 }
 if(!companion_file(script))return companion_error("DEPENDENCY_MISSING","COMPANION_SCRIPT_MISSING","Companion script is unavailable","script",script.wstring());
 if((name=="creature-auto-pose"||name=="hero-mounted-pose")&&!sawPython){
  auto posePython=companion_product_root(root)/L"build"/L"creature-auto-pose-provider"/L"venv"/L"Scripts"/L"python.exe";
  if(companion_file(posePython))pythonOption=posePython.wstring();
 }
 auto runtime=companion_python(pythonOption,root);
 if(runtime.executable.empty())return companion_error("DEPENDENCY_MISSING","PYTHON_UNAVAILABLE","Install Python or pass --python PATH","python 3",pythonOption);
 std::wstring moduleCheck=L"import importlib.util,json,sys; missing=[]; "+
  std::wstring(L"[missing.append(name) for name in sys.argv[1:] if importlib.util.find_spec(name) is None]; ")+
  L"print(json.dumps({'schema':'h3.tier-export.companion-error.v1','status':'DEPENDENCY_MISSING','code':'PYTHON_MODULE_MISSING','missing_modules':missing}),file=sys.stderr) if missing else None; sys.exit(2 if missing else 0)";
 std::vector<std::wstring> preflight={L"-B",L"-X",L"utf8",L"-c",moduleCheck};
 preflight.insert(preflight.end(),found->modules.begin(),found->modules.end());DWORD winError=0;
 DWORD preflightCode=companion_run_python(runtime,preflight,winError,20000);
 if(winError==ERROR_TIMEOUT){companion_error("TIMED_OUT","PYTHON_PROBE_TIMED_OUT","Python dependency check exceeded 20 seconds; its owned process tree was stopped");return 124;}
 if(winError!=ERROR_SUCCESS)return companion_error("DEPENDENCY_MISSING","PYTHON_START_FAILED","Could not start Python dependency check; Win32 error="+std::to_string(winError),"python",runtime.executable);
 if(preflightCode!=0)return (int)preflightCode;
 std::vector<std::wstring> childArgs={L"-B",L"-X",L"utf8",script.wstring()};
 if(frozenDispatch)childArgs.insert(childArgs.begin(),L"-E");
 childArgs.insert(childArgs.end(),forwarded.begin(),forwarded.end());
 if(name=="native-review"||name=="logical-alpha"||name=="sheet-intake"||name=="native-alias"||name=="town-process-review"||name=="town-flag-alignment"||name=="motion-review"||name=="town-scene"||name=="map-synth"||name=="map-source-batch"||name=="map-material-family"||name=="asset-repair"||name=="scale-draft"||name=="scale-audit"||name=="scale-layout"){
  std::wstring executable(32768,L'\0');DWORD size=GetModuleFileNameW(nullptr,executable.data(),(DWORD)executable.size());
  if(!size||size>=executable.size())return companion_error("DEPENDENCY_MISSING","RENDERER_PATH_UNAVAILABLE","Cannot identify the invoking executable");
  executable.resize(size);
  auto actual=clean_path(fs::path(executable));
  childArgs.insert(childArgs.end(),{L"--renderer",actual.wstring(),L"--renderer-sha256",wide(sha(read_all(actual)))});
 }
 DWORD childCode=companion_run_python(runtime,childArgs,winError,timeoutMs);
 if(winError==ERROR_TIMEOUT){companion_error("TIMED_OUT","COMPANION_TIMED_OUT","Companion exceeded its deadline; its owned process tree was stopped and partial artifacts require inspection");return 124;}
 if(winError!=ERROR_SUCCESS)return companion_error("DEPENDENCY_MISSING","COMPANION_START_FAILED","Could not start companion process; Win32 error="+std::to_string(winError),"python",runtime.executable);
 return (int)childCode;
}

inline bool expansion_batch_contains_mounted_hero(const Json& batch){
 if(!batch.is_object()||!batch.contains("items")||!batch.at("items").is_array())return false;
 for(const auto& item:batch.at("items")){
  if(!item.is_object()||!item.contains("source")||!item.at("source").is_string())continue;
  auto identity=path(item.at("source").get<std::string>()).parent_path().filename().wstring();
  if(identity.size()>=2&&((identity[0]==L'A'||identity[0]==L'a')&&(identity[1]==L'H'||identity[1]==L'h')))return true;
 }
 return false;
}

inline Json apply_mounted_hero_export_gate(const Json& batch,Json report){
 if(report.value("status",std::string{})=="PREFLIGHT_BLOCKED"||!expansion_batch_contains_mounted_hero(batch))return report;
 auto receiptDirectory=clean_path(path(batch.at("receipt_directory").get<std::string>()));
 fs::create_directories(receiptDirectory);
 const auto token=std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64());
 fs::path batchSnapshot,gateReceipt;
 for(unsigned suffix=0;suffix<10000;suffix++){
  const auto stem=L"hero-canvas-gate-"+token+L"-"+std::to_wstring(suffix);
  auto candidateBatch=receiptDirectory/(stem+L"-batch.json");
  auto candidateReceipt=receiptDirectory/(stem+L"-receipt.json");
  if(!fs::exists(candidateBatch)&&!fs::exists(candidateReceipt)){
   batchSnapshot=std::move(candidateBatch);gateReceipt=std::move(candidateReceipt);break;
  }
 }
 need(!batchSnapshot.empty()&&!gateReceipt.empty(),"HERO_GATE_EVIDENCE_PATH_EXHAUSTED");
 write_json_new(batchSnapshot,batch);
 const int exitCode=run_companion_command("hero-canvas-gate",{
  "--batch",str(batchSnapshot),"--batch-sha256",sha(read_all(batchSnapshot)),
  "--mode","verify","--report",str(gateReceipt),"--quiet"});
 Json gateReport;
 if(fs::is_regular_file(gateReceipt))gateReport=read_json(gateReceipt);
 else gateReport={{"schema","h3.hero-canvas-gate.error.v1"},{"status","FAILED"},
                  {"error","Gate process returned without a receipt"},{"exit_code",exitCode}};
 const bool passed=exitCode==0&&gateReport.value("status",std::string{})=="PASS";
 report["hero_canvas_gate"]=passed?"PASS":"BLOCKED";
 report["hero_canvas_gate_receipt_path"]=str(gateReceipt);
 report["hero_canvas_gate_batch_snapshot"]=str(batchSnapshot);
 report["hero_canvas_gate_receipt"]=gateReport;
 if(!passed){
  report["failed"]=report.value("failed",0)+1;
  report["status"]="COMPLETED_WITH_FAILURES";
 }
 return report;
}

inline bool is_companion_command(const std::string& name){
 return std::any_of(companion_commands().begin(),companion_commands().end(),[&](const auto& command){return command.name==name;});
}

inline void print_companion_help(){
 std::cout<<"  capabilities                                                     Emit the complete JSON command directory.\n";
 for(const auto& command:companion_commands())std::cout<<"  "<<command.name<<" [--tool-root PATH] [--python EXE] "<<command.usage<<"\n      "<<command.purpose<<"\n";
 std::cout<<"      Companion commands require Python and listed modules; interpolate also requires the pinned external RIFE executable/model in its batch.\n";
 std::cout<<"      --companion-timeout-seconds 1..86400 bounds the owned process tree (default 3600); dependency probes have a 20-second limit.\n";
}

} // namespace h3tier
