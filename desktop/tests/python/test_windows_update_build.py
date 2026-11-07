"""Release artifacts must reconstruct a real tree and retain historical routes."""
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'desktop/python'))
spec=importlib.util.spec_from_file_location('windows_update_build',ROOT/'packaging/windows/build_updates.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class BuildTests(unittest.TestCase):
    def tree(self,folder,version):
        root=folder/version;root.mkdir();(root/'payload').mkdir()
        (root/'electricity-toolbox.exe').write_bytes(b'MZ'+version.encode())
        (root/'payload/manifest.json').write_text(json.dumps(dict(version=version)))
        (root/'payload/runtime').write_bytes(b'unchanged-runtime'*1000)
        return root

    def test_build_verifies_three_old_trees_and_preserves_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);roots=[self.tree(folder,v) for v in ('1.0.1','1.0.2','1.0.3')]
            new=self.tree(folder,'1.0.4');output=folder/'output';output.mkdir()
            (output/'ElectricityToolbox_1.0.4_windows_x64_setup.exe').write_bytes(b'full'*10000)
            assets=builder.build(new,output,base_roots=roots,fetch_history=False)
            index=json.loads((assets/builder.INDEX_NAME).read_text())
            self.assertEqual(len(index['patches']),3)
            self.assertEqual(index['platform'],'windows-x64')
            for root in roots:self.assertEqual(builder.install_version(root),root.name)
            for edge in index['patches']:
                self.assertLess(edge['size'],40000)
                self.assertEqual('sha256:'+builder.digest(assets/edge['name']),edge['digest'])
                self.assertTrue((assets/(edge['name']+'.sha256')).is_file())
            with self.assertRaises(FileExistsError):builder.build(new,output,fetch_history=False)

    def test_full_only_resets_history_and_minor_change_excludes_old_baselines(self):
        for full_only in (False,True):
            with self.subTest(full_only=full_only),tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp);old=self.tree(folder,'1.0.9');new=self.tree(folder,'1.1.0')
                (folder/'ElectricityToolbox_1.1.0_windows_x64_setup.exe').write_bytes(b'full'*1000)
                with patch.object(builder,'gh',side_effect=AssertionError('no history in full-only')):
                    assets=builder.build(new,folder,full_only=full_only,base_roots=[old],fetch_history=full_only)
                self.assertEqual(json.loads((assets/builder.INDEX_NAME).read_text())['patches'],[])

    def test_history_is_retained_at_original_release_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);old=self.tree(folder,'1.0.2');new=self.tree(folder,'1.0.3')
            (folder/'ElectricityToolbox_1.0.3_windows_x64_setup.exe').write_bytes(b'full'*1000)
            name=builder.patch_name('1.0.1','1.0.2')
            edge=dict(base='1.0.1',target='1.0.2',name=name,size=10,digest='sha256:'+'a'*64,
                browser_download_url=f'https://github.com/{builder.REPOSITORY}/releases/download/v1.0.2/{name}')
            history=folder/'history.json';history.write_text(json.dumps(dict(format=1,version='1.0.2',platform='windows-x64',patches=[edge])))
            releases=[dict(tag_name='v1.0.2',draft=False,prerelease=False,assets=[dict(name=builder.INDEX_NAME)])]
            with patch.object(builder,'gh',return_value=json.dumps(releases)),patch.object(builder,'fetch_asset',return_value=history):
                assets=builder.build(new,folder,base_roots=[old])
            index=json.loads((assets/builder.INDEX_NAME).read_text())
            self.assertEqual(index['patches'][0],edge)
            self.assertEqual(len(index['patches']),2)

    def test_releases_reads_public_api_when_gh_is_missing(self):
        payload=[dict(tag_name='v1.0.1')]
        with patch.object(builder,'gh',side_effect=FileNotFoundError('gh missing')) as cli, \
                patch.object(builder,'api',return_value=payload) as http:
            self.assertEqual(builder.releases(),payload)
        cli.assert_called_once()
        http.assert_called_once_with(f'/repos/{builder.REPOSITORY}/releases?per_page=100')

    def test_missing_gh_and_no_history_still_builds_from_local_installers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);old=self.tree(folder,'1.0.3');new=self.tree(folder,'1.0.4')
            output=folder/'output';output.mkdir()
            (output/'ElectricityToolbox_1.0.3_windows_x64_setup.exe').write_bytes(b'MZold')
            (output/'ElectricityToolbox_1.0.4_windows_x64_setup.exe').write_bytes(b'full'*1000)
            used=[]
            @contextmanager
            def fake_installed(path):
                used.append(Path(path).name);yield old
            with patch.object(builder,'gh',side_effect=FileNotFoundError('gh missing')), \
                    patch.object(builder,'api',return_value=[]), \
                    patch.object(builder,'installed',fake_installed):
                assets=builder.build(new,output)
            index=json.loads((assets/builder.INDEX_NAME).read_text())
            self.assertEqual([edge['base'] for edge in index['patches']],['1.0.3'])
            self.assertEqual(used,['ElectricityToolbox_1.0.3_windows_x64_setup.exe'])
            for path in (assets/builder.INDEX_NAME,assets/(builder.INDEX_NAME+'.sha256')):
                self.assertTrue(path.is_file())

    def test_unusable_history_never_aborts_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);old=self.tree(folder,'1.0.3');new=self.tree(folder,'1.0.4')
            (folder/'ElectricityToolbox_1.0.4_windows_x64_setup.exe').write_bytes(b'full'*1000)
            with patch.object(builder,'gh',side_effect=FileNotFoundError('gh missing')), \
                    patch.object(builder,'api',side_effect=builder.UpdateError('离线')), \
                    patch.object(builder,'installed',side_effect=AssertionError('must not install')):
                assets=builder.build(new,folder,base_roots=[old],fetch_history=True)
            index=json.loads((assets/builder.INDEX_NAME).read_text())
            self.assertEqual([edge['base'] for edge in index['patches']],['1.0.3'])

    def test_base_installer_names_are_validated(self):
        for name in ('ElectricityToolbox_1.0.3_setup.exe','wrong_1.0.3_windows_x64_setup.exe',
                     'ElectricityToolbox_x_windows_x64_setup.exe'):
            with self.subTest(name=name),self.assertRaises(builder.UpdateError):
                builder.installer_version(name)
        self.assertEqual(builder.installer_version('ElectricityToolbox_1.0.3_windows_x64_setup.exe'),'1.0.3')

    def test_published_release_keeps_its_slot_over_unpublished_local_installers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);new=self.tree(folder,'1.0.5')
            output=folder/'output';output.mkdir()
            (output/'ElectricityToolbox_1.0.5_windows_x64_setup.exe').write_bytes(b'full'*1000)
            for version in ('1.0.2','1.0.3','1.0.4'):
                (output/f'ElectricityToolbox_{version}_windows_x64_setup.exe').write_bytes(b'MZ'+version.encode())
            published=folder/'published';published.mkdir()
            pub=published/'ElectricityToolbox_1.0.1_windows_x64_setup.exe';pub.write_bytes(b'MZpublished')
            trees={'ElectricityToolbox_1.0.1_windows_x64_setup.exe':self.tree(folder,'1.0.1'),
                   'ElectricityToolbox_1.0.4_windows_x64_setup.exe':self.tree(folder,'1.0.4'),
                   'ElectricityToolbox_1.0.3_windows_x64_setup.exe':self.tree(folder,'1.0.3')}
            @contextmanager
            def fake_installed(path):
                yield trees[Path(path).name]
            release=dict(tag_name='v1.0.1',draft=False,prerelease=False,
                assets=[dict(name='ElectricityToolbox_1.0.1_windows_x64_setup.exe')])
            with patch.object(builder,'gh',side_effect=FileNotFoundError('gh missing')), \
                    patch.object(builder,'api',return_value=[release]), \
                    patch.object(builder,'fetch_asset',return_value=pub), \
                    patch.object(builder,'installed',fake_installed):
                assets=builder.build(new,output)
            bases={edge['base'] for edge in json.loads((assets/builder.INDEX_NAME).read_text())['patches']}
            self.assertEqual(bases,{'1.0.1','1.0.3','1.0.4'})

if __name__=='__main__':unittest.main()
