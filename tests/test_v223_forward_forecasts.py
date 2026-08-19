import json
from pathlib import Path

from engine.real_rate import build as build_real_rate
from engine.us_macro_context import _dxy_payload


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "public" / "data" / "raw.json"


def _raw():
    return json.loads(RAW.read_text(encoding="utf-8"))


def test_real_rate_3m_uses_validated_non_persistence_candidate_on_snapshot():
    out = build_real_rate(_raw())
    assert out["available"] is True
    assert out["selected_model_3m"] == "validated_mean_reversion"
    assert out["forecast_usable_3m"] is True
    assert out["forecast_3m_pct"] != out["current_pct"]
    bt = out["backtest_3m"]
    assert bt["raw_skill_pct"] >= 2.0
    assert bt["direction_accuracy"] >= 52.0


def test_dxy_3m_uses_validated_non_persistence_candidate_on_snapshot():
    out = _dxy_payload(_raw())
    assert out["available"] is True
    assert out["selected_model_3m"] == "price_mean_reversion"
    assert out["forecast_3m"] != out["current"]
    assert out["direction_3m"] in {"up", "down"}
    bt = out["backtest_3m"]
    assert bt["raw_skill_pct"] >= 2.0
    assert bt["direction_accuracy"] >= 52.0
