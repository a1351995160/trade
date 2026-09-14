import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.technical_followup_signals_v1 import cci,kama,chosen


def test_cci_hand_zero_deviation_and_transition():
    close=np.arange(1.,22.)
    prices=pd.DataFrame({'high':close,'low':close,'close':close})
    values=cci(prices)
    assert values.iloc[:19].isna().all()
    assert values.iloc[19]==pytest.approx((20-10.5)/(.015*5))
    flat=prices*0+10
    assert cci(flat).isna().all()
    assert not chosen(flat,'CCI_20_TREND_HOLD_20').lt(0).any()


def test_kama_seed_recurrence_and_flat():
    close=pd.Series(np.arange(1.,13.))
    values=kama(close)
    assert values.iloc[:9].isna().all()
    assert values.iloc[9]==5.5
    assert values.iloc[10]==pytest.approx(5.5+(2/3)**2*(11-5.5))
    assert kama(pd.Series([10.]*40)).iloc[9:].eq(10).all()


@pytest.mark.parametrize('name',['CCI_20_TREND_HOLD_20','KAMA_10_CROSS_HOLD_20'])
def test_followup_prefix_invariance(name):
    close=10+np.sin(np.arange(90)/3)
    prices=pd.DataFrame({'high':close+.1,'low':close-.1,'close':close})
    full=chosen(prices,name)
    for stop in [25,40,65]:
        pd.testing.assert_series_equal(full.iloc[:stop],chosen(prices.iloc[:stop],name))
