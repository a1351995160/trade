import pandas as pd
import pytest
from chanlun_trader.research_factory.technical_pattern_signals_v1 import bullish_engulfing


def test_declining_body_engulfing_not_shadow_only():
    frame=pd.DataFrame({'open':[16,15,14,13,12,11,9], 'close':[15,14,13,12,11,10,12]},dtype=float)
    assert bullish_engulfing(frame).iloc[-1]==pytest.approx(-3/12)
    frame.loc[6,'open']=10.5
    assert bullish_engulfing(frame).iloc[-1]>0
    frame.loc[6,'open']=9
    frame.loc[0,'close']=9
    assert bullish_engulfing(frame).iloc[-1]>0
