"""海门 #1~#6 六机回归测试（对齐 mac v0.1.32）。

覆盖：
- generate_electricity_report.py（Windows 内联 + native 计算链）识别/汇总 5、6 号机；
- 扁平共享模块 toolbox_trade_report.py（toolbox_shared/trade_report.py 的移植）六机逻辑；
- 新版「南方分公司」汇总页 5/6 号机动态纳入与快报缺行豁免；
- openpyxl 写出后重新打开回读关键单元格。
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "电力工具脚本/电力交易分析工具/generate_electricity_report.py"
SPEC = importlib.util.spec_from_file_location("generate_electricity_report_six", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

import toolbox_trade_report as SHARED  # noqa: E402

PYTHON_BACKEND = mock.patch.dict(os.environ, {"TOOLBOX_DOMAIN_BACKEND": "python"})


def six_unit_hourly_workbook():
    """海门逐时明细含 5、6 号机两个垂直块。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "海门"
    for index, number in enumerate((5, 6)):
        start = 1 + index * 30
        sheet.cell(start, 1, f"{number}号机")
        for hour in range(24):
            row = start + 3 + hour
            sheet.cell(row, 1, f"{hour}:00")
            for column in (2, 3, 5, 6, 7, 8, 10):
                sheet.cell(row, column, number * 10)
    sheet.cell(62, 1, "末尾")
    return book


def southern_summary_workbook():
    """南方分公司新版汇总页 + 快报页（含海门 #5/#6 可选机组）。"""
    book = Workbook()
    summary = book.active
    summary.title = "新"
    brief = book.create_sheet("快报")
    summary["A8"] = "南方分公司"
    summary["D8"] = "中长期"
    summary["G8"] = "日前出清"
    labels = {
        10: ("火电机组合计", None),
        13: ("汕头", "合计"), 14: (None, "#1机组"),
        15: (None, "#2机组"), 16: (None, "#3机组"),
        18: ("海门", "合计"), 19: (None, "#1机组"),
        20: (None, "#2机组"), 21: (None, "#3机组"),
        22: (None, "#4机组"), 23: (None, "#5机组"), 24: (None, "#6机组"),
        26: ("东莞", "合计"), 28: ("新能源", None),
    }
    for row, (plant, unit) in labels.items():
        if plant:
            summary.cell(row, 1, plant)
        if unit:
            summary.cell(row, 2, unit)
        for column, value in ((3, 300 + row), (4, row), (7, row * 2),
                              (8, 400 + row), (9, row), (10, 500 + row), (11, row / 10)):
            summary.cell(row, column, value)
    brief_labels = {
        7: ("分公司", None), 8: ("汕头", "合计"),
        9: (None, "#1机组"), 10: (None, "#2机组"), 11: (None, "#3机组"),
        13: ("海门", "合计"), 14: (None, "#1机组"),
        15: (None, "#2机组"), 16: (None, "#3机组"), 17: (None, "#4机组"),
        18: (None, "#5机组"), 19: (None, "#6机组"),
        20: ("东莞", "合计"),
    }
    for row, (plant, unit) in brief_labels.items():
        if plant:
            brief.cell(row, 1, plant)
        if unit:
            brief.cell(row, 2, unit)
        brief.cell(row, 3, 100 + row)
        brief.cell(row, 11, 0.5)
    return book


class SharedModuleSixUnitTests(unittest.TestCase):
    def test_flat_module_exposes_six_haimen_units(self):
        self.assertEqual(
            SHARED.HAIMEN_UNIT_NAMES,
            tuple(f"海门#{number}机组" for number in range(1, 7)),
        )
        self.assertTrue(SHARED.is_optional_haimen_unit("海门#5机组"))
        self.assertTrue(SHARED.is_optional_haimen_unit("海门#6机组"))
        self.assertFalse(SHARED.is_optional_haimen_unit("海门#4机组"))

    def test_optional_haimen_hourly_blocks(self):
        book = six_unit_hourly_workbook()
        builder = SHARED.ReportBuilder("unused.xlsx")
        builder.wb = book
        with PYTHON_BACKEND:
            blocks = builder._parse_blocks("海门", "海门")
        self.assertEqual(set(blocks), {"海门#5机组", "海门#6机组"})
        self.assertEqual(len(blocks["海门#6机组"]), 24)
        self.assertEqual(blocks["海门#5机组"][0]["da"], 50)
        book.close()

    def test_southern_summary_dynamic_six_units_and_missing_brief_exemption(self):
        book = southern_summary_workbook()
        builder = SHARED.ReportBuilder("unused.xlsx")
        builder.wb = book
        with PYTHON_BACKEND:
            builder._parse_summary()
        self.assertEqual(len(builder.companies["海门"]), 6)
        self.assertIn("海门#5机组", builder.summary)
        self.assertIn("海门#6机组", builder.summary)
        self.assertEqual(builder.summary["海门#5机组"]["ml_mwh"], 230)
        self.assertEqual(builder.summary["海门#6机组"]["capacity_10mw"], 119)
        # 快报缺 #6 标签时 5/6 号机豁免，不报错，仅容量/负荷率留空。
        book["快报"]["B19"] = None
        with PYTHON_BACKEND:
            builder._parse_summary()
        self.assertIsNone(builder.summary["海门#6机组"]["capacity_10mw"])
        self.assertEqual(builder.summary["海门#6机组"]["profit_wanyuan"], 2.4)
        book.close()


