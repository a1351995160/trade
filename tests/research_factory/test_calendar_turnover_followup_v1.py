import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.turnover_regime_signals_v1 import LOW,LOW20,chosen as turnover_chosen
from chanlun_trader.research_factory.calendar_recovery_signals_v1 import NAME,chosen
from chanlun_trader.research_factory.technical_train_signals_v1 import transform
from chanlun_trader.research_factory.train_search_batch_v1 import contract


def fixture():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-07-01',periods=95)]
    i=max(i for i in range(65,94) if days[i]//100!=days[i+1]//100)
    c=100+.1*np.arange(95);c[i-5:i+1]=[112,111,110,109,108,109]
    return pd.DataFrame({'date':days,'open':c,'high':c+1,'low':c-1,'close':c}),days,i


def test_calendar_score_by_hand_and_no_terminal_month_guess():
    f,days,i=fixture();out=chosen(f,NAME,days)
    sigma=f.close.pct_change(fill_method=None).iloc[i-19:i+1].std(ddof=0)
    assert out.iloc[i]==pytest.approx(-1/(1+sigma))
    assert out.iloc[i]<0
    assert chosen(f.iloc[:i+1],NAME,days[:i+1]).iloc[-1]==1
    pd.testing.assert_series_equal(out.iloc[:i+1],chosen(f.iloc[:i+1],NAME,days))
    scaled=f.copy();scaled[['open','high','low','close']]*=100
    np.testing.assert_allclose(out,chosen(scaled,NAME,days),equal_nan=True)
    f.loc[i,'close']=107
    assert chosen(f,NAME,days).iloc[i]==1


def test_calendar_missing_reset_late_and_exact_no_market_gate():
    f,days,i=fixture()
    rows=f.rename(columns={k:'signal_'+k for k in ('open','high','low','close')}).rename(columns={'date':'timestamp'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=transform(rows,days,NAME)
    assert out.value.iloc[i]<0 and 'market_gate' not in contract(NAME)
    assert not transform(rows.drop(index=i-5),days,NAME).computable.iloc[i]
    rows.loc[i-10,'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    assert transform(rows,days,NAME).effective_available_at.iloc[i].year==2028


def test_low_turnover_entry_is_unchanged_only_explicit_holding_changes():
    f,days,_=fixture();f['turnover_rank']=.2
    pd.testing.assert_series_equal(turnover_chosen(f,LOW,days),turnover_chosen(f,LOW20,days))
    rows=f.rename(columns={k:'signal_'+k for k in ('open','high','low','close')}).rename(columns={'date':'timestamp'})
    rows=rows.assign(symbol='000001.SZ',effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'),market_median=.01,market_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    a=transform(rows,days,LOW);b=transform(rows,days,LOW20)
    for field in ('value','computable','effective_available_at'):pd.testing.assert_series_equal(a[field],b[field])
    assert contract(LOW)['holding_sessions']==3 and contract(LOW20)['holding_sessions']==20
    assert contract(LOW20)['parent_candidate']==LOW


def test_actual_twenty_session_exit_fees_and_governance(tmp_path):
    from test_return_path_signals_v1 import test_actual_account_fixed_exit_and_budget
    test_actual_account_fixed_exit_and_budget(LOW20,tmp_path)


def test_actual_three_session_exit_fees_and_governance(tmp_path):
    from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget
    test_three_session_account_contract_novelty_and_budget(NAME,tmp_path)
