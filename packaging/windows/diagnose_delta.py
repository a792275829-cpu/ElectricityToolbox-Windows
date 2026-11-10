"""Explain why a Windows install fell back from a file delta to the full installer.

The updater records no reason for the fallback, so the only observable symptom is
"patch first, full package afterwards". This script reproduces the exact check
`windows_delta.apply_patch` performs against the published patch baseline and
prints the files that break it. It is read-only: the installation is never
modified and no staging directory is created.

    python packaging/windows/diagnose_delta.py --root "<install dir>"
    python packaging/windows/diagnose_delta.py --root "<install dir>" --patch <patch.tar.xz>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop/python'))

from delta_update import digest  # noqa: E402
from github_update import UpdateError, version_parts  # noqa: E402
from windows_delta import INDEX_NAME, install_version, patch_name  # noqa: E402

REPOSITORY = 'a792275829-cpu/ElectricityToolbox-Windows'
MAX_ENTRIES = 100_000
PYC = re.compile(r'(.+)\.cpython-[0-9]+(?:\.opt-[12])?\.pyc')


def api(path):
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'ElectricityToolbox-diagnose'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(8 * 1024 * 1024))


def download(url, path):
    request = urllib.request.Request(url, headers={'User-Agent': 'ElectricityToolbox-diagnose'})
    with urllib.request.urlopen(request, timeout=120) as source, open(path, 'wb') as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
    return Path(path)


def fetch_patch(root_version, target, local):
    if local:
        return Path(local)
    releases = api(f'/repos/{REPOSITORY}/releases?per_page=20')
    release = next((r for r in releases if r.get('tag_name') == f'v{target}'), None)
    if release is None:
        raise UpdateError(f'找不到 v{target} 的发布记录')
    index_asset = next((a for a in release['assets'] if a['name'] == INDEX_NAME), None)
    if index_asset is None:
        raise UpdateError('该版本没有增量索引，更新器不会尝试补丁')
    folder = Path(tempfile.mkdtemp(prefix='ElectricityToolboxDiagnose-'))
    index = json.loads(download(index_asset['browser_download_url'], folder / INDEX_NAME).read_text(encoding='utf-8'))
    edge = next((e for e in index.get('patches', []) if e.get('base') == root_version), None)
    if edge is None:
        raise UpdateError(f'索引里没有 {root_version}→{target} 的补丁，更新器不会尝试补丁')
    print(f'补丁：{edge["name"]}  {edge["size"] / 1024**2:.1f} MB')
    return download(edge['browser_download_url'], folder / edge['name'])


def manifest(patch):
    with tarfile.open(patch, 'r:xz') as archive:
        first = archive.next()
        if first is None or first.name != 'manifest.json':
            raise UpdateError('补丁缺少清单')
        with archive.extractfile(first) as stream:
            raw = stream.read(64 * 1024 * 1024)
    data = json.loads(raw)
    print(f'基线：{data["base"]} → {data["target"]}，清单 {len(data["before"])} 条')
    return data['before'], data['after']


def collect(root, cancel=None):
    """Same managed-file view as windows_delta.inventory, without failing early."""
    result = {}
    for top in ('electricity-toolbox.exe', 'uninstall.exe', 'payload'):
        path = root / top
        if not path.exists():
            continue
        stack = [path]
        while stack:
            item = stack.pop()
            info = item.lstat()
            name = item.relative_to(root).as_posix()
            if getattr(info, 'st_file_attributes', 0) & 0x400 or item.is_symlink():
                result[name] = {'type': 'reparse', 'size': 0, 'sha256': ''}
                continue
            if item.is_dir():
                result[name] = {'type': 'dir'}
                stack.extend(sorted(item.iterdir(), reverse=True))
            else:
                result[name] = {'type': 'file', 'size': info.st_size, 'sha256': digest(item)}
            if len(result) > MAX_ENTRIES:
                raise UpdateError('文件过多')
    return result


def recoverable(name, current, before):
    """Mirror windows_delta._remove_added_bytecode for extra entries."""
    path = Path(name)
    item = current[name]
    if not name.startswith('payload/'):
        return False
    if item['type'] == 'dir':
        return path.name == '__pycache__' and before.get(path.parent.as_posix()) == {'type': 'dir'}
    match = PYC.fullmatch(path.name)
    if match is None or path.parent.name != '__pycache__':
        return False
    return before.get((path.parent.parent / (match.group(1) + '.py')).as_posix(), {}).get('type') == 'file'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', help='安装目录（含 electricity-toolbox.exe）')
    parser.add_argument('--target', help='目标版本，默认安装目录当前版本的下一个发布版')
    parser.add_argument('--patch', help='本地补丁 tar.xz，离线时使用')
    args = parser.parse_args()
    root = Path(args.root or os.environ.get('TOOLBOX_INSTALL_ROOT') or '')
    if not root.is_dir():
        parser.error('请用 --root 指定已安装的 Windows 程序目录')
    try:
        current = install_version(root)
    except (OSError, ValueError, UpdateError) as exc:
        parser.error(f'读取 payload/manifest.json 失败：{exc}')
    target = args.target
    if not target:
        latest = max((r['tag_name'] for r in api(f'/repos/{REPOSITORY}/releases?per_page=20')
                      if not r.get('draft') and not r.get('prerelease')), key=version_parts)
        target = latest.removeprefix('v')
    print(f'安装目录：{root}\n当前版本：{current}  目标版本：{target}')
    before, _after = manifest(fetch_patch(current, target, args.patch))
    if install_version(root) != current:
        print('注意：诊断期间安装版本发生变化')
    current_inventory = collect(root)
    missing = sorted(set(before) - set(current_inventory))
    extra = sorted(set(current_inventory) - set(before))
    lost = [n for n in extra if not recoverable(n, current_inventory, before)]
    bytecode = len(extra) - len(lost)
    changed = sorted(n for n in before if n in current_inventory and current_inventory[n] != before[n])
    print(f'本机受管文件 {len(current_inventory)} 条；缺失 {len(missing)}，'
          f'可回收缓存 {bytecode}，多余 {len(lost)}，内容不符 {len(changed)}')
    for label, names in (('缺失', missing), ('多余', lost), ('内容不符', changed)):
        for name in names[:20]:
            print(f'  [{label}] {name}')
        if len(names) > 20:
            print(f'  [{label}] ……共 {len(names)} 条')
    blocked = bool(missing or lost or changed)
    print('\n判定：' + ('补丁校验会失败，更新器必然回退完整安装包（上面的文件即原因）'
                       if blocked else '补丁校验可通过，回退原因不在文件清单，请检查补丁下载与磁盘/权限'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
