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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,text/html,*/*",
}
FAST_TIMEOUT = (3, 6)
OFFICIAL_TIMEOUT = (4, 15)
FRED_BULK_TIMEOUT = (4, 18)
MAX_WORKERS = 12
CURVE_MONTHS_AHEAD = 18
MONTH_TO_CODE = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(message, flush=True)


def make_session(total_retries: int = 1) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session


def request(url: str, official: bool = False, timeout=None, retries: int = 1) -> requests.Response:
    timeout = timeout or (OFFICIAL_TIMEOUT if official else FAST_TIMEOUT)
    with make_session(retries) as session:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response


def yahoo_chart(symbol: str, range_: str = "5d") -> dict[str, Any]:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + f"?range={range_}&interval=1d"
    payload = request(url).json()
    result = payload.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"No Yahoo result for {symbol}")
    result = result[0]
    meta = result.get("meta", {})
    observations = []
    for timestamp, value in zip(result.get("timestamp") or [], result.get("indicators", {}).get("quote", [{}])[0].get("close") or []):
        if value is not None:
            observations.append({"date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(), "value": float(value)})
    price = meta.get("regularMarketPrice")
    if price is None and observations:
        price = observations[-1]["value"]
    if price is None:
        raise ValueError(f"No usable price for {symbol}")
    return {"symbol": symbol, "price": float(price), "exchange": meta.get("exchangeName"), "currency": meta.get("currency"), "observations": observations, "source_url": url}


def parse_fred_series_csv(text: str, series_id: str, source_url: str) -> dict[str, Any]:
    """Parse FRED graph CSV.

    FRED currently labels the date column ``observation_date``; older code
    assumed ``DATE`` and therefore rejected otherwise valid responses.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    columns = reader.fieldnames or []
    date_col = next(
        (column for column in columns if column.strip().lower() in {"date", "observation_date"}),
        None,
    )
    value_col = next(
        (column for column in columns if column.strip().upper() == series_id.upper()),
        None,
    )
    if not date_col or not value_col:
        preview = text[:180].replace("\n", " ")
        raise ValueError(
            f"unexpected FRED CSV columns={columns}; preview={preview!r}"
        )

    rows = []
    for row in reader:
        day = (row.get(date_col) or "").strip()
        raw_value = (row.get(value_col) or "").strip()
        if not day or raw_value in {"", ".", "NA", "NaN"}:
            continue
        try:
            rows.append({"date": day, "value": float(raw_value)})
        except ValueError:
            continue
    if not rows:
        raise ValueError(f"FRED returned no numeric observations for {series_id}")
    return {
        "series_id": series_id,
        "latest": rows[-1],
        "observations": rows[-900:],
        "source_url": source_url,
        "stale": False,
    }


def fred_series_csv(series_id: str) -> dict[str, Any]:
    # The graph download endpoint is public and does not require an API key.
    # One series per request is intentional: fredgraph.csv does not provide a
    # reliable comma-separated bulk contract for unattended GitHub runners.
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    response = request(url, official=True, timeout=(4, 10), retries=2)
    return parse_fred_series_csv(response.text, series_id, url)

def json_endpoint(url: str) -> dict[str, Any]:
    return {"payload": request(url, official=True).json(), "source_url": url}


def text_endpoint(url: str) -> dict[str, Any]:
    response = request(url, official=True)
    return {"content_type": response.headers.get("content-type"), "text": response.text[:500000], "source_url": url}


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    value = year * 12 + month - 1 + offset
    return value // 12, value % 12 + 1


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
        return value, {"name": name, "ok": True, "elapsed_ms": round((time.perf_counter()-started)*1000), "error": None}
    except Exception as exc:
        return None, {"name": name, "ok": False, "elapsed_ms": round((time.perf_counter()-started)*1000), "error": f"{type(exc).__name__}: {exc}"}


def collect_curve_parallel(symbols, group, statuses):
    log(f"[{group}] scan started: {len(symbols)} candidates")
    usable = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(safe_collect, f"{group}:{s}", yahoo_chart, s, "5d"): s for s in symbols}
        for index, future in enumerate(as_completed(futures), 1):
            value, status = future.result()
            statuses.append(status)
            if value:
                usable.append(value)
            if index % 10 == 0 or index == len(symbols):
                log(f"[{group}] {index}/{len(symbols)}, usable={len(usable)}")
    usable.sort(key=lambda x: x["symbol"])
    return usable


def load_previous_raw() -> dict[str, Any]:
    path = Path("public/data/raw.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def collect_fred(raw: dict[str, Any], statuses: list[dict[str, Any]], previous_raw: dict[str, Any]) -> None:
    ids = list(FRED_SERIES.keys())
    log(f"[4/6] FRED parallel requests: {len(ids)} series")
    previous_fred = previous_raw.get("fred", {}) if isinstance(previous_raw, dict) else {}
    live_count = 0
    cached_count = 0
    missing_count = 0
    started = time.perf_counter()

    # Six workers keeps total runtime low without hitting FRED too aggressively.
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(safe_collect, f"fred:{sid}", fred_series_csv, sid): (sid, key)
            for sid, key in FRED_SERIES.items()
        }
        for future in as_completed(futures):
            sid, key = futures[future]
            value, status = future.result()
            if value:
                raw["fred"][key] = value
                live_count += 1
                status["stale"] = False
                statuses.append(status)
                log(f"[FRED] {sid} live=True")
                continue

            cached = previous_fred.get(key)
            if cached:
                cached = dict(cached)
                cached["stale"] = True
                cached["fallback_reason"] = status.get("error") or "live request failed"
                raw["fred"][key] = cached
                cached_count += 1
                statuses.append({
                    "name": f"fred:{sid}", "ok": True, "stale": True,
                    "elapsed_ms": status.get("elapsed_ms", 0),
                    "error": cached["fallback_reason"],
                })
                log(f"[FRED] {sid} live=False cache=True")
            else:
                raw["fred"][key] = None
                missing_count += 1
                status["stale"] = False
                statuses.append(status)
                log(f"[FRED] {sid} live=False error={status.get('error')}")

    elapsed = time.perf_counter() - started
    log(f"[FRED] live={live_count}, cache={cached_count}, missing={missing_count}, elapsed={elapsed:.1f}s")

def main() -> None:
    started = time.perf_counter()
    out = Path("public/data")
    out.mkdir(parents=True, exist_ok=True)
    previous_raw = load_previous_raw()
    statuses = []
    raw = {"generated_at_utc": utc_now(), "collector_version": "3.1.0-fred-date-parser-parallel", "futures": {}, "fred": {}, "nyfed": {}, "fed": {}}

    log("[1/6] ZQ continuous")
    value, status = safe_collect("yahoo:ZQ=F", yahoo_chart, "ZQ=F", "1mo")
    raw["futures"]["zq_continuous"] = value
    statuses.append(status)

    log("[2/6] ZQ curve")
    raw["futures"]["zq_curve"] = collect_curve_parallel(contract_candidates("ZQ", (".CBT", "")), "zq", statuses)

    log("[3/6] SOFR curve")
    symbols = []
    for root in SOFR_ROOTS:
        symbols.extend(contract_candidates(root, (".CME", "")))
    raw["futures"]["sofr_curve"] = collect_curve_parallel(symbols, "sofr", statuses)

    collect_fred(raw, statuses, previous_raw)

    log("[5/6] NY Fed")
    for key, url in NYFED_ENDPOINTS.items():
        value, status = safe_collect(f"nyfed:{key}", json_endpoint, url)
        raw["nyfed"][key] = value
        statuses.append(status)
        log(f"[NYFED] {key} ok={status['ok']}")

    log("[6/6] Federal Reserve")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(safe_collect, f"fed:{key}", text_endpoint, url): key for key, url in FED_ENDPOINTS.items()}
        for future in as_completed(futures):
            key = futures[future]
            value, status = future.result()
            raw["fed"][key] = value
            statuses.append(status)
            log(f"[FED] {key} ok={status['ok']}")

    Path("public/data/raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("public/data/source_status.json").write_text(json.dumps({"generated_at_utc": utc_now(), "collector_version": "3.1.0-fred-date-parser-parallel", "sources": statuses}, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(bool(item.get("ok")) for item in statuses)
    stale = sum(bool(item.get("stale")) for item in statuses)
    log(f"COMPLETE {ok}/{len(statuses)} (stale={stale}) in {time.perf_counter()-started:.1f}s")


if __name__ == "__main__":
    main()
