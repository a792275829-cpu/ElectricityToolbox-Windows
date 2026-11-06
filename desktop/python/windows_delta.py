"""Windows file deltas: prepare an isolated, hard-linked installation sibling."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import unicodedata
import uuid

from delta_update import cancelled, digest, safe_name, select_route
from github_update import UpdateError, version_parts

FORMAT = 1
PLATFORM = 'windows-x64'
INDEX_NAME = 'windows-update-manifest.json'
MAX_MANIFEST = 32 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_SIZE = 12 * 1024**3
STAGE_PREFIX = '.electricity-toolbox-stage-'


def _io_path(path):
    """Use Win32 extended paths for I/O without changing public stage ownership."""
    path = Path(path)
    if os.name != 'nt':
        return path
    text = str(path.absolute())
    if text.startswith('\\\\?\\'):
        return path.absolute()
    return Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)


def _version(value):
    if not isinstance(value, str) or value.startswith('v'):
        raise UpdateError('Windows 补丁版本无效')
    version_parts(value)
    return value


def patch_name(base, target):
    return f'ElectricityToolbox-{_version(base)}-to-{_version(target)}-{PLATFORM}.patch.tar.xz'


def select_windows_route(index, current, target, full_size, repository):
    if not isinstance(index, dict) or index.get('platform') != PLATFORM or not isinstance(index.get('patches'), list):
        raise UpdateError('Windows 增量索引平台无效')
    for edge in index.get('patches', []):
        if not isinstance(edge, dict):
            raise UpdateError('Windows 增量索引无效')
        base, end = _version(edge.get('base')), _version(edge.get('target'))
        name = patch_name(base, end)
        if edge.get('browser_download_url') != f'https://github.com/{repository}/releases/download/v{end}/{name}':
            raise UpdateError('Windows 补丁下载地址无效')
    return select_route(index, current, target, full_size, repository, PLATFORM)


def windows_name(name):
    safe_name(name)
    if len(name) > 32760:
        raise UpdateError('Windows 补丁路径过长')
    for part in name.split('/'):
        if (any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.endswith((' ', '.'))
                or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])', part.split('.')[0].rstrip(' '))):
            raise UpdateError('Windows 补丁包含不安全的路径')
    return name


def _managed(name):
    return name in {'electricity-toolbox.exe', 'WebView2Loader.dll', 'uninstall.exe', 'payload'} or name.startswith('payload/')


def validate_inventory(entries):
    if not isinstance(entries, dict) or not entries or len(entries) > MAX_ENTRIES:
        raise UpdateError('Windows 补丁文件清单无效')
    keys, total = set(), 0
    for name, item in entries.items():
        windows_name(name)
        key = unicodedata.normalize('NFC', name).casefold()
        if key in keys or not _managed(name) or not isinstance(item, dict):
            raise UpdateError('Windows 补丁包含重复或非程序路径')
        keys.add(key)
        if item.get('type') == 'dir':
            if set(item) != {'type'}:
                raise UpdateError('Windows 补丁目录清单无效')
        elif item.get('type') == 'file':
            if (set(item) != {'type', 'size', 'sha256'} or type(item.get('size')) is not int
                    or not 0 <= item['size'] <= 2 * 1024**3
                    or not isinstance(item.get('sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
                raise UpdateError('Windows 补丁文件摘要或大小无效')
            total += item['size']
        else:
            raise UpdateError('Windows 补丁包含不支持的文件类型')
        for parent in Path(name).parents:
            if parent.as_posix() != '.' and entries.get(parent.as_posix(), {}).get('type') != 'dir':
                raise UpdateError('Windows 补丁父目录无效')
    if (total > MAX_SIZE or entries.get('payload') != {'type': 'dir'}
            or entries.get('electricity-toolbox.exe', {}).get('type') != 'file'
            or entries.get('payload/manifest.json', {}).get('type') != 'file'):
        raise UpdateError('Windows 补丁缺少有效程序或还原体积超限')


def _plain(path):
    info = _io_path(path).lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
        raise UpdateError('Windows 程序目录包含链接、重解析点或特殊文件')
    return info


def _walk(root):
    _plain(root)
    for path in sorted(root.iterdir()):
        info = _plain(path)
        yield path, info
        if stat.S_ISDIR(info.st_mode):
            yield from _walk(path)


def inventory(root, cancel=None):
    root, result = _io_path(root), {}
    _plain(root)
    for top in ('electricity-toolbox.exe', 'WebView2Loader.dll', 'uninstall.exe', 'payload'):
        path = root / top
        if not path.exists():
            continue
        info = _plain(path)
        entries = [(path, info)]
        if stat.S_ISDIR(info.st_mode):
            entries = __import__('itertools').chain(entries, _walk(path))
        for path, info in entries:
            cancelled(cancel)
            name = path.relative_to(root).as_posix()
            windows_name(name)
            result[name] = ({'type': 'dir'} if stat.S_ISDIR(info.st_mode) else
                dict(type='file', size=info.st_size, sha256=digest(path, cancel)))
            if len(result) > MAX_ENTRIES:
                raise UpdateError('Windows 程序文件过多')
    validate_inventory(result)
    return result


def install_version(root):
    value = json.loads((_io_path(root) / 'payload/manifest.json').read_text(encoding='utf-8'))
    return _version(value.get('version'))


def create_patch(old, new, output):
    old, new, output = _io_path(old), _io_path(new), Path(output)
    base, target = install_version(old), install_version(new)
    if version_parts(base) >= version_parts(target):
        raise UpdateError('Windows 补丁目标必须高于基准版本')
    before, after = inventory(old), inventory(new)
    manifest = dict(format=FORMAT, platform=PLATFORM, base=base, target=target, before=before, after=after)
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if len(raw) > MAX_MANIFEST:
        raise UpdateError('Windows 补丁清单过大')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise UpdateError('Windows 补丁已存在，禁止覆盖')
    try:
        with tarfile.open(output, 'w:xz', preset=6) as archive:
            member = tarfile.TarInfo('manifest.json')
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
            for name, item in sorted(after.items()):
                if item['type'] == 'file' and before.get(name) != item:
                    archive.add(new / name, arcname='files/' + name, recursive=False)
        return dict(base=base, target=target, name=output.name, size=output.stat().st_size,
            digest='sha256:' + digest(output))
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def clone_installation(source, destination, cancel=None, progress=None):
    _plain(Path(source))
    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists() or destination.resolve().is_relative_to(source):
        raise UpdateError('Windows 更新暂存目录已存在或位置无效')
    source, destination = _io_path(source), _io_path(destination)
    entries = list(_walk(source))
    for path, _ in entries:
        windows_name(path.relative_to(source).as_posix())
    destination.mkdir()
    linked_files = linked_bytes = copied_bytes = 0
    for i, (path, info) in enumerate(entries):
        cancelled(cancel)
        target = destination / path.relative_to(source)
        if stat.S_ISDIR(info.st_mode):
            target.mkdir()
        else:
            try:
                os.link(path, target)
                linked_files += 1
                linked_bytes += info.st_size
            except OSError:
                shutil.copy2(path, target)
                copied_bytes += info.st_size
        if progress:
            progress((i + 1) / max(1, len(entries)))
    return dict(linked_files=linked_files, linked_bytes=linked_bytes, copied_bytes=copied_bytes)


def _metadata(raw, base, target):
    try:
        manifest = json.loads(raw)
        if (set(manifest) != {'format', 'platform', 'base', 'target', 'before', 'after'}
                or manifest['format'] != FORMAT or manifest['platform'] != PLATFORM
                or manifest['base'] != base or manifest['target'] != target
                or version_parts(_version(base)) >= version_parts(_version(target))):
            raise UpdateError('Windows 补丁版本或平台不匹配')
        validate_inventory(manifest['before'])
        validate_inventory(manifest['after'])
        return manifest
    except (ValueError, TypeError, KeyError) as error:
        raise UpdateError('Windows 补丁清单无效') from error


def _remove_added_bytecode(stage, current, expected):
    """Discard regenerable extra caches in staging without relaxing managed hashes."""
    if any(current.get(name) != item for name, item in expected.items()):
        return False
    extra = set(current) - set(expected)
    for name in extra:
        path, item = Path(name), current[name]
        if not name.startswith('payload/'):
            return False
        if item['type'] == 'dir':
            if path.name != '__pycache__' or expected.get(path.parent.as_posix()) != {'type': 'dir'}:
                return False
        else:
            match = re.fullmatch(r'(.+)\.cpython-[0-9]+(?:\.opt-[12])?\.pyc', path.name)
            if path.parent.name != '__pycache__' or match is None:
                return False
            source = (path.parent.parent / (match[1] + '.py')).as_posix()
            if expected.get(source, {}).get('type') != 'file':
                return False
    # Validate the complete set before removing anything. Reparse points were
    # already rejected by inventory(); unlinking hard links preserves live files.
    for name in sorted(extra, key=lambda value: (-value.count('/'), value)):
        path = stage / name
        if current[name]['type'] == 'dir':
            path.rmdir()
        else:
            path.unlink()
    return True


def apply_patch(stage, patch, expected_base, expected_target, cancel=None):
    stage = _io_path(stage)
    cancelled(cancel)
    with tarfile.open(patch, 'r:xz') as archive:
        first = archive.next()
        if first is None or first.name != 'manifest.json' or not first.isfile() or first.size > MAX_MANIFEST:
            raise UpdateError('Windows 补丁缺少有效清单')
        with archive.extractfile(first) as stream:
            manifest = _metadata(stream.read(MAX_MANIFEST + 1), expected_base, expected_target)
        before, after = manifest['before'], manifest['after']
        current = inventory(stage, cancel)
        if (install_version(stage) != expected_base
                or (current != before and not _remove_added_bytecode(stage, current, before))):
            raise UpdateError('旧版程序文件已变化或版本不匹配，改用完整安装包')
        changed = {name for name in after if before.get(name) != after[name]}
        expected = {'files/' + name: after[name]['size'] for name in changed if after[name]['type'] == 'file'}
        members = {}
        for member in archive:
            cancelled(cancel)
            if member.name == 'manifest.json' and member.offset == first.offset:
                continue
            windows_name(member.name)
            if (member.name in members or member.name not in expected or not member.isfile()
                    or member.size != expected[member.name] or len(members) >= MAX_ENTRIES):
                raise UpdateError('Windows 补丁压缩包文件集合或大小不匹配')
            members[member.name] = member
        if set(members) != set(expected):
            raise UpdateError('Windows 补丁文件缺失')
        # Unlink staged hard links BEFORE writing. The live installation stays immutable.
        for name in sorted(set(before) - set(after) | changed, key=lambda n: (-n.count('/'), n)):
            cancelled(cancel)
            path = stage / name
            if path.is_file():
                path.unlink()
            elif path.is_dir() and (name not in after or after[name]['type'] != 'dir'):
                path.rmdir()
        for name in sorted(after, key=lambda n: (n.count('/'), n)):
            cancelled(cancel)
            path, item = stage / name, after[name]
            if item['type'] == 'dir':
                path.mkdir(exist_ok=True)
            elif name in changed:
                with archive.extractfile(members['files/' + name]) as source, path.open('xb') as target:
                    while chunk := source.read(1024 * 1024):
                        cancelled(cancel)
                        target.write(chunk)
                if path.stat().st_size != item['size'] or digest(path, cancel) != item['sha256']:
                    raise UpdateError('Windows 补丁文件内容校验失败')
    if install_version(stage) != expected_target or inventory(stage, cancel) != after:
        raise UpdateError('Windows 补丁还原校验失败')
    return manifest


def cleanup_stage(stage, root, token):
    stage, root = Path(stage), Path(root)
    if (stage.parent.resolve() != root.parent.resolve() or stage.name != STAGE_PREFIX + token
            or not re.fullmatch('[a-f0-9-]{32,36}', token)):
        raise UpdateError('拒绝清理非本次 Windows 更新目录')
    stage = _io_path(stage)
    if stage.exists():
        _plain(stage)
        # Validate links before a recursive operation, even on Python versions that
        # already avoid traversing Windows junctions in shutil.rmtree.
        for _ in _walk(stage):
            pass
        shutil.rmtree(stage)


@dataclass
class PreparedUpdate:
    manifest: Path
    staging_dir: Path
    token: str
    linked_files: int
    linked_bytes: int
    copied_bytes: int


def prepare_update(root, patches, target, cache, cancel=None, progress=None):
    _plain(Path(root))
    root, cache = Path(root).resolve(), Path(cache)
    token = str(uuid.uuid4())
    stage = root.with_name(STAGE_PREFIX + token)
    try:
        if progress:
            progress(0)
        cancelled(cancel)
        base = install_version(root)
        before = inventory(root, cancel)
        counts = clone_installation(root, stage, cancel,
            (lambda value: progress(value * .2)) if progress else None)
        current, final = base, None
        if not patches:
            raise UpdateError('Windows 增量更新路径为空')
        for i, (path, start, end) in enumerate(patches):
            if start != current:
                raise UpdateError('Windows 增量更新路径不连续')
            final = apply_patch(stage, path, start, end, cancel)
            current = end
            if progress:
                progress(.2 + .8 * (i + 1) / len(patches))
        if current != target:
            raise UpdateError('Windows 增量目标版本不匹配')
        cancelled(cancel)
        raw = dict(format=FORMAT, platform=PLATFORM, base=base, target=target,
            before=before, after=final['after'])
        path = cache / 'delta-plan.json'
        path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        return PreparedUpdate(path, stage, token, **counts)
    except BaseException:
        cleanup_stage(stage, root, token)
        raise
