"""现金/额度/名额分别解释，且旧整数分配结果保持一致。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.order import OrderStatus
from chanlun_trader.research_factory.portfolio_execution_v1 import build_portfolio_plan
from chanlun_trader.research_factory.universe_selection_v1 import selection_metadata
from test_portfolio_execution_v1 import OPEN, CLOSE, risk, buy, order, policy, admissions


def test_cash_and_lot_rounding_have_original_financial_evidence():
    ledger = PortfolioLedger(10000.)
    adapter = risk(ledger)
    ledger.cash = 995.
    ledger.current_equity = lambda: 10000.
    value = adapter.buy_allocation('B', '000002.SZ', 10., OPEN)
    assert value['allocated_quantity'] == adapter.buy_quantity('B', '000002.SZ', 10., OPEN) == 0
    assert value['requested_quantity'] == 500
    assert value['primary_reason'] == 'CASH_INCLUDING_FEES'
    assert 'LOT_ROUNDING' in value['binding_limits']
    assert value['basis']['available_cash'] == 995.
    assert value['basis']['limits']['cash'] == 995.


@pytest.mark.parametrize('changes,reason,expected', [
    ({'max_symbol_exposure_bps': 1000}, 'SYMBOL_EXPOSURE', 100),
    ({'max_buy_turnover_bps': 1000}, 'BUY_TURNOVER', 100),
])
def test_independent_caps_are_not_reported_as_slots(changes, reason, expected):
    adapter = risk(PortfolioLedger(10000.), **changes)
    value = adapter.buy_allocation('A', '000001.SZ', 10., OPEN)
    assert value['allocated_quantity'] == expected
    assert value['primary_reason'] == reason
    assert value['binding_limits'] == [reason]
    assert not value['basis']['position_limit']


def test_pending_reservations_and_multiple_binding_limits_are_preserved():
    ledger = PortfolioLedger(10000.)
    pending = order(strategy='A', symbol='000001.SZ', quantity=100,
                    status=OrderStatus.ACCEPTED)
    adapter = risk(ledger, orders=[pending], max_positions=1, max_buy_turnover_bps=0)
    value = adapter.buy_allocation('B', '000002.SZ', 10., OPEN)
    assert value['primary_reason'] == 'POSITION_LIMIT'
    assert value['binding_limits'] == ['POSITION_LIMIT', 'BUY_TURNOVER']
    assert value['basis']['pending_buy_cost'] > 1000.
    assert value['basis']['occupied_position_keys'] == [['A', '000001.SZ']]


def test_member_fee_and_target_have_separate_caps_and_precise_guard_reason():
    ledger = PortfolioLedger(10000.)
    adapter = risk(ledger)
    value = adapter.buy_allocation('A', '000001.SZ', 10., OPEN)
    assert value['requested_quantity'] == 500 and value['allocated_quantity'] == 400
    assert value['primary_reason'] == 'MEMBER_ALLOCATION'
    assert value['quantity_limits']['TARGET_WEIGHT'] == 500
    assert value['quantity_limits']['MEMBER_ALLOCATION'] == 400
    assert value['estimated_fee'] > 0
    invalid = adapter.buy_allocation('A', '000001.SZ', 10., OPEN, target_weight=float('nan'))
    assert invalid['primary_reason'] == 'PORTFOLIO_TARGET_WEIGHT_INVALID'
    assert adapter.buy_quantity('A', '000001.SZ', 10., OPEN, target_weight=float('nan')) == 0


def test_ownership_reason_and_same_open_sale_proceeds_stay_unspendable():
    ledger = PortfolioLedger(10000.)
    buy(ledger)
    adapter = risk(ledger, overlap='ONE_STRATEGY_PER_SYMBOL')
    value = adapter.buy_allocation('B', '000001.SZ', 10., OPEN)
    assert value['primary_reason'] == 'PORTFOLIO_SYMBOL_OWNED_BY_OTHER_STRATEGY'


def test_selection_is_versioned_while_old_rows_keep_old_schema_and_code_order():
    records = [{'strategy_id': 'A', 'symbol': symbol, 'side': 'BUY'}
               for symbol in ('000002.SZ', '000001.SZ')]
    kwargs = dict(policy=policy(), ledger=PortfolioLedger(10000.), admissions=admissions(),
                  input_identity='data', decision_at=CLOSE, next_session=20260925)
    old = build_portfolio_plan(decisions=records, **kwargs)
    assert old['schema_version'] == 'PORTFOLIO_PAPER_PLAN_V1'
    assert [row['symbol'] for row in old['intents']] == ['000001.SZ', '000002.SZ']
    scored = deepcopy(records)
    for record, score in zip(scored, (1., 2.)):
        record['metadata'] = {'selection': selection_metadata(score, rule_identity='a'*64, direction='ASCENDING')}
    new = build_portfolio_plan(decisions=scored, **kwargs)
    assert new['schema_version'] == 'PORTFOLIO_PAPER_PLAN_V2'
    assert [row['symbol'] for row in new['intents']] == ['000002.SZ', '000001.SZ']
    assert build_portfolio_plan(decisions=records, **kwargs) == old
    scored[0]['metadata']['selection'] = selection_metadata(None, rule_identity='a'*64, direction='ASCENDING')
    unknown = build_portfolio_plan(decisions=scored, **kwargs)
    assert unknown['excluded'][0]['reason'] == 'SCORE_UNKNOWN'
    scored[1]['metadata']['selection']['rule_identity'] = 'b'*64
    with pytest.raises(ValueError, match='SELECTION_RULE_CONFLICT'):
        build_portfolio_plan(decisions=scored, **kwargs)


def test_broker_cannot_erase_original_allocated_quantity_when_marking_filled():
    from chanlun_trader.engine.broker import BrokerSimulator
    from chanlun_trader.engine.order_manager import OrderManager
    from chanlun_trader.engine.risk import RiskConfig
    from chanlun_trader.engine.slippage import FixedBpsSlippage
    from chanlun_trader.engine.time_types import EventKind
    from chanlun_trader.research_factory.portfolio_execution_v1 import PortfolioExecutionRiskV1
    ledger, manager = PortfolioLedger(10000.), OrderManager()
    adapter = PortfolioExecutionRiskV1(ledger, RiskConfig(max_positions=10, require_universe=False),
        policy=policy(), fee_model=risk(ledger).fee_model,
        admission_check=lambda key: admissions()[key], orders_provider=manager.open_orders,
        prices={'000001.SZ': 10.}, session_at=OPEN)
    pending = order(strategy='A', symbol='000001.SZ', quantity=200, eligible_at=OPEN,
                    metadata={'target_weight': .5, 'requested_quantity': 250, 'allocated_quantity': 200})
    manager.create_order(pending, OPEN)
    manager.submit(pending, OPEN)
    broker = BrokerSimulator(None, SimpleNamespace(mode='DAILY'), ledger, manager, risk_manager=adapter,
        slippage_model=FixedBpsSlippage(0.),
        fill_model=SimpleNamespace(try_fill=lambda order, ts, bar: (10., 100, 'OK')),
        price_limit_model=SimpleNamespace(can_buy_at_open=lambda *args: (True, 'OK')))
    broker._bar_for_order = lambda *args: {'open': 10., 'volume': 100000}
    broker.process_orders(EventKind.SESSION_OPEN, OPEN)
    assert pending.status == OrderStatus.FILLED and pending.quantity == 100
    assert pending.metadata['allocated_quantity'] == 200
    assert pending.metadata['requested_quantity'] == 250
