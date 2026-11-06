import json, os, subprocess, unittest, sys, tempfile
from unittest.mock import patch

from pathlib import Path
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import analyze_500kv_section_gaps as gaps
from src import deduplicate_500kv_prices as dedupe
from src import classify_500kv_nodes as classification
from openpyxl import Workbook, load_workbook
from toolbox_native_excel import native_binary
BINARY = native_binary() or Path(__file__).resolve().parents[1] / 'bin' / ('toolbox-price-xlsx.exe' if os.name == 'nt' else 'toolbox-price-xlsx')
def call(action, **payload):
    p=subprocess.run([str(BINARY), 'domain-sections'],input=json.dumps(dict(protocol=1,path='',operation='sections',payload=dict(action=action,**payload))),text=True,encoding="utf-8",capture_output=True)
    if p.returncode: raise RuntimeError(p.stderr)
    return json.loads(p.stdout)['result']
@unittest.skipUnless(BINARY.is_file(), "native domain binary is not built")
class SectionsParity(unittest.TestCase):
    def test_available_default_auto_executes_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            topology=Path(tmp)/'topology.md'
            topology.write_text('## 2. 接线\n### 片区\n甲-乙\n## 3. 结束',encoding='utf-8')
            with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND='auto'), patch.object(gaps,'_parse_topology_edges_reference',side_effect=AssertionError('Python fallback executed')):
                edges,duplicates=gaps.parse_topology_edges(topology)
            self.assertEqual(edges,[gaps.TopologyEdge('片区','甲','乙')])
            self.assertEqual(duplicates,[])

    def test_normalization(self):
        names=['其他甲开关站500kV#1','乙核电厂。','其他丙换流站(500kV)2母','丁220 KV#2','戊500_k v12A','己#3B','云霄换流站福建']
        actual=call('normalize',names=names)
        self.assertEqual(actual['price'],[gaps.normalize_price_node_name(n) for n in names])
        self.assertEqual(actual['node'],[dedupe.normalize_node_name(n) for n in names])
        self.assertEqual(actual['key'],[classification.node_key(n) for n in names])
    def test_topology(self):
        r=call('topology',text='## 1. 名单\n孤点\n## 2. 接线\n### 区\n甲：乙、丙\n乙-甲\n## 3. 其他')
        self.assertEqual(r['edges'],[dict(group='区',a='甲',b='乙'),dict(group='区',a='甲',b='丙')])
        self.assertEqual(r['duplicates'],[['乙','甲','区']])

    def test_decimal_mean_and_deduplication(self):
        rows=[['0.10000000000000000000000000001', None, ''], ['0.2', 0, None], ['0.3', '0', None]]
        self.assertEqual(call('mean',rows=rows),list(dedupe._average_price_rows([tuple(r) for r in rows])))
        payload=dict(sheets=[dict(name='价格',headers=['00:00','00:15'],rows=[
            ['甲500kV#1','价格',1,'2.00'], ['乙500kV#1','价格','1.0',2],
            ['甲220kV','价格',7,8], ['丙500_k v#2','价格',0,None]])],nodes=None)
        result=call('dedupe',**payload)
        self.assertEqual((result['kept'],result['removed']),(2,2))
        self.assertEqual(result['sheets'][0]['rows'][0],['甲500kV','价格',1,'2.00'])
        payload['nodes']=['甲','乙']
        payload['sheets'][0]['rows'].append(['甲站500kV#2','价格','3.0',None])
        result=call('dedupe',**payload)
        self.assertEqual(result['sheets'][0]['rows'][0],['甲','价格',11/3,5.0])

    def test_topology_alias_longest_exact_priority_and_cross_sheet_means(self):
        payload=dict(nodes=['甲','甲站','乙'],sheets=[
            dict(name='第一页',headers=['00:00'],rows=[['甲500kV#1','价格','1.10'],['包含乙的节点','价格',None]]),
            dict(name='第二页',headers=['00:00'],rows=[['其他甲变500kV#2','价格','1.30'],['甲500kV#1','价格','1.10']])])
        result=call('dedupe',**payload)
        self.assertEqual((result['kept'],result['removed']),(2,2))
        self.assertEqual(result['sheets'],[dict(name='第一页',headers=['00:00'],rows=[['甲站','价格',float(Decimal('3.5')/3)],['乙','价格',None]])])
        payload['sheets'][1]['headers']=['00:15']
        with self.assertRaises(RuntimeError): call('dedupe',**payload)

    def test_analysis_previous_threshold_missing_and_accumulation(self):
        times=['00:00','00:15','00:30','00:45','01:00','01:15','01:30','01:45','02:00']
        rows=[]
        for name,prices in [('甲',[100,110,110,120,120,130,130,140,None]),('乙',[100,90,90,80,80,70,70,60,0]),('丙',[100,90,90,80,80,70,70,70,0])]:
            rows.append(dict(raw_name=name+'500kV',normalized_name=name,prices=dict(zip(times,prices))))
        edges=[gaps.TopologyEdge('片区', '甲',n) for n in ['乙','丙','缺失']]
        for threshold in [0,20,80]:
            reference=gaps._calculate_reference(edges,times,rows,threshold)
            actual=call('analyze',edges=[dict(group=e.group,a=e.a,b=e.b) for e in edges],time_headers=times,price_rows=rows,threshold=threshold)
            self.assertEqual(actual,reference)
        self.assertEqual(call('match',nodes=['甲','缺失'],rows=rows),gaps.match_price_rows(['甲','缺失'],rows))

    def test_summary_order_ties_and_windows(self):
        records=[]
        for edge,group in [('乙-丙','二区'),('甲-乙','一区')]:
            for i,direction in enumerate(['乙->甲','甲->乙','乙->甲','甲->乙']):
                records.append(dict(group=group,edge=edge,time=['00:15','00:30','01:00','01:15'][i],abs_diff=100.0,low='低',high='高',direction=direction))
        times=['00:00','00:15','00:30','00:45','01:00','01:15']
        actual=call('summaries',records=records,time_headers=times)
        self.assertEqual(actual['edge_summary'],gaps.summarize_edges(records,times))
        self.assertEqual(actual['group_summary'],gaps.summarize_groups(records))

    def test_decimal_precision_large_small_and_cancellation(self):
        cases=[[['9999999999999999999999999999'],['-9999999999999999999999999999'],['0.1']],
               [['1e-50'],['2e-50'],['3e-50']],
               [['123456789012345678901234567890'],['0.12345678901234567890123456789']],
               [[None],[''],['0']]]
        for rows in cases:
            self.assertEqual(call('mean',rows=rows),list(dedupe._average_price_rows([tuple(r) for r in rows])))
        with self.assertRaises(RuntimeError): call('mean',rows=[['invalid'],[1]])

    def test_synthetic_workbooks_python_rust_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'prices.xlsx'; topology=root/'topology.md'
            topology.write_text('## 1. 节点\n甲、乙\n## 2. 接线\n### 片区\n甲-乙\n乙-甲\n## 3. 结束',encoding='utf-8')
            book=Workbook();sheet=book.active;sheet.title='价格'
            sheet.append(['节点名称','数据项','00:00','00:15','00:30','00:45','01:00','01:15'])
            sheet.append(['其他甲站500kV#1','电价',100,110,120,130,140,150])
            sheet.append(['其他甲站500kV#2','电价',100,110,120,130,140,150])
            sheet.append(['其他乙站500kV','电价',100,90,80,70,60,50]);book.save(source)
            outputs=[]
            for mode in ['python','rust']:
                with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND=mode):
                    reduced=root/(mode+'.xlsx');dedupe.process_workbook(source,reduced,topology)
                    result=gaps.analyze_section_gaps(topology,reduced,root/mode,threshold=0)
                    snapshot=[]
                    for path in [reduced,result.output_path]:
                        wb=load_workbook(path,data_only=True)
                        snapshot.append([(ws.title,list(ws.values), [[(cell.data_type,cell.number_format) for cell in row] for row in ws],ws.freeze_panes) for ws in wb]);wb.close()
                    outputs.append(snapshot)
            self.assertEqual(outputs[0],outputs[1])

    def test_classification_full_workbook_parity(self):
        self._assert_classification_full_workbook_parity()

    def test_classification_missing_quantity_parity(self):
        self._assert_classification_full_workbook_parity(missing_quantity=True)

    def _assert_classification_full_workbook_parity(self, missing_quantity=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'cities.xlsx';topology=root/'topology.md'
            topology.write_text('## 1. 名单\n孤点\n## 2. 接线\n### 非行政区域\n甲站-乙变\n## 3. 结束',encoding='utf-8')
            wb=Workbook();ws=wb.active;ws.title='各市节点名单';ws.append(['地市','去重节点数量','节点名称（顿号分隔）'])
            for _,cities in classification.REGIONS:
                for city in cities: ws.append([city+'市',None if missing_quantity and city=='广州' else 1 if city in ['广州','惠州'] else 0,'甲站' if city=='广州' else '乙站' if city=='惠州' else ''])
            ws=wb.create_sheet('补充节点待核验');ws.append(['识别状态','匹配地市','补充记录统一节点名','原始节点名称（逐条）']);ws.append(['地市待核验','江门市','孤点','孤点500kV#1'])
            wb.save(source)
            with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND='python'): reference=classification.classify(source,topology,confirmed_cities={})
            with patch.dict(os.environ,TOOLBOX_DOMAIN_BACKEND='rust'): actual=classification.classify(source,topology,confirmed_cities={})
            self.assertEqual(actual,reference)
if __name__=='__main__': unittest.main()

