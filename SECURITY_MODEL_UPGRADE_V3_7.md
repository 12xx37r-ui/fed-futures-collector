# V3.7 security and objective-validation upgrade

- `FRED_API_KEY` remains a runtime environment secret and is still used for authenticated requests.
- URLs, redirects, exceptions, caches, `raw.json`, and `source_status.json` are recursively redacted before persistence.
- A sanitized point-in-time raw snapshot is archived daily under `public/data/vintages/`.
- FRED observations preserve `realtime_start`, `realtime_end`, and retrieval time for future live-vintage validation.
- FOMC certification now requires positive skill over the majority-class benchmark, Brier skill, a 95% accuracy lower bound above 50%, at least 60 meetings, and real-time vintages.
- Reconstructed history remains research evidence and cannot receive an institutional label.

Never print or persist `os.environ["FRED_API_KEY"]`. GitHub Actions should continue to inject it from repository Secrets.
