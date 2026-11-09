"""Native ZIP/XML and filesystem transaction acceptance; synthetic files only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import re
import stat
import warnings
from datetime import datetime, date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from toolbox_native_excel import native_binary


def native(action, **payload):
    binary = native_binary()
    result = subprocess.run([str(binary)], input=json.dumps(dict(protocol=1, operation='workbook', path='', payload=dict(action=action, **payload))), capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)['result']


class NativeWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust')
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def workbook(self, root):
        path = root / 'source.xlsx'
        book = Workbook(); sheet = book.active; sheet.title = '10月'
        sheet['A1'] = 'header'; sheet['A4'] = 10
        sheet['C4'] = 0.125; sheet['C4'].number_format = '0.00%'
        sheet['C4'].font = Font(name='Arial', bold=True)
        sheet['D4'] = '=C4*2'
        book.save(path); book.close()
        return path

    def test_real_native_scan_and_sparse_patch_preserve_package_and_style(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = self.workbook(root); output = root/'output.xlsx'
            scan = native('inspect', path=str(path), columns=[1,3,4], selected_sheets=['10月'], capture_cache=True)
            self.assertEqual({(c['row'],c['column']):c['value'] for c in scan['sheets'][0]['cells']}[(4,4)], '=C4*2')
            from toolbox_native_workbook import patch_package, verify_package
            patch_package(path, output, {'10月': {'C4': 0.25}})
            verify_package(path, output, {'xl/worksheets/sheet1.xml'})
            book=load_workbook(output); self.assertEqual(book.active['C4'].value,0.25)
            self.assertEqual(book.active['C4'].number_format,'0.00%')
            self.assertEqual(book.active['D4'].value,'=C4*2'); self.assertTrue(book.active['C4'].font.bold); book.close()
            with ZipFile(path) as a, ZipFile(output) as b:
                self.assertEqual(a.namelist(),b.namelist())
                for name in a.namelist():
                    if name != 'xl/worksheets/sheet1.xml': self.assertEqual(a.read(name),b.read(name))

    def test_noop_package_is_byte_identical(self):
        from toolbox_native_workbook import patch_package
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=self.workbook(root); output=root/'output.xlsx'
            patch_package(path,output,{})
            self.assertEqual(path.read_bytes(),output.read_bytes())

    def test_shared_formula_master_replacement_preserves_relative_absolute_refs_and_literals(self):
        from toolbox_native_workbook import patch_sparse_xml
        source=b'<worksheet><sheetData><row r="4"><c r="C4"><f t="shared" si="0" ref="C4:D4">SUM(A4,$B$2)+IF(A4&gt;0,"A4&amp;",0)</f><v>1</v></c><c r="D4"><f t="shared" si="0"/><v>2</v></c></row></sheetData></worksheet>'
        result=patch_sparse_xml(source,{'C4':10})
        self.assertIn(b'<c r="C4"><v>10</v></c>',result)
        self.assertIn(b'<f>SUM(B4,$B$2)+IF(B4&gt;0,"A4&amp;",0)</f><v>2</v>',result)

    def test_shared_translation_preserves_quoted_sheet_and_structured_names_and_shifts_ranges(self):
        from toolbox_native_workbook import patch_sparse_xml
        source=b'<worksheet><sheetData><row r="4"><c r="C4"><f t="shared" si="0" ref="C4:D5">SUM(A:A,$B:C,1:4,$2:5)+\'A1\'!A4+Table1[A1]</f><v>1</v></c><c r="D5"><f t="shared" si="0"/><v>2</v></c></row></sheetData></worksheet>'
        result=patch_sparse_xml(source,{'C4':10})
        self.assertIn(b"SUM(B:B,$B:D,2:5,$2:6)+'A1'!B5+Table1[A1]",result)

    def test_clone_rows_and_recalculation_xml_match_original_engines(self):
        from toolbox_native_workbook import append_rows_xml, recalculate_xml
        from toolbox.runtime import load_module
        root=Path(__file__).resolve().parents[2]
        module=load_module('native_workbook_benefit_oracle',root/'功率调整收益统计表更新/update_power_benefit.py')
        data=b'<workbook><calcPr calcId="123" fullCalcOnLoad="0"/></workbook>'
        self.assertEqual(recalculate_xml(data),module.request_recalculation(data))
        source=b'<worksheet><dimension ref="A1:O9"/><sheetData><row r="4" ht="18"><c r="C4" s="3"><v>1</v></c><c r="O4" s="7"><f>E4-F4</f><v>2</v></c></row></sheetData></worksheet>'
        result=append_rows_xml(source,[{'source_row':4,'target_row':10,'changes':{'C10':40000}}])
        self.assertIn(b'<dimension ref="A1:O10"/>',result)
        self.assertIn(b'<row r="10" ht="18"><c r="C10" s="3"><v>40000</v></c><c r="O10" s="7"><f>E4-F4</f><v>2</v></c></row>',result)

    def test_native_verification_rejects_untargeted_member_change(self):
        from toolbox_native_workbook import verify_package
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.workbook(root);output=root/'output.xlsx'
            with ZipFile(path) as a,ZipFile(output,'w') as b:
                for info in a.infolist():
                    data=a.read(info.filename)
                    if info.filename=='xl/styles.xml': data=data.replace(b'Arial',b'Other')
                    b.writestr(info,data)
            with self.assertRaisesRegex(ValueError,'非目标部件'):verify_package(path,output,{'xl/worksheets/sheet1.xml'})

    def test_native_inspection_retains_date_epochs_sparse_styles_cached_and_merged_semantics(self):
        from openpyxl.utils.datetime import CALENDAR_MAC_1904, to_excel
        from toolbox_workbook_inspection import load_inspection_pair, UnsupportedInspection
        from openpyxl.styles import Border, Side
        for epoch1904 in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder); path=self.workbook(root)
                book=load_workbook(path)
                if epoch1904: book.epoch=CALENDAR_MAC_1904
                book.active['A4']=datetime(2026,10,1);book.active['A4'].number_format='yyyy-mm-dd'
                book.active.merge_cells('A4:A6');book.active['J4']='=1+1';book.save(path);book.close()
                original=root/'raw.xlsx';path.rename(original)
                with ZipFile(original) as a,ZipFile(path,'w') as b:
                    for info in a.infolist():
                        data=a.read(info.filename)
                        if info.filename=='xl/worksheets/sheet1.xml':
                            text=data.decode(); serial=to_excel(datetime(2026,10,1),CALENDAR_MAC_1904 if epoch1904 else datetime(1899,12,30))
                            text=re.sub(r'(<c\b[^>]*r="A4"[^>]*>).*?</c>',rf'\g<1><f>DATE(2026,10,1)</f><v>{serial}</v></c>',text)
                            text=text.replace('<row r="4">','<row r="4"><c r="B4"><f t="shared" si="0" ref="B4:J4">A4+$A$1</f><v>2</v></c>')
                            text=re.sub(r'<c\b[^>]*r="J4"[^>]*>.*?</c>','<c r="J4"><f t="shared" si="0"/><v>2</v></c>',text)
                            data=text.encode()
                        b.writestr(info,data)
                args=dict(columns=(1,3,10),target_columns=(3,10),sheet_filter=lambda n:True)
                books=[]
                for mode in ('python','rust'):
                    with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND=mode): books.append(load_inspection_pair(path,**args))
                for native_view,reference_view in zip(books[1],books[0]):
                    for coordinate in ('A4','A5','C4','J4','L9'):
                        actual,expected=native_view['10月'][coordinate],reference_view['10月'][coordinate]
                        self.assertEqual((actual.value,actual.number_format,actual._style,actual.style_id),(expected.value,expected.number_format,expected._style,expected.style_id))
                book=load_workbook(path);book.active['A6'].border=Border(bottom=Side(style='thick'));book.save(path);book.close()
                with self.assertRaisesRegex(UnsupportedInspection,'边框'): load_inspection_pair(path,**args)

    def test_fingerprint_matches_python_handle_identity(self):
        from toolbox_workbook_commit import workbook_fingerprint
        with tempfile.TemporaryDirectory() as folder:
            path=self.workbook(Path(folder))
            actual=workbook_fingerprint(path)
            with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND='python'): expected=workbook_fingerprint(path)
            self.assertEqual(actual,expected)

    def test_array_formula_metadata_uses_existing_compatibility_wrapper(self):
        from toolbox_workbook_inspection import load_inspection_pair
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.workbook(root);original=root/'original.xlsx';path.rename(original)
            with ZipFile(original) as a,ZipFile(path,'w') as b:
                for info in a.infolist():
                    data=a.read(info.filename)
                    if info.filename=='xl/worksheets/sheet1.xml': data=data.replace(b'<f>C4*2</f>',b'<f t="array" ref="D4:D5" ca="1">C4*2</f>')
                    b.writestr(info,data)
            book,_=load_inspection_pair(path,columns=(4,),target_columns=(4,),sheet_filter=lambda _:True)
            formula=book['10月']['D4'].value
            self.assertEqual((formula.ref,formula.text),('D4:D5','=C4*2'))

    def test_numeric_inspection_preserves_large_integer_and_overflow_semantics(self):
        from toolbox_workbook_inspection import load_inspection_pair
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.workbook(root);original=root/'original.xlsx';path.rename(original)
            with ZipFile(original) as a,ZipFile(path,'w') as b:
                for info in a.infolist():
                    data=a.read(info.filename)
                    if info.filename=='xl/worksheets/sheet1.xml':
                        data=data.replace(b'<v>0.125</v>',b'<v>123456789012345678901</v>').replace(b'<f>C4*2</f><v></v>',b'<v>1e309</v>')
                    b.writestr(info,data)
            book,_=load_inspection_pair(path,columns=(3,4),target_columns=(3,4),sheet_filter=lambda _:True)
            self.assertEqual(book['10月']['C4'].value,123456789012345678901)
            self.assertEqual(book['10月']['D4'].value,float('inf'))

    def test_corrupt_optional_xml_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'source.xlsx';raw=root/'raw.xlsx';Workbook().save(raw)
            with ZipFile(raw) as a,ZipFile(path,'w') as b:
                for info in a.infolist(): b.writestr(info,b'\xff' if info.filename=='xl/styles.xml' else a.read(info.filename))
            with self.assertRaises(AssertionError):native('inspect',path=str(path),columns=[1],selected_sheets=['Sheet'])

    def test_spot_date_extension_and_rows_match_original_xml(self):
        from toolbox.runtime import load_module
        from openpyxl.utils.datetime import to_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
        module=load_module('native_workbook_spot_oracle',Path(__file__).resolve().parents[2]/'现货价格更新/update_spot_prices.py')
        source=b'<worksheet><dimension ref="A1:Y4"/><sheetData><row r="4" ht="20"><c r="A4" s="1"><v>45000</v></c><c r="B4" s="2"><v>100</v></c><c r="Y4" s="3"><v>200</v></c></row></sheetData></worksheet>'
        dates={5:date(2026,10,1),6:date(2026,10,2)}
        rows={5:tuple(0.123456789123456+index for index in range(24)),6:tuple(range(24))}
        for epoch in (CALENDAR_WINDOWS_1900,CALENDAR_MAC_1904):
            expected=module.extend_sheet_dates(source,dates,epoch=epoch)
            actual=native('extend_dates_xml',xml=source.decode(),dates={str(row):format(to_excel(day,epoch),'.15g') for row,day in dates.items()})['xml'].encode()
            self.assertEqual(actual,expected)
            expected=module.patch_sheet_xml(expected,rows)
            actual=native('patch_spot_rows',xml=actual.decode(),rows={str(row):[format(v,'.15g') for v in values] for row,values in rows.items()})['xml'].encode()
            self.assertEqual(actual,expected)

    def test_typed_cells_and_styles_part_read_back_with_exact_numeric_policy(self):
        from toolbox_native_workbook import patch_package
        from openpyxl.styles.stylesheet import write_stylesheet
        from openpyxl.xml.functions import tostring
        from openpyxl.compat.strings import safe_string
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.workbook(root);output=root/'output.xlsx';book=load_workbook(path)
            book.active['C4'].number_format='0.000000%';style=book.active['C4'].style_id
            styles=tostring(write_stylesheet(book));book.close()
            typed={'A1':{'kind':'inlineStr','value':' 正常<&>运行 '},'A4':{'kind':'b','value':True},
                'C4':{'kind':'n','value':safe_string(0.1234567891234567),'style_id':style},
                'D4':{'kind':'f','value':'=C4*3'},'B4':{'kind':'null','style_id':style},
                'F8':{'kind':'n','value':'12','style_id':style}}
            patch_package(path,output,{},typed_changes={'10月':typed},replace_parts={'xl/styles.xml':styles})
            book=load_workbook(output);sheet=book.active
            self.assertEqual(sheet['A1'].value,' 正常<&>运行 ');self.assertIs(sheet['A4'].value,True)
            self.assertEqual(sheet['C4'].value,float(safe_string(0.1234567891234567)))
            self.assertEqual(sheet['C4'].number_format,'0.000000%');self.assertEqual(sheet['D4'].value,'=C4*3')
            self.assertIsNone(sheet['B4'].value);self.assertEqual(sheet['B4'].style_id,style);book.close()
            self.assertEqual(sheet['F8'].value,12);self.assertEqual(sheet.calculate_dimension(),'A1:F8')

    def test_auto_inspection_uses_native_sheet_scan(self):
        from toolbox_workbook_inspection import load_inspection_pair
        with tempfile.TemporaryDirectory() as folder:
            path=self.workbook(Path(folder))
            with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND='auto'), patch('toolbox_workbook_inspection._python_inspection_pair',side_effect=AssertionError('Python scan ran')):
                book,_=load_inspection_pair(path,columns=(3,),target_columns=(3,),sheet_filter=lambda _:True)
            self.assertEqual(book['10月']['C4'].value,0.125)

    def prepare(self, root, name):
        from toolbox_workbook_commit import workbook_fingerprint
        original=root/f'{name}.xlsx'; temporary=root/f'.{name}.tmp.xlsx'
        original.write_bytes(b'original'); temporary.write_bytes(b'prepared')
        return SimpleNamespace(original=original,temporary=temporary,source_fingerprint=workbook_fingerprint(original))

    def test_verify_failure_rolls_back_all_replacements(self):
        from toolbox_workbook_commit import commit_workbooks
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); items=(self.prepare(root,'a'),self.prepare(root,'b'))
            def verify(path,item):
                self.assertEqual(path.read_bytes(),b'prepared')
                if item is items[1]: raise ValueError('business verification failed')
            with patch('toolbox_workbook_commit._commit_locked',side_effect=AssertionError('Python commit used')), self.assertRaisesRegex(ValueError,'business verification failed'):
                commit_workbooks(items,verify,create_backup=False)
            self.assertEqual({p.name:p.read_bytes() for p in root.iterdir()},{'a.xlsx':b'original','b.xlsx':b'original'})

    def test_external_edit_preserves_recovery_copy(self):
        from toolbox_workbook_commit import commit_workbooks
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); item=self.prepare(root,'a')
            def verify(path,_):
                path.write_bytes(b'external save'); raise ValueError('failed')
            with self.assertRaises(ValueError) as raised: commit_workbooks((item,),verify,create_backup=False)
            self.assertEqual(item.original.read_bytes(),b'external save')
            copies=list(root.glob('.a.*.rollback.xlsx')); self.assertEqual(len(copies),1)
            self.assertEqual(copies[0].read_bytes(),b'original')
            self.assertIn(str(copies[0]),' '.join(raised.exception.__notes__))

    def test_eof_rolls_back_and_releases_cross_process_lock(self):
        from toolbox_native_workbook import NativeTransaction
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); item=self.prepare(root,'a')
            transaction=NativeTransaction((item,),False); transaction.begin(); transaction.install(0)
            from toolbox_platform import directory_lock
            with self.assertRaises(BlockingIOError):
                with directory_lock(root): pass
            other=NativeTransaction((),False,paths=[item.original])
            with self.assertRaisesRegex(RuntimeError,'正在提交'): other.begin()
            other.close()
            transaction.process.stdin.close(); transaction.process.wait(timeout=5); transaction.close()
            self.assertEqual(item.original.read_bytes(),b'original')
            with NativeTransaction((),False,paths=[item.original]): pass
            self.assertEqual(set(root.iterdir()),{item.original})

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only deletion failure reproduction')
    def test_finalize_cleanup_failure_keeps_verified_commit_and_reports_retained_copy(self):
        from toolbox_workbook_commit import commit_workbooks
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);items=(self.prepare(root,'a'),self.prepare(root,'b'));copies=[];handles=[]
            def verify(path,item):
                if item is items[1]:
                    copies.extend(root.glob('.b.*.rollback.xlsx'));self.assertEqual(len(copies),1)
                    handle=kernel.CreateFileW(str(copies[0]),0x80000000,3,None,3,0,None)
                    self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
                    handles.append(handle)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    result=commit_workbooks(items,verify,create_backup=False)
                self.assertEqual(result,{item.original:None for item in items})
                self.assertEqual([item.original.read_bytes() for item in items],[b'prepared',b'prepared'])
                self.assertEqual(copies[0].read_bytes(),b'original')
                self.assertIn(str(copies[0]),' '.join(str(w.message) for w in caught))
                self.assertFalse(list(root.glob('.a.*.rollback.xlsx')))
            finally:
                for handle in handles:kernel.CloseHandle(handle)
                for copy in copies:
                    if copy.exists(): copy.chmod(stat.S_IWRITE)

    def test_lock_only_destination_need_not_exist_and_contends_with_python_lock(self):
        from toolbox_workbook_commit import workbook_commit_lock
        from toolbox_platform import directory_lock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'not_created.xlsx'
            with workbook_commit_lock((output,)):
                with self.assertRaises(BlockingIOError):
                    with directory_lock(root): pass
                self.assertFalse(output.exists())
            with directory_lock(root): pass
            self.assertEqual(list(root.iterdir()),[])

    def test_corrupt_present_style_ids_are_rejected(self):
        from toolbox_workbook_inspection import load_inspection_pair
        for identifier in ('broken','-1','999999999',''):
            with self.subTest(style=identifier),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path=self.workbook(root);raw=root/'raw.xlsx';path.rename(raw)
                with ZipFile(raw) as a,ZipFile(path,'w') as b:
                    for info in a.infolist():
                        data=a.read(info.filename)
                        if info.filename=='xl/worksheets/sheet1.xml':
                            data=re.sub(rb'(<c r="C4" s=")[^"]*(")',lambda m:m[1]+identifier.encode()+m[2],data)
                        b.writestr(info,data)
                with self.assertRaises(ValueError):
                    load_inspection_pair(path,columns=(3,),target_columns=(3,),sheet_filter=lambda _:True)

    @unittest.skipUnless(os.name == 'posix', 'ordinary Unix user permission regression')
    def test_readonly_source_can_be_backed_up_and_replaced_in_writable_directory(self):
        from toolbox_workbook_commit import commit_workbooks, workbook_fingerprint
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);item=self.prepare(root,'readonly');item.original.chmod(0o444)
            item.source_fingerprint=workbook_fingerprint(item.original)
            try:
                commit_workbooks((item,),lambda *_:None,create_backup=True)
                self.assertEqual(item.original.read_bytes(),b'prepared')
                backups=list(root.glob('readonly.备份-*.xlsx'));self.assertEqual(len(backups),1)
                self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode),0o444)
            finally:
                for p in root.iterdir():p.chmod(0o600)


if __name__ == '__main__': unittest.main()
