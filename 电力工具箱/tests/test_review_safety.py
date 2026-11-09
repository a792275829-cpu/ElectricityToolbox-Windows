"""Behaviour regressions for the November 9 module review; temporary inputs only."""
import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import Workbook
from toolbox.runtime import load_module

ROOT = Path(__file__).resolve().parents[2]


class FormulaSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("review_safe_wps", ROOT / "wps自动/wps_excel_to_kdocs_gui.py")

    def mapping(self, source, start="A1", end="A1"):
        return self.module.MappingRow("https://example.invalid/target", str(source),
            "数据", start, end, "目标", start, end)

    def workbook(self, path, cache=None, string_cache=False):
        book = Workbook()
        book.active.title = "数据"
        book.active["A1"] = '=IF(1=1,"",2)' if string_cache else "=1+1"
        book.active["C1"] = .125
        book.active["C1"].number_format = "0.0%"
        book.save(path)
        book.close()
        if cache is not None:
            replacement = path.with_suffix(".tmp")
            with ZipFile(path) as original, ZipFile(replacement, "w", ZIP_DEFLATED) as output:
                for info in original.infolist():
                    data = original.read(info.filename)
                    if info.filename == "xl/worksheets/sheet1.xml":
                        if string_cache:
                            data = data.replace(b'<c r="A1">', b'<c r="A1" t="str">')
                        data = data.replace(b"<v></v>", f"<v>{cache}</v>".encode(), 1)
                    output.writestr(info, data)
            replacement.replace(path)

    def test_missing_formula_cache_blocks_mapping_before_any_online_access(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.xlsx"
            self.workbook(path)
            before = path.read_bytes()
            online = self.module.MappingRow("https://example.invalid/target", "", "数据", "A1", "A1",
                "目标", "B1", "B1", source_type="kdocs", source_url="https://example.invalid/source")
            for mode in ("auto", "rust", "python"):
                with self.subTest(mode=mode), patch.dict(os.environ, TOOLBOX_XLSX_BACKEND=mode), patch.object(
                        self.module, "read_online_ranges") as read_online:
                    with self.assertRaisesRegex(ValueError, "公式缺少计算结果.*数据!A1"):
                        self.module.prepare_mappings([self.mapping(path), online])
                    read_online.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_numeric_formula_cache_and_real_blanks_keep_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.xlsx"
            self.workbook(path, cache="2")
            before = path.read_bytes()
            prepared = self.module.prepare_mappings([self.mapping(path, "A1", "C1")])
            self.assertEqual(prepared[0].values, [[2, "", .125]])
            self.assertEqual(path.read_bytes(), before)

    def test_cached_empty_string_formula_is_a_valid_blank(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.xlsx"
            self.workbook(path, cache="", string_cache=True)
            self.assertEqual(self.module.prepare_mappings([self.mapping(path)])[0].values, [[""]])

    def test_formula_outside_requested_regions_does_not_block_read(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.xlsx"
            self.workbook(path)
            self.assertEqual(self.module.read_excel_ranges(path, [("数据", "B1:C1")]), [([["", .125]], 1, 2)])


class SpotPriceUniquenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("review_safe_spot", ROOT / "现货价格更新/update_spot_prices.py")

    def parse(self, rows, skip=True):
        return self.module.parse_price_payload({"data": {"unitDateList": rows}}, unit_id="u",
            expected_dates=(date(2026, 11, 1),), label="测试电价", skip_incomplete=skip)

    def test_duplicate_complete_prices_are_rejected_in_both_orders_and_skip_modes(self):
        a = dict(unitId="u", date="2026-11-01", acPrice=[100] * 24)
        b = dict(a, acPrice=[900] * 24)
        for rows in ([a, b], [b, a], [a, a]):
            for skip in (True, False):
                with self.subTest(rows=rows, skip=skip), self.assertRaisesRegex(RuntimeError, "重复完整电价记录"):
                    self.parse(rows, skip)

    def test_other_units_and_incomplete_records_do_not_shadow_complete_prices(self):
        good = dict(unitId="u", date="2026-11-01", acPrice=[100] * 24)
        rows = [dict(good, acPrice=[900] * 23), good, dict(good, unitId="other")]
        self.assertEqual(self.parse(rows), {date(2026, 11, 1): (100.,) * 24})


class TradingBatchSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("review_safe_trade", ROOT / "电力工具脚本/电力交易分析工具/generate_electricity_report.py")

    def test_cli_and_both_gui_entries_reject_collisions_before_starting(self):
        from toolbox import pages
        for name in ("daily.xlsx", "DAILY.xlsx"):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                root = Path(directory)
                sources = [root / "甲/daily.xlsx", root / "乙" / name]
                output = root / "output"
                with patch.object(self.module, "generate_report") as render:
                    with self.assertRaisesRegex(ValueError, "输出文件名冲突"):
                        self.module.generate_batch(sources, output)
                    render.assert_not_called()
                fake = SimpleNamespace(files=sources, output_dir=Mock(get=Mock(return_value=str(output))),
                    run_in_thread=Mock(), clear_log=Mock(), run_button=Mock(), status=Mock(), append_log=Mock())
                with patch.object(pages.messagebox, "showerror") as error:
                    pages.TradeAnalysisPage.start_analysis(fake)
                    error.assert_called_once()
                    fake.run_in_thread.assert_not_called()
                with patch.object(self.module.messagebox, "showerror") as error, patch.object(
                        self.module.threading, "Thread") as thread:
                    self.module.ReportApp.run_batch(fake)
                    error.assert_called_once()
                    thread.assert_not_called()
                self.assertFalse(output.exists())

    def test_distinct_sources_produce_distinct_reports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "one.xlsx", root / "two.xlsx"]
            def render(source, *, out_path):
                out_path.write_text(source.name, encoding="utf-8")
                return out_path
            with patch.object(self.module, "generate_report", side_effect=render):
                outputs = self.module.generate_batch(sources, root / "out")
            self.assertEqual([p.read_text(encoding="utf-8") for p in outputs], ["one.xlsx", "two.xlsx"])

    def test_independent_gui_keeps_partial_completion_for_noncolliding_inputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake = SimpleNamespace(append_log=Mock(), _finish_batch=Mock(),
                root=SimpleNamespace(after=lambda _, callback, *args: callback(*args)))
            def render(source, *, out_path):
                if source.stem == "bad":
                    raise ValueError("invalid workbook")
                out_path.write_text("complete", encoding="utf-8")
            with patch.object(self.module, "generate_report", side_effect=render):
                self.module.ReportApp._run_batch_worker(fake, [root / "bad.xlsx", root / "good.xlsx"], root / "out")
            success, errors = fake._finish_batch.call_args.args
            self.assertEqual(success, [root / "out/good_分析报告.html"])
            self.assertEqual(len(errors), 1)
            self.assertEqual(success[0].read_text(encoding="utf-8"), "complete")
