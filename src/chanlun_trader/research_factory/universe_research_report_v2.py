"""全范围研究双报告：实际账户和账户无关的信号观察分开。"""
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import math

import numpy as np
import pandas as pd

from .board_execution_policy_v1 import BoardExecutionPolicyV1
from .common import stable_hash
from .universe_signal_funnel_v1 import is_signal_opportunity
from .universe_report_state_v1 import OwnReportState, at_boundary, deadline, source_identity


VERSION = 'UNIVERSE_RESEARCH_REPORT_V2'
OBSERVATION_VERSION = 'SIGNAL_OBSERVATION_PLAN_V1'
PRICE_POLICY = 'FRACTIONAL_ONE_SHARE_ENTITLED_ECONOMIC_VALUE_V1'
COMPARATOR = 'QUALIFIED_SCOPE_EQUAL_WEIGHT_SAME_ENTRY_HORIZON'


def default_observation_plan(window):
    return {'version': OBSERVATION_VERSION, 'horizons': [5, 10, 20],
        'entry': 'NEXT_EXCHANGE_SESSION_RAW_OPEN', 'exit': 'HORIZON_SESSION_RAW_CLOSE',
        'price_policy': PRICE_POLICY, 'comparator': COMPARATOR,
        'start': window['account_start'], 'end': window['account_end']}


def _day(value):
    return int(pd.Timestamp(value).strftime('%Y%m%d'))


