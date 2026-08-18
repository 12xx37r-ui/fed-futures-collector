from __future__ import annotations
import json, math, random, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

ROOT=Path(__file__).resolve().parent
LATEST=ROOT/"public"/"data"/"latest.json"
STATUS=ROOT/"public"/"data"/"fast_market_refresh_status.json"
TIMEOUT=(3,8)
FULL_GUARD_MINUTES=120
YAHOO_MIN_INTERVAL_SECONDS=0.35

def now_iso(): return datetime.now(timezone.utc).isoformat()
def read(path):
    try:
        x=json.loads(path.read_text(encoding="utf-8")); return x if isinstance(x,dict) else {}
    except Exception:return {}
def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(path)
def n(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except Exception:return None
def market_quote(session,symbol):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    last=None
    for a in range(2):
        try:
            r=session.get(url,params={"range":"1d","interval":"5m","includePrePost":"true","events":"history","_ts":int(time.time())},
                          timeout=TIMEOUT,headers={"Cache-Control":"no-cache","Pragma":"no-cache"})
            if r.status_code==429:
                ra=r.headers.get("Retry-After")
                try: wait=float(ra) if ra is not None else None
                except Exception: wait=None
                time.sleep(min(15.0, wait if wait is not None and wait>=0 else 1.5*(2**a)+random.random()))
                continue
            r.raise_for_status()
            node=((((r.json() or {}).get("chart") or {}).get("result") or [None])[0] or {})
            meta=node.get("meta") or {}
            p=n(meta.get("regularMarketPrice")); ts=meta.get("regularMarketTime")
            if p is None or ts is None: raise ValueError("price/time unavailable")
            return {"symbol":symbol,"price":p,"market_time_utc":datetime.fromtimestamp(int(ts),tz=timezone.utc).isoformat(),
                    "retrieved_at_utc":now_iso(),"source":"Yahoo Finance chart metadata"}
        except Exception as e:
            last=e
            if a<1: time.sleep(1.0+random.random())
    return {"symbol":symbol,"error":f"{type(last).__name__}: {str(last)[:120]}"}

def discover_symbols(payload):
    found=[]
    def walk(x):
        if isinstance(x,dict):
            s=x.get("symbol")
            if isinstance(s,str) and (s=="ZQ=F" or s.startswith("ZQ") or s.startswith("SR1") or s.startswith("SR3")):
                found.append(s)
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(payload)
    uniq=[]
    for s in found:
        if s not in uniq: uniq.append(s)
    zq=[s for s in uniq if s=="ZQ=F"] + [s for s in uniq if s.startswith("ZQ") and s!="ZQ=F"][:3]
    sr=[s for s in uniq if s.startswith("SR1") or s.startswith("SR3")][:3]
    return (zq+sr)[:7]

def parse_dt(v):
    try:
        d=datetime.fromisoformat(str(v or "").replace("Z","+00:00"))
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def main():
    p=read(LATEST)
    if not p: raise SystemExit("public/data/latest.json missing")
    full_dt=parse_dt(p.get("generated_at_utc"))
    if full_dt is not None:
        full_age=(datetime.now(timezone.utc)-full_dt).total_seconds()/60.0
        if 0 <= full_age <= FULL_GUARD_MINUTES:
            skipped={"schema_version":"1.0","generated_at_utc":now_iso(),"status":"SKIP_RECENT_FULL",
                     "reason":"full output is recent","full_generated_at_utc":full_dt.isoformat(),
                     "full_age_minutes":round(full_age,2),"guard_minutes":FULL_GUARD_MINUTES,
                     "network_calls":0}
            write(STATUS,skipped)
            print(json.dumps(skipped,ensure_ascii=False))
            return
    symbols=discover_symbols(p)
    if not symbols: symbols=["ZQ=F"]
    prev=((p.get("fast_market_snapshot") or {}).get("quotes") or {})
    s=requests.Session(); s.headers.update({"User-Agent":"fed-futures-fast-refresh/1.0","Accept":"application/json"})
    attempted=[]
    for i,sym in enumerate(symbols):
        if i: time.sleep(YAHOO_MIN_INTERVAL_SECONDS)
        attempted.append(market_quote(s,sym))
    newer={}
    for q in attempted:
        if "price" not in q: continue
        old=prev.get(q["symbol"]) if isinstance(prev,dict) else None
        old_dt=parse_dt((old or {}).get("market_time_utc")) if isinstance(old,dict) else None
        new_dt=parse_dt(q.get("market_time_utc"))
        if old_dt is None or (new_dt is not None and new_dt > old_dt):
            newer[q["symbol"]]=q
    if not newer:
        print(json.dumps({"status":"NO_CHANGE","symbols_attempted":symbols,"network_calls":len(symbols)}, ensure_ascii=False))
        return
    merged=dict(prev) if isinstance(prev,dict) else {}
    merged.update(newer)
    p["fast_market_snapshot"]={"version":"V230","generated_at_utc":now_iso(),"scope":"nearest ZQ/SOFR only",
        "quotes":merged,"full_engine_recomputed":False,"representative_probabilities_changed":False}
    items=((p.get("freshness") or {}).get("items") or [])
    if isinstance(items,list):
        for item in items:
            if isinstance(item,dict) and item.get("symbol") in newer:
                q=newer[item["symbol"]]
                item["market_time_utc"]=q["market_time_utc"]
                item["fast_refresh_price"]=q["price"]
                item["fast_refreshed_at_utc"]=q["retrieved_at_utc"]
    write(LATEST,p)
    write(STATUS,{"schema_version":"1.0","generated_at_utc":now_iso(),"status":"UPDATED",
                  "symbols_attempted":symbols,"symbols_updated":sorted(newer),
                  "network_calls":len(symbols),"max_symbols_per_run":7,
                  "model_formulas_changed":False,"probabilities_recomputed":False})
if __name__=="__main__": main()
