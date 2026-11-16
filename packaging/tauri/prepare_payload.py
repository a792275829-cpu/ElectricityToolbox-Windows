"""Source selection and resource helpers used by the Windows payload builder."""

from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "desktop_build_support", Path(__file__).with_name("build_support.py")
)
build_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_support)


MODULE_DIRECTORIES = {
    "电力工具箱", "上网电量抓取", "风力预测截图", "市场表更新", "现货价格更新",
    "交易系统信息更新", "功率调整收益统计表更新", "集团每日上传", "广东电价预测",
    "广东运行日报PDF解析", "南方分公司电力市场日报", "每日生产经营情况汇报自动生成工具",
    "500kV断面分析", "wps自动", "电力工具脚本",
}

# Fixed runtime inputs, including templates used indirectly by renderers.
REQUIRED_RUNTIME_ASSETS = tuple(map(Path, (
    "500kV断面分析/500kV节点拓扑.md",
    "500kV断面分析/src/region_curves.js",
    "500kV断面分析/src/region_curves.css",
    "500kV断面分析/data/500kv_node_positions.json",
    "500kV断面分析/data/500kv_node_position_candidates.json",
    "500kV断面分析/接线图.png",
    "500kV断面分析/tools/anchor_calibrator/index.html",
    "500kV断面分析/tools/anchor_calibrator/app.js",
    "500kV断面分析/tools/anchor_calibrator/styles.css",
    "500kV断面分析/tools/topology_editor/index.html",
    "500kV断面分析/tools/topology_editor/app.js",
    "500kV断面分析/tools/topology_editor/style.css",
    "风力预测截图/fonts/NotoSerifSC.ttf",
    "每日生产经营情况汇报自动生成工具/模板/每日生产经营情况汇报（自动）.docx",
    "每日生产经营情况汇报自动生成工具/scripts/load_compare_subject_map.json",
    "wps自动/wps_excel_to_kdocs_config.defaults.json",
    "集团每日上传/templates/省内数据模板.xlsx",
    "广东运行日报PDF解析/gd_pdf_tool/recognize_image_labels.ps1",
    "南方分公司电力市场日报/gd_power_report/templates/legacy_print_report.css",
    "南方分公司电力市场日报/gd_power_report/templates/legacy_concise_report.css",
    "南方分公司电力市场日报/gd_power_report/templates/legacy_print_report.html.j2",
    "南方分公司电力市场日报/gd_power_report/templates/legacy_concise_report.html.j2",
    "南方分公司电力市场日报/gd_power_report/templates/report_logo.html.j2",
    "南方分公司电力市场日报/gd_power_report/templates/metric_card_layout.js.j2",
    "desktop/assets/app-icon-256.png",
)))
FORECAST_SEED = Path("广东电价预测/输出/广东电价数据总表.xlsx")


def validate_runtime_assets(source: Path) -> None:
    for relative in REQUIRED_RUNTIME_ASSETS:
        path = source / relative
        if not path.is_file() or not path.stat().st_size or (
            path.suffix == ".md" and not path.read_text(encoding="utf-8").strip()
        ):
            raise FileNotFoundError(f"打包缺少必需功能资源或文件为空：{relative}")


# `playwright install chromium` 会把完整 Chromium、headless shell 和 ffmpeg 全装上，
# Windows 上合计 547 MB。工具箱只用到前两个：ffmpeg 仅供 record_video 使用，本仓库
# 没有任何调用方；locales 除简中／英文外都用不到；chrome_proxy / pwa_launcher /
# notification_helper 是浏览器自身发布用的辅助进程，自动化不需要。
BROWSER_KEPT_LOCALES = frozenset({"zh-CN.pak", "en-US.pak"})
BROWSER_DROPPED_EXECUTABLES = ("chrome_proxy.exe", "chrome_pwa_launcher.exe",
                               "notification_helper.exe")


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def prune_browser_runtime(browsers: Path) -> dict[str, int]:
    """Trim Playwright browser payload the toolbox never loads.

    只删除确实用不到的静态文件，保留 `INSTALLATION_COMPLETE` 与全部 dll／pak，
    因此浏览器行为不变；安装包因此少约 20 MB（locales 为主）。
    """
    if not browsers.is_dir():
        return {}
    saved: dict[str, int] = {}
    for folder in sorted(browsers.glob("ffmpeg-*")):
        saved[f"ffmpeg:{folder.name}"] = _tree_size(folder)
        shutil.rmtree(folder)
    for folder in sorted(browsers.glob("chromium*")):
        for locales in sorted(folder.glob("chrome-*/locales")):
            removed = 0
            for pak in locales.glob("*.pak"):
                if pak.name not in BROWSER_KEPT_LOCALES:
                    removed += pak.stat().st_size
                    pak.unlink()
            if removed:
                saved[f"locales:{folder.name}"] = removed
        for binary in folder.glob("chrome-*/*.exe"):
            if binary.name in BROWSER_DROPPED_EXECUTABLES:
                saved[f"exe:{binary.name}"] = binary.stat().st_size
                binary.unlink()
    return saved


