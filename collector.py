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
from bs4 import BeautifulSoup

from config import (
    FED_ENDPOINTS, FRED_SERIES, FUTURES_YEARS_AHEAD,
    NYFED_ENDPOINTS, SOFR_ROOTS, ZQ_MONTH_CODES,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,text/html,*/*",
}
TIMEOUT = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r


def yahoo_chart(symbol: str, range_: str = "1mo") -> dict[str, Any]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range={range_}&interval=1d"
    )
    payload = request(url).json()
    result = payload.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"No Yahoo chart result for {symbol}")
    result = result[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    closes = (
        result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    )
    obs = []
    for ts, value in zip(timestamps, closes):
        if value is None:
            continue
        obs.append({
            "date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            "value": float(value),
        })
    return {
        "symbol": symbol,
        "price": meta.get("regularMarketPrice"),
        "exchange": meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "observations": obs,
        "source_url": url,
    }


def fred_csv(series_id: str) -> dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    rows = []
    reader = csv.DictReader(io.StringIO(request(url).text))
    for row in reader:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        try:
            rows.append({"date": row["DATE"], "value": float(raw)})
        except (ValueError, KeyError):
            pass
    return {
        "series_id": series_id,
        "latest": rows[-1] if rows else None,
        "observations": rows[-900:],
        "source_url": url,
    }


def json_endpoint(url: str) -> dict[str, Any]:
    return {"payload": request(url).json(), "source_url": url}


def rss_or_html(url: str) -> dict[str, Any]:
    r = request(url)
    return {
        "content_type": r.headers.get("content-type"),
        "text": r.text[:500000],
        "source_url": url,
    }


def contract_candidates(root: str, exchange_suffixes: tuple[str, ...]) -> list[str]:
    now = datetime.now(timezone.utc)
    symbols = []
    for year in range(now.year, now.year + FUTURES_YEARS_AHEAD + 1):
        yy = str(year)[-2:]
        for month_code in ZQ_MONTH_CODES:
            for suffix in exchange_suffixes:
                symbols.append(f"{root}{month_code}{yy}{suffix}")
    return symbols


def safe(name: str, fn, *args) -> tuple[Any, dict[str, Any]]:
    start = time.perf_counter()
    try:
        value = fn(*args)
        return value, {
            "name": name, "ok": True,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "error": None,
        }
    except Exception as exc:
        return None, {
            "name": name, "ok": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect_curve(symbols: list[str], group: str, statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    curve = []
    for symbol in symbols:
        value, status = safe(f"{group}:{symbol}", yahoo_chart, symbol, "5d")
        statuses.append(status)
        if value and value.get("price") is not None:
            curve.append(value)
    return curve


def main() -> None:
    out = Path("public/data")
    out.mkdir(parents=True, exist_ok=True)

    statuses: list[dict[str, Any]] = []
    raw: dict[str, Any] = {
        "generated_at_utc": utc_now(),
        "futures": {},
        "fred": {},
        "nyfed": {},
        "fed": {},
    }

    continuous, st = safe("yahoo:ZQ=F", yahoo_chart, "ZQ=F", "1mo")
    statuses.append(st)
    raw["futures"]["zq_continuous"] = continuous

    zq_symbols = contract_candidates("ZQ", (".CBT", ""))
    raw["futures"]["zq_curve"] = collect_curve(zq_symbols, "zq", statuses)

    sofr_symbols = []
    for root in SOFR_ROOTS:
        sofr_symbols.extend(contract_candidates(root, (".CME", "")))
    raw["futures"]["sofr_curve"] = collect_curve(sofr_symbols, "sofr", statuses)

    for series_id, key in FRED_SERIES.items():
        value, st = safe(f"fred:{series_id}", fred_csv, series_id)
        statuses.append(st)
        raw["fred"][key] = value

    for key, url in NYFED_ENDPOINTS.items():
        value, st = safe(f"nyfed:{key}", json_endpoint, url)
        statuses.append(st)
        raw["nyfed"][key] = value

    for key, url in FED_ENDPOINTS.items():
        value, st = safe(f"fed:{key}", rss_or_html, url)
        statuses.append(st)
        raw["fed"][key] = value

    Path("public/data/raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path("public/data/source_status.json").write_text(
        json.dumps(
            {"generated_at_utc": utc_now(), "sources": statuses},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    ok = sum(1 for x in statuses if x["ok"])
    print(f"collection complete: {ok}/{len(statuses)} sources")


if __name__ == "__main__":
    main()
