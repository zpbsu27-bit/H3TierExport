#pragma once
#include "win_io.hpp"
#include <d3d11.h>
#include <dxgi1_4.h>
#include "axis_shader.h"
#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"dxgi.lib")
namespace h3tier {
struct Gpu {
 Com<IDXGIAdapter1>adapter;Com<ID3D11Device>dev;Com<ID3D11DeviceContext>ctx;Com<ID3D11ComputeShader>shader;Com<ID3D11Buffer>constant;
 std::mutex mutex;Json info;bool available=false;std::uint64_t configured_limit=0;unsigned adapter_index=0;
 explicit Gpu(int chosen=-1,unsigned budgetMB=0){
  info={{"available",false},{"backend","D3D11 compute shader cs_5_0"},{"software_WARP_used",false}};
  try{
   Com<IDXGIFactory1> factory;hr(CreateDXGIFactory1(IID_PPV_ARGS(&factory)),"DXGI factory");Json rows=Json::array();SIZE_T best=0;
   for(UINT i=0;;i++){Com<IDXGIAdapter1>a;auto r=factory->EnumAdapters1(i,&a);if(r==DXGI_ERROR_NOT_FOUND)break;hr(r,"DXGI enumerate");DXGI_ADAPTER_DESC1 d{};hr(a->GetDesc1(&d),"DXGI description");rows.push_back({{"index",i},{"description",utf8(d.Description)},{"dedicated_bytes",d.DedicatedVideoMemory},{"software",bool(d.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)}});if(d.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)continue;if((chosen>=0&&i==(UINT)chosen)||(chosen<0&&d.DedicatedVideoMemory>best)){adapter=a;best=d.DedicatedVideoMemory;adapter_index=i;}}
   info["enumerated_adapters"]=rows;need(bool(adapter),"no suitable hardware GPU");DXGI_ADAPTER_DESC1 d{};hr(adapter->GetDesc1(&d),"adapter info");D3D_FEATURE_LEVEL fl;D3D_FEATURE_LEVEL allowed[]={D3D_FEATURE_LEVEL_11_0};hr(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,0,allowed,1,D3D11_SDK_VERSION,&dev,&fl,&ctx),"hardware D3D11 compute device");hr(dev->CreateComputeShader(g_axis_shader,sizeof(g_axis_shader),nullptr,&shader),"embedded compute shader");D3D11_BUFFER_DESC c{};c.ByteWidth=32;c.Usage=D3D11_USAGE_DEFAULT;c.BindFlags=D3D11_BIND_CONSTANT_BUFFER;hr(dev->CreateBuffer(&c,nullptr,&constant),"GPU constant buffer");
   configured_limit=budgetMB?std::uint64_t(budgetMB)*1024*1024:std::min<std::uint64_t>(std::uint64_t(d.DedicatedVideoMemory)*35/100,4ull*1024*1024*1024);need(configured_limit>=32ull*1024*1024,"insufficient dedicated video memory");
   LARGE_INTEGER version{};adapter->CheckInterfaceSupport(__uuidof(IDXGIDevice),&version);
   info.update({{"available",true},{"description",utf8(d.Description)},{"index",adapter_index},{"vendor_id",d.VendorId},{"device_id",d.DeviceId},{"luid_low",d.AdapterLuid.LowPart},{"luid_high",d.AdapterLuid.HighPart},{"driver_version",version.QuadPart},{"dedicated_bytes",d.DedicatedVideoMemory},{"configured_budget_bytes",configured_limit},{"feature_level","11_0"},{"embedded_shader_bytes",sizeof(g_axis_shader)}});available=true;
  }catch(const std::exception&e){info["error"]=e.what();}
 }
 std::uint64_t live_limit(){Com<IDXGIAdapter3>a;if(SUCCEEDED(adapter.As(&a))){DXGI_QUERY_VIDEO_MEMORY_INFO m{};if(SUCCEEDED(a->QueryVideoMemoryInfo(0,DXGI_MEMORY_SEGMENT_GROUP_LOCAL,&m))){auto free=m.Budget>m.CurrentUsage?m.Budget-m.CurrentUsage:0;return std::min<std::uint64_t>(configured_limit,free*70/100);}}return configured_limit;}
 struct Buffer{Com<ID3D11Buffer>b;Com<ID3D11ShaderResourceView>srv;Com<ID3D11UnorderedAccessView>uav;};
 Buffer buffer(UINT stride,size_t count,const void*data,bool writable){need(count>0&&count*stride<0x80000000ull,"GPU resource size limit");D3D11_BUFFER_DESC d{};d.ByteWidth=(UINT)(count*stride);d.Usage=data?D3D11_USAGE_IMMUTABLE:D3D11_USAGE_DEFAULT;d.BindFlags=D3D11_BIND_SHADER_RESOURCE|(writable?D3D11_BIND_UNORDERED_ACCESS:0);d.MiscFlags=D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;d.StructureByteStride=stride;D3D11_SUBRESOURCE_DATA s{};s.pSysMem=data;Buffer out;hr(dev->CreateBuffer(&d,data?&s:nullptr,&out.b),"GPU buffer");hr(dev->CreateShaderResourceView(out.b.Get(),nullptr,&out.srv),"GPU SRV");if(writable)hr(dev->CreateUnorderedAccessView(out.b.Get(),nullptr,&out.uav),"GPU UAV");return out;}
 Buffer pass(const Buffer&in,int srcW,int w,int h,const Axis&a,bool horizontal){
  auto spans=buffer(16,a.spans.size(),a.spans.data(),false),weights=buffer(4,a.weights.size(),a.weights.data(),false),out=buffer(16,std::size_t(w)*h,nullptr,true);
  ID3D11ShaderResourceView* srvs[]={in.srv.Get(),spans.srv.Get(),weights.srv.Get()};ID3D11UnorderedAccessView*u=out.uav.Get();ID3D11Buffer*c=constant.Get();ctx->CSSetShader(shader.Get(),nullptr,0);ctx->CSSetShaderResources(0,3,srvs);ctx->CSSetUnorderedAccessViews(0,1,&u,nullptr);ctx->CSSetConstantBuffers(0,1,&c);
  // Small dispatch slices, not one unbounded multi-second shader invocation.
  for(int row=0;row<h;row+=32){UINT p[]={UINT(srcW),UINT(w),UINT(h),horizontal?1u:0u,UINT(row),UINT(std::min(32,h-row)),0,0};ctx->UpdateSubresource(constant.Get(),0,nullptr,p,0,0);ctx->Dispatch((w+7)/8,(std::min(32,h-row)+7)/8,1);}
  ID3D11UnorderedAccessView*empty=nullptr;ID3D11ShaderResourceView*none[3]={};ctx->CSSetUnorderedAccessViews(0,1,&empty,nullptr);ctx->CSSetShaderResources(0,3,none);return out;
 }
 std::vector<Image> resize_many_locked(const Image&im,const std::vector<std::array<int,2>>&sizes,Filter f){
  need(available,"GPU unavailable");std::uint64_t peak=0;for(auto q:sizes){need(q[0]>0&&q[1]>0&&q[0]<=im.w&&q[1]<=im.h,"GPU downsampling only");auto intermediate=std::min(std::uint64_t(q[0])*im.h,std::uint64_t(im.w)*q[1]);auto bytes=(im.p.size()+intermediate+2ull*q[0]*q[1])*16+8ull*1024*1024;peak=std::max(peak,bytes);}const auto permitted=live_limit();need(peak<=permitted,"GPU memory admission denied: required="+std::to_string(peak)+" available_budget="+std::to_string(permitted)+" configured_limit="+std::to_string(configured_limit)+"; CPU fallback required");
  // D3D11 defers destruction. This guard is declared BEFORE temporary resources
  // so it runs AFTER their COM references have been released, including on error.
  // Clearing/flushing only our own compute context prevents stale residency from
  // making subsequent valid images appear to exhaust a small adapter budget.
  struct ReleaseDeferred { ID3D11DeviceContext* context; ~ReleaseDeferred(){context->ClearState();context->Flush();} } release{ctx.Get()};
  auto source=buffer(16,im.p.size(),im.p.data(),false);std::vector<Image>result;
  for(auto q:sizes){int w=q[0],h=q[1];if(w==im.w&&h==im.h){result.push_back(im);continue;}auto ax=make_axis(im.w,w,f),ay=make_axis(im.h,h,f);bool firstH=std::uint64_t(w)*im.h<=std::uint64_t(im.w)*h;
   auto middle=firstH?pass(source,im.w,w,im.h,ax,true):pass(source,im.w,im.w,h,ay,false);
   auto out=firstH?pass(middle,w,w,h,ay,false):pass(middle,im.w,w,h,ax,true);
   D3D11_BUFFER_DESC d{};out.b->GetDesc(&d);d.Usage=D3D11_USAGE_STAGING;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;d.BindFlags=0;d.MiscFlags=0;d.StructureByteStride=0;Com<ID3D11Buffer>stage;hr(dev->CreateBuffer(&d,nullptr,&stage),"GPU staging");ctx->CopyResource(stage.Get(),out.b.Get());D3D11_MAPPED_SUBRESOURCE m{};hr(ctx->Map(stage.Get(),0,D3D11_MAP_READ,0,&m),"GPU readback/map; device removal is not success");Image image(w,h);std::memcpy(image.p.data(),m.pData,image.p.size()*16);ctx->Unmap(stage.Get(),0);result.push_back(std::move(image));
  }return result;
 }
};
}
