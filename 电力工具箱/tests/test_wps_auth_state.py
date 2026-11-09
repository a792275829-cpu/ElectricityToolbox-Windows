"""WPS 登录态必须跨专用浏览器重启保留。

专用浏览器在「登录窗口」与「后台 headless」之间切换时会重启进程。重启前的
强制结束不会触发 Chrome 的退出流程，刚扫码得到的会话 Cookie 还没有写入
profile 数据库，下一次检查就会显示未登录，用户被迫反复扫码。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import load_module

ROOT = Path(__file__).resolve().parents[2]


def load_writer():
    return load_module(
        "wps_auth_state_test",
        ROOT / "wps自动" / "wps_excel_to_kdocs_gui.py",
    )


def cookie(name, domain=".kdocs.cn", value="v", expires=4102444800.0, **extra):
    data = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": expires,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }
    data.update(extra)
    return data


class WpsAuthStateTests(unittest.TestCase):
    def setUp(self):
        self.module = load_writer()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state_path = Path(self.folder.name) / "wps-auth-state.json"
        patcher = mock.patch.object(self.module, "WPS_AUTH_STATE_PATH", self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.context = mock.Mock()
        self.context.pages = []

    def saved_cookies(self):
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], self.module.WPS_AUTH_STATE_SCHEMA)
        return payload["cookies"]

    def test_snapshot_keeps_only_wps_login_cookies(self):
        self.context.cookies.return_value = [
            cookie("wps_sid", value="sid-value"),
            cookie("csrf", domain=".wps.cn", expires=-1),
            cookie("sessionid", domain=".example.com"),
        ]
        self.assertTrue(self.module.save_wps_auth_state(self.context))
        names = sorted(item["name"] for item in self.saved_cookies())
        self.assertEqual(names, ["csrf", "wps_sid"])

    def test_snapshot_is_not_rewritten_when_nothing_changed(self):
        self.context.cookies.return_value = [cookie("wps_sid")]
        self.assertTrue(self.module.save_wps_auth_state(self.context))
        self.assertFalse(self.module.save_wps_auth_state(self.context))

    def test_snapshot_ignores_contexts_without_wps_cookies(self):
        self.context.cookies.return_value = [cookie("sessionid", domain=".example.com")]
        self.assertFalse(self.module.save_wps_auth_state(self.context))
        self.assertFalse(self.state_path.exists())

    def test_snapshot_failure_is_reported_without_raising(self):
        self.context.cookies.return_value = [cookie("wps_sid")]
        messages = []
        with mock.patch.object(self.module.os, "replace", side_effect=OSError("拒绝访问")):
            self.assertFalse(self.module.save_wps_auth_state(self.context, messages.append))
        self.assertTrue(any("未能保存" in message for message in messages))

    def test_restore_adds_missing_cookies_and_skips_expired_ones(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "schema": self.module.WPS_AUTH_STATE_SCHEMA,
                    "version": 1,
                    "cookies": [
                        cookie("wps_sid", value="fresh"),
                        cookie("exp", value="old", expires=1.0),
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.context.cookies.return_value = []
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 1)
        added = self.context.add_cookies.call_args.args[0]
        self.assertEqual([item["name"] for item in added], ["wps_sid"])
        self.assertEqual(added[0]["value"], "fresh")

    def test_restore_keeps_the_cookie_the_browser_already_holds(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "schema": self.module.WPS_AUTH_STATE_SCHEMA,
                    "version": 1,
                    "cookies": [cookie("wps_sid", value="older")],
                }
            ),
            encoding="utf-8",
        )
        self.context.cookies.return_value = [cookie("wps_sid", value="newer")]
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 0)
        self.context.add_cookies.assert_not_called()

    def test_restore_ignores_missing_or_broken_state_files(self):
        self.context.cookies.return_value = []
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 0)
        self.state_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 0)
        self.state_path.write_text(json.dumps({"schema": "other", "cookies": [cookie("a")]}), encoding="utf-8")
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 0)
        self.context.add_cookies.assert_not_called()

    def test_restore_falls_back_to_single_cookie_writes(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "schema": self.module.WPS_AUTH_STATE_SCHEMA,
                    "version": 1,
                    "cookies": [cookie("wps_sid"), cookie("uid")],
                }
            ),
            encoding="utf-8",
        )
        self.context.cookies.return_value = []
        self.context.add_cookies.side_effect = [RuntimeError("批量写入失败"), None, RuntimeError("拒绝")]
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 1)

    def test_restored_cookies_reload_open_kdocs_pages(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "schema": self.module.WPS_AUTH_STATE_SCHEMA,
                    "version": 1,
                    "cookies": [cookie("wps_sid")],
                }
            ),
            encoding="utf-8",
        )
        self.context.cookies.return_value = []
        kdocs_page = mock.Mock(url="https://www.kdocs.cn/l/abc")
        other_page = mock.Mock(url="chrome://newtab")
        self.context.pages = [kdocs_page, other_page]
        self.assertEqual(self.module.restore_wps_auth_state(self.context), 1)
        kdocs_page.reload.assert_called_once()
        other_page.reload.assert_not_called()

    def test_confirmed_login_is_saved_for_the_next_browser_start(self):
        self.context.cookies.return_value = [cookie("wps_sid", value="fresh")]
        self.context.request.get.return_value = mock.Mock(status=200)
        self.context.request.get.return_value.json.return_value = {"result": "ok", "userid": 1}
        with (
            mock.patch.object(self.module, "PROFILE_DIR", Path(self.folder.name)),
            mock.patch.object(self.module, "sync_playwright", return_value=mock.MagicMock()),
            mock.patch.object(self.module, "open_browser_context", return_value=(None, self.context, False)),
        ):
            status = self.module.check_wps_login_status("managed_cdp", "local", mock.Mock())
        self.assertEqual(status.state, "logged_in")
        self.assertEqual([item["name"] for item in self.saved_cookies()], ["wps_sid"])

    def test_logged_out_probe_does_not_save_a_snapshot(self):
        self.context.cookies.return_value = [cookie("wps_sid")]
        self.context.request.get.return_value = mock.Mock(status=403)
        self.context.request.get.return_value.json.return_value = {"result": "userNotLogin"}
        with (
            mock.patch.object(self.module, "PROFILE_DIR", Path(self.folder.name)),
            mock.patch.object(self.module, "sync_playwright", return_value=mock.MagicMock()),
            mock.patch.object(self.module, "open_browser_context", return_value=(None, self.context, False)),
        ):
            status = self.module.check_wps_login_status("managed_cdp", "local", mock.Mock())
        self.assertEqual(status.state, "logged_out")
        self.assertFalse(self.state_path.exists())

    def test_open_browser_context_restores_saved_login(self):
        playwright = mock.Mock()
        browser = playwright.chromium.connect_over_cdp.return_value
        browser.contexts = [self.context]
        with (
            mock.patch.object(self.module, "try_resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "ensure_wps_dedicated_chrome"),
            mock.patch.object(self.module, "restore_wps_auth_state") as restore,
        ):
            self.module.open_browser_context(playwright, "managed_cdp", "local", mock.Mock())
        restore.assert_called_once_with(self.context)

    def test_open_browser_context_leaves_a_user_browser_alone(self):
        playwright = mock.Mock()
        browser = playwright.chromium.connect_over_cdp.return_value
        browser.contexts = [self.context]
        with (
            mock.patch.object(self.module, "resolve_cdp_endpoint", return_value="endpoint"),
            mock.patch.object(self.module, "restore_wps_auth_state") as restore,
        ):
            self.module.open_browser_context(playwright, "cdp", "local", mock.Mock())
        restore.assert_not_called()


class WpsGracefulStopTests(unittest.TestCase):
    """重启前先让可见窗口自行退出，Chrome 才有机会把 Cookie 落盘。"""

    def setUp(self):
        self.module = load_writer()
        self.alive = {"value": True}
        self.clock = {"value": 0.0}
        self.commands = []

        def run(command, **_kwargs):
            self.commands.append(command)
            if "/F" in command:
                self.alive["value"] = False
            return mock.Mock(returncode=0)

        def monotonic():
            # 快进假时钟，避免等待循环真的耗费时间。
            self.clock["value"] += 1.0
            return self.clock["value"]

        self.patches = [
            mock.patch.object(self.module.sys, "platform", "win32"),
            mock.patch.object(self.module, "process_is_alive", side_effect=lambda _pid: self.alive["value"]),
            mock.patch.object(self.module, "is_managed_chrome_command", return_value=True),
            mock.patch.object(self.module, "managed_chrome_command", return_value="chrome.exe --user-data-dir=x"),
            mock.patch.object(self.module, "clear_managed_chrome_state"),
            mock.patch.object(self.module.subprocess, "run", side_effect=run),
            mock.patch.object(self.module.time_module, "monotonic", side_effect=monotonic),
            mock.patch.object(self.module.time_module, "sleep"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_visible_login_window_is_closed_before_force_kill(self):
        def close_by_itself(command, **_kwargs):
            self.commands.append(command)
            if "/F" not in command:
                self.alive["value"] = False
            return mock.Mock(returncode=0)

        with (
            mock.patch.object(self.module, "managed_chrome_is_headless", return_value=False),
            mock.patch.object(self.module.subprocess, "run", side_effect=close_by_itself),
        ):
            self.module.stop_managed_chrome(4321, lambda _message: None, "正在切换为后台运行")
        self.assertEqual(self.commands, [["taskkill.exe", "/PID", "4321", "/T"]])

    def test_unresponsive_window_falls_back_to_force_kill(self):
        with mock.patch.object(self.module, "managed_chrome_is_headless", return_value=False):
            self.module.stop_managed_chrome(4321, lambda _message: None, "正在切换为后台运行")
        self.assertEqual(self.commands[0], ["taskkill.exe", "/PID", "4321", "/T"])
        self.assertEqual(self.commands[-1], ["taskkill.exe", "/PID", "4321", "/T", "/F"])

    def test_headless_instance_is_killed_without_waiting(self):
        with mock.patch.object(self.module, "managed_chrome_is_headless", return_value=True):
            self.module.stop_managed_chrome(4321, lambda _message: None, "正在应用更新后的浏览器设置")
        self.assertEqual(self.commands, [["taskkill.exe", "/PID", "4321", "/T", "/F"]])


if __name__ == "__main__":
    unittest.main()
