"""WPS process discovery with restricted PATH and competing UI operations."""
from pathlib import Path
import os
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]


@unittest.skipUnless(sys.platform == 'win32', 'Windows process query')
class BrowserDiscoveryTests(unittest.TestCase):
    def test_windows_ocr_uses_system_powershell_without_path(self):
        import tempfile
        from PIL import Image, ImageDraw, ImageFont
        sys.path.insert(0, str(ROOT / '广东运行日报PDF解析'))
        from gd_pdf_tool.image_labels import recognize_image_labels
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '中文 标签.png'
            image = Image.new('RGB', (800, 240), 'white')
            font = ImageFont.truetype(str(Path(os.environ['WINDIR']) / 'Fonts/msyh.ttc'), 60)
            ImageDraw.Draw(image).text((50, 70), '燃煤机组', fill='black', font=font)
            image.save(path)
            image.close()
            with patch.dict(os.environ, {'PATH': ''}):
                try:
                    labels = recognize_image_labels(path)
                except RuntimeError as exc:
                    if 'Install the Windows Simplified Chinese OCR language feature' in str(exc):
                        self.skipTest('Windows Simplified Chinese OCR language feature is not installed')
                    self.fail(f'Windows OCR still depends on PATH: {exc}')
            self.assertIn('燃煤机组', ''.join(label['text'].replace(' ', '') for label in labels))

    def test_process_query_does_not_require_powershell_on_path(self):
        from toolbox_platform import windows_browser_processes
        with patch.dict(os.environ, {'PATH': ''}):
            try:
                rows = windows_browser_processes(os.getpid())
            except FileNotFoundError as exc:
                self.fail(f'WPS process discovery still depends on PATH: {exc}')
        self.assertEqual([int(row['ProcessId']) for row in rows], [os.getpid()])

    def test_process_query_uses_windows_api_when_environment_roots_are_missing(self):
        from toolbox_platform import windows_browser_processes
        environment = dict(os.environ)
        for name in ('SYSTEMROOT', 'WINDIR'):
            environment.pop(name, None)
        environment['PATH'] = ''
        with patch.dict(os.environ, environment, clear=True):
            try:
                rows = windows_browser_processes(os.getpid())
            except FileNotFoundError as exc:
                self.fail(f'WPS process discovery cannot resolve the system executable: {exc}')
        self.assertEqual([int(row['ProcessId']) for row in rows], [os.getpid()])


class WpsOperationSerializationTests(unittest.TestCase):
    def test_preview_write_and_inspect_wait_for_the_active_login_check(self):
        from toolbox.ui_tasks import RemoteTaskRegistry
        for action in ('preview', 'run', 'inspect'):
            with self.subTest(action=action):
                registry = RemoteTaskRegistry(None)
                entered = threading.Event()
                release = threading.Event()
                completed = threading.Event()
                active_lock = threading.Lock()
                active = 0
                calls = []
                results = {}
                errors = []

                def execute(tool_id, operation, values, token, log):
                    nonlocal active
                    with active_lock:
                        if active:
                            raise ValueError('该工具已有任务运行，请等待完成')
                        active += 1
                        calls.append(operation)
                    try:
                        if operation == 'status':
                            entered.set()
                            if not release.wait(3):
                                raise RuntimeError('Test did not release the background check')
                        return {'operation': operation}
                    finally:
                        with active_lock:
                            active -= 1

                def invoke(operation):
                    try:
                        results[operation] = registry.wps_action(operation, {}, [], lambda _: None)
                    except Exception as exc:
                        errors.append(str(exc))
                    finally:
                        if operation != 'status':
                            completed.set()

                with patch.object(registry, 'execute_remote', side_effect=execute):
                    first = threading.Thread(target=invoke, args=('status',), daemon=True)
                    second = threading.Thread(target=invoke, args=(action,), daemon=True)
                    first.start()
                    self.assertTrue(entered.wait(2))
                    second.start()
                    try:
                        self.assertFalse(completed.wait(.15),
                            'Foreground WPS action must wait, rather than fail with task occupied')
                    finally:
                        release.set()
                        first.join(3)
                        second.join(3)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(calls, ['status', action])
                self.assertEqual(results[action], {'operation': action})

    def test_failed_check_releases_the_queue_for_the_next_operation(self):
        from toolbox.ui_tasks import RemoteTaskRegistry
        registry = RemoteTaskRegistry(None)
        with patch.object(registry, 'execute_remote', side_effect=[RuntimeError('check failed'), {'ok': True}]):
            with self.assertRaisesRegex(RuntimeError, 'check failed'):
                registry.wps_action('status', {}, [], lambda _: None)
            result = []
            worker = threading.Thread(target=lambda: result.append(
                registry.wps_action('preview', {}, [], lambda _: None)), daemon=True)
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'Failed WPS operation left the queue locked')
            self.assertEqual(result, [{'ok': True}])


if __name__ == '__main__':
    unittest.main()
