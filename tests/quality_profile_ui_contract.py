"""Exercise the real native wizard and the same-EXE frozen QA route."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import time
import unittest
import uuid

PRODUCT=Path(__file__).resolve().parents[1]
EXE=PRODUCT/'build/candidate/H3TierExport.exe'
u=c.WinDLL('user32',use_last_error=True)
u.GetDlgItem.argtypes=[w.HWND,c.c_int];u.GetDlgItem.restype=w.HWND
u.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
u.SetWindowTextW.argtypes=[w.HWND,w.LPCWSTR]
u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,c.c_void_p];u.SendMessageW.restype=c.c_ssize_t
CALLBACK=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
u.EnumWindows.argtypes=[CALLBACK,w.LPARAM]
u.EnumChildWindows.argtypes=[w.HWND,CALLBACK,w.LPARAM]

def wait_for(get,seconds=45):
 until=time.monotonic()+seconds
 while time.monotonic()<until:
  value=get()
  if value:return value
  time.sleep(.05)
 raise AssertionError('native UI operation timed out')

def window(pid,klass):
 result=[]
 @CALLBACK
 def visit(hwnd,_):
  current=w.DWORD();u.GetWindowThreadProcessId(hwnd,c.byref(current))
  if current.value==pid:
   name=c.create_unicode_buffer(256);u.GetClassNameW(hwnd,name,256)
   if name.value==klass:result.append(hwnd)
  return True
 u.EnumWindows(visit,0);return result[0] if result else None

class QualityWizard(unittest.TestCase):
 def test_one_click_freezes_scope_and_reports_missing_frame(self):
  fixture=PRODUCT/'build/experiments/quality-ui-fixtures'/uuid.uuid4().hex;fixture.mkdir(parents=True)
  masters=fixture/'masters';masters.mkdir()
  inventory=fixture/'inventory.json';inventory.write_text(json.dumps({'rows':[
   {'kind':'def','identity':'UNIT','group':0,'frame':0,'canvas':[32,32],'crop':[30,30],'anchor':[1,1]}]}))
  standard=fixture/'standard.json';standard.write_text('{"version":"UI synthetic fixture"}')
  startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
  proc=subprocess.Popen([str(EXE)],startupinfo=startup,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   main=wait_for(lambda:window(proc.pid,'H3TierExport.MainWindow'))
   u.PostMessageW(main,0x111,1021,0)
   task=wait_for(lambda:window(proc.pid,'H3TierExport.TaskCenter'))
   u.PostMessageW(task,0x111,2213,0)
   wizard=wait_for(lambda:window(proc.pid,'H3TierExport.QualityProfile'))
   for control,text in [(2402,str(masters)),(2403,'UNIT'),(2406,str(inventory)),(2407,str(standard))]:
    # SetWindowTextW across processes changes the caption cache, not the
    # Edit control's text buffer. WM_SETTEXT is system-marshalled correctly.
    handle=u.GetDlgItem(wizard,control);self.assertTrue(handle)
    value=c.create_unicode_buffer(text);self.assertTrue(u.SendMessageW(handle,0xC,0,c.cast(value,c.c_void_p)))
    observed=c.create_unicode_buffer(32768);u.SendMessageW(handle,0xD,32768,c.cast(observed,c.c_void_p))
    self.assertEqual(observed.value,text)
   before=set((PRODUCT/'build/experiments/quality-requests').glob('*/manifest.json'))
   u.PostMessageW(wizard,0x111,2431,0)
   manifest=wait_for(lambda:next(iter(set((PRODUCT/'build/experiments/quality-requests').glob('*/manifest.json'))-before),None))
   operation=json.loads(manifest.read_text(encoding='utf-8'))['operation_id']
   report=PRODUCT/'build/experiments/scale-layout'/operation/'quality-report.json'
   wait_for(report.is_file)
   data=json.loads(report.read_text(encoding='utf-8'))
   self.assertEqual(data['scope'],[['def','UNIT',0,0]])
   self.assertIn('MISSING_OFFICIAL_SLOT_FILE',data['items'][0]['errors'])
   self.assertFalse(data['verification_coverage']['complete_validation'])
   self.assertEqual(list(masters.rglob('*.png')),[])
   (fixture/'ui-evidence.json').write_text(json.dumps({'exe':str(EXE),'manifest':str(manifest),'report':str(report),'native_button_route_executed':True,'source_images_written':0},indent=2))
  except Exception:
   messages=[];dialog=window(proc.pid,'#32770')
   if dialog:
    @CALLBACK
    def collect(hwnd,_):
     value=c.create_unicode_buffer(4096);u.GetWindowTextW(hwnd,value,4096)
     if value.value:messages.append(value.value)
     return True
    u.EnumChildWindows(dialog,collect,0)
   (fixture/'ui-error.json').write_text(json.dumps(messages,ensure_ascii=False))
   raise AssertionError('wizard failed: '+repr(messages))
  finally:
   # Only this test's own process/windows, never a user's existing tool instance.
   for klass in ('H3TierExport.QualityProfile','H3TierExport.TaskCenter','H3TierExport.MainWindow'):
    hwnd=window(proc.pid,klass)
    if hwnd:u.PostMessageW(hwnd,0x10,0,0)
   try:proc.wait(timeout=10)
   except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=10)

if __name__=='__main__':unittest.main()
