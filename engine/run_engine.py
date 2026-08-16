from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .confidence import calculate
from .dotplot import load_manual_dotplot, parse_sep_page
from .ensemble import combine, policy_probabilities
from .fed_text import text_score
from .fomc_calendar import all_fomc_dates, next_meeting, parse_fomc_dates
from .futures_curve import build_curve, stable_meeting_rate, target_probabilities
from .macro_model import score as macro_score
from .optimizer import optimized_weights
from .policy_regime import policy_inertia_asof
from .utils import latest


ENGINE_VERSION = "4.0.0-probability-validation-split"


def _market_freshness(raw: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for key in ("zq_continuous", "zq_curve", "sofr_curve"):
        block = (raw.get("futures") or {}).get(key)
        candidates = block if isinstance(block, list) else [block] if isinstance(block, dict) else []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            text = str(item.get("market_time_utc") or "").strip()
            age_minutes = None
            if text:
                try:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_minutes = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0)
                except Exception:
                    age_minutes = None
            rows.append({
                "group": key,
                "symbol": item.get("symbol"),
                "market_time_utc": item.get("market_time_utc"),
                "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
                "status": "LIVE" if age_minutes is not None and age_minutes <= 180 else "CACHE" if item.get("price") is not None else "UNAVAILABLE",
                "market_state": item.get("market_state"),
            })
    usable = [x for x in rows if x.get("status") != "UNAVAILABLE"]
    return {
        "contract": "V217-additive-freshness-contract",
        "new_network_calls": 0,
        "existing_pricing_semantics_changed": False,
        "market_rows_checked": len(rows),
        "market_rows_usable": len(usable),
        "items": rows,
    }


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def nyfed_latest(payload: dict | None) -> float | None:
    if not payload:
        return None

    obj = payload.get("payload", payload)
    accepted = {"percentrate", "ratepercent", "rate"}

    def walk(item: Any) -> float | None:
        if isinstance(item, dict):
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


