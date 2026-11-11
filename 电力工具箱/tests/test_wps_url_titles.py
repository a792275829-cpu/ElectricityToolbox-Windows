from __future__ import annotations

import copy
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

TOOLBOX_TEST_TAGS = ("gui",)

from toolbox.runtime import load_module
from toolbox_wps_config import build_config_export, runtime_config_from_export_payload
from toolbox_wps_urls import url_choices

ROOT = Path(__file__).resolve().parents[2]
FIRST = 'https://www.kdocs.cn/l/first'
SECOND = 'https://www.kdocs.cn/l/second'
TITLE = '出清情况（分公司2026.10.12）'


class WpsTitleFormatTests(unittest.TestCase):
    def test_rust_binding_formats_and_resolves_label(self):
        label = FIRST + '（' + TITLE + '）'
        choices = url_choices([FIRST, SECOND, FIRST], {FIRST: TITLE}, label)
        self.assertEqual(choices['url'], FIRST)
        self.assertEqual(choices['options'], [
            {'url': FIRST, 'label': label}, {'url': SECOND, 'label': SECOND},
        ])
        self.assertEqual(url_choices([], {}, 'https://example.test/（原始）')['url'],
                         'https://example.test/（原始）')

    def test_title_cache_survives_export_import_without_changing_mappings(self):
        config = json.loads((ROOT / 'wps自动/wps_excel_to_kdocs_config.defaults.json').read_text())
        original = copy.deepcopy(config['configs'])
        config['document_titles'] = {FIRST: TITLE, SECOND: '另一份表格'}
        restored = runtime_config_from_export_payload(build_config_export(config))
        self.assertEqual(restored['document_titles'], config['document_titles'])
        self.assertEqual(restored['configs'], original)


class WpsTitleDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.module = load_module('wps_url_title_dialog_test', ROOT / 'wps自动/wps_excel_to_kdocs_gui.py')
        with (mock.patch.object(self.module.DocumentConfigDialog, 'wait_visibility'),
              mock.patch.object(self.module.DocumentConfigDialog, 'wait_window')):
            self.dialog = self.module.DocumentConfigDialog(
                self.root, '编辑配置', recent_urls=[FIRST, SECOND],
                document_titles={FIRST: TITLE, SECOND: '另一份表格'},
            )

    def tearDown(self):
        self.dialog.destroy()
        self.root.destroy()

    def test_selected_label_keeps_raw_url_for_read_and_save(self):
        dialog = self.dialog
        self.assertEqual(dialog.target_url_display.get(), FIRST + '（' + TITLE + '）')
        self.assertEqual(dialog.kdocs_url.get(), FIRST)
        self.assertEqual(list(dialog.target_url_combo['values']), [
            FIRST + '（' + TITLE + '）', SECOND + '（另一份表格）',
        ])
        dialog.target_url_display.set(SECOND + '（另一份表格）')
        self.assertEqual(dialog.kdocs_url.get(), SECOND)
        with mock.patch.object(dialog, '_start_online_read') as read:
            dialog.refresh_online_sheets()
        read.assert_called_once_with('target', SECOND, True)
        dialog.local_file.set('/tmp/source.xlsx')
        dialog.regions = [self.module.RegionRow('来源', 'A1', 'A2', '目标', 'B1', 'B2')]
        self.assertEqual(dialog.build_current_config().kdocs_url, SECOND)
        dialog.target_url_display.set('https://example.test/（原始）')
        self.assertEqual(dialog.kdocs_url.get(), 'https://example.test/（原始）')

    def test_metadata_read_updates_inline_label_and_cache(self):
        dialog = self.dialog
        callback = mock.Mock()
        dialog.on_document_title = callback
        with mock.patch.object(self.module, 'read_online_document_info', return_value={
            'title': '更新后的名称（含括号）', 'sheets': ['工作表'],
        }) as read:
            dialog.refresh_online_sheets()
            deadline = time.monotonic() + 2
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(dialog._pending_online_reads)
        self.assertEqual(read.call_args.args[0], FIRST)
        self.assertEqual(dialog.target_url_display.get(), FIRST + '（更新后的名称（含括号））')
        callback.assert_called_once_with(FIRST, '更新后的名称（含括号）')
        labels = []
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label): labels.append(str(child.cget('text')))
                walk(child)
        walk(dialog)
        self.assertNotIn('目标表格名称', labels)

    def test_outdated_read_does_not_attach_name_to_different_url(self):
        dialog = self.dialog
        dialog._request_ids['target'] = 1
        dialog._pending_online_reads['target'] = (1, FIRST)
        dialog.kdocs_url.set(SECOND)
        dialog._online_results.put(('target', 1, FIRST, False,
                                   {'title': '过时名称', 'sheets': ['旧工作表']}, None))
        dialog._drain_online_results()
        self.assertEqual(dialog.target_url_display.get(), SECOND + '（另一份表格）')
        self.assertEqual(dialog.online_sheets, [])
        self.assertEqual(dialog.document_titles[FIRST], TITLE)

    def test_source_refresh_reads_link_decorates_label_and_supplies_region_choices(self):
        dialog = self.dialog
        dialog.source_url_display.set(SECOND + '（另一份表格）')
        self.assertEqual(dialog.source_url.get(), SECOND)
        self.assertEqual(dialog.source_type.get(), 'excel')
        with mock.patch.object(self.module, 'read_online_document_info', return_value={
            'title': '来源名称（含括号）', 'sheets': ['来源一', '来源二'],
        }) as read:
            dialog.refresh_source_button.invoke()
            deadline = time.monotonic() + 2
            while dialog._pending_online_reads and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertFalse(dialog._pending_online_reads)
        self.assertEqual(dialog.source_type.get(), 'kdocs')
        self.assertEqual(read.call_args.args[0], SECOND)
        self.assertEqual(dialog.source_url_display.get(), SECOND + '（来源名称（含括号））')
        self.assertEqual(dialog.local_sheets, ['来源一', '来源二'])
        self.assertEqual(dialog.document_titles[SECOND], '来源名称（含括号）')
        original = self.module.RegionDialog

        def fill_region(*args, **kwargs):
            region = original(*args, **kwargs)
            combo = next(w for w in region.winfo_children()
                         if isinstance(w, ttk.Combobox) and str(w.cget('textvariable')) == str(region.vars['source_sheet']))
            self.assertEqual(list(combo['values']), ['来源一', '来源二'])
            combo.current(1)
            for field, value in {'source_start': 'A1', 'source_end': 'A2', 'target_sheet': '目标',
                                 'target_start': 'B1', 'target_end': 'B2'}.items():
                region.vars[field].set(value)
            region.on_ok()
            return region

        with (mock.patch.object(original, 'wait_visibility'),
              mock.patch.object(original, 'wait_window'),
              mock.patch.object(self.module, 'RegionDialog', side_effect=fill_region)):
            dialog.add_region()
        self.assertEqual(dialog.regions[0].source_sheet, '来源二')
        self.assertEqual(dialog.build_current_config().source_url, SECOND)

    def test_region_opened_before_source_read_uses_latest_sheets_on_dropdown(self):
        dialog = self.dialog
        dialog.source_type.set('kdocs')
        dialog.source_url.set(SECOND)
        with (mock.patch.object(self.module.RegionDialog, 'wait_visibility'),
              mock.patch.object(self.module.RegionDialog, 'wait_window')):
            region = self.module.RegionDialog(dialog, '添加区域', sheet_provider=dialog._region_sheet_choices)
        try:
            combo = next(w for w in region.winfo_children() if isinstance(w, ttk.Combobox))
            self.assertEqual(list(combo['values']), [])
            request = dialog._request_ids['source']
            dialog._pending_online_reads['source'] = (request, SECOND)
            dialog._online_results.put(('source', request, SECOND, False,
                                        {'title': '来源', 'sheets': ['新工作表一', '新工作表二']}, None))
            dialog._drain_online_results()
            combo.tk.call(combo.cget('postcommand'))
            self.assertEqual(list(combo['values']), ['新工作表一', '新工作表二'])
            combo.current(1)
            self.assertEqual(region.vars['source_sheet'].get(), '新工作表二')
        finally:
            region.destroy()

    def test_changed_source_link_discards_stale_sheets_and_metadata(self):
        dialog = self.dialog
        dialog.source_type.set('kdocs')
        dialog.source_url.set(FIRST)
        old_request = dialog._request_ids['source']
        dialog._pending_online_reads['source'] = (old_request, FIRST)
        dialog.local_sheets = ['旧工作表']
        dialog.source_url.set(SECOND)
        dialog._online_results.put(('source', old_request, FIRST, False,
                                   {'title': '过时名称', 'sheets': ['旧结果']}, None))
        dialog._drain_online_results()
        self.assertEqual(dialog.local_sheets, [])
        self.assertEqual(dialog.source_url_display.get(), SECOND + '（另一份表格）')
        self.assertEqual(dialog.document_titles[FIRST], TITLE)
        self.assertEqual(str(dialog.refresh_source_button['state']), 'normal')

    def test_existing_online_source_loads_metadata_automatically(self):
        initial = self.module.DocumentConfig(name='test', kdocs_url=FIRST, local_file='', regions=[],
                                             source_type='kdocs', source_url=SECOND)
        with (mock.patch.object(self.module.DocumentConfigDialog, 'wait_visibility'),
              mock.patch.object(self.module.DocumentConfigDialog, 'wait_window'),
              mock.patch.object(self.module.DocumentConfigDialog, '_start_online_read') as read):
            dialog = self.module.DocumentConfigDialog(self.root, '编辑配置', initial=initial)
        try:
            self.assertIn(mock.call('source', SECOND, False), read.call_args_list)
            self.assertIn(mock.call('target', FIRST, False), read.call_args_list)
        finally:
            dialog.destroy()

    def test_parent_saves_only_raw_urls_and_reuses_cached_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            with (mock.patch.object(self.module, 'load_config', return_value={}),
                  mock.patch.object(self.module, 'CONFIG_PATH', config_path)):
                frame = self.module.WpsWriterFrame(self.root)
                try:
                    frame.remember_document_title(FIRST, TITLE)
                    saved = json.loads(config_path.read_text())
                    self.assertEqual(saved['document_titles'], {FIRST: TITLE})
                    self.assertNotIn('（', ''.join(saved['recent_urls']))
                    frame.apply_runtime_config(saved)
                    self.assertEqual(frame.document_titles, {FIRST: TITLE})
                finally:
                    frame.destroy()


if __name__ == '__main__':
    unittest.main()
