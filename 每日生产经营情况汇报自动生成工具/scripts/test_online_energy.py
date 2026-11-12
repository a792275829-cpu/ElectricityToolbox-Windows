from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from docx.shared import RGBColor

from generate_red_marked_report import (
    build_online_energy_text,
    build_online_pending_segments,
    load_online_energy_summary,
    load_online_price_summary,
    parse_float,
    replace_paragraph_segments,
)


def _build_workbook(path: Path, *, fire: tuple[float, float, float], qingyuan: float,
                    offshore: float, tuolian: float, guihu: float,
                    prices: tuple[float, float, float] = (1629, 3479, 847)) -> None:
    workbook = Workbook()
    brief = workbook.active
    brief.title = "快报"
    shantou, haimen, dongguan = fire
    total = shantou + haimen + dongguan + qingyuan
    for row, energy in ((7, total), (8, shantou), (13, haimen), (19, dongguan), (25, qingyuan)):
        brief.cell(row, 1, {7: "分公司", 8: "汕头", 13: "海门", 19: "东莞", 25: "清远"}[row])
        if row != 7:
            brief.cell(row, 2, "合计")
        brief.cell(row, 10, energy)
    brief.cell(28, 1, "新能源")
    for row, label, energy in (
        (31, "分公司", offshore + tuolian + guihu),
        (32, "汕头海风", offshore),
        (33, "鮀莲光伏", tuolian),
        (34, "归湖光伏", guihu),
    ):
        brief.cell(row, 1, label)
        brief.cell(row, 11, energy)

    day_ahead = workbook.create_sheet("快报  (日前)")
    for row, label in ((7, "分公司"), (8, "汕头"), (13, "海门"), (19, "东莞")):
        day_ahead.cell(row, 1, label)
    day_ahead.cell(7, 7, 300)
    for row, price in ((8, prices[0]), (13, prices[1]), (19, prices[2])):
        day_ahead.cell(row, 7, price)
    for row, label, price in ((32, "汕头海风", 676), (33, "鮀莲光伏", 598), (34, "归湖光伏", 576)):
        day_ahead.cell(row, 1, label)
        day_ahead.cell(row, 8, price)
    workbook.save(path)


class OnlineEnergyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "出清情况（分公司2026.11.4）.xlsx"

    def test_splits_fire_renewable_and_qingyuan_from_brief(self) -> None:
        # 11.4 实际值：火电三厂 1629.02/3478.60/847.48、清远 230.57、
        # 新能源 348.70/32.54/18.48，总数 6585.39。
        _build_workbook(self.path, fire=(1629.02, 3478.60, 847.48), qingyuan=230.571,
                        offshore=348.70, tuolian=32.54, guihu=18.48)
        energy = load_online_energy_summary("2026-11-04", self.path)

        self.assertAlmostEqual(energy["companies"]["汕头"], 1629.02)
        self.assertAlmostEqual(energy["companies"]["清远"], 230.571)
        self.assertAlmostEqual(energy["companies"]["光伏"], 51.02)
        self.assertAlmostEqual(energy["renewableEnergy"], 399.72)
        self.assertAlmostEqual(energy["total"], 6585.391, places=3)

    def test_text_reports_every_unit_and_price(self) -> None:
        _build_workbook(self.path, fire=(1629.02, 3478.60, 847.48), qingyuan=230.571,
                        offshore=348.70, tuolian=32.54, guihu=18.48)
        energy = load_online_energy_summary("2026-11-04", self.path)
        prices = load_online_price_summary("2026-11-04", self.path)

        text = build_online_energy_text("2026-11-04", energy, prices)

        self.assertTrue(text.startswith("11月4日分公司上网电量6585万千瓦时，其中，汕头1629万、"
                                        "海门3479万、东莞847万、海上风电349万、光伏51万千瓦时、"
                                        "清远231万千瓦时；"))
        self.assertIn("分公司日前均价300厘/千瓦时，其中汕头1629厘、海门3479厘、东莞847厘、"
                      "海上风电676厘、鮀莲598厘、归湖576厘/千瓦时。", text)

    def test_zero_realtime_sheet_yields_zero_total(self) -> None:
        # 报告日当天的工作簿上网电量列尚未出数，整列为 0，调用方据此跳过。
        _build_workbook(self.path, fire=(0, 0, 0), qingyuan=0, offshore=0, tuolian=0, guihu=0)
        energy = load_online_energy_summary("2026-11-05", self.path)

        self.assertEqual(energy["total"], 0)

    def test_pending_segments_keep_energy_text_and_update_price(self) -> None:
        # 电量未出数（「快报」J 列为 0）时，电量句原样保留模板文字并标红（留人工填数，
        # 只换段首日期），均价句仍按「快报  (日前)」更新为黑字，不再整段冻结在模板旧值。
        _build_workbook(self.path, fire=(0, 0, 0), qingyuan=0, offshore=0, tuolian=0, guihu=0)
        energy = load_online_energy_summary("2026-11-09", self.path)
        prices = load_online_price_summary("2026-11-09", self.path)
        template_text = (
            "11月3日分公司上网电量6228万千瓦时，其中，汕头1279万、海门3449万、东莞970万、"
            "海上风电207万、光伏95万千瓦时、清远230万千瓦时；分公司日前均价160厘/千瓦时，"
            "其中汕头524厘、海门-35厘、东莞737厘、海上风电64厘、鮀莲622厘、归湖490厘/千瓦时。 "
        )

        self.assertEqual(energy["total"], 0)
        segments = build_online_pending_segments(template_text, "2026-11-09", prices)

        self.assertEqual([red for _, red in segments], [True, False])
        self.assertEqual(
            segments[0][0],
            "11月9日分公司上网电量6228万千瓦时，其中，汕头1279万、海门3449万、东莞970万、"
            "海上风电207万、光伏95万千瓦时、清远230万千瓦时；",
        )
        self.assertEqual(
            segments[1][0],
            "分公司日前均价300厘/千瓦时，其中汕头1629厘、海门3479厘、东莞847厘、"
            "海上风电676厘、鮀莲598厘、归湖576厘/千瓦时。",
        )

    def test_pending_energy_sentence_keeps_inner_separators(self) -> None:
        # 电量句内部若再出现分号，按「分公司日前均价」定位均价句，不能把电量句截断。
        prices = {
            "averagePrice": 45.08,
            "companies": {
                "汕头": 409.57, "海门": -50.0, "东莞": 434.26,
                "海上风电": 407.23, "鮀莲": 371.39, "归湖": 372.41,
            },
        }
        template_text = (
            "11月3日分公司上网电量6228万千瓦时；汕头1279万、海门3449万；东莞970万千瓦时；"
            "分公司日前均价160厘/千瓦时，其中汕头524厘、归湖490厘/千瓦时。"
        )

        segments = build_online_pending_segments(template_text, "2026-11-09", prices)

        self.assertEqual([red for _, red in segments], [True, False])
        self.assertEqual(
            segments[0][0],
            "11月9日分公司上网电量6228万千瓦时；汕头1279万、海门3449万；东莞970万千瓦时；",
        )
        self.assertTrue(segments[1][0].startswith("分公司日前均价45厘/千瓦时"))

    def test_pending_energy_sentence_without_separator_gains_one(self) -> None:
        prices = {
            "averagePrice": 45.08,
            "companies": {
                "汕头": 409.57, "海门": -50.0, "东莞": 434.26,
                "海上风电": 407.23, "鮀莲": 371.39, "归湖": 372.41,
            },
        }

        segments = build_online_pending_segments(
            "11月3日分公司上网电量6228万千瓦时", "2026-11-09", prices
        )

        self.assertEqual(segments[0], ("11月9日分公司上网电量6228万千瓦时；", True))
        self.assertEqual(segments[1][1], False)

    def test_pending_paragraph_marks_only_energy_red(self) -> None:
        from docx import Document

        prices = {
            "averagePrice": 45.08,
            "companies": {
                "汕头": 409.57, "海门": -50.0, "东莞": 434.26,
                "海上风电": 407.23, "鮀莲": 371.39, "归湖": 372.41,
            },
        }
        template_text = "11月3日分公司上网电量6228万千瓦时；分公司日前均价160厘/千瓦时。"
        document = Document()
        paragraph = document.add_paragraph(template_text)
        paragraph.runs[0].font.color.rgb = RGBColor(0x00, 0x00, 0x00)

        replace_paragraph_segments(
            paragraph,
            build_online_pending_segments(template_text, "2026-11-09", prices),
        )

        self.assertEqual(
            [(run.text, str(run.font.color.rgb)) for run in paragraph.runs],
            [
                ("11月9日分公司上网电量6228万千瓦时；", "FF0000"),
                (
                    "分公司日前均价45厘/千瓦时，其中汕头410厘、海门-50厘、东莞434厘、"
                    "海上风电407厘、鮀莲371厘、归湖372厘/千瓦时。",
                    "000000",
                ),
            ],
        )

    def test_placeholders_are_blank_not_invalid(self) -> None:
        # 停机、未投产、计划口径的单元格写作斜杠／横线，与空白同义。
        for value in (None, "", " ", "/", "\uff0f", "-", "\u2014", "\u2013"):
            self.assertEqual(parse_float(value), 0.0, repr(value))
        self.assertEqual(parse_float("12.5"), 12.5)

    def test_shifted_fire_rows_follow_labels_and_slash_cells(self) -> None:
        # 现网模板在海门增加了 #5／#6 机组，东莞整体下移到第 21 行，
        # 分公司合计公式也随之变成 =J8+J13+J21+J27；按标签定位才能读到东莞。
        workbook = Workbook()
        brief = workbook.active
        brief.title = "快报"
        for row, label, energy in (
            (7, "分公司", 300.0), (8, "汕头", 100.0), (13, "海门", 150.0), (21, "东莞", 50.0),
        ):
            brief.cell(row, 1, label)
            brief.cell(row, 10, energy)
        for row, unit in ((18, "#5机组"), (19, "#6机组")):
            brief.cell(row, 2, unit)
            brief.cell(row, 6, "/")
            brief.cell(row, 7, "/")
        brief.cell(28, 1, "新能源")
        for row, label, energy in (
            (32, "汕头海风", 10.0), (33, "鮀莲光伏", 2.0), (34, "归湖光伏", 3.0),
        ):
            brief.cell(row, 1, label)
            brief.cell(row, 11, energy)

        day_ahead = workbook.create_sheet("快报  (日前)")
        for row, label, price in (
            (7, "分公司", 300), (8, "汕头", 310), (13, "海门", 320), (21, "东莞", 330),
        ):
            day_ahead.cell(row, 1, label)
            day_ahead.cell(row, 7, price)
        day_ahead.cell(19, 7, "/")
        for row, label, price in (
            (32, "汕头海风", 676), (33, "鮀莲光伏", 598), (34, "归湖光伏", 576),
        ):
            day_ahead.cell(row, 1, label)
            day_ahead.cell(row, 8, price)
        workbook.save(self.path)

        energy = load_online_energy_summary("2026-11-07", self.path)
        prices = load_online_price_summary("2026-11-07", self.path)

        self.assertAlmostEqual(energy["companies"]["东莞"], 50.0)
        self.assertAlmostEqual(energy["companies"]["清远"], 0.0)
        self.assertAlmostEqual(prices["companies"]["东莞"], 330)
        self.assertAlmostEqual(prices["companies"]["汕头"], 310)


if __name__ == "__main__":
    unittest.main()
