import numpy as np
import pandas as pd
import pytest
from chanlun_trader.research_factory.etf_grid_preflight_v1 import (
    grid_spacing,state,adjacent_grid,normalize_etf_diagnostic,check_input,
)


def test_spacing_has_correct_dimension_and_floor():
    assert grid_spacing(.08,4)==.025
    assert grid_spacing(.16,4)==.04
    assert grid_spacing(.32,8)==.04
    for atr,c in ((np.nan,4),(.1,0),(-1,4)):
        with pytest.raises(ValueError):grid_spacing(atr,c)


def test_bear_unknown_and_ties_never_disable_sells():
    assert state(3,4,3.5,4)=={'state':'C','buy_slots':0,'sell_enabled':True}
    assert state(4,4,4,4)['state']=='B'
    assert state(4,4.1,4,3.9)['buy_slots']==4
    assert state(np.nan,4,4,4)['sell_enabled'] is True


def test_adjacent_pair_and_dynamic_fourth_level():
    assert adjacent_grid(4,.025,1)=={'buy':3.9,'sell':4.,'gross_for_200':20.}
    assert adjacent_grid(4,.025,4)=={'buy':3.6,'sell':3.7,'gross_for_200':20.}
    assert adjacent_grid(4,.04,4)['buy']==3.36 # 不能仍称0.90P0


def test_etf_units_are_separate_and_spread_cannot_be_invented():
    rows=[{'date':20220801,'open':40.,'high':41.,'low':39.,'close':40.,'amount_encoded':600_000_000.,'volume_encoded':150_000_000}]
    frame=normalize_etf_diagnostic(rows)
    assert frame.close.iloc[0]==4 and rows[0]['close']==40
    result=check_input(frame,[20220801])
    assert result['unit_ratio_median']==1
    assert result['reasons']==['HISTORICAL_BID_ASK_REQUIRED']
    assert result['status']=='NOT_READY' and result['price_exposures']==0
    with pytest.raises(NotImplementedError):check_input(frame,[20220801],historical_spread=.001)


def test_overflow_marker_decodes_without_changing_normal_records():
    from chanlun_trader.research_factory.etf_grid_preflight_v1 import decode_day_volume
    assert decode_day_volume(47543511,0xc3640022)==4754351134
    assert decode_day_volume(46470564,0xc3640043)==4647056467
    for reserved in (0,0x10000,3139,0xc3650022):
        assert decode_day_volume(12345,reserved)==12345
    for raw,reserved in ((100,0xc3640000),(47543511,0xc3640064),(-1,0)):
        with pytest.raises(ValueError):decode_day_volume(raw,reserved)


def test_model_approval_does_not_grant_execution_or_vendor_evidence():
    from chanlun_trader.research_factory.etf_grid_preflight_v1 import check_model_input
    frame=normalize_etf_diagnostic([dict(date=20220801,open=40,high=41,low=39,close=40,amount_encoded=600000000,volume_encoded=150000000)])
    with pytest.raises(PermissionError):check_model_input(frame,[20220801])
    result=check_model_input(frame,[20220801],spread_model_approved=True)
    assert result['status']=='MODEL_BASIC_INPUT_CHECKS_PASSED'
    assert result['execution_authorized'] is False and result['slippage_per_side']==.001
    frame.loc[0,'volume_shares']=1
    assert check_model_input(frame,[20220801],spread_model_approved=True)['status']=='NOT_READY'
