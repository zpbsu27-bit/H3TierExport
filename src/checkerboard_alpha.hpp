#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <vector>

namespace h3tier::checkerboard_alpha {

struct Color { int r=0,g=0,b=0; };

struct Result {
 std::vector<std::uint8_t> rgba;
 int width=0,height=0,tile_size=0,phase=0;
 Color colors[2]{};
 double edge_coverage=0.0,edge_match=0.0;
 std::uint64_t transparent_pixels=0,softened_pixels=0,opaque_pixels=0;
};

inline int distance(const std::uint8_t* p,const Color& c){
 return std::max({std::abs(int(p[0])-c.r),std::abs(int(p[1])-c.g),std::abs(int(p[2])-c.b)});
}
inline int distance(const Color& a,const Color& b){
 return std::max({std::abs(a.r-b.r),std::abs(a.g-b.g),std::abs(a.b-b.b)});
}
inline int luma(const Color& c){return (c.r*54+c.g*183+c.b*19)/256;}
inline int chroma(const Color& c){return std::max({c.r,c.g,c.b})-std::min({c.r,c.g,c.b});}
inline Color midpoint(const Color& a,const Color& b){return{(a.r+b.r+1)/2,(a.g+b.g+1)/2,(a.b+b.b+1)/2};}
inline std::uint8_t clamp8(double v){return static_cast<std::uint8_t>(std::clamp(std::lround(v),0l,255l));}
inline std::size_t offset(int x,int y,int width){return (std::size_t(y)*std::size_t(width)+std::size_t(x))*4;}

struct BinStats {std::uint64_t count=0,r=0,g=0,b=0;};
struct Model {Color colors[2]{};int tile=0,phase=0,ox=0,oy=0;double coverage=0.0,match=0.0;bool phase_locked=true;};

inline Color background_at(const Model& model,int x,int y){
 const int parity=(((x+model.ox)/model.tile)+((y+model.oy)/model.tile)+model.phase)&1;
 return model.colors[parity];
}

inline int distance_to_segment(const std::uint8_t* p,const Color& a,const Color& b){
 const double vx=double(b.r-a.r),vy=double(b.g-a.g),vz=double(b.b-a.b);
 const double len2=vx*vx+vy*vy+vz*vz;
 if(len2<1.0)return distance(p,a);
 const double wx=double(int(p[0])-a.r),wy=double(int(p[1])-a.g),wz=double(int(p[2])-a.b);
 const double t=std::clamp((wx*vx+wy*vy+wz*vz)/len2,0.0,1.0);
 const Color proj{int(std::lround(a.r+t*vx)),int(std::lround(a.g+t*vy)),int(std::lround(a.b+t*vz))};
 return distance(p,proj);
}

inline std::array<int,3> color_bin(const std::uint8_t* p){
 auto q=[](int v){return std::min(255,((v+4)/8)*8);};
 return{q(p[0]),q(p[1]),q(p[2])};
}

inline std::vector<std::size_t> border_pixels(int width,int height){
 std::vector<std::size_t> out;
 out.reserve(std::size_t(width+height)*8);
 const int band=std::min(3,std::max(1,std::min(width,height)/128));
 for(int y=0;y<band;y++)for(int x=0;x<width;x++){
  out.push_back(offset(x,y,width));
  out.push_back(offset(x,height-1-y,width));
 }
 for(int x=0;x<band;x++)for(int y=band;y<height-band;y++){
  out.push_back(offset(x,y,width));
  out.push_back(offset(width-1-x,y,width));
 }
 std::sort(out.begin(),out.end());
 out.erase(std::unique(out.begin(),out.end()),out.end());
 return out;
}

inline Model detect(const std::vector<std::uint8_t>& rgba,int width,int height){
 if(width<32||height<32||std::uint64_t(width)*height>16ull*1024*1024)
  throw std::runtime_error("CHECKERBOARD_IMAGE_SIZE_UNSUPPORTED");
 if(rgba.size()!=std::size_t(width)*height*4)
  throw std::runtime_error("CHECKERBOARD_RGBA_SIZE_INVALID");

 const auto edges=border_pixels(width,height);
 std::map<std::array<int,3>,BinStats> histogram;
 std::uint64_t opaqueEdges=0,neutralEdges=0;
 for(const auto at:edges){
  const auto* p=rgba.data()+at;
  if(p[3]<220)continue;
  ++opaqueEdges;
  const Color q{color_bin(p)[0],color_bin(p)[1],color_bin(p)[2]};
  if(chroma(q)>34||luma(q)<90)continue;
  ++neutralEdges;
  auto& bin=histogram[color_bin(p)];++bin.count;bin.r+=p[0];bin.g+=p[1];bin.b+=p[2];
 }
 if(opaqueEdges<std::uint64_t(width+height)||neutralEdges<std::uint64_t(width+height)/2)
  throw std::runtime_error("CHECKERBOARD_EDGE_SAMPLES_INSUFFICIENT");

 std::vector<std::pair<std::array<int,3>,BinStats>> bins(histogram.begin(),histogram.end());
 std::sort(bins.begin(),bins.end(),[](const auto& a,const auto& b){return a.second.count>b.second.count;});
 if(bins.size()>24)bins.resize(24);
 const std::uint64_t minimumBin=std::max<std::uint64_t>(8,neutralEdges/40);
 std::uint64_t bestPair=0;Color palette[2]{};
 for(std::size_t i=0;i<bins.size();i++)for(std::size_t j=i+1;j<bins.size();j++){
  const auto& a=bins[i].second;const auto& b=bins[j].second;
  if(a.count<minimumBin||b.count<minimumBin)continue;
  Color ca{int(a.r/a.count),int(a.g/a.count),int(a.b/a.count)};
  Color cb{int(b.r/b.count),int(b.g/b.count),int(b.b/b.count)};
  const int separation=std::abs(luma(ca)-luma(cb));
  if(chroma(ca)>30||chroma(cb)>30||separation<9||separation>110)continue;
  std::uint64_t c0Count=0,c1Count=0,r0=0,g0=0,b0=0,r1=0,g1=0,b1=0;
  for(const auto& entry:bins){
   const auto& st=entry.second;
   const Color c{int(st.r/st.count),int(st.g/st.count),int(st.b/st.count)};
   const int d0=distance(c,ca),d1=distance(c,cb);
   if(d0<=16&&d0<=d1){c0Count+=st.count;r0+=st.r;g0+=st.g;b0+=st.b;}
   else if(d1<=16){c1Count+=st.count;r1+=st.r;g1+=st.g;b1+=st.b;}
  }
  if(c0Count<minimumBin||c1Count<minimumBin)continue;
  const auto count=c0Count+c1Count;
  if(count>bestPair){
   bestPair=count;
   palette[0]={int(r0/c0Count),int(g0/c0Count),int(b0/c0Count)};
   palette[1]={int(r1/c1Count),int(g1/c1Count),int(b1/c1Count)};
  }
 }
 if(!bestPair||double(bestPair)/double(neutralEdges)<0.68)
  throw std::runtime_error("CHECKERBOARD_NEUTRAL_PALETTE_UNCERTAIN");

 const int maxTile=std::min({256,width/4,height/4});
 if(maxTile<4)throw std::runtime_error("CHECKERBOARD_TILE_RANGE_INVALID");
 const Color c0=palette[0],c1=palette[1];
 bool found=false;Model best{};double bestQuality=-1.0;
 for(int tile=4;tile<=maxTile;tile++)for(int phase=0;phase<2;phase++){
  std::uint64_t valid=0,matched=0;
  for(const auto at:edges){
   const auto* p=rgba.data()+at;if(p[3]<220)continue;
   if(std::min(distance(p,c0),distance(p,c1))>54)continue;
   ++valid;
   const int x=int((at/4)%std::size_t(width)),y=int((at/4)/std::size_t(width));
   const int parity=((x/tile)+(y/tile)+phase)&1;
   if(distance(p,palette[parity])<=22)++matched;
  }
  const double coverage=opaqueEdges?double(valid)/double(opaqueEdges):0.0;
  const double match=valid?double(matched)/double(valid):0.0;
  const double quality=coverage*match;
  if(quality>bestQuality){
   bestQuality=quality;best={};best.colors[0]=palette[0];best.colors[1]=palette[1];
   best.tile=tile;best.phase=phase;best.coverage=coverage;best.match=match;best.phase_locked=true;found=true;
  }
 }

 // Fallback for Gemini / AI-generated checkerboards with fractional tile step,
 // non-zero border offset, or local neural phase inversions.
 if(!found||best.match<0.90){
  std::uint64_t valid=0,segMatched=0;
  const int segTol=std::clamp(distance(c0,c1)/5+7,10,18);
  for(const auto at:edges){
   const auto* p=rgba.data()+at;if(p[3]<220)continue;
   if(std::min(distance(p,c0),distance(p,c1))>54)continue;
   ++valid;
   if(distance_to_segment(p,c0,c1)<=segTol)++segMatched;
  }
  const int splitLuma=(luma(c0)+luma(c1))/2;
  std::vector<int> runs;
  auto scan_line=[&](bool horizontal,int fixedCoord){
   std::vector<int> flips;
   const int len=horizontal?width:height;
   int prevState=-1;
   for(int pos=0;pos<len;pos++){
    const int x=horizontal?pos:fixedCoord,y=horizontal?fixedCoord:pos;
    const auto* p=rgba.data()+offset(x,y,width);
    if(p[3]<220||distance_to_segment(p,c0,c1)>segTol+6)continue;
    const int pl=luma(Color{p[0],p[1],p[2]});
    if(std::abs(pl-splitLuma)<=3)continue;
    const int state=pl>=splitLuma?1:0;
    if(prevState!=-1&&state!=prevState)flips.push_back(pos);
    prevState=state;
   }
   for(std::size_t k=0;k+1<flips.size();k++){
    const int step=flips[k+1]-flips[k];
    if(step>=4&&step<=maxTile*2)runs.push_back(step);
   }
  };
  scan_line(true,std::min(1,height-1));
  scan_line(true,std::max(0,height-2));
  scan_line(false,std::min(1,width-1));
  scan_line(false,std::max(0,width-2));

  if(runs.size()>=6){
   std::sort(runs.begin(),runs.end());
   const int aiTile=std::clamp(runs[runs.size()/2],4,maxTile);
   const int tol1=std::max(3,aiTile/5),tol2=std::max(4,aiTile/4);
   std::size_t consistent=0;
   for(const int r:runs)if(std::abs(r-aiTile)<=tol1||std::abs(r-2*aiTile)<=tol2)++consistent;
   const double runConsistency=double(consistent)/double(runs.size());
   const double coverage=opaqueEdges?double(valid)/double(opaqueEdges):0.0;
   const double segMatch=valid?double(segMatched)/double(valid):0.0;
   const double aiMatch=segMatch*runConsistency;
   const double quality=coverage*aiMatch;
   if(coverage>=0.58&&runConsistency>=0.80&&aiMatch>=0.85&&quality>bestQuality){
    best={};best.colors[0]=c0;best.colors[1]=c1;best.tile=aiTile;best.phase=0;
    best.coverage=coverage;best.match=aiMatch;best.phase_locked=false;
    bestQuality=quality;found=true;
   }
  }
 }

 if(!found||best.coverage<0.58||best.match<(best.phase_locked?0.90:0.85)||bestQuality<0.55)
  throw std::runtime_error("CHECKERBOARD_PATTERN_NOT_CONFIRMED");
 return best;
}

inline Result remove(const std::vector<std::uint8_t>& source,int width,int height){
 const Model model=detect(source,width,height);
 const std::size_t count=std::size_t(width)*height;
 std::vector<std::uint8_t> candidate(count,0),background(count,0);
 const int separation=distance(model.colors[0],model.colors[1]);
 const int hardTolerance=std::clamp(separation/6+7,10,18);
 const int lineTolerance=std::clamp(separation/5+4,7,14);
 const Color middle=midpoint(model.colors[0],model.colors[1]);

 if(model.phase_locked){
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){
   const auto idx=std::size_t(y)*width+x,at=idx*4;
   const auto* p=source.data()+at;if(p[3]<8)continue;
   const auto expected=background_at(model,x,y);
   bool match=distance(p,expected)<=hardTolerance;
   const int rx=(x+model.ox)%model.tile,ry=(y+model.oy)%model.tile;
   const bool onGridLine=rx<=1||rx>=model.tile-1||ry<=1||ry>=model.tile-1;
   if(!match&&onGridLine&&distance(p,middle)<=lineTolerance)match=true;
   candidate[idx]=match?1:0;
  }
 }else{
  // Phase-unlocked / AI-drifting checkerboard: classify pixels on the neutral
  // segment [c0, c1], while protecting large monochromatic neutral foreground regions.
  std::vector<int> pxLuma(count,0);
  for(std::size_t i=0;i<count;i++){
   const auto* p=source.data()+i*4;
   pxLuma[i]=luma(Color{p[0],p[1],p[2]});
  }
  const int win=std::clamp(model.tile+5,9,std::min({64,width,height}));
  std::vector<int> hMin(count,0),hMax(count,0);
  for(int y=0;y<height;y++){
   const std::size_t row=std::size_t(y)*width;
   for(int x=0;x<width;x++){
    const int x0=std::clamp(x-win/2,0,std::max(0,width-win));
    const int x1=std::min(width,x0+win);
    int mn=255,mx=0;
    for(int xx=x0;xx<x1;xx++){const int v=pxLuma[row+xx];if(v<mn)mn=v;if(v>mx)mx=v;}
    hMin[row+x]=mn;hMax[row+x]=mx;
   }
  }
  const int lumaSep=std::abs(luma(model.colors[0])-luma(model.colors[1]));
  const int minRange=std::max(8,lumaSep/2);
  std::vector<std::uint8_t> solidCore(count,0),protectedSolid(count,0);
  for(int y=0;y<height;y++){
   const int y0=std::clamp(y-win/2,0,std::max(0,height-win));
   const int y1=std::min(height,y0+win);
   for(int x=0;x<width;x++){
    int mn=255,mx=0;
    for(int yy=y0;yy<y1;yy++){
     const std::size_t idx=std::size_t(yy)*width+x;
     if(hMin[idx]<mn)mn=hMin[idx];
     if(hMax[idx]>mx)mx=hMax[idx];
    }
    if(mx-mn<minRange)solidCore[std::size_t(y)*width+x]=1;
   }
  }
  const int pad=win/2;
  for(int y=0;y<height;y++)for(int x=0;x<width;x++)if(solidCore[std::size_t(y)*width+x]){
   for(int dy=-pad;dy<=pad;dy++)for(int dx=-pad;dx<=pad;dx++){
    const int xx=x+dx,yy=y+dy;
    if(xx>=0&&yy>=0&&xx<width&&yy<height)protectedSolid[std::size_t(yy)*width+xx]=1;
   }
  }
  const int minL=std::min(luma(model.colors[0]),luma(model.colors[1]))-hardTolerance;
  const int maxL=std::max(luma(model.colors[0]),luma(model.colors[1]))+hardTolerance;
  const int maxChroma=std::max({14,chroma(model.colors[0])+8,chroma(model.colors[1])+8});
  for(std::size_t i=0;i<count;i++){
   if(protectedSolid[i])continue;
   const auto* p=source.data()+i*4;if(p[3]<8)continue;
   const Color c{p[0],p[1],p[2]};
   if(chroma(c)>maxChroma||pxLuma[i]<minL||pxLuma[i]>maxL)continue;
   if(distance_to_segment(p,model.colors[0],model.colors[1])<=hardTolerance)candidate[i]=1;
  }
 }

