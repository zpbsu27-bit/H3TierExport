#pragma once
#include "wic.hpp"
#include <cctype>
#include <map>
#include <tuple>

namespace h3tier {
inline constexpr const char* SKILL_VARIANT_ALGORITHM="h3-quality-high-resolution-master-variant-v2";
inline constexpr const char* SKILL_VARIANT_REGISTRATION="proportional-contain";

struct SkillVariantJob {
 std::string id,semanticId,variant,resource,outputFilename,sourceSha;
 Json key;
 std::string registration;
 int nativeW=0,nativeH=0;
 fs::path source;
 int sourceW=0,sourceH=0,targetW=0,targetH=0;
};

inline std::string skill_variant_lower_ascii(std::string value){
 for(auto& c:value)c=(char)std::tolower((unsigned char)c);return value;
}
inline std::string skill_resource_stem(const std::string& resource){
 need(!resource.empty()&&resource.find_first_of("/\\:")==std::string::npos,"skill resource must be an official PCX basename");
 auto dot=resource.rfind('.');need(dot!=std::string::npos&&skill_variant_lower_ascii(resource.substr(dot))==".pcx","skill resource must use the official PCX extension");
 auto stem=resource.substr(0,dot);need(!stem.empty()&&token(stem)==stem,"unsafe official skill resource basename");return stem;
}

inline std::vector<SkillVariantJob> skill_variant_jobs(const Json& manifest,const std::string& expectedManifestSha){
 need(valid_sha(expectedManifestSha),"skill variant manifest SHA256 required");
 need(manifest.is_object()&&manifest.value("schema","")=="h3.skill-master-variant-manifest.v2","unsupported skill variant manifest");
 need(manifest.value("registration","")==SKILL_VARIANT_REGISTRATION||manifest.value("registration","")=="full-canvas-axis-normalization","unsupported master registration mode");
 need(manifest.value("scale_semantics","")=="source-resolution-master-no-tier-export","skill variants must target native pixels, never 3X tiers");
 need(manifest.value("algorithm","")==SKILL_VARIANT_ALGORITHM,"skill variant algorithm changed");
 need(manifest.contains("items")&&manifest["items"].is_array()&&!manifest["items"].empty()&&manifest["items"].size()<=5000,"skill variant item count");
 std::set<std::string> ids,slots;std::map<std::wstring,std::tuple<std::string,int,int>> sourceCache;std::vector<SkillVariantJob> jobs;jobs.reserve(manifest["items"].size());Wic decoder;
 for(const auto& item:manifest["items"]){
  SkillVariantJob job;job.id=token(item.at("id"));job.semanticId=item.at("semantic_id").get<std::string>();job.variant=token(item.at("variant"));job.resource=item.at("resource").get<std::string>();job.outputFilename=item.at("output_filename").get<std::string>();job.key=item.at("key");job.registration=manifest.at("registration");
  need(!job.semanticId.empty()&&job.semanticId.size()<=160&&!job.resource.empty()&&job.resource.size()<=200,"skill variant labels missing");
  need(ids.insert(job.id).second,"duplicate skill variant ID");need(job.key.is_array()&&job.key.size()==4&&job.key[0]=="def","skill variant key must be a DEF slot");auto identity=token(job.key[1].get<std::string>());int group=integer(job.key[2],0,65535,"group"),frame=integer(job.key[3],0,65535,"frame");auto slot="def:"+identity+":"+std::to_string(group)+":"+std::to_string(frame);need(slots.insert(slot).second,"duplicate native skill slot in variant manifest");
  need(!job.outputFilename.empty()&&job.outputFilename.find_first_of("/\\:")==std::string::npos,"skill output filename must be a basename");need(std::filesystem::path(job.outputFilename).extension()==L".png","skill output must be PNG");
  job.source=clean_path(path(item.at("source_png")));need(valid_sha(item.at("source_sha256")),"skill variant source SHA256 required");job.sourceSha=item.at("source_sha256");
  auto sourceKey=folded(job.source);auto cached=sourceCache.find(sourceKey);
  if(cached==sourceCache.end()){
   auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"skill master source SHA256 drift: "+str(job.source));auto meta=decoder.frame(raw);cached=sourceCache.emplace(sourceKey,std::tuple{job.sourceSha,(int)meta.w,(int)meta.h}).first;
  }else need(std::get<0>(cached->second)==job.sourceSha,"one skill master has conflicting source hashes");
  job.sourceW=std::get<1>(cached->second);job.sourceH=std::get<2>(cached->second);
  auto sourcePixels=pair(item.at("source_pixels"),1,"skill source pixels");need(sourcePixels[0]==job.sourceW&&sourcePixels[1]==job.sourceH,"skill master source dimensions changed");
  auto nativePixels=pair(item.at("native_pixels"),1,"native skill reference pixels");job.nativeW=nativePixels[0];job.nativeH=nativePixels[1];
  const std::set<std::pair<int,int>> officialSizes={{32,32},{44,44},{58,64},{82,93}};
  need(officialSizes.contains({job.nativeW,job.nativeH}),"unknown official skill aspect ratio");
  job.targetW=std::max(job.sourceW,int((std::int64_t(job.sourceH)*job.nativeW+job.nativeH-1)/job.nativeH));
  job.targetH=std::max(job.sourceH,int((std::int64_t(job.sourceW)*job.nativeH+job.nativeW-1)/job.nativeW));
  need(item.at("target_pixels")==Json::array({job.targetW,job.targetH}),"master canvas must contain every source pixel at the official aspect ratio");
  need(std::uint64_t(job.targetW)*job.targetH<=MAX_PIXELS,"master variant pixel limit");jobs.push_back(std::move(job));
 }
 std::map<std::string,std::vector<std::size_t>> byResource;std::set<std::string> outputNames;
 for(std::size_t i=0;i<jobs.size();i++)byResource[skill_variant_lower_ascii(jobs[i].resource)].push_back(i);
 for(auto& [resource,indices]:byResource){
  (void)resource;std::sort(indices.begin(),indices.end(),[&](std::size_t a,std::size_t b){const auto&x=jobs[a].key;const auto&y=jobs[b].key;return std::tuple{x[1].get<std::string>(),x[2].get<int>(),x[3].get<int>()}<std::tuple{y[1].get<std::string>(),y[2].get<int>(),y[3].get<int>()};});
  for(std::size_t rank=0;rank<indices.size();rank++){auto& job=jobs[indices[rank]];auto expected=skill_resource_stem(job.resource);if(indices.size()>1)expected+="_"+std::to_string(rank);expected+=".png";need(job.outputFilename==expected,"skill output name must match official PCX basename and duplicate suffix order: "+expected);need(outputNames.insert(skill_variant_lower_ascii(job.outputFilename)).second,"duplicate official skill output filename");}
 }
 return jobs;
}

