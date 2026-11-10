"""Release provenance must reject dirty sources and exclude untracked files."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOLBOX_TEST_TAGS = ()
ROOT = Path(__file__).resolve().parents[3]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source = load('release_source_tests', 'tools/release_source.py')
support = load('release_build_support_tests', 'packaging/tauri/build_support.py')


class ReleaseSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git('init', '-q')
        (self.root / 'toolbox_fixture.py').write_text('original', encoding='utf-8')
        (self.root / '.gitignore').write_text('ignored/\n', encoding='utf-8')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root).decode().strip()

    def test_clean_checkout_records_exact_commit(self):
        result = source.inspect_source(self.root)
        self.assertEqual(result, {'commit': self.git('rev-parse', 'HEAD'),
                                  'development': False, 'dirty': False})
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored/runtime').write_text('cache')
        self.assertFalse(source.inspect_source(self.root)['dirty'])

    def test_dirty_and_untracked_sources_fail_release_but_allow_development(self):
        for path in ('toolbox_fixture.py', 'toolbox_local.py'):
            with self.subTest(path=path):
                target = self.root / path
                target.write_text('local change')
                with self.assertRaises(ValueError):
                    source.inspect_source(self.root)
                self.assertTrue(source.inspect_source(self.root, development=True)['dirty'])
                if path == 'toolbox_fixture.py':
                    self.git('restore', path)
                else:
                    target.unlink()

    def test_release_export_excludes_untracked_module(self):
        (self.root / 'toolbox_local.py').write_text('local change')
        with tempfile.TemporaryDirectory() as destination, patch.object(support, 'ROOT', self.root):
            output = Path(destination)
            support.export_source(output, tracked_only=True)
            self.assertTrue((output / 'toolbox_fixture.py').is_file())
            self.assertFalse((output / 'toolbox_local.py').exists())
            support.export_source(output)
            self.assertTrue((output / 'toolbox_local.py').is_file())
