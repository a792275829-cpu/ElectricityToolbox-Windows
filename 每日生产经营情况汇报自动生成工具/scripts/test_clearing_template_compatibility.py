"""清远计划电量动态行号兼容（旧 64 / 新 66）与审计字段回归。

对应 mac v0.1.32 的 test_clearing_template_compatibility.py，适配 Windows 扁平
toolbox_clearing_layout 模块与 generate_red_marked_report 的导入方式。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from generate_red_marked_report import load_day_ahead_clearing_summary
from toolbox_clearing_layout import brief_row_index, qingyuan_plan_values


def basic_info_rows(total_row: int = 64, *, with_total: bool = True):
    """按行号构造「基础信息」区（索引 0 对应第 1 行）。"""
    rows = [[None] * 30 for _ in range(total_row + 1)]
    rows[36][25] = "清远上网电量（MWh）"   # Z37
    rows[38][25] = "时刻"                   # Z39
    rows[38][26] = "计划\n发电量"           # AA39
    rows[38][28] = "计划\n发电量"           # AC39
    if with_total:
        rows[total_row - 1][25] = "合计"    # Z{total_row}
    rows[total_row - 1][26] = 0            # AA{total_row}
    rows[total_row - 1][28] = 2300        # AC{total_row}
    return rows


def clearing_workbook(path: Path, total_row: int) -> None:
    book = Workbook()
    basic = book.active
    basic.title = "基础信息"
    basic["Z37"] = "清远上网电量（MWh）"
    basic["Z39"] = "时刻"
    basic["AA39"] = basic["AC39"] = "计划\n发电量"
    basic.cell(total_row, 26, "合计")       # Z{total_row}
    basic.cell(total_row, 27, 0)            # AA{total_row}
    basic.cell(total_row, 29, 2300)        # AC{total_row}
    ahead = book.create_sheet("快报  (日前)")
    for row, label in ((7, "分公司"), (8, "汕头"), (13, "海门"), (19, "东莞")):
        ahead.cell(row, 1, label)
    for row, energy, price in ((7, 100, 400), (8, 20, 410)):
        ahead.cell(row, 6, energy)
        ahead.cell(row, 7, price)
    for row, label, energy, price in (
        (25, "汕头海风", 5, 300),
        (26, "鮀莲光伏", 3, 310),
        (27, "归湖光伏", 2, 320),
    ):
        ahead.cell(row, 1, label)
        ahead.cell(row, 7, energy)
        ahead.cell(row, 8, price)
    book.save(path)
    book.close()


class ClearingTemplateCompatibilityTests(unittest.TestCase):
    def test_plan_values_locate_old_and_new_total_rows(self):
        self.assertEqual(
            qingyuan_plan_values(basic_info_rows(64)),
            {"AA64": 0, "AC64": 2300},
        )
        self.assertEqual(
            qingyuan_plan_values(basic_info_rows(66)),
            {"AA66": 0, "AC66": 2300},
        )

    def test_missing_total_raises_key_error(self):
        with self.assertRaisesRegex(KeyError, "计划发电量合计行"):
            qingyuan_plan_values(basic_info_rows(66, with_total=False))

    def test_brief_row_index_maps_six_unit_rows(self):
        rows = [
            ["分公司"], ["汕头"], ["海门"], ["东莞"], ["清远"],
            ["", "#5机组"], ["", "#6机组"], ["新能源"], ["分公司"],
            ["汕头海风"], ["归湖光伏"], ["日前简析：略", ""],
        ]
        index = brief_row_index(rows)
        self.assertEqual(index["分公司火电"], 1)
        self.assertEqual(index["清远"], 5)
        self.assertEqual(index["清远#5机组"], 6)
        self.assertEqual(index["分公司新能源"], 9)

    def test_day_ahead_audit_cells_follow_template_row(self):
        for total_row in (64, 66):
            with self.subTest(total_row=total_row), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "出清情况（分公司2026.11.4).xlsx"
                clearing_workbook(path, total_row)
                summary = load_day_ahead_clearing_summary("2026-11-04", path)
                self.assertEqual(
                    summary["qingyuanSource"]["cells"],
                    {f"AA{total_row}": 0, f"AC{total_row}": 2300},
                )


if __name__ == "__main__":
    unittest.main()
