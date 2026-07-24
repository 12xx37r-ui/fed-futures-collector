from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from confidence import calculate_confidence


def latest_value(item: dict[str, Any] | None) -> float | None:
    if not item:
        return None
    latest = item.get("latest")
    if isinstance(latest, dict):
        value = latest.get("value")
        return float(value) if value is not None else None
    price = item.get("regular_market_price")
    return float(price) if price is not None else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_three_way(cut: float, hold: float, hike: float) -> dict[str, float]:
    values = [max(0.0, cut), max(0.0, hold), max(0.0, hike)]
    total = sum(values) or 1.0
    return {
        "cut": round(values[0] / total * 100, 1),
        "hold": round(values[1] / total * 100, 1),
        "hike": round(values[2] / total * 100, 1),
    }


def build_macro_score(raw: dict[str, Any]) -> tuple[float, dict[str, float]]:
    fred = raw.get("fred", {})

    core_cpi = latest_value(fred.get("core_cpi"))
    unemployment = latest_value(fred.get("unemployment_rate"))
    claims = latest_value(fred.get("initial_claims"))
    treasury_2y = latest_value(fred.get("treasury_2y"))
    hy_oas = latest_value(fred.get("hy_oas"))
    nfci = latest_value(fred.get("nfci"))

    blocks = {
        "inflation": 0.0,
        "employment": 0.0,
        "growth_financial": 0.0,
    }

    if core_cpi is not None:
        blocks["inflation"] = clamp((3.0 - core_cpi) / 2.0, -1.0, 1.0)

    if unemployment is not None:
        blocks["employment"] += clamp((unemployment - 4.0) / 1.5, -1.0, 1.0) * 0.7
    if claims is not None:
        blocks["employment"] += clamp((claims - 230000.0) / 120000.0, -1.0, 1.0) * 0.3

    financial_parts = []
    if treasury_2y is not None:
        financial_parts.append(clamp((4.0 - treasury_2y) / 2.0, -1.0, 1.0))
    if hy_oas is not None:
        financial_parts.append(clamp((hy_oas - 4.0) / 3.0, -1.0, 1.0))
    if nfci is not None:
        financial_parts.append(clamp(nfci / 1.5, -1.0, 1.0))
    if financial_parts:
        blocks["growth_financial"] = sum(financial_parts) / len(financial_parts)

    macro_score = (
        blocks["inflation"] * 0.45
        + blocks["employment"] * 0.35
        + blocks["growth_financial"] * 0.20
    )
    return macro_score, blocks


def build_market_score(raw: dict[str, Any]) -> float:
    zq = latest_value(raw.get("market", {}).get("zq_continuous"))
    if zq is None:
        return 0.0
    implied_rate = 100.0 - zq
    # 임시 초기식. 이후 월물곡선과 FOMC 일수 분해식으로 교체한다.
    return clamp((4.5 - implied_rate) / 1.5, -1.0, 1.0)


def main() -> None:
    raw = json.loads(Path("public/data/raw.json").read_text(encoding="utf-8"))
    status = json.loads(
        Path("public/data/source_status.json").read_text(encoding="utf-8")
    )

    market_score = build_market_score(raw)
    macro_score, macro_blocks = build_macro_score(raw)

    # 고정된 1차 앙상블: 다음 회의는 시장선물 중심
    combined_score = market_score * 0.60 + macro_score * 0.40

    cut = 33.3 + combined_score * 28.0
    hike = 12.0 - combined_score * 10.0
    hold = 100.0 - cut - hike
    probabilities = normalize_three_way(cut, hold, hike)

    confidence = calculate_confidence(status)

    latest = {
        "engine_version": "2.0.0-alpha",
        "generated_at_utc": raw.get("generated_at_utc"),
        "probabilities": probabilities,
        "scores": {
            "market_futures": round(market_score, 4),
            "macro": round(macro_score, 4),
            "combined": round(combined_score, 4),
            "macro_blocks": {k: round(v, 4) for k, v in macro_blocks.items()},
        },
        "confidence": confidence,
        "warnings": [
            "현재는 ZQ 연속물 기반 초기식입니다.",
            "개별 ZQ 월물곡선과 FOMC 회의 전후 일수 분해는 다음 단계에서 추가됩니다.",
            "본 결과는 투자 조언이 아니라 모델 개발용 중간 산출물입니다.",
        ],
    }

    Path("public/data/latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(latest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
