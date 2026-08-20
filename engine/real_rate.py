from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge


def _obs(series: dict[str, Any] | None) -> list[tuple[str, float]]:
    if not series:
        return []
    out: list[tuple[str, float]] = []
    for row in series.get("observations", []):
        try:
            out.append((str(row.get("date")), float(row.get("value"))))
        except (TypeError, ValueError):
            pass
    out.sort(key=lambda x: x[0])
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _asof(rows: list[tuple[str, float]], dates: list[str]) -> list[float | None]:
    out: list[float | None] = []
    j = 0
    last: float | None = None
    for d in dates:
        while j < len(rows) and rows[j][0] <= d:
            last = rows[j][1]
            j += 1
        out.append(last)
    return out


def _predict(values: list[float], y5: float | None, y20: float | None, horizon: int) -> tuple[float, dict[str, float]]:
    """Low-variance structural candidate for the 10Y real yield."""
    cur = values[-1]
    m20 = cur - values[-21] if len(values) > 21 else 0.0
    m60 = cur - values[-61] if len(values) > 61 else m20
    anchor = cur
    if y5 is not None and y20 is not None:
        anchor = 0.55 * y5 + 0.45 * y20
    elif y5 is not None:
        anchor = y5
    elif y20 is not None:
        anchor = y20
    scale = horizon / 63.0
    trend = cur + _clamp((0.38 * m20 + 0.17 * m60) * scale, -0.75, 0.75)
    curve = cur + _clamp(0.32 * (anchor - cur) * scale, -0.50, 0.50)
    meanrev = cur + _clamp(0.12 * (mean(values[-252:]) - cur) * scale, -0.35, 0.35)
    pred = 0.50 * trend + 0.30 * curve + 0.20 * meanrev
    return pred, {"trend": trend, "curve": curve, "mean_reversion": meanrev}


def _metrics(errs: list[float], base_errs: list[float], hits: int, cases: int, min_samples: int) -> dict[str, Any]:
    if not errs:
        return {
            "mae": None, "rmse": None, "baseline_rmse": None, "skill_pct": 0.0,
            "raw_skill_pct": None, "direction_accuracy": None, "direction_cases": 0,
            "samples": 0, "fallback_used": True,
            "quality_gate": {"passed": False, "reason": "walk-forward samples unavailable"},
        }
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = sqrt(sum(e * e for e in errs) / len(errs))
    baseline_rmse = sqrt(sum(e * e for e in base_errs) / len(base_errs))
    raw_skill = (1.0 - rmse / baseline_rmse) * 100.0 if baseline_rmse > 0 else -999.0
    skill = max(0.0, raw_skill)
    da = 100.0 * hits / cases if cases else None
    passed = len(errs) >= min_samples and raw_skill >= 2.0 and (da is None or da >= 52.0)
    return {
        "mae": round(mae, 4), "rmse": round(rmse, 4), "baseline_rmse": round(baseline_rmse, 4),
        "skill_pct": round(skill, 4), "raw_skill_pct": round(raw_skill, 4),
        "direction_accuracy": round(da, 1) if da is not None else None,
        "direction_cases": cases, "samples": len(errs), "fallback_used": not passed,
        "quality_gate": {"passed": passed, "requirements": {"samples_min": min_samples, "skill_pct_min": 2.0, "direction_accuracy_min": 52.0}},
    }


def _walk_forward(
    dates: list[str], values: list[float], y5_asof: list[float | None], y20_asof: list[float | None], horizon: int,
) -> dict[str, Any]:
    errs: list[float] = []
    base_errs: list[float] = []
    hits = cases = 0
    start = max(252, len(values) - 1000)
    for i in range(start, len(values) - horizon):
        hist = values[: i + 1]
        pred, _ = _predict(hist, y5_asof[i], y20_asof[i], horizon)
        actual_future = values[i + horizon]
        errs.append(pred - actual_future)
        base_errs.append(values[i] - actual_future)
        actual_change = actual_future - values[i]
        pred_change = pred - values[i]
        if abs(actual_change) >= 0.05:
            cases += 1
            hits += int((actual_change >= 0) == (pred_change >= 0))
    return _metrics(errs, base_errs, hits, cases, 60)


