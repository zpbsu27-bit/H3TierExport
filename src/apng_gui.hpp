#pragma once
#include "apng.hpp"
#include "apng_scale_mode.hpp"
#include <commctrl.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <algorithm>
#include <cwctype>
#include <fstream>
#include <map>
#include <optional>
#include <set>

#pragma comment(lib,"comctl32.lib")
#pragma comment(lib,"comdlg32.lib")
#pragma comment(lib,"shell32.lib")
#pragma comment(lib,"gdi32.lib")
#pragma comment(lib,"msimg32.lib")

namespace h3tier::apngui {

constexpr wchar_t WINDOW_CLASS[]=L"H3TierExport.ApngPreview";
constexpr UINT WM_EXPORT_DONE=WM_APP+73,WM_EXPORT_PROGRESS=WM_APP+74;
constexpr UINT WM_FOLDER_PICK_DONE=WM_APP+75,WM_SCAN_DONE=WM_APP+76,WM_PREVIEW_DONE=WM_APP+77;
constexpr UINT_PTR PREVIEW_TIMER=17;
enum : int {
 IDC_FOLDER=3301,IDC_FOLDER_BROWSE,IDC_OUTPUT,IDC_OUTPUT_BROWSE,
 IDC_GROUPS=3310,IDC_PREVIEW,IDC_FRAME_LABEL,IDC_FRAME_PREV,IDC_PLAY,IDC_FRAME_NEXT,
 IDC_FPS,IDC_LOOP,IDC_MANUAL,IDC_EXPORT_SELECTED,IDC_EXPORT_ALL,IDC_CLOSE,
 IDC_STATUS,IDC_LOG,IDC_SCALE_MODE,IDC_FILE_LABEL
};

struct FrameFile {fs::path path;int frameNumber=-1;std::uint32_t width=0,height=0;std::uint64_t bytes=0;};
struct AnimationGroup {std::wstring key;std::vector<FrameFile>frames;bool manual=false;};
struct PreviewBitmap {HBITMAP bitmap{};std::uint32_t width=0,height=0;std::uint64_t bytes=0;};
struct ExportWork {std::vector<AnimationGroup>groups;fs::path outputDirectory;int fps=8;bool loop=true;};
struct ExportResult {std::size_t succeeded=0;std::vector<std::wstring>lines;};
struct FolderPickResult {std::wstring path,error;bool outputDirectory=false;};
struct ScanResult {fs::path root;std::vector<AnimationGroup>groups;std::size_t pngCount=0,unmatched=0,singletons=0,invalid=0;std::wstring firstError,error;};
struct PreviewLoadResult {std::uint64_t generation=0;std::size_t groupIndex=0;std::vector<PreviewBitmap>frames;std::wstring error;};
struct State {
 HWND owner{},window{},folder{},folderBrowse{},output{},outputBrowse{},groups{},preview{},frameLabel{},framePrev{},play{},frameNext{},fps{},loop{},manual{},exportSelected{},exportAll{},close{},status{},log{};
 HWND scaleMode{},fileLabel{};bool groupScale=true;
 HFONT font{};std::wstring initialGroup;std::vector<AnimationGroup>items;std::vector<PreviewBitmap>previewFrames;
 std::size_t currentFrame=0;int previewGroup=-1;std::uint64_t previewGeneration=0,scanGeneration=0;bool playing=false,busy=false,done=false,outputAuto=true,updatingOutput=false,folderDialogBusy=false,scanning=false,previewLoading=false;
 std::vector<fs::path>pendingManualFiles;
};

inline int dpi(HWND w){auto p=(decltype(&GetDpiForWindow))GetProcAddress(GetModuleHandleW(L"user32.dll"),"GetDpiForWindow");return p?(int)p(w):96;}
inline int sx(HWND w,int n){return MulDiv(n,dpi(w),96);}
inline std::wstring text(HWND h){int n=GetWindowTextLengthW(h);std::wstring out((size_t)std::max(0,n)+1,L'\0');if(n)GetWindowTextW(h,out.data(),n+1);out.resize((size_t)std::max(0,n));return out;}
inline void set_text(HWND h,const std::wstring&s){SetWindowTextW(h,s.c_str());}
inline LRESULT CALLBACK drop_forward(HWND,UINT,WPARAM,LPARAM,UINT_PTR,DWORD_PTR);
inline HWND control(State&s,const wchar_t*klass,const wchar_t*label,DWORD style,int id,DWORD ex=0){auto h=CreateWindowExW(ex,klass,label,WS_CHILD|WS_VISIBLE|style,0,0,10,10,s.window,(HMENU)(INT_PTR)id,GetModuleHandleW(nullptr),nullptr);need(h!=nullptr,"create APNG UI control");DragAcceptFiles(h,TRUE);SetWindowSubclass(h,drop_forward,1,(DWORD_PTR)s.window);if(s.font)SendMessageW(h,WM_SETFONT,(WPARAM)s.font,TRUE);return h;}
inline LRESULT CALLBACK drop_forward(HWND h,UINT message,WPARAM wp,LPARAM lp,UINT_PTR id,DWORD_PTR parent){
 if(message==WM_DROPFILES)return SendMessageW((HWND)parent,message,wp,lp);
 if(message==WM_NCDESTROY)RemoveWindowSubclass(h,drop_forward,id);
 return DefSubclassProc(h,message,wp,lp);
}
inline void append_log(State&s,const std::wstring&line){int n=GetWindowTextLengthW(s.log);SendMessageW(s.log,EM_SETSEL,n,n);auto value=line+L"\r\n";SendMessageW(s.log,EM_REPLACESEL,FALSE,(LPARAM)value.c_str());SendMessageW(s.log,EM_SCROLLCARET,0,0);}
inline void clear_preview(State&s);
inline void load_group_preview(State&s,std::size_t index);
inline void update_preview_file_label(State&s);

inline std::pair<std::uint32_t,std::uint32_t> png_dimensions(const fs::path&path){
 std::ifstream in(path,std::ios::binary);need(bool(in),"cannot open PNG header: "+str(path));std::uint8_t h[24]{};in.read((char*)h,sizeof(h));need(in.gcount()==sizeof(h),"PNG header is truncated: "+str(path));
 static constexpr std::uint8_t sig[]={137,80,78,71,13,10,26,10};need(std::memcmp(h,sig,8)==0&&apng_be32(h+8)==13&&std::memcmp(h+12,"IHDR",4)==0,"invalid PNG header: "+str(path));
 auto w=apng_be32(h+16),height=apng_be32(h+20);need(w>0&&height>0&&w<=MAX_AXIS&&height<=MAX_AXIS&&std::uint64_t(w)*height<=APNG_MAX_FRAME_PIXELS,"PNG dimension/pixel limit: "+str(path));return{w,height};
}
inline std::optional<std::pair<std::wstring,int>> official_frame_key(const fs::path&path){
 auto base=path.stem().wstring();auto hashAt=base.rfind(L"__");
 if(hashAt!=std::wstring::npos){auto suffix=base.substr(hashAt+2);bool hash=suffix.size()==12&&std::all_of(suffix.begin(),suffix.end(),[](wchar_t c){return(c>=L'0'&&c<=L'9')||(c>=L'a'&&c<=L'f')||(c>=L'A'&&c<=L'F');});if(!hash)return std::nullopt;base.resize(hashAt);}
 if(base.rfind(L"def_",0)==0)base.erase(0,4);
 auto frameSep=base.rfind(L'_');if(frameSep==std::wstring::npos||frameSep+1>=base.size())return std::nullopt;
 auto groupSep=base.rfind(L'_',frameSep-1);if(groupSep==std::wstring::npos||groupSep==0||groupSep+1==frameSep)return std::nullopt;
 auto digits=[](const std::wstring&s){return !s.empty()&&std::all_of(s.begin(),s.end(),[](wchar_t c){return c>=L'0'&&c<=L'9';});};
 auto group=base.substr(groupSep+1,frameSep-groupSep-1),frame=base.substr(frameSep+1);if(!digits(group)||!digits(frame))return std::nullopt;
 auto identity=base.substr(0,groupSep);if(identity.size()>=4){auto suffix=identity.substr(identity.size()-4);for(auto&c:suffix)c=(wchar_t)towlower(c);if(suffix==L".def")identity.resize(identity.size()-4);}
 if(identity.empty()||!std::all_of(identity.begin(),identity.end(),[](wchar_t c){return(c>=L'a'&&c<=L'z')||(c>=L'A'&&c<=L'Z')||(c>=L'0'&&c<=L'9')||c==L'_';}))return std::nullopt;
 try{size_t used=0;auto number=std::stoul(frame,&used);if(used!=frame.size()||number>65535)return std::nullopt;return std::pair{identity+L"_"+group,(int)number};}catch(...){return std::nullopt;}
}
inline std::wstring read_folder_dialog(HWND owner){
 Com<IFileOpenDialog>dialog;hr(CoCreateInstance(CLSID_FileOpenDialog,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(&dialog)),"create folder picker");
 FILEOPENDIALOGOPTIONS options{};hr(dialog->GetOptions(&options),"read folder picker options");hr(dialog->SetOptions(options|FOS_PICKFOLDERS|FOS_FORCEFILESYSTEM|FOS_PATHMUSTEXIST|FOS_NOCHANGEDIR),"set folder picker options");hr(dialog->SetTitle(L"选择包含 PNG 帧的文件夹"),"set folder picker title");
 auto shown=dialog->Show(owner);if(shown==HRESULT_FROM_WIN32(ERROR_CANCELLED))return{};hr(shown,"show folder picker");Com<IShellItem>item;hr(dialog->GetResult(&item),"read selected folder");PWSTR value=nullptr;hr(item->GetDisplayName(SIGDN_FILESYSPATH,&value),"read selected folder path");std::wstring result=value?value:L"";CoTaskMemFree(value);return result;
}
inline void start_folder_picker(State&s,bool outputDirectory){
 if(s.folderDialogBusy||s.scanning||s.busy)return;s.folderDialogBusy=true;
 EnableWindow(s.folderBrowse,FALSE);EnableWindow(s.outputBrowse,FALSE);EnableWindow(s.manual,FALSE);EnableWindow(s.close,FALSE);
 set_text(s.status,L"正在打开文件夹选择框…");append_log(s,L"正在打开文件夹选择框…");HWND window=s.window;
 std::thread([window,outputDirectory]{
  auto result=std::make_unique<FolderPickResult>();result->outputDirectory=outputDirectory;bool initialized=false;
  try{
   auto hrResult=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);need(SUCCEEDED(hrResult),"initialize STA folder picker");initialized=true;
   // The main H3TierExport window runs in an MTA.  Show the shell dialog on
   // this dedicated STA worker so its UI can pump independently.
   result->path=read_folder_dialog(window);
  }catch(const std::exception&e){result->error=wide(e.what());}catch(...){result->error=L"文件夹选择框发生未知错误";}
  if(initialized)CoUninitialize();
  if(PostMessageW(window,WM_FOLDER_PICK_DONE,0,(LPARAM)result.get()))result.release();
 }).detach();
}
inline std::vector<fs::path> choose_png_files(HWND owner){
 std::vector<wchar_t>buffer(65536);OPENFILENAMEW of{sizeof(of)};of.hwndOwner=owner;of.lpstrFile=buffer.data();of.nMaxFile=(DWORD)buffer.size();of.lpstrFilter=L"PNG 图片 (*.png)\0*.png\0All files\0*.*\0";of.lpstrTitle=L"选择一组 PNG 帧（可多选）";of.Flags=OFN_FILEMUSTEXIST|OFN_ALLOWMULTISELECT|OFN_EXPLORER|OFN_NOCHANGEDIR;
 if(!GetOpenFileNameW(&of))return{};std::vector<std::wstring>names;for(size_t at=0;at<buffer.size()&&buffer[at];){std::wstring s=buffer.data()+at;names.push_back(s);at+=s.size()+1;}
 std::vector<fs::path>out;if(names.empty())return out;if(names.size()==1)out.emplace_back(names[0]);else{fs::path folder(names[0]);for(size_t i=1;i<names.size();i++)out.push_back(folder/names[i]);}return out;
}
inline void refresh_groups(State&s,std::optional<std::size_t>select=std::nullopt){
 ListView_DeleteAllItems(s.groups);
 for(size_t i=0;i<s.items.size();i++){
  const auto&g=s.items[i];auto key=g.key.empty()?L"(未命名序列)":g.key;auto count=std::to_wstring(g.frames.size());bool sameCanvas=true,duplicateFrame=false;std::uint32_t w=0,h=0;std::set<int>ids;
  for(const auto&f:g.frames){if(!w){w=f.width;h=f.height;}else if(w!=f.width||h!=f.height)sameCanvas=false;if(f.frameNumber>=0&&!ids.insert(f.frameNumber).second)duplicateFrame=true;}
  std::wstring canvas=sameCanvas&&w?std::to_wstring(w)+L"×"+std::to_wstring(h):L"尺寸不同";
  std::wstring state=g.frames.size()>APNG_MAX_FRAMES?L"帧数超限":duplicateFrame?L"帧号重复":sameCanvas?L"可预览":L"可预览/需检查";
  LVITEMW item{};item.mask=LVIF_TEXT|LVIF_PARAM;item.iItem=(int)i;item.pszText=(LPWSTR)key.c_str();item.lParam=(LPARAM)i;ListView_InsertItem(s.groups,&item);
  ListView_SetItemText(s.groups,(int)i,1,(LPWSTR)count.c_str());ListView_SetItemText(s.groups,(int)i,2,(LPWSTR)canvas.c_str());ListView_SetItemText(s.groups,(int)i,3,(LPWSTR)state.c_str());
 }
 if(select&&*select<s.items.size()){ListView_SetItemState(s.groups,(int)*select,LVIS_SELECTED|LVIS_FOCUSED,LVIS_SELECTED|LVIS_FOCUSED);ListView_EnsureVisible(s.groups,(int)*select,FALSE);}
}
inline fs::path default_output_directory(const fs::path&folder){return folder.parent_path()/fs::path(folder.filename().wstring()+L"-APNG导出");}
inline void set_default_output(State&s,const fs::path&folder){if(s.outputAuto){auto out=default_output_directory(folder);s.updatingOutput=true;set_text(s.output,out.wstring());s.updatingOutput=false;}}
inline ScanResult scan_folder_data(const fs::path&folder){
 ScanResult result;result.root=clean_path(folder);need(fs::is_directory(result.root),"请选择一个存在的文件夹");
 std::map<std::wstring,AnimationGroup>groups;
 for(const auto&entry:fs::directory_iterator(result.root)){
  if(!entry.is_regular_file())continue;DWORD attrs=GetFileAttributesW(entry.path().c_str());if(attrs==INVALID_FILE_ATTRIBUTES||(attrs&FILE_ATTRIBUTE_REPARSE_POINT))continue;
  auto ext=entry.path().extension().wstring();for(auto&c:ext)c=(wchar_t)towlower(c);if(ext!=L".png")continue;result.pngCount++;
  auto parsed=official_frame_key(entry.path());if(!parsed){result.unmatched++;continue;}
  try{auto file=clean_path(entry.path());auto [w,h]=png_dimensions(file);auto&g=groups[parsed->first];g.key=parsed->first;g.frames.push_back({file,parsed->second,w,h,fs::file_size(file)});}
  catch(const std::exception&e){result.invalid++;if(result.firstError.empty())result.firstError=wide(e.what());}
 }
 for(auto&[key,g]:groups){
  std::stable_sort(g.frames.begin(),g.frames.end(),[](const FrameFile&a,const FrameFile&b){return a.frameNumber<b.frameNumber;});
  if(g.frames.size()<2){result.singletons++;continue;}result.groups.push_back(std::move(g));
 }
 return result;
}
inline void scan_folder_async(State&s,const fs::path&folder,std::vector<fs::path>manualFiles={}){
 s.scanning=true;s.pendingManualFiles=std::move(manualFiles);auto generation=++s.scanGeneration;
 clear_preview(s);s.items.clear();refresh_groups(s);set_text(s.status,L"正在扫描文件夹；窗口仍可响应。 "+folder.wstring());
 EnableWindow(s.folderBrowse,FALSE);EnableWindow(s.outputBrowse,FALSE);EnableWindow(s.manual,FALSE);EnableWindow(s.exportSelected,FALSE);EnableWindow(s.exportAll,FALSE);
 HWND window=s.window;std::thread([window,generation,folder]{
  auto result=std::make_unique<ScanResult>();
  try{*result=scan_folder_data(folder);}catch(const std::exception&e){result->error=wide(e.what());}catch(...){result->error=L"未知扫描错误";}
  if(PostMessageW(window,WM_SCAN_DONE,(WPARAM)generation,(LPARAM)result.get()))result.release();
 }).detach();
}

