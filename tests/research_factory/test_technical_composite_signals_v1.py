import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.technical_composite_signals_v1 import directional,chosen,FORMULAS


def frame(close):
    c=np.asarray(close,dtype=float)
    return pd.DataFrame({'open':c,'high':c+.1,'low':c-.1,'close':c,'amount':100.})


def test_directional_hand_seed_recursion_and_degenerate():
    f=frame(np.arange(100.,140.))
    adx,plus,minus=directional(f)
    assert adx.iloc[:27].isna().all()
    assert adx.iloc[27:].eq(100).all()
    assert plus.iloc[14]==pytest.approx(100/1.1)
    assert minus.iloc[14:].eq(0).all()
    flat=frame([100.]*40)
    a,p,m=directional(flat)
    assert a.iloc[27:].eq(0).all() and p.iloc[14:].eq(0).all() and m.iloc[14:].eq(0).all()
    flat['high']=100.;flat['low']=100.
    assert directional(flat)[0].iloc[27:].eq(0).all()


def test_directional_equal_outside_moves_are_not_double_counted():
    f=pd.DataFrame({'high':100.+np.arange(40),'low':100.-np.arange(40),'close':100.})
    a,p,m=directional(f)
    assert a.iloc[27:].eq(0).all() and p.iloc[14:].eq(0).all() and m.iloc[14:].eq(0).all()


def test_adx_recursion_after_opposing_movement_by_hand():
    f=frame(np.arange(100.,129.))
    f.loc[28,['high','low','close']]=[127.,125.,126.]
    a,p,m=directional(f)
    assert p.iloc[28]==pytest.approx(100*13/(13*1.1+2))
    assert m.iloc[28]==pytest.approx(100*1.9/(13*1.1+2))
    assert a.iloc[28]==pytest.approx((1300+100*(13-1.9)/(13+1.9))/14)


@pytest.mark.parametrize('name',FORMULAS)
def test_composites_have_prefix_invariance_and_no_flat_event(name):
    f=frame(100+np.arange(230)*.1+np.sin(np.arange(230)/4))
    pd.testing.assert_series_equal(chosen(f,name).iloc[:200],chosen(f.iloc[:200],name))
    assert not chosen(frame([100.]*200),name).lt(0).any()


def test_trend_pullback_requires_touch_and_recovery():
    f=frame(100+np.arange(200)*.2)
    f.loc[198,'low']=137.5
    result=chosen(f,'ADX_EMA_PULLBACK_HOLD_20')
    assert result.iloc[-1]<0
    f.loc[198,'low']=f.loc[198,'close']-.1
    assert chosen(f,'ADX_EMA_PULLBACK_HOLD_20').iloc[-1]==1
    f.loc[198,'low']=137.5;f.loc[199,['open','high','low','close']]=[139.,139.1,138.9,139.]
    assert chosen(f,'ADX_EMA_PULLBACK_HOLD_20').iloc[-1]==1


def test_contraction_breakout_requires_amount_confirmation():
    c=100+np.arange(200)*.03+np.sin(np.arange(200))
    c[-21:-1]=110.+np.arange(20)*.001;c[-1]=112.
    f=frame(c);f.loc[199,'amount']=200.
    assert chosen(f,'SQUEEZE_TREND_TURNOVER_HOLD_20').iloc[-1]<0
    f.loc[199,'amount']=149.
    assert chosen(f,'SQUEEZE_TREND_TURNOVER_HOLD_20').iloc[-1]==1
    f.loc[199,'amount']=150.
    assert chosen(f,'SQUEEZE_TREND_TURNOVER_HOLD_20').iloc[-1]<0
