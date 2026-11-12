#pragma once
// Offline candidates only. Correspondence meshes are supplied by the art owner.
// No inference, approval, native timing guess, or shared-manifest writes.
#include "jobs.hpp"
#include <map>
#include <numbers>

namespace h3tier {
inline constexpr const char* MOTION_ALGORITHM="linear-premultiplied-correspondence-mesh-ewa-time-v6";
inline constexpr const char* MOTION_ALGORITHM_V5="linear-premultiplied-correspondence-mesh-ewa-time-v5";
inline constexpr const char* MOTION_ALGORITHM_V4="linear-premultiplied-correspondence-mesh-ewa-time-v4";
inline bool motion_modern_algorithm(const std::string& value){return value==MOTION_ALGORITHM||value==MOTION_ALGORITHM_V5;}
struct MotionPoint { double x=0,y=0; };
using MotionTriangle=std::array<int,3>;
inline double motion_real(const Json& j,const char* label){need(j.is_number()&&!j.is_boolean(),std::string("number required: ")+label);double v=j.get<double>();need(std::isfinite(v),std::string("nonfinite: ")+label);return v;}
inline MotionPoint motion_point(const Json& j){need(j.is_array()&&j.size()==2,"motion point requires two coordinates");return{motion_real(j[0],"x"),motion_real(j[1],"y")};}
inline double motion_area(MotionPoint a,MotionPoint b,MotionPoint c){return(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);}
inline std::int64_t motion_gcd(std::int64_t a,std::int64_t b){while(b){auto r=a%b;a=b;b=r;}return a;}
inline Json motion_fraction(std::int64_t n,std::int64_t d){need(d>0&&n>=0,"MOTION_RATIONAL_COORDINATE_INVALID");auto g=motion_gcd(n,d);if(g==0)g=d;return Json::array({n/g,d/g});}
inline std::pair<int,int> motion_ratio(const Json& value){need(value.is_array()&&value.size()==2,"MOTION_RATIONAL_GRID_REQUIRES_NUMERATOR_DENOMINATOR");int n=integer(value[0],1,65536,"rational source grid numerator"),d=integer(value[1],1,65536,"rational source grid denominator");need(motion_gcd(n,d)==1,"MOTION_RATIONAL_GRID_MUST_BE_REDUCED");need(double(n)/d>=3.0&&double(n)/d<=MAX_AXIS,"MOTION_RATIONAL_GRID_DENSITY_OUT_OF_RANGE");return {n,d};}
inline std::array<double,3> motion_weights(MotionPoint p,MotionPoint a,MotionPoint b,MotionPoint c){double d=motion_area(a,b,c);return{motion_area(p,b,c)/d,motion_area(a,p,c)/d,motion_area(a,b,p)/d};}
inline bool motion_inside(const std::array<double,3>& w){return std::min({w[0],w[1],w[2]})>=-1e-9;}
inline std::vector<MotionPoint> motion_points(const Json& j){need(j.is_array()&&j.size()>=3&&j.size()<=512,"mesh vertex count 3..512");std::vector<MotionPoint> out;for(auto& v:j)out.push_back(motion_point(v));return out;}

struct MotionSource {fs::path file;std::string hash;Json key;int w=0,h=0,rawW=0,rawH=0,scale=3,rationalOutsideMaxAlpha=0;std::uint64_t rationalOutsidePositive=0;double scaleX=3,scaleY=3;std::array<int,2> origin{},padding{};std::array<int,4> roi{},sourceNativeRect{};std::array<int,2> rationalGrid{};std::array<double,2> rationalOffset{};std::array<double,4> rationalRect{};bool hasRoi=false,registered=false,explicitRegion=false,rationalRegion=false,rationalWholeCrop=false;};
inline MotionPoint motion_source_native_point(const MotionSource& source,MotionPoint p,bool includePadding=false){double padX=includePadding&&source.registered&&!source.explicitRegion&&!source.rationalRegion?source.padding[0]:0,padY=includePadding&&source.registered&&!source.explicitRegion&&!source.rationalRegion?source.padding[1]:0;double offsetX=source.rationalRegion?source.rationalOffset[0]:0,offsetY=source.rationalRegion?source.rationalOffset[1]:0;return{(p.x-padX-offsetX)/source.scaleX+source.origin[0],(p.y-padY-offsetY)/source.scaleY+source.origin[1]};}
struct MotionSourceMeta {std::string hash;int w=0,h=0;std::set<std::array<int,4>> validatedZeroOutside;std::set<std::string> validatedComponents;std::map<std::string,std::pair<std::uint64_t,int>> nativeCropAlphaAudit;};
using MotionSourceCache=std::map<std::wstring,MotionSourceMeta>;
struct MotionLayer {std::string name,kind;MotionSource a,b;bool paired=false,posePaired=false;double time=0;std::vector<MotionPoint> va,vb,target;std::vector<std::array<double,3>> gainA,gainB;std::vector<MotionTriangle> triangles;Json anchors;};
inline constexpr std::uint64_t MOTION_SAMPLE_WORK_LIMIT=1ull<<18,MOTION_JOB_WORK_LIMIT=1ull<<28,MOTION_BATCH_WORK_LIMIT=1ull<<30;
struct MotionSamplingWork {
 std::uint64_t limit=0,used=0,source=0,raster=0,ewa=0,alpha=0;double maximumLambda=1;
 void add(std::uint64_t n,std::uint64_t& category){need(n<=limit&&used<=limit-n,"MOTION_SAMPLING_WORK_BUDGET_EXCEEDED");used+=n;category+=n;}
 Json evidence()const{return {{"unit","source-raster-ewa-alpha-loop-visits"},{"source_visits",source},{"target_raster_visits",raster},{"ewa_visits",ewa},{"alpha_visits",alpha},{"total_visits",used},{"maximum_lambda_JJt",maximumLambda}};}
};
struct MotionJob {std::string id,fingerprint,mode,reuse,algorithm;Json entry,contract,native,quality;fs::path output,receipt;int w=0,h=0,targetScale=3;std::array<int,2> origin{};std::array<int,4> reframe{};std::vector<MotionLayer> layers;MotionSource copy;std::uint64_t admission=0,workLimit=0;bool anchoredSurface=false;MotionSamplingWork estimatedWork;};
inline void motion_admit_sampling(MotionJob& job,std::uint64_t* preflightRasterWork=nullptr);
inline void motion_fields(const Json& value,std::initializer_list<const char*> allowed){
 need(value.is_object(),"MOTION_OBJECT_REQUIRED");for(auto it=value.begin();it!=value.end();++it){bool found=false;for(auto name:allowed)found|=it.key()==name;need(found,"MOTION_UNKNOWN_FIELD: "+it.key());}
}

inline Json motion_settings(Json settings){need(settings.at("backend")=="cpu"&&settings.at("space")=="linear"&&settings.at("filter")=="h3-quality","motion requires CPU / linear / h3-quality");return settings_checked(Json{{"settings",settings}});}
inline std::map<std::string,Json> motion_native_entries(const Json& batch){
 need(batch.at("native_contracts").is_array()&&!batch.at("native_contracts").empty()&&batch.at("native_contracts").size()<=100,"native contract count");
 std::map<std::string,Json> entries;
 for(auto& binding:batch.at("native_contracts")){
  need(valid_sha(binding.at("sha256")),"native contract SHA required");auto raw=read_all(path(binding.at("path")),32*1024*1024);need(sha(raw)==binding.at("sha256").get<std::string>(),"native contract drift");auto native=parse_json(raw);
  need(native.at("entries").is_array()&&!native.at("entries").empty()&&native.at("entries").size()<=10000,"native entries required");
  Json projection;
  if(native.contains("projection_source")){auto dependency=native.at("projection_source");need(valid_sha(dependency.at("sha256")),"native projection source SHA required");auto original=read_all(path(dependency.at("path")),32*1024*1024);need(sha(original)==dependency.at("sha256").get<std::string>(),"native projection source drift");projection=parse_json(original);need(projection.at("frames").is_array(),"projection must consume original frame bindings");}
  for(auto& entry:native.at("entries")){
   native_name(entry.at("key"));need(entry.at("key")[0]=="def","motion requires original DEF slots");need(valid_sha(entry.at("equivalence_class")),"original equivalence class required");
   auto canvas=pair(entry.at("targets").at("canvas_1x"),1,"canvas"),crop=pair(entry.at("targets").at("crop_1x"),1,"crop"),origin=pair(entry.at("targets").at("origin_1x"),0,"origin");
   need(origin[0]+crop[0]<=canvas[0]&&origin[1]+crop[1]<=canvas[1],"native crop outside canvas");
   auto name=entry.at("key").dump();if(entries.contains(name))need(entries.at(name)==entry,"conflicting original slot bindings");else entries.emplace(name,entry);
   need(entries.size()<=10000,"combined native entry limit");if(!projection.is_null()){bool found=false;for(auto& frame:projection.at("frames"))if(frame.at("key")==entry.at("key")){need(!found&&frame.at("targets")==entry.at("targets")&&frame.at("source_equivalence_class")==entry.at("equivalence_class"),"native projection differs from original frozen binding");found=true;}need(found,"native projection invented a slot");}
  }
 }
 return entries;
}
// The source_rect is a lossless crop of the original pinned PNG, not a new source.
// vertices_a/vertices_b are ROI-local, in original native source pixel units.
inline MotionSource motion_source(const Json& j,const Json& key,const std::map<std::string,Json>& native,MotionSourceCache& cache,bool allowReframe=false,bool allowRegistered=false){
 MotionSource s;s.file=clean_path(path(j.at("path")));s.hash=j.at("sha256");need(valid_sha(j.at("sha256")),"motion source SHA required");s.key=j.at("key");
 need(native.contains(s.key.dump())&&s.key[1]==key[1],"appearance source must bind a native slot of the same creature; share motion templates, not textures");
 auto targetIt=native.find(key.dump());need(targetIt!=native.end(),"motion target native slot is missing");
 auto geometry=native.at(s.key.dump()).at("targets");auto crop=pair(geometry.at("crop_1x"),1,"source crop");s.origin=pair(geometry.at("origin_1x"),0,"source origin");
 if(j.contains("source_layout"))need(j.at("source_layout").is_string(),"MOTION_SOURCE_LAYOUT_UNSUPPORTED");
 const auto layout=j.value("source_layout",std::string());
 if(!layout.empty())need(layout=="integer-transparent-padding"||layout=="explicit-native-roi"||layout=="rational-native-grid","MOTION_SOURCE_LAYOUT_UNSUPPORTED");
 s.registered=!layout.empty();auto name=folded(s.file);
 if(cache.contains(name)){const auto& meta=cache.at(name);need(meta.hash==s.hash,"conflicting source hash within motion batch");s.rawW=meta.w;s.rawH=meta.h;}
 else{auto raw=read_all(s.file);need(sha(raw)==s.hash,"motion source drift");need(raw.size()>=24&&!std::memcmp(raw.data(),"\x89PNG\r\n\x1a\n",8),"motion source must be PNG");Wic decoder;auto meta=decoder.frame(raw);s.rawW=int(meta.w);s.rawH=int(meta.h);cache.emplace(name,MotionSourceMeta{s.hash,s.rawW,s.rawH,{}});}
 need(s.rawW>0&&s.rawH>0&&s.rawW<=MAX_AXIS&&s.rawH<=MAX_AXIS&&std::uint64_t(s.rawW)*s.rawH<=MAX_PIXELS,"motion source pixels exceed bounded native source budget");
 need(j.at("pixels")==Json::array({s.rawW,s.rawH}),"motion source dimensions changed");
 auto rect4=[](const Json& value,const char* label){need(value.is_array()&&value.size()==4,std::string(label)+" requires x,y,width,height");return std::array<int,4>{integer(value[0],0,MAX_AXIS,label),integer(value[1],0,MAX_AXIS,label),integer(value[2],1,MAX_AXIS,label),integer(value[3],1,MAX_AXIS,label)};};
 if(layout=="explicit-native-roi"){
  need(allowRegistered&&!allowReframe&&!j.contains("padding_registration")&&!j.contains("native_scale")&&!j.contains("source_rect")&&j.contains("source_region"),"MOTION_EXPLICIT_REGION_REQUIRES_PINNED_MESH_INPUT");
  need(s.key[2]==key[2],"MOTION_EXPLICIT_REGION_SOURCE_GROUP_MISMATCH");
  const auto& region=j.at("source_region");motion_fields(region,{"pixel_rect","native_rect_1x","allow_target_subregion"});
  bool allowSubregion=region.contains("allow_target_subregion")&&region.at("allow_target_subregion").is_boolean()&&region.at("allow_target_subregion").get<bool>();
  s.roi=rect4(region.at("pixel_rect"),"source region pixel rect");s.sourceNativeRect=rect4(region.at("native_rect_1x"),"source region native rect");
  need(std::int64_t(s.roi[0])+s.roi[2]<=s.rawW&&std::int64_t(s.roi[1])+s.roi[3]<=s.rawH,"MOTION_EXPLICIT_REGION_PIXEL_BOUNDS_INVALID");
  auto parentCanvas=pair(geometry.at("canvas_1x"),1,"source canvas");
  need(s.sourceNativeRect[0]+s.sourceNativeRect[2]<=parentCanvas[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=parentCanvas[1],"MOTION_EXPLICIT_REGION_OUTSIDE_SOURCE_CANVAS");
  need(s.sourceNativeRect[0]>=s.origin[0]&&s.sourceNativeRect[1]>=s.origin[1]&&s.sourceNativeRect[0]+s.sourceNativeRect[2]<=s.origin[0]+crop[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=s.origin[1]+crop[1],"MOTION_EXPLICIT_REGION_OUTSIDE_SOURCE_CROP");
  const auto& target=targetIt->second.at("targets");auto targetCrop=pair(target.at("crop_1x"),1,"target crop"),targetOrigin=pair(target.at("origin_1x"),0,"target origin");
  if(allowSubregion){
   need(target.at("canvas_1x")==geometry.at("canvas_1x")&&s.sourceNativeRect[0]>=targetOrigin[0]&&s.sourceNativeRect[1]>=targetOrigin[1]&&s.sourceNativeRect[0]+s.sourceNativeRect[2]<=targetOrigin[0]+targetCrop[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=targetOrigin[1]+targetCrop[1],"MOTION_EXPLICIT_REGION_DOES_NOT_MATCH_TARGET_NATIVE_CROP");
  }else{
   need(target.at("canvas_1x")==geometry.at("canvas_1x")&&s.sourceNativeRect[0]==targetOrigin[0]&&s.sourceNativeRect[1]==targetOrigin[1]&&s.sourceNativeRect[2]==targetCrop[0]&&s.sourceNativeRect[3]==targetCrop[1],"MOTION_EXPLICIT_REGION_DOES_NOT_MATCH_TARGET_NATIVE_CROP");
  }
  s.scaleX=double(s.roi[2])/s.sourceNativeRect[2];s.scaleY=double(s.roi[3])/s.sourceNativeRect[3];
  need(s.scaleX>=3&&s.scaleY>=3&&s.scaleX<=MAX_AXIS&&s.scaleY<=MAX_AXIS,"MOTION_EXPLICIT_REGION_SOURCE_BELOW_3X_OR_OVER_LIMIT");
  double anisotropy=std::max(s.scaleX/s.scaleY,s.scaleY/s.scaleX);need(anisotropy<=4.0,"MOTION_EXPLICIT_REGION_ANISOTROPY_EXCEEDS_LIMIT");
  s.scale=std::max(3,int(std::floor(std::min(s.scaleX,s.scaleY))));s.origin={s.sourceNativeRect[0],s.sourceNativeRect[1]};s.w=s.roi[2];s.h=s.roi[3];s.hasRoi=true;s.explicitRegion=true;return s;
 }
 if(layout=="rational-native-grid"){
  need(allowRegistered&&!allowReframe&&!j.contains("padding_registration")&&!j.contains("native_scale")&&!j.contains("source_rect")&&j.contains("source_region"),"MOTION_RATIONAL_REGION_REQUIRES_PINNED_MESH_INPUT");
  need(s.key[2]==key[2],"MOTION_RATIONAL_REGION_SOURCE_GROUP_MISMATCH");
  const auto& region=j.at("source_region");motion_fields(region,{"native_rect_1x","pixels_per_native_ratio","allow_target_subregion"});
  bool allowSubregion=region.contains("allow_target_subregion")&&region.at("allow_target_subregion").is_boolean()&&region.at("allow_target_subregion").get<bool>();
  s.sourceNativeRect=rect4(region.at("native_rect_1x"),"rational source native rect");auto [numerator,denominator]=motion_ratio(region.at("pixels_per_native_ratio"));s.rationalGrid={numerator,denominator};
  auto parentCanvas=pair(geometry.at("canvas_1x"),1,"rational source canvas");
  need((std::int64_t(parentCanvas[0])*numerator)%denominator==0&&(std::int64_t(parentCanvas[1])*numerator)%denominator==0&&
       std::int64_t(parentCanvas[0])*numerator/denominator==s.rawW&&std::int64_t(parentCanvas[1])*numerator/denominator==s.rawH,
       "MOTION_RATIONAL_GRID_PARENT_SIZE_MISMATCH");
  need(s.sourceNativeRect[0]+s.sourceNativeRect[2]<=parentCanvas[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=parentCanvas[1]&&
       s.sourceNativeRect[0]>=s.origin[0]&&s.sourceNativeRect[1]>=s.origin[1]&&
       s.sourceNativeRect[0]+s.sourceNativeRect[2]<=s.origin[0]+crop[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=s.origin[1]+crop[1],
       "MOTION_RATIONAL_REGION_OUTSIDE_SOURCE_CROP");
  const auto& target=targetIt->second.at("targets");auto targetCrop=pair(target.at("crop_1x"),1,"rational target crop"),targetOrigin=pair(target.at("origin_1x"),0,"rational target origin");
  if(allowSubregion)need(target.at("canvas_1x")==geometry.at("canvas_1x")&&s.sourceNativeRect[0]>=targetOrigin[0]&&s.sourceNativeRect[1]>=targetOrigin[1]&&
       s.sourceNativeRect[0]+s.sourceNativeRect[2]<=targetOrigin[0]+targetCrop[0]&&s.sourceNativeRect[1]+s.sourceNativeRect[3]<=targetOrigin[1]+targetCrop[1],
       "MOTION_RATIONAL_REGION_DOES_NOT_MATCH_TARGET_NATIVE_CROP");
  else need(target.at("canvas_1x")==geometry.at("canvas_1x")&&s.sourceNativeRect[0]==targetOrigin[0]&&s.sourceNativeRect[1]==targetOrigin[1]&&
       s.sourceNativeRect[2]==targetCrop[0]&&s.sourceNativeRect[3]==targetCrop[1],"MOTION_RATIONAL_REGION_DOES_NOT_MATCH_TARGET_NATIVE_CROP");
  auto leftN=std::int64_t(s.sourceNativeRect[0])*numerator,topN=std::int64_t(s.sourceNativeRect[1])*numerator;
  auto rightN=std::int64_t(s.sourceNativeRect[0]+s.sourceNativeRect[2])*numerator,bottomN=std::int64_t(s.sourceNativeRect[1]+s.sourceNativeRect[3])*numerator;
  auto ceilDiv=[&](std::int64_t n){return (n+denominator-1)/denominator;};int left=int(leftN/denominator),top=int(topN/denominator),right=int(ceilDiv(rightN)),bottom=int(ceilDiv(bottomN));
  need(left>=0&&top>=0&&right<=s.rawW&&bottom<=s.rawH&&right>left&&bottom>top,"MOTION_RATIONAL_REGION_PIXEL_ENVELOPE_INVALID");
  s.roi={left,top,right-left,bottom-top};s.rationalRect={double(leftN)/denominator,double(topN)/denominator,
       double(std::int64_t(s.sourceNativeRect[2])*numerator)/denominator,double(std::int64_t(s.sourceNativeRect[3])*numerator)/denominator};
  s.rationalOffset={double(leftN%denominator)/denominator,double(topN%denominator)/denominator};s.scaleX=s.scaleY=double(numerator)/denominator;
  bool wholeTargetCrop=s.sourceNativeRect[0]==targetOrigin[0]&&s.sourceNativeRect[1]==targetOrigin[1]&&s.sourceNativeRect[2]==targetCrop[0]&&s.sourceNativeRect[3]==targetCrop[1];s.rationalWholeCrop=wholeTargetCrop;
  auto cropKey=s.key.dump()+":"+std::to_string(targetOrigin[0])+":"+std::to_string(targetOrigin[1])+":"+std::to_string(targetCrop[0])+":"+std::to_string(targetCrop[1]);
  auto& audits=cache.at(name).nativeCropAlphaAudit;
  if(wholeTargetCrop){
   if(!audits.contains(cropKey)){
    auto sourceBytes=read_all(s.file);need(sha(sourceBytes)==s.hash,"motion source drift before native crop alpha audit");std::string note;Wic decoder;auto full=decoder.decode(sourceBytes,true,note);
    std::uint64_t outsidePositive=0,visibleOutside=0;int maxOutside=0;double x0=s.rationalRect[0],y0=s.rationalRect[1],x1=x0+s.rationalRect[2],y1=y0+s.rationalRect[3];
    for(int y=0;y<full.h;++y)for(int x=0;x<full.w;++x){auto alpha=full.p[std::size_t(y)*full.w+x].a;int alpha8=std::clamp(int(std::lround(alpha*255.f)),0,255);if(alpha8>0&&(x+.5<x0-1e-12||x+.5>=x1-1e-12||y+.5<y0-1e-12||y+.5>=y1-1e-12)){++outsidePositive;maxOutside=std::max(maxOutside,alpha8);visibleOutside+=alpha8>=16;}}
    need(visibleOutside==0,"MOTION_RATIONAL_SOURCE_HAS_VISIBLE_ALPHA_OUTSIDE_NATIVE_CROP: pixels="+std::to_string(visibleOutside)+" threshold_alpha8=16");audits.emplace(cropKey,std::pair<std::uint64_t,int>{outsidePositive,maxOutside});
   }
   s.rationalOutsidePositive=audits.at(cropKey).first;s.rationalOutsideMaxAlpha=audits.at(cropKey).second;
  }
  s.scale=std::max(3,int(std::floor(s.scaleX)));s.origin={s.sourceNativeRect[0],s.sourceNativeRect[1]};s.w=s.roi[2];s.h=s.roi[3];
  s.hasRoi=true;s.registered=true;s.rationalRegion=true;return s;
 }
 if(layout=="integer-transparent-padding"){
  need(allowRegistered&&!allowReframe&&!j.contains("source_rect")&&!j.contains("source_region"),"MOTION_REGISTERED_SOURCE_REQUIRES_MESH_INPUT");
  const auto& registration=j.at("padding_registration");motion_fields(registration,{"common_k","offset"});
  s.scale=integer(registration.at("common_k"),3,MAX_AXIS,"registered source common_k");
  s.padding=pair(registration.at("offset"),0,"registered source offset");
  if(j.contains("native_scale"))need(integer(j.at("native_scale"),3,MAX_AXIS,"source native scale")==s.scale,"MOTION_REGISTERED_SCALE_ECHO_MISMATCH");
 }else{need(!j.contains("padding_registration")&&!j.contains("source_region"),"MOTION_REGISTRATION_REQUIRES_EXPLICIT_LAYOUT");s.scale=integer(j.at("native_scale"),3,32,"source native scale");}
 s.scaleX=s.scaleY=s.scale;
 s.w=s.rawW;s.h=s.rawH;
 if(s.registered){
  s.w=crop[0]*s.scale;s.h=crop[1]*s.scale;
  need(s.w<=MAX_AXIS&&s.h<=MAX_AXIS&&std::uint64_t(s.w)*s.h<=MAX_PIXELS,"MOTION_REGISTERED_SOURCE_PIXEL_LIMIT");
  need(std::int64_t(s.padding[0])+s.rawW<=s.w&&std::int64_t(s.padding[1])+s.rawH<=s.h,"MOTION_REGISTRATION_WOULD_CLIP_SOURCE");
  return s;
 }
 if(j.contains("source_rect")){
  need(!allowReframe,"source_rect is only for mesh/interpolate image sources");
  auto rect=j.at("source_rect");need(rect.is_array()&&rect.size()==4,"source_rect requires x,y,width,height");
  s.roi={integer(rect[0],0,s.rawW,"source_rect x"),integer(rect[1],0,s.rawH,"source_rect y"),integer(rect[2],1,s.rawW,"source_rect width"),integer(rect[3],1,s.rawH,"source_rect height")};
  need(s.roi[2]==crop[0]*s.scale&&s.roi[3]==crop[1]*s.scale,"source_rect must equal exact native scaled crop");
  need(std::int64_t(s.roi[0])+s.roi[2]<=s.rawW&&std::int64_t(s.roi[1])+s.roi[3]<=s.rawH,"source_rect outside pinned full PNG");
  s.hasRoi=true;s.w=s.roi[2];s.h=s.roi[3];
  auto& proof=cache.at(name).validatedZeroOutside;
  if(!proof.contains(s.roi)){
   auto raw=read_all(s.file);need(sha(raw)==s.hash,"motion source drift before ROI alpha proof");
   Wic decoder;auto pixels=decoder.decode_rgba(raw,s.rawW,s.rawH);
   for(int y=0;y<s.rawH;y++)for(int x=0;x<s.rawW;x++)
    if((x<s.roi[0]||x>=s.roi[0]+s.w||y<s.roi[1]||y>=s.roi[1]+s.h)&&pixels[(std::size_t(y)*s.rawW+x)*4+3]!=0)
     need(false,"NEEDS_KEYFRAME: positive alpha outside strict source_rect");
   proof.insert(s.roi);
  }
 }
 need((allowReframe&&s.scale==3&&!s.hasRoi)||(s.w==crop[0]*s.scale&&s.h==crop[1]*s.scale),"source must be an exact native crop at its declared integer scale");
 return s;
}
inline Image motion_crop_strict_source(const Image& full,const MotionSource& s){
 need(full.w==s.rawW&&full.h==s.rawH,"motion pinned source dimensions changed before sampling");
 if(s.explicitRegion){Image roi(s.w,s.h);for(int y=0;y<s.h;y++)std::copy_n(full.p.begin()+std::size_t(y+s.roi[1])*full.w+s.roi[0],s.w,roi.p.begin()+std::size_t(y)*s.w);return roi;}
 if(s.rationalRegion){Image roi(s.w,s.h);for(int y=0;y<s.h;y++)std::copy_n(full.p.begin()+std::size_t(y+s.roi[1])*full.w+s.roi[0],s.w,roi.p.begin()+std::size_t(y)*s.w);
  const double x0=s.rationalRect[0],y0=s.rationalRect[1],x1=x0+s.rationalRect[2],y1=y0+s.rationalRect[3];
  for(int y=0;y<s.h;y++)for(int x=0;x<s.w;x++){double sx=s.roi[0]+x+.5,sy=s.roi[1]+y+.5;if(sx<x0-1e-12||sx>=x1-1e-12||sy<y0-1e-12||sy>=y1-1e-12)roi.p[std::size_t(y)*s.w+x]=Pixel{};}
  return roi;}
 if(s.registered){
  Image registered(s.w,s.h);
  for(int y=0;y<full.h;y++)std::copy_n(full.p.begin()+std::size_t(y)*full.w,full.w,registered.p.begin()+std::size_t(y+s.padding[1])*s.w+s.padding[0]);
  return registered;
 }
 if(!s.hasRoi)return full;
 for(int y=0;y<full.h;y++)for(int x=0;x<full.w;x++)
  if((x<s.roi[0]||x>=s.roi[0]+s.w||y<s.roi[1]||y>=s.roi[1]+s.h)&&full.p[std::size_t(y)*full.w+x].a>0)
   need(false,"NEEDS_KEYFRAME: positive alpha outside strict source_rect");
 Image roi(s.w,s.h);
 for(int y=0;y<s.h;y++)std::copy_n(full.p.begin()+std::size_t(y+s.roi[1])*full.w+s.roi[0],s.w,roi.p.begin()+std::size_t(y)*s.w);
 return roi;
}
struct MotionAffine {double a=1,b=0,c=0,d=1,tx=0,ty=0;};
inline MotionAffine motion_affine_compose(const MotionAffine& a,const MotionAffine& b){
 return {a.a*b.a+a.b*b.c,a.a*b.b+a.b*b.d,a.c*b.a+a.d*b.c,a.c*b.b+a.d*b.d,
         a.a*b.tx+a.b*b.ty+a.tx,a.c*b.tx+a.d*b.ty+a.ty};
}
inline MotionPoint motion_affine_point(const MotionAffine& m,MotionPoint p){return {m.a*p.x+m.b*p.y+m.tx,m.c*p.x+m.d*p.y+m.ty};}
inline std::vector<MotionPoint> motion_skeleton_target(const Json& rig,const Json& pose,
                                                        const std::vector<MotionPoint>& rest,
                                                        std::array<int,2> canvas,
                                                        std::vector<std::array<double,3>>* outGains=nullptr){
 motion_fields(rig,{"bones","vertex_weights"});
 need(rig.at("bones").is_array()&&!rig.at("bones").empty()&&rig.at("bones").size()<=32,"SKELETON_BONE_COUNT_INVALID");
 need(pose.is_object(),"SKELETON_POSE_OBJECT_REQUIRED");
 struct Bone {std::string id;int parent=-1;MotionPoint pivot;MotionAffine world;std::array<double,3> gain{1,1,1};};
 std::vector<Bone> bones;std::map<std::string,int> byName;
 for(std::size_t i=0;i<rig.at("bones").size();++i){
  const auto& value=rig.at("bones")[i];motion_fields(value,{"id","parent","pivot_1x"});
  Bone bone;bone.id=token(value.at("id"));bone.pivot=motion_point(value.at("pivot_1x"));
  need(bone.pivot.x>=0&&bone.pivot.y>=0&&bone.pivot.x<=canvas[0]&&bone.pivot.y<=canvas[1],"SKELETON_PIVOT_OUTSIDE_CANVAS");
  if(!value.at("parent").is_null()){
   auto parent=token(value.at("parent"));need(byName.contains(parent),"SKELETON_PARENT_MUST_PRECEDE_CHILD");bone.parent=byName.at(parent);
  }
  need(!byName.contains(bone.id),"SKELETON_BONE_ID_DUPLICATE");byName.emplace(bone.id,int(i));
  MotionAffine local;const auto found=pose.find(bone.id);
  if(found!=pose.end()){
   motion_fields(*found,{"rotation_degrees","translation_1x","scale","rgb_gain"});
   double angle=motion_real(found->value("rotation_degrees",0.0),"skeleton rotation")*std::numbers::pi/180.0;
   need(angle>=-std::numbers::pi-1e-9&&angle<=std::numbers::pi+1e-9,"SKELETON_ROTATION_OUT_OF_RANGE");
   MotionPoint translation=found->contains("translation_1x")?motion_point(found->at("translation_1x")):MotionPoint{};
   MotionPoint scale=found->contains("scale")?motion_point(found->at("scale")):MotionPoint{1,1};
   need(scale.x>=.25&&scale.x<=4&&scale.y>=.25&&scale.y<=4,"SKELETON_SCALE_OUT_OF_RANGE");
   if(found->contains("rgb_gain")){
    const auto& g=found->at("rgb_gain");need(g.is_array()&&g.size()==3,"SKELETON_RGB_GAIN_ARRAY_REQUIRED");
    for(int c=0;c<3;++c){bone.gain[c]=motion_real(g[c],"skeleton rgb_gain");need(bone.gain[c]>=.5&&bone.gain[c]<=2,"SKELETON_RGB_GAIN_OUT_OF_RANGE");}
   }
   double co=std::cos(angle),si=std::sin(angle);local.a=co*scale.x;local.b=-si*scale.y;local.c=si*scale.x;local.d=co*scale.y;
   local.tx=bone.pivot.x+translation.x-local.a*bone.pivot.x-local.b*bone.pivot.y;
   local.ty=bone.pivot.y+translation.y-local.c*bone.pivot.x-local.d*bone.pivot.y;
  }
  if(bone.parent>=0)for(int c=0;c<3;++c){bone.gain[c]*=bones[bone.parent].gain[c];need(bone.gain[c]>=.5-1e-9&&bone.gain[c]<=2+1e-9,"SKELETON_RGB_GAIN_OUT_OF_RANGE");}
  bone.world=bone.parent<0?local:motion_affine_compose(bones[bone.parent].world,local);
  bones.push_back(std::move(bone));
 }
 for(auto it=pose.begin();it!=pose.end();++it)need(byName.contains(it.key()),"SKELETON_POSE_UNKNOWN_BONE: "+it.key());
 const auto& weights=rig.at("vertex_weights");need(weights.is_array()&&weights.size()==rest.size(),"SKELETON_VERTEX_WEIGHT_COUNT_MISMATCH");
 std::vector<MotionPoint> target;target.reserve(rest.size());
 std::vector<std::array<double,3>> gains;if(outGains)gains.reserve(rest.size());
 bool hasGain=false;
 for(std::size_t i=0;i<rest.size();++i){
  const auto& row=weights[i];need(row.is_array()&&row.size()==bones.size(),"SKELETON_VERTEX_WEIGHT_WIDTH_MISMATCH");
  double total=0,x=0,y=0;std::array<double,3> g{0,0,0};
  for(std::size_t b=0;b<bones.size();++b){
   double weight=motion_real(row[b],"skeleton vertex weight");need(weight>=0&&weight<=1,"SKELETON_VERTEX_WEIGHT_OUT_OF_RANGE");
   auto point=motion_affine_point(bones[b].world,rest[i]);total+=weight;x+=point.x*weight;y+=point.y*weight;
   for(int c=0;c<3;++c)g[c]+=bones[b].gain[c]*weight;
  }
  need(std::abs(total-1)<=1e-6,"SKELETON_VERTEX_WEIGHTS_MUST_SUM_TO_ONE");target.push_back({x,y});
  for(int c=0;c<3;++c)hasGain|=std::abs(g[c]-1)>1e-9;
  if(outGains)gains.push_back(g);
 }
 if(outGains){if(hasGain)*outGains=std::move(gains);else outGains->clear();}
 return target;
}
inline void motion_rigid_component(const MotionLayer& layer,const MotionJob& job,const Json& contract,const Json& sourceGeometry,MotionSourceCache& cache){
 need(job.algorithm==MOTION_ALGORITHM&&job.anchoredSurface&&job.mode=="mesh"&&!layer.paired&&!layer.posePaired&&layer.gainA.empty(),"MOTION_COMPONENT_REQUIRES_V6_STATIC_SURFACE");
 motion_fields(contract,{"schema","receipt","quarter_turns_clockwise","target_rect_1x"});
 need(contract.at("schema")=="h3.motion.rigid-component.v1","MOTION_COMPONENT_CONTRACT_REQUIRED");
 need(layer.a.explicitRegion&&std::abs(layer.a.scaleX-layer.a.scaleY)<=1e-8,"MOTION_COMPONENT_REQUIRES_UNIFORM_REAL_SOURCE_GRID");
 const auto& binding=contract.at("receipt");need(valid_sha(binding.at("sha256")),"MOTION_COMPONENT_RECEIPT_SHA_REQUIRED");
 auto raw=read_all(path(binding.at("path")),4*1024*1024);need(sha(raw)==binding.at("sha256").get<std::string>(),"MOTION_COMPONENT_RECEIPT_DRIFT");auto proof=parse_json(raw);
 need(proof.at("schema")=="h3.tile-interface.component-receipt.v1"&&proof.at("native_key")==layer.a.key,"MOTION_COMPONENT_RECEIPT_IDENTITY_DIFFERS");
 need(proof.at("geometry")==sourceGeometry,"MOTION_COMPONENT_NATIVE_GEOMETRY_DIFFERS");
 need(proof.at("component_image").at("sha256")==layer.a.hash&&proof.at("pixel_permutation_only")==true&&proof.at("resampling_performed")==false&&proof.at("RGB_averaged")==false&&proof.at("extrusion_performed")==false,"MOTION_COMPONENT_MUST_BE_PIXEL_PRESERVING");
 need(proof.at("component_rect_1x")==Json(layer.a.sourceNativeRect)&&motion_real(proof.at("pixels_per_native"),"component source scale")==layer.a.scaleX,"MOTION_COMPONENT_SOURCE_RECT_DIFFERS");
 const auto& parent=proof.at("source");need(valid_sha(parent.at("sha256")),"MOTION_COMPONENT_PARENT_SHA_REQUIRED");auto parentBytes=read_all(path(parent.at("path")));need(sha(parentBytes)==parent.at("sha256").get<std::string>(),"MOTION_COMPONENT_PARENT_DRIFT");
 auto componentRect=[](const Json& v,const char* label){need(v.is_array()&&v.size()==4,std::string(label)+" requires four integers");return std::array<int,4>{integer(v[0],0,MAX_AXIS,label),integer(v[1],0,MAX_AXIS,label),integer(v[2],1,MAX_AXIS,label),integer(v[3],1,MAX_AXIS,label)};};
 auto& verified=cache.at(folded(layer.a.file)).validatedComponents;auto proofSha=binding.at("sha256").get<std::string>();
 if(!verified.contains(proofSha)){
  int k=integer(proof.at("pixels_per_native"),4,128,"component native density");auto half=componentRect(proof.at("half_rect_1x"),"component half rectangle");auto dst=componentRect(proof.at("component_rect_1x"),"component destination rectangle");
  need(dst[2]==half[2]*2&&dst[3]==half[3],"MOTION_COMPONENT_HALF_LAYOUT_DIFFERS");auto parentPixels=pair(parent.at("pixels"),1,"component parent pixels");auto canvas=pair(sourceGeometry.at("canvas_1x"),1,"component canvas");
  need(parentPixels[0]==layer.a.rawW&&parentPixels[1]==layer.a.rawH&&std::int64_t(canvas[0])*k==layer.a.rawW&&std::int64_t(canvas[1])*k==layer.a.rawH,"MOTION_COMPONENT_FULL_SOURCE_GRID_DIFFERS");
  need(std::int64_t(half[0]+half[2])*k<=parentPixels[0]&&std::int64_t(half[1]+half[3])*k<=parentPixels[1]&&std::int64_t(dst[0]+dst[2])*k<=layer.a.rawW&&std::int64_t(dst[1]+dst[3])*k<=layer.a.rawH,"MOTION_COMPONENT_REPLAY_OUTSIDE_SOURCE");
  Wic decoder;auto original=decoder.decode_rgba(parentBytes,parentPixels[0],parentPixels[1]);auto componentBytes=read_all(layer.a.file);need(sha(componentBytes)==layer.a.hash,"MOTION_COMPONENT_IMAGE_DRIFT");auto candidate=decoder.decode_rgba(componentBytes,layer.a.rawW,layer.a.rawH);
  for(int y=0;y<layer.a.rawH;++y)for(int x=0;x<layer.a.rawW;++x){bool inside=x>=dst[0]*k&&x<(dst[0]+dst[2])*k&&y>=dst[1]*k&&y<(dst[1]+dst[3])*k;size_t at=(std::size_t(y)*layer.a.rawW+x)*4;
   if(!inside){for(int c=0;c<4;++c)need(candidate[at+c]==0,"MOTION_COMPONENT_UNDECLARED_PIXELS_OUTSIDE_RECT");continue;}
   int u=x-dst[0]*k;int sx=half[0]*k+(u<half[2]*k?u:half[2]*2*k-1-u),sy=half[1]*k+y-dst[1]*k;size_t from=(std::size_t(sy)*parentPixels[0]+sx)*4;
   for(int c=0;c<4;++c)need(candidate[at+c]==original[from+c],"MOTION_COMPONENT_PIXEL_PERMUTATION_REPLAY_DIFFERS");
  }
  verified.insert(proofSha);
 }
 int turn=integer(contract.at("quarter_turns_clockwise"),0,3,"component quarter turn");auto rect=componentRect(contract.at("target_rect_1x"),"component target rectangle");
 double w=layer.a.sourceNativeRect[2],h=layer.a.sourceNativeRect[3];need(rect[2]==(turn%2?h:w)&&rect[3]==(turn%2?w:h),"MOTION_COMPONENT_NONUNIFORM_OR_SCALED_PLACEMENT");
 need(layer.va.size()==4&&layer.target.size()==4&&layer.triangles==std::vector<MotionTriangle>{{0,1,2},{1,3,2}},"MOTION_COMPONENT_EXACT_RECTANGLE_MESH_REQUIRED");
 const std::array<MotionPoint,4> local={MotionPoint{0,0},MotionPoint{w,0},MotionPoint{0,h},MotionPoint{w,h}};
 for(size_t i=0;i<4;++i){auto p=local[i];need(std::hypot(layer.va[i].x-p.x*layer.a.scaleX,layer.va[i].y-p.y*layer.a.scaleY)<=1e-8,"MOTION_COMPONENT_UV_MUST_COVER_DECLARED_RECT");MotionPoint rotated;
  if(turn==0)rotated=p;else if(turn==1)rotated={h-p.y,p.x};else if(turn==2)rotated={w-p.x,h-p.y};else rotated={p.y,w-p.x};
  need(std::hypot(layer.target[i].x-rect[0]-rotated.x,layer.target[i].y-rect[1]-rotated.y)<=1e-8,"MOTION_COMPONENT_RIGID_PLACEMENT_DIFFERS");
 }
}
inline void motion_component_currentness(const MotionJob& job){
 for(const auto& layer:job.entry.at("layers"))if(layer.at("kind")=="component"){
  const auto& binding=layer.at("component_contract").at("receipt");auto raw=read_all(path(binding.at("path")),4*1024*1024);need(sha(raw)==binding.at("sha256").get<std::string>(),"MOTION_COMPONENT_RECEIPT_DRIFT");auto proof=parse_json(raw);const auto& source=proof.at("source");auto parent=read_all(path(source.at("path")));need(sha(parent)==source.at("sha256").get<std::string>(),"MOTION_COMPONENT_PARENT_DRIFT");
 }
}
inline void motion_mesh_validate(MotionLayer& layer,const MotionJob& job){
 need(layer.target.size()==layer.va.size()&&(!layer.paired||layer.vb.size()==layer.va.size()),"corresponding vertex count differs");
 auto bounds=[](const std::vector<MotionPoint>& points,int w,int h){for(auto p:points)need(p.x>=0&&p.y>=0&&p.x<=w&&p.y<=h,"mesh UV outside input image");};bounds(layer.va,layer.a.w,layer.a.h);if(layer.paired)bounds(layer.vb,layer.b.w,layer.b.h);
 for(auto p:layer.target)need(p.x*job.targetScale>=job.origin[0]-1e-8&&p.y*job.targetScale>=job.origin[1]-1e-8&&p.x*job.targetScale<=job.origin[0]+job.w+1e-8&&p.y*job.targetScale<=job.origin[1]+job.h+1e-8,"NEEDS_KEYFRAME: deformed mesh exceeds original crop");
 std::set<MotionTriangle> unique;
 for(auto triangle:layer.triangles){
  auto sorted=triangle;std::sort(sorted.begin(),sorted.end());need(unique.insert(sorted).second&&sorted[0]!=sorted[1]&&sorted[1]!=sorted[2],"duplicate/degenerate mesh triangle");
  auto i=triangle[0],j=triangle[1],k=triangle[2];double source=motion_area(layer.va[i],layer.va[j],layer.va[k])/(layer.a.scaleX*layer.a.scaleY),target=motion_area(layer.target[i],layer.target[j],layer.target[k]);
  need(source>1e-7&&target>1e-7,"NEEDS_KEYFRAME: folded or degenerate triangle");need(target/source>=.2&&target/source<=5,"NEEDS_KEYFRAME: excessive triangle area change");
  if(layer.paired){double other=motion_area(layer.vb[i],layer.vb[j],layer.vb[k])/(layer.b.scaleX*layer.b.scaleY);need(other>1e-7&&target/other>=.2&&target/other<=5,"NEEDS_KEYFRAME: incompatible second keyframe topology");}
  auto edges=[&](const std::vector<MotionPoint>& uv,const MotionSource& source){for(int n=0;n<3;n++){int a=triangle[n],b=triangle[(n+1)%3];double length=std::hypot((uv[a].x-uv[b].x)/source.scaleX,(uv[a].y-uv[b].y)/source.scaleY),changed=std::hypot(layer.target[a].x-layer.target[b].x,layer.target[a].y-layer.target[b].y);need(length>1e-7&&changed/length>=.25-1e-9&&changed/length<=2.5+1e-9,"NEEDS_KEYFRAME: excessive mesh edge stretch");}};edges(layer.va,layer.a);if(layer.paired)edges(layer.vb,layer.b);
 }
 for(auto& anchor:layer.anchors){int vertex=integer(anchor.at("vertex"),0,int(layer.target.size())-1,"anchor vertex");auto expected=motion_point(anchor.at("target_1x"));double tolerance=motion_real(anchor.value("tolerance_3x",.25),"anchor tolerance");need(tolerance>=0&&tolerance<=1,"anchor tolerance 0..1 exact3X pixel");auto p=layer.target[vertex];need(std::hypot((p.x-expected.x)*3,(p.y-expected.y)*3)<=tolerance,"NEEDS_KEYFRAME: pose/foot anchor moved");}
 if(job.anchoredSurface&&layer.kind=="surface"){
  need(layer.a.registered&&(!layer.paired||layer.b.registered),"MOTION_SURFACE_REQUIRES_REGISTERED_SOURCE");
  std::set<int> fixed;
  for(const auto& anchor:layer.anchors){
   int vertex=integer(anchor.at("vertex"),0,int(layer.va.size())-1,"surface anchor");need(fixed.insert(vertex).second,"MOTION_SURFACE_DUPLICATE_ANCHOR");
   auto expected=motion_point(anchor.at("target_1x"));
    auto bound=[&](const MotionSource& source,const std::vector<MotionPoint>& vertices){auto p=motion_source_native_point(source,vertices[vertex]);need(std::hypot((p.x-expected.x)*3,(p.y-expected.y)*3)<=1e-8,"MOTION_SURFACE_ANCHOR_NOT_SOURCE_BOUND");};
   bound(layer.a,layer.va);if(layer.paired)bound(layer.b,layer.vb);
  }
  need(fixed.size()>=2,"MOTION_SURFACE_FIXED_ANCHORS_REQUIRED");
  auto first=layer.va[*fixed.begin()];bool separated=false;for(int vertex:fixed)separated|=std::hypot((layer.va[vertex].x-first.x)/layer.a.scaleX,(layer.va[vertex].y-first.y)/layer.a.scaleY)>1e-7;
  need(separated,"MOTION_SURFACE_FIXED_ANCHORS_NOT_DISTINCT");
 }
}
inline MotionJob motion_job(const Json& entry,const Json& batch,const Json& settings,const std::map<std::string,Json>& native,MotionSourceCache& cache,std::uint64_t* preflightRasterWork=nullptr){
 MotionJob job;job.id=token(entry.at("id"));job.entry=entry;job.mode=entry.value("mode","mesh");job.algorithm=batch.at("algorithm").get<std::string>();job.targetScale=integer(batch.at("target_scale"),3,32,"motion target scale");need(native.contains(entry.at("key").dump()),"target is not an original bound slot");job.native=native.at(entry.at("key").dump());
 need(entry.at("targets")==job.native.at("targets"),"original target geometry changed");auto crop=pair(entry.at("targets").at("crop_1x"),1,"crop"),origin=pair(entry.at("targets").at("origin_1x"),0,"origin"),canvas=pair(entry.at("targets").at("canvas_1x"),1,"canvas");
 need(std::uint64_t(canvas[0])*job.targetScale<=MAX_AXIS&&std::uint64_t(canvas[1])*job.targetScale<=MAX_AXIS&&std::uint64_t(canvas[0])*job.targetScale*canvas[1]*job.targetScale<=MAX_PIXELS&&std::uint64_t(crop[0])*job.targetScale<=MAX_AXIS&&std::uint64_t(crop[1])*job.targetScale<=MAX_AXIS,"motion target geometry exceeds limits");
 job.w=crop[0]*job.targetScale;job.h=crop[1]*job.targetScale;job.origin={origin[0]*job.targetScale,origin[1]*job.targetScale};
 job.admission=std::uint64_t(job.w)*job.h*96+32ull*1024*1024;
 Json sourceGeometry=Json::array();
 auto add=[&](const MotionSource& s){job.admission+=std::uint64_t(s.w)*s.h*80+fs::file_size(s.file)*3+((s.hasRoi||s.registered)?std::uint64_t(s.rawW)*s.rawH*32:0);Json binding={{"key",s.key},{"targets",native.at(s.key.dump()).at("targets")}};if(s.rationalRegion){auto [n,d]=s.rationalGrid;auto x0=std::int64_t(s.sourceNativeRect[0])*n,y0=std::int64_t(s.sourceNativeRect[1])*n,x1=std::int64_t(s.sourceNativeRect[0]+s.sourceNativeRect[2])*n,y1=std::int64_t(s.sourceNativeRect[1]+s.sourceNativeRect[3])*n;binding["source_region"]={{"source_layout","rational-native-grid"},{"native_rect_1x",s.sourceNativeRect},{"pixels_per_native_ratio",{n,d}},
       {"pixel_rect_rational",{motion_fraction(x0,d),motion_fraction(y0,d),motion_fraction(std::int64_t(s.sourceNativeRect[2])*n,d),motion_fraction(std::int64_t(s.sourceNativeRect[3])*n,d)}},
       {"envelope_pixels",s.roi},{"envelope_offset_rational",{motion_fraction(x0%d,d),motion_fraction(y0%d,d)}},{"scale_xy",{s.scaleX,s.scaleY}},
        {"raw_pixels",{s.rawW,s.rawH}},{"sampled_pixels",{s.w,s.h}},{"whole_target_crop",s.rationalWholeCrop},
        {"outside_crop_alpha_positive_pixels",s.rationalOutsidePositive},{"outside_crop_alpha_max8",s.rationalOutsideMaxAlpha},
        {"outside_crop_visible_threshold8",16},{"outside_crop_visible_pixels_rejected",s.rationalWholeCrop},
        {"outside_region_texel_centers","transparent in sampler working copy"}};}
  else if(s.explicitRegion)binding["source_region"]={{"source_layout","explicit-native-roi"},{"pixel_rect",s.roi},{"native_rect_1x",s.sourceNativeRect},{"scale_xy",{s.scaleX,s.scaleY}},{"raw_pixels",{s.rawW,s.rawH}},{"sampled_pixels",{s.w,s.h}}};
  else if(s.registered)binding["registration"]={{"source_layout","integer-transparent-padding"},{"common_k",s.scale},{"offset",s.padding},{"raw_pixels",{s.rawW,s.rawH}},{"registered_pixels",{s.w,s.h}}};sourceGeometry.push_back(std::move(binding));};
 if(entry.contains("reuse_from")){job.reuse=token(entry.at("reuse_from"));need(job.reuse!=job.id,"self reuse");need(job.mode=="reuse","original duplicate must use reuse mode");}
 else if(job.mode=="reuse"||job.mode=="reframe"){
  need(job.targetScale==3,"reuse/reframe requires exact3X target");job.copy=motion_source(entry.at("source"),entry.at("key"),native,cache,job.mode=="reframe");need(!job.copy.hasRoi,"source_rect is only for mesh/interpolate");need(job.copy.scale==3,"reuse/reframe requires native3X source");
  if(job.mode=="reuse")need(job.copy.w==job.w&&job.copy.h==job.h,"exact reuse requires matching3X crop");
  else{auto rect=entry.at("source_rect");need(rect.is_array()&&rect.size()==4,"reframe source_rect requires x,y,width,height");job.reframe={integer(rect[0],0,job.copy.w,"reframe x"),integer(rect[1],0,job.copy.h,"reframe y"),integer(rect[2],1,job.copy.w,"reframe width"),integer(rect[3],1,job.copy.h,"reframe height")};need(job.reframe[2]==job.w&&job.reframe[3]==job.h&&job.reframe[0]+job.w<=job.copy.w&&job.reframe[1]+job.h<=job.copy.h,"reframe must select one exact original3X crop");}
  need(native.at(job.copy.key.dump()).at("equivalence_class")==job.native.at("equivalence_class")&&native.at(job.copy.key.dump()).at("targets")==job.native.at("targets"),"reuse/reframe is not the same original picture/geometry");add(job.copy);
 }else{
  need(job.mode=="mesh"||job.mode=="interpolate"||job.mode=="skeleton","motion mode must be mesh, interpolate, skeleton, reuse or reframe");need(entry.at("layers").is_array()&&!entry.at("layers").empty()&&entry.at("layers").size()<=16,"motion layers 1..16");bool body=false;
  job.quality=entry.at("quality");auto qualityMode=job.quality.value("mode",std::string("body-foot-volume"));need(qualityMode=="body-foot-volume"||qualityMode=="anchored-surface","MOTION_QUALITY_MODE_UNSUPPORTED");job.anchoredSurface=qualityMode=="anchored-surface";
  if(job.anchoredSurface)motion_fields(job.quality,{"mode","min_pair_alpha_overlap","max_pair_color_error"});
  else{double minHeight=motion_real(job.quality.at("min_height_fraction"),"minimum height"),maxHeight=motion_real(job.quality.at("max_height_fraction"),"maximum height");need(minHeight>=.5&&minHeight<=1&&maxHeight>=1&&maxHeight<=1.5,"body height quality range");double reference=motion_real(job.quality.at("reference_body_height_1x"),"reference body height");need(reference>0&&reference<=canvas[1],"reference body height range");double foot=motion_real(job.quality.at("foot_y_1x"),"foot height"),tolerance=motion_real(job.quality.at("foot_tolerance_1x"),"foot tolerance");need(foot>=origin[1]&&foot<=origin[1]+crop[1]&&tolerance>=0&&tolerance<=2,"foot quality constraint range");}
   double overlap=motion_real(job.quality.value("min_pair_alpha_overlap",.8),"pair alpha overlap"),color=motion_real(job.quality.value("max_pair_color_error",.18),"pair color error");need(overlap>=.5&&overlap<=1&&color>=.01&&color<=.25,"pair alignment constraint range");
   std::set<std::string> names;
  for(auto& l:entry.at("layers")){
   MotionLayer layer;layer.name=token(l.at("id"));need(names.insert(layer.name).second,"duplicate layer ID");layer.kind=l.at("kind");need(layer.kind=="effect"||layer.kind==(job.anchoredSurface?"surface":"body")||(job.anchoredSurface&&layer.kind=="component"),job.anchoredSurface?"MOTION_SURFACE_LAYER_KIND_REQUIRED":"explicit body/effect layer kind required");body|=layer.kind==(job.anchoredSurface?"surface":"body");layer.a=motion_source(l.at("source_a"),entry.at("key"),native,cache,false,true);layer.va=motion_points(l.at("vertices_a"));add(layer.a);
   layer.paired=job.mode=="interpolate"||(job.mode=="skeleton"&&l.contains("source_b"));if(layer.paired){layer.b=motion_source(l.at("source_b"),entry.at("key"),native,cache,false,true);layer.vb=motion_points(l.at("vertices_b"));layer.time=motion_real(entry.at("time"),"interpolation time");need(layer.time>=0&&layer.time<=1,"interpolation time0..1");add(layer.b);}
   need(l.at("triangles").is_array()&&!l.at("triangles").empty()&&l.at("triangles").size()<=1024,"triangle count1..1024");for(auto& t:l.at("triangles")){need(t.is_array()&&t.size()==3,"triangle requires3indices");layer.triangles.push_back({integer(t[0],0,int(layer.va.size())-1,"triangle"),integer(t[1],0,int(layer.va.size())-1,"triangle"),integer(t[2],0,int(layer.va.size())-1,"triangle")});}
    if(job.anchoredSurface){layer.anchors=l.value("anchors",Json::array());need(layer.anchors.is_array()&&layer.anchors.size()<=512,"anchor array");}
    layer.posePaired=l.contains("pose_a_1x")||l.contains("pose_b_1x");
    if(job.mode=="skeleton"){
     need(!layer.posePaired&&!l.contains("target_vertices_1x")&&l.contains("skeleton")&&l.contains("rest_vertices_1x")&&entry.contains("pose"),"skeleton mode requires source texture, rig, rest vertices and explicit pose");
     auto rest=motion_points(l.at("rest_vertices_1x"));need(rest.size()==layer.va.size(),"skeleton rest vertex count differs from source mesh");
      for(std::size_t v=0;v<rest.size();++v){auto sourcePoint=motion_source_native_point(layer.a,layer.va[v],true);need(std::hypot(sourcePoint.x-rest[v].x,sourcePoint.y-rest[v].y)<=1e-8,"SKELETON_REST_VERTEX_NOT_SOURCE_BOUND");}
     if(layer.paired){
      need(l.contains("rest_vertices_b_1x")&&entry.contains("pose_b")&&layer.vb.size()==layer.va.size(),"paired skeleton mode requires rest_vertices_b_1x, pose_b, and matching vertices_b");
      auto restB=motion_points(l.at("rest_vertices_b_1x"));need(restB.size()==layer.vb.size(),"skeleton rest_vertices_b_1x count differs from source mesh");
       for(std::size_t v=0;v<restB.size();++v){auto sourcePoint=motion_source_native_point(layer.b,layer.vb[v],true);need(std::hypot(sourcePoint.x-restB[v].x,sourcePoint.y-restB[v].y)<=1e-8,"SKELETON_REST_VERTEX_B_NOT_SOURCE_BOUND");}
      auto targetA=motion_skeleton_target(l.at("skeleton"),entry.at("pose"),rest,canvas,&layer.gainA);
      auto targetB=motion_skeleton_target(l.value("skeleton_b",l.at("skeleton")),entry.at("pose_b"),restB,canvas,&layer.gainB);
      layer.target=targetA;motion_mesh_validate(layer,job);layer.target=targetB;motion_mesh_validate(layer,job);layer.target.clear();
      for(size_t n=0;n<targetA.size();n++)layer.target.push_back({targetA[n].x*(1-layer.time)+targetB[n].x*layer.time,targetA[n].y*(1-layer.time)+targetB[n].y*layer.time});
     }else{
      layer.target=motion_skeleton_target(l.at("skeleton"),entry.at("pose"),rest,canvas,&layer.gainA);
     }
    }else if(layer.posePaired){need(!layer.paired&&!l.contains("target_vertices_1x"),"pose mesh uses one texture and two explicit poses");layer.time=motion_real(entry.at("time"),"pose interpolation time");need(layer.time>=0&&layer.time<=1,"pose interpolation time0..1");auto a=motion_points(l.at("pose_a_1x")),b=motion_points(l.at("pose_b_1x"));need(a.size()==layer.va.size()&&b.size()==a.size(),"pose vertex correspondence differs");
     for(auto name:{"pose_a_key","pose_b_key"}){auto key=l.at(name);need(native.contains(key.dump())&&key[1]==entry.at("key")[1]&&native.at(key.dump()).at("targets").at("canvas_1x")==entry.at("targets").at("canvas_1x"),"pose key must bind the same original creature and canvas");sourceGeometry.push_back({{"key",key},{"targets",native.at(key.dump()).at("targets")}});}
     auto saved=layer.target;layer.target=a;motion_mesh_validate(layer,job);layer.target=b;motion_mesh_validate(layer,job);layer.target=std::move(saved);for(size_t n=0;n<a.size();n++)layer.target.push_back({a[n].x*(1-layer.time)+b[n].x*layer.time,a[n].y*(1-layer.time)+b[n].y*layer.time});
    }else if(l.contains("target_vertices_1x"))layer.target=motion_points(l.at("target_vertices_1x"));else{need(layer.paired&&layer.va.size()==layer.vb.size(),"mesh target vertices required");for(size_t n=0;n<layer.va.size();n++){auto a=motion_source_native_point(layer.a,layer.va[n]),b=motion_source_native_point(layer.b,layer.vb[n]);layer.target.push_back({a.x*(1-layer.time)+b.x*layer.time,a.y*(1-layer.time)+b.y*layer.time});}}
   layer.anchors=l.value("anchors",Json::array());need(layer.anchors.is_array()&&layer.anchors.size()<=512,"anchor array");motion_mesh_validate(layer,job);
   if(layer.kind=="component")motion_rigid_component(layer,job,l.at("component_contract"),native.at(layer.a.key.dump()).at("targets"),cache);else need(!l.contains("component_contract"),"MOTION_COMPONENT_CONTRACT_ON_NONCOMPONENT_LAYER");
   job.layers.push_back(std::move(layer));
  }
  need(body,job.anchoredSurface?"MOTION_PRIMARY_SURFACE_REQUIRED":"at least one explicit body layer required for foot/volume diagnostics");
 }
 bool registered=false;for(const auto& layer:job.layers)registered|=layer.a.registered||(layer.paired&&layer.b.registered);
 if(registered){
  const auto& budget=entry.at("sampling_budget");motion_fields(budget,{"schema","max_loop_visits"});need(budget.at("schema")=="h3.motion.sampling-budget.v1","MOTION_SAMPLING_BUDGET_SCHEMA_INVALID");
  job.workLimit=integer(budget.at("max_loop_visits"),1,int(MOTION_JOB_WORK_LIMIT),"motion sampling max_loop_visits");motion_admit_sampling(job,preflightRasterWork);
 }else need(!entry.contains("sampling_budget"),"MOTION_SAMPLING_BUDGET_REQUIRES_REGISTERED_SOURCE");
 auto root=clean_path(path(batch.at("output_root")));auto receipts=clean_path(path(batch.at("receipt_directory")));contained(receipts,root);
 job.contract={{"entry",entry},{"native_entry",job.native},{"source_geometry",sourceGeometry},{"algorithm",job.algorithm},{"target_scale",job.targetScale},{"space","linear"},{"png_filter",settings.at("png_filter")},{"output_root",str(root)}};
 if(job.workLimit)job.contract["sampling_work_admission"]=job.estimatedWork.evidence();
 job.fingerprint=sha_text(job.contract.dump());job.output=root/L"frames"/path(job.id)/path(job.fingerprint+".png");job.receipt=receipts/path(job.id+"-"+job.fingerprint+".json");contained(job.output,root);contained(job.receipt,root);return job;
}
inline Json check_motion(const Json& batch,const Json& settings,std::vector<MotionJob>& jobs){
 auto algorithm=batch.at("algorithm").get<std::string>();need(batch.at("schema")=="h3.motion.batch.v1"&&(motion_modern_algorithm(algorithm)||algorithm==MOTION_ALGORITHM_V4),"unsupported motion contract");int targetScale=integer(batch.at("target_scale"),3,32,"motion target scale");if(algorithm==MOTION_ALGORITHM_V4)need(targetScale==3,"legacy motion contract is exact3X only");motion_settings(settings);auto native=motion_native_entries(batch);
 need(batch.at("items").is_array()&&!batch.at("items").empty()&&batch.at("items").size()<=10000,"motion item count1..10000");std::set<std::string> ids,keys;std::map<std::string,size_t> owners;std::set<std::wstring> sources;Json items=Json::array();int blocked=0;
 std::uint64_t registeredWork=0,preflightRasterWork=0;
 MotionSourceCache cache;for(auto& entry:batch.at("items"))try{
  auto job=motion_job(entry,batch,settings,native,cache,&preflightRasterWork);need(job.estimatedWork.used<=MOTION_BATCH_WORK_LIMIT&&registeredWork<=MOTION_BATCH_WORK_LIMIT-job.estimatedWork.used,"MOTION_BATCH_SAMPLING_WORK_LIMIT");need(ids.insert(job.id).second&&keys.insert(entry.at("key").dump()).second,"duplicate job ID/original slot");
  if(!job.reuse.empty()){need(owners.contains(job.reuse),"reuse owner must precede alias and be a real picture");auto& owner=jobs.at(owners.at(job.reuse));need(owner.reuse.empty()&&owner.native.at("key")[1]==job.native.at("key")[1]&&owner.native.at("equivalence_class")==job.native.at("equivalence_class")&&owner.native.at("targets")==job.native.at("targets"),"reuse is not an original equivalent with matching geometry");job.contract["owner_fingerprint"]=owner.fingerprint;job.fingerprint=sha_text(job.contract.dump());auto root=clean_path(path(batch.at("output_root")));job.output=root/L"frames"/path(job.id)/path(job.fingerprint+".png");job.receipt=clean_path(path(batch.at("receipt_directory")))/path(job.id+"-"+job.fingerprint+".json");}
  else owners.emplace(job.id,jobs.size());
  if((job.mode=="reuse"||job.mode=="reframe")&&job.reuse.empty())sources.insert(folded(job.copy.file));for(auto& layer:job.layers){sources.insert(folded(layer.a.file));if(layer.paired)sources.insert(folded(layer.b.file));}
  items.push_back({{"id",job.id},{"status","READY"},{"mode",job.mode},{"output",str(job.output)},{"output_pixels",{job.w,job.h}},{"fingerprint",job.fingerprint},{"review_required",true}});if(job.workLimit){items.back()["sampling_work_admission"]=job.estimatedWork.evidence();registeredWork+=job.estimatedWork.used;}jobs.push_back(std::move(job));
 }catch(const std::exception& e){blocked++;items.push_back({{"id",entry.value("id","invalid")},{"status","BLOCKED"},{"error",e.what()}});}
 for(auto& binding:batch.at("native_contracts"))sources.insert(folded(path(binding.at("path"))));
 for(auto& job:jobs)need(!sources.contains(folded(job.output))&&!sources.contains(folded(job.receipt)),"motion output/receipt aliases an input");
 return{{"items",items},{"ready",jobs.size()},{"blocked",blocked},{"failed",blocked},{"outputs_written",0},{"unique_sources_checked",cache.size()},{"target_scale",targetScale},{"review_required",true}};
}

inline std::array<int,4> motion_raster_bounds(MotionPoint a,MotionPoint b,MotionPoint c,int width,int height){
 return {std::max(0,int(std::floor(std::min({a.x,b.x,c.x})))),std::max(0,int(std::floor(std::min({a.y,b.y,c.y})))),std::min(width,int(std::ceil(std::max({a.x,b.x,c.x})))),std::min(height,int(std::ceil(std::max({a.y,b.y,c.y}))))};
}
inline std::uint64_t motion_raster_visits(const std::array<int,4>& r){return std::uint64_t(std::max(0,r[2]-r[0]))*std::max(0,r[3]-r[1]);}
inline void motion_source_coverage(const Image& im,const std::vector<MotionPoint>& uv,const std::vector<MotionTriangle>& triangles,MotionSamplingWork* work=nullptr,Json* evidence=nullptr){
 std::vector<unsigned char> covered(im.p.size(),0);
 if(work)work->add(im.p.size(),work->source);
 for(auto t:triangles){auto a=uv[t[0]],b=uv[t[1]],c=uv[t[2]];int l=std::max(0,int(std::floor(std::min({a.x,b.x,c.x})))),r=std::min(im.w,int(std::ceil(std::max({a.x,b.x,c.x}))));int top=std::max(0,int(std::floor(std::min({a.y,b.y,c.y})))),bottom=std::min(im.h,int(std::ceil(std::max({a.y,b.y,c.y}))));
  if(work)work->add(std::uint64_t(std::max(0,r-l))*std::max(0,bottom-top),work->source);
  for(int y=top;y<bottom;y++)for(int x=l;x<r;x++)if(motion_inside(motion_weights({x+.5,y+.5},a,b,c)))covered[std::size_t(y)*im.w+x]=1;
 }
 std::uint64_t positive=0,low=0;
 for(size_t n=0;n<im.p.size();n++){need(im.p[n].a<=0.f||covered[n],"NEEDS_KEYFRAME: mesh hull discards visible source pixels");if(evidence&&im.p[n].a>0){++positive;low+=im.p[n].a<=1.f/255+1e-8f;}}
 if(evidence)*evidence={{"positive_pixels",positive},{"positive_pixels_covered",positive},{"positive_pixels_missing",0},{"alpha_le_1_over_255_pixels",low},{"all_positive_alpha_required",true}};
}
struct MotionFootprint {double c00=1,c01=0,c11=1,i00=1,i01=0,i11=1,maximumLambda=1;};
inline MotionFootprint motion_footprint(MotionPoint a,MotionPoint b,MotionPoint c,MotionPoint u,MotionPoint v,MotionPoint w,bool registered=false){
 double d=motion_area(a,b,c),dx1=b.x-a.x,dy1=b.y-a.y,dx2=c.x-a.x,dy2=c.y-a.y;
 double j00=((v.x-u.x)*dy2-(w.x-u.x)*dy1)/d,j01=(-(v.x-u.x)*dx2+(w.x-u.x)*dx1)/d,j10=((v.y-u.y)*dy2-(w.y-u.y)*dy1)/d,j11=(-(v.y-u.y)*dx2+(w.y-u.y)*dx1)/d;
 double aa=j00*j00+j01*j01,bb=j00*j10+j01*j11,cc=j10*j10+j11*j11;
 double theta=.5*std::atan2(2*bb,aa-cc),co=std::cos(theta),si=std::sin(theta),delta=std::hypot(aa-cc,2*bb),l1=std::max(1.,(aa+cc+delta)/2),l2=std::max(1.,(aa+cc-delta)/2);
 need(std::isfinite(l1)&&std::isfinite(l2)&&l1<=(registered?double(MAX_AXIS)*MAX_AXIS:256.),registered?"MOTION_SAMPLING_FOOTPRINT_NUMERIC_LIMIT":"NEEDS_KEYFRAME: unsupported sampling footprint");MotionFootprint f;f.maximumLambda=l1;f.c00=l1*co*co+l2*si*si;f.c01=(l1-l2)*co*si;f.c11=l1*si*si+l2*co*co;double det=f.c00*f.c11-f.c01*f.c01;f.i00=f.c11/det;f.i01=-f.c01/det;f.i11=f.c00/det;return f;
}
struct MotionSampleBounds {
 bool identity=false;int l=0,r=-1,t=0,b=-1,al=0,ar=-1,at=0,ab=-1;
 std::uint64_t ewa()const{return std::uint64_t(std::max(0,r-l+1))*std::max(0,b-t+1);}
 std::uint64_t alpha()const{return std::uint64_t(std::max(0,ar-al+1))*std::max(0,ab-at+1);}
};
// x/y are source pixel-centre indices, after the existing -.5 conversion.
inline MotionSampleBounds motion_sample_bounds(double x,double y,const MotionFootprint& f){
 MotionSampleBounds q;q.identity=std::abs(x-std::round(x))<1e-9&&std::abs(y-std::round(y))<1e-9&&std::abs(f.c00-1)<1e-9&&std::abs(f.c11-1)<1e-9&&std::abs(f.c01)<1e-9;
 if(q.identity)return q;
 constexpr double radius=4.2410628637960699*.88451209326050048;
 double rx=std::sqrt(f.c00),ry=std::sqrt(f.c11);q.l=int(std::ceil(x-radius*rx));q.r=int(std::floor(x+radius*rx));q.t=int(std::ceil(y-radius*ry));q.b=int(std::floor(y+radius*ry));
 q.al=int(std::ceil(x-.5*rx));q.ar=int(std::floor(x+.5*rx));q.at=int(std::ceil(y-.5*ry));q.ab=int(std::floor(y+.5*ry));return q;
}
inline void motion_sample_work(MotionSamplingWork& work,const MotionSampleBounds& q){
 need(q.ewa()+q.alpha()<=MOTION_SAMPLE_WORK_LIMIT,"MOTION_SINGLE_SAMPLE_WORK_LIMIT");work.add(q.ewa(),work.ewa);work.add(q.alpha(),work.alpha);
}
inline void motion_admit_sampling(MotionJob& job,std::uint64_t* preflightRasterWork){
 auto& work=job.estimatedWork;work.limit=job.workLimit;
 auto dest=[&](const MotionLayer& layer,int index){auto p=layer.target[index];return MotionPoint{p.x*job.targetScale-job.origin[0],p.y*job.targetScale-job.origin[1]};};
 // First bound all raster loops in O(triangle count), before enumerating any
 // target centres. This prevents the admission pass itself becoming unbounded.
 for(const auto& layer:job.layers)for(int side=0;side<(layer.paired?2:1);++side){
  const auto& source=side?layer.b:layer.a;const auto& uv=side?layer.vb:layer.va;work.add(std::uint64_t(source.w)*source.h,work.source);
  for(auto t:layer.triangles){work.add(motion_raster_visits(motion_raster_bounds(uv[t[0]],uv[t[1]],uv[t[2]],source.w,source.h)),work.source);work.add(motion_raster_visits(motion_raster_bounds(dest(layer,t[0]),dest(layer,t[1]),dest(layer,t[2]),job.w,job.h)),work.raster);}
 }
 std::uint64_t admissionRaster=work.raster;for(const auto& layer:job.layers)admissionRaster+=std::uint64_t(job.w)*job.h*(layer.paired?2:1);
 need(admissionRaster<=MOTION_BATCH_WORK_LIMIT,"MOTION_ADMISSION_RASTER_WORK_LIMIT");
 if(preflightRasterWork){need(*preflightRasterWork<=MOTION_BATCH_WORK_LIMIT-admissionRaster,"MOTION_BATCH_ADMISSION_RASTER_WORK_LIMIT");*preflightRasterWork+=admissionRaster;}
 for(const auto& layer:job.layers)for(int side=0;side<(layer.paired?2:1);++side){
  const auto& source=side?layer.b:layer.a;const auto& uv=side?layer.vb:layer.va;std::vector<unsigned char> covered(std::size_t(job.w)*job.h,0);
  for(auto t:layer.triangles){auto a=dest(layer,t[0]),b=dest(layer,t[1]),c=dest(layer,t[2]);auto u=uv[t[0]],v=uv[t[1]],w=uv[t[2]];auto footprint=motion_footprint(a,b,c,u,v,w,source.registered);work.maximumLambda=std::max(work.maximumLambda,footprint.maximumLambda);auto bounds=motion_raster_bounds(a,b,c,job.w,job.h);
   for(int y=bounds[1];y<bounds[3];++y)for(int x=bounds[0];x<bounds[2];++x){auto weights=motion_weights({x+.5,y+.5},a,b,c);if(!motion_inside(weights))continue;auto pos=std::size_t(y)*job.w+x;bool interior=std::min({weights[0],weights[1],weights[2]})>1e-7;need(!(covered[pos]==2&&interior),"NEEDS_KEYFRAME: overlapping mesh triangles");if(covered[pos])continue;covered[pos]=interior?2:1;
    motion_sample_work(work,motion_sample_bounds(weights[0]*u.x+weights[1]*v.x+weights[2]*w.x-.5,weights[0]*u.y+weights[1]*v.y+weights[2]*w.y-.5,footprint));
   }
  }
 }
}
inline Pixel motion_sample(const Image& im,double x,double y,const MotionFootprint& f,MotionSamplingWork* work=nullptr){
 x-=.5;y-=.5;
 auto bounds=motion_sample_bounds(x,y,f);if(work)motion_sample_work(*work,bounds);
 // Integer identity/translation keeps decoded pixels exactly. Otherwise use
 // the existing sharp EWA kernel with this triangle's affine footprint.
 if(bounds.identity){int xx=int(std::round(x)),yy=int(std::round(y));return xx>=0&&yy>=0&&xx<im.w&&yy<im.h?im.p[std::size_t(yy)*im.w+xx]:Pixel{};}
 int l=bounds.l,r=bounds.r,t=bounds.t,b=bounds.b;double sum=0,channels[4]{};
 for(int yy=t;yy<=b;yy++)for(int xx=l;xx<=r;xx++){double dx=xx-x,dy=yy-y,z=ewa_sharp_weight(f.i00*dx*dx+2*f.i01*dx*dy+f.i11*dy*dy);if(z==0)continue;sum+=z;if(xx>=0&&yy>=0&&xx<im.w&&yy<im.h){auto p=im.p[std::size_t(yy)*im.w+xx];channels[0]+=z*p.r;channels[1]+=z*p.g;channels[2]+=z*p.b;channels[3]+=z*p.a;}}
 need(sum>1e-10,"mesh EWA normalization failed");Pixel p{float(channels[0]/sum),float(channels[1]/sum),float(channels[2]/sum),float(channels[3]/sum)};
 // Bound coverage by the source footprint of this output pixel, independently
 // of the sharp reconstruction kernel. Transparent pixels cannot acquire a
 // positive ringing fringe from distant opaque samples. Larger reduction
 // footprints still include subpixel detail, rather than a nearest-pixel mask.
 int al=bounds.al,ar=bounds.ar,at=bounds.at,ab=bounds.ab;float low=1,high=0;
 for(int yy=at;yy<=ab;yy++)for(int xx=al;xx<=ar;xx++){float alpha=xx>=0&&yy>=0&&xx<im.w&&yy<im.h?im.p[std::size_t(yy)*im.w+xx].a:0;low=std::min(low,alpha);high=std::max(high,alpha);}
 float alpha=std::clamp(p.a,low,high);float ratio=p.a>1e-9f?alpha/p.a:0;p.r*=ratio;p.g*=ratio;p.b*=ratio;p.a=alpha;p.r=std::clamp(p.r,0.f,p.a);p.g=std::clamp(p.g,0.f,p.a);p.b=std::clamp(p.b,0.f,p.a);return p;
}
inline Image motion_warp(const Image& im,const std::vector<MotionPoint>& uv,const MotionLayer& layer,const MotionJob& job,bool registered=false,MotionSamplingWork* work=nullptr,Json* coverage=nullptr,const std::vector<std::array<double,3>>* gains=nullptr){
 motion_source_coverage(im,uv,layer.triangles,work,coverage);Image out(job.w,job.h);std::vector<unsigned char> covered(out.p.size(),0);
 auto dest=[&](int index){auto v=layer.target[index];return MotionPoint{v.x*job.targetScale-job.origin[0],v.y*job.targetScale-job.origin[1]};};
 for(auto triangle:layer.triangles){auto a=dest(triangle[0]),b=dest(triangle[1]),c=dest(triangle[2]);auto u=uv[triangle[0]],v=uv[triangle[1]],w=uv[triangle[2]];auto footprint=motion_footprint(a,b,c,u,v,w,registered);if(work)work->maximumLambda=std::max(work->maximumLambda,footprint.maximumLambda);
  int l=std::max(0,int(std::floor(std::min({a.x,b.x,c.x})))),r=std::min(out.w,int(std::ceil(std::max({a.x,b.x,c.x}))));int top=std::max(0,int(std::floor(std::min({a.y,b.y,c.y})))),bottom=std::min(out.h,int(std::ceil(std::max({a.y,b.y,c.y}))));
  if(work)work->add(std::uint64_t(std::max(0,r-l))*std::max(0,bottom-top),work->raster);
  for(int y=top;y<bottom;y++)for(int x=l;x<r;x++){auto weights=motion_weights({x+.5,y+.5},a,b,c);if(!motion_inside(weights))continue;auto pos=std::size_t(y)*out.w+x;bool interior=std::min({weights[0],weights[1],weights[2]})>1e-7;need(!(covered[pos]==2&&interior),"NEEDS_KEYFRAME: overlapping mesh triangles");if(covered[pos])continue;covered[pos]=interior?2:1;auto sample=motion_sample(im,weights[0]*u.x+weights[1]*v.x+weights[2]*w.x,weights[0]*u.y+weights[1]*v.y+weights[2]*w.y,footprint,work);
   if(gains&&gains->size()==layer.target.size()){
    const auto& ga=(*gains)[triangle[0]],&gb=(*gains)[triangle[1]],&gc=(*gains)[triangle[2]];
    float gr=float(weights[0]*ga[0]+weights[1]*gb[0]+weights[2]*gc[0]);
    float gg=float(weights[0]*ga[1]+weights[1]*gb[1]+weights[2]*gc[1]);
    float gbb=float(weights[0]*ga[2]+weights[1]*gb[2]+weights[2]*gc[2]);
    sample.r=std::clamp(sample.r*gr,0.f,sample.a);
    sample.g=std::clamp(sample.g*gg,0.f,sample.a);
    sample.b=std::clamp(sample.b*gbb,0.f,sample.a);
   }
   out.p[pos]=sample;
  }
 }return out;
}
inline Pixel motion_mix(Pixel a,Pixel b,double t){float f=float(t),g=1-f;return{a.r*g+b.r*f,a.g*g+b.g*f,a.b*g+b.b*f,a.a*g+b.a*f};}
inline Json motion_alignment(const Image& a,const Image& b){
 double intersection=0,united=0,color=0,colorWeight=0,peakA=0,peakB=0;
 for(size_t n=0;n<a.p.size();n++){peakA=std::max(peakA,double(a.p[n].a));peakB=std::max(peakB,double(b.p[n].a));}
 for(size_t n=0;n<a.p.size();n++){auto x=a.p[n],y=b.p[n];double ax=peakA>1e-9?x.a/peakA:0,by=peakB>1e-9?y.a/peakB:0;intersection+=std::min(ax,by);united+=std::max(ax,by);double low=std::min(x.a,y.a);if(low>1e-6){colorWeight+=low;color+=low*(std::abs(x.r/x.a-y.r/y.a)+std::abs(x.g/x.a-y.g/y.a)+std::abs(x.b/x.a-y.b/y.a))/3;}}
 return{{"weighted_alpha_overlap",united>1e-9?intersection/united:0},{"alpha_overlap_normalization","per_layer_peak"},{"mean_straight_linear_rgb_error",colorWeight>1e-9?color/colorWeight:1},{"visual_review_required",true}};
}
inline void motion_over(Image& below,const Image& above){for(size_t n=0;n<below.p.size();n++){auto a=above.p[n];auto& b=below.p[n];float f=1-a.a;b={a.r+b.r*f,a.g+b.g*f,a.b+b.b*f,a.a+b.a*f};}}
inline Json motion_metrics(const Image& im,std::array<int,2> origin,int targetScale){
 int l=im.w,t=im.h,r=0,b=0,opaqueBottom=0;double area=0;std::uint64_t semi=0,visible=0;bool edge=false;
 for(int y=0;y<im.h;y++)for(int x=0;x<im.w;x++){auto a=im.p[std::size_t(y)*im.w+x].a;area+=a;if(a>0&&a<1)semi++;if(a>=128.f/255)opaqueBottom=std::max(opaqueBottom,y+1);if(a>=16.f/255){visible++;l=std::min(l,x);t=std::min(t,y);r=std::max(r,x+1);b=std::max(b,y+1);edge|=x==0||y==0||x==im.w-1||y==im.h-1;}}
 need(visible>0,"NEEDS_KEYFRAME: empty rendered body");int foot=opaqueBottom?opaqueBottom:b;double footAt3x=std::floor(double(foot-1)*3/targetScale),footY=targetScale==3?double(origin[1]+foot-1)/3.0:double(origin[1])/targetScale+footAt3x/3.0;Json result={{"body_height_1x",double(b-t)/targetScale},{"foot_y_1x",footY},{"foot_alpha_threshold",opaqueBottom?128:16},{"foot_anchor_convention",targetScale==3?"last_body_pixel_index":"last_3x_pixel_equivalent"},{"visible_pixels",visible},{"partial_alpha_pixels",semi},{"core_touches_crop_edge",edge}};result[targetScale==3?"alpha_bbox_3x":"alpha_bbox_target_scale"]={l,t,r,b};result[targetScale==3?"alpha_area_3x":"alpha_area_target_scale"]=area;return result;
}
inline Json motion_surface_metrics(const Image& im,const std::vector<std::uint8_t>& encoded,int targetScale){
 int l=im.w,t=im.h,r=0,b=0;std::uint64_t positive=0;double area=0;
 for(int y=0;y<im.h;y++)for(int x=0;x<im.w;x++){auto a=im.p[std::size_t(y)*im.w+x].a;area+=a;if(a>0){++positive;l=std::min(l,x);t=std::min(t,y);r=std::max(r,x+1);b=std::max(b,y+1);}}
 need(positive>0,"NEEDS_KEYFRAME: empty rendered surface");
 int ql=im.w,qt=im.h,qr=0,qb=0;std::uint64_t encodedPositive=0,encodedAlpha1=0;
 for(int y=0;y<im.h;y++)for(int x=0;x<im.w;x++){auto a=encoded[(std::size_t(y)*im.w+x)*4+3];if(a){++encodedPositive;encodedAlpha1+=a==1;ql=std::min(ql,x);qt=std::min(qt,y);qr=std::max(qr,x+1);qb=std::max(qb,y+1);}}
 need(encodedPositive>0,"NEEDS_KEYFRAME: primary surface empty after alpha quantization");
 Json result={{"quality_mode","anchored-surface"},{"positive_support_before_quantization",positive},{"positive_support_after_quantization",encodedPositive},{"alpha1_pixels_after_quantization",encodedAlpha1},{"fixed_mount_geometry_verified",true},{"semantic_body_or_foot_inferred",false}};result[targetScale==3?"positive_support_bbox_3x":"positive_support_bbox_target_scale"]={l,t,r,b};result[targetScale==3?"encoded_support_bbox_3x":"encoded_support_bbox_target_scale"]={ql,qt,qr,qb};result[targetScale==3?"alpha_area_3x":"alpha_area_target_scale"]=area;return result;
}
inline std::pair<std::vector<std::uint8_t>,Json> render_motion(const MotionJob& job){
 motion_component_currentness(job);
 Wic decoder;Image composite(job.w,job.h),body(job.w,job.h);Json layers=Json::array();MotionSamplingWork work;work.limit=job.workLimit;auto counter=job.workLimit?&work:nullptr;
 for(auto& layer:job.layers){
  auto decode=[&](const MotionSource& s){auto raw=read_all(s.file);need(sha(raw)==s.hash,"motion source drift before sampling");std::string note;auto full=decoder.decode(raw,true,note);return motion_crop_strict_source(full,s);};
  Json coverageA,coverageB;auto a=decode(layer.a);auto warped=motion_warp(a,layer.va,layer,job,layer.a.registered,counter,layer.a.registered?&coverageA:nullptr,&layer.gainA);
   Json alignment=Json::object();
   if(layer.paired){auto b=decode(layer.b);auto other=motion_warp(b,layer.vb,layer,job,layer.b.registered,counter,layer.b.registered?&coverageB:nullptr,&layer.gainB);alignment=motion_alignment(warped,other);if((layer.kind=="body"||layer.kind=="surface")&&layer.time>0&&layer.time<1){double overlap=alignment.at("weighted_alpha_overlap"),color=alignment.at("mean_straight_linear_rgb_error");need(overlap>=job.quality.value("min_pair_alpha_overlap",.8)&&color<=job.quality.value("max_pair_color_error",.18),"NEEDS_KEYFRAME: incompatible aligned body pair; alpha_overlap="+std::to_string(overlap)+" color_error="+std::to_string(color));}for(size_t n=0;n<warped.p.size();n++)warped.p[n]=motion_mix(warped.p[n],other.p[n],layer.time);}
   motion_over(composite,warped);if(layer.kind=="body"||layer.kind=="surface")motion_over(body,warped);layers.push_back({{"id",layer.name},{"kind",layer.kind},{"time",layer.time},{"pose_interpolated",layer.posePaired},{"vertices",layer.target.size()},{"triangles",layer.triangles.size()},{"alignment",alignment}});
   if(layer.kind=="component")layers.back()["rigid_component_one_to_one_verified"]=true;
   if(layer.a.registered)layers.back()["source_a_coverage"]=coverageA;if(layer.paired&&layer.b.registered)layers.back()["source_b_coverage"]=coverageB;
   if(layer.kind=="surface")layers.back()["fixed_mount_anchors"]=layer.anchors.size();
 }
 Json metrics;
 if(job.anchoredSurface)metrics=motion_surface_metrics(body,quantize(body,true),job.targetScale);
 else{
 metrics=motion_metrics(body,job.origin,job.targetScale);double height=metrics.at("body_height_1x").get<double>()/motion_real(job.quality.at("reference_body_height_1x"),"reference height"),foot=metrics.at("foot_y_1x").get<double>()-motion_real(job.quality.at("foot_y_1x"),"foot");
 need(height>=motion_real(job.quality.at("min_height_fraction"),"minimum")&&height<=motion_real(job.quality.at("max_height_fraction"),"maximum"),"NEEDS_KEYFRAME: rendered body volume differs from original; height_fraction="+std::to_string(height));need(std::abs(foot)<=motion_real(job.quality.at("foot_tolerance_1x"),"foot tolerance"),"NEEDS_KEYFRAME: rendered foot position drift; actual_1x="+std::to_string(metrics.at("foot_y_1x").get<double>())+" expected_1x="+std::to_string(motion_real(job.quality.at("foot_y_1x"),"foot"))+" error_1x="+std::to_string(foot));
 metrics.update({{"height_fraction",height},{"foot_error_1x",foot}});
 }
 metrics.update({{"layers",layers},{"art_approved",false},{"continuity_review_required",true}});auto pixels=quantize(composite,true);
 if(job.workLimit){
  need(work.evidence()==job.estimatedWork.evidence(),"MOTION_SAMPLING_WORK_ADMISSION_MISMATCH");metrics["sampling_work"]=work.evidence();std::uint64_t positive=0,alpha1=0;for(std::size_t at=3;at<pixels.size();at+=4){positive+=pixels[at]>0;alpha1+=pixels[at]==1;}metrics["output_alpha_positive_pixels"]=positive;metrics["output_alpha1_pixels"]=alpha1;
 }
 motion_component_currentness(job);return{std::move(pixels),metrics};
}
inline Json motion_authority(){return{{"model_inference_performed",false},{"model_native_output",false},{"human_approved",false},{"art_approved",false},{"game_deployed",false},{"runtime_accepted",false},{"native_timing_verified",false}};}
inline std::vector<std::uint8_t> reframe_motion(const MotionJob& job){
 auto png=read_all(job.copy.file);need(sha(png)==job.copy.hash,"reframe source drift");Wic decoder;auto source=decoder.decode_rgba(png,job.copy.w,job.copy.h);
 auto [left,top,width,height]=job.reframe;need(width==job.w&&height==job.h,"reframe target geometry changed");
 for(int y=0;y<job.copy.h;y++)for(int x=0;x<job.copy.w;x++)if(x<left||x>=left+width||y<top||y>=top+height)
  need(source[(std::size_t(y)*job.copy.w+x)*4+3]==0,"NEEDS_KEYFRAME: transparent reframe would discard visible source pixels");
 std::vector<std::uint8_t> result(std::size_t(job.w)*job.h*4);
 for(int y=0;y<job.h;y++)std::copy_n(source.begin()+(std::size_t(y+top)*job.copy.w+left)*4,std::size_t(job.w)*4,result.begin()+std::size_t(y)*job.w*4);
 return result;
}
inline Json verify_motion(const MotionJob& job,bool replay,const std::map<std::string,MotionJob>& owners){
 auto receipt=read_json(job.receipt);need(receipt.at("schema")=="h3.motion.receipt.v1"&&receipt.at("job_fingerprint")==job.fingerprint&&receipt.at("contract")==job.contract&&receipt.at("algorithm")==job.algorithm,"motion receipt contract drift");
 if(motion_modern_algorithm(job.algorithm))need(receipt.at("target_scale")==job.targetScale,"motion receipt target scale drift");else if(receipt.contains("target_scale"))need(receipt.at("target_scale")==3,"legacy motion receipt target scale drift");
 auto originField=job.targetScale==3?"origin_3x":"origin_at_target_scale";need(receipt.at(originField)==job.origin,"motion receipt origin drift");auto authority=motion_authority();for(auto& [name,value]:authority.items())need(receipt.at(name)==value,"motion receipt authority changed");need(receipt.at("output")==str(job.output)&&receipt.at("output_pixels")==Json::array({job.w,job.h}),"motion receipt geometry changed");
 auto png=read_all(job.output);Wic decoder;auto rgba=decoder.decode_rgba(png,job.w,job.h);need(sha(png)==receipt.at("png_sha256").get<std::string>()&&sha(rgba)==receipt.at("rgba_sha256").get<std::string>(),"motion output drift");
 if(!job.reuse.empty()){auto owner=owners.at(job.reuse);auto ownerReceipt=read_json(owner.receipt);need(ownerReceipt.at("job_fingerprint")==owner.fingerprint,"reuse owner contract drift");auto bytes=read_all(owner.output);need(sha(bytes)==ownerReceipt.at("png_sha256").get<std::string>()&&png==bytes,"duplicate slot differs from canonical picture");}
 else if(job.mode=="reuse"){auto source=read_all(job.copy.file);need(sha(source)==job.copy.hash&&png==source,"exact3X source reuse changed bytes");}
 else if(job.mode=="reframe")need(rgba==reframe_motion(job),"transparent reframe changed source pixels");
 else if(replay){auto rendered=render_motion(job);need(rgba==rendered.first&&receipt.at("diagnostics")==rendered.second,"motion output differs from independent replay");}
 return receipt;
}
inline Json process_motion(const MotionJob& job,const Json& settings,bool verifyOnly,const std::map<std::string,MotionJob>& owners){
 auto start=Clock::now();std::vector<Handle> pins;for(auto& layer:job.layers){pins.push_back(open_read(layer.a.file));if(layer.paired)pins.push_back(open_read(layer.b.file));}if((job.mode=="reuse"||job.mode=="reframe")&&job.reuse.empty())pins.push_back(open_read(job.copy.file));
 if(fs::exists(job.receipt)){verify_motion(job,verifyOnly,owners);return{{"id",job.id},{"status",verifyOnly?"VERIFIED":"REUSED_VERIFIED"},{"receipt",str(job.receipt)},{"output",str(job.output)},{"total_ms",ms(start)}};}
 need(!verifyOnly,"motion receipt missing");std::vector<std::uint8_t> png,rgba;Json diagnostics=Json::object();Wic decoder;
 if(!job.reuse.empty()){auto& owner=owners.at(job.reuse);verify_motion(owner,false,owners);png=read_all(owner.output);rgba=decoder.decode_rgba(png,job.w,job.h);diagnostics={{"original_exact_duplicate",true},{"owner",owner.id}};}
 else if(job.mode=="reuse"){png=read_all(job.copy.file);need(sha(png)==job.copy.hash,"reuse source drift");rgba=decoder.decode_rgba(png,job.w,job.h);diagnostics={{"exact3X_byte_reuse",true}};}
 else if(job.mode=="reframe"){rgba=reframe_motion(job);png=decoder.encode(rgba,job.w,job.h,settings.at("png_filter")=="adaptive");diagnostics={{"transparent_reframe",true},{"source_rect",job.reframe},{"all_visible_source_pixels_preserved",true},{"resampling_performed",false}};}
 else{auto rendered=render_motion(job);rgba=std::move(rendered.first);diagnostics=std::move(rendered.second);png=decoder.encode(rgba,job.w,job.h,settings.at("png_filter")=="adaptive");need(decoder.decode_rgba(png,job.w,job.h)==rgba,"motion PNG roundtrip differs");}
 Json receipt={{"schema","h3.motion.receipt.v1"},{"created_utc",now()},{"tool_version",VERSION},{"job_fingerprint",job.fingerprint},{"contract",job.contract},{"algorithm",job.algorithm},{"backend","cpu"},{"output",str(job.output)},{"output_pixels",{job.w,job.h}},{"png_sha256",sha(png)},{"rgba_sha256",sha(rgba)},{"bytes",png.size()},{"diagnostics",diagnostics},{"total_ms",ms(start)}};
 if(job.targetScale==3)receipt["origin_3x"]=job.origin;else receipt["origin_at_target_scale"]=job.origin;
 if(motion_modern_algorithm(job.algorithm))receipt["target_scale"]=job.targetScale;receipt.update(motion_authority());
 same_or_new(job.output,png);for(auto& layer:job.layers){need(sha(read_all(layer.a.file))==layer.a.hash,"source changed during motion");if(layer.paired)need(sha(read_all(layer.b.file))==layer.b.hash,"source changed during motion");}write_json_new(job.receipt,receipt);verify_motion(job,false,owners);
 return{{"id",job.id},{"status","DERIVED_VERIFIED_REVIEW_REQUIRED"},{"receipt",str(job.receipt)},{"output",str(job.output)},{"output_pixels",{job.w,job.h}},{"total_ms",ms(start)}};
}
inline Json make_motion_batch(Json spec){
 need(spec.at("schema")=="h3.motion.spec.v1","motion specification required");int targetScale=spec.contains("target_scale")?integer(spec.at("target_scale"),3,32,"motion target scale"):3;spec["schema"]="h3.motion.batch.v1";spec["algorithm"]=MOTION_ALGORITHM;spec["target_scale"]=targetScale;spec["created_utc"]=now();auto root=clean_path(path(spec.at("output_root")));spec["receipt_directory"]=str(root/L"receipts");auto settings=motion_settings(settings_checked(spec));spec["settings"]=settings;
 for(auto& binding:spec.at("native_contracts")){auto raw=read_all(path(binding.at("path")),32*1024*1024);if(binding.contains("sha256"))need(binding.at("sha256")==sha(raw),"native contract changed before freeze");binding["sha256"]=sha(raw);}
 auto freeze=[](Json& s){auto raw=read_all(path(s.at("path")));if(s.contains("sha256"))need(s.at("sha256")==sha(raw),"source changed before freeze");Wic decoder;auto meta=decoder.frame(raw);if(s.contains("pixels"))need(s.at("pixels")==Json::array({meta.w,meta.h}),"source dimensions changed before freeze");s["sha256"]=sha(raw);s["pixels"]={meta.w,meta.h};};
 for(auto& entry:spec.at("items")){if(entry.contains("reuse_from"))continue;auto mode=entry.value("mode","mesh");if(mode=="reuse"||mode=="reframe")freeze(entry["source"]);else for(auto& layer:entry.at("layers")){freeze(layer["source_a"]);if(mode=="interpolate"||(mode=="skeleton"&&layer.contains("source_b")))freeze(layer["source_b"]);}}
  std::vector<MotionJob> jobs;auto pre=check_motion(spec,settings,jobs);if(pre.at("blocked")!=0)for(auto& item:pre.at("items"))if(item.at("status")=="BLOCKED")throw std::runtime_error("motion specification blocked: "+item.at("id").get<std::string>()+": "+item.at("error").get<std::string>());spec["review_required"]=true;return spec;
}
inline Json run_motion(const Json& batch,const Json& settings,bool verifyOnly=false){
 auto start=Clock::now();std::vector<MotionJob> jobs;auto pre=check_motion(batch,settings,jobs);Json report={{"schema","h3.motion.run.v1"},{"created_utc",now()},{"algorithm",batch.at("algorithm")},{"target_scale",batch.at("target_scale")},{"preflight",pre},{"results",Json::array()},{"review_required",true}};report.update(motion_authority());
 if(pre.at("blocked")!=0||jobs.empty()){report.update({{"status","PREFLIGHT_BLOCKED"},{"failed",pre.at("blocked")},{"outputs_written",0}});return report;}
 auto receiptDir=clean_path(path(batch.at("receipt_directory")));fs::create_directories(receiptDir);Handle lock(CreateFileW((receiptDir/L".h3-motion.lock").c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr));need(lock.h!=INVALID_HANDLE_VALUE,"another motion run holds this output root");
 auto host=host_memory();auto limit=std::min(settings.at("memory_gib").get<std::uint64_t>()*1024*1024*1024,std::min(host.at("physical_bytes").get<std::uint64_t>()*3/4,host.at("available_bytes").get<std::uint64_t>()*3/4));need(limit>=256ull*1024*1024,"too little RAM for motion");MemoryGate memory(limit);std::map<std::string,MotionJob> owners;std::vector<size_t> pictures,aliases;for(size_t n=0;n<jobs.size();n++){if(jobs[n].reuse.empty()){owners.emplace(jobs[n].id,jobs[n]);pictures.push_back(n);}else aliases.push_back(n);}
 auto stage=[&](const std::vector<size_t>& indices){unsigned count=std::min<unsigned>(settings.at("workers").get<unsigned>(),unsigned(indices.size()));std::atomic<size_t> next=0;std::mutex mutex;std::vector<std::thread> threads;for(unsigned n=0;n<count;n++)threads.emplace_back([&]{while(true){auto i=next++;if(i>=indices.size())break;auto& job=jobs[indices[i]];Json result;try{ComInit com;MemoryLease lease(memory,job.admission);result=process_motion(job,settings,verifyOnly,owners);}catch(const std::exception& e){std::string error=e.what();result={{"id",job.id},{"status",error.find("NEEDS_KEYFRAME")!=std::string::npos?"NEEDS_KEYFRAME":"FAILED"},{"error",error}};}std::lock_guard guard(mutex);report["results"].push_back(result);}});for(auto& thread:threads)thread.join();};
 stage(pictures);stage(aliases);int failed=0,written=0;for(auto& result:report.at("results")){auto status=result.at("status");failed+=status=="FAILED"||status=="NEEDS_KEYFRAME";written+=status=="DERIVED_VERIFIED_REVIEW_REQUIRED";}
 // Reports follow original requested slot order, regardless of worker completion.
 Json ordered=Json::array();for(auto& job:jobs)for(auto& result:report.at("results"))if(result.at("id")==job.id)ordered.push_back(result);report["results"]=ordered;
 report.update({{"status",failed?"COMPLETED_WITH_FAILURES":"COMPLETED"},{"failed",failed},{"outputs_written",written},{"unique_pictures",pictures.size()},{"duplicate_slots",aliases.size()},{"workers",settings.at("workers")},{"wall_ms",ms(start)},{"memory_budget_bytes",limit},{"peak_admitted_estimate_bytes",memory.peak}});report.update(process_memory_peaks());return report;
}
inline Json probe_motion(const Json& batch,const Json& settings){
 auto start=Clock::now();std::vector<MotionJob> jobs;auto pre=check_motion(batch,settings,jobs);
 Json report={{"schema","h3.motion.probe.v1"},{"created_utc",now()},{"algorithm",batch.at("algorithm")},{"target_scale",batch.at("target_scale")},{"preflight",pre},{"results",Json::array()},{"outputs_written",0},{"review_required",true}};report.update(motion_authority());
 if(pre.at("blocked")!=0||jobs.empty()){report.update({{"status","PREFLIGHT_BLOCKED"},{"failed",pre.at("blocked")},{"ready",0}});return report;}
 auto host=host_memory();auto limit=std::min(settings.at("memory_gib").get<std::uint64_t>()*1024*1024*1024,std::min(host.at("physical_bytes").get<std::uint64_t>()*3/4,host.at("available_bytes").get<std::uint64_t>()*3/4));need(limit>=256ull*1024*1024,"too little RAM for motion probe");MemoryGate memory(limit);
 std::vector<Json> checked(jobs.size());std::vector<size_t> pictures,aliases;std::map<std::string,size_t> owners;
 for(size_t i=0;i<jobs.size();i++){if(jobs[i].reuse.empty()){pictures.push_back(i);owners.emplace(jobs[i].id,i);}else aliases.push_back(i);}
 std::atomic<size_t> next=0;std::vector<std::thread> threads;unsigned count=std::min<unsigned>(settings.at("workers").get<unsigned>(),unsigned(pictures.size()));
 for(unsigned n=0;n<count;n++)threads.emplace_back([&]{while(true){auto position=next++;if(position>=pictures.size())break;auto index=pictures[position];auto& job=jobs[index];
  try{
   ComInit com;MemoryLease lease(memory,job.admission);Json diagnostics=Json::object();
   if(job.mode=="reuse"){
    auto png=read_all(job.copy.file);need(sha(png)==job.copy.hash,"reuse source drift before probe");Wic decoder;decoder.decode_rgba(png,job.w,job.h);diagnostics={{"exact3X_byte_reuse",true}};
   }else if(job.mode=="reframe"){reframe_motion(job);diagnostics={{"transparent_reframe",true},{"source_rect",job.reframe},{"all_visible_source_pixels_preserved",true},{"resampling_performed",false}};}
   else diagnostics=render_motion(job).second;
   checked[index]={{"id",job.id},{"status","READY_FOR_RUN"},{"output",str(job.output)},{"diagnostics",diagnostics}};
  }catch(const std::exception& e){std::string error=e.what();checked[index]={{"id",job.id},{"status",error.find("NEEDS_KEYFRAME")!=std::string::npos?"NEEDS_KEYFRAME":"FAILED"},{"error",error}};}
 }});
 for(auto& thread:threads)thread.join();
 for(auto index:aliases){auto& job=jobs[index];auto owner=owners.find(job.reuse);need(owner!=owners.end(),"motion probe owner missing");
  checked[index]=checked[owner->second].at("status")=="READY_FOR_RUN"
   ?Json{{"id",job.id},{"status","READY_FOR_RUN"},{"output",str(job.output)},{"diagnostics",{{"original_exact_duplicate",true},{"owner",job.reuse}}}}
   :Json{{"id",job.id},{"status","NEEDS_KEYFRAME"},{"error","NEEDS_KEYFRAME: reuse owner cannot produce a candidate"}};
 }
 int failed=0,ready=0;for(auto& result:checked){auto status=result.at("status").get<std::string>();failed+=status=="FAILED"||status=="NEEDS_KEYFRAME";ready+=status=="READY_FOR_RUN";report["results"].push_back(result);}
 report.update({{"status",failed?"NEEDS_KEYFRAMES_OR_FIXES":"READY_FOR_RUN"},{"failed",failed},{"ready",ready},{"unique_pictures",jobs.size()},{"wall_ms",ms(start)},{"memory_budget_bytes",limit},{"peak_admitted_estimate_bytes",memory.peak}});report.update(process_memory_peaks());return report;
}
} // namespace h3tier
