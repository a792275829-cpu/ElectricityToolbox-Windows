from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path

from openpyxl import Workbook

TOOLBOX_TEST_TAGS = ()
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "南方分公司电力市场日报"))

from gd_power_report import company_clearing_xlsx as clearing
from gd_power_report import pipeline
from gd_power_report.legacy_concise_report import render_legacy_concise_report
from gd_power_report.legacy_print_report import render_print_report
import test_market_report_optimizations as helpers


class MarketPriceComparisonTests(unittest.TestCase):
    def test_narrative_price_and_change_variants(self):
        for change, expected in (
            ("环比上涨22.57元/兆瓦时", 22.57),
            ("环比\n下降 22.57 元/兆瓦时", -22.57),
            ("较昨日涨22.57元/MWh", 22.57),
            ("较上日降低22.57元／兆瓦时", -22.57),
            ("较前日回落22.57元/兆瓦时", -22.57),
            ("环比基本持平", 0),
        ):
            with self.subTest(change=change):
                card = clearing._market_average_price_card(
                    f"全省日前出清均价为 -5.25 元/兆瓦时，{change}。"
                )
                self.assertEqual(card["value"], -5.25)
                self.assertEqual(card["delta"], expected)
                self.assertEqual(card["delta_prefix"], "环比")

    def test_no_change_does_not_borrow_another_metrics_delta(self):
        card = clearing._market_average_price_card(
            "全省日前均价350元/兆瓦时。机组报价环比上涨20元/兆瓦时。"
        )
        self.assertEqual(card["value"], 350)
        self.assertIsNone(card["delta"])

    def test_other_metric_in_same_sentence_stays_out_of_price_delta(self):
        for text in (
            "全省日前均价350元/兆瓦时，机组报价环比上涨20元/兆瓦时。",
            "全省日前均价350元/兆瓦时，负荷环比下降20元/兆瓦时。",
        ):
            with self.subTest(text=text):
                card = clearing._market_average_price_card(text)
                self.assertEqual(card["value"], 350)
                self.assertIsNone(card["delta"])
                self.assertNotIn("delta_prefix", card)

    def test_price_delta_takes_the_comparison_written_first(self):
        card = clearing._market_average_price_card(
            "全省日前均价350元/兆瓦时，较昨日涨22.57元/MWh，机组报价环比上涨20元/兆瓦时。"
        )
        self.assertEqual(card["delta"], 22.57)
        self.assertEqual(card["delta_prefix"], "环比")

    def test_recap_narrative_from_another_date_is_dropped(self):
        workbook = Workbook()
        stale = workbook.active
        stale.title = "新快报"
        stale["A2"] = date(2026, 10, 5)
        stale["A4"] = "日前简析：全省日前均价900元/兆瓦时。"
        target = date(2026, 10, 6)
        self.assertEqual(clearing._date_matched_day_narrative(workbook, target, stale), "")
        self.assertIsNone(
            clearing._market_average_price_card(
                clearing._date_matched_day_narrative(workbook, target, stale)
            )["value"]
        )
        # 日期一致或工作表没有日期时仍沿用备用简析。
        stale["A2"] = target
        self.assertIn(
            "900元/兆瓦时",
            clearing._date_matched_day_narrative(workbook, target, stale),
        )
        del stale["A2"]
        self.assertIn(
            "900元/兆瓦时",
            clearing._date_matched_day_narrative(workbook, target, stale),
        )
        # 快报按次日生成的日期不算滞后，仍沿用备用简析。
        stale["A2"] = date(2026, 10, 7)
        self.assertIn(
            "900元/兆瓦时",
            clearing._date_matched_day_narrative(workbook, target, stale),
        )
        workbook.close()

    def test_comparison_workbook_reads_date_matched_market_price(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.xlsx"
            workbook = Workbook()
            company = workbook.active
            company.title = "分公司"
            company["A2"] = date(2026, 10, 6)
            company["A5"] = "分公司火电"
            company["B2"], company["B3"], company["B5"] = "日前出清", "电量", 100
            recap = workbook.create_sheet("新快报")
            recap["A4"] = "日前简析：全省日前均价999元/兆瓦时。"
            for day, price in ((6, 300), (5, 900)):
                sheet = workbook.create_sheet(f"快报{day}")
                sheet["A40"] = date(2026, 10, day)
                sheet["A41"] = f"日前简析：全省日前均价{price}元/兆瓦时。"
            workbook.save(path)
            workbook.close()
            parsed = clearing.parse_day_ahead_comparison_workbook(path)
            self.assertEqual(parsed["report_date"], "2026-10-06")
            self.assertEqual(parsed["market_cards"][0]["value"], 300)

    def test_pipeline_keeps_price_comparison_through_website_merge_and_both_renderers(self):
        for current, previous, expected in ((350, 300, 50), (250, 300, -50), (0, 0, 0), (350, None, 7)):
            with self.subTest(current=current, previous=previous), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                helper = helpers.ReportInputAndErrorTests()
                _, _, collect, render = helper.inputs(stack)
                original_reports = list(pipeline.parse_daily_report_workbook.side_effect)
                original_reports[0]["market"]["day_ahead"] = [
                    {"label": "全省日前均价", "value": current, "unit": "元/兆瓦时", "delta": 7}
                ]
                pipeline.parse_daily_report_workbook.side_effect = original_reports
                pipeline.parse_day_ahead_comparison_workbook.return_value["market_cards"] = [
                    {"label": "全省日前均价", "value": previous, "pending": previous is None}
                ]
                collect.return_value = {
                    f"market_{period}_cards": [{"label": label, "value": 100,
                        "thermal_breakdown": {"coal_delta": 1, "gas_delta": 2}}]
                    for period, label in (("day_ahead", "市场开机"), ("realtime", "在线机组数"))
                }

                def export(folder, report, progress):
                    price = next(c for c in report["market"]["day_ahead"] if c["label"] == "全省日前均价")
                    self.assertEqual(price["delta"], expected)
                    # Render actual templates and read back the comparison markup.
                    display_report = copy.deepcopy(report)
                    display_report["appendices"] = {period: {"rows": []} for period in ("day_ahead", "realtime")}
                    for renderer in (render_legacy_concise_report, render_print_report):
                        html = renderer(folder, display_report).read_text(encoding="utf-8")
                        self.assertIn("全省日前均价", html)
                        expected_text = f"环比 {'▲ ' if expected > 0 else '▼ ' if expected < 0 else ''}{abs(expected):g}"
                        self.assertTrue(expected_text in html, f"{renderer.__name__} 缺少 {expected_text}")
                    phone, printing = folder / "phone.pdf", folder / "print.pdf"
                    helpers.write_pdf(phone)
                    helpers.write_pdf(printing)
                    return folder / "phone.html", phone, printing

                render.side_effect = export
                helper.run_report(directory)
                render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
