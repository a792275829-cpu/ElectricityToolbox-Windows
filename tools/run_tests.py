#!/usr/bin/env python3
"""统一测试入口：按档位逐模块运行仓库里的 unittest 用例。

为什么不直接用 `python -m unittest discover`：`电力工具箱/tests` 有 81 个模块，
其中 33 个会创建 Tk 窗口、15 个会连外网，`discover` 会停在这些用例上（无窗口、
无网络时不是报错而是等），一个模块挂住就拖死整轮。因此这里逐模块起子进程并
带超时，挂住的只影响那一个模块。

用法（在仓库任意目录执行）：

    .venv\\Scripts\\python.exe tools/run_tests.py                   # fast 档（默认）
    .venv\\Scripts\\python.exe tools/run_tests.py full              # 全量，含 GUI、联网与 500kV
    .venv\\Scripts\\python.exe tools/run_tests.py --list            # 只打印分档结果
    .venv\\Scripts\\python.exe tools/run_tests.py --group desktop
    .venv\\Scripts\\python.exe tools/run_tests.py --module test_date_inputs --module test_verify_payload

CI 用法：先设置 `TOOLBOX_XLSX_BACKEND=rust` 之类的后端变量，再用 `--module`
组合出需要 Rust 后端的模块；环境变量会原样传给子进程。

子进程的 TEMP/TMP/TMPDIR 一律指向本轮专用的临时目录。缺少这些变量时
`tempfile.gettempdir()` 会退回当前目录，测试就会在仓库里留下 `安装`、
`升级` 这类空目录（历史上已经出现过）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "电力工具箱"
SECTION = ROOT / "500kV断面分析"
DESKTOP_TESTS = ROOT / "desktop/tests/python"
TOOLBOX_TESTS = TOOLBOX / "tests"
SECTION_TESTS = SECTION / "tests"

# 需要窗口或外网的用例不在 fast 档跑：无窗口、无网络时它们不是报错而是等待。
GUI_HINTS = re.compile(r"\btkinter\b|\bTk\(\)|\bttk\.")
NETWORK_HINTS = re.compile(r"\brequests\b|urlopen|\bplaywright\b|\bsocket\.|\bhttp\.client\b")

DEFAULT_TIMEOUT = 300
DESKTOP_TIMEOUT = 900


@dataclass
class Case:
    name: str
    kind: str            # toolbox | section | desktop
    module: str | None   # 模块文件名（不含 .py）；desktop 为整目录
    tags: set[str] = field(default_factory=set)


@dataclass
class Outcome:
    case: Case
    status: str          # OK | FAIL | TIMEOUT
    seconds: float
    detail: str = ""


def classify(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    tags = set()
    if GUI_HINTS.search(text):
        tags.add("gui")
    if NETWORK_HINTS.search(text):
        tags.add("network")
    return tags


def toolbox_cases() -> list[Case]:
    return [Case(f"toolbox:{path.stem}", "toolbox", path.stem, classify(path))
            for path in sorted(TOOLBOX_TESTS.glob("test_*.py"))]


def section_cases() -> list[Case]:
    return [Case(f"section:{path.stem}", "section", path.stem, classify(path))
            for path in sorted(SECTION_TESTS.glob("test_*.py"))]


def desktop_cases() -> list[Case]:
    """桌面组按整目录跑一次：模块之间共用 helper，逐个起进程反而更慢。"""
    return [Case("desktop", "desktop", None, set())]


def desktop_modules() -> list[Case]:
    return [Case(f"desktop:{path.stem}", "desktop", path.stem, set())
            for path in sorted(DESKTOP_TESTS.glob("test_*.py"))]


def all_cases() -> list[Case]:
    return toolbox_cases() + section_cases() + desktop_cases()


def find_modules(modules: list[str]) -> list[Case]:
    """按文件名找模块：工具箱、500kV、桌面的模块都能用同一个名字指定。"""
    index: dict[str, list[Case]] = {}
    for case in toolbox_cases() + section_cases() + desktop_modules():
        index.setdefault(case.module or "", []).append(case)
    found: list[Case] = []
    for wanted in modules:
        stem = Path(wanted).stem
        matches = index.get(stem) or index.get(wanted) or []
        if not matches:
            raise SystemExit(f"找不到测试模块：{wanted}")
        found.extend(matches)
    return found


def select(tier: str, groups: list[str], modules: list[str]) -> list[Case]:
    if modules:
        return find_modules(modules)

    cases = []
    if "toolbox" in groups:
        cases += toolbox_cases()
    if "section" in groups:
        cases += section_cases()
    if "desktop" in groups:
        cases += desktop_cases()
    if tier == "fast":
        # 500kV 用例依赖 Rust 后端，且单个模块耗时远高于工具箱，只在 full 档跑。
        cases = [case for case in cases
                 if case.kind != "section" and not (case.tags & {"gui", "network"})]
    return cases


def command_for(case: Case) -> tuple[list[str], Path]:
    if case.kind == "toolbox":
        return [sys.executable, "-m", "unittest", "-v", f"tests.{case.module}"], TOOLBOX
    if case.kind == "section":
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests",
                "-p", f"{case.module}.py", "-v"], SECTION
    pattern = f"{case.module}.py" if case.module else "test_*.py"
    return [sys.executable, "-m", "unittest", "discover",
            "-s", "desktop/tests/python", "-p", pattern, "-v"], ROOT


def environment_for(case: Case, scratch: Path) -> dict[str, str]:
    env = os.environ.copy()
    paths = [str(ROOT)]
    if case.kind == "toolbox":
        paths += [str(TOOLBOX), str(TOOLBOX_TESTS)]
    current = env.get("PYTHONPATH")
    if current:
        paths.append(current)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # tempfile 在没有这些变量时会退回当前目录，测试会把空目录留在仓库里。
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(scratch)
    return env


def tail(text: str, limit: int = 25) -> str:
    lines = [line for line in text.rstrip().splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def run_case(case: Case, scratch: Path, timeout: int) -> Outcome:
    command, cwd = command_for(case)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=str(cwd), env=environment_for(case, scratch),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Outcome(case, "TIMEOUT", time.monotonic() - started,
                       f"超过 {timeout}s 未结束（GUI 或联网用例请加 --group/--module 单独跑）")
    elapsed = time.monotonic() - started
    if result.returncode == 0:
        return Outcome(case, "OK", elapsed)
    return Outcome(case, "FAIL", elapsed, tail(result.stderr or result.stdout))


def print_list(cases: list[Case]) -> None:
    for case in cases:
        tags = ",".join(sorted(case.tags)) or "fast"
        print(f"{case.name:<58} {tags}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按档位运行电力工具箱的 unittest 用例")
    parser.add_argument("tier", nargs="?", default="fast", choices=["fast", "full"])
    parser.add_argument("--group", action="append", default=[],
                        choices=["toolbox", "section", "desktop"],
                        help="只跑某个分组，可重复；默认 fast=工具箱+桌面，full=全部")
    parser.add_argument("--module", action="append", default=[],
                        help="指定模块文件名（可重复），忽略档位与分组")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"单模块超时秒数，默认 {DEFAULT_TIMEOUT}")
    parser.add_argument("--list", action="store_true", help="只打印本轮会跑哪些模块")
    parser.add_argument("--keep-temp", action="store_true", help="保留本轮临时目录便于排查")
    args = parser.parse_args(argv)

    groups = args.group or (["toolbox", "desktop"] if args.tier == "fast"
                            else ["toolbox", "section", "desktop"])
    cases = select(args.tier, groups, args.module)
    if not cases:
        print("没有匹配的用例。")
        return 0
    if args.list:
        print_list(cases)
        return 0

    scratch_root = Path(tempfile.mkdtemp(prefix="toolbox-tests-"))
    print(f"档位={args.tier} 分组={','.join(groups)} 用例={len(cases)} 临时目录={scratch_root}")
    outcomes: list[Outcome] = []
    started = time.monotonic()
    try:
        for index, case in enumerate(cases, start=1):
            scratch = scratch_root / f"case-{index:03d}"
            scratch.mkdir()
            timeout = args.timeout if case.kind != "desktop" else max(args.timeout, DESKTOP_TIMEOUT)
            outcome = run_case(case, scratch, timeout)
            outcomes.append(outcome)
            print(f"[{outcome.status:<7}] {case.name:<56} {outcome.seconds:6.1f}s")
            if outcome.detail:
                for line in outcome.detail.splitlines():
                    print(f"          {line}")
            shutil.rmtree(scratch, ignore_errors=True)
    finally:
        if args.keep_temp:
            print(f"临时目录保留在 {scratch_root}")
        else:
            shutil.rmtree(scratch_root, ignore_errors=True)

    elapsed = time.monotonic() - started
    failed = [outcome for outcome in outcomes if outcome.status != "OK"]
    print("-" * 72)
    print(f"通过 {len(outcomes) - len(failed)} / {len(outcomes)}，用时 {elapsed:.1f}s")
    if failed:
        print("失败或超时的模块：")
        for outcome in failed:
            print(f"  - {outcome.case.name}（{outcome.status}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
