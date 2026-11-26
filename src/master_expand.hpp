#pragma once
#include "jobs.hpp"
#include "full_raw_domain.hpp"
#include "official_silhouette.hpp"
#include <numeric>

namespace h3tier {
// A master may be standardised to any integer multiple of the official 1X slot, not only 3X/4X:
// the press that finally ships is a 3X expand, and preparing the master at the largest multiple
// its own pixels can carry keeps that final step an exact integer downscale.
inline constexpr int MAX_STANDARD_SIZE_MULTIPLE=36;
inline constexpr const char* EXPAND_ALGORITHM="linear-premultiplied-lanczos3-local-range-v1";  // legacy sampler: verify/replay only, never selectable
inline constexpr const char* ANIMATION_CONTRACT_V1="static-structure-dynamic-fx";
inline constexpr const char* ANIMATION_CONTRACT_V2="static-structure-dynamic-fx-v2";
inline constexpr const char* ANIMATION_CONTRACT_BASE_OVERLAY_V1="static-base-dynamic-overlay-v1";
inline constexpr const char* ANIMATION_CONTRACT_BASE_OVERLAY_V2="static-base-dynamic-overlay-v2";
inline constexpr const char* ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1="static-base-authored-fx-roi-v1";
inline constexpr const char* ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2="static-base-authored-fx-roi-v2";
inline constexpr const char* ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1="static-composite-authored-fx-roi-v1";
inline constexpr const char* ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2="static-composite-authored-fx-roi-v2";
inline constexpr double ANIMATION_AUTHORED_FX_MAX_ROI_FRACTION=0.75;
inline constexpr double ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION=0.10;
inline constexpr const char* FRAME_SET_SINGLE_V1="single-or-unrelated-v1";
inline constexpr const char* FRAME_SET_STATIC_CATALOG_V1="independent-static-frame-catalog-v1";
inline constexpr const char* VISIBLE_SCALE_GATE_SCHEMA="h3.master-expand.visible-scale-floor.v1";
inline constexpr double VISIBLE_SCALE_FLOOR_RATIO=0.80;
inline constexpr const char* STATIC_FRAME_SCALE_GATE_SCHEMA="h3.master-expand.visible-scale-frame-catalog.v4";
inline constexpr double STATIC_FRAME_SCALE_MIN_RATIO=0.95;
inline constexpr double STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO=0.95;
inline constexpr double ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_FRAME_SCALE_SPREAD_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_STATIC_BASE_MIN_FRACTION=0.20;
inline constexpr double ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS=5.0;
inline constexpr double ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT=5.0;
inline constexpr double ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT=12.0;
inline constexpr double ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS=5.0;
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
  bool silhouette=false,staticFrameCatalog=false;fs::path reference;std::string referenceSha;int referenceW=0,referenceH=0,referenceScale=3;
 Json animationGroupTransform=Json{};
 ArtworkBox slotBox,slotBox1x,masterBox;std::string fitAnchor="bottom_right";
};
inline ArtworkBox animation_rgba_artwork_box(const std::vector<std::uint8_t>& rgba,int width,int height);
inline Image animation_group_fit_cpu(const Image& source,int outW,int outH,const Json& transform){
 need(transform.is_object()&&transform.value("schema",std::string())=="h3.animation.group-uniform-fit.v1",
      "ANIMATION_GROUP_TRANSFORM_INVALID");
 const auto& sourceBox=transform.at("source_union_box");const auto& targetBox=transform.at("target_union_box_3x");
 need(sourceBox.is_array()&&sourceBox.size()==4&&targetBox.is_array()&&targetBox.size()==4,"ANIMATION_GROUP_TRANSFORM_BOX_INVALID");
 int sx0=sourceBox[0].get<int>(),sy0=sourceBox[1].get<int>(),sx1=sourceBox[2].get<int>(),sy1=sourceBox[3].get<int>();
 int tx0=targetBox[0].get<int>(),ty0=targetBox[1].get<int>(),tx1=targetBox[2].get<int>(),ty1=targetBox[3].get<int>();
 need(sx0>=0&&sy0>=0&&sx1>sx0&&sy1>sy0&&sx1<=source.w&&sy1<=source.h,"ANIMATION_GROUP_SOURCE_BOX_INVALID");
 need(tx0>=0&&ty0>=0&&tx1>tx0&&ty1>ty0&&tx1<=outW&&ty1<=outH,"ANIMATION_GROUP_TARGET_BOX_INVALID");
 int sw=sx1-sx0,sh=sy1-sy0,tw=tx1-tx0,th=ty1-ty0;
 double scale=transform.at("scale").get<double>();
 need(std::isfinite(scale)&&scale>0,"ANIMATION_GROUP_SCALE_INVALID");
 int rw=std::max(1,int(std::lround(sw*scale))),rh=std::max(1,int(std::lround(sh*scale)));
 need(rw==tw&&rh==th,"ANIMATION_GROUP_TRANSFORM_DIMENSION_DRIFT");
 Image crop(sw,sh);for(int y=0;y<sh;y++)std::copy_n(source.p.data()+std::size_t(sy0+y)*source.w+sx0,sw,crop.p.data()+std::size_t(y)*sw);
 auto fitted=quality_resize_cpu(crop,rw,rh);Image out(outW,outH);
 for(int y=0;y<rh;y++)std::copy_n(fitted.p.data()+std::size_t(y)*rw,rw,out.p.data()+std::size_t(ty0+y)*outW+tx0);
 return out;
}
inline std::string expand_frame_group(const fs::path& source){
 auto stem=source.stem().wstring();auto split=stem.rfind(L'_');
 if(split==std::wstring::npos||split==0||split+1>=stem.size())return {};
 auto digits=[](const std::wstring& value){return std::all_of(value.begin(),value.end(),[](wchar_t c){return c>=L'0'&&c<=L'9';});};
 if(!digits(stem.substr(0,split))||!digits(stem.substr(split+1)))return {};
 return str(source.parent_path())+"|"+utf8(stem.substr(0,split));
}
inline std::optional<std::pair<std::wstring,std::wstring>> animation_group_frame(const fs::path& frame){
 auto stem=frame.stem().wstring();auto split=stem.rfind(L'_');
 if(split==std::wstring::npos||split==0||split+1>=stem.size())return std::nullopt;
 auto digits=[](const std::wstring& value){return std::all_of(value.begin(),value.end(),[](wchar_t c){return c>=L'0'&&c<=L'9';});};
 auto group=stem.substr(0,split),number=stem.substr(split+1);
 if(!digits(number))return std::nullopt;
 return std::make_pair(std::move(group),std::move(number));
}
inline std::wstring animation_folded_text(std::wstring value){for(auto& c:value)c=(wchar_t)towlower(c);return value;}
inline std::vector<fs::path> animation_reference_frames(const fs::path& directory,const std::wstring& group){
 std::vector<fs::path> frames;auto prefix=animation_folded_text(group+L"_");
 for(const auto& entry:fs::directory_iterator(directory)){
  if(!entry.is_regular_file()||animation_folded_text(entry.path().extension().wstring())!=L".png")continue;
  auto stem=entry.path().stem().wstring();auto lowered=animation_folded_text(stem);
  if(lowered.size()<=prefix.size()||lowered.compare(0,prefix.size(),prefix)!=0)continue;
  auto suffix=stem.substr(group.size()+1);
  if(suffix.empty()||!std::all_of(suffix.begin(),suffix.end(),[](wchar_t c){return c>=L'0'&&c<=L'9';}))continue;
  frames.push_back(clean_path(entry.path()));
 }
 std::sort(frames.begin(),frames.end(),[](const fs::path& a,const fs::path& b){return animation_folded_text(a.filename().wstring())<animation_folded_text(b.filename().wstring());});
 return frames;
}
inline Json build_animation_reference_inventory(const std::vector<fs::path>& sources,const fs::path& referenceRoot,bool allowSingletonFrameGroups=false){
 struct Group {std::wstring identity,group;std::set<std::wstring> sourceNames;};
 std::map<std::wstring,Group> groups;
 for(const auto& source:sources){
  auto parsed=animation_group_frame(source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: expected <group>_<frame>.png: "+str(source));
  auto identity=source.parent_path().filename().wstring();auto key=animation_folded_text(identity+L"|"+parsed->first);
  auto& item=groups[key];item.identity=identity;item.group=parsed->first;
  need(item.sourceNames.insert(animation_folded_text(source.filename().wstring())).second,"ANIMATION_DUPLICATE_SOURCE_FRAME: "+str(source));
 }
 Json inventory=Json::array();
 for(const auto& [key,item]:groups){
  (void)key;auto directory=clean_path(referenceRoot/item.identity);
  need(fs::exists(directory)&&fs::is_directory(directory),"ANIMATION_OFFICIAL_REFERENCE_DIRECTORY_MISSING: "+str(directory));
  auto refs=animation_reference_frames(directory,item.group);std::set<std::wstring> referenceNames;
  if(allowSingletonFrameGroups)need(!refs.empty(),"STATIC_FRAME_CATALOG_OFFICIAL_REFERENCE_GROUP_EMPTY: "+utf8(item.identity)+"/"+utf8(item.group));
  else need(refs.size()>=2,"ANIMATION_OFFICIAL_REFERENCE_GROUP_INCOMPLETE: "+utf8(item.identity)+"/"+utf8(item.group));
  Json frames=Json::array();Wic decoder;
  for(const auto& ref:refs){
  auto name=animation_folded_text(ref.filename().wstring());referenceNames.insert(name);auto raw=read_all(ref);auto meta=decoder.frame(raw);
   frames.push_back({{"name",str(ref.filename())},{"path",str(ref)},{"sha256",sha(raw)},{"pixels",{meta.w,meta.h}}});
  }
  need(referenceNames==item.sourceNames,"ANIMATION_OFFICIAL_FRAME_SET_MISMATCH: "+utf8(item.identity)+"/"+utf8(item.group)
       +" source="+std::to_string(item.sourceNames.size())+" official="+std::to_string(referenceNames.size()));
  inventory.push_back({{"identity",utf8(item.identity)},{"group",utf8(item.group)},
                       {"reference_directory",str(directory)},{"frames",std::move(frames)}});
 }
 return inventory;
}
inline std::vector<std::string> unclassified_multiframe_groups(const std::vector<fs::path>& sources,const fs::path& referenceRoot){
 struct Group {std::wstring identity,group;std::vector<fs::path> sources;};
 std::map<std::wstring,Group> groups;
 for(const auto& source:sources){
  auto parsed=animation_group_frame(source);if(!parsed)continue;
  auto identity=source.parent_path().filename().wstring();auto key=animation_folded_text(identity+L"|"+parsed->first);
  auto& row=groups[key];row.identity=identity;row.group=parsed->first;row.sources.push_back(source);
 }
 std::vector<std::string> result;
 for(const auto& [key,row]:groups){
  (void)key;bool multi=row.sources.size()>=2;
  if(!multi&&!referenceRoot.empty()){
   auto directory=clean_path(referenceRoot/row.identity);
   if(fs::exists(directory)&&fs::is_directory(directory))multi=animation_reference_frames(directory,row.group).size()>=2;
  }
  if(multi)result.push_back(utf8(row.identity)+"/"+utf8(row.group));
 }
 return result;
}
inline void seal_animation_group_transforms(Json& items){
 need(items.is_array()&&!items.empty(),"ANIMATION_GROUP_TRANSFORM_ITEMS_MISSING");
 struct Frame {std::size_t index=0;std::wstring identity,group;fs::path source,reference;ArtworkBox sourceBox,referenceBox;int sourceW=0,sourceH=0,referenceW=0,referenceH=0,targetScale=3;std::string anchor;};
 std::map<std::wstring,std::vector<Frame>> groups;Wic decoder;
 for(std::size_t i=0;i<items.size();i++){
  auto& entry=items[i];auto source=clean_path(path(entry.at("source").get<std::string>()));
  auto parsed=animation_group_frame(source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(source));
  need(entry.contains("reference")&&entry.contains("reference_pixels"),"ANIMATION_GROUP_OFFICIAL_REFERENCE_REQUIRED: "+str(source));
  auto identity=source.parent_path().filename().wstring();auto key=animation_folded_text(identity+L"|"+parsed->first);
  auto raw=read_all(source);std::string note;auto im=decoder.decode(raw,true,note);auto sourceBox=artwork_box(im);
  auto reference=clean_path(path(entry.at("reference").get<std::string>()));auto referenceRaw=read_all(reference);auto official=decoder.decode(referenceRaw,true,note);auto referenceBox=artwork_box(official,true);
  need(!sourceBox.empty&&!referenceBox.empty,"ANIMATION_GROUP_EMPTY_SOURCE_OR_REFERENCE: "+str(source));
  need(entry.at("source_pixels")==Json::array({im.w,im.h}),"ANIMATION_GROUP_SOURCE_DIMENSION_DRIFT: "+str(source));
  need(entry.at("reference_pixels")==Json::array({official.w,official.h}),"ANIMATION_GROUP_REFERENCE_DIMENSION_DRIFT: "+str(reference));
  need(official.w==integer(entry.at("base_pixels")[0],1,MAX_AXIS,"base width")&&official.h==integer(entry.at("base_pixels")[1],1,MAX_AXIS,"base height"),
       "ANIMATION_GROUP_REFERENCE_NOT_NATIVE_1X: "+str(reference));
  int targetScale=integer(entry.at("target_scale"),3,MAX_STANDARD_SIZE_MULTIPLE,"target master scale");
  auto anchor=entry.value("fit_anchor",std::string("bottom_right"));
  need(anchor=="bottom_right"||anchor=="center","ANIMATION_GROUP_FIT_ANCHOR_INVALID");
  auto& group=groups[key];if(!group.empty())
   need(group.front().sourceW==im.w&&group.front().sourceH==im.h&&group.front().referenceW==official.w&&group.front().referenceH==official.h
        &&group.front().targetScale==targetScale&&group.front().anchor==anchor,"ANIMATION_GROUP_REGISTRATION_FIELDS_DRIFT: "+utf8(identity)+"/"+utf8(parsed->first));
  group.push_back({i,identity,parsed->first,source,reference,sourceBox,referenceBox,im.w,im.h,official.w,official.h,targetScale,anchor});
 }
 for(auto& [key,frames]:groups){
  need(frames.size()>=2,"ANIMATION_SCALE_GROUP_NEEDS_AT_LEAST_2_FRAMES: "+utf8(key));
  int sx0=frames.front().sourceBox.x0,sy0=frames.front().sourceBox.y0,sx1=frames.front().sourceBox.x1,sy1=frames.front().sourceBox.y1;
  int rx0=frames.front().referenceBox.x0,ry0=frames.front().referenceBox.y0,rx1=frames.front().referenceBox.x1,ry1=frames.front().referenceBox.y1;
  for(const auto& frame:frames){sx0=std::min(sx0,frame.sourceBox.x0);sy0=std::min(sy0,frame.sourceBox.y0);sx1=std::max(sx1,frame.sourceBox.x1);sy1=std::max(sy1,frame.sourceBox.y1);
   rx0=std::min(rx0,frame.referenceBox.x0);ry0=std::min(ry0,frame.referenceBox.y0);rx1=std::max(rx1,frame.referenceBox.x1);ry1=std::max(ry1,frame.referenceBox.y1);}
  int sourceW=sx1-sx0,sourceH=sy1-sy0,targetScale=frames.front().targetScale;
  int targetW=frames.front().referenceW*targetScale,targetH=frames.front().referenceH*targetScale;
  int refX0=rx0*targetScale,refY0=ry0*targetScale,refX1=rx1*targetScale,refY1=ry1*targetScale;
  int refW=refX1-refX0,refH=refY1-refY0;need(sourceW>0&&sourceH>0&&refW>0&&refH>0,"ANIMATION_GROUP_EMPTY_UNION_BOX");
  double scale=std::min(double(refW)/sourceW,double(refH)/sourceH);
  int fitW=std::max(1,int(std::lround(sourceW*scale))),fitH=std::max(1,int(std::lround(sourceH*scale)));
  int tx=0,ty=0;if(frames.front().anchor=="bottom_right"){tx=refX1-fitW;ty=refY1-fitH;}
  else{tx=refX0+(refW-fitW)/2;ty=refY0+(refH-fitH)/2;}
  need(tx>=refX0&&ty>=refY0&&tx+fitW<=refX1&&ty+fitH<=refY1&&tx>=0&&ty>=0&&tx+fitW<=targetW&&ty+fitH<=targetH,
       "ANIMATION_GROUP_UNIFORM_FIT_OUTSIDE_OFFICIAL_UNION: "+utf8(key));
  Json transform={{"schema","h3.animation.group-uniform-fit.v1"},{"group_key",utf8(key)},
   {"source_canvas",{frames.front().sourceW,frames.front().sourceH}},{"target_canvas_3x",{targetW,targetH}},
   {"source_union_box",{sx0,sy0,sx1,sy1}},{"official_union_box_1x",{rx0,ry0,rx1,ry1}},
   {"reference_union_box_3x",{refX0,refY0,refX1,refY1}},{"target_union_box_3x",{tx,ty,tx+fitW,ty+fitH}},
   {"scale",scale},{"offset_pixels",{tx,ty}},
   {"anchor",frames.front().anchor},{"registration","one_shared_transform_for_all_frames"}};
  for(const auto& frame:frames)items[frame.index]["animation_group_transform"]=transform;
 }
}

inline void seal_animation_group_transforms_authored_composite(Json& items,const fs::path& roiPath,const std::string& expectedRoiSha){
 need(items.is_array()&&!items.empty()&&!roiPath.empty(),"ANIMATION_AUTHORED_COMPOSITE_TRANSFORM_INPUTS_MISSING");
 need(fs::exists(roiPath)&&fs::is_regular_file(roiPath),"ANIMATION_AUTHORED_ROI_NOT_FOUND: "+str(roiPath));
 auto roiRaw=read_all(roiPath);need(sha(roiRaw)==expectedRoiSha,"ANIMATION_AUTHORED_ROI_DRIFT: "+str(roiPath));
 Wic decoder;auto roiMeta=decoder.frame(roiRaw);const int roiW=roiMeta.w,roiH=roiMeta.h;
 auto roiRgba=decoder.decode_rgba(roiRaw,roiW,roiH);std::vector<std::uint8_t> sourceRoi(std::size_t(roiW)*roiH,0);
 std::uint64_t roiPixels=0;
 for(std::size_t p=0;p<sourceRoi.size();p++){
  auto at=p*4;auto luma=(54u*roiRgba[at]+183u*roiRgba[at+1]+19u*roiRgba[at+2])/256u;
  if(roiRgba[at+3]&&luma>=1){sourceRoi[p]=1;roiPixels++;}
 }
 need(roiPixels>0,"ANIMATION_AUTHORED_ROI_EMPTY");
 struct Frame {std::size_t index=0;std::wstring frameNumber;fs::path source,reference;std::string sourceSha,referenceSha;int sourceW=0,sourceH=0,referenceW=0,referenceH=0,targetScale=3;std::string anchor;ArtworkBox sourceContent,officialContent;};
 std::map<std::wstring,std::vector<Frame>> groups;
 for(std::size_t i=0;i<items.size();i++){
  const auto& entry=items[i];auto source=clean_path(path(entry.at("source").get<std::string>()));
  auto reference=clean_path(path(entry.at("reference").get<std::string>()));
  auto parsed=animation_group_frame(source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(source));
  need(entry.contains("reference_sha256")&&entry.contains("reference_pixels"),"ANIMATION_GROUP_OFFICIAL_REFERENCE_REQUIRED: "+str(source));
  auto sourceSha=entry.at("source_sha256").get<std::string>(),referenceSha=entry.at("reference_sha256").get<std::string>();
  auto sourceRaw=read_all(source),referenceRaw=read_all(reference);
  need(sha(sourceRaw)==sourceSha,"ANIMATION_GROUP_SOURCE_DRIFT: "+str(source));
  need(sha(referenceRaw)==referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(reference));
  auto sourceMeta=decoder.frame(sourceRaw),referenceMeta=decoder.frame(referenceRaw);
  int sourceW=sourceMeta.w,sourceH=sourceMeta.h,referenceW=referenceMeta.w,referenceH=referenceMeta.h;
  need(entry.at("source_pixels")==Json::array({sourceW,sourceH}),"ANIMATION_GROUP_SOURCE_DIMENSION_DRIFT: "+str(source));
  need(entry.at("reference_pixels")==Json::array({referenceW,referenceH}),"ANIMATION_GROUP_REFERENCE_DIMENSION_DRIFT: "+str(reference));
  need(roiW==sourceW&&roiH==sourceH,"ANIMATION_AUTHORED_ROI_CANVAS_MISMATCH: "+str(source));
  double aspectError=std::fabs((double(sourceW)/sourceH)/(double(referenceW)/referenceH)-1.0)*100.0;
  need(aspectError<=0.5,"ANIMATION_AUTHORED_COMPOSITE_CANVAS_ASPECT_MISMATCH: "+str(source));
  auto sourceRgba=decoder.decode_rgba(sourceRaw,sourceW,sourceH);
  auto officialRgba=decoder.decode_rgba(referenceRaw,referenceW,referenceH);
  auto sourceBox=animation_rgba_artwork_box(sourceRgba,sourceW,sourceH);
  auto officialBox=animation_rgba_artwork_box(officialRgba,referenceW,referenceH);
  need(!sourceBox.empty&&!officialBox.empty,"ANIMATION_GROUP_EMPTY_SOURCE_OR_REFERENCE: "+str(source));
  need(referenceW==integer(entry.at("base_pixels")[0],1,MAX_AXIS,"base width")
       &&referenceH==integer(entry.at("base_pixels")[1],1,MAX_AXIS,"base height"),
       "ANIMATION_GROUP_REFERENCE_NOT_NATIVE_1X: "+str(reference));
  auto targetScale=integer(entry.at("target_scale"),3,MAX_STANDARD_SIZE_MULTIPLE,"target master scale");
  need(targetScale==3,"ANIMATION_AUTHORED_COMPOSITE_TARGET_SCALE_MUST_BE_3X");
  auto fitAnchor=entry.value("fit_anchor",std::string("bottom_right"));
  need(fitAnchor=="bottom_right"||fitAnchor=="center","ANIMATION_GROUP_FIT_ANCHOR_INVALID");
  auto identity=source.parent_path().filename().wstring(),key=animation_folded_text(identity+L"|"+parsed->first);
  auto& group=groups[key];
  if(!group.empty())need(group.front().sourceW==sourceW&&group.front().sourceH==sourceH
       &&group.front().referenceW==referenceW&&group.front().referenceH==referenceH
       &&group.front().targetScale==targetScale&&group.front().anchor==fitAnchor,
       "ANIMATION_GROUP_REGISTRATION_FIELDS_DRIFT: "+utf8(identity)+"/"+utf8(parsed->first));
  group.push_back({i,parsed->second,source,reference,sourceSha,referenceSha,sourceW,sourceH,referenceW,referenceH,targetScale,fitAnchor,sourceBox,officialBox});
 }
 need(groups.size()==1,"ANIMATION_AUTHORED_COMPOSITE_REQUIRES_ONE_GROUP_PER_PLAN");
 auto mergeBox=[](int& x0,int& y0,int& x1,int& y1,bool& empty,const ArtworkBox& box){
  if(box.empty)return;
  if(empty){x0=box.x0;y0=box.y0;x1=box.x1;y1=box.y1;empty=false;return;}
  x0=std::min(x0,box.x0);y0=std::min(y0,box.y0);x1=std::max(x1,box.x1);y1=std::max(y1,box.y1);
 };
 auto clearMask=[](std::vector<std::uint8_t>& rgba,const std::vector<std::uint8_t>& mask){
  need(rgba.size()==mask.size()*4,"ANIMATION_AUTHORED_COMPOSITE_MASK_SIZE_MISMATCH");
  for(std::size_t p=0;p<mask.size();p++)if(mask[p])rgba[p*4+3]=0;
 };
 for(auto& [wideKey,frames]:groups){
  std::sort(frames.begin(),frames.end(),[](const Frame& a,const Frame& b){
   return std::stoi(std::string(a.frameNumber.begin(),a.frameNumber.end()))<std::stoi(std::string(b.frameNumber.begin(),b.frameNumber.end()));
  });
  need(frames.size()>=2&&frames.front().frameNumber==L"0","ANIMATION_BASE_FRAME0_REQUIRED: "+utf8(wideKey));
  const auto& first=frames.front();const int sourceW=first.sourceW,sourceH=first.sourceH;
  const int officialW=first.referenceW,officialH=first.referenceH,targetScale=first.targetScale;
  const std::size_t sourcePixels=std::size_t(sourceW)*sourceH,officialPixels=std::size_t(officialW)*officialH;
  auto firstSourceRaw=read_all(first.source),firstOfficialRaw=read_all(first.reference);
  need(sha(firstSourceRaw)==first.sourceSha&&sha(firstOfficialRaw)==first.referenceSha,"ANIMATION_AUTHORED_COMPOSITE_BASE_DRIFT");
  auto sourceBase=decoder.decode_rgba(firstSourceRaw,sourceW,sourceH);
  auto officialBase=decoder.decode_rgba(firstOfficialRaw,officialW,officialH);
  std::vector<std::uint8_t> officialEffectRoi(officialPixels,0);
  auto differs=[](const std::vector<std::uint8_t>& a,const std::vector<std::uint8_t>& b,std::size_t at){
   return a[at]!=b[at]||a[at+1]!=b[at+1]||a[at+2]!=b[at+2]||a[at+3]!=b[at+3];
  };
  for(std::size_t fi=1;fi<frames.size();fi++){
   const auto& frame=frames[fi];auto raw=read_all(frame.reference);
   need(sha(raw)==frame.referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(frame.reference));
   auto image=decoder.decode_rgba(raw,officialW,officialH);
   for(std::size_t p=0;p<officialPixels;p++)if(differs(image,officialBase,p*4))officialEffectRoi[p]=1;
  }
  auto sourceToOfficial=[&](int x,int y){
   int nx=std::clamp(int((static_cast<long long>(x)*officialW)/sourceW),0,officialW-1);
   int ny=std::clamp(int((static_cast<long long>(y)*officialH)/sourceH),0,officialH-1);
   return std::size_t(ny)*officialW+nx;
  };
  auto officialToSource=[&](int x,int y){
   int sx=std::clamp(int((static_cast<long long>(x)*sourceW)/officialW),0,sourceW-1);
   int sy=std::clamp(int((static_cast<long long>(y)*sourceH)/officialH),0,sourceH-1);
   return std::size_t(sy)*sourceW+sx;
  };
  std::vector<std::uint8_t> sourceStructureMask=sourceRoi,officialStructureMask=officialEffectRoi;
  for(std::size_t p=0;p<sourcePixels;p++){
   int x=int(p%sourceW),y=int(p/sourceW);
   if(officialEffectRoi[sourceToOfficial(x,y)])sourceStructureMask[p]=1;
  }
  for(std::size_t p=0;p<officialPixels;p++){
   int x=int(p%officialW),y=int(p/officialW);
   if(sourceRoi[officialToSource(x,y)])officialStructureMask[p]=1;
  }
  clearMask(sourceBase,sourceStructureMask);clearMask(officialBase,officialStructureMask);
  auto sourceStructure=animation_rgba_artwork_box(sourceBase,sourceW,sourceH);
  auto officialStructure=animation_rgba_artwork_box(officialBase,officialW,officialH);
  need(!sourceStructure.empty&&!officialStructure.empty,"ANIMATION_AUTHORED_COMPOSITE_STRUCTURE_EMPTY");
  int sourceUnionX0=sourceW,sourceUnionY0=sourceH,sourceUnionX1=-1,sourceUnionY1=-1;
  int officialUnionX0=officialW,officialUnionY0=officialH,officialUnionX1=-1,officialUnionY1=-1;
  bool sourceUnionEmpty=true,officialUnionEmpty=true;
  for(const auto& frame:frames){
   mergeBox(sourceUnionX0,sourceUnionY0,sourceUnionX1,sourceUnionY1,sourceUnionEmpty,frame.sourceContent);
   mergeBox(officialUnionX0,officialUnionY0,officialUnionX1,officialUnionY1,officialUnionEmpty,frame.officialContent);
  }
  need(!sourceUnionEmpty&&!officialUnionEmpty,"ANIMATION_AUTHORED_COMPOSITE_CONTENT_UNION_EMPTY");
  int sourceStructW=sourceStructure.x1-sourceStructure.x0,sourceStructH=sourceStructure.y1-sourceStructure.y0;
  int officialStructW=officialStructure.x1-officialStructure.x0,officialStructH=officialStructure.y1-officialStructure.y0;
  need(sourceStructW>0&&sourceStructH>0&&officialStructW>0&&officialStructH>0,"ANIMATION_AUTHORED_COMPOSITE_STRUCTURE_DIMENSIONS_INVALID");
  int targetStructW=officialStructW*targetScale,targetStructH=officialStructH*targetScale;
  double scale=std::min(double(targetStructW)/sourceStructW,double(targetStructH)/sourceStructH);
  need(std::isfinite(scale)&&scale>0,"ANIMATION_AUTHORED_COMPOSITE_STRUCTURE_SCALE_INVALID");
  int fitW=std::max(1,int(std::lround(sourceStructW*scale))),fitH=std::max(1,int(std::lround(sourceStructH*scale)));
  int refX0=officialStructure.x0*targetScale,refY0=officialStructure.y0*targetScale;
  int refX1=officialStructure.x1*targetScale,refY1=officialStructure.y1*targetScale;
  int structX0=0,structY0=0;
  if(first.anchor=="bottom_right"){structX0=refX1-fitW;structY0=refY1-fitH;}
  else{structX0=refX0+(targetStructW-fitW)/2;structY0=refY0+(targetStructH-fitH)/2;}
  int sourceUnionW=sourceUnionX1-sourceUnionX0,sourceUnionH=sourceUnionY1-sourceUnionY0;
  int targetUnionX0=structX0-int(std::lround((sourceStructure.x0-sourceUnionX0)*scale));
  int targetUnionY0=structY0-int(std::lround((sourceStructure.y0-sourceUnionY0)*scale));
  int targetUnionW=std::max(1,int(std::lround(sourceUnionW*scale)));
  int targetUnionH=std::max(1,int(std::lround(sourceUnionH*scale)));
  int targetCanvasW=officialW*targetScale,targetCanvasH=officialH*targetScale;
  int targetUnionX1=targetUnionX0+targetUnionW,targetUnionY1=targetUnionY0+targetUnionH;
  need(targetUnionX0>=0&&targetUnionY0>=0&&targetUnionX1<=targetCanvasW&&targetUnionY1<=targetCanvasH,
       "ANIMATION_AUTHORED_COMPOSITE_FULL_CEL_TRANSFORM_CLIPS_FX_OR_STRUCTURE: "+utf8(wideKey));
  Json frameTransform={{"schema","h3.animation.group-uniform-fit.v1"},{"group_key",utf8(wideKey)},
   {"source_canvas",{sourceW,sourceH}},{"target_canvas_3x",{targetCanvasW,targetCanvasH}},
   {"source_union_box",{sourceUnionX0,sourceUnionY0,sourceUnionX1,sourceUnionY1}},
   {"official_union_box_1x",{officialUnionX0,officialUnionY0,officialUnionX1,officialUnionY1}},
   {"reference_union_box_3x",{officialUnionX0*targetScale,officialUnionY0*targetScale,officialUnionX1*targetScale,officialUnionY1*targetScale}},
   {"target_union_box_3x",{targetUnionX0,targetUnionY0,targetUnionX1,targetUnionY1}},
   {"scale",scale},{"offset_pixels",{targetUnionX0,targetUnionY0}},{"anchor",first.anchor},
   {"registration","one_shared_transform_for_all_full_cels"},
   {"fit_basis","authored-static-structure-v2"},
   {"source_structure_box",{sourceStructure.x0,sourceStructure.y0,sourceStructure.x1,sourceStructure.y1}},
   {"official_structure_box_1x",{officialStructure.x0,officialStructure.y0,officialStructure.x1,officialStructure.y1}},
   {"target_structure_box_3x",{structX0,structY0,structX0+fitW,structY0+fitH}},
   {"authored_effect_roi_path",str(clean_path(roiPath))},{"authored_effect_roi_sha256",expectedRoiSha},
   {"authored_effect_roi_pixels",roiPixels}};
  for(const auto& frame:frames)items[frame.index]["animation_group_transform"]=frameTransform;
 }
}

inline void verify_animation_group_transforms(const Json& batch,const std::vector<ExpansionJob>& jobs){
 need(batch.contains("animation_contract")
      &&(batch.at("animation_contract")==ANIMATION_CONTRACT_V2
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V1
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V2
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2),
      "ANIMATION_GROUP_TRANSFORM_CONTRACT_INVALID");
 Json entries=batch.at("items");
 auto contract=batch.at("animation_contract").get<std::string>();
 if(contract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2){
  const auto& roi=batch.at("animation_gate").at("authored_effect_roi");
  seal_animation_group_transforms_authored_composite(entries,clean_path(path(roi.at("path").get<std::string>())),roi.at("sha256").get<std::string>());
 }else seal_animation_group_transforms(entries);
 need(entries.size()==batch.at("items").size(),"ANIMATION_GROUP_TRANSFORM_ITEM_COUNT_DRIFT");
 for(std::size_t i=0;i<entries.size();i++)
  need(batch.at("items")[i].contains("animation_group_transform")&&batch.at("items")[i].at("animation_group_transform")==entries[i].at("animation_group_transform"),
       "ANIMATION_GROUP_TRANSFORM_DRIFT: "+batch.at("items")[i].value("id",std::string("unknown")));
 need(jobs.size()==entries.size(),"ANIMATION_GROUP_TRANSFORM_JOB_COUNT_DRIFT");
 for(std::size_t i=0;i<jobs.size();i++)need(jobs[i].animationGroupTransform==entries[i].at("animation_group_transform"),
       "ANIMATION_GROUP_TRANSFORM_JOB_BINDING_DRIFT: "+jobs[i].id);
}
inline void verify_animation_reference_inventory(const Json& batch,const std::vector<ExpansionJob>& jobs){
 need(batch.contains("animation_gate")&&batch.at("animation_gate").contains("official_frame_inventory")
      &&batch.at("animation_gate").at("official_frame_inventory").is_array(),
      "ANIMATION_OFFICIAL_FRAME_INVENTORY_MISSING: re-plan with the current static-FX scale gate");
 using GroupKey=std::wstring;
 struct Selected {std::wstring identity,group;std::set<std::wstring> names;std::vector<const ExpansionJob*> jobs;};
 std::map<GroupKey,Selected> selected;
 for(const auto& job:jobs){
  auto parsed=animation_group_frame(job.source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(job.source));
  auto identity=job.source.parent_path().filename().wstring();auto key=animation_folded_text(identity+L"|"+parsed->first);
  auto& row=selected[key];row.identity=identity;row.group=parsed->first;
  need(row.names.insert(animation_folded_text(job.source.filename().wstring())).second,"ANIMATION_DUPLICATE_SOURCE_FRAME: "+str(job.source));
  row.jobs.push_back(&job);
 }
 const auto& rows=batch.at("animation_gate").at("official_frame_inventory");
 need(rows.size()==selected.size(),"ANIMATION_OFFICIAL_FRAME_GROUP_SET_MISMATCH");
 std::set<GroupKey> seen;
 for(const auto& row:rows){
  need(row.is_object()&&row.contains("identity")&&row.contains("group")&&row.contains("reference_directory")&&row.contains("frames")&&row.at("frames").is_array(),
       "ANIMATION_OFFICIAL_FRAME_INVENTORY_INVALID");
  auto identity=path(row.at("identity").get<std::string>()).wstring();
  auto group=path(row.at("group").get<std::string>()).wstring();
  auto key=animation_folded_text(identity+L"|"+group);need(seen.insert(key).second,"ANIMATION_OFFICIAL_FRAME_GROUP_DUPLICATE");
  auto found=selected.find(key);need(found!=selected.end(),"ANIMATION_OFFICIAL_FRAME_GROUP_SET_MISMATCH: "+row.at("identity").get<std::string>()+"/"+row.at("group").get<std::string>());
  auto directory=clean_path(path(row.at("reference_directory").get<std::string>()));
  need(fs::exists(directory)&&fs::is_directory(directory),"ANIMATION_OFFICIAL_REFERENCE_DIRECTORY_MISSING: "+str(directory));
  auto refs=animation_reference_frames(directory,found->second.group);std::set<std::wstring> actualNames,manifestNames;
  for(const auto& ref:refs)actualNames.insert(animation_folded_text(ref.filename().wstring()));
  for(const auto& frame:row.at("frames")){
   need(frame.is_object()&&frame.contains("name")&&frame.contains("path")&&frame.contains("sha256")&&frame.contains("pixels"),"ANIMATION_OFFICIAL_FRAME_ROW_INVALID");
   auto name=frame.at("name").get<std::string>();auto file=clean_path(path(frame.at("path").get<std::string>()));
   need(clean_path(file.parent_path())==directory&&animation_folded_text(file.filename().wstring())==animation_folded_text(path(name).filename().wstring()),"ANIMATION_OFFICIAL_FRAME_PATH_INVALID: "+name);
   auto raw=read_all(file);need(sha(raw)==frame.at("sha256").get<std::string>(),"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(file));
   Wic decoder;auto meta=decoder.frame(raw);need(frame.at("pixels")==Json::array({meta.w,meta.h}),"ANIMATION_OFFICIAL_FRAME_DIMENSION_DRIFT: "+str(file));
   need(manifestNames.insert(animation_folded_text(file.filename().wstring())).second,"ANIMATION_OFFICIAL_FRAME_DUPLICATE: "+name);
  }
  need(manifestNames==actualNames,"ANIMATION_OFFICIAL_FRAME_INVENTORY_DRIFT: "+str(directory)+"/"+utf8(found->second.group));
  need(found->second.names==manifestNames,"ANIMATION_OFFICIAL_FRAME_SET_MISMATCH: "+row.at("identity").get<std::string>()+"/"+row.at("group").get<std::string>());
  for(const auto* job:found->second.jobs){
   need(clean_path(job->reference.parent_path())==directory,"ANIMATION_OFFICIAL_REFERENCE_DIRECTORY_MISMATCH: "+str(job->reference));
   auto refName=animation_folded_text(job->reference.filename().wstring());
   auto rowIt=std::find_if(row.at("frames").begin(),row.at("frames").end(),[&](const Json& frame){return animation_folded_text(path(frame.at("name").get<std::string>()).filename().wstring())==refName;});
   need(rowIt!=row.at("frames").end()&&rowIt->at("sha256")==job->referenceSha,"ANIMATION_OFFICIAL_FRAME_BINDING_MISMATCH: "+str(job->reference));
  }
 }
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
 Json result={{"contract",ANIMATION_CONTRACT_V1},{"status",jobs.empty()?"BLOCKED":"PASS"},{"limits",{{"max_translation_delta_output_px",0},{"max_size_delta_output_px",0},{"max_scale_delta_percent",0.1}}},{"groups",Json::array()},{"failures",Json::array()}};
 if(jobs.empty()){
  result["failures"].push_back({{"group",""},{"frame",""},{"reason","ANIMATION_REGISTRATION_NOT_EVALUATED"},
      {"action","No expansion frame passed the per-item preflight; an empty registration audit is never a pass."}});
  return result;
 }
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
inline std::vector<std::uint8_t> animation_base_then_overlay_rgba(const std::vector<std::uint8_t>& base,
 const std::vector<std::uint8_t>& overlay){
 need(base.size()==overlay.size()&&base.size()%4==0,"ANIMATION_BASE_OVERLAY_RGBA_SIZE_MISMATCH");
 auto out=base;
 for(std::size_t at=0;at<out.size();at+=4){
  const unsigned sa=overlay[at+3];if(sa==0)continue;
  if(sa==255){std::copy_n(overlay.data()+at,4,out.data()+at);continue;}
  const unsigned da=base[at+3];
  const unsigned alphaNumerator=sa*255u+da*(255u-sa);
  if(alphaNumerator==0){std::fill_n(out.data()+at,4,std::uint8_t(0));continue;}
  for(unsigned c=0;c<3;c++){
   const unsigned colorNumerator=unsigned(overlay[at+c])*sa*255u+unsigned(base[at+c])*da*(255u-sa);
   out[at+c]=std::uint8_t((colorNumerator+alphaNumerator/2u)/alphaNumerator);
  }
  out[at+3]=std::uint8_t(std::min(255u,(alphaNumerator+127u)/255u));
 }
 return out;
}
inline ArtworkBox animation_rgba_artwork_box(const std::vector<std::uint8_t>& rgba,int width,int height){
 need(width>0&&height>0&&rgba.size()==std::size_t(width)*height*4,"ANIMATION_RGBA_BOX_SIZE_MISMATCH");
 ArtworkBox box;const int threshold=int(std::ceil(SILHOUETTE_SOLID*255.f));
 for(int y=0;y<height;y++)for(int x=0;x<width;x++){
  if(rgba[(std::size_t(y)*width+x)*4+3]<threshold)continue;
  if(box.empty)box={x,y,x+1,y+1,false,false};
  else{box.x0=std::min(box.x0,x);box.y0=std::min(box.y0,y);box.x1=std::max(box.x1,x+1);box.y1=std::max(box.y1,y+1);}
 }
 return box;
}
inline Json post_export_static_frame_scale_gate(const ExpansionJob& job,const std::vector<std::uint8_t>& rgba){
 const auto visible=animation_rgba_artwork_box(rgba,job.w,job.h);
 const int expectedW=job.slotBox.x1-job.slotBox.x0,expectedH=job.slotBox.y1-job.slotBox.y0;
 const int visibleW=visible.empty?0:visible.x1-visible.x0,visibleH=visible.empty?0:visible.y1-visible.y0;
 const double widthRatio=expectedW>0?double(visibleW)/expectedW:0.0;
 const double heightRatio=expectedH>0?double(visibleH)/expectedH:0.0;
 const bool blocked=visible.empty||expectedW<=0||expectedH<=0||
  widthRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO||heightRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO;
 return{{"schema","h3.master-expand.visible-scale-post-export.v1"},{"status",blocked?"BLOCKED":"PASS"},
  {"id",job.id},{"output",str(job.output)},{"output_pixels",{job.w,job.h}},
  {"official_reference",str(job.reference)},{"official_reference_sha256",job.referenceSha},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
  {"expected_visible_box_3x",{job.slotBox.x0,job.slotBox.y0,job.slotBox.x1,job.slotBox.y1}},
  {"actual_visible_box_3x",visible.empty?Json::array():Json::array({visible.x0,visible.y0,visible.x1,visible.y1})},
  {"actual_visible_size_3x",{visibleW,visibleH}},{"expected_visible_size_3x",{expectedW,expectedH}},
  {"visible_width_ratio",widthRatio},{"visible_height_ratio",heightRatio},
  {"minimum_width_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},
  {"minimum_height_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},
  {"measurement","read-back 3X PNG alpha>=1/32 bounding box versus the matching official native visible box times target_scale"}};
}
struct VisibleScaleMetrics {double widthRatio=0,heightRatio=0,fitScale=0;int projectedWidth=0,projectedHeight=0;double postFitWidthRatio=0,postFitHeightRatio=0;};
inline Json static_frame_catalog_v2_legacy_policy(){
 return{{"schema","h3.master-expand.visible-scale-frame-catalog.v2"},
  {"minimum_width_ratio",STATIC_FRAME_SCALE_MIN_RATIO},{"minimum_height_ratio",STATIC_FRAME_SCALE_MIN_RATIO},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
  {"measurement","normalized source visible width/height fractions versus matching official frame fractions before any resampling"}};
}
inline Json static_frame_catalog_v3_legacy_policy(){
 return{{"schema","h3.master-expand.visible-scale-frame-catalog.v3"},
  {"minimum_width_ratio",STATIC_FRAME_SCALE_MIN_RATIO},{"minimum_height_ratio",STATIC_FRAME_SCALE_MIN_RATIO},
  {"minimum_post_fit_width_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},{"minimum_post_fit_height_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
  {"measurement","source visible fractions before resampling and predicted visible fractions after uniform official visible-box fit"}};
}
inline Json visible_scale_gate_policy(bool staticFrameCatalog=false){
 if(staticFrameCatalog)return{{"schema",STATIC_FRAME_SCALE_GATE_SCHEMA},
  {"minimum_width_ratio",STATIC_FRAME_SCALE_MIN_RATIO},{"minimum_height_ratio",STATIC_FRAME_SCALE_MIN_RATIO},
  {"minimum_post_fit_width_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},{"minimum_post_fit_height_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
   {"measurement","source alpha-box fractions and exact predicted crop-fit size versus the official visible box; verified again from the read-back 3X PNG"}};
 return{{"schema",VISIBLE_SCALE_GATE_SCHEMA},{"minimum_width_ratio",VISIBLE_SCALE_FLOOR_RATIO},
  {"minimum_height_ratio",VISIBLE_SCALE_FLOOR_RATIO},{"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
  {"measurement","predicted output artwork box after the official uniform fit versus the official visible box"}};
}
inline VisibleScaleMetrics measure_visible_scale(const ArtworkBox& sourceBox,const ArtworkBox& officialBox){
 need(!sourceBox.empty&&!officialBox.empty,"VISIBLE_SCALE_BOX_EMPTY");
 const int sourceW=sourceBox.x1-sourceBox.x0,sourceH=sourceBox.y1-sourceBox.y0;
 const int officialW=officialBox.x1-officialBox.x0,officialH=officialBox.y1-officialBox.y0;
 need(sourceW>0&&sourceH>0&&officialW>0&&officialH>0,"VISIBLE_SCALE_DIMENSION_INVALID");
 const double fitScale=std::min(double(officialW)/sourceW,double(officialH)/sourceH);
 int projectedW=std::max(1,int(std::lround(sourceW*fitScale)));
 int projectedH=std::max(1,int(std::lround(sourceH*fitScale)));
 if(projectedW>officialW)projectedW=officialW;
 if(projectedH>officialH)projectedH=officialH;
 return{double(projectedW)/officialW,double(projectedH)/officialH,fitScale,projectedW,projectedH};
}
inline VisibleScaleMetrics measure_static_frame_visible_scale(const ArtworkBox& sourceBox,int sourceCanvasW,int sourceCanvasH,
                                                               const ArtworkBox& officialBox,int officialCanvasW,int officialCanvasH){
 need(!sourceBox.empty&&!officialBox.empty,"VISIBLE_SCALE_BOX_EMPTY");
 need(sourceCanvasW>0&&sourceCanvasH>0&&officialCanvasW>0&&officialCanvasH>0,"VISIBLE_SCALE_CANVAS_INVALID");
 const int sourceW=sourceBox.x1-sourceBox.x0,sourceH=sourceBox.y1-sourceBox.y0;
 const int officialW=officialBox.x1-officialBox.x0,officialH=officialBox.y1-officialBox.y0;
 need(sourceW>0&&sourceH>0&&officialW>0&&officialH>0,"VISIBLE_SCALE_DIMENSION_INVALID");
 const double sourceWidthFraction=double(sourceW)/sourceCanvasW,sourceHeightFraction=double(sourceH)/sourceCanvasH;
 const double officialWidthFraction=double(officialW)/officialCanvasW,officialHeightFraction=double(officialH)/officialCanvasH;
 need(officialWidthFraction>0&&officialHeightFraction>0,"VISIBLE_SCALE_OFFICIAL_FRACTION_INVALID");
 const double widthRatio=sourceWidthFraction/officialWidthFraction,heightRatio=sourceHeightFraction/officialHeightFraction;
  // The exporter crops the source to its visible box, then fits that box into the official
  // visible box. Predicting a fit of the whole padded canvas can overstate one axis by a pixel
  // (the CH03 1_2 failure was predicted as 106/111 although export produced 105/111).
  const auto postFit=measure_visible_scale(sourceBox,officialBox);
  return{widthRatio,heightRatio,postFit.fitScale,postFit.projectedWidth,postFit.projectedHeight,
         postFit.widthRatio,postFit.heightRatio};
}
inline bool visible_scale_below_floor(const VisibleScaleMetrics& m,bool staticFrameCatalog=false){
 const double floor=staticFrameCatalog?STATIC_FRAME_SCALE_MIN_RATIO:VISIBLE_SCALE_FLOOR_RATIO;
 if(m.widthRatio<floor||m.heightRatio<floor)return true;
 return staticFrameCatalog&&(m.postFitWidthRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO||
                             m.postFitHeightRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO);
}
inline Json visible_scale_diagnostic(const ExpansionJob& job,const ArtworkBox& sourceBox,const ArtworkBox& officialBox,
                                    const VisibleScaleMetrics& m){
 return{{"source",str(job.source)},{"source_sha256",job.sourceSha},{"official_reference",str(job.reference)},
  {"official_reference_sha256",job.referenceSha},{"source_visible_box_pixels",{sourceBox.x0,sourceBox.y0,sourceBox.x1,sourceBox.y1}},
  {"projected_visible_size",{m.projectedWidth,m.projectedHeight}},
  {"official_visible_box",{officialBox.x0,officialBox.y0,officialBox.x1,officialBox.y1}},
  {"normalized_width_ratio",m.widthRatio},{"normalized_height_ratio",m.heightRatio},{"uniform_fit_scale",m.fitScale},
  {"minimum_width_ratio",VISIBLE_SCALE_FLOOR_RATIO},{"minimum_height_ratio",VISIBLE_SCALE_FLOOR_RATIO},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
  {"action","Re-draw or re-compose the subject so the predicted 3X visible box reaches at least 80% of the official visible box on both axes."}};
}
inline Json static_frame_visible_scale_diagnostic(const ExpansionJob& job,const ArtworkBox& sourceBox,const ArtworkBox& officialBox,
                                                   const VisibleScaleMetrics& metrics){
 const int sourceW=sourceBox.x1-sourceBox.x0,sourceH=sourceBox.y1-sourceBox.y0;
 const int officialW=officialBox.x1-officialBox.x0,officialH=officialBox.y1-officialBox.y0;
 return{{"source",str(job.source)},{"source_sha256",job.sourceSha},{"source_canvas_pixels",{job.sourceW,job.sourceH}},
  {"official_reference",str(job.reference)},{"official_reference_sha256",job.referenceSha},
  {"official_canvas_pixels",{job.referenceW,job.referenceH}},
  {"source_visible_box_pixels",{sourceBox.x0,sourceBox.y0,sourceBox.x1,sourceBox.y1}},
  {"official_visible_box_1x",{officialBox.x0,officialBox.y0,officialBox.x1,officialBox.y1}},
  {"source_visible_fraction",{double(sourceW)/job.sourceW,double(sourceH)/job.sourceH}},
  {"official_visible_fraction",{double(officialW)/job.referenceW,double(officialH)/job.referenceH}},
  {"projected_visible_size_1x",{metrics.projectedWidth,metrics.projectedHeight}},
  {"normalized_width_ratio",metrics.widthRatio},{"normalized_height_ratio",metrics.heightRatio},
  {"uniform_visible_fit_scale",metrics.fitScale},
  {"predicted_post_fit_width_ratio",metrics.postFitWidthRatio},{"predicted_post_fit_height_ratio",metrics.postFitHeightRatio},
  {"minimum_width_ratio",STATIC_FRAME_SCALE_MIN_RATIO},{"minimum_height_ratio",STATIC_FRAME_SCALE_MIN_RATIO},
  {"minimum_post_fit_width_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},{"minimum_post_fit_height_ratio",STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO},
  {"alpha_threshold",int(std::ceil(SILHOUETTE_SOLID*255.f))},
   {"measurement","source alpha fractions plus the exact visible-box crop-fit prediction used by the exporter; read-back 3X alpha bounds are checked separately"},
   {"action","Recompose the source proportions so both source coverage and the actual crop-fit visible width/height remain at least 95% of the official frame."}};
}
inline Json visible_scale_gate_audit(const std::vector<ExpansionJob>& jobs,std::size_t requested,bool staticFrameCatalog=false){
 const auto policy=visible_scale_gate_policy(staticFrameCatalog);
 Json result={{"schema",policy.at("schema")},{"status","PASS"},{"policy",policy},
  {"requested_frames",requested},{"validated_frames",jobs.size()},{"frames",Json::array()},{"failures",Json::array()}};
 if(jobs.empty()||jobs.size()!=requested){
  result["status"]="BLOCKED";result["failures"].push_back({{"id","frame-set"},{"reason","VISIBLE_SCALE_GATE_INPUTS_INCOMPLETE"},
   {"diagnostic",{{"requested_frames",requested},{"validated_frames",jobs.size()}}}});return result;
 }
 Wic decoder;
 for(const auto& job:jobs){
  try{
   need(job.silhouette,"OFFICIAL_VISIBLE_SCALE_REFERENCE_REQUIRED: "+str(job.source));
   auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"VISIBLE_SCALE_SOURCE_DRIFT: "+str(job.source));
   std::string note;auto image=decoder.decode(raw,true,note);auto sourceBox=artwork_box(image);
   ArtworkBox officialBox=staticFrameCatalog?job.slotBox1x:job.slotBox;
   auto metrics=staticFrameCatalog
      ?measure_static_frame_visible_scale(sourceBox,job.sourceW,job.sourceH,officialBox,job.referenceW,job.referenceH)
      :measure_visible_scale(sourceBox,officialBox);
   auto diagnostic=staticFrameCatalog?static_frame_visible_scale_diagnostic(job,sourceBox,officialBox,metrics)
      :visible_scale_diagnostic(job,sourceBox,officialBox,metrics);
   const bool rawTooSmall=metrics.widthRatio<STATIC_FRAME_SCALE_MIN_RATIO||metrics.heightRatio<STATIC_FRAME_SCALE_MIN_RATIO;
   const bool postFitTooSmall=staticFrameCatalog&&(metrics.postFitWidthRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO||
                                                   metrics.postFitHeightRatio<STATIC_FRAME_POST_FIT_SCALE_MIN_RATIO);
   const bool tooSmall=visible_scale_below_floor(metrics,staticFrameCatalog);
   if(staticFrameCatalog)diagnostic["blocking_stage"]=rawTooSmall?"pre-fit-source-fraction":postFitTooSmall?"post-fit-visible-aspect":"none";
   diagnostic["status"]=tooSmall?"BLOCKED":"PASS";
   result["frames"].push_back(diagnostic);
   if(tooSmall)result["failures"].push_back({{"id",job.id},{"reason","MASTER_VISIBLE_SCALE_TOO_SMALL"},{"diagnostic",diagnostic}});
  }catch(const std::exception& e){result["failures"].push_back({{"id",job.id},{"reason","VISIBLE_SCALE_GATE_FRAME_BLOCKED"},{"diagnostic",{{"error",e.what()}}}});}
 }
 result["status"]=result["failures"].empty()?"PASS":"BLOCKED";result["failure_count"]=result["failures"].size();return result;
}
// Version 2 audits complete composite frames. BASE v1 preserves the original
// fixed 20% shared-pixel requirement. BASE v2 compares the source's fixed-area
// fraction with the official sequence's own fraction and also checks each FX
// frame's absolute size and signed anchor against its matching native frame.
// The legacy v1 audit remains available only for replaying historical receipts.
inline Json static_animation_scale_audit_v2(const std::vector<ExpansionJob>& jobs,std::size_t requested,int preblocked,bool baseOverlay=false,bool nativeBaseEnvelope=false){
 struct Measure {
  const ExpansionJob* job=nullptr;ArtworkBox sourceBox,projectedBox,officialBox;
  double ratioW=0,ratioH=0,aspectErrorPercent=0,visibleScalePercent=0,fitScale=0;
  std::vector<std::uint8_t> rgba,officialRgba;
 };
  const char* auditContract=nativeBaseEnvelope?ANIMATION_CONTRACT_BASE_OVERLAY_V2:baseOverlay?ANIMATION_CONTRACT_BASE_OVERLAY_V1:ANIMATION_CONTRACT_V2;
  Json result={{"contract",auditContract},{"gate_version",nativeBaseEnvelope?2:baseOverlay?1:2},{"status","PASS"},
  {"limits",{{"max_visible_size_deviation_percent",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT},
             {"max_visible_aspect_deviation_percent",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},
             {"max_frame_scale_spread_percent",ANIMATION_FRAME_SCALE_SPREAD_LIMIT_PERCENT},
             {"max_fx_excess_spread_percentage_points",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
              {"minimum_static_base_fraction",nativeBaseEnvelope?0.0:ANIMATION_STATIC_BASE_MIN_FRACTION},
              {"static_base_native_deficit_limit_percentage_points",nativeBaseEnvelope?ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS:0.0},
              {"max_dynamic_fx_frame_size_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT:0.0},
              {"max_dynamic_fx_frame_anchor_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT:0.0}}},
  {"input_frame_count",requested},{"preflight_blocked_items",preblocked},
  {"groups",Json::array()},{"failures",Json::array()}};
 auto fail=[&](const std::string& group,const std::string& frame,const std::string& reason,const Json& detail=Json::object()){
  result["status"]="BLOCKED";
  result["failures"].push_back({{"group",group},{"frame",frame},{"reason",reason},{"diagnostic",detail}});
 };
 if(jobs.empty()||jobs.size()!=requested||preblocked>0){
  fail("","","ANIMATION_SCALE_INPUTS_INCOMPLETE",
       {{"valid_frames",jobs.size()},{"requested_frames",requested},{"preflight_blocked_items",preblocked},
        {"action","Repair missing or geometrically invalid frames before exporting an animation group."}});
 }
 std::map<std::string,std::vector<const ExpansionJob*>> jobGroups;
 for(const auto& job:jobs){
  auto key=expand_frame_group(job.source);
  if(key.empty()){fail(str(job.source.parent_path()),str(job.source.filename()),"ANIMATION_SCALE_FRAME_NAME_REQUIRED");continue;}
  jobGroups[key].push_back(&job);
 }
 Wic decoder;
 for(auto& [key,groupJobs]:jobGroups){
  std::sort(groupJobs.begin(),groupJobs.end(),[](const ExpansionJob* a,const ExpansionJob* b){
   auto number=[](const fs::path& p){auto s=p.stem().wstring();auto at=s.rfind(L'_');return at==std::wstring::npos?0u:(unsigned)std::stoul(s.substr(at+1));};
   return number(a->source)<number(b->source);
  });
  if(groupJobs.size()<2){fail(key,"","ANIMATION_SCALE_GROUP_NEEDS_AT_LEAST_2_FRAMES");continue;}
  if(baseOverlay){
   auto first=animation_group_frame(groupJobs.front()->source);
   if(!first||first->second!=L"0"){fail(key,"","ANIMATION_BASE_OVERLAY_FRAME0_REQUIRED",
      {{"first_frame",str(groupJobs.front()->source.filename())},{"action","The complete static base must be the first logical frame, frame 0."}});continue;}
  }
  std::vector<Measure> frames;frames.reserve(groupJobs.size());int canvasW=0,canvasH=0,officialCanvasW=0,officialCanvasH=0;std::uint64_t auditPixels=0;
  std::vector<std::uint8_t> baseSourceRgba,baseOfficialRgba;
  for(const auto* job:groupJobs){
   const auto frame=job->source.filename().string();
   try{
    need(job->silhouette,"ANIMATION_SCALE_OFFICIAL_REFERENCE_REQUIRED: "+str(job->source));
    auto raw=read_all(job->source);need(sha(raw)==job->sourceSha,"ANIMATION_SCALE_SOURCE_DRIFT: "+str(job->source));
    auto officialRaw=read_all(job->reference);need(sha(officialRaw)==job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(job->reference));
    std::string note;auto image=decoder.decode(raw,true,note);auto box=artwork_box(image);
    need(!box.empty,"ANIMATION_SCALE_EMPTY_MASTER: "+str(job->source));
    need(job->animationGroupTransform.is_object(),"ANIMATION_GROUP_TRANSFORM_MISSING: "+str(job->source));
    double fitScale=job->animationGroupTransform.at("scale").get<double>();
    const auto& offset=job->animationGroupTransform.at("offset_pixels");int ox=offset[0].get<int>(),oy=offset[1].get<int>();
    const auto& groupSourceBox=job->animationGroupTransform.at("source_union_box");
    int gsx=groupSourceBox[0].get<int>(),gsy=groupSourceBox[1].get<int>();
    auto rgba=decoder.decode_rgba(raw,job->sourceW,job->sourceH);
    auto officialRgba=decoder.decode_rgba(officialRaw,job->referenceW,job->referenceH);
    ArtworkBox officialBox=job->slotBox1x;
    if(baseOverlay){
     if(job==groupJobs.front()){baseSourceRgba=rgba;baseOfficialRgba=officialRgba;}
     need(!baseSourceRgba.empty()&&!baseOfficialRgba.empty(),"ANIMATION_BASE_OVERLAY_BASE_MISSING: "+key);
     rgba=animation_base_then_overlay_rgba(baseSourceRgba,rgba);
     officialRgba=animation_base_then_overlay_rgba(baseOfficialRgba,officialRgba);
     box=animation_rgba_artwork_box(rgba,job->sourceW,job->sourceH);
     officialBox=animation_rgba_artwork_box(officialRgba,job->referenceW,job->referenceH);
     need(!box.empty&&!officialBox.empty,"ANIMATION_BASE_OVERLAY_EFFECTIVE_FRAME_EMPTY: "+str(job->source));
    }
    int refW=officialBox.x1-officialBox.x0,refH=officialBox.y1-officialBox.y0;
    need(refW>0&&refH>0&&job->referenceW>0&&job->referenceH>0,"ANIMATION_SCALE_EMPTY_OFFICIAL_VISIBLE_BOX: "+str(job->source));
    ArtworkBox projected{int(std::lround((box.x0-gsx)*fitScale))+ox,int(std::lround((box.y0-gsy)*fitScale))+oy,
      int(std::lround((box.x1-gsx)*fitScale))+ox,int(std::lround((box.y1-gsy)*fitScale))+oy,false,false};
    int projectedW=projected.x1-projected.x0,projectedH=projected.y1-projected.y0;
    int referenceW=refW*job->referenceScale,referenceH=refH*job->referenceScale;
    need(projectedW>0&&projectedH>0&&referenceW>0&&referenceH>0,"ANIMATION_SCALE_BAD_VISIBLE_FRACTION: "+str(job->source));
    double ratioW=double(projectedW)/referenceW,ratioH=double(projectedH)/referenceH;
    double aspectError=std::fabs(ratioW/ratioH-1.0)*100.0;
    double sizeError=std::max(std::fabs(ratioW-1.0),std::fabs(ratioH-1.0))*100.0;
    int slotW=job->slotBox.x1-job->slotBox.x0,slotH=job->slotBox.y1-job->slotBox.y0;
    auto sourcePixels=std::uint64_t(job->sourceW)*job->sourceH;
    auto officialPixels=std::uint64_t(job->referenceW)*job->referenceH;
    auditPixels+=sourcePixels+officialPixels;
    need(auditPixels<=256ull*1024*1024,"ANIMATION_SCALE_PIXEL_BUDGET_EXCEEDED: "+key);
    if(!canvasW){canvasW=job->sourceW;canvasH=job->sourceH;}
    if(job->sourceW!=canvasW||job->sourceH!=canvasH)
     fail(key,frame,"ANIMATION_MASTER_CANVAS_DRIFT",{{"expected_pixels",{canvasW,canvasH}},{"actual_pixels",{job->sourceW,job->sourceH}}});
    if(!officialCanvasW){officialCanvasW=job->referenceW;officialCanvasH=job->referenceH;}
    need(job->referenceW==officialCanvasW&&job->referenceH==officialCanvasH,
         "ANIMATION_OFFICIAL_CANVAS_DRIFT: "+str(job->reference));
    if(aspectError>ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT)
     fail(key,frame,"ANIMATION_VISIBLE_ASPECT_MISMATCH",
       {{"master_artwork_box",{box.x0,box.y0,box.x1,box.y1}},
        {"official_visible_box_1x",{officialBox.x0,officialBox.y0,officialBox.x1,officialBox.y1}},
        {"normalized_width_ratio",ratioW},{"normalized_height_ratio",ratioH},{"aspect_deviation_percent",aspectError},
        {"action","Redraw/recompose the master to the official visible aspect; uniform fitting would make one axis visibly small."}});
    if(sizeError>ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT)
     fail(key,frame,"ANIMATION_VISIBLE_SIZE_MISMATCH",
       {{"normalized_width_ratio",ratioW},{"normalized_height_ratio",ratioH},{"maximum_size_deviation_percent",sizeError},
        {"uniform_reference_fit_scale",fitScale},
        {"action","The shared group fit still leaves this frame outside native visible-size tolerance; redraw or recompose the frame without changing the group's common transform."}});
    frames.push_back({job,box,projected,officialBox,ratioW,ratioH,aspectError,sizeError,fitScale,std::move(rgba),std::move(officialRgba)});
   }catch(const std::exception& e){fail(key,frame,"ANIMATION_SCALE_FRAME_BLOCKED",{{"error",e.what()}});}
  }
  if(frames.size()<2) { fail(key,"","ANIMATION_SCALE_GROUP_NOT_MEASURED",{{"valid_frames",frames.size()}});continue; }
  double minScale=std::numeric_limits<double>::infinity(),maxScale=0;
  for(const auto& f:frames){double scale=std::sqrt(f.ratioW*f.ratioH);minScale=std::min(minScale,scale);maxScale=std::max(maxScale,scale);}
  double scaleSpread=minScale>0?(maxScale/minScale-1.0)*100.0:0;
  if(scaleSpread>ANIMATION_FRAME_SCALE_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_FRAME_SCALE_DRIFT",
        {{"frame_scale_spread_percent",scaleSpread},{"limit_percent",ANIMATION_FRAME_SCALE_SPREAD_LIMIT_PERCENT},
         {"action","Compare each frame to its own official visible bounds and redraw outlier frames."}});
  // BASE frame 0 is the shared base itself (and already contains its initial
  // flag pose). Compare that single base against later base+overlay scenes;
  // composing frame 0 over itself would make semi-transparent edge pixels look
  // like the whole structure changed even though no structural layer moved.
  auto referenceFrame=baseOverlay?baseSourceRgba:frames.front().rgba;
  auto officialReferenceFrame=baseOverlay?baseOfficialRgba:frames.front().officialRgba;
  auto pixels=std::size_t(canvasW)*canvasH,officialPixels=std::size_t(officialCanvasW)*officialCanvasH;
  std::vector<std::uint8_t> stable(pixels,1),officialStable(officialPixels,1);
  for(std::size_t i=1;i<frames.size();i++){
   const auto& rgba=frames[i].rgba;const auto& officialRgba=frames[i].officialRgba;
   for(std::size_t p=0;p<pixels;p++){
    auto at=p*4;
    if(stable[p]&&(rgba[at]!=referenceFrame[at]||rgba[at+1]!=referenceFrame[at+1]||rgba[at+2]!=referenceFrame[at+2]||rgba[at+3]!=referenceFrame[at+3]))stable[p]=0;
   }
   for(std::size_t p=0;p<officialPixels;p++){
    auto at=p*4;
    if(officialStable[p]&&(officialRgba[at]!=officialReferenceFrame[at]||officialRgba[at+1]!=officialReferenceFrame[at+1]||officialRgba[at+2]!=officialReferenceFrame[at+2]||officialRgba[at+3]!=officialReferenceFrame[at+3]))officialStable[p]=0;
   }
  }
  std::uint64_t stablePixels=0,officialStablePixels=0;
  std::uint64_t minVisible=std::numeric_limits<std::uint64_t>::max(),officialMinVisible=std::numeric_limits<std::uint64_t>::max();
  int x0=canvasW,y0=canvasH,x1=-1,y1=-1,officialX0=canvasW,officialY0=canvasH,officialX1=-1,officialY1=-1;
  for(const auto& f:frames){
   std::uint64_t visible=0,officialVisible=0;
   for(std::size_t p=0;p<pixels;p++)if(f.rgba[p*4+3]>=16)visible++;
   for(std::size_t p=0;p<officialPixels;p++)if(f.officialRgba[p*4+3]>=16)officialVisible++;
   minVisible=std::min(minVisible,visible);
   officialMinVisible=std::min(officialMinVisible,officialVisible);
  }
  for(int y=0;y<canvasH;y++)for(int x=0;x<canvasW;x++){auto p=std::size_t(y)*canvasW+x;
   if(stable[p]&&referenceFrame[p*4+3]>=16){stablePixels++;x0=std::min(x0,x);y0=std::min(y0,y);x1=std::max(x1,x+1);y1=std::max(y1,y+1);}
  }
  for(int y=0;y<officialCanvasH;y++)for(int x=0;x<officialCanvasW;x++){auto p=std::size_t(y)*officialCanvasW+x;
    if(officialStable[p]&&officialReferenceFrame[p*4+3]>=16){officialStablePixels++;officialX0=std::min(officialX0,x);officialY0=std::min(officialY0,y);officialX1=std::max(officialX1,x+1);officialY1=std::max(officialY1,y+1);}
  }
  double stableFraction=minVisible?double(stablePixels)/minVisible:0;
  double officialStableFraction=officialMinVisible?double(officialStablePixels)/officialMinVisible:0;
  // A more stable source is allowed; extra movement below the official fixed
  // pixel fraction is limited because it creates the flashing the gate detects.
  double nativeStableDeficitPoints=std::max(0.0,officialStableFraction-stableFraction)*100.0;
  bool stableInvalid=nativeBaseEnvelope
      ?nativeStableDeficitPoints>ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS
      :(x1<=x0||y1<=y0||stableFraction<ANIMATION_STATIC_BASE_MIN_FRACTION);
  if(stableInvalid)
   fail(key,"","ANIMATION_STATIC_BASE_NOT_MEASURABLE",
        {{"stable_pixel_fraction",stableFraction},{"stable_pixels",stablePixels},{"visible_pixels_min",minVisible},
         {"official_stable_pixel_fraction",officialStableFraction},{"official_stable_pixels",officialStablePixels},
         {"official_visible_pixels_min",officialMinVisible},{"native_stability_deficit_percentage_points",nativeStableDeficitPoints},
         {"stable_bbox",x1>x0?Json::array({x0,y0,x1,y1}):Json(nullptr)},
         {"official_stable_bbox",officialX1>officialX0?Json::array({officialX0,officialY0,officialX1,officialY1}):Json(nullptr)},
         {"minimum_fraction",nativeBaseEnvelope?0.0:ANIMATION_STATIC_BASE_MIN_FRACTION},
         {"maximum_stability_deficit_from_official_percentage_points",nativeBaseEnvelope?ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS:0.0},
         {"action",nativeBaseEnvelope?"Keep at least the official fixed-pixel area, within five percentage points; extra stable pixels are allowed, while source pixels that flicker beyond the native sequence are blocked.":"Split the sequence into one shared static base layer and per-frame FX layers, then refreeze."}});
  // Compare animation amplitude and mean anchor, not each stylized FX silhouette
  // pixel-for-pixel. Official frame-to-frame variation is the allowed baseline;
  // excess master breathing and a shifted group anchor are the blocking defects.
   std::vector<std::array<double,4>> mean(pixels),officialMean(officialPixels);
  if(baseOverlay){
   // Dynamic measurements belong to the overlay layers, not the composed
   // house scenes. Frame 0 is the base/initial pose; frames 1+ are flag-only.
   std::size_t overlayFrames=0;
   for(std::size_t i=1;i<frames.size();i++){
    const auto& f=frames[i];auto sourceRaw=read_all(f.job->source),officialRaw=read_all(f.job->reference);
    need(sha(sourceRaw)==f.job->sourceSha,"ANIMATION_SCALE_SOURCE_DRIFT: "+str(f.job->source));
    need(sha(officialRaw)==f.job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(f.job->reference));
    auto sourceOverlay=decoder.decode_rgba(sourceRaw,f.job->sourceW,f.job->sourceH);
    auto officialOverlay=decoder.decode_rgba(officialRaw,f.job->referenceW,f.job->referenceH);
    for(std::size_t p=0;p<pixels;p++)for(int c=0;c<4;c++)mean[p][c]+=sourceOverlay[p*4+c];
    for(std::size_t p=0;p<officialPixels;p++)for(int c=0;c<4;c++)officialMean[p][c]+=officialOverlay[p*4+c];
    overlayFrames++;
   }
   if(overlayFrames){
    for(auto& pixel:mean)for(auto& channel:pixel)channel/=double(overlayFrames);
    for(auto& pixel:officialMean)for(auto& channel:pixel)channel/=double(overlayFrames);
   }
  }else{
   for(std::size_t p=0;p<pixels;p++)for(int c=0;c<4;c++){
    double sum=0;for(const auto& f:frames)sum+=f.rgba[p*4+c];mean[p][c]=sum/double(frames.size());
   }
   for(std::size_t p=0;p<officialPixels;p++)for(int c=0;c<4;c++){
    double sum=0;for(const auto& f:frames)sum+=f.officialRgba[p*4+c];officialMean[p][c]=sum/double(frames.size());
   }
  }
  struct FxExtent {int x0=0,y0=0,x1=0,y1=0;std::uint64_t changed=0;bool present=false;};
  auto changed_extent=[](const std::vector<std::uint8_t>& rgba,const std::vector<std::array<double,4>>& average,int w,int h,bool include_overlay_absence){
   FxExtent extent;extent.x0=w;extent.y0=h;extent.x1=-1;extent.y1=-1;
   for(int y=0;y<h;y++)for(int x=0;x<w;x++){
    auto p=std::size_t(y)*w+x,at=p*4;
    // In BASE mode, a transparent overlay pixel can still change the effective
    // scene relative to another phase: it reveals the already-drawn frame 0.
    // Compare those pixels whenever the group mean has visible alpha. Keep the
    // v2 rule unchanged for complete independent frames.
    if(rgba[at+3]<16&&(!include_overlay_absence||average[p][3]<16.0))continue;
    double delta=std::max({std::fabs(double(rgba[at])-average[p][0]),
                           std::fabs(double(rgba[at+1])-average[p][1]),
                           std::fabs(double(rgba[at+2])-average[p][2]),
                           std::fabs(double(rgba[at+3])-average[p][3])});
    if(delta<16.0)continue;
    extent.changed++;extent.x0=std::min(extent.x0,x);extent.y0=std::min(extent.y0,y);
    extent.x1=std::max(extent.x1,x+1);extent.y1=std::max(extent.y1,y+1);
   }
   extent.present=extent.x1>extent.x0&&extent.y1>extent.y0;return extent;
  };
  Json fxRows=Json::array();int sourceFxFrames=0,officialFxFrames=0,fxActivityMismatchCount=0,fxSizeMismatchCount=0,fxAnchorMismatchCount=0;
  std::vector<double> sourceWidths,sourceHeights,officialWidths,officialHeights,sourceCentersX,sourceCentersY,officialCentersX,officialCentersY;
  for(std::size_t frameIndex=0;frameIndex<frames.size();frameIndex++){
   const auto& f=frames[frameIndex];
   if(baseOverlay&&frameIndex==0){
    fxRows.push_back({{"frame",str(f.job->source.filename())},{"role","static-base-with-initial-overlay"},
      {"master_bbox",nullptr},{"master_changed_pixels",0},{"official_bbox_1x",nullptr},{"official_changed_pixels",0}});
    continue;
   }
   std::vector<std::uint8_t> sourceOverlay,officialOverlay;
   const std::vector<std::uint8_t>* sourceFxImage=&f.rgba;const std::vector<std::uint8_t>* officialFxImage=&f.officialRgba;
   if(baseOverlay){
    auto sourceRaw=read_all(f.job->source),officialRaw=read_all(f.job->reference);
    need(sha(sourceRaw)==f.job->sourceSha,"ANIMATION_SCALE_SOURCE_DRIFT: "+str(f.job->source));
    need(sha(officialRaw)==f.job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(f.job->reference));
    sourceOverlay=decoder.decode_rgba(sourceRaw,f.job->sourceW,f.job->sourceH);
    officialOverlay=decoder.decode_rgba(officialRaw,f.job->referenceW,f.job->referenceH);
    sourceFxImage=&sourceOverlay;officialFxImage=&officialOverlay;
   }
   auto sourceFx=changed_extent(*sourceFxImage,mean,canvasW,canvasH,baseOverlay);
   auto officialFx=changed_extent(*officialFxImage,officialMean,officialCanvasW,officialCanvasH,baseOverlay);
   sourceFxFrames+=sourceFx.present;officialFxFrames+=officialFx.present;
   double widthDeviation=0,heightDeviation=0,centerXDelta=0,centerYDelta=0;
   double sourceW=0,sourceH=0,officialW=0,officialH=0,sourceCx=0,sourceCy=0,officialCx=0,officialCy=0;
   int projectedX0=0,projectedY0=0,projectedX1=0,projectedY1=0;
   if(sourceFx.present&&officialFx.present){
    // Source FX is authored in the high-resolution master canvas. Map it
    // through the same shared group transform used by expand before comparing
    // it with the official native frame. Comparing raw source-canvas fractions
    // is wrong when a master has transparent layout/padding or a different
    // canvas aspect from the official slot (for example a panoramic dock).
    const auto& transform=f.job->animationGroupTransform;
    const auto& sourceUnion=transform.at("source_union_box");
    const auto& offset=transform.at("offset_pixels");
    const auto& targetCanvas=transform.at("target_canvas_3x");
    double groupScale=transform.at("scale").get<double>();
    int groupX0=sourceUnion[0].get<int>(),groupY0=sourceUnion[1].get<int>();
    int offsetX=offset[0].get<int>(),offsetY=offset[1].get<int>();
    int targetW=targetCanvas[0].get<int>(),targetH=targetCanvas[1].get<int>();
    need(std::isfinite(groupScale)&&groupScale>0&&targetW>0&&targetH>0,
         "ANIMATION_FX_GROUP_TRANSFORM_INVALID: "+str(f.job->source));
    projectedX0=int(std::lround((sourceFx.x0-groupX0)*groupScale))+offsetX;
    projectedY0=int(std::lround((sourceFx.y0-groupY0)*groupScale))+offsetY;
    projectedX1=int(std::lround((sourceFx.x1-groupX0)*groupScale))+offsetX;
    projectedY1=int(std::lround((sourceFx.y1-groupY0)*groupScale))+offsetY;
    sourceW=double(projectedX1-projectedX0)/targetW;officialW=double(officialFx.x1-officialFx.x0)/officialCanvasW;
    sourceH=double(projectedY1-projectedY0)/targetH;officialH=double(officialFx.y1-officialFx.y0)/officialCanvasH;
    sourceCx=(projectedX0+projectedX1)/(2.0*targetW);sourceCy=(projectedY0+projectedY1)/(2.0*targetH);
    officialCx=(officialFx.x0+officialFx.x1)/(2.0*officialCanvasW);officialCy=(officialFx.y0+officialFx.y1)/(2.0*officialCanvasH);
    sourceWidths.push_back(sourceW);sourceHeights.push_back(sourceH);officialWidths.push_back(officialW);officialHeights.push_back(officialH);
    sourceCentersX.push_back(sourceCx);sourceCentersY.push_back(sourceCy);officialCentersX.push_back(officialCx);officialCentersY.push_back(officialCy);
    widthDeviation=std::fabs(sourceW/officialW-1.0)*100.0;heightDeviation=std::fabs(sourceH/officialH-1.0)*100.0;
    centerXDelta=std::fabs(sourceCx-officialCx)*100.0;centerYDelta=std::fabs(sourceCy-officialCy)*100.0;
     if(nativeBaseEnvelope&&(widthDeviation>ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT||heightDeviation>ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT)){
      fxSizeMismatchCount++;
      fail(key,str(f.job->source.filename()),"ANIMATION_DYNAMIC_FX_FRAME_SIZE_MISMATCH",
       {{"source_width_fraction",sourceW},{"official_width_fraction",officialW},{"width_deviation_percent",widthDeviation},
        {"source_height_fraction",sourceH},{"official_height_fraction",officialH},{"height_deviation_percent",heightDeviation},
        {"limit_percent",ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT},
        {"action","Apply one shared transform to the isolated overlay sequence so every frame matches its corresponding official FX size."}});
     }
     if(nativeBaseEnvelope&&(centerXDelta>ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT||centerYDelta>ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT)){
      fxAnchorMismatchCount++;
      fail(key,str(f.job->source.filename()),"ANIMATION_DYNAMIC_FX_FRAME_ANCHOR_MISMATCH",
       {{"signed_center_x_delta_percent_of_canvas",(sourceCx-officialCx)*100.0},
        {"signed_center_y_delta_percent_of_canvas",(sourceCy-officialCy)*100.0},
        {"limit_percent",ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT},
        {"action","Translate the isolated effect layer with one shared group offset so each phase stays on its official native anchor."}});
     }
   }
   bool activityMismatch=sourceFx.present!=officialFx.present;
   if(activityMismatch){
    fxActivityMismatchCount++;
    fail(key,str(f.job->source.filename()),"ANIMATION_DYNAMIC_FX_ACTIVITY_MISMATCH",
     {{"master_fx_bbox",sourceFx.present?Json::array({sourceFx.x0,sourceFx.y0,sourceFx.x1,sourceFx.y1}):Json(nullptr)},
      {"official_fx_bbox_1x",officialFx.present?Json::array({officialFx.x0,officialFx.y0,officialFx.x1,officialFx.y1}):Json(nullptr)},
      {"action","Restore a measurable frame-specific FX layer; preserve any official phase in which the effect is absent."}});
   }
   fxRows.push_back({{"frame",str(f.job->source.filename())},
    {"master_bbox",sourceFx.present?Json::array({sourceFx.x0,sourceFx.y0,sourceFx.x1,sourceFx.y1}):Json(nullptr)},
    {"projected_master_bbox_3x",sourceFx.present?Json::array({projectedX0,projectedY0,projectedX1,projectedY1}):Json(nullptr)},
    {"master_changed_pixels",sourceFx.changed},{"master_width",sourceFx.present?sourceFx.x1-sourceFx.x0:0},
    {"master_height",sourceFx.present?sourceFx.y1-sourceFx.y0:0},
    {"official_bbox_1x",officialFx.present?Json::array({officialFx.x0,officialFx.y0,officialFx.x1,officialFx.y1}):Json(nullptr)},
    {"official_changed_pixels",officialFx.changed},{"official_width_1x",officialFx.present?officialFx.x1-officialFx.x0:0},
    {"official_height_1x",officialFx.present?officialFx.y1-officialFx.y0:0},
     {"source_to_official_width_ratio",sourceW>0&&officialW>0?sourceW/officialW:0.0},
     {"source_to_official_height_ratio",sourceH>0&&officialH>0?sourceH/officialH:0.0},
    {"width_deviation_percent",widthDeviation},{"height_deviation_percent",heightDeviation},
     {"center_x_delta_percent_of_canvas",centerXDelta},{"center_y_delta_percent_of_canvas",centerYDelta},
     {"signed_center_x_delta_percent_of_canvas",(sourceCx-officialCx)*100.0},
     {"signed_center_y_delta_percent_of_canvas",(sourceCy-officialCy)*100.0}});
  }
  if(officialFxFrames<2)fail(key,"","ANIMATION_OFFICIAL_DYNAMIC_FX_NOT_MEASURABLE",
     {{"measured_official_dynamic_frames",officialFxFrames},{"minimum_frames",2},
      {"action","Use a matching official animated 1X frame group; a static or incomplete reference cannot certify dynamic FX."}});
  if(sourceFxFrames<2)fail(key,"","ANIMATION_MASTER_DYNAMIC_FX_NOT_MEASURABLE",
     {{"measured_master_dynamic_frames",sourceFxFrames},{"minimum_frames",2},
      {"action","Restore the per-frame dynamic layer from the complete official sequence."}});
  auto spread_percent=[](const std::vector<double>& values){
   if(values.size()<2)return 0.0;auto [minimum,maximum]=std::minmax_element(values.begin(),values.end());
   double mean=std::accumulate(values.begin(),values.end(),0.0)/double(values.size());
   return mean>0?(*maximum-*minimum)/mean*100.0:0.0;
  };
  auto mean_value=[](const std::vector<double>& values){return values.empty()?0.0:std::accumulate(values.begin(),values.end(),0.0)/double(values.size());};
  double fxWidthSpread=spread_percent(sourceWidths),fxHeightSpread=spread_percent(sourceHeights);
  double officialFxWidthSpread=spread_percent(officialWidths),officialFxHeightSpread=spread_percent(officialHeights);
  double fxWidthExcess=std::max(0.0,fxWidthSpread-officialFxWidthSpread),fxHeightExcess=std::max(0.0,fxHeightSpread-officialFxHeightSpread);
  double fxCenterXDelta=std::fabs(mean_value(sourceCentersX)-mean_value(officialCentersX))*100.0;
  double fxCenterYDelta=std::fabs(mean_value(sourceCentersY)-mean_value(officialCentersY))*100.0;
  if(fxWidthExcess>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||fxHeightExcess>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT)
   fail(key,"","ANIMATION_DYNAMIC_FX_SCALE_BREATHING",
    {{"master_width_spread_percent",fxWidthSpread},{"official_width_spread_percent",officialFxWidthSpread},{"excess_width_spread_percentage_points",fxWidthExcess},
     {"master_height_spread_percent",fxHeightSpread},{"official_height_spread_percent",officialFxHeightSpread},{"excess_height_spread_percentage_points",fxHeightExcess},
     {"limit_percentage_points",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
     {"action","Separate the static layer and normalize only frame-to-frame scale breathing; retain natural FX variation present in the official sequence."}});
  if(fxCenterXDelta>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||fxCenterYDelta>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT)
   fail(key,"","ANIMATION_DYNAMIC_FX_GROUP_ANCHOR_DRIFT",
    {{"group_mean_center_x_delta_percent_of_canvas",fxCenterXDelta},{"group_mean_center_y_delta_percent_of_canvas",fxCenterYDelta},
     {"limit_percent",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
     {"action","Keep the FX layer registered to the same native source region and preserve its official average location."}});
  Json frameRows=Json::array();
  for(const auto& f:frames){
   auto fname=f.job->source.filename().string();
   frameRows.push_back({{"frame",fname},{"reference",str(f.job->reference)},
    {"source_visible_box",{f.sourceBox.x0,f.sourceBox.y0,f.sourceBox.x1,f.sourceBox.y1}},
    {"projected_visible_box_3x",{f.projectedBox.x0,f.projectedBox.y0,f.projectedBox.x1,f.projectedBox.y1}},
    {"official_visible_box_1x",{f.officialBox.x0,f.officialBox.y0,f.officialBox.x1,f.officialBox.y1}},
    {"normalized_width_ratio",f.ratioW},{"normalized_height_ratio",f.ratioH},
    {"aspect_deviation_percent",f.aspectErrorPercent},{"maximum_size_deviation_percent",f.visibleScalePercent},
    {"uniform_reference_fit_scale",f.fitScale}});
  }
  Json groupResult={{"key",key},{"frames",frameRows},{"frame_scale_spread_percent",scaleSpread}};
  groupResult["static_base"]={{"comparison",nativeBaseEnvelope?"source_vs_official_base_fraction_on_vcmi_composites":baseOverlay?"frame0_base_vs_later_composited_scenes_exact_rgba":"exact_rgba_common_pixels"},
      {"stable_pixel_fraction",stableFraction},{"stable_pixels",stablePixels},{"minimum_visible_pixels",minVisible},
      {"official_stable_pixel_fraction",officialStableFraction},{"official_stable_pixels",officialStablePixels},
      {"official_minimum_visible_pixels",officialMinVisible},{"native_stability_deficit_percentage_points",nativeStableDeficitPoints},
      {"native_stability_deficit_limit_percentage_points",nativeBaseEnvelope?ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS:0.0},
      {"stable_bbox",x1>x0?Json::array({x0,y0,x1,y1}):Json(nullptr)},
      {"official_stable_bbox",officialX1>officialX0?Json::array({officialX0,officialY0,officialX1,officialY1}):Json(nullptr)}};
  groupResult["dynamic_fx"]={{"measurement",baseOverlay?"overlay_only_frames_1_plus_vs_official_overlay_sequence":"group_motion_envelope_vs_official_sequence"},
      {"registration_space","shared_group_transform_3x_normalized_to_native_canvas"},
      {"frames",fxRows},{"measured_master_frame_count",sourceFxFrames},{"measured_official_frame_count",officialFxFrames},
      {"master_width_spread_percent",fxWidthSpread},{"official_width_spread_percent",officialFxWidthSpread},
      {"master_height_spread_percent",fxHeightSpread},{"official_height_spread_percent",officialFxHeightSpread},
      {"excess_width_spread_percentage_points",fxWidthExcess},{"excess_height_spread_percentage_points",fxHeightExcess},
      {"mean_center_x_delta_percent_of_canvas",fxCenterXDelta},{"mean_center_y_delta_percent_of_canvas",fxCenterYDelta},
      {"excess_spread_limit_percentage_points",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
      {"mean_center_limit_percent",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
       {"max_frame_size_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT:0.0},
       {"max_frame_anchor_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT:0.0},
      {"status",(sourceFxFrames<2||officialFxFrames<2||fxActivityMismatchCount>0||
        fxWidthExcess>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||fxHeightExcess>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||
         fxCenterXDelta>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||fxCenterYDelta>ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT||
         fxSizeMismatchCount>0||fxAnchorMismatchCount>0)?"BLOCKED":"PASS"}};
  result["groups"].push_back(std::move(groupResult));
 }
 if(result["groups"].empty())fail("","","ANIMATION_SCALE_NO_MEASURED_GROUPS",
     {{"valid_frames",jobs.size()},{"requested_frames",requested},{"action","A contract with no measured frame group is never a pass."}});
 return result;
}
inline Json static_animation_authored_roi_audit(const std::vector<ExpansionJob>& jobs,std::size_t requested,int preblocked,const Json& batch){
 Json result={{"contract",ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1},{"gate_version",1},{"status","PASS"},
  {"limits",{{"max_visible_size_deviation_percent",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT},
              {"max_visible_aspect_deviation_percent",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},
              {"max_native_stability_deficit_percentage_points",ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS},
              {"max_dynamic_roi_size_spread_percent",ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT},
              {"max_dynamic_roi_anchor_spread_percent",ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT},
              {"max_dynamic_roi_luma_spread_percent",ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT}}},
  {"input_frame_count",requested},{"preflight_blocked_items",preblocked},{"groups",Json::array()},{"failures",Json::array()}};
 auto fail=[&](const std::string& group,const std::string& frame,const std::string& reason,const Json& diagnostic=Json::object()){
  result["status"]="BLOCKED";result["failures"].push_back({{"group",group},{"frame",frame},{"reason",reason},{"diagnostic",diagnostic}});
 };
 if(jobs.empty()||jobs.size()!=requested||preblocked>0){
  fail("","","ANIMATION_AUTHORED_ROI_INPUTS_INCOMPLETE",{{"valid_frames",jobs.size()},{"requested_frames",requested},{"preflight_blocked_items",preblocked}});
  return result;
 }
 need(batch.contains("animation_gate")&&batch.at("animation_gate").contains("authored_effect_roi"),
      "ANIMATION_AUTHORED_ROI_POLICY_MISSING");
 const auto& roiRow=batch.at("animation_gate").at("authored_effect_roi");
 need(roiRow.is_object()&&roiRow.contains("path")&&roiRow.contains("sha256")&&roiRow.contains("pixels")&&roiRow.contains("alpha_threshold"),
      "ANIMATION_AUTHORED_ROI_POLICY_INVALID");
 auto roiPath=clean_path(path(roiRow.at("path").get<std::string>()));
 need(fs::exists(roiPath),"ANIMATION_AUTHORED_ROI_NOT_FOUND: "+str(roiPath));
 auto roiRaw=read_all(roiPath);need(sha(roiRaw)==roiRow.at("sha256").get<std::string>(),"ANIMATION_AUTHORED_ROI_DRIFT: "+str(roiPath));
 Wic decoder;auto roiMeta=decoder.frame(roiRaw);int roiW=roiMeta.w,roiH=roiMeta.h;
 need(roiRow.at("pixels")==Json::array({roiW,roiH}),"ANIMATION_AUTHORED_ROI_DIMENSION_DRIFT");
 auto roiRgba=decoder.decode_rgba(roiRaw,roiW,roiH);auto threshold=integer(roiRow.at("alpha_threshold"),1,255,"authored ROI threshold");
 std::vector<std::uint8_t> roi(std::size_t(roiW)*roiH,0);std::uint64_t roiPixels=0;int rx0=roiW,ry0=roiH,rx1=-1,ry1=-1;
 for(int y=0;y<roiH;y++)for(int x=0;x<roiW;x++){
  auto p=std::size_t(y)*roiW+x,at=p*4;auto luma=(54u*roiRgba[at]+183u*roiRgba[at+1]+19u*roiRgba[at+2])/256u;
  if(roiRgba[at+3]&&luma>=unsigned(threshold)){roi[p]=1;roiPixels++;rx0=std::min(rx0,x);ry0=std::min(ry0,y);rx1=std::max(rx1,x+1);ry1=std::max(ry1,y+1);}
 }
 need(roiPixels>0&&rx1>rx0&&ry1>ry0,"ANIMATION_AUTHORED_ROI_EMPTY");
 std::map<std::wstring,std::vector<const ExpansionJob*>> groups;
 for(const auto& job:jobs){auto parsed=animation_group_frame(job.source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(job.source));auto key=animation_folded_text(job.source.parent_path().filename().wstring()+L"|"+parsed->first);groups[key].push_back(&job);}
 need(groups.size()==1,"ANIMATION_AUTHORED_ROI_REQUIRES_ONE_GROUP_PER_PLAN");
 Wic imageDecoder;std::size_t totalOverlayFrames=0;
 for(auto& [wideKey,groupJobs]:groups){
  auto key=utf8(wideKey);std::sort(groupJobs.begin(),groupJobs.end(),[](const ExpansionJob* a,const ExpansionJob* b){return animation_group_frame(a->source)->second<animation_group_frame(b->source)->second;});
  need(groupJobs.size()>=2,"ANIMATION_SCALE_GROUP_NEEDS_AT_LEAST_2_FRAMES: "+key);
  need(animation_group_frame(groupJobs.front()->source)->second==L"0","ANIMATION_BASE_OVERLAY_FRAME0_REQUIRED: "+key);
  const int canvasW=groupJobs.front()->sourceW,canvasH=groupJobs.front()->sourceH;
  const int officialW=groupJobs.front()->referenceW,officialH=groupJobs.front()->referenceH;
  need(roiW==canvasW&&roiH==canvasH,"ANIMATION_AUTHORED_ROI_CANVAS_MISMATCH: "+key);
  for(const auto* job:groupJobs)need(job->sourceW==canvasW&&job->sourceH==canvasH&&job->referenceW==officialW&&job->referenceH==officialH,
       "ANIMATION_AUTHORED_ROI_SEQUENCE_CANVAS_DRIFT: "+str(job->source));
  const auto* first=groupJobs.front();auto firstRaw=read_all(first->source),firstOfficialRaw=read_all(first->reference);
  need(sha(firstRaw)==first->sourceSha&&sha(firstOfficialRaw)==first->referenceSha,"ANIMATION_AUTHORED_ROI_BASE_SOURCE_DRIFT");
  auto sourceBase=imageDecoder.decode_rgba(firstRaw,canvasW,canvasH),officialBase=imageDecoder.decode_rgba(firstOfficialRaw,officialW,officialH);
  auto sourcePixels=std::size_t(canvasW)*canvasH,officialPixels=std::size_t(officialW)*officialH;
  std::vector<std::uint8_t> stable(sourcePixels,1),officialStable(officialPixels,1);
  std::uint64_t minVisible=std::numeric_limits<std::uint64_t>::max(),officialMinVisible=std::numeric_limits<std::uint64_t>::max();
  std::vector<double> overlayAreas,lumas,centersX,centersY,widths,heights;Json frameRows=Json::array();int outsideRoiChanges=0,activeFrameCount=0;
  for(std::size_t fi=0;fi<groupJobs.size();fi++){
   const auto* job=groupJobs[fi];auto raw=read_all(job->source),refRaw=read_all(job->reference);
   need(sha(raw)==job->sourceSha,"ANIMATION_AUTHORED_ROI_SOURCE_DRIFT: "+str(job->source));
   need(sha(refRaw)==job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(job->reference));
   auto source=imageDecoder.decode_rgba(raw,canvasW,canvasH),official=imageDecoder.decode_rgba(refRaw,officialW,officialH);
   auto sourceScene=fi==0?source:animation_base_then_overlay_rgba(sourceBase,source);
   auto officialScene=fi==0?official:animation_base_then_overlay_rgba(officialBase,official);
   ArtworkBox sourceBox=animation_rgba_artwork_box(sourceScene,canvasW,canvasH);
   ArtworkBox officialBox=animation_rgba_artwork_box(officialScene,officialW,officialH);
   need(!sourceBox.empty&&!officialBox.empty,"ANIMATION_AUTHORED_ROI_EMPTY_COMPOSITE: "+str(job->source));
   need(job->animationGroupTransform.is_object(),"ANIMATION_GROUP_TRANSFORM_MISSING: "+str(job->source));
   const auto& transform=job->animationGroupTransform;const auto& unionBox=transform.at("source_union_box");const auto& offset=transform.at("offset_pixels");
   double groupScale=transform.at("scale").get<double>();int sx0=unionBox[0].get<int>(),sy0=unionBox[1].get<int>(),ox=offset[0].get<int>(),oy=offset[1].get<int>();
   int px0=int(std::lround((sourceBox.x0-sx0)*groupScale))+ox,py0=int(std::lround((sourceBox.y0-sy0)*groupScale))+oy;
   int px1=int(std::lround((sourceBox.x1-sx0)*groupScale))+ox,py1=int(std::lround((sourceBox.y1-sy0)*groupScale))+oy;
   double ratioW=double(px1-px0)/(officialBox.x1-officialBox.x0)/job->referenceScale;
   double ratioH=double(py1-py0)/(officialBox.y1-officialBox.y0)/job->referenceScale;
   double aspectError=std::fabs(ratioW/ratioH-1.0)*100.0;
   double sizeError=std::max(std::fabs(ratioW-1.0),std::fabs(ratioH-1.0))*100.0;
   if(aspectError>ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT||sizeError>ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT)
    fail(key,str(job->source.filename()),"ANIMATION_AUTHORED_ROI_COMPOSITE_GEOMETRY_MISMATCH",
     {{"width_ratio",ratioW},{"height_ratio",ratioH},{"aspect_deviation_percent",aspectError},{"size_deviation_percent",sizeError},
      {"limits_percent",{{"aspect",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},{"size",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT}}}});
    std::uint64_t sourceVisible=0,officialVisible=0,changed=0,overlayArea=0;double overlayLumaSum=0;
    int cx0=canvasW,cy0=canvasH,cx1=-1,cy1=-1;
    auto overlayRaw=fi?imageDecoder.decode_rgba(raw,canvasW,canvasH):std::vector<std::uint8_t>{};
   for(std::size_t p=0;p<sourcePixels;p++){
    auto at=p*4;bool changedPixel=sourceScene[at]!=sourceBase[at]||sourceScene[at+1]!=sourceBase[at+1]||sourceScene[at+2]!=sourceBase[at+2]||sourceScene[at+3]!=sourceBase[at+3];
    if(sourceScene[at+3]>=16)sourceVisible++;
    if(fi&&changedPixel){if(!roi[p])outsideRoiChanges++;else changed++;}
    if(fi&&roi[p]&&overlayRaw[at+3]>=16){
     overlayArea++;auto x=int(p%canvasW),y=int(p/canvasW);cx0=std::min(cx0,x);cy0=std::min(cy0,y);cx1=std::max(cx1,x+1);cy1=std::max(cy1,y+1);
     overlayLumaSum+=(54.0*overlayRaw[at]+183.0*overlayRaw[at+1]+19.0*overlayRaw[at+2])/256.0;
    }
    if(fi&&stable[p]&&changedPixel)stable[p]=0;
   }
   for(std::size_t p=0;p<officialPixels;p++){
    auto at=p*4;bool changedPixel=officialScene[at]!=officialBase[at]||officialScene[at+1]!=officialBase[at+1]||officialScene[at+2]!=officialBase[at+2]||officialScene[at+3]!=officialBase[at+3];
    if(officialScene[at+3]>=16)officialVisible++;
    if(fi&&officialStable[p]&&changedPixel)officialStable[p]=0;
   }
   minVisible=std::min(minVisible,sourceVisible);officialMinVisible=std::min(officialMinVisible,officialVisible);
    if(fi){
     totalOverlayFrames++;
     if(changed==0)fail(key,str(job->source.filename()),"ANIMATION_AUTHORED_FX_FRAME_INACTIVE",{{"roi_pixels",roiPixels}});else activeFrameCount++;
     if(overlayArea==0)fail(key,str(job->source.filename()),"ANIMATION_AUTHORED_FX_LAYER_EMPTY");
     overlayAreas.push_back(double(overlayArea));lumas.push_back(overlayArea?overlayLumaSum/double(overlayArea):0.0);
     if(cx1>cx0&&cy1>cy0){widths.push_back(double(cx1-cx0));heights.push_back(double(cy1-cy0));centersX.push_back((cx0+cx1)/2.0/canvasW);centersY.push_back((cy0+cy1)/2.0/canvasH);}
    }
    frameRows.push_back({{"frame",str(job->source.filename())},
     {"composite_source_box",Json::array({sourceBox.x0,sourceBox.y0,sourceBox.x1,sourceBox.y1})},
     {"projected_source_box_3x",Json::array({px0,py0,px1,py1})},
     {"official_composite_box_1x",Json::array({officialBox.x0,officialBox.y0,officialBox.x1,officialBox.y1})},
     {"normalized_width_ratio",ratioW},{"normalized_height_ratio",ratioH},
     {"changed_pixels_inside_roi",changed},{"authored_roi_alpha_pixels",overlayArea},
     {"visible_roi_bbox",overlayArea&&cx1>cx0?Json::array({cx0,cy0,cx1,cy1}):Json(nullptr)}});
  }
  auto fraction=[&](std::uint64_t stablePixels,std::uint64_t visible){return visible?double(stablePixels)/visible:0.0;};
  std::uint64_t stablePixels=0,officialStablePixels=0;for(std::size_t p=0;p<sourcePixels;p++)if(stable[p]&&sourceBase[p*4+3]>=16)stablePixels++;
  for(std::size_t p=0;p<officialPixels;p++)if(officialStable[p]&&officialBase[p*4+3]>=16)officialStablePixels++;
  double sourceStable=fraction(stablePixels,minVisible),officialStableFraction=fraction(officialStablePixels,officialMinVisible);
  double stableDeficit=std::max(0.0,officialStableFraction-sourceStable)*100.0;
  if(stableDeficit>ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS)
   fail(key,"","ANIMATION_AUTHORED_BASE_FLICKER_EXCEEDS_OFFICIAL",
     {{"source_stable_fraction",sourceStable},{"official_stable_fraction",officialStableFraction},{"deficit_percentage_points",stableDeficit},
     {"limit_percentage_points",ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS}});
  auto spread=[](const std::vector<double>& v){if(v.size()<2)return 0.0;auto [lo,hi]=std::minmax_element(v.begin(),v.end());double avg=std::accumulate(v.begin(),v.end(),0.0)/v.size();return avg>0?(*hi-*lo)/avg*100.0:0.0;};
  double coverageSpread=spread(overlayAreas),lumaSpread=spread(lumas),widthSpread=spread(widths),heightSpread=spread(heights);
  auto centerSpread=[](const std::vector<double>& v){if(v.size()<2)return 0.0;auto [lo,hi]=std::minmax_element(v.begin(),v.end());return (*hi-*lo)*100.0;};
  double xSpread=centerSpread(centersX),ySpread=centerSpread(centersY);
  if(activeFrameCount<2)fail(key,"","ANIMATION_AUTHORED_FX_NOT_MEASURABLE",{{"active_overlay_frames",activeFrameCount},{"minimum",2}});
  if(lumaSpread>ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_AUTHORED_FX_LUMA_PULSE",{{"luma_spread_percent",lumaSpread},{"limit_percent",ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT}});
  if(coverageSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT||widthSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT||heightSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_AUTHORED_FX_SIZE_BREATHING",{{"coverage_spread_percent",coverageSpread},{"width_spread_percent",widthSpread},{"height_spread_percent",heightSpread},{"limit_percent",ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT}});
  if(xSpread>ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT||ySpread>ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_AUTHORED_FX_ANCHOR_DRIFT",{{"center_x_spread_percent",xSpread},{"center_y_spread_percent",ySpread},{"limit_percent",ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT}});
  if(outsideRoiChanges>0)fail(key,"","ANIMATION_AUTHORED_FX_OUTSIDE_ROI",{{"outside_roi_changed_pixels",outsideRoiChanges}});
  result["groups"].push_back({{"key",key},{"frame_count",groupJobs.size()},{"authored_effect_roi",{{"path",str(roiPath)},{"sha256",roiRow.at("sha256")},{"pixels",{roiW,roiH}},{"bbox",{rx0,ry0,rx1,ry1}},{"active_pixels",roiPixels}}},
   {"static_base",{{"source_stable_fraction",sourceStable},{"official_stable_fraction",officialStableFraction},{"stability_deficit_percentage_points",stableDeficit},{"outside_roi_changes",outsideRoiChanges},{"minimum_visible_source_pixels",minVisible},{"minimum_visible_official_pixels",officialMinVisible}}},
   {"dynamic_fx",{{"active_frames",activeFrameCount},{"coverage_spread_percent",coverageSpread},{"luma_spread_percent",lumaSpread},{"width_spread_percent",widthSpread},{"height_spread_percent",heightSpread},{"center_x_spread_percent",xSpread},{"center_y_spread_percent",ySpread},{"status",result["status"]=="PASS"?"PASS":"BLOCKED"}}},
   {"frames",frameRows}});
 }
 if(totalOverlayFrames<2)fail("","","ANIMATION_AUTHORED_FX_GROUP_NOT_MEASURED",{{"overlay_frames",totalOverlayFrames}});
 result["official_frame_inventory"]=batch.at("animation_gate").at("official_frame_inventory");
 return result;
}
inline Json static_animation_authored_roi_v2_audit(const std::vector<ExpansionJob>& jobs,std::size_t requested,int preblocked,const Json& batch,bool baseOverlay){
 const std::string contract=batch.at("animation_contract").get<std::string>();
 const int gateVersion=(baseOverlay||contract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2)?2:1;
 Json result={{"contract",contract},{"gate_version",gateVersion},{"status","PASS"},
  {"limits",{{"max_visible_size_deviation_percent",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT},
              {"max_visible_aspect_deviation_percent",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},
              {"max_native_stability_deficit_percentage_points",ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS},
              {"max_dynamic_roi_size_spread_percent",ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT},
              {"max_dynamic_roi_anchor_spread_percent",ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT},
              {"max_dynamic_roi_luma_spread_percent",ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT},
              {"max_authored_roi_fraction",ANIMATION_AUTHORED_FX_MAX_ROI_FRACTION},
              {"min_measurable_structure_fraction",ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION}}},
  {"input_frame_count",requested},{"preflight_blocked_items",preblocked},{"groups",Json::array()},{"failures",Json::array()}};
 auto fail=[&](const std::string& group,const std::string& frame,const std::string& reason,const Json& diagnostic=Json::object()){
  result["status"]="BLOCKED";result["failures"].push_back({{"group",group},{"frame",frame},{"reason",reason},{"diagnostic",diagnostic}});
 };
 if(jobs.empty()||jobs.size()!=requested||preblocked>0){
  fail("","","ANIMATION_AUTHORED_ROI_INPUTS_INCOMPLETE",{{"valid_frames",jobs.size()},{"requested_frames",requested},{"preflight_blocked_items",preblocked}});
  return result;
 }
 need(batch.contains("animation_gate")&&batch.at("animation_gate").contains("authored_effect_roi"),"ANIMATION_AUTHORED_ROI_POLICY_MISSING");
 const auto& roiRow=batch.at("animation_gate").at("authored_effect_roi");
 need(roiRow.is_object()&&roiRow.contains("path")&&roiRow.contains("sha256")&&roiRow.contains("pixels")&&roiRow.contains("alpha_threshold"),"ANIMATION_AUTHORED_ROI_POLICY_INVALID");
 auto roiPath=clean_path(path(roiRow.at("path").get<std::string>()));need(fs::exists(roiPath),"ANIMATION_AUTHORED_ROI_NOT_FOUND: "+str(roiPath));
 auto roiRaw=read_all(roiPath);need(sha(roiRaw)==roiRow.at("sha256").get<std::string>(),"ANIMATION_AUTHORED_ROI_DRIFT: "+str(roiPath));
 Wic decoder;auto roiMeta=decoder.frame(roiRaw);int roiW=roiMeta.w,roiH=roiMeta.h;
 need(roiRow.at("pixels")==Json::array({roiW,roiH}),"ANIMATION_AUTHORED_ROI_DIMENSION_DRIFT");
 auto roiRgba=decoder.decode_rgba(roiRaw,roiW,roiH);auto threshold=integer(roiRow.at("alpha_threshold"),1,255,"authored ROI threshold");
 std::vector<std::uint8_t> sourceRoi(std::size_t(roiW)*roiH,0);std::uint64_t roiPixels=0;int rx0=roiW,ry0=roiH,rx1=-1,ry1=-1;
 for(int y=0;y<roiH;y++)for(int x=0;x<roiW;x++){
  auto p=std::size_t(y)*roiW+x,at=p*4;auto luma=(54u*roiRgba[at]+183u*roiRgba[at+1]+19u*roiRgba[at+2])/256u;
  if(roiRgba[at+3]&&luma>=unsigned(threshold)){sourceRoi[p]=1;roiPixels++;rx0=std::min(rx0,x);ry0=std::min(ry0,y);rx1=std::max(rx1,x+1);ry1=std::max(ry1,y+1);}
 }
 need(roiPixels>0&&rx1>rx0&&ry1>ry0,"ANIMATION_AUTHORED_ROI_EMPTY");
 if(double(roiPixels)/double(roiW*roiH)>ANIMATION_AUTHORED_FX_MAX_ROI_FRACTION)
  fail("","","ANIMATION_AUTHORED_ROI_TOO_LARGE",{{"positive_pixels",roiPixels},{"canvas_pixels",roiW*roiH},{"fraction",double(roiPixels)/double(roiW*roiH)},{"limit_fraction",ANIMATION_AUTHORED_FX_MAX_ROI_FRACTION}});
 std::map<std::wstring,std::vector<const ExpansionJob*>> groups;
 for(const auto& job:jobs){auto parsed=animation_group_frame(job.source);need(parsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(job.source));auto key=animation_folded_text(job.source.parent_path().filename().wstring()+L"|"+parsed->first);groups[key].push_back(&job);}
 need(groups.size()==1,"ANIMATION_AUTHORED_ROI_REQUIRES_ONE_GROUP_PER_PLAN");
 Wic imageDecoder;std::size_t measuredFrames=0;
 for(auto& [wideKey,groupJobs]:groups){
  auto key=utf8(wideKey);std::sort(groupJobs.begin(),groupJobs.end(),[](const ExpansionJob* a,const ExpansionJob* b){return animation_group_frame(a->source)->second<animation_group_frame(b->source)->second;});
  need(groupJobs.size()>=2,"ANIMATION_SCALE_GROUP_NEEDS_AT_LEAST_2_FRAMES: "+key);
  need(animation_group_frame(groupJobs.front()->source)->second==L"0","ANIMATION_BASE_FRAME0_REQUIRED: "+key);
  const int canvasW=groupJobs.front()->sourceW,canvasH=groupJobs.front()->sourceH;
  const int officialW=groupJobs.front()->referenceW,officialH=groupJobs.front()->referenceH;
  need(roiW==canvasW&&roiH==canvasH,"ANIMATION_AUTHORED_ROI_CANVAS_MISMATCH: "+key);
  for(const auto* job:groupJobs)need(job->sourceW==canvasW&&job->sourceH==canvasH&&job->referenceW==officialW&&job->referenceH==officialH,"ANIMATION_AUTHORED_ROI_SEQUENCE_CANVAS_DRIFT: "+str(job->source));
  const auto* first=groupJobs.front();auto firstRaw=read_all(first->source),firstOfficialRaw=read_all(first->reference);
  need(sha(firstRaw)==first->sourceSha&&sha(firstOfficialRaw)==first->referenceSha,"ANIMATION_AUTHORED_ROI_BASE_SOURCE_DRIFT");
  auto sourceBase=imageDecoder.decode_rgba(firstRaw,canvasW,canvasH),officialBase=imageDecoder.decode_rgba(firstOfficialRaw,officialW,officialH);
  const std::size_t sourcePixels=std::size_t(canvasW)*canvasH,officialPixels=std::size_t(officialW)*officialH;
  need(groupJobs.front()->animationGroupTransform.is_object(),"ANIMATION_GROUP_TRANSFORM_MISSING: "+key);
  const auto& transform=groupJobs.front()->animationGroupTransform;const auto& unionBox=transform.at("source_union_box");const auto& offset=transform.at("offset_pixels");
  double groupScale=transform.at("scale").get<double>();int sx0=unionBox[0].get<int>(),sy0=unionBox[1].get<int>(),ox=offset[0].get<int>(),oy=offset[1].get<int>();
  int targetScale=int(std::lround(groupJobs.front()->referenceScale));need(targetScale==3,"ANIMATION_AUTHORED_ROI_TARGET_SCALE_MUST_BE_3X");
  auto sourceToNative=[&](int x,int y,int& nx,int& ny){
   int x3=int(std::lround((x-sx0)*groupScale))+ox,y3=int(std::lround((y-sy0)*groupScale))+oy;
   if(x3<0||y3<0||x3>=officialW*targetScale||y3>=officialH*targetScale)return false;
   nx=std::clamp(x3/targetScale,0,officialW-1);ny=std::clamp(y3/targetScale,0,officialH-1);return true;
  };
  std::vector<std::uint8_t> officialRoi(officialPixels,0),projectedSourceRoi(officialPixels,0);
  for(std::size_t p=0;p<sourcePixels;p++)if(sourceRoi[p]){int x=int(p%canvasW),y=int(p/canvasW),nx=0,ny=0;if(sourceToNative(x,y,nx,ny))projectedSourceRoi[std::size_t(ny)*officialW+nx]=1;}
  for(std::size_t fi=1;fi<groupJobs.size();fi++){
   const auto* job=groupJobs[fi];auto raw=read_all(job->reference);need(sha(raw)==job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(job->reference));auto official=imageDecoder.decode_rgba(raw,officialW,officialH);
   for(std::size_t p=0;p<officialPixels;p++){
    auto at=p*4;bool effectPixel=baseOverlay?official[at+3]>=16:(official[at]!=officialBase[at]||official[at+1]!=officialBase[at+1]||official[at+2]!=officialBase[at+2]||official[at+3]!=officialBase[at+3]);
    if(effectPixel)officialRoi[p]=1;
   }
  }
  for(std::size_t p=0;p<officialPixels;p++)if(projectedSourceRoi[p])officialRoi[p]=1;
  std::uint64_t officialRoiPixels=0;int orx0=officialW,ory0=officialH,orx1=-1,ory1=-1;
  for(std::size_t p=0;p<officialPixels;p++)if(officialRoi[p]){officialRoiPixels++;int x=int(p%officialW),y=int(p/officialW);orx0=std::min(orx0,x);ory0=std::min(ory0,y);orx1=std::max(orx1,x+1);ory1=std::max(ory1,y+1);}
  std::vector<std::uint8_t> sourceStructureRoi=sourceRoi,officialStructureRoi=officialRoi;
  for(std::size_t p=0;p<sourcePixels;p++){
   int x=int(p%canvasW),y=int(p/canvasW),nx=0,ny=0;if(sourceToNative(x,y,nx,ny)&&officialRoi[std::size_t(ny)*officialW+nx])sourceStructureRoi[p]=1;
  }
  for(std::size_t p=0;p<sourcePixels;p++)if(sourceRoi[p]){int x=int(p%canvasW),y=int(p/canvasW),nx=0,ny=0;if(sourceToNative(x,y,nx,ny))officialStructureRoi[std::size_t(ny)*officialW+nx]=1;}
  auto clearRoi=[](std::vector<std::uint8_t>& rgba,const std::vector<std::uint8_t>& mask){for(std::size_t p=0;p<mask.size();p++)if(mask[p])rgba[p*4+3]=0;};
  std::vector<std::uint8_t> stable(sourcePixels,1),officialStable(officialPixels,1);std::uint64_t minVisible=std::numeric_limits<std::uint64_t>::max(),officialMinVisible=std::numeric_limits<std::uint64_t>::max();
  std::vector<double> overlayAreas,lumas,centersX,centersY,widths,heights;Json frameRows=Json::array();int outsideRoiChanges=0,activeFrameCount=0;std::size_t measuredOverlayFrames=0;
  auto differs=[](const std::vector<std::uint8_t>& a,const std::vector<std::uint8_t>& b,std::size_t at){return a[at]!=b[at]||a[at+1]!=b[at+1]||a[at+2]!=b[at+2]||a[at+3]!=b[at+3];};
  for(std::size_t fi=0;fi<groupJobs.size();fi++){
   const auto* job=groupJobs[fi];auto raw=read_all(job->source),refRaw=read_all(job->reference);need(sha(raw)==job->sourceSha,"ANIMATION_AUTHORED_ROI_SOURCE_DRIFT: "+str(job->source));need(sha(refRaw)==job->referenceSha,"ANIMATION_OFFICIAL_FRAME_DRIFT: "+str(job->reference));
   auto source=imageDecoder.decode_rgba(raw,canvasW,canvasH),official=imageDecoder.decode_rgba(refRaw,officialW,officialH);
   auto sourceScene=baseOverlay?(fi==0?source:animation_base_then_overlay_rgba(sourceBase,source)):source;
   auto officialScene=baseOverlay?(fi==0?official:animation_base_then_overlay_rgba(officialBase,official)):official;
   auto sourceGeometry=sourceScene,officialGeometry=officialScene;
   clearRoi(sourceGeometry,sourceStructureRoi);clearRoi(officialGeometry,officialStructureRoi);
   ArtworkBox sourceBox=animation_rgba_artwork_box(sourceGeometry,canvasW,canvasH),officialBox=animation_rgba_artwork_box(officialGeometry,officialW,officialH);
   need(!sourceBox.empty&&!officialBox.empty,"ANIMATION_AUTHORED_ROI_STRUCTURE_EMPTY: "+str(job->source));
   const auto& currentTransform=job->animationGroupTransform;const auto& currentUnion=currentTransform.at("source_union_box");const auto& currentOffset=currentTransform.at("offset_pixels");
   double currentScale=currentTransform.at("scale").get<double>();int csx0=currentUnion[0].get<int>(),csy0=currentUnion[1].get<int>(),cox=currentOffset[0].get<int>(),coy=currentOffset[1].get<int>();
   int px0=int(std::lround((sourceBox.x0-csx0)*currentScale))+cox,py0=int(std::lround((sourceBox.y0-csy0)*currentScale))+coy;
   int px1=int(std::lround((sourceBox.x1-csx0)*currentScale))+cox,py1=int(std::lround((sourceBox.y1-csy0)*currentScale))+coy;
   double ratioW=double(px1-px0)/(officialBox.x1-officialBox.x0)/job->referenceScale,ratioH=double(py1-py0)/(officialBox.y1-officialBox.y0)/job->referenceScale;
   double aspectError=std::fabs(ratioW/ratioH-1.0)*100.0,sizeError=std::max(std::fabs(ratioW-1.0),std::fabs(ratioH-1.0))*100.0;
   if(aspectError>ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT||sizeError>ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT)
    fail(key,str(job->source.filename()),"ANIMATION_AUTHORED_ROI_STRUCTURE_GEOMETRY_MISMATCH",{{"width_ratio",ratioW},{"height_ratio",ratioH},{"aspect_deviation_percent",aspectError},{"size_deviation_percent",sizeError},{"limits_percent",{{"aspect",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},{"size",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT}}}});
   std::uint64_t sourceVisible=0,officialVisible=0,changed=0,overlayArea=0;double overlayLumaSum=0;int cx0=canvasW,cy0=canvasH,cx1=-1,cy1=-1;
   const auto& effectFrame=source;
   for(std::size_t p=0;p<sourcePixels;p++){
    auto at=p*4;bool changedPixel=differs(sourceScene,sourceBase,at);
    if(sourceGeometry[at+3]>=16)sourceVisible++;if(sourceRoi[p]&&changedPixel)changed++;
    if(changedPixel&&!sourceRoi[p])outsideRoiChanges++;
    if(stable[p]&&changedPixel)stable[p]=0;
    bool measureEffect=sourceRoi[p]&&effectFrame[at+3]>=16&&(baseOverlay?fi>0:(fi>0&&changedPixel));
    if(measureEffect){overlayArea++;int x=int(p%canvasW),y=int(p/canvasW);cx0=std::min(cx0,x);cy0=std::min(cy0,y);cx1=std::max(cx1,x+1);cy1=std::max(cy1,y+1);overlayLumaSum+=(54.0*effectFrame[at]+183.0*effectFrame[at+1]+19.0*effectFrame[at+2])/256.0;}
   }
   for(std::size_t p=0;p<officialPixels;p++){
    auto at=p*4;bool changedPixel=differs(officialScene,officialBase,at);if(officialGeometry[at+3]>=16)officialVisible++;if(officialStable[p]&&changedPixel)officialStable[p]=0;
   }
   minVisible=std::min(minVisible,sourceVisible);officialMinVisible=std::min(officialMinVisible,officialVisible);
   bool measureFrame=fi>0;
   if(measureFrame){measuredOverlayFrames++;if(overlayArea==0&&baseOverlay)fail(key,str(job->source.filename()),"ANIMATION_AUTHORED_FX_LAYER_EMPTY");if(overlayArea>0){
     activeFrameCount++;overlayAreas.push_back(double(overlayArea));lumas.push_back(overlayLumaSum/double(overlayArea));
     if(cx1>cx0&&cy1>cy0){widths.push_back(double(cx1-cx0));heights.push_back(double(cy1-cy0));centersX.push_back((cx0+cx1)/2.0/canvasW);centersY.push_back((cy0+cy1)/2.0/canvasH);}
   }}
   frameRows.push_back({{"frame",str(job->source.filename())},{"source_structure_box",Json::array({sourceBox.x0,sourceBox.y0,sourceBox.x1,sourceBox.y1})},{"official_structure_box",Json::array({officialBox.x0,officialBox.y0,officialBox.x1,officialBox.y1})},{"projected_source_structure_box_3x",Json::array({px0,py0,px1,py1})},{"normalized_width_ratio",ratioW},{"normalized_height_ratio",ratioH},{"changed_pixels_inside_roi",changed},{"authored_roi_alpha_pixels",overlayArea},{"visible_roi_bbox",overlayArea&&cx1>cx0?Json::array({cx0,cy0,cx1,cy1}):Json(nullptr)}});
  }
  auto fraction=[](std::uint64_t stablePixels,std::uint64_t visible){return visible?double(stablePixels)/visible:0.0;};
  std::uint64_t stablePixels=0,officialStablePixels=0;for(std::size_t p=0;p<sourcePixels;p++)if(!sourceStructureRoi[p]&&stable[p]&&sourceBase[p*4+3]>=16)stablePixels++;
  for(std::size_t p=0;p<officialPixels;p++)if(!officialStructureRoi[p]&&officialStable[p]&&officialBase[p*4+3]>=16)officialStablePixels++;
  double sourceStable=fraction(stablePixels,minVisible),officialStableFraction=fraction(officialStablePixels,officialMinVisible),stableDeficit=std::max(0.0,officialStableFraction-sourceStable)*100.0;
  if(stableDeficit>ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS)fail(key,"","ANIMATION_AUTHORED_BASE_FLICKER_EXCEEDS_OFFICIAL",{{"source_stable_fraction",sourceStable},{"official_stable_fraction",officialStableFraction},{"deficit_percentage_points",stableDeficit},{"limit_percentage_points",ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS}});
  auto spread=[](const std::vector<double>& v){if(v.size()<2)return 0.0;auto [lo,hi]=std::minmax_element(v.begin(),v.end());double avg=std::accumulate(v.begin(),v.end(),0.0)/v.size();return avg>0?(*hi-*lo)/avg*100.0:0.0;};
  auto centerSpread=[](const std::vector<double>& v){if(v.size()<2)return 0.0;auto [lo,hi]=std::minmax_element(v.begin(),v.end());return (*hi-*lo)*100.0;};
  double coverageSpread=spread(overlayAreas),lumaSpread=spread(lumas),widthSpread=spread(widths),heightSpread=spread(heights),xSpread=centerSpread(centersX),ySpread=centerSpread(centersY);
  if(activeFrameCount<2)fail(key,"","ANIMATION_AUTHORED_FX_NOT_MEASURABLE",{{"active_effect_frames",activeFrameCount},{"minimum",2}});
  if(lumaSpread>ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT)fail(key,"","ANIMATION_AUTHORED_FX_LUMA_PULSE",{{"luma_spread_percent",lumaSpread},{"limit_percent",ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT}});
  if(coverageSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT||widthSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT||heightSpread>ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_AUTHORED_FX_SIZE_BREATHING",{{"coverage_spread_percent",coverageSpread},{"width_spread_percent",widthSpread},{"height_spread_percent",heightSpread},{"limit_percent",ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT}});
  if(xSpread>ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT||ySpread>ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT)
   fail(key,"","ANIMATION_AUTHORED_FX_ANCHOR_DRIFT",{{"center_x_spread_percent",xSpread},{"center_y_spread_percent",ySpread},{"limit_percent",ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT}});
  if(outsideRoiChanges>0)fail(key,"","ANIMATION_AUTHORED_FX_OUTSIDE_ROI",{{"outside_roi_changed_pixels",outsideRoiChanges}});
  std::uint64_t sourceStructurePixels=0,officialStructurePixels=0;
  for(std::size_t p=0;p<sourcePixels;p++)if(!sourceStructureRoi[p]&&sourceBase[p*4+3]>=16)sourceStructurePixels++;
  for(std::size_t p=0;p<officialPixels;p++)if(!officialStructureRoi[p]&&officialBase[p*4+3]>=16)officialStructurePixels++;
  double sourceStructureFraction=double(sourceStructurePixels)/sourcePixels,officialStructureFraction=double(officialStructurePixels)/officialPixels;
  if(sourceStructureFraction<ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION||officialStructureFraction<ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION)
   fail(key,"","ANIMATION_AUTHORED_FX_STRUCTURE_UNMEASURABLE",{{"source_structure_fraction",sourceStructureFraction},{"official_structure_fraction",officialStructureFraction},{"minimum_fraction",ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION}});
  Json authoredRoiReport={{"path",str(roiPath)},{"sha256",roiRow.at("sha256")},{"pixels",{roiW,roiH}},{"bbox",{rx0,ry0,rx1,ry1}},{"active_pixels",roiPixels}};
  Json officialRoiReport={{"bbox",{orx0,ory0,orx1,ory1}},{"active_pixels",officialRoiPixels},{"basis",baseOverlay?"union-of-official-overlay-alpha":"union-of-official-full-frame-deltas"}};
  Json structureReport={{"source_structure_fraction",sourceStructureFraction},{"official_structure_fraction",officialStructureFraction},{"source_visible_pixels",minVisible},{"official_visible_pixels",officialMinVisible},{"measurement","visible structure bounds outside the union of authored and official effect ROIs"}};
  Json staticReport={{"source_stable_fraction",sourceStable},{"official_stable_fraction",officialStableFraction},{"stability_deficit_percentage_points",stableDeficit},{"outside_roi_changes",outsideRoiChanges}};
  Json effectReport={{"active_frames",activeFrameCount},{"measured_effect_frames",measuredOverlayFrames},{"coverage_spread_percent",coverageSpread},{"luma_spread_percent",lumaSpread},{"width_spread_percent",widthSpread},{"height_spread_percent",heightSpread},{"center_x_spread_percent",xSpread},{"center_y_spread_percent",ySpread},{"status",result["status"]=="PASS"?"PASS":"BLOCKED"}};
  Json groupResult={{"key",key},{"frame_count",groupJobs.size()},{"authored_effect_roi",authoredRoiReport},{"official_effect_roi",officialRoiReport},{"structure_geometry",structureReport},{"static_base",staticReport},{"dynamic_fx",effectReport},{"frames",frameRows}};
  result["groups"].push_back(std::move(groupResult));
 }
 result["official_frame_inventory"]=batch.at("animation_gate").at("official_frame_inventory");return result;
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
  job.staticFrameCatalog=batch.value("frame_set_classification",std::string())==FRAME_SET_STATIC_CATALOG_V1;
 if(entry.contains("animation_group_transform")){
  job.animationGroupTransform=entry.at("animation_group_transform");
  need(batch.contains("animation_contract")
       &&(batch.at("animation_contract")==ANIMATION_CONTRACT_V2
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V1
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V2
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2
         ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2),
       "ANIMATION_GROUP_TRANSFORM_WITHOUT_REGISTERED_CONTRACT");
  need(job.silhouette,"ANIMATION_GROUP_TRANSFORM_REQUIRES_OFFICIAL_REFERENCES");
  job.fitMode="animation-group-uniform-fit";
 }else if(batch.contains("animation_contract")
          &&(batch.at("animation_contract")==ANIMATION_CONTRACT_V2
              ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V1
              ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_OVERLAY_V2
              ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1
              ||batch.at("animation_contract")==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2
              ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1
           ||batch.at("animation_contract")==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2))
  need(false,"ANIMATION_GROUP_TRANSFORM_MISSING: re-plan the complete group with the current tool");
 job.allowPadded=entry.value("padded_master_allowed",false);
 need(job.w<=job.samplingW*2&&job.h<=job.samplingH*2,"expansion exceeds 2x per edge; supply a better source");
 job.contract={{"entry",entry},{"output_root",str(root)},{"algorithm",batch.at("algorithm")},{"space","linear"},{"png_filter",settings.at("png_filter")}};
 if(batch.contains("animation_contract"))job.contract["animation_contract"]=batch.at("animation_contract");
 if(batch.contains("animation_gate"))job.contract["animation_gate"]=batch.at("animation_gate");
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
 bool hasVisibleScaleReference=false;for(const auto& job:jobs)if(job.silhouette){hasVisibleScaleReference=true;break;}
 const auto scaleFrameClass=batch.value("frame_set_classification",std::string());
 const bool visibleScaleGateRequired=!batch.contains("animation_contract")&&
      (hasVisibleScaleReference||scaleFrameClass==FRAME_SET_STATIC_CATALOG_V1||batch.contains("visible_scale_gate"));
 const bool visibleScaleGateBound=!batch.contains("animation_contract")&&batch.contains("visible_scale_gate");
 const bool staticFrameCatalog=scaleFrameClass==FRAME_SET_STATIC_CATALOG_V1;
 const bool historicalWeakScalePolicy=visibleScaleGateBound&&staticFrameCatalog&&verifyOnly&&
      (batch.at("visible_scale_gate")==visible_scale_gate_policy(false)||
        batch.at("visible_scale_gate")==static_frame_catalog_v2_legacy_policy()||
        batch.at("visible_scale_gate")==static_frame_catalog_v3_legacy_policy());
 Json visibleScaleAudit={{"status","NOT_APPLICABLE"}};
 if(visibleScaleGateBound){
  const auto expectedPolicy=visible_scale_gate_policy(staticFrameCatalog);
  if(batch.at("visible_scale_gate")==expectedPolicy)
   visibleScaleAudit={{"schema",expectedPolicy.at("schema")},{"status","BOUND"},{"policy",expectedPolicy}};
  else if(historicalWeakScalePolicy)
   visibleScaleAudit={{"schema",STATIC_FRAME_SCALE_GATE_SCHEMA},{"status","HISTORICAL_WEAK_POLICY_REPLAY_ONLY"},
    {"historical_policy",batch.at("visible_scale_gate")},{"current_policy",expectedPolicy}};
  else{
   const std::string error=staticFrameCatalog?"VISIBLE_SCALE_GATE_POLICY_STALE_REPLAN_REQUIRED":"VISIBLE_SCALE_GATE_POLICY_INVALID";
   blocked++;items.push_back({{"id","visible-scale-gate"},{"status","BLOCKED"},{"error",error}});
   visibleScaleAudit={{"schema",expectedPolicy.at("schema")},{"status","BLOCKED"},{"reason",error},{"policy",expectedPolicy}};
  }
 }else if(visibleScaleGateRequired){
  if(!verifyOnly){blocked++;items.push_back({{"id","visible-scale-gate"},{"status","BLOCKED"},{"error","VISIBLE_SCALE_GATE_POLICY_REQUIRED"}});
   visibleScaleAudit={{"schema",staticFrameCatalog?STATIC_FRAME_SCALE_GATE_SCHEMA:VISIBLE_SCALE_GATE_SCHEMA},{"status","BLOCKED"},{"reason","VISIBLE_SCALE_GATE_POLICY_REQUIRED"}};}
  else visibleScaleAudit={{"schema",staticFrameCatalog?STATIC_FRAME_SCALE_GATE_SCHEMA:VISIBLE_SCALE_GATE_SCHEMA},{"status","HISTORICAL_UNGATED_REPLAY_ONLY"}};
 }
 Json animationAudit;
 if(batch.contains("animation_contract")){
  auto contract=batch.at("animation_contract").get<std::string>();
  if(contract==ANIMATION_CONTRACT_V1){
   animationAudit=static_animation_registration_audit(jobs);
   if(!verifyOnly){
    animationAudit["status"]="BLOCKED";
    animationAudit["failures"].push_back({{"group",""},{"frame",""},{"reason","LEGACY_ANIMATION_CONTRACT_REQUIRES_REPLAN"},
      {"action","Legacy alpha-box-only animation batches are verify-only. Re-plan with static-structure-dynamic-fx-v2 and an official 1X reference root."}});
   }
  }else if(contract==ANIMATION_CONTRACT_V2||contract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||contract==ANIMATION_CONTRACT_BASE_OVERLAY_V2){
   const bool baseOverlay=contract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||contract==ANIMATION_CONTRACT_BASE_OVERLAY_V2;
   const bool nativeBaseEnvelope=contract==ANIMATION_CONTRACT_BASE_OVERLAY_V2;
   const auto gateSchema=nativeBaseEnvelope?"h3.animation.static-base-overlay.scale-gate.v2":
      baseOverlay?"h3.animation.static-base-overlay.scale-gate.v1":"h3.animation.static-fx.scale-gate.v2";
   need(batch.contains("animation_gate")&&batch.at("animation_gate").value("schema",std::string())==gateSchema,
        "ANIMATION_SCALE_GATE_POLICY_MISSING: re-plan with the gate required by this animation contract");
   if(baseOverlay){
    need(batch.at("animation_gate").value("render_semantics",std::string())=="vcmi-base-frame-then-current-frame"
         &&batch.at("animation_gate").value("base_frame",-1)==0,
         "ANIMATION_BASE_OVERLAY_RENDER_SEMANTICS_REQUIRED");
   }
   if(blocked==0){verify_animation_reference_inventory(batch,jobs);verify_animation_group_transforms(batch,jobs);}
   animationAudit=static_animation_scale_audit_v2(jobs,batch.at("items").size(),blocked,baseOverlay,nativeBaseEnvelope);
   animationAudit["official_frame_inventory"]=batch.at("animation_gate").at("official_frame_inventory");
  }else if(contract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1){
   const auto gateSchema="h3.animation.authored-base-overlay.scale-gate.v1";
   need(batch.contains("animation_gate")&&batch.at("animation_gate").value("schema",std::string())==gateSchema,
        "ANIMATION_AUTHORED_ROI_SCALE_GATE_POLICY_MISSING: re-plan with the explicit effect ROI contract");
   need(batch.at("animation_gate").value("render_semantics",std::string())=="vcmi-base-frame-then-current-frame"
        &&batch.at("animation_gate").value("base_frame",-1)==0,
        "ANIMATION_BASE_OVERLAY_RENDER_SEMANTICS_REQUIRED");
   if(blocked==0){verify_animation_reference_inventory(batch,jobs);verify_animation_group_transforms(batch,jobs);}
   animationAudit=static_animation_authored_roi_audit(jobs,batch.at("items").size(),blocked,batch);
  }else if(contract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||contract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||contract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2){
   const bool baseAuthoredV2=contract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2;
   const bool compositeV2=contract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
   const auto gateSchema=baseAuthoredV2?"h3.animation.authored-base-overlay.scale-gate.v2":compositeV2?"h3.animation.authored-composite.scale-gate.v2":"h3.animation.authored-composite.scale-gate.v1";
   need(batch.contains("animation_gate")&&batch.at("animation_gate").value("schema",std::string())==gateSchema,"ANIMATION_AUTHORED_ROI_V2_POLICY_MISSING: re-plan with the matching authored effect contract");
   if(baseAuthoredV2)need(batch.at("animation_gate").value("render_semantics",std::string())=="vcmi-base-frame-then-current-frame"&&batch.at("animation_gate").value("base_frame",-1)==0,"ANIMATION_BASE_OVERLAY_RENDER_SEMANTICS_REQUIRED");
   else need(batch.at("animation_gate").value("render_semantics",std::string())=="full-frame-per-frame","ANIMATION_FULL_FRAME_RENDER_SEMANTICS_REQUIRED");
   if(blocked==0){verify_animation_reference_inventory(batch,jobs);verify_animation_group_transforms(batch,jobs);}
   animationAudit=static_animation_authored_roi_v2_audit(jobs,batch.at("items").size(),blocked,batch,baseAuthoredV2);
  }else need(false,"EXPANSION_ANIMATION_CONTRACT_UNSUPPORTED: "+contract);
  if(animationAudit.at("status")=="BLOCKED"){
   blocked+=int(animationAudit.at("failures").size());
   for(const auto& failure:animationAudit.at("failures")){
    auto group=failure.value("group",std::string()),frame=failure.value("frame",std::string());
    auto id=group.empty()?frame:frame.empty()?group:group+"/"+frame;
    items.push_back({{"id",id},{"status","BLOCKED"},{"error",failure.value("reason",std::string("STATIC_ANIMATION_REGISTRATION_DRIFT"))},{"diagnostic",failure}});
   }
  }
 }
  const auto frameSetClassification=batch.value("frame_set_classification",std::string());
  need(frameSetClassification.empty()||frameSetClassification==FRAME_SET_SINGLE_V1||frameSetClassification==FRAME_SET_STATIC_CATALOG_V1,
       "EXPANSION_FRAME_SET_CLASSIFICATION_UNSUPPORTED: "+frameSetClassification);
  need(!(batch.contains("animation_contract")&&!frameSetClassification.empty()&&frameSetClassification!=FRAME_SET_SINGLE_V1),
       "EXPANSION_FRAME_SET_CLASSIFICATION_CONFLICT");
  if(frameSetClassification==FRAME_SET_STATIC_CATALOG_V1){
   need(!batch.contains("animation_contract"),"EXPANSION_FRAME_SET_CLASSIFICATION_CONFLICT");
   need(batch.contains("frame_set_inventory")&&batch.at("frame_set_inventory").is_array(),
        "FRAME_SET_CATALOG_OFFICIAL_INVENTORY_MISSING: re-plan with the complete official 1X catalog");
   if(blocked==0){
    Json inventoryBatch={{"animation_gate",{{"official_frame_inventory",batch.at("frame_set_inventory")}}}};
    verify_animation_reference_inventory(inventoryBatch,jobs);
   }
  }else if(!batch.contains("animation_contract")){
   std::vector<std::string> groups;
   // Also inspect bound per-frame references: a one-frame source with a complete
   // official multi-frame group is an incomplete sequence, not an unrelated still.
   std::map<std::wstring,std::vector<const ExpansionJob*>> groupedJobs;
   for(const auto& job:jobs){
    auto parsed=animation_group_frame(job.source);if(!parsed)continue;
    auto key=animation_folded_text(job.source.parent_path().filename().wstring()+L"|"+parsed->first);
    groupedJobs[key].push_back(&job);
   }
   for(const auto& [key,group]:groupedJobs){
    (void)key;bool multi=group.size()>=2;
    if(!multi&&!group.empty()&&group.front()->silhouette){
     auto refs=animation_reference_frames(group.front()->reference.parent_path(),animation_group_frame(group.front()->source)->first);
     multi=refs.size()>=2;
    }
    if(multi)groups.push_back(utf8(group.front()->source.parent_path().filename().wstring())+"/"+
                              utf8(animation_group_frame(group.front()->source)->first));
   }
   if(!groups.empty()){
    blocked++;items.push_back({{"id","frame-set-classification"},{"status","BLOCKED"},
     {"error","MULTIFRAME_FRAME_SET_CLASSIFICATION_REQUIRED"},{"groups",groups},
     {"action","Choose a supported animation contract, or independent-static-frame-catalog-v1 with the complete official 1X frame set."}});
   }
  }
 if(!batch.contains("animation_contract")&&visibleScaleGateBound&&blocked==0&&!historicalWeakScalePolicy){
  visibleScaleAudit=visible_scale_gate_audit(jobs,batch.at("items").size(),staticFrameCatalog);
  if(visibleScaleAudit.value("status",std::string())=="BLOCKED"){
   for(const auto& failure:visibleScaleAudit.at("failures")){
    blocked++;items.push_back({{"id",failure.value("id",std::string("visible-scale-gate"))},{"status","BLOCKED"},
      {"error",failure.value("reason",std::string("MASTER_VISIBLE_SCALE_TOO_SMALL"))},{"diagnostic",failure.value("diagnostic",Json::object())}});
   }
  }
 }
 for(const auto& output:outputs)need(!sources.contains(output),"an expanded output would replace another job's source");
 for(const auto& receipt:receipts)need(!outputs.contains(receipt)&&!sources.contains(receipt),"receipt aliases an image in the batch");
 if(batch.contains("animation_contract"))return{{"items",items},{"ready",jobs.size()},{"blocked",blocked},{"outputs_written",0},{"animation_registration",animationAudit}};
 return{{"items",items},{"ready",jobs.size()},{"blocked",blocked},{"outputs_written",0},{"visible_scale_gate",visibleScaleAudit}};
}
inline Json make_fullraw_expansion_batch(const fs::path& spec,const std::string& digest,const fs::path& outputRoot){
 auto settings=expansion_settings(default_settings());auto batch=fullraw_batch_document(spec,digest,outputRoot,settings);
 std::vector<ExpansionJob> jobs;auto pre=check_expansion(batch,settings,jobs);
 need(pre.at("blocked")==0,"FULLRAW_PLAN_BLOCKED: "+pre.dump());return batch;
}
inline Json make_expansion_batch(const std::vector<fs::path>& sources,int sourceTier,int targetScale,std::array<int,2> customBase,const fs::path& outputRoot,const std::string& filter="h3-quality",const fs::path& mirrorSourceRoot={},bool allowPaddedMaster=false,const fs::path& referenceRoot={},const std::string& fitAnchor="",const fs::path& nativeAuthorityPath={},const std::string& animationContract="",const std::string& frameSetClassification="",const fs::path& animationRoiMask={}){
 need(filter=="h3-quality","master expansion supports only the h3-quality filter; the legacy local-range sampler is verify-only");
 need(fitAnchor==""||fitAnchor=="bottom_right"||fitAnchor=="center","fit anchor must be bottom_right or center");
  need(animationContract==""||animationContract==ANIMATION_CONTRACT_V2||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V2||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2,
       "new animation plans must use static-structure-dynamic-fx-v2 or a supported VCMI base-overlay contract; legacy v1 is historical verify-only");
  need(frameSetClassification==""||frameSetClassification==FRAME_SET_SINGLE_V1||frameSetClassification==FRAME_SET_STATIC_CATALOG_V1,
       "EXPANSION_FRAME_SET_CLASSIFICATION_UNSUPPORTED: "+frameSetClassification);
  need(animationContract.empty()||frameSetClassification.empty()||frameSetClassification==FRAME_SET_SINGLE_V1,
       "EXPANSION_FRAME_SET_CLASSIFICATION_CONFLICT");
  if(animationContract==ANIMATION_CONTRACT_V2||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V2||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2){
  need(targetScale==3,"static animation gates currently certify formal 3X delivery only");
  need(!referenceRoot.empty(),"animation geometry contracts require an official native 1X reference root");
 }
  const bool authoredEffectRoiContract=animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
  need(authoredEffectRoiContract?!animationRoiMask.empty():animationRoiMask.empty(),
       "ANIMATION_AUTHORED_ROI_MASK_REQUIRED_OR_UNEXPECTED");
  if(!animationRoiMask.empty())need(fs::exists(animationRoiMask)&&fs::is_regular_file(animationRoiMask)&&animationRoiMask.extension()==L".png",
       "ANIMATION_AUTHORED_ROI_MASK_NOT_FOUND_OR_NOT_PNG: "+str(animationRoiMask));
 need(referenceRoot.empty()||fs::exists(referenceRoot)&&fs::is_directory(referenceRoot),"reference root must be an existing directory");
  if(animationContract.empty()&&frameSetClassification!=FRAME_SET_STATIC_CATALOG_V1){
   auto unclassified=unclassified_multiframe_groups(sources,referenceRoot);
   if(!unclassified.empty())need(false,"MULTIFRAME_FRAME_SET_CLASSIFICATION_REQUIRED: "+unclassified.front()+
        "; choose an animation contract or independent-static-frame-catalog-v1 and bind the complete official 1X frame set");
  }
  if(frameSetClassification==FRAME_SET_STATIC_CATALOG_V1){
   need(animationContract.empty(),"EXPANSION_FRAME_SET_CLASSIFICATION_CONFLICT");
   need(targetScale==3,"independent static frame catalogs currently certify formal 3X delivery only");
   need(!referenceRoot.empty(),"FRAME_SET_CATALOG_OFFICIAL_REFERENCE_REQUIRED: select the complete official native 1X frame root");
  }
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
  Json authoredRoi=Json{};
  if(authoredEffectRoiContract){
   auto roiPath=clean_path(animationRoiMask);auto roiRaw=read_all(roiPath);auto roiMeta=decoder.frame(roiRaw);
   need(sources.size()>=2,"ANIMATION_AUTHORED_ROI_NEEDS_MULTIPLE_FRAMES");
   auto firstSource=clean_path(sources.front());auto firstParsed=animation_group_frame(firstSource);
   need(firstParsed.has_value(),"ANIMATION_SCALE_FRAME_NAME_REQUIRED: "+str(firstSource));
   for(std::size_t i=0;i<sources.size();i++){
    auto source=clean_path(sources[i]);auto parsed=animation_group_frame(source);
    need(parsed.has_value()&&clean_path(source.parent_path())==clean_path(firstSource.parent_path())&&parsed->first==firstParsed->first,
         "ANIMATION_AUTHORED_ROI_REQUIRES_ONE_GROUP_PER_PLAN");
    need(items[i].at("source_pixels")==Json::array({roiMeta.w,roiMeta.h}),
         "ANIMATION_AUTHORED_ROI_CANVAS_MISMATCH: "+str(source));
   }
   auto roiPixels=decoder.decode_rgba(roiRaw,roiMeta.w,roiMeta.h);std::uint64_t positive=0;int x0=roiMeta.w,y0=roiMeta.h,x1=-1,y1=-1;
   constexpr int roiThreshold=1;
   for(int y=0;y<roiMeta.h;y++)for(int x=0;x<roiMeta.w;x++){
    auto at=(std::size_t(y)*roiMeta.w+x)*4;auto luma=(54*roiPixels[at]+183*roiPixels[at+1]+19*roiPixels[at+2])/256;
    if(roiPixels[at+3]&&luma>=roiThreshold){positive++;x0=std::min(x0,x);y0=std::min(y0,y);x1=std::max(x1,x+1);y1=std::max(y1,y+1);}
   }
   need(positive>0&&x1>x0&&y1>y0,"ANIMATION_AUTHORED_ROI_EMPTY");
   authoredRoi={{"path",str(roiPath)},{"sha256",sha(roiRaw)},{"pixels",{roiMeta.w,roiMeta.h}},
                {"alpha_threshold",roiThreshold},{"bbox",{x0,y0,x1,y1}},{"positive_pixels",positive},
                {"semantics","RGBA luminance at or above threshold marks authored effect region"}};
  }
 auto settings=default_settings();settings["filter"]=filter;settings=expansion_settings(settings);
  if(animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2)seal_animation_group_transforms_authored_composite(items,animationRoiMask,authoredRoi.at("sha256").get<std::string>());else if(animationContract==ANIMATION_CONTRACT_V2||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V2||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1)seal_animation_group_transforms(items);
 Json batch={{"schema","h3.master-expand.batch.v1"},{"algorithm",QUALITY_ALGORITHM},{"created_utc",now()},{"output_root",str(root)},{"receipt_directory",str(root/L"receipts")},{"settings",settings},{"items",items},{"note","Algorithmic resampling only; source files unchanged. New masters require visual review. No model inference, no native-detail claim and no game deployment."}};
 if(!mirrorRoot.empty())batch["mirror_source_root"]=str(mirrorRoot);
 if(!authority.is_null()){batch["native_1x_authority"]={{"path",authority.at("_authority_path")},{"sha256",authority.at("_authority_sha256")},{"source_lods",authority.value("source_lods",Json::array())}};}
  if(!frameSetClassification.empty())batch["frame_set_classification"]=frameSetClassification;
  if(frameSetClassification==FRAME_SET_STATIC_CATALOG_V1)
   batch["frame_set_inventory"]=build_animation_reference_inventory(sources,referenceRoot,true);
  if(!animationContract.empty()){
  batch["animation_contract"]=animationContract;
   const bool baseOverlay=animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V1||animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V2||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2;
   const bool nativeBaseEnvelope=animationContract==ANIMATION_CONTRACT_BASE_OVERLAY_V2;
   const bool authoredEffectRoi=animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
   const bool authoredEffectV2=animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
   const bool authoredComposite=animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1||animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
   const std::string gateSchema=animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2?"h3.animation.authored-composite.scale-gate.v2":authoredComposite?"h3.animation.authored-composite.scale-gate.v1":animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2?"h3.animation.authored-base-overlay.scale-gate.v2":animationContract==ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1?"h3.animation.authored-base-overlay.scale-gate.v1":nativeBaseEnvelope?"h3.animation.static-base-overlay.scale-gate.v2":baseOverlay?"h3.animation.static-base-overlay.scale-gate.v1":"h3.animation.static-fx.scale-gate.v2";
   batch["animation_gate"]={{"schema",gateSchema},
    {"max_visible_size_deviation_percent",ANIMATION_VISIBLE_SIZE_DEVIATION_LIMIT_PERCENT},
    {"max_visible_aspect_deviation_percent",ANIMATION_VISIBLE_ASPECT_DEVIATION_LIMIT_PERCENT},
    {"max_frame_scale_spread_percent",ANIMATION_FRAME_SCALE_SPREAD_LIMIT_PERCENT},
    {"minimum_static_base_fraction",nativeBaseEnvelope||authoredEffectRoi?0.0:ANIMATION_STATIC_BASE_MIN_FRACTION},
    {"static_base_native_deficit_limit_percentage_points",nativeBaseEnvelope?ANIMATION_STATIC_BASE_NATIVE_DEFICIT_LIMIT_PERCENTAGE_POINTS:0.0},
    {"fx_excess_spread_limit_percentage_points",ANIMATION_FX_REFERENCE_DEVIATION_LIMIT_PERCENT},
    {"max_dynamic_fx_frame_size_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_SIZE_DEVIATION_LIMIT_PERCENT:0.0},
    {"max_dynamic_fx_frame_anchor_deviation_percent",nativeBaseEnvelope?ANIMATION_FX_FRAME_ANCHOR_LIMIT_PERCENT:0.0},
    {"max_authored_fx_size_spread_percent",authoredEffectRoi?ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT:0.0},
    {"max_authored_fx_anchor_spread_percent",authoredEffectRoi?ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT:0.0},
    {"max_authored_fx_luma_spread_percent",authoredEffectRoi?ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT:0.0},
    {"group_registration","one_shared_uniform_transform_for_the_complete_frame_group"},
    {"dynamic_fx_registration",animationContract==ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2?"full-frame cel; shared transform is fitted from static structure outside authored and official effect ROIs":authoredComposite?"legacy full-frame authored ROI; transform follows the complete-cel union":authoredEffectV2?"base-overlay authored ROI; static structure compared outside source and official effect regions":authoredEffectRoi?"legacy authored ROI full-scene comparison":nativeBaseEnvelope?"compare_each_overlay_size_and_anchor_to_its_official_native_frame":baseOverlay?"compare_overlay_only_frames_1_plus_to_official_overlays":"compare_group_motion_spread_to_official_and_group_mean_anchor"},
    {"dynamic_fx_coordinate_space","shared_group_transform_3x_normalized_to_native_canvas"},
    {"visible_measurement",authoredEffectV2?"visible structure bounds outside authored and official FX ROIs; native canvas coordinates":"alpha>=1/32 source and official frame boxes in native canvas coordinates"},
    {"static_base_measurement",authoredEffectV2?"fixed structure outside effect ROIs; source changes are restricted to the sealed authored ROI":authoredEffectRoi?"source and official fixed-pixel fractions are compared after VCMI BASE composition; changes outside the authored effect ROI block":nativeBaseEnvelope?"source and official fixed-pixel fractions are compared after VCMI BASE composition; no fixed fraction is imposed when native animation changes the full cel":baseOverlay?"frame 0 is the shared base sample; later source/reference overlays are composed over frame 0 per VCMI BASE semantics":"exact RGBA pixels shared across the complete frame group"},
    {"official_frame_inventory",build_animation_reference_inventory(sources,referenceRoot)}};
   if(authoredEffectRoi){
    batch["animation_gate"]["authored_effect_roi"]=authoredRoi;
    batch["animation_gate"]["authored_effect_limits"]={{"max_native_stability_deficit_percentage_points",ANIMATION_AUTHORED_FX_NATIVE_BASE_DEFICIT_LIMIT_PERCENTAGE_POINTS},
      {"max_roi_size_spread_percent",ANIMATION_AUTHORED_FX_SIZE_SPREAD_LIMIT_PERCENT},
      {"max_roi_anchor_spread_percent",ANIMATION_AUTHORED_FX_ANCHOR_SPREAD_LIMIT_PERCENT},
      {"max_roi_luma_spread_percent",ANIMATION_AUTHORED_FX_LUMA_SPREAD_LIMIT_PERCENT}};
    if(authoredEffectV2){
     batch["animation_gate"]["authored_effect_limits"]["max_roi_fraction"]=ANIMATION_AUTHORED_FX_MAX_ROI_FRACTION;
     batch["animation_gate"]["authored_effect_limits"]["min_structure_fraction"]=ANIMATION_AUTHORED_FX_MIN_STRUCTURE_FRACTION;
    }
   }
   if(baseOverlay){batch["animation_gate"]["render_semantics"]="vcmi-base-frame-then-current-frame";batch["animation_gate"]["base_frame"]=0;}
   else if(authoredComposite)batch["animation_gate"]["render_semantics"]="full-frame-per-frame";
 }
 if(!referenceRoot.empty()&&animationContract.empty())
  batch["visible_scale_gate"]=visible_scale_gate_policy(frameSetClassification==FRAME_SET_STATIC_CATALOG_V1);
 std::vector<ExpansionJob> checked;auto pre=check_expansion(batch,settings,checked);need(pre["blocked"]==0,"EXPANSION_PLAN_BLOCKED: "+pre.dump());return batch;
}
inline Json verify_expanded_master(const ExpansionJob& job,bool replay){
 auto r=read_json(job.receipt);
 need(r.at("schema")== (job.domain.active?"h3.master-expand.receipt.v2":"h3.master-expand.receipt.v1")&&r.at("job_fingerprint")==job.fingerprint&&r.at("contract")==job.contract,"expanded-master receipt identity changed");
 need(r.at("algorithm")==job.contract.at("algorithm")&&r.at("backend")=="cpu"&&r.at("model_inference_performed")==false&&r.at("model_native_output")==false&&r.at("human_approved")==false&&r.at("game_deployed")==false&&r.at("runtime_accepted")==false,"expanded-master provenance/authority changed");
 need(r.at("source_sha256")==job.sourceSha&&r.at("source_pixels")==Json::array({job.sourceW,job.sourceH})&&r.at("output_pixels")==Json::array({job.w,job.h}),"expanded-master source or geometry changed");
 need(r.at("source")==str(job.source)&&r.at("output")==str(job.output)&&r.at("base_pixels")==job.contract["entry"]["base_pixels"]&&r.at("target_scale")==job.contract["entry"]["target_scale"]&&r.at("source_unchanged")==true,"expanded-master receipt fields changed");
 need(r.at("scale_x")==double(job.w)/job.samplingW&&r.at("scale_y")==double(job.h)/job.samplingH,"expanded-master scale changed");
 if(job.animationGroupTransform.is_object())need(r.contains("animation_group_transform")&&r.at("animation_group_transform")==job.animationGroupTransform,
      "ANIMATION_GROUP_TRANSFORM_RECEIPT_DRIFT");
 if(r.contains("fit_mode")){
  need(r.at("master_untouched").get<bool>()&&r.at("source_unchanged").get<bool>(),"expanded-master immutability claim changed");
  need(job.silhouette?r.at("fit_mode").get<std::string>()==job.fitMode
      :r.at("fit_mode").get<std::string>()==master_fit_mode(job.samplingW,job.samplingH,job.w,job.h),"expanded-master fit mode changed");
  need(r.at("official_output_pixels")==Json::array({job.w,job.h}),"expanded-master output is not the official native size times the target scale");
  need(std::fabs(r.at("aspect_error_pct").get<double>()-job.aspectError*100.0)<1e-9,"expanded-master aspect error changed");
  if(!job.silhouette)need(master_fit_aspect_error(job.samplingW,job.samplingH,job.w,job.h)<=MASTER_FIT_ASPECT_CEILING,"expanded-master aspect error exceeds the fit ceiling");
  need(!r.contains("padded_master_allowed")||r.at("padded_master_allowed").get<bool>()==job.allowPadded,"expanded-master padded-canvas allowance changed");
 }
 auto source=read_all(job.source);need(sha(source)==job.sourceSha,"source changed on resume");auto png=read_all(job.output);Wic decoder;
 need(sha(png)==r.at("png_sha256").get<std::string>()&&png.size()==r.at("bytes"),"expanded PNG damaged/changed");auto pixels=decoder.decode_rgba(png,job.w,job.h);need(sha(pixels)==r.at("rgba_sha256").get<std::string>(),"expanded PNG readback differs");
  if(job.staticFrameCatalog){
   const auto outputScale=post_export_static_frame_scale_gate(job,pixels);
   need(outputScale.at("status")=="PASS","EXPANDED_VISIBLE_SCALE_TOO_SMALL: "+outputScale.dump());
   if(r.contains("post_export_visible_scale_gate"))
    need(r.at("post_export_visible_scale_gate")==outputScale,"EXPANDED_VISIBLE_SCALE_RECEIPT_DRIFT");
   r["post_export_visible_scale_gate"]=outputScale;
  }
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
  int ax0=job.slotBox.x0,ay0=job.slotBox.y0,ax1=job.slotBox.x1,ay1=job.slotBox.y1;
  if(job.animationGroupTransform.is_object()){
   const auto& b=job.animationGroupTransform.at("target_union_box_3x");ax0=b[0].get<int>();ay0=b[1].get<int>();ax1=b[2].get<int>();ay1=b[3].get<int>();
  }
  need(!pb.empty&&pb.x0>=ax0&&pb.y0>=ay0&&pb.x1<=ax1&&pb.y1<=ay1,
   "expanded artwork overflows the official silhouette box ("+std::to_string(pb.x0)+","+std::to_string(pb.y0)+","+std::to_string(pb.x1)+","+std::to_string(pb.y1)
   +" outside "+std::to_string(ax0)+","+std::to_string(ay0)+","+std::to_string(ax1)+","+std::to_string(ay1)+")");
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
  Image replayImage=job.animationGroupTransform.is_object()?animation_group_fit_cpu(im,job.w,job.h,job.animationGroupTransform):
   job.silhouette?silhouette_expand_cpu(im,job.w,job.h,job.slotBox,artwork_box(im),job.fitAnchor):expansion_replay(im,job.w,job.h,job.contract);
  auto expected=quantize(replayImage,true);
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
   Json result={{"id",job.id},{"status",verifyOnly?"VERIFIED":"REUSED_VERIFIED"},{"receipt",str(job.receipt)},{"total_ms",ms(start)}};
   if(prior.contains("post_export_visible_scale_gate"))result["post_export_visible_scale_gate"]=prior.at("post_export_visible_scale_gate");
   return result;}
 need(!verifyOnly,"expanded-master receipt missing");auto raw=read_all(job.source);need(sha(raw)==job.sourceSha,"source drift before expansion");Wic decoder;std::string colorNote;
 auto t=Clock::now();auto im=decoder.decode(raw,true,colorNote);if(job.domain.active)im=fullraw_compose(job.domain,im);double decodeMs=ms(t);
 auto pad=master_pad_signature(im);
 need(job.allowPadded||!pad.padded,"master canvas is padded around its own artwork (bands left/right/top/bottom "+std::to_string(int(pad.left*100))+"%/"+std::to_string(int(pad.right*100))+"%/"+std::to_string(int(pad.top*100))+"%/"+std::to_string(int(pad.bottom*100))+"%); the ratio ceiling passes but the artwork would shrink inside the official cell - re-derive the master so the artwork fills its canvas, or plan with --allow-padded-master");
 t=Clock::now();Image expanded;ArtworkBox masterBox;
 if(job.animationGroupTransform.is_object()){masterBox=artwork_box(im);expanded=animation_group_fit_cpu(im,job.w,job.h,job.animationGroupTransform);}
 else if(job.silhouette){masterBox=artwork_box(im);expanded=silhouette_expand_cpu(im,job.w,job.h,job.slotBox,masterBox,job.fitAnchor);}
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
 if(job.animationGroupTransform.is_object())receipt["animation_group_transform"]=job.animationGroupTransform;
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
  if(job.staticFrameCatalog)receipt["post_export_visible_scale_gate"]=post_export_static_frame_scale_gate(job,pixels);
 same_or_new(job.output,png);
 if(job.domain.active&&!cropPng.empty())same_or_new(job.domain.cropOutput,cropPng);
 need(sha(read_all(job.source))==job.sourceSha,"source changed during expansion");
 if(job.domain.active)fullraw_recheck(job.contract.at("entry"),job.domainBatch,job.domain);
  write_json_new(job.receipt,receipt);auto verified=verify_expanded_master(job,false);
 if(job.domain.active&&detail.at("crop_status")!="ZERO_OUTSIDE_CROP")
  return {{"id",job.id},{"status","FAILED"},{"code",detail.at("crop_failure")},
   {"diagnostic_output",str(job.output)},{"diagnostic_output_written",true},{"receipt",str(job.receipt)},
   {"diagnostics",detail.at("diagnostics")},{"crop_written",false}};
  Json result={{"id",job.id},{"status","EXPANDED_VERIFIED"},{"output",str(job.output)},{"receipt",str(job.receipt)},{"source_pixels",{job.sourceW,job.sourceH}},{"output_pixels",{job.w,job.h}},{"resample_ms",resizeMs},{"total_ms",ms(start)}};
  if(verified.contains("post_export_visible_scale_gate"))result["post_export_visible_scale_gate"]=verified.at("post_export_visible_scale_gate");
  return result;
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
