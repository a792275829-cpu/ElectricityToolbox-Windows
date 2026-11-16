from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLBOX_TEST_TAGS = ()

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]
spec = importlib.util.spec_from_file_location(
    "prepare_workspace", ROOT / "packaging/tauri/prepare_workspace.py"
)
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class DesktopTests(unittest.TestCase):


    def test_payload_export_keeps_required_sources_and_excludes_private_data(self):
        spec = importlib.util.spec_from_file_location("payload_tests", ROOT / "packaging/tauri/prepare_payload.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ("上网电量抓取/auth_state.json", "x/config.json", "x/.browser-profile/Default/Cookies",
                     "x/.qingneng-session/config.json", ".toolbox/qingneng/config.json", "x/输出/report.xlsx",
                     "x/node_modules/package/index.js", "x/.state/cookies", "x/auth_state.json.lock",
                     "测试样例/市场表更新/业务副本.xlsx"):
            with self.subTest(name=name):
                self.assertFalse(module.build_support.exportable(Path(name)))
        for name in ("native_bridge.py", "github_update.py", "update_ui.py"):
            self.assertTrue(module.runtime_source(Path("desktop/python") / name))
        self.assertTrue(module.runtime_source(Path("500kV断面分析/data/500kv_node_positions.json")))
        self.assertFalse(module.runtime_source(Path("电力工具箱/tests/test_toolbox.py")))
        self.assertFalse(module.runtime_source(Path("packaging/tauri/build_support.py")))



class UpdateWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.payload = self.base / "payload"
        self.root = self.base / "user"
        (self.payload / "workspace").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def release(self, files):
        for name, content in files.items():
            path = self.payload / "workspace" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        hashes = {n: workspace.digest(self.payload / "workspace" / n) for n in files}
        (self.payload / "manifest.json").write_text(
            json.dumps({"files": hashes, "build": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "version": "test"})
        )

    def test_update_preserves_data_and_removes_only_managed(self):
        self.release({"tool.py": "v1", "old.py": "old"})
        workspace.install(self.payload, self.root)
        (self.root / "config.json").write_text("private")
        (self.root / "output.xlsx").write_text("report")
        self.release({"tool.py": "v2"})
        workspace.install(self.payload, self.root)
        self.assertEqual((self.root / "tool.py").read_text(), "v2")
        self.assertFalse((self.root / "old.py").exists())
        self.assertEqual((self.root / "config.json").read_text(), "private")
        self.assertTrue((self.root / "output.xlsx").exists())

    def test_local_edit_blocks_whole_update(self):
        self.release({"tool.py": "v1", "second.py": "v1"})
        workspace.install(self.payload, self.root)
        (self.root / "tool.py").write_text("local edit")
        self.release({"tool.py": "v2", "second.py": "v2"})
        with self.assertRaises(RuntimeError):
            workspace.install(self.payload, self.root)
        self.assertEqual((self.root / "tool.py").read_text(), "local edit")
        self.assertEqual((self.root / "second.py").read_text(), "v1")

    def test_managed_recovery_backs_up_edits_and_preserves_data(self):
        self.release({"tool.py": "v1", "second.py": "v1"})
        workspace.install(self.payload, self.root)
        (self.root / "tool.py").write_text("local edit")
        (self.root / "config.json").write_text("private")
        self.release({"tool.py": "v2", "second.py": "v2"})
        backups = self.base / "backups"
        workspace.install(self.payload, self.root, backup_root=backups, recover_local_edits=True)
        self.assertEqual((self.root / "tool.py").read_text(), "v2")
        self.assertEqual((self.root / "second.py").read_text(), "v2")
        self.assertEqual((self.root / "config.json").read_text(), "private")
        snapshot = next(backups.iterdir())
        self.assertEqual((snapshot / "tool.py").read_text(), "local edit")
        # A retry after a failed state write must not reuse the first snapshot.
        (self.root / "tool.py").write_text("second edit")
        (self.root / ".toolbox/desktop/installed.json").unlink()
        workspace.install(self.payload, self.root, backup_root=backups, recover_local_edits=True)
        self.assertEqual(len(list(backups.iterdir())), 2)
        self.assertEqual((snapshot / "tool.py").read_text(), "local edit")

    def test_recovery_backup_failure_does_not_replace_any_source(self):
        self.release({"tool.py": "v1", "second.py": "v1"})
        workspace.install(self.payload, self.root)
        (self.root / "tool.py").write_text("local edit")
        self.release({"tool.py": "v2", "second.py": "v2"})
        copy = workspace.shutil.copy2
        def fail_on_edit(source, target):
            if Path(source).name == "tool.py":
                raise OSError("backup failed")
            return copy(source, target)
        with mock.patch.object(workspace.shutil, "copy2", side_effect=fail_on_edit):
            with self.assertRaisesRegex(OSError, "backup failed"):
                workspace.install(self.payload, self.root, recover_local_edits=True)
        self.assertEqual((self.root / "tool.py").read_text(), "local edit")
        self.assertEqual((self.root / "second.py").read_text(), "v1")

    def test_recovery_state_failure_rolls_back_sources_and_keeps_backup(self):
        self.release({"tool.py": "v1", "second.py": "v1"})
        workspace.install(self.payload, self.root)
        state = self.root / ".toolbox/desktop/installed.json"
        original_state = state.read_bytes()
        (self.root / "tool.py").write_text("local edit")
        self.release({"tool.py": "v2", "second.py": "v2"})
        backups = self.base / "backups"
        replace = workspace.os.replace
        def fail_on_state(source, target):
            if Path(target) == state:
                raise OSError("state failed")
            return replace(source, target)
        with mock.patch.object(workspace.os, "replace", side_effect=fail_on_state):
            with self.assertRaisesRegex(OSError, "state failed"):
                workspace.install(self.payload, self.root, backup_root=backups, recover_local_edits=True)
        self.assertEqual((self.root / "tool.py").read_text(), "local edit")
        self.assertEqual((self.root / "second.py").read_text(), "v1")
        self.assertEqual(state.read_bytes(), original_state)
        self.assertEqual((next(backups.iterdir()) / "tool.py").read_text(), "local edit")

    def test_bad_hash_and_escape(self):
        self.release({"tool.py": "v1"})
        (self.payload / "workspace/tool.py").write_text("damaged")
        with self.assertRaises(ValueError):
            workspace.install(self.payload, self.root)
        for name in [
            "../escape",
            "/escape",
            "a/../../escape",
            "C:/escape",
            "a\\escape",
        ]:
            with self.assertRaises(ValueError):
                workspace.safe(self.root, name)


if __name__ == "__main__":
    unittest.main()
