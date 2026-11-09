from __future__ import annotations

import shutil
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os
from pathlib import Path

from .runtime import ToolPaths, node_executable, node_file_usable


class DiagnosticLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    category: str = "运行环境"
    recommendation: str = ""
    level: DiagnosticLevel | None = None

    @property
    def status(self) -> DiagnosticLevel:
        if self.level is not None:
            return self.level
        return DiagnosticLevel.OK if self.ok else DiagnosticLevel.ERROR


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    checks: tuple[DiagnosticCheck, ...]
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)

    @property
    def errors(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.ERROR)

    @property
    def warnings(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.WARNING)

    @property
    def passed(self) -> tuple[DiagnosticCheck, ...]:
        return tuple(item for item in self.checks if item.status is DiagnosticLevel.OK)

    def summary(self) -> str:
        icons = {
            DiagnosticLevel.OK: "✓",
            DiagnosticLevel.WARNING: "!",
            DiagnosticLevel.ERROR: "✗",
        }
        lines = [
            f"诊断时间：{self.generated_at:%Y-%m-%d %H:%M:%S}",
            f"结果：{len(self.passed)} 项正常，{len(self.warnings)} 项提醒，{len(self.errors)} 项异常",
            "",
        ]
        for item in self.checks:
            lines.append(f"{icons[item.status]} [{item.category}] {item.name}：{item.detail}")
            if item.recommendation and item.status is not DiagnosticLevel.OK:
                lines.append(f"  建议：{item.recommendation}")
        return "\n".join(lines)


WINDOWS_SETUP_HINT = "运行平台初始化脚本 setup_windows.ps1（在工具箱目录下用 PowerShell 执行）。"


def _rust_backend_check() -> DiagnosticCheck:
    """Rust 业务后端不是所有页面都必需，但缺它会让两个功能直接不可用。"""
    try:
        from toolbox_native_excel import native_binary

        binary = native_binary()
    except (ImportError, OSError, ValueError) as exc:
        binary = None
        detail = f"无法检查：{exc}"
    else:
        detail = str(binary) if binary is not None else (
            "未安装；WPS 文档配置、市场日报「填写日前及实时市场」不可用，"
            "其余工具自动回退 Python 实现"
        )
    return DiagnosticCheck(
        "Rust 业务后端",
        binary is not None,
        detail,
        "运行环境",
        f"构建原生后端：{WINDOWS_SETUP_HINT}",
        DiagnosticLevel.OK if binary is not None else DiagnosticLevel.WARNING,
    )


def _playwright_browser_dir() -> Path | None:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override not in {"0", ""}:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "ms-playwright" if base else None


def _bundled_browser_executable() -> Path | None:
    cache = _playwright_browser_dir()
    if cache is None or not cache.is_dir():
        return None
    layouts = (
        ("chromium-*", ("chrome-win/chrome.exe", "chrome-win64/chrome.exe")),
        ("chromium_headless_shell-*", ("chrome-win/headless_shell.exe",
                                       "chrome-win64/headless_shell.exe")),
    )
    for pattern, relatives in layouts:
        for folder in sorted(cache.glob(pattern)):
            for relative in relatives:
                candidate = folder / relative
                if candidate.is_file():
                    return candidate
    return None


def _portable_mode() -> bool:
    # 安装包外壳会设置 TOOLBOX_PORTABLE=1，此时各抓取脚本以 channel=None 启动，
    # 只用内置 Chromium，系统 Chrome 根本不参与。
    return os.environ.get("TOOLBOX_PORTABLE") == "1"


def _system_browser_executable() -> Path | None:
    # 源码运行时抓取脚本优先用系统 Chrome、再回退内置 Chromium，所以装了
    # Chrome/Edge 的机器即使没下载内置浏览器也能登录抓取。
    if _portable_mode():
        return None
    candidates: list[Path] = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Google/Chrome/Application/chrome.exe")
            candidates.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    return next((path for path in candidates if path.is_file()), None)


