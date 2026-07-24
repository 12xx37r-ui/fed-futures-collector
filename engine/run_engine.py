from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .confidence import calculate
from .dotplot import load_manual_dotplot, parse_sep_page
from .ensemble import combine, softmax_three
from .fed_text import text_score
from .fomc_calendar import next_meeting, parse_fomc_dates
from .futures_curve import build_curve, meeting_adjusted_rate, target_probabilities
from .macro_model import score as macro_score
from .optimizer import optimized_weights
from .utils import latest


def nyfed_latest(payload: dict | None) -> float | None:
    if not payload:
        return None
    obj = payload.get("payload", {})
    # API 필드 변경에 대비해 재귀적으로 ratePercent 탐색
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key.lower() in ("ratepercent", "rate") and isinstance(value, (int, float, str)):
                    try:
                        value = float(value)
                        if 0 <= value <= 20:
                            return value
                    except ValueError:
                        pass
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return None


def main() -> None:
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
    effr = nyfed_latest(raw.get("nyfed", {}).get("effr"))
    if effr is None:
        effr = latest(raw.get("fred", {}).get("effr_fred"))

    market_score = 0.0
    market_path = None
    market_probs = None
    if zq_curve and upcoming and effr is not None:
        meeting = date.fromisoformat(upcoming)
        contract_month = f"{meeting.year:04d}-{meeting.month:02d}"
        contract = next((x for x in zq_curve if x["contract_month"] == contract_month), None)
        if contract:
            expected_post = meeting_adjusted_rate(
                contract["implied_average_rate"], meeting, effr
            )
            market_path = {
                "meeting": upcoming,
                "pre_meeting_rate": effr,
                "monthly_average_rate": contract["implied_average_rate"],
                "expected_post_meeting_rate": round(expected_post, 5),
                "contract": contract,
            }
            market_probs = target_probabilities(expected_post, effr)
            market_score = max(-1.0, min(1.0, (effr - expected_post) / 0.50))

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

    warnings = []
    if not zq_curve:
        warnings.append("ZQ 개별 월물곡선 미확보: 연속물·거시모델 대체")
    if not sofr_curve:
        warnings.append("SOFR Futures 곡선 미확보: NY Fed SOFR·FRED 대체")
    if not upcoming:
        warnings.append("FOMC 일정 자동파싱 실패")
    if not dot_manual.get("available"):
        warnings.append("점도표 수치 자동추출은 보조용; manual dotplot 미입력")
    if not opt["active"]:
        warnings.append("자동가중치 최적화 비활성: " + opt["reason"])

    result = {
        "engine_version": "2.5.0-full-architecture",
        "generated_at_utc": raw.get("generated_at_utc"),
        "next_fomc": upcoming,
        "probabilities": probabilities,
        "market_implied_target_probabilities": market_probs,
        "market_path": market_path,
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
        "probabilities": probabilities,
        "confidence": confidence,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
