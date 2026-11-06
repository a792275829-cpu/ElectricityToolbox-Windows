"""Windows 安装包里的 Playwright 浏览器瘦身：只删确定用不到的文件。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging/tauri')]
import prepare_payload


def touch(path: Path, size: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)


class BrowserPayloadPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.browsers = Path(self.temp.name) / 'ms-playwright'

    def build_bundle(self) -> None:
        chrome = self.browsers / 'chromium-1187' / 'chrome-win'
        touch(self.browsers / 'chromium-1187' / 'INSTALLATION_COMPLETE', 0)
        touch(chrome / 'chrome.exe')
        touch(chrome / 'chrome.dll', 64)
        touch(chrome / 'resources.pak')
        for name in ('zh-CN.pak', 'en-US.pak', 'de.pak', 'ja.pak'):
            touch(chrome / 'locales' / name)
        for name in ('chrome_proxy.exe', 'chrome_pwa_launcher.exe',
                     'notification_helper.exe', 'elevation_service.exe'):
            touch(chrome / name)
        shell = self.browsers / 'chromium_headless_shell-1187' / 'chrome-win'
        touch(shell / 'headless_shell.exe', 64)
        touch(shell / 'headless_lib_strings.pak')
        touch(self.browsers / 'ffmpeg-1011' / 'ffmpeg-win64.exe')
        touch(self.browsers / 'winldd-1007' / 'PrintDeps.exe')

    def test_prune_drops_only_unused_browser_files(self) -> None:
        self.build_bundle()
        saved = prepare_payload.prune_browser_runtime(self.browsers)

        chrome = self.browsers / 'chromium-1187' / 'chrome-win'
        # 启动与渲染必需：可执行文件、dll、pak、安装标记都必须留着。
        for relative in ('chrome.exe', 'chrome.dll', 'resources.pak',
                         'locales/zh-CN.pak', 'locales/en-US.pak'):
            self.assertTrue((chrome / relative).is_file(), relative)
        self.assertTrue((self.browsers / 'chromium-1187' / 'INSTALLATION_COMPLETE').is_file())
        self.assertTrue((self.browsers / 'chromium_headless_shell-1187'
                         / 'chrome-win' / 'headless_shell.exe').is_file())
        # 自动化不需要的：录像用 ffmpeg、其他语种 pak、浏览器自带辅助进程。
        self.assertFalse((self.browsers / 'ffmpeg-1011').exists())
        self.assertFalse((chrome / 'locales' / 'de.pak').exists())
        self.assertFalse((chrome / 'locales' / 'ja.pak').exists())
        for name in ('chrome_proxy.exe', 'chrome_pwa_launcher.exe', 'notification_helper.exe'):
            self.assertFalse((chrome / name).exists(), name)
        # 名字不在删除清单里的 exe 不能误删。
        self.assertTrue((chrome / 'elevation_service.exe').is_file())
        self.assertTrue((self.browsers / 'winldd-1007' / 'PrintDeps.exe').is_file())
        self.assertIn('ffmpeg:ffmpeg-1011', saved)
        self.assertIn('locales:chromium-1187', saved)
        self.assertIn('exe:chrome_proxy.exe', saved)

    def test_prune_is_idempotent_and_tolerates_missing_tree(self) -> None:
        self.assertEqual({}, prepare_payload.prune_browser_runtime(self.browsers))
        self.build_bundle()
        self.assertTrue(prepare_payload.prune_browser_runtime(self.browsers))
        self.assertEqual({}, prepare_payload.prune_browser_runtime(self.browsers))


if __name__ == '__main__':
    unittest.main()
