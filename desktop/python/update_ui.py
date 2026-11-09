"""Tk update UI for digest-verified DMG downloads."""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import messagebox, ttk

from github_update import Cancelled, GitHubUpdater
from toolbox_platform import window_scale, windows_frame_metrics, windows_work_area


class UpdateController:
    RETRY_DELAY_MS = 30_000
    RECHECK_INTERVAL_MS = 3 * 60 * 60 * 1000

    def __init__(self, app, service, updater=None):
        self.app, self.service = app, service
        self.updater = updater or GitHubUpdater(os.environ.get("TOOLBOX_APP_VERSION", "0.1.3"))
        self.release = None
        self.checking = False
        self.manual = False
        self.window = None
        self._retry_count = 0
        self._retry_after = None
        self.messages = queue.Queue()
        self.app.set_update_check_failed(False, retry=lambda: self.check(manual=True))
        self.app.after(40, self.poll)
        self._initial_after = self.app.after(300, self._initial_check)
        self.app.after(self.RECHECK_INTERVAL_MS, self._periodic_check)

    def worker(self, operation, done):
        def run():
            try:
                result = operation()
            except Exception as exc:
                self.messages.put((done, None, exc))
            else:
                self.messages.put((done, result, None))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        for _ in range(64):
            try:
                callback, result, error = self.messages.get_nowait()
            except queue.Empty:
                break
            callback(result, error)
        self.app.after(40, self.poll)

    def _cancel_retry(self):
        if self._retry_after is not None:
            self.app.after_cancel(self._retry_after)
            self._retry_after = None

    def _initial_check(self):
        self._initial_after = None
        self.check()

    def _retry_check(self):
        self._retry_after = None
        self.check(_retry=True)

    def _periodic_check(self):
        self.app.after(self.RECHECK_INTERVAL_MS, self._periodic_check)
        self.check()

    def check(self, manual=False, *, _retry=False):
        self.manual = self.manual or manual
        if self.checking:
            return
        if self._initial_after is not None:
            self.app.after_cancel(self._initial_after)
            self._initial_after = None
        self._cancel_retry()
        if not _retry:
            self._retry_count = 0
        self.checking = True
        self._checking_state(True)
        self.worker(self.updater.check, self.checked)

    def _checking_state(self, checking):
        # The footer button also carries progress; a slow API + release-page
        # fallback can take tens of seconds, so never leave it looking dead.
        notify = getattr(self.app, "set_update_checking", None)
        if callable(notify):
            try:
                notify(checking)
            except Exception:
                pass

    def checked(self, release, error):
        self.checking = False
        self._checking_state(False)
        manual, self.manual = self.manual, False
        if error:
            self.app.set_update_check_failed(True)
            if self._retry_count < 2 and self._retry_after is None:
                self._retry_count += 1
                self._retry_after = self.app.after(self.RETRY_DELAY_MS, self._retry_check)
            if manual:
                messagebox.showerror("无法检查更新", str(error), parent=self.app)
            return
        self._cancel_retry()
        self._retry_count = 0
        self.app.set_update_check_failed(False)
        self.release = release
        self.app.set_update_available(release is not None)
        if manual:
            if release:
                self.show()
            else:
                messagebox.showinfo("已是最新版本", f"当前版本 v{self.updater.current}", parent=self.app)

    def show(self):
        if not self.release:
            return
        if self.window and self.window.winfo_exists():
            self.window.lift()
            return
        self.window = UpdateWindow(self, self.release)

    def update_event(self, method, params):
        window = self.window
        if window and window.winfo_exists():
            window.update_event(method, params)
        elif method == "update_failed":
            messagebox.showerror("更新未完成", params.get("error", "请重试更新"), parent=self.app)

    def activate(self):
        self.check()


