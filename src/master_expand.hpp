#pragma once
#include "jobs.hpp"
#include "full_raw_domain.hpp"
#include "official_silhouette.hpp"

namespace h3tier {
// A master may be standardised to any integer multiple of the official 1X slot, not only 3X/4X:
// the press that finally ships is a 3X expand, and preparing the master at the largest multiple
// its own pixels can carry keeps that final step an exact integer downscale.
inline constexpr int MAX_STANDARD_SIZE_MULTIPLE=36;
inline constexpr const char* EXPAND_ALGORITHM="linear-premultiplied-lanczos3-local-range-v1";  // legacy sampler: verify/replay only, never selectable
inline Json expansion_settings(Json settings){
 need(settings.at("filter")=="h3-quality"&&settings.at("space")=="linear","master expansion uses h3-quality only; the legacy local-range sampler cannot be selected (it stays available for verifying old receipts)");
 settings["backend"]="cpu";return settings_checked(Json{{"settings",settings}});
}
inline bool supported_expansion(const Json& batch){return (batch.at("schema")=="h3.master-expand.batch.v1"&&(batch.at("algorithm")==EXPAND_ALGORITHM||batch.at("algorithm")==QUALITY_ALGORITHM))||(fullraw_batch(batch)&&batch.at("sampling_mode")=="full_raw_sampling_domain"&&batch.at("algorithm")==QUALITY_ALGORITHM);}
inline Image expansion_replay(const Image& im,int w,int h,const Json& contract){return contract.at("algorithm")==EXPAND_ALGORITHM?expand_uniform_cpu(im,w,h):expand_quality_cpu(im,w,h);}
struct ExpansionJob {
 std::string id,fingerprint,sourceSha;fs::path source,output,receipt;Json contract;
 int sourceW=0,sourceH=0,samplingW=0,samplingH=0,w=0,h=0;std::uint64_t admission=0;
 double aspectError=0;std::string fitMode;bool allowPadded=false;
 FullRawDomain domain;Json domainBatch;
 bool silhouette=false;fs::path reference;std::string referenceSha;int referenceW=0,referenceH=0,referenceScale=3;
 ArtworkBox slotBox,slotBox1x,masterBox;std::string fitAnchor="bottom_right";
};
inline std::string expand_frame_group(const fs::path& source){
 auto stem=source.stem().wstring();auto split=stem.rfind(L'_');
 if(split==std::wstring::npos||split==0||split+1>=stem.size())return {};
 auto digits=[](const std::wstring& value){return std::all_of(value.begin(),value.end(),[](wchar_t c){return c>=L'0'&&c<=L'9';});};
 if(!digits(stem.substr(0,split))||!digits(stem.substr(split+1)))return {};
 return str(source.parent_path())+"|"+utf8(stem.substr(0,split));
}
inline Json static_animation_registration_audit(const std::vector<ExpansionJob>& jobs){
 struct Measure {const ExpansionJob* job;ArtworkBox alpha;double scale;int x,y,w,h;};
 std::map<std::string,std::vector<Measure>> groups;Wic decoder;
 for(const auto& job:jobs){
  auto key=expand_frame_group(job.source);
  need(!key.empty(),"STATIC_ANIMATION_FRAME_NAME_REQUIRED: expected <group>_<frame>.png in an identity folder: "+str(job.source));
  auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"STATIC_ANIMATION_SOURCE_DRIFT: "+str(job.source));
  std::string note;auto image=decoder.decode(raw,true,note);auto alpha=artwork_box(image);
  need(!alpha.empty,"STATIC_ANIMATION_EMPTY_FRAME: "+str(job.source));
  int aw=alpha.x1-alpha.x0,ah=alpha.y1-alpha.y0,w=0,h=0,x=0,y=0;double scale=0;
  if(job.silhouette){
   int bw=job.slotBox.x1-job.slotBox.x0,bh=job.slotBox.y1-job.slotBox.y0;
   need(aw>0&&ah>0&&bw>0&&bh>0,"STATIC_ANIMATION_INVALID_ARTWORK_BOX: "+str(job.source));
   scale=std::min(double(bw)/aw,double(bh)/ah);w=std::max(1,int(std::lround(aw*scale)));h=std::max(1,int(std::lround(ah*scale)));
   w=std::min(w,bw);h=std::min(h,bh);
   auto why=silhouette_place(job.w,job.h,job.slotBox.x0,job.slotBox.y0,job.slotBox.x1,job.slotBox.y1,w,h,job.fitAnchor,x,y);
   need(why==nullptr,"STATIC_ANIMATION_FIT_INVALID: "+str(job.source));
  }else{
   double sx=double(job.w)/job.sourceW,sy=double(job.h)/job.sourceH;scale=std::min(sx,sy);
   x=int(std::lround(alpha.x0*sx));y=int(std::lround(alpha.y0*sy));w=int(std::lround(aw*sx));h=int(std::lround(ah*sy));
  }
  groups[key].push_back({&job,alpha,scale,x,y,w,h});
 }
 Json result={{"contract","static-structure-dynamic-fx"},{"status","PASS"},{"limits",{{"max_translation_delta_output_px",0},{"max_size_delta_output_px",0},{"max_scale_delta_percent",0.1}}},{"groups",Json::array()},{"failures",Json::array()}};
 for(auto& [key,frames]:groups){
  need(frames.size()>=2,"STATIC_ANIMATION_GROUP_NEEDS_AT_LEAST_2_FRAMES: "+key);
  auto frameIndex=[](const Measure& m){auto stem=m.job->source.stem().wstring();auto split=stem.rfind(L'_');return std::stoul(stem.substr(split+1));};
  std::sort(frames.begin(),frames.end(),[&](const Measure& a,const Measure& b){return frameIndex(a)<frameIndex(b);});
  const auto& anchor=frames.front();Json frameRows=Json::array();
  for(const auto& current:frames){
   double scaleDelta=std::fabs(current.scale-anchor.scale)/anchor.scale*100.0;
   int dx=current.x-anchor.x,dy=current.y-anchor.y,dw=current.w-anchor.w,dh=current.h-anchor.h;
   bool bad=dx!=0||dy!=0||dw!=0||dh!=0||scaleDelta>0.1;
   Json row={{"frame",str(current.job->source.filename())},{"source_artwork_box",{current.alpha.x0,current.alpha.y0,current.alpha.x1,current.alpha.y1}},
             {"reference",current.job->silhouette?Json(str(current.job->reference)):Json(nullptr)},
             {"reference_artwork_box_1x",current.job->silhouette?Json::array({current.job->slotBox1x.x0,current.job->slotBox1x.y0,current.job->slotBox1x.x1,current.job->slotBox1x.y1}):Json(nullptr)},
             {"slot_artwork_box_3x",current.job->silhouette?Json::array({current.job->slotBox.x0,current.job->slotBox.y0,current.job->slotBox.x1,current.job->slotBox.y1}):Json(nullptr)},
             {"fitted_box_3x",{current.x,current.y,current.x+current.w,current.y+current.h}},
             {"scale",current.scale},{"delta_vs_anchor",{{"x_px",dx},{"y_px",dy},{"width_px",dw},{"height_px",dh},{"scale_percent",scaleDelta}}},
             {"status",bad?"BLOCKED":"READY"}};
   frameRows.push_back(row);
   if(bad){result["status"]="BLOCKED";result["failures"].push_back({{"group",key},{"anchor_frame",str(anchor.job->source.filename())},
      {"frame",str(current.job->source.filename())},{"source_artwork_box",row["source_artwork_box"]},{"reference",row["reference"]},
      {"reference_artwork_box_1x",row["reference_artwork_box_1x"]},{"slot_artwork_box_3x",row["slot_artwork_box_3x"]},{"fitted_box_3x",row["fitted_box_3x"]},
      {"delta_vs_anchor",row["delta_vs_anchor"]},{"action","Frame transform differs from the static group anchor. Keep the building alpha bounds fixed, or use the same reference silhouette for every frame; limit permitted motion to the dynamic FX layer."}});}
  }
  result["groups"].push_back({{"key",key},{"anchor_frame",str(anchor.job->source.filename())},{"frames",frameRows}});
 }
 return result;
}
inline ArtworkBox scale_box(const ArtworkBox& b,int s){return {b.x0*s,b.y0*s,b.x1*s,b.y1*s,b.empty,b.matte};}
inline void load_silhouette(ExpansionJob& job,const Json& entry,const Json& batch){
 job.reference=clean_path(path(entry.at("reference")));auto root=clean_path(path(batch.at("output_root")));
 (void)root;need(fs::exists(job.reference),"official reference frame is missing: "+str(job.reference));
 auto raw=read_all(job.reference);
 need(valid_sha(entry.at("reference_sha256")),"reference SHA256 required");
 job.referenceSha=entry.at("reference_sha256").get<std::string>();
 need(sha(raw)==job.referenceSha,"reference drift");
 Wic decoder;std::string note;auto im=decoder.decode(raw,true,note);
 job.referenceW=im.w;job.referenceH=im.h;
 need(entry.at("reference_pixels")==Json::array({im.w,im.h}),"reference PNG dimensions changed");
 int scale=integer(entry.at("target_scale"),3,MAX_STANDARD_SIZE_MULTIPLE,"target master scale");
 job.referenceScale=scale;
 need(im.w==job.w/scale&&im.h==job.h/scale,"reference frame is not the official 1X slot of this job");
 auto box=artwork_box(im,true);
 need(!box.empty,"reference frame paints nothing; it cannot bound a silhouette fit");
 job.slotBox1x=box;job.slotBox=scale_box(box,scale);
 job.fitAnchor=entry.value("fit_anchor",batch.value("fit_anchor",std::string("bottom_right")));
 need(job.fitAnchor=="bottom_right"||job.fitAnchor=="center","fit_anchor must be bottom_right or center");
 job.silhouette=true;
}
inline ExpansionJob expansion_job(const Json& entry,const Json& batch,const Json& settings,bool verifyOnly=false){
 need(supported_expansion(batch),"unsupported master-expansion contract");
 auto legacy=batch.at("algorithm")==EXPAND_ALGORITHM;
 need(settings.at("space")=="linear","master expansion requires linear space");
 need(legacy?batch.at("settings").at("filter")=="lanczos3":settings.at("filter")=="h3-quality"&&batch.at("settings").at("filter")=="h3-quality","filter differs from the sealed expansion algorithm; prepare a separate batch");
 need(!legacy||verifyOnly,"legacy low-quality expansion is verify-only; re-plan this batch with --filter h3-quality");
 ExpansionJob job;job.id=token(entry.at("id"));job.source=clean_path(path(entry.at("source")));job.output=clean_path(path(entry.at("output")));
 auto root=clean_path(path(batch.at("output_root")));contained(job.output,root);
 need(folded(job.source)!=folded(job.output)&&job.output.extension()==L".png","expanded master must be a separate PNG");
 need(valid_sha(entry.at("source_sha256")),"source SHA256 required");job.sourceSha=entry.at("source_sha256");
 auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"expansion source drift");
 need(raw.size()>=24&&!std::memcmp(raw.data(),"\x89PNG\r\n\x1a\n",8),"expansion source must be an original PNG");
 Wic decoder;auto meta=decoder.frame(raw);job.sourceW=meta.w;job.sourceH=meta.h;
 need(entry.at("source_pixels")==Json::array({job.sourceW,job.sourceH}),"source PNG dimensions changed");
 auto base=pair(entry.at("base_pixels"),1,"original 1X pixels");int scale=integer(entry.at("target_scale"),fullraw_batch(batch)?1:3,fullraw_batch(batch)?3:MAX_STANDARD_SIZE_MULTIPLE,"target master scale");
 job.w=base[0]*scale;job.h=base[1]*scale;
 need(job.w<=MAX_AXIS&&job.h<=MAX_AXIS&&std::uint64_t(job.w)*job.h<=MAX_PIXELS,"master target exceeds pixel limit");
 job.samplingW=job.sourceW;job.samplingH=job.sourceH;
 if(fullraw_batch(batch)){
  job.domain=fullraw_read(entry,batch);job.samplingW=job.domain.w;job.samplingH=job.domain.h;
  job.domainBatch={{"schema",batch.at("schema")},{"sampling_mode",batch.at("sampling_mode")},
                  {"algorithm",batch.at("algorithm")},{"fullraw_spec",batch.at("fullraw_spec")},
                  {"output_root",batch.at("output_root")}};
 }
 job.aspectError=master_fit_aspect_error(job.samplingW,job.samplingH,job.w,job.h);
 job.fitMode=master_fit_mode(job.samplingW,job.samplingH,job.w,job.h);
 // A silhouette fit measures the artwork instead of the canvas outline, so the canvas ratio is no
 // longer the thing that has to match the slot; the coverage floor inside the fit is.
 if(entry.contains("reference")){load_silhouette(job,entry,batch);job.fitMode="official-silhouette-fit";}
 else need(job.aspectError<=MASTER_FIT_ASPECT_CEILING,"aspect error above 5% of the official slot ratio; the master is never altered - register it through scale-layout");
 job.allowPadded=entry.value("padded_master_allowed",false);
 need(job.w<=job.samplingW*2&&job.h<=job.samplingH*2,"expansion exceeds 2x per edge; supply a better source");
 job.contract={{"entry",entry},{"output_root",str(root)},{"algorithm",batch.at("algorithm")},{"space","linear"},{"png_filter",settings.at("png_filter")}};
 if(job.domain.active)job.contract["full_raw_sampling_domain"]=job.domain.binding;
 job.fingerprint=sha_text(job.contract.dump());auto receipts=clean_path(path(batch.at("receipt_directory")));contained(receipts,root);
 job.receipt=receipts/path(job.id+"-"+job.fingerprint+".json");need(folded(job.receipt)!=folded(job.output)&&folded(job.receipt)!=folded(job.source),"receipt aliases an image");
 job.admission=std::uint64_t(job.sourceW)*job.sourceH*64+std::uint64_t(job.w)*job.h*96+raw.size()*2+64ull*1024*1024;
 if(job.domain.active)job.admission+=std::uint64_t(job.samplingW)*job.samplingH*64;
 return job;
}
// The 1999 native DEF is the only size authority for a 3X delivery (user ruling 2026-11-04).
// The 2015 HD Edition pack ships its own DEF for some identities with a DIFFERENT canvas - 12
// interface identities do - so a reference PNG taken from there silently retargets every frame
// planned against it. The authority table is generated from H3sprite.lod/H3bitmap.lod only, and
// a plan that disagrees with it is refused instead of being trusted.
inline Json load_native_authority(const fs::path& p){
 need(fs::exists(p)&&fs::is_regular_file(p),"NATIVE_1X_AUTHORITY_NOT_FOUND: "+str(p));
 auto raw=read_all(p);auto doc=read_json(p);
 need(doc.value("schema",std::string())=="h3.native-1x-authority.v1",
     "NATIVE_1X_AUTHORITY_SCHEMA: the table must be h3.native-1x-authority.v1");
 need(doc.contains("frames")&&doc["frames"].is_object(),
     "NATIVE_1X_AUTHORITY_FRAMES_MISSING: expected a frames object");
 auto lods=doc.value("source_lods",Json::array());
 bool native_only=false;
 if(lods.is_array()&&!lods.empty()){
  native_only=true;
  for(const auto& entry:lods){
   auto name=entry.is_object()?entry.value("name",std::string()):entry.get<std::string>();
   for(auto& c:name)c=(char)std::tolower((unsigned char)c);
   need(name.find("h3ab_")==std::string::npos,
        "NATIVE_1X_AUTHORITY_SOURCE_IS_HD_PACK: "+name+" is the 2015 HD Edition pack, not native 1X");
  }
 }
 need(native_only,"NATIVE_1X_AUTHORITY_SOURCE_LODS_MISSING: the table must name the native LOD it came from");
 doc["_authority_sha256"]=sha(raw);doc["_authority_path"]=str(clean_path(p));
 return doc;
}
inline std::array<int,2> native_authority_size(const Json& authority,const std::wstring& identity,
                                               const std::wstring& frame){
 std::string id;for(auto c:identity)id+=(char)std::toupper((unsigned char)c);
 std::string fk;for(auto c:frame)fk+=(char)std::tolower((unsigned char)c);
 const auto& frames=authority.at("frames");
 need(frames.contains(id),"NATIVE_1X_AUTHORITY_MISSING_IDENTITY: "+id
     +" is not registered in the native 1X authority (native H3sprite/H3bitmap has no such DEF frame)");
 const auto& row=frames.at(id);
 need(row.is_object()&&row.contains(fk),"NATIVE_1X_AUTHORITY_MISSING_FRAME: "+id+"/"+fk);
 const auto& want=row.at(fk);
 need(want.is_array()&&want.size()==2&&want[0].get<int>()>0&&want[1].get<int>()>0,
     "NATIVE_1X_AUTHORITY_BAD_SIZE: "+id+"/"+fk);
 return {want[0].get<int>(),want[1].get<int>()};
}
inline Json check_expansion(const Json& batch,const Json& settings,std::vector<ExpansionJob>& jobs,bool verifyOnly=false){
 need(supported_expansion(batch),"unsupported expansion batch");
 need(verifyOnly||batch.at("algorithm")!=EXPAND_ALGORITHM,"legacy low-quality expansion is verify-only; re-plan this batch with --filter h3-quality");
 need(batch.at("items").is_array()&&!batch.at("items").empty()&&batch.at("items").size()<=10000,"expansion item count");
 // A sealed native authority is re-read at execution time: planning against it and then editing
 // base_pixels (or swapping the table) must not be able to ship an HD-pack-sized 3X.
 Json authority=Json{};
 if(batch.contains("native_1x_authority")&&batch.at("native_1x_authority").is_object()){
  const auto& sealed=batch.at("native_1x_authority");
  auto file=clean_path(path(sealed.at("path").get<std::string>()));
  need(fs::exists(file),"NATIVE_1X_AUTHORITY_NOT_FOUND at execution: "+str(file));
  need(sha(read_all(file))==sealed.at("sha256").get<std::string>(),
       "NATIVE_1X_AUTHORITY_DRIFTED: the native 1X table changed after this batch was planned");
  authority=load_native_authority(file);
 }
 std::set<std::string> ids;std::set<std::wstring> outputs,sources,receipts;Json items=Json::array();int blocked=0;
 for(const auto& x:batch.at("items")){
  try{if(!authority.is_null()){need(x.contains("native_1x_pixels"),"NATIVE_1X_AUTHORITY_UNSEALED: this item carries no native 1X size while the batch seals an authority table");auto sp=clean_path(path(x.value("source",std::string())));auto auth=native_authority_size(authority,sp.parent_path().filename().wstring(),sp.stem().wstring());Json want=Json::array();want.push_back(auth[0]);want.push_back(auth[1]);need(x.at("base_pixels")==want,"NATIVE_1X_AUTHORITY_CONFLICT: base_pixels is not the native 1X size of "+str(sp));need(x.at("native_1x_pixels")==want,"NATIVE_1X_AUTHORITY_CONFLICT: the sealed native size was edited for "+str(sp));}auto job=expansion_job(x,batch,settings,verifyOnly);need(ids.insert(job.id).second,"duplicate expansion ID");need(outputs.insert(folded(job.output)).second,"duplicate expanded output");need(receipts.insert(folded(job.receipt)).second,"duplicate receipt");sources.insert(folded(job.source));if(job.domain.active){need(outputs.insert(folded(job.domain.cropOutput)).second,"duplicate fullraw crop output");for(const auto& [name,ref]:job.domain.refs){(void)name;sources.insert(folded(ref.first));}}items.push_back({{"id",job.id},{"status","READY"},{"source_pixels",{job.sourceW,job.sourceH}},{"target_pixels",{job.w,job.h}},{"edge_ratio",double(job.w)/job.sourceW}});jobs.push_back(std::move(job));}
  catch(const std::exception& e){blocked++;items.push_back({{"id",x.value("id","invalid")},{"status","BLOCKED"},{"error",e.what()}});}
 }
 Json animationAudit;
 if(batch.contains("animation_contract")){
  need(batch.at("animation_contract")=="static-structure-dynamic-fx","EXPANSION_ANIMATION_CONTRACT_UNSUPPORTED: "+batch.at("animation_contract").dump());
  animationAudit=static_animation_registration_audit(jobs);
  if(animationAudit.at("status")=="BLOCKED"){
   blocked+=int(animationAudit.at("failures").size());
   for(const auto& failure:animationAudit.at("failures"))items.push_back({{"id",failure.at("group").get<std::string>()+"/"+failure.at("frame").get<std::string>()},{"status","BLOCKED"},{"error","STATIC_ANIMATION_REGISTRATION_DRIFT"},{"diagnostic",failure}});
  }
 }
 for(const auto& output:outputs)need(!sources.contains(output),"an expanded output would replace another job's source");
 for(const auto& receipt:receipts)need(!outputs.contains(receipt)&&!sources.contains(receipt),"receipt aliases an image in the batch");
 if(batch.contains("animation_contract"))return{{"items",items},{"ready",jobs.size()},{"blocked",blocked},{"outputs_written",0},{"animation_registration",animationAudit}};
 return{{"items",items},{"ready",jobs.size()},{"blocked",blocked},{"outputs_written",0}};
}
inline Json make_fullraw_expansion_batch(const fs::path& spec,const std::string& digest,const fs::path& outputRoot){
 auto settings=expansion_settings(default_settings());auto batch=fullraw_batch_document(spec,digest,outputRoot,settings);
 std::vector<ExpansionJob> jobs;auto pre=check_expansion(batch,settings,jobs);
 need(pre.at("blocked")==0,"FULLRAW_PLAN_BLOCKED: "+pre.dump());return batch;
}
inline Json make_expansion_batch(const std::vector<fs::path>& sources,int sourceTier,int targetScale,std::array<int,2> customBase,const fs::path& outputRoot,const std::string& filter="h3-quality",const fs::path& mirrorSourceRoot={},bool allowPaddedMaster=false,const fs::path& referenceRoot={},const std::string& fitAnchor="",const fs::path& nativeAuthorityPath={},const std::string& animationContract=""){
 need(filter=="h3-quality","master expansion supports only the h3-quality filter; the legacy local-range sampler is verify-only");
 need(fitAnchor==""||fitAnchor=="bottom_right"||fitAnchor=="center","fit anchor must be bottom_right or center");
 need(animationContract==""||animationContract=="static-structure-dynamic-fx","animation contract must be static-structure-dynamic-fx");
 need(referenceRoot.empty()||fs::exists(referenceRoot)&&fs::is_directory(referenceRoot),"reference root must be an existing directory");
 Json authority=nativeAuthorityPath.empty()?Json{}:load_native_authority(nativeAuthorityPath);
 need(!sources.empty()&&sources.size()<=10000,"choose at least one PNG");need(targetScale>=3&&targetScale<=MAX_STANDARD_SIZE_MULTIPLE,"master target must be 3X.."+std::to_string(MAX_STANDARD_SIZE_MULTIPLE)+"X");
 need(sourceTier==2||sourceTier==3||sourceTier==0,"source tier must be2X,3X or explicit1X dimensions");
 auto root=clean_path(outputRoot);auto mirrorRoot=mirrorSourceRoot.empty()?fs::path{}:clean_path(mirrorSourceRoot);
 if(!mirrorRoot.empty())need(fs::exists(mirrorRoot)&&fs::is_directory(mirrorRoot),"mirror source root must be an existing directory");
 Json items=Json::array();Wic decoder;int n=0;
 for(const auto& p:sources){
  auto source=clean_path(p);auto raw=read_all(source);auto meta=decoder.frame(raw);auto base=customBase;
  if(sourceTier){need(meta.w%sourceTier==0&&meta.h%sourceTier==0,"PNG dimensions do not divide by selected source tier; specify original1X width/height");base={int(meta.w)/sourceTier,int(meta.h)/sourceTier};}
  if(!authority.is_null()){
   auto auth=native_authority_size(authority,source.parent_path().filename().wstring(),source.stem().wstring());
   if(customBase[0]>0||sourceTier)need(base==auth,"NATIVE_1X_AUTHORITY_CONFLICT: this master declares 1X "
       +std::to_string(base[0])+"x"+std::to_string(base[1])+" but the native authority says "
       +std::to_string(auth[0])+"x"+std::to_string(auth[1])+"; the 2015 HD pack (H3ab_spr/H3ab_bmp) must not be used as the size authority");
   base=auth;
  }
  need(base[0]>0&&base[1]>0,"original1X width/height required");
  fs::path relativeParent;
  if(!mirrorRoot.empty()){
   contained(source,mirrorRoot);auto relative=source.lexically_relative(mirrorRoot);
   need(!relative.empty()&&!relative.is_absolute(),"source cannot be made relative to mirror source root");
   for(const auto& part:relative)need(part!=fs::path(L".")&&part!=fs::path(L".."),"source escapes mirror source root");
   relativeParent=relative.parent_path();
  }
  // The 3X copy carries the master's own file name; the delivery directory, not a
  // scale suffix, is what marks the publish tier (user ruling 2026-10-31, and
  // AGENTS forbids `_3X`/`x3` in asset file names). Safety therefore comes from the
  // path, not from the name: the output must land in --output-root and may never be
  // written beside or on top of the master it came from.
  auto output=root/relativeParent/fs::path(source.stem().wstring()+L".png");contained(output,root);
  // The 3X now shares the master's file name, so the delivery directory is the only
  // thing separating them: refuse a plan whose 3X would land on, or beside, the master
  // it came from. A nested subdirectory under the master folder stays allowed, which is
  // how working and review trees are laid out.
  need(output!=source,"MASTER_EXPAND_OUTPUT_WOULD_REPLACE_MASTER: --output-root resolves onto the master "+str(source));
  need(clean_path(output.parent_path())!=clean_path(source.parent_path()),"MASTER_EXPAND_OUTPUT_BESIDE_MASTER: --output-root must be the 3X delivery directory, not the directory holding the master "+str(source));
  Json entry{{"id","MASTER_"+std::to_string(++n)},{"source",str(source)},{"source_sha256",sha(raw)},{"source_pixels",{meta.w,meta.h}},{"base_pixels",base},{"target_scale",targetScale},{"output",str(output)}};
  if(!authority.is_null()){entry["native_1x_pixels"]=base;entry["size_authority"]="native-1x";}
  if(!referenceRoot.empty()){
   auto identity=source.parent_path().filename();auto dir=clean_path(referenceRoot)/identity;
   need(fs::exists(dir)&&fs::is_directory(dir),"official reference directory is missing for this master: "+str(dir));
   auto want=folded((dir/(source.stem().wstring()+L".png")).wstring());fs::path ref;
   for(const auto& e:fs::directory_iterator(dir))if(folded(e.path().wstring())==want){ref=e.path();break;}
   need(!ref.empty(),"official reference frame is missing for this master: "+str(dir/(source.stem().wstring()+L".png")));
   auto rraw=read_all(ref);auto rmeta=decoder.frame(rraw);
   need(rmeta.w==base[0]&&rmeta.h==base[1],"official reference frame "+std::to_string(rmeta.w)+"x"+std::to_string(rmeta.h)+" is not the 1X slot "+std::to_string(base[0])+"x"+std::to_string(base[1])+"; the size authority is the 1999 native DEF, and a reference at a different size is usually the 2015 HD pack (H3ab_spr/H3ab_bmp)");
   entry["reference"]=str(ref);entry["reference_sha256"]=sha(rraw);entry["reference_pixels"]={rmeta.w,rmeta.h};
   if(!fitAnchor.empty())entry["fit_anchor"]=fitAnchor;
  }
  if(allowPaddedMaster)entry["padded_master_allowed"]=true;
  items.push_back(std::move(entry));
 }
 auto settings=default_settings();settings["filter"]=filter;settings=expansion_settings(settings);
 Json batch={{"schema","h3.master-expand.batch.v1"},{"algorithm",QUALITY_ALGORITHM},{"created_utc",now()},{"output_root",str(root)},{"receipt_directory",str(root/L"receipts")},{"settings",settings},{"items",items},{"note","Algorithmic resampling only; source files unchanged. New masters require visual review. No model inference, no native-detail claim and no game deployment."}};
 if(!mirrorRoot.empty())batch["mirror_source_root"]=str(mirrorRoot);
 if(!authority.is_null()){batch["native_1x_authority"]={{"path",authority.at("_authority_path")},{"sha256",authority.at("_authority_sha256")},{"source_lods",authority.value("source_lods",Json::array())}};}
 if(!animationContract.empty())batch["animation_contract"]=animationContract;
 std::vector<ExpansionJob> checked;auto pre=check_expansion(batch,settings,checked);need(pre["blocked"]==0,"EXPANSION_PLAN_BLOCKED: "+pre.dump());return batch;
}
inline Json verify_expanded_master(const ExpansionJob& job,bool replay){
 auto r=read_json(job.receipt);
 need(r.at("schema")== (job.domain.active?"h3.master-expand.receipt.v2":"h3.master-expand.receipt.v1")&&r.at("job_fingerprint")==job.fingerprint&&r.at("contract")==job.contract,"expanded-master receipt identity changed");
 need(r.at("algorithm")==job.contract.at("algorithm")&&r.at("backend")=="cpu"&&r.at("model_inference_performed")==false&&r.at("model_native_output")==false&&r.at("human_approved")==false&&r.at("game_deployed")==false&&r.at("runtime_accepted")==false,"expanded-master provenance/authority changed");
 need(r.at("source_sha256")==job.sourceSha&&r.at("source_pixels")==Json::array({job.sourceW,job.sourceH})&&r.at("output_pixels")==Json::array({job.w,job.h}),"expanded-master source or geometry changed");
 need(r.at("source")==str(job.source)&&r.at("output")==str(job.output)&&r.at("base_pixels")==job.contract["entry"]["base_pixels"]&&r.at("target_scale")==job.contract["entry"]["target_scale"]&&r.at("source_unchanged")==true,"expanded-master receipt fields changed");
 need(r.at("scale_x")==double(job.w)/job.samplingW&&r.at("scale_y")==double(job.h)/job.samplingH,"expanded-master scale changed");
 if(r.contains("fit_mode")){
  need(r.at("master_untouched").get<bool>()&&r.at("source_unchanged").get<bool>(),"expanded-master immutability claim changed");
  need(job.silhouette?r.at("fit_mode").get<std::string>()=="official-silhouette-fit"
      :r.at("fit_mode").get<std::string>()==master_fit_mode(job.samplingW,job.samplingH,job.w,job.h),"expanded-master fit mode changed");
  need(r.at("official_output_pixels")==Json::array({job.w,job.h}),"expanded-master output is not the official native size times the target scale");
  need(std::fabs(r.at("aspect_error_pct").get<double>()-job.aspectError*100.0)<1e-9,"expanded-master aspect error changed");
  if(!job.silhouette)need(master_fit_aspect_error(job.samplingW,job.samplingH,job.w,job.h)<=MASTER_FIT_ASPECT_CEILING,"expanded-master aspect error exceeds the fit ceiling");
  need(!r.contains("padded_master_allowed")||r.at("padded_master_allowed").get<bool>()==job.allowPadded,"expanded-master padded-canvas allowance changed");
 }
 auto source=read_all(job.source);need(sha(source)==job.sourceSha,"source changed on resume");auto png=read_all(job.output);Wic decoder;
 need(sha(png)==r.at("png_sha256").get<std::string>()&&png.size()==r.at("bytes"),"expanded PNG damaged/changed");auto pixels=decoder.decode_rgba(png,job.w,job.h);need(sha(pixels)==r.at("rgba_sha256").get<std::string>(),"expanded PNG readback differs");
 if(job.silhouette){
  auto s=r.at("silhouette");
  need(s.at("reference")==str(job.reference)&&s.at("reference_sha256")==job.referenceSha&&s.at("reference_pixels")==Json::array({job.referenceW,job.referenceH}),"silhouette reference identity changed");
  auto refRaw=read_all(job.reference);need(sha(refRaw)==job.referenceSha,"silhouette reference drift");
  std::string rn;auto refIm=decoder.decode(refRaw,true,rn);
  auto box=artwork_box(refIm,true);
  need(!box.empty&&Json::array({box.x0,box.y0,box.x1,box.y1})==s.at("reference_artwork_box_1x"),"silhouette reference box changed");
  need(box.matte==s.at("opaque_matte_stripped").get<bool>(),"silhouette matte claim changed");
  auto scaled=scale_box(box,job.referenceScale);
  need(Json::array({scaled.x0,scaled.y0,scaled.x1,scaled.y1})==s.at("slot_artwork_box_scaled"),"silhouette slot box changed");
  Image painted(job.w,job.h);
  for(int i=0;i<job.w*job.h;i++)painted.p[i].a=pixels[std::size_t(i)*4+3]/255.f;
  auto pb=artwork_box(painted);
  need(!pb.empty&&pb.x0>=job.slotBox.x0&&pb.y0>=job.slotBox.y0&&pb.x1<=job.slotBox.x1&&pb.y1<=job.slotBox.y1,
   "expanded artwork overflows the official silhouette box ("+std::to_string(pb.x0)+","+std::to_string(pb.y0)+","+std::to_string(pb.x1)+","+std::to_string(pb.y1)
   +" outside "+std::to_string(job.slotBox.x0)+","+std::to_string(job.slotBox.y0)+","+std::to_string(job.slotBox.x1)+","+std::to_string(job.slotBox.y1)+")");
  need(s.at("overflow")=="NONE","silhouette overflow claim changed");
 }
 if(job.domain.active){
  auto detail=fullraw_detail(job.domain,pixels);
  if(detail.at("crop_status")=="ZERO_OUTSIDE_CROP"){
   auto cropPixels=fullraw_crop(job.domain,pixels);auto cropPng=read_all(job.domain.cropOutput);
   int cw=job.domain.crop[0]*job.domain.scale,ch=job.domain.crop[1]*job.domain.scale;
   need(decoder.decode_rgba(cropPng,cw,ch)==cropPixels,"FULLRAW_CROP_BYTES_DIFFER_FROM_FULL_CANVAS");
   detail["crop"]={{"path",str(job.domain.cropOutput)},{"pixels",{cw,ch}},{"bytes",cropPng.size()},
                    {"sha256",sha(cropPng)},{"rgba_sha256",sha(cropPixels)}};
  }else need(!fs::exists(job.domain.cropOutput),"FULLRAW_REJECTED_CROP_MUST_NOT_EXIST");
  need(r.at("full_raw_sampling_domain").dump()==detail.dump(),"FULLRAW_DIAGNOSTIC_OR_CROP_RECEIPT_DRIFT");
 }
 if(replay){std::string note;auto im=decoder.decode(source,true,note);if(job.domain.active)im=fullraw_compose(job.domain,im);
  auto expected=quantize(job.silhouette?silhouette_expand_cpu(im,job.w,job.h,job.slotBox,artwork_box(im),job.fitAnchor):expansion_replay(im,job.w,job.h,job.contract),true);
  need(pixels==expected,"expanded PNG differs from independent CPU replay");}
 if(job.domain.active)fullraw_recheck(job.contract.at("entry"),job.domainBatch,job.domain);
 return r;
}
inline Json process_expansion(const ExpansionJob& job,const Json& settings,bool verifyOnly){
 auto algorithm=job.contract.at("algorithm");auto start=Clock::now();auto pin=open_read(job.source);
 std::vector<Handle> sourcePins;
 if(job.domain.active)for(const auto& [name,ref]:job.domain.refs){(void)name;sourcePins.push_back(open_read(ref.first));}
 if(fs::exists(job.receipt)){auto prior=verify_expanded_master(job,verifyOnly||job.domain.active);
  if(job.domain.active&&prior.at("full_raw_sampling_domain").at("crop_status")!="ZERO_OUTSIDE_CROP")
   return {{"id",job.id},{"status","FAILED"},{"code",prior.at("full_raw_sampling_domain").at("crop_failure")},{"receipt",str(job.receipt)},{"diagnostic_reused",true},{"crop_written",false}};
  return{{"id",job.id},{"status",verifyOnly?"VERIFIED":"REUSED_VERIFIED"},{"receipt",str(job.receipt)},{"total_ms",ms(start)}};}
 need(!verifyOnly,"expanded-master receipt missing");auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"source drift before expansion");Wic decoder;std::string colorNote;
 auto t=Clock::now();auto im=decoder.decode(raw,true,colorNote);if(job.domain.active)im=fullraw_compose(job.domain,im);double decodeMs=ms(t);
 auto pad=master_pad_signature(im);
 need(job.allowPadded||!pad.padded,"master canvas is padded around its own artwork (bands left/right/top/bottom "+std::to_string(int(pad.left*100))+"%/"+std::to_string(int(pad.right*100))+"%/"+std::to_string(int(pad.top*100))+"%/"+std::to_string(int(pad.bottom*100))+"%); the ratio ceiling passes but the artwork would shrink inside the official cell - re-derive the master so the artwork fills its canvas, or plan with --allow-padded-master");
 t=Clock::now();Image expanded;ArtworkBox masterBox;
 if(job.silhouette){masterBox=artwork_box(im);expanded=silhouette_expand_cpu(im,job.w,job.h,job.slotBox,masterBox,job.fitAnchor);}
 else expanded=expansion_replay(im,job.w,job.h,job.contract);
 double resizeMs=ms(t);t=Clock::now();
 auto pixels=quantize(expanded,true),png=decoder.encode(pixels,job.w,job.h,settings.at("png_filter")=="adaptive");need(decoder.decode_rgba(png,job.w,job.h)==pixels,"expanded PNG encoding roundtrip differs");double encodeMs=ms(t);
 Json receipt={{"schema",job.domain.active?"h3.master-expand.receipt.v2":"h3.master-expand.receipt.v1"},{"created_utc",now()},{"tool_version",VERSION},{"job_fingerprint",job.fingerprint},{"contract",job.contract},{"algorithm",algorithm},{"backend","cpu"},{"source",str(job.source)},{"source_sha256",job.sourceSha},{"source_pixels",{job.sourceW,job.sourceH}},{"output",str(job.output)},{"output_pixels",{job.w,job.h}},{"base_pixels",job.contract["entry"]["base_pixels"]},{"target_scale",job.contract["entry"]["target_scale"]},{"scale_x",double(job.w)/job.samplingW},{"scale_y",double(job.h)/job.samplingH},{"bytes",png.size()},{"png_sha256",sha(png)},{"rgba_sha256",sha(pixels)},{"color_management",colorNote},{"decode_ms",decodeMs},{"resample_ms",resizeMs},{"encode_ms",encodeMs},{"source_unchanged",true},{"master_untouched",true},{"fit_mode",job.fitMode},{"aspect_error_pct",job.aspectError*100.0},{"official_output_pixels",{job.w,job.h}},{"artwork_bands_pct",{pad.left*100,pad.right*100,pad.top*100,pad.bottom*100}},{"padded_master_allowed",job.allowPadded},{"model_inference_performed",false},{"model_native_output",false},{"human_approved",false},{"game_deployed",false},{"runtime_accepted",false}};
 if(job.silhouette)receipt["silhouette"]={{"reference",str(job.reference)},{"reference_sha256",job.referenceSha},
  {"reference_pixels",{job.referenceW,job.referenceH}},
  {"reference_artwork_box_1x",{job.slotBox1x.x0,job.slotBox1x.y0,job.slotBox1x.x1,job.slotBox1x.y1}},
  {"opaque_matte_stripped",job.slotBox1x.matte},
  {"slot_artwork_box_scaled",{job.slotBox.x0,job.slotBox.y0,job.slotBox.x1,job.slotBox.y1}},
  {"master_artwork_box",{masterBox.x0,masterBox.y0,masterBox.x1,masterBox.y1}},
  {"artwork_pixels",{masterBox.x1-masterBox.x0,masterBox.y1-masterBox.y0}},
  {"fit_anchor",job.fitAnchor},{"overflow","NONE"}};
 Json detail;std::vector<std::uint8_t> cropPng;
 if(job.domain.active){
  detail=fullraw_detail(job.domain,pixels);
  if(detail.at("crop_status")=="ZERO_OUTSIDE_CROP"){
   auto cropPixels=fullraw_crop(job.domain,pixels);int cw=job.domain.crop[0]*job.domain.scale,ch=job.domain.crop[1]*job.domain.scale;
   cropPng=decoder.encode(cropPixels,cw,ch,settings.at("png_filter")=="adaptive");
   need(decoder.decode_rgba(cropPng,cw,ch)==cropPixels,"FULLRAW_CROP_ENCODING_CHANGED_PIXELS");
   detail["crop"]={{"path",str(job.domain.cropOutput)},{"pixels",{cw,ch}},{"bytes",cropPng.size()},
                    {"sha256",sha(cropPng)},{"rgba_sha256",sha(cropPixels)}};
  }else need(!fs::exists(job.domain.cropOutput),"FULLRAW_REJECTED_CROP_PATH_ALREADY_EXISTS");
  fullraw_recheck(job.contract.at("entry"),job.domainBatch,job.domain);
  receipt["full_raw_sampling_domain"]=detail;
 }
 same_or_new(job.output,png);
 if(job.domain.active&&!cropPng.empty())same_or_new(job.domain.cropOutput,cropPng);
 need(sha(read_all(job.source))==job.sourceSha,"source changed during expansion");
 if(job.domain.active)fullraw_recheck(job.contract.at("entry"),job.domainBatch,job.domain);
 write_json_new(job.receipt,receipt);verify_expanded_master(job,false);
 if(job.domain.active&&detail.at("crop_status")!="ZERO_OUTSIDE_CROP")
  return {{"id",job.id},{"status","FAILED"},{"code",detail.at("crop_failure")},
   {"diagnostic_output",str(job.output)},{"diagnostic_output_written",true},{"receipt",str(job.receipt)},
   {"diagnostics",detail.at("diagnostics")},{"crop_written",false}};
 return{{"id",job.id},{"status","EXPANDED_VERIFIED"},{"output",str(job.output)},{"receipt",str(job.receipt)},{"source_pixels",{job.sourceW,job.sourceH}},{"output_pixels",{job.w,job.h}},{"resample_ms",resizeMs},{"total_ms",ms(start)}};
}
inline Json run_expansion(const Json& batch,const Json& settings,bool verifyOnly=false){
 auto algorithm=batch.at("algorithm");auto start=Clock::now();std::vector<ExpansionJob> jobs;auto pre=check_expansion(batch,settings,jobs,verifyOnly);
 Json report={{"schema","h3.master-expand.run.v1"},{"created_utc",now()},{"algorithm",algorithm},{"backend","cpu"},{"preflight",pre},{"results",Json::array()},{"model_inference_performed",false},{"game_deployed",false}};
 if(pre["blocked"]!=0||jobs.empty()){report.update({{"status","PREFLIGHT_BLOCKED"},{"failed",pre["blocked"]},{"outputs_written",0}});return report;}
 auto receiptDir=clean_path(path(batch.at("receipt_directory")));fs::create_directories(receiptDir);Handle lock(CreateFileW((receiptDir/L".h3-master-expand.lock").c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr));need(lock.h!=INVALID_HANDLE_VALUE,"another expansion holds this output root");
 auto host=host_memory();auto limit=std::min(settings.at("memory_gib").get<std::uint64_t>()*1024*1024*1024,std::min(host["physical_bytes"].get<std::uint64_t>()*3/4,host["available_bytes"].get<std::uint64_t>()*3/4));need(limit>=256ull*1024*1024,"too little available RAM");MemoryGate memory(limit);
 unsigned count=std::min<unsigned>(settings.at("workers").get<unsigned>(),unsigned(jobs.size()));std::atomic<size_t> next=0;std::mutex mutex;std::vector<std::thread> workers;
 for(unsigned n=0;n<count;n++)workers.emplace_back([&]{while(true){size_t i=next++;if(i>=jobs.size())break;Json result;try{ComInit com;MemoryLease lease(memory,jobs[i].admission);result=process_expansion(jobs[i],settings,verifyOnly);}catch(const std::exception& e){result={{"id",jobs[i].id},{"status","FAILED"},{"error",e.what()}};}std::lock_guard guard(mutex);report["results"].push_back(result);}});
 for(auto& worker:workers)worker.join();int failed=0,written=0;for(auto& result:report["results"]){failed+=result["status"]=="FAILED";written+=result["status"]=="EXPANDED_VERIFIED";}
 report.update({{"status",failed?"COMPLETED_WITH_FAILURES":"COMPLETED"},{"failed",failed},{"outputs_written",written},{"workers",count},{"wall_ms",ms(start)},{"memory_budget_bytes",limit},{"peak_admitted_estimate_bytes",memory.peak}});report.update(process_memory_peaks());return report;
}
}
