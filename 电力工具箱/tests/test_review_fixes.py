"""Regression coverage for the five confirmed e24a061 review findings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from toolbox.runtime import load_module
from toolbox_native_domain import invoke

ROOT = Path(__file__).resolve().parents[2]


class ReviewFixTests(unittest.TestCase):
    def test_transaction_rejects_ambiguous_dates_in_all_backends(self):
        module = load_module("review_transaction", ROOT / "交易系统信息更新/update_transaction_info.py")
        target = date(2026, 11, 9)
        valid = dict(date=str(target), max=100, min=10, avg=50, sum=1)
        invalid_records = ([dict(valid, date="2026-11-08")],
                           [dict(valid, date="")], [dict(valid), dict(valid)])
        for backend in ("python", "rust", "auto"):
            with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                for records in invalid_records:
                    payload = {"data": {"dataNetLoadDTOList": [dict(loadType="1", forecastPeriodList=records)]}}
                    with self.subTest(backend=backend, records=records):
                        with self.assertRaises((RuntimeError, ValueError)):
                            module.summarize_load_type(payload, "1", target,
                                period_list_key="forecastPeriodList", label="日前")
                payload["data"]["dataNetLoadDTOList"][0]["forecastPeriodList"] = [valid]
                self.assertEqual(module.summarize_load_type(payload, "1", target,
                    period_list_key="forecastPeriodList", label="日前"), (100, 10, 50, 100000))
        # Strict native calls must reject, rather than hiding behind auto fallback.
        with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            for records in invalid_records:
                payload["data"]["dataNetLoadDTOList"][0]["forecastPeriodList"] = records
                with self.assertRaises(ValueError):
                    invoke("market_payload", "transaction_load_summary", dict(
                        dataset=payload, load_type="1", date=str(target),
                        period_key="forecastPeriodList", label="日前"))

    def test_array_and_table_formulas_survive_overwrite_and_readback(self):
        module = load_module("review_market", ROOT / "市场表更新/update_market_table.py")
        for backend in ("python", "rust"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "market.xlsx"
                book = Workbook()
                sheet = book.active
                sheet.title = module.MARKET_SHEET_NAME
                sheet["A2"], sheet["A3"] = date(2026, 11, 9), date(2026, 11, 10)
                sheet["B2"] = ArrayFormula(ref="B2:B3", text="=C2:C3*2")
                sheet["B3"] = 7
                sheet["D2"] = DataTableFormula(ref="D2:D3", dt2D=False, r1="C2")
                sheet["B3"].number_format = "0.00%"
                book.save(path)
                book.close()
                with mock.patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=backend):
                    with module.workbook_write_session(path):
                        first = module.write_market_row_values(path, date(2026, 11, 9), [42, 99, 10],
                            start_column="B", expected_count=3, label="fixture", overwrite=True)
                        spill = module.write_market_row_values(path, date(2026, 11, 10), [42],
                            start_column="B", expected_count=1, label="fixture", overwrite=True)
                self.assertEqual((first["count"], first["skipped"], spill["skipped"]), (1, 2, 1))
                book = load_workbook(path)
                try:
                    sheet = book.active
                    self.assertEqual(sheet["B2"].data_type, "f")
                    self.assertEqual(sheet["B2"].value.text, "=C2:C3*2")
                    self.assertEqual(sheet["B3"].value, 7)
                    self.assertEqual(sheet["B3"].number_format, "0.00%")
                    self.assertIsInstance(sheet["D2"].value, DataTableFormula)
                    self.assertEqual(sheet["C2"].value, 99)
                finally:
                    book.close()

    def test_pdf_page_and_service_share_cancellation_policy(self):
        from toolbox.daily_pdf_page import DailyPdfParserPage
        from toolbox.service import ToolboxService
        with tempfile.TemporaryDirectory() as folder:
            pdf, excel = Path(folder) / "source.pdf", Path(folder) / "output.xlsx"
            pdf.touch()
            excel.touch()
            for mode in ("parse", "both", "upload"):
                page = SimpleNamespace(
                    pdf_path=mock.Mock(get=lambda: str(pdf)),
                    output_path=mock.Mock(get=lambda: str(excel)),
                    mode=mock.Mock(get=lambda: mode),
                    paths=SimpleNamespace(daily_pdf_parser_dir=ROOT),
                    clear_log=mock.Mock(), build_command=mock.Mock(return_value=[]), run_process=mock.Mock())
                with mock.patch("toolbox.daily_pdf_page.messagebox.askyesno", return_value=True):
                    DailyPdfParserPage.start_parse(page)
                expected = mode == "parse"
                self.assertEqual(page.run_process.call_args.kwargs["cancellable"], expected)
                self.assertEqual(ToolboxService.cancellable("daily-pdf-parser", "run", {"mode": mode}), expected)

    def test_cold_dynamic_login_keeps_exception_identity_and_cli_metadata(self):
        script = r"""
import contextlib, io, json, os
from pathlib import Path
from toolbox.runtime import load_module
import sys
assert 'toolbox_qingneng_login' not in sys.modules
for name, path in [('online', '上网电量抓取/export_online_energy.py'),
                   ('group', '集团每日上传/upload_daily_report.py')]:
    module = load_module('cold_' + name, Path(path))
    from toolbox_qingneng_login import LoginError
    from toolbox.tasks.errors import error_detail, classify_error, ErrorCategory
    from toolbox_cli_login import emit_login_error, ERROR_PREFIX
    assert module.qingneng_login.LoginError is LoginError
    exc = module.qingneng_login.LoginError('credentials', 'fixture rejected')
    assert error_detail(exc)['login_kind'] == 'credentials'
    assert classify_error(exc).category == ErrorCategory.AUTHENTICATION
    os.environ['TOOLBOX_CLI_PROTOCOL'] = '1'
    output = io.StringIO()
    with contextlib.redirect_stdout(output): emit_login_error(exc)
    assert json.loads(output.getvalue().removeprefix(ERROR_PREFIX))['login_kind'] == 'credentials'
print('cold login identity OK')
"""
        completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("cold login identity OK", completed.stdout)
