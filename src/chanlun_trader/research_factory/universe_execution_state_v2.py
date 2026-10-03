"""只在完整收盘提交的显式引擎 codec；不用于独立审计初始化。"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

from .common import jsonable, stable_hash

VERSION = 'UNIVERSE_EXECUTION_STATE_V2'
ENGINE_FIELDS = ('_signal_seq', '_intent_seq', '_target_seq', '_exit_order_lots',
                 '_pending_exit_lots', 'sizing_skips')


def capture(paper, *, last_day, execution_identity, artifacts, decisions, peak, drawdown):
    engine = paper.engine
    orders = engine.order_manager
    body = {'version': VERSION, 'execution_identity': execution_identity,
        'last_day': last_day, 'calendar_identity': stable_hash(paper.inputs.window['calendar']),
        'ledger': engine.ledger.checkpoint(),
        'engine': {name: deepcopy(getattr(engine, name)) for name in ENGINE_FIELDS},
        'orders': {k: asdict(v) for k, v in orders.orders.items()},
        'order_counters': [orders._seq, orders._id_counter], 'open_order_ids': sorted(orders._open_ids),
        'broker_fill_counter': engine.broker._fill_counter,
        'events': engine.event_log.to_records(), 'event_counter': engine.event_log._seq,
        'rule_states': [[list(key), value] for key, value in sorted(paper.rule_states.items())],
        'rule_exits': {key: {'price_policy': value.price_policy, 'trace': value.trace,
            'trailing': {k: asdict(v) for k, v in value.evaluator._v1.trailing.items()},
            'evaluations': value.evaluator.evaluations}
            for key, value in paper.rule_exits.items()},
        'bars': [{'symbol': symbol, 'date': int(day), **row.to_dict()}
            for symbol, frame in sorted(paper.store.daily_raw.items()) for day, row in frame.iterrows()],
        'states': [{**asdict(rows[-1]), 'listing_date': getattr(rows[-1], 'listing_date', None),
            'listing_sessions_before_calendar': getattr(rows[-1], 'listing_sessions_before_calendar', None)}
            for _, rows in sorted(paper.master._states.items()) if rows],
        'skips': deepcopy(paper.skips), 'allocations': deepcopy(getattr(paper, 'allocations', [])),
        'decisions': decisions, 'artifacts': artifacts, 'peak': peak, 'drawdown': drawdown}
    # 恢复时必须保留字典插入次序，浮点权益与 FIFO 遍历都可能受其影响。
    body = json.loads(json.dumps(jsonable(body), ensure_ascii=False, default=str, allow_nan=False))
    return {**body, 'state_identity': stable_hash(body)}


def restore(paper, snapshot, *, execution_identity):
    from ..engine.daily_exit_v1 import LotTrailingState
    from ..engine.events import BacktestEvent, OrderEvent
    from ..engine.order import Order, OrderStatus
    from ..engine.security_state import SecurityState
    body = {k: v for k, v in snapshot.items() if k != 'state_identity'}
    if (body.get('version') != VERSION or snapshot.get('state_identity') != stable_hash(body)
            or body['execution_identity'] != execution_identity
            or body['calendar_identity'] != stable_hash(paper.inputs.window['calendar'])
            or body['last_day'] not in paper.inputs.window['calendar']):
        raise ValueError('UNIVERSE_STATE_OR_EXECUTION_CONFLICT')
    engine = paper.engine
    ledger = type(engine.ledger).restore(body['ledger'], engine.ledger.events, engine.ledger.dataset_id)
    engine.ledger = engine.broker.ledger = engine.risk.ledger = ledger
    if type(body['broker_fill_counter']) is not int or body['broker_fill_counter'] < 0:
        raise ValueError('UNIVERSE_STATE_FILL_COUNTER_CONFLICT')
    engine.broker._fill_counter = body['broker_fill_counter']
    for key in ENGINE_FIELDS:
        setattr(engine, key, deepcopy(body['engine'][key]))
    manager = engine.order_manager
    manager.orders = {key: Order(**value) for key, value in body['orders'].items()}
    manager._seq, manager._id_counter = body['order_counters']
    manager._open_ids = set(body['open_order_ids'])
    if manager._open_ids != {k for k, o in manager.orders.items() if o.is_open}:
        raise ValueError('UNIVERSE_STATE_OPEN_ORDERS_CONFLICT')
    engine.event_log._events = []
    for row in body['events']:
        row = dict(row)
        if 'order_status' in row:
            if row['order_status'] is not None:
                row['order_status'] = OrderStatus(row['order_status'])
            event = OrderEvent(**row)
        else:
            event = BacktestEvent(**row)
        engine.event_log._events.append(event)
    engine.event_log._seq = body['event_counter']
    paper.rule_states = {tuple(key): deepcopy(value) for key, value in body['rule_states']}
    if set(body['rule_exits']) != set(paper.rule_exits):
        raise ValueError('UNIVERSE_STATE_EXIT_CONFLICT')
    for key, state in body['rule_exits'].items():
        adapter = paper.rule_exits[key]
        adapter.price_policy, adapter.trace = state['price_policy'], deepcopy(state['trace'])
        adapter.evaluator._v1.trailing = {k: LotTrailingState(**v) for k, v in state['trailing'].items()}
        adapter.evaluator.evaluations = deepcopy(state['evaluations'])
    paper.store.daily_raw.clear()
    paper._load_bars(body['bars'])
    paper.master._states.clear()
    for row in body['states']:
        row = dict(row)
        listing, before = row.pop('listing_date'), row.pop('listing_sessions_before_calendar')
        state = SecurityState(**row)
        state.listing_date, state.listing_sessions_before_calendar = listing, before
        paper.master.add_state(state)
        paper.master.strict_daily_symbols.add(state.symbol)
    paper.skips = deepcopy(body['skips'])
    paper.allocations = deepcopy(body['allocations'])
    paper._invariants()
    return deepcopy(body['decisions']), body['peak'], body['drawdown']


def write_snapshot(path, value):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('UNIVERSE_STATE_PATH_REDIRECTED')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.close_', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(jsonable(value), ensure_ascii=False, separators=(',', ':'),
                                    default=str, allow_nan=False))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
