"""Keep full-size tool forms reachable when the window is smaller than a page."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import BACKGROUND

# 延迟装配的页面（WPS 表体）只改变请求尺寸、不改变当前尺寸，视口收不到
# <Configure>。布局完成后按固定节拍复查若干次，发现请求尺寸变大就重新测量。
LAYOUT_PROBE_ATTEMPTS = 8
LAYOUT_PROBE_DELAY_MS = 150


class Viewport(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, width: int) -> None:
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, width=width, height=1,
                                background=BACKGROUND, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.content = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self._refresh_id = None
        self._probe_id = None
        self._probe_left = 0
        self.canvas.bind("<Configure>", self.refresh)
        self.content.bind("<Configure>", self.refresh)
        self._layout_bindings = {
            event: self.winfo_toplevel().bind(event, self._queue_refresh, add="+")
            for event in ("<Configure>", "<Map>", "<Unmap>")
        }
        self._wheel_binding = self.winfo_toplevel().bind("<MouseWheel>", self._scroll, add="+")
        self._focus_binding = self.winfo_toplevel().bind("<FocusIn>", self._reveal_focus, add="+")

    def refresh(self, _event=None) -> None:
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
        # Only mapped children contribute to the grid request, so a previously
        # visited large page does not impose its size on the next small page.
        required_width = self.content.winfo_reqwidth()
        required_height = self.content.winfo_reqheight()
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        for bar, needed, position in (
            (self.vertical, required_height > height, dict(row=0, column=1, sticky="ns")),
            (self.horizontal, required_width > width, dict(row=1, column=0, sticky="ew")),
        ):
            if needed and not bar.winfo_manager():
                bar.grid(**position)
            elif not needed and bar.winfo_manager():
                bar.grid_remove()
        size = (max(width, required_width), max(height, required_height))
        if size != getattr(self, "_content_size", None):
            self._content_size = size
            self.canvas.itemconfigure(self.window, width=size[0], height=size[1])
            self.canvas.configure(scrollregion=(0, 0, *size))
        self._start_layout_probe()

    def _start_layout_probe(self) -> None:
        if self._probe_id is not None:
            self.after_cancel(self._probe_id)
        self._probe_left = LAYOUT_PROBE_ATTEMPTS
        self._probe_id = self.after(LAYOUT_PROBE_DELAY_MS, self._probe_layout)

    def _probe_layout(self) -> None:
        self._probe_id = None
        applied = getattr(self, "_content_size", (0, 0))
        needed = (self.content.winfo_reqwidth(), self.content.winfo_reqheight())
        if needed[0] > applied[0] or needed[1] > applied[1]:
            self.refresh()
            return
        self._probe_left -= 1
        if self._probe_left > 0:
            self._probe_id = self.after(LAYOUT_PROBE_DELAY_MS, self._probe_layout)

    def _queue_refresh(self, event) -> None:
        # A child can change its requested size while the canvas window keeps
        # the content's actual size fixed (notably WPS's deferred construction).
        if self._contains(event.widget) and self._refresh_id is None:
            self._refresh_id = self.after_idle(self.refresh)

    def reset(self) -> None:
        # moveto 在已经归位时仍然会让画布重绘一遍窗口项，先判断再移动。
        if self.canvas.xview()[0] != 0.0:
            self.canvas.xview_moveto(0)
        if self.canvas.yview()[0] != 0.0:
            self.canvas.yview_moveto(0)

    def _contains(self, widget) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _scroll(self, event):
        if not self._contains(event.widget):
            return
        # Native text/list/table scroll bindings run before the toplevel binding.
        # Leave those widgets alone to avoid scrolling two containers at once.
        if event.widget is not self.canvas and hasattr(event.widget, "yview"):
            return
        delta = int(event.delta)
        units = -int(delta / 120) if abs(delta) >= 120 else -delta
        horizontal = bool(event.state & 0x1)
        bar = self.horizontal if horizontal else self.vertical
        if bar.winfo_manager():
            view = self.canvas.xview_scroll if horizontal else self.canvas.yview_scroll
            view(units, "units")
            return "break"

    def _reveal_focus(self, event) -> None:
        widget = event.widget
        if not self._contains(widget) or widget in (self.canvas, self.horizontal, self.vertical):
            return
        for axis, position, extent, view, move in (
            ("x", widget.winfo_rootx(), widget.winfo_width(), self.canvas.xview, self.canvas.xview_moveto),
            ("y", widget.winfo_rooty(), widget.winfo_height(), self.canvas.yview, self.canvas.yview_moveto),
        ):
            origin = getattr(self.canvas, f"winfo_root{axis}")()
            available = getattr(self.canvas, "winfo_width" if axis == "x" else "winfo_height")()
            total = self.content.winfo_width() if axis == "x" else self.content.winfo_height()
            offset = position - origin
            correction = offset if offset < 0 else max(0, offset + extent - available)
            if correction:
                move(max(0, view()[0] + correction / max(1, total)))

    def destroy(self) -> None:
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        if self._probe_id is not None:
            self.after_cancel(self._probe_id)
            self._probe_id = None
        top = self.winfo_toplevel()
        for event, binding in self._layout_bindings.items():
            top.unbind(event, binding)
        top.unbind("<MouseWheel>", self._wheel_binding)
        top.unbind("<FocusIn>", self._focus_binding)
        super().destroy()
