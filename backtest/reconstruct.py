from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine.ensemble import combine, policy_probabilities
from engine.policy_regime import actual_direction, policy_inertia_asof, policy_rate_asof
from engine.utils import clamp
from config import BASE_WEIGHTS

LAGS_DAYS = {
    "core_cpi": 45,
    "core_pce": 45,
    "unemployment_rate": 35,
    "nonfarm_payrolls": 35,
    "initial_claims": 7,
    "retail_sales": 45,
    "industrial_production": 45,
    "nfci": 7,
    "hy_oas": 1,
    "vix": 1,
    "treasury_2y": 1,
    "effr_fred": 1,
    "target_upper": 1,
    "target_lower": 1,
}


def _points(series: dict[str, Any] | None, cutoff: date) -> list[tuple[date, float]]:
    out = []
    for row in (series or {}).get("observations") or []:
        try:
            d = date.fromisoformat(str(row["date"]))
            v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if d <= cutoff:
            out.append((d, v))
    out.sort()
    return out


def _latest(fred: dict, key: str, origin: date) -> float | None:
    pts = _points(fred.get(key), origin - timedelta(days=LAGS_DAYS.get(key, 0)))
    return pts[-1][1] if pts else None


def _change(fred: dict, key: str, origin: date, periods: int) -> float | None:
    pts = _points(fred.get(key), origin - timedelta(days=LAGS_DAYS.get(key, 0)))
    if len(pts) <= periods:
        return None
    return pts[-1][1] - pts[-1-periods][1]


def _annualized(fred: dict, key: str, origin: date, periods: int) -> float | None:
    pts = _points(fred.get(key), origin - timedelta(days=LAGS_DAYS.get(key, 0)))
    vals = [v for _, v in pts if v != 0]
    if len(vals) <= periods:
        return None
    ratio = vals[-1] / vals[-1-periods]
    return (ratio ** (12 / periods) - 1) * 100


def features_asof(raw: dict[str, Any], origin: date) -> dict[str, float]:
    f = raw.get("fred") or {}
    policy_rate = policy_rate_asof(raw, origin - timedelta(days=1))
    dgs2 = _latest(f, "treasury_2y", origin)
    market = clamp((policy_rate - dgs2) / 0.50) if policy_rate is not None and dgs2 is not None else 0.0

    inflation_parts = []
    for key in ("core_cpi", "core_pce"):
        value = _annualized(f, key, origin, 3)
        if value is not None:
            inflation_parts.append(clamp((3.0 - value) / 3.0))
    inflation = sum(inflation_parts) / len(inflation_parts) if inflation_parts else 0.0

    employment_parts = []
    unemployment = _latest(f, "unemployment_rate", origin)
    payroll_delta = _change(f, "nonfarm_payrolls", origin, 3)
    claims = _latest(f, "initial_claims", origin)
    if unemployment is not None:
        employment_parts.append(clamp((unemployment - 4.1) / 1.2))
    if payroll_delta is not None:
        employment_parts.append(clamp((-payroll_delta) / 500.0))
    if claims is not None:
        employment_parts.append(clamp((claims - 230000) / 100000))
    employment = sum(employment_parts) / len(employment_parts) if employment_parts else 0.0

    growth_parts = []
    for key in ("retail_sales", "industrial_production"):
        value = _annualized(f, key, origin, 3)
        if value is not None:
            growth_parts.append(clamp((1.5 - value) / 5.0))
    growth = sum(growth_parts) / len(growth_parts) if growth_parts else 0.0

    financial_parts = []
    nfci = _latest(f, "nfci", origin)
    hy = _latest(f, "hy_oas", origin)
    vix = _latest(f, "vix", origin)
    if nfci is not None:
        financial_parts.append(clamp(nfci / 1.5))
    if hy is not None:
        financial_parts.append(clamp((hy - 4.0) / 3.0))
    if vix is not None:
        financial_parts.append(clamp((vix - 20.0) / 20.0))
    if dgs2 is not None:
        financial_parts.append(clamp((4.0 - dgs2) / 2.0))
    financial = sum(financial_parts) / len(financial_parts) if financial_parts else 0.0

    return {
        "policy_inertia": round(policy_inertia_asof(raw, origin), 5),
        "market": round(market, 5),
        "inflation": round(inflation, 5),
        "employment": round(employment, 5),
        "growth": round(growth, 5),
        "financial": round(financial, 5),
        # Historical Fed text is not reconstructed from today's RSS archive.
        "fed_text": 0.0,
    }


def reconstruct(raw: dict[str, Any], fomc_dates: list[str]) -> list[dict[str, Any]]:
    rows = []
    today = date.today()
    # Past-only Dirichlet prior.  The prediction for each meeting is made
    # before that meeting's outcome is added, preventing class-frequency
    # look-ahead while calibrating the naturally high FOMC hold base-rate.
    prior_counts = {"cut": 1.0, "hold": 3.0, "hike": 1.0}
    for text in sorted(set(fomc_dates)):
        try:
            meeting = date.fromisoformat(text)
        except ValueError:
            continue
        if meeting >= today - timedelta(days=5):
            continue
        actual = actual_direction(raw, meeting)
        if not actual:
            continue
        origin = meeting - timedelta(days=7)
        features = features_asof(raw, origin)
        score, _ = combine(features, BASE_WEIGHTS["next_meeting"])
        prior_total = sum(prior_counts.values()) or 1.0
        prior = {k: prior_counts[k] / prior_total for k in prior_counts}
        probs = policy_probabilities(score, prior)
        rows.append({
            "origin_date": origin.isoformat(),
            "meeting": meeting.isoformat(),
            "probabilities": probs,
            "features": features,
            "actual_direction": actual[0],
            "actual_change_bps": actual[1],
            "validation_type": "release_lagged_policy_regime_fomc_base_rate_calibrated",
            "class_prior_before_meeting": {k: round(v, 6) for k, v in prior.items()},
        })
        prior_counts[actual[0]] += 1.0
    return rows
