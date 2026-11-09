import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
BINARY = Path(os.environ.get('TOOLBOX_NATIVE_BINARY', ROOT / '500kV断面分析/bin/toolbox-price-xlsx.exe'))

def native(action, **payload):
    response = subprocess.run([str(BINARY)], input=json.dumps(dict(protocol=1, operation='trading', path='', payload=dict(action=action, **payload)), ensure_ascii=False), capture_output=True, text=True, encoding='utf-8')
    if response.returncode:
        raise AssertionError('Trading Rust operation unavailable: ' + response.stderr)
    result = json.loads(response.stdout)
    if 'error' in result:
        raise AssertionError(result['error'])
    return result['result']

class TradingNativeParityTests(unittest.TestCase):
    def test_merge_preserves_order_and_counts(self):
        segments = [dict(start=0, end=100, price=300), dict(start=100, end=200, price=300), dict(start=200, end=250, price=500)]
        self.assertEqual(native('offer_merge', segments=segments), [dict(start=0, end=200, price=300, source_count=2), dict(start=200, end=250, price=500, source_count=1)])


# Domain fixture: both rectangular exports and vertically repeated blocks, plus
# shifted company summary labels and renewable values. No production data.
import importlib.util
import sys
from tempfile import TemporaryDirectory
from unittest import mock
from openpyxl import Workbook, load_workbook

