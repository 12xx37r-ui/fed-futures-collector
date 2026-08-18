from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("FRESHNESS_DATA_DIR", str(ROOT / "public" / "data")))


def read(name: str) -> dict[str, Any]:
    try:
        x = json.loads((DATA / name).read_text(encoding="utf-8"))
        return x if isinstance(x, dict) else {}
    except Exception:
        return {}


def parse_dt(v: Any) -> datetime | None:
    t = str(v or "").strip()
    if not t:
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_hours(v: Any) -> float | None:
    d = parse_dt(v)
    return None if d is None else max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 3600.0)


def source_state(row: dict[str, Any]) -> str:
    if not row:
        return "UNAVAILABLE"
    if row.get("ok") is True:
        return "LIVE"
    if row.get("classification") in {"expected_unlisted", "quality_rejection"} and row.get("blocking") is False:
        return "CACHE"
    return "UNAVAILABLE"


def main() -> None:
    latest = read("latest.json")
    status = read("source_status.json")
    source_rows = status.get("sources") if isinstance(status.get("sources"), list) else []
    source_by_name = {str(x.get("name")): x for x in source_rows if isinstance(x, dict) and x.get("name")}

    items: list[dict[str, Any]] = []

    # A. 모든 실제 수집 source. 성공/실패 자체는 이번 workflow의 network 결과다.
    for name, row in source_by_name.items():
        items.append({
            "source": name,
            "status": source_state(row),
            "cadence": "collector-run",
            "observation_at": row.get("observed_at") or row.get("market_time_utc"),
            "elapsed_ms": row.get("elapsed_ms"),
            "error": row.get("error"),
            "blocking": row.get("blocking"),
            "classification": row.get("classification"),
        })

    # B. ZQ/SOFR 시장 row는 collector가 이미 latest.json에 남긴 timestamp를 그대로 감사한다.
    market = ((latest.get("freshness") or {}).get("items") or [])
    for row in market:
        if not isinstance(row, dict):
            continue
        age = age_hours(row.get("market_time_utc"))
        market_state = str(row.get("market_state") or "").upper()
        if row.get("market_time_utc") is None:
            state = "UNAVAILABLE"
        elif market_state in {"CLOSED", "CLOSE", "POST", "PRE"}:
            state = "CACHE"
        else:
            # 기존 collector의 분류를 우선하되, 너무 오래된 행을 LIVE로 승격하지 않는다.
            prior = str(row.get("status") or "").upper()
            state = "LIVE" if prior == "LIVE" and age is not None and age <= 6.0 else "CACHE"
        items.append({
            "source": f"market:{row.get('group')}:{row.get('symbol')}",
            "status": state,
            "cadence": "intraday-market",
            "observation_at": row.get("market_time_utc"),
            "age_hours": round(age, 2) if age is not None else None,
            "market_state": market_state or None,
        })

    # C. 정책금리 현재값의 의미를 분리. EFFR은 관측금리, target range는 FOMC 이벤트형 값이다.
    effr = latest.get("current_effective_rate")
    effr_source = str(latest.get("current_effective_rate_source") or "")
    effr_ok = bool(effr_source and any(k in n.lower() and source_state(r) == "LIVE" for n, r in source_by_name.items() for k in ("effr", "nyfed")))
    items.append({
        "source": "effective_fed_funds_rate",
        "status": "LIVE" if effr is not None and effr_ok else "CACHE" if effr is not None else "UNAVAILABLE",
        "cadence": "business-day",
        "observation_at": latest.get("generated_at_utc"),
        "value": effr,
        "provider": effr_source or None,
        "semantic": "EFFR observed effective rate; not the FOMC target-range midpoint",
    })
    lower, upper = latest.get("current_target_lower"), latest.get("current_target_upper")
    items.append({
        "source": "fomc_target_range",
        "status": "CACHE" if lower is not None and upper is not None else "UNAVAILABLE",
        "cadence": "event/FOMC",
        "observation_at": latest.get("generated_at_utc"),
        "lower": lower,
        "upper": upper,
        "note": "FOMC 결정이 없는 날 값이 동일한 것은 정상입니다. 매 workflow마다 새 값이 생기는 계열이 아닙니다.",
    })

    bad = [x for x in source_rows if isinstance(x, dict) and not x.get("ok") and x.get("blocking") is not False]
    stale = [x for x in items if x.get("status") in {"CACHE", "LKG"}]
    payload = {
        "schema_version": "1.1.0",
        "patch_version": "V228-zero-call-freshness-unification",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine_generated_at_utc": latest.get("generated_at_utc"),
        "compatibility": {
            "existing_keys_removed": False,
            "existing_field_semantics_changed": False,
            "new_network_calls_added_by_patch": 0,
            "collector_network_policy_changed": False,
            "model_formulas_changed": False,
            "output_values_changed": False,
        },
        "items": items,
        "source_summary": {
            "total": len(source_rows),
            "blocking_failed": len(bad),
            "stale_or_intentionally_cached": len(stale),
        },
        "summary": {k: sum(x.get("status") == k for x in items) for k in ("LIVE", "CACHE", "LKG", "FALLBACK", "UNAVAILABLE")},
    }
    (DATA / "freshness_status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
