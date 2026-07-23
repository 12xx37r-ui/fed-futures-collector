import json
import sys
import time
from datetime import datetime, timezone

import requests

TESTS = [
    {
        "name": "CME settlements endpoint",
        "url": "https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/305/FUT",
        "params": {"tradeDate": ""},
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.cmegroup.com/markets/interest-rates/stirs/30-day-federal-fund.settlements.html",
        },
    },
    {
        "name": "Yahoo chart endpoint",
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/ZQ=F",
        "params": {"range": "5d", "interval": "1d"},
        "headers": {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    },
]

def classify(status, content_type, body):
    s = (body or "").lower()
    ct = (content_type or "").lower()
    if status in (401, 403) or any(x in s for x in ["access denied", "captcha", "cloudflare", "akamai", "bot detection"]):
        return "BLOCKED_OR_WAF"
    if status == 404:
        return "ENDPOINT_NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if 200 <= status < 300 and "json" in ct:
        return "JSON_OK"
    if 200 <= status < 300 and ("html" in ct or s.lstrip().startswith("<!doctype") or s.lstrip().startswith("<html")):
        return "HTML_INSTEAD_OF_JSON"
    if 200 <= status < 300:
        return "HTTP_OK_OTHER"
    return "HTTP_ERROR"

def run_test(item):
    started = time.time()
    try:
        r = requests.get(
            item["url"],
            params=item.get("params"),
            headers=item.get("headers"),
            timeout=25,
            allow_redirects=True,
        )
        body = r.text
        ct = r.headers.get("content-type", "")
        result = {
            "name": item["name"],
            "requested_url": r.request.url,
            "final_url": r.url,
            "status": r.status_code,
            "content_type": ct,
            "response_bytes": len(r.content),
            "elapsed_seconds": round(time.time() - started, 3),
            "classification": classify(r.status_code, ct, body),
            "body_preview": body[:800].replace("\n", " "),
        }
    except Exception as e:
        result = {
            "name": item["name"],
            "requested_url": item["url"],
            "status": None,
            "content_type": None,
            "response_bytes": 0,
            "elapsed_seconds": round(time.time() - started, 3),
            "classification": "NETWORK_OR_TIMEOUT_ERROR",
            "error": repr(e),
        }
    return result

def main():
    output = {
        "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "results": [run_test(x) for x in TESTS],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    with open("diagnostic-result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