 std::vector<std::uint32_t> queue;
 queue.reserve(std::min<std::size_t>(count/3,4*std::size_t(width+height)*model.tile));
 auto seed=[&](int x,int y){const auto idx=std::size_t(y)*width+x;if(candidate[idx]&&!background[idx]){background[idx]=1;queue.push_back(static_cast<std::uint32_t>(idx));}};
 for(int x=0;x<width;x++){seed(x,0);seed(x,height-1);}
 for(int y=1;y<height-1;y++){seed(0,y);seed(width-1,y);}
 for(std::size_t head=0;head<queue.size();head++){
  const auto idx=queue[head];const int x=int(idx%width),y=int(idx/width);
  const int nx[4]={x-1,x+1,x,x},ny[4]={y,y,y-1,y+1};
  for(int k=0;k<4;k++)if(nx[k]>=0&&ny[k]>=0&&nx[k]<width&&ny[k]<height)seed(nx[k],ny[k]);
 }

 // Also clear enclosed interior holes that contain alternating light & dark checkerboard tiles.
 std::vector<std::uint8_t> visitedHole(count,0);
 const std::size_t minHoleShade=std::max<std::size_t>(24,std::size_t(model.tile)*std::size_t(model.tile)/2);
 std::vector<std::uint32_t> comp;
 for(std::size_t i=0;i<count;i++)if(candidate[i]&&!background[i]&&!visitedHole[i]){
  comp.clear();visitedHole[i]=1;comp.push_back(static_cast<std::uint32_t>(i));
  std::size_t near0=0,near1=0;
  for(std::size_t head=0;head<comp.size();head++){
   const auto u=comp[head];const auto* p=source.data()+std::size_t(u)*4;
   if(distance(p,model.colors[0])<=hardTolerance)++near0;
   if(distance(p,model.colors[1])<=hardTolerance)++near1;
   const int x=int(u%width),y=int(u/width);
   const int nx[4]={x-1,x+1,x,x},ny[4]={y,y,y-1,y+1};
   for(int k=0;k<4;k++)if(nx[k]>=0&&ny[k]>=0&&nx[k]<width&&ny[k]<height){
    const auto ni=std::size_t(ny[k])*width+nx[k];
    if(candidate[ni]&&!background[ni]&&!visitedHole[ni]){visitedHole[ni]=1;comp.push_back(static_cast<std::uint32_t>(ni));}
   }
  }
  if(near0>=minHoleShade&&near1>=minHoleShade){
   for(const auto u:comp)background[u]=1;
  }
 }

