"""Source-mode restart button and detached relaunch regressions."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "电力工具箱"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from toolbox_platform import relaunch_source_app, source_restart_available

HOST_VARIABLES = ("TOOLBOX_HOST_PID", "TOOLBOX_APP_BUNDLE", "TOOLBOX_INSTALLED")
LAUNCHER = ROOT / "电力工具箱" / "toolbox_launcher.pyw"

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - headless verification machines
    tk = None


class SourceRestartAvailabilityTests(unittest.TestCase):
    def test_plain_source_run_can_restart(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(source_restart_available())

    def test_each_desktop_host_marker_disables_restart(self) -> None:
        for name in HOST_VARIABLES:
            with self.subTest(marker=name), mock.patch.dict(os.environ, {name: "12345"}, clear=True):
                self.assertFalse(source_restart_available())


class RelaunchSourceAppTests(unittest.TestCase):
    def test_detached_relaunch_uses_current_interpreter_and_environment(self) -> None:
        with mock.patch.dict(os.environ, {"TOOLBOX_SOURCE_DEBUG": "1", "TOOLBOX_DATA_ROOT": "test-data"}, clear=True), \
                mock.patch("toolbox_platform.subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            self.assertEqual(relaunch_source_app(LAUNCHER, cwd=ROOT), 4242)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1], str(LAUNCHER))
        self.assertEqual(kwargs["cwd"], str(ROOT))
        self.assertEqual(kwargs["env"]["TOOLBOX_SOURCE_DEBUG"], "1")
        self.assertEqual(kwargs["env"]["TOOLBOX_DATA_ROOT"], "test-data")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        if os.name == "nt":
            self.assertTrue(args[0][0].endswith("pythonw.exe"))
            self.assertEqual(kwargs["creationflags"],
                             subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        else:
            self.assertTrue(kwargs["start_new_session"])

    @unittest.skipUnless(os.name == "nt", "Windows job breakaway only")
    def test_windows_falls_back_when_breakaway_is_forbidden(self) -> None:
        with mock.patch("toolbox_platform.subprocess.Popen") as popen:
            popen.side_effect = [OSError("breakaway denied"), mock.Mock(pid=99)]
            self.assertEqual(relaunch_source_app(LAUNCHER, cwd=ROOT), 99)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    @unittest.skipIf(os.name == "nt", "non-Windows has no creationflags fallback")
    def test_non_windows_propagates_launch_failure(self) -> None:
        with mock.patch("toolbox_platform.subprocess.Popen", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                relaunch_source_app(LAUNCHER, cwd=ROOT)


@unittest.skipIf(tk is None, "tkinter unavailable")
class RestartButtonTests(unittest.TestCase):
    def _build_app(self):
        from toolbox.app import PAGE_NAMES, ToolboxApp
        factories = {name: (lambda parent, _paths, _registry: tk.Frame(parent)) for name in PAGE_NAMES}
        return ToolboxApp(workspace=ROOT, page_factories=factories, lazy_pages=True)

    def setUp(self) -> None:
        with mock.patch.dict(os.environ, {name: "" for name in HOST_VARIABLES}, clear=False):
            self.app = self._build_app()
        self.app.update()

    def tearDown(self) -> None:
        self.app.destroy()

    def test_restart_button_sits_left_of_the_right_aligned_version(self) -> None:
        self.assertEqual(self.app.restart_button.cget("text"), "重启")
        self.assertEqual(self.app.restart_button.winfo_manager(), "grid")
        self.assertEqual(int(self.app.restart_button.grid_info()["column"]), 3)
        self.assertEqual(int(self.app.version_label.grid_info()["column"]), 4)
        self.assertLess(self.app.restart_button.winfo_rootx(), self.app.version_label.winfo_rootx())

    def test_desktop_host_hides_the_restart_button(self) -> None:
        with mock.patch.dict(os.environ, {"TOOLBOX_HOST_PID": "12345"}, clear=False):
            desktop_app = self._build_app()
        try:
            desktop_app.update()
            self.assertNotEqual(desktop_app.restart_button.winfo_manager(), "grid")
            self.assertEqual(int(desktop_app.version_label.grid_info()["column"]), 4)
        finally:
            desktop_app.destroy()

    def test_restart_starts_new_process_then_closes_this_window(self) -> None:
        with mock.patch.object(self.app, "_confirm_shutdown", return_value=True), \
                mock.patch("toolbox.app.relaunch_source_app") as relaunch, \
                mock.patch.object(self.app, "destroy") as destroy:
            self.app.restart_application()
        relaunch.assert_called_once()
        self.assertEqual(relaunch.call_args.args[0], LAUNCHER)
        self.assertEqual(relaunch.call_args.kwargs["cwd"], ROOT)
        destroy.assert_called_once()

    def test_cancelled_confirmation_does_not_restart(self) -> None:
        with mock.patch.object(self.app, "_confirm_shutdown", return_value=False), \
                mock.patch("toolbox.app.relaunch_source_app") as relaunch, \
                mock.patch.object(self.app, "destroy") as destroy:
            self.app.restart_application()
        relaunch.assert_not_called()
        destroy.assert_not_called()

    def test_failed_launch_keeps_the_current_window_open(self) -> None:
        with mock.patch.object(self.app, "_confirm_shutdown", return_value=True), \
                mock.patch("toolbox.app.relaunch_source_app", side_effect=OSError("denied")), \
                mock.patch("toolbox.app.messagebox.showerror") as showerror, \
                mock.patch.object(self.app, "destroy") as destroy:
            self.app.restart_application()
        self.assertIn("重启失败", showerror.call_args.args[0])
        destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
