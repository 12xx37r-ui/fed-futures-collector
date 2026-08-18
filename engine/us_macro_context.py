from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _rmse(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 999.0


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2.0


def _obs(series: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = (series or {}).get("observations") or []
    out = []
    for row in rows:
        if row.get("date") and _finite(row.get("value")):
            out.append({"date": str(row["date"]), "value": float(row["value"])})
    out.sort(key=lambda x: x["date"])
    return out


def _month_yoy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 13:
        return []
    out = []
    for i in range(12, len(rows)):
        a, b = rows[i], rows[i - 12]
        if b["value"] > 0:
            out.append({"date": a["date"], "value": (a["value"] / b["value"] - 1.0) * 100.0})
    return out


def _linear_log_forecast(values: list[float], lookback: int, horizon: int) -> float:
    data = values[-lookback:]
    if len(data) < 3 or any(v <= 0 for v in data):
        return values[-1]
    ys = [math.log(v) for v in data]
    n = len(ys)
    xbar = (n - 1) / 2.0
    ybar = _mean(ys)
    denom = sum((i - xbar) ** 2 for i in range(n)) or 1.0
    slope = sum((i - xbar) * (y - ybar) for i, y in enumerate(ys)) / denom
    return math.exp(ybar + slope * ((n - 1 + horizon) - xbar))


def _monthly_candidates(values: list[float], horizon: int) -> list[float]:
    cur = values[-1]
    def trend(months: int, damp: float = 1.0) -> float:
        if len(values) <= months or values[-1 - months] <= 0:
            return cur
        monthly = (cur / values[-1 - months]) ** (1.0 / months) - 1.0
        return cur * (1.0 + monthly * damp) ** horizon
    p3 = trend(3, 0.90)
    p6 = trend(6, 0.80)
    p12 = _linear_log_forecast(values, min(12, len(values)), horizon)
    damped = trend(6, 0.45)
    return [p3, p6, p12, damped]


def _monthly_ensemble(values: list[float], horizon: int) -> dict[str, Any]:
    if len(values) < 40:
        raise ValueError("monthly history insufficient")
    starts = range(max(24, len(values) - 72 - horizon), len(values) - horizon)
    errors = [[] for _ in range(4)]
    baseline_errors: list[float] = []
    for origin in starts:
        hist = values[: origin + 1]
        actual = values[origin + horizon]
        if actual <= 0:
            continue
        preds = _monthly_candidates(hist, horizon)
        for i, pred in enumerate(preds):
            errors[i].append((pred / actual - 1.0) * 100.0)
        baseline_errors.append((hist[-1] / actual - 1.0) * 100.0)
    rmses = [_rmse(e) for e in errors]
    inv = [1.0 / (r * r + 0.04) for r in rmses]
    total = sum(inv) or 1.0
    weights = [x / total for x in inv]
    preds = _monthly_candidates(values, horizon)
    forecast = sum(w * p for w, p in zip(weights, preds))
    aligned = min((len(e) for e in errors), default=0)
    ensemble_errors = []
    for k in range(aligned):
        ensemble_errors.append(sum(weights[i] * errors[i][k] for i in range(4)))
    robust = max(_rmse(ensemble_errors), _median([abs(x) for x in ensemble_errors[-24:]]) * 1.4826, 0.15)
    baseline_rmse = _rmse(baseline_errors)
    skill = (1.0 - robust / baseline_rmse) * 100.0 if baseline_rmse > 0 and baseline_rmse < 900 else None
    fallback = skill is None or skill < 0
    if fallback:
        forecast = values[-1]
        robust = baseline_rmse if baseline_rmse < 900 else robust
        skill = 0.0
    return {
        "forecast": forecast,
        "rmse_pct": robust,
        "baseline_rmse_pct": baseline_rmse if baseline_rmse < 900 else None,
        "skill_pct": skill,
        "weights": weights,
        "model_forecasts": preds,
        "backtests": aligned,
        "fallback_used": fallback,
    }


def _dxy_candidates(values: list[float], horizon: int) -> list[float]:
    cur = values[-1]
    def log_trend(days: int, damp: float) -> float:
        if len(values) <= days or values[-1 - days] <= 0 or cur <= 0:
            return cur
        daily = math.log(cur / values[-1 - days]) / days
        return cur * math.exp(daily * horizon * damp)
    p20 = log_trend(20, 0.45)
    p60 = log_trend(60, 0.60)
    p120 = log_trend(120, 0.45)
    mean252 = _mean(values[-252:]) if len(values) >= 60 else cur
    meanrev = cur + (mean252 - cur) * min(0.30, horizon / 252.0 * 0.30)
    return [p20, p60, p120, meanrev]


def _dxy_ensemble(values: list[float], horizon: int) -> dict[str, Any]:
    if len(values) < 300:
        raise ValueError("DXY daily history insufficient")
    start = max(252, len(values) - 760 - horizon)
    errors = [[] for _ in range(4)]
    baseline_errors: list[float] = []
    direction_hits = 0
    direction_cases = 0
    for origin in range(start, len(values) - horizon):
        hist = values[: origin + 1]
        actual = values[origin + horizon]
        if actual <= 0:
            continue
        preds = _dxy_candidates(hist, horizon)
        for i, pred in enumerate(preds):
            errors[i].append((pred / actual - 1.0) * 100.0)
        baseline_errors.append((hist[-1] / actual - 1.0) * 100.0)
    rmses = [_rmse(e) for e in errors]
    inv = [1.0 / (r * r + 0.09) for r in rmses]
    total = sum(inv) or 1.0
    weights = [x / total for x in inv]
    preds = _dxy_candidates(values, horizon)
    forecast = sum(w * p for w, p in zip(weights, preds))
    aligned = min((len(e) for e in errors), default=0)
    ensemble_errors = [sum(weights[i] * errors[i][k] for i in range(4)) for k in range(aligned)]
    robust = max(_rmse(ensemble_errors), _median([abs(x) for x in ensemble_errors[-120:]]) * 1.4826, 0.35)
    baseline_rmse = _rmse(baseline_errors)
    skill = (1.0 - robust / baseline_rmse) * 100.0 if baseline_rmse > 0 and baseline_rmse < 900 else None
    fallback = skill is None or skill < 0
    if fallback:
        forecast = values[-1]
        robust = baseline_rmse if baseline_rmse < 900 else robust
        skill = 0.0
    # Direction audit with final weights on the same rolling origins.
    for k in range(aligned):
        origin = start + k
        actual_change = values[origin + horizon] - values[origin]
        pred_future = values[origin + horizon] * (1.0 + ensemble_errors[k] / 100.0)
        pred_change = pred_future - values[origin]
        if abs(actual_change / values[origin] * 100.0) >= 0.35:
            direction_cases += 1
            if (actual_change >= 0) == (pred_change >= 0):
                direction_hits += 1
    return {
        "forecast": forecast,
        "rmse_pct": robust,
        "baseline_rmse_pct": baseline_rmse if baseline_rmse < 900 else None,
        "skill_pct": skill,
        "weights": weights,
        "model_forecasts": preds,
        "backtests": aligned,
        "direction_accuracy": (direction_hits / direction_cases * 100.0) if direction_cases else None,
        "direction_cases": direction_cases,
        "fallback_used": fallback,
    }


def _dxy_payload(raw: dict[str, Any]) -> dict[str, Any]:
    dxy = (raw.get("market") or {}).get("dxy") or {}
    obs = dxy.get("observations") or []
    rows = [{"date": str(x.get("date")), "value": float(x.get("value"))} for x in obs if x.get("date") and _finite(x.get("value"))]
    rows.sort(key=lambda x: x["date"])
    if len(rows) < 300:
        return {"available": False, "status": "UNAVAILABLE", "reason": "DXY history insufficient"}
    values = [x["value"] for x in rows]
    current = float(dxy.get("price")) if _finite(dxy.get("price")) else values[-1]
    values[-1] = current
    f1 = _dxy_ensemble(values, 21)
    f3 = _dxy_ensemble(values, 63)
    c3 = (f3["forecast"] / current - 1.0) * 100.0
    interval = max(1.0, 1.2816 * float(f3["rmse_pct"]))
    confidence = round(_clamp(45 + min(25, max(0.0, float(f3.get("skill_pct") or 0.0)) * 2.0) + min(18, (f3.get("direction_accuracy") or 50.0) - 50.0) - min(18, interval * 2.0), 35, 88))
    direction = "up" if c3 > 0.35 else "down" if c3 < -0.35 else "flat"
    return {
        "available": True,
        "status": "LKG" if dxy.get("stale") else "LIVE",
        "symbol": "DX-Y.NYB",
        "source": "Yahoo Finance DXY delayed market data",
        "source_url": dxy.get("source_url"),
        "observation_date": rows[-1]["date"],
        "market_time_utc": dxy.get("market_time_utc"),
        "current": round(current, 6),
        "forecast_1m": round(float(f1["forecast"]), 6),
        "forecast_3m": round(float(f3["forecast"]), 6),
        "forecast_change_3m_pct": round(c3, 6),
        "forecast_range_80": [round(f3["forecast"] * (1 - interval / 100.0), 6), round(f3["forecast"] * (1 + interval / 100.0), 6)],
        "direction_3m": direction,
        "confidence": confidence,
        "backtest_1m": {k: v for k, v in f1.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "backtest_3m": {k: v for k, v in f3.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "model": "walk-forward inverse-RMSE ensemble of 20d/60d/120d damped trends and 1y mean reversion; persistence safety fallback",
        "limitation": "DXY price is Yahoo Finance delayed data; forecast is a statistical direction estimate, not an ICE real-time quote or guaranteed target.",
    }


def _m2_from_h6_text(text: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(text or "", "html.parser")
    rows: list[dict[str, Any]] = []
    months = {m.lower(): i for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
    def month_key(s: str) -> str | None:
        x = re.sub(r"\s+", " ", str(s or "")).strip()
        m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})", x, re.I)
        if m:
            return f"{int(m.group(2)):04d}-{months[m.group(1).lower()]:02d}-01"
        m = re.search(r"(20\d{2})[-/]([01]?\d)", x)
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-01"
        return None
    def num(s: str) -> float | None:
        try:
            return float(str(s).replace(",", "").strip())
        except Exception:
            return None
    for table in soup.find_all("table"):
        if "M2" not in " ".join(table.stripped_strings):
            continue
        local = []
        for tr in table.find_all("tr"):
            cells = [" ".join(x.stripped_strings).strip() for x in tr.find_all(["th", "td"])]
            if len(cells) < 3:
                continue
            dt = month_key(cells[0])
            if not dt:
                continue
            nums = [num(x) for x in cells[1:]]
            nums = [x for x in nums if x is not None]
            if len(nums) >= 2 and nums[1] > 100:
                local.append({"date": dt, "value": nums[1]})
        if len(local) >= 13:
            rows = local
            break
    return rows


def _m2_payload(raw: dict[str, Any]) -> dict[str, Any]:
    source_series = (raw.get("fred") or {}).get("m2") or {}
    rows = _obs(source_series)
    source = "FRED M2SL / Federal Reserve H.6"
    source_url = source_series.get("source_url")
    status = "LKG" if source_series.get("stale") else "LIVE"
    # If FRED is only last-good, prefer a newly fetched official H.6 series.
    if len(rows) < 40 or source_series.get("stale"):
        h6 = (raw.get("fed") or {}).get("h6") or {}
        h6_rows = _m2_from_h6_text(h6.get("text") or "")
        if len(h6_rows) >= 40:
            rows = h6_rows
            source = "Federal Reserve Board H.6 Money Stock Measures"
            source_url = h6.get("source_url")
            status = "LIVE"
    if len(rows) < 40:
        return {"available": False, "status": "UNAVAILABLE", "reason": "US M2 monthly history insufficient"}
    values = [x["value"] for x in rows]
    yoy = _month_yoy(rows)
    if len(yoy) < 4:
        return {"available": False, "status": "UNAVAILABLE", "reason": "US M2 YoY history insufficient"}
    f1 = _monthly_ensemble(values, 1)
    f3 = _monthly_ensemble(values, 3)
    current_yoy = yoy[-1]["value"]
    prior3_yoy = yoy[-4]["value"]
    denom1 = values[-12] if len(values) >= 12 else None
    denom3 = values[-10] if len(values) >= 10 else None
    forecast_yoy_1m = (f1["forecast"] / denom1 - 1.0) * 100.0 if denom1 and denom1 > 0 else current_yoy
    forecast_yoy_3m = (f3["forecast"] / denom3 - 1.0) * 100.0 if denom3 and denom3 > 0 else current_yoy
    interval = max(0.20, 1.2816 * float(f3["rmse_pct"]))
    confidence = round(_clamp(58 + min(22, max(0.0, float(f3.get("skill_pct") or 0.0)) * 2.0) - min(18, interval * 6.0), 40, 90))
    return {
        "available": True,
        "status": status,
        "source": source,
        "source_url": source_url,
        "observation_date": rows[-1]["date"],
        "level_billions_usd": round(values[-1], 3),
        "current_yoy_pct": round(current_yoy, 6),
        "prior_3m_yoy_pct": round(prior3_yoy, 6),
        "acceleration_3m_pp": round(current_yoy - prior3_yoy, 6),
        "forecast_1m_level_billions_usd": round(float(f1["forecast"]), 3),
        "forecast_3m_level_billions_usd": round(float(f3["forecast"]), 3),
        "forecast_1m_yoy_pct": round(forecast_yoy_1m, 6),
        "forecast_3m_yoy_pct": round(forecast_yoy_3m, 6),
        "forecast_change_3m_pp": round(forecast_yoy_3m - current_yoy, 6),
        "forecast_3m_level_range_80": [round(f3["forecast"] * (1 - interval / 100.0), 3), round(f3["forecast"] * (1 + interval / 100.0), 3)],
        "confidence": confidence,
        "backtest_1m": {k: v for k, v in f1.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "backtest_3m": {k: v for k, v in f3.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "model": "monthly level walk-forward inverse-RMSE ensemble of 3m/6m/12m/damped trends with persistence safety fallback",
    }


def build_us_macro_context(raw: dict[str, Any]) -> dict[str, Any]:
    m2 = _m2_payload(raw)
    dxy = _dxy_payload(raw)
    available = bool(m2.get("available") or dxy.get("available"))
    return {
        "schema_version": "1.0",
        "metric": "us_liquidity_dxy",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "available": available,
        "m2": m2,
        "dxy": dxy,
        "methodology": "US M2 and DXY are calculated once in the Fed engine and published for downstream reuse. Each forecast uses walk-forward error weighting with persistence safety fallback.",
        "downstream_contract": "Global engine should reuse these values first and only query independent official fallbacks if this context is unavailable or stale.",
    }


def write_us_macro_context(raw: dict[str, Any], path: str | Path = "public/data/us_liquidity_dxy.json") -> dict[str, Any]:
    out = build_us_macro_context(raw)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def refresh_dxy_context(existing: dict[str, Any], dxy_raw: dict[str, Any]) -> dict[str, Any]:
    raw = {"market": {"dxy": dxy_raw}}
    new_dxy = _dxy_payload(raw)
    out = dict(existing or {})
    out["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    out["dxy"] = new_dxy
    out["available"] = bool((out.get("m2") or {}).get("available") or new_dxy.get("available"))
    return out
