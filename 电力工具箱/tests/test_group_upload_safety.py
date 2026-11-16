"""Cross-day generation and actual import-control flow with isolated files/API replies."""
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from openpyxl import Workbook, load_workbook
from toolbox.runtime import load_module
from toolbox.upload_service import run_group_upload_with_staging

TOOLBOX_TEST_TAGS = ()
ROOT = Path(__file__).resolve().parents[2]


class GroupUploadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("group_upload_safety_20261116", ROOT / "集团每日上传/upload_daily_report.py")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_two_days_energy_only_staging_recomputes_saved_formula_and_keeps_inputs(self):
        module = self.module
        source = self.folder / "广东-省内数据-20261101.xlsx"
        book = Workbook()
        book.active.title = "省内数据"
        book.active["C2"] = "=100000-20000-100"
        book.active["C2"].number_format = "0.00"
        book.save(source)
        book.close()
        before = source.read_bytes()
        for day, capacity, expected in (("2026-11-02", 50000, 49900), ("2026-11-03", 60000, 39900)):
            energy = self.folder / f"广东-能销数据-{day.replace('-', '')}.xlsx"
            energy.write_bytes(b"isolated-energy")
            def generate(paths, *, force):
                selected_day, uploads = module.prepare_upload_files(paths)
                module.update_province_excel_for_upload(object(), selected_day, uploads)
            with (mock.patch.object(module, "run_api_upload", side_effect=generate),
                  mock.patch.object(module, "fetch_startup_max", return_value=20),
                  mock.patch.object(module, "fetch_capacity_average", return_value=capacity)):
                run_group_upload_with_staging(module, [energy], force=False, on_log=lambda _: None)
            output = self.folder / f"广东-省内数据-{day.replace('-', '')}.xlsx"
            for cached in (True, False):
                book = load_workbook(output, data_only=cached)
                try:
                    self.assertEqual(book.sheetnames, ["省内数据"])
                    self.assertEqual(book.active["C2"].value, expected)
                    self.assertEqual(book.active["C2"].number_format, "0.00")
                    self.assertEqual(book.active["A2"].value, datetime.fromisoformat(day))
                    self.assertIn(module.PROVINCE_FORMULA_NAME, book.defined_names)
                finally:
                    book.close()
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(energy.read_bytes(), b"isolated-energy")

    def response(self, payload):
        return mock.Mock(status=200, text=lambda: json.dumps(payload))

    def test_external_save_during_fetch_is_not_overwritten(self):
        module = self.module
        source = self.folder / "广东-省内数据-20261102.xlsx"
        energy = self.folder / "广东-能销数据-20261102.xlsx"
        energy.write_bytes(b"energy")
        book = Workbook()
        book.active["C2"] = "=100000-20000-100"
        book.save(source)
        book.close()
        def outside_save(*args):
            book = load_workbook(source)
            book.active["D1"] = "外部修改"
            book.save(source)
            book.close()
            return 20
        uploads = [module.UploadFile(source, "省内", "2026-11-02"),
                   module.UploadFile(energy, "能销", "2026-11-02")]
        with (mock.patch.object(module, "fetch_startup_max", side_effect=outside_save),
              mock.patch.object(module, "fetch_capacity_average", return_value=50000)):
            with self.assertRaisesRegex(RuntimeError, "工作簿被修改"):
                module.update_province_excel_for_upload(object(), "2026-11-02", uploads)
        book = load_workbook(source)
        try:
            self.assertEqual(book.active["D1"].value, "外部修改")
            self.assertEqual(book.active["C2"].value, "=100000-20000-100")
        finally:
            book.close()
        self.assertEqual(set(self.folder.iterdir()), {source, energy})

    def status(self, **changes):
        row = dict(date="2026-11-02", provinceId="44", groupDataType="DAY_MARKET_INFO",
                   dataStatus=1, updateTime="2026-11-02 10:00:00")
        return {**row, **changes}

    def test_status_rejects_wrong_date_type_province_and_duplicate_records(self):
        for rows in ([self.status(date="2026-11-01")], [self.status(provinceId="45")],
                     [self.status(groupDataType="DAY_ENERGY_SCALE")], [self.status(), self.status()]):
            context = mock.Mock()
            context.request.get.return_value = self.response({"retCode": "T200", "data": rows})
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, "唯一"):
                self.module.get_daily_status(context, "2026-11-02", "DAY_MARKET_INFO", "44")

    def run_upload(self, payload, after_rows, succeeds):
        module = self.module
        path = self.folder / "省内20261102.xlsx"
        path.write_bytes(b"isolated-upload")
        uploads = [module.UploadFile(path, "省内", "2026-11-02")]
        context = mock.Mock(pages=[])
        context.request.post.return_value = self.response(payload)
        replies = [self.response({"retCode": "T200", "data": []})]
        replies += [self.response({"retCode": "T200", "data": after_rows})] * 3
        context.request.get.side_effect = replies
        logs = []
        with (mock.patch.object(module, "AUTH_LOCK_PATH", self.folder / "auth.lock"),
              mock.patch.object(module, "prepare_upload_files", return_value=("2026-11-02", uploads)),
              mock.patch.object(module, "load_config", return_value=module.Config()),
              mock.patch.object(module, "sync_playwright", return_value=nullcontext(object())),
              mock.patch.object(module, "launch_context", return_value=context),
              mock.patch.object(module, "update_province_excel_for_upload", return_value=uploads),
              mock.patch.object(module, "create_daily_report") as report,
              mock.patch.object(module, "save_auth_state"),
              mock.patch.object(module.threading, "Timer"),
              mock.patch.object(module.time, "sleep"),
              mock.patch.object(module, "log", side_effect=logs.append)):
            if succeeds:
                module.run_api_upload([path])
                report.assert_called_once()
                self.assertTrue(any("导入成功" in line for line in logs))
            else:
                with self.assertRaisesRegex(RuntimeError, "结果待核对"):
                    module.run_api_upload([path])
                report.assert_not_called()
                self.assertFalse(any("导入成功" in line for line in logs))
        context.request.post.assert_called_once()
        context.close.assert_called_once()
        self.assertFalse((self.folder / "auth.lock").exists())

    def test_unknown_import_protocol_never_reports_success_or_replays_submission(self):
        for payload in ({}, None, [], {"retCode": "T200"}, {"retCode": "T200", "data": None},
                        {"retCode": "T200", "data": {}}, {"retCode": "T200", "data": ["invalid file"]}):
            with self.subTest(payload=payload):
                self.run_upload(payload, [self.status()], False)

    def test_valid_reply_requires_matching_completed_readback_before_report(self):
        self.run_upload({"retCode": "T200", "data": []}, [self.status()], True)
        for rows in ([], [self.status(dataStatus=0)], [self.status(updateTime="")],
                     [self.status(date="2026-11-01")]):
            with self.subTest(rows=rows):
                self.run_upload({"retCode": "T200", "data": []}, rows, False)
