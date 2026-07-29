import unittest
from datetime import date
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

    def test_metadata_only_contract_is_rejected(self):
        curve = build_curve([
            {"symbol": "SR1Z27.CME", "price": 96.87, "observations": []},
            {"symbol": "SR3Z27.CME", "price": 95.92, "observations": [{"date": "2026-07-28", "value": 95.92}]},
        ], ("SR1", "SR3"))
        self.assertEqual(len(curve), 1)
        self.assertEqual(curve[0]["symbol"], "SR3Z27.CME")

if __name__ == "__main__":
    unittest.main()
