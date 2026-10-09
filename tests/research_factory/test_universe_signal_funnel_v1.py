"""漏斗逐层身份及原始数量证据；不以扫描行或FILLED状态代替成交机会。"""
from copy import deepcopy

import pytest

from chanlun_trader.research_factory.universe_signal_funnel_v1 import (
    build_signal_funnel_v1, build_signal_funnel_stream_v1, is_signal_opportunity, signal_key,
)


RULE = 'a' * 64
CALENDAR = [20240726, 20240729, 20240730]


def case():
    key = signal_key(RULE, 'A', '000001.SZ', 20240726)
    intent = {'intent_id': 'i1', 'strategy_id': 'A', 'symbol': '000001.SZ',
              'side': 'BUY', 'signal_key': key, 'reason': 'BUY'}
    order = {'order_id': 'o1', 'intent_id': 'i1', 'strategy_id': 'A', 'symbol': '000001.SZ',
             'side': 'BUY', 'created_at': '2024-07-29T09:30:00+08:00', 'quantity': 100,
             'filled_quantity': 100, 'status': 'FILLED',
             'metadata': {'funnel_version': 'UNIVERSE_SIGNAL_FUNNEL_V1', 'signal_key': key,
                          'parent_intent_id': 'i1', 'decision_session': 20240726,
                          'execution_session': 20240729, 'requested_quantity': 300, 'allocated_quantity': 200}}
    fill = {'trade_id': 't1', 'order_id': 'o1', 'strategy_id': 'A', 'symbol': '000001.SZ',
            'side': 'BUY', 'quantity': 50, 'fill_time': '2024-07-29T09:30:00+08:00'}
    row = {'symbol': '000001.SZ', 'side': 'BUY', 'reason': 'BUY',
           'conditions': {'buy': True, 'sell': False, 'market_filter': True, 'condition_ready': True,
                          'ready': False, 'score_ready': False},
           'qualification': {'signal_ready': True}}
    return {'rule_identity': RULE, 'strategy_id': 'A', 'calendar': CALENDAR,
            'scan_days': [{'date': 20240726, 'processed': 1, 'rows': [row]},
                          {'date': 20240730, 'processed': 1, 'rows': [deepcopy(row)]}],
            'plans': [{'decision_at': '2024-07-26T15:30:00+08:00', 'next_session': 20240729,
                       'intents': [intent], 'excluded': []}],
            'orders': {'o1': order}, 'fills': [fill, {**fill, 'trade_id': 't2'}]}


def test_original_allocated_quantity_survives_broker_rewrite_and_two_fills():
    value = build_signal_funnel_v1(**case())
    assert value['counts'] == {'scan_rows': 2, 'opportunity_signals': 2, 'intents': 1,
                               'orders': 1, 'fills': 2, 'realized_signals': 1, 'tail_signals': 1}
    order = value['orders'][0]
    assert order['status'] == 'FILLED' and order['quantity'] == 100
    assert order['requested_quantity'] == 300 and order['allocated_quantity'] == 200
    assert order['actual_quantity'] == 100 and order['disposition'] == 'PARTIALLY_REALIZED'
    assert order['execution_realization'] == .5 and order['requested_realization'] == 1 / 3
    assert value['signals'][-1]['disposition'] == 'END_OF_OBSERVATION_NO_NEXT_SESSION'
    assert value['signals'][-1]['blocked'] is False


def test_score_unknown_and_cooldown_still_count_condition_opportunities():
    data = case()
    data['plans'][0]['intents'] = []
    data['orders'], data['fills'] = {}, []
    data['scan_days'][0]['rows'][0].update(side='HOLD', reason='SCORE_UNKNOWN')
    value = build_signal_funnel_v1(**data)
    assert value['counts']['opportunity_signals'] == 2
    assert value['opportunity_dispositions']['SCORE_UNKNOWN'] == 1
    row = data['scan_days'][0]['rows'][0]
    row['reason'] = 'COOLDOWN'
    assert is_signal_opportunity(row)
    row['conditions']['buy'] = None
    assert not is_signal_opportunity(row)


def test_original_skip_cannot_be_upgraded_to_a_specific_position_reason():
    data = case()
    data['orders'], data['fills'] = {}, []
    data['skipped_intents'] = [{'intent_id': 'i1', 'reason': 'NO_PERMITTED_QUANTITY'}]
    value = build_signal_funnel_v1(**data)
    assert value['opportunity_dispositions']['NO_PERMITTED_QUANTITY_UNCLASSIFIED'] == 1


@pytest.mark.parametrize('allocated', [0, 200])
def test_native_allocation_retains_request_without_double_counting_orders(allocated):
    data = case()
    data['allocations'] = [{'intent_id': 'i1', 'execution_session': 20240729,
        'requested_quantity': 300, 'allocated_quantity': allocated}]
    if not allocated:
        data['orders'], data['fills'] = {}, []
        data['skipped_intents'] = [{'intent_id': 'i1', 'reason': 'POSITION_LIMIT'}]
    result = build_signal_funnel_v1(**data)
    intent = result['intents'][0]
    assert intent['requested_quantity'] == 300
    assert intent['allocated_quantity'] == allocated
    assert intent['actual_quantity'] == (100 if allocated else 0)


