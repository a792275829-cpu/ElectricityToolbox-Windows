"""Real Windows regression coverage; no production websites or workbooks."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]


@unittest.skipUnless(sys.platform == 'win32', 'Windows kernel integration')
class WindowsPlatformTests(unittest.TestCase):
    def test_original_navigation_labels_fit_windows_font_metrics(self):
        import tkinter as tk
        from tkinter import font, ttk
        from toolbox.app import ToolboxApp, PAGE_NAMES
        app = ToolboxApp(page_factories={name: lambda parent, *_: tk.Frame(parent) for name in PAGE_NAMES}, lazy_pages=True)
        try:
            app.update()
            nav_font = font.Font(root=app, font=ttk.Style(app).lookup('Nav.TButton', 'font'))
            for name, button in app.nav_buttons.items():
                self.assertGreaterEqual(button.winfo_width(), nav_font.measure(name) + 20, name)
        finally:
            app.destroy()

    def test_windows_ocr_reads_chinese_labels_with_normalized_boxes(self):
        sys.path.insert(0, str(ROOT / '广东运行日报PDF解析'))
        from gd_pdf_tool.image_labels import recognize_image_labels
        from PIL import Image, ImageDraw, ImageFont
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '中文 标签.png'
            image = Image.new('RGB', (800, 240), 'white')
            font = ImageFont.truetype(str(Path(os.environ['WINDIR']) / 'Fonts/msyh.ttc'), 60)
            ImageDraw.Draw(image).text((50, 70), '燃煤机组', fill='black', font=font)
            image.save(path)
            try:
                labels = recognize_image_labels(path)
            except RuntimeError as exc:
                if 'Install the Windows Simplified Chinese OCR language feature' in str(exc):
                    self.skipTest('Windows Simplified Chinese OCR language feature is not installed')
                raise
            self.assertIn('燃煤机组', ''.join(label['text'].replace(' ', '') for label in labels))
            for label in labels:
                self.assertTrue(0 <= label['box']['origin']['x'] <= 1)
                self.assertTrue(0 <= label['box']['origin']['y'] <= 1)

    def test_pid_probe_does_not_terminate_running_process(self):
        from toolbox_platform import process_is_alive
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            self.assertTrue(process_is_alive(child.pid))
            self.assertIsNone(child.poll())
            child.terminate()
            child.wait(timeout=5)
            self.assertFalse(process_is_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_windows_updater_never_contacts_mac_release_channel(self):
        sys.path.insert(0, str(ROOT / 'desktop/python'))
        from github_update import GitHubUpdater, UpdateError
        def reject_network(*args, **kwargs):
            raise AssertionError('Windows attempted to access the macOS update channel')
        updater = GitHubUpdater('0.1.19', opener=reject_network)
        with self.assertRaisesRegex(UpdateError, 'Windows'):
            updater.check()

    def test_native_exe_is_discovered(self):
        import toolbox_native_excel as excel
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / '500kV断面分析/bin/toolbox-price-xlsx.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'test')
            with patch.object(excel, 'ROOT', root):
                self.assertEqual(excel.native_binary(), binary)

    def test_output_lock_excludes_other_process_and_releases_on_exit(self):
        # Importing the old implementation fails on Windows (fcntl).
        from toolbox_workbook_commit import workbook_commit_lock
        with tempfile.TemporaryDirectory(prefix='中文 空格 ') as tmp:
            target = Path(tmp) / '表.xlsx'
            script = (
                'from pathlib import Path; from toolbox_workbook_commit import workbook_commit_lock; '
                f'lock=workbook_commit_lock([Path({str(target)!r})]); lock.__enter__(); print("locked")'
            )
            with workbook_commit_lock([target]):
                result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                    capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertNotEqual(result.returncode, 0)
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_process_output_unicode_and_partial_line(self):
        script = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / '电力工具箱'))
from toolbox.tasks import ProcessRunner, CancellationToken
lines=[]
ProcessRunner().run([sys.executable, '-u', '-c', "import sys; sys.stdout.buffer.write(('中文日志'+chr(13)+chr(10)+'末行').encode('utf-8'))"], cwd=Path.cwd(), env=None, token=CancellationToken(), on_output=lines.append)
assert lines == ['中文日志\\n', '末行'], repr(lines)
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancellation_reaps_descendant_even_after_parent_exits(self):
        script = '''
import sys, threading, time, os, ctypes
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / '电力工具箱'))
from toolbox.tasks import ProcessRunner, CancellationToken, TaskCancelled
from toolbox.tasks import windows_process
original_job = windows_process.ProcessJob
def delayed_job(process):
    time.sleep(.4)  # Exercise slow initialization before ownership is established.
    return original_job(process)
windows_process.ProcessJob = delayed_job
token=CancellationToken()
child=[]
def output(line):
    child.append(int(line.strip()))
    token.cancel()
code="import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);print(p.pid,flush=True)"
try:
    ProcessRunner(terminate_timeout=.3).run([sys.executable,'-u','-c',code],cwd=Path.cwd(),env=None,token=token,on_output=output)
except TaskCancelled:
    pass
else:
    raise AssertionError('cancellation was ignored')
assert child
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.restype=ctypes.c_void_p
k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
k.CloseHandle.argtypes=[ctypes.c_void_p]
h=k.OpenProcess(0x100000,False,child[0])
if h:
    try: assert k.WaitForSingleObject(h,3000)==0, 'descendant survived cancellation'
    finally: k.CloseHandle(h)
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_persistent_browser_outlives_python_worker_but_other_children_are_reaped(self):
        from toolbox.tasks import ProcessRunner, CancellationToken, TaskCancelled
        from toolbox_platform import process_is_alive, hidden_process_options
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                token = CancellationToken()
                pids = []
                code = '''
import json, subprocess, sys, time
from toolbox_platform import detached_browser_process_options
stdio = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ordinary = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], **stdio)
browser = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], **stdio, **detached_browser_process_options())
print(json.dumps([ordinary.pid, browser.pid]), flush=True)
'''
                if cancel:
                    code += '\ntime.sleep(60)\n'
                def output(line):
                    import json
                    pids.extend(json.loads(line))
                    if cancel:
                        token.cancel()
                try:
                    try:
                        ProcessRunner().run([sys.executable, '-u', '-c', code], cwd=ROOT,
                            env=None, token=token, on_output=output, allow_browser_breakaway=True)
                    except TaskCancelled:
                        self.assertTrue(cancel)
                    else:
                        self.assertFalse(cancel)
                    self.assertEqual(len(pids), 2)
                    self.assertFalse(process_is_alive(pids[0]), 'Ordinary worker descendant survived cleanup')
                    self.assertTrue(process_is_alive(pids[1]), 'Dedicated browser was closed by worker cleanup')
                finally:
                    if len(pids) == 2 and process_is_alive(pids[1]):
                        subprocess.run(['taskkill', '/PID', str(pids[1]), '/T', '/F'],
                            capture_output=True, **hidden_process_options())


if __name__ == '__main__':
    unittest.main()
