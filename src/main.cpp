#include "diagnostics.hpp"
#include "gui.hpp"
#include "apng_gui.hpp"
#include "checkerboard_gui.hpp"
#include "master_expand.hpp"
#include "motion.hpp"
#include "companion_cli.hpp"
#include "master_variant.hpp"
#include "map_artifact.hpp"
#include "native_review.hpp"
#include "logical_alpha.hpp"
#include <commdlg.h>
#include <cctype>
#pragma comment(lib,"comdlg32.lib")
#pragma comment(lib,"user32.lib")
using namespace h3tier;

struct Args{
 std::vector<std::string>a;
 Args(int n,wchar_t**v){for(int i=1;i<n;i++)a.push_back(utf8(v[i]));}
 bool has(const std::string&k)const{return std::find(a.begin(),a.end(),k)!=a.end();}
 std::string get(const std::string&k,const std::string&def="")const{for(size_t i=0;i<a.size();i++)if(a[i]==k){need(i+1<a.size(),"missing argument for "+k);return a[i+1];}return def;}
 std::vector<std::string>all(const std::string&k)const{std::vector<std::string>r;for(size_t i=0;i<a.size();i++)if(a[i]==k){need(i+1<a.size(),"missing argument for "+k);r.push_back(a[++i]);}return r;}
};
bool parse_legacy_multitier_replay(const Args&a,const std::string&command){
 const std::string flag="--legacy-multitier-replay";
 if(command!="run"){
  for(size_t i=1;i<a.a.size();i++)need(a.a[i].rfind(flag,0)!=0,"LEGACY_MULTITIER_REPLAY_RUN_ONLY");
  return false;
 }
 const std::set<std::string> valueOptions={"--plan","--backend","--workers","--memory-gib","--gpu-mib","--filter","--space","--png-filter","--adapter","--report"};
 const std::set<std::string> allowed={"--plan","--backend","--workers","--memory-gib","--gpu-mib","--filter","--space","--png-filter","--adapter","--report",flag};
 size_t flagCount=0;
 for(size_t i=1;i<a.a.size();i++){
  const auto&tokenValue=a.a[i];
  if(tokenValue==flag){flagCount++;need(i+1==a.a.size()||a.a[i+1].rfind("--",0)==0,"LEGACY_MULTITIER_REPLAY_TAKES_NO_VALUE");continue;}
  need(tokenValue.rfind(flag,0)!=0,"LEGACY_MULTITIER_REPLAY_MUST_BE_STANDALONE");
  need(!tokenValue.empty()&&tokenValue.rfind("--",0)==0,"RUN_UNEXPECTED_POSITIONAL_ARGUMENT");
  need(allowed.contains(tokenValue),"RUN_UNKNOWN_ARGUMENT: "+tokenValue);
  if(valueOptions.contains(tokenValue)){need(i+1<a.a.size()&&a.a[i+1].rfind("--",0)!=0,"RUN_ARGUMENT_VALUE_REQUIRED: "+tokenValue);i++;}
 }
 need(flagCount<=1,"LEGACY_MULTITIER_REPLAY_DUPLICATE");
 return flagCount==1;
}
Json overrides(Json s,const Args&a){
 for(auto k:{"backend","filter","space","png_filter"}){
  std::string flag="--"+std::string(k);if(k==std::string("png_filter"))flag="--png-filter";if(a.has(flag))s[k]=a.get(flag);
 }
 for(auto x:std::vector<std::pair<std::string,std::string>>{{"--workers","workers"},{"--memory-gib","memory_gib"},{"--gpu-mib","gpu_memory_mib"},{"--adapter","adapter"}})
  if(a.has(x.first)){auto v=a.get(x.first);size_t used=0;int n=std::stoi(v,&used);need(used==v.size(),"invalid integer option");s[x.second]=n;}
 return settings_checked(Json{{"settings",s}});
}
void help(){std::cout<<"H3TierExport "<<VERSION<<R"( - x64 visual asset and animation production workbench (CPU + D3D11)

  Double-click H3TierExport.exe for the native Windows UI.
  H3TierExport.exe ui-smoke
  H3TierExport.exe apng-preview --folder PNG_DIRECTORY [--def DEFNAME --group GROUP] [--fps 1..60]
  H3TierExport.exe checkerboard-alpha --input PNG_OR_JPEG [--input ...] --outdir NEW-DIRECTORY [--report NEW.json]
  H3TierExport.exe info
  H3TierExport.exe capabilities
  H3TierExport.exe selftest [--require-gpu] [--report file.json]
  H3TierExport.exe plan --batches DIRECTORY --out new-plan.json
  H3TierExport.exe plan --batch batch-001.json [--batch ...] --out new-plan.json
  H3TierExport.exe check --plan plan.json [--report file.json]
  H3TierExport.exe approve --plan plan.json --id ASSET_ID --evidence "user approval" --out NEW-approved-plan.json
  H3TierExport.exe run --plan approved-plan.json [--backend auto|cpu|gpu|hybrid]
                          [--workers 16] [--memory-gib 64] [--gpu-mib 0]
                          [--filter h3-quality|lanczos3|mitchell|area] [--space linear|srgb]
                          [--png-filter adaptive|none] [--adapter N] [--report NEW.json]
                          [--legacy-multitier-replay]  # explicit old approved {1,2,3} plans only
  H3TierExport.exe verify --plan approved-plan.json
  H3TierExport.exe bench --outdir NEW-DIRECTORY --jobs 48 --backend hybrid --workers 16
  H3TierExport.exe expand-plan --source SOURCE.png [--source ...] --source-tier 3
                              [--target-scale 3] --output-root NEW-DIRECTORY --out NEW-batch.json
                              [--animation-contract static-structure-dynamic-fx-v2|static-base-dynamic-overlay-v1|static-base-dynamic-overlay-v2|static-base-authored-fx-roi-v1|static-base-authored-fx-roi-v2|static-composite-authored-fx-roi-v1|static-composite-authored-fx-roi-v2]
                              [--animation-roi-mask AUTHORED-FX-ROI.png]
                              [--frame-set-classification single-or-unrelated-v1|independent-static-frame-catalog-v1]
                              [--mirror-source-root MASTER-ROOT] [--allow-padded-master]
                              [--reference-root NATIVE-1X-FRAMES] [--fit-anchor bottom_right|center]
                              [--native-authority NATIVE-1X-AUTHORITY.json]
      With --native-authority, the 1X size of every frame comes from that table (generated from
      H3sprite.lod/H3bitmap.lod only) and overrides --source-tier/--base-width: the 2015 HD
      Edition pack (H3ab_spr/H3ab_bmp) ships its own DEF for some identities at a DIFFERENT
      canvas, and 12 interface identities actually differ, so a master must never be planned or
      executed against HD-pack geometry. The table's SHA is sealed into the batch and re-checked
      on expand-check/expand/expand-verify.
      For a generated PNG, replace --source-tier with --base-width W --base-height H (original1X).
      With --mirror-source-root, preserve each source's folder path under MASTER-ROOT.
      With --reference-root, each master is fitted to the OFFICIAL VISIBLE BOX instead of the whole
      official slot: the reference frame is REFERENCE-ROOT/<master folder>/<master file name>, its
      painted extent (alpha, minus a border-connected opaque matte equal to the frame corner) is the
      box, and the master artwork is uniformly resampled into that box without stretching or cropping
      it. Non-animation single/reference-root batches keep that 80% post-fit floor. For independent-static-frame-catalog-v1, each alpha box is compared as a fraction of its full source canvas with the matching official frame alpha box as a fraction of its full native canvas before any fit; either axis below 95% blocks with MASTER_VISIBLE_SCALE_TOO_SMALL, so uniform enlargement cannot hide a small source. The complete official frame root is mandatory. Use this for anything that shares a tile with other objects - a master that fills its canvas
      otherwise draws larger than the game ever did and covers the neighbouring tile.
      For a static base with animated FX, pass every frame of one group and its official 1X reference
      folder, then use --animation-contract static-structure-dynamic-fx-v2. The mandatory gate
      registers a complete loop with one shared uniform transform, checks projected visible size/aspect,
      the shared exact-pixel static base, inter-frame scale drift, excess FX breathing beyond the
      official sequence envelope, the group-mean FX anchor, and exact source-frame coverage. Intentional
      official smoke/wing/water changes are retained; excess size spread over the official sequence by
      more than 5 percentage points or mean-anchor drift over 5 percent of the canvas blocks.
      Missing or extra official frames, changed reference pixels, empty groups, and unmeasurable static
      bases block export; legacy static-structure-dynamic-fx batches are verify-only.
      For a VCMI BASE sequence with complete animated cels or partial overlays, use
      static-base-dynamic-overlay-v2. It reproduces frame0+current-frame rendering, compares the
      fixed-pixel fraction with the official native sequence, and blocks any FX frame whose size or
      anchor differs from its corresponding official frame by more than 5 percent. Contract v1 remains
      verify-only for historical batches.
      When a user explicitly defines a new effect-only animation region over a VCMI BASE sequence, use
      static-base-authored-fx-roi-v1 for legacy whole-scene geometry or static-base-authored-fx-roi-v2
      when the official frame 0 contains an old effect that must be removed from the new static base.
      v2 measures structure outside the union of the sealed source ROI and official native effect ROI.
      For complete current-frame cels, keep v1 for historical replay and use static-composite-authored-fx-roi-v2
      for new plans. v2 fits the shared transform from static structure outside authored and official effect
      regions, then applies it to every full cel; only the sealed authored ROI may vary. All modes retain official frame inventory, shared
      registration, ROI containment, structure size/aspect, fixed-base stability, effect-size/anchor
      stability and frame-to-frame luminance pulse.
      Any detectable multi-frame group blocks unless it declares one of the animation contracts or
      independent-static-frame-catalog-v1. The latter binds and checks the exact official 1X frame
      inventory and each frame's native slot geometry without treating the frames as animation.
      Independent static-frame catalogs require a complete reference root, allow an official group with one physical frame, and check source coverage plus predicted post-fit width and height; either stage below 95% blocks with MASTER_VISIBLE_SCALE_TOO_SMALL. Stale pre-fit-only catalog plans are verify-only. Animation contracts still require at least two frames per group and retain their existing sequence gates.
      --frame-set-classification single-or-unrelated-v1 is an explicit declaration for unrelated
      stills; it does not permit a detected multi-frame sequence.
      Expansion always seals --filter h3-quality; the legacy local-range sampler cannot be selected
      and remains only to verify/replay receipts that were produced with it.
      A master whose artwork is centered inside a canvas widened to force an aspect is refused even
      when the ratio ceiling passes, because the artwork would shrink inside the official cell.
      Re-derive that master so the artwork fills its canvas; --allow-padded-master overrides it.
  H3TierExport.exe expand-plan --fullraw-spec SPEC.json --fullraw-spec-sha256 SHA
                              --output-root NEW-BUILD-DIRECTORY --out NEW-batch.json
  H3TierExport.exe expand-check|expand|expand-verify --batch NEW-batch.json
                              [--workers 32] [--memory-gib 64] [--report NEW.json]
  H3TierExport.exe motion-plan --spec SPEC.json --out NEW-motion-batch.json
  H3TierExport.exe motion-check|motion-probe|motion-run|motion-verify --batch motion-batch.json
                              [--workers 4] [--memory-gib 2] [--report NEW.json]
  H3TierExport.exe review-render --spec SPEC.json --spec-sha256 SHA --outdir NEW-IGNORED-ROOT
  H3TierExport.exe logical-alpha-render --spec SPEC.json --spec-sha256 SHA --outdir NEW-IGNORED-ROOT --operation-id ID
  H3TierExport.exe skill-variant-check|skill-variant-export|skill-variant-verify --manifest MANIFEST.json --manifest-sha256 SHA --outdir NEW-DIRECTORY
  H3TierExport.exe map-artifact-check|map-artifact-export|map-artifact-verify --manifest MANIFEST.json --manifest-sha256 SHA --outdir NEW-DIRECTORY
  )";
 print_companion_help();
 std::cout<<R"(

