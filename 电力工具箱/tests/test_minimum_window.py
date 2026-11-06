"""Guard the fixed minimum window and full-size page viewport."""
from __future__ import annotations

import gc
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from toolbox.app import (  # noqa: E402
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PAGE_NAMES,
    ToolboxApp,
)
from toolbox_platform import windows_work_area  # noqa: E402
from tests.screen_layout import skip_if_window_does_not_fit  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class MinimumWindowTests(unittest.TestCase):
    def setUp(self):
        credentials = patch(
            "toolbox.ui.credentials_panel.CredentialStore.load", return_value={}
        )
        credentials.start()
        self.addCleanup(credentials.stop)
        self.app = ToolboxApp(workspace=ROOT, check_updates=lambda: None)
        self.addCleanup(self.close_app)
        self._settle(0.3)

    def close_app(self):
        self.app.destroy()
        gc.collect()

    def _settle(self, seconds=0.25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

    def _settle_page(self, name: str, *, timeout: float = 5.0) -> None:
        """Wait until the page's actual and requested size both stop changing.

        WPS 页面的表体由工作线程延迟装配，装配途中会短暂出现 1191 甚至 1680 的
        瞬时请求宽度（稳定后回落到 928），固定 sleep 量到中途状态就会误判成压缩。
        """
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        previous = None
        stable = 0
        while time.monotonic() < deadline:
            self.app.update()
            page = self.app.pages.get(name)
            if page is None:
                time.sleep(0.01)
                continue
            current = (page.winfo_width(), page.winfo_reqwidth(),
                       page.winfo_height(), page.winfo_reqheight())
            stable = stable + 1 if current == previous else 0
            previous = current
            loaded = name != "WPS写入工具" or getattr(page, "_writer_load_done", True)
            # 约 150 ms 内尺寸不再变化，且已过最短观察窗，才算完成布局。
            if stable >= 15 and loaded and time.monotonic() - started >= 0.4:
                return
            time.sleep(0.01)

    def test_minimum_size_contract(self):
        self.assertEqual((MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT), (1280, 802))
        self.assertGreaterEqual(DEFAULT_WINDOW_WIDTH, MIN_WINDOW_WIDTH)
        self.assertGreaterEqual(DEFAULT_WINDOW_HEIGHT, MIN_WINDOW_HEIGHT)
        # 常量是 96 DPI 逻辑尺寸；实际窗口按屏幕 DPI 缩放。
        self.assertGreaterEqual(self.app.window_scale, 1.0)

    def test_initial_window_enforces_minimum(self):
        skip_if_window_does_not_fit(self, self.app)
        scale = self.app.window_scale
        expected = (int(MIN_WINDOW_WIDTH * scale), int(MIN_WINDOW_HEIGHT * scale))
        self.assertEqual(self.app.minsize(), expected)
        self.assertGreaterEqual(self.app.winfo_width(), expected[0])
        self.assertGreaterEqual(self.app.winfo_height(), expected[1])

    def test_windows_window_stays_inside_work_area(self):
        # 客户区尺寸等于屏幕高度时窗口本体仍会超出屏幕，右栏和状态栏被裁掉。
        if os.name != "nt":
            self.skipTest("仅 Windows 需要校验工作区边界")
        area = windows_work_area()
        if area is None:
            self.skipTest("无法读取 Windows 工作区")
        self._settle(0.3)
        left, top, width, height = area
        app = self.app
        self.assertLessEqual(app.winfo_rootx() + app.winfo_width(), left + width)
        self.assertLessEqual(app.winfo_rooty() + app.winfo_height(), top + height)

    @staticmethod
    def _widest_children(page) -> str:
        """List the widest descendants, so a compression failure names its cause."""
        rows = []

        def walk(widget, depth=0):
            try:
                requested = widget.winfo_reqwidth()
            except Exception:
                return
            if requested >= 600:
                rows.append((requested, depth, widget.winfo_class()))
            for child in widget.winfo_children():
                walk(child, depth + 1)

        walk(page)
        widest = sorted(rows, reverse=True)[:4]
        return "，".join(f"{cls}@{depth}={req}" for req, depth, cls in widest) or "无"

    def test_all_pages_keep_full_size_at_minimum_window(self):
        skip_if_window_does_not_fit(self, self.app)
        app = self.app
        app.geometry(f"{app.minsize()[0]}x{app.minsize()[1]}")
        self._settle(0.3)
        # Warm up lazy pages once; WPS builds part of its form in a worker.
        for name in PAGE_NAMES:
            app.show_page(name)
            self._settle(0.2)
        for name in PAGE_NAMES:
            with self.subTest(page=name):
                app.show_page(name)
                self._settle_page(name)
                page = app.pages[name]
                context = (
                    f"窗口 {app.winfo_width()}x{app.winfo_height()} "
                    f"页面区 {app.page_host.winfo_width()} "
                    f"页面区请求宽 {app.page_host.winfo_reqwidth()} "
                    f"画布项宽 {app.page_viewport.canvas.itemcget(app.page_viewport.window, 'width')} "
                    f"右栏 {'显示' if app.right_rail_visible else '收起'}"
                )
                widest = self._widest_children(page)
                self.assertGreaterEqual(
                    page.winfo_width(), page.winfo_reqwidth() - 2,
                    f"{name} 被横向压缩：{page.winfo_width()} < {page.winfo_reqwidth()}"
                    f"（{context}；最宽控件 {widest}）",
                )
                self.assertGreaterEqual(
                    page.winfo_height(), page.winfo_reqheight() - 2,
                    f"{name} 被纵向压缩：{page.winfo_height()} < {page.winfo_reqheight()}（{context}）",
                )

    def test_overflow_is_scrollable_and_switching_resets_scroll(self):
        skip_if_window_does_not_fit(self, self.app)
        app = self.app
        app.geometry(f"{app.minsize()[0]}x{app.minsize()[1]}")
        self._settle(0.3)
        app.show_page("现货价格分析表更新")
        self._settle(0.3)
        view = app.page_viewport
        self.assertEqual(view.vertical.winfo_manager(), "grid")
        app.show_page("风力预测截图")
        self._settle(0.3)
        self.assertEqual(view.canvas.yview()[0], 0.0)
        self.assertEqual(view.canvas.xview()[0], 0.0)


if __name__ == "__main__":
    unittest.main()
