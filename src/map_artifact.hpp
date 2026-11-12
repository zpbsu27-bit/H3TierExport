#pragma once
#include "wic.hpp"
#include <map>
#include <set>
#include <sstream>

namespace h3tier {
inline constexpr const char* MAP_ARTIFACT_ALGORITHM="official-shadow-uniform-ai-master-v2";
inline bool artifact_placeholder(const Json& item){return item.value("source_role","")=="official-placeholder";}
inline std::uint32_t artifact_u32(const std::vector<std::uint8_t>& raw,std::size_t at){
 need(at+4<=raw.size(),"MAP_ARTIFACT_DEF_TRUNCATED");return raw[at]|(std::uint32_t(raw[at+1])<<8)|(std::uint32_t(raw[at+2])<<16)|(std::uint32_t(raw[at+3])<<24);
}
inline std::vector<std::uint8_t> artifact_pin(const Json& ref){
 need(ref.is_object()&&valid_sha(ref.at("sha256")),"MAP_ARTIFACT_SOURCE_PIN_REQUIRED");auto raw=read_all(clean_path(path(ref.at("path"))));need(sha(raw)==ref.at("sha256").get<std::string>(),"MAP_ARTIFACT_SOURCE_SHA_DRIFT: "+ref.at("path").get<std::string>());return raw;
}
inline Rect artifact_bbox(const Image& image,float threshold){
 Rect box{image.w,image.h,0,0};for(int y=0;y<image.h;y++)for(int x=0;x<image.w;x++)if(image.p[std::size_t(y)*image.w+x].a>threshold){box.l=std::min(box.l,x);box.t=std::min(box.t,y);box.r=std::max(box.r,x+1);box.b=std::max(box.b,y+1);}need(box.w()>0&&box.h()>0,"MAP_ARTIFACT_EMPTY_SUBJECT");return box;
}
inline Json artifact_rect_json(Rect r){return Json::array({r.l,r.t,r.r,r.b});}
struct ArtifactNative {int w=0,h=0,count=0;Rect crop;};
inline ArtifactNative artifact_native_geometry(const Json& item){
 auto raw=artifact_pin(item.at("native_def"));const auto& key=item.at("key");need(key.is_array()&&key.size()==4&&key[0]=="def","MAP_ARTIFACT_DEF_KEY_REQUIRED");auto identity=token(key[1]);need(identity.rfind("AVA",0)==0,"MAP_ARTIFACT_AVA_IDENTITY_REQUIRED");int group=integer(key[2],0,65535,"group"),frame=integer(key[3],0,65535,"frame");
 auto groups=artifact_u32(raw,12);need(groups<=64,"MAP_ARTIFACT_DEF_GROUP_LIMIT");std::size_t at=784;ArtifactNative result;bool found=false;
 for(std::uint32_t n=0;n<groups;n++){auto id=artifact_u32(raw,at),count=artifact_u32(raw,at+4);need(count>0&&count<=10000,"MAP_ARTIFACT_DEF_FRAME_LIMIT");std::size_t offsets=at+16+std::size_t(count)*13;need(offsets+std::size_t(count)*4<=raw.size(),"MAP_ARTIFACT_DEF_DIRECTORY_TRUNCATED");if(id==std::uint32_t(group)){need(!found&&frame<int(count),"MAP_ARTIFACT_DEF_SLOT_INVALID");auto offset=artifact_u32(raw,offsets+frame*4);int w=int(artifact_u32(raw,offset+8)),h=int(artifact_u32(raw,offset+12)),cw=int(artifact_u32(raw,offset+16)),ch=int(artifact_u32(raw,offset+20)),x=int(artifact_u32(raw,offset+24)),y=int(artifact_u32(raw,offset+28));need(w>0&&h>0&&w<=512&&h<=512&&cw>0&&ch>0&&x>=0&&y>=0&&x+cw<=w&&y+ch<=h,"MAP_ARTIFACT_NATIVE_GEOMETRY_INVALID");result={w,h,int(count),{x,y,x+cw,y+ch}};found=true;}at=offsets+std::size_t(count)*4;}
 need(found,"MAP_ARTIFACT_NATIVE_GROUP_MISSING");need(item.at("native_canvas")==Json::array({result.w,result.h})&&item.at("native_crop")==artifact_rect_json(result.crop),"MAP_ARTIFACT_NATIVE_GEOMETRY_DRIFT");return result;
}
inline std::map<std::string,std::set<std::pair<int,int>>> artifact_objects(const Json& manifest){
 std::map<std::string,std::set<std::pair<int,int>>> objects;need(manifest.at("object_tables").is_array()&&!manifest.at("object_tables").empty(),"MAP_ARTIFACT_OFFICIAL_OBJECT_TABLE_REQUIRED");
 for(const auto& ref:manifest.at("object_tables")){auto raw=artifact_pin(ref);std::istringstream text(std::string(raw.begin(),raw.end()));std::string line;while(std::getline(text,line)){std::istringstream input(line);std::vector<std::string> parts;std::string part;while(input>>part)parts.push_back(part);if(parts.size()!=9)continue;auto name=parts[0];for(auto& c:name)c=(char)std::toupper((unsigned char)c);if(name.rfind("AVA",0)!=0||name.size()<5)continue;auto dot=name.rfind('.');if(dot==std::string::npos)continue;try{objects[name.substr(0,dot)].insert({std::stoi(parts[5]),std::stoi(parts[6])});}catch(const std::exception&){throw std::runtime_error("MAP_ARTIFACT_OBJECT_TABLE_ROW_INVALID");}}}return objects;
}
struct ArtifactMapJob {Json original;std::string id,filename;fs::path source;std::string sourceSha;ArtifactNative native;};
inline std::vector<ArtifactMapJob> artifact_map_jobs(const Json& manifest){
 need(manifest.value("schema","")=="h3.map-artifact-master-manifest.v1"&&manifest.value("algorithm","")==MAP_ARTIFACT_ALGORITHM,"MAP_ARTIFACT_MANIFEST_SCHEMA");artifact_pin(manifest.at("official_inventory"));auto objects=artifact_objects(manifest);need(manifest.at("items").is_array()&&!manifest.at("items").empty()&&manifest.at("items").size()<=1000,"MAP_ARTIFACT_ITEM_COUNT");std::set<std::wstring> names;std::set<std::string> ids;std::vector<ArtifactMapJob> jobs;
 Wic decoder;
 for(const auto& item:manifest.at("items")){ArtifactMapJob job;job.original=item;job.id=token(item.at("id"));need(ids.insert(job.id).second,"MAP_ARTIFACT_DUPLICATE_ID");job.filename=item.at("output_filename");auto file=fs::path(job.filename);need(file.filename()==file&&file.extension()==L".png"&&names.insert(folded(file)).second,"MAP_ARTIFACT_INVALID_OR_DUPLICATE_FILENAME");auto resource=item.at("resource").get<std::string>();need(resource.find_first_of("/\\:")==std::string::npos&&file.stem().string()==resource.substr(0,resource.rfind('.')),"MAP_ARTIFACT_OFFICIAL_RESOURCE_FILENAME_REQUIRED");job.source=clean_path(path(item.at("source").at("path")));job.sourceSha=item.at("source").at("sha256");auto sourceRaw=artifact_pin(item.at("source"));std::string note;auto source=decoder.decode(sourceRaw,true,note);if(!artifact_placeholder(item))need(std::any_of(source.p.begin(),source.p.end(),[](const Pixel& p){return p.a==0;}),"MAP_ARTIFACT_TRUE_ALPHA_SOURCE_REQUIRED");artifact_bbox(source,0);job.native=artifact_native_geometry(item);auto referenceRaw=artifact_pin(item.at("reference"));auto reference=decoder.frame(referenceRaw);need(reference.w==unsigned(job.native.w)&&reference.h==unsigned(job.native.h),"MAP_ARTIFACT_REFERENCE_CANVAS_DRIFT");auto identity=item.at("key")[1].get<std::string>();
  if(artifact_placeholder(item)){
   static const std::set<std::string> officialLabels{"AVA0003","AVA0004","AVA0142","AVA0143","AVA0144"};
   need(officialLabels.contains(identity)&&item.at("object_type").is_null()&&sourceRaw==referenceRaw,"MAP_ARTIFACT_PLACEHOLDER_REQUIRES_EXACT_OFFICIAL_LABEL");
   need(source.w==reference.w&&source.h==reference.h&&!item.contains("shadow_reference")&&!item.contains("reference_sequence"),"MAP_ARTIFACT_PLACEHOLDER_GEOMETRY_OR_LAYER_DRIFT");
   integer(item.at("working_scale"),4,64,"placeholder working scale");
  }
  if(item.at("object_type").is_null()){need(!objects.contains(identity)&&item.at("object_subtype").is_null()&&item.at("source_artifact_frame").is_null()&&(item.value("source_role","")=="map-only-ai-mother"||artifact_placeholder(item)),"MAP_ARTIFACT_LOD_ONLY_SOURCE_MUST_NOT_INVENT_OBJECT_BINDING");}
  else{int type=integer(item.at("object_type"),0,255,"object type"),subtype=integer(item.at("object_subtype"),0,1000,"object subtype");need(type==5||type==6||type==93,"MAP_ARTIFACT_UNSUPPORTED_OBJECT_TYPE");need(objects.contains(identity)&&objects.at(identity).contains({type,subtype}),"MAP_ARTIFACT_IDENTITY_NOT_IN_OFFICIAL_OBJECT_TABLE: "+identity);if(type==5)need(item.at("source_artifact_frame")==subtype,"MAP_ARTIFACT_BACKPACK_IDENTITY_MISMATCH");else if(type==6)need(subtype==0&&item.at("source_artifact_frame").is_null(),"MAP_ARTIFACT_PANDORA_MUST_BE_MAP_ONLY");else need(subtype==0&&item.at("source_artifact_frame")==1,"MAP_ARTIFACT_SPELL_SCROLL_REQUIRES_INVENTORY_FRAME_ONE");}
  if(item.contains("shadow_reference")){int scale=integer(item.at("shadow_reference_scale"),1,8,"official shadow reference scale");auto shadow=decoder.frame(artifact_pin(item.at("shadow_reference")));need(shadow.w==unsigned(job.native.crop.w()*scale)&&shadow.h==unsigned(job.native.crop.h()*scale),"MAP_ARTIFACT_OFFICIAL_SHADOW_REGISTRATION_DRIFT");}
  if(job.native.count>1){need(item.contains("reference_sequence")&&item.at("reference_sequence").size()==std::size_t(job.native.count),"MAP_ARTIFACT_OFFICIAL_ANIMATION_SEQUENCE_REQUIRED");for(const auto& ref:item.at("reference_sequence")){auto picture=decoder.frame(artifact_pin(ref));need(picture.w==reference.w&&picture.h==reference.h,"MAP_ARTIFACT_SEQUENCE_CANVAS_DRIFT");}}jobs.push_back(std::move(job));}
 return jobs;
}
inline void artifact_over(Image& canvas,const Image& layer,int x,int y){
 need(x>=0&&y>=0&&x+layer.w<=canvas.w&&y+layer.h<=canvas.h,"MAP_ARTIFACT_SUBJECT_OUTSIDE_CANVAS");for(int yy=0;yy<layer.h;yy++)for(int xx=0;xx<layer.w;xx++){const auto& s=layer.p[std::size_t(yy)*layer.w+xx];auto& d=canvas.p[std::size_t(y+yy)*canvas.w+x+xx];float remain=1-s.a;d={s.r+d.r*remain,s.g+d.g*remain,s.b+d.b*remain,s.a+d.a*remain};}
}
inline float artifact_shadow_at(const Image& ref,double x,double y){
 int x0=int(std::floor(x)),y0=int(std::floor(y));double fx=x-x0,fy=y-y0,value=0;for(int dy=0;dy<2;dy++)for(int dx=0;dx<2;dx++){int xx=x0+dx,yy=y0+dy;if(xx<0||yy<0||xx>=ref.w||yy>=ref.h)continue;float a=ref.p[std::size_t(yy)*ref.w+xx].a;if(a>0&&a<.999f)value+=a*(dx?fx:1-fx)*(dy?fy:1-fy);}return float(value);
}
inline float artifact_alpha_at(const Image& image,double x,double y){
 int x0=int(std::floor(x)),y0=int(std::floor(y));double fx=x-x0,fy=y-y0,value=0;
 for(int dy=0;dy<2;dy++)for(int dx=0;dx<2;dx++){
  int xx=x0+dx,yy=y0+dy;if(xx<0||yy<0||xx>=image.w||yy>=image.h)continue;
  value+=image.p[std::size_t(yy)*image.w+xx].a*(dx?fx:1-fx)*(dy?fy:1-fy);
 }return float(value);
}
inline void artifact_blur_shadow(Image& image,Rect bounds,int radius){
 if(radius<1)return;std::vector<float> scratch(image.p.size());
 // Three separable box passes approximate a Gaussian without a radius-dependent cost.
 for(int pass=0;pass<3;pass++){
  for(int y=bounds.t;y<bounds.b;y++){
   double sum=0;for(int x=bounds.l-radius;x<=bounds.l+radius;x++)if(x>=bounds.l&&x<bounds.r)sum+=image.p[std::size_t(y)*image.w+x].a;
   for(int x=bounds.l;x<bounds.r;x++){
    scratch[std::size_t(y)*image.w+x]=float(sum/(2*radius+1));
    if(x-radius>=bounds.l)sum-=image.p[std::size_t(y)*image.w+x-radius].a;
    if(x+radius+1<bounds.r)sum+=image.p[std::size_t(y)*image.w+x+radius+1].a;
   }
  }
  for(int x=bounds.l;x<bounds.r;x++){
   double sum=0;for(int y=bounds.t-radius;y<=bounds.t+radius;y++)if(y>=bounds.t&&y<bounds.b)sum+=scratch[std::size_t(y)*image.w+x];
   for(int y=bounds.t;y<bounds.b;y++){
    image.p[std::size_t(y)*image.w+x]={0,0,0,float(sum/(2*radius+1))};
    if(y-radius>=bounds.t)sum-=scratch[std::size_t(y-radius)*image.w+x];
    if(y+radius+1<bounds.b)sum+=scratch[std::size_t(y+radius+1)*image.w+x];
   }
  }
 }
}
inline Json artifact_project_shadow(Image& canvas,const Image& body,Rect drawn,Rect allowed,
                                    double targetX,double targetY,float opacity,int scale){
 double mass=0,meanX=0,meanY=0;
 for(int y=0;y<body.h;y++)for(int x=0;x<body.w;x++){
  double a=body.p[std::size_t(y)*body.w+x].a;mass+=a;
  meanX+=(drawn.l+x+.5)*a;meanY+=(drawn.t+y+.5)*a;
 }
 need(mass>0,"MAP_ARTIFACT_EMPTY_SHADOW_SUBJECT");meanX/=mass;meanY/=mass;
 double anchorX=(drawn.l+drawn.r)*.5,anchorY=drawn.b-.5;
 double rise=anchorY-meanY;need(rise>0,"MAP_ARTIFACT_SHADOW_CONTACT_INVALID");
 double vertical=(anchorY-targetY)/rise;
 need(vertical>0,"MAP_ARTIFACT_OFFICIAL_SHADOW_NOT_ABOVE_CONTACT");
 double horizontal=.65,shear=(anchorX+horizontal*(meanX-anchorX)-targetX)/rise;
 auto project=[&](double x,double y){return std::pair{anchorX+horizontal*(x-anchorX)+shear*(y-anchorY),anchorY+vertical*(y-anchorY)};};
 double fit=1.,margin=std::max(.5,double(scale)*1.2);
 for(double x:{double(drawn.l),double(drawn.r)})for(double y:{double(drawn.t),double(drawn.b)}){
  auto [px,py]=project(x,y);
  if(px<targetX)fit=std::min(fit,std::max(.01,(targetX-allowed.l-margin)/(targetX-px)));
  if(px>targetX)fit=std::min(fit,std::max(.01,(allowed.r-margin-targetX)/(px-targetX)));
  if(py<targetY)fit=std::min(fit,std::max(.01,(targetY-allowed.t-margin)/(targetY-py)));
  if(py>targetY)fit=std::min(fit,std::max(.01,(allowed.b-margin-targetY)/(py-targetY)));
 }
 for(int y=allowed.t;y<allowed.b;y++)for(int x=allowed.l;x<allowed.r;x++){
  double py=targetY+(y+.5-targetY)/fit,px=targetX+(x+.5-targetX)/fit;
  double bodyY=anchorY+(py-anchorY)/vertical;
  double bodyX=anchorX+(px-anchorX-shear*(bodyY-anchorY))/horizontal;
  canvas.p[std::size_t(y)*canvas.w+x]={0,0,0,opacity*artifact_alpha_at(body,bodyX-drawn.l-.5,bodyY-drawn.t-.5)};
 }
 int radius=std::max(1,int(std::lround(scale*.4)));
 artifact_blur_shadow(canvas,allowed,radius);
 return{{"method","high-resolution-subject-alpha-official-centroid-affine-gaussian"},
        {"official_centroid_pixels",{targetX,targetY}},{"opacity",opacity},
        {"projection_horizontal",horizontal},{"projection_vertical",vertical},
        {"projection_shear",shear},{"projection_fit",fit},{"gaussian_box_radius",radius},
        {"source","high-resolution-subject-alpha"},{"native_shadow_pixels_enlarged",false}};
}
inline void artifact_glints(Image& canvas,const std::vector<Image>& sequence,int frame,Rect nativeBody,Rect drawn){
 if(sequence.size()<2)return;const int w=sequence[0].w,h=sequence[0].h;std::vector<float> current(w*h),minimum(w*h,255);std::vector<int> opaque(w*h);for(std::size_t f=0;f<sequence.size();f++)for(int i=0;i<w*h;i++){auto p=sequence[f].p[i];float lum=p.a>.99f?255*(.299f*linear_to_srgb(p.r/p.a)+.587f*linear_to_srgb(p.g/p.a)+.114f*linear_to_srgb(p.b/p.a)):0;minimum[i]=std::min(minimum[i],lum);opaque[i]+=p.a>.99f;if(int(f)==frame)current[i]=lum;}
 std::vector<bool> mask(w*h),visited(w*h);for(int i=0;i<w*h;i++)mask[i]=(current[i]-minimum[i]>75)||(opaque[i]<=int(sequence.size()/2)&&current[i]>140);
 for(int start=0;start<w*h;start++){if(!mask[start]||visited[start])continue;std::vector<int> pending{start};visited[start]=true;Rect bounds{w,h,0,0};double cx=0,cy=0,weight=0;for(std::size_t k=0;k<pending.size();k++){int i=pending[k],x=i%w,y=i/w;bounds.l=std::min(bounds.l,x);bounds.r=std::max(bounds.r,x+1);bounds.t=std::min(bounds.t,y);bounds.b=std::max(bounds.b,y+1);double z=std::max(1.f,current[i]-120);z*=z;cx+=(x+.5)*z;cy+=(y+.5)*z;weight+=z;for(int yy=std::max(0,y-1);yy<=std::min(h-1,y+1);yy++)for(int xx=std::max(0,x-1);xx<=std::min(w-1,x+1);xx++){int next=yy*w+xx;if(mask[next]&&!visited[next]){visited[next]=true;pending.push_back(next);}}}
  cx=drawn.l+(cx/weight-nativeBody.l)*drawn.w()/nativeBody.w();cy=drawn.t+(cy/weight-nativeBody.t)*drawn.h()/nativeBody.h();double radius=std::max(bounds.w(),bounds.h())*.5*std::min(double(drawn.w())/nativeBody.w(),double(drawn.h())/nativeBody.h());radius=std::max(1.,radius);int l=std::max(0,int(std::floor(cx-radius))),r=std::min(canvas.w-1,int(std::ceil(cx+radius))),t=std::max(0,int(std::floor(cy-radius))),b=std::min(canvas.h-1,int(std::ceil(cy+radius)));for(int y=t;y<=b;y++)for(int x=l;x<=r;x++){double dx=std::abs(x+.5-cx),dy=std::abs(y+.5-cy),thickness=std::max(.6,radius*.11);double a=std::max(std::max(std::max(0.,1-dx/radius)*std::exp(-dy*dy/(thickness*thickness)),std::max(0.,1-dy/radius)*std::exp(-dx*dx/(thickness*thickness))),std::exp(-(dx*dx+dy*dy)/(radius*radius*.055)));if(a<1./255)continue;float alpha=float(a);Pixel s{alpha,alpha*srgb_to_linear(.94f),alpha*srgb_to_linear(.64f),alpha};auto& d=canvas.p[std::size_t(y)*canvas.w+x];d={s.r+d.r*(1-alpha),s.g+d.g*(1-alpha),s.b+d.b*(1-alpha),alpha+d.a*(1-alpha)};}
 }
}
inline std::pair<std::vector<std::uint8_t>,Json> render_artifact_map(const ArtifactMapJob& job,Wic& decoder){
 if(artifact_placeholder(job.original)){
  int scale=integer(job.original.at("working_scale"),4,64,"placeholder working scale");
  auto raw=artifact_pin(job.original.at("reference"));
  auto native=decoder.decode_rgba(raw,job.native.w,job.native.h);
  int width=job.native.w*scale,height=job.native.h*scale;
  std::vector<std::uint8_t> rgba(std::size_t(width)*height*4);
  for(int y=0;y<height;y++)for(int x=0;x<width;x++)
   for(int c=0;c<4;c++)rgba[(std::size_t(y)*width+x)*4+c]=native[(std::size_t(y/scale)*job.native.w+x/scale)*4+c];
  auto png=decoder.encode(rgba,width,height,true);
  need(decoder.decode_rgba(png,width,height)==rgba,"MAP_ARTIFACT_PNG_ROUNDTRIP_FAILED");
  return {std::move(png),{{"source_role","official-placeholder"},
   {"canvas_pixels",{width,height}},{"native_working_scale",scale},
   {"label_rendering","exact-official-label-pixel-cells"},{"is_ai_redraw",false},
   {"shadow_method","none-official-placeholder"},{"rgba_sha256",sha(rgba)}}};
 }
 auto sourceRaw=artifact_pin(job.original.at("source"));std::string colorNote;auto source=decoder.decode(sourceRaw,true,colorNote);auto sourceRect=artifact_bbox(source,0.f);if(job.original.contains("source_rect")){auto chosen=rectangle(job.original.at("source_rect"));need(chosen.l<=sourceRect.l&&chosen.t<=sourceRect.t&&chosen.r>=sourceRect.r&&chosen.b>=sourceRect.b,"MAP_ARTIFACT_SOURCE_RECT_CLIPS_ALPHA");sourceRect=chosen;}auto cut=crop_image(source,sourceRect);
 auto referenceRaw=artifact_pin(job.original.at("reference"));std::string note;auto reference=decoder.decode(referenceRaw,true,note);need(reference.w==job.native.w&&reference.h==job.native.h,"MAP_ARTIFACT_REFERENCE_CANVAS_DRIFT");std::vector<Image> sequence;
 if(job.original.contains("reference_sequence"))for(const auto& ref:job.original.at("reference_sequence")){auto raw=artifact_pin(ref);auto picture=decoder.decode(raw,true,note);need(picture.w==reference.w&&picture.h==reference.h,"MAP_ARTIFACT_SEQUENCE_CANVAS_DRIFT");sequence.push_back(std::move(picture));}
 Image stable=reference;for(auto& p:stable.p)p.a=p.a>.999f?1.f:0.f;for(const auto& picture:sequence)for(std::size_t i=0;i<stable.p.size();i++)if(picture.p[i].a<.999f)stable.p[i].a=0;auto nativeBody=artifact_bbox(stable,.999f);
 int scale=std::max(1,int(std::floor(std::max(double(cut.w)/nativeBody.w(),double(cut.h)/nativeBody.h()))));if(job.original.contains("working_scale"))scale=integer(job.original.at("working_scale"),1,256,"working scale");int width=job.native.w*scale,height=job.native.h*scale;need(width<=MAX_AXIS&&height<=MAX_AXIS&&std::uint64_t(width)*height<=MAX_PIXELS,"MAP_ARTIFACT_WORKING_CANVAS_LIMIT");double factor=std::min({1.,double(nativeBody.w()*scale)/cut.w,double(nativeBody.h()*scale)/cut.h});int drawW=std::max(1,int(std::lround(cut.w*factor))),drawH=std::max(1,int(std::lround(cut.h*factor)));double sx=double(drawW)/cut.w,sy=double(drawH)/cut.h;need(std::abs(sx-sy)<=1./std::min(cut.w,cut.h)+1e-9,"MAP_ARTIFACT_NONUNIFORM_RESIZE_REJECTED");int x=int(std::lround((nativeBody.l+nativeBody.w()*.5)*scale-drawW*.5)),y=nativeBody.b*scale-drawH;Rect drawn{x,y,x+drawW,y+drawH};
 Image shadowReference=reference;int shadowScale=1,shadowX=0,shadowY=0;if(job.original.contains("shadow_reference")){auto shadowRaw=artifact_pin(job.original.at("shadow_reference"));shadowReference=decoder.decode(shadowRaw,true,note);shadowScale=job.original.at("shadow_reference_scale");shadowX=job.native.crop.l;shadowY=job.native.crop.t;for(const auto& p:shadowReference.p)need(p.a<.999f&&std::max({p.r,p.g,p.b})-std::min({p.r,p.g,p.b})<.01f,"MAP_ARTIFACT_SEPARATE_SHADOW_CONTAINS_BODY_COLOR");}
 Image canvas(width,height);double sum=0,cx=0,cy=0;float shadowOpacity=0;for(int yy=0;yy<shadowReference.h;yy++)for(int xx=0;xx<shadowReference.w;xx++){float a=shadowReference.p[std::size_t(yy)*shadowReference.w+xx].a;if(a>0&&a<.999f){shadowOpacity=std::max(shadowOpacity,a);sum+=a;cx+=(shadowX+(xx+.5)/shadowScale)*a;cy+=(shadowY+(yy+.5)/shadowScale)*a;}}
 auto body=quality_resize_cpu(cut,drawW,drawH);Json shadowDetails=nullptr;
 if(sum>0){Rect allowed{job.native.crop.l*scale,job.native.crop.t*scale,job.native.crop.r*scale,job.native.crop.b*scale};shadowDetails=artifact_project_shadow(canvas,body,drawn,allowed,cx/sum*scale,cy/sum*scale,shadowOpacity,scale);}
 artifact_over(canvas,body,x,y);artifact_glints(canvas,sequence,job.original.at("key")[3].get<int>(),nativeBody,drawn);auto rgba=quantize(canvas,true);auto png=decoder.encode(rgba,width,height,true);need(decoder.decode_rgba(png,width,height)==rgba,"MAP_ARTIFACT_PNG_ROUNDTRIP_FAILED");Json angle=nullptr;if(sum>0)angle=std::atan2(nativeBody.b-cy/sum,cx/sum-(nativeBody.l+nativeBody.w()*.5))*180/3.14159265358979323846;
 Json metadata={{"source_pixels",{source.w,source.h}},{"source_rect",artifact_rect_json(sourceRect)},{"native_body_bbox",artifact_rect_json(nativeBody)},{"drawn_body_bbox",artifact_rect_json(drawn)},{"canvas_pixels",{width,height}},{"native_working_scale",scale},{"source_scale_x",sx},{"source_scale_y",sy},{"aspect_error",std::abs(sx-sy)},{"filter_algorithm",QUALITY_ALGORITHM},{"source_resampled_once",drawW!=cut.w||drawH!=cut.h},{"source_color_management",colorNote},{"shadow_method",sum>0?"high-resolution-subject-alpha-official-centroid-affine-gaussian":"none-in-official-reference"},{"shadow_projection",shadowDetails},{"official_shadow_angle_degrees",angle},{"shadow_derived_from_ai_glow",false},{"native_animation_frames",sequence.size()},{"rgba_sha256",sha(rgba)}};return {std::move(png),metadata};
}
inline Json run_artifact_map_command(const std::string& command,const fs::path& manifestPath,const std::string& manifestSha,const fs::path& outputRoot){
 need(valid_sha(Json(manifestSha)),"MAP_ARTIFACT_MANIFEST_SHA_REQUIRED");auto raw=read_all(manifestPath,32*1024*1024);need(sha(raw)==manifestSha,"MAP_ARTIFACT_MANIFEST_SHA_DRIFT");auto manifest=parse_json(raw);auto jobs=artifact_map_jobs(manifest);auto root=clean_path(outputRoot);for(const auto& job:jobs){contained(root/path(job.filename),root);need(folded(job.source).rfind(folded(root)+L"\\",0)!=0,"MAP_ARTIFACT_OUTPUT_CONTAINS_SOURCE");}
 if(command=="map-artifact-check")return{{"status","READY"},{"ready",jobs.size()},{"outputs_written",0},{"algorithm",MAP_ARTIFACT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"target_kind","high-resolution-master"},{"game_writes",0}};
 auto receiptPath=root/L"map-artifact-receipt.json";bool verify=command=="map-artifact-verify",existing=fs::exists(receiptPath);Json previous;if(existing){previous=read_json(receiptPath);need(previous.value("schema","")=="h3.map-artifact-master-receipt.v1"&&previous.at("manifest_sha256")==manifestSha&&previous.at("outputs").size()==jobs.size(),"MAP_ARTIFACT_RECEIPT_DRIFT");}else need(!verify,"MAP_ARTIFACT_RECEIPT_MISSING");
 if(!verify)fs::create_directories(root);Handle lock(CreateFileW((root/L".map-artifact.lock").c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,verify?OPEN_EXISTING:OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr));need(lock.h!=INVALID_HANDLE_VALUE,"MAP_ARTIFACT_OUTPUT_BUSY");Wic decoder;Json outputs=Json::array();int written=0;
 for(std::size_t i=0;i<jobs.size();i++){const auto& job=jobs[i];auto rendered=render_artifact_map(job,decoder);auto target=root/path(job.filename);auto hash=sha(rendered.first);if(verify||existing){const auto& row=previous.at("outputs")[i];need(row.at("id")==job.id&&row.at("png_sha256")==hash&&row.at("render")==rendered.second&&folded(path(row.at("path")))==folded(target)&&read_all(target)==rendered.first,"MAP_ARTIFACT_REPLAY_FAILED: "+job.id);}else{bool had=fs::exists(target);same_or_new(target,rendered.first);written+=!had;}
  artifact_pin(job.original.at("source"));artifact_pin(job.original.at("reference"));outputs.push_back({{"id",job.id},{"key",job.original.at("key")},{"resource",job.original.at("resource")},{"source",job.original.at("source")},{"path",str(target)},{"png_sha256",hash},{"bytes",rendered.first.size()},{"render",rendered.second}});}
 need(sha(read_all(manifestPath,32*1024*1024))==manifestSha,"MAP_ARTIFACT_MANIFEST_CHANGED_DURING_BATCH");artifact_objects(manifest);artifact_pin(manifest.at("official_inventory"));for(const auto& job:jobs){artifact_native_geometry(job.original);artifact_pin(job.original.at("source"));artifact_pin(job.original.at("reference"));if(job.original.contains("shadow_reference"))artifact_pin(job.original.at("shadow_reference"));if(job.original.contains("reference_sequence"))for(const auto& ref:job.original.at("reference_sequence"))artifact_pin(ref);}if(!existing){Json receipt={{"schema","h3.map-artifact-master-receipt.v1"},{"created_utc",now()},{"manifest_sha256",manifestSha},{"algorithm",MAP_ARTIFACT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"target_kind","high-resolution-master"},{"outputs",outputs},{"art_approved",false},{"game_deployed",false}};write_json_new(receiptPath,receipt);}
 return{{"status",verify?"VERIFIED":existing?"REUSED_VERIFIED":"EXPORTED_VERIFIED"},{"verified",jobs.size()},{"outputs_written",written},{"manifest_sha256",manifestSha},{"receipt",str(receiptPath)},{"algorithm",MAP_ARTIFACT_ALGORITHM},{"filter_algorithm",QUALITY_ALGORITHM},{"independent_cpu_replay",verify||existing},{"game_writes",0}};
}
}
