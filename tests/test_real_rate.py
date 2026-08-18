import unittest

from engine.real_rate import build


class RealRateTests(unittest.TestCase):
    def test_real_rate_schema(self):
        obs = [
            {"date": f"2025-01-{(i % 28) + 1:02d}", "value": 1.0 + i * 0.001}
            for i in range(400)
        ]
        raw = {
            "fred": {
                "real_yield_10y": {"observations": obs},
                "real_yield_5y": {"observations": obs},
                "real_yield_20y": {"observations": obs},
            }
        }
        out = build(raw)
        self.assertTrue(out["available"])
        self.assertIn("current_pct", out)
        self.assertIn("forecast_3m_pct", out)
        self.assertIn("baseline_rmse", out["backtest_3m"])
        self.assertIn("quality_gate", out["backtest_3m"])
        self.assertIn("recent_change", out)
        self.assertIn("current_curve", out)
        self.assertIn("direction_3m", out)
        self.assertIn("forecast_usable_3m", out)
        self.assertIn("candidate_forecast_3m_pct", out)
        if not out["forecast_usable_3m"]:
            self.assertEqual(out["forecast_3m_pct"], out["current_pct"])

    def test_real_rate_never_promotes_unvalidated_macro_candidate(self):
        obs = [
            {"date": f"2025-01-{(i % 28) + 1:02d}", "value": 2.0 + (i % 17) * 0.01}
            for i in range(420)
        ]
        raw = {"fred": {
            "real_yield_10y": {"observations": obs},
            "real_yield_5y": {"observations": obs},
            "real_yield_20y": {"observations": obs},
        }}
        out = build(raw)
        self.assertTrue(out["available"])
        if not out["forecast_quality_gate"]["passed"]:
            self.assertEqual(out["selected_model_3m"], "persistence")
            self.assertEqual(out["forecast_change_3m_pctp"], 0.0)


if __name__ == "__main__":
    unittest.main()
