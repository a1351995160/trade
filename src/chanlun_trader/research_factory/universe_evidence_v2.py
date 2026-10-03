"""新版分片账户的独立重建；只从自己的已核对收盘状态继续。"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import numpy as np
import pandas as pd

from .common import stable_hash
from .universe_evidence_v1 import (
    _Reconstruction, _buy_limits, _execute_day, _fee, _final_account_audit,
    _number, _policy, _require, _stamp, _time, _tree,
)
from .universe_signal_funnel_v1 import signal_key


VERSION = 'UNIVERSE_EVIDENCE_V2'
BACKEND = 'UNIVERSE_ACCOUNT_BACKEND_V2'
_STATE_FIELDS = ('cash', 'lots', 'positions', 'prices', 'last_exit', 'entitlements', 'rights',
    'receivables', 'applied', 'paid', 'action_audit', 'pending_share_credits', 'bonus_parent_lots',
    'share_price_factors', 'cash_price_adjustments', 'dividend_record_factors', 'share_tax_lots',
    'share_tax', 'new_share_lots', 'tax', 'income', 'fees', 'turnover', 'realized',
    'rule_states', 'trailing', 'exit_rows', 'trade_count')


def _pack(value):
    # 字典插入次序影响浮点加总；显式保存次序，不能用排序后的JSON字典恢复。
    if isinstance(value, dict):
        return ['DICT', [[key, _pack(item)] for key, item in value.items()]]
    if isinstance(value, list):
        return ['LIST', [_pack(item) for item in value]]
    if isinstance(value, set):
        return ['SET', sorted(value)]
    return ['VALUE', value]


def _unpack(value):
    _require(isinstance(value, list) and len(value) == 2, 'OWN_STATE_CODEC_INVALID')
    kind, body = value
    if kind == 'DICT':
        _require(len({key for key, item in body}) == len(body), 'OWN_STATE_DUPLICATE_KEY')
        return {key: _unpack(item) for key, item in body}
    if kind == 'LIST':
        return [_unpack(item) for item in body]
    if kind == 'SET':
        return set(body)
    _require(kind == 'VALUE' and (body is None or type(body) in (str, bool, int, float)), 'OWN_STATE_CODEC_INVALID')
    return body


def _write(path, value):
    from .universe_execution_state_v2 import write_snapshot
    write_snapshot(path, {**value, 'receipt_hash': stable_hash(value)})


def _read(path, identity):
    if not path.exists():
        return None
    _require(path.resolve() == path.absolute(), 'OWN_STATE_PATH_REDIRECTED')
    value = json.loads(path.read_text(encoding='utf-8'))
    body = {key: item for key, item in value.items() if key != 'receipt_hash'}
    _require(value['receipt_hash'] == stable_hash(body) and body['version'] == VERSION
             and body['identity'] == identity, 'OWN_STATE_IDENTITY_CONFLICT')
    return body


def _verify_prior_artifact_bytes(reader, count):
    """自己的已核对内容只需核对原字节仍在，复用不再重复解压/解释。"""
    root = Path(reader.manifest['root'])
    for row in reader.manifest['days'][:count]:
        path = root / row['file']
        _require(path.resolve().parent == root and path.name == row['file'], 'ARTIFACT_REDIRECTED')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        _require(digest == row['sha256'], 'ARTIFACT_CHANGED')


class _ConditionTable:
    """单证券公式重算，自己的只读数值表；不读取执行器的缓存。"""
    def __init__(self, strategy, inputs, root, identity):
        from ..engine.conditions_v2 import ConditionContext
        from .causal_dividend_features_v1 import causal_hfq_bars, causal_hfq_bars_v2
        from .research_rule_strategy_v2 import _field_references, evaluate_condition
        root.mkdir(parents=True, exist_ok=True)
        self.symbols = {symbol: index for index, symbol in enumerate(inputs.symbols)}
        self.days = {day: index for index, day in enumerate(inputs.calendar)}
        columns = ['buy', 'sell', 'market_filter', 'ready']
        if hasattr(strategy, 'selection'):
            columns += ['score', 'score_ready', 'condition_ready']
        manifest_path, array_path = root / 'OWN_FEATURES.json', root / 'OWN_FEATURES.npy'
        previous = _read(manifest_path, identity)
        if previous:
            with array_path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            _require(previous['sha256'] == digest, 'OWN_FEATURE_CACHE_CHANGED')
            self.preparation = previous['preparation']
        else:
            values = np.lib.format.open_memmap(array_path, mode='w+', dtype='float64',
                shape=(len(inputs.symbols), len(inputs.calendar), len(columns)))
            values[:] = np.nan
            daily_groups = inputs.daily.groupby('symbol', sort=False).indices
            turn_groups = inputs.turn.groupby('symbol', sort=False).indices
            event_groups = defaultdict(list)
            for event in inputs.events:
                event_groups[event['symbol']].append(event)
            fields_needed = {name for expression in strategy.expressions.values()
                             for name in _field_references(expression)}
            self.preparation = []
            for symbol in inputs.symbols:
                raw = inputs.daily.iloc[daily_groups.get(symbol, [])]
                raw = raw.loc[raw.volume.gt(0)].sort_values('date').copy()
                if raw.empty:
                    self.preparation.append({'symbol': symbol, 'status': 'NO_VALID_BARS'})
                    continue
                raw['adjustflag'] = '3'
                events = tuple(event for event in event_groups[symbol]
                    if int(raw.date.min()) < event['effective_date'] <= int(raw.date.max()))
                transform = causal_hfq_bars_v2 if any(event.get('price_version') == 'CASH_AND_SHARES_V2'
                    or event['event_type'] in {'BONUS', 'CAPITALIZATION'} for event in events) else causal_hfq_bars
                bars, trace = transform(raw, events)
                bars = bars.set_index('date')
                turns = inputs.turn.iloc[turn_groups.get(symbol, [])].set_index('date')
                vendor = turns['turn'].reindex(bars.index) if 'turn' in turns else None
                matrix = strategy.build_feature_matrix(bars, vendor)
                computed = {f'{alias}.{output}': matrix[f'{alias}.{output}'] for alias, output in strategy.references}
                masks = {name: matrix[name + '__ready'] for name in computed}
                fields = {name: matrix[name] for name in fields_needed if name in matrix}
                context = ConditionContext(computed, fields, matrix.index, ready=masks)
                frame = pd.DataFrame({name: evaluate_condition(expression, context)
                    for name, expression in strategy.expressions.items()}, index=matrix.index)
                if 'market_filter' not in frame:
                    frame['market_filter'] = 1.
                ready = pd.Series(True, index=matrix.index)
                condition_refs = {f'{alias}.{output}' for alias, output in
                                  getattr(strategy, 'condition_references', strategy.references)}
                for name, series in computed.items():
                    if name in condition_refs:
                        ready &= masks[name].eq(True) & series.map(lambda value: math.isfinite(float(value)))
                for series in fields.values():
                    ready &= series.map(lambda value: math.isfinite(float(value)))
                frame['ready'] = ready & frame[['buy', 'sell', 'market_filter']].notna().all(axis=1)
                if hasattr(strategy, 'selection'):
                    frame['score'], frame['score_ready'] = strategy.evaluate_selection(matrix)
                    frame['condition_ready'] = frame['ready']
                records = [{**{str(key): None if pd.isna(item) else bool(item)
                    if key in ('ready', 'score_ready', 'condition_ready') else float(item)
                    for key, item in row.items()}, 'date': int(day)} for day, row in frame.iterrows()]
                self.preparation.append({'symbol': symbol, 'status': 'COMPUTED', 'bars': len(matrix),
                    'conditions_hash': stable_hash(records), 'price_trace': trace})
                indices = [self.days[int(day)] for day in frame.index if int(day) in self.days]
                selected = frame.loc[[day for day in frame.index if int(day) in self.days], columns]
                values[self.symbols[symbol], indices, :] = selected.to_numpy(dtype=float)
            values.flush()
            del values
            with array_path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            _write(manifest_path, {'version': VERSION, 'identity': identity,
                'sha256': digest, 'preparation': self.preparation})
        self.values = np.load(array_path, mmap_mode='r')
        self.columns = columns

    def truth(self, symbol, day):
        row = self.values[self.symbols[symbol], self.days[day]]
        missing = np.isnan(row[:4]).all()
        truth = {name: None if missing or np.isnan(row[index]) else bool(row[index])
                 for index, name in enumerate(self.columns[:3])}
        truth.update(ready=False if missing or not np.isfinite(row[3]) else bool(row[3]),
                     reason='NO_COMPLETED_BAR' if missing else 'COMPUTED')
        if len(self.columns) > 4:
            truth.update(score=float(row[4]) if np.isfinite(row[4]) else None,
                score_ready=bool(row[5]) if np.isfinite(row[5]) else False, condition_ready=truth['ready'])
        return truth


class _ReconstructionV2(_Reconstruction):
    version = VERSION

    def quantity(self, symbol):
        if getattr(self, '_indexed_lots_count', -1) != len(self.lots):
            self._lots_by_symbol = defaultdict(list)
            for lot in self.lots.values():
                self._lots_by_symbol[lot['symbol']].append(lot)
            self._indexed_lots_count = len(self.lots)
        # FIFO更新的是同一普通字典；不能在fill尚未同步position时提前读position数量。
        return sum(lot['remaining_quantity'] for lot in self._lots_by_symbol[symbol])

    def decisions(self, day, frames):
        risk = self.risk_exits(day)
        session, records, rows = self.inputs.session_index(day), [], []
        active_by_symbol = defaultdict(list)
        for lot in self.lots.values():
            if lot['remaining_quantity']:
                active_by_symbol[lot['symbol']].append(lot)
        for symbol in self.inputs.symbols:
            truth, qualification = frames.truth(symbol, day), self.inputs.scan_status(symbol, day)
            previous = self.rule_states.get(symbol, {})
            quantity = self.quantity(symbol)
            current = {'rule_identity': self.strategy.rule_identity, 'last_session_index': session,
                       'exit_pending': bool(quantity and previous.get('exit_pending', False))}
            held = session - min((lot['entry_session_index'] for lot in active_by_symbol[symbol]), default=session)
            side, reason, weight = 'HOLD', 'HOLD', 0.
            rule = self.strategy.payload
            if quantity and (current['exit_pending'] or held >= rule['max_hold_sessions']
                             or (held >= rule['min_hold_sessions'] and truth['sell'] is True)):
                current['exit_pending'] = True
                side, reason = 'SELL', 'EXIT_PENDING' if previous.get('exit_pending') else 'SELL'
            elif quantity:
                pass
            elif truth['sell'] is True:
                reason = 'EXIT_PRIORITY'
            elif symbol in self.last_exit and session - self.last_exit[symbol] <= rule['cooldown_sessions']:
                reason = 'COOLDOWN'
            elif not truth['ready'] or any(truth[key] is None for key in ('buy', 'sell', 'market_filter')):
                reason = 'CONDITION_UNKNOWN'
            elif not truth['buy'] or not truth['market_filter']:
                reason = 'CONDITION_FALSE'
            else:
                side, reason, weight = 'BUY', 'BUY', rule['target_weight']
            if side == 'BUY' and hasattr(self.strategy, 'selection') and not truth['score_ready']:
                side, reason = 'HOLD', 'SCORE_UNKNOWN'
            if side == 'BUY' and not qualification['entry_eligible']:
                side, reason = 'HOLD', 'SECURITY_NOT_ELIGIBLE'
            record = {'strategy_id': self.strategy.strategy_id, 'symbol': symbol, 'side': side,
                'reason': reason, 'target_weight': weight,
                'signal_key': signal_key(self.strategy.rule_identity, self.strategy.strategy_id, symbol, day)}
            if risk.get(symbol) and side != 'SELL':
                record.update(side='SELL', reason='RULE_RISK_EXIT_V3', target_weight=0., exit_lot_ids=sorted(risk[symbol]))
            self.rule_states[symbol] = current
            records.append(record)
            rows.append({'symbol': symbol, 'conditions': truth, 'qualification': qualification,
                         'side': record['side'], 'reason': record['reason']})
        if hasattr(self.strategy, 'selection'):
            direction = self.strategy.selection['direction']
            candidates = [record for record in records if record['side'] == 'BUY']
            candidates.sort(key=lambda record: (
                frames.truth(record['symbol'], day)['score'] * (1 if direction == 'ASCENDING' else -1), record['symbol']))
            for rank, record in enumerate(candidates, 1):
                record['metadata'] = {'selection': {'version': 'UNIVERSE_SELECTION_V1',
                    'rule_identity': self.strategy.rule_identity,
                    'score': frames.truth(record['symbol'], day)['score'], 'score_ready': True,
                    'direction': direction, 'tie_breaker': 'SYMBOL_ASCENDING', 'rank': rank}}
        return records, rows

    def validate_plan(self, plan, previous_day, day, decisions, portfolio):
        _require(plan['plan_id'] == 'PORTFOLIO_PAPER_' + stable_hash({key: value for key, value in plan.items() if key != 'plan_id'}), 'PLAN_HASH_CONFLICT')
        _require(plan['input_identity'] == self.inputs.input_identity and plan['next_session'] == day
                 and _time(plan['decision_at']) == _stamp(previous_day, close=True) + pd.Timedelta(minutes=30), 'PLAN_SCOPE_CONFLICT')
        _require(plan['policy_hash'] == stable_hash(portfolio) and plan['members'] == portfolio['members'], 'PLAN_POLICY_CONFLICT')
        _require(plan['account_identity'] == self.economic_identity(), 'PLAN_ACCOUNT_IDENTITY_CONFLICT')
        _tree(plan['holdings'], [lot for lot in self.public_lots().values() if lot['remaining_quantity']], 'PLAN_HOLDINGS_CONFLICT')
        _require(plan['status'] == 'PLANNED' and plan['usage_qualified'] is False
                 and plan['real_execution_authorized'] is False
                 and 'SELL_PROCEEDS_NOT_SPENDABLE' in plan['limits'], 'PLAN_QUALIFICATION_CONFLICT')
        def ordering(item):
            selection = item.get('metadata', {}).get('selection')
            score = (selection['score'] * (1 if selection['direction'] == 'ASCENDING' else -1)) if selection else 0.
            return item['side'] != 'SELL', item['strategy_id'], score, item['symbol']
        expected, excluded = [], []
        for item in sorted(decisions, key=ordering):
            if item['side'] == 'HOLD':
                continue
            normalized = {'strategy_id': item['strategy_id'], 'symbol': item['symbol'], 'side': item['side'],
                          'priority': 0, 'target_weight': float(item['target_weight']), 'reason': item['reason']}
            for key in ('metadata', 'signal_key', 'exit_lot_ids'):
                if key in item:
                    normalized[key] = deepcopy(item[key])
            if item['side'] == 'SELL' and not self.quantity(item['symbol']):
                excluded.append({**normalized, 'reason': 'NO_OWNED_POSITION'})
                continue
            normalized['intent_id'] = 'PORTFOLIO_INTENT_' + stable_hash([plan['account_identity'],
                plan['policy_hash'], self.inputs.input_identity, plan['decision_at'], day, len(expected), dict(normalized)])
            expected.append(normalized)
        _tree(plan['intents'], expected, 'PLAN_INTENTS_CONFLICT')
        _tree(plan['excluded'], excluded, 'PLAN_EXCLUSIONS_CONFLICT')
        _require(plan['schema_version'] == ('PORTFOLIO_PAPER_PLAN_V2' if decisions else 'PORTFOLIO_PAPER_PLAN_V1'), 'PLAN_VERSION_CONFLICT')

    def _link(self, item, intent_id, plan):
        return {'signal_key': item['signal_key'], 'parent_intent_id': item['intent_id'],
                'strategy_id': item['strategy_id'], 'symbol': item['symbol'],
                'decision_session': int(_time(plan['decision_at']).strftime('%Y%m%d')),
                'execution_session': plan['next_session']}

    def validate_skip(self, item, intent_id, plan, reason, skips):
        expected = {'intent_id': intent_id, 'reason': reason, **self._link(item, intent_id, plan)}
        _tree(skips.get(intent_id, []), [expected], 'PRECISE_SKIP_CONFLICT')
        self.expected_skip_ids.add(intent_id)

    def validate_order_metadata(self, order, item, intent_id, plan, requested, allocated):
        expected = {'plan_id': plan['plan_id'], 'paper': True, 'target_weight': item['target_weight'],
                    'funnel_version': 'UNIVERSE_SIGNAL_FUNNEL_V1', **self._link(item, intent_id, plan),
                    'requested_quantity': requested, 'allocated_quantity': allocated}
        if 'selection' in item.get('metadata', {}):
            expected['selection'] = item['metadata']['selection']
        if 'broker_rejection_reason' in order['metadata']:
            expected['broker_rejection_reason'] = order['metadata']['broker_rejection_reason']
        _tree(order['metadata'], expected, 'ORIGINAL_QUANTITY_OR_SELECTION_CONFLICT')

    def validate_broker_reason(self, order, reason, quantity):
        if quantity:
            _require('broker_rejection_reason' not in order['metadata'], 'UNEXPECTED_BROKER_REJECTION_REASON')
        else:
            _require(order['metadata'].get('broker_rejection_reason') == reason
                     and order['reason_code'] == reason, 'BROKER_REJECTION_REASON_CONFLICT')

    def validate_allocation(self, item, intent_id, plan, price, pending, opening_cash, opening_equity, portfolio):
        symbol = item['symbol']
        limits = _buy_limits(self, portfolio, symbol, price, pending, opening_cash, opening_equity,
                            0., 0., item['target_weight'], allow_position_limit=True)
        active = {name for name in self.positions if self.quantity(name)}
        pending_buy = [order for order in pending if order['side'] == 'BUY']
        occupied = active | {order['symbol'] for order in pending_buy}
        position_limit = symbol not in occupied and len(occupied) >= portfolio['max_positions']
        if limits is None:
            value = {'version': 'PORTFOLIO_BUY_ALLOCATION_V1', 'requested_quantity': 0,
                'allocated_quantity': 0, 'primary_reason': 'PORTFOLIO_EXIT_BUY_CONFLICT',
                'binding_limits': ['PORTFOLIO_EXIT_BUY_CONFLICT'], 'basis': {}, 'quantity_limits': {}}
            raise ValueError('UNIVERSE_AUDIT_UNEXPECTED_EXIT_BUY_CONFLICT')
        names = {'cash': 'CASH_INCLUDING_FEES', 'symbol': 'SYMBOL_EXPOSURE',
                 'strategy': 'MEMBER_ALLOCATION', 'target': 'TARGET_WEIGHT', 'turnover': 'BUY_TURNOVER'}
        caps = {}
        for key, amount in limits.items():
            cap = int(amount / price / 100) * 100
            if key in ('cash', 'strategy'):
                while cap and cap * price + _fee('BUY', cap, price, self.costs) > amount + 1e-9:
                    cap -= 100
            caps[names[key]] = cap
        requested = int(limits['target'] / price / 100) * 100
        quantity = min(caps.values()) if not position_limit else 0
        bindings = ['POSITION_LIMIT'] if position_limit else []
        bindings += [key for key, cap in caps.items() if cap == quantity and cap < requested]
        if requested == 0:
            bindings += ['TARGET_WEIGHT' if limits['target'] <= 0 else 'LOT_ROUNDING']
        if quantity == 0 and any(0 < amount < price * 100 for amount in limits.values()):
            bindings.append('LOT_ROUNDING')
        bindings = list(dict.fromkeys(bindings))
        pending_cost = pending_gross = pending_symbol = 0.
        exposure = self.quantity(symbol) * price
        strategy_exposure = 0.
        def reference(name):
            raw = self.inputs.bar(name, self._current_day)
            return (_number(raw['open']) * (1 + self.costs['slippage_bps'])
                    if raw is not None and self.inputs.state(name, self._current_day)['suspension_status'] == 'TRADING'
                    else self.prices[name])
        for name in active:
            strategy_exposure += self.quantity(name) * (price if name == symbol else reference(name))
        for order in pending_buy:
            value_price = price if order['symbol'] == symbol else reference(order['symbol'])
            amount = order['remaining'] * value_price
            pending_gross += amount
            pending_cost += amount + _fee('BUY', order['remaining'], value_price, self.costs)
            pending_symbol += amount if order['symbol'] == symbol else 0.
        basis = {'equity': self.equity(), 'ledger_cash': self.cash, 'ledger_reserved_cash': 0.,
            'available_cash': self.cash, 'opening_cash': opening_cash, 'opening_equity': opening_equity,
            'same_open_buy_cost': 0., 'pending_buy_cost': pending_cost, 'pending_buy_gross': pending_gross,
            'symbol_exposure': exposure, 'pending_symbol_exposure': pending_symbol,
            'member_exposure': strategy_exposure, 'pending_member_cost': pending_cost,
            'member_symbol_exposure': exposure, 'pending_member_symbol_exposure': pending_symbol,
            'occupied_position_keys': [[self.strategy.strategy_id, name] for name in sorted(occupied)],
            'max_positions': portfolio['max_positions'], 'position_limit': position_limit,
            'member_weight_bps': 10000, 'target_weight': float(item['target_weight']),
            'max_symbol_exposure_bps': portfolio['max_symbol_exposure_bps'],
            'max_buy_turnover_bps': portfolio['max_buy_turnover_bps'], 'lot_size': 100,
            'estimated_price': price, 'limits': limits}
        value = {'version': 'PORTFOLIO_BUY_ALLOCATION_V1', 'requested_quantity': requested,
            'allocated_quantity': quantity, 'primary_reason': bindings[0] if bindings else 'ALLOCATED',
            'binding_limits': bindings, 'basis': basis, 'quantity_limits': caps,
            'estimated_fee': _fee('BUY', quantity, price, self.costs) if quantity else 0.}
        expected = {'intent_id': intent_id, **self._link(item, intent_id, plan), **value}
        _tree(self.day_allocations.get(intent_id, []), [expected], 'ALLOCATION_BASIS_OR_REASON_CONFLICT')
        self.expected_allocation_ids.add(intent_id)
        return value


def _reconstruct(bundle, window, result, *, initial_cash, costs, strategy_id,
                 rule, audit_checkpoint_path, segment_seconds, resources):
    """逐日读取执行原件，以自己的字典账务核对后提交独立审计收盘。"""
    from .formal_account_backend_v1 import normalized_costs
    from .research_rule_strategy_v3 import ResearchRuleStrategyV3
    from .research_rule_strategy_v4 import ResearchRuleStrategyV4
    from .universe_account_inputs_v1 import _prepare_owned_universe_account_inputs_v1
    from .universe_execution_artifacts_v1 import ArtifactSequence, hydrated_result
    started = time.monotonic()
    result = hydrated_result(result)
    strategy_type = ResearchRuleStrategyV4 if rule.get('version') == 'RESEARCH_RULE_STRATEGY_V4' else ResearchRuleStrategyV3
    strategy = strategy_type(rule, strategy_id=strategy_id)
    costs = normalized_costs(costs)
    inputs = _prepare_owned_universe_account_inputs_v1(bundle, window,
        required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
    _require(result['execution_version'] == BACKEND and result['profile'] == inputs.bundle['profile']
             and result['input_identity'] == inputs.input_identity, 'INPUT_OR_BACKEND_IDENTITY_CONFLICT')
    _require(result['strategy_qualified'] is False and result['independent_confirmation_eligible'] is False,
             'UNSUPPORTED_QUALIFICATION')
    policy, portfolio = _policy(result, inputs, strategy, initial_cash, costs, backend=BACKEND)
    own_source = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                  for name in ('universe_evidence_v1.py', 'universe_evidence_v2.py')}
    identity = stable_hash({'version': VERSION, 'input_identity': inputs.input_identity,
        'execution_identity': result['execution_identity'], 'rule_identity': strategy.rule_identity,
        'artifacts': result['artifacts'], 'final_account_checkpoint': result['final_account_checkpoint'],
        'metrics': result['metrics'], 'source': own_source, 'costs': costs, 'initial_cash': initial_cash})
    checkpoint_path = Path(audit_checkpoint_path).absolute()
    _require(checkpoint_path.resolve() == checkpoint_path, 'OWN_STATE_PATH_REDIRECTED')
    first = inputs.session_index(inputs.window['account_start'])
    days = list(inputs.calendar[first:])
    day_reader = ArtifactSequence(result['artifacts'], 'scan')
    _require(first > 0 and [row['date'] for row in result['artifacts']['days']] == days,
             'ACCOUNT_OR_SCAN_DATES_CONFLICT')
    previous = _read(checkpoint_path, identity)
    if previous and previous.get('complete'):
        _verify_prior_artifact_bytes(day_reader, len(day_reader))
        inputs.assert_unchanged()
        return previous['evidence']
    frames = _ConditionTable(strategy, inputs, checkpoint_path.parent / (checkpoint_path.stem + '_OWN_FEATURES'), identity)
    resources.append(frames.values)
    _tree(result['scan_preparation'], frames.preparation, 'SCAN_PREPARATION_CONFLICT')
    scanner_identity = stable_hash({'version': 'UNIVERSE_SIGNAL_SCAN_V2', 'input_identity': inputs.input_identity,
        'rule_identity': strategy.rule_identity, 'preparation': frames.preparation})
    _require(result['scanner_identity'] == scanner_identity, 'SCANNER_IDENTITY_CONFLICT')
    _require(result['execution_identity'] == stable_hash({'description': result['execution_description'],
        'rule': strategy.rule_identity, 'scanner': scanner_identity}), 'EXECUTION_IDENTITY_CONFLICT')
    checkpoint, fills = result['final_account_checkpoint'], result['fills']
    economic = checkpoint['economic']
    _require(economic['trades'] == fills and not economic['open_order_ids']
             and not checkpoint['invariant_errors'], 'FINAL_EXPORT_OR_OPEN_ORDERS_CONFLICT')
    _require(len(economic['snapshots']) == 2 * len(days), 'SNAPSHOT_COVERAGE_CONFLICT')
    _require(len({trade['trade_id'] for trade in fills}) == len(fills), 'DUPLICATE_TRADE')
    by_day, orders_by_day = defaultdict(list), defaultdict(list)
    previous_time = None
    for trade in fills:
        stamp = _time(trade['fill_time'])
        day = int(stamp.strftime('%Y%m%d'))
        _require(day in days and stamp == _stamp(day) and trade['symbol'] in inputs.symbols
                 and trade['strategy_id'] == strategy_id and (previous_time is None or previous_time <= stamp), 'FILL_SCOPE_OR_TIME_CONFLICT')
        by_day[day].append(trade)
        previous_time = stamp
    for key, order in economic['orders'].items():
        day = int(_time(order['created_at']).strftime('%Y%m%d'))
        _require(key == order['order_id'] and day in days, 'ORDER_SCOPE_CONFLICT')
        orders_by_day[day].append(order)
    for orders in orders_by_day.values():
        orders.sort(key=lambda order: order['sequence'])
    account = _ReconstructionV2(inputs, strategy, _number(initial_cash), costs, policy)
    decisions, daily, peak, drawdown, previous_equity, last_index = [], [], float(initial_cash), 0., float(initial_cash), -1
    if previous:
        _require(set(previous['state']) == set(_STATE_FIELDS) and previous['origin'] == 'INDEPENDENT_RECONSTRUCTION_ONLY', 'OWN_STATE_SCOPE_CONFLICT')
        for name, value in previous['state'].items():
            setattr(account, name, _unpack(value))
        decisions = _unpack(previous['decisions'])
        daily, peak, drawdown, previous_equity, last_index = (previous[name] for name in
            ('daily_accounts', 'peak', 'drawdown', 'previous_equity', 'last_index'))
        _require(type(last_index) is int and -1 <= last_index < len(days)
                 and [row['date'] for row in daily] == days[:last_index + 1], 'OWN_STATE_SESSION_CONFLICT')
        _verify_prior_artifact_bytes(day_reader, last_index + 1)
    def save(index, complete=False, evidence=None):
        state = {name: _pack(getattr(account, name)) for name in _STATE_FIELDS}
        body = {'version': VERSION, 'identity': identity, 'origin': 'INDEPENDENT_RECONSTRUCTION_ONLY',
            'last_index': index, 'state': state, 'decisions': _pack(decisions), 'daily_accounts': daily,
            'peak': peak, 'drawdown': drawdown, 'previous_equity': previous_equity, 'complete': complete}
        if evidence is not None:
            body['evidence'] = evidence
        _write(checkpoint_path, body)
    def pause(index):
        save(index)
        from .universe_account_backend_v2 import SegmentBoundary
        boundary = SegmentBoundary('UNIVERSE_AUDIT_NEXT_SEGMENT')
        boundary.phase = 'AUDIT'
        raise boundary
    for index, day in enumerate(days):
        if index <= last_index:
            continue
        if segment_seconds is not None and time.monotonic() - started >= segment_seconds:
            pause(index - 1)
        account.expected_skip_ids, account.expected_allocation_ids = set(), set()
        account.day_allocations = defaultdict(list)
        packet = day_reader.read_day(index)
        previous_day = inputs.calendar[first + index - 1]
        _require(packet['account']['date'] == packet['scan']['date'] == day
                 and packet['decision']['date'] == previous_day, 'ACCOUNT_OR_SCAN_DATES_CONFLICT')
        for allocation in packet['allocation_records']:
            account.day_allocations[allocation['intent_id']].append(allocation)
        skips = defaultdict(list)
        for skip in packet['skipped_intents']:
            skips[skip['intent_id']].append(skip)
        decision_row = packet['decision']
        _tree(decision_row['decisions'], decisions, 'CONSUMED_DECISIONS_CONFLICT')
        account.validate_plan(decision_row['plan'], previous_day, day, decisions, portfolio)
        account.open_actions(day)
        account.mark(day, opening=True)
        _execute_day(account, day, decision_row['plan'], orders_by_day[day], by_day[day],
                     portfolio, previous_equity, skips)
        _require(set(skips) == account.expected_skip_ids, 'UNEXPECTED_PRECISE_SKIP')
        _require(set(account.day_allocations) == account.expected_allocation_ids, 'UNEXPECTED_ALLOCATION')
        _tree(economic['snapshots'][2 * index], account.snapshot(day, opening=True), 'OPEN_SNAPSHOT_CONFLICT')
        account.record_actions(day)
        account.mark(day)
        close_snapshot = account.snapshot(day)
        _tree(economic['snapshots'][2 * index + 1], close_snapshot, 'CLOSE_SNAPSHOT_CONFLICT')
        equity = account.equity()
        positions = [{'strategy_id': strategy_id, 'symbol': symbol, 'quantity': account.quantity(symbol)}
                     for symbol in sorted(account.positions)]
        stale = [{'symbol': symbol, 'status': 'STALE_VERIFIED_SUSPENSION'} for symbol in account.positions
                 if account.quantity(symbol) and inputs.state(symbol, day)['suspension_status'] == 'SUSPENDED']
        reconstructed = {'date': day, 'cash': account.cash, 'equity': equity, 'positions': positions,
                         'stale_valuations': stale}
        _tree(packet['account'], reconstructed, 'DAILY_ACCOUNT_CONFLICT')
        daily.append(reconstructed)
        peak, previous_equity = max(peak, equity), close_snapshot['equity']
        drawdown = max(drawdown, 1 - equity / peak)
        decisions, rows = account.decisions(day, frames)
        scan = packet['scan']
        _require(scan['target'] == scan['processed'] == len(inputs.symbols)
                 and [row['symbol'] for row in scan['rows']] == list(inputs.symbols), 'FULL_SCAN_COVERAGE_CONFLICT')
        _tree(scan['rows'], rows, 'SCAN_CONDITIONS_OR_DECISIONS_CONFLICT')
        _require(scan['identity'] == stable_hash(rows), 'SCAN_IDENTITY_CONFLICT')
        save(index)
    evidence = _final_account_audit(account, days, checkpoint, economic, fills, result, daily,
                                    drawdown, strategy, inputs, initial_cash, scanner_identity)
    evidence['method_scopes'].update(selection='INDEPENDENT_CLOSE_SCORE_ORDER_AND_IDENTITY',
        allocations='INDEPENDENT_LIMITS_FEES_PENDING_CASH_AND_ORIGINAL_QUANTITY',
        continuation='OWN_PREVIOUSLY_VERIFIED_CLOSE_STATE_ONLY')
    save(len(days) - 1, complete=True, evidence=evidence)
    return evidence


def reconstruct_universe_account_v2(bundle, window, result, *, initial_cash, costs, strategy_id,
                                     rule, audit_checkpoint_path=None, segment_seconds=None):
    """即使核验拒绝，Windows 数值映射也先关闭，再清理临时目录。"""
    resources = []
    def run(path):
        try:
            return _reconstruct(bundle, window, result, initial_cash=initial_cash, costs=costs,
                strategy_id=strategy_id, rule=rule, audit_checkpoint_path=path,
                segment_seconds=segment_seconds, resources=resources)
        finally:
            for value in resources:
                value._mmap.close()
    if audit_checkpoint_path is not None:
        return run(audit_checkpoint_path)
    with tempfile.TemporaryDirectory(prefix='universe_own_audit_') as directory:
        return run(Path(directory).resolve() / 'AUDIT.json')
