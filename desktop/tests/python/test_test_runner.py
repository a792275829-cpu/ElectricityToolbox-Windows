"""Regression coverage for discovery, isolation and retained failure evidence."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLBOX_TEST_TAGS = ()


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("toolbox_test_runner", ROOT / "tools/run_tests.py")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class TestRunnerTests(unittest.TestCase):
    def test_actual_worker_reports_import_skip_partial_skip_and_empty_discovery(self):
        for text, expected_status, count, skipped in (
            ("import unittest\nraise unittest.SkipTest('required library missing')\n", "SKIP", 1, 1),
            ("import unittest\nclass Tests(unittest.TestCase):\n def test_ok(self): pass\n @unittest.skip('optional capability missing')\n def test_skip(self): pass\n", "OK", 2, 1),
            ("# empty test module\n", "FAIL", 0, 0),
        ):
            with self.subTest(status=expected_status), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                (folder / "test_fixture.py").write_text(text, encoding="utf-8")
                case = runner.Case("desktop:fixture", "desktop", "fixture")
                command = [sys.executable, str(ROOT / "tools/unittest_worker.py"),
                           "discover", "-s", str(folder), "-p", "test_fixture.py", "-v"]
                with patch.object(runner, "command_for", return_value=(command, ROOT)):
                    outcome = runner.run_case(case, folder, 15)
                self.assertEqual(outcome.status, expected_status, outcome.detail)
                self.assertEqual(outcome.tests, count)
                self.assertEqual(len(outcome.skips), skipped)
                if skipped:
                    self.assertIn("missing", outcome.detail)
                self.assertTrue((folder / "stderr.log").is_file())

    def test_actual_strict_entry_rejects_missing_rust_service_and_keeps_counts(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            report = folder / "results.json"
            result = subprocess.run([sys.executable, str(ROOT / "tools/run_tests.py"),
                "full", "--strict", "--module", "test_rust_service", "--report", str(report)],
                cwd=ROOT, env={**os.environ, "TOOLBOX_SERVICE_BINARY": str(folder / "missing.exe")},
                capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            rows = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["status"], "SKIP")
            self.assertGreater(rows[0]["tests"], 0)
            self.assertEqual(rows[0]["tests"], len(rows[0]["skipped_tests"]))
            self.assertIn("build native/price-xlsx", result.stdout)

    def test_platform_exclusion_is_explicit_and_does_not_hide_dependency_skips(self):
        platform = "linux" if sys.platform == "win32" else "win32"
        case = runner.Case("desktop:fixture", "desktop", "fixture", platforms=(platform,))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(runner.run_case(case, Path(directory), 5).status, "N/A")
        with patch.object(runner, "select", return_value=[case]):
            self.assertEqual(runner.main(["full", "--strict"]), 0)

    def test_explicit_tags_override_text_heuristics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test_fixture.py'
            path.write_text('TOOLBOX_TEST_TAGS = ()\n# requests tkinter\n', encoding='utf-8')
            self.assertEqual(runner.classify(path), set())
            path.write_text('TOOLBOX_TEST_TAGS = ("network",)\n', encoding='utf-8')
            self.assertEqual(runner.classify(path, legacy_desktop=True), {'network'})
            path.write_text('TOOLBOX_TEST_TAGS = ("netwrok",)\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                runner.classify(path)

    def test_strict_mode_rejects_a_skipped_module(self):
        case = runner.Case('node:fixture.mjs', 'node', 'fixture.mjs')
        with (
            patch.object(runner, 'select', return_value=[case]),
            patch.object(runner, 'run_case', return_value=runner.Outcome(case, 'SKIP', 0, 'missing')),
        ):
            self.assertEqual(runner.main(['full', '--strict']), 1)
            self.assertEqual(runner.main(['full']), 0)

    def test_all_workspace_python_tests_are_reachable(self):
        import subprocess

        tracked = subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard", "-z", "*test*.py"], cwd=ROOT,
        ).decode("utf-8").split("\0")
        expected = {ROOT / name for name in tracked
                    if Path(name).name.startswith("test_") and (ROOT / name).is_file()}
        actual = {
            directory / f"{case.module}.py"
            for group, (_, directory) in runner.GROUP_DIRECTORIES.items()
            for case in runner.group_cases(group)
        }
        self.assertFalse(expected - actual, expected - actual)

    def test_desktop_modules_have_separate_commands(self):
        cases = runner.group_cases("desktop")
        self.assertGreater(len(cases), 1)
        for case in cases:
            command, _ = runner.command_for(case)
            self.assertIn(f"{case.module}.py", command)
            self.assertNotIn("test_*.py", command)

    def test_each_module_overrides_user_data_and_temp_directories(self):
        case = runner.Case("toolbox:fixture", "toolbox", "fixture")
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            with patch.dict(runner.os.environ, TOOLBOX_DATA_ROOT="user-data"):
                env = runner.environment_for(case, scratch)
            self.assertEqual(Path(env["TOOLBOX_DATA_ROOT"]), scratch / "data")
            self.assertEqual(env["TOOLBOX_CODE_ROOT"], str(ROOT))
            self.assertTrue((scratch / "data").is_dir())
            self.assertEqual(env["TEMP"], str(scratch))

    def test_explicit_section_group_is_not_filtered_by_fast(self):
        with patch.object(runner, "print_list") as display:
            self.assertEqual(runner.main(["--group", "section", "--list"]), 0)
        self.assertEqual(len(display.call_args.args[0]), len(runner.group_cases("section")))
        self.assertGreater(len(display.call_args.args[0]), 0)

    def test_keep_temp_retains_module_artifacts_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "run"
            scratch.mkdir()
            case = runner.Case("desktop:fixture", "desktop", "fixture")

            def fake_run(selected, folder, timeout):
                self.assertEqual(timeout, 7)
                (folder / "evidence.txt").write_text("failure evidence", encoding="utf-8")
                return runner.Outcome(selected, "FAIL", 0, "fixture failure")

            with (
                patch.object(runner, "select", return_value=[case]),
                patch.object(runner.tempfile, "mkdtemp", return_value=str(scratch)),
                patch.object(runner, "run_case", side_effect=fake_run),
            ):
                self.assertEqual(runner.main(["--keep-temp", "--timeout", "7"]), 1)
            self.assertEqual((scratch / "case-001/evidence.txt").read_text(), "failure evidence")

    def test_no_matches_fails_and_duplicate_selectors_run_once(self):
        with patch.object(runner, "select", return_value=[]):
            self.assertEqual(runner.main([]), 1)
        cases = runner.find_modules(["test_test_runner", "desktop:test_test_runner"])
        self.assertEqual(len(cases), 1)

    def test_node_missing_is_explicitly_skipped(self):
        case = runner.Case("node:fixture.mjs", "node", "fixture.mjs")
        with patch.object(runner, "node_executable", return_value=None):
            result = runner.run_case(case, ROOT, 1)
            self.assertEqual(result.status, "SKIP")
            with self.assertRaises(SystemExit):
                runner.main(["--group", "node"])
