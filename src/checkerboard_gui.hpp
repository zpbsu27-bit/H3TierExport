#pragma once

#include "checkerboard_alpha.hpp"
#include "wic.hpp"
#include <cstring>
#include <iomanip>
#include <sstream>

namespace h3tier::checkerboard_gui {

struct BatchOutcome {
 Json receipt;
 fs::path output_directory;
 std::size_t succeeded=0,failed=0;
};

inline fs::path unique_output_directory(const fs::path& sourceDirectory){
 SYSTEMTIME t{};GetLocalTime(&t);
 std::wostringstream name;
 name<<L"Gemini-transparent-"<<std::setfill(L'0')<<std::setw(4)<<t.wYear
     <<std::setw(2)<<t.wMonth<<std::setw(2)<<t.wDay<<L'-'
     <<std::setw(2)<<t.wHour<<std::setw(2)<<t.wMinute<<std::setw(2)<<t.wSecond
     <<L'-'<<GetCurrentProcessId()<<L'-'<<GetTickCount64();
 return sourceDirectory/name.str();
}

inline BatchOutcome process_files(const std::vector<fs::path>& inputs,const fs::path& requestedOutputDirectory={}){
 need(!inputs.empty(),"CHECKERBOARD_INPUTS_REQUIRED");
 BatchOutcome out;Wic wic;
 fs::path parent=clean_path(inputs.front()).parent_path();
 fs::path requestedOutput;
 if(!requestedOutputDirectory.empty()){
  requestedOutput=clean_path(requestedOutputDirectory);
  need(!fs::exists(requestedOutput),"CHECKERBOARD_OUTPUT_DIRECTORY_ALREADY_EXISTS: "+str(requestedOutput));
 }
 Json rows=Json::array();
 for(const auto& rawPath:inputs){
  Json row={{"input_path",str(rawPath)}, {"status","FAILED"}};
  try{
   const auto input=clean_path(rawPath);
   need(input.parent_path()==parent,"CHECKERBOARD_BATCH_MUST_USE_ONE_FOLDER");
   const auto source=read_all(input);
   auto ext=input.extension().wstring();
   std::transform(ext.begin(),ext.end(),ext.begin(),[](wchar_t c){return(c>=L'A'&&c<=L'Z')?wchar_t(c-L'A'+L'a'):c;});
   const bool png=source.size()>=8&&std::memcmp(source.data(),"\x89PNG\r\n\x1a\n",8)==0;
   const bool jpeg=source.size()>=3&&source[0]==0xff&&source[1]==0xd8&&source[2]==0xff;
   need((ext==L".png"&&png)||((ext==L".jpg"||ext==L".jpeg")&&jpeg),"CHECKERBOARD_PNG_OR_JPEG_REQUIRED");
   const auto frame=wic.frame(source);
   auto rgba=wic.decode_rgba(source,(int)frame.w,(int)frame.h);
   auto processed=checkerboard_alpha::remove(rgba,(int)frame.w,(int)frame.h);
   const auto encoded=wic.encode(processed.rgba,(int)frame.w,(int)frame.h,true);
   const auto readback=wic.decode_rgba(encoded,(int)frame.w,(int)frame.h);
   need(readback==processed.rgba,"CHECKERBOARD_OUTPUT_PIXEL_READBACK_MISMATCH");

   if(out.output_directory.empty())out.output_directory=requestedOutput.empty()?unique_output_directory(parent):requestedOutput;
   auto output=out.output_directory/(input.stem().wstring()+L"_transparent.png");
   write_new(output,encoded);
   const auto verify=read_all(output);
   need(sha(verify)==sha(encoded),"CHECKERBOARD_OUTPUT_HASH_READBACK_MISMATCH");
   row.update({{"status","OUTPUT_WRITTEN_REVIEW_REQUIRED"},
               {"input_sha256",sha(source)},{"input_bytes",source.size()},{"input_format",png?"PNG":"JPEG"},
               {"output_path",str(output)},{"output_sha256",sha(verify)},
               {"output_bytes",verify.size()},{"pixels",{frame.w,frame.h}},
               {"checkerboard_tile_px",processed.tile_size},
               {"checkerboard_phase",processed.phase},
               {"checkerboard_palette_rgb",Json::array({Json::array({processed.colors[0].r,processed.colors[0].g,processed.colors[0].b}),Json::array({processed.colors[1].r,processed.colors[1].g,processed.colors[1].b})})},
               {"edge_coverage",processed.edge_coverage},{"edge_match",processed.edge_match},
               {"transparent_pixels",processed.transparent_pixels},
               {"softened_edge_pixels",processed.softened_pixels},
               {"opaque_pixels",processed.opaque_pixels}});
   ++out.succeeded;
  }catch(const std::exception& error){row["error"]=error.what();++out.failed;}
  rows.push_back(std::move(row));
 }
 out.receipt={{"schema","h3.checkerboard-background-removal.v1"},
              {"algorithm","edge-connected-periodic-checkerboard-matting-v1"},
              {"created_utc",now()},{"status",out.succeeded?"OUTPUTS_WRITTEN_REVIEW_REQUIRED":"NO_OUTPUT_WRITTEN"},
              {"source_pngs_modified",0},{"art_approval",false},
              {"succeeded",out.succeeded},{"failed",out.failed},{"items",rows}};
 if(out.succeeded){
  out.receipt["output_directory"]=str(out.output_directory);
  out.receipt["source_files_modified"]=0;
  write_json_new(out.output_directory/L"checkerboard-removal-receipt.json",out.receipt);
 }
 return out;
}

} // namespace h3tier::checkerboard_gui
