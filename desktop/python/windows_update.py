"""Verified Windows file deltas with a full NSIS fallback and transactional handoff."""
from __future__ import annotations

import os
import hashlib
import http.client
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request

from github_update import Cancelled, GitHubUpdater, Release, UpdateError, trusted_url, version_parts
from windows_delta import INDEX_NAME, cleanup_stage, install_version, prepare_update, select_windows_route

WINDOWS_REPOSITORY = "QiaoZhang013/electricity-toolbox-rust-windows"


def installer_name(tag):
    return f"ElectricityToolbox_{tag.removeprefix('v')}_windows_x64_setup.exe"


def parse_windows_release(data, current):
    try:
        tag = data['tag_name']
        latest = version_parts(tag)
        if data['draft'] or data['prerelease'] or latest <= version_parts(current):
            return None
        base = f'https://github.com/{WINDOWS_REPOSITORY}/releases'
        if data['html_url'] != f'{base}/tag/{tag}':
            raise UpdateError('Windows 版本发布页面无效')
        name = installer_name(tag)
        assets = [asset for asset in data['assets'] if asset['name'] == name]
        if len(assets) != 1:
            raise UpdateError('此版本缺少唯一的 Windows x64 安装包')
        asset = assets[0]
        if (type(asset['size']) is not int or not 0 < asset['size'] <= 2 * 1024**3
                or not isinstance(asset['digest'], str)
                or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', asset['digest'])
                or asset['browser_download_url'] != f'{base}/download/{tag}/{name}'):
            raise UpdateError('Windows 安装包链接、大小或 SHA-256 摘要无效')
        indices = [a for a in data['assets'] if a['name'] == INDEX_NAME]
        index = indices[0] if len(indices) == 1 else None
        if index and not (type(index.get('size')) is int and 0 < index['size'] <= 2*1024*1024
                and isinstance(index.get('digest'), str) and re.fullmatch(r'sha256:[a-fA-F0-9]{64}',index['digest'])
                and index.get('browser_download_url') == f'{base}/download/{tag}/{INDEX_NAME}'):
            index = None
        return Release(tag, str(data.get('body') or '此版本没有提供更新说明。')[:100_000],
                       data['html_url'], dict(asset), dict(index) if index else None)
    except (KeyError, TypeError, AttributeError) as exc:
        raise UpdateError('Windows 版本信息无效') from exc


