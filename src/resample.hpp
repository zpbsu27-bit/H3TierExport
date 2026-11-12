#pragma once
// H3TierExport: direct, scale-aware separable reconstruction.
// Float RGBA is premultiplied, normally in linear sRGB. No chained tier scaling.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#if defined(_M_X64) || defined(__SSE2__)
#include <emmintrin.h>
#define H3TIER_SSE2 1
#endif
namespace h3tier {
inline void need(bool b,const std::string& s){if(!b)throw std::runtime_error(s);}
inline constexpr std::uint64_t MAX_PIXELS=64ull*1024*1024;
inline constexpr int MAX_AXIS=16384;
struct alignas(16) Pixel {float r=0,g=0,b=0,a=0;bool operator==(const Pixel&)const=default;};
static_assert(sizeof(Pixel)==16);
struct Rect{int l=0,t=0,r=0,b=0;int w()const{return r-l;}int h()const{return b-t;}bool operator==(const Rect&)const=default;};
struct Image{
 int w=0,h=0;std::vector<Pixel> p;
 Image()=default;
 Image(int x,int y):w(x),h(y){need(x>0&&y>0&&x<=MAX_AXIS&&y<=MAX_AXIS&&std::uint64_t(x)*y<=MAX_PIXELS,"image pixel/axis limit");p.resize(std::size_t(x)*y);}
};
enum class Filter {Lanczos3,Mitchell,Area,H3Quality};
inline std::string filter_name(Filter f){return f==Filter::Lanczos3?"lanczos3":f==Filter::Mitchell?"mitchell":f==Filter::H3Quality?"h3-quality":"area";}
inline Filter parse_filter(const std::string& s){if(s=="lanczos3")return Filter::Lanczos3;if(s=="mitchell")return Filter::Mitchell;if(s=="area")return Filter::Area;if(s=="h3-quality")return Filter::H3Quality;throw std::runtime_error("unknown filter: "+s);}
inline float srgb_to_linear(float v){return v<=0.04045f?v/12.92f:std::pow((v+.055f)/1.055f,2.4f);}
inline float linear_to_srgb(float v){return v<=.0031308f?v*12.92f:1.055f*std::pow(v,1.f/2.4f)-.055f;}
inline const std::array<float,65536>& linear_lut(){static const auto a=[](){std::array<float,65536>x{};for(int i=0;i<65536;i++)x[i]=srgb_to_linear(float(i)/65535.f);return x;}();return a;}
inline Image crop_image(const Image& im,Rect q){need(q.l>=0&&q.t>=0&&q.w()>0&&q.h()>0&&q.r<=im.w&&q.b<=im.h,"crop outside source");Image out(q.w(),q.h());for(int y=0;y<out.h;y++)std::copy_n(im.p.data()+std::size_t(q.t+y)*im.w+q.l,out.w,out.p.data()+std::size_t(y)*out.w);return out;}
struct Span{std::uint32_t start,count,offset,pad=0;};
struct Axis{std::vector<Span>spans;std::vector<float>weights;};
inline double sinc(double x){if(std::abs(x)<1e-10)return 1.;double p=3.14159265358979323846*x;return std::sin(p)/p;}
inline double kernel(double x,Filter f){x=std::abs(x);if(f==Filter::Lanczos3)return x<3?sinc(x)*sinc(x/3):0.;if(f==Filter::Mitchell){constexpr double B=1./3,C=1./3;if(x<1)return ((12-9*B-6*C)*x*x*x+(-18+12*B+6*C)*x*x+6-2*B)/6;if(x<2)return ((-B-6*C)*x*x*x+(6*B+30*C)*x*x+(-12*B-48*C)*x+8*B+24*C)/6;return 0.;}return x<=.5?1.:0.;}
inline Axis make_axis(int src,int dst,Filter f,bool expand=false){
 need(f!=Filter::H3Quality,"quality filter uses the CPU reconstruction path");
 need(src>0&&src<=MAX_AXIS&&dst>0&&dst<=MAX_AXIS&&(expand||dst<=src),"downsampling only; invalid target axis");Axis a;a.spans.reserve(dst);
 if(src==dst){for(int i=0;i<dst;i++){a.spans.push_back({(unsigned)i,1,(unsigned)i});a.weights.push_back(1.f);}return a;}
 double scale=double(src)/dst,kernelScale=std::max(1.,scale),radius=(f==Filter::Lanczos3?3.:2.)*kernelScale;
 for(int i=0;i<dst;i++){
  double center=(i+.5)*scale-.5;int lo,hi;
  if(f==Filter::Area){lo=std::max(0,int(std::floor(i*scale)));hi=std::min(src-1,int(std::ceil((i+1)*scale))-1);}
  else{lo=std::max(0,int(std::ceil(center-radius)));hi=std::min(src-1,int(std::floor(center+radius)));}
  Span s{(unsigned)lo,(unsigned)(hi-lo+1),(unsigned)a.weights.size()};double sum=0;std::vector<double> w(s.count);
  for(int j=lo;j<=hi;j++){double v=f==Filter::Area?std::max(0.,std::min(double(j+1),(i+1)*scale)-std::max(double(j),i*scale)):kernel((center-j)/kernelScale,f);w[j-lo]=v;sum+=v;}
  need(sum>1e-12,"filter normalization failed");for(double v:w)a.weights.push_back(float(v/sum));a.spans.push_back(s);
 }
 return a;
}
inline Image axis_cpu(const Image& im,const Axis& a,bool horizontal){
 int w=horizontal?int(a.spans.size()):im.w,h=horizontal?im.h:int(a.spans.size());Image out(w,h);
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){
  auto s=a.spans[horizontal?x:y];std::size_t pos=horizontal?std::size_t(y)*im.w+s.start:std::size_t(s.start)*im.w+x;std::size_t step=horizontal?1:im.w;
#ifdef H3TIER_SSE2
  __m128 acc=_mm_setzero_ps();for(unsigned k=0;k<s.count;k++,pos+=step)acc=_mm_add_ps(acc,_mm_mul_ps(_mm_load_ps(&im.p[pos].r),_mm_set1_ps(a.weights[s.offset+k])));_mm_store_ps(&out.p[std::size_t(y)*w+x].r,acc);
#else
  Pixel acc{};for(unsigned k=0;k<s.count;k++,pos+=step){auto p=im.p[pos];float z=a.weights[s.offset+k];acc.r+=p.r*z;acc.g+=p.g*z;acc.b+=p.b*z;acc.a+=p.a*z;}out.p[std::size_t(y)*w+x]=acc;
#endif
 }
 return out;
}
inline Image quality_resize_cpu(const Image& im,int w,int h);
inline Image resize_cpu(const Image& im,int w,int h,Filter f){
 need(w>0&&h>0&&w<=im.w&&h<=im.h,"downsampling only; insufficient master pixels");if(w==im.w&&h==im.h)return im;
 if(f==Filter::H3Quality)return quality_resize_cpu(im,w,h);
 auto ax=make_axis(im.w,w,f),ay=make_axis(im.h,h,f);
 bool firstH=std::uint64_t(w)*im.h<=std::uint64_t(im.w)*h;
 auto tmp=firstH?axis_cpu(im,ax,true):axis_cpu(im,ay,false);
 return firstH?axis_cpu(tmp,ay,false):axis_cpu(tmp,ax,true);
}
// Explicit master preparation only. The ordinary tier exporter remains shrink-only.
// Master files are read-only inputs. Expansion never crops, pads, repaints or resaves a
// master; it writes a separate PNG. What must equal the official native rect times the
// target scale is the OUTPUT, so a master whose own ratio differs slightly from that rect
// is fitted to the official size instead of being edited. Only a gross difference is
// refused, because that means the master does not belong to the slot and has to be
// registered through scale-layout first.
inline constexpr double MASTER_FIT_ASPECT_CEILING=0.05;
inline double master_fit_aspect_error(int sw,int sh,int w,int h){
 double ax=double(w)/sw,ay=double(h)/sh;return std::fabs(ax-ay)/std::max(ax,ay);
}
inline const char* master_fit_mode(int sw,int sh,int w,int h){
 return std::int64_t(w)*sh==std::int64_t(h)*sw?"uniform":"official-size-fit";
}
// The ratio ceiling above only sees the canvas outline, so a master whose canvas was widened
// (or heightened) around an artwork that keeps its own shape passes it with 0% error while the
// artwork itself shrinks inside the official cell. That signature is one axis flush against the
// artwork and near-equal empty bands on the other. Symmetric bands are a master defect: the
// exporter neither stretches nor crops it away, so the master has to be re-derived. The floors
// are deliberately tight - slack that is one-sided, or slack under a band that is not flush,
// is ordinary framing (a road bend, a peak clipped by its slot) and must not be refused.
inline constexpr double MASTER_PAD_EDGE_FLOOR=0.02,MASTER_PAD_FLUSH_CEILING=0.005,MASTER_PAD_SYMMETRY=0.15;
struct MasterPad{double left=0,right=0,top=0,bottom=0;bool padded=false;};
inline bool pad_bands(double first,double last){
 return first>=MASTER_PAD_EDGE_FLOOR&&last>=MASTER_PAD_EDGE_FLOOR
   &&std::fabs(first-last)/std::max(first,last)<=MASTER_PAD_SYMMETRY;
}
inline MasterPad master_pad_signature(const Image& im){
 MasterPad s;auto solid=[&](int x,int y){return im.p[std::size_t(y)*im.w+x].a>1.f/32.f;};
 int first=0,last=-1;
 for(int x=0;x<im.w;x++){bool hit=false;for(int y=0;y<im.h;y++)if(solid(x,y)){hit=true;break;}if(hit){if(last<0)first=x;last=x;}}
 if(last<0)return s;
 s.left=double(first)/im.w;s.right=double(im.w-1-last)/im.w;
 first=0;last=-1;
 for(int y=0;y<im.h;y++){bool hit=false;for(int x=0;x<im.w;x++)if(solid(x,y)){hit=true;break;}if(hit){if(last<0)first=y;last=y;}}
 s.top=double(first)/im.h;s.bottom=double(im.h-1-last)/im.h;
 bool xFlush=s.left<MASTER_PAD_FLUSH_CEILING&&s.right<MASTER_PAD_FLUSH_CEILING;
 bool yFlush=s.top<MASTER_PAD_FLUSH_CEILING&&s.bottom<MASTER_PAD_FLUSH_CEILING;
 s.padded=(xFlush&&pad_bands(s.top,s.bottom))||(yFlush&&pad_bands(s.left,s.right));
 return s;
}
inline Image expand_uniform_cpu(const Image& im,int w,int h){
 need(w>0&&h>0&&w<=MAX_AXIS&&h<=MAX_AXIS&&std::uint64_t(w)*h<=MAX_PIXELS,"expansion pixel/axis limit");
 need(master_fit_aspect_error(im.w,im.h,w,h)<=MASTER_FIT_ASPECT_CEILING,"aspect error above 5% of the official slot ratio; the master is never altered - register it through scale-layout");
 need(w<=im.w*2&&h<=im.h*2,"expansion exceeds 2x per edge; supply a better source");
 if(w<=im.w&&h<=im.h)return resize_cpu(im,w,h,Filter::Lanczos3);
 auto ax=make_axis(im.w,w,Filter::Lanczos3,true),ay=make_axis(im.h,h,Filter::Lanczos3,true);
 bool firstH=std::uint64_t(w)*im.h<=std::uint64_t(im.w)*h;
 auto tmp=firstH?axis_cpu(im,ax,true):axis_cpu(im,ay,false);
 auto out=firstH?axis_cpu(tmp,ay,false):axis_cpu(tmp,ax,true);
 // Limit negative-lobe overshoot to the four neighboring source samples. Work
 // on premultiplied channels so invisible RGB cannot bleed into the silhouette.
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){
  double cx=(x+.5)*im.w/w-.5,cy=(y+.5)*im.h/h-.5;
  int x0=std::clamp(int(std::floor(cx)),0,im.w-1),x1=std::clamp(int(std::floor(cx))+1,0,im.w-1);
  int y0=std::clamp(int(std::floor(cy)),0,im.h-1),y1=std::clamp(int(std::floor(cy))+1,0,im.h-1);
  const Pixel* samples[]={&im.p[std::size_t(y0)*im.w+x0],&im.p[std::size_t(y0)*im.w+x1],&im.p[std::size_t(y1)*im.w+x0],&im.p[std::size_t(y1)*im.w+x1]};
  auto& p=out.p[std::size_t(y)*w+x];
  for(auto channel:{&Pixel::r,&Pixel::g,&Pixel::b,&Pixel::a}){float lo=1.f,hi=0.f;for(auto s:samples){lo=std::min(lo,s->*channel);hi=std::max(hi,s->*channel);}p.*channel=std::clamp(p.*channel,lo,hi);}
  p.a=std::clamp(p.a,0.f,1.f);p.r=std::clamp(p.r,0.f,p.a);p.g=std::clamp(p.g,0.f,p.a);p.b=std::clamp(p.b,0.f,p.a);
 }
 return out;
}
// Quality-first reconstruction. Mathematical filter definitions are implemented
// independently; see README for the primary references and comparison evidence.
inline constexpr const char* QUALITY_ALGORITHM="linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4";
inline Image axis_precise(const Image& im,const Axis& a,bool horizontal){
 int w=horizontal?int(a.spans.size()):im.w,h=horizontal?im.h:int(a.spans.size());Image out(w,h);
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){
  auto s=a.spans[horizontal?x:y];std::size_t pos=horizontal?std::size_t(y)*im.w+s.start:std::size_t(s.start)*im.w+x,step=horizontal?1:im.w;double v[4]{};
  for(unsigned k=0;k<s.count;k++,pos+=step){const auto& p=im.p[pos];double z=a.weights[s.offset+k];v[0]+=p.r*z;v[1]+=p.g*z;v[2]+=p.b*z;v[3]+=p.a*z;}
  out.p[std::size_t(y)*w+x]={float(v[0]),float(v[1]),float(v[2]),float(v[3])};
 }return out;
}
inline double jinc(double r){if(r<1e-9)return 1.;double x=3.14159265358979323846*r;return 2*std::cyl_bessel_j(1.,x)/x;}
inline double ewa_sharp_weight(double radiusSquared){
 constexpr double radius=4.2410628637960699,blur=.88451209326050048;
 constexpr int n=16384;constexpr double bound=radius*radius*blur*blur;
 static const auto table=[](){std::array<double,n+1> a{};for(int i=0;i<=n;i++){double r=std::sqrt(bound*i/n)/blur;a[i]=i==n?0:jinc(r)*jinc(r/radius*1.2196698912665045);}return a;}();
 if(radiusSquared>=bound)return 0;double f=radiusSquared*n/bound;int i=int(f);return table[i]+(table[i+1]-table[i])*(f-i);
}
inline Image ewa_sharp_cpu(const Image& im,int w,int h){
 need(w>0&&h>0&&w<=MAX_AXIS&&h<=MAX_AXIS&&std::uint64_t(w)*h<=MAX_PIXELS,"quality pixel/axis limit");if(w==im.w&&h==im.h)return im;
 double sx=double(im.w)/w,sy=double(im.h)/h,fx=std::max(1.,sx),fy=std::max(1.,sy);constexpr double radius=4.2410628637960699*.88451209326050048;Image out(w,h);
 // No intermediate image or iterative enlargement: every sample is from im.
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){
  double cx=(x+.5)*sx-.5,cy=(y+.5)*sy-.5,v[4]{},sum=0;
  int l=std::max(0,int(std::ceil(cx-radius*fx))),r=std::min(im.w-1,int(std::floor(cx+radius*fx))),t=std::max(0,int(std::ceil(cy-radius*fy))),b=std::min(im.h-1,int(std::floor(cy+radius*fy)));
  for(int yy=t;yy<=b;yy++)for(int xx=l;xx<=r;xx++){
   double dx=(xx-cx)/fx,dy=(yy-cy)/fy,z=ewa_sharp_weight(dx*dx+dy*dy);const auto& p=im.p[std::size_t(yy)*im.w+xx];sum+=z;v[0]+=z*p.r;v[1]+=z*p.g;v[2]+=z*p.b;v[3]+=z*p.a;
  }
  need(sum>1e-10,"EWA normalization failed");out.p[std::size_t(y)*w+x]={float(v[0]/sum),float(v[1]/sum),float(v[2]/sum),float(v[3]/sum)};
 }return out;
}
inline void footprint_antiring(const Image& im,Image& out){
 // Exact rational form of the original half-width max(.5, scale*.5),
 // with source pixel cells [j,j+1] and denominator 2*dst on each axis.
 struct Footprint{std::int64_t left,right,den;int first,last;};
 auto footprints=[](int src,int dst){
  std::vector<Footprint> spans;spans.reserve(dst);
  const std::int64_t den=2*std::int64_t(dst),radius=std::max(src,dst);
  for(int i=0;i<dst;i++){
   const std::int64_t center=(2*std::int64_t(i)+1)*src,left=center-radius,right=center+radius;
   // After clipping, these are floor(left/den) and ceil(right/den)-1.
   spans.push_back({left,right,den,int(std::max<std::int64_t>(0,left)/den),int(std::min<std::int64_t>(src-1,(right-1)/den))});
  }return spans;
 };
 const auto xs=footprints(im.w,out.w),ys=footprints(im.h,out.h);
 for(int y=0;y<out.h;y++)for(int x=0;x<out.w;x++){
  const auto& fx=xs[x];const auto& fy=ys[y];
  double alphaLo=1,alphaHi=0,v[4]{},sum=0,lumaLo=1,lumaHi=0;
  for(int yy=fy.first;yy<=fy.last;yy++)for(int xx=fx.first;xx<=fx.last;xx++){
   const std::int64_t wx=std::min(fx.right,(std::int64_t(xx)+1)*fx.den)-std::max(fx.left,std::int64_t(xx)*fx.den);
   const std::int64_t wy=std::min(fy.right,(std::int64_t(yy)+1)*fy.den)-std::max(fy.left,std::int64_t(yy)*fy.den);
   if(wx<=0||wy<=0)continue;
   const auto& p=im.p[std::size_t(yy)*im.w+xx];const float* q=&p.r;
   // The common positive denominator cancels in v/sum; zero-area neighbors
   // must not widen either the alpha or straight-luma extrema.
   const double z=double(wx)*double(wy);
   alphaLo=std::min(alphaLo,double(p.a));alphaHi=std::max(alphaHi,double(p.a));
   for(int c=0;c<4;c++)v[c]+=q[c]*z;sum+=z;
   if(p.a>1e-7f){double lum=(.2126*p.r+.7152*p.g+.0722*p.b)/p.a;lumaLo=std::min(lumaLo,lum);lumaHi=std::max(lumaHi,lum);}
  }
  need(sum>0,"antiring footprint empty");for(double& c:v)c/=sum;
  auto& p=out.p[std::size_t(y)*out.w+x];double alpha=std::clamp(double(p.a),std::max(0.,alphaLo),std::min(1.,alphaHi));
  if(alpha<=1e-7||v[3]<=1e-7){p={};continue;}
  // Constrain coverage independently and rescale premultiplied RGB with it.
  // Blending all four channels toward an area average also softened the paint
  // on transparent sprites. The straight-color limiter must not change alpha.
  double rgb[3]{},base[3]{},mix=0;const float* q=&p.r;
  for(int c=0;c<3;c++){
   base[c]=v[c]/v[3];rgb[c]=p.a>1e-7f?q[c]/p.a:base[c];double delta=base[c]-rgb[c];
   if(rgb[c]<0&&delta>0)mix=std::max(mix,-rgb[c]/delta);else if(rgb[c]>1&&delta<0)mix=std::max(mix,(1-rgb[c])/delta);
  }
  double lum=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2],baseLum=.2126*base[0]+.7152*base[1]+.0722*base[2],delta=baseLum-lum;
  const double allowance=std::min(.01,.05*(lumaHi-lumaLo));lumaLo-=allowance;lumaHi+=allowance;
  if(lum<lumaLo-2e-6&&delta>0)mix=std::max(mix,(lumaLo-lum)/delta);else if(lum>lumaHi+2e-6&&delta<0)mix=std::max(mix,(lumaHi-lum)/delta);
  mix=std::clamp(mix,0.,1.);p.a=float(alpha);float* dest=&p.r;
  for(int c=0;c<3;c++)dest[c]=float(std::clamp(rgb[c]+(base[c]-rgb[c])*mix,0.,1.)*alpha);
 }
}
inline void dehaze_and_restore_clarity(const Image& im,Image& out){
 if(out.w<4||out.h<4)return;
 Image srcOut=out;
 struct Footprint{std::int64_t left,right,den;int first,last;};
 auto footprints=[](int src,int dst){
  std::vector<Footprint> spans;spans.reserve(dst);
  const std::int64_t den=2*std::int64_t(dst),radius=std::max(src,dst);
  for(int i=0;i<dst;i++){
   const std::int64_t center=(2*std::int64_t(i)+1)*src,left=center-radius,right=center+radius;
   spans.push_back({left,right,den,int(std::max<std::int64_t>(0,left)/den),int(std::min<std::int64_t>(src-1,(right-1)/den))});
  }return spans;
 };
 const auto xs=footprints(im.w,out.w),ys=footprints(im.h,out.h);
 for(int y=1;y+1<out.h;y++)for(int x=1;x+1<out.w;x++){
  const auto& p0=srcOut.p[std::size_t(y)*out.w+x];
  if(p0.a<.98f)continue;
  bool opaqueNeighborhood=true;
  double neighLum=0;
  static constexpr double kW[3][3]={{1./16.,2./16.,1./16.},{2./16.,4./16.,2./16.},{1./16.,2./16.,1./16.}};
  for(int dy=-1;dy<=1&&opaqueNeighborhood;dy++)for(int dx=-1;dx<=1;dx++){
   const auto& q=srcOut.p[std::size_t(y+dy)*out.w+(x+dx)];
   if(q.a<.98f){opaqueNeighborhood=false;break;}
   neighLum+=kW[dy+1][dx+1]*((.2126*q.r+.7152*q.g+.0722*q.b)/q.a);
  }
  if(!opaqueNeighborhood)continue;
  const auto& fx=xs[x];const auto& fy=ys[y];
  double sum=0,linLumSum=0,sqrtLumSum=0,lumaLo=1e9,lumaHi=-1e9,alphaLo=1e9;
  for(int yy=fy.first;yy<=fy.last;yy++)for(int xx=fx.first;xx<=fx.last;xx++){
   const std::int64_t wx=std::min(fx.right,(std::int64_t(xx)+1)*fx.den)-std::max(fx.left,std::int64_t(xx)*fx.den);
   const std::int64_t wy=std::min(fy.right,(std::int64_t(yy)+1)*fy.den)-std::max(fy.left,std::int64_t(yy)*fy.den);
   if(wx<=0||wy<=0)continue;
   const auto& sp=im.p[std::size_t(yy)*im.w+xx];
   const double z=double(wx)*double(wy);
   alphaLo=std::min(alphaLo,double(sp.a));
   if(sp.a>1e-7f){
    double sl=std::clamp((.2126*sp.r+.7152*sp.g+.0722*sp.b)/sp.a,0.,1.);
    linLumSum+=sl*z;sqrtLumSum+=std::sqrt(sl)*z;sum+=z;
    lumaLo=std::min(lumaLo,sl);lumaHi=std::max(lumaHi,sl);
   }
  }
  if(sum<=0||alphaLo<.98||lumaHi-lumaLo<=1e-4)continue;
  const double linLum=linLumSum/sum,percLum=(sqrtLumSum/sum)*(sqrtLumSum/sum);
  const double lum=(.2126*p0.r+.7152*p0.g+.0722*p0.b)/p0.a;
  const double hazeGap=std::max(0.,linLum-percLum);
  const double shadowWeight=std::clamp((lumaHi-lum)/std::max(1e-6,lumaHi-lumaLo),.15,1.);
  const double dehazedLum=lum-.55*hazeGap*shadowWeight;
  const double detail=lum-neighLum;
  const double targetLum=std::clamp(dehazedLum+.32*detail,lumaLo,lumaHi);
  double rgb[3]={p0.r/p0.a,p0.g/p0.a,p0.b/p0.a};
  const double ratio=lum>1e-4?std::clamp(targetLum/lum,.4,2.2):1.;
  const double shift=targetLum-lum;
  auto& dest=out.p[std::size_t(y)*out.w+x];
  dest.r=float(std::clamp(.82*(rgb[0]*ratio)+.18*(rgb[0]+shift),0.,1.)*p0.a);
  dest.g=float(std::clamp(.82*(rgb[1]*ratio)+.18*(rgb[1]+shift),0.,1.)*p0.a);
  dest.b=float(std::clamp(.82*(rgb[2]*ratio)+.18*(rgb[2]+shift),0.,1.)*p0.a);
 }
}
inline Image quality_resize_cpu(const Image& im,int w,int h){
 need(w>0&&h>0&&w<=MAX_AXIS&&h<=MAX_AXIS&&std::uint64_t(w)*h<=MAX_PIXELS,"quality pixel/axis limit");if(w==im.w&&h==im.h)return im;
 Image out;
 if(w>=im.w&&h>=im.h)out=ewa_sharp_cpu(im,w,h);
 else{
  // Scale-widened sinc is retained for minification: radial smoothing can erase
  // woven texture and very fine icon strokes. Both passes use double sums.
  auto ax=make_axis(im.w,w,Filter::Lanczos3,true),ay=make_axis(im.h,h,Filter::Lanczos3,true);bool firstH=std::uint64_t(w)*im.h<=std::uint64_t(im.w)*h;
  auto tmp=firstH?axis_precise(im,ax,true):axis_precise(im,ay,false);out=firstH?axis_precise(tmp,ay,false):axis_precise(tmp,ax,true);
 }
 footprint_antiring(im,out);
 if((w<im.w||h<im.h)&&w>=4&&h>=4)dehaze_and_restore_clarity(im,out);
 return out;
}
inline Image expand_quality_cpu(const Image& im,int w,int h){
 need(master_fit_aspect_error(im.w,im.h,w,h)<=MASTER_FIT_ASPECT_CEILING,"aspect error above 5% of the official slot ratio; the master is never altered - register it through scale-layout");need(w<=im.w*2&&h<=im.h*2,"expansion exceeds 2x per edge; supply a better source");return quality_resize_cpu(im,w,h);
}
inline std::vector<std::uint8_t> quantize(const Image& im,bool linear){
 std::vector<std::uint8_t> out(im.p.size()*4);
 auto byte=[](float v){return(std::uint8_t)std::lround(std::clamp(v,0.f,1.f)*255.f);};
 for(std::size_t i=0;i<im.p.size();i++){
  auto p=im.p[i];need(std::isfinite(p.r)&&std::isfinite(p.g)&&std::isfinite(p.b)&&std::isfinite(p.a),"non-finite filtered pixels");
  float a=std::clamp(p.a,0.f,1.f);auto A=byte(a);out[4*i+3]=A;
  if(!A||a<=1e-7f)continue;
  float v[3]={p.r,p.g,p.b};for(int c=0;c<3;c++){float z=std::clamp(v[c]/a,0.f,1.f);out[4*i+c]=byte(linear?linear_to_srgb(z):z);}
 }
 return out;
}
inline std::vector<std::uint8_t> place_rgba(const Image& crop,int w,int h,int x,int y,bool linear){need(w>0&&h>0&&std::uint64_t(w)*h<=MAX_PIXELS&&x>=0&&y>=0&&x+crop.w<=w&&y+crop.h<=h,"native placement outside canvas");auto rgba=quantize(crop,linear);std::vector<std::uint8_t>out(std::size_t(w)*h*4,0);for(int r=0;r<crop.h;r++)std::memcpy(out.data()+((std::size_t(r+y)*w)+x)*4,rgba.data()+std::size_t(r)*crop.w*4,crop.w*4);return out;}
}
