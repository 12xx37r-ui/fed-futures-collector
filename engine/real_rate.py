from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Any


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


def _walk_forward(
    dates: list[str],
    values: list[float],
    y5_asof: list[float | None],
    y20_asof: list[float | None],
    horizon: int,
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
    if not errs:
        return {
            "mae": None, "rmse": None, "baseline_rmse": None, "skill_pct": 0.0,
            "direction_accuracy": None, "direction_cases": 0, "samples": 0,
            "fallback_used": True,
            "quality_gate": {"passed": False, "reason": "walk-forward samples unavailable"},
        }
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = sqrt(sum(e * e for e in errs) / len(errs))
    baseline_rmse = sqrt(sum(e * e for e in base_errs) / len(base_errs))
    raw_skill = (1.0 - rmse / baseline_rmse) * 100.0 if baseline_rmse > 0 else -999.0
    skill = max(0.0, raw_skill)
    da = 100.0 * hits / cases if cases else None
    passed = len(errs) >= 60 and raw_skill >= 2.0 and (da is None or da >= 52.0)
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "baseline_rmse": round(baseline_rmse, 4),
        "skill_pct": round(skill, 4),
        "raw_skill_pct": round(raw_skill, 4),
        "direction_accuracy": round(da, 1) if da is not None else None,
        "direction_cases": cases,
        "samples": len(errs),
        "fallback_used": not passed,
        "quality_gate": {
            "passed": passed,
            "requirements": {"samples_min": 60, "skill_pct_min": 2.0, "direction_accuracy_min": 52.0},
        },
    }


def build(raw: dict[str, Any]) -> dict[str, Any]:
    fred = raw.get("fred", {})
    s10 = _obs(fred.get("real_yield_10y"))
    if len(s10) < 80:
        return {"available": False, "reason": "DFII10 observations insufficient"}

    dates = [d for d, _ in s10]
    vals = [v for _, v in s10]
    s5 = _obs(fred.get("real_yield_5y"))
    s20 = _obs(fred.get("real_yield_20y"))
    y5_asof = _asof(s5, dates)
    y20_asof = _asof(s20, dates)
    cur = vals[-1]
    cur5 = y5_asof[-1]
    cur20 = y20_asof[-1]

    p1_raw, c1 = _predict(vals, cur5, cur20, 21)
    p3_raw, c3 = _predict(vals, cur5, cur20, 63)
    bt1 = _walk_forward(dates, vals, y5_asof, y20_asof, 21)
    bt3 = _walk_forward(dates, vals, y5_asof, y20_asof, 63)

    # Forecasts are only promoted when they beat persistence OOS. Otherwise the
    # current real yield is retained rather than publishing an unvalidated move.
    p1 = p1_raw if (bt1.get("quality_gate") or {}).get("passed") else cur
    p3 = p3_raw if (bt3.get("quality_gate") or {}).get("passed") else cur
    rmse3 = float(bt3.get("rmse") or bt3.get("baseline_rmse") or 0.45)
    lo = p3 - 1.2816 * rmse3
    hi = p3 + 1.2816 * rmse3
    skill3 = float(bt3.get("skill_pct") or 0.0)
    da3 = bt3.get("direction_accuracy")
    confidence = round(_clamp(
        42.0 + min(25.0, skill3 * 1.5) + max(-8.0, min(12.0, (float(da3) if da3 is not None else 50.0) - 50.0))
        - min(15.0, rmse3 * 15.0),
        35.0, 88.0,
    ))
    return {
        "available": True,
        "status": "LKG" if bool((fred.get("real_yield_10y") or {}).get("stale")) else "LIVE",
        "source": "FRED DFII5/DFII10/DFII20; Federal Reserve H.15",
        "as_of": s10[-1][0],
        "current_pct": round(cur, 4),
        "forecast_1m_pct": round(p1, 4),
        "forecast_3m_pct": round(p3, 4),
        "forecast_change_3m_pctp": round(p3 - cur, 4),
        "forecast_3m_range_80_pct": [round(lo, 4), round(hi, 4)],
        "confidence": confidence,
        "selected_model_1m": "curve_momentum" if p1 != cur else "persistence",
        "selected_model_3m": "curve_momentum" if p3 != cur else "persistence",
        "model": "walk-forward momentum + real-yield-curve anchor + mean reversion with persistence safety gate",
        "components_1m": {k: round(v, 4) for k, v in c1.items()},
        "components_3m": {k: round(v, 4) for k, v in c3.items()},
        "backtest_1m": bt1,
        "backtest_3m": bt3,
        "schema_version": "1.1.0",
    }
