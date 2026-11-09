from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock, patch

from toolbox.runtime import ToolPaths, load_module


ROOT = Path(__file__).resolve().parents[2]


class OnlineEnergyOutputTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_online_energy_output", ToolPaths(ROOT).online_energy)

    def range_module(self, fetch, *, units=({"id": "u", "name": "#1"},)):
        """Patch the browser boundary so a whole date range uses one session."""
        module = self.module
        context = SimpleNamespace(close=Mock())
        session = SimpleNamespace(
            sync_playwright=Mock(side_effect=lambda: nullcontext(None)),
            launch_context=Mock(side_effect=lambda *a, **k: context),
            ensure_login=Mock(return_value=None),
            get_companies=Mock(return_value=[dict(tenantId="test", name="海门电厂")]),
        )
        overrides = dict(
            sync_playwright=session.sync_playwright,
            launch_context=session.launch_context,
            ensure_login=session.ensure_login,
            log=lambda *a: None,
            get_companies=session.get_companies,
            switch_company=lambda *a: None,
            get_units=lambda *a: list(units),
            is_renewable_company=lambda *a: False,
            fetch_fire_rows=fetch,
        )
        return (module, context, session,
                patch.multiple(module, **overrides), patch.object(module.time, "sleep"))

    def date_rows(self, run_date, fetch, *, units=({"id": "u", "unitName": "#1"},),
                  companies=None):
        """Call collect_date_rows directly with the network boundary patched."""
        module = self.module
        context = SimpleNamespace(close=Mock())
        if companies is None:
            companies = [{"tenantId": "test", "name": "海门电厂"}]
        overrides = dict(
            switch_company=lambda *a: None,
            get_units=lambda *a: list(units),
            is_renewable_company=lambda *a: False,
            fetch_fire_rows=fetch,
            log=lambda *a: None,
        )
        with patch.multiple(module, **overrides), patch.object(module.time, "sleep"):
            return module.collect_date_rows(
                run_date, context, companies, module.Config(request_delay_seconds=0))

    def test_default_and_empty_config_use_downloads_subfolder(self):
        expected = Path.home() / "Downloads" / "上网电量"
        self.assertEqual(self.module.OUTPUT_DIR, expected)
        for raw in ({}, {"outputDir": ""}, {"outputDir": None}):
            with self.subTest(raw=raw), mock.patch.object(self.module, "read_login_config", return_value=raw):
                self.assertEqual(self.module.load_config().output_dir, expected)

    def test_toolbox_directory_matches_export_config(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = ToolPaths(Path(folder))
            config_path = paths.online_energy_dir / "config.json"
            config_path.parent.mkdir()
            for raw in ({}, {"outputDir": "~/Downloads/自定义电量"}):
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(raw=raw), mock.patch.object(self.module, "read_login_config", return_value=raw):
                    self.assertEqual(paths.online_energy_output_dir, self.module.load_config().output_dir)

    def test_managed_data_root_does_not_redirect_default_downloads(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {
            "TOOLBOX_CODE_ROOT": str(ROOT), "TOOLBOX_DATA_ROOT": folder,
        }):
            self.assertEqual(ToolPaths(ROOT).online_energy_output_dir, Path.home() / "Downloads" / "上网电量")

    def test_export_creates_folder_and_workbook_can_be_read_back(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "Downloads" / "上网电量"
            rows = [{"company": "汕头电厂", "unit": "#1", "time": "00:00", "onlineEle": 12.5}]
            path = self.module.save_excel("2026-10-24", rows, output)
            self.assertEqual(path, output / "上网电量汇总_2026-10-24.xlsx")
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A1"].value, "汕头 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["A2"].value, "1号机")
                self.assertEqual(sheet["A3"].value, 12.5)
                self.assertEqual(sheet["A3"].number_format, "General")
                self.assertEqual(sheet["A4"].value, 0)
            finally:
                workbook.close()

    def test_qingyuan_is_sorted_after_existing_companies(self):
        names = [item['name'] for item in self.module.FALLBACK_COMPANIES] + ['清远电厂']
        companies = [{'tenantId': str(index), 'name': name} for index, name in enumerate(names)]
        context = mock.Mock()
        with mock.patch.object(self.module, 'parse_json_response', return_value={'data': list(reversed(companies))}):
            result = self.module.get_companies(context)
        self.assertEqual([item['name'] for item in result], names)
        self.assertEqual(self.module.display_company_name('清远电厂'), '清远')

    def test_qingyuan_export_preserves_existing_blocks_and_reads_back_all_hours(self):
        import openpyxl

        companies = [item['name'] for item in self.module.FALLBACK_COMPANIES]
        rows = [{'company': name, 'unit': '#1', 'time': '00:00', 'onlineEle': 100 + i}
                for i, name in enumerate(companies)]
        for hour in range(24):
            for unit, value in (('#1', hour + 0.125), ('#2', 0.0 if hour == 0 else hour + 0.75)):
                rows.append({'company': '清远电厂', 'unit': unit, 'time': f'{hour:02d}:00', 'onlineEle': value})
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            path = self.module.save_excel('2026-10-28', rows, output)
            self.assertEqual(path.name, '上网电量汇总_2026-10-28.xlsx')
            workbook = openpyxl.load_workbook(path)
            try:
                self.assertEqual(workbook.sheetnames, ['汇总'])
                sheet = workbook['汇总']
                for i, name in enumerate(companies):
                    start = 1 + i * 27
                    self.assertEqual(sheet.cell(start, 1).value,
                                     f'{self.module.display_company_name(name)} 实际上网电量（万千瓦时）')
                    self.assertEqual(sheet.cell(start + 2, 1).value, 100 + i)
                self.assertEqual(sheet['A163'].value, '清远 实际上网电量（万千瓦时）')
                self.assertIn('A163:B163', {str(item) for item in sheet.merged_cells.ranges})
                self.assertEqual([sheet['A164'].value, sheet['B164'].value], ['1号机', '2号机'])
                for hour in range(24):
                    for column, expected in ((1, hour + 0.125), (2, 0.0 if hour == 0 else hour + 0.75)):
                        cell = sheet.cell(165 + hour, column)
                        self.assertEqual(cell.value, expected)
                        self.assertEqual(cell.number_format, 'General')
                self.assertEqual(sheet['A189'].value, None)
            finally:
                workbook.close()

    def test_qingyuan_collection_uses_dedicated_endpoint_and_d2_plan(self):
        context = mock.Mock()
        companies = [{'tenantId': 'qingyuan-test-tenant', 'name': '清远电厂'}]
        units = [{'id': 'u2', 'unitName': '清远#2'}, {'id': 'u1', 'unitName': '清远#1'}]
        config = self.module.Config(request_delay_seconds=0)
        manager = mock.MagicMock()

        def fetch(_context, run_date, _tenant_id, *, metric='onlineEle'):
            base = 1.5 if metric == 'onlineEle' else 100.0
            return [
                {'unitId': 'u1', 'runDate': run_date, 'onlineEle': [base] * 24},
                {'unitId': 'u2', 'runDate': run_date, 'onlineEle': [base + 0.75] * 24},
            ]

        with (mock.patch.object(self.module, 'sync_playwright', return_value=manager),
              mock.patch.object(self.module, 'launch_context', return_value=context),
              mock.patch.object(self.module, 'ensure_login'),
              mock.patch.object(self.module, 'get_companies', return_value=companies),
              mock.patch.object(self.module, 'switch_company') as switch,
              mock.patch.object(self.module, 'get_units', return_value=units),
              mock.patch.object(self.module, 'fetch_qingyuan_rows', side_effect=fetch) as qingyuan,
              mock.patch.object(self.module, 'fetch_fire_rows') as fire,
              mock.patch.object(self.module, 'fetch_energy_rows') as renewable,
              mock.patch.object(self.module.time, 'sleep'),
              mock.patch.object(self.module, 'log')):
            rows = self.module.collect_detail_rows('2026-10-28', config)

        switch.assert_called_once_with(context, 'qingyuan-test-tenant')
        fire.assert_not_called()
        renewable.assert_not_called()
        self.assertEqual([call.args[1] for call in qingyuan.call_args_list],
                         ['2026-10-28', '2026-10-30'])
        self.assertEqual(qingyuan.call_args_list[1].kwargs['metric'], 'planValue')
        context.close.assert_called_once()
        self.assertEqual(len(rows), 48)
        self.assertTrue(all(row['planDate'] == '2026-10-30' for row in rows))
        blocks = self.module.build_company_blocks(rows)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['company'], '清远')
        self.assertEqual(blocks[0]['units'], ['1号机', '2号机'] * 2)
        self.assertEqual(blocks[0]['plan_date'], '2026-10-30')
        self.assertEqual(blocks[0]['rows'][0], [1.5, 2.25, 100.0, 100.75])

    def test_qingyuan_plan_units_mismatch_stops_export(self):
        context = mock.Mock()
        companies = [{'tenantId': 'qingyuan-test-tenant', 'name': '清远电厂'}]
        units = [{'id': 'u1', 'unitName': '清远#1'}]
        config = self.module.Config(request_delay_seconds=0)
        manager = mock.MagicMock()

        def fetch(_context, run_date, _tenant_id, *, metric='onlineEle'):
            unit = 'u1' if metric == 'onlineEle' else 'other'
            return [{'unitId': unit, 'runDate': run_date, 'onlineEle': [1.0] * 24}]

        with (mock.patch.object(self.module, 'sync_playwright', return_value=manager),
              mock.patch.object(self.module, 'launch_context', return_value=context),
              mock.patch.object(self.module, 'ensure_login'),
              mock.patch.object(self.module, 'get_companies', return_value=companies),
              mock.patch.object(self.module, 'switch_company'),
              mock.patch.object(self.module, 'get_units', return_value=units),
              mock.patch.object(self.module, 'fetch_qingyuan_rows', side_effect=fetch),
              mock.patch.object(self.module.time, 'sleep'),
              mock.patch.object(self.module, 'log')):
            with self.assertRaisesRegex(ValueError, '计划电量机组与机组列表不符'):
                self.module.collect_detail_rows('2026-10-28', config)
        context.close.assert_called_once()

    def test_qingyuan_plan_columns_preserve_actual_position(self):
        import openpyxl
        rows = [{"company": "清远电厂", "unit": unit, "time": f"{hour:02d}:00",
                 "onlineEle": 10 + col, "planEle": None if hour == 0 else 100 + col,
                 "planDate": "2027-01-01"}
                for col, unit in enumerate(("清远#1、#2机", "清远#3、#4机")) for hour in range(24)]
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-12-30", rows, Path(folder))
            book = openpyxl.load_workbook(path)
            try:
                sheet = book["汇总"]
                self.assertEqual(sheet["A1"].value, "清远 实际上网电量（万千瓦时）")
                self.assertIn("2027-01-01 计划电量", sheet["C1"].value)
                merged = {str(item) for item in sheet.merged_cells.ranges}
                self.assertIn("A1:B1", merged)
                self.assertIn("C1:D1", merged)
                self.assertEqual([sheet.cell(2, c).value for c in range(1, 5)],
                                 ["1、2号机", "3、4号机"] * 2)
                self.assertEqual([sheet.cell(3, c).value for c in range(1, 5)], [10, 11, None, None])
                self.assertEqual([sheet.cell(4, c).value for c in range(1, 5)], [10, 11, 100, 101])
                self.assertEqual(sheet["C3"].number_format, "General")
            finally:
                book.close()

    def test_qingyuan_two_groups_are_appended_with_hourly_values(self):
        import openpyxl

        rows = [
            {"company": company, "unit": unit, "time": f"{hour:02d}:00", "onlineEle": value + hour}
            for company, unit, value in (
                ("清远电厂", "清远#1、#2机", 100.7),
                ("清远电厂", "清远#3、#4机", 0),
                ("汕头电厂", "#1", 10),
            )
            for hour in range(24)
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-10-28", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A1"].value, "汕头 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["A28"].value, "清远 实际上网电量（万千瓦时）")
                self.assertEqual((sheet["A29"].value, sheet["B29"].value), ("1、2号机", "3、4号机"))
                self.assertEqual((sheet["A30"].value, sheet["B30"].value), (100.7, 0))
                self.assertEqual((sheet["A53"].value, sheet["B53"].value), (123.7, 23))
                self.assertEqual(sheet["A30"].number_format, "General")
            finally:
                workbook.close()

    def test_qingyuan_api_uses_online_columns_and_checks_hours(self):
        columns = [{"unitId": unit, "fieldKey": f"{unit}_{metric}", "metricType": metric}
                   for unit in ("a", "b") for metric in ("actualEle", "onlineEle", "planValue")]
        rows = [{"time": f"{hour:02d}:00", "valueMap": {
            "a_actualEle": 999, "a_onlineEle": hour + 0.7,
            "b_actualEle": 999, "b_onlineEle": 0,
            "a_planValue": 100, "b_planValue": 55,
        }} for hour in reversed(range(24))]
        context = mock.Mock()
        with mock.patch.object(self.module, "parse_json_response",
                               return_value={"data": {"columnList": columns, "rowList": rows}}):
            result = self.module.fetch_qingyuan_rows(context, "2026-10-28", "tenant")
            self.assertEqual(result[0]["onlineEle"][0], 0.7)
            self.assertEqual(result[0]["onlineEle"][-1], 23.7)
            self.assertEqual(result[1]["onlineEle"], [0] * 24)
            plans = self.module.fetch_qingyuan_rows(context, "2026-10-30", "tenant", metric="planValue")
            self.assertEqual(plans[0]["onlineEle"], [100] * 24)
            self.assertEqual(plans[1]["onlineEle"], [55] * 24)
            self.assertEqual(context.request.get.call_args.kwargs["params"]["runDate"], "2026-10-30")
            self.module.fetch_qingyuan_rows(context, "2026-10-28", "tenant")
            self.assertEqual(context.request.get.call_args.kwargs["params"], {
                "runDate": "2026-10-28", "endDate": "2026-10-28", "orgId": "tenant"})
            rows[-1]["time"] = rows[0]["time"]
            with self.assertRaisesRegex(ValueError, "重复"):
                self.module.fetch_qingyuan_rows(context, "2026-10-28", "tenant")

    def test_qingyuan_unreported_values_stay_blank_and_other_values_remain(self):
        import openpyxl

        values = self.module.online_energy_hours([None, 0, 12.5] + [None] * 21,
                                                 label="清远", allow_empty=True)
        with self.assertRaisesRegex(ValueError, "缺失或无效"):
            self.module.online_energy_hours([None] * 24, label="其他电厂")
        with self.assertRaisesRegex(ValueError, "缺失或无效"):
            self.module.online_energy_hours(["无效"] * 24, label="清远", allow_empty=True)
        with self.assertRaisesRegex(ValueError, "24个小时电量"):
            self.module.online_energy_hours([1.0] * 23, label="清远", allow_empty=True)
        rows = [{"company": "清远电厂", "unit": "清远#1、#2机", "time": f"{hour:02d}:00",
                 "onlineEle": value} for hour, value in enumerate(values)]
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-11-01", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertIsNone(sheet["A3"].value)
                self.assertEqual(sheet["A4"].value, 0)
                self.assertEqual(sheet["A5"].value, 12.5)
                self.assertEqual(sheet["A3"].number_format, "General")
            finally:
                workbook.close()

    def test_qingyuan_plan_date_mismatch_stops_export(self):
        rows = [{"company": "清远电厂", "unit": "#1", "time": f"{hour:02d}:00",
                 "onlineEle": 1.0, "planEle": 2.0,
                 "planDate": "2027-01-02" if hour else "2027-01-01"}
                for hour in range(24)]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "计划电量日期不一致"):
                self.module.save_excel("2026-12-30", rows, Path(folder))
            self.assertEqual(list(Path(folder).glob("*.xlsx")), [])

    # ---- New Mac-aligned validation / empty-value / single-session coverage ----

    def test_collect_date_rows_rejects_bad_unit_inventory(self):
        with self.assertRaisesRegex(ValueError, "机组列表为空、缺少ID或ID重复"):
            self.date_rows("2026-10-28", lambda *a: [], units=[])
        with self.assertRaisesRegex(ValueError, "机组列表为空、缺少ID或ID重复"):
            self.date_rows("2026-10-28", lambda *a: [], units=[{"unitName": "#1"}])
        with self.assertRaisesRegex(ValueError, "机组列表为空、缺少ID或ID重复"):
            self.date_rows("2026-10-28", lambda *a: [],
                           units=[{"id": "u", "unitName": "#1"}, {"id": "u", "unitName": "#2"}])

    def test_collect_date_rows_rejects_unknown_or_duplicate_units(self):
        units = [{"id": "u", "unitName": "#1"}]
        with self.assertRaisesRegex(ValueError, "返回未知或重复机组 ghost"):
            self.date_rows("2026-10-28", lambda *a: [
                {"unitId": "ghost", "runDate": "2026-10-28", "onlineEle": [1.0] * 24}], units=units)
        with self.assertRaisesRegex(ValueError, "返回未知或重复机组 u"):
            self.date_rows("2026-10-28", lambda *a: [
                {"unitId": "u", "runDate": "2026-10-28", "onlineEle": [1.0] * 24} for _ in range(2)],
                units=units)

    def test_collect_date_rows_rejects_return_date_mismatch(self):
        with self.assertRaisesRegex(ValueError, "返回日期与 2026-10-28 不符"):
            self.date_rows("2026-10-28", lambda *a: [
                {"unitId": "u", "runDate": "2026-10-27", "onlineEle": [1.0] * 24}],
                units=[{"id": "u", "unitName": "#1"}])

    def test_collect_date_rows_keeps_unit_without_rows_blank(self):
        units = [{"id": "u1", "unitName": "#1"}, {"id": "u2", "unitName": "#2"}]
        rows = self.date_rows("2026-10-28", lambda *a: [
            {"unitId": "u1", "runDate": "2026-10-28", "onlineEle": [1.0] * 24}], units=units)
        self.assertEqual(len(rows), 48)
        self.assertEqual([row["unit"] for row in rows[:24]], ["#1"] * 24)
        self.assertEqual([row["unit"] for row in rows[24:]], ["#2"] * 24)
        self.assertEqual([row["onlineEle"] for row in rows[:24]], [1.0] * 24)
        self.assertTrue(all(row["onlineEle"] is None for row in rows[24:]))

    def test_collect_date_rows_rejects_company_without_any_rows(self):
        units = [{"id": "u1", "unitName": "#1"}, {"id": "u2", "unitName": "#2"}]
        with self.assertRaisesRegex(ValueError, "未返回任何机组上网电量"):
            self.date_rows("2026-10-28", lambda *a: [], units=units)

    def test_collect_date_rows_skips_unit_created_after_run_date(self):
        units = [{"id": "u1", "unitName": "海门#5机组", "createTime": "2026-11-09 09:25:35"},
                 {"id": "u2", "unitName": "海门#1机组", "createTime": "2022-05-25 23:04:41"}]
        rows = self.date_rows("2026-11-08", lambda *a: [
            {"unitId": "u2", "runDate": "2026-11-08", "onlineEle": [2.0] * 24}], units=units)
        self.assertEqual({row["unit"] for row in rows}, {"海门#1机组"})
        self.assertEqual(len(rows), 24)
        # 同一天新建的机组保留，以便当期数据补报后仍然输出。
        rows = self.date_rows("2026-11-09", lambda *a: [
            {"unitId": "u2", "runDate": "2026-11-09", "onlineEle": [2.0] * 24}], units=units)
        self.assertEqual(len(rows), 48)

    def test_iter_detail_rows_rejects_empty_company_list(self):
        context = SimpleNamespace(close=Mock())
        with (patch.object(self.module, "sync_playwright", return_value=nullcontext(None)),
              patch.object(self.module, "launch_context", return_value=context),
              patch.object(self.module, "ensure_login"),
              patch.object(self.module, "get_companies", return_value=[]),
              patch.object(self.module, "log")):
            with self.assertRaisesRegex(ValueError, "未返回可采集公司"):
                self.module.collect_detail_rows("2026-10-28", self.module.Config())
        context.close.assert_called_once()

    def test_non_qingyuan_unreported_hours_stay_blank_not_zero(self):
        import openpyxl

        values = [None, 0, 12.5] + [None] * 21
        with patch.object(self.module, "number_or_zero",
                          side_effect=AssertionError("number_or_zero 补 0 分流仍存在")):
            rows = self.date_rows("2026-10-28", lambda *a: [
                {"unitId": "u", "runDate": "2026-10-28", "onlineEle": values}],
                units=[{"id": "u", "unitName": "#1"}])
        self.assertEqual(len(rows), 24)
        self.assertIsNone(rows[0]["onlineEle"])
        self.assertEqual(rows[1]["onlineEle"], 0)
        self.assertEqual(rows[2]["onlineEle"], 12.5)
        self.assertEqual(sum(row["onlineEle"] is None for row in rows), 22)
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-10-28", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertIsNone(sheet["A3"].value)
                self.assertEqual(sheet["A4"].value, 0)
                self.assertEqual(sheet["A5"].value, 12.5)
                self.assertEqual(sheet["A3"].number_format, "General")
            finally:
                workbook.close()

    def test_empty_hour_array_means_unreported_day(self):
        # 网站对尚未填报的场站返回 []，应视为 24 个未填报时段而不是致命错误。
        self.assertEqual(self.module.online_energy_hours([], label="华归光伏电站", allow_empty=True),
                         (None,) * 24)
        with self.assertRaisesRegex(ValueError, "24个小时电量"):
            self.module.online_energy_hours([], label="华归光伏电站")
        with self.assertRaisesRegex(ValueError, "24个小时电量"):
            self.module.online_energy_hours([1.0] * 23, label="华归光伏电站", allow_empty=True)
        with self.assertRaisesRegex(ValueError, "缺失或无效"):
            self.module.online_energy_hours(["无效"] * 24, label="华归光伏电站", allow_empty=True)

    def test_collect_date_rows_keeps_unreported_renewable_station_blank(self):
        import openpyxl

        module = self.module
        context = SimpleNamespace(close=Mock())
        units = [{"id": "u1", "unitName": "华归光伏电站"}]
        companies = [{"tenantId": "test", "name": "华能（潮州潮安）新能源有限责任公司"}]
        overrides = dict(
            switch_company=lambda *a: None,
            get_units=lambda *a: list(units),
            is_renewable_company=lambda *a: True,
            fetch_energy_rows=lambda *a, **k: [
                {"unitId": "u1", "runDate": "2026-11-08", "onlineEle": []}],
            log=lambda *a: None,
        )
        with patch.multiple(module, **overrides), patch.object(module.time, "sleep"):
            rows = module.collect_date_rows("2026-11-08", context, companies,
                                            module.Config(request_delay_seconds=0))
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(row["onlineEle"] is None for row in rows))
        self.assertEqual({row["unit"] for row in rows}, {"华归光伏电站"})
        with tempfile.TemporaryDirectory() as folder:
            path = module.save_excel("2026-11-08", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A2"].value, "华归光伏电站")
                self.assertIsNone(sheet["A3"].value)
                self.assertIsNone(sheet["A26"].value)
            finally:
                workbook.close()

    def test_blank_data_is_exported_with_ui_warning_marker(self):
        # 工具箱按日志里的「无数据：」「数据缺失」等关键字把状态标成「完成（有提示）」。
        lines = []
        module = self.module
        units = [{"id": "u1", "unitName": "#1"}, {"id": "u2", "unitName": "#2"}]
        overrides = dict(
            switch_company=lambda *a: None,
            get_units=lambda *a: list(units),
            is_renewable_company=lambda *a: False,
            fetch_fire_rows=lambda *a: [
                {"unitId": "u1", "runDate": "2026-11-08", "onlineEle": []},
                {"unitId": "u2", "runDate": "2026-11-08",
                 "onlineEle": [None] * 3 + [1.5] * 21}],
            log=lines.append,
        )
        with patch.multiple(module, **overrides), patch.object(module.time, "sleep"):
            rows = module.collect_date_rows("2026-11-08", SimpleNamespace(close=Mock()),
                                            [{"tenantId": "t", "name": "海门电厂"}],
                                            module.Config(request_delay_seconds=0))
        self.assertEqual(len(rows), 48)
        text = "\n".join(lines)
        self.assertIn("无数据：", text)
        self.assertIn("数据缺失：3 个时段", text)
        # 界面判定用到的关键字必须在同一份日志里出现。
        self.assertTrue(any(marker in text for marker in ("跳过", "回读不一致", "读取失败", "数据缺失", "无数据：")))

    def test_blank_summary_lists_only_fully_blank_units(self):
        rows = [
            {"company": "华能（潮州潮安）新能源有限责任公司", "unit": "华归光伏电站",
             "time": f"{hour:02d}:00", "onlineEle": None} for hour in range(24)
        ] + [
            {"company": "海门电厂", "unit": "海门#1机组",
             "time": f"{hour:02d}:00", "onlineEle": 0.0 if hour == 0 else None} for hour in range(24)
        ]
        self.assertEqual(self.module.blank_unit_labels(rows), ["潮安 / 华归光伏电站"])
        lines = []
        with patch.object(self.module, "log", lines.append):
            self.module.log_blank_summary("2026-11-08", rows)
        self.assertEqual(len(lines), 1)
        self.assertIn("2026-11-08 有 1 个机组当日无数据：潮安 / 华归光伏电站", lines[0])
        with patch.object(self.module, "log", lines.append):
            self.module.log_blank_summary("2026-11-08", rows[24:])
        self.assertEqual(len(lines), 1)

    def test_range_dates_reject_reversed_range_and_cover_both_ends(self):
        self.assertEqual(self.module.range_dates("2026-10-26", "2026-10-28"),
                         ["2026-10-26", "2026-10-27", "2026-10-28"])
        self.assertEqual(self.module.range_dates("2026-10-26", "2026-10-26"), ["2026-10-26"])
        with self.assertRaisesRegex(ValueError, "结束日期不能早于开始日期"):
            self.module.range_dates("2026-10-28", "2026-10-26")

    def test_range_export_writes_one_workbook_per_day_and_reuses_one_session(self):
        fetched = []

        def fetch(_context, run_date, _tenant_id):
            fetched.append(run_date)
            return [dict(unitId="u", runDate=run_date, onlineEle=[1.5] * 24)]

        module, context, session, overrides, sleep = self.range_module(fetch)
        with tempfile.TemporaryDirectory() as folder, overrides, sleep:
            paths = module.export_online_energy_range(
                "2026-10-26", "2026-10-28", module.Config(output_dir=Path(folder)))
            self.assertEqual([path.name for path in paths], [
                "上网电量汇总_2026-10-26.xlsx",
                "上网电量汇总_2026-10-27.xlsx",
                "上网电量汇总_2026-10-28.xlsx",
            ])
            self.assertTrue(all(path.is_file() for path in paths))
            self.assertEqual(fetched, ["2026-10-26", "2026-10-27", "2026-10-28"])
        context.close.assert_called_once()
        self.assertEqual(session.sync_playwright.call_count, 1)
        self.assertEqual(session.launch_context.call_count, 1)
        self.assertEqual(session.ensure_login.call_count, 1)
        self.assertEqual(session.get_companies.call_count, 1)

    def test_range_export_stops_on_bad_day_and_keeps_finished_workbooks(self):
        def fetch(_context, run_date, _tenant_id):
            return [dict(unitId="u", runDate=run_date,
                         onlineEle=[1.0] * 24 if run_date != "2026-10-27" else ["无效"] * 24)]

        module, context, session, overrides, sleep = self.range_module(fetch)
        with tempfile.TemporaryDirectory() as folder, overrides, sleep:
            folder = Path(folder)
            with self.assertRaisesRegex(ValueError, "缺失或无效"):
                module.export_online_energy_range(
                    "2026-10-26", "2026-10-28", module.Config(output_dir=folder))
            self.assertTrue((folder / "上网电量汇总_2026-10-26.xlsx").is_file())
            self.assertFalse((folder / "上网电量汇总_2026-10-27.xlsx").exists())
            self.assertFalse((folder / "上网电量汇总_2026-10-28.xlsx").exists())
        context.close.assert_called_once()
        self.assertEqual(session.sync_playwright.call_count, 1)

    def test_full_layout_with_plan_columns_reads_back_at_row_163(self):
        import openpyxl

        plan_date = "2026-10-30"
        rows = []
        for i, company in enumerate(item["name"] for item in self.module.FALLBACK_COMPANIES):
            for hour in range(24):
                rows.append({"company": company, "unit": "#1", "time": f"{hour:02d}:00",
                             "onlineEle": 100 + i + hour})
        for col, unit in enumerate(("清远#1", "清远#2")):
            for hour in range(24):
                rows.append({"company": "清远电厂", "unit": unit, "time": f"{hour:02d}:00",
                             "onlineEle": 10 + col + hour,
                             "planEle": None if hour == 0 else 200 + col + hour,
                             "planDate": plan_date})
        with tempfile.TemporaryDirectory() as folder:
            path = self.module.save_excel("2026-10-28", rows, Path(folder))
            workbook = openpyxl.load_workbook(path)
            try:
                sheet = workbook["汇总"]
                self.assertEqual(sheet["A163"].value, "清远 实际上网电量（万千瓦时）")
                self.assertEqual(sheet["C163"].value, f"清远 {plan_date} 计划电量（万千瓦时）")
                merged = {str(item) for item in sheet.merged_cells.ranges}
                self.assertIn("A163:B163", merged)
                self.assertIn("C163:D163", merged)
                self.assertEqual([sheet.cell(164, c).value for c in range(1, 5)],
                                 ["1号机", "2号机", "1号机", "2号机"])
                self.assertEqual([sheet.cell(165, c).value for c in range(1, 5)],
                                 [10, 11, None, None])
                self.assertEqual([sheet.cell(188, c).value for c in range(1, 5)],
                                 [33, 34, 223, 224])
                for ref in ("A165", "B165", "C165", "D165", "A188", "C188", "D188"):
                    self.assertEqual(sheet[ref].number_format, "General")
                self.assertIsNone(sheet["A189"].value)
            finally:
                workbook.close()

    def test_launch_context_wraps_context_for_session_recovery(self):
        import toolbox_auth_requests

        module = self.module
        raw_context = SimpleNamespace(add_cookies=Mock(), clear_cookies=Mock(),
                                      storage_state=Mock(return_value={}))
        chromium = SimpleNamespace(launch_persistent_context=Mock(return_value=raw_context))
        playwright = SimpleNamespace(chromium=chromium)
        with (patch.object(module, "PROFILE_DIR", mock.MagicMock()),
              patch.object(module, "playwright_proxy", return_value=None),
              patch.object(module, "prepare_browser_session", return_value=True),
              patch.object(module, "load_saved_auth_state") as load_saved):
            wrapped = module.launch_context(playwright, headless=False)
        self.assertIsInstance(wrapped, toolbox_auth_requests.AuthenticatedContext)
        self.assertIs(wrapped._context, raw_context)
        self.assertIs(wrapped._auth, module)
        self.assertIs(wrapped._auth, module._SELF_MODULE)
        self.assertFalse(raw_context._qingneng_headless)
        load_saved.assert_not_called()

    def test_main_reports_structured_login_error(self):
        module = self.module
        with (patch.object(module, "load_config", return_value=module.Config()),
              patch.object(module, "ask_date", side_effect=ValueError("bad date")),
              patch.object(sys, "argv", ["export_online_energy.py"]),
              patch("toolbox_cli_login.emit_login_error") as emit):
            code = module.main()
        self.assertEqual(code, 1)
        emit.assert_called_once()
