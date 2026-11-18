"""Cancellation checkpoints shared by Tk workers and their CLI counterparts."""
from contextvars import ContextVar
import os
from pathlib import Path
from threading import Event


CANCEL_FILE_ENV = "TOOLBOX_TASK_CANCEL_FILE"
CANCEL_EXIT_CODE = 130
COOPERATIVE_TOOLS = frozenset({
    "daily-pdf-parser", "group-upload", "guangdong-price", "market-table",
    "power-benefit", "private-batch-upload", "spot-price", "summary-upload",
    "transaction-info", "wps-writer",
})
_check = ContextVar("toolbox_cancellation_check", default=None)


class TaskCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise TaskCancelled("任务已取消")


def uses_checkpoints(tool_id, action):
    return tool_id in COOPERATIVE_TOOLS and action in {"run", "update", "predict", "preview"}


def run_with_checkpoint(check, run):
    binding = _check.set(check)
    try:
        return run()
    finally:
        _check.reset(binding)


def checkpoint():
    check = _check.get()
    if check is not None:
        check()
    marker = os.environ.get(CANCEL_FILE_ENV)
    if marker and Path(marker).exists():
        raise TaskCancelled("任务已取消，已完成的写入或上传请查看日志。")
