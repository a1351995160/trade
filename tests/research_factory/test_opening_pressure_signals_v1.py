import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.opening_pressure_signals_v1 import NAMES,FREQUENCY,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform


def fixture():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=50)]
    frame=pd.DataFrame({'date':days,'open':100+np.arange(50)%2,'close':100.,'high':102.,'low':99.,'raw_close':10.})
    return frame,days


@pytest.mark.parametrize('name',NAMES)
def test_joint_same_bar_hand_count_scale_and_affordability(name):
    frame,days=fixture();out=chosen(frame,name,days)
    i=24
    expected=-.5 if name==FREQUENCY else -.5*(1-100/101)
    assert out.iloc[i]==pytest.approx(expected)
    assert out.iloc[:20].isna().all()
    assert out.iloc[23]==1
    scaled=frame.copy();scaled[['open','close','high','low']]*=17
    np.testing.assert_allclose(out,chosen(scaled,name,days),equal_nan=True)
    scaled.loc[i,'raw_close']=34
    assert chosen(scaled,name,days).iloc[i]==1
    scaled.loc[i,'raw_close']=np.nan
    assert pd.isna(chosen(scaled,name,days).iloc[i])
    # 日内为负但没有正隔夜时，不能偷算成NR。
    frame['open']=99.;frame['close']=98.
    frame.loc[1:,'open']=97.
    assert chosen(frame,name,days).iloc[i]==1


@pytest.mark.parametrize('name',NAMES)
def test_integration_missing_late_and_original_account(name,tmp_path):
    f,days=fixture()
    rows=f.rename(columns={k:'signal_'+k for k in ('open','close','high','low')}).rename(columns={'date':'timestamp','raw_close':'close'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'),market_median=.01,market_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=transform(rows,days,name);assert out.value.iloc[24]<0
    assert not transform(rows.drop(index=15),days,name).computable.iloc[24]
    rows.loc[12,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,name).effective_available_at.iloc[24].year==2028
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)
