"""日报工作表识别：A4 日前简析为空时仍按收益模板识别（mac v0.1.32 口径）。

源出清表偶尔会把日前简析段落清空，但收益表格仍完整；识别应依据 A6/A2 日期、
分公司汇总行与日前出清 电量/电价 表头，而不是依赖 A4 简析或 max_column>=17。
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOL_DIR.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import openpyxl  # noqa: E402
from gd_power_report import company_clearing_xlsx as MODULE  # noqa: E402


def benefit_sheet(*, day_narrative: bool = True):
    """A6 日期 + 分公司汇总行 + 日前出清电量/电价表头；A4 简析可为空。"""
    sheet = openpyxl.Workbook().active
    sheet.title = MODULE.ENERGY_METRICS_SHEET_NAME
    sheet["A6"] = date(2026, 11, 8)
    sheet["B2"] = "日前出清"
    sheet.merge_cells("B2:C2")
    sheet["B3"] = "电量"
    sheet["C3"] = "电价"
    sheet["A5"] = "分公司火电"
    sheet["Q4"] = "占位"
    if day_narrative:
        sheet["A4"] = "日前市场简析：竞价空间回落。"
    return sheet


class BlankNarrativeRecognitionTests(unittest.TestCase):
    def test_empty_a4_still_selects_benefit_template(self):
        workbook = benefit_sheet(day_narrative=False).parent
        self.assertIsNone(workbook.active["A4"].value)
        self.assertTrue(MODULE._looks_like_report_sheet(workbook.active))
        self.assertEqual(
            MODULE._select_report_sheet(workbook).title,
            MODULE.ENERGY_METRICS_SHEET_NAME,
        )

    def test_missing_narrative_sheets_excludes_benefit_template(self):
        # 收益模板即使缺 A4 也已被识别，不应再被误报为「缺少简析段落」。
        workbook = benefit_sheet(day_narrative=False).parent
        self.assertEqual(MODULE._missing_narrative_sheets(workbook), [])

    def test_legacy_narrative_sheet_still_recognized(self):
        sheet = openpyxl.Workbook().active
        sheet.title = "快报 (1)"
        sheet["A4"] = "日前简析：\n统调负荷上涨。"
        sheet["Q4"] = "占位"
        sheet["A70"] = "二、实时市场简析"
        self.assertTrue(MODULE._looks_like_report_sheet(sheet))
        self.assertEqual(MODULE._select_report_sheet(sheet.parent).title, "快报 (1)")


if __name__ == "__main__":
    unittest.main()
