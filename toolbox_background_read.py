"""Bounded, latest-result-only metadata reads for Tk forms.

Workers never call Tk or retain widgets. Timeouts stop polling, not Python
threads; a shared limit prevents repeated retries from creating unlimited reads.
"""
import queue
import threading
import time

_slots = threading.BoundedSemaphore(4)


class BackgroundRead:
    def __init__(self, widget, *, timeout=30):
        self.widget = widget
        self.timeout = timeout
        self._after = None
        self._generation = 0
        self.pending = False
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event):
        if event.widget is self.widget:
            self.cancel()

    def cancel(self):
        self._generation += 1
        self.pending = False
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def start(self, read, done):
        self.cancel()
        generation = self._generation
        if not _slots.acquire(blocking=False):
            done(None, "已有文件读取尚未结束，请稍后重试。")
            return
        results = queue.Queue()

        def worker():
            try:
                results.put((read(), None))
            except Exception as exc:
                results.put((None, str(exc) or type(exc).__name__))
            finally:
                _slots.release()

        self.pending = True
        try:
            threading.Thread(target=worker, name="toolbox-metadata", daemon=True).start()
        except Exception:
            _slots.release()
            self.pending = False
            raise
        deadline = time.monotonic() + self.timeout

        def poll():
            self._after = None
            if generation != self._generation:
                return
            try:
                value, error = results.get_nowait()
            except queue.Empty:
                if time.monotonic() < deadline:
                    self._after = self.widget.after(50, poll)
                    return
                value, error = None, "读取超时，请检查文件是否位于断开的网络盘、同步目录，或复制到本机后重试。"
            self.pending = False
            done(value, error)

        self._after = self.widget.after(50, poll)
