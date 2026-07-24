from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from config import FED_ENDPOINTS, FRED_SERIES, NYFED_ENDPOINTS, YAHOO_SYMBOLS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,*/*",
}
TIMEOUT = 25


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def get(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def fetch_yahoo(symbol: str) -> dict[str, Any]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range=1mo&interval=1d"
    )
    response = get(url)
    payload = response.json()
    result = payload["chart"]["result"][0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp", [])
    quote_data = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quote_data.get("close", [])

    observations = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        observations.append(
            {
                "date_utc": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                "value": float(close),
            }
        )

    return {
        "symbol": symbol,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "regular_market_price": meta.get("regularMarketPrice"),
        "observations": observations,
        "source_url": url,
    }


def fetch_fred(series_id: str) -> dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = get(url)
    reader = csv.DictReader(io.StringIO(response.text))
    rows = []
    for row in reader:
        raw = row.get(series_id)
        if not raw or raw == ".":
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        rows.append({"date": row["DATE"], "value": value})

    return {
        "series_id": series_id,
        "latest": rows[-1] if rows else None,
        "observations": rows[-400:],
        "source_url": url,
    }


def fetch_json(url: str) -> dict[str, Any]:
    response = get(url)
    return {
        "payload": response.json(),
        "source_url": url,
    }


def fetch_text(url: str) -> dict[str, Any]:
    response = get(url)
    return {
        "content_type": response.headers.get("content-type"),
        "text": response.text[:250000],
        "source_url": url,
    }


def safe_collect(name: str, fn, *args) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    try:
        value = fn(*args)
        return value, {
            "name": name,
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error": None,
        }
    except Exception as exc:
        return None, {
            "name": name,
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    output_dir = Path("public/data")
    output_dir.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "generated_at_utc": now_utc(),
        "market": {},
        "fred": {},
        "nyfed": {},
        "fed": {},
    }
    source_status: list[dict[str, Any]] = []

    for key, symbol in YAHOO_SYMBOLS.items():
        value, status = safe_collect(f"yahoo:{symbol}", fetch_yahoo, symbol)
        data["market"][key] = value
        source_status.append(status)

    for series_id, key in FRED_SERIES.items():
        value, status = safe_collect(f"fred:{series_id}", fetch_fred, series_id)
        data["fred"][key] = value
        source_status.append(status)

    for key, url in NYFED_ENDPOINTS.items():
        value, status = safe_collect(f"nyfed:{key}", fetch_json, url)
        data["nyfed"][key] = value
        source_status.append(status)

    for key, url in FED_ENDPOINTS.items():
        value, status = safe_collect(f"fed:{key}", fetch_text, url)
        data["fed"][key] = value
        source_status.append(status)

    Path("public/data/raw.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path("public/data/source_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": now_utc(),
                "sources": source_status,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok_count = sum(1 for item in source_status if item["ok"])
    print(f"Collected {ok_count}/{len(source_status)} sources successfully.")


if __name__ == "__main__":
    main()
