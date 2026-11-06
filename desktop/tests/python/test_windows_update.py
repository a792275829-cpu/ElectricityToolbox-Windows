"""Windows full-installer channel; no live releases, credentials or user data."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'desktop/python'),str(ROOT/'电力工具箱')]
from github_update import Cancelled, UpdateError
from windows_update import WindowsUpdater, WINDOWS_REPOSITORY, parse_windows_release

PAYLOAD=b'MZverified-windows-installer'
def metadata(version='0.1.22'):
    name=f'ElectricityToolbox_{version}_windows_x64_setup.exe'
    return dict(tag_name='v'+version,draft=False,prerelease=False,body='Windows 更新说明',
        html_url=f'https://github.com/{WINDOWS_REPOSITORY}/releases/tag/v{version}',
        assets=[dict(name=name,size=len(PAYLOAD),digest='sha256:'+hashlib.sha256(PAYLOAD).hexdigest(),
                     browser_download_url=f'https://github.com/{WINDOWS_REPOSITORY}/releases/download/v{version}/{name}')])

class Response(io.BytesIO):
    status=200
    def __init__(self,data,url='',headers=None):super().__init__(data);self.url=url;self.headers=headers or {}
    def geturl(self):return self.url

class WindowsChannelTests(unittest.TestCase):
    def test_short_http_download_resumes_and_ignored_range_restarts_safely(self):
        import http.server
        import urllib.request
        content=b'MZ'+bytes(range(256))*4096
        for ignore_range in (False,True):
            with self.subTest(ignore_range=ignore_range),tempfile.TemporaryDirectory() as folder:
                requests=[]
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_GET(self):
                        requested=self.headers.get('Range');requests.append(requested)
                        start=int(requested.split('=')[1].split('-')[0]) if requested and not ignore_range else 0
                        self.send_response(206 if start else 200)
                        self.send_header('Content-Length',str(len(content)-start))
                        if start:self.send_header('Content-Range',f'bytes {start}-{len(content)-1}/{len(content)}')
                        self.end_headers()
                        if len(requests)==1:
                            self.wfile.write(content[:300000]);self.wfile.flush();self.close_connection=True
                        else:self.wfile.write(content[start:])
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
                worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                try:
                    def opener(request,timeout):
                        return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}/file',headers=dict(request.header_items())),timeout=timeout)
                    updater=WindowsUpdater('0.1.21',opener=opener);asset=metadata()['assets'][0]
                    asset.update(size=len(content),digest='sha256:'+hashlib.sha256(content).hexdigest())
                    result=updater.download_asset(asset,Path(folder)/'file.exe',threading.Event(),lambda _:None)
                    self.assertEqual(result.read_bytes(),content)
                    self.assertEqual(requests[0],None)
                    self.assertTrue(requests[1].startswith('bytes='))
                finally:server.shutdown();server.server_close();worker.join()

    def delta_fixture(self, directory):
        import windows_delta as delta
        import shutil
        old=Path(directory)/'installed';old.mkdir();(old/'payload').mkdir()
        (old/'electricity-toolbox.exe').write_bytes(b'MZ old host')
        (old/'payload/manifest.json').write_text('{"version":"0.1.21"}')
        (old/'payload/runtime.bin').write_bytes(b'unchanged'*1000)
        new=Path(directory)/'new';shutil.copytree(old,new)
        (new/'payload/manifest.json').write_text('{"version":"0.1.22"}')
        name=delta.patch_name('0.1.21','0.1.22')
        edge=delta.create_patch(old,new,Path(directory)/name)
        edge['browser_download_url']=f'https://github.com/{WINDOWS_REPOSITORY}/releases/download/v0.1.22/{name}'
        index=json.dumps(dict(format=1,version='0.1.22',platform=delta.PLATFORM,patches=[edge])).encode()
        data=metadata();data['assets'][0]['size']=1000000
        data['assets'].append(dict(name=delta.INDEX_NAME,size=len(index),digest='sha256:'+hashlib.sha256(index).hexdigest(),
            browser_download_url=f'https://github.com/{WINDOWS_REPOSITORY}/releases/download/v0.1.22/{delta.INDEX_NAME}'))
        payloads={name:(Path(directory)/name).read_bytes(),delta.INDEX_NAME:index}
        return old,data,payloads

    def test_delta_download_prepares_small_update_and_preserves_live_tree(self):
        from windows_delta import cleanup_stage
        with tempfile.TemporaryDirectory() as folder:
            old,data,payloads=self.delta_fixture(folder)
            def opener(request,timeout):return Response(payloads[request.full_url.rsplit('/',1)[-1]])
            updater=WindowsUpdater('0.1.21',opener=opener)
            with patch.dict(os.environ,{'TOOLBOX_INSTALL_ROOT':str(old)}):
                path=updater.download(parse_windows_release(data,'0.1.21'),threading.Event(),lambda _:None)
            self.addCleanup(lambda:__import__('shutil').rmtree(path.parent,ignore_errors=True))
            self.addCleanup(cleanup_stage,updater._prepared.staging_dir,old,updater._prepared.token)
            self.assertEqual(path.name,'delta-plan.json')
            self.assertEqual(updater.update_mode,'delta')
            self.assertLess(updater.download_bytes,100000)
            self.assertEqual(json.loads((old/'payload/manifest.json').read_text())['version'],'0.1.21')
            self.assertEqual(json.loads((updater._prepared.staging_dir/'payload/manifest.json').read_text())['version'],'0.1.22')

    def test_corrupt_delta_falls_back_to_full_but_cancel_never_does(self):
        with tempfile.TemporaryDirectory() as folder:
            old,data,payloads=self.delta_fixture(folder)
            data['assets'][0].update(size=len(PAYLOAD))
            # Keep the delta smaller than the full test artifact through a mocked route.
            release=parse_windows_release(data,'0.1.21');calls=[]
            def opener(request,timeout):
                name=request.full_url.rsplit('/',1)[-1];calls.append(name)
                return Response(PAYLOAD if name.endswith('.exe') else b'corrupt')
            updater=WindowsUpdater('0.1.21',opener=opener)
            with patch.dict(os.environ,{'TOOLBOX_INSTALL_ROOT':str(old)}):
                path=updater.download(release,threading.Event(),lambda _:None)
                self.addCleanup(lambda:__import__('shutil').rmtree(path.parent,ignore_errors=True))
                self.assertEqual(path.read_bytes(),PAYLOAD)
                self.assertTrue(updater.fallback_reason)
                calls.clear();cancel=threading.Event();cancel.set()
                with self.assertRaises(Cancelled):updater.download(release,cancel,lambda _:None)
                self.assertFalse(any(name.endswith('.exe') for name in calls))

    def test_optional_index_does_not_break_full_channel_when_invalid(self):
        data=metadata();data['assets'].append(dict(name='windows-update-manifest.json',size=True))
        self.assertIsNone(parse_windows_release(data,'0.1.21').update_asset)

    def test_retired_account_release_is_not_accepted_by_migration_channel(self):
        data=metadata('0.1.25')
        retired='Qiaozhang13/electricity-toolbox-rust-windows'
        data['html_url']=f'https://github.com/{retired}/releases/tag/v0.1.25'
        name=data['assets'][0]['name']
        data['assets'][0]['browser_download_url']=f'https://github.com/{retired}/releases/download/v0.1.25/{name}'
        with self.assertRaises(UpdateError):
            parse_windows_release(data,'0.1.24')

    def test_channel_accepts_only_exact_windows_asset_from_our_repository(self):
        release=parse_windows_release(metadata(),'0.1.21')
        self.assertEqual(release.asset['name'],'ElectricityToolbox_0.1.22_windows_x64_setup.exe')
        self.assertIsNone(release.update_asset)
        for change in (dict(draft=True),dict(prerelease=True),dict(tag_name='v0.1.21')):
            data=metadata();data.update(change)
            self.assertIsNone(parse_windows_release(data,'0.1.21'))

    def test_invalid_asset_is_rejected_before_download(self):
        for field,value in [('name','bad.dmg'),('size',True),('size',0),('digest',''),
                            ('browser_download_url','https://github.com/evil/repo/releases/download/v0.1.22/setup.exe')]:
            data=metadata();data['assets'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(UpdateError):parse_windows_release(data,'0.1.21')
        data=metadata();data['assets']*=2
        with self.assertRaises(UpdateError):parse_windows_release(data,'0.1.21')

    def test_checks_actual_windows_channel_then_verifies_download(self):
        calls=[]
        def opener(request,timeout):
            calls.append(request.full_url)
            return Response(json.dumps(metadata()).encode() if request.full_url.endswith('/latest') else PAYLOAD)
        updater=WindowsUpdater('0.1.21',opener=opener)
        release=updater.check()
        self.assertEqual(calls[0],f'https://api.github.com/repos/{WINDOWS_REPOSITORY}/releases/latest')
        path=updater.download(release,threading.Event(),lambda _:None)
        self.addCleanup(lambda: __import__('shutil').rmtree(path.parent,ignore_errors=True))
        self.assertEqual(path.read_bytes(),PAYLOAD)
        self.assertTrue(path.name.endswith('.exe'))

    def test_rate_limit_fallback_reads_only_verified_windows_asset(self):
        data=metadata();asset=data['assets'][0];calls=[]
        base=f'https://github.com/{WINDOWS_REPOSITORY}/releases'
        def opener(request,timeout):
            calls.append((request.full_url,request.get_method()))
            if request.full_url.startswith('https://api.github.com/'):
                raise urllib.error.HTTPError(request.full_url,403,'rate limit',{},None)
            if request.full_url==base+'/latest':
                return Response(b'<div class="markdown-body">Windows notes</div>',data['html_url'])
            if request.full_url==base+'/expanded_assets/v0.1.22':
                html=f'<li><a href="/{WINDOWS_REPOSITORY}/releases/download/v0.1.22/{asset["name"]}">installer</a><clipboard-copy value="{asset["digest"]}"></clipboard-copy></li>'
                return Response(html.encode(),request.full_url)
            if request.full_url==asset['browser_download_url'] and request.get_method()=='HEAD':
                return Response(b'',request.full_url,{'Content-Length':str(len(PAYLOAD))})
            raise AssertionError(request.full_url)
        release=WindowsUpdater('0.1.21',opener=opener).check()
        self.assertEqual(release.asset,asset)
        self.assertTrue(all('apple' not in url for url,_ in calls))

    def test_corrupt_or_cancelled_download_leaves_no_installer(self):
        release=parse_windows_release(metadata(),'0.1.21')
        updater=WindowsUpdater('0.1.21',opener=lambda *a,**kw:Response(PAYLOAD[:-1]+b'!'))
        with self.assertRaisesRegex(UpdateError,'校验'):updater.download(release,threading.Event(),lambda _:None)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(Cancelled):updater.download(release,cancel,lambda _:None)

    def test_source_launch_cannot_install_and_busy_service_cannot_be_reserved(self):
        updater=WindowsUpdater('0.1.21')
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(UpdateError,'安装'):updater.prepare_update()

    def test_abort_releases_only_our_maintenance_reservation(self):
        from unittest.mock import Mock
        client=Mock();updater=WindowsUpdater('0.1.21',client)
        with patch.dict(os.environ,{'TOOLBOX_INSTALLED':'1'}):updater.prepare_update()
        updater.abort_update()
        self.assertEqual([call.args[0] for call in client.call.call_args_list],['prepare_update','abort_update'])

    def test_previous_helper_failure_is_shown_once(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TOOLBOX_APP_HOME':directory}):
            path=Path(directory)/'state/windows-update.json';path.parent.mkdir()
            path.write_text(json.dumps(dict(state='failed',error='安装失败；已恢复旧程序')),encoding='utf-8')
            updater=WindowsUpdater('0.1.21')
            self.assertIn('已恢复旧程序',updater.take_install_error())
            self.assertIsNone(updater.take_install_error())

    def test_failure_for_an_already_installed_version_is_archived_without_a_dialog(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TOOLBOX_APP_HOME':directory}):
            path=Path(directory)/'state/windows-update.json';path.parent.mkdir()
            for target in ('0.1.28','0.1.29'):
                with self.subTest(target=target):
                    path.write_text(json.dumps(dict(state='failed',version=target,error='old failure')),encoding='utf-8')
                    self.assertIsNone(WindowsUpdater('0.1.29').take_install_error())
                    saved=json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual(saved['error'],'old failure')
                    self.assertTrue(saved['notified'])
                    self.assertEqual(saved['superseded_by'],'0.1.29')

    def test_failed_newer_version_still_reports_the_error(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TOOLBOX_APP_HOME':directory}):
            path=Path(directory)/'state/windows-update.json';path.parent.mkdir()
            path.write_text(json.dumps(dict(state='failed',version='0.1.30',error='new failure')),encoding='utf-8')
            updater=WindowsUpdater('0.1.29')
            self.assertEqual(updater.take_install_error(),'new failure')
            self.assertIsNone(updater.take_install_error())

    def test_successful_update_cache_is_removed_without_touching_other_files(self):
        with tempfile.TemporaryDirectory() as home,tempfile.TemporaryDirectory(prefix='ElectricityToolboxWindowsUpdate-') as cache:
            root=Path(cache);version='0.1.22'
            (root/installer_name_for_test(version)).write_bytes(PAYLOAD)
            (root/'toolbox-update.exe').write_bytes(b'helper')
            (root/'plan.json').write_text(json.dumps(dict(app_home=home,version=version)),encoding='utf-8')
            state=Path(home)/'state/windows-update.json';state.parent.mkdir()
            state.write_text(json.dumps(dict(state='succeeded',version=version,cache=cache)),encoding='utf-8')
            with patch.dict(os.environ,{'TOOLBOX_APP_HOME':home}):WindowsUpdater('0.1.22').cleanup_completed_download()
            self.assertFalse(root.exists())
            with tempfile.TemporaryDirectory(prefix='ElectricityToolboxWindowsUpdate-') as foreign:
                candidate=Path(foreign)
                (candidate/'plan.json').write_text(json.dumps(dict(app_home=home,version=version)),encoding='utf-8')
                (candidate/'keep-user-file.txt').write_text('KEEP')
                state.write_text(json.dumps(dict(state='succeeded',version=version,cache=foreign)),encoding='utf-8')
                with patch.dict(os.environ,{'TOOLBOX_APP_HOME':home}):
                    self.assertFalse(WindowsUpdater('0.1.22').cleanup_completed_download())
                self.assertEqual((candidate/'keep-user-file.txt').read_text(),'KEEP')

def installer_name_for_test(version):
    return f'ElectricityToolbox_{version}_windows_x64_setup.exe'

class SharedVersionTests(unittest.TestCase):
    def test_invalid_host_version_uses_installed_metadata_then_unknown(self):
        import toolbox_version
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TOOLBOX_APP_VERSION':'bad\nversion'}):
            root=Path(directory);state=root/'state/installed.json';state.parent.mkdir()
            state.write_text('{"version":"0.1.22"}')
            with patch.object(toolbox_version,'__file__',str(root/'app/toolbox_version.py')):
                self.assertEqual(toolbox_version.application_version(),'0.1.22')
                state.write_text('{"version":false}')
                self.assertEqual(toolbox_version.application_version(),'未知')

    def test_native_bridge_selects_windows_updater_and_preserves_mac_remote(self):
        from unittest.mock import Mock
        import native_bridge
        from remote_updater import RemoteUpdater
        with patch('native_bridge.sys.platform','win32'):
            self.assertIsInstance(native_bridge.create_updater(Mock()),WindowsUpdater)
        with patch('native_bridge.sys.platform','darwin'):
            self.assertIsInstance(native_bridge.create_updater(Mock()),RemoteUpdater)

    def test_installed_log_and_status_bar_use_host_version(self):
        import tkinter as tk
        from toolbox_log_console import LogConsole
        from toolbox.app import application_version
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'TOOLBOX_APP_VERSION':'0.1.22'}):
            root=tk.Tk();root.withdraw()
            try:
                console=LogConsole(root,module='验收',directory=Path(folder))
                console.begin()
                self.assertIn('版本：0.1.22',console.contents())
                self.assertEqual(application_version(),'版本 v0.1.22')
            finally:root.destroy()

if __name__=='__main__':unittest.main()
