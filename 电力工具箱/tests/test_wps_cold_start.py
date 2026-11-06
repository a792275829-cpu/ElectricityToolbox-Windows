"""The WPS form must not initialize native spreadsheet libraries on first visit."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WpsColdStartTests(unittest.TestCase):
    def test_cell_addresses_preserve_existing_coordinate_rules(self):
        from toolbox.runtime import load_module
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string, get_column_letter
        module = load_module('wps_coordinate_compatibility_test', ROOT / 'wps自动/wps_excel_to_kdocs_gui.py')
        for address in ('a1', '$b$002', ' z99 ', 'AA10', 'AZ7', 'ZZ15', 'AAA1', 'XFD1048576', 'ZZZ9999999'):
            column, row = coordinate_from_string(address.strip().upper())
            with self.subTest(address=address):
                self.assertEqual(module.parse_start_cell(address), (row, column_index_from_string(column)))
        for column in range(1, 18279):
            self.assertEqual(module.get_column_letter(column), get_column_letter(column))
        for address in ('', 'A0', '1A', 'AAAA1', 'A-1', 'A1:B2', 'A 1'):
            with self.subTest(invalid=address), self.assertRaises(Exception):
                module.parse_start_cell(address)

    def test_first_visit_and_saved_regions_need_no_excel_runtime(self):
        script = textwrap.dedent('''
            import importlib.abc
            import json
            from pathlib import Path
            import sys
            import time
            import tkinter as tk
            from tkinter import ttk

            class NoExcelRuntime(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'openpyxl', 'numpy', 'pandas'}:
                        raise AssertionError('WPS form initialized Excel runtime: ' + fullname)

            sys.meta_path.insert(0, NoExcelRuntime())
            from toolbox.pages import WpsWriterPage
            from toolbox.runtime import ToolPaths, TaskRegistry
            from toolbox.ui.theme import configure_theme
            root = tk.Tk()
            root.withdraw()
            configure_theme(ttk.Style(root))
            registry = TaskRegistry()
            page = WpsWriterPage(root, ToolPaths(Path.cwd().parent), registry)
            page.pack(fill='both', expand=True)
            try:
                deadline = time.monotonic() + 5
                while not page._writer_load_done and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.005)
                assert hasattr(page, 'writer_frame'), 'WPS controls did not finish loading'
                frame = page.writer_frame
                assert len(frame.configs) == 3, 'Default document configurations missing'
                assert sum(len(c.regions) for c in frame.configs) == 34
                assert len(frame.tree.get_children()) == 3
                module = sys.modules['toolbox_wps_writer_gui']
                for config in ({}, {'mappings': [{'kdocs_url': 'https://example.invalid/target',
                        'local_file': '', 'source_sheet': 'source', 'source_range': '$a$1:$b$2',
                        'target_sheet': 'target', 'target_start': '$c$3'}]}):
                    module.CONFIG_PATH.write_text(json.dumps(config), encoding='utf-8')
                    frame.load_initial_config()
                    expected = int(bool(config))
                    assert len(frame.configs) == expected
                    assert len(frame.tree.get_children()) == expected
                    if expected:
                        region = frame.configs[0].regions[0]
                        assert (region.source_start, region.source_end, region.target_start, region.target_end) == ('A1', 'B2', '$c$3', 'D4')
                page.on_navigation_hidden()
                page.on_navigation_shown()
                root.update()
                assert page.writer_frame is frame
                assert not any(name.split('.')[0] in {'openpyxl', 'numpy', 'pandas'} for name in sys.modules)
                print('wps-first-visit-regions-reopen: OK')
            finally:
                page.destroy()
                registry.terminate_all()
                root.destroy()
        ''')
        with tempfile.TemporaryDirectory() as data:
            result = subprocess.run(
                [sys.executable, '-B', '-X', 'utf8', '-c', script], cwd=ROOT / '电力工具箱',
                env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1',
                     'TOOLBOX_CODE_ROOT': str(ROOT), 'TOOLBOX_DATA_ROOT': data},
                capture_output=True, encoding='utf-8', timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('wps-first-visit-regions-reopen: OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
