from __future__ import annotations

import time
import tkinter as tk
import unittest

from toolbox_file_button import FileSelectButton


def _descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


class FileSelectButtonTests(unittest.TestCase):
    """加号选文件按钮只画图标，且悬停/点击后不得残留任何提示浮层。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("260x120")
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def _button(self, command=None) -> FileSelectButton:
        button = FileSelectButton(self.root, text="添加文件", command=command)
        button.pack(pady=16)
        self.root.update()
        return button

    def _pump(self, seconds: float = 0.8) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.root.update()
            time.sleep(0.02)

    def _popups(self) -> list[tk.Toplevel]:
        return [widget for widget in _descendants(self.root)
                if isinstance(widget, tk.Toplevel)]

    def test_button_draws_plus_icon_only(self):
        button = self._button()
        self.assertEqual("添加文件", button.cget("text"))
        self.assertEqual("image", str(button.cget("compound")))
        self.assertTrue(button.cget("image"))

    def test_hover_and_click_leave_no_tooltip_window(self):
        clicks: list[int] = []
        button = self._button(command=lambda: clicks.append(1))

        # 悬停 → 按下 → 松开（真实环境下这里会弹出文件对话框，关闭后指针
        # 仍停在按钮上），任何阶段都不能冒出浮层。
        button.event_generate("<Enter>")
        self._pump(0.6)
        self.assertEqual([], self._popups())

        button.event_generate("<ButtonPress-1>")
        button.event_generate("<ButtonRelease-1>")
        self._pump(0.8)
        self.assertEqual([1], clicks)
        self.assertEqual([], self._popups())

        # 键盘聚焦同样不应弹出提示。
        button.focus_force()
        self._pump(0.6)
        self.assertEqual([], self._popups())

    def test_hover_text_never_overlaps_button(self):
        button = self._button()
        button.event_generate("<Enter>")
        self._pump(0.8)
        for popup in self._popups():  # pragma: no cover - 回归时给出明确失败信息
            texts = [child.cget("text") for child in _descendants(popup)
                     if "text" in child.keys()]
            self.fail(f"按钮下方出现提示浮层，遮挡按钮：{texts}")


if __name__ == "__main__":
    unittest.main()
