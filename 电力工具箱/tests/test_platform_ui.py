from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest
import gc
from pathlib import Path


class CatalogTests(unittest.TestCase):
    def test_catalog_has_unique_tools_in_business_groups(self) -> None:
        from toolbox.catalog import default_catalog

        catalog = default_catalog()
        self.assertEqual(16, len(catalog))
        self.assertEqual(16, len({item.tool_id for item in catalog}))
        self.assertEqual(
            {"数据采集", "分析与报告", "上传与写入"},
            {item.category for item in catalog},
        )
        self.assertEqual("数据采集", next(item.category for item in catalog if item.tool_id == "summary-upload"))
        categories = [item.category for item in catalog]
        self.assertEqual(3, sum(index == 0 or value != categories[index - 1] for index, value in enumerate(categories)))

    def test_adapter_registry_matches_catalog_and_marks_side_effects(self) -> None:
        from toolbox.adapters import default_adapters
        from toolbox.catalog import default_catalog

        adapters = default_adapters()
        self.assertEqual(
            {item.tool_id for item in default_catalog()},
            set(adapters),
        )
        self.assertFalse(adapters["trade-analysis"].destructive)
        self.assertFalse(adapters["daily-pdf-parser"].destructive)
        self.assertTrue(adapters["wind-forecast"].retryable_read)
        self.assertTrue(adapters["summary-upload"].destructive)
        self.assertTrue(adapters["group-upload"].destructive)
        self.assertTrue(adapters["spot-price"].destructive)
        self.assertTrue(adapters["spot-price"].retryable_read)
        self.assertTrue(adapters["transaction-info"].destructive)
        self.assertTrue(adapters["transaction-info"].retryable_read)
        self.assertTrue(adapters["guangdong-price"].retryable_read)


