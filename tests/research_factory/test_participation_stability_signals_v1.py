import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.participation_stability_signals_v1 import NAMES,STABLE,chosen


@pytest.mark.parametrize('name',NAMES)
def test_hand_formula_units_missing_and_original_account(name,tmp_path):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.1
    p=pd.DataFrame({'date':days,'close':c,'raw_close':10.,'volume':1000.+np.arange(70)})
    i=65;assert pd.Timestamp(str(days[i])).dayofweek==4
    cv=np.std(p.volume.iloc[i-19:i+1],ddof=0)/np.mean(p.volume.iloc[i-19:i+1])
    expected=-1/(1+cv)
    # 上涨路径的下行均方根为0；两个独立公式在此手算例一致。
    assert chosen(p,name,days).iloc[i]==pytest.approx(expected)
    pd.testing.assert_series_equal(chosen(p,name,days),chosen(p.assign(volume=p.volume*100),name,days))
    p.loc[i,'raw_close']=34
    assert chosen(p,name,days).iloc[i]==1
    p.loc[i,'raw_close']=10;p.loc[i-3,'volume']=0
    assert pd.isna(chosen(p,name,days).iloc[i])
    if name!=STABLE:
        p['volume']=1000;p['close']=100
        assert pd.isna(chosen(p,name,days).iloc[i])
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)


def test_downside_ratio_hand_calculation():
    name=NAMES[1];days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.3;c[64]-=1
    p=pd.DataFrame({'date':days,'close':c,'raw_close':10.,'volume':1000.})
    r=p.close.pct_change(fill_method=None).iloc[46:66]
    ratio=np.sqrt(np.mean(np.minimum(r,0)**2))/np.sqrt(np.mean(r**2))
    assert chosen(p,name,days).iloc[65]==pytest.approx(-1/(1+ratio))
