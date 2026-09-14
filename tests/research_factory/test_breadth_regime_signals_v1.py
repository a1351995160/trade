import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.breadth_regime_signals_v1 import NAMES,context,gate


def test_breadth_hand_calendar_missing_members_and_late_time():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-08-01',periods=25)]
    frame=pd.DataFrame([{'symbol':str(s),'timestamp':day,'value':.01 if s<60 else -.01,
        'effective_available_at':pd.Timestamp(str(day),tz='UTC')} for day in days for s in range(100)])
    out=context(frame,days)
    assert out[out.timestamp==days[19]].breadth20.eq(.6).all()
    assert out[out.timestamp==days[18]].breadth20.isna().all()
    late=frame.copy();late.loc[(late.timestamp==days[1])&(late.symbol=='0'),'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    value=context(late,days);assert value[value.timestamp==days[19]].breadth_available_at.dt.year.eq(2028).all()
    missing=frame.drop(frame[(frame.timestamp==days[5])&(frame.symbol=='0')].index)
    assert context(missing,days).query('timestamp == @days[19]').breadth20.isna().all()
    changed=frame.copy();changed.loc[changed.timestamp==days[-1],'value']=1
    pd.testing.assert_frame_equal(out[out.timestamp<days[-1]].reset_index(drop=True),context(changed,days).query('timestamp < @days[-1]').reset_index(drop=True))
    with pytest.raises(ValueError,match='DUPLICATE'):context(pd.concat([frame,frame.iloc[:1]]),days)


@pytest.mark.parametrize('name',NAMES)
def test_gate_boundary_and_original_account(name,tmp_path):
    a=pd.DataFrame({'breadth':[.5,.7,.7],'breadth5':[.5,.7,.4],'breadth20':[.5,.6,.6],
        'breadth_available_at':[pd.Timestamp('2023-01-01',tz='UTC')]*3})
    event,known=gate(a,name)
    assert known.all() and not event.iloc[0] and event.iloc[1]
    assert bool(event.iloc[2])==(name==NAMES[0])
    a.loc[1,'breadth20']=np.nan;assert not gate(a,name)[1].iloc[1]
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(name,tmp_path)


@pytest.mark.parametrize('name',NAMES)
def test_actual_prepare_price_wiring(monkeypatch,tmp_path,name):
    from test_trend_risk_horizon_signals_v1 import test_actual_prepare_keeps_raw_and_hfq
    test_actual_prepare_keeps_raw_and_hfq(monkeypatch,tmp_path,name)
