"""Release artifacts must reconstruct a real tree and retain historical routes."""
import importlib.util
import json
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

if __name__=='__main__':unittest.main()