def resolve_effective_rate(
    raw: dict[str, Any],
    zq_curve: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    effr = nyfed_latest(raw.get("nyfed", {}).get("effr"))
    if effr is not None:
        return effr, "nyfed_effr"

    effr_fred = latest(raw.get("fred", {}).get("effr_fred"))
    if effr_fred is not None:
        return effr_fred, "fred_dff"

    sofr = nyfed_latest(raw.get("nyfed", {}).get("sofr"))
    if sofr is None:
        sofr = latest(raw.get("fred", {}).get("sofr_fred"))
    if sofr is not None:
        return sofr, "sofr_proxy"

    current_month = date.today().strftime("%Y-%m")
    contract = next(
        (row for row in zq_curve if row.get("contract_month") == current_month),
        None,
    )
    if contract:
        return float(contract["implied_average_rate"]), "zq_current_month_proxy"

    return None, None


def classify_market_actions(
    targets: dict[str, float] | None,
    current_rate: float | None,
) -> dict[str, float] | None:
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


def _previous_month_key(year: int, month: int) -> str:
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def build_meeting_path(
    fomc_dates: list[str],
    zq_curve: list[dict[str, Any]],
    starting_rate: float | None,
) -> list[dict[str, Any]]:
    """Build a stable meeting path.

    For each meeting month, the pre-meeting rate is estimated from the previous
    month's ZQ average when available. This avoids recursively feeding a
    late-month inversion into all later meetings, which caused explosive values.
    """
    if starting_rate is None:
        return []

    curve_by_month = {
        str(row["contract_month"]): row
        for row in zq_curve
        if row.get("contract_month")
    }
    path: list[dict[str, Any]] = []
    seen_months: set[str] = set()
    today = date.today()
    last_valid_rate = float(starting_rate)

    for meeting_text in sorted(set(fomc_dates)):
        try:
            meeting = date.fromisoformat(meeting_text)
        except ValueError:
            continue
        if meeting < today:
            continue

        month_key = f"{meeting.year:04d}-{meeting.month:02d}"
        if month_key in seen_months:
            continue
        seen_months.add(month_key)

        contract = curve_by_month.get(month_key)
        if not contract:
            continue

        previous_contract = curve_by_month.get(
            _previous_month_key(meeting.year, meeting.month)
        )
        if previous_contract:
            pre_rate = float(previous_contract["implied_average_rate"])
            pre_rate_source = "previous_month_zq_average"
        else:
            pre_rate = last_valid_rate
            pre_rate_source = "effective_or_previous_valid_rate"

        monthly_average = float(contract["implied_average_rate"])
        post_rate, estimate_method, raw_inversion, stability_flags = stable_meeting_rate(
            monthly_average, meeting, pre_rate
        )
        change = post_rate - pre_rate
        targets = target_probabilities(post_rate, pre_rate)
        path.append({
            "meeting": meeting_text,
            "contract_symbol": contract["symbol"],
            "pre_meeting_rate": round(pre_rate, 5),
            "pre_meeting_rate_source": pre_rate_source,
            "monthly_average_rate": round(monthly_average, 5),
            "expected_post_meeting_rate": round(post_rate, 5),
            "expected_change_bps": round(change * 100, 2),
            "estimate_method": estimate_method,
            "raw_calendar_inversion_rate": round(raw_inversion, 5),
            "stability_flags": stability_flags,
            "target_rate_probabilities": targets,
            "action_probabilities": classify_market_actions(targets, pre_rate),
        })
        last_valid_rate = post_rate

    return path


def main() -> None:
    print(f"ENGINE_VERSION={ENGINE_VERSION}", flush=True)

    raw = json.loads(Path("public/data/raw.json").read_text(encoding="utf-8"))
    status = json.loads(
        Path("public/data/source_status.json").read_text(encoding="utf-8")
    )

    zq_curve = build_curve(
        raw.get("futures", {}).get("zq_curve", []),
        ("ZQ",),
    )
    sofr_curve = build_curve(
        raw.get("futures", {}).get("sofr_curve", []),
        ("SR1", "SR3"),
    )

    calendar_html = (
        raw.get("fed", {}).get("fomc_calendar") or {}
    ).get("text", "")
    live_fomc_dates = parse_fomc_dates(calendar_html)
    fomc_dates = all_fomc_dates(calendar_html)
    upcoming = next_meeting(live_fomc_dates) or next_meeting(fomc_dates)

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

    next_path = next(
        (row for row in meeting_path if row["meeting"] == upcoming),
        None,
    )
    market_probs = (
        next_path.get("target_rate_probabilities") if next_path else None
    )
    market_action_probs = (
        next_path.get("action_probabilities") if next_path else None
    )

    market_score = 0.0
    if next_path and effective_rate is not None:
        expected_post = float(next_path["expected_post_meeting_rate"])
        market_score = max(
            -1.0,
            min(1.0, (effective_rate - expected_post) / 0.50),
        )

    features = {
        "policy_inertia": policy_inertia_asof(raw, date.today()),
        "market": market_score,
        "inflation": macro["inflation"],
        "employment": macro["employment"],
        "growth": macro["growth"],
        "financial": macro["financial"],
        "fed_text": fed_comm["score"],
    }

    opt = optimized_weights()
    combined, weights_used = combine(features, opt["weights"])

    # Use only completed historical meetings to calibrate the auxiliary
    # model's action base-rates.  The representative market-implied path is
    # unchanged; this affects the self-model cross-check only.
    backtest_path = Path("public/data/backtest.json")
    backtest = json.loads(backtest_path.read_text(encoding="utf-8")) if backtest_path.exists() else {}
    class_prior = backtest.get("class_frequency") if isinstance(backtest.get("class_frequency"), dict) else None
    model_probabilities = policy_probabilities(combined, class_prior)

    feature_status = {
        "zq_curve": zq_curve,
        "sofr_curve": sofr_curve,
        "fomc_dates": fomc_dates,
        "fed_text_score": fed_comm["score"],
        "dotplot_available": (
            dot_manual.get("available")
            or bool(dot_auto.get("validated"))
        ),
    }
    confidence = calculate(status, feature_status, backtest)
    validation_passed = bool((backtest.get("quality_gate") or {}).get("passed"))
    # Safety invariant: Fed Funds futures remain the representative forecast
    # whenever a valid market-implied path exists.  The auxiliary model is
    # exposed separately for cross-checking and never silently replaces the
    # working market path merely because a reconstructed gate passes.
    if market_action_probs:
        probabilities = market_action_probs
        representative_probability_source = "market_implied_primary"
    else:
        probabilities = model_probabilities
        representative_probability_source = (
            "validated_auxiliary_model_fallback_no_market_curve"
            if validation_passed else
            "unvalidated_auxiliary_model_fallback_no_market_curve"
        )

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
        confidence["data_quality_score"] = max(0, int(confidence.get("data_quality_score") or 0) - 8)
        confidence["data_quality_grade"] = (
            "LOW" if confidence["data_quality_score"] < 70 else confidence.get("data_quality_grade", "MEDIUM")
        )
    elif effective_rate_source == "zq_current_month_proxy":
        warnings.append("EFFR·SOFR 미확보로 당월 ZQ 평균금리를 대체값으로 사용")
        confidence["data_quality_score"] = max(0, int(confidence.get("data_quality_score") or 0) - 15)
        confidence["data_quality_grade"] = "LOW"
    if not meeting_path and upcoming:
        warnings.append("다음 회의 월과 일치하는 정상 ZQ 경로 미확보")
    stabilized = [row for row in meeting_path if row.get("stability_flags")]
    if stabilized:
        warnings.append(f"월말·불안정 회의 {len(stabilized)}건은 직접 월물곡선으로 안정화")
    if not dot_manual.get("available") and not dot_auto.get("validated"):
        warnings.append("공식 점도표 자동 검증 실패·수동 검증값 미입력: 점도표 미사용")
    if not opt["active"]:
        warnings.append("자동가중치 최적화 비활성: " + opt["reason"])

    result = {
        "engine_version": ENGINE_VERSION,
        "generated_at_utc": raw.get("generated_at_utc"),
        "next_fomc": upcoming,
        "fomc_dates": fomc_dates,
        "current_effective_rate": effective_rate,
        "current_effective_rate_source": effective_rate_source,
        "probabilities": probabilities,
        "representative_probability_source": representative_probability_source,
        "model_probabilities": model_probabilities,
        "model_probability_calibration": {
            "method": "past_only_fomc_action_base_rate",
            "class_prior": class_prior or {"cut": 0.15, "hold": 0.70, "hike": 0.15},
        },
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
        "validation": backtest,
        "warnings": warnings,
        # V217: additive source/market freshness contract. No existing field changes.
        "freshness": _market_freshness(raw),
    }

    Path("public/data/latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "next_fomc": upcoming,
        "current_effective_rate": effective_rate,
        "effective_rate_source": effective_rate_source,
        "market_actions": market_action_probs,
        "probabilities": probabilities,
        "model_probabilities": model_probabilities,
        "representative_probability_source": representative_probability_source,
        "confidence": confidence,
        "validation": backtest,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