class _Observations:
    """每个收盘/期限的全池对照仅计算一次，不为每个信号重复扫全池。"""
    def __init__(self, inputs):
        self.inputs = inputs
        self.days = tuple(inputs.window['calendar'])
        self.symbols = tuple(inputs.window['symbols'])
        self.symbol_index = {symbol: index for index, symbol in enumerate(self.symbols)}
        shape = (len(self.symbols), len(self.days))
        self.prices = {}
        for field in ('open', 'close', 'prev_close', 'volume'):
            table = inputs.bundle['daily'].pivot(index='symbol', columns='date', values=field)
            self.prices[field] = table.reindex(index=self.symbols, columns=self.days).to_numpy(dtype=float)
        self.open_codes = np.full(shape, -1, dtype=np.int16)
        self.code_reasons = ['OK']
        self.reason_codes = {'OK': 0}
        self.events = {symbol: [] for symbol in self.symbols}
        for event in inputs.bundle['events']:
            if event['symbol'] in self.events:
                self.events[event['symbol']].append(event)
        self.policy = BoardExecutionPolicyV1(calendar=self.days, listing_dates=inputs.bundle.get('listing_dates'))
        self.comparators = {}

    def _opening_reason(self, security, index):
        code = self.open_codes[security, index]
        if code >= 0:
            return self.code_reasons[code]
        symbol, day = self.symbols[security], self.days[index]
        state = self.inputs.state(symbol, day)
        opening = self.prices['open'][security, index]
        previous = self.prices['prev_close'][security, index]
        if not state['state_known']:
            reason = 'OPEN_SECURITY_STATE_UNKNOWN'
        elif not state['listed'] or state['delisted']:
            reason = 'OPEN_SECURITY_NOT_LISTED'
        elif state['suspension_status'] != 'TRADING':
            reason = 'OPEN_SUSPENDED'
        elif (not state['universe_member'] or state['eligibility_status'] != 'ELIGIBLE'
                or state['st_status'] != 'NORMAL'):
            reason = 'OPEN_SECURITY_NOT_ELIGIBLE'
        elif not math.isfinite(opening) or opening <= 0 or not math.isfinite(previous) or previous <= 0:
            reason = 'NEXT_SESSION_RAW_OPEN_MISSING'
        elif index == 0 or not math.isfinite(self.prices['volume'][security, index - 1]) or self.prices['volume'][security, index - 1] <= 0:
            reason = 'OPEN_LIQUIDITY_REFERENCE_UNAVAILABLE'
        else:
            try:
                regime = self.policy.resolve(symbol, day, state)
                up, down = regime.limit_prices(float(previous))
                if regime.regime_id == 'LEGACY_IPO_FIRST_SESSION':
                    reason = 'UNSUPPORTED_LEGACY_IPO_OPENING_DAY'
                elif up is not None and (opening > up or opening < down):
                    reason = 'OPEN_OUTSIDE_DAILY_PRICE_LIMIT'
                elif up is not None and opening == up:
                    reason = 'LIMIT_UP_OPEN_DAILY_CONSERVATIVE'
                else:
                    reason = 'OK'
            except ValueError:
                reason = 'OPEN_BOARD_POLICY_UNKNOWN'
        if reason not in self.reason_codes:
            self.reason_codes[reason] = len(self.code_reasons)
            self.code_reasons.append(reason)
        self.open_codes[security, index] = self.reason_codes[reason]
        return reason

    def _economic_value(self, symbol, entry, end, close):
        quantity, paid, receivable, uncredited, locked = 1., 0., 0., 0., 0.
        captures, timeline = {}, []
        for event in self.events[symbol]:
            if entry <= event['record_date'] <= end:
                timeline.append((event['record_date'], 1, event['event_id'], 'RECORD', event))
                if event['effective_date'] <= end:
                    timeline.append((event['effective_date'], 0 if event['event_type'] == 'CASH_DIVIDEND' else .5,
                        event['event_id'], 'EFFECTIVE', event))
        for _, _, key, phase, event in sorted(timeline, key=lambda item: item[:3]):
            if phase == 'RECORD':
                captures[key] = quantity
                continue
            if key not in captures or not isinstance(event.get('source'), str) or event['source'] in {'', 'UNKNOWN'}:
                return None, 'ACTION_ENTITLEMENT_OR_SOURCE_UNKNOWN'
            terms = event.get('terms', {})
            if event['event_type'] == 'CASH_DIVIDEND':
                cash = terms.get('cash_per_share')
                if type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0 or type(event.get('payment_date')) is not int:
                    return None, 'ACTION_CASH_TERMS_UNKNOWN'
                amount = captures[key] * cash
                if event['payment_date'] <= end:
                    paid += amount
                else:
                    receivable += amount
            elif event['event_type'] in {'BONUS', 'CAPITALIZATION'}:
                numerator, denominator = terms.get('ratio_numerator'), terms.get('ratio_denominator')
                if (type(numerator) is not int or type(denominator) is not int or denominator <= 0
                        or numerator <= denominator or type(event.get('share_credit_date')) is not int
                        or type(event.get('tradable_date')) is not int):
                    return None, 'ACTION_SHARE_TERMS_UNKNOWN'
                amount = captures[key] * (numerator / denominator - 1)
                quantity += amount
                if event['share_credit_date'] > end:
                    uncredited += amount
                if event['tradable_date'] > end:
                    locked += amount
            else:
                return None, 'ACTION_TYPE_UNSUPPORTED'
        return {'share_quantity': quantity, 'paid_gross_cash': paid, 'gross_cash_receivable': receivable,
            'uncredited_shares': uncredited, 'locked_shares': locked,
            'economic_value': quantity * close + paid + receivable}, None

    def observe(self, symbol, decision_index, horizon):
        entry_index, end_index = decision_index + 1, decision_index + horizon
        base = {'symbol': symbol, 'decision_session': self.days[decision_index], 'horizon_sessions': horizon,
            'entry_session': self.days[entry_index] if entry_index < len(self.days) else None,
            'end_session': self.days[end_index] if end_index < len(self.days) else None,
            'status': 'CENSORED', 'reason': None, 'return': None}
        if entry_index >= len(self.days):
            return {**base, 'reason': 'END_OF_OBSERVATION_NO_NEXT_SESSION'}
        if end_index >= len(self.days):
            return {**base, 'reason': 'HORIZON_OUTSIDE_OBSERVATION'}
        security = self.symbol_index[symbol]
        reason = self._opening_reason(security, entry_index)
        if reason != 'OK':
            return {**base, 'reason': reason}
        opening, close = self.prices['open'][security, entry_index], self.prices['close'][security, end_index]
        if not math.isfinite(close) or close <= 0:
            return {**base, 'status': 'UNKNOWN', 'reason': 'HORIZON_RAW_CLOSE_MISSING'}
        value, reason = self._economic_value(symbol, self.days[entry_index], self.days[end_index], close)
        if reason:
            return {**base, 'status': 'UNKNOWN', 'reason': reason}
        return {**base, 'status': 'OBSERVED', 'reason': 'OK', 'entry_raw_open': opening,
            'end_raw_close': close, **value, 'return': value['economic_value'] / opening - 1}

    def comparator(self, decision_index, horizon):
        key = decision_index, horizon
        if key not in self.comparators:
            total, count, censored = 0., 0, Counter()
            for symbol in self.symbols:
                row = self.observe(symbol, decision_index, horizon)
                if row['status'] == 'OBSERVED':
                    total += row['return']
                    count += 1
                else:
                    censored[row['reason']] += 1
            self.comparators[key] = {'status': 'AVAILABLE' if count else 'NO_COMPARABLE_OBSERVATIONS',
                'return': total / count if count else None, 'observed': count,
                'scope_count': len(self.symbols), 'excluded': dict(sorted(censored.items())), 'policy': COMPARATOR}
        return self.comparators[key]


