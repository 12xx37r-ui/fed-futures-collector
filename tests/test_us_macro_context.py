import unittest
from datetime import date, timedelta

from engine.us_macro_context import build_us_macro_context, refresh_dxy_context


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

    def test_dxy_refresh_preserves_m2(self):
        existing={'available':True,'m2':{'available':True,'current_yoy_pct':5.0},'dxy':{}}
        dxy={'observations':dxy_rows(),'price':103.0,'source_url':'x','stale':False}
        out=refresh_dxy_context(existing,dxy)
        self.assertEqual(out['m2']['current_yoy_pct'],5.0)
        self.assertTrue(out['dxy']['available'])


if __name__=='__main__': unittest.main()
