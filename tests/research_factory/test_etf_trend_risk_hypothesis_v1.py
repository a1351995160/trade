import pytest
from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import decision


def call(**changes):
    values=dict(momentum=.1,annual_volatility=.2,close=4,peak_close=4.1,atr=.1,
                held_weight=0,equity=10000,month_end=True)
    values.update(changes)
    return decision(**values)


def test_entry_is_risk_sized_and_capped():
    assert call()['target_weight']==pytest.approx(.4)
    assert call(annual_volatility=.01)['target_weight']==.9


def test_exit_existing_holding_not_just_disable_buying():
    assert call(momentum=-.1,held_weight=.4)['target_weight']==0
    assert call(close=3.8,held_weight=.4,month_end=False)['reason']=='EXIT_TRAILING_CLOSE'


def test_hold_and_unknown_are_not_zero_targets():
    assert call(month_end=False)['target_weight'] is None
    assert call(momentum=float('nan'))['target_weight'] is None
    assert call(annual_volatility=0)['target_weight'] is None


def test_same_risk_budget_benchmark_is_not_same_realized_exposure():
    assert call(benchmark=True)['target_weight']==call()['target_weight']
    assert call(momentum=-.1)['target_weight']==0
    assert call(momentum=-.1,benchmark=True)['target_weight']>0


def test_resize_filter_does_not_block_exit():
    assert call(held_weight=.45)['reason']=='SMALL_RESIZE_COST_FILTER'
    assert call(momentum=0,held_weight=.1)['target_weight']==0
    assert call(held_weight=.1)['reason']=='NO_ADD_TO_EXISTING_POSITION'


def test_three_event_rules_and_forced_exits():
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import FAMILY,batch_decision
    row=dict(close=4,ma60=3.8,ret2=-.1,sigma=.02,atr=.1,ma5=4.1,
        sigma_median=.03,prior_high20=3.9,prior_low10=3.7)
    cal=dict(month_end=False,third_last=True,month_ordinal=20,month=202207)
    for name in FAMILY[1:]:
        assert batch_decision(name,row,cal,None,held_weight=0,equity=10000)['target_weight']>0
    h=dict(entry_price=4,entry_atr=.1,peak=4.1,sessions=5,entry_month=202207)
    assert batch_decision(FAMILY[1],row,cal,h,held_weight=.4,equity=10000)['target_weight']==0
    assert batch_decision(FAMILY[2],row,cal,{**h,'sessions':20},held_weight=.4,equity=10000)['target_weight']==0
    assert batch_decision(FAMILY[3],row,{**cal,'month':202208,'month_ordinal':3},h,held_weight=.4,equity=10000)['target_weight']==0


def test_calendar_end_not_invented_and_features_are_causal():
    import numpy as np
    import pandas as pd
    from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import calendar_fields,features
    assert calendar_fields([20220727,20220728,20220729,20220801],0)['third_last']
    assert not calendar_fields([20220727,20220728,20220729],0)['third_last']
    close=np.linspace(3,5,125)
    f=pd.DataFrame(dict(close=close,open=close,high=close+.1,low=close-.1))
    before=features(f);f.loc[124,'close']=100
    pd.testing.assert_frame_equal(before.iloc[:124],features(f).iloc[:124])
