"""全范围条件扫描：复用 V3 指标与表达式，分片只影响计算组织。"""
from copy import deepcopy
import math

import pandas as pd

from .causal_dividend_features_v1 import causal_hfq_bars
from .common import stable_hash
from .research_rule_strategy_v2 import _field_references, evaluate_condition
from .strategy_interface_v1 import Decision, TargetWeight


VERSION = 'UNIVERSE_SIGNAL_SCAN_V1'


class UniverseSignalScanV1:
    def __init__(self, strategy, inputs, *, batch_size=128, progress=None, allow_data_gaps=False):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError('UNIVERSE_BATCH_SIZE_INVALID')
        if type(allow_data_gaps) is not bool:
            raise ValueError('UNIVERSE_SCAN_DATA_GAP_MODE_INVALID')
        self.strategy, self.inputs = strategy, inputs
        self.conditions = {}
        self.preparation = []
        self._preparation_gaps = {}
        self.progress = progress or (lambda record: None)
        symbols = sorted(inputs.window['symbols'])
        grouped = {str(key): frame.sort_values('date') for key, frame in inputs.bundle['daily'].groupby('symbol', sort=False)}
        turns = {str(key): frame.set_index('date') for key, frame in inputs.bundle['turn'].groupby('symbol', sort=False)}
        for offset in range(0, len(symbols), batch_size):
            for symbol in symbols[offset:offset + batch_size]:
                raw = grouped.get(symbol)
                if raw is None or raw.empty:
                    self.conditions[symbol] = pd.DataFrame(columns=['buy', 'sell', 'market_filter', 'ready'])
                    self.preparation.append({'symbol': symbol, 'status': 'NO_VALID_BARS'})
                    continue
                raw = raw.copy()
                # 零活动停牌记录不进入指标 bar 轴；账户仍保留每个交易 session。
                raw = raw.loc[raw.volume.gt(0)]
                if raw.empty:
                    self.conditions[symbol] = pd.DataFrame(columns=['buy', 'sell', 'market_filter', 'ready'])
                    self.preparation.append({'symbol': symbol, 'status': 'NO_VALID_BARS'})
                    continue
                raw['adjustflag'] = '3'
                actions = tuple(e for e in inputs.bundle['events'] if e['symbol'] == symbol
                                and int(raw.date.min()) < e['effective_date'] <= int(raw.date.max()))
                try:
                    bars, price_trace = causal_hfq_bars(raw, actions)
                except ValueError as error:
                    # 公共纯信号扫描保留该证券未知；账户默认仍要求严格的除息价格证据。
                    if not allow_data_gaps or str(error) != 'CAUSAL_PRICE_EX_DATE_MISSING':
                        raise
                    self.conditions[symbol] = pd.DataFrame(columns=['buy', 'sell', 'market_filter', 'ready'])
                    self._preparation_gaps[symbol] = str(error)
                    self.preparation.append({'symbol': symbol, 'status': 'UNKNOWN', 'reason': str(error)})
                    continue
                bars = bars.set_index('date')
                vendor = turns.get(symbol)
                turn = vendor['turn'].reindex(bars.index) if vendor is not None and 'turn' in vendor else None
                matrix = strategy.build_feature_matrix(bars, turn)
                from ..engine.conditions_v2 import ConditionContext
                values = {f'{alias}.{output}': matrix[f'{alias}.{output}'] for alias, output in strategy.references}
                ready = {key: matrix[key + '__ready'] for key in values}
                fields = {name: matrix[name] for name in _field_references_union(strategy) if name in matrix}
                context = ConditionContext(values, fields, matrix.index, ready=ready)
                conditions = pd.DataFrame({name: evaluate_condition(expr, context)
                                           for name, expr in strategy.expressions.items()}, index=matrix.index)
                if 'market_filter' not in conditions:
                    conditions['market_filter'] = 1.0
                mask = pd.Series(True, index=matrix.index)
                for key, series in values.items():
                    mask &= ready[key].eq(True) & series.map(lambda v: math.isfinite(float(v)))
                for series in fields.values():
                    mask &= series.map(lambda v: math.isfinite(float(v)))
                conditions['ready'] = mask & conditions[['buy', 'sell', 'market_filter']].notna().all(axis=1)
                self.conditions[symbol] = conditions
                self.preparation.append({'symbol': symbol, 'status': 'COMPUTED', 'bars': len(matrix),
                    'conditions_hash': stable_hash(_records(conditions)), 'price_trace': price_trace})
            self.progress({'processed': min(offset + batch_size, len(symbols)), 'target': len(symbols)})
        self.identity = stable_hash({'version': VERSION, 'input_identity': inputs.input_identity,
            'rule_identity': strategy.rule_identity, 'preparation': self.preparation})

    def at(self, symbol, day):
        frame = self.conditions[symbol]
        if day not in frame.index:
            return {'buy': None, 'sell': None, 'market_filter': None, 'ready': False,
                    'reason': self._preparation_gaps.get(symbol, 'NO_COMPLETED_BAR')}
        row = frame.loc[day]
        return {**{key: None if pd.isna(row[key]) else bool(row[key])
                   for key in ('buy', 'sell', 'market_filter')},
                'ready': bool(row['ready']), 'reason': 'COMPUTED'}


def _field_references_union(strategy):
    return {field for expr in strategy.expressions.values() for field in _field_references(expr)}


def _records(frame):
    # 不把 NaN 当 JSON 数值；UNKNOWN 仍保持未知。
    return [{**{str(key): None if pd.isna(value) else bool(value) if key == 'ready' else float(value)
                for key, value in row.items()}, 'date': int(day)} for day, row in frame.iterrows()]


def decision_from_conditions(strategy, truth, *, account, state, index):
    """与 V3 状态型规则同语义，持有/冷却轴使用独立交易所 session。"""
    quantity, entry, last_exit = account['quantity'], account['entry_session_index'], account['last_exit_session_index']
    if state and (state.get('rule_identity') != strategy.rule_identity or state.get('last_session_index') != index - 1):
        raise ValueError('UNIVERSE_RULE_STATE_CONTINUITY')
    updated = {'rule_identity': strategy.rule_identity, 'last_session_index': index,
               'exit_pending': bool(quantity and state.get('exit_pending', False))}
    def answer(reason, weight=None):
        return Decision(reason, None if weight is None else TargetWeight(weight, increase_existing=False),
                        deepcopy(updated), deepcopy(truth))
    if quantity and (entry is None or not 0 <= entry <= index):
        raise ValueError('UNIVERSE_REAL_ENTRY_REQUIRED')
    held = index - entry if quantity else 0
    if quantity and (updated['exit_pending'] or held >= strategy.payload['max_hold_sessions']
                     or (held >= strategy.payload['min_hold_sessions'] and truth['sell'] is True)):
        updated['exit_pending'] = True
        return answer('EXIT_PENDING' if state.get('exit_pending') else 'SELL', 0.)
    if quantity:
        return answer('HOLD')
    if truth['sell'] is True:
        return answer('EXIT_PRIORITY')
    if last_exit is not None and index - last_exit <= strategy.payload['cooldown_sessions']:
        return answer('COOLDOWN')
    if not truth['ready'] or any(truth[k] is None for k in ('buy', 'sell', 'market_filter')):
        return answer('CONDITION_UNKNOWN')
    if not truth['buy'] or not truth['market_filter']:
        return answer('CONDITION_FALSE')
    return answer('BUY', strategy.payload['target_weight'])
