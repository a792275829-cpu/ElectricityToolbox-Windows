"""Offline coverage for the unit-level realtime recap reader.

The 海门 5、6 号机 rows added to the clearing recap also widen the plant's unit
fleet. These tests pin that the reader keeps working while the source workbook
has not yet published the matching per-unit blocks, and that the realtime recap
date is no longer read from a fixed row.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOL_DIR.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import openpyxl  # noqa: E402

from gd_power_report import realtime_unit_xlsx as MODULE  # noqa: E402

TARGET = date(2026, 11, 8)


def unit_block(sheet, base_row: int, unit: str) -> None:
    sheet.cell(base_row, 1, unit)
    for column, label in enumerate(("时刻", "日前出清", "实时出清", "实际上网电量"), 1):
        sheet.cell(base_row + 1, column, label)
    for hour in range(24):
        row = base_row + 3 + hour
        sheet.cell(row, 1, time(hour))
        for column in range(2, 5):
            sheet.cell(row, column, (hour + column) * 0.125)


def unit_workbook(*, extra_haimen_units: tuple[str, ...] = ()) -> "openpyxl.Workbook":
    workbook = openpyxl.Workbook()
    workbook.active.title = "基础信息"
    workbook.active["A2"] = TARGET
    for plant, units in MODULE.EXPECTED_UNITS.items():
        sheet = workbook.create_sheet(plant)
        for index, unit in enumerate(units):
            unit_block(sheet, 1 + index * 30, unit)
    if extra_haimen_units:
        sheet = workbook["海门"]
        start = 1 + len(MODULE.EXPECTED_UNITS["海门"]) * 30
        for index, unit in enumerate(extra_haimen_units):
            unit_block(sheet, start + index * 30, unit)
    return workbook


class RealtimeDateTests(unittest.TestCase):
    def test_recap_date_is_read_from_the_title_not_a_fixed_row(self):
        sheet = openpyxl.Workbook().active
        sheet.title = "新"
        sheet["A6"] = datetime(2026, 11, 8)
        sheet["A6"].number_format = '"分公司日前出清复盘（"yyyy/m/d")"'
        # 日前复盘增加两行后，实时复盘从第 68 行下移到第 74 行；A68 现在是一整点。
        sheet["A68"] = "22:00"
        sheet["A74"] = datetime(2026, 11, 8)
        sheet["A74"].number_format = '"分公司实时出清复盘（"yyyy/m/d")"'
        self.assertEqual(MODULE._realtime_date(sheet.parent, TARGET), TARGET)

    def test_mismatched_recap_date_still_fails(self):
        sheet = openpyxl.Workbook().active
        sheet.title = "新"
        sheet["A74"] = datetime(2026, 11, 7)
        sheet["A74"].number_format = '"分公司实时出清复盘（"yyyy/m/d")"'
        with self.assertRaisesRegex(ValueError, "应为2026-11-08"):
            MODULE._realtime_date(sheet.parent, TARGET)


class OptionalUnitBlockTests(unittest.TestCase):
    def parse(self, workbook):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "units.xlsx"
            workbook.save(path)
            return MODULE.parse_realtime_unit_workbook(path, TARGET)

    def test_missing_new_units_do_not_break_the_existing_fleet(self):
        parsed = self.parse(unit_workbook())
        haimen = next(p for p in parsed["plants"] if p["plant"] == "海门")
        self.assertEqual(
            [unit["unit"] for unit in haimen["units"]],
            ["#1机组", "#2机组", "#3机组", "#4机组"],
        )

    def test_new_unit_blocks_are_picked_up_when_present(self):
        parsed = self.parse(unit_workbook(extra_haimen_units=("5号机", "6号机")))
        haimen = next(p for p in parsed["plants"] if p["plant"] == "海门")
        self.assertEqual(
            [unit["unit"] for unit in haimen["units"]],
            ["#1机组", "#2机组", "#3机组", "#4机组", "#5机组", "#6机组"],
        )

    def test_unknown_unit_block_is_still_rejected(self):
        workbook = unit_workbook()
        unit_block(workbook["汕头"], 91, "9号机")
        with self.assertRaisesRegex(ValueError, "未识别机组块"):
            self.parse(workbook)


if __name__ == "__main__":
    unittest.main()
