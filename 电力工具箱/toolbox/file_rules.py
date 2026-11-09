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


def scan_clearing_files(downloads_dir: Path | None = None) -> dict[date, Path]:
    """一次扫描 Downloads：每个文件名日期只解析一次、只 stat 一次，按日期分桶
    并只保留各桶 mtime 最大的出清文件。

    返回值是调用时刻的目录快照，仅用于同一次点击内多处查询复用；下次点击会
    重新扫描，保证期间新建的文件能被发现。
    """
    downloads = downloads_dir or (Path.home() / "Downloads")
    latest: dict[date, tuple[Path, float]] = {}
    for path in downloads.glob("*.xlsx"):
        if path.name.startswith("~$") or "出清情况" not in path.name:
            continue
        parsed = clearing_file_date(path)
        if parsed is None:
            continue
        mtime = path.stat().st_mtime
        current = latest.get(parsed)
        if current is None or mtime > current[1]:
            latest[parsed] = (path, mtime)
    return {day: item[0] for day, item in latest.items()}


def find_clearing_files_for_dates(
    today: date,
    downloads_dir: Path | None = None,
    ) -> list[Path]:
    scan = scan_clearing_files(downloads_dir)
    matches: list[Path] = []
    for wanted in (today, today - timedelta(days=2)):
        path = scan.get(wanted)
        if path is not None:
            matches.append(path)
    return matches


def find_workbook_for_day(
    target_day: date,
    downloads_dir: Path | None = None,
    *,
    scan: dict[date, Path] | None = None,
) -> Path | None:
    # 按解析出的完整日期精确比较，避免「11.1」误选「11.10」或漏掉补零的「11.01」。
    if scan is None:
        scan = scan_clearing_files(downloads_dir)
    return scan.get(target_day)


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
