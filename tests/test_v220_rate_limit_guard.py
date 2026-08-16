import unittest
import collector

class TestV220RateLimitGuard(unittest.TestCase):
    def test_bounded_workers(self):
        self.assertLessEqual(collector.MAX_WORKERS, 4)
    def test_provider_pacing(self):
        self.assertIn("query1.finance.yahoo.com", collector._PROVIDER_MIN_INTERVAL)
        self.assertIn("api.stlouisfed.org", collector._PROVIDER_MIN_INTERVAL)

if __name__ == "__main__": unittest.main()
