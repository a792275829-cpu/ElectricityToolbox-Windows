import os, sys, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'广东电价预测'))
from gd_price_forecast import features as f, evaluation as e, data as d

class ForecastNativeParity(unittest.TestCase):
    def frame(self, days=40):
        dates=pd.date_range('2024-02-10',periods=days)
        rows=[]
        for j,day in enumerate(dates):
            for slot in range(96):
                rows.append({'日期':day,'时刻':f'{slot//4:02}:{slot%4*15:02}',d.TARGET:float(j*4+slot) if (j+slot)%37 else np.nan,
                    '日前统调负荷(MW)':1000.+slot,'日前风电出力(MW)':20.+j,'日前光伏出力(MW)':float(slot),
                    '日前B类竞价空间(MW)':200.,'温度':25.+j,'实际电力':999.})
        return pd.DataFrame(rows)
    def test_full_frame_all_modes_missing_date_leap_nan_supply_holidays_weather(self):
        frame=self.frame();frame=frame[frame['日期']!=pd.Timestamp('2024-02-15')].sample(frac=1,random_state=3)
        for mode in ('legacy','none','lagged','published'):
            with self.subTest(mode=mode),patch.dict(os.environ,{'TOOLBOX_DOMAIN_BACKEND':'rust','TOOLBOX_FEATURE_BACKEND':'auto'}):
                actual=f.build_features(frame,exogenous_mode=mode,holidays=True,supply=True,weather=True)
                with patch.dict(os.environ,{"TOOLBOX_FEATURE_BACKEND":"python"}):
                    expected=f._build_features_python(frame,exogenous_mode=mode,holidays=True,supply=True,weather=True)
                for a,b in zip(actual,expected):
                    if isinstance(a,pd.Series):pd.testing.assert_series_equal(a,b)
                    else:pd.testing.assert_frame_equal(a,b,rtol=1e-10,atol=1e-10)
    def test_binary_feature_protocol_and_validation(self):
        import tempfile
        from toolbox_native_domain import invoke
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"rust"}):
            source=Path(tmp)/"input.f64"; output=Path(tmp)/"output.f64"
            np.array([12.],dtype="<f8").tofile(source)
            payload={"dates":["2024-02-29"],"slots":["00:00"],"target":{"offset":0,"length":1},"exogenous_mode":"none", "binary":{"input_path":str(source),"output_path":str(output),"dtype":"<f8","rows":1,"bytes":8}}
            result=invoke("forecast","features",payload)
            self.assertEqual(result["dtype"],"<f8")
            self.assertEqual(result["rows"],1)
            self.assertEqual(output.stat().st_size,len(result["columns"])*8)
            values=np.fromfile(output,dtype="<f8").reshape(len(result["columns"]),1)
            self.assertEqual(values[0,0],0.)
            plain=invoke("forecast","features",{"dates":payload["dates"],"slots":payload["slots"],"target":[12.],"exogenous_mode":"none"})
            self.assertEqual(result["columns"],[c["name"] for c in plain])
            expected=np.array([[np.nan if v is None else v for v in c["values"]] for c in plain])
            np.testing.assert_allclose(values,expected,rtol=1e-10,atol=1e-10)

            for changed in ({"dtype":">f8"},{"bytes":16},{"rows":2}):
                bad={**payload,"binary":{**payload["binary"],**changed}}
                with self.assertRaisesRegex(ValueError,"format|length|bounds|shape"):invoke("forecast","features",bad)
            bad={**payload,"target":{"offset":8,"length":1}}
            with self.assertRaisesRegex(ValueError,"format|length|bounds|shape"):invoke("forecast","features",bad)

    def test_binary_adapter_rejects_bad_output_and_cleans_files(self):
        paths=[]
        def malformed(domain,action,payload):
            paths.extend([Path(payload["binary"]["input_path"]),Path(payload["binary"]["output_path"])])
            paths[-1].write_bytes(b"bad")
            return {"columns":["slot"],"rows":len(payload["dates"]),"dtype":"<f8","order":"column-major","bytes":len(payload["dates"])*8}
        with patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"rust","TOOLBOX_FEATURE_BACKEND":"auto"}),patch.object(d,"_invoke_forecast",side_effect=malformed):
            with self.assertRaisesRegex(ValueError,"格式或长度"):
                f.build_features(self.frame(1))
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_infinite_metrics_match_python(self):
        identity=pd.DataFrame({"日期":pd.to_datetime(["2024-01-01"]),"时刻":["00:00"]})
        with patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"rust"}):
            for truth in (np.inf,-np.inf):
                actual=e.evaluate_predictions([truth],[0.],identity)
                expected=e._evaluate_predictions_python([truth],[0.],identity)
                self.assertEqual(actual[0],expected[0])
                pd.testing.assert_frame_equal(actual[1],expected[1])
                pd.testing.assert_frame_equal(actual[2],expected[2])

    def test_zero_holdout_matches_public_python_slicing(self):
        identity=pd.DataFrame({"日期":pd.date_range("2024-01-01",periods=4)})
        with patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"rust"}):
            a,ah=e.make_date_splits(identity,holdout_days=0,validation_days=1,n_splits=1)
        b,bh=e._make_date_splits_python(identity,holdout_days=0,validation_days=1,n_splits=1)
        np.testing.assert_array_equal(ah,bh)
        for aa,bb in zip(a,b):
            for i,j in zip(aa,bb):np.testing.assert_array_equal(i,j)

    def test_python_modes_skip_native_preparation(self):
        sentinel=object()
        for domain,feature in (("python","auto"),("rust","python")):
            with self.subTest(domain=domain,feature=feature), patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":domain,"TOOLBOX_FEATURE_BACKEND":feature}):
                with patch.object(f,"_build_features_python",return_value=sentinel) as oracle, patch.object(pd,"to_numeric",side_effect=AssertionError("native payload prepared")), patch("toolbox_native_domain.invoke",side_effect=AssertionError("native invoked")):
                    self.assertIs(f.build_features(object()),sentinel)
                    oracle.assert_called_once()

    def test_normalization_and_duplicate_feature_keys(self):
        frame=self.frame(2).iloc[::-1].copy()
        frame["日期"] = frame["日期"] + pd.Timedelta(hours=5)
        frame["时刻"] = frame["时刻"] + ":00"
        with patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"python"}):
            expected=d._normalize(frame)
        with patch.dict(os.environ,{"TOOLBOX_DOMAIN_BACKEND":"rust"}):
            actual=d._normalize(frame)
            pd.testing.assert_frame_equal(actual,expected)
            with self.assertRaises(ValueError):
                f.build_features(pd.concat([expected,expected.iloc[:1]]))

    def test_splits_metrics(self):
        frame=self.frame();ident=frame[['日期','时刻']];y=frame[d.TARGET]
        with patch.dict(os.environ,{'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            pd.testing.assert_series_equal(e.previous_day_baseline(y),y.shift(96))
            pd.testing.assert_series_equal(e.seven_day_baseline(y),y.shift(672))
            aa,ah=e.make_date_splits(ident);bb,bh=e._make_date_splits_python(ident)
            np.testing.assert_array_equal(ah,bh)
            for a,b in zip(aa,bb):
                for i,j in zip(a,b):np.testing.assert_array_equal(i,j)
            a=e.evaluate_predictions(y,y+2,ident);b=e._evaluate_predictions_python(y,y+2,ident)
            self.assertEqual(a[0],b[0]);pd.testing.assert_frame_equal(a[1],b[1]);pd.testing.assert_frame_equal(a[2],b[2])
    def test_grain_partial_duplicate(self):
        frame=self.frame(3);frame[d.TARGET]=1.
        with patch.dict(os.environ,{'TOOLBOX_DOMAIN_BACKEND':'rust'}):
            d.validate_quarter_hour_grain(frame)
            frame.loc[frame['日期']==frame['日期'].max(),d.TARGET]=np.nan
            a=d.split_labeled_prediction_dates(frame);b=d._split_labeled_prediction_dates_python(frame)
            for i,j in zip(a,b):pd.testing.assert_frame_equal(i,j)
            for bad in (pd.concat([frame,frame.iloc[:1]]),frame.iloc[1:],frame.drop(index=range(96,192))):
                with self.assertRaises(Exception):d.validate_quarter_hour_grain(bad)
            frame.loc[0,d.TARGET]=np.nan
            with self.assertRaises(Exception):d.split_labeled_prediction_dates(frame)
    def test_default_native_is_called(self):
        from toolbox_native_domain import invoke
        with patch.dict(os.environ,{'TOOLBOX_DOMAIN_BACKEND':'auto','TOOLBOX_FEATURE_BACKEND':'auto'}):
            with patch('toolbox_native_domain.invoke',wraps=invoke) as called, patch.object(f, '_build_features_python', side_effect=AssertionError('native default fell back')):
                f.build_features(self.frame(3))
                self.assertEqual(called.call_args.args[:2],('forecast','features'))
                payload=called.call_args.args[2]
                self.assertEqual(set(payload["target"]),{"offset","length"})
                self.assertTrue(all(isinstance(c["values"],dict) for c in payload["raw"]))
                self.assertFalse(Path(payload["binary"]["input_path"]).exists())
                self.assertFalse(Path(payload["binary"]["output_path"]).exists())
if __name__=='__main__':unittest.main()
