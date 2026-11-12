"""Guard the APNG preview's folder picker, scan, one-shot replay and manual removal."""

from pathlib import Path
import re
import unittest

GUI = Path(__file__).resolve().parents[1] / "src" / "apng_gui.hpp"
SOURCE = GUI.read_text(encoding="utf-8")


def function_body(name: str) -> str:
    match = re.search(rf"inline [^\n]*\b{name}\([^\n]*\)\s*\{{(.*?)\n\}}", SOURCE, re.S)
    if not match:
        raise AssertionError(f"missing function: {name}")
    return match.group(1)


class ApngGuiInteractionTests(unittest.TestCase):
    def test_folder_picker_uses_a_dedicated_sta_worker(self):
        picker = function_body("start_folder_picker")
        self.assertIn("std::thread([window,outputDirectory]", picker)
        self.assertIn("CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED)", picker)
        self.assertIn("read_folder_dialog(window)", picker)
        self.assertIn("WM_FOLDER_PICK_DONE", picker)

    def test_directory_scan_and_preview_decode_run_off_the_ui_thread(self):
        scan = function_body("scan_folder_async")
        preview = function_body("load_group_preview")
        self.assertIn("std::thread([window,generation,folder]", scan)
        self.assertIn("scan_folder_data(folder)", scan)
        self.assertIn("WM_SCAN_DONE", scan)
        self.assertIn("std::thread([window,generation,index,group=std::move(group)]", preview)
        self.assertIn("WM_PREVIEW_DONE", preview)

    def test_completed_one_shot_restarts_from_frame_one(self):
        playback = function_body("start_preview_playback")
        self.assertIn("if(restartAtEnd&&!loop&&s.currentFrame+1>=s.previewFrames.size())s.currentFrame=0", playback)
        self.assertIn("else start_preview_playback(*s,true)", SOURCE)

    def test_context_menu_only_removes_manual_group_from_the_list(self):
        remove = function_body("delete_manual_group")
        self.assertIn("s.items[index].manual", remove)
        self.assertIn("s.items.erase(s.items.begin()+index)", remove)
        self.assertIn("不删除源 PNG", remove)
        self.assertNotRegex(remove, r"\b(DeleteFile|remove|unlink)\s*\(")
        self.assertIn("case WM_CONTEXTMENU", SOURCE)
        self.assertIn("show_group_context_menu(*s,lp)", SOURCE)


if __name__ == "__main__":
    unittest.main()
