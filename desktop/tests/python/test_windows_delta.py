"""Windows patch files, hard-link isolation and cross-platform route selection."""
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'desktop/python')]
from github_update import Cancelled, UpdateError
from delta_update import select_route


class RouteTests(unittest.TestCase):
    def test_windows_uses_the_cheapest_route_and_never_mac_assets(self):
        def edge(base, target, size, platform='windows-x64'):
            name = f'ElectricityToolbox-{base}-to-{target}-{platform}.patch.tar.xz'
            return dict(base=base, target=target, name=name, size=size, digest='sha256:' + 'a'*64,
                browser_download_url=f'https://github.com/owner/repo/releases/download/v{target}/{name}')
        first, last, direct = edge('1.0.1', '1.0.2', 10), edge('1.0.2', '1.0.3', 20), edge('1.0.1', '1.0.3', 40)
        index = dict(format=1, version='1.0.3', patches=[direct, first, last])
        self.assertEqual(select_route(index, '1.0.1', '1.0.3', 100, 'owner/repo', 'windows-x64'), [first, last])
        self.assertEqual(select_route(index, '1.0.1', '1.0.3', 30, 'owner/repo', 'windows-x64'), [])
        index['patches'] = [edge('1.0.1', '1.0.3', 1, 'apple-silicon')]
        with self.assertRaises(UpdateError):
            select_route(index, '1.0.1', '1.0.3', 100, 'owner/repo', 'windows-x64')

    def test_existing_mac_route_contract_is_preserved(self):
        name = 'ElectricityToolbox-1.0.1-to-1.0.2-apple-silicon.patch.tar.xz'
        edge = dict(base='1.0.1', target='1.0.2', name=name, size=10, digest='sha256:'+'a'*64,
            browser_download_url=f'https://github.com/owner/repo/releases/download/v1.0.2/{name}')
        self.assertEqual(select_route(dict(format=1, version='1.0.2', patches=[edge]),
            '1.0.1', '1.0.2', 100, 'owner/repo'), [edge])


class PatchTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows extended-length path behavior')
    def test_long_staging_paths_restore_with_links_and_copy_fallback(self):
        padding = 240 - len(str(self.old / 'payload/runtime/library.bin')) - 1
        self.assertGreater(padding, 0)
        relative = Path('payload/runtime') / ('x' * padding) / 'library.bin'
        changed = relative.with_name('changed.bin')
        for root, version in ((self.old, b'old'), (self.new, b'new')):
            (root / relative).parent.mkdir(parents=True)
            (root / relative).write_bytes(b'shared long-path runtime')
            (root / changed).write_bytes(version)
        archive = self.patch()
        before = self.delta.inventory(self.old)
        for copy_only in (False, True):
            with self.subTest(copy_only=copy_only):
                with patch('windows_delta.os.link', side_effect=OSError('unsupported')) if copy_only else patch('windows_delta.os.link', wraps=os.link):
                    prepared = self.delta.prepare_update(self.old,
                        [(archive, '1.0.1', '1.0.2')], '1.0.2', self.root)
                try:
                    self.assertGreater(len(str(prepared.staging_dir / relative)), 260)
                    self.assertEqual(self.delta.inventory(prepared.staging_dir), self.delta.inventory(self.new))
                    self.assertEqual(self.delta.inventory(self.old), before)
                    extended = Path('\\\\?\\' + str(prepared.staging_dir))
                    self.assertEqual((extended / changed).read_bytes(), b'new')
                    self.assertEqual(os.path.samefile(self.old / relative, extended / relative), not copy_only)
                finally:
                    self.delta.cleanup_stage(prepared.staging_dir, self.old, prepared.token)

    def test_copy_fallback_preserves_old_bytes_without_hardlink_support(self):
        archive=self.patch();stage=self.root/'copy-only'
        with patch('windows_delta.os.link',side_effect=OSError('not supported')):
            counts=self.delta.clone_installation(self.old,stage)
        self.assertEqual(counts['linked_files'],0)
        self.assertGreater(counts['copied_bytes'],100000)
        self.delta.apply_patch(stage,archive,'1.0.1','1.0.2')
        self.assertEqual(self.delta.inventory(stage),self.delta.inventory(self.new))
        self.assertEqual(self.delta.install_version(self.old),'1.0.1')

    def test_nonmanaged_unsafe_extras_and_reparse_metadata_are_rejected(self):
        from types import SimpleNamespace
        import stat
        candidate=self.root/'plain'
        with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)):
            with self.assertRaises(UpdateError):self.delta._plain(candidate)
        with self.assertRaises(UpdateError):self.delta.windows_name('payload/e\u0301/../x')
        entries=self.delta.inventory(self.old)
        entries['payload/é']={'type':'dir'};entries['payload/e\u0301']={'type':'dir'}
        with self.assertRaises(UpdateError):self.delta.validate_inventory(entries)

    def setUp(self):
        import windows_delta
        self.delta = windows_delta
        self.temp = tempfile.TemporaryDirectory(prefix='Windows 增量 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.application('old', '1.0.1')
        self.new = self.application('new', '1.0.2')

    def application(self, name, version):
        root = self.root / name
        (root / 'payload/runtime').mkdir(parents=True)
        (root / 'payload/app').mkdir()
        (root / 'payload/manifest.json').write_text(json.dumps(dict(version=version)), encoding='utf-8')
        (root / 'electricity-toolbox.exe').write_bytes(b'MZhost-' + version.encode())
        (root / 'WebView2Loader.dll').write_bytes(b'stable loader')
        (root / 'payload/runtime/stable.bin').write_bytes(b'unchanged runtime' * 10000)
        (root / 'payload/app/业务.py').write_text(f'VERSION={version!r}', encoding='utf-8')
        (root / 'uninstall.exe').write_bytes(b'local generated uninstaller ' + version.encode())
        return root

    def patch(self):
        path = self.root / self.delta.patch_name('1.0.1', '1.0.2')
        edge = self.delta.create_patch(self.old, self.new, path)
        self.assertLess(edge['size'], 30000)
        self.assertEqual(edge['digest'], 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def rewrite(self, patch, manifest=None, extra=None, corrupt=None):
        with tarfile.open(patch, 'r:xz') as archive:
            records = [(member.name, archive.extractfile(member).read()) for member in archive]
        changed = self.root / 'changed.patch.tar.xz'
        with tarfile.open(changed, 'w:xz') as archive:
            for name, raw in records:
                if name == 'manifest.json' and manifest:
                    data = json.loads(raw)
                    manifest(data)
                    raw = json.dumps(data).encode()
                if name == corrupt:
                    raw = b'!' * len(raw)
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
            if extra:
                member = tarfile.TarInfo(extra)
                member.size = 1
                archive.addfile(member, io.BytesIO(b'!'))
        return changed

    def test_roundtrip_changes_removals_empty_dirs_and_type_changes(self):
        (self.old / 'payload/app/remove').write_text('remove')
        (self.old / 'payload/app/switch').write_text('file')
        (self.new / 'payload/app/switch').mkdir()
        (self.new / 'payload/app/switch/child').write_text('child')
        (self.new / 'payload/empty').mkdir()
        (self.old / 'payload/app/dir-to-file').mkdir()
        (self.old / 'payload/app/dir-to-file/child').write_text('old')
        (self.new / 'payload/app/dir-to-file').write_text('new')
        archive = self.patch()
        stage = self.root / 'restored'
        self.delta.clone_installation(self.old, stage)
        self.delta.apply_patch(stage, archive, '1.0.1', '1.0.2')
        self.assertEqual(self.delta.inventory(stage), self.delta.inventory(self.new))
        self.assertEqual((stage / 'uninstall.exe').read_bytes(), (self.new / 'uninstall.exe').read_bytes())
        self.assertEqual(self.delta.install_version(self.old), '1.0.1')
        self.assertTrue((self.old / 'payload/app/remove').is_file())

    def test_hardlinked_changes_never_modify_the_running_installation(self):
        archive = self.patch()
        before = self.delta.inventory(self.old)
        stage = self.root / 'linked'
        self.delta.clone_installation(self.old, stage)
        stable = 'payload/runtime/stable.bin'
        self.assertTrue(os.path.samefile(self.old / stable, stage / stable))
        self.delta.apply_patch(stage, archive, '1.0.1', '1.0.2')
        self.assertTrue(os.path.samefile(self.old / stable, stage / stable))
        self.assertFalse(os.path.samefile(self.old / 'electricity-toolbox.exe', stage / 'electricity-toolbox.exe'))
        self.assertEqual(self.delta.inventory(self.old), before)

    def test_modified_base_is_rejected_before_applying_the_patch(self):
        archive = self.patch()
        (self.old / 'payload/runtime/stable.bin').write_bytes(b'user modified')
        stage = self.root / 'changed-base'
        self.delta.clone_installation(self.old, stage)
        with self.assertRaisesRegex(UpdateError, '变化|不匹配'):
            self.delta.apply_patch(stage, archive, '1.0.1', '1.0.2')

    def test_additional_source_backed_bytecode_is_removed_only_from_the_stage(self):
        archive = self.patch()
        cache = self.old / 'payload/app/__pycache__'
        cache.mkdir()
        bytecode = cache / '业务.cpython-312.pyc'
        bytecode.write_bytes(b'generated cache')
        before = self.delta.inventory(self.old)
        prepared = self.delta.prepare_update(self.old, [(archive, '1.0.1', '1.0.2')], '1.0.2', self.root)
        self.addCleanup(lambda: self.delta.cleanup_stage(prepared.staging_dir, self.old, prepared.token))
        self.assertEqual(self.delta.inventory(prepared.staging_dir), self.delta.inventory(self.new))
        self.assertFalse((prepared.staging_dir / 'payload/app/__pycache__').exists())
        self.assertEqual(self.delta.inventory(self.old), before)
        self.assertEqual(bytecode.read_bytes(), b'generated cache')
        plan = json.loads(prepared.manifest.read_text(encoding='utf-8'))
        self.assertEqual(plan['before'], before)
        self.assertEqual(plan['after'], self.delta.inventory(self.new))

    def test_added_cache_never_hides_modified_managed_code_or_unknown_files(self):
        archive = self.patch()
        for name in ('payload/app/__pycache__/orphan.cpython-312.pyc',
                     'payload/app/__pycache__/业务.txt',
                     'payload/app/业务.pyc'):
            with self.subTest(name=name):
                stage = self.root / ('unsafe-cache-' + str(len(list(self.root.iterdir()))))
                self.delta.clone_installation(self.old, stage)
                path = stage / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'unknown extra')
                with self.assertRaises(UpdateError):
                    self.delta.apply_patch(stage, archive, '1.0.1', '1.0.2')
        stage = self.root / 'modified-source-and-cache'
        self.delta.clone_installation(self.old, stage)
        (stage / 'payload/app/业务.py').write_bytes(b'changed code')
        (stage / 'payload/app/__pycache__').mkdir()
        (stage / 'payload/app/__pycache__/业务.cpython-312.pyc').write_bytes(b'cache')
        with self.assertRaises(UpdateError):
            self.delta.apply_patch(stage, archive, '1.0.1', '1.0.2')

    def test_archive_tampering_and_unsafe_entries_are_rejected(self):
        archive = self.patch()
        for extra in ('files/../escape', 'files/C:/bad', 'files/payload/app/CON.txt',
                      'files/payload/app/a:stream', 'files/payload/app/trailing.', 'files/payload/app/new'):
            with self.subTest(extra=extra):
                changed = self.rewrite(archive, extra=extra)
                stage = self.root / ('bad-' + str(len(list(self.root.iterdir()))))
                self.delta.clone_installation(self.old, stage)
                with self.assertRaises(UpdateError):
                    self.delta.apply_patch(stage, changed, '1.0.1', '1.0.2')
        changed = self.rewrite(archive, corrupt='files/electricity-toolbox.exe')
        stage = self.root / 'corrupted'
        self.delta.clone_installation(self.old, stage)
        with self.assertRaises(UpdateError):
            self.delta.apply_patch(stage, changed, '1.0.1', '1.0.2')
        self.assertTrue((self.old / 'electricity-toolbox.exe').read_bytes().startswith(b'MZhost'))

    def test_case_collisions_and_missing_parents_are_rejected(self):
        entries = self.delta.inventory(self.old)
        for device in ('COM¹.txt','LPT².bin','NUL .txt'):
            with self.assertRaises(UpdateError):self.delta.windows_name('payload/'+device)
        for change in (lambda x: x.update({'PAYLOAD': {'type': 'dir'}}),
                       lambda x: x.pop('payload'),
                       lambda x: x.update({'payload/NUL.bin': {'type': 'file', 'size': 0, 'sha256': 'a'*64}})):
            sample = copy.deepcopy(entries)
            change(sample)
            with self.assertRaises(UpdateError):
                self.delta.validate_inventory(sample)

    def test_cancelled_preparation_removes_only_owned_stage(self):
        archive = self.patch()
        cache = self.root / 'cache'
        cache.mkdir()
        cancel = threading.Event()
        def progress(_fraction):
            cancel.set()
        with self.assertRaises(Cancelled):
            self.delta.prepare_update(self.old, [(archive, '1.0.1', '1.0.2')],
                '1.0.2', cache, cancel, progress)
        self.assertFalse(list(self.root.glob('.electricity-toolbox-stage-*')))
        self.assertEqual(self.delta.install_version(self.old), '1.0.1')

    def test_prepared_metadata_binds_complete_base_and_target(self):
        archive = self.patch()
        cache = self.root / 'cache'
        cache.mkdir()
        prepared = self.delta.prepare_update(self.old, [(archive, '1.0.1', '1.0.2')],
            '1.0.2', cache, threading.Event(), lambda _: None)
        self.addCleanup(lambda: self.delta.cleanup_stage(prepared.staging_dir, self.old, prepared.token))
        meta = json.loads(prepared.manifest.read_text(encoding='utf-8'))
        self.assertEqual(meta['before'], self.delta.inventory(self.old))
        self.assertEqual(meta['after'], self.delta.inventory(self.new))
        self.assertEqual(prepared.staging_dir.name, '.electricity-toolbox-stage-' + prepared.token)
        self.assertEqual(prepared.staging_dir.parent, self.old.parent.resolve())
        self.assertGreater(prepared.linked_bytes, 100000)
        with self.assertRaises(UpdateError):
            self.delta.cleanup_stage(self.new, self.old, prepared.token)
        self.assertTrue(self.new.is_dir())


if __name__ == '__main__':
    unittest.main()
