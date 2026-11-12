#pragma once
// A frozen source-domain adapter, not a new sampler. Caller-owned placement
// and native-slot contracts remain distinct from art or game acceptance.
#include "motion.hpp"
#include "native_review.hpp"

namespace h3tier {
inline bool fullraw_batch(const Json& batch){
 return batch.value("schema","")=="h3.master-expand.batch.v2";
}
struct FullRawDomain {
 bool active=false,fullCanvas=false;
 int k=1,scale=3,tx=0,ty=0,w=0,h=0,rawW=0,rawH=0;
 std::array<int,2> canvas{},crop{},origin{};
 fs::path cropOutput;
 Json binding,proof;
 std::map<std::wstring,std::pair<fs::path,std::string>> refs;
};
inline std::vector<std::uint8_t> fullraw_ref(const Json& reference,FullRawDomain& domain){
 native_review_fields(reference,{"path","sha256"});
 need(reference.at("path").is_string()&&valid_sha(reference.at("sha256")),"FULLRAW_REFERENCE_INVALID");
 auto requested=path(reference.at("path"));need(requested.is_absolute(),"FULLRAW_ABSOLUTE_REFERENCE_REQUIRED");
 auto file=native_review_output_path(requested);auto raw=read_all(file,256ull*1024*1024);
 auto digest=sha(raw);need(digest==reference.at("sha256").get<std::string>(),"FULLRAW_REFERENCE_DRIFT: "+str(file));
 auto key=folded(file);auto found=domain.refs.find(key);
 need(found==domain.refs.end()||found->second.second==digest,"FULLRAW_CONFLICTING_REFERENCE");
 domain.refs[key]={file,digest};return raw;
}
inline Json fullraw_spec(const Json& reference,FullRawDomain& domain){
 auto spec=parse_json(fullraw_ref(reference,domain));
 native_review_fields(spec,{"schema","target_scale","native_contracts","items"});
 need(spec.at("schema")=="h3.master-expand.fullraw-spec.v1"||
      spec.at("schema")=="h3.master-expand.fullraw-spec.v2","FULLRAW_SPEC_SCHEMA_INVALID");
 need(spec.at("target_scale").is_number_integer()&&!spec.at("target_scale").is_boolean(),"FULLRAW_SCALE_INTEGER_REQUIRED");
 int scale=spec.at("target_scale").get<int>();need(scale==1||scale==3,"FULLRAW_DIRECT_SCALE_MUST_BE_1_OR_3");
 need(spec.at("items").is_array()&&!spec.at("items").empty()&&spec.at("items").size()<=64,"FULLRAW_BATCH_LIMIT_1_TO_64");
 need(spec.at("native_contracts").is_array()&&!spec.at("native_contracts").empty()&&spec.at("native_contracts").size()<=100,"FULLRAW_NATIVE_CONTRACT_REQUIRED");
 for(const auto& ref:spec.at("native_contracts")){
  auto contract=parse_json(fullraw_ref(ref,domain));
  need(contract.contains("projection_source"),"FULLRAW_ORIGINAL_NATIVE_PROJECTION_REQUIRED");
  fullraw_ref(contract.at("projection_source"),domain);
 }
 return spec;
}
inline Json fullraw_entry(const Json& item,const Json& spec,const Json& native,const fs::path& root){
 const bool fullCanvas=spec.at("schema")=="h3.master-expand.fullraw-spec.v2";
 if(fullCanvas){
  native_review_fields(item,{"id","key","source","source_layout","lineage"});
  need(item.at("source_layout")=="full-native-canvas","FULLRAW_SOURCE_LAYOUT_UNSUPPORTED");
 }else native_review_fields(item,{"id","key","source","natural_k","translation","lineage"});
 const auto& source=item.at("source");native_review_fields(source,{"path","sha256","pixels","rgba_sha256"});
 auto id=token(item.at("id"));int scale=spec.at("target_scale").get<int>();
 need(item.at("key")==native.at("key"),"FULLRAW_NATIVE_SLOT_MISMATCH");
 Json domain={{"key",item.at("key")},{"native_entry",native},{"lineage",item.at("lineage")},
              {"raw_rgba_sha256",source.at("rgba_sha256")},
              {"crop_output",str(root/path(id+"-crop-"+std::to_string(scale)+"x.png"))}};
 if(fullCanvas)domain["source_layout"]="full-native-canvas";
 else{domain["natural_k"]=item.at("natural_k");domain["translation"]=item.at("translation");}
 return {{"id",id},{"source",source.at("path")},{"source_sha256",source.at("sha256")},
  {"source_pixels",source.at("pixels")},{"base_pixels",native.at("targets").at("canvas_1x")},
  {"target_scale",scale},{"output",str(root/path(id+"-full-"+std::to_string(scale)+"x.png"))},
  {"full_raw_sampling_domain",domain}};
}
inline Json fullraw_batch_document(const fs::path& specPath,const std::string& specSha,const fs::path& outputRoot,const Json& settings){
 FullRawDomain domain;Json ref={{"path",str(specPath)},{"sha256",specSha}};
 auto spec=fullraw_spec(ref,domain);auto native=motion_native_entries(spec);
 auto root=native_review_output_path(outputRoot);
 need(native_review_within(root,native_review_product_root()/L"build"),"FULLRAW_OUTPUT_MUST_STAY_IN_PRODUCT_BUILD");
 std::set<std::string> ids;Json items=Json::array();
 for(const auto& item:spec.at("items")){
  const auto id=token(item.at("id"));need(ids.insert(id).second,"FULLRAW_DUPLICATE_ID");
  auto key=item.at("key").dump();need(native.contains(key),"FULLRAW_UNBOUND_NATIVE_SLOT");
  items.push_back(fullraw_entry(item,spec,native.at(key),root));
 }
 return {{"schema","h3.master-expand.batch.v2"},{"sampling_mode","full_raw_sampling_domain"},
  {"algorithm",QUALITY_ALGORITHM},{"created_utc",now()},{"fullraw_spec",ref},
  {"output_root",str(root)},{"receipt_directory",str(root/L"receipts")},{"settings",settings},{"items",items},
  {"note","Full raw source domain, one existing quality-kernel sampling stage. Diagnostic full canvas is retained on outcrop failure. No art, native timing or game approval."}};
}
inline FullRawDomain fullraw_read(const Json& entry,const Json& batch){
 FullRawDomain d;d.active=true;
 need(fullraw_batch(batch)&&batch.at("sampling_mode")=="full_raw_sampling_domain"&&batch.at("algorithm")==QUALITY_ALGORITHM,"FULLRAW_BATCH_MODE_INVALID");
 auto spec=fullraw_spec(batch.at("fullraw_spec"),d);auto natives=motion_native_entries(spec);
 const Json* recipe=nullptr;std::set<std::string> ids;
 for(const auto& item:spec.at("items")){
  auto id=token(item.at("id"));need(ids.insert(id).second,"FULLRAW_DUPLICATE_ID");
  if(item.at("id")==entry.at("id"))recipe=&item;
 }
 need(recipe!=nullptr,"FULLRAW_RECIPE_NOT_IN_FROZEN_SPEC");
 auto key=recipe->at("key");native_name(key);
 need(key[0]=="def"&&key[2].is_number_integer()&&key[2].get<int>()>=0&&key[2].get<int>()<=21&&natives.contains(key.dump()),"FULLRAW_UNBOUND_NATIVE_SLOT");
 const auto& native=natives.at(key.dump());auto root=native_review_output_path(path(batch.at("output_root")));
 need(native_review_within(root,native_review_product_root()/L"build"),"FULLRAW_OUTPUT_MUST_STAY_IN_PRODUCT_BUILD");
 auto expected=fullraw_entry(*recipe,spec,native,root);
 need(entry.dump()==expected.dump(),"FULLRAW_FROZEN_RECIPE_CHANGED");
 d.canvas=pair(native.at("targets").at("canvas_1x"),1,"fullraw canvas");
 d.crop=pair(native.at("targets").at("crop_1x"),1,"fullraw crop");
 d.origin=pair(native.at("targets").at("origin_1x"),0,"fullraw origin");
 d.fullCanvas=spec.at("schema")=="h3.master-expand.fullraw-spec.v2";
 d.scale=spec.at("target_scale").get<int>();
 if(!d.fullCanvas){
  d.k=integer(recipe->at("natural_k"),1,64,"fullraw natural integer K");
  need(d.scale<=d.k,"FULLRAW_NO_SOURCE_DOMAIN_UPSCALE");
  auto translation=recipe->at("translation");need(translation.is_array()&&translation.size()==2,"FULLRAW_INTEGER_TRANSLATION_REQUIRED");
  d.tx=integer(translation[0],-MAX_AXIS,MAX_AXIS,"fullraw translation x");
  d.ty=integer(translation[1],-MAX_AXIS,MAX_AXIS,"fullraw translation y");
  d.w=d.canvas[0]*d.k;d.h=d.canvas[1]*d.k;
  need(d.w>0&&d.h>0&&d.w<=MAX_AXIS&&d.h<=MAX_AXIS&&std::uint64_t(d.w)*d.h<=MAX_PIXELS,"FULLRAW_DOMAIN_PIXEL_LIMIT");
 }
 const auto& lineage=recipe->at("lineage");need(lineage.is_array()&&!lineage.empty()&&lineage.size()<=16,"FULLRAW_LINEAGE_REQUIRED");
 for(const auto& ref:lineage)fullraw_ref(ref,d);
 const auto& source=recipe->at("source");
 auto raw=fullraw_ref(Json{{"path",source.at("path")},{"sha256",source.at("sha256")}},d);
 need(raw.size()>=33&&!std::memcmp(raw.data(),"\x89PNG\r\n\x1a\n",8)&&raw[24]==8&&raw[25]==6,"FULLRAW_REQUIRES_8BIT_RGBA_PNG_NO_IMPLICIT_QUANTIZATION");
 Wic decoder;auto frame=decoder.frame(raw);d.rawW=(int)frame.w;d.rawH=(int)frame.h;
 if(d.fullCanvas){
  need(std::int64_t(d.rawW)*d.canvas[1]==std::int64_t(d.rawH)*d.canvas[0],"FULLRAW_FULL_CANVAS_ASPECT_MISMATCH");
  need(std::int64_t(d.rawW)>=std::int64_t(d.canvas[0])*d.scale&&
       std::int64_t(d.rawH)>=std::int64_t(d.canvas[1])*d.scale,"FULLRAW_NO_SOURCE_DOMAIN_UPSCALE");
  d.w=d.rawW;d.h=d.rawH;
  need(d.w>0&&d.h>0&&d.w<=MAX_AXIS&&d.h<=MAX_AXIS&&std::uint64_t(d.w)*d.h<=MAX_PIXELS,"FULLRAW_DOMAIN_PIXEL_LIMIT");
 }
 need(source.at("pixels")==Json::array({d.rawW,d.rawH}),"FULLRAW_RAW_DIMENSION_DRIFT");
 auto rgba=decoder.decode_rgba(raw,d.rawW,d.rawH);
 need(valid_sha(source.at("rgba_sha256"))&&sha(rgba)==source.at("rgba_sha256").get<std::string>(),"FULLRAW_RAW_RGBA_SHA_MISMATCH");
 std::vector<std::uint8_t> positiveBytes;std::uint64_t positive=0,alpha1=0,outcrop=0;
 for(int y=0;y<d.rawH;y++)for(int x=0;x<d.rawW;x++){
  auto at=(std::size_t(y)*d.rawW+x)*4;auto a=rgba[at+3];if(!a)continue;
  int dx=x+d.tx,dy=y+d.ty;
  need(dx>=0&&dy>=0&&dx<d.w&&dy<d.h,"FULLRAW_POSITIVE_OUTSIDE_DOMAIN: "+std::to_string(x)+","+std::to_string(y));
  positiveBytes.insert(positiveBytes.end(),rgba.data()+at,rgba.data()+at+4);++positive;alpha1+=a==1;
  if(d.fullCanvas)outcrop+=(std::int64_t(x)*d.canvas[0]<std::int64_t(d.origin[0])*d.rawW||
      std::int64_t(y)*d.canvas[1]<std::int64_t(d.origin[1])*d.rawH||
      std::int64_t(x+1)*d.canvas[0]>std::int64_t(d.origin[0]+d.crop[0])*d.rawW||
      std::int64_t(y+1)*d.canvas[1]>std::int64_t(d.origin[1]+d.crop[1])*d.rawH);
  else outcrop+=(dx<d.origin[0]*d.k||dy<d.origin[1]*d.k||dx>=(d.origin[0]+d.crop[0])*d.k||dy>=(d.origin[1]+d.crop[1])*d.k);
 }
 need(positive>0,"FULLRAW_EMPTY_SOURCE");
 d.cropOutput=path(entry.at("full_raw_sampling_domain").at("crop_output"));
 for(const auto& [name,ref]:d.refs){
  (void)name;need(!native_review_overlap(ref.first,root),"FULLRAW_INPUT_OUTPUT_OVERLAP");
 }
 d.proof={{"positive_rgba_pixels",positive},{"alpha1_pixels",alpha1},{"positive_outside_native_crop_before_sampling",outcrop},
  {"raw_rgba_sha256",source.at("rgba_sha256")},{"ordered_positive_rgba_sha256",sha(positiveBytes)},
  {"full_domain_pixels",{d.w,d.h}},{"all_positive_contained",true},{"source_pixels_dropped",0},
  {"source_alpha_changed",false},{"k_or_phase_search",false}};
 if(d.fullCanvas){
  d.proof["source_layout"]="full-native-canvas";
  d.proof["source_pixels_per_native_pixel"]={d.rawW,d.canvas[0]};
  d.proof["registration"]="explicit-whole-source-to-whole-native-canvas";
 }
 const auto build=native_review_build_binding(native_review_product_root(),native_review_executable_path(),
       {"src/full_raw_domain.hpp","src/master_expand.hpp","src/motion.hpp"});
 d.binding={{"spec",batch.at("fullraw_spec")},{"native_contracts",spec.at("native_contracts")},{"recipe",*recipe},
  {"native_entry",native},{"executable_sha256",build.at("executable").at("sha256")},
  {"build_receipt_sha256",build.at("build_receipt").at("sha256")},
  {"placement_authority","caller-frozen recipe; not an automatic registration or art approval"}};
 return d;
}
inline Image fullraw_compose(const FullRawDomain& d,const Image& raw){
 need(raw.w==d.rawW&&raw.h==d.rawH,"FULLRAW_DECODE_DIMENSIONS_CHANGED");
 if(d.fullCanvas)return raw;
 Image full(d.w,d.h);std::uint64_t copied=0;
 for(int y=0;y<raw.h;y++)for(int x=0;x<raw.w;x++){
  const auto& p=raw.p[std::size_t(y)*raw.w+x];if(p.a<=0)continue;
  int dx=x+d.tx,dy=y+d.ty;need(dx>=0&&dy>=0&&dx<d.w&&dy<d.h,"FULLRAW_DECODE_POSITIVE_OUTSIDE_DOMAIN");
  full.p[std::size_t(dy)*d.w+dx]=p;++copied;
 }
 need(copied==d.proof.at("positive_rgba_pixels").get<std::uint64_t>(),"FULLRAW_POSITIVE_COPY_COUNT_CHANGED");return full;
}
inline Json fullraw_audit(const FullRawDomain& d,const std::vector<std::uint8_t>& rgba){
 int w=d.canvas[0]*d.scale,h=d.canvas[1]*d.scale;
 need(rgba.size()==std::size_t(w)*h*4,"FULLRAW_OUTPUT_DIMENSION_MISMATCH");
 std::uint64_t outside=0,alpha1=0,total=0;std::array<std::uint64_t,256> histogram{};Json coordinates=Json::array();
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){
  auto a=rgba[(std::size_t(y)*w+x)*4+3];if(!a)continue;++total;
  if(x<d.origin[0]*d.scale||y<d.origin[1]*d.scale||x>=(d.origin[0]+d.crop[0])*d.scale||y>=(d.origin[1]+d.crop[1])*d.scale){
   ++outside;++histogram[a];alpha1+=a==1;if(coordinates.size()<256)coordinates.push_back({x,y,a});
  }
 }
 return {{"outside_alpha_positive_pixels",outside},{"outside_alpha1_pixels",alpha1},{"outside_alpha_histogram",histogram},
  {"outside_first_xy_alpha",coordinates},{"coordinates_truncated",outside>coordinates.size()},
  {"output_positive_pixels",total},{"zero_outcrop_required",true}};
}
inline std::vector<std::uint8_t> fullraw_crop(const FullRawDomain& d,const std::vector<std::uint8_t>& rgba){
 need(d.proof.at("positive_outside_native_crop_before_sampling")==0,"FULLRAW_SOURCE_POSITIVE_OUTSIDE_NATIVE_CROP");
 need(fullraw_audit(d,rgba).at("outside_alpha_positive_pixels")==0,"FULLRAW_OUTPUT_POSITIVE_OUTSIDE_CROP");
 int w=d.crop[0]*d.scale,h=d.crop[1]*d.scale,fullW=d.canvas[0]*d.scale;
 std::vector<std::uint8_t> out(std::size_t(w)*h*4);
 for(int y=0;y<h;y++)std::copy_n(rgba.data()+(std::size_t(y+d.origin[1]*d.scale)*fullW+d.origin[0]*d.scale)*4,
                                  std::size_t(w)*4,out.data()+std::size_t(y)*w*4);
 return out;
}
inline Json fullraw_detail(const FullRawDomain& d,const std::vector<std::uint8_t>& rgba){
 auto diagnostic=fullraw_audit(d,rgba);
 bool sourceOutside=d.proof.at("positive_outside_native_crop_before_sampling")!=0;
 bool good=!sourceOutside&&diagnostic.at("outside_alpha_positive_pixels")==0;
 Json detail={{"source_proof",d.proof},{"diagnostics",diagnostic},{"materialized_source_file_written",false},
  {"sampling_stages",1},{"crop_status",good?"ZERO_OUTSIDE_CROP":"REJECTED_OUTCROP_ALPHA"},{"crop",nullptr},
  {"native_timing_verified",false},{"art_approved",false},{"game_deployed",false}};
 detail["crop_failure"]=good?Json(nullptr):Json(sourceOutside?"FULLRAW_SOURCE_POSITIVE_OUTSIDE_NATIVE_CROP":"FULLRAW_OUTPUT_POSITIVE_OUTSIDE_CROP");
 if(d.fullCanvas)detail["source_layout"]="full-native-canvas";
 return detail;
}
inline void fullraw_recheck(const Json& entry,const Json& batch,const FullRawDomain& expected){
 auto current=fullraw_read(entry,batch);
 need(current.binding.dump()==expected.binding.dump()&&current.proof.dump()==expected.proof.dump(),"FULLRAW_DEPENDENCY_CHANGED_DURING_EXECUTION");
}
} // namespace h3tier
