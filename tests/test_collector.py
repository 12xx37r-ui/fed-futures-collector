import unittest

from collector import parse_fred_series_csv, redact_secrets, sanitize_url


class CollectorTests(unittest.TestCase):
    def test_fred_observation_date_header(self):
        text = "observation_date,DGS10\n2026-07-01,4.48\n2026-07-02,.\n"
        result = parse_fred_series_csv(text, "DGS10", "test")
        self.assertEqual(result["latest"]["date"], "2026-07-01")
        self.assertEqual(result["latest"]["value"], 4.48)

    def test_fred_legacy_date_header(self):
        text = "DATE,UNRATE\n2026-06-01,4.1\n"
        result = parse_fred_series_csv(text, "UNRATE", "test")
        self.assertEqual(result["latest"]["value"], 4.1)

    def test_fred_api_key_is_redacted_from_url(self):
        url = "https://api.stlouisfed.org/fred/series/observations?series_id=DFF&api_key=secret123&file_type=json"
        cleaned = sanitize_url(url)
        self.assertNotIn("secret123", cleaned)
        self.assertIn("api_key=%5BREDACTED%5D", cleaned)
        self.assertIn("series_id=DFF", cleaned)

    def test_recursive_redaction_covers_payload_and_errors(self):
        payload = {
            "source_url": "https://example.test/data?token=abc&series=x",
            "nested": {"api_key": "abc", "error": "failed https://x.test/?access_token=abc"},
        }
        cleaned = redact_secrets(payload)
        self.assertNotIn("abc", str(cleaned))
        self.assertEqual(cleaned["nested"]["api_key"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
