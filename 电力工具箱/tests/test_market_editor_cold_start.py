"""Exercise the first click without a test runner's preloaded Excel packages."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MarketEditorColdStartTests(unittest.TestCase):
    def test_first_open_edit_save_reopen_needs_no_spreadsheet_runtime(self):
        # Reintroducing an eager Excel import makes the UI depend on NumPy's
        # native initialization again. Block that boundary in a fresh process.
        script = textwrap.dedent('''
            import importlib.abc
            import json
            from pathlib import Path
            import sys
            import tkinter as tk

            class NoSpreadsheetRuntime(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'openpyxl', 'numpy', 'pandas'}:
                        raise AssertionError('UI attempted spreadsheet initialization: ' + fullname)

            sys.meta_path.insert(0, NoSpreadsheetRuntime())
            from toolbox.market_report_page import MarketReportPage
            from toolbox.runtime import TaskRegistry, ToolPaths
            from toolbox.ui.theme import configure_theme
            from tkinter import ttk
            root = tk.Tk()
            root.geometry('1000x850')
            root.tk.call('tk', 'scaling', 2.5)
            configure_theme(ttk.Style(root))
            registry = TaskRegistry()
            page = MarketReportPage(root, ToolPaths(Path.cwd().parent), registry)
            page.pack(fill='both', expand=True)
            page.report_date.set('2026-10-28')
            root.update()
            try:
                for attempt in range(2):
                    page.open_market_editor()
                    editor = page.market_editor
                    assert sum(len(pair) for fields in editor.values.values() for pair in fields.values()) == 36
                    editor.values['realtime']['price']['value'].set('-50')
                    editor.values['realtime']['price']['comparison'].set('0')
                    editor.refresh()
                    assert editor.deltas['realtime']['price'].get() == '-50'
                    heartbeat = []
                    root.after(0, lambda: heartbeat.append(True))
                    root.update()
                    assert heartbeat, 'Tk event loop stopped responding'
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
                    save = next(w for w in descendants(editor) if isinstance(w, ttk.Button) and w.cget('text') == '保存并返回')
                    assert save.winfo_ismapped(), ('Save button is not visible', editor.geometry(), editor.winfo_reqheight(), save.winfo_geometry(), save.master.winfo_geometry())
                    assert save.winfo_rooty() + save.winfo_height() <= editor.winfo_rooty() + editor.winfo_height(), 'Save button clipped at display scaling'
                    assert save.winfo_rooty() + save.winfo_height() <= root.winfo_screenheight(), 'Save button below screen at display scaling'
                    tabs = next(w for w in descendants(editor) if isinstance(w, ttk.Notebook))
                    tabs.select(1)
                    root.update()
                    viewport = editor.nametowidget(tabs.select())
                    canvas = next(w for w in viewport.winfo_children() if isinstance(w, tk.Canvas))
                    canvas.yview_moveto(1)
                    root.update()
                    clear = next(w for w in descendants(canvas) if isinstance(w, ttk.Button) and w.cget('text') == '清空本页')
                    assert canvas.winfo_rooty() <= clear.winfo_rooty(), 'Last row cannot be reached by scrolling'
                    assert clear.winfo_rooty() + clear.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height(), 'Last row clipped after scrolling'
                    editor.save()
                    saved = json.loads(page.manual_market.get())
                    assert saved['report_date'] == '2026-10-28'
                    assert saved['realtime']['price'] == {'value': -50, 'comparison': 0}
                print('cold-open-edit-save-reopen: OK')
            finally:
                if page.market_editor is not None and page.market_editor.winfo_exists():
                    page.market_editor.close()
                page.destroy()
                registry.terminate_all()
                root.destroy()
        ''')
        result = subprocess.run(
            [sys.executable, '-B', '-c', script], cwd=ROOT / '电力工具箱',
            env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1'},
            capture_output=True, text=True, encoding='utf-8', timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('cold-open-edit-save-reopen: OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