inline Json skill_variant_manifest_checked(const fs::path& manifestPath,const std::string& expectedSha,const std::string& buildSourceSha){
 need(valid_sha(Json(buildSourceSha)),"H3TierExport build source manifest SHA256 required");
 auto raw=read_all(manifestPath,32*1024*1024);need(sha(raw)==expectedSha,"skill variant manifest SHA256 drift");auto manifest=parse_json(raw);auto jobs=skill_variant_jobs(manifest,expectedSha);
 const auto& inventory=manifest.at("official_inventory");need(inventory.is_object()&&valid_sha(inventory.at("sha256")),"official native inventory binding missing");
 const auto& mapping=manifest.at("master_mapping");need(mapping.is_object()&&valid_sha(mapping.at("sha256")),"current mother mapping binding missing");
 auto inventoryBytes=read_all(clean_path(path(inventory.at("path"))));need(sha(inventoryBytes)==inventory.at("sha256").get<std::string>(),"official native inventory drift");
 auto mappingBytes=read_all(clean_path(path(mapping.at("path"))));need(sha(mappingBytes)==mapping.at("sha256").get<std::string>(),"current skill mother mapping drift");
 return{{"manifest",str(clean_path(manifestPath))},{"manifest_sha256",expectedSha},{"build_source_manifest_sha256",buildSourceSha},{"official_inventory",inventory},{"master_mapping",mapping},{"ready",jobs.size()},{"blocked",0},{"outputs_written",0},{"target_kind","high-resolution-master"},{"registration",manifest.at("registration")},{"algorithm",SKILL_VARIANT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"target_scale",nullptr},{"model_inference_performed",false},{"game_deployed",false}};
}

