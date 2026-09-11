"""授权范围内的确定型会计测试，不读取任何真实行情。"""
import pandas as pd
import pytest

from chanlun_trader.engine.corporate_accounting_v1 import CorporateActionAccountingV1, CorporateActionError
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.signal import Side


def stamp(day, time='09:30'):
    return pd.Timestamp(f'2022-08-{day:02d} {time}',tz='Asia/Shanghai')


def buy(ledger, day=1, quantity=100):
    result, message=ledger.apply_fill(Fill('b'+str(day),'o','S','600000.SH',Side.BUY,quantity,10,stamp(day)))
    assert message=='OK'
    return result.lot_id


def dividend(**updates):
    row=dict(event_id='D1',symbol='600000.SH',effective_date=20220803,event_type='CASH_DIVIDEND',
        record_date=20220802,payment_date=20220805,terms={'cash_per_share':1,'tax_rule':{'kind':'EXPLICIT_NET','source':'SYNTHETIC_TAX_FIXTURE'}},
        units='CNY_PER_SHARE',source='SYNTHETIC',source_published_at=None)
    return {**row,**updates}


def test_dividend_entitlement_receivable_payment_and_restart():
    from chanlun_trader.engine.ledger import PortfolioLedger
    old=PortfolioLedger(1000);buy(old);old.mark_to_market('600000.SH',9,stamp(3))
    assert old.current_equity()==900  # 旧账本没有事件应收款，不能作为本用途完整会计。
    l=CorporateActionAccountingV1(1000,[dividend()],'DATASET')
    buy(l); l.on_close(stamp(2,'15:30')); l.on_open(stamp(3)); l.mark_to_market('600000.SH',9,stamp(3))
    assert l.cash_receivable==100 and l.available_cash()==0 and l.current_equity()==1000
    assert l.snapshot(stamp(3)).equity==1000
    restored=CorporateActionAccountingV1.restore(l.checkpoint(),[dividend()],'DATASET')
    restored.on_open(stamp(3)); restored.on_open(stamp(5)); restored.on_open(stamp(5))
    assert restored.cash_receivable==0 and restored.available_cash()==100 and restored.current_equity()==1000


def test_sold_before_record_has_no_rights_but_sold_after_keeps_rights():
    for sold_before in [True,False]:
        l=CorporateActionAccountingV1(1000,[dividend()],'DATASET'); buy(l)
        sale=Fill('s','o2','S','600000.SH',Side.SELL,100,10,stamp(2))
        if sold_before:l.apply_fill(sale)
        l.on_close(stamp(2,'15:30'))
        if not sold_before:l.apply_fill(Fill('s','o2','S','600000.SH',Side.SELL,100,9,stamp(3)))
        l.on_open(stamp(3))
        assert l.cash_receivable==(0 if sold_before else 100)


def split(**updates):
    return dict(event_id='S1',symbol='600000.SH',effective_date=20220803,event_type='SPLIT',
        share_credit_date=20220803,tradable_date=20220804,terms={'ratio_numerator':2,'ratio_denominator':1},
        units='NEW_SHARES_PER_OLD_SHARE',source='SYNTHETIC',source_published_at=None,**updates)


def test_split_quantity_basis_sellable_fifo_and_restart():
    e=split(); l=CorporateActionAccountingV1(2000,[e],'DATASET'); first=buy(l); second=buy(l,2)
    l.on_open(stamp(3)); l.mark_to_market('600000.SH',5,stamp(3))
    assert l.total_quantity('600000.SH')==400 and l.current_equity()==2000
    assert l.lots[first].quantity==200 and l.lots[first].cost==1000
    assert l.get_position('S','600000.SH').average_cost==5
    assert l.sellable_quantity('S','600000.SH',stamp(3))==0
    l=CorporateActionAccountingV1.restore(l.checkpoint(),[e],'DATASET'); l.on_open(stamp(3))
    assert l.sellable_quantity('S','600000.SH',stamp(4))==400
    _,msg=l.apply_fill(Fill('sell','o','S','600000.SH',Side.SELL,200,5,stamp(4)))
    assert msg=='OK' and l.lots[first].remaining_quantity==0 and l.lots[second].remaining_quantity==200
    consolidation={**split(),'event_type':'CONSOLIDATION','terms':{'ratio_numerator':1,'ratio_denominator':2}}
    reverse=CorporateActionAccountingV1(2000,[consolidation],'D');buy(reverse,quantity=200)
    reverse.on_open(stamp(3));reverse.mark_to_market('600000.SH',20,stamp(3))
    assert reverse.total_quantity('600000.SH')==100 and reverse.current_equity()==2000
    assert next(iter(reverse.lots.values())).cost==2000


@pytest.mark.parametrize('event',[dividend(payment_date=None),dividend(terms={'cash_per_share':1}),
    {**split(),'terms':{'ratio_numerator':1,'ratio_denominator':3}},
    {**split(),'event_type':'RIGHTS'},{**split(),'event_type':'DELISTING'},{**split(),'event_type':'UNKNOWN'}])
def test_unsupported_is_stable_and_does_not_mutate_holdings(event):
    l=CorporateActionAccountingV1(1000,[event],'D'); buy(l)
    with pytest.raises(CorporateActionError):
        l.on_close(stamp(2,'15:30')); l.on_open(stamp(3))
    assert l.total_quantity('600000.SH')==100 and l.cash==0


def test_delayed_share_credit_is_not_available_for_sale():
    event={**split(),'share_credit_date':20220805,'tradable_date':20220808}
    l=CorporateActionAccountingV1(1000,[event],'D');buy(l);l.on_open(stamp(3))
    l.mark_to_market('600000.SH',5,stamp(3))
    assert l.current_equity()==1000 and l.pending_share_credits
    assert l.sellable_quantity('S','600000.SH',stamp(5))==0
    l=CorporateActionAccountingV1.restore(l.checkpoint(),[event],'D');l.on_open(stamp(5))
    assert not l.pending_share_credits
    assert l.sellable_quantity('S','600000.SH',stamp(8))==200


@pytest.mark.parametrize('kind',['BONUS','CAPITALIZATION'])
def test_bonus_locks_only_new_shares_and_preserves_parent_entry(kind):
    event={**split(),'event_type':kind,'record_date':20220802,'share_credit_date':20220805,'tradable_date':20220808}
    l=CorporateActionAccountingV1(1000,[event],'D');parent=buy(l);l.on_close(stamp(2,'15:30'));l.on_open(stamp(3))
    l.mark_to_market('600000.SH',5,stamp(3))
    assert l.current_equity()==1000 and l.total_quantity('600000.SH')==200
    assert l.sellable_quantity('S','600000.SH',stamp(3))==100
    assert sum(lot.cost for lot in l.lots.values())==pytest.approx(1000)
    child=next(lot for lot in l.lots.values() if lot.lot_id!=parent)
    assert child.buy_time==l.lots[parent].buy_time
    assert child.sellable_from==stamp(8)
