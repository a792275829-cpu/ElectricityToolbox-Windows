"""Report date read from the D workbook, shared by the page and service worker.

Kept free of Tk imports because ``workflows`` also uses it while running a
headless service task.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import subprocess

from toolbox_platform import hidden_process_options


def report_dates_module():
    from .runtime import load_module
    return load_module(
        "toolbox_market_report_dates",
        Path(__file__).resolve().parents[2]
        / "南方分公司电力市场日报/gd_power_report/report_dates.py",
    )


def report_date_from_day_report(path: str | Path) -> date:
    return report_dates_module().infer_report_date(Path(path))


def report_date_in_subprocess(path: str | Path, *, timeout: float = 25) -> date:
    """Keep Excel parsing, imports and fallback loading out of the Tk process.

    A Python thread alone still shares the GIL and module loader with the UI.
    subprocess.run kills and reaps its child on timeout, including slow full
    workbook fallbacks; the form can then retry without leaving readers behind.
    """
    from .runtime import python_executable, utf8_environment

    script = Path(__file__).resolve().parents[2] / "南方分公司电力市场日报/gd_power_report/report_dates.py"
    try:
        result = subprocess.run(
            [python_executable(), str(script), "--read-date", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            env=utf8_environment(), timeout=timeout, **hidden_process_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("报告日期识别超时，已停止读取。请将文件复制到本机后重试，并检查工作簿是否过大或损坏。") from exc
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        detail = result.stderr.strip()[-1000:] or f"退出码 {result.returncode}"
        raise ValueError(f"报告日期读取进程异常：{detail}") from exc
    if result.returncode or response.get("error"):
        raise ValueError(response.get("error") or f"报告日期读取失败（退出码 {result.returncode}）")
    return date.fromisoformat(response["date"])
