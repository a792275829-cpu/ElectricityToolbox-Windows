"""电量汇总「来源冲突」校验回归测试（对齐 mac v0.1.32 summarize_511_excel.py）。

同机组 + 同市场出现多份来源文件时必须报错，避免静默覆盖；
不同市场（日前/实时）互不冲突。并用 openpyxl 重新打开回读关键单元格。
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "电力工具脚本/电量汇总工具/summarize_511_excel.py"
SPEC = importlib.util.spec_from_file_location("summarize_511_excel_conflict", SCRIPT)
summary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)

PYTHON_BACKEND = mock.patch.dict(os.environ, {"TOOLBOX_DOMAIN_BACKEND": "python"})
NOOP = lambda message: None  # noqa: E731
ONE_UNIT = [("1号机", [], [])]


def make_station(root, filenames):
    station = root / "海门"
    station.mkdir(exist_ok=True)
    for name in filenames:
        (station / name).touch()
    return station


class SourceConflictTests(unittest.TestCase):
    def test_two_sources_same_unit_same_market_raise_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            station = make_station(Path(directory), [
                "A日前交易结果查询.xlsx",
                "B日前交易结果查询.xlsx",
            ])
            with mock.patch.object(summary, "parse_workbook", return_value=ONE_UNIT):
                with self.assertRaises(ValueError) as ctx:
                    summary._collect_station_units([station], NOOP)
            message = str(ctx.exception)
            self.assertIn("来源冲突", message)
            self.assertIn("请仅保留明确选定的一份来源后重试。", message)
            self.assertIn("日前电量", message)

    def test_same_unit_different_markets_do_not_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            station = make_station(Path(directory), [
                "日前交易结果查询.xlsx",
                "实时交易结果查询.xlsx",
            ])
            with mock.patch.object(summary, "parse_workbook", return_value=ONE_UNIT):
                units = summary._collect_station_units([station], NOOP)
            self.assertEqual(len(units), 1)
            self.assertEqual(set(units[0].values), {"dayahead", "realtime"})

    def test_single_source_and_workbook_roundtrip_readback(self):
        """单份来源正常汇总，写出的 Excel 可重新打开并回读关键单元格。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            station = make_station(root, ["日前交易结果查询.xlsx"])
            with mock.patch.object(summary, "parse_workbook", return_value=ONE_UNIT):
                units = summary._collect_station_units([station], NOOP)
            self.assertEqual(len(units), 1)
            self.assertEqual(set(units[0].values), {"dayahead"})

            cells = [summary.NumericCell(float(hour)) for hour in range(24)]
            units[0].values["dayahead"] = {"energy": cells, "price": cells}
            with PYTHON_BACKEND:
                output = summary.write_workbook(units, root / "汇总.xlsx", NOOP)
            reopened = load_workbook(output, data_only=True)
            try:
                self.assertIn("海门", reopened.sheetnames)
                values = [cell.value for sheet in reopened.worksheets
                          for row in sheet.iter_rows() for cell in row]
                self.assertIn("1号机", values)
                self.assertIn("日前数据", values)
                self.assertIn("实时数据", values)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
