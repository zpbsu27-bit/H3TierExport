#include "../src/resample.hpp"
#include "../src/official_silhouette.hpp"
#include "../src/apng.hpp"
#include <iostream>
#include <stdexcept>
using namespace h3tier;
int main(){
 int checks=0;
 auto check=[&](bool v,const char* m){++checks;if(!v)throw std::runtime_error(m);};
 auto mustfail=[&](auto f){try{f();}catch(const std::exception&){++checks;return;}throw std::runtime_error("missing rejection");};
 // A failing check has to name itself: an escaping exception reaches terminate and the harness
 // only sees a crash code, which reads like a compiler problem rather than a broken guarantee.
 try{
 Image im(73,55); for(auto& p:im.p)p={0.15f,0.3f,0.1f,0.5f};
 for(auto filter:{Filter::Lanczos3,Filter::Mitchell,Filter::Area}){auto x=resize_cpu(im,17,13,filter);for(auto p:x.p)check(std::abs(p.r-.15f)<1e-5f&&std::abs(p.a-.5f)<1e-5f,"constant preservation");}
 Image cb(256,256);for(int y=0;y<256;y++)for(int x=0;x<256;x++){float v=(x+y)%2?1.f:0.f;cb.p[y*256+x]={v,v,v,1};}
 auto tiny=resize_cpu(cb,1,1,Filter::Area);auto pix=quantize(tiny,true);check(pix[0]>=187&&pix[0]<=188,"linear-light averaging");check(pix[3]==255,"opaque alpha");
 auto same=resize_cpu(im,73,55,Filter::Lanczos3);check(same.p==im.p,"identity scale exact");
 auto crop=crop_image(im,{3,4,20,17});check(crop.w==17&&crop.h==13,"crop geometry");
 mustfail([&]{crop_image(im,{-1,0,10,10});});mustfail([&]{resize_cpu(im,74,55,Filter::Area);});
 for(auto f:{Filter::Lanczos3,Filter::Mitchell,Filter::Area})for(int n:{1,2,5,31,513})for(int d:{1,2,7,31})if(d<=n){auto a=make_axis(n,d,f);for(auto r:a.spans){double sum=0;for(unsigned k=0;k<r.count;k++)sum+=a.weights[r.offset+k];check(std::abs(sum-1)<2e-6,"normalized weights");check(r.start+r.count<=(unsigned)n,"edge bound");}}
 Image transparent(100,100);for(auto& p:transparent.p)p={0,0,0,0};auto tr=quantize(resize_cpu(transparent,19,19,Filter::Lanczos3),true);for(auto v:tr)check(v==0,"transparent zero");
 // Master preparation is explicit; the ordinary derivative path still rejects enlargement.
 mustfail([&]{make_axis(30,40,Filter::Lanczos3);});
 // The master is never edited. What must be exact is the OUTPUT size: a target that is the
 // official native rect may differ slightly in ratio from the master and is then fitted to it,
 // with the fit recorded. Only a ratio difference beyond the ceiling is refused, because that
 // means the master does not belong to this slot and needs scale-layout registration first.
 check(master_fit_aspect_error(73,55,80,60)<=MASTER_FIT_ASPECT_CEILING,"0.5% ratio difference is inside the fit ceiling");
 auto fit=expand_uniform_cpu(im,80,60);
 check(fit.w==80&&fit.h==60,"output keeps the requested official size exactly");
 check(*master_fit_mode(73,55,80,60)=='o',"non-proportional master is reported as an official-size fit");
 check(*master_fit_mode(73,55,146,110)=='u',"proportional master stays a uniform expansion");
 mustfail([&]{expand_uniform_cpu(im,80,40);});
 mustfail([&]{expand_quality_cpu(im,80,40);});
 mustfail([&]{expand_uniform_cpu(im,219,165);});
 check(expand_uniform_cpu(im,73,55).p==im.p,"expansion identity");
 auto up=expand_uniform_cpu(im,146,110);
 for(auto p:up.p)check(std::abs(p.r-.15f)<1e-5f&&std::abs(p.a-.5f)<1e-5f,"expanded constant preservation");
 auto empty=quantize(expand_uniform_cpu(transparent,200,200),true);
 for(auto v:empty)check(v==0,"expanded transparent zero");
 Image edge(30,24);for(int y=0;y<edge.h;y++)for(int x=0;x<edge.w;x++)edge.p[y*edge.w+x]=x<15?Pixel{.6f,0,0,.6f}:Pixel{};
 auto eu=expand_uniform_cpu(edge,40,32);
 for(auto p:eu.p)check(p.a>=0&&p.a<=.6f&&p.r>=0&&p.r<=p.a&&p.g==0&&p.b==0,"premultiplied edge range without colored halo");
 auto eb=quantize(eu,true);for(size_t i=0;i<eb.size();i+=4)if(!eb[i+3])check(eb[i]==0&&eb[i+1]==0&&eb[i+2]==0,"expanded alpha zero clears RGB");
 // A canvas widened around artwork that keeps its own shape passes the ratio ceiling with 0% error
 // yet shrinks the artwork inside the official cell, so the pad signature has to catch it.
 Image padded(100,100);for(int y=0;y<100;y++)for(int x=10;x<90;x++)padded.p[y*100+x]={.4f,.2f,.1f,1.f};
 auto ps=master_pad_signature(padded);
 check(ps.padded&&ps.left>.09&&ps.left<.11&&ps.right>.09&&ps.right<.11&&ps.top==0&&ps.bottom==0,"symmetric side bands under flush top/bottom read as a padded canvas");
 Image grounded(100,100);for(int y=0;y<80;y++)for(int x=0;x<100;x++)grounded.p[y*100+x]={.4f,.2f,.1f,1.f};
 check(!master_pad_signature(grounded).padded,"one-sided slack under a flush edge is natural framing");
 Image inset(100,100);for(int y=20;y<80;y++)for(int x=20;x<80;x++)inset.p[y*100+x]={.4f,.2f,.1f,1.f};
 check(!master_pad_signature(inset).padded,"slack on both axes is a cutout, not a padded canvas");
 Image offcenter(100,100);for(int y=0;y<100;y++)for(int x=2;x<41;x++)offcenter.p[y*100+x]={.4f,.2f,.1f,1.f};
 check(!master_pad_signature(offcenter).padded,"one-sided off-center framing is not a symmetric pad");
 Image nearEdge(100,100);for(int y=0;y<100;y++)for(int x=1;x<99;x++)nearEdge.p[y*100+x]={.4f,.2f,.1f,1.f};
 check(!master_pad_signature(nearEdge).padded,"1% symmetric bands stay under the pad floor");
 check(!master_pad_signature(im).padded,"a master covered edge to edge carries no pad claim");
 check(!master_pad_signature(transparent).padded,"an empty master carries no pad claim");
 // APNG packing: one animation often mixes frames from two writers, and only one of them
 // records sRGB/gAMA/cHRM/pHYs. Omission inherits the group value; two different
 // declarations of the same chunk stay a hard stop.
 {
  auto chunk=[](const char*type,std::vector<std::uint8_t>data){ApngChunk c;std::copy_n(type,4,c.type.begin());c.data=std::move(data);return c;};
  auto info=[](std::uint8_t ihdr,std::vector<ApngChunk>shared){ApngPngInfo i;i.ihdr.fill(ihdr);i.shared=std::move(shared);return i;};
  auto srgb=chunk("sRGB",{0}),gama=chunk("gAMA",{0,0,0,1}),phys=chunk("pHYs",{0,0,11,19,0,0,11,19,1});
  auto declared=info(7,{srgb,gama,phys}),bare=info(7,{});
  auto merged=declared;apng_adopt_shared(merged,bare);
  check(merged.shared==declared.shared,"a frame without colour metadata inherits the group value");
  auto grown=bare;apng_adopt_shared(grown,declared);
  check(grown.shared==declared.shared,"a later frame contributes the group colour metadata");
  mustfail([&]{auto m=declared;apng_adopt_shared(m,info(7,{chunk("gAMA",{0,0,0,2})}));});
  mustfail([&]{auto m=declared;apng_adopt_shared(m,info(9,{}));});
  check(apng_shared_compatible(merged.shared,bare.shared),"a frame that omits the chunks stays compatible on re-read");
  check(!apng_shared_compatible(merged.shared,{chunk("sRGB",{1})}),"a changed colour chunk is rejected on re-read");
 }
 {
  // An official frame is a slot, not a picture. Filling the slot draws the artwork larger than the
  // game ever did, so a master has to be fitted into the extent the official frame actually paints.
  Image ref(40,40);for(auto&p:ref.p)p={0,0,0,0};
  for(int y=10;y<40;y++)for(int x=15;x<40;x++)ref.p[y*40+x]={.4f,.3f,.2f,1.f};
  auto painted=artwork_box(ref,true);
  check(painted.x0==15&&painted.y0==10&&painted.x1==40&&painted.y1==40,"the reference silhouette is the painted extent, not the slot");
  check(!painted.matte,"a transparent corner carries no matte to strip");
  Image matte(40,40);for(auto&p:matte.p)p={0,0,0,1.f};
  for(int y=8;y<32;y++)for(int x=8;x<32;x++)matte.p[y*40+x]={.5f,.4f,.3f,1.f};
  for(int y=14;y<18;y++)for(int x=14;y<18&&x<18;x++)matte.p[y*40+x]={0,0,0,1.f}; // enclosed black stays art
  auto boxed=artwork_box(matte,true);
  check(boxed.x0==8&&boxed.y0==8&&boxed.x1==32&&boxed.y1==32&&boxed.matte,"a border-connected opaque matte equal to the corner is background");
  check(!artwork_box(matte,false).matte,"matte stripping is opt-in");
  Image darkEdge(40,40);for(auto&p:darkEdge.p)p={0,0,0,0};
  for(int y=0;y<40;y++)for(int x=0;x<40;x++)if(x>=6)darkEdge.p[y*40+x]={.2f,.2f,.2f,1.f};
  darkEdge.p[0]={0,0,0,0};
  check(artwork_box(darkEdge,true).x0==6,"a transparent corner never turns dark artwork into matte");
  int px=0,py=0;
  check(silhouette_place(120,120,30,20,120,120,40,50,"bottom_right",px,py)==nullptr&&px==80&&py==70,"bottom_right keeps the slot corner");
  check(silhouette_place(120,120,30,20,120,120,40,50,"center",px,py)==nullptr&&px==55&&py==45,"center sits inside the silhouette box");
  check(silhouette_place(120,120,30,20,120,120,40,50,"top_left",px,py)!=nullptr,"an unknown anchor is refused");
  check(silhouette_place(120,120,30,20,120,120,200,50,"center",px,py)!=nullptr,"artwork wider than the silhouette box is refused");
  Image master(200,200);for(auto&p:master.p)p={.6f,.2f,.1f,1.f};
  for(int y=0;y<200;y++)for(int x=0;x<20;x++)master.p[y*200+x]={0,0,0,0};
  ArtworkBox slot{30,20,120,120,false,false},art=artwork_box(master);
  check(art.x0==20&&art.x1==200,"the master is measured by its own artwork");
  auto fitted=silhouette_expand_cpu(master,120,120,slot,art,"bottom_right");
  check(fitted.w==120&&fitted.h==120,"the output stays the official slot size");
  auto fb=artwork_box(fitted);
  check(fb.x0>=slot.x0&&fb.y0>=slot.y0&&fb.x1<=slot.x1&&fb.y1<=slot.y1,"the fitted artwork stays inside the official silhouette");
  check(std::abs(double(fb.x1-fb.x0)/double(fb.y1-fb.y0)-179.0/199.0)<0.05,"fitting never stretches the artwork");
  auto again=silhouette_expand_cpu(master,120,120,slot,art,"bottom_right");
  check(again.p==fitted.p,"silhouette expansion is replayable byte for byte");
  mustfail([&]{silhouette_expand_cpu(master,120,120,slot,art,"top_left");});
  mustfail([&]{ArtworkBox emptyBox;silhouette_expand_cpu(master,120,120,emptyBox,art,"bottom_right");});
  ArtworkBox narrow{40,20,80,120,false,false};
  mustfail([&]{silhouette_expand_cpu(master,120,120,narrow,art,"bottom_right");});
  ArtworkBox roomy{20,20,110,120,false,false};
  check(!silhouette_expand_cpu(master,120,120,roomy,art,"bottom_right").p.empty(),"a shape difference inside the coverage floor still fits");
 }
 }catch(const std::exception& e){
  std::cout<<"{\"passed\":false,\"error\":\""<<e.what()<<"\",\"checks\":"<<checks<<"}\n";
  return 1;
 }
 std::cout<<"{\"passed\":true,\"checks\":"<<checks<<"}\n";
}
