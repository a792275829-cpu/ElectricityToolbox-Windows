import os
import unittest
from unittest.mock import patch
from toolbox_native_domain import invoke

class MarketPayloadNativeTests(unittest.TestCase):
    def native(self, action, **payload):
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust'):
            return invoke('market_payload', action, payload)

    def test_period_metric_null_zero(self):
        self.assertEqual(self.native('period_metric', period={'max': 0, 'loadVal': [100]}, field='max'), {'value': 0.0})
        self.assertEqual(self.native('period_metric', period={'loadVal': [0, None, '10', True]}, field='avg'), {'value': 5.0})

    def test_latest_period(self):
        rows = [{'date': '2026-10-27', 'versionDate': 'a', 'max': 1}, {'date': '2026-10-27', 'versionDate': 'b', 'max': 0}]
        self.assertEqual(self.native('period_for_date', periods=rows, date='2026-10-27')['value'], rows[1])

    def test_bad_action(self):
        with self.assertRaises(ValueError):
            self.native('invalid')


class MarketPayloadParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / '南方分公司电力市场日报'))
        from gd_power_report.collector import market
        from gd_power_report import market_analysis
        cls.market = market
        cls.analysis = market_analysis

    def parity(self, fn, *args, **kwargs):
        import copy
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='python'):
            expected = fn(*copy.deepcopy(args), **copy.deepcopy(kwargs))
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust'):
            actual = fn(*copy.deepcopy(args), **copy.deepcopy(kwargs))
        self.assertEqual(actual, expected)
        return actual

    def test_thermal_tie_zero_and_partial_fuel(self):
        m=self.market
        groups=m.DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS
        p={'data':[{'dataItemGroup':groups[0], 'dataType':'1', 'unitCount':[2,0]*12},
                   {'dataItemGroup':groups[1], 'dataType':'1', 'unitCount':[0]*24}]}
        self.parity(m.parse_day_ahead_thermal_unit_count_card,p,{'data':[]})
        p['data'].pop()
        result=self.parity(m._thermal_count_breakdown,p,groups,'1',allow_partial=True)
        self.assertEqual(result['coal'],2)
        self.assertIsNone(result['gas'])
        self.parity(m._thermal_count_breakdown,p,groups,'1')
        p['data'][0]['unitCount'][0]=True
        self.parity(m._thermal_count_breakdown,p,groups,'1',allow_partial=True)

    def test_cards_dates_zero_and_partial_endpoints(self):
        from datetime import date
        m=self.market
        periods=[{'date':'2026-10-27','versionDate':'a','max':99},
                 {'date':'2026-10-27','versionDate':'b','max':0,'min':0},
                 {'date':'2026-10-26','loadVal':[10,20,None,True]}]
        p={'data':{'dataNetLoadDTOList':[{'loadType':kind,'forecastPeriodList':periods,'actualPeriodList':[{'date':'2026-10-27','loadVal':[0,20]}]} for kind in ['1','2','3','4','6']]}}
        board={'data':{'dataApplyConfigDOList':[{'date':'2026-10-27','dataType':'1','id':'','onlineUnit':0,'overHaulCapacity':0},
                    {'date':'2026-10-27','dataType':'1','id':'saved','onlineUnit':0,'overHaulCapacity':0},
                    {'date':'2026-10-26','dataType':'1','onlineUnit':20,'overHaulCapacity':30}]}}
        self.parity(m.parse_day_ahead_market_cards,p,board,date(2026,10,27))
        self.parity(m.parse_realtime_market_comparison,p,date(2026,10,27))
        self.parity(m.parse_market_board_status,board,date(2026,10,27))
        self.parity(m.parse_day_ahead_market_cards,{},board,date(2026,10,27),allow_partial=True)
        self.parity(m.parse_realtime_market_comparison,{},date(2026,10,27),allow_partial=True)
        self.parity(m._period_for_date,periods,date(2026,10,27))
        self.parity(m._period_for_date,periods+[periods[1]],date(2026,10,27))

    def test_unit_status_priority_and_summary(self):
        a=self.analysis
        for da,rt,actual in [(0,0,0),(1,1,1),(1,1,0),(1,1,2),(1,1,.1),(None,1,1)]:
            hours=[{'day_ahead_mwh':da,'realtime_mwh':rt,'actual_mwh':actual} for _ in range(24)]
            parsed={'plants':[{'plant':'中文电厂','units':[{'unit':'#1','hours':hours}]}]}
            self.parity(a.summarize_realtime_units,parsed)
        hours=[{'day_ahead_mwh':1,'realtime_mwh':1,'actual_mwh':1} for _ in range(24)]
        hours[10]['actual_mwh']=hours[11]['actual_mwh']=0
        self.parity(a.classify_unit_status,hours,2.4,2.2)

    def test_merge_history_and_latest_ties(self):
        from datetime import date
        m=self.market
        a={'data':{'dataNetLoadDTOList':[{'loadType':'4','forecastPeriodList':[{'date':'2026-10-26'}]}]}}
        b={'data':{'dataNetLoadDTOList':[{'loadType':'4','forecastPeriodList':[{'date':'2026-10-25'}]}, {'loadType':'1'}]}}
        import copy
        expected=copy.deepcopy(a); actual=copy.deepcopy(a)
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='python'): m._merge_load_payload(expected,b)
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust'): m._merge_load_payload(actual,b)
        self.assertEqual(actual,expected)
        self.parity(m._missing_west_history_dates,actual,date(2026,10,27))


    def test_consumer_specific_load_date_and_scaling(self):
        from datetime import date
        import importlib.util, sys
        from pathlib import Path
        root=Path(__file__).resolve().parents[2]
        def load(name,path):
            spec=importlib.util.spec_from_file_location(name,root/path)
            module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
            return module
        market=load('native_parity_market_table','市场表更新/update_market_table.py')
        transaction=load('native_parity_transaction','交易系统信息更新/update_transaction_info.py')
        benefit=load('native_parity_benefit','功率调整收益统计表更新/update_power_benefit.py')
        target=date(2026,10,27)
        p={'data':{'dataNetLoadDTOList':[{'loadType':'1','forecastPeriodList':[
            {'date':'2026-10-26','max':10,'min':0,'avg':5,'sum':.12345678901},
            {'date':'2026-10-27','max':20,'min':0,'avg':10,'sum':.23456789012}]}]}}
        a=self.parity(market.summarize_load_type,p,'1',target,'forecastPeriodList','日前')
        b=self.parity(transaction.summarize_load_type,p,'1',target,period_list_key='forecastPeriodList',label='日前')
        self.assertEqual(a[0],10);self.assertEqual(b[0],20);self.assertEqual(b[3],23456.78901)
        rows=[{'date':'2026年10月27日','info':{'中文字段':0,'比例':'12.34%','备用':3}}]
        self.parity(market.extract_report_market_values,rows,target,fields=(('缺失','中文字段'),'比例'),expected_count=2,label='报表',percent_fields={'比例'})
        rows=[{'date':'2026/10/27','dataType':1,'dataItemGroup':g,'unitCount':list(range(24))} for g in market.DAY_AHEAD_CLEARING_UNIT_COUNT_GROUPS]
        self.assertEqual(self.parity(market.extract_spot_clearing_unit_count_values,rows,target),[23,23])
        rows[1]['unitCount'][19]=100
        self.assertEqual(self.parity(market.extract_spot_clearing_unit_count_values,rows,target),[23,100])
        forecast={'data':[{'unitId':'u','forecastPower24':[0]*24,'stPredictPowerList24':[None]*24,'rawShortTermPower24':[2]*24}]}
        self.parity(benefit.parse_forecast_payload,forecast,unit_id='u')
        self.parity(benefit.parse_forecast_payload,forecast,unit_id='u',fallback=True)
        trade={'data':{'unitDateList':[{'unitId':'u','date':'2026-10-27','dataType':1,'electricity':[1]*24,'acPrice':[0]*24}]}}
        self.parity(benefit.parse_trade_payload,trade,unit_id='u',expected_dates={target},data_type=1)
        self.parity(benefit.points24,[1.123456789012]*24,label='原始功率',scale=10)
        trade['data']['unitDateList'][0]['date']=' 2026-10-27 00:00:00'
        self.parity(benefit.parse_trade_payload,trade,unit_id='u',expected_dates={target},data_type=1)
        for action,payload in [('forecast_payload',{'payload':{'data':'bad'},'unit_id':'u'}),('trade_payload',{'payload':{'data':{'unitDateList':'bad'}},'unit_id':'u','data_type':1,'expected_dates':[]}),('points24',{'raw':[1]*23})]:
            with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust'):
                with self.assertRaises(ValueError): invoke('market_payload',action,payload)


    def test_leaf_math_and_business_payload(self):
        from datetime import date
        from types import SimpleNamespace
        m=self.market
        target=date(2026,10,27)
        items=[{'loadType':'4','forecastPeriodList':[{'date':'2026-10-27','max':0},{'date':'2026-10-26','max':10}], 'actualPeriodList':[{'date':'2026-10-27','max':20}]}]
        self.parity(m._day_ahead_power_maximum_card,items,'西电东送电力','4',target)
        self.parity(m._realtime_power_maximum_card,items,'西电东送电力','4',target)
        self.parity(m._maintenance_comparison_card,{'overHaulCapacity':0},None,target,realtime=True)
        self.parity(m._market_board_row,{'data':{'dataApplyConfigDOList':[{'date':target.isoformat(),'dataType':'1','onlineUnit':0}]}},target)
        self.parity(m._market_board_row,{'data':{'dataApplyConfigDOList':[{'date':target.isoformat(),'dataType':'1','id':'','createTime':False,'updateTime':0,'onlineUnit':0}]}},target)
        self.parity(self.analysis._daily_total,[{'x':0}]*24,'x')
        self.parity(self.analysis._daily_total,[{'x':'bad'}]*23,'x')
        self.parity(self.analysis._daily_total,[{'x':'bad'}]*23+[{'x':None}],'x')
        self.parity(self.analysis.classify_unit_status,[{'day_ahead_mwh':None,'actual_mwh':'bad','realtime_mwh':1}]*24,1,1)
        groups=m.DAY_AHEAD_THERMAL_UNIT_COUNT_GROUPS
        p={'data':[{'dataItemGroup':g,'dataType':'1','unitCount':[i]*24} for i,g in enumerate(groups)]}
        self.parity(m._thermal_unit_counts,p,groups,'1')
        for payload in [{'retCode':'T200','data':[]},{'code':200,'data':None},{'retCode':'','code':'','data':0}, {'retCode':False,'data':None}, {'retCode':0,'data':[]}]:
            response=SimpleNamespace(ok=True,json=lambda:payload)
            self.parity(m._json,response,'接口')
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND='rust'):
            for payload in [{'retCode':'T500','retMsg':'失败'},{'code':None}]:
                with self.assertRaises(ValueError): m._json(SimpleNamespace(ok=True,json=lambda:payload),'接口')
            with self.assertRaises(ValueError): self.analysis._daily_total([{'x':'bad'}]*24,'x')
            with self.assertRaises(ValueError): invoke('market_payload','thermal_breakdown',{'payload':{},'groups':[],'data_type':'1'})


    def test_trade_type_missing_null_and_mismatch_contract(self):
        import importlib.util
        import sys
        from datetime import date
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "market_payload_trade_type_review", root / "功率调整收益统计表更新/update_power_benefit.py")
        benefit = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = benefit
        spec.loader.exec_module(benefit)
        target = date(2026, 10, 27)
        base = {"unitId": "u", "date": target.isoformat(),
                "electricity": [1] * 24, "acPrice": [2] * 24}
        for name, extra, valid in [
            ("missing", {}, True), ("null", {"dataType": None}, False),
            ("matching integer", {"dataType": 1}, True),
            ("matching string", {"dataType": "1"}, True),
            ("mismatch integer", {"dataType": 2}, False),
            ("mismatch string", {"dataType": "2"}, False),
        ]:
            payload = {"data": {"unitDateList": [{**base, **extra}]}}
            params = {"payload": payload, "unit_id": "u",
                      "expected_dates": [target.isoformat()], "data_type": 1}
            with self.subTest(case=name):
                if valid:
                    expected = {target: {"K": (10,) * 24, "L": (2,) * 24}}
                    for mode in ("python", "rust"):
                        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=mode):
                            self.assertEqual(benefit.parse_trade_payload(
                                payload, unit_id="u", expected_dates={target}, data_type=1), expected)
                    with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
                        self.assertEqual(invoke("market_payload", "trade_payload", params),
                                         {target.isoformat(): {"K": [10] * 24, "L": [2] * 24}})
                else:
                    for mode in ("python", "rust"):
                        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND=mode):
                            with self.subTest(boundary="public adapter", mode=mode):
                                with self.assertRaisesRegex(ValueError, "日前/实时类型不匹配"):
                                    benefit.parse_trade_payload(
                                        payload, unit_id="u", expected_dates={target}, data_type=1)
                    with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
                        with self.subTest(boundary="domain action"):
                            with self.assertRaisesRegex(ValueError, "日前/实时类型不匹配"):
                                invoke("market_payload", "trade_payload", params)


    def test_market_load_unavailable_preserves_public_runtime_error(self):
        import importlib.util
        import sys
        from datetime import date
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "market_payload_missing_actual_review", root / "市场表更新/update_market_table.py")
        market_table = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = market_table
        spec.loader.exec_module(market_table)
        target = date(2026, 7, 5)
        missing = {"max": None, "min": None, "avg": None, "sum": None}
        for name, periods in [("null metrics", [missing]),
                              ("empty metric", [{**missing, "max": 1, "min": ""}]),
                              ("no periods", [])]:
            dataset = {"data": {"dataNetLoadDTOList": [
                {"loadType": "1", "actualPeriodList": periods}]}}
            for mode in ("python", "rust"):
                with self.subTest(case=name, mode=mode), patch.dict(
                        os.environ, TOOLBOX_DOMAIN_BACKEND=mode):
                    with self.assertRaisesRegex(RuntimeError,
                            "2026-07-05 loadType=1 没有返回实际运行数据"):
                        market_table.extract_actual_load_values(dataset, target)
        # A published zero is data, and malformed nonempty text stays a numeric error.
        zero = {"data": {"dataNetLoadDTOList": [
            {"loadType": "1", "actualPeriodList": [dict.fromkeys(missing, 0)]}]}}
        self.parity(market_table.summarize_actual_load_type, zero, "1", target)
        zero["data"]["dataNetLoadDTOList"][0]["actualPeriodList"][0]["max"] = "bad"
        with patch.dict(os.environ, TOOLBOX_DOMAIN_BACKEND="rust"):
            with self.assertRaises(ValueError):
                market_table.summarize_actual_load_type(zero, "1", target)

if __name__ == '__main__': unittest.main()

