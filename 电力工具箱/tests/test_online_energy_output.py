from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from toolbox.runtime import ToolPaths, load_module


ROOT = Path(__file__).resolve().parents[2]


class OnlineEnergyOutputTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_online_energy_output", ToolPaths(ROOT).online_energy)

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
