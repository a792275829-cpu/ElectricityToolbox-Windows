"""Thin native workbook adapters; no mutation is retried through Python."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import warnings

from toolbox_native_domain import backend_mode, invoke
from toolbox_native_excel import native_binary


def _numbers(changes):
    return {reference: format(float(value), '.15g') for reference, value in changes.items()}


def sheet_paths(path):
    result = invoke('workbook', 'sheet_paths', {'path': str(path)})
    return None if result is None else {item['name']: item['path'] for item in result['sheets']}


def inspect(path, *, columns, selected_sheets, requests=None, capture_cache=True):
    return invoke('workbook', 'inspect', {'path': str(path), 'columns': list(columns),
        'selected_sheets': list(selected_sheets), 'requests': None if requests is None else
        {name: [list(position) for position in values] for name, values in requests.items()},
        'capture_cache': capture_cache})


def patch_sparse_xml(data, changes):
    result = invoke('workbook', 'patch_sparse_xml', {'xml': data.decode('utf-8'), 'changes': _numbers(changes)})
    return None if result is None else result['xml'].encode('utf-8')


def append_rows_xml(data, rows):
    """Rows contain source_row, target_row and coordinate->numeric changes."""
    result = invoke('workbook', 'append_rows_xml', {'xml': data.decode('utf-8'),
        'rows': [{**row, 'changes': _numbers(row['changes'])} for row in rows]})
    return None if result is None else result['xml'].encode('utf-8')


def recalculate_xml(data):
    result = invoke('workbook', 'recalculate_xml', {'xml': data.decode('utf-8')})
    return None if result is None else result['xml'].encode('utf-8')


def extend_dates_xml(data, dates):
    """dates maps row numbers to Excel serials already using the source epoch."""
    result = invoke('workbook', 'extend_dates_xml', {'xml': data.decode('utf-8'),
        'dates': {str(row): format(float(serial), '.15g') for row, serial in dates.items()}})
    return None if result is None else result['xml'].encode('utf-8')


def patch_spot_rows(data, rows):
    result = invoke('workbook', 'patch_spot_rows', {'xml': data.decode('utf-8'),
        'rows': {str(row): [format(float(value), '.15g') for value in values] for row, values in rows.items()}})
    return None if result is None else result['xml'].encode('utf-8')


def patch_cells_xml(data, typed_changes):
    result = invoke('workbook', 'patch_sparse_xml', {'xml': data.decode('utf-8'), 'changes': typed_changes})
    return None if result is None else result['xml'].encode('utf-8')


def patch_package(original, output, changes, *, recalculate=False, append=None,
                  typed_changes=None, replace_parts=None, spot_rows=None, date_extensions=None):
    """Patch sparse numeric inputs and/or clone template rows in the native ZIP.

    Selection returns None only before mutation when no native helper exists or
    Python was explicitly selected. Once selected, native errors propagate.
    """
    if not transaction_available():
        return None
    cells = {name: _numbers(values) for name, values in changes.items()}
    for name, values in (typed_changes or {}).items(): cells.setdefault(name, {}).update(values)
    return invoke('workbook', 'patch_package', {'original': str(original), 'output': str(output),
        'changes': cells,
        'replace_parts': {name: value.decode('utf-8') if isinstance(value, bytes) else value for name,value in (replace_parts or {}).items()},
        'spot_rows': {name: {str(row): [format(float(value),'.15g') for value in values] for row,values in rows.items()} for name,rows in (spot_rows or {}).items()},
        'date_extensions': {name: {str(row):format(float(value),'.15g') for row,value in dates.items()} for name,dates in (date_extensions or {}).items()},
        'recalculate': recalculate, 'append': {name: [{**row, 'changes': _numbers(row['changes'])}
            for row in rows] for name, rows in (append or {}).items()}}, side_effects=True)


def verify_package(original, output, changed_parts):
    return invoke('workbook', 'verify_package', {'original': str(original), 'output': str(output),
        'changed_parts': sorted(changed_parts)})


def transaction_available():
    if backend_mode() == 'python':
        return False
    result = invoke('workbook', 'capabilities', {})
    return result is not None and result.get('transaction') is True


class NativeTransaction:
    """One process owns locks/backups/replacements until verified or rolled back."""
    def __init__(self, prepared, create_backup, *, paths=()):
        self.prepared = tuple(prepared)
        self.create_backup = create_backup
        self.paths = tuple(paths)
        self.process = None
        self.finished = False

    def _request(self, action, **payload):
        process = self.process
        process.stdin.write(json.dumps({'action': action, **payload}, ensure_ascii=False) + '\n')
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            process.wait(timeout=10)
            detail = process.stderr.read().strip()
            raise RuntimeError(detail or 'Rust 工作簿事务进程意外退出')
        response = json.loads(line)
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']

    def begin(self):
        binary = native_binary()
        if binary is None:
            raise RuntimeError('未安装 Rust 工作簿事务后端')
        self.process = subprocess.Popen([str(binary), '--workbook-transaction'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return self._request('begin', items=[{'original': str(item.original.absolute()),
            'temporary': str(item.temporary.absolute()), 'fingerprint': list(item.source_fingerprint)}
            for item in self.prepared], create_backup=self.create_backup,
            timestamp=datetime.now().strftime('%Y%m%d-%H%M%S'), paths=[str(Path(path).resolve()) for path in self.paths])

    def install(self, index):
        return self._request('install', index=index)

    def rollback(self):
        result = self._request('rollback')
        self.finished = True
        return result['notes']

    def finalize(self):
        result = self._request('finalize')
        self.finished = True
        self.cleanup_warnings = tuple(result.get('cleanup_warnings', ()))
        for warning in self.cleanup_warnings:
            warnings.warn(warning, RuntimeWarning, stacklevel=2)
        return {prepared.original: None if item['backup'] is None else Path(item['backup'])
                for prepared, item in zip(self.prepared, result['backups'])}

    def close(self):
        if self.process is None:
            return
        process, self.process = self.process, None
        if not process.stdin.closed:
            process.stdin.close()  # EOF is a native rollback, never kill during replace.
        process.stdin = None
        try:
            _, self.close_diagnostics = process.communicate(timeout=30)
        finally:
            process.stdout.close(); process.stderr.close()

    def __enter__(self):
        try:
            self.begin()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self.finished:
                try:
                    notes = self.rollback()
                except Exception as rollback_error:
                    if exc is None:
                        raise
                    exc.add_note(f'Rust 回滚协议失败，关闭输入后由原生事务恢复：{rollback_error}')
                    notes = []
                if exc is not None:
                    for note in notes: exc.add_note(note)
                elif notes:
                    raise RuntimeError('\n'.join(notes))
        finally:
            self.close()
