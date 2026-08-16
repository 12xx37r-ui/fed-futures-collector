import collector


def test_v218_yahoo_url_has_cache_buster_and_no_cache_headers(monkeypatch):
    seen={}
    class Resp:
        def json(self):
            return {'chart':{'result':[{'meta':{'regularMarketPrice':96.5,'regularMarketTime':1700000000},'timestamp':[1699900000],'indicators':{'quote':[{'close':[96.4]}]}}]}}
    def fake_request(url, official=False, timeout=None, retries=1):
        seen['url']=url; seen['retries']=retries
        return Resp()
    monkeypatch.setattr(collector,'request',fake_request)
    out=collector.yahoo_chart('ZQ=F','5d')
    assert '&_ts=' in seen['url']
    assert seen['retries']==2
    assert out['refetch_policy']=='network_each_workflow_no_cache'
    assert collector.HEADERS['Cache-Control'].startswith('no-cache')