def _account_episodes(result, inputs):
    economic = result['final_account_checkpoint']['economic']
    events = {event['event_id']: event for event in inputs.bundle['events']}
    activity, active, episodes, lot_episodes = [], {}, [], {}
    for trade in result['fills']:
        if trade.get('reality_flag', 'OK') != 'OK':
            raise ValueError('REPORT_UNSUPPORTED_ECONOMIC_TRADE')
        activity.append((_day(trade['fill_time']), 1, trade['fill_time'], 'TRADE', trade))
    for action in economic.get('action_audit', []):
        event = events.get(action['event_id'])
        if event and event['event_type'] in {'BONUS', 'CAPITALIZATION'} and action['phase'] == 'EFFECTIVE':
            activity.append((_day(action['timestamp']), 0, action['timestamp'], 'SHARES', {'event': event, 'audit': action}))
    for day, _, _, kind, item in sorted(activity, key=lambda row: row[:3]):
        symbol = item['symbol'] if kind == 'TRADE' else item['event']['symbol']
        if kind == 'SHARES':
            episode = active.get(symbol)
            if episode is not None:
                event = item['event']
                added = item['audit']['held_quantity'] * (Fraction(event['terms']['ratio_numerator'],
                    event['terms']['ratio_denominator']) - 1)
                if added.denominator != 1:
                    raise ValueError('REPORT_EPISODE_FRACTIONAL_SHARE_CONFLICT')
                episode['quantity'] += int(added)
            continue
        side = getattr(item['side'], 'value', item['side'])
        episode = active.get(symbol)
        if side == 'BUY':
            if episode is None:
                episode = {'episode_id': stable_hash([symbol, item['trade_id']]), 'symbol': symbol,
                    'start_session': day, 'end_session': None, 'quantity': 0,
                    'buy_cash': 0., 'sell_cash': 0., 'fees': 0., 'cash_dividend': 0., 'dividend_tax': 0.,
                    'trade_ids': [], 'status': 'OPEN'}
                episodes.append(episode)
                active[symbol] = episode
            episode['quantity'] += item['quantity']
            episode['buy_cash'] += item['gross_value'] + item['fee']
            if item.get('lot_id'):
                lot_episodes[item['lot_id']] = episode
        elif side == 'SELL':
            if episode is None or item['quantity'] > episode['quantity']:
                raise ValueError('REPORT_EPISODE_QUANTITY_CONFLICT')
            episode['quantity'] -= item['quantity']
            episode['sell_cash'] += item['gross_value'] - item['fee']
            if episode['quantity'] == 0:
                episode.update(status='CLOSED', end_session=day)
                del active[symbol]
        else:
            raise ValueError('REPORT_TRADE_SIDE_INVALID')
        episode['fees'] += item['fee']
        episode['trade_ids'].append(item['trade_id'])
    parents = economic.get('bonus_parent_lots', {})
    for child, parent in parents.items():
        while parent in parents:
            parent = parents[parent]
        if parent in lot_episodes:
            lot_episodes[child] = lot_episodes[parent]
    for event_id, quantity in economic.get('entitlements', {}).items():
        event = events.get(event_id)
        if not event or event['event_type'] != 'CASH_DIVIDEND' or event_id not in economic.get('applied', []):
            continue
        eligible = [episode for episode in episodes if episode['symbol'] == event['symbol']
            and episode['start_session'] <= event['record_date']
            and (episode['end_session'] is None or episode['end_session'] > event['record_date'])]
        if quantity and len(eligible) != 1:
            raise ValueError('REPORT_DIVIDEND_EPISODE_ATTRIBUTION_UNKNOWN')
        if quantity:
            terms, amount = event['terms'], float(event['terms']['cash_per_share'])
            if terms['tax_rule']['kind'] == 'EXPLICIT_FIXED_PER_SHARE':
                amount -= float(terms['tax_rule']['tax_per_share'])
            eligible[0]['cash_dividend'] += quantity * amount
    for action in economic.get('action_audit', []):
        if action['phase'] not in {'DEFERRED_INDIVIDUAL_TAX', 'DEFERRED_SHARE_TAX'}:
            continue
        episode = lot_episodes.get(action['lot_id'])
        if episode is None:
            raise ValueError('REPORT_TAX_EPISODE_ATTRIBUTION_UNKNOWN')
        episode['dividend_tax'] += action['amount']
    for episode in episodes:
        episode['net_profit'] = (episode['sell_cash'] + episode['cash_dividend'] - episode['dividend_tax']
            - episode['buy_cash']) if episode['status'] == 'CLOSED' else None
    return episodes