class UpdateWindow(tk.Toplevel):
    # 96 DPI 下的设计尺寸。窗口按 DPI 缩放后还要受屏幕工作区限制，见 _fit_to_content()。
    WIDTH = 760
    MIN_WIDTH = 640
    MIN_HEIGHT = 520
    SCREEN_MARGIN = 64
    BODY_PADDING = 24

    def __init__(self, controller, release):
        super().__init__(controller.app)
        self.controller, self.release = controller, release
        self.downloading = self.installing = False
        self.cancel_download = threading.Event()
        self.downloaded_path = None
        self._wraplength = 0
        self.title("软件更新")
        self.transient(controller.app)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.bind("<Escape>", lambda _e: self.dismiss())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body = ttk.Frame(self, padding=self.BODY_PADDING)
        body.grid(sticky="nsew")
        self.body = body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text=f"发现新版本 {release.version}", font=("Helvetica", 20, "bold")).grid(sticky="w")
        self.is_windows = bool(release.asset and release.asset.get('name', '').endswith('.exe'))
        self.platform_text = f"当前 v{controller.updater.current}  →  {release.version}  ·  " + ('Windows x64' if self.is_windows else 'Apple 芯片')
        ttk.Label(body, text=self.platform_text).grid(sticky="w", pady=(8, 16))
        ttk.Separator(body).grid(sticky="ew", pady=(0, 12))
        notes_frame = ttk.Frame(body)
        notes_frame.grid(sticky="nsew")
        self.notes_frame = notes_frame
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(0, weight=1)
        notes = tk.Text(notes_frame, wrap="word", relief="flat", height=12, padx=8, pady=8)
        notes.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(notes_frame, command=notes.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        notes.configure(yscrollcommand=scrollbar.set)
        self.notes = notes
        self._notes_rows = self._render_notes(release.notes)
        self._notes_font = tkfont.Font(root=self, font=notes.cget("font") or "TkTextFont")
        notes.tag_configure(
            "heading",
            font=(self._notes_font.cget("family"), max(9, abs(int(self._notes_font.cget("size"))) + 2), "bold"),
            spacing1=10, spacing3=4,
        )
        for text, heading in self._notes_rows:
            if heading:
                notes.insert("end", text + "\n", "heading")
            else:
                notes.insert("end", text + "\n")
        notes.configure(state="disabled")
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.grid(sticky="ew", pady=(12, 8))
        self.status = tk.StringVar(self, "开始更新后将自动下载、校验、退出并安装，再重新启动工具箱；配置和数据保留。" +
                                  ('Windows 优先下载小补丁；补丁不适用时使用完整安装包，安装失败自动恢复旧程序。' if self.is_windows else '日常优先使用增量补丁。'))
        self.status_label = ttk.Label(body, textvariable=self.status, wraplength=490)
        self.status_label.grid(sticky="w", pady=(0, 16))
        actions = ttk.Frame(body)
        actions.grid(sticky="ew")
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="发布页面", command=lambda: webbrowser.open(release.page)).grid(row=0, column=0)
        self.cancel_button = ttk.Button(actions, text="稍后", command=self.dismiss)
        self.cancel_button.grid(row=0, column=2, padx=(8, 8))
        self.primary = ttk.Button(actions, text="开始更新", command=self.perform, style="Primary.TButton")
        self.primary.grid(row=0, column=3)
        if not release.asset:
            self.primary.state(["disabled"])
            self.status.set("此版本尚未提供 Apple 芯片安装包，请查看发布页面。")
        self._fit_to_content()
        self.bind("<Configure>", self._on_resize, add="+")

    def _fit_to_content(self):
        """Size the window from the release notes, then clamp it to the display.

        原来固定 560x510：更新说明每版约 90 行，正文只能显示十几行；而 Tk 的
        geometry 在 Windows 高 DPI 下是物理像素，150% 屏幕上这个尺寸的观感只有
        约 373x340，整个窗口看起来很小。这里按 DPI 放大，并按屏幕工作区、说明
        文本的实际换行行数取高度。
        """
        self.update_idletasks()
        scale = window_scale(self)
        work_area = windows_work_area()
        if work_area is not None:
            left, top, area_width, area_height = work_area
        else:
            left = top = 0
            area_width, area_height = self.winfo_screenwidth(), self.winfo_screenheight()
        frame_width, frame_height = windows_frame_metrics()
        margin = int(self.SCREEN_MARGIN * scale)
        available_width = max(int(self.MIN_WIDTH * scale), area_width - frame_width - margin)
        available_height = max(int(self.MIN_HEIGHT * scale), area_height - frame_height - margin)
        width = min(int(self.WIDTH * scale), available_width)
        fixed_height = self.body.winfo_reqheight() - self.notes_frame.winfo_reqheight()
        needed = fixed_height + self._notes_height(width, scale)
        height = min(max(needed, int(self.MIN_HEIGHT * scale)), available_height)
        x = left + max(0, (area_width - width - frame_width) // 2)
        y = top + max(0, (area_height - height - frame_height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(int(self.MIN_WIDTH * scale), available_width),
                     min(int(self.MIN_HEIGHT * scale), available_height))
        self._sync_wraplength(width)

    def _render_notes(self, notes):
        """Turn the GitHub release body into readable plain text rows.

        更新说明是 Release 的 Markdown 正文，直接塞进 Text 会显示成
        「### 修复…」「`code`」「**粗体**」，一屏里近半字符是标记符号。
        这里只做显示层清洗，不改动下载与校验使用的原始内容。
        """
        rows = []
        for raw in (notes or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                rows.append(("", False))
                continue
            stripped = line.strip()
            if len(stripped) >= 3 and set(stripped) <= {"-", "*", "_"}:
                rows.append(("", False))  # 分隔线
                continue
            heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
            if heading:
                rows.append((self._strip_markdown(heading.group(1), True), True))
                continue
            rows.append((self._strip_markdown(stripped), False))
        while rows and not rows[-1][0]:
            rows.pop()
        return rows or [("", False)]

    @staticmethod
    def _strip_markdown(text, heading=False):
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"\1", text)
        if heading:
            text = re.sub(r"[*_`]", "", text)
        else:
            text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"^>\s?", "", text)
        return text.rstrip()

    def _notes_height(self, width, scale):
        """Estimate the pixel height the notes need at the final window width."""
        padding = int(self.BODY_PADDING * scale)
        usable = max(120, width - 2 * padding - 40)  # 滚动条 + 边框
        font = self._notes_font
        line_height = font.metrics("linespace")
        measured = {}

        def char_width(char):
            if char not in measured:
                measured[char] = font.measure(char)
            return measured[char]

        rows = 0
        extra = 0
        for text, heading in self._notes_rows:
            row_width, line_rows = 0, 1
            for char in text:
                size = char_width(char)
                if row_width + size > usable and row_width:
                    line_rows += 1
                    row_width = size
                else:
                    row_width += size
            rows += line_rows
            if heading:
                extra += int(line_height * .35) + 14  # spacing1 + spacing3
        return rows * line_height + extra + 2 * int(8 * scale) + 4

    def _on_resize(self, event):
        if event.widget is self:
            self._sync_wraplength(event.width)

    def _sync_wraplength(self, width):
        target = max(240, width - 2 * self.BODY_PADDING - 24)
        if abs(target - self._wraplength) > 8:
            self._wraplength = target
            self.status_label.configure(wraplength=target)

    def dismiss(self):
        if self.installing:
            return
        if self.downloading:
            self.cancel_download.set()
            self.status.set("正在取消下载…")
            return
        self.destroy()

    def perform(self):
        if self.downloading or self.installing:
            return
        try:
            self.controller.service.call('prepare_update', {})
        except Exception as exc:
            self.status.set(str(exc))
            return
        self.cancel_download.clear()
        self.downloading = True
        self.primary.configure(text="下载中…", state="disabled")
        self.cancel_button.configure(text="取消下载")
        self.status.set("正在连接 GitHub…")
        def progress(fraction):
            self.controller.messages.put((self.download_progress, fraction, None))
        def download():
            try:
                self.controller.updater.prepare_update()
                return self.controller.updater.download(self.release, self.cancel_download, progress)
            except Exception as error:
                try:
                    self.controller.updater.abort_update()
                except Exception as cleanup:
                    error.add_note(f'清理更新状态失败：{cleanup}')
                raise
        self.controller.worker(download, self.download_finished)

    def download_progress(self, fraction, error):
        if self.winfo_exists():
            self.progress["value"] = fraction * 100
            updater = self.controller.updater
            phase = getattr(updater, 'phase', '')
            size = getattr(updater, 'download_bytes', 0)
            mode = '增量更新' if getattr(updater, 'update_mode', '') == 'delta' else '完整更新'
            self.status.set(f"{mode} · {size/1024**2:.1f} MB · {phase} · {fraction:.0%}"
                            if phase and size else f"正在下载并准备更新 · {fraction:.0%}")

    def download_finished(self, path, error):
        if not self.winfo_exists():
            return
        self.downloading = False
        if isinstance(error, Cancelled):
            self.controller.service.call('abort_update', {})
            self.destroy()
            return
        if self.cancel_download.is_set():
            def cancelled(_result, _error):
                self.controller.service.call('abort_update', {})
                self.destroy()
            self.controller.worker(self.controller.updater.abort_update, cancelled)
            return
        self.cancel_button.configure(text="关闭")
        self.primary.configure(state="normal", text="重试下载")
        if error:
            self.controller.service.call('abort_update', {})
            self.status.set(str(error))
            return
        self.downloaded_path = path
        self.installing = True
        self.primary.configure(text="安装准备中…", state='disabled')
        self.cancel_button.configure(state='disabled')
        self.status.set('正在校验 Windows 安装包并准备退出交接…' if self.is_windows else '正在校验镜像和应用，准备自动安装…')
        def prepare():
            try:
                return self.controller.updater.prepare_install(self.release, path)
            except Exception as error:
                try:
                    self.controller.updater.abort_update()
                except Exception as cleanup:
                    error.add_note(f'清理更新状态失败：{cleanup}')
                raise
        self.controller.worker(prepare, self.install_prepared)

    def install_prepared(self, result, error):
        if error:
            self.controller.service.call('update_failed', dict(error=str(error)))
            return
        self.controller.worker(self.controller.updater.commit_install, self.install_committed)

    def install_committed(self, result, error):
        if error:
            self.abort_install(error)
            return
        self.status.set('校验通过，正在退出工具箱并自动安装；完成后将自动重新打开。')
        self._close_deadline = time.monotonic() + 30
        self.finish_update()

    def finish_update(self):
        try:
            self.controller.service.call('finish_update', {})
        except Exception as exc:
            if '等待' in str(exc) and time.monotonic() < self._close_deadline:
                self.after(200, self.finish_update)
            else:
                self.abort_install(exc)

    def abort_install(self, error):
        def finished(_result, _error):
            self.controller.service.call('update_failed', dict(error=str(error)))
        self.controller.worker(self.controller.updater.abort_update, finished)

    def update_event(self, method, params):
        if method == "update_cancelled":
            self.downloading = False
            self.destroy()
        elif method == "update_failed":
            self.downloading = self.installing = False
            self.primary.configure(state="normal", text="重试更新")
            self.cancel_button.configure(state="normal", text="稍后")
            self.status.set(params.get("error", "更新失败，请重试。"))
        elif method == "update_progress":
            stage = params.get("stage", "downloading")
            self.progress["value"] = params.get("percent", 0)
            if stage == "installing":
                self.downloading = False
                self.installing = True
                self.cancel_button.configure(state="disabled")
                self.status.set("签名校验通过，正在自动更新并重新启动…")
            else:
                self.status.set(f"正在下载及校验签名 · {params.get('percent', 0)}%")
