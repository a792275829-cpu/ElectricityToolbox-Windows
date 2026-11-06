from __future__ import annotations
import re
from datetime import date, timedelta
from pathlib import Path

# 单一实现放在根目录，供 toolbox 包与独立的报告 GUI 共用；此处保留名字，
# 让已有的 `from .file_rules import clearing_file_date` 调用方继续可用。
from toolbox_dates import clearing_file_date


def default_output_for_pdf(pdf_path: str | Path) -> Path:
    source = Path(pdf_path).expanduser()
    year = re.search(r"(20\d{2})", source.name)
    month_day = re.search(r"[\(（](\d{2})\.(\d{2})[\)）]", source.name)
    if year and month_day:
        suffix = f"{year.group(1)}{month_day.group(1)}{month_day.group(2)}"
    else:
        suffix = source.stem
    return source.parent / f"现货运行日报报价容量区间{suffix}.xlsx"


def find_clearing_files_for_dates(
    today: date,
    downloads_dir: Path | None = None,
    ) -> list[Path]:
    downloads = downloads_dir or (Path.home() / "Downloads")
    wanted_dates = [today, today - timedelta(days=2)]
    matches: list[Path] = []
    for wanted in wanted_dates:
        candidates = [
            path
            for path in downloads.glob("*.xlsx")
            if not path.name.startswith("~$")
            and "出清情况" in path.name
            and clearing_file_date(path) == wanted
        ]
        if candidates:
            matches.append(max(candidates, key=lambda path: path.stat().st_mtime))
    return matches


def find_workbook_for_day(target_day: date, downloads_dir: Path | None = None) -> Path | None:
    # 按解析出的完整日期精确比较，避免「11.1」误选「11.10」或漏掉补零的「11.01」。
    downloads = downloads_dir or (Path.home() / "Downloads")
    candidates = [
        path
        for path in downloads.glob("*.xlsx")
        if not path.name.startswith("~$")
        and "出清情况" in path.name
        and clearing_file_date(path) == target_day
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def find_latest_energy_file_for_date(
    today: date,
    upload_dir: Path,
    ) -> Path | None:
    target_day = today - timedelta(days=2)
    candidates = [
        path
        for path in upload_dir.glob("*.xls*")
        if not path.name.startswith("~$")
        and "能销" in path.name
        and clearing_file_date(path) == target_day
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
