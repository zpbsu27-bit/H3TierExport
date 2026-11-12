// Numerical edge contracts and a real C++ PNG comparison runner.
#include "wic.hpp"
#include <iostream>
using namespace h3tier;
int wmain(int argc,wchar_t**argv){try{
 ComInit com;
 if(argc==6){
  fs::path source=argv[1],output=argv[2];int w=std::stoi(argv[3]),h=std::stoi(argv[4]);std::string filter=utf8(argv[5]);Wic decoder;auto raw=read_all(source);std::string note;auto im=decoder.decode(raw,true,note);auto t=Clock::now();Image out;
  if(filter=="h3-quality")out=quality_resize_cpu(im,w,h);
  else if(filter=="ewa-sharp")out=ewa_sharp_cpu(im,w,h);
  else if(filter=="legacy-up")out=expand_uniform_cpu(im,w,h);
  else{auto f=parse_filter(filter);auto tmp=axis_precise(im,make_axis(im.w,w,f,true),true);out=axis_precise(tmp,make_axis(im.h,h,f,true),false);}
  double elapsed=ms(t);auto pixels=quantize(out,true),png=decoder.encode(pixels,w,h,true);same_or_new(output,png);need(decoder.decode_rgba(png,w,h)==pixels,"roundtrip");
  std::cout<<Json{{"source",str(source)},{"source_sha256",sha(raw)},{"filter",filter},{"pixels",{w,h}},{"output",str(output)},{"png_sha256",sha(png)},{"resample_ms",elapsed},{"source_unchanged",sha(read_all(source))==sha(raw)}}.dump()<<std::endl;return 0;
 }
 need(argc==1,"comparison arguments required: source output width height filter");int checks=0;auto check=[&](bool b,const char*name){need(b,name);checks++;};
 Image flat(12,9);for(auto& p:flat.p)p={.2f,.1f,.3f,.5f};
 check(quality_resize_cpu(flat,12,9).p==flat.p,"identity is bit exact");
 for(auto size:{std::array<int,2>{16,12},std::array<int,2>{4,3}}){auto out=quality_resize_cpu(flat,size[0],size[1]);double error=0;for(const auto& p:out.p)error=std::max(error,double(std::abs(p.r-.2f)+std::abs(p.a-.5f)));check(error<2e-6,"constant premultiplied color preserved");}
 Image edge(24,18);for(int y=0;y<edge.h;y++)for(int x=0;x<edge.w;x++){float a=(x+y<20)?0.f:((x+y==20)?.35f:.8f);edge.p[std::size_t(y)*edge.w+x]={a*.15f,a*.4f,a*.7f,a};}
 auto out=expand_quality_cpu(edge,32,24);double chroma=0;bool fringe=false;for(const auto& p:out.p){if(p.a>1e-5)chroma=std::max(chroma,double(std::abs(p.r/p.a-.15f)+std::abs(p.g/p.a-.4f)+std::abs(p.b/p.a-.7f)));fringe|=p.a<0||p.a>.800002f||p.r<0||p.r>p.a;}
 check(!fringe,"alpha overshoot and premultiplied physical bounds");check(chroma<3e-6,"colored diagonal edge keeps hue at translucent pixels");
 auto pixels=quantize(out,true);bool hidden=false;for(size_t i=0;i<pixels.size();i+=4)if(pixels[i+3]==0)hidden|=pixels[i]||pixels[i+1]||pixels[i+2];check(!hidden,"transparent pixels have zero RGB");
 Image white(32,24);for(int y=0;y<white.h;y++)for(int x=0;x<white.w;x++){float v=x<16?.1f:.8f;white.p[std::size_t(y)*white.w+x]={v,v,v,1};}
 auto step=expand_quality_cpu(white,48,36);double overshoot=0;for(auto p:step.p)overshoot=std::max(overshoot,std::max(.1-double(p.r),double(p.r)-.8));check(overshoot<.015,"painted contrast ringing stays bounded");
 auto average=quality_resize_cpu(white,1,1);check(average.p[0].r>.43f&&average.p[0].r<.47f,"strong minification preserves energy");
 auto fail=[&](auto fn){bool rejected=false;try{fn();}catch(...){rejected=true;}check(rejected,"invalid expansion rejected");};fail([&]{expand_quality_cpu(edge,40,24);});fail([&]{expand_quality_cpu(edge,72,54);});fail([&]{resize_cpu(edge,32,24,Filter::H3Quality);});
 check(std::string(QUALITY_ALGORITHM)=="linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4","exact-support antiring has its own algorithm identity");
 auto fill=[](Image& image,Pixel p){std::fill(image.p.begin(),image.p.end(),p);};
 const Pixel grayExpected{.125f,.125f,.125f,1.f};
 for(bool vertical:{false,true}){
  // At 1200 -> 450, slot 93 covers [248,250+2/3): only 248..250.
  // Changing cell 247 must be bit-exactly irrelevant on either axis.
  Image source(vertical?1:1200,vertical?1200:1);fill(source,grayExpected);
  auto colorPoison=source;colorPoison.p[247]={.875f,.875f,.875f,1.f};
  Image clean(vertical?1:450,vertical?450:1);fill(clean,{.5f,.5f,.5f,1.f});auto changed=clean;
  footprint_antiring(source,clean);footprint_antiring(colorPoison,changed);
  check(clean.p[93]==grayExpected&&changed.p[93]==clean.p[93],vertical?"y zero-area color cannot widen luma extrema":"x zero-area color cannot widen luma extrema");
  fill(source,{.0625f,.0625f,.0625f,.5f});auto alphaPoison=source;alphaPoison.p[247]={};
  fill(clean,{.015625f,.015625f,.015625f,.125f});changed=clean;
  footprint_antiring(source,clean);footprint_antiring(alphaPoison,changed);
  check(clean.p[93].a==.5f&&changed.p[93]==clean.p[93],vertical?"y zero-area alpha cannot widen coverage extrema":"x zero-area alpha cannot widen coverage extrema");
  // Known area weights are 3/8, 3/8, 1/4, giving green 15/32.
  fill(source,{0,0,0,1});source.p[248]={0,.25f,0,1};source.p[249]={0,.5f,0,1};source.p[250]={0,.75f,0,1};
  fill(clean,{-1,0,0,1});footprint_antiring(source,clean);
  check(clean.p[93]==Pixel{0,.46875f,0,1},vertical?"y exact positive-area average matches hand weights":"x exact positive-area average matches hand weights");
  Image oldPadding(vertical?1:1200,vertical?1200:1),newPadding(vertical?1:1200,vertical?1200:1);
  for(int i=0;i<1070;i++){Pixel p=i==413?Pixel{.875f,.875f,.875f,1}:grayExpected;oldPadding.p[i+130]=p;newPadding.p[i+34]=p;}
  Image oldOutput(vertical?1:450,vertical?450:1);fill(oldOutput,{.5f,.5f,.5f,1});auto newOutput=oldOutput;
  footprint_antiring(oldPadding,oldOutput);footprint_antiring(newPadding,newOutput);
  // Both exact supports are raw-parent 414..416, away from canvas borders.
  check(oldOutput.p[204]==grayExpected&&newOutput.p[168]==oldOutput.p[204],vertical?"y integral padding translation keeps interior extrema":"x integral padding translation keeps interior extrema");
 }
 Image enlarge(2,1);enlarge.p={{0,.25f,0,1},{0,.75f,0,1}};Image enlarged(4,1);fill(enlarged,{-1,0,0,1});
 footprint_antiring(enlarge,enlarged);
 const std::vector<Pixel> enlargedExpected{{0,.25f,0,1},{0,.375f,0,1},{0,.625f,0,1},{0,.75f,0,1}};
 check(enlarged.p==enlargedExpected,"upscale half-width .5 keeps hand weights and clipped edge normalization");
 for(bool transpose:{false,true}){
  Image mixed(transpose?2:3,transpose?3:2);
  for(int y=0;y<2;y++)for(int x=0;x<3;x++)mixed.p[transpose?std::size_t(x)*mixed.w+y:std::size_t(y)*mixed.w+x]={0,(y?.625f:.125f)+.125f*x,0,1};
  Image mixedOutput(transpose?4:1,transpose?1:4);fill(mixedOutput,{-1,0,0,1});footprint_antiring(mixed,mixedOutput);
  check(mixedOutput.p==enlargedExpected,transpose?"mixed enlarge-x shrink-y matches hand area average":"mixed shrink-x enlarge-y matches hand area average");
 }
 Image weak(3,1),weakOutput(1,1);const float alpha1=1.f/255.f;const Pixel weakPixel{alpha1*.25f,alpha1*.5f,alpha1*.75f,alpha1};
 weak.p[1]=weakPixel;weakOutput.p[0]=weakPixel;footprint_antiring(weak,weakOutput);
 check(weakOutput.p[0]==weakPixel&&quantize(weakOutput,true)[3]==1,"positive-area alpha1 remains in coverage and straight-color support");
 Image tex(24,24);
 for(int y=0;y<24;y++)for(int x=0;x<24;x++){
  int gx=x/3,gy=y/3;bool darkCell=((gx+gy)%2)==0;
  float v=darkCell?((x%3==1&&y%3==1)?.85f:.04f):((x%3==1&&y%3==1)?.92f:.18f);
  tex.p[std::size_t(y)*24+x]={v*.9f,v*.8f,v*.6f,1.f};
 }
 auto linOnly=[&](){
  auto ax=make_axis(24,8,Filter::Lanczos3,true),ay=make_axis(24,8,Filter::Lanczos3,true);
  auto tmp=axis_precise(tex,ax,true);auto o=axis_precise(tmp,ay,false);footprint_antiring(tex,o);return o;
 }();
 auto dehazed=quality_resize_cpu(tex,8,8);
 double linDark=0,dehazedDark=0,linContrast=0,dehazedContrast=0;
 for(int y=2;y<6;y++)for(int x=2;x<6;x++){
  const auto& pL=linOnly.p[std::size_t(y)*8+x];const auto& pD=dehazed.p[std::size_t(y)*8+x];
  double lL=.2126*pL.r+.7152*pL.g+.0722*pL.b,lD=.2126*pD.r+.7152*pD.g+.0722*pD.b;
  if(((x+y)%2)==0){linDark+=lL;dehazedDark+=lD;}
  else{linContrast+=lL;dehazedContrast+=lD;}
  check(pD.a==1.f&&lD>=.0399&&lD<=.9201,"dehaze stays within footprint luma extrema and preserves opaque alpha");
 }
 check(dehazedDark<linDark&&(dehazedContrast-dehazedDark)>(linContrast-linDark),"perceptual dehaze suppresses linear highlight veil and restores micro-contrast");
 std::cout<<Json{{"passed",true},{"quality_contracts",checks},{"max_translucent_chroma_error",chroma},{"contrast_overshoot",overshoot},{"algorithm",QUALITY_ALGORITHM}}.dump()<<std::endl;return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}}
