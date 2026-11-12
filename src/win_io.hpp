#pragma once
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <psapi.h>
#include <wrl/client.h>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <set>
#include <mutex>
#include <atomic>
#include <condition_variable>
#include <thread>
#include <sstream>
#include <iomanip>
#include "../third_party/json.hpp"
#include "resample.hpp"
#pragma comment(lib,"bcrypt.lib")
#pragma comment(lib,"psapi.lib")
namespace h3tier {
using Json=nlohmann::json;namespace fs=std::filesystem;
template<class T>using Com=Microsoft::WRL::ComPtr<T>;
using Clock=std::chrono::steady_clock;
inline double ms(Clock::time_point t){return std::chrono::duration<double,std::milli>(Clock::now()-t).count();}
inline void hr(HRESULT x,const char*s){if(FAILED(x)){char b[32];sprintf_s(b," [HRESULT 0x%08lX]",(unsigned long)x);throw std::runtime_error(std::string(s)+b);}}
inline std::wstring wide(const std::string&s){if(s.empty())return {};int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),(int)s.size(),nullptr,0);need(n>0,"invalid UTF-8");std::wstring o(n,0);MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),(int)s.size(),o.data(),n);return o;}
inline std::string utf8(const std::wstring&s){if(s.empty())return {};int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),(int)s.size(),nullptr,0,nullptr,nullptr);need(n>0,"invalid UTF-16");std::string o(n,0);WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),(int)s.size(),o.data(),n,nullptr,nullptr);return o;}
inline std::string str(const fs::path&p){return utf8(p.wstring());}
inline fs::path path(const std::string&s){need(s.find('\0')==std::string::npos,"NUL path");return fs::path(wide(s));}
inline std::string now(){SYSTEMTIME t{};GetSystemTime(&t);char b[80];sprintf_s(b,"%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,t.wMilliseconds);return b;}
struct Handle{HANDLE h=INVALID_HANDLE_VALUE;Handle()=default;explicit Handle(HANDLE x):h(x){}Handle(const Handle&)=delete;Handle&operator=(const Handle&)=delete;Handle(Handle&&x)noexcept:h(x.h){x.h=INVALID_HANDLE_VALUE;}~Handle(){if(h!=INVALID_HANDLE_VALUE)CloseHandle(h);}operator HANDLE()const{return h;}};
inline fs::path clean_path(fs::path p){
 for(auto&c:p)need(c!=L"..","parent traversal rejected");p=fs::absolute(p).lexically_normal();
 auto w=p.wstring();need(w.size()>3&&w[1]==L':'&&w[2]==L'\\',"only local absolute drive paths supported");need(w.find(L':',2)==std::wstring::npos,"alternate data stream rejected");
 for(auto n=p;!n.empty();n=n.parent_path()){
  DWORD a=GetFileAttributesW(n.c_str());if(a!=INVALID_FILE_ATTRIBUTES)need(!(a&FILE_ATTRIBUTE_REPARSE_POINT),"filesystem links/reparse points rejected: "+str(n));
  if(n==n.parent_path())break;
 }return p;
}
inline std::wstring folded(const fs::path&p){auto s=clean_path(p).wstring();for(auto&c:s)c=(wchar_t)towlower(c);return s;}
inline void contained(const fs::path&p,const fs::path&r){auto x=folded(p),y=folded(r);if(y.back()!=L'\\')y+=L'\\';need(x.size()>y.size()&&x.compare(0,y.size(),y)==0,"output escapes configured root");}
inline Handle open_read(const fs::path&p){auto c=clean_path(p);Handle f(CreateFileW(c.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_SEQUENTIAL_SCAN,nullptr));need(f.h!=INVALID_HANDLE_VALUE,"cannot open source: "+str(c)+" winerr="+std::to_string(GetLastError()));BY_HANDLE_FILE_INFORMATION i{};need(GetFileInformationByHandle(f,&i)&&i.nNumberOfLinks==1&&!(i.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT)),"source must be independent regular file");return f;}
inline std::vector<std::uint8_t> read_all(const fs::path&p,std::uint64_t max=512ull*1024*1024){auto f=open_read(p);LARGE_INTEGER n{};need(GetFileSizeEx(f,&n)&&n.QuadPart>0&&(std::uint64_t)n.QuadPart<=max,"source file size outside limit");std::vector<std::uint8_t>b((size_t)n.QuadPart);size_t at=0;while(at<b.size()){DWORD got=0,ask=(DWORD)std::min<size_t>(b.size()-at,4*1024*1024);need(ReadFile(f,b.data()+at,ask,&got,nullptr)&&got==ask,"short file read");at+=got;}return b;}
inline std::string sha_bytes(const void*p,size_t n){BCRYPT_ALG_HANDLE a=nullptr;need(BCryptOpenAlgorithmProvider(&a,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0,"SHA provider");unsigned char hash[32]{};NTSTATUS st=BCryptHash(a,nullptr,0,(PUCHAR)p,(ULONG)n,hash,32);BCryptCloseAlgorithmProvider(a,0);need(st>=0,"SHA hash");std::ostringstream s;s<<std::hex<<std::setfill('0');for(auto b:hash)s<<std::setw(2)<<(unsigned)b;return s.str();}
inline std::string sha(const std::vector<std::uint8_t>&b){return sha_bytes(b.data(),b.size());}
inline std::string sha_text(const std::string&s){return sha_bytes(s.data(),s.size());}
inline bool valid_sha(const Json&j){if(!j.is_string())return false;auto s=j.get<std::string>();return s.size()==64&&s.find_first_not_of("0123456789abcdef")==std::string::npos;}
inline Json parse_json(const std::vector<std::uint8_t>&b){std::vector<std::set<std::string>>stack;return Json::parse(b,[&](int depth,Json::parse_event_t e,Json&v){need(depth<100,"JSON nesting limit");if(e==Json::parse_event_t::object_start)stack.emplace_back();if(e==Json::parse_event_t::key)need(!stack.empty()&&stack.back().insert(v.get<std::string>()).second,"duplicate JSON key");if(e==Json::parse_event_t::object_end)stack.pop_back();return true;});}
inline Json read_json(const fs::path&p){return parse_json(read_all(p,32*1024*1024));}
inline void write_new(const fs::path&dest,const std::vector<std::uint8_t>&data){
 auto p=clean_path(dest);fs::create_directories(p.parent_path());p=clean_path(p);need(!fs::exists(p),"output already exists: "+str(p));
 static std::atomic<unsigned>seq=0;auto tmp=p;tmp+=L".writing-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(seq++);
 Handle f(CreateFileW(tmp.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr));need(f.h!=INVALID_HANDLE_VALUE,"cannot create output temporary");
 try{size_t at=0;while(at<data.size()){DWORD n=0,ask=(DWORD)std::min<size_t>(data.size()-at,4*1024*1024);need(WriteFile(f,data.data()+at,ask,&n,nullptr)&&n==ask,"short output write");at+=n;}need(FlushFileBuffers(f),"output flush failed");CloseHandle(f.h);f.h=INVALID_HANDLE_VALUE;need(MoveFileExW(tmp.c_str(),p.c_str(),MOVEFILE_WRITE_THROUGH),"atomic publish failed/existing output");}
 catch(...){if(f.h!=INVALID_HANDLE_VALUE){CloseHandle(f.h);f.h=INVALID_HANDLE_VALUE;}DeleteFileW(tmp.c_str());throw;}
}
inline void write_json_new(const fs::path&p,const Json&j){auto s=j.dump(2)+"\n";write_new(p,{s.begin(),s.end()});}
inline void same_or_new(const fs::path&p,const std::vector<std::uint8_t>&b){if(fs::exists(p)){need(read_all(p)==b,"existing output differs; never overwrite: "+str(p));return;}write_new(p,b);need(sha(read_all(p))==sha(b),"output readback hash mismatch");}
inline int integer(const Json&j,int low,int high,const char*name){need(j.is_number_integer()&&!j.is_boolean(),std::string("integer required: ")+name);auto n=j.get<std::int64_t>();need(n>=low&&n<=high,std::string("integer range: ")+name);return(int)n;}
inline std::array<int,2> pair(const Json&j,int low,const char*n){need(j.is_array()&&j.size()==2,std::string("pair required: ")+n);return{integer(j[0],low,MAX_AXIS,n),integer(j[1],low,MAX_AXIS,n)};}
inline Rect rectangle(const Json&j){need(j.is_array()&&j.size()==4,"explicit source/effective crop rectangle required");Rect r{integer(j[0],0,MAX_AXIS,"left"),integer(j[1],0,MAX_AXIS,"top"),integer(j[2],1,MAX_AXIS,"right"),integer(j[3],1,MAX_AXIS,"bottom")};need(r.w()>0&&r.h()>0,"empty rectangle");return r;}
inline std::string token(const std::string&s){need(!s.empty()&&s.size()<100&&s.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")==std::string::npos,"unsafe asset ID");return s;}
inline std::uint64_t peak_rss(){PROCESS_MEMORY_COUNTERS_EX p{};p.cb=sizeof(p);return GetProcessMemoryInfo(GetCurrentProcess(),(PROCESS_MEMORY_COUNTERS*)&p,sizeof(p))?p.PeakWorkingSetSize:0;}
inline Json process_memory_peaks(){PROCESS_MEMORY_COUNTERS_EX p{};p.cb=sizeof(p);need(GetProcessMemoryInfo(GetCurrentProcess(),(PROCESS_MEMORY_COUNTERS*)&p,sizeof(p))!=0,"process memory measurement failed");return{{"peak_process_working_set_bytes",p.PeakWorkingSetSize},{"peak_process_committed_bytes",p.PeakPagefileUsage},{"current_process_private_bytes",p.PrivateUsage}};}
struct MemoryGate{
 std::mutex m;std::condition_variable cv;std::uint64_t used=0,peak=0,limit;
 explicit MemoryGate(std::uint64_t n):limit(n){}
 void acquire(std::uint64_t n){need(n<=limit,"one asset exceeds configured RAM budget");std::unique_lock l(m);cv.wait(l,[&]{return n<=limit-used;});used+=n;peak=std::max(peak,used);}
 void release(std::uint64_t n){std::lock_guard l(m);used-=n;cv.notify_all();}
};
struct MemoryLease{MemoryGate&g;std::uint64_t n;MemoryLease(MemoryGate&g_,std::uint64_t n_):g(g_),n(n_){g.acquire(n);}~MemoryLease(){g.release(n);}};
struct ComInit{HRESULT r;ComInit():r(CoInitializeEx(nullptr,COINIT_MULTITHREADED)){hr(r,"COM initialize");}~ComInit(){if(SUCCEEDED(r))CoUninitialize();}};
inline Json host_memory(){MEMORYSTATUSEX m{sizeof(m)};need(GlobalMemoryStatusEx(&m),"memory query");return{{"physical_bytes",m.ullTotalPhys},{"available_bytes",m.ullAvailPhys},{"logical_threads",std::thread::hardware_concurrency()}};}
}
