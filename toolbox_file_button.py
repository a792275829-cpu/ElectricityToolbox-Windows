"""Compact file picker shared by toolbox pages and standalone dialogs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

class FileSelectButton(ttk.Button):
    def __init__(self, parent, *, text="选择文件", command=None):
        # Retain the descriptive text for keyboard/accessibility clients and
        # tests; only a clear plus sign is drawn on the button. No hover/focus
        # tooltip: it popped up under the button right after picking a file.
        style = ttk.Style(parent)
        style.configure("FileSelect.TButton", padding=5, width=0, anchor="center")
        if style.theme_use() == "aqua":
            # Aqua's native push-button bezel enforces a 70 px minimum even
            # for an image-only button. Use the same compact icon in dialogs.
            style.layout("FileSelect.TButton", [("Button.padding", {
                "sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})],
            })])
        self._icons = []
        for ink in ("#333333", "#b5b5b0"):
            icon = tk.PhotoImage(master=parent, width=20, height=20)
            icon.put(ink, to=(4, 9, 16, 11))
            icon.put(ink, to=(9, 4, 11, 16))
            self._icons.append(icon)
        super().__init__(parent, text=text, command=command, style="FileSelect.TButton",
                         image=(self._icons[0], "disabled", self._icons[1]), compound="image")