SCRIPT = ROOT / '电力工具脚本/电力交易分析工具/generate_electricity_report.py'
spec = importlib.util.spec_from_file_location('trading_parity_report', SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
SUMMARY_SCRIPT = ROOT / '电力工具脚本/电量汇总工具/summarize_511_excel.py'
spec = importlib.util.spec_from_file_location('trading_parity_summary', SUMMARY_SCRIPT)
summary = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = summary
spec.loader.exec_module(summary)


def fixture(new=False):
    book = Workbook()
    book.active.title = '新' if new else '日前'
    ws = book.active
    if new:
        ws['A8'], ws['D8'], ws['G8'] = '火电', '中长期', '日前出清'
        brief = book.create_sheet('快报')
        for row in range(7, 20):
            brief.cell(row, 3, 100 + row)
            brief.cell(row, 11, 0.125)
    for row in range(4, 22):
        for col in range(3, 12): ws.cell(row, col, row * col / 10)
    if new: ws['A8'], ws['D8'], ws['G8'] = '火电', '中长期', '日前出清'
    comp = book.create_sheet('运行补偿')
    comp.cell(30, 1, '汕头#1机组')
    comp.cell(31, 17, '厂用电率'); comp.cell(31, 18, 0.05)
    comp.cell(32, 17, '段号')
    for r, start, end, price in [(33, 0, 100, 300), (34, 100, 200, 300), (35, 200, 300, 500)]:
        for c, value in [(18,start),(19,end),(20,price)]: comp.cell(r,c,value)
    for plant in report.THERMAL_COMPANIES:
        ws = book.create_sheet(plant)
        ws.cell(1,2,'#1机组'); ws.cell(2,4,'实时电量'); ws.cell(1,11,'偏差')
        for h in range(24):
            ws.cell(h+3,1,f'{h}:00')
            for c,v in [(2,h+1),(3,300),(4,h+2),(5,310)]: ws.cell(h+3,c,v)
        # A trailing vertical block must override the horizontal representation.
        if plant == '海门':
            ws.cell(50,1,'1号机')
            for h in range(24):
                for c,v in [(1,f'{h}:00'),(2,9),(3,200),(5,10),(6,300),(7,11),(8,350),(10,250)]: ws.cell(53+h,c,v)
            ws.cell(85,1,'末尾')
    for plant in report.RENEWABLE_UNITS:
        ws = book.create_sheet(plant)
        for h in range(24):
            for c,v in [(1,f'{h}:00'),(2,10),(3,300),(4,12),(5,350),(12,8),(13,250)]: ws.cell(h+4,c,v)
        ws['AE28'] = 321.5
    return book


def sheets(book):
    return {ws.title: [list(row) for row in ws.iter_rows(values_only=True)] for ws in book.worksheets}


def reference(book):
    builder = report.ReportBuilder('2026-10-27.xlsx')
    builder.wb = book
    with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
        builder._parse_summary()
        builder.offer_cards = builder._parse_offer_cards()
        for plant in report.THERMAL_COMPANIES: builder.hourly.update(builder._parse_blocks(plant, plant))
        builder._parse_renewables()
        builder.compare_mode = 'realtime_vs_dayahead' if builder._has_realtime_data() else 'dayahead_vs_ml'
        builder._fill_dongguan_summary()
    return builder

class TradingBulkParityTests(unittest.TestCase):
    def test_full_report_old_and_new_layout(self):
        for new in (False, True):
            with self.subTest(new=new):
                book = fixture(new)
                expected = reference(book)
                actual = native('report', sheets=sheets(book), companies=expected.companies)
                self.assertEqual(actual['summary'], expected.summary)
                self.assertEqual(actual['hourly'], expected.hourly)
                self.assertEqual(actual['offer_cards'], expected.offer_cards)
                self.assertEqual(actual['compare_mode'], expected.compare_mode)
                self.assertEqual(actual['unit_order'], list(expected.hourly))
                for plant in report.THERMAL_COMPANIES:
                    ws=book[plant]
                    for r in range(3,27): ws.cell(r,4,0);ws.cell(r,5,0)
                    if plant=='海门':
                        for r in range(53,77): ws.cell(r,7,0);ws.cell(r,8,0)
                for plant in report.RENEWABLE_UNITS:
                    for r in range(4,28): book[plant].cell(r,4,0);book[plant].cell(r,5,0)
                expected=reference(book)
                actual=native('report',sheets=sheets(book),companies=expected.companies)
                self.assertEqual(actual['compare_mode'],'dayahead_vs_ml')
                self.assertEqual(actual['summary'],expected.summary)
                self.assertEqual(actual['hourly'],expected.hourly)

    def test_offer_boundaries_and_classification(self):
        builder = reference(fixture())
        unit='汕头#1机组'
        for mwh in (0, -0.05, 0.05, 0.051, 10, 95, 190, 300, (200+0.05-1e-8)*0.95, (200+0.05+1e-8)*0.95):
            for price in (299.95, 300, 300.05, 500, 900):
                actual = native('offer', card=builder.offer_cards[unit], clearing_mwh=mwh, clearing_price=price)
                self.assertEqual(actual['range']['label'], builder._normal_output_range(unit,mwh,price))
                self.assertEqual(actual['evaluation'], list(builder._evaluate_offer_match(unit,mwh,price)))
                self.assertEqual(actual['direction'], builder._offer_match_direction(unit,mwh,price))
        for mode in ('dayahead_vs_ml','realtime_vs_dayahead'):
            for cost in (None,0,400):
                for dev in (-1,0,1):
                    item=dict(cost=cost,da_price=300,price_diff=100,dev=dev)
                    self.assertEqual(native('classify',item=item,compare_mode=mode), report.classify_point(item,mode))

    def test_summary_parse_formats_and_public_workbook_parity(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); station=root/'归湖'; station.mkdir()
            book=Workbook(); ws=book.active; ws.title='归湖#01'
            ws.append(['时刻','电量','出清电价'])
            for hour in range(24):
                ws.append([f'{hour}:00', '1,200.000', 0.125])
                ws.cell(hour+2,3).number_format='0.00%'
            source=station/'日前交易结果查询.xlsx'; book.save(source)
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected=summary.parse_workbook(source)
                summary.run_summary(station,root/'python.xlsx')
            actual=native('summary_parse', filename=source.name, sheets=[dict(name=ws.title, rows=[[dict(value=c.value,number_format=c.number_format) for c in row] for row in ws.iter_rows()])])
            self.assertEqual(actual, [dict(unit_name=u,energy=[dict(value=c.value,number_format=c.number_format) for c in e],price=[dict(value=c.value,number_format=c.number_format) for c in p]) for u,e,p in expected])
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}), mock.patch.object(summary,'read_numeric_cell',side_effect=AssertionError('Python numeric parser ran')):
                summary.run_summary(station,root/'rust.xlsx')
            books=[load_workbook(root/f'{kind}.xlsx') for kind in ('python','rust')]
            self.assertEqual(books[0].sheetnames, ['潮安'])
            self.assertEqual(books[0].sheetnames, books[1].sheetnames)
            for a,b in zip(books[0].worksheets,books[1].worksheets):
                self.assertEqual([(c.coordinate,c.value,c.number_format,c._style) for row in a for c in row],[(c.coordinate,c.value,c.number_format,c._style) for row in b for c in row])
            for book in books: book.close()


