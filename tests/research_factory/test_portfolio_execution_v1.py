"""共享真实引擎账本上的组合计划、成交风险与归属校验。"""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from chanlun_trader.engine.fee import ChinaAStockFeeModel
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.order import Order, OrderStatus
from chanlun_trader.engine.risk import RiskConfig
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.daily_plan import account_identity
from chanlun_trader.research_factory.portfolio_execution_v1 import (
    PortfolioMemberV1, PortfolioExecutionPolicyV1, PortfolioExecutionRiskV1,
    build_portfolio_plan, validate_portfolio_plan,
)


CLOSE = pd.Timestamp('2026-09-24T15:05:00+08:00')
OPEN = pd.Timestamp('2026-09-25T09:31:00+08:00')


def policy(**changes):
    return PortfolioExecutionPolicyV1(**{
        'policy_id': 'test', 'members': (
            PortfolioMemberV1(strategy_id='A', rule_identity='a'*64, weight_bps=5000, priority=0),
            PortfolioMemberV1(strategy_id='B', rule_identity='b'*64, weight_bps=5000, priority=1)),
        'purpose': 'ENGINEERING_OBSERVATION', 'max_positions': 4,
        'max_symbol_exposure_bps': 10000, 'max_buy_turnover_bps': 10000,
        'valid_until': '2026-10-01T00:00:00+08:00', **changes})


def admissions():
    return {key: {'allowed': True, 'archive_hash': key.lower()*64,
                  'strategy_qualified': False, 'review_hash': None, 'source_profile': 'TEST'}
            for key in ('A', 'B')}


def buy(ledger, strategy='A', symbol='000001.SZ', quantity=100):
    trade, reason = ledger.apply_fill(Fill('f'+str(len(ledger.trades)), 'o', strategy, symbol,
        Side.BUY, quantity, 10., CLOSE-pd.Timedelta(days=1)))
    assert trade is not None, reason
    return trade


def order(strategy='B', symbol='000002.SZ', quantity=100, side=Side.BUY, **fields):
    return Order(order_id='order', strategy_id=strategy, symbol=symbol, side=side,
                 quantity=quantity, created_at=OPEN, intent_id='intent', signal_id='signal', **fields)


def risk(ledger, *, config=None, orders=(), admission=None, **changes):
    return PortfolioExecutionRiskV1(ledger, config or RiskConfig(max_position_weight=1., max_positions=10),
        policy=policy(**changes), fee_model=ChinaAStockFeeModel(),
        admission_check=lambda key: (admission or admissions())[key],
        orders_provider=lambda: orders,
        prices={'000001.SZ': 10., '000002.SZ': 10.}, session_at=OPEN)


def test_plan_preserves_owner_and_exit_conflict_without_mutating_account():
    ledger = PortfolioLedger(10000.)
    buy(ledger)
    before = account_identity(ledger)
    evidence = admissions()
    evidence['A']['allowed'] = False
    plan = build_portfolio_plan(policy=policy(), decisions=[
        {'strategy_id':'A','symbol':'000001.SZ','side':'SELL'},
        {'strategy_id':'B','symbol':'000001.SZ','side':'BUY'}], ledger=ledger,
        admissions=evidence, input_identity='data', decision_at=CLOSE, next_session=20260925)
    assert plan['intents'][0]['side'] == 'SELL'
    assert plan['excluded'][0]['reason'] == 'EXIT_BUY_CONFLICT'
    assert plan['holdings'][0]['strategy_id'] == 'A'
    assert plan['usage_qualified'] is False
    assert account_identity(ledger) == before
    assert validate_portfolio_plan(plan, policy=policy(), ledger=ledger, input_identity='data',
                                   event_at=OPEN, admissions=evidence)
    ledger.cash += 1
    with pytest.raises(ValueError, match='CONTEXT_CHANGED'):
        validate_portfolio_plan(plan, policy=policy(), ledger=ledger, input_identity='data',
                                event_at=OPEN, admissions=evidence)


