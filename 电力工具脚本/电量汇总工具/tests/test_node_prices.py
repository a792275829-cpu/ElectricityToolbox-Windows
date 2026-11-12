"""节点电价真实结构解析、异常校验与汇总文件回读。"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

TOOLBOX_TEST_TAGS = ()
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "电力工具箱"))
from toolbox_node_prices import NODE_TIME_SLOTS, parse_node_prices, collect_node_prices
from toolbox.review_classification import prepare_review_folder
from toolbox.tasks import CancellationToken

SPEC = importlib.util.spec_from_file_location("summary_node_prices", ROOT / "电力工具脚本/电量汇总工具/summarize_511_excel.py")
summary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)


class NodePriceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        patch = mock.patch.dict(os.environ, {"TOOLBOX_DOMAIN_BACKEND": "python", "TOOLBOX_XLSX_BACKEND": "python"})
        patch.start()
        self.addCleanup(patch.stop)

    def source(self, market="日前", day="2026-11-12", bad=None, duplicate=False):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = f"{market}节点电价查询({day})"
        # Reverse columns to prove matching follows time labels.
        sheet.append(["节点名称", "数据项", *reversed(NODE_TIME_SLOTS)])
        for name, offset in (("甲海风升压站", 0), ("乙升压站", 10), ("丙海风", 20), ("其他", 999)):
            values = [str(i + offset - 10) for i in reversed(range(96))]
            if bad is not None and name == "乙升压站":
                values[0] = bad
            sheet.append([name, "电价(元/MWh)", *values])
        if duplicate:
            sheet.append(["丙海风", "电价(元/MWh)", *([0] * 96)])
        province = workbook.create_sheet(f"全省-{market}节点电价查询({day})")
        province.append(["节点名称", "数据项", *reversed(NODE_TIME_SLOTS)])
        province.append(["全省", "电价(元/MWh)", *[i - 50.25 for i in reversed(range(96))]])
        extra = workbook.create_sheet("Sheet1")
        extra.append([999] * 96)
        path = self.folder / f"{market}节点电价查询({day}).xlsx"
        workbook.save(path)
        workbook.close()
        return path

    def test_average_roundtrip_and_original_files_preserved(self):
        paths = [self.source(), self.source("实时", "2026-11-10")]
        originals = [p.read_bytes() for p in paths]
        prepare_review_folder(self.folder, logger=lambda _: None, token=CancellationToken(), template=self.folder / "不存在")
        unit = summary.UnitData("海门", "海门", "1号机")
        with mock.patch.object(summary, "collect_units", return_value=[unit]):
            output = summary.run_summary(self.folder, self.folder / "测试汇总.xlsx")
        workbook = load_workbook(output, data_only=True)
        try:
            self.assertEqual(workbook.sheetnames, ["海门", "海风汇总", "全省汇总"])
            sheet = workbook["海风汇总"]
            self.assertEqual(sheet["A1"].value, "海风日前2026-11-12")
            self.assertEqual(sheet["A3"].value, "海风实时2026-11-10")
            for row in (2, 4):
                self.assertEqual([sheet.cell(row, i + 1).value for i in range(96)], list(range(96)))
                self.assertEqual(sheet.cell(row, 1).number_format, "0.00")
            province = workbook["全省汇总"]
            self.assertEqual(province["A1"].value, "全省日前2026-11-12")
            self.assertEqual(province["A3"].value, "全省实时2026-11-10")
            for row in (2, 4):
                self.assertEqual([province.cell(row, i + 1).value for i in range(96)],
                                 [i - 50.25 for i in range(96)])
                self.assertEqual(province.cell(row, 1).number_format, "0.00")
        finally:
            workbook.close()
        self.assertEqual([p.read_bytes() for p in paths], originals)
        self.assertEqual(parse_node_prices(paths[0]).count, 3)

    def test_nodes_only_and_missing_market(self):
        self.source()
        output = summary.run_summary(self.folder, self.folder / "测试汇总.xlsx")
        workbook = load_workbook(output, data_only=True)
        self.assertEqual(workbook.sheetnames, ["海风汇总", "全省汇总"])
        self.assertIn("未提供", workbook.active["A3"].value)
        self.assertIsNone(workbook.active["A4"].value)
        self.assertIsNone(workbook["全省汇总"]["A4"].value)
        workbook.close()

    def test_invalid_price_duplicate_node_and_source_rejected(self):
        for bad in ("", "NaN", "不是数字"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "电价缺失或无效"):
                parse_node_prices(self.source(bad=bad))
        with self.assertRaisesRegex(ValueError, "节点重复"):
            parse_node_prices(self.source(duplicate=True))
        self.source()
        self.source(day="2026-11-13")
        with self.assertRaisesRegex(ValueError, "来源冲突"):
            collect_node_prices(self.folder, lambda _: None)

    def test_missing_time_and_date_mismatch_rejected(self):
        path = self.source()
        workbook = load_workbook(path)
        workbook.active["C1"] = "错误时点"
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValueError, "表头缺失或重复"):
            parse_node_prices(path)
        path = self.source()
        renamed = path.with_name("日前节点电价查询(2026-11-13).xlsx")
        path.rename(renamed)
        with self.assertRaisesRegex(ValueError, "日期不一致"):
            parse_node_prices(renamed)

    def test_province_missing_invalid_duplicate_or_wrong_date_rejected(self):
        for issue, message in (("missing", "第二个工作表"), ("invalid", "电价缺失或无效"),
                               ("duplicate", "节点重复"), ("date", "多个表格日期")):
            path = self.source()
            workbook = load_workbook(path)
            sheet = workbook.worksheets[1]
            if issue == "missing":
                sheet["A2"] = "其他"
            elif issue == "invalid":
                sheet["C2"] = None
            elif issue == "duplicate":
                sheet.append(["全省", "电价(元/MWh)", *([1] * 96)])
            else:
                sheet.title = "全省-日前节点电价查询(2026-11-13)"
            workbook.save(path)
            workbook.close()
            with self.subTest(issue=issue), self.assertRaisesRegex(ValueError, message):
                parse_node_prices(path)