inline std::vector<std::uint8_t> skill_variant_png_from_image(const SkillVariantJob& job,const Image& image,Wic& decoder){
 need(image.w==job.sourceW&&image.h==job.sourceH,"skill master geometry changed during derivation");int contentW=job.targetW,contentH=job.targetH;
 if(job.registration=="proportional-contain"){
  double ratio=std::min(double(job.targetW)/image.w,double(job.targetH)/image.h);
  contentW=std::min(job.targetW,std::max(1,int(std::llround(image.w*ratio))));contentH=std::min(job.targetH,std::max(1,int(std::llround(image.h*ratio))));
 }
 auto resized=quality_resize_cpu(image,contentW,contentH);
 auto pixels=place_rgba(resized,job.targetW,job.targetH,(job.targetW-contentW)/2,(job.targetH-contentH)/2,true);auto png=decoder.encode(pixels,job.targetW,job.targetH,true);
 need(decoder.decode_rgba(png,job.targetW,job.targetH)==pixels,"skill native variant PNG roundtrip mismatch");return png;
}
inline std::vector<std::uint8_t> skill_variant_png(const SkillVariantJob& job,Wic& decoder,std::string& colorNote){
 auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"skill master changed before native-size derivation");auto image=decoder.decode(raw,true,colorNote);auto png=skill_variant_png_from_image(job,image,decoder);
 need(sha(read_all(job.source))==job.sourceSha,"skill master changed during native-size derivation");return png;
}

inline Json verify_skill_variant_export(const Json& manifest,const std::string& manifestSha,const std::string& buildSourceSha,const fs::path& outputRoot,bool replay){
 auto jobs=skill_variant_jobs(manifest,manifestSha);auto root=clean_path(outputRoot);auto receiptPath=root/L"skill-master-variant-receipt.json";auto receipt=read_json(receiptPath);
 need(receipt.value("schema","")=="h3.skill-master-variant-receipt.v2"&&receipt.value("manifest_sha256","")==manifestSha&&receipt.value("algorithm","")==SKILL_VARIANT_ALGORITHM&&receipt.value("filter_algorithm","")==QUALITY_ALGORITHM&&receipt.value("build_source_manifest_sha256","")==buildSourceSha,"skill variant receipt identity/build/kernel changed");
 need(receipt.at("registration")==manifest.at("registration")&&receipt.contains("target_scale")&&receipt.at("target_scale").is_null()&&receipt.value("model_inference_performed",true)==false&&receipt.value("game_deployed",true)==false&&receipt.value("runtime_accepted",true)==false,"skill variant receipt claims invalid authority");
 need(receipt.at("outputs").is_array()&&receipt.at("outputs").size()==jobs.size(),"skill variant receipt output count changed");
 std::map<std::string,const Json*> saved;for(const auto& item:receipt.at("outputs"))need(saved.emplace(item.at("id").get<std::string>(),&item).second,"duplicate skill variant receipt ID");
 Wic decoder;int verified=0;
 for(const auto& job:jobs){
  need(saved.contains(job.id),"skill variant receipt item missing: "+job.id);const auto& row=*saved.at(job.id);auto bytes=read_all(job.source);need(sha(bytes)==job.sourceSha,"skill source drift during verification");
  need(row.at("key")==job.key&&row.at("semantic_id")==job.semanticId&&row.at("variant")==job.variant&&row.at("resource")==job.resource&&row.at("output_filename")==job.outputFilename&&row.at("source_sha256")==job.sourceSha&&row.at("source_pixels")==Json::array({job.sourceW,job.sourceH})&&row.at("target_pixels")==Json::array({job.targetW,job.targetH})&&row.at("native_pixels")==Json::array({job.nativeW,job.nativeH}),"skill variant receipt contract changed: "+job.id);
  auto output=clean_path(path(row.at("output")));contained(output,root);need(output.filename().string()==job.outputFilename,"skill variant output filename differs from official resource naming");
  auto png=read_all(output);auto outputSha=sha(png);need(outputSha==row.at("png_sha256").get<std::string>()&&png.size()==row.at("bytes"),"skill variant PNG hash differs");auto pixels=decoder.decode_rgba(png,job.targetW,job.targetH);need(sha(pixels)==row.at("rgba_sha256").get<std::string>(),"skill variant pixels differ");
  if(replay){std::string note;auto expected=skill_variant_png(job,decoder,note);need(expected==png,"skill variant differs from independent CPU replay: "+job.id);}
  verified++;
 }
 return{{"status","VERIFIED"},{"verified",verified},{"outputs_written",0},{"manifest_sha256",manifestSha},{"algorithm",SKILL_VARIANT_ALGORITHM},{"independent_cpu_replay",replay},{"model_inference_performed",false},{"game_deployed",false}};
}

