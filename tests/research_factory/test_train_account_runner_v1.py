"""真实原引擎的合成账户链路，固定持有及公司行动集成。"""
from types import SimpleNamespace
from dataclasses import replace
import pandas as pd
import pytest

from chanlun_trader.research_factory.train_account_runner_v1 import run_fixed_reference
from chanlun_trader.data.tdx.windowed_actions_v1 import WindowedCorporateActionDatasetV1


def fixture():
    days=[20220801,20220802,20220803,20220804,20220805,20220808,20220809,20220810]
    daily=[];states=[];factors=[]
    for index,day in enumerate(days):
        price=10-index*.05
        for symbol in ['600000.SH','600001.SH','600002.SH']:
            visible=pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=15,microseconds=1)
            daily.append(dict(symbol=symbol,date=day,open=price,high=price+.1,low=price-.1,close=price,
                volume=1000000,amount=10000000,prev_close=10 if index==0 else 10-(index-1)*.05,
                modeled_available_at=visible,available_at=None))
            states.append(dict(symbol=symbol,trade_date=str(day),available_at=str(visible.normalize()+pd.Timedelta(hours=8)),
                universe_member=True,eligibility_status='ELIGIBLE',st_status='NORMAL',suspension_status='TRADING',
                board='MAIN',listed=True,delisted=False))
            factors.append(dict(symbol=symbol,timestamp=day,value=-.05,effective_available_at=visible))
    actions=WindowedCorporateActionDatasetV1('SYNTHETIC','WindowedCorporateActionDatasetV1',20220722,20240731,(),
        'a'*64,('600000.SH','600001.SH','600002.SH'),'SYNTHETIC')
    return SimpleNamespace(daily=pd.DataFrame(daily),states=pd.DataFrame(states),factors=pd.DataFrame(factors),
        calendar=days,actions=actions,input_identity='a'*64,pool_identity='b'*64,factor_identity='c'*64,
        calendar_identity='d'*64,contract_identity='e'*64)


def test_original_broker_account_lot_exit_and_no_same_bar_fill():
    result=run_fixed_reference(fixture(),('SYNTHETIC',False))
    buys=[t for t in result['trades'] if t['side']=='BUY'];sells=[t for t in result['trades'] if t['side']=='SELL']
    assert len(buys)>=3 and len(sells)>=3
    assert all(t['fill_time'].strftime('%Y%m%d')=='20220802' for t in buys[:3])
    assert all(t['fill_time'].strftime('%Y%m%d')=='20220808' for t in sells[:3])
    assert result['rankings'][0]['top3'][0]['symbol']=='600000.SH'
    assert result['daily_account'][-1]['available_cash']>=0
    assert result['daily_account'][-1]['cash_receivable']==0
    assert result['final_account_checkpoint']['state']['total_fees']>0


def test_unknown_state_fails_instead_of_deleting_symbol():
    b=fixture();b.states.loc[0,'st_status']='UNKNOWN'
    with pytest.raises(ValueError,match='UNKNOWN'):run_fixed_reference(b,('SYNTHETIC',False))


def test_late_factor_time_cannot_generate_early_signal():
    b=fixture();b.factors['effective_available_at']=pd.Timestamp('2024-07-31 15:31',tz='Asia/Shanghai')
    r=run_fixed_reference(b,('SYNTHETIC',False))
    assert not r['signals'] and not r['trades']


def test_unknown_original_time_remains_unknown_but_modeled_daily_time_is_used():
    b=fixture();b.daily['available_at']='UNKNOWN'
    result=run_fixed_reference(b,('SYNTHETIC',False))
    assert result['trades'][0]['fill_time'].strftime('%Y%m%d')=='20220802'
    assert b.daily.available_at.eq('UNKNOWN').all()


def test_engine_receivable_is_part_of_equity_before_payment():
    b=fixture();event=dict(event_id='div',symbol='600000.SH',effective_date=20220803,event_type='CASH_DIVIDEND',
        record_date=20220802,payment_date=20220805,terms={'cash_per_share':1,'tax_rule':{'kind':'EXPLICIT_NET','source':'SYNTHETIC'}},
        units='CNY_PER_SHARE',source='SYNTHETIC',source_published_at=None)
    b.actions=replace(b.actions,events=(event,))
    r=run_fixed_reference(b,('SYNTHETIC',False));rows={x['date']:x for x in r['daily_holdings']}
    quantity=r['trades'][0]['quantity']
    assert rows[20220803]['receivables']['div']==quantity
    assert rows[20220803]['cash']==rows[20220802]['cash']
    assert rows[20220805]['cash']==pytest.approx(rows[20220803]['cash']+quantity)


def test_split_replaces_pending_exit_with_converted_quantity():
    b=fixture();event=dict(event_id='split',symbol='600000.SH',effective_date=20220808,event_type='SPLIT',
        share_credit_date=20220808,tradable_date=20220808,terms={'ratio_numerator':2,'ratio_denominator':1},
        units='NEW_SHARES_PER_OLD_SHARE',source='SYNTHETIC',source_published_at=None)
    b.actions=replace(b.actions,events=(event,))
    mask=(b.daily.symbol=='600000.SH')&(b.daily.date>=20220808)
    b.daily.loc[mask,['open','high','low','close','prev_close']]/=2
    r=run_fixed_reference(b,('SYNTHETIC',False))
    buy=next(t for t in r['trades'] if t['side']=='BUY' and t['symbol']=='600000.SH')
    sale=next(t for t in r['trades'] if t['side']=='SELL' and t['symbol']=='600000.SH')
    assert sale['quantity']==2*buy['quantity']
    assert sale['fill_time'].strftime('%Y%m%d')=='20220808'
    assert any(o['metadata'].get('corporate_action_replaces_order') for o in r['orders'])
