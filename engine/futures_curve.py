from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

MONTH_CODE = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}


@dataclass
class Contract:
    symbol: str
    root: str
    year: int
    month: int
    price: float
    implied_rate: float
    observations: int


def parse_contract(item: dict[str, Any], roots: tuple[str, ...]) -> Contract | None:
    symbol = str(item.get("symbol") or "")
    price = item.get("price")
    observations = item.get("observations")
    # A metadata price without an actual observation is not a tradeable curve point.
    if price is None or not isinstance(observations, list) or not observations:
        return None
    clean = symbol.split(".")[0]
    match = re.match(r"(" + "|".join(roots) + r")([FGHJKMNQUVXZ])(\d{2})$", clean)
    if not match:
        return None
    root, code, yy = match.groups()
    price_f = float(price)
    if not (80.0 <= price_f <= 100.5):
        return None
    year = 2000 + int(yy)
    return Contract(
        symbol=symbol,
        root=root,
        year=year,
        month=MONTH_CODE[code],
        price=price_f,
        implied_rate=100.0 - price_f,
        observations=len(observations),
    )


def _remove_local_outliers(contracts: list[Contract], max_deviation: float = 0.75) -> list[Contract]:
    """Reject isolated curve points far from both neighbouring months.

    The threshold is in percentage points. It is intentionally wide enough to
    retain genuine repricing while excluding stale/mis-mapped Yahoo metadata.
    """
    if len(contracts) < 3:
        return contracts
    kept: list[Contract] = []
    for i, contract in enumerate(contracts):
        neighbours = []
        if i > 0:
            neighbours.append(contracts[i - 1].implied_rate)
        if i + 1 < len(contracts):
            neighbours.append(contracts[i + 1].implied_rate)
        if len(neighbours) == 2:
            centre = median(neighbours)
            if abs(contract.implied_rate - centre) > max_deviation:
                continue
        kept.append(contract)
    return kept


def build_curve(items: list[dict[str, Any]], roots: tuple[str, ...]) -> list[dict[str, Any]]:
    contracts = [parse_contract(x, roots) for x in items]
    contracts = [x for x in contracts if x is not None]
    contracts.sort(key=lambda x: (x.year, x.month, roots.index(x.root)))

    # Keep one point per month. Root order defines preference (SR1 before SR3),
    # then prefer exchange-suffixed symbols and the richer observation history.
    by_month: dict[tuple[int, int], Contract] = {}
    for contract in contracts:
        key = (contract.year, contract.month)
        existing = by_month.get(key)
        if existing is None:
            by_month[key] = contract
            continue
        new_rank = (roots.index(contract.root), 0 if "." in contract.symbol else 1, -contract.observations)
        old_rank = (roots.index(existing.root), 0 if "." in existing.symbol else 1, -existing.observations)
        if new_rank < old_rank:
            by_month[key] = contract

    selected = sorted(by_month.values(), key=lambda x: (x.year, x.month))
    selected = _remove_local_outliers(selected)
    return [
        {
            "symbol": c.symbol,
            "contract_month": f"{c.year:04d}-{c.month:02d}",
            "price": round(c.price, 5),
            "implied_average_rate": round(c.implied_rate, 5),
            "observation_count": c.observations,
        }
        for c in selected
    ]


def meeting_adjusted_rate(
    monthly_average_rate: float,
    meeting_date: date,
    pre_meeting_rate: float,
) -> float:
    days = calendar.monthrange(meeting_date.year, meeting_date.month)[1]
    pre_days = meeting_date.day - 1
    post_days = days - pre_days
    if post_days <= 0:
        return monthly_average_rate
    return (monthly_average_rate * days - pre_meeting_rate * pre_days) / post_days


def stable_meeting_rate(
    monthly_average_rate: float,
    meeting_date: date,
    pre_meeting_rate: float,
) -> tuple[float, str, float, list[str]]:
    """Return a robust post-meeting estimate and an auditable method label."""
    raw = meeting_adjusted_rate(monthly_average_rate, meeting_date, pre_meeting_rate)
    days = calendar.monthrange(meeting_date.year, meeting_date.month)[1]
    post_days = days - (meeting_date.day - 1)
    flags: list[str] = []

    unstable = False
    if post_days < 7:
        unstable = True
        flags.append("late_month_meeting")
    if abs(raw - pre_meeting_rate) > 0.50:
        unstable = True
        flags.append("raw_change_over_50bp")
    if abs(raw - monthly_average_rate) > 0.35:
        unstable = True
        flags.append("raw_far_from_monthly_curve")
    if not (-0.25 <= raw <= 15.0):
        unstable = True
        flags.append("raw_out_of_bounds")

    if unstable:
        # The monthly average is the direct futures observation and does not
        # amplify a small curve move merely because a meeting occurs near month-end.
        estimate = monthly_average_rate
        method = "monthly_curve_proxy"
    else:
        estimate = raw
        method = "calendar_weighted_inversion"

    # One meeting cannot create an unbounded discontinuity in production output.
    delta = max(-0.50, min(0.50, estimate - pre_meeting_rate))
    if abs(delta - (estimate - pre_meeting_rate)) > 1e-12:
        flags.append("change_capped_50bp")
        estimate = pre_meeting_rate + delta
        method += "_capped"
    return estimate, method, raw, flags


def target_probabilities(
    expected_rate: float,
    current_rate: float,
    step: float = 0.25,
) -> dict[str, float]:
    diff_steps = (expected_rate - current_rate) / step
    lower = int(diff_steps // 1)
    upper = lower + 1
    upper_prob = max(0.0, min(1.0, diff_steps - lower))
    lower_prob = 1.0 - upper_prob

    outcomes: dict[str, float] = {}
    for steps, probability in ((lower, lower_prob), (upper, upper_prob)):
        rate = current_rate + steps * step
        key = f"{rate:.2f}"
        outcomes[key] = outcomes.get(key, 0.0) + probability

    return {key: round(value * 100, 2) for key, value in sorted(outcomes.items())}
