"""Exercise the actual detached Rust helper with disposable tiny NSIS fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'desktop/python'))
SERVICE=Path(os.environ.get('TOOLBOX_SERVICE_BINARY',ROOT/'电力工具箱/bin/toolbox-service.exe'))
MAKENSIS=Path(os.environ.get('LOCALAPPDATA',''))/'tauri/NSIS/makensis.exe'

@unittest.skipUnless(os.name=='nt' and SERVICE.is_file() and MAKENSIS.is_file(),'Windows native helper and NSIS')
class WindowsHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=tempfile.TemporaryDirectory(prefix='update-nsis-')
        folder=Path(cls.fixtures.name)
        cls.installers={}
        sleeper=folder/'sleeping-app.exe'
        sleeper_script=folder/'sleeping-app.nsi'
        sleeper_script.write_text('Unicode true\nRequestExecutionLevel user\nSilentInstall silent\nSetCompress off\n'
            f'OutFile "{sleeper}"\nSection\nSleep 20000\nSectionEnd\n',encoding='utf-8-sig')
        subprocess.run([str(MAKENSIS),str(sleeper_script)],check=True,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        for name in ('good','fail','badapp','hold'):
            fail=name=='fail'
            manifest=folder/(name+'.json');manifest.write_text(json.dumps({'version':'9.0.1'}))
            installer=folder/(name+'.exe')
            script=folder/(name+'.nsi')
            binary=SERVICE
            if name=='hold':binary=sleeper
            if name=='badapp':
                binary=folder/'not-a-program.exe';binary.write_bytes(b'MZinvalid')
            script.write_text('Unicode true\nRequestExecutionLevel user\nSilentInstall silent\nSetCompress off\n'
                f'OutFile "{installer}"\nSection\nSetOutPath "$INSTDIR"\n'
                f'File "/oname=electricity-toolbox.exe" "{binary}"\n'
                f'SetOutPath "$INSTDIR\\payload"\nFile "/oname=manifest.json" "{manifest}"\n'
                + ('SetErrorLevel 7\nQuit\n' if fail else 'SetErrorLevel 0\n')+'SectionEnd\n',encoding='utf-8-sig')
            result=subprocess.run([str(MAKENSIS),str(script)],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode==0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
            cls.installers[name]=installer
    @classmethod
    def tearDownClass(cls):cls.fixtures.cleanup()

    def setUp(self):
        import shutil
        self.temp=tempfile.TemporaryDirectory(prefix='Windows 升级 空格 ')
        self.root=Path(self.temp.name);self.target=self.root/'安装 & 程序'
        self.target.mkdir();(self.target/'payload').mkdir()
        (self.target/'payload/manifest.json').write_text('{"version":"9.0.0"}')
        shutil.copy2(SERVICE,self.target/'electricity-toolbox.exe')
        self.home=self.root/'用户 数据';self.home.mkdir();(self.home/'keep.txt').write_text('KEEP-DATA')
        self.cache=self.root/'cache';self.cache.mkdir()
        self.processes=[];self.helper=None
    def tearDown(self):
        for child in self.processes:
            if child.poll() is None:child.stdin.close()
            child.wait(timeout=8)
            for stream in (child.stdin,child.stdout,child.stderr):
                if stream and not stream.closed:stream.close()
        if self.helper and self.helper.poll() is None:
            (self.cache/'decision.json').write_text(json.dumps({'token':'test-owned','action':'abort'}))
            self.helper.wait(timeout=8)
        deadline=time.monotonic()+5
        while True:
            try:self.temp.cleanup();break
            except PermissionError:
                if time.monotonic()>=deadline:raise
                time.sleep(.02)
    def children(self):
        for path in (self.target/'electricity-toolbox.exe',SERVICE,SERVICE):
            child=subprocess.Popen([str(path)],cwd=self.root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertIn(b'service-ready',child.stdout.readline())
            self.processes.append(child)
        return [dict(pid=p.pid,path=str(path.resolve())) for p,path in zip(self.processes,(self.target/'electricity-toolbox.exe',SERVICE,SERVICE))]
    def status(self):
        path=self.cache/'status.json'
        try:return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def launch(self,kind='good',**changes):
        import shutil
        installer=self.cache/'installer.exe';shutil.copy2(self.installers[kind],installer)
        plan=dict(schema=1,installer=str(installer),sha256=hashlib.sha256(installer.read_bytes()).hexdigest(),size=installer.stat().st_size,
                  version='9.0.1',target_dir=str(self.target),app_home=str(self.home),processes=self.children(),token='test-owned',wait_timeout_ms=2000)
        plan.update(changes);(self.cache/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        self.helper=subprocess.Popen([str(SERVICE),'--windows-update-helper',str(self.cache/'plan.json')],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+5
        while not self.status() and self.helper.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(self.status().get('state'),'ready',self.status())
        return installer
    def commit(self):
        (self.cache/'decision.json').write_text(json.dumps({'token':'test-owned','action':'commit'}))
        for p in self.processes:p.stdin.close()
        self.helper.wait(timeout=15)

    def test_invalid_plan_is_rejected_instead_of_serving_normal_protocol(self):
        plan=self.cache/'plan.json';plan.write_text('{}')
        result=subprocess.run([str(SERVICE),'--windows-update-helper',str(plan)],input=b'',capture_output=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode,0)

    def test_real_nsis_install_waits_for_owned_processes_and_relaunches(self):
        self.launch()
        (self.cache/'decision.json').write_text(json.dumps({'token':'test-owned','action':'commit'}))
        time.sleep(.15)
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')
        for p in self.processes:p.stdin.close()
        self.helper.wait(timeout=15)
        self.assertEqual(self.status()['state'],'succeeded',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.1')
        self.assertEqual((self.home/'keep.txt').read_text(),'KEEP-DATA')
        self.assertGreater(self.status()['relaunch_pid'],0)

    def test_relaunched_application_does_not_inherit_helper_log(self):
        from unittest.mock import patch
        import signal
        original=subprocess.Popen
        log=self.cache/'helper.log'
        def spawn(*args,**kwargs):
            if '--windows-update-helper' in args[0]:
                with log.open('wb') as stream:
                    return original(*args,**{**kwargs,'stderr':stream})
            return original(*args,**kwargs)
        pid=None
        try:
            with patch('subprocess.Popen',side_effect=spawn):self.launch('hold')
            self.commit()
            self.assertEqual(self.status()['state'],'succeeded',self.status())
            pid=self.status()['relaunch_pid']
            log.unlink()  # Must be removable while the relaunched app is still alive.
            self.assertFalse(log.exists())
        finally:
            if pid:
                try:os.kill(pid,signal.SIGTERM)
                except OSError:pass

    def test_partial_install_failure_restores_old_program_and_data(self):
        self.launch('fail');self.commit()
        self.assertEqual(self.status()['state'],'failed',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')
        self.assertEqual((self.home/'keep.txt').read_text(),'KEEP-DATA')

    def test_changed_installer_cannot_replace_program(self):
        installer=self.launch();installer.write_bytes(b'MZchanged')
        self.commit()
        self.assertEqual(self.status()['state'],'failed')
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')

    def test_unlaunchable_new_program_rolls_back_before_discarding_old_version(self):
        self.launch('badapp');self.commit()
        self.assertEqual(self.status()['state'],'failed',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')

    def test_abort_does_not_stop_processes_or_install(self):
        self.launch()
        (self.cache/'decision.json').write_text(json.dumps({'token':'test-owned','action':'abort'}))
        self.helper.wait(timeout=8)
        self.assertEqual(self.status()['state'],'aborted')
        self.assertTrue(all(p.poll() is None for p in self.processes))

    def test_timeout_never_replaces_a_running_program(self):
        self.launch(wait_timeout_ms=200)
        (self.cache/'decision.json').write_text(json.dumps({'token':'test-owned','action':'commit'}))
        self.helper.wait(timeout=8)
        self.assertEqual(self.status()['state'],'failed')
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')
        self.assertTrue(all(p.poll() is None for p in self.processes))

    def launch_delta(self, badapp=False, extra_cache=False, **changes):
        import shutil
        from windows_delta import create_patch, prepare_update
        (self.target/'payload/runtime').mkdir()
        (self.target/'payload/runtime/keep.bin').write_bytes(b'unchanged runtime bytes')
        (self.target/'payload/source.py').write_text('VALUE = 1\n')
        new=self.root/'new-release'
        shutil.copytree(self.target,new)
        (new/'payload/manifest.json').write_text('{"version":"9.0.1"}')
        (new/'payload/added.txt').write_text('new program file')
        if badapp:(new/'electricity-toolbox.exe').write_bytes(b'MZinvalid')
        archive=self.cache/'patch.tar.xz'
        create_patch(self.target,new,archive)
        if extra_cache:
            (self.target/'payload/__pycache__').mkdir()
            (self.target/'payload/__pycache__/source.cpython-312.pyc').write_bytes(b'generated cache')
        self.prepared=prepare_update(self.target,[(archive,'9.0.0','9.0.1')],'9.0.1',self.cache)
        artifact=self.prepared.manifest
        self.runtime_inode=(self.target/'payload/runtime/keep.bin').stat().st_ino
        plan=dict(schema=2,installer=str(artifact),sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            size=artifact.stat().st_size,version='9.0.1',target_dir=str(self.target),app_home=str(self.home),
            processes=self.children(),token=self.prepared.token,staging_dir=str(self.prepared.staging_dir),wait_timeout_ms=2000)
        plan.update(changes)
        (self.cache/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        self.helper=subprocess.Popen([str(SERVICE),'--windows-update-helper',str(self.cache/'plan.json')],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+5
        while not self.status() and self.helper.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(self.status().get('state'),'ready',self.status())
        return plan

    def commit_delta(self):
        (self.cache/'decision.json').write_text(json.dumps({'token':self.prepared.token,'action':'commit'}))
        for child in self.processes:child.stdin.close()
        self.helper.wait(timeout=20)

    def test_real_delta_switches_verified_tree_and_reuses_runtime_files(self):
        self.launch_delta()
        self.commit_delta()
        self.assertEqual(self.status()['state'],'succeeded',self.status())
        self.assertEqual(self.status()['mode'],'delta')
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.1')
        self.assertEqual((self.target/'payload/runtime/keep.bin').stat().st_ino,self.runtime_inode)
        self.assertEqual((self.home/'keep.txt').read_text(),'KEEP-DATA')
        self.assertFalse(list(self.root.glob('.electricity-toolbox-rollback-*')))

    def test_real_helper_accepts_cache_removal_with_complete_original_inventory(self):
        self.launch_delta(extra_cache=True)
        self.assertTrue((self.target/'payload/__pycache__/source.cpython-312.pyc').is_file())
        self.assertFalse((self.prepared.staging_dir/'payload/__pycache__').exists())
        self.commit_delta()
        self.assertEqual(self.status()['state'], 'succeeded', self.status())
        self.assertEqual(self.status()['mode'], 'delta')
        self.assertFalse((self.target/'payload/__pycache__').exists())
        self.assertEqual((self.home/'keep.txt').read_text(), 'KEEP-DATA')

    def test_delta_stage_modified_after_ready_never_replaces_old_install(self):
        self.launch_delta()
        (self.prepared.staging_dir/'payload/added.txt').write_text('modified after ready')
        self.commit_delta()
        self.assertEqual(self.status()['state'],'failed',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')
        self.assertEqual((self.home/'keep.txt').read_text(),'KEEP-DATA')

    def test_delta_modified_base_is_rejected_after_handoff(self):
        self.launch_delta()
        (self.target/'payload/runtime/keep.bin').write_bytes(b'modified baseline')
        self.commit_delta()
        self.assertEqual(self.status()['state'],'failed',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')

    def test_delta_unlaunchable_target_restores_original_program_and_data(self):
        self.launch_delta(badapp=True)
        self.commit_delta()
        self.assertEqual(self.status()['state'],'failed',self.status())
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')
        self.assertEqual((self.home/'keep.txt').read_text(),'KEEP-DATA')
        self.assertTrue((self.target/'electricity-toolbox.exe').stat().st_size>100000)

    def test_delta_abort_keeps_running_processes_and_old_tree(self):
        self.launch_delta()
        (self.cache/'decision.json').write_text(json.dumps({'token':self.prepared.token,'action':'abort'}))
        self.helper.wait(timeout=8)
        self.assertEqual(self.status()['state'],'aborted',self.status())
        self.assertTrue(all(p.poll() is None for p in self.processes))
        self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')

    def test_delta_rejects_added_or_atomically_replaced_unknown_local_files(self):
        for replace in (False,True):
            with self.subTest(replace=replace):
                # Separate fixture lifecycle for each real helper transaction.
                if replace:
                    self.tearDown();self.setUp()
                    (self.target/'notes.txt').write_text('older local notes')
                self.launch_delta()
                latest=self.target/'latest.tmp';latest.write_text('latest local notes')
                latest.replace(self.target/'notes.txt')
                self.commit_delta()
                self.assertEqual(self.status()['state'],'failed',self.status())
                self.assertEqual((self.target/'notes.txt').read_text(),'latest local notes')
                self.assertEqual(json.loads((self.target/'payload/manifest.json').read_text())['version'],'9.0.0')

    def test_full_update_preserves_unknown_local_files(self):
        (self.target/'notes.txt').write_text('preserve local notes')
        (self.target/'local').mkdir();(self.target/'local/history.txt').write_text('history')
        self.launch();self.commit()
        self.assertEqual(self.status()['state'],'succeeded',self.status())
        self.assertTrue((self.target/'notes.txt').is_file(),'full fallback lost a local file')
        self.assertEqual((self.target/'notes.txt').read_text(),'preserve local notes')
        self.assertEqual((self.target/'local/history.txt').read_text(),'history')
