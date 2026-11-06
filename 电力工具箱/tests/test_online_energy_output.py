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

    def test_qingyuan_collection_uses_selected_date_and_existing_fire_endpoint(self):
        context = mock.Mock()
        companies = [{'tenantId': 'qingyuan-test-tenant', 'name': '清远电厂'}]
        units = [{'id': 'u2', 'unitName': '清远#2'}, {'id': 'u1', 'unitName': '清远#1'}]
        config = self.module.Config(request_delay_seconds=0)
        manager = mock.MagicMock()
        with (mock.patch.object(self.module, 'sync_playwright', return_value=manager),
              mock.patch.object(self.module, 'launch_context', return_value=context),
              mock.patch.object(self.module, 'ensure_login'),
              mock.patch.object(self.module, 'get_companies', return_value=companies),
              mock.patch.object(self.module, 'switch_company') as switch,
              mock.patch.object(self.module, 'get_units', return_value=units),
              mock.patch.object(self.module, 'fetch_fire_rows', return_value=[
                  {'unitId': 'u2', 'onlineEle': [2.25] * 24},
                  {'unitId': 'u1', 'onlineEle': [1.5] * 24}]) as fire,
              mock.patch.object(self.module, 'fetch_energy_rows') as renewable,
              mock.patch.object(self.module.time, 'sleep'),
              mock.patch.object(self.module, 'log')):
            rows = self.module.collect_detail_rows('2026-10-28', config)
        switch.assert_called_once_with(context, 'qingyuan-test-tenant')
        fire.assert_called_once_with(context, '2026-10-28', 'qingyuan-test-tenant')
        renewable.assert_not_called()
        context.close.assert_called_once()
        self.assertEqual(len(rows), 48)
        blocks = self.module.build_company_blocks(rows)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['company'], '清远')
        self.assertEqual(blocks[0]['units'], ['1号机', '2号机'])
        self.assertEqual(blocks[0]['rows'], [[1.5, 2.25]] * 24)