def _account_report(result, inputs, summary=None):
    initial = float(result['account_policy']['initial_cash'])
    last, count, empty, occupied, cash_sum, exposure_sum, peak, drawdown = None, 0, 0, 0, 0., 0., initial, 0.
    curve = []
    if summary is None:
        for day in result['daily_accounts']:
            curve.append({'date': day['date'], 'equity': day['equity']})
            if not math.isfinite(day['cash']) or not math.isfinite(day['equity']) or day['equity'] <= 0:
                raise ValueError('REPORT_ACCOUNT_VALUE_INVALID')
            last, count = day, count + 1
            slots = sum(position['quantity'] > 0 for position in day['positions'])
            empty += slots == 0
            occupied += slots
            cash_sum += day['cash']
            exposure_sum += max(0., (day['equity'] - day['cash']) / day['equity'])
            peak = max(peak, day['equity'])
            drawdown = max(drawdown, 1 - day['equity'] / peak)
    else:
        curve = summary.get('daily_equity', [])
        last, count, empty, occupied, cash_sum, exposure_sum, peak, drawdown = (
            summary[key] for key in ('last', 'count', 'empty', 'occupied', 'cash_sum', 'exposure_sum', 'peak', 'drawdown'))
    if last is None:
        raise ValueError('REPORT_ACCOUNT_DAYS_REQUIRED')
    episodes = _account_episodes(result, inputs)
    closed = [episode for episode in episodes if episode['status'] == 'CLOSED']
    profits = sorted((episode['net_profit'] for episode in closed), reverse=True)
    closed_profit = sum(profits)
    economic = result['final_account_checkpoint']['economic']
    open_cost = sum(lot['cost'] * (lot['remaining_quantity'] / lot['quantity'])
        for lot in economic.get('lots', {}).values() if lot['remaining_quantity'])
    market_value = last['equity'] - last['cash'] - sum(economic.get('receivables', {}).values())
    return {'question': '实际本金按规则交易后的账户结果', 'initial_cash': initial,
        'sessions': count, 'final_cash': last['cash'], 'final_equity': last['equity'],
        'net_profit': last['equity'] - initial, 'net_return': last['equity'] / initial - 1,
        'max_drawdown': drawdown, 'total_fees': sum(fill['fee'] for fill in result['fills']),
        'dividend_tax': economic.get('dividend_tax_withheld', 0.),
        'share_tax': economic.get('share_tax_withheld', 0.), 'trade_count': len(result['fills']),
        'empty_sessions': empty, 'empty_fraction': empty / count, 'average_slots': occupied / count,
        'average_cash': cash_sum / count, 'average_equity_less_cash_fraction': exposure_sum / count,
        'closed_episodes': len(closed), 'winning_closed_episodes': sum(profit > 0 for profit in profits),
        'closed_episode_win_rate': sum(profit > 0 for profit in profits) / len(profits) if profits else None,
        'closed_episode_net_profit': closed_profit,
        'without_best_1_closed_episode_profit': closed_profit - sum(profits[:1]),
        'without_best_3_closed_episode_profit': closed_profit - sum(profits[:3]),
        'concentration_measure': 'SORTED_EPISODE_PNL_ATTRIBUTION_NOT_COUNTERFACTUAL_BACKTEST',
        'open_episodes': len(episodes) - len(closed), 'end_positions': deepcopy(last['positions']),
        'end_cash_receivable': sum(economic.get('receivables', {}).values()),
        'end_equity_less_cash_and_receivables': last['equity'] - last['cash'] - sum(economic.get('receivables', {}).values()),
        'end_open_market_value_projection': market_value, 'end_open_cost_basis': open_cost,
        'end_open_unrealized_profit_projection': market_value - open_cost,
        'episodes': episodes, 'reconciliation': deepcopy(result.get('reconciliation', {})),
        'business_diagnostics': _business_diagnostics(curve, initial, episodes),
        'qualification': 'NOT_ASSESSED', 'strategy_qualified': False}


