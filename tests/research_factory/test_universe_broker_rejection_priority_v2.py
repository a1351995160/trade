"""撮合拒单原因保持原生优先级；合成数据不作为真实验收证据。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from chanlun_trader.engine.fee import ChinaAStockFeeModel
from chanlun_trader.engine.fill import Fill
from chanlun_trader.engine.ledger import PortfolioLedger
from chanlun_trader.engine.order import Order
from chanlun_trader.engine.risk import RiskConfig
from chanlun_trader.engine.signal import Side
from chanlun_trader.research_factory.formal_account_backend_v1 import STRESS_COSTS
from chanlun_trader.research_factory.portfolio_execution_v1 import (
    PortfolioExecutionPolicyV1, PortfolioExecutionRiskV1, PortfolioMemberV1,
)
from chanlun_trader.research_factory.research_rule_strategy_v4 import ResearchRuleStrategyV4
from chanlun_trader.research_factory.universe_account_backend_v2 import UniverseAccountBackendV2
from chanlun_trader.research_factory.universe_account_inputs_v1 import (
    UniverseAccountInputsV1, universe_input_identity_v1,
)
from chanlun_trader.research_factory.universe_evidence_v1 import _execute_day, _stamp, reconstruct_universe_account
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import hydrated_result
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile
from universe_test_fixture_v1 import fixture, proposal


def _prices(bundle, symbol, opens, closes):
    rows = bundle['daily'].symbol.eq(symbol)
    bundle['daily'].loc[rows, 'open'] = opens
    bundle['daily'].loc[rows, 'close'] = closes
    bundle['daily'].loc[rows, 'prev_close'] = [closes[0], *closes[:-1]]
    bundle['daily'].loc[rows, 'high'] = [max(a, b) * 1.01 for a, b in zip(opens, closes)]
    bundle['daily'].loc[rows, 'low'] = [min(a, b) * .99 for a, b in zip(opens, closes)]
    bundle['daily'].loc[rows, 'amount'] = [value * 1_000_000 for value in closes]


def _stress_weight_case(root):
    window, bundle = fixture(symbols=['000001.SZ', '600000.SH'], days_count=65)
    first_opens, first_closes = [12.] * 65, [12.] * 65
    first_opens[60] = 11.99
    first_opens[62:] = first_closes[62:] = [12.05] * 3
    second_opens, second_closes = [4.23] * 65, [4.23] * 65
    second_opens[61] = 4.22
    _prices(bundle, '000001.SZ', first_opens, first_closes)
    _prices(bundle, '600000.SH', second_opens, second_closes)
    rule = proposal()
    rule.update(version='RESEARCH_RULE_STRATEGY_V4', max_hold_sessions=10, target_weight=.5,
        buy={'op': 'gt', 'args': [{'op': 'field', 'args': ['close'], 'params': {}},
                                {'op': 'field', 'args': ['open'], 'params': {}}], 'params': {}},
        sell={'op': 'lt', 'args': [{'op': 'field', 'args': ['close'], 'params': {}},
                                 {'op': 'const', 'args': [], 'params': {'value': 0}}], 'params': {}},
        selection={'score': {'op': 'field', 'args': ['close'], 'params': {}},
                   'direction': 'DESCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'})
    strategy = ResearchRuleStrategyV4(rule, strategy_id='priority')
    backend = UniverseAccountBackendV2(window, costs='STRESS', initial_cash=50000,
        max_positions=2, max_symbol_exposure_bps=5000,
        execution_profile=execution_profile(SEGMENTED_PROFILE, 5), checkpoint_path=root / 'EXECUTION.json')
    result = backend.run(strategy, bundle, bundle['events'],
        lambda: {'input_identity': universe_input_identity_v1(bundle, window)})
    rejected = [order for order in result['final_account_checkpoint']['economic']['orders'].values()
                if order['status'] == 'REJECTED']
    assert len(rejected) == 1
    assert rejected[0]['symbol'] == '600000.SH'
    assert rejected[0]['reason_code'] == rejected[0]['metadata']['broker_rejection_reason'] == 'MAX_POSITION_WEIGHT'
    return bundle, window, rule, result, rejected[0]['order_id']


def _audit(data, result=None):
    bundle, window, rule, original, _ = data
    return reconstruct_universe_account(bundle, window, hydrated_result(original if result is None else result),
        initial_cash=50000, costs=STRESS_COSTS, strategy_id='priority', rule=rule)


def test_real_backend_weight_rejection_passes_independent_account_audit(tmp_path):
    data = _stress_weight_case(tmp_path)
    assert _audit(data)['metrics'] == data[3]['metrics']


@pytest.mark.parametrize('forged_reason', ['PORTFOLIO_CASH_FEE_EXPOSURE_OR_TURNOVER_LIMIT', 'MAX_POSITIONS'])
def test_real_rejection_cannot_be_relabelled_to_another_risk_layer(tmp_path, forged_reason):
    data = _stress_weight_case(tmp_path)
    forged = deepcopy(data[3])
    order = forged['final_account_checkpoint']['economic']['orders'][data[4]]
    order['reason_code'] = order['metadata']['broker_rejection_reason'] = forged_reason
    with pytest.raises(ValueError, match='BROKER_REJECTION_REASON_CONFLICT'):
        _audit(data, forged)


class _AtBrokerValidation(Exception):
    def __init__(self, reason, quantity):
        self.reason, self.quantity = reason, quantity


def _risk_boundary(monkeypatch, *, previous_equity, cash=40000., equity=63696.7875,
                   quantity=7500, price=4.2385, symbol='600000.SH', max_positions=2):
    """仅隔离已创建订单的成交风控；持有股票再买入及容差无法由当前规则自然触发。"""
    window, bundle = fixture(symbols=['000001.SZ', '600000.SH'], days_count=63)
    held_price = (equity - cash) / 1000
    raw_price = price / (1 + STRESS_COSTS['slippage_bps'])
    _prices(bundle, '000001.SZ', [held_price] * 63, [held_price] * 63)
    _prices(bundle, '600000.SH', [raw_price] * 63, [raw_price] * 63)
    day, previous_day = window['calendar'][-1], window['calendar'][-2]
    if symbol == '000001.SZ':
        price = round(held_price * (1 + STRESS_COSTS['slippage_bps']), 4)
    inputs = UniverseAccountInputsV1(bundle, window, stage='ACCOUNT')
    ledger = PortfolioLedger(equity)
    trade, reason = ledger.apply_fill(Fill('held', 'held-order', 'priority', '000001.SZ',
        Side.BUY, 1000, held_price, _stamp(previous_day)))
    assert trade is not None, reason
    ledger.cash = cash
    ledger.snapshots.append(SimpleNamespace(equity=previous_equity))
    policy = PortfolioExecutionPolicyV1(policy_id='priority', members=(
        PortfolioMemberV1(strategy_id='priority', rule_identity='a' * 64, weight_bps=10000, priority=0),),
        purpose='ENGINEERING_OBSERVATION', max_positions=max_positions, max_symbol_exposure_bps=5000,
        max_buy_turnover_bps=10000, valid_until='2023-01-01T00:00:00+08:00')
    risk = PortfolioExecutionRiskV1(ledger, RiskConfig(max_positions=max_positions, max_position_weight=.5),
        policy=policy, fee_model=ChinaAStockFeeModel(**{key: STRESS_COSTS[key] for key in
            ('commission_rate', 'min_commission', 'stamp_tax_rate')}),
        admission_check=lambda _: {'allowed': True, 'archive_hash': 'a' * 64}, orders_provider=lambda: (),
        prices={'000001.SZ': held_price * 1.002, '600000.SH': raw_price * 1.002}, session_at=_stamp(day))
    order = Order(order_id='test-order', strategy_id='priority', symbol=symbol, side=Side.BUY,
        quantity=quantity, created_at=_stamp(day), intent_id='intent', signal_id='signal',
        metadata={'target_weight': .5})
    actual = risk.pre_trade(order, _stamp(day), price)
    from chanlun_trader.research_factory.universe_evidence_v2 import _ReconstructionV2
    account = _ReconstructionV2(inputs, SimpleNamespace(strategy_id='priority'), cash, STRESS_COSTS,
        {'initial_cash': equity})
    account.lots = {'held': {'symbol': '000001.SZ', 'remaining_quantity': 1000}}
    account.positions = {'000001.SZ': {'quantity': 1000}}
    account.prices = {'000001.SZ': held_price}
    # 上游分配与绑定有完整端到端测试；这里输入一个已创建订单，只观察独立风控分类。
    monkeypatch.setattr(account, 'validate_allocation', lambda *args: {
        'requested_quantity': quantity, 'allocated_quantity': quantity})
    monkeypatch.setattr(account, 'validate_order_metadata', lambda *args: None)
    def capture(order, reason, quantity):
        raise _AtBrokerValidation(reason, quantity)
    monkeypatch.setattr(account, 'validate_broker_reason', capture)
    intent = {'intent_id': 'intent', 'symbol': symbol, 'side': 'BUY', 'target_weight': .5}
    plan = {'plan_id': 'plan', 'intents': [intent]}
    supplied = {'order_id': 'test-order', 'intent_id': 'intent', 'strategy_id': 'priority',
        'symbol': symbol, 'side': 'BUY', 'time_in_force': 'DAY', 'priority': 0, 'sequence': 1,
        'metadata': {'plan_id': 'plan'}, **{key: _stamp(day).isoformat() for key in
            ('created_at', 'submitted_at', 'eligible_at')}}
    with pytest.raises(_AtBrokerValidation) as predicted:
        _execute_day(account, day, plan, [supplied], [],
            {'max_positions': max_positions, 'max_symbol_exposure_bps': 5000}, previous_equity, {})
    return actual, predicted.value


def test_combination_limit_precedes_old_snapshot_weight_risk(monkeypatch):
    actual, predicted = _risk_boundary(monkeypatch, previous_equity=20000., cash=7500 * 4.2385)
    assert actual.reason == 'PORTFOLIO_CASH_FEE_EXPOSURE_OR_TURNOVER_LIMIT'
    assert predicted.reason == actual.reason and predicted.quantity == 0


def test_old_snapshot_weight_risk_precedes_position_count(monkeypatch):
    actual, predicted = _risk_boundary(monkeypatch, previous_equity=20000.,
        cash=50000., symbol='000001.SZ', quantity=1000, max_positions=1)
    assert actual.reason == 'MAX_POSITION_WEIGHT'
    assert predicted.reason == actual.reason and predicted.quantity == 0


def test_position_count_keeps_its_native_reason(monkeypatch):
    actual, predicted = _risk_boundary(monkeypatch, previous_equity=63696.7875,
        symbol='000001.SZ', quantity=100, max_positions=1)
    assert actual.reason == 'MAX_POSITIONS'
    assert predicted.reason == actual.reason and predicted.quantity == 0


@pytest.mark.parametrize('overweight', [.5e-9, 1.5e-9], ids=['within-ratio-tolerance', 'outside-ratio-tolerance'])
def test_weight_tolerance_is_a_ratio_not_an_absolute_currency_amount(monkeypatch, overweight):
    quantity, price = 100, 4.2385
    previous_equity = quantity * price / (.5 + overweight)
    actual, predicted = _risk_boundary(monkeypatch, previous_equity=previous_equity,
        quantity=quantity, price=price)
    assert actual.ok is (overweight < 1e-9)
    assert predicted.reason == (None if actual.ok else 'MAX_POSITION_WEIGHT')
    assert bool(predicted.quantity) is actual.ok
