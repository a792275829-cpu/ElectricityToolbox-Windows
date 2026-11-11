from __future__ import annotations

import gc
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

TOOLBOX_TEST_TAGS = ("gui",)


def descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WpsToolbarLayoutTests(unittest.TestCase):
    def test_wide_columns_stay_inside_table_in_toolbox_shell(self) -> None:
        from toolbox.app import ToolboxApp
        from toolbox.runtime import ToolPaths, load_module

        workspace = Path(__file__).resolve().parents[2]
        module = load_module("wps_shell_layout_test", ToolPaths(workspace).wps_writer)
        with (
            mock.patch("toolbox.app.configure_dpi"),
            mock.patch("toolbox.pages.load_module", return_value=module),
            mock.patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}),
            mock.patch.object(module.WpsWriterFrame, "load_initial_config"),
            mock.patch.object(module.WpsWriterFrame, "_schedule_login_check"),
        ):
            app = ToolboxApp(workspace=workspace, check_updates=lambda: None)
            self.addCleanup(app.destroy)
            app.show_page("WPS写入工具")

            def settle():
                deadline = time.monotonic() + 0.6
                while time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

            deadline = time.monotonic() + 5
            while not hasattr(app.pages["WPS写入工具"], "writer_frame") and time.monotonic() < deadline:
                app.update()
                time.sleep(0.01)
            page = app.pages["WPS写入工具"]
            frame = page.writer_frame
            frame.selection_status.set("已选择：" + "每日生产经营情况汇总表" * 8)
            for width, height in (app.minsize(), (app.winfo_width(), app.winfo_height())):
                for rail_visible in (True, False):
                    with self.subTest(size=(width, height), rail_visible=rail_visible):
                        if app.right_rail_visible != rail_visible:
                            app.toggle_right_rail()
                        app.geometry(f"{width}x{height}")
                        settle()
                        view = app.page_viewport
                        self.assertEqual(view.horizontal.winfo_manager(), "", (
                            app.winfo_width(), view.canvas.winfo_width(), page.winfo_reqwidth()
                        ))
                        self.assertEqual(view.canvas.xview(), (0.0, 1.0))
                        self.assertLessEqual(page.winfo_reqwidth(), view.canvas.winfo_width())
                        for button in descendants(frame):
                            if isinstance(button, ttk.Button) and button.cget("text") in {"预览", "开始写入"}:
                                self.assertTrue(button.winfo_ismapped())
                                self.assertGreaterEqual(button.winfo_rootx(), view.canvas.winfo_rootx())
                                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), view.canvas.winfo_rootx() + view.canvas.winfo_width())

    def test_all_document_config_buttons_fit_at_520_pixels(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("520x720")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.runtime import ToolPaths, load_module
        from toolbox.ui.theme import configure_theme

        configure_theme(ttk.Style(root))

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        module = load_module("wps_responsive_toolbar_test", paths.wps_writer)
        frame = module.WpsWriterFrame(root)
        frame.pack(fill="both", expand=True)
        root.update()

        expected = {
            "添加配置", "编辑", "复制", "删除", "上移", "下移",
            "导入", "导出", "预览", "开始写入",
        }
        buttons = {
            str(widget.cget("text")): widget
            for widget in descendants(frame)
            if isinstance(widget, module.ttk.Button)
            and str(widget.cget("text")) in expected
        }
        self.assertEqual(expected, set(buttons))
        left = frame.winfo_rootx()
        right = left + frame.winfo_width()
        for text, button in buttons.items():
            with self.subTest(button=text):
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_rootx(), left)
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), right)

        checkbox = next(widget for widget in descendants(frame)
                        if isinstance(widget, ttk.Checkbutton)
                        and widget.cget("text") == "覆盖非空单元格")
        self.assertTrue(frame.overwrite.get())
        self.assertTrue(checkbox.winfo_ismapped())
        self.assertGreaterEqual(checkbox.winfo_rootx(), left)
        self.assertLessEqual(checkbox.winfo_rootx() + checkbox.winfo_width(), right)
        with mock.patch.object(module, "save_config") as save:
            checkbox.invoke()
        self.assertFalse(frame.overwrite.get())
        self.assertFalse(save.call_args.args[0]["overwrite"])

        tree_top = frame.tree.winfo_rooty()
        tree_bottom = tree_top + frame.tree.winfo_height()
        for text in ("添加配置", "编辑", "复制", "删除", "上移", "下移", "导入", "导出"):
            with self.subTest(toolbar_button=text):
                self.assertLess(buttons[text].winfo_rooty(), tree_top)
        for text in ("预览", "开始写入"):
            with self.subTest(execution_button=text):
                self.assertGreaterEqual(buttons[text].winfo_rooty(), tree_bottom)

        frame.destroy()
        root.destroy()
        gc.collect()

    def test_wps_page_shows_only_the_module_specific_log(self) -> None:
        try:
            root = tk.Tk()
            root.geometry("1000x760")
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from toolbox.pages import WpsWriterPage
        from toolbox.runtime import TaskRegistry, ToolPaths
        from toolbox.ui.theme import configure_theme

        configure_theme(ttk.Style(root))

        paths = ToolPaths(Path(__file__).resolve().parents[2])
        page = WpsWriterPage(root, paths, TaskRegistry())
        page.pack(fill="both", expand=True)
        deadline = time.monotonic() + 5
        while not hasattr(page, "writer_frame") and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertTrue(hasattr(page, "writer_frame"))
        root.update_idletasks()

        self.assertEqual(page.footer.winfo_manager(), "")
        self.assertEqual(page.actions.winfo_manager(), "")
        self.assertIsNot(page.log_text, page.writer_frame.log_text)

        from toolbox_log_console import LogConsole

        visible_consoles = [widget for widget in descendants(page)
                            if isinstance(widget, LogConsole) and widget.winfo_ismapped()]
        self.assertEqual(1, len(visible_consoles))
        visible_log_frames = [
            str(widget.cget("text"))
            for widget in descendants(page)
            if isinstance(widget, ttk.LabelFrame) and widget.winfo_ismapped()
        ]
        self.assertIs(visible_consoles[0], page.writer_frame.log_console)
        self.assertNotIn("操作日志", visible_log_frames)

        page.destroy()
        root.destroy()
        gc.collect()


if __name__ == "__main__":
    unittest.main()