class TradingAggregationParityTests(unittest.TestCase):
    def test_company_totals_and_representative_order(self):
        builder=reference(fixture())
        for mode in ('dayahead_vs_ml','realtime_vs_dayahead'):
            builder.compare_mode=mode
            for company,units in builder.companies.items():
                with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                    expected=builder._company_summary(company,units)
                    points=builder._representative_points(units,3)
                self.assertEqual(native('company_summary',company=company,units=units,summary=builder.summary,compare_mode=mode),expected)
                self.assertEqual(native('representative',units=units,hourly=builder.hourly,limit=3,compare_mode=mode),points)

    def test_order_unknown_stations_preserves_stable_ties(self):
        units=[dict(station_key=s,unit_name=u) for s,u in [('未知乙','2号机'),('海门','10号机'),('海门','2号机'),('未知甲','2号机'),('汕头','自定义')]]
        self.assertEqual(native('unit_order',units=units,station_order=summary.STATION_ORDER),[4,2,1,0,3])

    def test_rankings_exclude_state_changes_across_markets(self):
        sys.path.insert(0,str(SCRIPT.parent))
        import find_output_range_deviation_days as deviations
        from dataclasses import asdict
        rows=[]
        for day,values in [('2026-10-01',[1,2]),('2026-10-02',[0,2]),('2026-10-03',[3,4])]:
            for market in ('日前','实时'):
                for hour,value in enumerate(values):
                    rows.append(deviations.HourDeviation(market,day,'海门#1机组',f'{hour}:00',value,300,value,'0-10MW',value*1.1,'fixture.xlsx'))
        with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
            expected,excluded=deviations.rank_plant_days_with_exclusions(rows,1)
        result=native('rank',rows=[asdict(r) for r in rows],top=1)
        self.assertEqual(result['scores'],[asdict(r) for r in expected])
        self.assertEqual(result['excluded'],[list(r) for r in excluded])


class TradingPublicPathTests(unittest.TestCase):
    def test_public_report_rust_bulk_matches_render_and_skips_python_parsers(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); source=root/'2026-10-27出清情况.xlsx'
            fixture().save(source)
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected=report.generate_report(source,root/'reference.html').read_text(encoding='utf-8')
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                with mock.patch.object(report.ReportBuilder,'_parse_summary',side_effect=AssertionError('Python parser ran')), mock.patch.object(report.ReportBuilder,'_parse_blocks',side_effect=AssertionError('Python hourly parser ran')), mock.patch.object(report.ReportBuilder,'_fill_dongguan_summary',side_effect=AssertionError('Python aggregation ran')):
                    actual=report.generate_report(source,root/'native.html').read_text(encoding='utf-8')
            self.assertEqual(actual,expected)
            # Auto selection must also use native bulk execution when available.
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'auto'}), mock.patch.object(report.ReportBuilder,'_parse_summary',side_effect=AssertionError('auto fell back')):
                report.generate_report(source,root/'auto.html')

    def test_missing_and_empty_offer_cards_preserve_null_results(self):
        builder=report.ReportBuilder('unused.xlsx')
        for card in (None, {}):
            builder.offer_cards={'unit':card}
            for mwh in (0,10):
                with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                    expected=(builder._evaluate_offer_match('unit',mwh,300),builder._offer_match_direction('unit',mwh,300),builder._normal_output_range('unit',mwh,300))
                with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                    actual=(builder._evaluate_offer_match('unit',mwh,300),builder._offer_match_direction('unit',mwh,300),builder._normal_output_range('unit',mwh,300))
                self.assertEqual(actual,expected)

    def test_version_selected_float_sum_cancellation(self):
        points=[dict(da=x,da_price=1,rt=0,rt_price=0,ml=0,ml_price=0,profit_ml=0) for x in (1e16,1.,-1e16)]
        compensated=native('aggregate',points=points,_compensated_sum=True)
        legacy=native('aggregate',points=points,_compensated_sum=False)
        self.assertEqual(compensated['da_mwh'],1.)
        self.assertEqual(legacy['da_mwh'],0.)
        for epsilon in (-1e-10,0,1e-10,1e-9-1e-12,1e-9+1e-12):
            segments=[dict(start=0,end=10,price=100),dict(start=10+epsilon,end=20,price=100+epsilon)]
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected=report.merge_offer_segments(segments)
            self.assertEqual(native('offer_merge',segments=segments),expected)

    def test_bulk_deviations_and_ranking_workbook_readback(self):
        from dataclasses import asdict
        sys.path.insert(0,str(SCRIPT.parent))
        import find_output_range_deviation_days as deviation
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'2026-10-27出清情况.xlsx';fixture().save(source)
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'python'}):
                expected=deviation.workbook_hour_deviations(source,[])
                scores,excluded=deviation.rank_plant_days_with_exclusions(expected,10)
                deviation.write_results(root/'python.xlsx',scores,excluded,expected)
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':'rust'}):
                actual=deviation.workbook_hour_deviations(source,[])
                scores,excluded=deviation.rank_plant_days_with_exclusions(actual,10)
                deviation.write_results(root/'rust.xlsx',scores,excluded,actual)
            self.assertEqual([asdict(r) for r in actual],[asdict(r) for r in expected])
            books=[load_workbook(root/f'{kind}.xlsx') for kind in ('python','rust')]
            self.assertEqual(books[0].sheetnames,['日前Top天','实时Top天','逐小时明细','剔除日期','说明'])
            self.assertEqual(books[0].sheetnames,books[1].sheetnames)
            for a,b in zip(books[0].worksheets,books[1].worksheets):
                self.assertEqual([(c.value,c.number_format,c._style) for row in a for c in row],[(c.value,c.number_format,c._style) for row in b for c in row])
            for book in books:book.close()

