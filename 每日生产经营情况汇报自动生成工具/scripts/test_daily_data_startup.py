from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl

import fetch_daily_data as daily

TOOLBOX_TEST_TAGS = ()


class DailyDataStartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name in (
            "load_config", "sync_playwright", "launch_context", "ensure_login",
            "run_load_compare_with_context", "fetch_price_summaries",
            "fetch_capacity_summary", "fetch_load_info_summary",
            "fetch_startup_count_summary", "fetch_rolling_forecast",
        ):
            self.mocks[name] = self.stack.enter_context(patch.object(daily, name))
        self.mocks["load_config"].return_value = SimpleNamespace(headless=True)
        self.context = self.mocks["launch_context"].return_value
        self.mocks["run_load_compare_with_context"].return_value = {"subjects": []}
        self.mocks["fetch_price_summaries"].return_value = []
        self.mocks["fetch_capacity_summary"].side_effect = lambda context, **kw: {
            "date": kw["date_text"], "capacityWanKw": 123,
        }
        self.mocks["fetch_load_info_summary"].side_effect = lambda context, **kw: {
            "date": kw["date_text"], "byType": {},
        }
        self.mocks["fetch_rolling_forecast"].return_value = {
            "orgName": "测试公司", "unitName": "测试场站", "rows": [],
            "selectedDate": "2026-11-12",
            "forecastStartDate": "2026-11-13", "forecastEndDate": "2026-11-17",
        }
        self.mocks["fetch_startup_count_summary"].side_effect = RuntimeError(
            "读取市场行情看板开机台数 2026-11-11 失败：T100"
        )

    def run_daily(self, **kwargs):
        return daily.run_daily_data(
            selected_date="2026-11-12", compare_date="2026-11-11", subjects=[], **kwargs,
        )

    def test_default_skips_obsolete_api_and_export_survives_reopen(self):
        payload = self.run_daily()
        self.mocks["fetch_startup_count_summary"].assert_not_called()
        self.context.close.assert_called_once()
        self.assertEqual(
            [call.kwargs["date_text"] for call in self.mocks["fetch_capacity_summary"].call_args_list],
            ["2026-11-12", "2026-11-11", "2026-11-10", "2026-11-09"],
        )
        self.assertEqual(payload["datePlan"]["dayAheadDate"], "2026-11-12")
        for section in (payload, payload["marketAnalysisData"]):
            startup = section["operationPublicData"]["startupCount"]
            self.assertIsNone(startup["difference"])
            self.assertEqual(startup["selected"]["status"], "not_requested")
            self.assertIsNone(startup["selected"]["maxStartupCount"])
        with tempfile.TemporaryDirectory() as directory:
            output = daily.save_output(payload, Path(directory))
            workbook = openpyxl.load_workbook(output, data_only=True)
            try:
                self.assertEqual(workbook["汇总"]["B2"].value, "2026-11-12")
                self.assertEqual(workbook["汇总"]["B3"].value, "2026-11-11")
                self.assertEqual(workbook["汇总"]["B6"].value,
                                 "123万千瓦 / 未抓取（早报采用报告日快报(日前)A41原文）")
                self.assertEqual(workbook["汇总"]["B6"].number_format, "General")
                raw = json.loads(workbook["原始JSON"]["A1"].value)
                self.assertIsNone(raw["operationPublicData"]["startupCount"]["selected"]["maxStartupCount"])
            finally:
                workbook.close()
        with patch.object(daily, "log") as log:
            daily.print_result(payload, output)
        self.assertIn("未抓取", str(log.call_args_list))
        self.assertNotIn("None台", str(log.call_args_list))

    def test_explicit_startup_fetch_keeps_original_error_and_closes_context(self):
        with self.assertRaisesRegex(RuntimeError, "2026-11-11 失败：T100"):
            self.run_daily(include_startup_counts=True)
        self.context.close.assert_called_once()

    def test_explicit_startup_fetch_preserves_counts_and_comparison(self):
        fetch = self.mocks["fetch_startup_count_summary"]
        fetch.side_effect = [
            {"maxStartupCount": count} for count in (451, 428, 420, 418)
        ]
        payload = self.run_daily(include_startup_counts=True)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(payload["operationPublicData"]["startupCount"]["difference"], 23)
        self.assertEqual(payload["marketAnalysisData"]["operationPublicData"]["startupCount"]["difference"], 2)

    def test_other_required_data_failures_still_propagate(self):
        self.mocks["fetch_capacity_summary"].side_effect = RuntimeError("容量接口失败")
        with self.assertRaisesRegex(RuntimeError, "容量接口失败"):
            self.run_daily()
        self.context.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