inline Json run_skill_variant_export(const Json& manifest,const std::string& manifestSha,const std::string& buildSourceSha,const fs::path& outputDirectory,bool verifyOnly){
 auto checked=skill_variant_jobs(manifest,manifestSha);auto root=clean_path(outputDirectory);
 for(const auto& job:checked){auto src=folded(job.source),dest=folded(root);if(src.rfind(dest+L"\\",0)==0)throw std::runtime_error("skill variant output root contains a source master");}
 if(verifyOnly)return verify_skill_variant_export(manifest,manifestSha,buildSourceSha,root,true);
 auto receiptPath=root/L"skill-master-variant-receipt.json";
 if(fs::exists(receiptPath)){auto verified=verify_skill_variant_export(manifest,manifestSha,buildSourceSha,root,false);verified["status"]="REUSED_VERIFIED";return verified;}
 need(!fs::exists(root)||fs::is_directory(root),"skill variant output root is not a directory");fs::create_directories(root);Handle rootLock(CreateFileW((root/L".skill-master-variants.lock").c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr));need(rootLock.h!=INVALID_HANDLE_VALUE,"another master derivation owns this output root");Wic decoder;std::vector<Json> outputRows(checked.size());std::map<std::wstring,std::vector<std::size_t>> bySource;
 for(std::size_t i=0;i<checked.size();i++)bySource[folded(checked[i].source)].push_back(i);
 int count=0;
 for(const auto& [sourcePath,indices]:bySource){
  (void)sourcePath;const auto& first=checked[indices.front()];auto raw=read_all(first.source);need(sha(raw)==first.sourceSha,"skill master changed before native-size batch");std::string colorNote;auto image=decoder.decode(raw,true,colorNote);
  for(auto index:indices){const auto& job=checked[index];auto png=skill_variant_png_from_image(job,image,decoder);auto pngSha=sha(png);auto output=root/path(job.outputFilename);
   same_or_new(output,png);auto pixels=decoder.decode_rgba(read_all(output),job.targetW,job.targetH);
   outputRows[index]={{"id",job.id},{"key",job.key},{"semantic_id",job.semanticId},{"variant",job.variant},{"resource",job.resource},{"output_filename",job.outputFilename},{"source",str(job.source)},{"source_sha256",job.sourceSha},{"source_pixels",{job.sourceW,job.sourceH}},{"target_pixels",{job.targetW,job.targetH}},{"native_pixels",{job.nativeW,job.nativeH}},{"output",str(output)},{"bytes",png.size()},{"png_sha256",pngSha},{"rgba_sha256",sha(pixels)},{"color_management",colorNote}};count++;
  }
  need(sha(read_all(first.source))==first.sourceSha,"skill master changed during native-size batch");
 }
 Json outputs=Json::array();for(auto& row:outputRows)outputs.push_back(std::move(row));
 Json receipt={{"schema","h3.skill-master-variant-receipt.v2"},{"created_utc",now()},{"manifest_sha256",manifestSha},{"build_source_manifest_sha256",buildSourceSha},{"algorithm",SKILL_VARIANT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"registration",manifest.at("registration")},{"target_scale",nullptr},{"target_kind","high-resolution-master"},{"backend","cpu"},{"outputs",outputs},{"outputs_written",count},{"model_inference_performed",false},{"model_native_output",false},{"art_approved",false},{"runtime_accepted",false},{"game_deployed",false}};
 write_json_new(receiptPath,receipt);return{{"status","EXPORTED_VERIFIED"},{"ready",checked.size()},{"outputs_written",count},{"output_root",str(root)},{"receipt",str(receiptPath)},{"manifest_sha256",manifestSha},{"build_source_manifest_sha256",buildSourceSha},{"algorithm",SKILL_VARIANT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"target_kind","high-resolution-master"},{"target_scale",nullptr},{"model_inference_performed",false},{"game_deployed",false}};
}

inline Json run_skill_variant_command(const std::string& command,const fs::path& manifestPath,const std::string& manifestSha,const std::string& buildSourceSha,const fs::path& outputRoot){
 auto checked=skill_variant_manifest_checked(manifestPath,manifestSha,buildSourceSha);auto manifestBytes=read_all(manifestPath,32*1024*1024);need(sha(manifestBytes)==manifestSha,"skill variant manifest changed after preflight");auto manifest=parse_json(manifestBytes);
 if(command=="skill-variant-check"){checked["status"]="READY";checked["output_root"]=str(clean_path(outputRoot));return checked;}
 if(command=="skill-variant-export")return run_skill_variant_export(manifest,manifestSha,buildSourceSha,outputRoot,false);
 if(command=="skill-variant-verify")return run_skill_variant_export(manifest,manifestSha,buildSourceSha,outputRoot,true);
 throw std::runtime_error("unknown skill variant command: "+command);
}
}