def _business_diagnostics(curve, initial, episodes):
    """固定前后两半连续账户归因；未平仓权益保留，不是重新开两个账户。"""
    if not curve:
        raise ValueError('REPORT_BUSINESS_ACCOUNT_CURVE_REQUIRED')
    periods = []
    boundary = max(1, len(curve) // 2)
    for name, start, end in (('FIRST_HALF', 0, boundary), ('SECOND_HALF', boundary, len(curve))):
        rows = curve[start:end]
        if not rows:
            continue
        opening = initial if start == 0 else curve[start - 1]['equity']
        peak, drawdown = opening, 0.
        for row in rows:
            peak = max(peak, row['equity'])
            drawdown = max(drawdown, 1 - row['equity'] / peak)
        periods.append({'period': name, 'account_start': rows[0]['date'], 'account_end': rows[-1]['date'],
            'sessions': len(rows), 'opening_equity': opening, 'closing_equity': rows[-1]['equity'],
            'net_profit': rows[-1]['equity'] - opening,
            'net_return': rows[-1]['equity'] / opening - 1, 'maximum_drawdown': drawdown})
    closed = [row for row in episodes if row['status'] == 'CLOSED']
    symbol = Counter()
    for row in closed:
        symbol[row['symbol']] += row['net_profit']
    profits = sorted((row['net_profit'] for row in closed), reverse=True)
    return {'version': 'CONTINUOUS_BUSINESS_DIAGNOSTICS_V1',
        'account_dates': [row['date'] for row in curve],
        'subperiod_policy': 'FROZEN_ACCOUNT_SESSION_HALVES_CONTINUOUS_EQUITY', 'subperiods': periods,
        'concentration': {'symbol': dict(sorted(symbol.items())),
            'complete_round_trip': {'count': len(closed), 'net_profit': sum(profits),
                'without_best_1_profit': sum(profits[1:]), 'without_best_3_profit': sum(profits[3:]),
                'policy': 'ATTRIBUTION_NOT_COUNTERFACTUAL_ACCOUNT'},
            'time_period': periods},
        'benchmarks': {'CASH': {'net_return': 0., 'interest_assumed': 0.},
            'PRICE_REFERENCE': {'policy': 'SYSTEM_SUPPORTED_PRICE_REFERENCE_WITH_INVESTABILITY_DISCLOSED',
                'metrics_source': 'PUBLIC_FINAL_REPORT_BENCHMARK', 'investable_claim': False}},
        'equity_includes_open_positions': True}


def _statistic():
    return {'observed': 0, 'sum_return': 0., 'wins': 0, 'comparable': 0,
        'sum_excess': 0., 'positive_excess': 0, 'censored': Counter()}


def _add_observation(stat, observed, comparator):
    if observed['status'] != 'OBSERVED':
        stat['censored'][observed['reason']] += 1
        return
    stat['observed'] += 1
    stat['sum_return'] += float(observed['return'])
    stat['wins'] += int(observed['return'] > 0)
    if comparator['return'] is not None:
        excess = observed['return'] - comparator['return']
        stat['comparable'] += 1
        stat['sum_excess'] += float(excess)
        stat['positive_excess'] += int(excess > 0)


def _finish_statistic(stat):
    stat['mean_return'] = stat.pop('sum_return') / stat['observed'] if stat['observed'] else None
    stat['win_rate'] = stat['wins'] / stat['observed'] if stat['observed'] else None
    stat['mean_excess_return'] = stat.pop('sum_excess') / stat['comparable'] if stat['comparable'] else None
    stat['positive_excess_rate'] = stat['positive_excess'] / stat['comparable'] if stat['comparable'] else None
    stat['censored'] = dict(sorted(stat['censored'].items()))


def build_research_reports(result, inputs, observation_plan, *, event_sink=None,
                           report_checkpoint_path=None, segment_seconds=None):
    """只在调用方已授权的评价阶段读取 raw 资料；不创建或扩大研究预算。"""
    limit = deadline(segment_seconds)
    if observation_plan != default_observation_plan(inputs.window):
        raise ValueError('SIGNAL_OBSERVATION_PLAN_OR_SCOPE_INVALID')
    if result.get('input_identity') != inputs.input_identity:
        raise ValueError('REPORT_INPUT_IDENTITY_CONFLICT')
    if event_sink is not None and not callable(event_sink):
        raise ValueError('REPORT_EVENT_SINK_INVALID')
    if report_checkpoint_path is None and segment_seconds is not None:
        raise ValueError('REPORT_SEGMENT_CHECKPOINT_REQUIRED')
    if report_checkpoint_path is not None and event_sink is not None:
        raise ValueError('REPORT_CONTINUATION_OWNS_DETAIL_SINK')
    horizons = observation_plan['horizons']
    statistics = {kind: {str(horizon): _statistic()
        for horizon in horizons} for kind in ('SECURITY_DAY', 'CONDITION_EPISODE')}
    parameters = result.get('strategy_plan', {}).get('strategy', {}).get('parameters', {})
    selection = parameters.get('candidate_payload', {}).get('selection')
    score_direction = selection.get('direction') if isinstance(selection, dict) else None
    top_count = result['account_policy'].get('portfolio', {}).get('max_positions')
    score_groups = ('TOP_FROZEN_SLOT_COUNT', 'OTHER_KNOWN_SCORE', 'SCORE_UNKNOWN') if (
        score_direction in {'ASCENDING', 'DESCENDING'} and type(top_count) is int and top_count > 0
        ) else ('KNOWN_SCORE', 'SCORE_UNKNOWN')
    score_statistics = {group: {str(horizon): _statistic() for horizon in horizons} for group in score_groups}
    trackers, condition_counts, score_unknown, opportunities, episode_count = {}, Counter(), 0, 0, 0
    uncertain_episodes = set()
    detail_count, detail_hash = 0, stable_hash({'version': VERSION, 'input': inputs.input_identity,
        'observation_plan': observation_plan})
    first_scan = None
    store = None
    observation_cursor = 0
    if report_checkpoint_path is not None:
        # 新版原件用不可变 manifest 绑定；不把延迟读取序列物化成整窗 Python 字典。
        excluded = {'daily_accounts', 'scan_days', 'decisions'} if result.get('artifacts') else set()
        binding = {'input_identity': inputs.input_identity, 'window': inputs.window,
            'observation_plan': observation_plan,
            'result_identity': stable_hash({key: value for key, value in result.items() if key not in excluded}),
            'source': source_identity(('universe_research_report_v2.py', 'universe_report_state_v1.py',
                'universe_signal_funnel_v1.py', 'universe_execution_artifacts_v1.py',
                'board_execution_policy_v1.py', 'common.py'))}
        initial = {'phase': 'ACCOUNT', 'account_cursor': 0,
            'account_summary': {'last': None, 'count': 0, 'empty': 0, 'occupied': 0, 'cash_sum': 0.,
                'exposure_sum': 0., 'peak': float(result['account_policy']['initial_cash']), 'drawdown': 0.,
                'daily_equity': []},
            'account_report': None, 'observation_cursor': 0, 'statistics': statistics,
            'score_statistics': score_statistics, 'trackers': trackers, 'condition_counts': {},
            'score_unknown': 0, 'opportunities': 0, 'episode_count': 0, 'uncertain_episodes': [],
            'detail_count': detail_count, 'detail_hash': detail_hash, 'first_scan': None}
        store = OwnReportState(report_checkpoint_path, kind=VERSION, binding=binding, initial=initial,
                               source_artifacts=result.get('artifacts'))
        if store.complete:
            return store.output
        at_boundary(limit, 'UNIVERSE_REPORT_NEXT_SEGMENT')
        saved = store.state
        if saved['observation_cursor'] != len(store.details.days):
            raise ValueError('REPORT_CHECKPOINT_CURSOR_CONFLICT')
        if saved['phase'] == 'ACCOUNT':
            summary = saved['account_summary']
            for index in range(saved['account_cursor'], len(result['daily_accounts'])):
                day = result['daily_accounts'][index]
                if not math.isfinite(day['cash']) or not math.isfinite(day['equity']) or day['equity'] <= 0:
                    raise ValueError('REPORT_ACCOUNT_VALUE_INVALID')
                slots = sum(position['quantity'] > 0 for position in day['positions'])
                summary.update(last=day, count=summary['count'] + 1,
                    empty=summary['empty'] + (slots == 0), occupied=summary['occupied'] + slots,
                    cash_sum=summary['cash_sum'] + day['cash'],
                    exposure_sum=summary['exposure_sum'] + max(0., (day['equity'] - day['cash']) / day['equity']),
                    peak=max(summary['peak'], day['equity']))
                summary['drawdown'] = max(summary['drawdown'], 1 - day['equity'] / summary['peak'])
                summary['daily_equity'].append({'date': day['date'], 'equity': day['equity']})
                saved['account_cursor'] = index + 1
                store.save()
                at_boundary(limit, 'UNIVERSE_REPORT_NEXT_SEGMENT')
            saved['account_report'] = _account_report(result, inputs, summary)
            saved['phase'] = 'OBSERVATIONS'
            store.save()
        account = saved['account_report']
        statistics, score_statistics = saved['statistics'], saved['score_statistics']
        for group in [*statistics.values(), *score_statistics.values()]:
            for stat in group.values():
                stat['censored'] = Counter(stat['censored'])
        trackers, condition_counts = saved['trackers'], Counter(saved['condition_counts'])
        score_unknown, opportunities, episode_count = (saved[key] for key in
            ('score_unknown', 'opportunities', 'episode_count'))
        uncertain_episodes = set(saved['uncertain_episodes'])
        detail_count, detail_hash, first_scan = (saved[key] for key in ('detail_count', 'detail_hash', 'first_scan'))
        observation_cursor = saved['observation_cursor']
    else:
        account = _account_report(result, inputs)
    observations = _Observations(inputs)
    indices = {day: index for index, day in enumerate(observations.days)}
    if store is not None:
        at_boundary(limit, 'UNIVERSE_REPORT_NEXT_SEGMENT')
    for scan_index in range(observation_cursor, len(result['scan_days'])):
        scan = result['scan_days'][scan_index]
        daily_details = []
        day = scan['date']
        if day < observation_plan['start'] or day > observation_plan['end'] or day not in indices:
            raise ValueError('REPORT_SCAN_OUTSIDE_OBSERVATION')
        first_scan = day if first_scan is None else first_scan
        ranked_symbols = set()
        if 'TOP_FROZEN_SLOT_COUNT' in score_statistics:
            scores = []
            for row in scan['rows']:
                truth = row['conditions']
                value = truth.get('score')
                if (is_signal_opportunity(row) and truth.get('score_ready') is True
                        and type(value) in (int, float) and math.isfinite(value)):
                    scores.append((value if score_direction == 'ASCENDING' else -value, row['symbol']))
            ranked_symbols = {symbol for _, symbol in sorted(scores)[:top_count]}
        for row in scan['rows']:
            symbol, truth = row['symbol'], row['conditions']
            if symbol not in observations.symbol_index:
                raise ValueError('REPORT_SCAN_SYMBOL_OUTSIDE_SCOPE')
            track = trackers.setdefault(symbol, {'active': None, 'boundary_unknown': day == first_scan, 'gap': False})
            known = truth.get('condition_ready', truth.get('ready')) is True and all(
                type(truth.get(key)) is bool for key in ('buy', 'sell', 'market_filter'))
            opportunity = is_signal_opportunity(row)
            condition_counts['READY' if known else 'UNKNOWN'] += 1
            if not known or row.get('qualification', {}).get('status') in {'SUSPENDED', 'UNKNOWN'}:
                track['gap'] = True
                track['boundary_unknown'] = True
                if track['active'] is not None:
                    uncertain_episodes.add(track['active'])
                continue
            if not opportunity:
                track.update(active=None, boundary_unknown=False, gap=False)
                continue
            opportunities += 1
            if truth.get('score_ready') is False or truth.get('selection', {}).get('score_ready') is False:
                score_unknown += 1
            known_score = (truth.get('score_ready') is True and type(truth.get('score')) in (int, float)
                and math.isfinite(truth['score']))
            score_group = ('SCORE_UNKNOWN' if not known_score else 'TOP_FROZEN_SLOT_COUNT'
                if symbol in ranked_symbols else 'OTHER_KNOWN_SCORE'
                if 'OTHER_KNOWN_SCORE' in score_statistics else 'KNOWN_SCORE')
            new_episode = track['active'] is None
            if new_episode:
                track['active'] = stable_hash([inputs.input_identity, symbol, day])
                episode_count += 1
                if track['boundary_unknown']:
                    uncertain_episodes.add(track['active'])
            for horizon in horizons:
                observed = observations.observe(symbol, indices[day], horizon)
                comparator = observations.comparator(indices[day], horizon) if observed['status'] == 'OBSERVED' else {
                    'status': 'EVENT_NOT_OBSERVED', 'return': None, 'policy': COMPARATOR}
                for kind in ('SECURITY_DAY', 'CONDITION_EPISODE') if new_episode else ('SECURITY_DAY',):
                    detail = {**observed, 'sample_kind': kind, 'condition_episode_id': track['active'],
                        'episode_boundary_unknown': bool(track['boundary_unknown']),
                        'episode_gap_observed': bool(track['gap']), 'score': truth.get('score'),
                        'score_ready': truth.get('score_ready'), 'score_group': score_group, 'comparator': comparator}
                    stat = statistics[kind][str(horizon)]
                    _add_observation(stat, observed, comparator)
                    if observed['status'] == 'OBSERVED' and comparator['return'] is not None:
                        detail['excess_return'] = observed['return'] - comparator['return']
                    if kind == 'SECURITY_DAY':
                        _add_observation(score_statistics[score_group][str(horizon)], observed, comparator)
                    detail_hash = stable_hash([detail_hash, detail])
                    detail_count += 1
                    if store is not None:
                        daily_details.append(detail)
                    if event_sink is not None:
                        event_sink(detail)
        if store is not None:
            store.state.update(observation_cursor=scan_index + 1, statistics=statistics,
                score_statistics=score_statistics, trackers=trackers, condition_counts=dict(condition_counts),
                score_unknown=score_unknown, opportunities=opportunities, episode_count=episode_count,
                uncertain_episodes=sorted(uncertain_episodes), detail_count=detail_count,
                detail_hash=detail_hash, first_scan=first_scan)
            store.commit(day, daily_details)
            at_boundary(limit, 'UNIVERSE_REPORT_NEXT_SEGMENT')
    for kind in [*statistics.values(), *score_statistics.values()]:
        for stat in kind.values():
            _finish_statistic(stat)
    signal = {'question': '不受账户资金和持仓限制的条件机会，在固定期限后如何',
        'scope_count': len(observations.symbols), 'opportunity_security_days': opportunities,
        'condition_episode_count': episode_count, 'boundary_unknown_episodes': len(uncertain_episodes),
        'score_unknown_opportunities': score_unknown, 'condition_counts': dict(condition_counts),
        'observation_plan': deepcopy(observation_plan), 'observation_plan_identity': stable_hash(observation_plan),
        'statistics': statistics, 'score_statistics': score_statistics,
        'score_comparison_policy': {'scope': 'RAW_CONDITION_OPPORTUNITIES_ACCOUNT_INDEPENDENT',
            'direction': score_direction, 'top_count': top_count,
            'actual_account_ranking_or_fill_claim': False},
        'event_detail_count': detail_count, 'event_detail_hash': detail_hash,
        'details_emitted': event_sink is not None or store is not None, 'account_independent_denominator': True,
        'value_policy': PRICE_POLICY, 'fees_and_taxes_in_signal_observations': False,
        'qualification': 'DESCRIPTIVE_ONLY_NOT_STATISTICAL_SIGNIFICANCE',
        'limitations': ['单股可分割权益价值；不是实际买入数量、税后账户收益或到期可成交价格。',
            '同日多股和重复条件区段相关；未知边界不构成独立样本。',
            '全池同日期同期限等权对照公开不能观察的成员，不以已成交交易作为信号分母。']}
    report = {'version': VERSION, 'input_identity': inputs.input_identity, 'account': account, 'signal': signal,
        'strategy_qualified': False, 'formal_method_applicable': False, 'paper_qualified': False}
    if store is not None:
        report['details_manifest'] = store.details.manifest()
    report = {**report, 'report_identity': stable_hash(report)}
    return store.finish(report) if store is not None else report
