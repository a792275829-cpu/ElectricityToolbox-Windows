"""Prove production workbook writers select native ZIP/XML, then read results."""
from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from toolbox.runtime import load_module

ROOT = Path(__file__).resolve().parents[2]


class NativeUpdatePipelineTests(unittest.TestCase):
    def test_native_market_readback_format_mismatch_preserves_original(self):
        module = load_module("native_market_corruption_test", ROOT / "市场表更新/update_market_table.py")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            path = Path(directory) / "market.xlsx"
            book = Workbook(); book.active["V2"] = .125
            book.active["V2"].number_format = "0.00%"
            book.save(path); book.close()
            original = path.read_bytes()
            session = module.WorkbookWriteSession(path)
            target = session.get_workbook().active
            target["V2"] = .25; session.record(target["V2"])
            save = module.save_market_workbook
            def corrupt_format(*args, **kwargs):
                result = save(*args, **kwargs)
                candidate = args[2]
                changed = load_workbook(candidate)
                changed.active["V2"].number_format = "0.000"
                changed.save(candidate); changed.close()
                return result
            try:
                with patch.object(module, "save_market_workbook", side_effect=corrupt_format):
                    with self.assertRaisesRegex(RuntimeError, "市场表保存后回读不一致"):
                        session.commit()
            finally:
                session.workbook.close()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".market.*.xlsx")), [])

    def test_transaction_append_uses_native_zip_pipeline(self):
        from test_transaction_info_update import TransactionInfoUpdateTests
        module = load_module("native_transaction_pipeline_test", ROOT / "交易系统信息更新/update_transaction_info.py")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            original = Path(directory) / "交易.xlsx"; output = Path(directory) / "输出.xlsx"
            TransactionInfoUpdateTests._build_fixture(original)
            day = date(2026,9,11)
            from datetime import timedelta
            pair = module.build_daily_load_pair(day, TransactionInfoUpdateTests._payload(day),
                TransactionInfoUpdateTests._payload(day-timedelta(days=3)))
            with patch.object(module, "patch_sheet_xml", side_effect=AssertionError("Python transaction ZIP pipeline was used")):
                bounds = module._write_patched_workbook(original, output, template_start=1, pairs=(pair,))
            self.assertEqual(bounds, (8,13))
            result = load_workbook(output)
            try:
                self.assertEqual(result["Sheet1"]["C8"].value, datetime(2026,9,11))
                self.assertEqual(result["Sheet1"]["J8"].value, datetime(2026,9,8))
                self.assertEqual(result["Sheet1"]["E8"].value, 1000)
                self.assertEqual(result["Sheet1"]["E8"].number_format, "0.0000")
                self.assertEqual(result["保留工作表"]["A1"].value, "不应改动")
            finally: result.close()

    def test_market_typed_cells_and_styles_avoid_python_workbook_save(self):
        module = load_module("native_market_pipeline_test", ROOT / "市场表更新/update_market_table.py")
        with tempfile.TemporaryDirectory(prefix="native-market-中文 ") as directory, patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            path = Path(directory) / "市场.xlsx"
            book = Workbook(); sheet = book.active; sheet.title = "市场"
            sheet.append(["日期", "方式", "比例", "公式"])
            sheet.append([datetime(2026, 10, 27), "单机", .125, "=1+1"])
            sheet["C2"].number_format = "0.00%"
            book.save(path); book.close()
            with ZipFile(path, "a") as archive:
                archive.writestr("custom/preserve.bin", b"preserve-non-target-member")
            session = module.WorkbookWriteSession(path)
            try:
                target = session.get_workbook()["市场"]
                for coordinate, value in {"B2":"双机 & <稳定>", "C2":.2345678901234567,
                                          "E2":True, "F2":None, "G2":"=SUM(1,2)",
                                          "H2":datetime(2026,10,28), "I2":time(12,30),
                                          "J2":timedelta(hours=27)}.items():
                    target[coordinate] = value
                    if coordinate == "B2": target[coordinate].font = Font(bold=True)
                    session.record(target[coordinate])
                with patch.object(session.workbook, "save", side_effect=AssertionError("Python workbook save was used")):
                    session.commit()
            finally:
                if session.workbook is not None: session.workbook.close()
            result = load_workbook(path)
            try:
                sheet = result["市场"]
                self.assertEqual(sheet["B2"].value, "双机 & <稳定>")
                self.assertTrue(sheet["B2"].font.bold)
                self.assertEqual(sheet["C2"].value, .2345678901234567)
                self.assertEqual(sheet["C2"].number_format, "0.00%")
                self.assertIs(sheet["E2"].value, True)
                self.assertIsNone(sheet["F2"].value)
                self.assertEqual(sheet["G2"].value, "=SUM(1,2)")
                self.assertEqual(sheet["H2"].value, datetime(2026,10,28))
                self.assertEqual(sheet["I2"].value, time(12,30))
                self.assertEqual(sheet["J2"].value, timedelta(hours=27))
                self.assertEqual(sheet["D2"].value, "=1+1")
            finally: result.close()
            with ZipFile(path) as archive:
                self.assertEqual(archive.read("custom/preserve.bin"), b"preserve-non-target-member")

    def test_spot_price_date_extension_and_values_use_native_zip_pipeline(self):
        module = load_module("native_spot_pipeline_test", ROOT / "现货价格更新/update_spot_prices.py")
        with tempfile.TemporaryDirectory(prefix="native-spot-中文 ") as directory, patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            original = Path(directory) / "价格.xlsx"; output = Path(directory) / "输出.xlsx"
            book = Workbook(); sheet = book.active; sheet.title = "10月"
            sheet.append(["日期", *[str(hour) for hour in range(24)]])
            sheet.append([datetime(2026,10,27), *[float(hour) for hour in range(24)]])
            sheet["B2"].number_format = "0.0000"; sheet["Z2"] = "=SUM(B2:Y2)"
            epoch = book.epoch; book.save(original); book.close()
            with patch.object(module, "patch_sheet_xml", side_effect=AssertionError("Python spot XML writer was used")):
                module.patch_xlsx_values(original, output,
                    {"10月":{2:tuple(float(i+1) for i in range(24)),3:tuple(float(i+2) for i in range(24))}},
                    date_extensions={"10月":{3:date(2026,10,28)}}, epoch=epoch)
            result = load_workbook(output)
            try:
                sheet = result["10月"]
                self.assertEqual(sheet["A3"].value, datetime(2026,10,28))
                self.assertEqual(sheet["B3"].number_format, "0.0000")
                self.assertEqual(sheet["Z2"].value, "=SUM(B2:Y2)")
                self.assertEqual([sheet.cell(3,c).value for c in range(2,26)], [float(i+2) for i in range(24)])
            finally: result.close()
