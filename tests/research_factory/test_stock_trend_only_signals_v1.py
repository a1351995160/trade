import numpy as np
import pandas as pd
from chanlun_trader.research_factory.stock_trend_only_signals_v1 import NAME


def test_no_market_input_required_and_same_stock_formula(tmp_path,monkeypatch):
    from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    from chanlun_trader.research_factory.trend_risk_horizon_signals_v1 import LOW_VOL,chosen
    from chanlun_trader.research_factory.train_search_batch_v1 import contract
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=70)]
    c=100+np.arange(70)*.1
    frame=pd.DataFrame({'symbol':'000001.SZ','timestamp':days,'signal_open':c,'signal_close':c,'signal_high':c+1,'signal_low':c-1,'close':10.,'effective_available_at':pd.Timestamp('2022-06-01',tz='UTC')})
    actual=transform(frame,days,NAME)
    prices=pd.DataFrame({'date':days,'close':c,'raw_close':10.})
    assert actual.value.iloc[65]==chosen(prices,LOW_VOL,days).iloc[65]
    assert not contract(NAME).get('market_gate') and not contract(NAME).get('market_context')
    altered=frame.assign(market_median=-1.,market_available_at=pd.Timestamp('2030-01-01',tz='UTC'))
    pd.testing.assert_frame_equal(actual,transform(altered,days,NAME))
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(NAME,tmp_path)
    from test_trend_risk_horizon_signals_v1 import test_actual_prepare_keeps_raw_and_hfq
    test_actual_prepare_keeps_raw_and_hfq(monkeypatch,tmp_path,NAME)
