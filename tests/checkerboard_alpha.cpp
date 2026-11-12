#include "../src/checkerboard_alpha.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace h3tier::checkerboard_alpha;

namespace {
void check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
std::size_t at(int x,int y,int width){return(std::size_t(y)*width+x)*4;}
Color board(const Model& model,int x,int y){return model.colors[((x/model.tile)+(y/model.tile)+model.phase)&1];}
void blend(std::vector<std::uint8_t>& rgba,int x,int y,int width,int height,const Model& model,Color foreground,double coverage){
 if(x<0||y<0||x>=width||y>=height||coverage<=0.0)return;
 coverage=std::clamp(coverage,0.0,1.0);const auto bg=board(model,x,y);const auto p=at(x,y,width);
 rgba[p]=static_cast<std::uint8_t>(std::lround(coverage*foreground.r+(1.0-coverage)*bg.r));
 rgba[p+1]=static_cast<std::uint8_t>(std::lround(coverage*foreground.g+(1.0-coverage)*bg.g));
 rgba[p+2]=static_cast<std::uint8_t>(std::lround(coverage*foreground.b+(1.0-coverage)*bg.b));
 rgba[p+3]=255; // checker pixels are baked into the source
}
void ellipse(std::vector<std::uint8_t>& rgba,int width,int height,const Model& model,int cx,int cy,int rx,int ry,Color color){
 for(int y=std::max(0,cy-ry-3);y<std::min(height,cy+ry+4);y++)for(int x=std::max(0,cx-rx-3);x<std::min(width,cx+rx+4);x++){
  const double dx=double(x-cx)/rx,dy=double(y-cy)/ry;
  const double coverage=std::clamp((1.035-std::sqrt(dx*dx+dy*dy))*14.0,0.0,1.0);
  blend(rgba,x,y,width,height,model,color,coverage);
 }
}
}

int main(){
 try{
  constexpr int width=256,height=320,tile=16;
  const Color light{238,238,238},dark{206,206,206};
  std::vector<std::uint8_t> source(std::size_t(width)*height*4);
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){
   const auto c=((x/tile+y/tile)&1)?dark:light;const auto p=at(x,y,width);
   source[p]=(std::uint8_t)c.r;source[p+1]=(std::uint8_t)c.g;source[p+2]=(std::uint8_t)c.b;source[p+3]=255;
  }
  const auto model=detect(source,width,height);
  check(model.tile==tile,"checkerboard tile period detected");
  check(model.match>=0.98,"checkerboard phase agrees with the edge samples");

  // A colorful troll-like figure and a large gray stone component test both
  // edge matting and protection of a neutral subject feature.
  const Color skin{101,111,43},skinLight{164,151,78},red{139,28,24};
  ellipse(source,width,height,model,119,170,37,83,skin);
  ellipse(source,width,height,model,112,72,27,31,skinLight);
  for(int x=139;x<174;x++)for(int y=177;y<187;y++)blend(source,x,y,width,height,model,red,1.0);
  const int stoneX=207,stoneY=167;
  const Color stone=board(model,stoneX,stoneY);
  ellipse(source,width,height,model,stoneX,stoneY,37,28,stone);
  const auto original=source;
  const auto result=remove(source,width,height);
  check(source==original,"source remains immutable");
  check(result.width==width&&result.height==height,"canvas dimensions are preserved");
  check(result.tile_size==tile&&result.edge_match>=0.98,"pattern model is recorded accurately");
  check(result.transparent_pixels>std::uint64_t(width)*height/2,"exterior checkerboard becomes transparent");
  check(result.rgba[at(0,0,width)+3]==0&&result.rgba[at(width-1,height-1,width)+3]==0,"all outer corners are transparent");
  const auto body=at(119,170,width);
  check(result.rgba[body+3]==255&&result.rgba[body]==skin.r,"opaque green subject core is unchanged");
  const auto rock=at(stoneX,stoneY,width);
  check(result.rgba[rock+3]==255&&result.rgba[rock]==stone.r,"neutral stone subject core is retained");
  check(result.softened_pixels>0,"semi-transparent silhouette edge is recovered");
  check(result.transparent_pixels+result.opaque_pixels==std::uint64_t(width)*height,"alpha coverage counts are consistent");

  std::vector<std::uint8_t> solid(std::size_t(width)*height*4);
  for(std::size_t i=0;i<solid.size();i+=4){solid[i]=235;solid[i+1]=235;solid[i+2]=235;solid[i+3]=255;}
  bool rejected=false;try{(void)remove(solid,width,height);}catch(const std::exception&){rejected=true;}
  check(rejected,"solid gray background is rejected without writing a candidate");

  // Gemini-style phase-drifting fractional checkerboard with antialiased transitions,
  // an enclosed interior checkerboard hole, and a solid neutral stone subject.
  std::vector<std::uint8_t> aiSource(std::size_t(width)*height*4);
  const Color aiLight{246,246,246},aiDark{203,203,203},aiMid{225,225,225};
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){
   const int tx=static_cast<int>(std::floor((x+3)/17.067));
   const int ty=static_cast<int>(std::floor((y+2)/17.067))+(y>=170?1:0);
   const bool seam=(std::abs(std::fmod(x+3.0,17.067))<0.9)||(std::abs(std::fmod(y+2.0,17.067))<0.9);
   const Color c=seam?aiMid:(((tx+ty)&1)?aiDark:aiLight);
   const auto p=at(x,y,width);
   aiSource[p]=(std::uint8_t)c.r;aiSource[p+1]=(std::uint8_t)c.g;aiSource[p+2]=(std::uint8_t)c.b;aiSource[p+3]=255;
  }
  // Ring with an enclosed interior checkerboard hole around (96, 130)
  for(int y=66;y<=194;y++)for(int x=32;x<=160;x++){
   const double d=std::hypot(double(x-96),double(y-130));
   if(d>=26.0&&d<=56.0){
    const auto p=at(x,y,width);
    aiSource[p]=(std::uint8_t)skin.r;aiSource[p+1]=(std::uint8_t)skin.g;aiSource[p+2]=(std::uint8_t)skin.b;aiSource[p+3]=255;
   }
  }
  // Neutral solid stone on the right side
  for(int y=135;y<=195;y++)for(int x=175;x<=240;x++){
   const double dx=double(x-207)/30.0,dy=double(y-165)/26.0;
   if(dx*dx+dy*dy<=1.0){
    const auto p=at(x,y,width);
    aiSource[p]=203;aiSource[p+1]=203;aiSource[p+2]=203;aiSource[p+3]=255;
   }
  }
  const auto aiResult=remove(aiSource,width,height);
  check(aiResult.rgba[at(0,0,width)+3]==0,"AI phase-drifting exterior is cleared");
  check(aiResult.rgba[at(96,130,width)+3]==0,"enclosed interior checkerboard hole is cleared");
  check(aiResult.rgba[at(96,86,width)+3]==255,"ring foreground stays opaque");
  check(aiResult.rgba[at(207,165,width)+3]==255,"solid gray stone core is protected under phase-drifting removal");

  std::cout<<"checkerboard_alpha PASS tile="<<result.tile_size
           <<" transparent="<<result.transparent_pixels
           <<" softened="<<result.softened_pixels
           <<" edge_match="<<result.edge_match<<"\n";
  return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}
}
