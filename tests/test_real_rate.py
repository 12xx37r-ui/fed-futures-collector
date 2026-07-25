from engine.real_rate import build

def test_real_rate_schema():
    obs=[{"date":f"2025-01-{(i%28)+1:02d}","value":1.0+i*0.001} for i in range(400)]
    raw={"fred":{"real_yield_10y":{"observations":obs},"real_yield_5y":{"observations":obs},"real_yield_20y":{"observations":obs}}}
    out=build(raw)
    assert out["available"] is True
    assert "current_pct" in out and "forecast_3m_pct" in out
