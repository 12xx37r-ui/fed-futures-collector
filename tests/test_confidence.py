import unittest

from engine.confidence import calculate


class ConfidenceTests(unittest.TestCase):
    def test_data_quality_and_model_validation_are_separate(self):
        status = {"sources": [
            {"name": "yahoo:ZQ=F", "ok": True},
            {"name": "fred:DFF", "ok": True},
            {"name": "fred:DGS2", "ok": True},
            {"name": "nyfed:effr", "ok": True},
            {"name": "nyfed:sofr", "ok": True},
            {"name": "fred:CPIAUCSL", "ok": True, "stale": False},
        ]}
        features = {
            "zq_curve": [1], "sofr_curve": [1], "fomc_dates": ["2026-09-16"],
            "fed_text_score": 0.0, "dotplot_available": True,
        }
        backtest = {
            "model_validation_score": 73,
            "quality_gate": {"passed": False, "candidate": True, "level": "검증 후보·표본확대 중"},
        }
        out = calculate(status, features, backtest)
        self.assertEqual(out["score"], 69)
        self.assertEqual(out["model_validation_score"], 73)
        self.assertEqual(out["grade"], "CANDIDATE")
        self.assertGreaterEqual(out["data_quality_score"], 80)
        self.assertNotEqual(out["data_quality_score"], out["model_validation_score"])

    def test_no_artificial_49_cap(self):
        out = calculate({"sources": []}, {}, {
            "model_validation_score": 88,
            "quality_gate": {"passed": False, "candidate": False, "level": "검증 미통과"},
        })
        self.assertEqual(out["score"], 15)
        self.assertEqual(out["model_validation_score"], 88)
        self.assertEqual(out["grade"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