inline void clear_preview(State&s){
 KillTimer(s.window,PREVIEW_TIMER);s.previewGeneration++;s.previewLoading=false;s.playing=false;set_text(s.play,L"播放");
 for(auto&frame:s.previewFrames)if(frame.bitmap)DeleteObject(frame.bitmap);s.previewFrames.clear();s.currentFrame=0;s.previewGroup=-1;
 if(s.framePrev)EnableWindow(s.framePrev,FALSE);if(s.play)EnableWindow(s.play,FALSE);if(s.frameNext)EnableWindow(s.frameNext,FALSE);
 if(s.preview)InvalidateRect(s.preview,nullptr,TRUE);if(s.frameLabel)set_text(s.frameLabel,L"无预览");
 if(s.fileLabel)set_text(s.fileLabel,L"文件：无预览");
}
inline void destroy_preview_frames(std::vector<PreviewBitmap>&frames){for(auto&frame:frames)if(frame.bitmap)DeleteObject(frame.bitmap);frames.clear();}
inline int preview_fps(const State&s){try{size_t used=0;auto value=text(s.fps);int fps=std::stoi(value,&used);if(used==value.size()&&fps>=1&&fps<=60)return fps;}catch(...){}return 8;}
inline void start_preview_playback(State&s,bool restartAtEnd){
 if(s.previewFrames.empty()||s.previewLoading)return;
 bool loop=SendMessageW(s.loop,BM_GETCHECK,0,0)==BST_CHECKED;
 if(restartAtEnd&&!loop&&s.currentFrame+1>=s.previewFrames.size())s.currentFrame=0;
 set_text(s.frameLabel,L"帧 "+std::to_wstring(s.currentFrame+1)+L" / "+std::to_wstring(s.previewFrames.size()));
 update_preview_file_label(s);
 InvalidateRect(s.preview,nullptr,FALSE);SetTimer(s.window,PREVIEW_TIMER,(UINT)std::max(16,1000/preview_fps(s)),nullptr);s.playing=true;set_text(s.play,L"暂停");
}
inline PreviewBitmap make_preview_bitmap(HDC dc,const std::vector<std::uint8_t>&rgba,std::uint32_t width,std::uint32_t height){
 BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=(LONG)width;info.bmiHeader.biHeight=-(LONG)height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
 void*bits=nullptr;HBITMAP bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&bits,nullptr,0);need(bitmap&&bits,"create APNG preview bitmap");auto*dst=(std::uint8_t*)bits;
 for(size_t i=0;i<rgba.size();i+=4){unsigned a=rgba[i+3];dst[i+0]=(std::uint8_t)((unsigned(rgba[i+2])*a+127)/255);dst[i+1]=(std::uint8_t)((unsigned(rgba[i+1])*a+127)/255);dst[i+2]=(std::uint8_t)((unsigned(rgba[i+0])*a+127)/255);dst[i+3]=(std::uint8_t)a;}
 return{bitmap,width,height,rgba.size()};
}
inline void load_group_preview(State&s,std::size_t index){
 need(index<s.items.size(),"animation group selection out of range");if(s.previewGroup==(int)index&&(s.previewLoading||!s.previewFrames.empty()))return;need(s.items[index].frames.size()>=2&&s.items[index].frames.size()<=APNG_MAX_FRAMES,"animation group needs 2 to 300 frames");
 clear_preview(s);s.previewGroup=(int)index;s.previewLoading=true;auto generation=s.previewGeneration;auto group=s.items[index];set_text(s.frameLabel,L"正在加载帧图…");set_text(s.status,L"正在加载预览："+group.key+L"（窗口仍可响应）");
 HWND window=s.window;std::thread([window,generation,index,group=std::move(group)]()mutable{
  auto result=std::make_unique<PreviewLoadResult>();result->generation=generation;result->groupIndex=index;std::uint64_t total=0;HDC dc=nullptr;
  try{
   ComInit com;dc=GetDC(nullptr);need(dc!=nullptr,"get desktop preview device context");Wic wic;
   for(const auto&frame:group.frames){auto raw=read_all(frame.path,APNG_MAX_FRAME_BYTES);total+=std::uint64_t(frame.width)*frame.height*4;need(total<=APNG_MAX_INPUT_BYTES,"APNG_PREVIEW_MEMORY_LIMIT");auto info=apng_inspect_png(raw);auto rgba=wic.decode_rgba(raw,(int)info.width,(int)info.height);need(rgba.size()==std::size_t(info.width)*info.height*4,"APNG_PREVIEW_FRAME_DECODE_MISMATCH");result->frames.push_back(make_preview_bitmap(dc,rgba,info.width,info.height));}
  }catch(const std::exception&e){result->error=wide(e.what());destroy_preview_frames(result->frames);}catch(...){result->error=L"未知帧预览错误";destroy_preview_frames(result->frames);}
  if(dc)ReleaseDC(nullptr,dc);
  if(PostMessageW(window,WM_PREVIEW_DONE,0,(LPARAM)result.get()))result.release();else destroy_preview_frames(result->frames);
 }).detach();
}
inline double current_preview_scale(const State&s,int width,int height){
 std::vector<std::pair<std::uint32_t,std::uint32_t>>canvases;
 for(const auto&frame:s.previewFrames)canvases.emplace_back(frame.width,frame.height);
 return apngscale::factor(canvases,std::min(s.currentFrame,canvases.empty()?std::size_t(0):canvases.size()-1),s.groupScale,width,height,sx(s.window,12));
}
inline std::wstring preview_filename_text(const fs::path&file){
 auto parsed=official_frame_key(file);std::wstring def=L"未识别";
 if(parsed){auto sep=parsed->first.rfind(L'_');def=parsed->first.substr(0,sep)+L".DEF";}
 return L"DEF："+def+L" | "+file.filename().wstring();
}
inline void update_preview_file_label(State&s){
 if(!s.fileLabel||s.previewGroup<0||(size_t)s.previewGroup>=s.items.size())return;
 const auto&group=s.items[(size_t)s.previewGroup];if(s.currentFrame>=group.frames.size())return;
 auto value=preview_filename_text(group.frames[s.currentFrame].path);
 if(s.currentFrame<s.previewFrames.size()){
  const auto&frame=s.previewFrames[s.currentFrame];RECT view{};GetClientRect(s.preview,&view);
  auto pct=std::llround(current_preview_scale(s,view.right-view.left,view.bottom-view.top)*1000)/10.0;
  value+=L" | "+std::to_wstring(frame.width)+L"×"+std::to_wstring(frame.height)+L" | "+std::to_wstring(pct)+L"%";
 }
 set_text(s.fileLabel,value);
}
inline void draw_preview(State&s,DRAWITEMSTRUCT*draw){
 auto dc=draw->hDC;auto r=draw->rcItem;FillRect(dc,&r,(HBRUSH)GetStockObject(WHITE_BRUSH));int tile=sx(s.window,18);
 HBRUSH light=CreateSolidBrush(RGB(238,238,238)),dark=CreateSolidBrush(RGB(215,215,215));
 for(int y=(int)r.top;y<r.bottom;y+=tile)for(int x=(int)r.left;x<r.right;x+=tile){RECT cell{x,y,std::min<LONG>((LONG)x+tile,r.right),std::min<LONG>((LONG)y+tile,r.bottom)};FillRect(dc,&cell,(((x-r.left)/tile+(y-r.top)/tile)&1)?dark:light);}
 DeleteObject(light);DeleteObject(dark);
 if(s.previewFrames.empty()){SetBkMode(dc,TRANSPARENT);DrawTextW(dc,L"选择左侧动画组即可播放预览",-1,&r,DT_CENTER|DT_VCENTER|DT_SINGLELINE);return;}
 const auto&frame=s.previewFrames[std::min(s.currentFrame,s.previewFrames.size()-1)];int rw=r.right-r.left,rh=r.bottom-r.top;double scale=current_preview_scale(s,rw,rh);int w=std::max(1,(int)std::llround(frame.width*scale)),h=std::max(1,(int)std::llround(frame.height*scale));int x=r.left+(rw-w)/2,y=r.top+(rh-h)/2;
 HDC memory=CreateCompatibleDC(dc);if(memory){auto old=SelectObject(memory,frame.bitmap);BLENDFUNCTION blend{AC_SRC_OVER,0,255,AC_SRC_ALPHA};SetStretchBltMode(dc,HALFTONE);AlphaBlend(dc,x,y,w,h,memory,0,0,(int)frame.width,(int)frame.height,blend);SelectObject(memory,old);DeleteDC(memory);}
}
inline void layout(State&s){
 RECT r{};GetClientRect(s.window,&r);int W=r.right,H=r.bottom,m=sx(s.window,14),gap=sx(s.window,8),bh=sx(s.window,30),label=sx(s.window,70),browse=sx(s.window,150);
 int y=m;MoveWindow(GetDlgItem(s.window,3201),m,y,label,bh,TRUE);MoveWindow(s.folder,m+label,y,W-2*m-label-browse-gap,bh,TRUE);MoveWindow(s.folderBrowse,W-m-browse,y,browse,bh,TRUE);y+=bh+gap;
 MoveWindow(GetDlgItem(s.window,3202),m,y,label,bh,TRUE);MoveWindow(s.output,m+label,y,W-2*m-label-browse-gap,bh,TRUE);MoveWindow(s.outputBrowse,W-m-browse,y,browse,bh,TRUE);y+=bh+gap;
 MoveWindow(GetDlgItem(s.window,3203),m,y,W-2*m,sx(s.window,30),TRUE);y+=sx(s.window,30)+gap;
 int bottom=sx(s.window,112),leftW=std::min(sx(s.window,500),(W-3*m)/2),rightX=m+leftW+gap,rightW=std::max(sx(s.window,260),W-rightX-m),bodyH=std::max(sx(s.window,180),H-y-bottom-m);
 MoveWindow(s.groups,m,y,leftW,bodyH,TRUE);MoveWindow(s.preview,rightX,y,rightW,std::max(sx(s.window,40),bodyH-sx(s.window,116)),TRUE);
 MoveWindow(s.fileLabel,rightX,y+bodyH-sx(s.window,110),rightW,sx(s.window,36),TRUE);
 MoveWindow(GetDlgItem(s.window,3205),rightX,y+bodyH-sx(s.window,70),sx(s.window,76),sx(s.window,28),TRUE);
 MoveWindow(s.scaleMode,rightX+sx(s.window,80),y+bodyH-sx(s.window,72),std::max(sx(s.window,170),rightW-sx(s.window,80)),sx(s.window,180),TRUE);
 int navY=y+bodyH-sx(s.window,38),btn=sx(s.window,78);MoveWindow(s.framePrev,rightX,navY,btn,sx(s.window,28),TRUE);MoveWindow(s.play,rightX+btn+gap,navY,btn,sx(s.window,28),TRUE);MoveWindow(s.frameNext,rightX+2*(btn+gap),navY,btn,sx(s.window,28),TRUE);MoveWindow(s.frameLabel,rightX+3*(btn+gap),navY,rightW-3*(btn+gap),sx(s.window,28),TRUE);
 int rowY=H-bottom+m/2;MoveWindow(s.manual,m,rowY,sx(s.window,150),bh,TRUE);MoveWindow(GetDlgItem(s.window,3204),m+sx(s.window,158),rowY,sx(s.window,55),bh,TRUE);MoveWindow(s.fps,m+sx(s.window,215),rowY,sx(s.window,55),bh,TRUE);MoveWindow(s.loop,m+sx(s.window,278),rowY,sx(s.window,140),bh,TRUE);
 int closeW=sx(s.window,90),allW=sx(s.window,125),selW=sx(s.window,125),x=W-m-closeW;MoveWindow(s.close,x,rowY,closeW,bh,TRUE);x-=gap+allW;MoveWindow(s.exportAll,x,rowY,allW,bh,TRUE);x-=gap+selW;MoveWindow(s.exportSelected,x,rowY,selW,bh,TRUE);
 MoveWindow(s.status,m,H-bottom+bh+m/2,W-2*m,sx(s.window,24),TRUE);MoveWindow(s.log,m,H-bottom+bh+sx(s.window,28)+m/2,W-2*m,std::max(sx(s.window,42),bottom-bh-sx(s.window,28)),TRUE);
 update_preview_file_label(s);
}

