"""0.1.31 移植改动的无 GUI 回归测试。

刻意不 import tkinter：本机及 CI 可能没有 Tk，页面测试类会被整体 skip，
而这批缺陷（run_process 参数错配、登录分支漏注册、路径逗号截断、日期误选）
恰恰因此漏网。这里全部用静态检查与纯逻辑断言守住。
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
TOOLBOX_DIR = HERE.parents[1]
ROOT = HERE.parents[2]
for _entry in (str(ROOT), str(TOOLBOX_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from toolbox import commands, workflows
from toolbox.file_rules import find_workbook_for_day
from toolbox.runtime import ToolPaths
from toolbox_dates import clearing_file_date

TOOLBOX_PKG = TOOLBOX_DIR / "toolbox"
UPLOADER_SCRIPTS = ROOT / "电力工具脚本" / "private-data-uploader-tool" / "scripts"
UPLOADER = UPLOADER_SCRIPTS / "upload-private-data.mjs"


def node_available() -> bool:
    return shutil.which("node") is not None


class RunProcessSignatureTests(unittest.TestCase):
    """防止新增页面传入基类 run_process 不接受的关键字。"""

    def test_all_run_process_calls_match_base_signature(self) -> None:
        widgets = ast.parse((TOOLBOX_PKG / "widgets.py").read_text(encoding="utf-8"))
        allowed = None
        for node in ast.walk(widgets):
            if isinstance(node, ast.FunctionDef) and node.name == "run_process":
                allowed = {argument.arg for argument in node.args.kwonlyargs}
                if node.args.kwarg:
                    allowed.add(node.args.kwarg.arg)
        self.assertIsNotNone(allowed, "widgets.ToolPage.run_process 未找到")

        violations = []
        for path in TOOLBOX_PKG.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "run_process"):
                    continue
                keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
                extra = keywords - allowed
                if extra:
                    violations.append((path.name, node.lineno, sorted(extra)))
        self.assertEqual(violations, [], f"run_process 收到签名外参数：{violations}")


class PrivateBatchWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        if not node_available():
            self.skipTest("node 不可用")
        self.paths = ToolPaths(ROOT)

    def test_login_plan_uses_dedicated_node_command(self) -> None:
        plan = workflows.plan(self.paths, "private-batch-upload", "login", {})
        command, cwd = plan[0]
        self.assertEqual(cwd, self.paths.private_uploader_dir)
        self.assertIn("--login", command)
        self.assertIn("--headed", command)
        self.assertIn("--batch", command)
        self.assertTrue(command[1].endswith("upload-private-data.mjs"))

    def test_login_plan_matches_commands_login(self) -> None:
        expected = commands.login(self.paths, "private-batch-upload")
        actual = workflows.plan(self.paths, "private-batch-upload", "login", {})[0][0]
        self.assertEqual(actual, expected)

    def test_files_use_repeated_flags_and_keep_commas(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "复盘,最终.xlsx"
            second = Path(folder) / "日前.xlsx"
            first.write_bytes(b"0")
            second.write_bytes(b"0")
            command = commands.private_batch(self.paths, "海风", [first, second])
            self.assertEqual(command.count("--files"), 2)
            self.assertEqual(command[command.index("--files") + 1], str(first))
            self.assertEqual(command[command.index("--files", command.index("--files") + 1) + 1], str(second))
            self.assertNotIn(f"{first},{second}", command)


class WorkbookDateMatchingTests(unittest.TestCase):
    """11.1 不能误选 11.10，也不能漏掉补零的 11.01。"""

    def test_parsed_date_is_shared_by_package_and_gui(self) -> None:
        from toolbox import file_rules
        self.assertIs(file_rules.clearing_file_date, clearing_file_date)

    def test_eleven_one_does_not_select_eleven_ten(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            downloads = Path(folder)
            (downloads / "2026.11.10出清情况.xlsx").write_bytes(b"0")
            self.assertIsNone(find_workbook_for_day(date(2026, 11, 1), downloads))
            (downloads / "2026.11.1出清情况.xlsx").write_bytes(b"0")
            self.assertEqual(
                find_workbook_for_day(date(2026, 11, 1), downloads).name,
                "2026.11.1出清情况.xlsx",
            )

    def test_zero_padded_filename_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            downloads = Path(folder)
            (downloads / "2026.11.01出清情况.xlsx").write_bytes(b"0")
            self.assertEqual(
                find_workbook_for_day(date(2026, 11, 1), downloads).name,
                "2026.11.01出清情况.xlsx",
            )
        self.assertEqual(clearing_file_date(Path("2026.11.10出清情况.xlsx")), date(2026, 11, 10))


class UploaderScriptTests(unittest.TestCase):
    def test_no_undefined_login_failure_helper(self) -> None:
        source = UPLOADER.read_text(encoding="utf-8")
        self.assertNotIn("loginFailure(", source)
        self.assertIn("进入私有数据导入页面时登录态失效，请先重新登录。", source)
        self.assertIn("throw lastError ?? new Error(", source)

    def test_cli_collects_raw_file_values(self) -> None:
        source = UPLOADER.read_text(encoding="utf-8")
        self.assertIn("parseFileArguments(args.files)", source)
        self.assertIn("args.files.push(String(argv[++i] ?? '').trim());", source)
        self.assertNotIn("args.files.push(...String(argv[++i]", source)

    def test_script_passes_node_syntax_check(self) -> None:
        if not node_available():
            self.skipTest("node 不可用")
        result = subprocess.run(
            ["node", "--check", str(UPLOADER)], capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_output_is_redacted(self) -> None:
        source = UPLOADER.read_text(encoding="utf-8")
        self.assertIn("function redact(", source)
        self.assertIn("console.log(redact(message))", source)
        self.assertIn("console.error(redact(detail)", source)
        self.assertIn("return redact(String(error?.message", source)
        self.assertNotIn("JSON.stringify(loginBody)", source)

    def test_failed_batch_reports_completed_progress(self) -> None:
        source = UPLOADER.read_text(encoding="utf-8")
        self.assertIn("已完成 ${completedBatches}/${batches.length} 批", source)
        self.assertIn("请勿重复上传已成功的文件", source)
        self.assertIn("上传进度：已完成", source)


class FileArgumentTests(unittest.TestCase):
    """Node 侧 --files 还原：Windows 含逗号路径不能被截断。"""

    def setUp(self) -> None:
        if not node_available():
            self.skipTest("node 不可用")

    def run_node(self, body: str):
        module_url = (UPLOADER_SCRIPTS / "batch-plan.mjs").resolve().as_uri()
        script = f"import * as m from {json.dumps(module_url)};\n{body}\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path)], capture_output=True, text=True, timeout=60,
            )
        if result.returncode != 0:
            self.fail(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_existing_comma_path_stays_whole(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "复盘,最终.xlsx"
            target.write_bytes(b"0")
            result = self.run_node(
                "console.log(JSON.stringify(m.parseFileArguments("
                + json.dumps([str(target)]) + ")));"
            )
            self.assertEqual(result, [str(target)])

    def test_legacy_comma_list_still_splits(self) -> None:
        result = self.run_node(
            "console.log(JSON.stringify(m.parseFileArguments(['a.xlsx,b.xlsx'])));"
        )
        self.assertEqual(result, ["a.xlsx", "b.xlsx"])

    def test_gui_repeated_flags_keep_all_paths(self) -> None:
        paths = ToolPaths(ROOT)
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "复盘,最终.xlsx"
            second = Path(folder) / "日前.xlsx"
            first.write_bytes(b"0")
            second.write_bytes(b"0")
            command = commands.private_batch(paths, "海风", [first, second])
            values = [command[i + 1] for i, item in enumerate(command) if item == "--files"]
            result = self.run_node(
                "console.log(JSON.stringify(m.parseFileArguments("
                + json.dumps(values) + ")));"
            )
            self.assertEqual(result, [str(first), str(second)])


class BatchBoundaryTests(unittest.TestCase):
    """补齐拆批的边界用例（恰好 12 个、恰好 10MB、空批次、混合）。"""

    def run_node(self, body: str):
        # Windows 上 ESM 的 import specifier 必须是 file:// URL，不能是 "C:\..."。
        module_url = (UPLOADER_SCRIPTS / "batch-plan.mjs").resolve().as_uri()
        script = (
            f"import * as m from {json.dumps(module_url)};\n"
            "const file=(name,size)=>({path:'/tmp/'+name,size});\n"
            f"{body}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path)], capture_output=True, text=True, timeout=60,
            )
        if result.returncode != 0:
            self.fail(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def setUp(self) -> None:
        if not node_available():
            self.skipTest("node 不可用")

    def test_exactly_twelve_files_stay_in_one_batch(self) -> None:
        sizes = self.run_node(
            "const files = Array.from({length:12}, (_, i) => file('a'+i+'.xlsx', 1000));\n"
            "console.log(JSON.stringify(m.planBatches(files).map(b => b.files.length)));"
        )
        self.assertEqual(sizes, [12])

    def test_exactly_ten_megabytes_stay_in_one_batch(self) -> None:
        sizes = self.run_node(
            "console.log(JSON.stringify(m.planBatches([file('a.xlsx', 10*1048576)])"
            ".map(b => b.files.length)));"
        )
        self.assertEqual(sizes, [1])

    def test_empty_list_produces_no_batches(self) -> None:
        self.assertEqual(self.run_node("console.log(JSON.stringify(m.planBatches([])));"), [])

    def test_twelve_files_and_size_limit_combined(self) -> None:
        sizes = self.run_node(
            "const files = Array.from({length:14}, (_, i) => file('a'+i+'.xlsx', 1048576));\n"
            "console.log(JSON.stringify(m.planBatches(files).map(b => b.files.length)));"
        )
        self.assertEqual(sizes, [10, 4])
        self.assertEqual(sum(sizes), 14)


if __name__ == "__main__":
    unittest.main()