def test_no_admitted_and_formal_cannot_promote_engineering_evidence():
    plan = build_portfolio_plan(policy=policy(purpose='FORMAL_OBSERVATION'), decisions=[
        {'strategy_id':'A','symbol':'000001.SZ','side':'BUY'}], ledger=PortfolioLedger(10000.),
        admissions=admissions(), input_identity='data', decision_at=CLOSE, next_session=20260925)
    assert plan['status'] == 'NO_ADMITTED_STRATEGIES'
    assert not plan['intents'] and not plan['usage_qualified']


def test_pending_buys_reserve_cash_and_strategy_allocation():
    ledger = PortfolioLedger(10000.)
    pending = order(strategy='A', symbol='000001.SZ', quantity=400, status=OrderStatus.ACCEPTED)
    adapter = risk(ledger, orders=[pending])
    assert adapter.buy_quantity('A', '000002.SZ', 10., OPEN) == 0
    assert adapter.buy_quantity('B', '000002.SZ', 10., OPEN) == 400
    too_much = order(quantity=600)
    assert not adapter.pre_trade(too_much, OPEN, 10.).ok


def test_actual_price_cumulative_symbol_and_turnover_are_rechecked():
    ledger = PortfolioLedger(10000.)
    adapter = risk(ledger, max_symbol_exposure_bps=3000)
    assert adapter.buy_quantity('B', '000002.SZ', 10., OPEN) == 300
    assert not adapter.pre_trade(order(quantity=300), OPEN, 11.).ok
    buy(ledger, quantity=200)
    assert adapter.buy_quantity('B', '000001.SZ', 10., OPEN) == 100
    limited = risk(ledger, max_buy_turnover_bps=1000)
    assert limited.buy_quantity('B', '000002.SZ', 10., OPEN) == 100


def test_cash_includes_fees_and_cannot_spend_same_open_sale_proceeds():
    ledger = PortfolioLedger(10000.)
    trade = buy(ledger, quantity=900)
    ledger.cash = 995.  # 代表已有成交费用后的唯一账本现金。
    adapter = risk(ledger)
    sold, reason = ledger.apply_fill(Fill('sell','sellorder','A','000001.SZ',Side.SELL,100,10.,OPEN), lot_id=trade.lot_id)
    assert sold is not None, reason
    assert ledger.cash > 1000
    assert adapter.buy_quantity('B', '000002.SZ', 10., OPEN) == 0
    assert not adapter.pre_trade(order(), OPEN, 10.).ok


def test_revocation_stops_buy_but_keeps_owned_exit_and_rejects_other_lot():
    ledger = PortfolioLedger(10000.)
    held = buy(ledger)
    evidence = admissions()
    adapter = risk(ledger, admission=evidence)
    evidence['B']['allowed'] = False
    assert adapter.buy_quantity('B', '000002.SZ', 10., OPEN) == 0
    assert adapter.pre_trade(order(strategy='A', symbol='000001.SZ', side=Side.SELL,
                                   lot_id=held.lot_id), OPEN, 10.).ok
    bad = order(strategy='B', symbol='000001.SZ', side=Side.SELL, lot_id=held.lot_id)
    assert adapter.pre_trade(bad, OPEN, 10.).reason == 'PORTFOLIO_LOT_OWNER_CONFLICT'


def test_pending_position_and_overlap_limits():
    ledger = PortfolioLedger(10000.)
    pending = order(strategy='A', symbol='000001.SZ', status=OrderStatus.ACCEPTED)
    assert risk(ledger, orders=[pending], max_positions=1).buy_quantity('B', '000002.SZ', 10., OPEN) == 0
    assert risk(ledger, orders=[pending], overlap='ONE_STRATEGY_PER_SYMBOL').buy_quantity('B', '000001.SZ', 10., OPEN) == 0


