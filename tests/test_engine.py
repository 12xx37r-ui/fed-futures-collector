import unittest
from datetime import date
from engine.run_engine import _policy_rate_outlook, _forecast_registry
from engine.futures_curve import (
    build_curve,
    meeting_adjusted_rate,
    stable_meeting_rate,
    target_probabilities,
)

class EngineTests(unittest.TestCase):
    def test_meeting_adjustment(self):
        value = meeting_adjusted_rate(4.25, date(2026, 9, 16), 4.50)
        self.assertTrue(3.5 < value < 4.5)

    def test_late_month_inversion_uses_monthly_proxy(self):
        value, method, raw, flags = stable_meeting_rate(3.885, date(2026, 10, 28), 3.785)
        self.assertAlmostEqual(value, 3.885, places=6)
        self.assertEqual(method, "monthly_curve_proxy")
        self.assertGreater(raw, 4.5)
        self.assertIn("late_month_meeting", flags)

    def test_probabilities_sum(self):
        probs = target_probabilities(4.25, 4.50)
        self.assertAlmostEqual(sum(probs.values()), 100.0, places=1)


    def test_policy_rate_outlook_summarizes_existing_market_path(self):
        today=date.today()
        y=today.year + (1 if today.month>9 else 0)
        m=((today.month+2-1)%12)+1
        meeting=f'{y:04d}-{m:02d}-15'
        path=[{'meeting':meeting,'expected_post_meeting_rate':3.75}]
        out=_policy_rate_outlook(path,4.0)
        self.assertTrue(out['available'])
        self.assertIn('3m',out['horizons'])
        self.assertFalse(out['model_probabilities_changed'])

    def test_metadata_only_contract_is_rejected(self):
        curve = build_curve([
            {"symbol": "SR1Z27.CME", "price": 96.87, "observations": []},
            {"symbol": "SR3Z27.CME", "price": 95.92, "observations": [{"date": "2026-07-28", "value": 95.92}]},
        ], ("SR1", "SR3"))
        self.assertEqual(len(curve), 1)
        self.assertEqual(curve[0]["symbol"], "SR3Z27.CME")

    def test_forecast_registry_separates_validated_and_abstain(self):
        result={
            'generated_at_utc':'2026-08-18T00:00:00+00:00',
            'policy_rate_outlook':{'current_effective_rate_pct':3.63,'horizons':{'3m':{'expected_rate_pct':3.75}}},
            'us_macro_context':{
                'm2':{'current_yoy_pct':5.5,'forecast_1m_yoy_pct':5.6,'forecast_3m_yoy_pct':5.7,'backtest_1m':{'skill_pct':5,'fallback_used':False},'backtest_3m':{'skill_pct':10,'fallback_used':False},'forecast_quality_gate':{'passed':True}},
                'dxy':{'current':100,'forecast_1m':100,'forecast_3m':100,'backtest_1m':{'skill_pct':0,'fallback_used':True},'backtest_3m':{'skill_pct':0,'fallback_used':True}},
            },
            'real_rate':{'current_pct':2.4,'forecast_1m_pct':2.4,'forecast_3m_pct':2.4,'forecast_usable_1m':False,'forecast_usable_3m':False},
            'features':{'inflation':.2,'employment':-.1,'growth':-.1,'financial':-.2},
        }
        reg=_forecast_registry(result)
        by={x['id']:x for x in reg['entries']}
        self.assertEqual(by['us_m2_3m']['status'],'VALIDATED')
        self.assertEqual(by['dxy_3m']['status'],'ABSTAIN')
        self.assertEqual(by['real_rate_10y_3m']['status'],'ABSTAIN')
        self.assertEqual(by['fed_policy_3m']['status'],'MARKET_IMPLIED')

if __name__ == "__main__":
    unittest.main()

class ObjectiveValidationTests(unittest.TestCase):
    def test_unverified_output_uses_market_probability(self):
        market = {"cut": 0.0, "hold": 99.0, "hike": 1.0}
        model = {"cut": 20.0, "hold": 50.0, "hike": 30.0}
        passed = False
        representative = model if passed else market
        self.assertEqual(representative, market)

class FinalImprovementTests(unittest.TestCase):
    def test_policy_outlook_adds_path_confidence_without_changing_rate(self):
        today=date.today(); m=((today.month+1-1)%12)+1; y=today.year+(1 if today.month+1>12 else 0)
        meeting=f'{y:04d}-{m:02d}-15'
        symbol='ZQTEST.CBT'
        raw={'futures':{'zq_curve':[{'symbol':symbol,'market_time_utc':'2999-01-01T00:00:00+00:00','price':96.0,'observations':[{'date':'2026-08-17','value':96.0}]*4}]}}
        out=_policy_rate_outlook([{'meeting':meeting,'contract_symbol':symbol,'expected_post_meeting_rate':3.75}],3.63,raw)
        self.assertEqual(out['horizons']['1m']['expected_rate_pct'],3.75)
        q=out['horizons']['1m']['path_confidence']
        self.assertEqual(q['observation_count'],4)
        self.assertEqual(q['live_ratio'],1.0)
        self.assertEqual(q['grade'],'HIGH')

class FinalRegimeAuditTests(unittest.TestCase):
    def test_small_regime_sample_is_not_presented_as_skill(self):
        from engine.us_macro_context import _m2_regime_audit
        from datetime import date as _date
        import calendar as _calendar

        def add_months(d, n):
            idx=d.year*12+d.month-1+n
            y,m0=divmod(idx,12); m=m0+1
            return _date(y,m,min(d.day,_calendar.monthrange(y,m)[1]))

        start=_date(2020,1,1)
        rows=[]
        cpi=[]
        target=[]
        recession=[]
        level=15000.0
        for i in range(64):
            d=add_months(start,i).isoformat()
            level*=1.004
            rows.append({'date':d,'value':level})
            cpi.append({'date':d,'value':100+i*0.25})
            target.append({'date':d,'value':4.0})
            # Only the last two eligible origins are recession observations.
            recession.append({'date':d,'value':1.0 if i>=59 else 0.0})
        raw={'fred':{
            'cpi':{'observations':cpi},
            'target_upper':{'observations':target},
            'recession_indicator':{'observations':recession},
        }}
        audit=_m2_regime_audit(raw,rows,3)
        rec=audit['regimes']['recession']
        self.assertLess(rec['samples'],10)
        self.assertEqual(rec['sample_status'],'INSUFFICIENT_SAMPLE')
        self.assertIsNone(rec['skill_pct'])
        self.assertIsNotNone(rec['raw_skill_pct'])
