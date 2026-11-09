"""Shared user-date parsing; service requests always receive ISO dates.

Also owns the workbook-filename date parsing used by both the toolbox package
and the standalone report GUI, so every entry point matches dates the same way.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path


DATE_INPUT_HINT = "点击日期框选择，或粘贴日期"
DATE_INPUT_ERROR = "请输入有效年月日，例如 2026-5-3 或 2026年5月3日。"


def parse_date_input(value: str) -> date:
    text = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(r"([0-9]{4})([-/.])([0-9]{1,2})\2([0-9]{1,2})", text)
    if match:
        parts = match.group(1), match.group(3), match.group(4)
    else:
        match = re.fullmatch(r"([0-9]{4})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日", text)
        if match is None:
            match = re.fullmatch(r"([0-9]{4})([0-9]{2})([0-9]{2})", text)
        if match is None:
            raise ValueError(DATE_INPUT_ERROR)
        parts = match.groups()
    try:
        return date(*(int(part) for part in parts))
    except ValueError as exc:
        raise ValueError(DATE_INPUT_ERROR) from exc


def normalize_date_input(value: str) -> str:
    return parse_date_input(value).isoformat()


def clearing_file_date(path: Path) -> date | None:
    """Parse the complete calendar date embedded in a downloaded workbook name.

    Comparing the parsed date instead of a raw ``2026.11.1`` substring avoids
    confusing 11.1 with 11.10, and also accepts zero-padded ``2026.11.01``.
    """
    patterns = [r"(20\d{2})[.\-_/年](\d{1,2})[.\-_/月](\d{1,2})", r"(20\d{2})(\d{2})(\d{2})"]
    for pattern in patterns:
        match = re.search(pattern, path.name)
        if match:
            try:
                return date(*(int(value) for value in match.groups()))
            except ValueError:
                return None
    return None
