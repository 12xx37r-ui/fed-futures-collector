import unittest
from datetime import date

from engine.policy_regime import actual_direction, policy_inertia_asof, target_midpoint


def _series(rows):
    return {"observations": [{"date": d, "value": v} for d, v in rows]}


class PolicyRegimeTests(unittest.TestCase):
    def test_target_midpoint_is_preferred(self):
        raw = {"fred": {
            "target_upper": _series([("2026-06-01", 4.00)]),
            "target_lower": _series([("2026-06-01", 3.75)]),
            "effr_fred": _series([("2026-06-01", 3.83)]),
        }}
        self.assertAlmostEqual(target_midpoint(raw, date(2026, 6, 2)), 3.875)

    def test_policy_inertia_cut_is_positive(self):
        raw = {"fred": {
            "target_upper": _series([("2025-12-20", 4.50), ("2026-02-01", 4.25)]),
            "target_lower": _series([("2025-12-20", 4.25), ("2026-02-01", 4.00)]),
        }}
        self.assertEqual(policy_inertia_asof(raw, date(2026, 2, 20), lookback_days=55), 1.0)

    def test_actual_direction_uses_target_range(self):
        raw = {"fred": {
            "target_upper": _series([("2026-03-17", 4.25), ("2026-03-18", 4.00)]),
            "target_lower": _series([("2026-03-17", 4.00), ("2026-03-18", 3.75)]),
        }}
        direction, bps = actual_direction(raw, date(2026, 3, 18))
        self.assertEqual(direction, "cut")
        self.assertAlmostEqual(bps, -25.0)


if __name__ == "__main__":
    unittest.main()
