import numpy as np
import pandas as pd
import pytest

from chanlun_trader.research_factory.turnover_regime_signals_v1 import LOW,HIGH,context,chosen


def test_calendar_mean_peer_rank_missing_and_late():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=30)]
    records=[(f'{i:06}.SZ',day,float(i)) for i in range(1,102) for day in days]
    turns=pd.DataFrame(records,columns=['symbol','timestamp','turn'])
    ready=turns[['symbol','timestamp']].assign(effective_available_at=pd.Timestamp('2022-01-01',tz='UTC'))
    out=context(ready,turns,days)
    last=out[out.timestamp==days[-1]].set_index('symbol')
    assert last.loc['000001.SZ','turnover_rank']==pytest.approx(1/101)
    assert last.loc['000101.SZ','turnover_rank']==1
    assert out[out.timestamp==days[18]].turnover_rank.isna().all()
    scaled=turns.copy();scaled.turn*=100
    np.testing.assert_allclose(out.turnover_rank,context(ready,scaled,days).turnover_rank,equal_nan=True)
    missing=turns[~((turns.symbol.isin(['000001.SZ','000002.SZ']))&(turns.timestamp==days[15]))]
    assert context(ready,missing,days).query('timestamp==@days[-1]').turnover_rank.isna().all()
    late=ready.copy();late.loc[(late.symbol=='000101.SZ')&(late.timestamp==days[-1]),'effective_available_at']=pd.Timestamp('2028-01-01',tz='UTC')
    final=context(late,turns,days)
    assert final[final.timestamp==days[-1]].effective_available_at.dt.year.eq(2028).all()
    with pytest.raises(ValueError,match='CONFLICT'):context(ready,pd.concat([turns,turns.iloc[:1]]),days)


@pytest.mark.parametrize('name',[LOW,HIGH])
def test_fixed_week_and_score_prefix(name):
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=51)]
    prices=pd.DataFrame({'date':days[:50],'close':np.linspace(100,125,50),'turnover_rank':.2 if name==LOW else .8})
    if name==HIGH:prices.loc[44:49,'close']=[120,119,118,117,116,117]
    result=chosen(prices,name,days)
    expected=-(prices.close[49]/prices.close[29]-1) if name==LOW else 117/120-1
    assert result.iloc[-1]==pytest.approx(expected)
    assert result.iloc[-1]<0
    pd.testing.assert_series_equal(result.iloc[:45],chosen(prices.iloc[:45],name,days))
    assert chosen(prices,name,days[:50]).iloc[-1]==1
    prices.turnover_rank=.5
    assert chosen(prices,name,days).dropna().eq(1).all()


@pytest.mark.parametrize('name',[LOW,HIGH])
def test_technical_market_missing_and_original_account(name,tmp_path):
    from chanlun_trader.research_factory.technical_train_signals_v1 import transform
    from test_skill_alpha_signals_v1 import test_three_session_account_contract_novelty_and_budget
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2022-01-03',periods=51)]
    c=np.linspace(100,125,50)
    if name==HIGH:c[44:50]=[120,119,118,117,116,117]
    rows=pd.DataFrame({'symbol':'000001.SZ','timestamp':days[:50],'signal_open':c,'signal_high':c+1,'signal_low':c-1,'signal_close':c,
        'turnover_rank':.2 if name==LOW else .8,'effective_available_at':pd.Timestamp('2022-01-01',tz='UTC'),
        'market_median':.01,'market_available_at':pd.Timestamp('2022-01-01',tz='UTC')})
    assert transform(rows,days,name).value.iloc[49]<0
    rows.loc[49,'turnover_rank']=np.nan
    assert not transform(rows,days,name).computable.iloc[49]
    rows.loc[49,'turnover_rank']=.2 if name==LOW else .8
    rows.loc[49,'market_median']=np.nan
    assert not transform(rows,days,name).computable.iloc[49]
    test_three_session_account_contract_novelty_and_budget(name,tmp_path)
