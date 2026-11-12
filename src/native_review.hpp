#pragma once

// Read-only native-resolution visual review. This renderer consumes an
// explicit source/geometry spec, but does not parse the canonical gzip
// inventory; review_native_batch.py owns that binding.
#include "wic.hpp"
#include <winternl.h>
#include <memory>
#include <cstddef>
#include <numeric>
#include <cctype>

namespace h3tier {

struct NativeReviewTownInput {
 fs::path file;
 std::string digest;
 std::size_t bytes=0;
};

struct NativeReviewItem {
 std::string id;
 Json key,targets,reference;
 Json atlasView=nullptr,stateView=nullptr,alphaCap=nullptr;
 fs::path source,referencePath;
 fs::path alphaCapPath;
 std::string sourceSha,referenceSha,registration,sourceLayout="native-crop",outputLayout="native-canvas";
 std::array<int,2> sourcePixels{},crop{},canvas{},origin{};
 Rect sourceRect;
 Rect atlasRect;
 std::array<int,2> atlasPixels{};
 bool hasAtlas=false,hasStateView=false,paddingRegistrationExplicit=false;
 bool sourceRectExplicit=false,hasReference=false,render1x=true,render3x=false;
 int commonK=0,registrationLeft=0,registrationTop=0;
 std::uint64_t sourcePositive=0,viewPositive=0;
 Json foregroundSelection=nullptr,foregroundProof=nullptr;
 std::vector<std::uint8_t> foregroundMask;
 std::vector<NativeReviewTownInput> foregroundInputs;
};

struct NativeReviewFileIdentity {
 DWORD volume=0,indexHigh=0,indexLow=0;
 bool operator==(const NativeReviewFileIdentity& other)const{
  return volume==other.volume&&indexHigh==other.indexHigh&&indexLow==other.indexLow;
 }
};
inline NativeReviewFileIdentity native_review_identity(HANDLE handle,const char* label){
 BY_HANDLE_FILE_INFORMATION info{};
 need(GetFileInformationByHandle(handle,&info),std::string("REVIEW_OUTPUT_IDENTITY_UNAVAILABLE: ")+label);
 return {info.dwVolumeSerialNumber,info.nFileIndexHigh,info.nFileIndexLow};
}
inline DWORD native_review_attributes(HANDLE handle,const char* label){
 BY_HANDLE_FILE_INFORMATION info{};
 need(GetFileInformationByHandle(handle,&info),std::string("REVIEW_OUTPUT_IDENTITY_UNAVAILABLE: ")+label);
 return info.dwFileAttributes;
}
inline fs::path native_review_output_path(fs::path supplied){
 need(!supplied.empty(),"output path required");
 for(const auto& component:supplied)need(component!=L"..","parent traversal rejected");
 auto value=fs::absolute(supplied).lexically_normal();auto text=value.wstring();
 need(text.size()>=3&&text[1]==L':'&&text[2]==L'\\',"only local absolute drive paths supported");
 need(text.find(L':',2)==std::wstring::npos,"alternate data stream rejected");
 return value;
}
inline std::wstring native_review_path_key(const fs::path& value){
 auto text=native_review_output_path(value).wstring();
 for(auto& c:text)c=(wchar_t)towlower(c);
 return text;
}
inline bool native_review_within(const fs::path& child,const fs::path& parent){
 auto c=native_review_path_key(child),p=native_review_path_key(parent);if(p.back()!=L'\\')p+=L'\\';
 return c.size()>p.size()&&c.compare(0,p.size(),p)==0;
}
inline bool native_review_overlap(const fs::path& a,const fs::path& b){
 auto x=native_review_path_key(a),y=native_review_path_key(b);
 auto inside=[](const std::wstring& child,const std::wstring& parent){
  auto prefix=parent;if(prefix.back()!=L'\\')prefix+=L'\\';
  return child.size()>prefix.size()&&child.compare(0,prefix.size(),prefix)==0;
 };
 return x==y||inside(x,y)||inside(y,x);
}

struct NativeReviewLockedDirectory {
 fs::path path;
 std::unique_ptr<Handle> pathHandle;
 std::unique_ptr<Handle> deleteHandle;
 NativeReviewFileIdentity identity;
 bool created=false;
};
struct NativeReviewCreatedFile {
 fs::path path;
 std::unique_ptr<Handle> handle;
 NativeReviewFileIdentity identity;
};

// Existing directory locks request LIST_DIRECTORY as well as attributes;
// attributes-only handles do not prevent a rename on Windows. A new directory
// keeps its original DELETE-capable NtCreateFile handle as the lock and
// duplicates that same open for identity tracking. Temporary path probes share
// DELETE so they coexist with it, while the original handle still denies
// external delete/rename requests.
class NativeReviewOutputGuard {
 std::vector<NativeReviewLockedDirectory> directories_;
 std::vector<NativeReviewCreatedFile> files_;
 bool keep_=false,cleanupDone_=false;
 static constexpr DWORD directoryFlags_=FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT;
 static constexpr DWORD directoryLockedShare_=FILE_SHARE_READ|FILE_SHARE_WRITE;
 static constexpr DWORD directoryCompatibleShare_=FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE;

