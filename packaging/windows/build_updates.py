"""Build and verify Windows deltas; keep historical assets at their original URLs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desktop/python'))
from github_update import UpdateError, version_parts
from windows_delta import (INDEX_NAME, PLATFORM, apply_patch, clone_installation, create_patch,
    digest, install_version, inventory, patch_name, select_windows_route)

REPOSITORY='QiaoZhang013/electricity-toolbox-rust-windows'

def gh(*args):
    executable=shutil.which('gh') or str(ROOT/'.toolbox/build-tools/gh-2.103.3/bin/gh.exe')
    return subprocess.check_output([executable,*args])

def fetch_asset(release,name,folder):
    matches=[a for a in release['assets'] if a['name']==name]
    if len(matches)!=1 or Path(name).name!=name:raise UpdateError('历史 Windows 附件缺失或重复')
    asset=matches[0];path=folder/name;cached=ROOT/'dist/windows'/name
    if cached.is_file() and cached.stat().st_size==asset['size'] and 'sha256:'+digest(cached)==asset.get('digest'):
        shutil.copy2(cached,path)
    else:
        gh('release','download',release['tag_name'],'--repo',REPOSITORY,'--pattern',name,'--dir',str(folder))
    if path.stat().st_size!=asset['size'] or 'sha256:'+digest(path)!=asset.get('digest'):
        raise UpdateError('历史 Windows 附件摘要不匹配')
    return path

@contextmanager
def installed(installer):
    """Unpack genuine NSIS in isolation, restoring the user's exact registry values."""
    if os.name!='nt':raise UpdateError('Windows 安装包验证需要 Windows')
    import winreg
    saved=[]
    for view in (winreg.KEY_WOW64_32KEY,winreg.KEY_WOW64_64KEY):
        for keypath in (r'Software\electricity\电力工具箱',r'Software\Microsoft\Windows\CurrentVersion\Uninstall\电力工具箱'):
            values={};exists=False
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keypath,0,winreg.KEY_READ|view) as key:
                    exists=True
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        name,value,kind=winreg.EnumValue(key,i);values[name]=(value,kind)
            except FileNotFoundError:pass
            saved.append((view,keypath,exists,values))
    try:
        with tempfile.TemporaryDirectory(prefix='ElectricityToolboxBaseline-') as folder:
            root=Path(folder)/'installation'
            command=subprocess.list2cmdline([str(Path(installer).resolve()),'/S','/UPDATE','/NS'])+' /D='+str(root)
            subprocess.run(command,check=True,timeout=900,creationflags=subprocess.CREATE_NO_WINDOW)
            inventory(root)
            yield root
    finally:
        for view,keypath,exists,values in saved:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,keypath,0,winreg.KEY_ALL_ACCESS|view) as key:
                for name in [winreg.EnumValue(key,i)[0] for i in range(winreg.QueryInfoKey(key)[1])]:
                    if name not in values:winreg.DeleteValue(key,name)
                for name,(value,kind) in values.items():winreg.SetValueEx(key,name,0,kind,value)
            if not exists:winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER,keypath,view)

def build_one(old,new,assets,full_size):
    base,target=install_version(old),install_version(new)
    patch=assets/patch_name(base,target)
    edge=create_patch(old,new,patch)
    if edge['size']>=full_size:
        patch.unlink();return None
    before=inventory(old)
    with tempfile.TemporaryDirectory(prefix='ElectricityToolboxDeltaVerify-') as folder:
        stage=Path(folder)/'restored'
        counts=clone_installation(old,stage)
        apply_patch(stage,patch,base,target)
        if inventory(stage)!=inventory(new) or inventory(old)!=before:
            raise UpdateError('Windows 补丁还原或旧版保护验证失败')
    edge['browser_download_url']=f'https://github.com/{REPOSITORY}/releases/download/v{target}/{patch.name}'
    print(json.dumps(dict(base=base,target=target,patch_bytes=edge['size'],full_bytes=full_size,**counts)),flush=True)
    return edge

def build(root,output,full_only=False,base_roots=(),fetch_history=True,limit=3):
    root,output=Path(root),Path(output);target=install_version(root);inventory(root)
    full=output/f'ElectricityToolbox_{target}_windows_x64_setup.exe'
    if not full.is_file():raise UpdateError('请先构建本版本完整 Windows 安装包')
    assets=output/f'updates-{target}'
    assets.mkdir()  # Never overwrite a published or previously reviewed artifact set.
    index=dict(format=1,version=target,platform=PLATFORM,patches=[])
    if not full_only:
        releases=json.loads(gh('api',f'repos/{REPOSITORY}/releases?per_page=100')) if fetch_history else []
        previous=sorted((r for r in releases if not r['draft'] and not r['prerelease'] and
            version_parts(r['tag_name'])<version_parts(target)),key=lambda r:version_parts(r['tag_name']),reverse=True)
        with tempfile.TemporaryDirectory(prefix='ElectricityToolboxHistory-') as folder:
            cache=Path(folder)
            if previous and version_parts(previous[0]['tag_name'])[:2]==version_parts(target)[:2] and any(a['name']==INDEX_NAME for a in previous[0]['assets']):
                history=json.loads(fetch_asset(previous[0],INDEX_NAME,cache).read_text(encoding='utf-8'))
                select_windows_route(history,'0.0.0',previous[0]['tag_name'].removeprefix('v'),full.stat().st_size,REPOSITORY)
                index['patches'].extend(history['patches'])
            seen=set()
            for old in base_roots:
                base=install_version(old)
                if base in seen or version_parts(base)[:2]!=version_parts(target)[:2]:continue
                edge=build_one(old,root,assets,full.stat().st_size);seen.add(base)
                if edge:index['patches'].append(edge)
            for release in previous[:limit]:
                base=release['tag_name'].removeprefix('v')
                if base in seen or version_parts(base)[:2]!=version_parts(target)[:2]:continue
                folder=cache/base;folder.mkdir()
                installer=fetch_asset(release,f'ElectricityToolbox_{base}_windows_x64_setup.exe',folder)
                with installed(installer) as old:
                    if install_version(old)!=base:raise UpdateError('历史 Windows 版本与标签不一致')
                    edge=build_one(old,root,assets,full.stat().st_size)
                    if edge:index['patches'].append(edge)
    index['patches']=list({(e['base'],e['target']):e for e in index['patches']}.values())
    select_windows_route(index,'0.0.0',target,full.stat().st_size,REPOSITORY)
    raw=json.dumps(index,ensure_ascii=False,indent=2)+'\n'
    if len(raw.encode())>2*1024*1024:raise UpdateError('Windows 索引过大，请用 full-only 重设基线')
    (assets/INDEX_NAME).write_text(raw,encoding='utf-8')
    for path in list(assets.iterdir()):
        path.with_name(path.name+'.sha256').write_text(digest(path)+'  '+path.name+'\n',encoding='ascii')
    return assets

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path,nargs='?')
    parser.add_argument('--installer',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/windows')
    parser.add_argument('--base-root',type=Path,action='append',default=[])
    parser.add_argument('--full-only',action='store_true')
    parser.add_argument('--no-fetch-history',action='store_true')
    args=parser.parse_args()
    if args.root:
        print(build(args.root,args.output,args.full_only,args.base_root,not args.no_fetch_history))
    elif args.installer:
        with installed(args.installer) as root:print(build(root,args.output,args.full_only,args.base_root,not args.no_fetch_history))
    else:parser.error('需要已安装 root 或 --installer')
