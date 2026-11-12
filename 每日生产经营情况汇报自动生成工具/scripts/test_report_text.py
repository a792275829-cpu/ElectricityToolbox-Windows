from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, call

from openpyxl import Workbook

TOOLBOX_TEST_TAGS = ()

from generate_red_marked_report import (
    build_actual_market_text, build_day_ahead_market_text,
    build_realtime_text, build_strategy_text, delta_phrase, load_change_text,
    load_trend_word, startup_change_text, UNIT_YUAN_PER_MWH, LOAD_SUBJECT_CODES,
    load_day_ahead_market_summary, clearing_input_cache,
)
import generate_red_marked_report as report


class ReportTextTests(unittest.TestCase):
    def test_generate_reads_market_summaries_from_two_different_dates(self) -> None:
        class StopBeforeNetwork(Exception):
            pass

        with tempfile.TemporaryDirectory() as root:
            day_source = Path(root) / "D.xlsx"
            clearing_source = Path(root) / "D-2.xlsx"
            for explicit in (True, False):
                with (
                    self.subTest(explicit=explicit),
                    patch.object(report, "find_clearing_workbook", side_effect=lambda date: {
                        "2026-11-12": day_source, "2026-11-10": clearing_source}[date]),
                    patch.object(report, "check_workbook_dates", return_value=[]),
                    patch.object(report, "resolve_template_path", return_value=Path(root) / "template.docx"),
                    patch.object(report, "load_day_ahead_market_summary", return_value="市场原文") as load,
                    patch("fetch_daily_data.run_daily_data", side_effect=StopBeforeNetwork),
                    self.assertRaises(StopBeforeNetwork),
                ):
                    report.generate("2026-11-12", Path(root) / "template.docx", Path(root),
                                    day_ahead_workbook=day_source if explicit else None,
                                    daily_clearing_workbook=clearing_source if explicit else None)
                self.assertEqual(load.call_args_list, [
                    call("2026-11-12", day_source), call("2026-11-10", clearing_source)])

    def test_market_summary_reads_a41_in_generation_cache_scope(self) -> None:
        expected = "市场开机451台，环比增加23台。全省日前均价402.99元/兆瓦时，环比上涨22.57元/兆瓦时。"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "出清情况.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "快报  (日前)"
            sheet["A39"] = "市场开机999台。全省日前均价999元/兆瓦时。"
            sheet["A41"] = "日前简析：\n1.负荷需求回升。" + expected + "\n2.汕头厂：策略。"
            workbook.save(path)
            workbook.close()
            # generate() 整体使用此缓存；只在缓存外测试会漏掉快照接口的不兼容。
            with clearing_input_cache():
                self.assertEqual(load_day_ahead_market_summary("2026-11-12", path), expected)
                self.assertEqual(load_day_ahead_market_summary("2026-11-12", path), expected)
            self.assertEqual(load_day_ahead_market_summary("2026-11-12", path), expected)
            sheet["A41"] = None
            workbook.save(path)
            workbook.close()
            with clearing_input_cache(), self.assertRaisesRegex(ValueError, "A41 缺少"):
                load_day_ahead_market_summary("2026-11-12", path)

    def test_market_summary_copies_whole_sentences_with_varied_wording(self) -> None:
        samples = (
            ("市场开机451台，较昨日多23台。全省日前出清均价384.33元/兆瓦时，较昨日涨21.64元/兆瓦时。", None),
            ("市场开机451台，基本不变；全省日前出清均价384.33元/MWh，较上日回落。", None),
            ("市场机组开机451台（增加23台）。日前平均电价384.33元/MWh，涨幅5.97%。", None),
            ("市场开机451台，与前日持平！全省日前均价384.33元/兆瓦时，较前日上涨.", None),
            ("市场开机451台，\n较昨日多23台。\n全省日前出清均价384.33元/兆瓦时，较昨日涨21.64元/兆瓦时", "市场开机451台，较昨日多23台。全省日前出清均价384.33元/兆瓦时，较昨日涨21.64元/兆瓦时。"),
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "D日.xlsx"
            for source, expected in samples:
                with self.subTest(source=source):
                    workbook = Workbook()
                    sheet = workbook.active
                    sheet.title = "快报（日前）"
                    sheet["A41"] = "日前简析：\n1.负荷上升。检修容量1579万千瓦。" + source + "\n2.汕头厂：策略不应被复制。"
                    workbook.save(path)
                    workbook.close()
                    self.assertEqual(load_day_ahead_market_summary("2026-11-12", path), expected or source)
            sheet["A41"] = "1.市场开机451台，较昨日多23台。\n2.汕头厂：全省日前出清均价384.33元/兆瓦时。"
            workbook.save(path)
            workbook.close()
            with self.assertRaisesRegex(ValueError, "A41 缺少"):
                load_day_ahead_market_summary("2026-11-12", path)

    def test_market_summary_reads_only_a41_and_preserves_source_wording(self) -> None:
        for sheet_name in ("快报  (日前)", "快报（日前）"):
            for changes in (("增加23台", "上涨21.64元/兆瓦时"),
                            ("减少3台", "下降5.59元/兆瓦时"), ("持平", "持平")):
                expected = (f"市场开机台数451台，环比{changes[0]}，"
                            f"全省日前出清均价384.33元/兆瓦时，环比{changes[1]}。")
                with self.subTest(sheet=sheet_name, changes=changes), tempfile.TemporaryDirectory() as root:
                    path = Path(root) / "D日.xlsx"
                    workbook = Workbook()
                    sheet = workbook.active
                    sheet.title = sheet_name
                    sheet["A39"] = "市场开机台数999台，环比持平，全省日前出清均价999元/兆瓦时，环比持平。"
                    sheet["A41"] = "日前简析：\n1.负荷需求回升。\n  " + expected + "\n2.汕头厂：策略文字。"
                    workbook.save(path)
                    workbook.close()
                    self.assertEqual(load_day_ahead_market_summary("2026-11-12", path), expected)
                    sheet["A41"] = None
                    workbook.save(path)
                    workbook.close()
                    with self.assertRaisesRegex(ValueError, "A41 缺少"):
                        load_day_ahead_market_summary("2026-11-12", path)

    def test_load_change_text_uses_flat_when_delta_rounds_to_zero(self) -> None:
        # 人工报告写「环比持平」，不写「环比上升0万千瓦」。
        self.assertEqual(load_change_text(0), "持平")
        self.assertEqual(load_change_text(4.9), "持平")
        self.assertEqual(load_change_text(-4.9), "持平")
        self.assertEqual(load_change_text(1000), "上升100万千瓦")
        self.assertEqual(load_change_text(-1980), "下降198万千瓦")

    def test_load_trend_word_has_no_number(self) -> None:
        # 句首「统调负荷需求上升，全天最大…」只带方向，不带数值。
        self.assertEqual(load_trend_word(0), "持平")
        self.assertEqual(load_trend_word(4), "持平")
        self.assertEqual(load_trend_word(1000), "上升")
        self.assertEqual(load_trend_word(-1980), "下降")

    def test_price_and_startup_deltas_use_flat_when_shown_as_zero(self) -> None:
        # 均价保留两位小数、台数取整，展示值为 0 时同样写「持平」。
        self.assertEqual(delta_phrase(0, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(0.004, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(-0.004, unit=UNIT_YUAN_PER_MWH, digits=2), "持平")
        self.assertEqual(delta_phrase(11.35, unit=UNIT_YUAN_PER_MWH, digits=2), "上升11.35元/兆瓦时")
        self.assertEqual(delta_phrase(-5.59, unit=UNIT_YUAN_PER_MWH, digits=2), "下降5.59元/兆瓦时")
        self.assertEqual(startup_change_text(0), "持平")
        self.assertEqual(startup_change_text(0.4), "持平")
        self.assertEqual(startup_change_text(2), "增加2台")
        self.assertEqual(startup_change_text(-3), "减少3台")

    def test_word_fetches_the_local_power_code_required_by_its_text(self) -> None:
        self.assertEqual(LOAD_SUBJECT_CODES, ["1", "6", "2", "3"])

    def test_local_power_uses_code_3_in_every_report_section(self) -> None:
        # Local power rises while total generation falls: confusing 3 and 18
        # would reverse the reported trend even though both codes exist.
        subjects = []
        by_type = {}
        for code in ("1", "2", "3", "6", "18"):
            difference = -10 if code == "18" else 10
            metric = {"selected": 100, "compare": 100 - difference,
                      "difference": difference}
            subjects.append({"loadType": code, "comparison": {
                name: dict(metric) for name in ("最大值(MW)", "最小值(MW)", "平均值(MW)")}})
            summary = {"selected": {"max": 100, "min": 10, "avg": 100},
                       "maxDiff": difference, "minDiff": difference,
                       "avgDiff": difference}
            by_type[code] = {"dayAhead": {**summary, "selected": {**summary["selected"], "avg": 90}},
                             "actual": summary}
        prices = {"rows": [{"dataType": kind, "selectedDailyAvgPrice": 100,
                            "difference": 10} for kind in ("PRE", "RT")]}
        payload = {"loadCompare": {"subjects": subjects}, "priceBoard": prices,
                   "marketAnalysisData": {"loadInfo": {"byType": by_type},
                                          "priceBoard": prices}}
        for builder, label in ((build_strategy_text, "地方电源出力"),
                               (build_realtime_text, "地方电出力"),
                               (build_day_ahead_market_text, "地方电出力"),
                               (build_actual_market_text, "地方电出力")):
            with self.subTest(section=builder.__name__):
                if builder in (build_strategy_text, build_day_ahead_market_text):
                    summary = "市场开机台数451台，环比增加23台，全省日前出清均价384.33元/兆瓦时，环比上涨21.64元/兆瓦时。"
                    # 与接口均价100相冲突时，两处报告仍必须逐字采用工作簿原文。
                    text = builder(payload, market_summary=summary)
                    self.assertTrue(text.endswith(summary))
                    self.assertNotIn("均价100", text)
                else:
                    text = builder(payload)
                self.assertIn(label + "增加", text)
                self.assertNotIn(label + "减少", text)

    def test_actual_market_text_accepts_missing_load_extrema(self) -> None:
        empty_summary = {
            "selected": {"max": None, "min": None, "avg": None, "sum": None},
            "compare": {"max": None, "min": None, "avg": None, "sum": None},
            "maxDiff": None,
            "minDiff": None,
            "avgDiff": None,
            "sumDiff": None,
        }
        payload = {
            "marketAnalysisData": {
                "loadInfo": {
                    "byType": {
                        code: {"actual": dict(empty_summary)}
                        for code in ("1", "2", "3", "6")
                    }
                },
                "priceBoard": {
                    "rows": [
                        {
                            "dataType": "RT",
                            "selectedDailyAvgPrice": None,
                            "difference": None,
                        }
                    ]
                },
            }
        }

        text = build_actual_market_text(payload)

        self.assertIn("全天最大值待补充万千瓦", text)
        self.assertIn("最小值待补充万千瓦", text)
        self.assertIn("全天实时均价待补充元/兆瓦时", text)
        self.assertIn("A类电出力待核对、地方电出力待核对", text)

    def test_actual_power_trends_compare_same_day_actual_against_day_ahead(self) -> None:
        from fetch_daily_data import build_load_info_comparison

        def item(actual, forecast):
            return {"actual": {"avg": actual, "max": 124510, "min": 81950},
                    "dayAhead": {"avg": forecast, "max": 128000, "min": 92000}}

        selected = {"date": "2026-11-10", "byType": {
            "1": item(100000, 110000), "6": item(1000, 2000),
            "2": item(900, 1000), "3": item(13349.65, 9126.06)}}
        previous = {"date": "2026-11-09", "byType": {
            "1": item(110000, 120000), "6": item(2000, 3000),
            "2": item(800, 700), "3": item(15000, 10000)}}
        comparison = build_load_info_comparison(selected, previous)
        # 两种口径刻意相反，防止误用 actual.avgDiff（与前日实际比较）。
        self.assertGreater(comparison["byType"]["2"]["actual"]["avgDiff"], 0)
        self.assertLess(comparison["byType"]["3"]["actual"]["avgDiff"], 0)
        payload = {"marketAnalysisData": {"loadInfo": comparison,
                   "priceBoard": {"rows": [{"dataType": "RT", "selectedDailyAvgPrice": 300}]}}}
        text = build_actual_market_text(payload)
        self.assertIn("A类电出力减少、地方电出力增加", text)
        self.assertIn("全天最大值12451万千瓦，最小值8195万千瓦", text)
        comparison["byType"]["3"]["dayAhead"]["selected"]["avg"] = 13349.65
        self.assertIn("地方电出力持平", build_actual_market_text(payload))


if __name__ == "__main__":
    unittest.main()
