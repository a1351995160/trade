"""评分专用依赖、冻结身份、受限语法及独立就绪语义。"""
from copy import deepcopy

import pandas as pd
import pytest

from chanlun_trader.research_factory.research_rule_strategy_v4 import (
    CAPABILITY, ResearchRuleStrategyV4, rule_capabilities, validate_rule_payload,
)
from chanlun_trader.research_factory.research_rule_strategy_v3 import (
    CAPABILITY as V3, ResearchRuleStrategyV3, EXECUTION_MODE,
)
from chanlun_trader.research_factory.strategy_interface_v1 import Context, describe


def node(op, *args, **params):
    return {'op': op, 'args': list(args), 'params': params}


def payload():
    return {'version': CAPABILITY, 'hypothesis': '趋势入场，较低波动优先', 'change_reason': '冻结同日选股依据',
        'buy': node('gt', node('field', 'close'), node('const', value=10)),
        'sell': node('lt', node('field', 'close'), node('const', value=5)),
        'market_filter': None, 'min_hold_sessions': 2, 'max_hold_sessions': 20,
        'cooldown_sessions': 2, 'target_weight': .25,
        'indicator_instances': [{'instance_id': 'vol', 'id': 'ROLLING_VOLATILITY',
            'version': 'ROLLING_VOLATILITY_V1', 'params': {'window': 100}}],
        'selection': {'score': node('indicator', 'vol', output='volatility', version='ROLLING_VOLATILITY_V1'),
            'direction': 'ASCENDING', 'tie_breaker': 'SYMBOL_ASCENDING'},
        'exits': {'execution_mode': EXECUTION_MODE, 'stop_loss_pct': None, 'take_profit_pct': None,
            'trailing_activate_pct': None, 'trailing_pct': None}}


def strategy(value=None):
    return ResearchRuleStrategyV4(payload() if value is None else value, strategy_id='SCORE_TEST')


def context(matrix, *, quantity=0, entry=None):
    return Context(matrix, tuple(matrix.index), len(matrix) - 1,
        {'quantity': quantity, 'sellable_quantity': quantity, 'entry_session_index': entry,
            'last_exit_session_index': None}, {})


def test_score_only_instance_extends_requirements_without_shrinking_raw_signal():
    rule = strategy()
    assert rule.condition_references == []
    assert rule.score_references == [('vol', 'volatility')]
    assert rule.requirements.warmup_sessions == 101
    assert rule.condition_warmup_sessions == 1
    matrix = rule.build_feature_matrix(pd.DataFrame({'close': [11. + i % 3 for i in range(120)]}))
    before = rule.on_close(context(matrix.iloc[:20]))
    assert before.reason == 'SCORE_UNKNOWN' and before.intent is None
    assert before.metadata['raw_signal'] and before.metadata['condition_ready']
    assert before.metadata['selection']['score'] is None
    after = rule.on_close(context(matrix))
    assert after.reason == 'BUY' and after.metadata['selection']['score_ready']
    assert after.metadata['selection']['direction'] == 'ASCENDING'
    rule.validate()


def test_arithmetic_score_is_actual_numerical_value_and_zero_division_unknown():
    value = payload()
    value['indicator_instances'] = []
    value['selection']['score'] = node('div', node('sub', node('field', 'close'), node('const', value=10)),
        node('field', 'volume'))
    rule = strategy(value)
    assert rule.requirements.fields == ('close', 'volume')
    frame = pd.DataFrame({'close': [12., 14.], 'volume': [2., 0.]})
    scores, ready = rule.evaluate_selection(frame)
    assert scores.iloc[0] == 1 and bool(ready.iloc[0])
    assert pd.isna(scores.iloc[1]) and not bool(ready.iloc[1])
    decision = rule.on_close(context(frame))
    assert decision.metadata['raw_signal'] and decision.reason == 'SCORE_UNKNOWN'


