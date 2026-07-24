import unittest
from datetime import date
from engine.futures_curve import meeting_adjusted_rate, target_probabilities

class EngineTests(unittest.TestCase):
    def test_meeting_adjustment(self):
        value = meeting_adjusted_rate(4.25, date(2026, 9, 16), 4.50)
        self.assertTrue(3.5 < value < 4.5)

    def test_probabilities_sum(self):
        probs = target_probabilities(4.25, 4.50)
        self.assertAlmostEqual(sum(probs.values()), 100.0, places=1)

if __name__ == "__main__":
    unittest.main()