class TradingReviewRegressionTests(unittest.TestCase):
    def test_typed_excel_times_keep_consumer_specific_eligibility(self):
        import datetime
        book=Workbook(); book.active.title='汕头'
        ws=book.active; ws['A1']='1号机'; ws['K1']='偏差'; ws['A31']='end'
        for hour in range(24):
            ws.cell(hour+4,1,datetime.time(hour)); ws.cell(hour+4,5,10)
        for name in report.RENEWABLE_UNITS:
            renewable=book.create_sheet(name)
            for hour in range(24):
                renewable.cell(hour+4,1,datetime.time(hour)); renewable.cell(hour+4,2,10); renewable.cell(hour+4,4,11)
        for backend in ('python','rust'):
            builder=report.ReportBuilder('unused.xlsx'); builder.wb=book
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':backend}):
                self.assertEqual(builder._parse_blocks('汕头','汕头'),{})
                builder._parse_renewables()
                self.assertEqual(builder.hourly,{unit:[] for unit in report.RENEWABLE_UNITS})
                self.assertFalse(builder._has_realtime_data())
                self.assertTrue(all(value['da_mwh']==0 for value in builder.summary.values()))
        # Horizontal exports alone convert a typed time to H:00.
        horizontal=Workbook(); ws=horizontal.active; ws.title='汕头'
        ws.cell(1,2,'#1机组'); ws.cell(2,4,'实时电量')
        for hour in range(24):
            ws.cell(hour+3,1,datetime.time(hour,30)); ws.cell(hour+3,2,hour+1)
        results=[]
        for backend in ('python','rust'):
            builder=report.ReportBuilder('unused.xlsx'); builder.wb=horizontal
            with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':backend}):
                results.append(builder._parse_blocks('汕头','汕头'))
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[1]['汕头#1机组'][0]['time'],'0:00')

    def test_summary_unit_marker_search_skips_descriptive_hashes(self):
        rows=[[dict(value=v,number_format='General') for v in row] for row in [['时刻','电量','出清电价']]+[[f'{hour}:00',1,2] for hour in range(24)]]
        for title in ('归湖#备用#02','归湖##备用#02#03','归湖#备用'):
            result=native('summary_parse',filename='fixture.xlsx',sheets=[dict(name=title,rows=rows)])
            self.assertEqual(result[0]['unit_name'],summary.extract_unit_name(title))

    def test_summary_typed_times_keep_original_normalization(self):
        import datetime
        with TemporaryDirectory() as directory:
            source=Path(directory)/'typed-times.xlsx'
            book=Workbook();ws=book.active;ws.title='归湖#备用#02'
            ws.append(['时刻','电量','出清电价'])
            for hour in range(24):ws.append([datetime.time(hour),1,2])
            book.save(source);book.close()
            results=[]
            for backend in ('python','rust'):
                with mock.patch.dict(os.environ, {'TOOLBOX_DOMAIN_BACKEND':backend}):
                    results.append(summary.parse_workbook(source))
            self.assertEqual(results[0],results[1])
            self.assertEqual(results[1][0][0],'2号机')
            self.assertEqual(len(results[1][0][1]),24)

if __name__ == '__main__': unittest.main()