def test_plan_tamper_and_wrong_session_are_rejected():
    ledger = PortfolioLedger(10000.)
    plan = build_portfolio_plan(policy=policy(), decisions=[], ledger=ledger, admissions=admissions(),
        input_identity='data', decision_at=CLOSE, next_session=20260925)
    changed = deepcopy(plan)
    changed['next_session'] = 20260928
    with pytest.raises(ValueError, match='HASH_CONFLICT'):
        validate_portfolio_plan(changed, policy=policy(), ledger=ledger, input_identity='data',
                                event_at=OPEN, admissions=admissions())
    with pytest.raises(ValueError, match='SESSION_CHANGED'):
        validate_portfolio_plan(plan, policy=policy(), ledger=ledger, input_identity='data',
                                event_at=OPEN+pd.Timedelta(days=1), admissions=admissions())


def test_target_weight_survives_plan_and_limits_owned_and_pending_symbol():
    ledger = PortfolioLedger(10000.)
    plan = build_portfolio_plan(policy=policy(), decisions=[
        {'strategy_id':'A','symbol':'000001.SZ','side':'BUY','target_weight':.5}],
        ledger=ledger, admissions=admissions(), input_identity='data', decision_at=CLOSE, next_session=20260925)
    assert plan['intents'][0]['target_weight'] == .5
    adapter = risk(ledger)
    assert adapter.buy_quantity('A', '000001.SZ', 10., OPEN, target_weight=.5) == 200
    buy(ledger, quantity=100)
    assert adapter.buy_quantity('A', '000001.SZ', 10., OPEN, target_weight=.5) == 100
    pending = order(strategy='A', symbol='000001.SZ', quantity=100, status=OrderStatus.ACCEPTED)
    adapter = risk(ledger, orders=[pending])
    assert adapter.buy_quantity('A', '000001.SZ', 10., OPEN, target_weight=.5) == 0
    too_large = order(strategy='A', symbol='000001.SZ', quantity=200, metadata={'target_weight':.5})
    assert not adapter.pre_trade(too_large, OPEN, 10.).ok
    assert adapter.buy_quantity('A', '000002.SZ', 10., OPEN, target_weight=float('nan')) == 0


def test_broker_does_not_apply_implicit_equal_slot_sizing_after_portfolio_allocation():
    from chanlun_trader.engine.broker import BrokerSimulator
    from chanlun_trader.engine.order_manager import OrderManager
    from chanlun_trader.engine.slippage import FixedBpsSlippage
    from chanlun_trader.engine.time_types import EventKind
    ledger = PortfolioLedger(10000.)
    manager = OrderManager()
    config = RiskConfig(max_positions=10, max_position_weight=1., require_universe=False)
    adapter = PortfolioExecutionRiskV1(ledger, config, policy=policy(), fee_model=ChinaAStockFeeModel(),
        admission_check=lambda key: admissions()[key], orders_provider=manager.open_orders,
        prices={'000001.SZ':10.}, session_at=OPEN)
    quantity = adapter.buy_quantity('A','000001.SZ',10.,OPEN,target_weight=.5)
    assert quantity == 200  # 原Broker的1/10规则会错误缩为100股。
    pending = order(strategy='A',symbol='000001.SZ',quantity=quantity,eligible_at=OPEN,
                    metadata={'target_weight':.5})
    manager.create_order(pending, OPEN)
    manager.submit(pending, OPEN)
    broker = BrokerSimulator(None, SimpleNamespace(mode='DAILY'), ledger, manager, risk_manager=adapter,
        slippage_model=FixedBpsSlippage(0.),
        fill_model=SimpleNamespace(try_fill=lambda order, ts, bar: (10.,order.remaining_quantity,'OK')),
        price_limit_model=SimpleNamespace(can_buy_at_open=lambda *args:(True,'OK')))
    broker._bar_for_order = lambda *args: {'open':10.,'volume':100000}
    broker.process_orders(EventKind.SESSION_OPEN,OPEN)
    assert ledger.position_qty('A','000001.SZ') == 200
    assert config.max_positions == 10  # 原配置不变；成交后仍保留其他原风控。
    assert adapter.base_config is config
