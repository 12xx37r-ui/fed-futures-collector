from __future__ import annotations

FRED_SERIES = {
    "DGS2": "treasury_2y",
    "DGS10": "treasury_10y",
    "DFF": "effr_fred",
    "SOFR": "sofr_fred",
    "CPIAUCSL": "cpi",
    "CPILFESL": "core_cpi",
    "PCEPI": "pce",
    "PCEPILFE": "core_pce",
    "UNRATE": "unemployment_rate",
    "PAYEMS": "nonfarm_payrolls",
    "AHETPI": "average_hourly_earnings",
    "ICSA": "initial_claims",
    "JTSJOL": "job_openings",
    "INDPRO": "industrial_production",
    "RSAFS": "retail_sales",
    "NFCI": "nfci",
    "BAMLH0A0HYM2": "hy_oas",
    "VIXCLS": "vix",
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
        "market": 0.50,
        "inflation": 0.18,
        "employment": 0.12,
        "growth": 0.07,
        "financial": 0.05,
        "fed_text": 0.08,
    },
    "medium": {
        "market": 0.35,
        "inflation": 0.22,
        "employment": 0.15,
        "growth": 0.12,
        "financial": 0.08,
        "fed_text": 0.08,
    },
}

MIN_SNAPSHOTS_FOR_OPTIMIZATION = 40