class DiagnosticTests(unittest.TestCase):
    def test_diagnostics_report_missing_tool_files_without_raising(self) -> None:
        import tempfile
        from toolbox.diagnostics import diagnose_environment
        from toolbox.runtime import ToolPaths

        with tempfile.TemporaryDirectory() as directory:
            report = diagnose_environment(ToolPaths(Path(directory)))
        self.assertFalse(report.ok)
        self.assertTrue(any("找不到" in item.detail for item in report.checks))

    def test_diagnostics_distinguish_optional_warning_from_blocking_error(self) -> None:
        from toolbox.diagnostics import DiagnosticCheck, DiagnosticLevel, DiagnosticReport

        report = DiagnosticReport(
            (
                DiagnosticCheck("正常", True, "可用"),
                DiagnosticCheck("可选组件", True, "未安装", level=DiagnosticLevel.WARNING),
                DiagnosticCheck("必要文件", False, "缺失"),
            )
        )

        self.assertEqual(1, len(report.passed))
        self.assertEqual(1, len(report.warnings))
        self.assertEqual(1, len(report.errors))
        self.assertFalse(report.ok)

    def test_path_diagnostics_only_inspect_and_do_not_modify_file(self) -> None:
        import tempfile
        from toolbox.diagnostics import diagnose_path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xlsx"
            path.write_bytes(b"unchanged")
            before = path.stat()
            report = diagnose_path(path)
            after = path.stat()

        self.assertTrue(report.ok)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_diagnostics_flag_missing_rust_backend_and_browser(self) -> None:
        # 缺 Rust 后端或浏览器时，所有工具文件和 Node 都“正常”，旧诊断仍报全绿，
        # 用户却在 WPS 文档配置和市场日报补录里直接遇到异常。
        import os
        import tempfile
        from unittest import mock
        from toolbox.diagnostics import DiagnosticLevel, diagnose_environment
        from toolbox.runtime import ToolPaths

        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("toolbox_native_excel.native_binary", return_value=None), \
                mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(Path(directory) / "browsers")}), \
                mock.patch("toolbox.diagnostics._system_browser_executable", return_value=None):
            report = diagnose_environment(ToolPaths(workspace))
        checks = {item.name: item for item in report.checks}
        rust = checks["Rust 业务后端"]
        browser = checks["浏览器自动化"]
        self.assertEqual(DiagnosticLevel.WARNING, rust.status)
        # 产品线只维护 Windows，提示里不应再出现 macOS 初始化脚本。
        self.assertIn("setup_windows.ps1", rust.recommendation)
        self.assertNotIn("setup_macos.command", rust.recommendation)
        self.assertNotIn("macOS", rust.recommendation)
        self.assertEqual(DiagnosticLevel.WARNING, browser.status)
        self.assertIn("Chrome", browser.detail)
        self.assertIn("setup_windows.ps1", browser.recommendation)

    def test_diagnostics_accept_bundled_chromium_runtime_layout(self) -> None:
        import os
        import tempfile
        from unittest import mock
        from toolbox.diagnostics import DiagnosticLevel, diagnose_environment
        from toolbox.runtime import ToolPaths

        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "browsers"
            executable = cache / "chromium-1187" / "chrome-win" / "chrome.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"stub")
            with mock.patch("toolbox_native_excel.native_binary", return_value=executable), \
                    mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(cache)}), \
                    mock.patch("toolbox.diagnostics._system_browser_executable", return_value=None):
                report = diagnose_environment(ToolPaths(workspace))
        checks = {item.name: item for item in report.checks}
        self.assertEqual(DiagnosticLevel.OK, checks["浏览器自动化"].status)
        self.assertIn("chromium-1187", checks["浏览器自动化"].detail)

    def test_diagnostics_treat_system_browser_as_unusable_in_portable_mode(self) -> None:
        # 安装包外壳设置 TOOLBOX_PORTABLE=1 后，抓取脚本一律 channel=None 走内置
        # Chromium；此时"机器上有 Chrome"帮不上忙，诊断不能据此报绿。
        import os
        import tempfile
        from unittest import mock
        from toolbox import diagnostics
        from toolbox.diagnostics import DiagnosticLevel, diagnose_environment
        from toolbox.runtime import ToolPaths

        with mock.patch.dict(os.environ, {"TOOLBOX_PORTABLE": "1"}), \
                mock.patch.object(diagnostics.Path, "is_file", return_value=True):
            self.assertIsNone(diagnostics._system_browser_executable())
        with mock.patch.dict(os.environ, {"TOOLBOX_PORTABLE": "0"}), \
                mock.patch.object(diagnostics.Path, "is_file", return_value=True):
            self.assertIsNotNone(diagnostics._system_browser_executable())

        workspace = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"TOOLBOX_PORTABLE": "1",
                                             "PLAYWRIGHT_BROWSERS_PATH": str(Path(directory) / "browsers")}):
            report = diagnose_environment(ToolPaths(workspace))
        browser = {item.name: item for item in report.checks}["浏览器自动化"]
        self.assertEqual(DiagnosticLevel.WARNING, browser.status)
        self.assertIn("内置 Chromium", browser.detail)
        # 便携版不给"去装 Chrome"这种无效建议。
        self.assertNotIn("安装 Chrome", browser.recommendation)

    def test_manual_market_missing_backend_hint_names_windows_script(self) -> None:
        from datetime import date
        from unittest import mock
        from toolbox.runtime import load_module

        workspace = Path(__file__).resolve().parents[2]
        module = load_module(
            "market_manual_platform_hint_test",
            workspace / "南方分公司电力市场日报/gd_power_report/manual_market.py",
        )
        with mock.patch.object(module, "native_binary", return_value=None), \
                self.assertRaises(RuntimeError) as error:
            module.preview_manual_market({}, date(2026, 11, 6))
        message = str(error.exception)
        self.assertIn("setup_windows.ps1", message)
        self.assertNotIn("setup_macos.command", message)


class WorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            gc.collect()

    def test_lazy_app_starts_with_first_tool_and_creates_page_once(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        self.root = None
        created: list[str] = []

        def factory(name: str):
            def build(parent, _paths, _registry):
                created.append(name)
                return tk.Frame(parent)
            return build

        app = ToolboxApp(
            workspace=Path.cwd(),
            page_factories={name: factory(name) for name in PAGE_NAMES},
            lazy_pages=True,
        )
        app.withdraw()
        self.root = app

        self.assertEqual("导出上网电量", app.current_page)
        self.assertEqual(["导出上网电量"], created)
        app.show_page("出清结果汇总上传")
        app.show_page("出清结果汇总上传")
        self.assertEqual(["导出上网电量", "出清结果汇总上传"], created)
        self.assertTrue(hasattr(app, "task_center"))
        self.assertIsNotNone(app.diagnostic_center)
        self.assertEqual("grid", app.diagnostic_center.winfo_manager())
        self.assertNotIn("运行诊断中心", app.nav_buttons)
        self.assertIs(app.task_center.master, app.diagnostic_center.master)
        self.assertLess(
            int(app.task_center.grid_info()["row"]),
            int(app.diagnostic_center.grid_info()["row"]),
        )

    def test_default_window_contains_every_navigation_module(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        self.root = None
        factories = {
            name: (lambda parent, _paths, _registry: tk.Frame(parent))
            for name in PAGE_NAMES
        }
        app = ToolboxApp(
            workspace=Path.cwd(),
            page_factories=factories,
            lazy_pages=True,
        )
        self.root = app
        app.update()  # Process the native Windows WM_SIZE before inspecting geometry.
        from tests.screen_layout import skip_if_window_does_not_fit
        skip_if_window_does_not_fit(self, app)


        last_button = app.nav_buttons[PAGE_NAMES[-1]]
        navigation_bottom = last_button.winfo_rooty() + last_button.winfo_height()
        status_top = app.status_bar.winfo_rooty()

        self.assertLessEqual(navigation_bottom, status_top)
        self.assertLessEqual(app.winfo_height(), app.winfo_screenheight())

    def test_sidebar_contains_only_business_navigation(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp
        from toolbox.catalog import default_catalog
        self.root.destroy()
        app = ToolboxApp(page_factories={name: (lambda parent, _paths, _registry: tk.Frame(parent)) for name in PAGE_NAMES}, lazy_pages=True)
        self.root = app
        self.assertFalse(hasattr(app, "search_entry"))
        self.assertNotIn("今日概览", app.nav_buttons)
        visible_names = [widget.cget("text") for widget in app.navigation.pack_slaves() if isinstance(widget, ttk.Button)]
        self.assertEqual([item.name for item in default_catalog()], visible_names)

    def test_auxiliary_panel_toggle_preserves_panels_and_expands_workspace(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        app = ToolboxApp(page_factories={name: (lambda parent, _paths, _registry: tk.Frame(parent)) for name in PAGE_NAMES}, lazy_pages=True)
        self.root = app
        app.update_idletasks()
        previous_width = app.page_host.winfo_width()
        original_task_center = app.task_center
        app.toggle_right_rail()
        app.update_idletasks()
        self.assertEqual("", app.right_rail.winfo_manager())
        self.assertGreater(app.page_host.winfo_width(), previous_width)
        app.toggle_right_rail()
        app.update_idletasks()
        self.assertEqual("grid", app.right_rail.winfo_manager())
        self.assertIs(original_task_center, app.task_center)

    def test_navigation_scroll_keeps_last_module_accessible_in_small_window(self) -> None:
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        app = ToolboxApp(page_factories={name: (lambda parent, _paths, _registry: tk.Frame(parent)) for name in PAGE_NAMES}, lazy_pages=True)
        self.root = app
        app.minsize(720, 400)
        app.geometry("980x440")
        app.update()
        self.assertEqual("pack", app.nav_scrollbar.winfo_manager())
        app.nav_canvas.yview_moveto(1)
        app.update_idletasks()
        last = app.nav_buttons["WPS写入工具"]
        self.assertGreaterEqual(last.winfo_rooty(), app.nav_canvas.winfo_rooty())
        self.assertLessEqual(last.winfo_rooty() + last.winfo_height(), app.status_bar.master.winfo_rooty())


class TaskCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            gc.collect()

    def test_refresh_preserves_selected_task_identity_when_history_rolls(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.tasks import TaskSnapshot, TaskState
        from toolbox.ui import TaskCenter

        registry = TaskRegistry()
        task_ids = [f"task-{index}" for index in range(13)]
        registry._snapshots = {
            task_id: TaskSnapshot(
                task_id=task_id,
                name=task_id,
                state=TaskState.SUCCEEDED,
            )
            for task_id in task_ids
        }
        center = TaskCenter(self.root, registry)
        center._refresh()
        selected_id = task_ids[2]
        center.listbox.selection_set(center._task_ids.index(selected_id))

        registry._snapshots["task-13"] = TaskSnapshot(
            task_id="task-13",
            name="task-13",
            state=TaskState.SUCCEEDED,
        )
        center._refresh()

        selected = center.listbox.curselection()
        self.assertTrue(selected)
        self.assertEqual(selected_id, center._task_ids[selected[0]])
        center.destroy()

    def test_cancel_button_is_disabled_for_finished_task(self) -> None:
        from toolbox.runtime import TaskRegistry
        from toolbox.tasks import TaskSnapshot, TaskState
        from toolbox.ui import TaskCenter

        registry = TaskRegistry()
        task_id = "finished"
        registry._snapshots[task_id] = TaskSnapshot(
            task_id=task_id,
            name=task_id,
            state=TaskState.SUCCEEDED,
        )
        center = TaskCenter(self.root, registry)
        center._refresh()
        center.listbox.selection_set(center._task_ids.index(task_id))
        center._sync_cancel_state()

        self.assertEqual("disabled", str(center.cancel_button.cget("state")))
        center.destroy()


class ModuleCacheTests(unittest.TestCase):
    def test_load_module_isolates_same_named_sibling_imports(self) -> None:
        import sys
        import tempfile
        from toolbox.runtime import clear_module_cache, load_module

        original_path = list(sys.path)
        previous_helper = sys.modules.get("helper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "helper.py").write_text("VALUE = 'first'\n", encoding="utf-8")
            (second_dir / "helper.py").write_text("VALUE = 'second'\n", encoding="utf-8")
            (first_dir / "main.py").write_text(
                "import helper\nVALUE = helper.VALUE\n", encoding="utf-8"
            )
            (second_dir / "main.py").write_text(
                "import helper\nVALUE = helper.VALUE\n", encoding="utf-8"
            )

            clear_module_cache()
            first = load_module("isolated_first", first_dir / "main.py")
            second = load_module("isolated_second", second_dir / "main.py")

        self.assertEqual("first", first.VALUE)
        self.assertEqual("second", second.VALUE)
        self.assertEqual(original_path, sys.path)
        self.assertIs(previous_helper, sys.modules.get("helper"))

    def test_load_module_reuses_unchanged_file_and_invalidates_on_change(self) -> None:
        import tempfile
        import time
        from toolbox.runtime import clear_module_cache, load_module

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("VALUE = 1\n", encoding="utf-8")
            clear_module_cache()
            first = load_module("cached_sample", path)
            second = load_module("cached_sample", path)
            self.assertIs(first, second)
            time.sleep(0.01)
            path.write_text("VALUE = 2\n", encoding="utf-8")
            third = load_module("cached_sample", path)
            self.assertIsNot(first, third)
            self.assertEqual(2, third.VALUE)


class NavigationTimingTests(unittest.TestCase):
    """右栏诊断区要能回答「切模块到底慢在哪」，且不能把右栏顶得上下跳。"""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if getattr(self, "root", None) is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            gc.collect()

    def build_app(self):
        from toolbox.app import PAGE_NAMES, ToolboxApp

        self.root.destroy()
        self.root = None

        def factory(_name):
            return lambda parent, _paths, _registry: tk.Frame(parent)

        app = ToolboxApp(
            workspace=Path.cwd(),
            page_factories={name: factory(name) for name in PAGE_NAMES},
            lazy_pages=True,
        )
        app.withdraw()
        self.root = app
        return app

    def test_navigation_summary_counts_cold_and_warm_opens(self) -> None:
        app = self.build_app()
        # 走 request_page 才是真实导航路径（左侧模块点击）；直接 show_page 不算一次切换。
        app.request_page("出清结果汇总上传")
        app.after(120, app.quit)
        app.mainloop()
        app.request_page("今日概览")
        app.after(120, app.quit)
        app.mainloop()
        self.assertGreaterEqual(len(app.navigation_timings), 2)
        self.assertTrue(all(float(item["ms"]) >= 0 for item in app.navigation_timings))
        summary = app.navigation_summary()
        self.assertIn("模块切换：最近", summary)
        self.assertIn("中位", summary)
        self.assertIn("最慢", summary)
        self.assertIn("首次", summary)

    def test_empty_summary_is_stable_text(self) -> None:
        app = self.build_app()
        self.assertEqual("模块切换：暂无记录", app.navigation_summary())

    def test_diagnostic_panel_height_does_not_move_when_timing_changes(self) -> None:
        app = self.build_app()
        app.update_idletasks()
        holder = app.diagnostic_center.nav_holder
        initial = int(holder.cget("height"))
        app.navigation_timings.extend(
            {"module": "南方分公司电力市场日报", "cold": True, "ms": 1234.5, "window": "1920x1203"}
            for _ in range(8)
        )
        app.diagnostic_center.refresh_navigation()
        for _ in range(4):
            app.update()
        self.assertEqual(initial, int(holder.cget("height")))
        self.assertEqual(initial, holder.winfo_height())


if __name__ == "__main__":
    unittest.main()
