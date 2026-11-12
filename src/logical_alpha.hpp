#pragma once

// Producer-bound logical-alpha data rendering. This consumes the frozen
// map-owner contract and hashes the canonical inventory, but never decodes the
// LOD/catalog or claims independent LOD verification.
#include <map>
#include <set>

namespace h3tier {

struct LogicalAlphaPreparedFrame {
 std::string id;
 Json key;
 Json sourceRgbEncoding;
 fs::path source;
 std::string sourceSha,alphaSha,resource,memberSha;
 int sourceW=0,sourceH=0,positive=0;
 std::array<int,2> canvas1{},crop1{},origin1{},canvas3{},crop3{},origin3{};
 std::vector<std::uint8_t> cropPng,canvasPng;
 std::string cropPngSha,canvasPngSha;
 int outputPositive=0,coveredPositive=0;
 Json material=nullptr;
};

inline std::string logical_alpha_ascii(const Json& value,const char* label){
 need(value.is_string(),std::string("LOGICAL_ALPHA_STRING_REQUIRED: ")+label);
 auto result=value.get<std::string>();
 need(!result.empty()&&result.size()<=80,"LOGICAL_ALPHA_STRING_RANGE");
 for(unsigned char c:result)need((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_'||c=='-',
                                 std::string("LOGICAL_ALPHA_ASCII_INVALID: ")+label);
 return result;
}

inline std::string logical_alpha_resource(const Json& value){
 need(value.is_string(),"LOGICAL_ALPHA_RESOURCE_REQUIRED");
 auto result=value.get<std::string>();
 need(!result.empty()&&result.size()<=80,"LOGICAL_ALPHA_RESOURCE_INVALID");
 for(unsigned char c:result)
  need((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_'||c=='-'||c=='.',
       "LOGICAL_ALPHA_RESOURCE_INVALID");
 return result;
}

inline std::string logical_alpha_sha(const Json& value,const char* label){
 need(value.is_string(),std::string("LOGICAL_ALPHA_SHA_REQUIRED: ")+label);
 auto result=value.get<std::string>();
 need(result.size()==64&&result.find_first_not_of("0123456789abcdef")==std::string::npos,
      std::string("LOGICAL_ALPHA_SHA_INVALID: ")+label);
 return result;
}

inline fs::path logical_alpha_absolute_file(const Json& value,const char* label,std::uint64_t limit){
 need(value.is_string()&&!value.get<std::string>().empty(),std::string("LOGICAL_ALPHA_PATH_REQUIRED: ")+label);
 auto supplied=path(value.get<std::string>());
 auto file=clean_path(supplied);
 auto size=fs::file_size(file);
 need(size>0&&size<=limit,std::string("LOGICAL_ALPHA_FILE_SIZE_LIMIT: ")+label);
 return file;
}

inline std::array<int,2> logical_alpha_pair(const Json& value,const char* label,int minimum=1){
 return native_review_pair(value,label,minimum);
}

inline std::string logical_alpha_frame_id(const Json& key){
 need(key.is_array()&&key.size()==4&&key[0]=="def"&&key[2].is_number_integer()&&key[3].is_number_integer(),
      "LOGICAL_ALPHA_KEY_INVALID");
 auto identity=logical_alpha_ascii(key[1],"key.identity");
 int group=integer(key[2],0,65535,"logical_alpha.group");
 int frame=integer(key[3],0,65535,"logical_alpha.frame");
 return identity+"_g"+std::to_string(group)+"_f"+std::to_string(frame);
}

inline std::vector<std::uint8_t> logical_alpha_whiten_rgb(std::vector<std::uint8_t> rgba){
 need(rgba.size()%4==0,"LOGICAL_ALPHA_RGBA_SIZE_INVALID");
 for(std::size_t i=0;i<rgba.size();i+=4){rgba[i]=255;rgba[i+1]=255;rgba[i+2]=255;}
 return rgba;
}

struct LogicalAlphaMaterialInputs {
 Json wire=nullptr,base,handoff,facts,decision,maskContract;
 std::map<std::wstring,Json> files;
 std::map<std::string,std::wstring> admittedSpellings;
 std::map<std::string,Json> recipes,records;
 std::map<std::string,std::vector<std::uint8_t>> boards;
 std::uint64_t bytes=0;

 Json pin(const Json& ref){
  need(ref.is_object()&&ref.contains("path")&&ref.contains("sha256"),"MATERIAL_INPUT_BINDING_REQUIRED");
  auto digest=logical_alpha_sha(ref.at("sha256"),"material input");
  need(ref.at("path").is_string(),"MATERIAL_INPUT_PATH_REQUIRED");
  const auto spelling=ref.at("path").get<std::string>();
  auto admitted=admittedSpellings.find(spelling);
  if(admitted!=admittedSpellings.end()){
   const auto& row=files.at(admitted->second);
   need(row.at("sha256")==digest,"MATERIAL_INPUT_BINDING_CONFLICT");
   if(ref.contains("bytes"))need(ref.at("bytes").is_number_integer()&&ref.at("bytes")==row.at("bytes"),
                                  "MATERIAL_INPUT_SIZE_MISMATCH");
   // Reuse one immutable admission only within this preflight. The complete
   // path/content set is checked again before output and inside the seal.
   return {{"path",row.at("path")},{"sha256",digest}};
  }
  auto file=native_review_input_path(ref.at("path"),"material input");
  auto size=fs::file_size(file);
  need(size>0&&size<=512ull*1024*1024,"MATERIAL_INPUT_SIZE_LIMIT");
  if(ref.contains("bytes"))need(ref.at("bytes").is_number_integer()&&ref.at("bytes").get<std::uint64_t>()==size,
                                 "MATERIAL_INPUT_SIZE_MISMATCH");
  Json row={{"path",str(file)},{"sha256",digest},{"bytes",size}};
  auto key=native_review_path_key(file);auto found=files.find(key);
  if(found!=files.end())need(found->second==row,"MATERIAL_INPUT_BINDING_CONFLICT");
  else{
   need(files.size()<512&&bytes+size<=512ull*1024*1024,"MATERIAL_INPUT_BUDGET_EXCEEDED");
   need(sha(read_all(file,512ull*1024*1024))==digest,"MATERIAL_INPUT_DRIFT");
   files.emplace(key,row);bytes+=size;
  }
  admittedSpellings.emplace(spelling,key);admittedSpellings.emplace(str(file),key);
  return {{"path",str(file)},{"sha256",digest}};
 }
 Json document(const Json& ref){
  auto bound=pin(ref);auto raw=read_all(path(bound.at("path")),16*1024*1024);
  need(sha(raw)==bound.at("sha256").get<std::string>(),"MATERIAL_INPUT_DRIFT");
  auto result=parse_json(raw);need(result.is_object(),"MATERIAL_JSON_OBJECT_REQUIRED");return result;
 }
 void same(const Json& left,const Json& right){need(pin(left)==pin(right),"MATERIAL_REFERENCE_MISMATCH");}
 void collect(const Json& value){
  if(value.is_object()){
   if(value.contains("path")&&value.contains("sha256")&&value.contains("bytes"))pin(value);
   for(const auto& child:value)collect(child);
  }else if(value.is_array())for(const auto& child:value)collect(child);
 }
 std::vector<std::uint8_t> image(Wic& wic,const Json& ref,const std::array<int,2>& pixels,bool requireRgba=true){
  auto bound=pin(ref);auto raw=read_all(path(bound.at("path")),16*1024*1024);
  need(sha(raw)==bound.at("sha256").get<std::string>(),"MATERIAL_INPUT_DRIFT");
  need(raw.size()>=33&&!std::memcmp(raw.data(),"\x89PNG\r\n\x1a\n",8)&&raw[24]==8&&
       (raw[25]==6||(!requireRgba&&raw[25]==2)),"MATERIAL_RGBA8_PNG_REQUIRED");
  need(ref.at("pixels")==pixels&&ref.at("stored_mode")==Json(raw[25]==6?"RGBA":"RGB"),"MATERIAL_PNG_BINDING_MISMATCH");
  auto frame=wic.frame(raw);native_review_require_png(frame,"material input");
  need(frame.w==(UINT)pixels[0]&&frame.h==(UINT)pixels[1],"MATERIAL_PNG_GEOMETRY_INVALID");
  auto rgba=wic.decode_rgba(raw,pixels[0],pixels[1]);
  need(sha(rgba)==ref.at("rgba_sha256").get<std::string>(),"MATERIAL_PNG_PIXELS_MISMATCH");
  std::vector<std::uint8_t> alpha;alpha.reserve(rgba.size()/4);int minimum=255,maximum=0,positive=0;
  for(std::size_t at=3;at<rgba.size();at+=4){auto a=rgba[at];alpha.push_back(a);minimum=std::min(minimum,int(a));maximum=std::max(maximum,int(a));positive+=a>0;}
  need(ref.at("alpha_extrema")==Json::array({minimum,maximum}),"MATERIAL_PNG_ALPHA_MISMATCH");
  if(ref.contains("alpha_plane_sha256"))
   need(sha(alpha)==ref.at("alpha_plane_sha256").get<std::string>()&&ref.at("alpha_positive_pixels")==positive,
        "MATERIAL_MASK_ALPHA_MISMATCH");
  return rgba;
 }
 void verify_wire()const{
  const auto& rows=wire.at("current_files");need(rows.is_array()&&rows.size()==files.size(),"MATERIAL_CURRENT_FILES_MISMATCH");
  std::set<std::wstring> seen;
  for(const auto& row:rows){
   native_review_fields(row,{"path","sha256","bytes"});
   need(row.at("path").is_string(),"MATERIAL_CURRENT_FILES_MISMATCH");
   // Wire rows may only name inputs already admitted from owner documents.
   // Membership is not a new admission or another currentness boundary.
   const auto admitted=admittedSpellings.find(row.at("path").get<std::string>());
   need(admitted!=admittedSpellings.end(),"MATERIAL_CURRENT_FILES_MISMATCH");
   const auto& key=admitted->second;auto found=files.find(key);
   need(seen.insert(key).second&&found!=files.end()&&found->second==row,"MATERIAL_CURRENT_FILES_MISMATCH");
  }
 }
 void recheck()const{
  for(const auto& [key,row]:files){
   // read_all freshly checks the complete path and independent file handle at
   // BOTH pre-write and seal boundaries. Size and SHA bind that same read.
   const auto file=path(row.at("path").get<std::string>());
   const auto expectedBytes=row.at("bytes").get<std::uint64_t>();
   need(native_review_path_key(file)==key,"MATERIAL_INPUT_BINDING_CONFLICT");
   std::vector<std::uint8_t> raw;
   try{raw=read_all(file,expectedBytes);}
   catch(const std::exception& error){throw std::runtime_error(std::string("MATERIAL_INPUT_CHANGED_BEFORE_SEAL: ")+error.what());}
   need(raw.size()==expectedBytes&&sha(raw)==row.at("sha256").get<std::string>(),"MATERIAL_INPUT_CHANGED_BEFORE_SEAL");
  }
 }
};

inline LogicalAlphaMaterialInputs logical_alpha_material_inputs(const Json& declared,const Json& wire,
                                                               const Json& sourceRef){
 native_review_fields(declared,{"schema","data_role","base_manifest","material_handoff","source_facts","source_decision"});
 need(declared.at("schema")=="h3.logical-alpha.road-material-input.v1"&&declared.at("data_role")=="road-material-3x",
      "MATERIAL_SOURCE_CONTRACT_SCHEMA_UNSUPPORTED");
 native_review_fields(wire,{"base_manifest","material_handoff","source_facts","source_decision","mask_contract","current_files"});
 LogicalAlphaMaterialInputs context;context.wire=wire;context.pin(sourceRef);
 for(const auto* name:{"base_manifest","material_handoff","source_facts","source_decision"}){
  native_review_fields(declared.at(name),{"path","sha256"});context.same(declared.at(name),wire.at(name));
 }
 context.base=context.document(wire.at("base_manifest"));context.handoff=context.document(wire.at("material_handoff"));
 context.facts=context.document(wire.at("source_facts"));context.decision=context.document(wire.at("source_decision"));
 const auto& decision=context.decision;const auto& facts=context.facts;
 need(context.base.at("schema")=="h3.map-ui.road-material-logical-candidate-input.v1"&&
      context.handoff.at("schema")=="h3.map-ui.road-material-consumer-handoff-proposal.v1"&&
      facts.at("schema")=="h3.map-ui.road51-source-native-facts-readback.v1"&&
      decision.at("schema")=="h3.map-ui.road51.Root-readonly-data-decision.v1"&&
      decision.at("status")=="READONLY_ROAD51_INPUT_FACTS_ACCEPTED_FOR_TOOL_MATERIAL_ROLE_REVIEW",
      "MATERIAL_OWNER_DATA_SCHEMA_UNSUPPORTED");
 need(context.base.at("frames").is_array()&&context.base.at("frames").size()==51,
      "MATERIAL_KEY_ORDER_OR_GRID_INVALID");
 need(context.handoff.at("material_sources").is_array()&&context.handoff.at("material_sources").size()==3,
      "MATERIAL_FAMILY_BINDING_INVALID");
 need(decision.at("boundaries").at("this_is_not_Map_writer_authority")==true&&
      decision.at("historical_source_data_semantics").at("Tool_independent_lod_verified")==false,"MATERIAL_OWNER_DATA_BOUNDARY_INVALID");
 context.same(decision.at("fact_readback"),wire.at("source_facts"));
 context.same(decision.at("base_manifest"),wire.at("base_manifest"));
 context.same(decision.at("consumer_handoff"),wire.at("material_handoff"));
 context.same(context.handoff.at("base_manifest"),wire.at("base_manifest"));
 context.same(facts.at("immutable_inputs").at("base_manifest"),wire.at("base_manifest"));
 context.same(facts.at("immutable_inputs").at("consumer_handoff"),wire.at("material_handoff"));
 context.same(facts.at("current_source").at("map"),decision.at("current_source"));
 context.same(facts.at("current_source").at("town"),decision.at("current_native_reader"));
 for(const auto* name:{"current_source","immutable_inputs","current_material_inputs","historical_sampling"})context.collect(facts.at(name));
 int contracts=0;
 for(const auto& row:context.base.at("provenance"))if(row.at("role")=="source-data-contract-r01.json"){
  ++contracts;context.same(row,wire.at("mask_contract"));
 }
 need(contracts==1,"MATERIAL_MASK_CONTRACT_REQUIRED");context.maskContract=context.document(wire.at("mask_contract"));
 return context;
}

inline Json logical_alpha_material_recipe(LogicalAlphaMaterialInputs& context,const Json& row,const Json& material,int frame){
 auto reference=[&](const Json& ref){
  return context.pin(ref);
 };
 const auto& masks=row.at("logical_masks");
 return {{"board",reference(material.at("sampled_board_3x_png"))},
         {"mask_crop_3x",reference(masks.at("crop_3x"))},{"mask_canvas_3x",reference(masks.at("canvas_3x"))},
         {"cell_3x",Json::array({frame%6*96,frame/6*96,96,96})},{"output_layout","native-crop"}};
}

inline void logical_alpha_bind_material(LogicalAlphaMaterialInputs& context,const Json& contract,Wic& wic){
 const std::array<std::string,3> families={"DIRTRD","GRAVRD","COBBRD"};
 const auto& base=context.base;const auto& facts=context.facts;const auto& materials=context.handoff.at("material_sources");
 const auto& factMaterials=facts.at("current_material_inputs");
 context.same(facts.at("immutable_inputs").at("native_catalog"),contract.at("source_authority").at("native_catalog"));
 auto catalog=path(context.pin(facts.at("immutable_inputs").at("native_catalog")).at("path").get<std::string>());
 auto h3Root=catalog.parent_path().parent_path().parent_path();
 if(_wcsicmp(h3Root.filename().c_str(),L"assets")==0&&_wcsicmp(h3Root.parent_path().filename().c_str(),L"H3HD")==0)
  h3Root=h3Root.parent_path().parent_path();
 auto mapKey=native_review_path_key(path(facts.at("current_source").at("map").at("path")));
 auto townKey=native_review_path_key(path(facts.at("current_source").at("town").at("path")));
 need((mapKey==native_review_path_key(h3Root/L"tools"/L"map_ui_batch_r16.py")||
       mapKey==native_review_path_key(h3Root/L"H3HD"/L"tools"/L"map_ui_batch_r16.py"))&&
      (townKey==native_review_path_key(h3Root/L"tools"/L"art_town_batch_r16.py")||
       townKey==native_review_path_key(h3Root/L"H3HD"/L"tools"/L"art_town_batch_r16.py")),"MATERIAL_CURRENT_SOURCE_PATH_INVALID");
 native_review_fields(base.at("source_key_order"),{"identities","frames_per_identity","keys"});
 need(base.at("frames").is_array()&&base.at("frames").size()==51&&
      base.at("source_key_order").at("identities")==Json::array({"DIRTRD","GRAVRD","COBBRD"})&&
      base.at("source_key_order").at("frames_per_identity")==contract.at("source_key_order").at("frames_per_identity")&&
      base.at("source_key_order").at("keys")==contract.at("source_key_order").at("keys")&&
      base.at("board_layout").at("grid")==Json::array({6,3})&&
      base.at("board_layout").at("tile_canvas_3x")==Json::array({96,96}),"MATERIAL_KEY_ORDER_OR_GRID_INVALID");
 need(materials.is_array()&&materials.size()==3&&factMaterials.is_array()&&factMaterials.size()==3,
      "MATERIAL_FAMILY_BINDING_INVALID");
 for(int family=0;family<3;family++){
  const auto& material=materials[family];const auto& fact=factMaterials[family];
  need(material.at("identity")==families[family]&&fact.at("family")==families[family],"MATERIAL_FAMILY_BINDING_INVALID");
  for(const auto& names:std::array<std::pair<const char*,const char*>,4>{{{"raw_png","raw"},
       {"generation_request","generation_request"},{"sampled_board_3x_png","board3x"},{"qualified_sampling_receipt","frozen_sampling_receipt"}}})
   context.same(material.at(names.first),fact.at(names.second));
  const auto rawSize=logical_alpha_pair(material.at("raw_png").at("pixels"),"material raw pixels");
  context.image(wic,material.at("raw_png"),rawSize,false);
  auto board=context.image(wic,material.at("sampled_board_3x_png"),{576,288});
  for(std::size_t at=3;at<board.size();at+=4)need(board[at]==255,"MATERIAL_BOARD_MUST_BE_OPAQUE");
  auto sampling=context.document(material.at("qualified_sampling_receipt"));
  need(sampling.at("schema")=="h3.master-expand.receipt.v1"&&sampling.at("target_scale")==3&&
       sampling.at("source_sha256")==material.at("raw_png").at("sha256")&&
       sampling.at("contract").at("entry").at("source_sha256")==material.at("raw_png").at("sha256")&&
       sampling.at("source_pixels")==material.at("raw_png").at("pixels")&&
       sampling.at("png_sha256")==material.at("sampled_board_3x_png").at("sha256")&&
       sampling.at("output_pixels")==Json::array({576,288})&&
       sampling.at("rgba_sha256")==material.at("sampled_board_3x_png").at("rgba_sha256"),"MATERIAL_FROZEN_SAMPLING_BINDING_INVALID");
  context.boards.emplace(families[family],std::move(board));
  for(int frame=0;frame<17;frame++){
   const int index=family*17+frame;const auto& row=base.at("frames")[index];const auto& old=contract.at("frames")[index];
   const auto& key=contract.at("source_key_order").at("keys")[index];auto id=logical_alpha_frame_id(key);
   need(logical_alpha_frame_id(row.at("key"))==id&&logical_alpha_frame_id(old.at("key"))==id&&
        integer(row.at("order_index"),0,50,"material order")==index&&row.at("material_identity")==families[family]&&
        row.at("native_lod_resource")==old.at("native_lod_resource")&&
        row.at("native_member_payload_sha256")==old.at("native_member_payload_sha256"),"MATERIAL_KEY_GEOMETRY_OR_CELL_INVALID");
   const auto& g1=row.at("native_geometry_1x");const auto& g3=row.at("geometry_3x");
   native_review_fields(g1,{"canvas","crop","origin"});native_review_fields(g3,{"canvas","crop","origin"});
   need(g1==old.at("native_geometry_1x"),"MATERIAL_KEY_GEOMETRY_OR_CELL_INVALID");
   for(const auto* name:{"canvas","crop","origin"}){
    auto one=logical_alpha_pair(g1.at(name),"material geometry",std::string(name)=="origin"?0:1);
    auto three=logical_alpha_pair(g3.at(name),"material geometry3",std::string(name)=="origin"?0:1);
    need(three==std::array<int,2>{one[0]*3,one[1]*3},"MATERIAL_KEY_GEOMETRY_OR_CELL_INVALID");
   }
   need(g3.at("canvas")==Json::array({96,96}),"MATERIAL_CANVAS_MUST_BE_96");
   Json cell=Json::array({frame%6*96,frame/6*96,96,96});const auto& declaredCell=row.at("material_cell");
   need(integer(declaredCell.at("frame"),0,16,"material cell frame")==frame&&
        declaredCell.at("sampled_board_rect_3x_xywh")==cell,"MATERIAL_KEY_GEOMETRY_OR_CELL_INVALID");
   for(const auto& value:declaredCell.at("sampled_board_rect_3x_xywh"))integer(value,0,576,"material cell coordinate");
   const auto& masks=row.at("logical_masks");context.collect(masks);
   context.records.emplace(id,row);
   context.recipes.emplace(id,logical_alpha_material_recipe(context,row,material,frame));
  }
 }
 context.verify_wire();
}

inline std::vector<std::uint8_t> logical_alpha_material_crop(LogicalAlphaMaterialInputs& context,
                                                           const LogicalAlphaPreparedFrame& frame,Wic& wic){
 const auto& row=context.records.at(frame.id);const auto& masks=row.at("logical_masks");
 need(frame.material==context.recipes.at(frame.id),"MATERIAL_FRAME_BINDING_MISMATCH");
 auto one=context.image(wic,masks.at("crop_1x"),frame.crop1);
 std::vector<std::uint8_t> oneAlpha;oneAlpha.reserve(one.size()/4);
 for(std::size_t at=3;at<one.size();at+=4)oneAlpha.push_back(one[at]);
 need(sha(oneAlpha)==frame.alphaSha,"MATERIAL_NATIVE_ALPHA_BINDING_INVALID");
 auto crop=context.image(wic,masks.at("crop_3x"),frame.crop3);
 auto canvas=context.image(wic,masks.at("canvas_3x"),frame.canvas3);
 const int cw=frame.crop3[0],ch=frame.crop3[1],ox=frame.origin3[0],oy=frame.origin3[1];
 for(int y=0;y<96;y++)for(int x=0;x<96;x++){
  auto a=canvas[(std::size_t(y)*96+x)*4+3];bool inside=x>=ox&&y>=oy&&x<ox+cw&&y<oy+ch;
  need(inside?a==crop[(std::size_t(y-oy)*cw+x-ox)*4+3]:a==0,"MATERIAL_CROP_CANVAS_ALPHA_MISMATCH");
 }
 const auto& board=context.boards.at(frame.key[1].get<std::string>());
 const auto& cell=frame.material.at("cell_3x");int left=cell[0].get<int>(),top=cell[1].get<int>();
 std::vector<std::uint8_t> result(std::size_t(cw)*ch*4,0);
 for(int y=0;y<ch;y++)for(int x=0;x<cw;x++){
  auto dst=(std::size_t(y)*cw+x)*4;auto alpha=crop[dst+3];
  if(alpha){auto src=(std::size_t(top+oy+y)*576+left+ox+x)*4;std::copy_n(board.data()+src,3,result.data()+dst);}
  result[dst+3]=alpha;
 }
 return result;
}

inline Json render_logical_alpha(const fs::path& requestedSpecPath,const std::string& expectedSpecSha,
                                const fs::path& requestedOutdir,const std::string& requestedOperationId){
 need(expectedSpecSha.size()==64&&expectedSpecSha.find_first_not_of("0123456789abcdef")==std::string::npos,
      "LOGICAL_ALPHA_SPEC_SHA256_INVALID");
 need(!requestedOperationId.empty()&&requestedOperationId.size()<=80,"LOGICAL_ALPHA_OPERATION_ID_INVALID");
 for(unsigned char c:requestedOperationId)
  need((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_'||c=='-',"LOGICAL_ALPHA_OPERATION_ID_INVALID");

 const auto productRoot=native_review_product_root();
 const auto executable=native_review_executable_path();
 const auto specPath=clean_path(requestedSpecPath);
 const auto outdir=native_review_output_path(requestedOutdir);
 const auto specRaw=read_all(specPath,16*1024*1024);
 need(sha(specRaw)==expectedSpecSha,"LOGICAL_ALPHA_SPEC_SHA256_MISMATCH");
 const auto spec=parse_json(specRaw);
  native_review_fields(spec,{"schema","operation_id","data_role","source_contract","official_inventory",
                             "source_authority","selected_identities","independent_lod_verified","frames"},{"material_inputs"});
 need(spec.at("schema")=="h3.logical-alpha.render-spec.v1","LOGICAL_ALPHA_SPEC_SCHEMA_UNSUPPORTED");
 need(spec.at("operation_id")==requestedOperationId,"LOGICAL_ALPHA_OPERATION_ID_MISMATCH");
  const bool material=spec.at("data_role")=="road-material-3x";
  need(material||spec.at("data_role")=="logical-alpha-mask","LOGICAL_ALPHA_DATA_ROLE_UNSUPPORTED");
  need(material==spec.contains("material_inputs"),"MATERIAL_INPUTS_REQUIRE_EXPLICIT_ROLE");
 need(spec.at("independent_lod_verified")==false,"LOGICAL_ALPHA_LOD_CLAIM_INVALID");
 auto contractRef=spec.at("source_contract");native_review_fields(contractRef,{"path","sha256"});
 const auto contractPath=logical_alpha_absolute_file(contractRef.at("path"),"source contract",16*1024*1024);
 const auto contractSha=logical_alpha_sha(contractRef.at("sha256"),"source contract");
 const auto contractRaw=read_all(contractPath,16*1024*1024);
 need(sha(contractRaw)==contractSha,"LOGICAL_ALPHA_SOURCE_CONTRACT_SHA256_MISMATCH");
  const auto declaredContract=parse_json(contractRaw);
  LogicalAlphaMaterialInputs materialInputs;
  if(material)materialInputs=logical_alpha_material_inputs(declaredContract,spec.at("material_inputs"),contractRef);
  const auto contract=material?materialInputs.maskContract:declaredContract;
 native_review_fields(contract,{"schema","created_utc","archive_root","data_role","request_scope","source_key_order",
                                "source_authority","provenance_evidence","alpha_plane_decode","input_encoding",
                                "summary","claim_boundary","frames"});
 need(contract.at("schema")=="h3.map-ui.road-logical-alpha-source-data-contract.v1",
      "LOGICAL_ALPHA_SOURCE_CONTRACT_SCHEMA_UNSUPPORTED");
 need(contract.at("frames").is_array()&&contract.at("frames").size()==51,"LOGICAL_ALPHA_SOURCE_CONTRACT_FRAME_COUNT_INVALID");
 need(contract.at("data_role")=="1X native logical-alpha input data only; not painted art or a high-resolution master",
      "LOGICAL_ALPHA_SOURCE_DATA_ROLE_INVALID");

 const std::array<std::string,3> familyOrder={"DIRTRD","GRAVRD","COBBRD"};
 auto sourceOrder=contract.at("source_key_order");
 native_review_fields(sourceOrder,{"identities","frames_per_identity","keys","bound_by","prototype_array_order_note"});
 need(sourceOrder.at("identities")==Json::array({"DIRTRD","GRAVRD","COBBRD"})&&
      sourceOrder.at("frames_per_identity").is_array()&&sourceOrder.at("frames_per_identity").size()==17&&
      sourceOrder.at("keys").is_array()&&sourceOrder.at("keys").size()==51,
      "LOGICAL_ALPHA_SOURCE_SLOT_ORDER_INVALID");
 for(int family=0;family<3;family++)for(int frame=0;frame<17;frame++){
  const auto index=family*17+frame;
  Json key=Json::array({"def",familyOrder[family],0,frame});
  need(sourceOrder.at("frames_per_identity")[frame]==frame&&sourceOrder.at("keys")[index]==key&&
       contract.at("frames")[index].at("key")==key,
       "LOGICAL_ALPHA_SOURCE_SLOT_ORDER_INVALID");
 }

 auto authority=spec.at("source_authority");
 need(authority==contract.at("source_authority"),"LOGICAL_ALPHA_SOURCE_AUTHORITY_MISMATCH");
 native_review_fields(authority,{"native_image_reader","native_catalog","native_lod","map_ui_producer","claim_boundary"});
 native_review_fields(authority.at("native_image_reader"),{"bytes","path","sha256","symbol"});
 native_review_fields(authority.at("native_catalog"),{"bytes","path","sha256"});
 native_review_fields(authority.at("native_lod"),{"bytes","path","sha256"});
 native_review_fields(authority.at("map_ui_producer"),{"bytes","path","sha256"});
 for(const auto* name:{"native_image_reader","native_catalog","native_lod","map_ui_producer"}){
  logical_alpha_sha(authority.at(name).at("sha256"),"producer authority");
  need(authority.at(name).at("bytes").is_number_integer()&&
       authority.at(name).at("bytes").get<std::int64_t>()>0,"LOGICAL_ALPHA_PRODUCER_AUTHORITY_INVALID");
 }
 need(authority.at("native_image_reader").at("symbol")=="art_town_batch_r16.py::native_image",
      "LOGICAL_ALPHA_NATIVE_READER_SYMBOL_INVALID");

 auto inputEncoding=contract.at("input_encoding");
 native_review_fields(inputEncoding,{"mode_counts","rgb_white_alpha_mask_count","nonwhite_rgb_mask_count","encoding_counts",
                                     "observed_encoding","neutral_encoding_required_for_current_inputs",
                                     "if_future_source_rgb_is_nonwhite"});
 need(inputEncoding.at("mode_counts")==Json{{"RGBA",51}}&&inputEncoding.at("encoding_counts").is_object()&&
      inputEncoding.at("rgb_white_alpha_mask_count").is_number_integer()&&
      inputEncoding.at("nonwhite_rgb_mask_count").is_number_integer()&&
      inputEncoding.at("neutral_encoding_required_for_current_inputs").is_boolean(),
      "LOGICAL_ALPHA_INPUT_ENCODING_SUMMARY_INVALID");
 int encodingCountTotal=0;
 for(auto it=inputEncoding.at("encoding_counts").begin();it!=inputEncoding.at("encoding_counts").end();++it){
  need(it.value().is_number_integer()&&it.value().get<std::int64_t>()>=0,
       "LOGICAL_ALPHA_INPUT_ENCODING_SUMMARY_INVALID");
  encodingCountTotal+=it.value().get<int>();
 }
 need(encodingCountTotal==51,"LOGICAL_ALPHA_INPUT_ENCODING_SUMMARY_INVALID");
 const auto whiteMaskCount=inputEncoding.at("rgb_white_alpha_mask_count").get<int>();
 const auto nonwhiteMaskCount=inputEncoding.at("nonwhite_rgb_mask_count").get<int>();
 need(whiteMaskCount>=0&&nonwhiteMaskCount>=0&&whiteMaskCount+nonwhiteMaskCount==51&&
      inputEncoding.at("neutral_encoding_required_for_current_inputs").get<bool>()==(nonwhiteMaskCount>0),
      "LOGICAL_ALPHA_INPUT_ENCODING_SUMMARY_INVALID");

 auto inventoryRef=spec.at("official_inventory");native_review_fields(inventoryRef,{"path","sha256"});
 const auto inventoryPath=logical_alpha_absolute_file(inventoryRef.at("path"),"canonical inventory",512ull*1024*1024);
 const auto inventorySha=logical_alpha_sha(inventoryRef.at("sha256"),"canonical inventory");
 const auto catalogBytes=authority.at("native_catalog").at("bytes");
 need(catalogBytes.is_number_integer()&&catalogBytes.get<std::int64_t>()>0&&
      authority.at("native_catalog").at("sha256")==inventorySha&&
      std::uint64_t(catalogBytes.get<std::int64_t>())==fs::file_size(inventoryPath),
      "LOGICAL_ALPHA_CANONICAL_INVENTORY_SHA_MISMATCH");
 const auto inventoryRaw=read_all(inventoryPath,512ull*1024*1024);
 need(sha(inventoryRaw)==inventorySha,"LOGICAL_ALPHA_CANONICAL_INVENTORY_BYTES_CHANGED");
 need(path(authority.at("native_catalog").at("path"))==inventoryPath,
      "LOGICAL_ALPHA_INVENTORY_PATH_DIFFERS_FROM_PRODUCER_CONTRACT");

 const auto buildBinding=native_review_build_binding(productRoot,executable,{"src/logical_alpha.hpp"});

 const auto safeBase=native_review_output_path(productRoot/L"build"/L"experiments"/L"logical-alpha");
 need(native_review_path_key(outdir.parent_path())==native_review_path_key(safeBase),
      "LOGICAL_ALPHA_OUTPUT_MUST_BE_NEW_DIRECT_OPERATION_CHILD");
 need(outdir.filename().wstring()==wide(requestedOperationId),"LOGICAL_ALPHA_OUTPUT_LEAF_MUST_MATCH_OPERATION_ID");
 need(native_review_within(outdir,safeBase),"LOGICAL_ALPHA_OUTPUT_OUTSIDE_IGNORED_ROOT");
 for(const auto& pathValue:{specPath,contractPath,inventoryPath})
  need(!native_review_overlap(pathValue,outdir),"LOGICAL_ALPHA_INPUT_OUTPUT_OVERLAP");


 const auto staging=native_review_output_path(safeBase/(L"."+wide(requestedOperationId)+L".staging"));
 need(!fs::exists(outdir),"LOGICAL_ALPHA_FINAL_ALREADY_EXISTS");
 need(!fs::exists(staging),"LOGICAL_ALPHA_STAGING_PRESENT_NO_REPLAY");
 const auto& selected=spec.at("selected_identities");
 need(selected.is_array()&&!selected.empty()&&selected.size()<=3,"LOGICAL_ALPHA_IDENTITY_SELECTION_INVALID");
 std::set<std::string> selectedIds;
 for(const auto& value:selected){
  auto identity=logical_alpha_ascii(value,"selected identity");
  need(identity=="DIRTRD"||identity=="GRAVRD"||identity=="COBBRD","LOGICAL_ALPHA_IDENTITY_UNSUPPORTED");
  need(selectedIds.insert(identity).second,"LOGICAL_ALPHA_IDENTITY_DUPLICATE");
 }
 const auto& frameSpecs=spec.at("frames");
 need(frameSpecs.is_array()&&frameSpecs.size()==17*selectedIds.size()&&frameSpecs.size()<=51,
      "LOGICAL_ALPHA_FRAME_COUNT_INVALID");

 Wic wic;
  bool materialBound=false;
 std::vector<LogicalAlphaPreparedFrame> prepared;
 prepared.reserve(frameSpecs.size());
 std::set<std::string> frameIds;
 std::uint64_t totalOutputPixels=0,totalEncodedBytes=0;
 std::size_t inputBytesTotal=0;
 std::size_t sourceAlphaCells=0;
 std::size_t sourcePositiveTotal=0;
 for(const auto& item:frameSpecs){
  native_review_fields(item,{"id","key","source_png","source_sha256","alpha_plane_sha256","alpha_positive_pixels",
                             "source_rgb_encoding",
                             "source_pixels","native_geometry_1x","geometry_3x","native_lod_resource",
                              "native_member_payload_sha256"},{"material"});
   need(material==item.contains("material"),"MATERIAL_FRAME_REQUIRES_EXPLICIT_ROLE");
  LogicalAlphaPreparedFrame frame;
  frame.key=item.at("key");
  frame.id=logical_alpha_ascii(item.at("id"),"frame.id");
   if(material)frame.material=item.at("material");
  need(frame.id==logical_alpha_frame_id(frame.key),"LOGICAL_ALPHA_FRAME_ID_KEY_MISMATCH");
  need(frameIds.insert(frame.id).second,"LOGICAL_ALPHA_FRAME_ID_DUPLICATE");
  const std::string identity=logical_alpha_ascii(frame.key[1],"key.identity");
  need(selectedIds.contains(identity),"LOGICAL_ALPHA_FRAME_NOT_IN_SELECTED_IDENTITY");
  need(frame.key[0]=="def"&&integer(frame.key[2],0,65535,"logical_alpha.group")==0&&
       integer(frame.key[3],0,65535,"logical_alpha.frame")<17,"LOGICAL_ALPHA_KEY_OUTSIDE_FROZEN_ROAD_SCOPE");

  auto record=std::find_if(contract.at("frames").begin(),contract.at("frames").end(),[&](const Json& entry){
   return entry.is_object()&&entry.contains("key")&&entry.at("key")==frame.key;
  });
  need(record!=contract.at("frames").end(),"LOGICAL_ALPHA_KEY_MISSING_FROM_SOURCE_CONTRACT");
  need(record->at("prototype_key")==Json::array({frame.key[1],frame.key[2],frame.key[3]}),
       "LOGICAL_ALPHA_PROTOTYPE_KEY_MISMATCH");
  const auto& sourceEntry=record->at("input_crop_png");
  native_review_fields(sourceEntry,{"alpha_plane_encoding","alpha_plane_sha256","alpha_positive_pixels_pil_verified",
                                    "archive_path","archive_relative_path","bytes","pixels","rgb_encoding","sha256",
                                    "source_scratch_path","stored_mode"});
  need(sourceEntry.at("stored_mode")=="RGBA"&&
       sourceEntry.at("alpha_plane_encoding")=="8-bit alpha bytes in row-major top-to-bottom order from Pillow RGBA decode",
       "LOGICAL_ALPHA_SOURCE_ENCODING_UNSUPPORTED");
  native_review_fields(item.at("source_rgb_encoding"),{"all_rgb_pixels_white","channel_extrema","classification",
                                                         "visible_rgb_unique_count"});
  need(item.at("source_rgb_encoding")==sourceEntry.at("rgb_encoding"),
       "LOGICAL_ALPHA_SOURCE_RGB_BINDING_MISMATCH");
  frame.sourceRgbEncoding=item.at("source_rgb_encoding");
  frame.source=logical_alpha_absolute_file(item.at("source_png"),"source PNG",16*1024*1024);
  const auto recordedSource=logical_alpha_absolute_file(sourceEntry.at("archive_path"),"contract source PNG",16*1024*1024);
  need(recordedSource==frame.source,"LOGICAL_ALPHA_SOURCE_PATH_DIFFERS_FROM_CONTRACT");
  frame.sourceSha=logical_alpha_sha(item.at("source_sha256"),"source PNG");
  frame.alphaSha=logical_alpha_sha(item.at("alpha_plane_sha256"),"source alpha plane");
  frame.resource=logical_alpha_resource(item.at("native_lod_resource"));
  frame.memberSha=logical_alpha_sha(item.at("native_member_payload_sha256"),"native member payload");
  need(frame.sourceSha==sourceEntry.at("sha256").get<std::string>()&&
       frame.alphaSha==sourceEntry.at("alpha_plane_sha256").get<std::string>()&&
       frame.resource==record->at("native_lod_resource").get<std::string>()&&
       frame.memberSha==record->at("native_member_payload_sha256").get<std::string>(),
       "LOGICAL_ALPHA_FRAME_SOURCE_BINDING_MISMATCH");
  need(item.at("source_pixels").is_array()&&item.at("source_pixels").size()==2,
       "LOGICAL_ALPHA_SOURCE_PIXELS_INVALID");
  frame.sourceW=integer(item.at("source_pixels").at(0),1,MAX_AXIS,"logical_alpha.source_width");
  frame.sourceH=integer(item.at("source_pixels").at(1),1,MAX_AXIS,"logical_alpha.source_height");
  need(item.at("source_pixels").is_array()&&item.at("source_pixels").size()==2&&
       sourceEntry.at("pixels")==item.at("source_pixels"),"LOGICAL_ALPHA_SOURCE_PIXELS_MISMATCH");
  const auto& geometry1=item.at("native_geometry_1x");
  need(geometry1==record->at("native_geometry_1x"),"LOGICAL_ALPHA_GEOMETRY_DIFFERS_FROM_SOURCE_CONTRACT");
  frame.canvas1=logical_alpha_pair(geometry1.at("canvas"),"canvas_1x");
  frame.crop1=logical_alpha_pair(geometry1.at("crop"),"crop_1x");
  frame.origin1=logical_alpha_pair(geometry1.at("origin"),"origin_1x",0);
  need(frame.crop1[0]==frame.sourceW&&frame.crop1[1]==frame.sourceH&&
       frame.origin1[0]+frame.crop1[0]<=frame.canvas1[0]&&frame.origin1[1]+frame.crop1[1]<=frame.canvas1[1],
       "LOGICAL_ALPHA_1X_GEOMETRY_INVALID");
  const auto& geometry3=item.at("geometry_3x");
  native_review_fields(geometry3,{"canvas","crop","origin"});
  frame.canvas3=logical_alpha_pair(geometry3.at("canvas"),"canvas_3x");
  frame.crop3=logical_alpha_pair(geometry3.at("crop"),"crop_3x");
  frame.origin3=logical_alpha_pair(geometry3.at("origin"),"origin_3x",0);
  need(frame.canvas3==std::array<int,2>{frame.canvas1[0]*3,frame.canvas1[1]*3}&&
       frame.crop3==std::array<int,2>{frame.crop1[0]*3,frame.crop1[1]*3}&&
       frame.origin3==std::array<int,2>{frame.origin1[0]*3,frame.origin1[1]*3},
       "LOGICAL_ALPHA_GEOMETRY_NOT_EXACTLY_3X");
  need(geometry3==Json{{"canvas",Json::array({frame.canvas1[0]*3,frame.canvas1[1]*3})},
                       {"crop",Json::array({frame.crop1[0]*3,frame.crop1[1]*3})},
                       {"origin",Json::array({frame.origin1[0]*3,frame.origin1[1]*3})}},
       "LOGICAL_ALPHA_GEOMETRY_FIELD_MISMATCH");
  if(material){
   if(!materialBound){
    logical_alpha_bind_material(materialInputs,contract,wic);
    for(const auto& [key,row]:materialInputs.files)
     need(!native_review_overlap(path(row.at("path")),outdir),"LOGICAL_ALPHA_INPUT_OUTPUT_OVERLAP");
    materialBound=true;
   }
   need(frame.material==materialInputs.recipes.at(frame.id),"MATERIAL_FRAME_BINDING_MISMATCH");
  }

  const auto cropPixelsCount=std::uint64_t(frame.crop3[0])*frame.crop3[1];
  const auto canvasPixelsCount=std::uint64_t(frame.canvas3[0])*frame.canvas3[1];
  totalOutputPixels+=cropPixelsCount+canvasPixelsCount;
  need(cropPixelsCount<=4ull*1024*1024&&canvasPixelsCount<=4ull*1024*1024&&
       totalOutputPixels<=16ull*1024*1024,"LOGICAL_ALPHA_BATCH_PIXEL_BUDGET_EXCEEDED");
  const auto inputRaw=read_all(frame.source,16*1024*1024);
  need(sha(inputRaw)==frame.sourceSha,"LOGICAL_ALPHA_SOURCE_PNG_SHA256_MISMATCH");
  need(inputRaw.size()==sourceEntry.at("bytes").get<std::uint64_t>(),"LOGICAL_ALPHA_SOURCE_PNG_SIZE_MISMATCH");
  need(inputRaw.size()>=33&&std::memcmp(inputRaw.data(),"\x89PNG\r\n\x1a\n",8)==0&&inputRaw[24]==8&&inputRaw[25]==6,
       "LOGICAL_ALPHA_SOURCE_NOT_8BIT_RGBA_PNG");
  auto frameInfo=wic.frame(inputRaw);
  need(frameInfo.w==(UINT)frame.sourceW&&frameInfo.h==(UINT)frame.sourceH,"LOGICAL_ALPHA_SOURCE_DIMENSIONS_CHANGED");
  auto rgba=wic.decode_rgba(inputRaw,frame.sourceW,frame.sourceH);
  std::vector<std::uint8_t> alpha;alpha.reserve(std::size_t(frame.sourceW)*frame.sourceH);
  std::array<int,3> rgbMin={255,255,255},rgbMax={0,0,0};
  std::set<std::array<std::uint8_t,3>> visibleColors;
  bool allRgbWhite=true;
  int positive=0;
  for(std::size_t offset=0;offset<rgba.size();offset+=4){
   std::array<std::uint8_t,3> color={rgba[offset],rgba[offset+1],rgba[offset+2]};
   for(int channel=0;channel<3;channel++){
    rgbMin[channel]=std::min(rgbMin[channel],int(color[channel]));
    rgbMax[channel]=std::max(rgbMax[channel],int(color[channel]));
    allRgbWhite=allRgbWhite&&color[channel]==255;
   }
   if(rgba[offset+3]>0)visibleColors.insert(color);
   alpha.push_back(rgba[offset+3]);if(rgba[offset+3]>0)++positive;
  }
  Json actualRgbEncoding={{"all_rgb_pixels_white",allRgbWhite},
   {"channel_extrema",{{rgbMin[0],rgbMax[0]},{rgbMin[1],rgbMax[1]},{rgbMin[2],rgbMax[2]}}},
   {"classification",allRgbWhite?"RGBA_RGB_ALL_WHITE_ALPHA_MASK":"RGBA_RGB_NONWHITE_ALPHA_MASK"},
   {"visible_rgb_unique_count",visibleColors.size()}};
  need(actualRgbEncoding==frame.sourceRgbEncoding,"LOGICAL_ALPHA_SOURCE_RGB_METADATA_MISMATCH");
  need(allRgbWhite||inputEncoding.at("neutral_encoding_required_for_current_inputs").get<bool>(),
       "LOGICAL_ALPHA_NONWHITE_RGB_NOT_DECLARED_FOR_NEUTRALIZATION");
  need(sha(alpha)==frame.alphaSha,"LOGICAL_ALPHA_SOURCE_ALPHA_SHA256_MISMATCH");
  frame.positive=integer(item.at("alpha_positive_pixels"),0,frame.sourceW*frame.sourceH,"source positive pixels");
  need(positive==frame.positive&&frame.positive==integer(sourceEntry.at("alpha_positive_pixels_pil_verified"),0,
      frame.sourceW*frame.sourceH,"contract positive pixels"),"LOGICAL_ALPHA_SOURCE_POSITIVE_COUNT_MISMATCH");

  std::vector<std::uint8_t> cropPixels;
  if(material)cropPixels=logical_alpha_material_crop(materialInputs,frame,wic);
  else{
   std::string colorNote;
   auto decoded=wic.decode(inputRaw,true,colorNote);
   // RGB is data-neutral metadata for this mask role. Normalize its in-memory
   // premultiplied color to white before the single alpha resample.
   for(auto& pixel:decoded.p)pixel.r=pixel.g=pixel.b=pixel.a;
   // One explicit logical-data expansion. Existing material inputs instead
   // copy their frozen 3X RGB/alpha bytes and never enter this sampler.
   auto resized=quality_resize_cpu(decoded,frame.crop3[0],frame.crop3[1]);
   cropPixels=logical_alpha_whiten_rgb(place_rgba(resized,frame.crop3[0],frame.crop3[1],0,0,true));
  }
  int covered=0;
  for(int y=0;y<frame.sourceH;y++)for(int x=0;x<frame.sourceW;x++){
   if(alpha[std::size_t(y)*frame.sourceW+x]==0)continue;
   bool present=false;
   for(int oy=0;oy<3&&!present;oy++)for(int ox=0;ox<3;ox++)
    if(cropPixels[(std::size_t(y*3+oy)*frame.crop3[0]+x*3+ox)*4+3]>0){present=true;break;}
   if(present)++covered;
  }
  need(covered==positive,"LOGICAL_ALPHA_POSITIVE_SOURCE_CELL_LOSS");
  auto canvasPixels=native_review_pixel_copy(cropPixels,frame.crop3[0],frame.crop3[1],
      Rect{0,0,frame.crop3[0],frame.crop3[1]},frame.canvas3[0],frame.canvas3[1],frame.origin3[0],frame.origin3[1]);
  if(!material)canvasPixels=logical_alpha_whiten_rgb(std::move(canvasPixels));
  int outputPositive=0,outsidePositive=0;
  for(int y=0;y<frame.canvas3[1];y++)for(int x=0;x<frame.canvas3[0];x++){
   const int alphaValue=canvasPixels[(std::size_t(y)*frame.canvas3[0]+x)*4+3];
   if(alphaValue>0){++outputPositive;if(x<frame.origin3[0]||y<frame.origin3[1]||
       x>=frame.origin3[0]+frame.crop3[0]||y>=frame.origin3[1]+frame.crop3[1])++outsidePositive;}
  }
  need(outsidePositive==0,"LOGICAL_ALPHA_OUTPUT_OUTSIDE_CROP_NONZERO");
  frame.outputPositive=outputPositive;frame.coveredPositive=covered;
  frame.cropPng=wic.encode(cropPixels,frame.crop3[0],frame.crop3[1],true);
  if(!material)frame.canvasPng=wic.encode(canvasPixels,frame.canvas3[0],frame.canvas3[1],true);
  need(wic.decode_rgba(frame.cropPng,frame.crop3[0],frame.crop3[1])==cropPixels&&
       (material||wic.decode_rgba(frame.canvasPng,frame.canvas3[0],frame.canvas3[1])==canvasPixels),
       "LOGICAL_ALPHA_PNG_PIXEL_ROUNDTRIP_MISMATCH");
  frame.cropPngSha=sha(frame.cropPng);frame.canvasPngSha=sha(frame.canvasPng);

  totalEncodedBytes+=frame.cropPng.size()+frame.canvasPng.size();
  need(totalEncodedBytes<=256ull*1024*1024,"LOGICAL_ALPHA_BATCH_ENCODED_BUDGET_EXCEEDED");
  prepared.push_back(std::move(frame));
  inputBytesTotal+=inputRaw.size();sourceAlphaCells+=alpha.size();sourcePositiveTotal+=positive;
 }
 for(const auto& identity:selectedIds){
  int count=0;for(const auto& frame:prepared)if(frame.key[1]==identity)++count;
  need(count==17,"LOGICAL_ALPHA_SELECTED_FAMILY_MUST_HAVE_17_FRAMES");
 }
 need(!prepared.empty()&&prepared.size()<=51,"LOGICAL_ALPHA_BATCH_EMPTY_OR_OVERSIZED");

 // Only after every source, resample, alpha coverage check, and PNG roundtrip
 // succeeds do we acquire the destination hierarchy and write the batch.

 auto recheckInputs=[&]{
  need(sha(read_all(specPath,16*1024*1024))==expectedSpecSha,"LOGICAL_ALPHA_SPEC_CHANGED_BEFORE_SEAL");
  need(sha(read_all(contractPath,16*1024*1024))==contractSha,"LOGICAL_ALPHA_CONTRACT_CHANGED_BEFORE_SEAL");
  need(sha(read_all(inventoryPath,512ull*1024*1024))==inventorySha,"LOGICAL_ALPHA_INVENTORY_CHANGED_BEFORE_SEAL");
  need(native_review_build_binding(productRoot,executable,{"src/logical_alpha.hpp"})==buildBinding,
       "LOGICAL_ALPHA_BUILD_CHANGED_BEFORE_SEAL");
  const auto boundBuildPath=path(buildBinding.at("build_receipt").at("path"));
  const auto boundBuildRaw=read_all(boundBuildPath,16*1024*1024);
  need(sha(boundBuildRaw)==buildBinding.at("build_receipt").at("sha256").get<std::string>(),"LOGICAL_ALPHA_BUILD_CHANGED_BEFORE_SEAL");
  if(buildBinding.at("test_receipt").is_null()){
   const auto boundBuild=parse_json(boundBuildRaw);
   for(const auto& row:boundBuild.at("sources")){
    // Only a candidate is gated by the mutable Tool checkout. A formal child
    // is bound to its executable and frozen build/tests by the check above.
    const auto file=productRoot/path(row.at("path"));
    need(sha(read_all(file,512ull*1024*1024))==row.at("sha256").get<std::string>(),"LOGICAL_ALPHA_SOURCE_CLOSURE_CHANGED_BEFORE_SEAL");
   }
  }
  for(const auto& frame:prepared)
   need(sha(read_all(frame.source,16*1024*1024))==frame.sourceSha,"LOGICAL_ALPHA_INPUT_CHANGED_BEFORE_SEAL");
   if(material)materialInputs.recheck();
 };
 recheckInputs();
 NativeReviewOutputGuard outputGuard;
 bool stagingOwned=false;
 try{
  outputGuard.ensure_directory(safeBase);
  outputGuard.verify_all();

  outputGuard.create_new_directory(staging);
  stagingOwned=true;outputGuard.keep();
  Json started={{"schema","h3.logical-alpha.start.v1"},{"operation_id",requestedOperationId},
   {"native_spec_sha256",expectedSpecSha},{"source_contract_sha256",contractSha},
   {"selected_identities",selected},{"renderer_sha256",buildBinding.at("executable").at("sha256")},
   {"build_receipt_sha256",buildBinding.at("build_receipt").at("sha256")}};
  const auto startText=started.dump(2)+"\n";
  const std::vector<std::uint8_t> startBytes(startText.begin(),startText.end());
  const auto startSha=outputGuard.write_new(staging/L"logical-alpha-start.json",startBytes);
  Json frameReceipts=Json::array();
  for(const auto& frame:prepared){
   const auto itemDirectory=staging/L"items"/wide(frame.id);
   if(!fs::exists(staging/L"items"))outputGuard.create_new_directory(staging/L"items");
   outputGuard.create_new_directory(itemDirectory);
   const auto cropPath=itemDirectory/L"crop-3x.png";
   const auto canvasPath=itemDirectory/L"canvas-3x.png";
   need(outputGuard.write_new(cropPath,frame.cropPng)==frame.cropPngSha,"LOGICAL_ALPHA_CROP_PNG_SHA_MISMATCH");
    if(!material)need(outputGuard.write_new(canvasPath,frame.canvasPng)==frame.canvasPngSha,"LOGICAL_ALPHA_CANVAS_PNG_SHA_MISMATCH");
   frameReceipts.push_back({{"id",frame.id},{"key",frame.key},{"source_png",str(frame.source)},
     {"source_sha256",frame.sourceSha},{"alpha_plane_sha256",frame.alphaSha},
     {"source_pixels",{frame.sourceW,frame.sourceH}},{"source_positive_pixels",frame.positive},
     {"source_rgb_encoding",frame.sourceRgbEncoding},
     {"native_geometry_1x",{{"canvas",frame.canvas1},{"crop",frame.crop1},{"origin",frame.origin1}}},
     {"geometry_3x",{{"canvas",frame.canvas3},{"crop",frame.crop3},{"origin",frame.origin3}}},
     {"outputs",{{"crop_3x",{{"path","items/"+frame.id+"/crop-3x.png"},
                              {"pixels",frame.crop3},{"bytes",frame.cropPng.size()},{"sha256",frame.cropPngSha}}},
                  {"canvas_3x",{{"path","items/"+frame.id+"/canvas-3x.png"},
                                {"pixels",frame.canvas3},{"bytes",frame.canvasPng.size()},{"sha256",frame.canvasPngSha}}}}},
     {"source_positive_cells_covered",frame.coveredPositive},{"source_positive_cells_missing",0},
     {"output_alpha_positive_pixels",frame.outputPositive},{"output_outside_crop_positive_pixels",0}});
    if(material){
     auto& row=frameReceipts.back();row["material"]=frame.material;row["outputs"].erase("canvas_3x");
     auto& crop=row["outputs"]["crop_3x"];crop["layout"]="native-crop";
     crop["canvas_pixels"]=frame.canvas3;crop["origin_pixels"]=frame.origin3;
    }
  }
  Json receipt={{"schema","h3.logical-alpha.completion.v2"},{"created_utc",now()},

    {"status","COMPLETED_REVIEW_REQUIRED"},{"operation_id",requestedOperationId},
    {"publication",{{"mode","same-volume-no-overwrite-directory-rename"},{"layout","items-v1"},
                    {"input_rechecked_before_seal",true},{"start_sha256",startSha}}},
    {"source_contract",{{"path",str(contractPath)},{"sha256",contractSha}}},
    {"official_inventory",{{"path",str(inventoryPath)},{"sha256",inventorySha}}},
    {"source_authority",authority},
    {"renderer",{{"path",str(executable)},{"sha256",sha(read_all(executable,1024ull*1024*1024))},
                  {"build_binding",buildBinding}}},
    {"algorithm",{{"filter","h3-quality"},{"quality_algorithm",QUALITY_ALGORITHM},
                   {"direct_scale","1X-crop-to-3X-crop"},{"resampler_count",1},
                   {"processing","linear-light premultiplied RGBA; output neutral RGB fixed white"},
                   {"source_geometry","canonical native canvas/crop/origin multiplied exactly by 3"}}},
     {"data_role",material?"road-material-3x":"logical-alpha-mask"},{"selected_identities",selected},
    {"native_spec_sha256",expectedSpecSha},
    {"frame_count",prepared.size()},{"source_png_bytes",inputBytesTotal},
    {"source_alpha_positive_pixels",sourcePositiveTotal},{"source_alpha_cells",sourceAlphaCells},
    {"independent_lod_verified",false},{"frames",frameReceipts},
     {"candidate_generation_performed",material},{"new_art_created",false},{"art_approved",false},
    {"runtime_accepted",false},{"game_deployed",false}};
   if(material){
    receipt["material_inputs"]=materialInputs.wire;
    receipt["algorithm"]={{"filter","none"},{"quality_algorithm",nullptr},{"direct_scale","existing-3X-integer-crop"},
     {"resampler_count",0},{"processing","exact source RGB copy for positive frozen alpha; alpha-zero RGB fixed zero; no model inference"},
     {"source_geometry","original crop3X; native canvas/origin are virtual only"}};
   }
  const auto receiptText=receipt.dump(2)+"\n";
  const std::vector<std::uint8_t> receiptBytes(receiptText.begin(),receiptText.end());

  outputGuard.write_new(staging/L"logical-alpha-completion.json",receiptBytes);
  const auto receiptSha=sha(receiptBytes);
  auto verifySeal=[&]{
   recheckInputs();
   std::map<std::wstring,std::string> expectedFiles;
   expectedFiles.emplace(native_review_path_key(staging/L"logical-alpha-start.json"),startSha);
   expectedFiles.emplace(native_review_path_key(staging/L"logical-alpha-completion.json"),receiptSha);
   std::set<std::wstring> expectedDirectories={native_review_path_key(staging/L"items")};
   for(const auto& frame:prepared){
    auto item=staging/L"items"/wide(frame.id);expectedDirectories.insert(native_review_path_key(item));
    expectedFiles.emplace(native_review_path_key(item/L"crop-3x.png"),frame.cropPngSha);
     if(!material)expectedFiles.emplace(native_review_path_key(item/L"canvas-3x.png"),frame.canvasPngSha);
   }
   std::size_t fileCount=0,directoryCount=0;
   for(const auto& entry:fs::recursive_directory_iterator(staging)){
    const DWORD attrs=GetFileAttributesW(entry.path().c_str());
    need(attrs!=INVALID_FILE_ATTRIBUTES&&!(attrs&FILE_ATTRIBUTE_REPARSE_POINT),"LOGICAL_ALPHA_SEAL_PATH_UNSAFE");
    auto key=native_review_path_key(entry.path());
    if(attrs&FILE_ATTRIBUTE_DIRECTORY){
     need(expectedDirectories.contains(key),"LOGICAL_ALPHA_UNOWNED_SEAL_DIRECTORY");++directoryCount;
    }else{
     auto found=expectedFiles.find(key);need(found!=expectedFiles.end(),"LOGICAL_ALPHA_UNOWNED_SEAL_FILE");
     need(sha(read_all(entry.path(),128ull*1024*1024))==found->second,"LOGICAL_ALPHA_SEAL_OUTPUT_CHANGED");++fileCount;
    }
   }
   need(fileCount==expectedFiles.size()&&directoryCount==expectedDirectories.size(),"LOGICAL_ALPHA_SEAL_TREE_INCOMPLETE");
  };
  outputGuard.publish_created_tree(staging,outdir,verifySeal);
  return {{"status","COMPLETED_REVIEW_REQUIRED"},{"operation_id",requestedOperationId},
          {"outdir",str(outdir)},{"receipt_path",str(outdir/L"logical-alpha-completion.json")},
          {"receipt_sha256",receiptSha},{"frame_count",prepared.size()},
          {"selected_identities",selected},{"source_png_bytes",inputBytesTotal},
          {"source_alpha_positive_pixels",sourcePositiveTotal},{"source_alpha_cells",sourceAlphaCells},
          {"independent_lod_verified",false},{"art_approved",false},{"runtime_accepted",false},
          {"game_deployed",false}};

 }catch(const std::exception& error){
  if(stagingOwned){
   outputGuard.keep();
   throw std::runtime_error(std::string("LOGICAL_ALPHA_BATCH_NOT_PUBLISHED; staging=")+str(staging)+"; "+error.what());
  }
  const auto cleanup=outputGuard.cleanup();
  if(!cleanup.empty())throw std::runtime_error(std::string("LOGICAL_ALPHA_FAILURE_WITH_PARTIAL_OUTPUT: ")+cleanup);
  throw;
 }
}

} // namespace h3tier
