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

from config import (
    FED_ENDPOINTS,
    FRED_SERIES,
    NYFED_ENDPOINTS,
    SOFR_ROOTS,
    ZQ_MONTH_CODES,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,text/html,*/*",
}

# 없는 Yahoo 월물 심볼이 오래 붙잡지 못하도록 짧게 제한
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 6
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
MAX_WORKERS = 12

# 현재 월부터 향후 18개월까지만 탐색
CURVE_MONTHS_AHEAD = 18


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(message, flush=True)


def request(url: str) -> requests.Response:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def yahoo_chart(symbol: str, range_: str = "5d") -> dict[str, Any]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range={range_}&interval=1d"
    )
    payload = request(url).json()
    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        raise ValueError(f"No Yahoo result for {symbol}: {error}")

    result = result[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    closes = (
        result.get("indicators", {})
        .get("quote", [{}])[0]
        .get("close") or []
    )

    observations = []
    for timestamp, value in zip(timestamps, closes):
        if value is None:
            continue
        observations.append(
            {
                "date": datetime.fromtimestamp(
                    timestamp, timezone.utc
                ).date().isoformat(),
                "value": float(value),
            }
        )

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
    rows = []
    reader = csv.DictReader(io.StringIO(request(url).text))

    for row in reader:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        try:
            rows.append({"date": row["DATE"], "value": float(raw)})
        except (ValueError, KeyError):
            continue

    return {
        "series_id": series_id,
        "latest": rows[-1] if rows else None,
        "observations": rows[-900:],
        "source_url": url,
    }


def json_endpoint(url: str) -> dict[str, Any]:
    return {"payload": request(url).json(), "source_url": url}


def text_endpoint(url: str) -> dict[str, Any]:
    response = request(url)
    return {
        "content_type": response.headers.get("content-type"),
        "text": response.text[:500000],
        "source_url": url,
    }


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = year * 12 + (month - 1) + offset
    return zero_based // 12, zero_based % 12 + 1


MONTH_TO_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def contract_candidates(
    root: str,
    exchange_suffixes: tuple[str, ...],
) -> list[str]:
    now = datetime.now(timezone.utc)
    symbols = []

    for offset in range(CURVE_MONTHS_AHEAD + 1):
        year, month = add_months(now.year, now.month, offset)
        yy = str(year)[-2:]
        code = MONTH_TO_CODE[month]

        for suffix in exchange_suffixes:
            symbols.append(f"{root}{code}{yy}{suffix}")

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


def collect_curve_parallel(
    symbols: list[str],
    group: str,
    statuses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    log(
        f"[{group}] parallel scan started: "
        f"{len(symbols)} candidates, {MAX_WORKERS} workers"
    )

    usable = []
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(
                safe_collect,
                f"{group}:{symbol}",
                yahoo_chart,
                symbol,
                "5d",
            ): symbol
            for symbol in symbols
        }

        for future in as_completed(future_map):
            symbol = future_map[future]
            completed += 1

            try:
                value, status = future.result()
            except Exception as exc:
                value = None
                status = {
                    "name": f"{group}:{symbol}",
                    "ok": False,
                    "elapsed_ms": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }

            statuses.append(status)

            if value and value.get("price") is not None:
                usable.append(value)
                log(
                    f"[{group}] OK {symbol} "
                    f"price={value['price']} "
                    f"({completed}/{len(symbols)})"
                )
            elif completed % 10 == 0 or completed == len(symbols):
                log(
                    f"[{group}] progress "
                    f"{completed}/{len(symbols)}, usable={len(usable)}"
                )

    usable.sort(key=lambda item: item["symbol"])
    log(f"[{group}] scan finished: usable={len(usable)}")
    return usable


def main() -> None:
    started_all = time.perf_counter()
    output_dir = Path("public/data")
    output_dir.mkdir(parents=True, exist_ok=True)

    statuses: list[dict[str, Any]] = []
    raw: dict[str, Any] = {
        "generated_at_utc": utc_now(),
        "collector_version": "2.6.0-fast",
        "futures": {},
        "fred": {},
        "nyfed": {},
        "fed": {},
    }

    log("[1/6] Collecting ZQ continuous contract")
    value, status = safe_collect(
        "yahoo:ZQ=F",
        yahoo_chart,
        "ZQ=F",
        "1mo",
    )
    statuses.append(status)
    raw["futures"]["zq_continuous"] = value
    log(f"[1/6] ZQ continuous ok={status['ok']}")

    log("[2/6] Scanning ZQ monthly curve")
    zq_symbols = contract_candidates("ZQ", (".CBT", ""))
    raw["futures"]["zq_curve"] = collect_curve_parallel(
        zq_symbols,
        "zq",
        statuses,
    )

    log("[3/6] Scanning SOFR futures curve")
    sofr_symbols = []
    for root in SOFR_ROOTS:
        sofr_symbols.extend(
            contract_candidates(root, (".CME", ""))
        )
    raw["futures"]["sofr_curve"] = collect_curve_parallel(
        sofr_symbols,
        "sofr",
        statuses,
    )

    log(f"[4/6] Collecting {len(FRED_SERIES)} FRED series")
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(
                safe_collect,
                f"fred:{series_id}",
                fred_csv,
                series_id,
            ): (series_id, key)
            for series_id, key in FRED_SERIES.items()
        }

        for future in as_completed(future_map):
            series_id, key = future_map[future]
            value, status = future.result()
            statuses.append(status)
            raw["fred"][key] = value
            log(f"[FRED] {series_id} ok={status['ok']}")

    log("[5/6] Collecting NY Fed official rates")
    for key, url in NYFED_ENDPOINTS.items():
        value, status = safe_collect(
            f"nyfed:{key}",
            json_endpoint,
            url,
        )
        statuses.append(status)
        raw["nyfed"][key] = value
        log(f"[NYFED] {key} ok={status['ok']}")

    log("[6/6] Collecting Federal Reserve pages and feeds")
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(
                safe_collect,
                f"fed:{key}",
                text_endpoint,
                url,
            ): key
            for key, url in FED_ENDPOINTS.items()
        }

        for future in as_completed(future_map):
            key = future_map[future]
            value, status = future.result()
            statuses.append(status)
            raw["fed"][key] = value
            log(f"[FED] {key} ok={status['ok']}")

    Path("public/data/raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path("public/data/source_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": utc_now(),
                "collector_version": "2.6.0-fast",
                "sources": statuses,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok_count = sum(1 for item in statuses if item["ok"])
    elapsed = round(time.perf_counter() - started_all, 2)

    log(
        f"COLLECTION COMPLETE: {ok_count}/{len(statuses)} "
        f"sources, elapsed={elapsed}s"
    )


if __name__ == "__main__":
    main()
