from __future__ import annotations

FRED_SERIES = {
    "DGS2": "treasury_2y",
    "DGS10": "treasury_10y",
    "DFF": "effr_fred",
    "DFEDTARU": "target_upper",
    "DFEDTARL": "target_lower",
    "SOFR": "sofr_fred",
    "CPIAUCSL": "cpi",
    "CPILFESL": "core_cpi",
    "PCEPI": "pce",
    "PCEPILFE": "core_pce",
    "UNRATE": "unemployment_rate",
    "USREC": "recession_indicator",
    "PAYEMS": "nonfarm_payrolls",
    "AHETPI": "average_hourly_earnings",
    "ICSA": "initial_claims",
    "JTSJOL": "job_openings",
    "INDPRO": "industrial_production",
    "RSAFS": "retail_sales",
    "NFCI": "nfci",
    "BAMLH0A0HYM2": "hy_oas",
    "VIXCLS": "vix",
    "M2SL": "m2",
    "DFII5": "real_yield_5y",
    "DFII10": "real_yield_10y",
    "DFII20": "real_yield_20y",
    "T10YIE": "breakeven_10y",
    "ECBDFR": "ecb_deposit_rate",
    "IRSTCI01JPM156N": "japan_overnight_rate",
}

NYFED_ENDPOINTS = {
    # EFFR는 무담보(unsecured) 연방기금시장 금리
    "effr": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/30.json",
    # SOFR는 담보부(secured) 금리
    "sofr": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/30.json",
}

FED_ENDPOINTS = {
    "press_rss": "https://www.federalreserve.gov/feeds/press_all.xml",
    "speeches_rss": "https://www.federalreserve.gov/feeds/speeches.xml",
    "fomc_calendar": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    # 2026-07 현재 최신 공식 SEP. 다음 단계에서 RSS 기반 자동 탐색으로 전환.
    "sep": "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260617.htm",
}

ZQ_MONTH_CODES = "FGHJKMNQUVXZ"
SOFR_ROOTS = ("SR1", "SR3")

BASE_WEIGHTS = {
    "next_meeting": {
        # Policy decisions are serially persistent.  The immediately preceding
        # policy move is therefore an explicit feature instead of being hidden
        # inside a generic macro score.  Market pricing remains important, but
        # it no longer dominates the auxiliary model.
        "policy_inertia": 0.50,
        "market": 0.21,
        "inflation": 0.11,
        "employment": 0.07,
        "growth": 0.04,
        "financial": 0.04,
        "fed_text": 0.03,
    },
    "medium": {
        "policy_inertia": 0.30,
        "market": 0.25,
        "inflation": 0.17,
        "employment": 0.10,
        "growth": 0.08,
        "financial": 0.05,
        "fed_text": 0.05,
    },
}

MIN_SNAPSHOTS_FOR_OPTIMIZATION = 40
