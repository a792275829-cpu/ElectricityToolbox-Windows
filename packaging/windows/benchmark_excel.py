"""Reproducible synthetic read benchmark; verify parity before reporting speed."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
import toolbox_native_excel as native


def benchmark(rows=20000, repeats=3, baseline_binary=None):
    result = {'rows': rows, 'columns': 10, 'repeats': repeats, 'seconds': {}}
    previous = os.environ.get('TOOLBOX_XLSX_BACKEND')
    try:
        with tempfile.TemporaryDirectory(prefix='toolbox-benchmark-') as directory:
            path = Path(directory) / '合成数据.xlsx'
            book = Workbook(write_only=True)
            sheet = book.create_sheet('数据')
            for index in range(rows):
                day = WriteOnlyCell(sheet, datetime(2026, 1, 1) + timedelta(days=index % 365))
                day.number_format = 'yyyy-mm-dd'
                sheet.append([day, f'机组{index % 20}', *[index * 10 + column for column in range(8)]])
            book.save(path)
            book.close()
            reference = None
            binaries = {'python': None, 'rust': native.native_binary()}
            if baseline_binary is not None:
                binaries = {'python': None, 'rust_before': baseline_binary.resolve(), 'rust': native.native_binary()}
            result['seconds'] = {mode: [] for mode in binaries}
            # Alternate implementations to reduce background-load/order bias.
            for _ in range(repeats):
                for mode, binary in binaries.items():
                    os.environ['TOOLBOX_XLSX_BACKEND'] = 'python' if mode == 'python' else 'rust'
                    started = time.perf_counter()
                    with patch.object(native, 'native_binary', return_value=binary):
                        source = native.load_value_workbook(path)
                    try:
                        values = [tuple((cell.value, cell.number_format) for cell in row)
                                  for row in source['数据'].iter_rows()]
                    finally:
                        source.close()
                    result['seconds'][mode].append(time.perf_counter() - started)
                    if reference is None:
                        reference = values
                    elif values != reference:
                        raise AssertionError(f'{mode}: cell values or number formats differ')
            result['median_seconds'] = {mode: statistics.median(times) for mode, times in result['seconds'].items()}
            result['speedup'] = result['median_seconds']['python'] / result['median_seconds']['rust']
            if baseline_binary is not None:
                result['rust_before_after_speedup'] = result['median_seconds']['rust_before'] / result['median_seconds']['rust']
            result['parity'] = 'All cell values and number formats match'
    finally:
        if previous is None:
            os.environ.pop('TOOLBOX_XLSX_BACKEND', None)
        else:
            os.environ['TOOLBOX_XLSX_BACKEND'] = previous
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=20000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-binary', type=Path)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error('rows and repeats must be positive')
    result = benchmark(args.rows, args.repeats, args.baseline_binary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
