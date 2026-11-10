"""Preview or reclaim timestamped snapshots within this checkout's build archive.

Deletion is opt-in and failures are reported; no alternate deletion channel is used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import stat

ROOT = Path(__file__).resolve().parents[2]


def reject_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('归档清理拒绝链接或重解析点')


def validate_root(path: Path) -> Path:
    reject_links(path)
    allowed = ROOT / 'dist/build-archive'
    reject_links(allowed)
    resolved = path.resolve()
    if not resolved.is_relative_to(allowed.resolve()):
        raise ValueError('归档清理只允许当前仓库 dist/build-archive 内的目录')
    return resolved


def validate_snapshot(path: Path) -> Path:
    validate_root(path.parent)
    if not re.fullmatch(r'\d{8}-\d{6}', path.name):
        raise ValueError('只允许清理时间戳命名的构建快照')
    reject_links(path)
    for item in path.rglob('*'):
        reject_links(item)
    return path


def archive_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def select_removals(archive_root: Path, keep: int, remove_all: bool) -> list[Path]:
    """Return snapshots to reclaim: newest `keep` are preserved unless remove_all."""
    if keep < 0:
        raise ValueError('--keep 不得小于 0')
    if not archive_root.is_dir():
        return []
    entries = sorted((path for path in archive_root.iterdir()
                      if path.is_dir() and re.fullmatch(r'\d{8}-\d{6}', path.name)),
                     key=lambda path: path.name, reverse=True)
    if remove_all:
        return entries
    return entries[max(keep, 0):]


def purge(path: Path) -> None:
    target = validate_snapshot(path)
    shutil.rmtree(target)
    if target.exists():
        raise OSError('构建快照回收未完成')


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT / 'dist/build-archive')
    parser.add_argument('--keep', type=int, default=1)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--apply', action='store_true', help='执行回收；默认仅预览')
    args = parser.parse_args(argv)
    archive_root = validate_root(args.root)
    targets = select_removals(archive_root, args.keep, args.all)
    if not targets:
        print(f'No build archive to reclaim: {archive_root}')
        return
    for path in targets:
        validate_snapshot(path)
    for path in targets:
        size = archive_size(path)
        if args.apply:
            purge(path)
        print(f'{"Reclaimed" if args.apply else "[preview]"} {path.name} ({size / 2**20:.1f} MB)')
    remaining = select_removals(archive_root, 0, True)
    print(f'Kept {len(remaining)} archive snapshot(s) in {archive_root}')
    if not args.apply:
        print('No changes; pass --apply to reclaim.')


if __name__ == '__main__':
    main()
