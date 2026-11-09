from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from toolbox_platform import hidden_process_options, windows_powershell_executable, windows_system_environment


def recognize_image_labels(image_path: Path) -> list[dict]:
    """Use local OS OCR; preserve Vision's normalized bottom-left box protocol."""
    if sys.platform == "win32":
        helper = Path(__file__).with_name("recognize_image_labels.ps1")
        command = [str(windows_powershell_executable()), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(helper), str(image_path.resolve())]
    else:
        helper = Path(__file__).with_name("recognize_image_labels.js")
        command = ["/usr/bin/osascript", "-l", "JavaScript", str(helper), str(image_path)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=90, env=windows_system_environment() if sys.platform == 'win32' else None,
            **hidden_process_options(),
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"图片标签识别失败：{detail.strip()}") from exc


def merge_image_labels(table, labels: list[dict], width: float, height: float) -> list[list]:
    """Match image labels to detected cells without replacing native PDF values."""
    values = table.extract()
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row.cells):
            if cell is None:
                continue
            original = values[row_index][column_index]
            # Nonempty cells, including every capacity value, stay untouched.
            if original and original.strip():
                continue
            matched = []
            for label in labels:
                if not re.search(r"[\u4e00-\u9fff]", label["text"]):
                    continue
                origin, size = label["box"]["origin"], label["box"]["size"]
                center_x = (origin["x"] + size["width"] / 2) * width
                center_y = (1 - origin["y"] - size["height"] / 2) * height
                if cell[0] <= center_x < cell[2] and cell[1] <= center_y < cell[3]:
                    matched.append((center_y, center_x, label["text"]))
            if matched:
                values[row_index][column_index] = "\n".join(item[2] for item in sorted(matched))
    return values


# 本次解析内的 OCR 结果缓存：键含 (文件绝对路径, mtime_ns, size, 页号)，
# 文件被改动即失效，且仅驻留内存、不跨进程保留。
_LABELS_CACHE = {}


def _page_cache_key(page):
    """Derive a stable (file, page) key; disable caching when identity is unknown."""
    pdf = getattr(page, "pdf", None)
    stream = getattr(pdf, "stream", None) if pdf is not None else None
    name = getattr(stream, "name", None) if stream is not None else None
    page_number = getattr(page, "page_number", None)
    if not isinstance(name, str) or not isinstance(page_number, int):
        return None
    try:
        path = Path(name).resolve()
        stat = path.stat()
    except (OSError, ValueError, TypeError):
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size, page_number)


def _recognized_labels(page) -> list[dict]:
    key = _page_cache_key(page)
    if key is not None and key in _LABELS_CACHE:
        return _LABELS_CACHE[key]
    with tempfile.TemporaryDirectory(prefix="daily-pdf-labels-") as folder:
        image_path = Path(folder) / "page.png"
        image = page.to_image(resolution=180).original
        try:
            image.save(image_path)
        finally:
            image.close()
        labels = recognize_image_labels(image_path)
    if key is not None:
        _LABELS_CACHE[key] = labels
    return labels


def extract_image_label_tables(page) -> list[list[list]]:
    """Recover rasterized headings only on pages with declaration-like tables."""
    if not page.images:
        return []
    tables = [
        table for table in page.find_tables()
        if any(
            sum(bool(re.search(r"\d.*[%％]", cell or "")) for cell in row) >= 4
            for row in table.extract()
        )
    ]
    if not tables:
        return []
    labels = _recognized_labels(page)
    return [merge_image_labels(table, labels, page.width, page.height) for table in tables]
