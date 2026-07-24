from __future__ import annotations
from typing import Any
from .utils import annualized_change, change, clamp, latest


def score(raw: dict[str, Any]) -> dict[str, float]:
    f = raw.get("fred", {})

    core_cpi_3m = annualized_change(f.get("core_cpi"), 3)
    core_pce_3m = annualized_change(f.get("core_pce"), 3)
    unemployment = latest(f.get("unemployment_rate"))
    payroll_delta = change(f.get("nonfarm_payrolls"), 3)
    claims = latest(f.get("initial_claims"))
    retail_3m = annualized_change(f.get("retail_sales"), 3)
    indpro_3m = annualized_change(f.get("industrial_production"), 3)
    nfci = latest(f.get("nfci"))
    hy = latest(f.get("hy_oas"))
    vix = latest(f.get("vix"))
    dgs2 = latest(f.get("treasury_2y"))

    inflation_parts = []
    for value in (core_cpi_3m, core_pce_3m):
        if value is not None:
            inflation_parts.append(clamp((3.0 - value) / 3.0))
    inflation = sum(inflation_parts) / len(inflation_parts) if inflation_parts else 0.0

    employment_parts = []
    if unemployment is not None:
        employment_parts.append(clamp((unemployment - 4.1) / 1.2))
    if payroll_delta is not None:
        employment_parts.append(clamp((-payroll_delta) / 500.0))
    if claims is not None:
        employment_parts.append(clamp((claims - 230000) / 100000))
    employment = sum(employment_parts) / len(employment_parts) if employment_parts else 0.0

    growth_parts = []
    for value in (retail_3m, indpro_3m):
        if value is not None:
            growth_parts.append(clamp((1.5 - value) / 5.0))
    growth = sum(growth_parts) / len(growth_parts) if growth_parts else 0.0

    financial_parts = []
    if nfci is not None:
        financial_parts.append(clamp(nfci / 1.5))
    if hy is not None:
        financial_parts.append(clamp((hy - 4.0) / 3.0))
    if vix is not None:
        financial_parts.append(clamp((vix - 20.0) / 20.0))
    if dgs2 is not None:
        financial_parts.append(clamp((4.0 - dgs2) / 2.0))
    financial = sum(financial_parts) / len(financial_parts) if financial_parts else 0.0

    return {
        "inflation": round(inflation, 5),
        "employment": round(employment, 5),
        "growth": round(growth, 5),
        "financial": round(financial, 5),
    }
