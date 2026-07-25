from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

MONTH_CODE = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}


@dataclass
class Contract:
    symbol: str
    year: int
    month: int
    price: float
    implied_rate: float


def parse_contract(item: dict[str, Any], roots: tuple[str, ...]) -> Contract | None:
    symbol = str(item.get("symbol") or "")
    price = item.get("price")
    if price is None:
        return None
    clean = symbol.split(".")[0]
    match = re.match(r"(" + "|".join(roots) + r")([FGHJKMNQUVXZ])(\d{2})$", clean)
    if not match:
        return None
    _, code, yy = match.groups()
    year = 2000 + int(yy)
    return Contract(symbol, year, MONTH_CODE[code], float(price), 100.0 - float(price))


def build_curve(items: list[dict[str, Any]], roots: tuple[str, ...]) -> list[dict[str, Any]]:
    contracts = [parse_contract(x, roots) for x in items]
    contracts = [x for x in contracts if x is not None]
    contracts.sort(key=lambda x: (x.year, x.month))

    # Yahoo may return both exchange-suffixed and unsuffixed versions of the
    # same contract. Keep one contract per month, preferring the exchange suffix.
    by_month: dict[tuple[int, int], Contract] = {}
    for contract in contracts:
        key = (contract.year, contract.month)
        existing = by_month.get(key)
        if existing is None or ("." in contract.symbol and "." not in existing.symbol):
            by_month[key] = contract

    selected = sorted(by_month.values(), key=lambda x: (x.year, x.month))
    return [
        {
            "symbol": c.symbol,
            "contract_month": f"{c.year:04d}-{c.month:02d}",
            "price": round(c.price, 5),
            "implied_average_rate": round(c.implied_rate, 5),
        }
        for c in selected
    ]


def meeting_adjusted_rate(
    monthly_average_rate: float,
    meeting_date: date,
    pre_meeting_rate: float,
) -> float:
    """Infer the post-meeting rate from the monthly average contract rate."""
    days = calendar.monthrange(meeting_date.year, meeting_date.month)[1]
    pre_days = meeting_date.day - 1
    post_days = days - pre_days
    if post_days <= 0:
        return monthly_average_rate
    return (monthly_average_rate * days - pre_meeting_rate * pre_days) / post_days


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
