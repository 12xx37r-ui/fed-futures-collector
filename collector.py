from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import FED_ENDPOINTS, FRED_SERIES, NYFED_ENDPOINTS, SOFR_ROOTS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,text/csv,application/xml,text/xml,text/html,*/*",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}
FAST_TIMEOUT = (3, 6)
OFFICIAL_TIMEOUT = (4, 15)
FRED_BULK_TIMEOUT = (4, 18)
MAX_WORKERS = 4
CURVE_MONTHS_AHEAD = 18
MONTH_TO_CODE = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}
SECRET_QUERY_KEYS = frozenset({"api_key", "apikey", "token", "access_token", "key"})
REDACTED = "[REDACTED]"

# Daily policy/market series need a longer history for meeting-by-meeting validation.
# Monthly macro series remain capped at 2,500 observations to avoid bloating raw.json.
FRED_LONG_HISTORY_SERIES = frozenset({"DFF", "DFEDTARU", "DFEDTARL", "DGS2", "VIXCLS", "NFCI"})

def fred_retention_limit(series_id: str) -> int:
    return 6000 if series_id in FRED_LONG_HISTORY_SERIES else 2500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(message, flush=True)


def sanitize_url(url: str | None) -> str | None:
    """Return a publish-safe URL while preserving a useful source locator."""
    if not url:
        return url
    parts = urlsplit(str(url))
    query = [
        (key, REDACTED if key.lower() in SECRET_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def redact_secrets(value: Any) -> Any:
    """Recursively redact secrets before any payload is persisted or logged."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SECRET_QUERY_KEYS or lowered in {"authorization", "password", "secret"}:
                out[key] = REDACTED
            elif lowered in {"source_url", "url", "request_url"} and isinstance(item, str):
                out[key] = sanitize_url(item)
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str) and "?" in value:
        return re.sub(r"https?://[^\s'\"<>]+", lambda m: sanitize_url(m.group(0)) or "", value)
    return value



# V220: process-wide provider pacing prevents bursty parallel requests from
# tripping public API rate limits while still refetching on every workflow.
_PROVIDER_LOCK = Lock()
_PROVIDER_LAST = {}
_PROVIDER_MIN_INTERVAL = {
    "query1.finance.yahoo.com": 0.18,
    "query2.finance.yahoo.com": 0.18,
    "api.stlouisfed.org": 0.60,
    "www.newyorkfed.org": 0.20,
    "www.federalreserve.gov": 0.20,
}

def _pace(url: str) -> None:
    host = urlsplit(url).netloc.lower()
    gap = _PROVIDER_MIN_INTERVAL.get(host, 0.08)
    with _PROVIDER_LOCK:
        now = time.monotonic()
        wait = gap - (now - _PROVIDER_LAST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _PROVIDER_LAST[host] = time.monotonic()

def make_session(total_retries: int = 1) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session


def request(url: str, official: bool = False, timeout=None, retries: int = 1) -> requests.Response:
    timeout = timeout or (OFFICIAL_TIMEOUT if official else FAST_TIMEOUT)
    with make_session(retries) as session:
        _pace(url)
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        return response


def yahoo_chart(symbol: str, range_: str = "5d") -> dict[str, Any]:
    # V218: every workflow execution reaches Yahoo with a unique request URL.
    # The 1d history remains unchanged for model/backtest compatibility; Yahoo
    # chart metadata provides the current market price/time used by the curve.
    nonce = int(time.time())
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="")
           + f"?range={range_}&interval=1d&events=history&_ts={nonce}")
    payload = request(url, retries=2).json()
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
    if not observations:
        raise ValueError(f"No observed close for {symbol}; metadata-only price rejected")

    market_time_utc = None
    raw_market_time = meta.get("regularMarketTime")
    try:
        if raw_market_time not in (None, ""):
            market_time_utc = datetime.fromtimestamp(int(raw_market_time), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        market_time_utc = None

    return {
        "symbol": symbol,
        "price": float(price),
        "exchange": meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "observations": observations,
        "source_url": url,
        # V217 additive freshness metadata; existing pricing keys are unchanged.
        "market_time_utc": market_time_utc,
        "regular_market_price": float(price),
        "exchange_timezone": meta.get("exchangeTimezoneName"),
        "market_state": meta.get("marketState"),
        "retrieved_at_utc": utc_now(),
        "refetch_policy": "network_each_workflow_no_cache",
    }


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
        "observations": rows[-fred_retention_limit(series_id):],
        "source_url": source_url,
        "stale": False,
    }



def fred_series_csv(series_id: str) -> dict[str, Any]:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FRED_API_KEY is missing from GitHub Actions Secrets.")

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }

    with make_session(total_retries=1) as session:
        _pace(url)
        response = session.get(
            url,
            params=params,
            timeout=(5, 20),
            allow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()

    if payload.get("error_message"):
        raise RuntimeError(f"FRED API error: {payload['error_message']}")

    rows = []
    for item in payload.get("observations") or []:
        day = str(item.get("date") or "").strip()
        raw_value = str(item.get("value") or "").strip()
        if not day or raw_value in {"", ".", "NA", "NaN"}:
            continue
        try:
            rows.append({
                "date": day,
                "value": float(raw_value),
                "realtime_start": item.get("realtime_start"),
                "realtime_end": item.get("realtime_end"),
            })
        except ValueError:
            continue

    if not rows:
        raise ValueError(f"FRED API returned no numeric observations for {series_id}")

    return {
        "series_id": series_id,
        "latest": rows[-1],
        "observations": rows[-fred_retention_limit(series_id):],
        "source_url": sanitize_url(response.url),
        "stale": False,
        "retrieved_at_utc": utc_now(),
        "point_in_time_snapshot": True,
    }

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
        error = redact_secrets(f"{type(exc).__name__}: {exc}")
        return None, {"name": name, "ok": False, "elapsed_ms": round((time.perf_counter()-started)*1000), "error": error}


def collect_curve_parallel(symbols, group, statuses):
    log(f"[{group}] scan started: {len(symbols)} exchange-qualified candidates")
    usable = []
    missing = 0
    errors = []
    quality_rejections = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(safe_collect, f"{group}:{s}", yahoo_chart, s, "5d"): s for s in symbols}
        for index, future in enumerate(as_completed(futures), 1):
            value, status = future.result()
            if value:
                usable.append(value)
                statuses.append(status)
            elif "404 Client Error" in str(status.get("error")):
                missing += 1  # an unlisted future month is expected, not a source failure
            elif "metadata-only price rejected" in str(status.get("error")) or "No observed close" in str(status.get("error")):
                status["classification"] = "quality_rejection"
                status["blocking"] = False
                quality_rejections.append(status)
                statuses.append(status)
            else:
                errors.append(status)
                statuses.append(status)
            if index % 10 == 0 or index == len(symbols):
                log(f"[{group}] {index}/{len(symbols)}, usable={len(usable)}, unlisted={missing}, errors={len(errors)}, quality_rejections={len(quality_rejections)}")
    statuses.append({
        "name": f"{group}:curve_summary",
        "ok": bool(usable),
        "elapsed_ms": 0,
        "error": None if usable else "no usable contracts",
        "attempted": len(symbols),
        "usable": len(usable),
        "expected_unlisted": missing,
        "quality_rejections": len(quality_rejections),
        "unexpected_errors": len(errors),
    })
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
    log(f"[4/6] FRED official API requests: {len(ids)} series")
    previous_fred = previous_raw.get("fred", {}) if isinstance(previous_raw, dict) else {}
    live_count = 0
    cached_count = 0
    missing_count = 0
    started = time.perf_counter()

    # Two workers reduce simultaneous pressure on FRED and improve runner reliability.
    with ThreadPoolExecutor(max_workers=2) as executor:
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



def collect_dxy(raw: dict[str, Any], statuses: list[dict[str, Any]], previous_raw: dict[str, Any]) -> None:
    """Collect DXY once for the Fed-engine downstream liquidity contract."""
    log("[DXY] 5y daily history")
    value, status = safe_collect("yahoo:DX-Y.NYB", yahoo_chart, "DX-Y.NYB", "5y")
    if value:
        value["stale"] = False
        raw.setdefault("market", {})["dxy"] = value
        statuses.append(status)
        log(f"[DXY] live=True elapsed_ms={status.get('elapsed_ms')}")
        return
    cached = ((previous_raw.get("market") or {}).get("dxy") if isinstance(previous_raw, dict) else None)
    if isinstance(cached, dict) and cached.get("observations"):
        cached = dict(cached)
        cached["stale"] = True
        cached["fallback_reason"] = status.get("error") or "live DXY request failed"
        raw.setdefault("market", {})["dxy"] = cached
        statuses.append({"name":"yahoo:DX-Y.NYB","ok":True,"stale":True,"elapsed_ms":status.get("elapsed_ms",0),"error":cached["fallback_reason"]})
        log("[DXY] live=False lkg=True")
    else:
        raw.setdefault("market", {})["dxy"] = None
        statuses.append(status)
        log(f"[DXY] live=False error={status.get('error')}")


def collect_h6_if_m2_not_live(raw: dict[str, Any], statuses: list[dict[str, Any]]) -> None:
    """Use Federal Reserve H.6 only when FRED M2SL was not obtained LIVE."""
    m2 = (raw.get("fred") or {}).get("m2") or {}
    if isinstance(m2, dict) and m2.get("observations") and not m2.get("stale"):
        log("[US M2] FRED M2SL live; H.6 network fallback skipped")
        return
    url = "https://www.federalreserve.gov/releases/h6/current/default.htm"
    value, status = safe_collect("fed:h6", text_endpoint, url)
    raw.setdefault("fed", {})["h6"] = value
    statuses.append(status)
    log(f"[US M2] H.6 fallback ok={status.get('ok')} elapsed_ms={status.get('elapsed_ms')}")

def main() -> None:
    started = time.perf_counter()
    out = Path("public/data")
    out.mkdir(parents=True, exist_ok=True)
    previous_raw = load_previous_raw()
    statuses = []
    raw = {"generated_at_utc": utc_now(), "collector_version": "3.6.0-objective-validation", "futures": {}, "fred": {}, "nyfed": {}, "fed": {}, "market": {}}

    log("[1/6] ZQ continuous")
    value, status = safe_collect("yahoo:ZQ=F", yahoo_chart, "ZQ=F", "5y")
    raw["futures"]["zq_continuous"] = value
    statuses.append(status)

    log("[2/6] ZQ curve")
    raw["futures"]["zq_curve"] = collect_curve_parallel(contract_candidates("ZQ", (".CBT",)), "zq", statuses)

    log("[3/6] SOFR curve")
    symbols = []
    for root in SOFR_ROOTS:
        symbols.extend(contract_candidates(root, (".CME",)))
    raw["futures"]["sofr_curve"] = collect_curve_parallel(symbols, "sofr", statuses)

    collect_fred(raw, statuses, previous_raw)
    collect_h6_if_m2_not_live(raw, statuses)
    collect_dxy(raw, statuses, previous_raw)

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

    # The API key remains available to the live request through the environment,
    # but no credential-bearing URL or error string may cross this persistence boundary.
    raw = redact_secrets(raw)
    status_payload = redact_secrets({"generated_at_utc": utc_now(), "collector_version": "3.9.0-policy-regime-validation", "sources": statuses})
    raw["collector_version"] = "3.9.0-policy-regime-validation"
    raw["security"] = {"credentials_persisted": False, "redaction": "recursive_url_and_secret_fields"}
    Path("public/data/raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("public/data/source_status.json").write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    vintage_dir = Path("public/data/vintages")
    vintage_dir.mkdir(parents=True, exist_ok=True)
    vintage_day = datetime.now(timezone.utc).date().isoformat()
    (vintage_dir / f"{vintage_day}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(bool(item.get("ok")) for item in statuses)
    stale = sum(bool(item.get("stale")) for item in statuses)
    log(f"COMPLETE {ok}/{len(statuses)} (stale={stale}) in {time.perf_counter()-started:.1f}s")


if __name__ == "__main__":
    main()
