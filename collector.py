from __future__ import annotations

import csv
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import FED_ENDPOINTS, FRED_SERIES, NYFED_ENDPOINTS, SOFR_ROOTS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,text/html,*/*",
}

FAST_TIMEOUT = (3, 6)
OFFICIAL_TIMEOUT = (5, 25)
MAX_WORKERS = 12
CURVE_MONTHS_AHEAD = 18

MONTH_TO_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(message, flush=True)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(HEADERS)
    return session


def request(url: str, official: bool = False) -> requests.Response:
    timeout = OFFICIAL_TIMEOUT if official else FAST_TIMEOUT
    with make_session() as session:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response


def yahoo_chart(symbol: str, range_: str = "5d") -> dict[str, Any]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range={range_}&interval=1d"
    )
    payload = request(url, official=False).json()
    result = payload.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"No Yahoo result for {symbol}")

    result = result[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []

    observations = []
    for timestamp, value in zip(timestamps, closes):
        if value is None:
            continue
        observations.append({
            "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
            "value": float(value),
        })

    price = meta.get("regularMarketPrice")
    if price is None and observations:
        price = observations[-1]["value"]
    if price is None:
        raise ValueError(f"No usable price for {symbol}")

    return {
        "symbol": symbol,
        "price": float(price),
        "exchange": meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "observations": observations,
        "source_url": url,
    }


def fred_csv(series_id: str) -> dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = request(url, official=True)
    rows = []
    reader = csv.DictReader(io.StringIO(response.text))

    for row in reader:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        try:
            rows.append({"date": row["DATE"], "value": float(raw)})
        except (ValueError, KeyError):
            continue

    if not rows:
        raise ValueError(f"FRED returned no observations for {series_id}")

    return {
        "series_id": series_id,
        "latest": rows[-1],
        "observations": rows[-900:],
        "source_url": url,
    }


def json_endpoint(url: str) -> dict[str, Any]:
    return {"payload": request(url, official=True).json(), "source_url": url}


def text_endpoint(url: str) -> dict[str, Any]:
    response = request(url, official=True)
    return {
        "content_type": response.headers.get("content-type"),
        "text": response.text[:500000],
        "source_url": url,
    }


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = year * 12 + (month - 1) + offset
    return zero_based // 12, zero_based % 12 + 1


def contract_candidates(root: str, suffixes: tuple[str, ...]) -> list[str]:
    now = datetime.now(timezone.utc)
    symbols = []
    for offset in range(CURVE_MONTHS_AHEAD + 1):
        year, month = add_months(now.year, now.month, offset)
        for suffix in suffixes:
            symbols.append(f"{root}{MONTH_TO_CODE[month]}{str(year)[-2:]}{suffix}")
    return symbols


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


def collect_curve_parallel(symbols, group, statuses):
    log(f"[{group}] scan started: {len(symbols)} candidates")
    usable = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(safe_collect, f"{group}:{s}", yahoo_chart, s, "5d"): s
            for s in symbols
        }
        for index, future in enumerate(as_completed(futures), 1):
            value, status = future.result()
            statuses.append(status)
            if value:
                usable.append(value)
            if index % 10 == 0 or index == len(symbols):
                log(f"[{group}] {index}/{len(symbols)}, usable={len(usable)}")
    usable.sort(key=lambda x: x["symbol"])
    return usable


def main() -> None:
    started = time.perf_counter()
    out = Path("public/data")
    out.mkdir(parents=True, exist_ok=True)
    statuses = []
    raw = {
        "generated_at_utc": utc_now(),
        "collector_version": "2.7.0-input-repair",
        "futures": {},
        "fred": {},
        "nyfed": {},
        "fed": {},
    }

    log("[1/6] ZQ continuous")
    value, status = safe_collect("yahoo:ZQ=F", yahoo_chart, "ZQ=F", "1mo")
    raw["futures"]["zq_continuous"] = value
    statuses.append(status)

    log("[2/6] ZQ curve")
    raw["futures"]["zq_curve"] = collect_curve_parallel(
        contract_candidates("ZQ", (".CBT", "")), "zq", statuses
    )

    log("[3/6] SOFR curve")
    symbols = []
    for root in SOFR_ROOTS:
        symbols.extend(contract_candidates(root, (".CME", "")))
    raw["futures"]["sofr_curve"] = collect_curve_parallel(symbols, "sofr", statuses)

    # FRED는 서버 부하·제한을 피하려고 동시 요청 수를 3개로 제한
    log(f"[4/6] FRED {len(FRED_SERIES)} series")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(safe_collect, f"fred:{sid}", fred_csv, sid): (sid, key)
            for sid, key in FRED_SERIES.items()
        }
        for future in as_completed(futures):
            sid, key = futures[future]
            value, status = future.result()
            raw["fred"][key] = value
            statuses.append(status)
            log(f"[FRED] {sid} ok={status['ok']}")

    log("[5/6] NY Fed")
    for key, url in NYFED_ENDPOINTS.items():
        value, status = safe_collect(f"nyfed:{key}", json_endpoint, url)
        raw["nyfed"][key] = value
        statuses.append(status)
        log(f"[NYFED] {key} ok={status['ok']}")

    log("[6/6] Federal Reserve")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(safe_collect, f"fed:{key}", text_endpoint, url): key
            for key, url in FED_ENDPOINTS.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            value, status = future.result()
            raw["fed"][key] = value
            statuses.append(status)
            log(f"[FED] {key} ok={status['ok']}")

    Path("public/data/raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path("public/data/source_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": utc_now(),
                "collector_version": "2.7.0-input-repair",
                "sources": statuses,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok = sum(item["ok"] for item in statuses)
    log(f"COMPLETE {ok}/{len(statuses)} in {time.perf_counter()-started:.1f}s")


if __name__ == "__main__":
    main()