inline void update_output_folder(State&s,const std::wstring&folder,const std::vector<fs::path>&manualFiles={}){if(!folder.empty()){set_text(s.folder,folder);set_default_output(s,fs::path(folder));scan_folder_async(s,fs::path(folder),manualFiles);}}
inline void add_manual_group(State&s,const std::vector<fs::path>&paths){
 need(paths.size()>=2,"请选择至少两张 PNG");AnimationGroup group;group.manual=true;group.key=L"手动序列 "+std::to_wstring(s.items.size()+1);std::set<std::wstring>seen;
 for(const auto&p:paths){auto file=clean_path(p);auto ext=file.extension().wstring();for(auto&c:ext)c=(wchar_t)towlower(c);need(ext==L".png","只能添加 PNG 图片");auto key=folded(file);if(!seen.insert(key).second)continue;auto[w,h]=png_dimensions(file);group.frames.push_back({file,-1,w,h,fs::file_size(file)});}
 need(group.frames.size()>=2,"手动序列至少需要两张不同 PNG");auto first=official_frame_key(group.frames.front().path);if(first){bool all=true;for(const auto&f:group.frames){auto parsed=official_frame_key(f.path);if(!parsed||parsed->first!=first->first){all=false;break;}}if(all)std::stable_sort(group.frames.begin(),group.frames.end(),[](const auto&a,const auto&b){return a.frameNumber<b.frameNumber;});}
 if(text(s.output).empty()){s.outputAuto=true;set_default_output(s,group.frames.front().path.parent_path());}
 s.items.push_back(std::move(group));refresh_groups(s,s.items.size()-1);EnableWindow(s.exportSelected,!s.busy&&!s.scanning);EnableWindow(s.exportAll,!s.busy&&!s.scanning);load_group_preview(s,s.items.size()-1);
}
inline std::wstring output_stem(const AnimationGroup&g){
 std::wstring base=g.key;if(g.manual){auto end=g.key.find_last_not_of(L"0123456789");auto suffix=end==std::wstring::npos?std::wstring(L"1"):g.key.substr(end+1);base=L"manual-sequence-"+suffix;}
 for(auto&c:base)if(!(c>=L'a'&&c<=L'z')&&!(c>=L'A'&&c<=L'Z')&&!(c>=L'0'&&c<=L'9')&&c!=L'_'&&c!=L'-')c=L'-';
 while(!base.empty()&&base.back()==L'-')base.pop_back();if(base.empty())base=L"animation";return base+L".apng.png";
}
inline void set_busy(State&s,bool busy){
 s.busy=busy;bool ready=!busy&&!s.scanning&&!s.folderDialogBusy;for(HWND h:{s.folder,s.folderBrowse,s.output,s.outputBrowse,s.groups,s.framePrev,s.play,s.frameNext,s.fps,s.loop,s.manual,s.exportSelected,s.exportAll,s.close,s.scaleMode})EnableWindow(h,ready);
 if(busy){KillTimer(s.window,PREVIEW_TIMER);s.playing=false;set_text(s.play,L"播放");set_text(s.status,L"正在生成 APNG；源 PNG 只读。");}
 else{int i=ListView_GetNextItem(s.groups,-1,LVNI_SELECTED);EnableWindow(s.exportSelected,ready&&i>=0);EnableWindow(s.exportAll,ready&&!s.items.empty());EnableWindow(s.framePrev,ready&&!s.previewFrames.empty());EnableWindow(s.play,ready&&!s.previewFrames.empty());EnableWindow(s.frameNext,ready&&!s.previewFrames.empty());}
}
inline void start_export(State&s,bool all){
 need(!s.busy&&!s.scanning&&!s.folderDialogBusy&&!s.previewLoading,"APNG UI is busy");auto outText=text(s.output);need(!outText.empty(),"请选择输出文件夹");auto out=clean_path(fs::path(outText));need(fs::exists(out)?fs::is_directory(out):true,"输出路径不是文件夹");
 std::vector<AnimationGroup>batch;if(all)batch=s.items;else{int index=ListView_GetNextItem(s.groups,-1,LVNI_SELECTED);need(index>=0&&(size_t)index<s.items.size(),"请选择一个动画组");batch.push_back(s.items[(size_t)index]);}
 need(!batch.empty(),"没有可导出的动画组");size_t used=0;auto fpsText=text(s.fps);int fps=0;try{fps=std::stoi(fpsText,&used);}catch(...){throw std::runtime_error("FPS 必须是 1 到 60 的整数");}need(used==fpsText.size()&&fps>=1&&fps<=60,"FPS 必须是 1 到 60 的整数");bool loop=SendMessageW(s.loop,BM_GETCHECK,0,0)==BST_CHECKED;
 ExportWork work{std::move(batch),out,fps,loop};set_busy(s,true);auto hwnd=s.window;
 std::thread([hwnd,work=std::move(work)]()mutable{
  auto*result=new ExportResult;
  try{ComInit com;std::size_t index=0;
   for(const auto&group:work.groups){
   std::wstring label=group.key;auto progressText=std::make_unique<std::wstring>(L"打包 "+std::to_wstring(index+1)+L" / "+std::to_wstring(work.groups.size())+L"："+label);if(PostMessageW(hwnd,WM_EXPORT_PROGRESS,0,(LPARAM)progressText.get()))progressText.release();
    try{if(!group.manual){std::set<int>frameIds;for(const auto&frame:group.frames)need(frameIds.insert(frame.frameNumber).second,"APNG_AUTOMATIC_GROUP_HAS_DUPLICATE_FRAME_NUMBER");}std::vector<fs::path>paths;for(const auto&frame:group.frames)paths.push_back(frame.path);auto output=work.outputDirectory/output_stem(group);auto packed=pack_apng(paths,output,work.fps,work.loop);result->succeeded++;result->lines.push_back(L"已导出："+packed.output.wstring()+L"（"+std::to_wstring(packed.frames)+L" 帧，"+std::to_wstring(packed.width)+L"×"+std::to_wstring(packed.height)+L"，"+std::to_wstring(packed.fps)+L" FPS，SHA-256 "+wide(packed.sha256)+L"）");}
    catch(const std::exception&e){result->lines.push_back(L"失败："+label+L" — "+wide(e.what()));}
    index++;
   }
  }catch(const std::exception&e){result->lines.push_back(L"导出线程失败："+wide(e.what()));}catch(...){result->lines.push_back(L"导出线程失败：未知错误");}
  if(!PostMessageW(hwnd,WM_EXPORT_DONE,0,(LPARAM)result))delete result;
 }).detach();
}

