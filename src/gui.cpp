#include "gui.hpp"
#include "resource.h"
#include "master_expand.hpp"
#include "motion.hpp"
#include "companion_cli.hpp"
#include "apng_gui.hpp"
#include "checkerboard_gui.hpp"
#include <commctrl.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlobj.h>
#include <optional>
#include <map>
#include <iterator>
#include <sstream>

#pragma comment(lib,"comctl32.lib")
#pragma comment(lib,"comdlg32.lib")
#pragma comment(lib,"shell32.lib")
#pragma comment(lib,"gdi32.lib")

namespace h3tier {
namespace {

constexpr wchar_t APP_CLASS[] = L"H3TierExport.MainWindow";
constexpr wchar_t APP_TITLE[] = L"H3TierExport - 高清素材与动画制作工作台";
constexpr wchar_t CAPABILITIES_TITLE[] = L"当前能力：高清导出 · 道路/接口批检 · 动作推演 · 批量任务  |  视频制作：规划中";
constexpr UINT WM_ASYNC_RESULT = WM_APP + 42;

enum : int {
 IDC_PATH=1001, IDC_BROWSE, IDC_CREATE_PLAN, IDC_CHECK, IDC_APPROVE, IDC_OPEN_MASTER,
 IDC_RUN, IDC_VERIFY, IDC_SELFTEST, IDC_OPEN_FOLDER,
 IDC_BACKEND, IDC_FILTER, IDC_WORKERS, IDC_MEMORY,
 IDC_LIST, IDC_STATUS, IDC_LOG, IDC_PROGRESS, IDC_EXPAND, IDC_MOTION, IDC_TASK_CENTER, IDC_CAPABILITIES, IDC_APNG, IDC_CHECKERBOARD
};

enum : int {
 IDC_TASK_KIND=2201, IDC_TASK_STAGE, IDC_TASK_INPUT, IDC_TASK_BROWSE_INPUT,
 IDC_TASK_SHA, IDC_TASK_OUTPUT, IDC_TASK_BROWSE_OUTPUT, IDC_TASK_OPERATION,
 IDC_TASK_NOTE, IDC_TASK_START, IDC_TASK_CANCEL, IDC_TASK_OPEN_OUTPUT, IDC_TASK_QUALITY_PROFILE
};

struct AsyncResult {
 std::wstring title;
 std::wstring body;
 bool ok=false;
 bool showError=true;
 fs::path reportDirectory;
};

struct AppState {
 HINSTANCE instance{};
 HWND window{},path{},browse{},createPlan{},check{},approve{},openMaster{},run{},verify{},selftest{},openFolder{},taskCenter{};
 HWND backend{},filter{},workers{},memory{},list{},status{},log{},progress{},expand{},motion{},apng{},checkerboard{},capabilities{};
 HFONT font{};
 fs::path currentPath;
 Json current;
 bool isPlan=false;
 bool isBatch=false;
 bool isExpansion=false;
 bool isMotion=false;
 bool isMotionSpec=false;
 bool busy=false;
 bool smoke=false;
 fs::path lastReportDirectory;
};

std::wstring wtext(HWND h){
 int n=GetWindowTextLengthW(h);
 std::wstring s((size_t)std::max(0,n)+1,L'\0');
 if(n) GetWindowTextW(h,s.data(),n+1);
 s.resize((size_t)std::max(0,n));return s;
}
void settext(HWND h,const std::wstring&s){SetWindowTextW(h,s.c_str());}
void font(HWND h,HFONT f){SendMessageW(h,WM_SETFONT,(WPARAM)f,TRUE);}
std::wstring jsonw(const Json&j){return wide(j.dump(2));}

int dpi(HWND w){auto p=(decltype(&GetDpiForWindow))GetProcAddress(GetModuleHandleW(L"user32.dll"),"GetDpiForWindow");return p?(int)p(w):96;}
int sx(HWND w,int v){return MulDiv(v,dpi(w),96);}

// Position by monitor work area (not the virtual desktop origin): the first
// window follows the cursor monitor; owned dialogs follow the parent monitor.
// No mouse movement or global keyboard-focus changes are made here.
RECT window_work_area(HWND owner){
 HMONITOR monitor{};
 if(owner&&IsWindow(owner))monitor=MonitorFromWindow(owner,MONITOR_DEFAULTTONEAREST);
 else{
  POINT cursor{};
  monitor=GetCursorPos(&cursor)?MonitorFromPoint(cursor,MONITOR_DEFAULTTONEAREST):
                                   MonitorFromPoint(POINT{0,0},MONITOR_DEFAULTTOPRIMARY);
 }
 MONITORINFO information{sizeof(information)};
 need(monitor&&GetMonitorInfoW(monitor,&information),"locate usable monitor work area");
 return information.rcWork;
}
POINT center_window_origin(HWND window,HWND owner){
 RECT windowBounds{};need(GetWindowRect(window,&windowBounds),"read window bounds for centering");
 const auto work=window_work_area(owner);
 const auto width=windowBounds.right-windowBounds.left,height=windowBounds.bottom-windowBounds.top;
 const auto workWidth=work.right-work.left,workHeight=work.bottom-work.top;
 return POINT{work.left+(width<=workWidth?(workWidth-width)/2:0),
              work.top+(height<=workHeight?(workHeight-height)/2:0)};
}
void center_window(HWND window,HWND owner){
 auto position=center_window_origin(window,owner);
 need(SetWindowPos(window,nullptr,position.x,position.y,0,0,
                   SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE),"center the new window");
}
bool window_is_centered(HWND window,HWND owner){
 RECT bounds{};need(GetWindowRect(window,&bounds),"read centered window bounds");
 auto expected=center_window_origin(window,owner);
 return std::abs(bounds.left-expected.x)<=1&&std::abs(bounds.top-expected.y)<=1;
}

std::wstring local_stamp(){
 SYSTEMTIME t{};GetLocalTime(&t);wchar_t b[32];
 swprintf_s(b,L"%04u%02u%02u-%02u%02u%02u",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond);
 return b;
}
fs::path unique_path(fs::path p){
 if(!fs::exists(p))return p;
 auto stem=p.stem().wstring();auto ext=p.extension().wstring();auto dir=p.parent_path();
 for(int i=2;i<1000;i++){auto q=dir/fs::path(stem+L"-"+std::to_wstring(i)+ext);if(!fs::exists(q))return q;}
 throw std::runtime_error("cannot allocate unique output filename");
}
void append_log(AppState&s,const std::wstring&line){
 if(!s.log)return;
 int n=GetWindowTextLengthW(s.log);
 SendMessageW(s.log,EM_SETSEL,n,n);
 std::wstring x=line+L"\r\n";
 SendMessageW(s.log,EM_REPLACESEL,FALSE,(LPARAM)x.c_str());
 SendMessageW(s.log,EM_SCROLLCARET,0,0);
}
void status(AppState&s,const std::wstring&line){settext(s.status,line);append_log(s,line);}

std::string combo_value(HWND h,const std::vector<std::string>&values){
 int i=(int)SendMessageW(h,CB_GETCURSEL,0,0);need(i>=0&&i<(int)values.size(),"invalid UI selection");return values[i];
}
int edit_int(HWND h,int low,int high,const char*name){
 auto s=utf8(wtext(h));size_t used=0;int n=std::stoi(s,&used);need(used==s.size()&&n>=low&&n<=high,std::string("invalid ")+name);return n;
}
Json ui_settings(AppState&s){
 auto d=default_settings();
 d["backend"]=combo_value(s.backend,{"auto","hybrid","cpu","gpu"});
 d["filter"]=combo_value(s.filter,{"h3-quality","lanczos3","mitchell","area"});
 d["workers"]=edit_int(s.workers,1,64,"workers");
 d["memory_gib"]=edit_int(s.memory,1,512,"memory GiB");
 return settings_checked(Json{{"settings",d}});
}
void set_busy(AppState&s,bool value){
 s.busy=value;
 for(HWND h:{s.browse,s.createPlan,s.check,s.approve,s.openMaster,s.run,s.verify,s.selftest,s.openFolder,s.expand,s.motion,s.taskCenter,s.apng,s.checkerboard})
  EnableWindow(h,!value);
 if(s.isExpansion){EnableWindow(s.approve,FALSE);EnableWindow(s.createPlan,FALSE);}
 if(s.isMotion||s.isMotionSpec){EnableWindow(s.approve,FALSE);EnableWindow(s.createPlan,!value&&s.isMotionSpec);if(s.isMotionSpec)for(HWND h:{s.check,s.run,s.verify})EnableWindow(h,FALSE);}
 SendMessageW(s.progress,PBM_SETMARQUEE,value?TRUE:FALSE,value?35:0);
 ShowWindow(s.progress,value?SW_SHOW:SW_HIDE);
 if(value)settext(s.status,L"正在处理…");
}

std::optional<fs::path> choose_json(HWND owner,const fs::path&current,const wchar_t*title=L"选择 batch JSON 或 H3TierExport plan"){
 wchar_t file[32768]{};
 if(!current.empty())wcsncpy_s(file,current.c_str(),_TRUNCATE);
 std::wstring init=current.empty()?L"":current.parent_path().wstring();
 OPENFILENAMEW of{sizeof(of)};of.hwndOwner=owner;of.lpstrFile=file;of.nMaxFile=32768;
 of.lpstrFilter=L"H3TierExport JSON (*.json)\0*.json\0All files\0*.*\0";
 of.lpstrInitialDir=init.empty()?nullptr:init.c_str();of.Flags=OFN_FILEMUSTEXIST|OFN_NOCHANGEDIR|OFN_EXPLORER;
 of.lpstrTitle=title;
 if(!GetOpenFileNameW(&of))return std::nullopt;
 return clean_path(fs::path(file));
}
std::optional<fs::path> choose_pose_input(HWND owner,const fs::path&current,const wchar_t*title=L"选择透明高清单帧或官方全槽冻结清单"){
 wchar_t file[32768]{};
 if(!current.empty())wcsncpy_s(file,current.c_str(),_TRUNCATE);
 std::wstring init=current.empty()?L"":current.parent_path().wstring();
 OPENFILENAMEW of{sizeof(of)};of.hwndOwner=owner;of.lpstrFile=file;of.nMaxFile=32768;
 of.lpstrFilter=L"PNG 图片或冻结 JSON (*.png;*.json)\0*.png;*.json\0PNG 图片 (*.png)\0*.png\0冻结 JSON (*.json)\0*.json\0";
 of.lpstrInitialDir=init.empty()?nullptr:init.c_str();of.Flags=OFN_FILEMUSTEXIST|OFN_NOCHANGEDIR|OFN_EXPLORER;
 of.lpstrTitle=title;
 if(!GetOpenFileNameW(&of))return std::nullopt;
 return clean_path(fs::path(file));
}
fs::path module_path(){
 std::wstring b(32768,L'\0');DWORD n=GetModuleFileNameW(nullptr,b.data(),(DWORD)b.size());need(n>0&&n<b.size(),"module path unavailable");b.resize(n);return fs::path(b);
}
fs::path default_workspace_root(){
 auto valid=[](const fs::path&root){
  return fs::is_directory(root/L"CompleteOfficialRelease-20261006-v1")||
         fs::is_directory(root/L"CompleteNativeBaseline")||
         (fs::is_regular_file(root/L"codex-map"/L"native-resource-inventory.json.gz")&&
          fs::is_directory(root/L"codex-高清母版")&&fs::is_directory(root/L"codex-3X"));
 };
 wchar_t overrideRoot[32768]{};DWORD overrideLength=GetEnvironmentVariableW(L"H3_TIER_EXPORT_H3_ROOT",overrideRoot,(DWORD)std::size(overrideRoot));
 if(overrideLength>0&&overrideLength<std::size(overrideRoot)){
   auto root=fs::path(std::wstring(overrideRoot,overrideLength));need(root.is_absolute(),"H3_TIER_EXPORT_H3_ROOT must be absolute");
   need(valid(root),"bound H3 workspace is unavailable");return clean_path(root.make_preferred());
 }
 auto n=module_path().parent_path();
 for(int i=0;i<6&&!n.empty();i++,n=n.parent_path()){
   auto config=n/L"H3TierExport-workspace.json";
   if(!fs::exists(config)&&_wcsicmp(n.filename().c_str(),L"H3TierExport")==0)config=n/L"resources"/L"H3TierExport-workspace.json";
   if(!fs::exists(config))continue;
   auto raw=read_all(config,64*1024);auto binding=parse_json(raw);
   need(binding.value("schema",std::string{})=="h3.tier-export.workspace.v1","invalid workspace binding schema");
   auto root=path(binding.at("h3_root").get<std::string>());need(root.is_absolute(),"workspace root must be absolute");
   need(valid(root),"bound H3 workspace is unavailable");
   return clean_path(root.make_preferred());
 }
 return {};
}
fs::path default_project_plan(){
 auto root=default_workspace_root();if(root.empty())return {};
 auto p=root/L"CompleteOfficialRelease-20261006-v1"/L"H3TierExport-plan.json";
 return fs::exists(p)?clean_path(p):fs::path{};
}
void clear_list(HWND list){ListView_DeleteAllItems(list);}
void add_row(HWND list,int row,const std::wstring&id,const std::wstring&state,const std::wstring&master,const std::wstring&views,LPARAM tag){
 LVITEMW item{};item.mask=LVIF_TEXT|LVIF_PARAM;item.iItem=row;item.pszText=(LPWSTR)id.c_str();item.lParam=tag;ListView_InsertItem(list,&item);
 ListView_SetItemText(list,row,1,(LPWSTR)state.c_str());ListView_SetItemText(list,row,2,(LPWSTR)master.c_str());ListView_SetItemText(list,row,3,(LPWSTR)views.c_str());
}
std::map<std::string,std::string> states_from_check(const Json&check){
 std::map<std::string,std::string> m;
 for(auto&i:check.at("items"))m[i.at("id").get<std::string>()]=i.at("status").get<std::string>();
 return m;
}

void refresh_list(AppState&s){
 clear_list(s.list);
 if(s.isMotion||s.isMotionSpec){
  std::map<std::string,std::string> states;if(s.isMotion){std::vector<MotionJob> jobs;states=states_from_check(check_motion(s.current,motion_settings(ui_settings(s)),jobs));}int row=0;
  for(size_t i=0;i<s.current.at("items").size();i++){auto& j=s.current["items"][i];auto id=j.at("id").get<std::string>();auto kind=j.value("mode","mesh");std::string input=j.contains("reuse_from")?"reuse: "+j.at("reuse_from").get<std::string>():kind=="reuse"?j.at("source").at("path").get<std::string>():j.at("layers").at(0).at("source_a").at("path").get<std::string>();add_row(s.list,row++,wide(id),s.isMotion?wide(states[id]):L"MOTION_SPEC",wide(input),wide(kind)+L" "+std::to_wstring(s.current.value("target_scale",3))+L"X",(LPARAM)i);}
  status(s,s.isMotion?L"动作候选：CPU网格变形／对齐插值。检查、派生、验证后逐帧看图；不会自动批准。":L"动作规格已载入：点击“生成计划”冻结原槽几何、图片SHA与控制点。不会改写原规格。");
 }else if(s.isExpansion){
  std::vector<ExpansionJob> jobs;auto checked=check_expansion(s.current,expansion_settings(ui_settings(s)),jobs);auto states=states_from_check(checked);int row=0;
  for(size_t i=0;i<s.current.at("items").size();i++){auto& j=s.current["items"][i];auto id=j.at("id").get<std::string>();add_row(s.list,row++,wide(id),wide(states[id]),wide(j.at("source")),std::to_wstring(j.at("target_scale").get<int>())+L"X",(LPARAM)i);}
  status(s,L"CPU母版扩展："+std::to_wstring(s.current.at("items").size())+L"张；等比例处理，原PNG保留。生成的母版须另行看图审核。");
 }else if(s.isPlan){
  Json checked;std::map<std::string,std::string> states;
  try{std::vector<Job>valid;checked=check_plan(s.current,ui_settings(s),valid);states=states_from_check(checked);}
  catch(const std::exception&e){append_log(s,L"检查计划失败: "+wide(e.what()));}
  int row=0;
  for(size_t i=0;i<s.current.at("jobs").size();i++){
   auto&j=s.current["jobs"][i];auto id=j.at("id").get<std::string>();
   auto st=states.count(id)?states[id]:(j.at("approval").value("approved",false)?"APPROVED":"PENDING");
   std::wstring master=wide(j.at("master").get<std::string>());std::wstring views=std::to_wstring(j.at("views").size());
   add_row(s.list,row++,wide(id),wide(st),master,views,(LPARAM)i);
  }
  auto reused=s.current.value("reuse",Json::array()).size();
  status(s,L"计划已载入：任务 "+std::to_wstring(s.current.at("jobs").size())+L"，跨批复用 "+std::to_wstring(reused)+L"；新计划默认只输出3X。旧三倍率计划仅复用完整回执和全部输出，缺损时零写入拒绝；GUI不提供旧档重放。");
 }else if(s.isBatch){
  int row=0;for(size_t i=0;i<s.current.at("items").size();i++){
   auto&x=s.current["items"][i];std::string id=x.value("id","(no id)");
   std::string master=x.value("output_master","");
   add_row(s.list,row++,wide(id),L"BATCH",wide(master),L"-",(LPARAM)i);
  }
  status(s,L"批次已载入："+std::to_wstring(s.current.at("items").size())+L" 项。新计划只使用 pcx3x_output_dir 并输出一套3X；点击“生成计划”可合并同目录 batch-001…004。");
 }else status(s,L"JSON 不是支持的 batch 或 H3TierExport plan。");
}
void load_document(AppState&s,const fs::path&p){
 auto data=read_json(p);
 s.currentPath=clean_path(p);s.current=std::move(data);
 s.lastReportDirectory.clear();settext(s.openFolder,L"打开目录");
 s.isPlan=s.current.value("schema","")=="h3.tier-export.plan.v1";
 s.isExpansion=s.current.value("schema","")=="h3.master-expand.batch.v1"||fullraw_batch(s.current);
 s.isMotion=s.current.value("schema","")=="h3.motion.batch.v1";s.isMotionSpec=s.current.value("schema","")=="h3.motion.spec.v1";
 s.isBatch=!s.isPlan&&!s.isExpansion&&!s.isMotion&&!s.isMotionSpec&&s.current.contains("items")&&s.current["items"].is_array();
 EnableWindow(s.approve,!s.isExpansion&&!s.isMotion&&!s.isMotionSpec);EnableWindow(s.createPlan,!s.isExpansion&&!s.isMotion);EnableWindow(s.backend,!s.isExpansion&&!s.isMotion&&!s.isMotionSpec);EnableWindow(s.filter,!s.isExpansion&&!s.isMotion&&!s.isMotionSpec);
 if(s.isPlan||s.isExpansion||s.isMotion||s.isMotionSpec){auto d=settings_checked(s.current);const std::vector<std::string> bs={"auto","hybrid","cpu","gpu"},fs={"h3-quality","lanczos3","mitchell","area"};SendMessageW(s.backend,CB_SETCURSEL,std::find(bs.begin(),bs.end(),d["backend"].get<std::string>())-bs.begin(),0);SendMessageW(s.filter,CB_SETCURSEL,std::find(fs.begin(),fs.end(),d["filter"].get<std::string>())-fs.begin(),0);}
 settext(s.run,s.isMotion?L"派生动作":s.isExpansion?L"扩级生成":L"导出已批准");
 settext(s.path,s.currentPath.wstring());
 set_busy(s,false);
 refresh_list(s);
}
int selected_index(AppState&s){
 int i=ListView_GetNextItem(s.list,-1,LVNI_SELECTED);if(i<0)return -1;LVITEMW v{};v.mask=LVIF_PARAM;v.iItem=i;need(ListView_GetItem(s.list,&v),"list selection read");return(int)v.lParam;
}
void open_master(AppState&s){
 need(s.isPlan||s.isExpansion||s.isMotion||s.isMotionSpec,"先打开计划");int idx=selected_index(s);need(idx>=0,"先选择一个任务");fs::path p;
 if(s.isMotion||s.isMotionSpec){auto j=s.current["items"][idx];if(s.isMotion){std::vector<MotionJob> jobs;check_motion(s.current,motion_settings(ui_settings(s)),jobs);for(auto& job:jobs)if(job.id==j.at("id").get<std::string>()&&fs::exists(job.output))p=job.output;}if(p.empty()){if(j.contains("reuse_from")){auto id=j.at("reuse_from");bool found=false;for(auto& owner:s.current.at("items"))if(owner.at("id")==id){j=owner;found=true;break;}need(found&&!j.contains("reuse_from"),"invalid motion reuse owner");}p=path(j.value("mode","mesh")=="reuse"?j.at("source").at("path").get<std::string>():j.at("layers").at(0).at("source_a").at("path").get<std::string>());}}
 else p=path(s.isExpansion?s.current["items"][idx].at("source").get<std::string>():s.current["jobs"][idx].at("master").get<std::string>());p=clean_path(p);need(fs::exists(p),"母版文件尚不存在");
 auto r=(INT_PTR)ShellExecuteW(s.window,L"open",p.c_str(),nullptr,p.parent_path().c_str(),SW_SHOWNORMAL);need(r>32,"无法打开母版");
}
void open_folder(AppState&s){
 fs::path p;
 if(!s.lastReportDirectory.empty())p=fs::is_directory(s.lastReportDirectory)?s.lastReportDirectory:s.lastReportDirectory.parent_path();
 else if((s.isPlan||s.isExpansion||s.isMotion||s.isMotionSpec)&&s.current.contains("output_root"))p=clean_path(path(s.current.at("output_root").get<std::string>()));
 else if(!s.currentPath.empty())p=s.currentPath.parent_path();
 else p=module_path().parent_path();
 auto r=(INT_PTR)ShellExecuteW(s.window,L"open",p.c_str(),nullptr,nullptr,SW_SHOWNORMAL);need(r>32,"无法打开目录");
}
void create_plan(AppState&s){
 if(s.isMotionSpec){auto batch=make_motion_batch(s.current);auto dest=unique_path(s.currentPath.parent_path()/(L"motion-batch-"+local_stamp()+L".json"));write_json_new(dest,batch);load_document(s,dest);return;}
 need(s.isBatch,"先打开 batch JSON");
 std::vector<fs::path>batches;auto dir=s.currentPath.parent_path();
 for(int n=1;n<=4;n++){wchar_t b[32];swprintf_s(b,L"batch-%03d.json",n);auto p=dir/b;if(fs::exists(p))batches.push_back(p);}
 if(batches.empty())batches.push_back(s.currentPath);
 auto dest=unique_path(dir/(L"H3TierExport-plan-ui-"+local_stamp()+L".json"));
 auto result=make_plan(batches,dest);append_log(s,jsonw(result));load_document(s,dest);
}
void approve_selected(AppState&s){
 need(s.isPlan,"先打开 plan");int idx=selected_index(s);need(idx>=0,"先选择一个任务");
 auto id=s.current["jobs"][idx].at("id").get<std::string>();
 auto master=wide(s.current["jobs"][idx].at("master").get<std::string>());
 std::wstring prompt=L"批准："+wide(id)+L"\r\n\r\n母版：\r\n"+master+
  L"\r\n\r\n这会绑定当前母版 SHA256 与 source/effective crop 合同。\r\n只有你已经亲自检查图像和裁片后才能继续。\r\n\r\n确认批准？";
 if(MessageBoxW(s.window,prompt.c_str(),L"H3TierExport - 用户批准",MB_YESNO|MB_ICONWARNING|MB_DEFBUTTON2)!=IDYES)return;
 std::string evidence="User explicitly approved in H3TierExport GUI after reviewing master/crops; asset="+id+"; recorded="+now();
 auto approved=seal_approval(s.current,id,evidence);
 auto out=unique_path(s.currentPath.parent_path()/(s.currentPath.stem().wstring()+L".approved-"+local_stamp()+L".json"));
 write_json_new(out,approved);append_log(s,L"已创建批准计划（未覆盖原文件）："+out.wstring());load_document(s,out);
}
void check_current(AppState&s){
 if(s.isMotion){std::vector<MotionJob> jobs;append_log(s,jsonw(check_motion(s.current,motion_settings(ui_settings(s)),jobs)));refresh_list(s);return;}
 if(s.isExpansion){std::vector<ExpansionJob> jobs;append_log(s,jsonw(check_expansion(s.current,expansion_settings(ui_settings(s)),jobs)));refresh_list(s);return;}
 need(s.isPlan,"先打开 plan");std::vector<Job>valid;auto r=check_plan(s.current,ui_settings(s),valid);append_log(s,jsonw(r));refresh_list(s);
}

std::optional<fs::path> choose_native_reference_root(HWND owner){
 wchar_t display[MAX_PATH]{};BROWSEINFOW info{};info.hwndOwner=owner;info.pszDisplayName=display;
 info.lpszTitle=L"选择与母版目录结构对应的官方原版 1X 帧目录";
 info.ulFlags=BIF_RETURNONLYFSDIRS|BIF_NEWDIALOGSTYLE;
 PIDLIST_ABSOLUTE item=SHBrowseForFolderW(&info);if(!item)return std::nullopt;
 wchar_t pathBuffer[MAX_PATH]{};BOOL ok=SHGetPathFromIDListW(item,pathBuffer);CoTaskMemFree(item);
 if(!ok)return std::nullopt;return clean_path(fs::path(pathBuffer));
}
std::optional<fs::path> choose_animation_roi_mask(HWND owner){
 wchar_t fileName[MAX_PATH]{};OPENFILENAMEW of{sizeof(of)};of.hwndOwner=owner;of.lpstrFile=fileName;of.nMaxFile=MAX_PATH;
 of.lpstrFilter=L"灰度动态区域 PNG (*.png)\0*.png\0";of.lpstrTitle=L"选择与高清母版画布同尺寸的动态效果 ROI mask";
 of.Flags=OFN_FILEMUSTEXIST|OFN_PATHMUSTEXIST|OFN_NOCHANGEDIR;
 if(!GetOpenFileNameW(&of)){need(CommDlgExtendedError()==0,"ROI mask selection failed");return std::nullopt;}
 return clean_path(fs::path(fileName));
}
struct ExpansionSetup {
 HWND window{},source{},target{},width{},height{},frameSetMode{},referenceRootEdit{},referenceBrowse{},roiMaskEdit{},roiMaskBrowse{};HFONT font{};
 fs::path referenceRoot,animationRoiMask;bool done=false,accepted=false;
 std::string animationContract,frameSetClassification=FRAME_SET_SINGLE_V1;
 int sourceTier=3,targetScale=3;std::array<int,2> base={300,300};
};
LRESULT CALLBACK expansion_setup_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
 auto* s=(ExpansionSetup*)GetWindowLongPtrW(w,GWLP_USERDATA);
 if(message==WM_NCCREATE){s=(ExpansionSetup*)((CREATESTRUCTW*)lp)->lpCreateParams;s->window=w;SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)s);}
 if(!s)return DefWindowProcW(w,message,wp,lp);
 if(message==WM_CREATE){
  auto control=[&](const wchar_t* klass,const wchar_t* text,DWORD style,int x,int y,int width,int height,int id){auto c=CreateWindowW(klass,text,WS_CHILD|WS_VISIBLE|style,x,y,width,height,w,(HMENU)(INT_PTR)id,GetModuleHandleW(nullptr),nullptr);need(c!=nullptr,"expansion setup control");font(c,s->font);return c;};
  control(L"STATIC",L"当前PNG的倍率",0,20,20,145,26,2001);s->source=control(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST,175,16,380,130,201);
  for(auto text:{L"标准3X素材",L"标准2X素材",L"生成原图：指定原版1X宽高"})SendMessageW(s->source,CB_ADDSTRING,0,(LPARAM)text);SendMessageW(s->source,CB_SETCURSEL,0,0);
  control(L"STATIC",L"制作母版",0,20,62,145,26,2002);s->target=control(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST,175,58,160,120,202);
  for(auto text:{L"标准3X（生产规格）",L"标准4X（历史兼容）"})SendMessageW(s->target,CB_ADDSTRING,0,(LPARAM)text);SendMessageW(s->target,CB_SETCURSEL,0,0);
  control(L"STATIC",L"原版1X宽 / 高",0,20,106,145,26,2003);s->width=control(L"EDIT",L"300",WS_BORDER|ES_NUMBER,175,102,100,26,203);s->height=control(L"EDIT",L"300",WS_BORDER|ES_NUMBER,295,102,100,26,204);EnableWindow(s->width,FALSE);EnableWindow(s->height,FALSE);
  control(L"STATIC",L"同组图片共用原版宽高。CPU等比例处理，最大扩至输入边长的2倍。\r\n保留原PNG，输出到程序目录的新文件夹；新母版需要看图审核。",0,20,148,600,52,2004);
  s->frameSetMode=control(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST,20,210,670,120,207);
  for(auto text:{L"单张 / 互不相关的帧（检测到同组多帧会阻断）",
                 L"完整动画：静态结构 + 动态效果（v2 几何门）",
                 L"VCMI BASE：frame 0 底图 + 当前帧叠层（官方稳定区和逐帧特效尺寸门）",
                 L"独立静态帧目录：完整官方帧集（允许单帧组）+95%门",
                 L"VCMI BASE：固定底图 + 自定义动态区域（旧合同）",
                 L"完整帧：用户指定动态 ROI（静态结构独立核验）",
                 L"VCMI BASE：用户指定动态 ROI（结构区剔除官方旧特效）",
                 L"完整帧：用户指定动态 ROI（按房体结构注册，完整帧保留）"})SendMessageW(s->frameSetMode,CB_ADDSTRING,0,(LPARAM)text);
  SendMessageW(s->frameSetMode,CB_SETCURSEL,0,0);
  control(L"STATIC",L"官方原版 1X 帧目录",0,20,248,145,26,2005);
  s->referenceRootEdit=control(L"EDIT",L"",ES_AUTOHSCROLL|WS_BORDER,175,244,395,26,208);
  s->referenceBrowse=control(L"BUTTON",L"浏览...",BS_PUSHBUTTON,580,242,90,30,209);
  control(L"STATIC",L"动态 FX 区域 mask PNG",0,20,291,145,26,2011);
  s->roiMaskEdit=control(L"EDIT",L"",ES_AUTOHSCROLL|WS_BORDER,175,287,395,26,210);
  s->roiMaskBrowse=control(L"BUTTON",L"浏览...",BS_PUSHBUTTON,580,285,90,30,211);
  control(L"STATIC",L"动画目录须有完整槽位；ROI 白色区可动，区外底图必须稳定。",0,175,319,520,22,2010);
  control(L"BUTTON",L"创建扩展批次",BS_DEFPUSHBUTTON,390,360,150,32,205);control(L"BUTTON",L"取消",BS_PUSHBUTTON,555,360,85,32,206);return 0;
 }
 if(message==WM_COMMAND){
  if(LOWORD(wp)==201){bool custom=SendMessageW(s->source,CB_GETCURSEL,0,0)==2;EnableWindow(s->width,custom);EnableWindow(s->height,custom);return 0;}
  if(LOWORD(wp)==209){auto chosen=choose_native_reference_root(w);if(chosen)settext(s->referenceRootEdit,chosen->wstring());return 0;}
  if(LOWORD(wp)==211){auto chosen=choose_animation_roi_mask(w);if(chosen)settext(s->roiMaskEdit,chosen->wstring());return 0;}
  if(LOWORD(wp)==205){try{
   int source=(int)SendMessageW(s->source,CB_GETCURSEL,0,0);s->sourceTier=source==0?3:source==1?2:0;
   s->targetScale=SendMessageW(s->target,CB_GETCURSEL,0,0)==0?3:4;
   if(!s->sourceTier)s->base={edit_int(s->width,1,MAX_AXIS,"original1X width"),edit_int(s->height,1,MAX_AXIS,"original1X height")};
   int mode=(int)SendMessageW(s->frameSetMode,CB_GETCURSEL,0,0);
   s->animationContract.clear();s->frameSetClassification.clear();
   if(mode==0)s->frameSetClassification=FRAME_SET_SINGLE_V1;
   else if(mode==1)s->animationContract=ANIMATION_CONTRACT_V2;
   else if(mode==2)s->animationContract=ANIMATION_CONTRACT_BASE_OVERLAY_V2;
   else if(mode==3)s->frameSetClassification=FRAME_SET_STATIC_CATALOG_V1;
   else if(mode==4)s->animationContract=ANIMATION_CONTRACT_BASE_AUTHORED_FX_V1;
   else if(mode==5)s->animationContract=ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V1;
   else if(mode==6)s->animationContract=ANIMATION_CONTRACT_BASE_AUTHORED_FX_V2;
   else if(mode==7)s->animationContract=ANIMATION_CONTRACT_COMPOSITE_AUTHORED_FX_V2;
   else need(false,"select a frame set type");
   if(mode!=0){
    auto value=wtext(s->referenceRootEdit);need(!value.empty(),"所选帧集门禁需要官方原版 1X 帧目录");
    s->referenceRoot=clean_path(fs::path(value));need(fs::is_directory(s->referenceRoot),"官方原版 1X 帧目录不存在");
   }
   if(mode==4||mode==5||mode==6||mode==7){
    auto roiValue=wtext(s->roiMaskEdit);need(!roiValue.empty(),"自定义动态区域门禁需要 ROI mask PNG");
    s->animationRoiMask=clean_path(fs::path(roiValue));need(fs::is_regular_file(s->animationRoiMask),"动态效果 ROI mask 文件不存在");
    need(s->animationRoiMask.extension()==L".png","动态效果 ROI mask 必须是 PNG");
   }
   if(mode==3)need(s->targetScale==3,"独立静态帧目录目前只允许制作正式 3X");
   s->accepted=true;SendMessageW(w,WM_CLOSE,0,0);
  }catch(const std::exception& e){MessageBoxW(w,wide(e.what()).c_str(),L"请检查扩展几何/动画门禁",MB_OK|MB_ICONERROR);}return 0;}
  if(LOWORD(wp)==206){SendMessageW(w,WM_CLOSE,0,0);return 0;}
 }
 if(message==WM_CLOSE){s->done=true;DestroyWindow(w);return 0;}
 return DefWindowProcW(w,message,wp,lp);
}
HWND create_expansion_setup(HWND owner,ExpansionSetup& setup,bool show){
 static std::once_flag once;std::call_once(once,[]{WNDCLASSW c{};c.lpfnWndProc=expansion_setup_proc;c.hInstance=GetModuleHandleW(nullptr);c.hCursor=LoadCursorW(nullptr,IDC_ARROW);c.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);c.lpszClassName=L"H3TierExport.ExpansionSetup";need(RegisterClassW(&c)!=0,"register expansion setup");});
  auto w=CreateWindowExW(WS_EX_DLGMODALFRAME,L"H3TierExport.ExpansionSetup",L"母版扩展设置",WS_CAPTION|WS_SYSMENU|WS_POPUP,CW_USEDEFAULT,CW_USEDEFAULT,760,440,owner,nullptr,GetModuleHandleW(nullptr),&setup);need(w!=nullptr,"create expansion setup");center_window(w,owner);if(show){ShowWindow(w,SW_SHOW);SetFocus(setup.source);}return w;
}
void create_expansion(AppState& s){
 std::vector<wchar_t> buffer(65536);OPENFILENAMEW of{sizeof(of)};of.hwndOwner=s.window;of.lpstrFile=buffer.data();of.nMaxFile=(DWORD)buffer.size();of.lpstrFilter=L"原始 PNG (*.png)\0*.png\0";of.lpstrTitle=L"选择要制作标准母版的PNG（可以多选）";of.Flags=OFN_FILEMUSTEXIST|OFN_ALLOWMULTISELECT|OFN_EXPLORER|OFN_NOCHANGEDIR;
 if(!GetOpenFileNameW(&of)){need(CommDlgExtendedError()==0,"PNG file selection failed");return;}
 std::vector<fs::path> sources;auto first=fs::path(buffer.data());auto* p=buffer.data()+wcslen(buffer.data())+1;
 if(!*p)sources.push_back(first);else while(*p){sources.push_back(first/fs::path(p));p+=wcslen(p)+1;}
 ExpansionSetup setup;setup.font=s.font;auto w=create_expansion_setup(s.window,setup,true);EnableWindow(s.window,FALSE);MSG msg{};
 while(!setup.done){auto result=GetMessageW(&msg,nullptr,0,0);if(result<=0){setup.done=true;if(IsWindow(w))DestroyWindow(w);if(!result)PostQuitMessage((int)msg.wParam);break;}if(!IsDialogMessageW(w,&msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}}
 EnableWindow(s.window,TRUE);SetActiveWindow(s.window);if(!setup.accepted)return;
 auto root=unique_path(module_path().parent_path()/(L"扩级母版-"+local_stamp()));auto batch=make_expansion_batch(sources,setup.sourceTier,setup.targetScale,setup.base,root,"h3-quality",{},false,setup.referenceRoot,"",{},setup.animationContract,setup.frameSetClassification,setup.animationRoiMask);auto batchPath=root/L"master-expand-batch.json";write_json_new(batchPath,batch);load_document(s,batchPath);
}

void post_result(HWND w,std::wstring title,Json result,bool ok,fs::path reportDirectory={},bool showError=true){
 auto*p=new AsyncResult{std::move(title),jsonw(result),ok,showError,std::move(reportDirectory)};PostMessageW(w,WM_ASYNC_RESULT,0,(LPARAM)p);
}
std::vector<fs::path> choose_checkerboard_pngs(HWND owner){
 std::vector<wchar_t> buffer(65536);OPENFILENAMEW of{sizeof(of)};of.hwndOwner=owner;of.lpstrFile=buffer.data();of.nMaxFile=(DWORD)buffer.size();
 of.lpstrFilter=L"PNG 图片 (*.png)\0*.png\0";of.lpstrTitle=L"选择 Gemini 生成的棋盘格 PNG（可以多选）";
 of.Flags=OFN_FILEMUSTEXIST|OFN_ALLOWMULTISELECT|OFN_EXPLORER|OFN_NOCHANGEDIR;
 if(!GetOpenFileNameW(&of)){need(CommDlgExtendedError()==0,"checkerboard PNG selection failed");return{};}
 std::vector<std::wstring> names;for(size_t at=0;at<buffer.size()&&buffer[at];){std::wstring value=buffer.data()+at;names.push_back(value);at+=value.size()+1;}
 std::vector<fs::path> files;if(names.empty())return files;if(names.size()==1){files.emplace_back(names[0]);return files;}
 const fs::path directory(names[0]);for(size_t i=1;i<names.size();i++)files.push_back(directory/names[i]);return files;
}
void async_checkerboard(AppState& s){
 need(!s.busy,"已有任务正在运行");auto files=choose_checkerboard_pngs(s.window);if(files.empty())return;
 set_busy(s,true);settext(s.status,L"正在检测并去除棋盘格；原PNG保持不变。");HWND window=s.window;
 std::thread([window,files=std::move(files)]()mutable{
  try{
   ComInit com;auto result=checkerboard_gui::process_files(files);
   if(!result.output_directory.empty())result.receipt["output_directory"]=str(result.output_directory);
   const bool ok=result.succeeded>0;
   post_result(window,L"棋盘格转透明",result.receipt,ok,result.output_directory,!ok);
  }catch(const std::exception& error){post_result(window,L"棋盘格转透明",Json{{"status","FAILED"},{"error",error.what()},{"source_pngs_modified",0}},false);}
 }).detach();
}
void async_run(AppState&s,const std::wstring&label,bool verifyOnly){
 need(s.isPlan||s.isExpansion||s.isMotion,"先打开已冻结计划");need(!s.busy,"已有任务正在运行");
 bool expand=s.isExpansion,motion=s.isMotion;auto plan=s.current;auto settings=ui_settings(s);HWND w=s.window;set_busy(s,true);
 std::thread([w,plan=std::move(plan),settings=std::move(settings),verifyOnly,label,expand,motion](){
  try{ComInit com;auto r=motion?run_motion(plan,motion_settings(settings),verifyOnly):expand?run_expansion(plan,expansion_settings(settings),verifyOnly):run_plan(plan,settings,verifyOnly);if(expand)r=apply_mounted_hero_export_gate(plan,std::move(r));bool ok=r.value("failed",0)==0;post_result(w,label,r,ok);}
  catch(const std::exception&e){post_result(w,label,Json{{"status","FAILED"},{"error",e.what()}},false);}
 }).detach();
}
void async_selftest(AppState&s){
 need(!s.busy,"已有任务正在运行");HWND w=s.window;set_busy(s,true);
 std::thread([w](){try{ComInit com;post_result(w,L"自检",selftest(false),true);}catch(const std::exception&e){post_result(w,L"自检",Json{{"status","FAILED"},{"error",e.what()}},false);}}).detach();
}

struct CompanionTaskRequest {
 std::string command,stage,inputSha256,operationId;
 fs::path input,output;
};
struct CompanionProcessResult {DWORD exitCode=1,winError=ERROR_SUCCESS;std::wstring output;};
std::mutex companion_capture_mutex;

bool task_command_has_stage(const std::string&command){
 return command=="alpha-benchmark"||command=="blender-actions"||
        command=="creature-production"||command=="creature-master-set"||command=="creature-auto-pose"||command=="hero-mounted-pose"||command=="sequence-temporal"||command=="logical-alpha"||command=="sheet-intake"||command=="native-alias"||command=="asset-repair"||command=="scale-draft"||command=="scale-audit"||command=="scale-layout"||command=="town-flag-alignment"||command=="map-object-sequence";
}
bool task_pose_command(const std::string&command){return command=="creature-auto-pose"||command=="hero-mounted-pose";}
std::string task_stage_name(int selected){
 static const std::array<const char*,3> stages={"check","run","verify"};
 need(selected>=0&&selected<(int)stages.size(),"choose check, run, or verify");return stages[(size_t)selected];
}
std::vector<std::wstring> companion_task_arguments(const CompanionTaskRequest&r){
 std::vector<std::wstring> args{wide(r.command)};
 if(task_command_has_stage(r.command))args.push_back(wide(r.stage));
 if(r.command=="flag-pole-audit"&&_wcsicmp(r.input.extension().c_str(),L".png")==0){
  args.push_back(L"--image");args.push_back(r.input.wstring());
  args.push_back(L"--image-sha256");args.push_back(wide(r.inputSha256));
  args.push_back(L"--outdir");args.push_back(r.output.wstring());
  return args;
 }
 if(r.command=="motion-review")args.push_back(L"--run-report");
 else args.push_back(L"--manifest");
 args.push_back(r.input.wstring());
 if(r.command!="motion-review"){
  args.push_back(L"--manifest-sha256");args.push_back(wide(r.inputSha256));
 }
 args.push_back(L"--outdir");args.push_back(r.output.wstring());
 if(task_command_has_stage(r.command)){
  args.push_back(L"--operation-id");args.push_back(wide(r.operationId));
 }
 return args;
}
std::wstring companion_task_command_line(const fs::path&executable,const CompanionTaskRequest&r){
 std::wstring command=companion_quote_argument(executable.wstring());
 for(const auto&arg:companion_task_arguments(r)){command.push_back(L' ');command+=companion_quote_argument(arg);}
 return command;
}
std::wstring utf8_lossy(const std::string&value){
 if(value.empty())return {};
 int count=MultiByteToWideChar(CP_UTF8,0,value.data(),(int)value.size(),nullptr,0);
 if(count<=0)return L"(日志编码无法识别)";
 std::wstring result((size_t)count,L'\0');
 MultiByteToWideChar(CP_UTF8,0,value.data(),(int)value.size(),result.data(),count);return result;
}
CompanionProcessResult run_companion_task_captured(const fs::path&executable,const CompanionTaskRequest&r){
 wchar_t tempDirectory[32768]{};DWORD tempLength=GetTempPathW((DWORD)std::size(tempDirectory),tempDirectory);
 need(tempLength>0&&tempLength<std::size(tempDirectory),"cannot locate temporary directory for task log");
 wchar_t tempFile[32768]{};need(GetTempFileNameW(tempDirectory,L"H3T",0,tempFile)!=0,"cannot create temporary task log path");
 SECURITY_ATTRIBUTES security{sizeof(security),nullptr,TRUE};
 HANDLE raw=CreateFileW(tempFile,GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
                        &security,CREATE_ALWAYS,FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_DELETE_ON_CLOSE,nullptr);
 if(raw==INVALID_HANDLE_VALUE){DWORD error=GetLastError();DeleteFileW(tempFile);throw std::runtime_error("cannot open temporary task log; Win32 error="+std::to_string(error));}
 Handle log(raw);CompanionProcessResult result;HANDLE oldOut=GetStdHandle(STD_OUTPUT_HANDLE),oldErr=GetStdHandle(STD_ERROR_HANDLE);
 {
  std::lock_guard<std::mutex> lock(companion_capture_mutex);
  if(!SetStdHandle(STD_OUTPUT_HANDLE,log.h)||!SetStdHandle(STD_ERROR_HANDLE,log.h)){
   DWORD error=GetLastError();SetStdHandle(STD_OUTPUT_HANDLE,oldOut);SetStdHandle(STD_ERROR_HANDLE,oldErr);
   throw std::runtime_error("cannot redirect companion output; Win32 error="+std::to_string(error));
  }
  try{result.exitCode=companion_run_process(executable.wstring(),companion_task_arguments(r),result.winError,3600*1000);}
  catch(...){SetStdHandle(STD_OUTPUT_HANDLE,oldOut);SetStdHandle(STD_ERROR_HANDLE,oldErr);throw;}
  SetStdHandle(STD_OUTPUT_HANDLE,oldOut);SetStdHandle(STD_ERROR_HANDLE,oldErr);
 }
 FlushFileBuffers(log.h);LARGE_INTEGER size{};need(GetFileSizeEx(log,&size),"cannot read task log size");
 constexpr std::uint64_t MAX_LOG_BYTES=1024*1024;std::uint64_t readBytes=std::min<std::uint64_t>((std::uint64_t)std::max<LONGLONG>(0,size.QuadPart),MAX_LOG_BYTES);
 LARGE_INTEGER beginning{};need(SetFilePointerEx(log,beginning,nullptr,FILE_BEGIN),"cannot seek task log");
 std::string bytes((size_t)readBytes,'\0');size_t offset=0;
 while(offset<bytes.size()){
  DWORD ask=(DWORD)std::min<size_t>(bytes.size()-offset,64*1024),got=0;
  need(ReadFile(log,bytes.data()+offset,ask,&got,nullptr)&&got==ask,"short read from task log");offset+=got;
 }
 result.output=utf8_lossy(bytes);
 if((std::uint64_t)std::max<LONGLONG>(0,size.QuadPart)>readBytes)result.output+=L"\r\n[日志超过1 MiB，后续内容已截断]";
 return result;
}

void async_companion_task(AppState&s,CompanionTaskRequest request){
 need(!s.busy,"已有任务正在运行");auto executable=module_path();HWND w=s.window;auto display=companion_task_command_line(executable,request);set_busy(s,true);
 std::thread([w,executable=std::move(executable),request=std::move(request),display=std::move(display)](){
  try{
   auto run=run_companion_task_captured(executable,request);bool ok=run.winError==ERROR_SUCCESS&&run.exitCode==0;
   Json result={{"command_line",utf8(display)},{"command",request.command},{"stage",request.stage},
    {"input",str(request.input)},{"input_sha256",request.inputSha256},{"operation_id",request.operationId},
    {"outdir",str(request.output)},{"exit_code",run.winError==ERROR_SUCCESS?Json(run.exitCode):Json(nullptr)},{"win32_error",run.winError},
    {"status",ok?"CLI_COMPLETED":"CLI_FAILED"},{"stdout_stderr",utf8(run.output)},
    {"note","CLI completion or verification does not approve art or confirm game receipt."}};
   post_result(w,L"任务 "+wide(request.command),std::move(result),ok,request.output,false);
  }catch(const std::exception&e){
   Json result={{"command_line",utf8(display)},{"command",request.command},{"stage",request.stage},
    {"input",str(request.input)},{"input_sha256",request.inputSha256},{"operation_id",request.operationId},
    {"outdir",str(request.output)},{"status","UI_DISPATCH_FAILED"},{"error",e.what()},
    {"note","CLI completion or verification does not approve art or confirm game receipt."}};
   post_result(w,L"任务 "+wide(request.command),std::move(result),false,request.output,false);
  }
 }).detach();
}

std::vector<std::string> task_commands(){
 return {"alpha-benchmark","blender-actions","creature-production","creature-master-set","creature-auto-pose","hero-mounted-pose","sequence-temporal","logical-alpha","sheet-intake","native-alias","native-review","motion-review","asset-repair","scale-draft","scale-audit","scale-layout","town-flag-alignment","town-flag-review","flag-pole-audit","map-object-sequence"};
}
std::string task_output_directory_name(const std::string&command){
 if(command=="alpha-benchmark")return "transparent-benchmark";
 if(command=="blender-actions")return "action-pipeline";
 if(command=="creature-production")return "creature-production";
 if(command=="creature-master-set")return "creature-master-set";
 if(command=="creature-auto-pose")return "creature-auto-pose";
 if(command=="hero-mounted-pose")return "hero-mounted-pose";
 if(command=="sequence-temporal")return "sequence-temporal";
 if(command=="logical-alpha")return "logical-alpha";
 if(command=="asset-repair")return "asset-repair";
 if(command=="scale-draft")return "scale-draft";
 if(command=="scale-audit")return "scale-audit";
 if(command=="scale-layout")return "scale-layout";
 if(command=="town-flag-alignment")return "town-flag-alignment";
 if(command=="town-flag-review")return "town-flag-review";
 if(command=="flag-pole-audit")return "flag-pole-audit";
 if(command=="map-object-sequence")return "map-object-sequence";
 if(command=="sheet-intake")return "sheet-intake";
 if(command=="native-alias")return "native-alias";
 if(command=="native-review")return "native-review";
 if(command=="motion-review")return "motion-review";
 throw std::runtime_error("unknown task-center command");
}
fs::path task_tool_root(){
 auto root=companion_product_root(module_path().parent_path());
 std::wstring runtimeLeaf=L".runtime-";for(const char* c=H3_COMPANION_RUNTIME_SHA;*c;++c)runtimeLeaf.push_back((wchar_t)*c);
 for(int depth=0;depth<10&&!root.empty();++depth,root=root.parent_path())
  if((fs::is_regular_file(root/L"src"/L"main.cpp")&&fs::is_directory(root/L"scripts"))
     ||fs::is_regular_file(root/runtimeLeaf/L"runtime-manifest.json"))return clean_path(root);
 throw std::runtime_error("cannot locate H3TierExport source root beside this executable");
}
bool safe_task_operation_id(const std::wstring&value){
 if(value.empty()||value.size()>96||value==L"."||value==L".."||value.back()==L'.')return false;
 for(wchar_t c:value)if(!((c>=L'a'&&c<=L'z')||(c>=L'A'&&c<=L'Z')||(c>=L'0'&&c<=L'9')||c==L'_'||c==L'-'||c==L'.'))return false;
 return true;
}
fs::path task_output_path(const std::string&command,const std::wstring&operationId){
 auto root=task_tool_root()/L"build"/L"experiments"/wide(task_output_directory_name(command));
 std::wstring leaf;
 if(task_command_has_stage(command))leaf=safe_task_operation_id(operationId)?operationId:L"operation-"+local_stamp();
 else leaf=L"ui-review-"+local_stamp();
 return clean_path(root/fs::path(leaf));
}

struct TaskCenterSetup {
 HWND window{},kind{},stage{},input{},browseInput{},hash{},output{},browseOutput{},operation{},note{},openOutput{};
 HFONT font{};bool done=false,accepted=false,changing=false,outputEdited=false,operationEdited=false;
 CompanionTaskRequest request;
};
std::string task_selected_command(const TaskCenterSetup&s){
 int index=(int)SendMessageW(s.kind,CB_GETCURSEL,0,0);auto commands=task_commands();
 need(index>=0&&index<(int)commands.size(),"choose a task type");return commands[(size_t)index];
}
std::string task_manifest_operation_id(const Json&document){
 for(const char*key:{"operation_id","operationId"})
  if(document.contains(key)&&document[key].is_string())return document[key].get<std::string>();
 return {};
}
void task_set_operation_id(TaskCenterSetup&s,const std::string&id){
 s.changing=true;settext(s.operation,wide(id));s.changing=false;
}
void task_refresh_output(TaskCenterSetup&s){
 if(s.outputEdited)return;
 auto command=task_selected_command(s);auto operation=utf8(wtext(s.operation));
 s.changing=true;settext(s.output,task_output_path(command,wide(operation)).wstring());s.changing=false;
}
void task_update_mode(TaskCenterSetup&s){
 auto command=task_selected_command(s);bool staged=task_command_has_stage(command);
 int selected=(int)SendMessageW(s.stage,CB_GETCURSEL,0,0);
 SendMessageW(s.stage,CB_SETCURSEL,task_pose_command(command)?1:staged?(selected>=0&&selected<3?selected:0):3,0);
 EnableWindow(s.stage,staged);EnableWindow(s.operation,staged);
 std::wstring note;
 if(command=="alpha-benchmark")note=L"补帧透明质量基准。输出位于 build\\experiments\\transparent-benchmark\\<operation_id>；运行结果仍需人工审阅。";
 else if(command=="blender-actions")note=L"按绑定的模型、骨架、Action、相机与原槽运行。有 motion_consistency 时检查实际骨骼/网格段尺度、固定相机及逐槽身体根部轨迹；缺合同记未测，不代表动作或美术通过。";
 else if(command=="creature-production")note=L"验证并交付整组已有源帧，不生成新姿态。输出位于 build\\experiments\\creature-production\\<operation_id>。";
 else if(command=="creature-master-set")note=L"核对完整官方槽位、尺寸比例、Alpha和命名；按冻结 common K 对真实母图等比缩小归一化。肢体完整性需绑定 AI/人工逐帧审查；候选仍需正式原生几何与3X验收。";
 else if(command=="creature-auto-pose")note=L"选高清透明 PNG 可自动描点；也可选官方全槽冻结清单批量检测。当前模型适用于人形候选，遮挡和幻想生物须复核；模型点位、翻转分歧及等比注册仅供审阅，不修改原图或自动批准。";
 else if(command=="hero-mounted-pose")note=L"选高清透明 PNG 或骑乘英雄冻结清单。分别给马和骑手生成模型描点，蓝点为马体翻转复测；遮挡、正背面可能误判，全部仅供复核，不修改原图或自动判定尺度与游戏注册。";
 else if(command=="sequence-temporal")note=L"读取按已复验序列布局回执冻结的任务，检查同姿态身体描点的全组跨度、相邻帧和显式循环首尾根部位移。数值只提示复核，不修改图片，不自动判定美术或游戏通过。";
 else if(command=="logical-alpha")note=L"生成 logical-alpha 数据，不代表新增高清绘画。输出位于 build\\experiments\\logical-alpha\\<operation_id>。";
 else if(command=="asset-repair")note=L"本地素材抢救：共享接口、同父图裁片、材质匀色与边界诊断、固定底座、八色通用旗纹派生及显式去污。先检查，再运行和逐像素复验；源图不改写，正式3X另走原生导出。";
 else if(command=="scale-draft")note=L"开工前按官方全槽生成类别尺度草案。没有核准样本或解剖标点时保留DRAFT/NOT_MEASURED；工作画幅不覆盖最终原生几何，不修改图片。载入scale-draft冻结任务。";
 else if(command=="scale-audit")note=L"制作后批量列出全部原生槽、缺漏、名称、SHA、采样K及透明框。仅用绑定的解剖标点报告身体尺度；可附SHA绑定动作复核表，检查官方帧顺序、相邻转场、loop/non-loop分类和风险说明完整性。bbox不判身体尺度；原生几何、3X、运行状态分别列项，不自动缩放或批准美术。";
 else if(command=="scale-layout")note=L"读取同一制作档案。生物序列清单用真实DEF、可比身体标点和根部轨迹做无损注册，放不进槽时不缩帧掩盖；运行后生成唯一3X导出请求。质量清单缺证据仍记未测，原母版不改写，不自动批准美术。";
 else if(command=="town-flag-alignment")note=L"旧底右注册候选预览：bottom_right 不证明旗杆或入口对齐；请用 town-flag-review 检查实际 MOD，逐旗核对杆顶和道路。无坐标表项时参考1999原版DEF旗面。";
 else if(command=="town-flag-review")note=L"读取冻结的实际MOD建筑、独立旗面层和原版1X参考，生成逐城左右旗拖动、杆顶和门路标记审图。位移单位为3X像素；保存的是待复核布局，不修改MOD。";
 else if(command=="flag-pole-audit")note=L"选择原版 DEF 文件夹中的 0_0.png 等帧，或按 DEF 身份命名的 PNG；也可载入批量清单。审图页显示 1999 原版画布、裁片、原点和旗面像素；确认图片注册后再标杆头/脚。旗面边缘不等于杆顶；不会修改源图。";
 else if(command=="map-object-sequence")note=L"仅扩展官方原本单帧的地图物件组。先用 CLI freeze 冻结连续、完整画布×3 的候选帧，再在此运行 check / run / verify。输出成对 Sprites 与 Sprites3x JSON、同名帧和原版静态 1X 回退图；不会改母版、正式3X或MOD。";
 else if(command=="sheet-intake")note=L"整图帧归属和原版槽位校验；保留全部非零透明像素与失败诊断，不裁图、不审批美术。";
 else if(command=="native-alias")note=L"原版索引与调色板及两槽几何精确等价核对；仅候选原字节复用，F10未获独立美术与时序批准。";
 else if(command=="native-review")note=L"只生成原生几何诊断审图包；报告要求人工复核，不会批准美术。输入须为冻结 native-review manifest。";
 else note=L"只生成动作时序 HTML/JSON 审阅包；它不确认原版时序，也不会批准美术。输入须为 motion run report。";
 note+=L"\r\n数值检查、候选生成和数据交付均不等于用户美术批准或游戏接收。";
 settext(s.note,note);task_refresh_output(s);
}
void task_load_input(TaskCenterSetup&s,const fs::path&input){
 auto pathValue=clean_path(input);auto bytes=read_all(pathValue,32*1024*1024);
 auto command=task_selected_command(s);bool posePng=task_pose_command(command)&&_wcsicmp(pathValue.extension().c_str(),L".png")==0;
 bool flagPng=command=="flag-pole-audit"&&_wcsicmp(pathValue.extension().c_str(),L".png")==0;
 Json document=(posePng||flagPng)?Json::object():parse_json(bytes);
 std::string operation=posePng?"pose-"+utf8(local_stamp()):task_command_has_stage(command)?task_manifest_operation_id(document):std::string{};
 if(task_pose_command(command)&&operation.empty())operation="pose-"+utf8(local_stamp());
 s.changing=true;settext(s.input,pathValue.wstring());settext(s.hash,wide(sha(bytes)));
 if(!s.operationEdited)SetWindowTextW(s.operation,wide(operation).c_str());s.changing=false;
 task_refresh_output(s);
}
void task_collect_request(TaskCenterSetup&s){
 auto command=task_selected_command(s);auto input=clean_path(fs::path(wtext(s.input)));
 auto bytes=read_all(input,32*1024*1024);bool posePng=task_pose_command(command)&&_wcsicmp(input.extension().c_str(),L".png")==0;
 bool flagPng=command=="flag-pole-audit"&&_wcsicmp(input.extension().c_str(),L".png")==0;
 Json document=(posePng||flagPng)?Json::object():parse_json(bytes);
 CompanionTaskRequest request;request.command=command;request.input=input;request.inputSha256=sha(bytes);
 s.changing=true;settext(s.hash,wide(request.inputSha256));s.changing=false;
 if(task_command_has_stage(command)){
  int stage=(int)SendMessageW(s.stage,CB_GETCURSEL,0,0);request.stage=task_stage_name(stage);
  std::wstring operation=wtext(s.operation);
  if(!s.operationEdited&&!posePng){
   auto fromManifest=task_manifest_operation_id(document);task_set_operation_id(s,fromManifest);operation=wide(fromManifest);
   if(task_pose_command(command)&&fromManifest.empty()){operation=L"pose-"+local_stamp();task_set_operation_id(s,utf8(operation));}
  }
  need(safe_task_operation_id(operation),"operation ID must be a safe ASCII path component; the selected CLI validates its exact contract");
  request.operationId=utf8(operation);
 }else request.stage="run";
 if(task_command_has_stage(command))task_refresh_output(s);request.output=clean_path(fs::path(wtext(s.output)));
 s.request=std::move(request);s.accepted=true;s.done=true;SendMessageW(s.window,WM_CLOSE,0,0);
}
struct FolderDialogSeed{std::wstring path;};
int CALLBACK task_folder_callback(HWND dialog,UINT message,LPARAM,LPARAM data){
 if(message==BFFM_INITIALIZED){auto*seed=(FolderDialogSeed*)data;if(seed&&!seed->path.empty())SendMessageW(dialog,BFFM_SETSELECTIONW,TRUE,(LPARAM)seed->path.c_str());}
 return 0;
}
std::optional<fs::path> choose_output_parent(HWND owner,const fs::path&current){
 FolderDialogSeed seed{current.wstring()};wchar_t display[MAX_PATH]{};
 BROWSEINFOW info{};info.hwndOwner=owner;info.pszDisplayName=display;info.lpszTitle=L"选择输出目录的父文件夹";
 info.ulFlags=BIF_RETURNONLYFSDIRS|BIF_NEWDIALOGSTYLE;info.lpfn=task_folder_callback;info.lParam=(LPARAM)&seed;
 PIDLIST_ABSOLUTE item=SHBrowseForFolderW(&info);if(!item)return std::nullopt;
 wchar_t pathBuffer[MAX_PATH]{};BOOL ok=SHGetPathFromIDListW(item,pathBuffer);CoTaskMemFree(item);
 if(!ok)return std::nullopt;return clean_path(fs::path(pathBuffer));
}
void task_choose_output(TaskCenterSetup&s){
 auto command=task_selected_command(s);auto current=fs::path(wtext(s.output)).parent_path();
 auto parent=choose_output_parent(s.window,current);if(!parent)return;
 std::wstring leaf;
 if(task_command_has_stage(command)){auto operation=wtext(s.operation);leaf=safe_task_operation_id(operation)?operation:L"operation-"+local_stamp();}
 else leaf=L"ui-review-"+local_stamp();
 s.changing=true;settext(s.output,(*parent/leaf).wstring());s.changing=false;s.outputEdited=true;
}
void task_open_output(TaskCenterSetup&s){
 auto output=clean_path(fs::path(wtext(s.output)));need(fs::is_directory(output),"输出目录尚不存在");
 auto result=(INT_PTR)ShellExecuteW(s.window,L"open",output.c_str(),nullptr,output.parent_path().c_str(),SW_SHOWNORMAL);
 need(result>32,"无法打开输出目录");
}
#include "quality_profile_gui.hpp"
LRESULT CALLBACK task_center_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
 auto*s=(TaskCenterSetup*)GetWindowLongPtrW(w,GWLP_USERDATA);
 if(message==WM_NCCREATE){s=(TaskCenterSetup*)((CREATESTRUCTW*)lp)->lpCreateParams;s->window=w;SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)s);}
 if(!s)return DefWindowProcW(w,message,wp,lp);
 try{
  if(message==WM_CREATE){
   auto control=[&](const wchar_t*klass,const wchar_t*text,DWORD style,int x,int y,int width,int height,int id,DWORD ex=0){
    auto h=CreateWindowExW(ex,klass,text,WS_CHILD|WS_VISIBLE|style,x,y,width,height,w,(HMENU)(INT_PTR)id,GetModuleHandleW(nullptr),nullptr);
    need(h!=nullptr,"create task-center control");font(h,s->font);return h;
   };
   control(L"STATIC",L"任务",0,20,20,110,26,2191);s->kind=control(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL,140,16,365,180,IDC_TASK_KIND);
   const wchar_t*labels[]={L"补帧质量基准 alpha-benchmark",L"Blender 骨骼动作 blender-actions",L"整组交付 creature-production",L"生物母版归一化与完成检查 creature-master-set",L"自动身体描点 creature-auto-pose",L"骑马英雄描点 hero-mounted-pose",L"动作尺度与轨迹筛查 sequence-temporal",L"逻辑遮罩 logical-alpha",L"整图帧归属 sheet-intake",L"原生精确复用 native-alias",L"原生几何审图 native-review",L"动作时序审图 motion-review",L"本地素材抢救与派生 asset-repair",L"开工前类别尺度草案 scale-draft",L"制作后全槽尺寸检查 scale-audit",L"人物尺度与批量质量验证 scale-layout",L"城镇候选旗标 town-flag-alignment",L"实际 MOD 旗面逐城复核 town-flag-review",L"通用旗杆坐标与杆长检查 flag-pole-audit",L"地图物件单帧扩展为动画 map-object-sequence"};
   for(auto label:labels)SendMessageW(s->kind,CB_ADDSTRING,0,(LPARAM)label);SendMessageW(s->kind,CB_SETCURSEL,0,0);
   control(L"STATIC",L"阶段",0,530,20,70,26,2192);s->stage=control(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL,590,16,170,150,IDC_TASK_STAGE);
   for(auto label:{L"检查 check",L"运行 run",L"验证 verify",L"生成审阅包 run only"})SendMessageW(s->stage,CB_ADDSTRING,0,(LPARAM)label);SendMessageW(s->stage,CB_SETCURSEL,0,0);
   control(L"STATIC",L"输入文件",0,20,62,110,26,2193);s->input=control(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,140,58,550,27,IDC_TASK_INPUT,WS_EX_CLIENTEDGE);
   s->browseInput=control(L"BUTTON",L"浏览…",BS_PUSHBUTTON,700,58,75,27,IDC_TASK_BROWSE_INPUT);
   control(L"STATIC",L"输入 SHA256",0,20,101,110,26,2194);s->hash=control(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL|ES_READONLY,140,97,635,27,IDC_TASK_SHA,WS_EX_CLIENTEDGE);
   control(L"STATIC",L"输出目录",0,20,140,110,26,2195);s->output=control(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,140,136,550,27,IDC_TASK_OUTPUT,WS_EX_CLIENTEDGE);
   s->browseOutput=control(L"BUTTON",L"选父目录…",BS_PUSHBUTTON,700,136,75,27,IDC_TASK_BROWSE_OUTPUT);
   control(L"STATIC",L"Operation ID",0,20,179,110,26,2196);s->operation=control(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,140,175,300,27,IDC_TASK_OPERATION,WS_EX_CLIENTEDGE);
   s->note=control(L"STATIC",L"",SS_LEFT|SS_NOPREFIX,20,222,755,90,IDC_TASK_NOTE);
   control(L"BUTTON",L"画前档案 / 一键检测…",BS_PUSHBUTTON,20,340,260,32,IDC_TASK_QUALITY_PROFILE);
   s->openOutput=control(L"BUTTON",L"打开输出目录",BS_PUSHBUTTON,360,340,125,32,IDC_TASK_OPEN_OUTPUT);
   control(L"BUTTON",L"开始任务",BS_DEFPUSHBUTTON,510,340,115,32,IDC_TASK_START);
   control(L"BUTTON",L"取消",BS_PUSHBUTTON,650,340,85,32,IDC_TASK_CANCEL);
   task_update_mode(*s);
   return 0;
  }
  if(message==WM_COMMAND){
   int id=LOWORD(wp),notification=HIWORD(wp);
   if(id==IDC_TASK_KIND&&notification==CBN_SELCHANGE){s->outputEdited=false;s->operationEdited=false;task_set_operation_id(*s,"");task_update_mode(*s);auto current=fs::path(wtext(s->input));if(!current.empty()){auto selected=task_selected_command(*s);if(!task_pose_command(selected)&&selected!="flag-pole-audit"&&_wcsicmp(current.extension().c_str(),L".png")==0){settext(s->input,L"");settext(s->hash,L"");}else task_load_input(*s,current);}return 0;}
   if(id==IDC_TASK_OUTPUT&&notification==EN_CHANGE&&!s->changing){s->outputEdited=true;return 0;}
   if(id==IDC_TASK_OPERATION&&notification==EN_CHANGE&&!s->changing){s->operationEdited=true;if(!s->outputEdited)task_refresh_output(*s);return 0;}
   if(notification!=BN_CLICKED)return 0;
   if(id==IDC_TASK_BROWSE_INPUT){auto command=task_selected_command(*s);auto selected=command=="flag-pole-audit"?choose_pose_input(w,fs::path(wtext(s->input)),L"选择 DEF 文件夹中的原生帧 PNG、身份命名 PNG 或批量清单"):task_pose_command(command)?choose_pose_input(w,fs::path(wtext(s->input))):choose_json(w,fs::path(wtext(s->input)),L"选择冻结输入 JSON");if(selected){s->operationEdited=false;task_load_input(*s,*selected);}return 0;}
   if(id==IDC_TASK_BROWSE_OUTPUT){task_choose_output(*s);return 0;}
   if(id==IDC_TASK_OPEN_OUTPUT){task_open_output(*s);return 0;}
   if(id==IDC_TASK_QUALITY_PROFILE){quality_profile_dialog(*s);return 0;}
   if(id==IDC_TASK_START){task_collect_request(*s);return 0;}
   if(id==IDC_TASK_CANCEL){SendMessageW(w,WM_CLOSE,0,0);return 0;}
  }
  if(message==WM_CLOSE){s->done=true;DestroyWindow(w);return 0;}
 }catch(const std::exception&e){MessageBoxW(w,wide(e.what()).c_str(),L"H3TierExport - 任务中心",MB_OK|MB_ICONERROR);return 0;}
 return DefWindowProcW(w,message,wp,lp);
}
HWND create_task_center_window(HWND owner,TaskCenterSetup&setup,bool show){
 static std::once_flag once;std::call_once(once,[]{WNDCLASSW c{};c.lpfnWndProc=task_center_proc;c.hInstance=GetModuleHandleW(nullptr);c.hCursor=LoadCursorW(nullptr,IDC_ARROW);c.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);c.lpszClassName=L"H3TierExport.TaskCenter";need(RegisterClassW(&c)!=0,"register task center");});
 auto w=CreateWindowExW(WS_EX_DLGMODALFRAME|WS_EX_CONTROLPARENT,L"H3TierExport.TaskCenter",L"H3TierExport - 批量任务中心",WS_CAPTION|WS_SYSMENU|WS_POPUP|WS_CLIPCHILDREN,CW_USEDEFAULT,CW_USEDEFAULT,810,430,owner,nullptr,GetModuleHandleW(nullptr),&setup);
 need(w!=nullptr,"create task center");center_window(w,owner);if(show){ShowWindow(w,SW_SHOW);SetActiveWindow(w);SetFocus(setup.kind);}return w;
}
void run_task_center(AppState&s){
 need(!s.busy,"已有任务正在运行");TaskCenterSetup setup;setup.font=s.font;
 auto window=create_task_center_window(s.window,setup,true);EnableWindow(s.window,FALSE);MSG msg{};
 while(!setup.done){auto result=GetMessageW(&msg,nullptr,0,0);if(result<=0){setup.done=true;if(IsWindow(window))DestroyWindow(window);if(!result)PostQuitMessage((int)msg.wParam);break;}
  if(!IsDialogMessageW(window,&msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}}
 EnableWindow(s.window,TRUE);SetActiveWindow(s.window);if(setup.accepted)async_companion_task(s,std::move(setup.request));
}


HWND ctl(AppState&s,const wchar_t*klass,const wchar_t*text,DWORD style,int id,DWORD ex=0){
 auto h=CreateWindowExW(ex,klass,text,WS_CHILD|WS_VISIBLE|style,0,0,10,10,s.window,(HMENU)(INT_PTR)id,s.instance,nullptr);
 need(h!=nullptr,"create UI control");font(h,s.font);return h;
}
void init_columns(HWND list){
 struct C{const wchar_t*t;int w;} c[]={{L"资源 ID",165},{L"状态",180},{L"母版路径",560},{L"视图",55}};
 for(int i=0;i<4;i++){LVCOLUMNW x{};x.mask=LVCF_TEXT|LVCF_WIDTH|LVCF_SUBITEM;x.pszText=(LPWSTR)c[i].t;x.cx=c[i].w;x.iSubItem=i;ListView_InsertColumn(list,i,&x);}
 ListView_SetExtendedListViewStyle(list,LVS_EX_FULLROWSELECT|LVS_EX_GRIDLINES|LVS_EX_DOUBLEBUFFER);
}
void create_controls(AppState&s){
 NONCLIENTMETRICSW ncm{sizeof(ncm)};SystemParametersInfoW(SPI_GETNONCLIENTMETRICS,sizeof(ncm),&ncm,0);
 HDC dc=GetDC(nullptr);int screenDpi=dc?GetDeviceCaps(dc,LOGPIXELSY):96;if(dc)ReleaseDC(nullptr,dc);
 ncm.lfMessageFont.lfHeight=-MulDiv(10,screenDpi,72);
 s.font=CreateFontIndirectW(&ncm.lfMessageFont);need(s.font!=nullptr,"create GUI font");
 s.path=ctl(s,L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,IDC_PATH,WS_EX_CLIENTEDGE);
 s.browse=ctl(s,L"BUTTON",L"浏览…",BS_PUSHBUTTON,IDC_BROWSE);
 s.createPlan=ctl(s,L"BUTTON",L"生成计划",BS_PUSHBUTTON,IDC_CREATE_PLAN);
 s.check=ctl(s,L"BUTTON",L"检查",BS_PUSHBUTTON,IDC_CHECK);
 s.approve=ctl(s,L"BUTTON",L"批准选中",BS_PUSHBUTTON,IDC_APPROVE);
 s.openMaster=ctl(s,L"BUTTON",L"打开母版",BS_PUSHBUTTON,IDC_OPEN_MASTER);
 s.run=ctl(s,L"BUTTON",L"导出已批准",BS_DEFPUSHBUTTON,IDC_RUN);
 s.verify=ctl(s,L"BUTTON",L"验证输出",BS_PUSHBUTTON,IDC_VERIFY);
 s.selftest=ctl(s,L"BUTTON",L"硬件自检",BS_PUSHBUTTON,IDC_SELFTEST);
 s.openFolder=ctl(s,L"BUTTON",L"打开目录",BS_PUSHBUTTON,IDC_OPEN_FOLDER);
 s.expand=ctl(s,L"BUTTON",L"母版扩展…",BS_PUSHBUTTON,IDC_EXPAND);
 s.motion=ctl(s,L"BUTTON",L"动作网格/推帧…",BS_PUSHBUTTON,IDC_MOTION);
 s.taskCenter=ctl(s,L"BUTTON",L"批量任务中心…",BS_PUSHBUTTON,IDC_TASK_CENTER);
 s.apng=ctl(s,L"BUTTON",L"文件夹动画预览/打包…",BS_PUSHBUTTON,IDC_APNG);
 s.checkerboard=ctl(s,L"BUTTON",L"棋盘格转透明…",BS_PUSHBUTTON,IDC_CHECKERBOARD);
 s.capabilities=ctl(s,L"STATIC",CAPABILITIES_TITLE,SS_LEFT,IDC_CAPABILITIES);
 ctl(s,L"STATIC",L"后端",SS_LEFT,2001);s.backend=ctl(s,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL,IDC_BACKEND);
 for(auto x:{L"自动 Auto",L"CPU+GPU 混合",L"仅 CPU",L"强制 GPU"})SendMessageW(s.backend,CB_ADDSTRING,0,(LPARAM)x);SendMessageW(s.backend,CB_SETCURSEL,2,0);
 ctl(s,L"STATIC",L"画质",SS_LEFT,2002);s.filter=ctl(s,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL,IDC_FILTER);
 for(auto x:{L"H3 精细画质（CPU）",L"经典 Lanczos3",L"柔和 Mitchell",L"面积平均"})SendMessageW(s.filter,CB_ADDSTRING,0,(LPARAM)x);SendMessageW(s.filter,CB_SETCURSEL,0,0);
 ctl(s,L"STATIC",L"并行任务",SS_LEFT,2003);s.workers=ctl(s,L"EDIT",L"32",WS_BORDER|ES_NUMBER|ES_CENTER,IDC_WORKERS,WS_EX_CLIENTEDGE);
 ctl(s,L"STATIC",L"内存上限 GiB",SS_LEFT,2004);s.memory=ctl(s,L"EDIT",L"64",WS_BORDER|ES_NUMBER|ES_CENTER,IDC_MEMORY,WS_EX_CLIENTEDGE);
 s.list=ctl(s,WC_LISTVIEWW,L"",LVS_REPORT|LVS_SHOWSELALWAYS|WS_BORDER,IDC_LIST,WS_EX_CLIENTEDGE);init_columns(s.list);
 s.status=ctl(s,L"STATIC",L"就绪",SS_LEFT,IDC_STATUS);
 s.log=ctl(s,L"EDIT",L"",WS_BORDER|ES_MULTILINE|ES_AUTOVSCROLL|ES_READONLY|WS_VSCROLL,IDC_LOG,WS_EX_CLIENTEDGE);SendMessageW(s.log,EM_SETLIMITTEXT,4*1024*1024,0);
 s.progress=ctl(s,PROGRESS_CLASSW,L"",PBS_MARQUEE,IDC_PROGRESS);ShowWindow(s.progress,SW_HIDE);
}
void layout(AppState&s){
 RECT r{};GetClientRect(s.window,&r);int W=r.right,H=r.bottom,m=sx(s.window,14),gap=sx(s.window,7),bh=sx(s.window,30),eh=sx(s.window,28);
 int y=m;int headerH=sx(s.window,23);
 MoveWindow(s.capabilities,m,y,W-2*m,headerH,TRUE);
 y+=headerH+gap;
 int bw=sx(s.window,82);MoveWindow(s.path,m,y,W-m*2-bw-gap,bh,TRUE);MoveWindow(s.browse,W-m-bw,y,bw,bh,TRUE);y+=bh+gap;
 HWND buttons[]={s.createPlan,s.check,s.approve,s.openMaster,s.run,s.verify,s.selftest,s.openFolder,s.expand,s.motion,s.taskCenter,s.apng,s.checkerboard};
 int buttonWidths[]={90,70,92,86,105,86,86,86,110,124,112,164,134};int x=m;
 for(int i=0;i<13;i++){int w=sx(s.window,buttonWidths[i]);if(x+w>W-m){x=m;y+=bh+gap;}MoveWindow(buttons[i],x,y,w,bh,TRUE);x+=w+gap;}y+=bh+gap;
 // labels were created with IDs; retrieve them for layout.
 int labelW=sx(s.window,54),comboW=sx(s.window,150),editW=sx(s.window,58);
 HWND l1=GetDlgItem(s.window,2001),l2=GetDlgItem(s.window,2002),l3=GetDlgItem(s.window,2003),l4=GetDlgItem(s.window,2004);
 MoveWindow(l1,m,y+5,labelW,eh,TRUE);x=m+labelW;MoveWindow(s.backend,x,y,comboW,eh*7,TRUE);x+=comboW+gap;
 MoveWindow(l2,x,y+5,labelW,eh,TRUE);x+=labelW;MoveWindow(s.filter,x,y,comboW,eh*5,TRUE);x+=comboW+gap;
 MoveWindow(l3,x,y+5,sx(s.window,68),eh,TRUE);x+=sx(s.window,68);MoveWindow(s.workers,x,y,editW,eh,TRUE);x+=editW+gap;
 MoveWindow(l4,x,y+5,sx(s.window,86),eh,TRUE);x+=sx(s.window,86);MoveWindow(s.memory,x,y,editW,eh,TRUE);
 y+=eh+gap;
 int logH=sx(s.window,150),statusH=sx(s.window,24),progH=sx(s.window,8);
 int listH=std::max(sx(s.window,120),H-y-logH-statusH-progH-gap*4-m);
 MoveWindow(s.list,m,y,W-2*m,listH,TRUE);y+=listH+gap;
 MoveWindow(s.status,m,y,W-2*m,statusH,TRUE);y+=statusH+gap;
 MoveWindow(s.progress,m,y,W-2*m,progH,TRUE);y+=progH+gap;
 MoveWindow(s.log,m,y,W-2*m,std::max(sx(s.window,70),H-y-m),TRUE);
}
void hardware_banner(AppState&s){
 try{
  auto mem=host_memory();Gpu g;double gib=mem["physical_bytes"].get<double>()/(1024.0*1024*1024);
  std::string gpu=g.info.value("description",g.available?"hardware GPU":"GPU unavailable");
  std::wstring x=L"H3TierExport "+wide(VERSION)+L" | CPU "+std::to_wstring(mem["logical_threads"].get<unsigned>())+L" 线程 | RAM "+std::to_wstring((int)std::llround(gib))+L" GiB | GPU "+wide(gpu);
  settext(s.status,x);append_log(s,x);
 }catch(const std::exception&e){append_log(s,L"硬件信息读取失败："+wide(e.what()));}
}
LRESULT CALLBACK proc(HWND w,UINT msg,WPARAM wp,LPARAM lp){
 auto*s=(AppState*)GetWindowLongPtrW(w,GWLP_USERDATA);
 if(msg==WM_NCCREATE){auto*cs=(CREATESTRUCTW*)lp;s=(AppState*)cs->lpCreateParams;SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)s);s->window=w;}
 if(!s)return DefWindowProcW(w,msg,wp,lp);
 try{
  switch(msg){
   case WM_CREATE:{
    create_controls(*s);auto icon=LoadIconW(s->instance,MAKEINTRESOURCEW(IDI_APP_ICON));if(icon){SendMessageW(w,WM_SETICON,ICON_BIG,(LPARAM)icon);SendMessageW(w,WM_SETICON,ICON_SMALL,(LPARAM)icon);}
    layout(*s);if(!s->smoke){hardware_banner(*s);auto p=default_project_plan();if(!p.empty())try{load_document(*s,p);}catch(const std::exception&e){append_log(*s,L"默认计划未载入："+wide(e.what()));}}
    return 0;
   }
   case WM_SIZE:layout(*s);return 0;
   case WM_DPICHANGED:{auto*rr=(RECT*)lp;SetWindowPos(w,nullptr,rr->left,rr->top,rr->right-rr->left,rr->bottom-rr->top,SWP_NOZORDER|SWP_NOACTIVATE);layout(*s);return 0;}
   case WM_COMMAND:{
    if(HIWORD(wp)!=BN_CLICKED && LOWORD(wp)!=IDC_BACKEND && LOWORD(wp)!=IDC_FILTER)return 0;
    switch(LOWORD(wp)){
     case IDC_BROWSE:{auto p=choose_json(w,s->currentPath);if(p)load_document(*s,*p);break;}
     case IDC_CREATE_PLAN:create_plan(*s);break;
     case IDC_CHECK:check_current(*s);break;
     case IDC_APPROVE:approve_selected(*s);break;
     case IDC_OPEN_MASTER:open_master(*s);break;
     case IDC_RUN:async_run(*s,L"导出",false);break;
     case IDC_VERIFY:async_run(*s,L"验证",true);break;
     case IDC_SELFTEST:async_selftest(*s);break;
     case IDC_OPEN_FOLDER:open_folder(*s);break;
     case IDC_EXPAND:create_expansion(*s);break;
     case IDC_APNG:apngui::run(w,s->font);break;
     case IDC_CHECKERBOARD:async_checkerboard(*s);break;
    case IDC_MOTION:{auto p=choose_json(w,{});if(p){auto document=read_json(*p);need(document.value("schema","")=="h3.motion.spec.v1"||document.value("schema","")=="h3.motion.batch.v1","请选择动作spec或冻结motion batch");load_document(*s,*p);}break;}
    case IDC_TASK_CENTER:run_task_center(*s);break;
    }return 0;
   }
   case WM_NOTIFY:{
    auto*n=(NMHDR*)lp;if(n->idFrom==IDC_LIST&&n->code==NM_DBLCLK){open_master(*s);return 0;}break;
   }
   case WM_ASYNC_RESULT:{
    std::unique_ptr<AsyncResult> r((AsyncResult*)lp);set_busy(*s,false);append_log(*s,L"["+r->title+L"]\r\n"+r->body);
    settext(s->status,(r->ok?L"完成：" : L"失败：")+r->title);
    if(!r->reportDirectory.empty()){
     s->lastReportDirectory=r->reportDirectory;settext(s->openFolder,L"打开报告位置");
     append_log(*s,L"报告位置："+r->reportDirectory.wstring());
    }
    if(!r->ok&&r->showError)MessageBoxW(w,r->body.c_str(),(L"H3TierExport - "+r->title).c_str(),MB_OK|MB_ICONERROR);
    return 0;
   }
   case WM_CLOSE:if(s->busy){MessageBoxW(w,L"仍有任务运行。为防止中途终止文件发布，请等待任务结束后再退出。",L"H3TierExport",MB_OK|MB_ICONWARNING);return 0;}DestroyWindow(w);return 0;
   case WM_DESTROY:if(s->font)DeleteObject(s->font);if(!s->smoke)PostQuitMessage(0);return 0;
  }
 }catch(const std::exception&e){append_log(*s,L"错误："+wide(e.what()));MessageBoxW(w,wide(e.what()).c_str(),L"H3TierExport",MB_OK|MB_ICONERROR);set_busy(*s,false);return 0;}
 return DefWindowProcW(w,msg,wp,lp);
}
void register_class(HINSTANCE i){
 static std::once_flag once;std::call_once(once,[&]{WNDCLASSEXW c{sizeof(c)};c.style=CS_HREDRAW|CS_VREDRAW;c.lpfnWndProc=proc;c.hInstance=i;c.hCursor=LoadCursorW(nullptr,IDC_ARROW);c.hIcon=LoadIconW(i,MAKEINTRESOURCEW(IDI_APP_ICON));c.hIconSm=c.hIcon;c.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);c.lpszClassName=APP_CLASS;need(RegisterClassExW(&c)!=0,"register GUI class");});
}
HWND create_main(AppState&s,bool show){
 register_class(s.instance);
 DWORD style=WS_OVERLAPPEDWINDOW|WS_CLIPCHILDREN;
 auto w=CreateWindowExW(0,APP_CLASS,APP_TITLE,style,CW_USEDEFAULT,CW_USEDEFAULT,1180,780,nullptr,nullptr,s.instance,&s);
 need(w!=nullptr,"create main window");center_window(w,nullptr);if(show){ShowWindow(w,SW_SHOW);UpdateWindow(w);SetFocus(s.path);}return w;
}
} // namespace

