"""Archive-instead-of-delete contract for the Windows payload build."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


payload = load("windows_prepare_payload", "packaging/windows/prepare_payload.py")
prune = load("prune_build_archive", "packaging/windows/prune_build_archive.py")


class PayloadArchiveTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory(prefix="payload-archive-")
        self.root = Path(self.temp.name)
        self.payload = self.root / "payload"
        self.archive = self.root / "build-archive"
        self.tree = self.payload / "app"
        (self.tree / "电力工具箱").mkdir(parents=True)
        for index in range(3):
            (self.tree / "电力工具箱" / f"tool_{index}.py").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_reports_both_generated_trees(self):
        (self.payload / "runtime/python").mkdir(parents=True)
        (self.payload / "runtime/python/python.exe").write_bytes(b"MZ")

        plan = payload.archive_plan(self.payload, self.archive, "20260101-000000")

        self.assertEqual([item[1].name for item in plan], ["app", "python"])
        self.assertEqual([item[2] for item in plan], ["3 个文件", "1 个文件"])
        self.assertEqual(plan[0][1], self.archive / "20260101-000000/app")

    def test_plan_marks_missing_tree(self):
        plan = payload.archive_plan(self.payload, self.archive, "20260101-000000")
        self.assertEqual(plan[0][2], "3 个文件")
        self.assertEqual(plan[1][2], "不存在，跳过")

    def test_stash_moves_tree_and_keeps_content(self):
        target = payload.stash(self.tree, "app", self.archive, "20260101-000000")

        self.assertFalse(self.tree.exists())
        self.assertEqual(target, self.archive / "20260101-000000/app")
        self.assertEqual(len(list((target / "电力工具箱").iterdir())), 3)

    def test_stash_returns_none_for_missing_tree(self):
        self.assertIsNone(payload.stash(self.payload / "runtime/python", "python",
                                        self.archive, "20260101-000000"))

    def test_stash_does_not_overwrite_existing_snapshot(self):
        first = payload.stash(self.tree, "app", self.archive, "20260101-000000")
        self.tree.mkdir(parents=True)
        second = payload.stash(self.tree, "app", self.archive, "20260101-000000")

        self.assertTrue(first.is_dir())
        self.assertEqual(second.name, "app-1")

    def test_no_rmtree_of_generated_trees(self):
        source = (ROOT / "packaging/windows/prepare_payload.py").read_text(encoding="utf-8")
        self.assertNotIn("shutil.rmtree(", source, "生成目录必须归档而不是原地删除")


class PruneArchiveTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory(prefix="prune-archive-")
        self.root = Path(self.temp.name)
        for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
            (self.root / stamp / "app").mkdir(parents=True)
            (self.root / stamp / "app" / "file.txt").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_keep_newest_snapshot_only(self):
        removals = prune.select_removals(self.root, 1, False)
        self.assertEqual([path.name for path in removals],
                         ["20260102-000000", "20260101-000000"])

    def test_remove_all_ignores_keep(self):
        self.assertEqual(len(prune.select_removals(self.root, 5, True)), 3)

    def test_missing_archive_is_not_an_error(self):
        self.assertEqual(prune.select_removals(self.root / "absent", 1, False), [])

    def test_purge_removes_nested_tree(self):
        target = self.root / "20260104-000000" / "python"
        nested = target / "Lib" / "site-packages"
        nested.mkdir(parents=True)
        for index in range(60):
            (nested / f"module_{index}.py").write_text("x", encoding="utf-8")

        prune.purge(self.root / "20260104-000000")

        self.assertFalse((self.root / "20260104-000000").exists())


if __name__ == "__main__":
    unittest.main()
