import sys
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))


@pytest.mark.parametrize('name',['WEEKLY_VOLUME_STABILITY_TREND_HOLD_20','WEEKLY_VOLUME_STABILITY_DOWNSIDE_HOLD_20'])
def test_actual_prepare_passes_raw_volume_and_separate_signal_prices(monkeypatch,tmp_path,name):
    import run_train_search_batch_v1 as batch
    import run_baostock_account_v1 as source
    import chanlun_trader.research_factory.train_search_batch_v1 as strategy
    day=20230106;symbol='000001.SZ'
    daily=pd.DataFrame({'symbol':[symbol],'date':[day],'open':[10.],'high':[11.],'low':[9.],'close':[10.],'volume':[12345.]})
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
        assert 'volume' in rows
        assert rows.volume.tolist()==[12345.]
        assert rows.close.tolist()==[10.] and rows.signal_close.tolist()==[20.]
        raise Checked()
    monkeypatch.setattr(strategy,'transform',transform)
    with pytest.raises(Checked):batch.prepare(name)
