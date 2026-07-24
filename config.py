from __future__ import annotations

# 무료 소스 우선순위: 공식기관 > 시장 무료경로 > 대체값
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
    "effr": "https://markets.newyorkfed.org/api/rates/secured/effr/last/30.json",
    "sofr": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/30.json",
}

FED_ENDPOINTS = {
    "press_rss": "https://www.federalreserve.gov/feeds/press_all.xml",
    "speeches_rss": "https://www.federalreserve.gov/feeds/speeches.xml",
    "fomc_calendar": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "sep": "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl.htm",
}

# Yahoo 심볼 후보. 실제 성공 여부는 source_status.json에 기록한다.
ZQ_MONTH_CODES = "FGHJKMNQUVXZ"
SOFR_ROOTS = ("SR1", "SR3")
FUTURES_YEARS_AHEAD = 2

# 다음 회의 / 중기 / 장기 기본 앙상블 가중치
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
