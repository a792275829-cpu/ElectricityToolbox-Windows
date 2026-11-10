"""Local WPS file selection must never parse a workbook on the Tk thread."""
import threading
import time
import tkinter as tk
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from toolbox.runtime import ToolPaths, load_module


class WpsLocalFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = ToolPaths(Path(__file__).resolve().parents[2])
        cls.module = load_module("wps_local_files_test", paths.wps_writer)

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.release = threading.Event()
        self.worker_done = threading.Event()
        self.dialogs = []

    def tearDown(self):
        self.release.set()
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.destroy()
        self.root.update_idletasks()
        self.root.destroy()

    def dialog(self, mapping=False):
        cls = self.module.MappingDialog if mapping else self.module.DocumentConfigDialog
        with mock.patch.object(cls, "wait_visibility"), mock.patch.object(cls, "wait_window"):
            dialog = cls(self.root, "Local file test")
        self.dialogs.append(dialog)
        return dialog

    def pump(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Local file read did not finish")

    def slow(self, _path):
        self.release.wait(3)
        self.worker_done.set()
        return ["Old sheet"]

    def test_metadata_skips_styles_shared_strings_and_worksheet_contents(self):
        from openpyxl import Workbook
        from openpyxl.chart import BarChart
        with TemporaryDirectory() as folder:
            path = Path(folder) / "source.xlsx"
            book = Workbook()
            book.active.title = "中文 & 表格"
            book.create_sheet("隐藏表").sheet_state = "hidden"
            chart = book.create_chartsheet("图表页")
            chart.add_chart(BarChart())
            book.save(path)
            expected = book.sheetnames
            book.close()
            original = zipfile.ZipFile.read
            parts = []

            def read(archive, name, *args, **kwargs):
                parts.append(name)
                return original(archive, name, *args, **kwargs)

            with mock.patch.object(self.module, "load_excel_workbook", side_effect=AssertionError("full load")), \
                 mock.patch.object(zipfile.ZipFile, "read", new=read):
                self.assertEqual(self.module.read_excel_sheet_names(path), expected)
            self.assertEqual(parts, ["[Content_Types].xml", "xl/workbook.xml"])
            # The ZIP handle is closed after the metadata read.
            renamed = path.with_name("renamed.xlsx")
            path.rename(renamed)

    def test_plus_button_returns_promptly_and_tk_keeps_processing_events(self):
        dialog = self.dialog()
        ticks = []
        with mock.patch.object(self.module.filedialog, "askopenfilename", return_value="slow.xlsx"), \
             mock.patch.object(self.module, "read_excel_sheet_names", side_effect=self.slow):
            start = time.monotonic()
            dialog.browse_local_file()
            self.assertLess(time.monotonic() - start, .2)
            self.assertEqual(dialog.source_doc_title.get(), "正在读取…")
            self.root.after(10, lambda: ticks.append(True))
            self.pump(lambda: bool(ticks))
            self.assertTrue(dialog._local_reader.pending)
            self.release.set()
            self.pump(lambda: dialog.local_sheets == ["Old sheet"])
            self.assertEqual(dialog.source_doc_title.get(), "slow.xlsx")

    def test_latest_file_wins_over_slow_old_selection(self):
        dialog = self.dialog()

        def read(path):
            return self.slow(path) if path.name == "old.xlsx" else ["New sheet"]

        with mock.patch.object(self.module, "read_excel_sheet_names", side_effect=read):
            dialog.local_file.set("old.xlsx")
            dialog.refresh_local_sheets()
            dialog.local_file.set("new.xlsx")
            dialog.refresh_local_sheets()
            self.pump(lambda: dialog.local_sheets == ["New sheet"])
            self.release.set()
            self.assertTrue(self.worker_done.wait(1))
            self.root.update()
            self.assertEqual(dialog.local_sheets, ["New sheet"])
            self.assertEqual(dialog.source_doc_title.get(), "new.xlsx")

    def test_timeout_preserves_saved_regions_and_sheet_names(self):
        dialog = self.dialog()
        region = self.module.RegionRow("Saved", "A1", "A2", "Target", "B1", "B2")
        dialog.regions = [region]
        dialog.local_sheets = ["Saved"]
        dialog._local_reader.timeout = .05
        with mock.patch.object(self.module, "read_excel_sheet_names", side_effect=self.slow), \
             mock.patch.object(self.module.messagebox, "showerror") as error:
            dialog.local_file.set("slow.xlsx")
            dialog.refresh_local_sheets()
            self.pump(lambda: dialog.source_doc_title.get() == "读取失败")
            self.assertIn("超时", error.call_args.args[1])
            self.assertEqual(dialog.local_sheets, ["Saved"])
            self.assertEqual(dialog.regions, [region])
            self.assertIsNone(dialog._local_reader._after)
            self.release.set()
            self.assertTrue(self.worker_done.wait(1))

    def test_switching_to_online_source_discards_local_result(self):
        dialog = self.dialog()
        with mock.patch.object(self.module, "read_excel_sheet_names", side_effect=self.slow):
            dialog.local_file.set("slow.xlsx")
            dialog.refresh_local_sheets()
            dialog.source_type.set("kdocs")
            dialog.source_doc_title.set("Online source")
            dialog.local_sheets = ["Online sheet"]
            self.release.set()
            self.assertTrue(self.worker_done.wait(1))
            self.root.update()
            self.assertEqual(dialog.local_sheets, ["Online sheet"])
            self.assertEqual(dialog.source_doc_title.get(), "Online source")
            self.assertFalse(dialog._local_reader.pending)

    def test_close_during_local_read_cancels_callbacks(self):
        dialog = self.dialog()
        with mock.patch.object(self.module, "read_excel_sheet_names", side_effect=self.slow):
            dialog.local_file.set("slow.xlsx")
            dialog.refresh_local_sheets()
            dialog.destroy()
            self.release.set()
            self.assertTrue(self.worker_done.wait(1))
            self.root.update()
            self.assertIsNone(dialog._local_reader._after)
            self.assertFalse(dialog._local_reader.pending)

    def test_legacy_mapping_dialog_also_reads_in_background(self):
        dialog = self.dialog(mapping=True)
        with mock.patch.object(self.module, "read_excel_sheet_names", side_effect=self.slow):
            dialog.vars["local_file"].set("slow.xlsx")
            start = time.monotonic()
            dialog.refresh_local_sheets()
            self.assertLess(time.monotonic() - start, .2)
            self.release.set()
            self.pump(lambda: dialog.vars["source_sheet"].get() == "Old sheet")
            self.assertEqual(tuple(dialog.local_sheet_combo["values"]), ("Old sheet",))

    def test_invalid_metadata_reports_useful_error(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<invalid")
            with self.assertRaisesRegex(ValueError, "目录不完整或已损坏"):
                self.module.read_excel_sheet_names(path)
