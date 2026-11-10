import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from toolbox_background_read import BackgroundRead
from toolbox.market_report_dates import report_date_in_subprocess
from toolbox.market_report_page import MarketReportPage
from toolbox.runtime import TaskRegistry, ToolPaths

ROOT = Path(__file__).resolve().parents[2]


class BackgroundReadTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.root.update_idletasks()
        self.root.destroy()

    def pump(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Tk callback did not complete")

    def test_slow_reader_keeps_tk_responsive_and_times_out(self):
        reader = BackgroundRead(self.root, timeout=.08)
        results, heartbeats = [], []
        started = threading.Event()

        def slow():
            started.set()
            self.release.wait(2)
            return "late"

        reader.start(slow, lambda value, error: results.append((value, error)))
        self.assertTrue(started.wait(.5))
        self.root.after(10, lambda: heartbeats.append(True))
        self.pump(lambda: bool(results))
        self.assertTrue(heartbeats)
        self.assertIn("超时", results[0][1])
        self.assertFalse(reader.pending)
        self.assertIsNone(reader._after)
        self.release.set()
        self.root.update()
        self.assertEqual(len(results), 1)

    def test_new_selection_discards_old_result_and_poll(self):
        reader = BackgroundRead(self.root)
        results = []
        finished = threading.Event()

        def old():
            self.release.wait(2)
            finished.set()
            return "old"

        reader.start(old, lambda value, error: results.append(value))
        reader.start(lambda: "new", lambda value, error: results.append(value))
        self.pump(lambda: bool(results))
        self.release.set()
        self.assertTrue(finished.wait(.5))
        self.root.update()
        self.assertEqual(results, ["new"])
        self.assertIsNone(reader._after)

    def test_destroyed_page_cancels_poll_without_worker_tk_calls(self):
        frame = tk.Frame(self.root)
        reader = BackgroundRead(frame)
        results = []
        release = self.release
        reader.start(lambda: release.wait(2), lambda *args: results.append(args))
        frame.destroy()
        release.set()
        self.root.update()
        self.assertFalse(reader.pending)
        self.assertIsNone(reader._after)
        self.assertEqual(results, [])

    def test_generate_while_reading_never_calls_synchronous_reader(self):
        registry = TaskRegistry()
        page = MarketReportPage(self.root, ToolPaths(ROOT), registry)
        release = self.release
        try:
            with mock.patch("toolbox.market_report_page.report_date_from_day_report",
                            side_effect=lambda _: release.wait(2)), \
                 mock.patch.object(page, "derive_report_date") as derive:
                page.day_report.set("slow.xlsx")
                page.report_date.set("2026-11-09")
                page.read_report_date(Path("slow.xlsx"))
                self.assertEqual(page.report_date.get(), "")
                start = time.monotonic()
                with self.assertRaisesRegex(ValueError, "正在识别"):
                    page.current_report_date()
                self.assertLess(time.monotonic() - start, .1)
                derive.assert_not_called()
        finally:
            page.destroy()
            registry.terminate_all()


class IsolatedDateTests(unittest.TestCase):
    def test_real_workbook_preserves_cell_priority_over_filename(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "分公司2026.11.10.xlsx"
            book = Workbook()
            book.active.title = "新快报"
            book.active["A6"] = date(2026, 11, 8)
            book.active["A2"] = date(2026, 11, 9)
            book.save(source)
            book.close()
            self.assertEqual(report_date_in_subprocess(source), date(2026, 11, 8))

    def test_real_process_timeout_returns_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "超时.*停止读取"):
            report_date_in_subprocess(ROOT / "missing.xlsx", timeout=.001)

    def test_corrupt_workbook_error_reaches_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bad.xlsx"
            source.write_bytes(b"broken zip")
            with self.assertRaisesRegex(ValueError, "zip"):
                report_date_in_subprocess(source)

    def test_crashed_reader_does_not_leave_form_waiting(self):
        failure = subprocess.CompletedProcess([], 3, stdout="", stderr="reader failed")
        with mock.patch("toolbox.market_report_dates.subprocess.run", return_value=failure):
            with self.assertRaisesRegex(ValueError, "reader failed"):
                report_date_in_subprocess("anything.xlsx")
