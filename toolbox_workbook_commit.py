"""Commit prepared XLSX files with verification and rollback."""
from __future__ import annotations

import os
import hashlib
from toolbox_platform import directory_lock
import shutil
import uuid
from contextlib import contextmanager, ExitStack
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, TypeVar
from toolbox_native_domain import invoke
from toolbox_native_workbook import NativeTransaction, transaction_available


FileFingerprint = tuple[int, int, int, int, int, str]


def workbook_fingerprint(path: Path) -> FileFingerprint:
    """Identify the exact input, rejecting a file changed while it is hashed."""
    try:
        result = invoke("workbook", "fingerprint", {"path": str(path)})
    except ValueError as exc:
        if not path.exists():
            raise FileNotFoundError(path) from exc
        raise RuntimeError(str(exc)) from exc
    if result is not None:
        return tuple(result)
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    with path.open("rb") as source:
        before = identity(os.fstat(source.fileno()))
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        if os.name == "nt":
            # Windows path-stat may return a cached creation timestamp whereas
            # fstat uses handle metadata. Compare two handles, retaining inode,
            # mtime, ctime and size checks (do not weaken conflict detection).
            with path.open("rb") as current:
                named_identity = identity(os.fstat(current.fileno()))
        else:
            named_identity = identity(path.stat())
        if before != identity(os.fstat(source.fileno())) or before != named_identity:
            raise RuntimeError(f"读取期间工作簿被修改，停止写入：{path}")
    return (*before, digest)


def assert_workbook_unchanged(path: Path, expected: FileFingerprint) -> None:
    try:
        current = workbook_fingerprint(path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"原工作簿已被移走，停止写入：{path}") from exc
    if current != expected:
        raise RuntimeError(f"准备或提交期间工作簿被修改，停止写入：{path}")


@contextmanager
def workbook_commit_lock(paths):
    """Serialize commits in each destination directory without leaving lock files."""
    paths = tuple(paths)
    if transaction_available():
        with NativeTransaction((), False, paths=paths):
            yield
        return
    with ExitStack() as stack:
        for parent in sorted({Path(path).resolve().parent for path in paths}):
            try:
                stack.enter_context(directory_lock(parent))
            except BlockingIOError as exc:
                raise RuntimeError(f"该目录有其他工作簿正在提交，请稍后重试：{parent}") from exc
        yield


class PreparedFile(Protocol):
    original: Path
    temporary: Path
    source_fingerprint: FileFingerprint


T = TypeVar("T", bound=PreparedFile)


def commit_workbooks(
    prepared: tuple[T, ...],
    verify: Callable[[Path, T], None],
    *,
    create_backup: bool,
) -> dict[Path, Path | None]:
    try:
        if transaction_available():
            with NativeTransaction(prepared, create_backup) as transaction:
                for index, item in enumerate(prepared):
                    transaction.install(index)
                    verify(item.original, item)
                return transaction.finalize()
        with workbook_commit_lock(item.original for item in prepared):
            return _commit_locked(prepared, verify, create_backup=create_backup)
    finally:
        for item in prepared:
            item.temporary.unlink(missing_ok=True)


def _commit_locked(prepared, verify, *, create_backup):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rollbacks: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    replaced: list[T] = []
    installed_fingerprints: dict[Path, FileFingerprint] = {}
    preserve: set[Path] = set()
    try:
        if len({item.original.resolve() for item in prepared}) != len(prepared):
            raise ValueError("同一工作簿不能重复提交。")
        for item in prepared:
            assert_workbook_unchanged(item.original, item.source_fingerprint)
        for item in prepared:
            rollback = item.original.with_name(
                f".{item.original.stem}.{uuid.uuid4().hex}.rollback.xlsx"
            )
            rollbacks[item.original] = rollback
            shutil.copy2(item.original, rollback)
            backup = None
            if create_backup:
                backup = item.original.with_name(
                    f"{item.original.stem}.备份-{timestamp}{item.original.suffix}"
                )
                if backup.exists():
                    backup = backup.with_name(f"{backup.stem}-{uuid.uuid4().hex[:8]}{backup.suffix}")
                shutil.copy2(item.original, backup)
            backups[item.original] = backup
        for item in prepared:
            installed_fingerprints[item.original] = workbook_fingerprint(item.temporary)
            assert_workbook_unchanged(item.original, item.source_fingerprint)
            os.replace(item.temporary, item.original)
            # Register immediately: verification can fail after replacement.
            replaced.append(item)
            verify(item.original, item)
    except Exception as error:
        for item in reversed(replaced):
            rollback = rollbacks[item.original]
            try:
                current = workbook_fingerprint(item.original)
                expected = installed_fingerprints[item.original]
                # Rename changes ctime; all other identity fields and content
                # must still match the file we installed, including after verify.
                if current[:4] != expected[:4] or current[5] != expected[5]:
                    raise RuntimeError("提交后工作簿已被外部修改，保留当前文件")
                os.replace(rollback, item.original)
            except Exception as restore_error:
                preserve.add(rollback)
                error.add_note(f"回滚失败，原文件副本保留于 {rollback}：{restore_error}")
        raise
    finally:
        for rollback in rollbacks.values():
            if rollback not in preserve:
                rollback.unlink(missing_ok=True)
    return backups
