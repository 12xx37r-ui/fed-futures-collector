from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _points(series: dict[str, Any] | None, cutoff: date | None = None) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for row in (series or {}).get("observations") or []:
        try:
            d = date.fromisoformat(str(row["date"]))
            v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if cutoff is None or d <= cutoff:
            out.append((d, v))
    out.sort()
    return out


def _latest_value(series: dict[str, Any] | None, cutoff: date) -> float | None:
    pts = _points(series, cutoff)
    return pts[-1][1] if pts else None


def target_midpoint(raw: dict[str, Any], cutoff: date) -> float | None:
    fred = raw.get("fred") or {}
    upper = _latest_value(fred.get("target_upper"), cutoff)
    lower = _latest_value(fred.get("target_lower"), cutoff)
    if upper is not None and lower is not None:
        return (upper + lower) / 2.0
    return None


def policy_rate_asof(raw: dict[str, Any], cutoff: date) -> float | None:
    target = target_midpoint(raw, cutoff)
    if target is not None:
        return target
    return _latest_value((raw.get("fred") or {}).get("effr_fred"), cutoff)


def policy_inertia_asof(raw: dict[str, Any], origin: date, lookback_days: int = 55) -> float:
    """Return +1 cut regime, 0 hold regime, -1 hike regime.

    The feature uses only rates known before ``origin``.  A roughly one-meeting
    lookback captures whether the most recent policy step was a cut or hike,
    while a hold naturally decays back to zero.  Target-range data are preferred
    because they describe the actual FOMC decision; DFF is only a fallback.
    """
    now = policy_rate_asof(raw, origin)
    before = policy_rate_asof(raw, origin - timedelta(days=lookback_days))
    if now is None or before is None:
        return 0.0
    delta = now - before
    if delta > 0.125:
        return -1.0
    if delta < -0.125:
        return 1.0
    return 0.0


def actual_direction(raw: dict[str, Any], meeting: date) -> tuple[str, float] | None:
    """Observed FOMC direction using target-range midpoint first, then DFF.

    The target range is the policy decision itself and is therefore preferred to
    inferring the action from the effective rate.  DFF remains as a compatibility
    fallback for old cached raw files that pre-date target-range collection.
    """
    pre_target = target_midpoint(raw, meeting - timedelta(days=1))
    post_target = target_midpoint(raw, meeting + timedelta(days=2))
    if pre_target is not None and post_target is not None:
        change = post_target - pre_target
        direction = "hike" if change > 0.125 else "cut" if change < -0.125 else "hold"
        return direction, round(change * 100, 2)

    pts = _points((raw.get("fred") or {}).get("effr_fred"), meeting + timedelta(days=10))
    pre = [v for d, v in pts if meeting - timedelta(days=7) <= d < meeting]
    post = [v for d, v in pts if meeting < d <= meeting + timedelta(days=7)]
    if not pre or not post:
        return None
    change = post[-1] - pre[-1]
    direction = "hike" if change > 0.125 else "cut" if change < -0.125 else "hold"
    return direction, round(change * 100, 2)