 const int speckMaxChroma=std::max({14,chroma(model.colors[0])+12,chroma(model.colors[1])+12});
 for(int pass=0;pass<2;pass++){
  bool anySpeckle=false;
  for(int y=0;y<height;y++)for(int x=0;x<width;x++){
   const auto idx=std::size_t(y)*width+x;
   if(background[idx])continue;
   int bgNeighbors=0;
   for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++){
    if(dx==0&&dy==0)continue;
    const int xx=x+dx,yy=y+dy;
    if(xx>=0&&yy>=0&&xx<width&&yy<height&&background[std::size_t(yy)*width+xx])++bgNeighbors;
   }
   if(bgNeighbors>=7){
    const auto* p=source.data()+idx*4;
    const Color c{p[0],p[1],p[2]};
    if(chroma(c)<=speckMaxChroma&&distance_to_segment(p,model.colors[0],model.colors[1])<=hardTolerance+12){
     background[idx]=1;anySpeckle=true;
    }
   }
  }
  if(!anySpeckle)break;
 }

 Result result;result.width=width;result.height=height;result.tile_size=model.tile;result.phase=model.phase;
 result.colors[0]=model.colors[0];result.colors[1]=model.colors[1];
 result.edge_coverage=model.coverage;result.edge_match=model.match;result.rgba=source;

 auto erode_mask=[&](const std::vector<std::uint8_t>& mask,int radius){
  std::vector<std::uint8_t> hErode(count,0),outMask(count,0);
  for(int y=0;y<height;y++){
   const std::size_t row=std::size_t(y)*width;
   for(int x=0;x<width;x++){
    std::uint8_t ok=1;
    for(int dx=-radius;dx<=radius;dx++){
     const int xx=std::clamp(x+dx,0,width-1);
     if(!mask[row+xx]){ok=0;break;}
    }
    hErode[row+x]=ok;
   }
  }
  for(int y=0;y<height;y++){
   for(int x=0;x<width;x++){
    std::uint8_t ok=1;
    for(int dy=-radius;dy<=radius;dy++){
     const int yy=std::clamp(y+dy,0,height-1);
     if(!hErode[std::size_t(yy)*width+x]){ok=0;break;}
    }
    outMask[std::size_t(y)*width+x]=ok;
   }
  }
  return outMask;
 };

 auto dilate_mask=[&](const std::vector<std::uint8_t>& mask,int radius){
  std::vector<std::uint8_t> hDil(count,0),outMask(count,0);
  for(int y=0;y<height;y++){
   const std::size_t row=std::size_t(y)*width;
   for(int x=0;x<width;x++){
    std::uint8_t ok=0;
    for(int dx=-radius;dx<=radius;dx++){
     const int xx=std::clamp(x+dx,0,width-1);
     if(mask[row+xx]){ok=1;break;}
    }
    hDil[row+x]=ok;
   }
  }
  for(int y=0;y<height;y++){
   for(int x=0;x<width;x++){
    std::uint8_t ok=0;
    for(int dy=-radius;dy<=radius;dy++){
     const int yy=std::clamp(y+dy,0,height-1);
     if(hDil[std::size_t(yy)*width+x]){ok=1;break;}
    }
    outMask[std::size_t(y)*width+x]=ok;
   }
  }
  return outMask;
 };

 std::vector<std::uint8_t> notBg(count,0);
 std::vector<int> pxChroma(count,0),pxBmin(count,0),pxLuma(count,0);
 for(std::size_t i=0;i<count;i++){
  const auto at=i*4;
  if(!background[i]&&source[at+3]>0)notBg[i]=1;
  const Color c{source[at],source[at+1],source[at+2]};
  pxChroma[i]=chroma(c);
  pxBmin[i]=std::min({int(source[at]),int(source[at+1]),int(source[at+2])});
  pxLuma[i]=luma(c);
 }

 auto fgCore=erode_mask(notBg,4);
 bool anyCore=false;
 for(auto v:fgCore)if(v){anyCore=true;break;}
 if(!anyCore)fgCore=erode_mask(notBg,2);
 const auto fgInner2px=erode_mask(notBg,2);

 std::vector<int> hChMax(count,0),chMax9(count,0);
 for(int y=0;y<height;y++){
  const std::size_t row=std::size_t(y)*width;
  for(int x=0;x<width;x++){
   int mx=0;
   for(int dx=-4;dx<=4;dx++){
    const int xx=std::clamp(x+dx,0,width-1);
    const auto idx=row+xx;
    const int v=notBg[idx]?pxChroma[idx]:0;
    if(v>mx)mx=v;
   }
   hChMax[row+x]=mx;
  }
 }
 for(int y=0;y<height;y++){
  for(int x=0;x<width;x++){
   int mx=0;
   for(int dy=-4;dy<=4;dy++){
    const int yy=std::clamp(y+dy,0,height-1);
    const int v=hChMax[std::size_t(yy)*width+x];
    if(v>mx)mx=v;
   }
   chMax9[std::size_t(y)*width+x]=mx;
  }
 }

 std::vector<double> aRaw(count,0.0);
 std::vector<std::uint8_t> fgPure(count,0);
 for(std::size_t i=0;i<count;i++){
  if(!notBg[i]){aRaw[i]=0.0;continue;}
  const double chMax=double(chMax9[i]);
  const double bmin=double(pxBmin[i]);
  const double ch=double(pxChroma[i]);
  const double bminExcess=std::max(0.0,bmin-95.0);
  const double chEff=(!fgInner2px[i]&&ch<chMax*0.92)?(ch-0.50*bminExcess):ch;
  const double chRatio=chEff/std::max(chMax,24.0);
  const double aCh=std::clamp((chRatio-0.60)/(0.80-0.60),0.0,1.0);
  const double aDark=std::clamp((115.0-bmin)/(115.0-68.0),0.0,1.0);
  const double aColor=std::max(aCh,aDark);
  const double dSeg=distance_to_segment(source.data()+i*4,model.colors[0],model.colors[1]);
  const double aNeutral=std::clamp((dSeg-hardTolerance*0.5)/std::max(1.0,hardTolerance*0.8),0.0,1.0);
  const double wColored=std::clamp((chMax-16.0)/8.0,0.0,1.0);
  const double ar=wColored*aColor+(1.0-wColored)*std::max(aColor,aNeutral);
  aRaw[i]=ar;
  if(fgCore[i]||ar>=0.95)fgPure[i]=1;
 }

 fgPure=erode_mask(dilate_mask(fgPure,1),1);
 const auto fgDeep=erode_mask(fgPure,3);
 std::vector<std::uint8_t> fgRim(count,0),bgDefinite(count,0);
 for(std::size_t i=0;i<count;i++){
  if(fgPure[i]&&!fgDeep[i])fgRim[i]=1;
  if(fgPure[i])aRaw[i]=1.0;
  if(!notBg[i])aRaw[i]=0.0;
  if(!notBg[i]||(!fgCore[i]&&aRaw[i]<0.05))bgDefinite[i]=1;
 }
 const auto bgClean=erode_mask(bgDefinite,3);

 const double gKernel[5]={0.06136,0.24477,0.38774,0.24477,0.06136};
 std::vector<double> tmpS(count,0.0),S0(count,0.0);
 for(int y=0;y<height;y++){
  const std::size_t row=std::size_t(y)*width;
  for(int x=0;x<width;x++){
   double acc=0.0;
   for(int k=-2;k<=2;k++)acc+=gKernel[k+2]*aRaw[row+std::clamp(x+k,0,width-1)];
   tmpS[row+x]=acc;
  }
 }
 for(int y=0;y<height;y++){
  for(int x=0;x<width;x++){
   double acc=0.0;
   for(int k=-2;k<=2;k++)acc+=gKernel[k+2]*tmpS[std::size_t(std::clamp(y+k,0,height-1))*width+x];
   S0[std::size_t(y)*width+x]=acc;
  }
 }

 std::vector<double> gx(count,0.0),gy(count,0.0),gradMag(count,0.0),dCur(count,0.0);
 for(int y=0;y<height;y++){
  const std::size_t row=std::size_t(y)*width;
  const int y0=std::max(0,y-1),y1=std::min(height-1,y+1);
  const double dyDen=(y1>y0)?double(y1-y0):1.0;
  for(int x=0;x<width;x++){
   const int x0=std::max(0,x-1),x1=std::min(width-1,x+1);
   const double dxDen=(x1>x0)?double(x1-x0):1.0;
   const double dx=(S0[row+x1]-S0[row+x0])/dxDen;
   const double dy=(S0[std::size_t(y1)*width+x]-S0[std::size_t(y0)*width+x])/dyDen;
   const double gm=std::hypot(dx,dy);
   const auto idx=row+x;
   gx[idx]=dx;gy[idx]=dy;gradMag[idx]=gm;
   dCur[idx]=std::clamp((S0[idx]-0.50)/std::max(gm,0.18),-4.5,4.5);
  }
 }

 auto box_sum=[&](const std::vector<double>& arr,int radius){
  std::vector<double> hSum(count,0.0),outSum(count,0.0);
  for(int y=0;y<height;y++){
   const std::size_t row=std::size_t(y)*width;
   for(int x=0;x<width;x++){
    double s=0.0;
    for(int dx=-radius;dx<=radius;dx++)s+=arr[row+std::clamp(x+dx,0,width-1)];
    hSum[row+x]=s;
   }
  }
  for(int y=0;y<height;y++){
   for(int x=0;x<width;x++){
    double s=0.0;
    for(int dy=-radius;dy<=radius;dy++)s+=hSum[std::size_t(std::clamp(y+dy,0,height-1))*width+x];
    outSum[std::size_t(y)*width+x]=s;
   }
  }
  return outSum;
 };

 const auto gxS=box_sum(gx,4);
 const auto gyS=box_sum(gy,4);
 std::vector<double> nx(count,0.0),ny(count,0.0),tx(count,0.0),ty(count,0.0);
 std::vector<std::size_t> activeIdx;
 activeIdx.reserve(count/16);
 for(std::size_t i=0;i<count;i++){
  const double gn=std::hypot(gxS[i],gyS[i]);
  const double invG=1.0/std::max(gn,1e-5);
  nx[i]=gxS[i]*invG;ny[i]=gyS[i]*invG;
  tx[i]=-ny[i];ty[i]=nx[i];
  if(!bgClean[i]&&!fgDeep[i]&&gradMag[i]>0.015)activeIdx.push_back(i);
 }

 for(int pass=0;pass<3;pass++){
  std::vector<double> dNext=dCur;
  for(const auto idx:activeIdx){
   const int x=int(idx%width),y=int(idx/width);
   const double txi=tx[idx],tyi=ty[idx],nxi=nx[idx],nyi=ny[idx];
   double dAcc=dCur[idx],wTot=1.0;
   for(int step=1;step<=22;step++){
    const double wSpatial=std::exp(-0.5*(double(step)/12.0)*(double(step)/12.0));
    for(int s=-1;s<=1;s+=2){
     const int sx=std::clamp(int(std::lround(double(x)+double(s*step)*txi)),0,width-1);
     const int sy=std::clamp(int(std::lround(double(y)+double(s*step)*tyi)),0,height-1);
     const auto sIdx=std::size_t(sy)*width+sx;
     const double dotN=nxi*nx[sIdx]+nyi*ny[sIdx];
     const double wCoh=std::clamp((dotN-0.82)/0.15,0.0,1.0);
     const double wEdge=(std::abs(dCur[sIdx])<4.2)?1.0:0.0;
     const double wt=wSpatial*wCoh*wEdge;
     dAcc+=wt*dCur[sIdx];
     wTot+=wt;
    }
   }
   dNext[idx]=dAcc/wTot;
  }
  dCur.swap(dNext);
 }

 std::vector<double> alphaCrisp(count,0.0);
 for(std::size_t i=0;i<count;i++){
  double ac=std::clamp(0.5+dCur[i]/1.25,0.0,1.0);
  if(bgClean[i]||!notBg[i])ac=0.0;
  if(fgDeep[i])ac=1.0;
  if(ac<0.02)ac=0.0;
  if(ac>0.98)ac=1.0;
  alphaCrisp[i]=ac;
 }

 std::vector<int> hLumMin(count,255),lumMin7(count,255);
 for(int y=0;y<height;y++){
  const std::size_t row=std::size_t(y)*width;
  for(int x=0;x<width;x++){
   int mn=255;
   for(int dx=-3;dx<=3;dx++){
    const auto idx=row+std::clamp(x+dx,0,width-1);
    const int v=notBg[idx]?pxLuma[idx]:255;
    if(v<mn)mn=v;
   }
   hLumMin[row+x]=mn;
  }
 }
 for(int y=0;y<height;y++){
  for(int x=0;x<width;x++){
   int mn=255;
   for(int dy=-3;dy<=3;dy++){
    const int v=hLumMin[std::size_t(std::clamp(y+dy,0,height-1))*width+x];
    if(v<mn)mn=v;
   }
   lumMin7[std::size_t(y)*width+x]=mn;
  }
 }

 std::vector<double> fgF(count,0.0),fgWR(count,0.0),fgWG(count,0.0),fgWB(count,0.0);
 std::vector<double> fgLocR(count,0.0),fgLocG(count,0.0),fgLocB(count,0.0);
 for(std::size_t i=0;i<count;i++){
  const auto at=i*4;
  fgLocR[i]=double(source[at]);fgLocG[i]=double(source[at+1]);fgLocB[i]=double(source[at+2]);
  if(fgRim[i]){
   const double wDark=std::exp(-std::max(0.0,double(pxLuma[i]-lumMin7[i]))/14.0);
   fgF[i]=wDark;
   fgWR[i]=fgLocR[i]*wDark;fgWG[i]=fgLocG[i]*wDark;fgWB[i]=fgLocB[i]*wDark;
  }
 }
 const int radii[4]={9,6,4,2};
 for(int rIdx=0;rIdx<4;rIdx++){
  const int rad=radii[rIdx];
  const auto fCnt=box_sum(fgF,rad);
  const auto fSR=box_sum(fgWR,rad);
  const auto fSG=box_sum(fgWG,rad);
  const auto fSB=box_sum(fgWB,rad);
  for(std::size_t i=0;i<count;i++){
   if(!fgPure[i]&&fCnt[i]>1e-3){
    fgLocR[i]=fSR[i]/fCnt[i];
    fgLocG[i]=fSG[i]/fCnt[i];
    fgLocB[i]=fSB[i]/fCnt[i];
   }
  }
 }

 std::uint64_t removed=0,softened=0;
 for(std::size_t i=0;i<count;i++){
  const auto at=i*4;
  if(source[at+3]==0){result.rgba[at]=result.rgba[at+1]=result.rgba[at+2]=0;continue;}
  const std::uint8_t newA=clamp8(double(source[at+3])*alphaCrisp[i]);
  if(newA==0){
   result.rgba[at]=result.rgba[at+1]=result.rgba[at+2]=0;result.rgba[at+3]=0;++removed;continue;
  }
  if(!fgDeep[i]){
   const int x=int(i%width),y=int(i/width);
   const double txi=tx[i],tyi=ty[i],nxi=nx[i],nyi=ny[i];
   double rAcc=0.0,gAcc=0.0,bAcc=0.0,wTot=0.0;
   for(int step=-8;step<=8;step++){
    const double wSpatial=std::exp(-0.5*(double(step)/4.5)*(double(step)/4.5));
    const int sx=std::clamp(int(std::lround(double(x)+double(step)*txi)),0,width-1);
    const int sy=std::clamp(int(std::lround(double(y)+double(step)*tyi)),0,height-1);
    const auto sIdx=std::size_t(sy)*width+sx;
    const double dotN=nxi*nx[sIdx]+nyi*ny[sIdx];
    const double wCoh=std::clamp((dotN-0.82)/0.15,0.0,1.0);
    const double wt=wSpatial*wCoh+((step==0)?1e-4:0.0);
    rAcc+=wt*fgLocR[sIdx];gAcc+=wt*fgLocG[sIdx];bAcc+=wt*fgLocB[sIdx];
    wTot+=wt;
   }
   const double sR=rAcc/wTot,sG=gAcc/wTot,sB=bAcc/wTot;
   const double tR=std::min(double(source[at]),sR);
   const double tG=std::min(double(source[at+1]),sG);
   const double tB=std::min(double(source[at+2]),sB);
   const double wOwn=std::clamp((aRaw[i]-0.65)/(0.95-0.65),0.0,1.0);
   result.rgba[at]=clamp8((1.0-wOwn)*tR+wOwn*double(source[at]));
   result.rgba[at+1]=clamp8((1.0-wOwn)*tG+wOwn*double(source[at+1]));
   result.rgba[at+2]=clamp8((1.0-wOwn)*tB+wOwn*double(source[at+2]));
  }
  result.rgba[at+3]=newA;
  if(newA<source[at+3])++softened;
 }
 std::uint64_t opaqueAfter=0;
 for(std::size_t i=3;i<result.rgba.size();i+=4)opaqueAfter+=result.rgba[i]>0;
 result.transparent_pixels=count-opaqueAfter;result.softened_pixels=softened;result.opaque_pixels=opaqueAfter;
 if(removed<std::uint64_t(count)/20||removed>std::uint64_t(count)*95/100||opaqueAfter<std::uint64_t(count)/100)
  throw std::runtime_error("CHECKERBOARD_REMOVAL_COVERAGE_UNCERTAIN");
 return result;
}

} // namespace h3tier::checkerboard_alpha