 NativeReviewLockedDirectory* find_directory(const fs::path& value){
  const auto key=native_review_path_key(value);
  for(auto& entry:directories_)if(native_review_path_key(entry.path)==key)return &entry;
  return nullptr;
 }
 const NativeReviewLockedDirectory* find_directory(const fs::path& value)const{
  const auto key=native_review_path_key(value);
  for(const auto& entry:directories_)if(native_review_path_key(entry.path)==key)return &entry;
  return nullptr;
 }
 static Handle open_directory(const fs::path& value,const char* label,
                              DWORD desiredAccess=FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,
                              DWORD shareAccess=directoryLockedShare_){
  Handle handle(CreateFileW(value.c_str(),desiredAccess,shareAccess,nullptr,
                            OPEN_EXISTING,directoryFlags_,nullptr));
  need(handle.h!=INVALID_HANDLE_VALUE,std::string("REVIEW_OUTPUT_DIRECTORY_OPEN_FAILED: ")+label+
       " winerr="+std::to_string(GetLastError()));
  const auto attributes=native_review_attributes(handle,label);
  need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0,"REVIEW_OUTPUT_NOT_DIRECTORY: "+std::string(label));
  need((attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0,"REVIEW_OUTPUT_REPARSE_POINT: "+std::string(label));
  return handle;
 }
 static void verify_directory(const NativeReviewLockedDirectory& entry){
  need(entry.pathHandle&&entry.pathHandle->h!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_DIRECTORY_HANDLE_CLOSED");
  auto attributes=native_review_attributes(*entry.pathHandle,"locked directory");
  need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0&&
       native_review_identity(*entry.pathHandle,"locked directory")==entry.identity,
       "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
  auto byPath=open_directory(entry.path,"directory path verification",FILE_READ_ATTRIBUTES,
                             directoryCompatibleShare_);
  attributes=native_review_attributes(byPath,"directory path verification");
  need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0&&
       native_review_identity(byPath,"directory path verification")==entry.identity,
       "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
  if(entry.deleteHandle&&entry.deleteHandle->h!=INVALID_HANDLE_VALUE)
   need(native_review_identity(*entry.deleteHandle,"created directory") == entry.identity,
        "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
 }
 void verify_chain(const fs::path& requested)const{
  // Sibling locks stay held; each mutation checks its complete path to the
  // drive root. Sealing and successful completion still audit every sibling.
  const auto value=native_review_output_path(requested),root=value.root_path();
  need(!directories_.empty()&&native_review_path_key(directories_.front().path)==native_review_path_key(root),
       "REVIEW_OUTPUT_ROOT_CHANGED");
  auto verify=[&](const fs::path& directory){
   const auto entry=find_directory(directory);need(entry!=nullptr,"REVIEW_OUTPUT_PARENT_NOT_LOCKED");
   verify_directory(*entry);
  };
  auto current=root;verify(current);
  for(const auto& component:value.relative_path()){current/=component;verify(current);}
 }
 void add_existing_directory(const fs::path& value){
  auto handle=open_directory(value,"existing directory");
  const auto identity=native_review_identity(handle,"existing directory");
  NativeReviewLockedDirectory entry;entry.path=value;entry.identity=identity;
  entry.pathHandle=std::make_unique<Handle>(std::move(handle));directories_.push_back(std::move(entry));
 }
 static std::wstring nt_status_text(NTSTATUS status){
  std::wostringstream out;out<<L"0x"<<std::hex<<std::uppercase<<(ULONG)status;return out.str();
 }
 Handle create_directory_relative(const NativeReviewLockedDirectory& parent,const std::wstring& leaf){
  Handle parentAccess(CreateFileW(parent.path.c_str(),FILE_ADD_SUBDIRECTORY|FILE_READ_ATTRIBUTES,
      directoryCompatibleShare_,nullptr,OPEN_EXISTING,directoryFlags_,nullptr));
  need(parentAccess.h!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_PARENT_CREATE_ACCESS_FAILED: winerr="+
       std::to_string(GetLastError()));
  need(native_review_identity(parentAccess,"parent create access")==parent.identity&&
       (native_review_attributes(parentAccess,"parent create access")&FILE_ATTRIBUTE_REPARSE_POINT)==0,
       "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
  auto module=GetModuleHandleW(L"ntdll.dll");
  need(module!=nullptr,"REVIEW_NT_CREATE_DIRECTORY_UNAVAILABLE");
  using NtCreateFileFn=NTSTATUS (NTAPI *)(PHANDLE,ACCESS_MASK,POBJECT_ATTRIBUTES,PIO_STATUS_BLOCK,
      PLARGE_INTEGER,ULONG,ULONG,ULONG,ULONG,PVOID,ULONG);
  auto create=reinterpret_cast<NtCreateFileFn>(GetProcAddress(module,"NtCreateFile"));
  need(create!=nullptr,"REVIEW_NT_CREATE_DIRECTORY_UNAVAILABLE");
  UNICODE_STRING name{};name.Buffer=const_cast<PWSTR>(leaf.c_str());
  name.Length=(USHORT)(leaf.size()*sizeof(wchar_t));name.MaximumLength=name.Length;
  OBJECT_ATTRIBUTES attributes{};attributes.Length=sizeof(attributes);attributes.RootDirectory=parentAccess.h;
  attributes.ObjectName=&name;attributes.Attributes=OBJ_CASE_INSENSITIVE;
  IO_STATUS_BLOCK io{};HANDLE created=INVALID_HANDLE_VALUE;
  constexpr ULONG FILE_DIRECTORY_FILE_VALUE=0x00000001;
  constexpr ULONG FILE_SYNCHRONOUS_IO_NONALERT_VALUE=0x00000020;
  constexpr ULONG FILE_OPEN_REPARSE_POINT_VALUE=0x00200000;
  const auto status=create(&created,DELETE|FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES|SYNCHRONIZE,
      &attributes,&io,nullptr,FILE_ATTRIBUTE_DIRECTORY,directoryLockedShare_,2,
      FILE_DIRECTORY_FILE_VALUE|FILE_OPEN_REPARSE_POINT_VALUE|FILE_SYNCHRONOUS_IO_NONALERT_VALUE,nullptr,0);
  if(status<0){
   if((ULONG)status==0xC0000035u)throw std::runtime_error("REVIEW_OUTPUT_ALREADY_EXISTS");
   throw std::runtime_error("REVIEW_OUTPUT_DIRECTORY_CREATE_FAILED: NTSTATUS "+utf8(nt_status_text(status)));
  }
  need(created!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_DIRECTORY_CREATE_FAILED: missing handle");
  return Handle(created);
 }
 void add_created_directory(const fs::path& value,const NativeReviewLockedDirectory& parent){
  const auto leaf=value.filename().wstring();need(!leaf.empty()&&leaf.find_first_of(L"\\/")==std::wstring::npos,
      "REVIEW_OUTPUT_DIRECTORY_COMPONENT_INVALID");
  Handle deleteHandle=create_directory_relative(parent,leaf);
  const auto identity=native_review_identity(deleteHandle,"new directory");
  const auto attributes=native_review_attributes(deleteHandle,"new directory");
  need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0,
       "REVIEW_OUTPUT_REPARSE_POINT");
  NativeReviewLockedDirectory entry;entry.path=value;entry.identity=identity;entry.created=true;
  entry.deleteHandle=std::make_unique<Handle>(std::move(deleteHandle));
  directories_.push_back(std::move(entry));
  auto tracked=find_directory(value);need(tracked!=nullptr,"REVIEW_OUTPUT_DIRECTORY_NOT_TRACKED");
  HANDLE duplicated=INVALID_HANDLE_VALUE;
  const BOOL duplicateOk=DuplicateHandle(GetCurrentProcess(),*tracked->deleteHandle,GetCurrentProcess(),&duplicated,
                                         0,FALSE,DUPLICATE_SAME_ACCESS);
  const DWORD duplicateError=duplicateOk?ERROR_SUCCESS:GetLastError();
  need(duplicateOk!=FALSE,"REVIEW_OUTPUT_DIRECTORY_DUPLICATE_FAILED: winerr="+
       std::to_string(duplicateError));
  Handle pathHandle(duplicated);
  need(native_review_identity(pathHandle,"new directory duplicate")==tracked->identity,
       "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
  tracked->pathHandle=std::make_unique<Handle>(std::move(pathHandle));
 }
 void ensure_child(const fs::path& value,bool createIfMissing){
  if(find_directory(value))return;
  auto parent=find_directory(value.parent_path());need(parent!=nullptr,"REVIEW_OUTPUT_PARENT_NOT_LOCKED");
  verify_chain(value.parent_path());
  Handle handle(CreateFileW(value.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,directoryLockedShare_,nullptr,
                            OPEN_EXISTING,directoryFlags_,nullptr));
  if(handle.h!=INVALID_HANDLE_VALUE){
   const auto attributes=native_review_attributes(handle,"existing directory");
   need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0,"REVIEW_OUTPUT_NOT_DIRECTORY: "+str(value));
   need((attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0,"REVIEW_OUTPUT_REPARSE_POINT: "+str(value));
   NativeReviewLockedDirectory entry;entry.path=value;entry.identity=native_review_identity(handle,"existing directory");
   entry.pathHandle=std::make_unique<Handle>(std::move(handle));directories_.push_back(std::move(entry));return;
  }
  const DWORD error=GetLastError();
  if(error!=ERROR_FILE_NOT_FOUND&&error!=ERROR_PATH_NOT_FOUND)
   throw std::runtime_error("REVIEW_OUTPUT_DIRECTORY_OPEN_FAILED: "+str(value)+" winerr="+std::to_string(error));
  if(!createIfMissing)throw std::runtime_error("REVIEW_OUTPUT_DIRECTORY_MISSING: "+str(value));
  try{add_created_directory(value,*parent);}
  catch(const std::exception& e){
   // Another actor may have won the create race. Re-open only for an
   // intermediate path; the caller still rejects a pre-existing leaf.
   if(std::string(e.what())!="REVIEW_OUTPUT_ALREADY_EXISTS")throw;
   Handle existing(CreateFileW(value.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,directoryLockedShare_,nullptr,
                               OPEN_EXISTING,directoryFlags_,nullptr));
   need(existing.h!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_DIRECTORY_OPEN_FAILED: "+str(value));
   const auto attributes=native_review_attributes(existing,"existing directory");
   need((attributes&FILE_ATTRIBUTE_DIRECTORY)!=0,"REVIEW_OUTPUT_NOT_DIRECTORY: "+str(value));
   need((attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0,"REVIEW_OUTPUT_REPARSE_POINT: "+str(value));
   NativeReviewLockedDirectory entry;entry.path=value;entry.identity=native_review_identity(existing,"existing directory");
   entry.pathHandle=std::make_unique<Handle>(std::move(existing));directories_.push_back(std::move(entry));
  }
 }
 void ensure_chain(const fs::path& requested,bool createIfMissing){
  auto value=native_review_output_path(requested);auto root=value.root_path();
  need(!root.empty(),"REVIEW_OUTPUT_DRIVE_ROOT_REQUIRED");
  if(directories_.empty())add_existing_directory(root);
  need(native_review_path_key(directories_.front().path)==native_review_path_key(root),
       "REVIEW_OUTPUT_ROOT_CHANGED");
  auto current=root;
  for(const auto& component:value.relative_path()){
   current/=component;
   if(find_directory(current))continue;
   ensure_child(current,createIfMissing);
  }
 }
 Handle open_parent_access(const fs::path& parent,DWORD requestedAccess){
  auto locked=find_directory(parent);need(locked&&locked->pathHandle&&locked->pathHandle->h!=INVALID_HANDLE_VALUE,
      "REVIEW_OUTPUT_PARENT_NOT_LOCKED");
  Handle access(CreateFileW(locked->path.c_str(),requestedAccess|FILE_READ_ATTRIBUTES,
      directoryCompatibleShare_,nullptr,OPEN_EXISTING,directoryFlags_,nullptr));
  need(access.h!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_PARENT_ACCESS_FAILED: winerr="+std::to_string(GetLastError()));
  need(native_review_identity(access,"parent access")==locked->identity&&
       (native_review_attributes(access,"parent access")&FILE_ATTRIBUTE_REPARSE_POINT)==0,
       "REVIEW_OUTPUT_DIRECTORY_IDENTITY_CHANGED");
  return access;
 }
public:
 NativeReviewOutputGuard()=default;
 NativeReviewOutputGuard(const NativeReviewOutputGuard&)=delete;
 NativeReviewOutputGuard& operator=(const NativeReviewOutputGuard&)=delete;
 ~NativeReviewOutputGuard(){if(!keep_&&!cleanupDone_)try{cleanup();}catch(...){}}
 void ensure_directory(const fs::path& value){ensure_chain(value,true);verify_chain(value);}
 void create_new_directory(const fs::path& value){
  auto output=native_review_output_path(value);ensure_chain(output.parent_path(),true);verify_chain(output.parent_path());
  auto parent=find_directory(output.parent_path());need(parent!=nullptr,"REVIEW_OUTPUT_PARENT_NOT_LOCKED");
  add_created_directory(output,*parent);verify_chain(output);
 }
 void verify_all()const{
  for(const auto& entry:directories_)verify_directory(entry);
 }
 void verify_parent(const fs::path& outputPath){
  verify_chain(outputPath.parent_path());
 }
 std::size_t create_temp_file(const fs::path& temporary){
  verify_parent(temporary);
  Handle handle(CreateFileW(temporary.c_str(),GENERIC_READ|GENERIC_WRITE|DELETE|FILE_READ_ATTRIBUTES,
      FILE_SHARE_READ|FILE_SHARE_DELETE,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL|FILE_FLAG_OPEN_REPARSE_POINT,nullptr));
  need(handle.h!=INVALID_HANDLE_VALUE,"REVIEW_OUTPUT_TEMP_CREATE_FAILED: winerr="+std::to_string(GetLastError()));
  BY_HANDLE_FILE_INFORMATION info{};
  need(GetFileInformationByHandle(handle,&info)&&!(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))&&
       info.nNumberOfLinks==1,"REVIEW_OUTPUT_TEMP_IDENTITY_INVALID");
  NativeReviewCreatedFile file;file.path=temporary;file.identity={info.dwVolumeSerialNumber,info.nFileIndexHigh,info.nFileIndexLow};
  file.handle=std::make_unique<Handle>(std::move(handle));files_.push_back(std::move(file));
  return files_.size()-1;
 }
 HANDLE file_handle(std::size_t index){need(index<files_.size()&&files_[index].handle,"REVIEW_OUTPUT_FILE_HANDLE_CLOSED");return *files_[index].handle;}
 void rename_created_file(std::size_t index,const fs::path& destination){
  need(index<files_.size()&&files_[index].handle,"REVIEW_OUTPUT_FILE_HANDLE_CLOSED");
  auto& file=files_[index];auto target=native_review_output_path(destination);
  need(native_review_path_key(target.parent_path())==native_review_path_key(file.path.parent_path()),
       "REVIEW_OUTPUT_RENAME_PARENT_CHANGED");verify_parent(target);
  // Keep the parent handle locked and publish inside that same directory.
  // MoveFileEx with no REPLACE flag is an atomic same-volume rename and is
  // supported by the Windows builds used here; the temporary handle shares
  // DELETE so the path operation can complete while its identity remains
  // pinned and is checked immediately afterward.
  auto parentAccess=open_parent_access(target.parent_path(),0x00000002); // FILE_ADD_FILE
  (void)parentAccess;
  const auto name=target.wstring();need(!name.empty()&&name.front()!=L'\\',
      "REVIEW_OUTPUT_FILE_NAME_INVALID");
  need(MoveFileExW(file.path.c_str(),name.c_str(),MOVEFILE_WRITE_THROUGH),
       "REVIEW_OUTPUT_ATOMIC_PUBLISH_FAILED: winerr="+std::to_string(GetLastError()));
  file.path=target;
  verify_parent(target);
  BY_HANDLE_FILE_INFORMATION fileInfo{};
  need(GetFileInformationByHandle(*file.handle,&fileInfo)&&
       NativeReviewFileIdentity{fileInfo.dwVolumeSerialNumber,fileInfo.nFileIndexHigh,fileInfo.nFileIndexLow}==file.identity&&
       !(fileInfo.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))&&fileInfo.nNumberOfLinks==1,
       "REVIEW_OUTPUT_FILE_IDENTITY_CHANGED");
 }
 std::string write_new(const fs::path& destination,const std::vector<std::uint8_t>& bytes){
  auto target=native_review_output_path(destination);verify_parent(target);
  static std::atomic<unsigned> sequence=0;
  auto temporary=target;temporary+=L".writing-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(sequence++);
  const auto index=create_temp_file(temporary);HANDLE file=file_handle(index);
  size_t at=0;while(at<bytes.size()){
   DWORD written=0;const DWORD ask=(DWORD)std::min<size_t>(bytes.size()-at,4*1024*1024);
   need(WriteFile(file,bytes.data()+at,ask,&written,nullptr)&&written==ask,"REVIEW_OUTPUT_SHORT_WRITE");at+=written;
  }
  need(FlushFileBuffers(file),"REVIEW_OUTPUT_FLUSH_FAILED");
  LARGE_INTEGER zero{};need(SetFilePointerEx(file,zero,nullptr,FILE_BEGIN),"REVIEW_OUTPUT_READBACK_SEEK_FAILED");
  std::vector<std::uint8_t> readback(bytes.size());at=0;
  while(at<readback.size()){
   DWORD got=0;const DWORD ask=(DWORD)std::min<size_t>(readback.size()-at,4*1024*1024);
   need(ReadFile(file,readback.data()+at,ask,&got,nullptr)&&got==ask,"REVIEW_OUTPUT_SHORT_READBACK");at+=got;
  }
  need(readback==bytes,"REVIEW_OUTPUT_READBACK_MISMATCH");verify_parent(target);rename_created_file(index,target);verify_parent(target);
  return sha(bytes);
 }

 // Publish a completely sealed, exclusively-created tree. Windows refuses
 // directory rename while descendants have open handles, even SHARE_DELETE.
 // Retain the root and all ancestors throughout; close only this guard's own
 // descendant handles after readback. No path writes occur after this point.
 // A failed switch is retained as staging evidence, never recursively removed.
 template<class SealVerifier>
 void publish_created_tree(const fs::path& source,const fs::path& destination,SealVerifier verifySeal){
  const auto from=native_review_output_path(source),to=native_review_output_path(destination);
  need(native_review_path_key(from.parent_path())==native_review_path_key(to.parent_path())&&
       native_review_path_key(from)!=native_review_path_key(to),"REVIEW_BATCH_RENAME_PARENT_INVALID");
  verify_all();
  const auto root=find_directory(from);
  need(root&&root->created&&root->deleteHandle,"REVIEW_BATCH_ROOT_NOT_OWNED");
  std::vector<std::pair<fs::path,NativeReviewFileIdentity>> childIdentities;
  for(const auto& entry:directories_)if(native_review_within(entry.path,from)){
   need(entry.created,"REVIEW_BATCH_DESCENDANT_NOT_OWNED");
   childIdentities.emplace_back(entry.path,entry.identity);
  }
  // Each write_new already flushed and byte-read-back its own write handle.
  // Strict input readers cannot reopen an output with our write handle present.
  // Preserve root and ancestor locks while releasing only owned descendants.
  // Then audit the complete sealed tree before its single namespace switch.
  for(const auto& file:files_)if(native_review_within(file.path,from)){
   need(file.handle&&native_review_identity(*file.handle,"sealed output")==file.identity,
        "REVIEW_BATCH_FILE_IDENTITY_CHANGED");
  }
  keep_=true;
  for(auto& file:files_)if(native_review_within(file.path,from))file.handle.reset();
  for(auto it=directories_.rbegin();it!=directories_.rend();++it)if(native_review_within(it->path,from)){
   it->pathHandle.reset();it->deleteHandle.reset();
  }
  files_.erase(std::remove_if(files_.begin(),files_.end(),[&](const auto& file){
   return native_review_within(file.path,from);}),files_.end());
  directories_.erase(std::remove_if(directories_.begin(),directories_.end(),[&](const auto& entry){
   return native_review_within(entry.path,from);}),directories_.end());
  verify_all();
  for(const auto& entry:childIdentities){
   auto handle=open_directory(entry.first,"sealed child",FILE_READ_ATTRIBUTES,directoryCompatibleShare_);
   need(native_review_identity(handle,"sealed child")==entry.second,"REVIEW_BATCH_SEALED_DIRECTORY_CHANGED");
  }
  verifySeal();
  verify_all();
  auto locked=find_directory(from);need(locked&&locked->deleteHandle,"REVIEW_BATCH_ROOT_HANDLE_CLOSED");
  const auto name=to.wstring();
  const auto size=sizeof(FILE_RENAME_INFO)+(name.size()+1)*sizeof(wchar_t);
  std::vector<std::uint8_t> storage(size,0);
  auto info=reinterpret_cast<FILE_RENAME_INFO*>(storage.data());
  info->ReplaceIfExists=FALSE;info->RootDirectory=nullptr;
  info->FileNameLength=(DWORD)(name.size()*sizeof(wchar_t));
  std::memcpy(info->FileName,name.c_str(),(name.size()+1)*sizeof(wchar_t));
  const BOOL renamed=SetFileInformationByHandle(*locked->deleteHandle,FileRenameInfo,info,(DWORD)size);
  const DWORD error=renamed?ERROR_SUCCESS:GetLastError();
  need(renamed!=FALSE,"REVIEW_BATCH_ATOMIC_RENAME_FAILED: winerr="+std::to_string(error));
  locked->path=to;
  verify_all();
 }
 void keep(){keep_=true;}
 std::string cleanup(){
  if(keep_||cleanupDone_)return {};cleanupDone_=true;std::vector<std::string> failures;
  for(auto it=files_.rbegin();it!=files_.rend();++it){
   if(!it->handle||it->handle->h==INVALID_HANDLE_VALUE)continue;
   BY_HANDLE_FILE_INFORMATION info{};
   if(!GetFileInformationByHandle(*it->handle,&info)||
      NativeReviewFileIdentity{info.dwVolumeSerialNumber,info.nFileIndexHigh,info.nFileIndexLow}!=it->identity||
      (info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))||info.nNumberOfLinks!=1){
    failures.push_back("file identity uncertain: "+str(it->path));it->handle.reset();continue;
   }
   FILE_DISPOSITION_INFO disposition{};disposition.DeleteFile=TRUE;
   if(!SetFileInformationByHandle(*it->handle,FileDispositionInfo,&disposition,sizeof(disposition)))
    failures.push_back("created file retained: "+str(it->path)+" winerr="+std::to_string(GetLastError()));
   it->handle.reset();
  }
  for(auto it=directories_.rbegin();it!=directories_.rend();++it){
   if(!it->created||!it->deleteHandle||it->deleteHandle->h==INVALID_HANDLE_VALUE)continue;
   bool pathMatches=false;
   if(it->pathHandle&&it->pathHandle->h!=INVALID_HANDLE_VALUE){
    try{
     auto attributes=native_review_attributes(*it->pathHandle,"created directory cleanup");
     pathMatches=(attributes&FILE_ATTRIBUTE_DIRECTORY)!=0&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT)==0&&
       native_review_identity(*it->pathHandle,"created directory cleanup")==it->identity;
    }catch(...){pathMatches=false;}
   }else try{
    auto pathHandle=open_directory(it->path,"created directory cleanup",FILE_READ_ATTRIBUTES,
                                   directoryCompatibleShare_);
    pathMatches=native_review_identity(pathHandle,"created directory cleanup")==it->identity;
   }catch(...){pathMatches=false;}
   // This closes only the duplicate; keep the original atomic create handle
   // open until disposition so the directory name cannot be swapped.
   it->pathHandle.reset();
   if(!pathMatches){failures.push_back("created directory identity uncertain: "+str(it->path));it->deleteHandle.reset();continue;}
   FILE_DISPOSITION_INFO disposition{};disposition.DeleteFile=TRUE;
   if(!SetFileInformationByHandle(*it->deleteHandle,FileDispositionInfo,&disposition,sizeof(disposition)))
    failures.push_back("created directory retained: "+str(it->path)+" winerr="+std::to_string(GetLastError()));
   it->deleteHandle.reset();
  }
  return failures.empty()?std::string{}:std::string("REVIEW_CLEANUP_PARTIAL: ")+([&]{
   std::string result;for(const auto& failure:failures){if(!result.empty())result+="; ";result+=failure;}return result;
  })();
 }
};

inline std::string native_review_write_output(NativeReviewOutputGuard& guard,const fs::path& pathValue,
                                             const std::vector<std::uint8_t>& bytes){
 return guard.write_new(pathValue,bytes);
}
inline void native_review_fields(const Json& value,std::initializer_list<const char*> required,
                                 std::initializer_list<const char*> optional={}){
 need(value.is_object(),"REVIEW_SCHEMA_OBJECT_REQUIRED");
 std::set<std::string> allowed,needed;
 for(auto key:required){allowed.insert(key);needed.insert(key);}
 for(auto key:optional)allowed.insert(key);
 for(auto it=value.begin();it!=value.end();++it)need(allowed.contains(it.key()),"REVIEW_SCHEMA_UNKNOWN_FIELD: "+it.key());
 for(const auto& key:needed)need(value.contains(key),"REVIEW_SCHEMA_REQUIRED_FIELD: "+key);
}
inline fs::path native_review_product_root(){
 std::array<wchar_t,32768> buffer{};DWORD length=GetModuleFileNameW(nullptr,buffer.data(),(DWORD)buffer.size());
 need(length>0&&length<(DWORD)buffer.size(),"REVIEW_PRODUCT_ROOT_UNAVAILABLE");
 auto executable=clean_path(fs::path(std::wstring(buffer.data(),length)));
 auto directory=executable.parent_path();
 // Release layout identifies the product without consulting a mutable source
 // checkout. Admission still requires this exact executable's four receipts.
 if(_wcsicmp(executable.filename().c_str(),L"H3TierExport.exe")==0){
  auto archive=directory.parent_path();auto dist=archive.parent_path();
  if(_wcsicmp(archive.filename().c_str(),L"archive")==0&&_wcsicmp(dist.filename().c_str(),L"dist")==0)
   return clean_path(dist.parent_path());
  if(_wcsicmp(directory.filename().c_str(),L"dist")==0)return clean_path(directory.parent_path());
 }
 for(auto root=directory;!root.empty();root=root.parent_path()){
  if(fs::is_regular_file(root/L"src"/L"main.cpp")&&fs::is_regular_file(root/L".gitignore"))return clean_path(root);
  if(root==root.parent_path())break;
 }
 throw std::runtime_error("REVIEW_PRODUCT_ROOT_UNAVAILABLE: expected release layout or candidate source tree");
}
inline fs::path native_review_executable_path(){
 std::array<wchar_t,32768> buffer{};DWORD length=GetModuleFileNameW(nullptr,buffer.data(),(DWORD)buffer.size());
 need(length>0&&length<(DWORD)buffer.size(),"REVIEW_EXECUTABLE_PATH_UNAVAILABLE");
 return clean_path(fs::path(std::wstring(buffer.data(),length)));
}
inline std::string native_review_binding_sha(const std::string& inventorySha,const Json& key,const Json& targets){
 Json binding={{"inventory_sha256",inventorySha},{"key",key},{"targets",targets}};
 return sha_text(binding.dump());
}
inline std::array<int,2> native_review_pair(const Json& value,const char* name,int minimum=1){
 need(value.is_array()&&value.size()==2,std::string("REVIEW_PAIR_REQUIRED: ")+name);
 return {integer(value[0],minimum,MAX_AXIS,name),integer(value[1],minimum,MAX_AXIS,name)};
}
inline Json native_review_targets(const Json& value){
 native_review_fields(value,{"canvas_1x","crop_1x","origin_1x"});
 auto canvas=native_review_pair(value.at("canvas_1x"),"canvas_1x");
 auto crop=native_review_pair(value.at("crop_1x"),"crop_1x");
 auto origin=native_review_pair(value.at("origin_1x"),"origin_1x",0);
 need(origin[0]+crop[0]<=canvas[0]&&origin[1]+crop[1]<=canvas[1],"REVIEW_CROP_OUTSIDE_CANVAS");
 return value;
}
inline std::pair<int,int> native_review_source_size(const Json& value,const char* name){
 auto pair=native_review_pair(value,name);
 return {pair[0],pair[1]};
}
inline Rect native_review_rect(const Json& value,int width,int height){
 need(value.is_array()&&value.size()==4,"REVIEW_SOURCE_RECT_REQUIRED");
 int x=integer(value[0],0,MAX_AXIS,"source_rect_x"),y=integer(value[1],0,MAX_AXIS,"source_rect_y");
 int w=integer(value[2],1,MAX_AXIS,"source_rect_width"),h=integer(value[3],1,MAX_AXIS,"source_rect_height");
 need(x+w<=width&&y+h<=height,"REVIEW_SOURCE_RECT_OUTSIDE_IMAGE");
 return {x,y,x+w,y+h};
}
inline int native_review_ceil_div(int n,int d){return (n+d-1)/d;}
inline fs::path native_review_input_path(const Json& value,const char* label){
 need(value.is_string(),std::string("REVIEW_PATH_REQUIRED: ")+label);
 auto supplied=path(value.get<std::string>());
 need(supplied.is_absolute(),std::string("REVIEW_PATH_MUST_BE_ABSOLUTE: ")+label);
 return clean_path(supplied);
}
inline void native_review_require_png(const Wic::Frame& frame,const char* label){
 GUID container{};hr(frame.d->GetContainerFormat(&container),"WIC container format");
 need(IsEqualGUID(container,GUID_ContainerFormatPng),std::string("REVIEW_PNG_REQUIRED: ")+label);
}
inline std::string native_review_hash_field(const Json& value,const char* label){
 need(value.is_string()&&valid_sha(value),std::string("REVIEW_SHA256_REQUIRED: ")+label);
 return value.get<std::string>();
}
inline void native_review_hash_file(const fs::path& file,const std::string& expected,const char* label){
 auto bytes=read_all(file);need(sha(bytes)==expected,std::string("REVIEW_INPUT_SHA256_MISMATCH: ")+label);
}
inline void native_review_town_recheck(const NativeReviewItem& item,const char* label){
 for(const auto& input:item.foregroundInputs){
  std::vector<std::uint8_t> raw;
  try{raw=read_all(input.file,input.bytes);}
  catch(const std::exception& error){throw std::runtime_error(std::string("REVIEW_INPUT_SHA256_MISMATCH: ")+label+"; "+error.what());}
  need(raw.size()==input.bytes&&sha(raw)==input.digest,std::string("REVIEW_INPUT_SHA256_MISMATCH: ")+label);
 }
}
inline void native_review_key(const Json& key){
 need(key.is_array(),"REVIEW_NATIVE_KEY_REQUIRED");
 if(key.size()==4&&key[0]=="def"){
  need(key[1].is_string(),"REVIEW_NATIVE_KEY_ID_REQUIRED");token(key[1].get<std::string>());
  integer(key[2],0,65535,"group");integer(key[3],0,65535,"frame");return;
 }
 if(key.size()==2&&key[0]=="pcx"){
  need(key[1].is_string(),"REVIEW_NATIVE_KEY_ID_REQUIRED");token(key[1].get<std::string>());return;
 }
 throw std::runtime_error("REVIEW_NATIVE_KEY_INVALID");
}

using NativeReviewPoint=std::array<int,2>;
inline std::int64_t native_review_cross(NativeReviewPoint a,NativeReviewPoint b,NativeReviewPoint c){
 return std::int64_t(b[0]-a[0])*(c[1]-a[1])-std::int64_t(b[1]-a[1])*(c[0]-a[0]);
}
inline bool native_review_segments_touch(NativeReviewPoint a,NativeReviewPoint b,
                                         NativeReviewPoint c,NativeReviewPoint d){
 auto on=[](NativeReviewPoint p,NativeReviewPoint q,NativeReviewPoint r){
  return r[0]>=std::min(p[0],q[0])&&r[0]<=std::max(p[0],q[0])&&
         r[1]>=std::min(p[1],q[1])&&r[1]<=std::max(p[1],q[1]);
 };
 auto x=native_review_cross(a,b,c),y=native_review_cross(a,b,d),
      z=native_review_cross(c,d,a),w=native_review_cross(c,d,b);
 if((x==0&&on(a,b,c))||(y==0&&on(a,b,d))||(z==0&&on(c,d,a))||(w==0&&on(c,d,b)))return true;
 return ((x<0&&y>0)||(x>0&&y<0))&&((z<0&&w>0)||(z>0&&w<0));
}
inline bool native_review_ring_contains(const std::vector<NativeReviewPoint>& ring,int x,int y){
 // Integer pixel corners and half-integer pixel centers. No division/rounding.
 const std::int64_t cx=2ll*x+1,cy=2ll*y+1;bool inside=false;
 for(std::size_t i=1;i<ring.size();i++){
  auto a=ring[i-1],b=ring[i];const std::int64_t ay=2ll*a[1],by=2ll*b[1];
  if((ay>cy)==(by>cy))continue;
  const std::int64_t dy=b[1]-a[1],left=(cx-2ll*a[0])*dy,right=std::int64_t(b[0]-a[0])*(cy-ay);
  if(dy>0?left<right:left>right)inside=!inside;
 }
 return inside;
}
inline void native_review_town_foreground(NativeReviewItem& item,const Json& selection,
                                          const std::vector<std::uint8_t>& sourceRaw,Wic& decoder){
 native_review_fields(selection,{"schema","authority"});
 need(selection.at("schema")=="h3.native-review.town-foreground-selection.v1","REVIEW_TOWN_SELECTION_SCHEMA_INVALID");
 native_review_fields(selection.at("authority"),{"path","sha256"});
 need(item.key.size()==2&&item.key[0]=="pcx"&&!item.hasAtlas&&!item.hasStateView&&item.alphaCap.is_null()&&
      item.registration=="integer-transparent-padding"&&item.paddingRegistrationExplicit&&item.sourceRectExplicit&&
      item.sourceLayout=="native-crop"&&item.outputLayout=="native-crop"&&!item.render1x&&item.render3x,
      "REVIEW_TOWN_SELECTION_ROLE_OR_REGISTRATION_INVALID");
 auto refDocument=[&](const Json& ref,const char* label){
  need(ref.is_object()&&ref.contains("path")&&ref.contains("sha256"),"REVIEW_TOWN_SELECTION_INPUT_REF_REQUIRED");
  auto file=native_review_input_path(ref.at("path"),label);auto digest=native_review_hash_field(ref.at("sha256"),label);
  auto raw=read_all(file,8ull*1024*1024);need(sha(raw)==digest,"REVIEW_TOWN_SELECTION_INPUT_SHA_MISMATCH");
  if(ref.contains("bytes"))need(integer(ref.at("bytes"),0,8*1024*1024,label)==(int)raw.size(),"REVIEW_TOWN_SELECTION_INPUT_BYTES_MISMATCH");
  item.foregroundInputs.push_back({file,digest,raw.size()});
  return std::pair<Json,Json>{parse_json(raw),Json{{"path",str(file)},{"sha256",digest}}};
 };
 auto [authority,authorityRef]=refDocument(selection.at("authority"),"Town selection authority");
 need(authority.at("schema")=="h3.art-town.root-component-candidate-input-authority.v1","REVIEW_TOWN_SELECTION_AUTHORITY_SCHEMA_INVALID");
 const auto& execution=authority.at("execution_authority");
 const auto& approved=execution.at("frozen_data_may_be_used_for_existing_Tool_owner_private_typed_Town_implementation_validation");
 const auto& sourceGrant=execution.at("formal_Source_intake_or_index_or_deployment_authorized_here");
 need(approved.is_boolean()&&approved.get<bool>()&&sourceGrant.is_boolean()&&!sourceGrant.get<bool>(),
      "REVIEW_TOWN_SELECTION_PRIVATE_AUTHORITY_REQUIRED");
 auto [vector,vectorRef]=refDocument(authority.at("frozen_vector"),"Town selection vector");
 auto [request,requestRef]=refDocument(vector.at("actual_parent_request"),"Town parent request");
 auto [standard,standardRef]=refDocument(authority.at("art_standard"),"Town art standard");
 if(vector.contains("art_standard_evidence_only")){
  const auto& vectorStandard=vector.at("art_standard_evidence_only");
  need(native_review_path_key(native_review_input_path(vectorStandard.at("path"),"Town vector art standard"))==
       native_review_path_key(path(standardRef.at("path")))&&vectorStandard.at("sha256")==standardRef.at("sha256"),
       "REVIEW_TOWN_SELECTION_STANDARD_BINDING_MISMATCH");
  if(vectorStandard.contains("bytes"))need(integer(vectorStandard.at("bytes"),0,8*1024*1024,"Town standard bytes")==
       (int)read_all(path(standardRef.at("path")),8ull*1024*1024).size(),"REVIEW_TOWN_SELECTION_STANDARD_BINDING_MISMATCH");
 }
 auto [nativeProof,nativeProofRef]=refDocument(vector.at("native_function").at("proof"),"Town native proof");
 const auto& parent=authority.at("parent");const auto& history=vector.at("actual_parent_only");
 need(native_review_path_key(native_review_input_path(parent.at("path"),"Town stable parent"))==native_review_path_key(item.source)&&
      parent.at("sha256")==item.sourceSha&&history.at("sha256")==item.sourceSha,"REVIEW_TOWN_SELECTION_PARENT_BINDING_MISMATCH");
 auto parentBytes=integer(parent.at("bytes"),1,512*1024*1024,"Town parent bytes");
 need(parentBytes==(int)sourceRaw.size()&&integer(history.at("bytes"),1,512*1024*1024,"Town history bytes")==parentBytes,
      "REVIEW_TOWN_SELECTION_PARENT_BINDING_MISMATCH");
 auto size=native_review_pair(parent.at("pixels"),"Town parent pixels"),historySize=native_review_pair(history.at("pixels"),"Town history pixels");
 need(size==item.sourcePixels&&historySize==size,"REVIEW_TOWN_SELECTION_PARENT_BINDING_MISMATCH");
 // A pinned explicit custody substitution, never an existence-based fallback.
 // The old generated-image path is an identity field, not a live dependency.
 need(history.at("path").is_string()&&path(history.at("path").get<std::string>()).is_absolute(),"REVIEW_TOWN_SELECTION_HISTORY_PATH_INVALID");
 const auto historyPath=native_review_output_path(path(history.at("path").get<std::string>()));
 const bool sameParentPath=native_review_path_key(historyPath)==native_review_path_key(item.source);
 if(!sameParentPath){
  need(parent.contains("same_exact_original_as_G")&&parent.at("same_exact_original_as_G").is_boolean()&&
       parent.at("same_exact_original_as_G").get<bool>(),"REVIEW_TOWN_SELECTION_EXPLICIT_PARENT_CUSTODY_REQUIRED");
 }
 auto sameArray=[](const Json& value,const Json& expected){
  need(value.is_array()&&value.size()==expected.size(),"REVIEW_TOWN_SELECTION_GEOMETRY_MISMATCH");
  for(std::size_t i=0;i<value.size();i++)need(integer(value[i],0,MAX_AXIS*MAX_AXIS,"Town geometry")==
      integer(expected[i],0,MAX_AXIS*MAX_AXIS,"Town expected geometry"),
      "REVIEW_TOWN_SELECTION_GEOMETRY_MISMATCH");
 };
 auto upper=[](std::string value){for(auto& c:value)c=(char)std::toupper((unsigned char)c);return value;};
 const auto identity=upper(item.key[1].get<std::string>());const auto& nf=vector.at("native_function");
 need(vector.at("target_identity")==identity&&nf.at("resource").is_string()&&
      upper(nf.at("resource").get<std::string>())==identity+".PCX","REVIEW_TOWN_SELECTION_RESOURCE_MISMATCH");
 const auto& geometry=authority.at("approved_shared_parent_geometry_for_candidate");
 const Json rect={item.sourceRect.l,item.sourceRect.t,item.sourceRect.w(),item.sourceRect.h()};
 const Json offset={item.registrationLeft,item.registrationTop};
 const Json registered={item.crop[0]*item.commonK,item.crop[1]*item.commonK};
 sameArray(geometry.at("source_rect_XYWH"),rect);
 sameArray(geometry.at("source_view_half_open"),Json::array({item.sourceRect.l,item.sourceRect.t,item.sourceRect.r,item.sourceRect.b}));
 need(integer(geometry.at("K"),1,MAX_AXIS,"Town K")==item.commonK,"REVIEW_TOWN_SELECTION_GEOMETRY_MISMATCH");
 sameArray(geometry.at("pad_offset"),offset);sameArray(geometry.at("registered_source_pixels"),registered);
 sameArray(geometry.at("native_canvas"),item.targets.at("canvas_1x"));sameArray(geometry.at("native_crop"),item.targets.at("crop_1x"));
 sameArray(geometry.at("native_origin"),item.targets.at("origin_1x"));
 native_review_pair(geometry.at("native_world_XY"),"Town owner world XY",0);
 sameArray(geometry.at("render_scales"),Json::array({3}));
 sameArray(geometry.at("candidate_pixels"),Json::array({item.crop[0]*3,item.crop[1]*3}));
 const auto& fit=geometry.at("selected_bbox_fit_or_recenter_allowed");
 need(fit.is_boolean()&&!fit.get<bool>(),"REVIEW_TOWN_SELECTION_BBOX_FIT_FORBIDDEN");
 const auto& polygons=vector.at("reference_geometry_polygons");
 need(polygons.is_array()&&!polygons.empty()&&polygons.size()<=64,"REVIEW_TOWN_SELECTION_RING_BUDGET");
 need(std::size_t(integer(authority.at("frozen_vector").at("polygons"),1,64,"Town polygon count"))==polygons.size(),
      "REVIEW_TOWN_SELECTION_VECTOR_MEMBERSHIP_MISMATCH");
 const auto& polygonIds=authority.at("frozen_vector").at("polygon_ids");
 need(polygonIds.is_array()&&polygonIds.size()==polygons.size(),"REVIEW_TOWN_SELECTION_VECTOR_MEMBERSHIP_MISMATCH");
 struct Ring{std::vector<NativeReviewPoint> points;Rect bounds;};std::vector<Ring> rings;std::set<std::string> ids;
 std::uint64_t edges=0,work=0;
 for(std::size_t index=0;index<polygons.size();index++){
  const auto& polygon=polygons[index];need(polygon.at("id").is_string(),"REVIEW_TOWN_SELECTION_RING_ID_REQUIRED");
  auto id=token(polygon.at("id").get<std::string>());
  need(ids.insert(id).second&&polygonIds[index]==id,"REVIEW_TOWN_SELECTION_VECTOR_MEMBERSHIP_MISMATCH");
  const auto& closed=polygon.at("closed");const auto& points=polygon.at("points");
  need(closed.is_boolean()&&closed.get<bool>()&&points.is_array()&&points.size()>=4&&points.size()<=257,
       "REVIEW_TOWN_SELECTION_CLOSED_RING_REQUIRED");
  Ring ring;ring.bounds={item.sourcePixels[0],item.sourcePixels[1],0,0};
  for(const auto& point:points){
   need(point.is_array()&&point.size()==2,"REVIEW_TOWN_SELECTION_POINT_REQUIRED");
   NativeReviewPoint p={integer(point[0],0,item.sourcePixels[0],"Town x"),integer(point[1],0,item.sourcePixels[1],"Town y")};
   ring.points.push_back(p);ring.bounds.l=std::min(ring.bounds.l,p[0]);ring.bounds.r=std::max(ring.bounds.r,p[0]);
   ring.bounds.t=std::min(ring.bounds.t,p[1]);ring.bounds.b=std::max(ring.bounds.b,p[1]);
  }
  need(ring.points.front()==ring.points.back()&&ring.bounds.w()>0&&ring.bounds.h()>0,"REVIEW_TOWN_SELECTION_CLOSED_RING_REQUIRED");
  const auto count=ring.points.size()-1;std::int64_t area=0;
  for(std::size_t i=0;i<count;i++){
   const auto a=ring.points[i],b=ring.points[i+1],c=ring.points[(i+2)%count];
   need(a!=b,"REVIEW_TOWN_SELECTION_DUPLICATE_EDGE");
   area+=std::int64_t(a[0])*b[1]-std::int64_t(b[0])*a[1];
   if(native_review_cross(a,b,c)==0)need(std::int64_t(a[0]-b[0])*(c[0]-b[0])+std::int64_t(a[1]-b[1])*(c[1]-b[1])<=0,
       "REVIEW_TOWN_SELECTION_SELF_INTERSECTION");
   for(std::size_t j=i+1;j<count;j++)if(j!=i+1&&!(i==0&&j==count-1))
    need(!native_review_segments_touch(a,b,ring.points[j],ring.points[j+1]),"REVIEW_TOWN_SELECTION_SELF_INTERSECTION");
  }
  need(area!=0,"REVIEW_TOWN_SELECTION_ZERO_AREA");
  edges+=count;work+=std::uint64_t(ring.bounds.w())*ring.bounds.h()*count;
  need(edges<=2048&&work<=100000000,"REVIEW_TOWN_SELECTION_WORK_BUDGET");
  rings.push_back(std::move(ring));
 }
 item.foregroundMask.assign(std::size_t(item.sourcePixels[0])*item.sourcePixels[1],0);
 for(const auto& ring:rings)for(int y=ring.bounds.t;y<ring.bounds.b;y++)for(int x=ring.bounds.l;x<ring.bounds.r;x++){
  auto& selected=item.foregroundMask[std::size_t(y)*item.sourcePixels[0]+x];
  if(!selected&&native_review_ring_contains(ring.points,x,y))selected=1;
 }
 auto rgba=decoder.decode_rgba(sourceRaw,item.sourcePixels[0],item.sourcePixels[1]);auto selectedRgba=rgba;
 std::uint64_t positive=0,alpha1=0,selectedPositive=0,selectedAlpha1=0,outside=0,outsideAlpha1=0;
 for(int y=0;y<item.sourcePixels[1];y++)for(int x=0;x<item.sourcePixels[0];x++){
  auto i=std::size_t(y)*item.sourcePixels[0]+x;const auto alpha=rgba[i*4+3];bool selected=item.foregroundMask[i]!=0;
  if(alpha){++positive;if(alpha==1)++alpha1;
   if(selected){++selectedPositive;if(alpha==1)++selectedAlpha1;
    if(x<item.sourceRect.l||y<item.sourceRect.t||x>=item.sourceRect.r||y>=item.sourceRect.b){++outside;if(alpha==1)++outsideAlpha1;}
   }
  }
  if(!selected)std::fill_n(selectedRgba.data()+i*4,4,std::uint8_t{0});
 }
 need(selectedPositive>0,"REVIEW_TOWN_SELECTION_NO_POSITIVE_SAMPLES");
 need(outside==0,"REVIEW_TOWN_SELECTED_POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT");
 const auto rgbaSha=sha(rgba);
 need(parent.at("whole_RGBA_sha256")==rgbaSha&&history.at("whole_RGBA_sha256")==rgbaSha,
      "REVIEW_TOWN_SELECTION_PARENT_RGBA_MISMATCH");
 item.foregroundSelection=selection;
 item.foregroundProof={{"schema","h3.native-review.town-foreground-selection-proof.v1"},
  {"operation","same-grid-positive-ring-union-copy-before-one-quality-sample"},
  {"inputs",{{"authority",authorityRef},{"vector",vectorRef},{"request",requestRef},{"art_standard",standardRef},{"native_proof",nativeProofRef}}},
  {"parent_custody",{{"policy",sameParentPath?"same-canonical-parent-path.v1":"explicit-root-approved-stable-parent-rebind.v1"},
     {"stable_parent",{{"path",str(item.source)},{"sha256",item.sourceSha},{"bytes",parentBytes},{"pixels",size}}},
     {"vector_parent_history",{{"path",history.at("path")},{"sha256",item.sourceSha},{"bytes",parentBytes},{"pixels",size}}},
     {"historical_path_is_runtime_dependency",sameParentPath}}},
  {"source_pixels",size},{"source_rect_xywh",rect},{"registered_pixels",registered},{"common_k",item.commonK},{"offset",offset},
  {"polygons",rings.size()},{"edges",edges},{"raster_work_bound",work},{"source_rgba8_sha256",rgbaSha},
  {"selected_source_rgba8_sha256",sha(selectedRgba)},{"parent_positive_pixels",positive},{"selected_positive_pixels",selectedPositive},
  {"excluded_positive_pixels",positive-selectedPositive},{"parent_alpha1_pixels",alpha1},{"selected_alpha1_pixels",selectedAlpha1},
  {"excluded_alpha1_pixels",alpha1-selectedAlpha1},{"selected_positive_outside_view",outside},{"selected_alpha1_outside_view",outsideAlpha1},
  {"selected_linear_premultiplied_samples_bit_identical",true},{"sampling_calls",1},{"intermediate_png_files",0},{"Source_art_approved",false}};
}

inline std::vector<NativeReviewItem> native_review_parse_spec(const Json& spec,
                                                               const fs::path& specPath,
                                                               const fs::path& outdir,
                                                               const fs::path& productRoot,
                                                               Wic& decoder,
                                                               Json& sourceManifest,
                                                               Json& inventory){
 native_review_fields(spec,{"schema","source_manifest","official_inventory","items"});
 need(spec.at("schema")=="h3.native-review.render-spec.v1","REVIEW_SPEC_SCHEMA_UNSUPPORTED");
 auto readBinding=[&](const Json& value,const char* label){
  native_review_fields(value,{"path","sha256"});
  fs::path file=native_review_input_path(value.at("path"),label);
  auto digest=native_review_hash_field(value.at("sha256"),label);
  native_review_hash_file(file,digest,label);
  return Json{{"path",str(file)},{"sha256",digest}};
 };
 sourceManifest=readBinding(spec.at("source_manifest"),"source_manifest");
 inventory=readBinding(spec.at("official_inventory"),"official_inventory");
 need(spec.at("items").is_array()&&!spec.at("items").empty()&&spec.at("items").size()<=512,
      "REVIEW_ITEM_COUNT_RANGE");
 // Jobs retain one byte per parent pixel until the transaction is sealed.
 // Bound the aggregate before decoding a source or allocating any mask.
 std::uint64_t townMaskPixels=0;
 for(const auto& entry:spec.at("items"))if(entry.contains("foreground_selection")){
  auto size=native_review_source_size(entry.at("source_pixels"),"Town source_pixels");
  const auto pixels=std::uint64_t(size.first)*size.second;
  need(pixels<=16ull*1024*1024,"REVIEW_TOWN_SELECTION_PIXEL_BUDGET");
  townMaskPixels+=pixels;
  need(townMaskPixels<=64ull*1024*1024,"REVIEW_TOWN_SELECTION_BATCH_MASK_BUDGET");
 }
 auto safeBase=native_review_output_path(productRoot/L"build"/L"experiments"/L"native-review");
 auto output=native_review_output_path(outdir);need(native_review_within(output,safeBase),"REVIEW_OUTPUT_OUTSIDE_IGNORED_ROOT");
 need(!fs::exists(output),"REVIEW_OUTPUT_ALREADY_EXISTS");
 std::vector<fs::path> inputs={specPath,path(sourceManifest.at("path")),path(inventory.at("path"))};
 std::vector<NativeReviewItem> result;std::set<std::string> ids,keys;
 for(const auto& entry:spec.at("items")){
  native_review_fields(entry,{"id","key","source_png","source_sha256","source_pixels","source_rect","registration","targets","geometry_binding_sha256","reference"},
                            {"source_layout","render_scales","atlas_view","state_view","alpha_cap_3x","padding_registration","output_layout","foreground_selection"});
  NativeReviewItem item;
  need(entry.at("id").is_string(),"REVIEW_ID_REQUIRED");item.id=token(entry.at("id").get<std::string>());
  need(ids.insert(item.id).second,"REVIEW_DUPLICATE_ID");item.key=entry.at("key");native_review_key(item.key);
  need(keys.insert(item.key.dump()).second,"REVIEW_DUPLICATE_OFFICIAL_KEY");
  item.source=native_review_input_path(entry.at("source_png"),"source_png");
  item.sourceSha=native_review_hash_field(entry.at("source_sha256"),"source_png");
  auto sourceRaw=read_all(item.source);need(sha(sourceRaw)==item.sourceSha,"REVIEW_SOURCE_SHA256_MISMATCH");
  auto sourceFrame=decoder.frame(sourceRaw);native_review_require_png(sourceFrame,"source_png");
  item.sourcePixels={(int)sourceFrame.w,(int)sourceFrame.h};
  if(entry.contains("foreground_selection")){
   need(entry.contains("source_layout")&&entry.at("source_layout")=="native-crop"&&
        entry.contains("output_layout")&&entry.at("output_layout")=="native-crop"&&
        entry.contains("render_scales")&&entry.at("render_scales").is_array()&&
        entry.at("render_scales").size()==1&&entry.at("render_scales")[0].is_number_integer()&&
        entry.at("render_scales")[0]==3,"REVIEW_TOWN_SELECTION_EXPLICIT_SINGLE3X_LAYOUT_REQUIRED");
   need(std::uint64_t(item.sourcePixels[0])*item.sourcePixels[1]<=16ull*1024*1024,"REVIEW_TOWN_SELECTION_PIXEL_BUDGET");
   need(sourceRaw.size()>=33&&!std::memcmp(sourceRaw.data()+12,"IHDR",4)&&sourceRaw[24]==8&&sourceRaw[25]==6,
        "REVIEW_TOWN_SELECTION_REQUIRES_RGBA8_SOURCE");
  }
  std::string preflightColor;auto decodedPreflight=decoder.decode(sourceRaw,true,preflightColor);
  need(decodedPreflight.w==item.sourcePixels[0]&&decodedPreflight.h==item.sourcePixels[1],"REVIEW_SOURCE_DECODE_SIZE_MISMATCH");
  auto declared=native_review_source_size(entry.at("source_pixels"),"source_pixels");
  need(declared.first==item.sourcePixels[0]&&declared.second==item.sourcePixels[1],"REVIEW_SOURCE_PIXEL_SIZE_MISMATCH");
  item.targets=native_review_targets(entry.at("targets"));
  item.canvas=native_review_pair(item.targets.at("canvas_1x"),"canvas_1x");
  item.crop=native_review_pair(item.targets.at("crop_1x"),"crop_1x");
  item.origin=native_review_pair(item.targets.at("origin_1x"),"origin_1x",0);
  item.sourceLayout=entry.value("source_layout",std::string("native-crop"));
  need(item.sourceLayout=="native-crop"||item.sourceLayout=="native-canvas","REVIEW_SOURCE_LAYOUT_UNSUPPORTED");
  item.outputLayout=entry.value("output_layout",std::string("native-canvas"));
  need(item.outputLayout=="native-crop"||item.outputLayout=="native-canvas","REVIEW_OUTPUT_LAYOUT_UNSUPPORTED");
  auto bindingSha=native_review_hash_field(entry.at("geometry_binding_sha256"),"geometry_binding_sha256");
  need(bindingSha==native_review_binding_sha(inventory.at("sha256").get<std::string>(),item.key,item.targets),
       "REVIEW_GEOMETRY_BINDING_HASH_MISMATCH");
  item.sourceRectExplicit=!entry.at("source_rect").is_null();
  item.sourceRect=item.sourceRectExplicit?native_review_rect(entry.at("source_rect"),item.sourcePixels[0],item.sourcePixels[1]):Rect{0,0,item.sourcePixels[0],item.sourcePixels[1]};
  item.registration=entry.at("registration").get<std::string>();
  item.paddingRegistrationExplicit=entry.contains("padding_registration");
  need(!item.paddingRegistrationExplicit||item.registration=="integer-transparent-padding",
       "REVIEW_PADDING_REGISTRATION_REQUIRES_PADDING_MODE");
  need(item.registration=="strict-aspect"||item.registration=="integer-transparent-padding"||
       item.registration=="full-viewport-axis-normalization"||item.registration=="full-viewport-axis-normalization-3x"||
       item.registration=="authored-atlas-window-3x"||item.registration=="authored-source-view-3x","REVIEW_REGISTRATION_UNSUPPORTED");
  need(item.outputLayout!="native-crop"||item.sourceLayout!="native-canvas"||item.registration=="strict-aspect",
       "REVIEW_CANVAS_CROP_OUTPUT_REQUIRES_STRICT_ASPECT_3X_ONLY");
  const int rw=item.sourceRect.w(),rh=item.sourceRect.h();
  const auto samplingBase=item.sourceLayout=="native-canvas"?item.canvas:item.crop;
  const int cw=samplingBase[0],ch=samplingBase[1];
  const bool viewport1x=item.registration=="full-viewport-axis-normalization";
  const bool viewport3x=item.registration=="full-viewport-axis-normalization-3x";
  item.hasAtlas=item.registration=="authored-atlas-window-3x";
  item.hasStateView=item.registration=="authored-source-view-3x";
  need(entry.contains("atlas_view")==item.hasAtlas&&entry.contains("state_view")==item.hasStateView&&
       (!entry.contains("alpha_cap_3x")||item.hasAtlas),
       "REVIEW_AUTHORED_VIEW_REQUIRES_ATLAS_MODE");
  if(item.hasStateView){
   need(item.sourceRectExplicit&&entry.contains("render_scales")&&entry.at("render_scales")==Json::array({3}),
        "REVIEW_AUTHORED_STATE_REQUIRES_RECT_AND_3X_ONLY");
   item.stateView=entry.at("state_view");
   native_review_fields(item.stateView,{"authority","source_rect_xywh","target_rect_1x","scope"});
   need(item.stateView.at("source_rect_xywh")==entry.at("source_rect")&&
        item.stateView.at("target_rect_1x")==Json::array({0,0,cw,ch})&&
        item.stateView.at("scope")=="author-chosen-ui-state-rectangle","REVIEW_AUTHORED_STATE_CORRESPONDENCE_DRIFT");
   item.stateView["authority"]=readBinding(item.stateView.at("authority"),"state view authority");
   inputs.push_back(path(item.stateView.at("authority").at("path")));
  }
  if(item.hasAtlas){
   need(!item.sourceRectExplicit&&entry.contains("render_scales")&&entry.at("render_scales")==Json::array({3}),
        "REVIEW_AUTHORED_VIEW_REQUIRES_WHOLE_SOURCE_AND_3X_ONLY");
   item.atlasView=entry.at("atlas_view");
   native_review_fields(item.atlasView,{"authority","sample_pixels_3x","frame_rect_3x","source_rect_raw_rational"});
   item.atlasView["authority"]=readBinding(item.atlasView.at("authority"),"atlas authority");
   inputs.push_back(path(item.atlasView.at("authority").at("path")));
   item.atlasPixels=native_review_pair(item.atlasView.at("sample_pixels_3x"),"atlas sample");
   need(item.atlasPixels[0]<=item.sourcePixels[0]&&item.atlasPixels[1]<=item.sourcePixels[1],
        "REVIEW_AUTHORED_ATLAS_NEEDS_HIGHER_MASTER");
   item.atlasRect=native_review_rect(item.atlasView.at("frame_rect_3x"),item.atlasPixels[0],item.atlasPixels[1]);
   need(item.atlasRect.w()==cw*3&&item.atlasRect.h()==ch*3,"REVIEW_AUTHORED_ATLAS_TARGET_MISMATCH");
   const auto& rational=item.atlasView.at("source_rect_raw_rational");
   need(rational.is_array()&&rational.size()==4,"REVIEW_AUTHORED_RATIONAL_PROJECTION_REQUIRED");
   const std::array<int,4> window={item.atlasRect.l,item.atlasRect.t,item.atlasRect.w(),item.atlasRect.h()};
   for(int component=0;component<4;component++){
    int axis=component%2,numerator=window[component]*item.sourcePixels[axis],denominator=item.atlasPixels[axis];
    int divisor=std::gcd(numerator,denominator);
    need(rational[component]==Json::array({numerator/divisor,denominator/divisor}),"REVIEW_AUTHORED_RATIONAL_PROJECTION_DRIFT");
   }
   if(entry.contains("alpha_cap_3x")){
    const auto& cap=entry.at("alpha_cap_3x");native_review_fields(cap,{"path","sha256","operation"});
    need(cap.at("operation")=="min(model_alpha,official_alpha)","REVIEW_AUTHORED_ALPHA_OPERATION_UNSUPPORTED");
    auto binding=readBinding(Json{{"path",cap.at("path")},{"sha256",cap.at("sha256")}},"alpha cap");
    item.alphaCap={{"path",binding.at("path")},{"sha256",binding.at("sha256")},{"operation",cap.at("operation")}};
    item.alphaCapPath=path(binding.at("path"));inputs.push_back(item.alphaCapPath);
    auto maskRaw=read_all(item.alphaCapPath);auto maskFrame=decoder.frame(maskRaw);native_review_require_png(maskFrame,"alpha cap");
    need(maskFrame.w==UINT(cw*3)&&maskFrame.h==UINT(ch*3),"REVIEW_AUTHORED_ALPHA_GEOMETRY_MISMATCH");
   }
  }
  if(viewport1x||viewport3x)
   need(item.sourceRect.l==0&&item.sourceRect.t==0&&rw==item.sourcePixels[0]&&rh==item.sourcePixels[1],
        "REVIEW_FULL_VIEWPORT_REQUIRES_WHOLE_SOURCE");
  for(int y=0;y<decodedPreflight.h;y++)for(int x=0;x<decodedPreflight.w;x++){
   if(decodedPreflight.p[std::size_t(y)*decodedPreflight.w+x].a<=0)continue;
   ++item.sourcePositive;
   if(x>=item.sourceRect.l&&y>=item.sourceRect.t&&x<item.sourceRect.r&&y<item.sourceRect.b)++item.viewPositive;
  }
  if(item.registration=="integer-transparent-padding"&&!entry.contains("foreground_selection"))
   need(item.sourcePositive==item.viewPositive,"REVIEW_POSITIVE_ALPHA_OUTSIDE_SOURCE_RECT");
  need(rw>=cw&&rh>=ch,"REVIEW_SOURCE_UNDER_NATIVE_1X");
  const bool exactAspect=std::int64_t(rw)*ch==std::int64_t(rh)*cw;
  if(item.registration=="strict-aspect"){
   if(item.sourceRectExplicit)need(exactAspect,"REVIEW_SOURCE_RECT_ASPECT_MISMATCH");
   else need(exactAspect,"REVIEW_FULL_IMAGE_ASPECT_MISMATCH: opt in to integer-transparent-padding");
  }else if(item.registration=="integer-transparent-padding"){
   need(!exactAspect||item.paddingRegistrationExplicit,"REVIEW_PADDING_ONLY_ALLOWED_FOR_ASPECT_MISMATCH");
  }
  item.commonK=std::max(native_review_ceil_div(rw,cw),native_review_ceil_div(rh,ch));
  const bool sourceCan3x=rw>=3*cw&&rh>=3*ch;
  if(viewport3x)need(sourceCan3x,"REVIEW_NEEDS_HIGHER_MASTER_FOR_3X");
  item.render3x=!viewport1x&&sourceCan3x;
  const auto scales=entry.value("render_scales",Json::array({3}));
  need(scales==Json::array({1})||scales==Json::array({3})||scales==Json::array({1,3}),
       "REVIEW_RENDER_SCALES_MUST_BE_1_3_OR_1_3");
  if(viewport3x)need(scales==Json::array({3}),"REVIEW_VIEWPORT_3X_MODE_REQUIRES_3X_ONLY");
  const bool wants3x=scales==Json::array({3})||scales==Json::array({1,3});
  if(wants3x)need(item.render3x,"REVIEW_REQUESTED_3X_UNAVAILABLE");
  item.render1x=scales!=Json::array({3});item.render3x=wants3x;
  need(item.outputLayout!="native-crop"||(!item.render1x&&item.render3x),
       "REVIEW_CROP_OUTPUT_REQUIRES_NATIVE_CROP_3X_ONLY");
  if(item.paddingRegistrationExplicit){
   const auto& padding=entry.at("padding_registration");native_review_fields(padding,{"common_k","offset"});
   int requestedK=integer(padding.at("common_k"),1,MAX_AXIS,"padding common K");
   need(requestedK>=item.commonK,"REVIEW_PADDING_REGISTRATION_DOMAIN_INVALID");
   item.commonK=requestedK;
   auto offset=native_review_pair(padding.at("offset"),"padding offset",0);
   item.registrationLeft=offset[0];item.registrationTop=offset[1];
  }
  if(item.registration=="integer-transparent-padding"){
   const std::int64_t registeredW=std::int64_t(cw)*item.commonK,registeredH=std::int64_t(ch)*item.commonK;
   need(registeredW<=MAX_AXIS&&registeredH<=MAX_AXIS&&registeredW*registeredH<=MAX_PIXELS,"REVIEW_PADDING_CANVAS_LIMIT");
   if(!item.paddingRegistrationExplicit){
    item.registrationLeft=(int(registeredW)-rw)/2;item.registrationTop=(int(registeredH)-rh)/2;
   }
   need(item.registrationLeft+rw<=registeredW&&item.registrationTop+rh<=registeredH,
        "REVIEW_PADDING_REGISTRATION_DOMAIN_INVALID");
  }
  for(int scale:{1,3}){
   if(scale==1&&!item.render1x)continue;
   if(scale==3&&!item.render3x)continue;
   int w=item.canvas[0]*scale,h=item.canvas[1]*scale;
   need(w<=MAX_AXIS&&h<=MAX_AXIS&&std::uint64_t(w)*h<=MAX_PIXELS,"REVIEW_TARGET_PIXEL_LIMIT");
  }
  if(entry.contains("foreground_selection"))native_review_town_foreground(item,entry.at("foreground_selection"),sourceRaw,decoder);
  item.hasReference=!entry.at("reference").is_null();
  if(item.hasReference){
   const auto& ref=entry.at("reference");native_review_fields(ref,{"path","sha256","layout","source_pixels"});
   item.referencePath=native_review_input_path(ref.at("path"),"reference.path");
   item.referenceSha=native_review_hash_field(ref.at("sha256"),"reference.sha256");
   auto refRaw=read_all(item.referencePath);need(sha(refRaw)==item.referenceSha,"REVIEW_REFERENCE_SHA256_MISMATCH");
   auto refFrame=decoder.frame(refRaw);native_review_require_png(refFrame,"reference");
   std::string referenceColorNote;auto referencePreflight=decoder.decode(refRaw,true,referenceColorNote);
   need(referencePreflight.w==refFrame.w&&referencePreflight.h==refFrame.h,"REVIEW_REFERENCE_DECODE_SIZE_MISMATCH");
   auto refSize=native_review_source_size(ref.at("source_pixels"),"reference.source_pixels");
   auto refPixelsPreflight=decoder.decode_rgba(refRaw,(int)refFrame.w,(int)refFrame.h);
   need(refPixelsPreflight.size()==std::size_t(refFrame.w)*refFrame.h*4,"REVIEW_REFERENCE_DECODE_FAILED");
   need(refSize.first==(int)refFrame.w&&refSize.second==(int)refFrame.h,"REVIEW_REFERENCE_PIXEL_SIZE_MISMATCH");
   need(ref.at("layout").is_string(),"REVIEW_REFERENCE_LAYOUT_REQUIRED");auto layout=ref.at("layout").get<std::string>();
   need(layout=="native-crop"||layout=="native-canvas","REVIEW_REFERENCE_LAYOUT_UNSUPPORTED");
   const auto expected=layout=="native-crop"?item.crop:item.canvas;
   need(refFrame.w==(UINT)expected[0]&&refFrame.h==(UINT)expected[1],"REVIEW_REFERENCE_GEOMETRY_MISMATCH");
   item.reference={{"path",str(item.referencePath)},{"sha256",item.referenceSha},{"layout",layout},
                   {"source_pixels",{refSize.first,refSize.second}}};
  }
  inputs.push_back(item.source);if(item.hasReference)inputs.push_back(item.referencePath);
  for(const auto& input:item.foregroundInputs)inputs.push_back(input.file);
  result.push_back(std::move(item));
 }
 std::map<std::string,std::vector<const NativeReviewItem*>> paddingGroups;
 for(const auto& item:result){
  auto group=item.key;
  if(group[0]=="def")group.erase(group.end()-1);
  paddingGroups[group.dump()].push_back(&item);
 }
 for(const auto& [group,items]:paddingGroups){
  const NativeReviewItem* explicitItem=nullptr;
  for(auto item:items)if(item->paddingRegistrationExplicit){explicitItem=item;break;}
  if(!explicitItem)continue;
  for(auto item:items)need(item->paddingRegistrationExplicit&&item->commonK==explicitItem->commonK&&
                         item->sourceLayout==explicitItem->sourceLayout,"REVIEW_PADDING_GROUP_CONTRACT_MISMATCH");
 }
 for(const auto& input:inputs)need(!native_review_overlap(input,output),"REVIEW_INPUT_OUTPUT_OVERLAP");
 return result;
}

inline Json native_review_build_binding(const fs::path& root,const fs::path& executable,
                                       const std::vector<std::string>& additionalRequiredSources={}){
 // A released executable must bind the source closure that was frozen at
 // publication. A later candidate rebuild is NOT a reason to invalidate it.
 // Candidate executables retain the original strict live-source gate.
 auto exeRaw=read_all(executable);auto executableSha=sha(exeRaw);
 auto releasedExe=native_review_output_path(root/L"dist"/L"H3TierExport.exe");
 auto actualExe=clean_path(executable);
 const bool current=native_review_path_key(actualExe)==native_review_path_key(releasedExe);
 const bool archived=_wcsicmp(actualExe.filename().c_str(),L"H3TierExport.exe")==0&&
                      native_review_path_key(actualExe.parent_path().parent_path())==native_review_path_key(root/L"dist"/L"archive");
 const bool formal=current||archived;
 auto releaseDirectory=actualExe.parent_path();
 auto receiptPath=clean_path(formal?releaseDirectory/L"H3TierExport-build-receipt.json":
                              root/L"build"/L"candidate"/L"build-receipt.json");
 auto receiptRaw=read_all(receiptPath);auto receipt=parse_json(receiptRaw);
 Json frozenTests=nullptr;
 if(formal){
  auto distRelease=read_all(clean_path(releaseDirectory/L"H3TierExport-release.json"));
  if(current){
   auto currentRelease=read_all(clean_path(root/L"reports"/L"current"/L"release.json"));
   need(distRelease==currentRelease,"REVIEW_RELEASE_REPORT_COPY_MISMATCH");
  }
  auto release=parse_json(distRelease);
  need(release.value("schema","")=="h3.tier-export.release.v2"&&
       release.value("exe_sha256","")==executableSha,"REVIEW_RELEASE_EXE_IDENTITY_MISMATCH");
  need(release.value("build_receipt_sha256","")==sha(receiptRaw),
       "REVIEW_RELEASE_FROZEN_BUILD_RECEIPT_DRIFT");
  need(release.contains("files")&&release.at("files").is_array(),
       "REVIEW_RELEASE_FILE_ROWS_INVALID");
  auto releaseFileListed=[&](const fs::path& file,const std::string& digest){
   int matched=0;
   for(const auto& entry:release.at("files")){
    need(entry.is_object()&&entry.contains("path")&&entry.contains("sha256")&&
         entry.at("path").is_string()&&valid_sha(entry.at("sha256")),
         "REVIEW_RELEASE_FILE_ROW_INVALID");
    const auto storedPath=path(entry.at("path").get<std::string>());
    const bool exactPath=storedPath.is_absolute()&&
       native_review_path_key(storedPath)==native_review_path_key(file);
    // The unique publisher records release files by leaf name. Resolve that
    // form only against this exact release directory; the SHA remains required.
    const bool exactLeaf=storedPath.parent_path().empty()&&
       _wcsicmp(storedPath.filename().c_str(),file.filename().c_str())==0;
    if((exactPath||exactLeaf)&&entry.at("sha256").get<std::string>()==digest)matched++;
   }
   need(matched==1,"REVIEW_RELEASE_FILE_IDENTITY_MISMATCH");
  };
  // Release rows retain their publication paths when the original four files
  // are explicitly archived. Resolve only the caller's exact archive folder.
  releaseFileListed(releasedExe,executableSha);
  releaseFileListed(root/L"dist"/L"H3TierExport-build-receipt.json",sha(receiptRaw));
  auto testsPath=clean_path(releaseDirectory/L"H3TierExport-tests-receipt.json");
  auto testsRaw=read_all(testsPath);auto tests=parse_json(testsRaw);auto testsSha=sha(testsRaw);
  need(release.value("test_receipt_sha256","")==testsSha,"REVIEW_RELEASE_FROZEN_TEST_RECEIPT_DRIFT");
  need(tests.value("schema","")=="h3.tier-export.test-suite.v2"&&tests.value("passed",false)&&
       tests.value("source_current",false)&&tests.value("exe_sha256","")==executableSha&&
       tests.value("build_receipt_sha256","")==sha(receiptRaw),"REVIEW_RELEASE_TEST_BUILD_IDENTITY_MISMATCH");
  need(tests.contains("results")&&tests.at("results").is_array()&&!tests.at("results").empty(),
       "REVIEW_RELEASE_TEST_RESULTS_MISSING");
  for(const auto& result:tests.at("results"))
   need(result.is_object()&&result.contains("exit_code")&&result.at("exit_code").is_number_integer()&&result.at("exit_code")==0,
        "REVIEW_RELEASE_TEST_RESULT_FAILED");
  releaseFileListed(root/L"dist"/L"H3TierExport-tests-receipt.json",testsSha);
  frozenTests={{"path",str(testsPath)},{"sha256",testsSha}};
 }
 need(receipt.value("schema","")=="h3.tier-export.build.v2"&&
      receipt.value("passed",false)&&receipt.value("source_current",false),
      "REVIEW_BUILD_RECEIPT_NOT_CURRENT");
 need(receipt.contains("executable")&&receipt.at("executable").value("sha256","")==executableSha,
      "REVIEW_EXECUTABLE_DIFFERS_FROM_CURRENT_BUILD");
 need(receipt.contains("sources")&&receipt.at("sources").is_array(),"REVIEW_BUILD_SOURCE_ROWS_INVALID");
 auto canonicalSourcePath=[](const Json& value){
  need(value.is_string(),"REVIEW_BUILD_SOURCE_PATH_INVALID");
  auto source=value.get<std::string>();need(!source.empty(),"REVIEW_BUILD_SOURCE_PATH_INVALID");
  need(source.find('\0')==std::string::npos,"REVIEW_BUILD_SOURCE_PATH_INVALID");
  std::replace(source.begin(),source.end(),'\\','/');
  need(source.front()!='/'&&source.find(':')==std::string::npos,"REVIEW_BUILD_SOURCE_PATH_ABSOLUTE");
  std::size_t begin=0;
  while(begin<=source.size()){
   auto end=source.find('/',begin);if(end==std::string::npos)end=source.size();
   auto component=source.substr(begin,end-begin);
   need(!component.empty()&&component!="."&&component!="..","REVIEW_BUILD_SOURCE_PATH_TRAVERSAL_OR_NONCANONICAL");
   if(end==source.size())break;begin=end+1;
  }
  return source;
 };
 std::map<std::string,Json> sourceRows;
 for(const auto& row:receipt.at("sources")){
  need(row.is_object()&&row.contains("path")&&row.contains("sha256"),"REVIEW_BUILD_SOURCE_ROW_INVALID");
  auto relative=canonicalSourcePath(row.at("path"));
  need(valid_sha(row.at("sha256")),"REVIEW_BUILD_SOURCE_SHA_INVALID: "+relative);
  need(!sourceRows.contains(relative),"REVIEW_BUILD_SOURCE_DUPLICATE: "+relative);
  sourceRows.emplace(std::move(relative),row);
 }
 Json sources=Json::array();
 std::vector<std::string> requiredSources={"src/native_review.hpp","src/resample.hpp","src/wic.hpp","src/main.cpp"};
 requiredSources.insert(requiredSources.end(),additionalRequiredSources.begin(),additionalRequiredSources.end());
 for(const auto& relative:requiredSources){
  fs::path pathValue=root/path(relative);auto found=sourceRows.find(relative);
  need(found!=sourceRows.end(),std::string("REVIEW_BUILD_SOURCE_MISSING: ")+relative);
  const auto expected=found->second.at("sha256").get<std::string>();
  // The formal EXE contains these C++ kernels; only the candidate sources
  // are checked against the current mutable checkout.
  if(!formal){
   auto actual=sha(read_all(pathValue));
   need(expected==actual,std::string("REVIEW_BUILD_SOURCE_STALE: ")+relative);
  }
  sources.push_back({{"path",relative},{"sha256",expected}});
 }
 return {{"build_receipt",{{"path",str(receiptPath)},{"sha256",sha(receiptRaw)}}},
         {"executable",{{"path",str(executable)},{"sha256",executableSha}}},
         {"kernel_sources",sources},{"test_receipt",frozenTests}};
}

inline std::string native_review_write_png(Wic& writer,NativeReviewOutputGuard& guard,const fs::path& pathValue,
                                           const std::vector<std::uint8_t>& pixels,int width,int height){
 auto png=writer.encode(pixels,width,height,true);need(writer.decode_rgba(png,width,height)==pixels,"REVIEW_PNG_PIXEL_ROUNDTRIP_MISMATCH");
 return native_review_write_output(guard,pathValue,png);
}
inline void native_review_write_json(NativeReviewOutputGuard& guard,const fs::path& pathValue,const Json& value){
 auto text=value.dump(2)+"\n";std::vector<std::uint8_t> bytes(text.begin(),text.end());
 native_review_write_output(guard,pathValue,bytes);
}
inline std::vector<std::uint8_t> native_review_pixel_copy(const std::vector<std::uint8_t>& source,
                                                         int sourceW,int sourceH,Rect rect,
                                                         int outputW,int outputH,int left,int top){
 need(rect.l>=0&&rect.t>=0&&rect.r<=sourceW&&rect.b<=sourceH&&rect.w()>0&&rect.h()>0,"REVIEW_PIXEL_COPY_RECT_INVALID");
 need(left>=0&&top>=0&&left+rect.w()<=outputW&&top+rect.h()<=outputH,"REVIEW_PIXEL_COPY_PLACEMENT_INVALID");
 std::vector<std::uint8_t> output(std::size_t(outputW)*outputH*4,0);
 for(int y=0;y<rect.h();y++)std::memcpy(output.data()+(std::size_t(y+top)*outputW+left)*4,
     source.data()+(std::size_t(y+rect.t)*sourceW+rect.l)*4,std::size_t(rect.w())*4);
 return output;
}

inline Json render_native_review(const fs::path& specPath,const std::string& expectedSpecSha,
                                const fs::path& requestedOutdir,const fs::path& executable){
 need(expectedSpecSha.size()==64&&expectedSpecSha.find_first_not_of("0123456789abcdef")==std::string::npos,
      "REVIEW_SPEC_SHA256_INVALID");
 auto productRoot=native_review_product_root();auto specFile=clean_path(specPath);auto outdir=native_review_output_path(requestedOutdir);
 auto specRaw=read_all(specFile);need(sha(specRaw)==expectedSpecSha,"REVIEW_SPEC_SHA256_MISMATCH");auto spec=parse_json(specRaw);
 auto build=native_review_build_binding(productRoot,clean_path(executable));
 Wic decoder;Json sourceManifest,inventory;auto jobs=native_review_parse_spec(spec,specFile,outdir,productRoot,decoder,sourceManifest,inventory);
 for(const auto& item:jobs)native_review_town_recheck(item,"Town foreground before output");
 // Revalidate all inputs before creating a destination. The renderer never
 // decompresses the official inventory; the companion owns that authority.
 fs::path safeBase=native_review_output_path(productRoot/L"build"/L"experiments"/L"native-review");
 need(native_review_within(outdir,safeBase),"REVIEW_OUTPUT_OUTSIDE_IGNORED_ROOT");
 NativeReviewOutputGuard outputGuard;
 try{
 outputGuard.ensure_directory(safeBase);
 outputGuard.ensure_directory(outdir.parent_path());
 outputGuard.verify_all();
 outputGuard.create_new_directory(outdir);

 Json records=Json::array();std::vector<std::pair<fs::path,std::string>> inputHashes;
 inputHashes.push_back({path(sourceManifest.at("path")),sourceManifest.at("sha256").get<std::string>()});
 inputHashes.push_back({path(inventory.at("path")),inventory.at("sha256").get<std::string>()});
 inputHashes.push_back({specFile,expectedSpecSha});
 // Authored atlas views share one quality-sampled whole source, but the
 // retained mathematical cache must have an aggregate bound. A cache entry
 // is RGBA8 (four bytes per pixel), so 2 Mi pixels is 32 MiB of retained data.
 // Oversized valid inputs still render through one local temporary; they are
 // never rejected merely because they cannot be retained.
 static constexpr std::size_t authoredAtlasCacheLimitPixels=2u*1024u*1024u;
 std::map<std::string,Image> authoredAtlases;
 std::size_t authoredSamplingCalls=0,authoredCachedPixels=0,authoredPeakCachedPixels=0;
 bool authoredAtlasReport=false;
 for(const auto& item:jobs){
  auto sourceRaw=read_all(item.source);need(sha(sourceRaw)==item.sourceSha,"REVIEW_SOURCE_CHANGED_BEFORE_RENDER");
  std::string colorNote;auto decoded=decoder.decode(sourceRaw,true,colorNote);
  need(decoded.w==item.sourcePixels[0]&&decoded.h==item.sourcePixels[1],"REVIEW_SOURCE_DIMENSIONS_CHANGED");
  if(!item.foregroundSelection.is_null()){
   Image foreground(decoded.w,decoded.h);
   need(item.foregroundMask.size()==decoded.p.size(),"REVIEW_TOWN_SELECTION_MASK_SIZE_MISMATCH");
   for(std::size_t i=0;i<decoded.p.size();i++)if(item.foregroundMask[i])foreground.p[i]=decoded.p[i];
   decoded=std::move(foreground);
  }
  auto selected=item.hasAtlas?Image():crop_image(decoded,item.sourceRect);
  Image registered=selected;
  int registeredW=selected.w,registeredH=selected.h;
  bool atlasReused=false;
  std::vector<std::uint8_t> alphaCapPixels;
  if(item.hasStateView)inputHashes.push_back({path(item.stateView.at("authority").at("path")),
                                            item.stateView.at("authority").at("sha256").get<std::string>()});
  if(item.hasAtlas){
   authoredAtlasReport=true;
   registeredW=item.atlasPixels[0];registeredH=item.atlasPixels[1];
   auto cacheKey=item.sourceSha+":"+std::to_string(registeredW)+"x"+std::to_string(registeredH);
   auto found=authoredAtlases.find(cacheKey);atlasReused=found!=authoredAtlases.end();
   Image sampledAtlas;
   if(!atlasReused){
    const auto footprint=std::size_t(registeredW)*std::size_t(registeredH);
    if(footprint>authoredAtlasCacheLimitPixels||authoredCachedPixels>authoredAtlasCacheLimitPixels-footprint){
     authoredAtlases.clear();authoredCachedPixels=0;
    }
    auto sampled=resize_cpu(decoded,registeredW,registeredH,Filter::H3Quality);
    ++authoredSamplingCalls;
    if(footprint<=authoredAtlasCacheLimitPixels){
     found=authoredAtlases.emplace(cacheKey,std::move(sampled)).first;
     authoredCachedPixels+=footprint;
     authoredPeakCachedPixels=std::max(authoredPeakCachedPixels,authoredCachedPixels);
    }else{
     sampledAtlas=std::move(sampled);
    }
   }
   const Image* sampledImage=atlasReused?&found->second:
       (registeredW>0&&registeredH>0&&std::size_t(registeredW)*std::size_t(registeredH)<=authoredAtlasCacheLimitPixels
         ?&authoredAtlases.find(cacheKey)->second:&sampledAtlas);
   registered=crop_image(*sampledImage,item.atlasRect);
   inputHashes.push_back({path(item.atlasView.at("authority").at("path")),
                         item.atlasView.at("authority").at("sha256").get<std::string>()});
   if(!item.alphaCap.is_null()){
    auto raw=read_all(item.alphaCapPath);auto digest=item.alphaCap.at("sha256").get<std::string>();
    need(sha(raw)==digest,"REVIEW_AUTHORED_ALPHA_CHANGED_BEFORE_RENDER");
    alphaCapPixels=decoder.decode_rgba(raw,registered.w,registered.h);
    inputHashes.push_back({item.alphaCapPath,digest});
   }
  }
  if(item.registration=="integer-transparent-padding"){
   const auto samplingBase=item.sourceLayout=="native-canvas"?item.canvas:item.crop;
   registeredW=samplingBase[0]*item.commonK;registeredH=samplingBase[1]*item.commonK;registered=Image(registeredW,registeredH);
   for(int y=0;y<selected.h;y++)std::copy_n(selected.p.data()+std::size_t(y)*selected.w,selected.w,
      registered.p.data()+std::size_t(y+item.registrationTop)*registeredW+item.registrationLeft);
  }
  const auto folder=outdir/path(item.id);outputGuard.create_new_directory(folder);
  Json outputs=Json::object();
  if(item.registration=="integer-transparent-padding"&&item.render1x){
   auto rawRgba=decoder.decode_rgba(sourceRaw,item.sourcePixels[0],item.sourcePixels[1]);
   auto padded=native_review_pixel_copy(rawRgba,item.sourcePixels[0],item.sourcePixels[1],item.sourceRect,
                                        registeredW,registeredH,item.registrationLeft,item.registrationTop);
   fs::path diagnostic=folder/L"registration-padding-diagnostic.png";
   auto diagnosticSha=native_review_write_png(decoder,outputGuard,diagnostic,padded,registeredW,registeredH);
   outputs["registration_padding_diagnostic"]={{"path",str(diagnostic)},{"pixels",{registeredW,registeredH}},
       {"operation","diagnostic preview only: alpha-zero padding plus WIC-decoded RGBA8 pixel copy; no resampling; source encoding and bit depth are not preserved in this preview"},
       {"source_encoding_preserved",false},{"source_png_modified",false},
       {"png_sha256",diagnosticSha}};
  }
  for(int scale:{1,3}){
   if(scale==1&&!item.render1x)continue;
   if(scale==3&&!item.render3x)continue;
   const int cropW=item.crop[0]*scale,cropH=item.crop[1]*scale;
   const bool fullCanvas=item.sourceLayout=="native-canvas";
   const int sampleW=fullCanvas?item.canvas[0]*scale:cropW,sampleH=fullCanvas?item.canvas[1]*scale:cropH;
   const bool croppedOutput=item.outputLayout=="native-crop";
   const int outputW=croppedOutput?cropW:item.canvas[0]*scale,outputH=croppedOutput?cropH:item.canvas[1]*scale;
   need(!item.hasAtlas||(registered.w==sampleW&&registered.h==sampleH),"REVIEW_AUTHORED_STATE_MUST_NOT_BE_RESAMPLED");
   auto resized=item.hasAtlas?registered:resize_cpu(registered,sampleW,sampleH,Filter::H3Quality);
   std::vector<std::uint8_t> pixels;Json canvasCropProof=nullptr;
   if(fullCanvas&&croppedOutput){
    // Keep the original full-grid sampling phase and quantize exactly once.
    // A crop may discard only encoded alpha-zero pixels, including no alpha1.
    auto canvasPixels=place_rgba(resized,sampleW,sampleH,0,0,true);
    const int ox=item.origin[0]*scale,oy=item.origin[1]*scale;
    std::uint64_t positive=0,alpha1=0,outside=0,outsideAlpha1=0;
    for(int y=0;y<sampleH;y++)for(int x=0;x<sampleW;x++){
     const auto alpha=canvasPixels[(std::size_t(y)*sampleW+x)*4+3];
     if(!alpha)continue;++positive;if(alpha==1)++alpha1;
     if(x<ox||y<oy||x>=ox+cropW||y>=oy+cropH){++outside;if(alpha==1)++outsideAlpha1;}
    }
    need(outside==0,"REVIEW_CROP_OUTPUT_POSITIVE_ALPHA_OUTSIDE_NATIVE_CROP: "+item.id+
         " pixels="+std::to_string(outside)+" alpha1="+std::to_string(outsideAlpha1));
    pixels=native_review_pixel_copy(canvasPixels,sampleW,sampleH,Rect{ox,oy,ox+cropW,oy+cropH},cropW,cropH,0,0);
    canvasCropProof={{"operation","single-native-canvas-sample-then-exact-rgba8-crop"},
        {"sampled_canvas_pixels",{sampleW,sampleH}},{"crop_rect_3x",{ox,oy,cropW,cropH}},
        {"positive_alpha_pixels_before_crop",positive},{"positive_alpha_pixels_after_crop",positive},
        {"alpha1_pixels_before_crop",alpha1},{"alpha1_pixels_after_crop",alpha1},
        {"excluded_positive_alpha_pixels",0},{"sampling_calls",1},{"intermediate_png_files",0}};
   }else pixels=place_rgba(resized,outputW,outputH,
                           fullCanvas||croppedOutput?0:item.origin[0]*scale,fullCanvas||croppedOutput?0:item.origin[1]*scale,true);
   if(!alphaCapPixels.empty()){
    const int ox=fullCanvas||croppedOutput?0:item.origin[0]*scale,oy=fullCanvas||croppedOutput?0:item.origin[1]*scale;
    // The functional contract caps only encoded alpha; even RGB hidden by the
    // cap remains exactly the already-quantized model RGB. No second sampling.
    for(int y=0;y<sampleH;y++)for(int x=0;x<sampleW;x++){
     auto target=(std::size_t(y+oy)*outputW+x+ox)*4+3;
     auto mask=(std::size_t(y)*sampleW+x)*4+3;
     pixels[target]=std::min(pixels[target],alphaCapPixels[mask]);
    }
   }
   const auto name=scale==1?L"candidate-1x.png":L"candidate-3x.png";fs::path output=folder/name;
   auto outputSha=native_review_write_png(decoder,outputGuard,output,pixels,outputW,outputH);
   outputs[scale==1?"candidate_1x":"candidate_3x"]={{"path",str(output)},
       {"pixels",{outputW,outputH}},
       {"crop_pixels",{cropW,cropH}},{"origin_pixels",{item.origin[0]*scale,item.origin[1]*scale}},
       {"png_sha256",outputSha},{"rgba_sha256",sha_bytes(pixels.data(),pixels.size())}};
   if(croppedOutput)outputs[scale==1?"candidate_1x":"candidate_3x"]["layout"]="native-crop";
    if(!canvasCropProof.is_null())outputs["candidate_3x"]["canvas_crop_proof"]=canvasCropProof;
  }
  if(item.hasReference&&item.render1x){
   auto refRaw=read_all(item.referencePath);need(sha(refRaw)==item.referenceSha,"REVIEW_REFERENCE_CHANGED_BEFORE_RENDER");
   const auto layout=item.reference.at("layout").get<std::string>();
   const int refW=layout=="native-crop"?item.crop[0]:item.canvas[0];
   const int refH=layout=="native-crop"?item.crop[1]:item.canvas[1];
   auto refPixels=decoder.decode_rgba(refRaw,refW,refH);
   if(layout=="native-crop")refPixels=native_review_pixel_copy(refPixels,refW,refH,Rect{0,0,refW,refH},
      item.canvas[0],item.canvas[1],item.origin[0],item.origin[1]);
   fs::path referenceOut=folder/L"reference-1x.png";
   auto referenceSha=native_review_write_png(decoder,outputGuard,referenceOut,refPixels,item.canvas[0],item.canvas[1]);
   outputs["reference_1x"]={{"path",str(referenceOut)},{"pixels",{item.canvas[0],item.canvas[1]}},
       {"source_path",str(item.referencePath)},{"source_sha256",item.referenceSha},
       {"layout",layout},{"png_sha256",referenceSha},
       {"operation","native 1X pixels copied/placed; no resampling"}};
   inputHashes.push_back({item.referencePath,item.referenceSha});
  }
  if(item.hasReference&&!item.render1x)inputHashes.push_back({item.referencePath,item.referenceSha});
  inputHashes.push_back({item.source,item.sourceSha});
  const int rw=item.sourceRect.w(),rh=item.sourceRect.h();
  const auto samplingBase=item.sourceLayout=="native-canvas"?item.canvas:item.crop;
  const bool capacity3x=rw>=3*samplingBase[0]&&rh>=3*samplingBase[1];
  const std::string capacity=item.registration=="full-viewport-axis-normalization"?"FULL_VIEWPORT_AXIS_NORMALIZATION_1X_DIAGNOSTIC_ONLY":
      item.registration=="full-viewport-axis-normalization-3x"?"FULL_VIEWPORT_AXIS_NORMALIZATION_3X_SOURCE_DIMENSIONS_ONLY":
      capacity3x?"POSSIBLE_3X_EFFECTIVE_RECT_UNVERIFIED":"UNDER_3X_SOURCE_RECT_DIMENSION_UPPER_BOUND";
  records.push_back({{"id",item.id},{"key",item.key},{"targets",item.targets},
      {"geometry_binding_sha256",native_review_binding_sha(inventory.at("sha256").get<std::string>(),item.key,item.targets)},
      {"source_png",str(item.source)},{"source_sha256",item.sourceSha},{"source_pixels",item.sourcePixels},
      {"source_rect",{item.sourceRect.l,item.sourceRect.t,item.sourceRect.w(),item.sourceRect.h()}},
      {"registration",item.registration},{"common_k",item.hasAtlas||item.hasStateView?Json(nullptr):Json(item.commonK)},{"source_layout",item.sourceLayout},
      {"source_view_proof",{{"source_positive_pixels",item.sourcePositive},{"retained_positive_pixels",item.viewPositive},
                            {"excluded_positive_pixels",item.sourcePositive-item.viewPositive},
                            {"all_positive_preserved",item.sourcePositive==item.viewPositive}}},
      {"source_rect_pixels",{rw,rh}},{"source_rect_capacity_3x",capacity},
      {"source_rect_effective_pixels_verified",false},{"registered_pixels",{registeredW,registeredH}},
      {"registration_offset",{item.registrationLeft,item.registrationTop}},
      {"render_input_pipeline",!item.foregroundSelection.is_null()
          ?"Town authority-bound same-grid foreground OR copy -> original integer padding -> H3Quality single3X sampling"
          :item.hasStateView
          ?"author-bound state pixel copy -> H3Quality explicit axis normalization directly to3X -> native placement"
          :item.hasAtlas
          ?"whole source -> H3Quality authored 3X atlas -> integer state pixel copy -> native placement; no state resampling"
           :item.sourceLayout=="native-canvas"&&item.outputLayout=="native-crop"
           ?"whole native canvas -> H3Quality single sampling -> exact RGBA8 native crop; zero positive alpha excluded"
          :item.sourceLayout=="native-canvas"
          ?"whole native canvas -> H3Quality direct sampling; native crop origin is not applied again"
          :item.registration=="integer-transparent-padding"
          ?"source crop -> alpha-zero integer canvas pixel-copy -> H3Quality direct sampling from the same registered input for each tier"
          :item.registration=="full-viewport-axis-normalization"
          ?"source crop -> full viewport axis normalization -> H3Quality direct sampling for 1X review"
          :item.registration=="full-viewport-axis-normalization-3x"
          ?"whole source -> full viewport axis normalization -> H3Quality direct sampling for 3X review"
          :"source crop -> H3Quality direct sampling from the same source input for each tier"},
      {"reference_provenance",item.hasReference?Json("caller-bound comparison"):Json(nullptr)},
      {"rendered_tiers",item.render1x?(item.render3x?Json::array({1,3}):Json::array({1})):Json::array({3})},
      {"color_handling",colorNote},{"outputs",outputs}});
  if(item.paddingRegistrationExplicit)
   records.back()["padding_registration"]={{"common_k",item.commonK},{"offset",{item.registrationLeft,item.registrationTop}}};
  if(item.outputLayout=="native-crop")records.back()["output_layout"]="native-crop";
  if(!item.foregroundSelection.is_null()){
   records.back()["foreground_selection"]=item.foregroundSelection;
   records.back()["foreground_selection_proof"]=item.foregroundProof;
   records.back()["source_view_proof_scope"]="whole parent ROI evidence; explicit Town selected-domain disposition is separate";
  }
  if(item.hasAtlas){
   records.back()["atlas_view"]=item.atlasView;records.back()["alpha_cap_3x"]=item.alphaCap;
   records.back()["atlas_sample_reused"]=atlasReused;
   records.back()["source_view_proof_scope"]="whole-master sampling input; output is the declared authored state only";
  }
  if(item.hasStateView){
   records.back()["state_view"]=item.stateView;
   records.back()["source_view_proof_scope"]="declared UI-state sampling input; other atlas states excluded";
  }
 }
 // Check every bound source again before sealing the receipt. A changed input
 // aborts and the output guard removes only files/directories created here.
 for(const auto& [file,digest]:inputHashes)native_review_hash_file(file,digest,"final input currentness");
 for(const auto& item:jobs)native_review_town_recheck(item,"final input currentness");
 Json report={{"schema","h3.native-review.render-receipt.v1"},{"status","REVIEW_REQUIRED"},
   {"spec",{{"path",str(specFile)},{"sha256",expectedSpecSha}}},
   {"source_manifest",sourceManifest},{"official_inventory",inventory},
   {"official_geometry_verified_by_renderer",false},
   {"official_geometry_verification_owner","review_native_batch.py"},
   {"algorithm",QUALITY_ALGORITHM},{"build_binding",build},
   {"output_root",str(outdir)},{"items",records},
   {"source_pngs_modified",0},{"reference_pngs_modified",0},
   {"authority",{{"art_approved",false},{"human_approved",false},{"runtime_accepted",false},
                  {"game_deployed",false},{"model_inference_performed",false},
                  {"effective_pixels_verified",false},{"automatic_art_score",false}}}};
 if(authoredAtlasReport){
  report["authored_atlas_sampling_calls"]=authoredSamplingCalls;
  report["authored_atlas_cache_limit_pixels"]=authoredAtlasCacheLimitPixels;
  report["authored_atlas_cached_pixels"]=authoredCachedPixels;
  report["authored_atlas_peak_cached_pixels"]=authoredPeakCachedPixels;
 }
 native_review_write_json(outputGuard,outdir/L"render-receipt.json",report);
 outputGuard.verify_all();outputGuard.keep();return report;
 }catch(const std::exception& error){
  auto cleanup=outputGuard.cleanup();
  if(cleanup.empty())throw;
  throw std::runtime_error(std::string(error.what())+"; "+cleanup);
 }catch(...){
  outputGuard.cleanup();throw;
 }
}

inline std::map<std::string,std::string> native_review_cli_arguments(const std::vector<std::string>& input){
 const std::set<std::string> allowed={"--spec","--spec-sha256","--outdir"};
 std::map<std::string,std::string> result;
 for(size_t i=0;i<input.size();i++){
  const auto& tokenValue=input[i];auto eq=tokenValue.find('=');auto name=tokenValue.substr(0,eq);
  need(allowed.contains(name),"REVIEW_CLI_UNKNOWN_ARGUMENT: "+name);
  std::string value;
  if(eq!=std::string::npos)value=tokenValue.substr(eq+1);
  else{need(i+1<input.size(),"REVIEW_CLI_VALUE_REQUIRED: "+name);value=input[++i];}
  need(!value.empty()&&value.rfind("--",0)!=0,"REVIEW_CLI_VALUE_REQUIRED: "+name);
  need(!result.contains(name),"REVIEW_CLI_DUPLICATE_ARGUMENT: "+name);result.emplace(name,value);
 }
 for(const auto& name:allowed)need(result.contains(name),"REVIEW_CLI_REQUIRED_ARGUMENT: "+name);
 return result;
}

} // namespace h3tier
