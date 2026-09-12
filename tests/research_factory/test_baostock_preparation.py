"""真实文件序列化形态的合成准备链，不访问任何行情或旧结果。"""
import importlib
import json
from pathlib import Path
import pandas as pd
from test_degraded_execution_v2 import degraded
from test_baostock_price_views_v1 import DAYS


def test_prepare_writes_raw_features_and_new_paths_without_outcomes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    module = importlib.import_module('prepare_baostock_account_v1')
    monkeypatch.setattr(module,'ROOT',tmp_path/'output')
    monkeypatch.setattr(module,'BASE',tmp_path/'base')
    monkeypatch.setattr(module,'active',lambda:None)
    import chanlun_trader.synthetic_batch_resources as resources
    monkeypatch.setattr(resources,'worker_resource_handshake',lambda:None)
    root, base = module.ROOT,module.BASE
    bundle = degraded()
    pre = DAYS[:6]
    days = pre+bundle.calendar
    symbols = sorted(bundle.daily.symbol.unique())
    state = bundle.states.copy()
    warm = pd.concat([state[state.trade_date == str(bundle.calendar[0])].assign(trade_date=str(d))
                     for d in pre],ignore_index=True)
    state = pd.concat([warm,state],ignore_index=True)
    state_path = base/'materialized-v3/HISTORICAL_POOL_AND_STATE.parquet'
    state_path.parent.mkdir(parents=True)
    state.to_parquet(state_path,index=False)
    module.save(base/'materialized-v3/CALENDAR.json',{'sessions':days})
    module.save(root/'READ_PLAN.json',{'symbols':symbols,'files':{str(state_path):module.sha(state_path)}})
    module.save(root/'NOVELTY.json',{'decision':{'allowed':True}})
    module.save(root/'HFQ_IPO_PREFIX_RULE_V1.json',{'synthetic':True})
    module.save(root/'quality/batch-0.json',{'passed':True})
    for symbol in symbols:
        raw = []
        for i, day in enumerate(days):
            price = 10-i*.03
            raw.append({'date':str(pd.Timestamp(str(day)).date()),'code':'sh.'+symbol[:6],
                'adjustflag':'3','open':price,'high':price+.1,'low':price-.1,'close':price,
                'preclose':price+.03,'volume':1000000,'amount':price*1000000,
                'tradestatus':'1','isST':'0'})
        hfq = [{'date':r['date'],'code':r['code'],'adjustflag':'1','close':r['close']*7} for r in raw]
        for flag,rows in [('3',raw),('1',hfq)]:
            path = root/'responses'/symbol/(flag+'.json')
            module.save(path,{'rows':rows})
            module.save(path.with_name(flag+'.access.json'),{'sha256':module.sha(path)})

    def load_synthetic():
        bundle.daily = pd.read_parquet(root/'DAILY.parquet')
        bundle.states = pd.read_parquet(root/'STATES.parquet')
        bundle.ready_factors = pd.read_parquet(root/'FEATURES.parquet')
        bundle.calendar = days
        bundle.coverage = []
        bundle.access = []
        return bundle

    monkeypatch.setattr(module,'load_bundle',load_synthetic)
    module.prepare()
    paths = json.loads((root/'CANDIDATE_PATHS.json').read_text())
    assert paths[0]['signal_session'] == 20220801
    assert paths[0]['entry_date'] == 20220802 and paths[0]['exit_date'] == 20220808
    assert len(paths) == 21
    assert bundle.daily.open.max() <= 10
    assert bundle.ready_factors.signal_version.str.contains('HFQ').all()
    ready = json.loads((root/'INPUT_READY.json').read_text())
    assert ready['status'] == 'NOT_READY'  # 小样本不得降低30路径/20开仓日门槛。
    assert not (root/'result.json').exists()
