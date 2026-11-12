#pragma once
#include "wic.hpp"
#include <algorithm>
#include <array>
#include <limits>
#include <optional>
#include <set>

namespace h3tier {

inline constexpr std::uint64_t APNG_MAX_FRAME_BYTES = 64ull * 1024 * 1024;
inline constexpr std::uint64_t APNG_MAX_INPUT_BYTES = 256ull * 1024 * 1024;
inline constexpr std::uint64_t APNG_MAX_OUTPUT_BYTES = 512ull * 1024 * 1024;
inline constexpr std::size_t APNG_MAX_FRAMES = 300;
inline constexpr std::uint64_t APNG_MAX_FRAME_PIXELS = 16ull * 1024 * 1024;

struct ApngChunk {
 std::array<char,4> type{};
 std::vector<std::uint8_t> data;
 bool operator==(const ApngChunk&) const = default;
};

struct ApngPngInfo {
 std::array<std::uint8_t,13> ihdr{};
 std::vector<ApngChunk> shared;
 std::uint32_t width=0,height=0;
};

struct ApngInput {
 fs::path path;
 std::string sha256;
 std::uint64_t bytes=0;
};

struct ApngPackResult {
 fs::path output;
 std::uint32_t width=0,height=0;
 std::size_t frames=0;
 int fps=0;
 bool loop=false;
 std::uint64_t bytes=0;
 std::string sha256;
};

inline std::uint32_t apng_be32(const std::uint8_t*p){
 return (std::uint32_t(p[0])<<24)|(std::uint32_t(p[1])<<16)|(std::uint32_t(p[2])<<8)|p[3];
}
inline void apng_put32(std::vector<std::uint8_t>&out,std::uint32_t v){
 out.push_back(std::uint8_t(v>>24));out.push_back(std::uint8_t(v>>16));out.push_back(std::uint8_t(v>>8));out.push_back(std::uint8_t(v));
}
inline void apng_put16(std::vector<std::uint8_t>&out,std::uint16_t v){out.push_back(std::uint8_t(v>>8));out.push_back(std::uint8_t(v));}
inline std::uint32_t apng_crc(const std::uint8_t*p,std::size_t n,std::uint32_t crc=0xffffffffu){
 for(std::size_t i=0;i<n;i++){crc^=p[i];for(int bit=0;bit<8;bit++)crc=(crc>>1)^(0xedb88320u&std::uint32_t(-int(crc&1)));}
 return crc;
}
inline std::uint32_t apng_chunk_crc(const std::uint8_t*type,const std::uint8_t*data,std::size_t n){
 return ~apng_crc(data,n,apng_crc(type,4));
}
inline bool apng_type(const std::uint8_t*p,const char*name){return std::memcmp(p,name,4)==0;}
inline bool apng_shared_type(const std::uint8_t*p){
 // These chunks affect pixel interpretation or canvas metadata and must be
 // byte-identical across frames. Text, timestamps, and private ancillary
 // chunks are intentionally omitted from the animation container.
 for(auto name:{"PLTE","tRNS","cHRM","gAMA","iCCP","sBIT","sRGB","bKGD","pHYs","cICP"})
  if(apng_type(p,name))return true;
 return false;
}
inline bool apng_known_critical(const std::uint8_t*p){
 return apng_type(p,"IHDR")||apng_type(p,"PLTE")||apng_type(p,"IDAT")||apng_type(p,"IEND");
}
inline ApngPngInfo apng_inspect_png(const std::vector<std::uint8_t>&raw){
 static constexpr std::uint8_t signature[]={137,80,78,71,13,10,26,10};
 need(raw.size()>=8&&std::memcmp(raw.data(),signature,8)==0,"APNG_INPUT_NOT_PNG");
 ApngPngInfo out;std::size_t at=8;std::uint32_t chunkCount=0;bool haveHeader=false,haveData=false,dataEnded=false,haveEnd=false;
 std::set<std::string> sharedSeen;
 while(at<raw.size()){
  need(++chunkCount<=200000,"APNG_PNG_CHUNK_COUNT_LIMIT");
  need(raw.size()-at>=12,"APNG_PNG_TRUNCATED_CHUNK");
  auto length=apng_be32(raw.data()+at);auto type=raw.data()+at+4;
  need(std::uint64_t(length)<=raw.size()-at-12,"APNG_PNG_CHUNK_OUT_OF_RANGE");
  auto data=raw.data()+at+8;auto stored=apng_be32(data+length);
  need(apng_chunk_crc(type,data,length)==stored,"APNG_PNG_CHUNK_CRC_MISMATCH");
  if(!haveHeader){
   need(apng_type(type,"IHDR")&&length==13,"APNG_PNG_IHDR_MUST_BE_FIRST");
   std::copy_n(data,13,out.ihdr.begin());out.width=apng_be32(data);out.height=apng_be32(data+4);
   need(out.width>0&&out.height>0&&out.width<=MAX_AXIS&&out.height<=MAX_AXIS&&
        std::uint64_t(out.width)*out.height<=APNG_MAX_FRAME_PIXELS,"APNG_FRAME_DIMENSION_OR_PIXEL_LIMIT");
   haveHeader=true;
  }else{
   need(!apng_type(type,"IHDR"),"APNG_PNG_DUPLICATE_IHDR");
   if(apng_type(type,"acTL")||apng_type(type,"fcTL")||apng_type(type,"fdAT"))
    throw std::runtime_error("APNG_INPUT_ALREADY_ANIMATED");
   if(apng_type(type,"IDAT")){
    need(!dataEnded,"APNG_PNG_IDAT_NOT_CONTIGUOUS");haveData=true;
   }else{
    if(haveData)dataEnded=true;
    if(apng_type(type,"IEND")){
     need(length==0&&haveData,"APNG_PNG_INVALID_IEND");haveEnd=true;at+=12;
     need(at==raw.size(),"APNG_PNG_TRAILING_BYTES");break;
    }
    need((std::uint8_t(type[0])&0x20)||apng_known_critical(type),"APNG_PNG_UNKNOWN_CRITICAL_CHUNK");
    if(apng_shared_type(type)){
     need(!dataEnded,"APNG_COLOR_OR_PALETTE_CHUNK_AFTER_IDAT");
     std::string name(reinterpret_cast<const char*>(type),4);
     need(sharedSeen.insert(name).second,"APNG_DUPLICATE_SHARED_PNG_CHUNK");
     ApngChunk chunk;std::copy_n(reinterpret_cast<const char*>(type),4,chunk.type.begin());
     chunk.data.assign(data,data+length);out.shared.push_back(std::move(chunk));
    }
   }
  }
  at+=std::size_t(length)+12;
 }
 need(haveHeader&&haveData&&haveEnd,"APNG_PNG_MISSING_REQUIRED_CHUNK");
 return out;
}

inline void apng_append_chunk(std::vector<std::uint8_t>&out,const char*type,const std::uint8_t*data,std::size_t length){
 need(length<=std::numeric_limits<std::uint32_t>::max(),"APNG_OUTPUT_CHUNK_TOO_LARGE");
 need(out.size()<=APNG_MAX_OUTPUT_BYTES&&length+12<=APNG_MAX_OUTPUT_BYTES-out.size(),"APNG_OUTPUT_SIZE_LIMIT");
 auto start=out.size();apng_put32(out,std::uint32_t(length));out.insert(out.end(),type,type+4);
 if(length)out.insert(out.end(),data,data+length);
 auto crc=apng_chunk_crc(out.data()+start+4,out.data()+start+8,length);apng_put32(out,crc);
}
inline void apng_append_chunk(std::vector<std::uint8_t>&out,const char*type,const std::vector<std::uint8_t>&data){
 apng_append_chunk(out,type,data.data(),data.size());
}

inline ApngPngInfo apng_validate_frame(const fs::path&file,Wic&wic,std::uint64_t&totalBytes){
 auto raw=read_all(file,APNG_MAX_FRAME_BYTES);totalBytes+=raw.size();
 need(totalBytes<=APNG_MAX_INPUT_BYTES,"APNG_TOTAL_INPUT_SIZE_LIMIT");
 auto info=apng_inspect_png(raw);
 auto pixels=wic.decode_rgba(raw,(int)info.width,(int)info.height);
 need(pixels.size()==std::size_t(info.width)*info.height*4,"APNG_FRAME_DECODE_SIZE_MISMATCH");
 return info;
}

// Frames written by different tools disagree on whether the optional colour
// chunks exist at all (sRGB/gAMA/cHRM/pHYs): one batch declares them, the next
// batch is a bare IHDR+IDAT+IEND file. An omission is not a conflict - PNG
// defaults to the same interpretation - so the container adopts the first value
// it sees. Two frames that both declare one chunk with different bytes are a
// real conflict and still stop the export.
inline bool apng_shared_matches(const std::vector<ApngChunk>&declared,const ApngChunk&chunk){
 auto it=std::find_if(declared.begin(),declared.end(),[&](const ApngChunk&c){return c.type==chunk.type;});
 return it!=declared.end()&&it->data==chunk.data;
}
inline void apng_adopt_shared(ApngPngInfo&container,const ApngPngInfo&frame){
 need(frame.ihdr==container.ihdr,"APNG_FRAMES_MUST_SHARE_CANVAS_AND_PNG_ENCODING");
 for(const auto&chunk:frame.shared){
  if(apng_shared_matches(container.shared,chunk))continue;
  auto declared=std::find_if(container.shared.begin(),container.shared.end(),[&](const ApngChunk&c){return c.type==chunk.type;});
  need(declared==container.shared.end(),std::string("APNG_FRAMES_MUST_SHARE_COLOR_AND_PALETTE_METADATA: ")+std::string(chunk.type.data(),4));
  container.shared.push_back(chunk);
 }
}
inline bool apng_shared_compatible(const std::vector<ApngChunk>&container,const std::vector<ApngChunk>&frame){
 for(const auto&chunk:frame)if(!apng_shared_matches(container,chunk))return false;
 return true;
}

inline ApngPackResult pack_apng(const std::vector<fs::path>&sourcePaths,const fs::path&outputPath,int fps,bool loop){
 need(sourcePaths.size()>=2&&sourcePaths.size()<=APNG_MAX_FRAMES,"APNG_REQUIRES_2_TO_300_FRAMES");
 need(fps>=1&&fps<=60,"APNG_FPS_OUT_OF_RANGE");
 auto output=clean_path(outputPath);auto outputExtension=output.extension().wstring();for(auto&c:outputExtension)c=(wchar_t)towlower(c);need(outputExtension==L".png"||outputExtension==L".apng","APNG_OUTPUT_EXTENSION_MUST_BE_PNG");
 std::vector<ApngInput> inputs;inputs.reserve(sourcePaths.size());std::set<std::wstring> seen;
 std::uint64_t totalBytes=0;std::optional<ApngPngInfo> common;Wic wic;
 for(const auto&sourcePath:sourcePaths){
  auto file=clean_path(sourcePath);need(fs::is_regular_file(file),"APNG_INPUT_NOT_REGULAR_FILE");
  auto ext=file.extension().wstring();for(auto&c:ext)c=(wchar_t)towlower(c);need(ext==L".png","APNG_INPUT_EXTENSION_MUST_BE_PNG");
  auto key=folded(file);need(seen.insert(key).second,"APNG_DUPLICATE_INPUT_FILE");need(folded(file)!=folded(output),"APNG_OUTPUT_MATCHES_INPUT");
  auto raw=read_all(file,APNG_MAX_FRAME_BYTES);totalBytes+=raw.size();need(totalBytes<=APNG_MAX_INPUT_BYTES,"APNG_TOTAL_INPUT_SIZE_LIMIT");
  auto info=apng_inspect_png(raw);
  need(std::uint64_t(info.width)*info.height<=APNG_MAX_FRAME_PIXELS,"APNG_FRAME_PIXEL_LIMIT");
  if(common)apng_adopt_shared(*common,info);
  else common=info;
  auto decoded=wic.decode_rgba(raw,(int)info.width,(int)info.height);
  need(decoded.size()==std::size_t(info.width)*info.height*4,"APNG_FRAME_DECODE_SIZE_MISMATCH");
  inputs.push_back({file,sha(raw),raw.size()});
 }
 need(common.has_value(),"APNG_EMPTY_SEQUENCE");

 std::vector<std::uint8_t>out;out.reserve((size_t)std::min<std::uint64_t>(totalBytes+inputs.size()*256,APNG_MAX_OUTPUT_BYTES));
 static constexpr std::uint8_t signature[]={137,80,78,71,13,10,26,10};out.insert(out.end(),std::begin(signature),std::end(signature));
 apng_append_chunk(out,"IHDR",common->ihdr.data(),common->ihdr.size());
 for(const auto&chunk:common->shared)apng_append_chunk(out,chunk.type.data(),chunk.data);
 std::vector<std::uint8_t>actl;apng_put32(actl,(std::uint32_t)inputs.size());apng_put32(actl,loop?0u:1u);apng_append_chunk(out,"acTL",actl);
 std::uint32_t sequence=0;
 for(std::size_t frameIndex=0;frameIndex<inputs.size();frameIndex++){
  const auto&input=inputs[frameIndex];auto raw=read_all(input.path,APNG_MAX_FRAME_BYTES);
  need(raw.size()==input.bytes&&sha(raw)==input.sha256,"APNG_INPUT_CHANGED_DURING_EXPORT");
  auto info=apng_inspect_png(raw);need(info.ihdr==common->ihdr&&apng_shared_compatible(common->shared,info.shared),"APNG_INPUT_METADATA_CHANGED_DURING_EXPORT");
  std::vector<std::uint8_t>fctl;apng_put32(fctl,sequence++);apng_put32(fctl,common->width);apng_put32(fctl,common->height);apng_put32(fctl,0);apng_put32(fctl,0);apng_put16(fctl,1);apng_put16(fctl,(std::uint16_t)fps);fctl.push_back(0);fctl.push_back(0);apng_append_chunk(out,"fcTL",fctl);
  std::size_t at=8;bool inData=false;
  while(at+12<=raw.size()){
   auto length=apng_be32(raw.data()+at);auto type=raw.data()+at+4;auto data=raw.data()+at+8;
   if(apng_type(type,"IDAT")){
    inData=true;
    if(frameIndex==0){need(out.size()<=APNG_MAX_OUTPUT_BYTES&&std::size_t(length)+12<=APNG_MAX_OUTPUT_BYTES-out.size(),"APNG_OUTPUT_SIZE_LIMIT");out.insert(out.end(),raw.begin()+at,raw.begin()+at+std::size_t(length)+12);}
    else if(length){std::vector<std::uint8_t>fdat;fdat.reserve(std::size_t(length)+4);apng_put32(fdat,sequence++);fdat.insert(fdat.end(),data,data+length);apng_append_chunk(out,"fdAT",fdat);}
   }else if(inData)break;
   at+=std::size_t(length)+12;
  }
  need(inData,"APNG_FRAME_HAS_NO_IMAGE_DATA");
 }
 apng_append_chunk(out,"IEND",nullptr,0);
 auto digest=sha(out);write_new(output,out);
 auto readback=read_all(output,APNG_MAX_OUTPUT_BYTES);need(readback==out&&sha(readback)==digest,"APNG_OUTPUT_READBACK_MISMATCH");
 return{output,common->width,common->height,inputs.size(),fps,loop,out.size(),digest};
}

} // namespace h3tier
