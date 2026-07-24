from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .confidence import calculate
from .dotplot import load_manual_dotplot, parse_sep_page
from .ensemble import combine, softmax_three
from .fed_text import text_score
from .fomc_calendar import next_meeting, parse_fomc_dates
from .futures_curve import build_curve, meeting_adjusted_rate, target_probabilities
from .macro_model import score as macro_score
from .optimizer import optimized_weights
from .utils import latest


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def nyfed_latest(payload: dict | None) -> float | None:
    """Extract the newest usable NY Fed rate despite field-name changes.

    Current NY Fed payloads commonly use ``percentRate`` while older payloads
    and examples use ``ratePercent``.  Search all nested objects and prefer the
    first rate found in list order (the API returns newest observations first).
    """
    if not payload:
        return None

    obj = payload.get("payload", payload)
    accepted = {"percentrate", "ratepercent", "rate"}

    def walk(item: Any) -> float | None:
        if isinstance(item, dict):
            # Inspect direct fields before descending so the latest row wins.
            for key, value in item.items():
                if _normalise_key(str(key)) in accepted:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    if 0.0 <= number <= 20.0:
                        return number
            for value in item.values():
                found = walk(value)
                if found is not None:
                    return found
        elif isinstance(item, list):
            for value in item:
                found = walk(value)
                if found is not None:
                    return found
        return None

    return walk(obj)