@pytest.mark.parametrize('fault,code', [
    ('duplicate_signal', 'DUPLICATE_SIGNAL'), ('duplicate_fill', 'FILL_LINK'),
    ('unknown_order', 'FILL_LINK'), ('wrong_fill_day', 'FILL_SESSION'),
    ('wrong_order_day', 'ORDER_LINK'), ('wrong_signal', 'ORDER_SIGNAL'),
    ('overallocated', 'ORDER_QUANTITY'), ('overfilled', 'FILL_QUANTITY'),
    ('missing_metadata', 'ORIGINAL_QUANTITY'), ('weekend', 'NEXT_SESSION'),
    ('missing_intent', 'INTENT_LINK'), ('missing_fills', 'FILL_QUANTITY'),
])
def test_identity_and_quantity_faults_are_rejected(fault, code):
    data = case()
    if fault == 'duplicate_signal':
        data['scan_days'].append(deepcopy(data['scan_days'][0]))
    elif fault == 'duplicate_fill':
        data['fills'].append(deepcopy(data['fills'][0]))
    elif fault == 'unknown_order':
        data['fills'][0]['order_id'] = 'absent'
    elif fault == 'wrong_fill_day':
        data['fills'][0]['fill_time'] = '2024-07-30T09:30:00+08:00'
    elif fault == 'wrong_order_day':
        data['orders']['o1']['created_at'] = '2024-07-30T09:30:00+08:00'
    elif fault == 'wrong_signal':
        data['orders']['o1']['metadata']['signal_key'] = 'wrong'
    elif fault == 'overallocated':
        data['orders']['o1']['metadata']['allocated_quantity'] = 400
    elif fault == 'overfilled':
        data['orders']['o1']['metadata']['allocated_quantity'] = 50
    elif fault == 'missing_metadata':
        data['orders']['o1']['metadata'] = {}
    elif fault == 'weekend':
        data['plans'][0]['next_session'] = 20240727
    elif fault == 'missing_intent':
        data['orders']['o1']['intent_id'] = 'absent'
    elif fault == 'missing_fills':
        data['fills'].clear()
    with pytest.raises(ValueError, match=code):
        build_signal_funnel_v1(**data)


def test_sell_lot_children_share_parent_but_keep_distinct_orders():
    data = case()
    intent = data['plans'][0]['intents'][0]
    intent['side'] = 'SELL'
    order = data['orders']['o1']
    order.update(intent_id='i1:lot1', side='SELL')
    for fill in data['fills']:
        fill['side'] = 'SELL'
    data['orders']['o2'] = {**deepcopy(order), 'order_id': 'o2', 'intent_id': 'i1:lot2'}
    data['fills'].extend([{**fill, 'trade_id': fill['trade_id'] + '-lot2', 'order_id': 'o2'}
                          for fill in list(data['fills'])])
    value = build_signal_funnel_v1(**data)
    assert value['counts']['intents'] == 1 and value['counts']['orders'] == 2
    assert value['intents'][0]['actual_quantity'] == 200
    assert value['counts']['realized_signals'] == 0


def packets(data):
    return [{'decision_session': 20240726, 'scan_days': [data['scan_days'][0]],
             'plans': data['plans'], 'orders': data['orders'], 'fills': data['fills']},
            {'decision_session': 20240730, 'scan_days': [data['scan_days'][1]], 'plans': []}]


def test_day_stream_has_same_unique_denominators_without_retaining_details():
    data, written = case(), []
    value = build_signal_funnel_stream_v1(rule_identity=RULE, strategy_id='A', calendar=CALENDAR,
        day_packets=iter(packets(data)), event_sink=written.append,
        expected_decision_sessions=[20240726, 20240730])
    reference = build_signal_funnel_v1(**data)
    assert value['counts'] == reference['counts']
    assert value['opportunity_dispositions'] == reference['opportunity_dispositions']
    assert 'signals' not in value and 'orders' not in value
    assert len(written) == 2 and written[-1]['chain_identity'] == value['detail_chain_identity']


def test_day_stream_rejects_duplicate_days_and_missing_frozen_scan_sessions():
    data = case()
    common = dict(rule_identity=RULE, strategy_id='A', calendar=CALENDAR, event_sink=lambda value: None)
    values = packets(data)
    with pytest.raises(ValueError, match='DAY_ORDER'):
        build_signal_funnel_stream_v1(**common, day_packets=[values[0], values[0]])
    with pytest.raises(ValueError, match='SCAN_SCOPE_INCOMPLETE'):
        build_signal_funnel_stream_v1(**common, day_packets=[values[0]],
                                     expected_decision_sessions=[20240726, 20240730])


def test_condition_ready_is_not_removed_by_a_score_only_long_warmup():
    row = case()['scan_days'][0]['rows'][0]
    row['qualification'].update(status='WARMUP_INSUFFICIENT', state_known=True,
                                bar_present=True, signal_ready=False, gap_reasons=[])
    assert is_signal_opportunity(row)
    row['qualification']['gap_reasons'] = ['UNIVERSE_SOURCE_NOT_QUALIFIED']
    assert not is_signal_opportunity(row)