int run_gui(HINSTANCE instance){
 INITCOMMONCONTROLSEX ic{sizeof(ic),ICC_LISTVIEW_CLASSES|ICC_PROGRESS_CLASS|ICC_STANDARD_CLASSES};InitCommonControlsEx(&ic);
 auto dpiFn=(decltype(&SetProcessDpiAwarenessContext))GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetProcessDpiAwarenessContext");if(dpiFn)dpiFn(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
 DWORD processes[2]{};if(GetConsoleProcessList(processes,2)==1)ShowWindow(GetConsoleWindow(),SW_HIDE);
 AppState s;s.instance=instance;create_main(s,true);
 MSG m{};while(GetMessageW(&m,nullptr,0,0)>0){TranslateMessage(&m);DispatchMessageW(&m);}return(int)m.wParam;
}
Json gui_smoke(HINSTANCE instance){
 auto resolvedTaskRoot=task_tool_root();need(fs::is_directory(resolvedTaskRoot),"task center runtime root unavailable");
 INITCOMMONCONTROLSEX ic{sizeof(ic),ICC_LISTVIEW_CLASSES|ICC_PROGRESS_CLASS|ICC_STANDARD_CLASSES};InitCommonControlsEx(&ic);
 AppState s;s.instance=instance;s.smoke=true;auto w=create_main(s,false);
 int controls=0;for(int id:{IDC_PATH,IDC_BROWSE,IDC_CREATE_PLAN,IDC_CHECK,IDC_APPROVE,IDC_OPEN_MASTER,IDC_RUN,IDC_VERIFY,IDC_SELFTEST,IDC_OPEN_FOLDER,IDC_BACKEND,IDC_FILTER,IDC_WORKERS,IDC_MEMORY,IDC_LIST,IDC_STATUS,IDC_LOG,IDC_PROGRESS,IDC_EXPAND,IDC_MOTION,IDC_TASK_CENTER,IDC_APNG,IDC_CHECKERBOARD})controls+=GetDlgItem(w,id)!=nullptr;need(controls==23,"GUI controls missing");
 need(window_is_centered(w,nullptr),"main window must start centered in monitor work area");
 need(wtext(w)==APP_TITLE,"GUI window title must describe the actual animation tool");
 need(GetDlgItem(w,IDC_CAPABILITIES)==s.capabilities&&wtext(s.capabilities)==CAPABILITIES_TITLE,
      "GUI must distinguish active production capabilities from planned video work");
 s.isMotion=true;set_busy(s,false);bool motionApprovalDisabled=!IsWindowEnabled(s.approve),motionPlanDisabled=!IsWindowEnabled(s.createPlan);need(motionApprovalDisabled&&motionPlanDisabled,"motion cannot grant approval/reseal current batch");s.isMotion=false;s.isMotionSpec=true;set_busy(s,false);need(!IsWindowEnabled(s.approve)&&IsWindowEnabled(s.createPlan),"motion spec sealing UI state");
 ExpansionSetup setup;setup.font=s.font;auto dialog=create_expansion_setup(w,setup,false);need(window_is_centered(dialog,w),"expansion setup must center on owner monitor");int setupControls=0;for(int id:{201,202,203,204,205,206,207,208,209,210,211})setupControls+=GetDlgItem(dialog,id)!=nullptr;auto modes=(int)SendMessageW(GetDlgItem(dialog,207),CB_GETCOUNT,0,0);bool roiMaskControls=GetDlgItem(dialog,210)!=nullptr&&GetDlgItem(dialog,211)!=nullptr;SendMessageW(dialog,WM_CLOSE,0,0);need(setup.done&&setupControls==11&&modes==8&&roiMaskControls,"expansion setup lifecycle/frame-set choices");
 TaskCenterSetup task;task.font=s.font;auto taskWindow=create_task_center_window(w,task,false);int taskControls=0;for(int id:{IDC_TASK_KIND,IDC_TASK_STAGE,IDC_TASK_INPUT,IDC_TASK_BROWSE_INPUT,IDC_TASK_SHA,IDC_TASK_OUTPUT,IDC_TASK_BROWSE_OUTPUT,IDC_TASK_OPERATION,IDC_TASK_NOTE,IDC_TASK_START,IDC_TASK_CANCEL,IDC_TASK_OPEN_OUTPUT,IDC_TASK_QUALITY_PROFILE})taskControls+=GetDlgItem(taskWindow,id)!=nullptr;need(taskControls==13,"task-center controls missing");
 need(window_is_centered(taskWindow,w),"task center must start centered on owner monitor");
 need(task.kind!=nullptr&&GetDlgItem(taskWindow,IDC_TASK_KIND)==task.kind,
      "task center initial focus control must exist");
 need(SendMessageW(task.kind,CB_GETCOUNT,0,0)==(LRESULT)task_commands().size(),"task selector must expose every actual route");
 for(size_t i=0;i<task_commands().size();++i){
  auto length=SendMessageW(task.kind,CB_GETLBTEXTLEN,(WPARAM)i,0);need(length>=0&&length<=4096,"task selector label missing");
  std::wstring label((size_t)length+1,L'\0');SendMessageW(task.kind,CB_GETLBTEXT,(WPARAM)i,(LPARAM)label.data());label.resize((size_t)length);
  need(label.find(wide(task_commands()[i]))!=std::wstring::npos,"task selector and command order differ");
 }
 auto argumentProbe=CompanionTaskRequest{"alpha-benchmark","check","0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","probe-id",fs::path(L"C:\\冻结 输入\\manifest.json"),fs::path(L"D:\\reports\\probe-id")};
 int newTaskRoutes=0,reviewTaskRoutes=0;
 for(const auto&command:task_commands()){
  auto probe=argumentProbe;probe.command=command;probe.stage=task_command_has_stage(command)?"verify":"run";
  auto route=companion_task_arguments(probe);
  if(task_command_has_stage(command)){
   need(route.size()==10&&route[0]==wide(command)&&route[1]==L"verify"&&route[2]==L"--manifest"&&route[4]==L"--manifest-sha256"&&route[6]==L"--outdir"&&route[8]==L"--operation-id","shared staged companion route shape");++newTaskRoutes;
  }else if(command=="native-review"||command=="town-flag-review"||command=="flag-pole-audit"){
   need(route.size()==7&&route[1]==L"--manifest"&&route[3]==L"--manifest-sha256"&&route[5]==L"--outdir","native-review route shape");++reviewTaskRoutes;
  }else{
   need(command=="motion-review"&&route.size()==5&&route[1]==L"--run-report"&&route[3]==L"--outdir","motion-review route shape");++reviewTaskRoutes;
  }
 }
 auto directFlagProbe=argumentProbe;directFlagProbe.command="flag-pole-audit";directFlagProbe.input=fs::path(L"C:\\候选\\AVCCAST0.png");
 auto directFlagRoute=companion_task_arguments(directFlagProbe);
 need(directFlagRoute.size()==7&&directFlagRoute[1]==L"--image"&&directFlagRoute[3]==L"--image-sha256"&&directFlagRoute[5]==L"--outdir","flag-pole direct PNG route shape");
 directFlagProbe.input=fs::path(L"C:\\候选\\AVMSAWR0\\0_0.png");
 directFlagRoute=companion_task_arguments(directFlagProbe);
 need(directFlagRoute.size()==7&&directFlagRoute[2]==directFlagProbe.input.wstring(),"flag-pole native slot PNG route preserves parent identity");
 need(newTaskRoutes+reviewTaskRoutes==(int)task_commands().size(),"task center routes all current companion types");
 need(task_stage_name(0)=="check"&&task_stage_name(1)=="run"&&task_stage_name(2)=="verify","standard companion stages");
 QualityProfileSetup quality;quality.font=s.font;auto qualityWindow=create_quality_profile_window(taskWindow,quality,false);
 need(window_is_centered(qualityWindow,taskWindow),"quality profile must be centered");
 need(SendMessageW(quality.preset,CB_GETCOUNT,0,0)==6,"six distinct quality strategy presets required");
 for(int id:{2401,2402,2403,2404,2405,2406,2407,2408,2422,2426,2427,2428,2430,2431,2432,2433})need(GetDlgItem(qualityWindow,id)!=nullptr,"quality profile control missing");
 SendMessageW(qualityWindow,WM_CLOSE,0,0);
 auto quoted=companion_quote_argument(argumentProbe.input.wstring());need(quoted.size()>2&&quoted.front()==L'"'&&quoted.back()==L'"'&&quoted.find(L"冻结 输入")!=std::wstring::npos,"companion argument quoting for spaced Unicode path");
 SendMessageW(taskWindow,WM_CLOSE,0,0);
 auto icon=LoadIconW(instance,MAKEINTRESOURCEW(IDI_APP_ICON));need(icon!=nullptr,"embedded icon missing");
 DestroyWindow(w);
  auto defaultPlan=default_project_plan();
  return{{"schema","h3.tier-export.ui-smoke.v1"},{"passed",true},{"controls",controls},{"expected_controls",23},{"task_center_controls",taskControls},{"shared_companion_route",true},{"task_root_resolved",true},{"task_tool_root",str(resolvedTaskRoot)},{"same_exe_cli_runner","companion_run_process"},{"unicode_path_quoted",true},{"new_task_routes",newTaskRoutes},{"review_task_routes",reviewTaskRoutes},{"standard_stages",Json::array({"check","run","verify"})},{"motion_approval_disabled",motionApprovalDisabled},{"motion_batch_reseal_disabled",motionPlanDisabled},{"expansion_setup_controls",setupControls},{"expansion_frame_set_modes",modes},{"expansion_reference_root_control",true},{"expansion_animation_roi_control",roiMaskControls},{"default_project_plan",str(defaultPlan)},{"icon_resource",true},{"files_written",0},{"main_window_centered",true},{"task_center_centered",true},{"expansion_setup_centered",true},{"capabilities_title",utf8(CAPABILITIES_TITLE)},{"window_title",utf8(APP_TITLE)},{"video_production_available",false}};
}
Json apng_selftest(){return apngui::selftest();}
} // namespace h3tier