def resolve_effective_rate(raw: dict[str, Any], zq_curve: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    """Resolve the pre-meeting overnight rate with transparent fallbacks."""
    effr = nyfed_latest(raw.get("nyfed", {}).get("effr"))
    if effr is not None:
        return effr, "nyfed_effr"

    effr_fred = latest(raw.get("fred", {}).get("effr_fred"))
    if effr_fred is not None:
        return effr_fred, "fred_dff"

    # SOFR normally trades close to EFFR.  It is a proxy, not an exact
    # replacement, so the result records this explicitly and confidence is cut.
    sofr = nyfed_latest(raw.get("nyfed", {}).get("sofr"))
    if sofr is None:
        sofr = latest(raw.get("fred", {}).get("sofr_fred"))
    if sofr is not None:
        return sofr, "sofr_proxy"

    # Last-resort market-only fallback.  This keeps the engine operational but
    # is flagged as low quality because the current-month average already embeds
    # part of the upcoming meeting outcome.
    current_month = date.today().strftime("%Y-%m")
    contract = next((x for x in zq_curve if x.get("contract_month") == current_month), None)
    if contract:
        return float(contract["implied_average_rate"]), "zq_current_month_proxy"

    return None, None


def classify_market_actions(targets: dict[str, float] | None, current_rate: float | None) -> dict[str, float] | None:
    if not targets or current_rate is None:
        return None
    result = {"cut": 0.0, "hold": 0.0, "hike": 0.0}
    tolerance = 0.125
    for rate_text, probability in targets.items():
        rate = float(rate_text)
        if rate < current_rate - tolerance:
            result["cut"] += float(probability)
        elif rate > current_rate + tolerance:
            result["hike"] += float(probability)
        else:
            result["hold"] += float(probability)
    return {key: round(value, 2) for key, value in result.items()}


def build_meeting_path(
    fomc_dates: list[str],
    zq_curve: list[dict[str, Any]],
    starting_rate: float | None,
) -> list[dict[str, Any]]:
    """Build a sequential meeting path for every meeting with a matching ZQ contract."""
    if starting_rate is None:
        return []

    curve_by_month = {row["contract_month"]: row for row in zq_curve}
    path: list[dict[str, Any]] = []
    pre_rate = float(starting_rate)
    today = date.today()

    for meeting_text in sorted(fomc_dates):
        meeting = date.fromisoformat(meeting_text)
        if meeting < today:
            continue
        contract = curve_by_month.get(f"{meeting.year:04d}-{meeting.month:02d}")
        if not contract:
            continue
        post_rate = meeting_adjusted_rate(
            float(contract["implied_average_rate"]), meeting, pre_rate
        )
        targets = target_probabilities(post_rate, pre_rate)
        path.append({
            "meeting": meeting_text,
            "contract_symbol": contract["symbol"],
            "pre_meeting_rate": round(pre_rate, 5),
            "monthly_average_rate": round(float(contract["implied_average_rate"]), 5),
            "expected_post_meeting_rate": round(post_rate, 5),
            "target_rate_probabilities": targets,
            "action_probabilities": classify_market_actions(targets, pre_rate),
        })
        # Sequentially carry the expected post-meeting rate into the next month.
        pre_rate = post_rate

    return path


def main() -> None:
    print("ENGINE_VERSION=2.9.0-fomc-market-live", flush=True)
    raw = json.loads(Path("public/data/raw.json").read_text(encoding="utf-8"))
    status = json.loads(Path("public/data/source_status.json").read_text(encoding="utf-8"))

    zq_curve = build_curve(raw.get("futures", {}).get("zq_curve", []), ("ZQ",))
    sofr_curve = build_curve(raw.get("futures", {}).get("sofr_curve", []), ("SR1", "SR3"))

    calendar_html = (raw.get("fed", {}).get("fomc_calendar") or {}).get("text", "")
    fomc_dates = parse_fomc_dates(calendar_html)
    upcoming = next_meeting(fomc_dates)

    fed_comm = text_score(
        (raw.get("fed", {}).get("press_rss") or {}).get("text", ""),
        (raw.get("fed", {}).get("speeches_rss") or {}).get("text", ""),
    )

    sep_html = (raw.get("fed", {}).get("sep") or {}).get("text", "")
    dot_auto = parse_sep_page(sep_html)
    dot_manual = load_manual_dotplot()

    macro = macro_score(raw)
    effective_rate, effective_rate_source = resolve_effective_rate(raw, zq_curve)
    meeting_path = build_meeting_path(fomc_dates, zq_curve, effective_rate)

    next_path = next((row for row in meeting_path if row["meeting"] == upcoming), None)
    market_probs = next_path.get("target_rate_probabilities") if next_path else None
    market_action_probs = next_path.get("action_probabilities") if next_path else None

    market_score = 0.0
    if next_path and effective_rate is not None:
        expected_post = float(next_path["expected_post_meeting_rate"])
        market_score = max(-1.0, min(1.0, (effective_rate - expected_post) / 0.50))

    features = {
        "market": market_score,
        "inflation": macro["inflation"],
        "employment": macro["employment"],
        "growth": macro["growth"],
        "financial": macro["financial"],
        "fed_text": fed_comm["score"],
    }
    opt = optimized_weights()
    combined, weights_used = combine(features, opt["weights"])
    probabilities = softmax_three(combined)

    feature_status = {
        "zq_curve": zq_curve,
        "sofr_curve": sofr_curve,
        "fomc_dates": fomc_dates,
        "fed_text_score": fed_comm["score"],
        "dotplot_available": dot_manual.get("available") or bool(dot_auto.get("auto_candidates")),
    }
    confidence = calculate(status, feature_status)

    warnings: list[str] = []
    if not zq_curve:
        warnings.append("ZQ 개별 월물곡선 미확보")
    if not sofr_curve:
        warnings.append("SOFR Futures 곡선 미확보")
    if not upcoming:
        warnings.append("FOMC 일정 자동파싱 실패")
    if effective_rate is None:
        warnings.append("EFFR·대체 익일금리 모두 미확보: 시장확률 계산 불가")
    elif effective_rate_source == "sofr_proxy":
        warnings.append("EFFR 미확보로 SOFR를 현재금리 대체값으로 사용")
        confidence["score"] = max(0, confidence["score"] - 8)
        confidence["grade"] = "LOW" if confidence["score"] < 70 else confidence["grade"]
    elif effective_rate_source == "zq_current_month_proxy":
        warnings.append("EFFR·SOFR 미확보로 당월 ZQ 평균금리를 대체값으로 사용")
        confidence["score"] = max(0, confidence["score"] - 15)
        confidence["grade"] = "LOW"
    if not meeting_path and upcoming:
        warnings.append("다음 회의 월과 일치하는 ZQ 월물 미확보")
    if not dot_manual.get("available"):
        warnings.append("점도표 수동 검증값 미입력")
    if not opt["active"]:
        warnings.append("자동가중치 최적화 비활성: " + opt["reason"])

    result = {
        "engine_version": "2.9.0-fomc-market-live",
        "generated_at_utc": raw.get("generated_at_utc"),
        "next_fomc": upcoming,
        "fomc_dates": fomc_dates,
        "current_effective_rate": effective_rate,
        "current_effective_rate_source": effective_rate_source,
        "probabilities": probabilities,
        "market_implied_target_probabilities": market_probs,
        "market_implied_action_probabilities": market_action_probs,
        "market_path": next_path,
        "meeting_path": meeting_path,
        "features": features,
        "weights": weights_used,
        "weight_optimizer": opt,
        "curves": {"zq": zq_curve, "sofr": sofr_curve},
        "macro_blocks": macro,
        "fed_text": fed_comm,
        "dotplot": {"manual": dot_manual, "automatic": dot_auto},
        "confidence": confidence,
        "warnings": warnings,
    }
    Path("public/data/latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "next_fomc": upcoming,
        "current_effective_rate": effective_rate,
        "effective_rate_source": effective_rate_source,
        "market_actions": market_action_probs,
        "probabilities": probabilities,
        "confidence": confidence,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
