#pragma once
#include "win_io.hpp"
#include <wincodec.h>
#include <propvarutil.h>
#pragma comment(lib,"windowscodecs.lib")
#pragma comment(lib,"ole32.lib")
#pragma comment(lib,"oleaut32.lib")
namespace h3tier {
struct Wic {
 Com<IWICImagingFactory> f;
 Wic(){hr(CoCreateInstance(CLSID_WICImagingFactory,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(&f)),"WIC factory");}
 struct Frame{Com<IWICStream>s;Com<IWICBitmapDecoder>d;Com<IWICBitmapFrameDecode>v;UINT w=0,h=0;};
 Frame frame(const std::vector<std::uint8_t>&raw){Frame t;hr(f->CreateStream(&t.s),"WIC stream");hr(t.s->InitializeFromMemory((BYTE*)raw.data(),(DWORD)raw.size()),"WIC memory");hr(f->CreateDecoderFromStream(t.s.Get(),nullptr,WICDecodeMetadataCacheOnDemand,&t.d),"WIC decoder");UINT n=0;hr(t.d->GetFrameCount(&n),"frame count");need(n==1,"animated/multi-page master rejected");hr(t.d->GetFrame(0,&t.v),"WIC frame");hr(t.v->GetSize(&t.w,&t.h),"image geometry");need(t.w>0&&t.h>0&&t.w<=MAX_AXIS&&t.h<=MAX_AXIS&&std::uint64_t(t.w)*t.h<=MAX_PIXELS,"decoded image pixel limit");
  Com<IWICMetadataQueryReader>r;if(SUCCEEDED(t.v->GetMetadataQueryReader(&r))){for(auto key:{L"/app1/ifd/{ushort=274}",L"/ifd/{ushort=274}",L"/eXIf/ifd/{ushort=274}"}){PROPVARIANT v{};if(SUCCEEDED(r->GetMetadataByName(key,&v))){bool ok=(v.vt==VT_UI2&&v.uiVal==1)||(v.vt==VT_UI4&&v.ulVal==1);PropVariantClear(&v);need(ok,"EXIF orientation needs explicit normalization before crop approval");}}}
  return t;
 }
 Image decode(const std::vector<std::uint8_t>&raw,bool linear,std::string& color_note){
  auto t=frame(raw);Com<IWICBitmapSource>src=t.v;UINT n=0;auto status=t.v->GetColorContexts(0,nullptr,&n);color_note="untagged RGB interpreted as sRGB";
  if(SUCCEEDED(status)&&n){need(n==1,"ambiguous multiple color profiles");Com<IWICColorContext>from,to;hr(f->CreateColorContext(&from),"source color context");IWICColorContext* ps=from.Get();hr(t.v->GetColorContexts(1,&ps,&n),"read color context");WICColorContextType type{};hr(from->GetType(&type),"profile type");UINT ex=0;if(type==WICColorContextExifColorSpace)from->GetExifColorSpace(&ex);
   if(type==WICColorContextProfile||(type==WICColorContextExifColorSpace&&ex!=1)){hr(f->CreateColorContext(&to),"destination color context");hr(to->InitializeFromExifColorSpace(1),"sRGB destination context");Com<IWICColorTransform>x;hr(f->CreateColorTransformer(&x),"WIC color transform");hr(x->Initialize(t.v.Get(),from.Get(),to.Get(),GUID_WICPixelFormat64bppRGBA),"ICC to sRGB (unsupported profile is not ignored)");src=x;color_note="WIC color-managed conversion to sRGB";}else color_note="declared sRGB";
  }
  // Fail closed for nonstandard PNG transfer/primaries without an ICC transform.
  if(raw.size()>8&&!std::memcmp(raw.data(),"\x89PNG\r\n\x1a\n",8)){
   auto be=[](const BYTE*p){return(unsigned(p[0])<<24)|(unsigned(p[1])<<16)|(unsigned(p[2])<<8)|p[3];};bool srgb=false,icc=false;unsigned gamma=45455;bool primaries=true;size_t at=8;
   while(at+12<=raw.size()){unsigned len=be(raw.data()+at);need(len<=raw.size()-at-12,"PNG chunk extent");auto k=raw.data()+at+4;auto d=k+4;if(!std::memcmp(k,"acTL",4))throw std::runtime_error("APNG master rejected");if(!std::memcmp(k,"sRGB",4))srgb=true;if(!std::memcmp(k,"iCCP",4))icc=true;if(!std::memcmp(k,"gAMA",4)){need(len==4,"invalid gAMA");gamma=be(d);}if(!std::memcmp(k,"cHRM",4)){need(len==32,"invalid cHRM");unsigned standard[]={31270,32900,64000,33000,30000,60000,15000,6000};for(int z=0;z<8;z++)primaries&=be(d+z*4)==standard[z];}at+=12+len;if(!std::memcmp(k,"IEND",4))break;}
   if(!srgb&&!icc)need((gamma==45455||gamma==45454)&&primaries,"non-sRGB PNG gAMA/cHRM needs explicit color-managed normalization");if(icc)need(color_note.find("color-managed")!=std::string::npos,"ICC profile present but not converted");
  }
  Com<IWICFormatConverter>conv;hr(f->CreateFormatConverter(&conv),"format converter");hr(conv->Initialize(src.Get(),GUID_WICPixelFormat64bppRGBA,WICBitmapDitherTypeNone,nullptr,0,WICBitmapPaletteTypeCustom),"16-bit RGBA decode");std::vector<std::uint16_t>rgba(std::size_t(t.w)*t.h*4);hr(conv->CopyPixels(nullptr,t.w*8,(UINT)(rgba.size()*2),(BYTE*)rgba.data()),"decode pixels");Image im(t.w,t.h);auto& lut=linear_lut();for(size_t i=0;i<im.p.size();i++){auto p=rgba.data()+i*4;float a=float(p[3])/65535.f;im.p[i]={a*(linear?lut[p[0]]:float(p[0])/65535.f),a*(linear?lut[p[1]]:float(p[1])/65535.f),a*(linear?lut[p[2]]:float(p[2])/65535.f),a};}return im;
 }
 std::vector<std::uint8_t> decode_rgba(const std::vector<std::uint8_t>&b,int expectedW,int expectedH){auto t=frame(b);need(t.w==unsigned(expectedW)&&t.h==unsigned(expectedH),"PNG readback dimensions differ");Com<IWICFormatConverter>c;hr(f->CreateFormatConverter(&c),"readback converter");hr(c->Initialize(t.v.Get(),GUID_WICPixelFormat32bppRGBA,WICBitmapDitherTypeNone,nullptr,0,WICBitmapPaletteTypeCustom),"readback RGBA");std::vector<BYTE>p(std::size_t(t.w)*t.h*4);hr(c->CopyPixels(nullptr,t.w*4,(UINT)p.size(),p.data()),"PNG readback pixels");return p;}
 std::vector<std::uint8_t> encode(const std::vector<std::uint8_t>&rgba,int w,int h,bool adaptive){
  need(rgba.size()==std::size_t(w)*h*4,"encode dimensions");Com<IStream>s;hr(CreateStreamOnHGlobal(nullptr,TRUE,&s),"encode memory stream");Com<IWICBitmapEncoder>e;hr(f->CreateEncoder(GUID_ContainerFormatPng,nullptr,&e),"PNG encoder");hr(e->Initialize(s.Get(),WICBitmapEncoderNoCache),"PNG initialization");Com<IWICBitmapFrameEncode>frame;Com<IPropertyBag2>props;hr(e->CreateNewFrame(&frame,&props),"PNG frame");PROPBAG2 prop{};prop.pstrName=(LPOLESTR)L"FilterOption";VARIANT v{};v.vt=VT_UI1;v.bVal=static_cast<BYTE>(adaptive?WICPngFilterAdaptive:WICPngFilterNone);hr(props->Write(1,&prop,&v),"PNG filter");hr(frame->Initialize(props.Get()),"PNG options");hr(frame->SetSize(w,h),"PNG size");WICPixelFormatGUID fmt=GUID_WICPixelFormat32bppBGRA;hr(frame->SetPixelFormat(&fmt),"PNG format");need(IsEqualGUID(fmt,GUID_WICPixelFormat32bppBGRA),"PNG encoder changed pixel format");
  Com<IWICMetadataQueryWriter>meta;hr(frame->GetMetadataQueryWriter(&meta),"PNG metadata writer");PROPVARIANT intent{};intent.vt=VT_UI1;intent.bVal=0;hr(meta->SetMetadataByName(L"/sRGB/RenderingIntent",&intent),"sRGB output metadata");
  std::vector<BYTE>bgra(rgba);for(size_t i=0;i<bgra.size();i+=4)std::swap(bgra[i],bgra[i+2]);hr(frame->WritePixels(h,w*4,(UINT)bgra.size(),bgra.data()),"PNG pixel write");hr(frame->Commit(),"PNG frame commit");hr(e->Commit(),"PNG encoder commit");STATSTG st{};hr(s->Stat(&st,STATFLAG_NONAME),"PNG encoded size");need(st.cbSize.QuadPart<512ull*1024*1024,"encoded PNG size limit");std::vector<BYTE>out((size_t)st.cbSize.QuadPart);LARGE_INTEGER zero{};hr(s->Seek(zero,STREAM_SEEK_SET,nullptr),"PNG rewind");ULONG n=0;hr(s->Read(out.data(),(ULONG)out.size(),&n),"PNG bytes");need(n==out.size(),"PNG short stream");return out;
 }
};
}