inline void add_file_paths(State&s,const std::vector<fs::path>&paths){
 std::vector<fs::path>files;std::optional<fs::path>folder;
 for(const auto&p:paths){DWORD a=GetFileAttributesW(p.c_str());if(a!=INVALID_FILE_ATTRIBUTES&&(a&FILE_ATTRIBUTE_DIRECTORY)){folder=p;break;}files.push_back(p);}
 if(folder){update_output_folder(s,folder->wstring(),files);return;}
 add_manual_group(s,files);
}
inline void delete_manual_group(State&s,std::size_t index){
 need(index<s.items.size()&&s.items[index].manual,"only a manual sequence can be removed here");
 auto label=s.items[index].key;clear_preview(s);s.items.erase(s.items.begin()+index);refresh_groups(s,s.items.empty()?std::nullopt:std::optional<std::size_t>(std::min(index,s.items.size()-1)));
 EnableWindow(s.exportAll,!s.items.empty());EnableWindow(s.exportSelected,!s.items.empty());
 if(!s.items.empty()){auto selected=ListView_GetNextItem(s.groups,-1,LVNI_SELECTED);if(selected>=0)load_group_preview(s,(std::size_t)selected);}
 else{clear_preview(s);set_text(s.frameLabel,L"无预览");}
 auto message=L"已移除"+label+L"（只从列表移除，不删除源 PNG）";set_text(s.status,message);append_log(s,message);
}
inline void show_group_context_menu(State&s,LPARAM location){
 if(s.busy||s.scanning||s.folderDialogBusy)return;
 POINT screen{(short)LOWORD(location),(short)HIWORD(location)};int index=-1;
 if(screen.x==-1&&screen.y==-1)index=ListView_GetNextItem(s.groups,-1,LVNI_SELECTED);
 else{POINT client=screen;ScreenToClient(s.groups,&client);LVHITTESTINFO hit{};hit.pt=client;index=ListView_HitTest(s.groups,&hit);if(index>=0)ListView_SetItemState(s.groups,index,LVIS_SELECTED|LVIS_FOCUSED,LVIS_SELECTED|LVIS_FOCUSED);}
 if(index<0||(std::size_t)index>=s.items.size()||!s.items[(std::size_t)index].manual)return;
 if(screen.x==-1&&screen.y==-1){RECT row{};if(ListView_GetItemRect(s.groups,index,&row,LVIR_BOUNDS)){screen={row.left,row.bottom};ClientToScreen(s.groups,&screen);}else{GetCursorPos(&screen);}}
 HMENU menu=CreatePopupMenu();need(menu!=nullptr,"create manual-sequence context menu");AppendMenuW(menu,MF_STRING,1,L"删除手动序列");SetForegroundWindow(s.window);
 UINT command=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_RIGHTBUTTON,screen.x,screen.y,0,s.window,nullptr);DestroyMenu(menu);PostMessageW(s.window,WM_NULL,0,0);
 if(command==1)delete_manual_group(s,(std::size_t)index);
}
inline LRESULT CALLBACK group_context_forward(HWND h,UINT message,WPARAM wp,LPARAM lp,UINT_PTR id,DWORD_PTR parent){
 if(message==WM_CONTEXTMENU){SendMessageW((HWND)parent,message,(WPARAM)h,lp);return 0;}
 if(message==WM_NCDESTROY)RemoveWindowSubclass(h,group_context_forward,id);
 return DefSubclassProc(h,message,wp,lp);
}
inline void finish_folder_dialog(State&s,std::unique_ptr<FolderPickResult>result){
 s.folderDialogBusy=false;EnableWindow(s.folderBrowse,!s.scanning&&!s.busy);EnableWindow(s.outputBrowse,!s.scanning&&!s.busy);EnableWindow(s.manual,!s.scanning&&!s.busy);EnableWindow(s.close,!s.busy);
 if(!result->error.empty()){set_text(s.status,L"文件夹选择失败："+result->error);append_log(s,L"文件夹选择失败："+result->error);return;}
 if(result->path.empty()){set_text(s.status,L"已取消文件夹选择。");return;}
 if(result->outputDirectory){set_text(s.output,result->path);s.outputAuto=false;set_text(s.status,L"输出目录已更新。");}
 else update_output_folder(s,result->path);
}
inline void finish_scan(State&s,std::uint64_t generation,std::unique_ptr<ScanResult>result){
 if(generation!=s.scanGeneration)return;s.scanning=false;EnableWindow(s.folderBrowse,!s.folderDialogBusy&&!s.busy);EnableWindow(s.outputBrowse,!s.folderDialogBusy&&!s.busy);EnableWindow(s.manual,!s.folderDialogBusy&&!s.busy);EnableWindow(s.exportAll,!s.busy&&!s.items.empty());EnableWindow(s.exportSelected,!s.busy&&!s.items.empty());
 if(!result->error.empty()){s.pendingManualFiles.clear();set_text(s.status,L"扫描失败："+result->error);append_log(s,L"扫描失败："+result->error);return;}
 s.items=std::move(result->groups);std::optional<std::size_t> selected;
 if(!s.initialGroup.empty())for(std::size_t i=0;i<s.items.size();i++)if(_wcsicmp(s.items[i].key.c_str(),s.initialGroup.c_str())==0){selected=i;break;}
 if(!selected&&s.initialGroup.empty()&&!s.items.empty())selected=0;refresh_groups(s,selected);
 std::wstring message=L"扫描完成：PNG "+std::to_wstring(result->pngCount)+L" 张，动画组 "+std::to_wstring(s.items.size());
 if(!s.initialGroup.empty()&&!selected)message+=L"；未找到指定动画组 "+s.initialGroup;
 if(result->singletons)message+=L"，单帧文件 "+std::to_wstring(result->singletons)+L" 组已跳过";if(result->unmatched)message+=L"，非标准命名 "+std::to_wstring(result->unmatched)+L" 张未分组";if(result->invalid)message+=L"，损坏/受限文件 "+std::to_wstring(result->invalid)+L" 张跳过";
 set_text(s.status,message);append_log(s,message);if(!result->firstError.empty())append_log(s,L"首个跳过原因："+result->firstError);
 if(!s.items.empty()){EnableWindow(s.exportSelected,!s.busy);EnableWindow(s.exportAll,!s.busy);}
 auto manualFiles=std::move(s.pendingManualFiles);s.pendingManualFiles.clear();if(!manualFiles.empty())add_manual_group(s,manualFiles);
}
inline void layout_columns(State&s){LVCOLUMNW c{};c.mask=LVCF_TEXT|LVCF_WIDTH|LVCF_SUBITEM;const wchar_t*names[]={L"动画组",L"帧数",L"画布",L"状态"};int widths[]={260,55,100,78};for(int i=0;i<4;i++){c.pszText=(LPWSTR)names[i];c.cx=sx(s.window,widths[i]);c.iSubItem=i;ListView_InsertColumn(s.groups,i,&c);}ListView_SetExtendedListViewStyle(s.groups,LVS_EX_FULLROWSELECT|LVS_EX_GRIDLINES|LVS_EX_DOUBLEBUFFER);}
inline LRESULT CALLBACK window_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
 auto*s=(State*)GetWindowLongPtrW(w,GWLP_USERDATA);if(message==WM_NCCREATE){auto*cs=(CREATESTRUCTW*)lp;s=(State*)cs->lpCreateParams;SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)s);s->window=w;}
 if(!s)return DefWindowProcW(w,message,wp,lp);
 try{
  switch(message){
   case WM_CREATE:{
    DragAcceptFiles(w,TRUE);
    control(*s,L"STATIC",L"帧文件夹",SS_LEFT,3201);s->folder=control(*s,L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,IDC_FOLDER,WS_EX_CLIENTEDGE);s->folderBrowse=control(*s,L"BUTTON",L"选择文件夹并扫描…",BS_PUSHBUTTON,IDC_FOLDER_BROWSE);
    control(*s,L"STATIC",L"输出目录",SS_LEFT,3202);s->output=control(*s,L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,IDC_OUTPUT,WS_EX_CLIENTEDGE);s->outputBrowse=control(*s,L"BUTTON",L"选择输出目录…",BS_PUSHBUTTON,IDC_OUTPUT_BROWSE);
    control(*s,L"STATIC",L"按 def_<名称>_<组>_<帧>__<SHA>.png 自动分组。选择左侧组即可实时播放；只预览不会导出，也不会改动源 PNG。",SS_LEFT,3203);
    s->groups=control(*s,WC_LISTVIEWW,L"",LVS_REPORT|LVS_SINGLESEL|LVS_SHOWSELALWAYS|WS_BORDER,IDC_GROUPS,WS_EX_CLIENTEDGE);SetWindowSubclass(s->groups,group_context_forward,2,(DWORD_PTR)w);layout_columns(*s);
    s->preview=control(*s,L"STATIC",L"",SS_OWNERDRAW|WS_BORDER,IDC_PREVIEW,WS_EX_CLIENTEDGE);s->framePrev=control(*s,L"BUTTON",L"上一帧",BS_PUSHBUTTON,IDC_FRAME_PREV);s->play=control(*s,L"BUTTON",L"播放",BS_PUSHBUTTON,IDC_PLAY);s->frameNext=control(*s,L"BUTTON",L"下一帧",BS_PUSHBUTTON,IDC_FRAME_NEXT);s->frameLabel=control(*s,L"STATIC",L"无预览",SS_LEFT,IDC_FRAME_LABEL);
    s->fileLabel=control(*s,L"STATIC",L"文件：无预览",SS_LEFT,IDC_FILE_LABEL);control(*s,L"STATIC",L"预览倍率",SS_LEFT,3205);
    s->scaleMode=control(*s,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL|WS_TABSTOP,IDC_SCALE_MODE);
    SendMessageW(s->scaleMode,CB_ADDSTRING,0,(LPARAM)L"逐帧适配窗口");SendMessageW(s->scaleMode,CB_ADDSTRING,0,(LPARAM)L"整组同倍率（PNG像素比例）");SendMessageW(s->scaleMode,CB_SETCURSEL,1,0);
    s->manual=control(*s,L"BUTTON",L"手动多选 PNG…",BS_PUSHBUTTON,IDC_MANUAL);control(*s,L"STATIC",L"FPS",SS_LEFT,3204);s->fps=control(*s,L"EDIT",L"8",WS_BORDER|ES_NUMBER|ES_CENTER,IDC_FPS,WS_EX_CLIENTEDGE);s->loop=control(*s,L"BUTTON",L"循环播放",BS_AUTOCHECKBOX|WS_TABSTOP,IDC_LOOP);SendMessageW(s->loop,BM_SETCHECK,BST_CHECKED,0);
    s->exportSelected=control(*s,L"BUTTON",L"导出选中",BS_PUSHBUTTON,IDC_EXPORT_SELECTED);s->exportAll=control(*s,L"BUTTON",L"导出全部",BS_DEFPUSHBUTTON,IDC_EXPORT_ALL);s->close=control(*s,L"BUTTON",L"关闭",BS_PUSHBUTTON,IDC_CLOSE);
    s->status=control(*s,L"STATIC",L"拖入一个文件夹即可扫描其中所有同名动画帧。",SS_LEFT,IDC_STATUS);s->log=control(*s,L"EDIT",L"",WS_BORDER|ES_MULTILINE|ES_AUTOVSCROLL|ES_READONLY|WS_VSCROLL,IDC_LOG,WS_EX_CLIENTEDGE);SendMessageW(s->log,EM_SETLIMITTEXT,1024*1024,0);layout(*s);return 0;
   }
   case WM_SIZE:layout(*s);return 0;
   case WM_DPICHANGED:{auto*r=(RECT*)lp;SetWindowPos(w,nullptr,r->left,r->top,r->right-r->left,r->bottom-r->top,SWP_NOZORDER|SWP_NOACTIVATE);layout(*s);return 0;}
   case WM_FOLDER_PICK_DONE:{std::unique_ptr<FolderPickResult>result((FolderPickResult*)lp);finish_folder_dialog(*s,std::move(result));return 0;}
   case WM_SCAN_DONE:{std::unique_ptr<ScanResult>result((ScanResult*)lp);finish_scan(*s,(std::uint64_t)wp,std::move(result));return 0;}
   case WM_PREVIEW_DONE:{
    std::unique_ptr<PreviewLoadResult>result((PreviewLoadResult*)lp);
    if(result->generation!=s->previewGeneration||result->groupIndex>=s->items.size()||s->previewGroup!=(int)result->groupIndex){destroy_preview_frames(result->frames);return 0;}
    s->previewLoading=false;
    if(!result->error.empty()){destroy_preview_frames(result->frames);s->previewGroup=-1;set_text(s->frameLabel,L"预览失败");set_text(s->status,L"预览失败："+result->error);append_log(*s,L"预览失败："+result->error);EnableWindow(s->framePrev,FALSE);EnableWindow(s->play,FALSE);EnableWindow(s->frameNext,FALSE);return 0;}
    s->previewFrames=std::move(result->frames);s->currentFrame=0;auto key=s->items[result->groupIndex].key;set_text(s->status,L"正在预览："+key+L"（不会导出或修改源 PNG）");EnableWindow(s->framePrev,!s->busy);EnableWindow(s->play,!s->busy);EnableWindow(s->frameNext,!s->busy);start_preview_playback(*s,false);return 0;
   }
   case WM_DROPFILES:{HDROP drop=(HDROP)wp;UINT count=DragQueryFileW(drop,0xffffffff,nullptr,0);std::vector<fs::path>paths;for(UINT i=0;i<count;i++){UINT len=DragQueryFileW(drop,i,nullptr,0);std::wstring value(len+1,L'\0');DragQueryFileW(drop,i,value.data(),len+1);value.resize(len);paths.emplace_back(value);}DragFinish(drop);if(!paths.empty())add_file_paths(*s,paths);return 0;}
   case WM_COMMAND:{int id=LOWORD(wp),code=HIWORD(wp);if(id==IDC_SCALE_MODE&&code==CBN_SELCHANGE){s->groupScale=SendMessageW(s->scaleMode,CB_GETCURSEL,0,0)==1;update_preview_file_label(*s);InvalidateRect(s->preview,nullptr,TRUE);return 0;}if(code==EN_CHANGE&&id==IDC_OUTPUT&&!s->updatingOutput)s->outputAuto=false;if(code==EN_CHANGE&&id==IDC_FPS&&s->playing){try{auto n=std::stoi(text(s->fps));if(n>=1&&n<=60)SetTimer(w,PREVIEW_TIMER,(UINT)std::max(16,1000/n),nullptr);}catch(...){}}if(code!=BN_CLICKED&&code!=EN_CHANGE)return 0;
    if(id==IDC_FOLDER_BROWSE)start_folder_picker(*s,false);
    else if(id==IDC_OUTPUT_BROWSE)start_folder_picker(*s,true);
    else if(id==IDC_MANUAL){auto paths=choose_png_files(w);if(!paths.empty())add_file_paths(*s,paths);}
    else if(id==IDC_EXPORT_SELECTED)start_export(*s,false);
    else if(id==IDC_EXPORT_ALL)start_export(*s,true);
    else if(id==IDC_CLOSE){if(s->busy){MessageBoxW(w,L"APNG 正在导出，请等待完成后再关闭。",L"H3TierExport",MB_OK|MB_ICONINFORMATION);return 0;}SendMessageW(w,WM_CLOSE,0,0);}
    else if(id==IDC_PLAY){if(s->previewFrames.empty()||s->previewLoading)return 0;if(s->playing){KillTimer(w,PREVIEW_TIMER);s->playing=false;set_text(s->play,L"播放");}else start_preview_playback(*s,true);}
    else if(id==IDC_FRAME_PREV||id==IDC_FRAME_NEXT){if(!s->previewFrames.empty()&&!s->previewLoading){if(s->playing){KillTimer(w,PREVIEW_TIMER);s->playing=false;set_text(s->play,L"播放");}auto n=s->previewFrames.size();if(id==IDC_FRAME_PREV)s->currentFrame=(s->currentFrame+n-1)%n;else s->currentFrame=(s->currentFrame+1)%n;set_text(s->frameLabel,L"帧 "+std::to_wstring(s->currentFrame+1)+L" / "+std::to_wstring(n));update_preview_file_label(*s);InvalidateRect(s->preview,nullptr,TRUE);}}
    return 0;
   }
   case WM_CONTEXTMENU:if((HWND)wp==s->groups){show_group_context_menu(*s,lp);return 0;}break;
   case WM_NOTIFY:{auto*n=(NMHDR*)lp;if(n->idFrom==IDC_GROUPS&&n->code==LVN_ITEMCHANGED&&!s->busy&&!s->scanning){auto*change=(NMLISTVIEW*)lp;if((change->uNewState&LVIS_SELECTED)&&!(change->uOldState&LVIS_SELECTED))load_group_preview(*s,(size_t)change->iItem);}return 0;}
   case WM_DRAWITEM:{auto*d=(DRAWITEMSTRUCT*)lp;if(d->CtlID==IDC_PREVIEW){draw_preview(*s,d);return TRUE;}break;}
   case WM_TIMER:if(wp==PREVIEW_TIMER&&s->playing&&!s->previewFrames.empty()){if(s->currentFrame+1<s->previewFrames.size())s->currentFrame++;else if(SendMessageW(s->loop,BM_GETCHECK,0,0)==BST_CHECKED)s->currentFrame=0;else{s->playing=false;KillTimer(w,PREVIEW_TIMER);set_text(s->play,L"播放");}set_text(s->frameLabel,L"帧 "+std::to_wstring(s->currentFrame+1)+L" / "+std::to_wstring(s->previewFrames.size()));update_preview_file_label(*s);InvalidateRect(s->preview,nullptr,FALSE);return 0;}break;
   case WM_EXPORT_PROGRESS:{std::unique_ptr<std::wstring>progressText((std::wstring*)lp);set_text(s->status,*progressText);append_log(*s,*progressText);return 0;}
   case WM_EXPORT_DONE:{std::unique_ptr<ExportResult>result((ExportResult*)lp);set_busy(*s,false);for(const auto&line:result->lines)append_log(*s,line);set_text(s->status,L"导出完成：成功 "+std::to_wstring(result->succeeded)+L" / "+std::to_wstring(result->lines.size()));return 0;}
   case WM_CLOSE:if(s->busy||s->folderDialogBusy){MessageBoxW(w,L"APNG 正在导出或打开系统文件夹选择框，请完成后再关闭。",L"H3TierExport",MB_OK|MB_ICONINFORMATION);return 0;}s->done=true;DestroyWindow(w);return 0;
   case WM_DESTROY:clear_preview(*s);return 0;
  }
 }catch(const std::exception&e){auto errorText=wide(e.what());set_text(s->status,L"错误："+errorText);append_log(*s,L"错误："+errorText);MessageBoxW(w,errorText.c_str(),L"H3TierExport - APNG",MB_OK|MB_ICONERROR);return 0;}
 return DefWindowProcW(w,message,wp,lp);
}

