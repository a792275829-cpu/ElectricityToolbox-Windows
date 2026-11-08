"""CLI 登录失效诊断链路与关闭草稿保护（移植自 Mac v0.1.32，导入名改为扁平）。"""
from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]

from toolbox_qingneng_login import LoginError
from toolbox_credentials import CredentialStore
from toolbox.tasks import CancellationToken, ProcessRunner, ProcessExecutionError


class CliPropagationTests(unittest.TestCase):
    def test_source_debug_interpreter_with_chinese_name_is_wrapped(self):
        from toolbox_cli_login import script_command
        from toolbox.tasks.errors import error_detail
        with mock.patch("toolbox_cli_login.sys.executable", "/tmp/电力工具箱源码调试"):
            command = script_command(["/tmp/电力工具箱源码调试", "/tmp/example.py", "--login"])
        self.assertEqual(Path(command[1]).name, "toolbox_cli_login.py")
        self.assertEqual(command[-2:], ["/tmp/example.py", "--login"])
        self.assertEqual(error_detail(LoginError("verification", "fixture captcha"))["login_kind"], "verification")

    def fixture(self, root, caught):
        script = root / "上网电量抓取/export_online_energy.py"
        script.parent.mkdir()
        text = 'from toolbox_qingneng_login import LoginError\n'
        if caught:
            text += ('from toolbox_cli_login import emit_login_error\n'
                     'try:\n raise LoginError("verification", "fixture captcha")\n'
                     'except LoginError as exc:\n emit_login_error(exc)\n raise SystemExit(1)\n')
        else:
            text += 'raise LoginError("verification", "fixture captcha")\n'
        script.write_text(text)
        return script

    def test_source_process_receives_login_type_from_caught_and_uncaught_cli(self):
        import sys
        for caught in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                script = self.fixture(root, caught)
                with self.assertRaises(ProcessExecutionError) as error:
                    ProcessRunner().run([sys.executable, str(script)], cwd=root, env=None,
                                        token=CancellationToken(), on_output=lambda _: None)
                self.assertEqual(error.exception.error_detail["login_kind"], "verification")

    def test_desktop_service_keeps_inner_cli_type_through_task_worker(self):
        from toolbox.service import ToolboxService
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root, True)
            service = ToolboxService(root)
            try:
                task = service.call("start", dict(request_id="login-fixture", tool_id="online-energy",
                                                  action="run", values={"start_date": "2026-10-28", "end_date": "2026-10-28"}))
                deadline = time.monotonic() + 15
                while service.registry.has_running_tasks() and time.monotonic() < deadline:
                    time.sleep(0.02)
                error = service.registry.snapshot(task).error
                self.assertIsNotNone(error)
                self.assertEqual(error.error_detail["login_kind"], "verification")
                self.assertEqual(error.error_detail["tool_id"], "online-energy")
                self.assertEqual(error.error_detail["stage"], "login")
            finally:
                service.registry.terminate_all()


class DraftLifecycleTests(unittest.TestCase):
    def test_incomplete_input_is_not_saved_on_close_and_offline_task_is_not_blocked(self):
        from toolbox.app import ToolboxApp, PAGE_NAMES
        from toolbox.widgets import ToolPage
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(Path(folder))
            before = store.save("original", "secret")
            app = ToolboxApp(workspace=Path(folder), page_factories={name: lambda parent, *_: tk.Frame(parent) for name in PAGE_NAMES})
            app.withdraw()
            panel = app.credentials_panel
            panel.username_var.set("rejected")
            panel.password_var.set("")
            page = ToolPage(app, registry=mock.Mock())
            page.tool_id = "trade-analysis"
            page.run_in_thread(lambda: None, status="local")
            page.registry.start_callable.assert_called_once()
            self.assertTrue(panel.dirty)
            app._on_close()
            self.assertEqual(store.load(), before)

    def test_close_is_blocked_when_complete_input_cannot_be_saved(self):
        from toolbox.app import ToolboxApp, PAGE_NAMES
        with tempfile.TemporaryDirectory() as folder:
            app = ToolboxApp(workspace=Path(folder), page_factories={name: lambda parent, *_: tk.Frame(parent) for name in PAGE_NAMES})
            app.withdraw()
            try:
                panel = app.credentials_panel
                panel.username_var.set("fixture")
                panel.password_var.set("fixture-secret")
                with mock.patch.object(panel.store, "save", side_effect=OSError), mock.patch("toolbox.app.messagebox.showwarning") as warning:
                    self.assertFalse(app._on_close())
                warning.assert_called_once()
                self.assertTrue(app.winfo_exists())
                self.assertTrue(panel.dirty)
                self.assertEqual(CredentialStore(Path(folder)).load(), {})
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
