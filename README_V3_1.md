# V3.1 FRED root fix

- Fixes FRED CSV date header parsing (`observation_date`, not only `DATE`).
- Removes unreliable comma-separated bulk request.
- Fetches 18 public FRED CSV series in parallel (6 workers).
- Preserves the last successful value as stale cache on later outages.
- Updates GitHub Actions to Node 24 compatible checkout/setup-python versions.
