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


if __name__ == "__main__":
    unittest.main()
