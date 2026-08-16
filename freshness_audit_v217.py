from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'public'/'data'

def read(name):
    try:
        x=json.loads((DATA/name).read_text(encoding='utf-8'))
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def main():
    latest=read('latest.json'); status=read('source_status.json')
    sources=status.get('sources') if isinstance(status.get('sources'),list) else []
    bad=[x for x in sources if isinstance(x,dict) and not x.get('ok')]
    stale=[x for x in sources if isinstance(x,dict) and x.get('stale')]
    market=((latest.get('freshness') or {}).get('items') or [])
    payload={
      'schema_version':'1.0.0','patch_version':'V217-additive-freshness-contract',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'engine_generated_at_utc':latest.get('generated_at_utc'),
      'compatibility':{'existing_keys_removed':False,'existing_field_semantics_changed':False,'new_network_calls':0,'model_formulas_changed':False},
      'source_summary':{'total':len(sources),'failed':len(bad),'stale':len(stale)},
      'market_summary':{'rows':len(market),'live':sum(x.get('status')=='LIVE' for x in market),'cache':sum(x.get('status')=='CACHE' for x in market),'unavailable':sum(x.get('status')=='UNAVAILABLE' for x in market)},
      'failed_sources':bad,'stale_sources':stale,
    }
    (DATA/'freshness_status.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source_summary':payload['source_summary'],'market_summary':payload['market_summary']}),flush=True)
if __name__=='__main__': main()
