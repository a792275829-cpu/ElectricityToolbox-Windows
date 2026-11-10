from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]
from toolbox.app import PAGE_NAMES, ToolboxApp
from native_bridge import NativeService
from github_update import Release
from update_ui import UpdateController


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ToolboxApp(workspace=Path(self.temp.name), page_factories={
            name: lambda parent, paths, registry: tk.Frame(parent) for name in PAGE_NAMES
        }, check_updates=lambda: self.controller.show())
        self.service = NativeService(self.app, emit=Mock())
        self.updater = Mock(current="0.1.3")
        self.updater.check.return_value = None
        self.controller = UpdateController(self.app, self.service, self.updater)
        self.service.updater = self.controller
        self.release = Release("v0.2.0", "测试更新说明", "https://github.com/test/repo/releases/tag/v0.2.0", {"name": "test.dmg"})

    def tearDown(self):
        self.app.destroy()
        self.temp.cleanup()

    def pump(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_background_result_only_shows_badge_manual_check_opens_notes(self):
        self.controller.checked(self.release, None)
        self.assertEqual(self.app.update_button.winfo_manager(), "pack")
        self.assertIsNone(self.controller.window)
        self.updater.check.return_value = self.release
        self.controller.check(manual=True)
        self.pump(lambda: self.controller.window is not None)
        self.assertEqual(self.controller.window.primary.cget("text"), "开始更新")
        self.assertIs(self.controller.window.controller.app, self.app)

    def test_windows_notes_describe_full_windows_installer(self):
        self.controller.release = Release('v0.1.22','Windows 更新','https://github.com/test/repo/releases/tag/v0.1.22',
                                          {'name':'ElectricityToolbox_0.1.22_windows_x64_setup.exe'})
        self.controller.show()
        window=self.controller.window
        self.assertIn('Windows',window.platform_text)
        self.assertNotIn('增量',window.status.get())
        self.assertNotIn('Apple',window.platform_text)

    def test_windows_progress_shows_delta_size_and_preparation_phase(self):
        self.controller.release = Release('v0.1.28','更新','https://github.com/test/repo/releases/tag/v0.1.28',
                                          {'name':'ElectricityToolbox_0.1.28_windows_x64_setup.exe'})
        self.controller.show()
        self.updater.update_mode='delta';self.updater.download_bytes=2*1024**2
        self.updater.phase='复用运行环境并校验新版本'
        self.controller.window.download_progress(.8,None)
        self.assertIn('增量更新 · 2.0 MB',self.controller.window.status.get())
        self.assertIn('复用运行环境',self.controller.window.status.get())

    def test_windows_full_fallback_states_the_rejected_patch_reason(self):
        self.controller.release = Release('v0.1.35','更新','https://github.com/test/repo/releases/tag/v0.1.35',
                                          {'name':'ElectricityToolbox_0.1.35_windows_x64_setup.exe'})
        self.controller.show()
        window=self.controller.window
        self.updater.update_mode='full';self.updater.download_bytes=310*1024**2
        self.updater.phase='下载完整安装包'
        # A rejected patch must not look like a silent double download.
        self.updater.fallback_reason='旧版程序文件已变化或版本不匹配，改用完整安装包'
        window.download_progress(.12,None)
        text=window.status.get()
        self.assertIn('完整更新（补丁不适用）',text)
        self.assertIn('旧版程序文件已变化',text)
        self.assertIn('已改用完整安装包',text)
        # Without a reason (and for mock/unset updaters) the extra line stays hidden.
        self.updater.fallback_reason=''
        window.download_progress(.12,None)
        self.assertNotIn('补丁不适用',window.status.get())
        del self.updater.fallback_reason
        window.download_progress(.12,None)
        self.assertNotIn('补丁不适用',window.status.get())

    def test_automatic_errors_are_silent_but_manual_errors_are_visible(self):
        with patch("update_ui.messagebox.showerror") as error:
            self.controller.checked(None, ValueError("离线"))
            error.assert_not_called()
            self.controller.manual = True
            self.controller.checked(None, ValueError("离线"))
            error.assert_called_once()

    def test_failed_check_offers_footer_retry_and_success_hides_it(self):
        self.controller.checked(None,ValueError('offline'))
        self.assertEqual(self.app.update_retry_button.winfo_manager(),'grid')
        self.assertEqual(self.app.update_retry_button.cget('text'),'检查更新')
        with patch.object(self.controller,'worker') as worker,patch('update_ui.messagebox.showinfo'):
            self.app.update_retry_button.invoke()
            self.assertTrue(self.controller.checking)
            worker.call_args.args[1](None,None)
        self.assertEqual(self.app.update_retry_button.winfo_manager(),'')

    def test_dmg_download_cancellation_waits_for_worker_ack(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.controller, "worker") as worker:
            window.perform()
            self.assertTrue(window.downloading)
            self.service.emit.assert_not_called()
            window.dismiss()
            self.assertTrue(window.cancel_download.is_set())
            self.assertTrue(window.winfo_exists())
            from github_update import Cancelled
            worker.call_args.args[1](None, Cancelled("已取消下载"))
            self.assertFalse(window.winfo_exists())

    def test_automatic_install_keeps_task_guard_and_failure_unlocks_inputs(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.app.registry, "has_running_tasks", return_value=True):
            with self.assertRaisesRegex(ValueError, "等待"):
                self.service.call("prepare_update", {})
        self.assertFalse(self.service.maintenance)
        self.service.call("prepare_update", {})
        self.service.call("update_progress", {"stage": "installing", "percent": 100})
        self.assertTrue(window.installing)
        self.assertTrue(self.service.maintenance)
        window.dismiss()
        self.assertTrue(window.winfo_exists())
        self.service.call("update_failed", {"error": "签名校验失败"})
        self.assertFalse(window.installing)
        self.assertFalse(self.service.maintenance)
        self.assertFalse(self.service._locked_widgets)
        self.assertEqual(window.status.get(), "签名校验失败")
        self.assertEqual(window.primary.cget("text"), "重试更新")

    def test_verified_dmg_automatically_prepares_commits_and_exits(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        self.updater.download.return_value = Path(self.temp.name) / "test.dmg"
        calls = []
        original = self.service.call
        def call(method, params):
            calls.append(method)
            return True if method == 'finish_update' else original(method, params)
        with patch.object(self.service, 'call', side_effect=call):
            window.perform()
            self.pump(lambda: 'finish_update' in calls)
        self.updater.prepare_update.assert_called_once()
        self.updater.prepare_install.assert_called_once_with(self.release, self.updater.download.return_value)
        self.updater.commit_install.assert_called_once()
        self.service.emit.assert_not_called()
        self.assertFalse(window.downloading)
        self.assertTrue(window.installing)
        self.assertTrue(self.service.maintenance)
        self.assertIn('自动安装', window.status.get())
        self.assertEqual(calls, ['prepare_update', 'finish_update'])

    def test_failed_download_can_be_retried_without_opening(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        self.updater.download.side_effect = ValueError("安装包校验失败")
        window.perform()
        self.pump(lambda: not window.downloading)
        self.updater.prepare_install.assert_not_called()
        self.updater.abort_update.assert_called_once()
        self.assertFalse(self.service.maintenance)
        self.assertIn("校验失败", window.status.get())
        self.assertEqual(window.primary.cget("text"), "重试下载")

    def test_prepare_failure_keeps_application_open_and_unlocks_inputs(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        self.updater.download.return_value = Path(self.temp.name) / 'test.dmg'
        self.updater.prepare_install.side_effect = ValueError('镜像签名错误')
        window.perform()
        self.pump(lambda: '镜像签名错误' in window.status.get())
        self.updater.commit_install.assert_not_called()
        self.assertFalse(self.service.maintenance)
        self.assertFalse(window.installing)
        self.assertTrue(self.app.winfo_exists())

    def test_running_task_prevents_update_before_any_download(self):
        self.controller.release = self.release
        self.controller.show()
        with patch.object(self.app.registry, 'has_running_tasks', return_value=True):
            self.controller.window.perform()
        self.updater.download.assert_not_called()
        self.assertFalse(self.service.maintenance)
        self.assertIn('等待', self.controller.window.status.get())

    def test_cancel_after_download_completed_does_not_start_install(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.controller, 'worker') as worker:
            window.perform()
            window.dismiss()
            window.download_finished(Path(self.temp.name) / 'verified.dmg', None)
            worker.call_args.args[1](True, None)
        self.updater.prepare_install.assert_not_called()
        self.assertFalse(self.service.maintenance)
        self.assertFalse(window.winfo_exists())

    def test_retry_button_shows_progress_while_rechecking(self):
        self.controller.checked(None, ValueError('offline'))
        self.assertEqual(self.app.update_retry_button.cget('text'), '检查更新')
        with patch.object(self.controller, 'worker'), patch('update_ui.messagebox.showinfo'):
            self.app.update_retry_button.invoke()
            self.assertEqual(self.app.update_retry_button.cget('text'), '检查中…')
            self.assertIn('disabled', self.app.update_retry_button.state())
            self.controller.checked(None, None)
        self.assertEqual(self.app.update_retry_button.winfo_manager(), '')

    def test_update_controls_stay_interactive_while_business_inputs_are_locked(self):
        self.controller.release = self.release
        self.controller.show()
        window = self.controller.window
        with patch.object(self.controller, 'worker'):
            window.perform()
        self.assertTrue(self.service.maintenance)
        self.assertFalse(any('ToolboxUpdateLock' in tag for tag in window.cancel_button.bindtags()))

    def test_window_follows_dpi_scale_and_stays_inside_work_area(self):
        self.controller.release = self.release
        with patch('update_ui.window_scale', return_value=2.0), \
                patch('update_ui.windows_work_area', return_value=(0, 0, 3840, 2160)), \
                patch('update_ui.windows_frame_metrics', return_value=(16, 39)):
            self.controller.show()
            window = self.controller.window
            self.app.update()
        self.assertGreaterEqual(window.winfo_width(), int(window.WIDTH * 2) - 40)
        self.assertLessEqual(window.winfo_height(), 2160 - 39)
        self.assertGreaterEqual(window.winfo_x(), 0)
        self.assertGreaterEqual(window.winfo_y(), 0)
        self.assertLessEqual(window.winfo_x() + window.winfo_width(), 3840)
        self.assertLessEqual(window.winfo_y() + window.winfo_height(), 2160)

    def test_long_notes_open_taller_with_actions_visible(self):
        notes = "\n".join(f"第{i}条：修复更新窗口过小导致无法完整查看更新说明。" for i in range(80))
        self.controller.release = Release("v0.1.34", notes, "https://example.com", {"name": "setup.exe"})
        self.controller.show()
        window = self.controller.window
        self.app.update()
        self.assertGreater(window.winfo_height(), int(window.MIN_HEIGHT * window.winfo_fpixels('1i') / 96))
        bottom = window.primary.winfo_rooty() + window.primary.winfo_height()
        self.assertLessEqual(bottom, window.winfo_rooty() + window.winfo_height())

    def test_short_notes_stay_at_minimum_height(self):
        self.controller.release = Release("v0.1.34", "仅修复更新窗口尺寸", "https://example.com", {"name": "setup.exe"})
        self.controller.show()
        window = self.controller.window
        self.app.update()
        minimum = int(window.MIN_HEIGHT * window.winfo_fpixels('1i') / 96)
        self.assertGreaterEqual(window.winfo_height(), minimum)
        self.assertLessEqual(window.winfo_height(), minimum + 120)

    def test_release_notes_markdown_renders_as_readable_text(self):
        notes = ("## [0.1.34]\n\n"
                 "- 修复 `update_ui` 的 **窗口尺寸**，详见 [发布页](https://example.com)\n\n"
                 "### 细节\n\n> 引用说明\n")
        self.controller.release = Release("v0.1.34", notes, "https://example.com", {"name": "setup.exe"})
        self.controller.show()
        window = self.controller.window
        text = window.notes.get("1.0", "end-1c")
        self.assertNotIn("##", text)
        self.assertNotIn("**", text)
        self.assertNotIn("`", text)
        self.assertIn("修复 update_ui 的 窗口尺寸，详见 发布页", text)
        self.assertIn("引用说明", text)
        self.assertTrue(window.notes.tag_ranges("heading"))


class UpdateScheduleTests(unittest.TestCase):
    class Scheduler:
        def __init__(self):
            self.pending={};self.serial=0
            self.set_update_available=Mock();self.set_update_check_failed=Mock()
        def after(self,delay,callback):
            self.serial+=1;self.pending[self.serial]=(delay,callback);return self.serial
        def after_cancel(self,key):self.pending.pop(key,None)
        def run(self,delay):
            key=next(k for k,v in self.pending.items() if v[0]==delay)
            self.pending.pop(key)[1]()
        def count(self,delay):return sum(v[0]==delay for v in self.pending.values())
    def setUp(self):
        self.app=self.Scheduler();self.updater=Mock(current='0.1.28')
        self.controller=UpdateController(self.app,Mock(),self.updater)
        self.controller.worker=Mock()
    def test_failures_retry_twice_at_thirty_seconds_then_stop(self):
        self.app.run(300)
        for _ in range(2):
            self.controller.checked(None,ValueError('offline'))
            self.assertEqual(self.app.count(30000),1)
            self.app.run(30000)
        self.controller.checked(None,ValueError('offline'))
        self.assertEqual(self.app.count(30000),0)
        self.assertEqual(self.controller.worker.call_count,3)
    def test_manual_check_before_startup_cancels_delayed_duplicate(self):
        self.controller.check(manual=True)
        self.assertEqual(self.app.count(300),0)
    def test_success_cancels_pending_retry_and_periodic_checks_continue(self):
        self.app.run(300);self.controller.checked(None,ValueError('offline'))
        self.controller.check();self.controller.checked(None,None)
        self.assertEqual(self.app.count(30000),0)
        self.assertEqual(self.app.count(10800000),1)
        self.app.run(10800000)
        self.assertTrue(self.controller.checking)
        self.assertEqual(self.app.count(10800000),1)
        calls=self.controller.worker.call_count
        self.controller.check(manual=True)
        self.assertEqual(self.controller.worker.call_count,calls)

if __name__ == "__main__":
    unittest.main()