Defaults: up to 32 job workers, 64 GiB ceiling (also limited by available physical RAM),
linear-light premultiplied H3 quality, adaptive lossless PNG; default CPU.
H3 quality uses scale-aware Lanczos3 reduction and EWA enlargement with controlled edges.
The UI exposes the same engine and never silently grants approval.
Video production is planned; no public video rendering or encoding command exists yet.
Only approve after YOU reviewed the master and exact source/effective rectangles.
Outputs are straight-alpha 8-bit sRGB PNGs with exact native canvas/crop/origin.
Native core commands need no Python/CUDA/model/API at runtime. Companion commands require Python; the RIFE experiment also requires its pinned external engine and model files.
Master expansion is explicit CPU resampling, up to2x per edge, with preserved PNG sources.
  The master is never cropped, padded or resaved; the OUTPUT is what must equal the official
  native size times --target-scale, so a master whose ratio differs by <=5% is fitted to that
  official size and the fit is recorded in the receipt (fit_mode/aspect_error_pct).
It never performs diffusion or claims new native detail; new masters require visual review.
Skill master variants retain source resolution and use official aspect ratios; proportional contain preserves the complete artwork without distortion. No 3X tier is produced.
Motion performs layered CPU mesh deformation, weighted 2D action skeletons and aligned two-keyframe interpolation.
Single-texture two-pose interpolation preserves face, material and pattern consistency.
Transparent reframe trims only fully alpha-zero source margins into original 3X crops.
Correspondences/anchors must be supplied. Folds, crop loss or foot/volume drift require keyframes.
Motion specs accept target_scale 3..32 (default 3); scales above 3 write high-resolution master candidates, not formal 3X.
Review a master candidate, then use the existing H3TierExport expansion path for the separate exact3X output.
Legacy v4 motion batches remain exact3X. Every motion result is dependency-versioned and awaits art review; no automatic approval.
)";}
int finish(Json result,const Args&a){
 if(a.has("--report"))write_json_new(path(a.get("--report")),result);
 std::cout<<result.dump(2)<<"\r\n";
 if(result.contains("failed")&&result["failed"]!=0)return 1;
 if(result.value("status","")=="NO_EXPORT_PREFLIGHT_BLOCKED_OR_PENDING")return 2;
 return 0;
}
int wmain(int argc,wchar_t**argv){
 SetConsoleOutputCP(CP_UTF8);
 try{
  ComInit com;
  if(argc==1)return run_gui(GetModuleHandleW(nullptr));
  Args a(argc,argv);auto command=a.a[0];
  if(command=="help"||command=="--help"){help();return 0;}
  const bool legacyMultitierReplay=parse_legacy_multitier_replay(a,command);
  if(command=="ui-smoke")return finish(gui_smoke(GetModuleHandleW(nullptr)),a);
  if(command=="apng-selftest")return finish(apng_selftest(),a);
  if(command=="apng-preview"){
   const std::set<std::string> allowed={"--folder","--def","--group","--fps"};std::set<std::string> seen;
   for(size_t i=1;i<a.a.size();){const auto&option=a.a[i];need(allowed.contains(option)&&seen.insert(option).second,"APNG_PREVIEW_UNKNOWN_OR_DUPLICATE_OPTION: "+option);need(i+1<a.a.size()&&!a.a[i+1].empty()&&a.a[i+1].rfind("--",0)!=0,"APNG_PREVIEW_OPTION_VALUE_REQUIRED: "+option);i+=2;}
   need(seen.contains("--folder"),"APNG_PREVIEW_FOLDER_REQUIRED");need(seen.contains("--def")==seen.contains("--group"),"APNG_PREVIEW_DEF_AND_GROUP_MUST_BE_PAIRED");
   auto folder=path(a.get("--folder"));need(fs::is_directory(folder),"APNG_PREVIEW_FOLDER_NOT_FOUND: "+str(folder));
   std::wstring group;
   if(a.has("--def")){auto def=a.get("--def"),frameGroup=a.get("--group");need(!def.empty()&&std::all_of(def.begin(),def.end(),[](unsigned char c){return std::isalnum(c)||c=='_';}),"APNG_PREVIEW_DEF_INVALID");need(!frameGroup.empty()&&std::all_of(frameGroup.begin(),frameGroup.end(),[](unsigned char c){return std::isdigit(c);}),"APNG_PREVIEW_GROUP_INVALID");group=wide(def)+L"_"+wide(frameGroup);}
   auto fpsText=a.get("--fps","8");size_t used=0;int fps=std::stoi(fpsText,&used);need(used==fpsText.size()&&fps>=1&&fps<=60,"APNG_PREVIEW_FPS_MUST_BE_1_TO_60");
   apngui::run(nullptr,nullptr,folder,group,fps);return 0;
  }
  if(command=="checkerboard-alpha"){
   const std::set<std::string> allowed={"--input","--outdir","--report"};
   std::set<std::string> seen;std::map<std::string,std::string> options;std::vector<fs::path> inputs;
   for(size_t i=1;i<a.a.size();){
    const auto& name=a.a[i];need(allowed.contains(name),"CHECKERBOARD_CLI_UNKNOWN_ARGUMENT: "+name);
    if(name!="--input")need(seen.insert(name).second,"CHECKERBOARD_CLI_DUPLICATE_ARGUMENT: "+name);
    need(i+1<a.a.size()&&!a.a[i+1].empty()&&a.a[i+1].rfind("--",0)!=0,"CHECKERBOARD_CLI_VALUE_REQUIRED: "+name);
    const auto value=a.a[i+1];if(name=="--input")inputs.push_back(path(value));else options.emplace(name,value);i+=2;
   }
   need(!inputs.empty(),"CHECKERBOARD_CLI_INPUT_REQUIRED");need(options.contains("--outdir"),"CHECKERBOARD_CLI_OUTDIR_REQUIRED");
   auto outputDirectory=clean_path(path(options.at("--outdir")));
   if(options.contains("--report")){
    auto reportPath=clean_path(path(options.at("--report")));
    need(!fs::exists(reportPath),"CHECKERBOARD_REPORT_ALREADY_EXISTS: "+str(reportPath));
    auto outputFold=folded(outputDirectory),reportFold=folded(reportPath);auto outputPrefix=outputFold;
    if(outputPrefix.back()!=L'\\')outputPrefix+=L'\\';
    const bool reportInside=reportFold==outputFold||(reportFold.size()>outputPrefix.size()&&reportFold.compare(0,outputPrefix.size(),outputPrefix)==0);
    need(!reportInside,"CHECKERBOARD_REPORT_MUST_BE_OUTSIDE_OUTPUT_DIRECTORY");
   }
   auto outcome=checkerboard_gui::process_files(inputs,outputDirectory);
   return finish(outcome.receipt,a);
  }
  if(command=="capabilities")return finish(companion_capabilities(),a);
  if(is_companion_command(command))return run_companion_command(command,std::vector<std::string>(a.a.begin()+1,a.a.end()));
  if(command=="review-render"){
   auto options=native_review_cli_arguments(std::vector<std::string>(a.a.begin()+1,a.a.end()));
   auto report=render_native_review(path(options.at("--spec")),options.at("--spec-sha256"),
                                    path(options.at("--outdir")),native_review_executable_path());
   std::cout<<report.dump(2)<<"\r\n";return 0;
  }
  if(command=="logical-alpha-render"){
   const std::set<std::string> allowed={"--spec","--spec-sha256","--outdir","--operation-id"};
   std::map<std::string,std::string> options;
   for(size_t i=1;i<a.a.size();i++){
    const auto& item=a.a[i];auto eq=item.find('=');auto name=item.substr(0,eq);
    need(allowed.contains(name),"LOGICAL_ALPHA_CLI_UNKNOWN_ARGUMENT: "+name);
    std::string value;
    if(eq!=std::string::npos)value=item.substr(eq+1);
    else{need(i+1<a.a.size(),"LOGICAL_ALPHA_CLI_VALUE_REQUIRED: "+name);value=a.a[++i];}
    need(!value.empty()&&value.rfind("--",0)!=0,"LOGICAL_ALPHA_CLI_VALUE_REQUIRED: "+name);
    need(!options.contains(name),"LOGICAL_ALPHA_CLI_DUPLICATE_ARGUMENT: "+name);options.emplace(name,value);
   }
   for(const auto& name:allowed)need(options.contains(name),"LOGICAL_ALPHA_CLI_REQUIRED_ARGUMENT: "+name);
   auto report=render_logical_alpha(path(options.at("--spec")),options.at("--spec-sha256"),
                                   path(options.at("--outdir")),options.at("--operation-id"));
   std::cout<<report.dump(2)<<"\r\n";return 0;
  }
  if(command=="map-artifact-check"||command=="map-artifact-export"||command=="map-artifact-verify"){
   const std::set<std::string> allowed={"--manifest","--manifest-sha256","--outdir","--report"};std::set<std::string> seen;
   for(size_t i=1;i<a.a.size();){const auto& name=a.a[i];need(allowed.contains(name)&&seen.insert(name).second,"MAP_ARTIFACT_UNKNOWN_OR_DUPLICATE_OPTION: "+name);need(i+1<a.a.size()&&!a.a[i+1].empty()&&a.a[i+1].rfind("--",0)!=0,"MAP_ARTIFACT_OPTION_VALUE_REQUIRED: "+name);i+=2;}
   for(const auto& name:{"--manifest","--manifest-sha256","--outdir"})need(seen.contains(name),"MAP_ARTIFACT_REQUIRED_OPTION: "+std::string(name));
   return finish(run_artifact_map_command(command,path(a.get("--manifest")),a.get("--manifest-sha256"),path(a.get("--outdir"))),a);
  }
  if(command=="skill-variant-check"||command=="skill-variant-export"||command=="skill-variant-verify"){
   const std::set<std::string> allowed={"--manifest","--manifest-sha256","--outdir","--report"};std::set<std::string> seen;
   for(size_t i=1;i<a.a.size();){const auto& name=a.a[i];need(allowed.contains(name)&&seen.insert(name).second,"SKILL_VARIANT_UNKNOWN_OR_DUPLICATE_OPTION: "+name);need(i+1<a.a.size()&&!a.a[i+1].empty()&&a.a[i+1].rfind("--",0)!=0,"SKILL_VARIANT_OPTION_VALUE_REQUIRED: "+name);i+=2;}
   for(const auto& name:{"--manifest","--manifest-sha256","--outdir"})need(seen.contains(name),"SKILL_VARIANT_REQUIRED_OPTION: "+std::string(name));
   auto result=run_skill_variant_command(command,path(a.get("--manifest")),a.get("--manifest-sha256"),H3_COMPANION_RUNTIME_SHA,path(a.get("--outdir")));return finish(result,a);
  }
  const auto directory=companion_capabilities();
  bool knownCommand=false;
  for(const auto& entry:directory.at("native_commands"))if(entry.at("name")==command){knownCommand=true;break;}
  if(!knownCommand)return companion_error("UNKNOWN_COMMAND","UNKNOWN_COMMAND","Unknown command: "+command);
  if(command=="info"){Gpu g;return finish({{"version",VERSION},{"arch","x64"},{"ui","native Win32"},{"cpu_kernel","quality: double accumulation, scale-aware Lanczos3 shrink / EWA Jinc enlargement, independent coverage limiter; legacy: SSE2"},{"quality_algorithm",QUALITY_ALGORITHM},{"motion_algorithm",MOTION_ALGORITHM},{"motion_capabilities",{"layered_2d_mesh","weighted_2d_action_skeleton","explicit_native_roi_mesh","rational_native_grid_roi_surface","aligned_two_keyframe_interpolation","same_texture_pose_interpolation","configurable_master_scale_3_to_32","native_slot_reuse","transparent_margin_reframe","per_frame_dependency_resume","foot_and_volume_diagnostics"}},{"memory",host_memory()},{"gpu",g.info},{"defaults",default_settings()},{"cli",directory}},a);}
  if(command=="selftest")return finish(selftest(a.has("--require-gpu")),a);
  if(command=="motion-plan"){
   need(a.has("--spec")&&a.has("--out"),"motion-plan requires --spec and new --out");auto batch=make_motion_batch(read_json(path(a.get("--spec"))));write_json_new(path(a.get("--out")),batch);return finish({{"batch",a.get("--out")},{"items",batch.at("items").size()},{"outputs_written",0},{"review_required",true}},a);
  }
  if(command=="motion-check"||command=="motion-probe"||command=="motion-run"||command=="motion-verify"){
   need(a.has("--batch"),"motion --batch required");auto batch=read_json(path(a.get("--batch")));auto settings=motion_settings(overrides(settings_checked(batch),a));
   if(command=="motion-check"){std::vector<MotionJob> jobs;return finish(check_motion(batch,settings,jobs),a);}
   if(command=="motion-probe")return finish(probe_motion(batch,settings),a);
   return finish(run_motion(batch,settings,command=="motion-verify"),a);
  }
  if(command=="plan"){
   std::vector<fs::path>b;for(auto s:a.all("--batch"))b.push_back(path(s));
   if(a.has("--batches"))for(int n=1;n<=4;n++){auto p=path(a.get("--batches"))/path("batch-00"+std::to_string(n)+".json");if(fs::exists(p))b.push_back(p);}
   need(a.has("--out"),"plan --out required");return finish(make_plan(b,path(a.get("--out"))),a);
  }
  if(command=="bench"){need(a.has("--outdir"),"new benchmark directory required");auto s=overrides(default_settings(),a);int n=std::stoi(a.get("--jobs","48"));need(n>=1&&n<=256,"benchmark jobs 1..256");return finish(benchmark(path(a.get("--outdir")),s,n),a);}
  if(command=="expand-plan"){
   need(a.has("--out")&&a.has("--output-root"),"expand-plan requires --out and --output-root");
   if(a.has("--fullraw-spec")||a.has("--fullraw-spec-sha256")){
    const std::set<std::string> allowed={"--fullraw-spec","--fullraw-spec-sha256","--output-root","--out","--report"};
    std::set<std::string> seen;
    for(size_t i=1;i<a.a.size();i+=2){need(allowed.contains(a.a[i])&&seen.insert(a.a[i]).second,"FULLRAW_CLI_UNKNOWN_OR_DUPLICATE_OPTION");need(i+1<a.a.size()&&!a.a[i+1].empty()&&a.a[i+1].rfind("--",0)!=0,"FULLRAW_CLI_VALUE_REQUIRED");}
    need(a.has("--fullraw-spec")&&a.has("--fullraw-spec-sha256"),"FULLRAW_SPEC_AND_SHA_REQUIRED");
    auto batch=make_fullraw_expansion_batch(path(a.get("--fullraw-spec")),a.get("--fullraw-spec-sha256"),path(a.get("--output-root")));
    write_json_new(path(a.get("--out")),batch);return finish({{"batch",a.get("--out")},{"items",batch.at("items").size()},
      {"sampling_mode","full_raw_sampling_domain"},{"outputs_written",0},{"model_inference_performed",false},{"art_approved",false}},a);
   }
   auto number=[&](const std::string& name,int fallback){auto v=a.get(name,std::to_string(fallback));size_t used=0;int n=std::stoi(v,&used);need(used==v.size(),"invalid integer option");return n;};
   need(!(a.has("--source-tier")&&(a.has("--base-width")||a.has("--base-height"))),"choose source tier OR original1X dimensions");
   need(a.has("--source-tier")||(a.has("--base-width")&&a.has("--base-height"))||a.has("--native-authority"),"source tier, both original1X dimensions, or --native-authority required");
   std::vector<fs::path> sources;for(auto s:a.all("--source"))sources.push_back(path(s));
   auto mirrorRoot=a.has("--mirror-source-root")?path(a.get("--mirror-source-root")):fs::path{};
   auto referenceRoot=a.has("--reference-root")?path(a.get("--reference-root")):fs::path{};
   auto nativeAuthority=a.has("--native-authority")?path(a.get("--native-authority")):fs::path{};
   auto animationRoiMask=a.has("--animation-roi-mask")?path(a.get("--animation-roi-mask")):fs::path{};
   auto batch=make_expansion_batch(sources,number("--source-tier",0),number("--target-scale",3),{number("--base-width",0),number("--base-height",0)},path(a.get("--output-root")),a.get("--filter","h3-quality"),mirrorRoot,a.has("--allow-padded-master"),referenceRoot,a.get("--fit-anchor",""),nativeAuthority,a.get("--animation-contract",""),a.get("--frame-set-classification",""),animationRoiMask);
   write_json_new(path(a.get("--out")),batch);return finish({{"batch",a.get("--out")},{"items",batch["items"].size()},{"outputs_written",0},{"model_inference_performed",false}},a);
  }
  if(command=="expand-check"||command=="expand"||command=="expand-verify"){
   need(a.has("--batch"),"--batch required");need(!a.has("--backend")||a.get("--backend")=="cpu","master expansion uses CPU only");
   auto batchPath=clean_path(path(a.get("--batch")));auto batch=read_json(batchPath);auto settings=expansion_settings(overrides(batch.value("settings",default_settings()),a));
   if(command=="expand-check"){std::vector<ExpansionJob> jobs;auto r=check_expansion(batch,settings,jobs);r["failed"]=r["blocked"];return finish(r,a);}
   auto report=run_expansion(batch,settings,command=="expand-verify");
   report=apply_mounted_hero_export_gate(batch,std::move(report));
   return finish(report,a);
  }
  need(a.has("--plan"),"--plan required");auto p=clean_path(path(a.get("--plan")));auto plan=read_json(p);auto settings=overrides(settings_checked(plan),a);
  if(command=="approve"){need(a.has("--id")&&a.has("--evidence")&&a.has("--out"),"approve requires id, evidence, new output path");auto approved=seal_approval(plan,a.get("--id"),a.get("--evidence"));write_json_new(path(a.get("--out")),approved);return finish({{"approved_id",a.get("--id")},{"new_plan",a.get("--out")},{"tiers_written",0}},a);}
  if(command=="check"){std::vector<Job>j;return finish(check_plan(plan,settings,j),a);}
  if(command=="run"||command=="verify")return finish(run_plan(plan,settings,command=="verify",legacyMultitierReplay),a);
  return companion_error("UNKNOWN_COMMAND","UNKNOWN_COMMAND","Unknown command: "+command);
 }catch(const std::exception&e){
  Json r={{"status","FAILED"},{"error",e.what()}};std::cerr<<r.dump()<<"\r\n";
  if(argc==1)MessageBoxW(nullptr,wide(e.what()).c_str(),L"H3TierExport error",MB_OK|MB_ICONERROR);
  return 1;
 }
}
