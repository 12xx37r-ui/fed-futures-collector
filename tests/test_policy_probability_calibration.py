import unittest
from engine.ensemble import policy_probabilities

class PolicyProbabilityCalibrationTest(unittest.TestCase):
    def test_hold_prior_dominates_neutral_score(self):
        p = policy_probabilities(0.0, {"cut":0.15,"hold":0.70,"hike":0.15})
        self.assertGreater(p["hold"], 65.0)
        self.assertAlmostEqual(sum(p.values()), 100.0, places=1)

    def test_strong_scores_can_overcome_hold_prior(self):
        prior={"cut":0.15,"hold":0.70,"hike":0.15}
        cut = policy_probabilities(1.0, prior)
        hike = policy_probabilities(-1.0, prior)
        self.assertGreater(cut["cut"], cut["hold"])
        self.assertGreater(hike["hike"], hike["hold"])

if __name__ == '__main__':
    unittest.main()
