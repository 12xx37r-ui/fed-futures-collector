from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

import numpy as np
from sklearn.linear_model import Ridge


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




def _series_asof_on_dates(series: dict[str, Any] | None, dates: list[str]) -> list[float | None]:
    rows = _obs(series)
    out: list[float | None] = []
    j = 0
    last: float | None = None
    for d in dates:
        while j < len(rows) and rows[j]["date"] <= d:
            last = float(rows[j]["value"])
            j += 1
        out.append(last)
    return out


def _dxy_macro_dataset(raw: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build DXY predictors from already-collected Fed-engine inputs.

    Includes domestic US financial conditions plus relative policy-rate spreads
    versus the euro area and Japan.  The two foreign-rate series are collected
    through the existing FRED provider, so no new provider or request path is
    introduced.
    """
    dates = [str(r["date"]) for r in rows]
    prices = np.array([float(r["value"]) for r in rows], dtype=float)
    fred = raw.get("fred") or {}
    keys = [
        "treasury_2y", "treasury_10y", "effr_fred", "nfci", "hy_oas", "vix",
        "ecb_deposit_rate", "japan_overnight_rate", "real_yield_10y",
    ]
    aligned = {k: _series_asof_on_dates(fred.get(k), dates) for k in keys}
    names = [
        "DXY 21d momentum", "DXY 63d momentum", "US 2Y yield", "US 2Y 21d change",
        "10Y-2Y curve", "NFCI", "HY OAS", "VIX", "EFFR-ECB deposit spread",
        "EFFR-Japan overnight spread", "US2Y-ECB deposit spread",
        "US2Y-Japan overnight spread", "US 10Y real yield",
    ]
    feats: list[list[float]] = []
    valid = []
    for i, p in enumerate(prices):
        if i < 63 or p <= 0:
            feats.append([math.nan] * len(names)); valid.append(False); continue
        required = [aligned[k][i] for k in keys]
        if any(v is None or not _finite(v) for v in required):
            feats.append([math.nan] * len(names)); valid.append(False); continue
        dgs2, dgs10, dff, nfci, hy, vix, ecb, jp, real10 = map(float, required)
        mom21 = math.log(p / prices[i-21]) * 100.0 if prices[i-21] > 0 else 0.0
        mom63 = math.log(p / prices[i-63]) * 100.0 if prices[i-63] > 0 else 0.0
        old2 = aligned["treasury_2y"][i-21] if i >= 21 else None
        d2_21 = dgs2 - float(old2) if old2 is not None and _finite(old2) else 0.0
        feats.append([
            mom21, mom63, dgs2, d2_21, dgs10-dgs2, nfci, hy, vix,
            dff-ecb, dff-jp, dgs2-ecb, dgs2-jp, real10,
        ])
        valid.append(True)
    return np.asarray(feats, dtype=float), np.asarray(valid, dtype=bool), names


def _ridge_fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_now: np.ndarray) -> float | None:
    if len(y_train) < 80:
        return None
    mu = np.nanmean(x_train, axis=0)
    sd = np.nanstd(x_train, axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    xt = (x_train - mu) / sd
    xn = (x_now - mu) / sd
    model = Ridge(alpha=12.0, fit_intercept=True)
    model.fit(xt, y_train)
    pred = float(model.predict(xn.reshape(1, -1))[0])
    return pred if math.isfinite(pred) else None


def _dxy_macro_variant(
    x: np.ndarray,
    valid: np.ndarray,
    prices: np.ndarray,
    horizon: int,
    columns: list[int],
    feature_names: list[str],
    variant: str,
) -> dict[str, Any]:
    origins = list(range(max(315, len(prices)-900-horizon), len(prices)-horizon, 5))
    errs: list[float] = []
    base_errs: list[float] = []
    hits = cases = 0
    for origin in origins:
        train_idx = [i for i in range(63, origin-horizon+1, 5) if valid[i] and i+horizon < origin+1]
        if len(train_idx) < 80 or not valid[origin]:
            continue
        xa = x[np.asarray(train_idx)][:, columns]
        ya = np.asarray([math.log(prices[i+horizon]/prices[i]) * 100.0 for i in train_idx], dtype=float)
        pred_ret = _ridge_fit_predict(xa, ya, x[origin, columns])
        if pred_ret is None:
            continue
        cap = 8.0 if horizon <= 21 else 14.0
        pred_ret = _clamp(pred_ret, -cap, cap)
        pred_level = prices[origin] * math.exp(pred_ret/100.0)
        actual = prices[origin+horizon]
        errs.append((pred_level/actual-1.0)*100.0)
        base_errs.append((prices[origin]/actual-1.0)*100.0)
        actual_ret = math.log(actual/prices[origin])*100.0
        if abs(actual_ret) >= 0.35:
            cases += 1
            if (actual_ret >= 0) == (pred_ret >= 0):
                hits += 1
    if len(errs) < 24:
        return {"available": False, "reason": "macro walk-forward samples insufficient", "samples": len(errs), "variant": variant}
    model_rmse = _rmse(errs); base_rmse = _rmse(base_errs)
    raw_skill = (1.0-model_rmse/base_rmse)*100.0 if base_rmse > 0 else -999.0
    skill = max(0.0, raw_skill)
    da = hits/cases*100.0 if cases else None
    final_idx = [i for i in range(63, len(prices)-horizon, 5) if valid[i]]
    pred_ret = _ridge_fit_predict(
        x[np.asarray(final_idx)][:, columns],
        np.asarray([math.log(prices[i+horizon]/prices[i])*100.0 for i in final_idx]),
        x[-1, columns],
    ) if valid[-1] else None
    if pred_ret is None:
        return {"available": False, "reason": "macro final fit unavailable", "samples": len(errs), "variant": variant}
    cap = 8.0 if horizon <= 21 else 14.0
    pred_ret = _clamp(pred_ret, -cap, cap)
    forecast = prices[-1] * math.exp(pred_ret/100.0)
    passed = raw_skill >= 2.0 and (da is None or da >= 52.0) and len(errs) >= 36
    return {
        "available": True, "forecast": forecast, "forecast_return_pct": pred_ret,
        "rmse_pct": model_rmse, "baseline_rmse_pct": base_rmse, "skill_pct": skill,
        "raw_skill_pct": raw_skill, "direction_accuracy": da, "direction_cases": cases,
        "backtests": len(errs), "variant": variant,
        "quality_gate": {"passed": passed, "requirements": {"skill_pct_min": 2.0, "direction_accuracy_min": 52.0, "samples_min": 36}},
        "features": [feature_names[i] for i in columns],
        "model": "expanding walk-forward ridge regression with persistence benchmark",
    }


def _dxy_macro_model(raw: dict[str, Any], rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    if len(rows) < 500:
        return {"available": False, "reason": "macro-aligned DXY history insufficient"}
    prices = np.asarray([float(r["value"]) for r in rows], dtype=float)
    x, valid, names = _dxy_macro_dataset(raw, rows)
    # Separate economic hypotheses are evaluated independently to reduce the
    # over-fitting risk of one oversized macro regression.
    variants = {
        "domestic_financial": list(range(0, 8)) + [12],
        "relative_policy_rates": [0, 1, 8, 9, 10, 11, 12],
        "combined": list(range(len(names))),
    }
    audits = {
        name: _dxy_macro_variant(x, valid, prices, horizon, cols, names, name)
        for name, cols in variants.items()
    }
    usable = [v for v in audits.values() if v.get("available")]
    if not usable:
        return {"available": False, "reason": "all macro variants unavailable", "candidate_audit": audits}
    # Selection is based only on walk-forward RMSE; the chosen model still has
    # to clear the hard quality gate before it can replace persistence/price.
    best = min(usable, key=lambda z: float(z.get("rmse_pct") or 999.0))
    out = dict(best)
    out["candidate_audit"] = audits
    out["selected_variant"] = best.get("variant")
    out["model"] = "validated selection across domestic, relative-rate and combined ridge candidates"
    return out


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
    values[-1] = current; rows[-1]["value"] = current
    price1 = _dxy_ensemble(values, 21); price3 = _dxy_ensemble(values, 63)
    macro1 = _dxy_macro_model(raw, rows, 21); macro3 = _dxy_macro_model(raw, rows, 63)

    def choose(price: dict[str, Any], macro: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if macro.get("available") and (macro.get("quality_gate") or {}).get("passed"):
            pskill = float(price.get("skill_pct") or 0.0)
            mskill = float(macro.get("skill_pct") or 0.0)
            if price.get("fallback_used") or mskill >= pskill + 1.0:
                return macro, "macro_ridge"
        return price, "price_ensemble"

    f1, sel1 = choose(price1, macro1); f3, sel3 = choose(price3, macro3)
    c3 = (float(f3["forecast"]) / current - 1.0) * 100.0
    interval = max(1.0, 1.2816 * float(f3.get("rmse_pct") or price3.get("rmse_pct") or 2.0))
    skill = float(f3.get("skill_pct") or 0.0); da = f3.get("direction_accuracy")
    confidence = round(_clamp(45 + min(25, max(0.0, skill) * 2.0) + min(18, (float(da) if da is not None else 50.0) - 50.0) - min(18, interval * 2.0), 35, 88))
    direction = "up" if c3 > 0.35 else "down" if c3 < -0.35 else "flat"
    def slim(x: dict[str, Any]) -> dict[str, Any]:
        return {k:v for k,v in x.items() if k not in {"forecast","model_forecasts","weights"}}
    return {
        "available": True, "status": "LKG" if dxy.get("stale") else "LIVE", "symbol": "DX-Y.NYB",
        "source": "Yahoo Finance DXY delayed market data", "source_url": dxy.get("source_url"),
        "observation_date": rows[-1]["date"], "market_time_utc": dxy.get("market_time_utc"), "current": round(current, 6),
        "forecast_1m": round(float(f1["forecast"]), 6), "forecast_3m": round(float(f3["forecast"]), 6),
        "forecast_change_3m_pct": round(c3, 6),
        "forecast_range_80": [round(float(f3["forecast"]) * (1 - interval / 100.0), 6), round(float(f3["forecast"]) * (1 + interval / 100.0), 6)],
        "direction_3m": direction, "confidence": confidence,
        "selected_model_1m": sel1, "selected_model_3m": sel3,
        "backtest_1m": slim(f1), "backtest_3m": slim(f3),
        "price_model_audit": {"1m": slim(price1), "3m": slim(price3)},
        "macro_model_audit": {"1m": slim(macro1), "3m": slim(macro3)},
        "model": "validated selection between price-only ensemble and macro-aware ridge model; persistence remains the hard safety benchmark",
        "limitation": "DXY price is Yahoo Finance delayed data. Macro candidates use the existing FRED/Yahoo collection path, including ECB/Japan rate differentials added to the same FRED batch; no new provider is introduced. Forecast is not a guaranteed target.",
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
        "yoy_history": [{"date": x["date"], "value": round(float(x["value"]), 6)} for x in yoy[-120:]],
        "level_history": [{"date": x["date"], "value": round(float(x["value"]), 3)} for x in rows[-132:]],
        "backtest_1m": {k: v for k, v in f1.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "backtest_3m": {k: v for k, v in f3.items() if k not in {"forecast", "model_forecasts", "weights"}},
        "forecast_quality_gate": {
            "passed": bool(not f3.get("fallback_used") and float(f3.get("skill_pct") or 0.0) > 0.0 and int(f3.get("backtests") or 0) >= 24),
            "benchmark": "persistence",
            "horizon": "3m",
        },
        "model": "monthly level walk-forward inverse-RMSE ensemble of 3m/6m/12m/damped trends with persistence safety fallback",
    }


def build_us_macro_context(raw: dict[str, Any]) -> dict[str, Any]:
    from .real_rate import build as build_real_rate
    m2 = _m2_payload(raw)
    dxy = _dxy_payload(raw)
    real_rate = build_real_rate(raw)
    available = bool(m2.get("available") or dxy.get("available") or real_rate.get("available"))
    return {
        "schema_version": "1.0",
        "metric": "us_liquidity_dxy",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "available": available,
        "m2": m2,
        "dxy": dxy,
        "real_rate": real_rate,
        "methodology": "US M2, DXY and 10Y real-rate context are calculated once in the Fed engine and published for downstream reuse. Forecasts are promoted only when walk-forward validation beats persistence; otherwise the current level is retained.",
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
    out["available"] = bool((out.get("m2") or {}).get("available") or new_dxy.get("available") or (out.get("real_rate") or {}).get("available"))
    return out
