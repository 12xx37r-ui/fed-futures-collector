from __future__ import annotations

FRED_SERIES = {
    "DGS2": "treasury_2y",
    "DFF": "effective_fed_funds_rate",
    "SOFR": "sofr",
    "CPIAUCSL": "cpi",
    "CPILFESL": "core_cpi",
    "PCEPI": "pce",
    "PCEPILFE": "core_pce",
    "UNRATE": "unemployment_rate",
    "PAYEMS": "nonfarm_payrolls",
    "AHETPI": "average_hourly_earnings",
    "ICSA": "initial_claims",
    "NFCI": "nfci",
    "BAMLH0A0HYM2": "hy_oas",
}

YAHOO_SYMBOLS = {
    "zq_continuous": "ZQ=F",
    "treasury_2y_proxy": "^IRX",
}

NYFED_ENDPOINTS = {
    "effr": "https://markets.newyorkfed.org/api/rates/secured/effr/last/20.json",
    "sofr": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/20.json",
}

FED_ENDPOINTS = {
    "press_rss": "https://www.federalreserve.gov/feeds/press_all.xml",
    "speeches_rss": "https://www.federalreserve.gov/feeds/speeches.xml",
    "fomc_calendar": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
}
