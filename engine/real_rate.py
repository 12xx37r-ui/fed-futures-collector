from __future__ import annotations
from dataclasses import dataclass
from math import sqrt
from statistics import mean
from typing import Any


def _obs(series: dict[str, Any] | None) -> list[tuple[str, float]]:
    if not series:
        return []
    out=[]
    for row in series.get("observations", []):
        try:
            v=float(row.get("value"))
            out.append((str(row.get("date")),v))
        except (TypeError,ValueError):
            pass
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo,min(hi,x))


def _predict(values: list[float], y5: float|None, y20: float|None, horizon: int) -> tuple[float, dict[str,float]]:
    cur=values[-1]
    m20=cur-values[-21] if len(values)>21 else 0.0
    m60=cur-values[-61] if len(values)>61 else m20
    anchor=cur
    if y5 is not None and y20 is not None:
        anchor=.55*y5+.45*y20
    elif y5 is not None: anchor=y5
    elif y20 is not None: anchor=y20
    scale=horizon/63.0
    trend=cur+_clamp((.38*m20+.17*m60)*scale,-.75,.75)
    curve=cur+_clamp(.32*(anchor-cur)*scale,-.50,.50)
    meanrev=cur+_clamp(.12*(mean(values[-252:])-cur)*scale,-.35,.35)
    pred=.50*trend+.30*curve+.20*meanrev
    return pred,{"trend":trend,"curve":curve,"mean_reversion":meanrev}


def _walk_forward(values:list[float], horizon:int) -> dict[str,float]:
    errs=[]
    start=max(252,len(values)-1000)
    for i in range(start,len(values)-horizon):
        hist=values[:i+1]
        pred,_=_predict(hist,None,None,horizon)
        errs.append(pred-values[i+horizon])
    if not errs:
        return {"mae":.35,"rmse":.45,"direction_accuracy":50.0,"samples":0}
    mae=sum(abs(e) for e in errs)/len(errs)
    rmse=sqrt(sum(e*e for e in errs)/len(errs))
    good=0
    for i,e in enumerate(errs):
        idx=start+i
        actual=values[idx+horizon]-values[idx]
        pred_change=(actual+e) # pred-current because e=pred-actual_future
        if (actual>=0)==(pred_change>=0): good+=1
    return {"mae":round(mae,4),"rmse":round(rmse,4),"direction_accuracy":round(100*good/len(errs),1),"samples":len(errs)}


def build(raw:dict[str,Any]) -> dict[str,Any]:
    fred=raw.get("fred",{})
    s10=_obs(fred.get("real_yield_10y"))
    if len(s10)<80:
        return {"available":False,"reason":"DFII10 observations insufficient"}
    vals=[v for _,v in s10]
    cur=vals[-1]
    y5=_obs(fred.get("real_yield_5y"))
    y20=_obs(fred.get("real_yield_20y"))
    cur5=y5[-1][1] if y5 else None
    cur20=y20[-1][1] if y20 else None
    p1,c1=_predict(vals,cur5,cur20,21)
    p3,c3=_predict(vals,cur5,cur20,63)
    bt1=_walk_forward(vals,21)
    bt3=_walk_forward(vals,63)
    lo=p3-1.2816*bt3["rmse"]
    hi=p3+1.2816*bt3["rmse"]
    conf=round(_clamp(.45*bt3["direction_accuracy"]+.35*(100-min(100,bt3["rmse"]*100))+.20*100,45,92))
    return {
      "available":True,
      "source":"FRED DFII5/DFII10/DFII20; Federal Reserve H.15",
      "as_of":s10[-1][0],
      "current_pct":round(cur,4),
      "forecast_1m_pct":round(p1,4),
      "forecast_3m_pct":round(p3,4),
      "forecast_change_3m_pctp":round(p3-cur,4),
      "forecast_3m_range_80_pct":[round(lo,4),round(hi,4)],
      "confidence":conf,
      "model":"walk-forward weighted momentum + real-yield curve anchor + mean reversion",
      "components_1m":{k:round(v,4) for k,v in c1.items()},
      "components_3m":{k:round(v,4) for k,v in c3.items()},
      "backtest_1m":bt1,
      "backtest_3m":bt3,
      "schema_version":"1.0.0"
    }