class WindowsUpdater(GitHubUpdater):
    platform_label = 'Windows x64'

    def __init__(self, current, client=None, opener=None):
        super().__init__(current, repository=WINDOWS_REPOSITORY, opener=opener)
        self.client = client
        self._reserved = False
        self._helper = None
        self._plan = None
        self._prepared = None
        self._prepared_root = None
        self.update_mode = 'full'
        self.download_bytes = 0
        self.phase = ''

    def check(self):
        return self.check_release()

    def parse_data(self, data):
        return parse_windows_release(data, self.current)

    def primary_asset_name(self, tag):
        return installer_name(tag)

    def release_asset_names(self, tag):
        return {self.primary_asset_name(tag), INDEX_NAME}

    def download_asset(self,asset,path,cancel,progress):
        """Resume transient failures within this attempt; always verify the whole file."""
        try:
            expected,size,url=asset['digest'],asset['size'],asset['browser_download_url']
            if (not isinstance(expected,str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',expected)
                    or type(size) is not int or not 0<size<=2*1024**3 or not trusted_url(url,self.repository)):
                raise UpdateError('更新文件链接、摘要或大小无效')
        except (KeyError,TypeError) as exc:
            raise UpdateError('更新文件元数据无效') from exc
        path=Path(path);count=0;deadline=time.monotonic()+1800
        try:
            for attempt in range(3):
                if cancel.is_set():raise Cancelled('已取消下载')
                headers={'User-Agent':f'ElectricityToolbox/{self.current}','Accept-Encoding':'identity'}
                if count:headers['Range']=f'bytes={count}-'
                try:
                    with self.open(urllib.request.Request(url,headers=headers),timeout=30) as source:
                        if source.status==206:
                            if source.headers.get('Content-Range')!=f'bytes {count}-{size-1}/{size}':
                                raise UpdateError('断点续传范围无效')
                        elif source.status==200:
                            count=0  # A server may ignore Range; overwrite rather than append.
                        else:raise UpdateError(f'下载失败（HTTP {source.status}）')
                        with path.open('ab' if count else 'wb') as target:
                            while chunk:=source.read(256*1024):
                                if cancel.is_set():raise Cancelled('已取消下载')
                                if time.monotonic()>deadline:raise UpdateError('下载超时，请重试')
                                count+=len(chunk)
                                if count>size:raise UpdateError('更新文件大小校验失败')
                                target.write(chunk);progress(count/size)
                    if count==size:break
                    raise OSError('下载连接提前结束')
                except (OSError,urllib.error.URLError,http.client.HTTPException) as exc:
                    # Non-transient HTTP errors must not be repeatedly requested.
                    if isinstance(exc,urllib.error.HTTPError) and exc.code not in {408,429,500,502,503,504}:raise
                    if attempt==2:raise UpdateError(f'下载重试后仍失败：{exc}') from exc
                    if cancel.wait(.3*(attempt+1)):raise Cancelled('已取消下载')
            if cancel.is_set():raise Cancelled('已取消下载')
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if count!=size or actual!=expected[7:].lower():raise UpdateError('更新文件校验失败，请重新下载')
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def download(self, release, cancel, progress):
        self.fallback_reason = None
        self.update_mode = 'full'
        self._prepared = None
        directory = Path(tempfile.mkdtemp(prefix='ElectricityToolboxWindowsUpdate-'))
        path = directory / installer_name(release.version)
        try:
            checked = parse_windows_release(dict(tag_name=release.version, draft=False, prerelease=False,
                html_url=release.page, body=release.notes, assets=[release.asset]), self.current)
            if checked is None:
                raise UpdateError('更新版本不高于当前版本')
            root = os.environ.get('TOOLBOX_INSTALL_ROOT')
            if release.update_asset and root and not self._delta_failed(release.version):
                try:
                    prepared = self._download_delta(release,Path(root),directory,cancel,progress)
                    if prepared:
                        self._prepared = prepared
                        self._prepared_root = Path(root)
                        return self.remember_download(release,prepared.manifest)
                except Cancelled:
                    raise
                except Exception as exc:
                    self.fallback_reason = str(exc)
                    for candidate in directory.iterdir():
                        if candidate.is_file() and not candidate.is_symlink() and not getattr(candidate.lstat(),'st_file_attributes',0)&0x400:
                            candidate.unlink()
            self.update_mode = 'full'
            self.phase = '下载完整安装包'
            self.download_bytes = checked.asset['size']
            return self.remember_download(release, self.download_asset(checked.asset, path, cancel, progress))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _delta_failed(self,version):
        try:
            status=json.loads((Path(os.environ['TOOLBOX_APP_HOME'])/'state/windows-update.json').read_text(encoding='utf-8'))
            return isinstance(status,dict) and status.get('state')=='failed' and status.get('mode')=='delta' and status.get('version')==version.removeprefix('v')
        except (KeyError,OSError,ValueError,TypeError):
            return False

    def _download_delta(self,release,root,directory,cancel,progress):
        if install_version(root)!=self.current.removeprefix('v'):
            raise UpdateError('当前 Windows 安装版本不匹配')
        index_asset=release.update_asset
        self.phase='读取增量更新索引'
        index_path=self.download_asset(index_asset,directory/INDEX_NAME,cancel,lambda _:None)
        route=select_windows_route(json.loads(index_path.read_text(encoding='utf-8')),self.current.removeprefix('v'),
            release.version.removeprefix('v'),release.asset['size']-index_asset['size'],self.repository)
        if not route:
            return None
        self.update_mode='delta'
        self.download_bytes=index_asset['size']+sum(edge['size'] for edge in route)
        total=sum(edge['size'] for edge in route);downloaded=0;patches=[]
        self.phase='下载增量补丁'
        for edge in route:
            path=self.download_asset(edge,directory/edge['name'],cancel,
                lambda fraction:progress(.7*(downloaded+fraction*edge['size'])/total))
            patches.append((path,edge['base'],edge['target']));downloaded+=edge['size']
        self.phase='复用运行环境并校验新版本'
        return prepare_update(root,patches,release.version.removeprefix('v'),directory,cancel,
            lambda fraction:progress(.7+.3*fraction))

    def prepare_update(self):
        if os.environ.get('TOOLBOX_INSTALLED') != '1' or self.client is None:
            raise UpdateError('请使用完整安装后的 Windows 桌面版执行自动安装')
        result = self.client.call('prepare_update')
        self._reserved = True
        return result

    @staticmethod
    def _atomic_json(path, value):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def _status(self):
        if self._plan is None:
            return {}
        try:
            return json.loads((self._plan.parent/'status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def prepare_install(self, release, path):
        if not self._reserved or self.downloaded is None or self.downloaded[:2] != (release.version, Path(path)):
            raise UpdateError('安装包未经过本次更新校验，请重新下载')
        try:
            root = Path(os.environ['TOOLBOX_INSTALL_ROOT']).resolve(strict=True)
            home = Path(os.environ['TOOLBOX_APP_HOME']).resolve(strict=True)
            host = int(os.environ['TOOLBOX_HOST_PID'])
            if not (root/'electricity-toolbox.exe').is_file() or home.is_relative_to(root) or host <= 0:
                raise ValueError('invalid installation')
        except (KeyError, OSError, ValueError) as exc:
            raise UpdateError('Windows 安装路径或宿主进程信息无效，请重新打开安装版') from exc
        with Path(path).open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != self.downloaded[2]:
            raise UpdateError('下载后的安装包已变化，请重新下载')
        ping = self.client.call('ping')
        if ping.get('engine') != 'rust' or ping.get('protocol') != 3:
            raise UpdateError('界面与 Rust 更新后台不匹配，请重新安装完整版本')
        service = Path(__file__).resolve().parents[2]/'电力工具箱/bin/toolbox-service.exe'
        helper = Path(path).parent/'toolbox-update.exe'
        prepared = self._prepared
        if prepared is not None and Path(path) != prepared.manifest:
            raise UpdateError('增量更新计划不匹配')
        helper_source = prepared.staging_dir/'payload/app/电力工具箱/bin/toolbox-service.exe' if prepared else service
        shutil.copy2(helper_source, helper)
        token = prepared.token if prepared else str(uuid.uuid4())
        self._plan = Path(path).parent/'plan.json'
        self._token = token
        plan = dict(schema=2 if prepared else 1, installer=str(Path(path).resolve()), sha256=digest,
            size=Path(path).stat().st_size, version=release.version.removeprefix('v'), target_dir=str(root), app_home=str(home),
            token=token, processes=[dict(pid=host,path=str(root/'electricity-toolbox.exe')),
                dict(pid=os.getpid(),path=str(Path(sys.executable).resolve())),dict(pid=ping['pid'],path=str(service))])
        if prepared:
            plan['staging_dir'] = str(prepared.staging_dir)
        self._atomic_json(self._plan, plan)
        with (self._plan.parent/'helper.log').open('wb') as log:
            self._helper = subprocess.Popen([str(helper),'--windows-update-helper',str(self._plan)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            if self._status().get('state') == 'ready' and self._helper.poll() is None:
                return True
            if self._helper.poll() is not None:
                detail = (self._plan.parent/'helper.log').read_text(encoding='utf-8',errors='replace')[-1200:]
                raise UpdateError('Windows 安装助手未完成准备：'+detail)
            time.sleep(.02)
        raise UpdateError('Windows 安装助手准备超时，未替换程序')

    def commit_install(self):
        if self._plan is None or self._helper is None or self._helper.poll() is not None or self._status().get('state') != 'ready':
            raise UpdateError('Windows 自动安装尚未准备完成')
        self._atomic_json(self._plan.parent/'decision.json',dict(action='commit',token=self._token))
        return True

    def abort_update(self):
        if self._helper is not None and self._helper.poll() is None:
            self._atomic_json(self._plan.parent/'decision.json',dict(action='abort',token=self._token))
            try:
                self._helper.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise UpdateError('安装助手尚未确认取消，请稍后重试') from exc
        if self._reserved:
            self.client.call('abort_update')
            self._reserved = False
        self._helper = None
        self._plan = None
        if self._prepared is not None:
            cleanup_stage(self._prepared.staging_dir,self._prepared_root,self._prepared.token)
            self._prepared = None
        if self.downloaded is not None:
            shutil.rmtree(self.downloaded[1].parent, ignore_errors=True)
            self.downloaded = None

    def take_install_error(self):
        home = os.environ.get('TOOLBOX_APP_HOME')
        if not home:
            return None
        path = Path(home)/'state/windows-update.json'
        try:
            status = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(status, dict) or status.get('state') != 'failed' or status.get('notified'):
                return None
            try:
                superseded = (isinstance(status.get('version'), str)
                              and version_parts(status['version']) <= version_parts(self.current))
            except UpdateError:
                superseded = False
            status['notified'] = True
            if superseded:
                # Keep the original failure for diagnostics after a later full
                # installation/retry has already installed this target or newer.
                status['superseded_by'] = self.current.removeprefix('v')
            self._atomic_json(path, status)
            return None if superseded else str(status.get('error') or '上次 Windows 更新未完成，请重试。')
        except (OSError, ValueError, TypeError):
            return None

    def cleanup_completed_download(self):
        """Run off the Tk thread; delete only an owned successful update cache."""
        home = os.environ.get('TOOLBOX_APP_HOME')
        if not home:
            return False
        report = Path(home)/'state/windows-update.json'
        try:
            for _ in range(50):
                status = json.loads(report.read_text(encoding='utf-8'))
                if status.get('state') != 'installing':
                    break
                time.sleep(.2)
            if status.get('state') != 'succeeded':
                return False
            cache = Path(str(status['cache']).removeprefix('\\\\?\\')).resolve()
            if cache.parent != Path(tempfile.gettempdir()).resolve() or not cache.name.startswith('ElectricityToolboxWindowsUpdate-'):
                return False
            if not cache.exists():
                return True
            if cache.is_symlink() or getattr(cache.lstat(),'st_file_attributes',0) & 0x400:
                return False
            plan = json.loads((cache/'plan.json').read_text(encoding='utf-8'))
            if Path(plan['app_home']).resolve() != Path(home).resolve() or plan['version'] != status['version']:
                return False
            names = {installer_name(status['version']), 'toolbox-update.exe', 'helper.log', 'plan.json',
                     'decision.json', 'status.json', 'uninstall-registry.reg', 'plan.tmp', 'decision.tmp', 'status.tmp',
                     INDEX_NAME, 'delta-plan.json'}
            index_path=cache/INDEX_NAME
            if index_path.is_file() and index_path.stat().st_size<=2*1024*1024:
                index=json.loads(index_path.read_text(encoding='utf-8'))
                from windows_delta import patch_name
                for edge in index.get('patches',[]):
                    names.add(patch_name(edge['base'],edge['target']))
            children = list(cache.iterdir())
            if any(p.name not in names or not p.is_file() or p.is_symlink()
                   or getattr(p.lstat(),'st_file_attributes',0) & 0x400 for p in children):
                return False
            # Windows denies deletion while the helper's image is still running.
            for _ in range(50):
                try:
                    (cache/'toolbox-update.exe').unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(.1)
            else:
                return False
            # The helper may still be publishing the last state or closing its log.
            # Retry the entire cleanup so one transiently locked file leaves no crumbs.
            for _ in range(50):
                try:
                    for path in cache.iterdir():
                        if path.name not in names or not path.is_file() or path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
                            return False
                        path.unlink(missing_ok=True)
                    cache.rmdir()
                    return True
                except PermissionError:
                    time.sleep(.1)
            return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