class GenerateReportSixUnitTests(unittest.TestCase):
    def test_companies_default_to_six_haimen_units(self):
        builder = MODULE.ReportBuilder("unused.xlsx")
        self.assertEqual(builder.companies["海门"], list(MODULE.HAIMEN_UNIT_NAMES))
        self.assertEqual(len(builder.companies["海门"]), 6)
        self.assertTrue(MODULE.is_optional_haimen_unit("海门#5机组"))
        self.assertFalse(MODULE.is_optional_haimen_unit("汕头#1机组"))

    def test_optional_haimen_hourly_blocks(self):
        book = six_unit_hourly_workbook()
        builder = MODULE.ReportBuilder("unused.xlsx")
        builder.wb = book
        with PYTHON_BACKEND:
            blocks = builder._parse_blocks("海门", "海门")
        self.assertEqual(set(blocks), {"海门#5机组", "海门#6机组"})
        self.assertEqual(len(blocks["海门#6机组"]), 24)
        self.assertEqual(blocks["海门#5机组"][0]["da"], 50)
        book.close()

    def test_southern_summary_dynamic_six_units_and_missing_brief_exemption(self):
        book = southern_summary_workbook()
        builder = MODULE.ReportBuilder("unused.xlsx")
        builder.wb = book
        with PYTHON_BACKEND:
            builder._parse_summary()
        self.assertEqual(len(builder.companies["海门"]), 6)
        self.assertIn("海门#5机组", builder.summary)
        self.assertIn("海门#6机组", builder.summary)
        self.assertEqual(builder.summary["海门#5机组"]["ml_mwh"], 230)
        self.assertEqual(builder.summary["海门#6机组"]["capacity_10mw"], 119)
        book["快报"]["B19"] = None
        with PYTHON_BACKEND:
            builder._parse_summary()
        self.assertIsNone(builder.summary["海门#6机组"]["capacity_10mw"])
        self.assertEqual(builder.summary["海门#6机组"]["profit_wanyuan"], 2.4)
        # 固定四台机组仍为必填标签。
        book["新"]["B22"] = None
        with PYTHON_BACKEND, self.assertRaisesRegex(ValueError, "缺少标签.*海门#4机组"):
            builder._parse_summary()
        book.close()

    def test_workbook_roundtrip_reads_back_key_cells(self):
        """openpyxl 写出后重新打开回读关键单元格。"""
        book = southern_summary_workbook()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "六机汇总.xlsx"
            book.save(path)
            reopened = load_workbook(path, data_only=True)
            try:
                self.assertEqual(reopened["新"]["A8"].value, "南方分公司")
                self.assertEqual(reopened["新"]["B23"].value, "#5机组")
                self.assertEqual(reopened["新"]["B24"].value, "#6机组")
                self.assertEqual(reopened["快报"]["B19"].value, "#6机组")
            finally:
                reopened.close()
        book.close()


class ShiftedBriefRowTests(unittest.TestCase):
    def test_shifted_dongguan_capacity_and_rate_in_python_and_rust(self):
        for backend in ("python", "rust"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as folder:
                book = southern_summary_workbook()
                book["快报"]["A20"] = " 东 莞 "
                book["快报"]["K19"], book["快报"]["K20"] = 0.61, 0.72
                book["快报"]["K20"].number_format = "0.00%"
                path = Path(folder) / "six.xlsx"
                book.save(path)
                book.close()
                book = load_workbook(path)
                try:
                    builder = MODULE.ReportBuilder(path)
                    builder.wb = book
                    with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                        builder._parse_summary()
                    self.assertEqual(builder.summary["东莞"]["capacity_10mw"], 120)
                    self.assertEqual(builder.summary["东莞"]["load_rate"], 0.72)
                    self.assertEqual(builder.summary["海门#6机组"]["capacity_10mw"], 119)
                    self.assertEqual(book["快报"]["K20"].number_format, "0.00%")
                    book["快报"]["B19"] = None
                    with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                        builder._parse_summary()
                    self.assertIsNone(builder.summary["海门#6机组"]["capacity_10mw"])
                    book["快报"]["A21"] = "东莞"
                    with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                        with self.assertRaisesRegex((ValueError, RuntimeError), "重复标签.*东莞"):
                            builder._parse_summary()
                    book["快报"]["A21"] = None
                    book["快报"]["A20"] = None
                    with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                        with self.assertRaisesRegex((ValueError, RuntimeError), "缺少标签.*东莞"):
                            builder._parse_summary()
                finally:
                    book.close()


if __name__ == "__main__":
    unittest.main()
