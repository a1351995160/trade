import pandas as pd
import pytest
from chanlun_trader.research_factory.technical_smoothing_signals_v1 import chosen,trix,atr


def test_triple_ema_recurrence_and_flat():
    c=pd.Series([10.,18.,18.])
    first=11.;second=10.125;third=10.015625
    assert trix(c).iloc[1]==pytest.approx(third/10-1)
    first+= (18-first)/8
    second+=(first-second)/8
    third+=(second-third)/8
    assert trix(c).iloc[2]==pytest.approx(third/10.015625-1)
    assert trix(pd.Series([10.]*50)).iloc[1:].eq(0).all()


def test_sma_crossover_at_one_frozen_boundary():
    frame=pd.DataFrame({'close':[10.]*200+[11.]})
    frame['open']=frame.close;frame['high']=frame.close;frame['low']=frame.close
    result=chosen(frame,'SMA_50_200_CROSS_HOLD_20')
    assert result.iloc[-1]==pytest.approx(1-(501/50)/(2001/200))
    assert not result.iloc[:-1].lt(0).any()


def test_atr_hand_seed_and_recursion():
    frame=pd.DataFrame({'close':[10.]*14+[20.],'open':[10.]*15,'high':[11.]*14+[21.],'low':[9.]*15})
    result=atr(frame)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13]==2
    assert result.iloc[14]==pytest.approx((26+12)/14)


def test_hammer_and_soldiers_hand_events():
    f=pd.DataFrame({'open':[16.,15.,14.,13.,12.,11.,9.], 'close':[15.,14.,13.,12.,11.,10.,10.]})
    f['high']=f[['open','close']].max(axis=1)+.1;f['low']=f[['open','close']].min(axis=1)-.1
    f.loc[6,'low']=6.
    assert chosen(f,'HAMMER_DOWN_5_HOLD_20').iloc[-1]==pytest.approx(-3/4.1)
    f=pd.DataFrame({'open':[10.,11.,12.],'close':[12.,13.,14.], 'high':[12.1,13.1,14.1],'low':[9.9,10.9,11.9]})
    assert chosen(f,'THREE_SOLDIERS_HOLD_20').iloc[-1]==pytest.approx(1-14/12)