def prepare_forecast_seed(defaults: Path, workbook: Path | None = None) -> Path:
    """Ship a byte-preserving initial workbook; never manage users' live data."""
    from migrate_forecast_data import copy_atomic, resolve_paths
    from openpyxl import load_workbook

    if workbook is None:
        configured = os.environ.get("FORECAST_MASTER_WORKBOOK")
        url = os.environ.get("FORECAST_MASTER_URL")
        if configured:
            workbook = Path(configured).expanduser()
        elif url:
            expected = os.environ.get("FORECAST_MASTER_SHA256", "")
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("下载预测总表必须设置有效的 FORECAST_MASTER_SHA256")
            workbook = build_support.download(
                url, ROOT / "dist/tauri-build/forecast" / expected / "master.xlsx", expected,
            )
        else:
            workbook = resolve_paths(ROOT / "广东电价预测")["masterWorkbook"]
    workbook = Path(workbook)
    if not workbook.is_file():
        raise FileNotFoundError("打包缺少电价预测总数据表，请提供 --forecast-workbook 或 FORECAST_MASTER_WORKBOOK")
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        sheet = book["整合数据"] if "整合数据" in book.sheetnames else book.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, ())
        if not {"日期", "时刻", "全省日前平均电价"}.issubset(headers) or next(rows, None) is None:
            raise ValueError("预测总表缺少必要字段或数据行")
    finally:
        book.close()
    target = defaults / FORECAST_SEED
    copy_atomic(workbook, target)
    return target


def runtime_source(relative: Path) -> bool:
    if not build_support.exportable(relative):
        return False
    if relative.as_posix() in {'desktop/python/windows_update.py', 'desktop/python/windows_delta.py'}:
        return True
    if relative.as_posix() in {"desktop/python/native_bridge.py", "desktop/python/service_bridge.py", "desktop/python/service_compat.py", "desktop/python/task_worker.py", "desktop/python/github_update.py", "desktop/python/delta_update.py", "desktop/python/update_ui.py", "desktop/assets/app-icon-256.png"}:
        return True
    if len(relative.parts) == 1:
        return relative.name.startswith("toolbox_") and relative.suffix == ".py"
    return (relative.parts[0] in MODULE_DIRECTORIES
            and not {"tests", "docs", "scripts-dev"}.intersection(relative.parts)
            and not relative.name.startswith("test_")
            and relative.suffix.lower() != ".command")


def prepare_defaults(source: Path, defaults: Path) -> None:
    """Build first-install settings separately from mutable user configuration."""
    shutil.rmtree(defaults, ignore_errors=True)
    defaults.mkdir(parents=True)
    for example in source.rglob("config.example.json"):
        raw = json.loads(example.read_text(encoding="utf-8"))
        for key in ("username", "password"):
            if key in raw:
                raw[key] = ""
        example.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        path = defaults / example.relative_to(source).with_name("config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    wps_source = source / "wps自动/wps_excel_to_kdocs_config.defaults.json"
    wps_target = defaults / "wps自动/wps_excel_to_kdocs_config.json"
    wps_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wps_source, wps_target)
    config = defaults / "电力工具脚本/private-data-uploader-tool/upload.config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {"baseUrl": "https://xhxt.chng.com.cn", "sourceDir": ".", "headless": True}
        ),
        encoding="utf-8",
    )
