import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.trend_risk_horizon_signals_v1 import NAMES,LOW_VOL,chosen


@pytest.mark.parametrize('name',NAMES)
def test_hand_price_path_and_account(name,tmp_path):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.3;c[64]-=1
    p=pd.DataFrame({'date':days,'close':c,'raw_close':10.})
    if name==LOW_VOL:risk=p.close.pct_change(fill_method=None).iloc[46:66].std(ddof=0)
    else:
        w=c[46:66];risk=max(1-w/np.maximum.accumulate(w))
    assert chosen(p,name,days).iloc[65]==pytest.approx(-1/(1+risk))
    p.loc[65,'raw_close']=34;assert chosen(p,name,days).iloc[65]==1
    p.loc[65,'raw_close']=np.nan;assert pd.isna(chosen(p,name,days).iloc[65])
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)


@pytest.mark.parametrize('name',NAMES)
def test_actual_prepare_keeps_raw_and_hfq(monkeypatch,tmp_path,name):
    # 真实prepare回归沿用接线fixture；这两项不依赖volume，直接检验RAW/HFQ隔离。
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import run_train_search_batch_v1 as batch
    import run_baostock_account_v1 as source
    import chanlun_trader.research_factory.train_search_batch_v1 as strategy
    from types import SimpleNamespace
    day=20230106;symbol='000001.SZ'
    daily=pd.DataFrame({'symbol':[symbol],'date':[day],'open':[10.],'high':[11.],'low':[9.],'close':[10.]})
    ready=pd.DataFrame({'symbol':[symbol],'timestamp':[day],'value':[.01],'effective_available_at':[pd.Timestamp('2023-01-06 10:00',tz='UTC')]})
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'bundle',lambda *a,**kw:SimpleNamespace(daily=daily,ready_factors=ready,calendar=[day]))
    monkeypatch.setattr(source,'response_directory',lambda *a:tmp_path)
    monkeypatch.setattr(batch,'sha',lambda *a:'hash')
    def read(path):
        if Path(path).name=='PREREGISTRATION.json':return {'novelty':{name:{'allowed':True}},'inputs':{}}
        if Path(path).name=='INPUT_MANIFEST.json':return {'inputs':{str(tmp_path/'1.json'):'hash'}}
        return {'rows':[{'code':'sz.000001','date':'2023-01-06','close':'20'}]}
    monkeypatch.setattr(batch,'read',read)
    class Checked(Exception):pass
    def transform(rows,*args):
        assert rows.close.tolist()==[10.] and rows.signal_close.tolist()==[20.]
        raise Checked()
    monkeypatch.setattr(strategy,'transform',transform)
    with pytest.raises(Checked):batch.prepare(name)
