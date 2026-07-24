from __future__ import annotations
from typing import Any
import math


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def latest(series: dict[str, Any] | None) -> float | None:
    if not series:
        return None
    item = series.get("latest")
    if isinstance(item, dict) and item.get("value") is not None:
        return float(item["value"])
    if series.get("price") is not None:
        return float(series["price"])
    return None


def change(series: dict[str, Any] | None, periods: int) -> float | None:
    if not series:
        return None
    obs = [o["value"] for o in series.get("observations", []) if o.get("value") is not None]
    if len(obs) <= periods:
        return None
    return float(obs[-1] - obs[-1-periods])


def annualized_change(series: dict[str, Any] | None, periods: int, frequency: int = 12) -> float | None:
    if not series:
        return None
    obs = [o["value"] for o in series.get("observations", []) if o.get("value") not in (None, 0)]
    if len(obs) <= periods:
        return None
    ratio = obs[-1] / obs[-1-periods]
    return (ratio ** (frequency / periods) - 1.0) * 100.0