inline void register_class(){
 static std::once_flag once;std::call_once(once,[]{WNDCLASSW c{};c.lpfnWndProc=window_proc;c.hInstance=GetModuleHandleW(nullptr);c.hCursor=LoadCursorW(nullptr,IDC_ARROW);c.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);c.lpszClassName=WINDOW_CLASS;need(RegisterClassW(&c)!=0,"register APNG window");});
}
inline Json selftest(){
 std::vector<std::pair<std::uint32_t,std::uint32_t>> canvases={{100,100},{200,100}};
 need(apngscale::factor(canvases,0,true,224,224)==apngscale::factor(canvases,1,true,224,224),"group preview must retain one pixel scale across unequal canvases");
 need(apngscale::factor(canvases,0,false,224,224)>apngscale::factor(canvases,1,false,224,224),"per-frame fitting remains an explicit separate mode");
 need(preview_filename_text(fs::path(L"CENCH_12_8.png")).find(L"CENCH_12_8.png")!=std::wstring::npos,"display exact current filename");
 need(preview_filename_text(fs::path(L"CENCH_12_8.png")).find(L"CENCH.DEF")!=std::wstring::npos,"display corresponding DEF filename");
 auto official=official_frame_key(fs::path(L"CBOAR_5_3.png"));need(official&&official->first==L"CBOAR_5"&&official->second==3,"official creature slot filename parsing");
 auto legacy=official_frame_key(fs::path(L"def_CBOAR_5_3__9515be1ca05b.png"));need(legacy&&legacy->first==L"CBOAR_5"&&legacy->second==3,"legacy creature slot filename compatibility");
 auto lodName=official_frame_key(fs::path(L"CBOAR.DEF_5_3__9515be1ca05b.png"));need(lodName&&lodName->first==L"CBOAR_5"&&lodName->second==3,"official DEF replacement filename parsing");
 register_class();State s;auto w=CreateWindowExW(0,WINDOW_CLASS,L"APNG regression",WS_POPUP,0,0,1160,760,nullptr,nullptr,GetModuleHandleW(nullptr),&s);need(w!=nullptr,"APNG regression window");
 try{
  need(s.groupScale&&SendMessageW(s.scaleMode,CB_GETCURSEL,0,0)==1,"common scale must be the default");
  SendMessageW(s.scaleMode,CB_SETCURSEL,0,0);SendMessageW(w,WM_COMMAND,MAKEWPARAM(IDC_SCALE_MODE,CBN_SELCHANGE),0);need(!s.groupScale,"actual scale selector must switch to per-frame fit");
  SendMessageW(s.scaleMode,CB_SETCURSEL,1,0);SendMessageW(w,WM_COMMAND,MAKEWPARAM(IDC_SCALE_MODE,CBN_SELCHANGE),0);need(s.groupScale,"actual scale selector must restore common group factor");
  s.previewFrames.resize(20);SendMessageW(s.loop,BM_SETCHECK,BST_UNCHECKED,0);start_preview_playback(s,false);
  for(int i=0;i<20;i++)SendMessageW(w,WM_TIMER,PREVIEW_TIMER,0);
  need(s.currentFrame==19&&!s.playing,"one-shot must stop on 20/20");SendMessageW(w,WM_COMMAND,MAKEWPARAM(IDC_PLAY,BN_CLICKED),0);
  need(s.currentFrame==0&&s.playing&&text(s.frameLabel)==L"帧 1 / 20","Play must restart a completed sequence");clear_preview(s);
  AnimationGroup manual;manual.key=L"手动序列 1";manual.manual=true;s.items.push_back(manual);refresh_groups(s);delete_manual_group(s,0);need(s.items.empty()&&s.previewFrames.empty(),"manual removal must clear sequence and preview");
  std::wstring pickerError;std::thread sta([&]{bool initialized=false;try{hr(CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED),"STA picker initialize");initialized=true;Com<IFileOpenDialog> dialog;hr(CoCreateInstance(CLSID_FileOpenDialog,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(&dialog)),"STA picker creation");}catch(const std::exception&e){pickerError=wide(e.what());}if(initialized)CoUninitialize();});sta.join();need(pickerError.empty(),"STA folder picker factory failed: "+utf8(pickerError));
  DestroyWindow(w);return{{"passed",true},{"official_creature_slot_filename",true},{"legacy_filename_compatibility",true},{"official_def_replacement_filename",true},{"group_common_scale_switch",true},{"exact_current_def_filename",true},{"one_shot_replay",true},{"manual_sequence_removal",true},{"sta_folder_picker_factory",true},{"actual_folder_selection_tested",false},{"source_png_writes",0}};
 }catch(...){DestroyWindow(w);throw;}
}
inline void run(HWND owner,HFONT font,const fs::path&initialFolder={},const std::wstring&initialGroup={},int initialFps=8){
 INITCOMMONCONTROLSEX controls{sizeof(controls),ICC_LISTVIEW_CLASSES|ICC_STANDARD_CLASSES};InitCommonControlsEx(&controls);
 register_class();
 State s;s.owner=owner;s.font=font;s.initialGroup=initialGroup;auto w=CreateWindowExW(WS_EX_DLGMODALFRAME|WS_EX_CONTROLPARENT,WINDOW_CLASS,L"H3TierExport - 文件夹动画预览与 APNG 打包",WS_CAPTION|WS_SYSMENU|WS_POPUP|WS_CLIPCHILDREN,CW_USEDEFAULT,CW_USEDEFAULT,1160,760,owner,nullptr,GetModuleHandleW(nullptr),&s);need(w!=nullptr,"create APNG window");
 set_text(s.fps,std::to_wstring(std::clamp(initialFps,1,60)));
 RECT bounds{};GetWindowRect(w,&bounds);HMONITOR monitor=MonitorFromWindow(owner?owner:w,MONITOR_DEFAULTTONEAREST);MONITORINFO mi{sizeof(mi)};if(GetMonitorInfoW(monitor,&mi)){int width=bounds.right-bounds.left,height=bounds.bottom-bounds.top;SetWindowPos(w,nullptr,mi.rcWork.left+(mi.rcWork.right-mi.rcWork.left-width)/2,mi.rcWork.top+(mi.rcWork.bottom-mi.rcWork.top-height)/2,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE);}
 ShowWindow(w,SW_SHOW);UpdateWindow(w);if(owner)EnableWindow(owner,FALSE);if(!initialFolder.empty())update_output_folder(s,initialFolder.wstring());MSG msg{};
 while(!s.done){auto code=GetMessageW(&msg,nullptr,0,0);if(code<=0){s.done=true;if(IsWindow(w))DestroyWindow(w);if(!code)PostQuitMessage((int)msg.wParam);break;}if(!IsDialogMessageW(w,&msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}}
 if(owner){EnableWindow(owner,TRUE);SetActiveWindow(owner);}
}

} // namespace h3tier::apngui
