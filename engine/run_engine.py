from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
import calendar
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
from .real_rate import build as build_real_rate
from .utils import latest
from .us_macro_context import write_us_macro_context


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
        "contract": "V218-live-refetch-contract",
        "new_network_calls": 0,
        "network_refetch_each_workflow": True,
        "http_cache_bypass": True,
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


def _add_months(d: date, months: int) -> date:
    idx = d.year * 12 + d.month - 1 + months
    y, m0 = divmod(idx, 12)
    m = m0 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _policy_rate_outlook(meeting_path: list[dict[str, Any]], current_rate: float | None, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize the market-implied FOMC path at standard horizons.

    This does not create a second rate model: it is a compact view of the same
    ZQ-derived meeting path already used as the representative policy forecast.
    """
    if current_rate is None:
        return {"available": False, "reason": "current effective rate unavailable"}
    today = date.today()
    rows = []
    for row in meeting_path:
        try:
            rows.append((date.fromisoformat(str(row.get("meeting"))), row))
        except Exception:
            continue
    rows.sort(key=lambda x: x[0])
    horizons = {}
    for label, months in (("1m", 1), ("3m", 3), ("6m", 6), ("12m", 12)):
        target = _add_months(today, months)
        eligible = [row for d, row in rows if d <= target]
        chosen = eligible[-1] if eligible else None
        expected = float(chosen["expected_post_meeting_rate"]) if chosen else float(current_rate)
        # Horizon-specific market-path quality.  This is metadata only and does
        # not alter pricing semantics.  Prefer the contract month supporting the
        # selected meeting and summarize freshness/availability around that point.
        quality = {"score": 35, "grade": "LOW", "observation_count": 0, "live_ratio": 0.0, "max_age_minutes": None}
        if raw is not None and chosen:
            src_month = str(chosen.get("meeting") or "")[:7]
            rows_q = []
            for item in ((raw.get("futures") or {}).get("zq_curve") or []):
                if not isinstance(item, dict):
                    continue
                cm = str(item.get("contract_month") or "")[:7]
                if cm != src_month:
                    continue
                age = None
                try:
                    mt = datetime.fromisoformat(str(item.get("market_time_utc") or "").replace("Z", "+00:00"))
                    if mt.tzinfo is None: mt = mt.replace(tzinfo=timezone.utc)
                    age = max(0.0, (datetime.now(timezone.utc) - mt.astimezone(timezone.utc)).total_seconds()/60.0)
                except Exception:
                    pass
                rows_q.append(age)
            if rows_q:
                live = sum(1 for x in rows_q if x is not None and x <= 180)
                ratio = live / len(rows_q)
                ages = [x for x in rows_q if x is not None]
                max_age = max(ages) if ages else None
                freshness_score = 100 if max_age is not None and max_age <= 180 else 75 if max_age is not None and max_age <= 1440 else 50 if max_age is not None and max_age <= 10080 else 25
                score = round(0.65*freshness_score + 0.35*(ratio*100))
                grade = "HIGH" if score >= 80 else "MEDIUM" if score >= 55 else "LOW"
                quality = {"score": score, "grade": grade, "observation_count": len(rows_q), "live_ratio": round(ratio,3), "max_age_minutes": round(max_age,1) if max_age is not None else None}
        horizons[label] = {
            "target_date": target.isoformat(),
            "expected_rate_pct": round(expected, 5),
            "change_from_current_bps": round((expected - float(current_rate)) * 100.0, 2),
            "source_meeting": chosen.get("meeting") if chosen else None,
            "source": "Fed Funds futures / ZQ meeting path" if chosen else "current effective rate; no meeting before horizon",
            "path_confidence": quality,
        }
    return {
        "available": True,
        "current_effective_rate_pct": round(float(current_rate), 5),
        "horizons": horizons,
        "representative_source": "market-implied ZQ path",
        "model_probabilities_changed": False,
    }


def _model_action_from_validated_rule(probabilities: dict, backtest: dict) -> dict:
    rule = backtest.get("class_balanced_decision_rule") if isinstance(backtest, dict) else None
    if not isinstance(rule, dict) or not rule.get("passed"):
        return {"available": False, "reason": "validated class-balanced decision rule unavailable"}
    try:
        ratio = float(rule.get("selected_hold_ratio_threshold"))
        hold = float(probabilities.get("hold", 0.0))
        challenger = max(("cut", "hike"), key=lambda k: float(probabilities.get(k, 0.0)))
        action = challenger if float(probabilities.get(challenger, 0.0)) >= hold * ratio else "hold"
    except Exception:
        return {"available": False, "reason": "decision rule parse failure"}
    return {
        "available": True,
        "action": action,
        "hold_ratio_threshold": ratio,
        "validation": rule.get("validation"),
        "representative_forecast_changed": False,
        "note": "시장내재 확률과 자체모델 확률은 변경하지 않고 검증된 hard-action 보조분류만 제공합니다.",
    }



def _forecast_registry(result: dict[str, Any]) -> dict[str, Any]:
    """Compact machine-readable promotion registry for forward outputs."""
    ctx = result.get("us_macro_context") or {}
    m2 = ctx.get("m2") or {}
    dxy = ctx.get("dxy") or {}
    rr = result.get("real_rate") or ctx.get("real_rate") or {}
    policy = result.get("policy_rate_outlook") or {}
    entries: list[dict[str, Any]] = []
    for horizon in ("1m", "3m", "6m", "12m"):
        row = (policy.get("horizons") or {}).get(horizon) or {}
        if row:
            entries.append({"id":f"fed_policy_{horizon}","label":f"Fed policy rate {horizon}","horizon":horizon,"current":policy.get("current_effective_rate_pct"),"forecast":row.get("expected_rate_pct"),"status":"MARKET_IMPLIED","usable":True,"basis":"Fed Funds futures / ZQ meeting path","path_confidence":row.get("path_confidence"),"quality_gate":{"passed":True,"type":"market_implied_not_model_forecast"},"reason":f"market-implied path; confidence {(row.get('path_confidence') or {}).get('grade','UNKNOWN')}"})
    for horizon, backtest_key, forecast_key in (("1m","backtest_1m","forecast_1m_yoy_pct"),("3m","backtest_3m","forecast_3m_yoy_pct")):
        bt=m2.get(backtest_key) or {}; usable=bool(not bt.get("fallback_used") and float(bt.get("skill_pct") or 0)>0)
        if horizon=="3m": usable=usable and bool((m2.get("forecast_quality_gate") or {}).get("passed"))
        entries.append({"id":f"us_m2_{horizon}","label":f"US M2 YoY {horizon}","horizon":horizon,"current":m2.get("current_yoy_pct"),"forecast":m2.get(forecast_key),"status":"VALIDATED" if usable else "ABSTAIN","usable":usable,"basis":m2.get("model"),"validation":bt,"reason":(f"validated: {float(bt.get('skill_pct') or 0):.1f}% skill vs persistence" if usable else f"abstain: fallback={bool(bt.get('fallback_used'))}, skill={float(bt.get('skill_pct') or 0):.1f}%")})
    for horizon, bt_key, fc_key in (("1m","backtest_1m","forecast_1m"),("3m","backtest_3m","forecast_3m")):
        bt=dxy.get(bt_key) or {}; usable=not bool(bt.get("fallback_used")) and float(bt.get("skill_pct") or 0)>=2.0
        entries.append({"id":f"dxy_{horizon}","label":f"DXY {horizon}","horizon":horizon,"current":dxy.get("current"),"forecast":dxy.get(fc_key),"status":"VALIDATED" if usable else "ABSTAIN","usable":usable,"basis":dxy.get(f"selected_model_{horizon}"),"validation":bt,"reason":(f"validated: {float(bt.get('skill_pct') or 0):.1f}% skill vs persistence" if usable else f"abstain: no validated edge; direction accuracy {float(bt.get('direction_accuracy') or 0):.1f}%")})
    for horizon, usable_key, fc_key, bt_key in (("1m","forecast_usable_1m","forecast_1m_pct","backtest_1m"),("3m","forecast_usable_3m","forecast_3m_pct","backtest_3m")):
        usable=bool(rr.get(usable_key))
        entries.append({"id":f"real_rate_10y_{horizon}","label":f"US 10Y real yield {horizon}","horizon":horizon,"current":rr.get("current_pct"),"forecast":rr.get(fc_key),"status":"VALIDATED" if usable else "ABSTAIN","usable":usable,"basis":rr.get(f"selected_model_{horizon}"),"validation":rr.get(bt_key),"candidate_forecast":rr.get(f"candidate_forecast_{horizon}_pct"),"reason":("validated real-yield forecast" if usable else f"abstain: RMSE {float((rr.get(bt_key) or {}).get('rmse') or 0):.4f} vs persistence {float((rr.get(bt_key) or {}).get('baseline_rmse') or 0):.4f}")})
    for key,label in (("inflation","Inflation factor"),("employment","Employment factor"),("growth","Growth factor"),("financial","Financial-conditions factor")):
        entries.append({"id":f"fed_feature_{key}","label":label,"horizon":"current","current":(result.get("features") or {}).get(key),"forecast":None,"status":"CURRENT_ONLY","usable":False,"basis":"Fed policy-model normalized feature; no standalone forward forecast"})
    return {"schema_version":"1.0","generated_at_utc":result.get("generated_at_utc"),"engine":"US Fed policy engine","entries":entries,"summary":{"validated":sum(1 for x in entries if x.get("status")=="VALIDATED"),"market_implied":sum(1 for x in entries if x.get("status")=="MARKET_IMPLIED"),"abstain":sum(1 for x in entries if x.get("status")=="ABSTAIN"),"current_only":sum(1 for x in entries if x.get("status")=="CURRENT_ONLY")},"policy":"Forward values are promoted only when their own validation gate passes; market-implied policy paths are labeled separately from model forecasts."}

def main() -> None:
    print(f"ENGINE_VERSION={ENGINE_VERSION}", flush=True)

    raw = json.loads(Path("public/data/raw.json").read_text(encoding="utf-8"))
    status = json.loads(
        Path("public/data/source_status.json").read_text(encoding="utf-8")
    )
    us_macro_context = write_us_macro_context(raw)
    real_rate = us_macro_context.get("real_rate") if isinstance(us_macro_context, dict) else None
    if not isinstance(real_rate, dict):
        real_rate = build_real_rate(raw)

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
    policy_rate_outlook = _policy_rate_outlook(meeting_path, effective_rate, raw)

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
    model_action_classification = _model_action_from_validated_rule(model_probabilities, backtest)

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
        "model_action_classification": model_action_classification,
        "model_probability_calibration": {
            "method": "past_only_fomc_action_base_rate",
            "class_prior": class_prior or {"cut": 0.15, "hold": 0.70, "hike": 0.15},
        },
        "market_implied_target_probabilities": market_probs,
        "market_implied_action_probabilities": market_action_probs,
        "market_path": next_path,
        "meeting_path": meeting_path,
        "policy_rate_outlook": policy_rate_outlook,
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
        "us_macro_context": us_macro_context,
        "real_rate": real_rate,
    }

    Path("public/data/latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path("public/data/macro_forecast_registry.json").write_text(
        json.dumps(_forecast_registry(result), ensure_ascii=False, indent=2),
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