def test_score_lag_dependencies_are_causal_and_future_prefix_unchanged():
    value = payload()
    value['selection']['score'] = node('ref', value['selection']['score'], periods=7)
    rule = strategy(value)
    assert rule.score_warmup_sessions == 108
    prefix = pd.DataFrame({'close': [11. + i % 3 for i in range(120)]})
    changed = pd.concat([prefix, pd.DataFrame({'close': [500., 1., 800.]}, index=[120, 121, 122])])
    first = rule.build_feature_matrix(prefix)
    expanded = rule.build_feature_matrix(changed)
    pd.testing.assert_frame_equal(first, expanded.iloc[:len(first)])
    pd.testing.assert_series_equal(rule.evaluate_selection(first)[0], rule.evaluate_selection(expanded)[0].iloc[:len(first)])


def test_unready_condition_stays_unknown_even_when_score_ready():
    value = payload()
    value['buy'] = node('gt', value['selection']['score'], node('const', value=0))
    value['selection']['score'] = node('const', value=1)
    rule = strategy(value)
    matrix = rule.build_feature_matrix(pd.DataFrame({'close': [12.] * 20}))
    decision = rule.on_close(context(matrix))
    assert decision.reason == 'CONDITION_UNKNOWN'
    assert not decision.metadata['raw_signal'] and not decision.metadata['condition_ready']
    assert decision.metadata['selection']['score_ready']


def test_exit_and_held_state_are_not_blocked_by_unknown_score():
    rule = strategy()
    matrix = rule.build_feature_matrix(pd.DataFrame({'close': [12., 12., 4.]}))
    assert rule.on_close(context(matrix.iloc[:2], quantity=100, entry=0)).reason == 'HOLD'
    exited = rule.on_close(context(matrix, quantity=100, entry=0))
    assert exited.reason == 'SELL' and exited.intent.weight == 0
    assert not exited.metadata['selection']['score_ready']


@pytest.mark.parametrize('score', [node('python', 'os'), node('field', 'labels_m6'), node('field', 'entry_price'),
    node('field', 'account_return'), node('ref', node('field', 'close'), periods=-1),
    node('const', value=float('inf')), node('const', value=True), node('rank', node('field', 'close')),
    node('gt', node('field', 'close'), node('const', value=0)),
    node('div', node('field', 'close')), node('add', node('field', 'close'), node('const', value=1), center=True)])
def test_future_labels_python_boolean_and_invalid_score_rejected(score):
    value = payload()
    value['selection']['score'] = score
    with pytest.raises(ValueError):
        validate_rule_payload(value)


@pytest.mark.parametrize('change', [{'direction': 'RANDOM'}, {'direction': {}},
    {'tie_breaker': 'BEST_FUTURE_RETURN'}, {'top_n': 2}, {'score': None}])
def test_selection_strict_fields(change):
    value = payload()
    value['selection'].update(change)
    with pytest.raises(ValueError):
        validate_rule_payload(value)


def test_v3_does_not_silently_accept_selection_or_arithmetic():
    value = payload()
    value['version'] = V3
    with pytest.raises(ValueError, match='PAYLOAD'):
        ResearchRuleStrategyV3(value, strategy_id='OLD')
    value.pop('selection')
    value['indicator_instances'] = []
    value['buy']['args'][0] = node('add', node('field', 'close'), node('const', value=1))
    with pytest.raises(ValueError, match='OPERATOR_UNSUPPORTED'):
        ResearchRuleStrategyV3(value, strategy_id='OLD')


def test_identity_and_validate_bind_scoring_and_source_dependencies():
    original = strategy()
    value = payload()
    value['hypothesis'] = '只改解释'
    assert strategy(value).rule_identity == original.rule_identity
    value['selection']['direction'] = 'DESCENDING'
    assert strategy(value).rule_identity != original.rule_identity
    value = deepcopy(payload())
    value['indicator_instances'][0]['params']['window'] = 101
    assert strategy(value).rule_identity != original.rule_identity
    assert any(path.endswith('universe_selection_v1.py') for path in describe(original)['source_hashes'])
    original.score_references = []
    with pytest.raises(ValueError, match='CHANGED_AFTER_FREEZE'):
        original.validate()


def test_capabilities_describe_research_only_and_existing_score_operators():
    caps = rule_capabilities()
    assert caps['version'] == CAPABILITY
    assert caps['qualification'] == 'EXPLORATORY_ONLY'
    assert caps['selection']['operators'] == ['add', 'const', 'div', 'field', 'indicator', 'mul', 'ref', 'sub']
