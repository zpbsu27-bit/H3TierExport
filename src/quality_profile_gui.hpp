// Included inside the existing task-center namespace; uses the same EXE route.
struct QualityProfileSetup {
 HWND window{},preset{},masters{},identities{},groups{},frames{},inventory{},standard{},profile{};
 HFONT font{};bool done=false;bool detect=false;fs::path manifest;
};
const std::vector<std::string>& quality_presets(){
 static const std::vector<std::string> names={"creature-actions","town-animation","mounted-heroes","roads-rivers","ground","road-connection"};return names;
}
fs::path quality_input_path(HWND control,const char*label){
 auto value=fs::path(wtext(control));
 need(!value.empty(),std::string(label)+": 请选择或填写完整本地路径");
 try{return clean_path(value.make_preferred());}
 catch(const std::exception&e){throw std::runtime_error(std::string(label)+": "+utf8(value.wstring())+"; "+e.what());}
}
void quality_profile_create(QualityProfileSetup&s,bool detect){
 auto masters=quality_input_path(s.masters,"masters root");need(fs::is_directory(masters),"请选择身份目录的共同父目录，不直接选择单张图片");
 auto operation=L"quality-"+local_stamp()+L"-"+std::to_wstring(GetTickCount64());
 auto output=task_tool_root()/L"build"/L"experiments"/L"quality-requests"/operation;
 std::vector<std::wstring> args{L"scale-layout"};
 auto existing=wtext(s.profile);
 if(!existing.empty()){
  auto p=quality_input_path(s.profile,"production profile");args.insert(args.end(),{L"quality-freeze",L"--profile",p.wstring(),L"--profile-sha256",wide(sha(read_all(p,32*1024*1024)))});
 }else{
  int selected=(int)SendMessageW(s.preset,CB_GETCURSEL,0,0);need(selected>=0&&selected<(int)quality_presets().size(),"请选择素材类别");
  auto inventory=quality_input_path(s.inventory,"official inventory"),standard=quality_input_path(s.standard,"art standard");
  args.insert(args.end(),{L"quality-init",L"--preset",wide(quality_presets()[selected]),L"--inventory",inventory.wstring(),L"--inventory-sha256",wide(sha(read_all(inventory,128*1024*1024))),L"--art-standard",standard.wstring(),L"--art-standard-sha256",wide(sha(read_all(standard,32*1024*1024)))});
  auto ids=wtext(s.identities);for(auto&c:ids)if(c==L','||c==L';'||c==L'\r'||c==L'\n'||c==L'\t')c=L' ';
  std::wistringstream stream(ids);std::wstring id;bool any=false;
  while(stream>>id){args.insert(args.end(),{L"--identity",id});any=true;}need(any,"填写官方身份ID，多个用空格或逗号分隔；不按现有图片推断任务总数");
  if(!wtext(s.groups).empty())args.insert(args.end(),{L"--groups",wtext(s.groups)});
  if(!wtext(s.frames).empty())args.insert(args.end(),{L"--frames",wtext(s.frames)});
 }
 args.insert(args.end(),{L"--masters-root",masters.wstring(),L"--operation-id",operation});
 DWORD error=0;auto code=companion_run_process(module_path().wstring(),args,error,60000);
 need(code==0&&error==ERROR_SUCCESS,"档案创建被原CLI合同阻断；请核对路径、官方ID和过滤范围。未改写图片，也未绕过校验。");
 s.manifest=output/L"manifest.json";need(fs::is_regular_file(s.manifest),"正式CLI未产生档案清单");
 s.detect=detect;s.done=true;SendMessageW(s.window,WM_CLOSE,0,0);
 if(!detect)ShellExecuteW(nullptr,L"open",(output/L"generation-brief.md").c_str(),nullptr,output.c_str(),SW_SHOWNORMAL);
}
void quality_profile_road_batch(QualityProfileSetup&s){
 auto inventory=quality_input_path(s.inventory,"official inventory"),standard=quality_input_path(s.standard,"art standard");
 auto registry=task_tool_root()/L"records"/L"map-consumer-model"/L"current.json";
 need(fs::is_regular_file(registry),"尚未登记经源函数验证的地图模型；请先由工具模型注册入口安装。缺模型不会把批量拼接判为通过。");
 auto sampled=choose_json(s.window,fs::path(),L"选择实际3X采样批次 motion-run.json（不会修改图片）");if(!sampled)return;
 auto operation=L"road-batch-"+local_stamp()+L"-"+std::to_wstring(GetTickCount64());
 std::vector<std::wstring> args{L"scale-layout",L"road-batch-freeze",L"--inventory",inventory.wstring(),L"--inventory-sha256",wide(sha(read_all(inventory,128*1024*1024))),
  L"--art-standard",standard.wstring(),L"--art-standard-sha256",wide(sha(read_all(standard,32*1024*1024))),
  L"--sampling-run",sampled->wstring(),L"--sampling-run-sha256",wide(sha(read_all(*sampled,32*1024*1024))),L"--operation-id",operation};
 auto ids=wtext(s.identities);for(auto&c:ids)if(c==L','||c==L';'||c==L'\r'||c==L'\n'||c==L'\t')c=L' ';
 std::wistringstream stream(ids);std::wstring id;bool any=false;while(stream>>id){args.insert(args.end(),{L"--identity",id});any=true;}
 need(any,"填写本轮道路官方身份，例如 COBBRD；整套17槽依据原版inventory自动确定。");
 DWORD error=0;auto code=companion_run_process(module_path().wstring(),args,error,120000);
 need(code==0&&error==ERROR_SUCCESS,"实际3X批次或地图模型核验失败。未改写、缩放或删除原图。");
 auto manifest=task_tool_root()/L"build"/L"experiments"/L"road-batch-requests"/operation/L"manifest.json";
 need(fs::is_regular_file(manifest),"批量拼接冻结清单未产生");
 auto output=task_tool_root()/L"build"/L"experiments"/L"road-batch-quality"/operation;
 args={L"scale-layout",L"road-batch-run",L"--manifest",manifest.wstring(),L"--manifest-sha256",wide(sha(read_all(manifest,32*1024*1024))),L"--outdir",output.wstring(),L"--operation-id",operation};
 code=companion_run_process(module_path().wstring(),args,error,120000);
 need(code==0&&error==ERROR_SUCCESS,"批量拼接检测未完成，请核对冻结输入；未改写图片。");
 need(fs::is_regular_file(output/L"road-batch-report.json"),"批量拼接报告未产生");
 ShellExecuteW(nullptr,L"open",(output/L"index.html").c_str(),nullptr,output.c_str(),SW_SHOWNORMAL);
 s.done=true;SendMessageW(s.window,WM_CLOSE,0,0);
}
LRESULT CALLBACK quality_profile_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
 auto*s=(QualityProfileSetup*)GetWindowLongPtrW(w,GWLP_USERDATA);
 if(message==WM_NCCREATE){s=(QualityProfileSetup*)((CREATESTRUCTW*)lp)->lpCreateParams;s->window=w;SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)s);}
 if(!s)return DefWindowProcW(w,message,wp,lp);
 try{
  if(message==WM_CREATE){
   auto ctl=[&](const wchar_t*klass,const wchar_t*text,DWORD style,int x,int y,int width,int height,int id){
    auto h=CreateWindowExW(0,klass,text,WS_CHILD|WS_VISIBLE|style,x,y,width,height,w,(HMENU)(INT_PTR)id,GetModuleHandleW(nullptr),nullptr);need(h!=nullptr,"quality-profile control");font(h,s->font);return h;};
   auto field=[&](const wchar_t*label,int y,int id){ctl(L"STATIC",label,0,18,y+3,190,24,id+100);return ctl(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL,210,y,555,28,id);};
   ctl(L"STATIC",L"素材类别",0,18,23,180,24,2490);s->preset=ctl(WC_COMBOBOXW,L"",CBS_DROPDOWNLIST|WS_VSCROLL,210,18,555,180,2401);
   for(auto label:{L"生物动作：身体 / 官方顺序 / 同偏移",L"动态建筑：底座 / 动区 / 循环",L"骑乘人物：骑手与坐骑 / 蹄底 / 朝向",L"河流道路：接口 / 接缝 / 流向",L"地面：不透明 / 铺贴 / 材质",L"道路拼接：原版槽位 / 真实地图格 / 实际3X"})SendMessageW(s->preset,CB_ADDSTRING,0,(LPARAM)label);
   SendMessageW(s->preset,CB_SETCURSEL,0,0);
   s->masters=field(L"母版身份目录的父目录",62,2402);ctl(L"BUTTON",L"浏览…",BS_PUSHBUTTON,775,62,85,28,2422);
   s->identities=field(L"官方身份（空格分隔）",106,2403);
   s->groups=field(L"官方组（空=全部）",150,2404);s->frames=field(L"官方帧（空=全部）",194,2405);
   s->inventory=field(L"官方inventory路径",238,2406);ctl(L"BUTTON",L"浏览…",BS_PUSHBUTTON,775,238,85,28,2426);
   s->standard=field(L"唯一美术规范路径",282,2407);ctl(L"BUTTON",L"浏览…",BS_PUSHBUTTON,775,282,85,28,2427);
   s->profile=field(L"复用已定制作档案（可空）",326,2408);ctl(L"BUTTON",L"浏览…",BS_PUSHBUTTON,775,326,85,28,2428);
   ctl(L"STATIC",L"画前：生成绑定官方槽位、规范SHA和类别策略的MD及档案草案。填写真实形象参照、身体尺度、工作画布与相应检测区域后，复用同一档案。\r\n画后：重新冻结当前图片SHA，再一键运行。缺图/多图、明确错误、风险、未测量分别列出；身体大小不用Alpha包围框冒充。\r\n多肢少肢/人物身份需要有效语义依据；未安装本地模型不会自动记通过。此工具不自动删除或改写母图。",SS_LEFT|SS_NOPREFIX,18,375,842,110,2491);
   ctl(L"BUTTON",L"生成画前档案",BS_PUSHBUTTON,170,505,180,34,2430);ctl(L"BUTTON",L"冻结并一键检测",BS_DEFPUSHBUTTON,365,505,200,34,2431);ctl(L"BUTTON",L"取消",BS_PUSHBUTTON,650,505,100,34,2432);
   ctl(L"BUTTON",L"读取3X批次：道路全槽与合法配对自动检测",BS_PUSHBUTTON,210,550,500,34,2433);
   auto workspace=default_workspace_root();if(!workspace.empty()){
    auto release=workspace/L"CompleteOfficialRelease-20261006-v1";
    settext(s->inventory,(release/L"codex-map"/L"native-resource-inventory.json.gz").wstring());
    settext(s->standard,(release/L"codex-高清母版"/L"批量生图工具"/L"美术标准"/L"town-art-direction-canonical.json").wstring());
   }
   return 0;
  }
  if(message==WM_COMMAND&&HIWORD(wp)==BN_CLICKED){
   int id=LOWORD(wp);
   if(id==2422){auto chosen=choose_output_parent(w,fs::path(wtext(s->masters)));if(chosen)settext(s->masters,chosen->wstring());return 0;}
   if(id==2426||id==2427||id==2428){auto target=id==2426?s->inventory:id==2427?s->standard:s->profile;auto chosen=choose_json(w,fs::path(wtext(target)),L"选择绑定的JSON文件（inventory可为json.gz）");if(chosen)settext(target,chosen->wstring());return 0;}
   if(id==2430||id==2431){quality_profile_create(*s,id==2431);return 0;}
   if(id==2433){quality_profile_road_batch(*s);return 0;}
   if(id==2432){SendMessageW(w,WM_CLOSE,0,0);return 0;}
  }
  if(message==WM_CLOSE){s->done=true;DestroyWindow(w);return 0;}
 }catch(const std::exception&e){MessageBoxW(w,wide(e.what()).c_str(),L"制作档案与成品检测",MB_OK|MB_ICONERROR);return 0;}
 return DefWindowProcW(w,message,wp,lp);
}
HWND create_quality_profile_window(HWND owner,QualityProfileSetup&setup,bool show){
 static std::once_flag once;std::call_once(once,[]{WNDCLASSW c{};c.lpfnWndProc=quality_profile_proc;c.hInstance=GetModuleHandleW(nullptr);c.hCursor=LoadCursorW(nullptr,IDC_ARROW);c.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);c.lpszClassName=L"H3TierExport.QualityProfile";need(RegisterClassW(&c)!=0,"register quality profile");});
 auto w=CreateWindowExW(WS_EX_DLGMODALFRAME|WS_EX_CONTROLPARENT,L"H3TierExport.QualityProfile",L"画前统一制作档案 / 画后分类检测",WS_CAPTION|WS_SYSMENU|WS_POPUP,CW_USEDEFAULT,CW_USEDEFAULT,900,650,owner,nullptr,GetModuleHandleW(nullptr),&setup);
 need(w!=nullptr,"create quality profile dialog");center_window(w,owner);if(show)ShowWindow(w,SW_SHOW);return w;
}
void quality_profile_dialog(TaskCenterSetup&task){
 QualityProfileSetup setup;setup.font=task.font;
 auto w=create_quality_profile_window(task.window,setup,true);EnableWindow(task.window,FALSE);MSG msg{};
 while(!setup.done){auto r=GetMessageW(&msg,nullptr,0,0);if(r<=0){if(IsWindow(w))DestroyWindow(w);if(!r)PostQuitMessage(0);break;}if(!IsDialogMessageW(w,&msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}}
 EnableWindow(task.window,TRUE);SetActiveWindow(task.window);
 if(!setup.manifest.empty()){
  auto commands=task_commands();auto target=std::find(commands.begin(),commands.end(),"scale-layout");
  need(target!=commands.end(),"quality profile requires scale-layout task route");
  SendMessageW(task.kind,CB_SETCURSEL,(WPARAM)std::distance(commands.begin(),target),0);task.outputEdited=false;task.operationEdited=false;task_update_mode(task);task_load_input(task,setup.manifest);
  if(setup.detect){SendMessageW(task.stage,CB_SETCURSEL,1,0);task_collect_request(task);}
 }
}