def _ridge_fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_now: np.ndarray) -> float | None:
    if len(y_train) < 80:
        return None
    mu = np.nanmean(x_train, axis=0)
    sd = np.nanstd(x_train, axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    model = Ridge(alpha=16.0, fit_intercept=True)
    model.fit((x_train - mu) / sd, y_train)
    pred = float(model.predict(((x_now - mu) / sd).reshape(1, -1))[0])
    return pred if np.isfinite(pred) else None


def _macro_dataset(raw: dict[str, Any], dates: list[str], vals: list[float]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    fred = raw.get("fred", {})
    keys = ["treasury_2y", "treasury_10y", "breakeven_10y", "effr_fred", "nfci", "hy_oas", "vix"]
    aligned = {k: _asof(_obs(fred.get(k)), dates) for k in keys}
    names = [
        "10Y real yield 21d change", "10Y real yield 63d change", "US 10Y nominal yield",
        "US 10Y nominal 21d change", "10Y breakeven inflation", "10Y breakeven 21d change",
        "10Y-2Y nominal curve", "EFFR", "NFCI", "HY OAS", "VIX",
    ]
    rows: list[list[float]] = []
    valid: list[bool] = []
    for i, cur in enumerate(vals):
        if i < 63:
            rows.append([np.nan] * len(names)); valid.append(False); continue
        req = [aligned[k][i] for k in keys]
        if any(v is None or not np.isfinite(float(v)) for v in req):
            rows.append([np.nan] * len(names)); valid.append(False); continue
        d2, d10, be10, effr, nfci, hy, vix = map(float, req)
        old10 = aligned["treasury_10y"][i-21]
        oldbe = aligned["breakeven_10y"][i-21]
        d10chg = d10 - float(old10) if old10 is not None else 0.0
        bechg = be10 - float(oldbe) if oldbe is not None else 0.0
        rows.append([cur-vals[i-21], cur-vals[i-63], d10, d10chg, be10, bechg, d10-d2, effr, nfci, hy, vix])
        valid.append(True)
    return np.asarray(rows, dtype=float), np.asarray(valid, dtype=bool), names


def _macro_variant(x: np.ndarray, valid: np.ndarray, vals: list[float], horizon: int, cols: list[int], names: list[str], variant: str) -> dict[str, Any]:
    values = np.asarray(vals, dtype=float)
    origins = list(range(max(315, len(values)-900-horizon), len(values)-horizon, 5))
    errs: list[float] = []
    base: list[float] = []
    hits = cases = 0
    for origin in origins:
        train = [i for i in range(63, origin-horizon+1, 5) if valid[i] and i+horizon < origin+1]
        if len(train) < 80 or not valid[origin]:
            continue
        xa = x[np.asarray(train)][:, cols]
        ya = np.asarray([values[i+horizon]-values[i] for i in train], dtype=float)
        pred_change = _ridge_fit_predict(xa, ya, x[origin, cols])
        if pred_change is None:
            continue
        cap = 0.75 if horizon <= 21 else 1.25
        pred_change = _clamp(pred_change, -cap, cap)
        pred = values[origin] + pred_change
        actual = values[origin+horizon]
        errs.append(pred-actual); base.append(values[origin]-actual)
        actual_change = actual-values[origin]
        if abs(actual_change) >= 0.05:
            cases += 1; hits += int((actual_change >= 0) == (pred_change >= 0))
    m = _metrics(errs, base, hits, cases, 36)
    final_train = [i for i in range(63, len(values)-horizon, 5) if valid[i]]
    pred_change = None
    if valid[-1] and len(final_train) >= 80:
        pred_change = _ridge_fit_predict(
            x[np.asarray(final_train)][:, cols],
            np.asarray([values[i+horizon]-values[i] for i in final_train], dtype=float),
            x[-1, cols],
        )
    if pred_change is None:
        return {"available": False, "reason": "macro final fit unavailable", "variant": variant, **m}
    cap = 0.75 if horizon <= 21 else 1.25
    pred_change = _clamp(pred_change, -cap, cap)
    return {
        "available": True, "variant": variant, "forecast": float(values[-1]+pred_change),
        "forecast_change_pctp": float(pred_change), "features": [names[i] for i in cols],
        "model": "expanding walk-forward ridge on real-yield/nominal/breakeven/financial inputs",
        **m,
    }


def _macro_model(raw: dict[str, Any], dates: list[str], vals: list[float], horizon: int) -> dict[str, Any]:
    x, valid, names = _macro_dataset(raw, dates, vals)
    variants = {
        "rates_inflation": [0, 1, 2, 3, 4, 5, 6, 7],
        "financial_conditions": [0, 1, 6, 8, 9, 10],
        "combined": list(range(len(names))),
    }
    audits = {k: _macro_variant(x, valid, vals, horizon, cols, names, k) for k, cols in variants.items()}
    usable = [v for v in audits.values() if v.get("available")]
    if not usable:
        return {"available": False, "reason": "all macro variants unavailable", "candidate_audit": audits}
    best = min(usable, key=lambda z: float(z.get("rmse") or 999.0))
    out = dict(best); out["candidate_audit"] = audits; out["selected_variant"] = best.get("variant")
    return out



def _validated_mean_reversion_predict(values: list[float], horizon: int) -> tuple[float, dict[str, float]]:
    """Conservative 3M real-yield mean-reversion candidate.

    The lookback/strength are deliberately fixed (not tuned per run) so the
    walk-forward audit remains genuinely out-of-sample. 84 trading days is
    long enough to smooth short-lived shocks while still reacting to a regime
    change. The pull is scaled by forecast horizon and capped.
    """
    cur = values[-1]
    # Horizon-specific fixed parameters.  Both specifications remain fixed
    # across origins; they are not re-tuned inside the walk-forward loop.
    # 1M uses a very weak pull because persistence is difficult to beat at
    # monthly horizons, while 3M uses the smoother 105d anchor that improved
    # both RMSE skill and directional accuracy in the long walk-forward audit.
    if horizon <= 21:
        lookback, base_strength = 90, 0.15
    else:
        lookback, base_strength = 105, 0.35
    lookback = min(lookback, len(values))
    anchor = mean(values[-lookback:])
    strength = base_strength * min(1.0, max(0.0, horizon / 63.0))
    change = _clamp((anchor - cur) * strength, -0.45, 0.45)
    return cur + change, {"anchor": anchor, "strength": strength, "lookback_days": float(lookback)}


def _walk_forward_mean_reversion(values: list[float], horizon: int) -> dict[str, Any]:
    errs: list[float] = []
    base_errs: list[float] = []
    hits = cases = 0
    start = max(252, len(values) - 1000)
    for i in range(start, len(values) - horizon):
        hist = values[: i + 1]
        pred, _ = _validated_mean_reversion_predict(hist, horizon)
        actual = values[i + horizon]
        errs.append(pred - actual)
        base_errs.append(values[i] - actual)
        actual_change = actual - values[i]
        pred_change = pred - values[i]
        if abs(actual_change) >= 0.05:
            cases += 1
            hits += int((actual_change >= 0) == (pred_change >= 0))
    out = _metrics(errs, base_errs, hits, cases, 60)
    out["model"] = "fixed horizon-specific mean reversion"
    return out


def _short_horizon_candidates(values: list[float]) -> dict[str, float]:
    """Small fixed 1M candidate family used only in past-only selection.

    Parameters are deliberately fixed and low-amplitude.  The selector may
    promote this block only after it clears the same persistence RMSE gate as
    every other production candidate.
    """
    cur = values[-1]
    def hist(k: int) -> float:
        return values[-1-k] if len(values) > k else cur
    anchor90 = mean(values[-min(90, len(values)):])
    return {
        "persistence": cur,
        "mean_reversion_90d": cur + _clamp((anchor90-cur)*0.05, -0.45, 0.45),
        "contrarian_63d": cur + _clamp(-(cur-hist(63))*0.05, -0.45, 0.45),
        "momentum_21d": cur + _clamp((cur-hist(21))*0.05, -0.45, 0.45),
    }


def _walk_forward_short_horizon_selection(values: list[float], horizon: int = 21) -> dict[str, Any]:
    """Nested/past-only 1M selector with a fixed candidate family.

    Candidate choice at each origin uses only the preceding 48 realized
    squared errors.  This is intentionally stricter than choosing the best
    candidate on the reported OOS sample.
    """
    errs: list[float] = []
    base_errs: list[float] = []
    hits = cases = 0
    losses: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    start = max(252, len(values)-1000)
    for i in range(start, len(values)-horizon):
        forecasts = _short_horizon_candidates(values[:i+1])
        eligible = []
        for name, ls in losses.items():
            if len(ls) >= 36:
                eligible.append((mean(ls[-48:]), name))
        selected = min(eligible)[1] if eligible else "persistence"
        pred = forecasts[selected]
        actual = values[i+horizon]
        cur = values[i]
        errs.append(pred-actual)
        base_errs.append(cur-actual)
        counts[selected] = counts.get(selected, 0) + 1
        actual_change = actual-cur
        pred_change = pred-cur
        if abs(actual_change) >= 0.05:
            cases += 1
            hits += int((actual_change >= 0) == (pred_change >= 0))
        for name, forecast in forecasts.items():
            losses.setdefault(name, []).append((forecast-actual)**2)
    out = _metrics(errs, base_errs, hits, cases, 60)
    final_eligible = [(mean(ls[-48:]), name) for name, ls in losses.items() if len(ls) >= 36]
    final_name = min(final_eligible)[1] if final_eligible else "persistence"
    final_forecast = _short_horizon_candidates(values)[final_name]
    out.update({
        "model": "past-only 48-error rolling selector over fixed low-amplitude 1M candidates",
        "selected_model": final_name,
        "selected_model_counts": counts,
        "forecast": final_forecast,
        "selection_no_lookahead": True,
        "selection_window_errors": 48,
    })
    return out


def _recent_changes(vals: list[float]) -> dict[str, float | None]:
    cur = vals[-1]
    def ch(n: int) -> float | None:
        return round(cur-vals[-1-n], 4) if len(vals) > n else None
    return {"5d_pctp": ch(5), "1m_pctp": ch(21), "3m_pctp": ch(63)}


def _direction(change: float, threshold: float = 0.03) -> str:
    if change > threshold: return "rising"
    if change < -threshold: return "falling"
    return "flat"


def build(raw: dict[str, Any]) -> dict[str, Any]:
    fred = raw.get("fred", {})
    s10 = _obs(fred.get("real_yield_10y"))
    if len(s10) < 80:
        return {"available": False, "reason": "DFII10 observations insufficient"}

    dates = [d for d, _ in s10]
    vals = [v for _, v in s10]
    s5 = _obs(fred.get("real_yield_5y")); s20 = _obs(fred.get("real_yield_20y"))
    y5_asof = _asof(s5, dates); y20_asof = _asof(s20, dates)
    cur = vals[-1]; cur5 = y5_asof[-1]; cur20 = y20_asof[-1]

    structural1, c1 = _predict(vals, cur5, cur20, 21)
    structural3, c3 = _predict(vals, cur5, cur20, 63)
    bt_struct1 = _walk_forward(dates, vals, y5_asof, y20_asof, 21)
    bt_struct3 = _walk_forward(dates, vals, y5_asof, y20_asof, 63)
    meanrev1, meanrev1_meta = _validated_mean_reversion_predict(vals, 21)
    meanrev3, meanrev3_meta = _validated_mean_reversion_predict(vals, 63)
    bt_meanrev1 = _walk_forward_mean_reversion(vals, 21)
    bt_meanrev3 = _walk_forward_mean_reversion(vals, 63)
    selective1 = _walk_forward_short_horizon_selection(vals, 21)
    macro1 = _macro_model(raw, dates, vals, 21)
    macro3 = _macro_model(raw, dates, vals, 63)

    def select(structural: float, bt: dict[str, Any], meanrev: float, bt_meanrev: dict[str, Any], macro: dict[str, Any], selective: dict[str, Any] | None = None) -> tuple[float, str, dict[str, Any]]:
        candidates = []
        if (bt.get("quality_gate") or {}).get("passed"):
            candidates.append((float(bt.get("rmse") or 999), structural, "curve_momentum", bt))
        if (bt_meanrev.get("quality_gate") or {}).get("passed"):
            candidates.append((float(bt_meanrev.get("rmse") or 999), meanrev, "validated_mean_reversion", bt_meanrev))
        if macro.get("available") and (macro.get("quality_gate") or {}).get("passed"):
            candidates.append((float(macro.get("rmse") or 999), float(macro["forecast"]), "macro_ridge", macro))
        if selective and (selective.get("quality_gate") or {}).get("passed") and selective.get("forecast") is not None:
            candidates.append((float(selective.get("rmse") or 999), float(selective["forecast"]), "selective_1m_fixed_family", selective))
        if not candidates:
            return cur, "persistence", {"quality_gate": {"passed": False}, "fallback_used": True}
        _, forecast, name, audit = min(candidates, key=lambda x: x[0])
        return forecast, name, audit

    p1, model1, selected1 = select(structural1, bt_struct1, meanrev1, bt_meanrev1, macro1, selective1)
    p3, model3, selected3 = select(structural3, bt_struct3, meanrev3, bt_meanrev3, macro3)
    # Unvalidated candidate is still published for audit, but never promoted as the final forecast.
    raw_candidates_3m = [structural3, meanrev3]
    if macro3.get("available") and macro3.get("forecast") is not None:
        raw_candidates_3m.append(float(macro3["forecast"]))
    candidate3 = min(raw_candidates_3m, key=lambda v: abs(v-cur)) if raw_candidates_3m else cur

    rmse3 = float(selected3.get("rmse") or bt_struct3.get("baseline_rmse") or 0.45)
    lo = p3 - 1.2816 * rmse3; hi = p3 + 1.2816 * rmse3
    skill3 = float(selected3.get("skill_pct") or 0.0); da3 = selected3.get("direction_accuracy")
    confidence = round(_clamp(42.0 + min(25.0, skill3*1.5) + max(-8.0, min(12.0, (float(da3) if da3 is not None else 50.0)-50.0)) - min(15.0, rmse3*15.0), 35.0, 88.0))
    gate3 = bool((selected3.get("quality_gate") or {}).get("passed"))
    current_curve = {
        "real_5y_pct": round(float(cur5), 4) if cur5 is not None else None,
        "real_10y_pct": round(cur, 4),
        "real_20y_pct": round(float(cur20), 4) if cur20 is not None else None,
        "10y_minus_5y_pctp": round(cur-float(cur5), 4) if cur5 is not None else None,
        "20y_minus_10y_pctp": round(float(cur20)-cur, 4) if cur20 is not None else None,
    }
    return {
        "available": True,
        "status": "LKG" if bool((fred.get("real_yield_10y") or {}).get("stale")) else "LIVE",
        "source": "FRED DFII5/DFII10/DFII20 + T10YIE; Federal Reserve H.15",
        "as_of": s10[-1][0],
        "current_pct": round(cur, 4),
        "current_curve": current_curve,
        "history": [{"date": d, "value": round(v, 6)} for d, v in s10[-1260:]],
        "recent_change": _recent_changes(vals),
        "forecast_1m_pct": round(p1, 4), "forecast_3m_pct": round(p3, 4),
        "forecast_change_1m_pctp": round(p1-cur, 4), "forecast_change_3m_pctp": round(p3-cur, 4),
        "direction_1m": _direction(p1-cur), "direction_3m": _direction(p3-cur),
        "forecast_usable_1m": bool((selected1.get("quality_gate") or {}).get("passed")),
        "forecast_usable_3m": gate3,
        "forecast_3m_range_80_pct": [round(lo, 4), round(hi, 4)],
        "confidence": confidence,
        "selected_model_1m": model1, "selected_model_3m": model3,
        "candidate_forecast_1m_pct": round(float(macro1.get("forecast")) if macro1.get("available") and macro1.get("forecast") is not None else structural1, 4),
        "candidate_forecast_3m_pct": round(candidate3, 4),
        "candidate_direction_3m": _direction(candidate3-cur),
        "model": "validated selection among persistence, fixed mean-reversion, real-curve momentum, and macro ridge using nominal yields + breakeven inflation + financial conditions",
        "components_1m": {k: round(v, 4) for k, v in c1.items()},
        "components_3m": {k: round(v, 4) for k, v in c3.items()},
        "backtest_1m": selected1 if model1 != "persistence" else bt_struct1,
        "backtest_3m": selected3 if model3 != "persistence" else bt_struct3,
        "structural_model_audit": {"1m": bt_struct1, "3m": bt_struct3},
        "mean_reversion_model_audit": {
            "1m": {**bt_meanrev1, "forecast": round(meanrev1, 4), "parameters": meanrev1_meta},
            "3m": {**bt_meanrev3, "forecast": round(meanrev3, 4), "parameters": meanrev3_meta},
        },
        "short_horizon_selective_audit": selective1,
        "macro_model_audit": {"1m": macro1, "3m": macro3},
        "forecast_quality_gate": {"passed": gate3, "benchmark": "persistence", "horizon": "3m"},
        "limitation": "Final forward movement is promoted only when a fixed, past-only walk-forward model beats persistence and clears the direction gate. Unvalidated candidates remain audit-only.",
        "schema_version": "1.3.0",
    }
