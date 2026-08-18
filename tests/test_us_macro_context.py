import unittest
from datetime import date, timedelta

from engine.us_macro_context import build_us_macro_context, refresh_dxy_context, _dxy_macro_model
from config import FRED_SERIES


def monthly_rows(n=72, start=20000.0):
    rows=[]
    y,m=2020,1
    level=start
    for i in range(n):
        level *= 1.004 + (0.0002 if i > n-10 else 0)
        rows.append({'date':f'{y:04d}-{m:02d}-01','value':level})
        m += 1
        if m == 13:
            y += 1; m = 1
    return rows


def dxy_rows(n=900):
    start=date(2023,1,1)
    rows=[]
    for i in range(n):
        v=100.0 + 0.004*i + 1.2*((i%37)-18)/37.0
        rows.append({'date':(start+timedelta(days=i)).isoformat(),'value':v})
    return rows


class UsMacroContextTests(unittest.TestCase):
    def test_builds_m2_and_dxy_forward_context(self):
        raw={
            'fred':{'m2':{'observations':monthly_rows(),'source_url':'https://fred.test/M2SL','stale':False}},
            'market':{'dxy':{'observations':dxy_rows(),'price':104.2,'source_url':'https://yahoo.test/dxy','market_time_utc':'2026-08-18T14:00:00+00:00','stale':False}},
            'fed':{},
        }
        out=build_us_macro_context(raw)
        self.assertTrue(out['available'])
        self.assertTrue(out['m2']['available'])
        self.assertTrue(out['dxy']['available'])
        self.assertIn('forecast_3m_yoy_pct',out['m2'])
        self.assertIn('forecast_3m',out['dxy'])
        self.assertIn(out['dxy']['direction_3m'],{'up','down','flat'})
        self.assertIn('macro_model_audit',out['dxy'])
        self.assertIn('selected_model_3m',out['dxy'])
        self.assertIn('yoy_history',out['m2'])
        self.assertIn('forecast_quality_gate',out['m2'])
        self.assertIn('real_rate',out)



    def test_dxy_macro_audit_includes_relative_rate_candidates(self):
        rows=dxy_rows(900)
        dates=[r['date'] for r in rows]
        def series(base, drift=0.0):
            return {'observations':[{'date':d,'value':base+drift*i} for i,d in enumerate(dates)]}
        raw={'fred':{
            'treasury_2y':series(4.0,0.0002), 'treasury_10y':series(4.4,0.00015),
            'effr_fred':series(4.25), 'nfci':series(-0.2,0.00005), 'hy_oas':series(3.3,0.0001),
            'vix':series(18.0,0.001), 'ecb_deposit_rate':series(2.25),
            'japan_overnight_rate':series(0.8,0.00002), 'real_yield_10y':series(1.9,0.00005),
        }}
        out=_dxy_macro_model(raw,rows,21)
        self.assertTrue(out['available'])
        self.assertIn(out['selected_variant'],{'domestic_financial','relative_policy_rates','combined'})
        self.assertIn('relative_policy_rates',out['candidate_audit'])
        self.assertIn('EFFR-ECB deposit spread',out['candidate_audit']['relative_policy_rates']['features'])

    def test_relative_rate_and_real_yield_series_are_collected_in_existing_fred_batch(self):
        self.assertEqual(FRED_SERIES['ECBDFR'], 'ecb_deposit_rate')
        self.assertEqual(FRED_SERIES['IRSTCI01JPM156N'], 'japan_overnight_rate')
        self.assertEqual(FRED_SERIES['DFII10'], 'real_yield_10y')

    def test_dxy_refresh_preserves_m2(self):
        existing={'available':True,'m2':{'available':True,'current_yoy_pct':5.0},'dxy':{}}
        dxy={'observations':dxy_rows(),'price':103.0,'source_url':'x','stale':False}
        out=refresh_dxy_context(existing,dxy)
        self.assertEqual(out['m2']['current_yoy_pct'],5.0)
        self.assertTrue(out['dxy']['available'])


if __name__=='__main__': unittest.main()