def _browser_check() -> DiagnosticCheck:
    system = _system_browser_executable()
    bundled = _bundled_browser_executable()
    if system is not None:
        return DiagnosticCheck(
            "浏览器自动化",
            True,
            f"使用系统浏览器：{system}" if bundled is None
            else f"系统浏览器：{system}（已内置 Chromium 兜底）",
        )
    if bundled is not None:
        return DiagnosticCheck("浏览器自动化", True, f"内置 Chromium：{bundled}")
    if _portable_mode():
        # 便携版不认系统浏览器，让用户去装 Chrome 只会白跑一趟。
        return DiagnosticCheck(
            "浏览器自动化",
            False,
            f"便携版固定使用内置 Chromium，但内置浏览器缺失（查找目录：{_playwright_browser_dir()}）；"
            "登录抓取和市场日报 PDF 不可用",
            "运行环境",
            "重新安装工具箱（安装包自带 Chromium），或运行初始化脚本补齐运行时。",
            DiagnosticLevel.WARNING,
        )
    return DiagnosticCheck(
        "浏览器自动化",
        False,
        "未找到 Chrome/Edge，也没有内置 Chromium；登录抓取和市场日报 PDF 不可用",
        "运行环境",
        f"安装 Chrome 或 Edge，或{WINDOWS_SETUP_HINT}",
        DiagnosticLevel.WARNING,
    )


def _path_access_check(name: str, path: Path, *, category: str = "文件与权限") -> DiagnosticCheck:
    if not path.exists():
        return DiagnosticCheck(
            name,
            False,
            f"找不到：{path}",
            category,
            "确认工具箱目录完整；不要单独移动或删除脚本文件。",
        )
    readable = os.access(path, os.R_OK)
    if not readable:
        return DiagnosticCheck(
            name,
            False,
            f"没有读取权限：{path}",
            category,
            "请授予工具箱读取该目录的权限（文件夹属性 → 安全 → 编辑，给当前用户读取权限）。",
        )
    return DiagnosticCheck(name, True, str(path), category)


def diagnose_path(path: Path, *, require_write: bool = True) -> DiagnosticReport:
    """只读检查一个用户文件的存在性与当前进程访问权限。"""
    path = Path(path).expanduser().resolve()
    checks = [_path_access_check("文件存在且可读取", path)]
    if path.exists():
        target = path if path.is_dir() else path.parent
        writable = os.access(target, os.W_OK)
        if path.is_file():
            writable = writable and os.access(path, os.W_OK)
        checks.append(
            DiagnosticCheck(
                "保存目录可写",
                writable or not require_write,
                str(target) if writable else f"没有写入权限：{target}",
                "文件与权限",
                "关闭可能占用文件的 WPS/Excel，并检查目录访问权限。",
                DiagnosticLevel.OK if writable else (
                    DiagnosticLevel.ERROR if require_write else DiagnosticLevel.WARNING
                ),
            )
        )
        if path.is_file() and path.name.startswith("~$"):
            checks.append(
                DiagnosticCheck(
                    "临时锁文件",
                    True,
                    "这是 Office/WPS 生成的临时文件，不应作为输入文件。",
                    "文件与权限",
                    "请选择不以 ~$ 开头的原始工作簿。",
                    DiagnosticLevel.WARNING,
                )
            )
    return DiagnosticReport(tuple(checks))


