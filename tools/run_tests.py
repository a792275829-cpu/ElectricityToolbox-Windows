#!/usr/bin/env python3
"""统一测试入口：按档位逐模块运行仓库里的 unittest 用例。

所有 Python 测试目录统一在 GROUP_DIRECTORIES 注册，逐模块启动子进程并设置超时。
默认 fast 过滤工具箱和业务目录中含 GUI／联网提示的模块；显式 --group 不作过滤。
full 包含全部 Python 组和可选 Node 组；缺少 Node 时明确报告跳过。

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
import ast
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
GROUP_DIRECTORIES = {
    "toolbox": (TOOLBOX, TOOLBOX_TESTS),
    "section": (SECTION, SECTION_TESTS),
    "desktop": (ROOT, DESKTOP_TESTS),
    "market-report": (ROOT / "南方分公司电力市场日报", ROOT / "南方分公司电力市场日报/tests"),
    "report": (ROOT / "每日生产经营情况汇报自动生成工具/scripts", ROOT / "每日生产经营情况汇报自动生成工具/scripts"),
    "trade": (ROOT / "电力工具脚本/电力交易分析工具", ROOT / "电力工具脚本/电力交易分析工具/tests"),
    "summary": (ROOT / "电力工具脚本/电量汇总工具", ROOT / "电力工具脚本/电量汇总工具/tests"),
    "wind": (ROOT / "风力预测截图", ROOT / "风力预测截图/tests"),
}
NODE_TESTS = ROOT / "电力工具脚本/private-data-uploader-tool/tests"


@dataclass
class Case:
    name: str
    kind: str            # GROUP_DIRECTORIES 中的组或 node
    module: str          # 模块文件名（Python 不含 .py，Node 保留 .mjs）
    tags: set[str] = field(default_factory=set)


@dataclass
class Outcome:
    case: Case
    status: str          # OK | FAIL | TIMEOUT | SKIP
    seconds: float
    detail: str = ""


def classify(path: Path, *, legacy_desktop: bool = False) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for statement in ast.parse(text).body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "TOOLBOX_TEST_TAGS"
                for target in statement.targets):
            tags = ast.literal_eval(statement.value)
            if not isinstance(tags, (tuple, list, set)) or any(
                    tag not in {"gui", "network"} for tag in tags):
                raise ValueError(f"{path.name}: TOOLBOX_TEST_TAGS 只允许 gui、network，或空元组")
            return set(tags)
    if legacy_desktop:
        return set()
    tags = set()
    if GUI_HINTS.search(text):
        tags.add("gui")
    if NETWORK_HINTS.search(text):
        tags.add("network")
    return tags


def group_cases(group: str) -> list[Case]:
    if group == "node":
        paths = sorted(NODE_TESTS.glob("*.test.mjs"))
        paths += sorted(TOOLBOX_TESTS.glob("test_*.mjs"))
        return [Case(f"node:{path.name}", "node", path.name) for path in paths]
    _, directory = GROUP_DIRECTORIES[group]
    return [Case(f"{group}:{path.stem}", group, path.stem,
                 classify(path, legacy_desktop=group == "desktop"))
            for path in sorted(directory.glob("test_*.py"))]


def all_cases() -> list[Case]:
    return [case for group in [*GROUP_DIRECTORIES, "node"] for case in group_cases(group)]


def find_modules(modules: list[str]) -> list[Case]:
    index: dict[str, list[Case]] = {}
    for case in all_cases():
        for key in (case.name, case.module):
            index.setdefault(key, []).append(case)
    found: list[Case] = []
    for wanted in modules:
        key = wanted if ":" in wanted or wanted.endswith(".mjs") else Path(wanted).stem
        matches = index.get(key, [])
        if not matches:
            raise SystemExit(f"找不到测试模块：{wanted}")
        for case in matches:
            if case not in found:
                found.append(case)
    return found


def select(tier: str, groups: list[str], modules: list[str]) -> list[Case]:
    if modules:
        return find_modules(modules)
    cases = [case for group in dict.fromkeys(groups) for case in group_cases(group)]
    if tier == "fast":
        cases = [case for case in cases if case.kind not in {"section", "node"}
                 and not (case.tags & {"gui", "network"})]
    return cases


def node_executable() -> str | None:
    portable = ROOT / "runtime/node/node.exe"
    return str(portable) if portable.is_file() else shutil.which("node")


def command_for(case: Case) -> tuple[list[str], Path]:
    if case.kind == "node":
        node = node_executable()
        if node is None:
            raise FileNotFoundError("Node.js 不可用；初始化上传工具后可运行 --group node")
        path = NODE_TESTS / case.module
        if not path.is_file():
            path = TOOLBOX_TESTS / case.module
        return [node, "--test", str(path)], ROOT
    cwd, directory = GROUP_DIRECTORIES[case.kind]
    if case.kind == "toolbox":
        return [sys.executable, "-m", "unittest", "-v", f"tests.{case.module}"], cwd
    return [sys.executable, "-m", "unittest", "discover", "-s", str(directory),
            "-p", f"{case.module}.py", "-v"], cwd


def environment_for(case: Case, scratch: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Tests must not read or update the checkout's real configs or login state.
    data = scratch / "data"
    data.mkdir(exist_ok=True)
    env["TOOLBOX_CODE_ROOT"] = str(ROOT)
    env["TOOLBOX_DATA_ROOT"] = str(data)
    paths = [str(ROOT), str(TOOLBOX)]
    if case.kind == "toolbox":
        paths.append(str(TOOLBOX_TESTS))
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
    started = time.monotonic()
    try:
        command, cwd = command_for(case)
    except FileNotFoundError as exc:
        return Outcome(case, "SKIP", 0, str(exc))
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
                        choices=[*GROUP_DIRECTORIES, "node"],
                        help="只跑某个完整分组，可重复，不受 fast 过滤")
    parser.add_argument("--module", action="append", default=[],
                        help="指定模块文件名（可重复），忽略档位与分组")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"单模块超时秒数，默认 {DEFAULT_TIMEOUT}")
    parser.add_argument("--list", action="store_true", help="只打印本轮会跑哪些模块")
    parser.add_argument("--keep-temp", action="store_true", help="保留本轮临时目录便于排查")
    parser.add_argument("--strict", action="store_true", help="模块依赖缺失导致的跳过视为失败；发行验证必用")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    groups = args.group or (list(GROUP_DIRECTORIES) if args.tier == "fast"
                            else [*GROUP_DIRECTORIES, "node"])
    cases = select("full" if args.group else args.tier, groups, args.module)
    if (args.group and "node" in args.group or args.module and any(
            case.kind == "node" for case in cases)) and node_executable() is None:
        parser.error("Node.js 不可用，无法执行显式指定的 node 分组")
    if not cases:
        print("没有匹配的用例，未执行验证。")
        return 1
    if args.list:
        print_list(cases)
        return 0

    scratch_root = Path(tempfile.mkdtemp(prefix="toolbox-tests-"))
    print(f"档位={args.tier} 分组={','.join(groups)} 用例={len(cases)} 临时目录={scratch_root}", flush=True)
    outcomes: list[Outcome] = []
    started = time.monotonic()
    try:
        for index, case in enumerate(cases, start=1):
            scratch = scratch_root / f"case-{index:03d}"
            scratch.mkdir()
            outcome = run_case(case, scratch, args.timeout)
            outcomes.append(outcome)
            print(f"[{outcome.status:<7}] {case.name:<56} {outcome.seconds:6.1f}s", flush=True)
            if outcome.detail:
                for line in outcome.detail.splitlines():
                    print(f"          {line}")
            if not args.keep_temp:
                shutil.rmtree(scratch, ignore_errors=True)
    finally:
        if args.keep_temp:
            print(f"临时目录保留在 {scratch_root}")
        else:
            shutil.rmtree(scratch_root, ignore_errors=True)

    elapsed = time.monotonic() - started
    failed = [outcome for outcome in outcomes if outcome.status in {"FAIL", "TIMEOUT"}]
    passed = sum(outcome.status == "OK" for outcome in outcomes)
    skipped = sum(outcome.status == "SKIP" for outcome in outcomes)
    print("-" * 72)
    print(f"通过 {passed} / {len(outcomes)}，跳过 {skipped}，用时 {elapsed:.1f}s")
    if failed or (args.strict and skipped):
        print("失败或超时的模块：")
        for outcome in failed:
            print(f"  - {outcome.case.name}（{outcome.status}）")
        if args.strict and skipped:
            print(f"严格模式拒绝 {skipped} 个跳过的模块。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