def diagnose_environment(paths: ToolPaths) -> DiagnosticReport:
    checks = [
        DiagnosticCheck(
            "Python",
            sys.version_info >= (3, 11),
            f"{sys.version.split()[0]} · {sys.executable}",
            "运行环境",
            "请运行 setup_windows.ps1 初始化环境，或重新安装工具箱。",
        )
    ]
    workspace = paths.workspace
    checks.append(_path_access_check("工具箱工作目录", workspace))
    if workspace.exists():
        writable = os.access(workspace, os.W_OK)
        checks.append(
            DiagnosticCheck(
                "工作目录写入权限",
                writable,
                str(workspace) if writable else f"没有写入权限：{workspace}",
                "文件与权限",
                "检查目录权限，或将完整工具箱移动到当前用户可写的目录。",
            )
        )
        try:
            free_bytes = shutil.disk_usage(workspace).free
        except OSError as exc:
            checks.append(
                DiagnosticCheck(
                    "可用磁盘空间",
                    True,
                    f"无法读取：{exc}",
                    "运行环境",
                    "如生成文件失败，请检查磁盘剩余空间。",
                    DiagnosticLevel.WARNING,
                )
            )
        else:
            free_gib = free_bytes / (1024 ** 3)
            low_space = free_gib < 1.0
            checks.append(
                DiagnosticCheck(
                    "可用磁盘空间",
                    True,
                    f"{free_gib:.1f} GB",
                    "运行环境",
                    "建议至少保留 1 GB 空间用于浏览器缓存和报表输出。",
                    DiagnosticLevel.WARNING if low_space else DiagnosticLevel.OK,
                )
            )
    tool_files: tuple[tuple[str, Path], ...] = (
        ("上网电量", paths.online_energy), ("交易分析", paths.trade_analysis),
        ("500kV断面分析", paths.section_analysis_runner),
        ("500kV拓扑名单", paths.section_analysis_topology),
        ("广东运行日报解析上传", paths.daily_pdf_parser),
        ("南方分公司电力市场日报", paths.market_report_runner),
        ("电量汇总", paths.summary), ("报告工具", paths.report_gui),
        ("私有上传", paths.private_uploader), ("集团上传", paths.group_upload),
        ("市场表更新", paths.market_table_update),
        ("现货价格分析表更新", paths.spot_price_update),
        ("交易系统信息更新", paths.transaction_info_update),
        ("功率调整收益统计表更新", paths.power_benefit_update),
        ("WPS写入", paths.wps_writer),
        ("广东数据抓取", paths.guangdong_data_update),
        ("广东电价预测", paths.guangdong_price_forecast),
    )
    for name, path in tool_files:
        checks.append(_path_access_check(name, path, category="工具文件"))

    node = node_executable()
    node_path = Path(node)
    node_available = node != "node" or shutil.which("node") is not None
    if node_path.is_absolute():
        node_available = node_file_usable(node_path)
    checks.append(
        DiagnosticCheck(
            "Node.js（仅私有上传需要）",
            True,
            node if node_available else "未找到；其他工具不受影响",
            "运行环境",
            "私有数据上传不可用时，运行 setup_windows.ps1 自动准备便携 Node.js，或配置 TOOLBOX_NODE 环境变量。",
            DiagnosticLevel.OK if node_available else DiagnosticLevel.WARNING,
        )
    )
    modules = paths.private_uploader.parent.parent / "node_modules"
    checks.append(
        DiagnosticCheck(
            "私有上传依赖（node_modules）",
            modules.is_dir(),
            str(modules) if modules.is_dir() else "未安装；私有数据批量上传会直接失败",
            "运行环境",
            "在电力工具脚本/private-data-uploader-tool 目录执行 npm ci。",
            DiagnosticLevel.OK if modules.is_dir() else DiagnosticLevel.WARNING,
        )
    )
    checks.append(_rust_backend_check())
    checks.append(_browser_check())
    return DiagnosticReport(tuple(checks))


# 运行期不变的环境探测结果按工作目录缓存：重复构造诊断面板时不再重复做
# 17 个路径检查、磁盘/浏览器缓存与 nvm/fnm 目录扫描。用户主动重新检查时
# 用 force=True 强制重算；检查项相同，检查时间由调用方组装报告时取当前时刻。
_ENVIRONMENT_CHECKS: dict[Path, tuple[DiagnosticCheck, ...]] = {}
_ENVIRONMENT_LOCK = threading.Lock()


def cached_environment_checks(paths: ToolPaths, *, force: bool = False) -> tuple[DiagnosticCheck, ...]:
    """返回一次环境探测的检查项；命中缓存时跳过重复的文件系统扫描。"""
    key = Path(paths.workspace)
    with _ENVIRONMENT_LOCK:
        if not force:
            cached = _ENVIRONMENT_CHECKS.get(key)
            if cached is not None:
                return cached
        checks = diagnose_environment(paths).checks
        _ENVIRONMENT_CHECKS[key] = checks
        return checks
