"""状态型规则的多股票历史账户；复用 Paper 执行语义，历史结果不授予资格。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import re

import pandas as pd

from .common import stable_hash
from .formal_account_backend_v1 import FormalAccountBackendV1, normalized_costs, normalized_window, window_input_identity
from .forward_paper_engine_v1 import ForwardPaperEngineV1
from .historical_process_v1 import LIMITATIONS, _require_historical_profile
from .portfolio_execution_v1 import build_portfolio_plan
from .research_rule_strategy_v2 import ResearchRuleStrategyV2
from scripts.s1_public_entry_strategy_v1 import _frame_hash


def rule_window(window):
    symbols = window.get('symbols') if isinstance(window, dict) else None
    if (not isinstance(symbols, (list, tuple)) or not symbols or len(set(symbols)) != len(symbols)
            or any(not isinstance(s, str) or not re.fullmatch(r'(?:00[0-9]{4}\.SZ|60[0-9]{4}\.SH)', s)
                   for s in symbols)):
        raise ValueError('RULE_MAIN_BOARD_UNIVERSE_REQUIRED')
    value = normalized_window({**window, 'symbols': ['000001.SZ', '600000.SH']})
    value['symbols'] = sorted(symbols)
    return value


def rule_input_identity(bundle, window):
    _require_historical_profile(bundle)
    return stable_hash({'window': rule_window(window), 'profile': bundle['profile'],
        'frames': {k: _frame_hash(bundle[k]) for k in ('daily', 'turn', 'states')},
        'calendar': bundle['calendar'], 'events': bundle['events'],
        'corporate_actions_complete': bundle['corporate_actions_complete'],
        'source_hashes': bundle['source_hashes']})


class RuleAccountBackendV2:
    def __init__(self, window, costs='BASE', initial_cash=1_000_000,
                 max_positions=None, max_symbol_exposure_bps=None, execution_profile='HISTORICAL_MODELED'):
        if execution_profile not in ('HISTORICAL_MODELED', 'OBSERVED'):
            raise ValueError('RULE_EXECUTION_PROFILE_INVALID')
        self.execution_profile = execution_profile
        self.window = rule_window(window)
        self.costs = normalized_costs(costs)
        if isinstance(initial_cash, bool) or not isinstance(initial_cash, (int, float)) or not 0 < initial_cash < 1e12:
            raise ValueError('RULE_INITIAL_CASH_INVALID')
        self.initial_cash = float(initial_cash)
        self.max_positions = len(self.window['symbols']) if max_positions is None else max_positions
        self.max_symbol_exposure_bps = (max(1, 10000 // len(self.window['symbols']))
            if max_symbol_exposure_bps is None else max_symbol_exposure_bps)
        if (type(self.max_positions) is not int or self.max_positions < 1
                or type(self.max_symbol_exposure_bps) is not int or not 1 <= self.max_symbol_exposure_bps <= 10000):
            raise ValueError('RULE_PORTFOLIO_LIMIT_INVALID')

    def check(self, requirements):
        if requirements != ResearchRuleStrategyV2.requirements:
            raise ValueError('RULE_BACKEND_REQUIREMENTS_UNSUPPORTED')

    def validate_strategy(self, strategy):
        if type(strategy) is not ResearchRuleStrategyV2:
            raise ValueError('RULE_BACKEND_STRATEGY_UNSUPPORTED')

    def describe(self):
        paths = [Path(__file__), Path(__file__).with_name('forward_paper_engine_v1.py')]
        return {'backend': 'RULE_ACCOUNT_BACKEND_V2', 'window': deepcopy(self.window),
            'costs': deepcopy(self.costs), 'initial_cash': self.initial_cash,
            'max_positions': self.max_positions, 'max_symbol_exposure_bps': self.max_symbol_exposure_bps,
            'initial_decision': 'FIRST_ACCOUNT_CLOSE',
            'source_hashes': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            'profile': self.execution_profile, 'strategy_qualified': False,
            'independent_confirmation_eligible': False}

    def run(self, strategy, bundle, actions, guard):
        self.validate_strategy(strategy)
        self.check(strategy.requirements)
        observed = self.execution_profile == 'OBSERVED'
        if not observed:
            _require_historical_profile(bundle)
        elif bundle.get('profile') not in ('REAL_OBSERVED', 'SYNTHETIC'):
            raise ValueError('RULE_OBSERVED_PROFILE_REQUIRED')
        if list(actions) != list(bundle['events']):
            raise ValueError('RULE_ACTION_INPUT_CONFLICT')
        # 复用原字段、矩形覆盖、主板交易状态及现金红利检查；仅证券数量不再写死为二。
        validator = object.__new__(FormalAccountBackendV1)
        validator.window = self.window
        validator._validate_bundle(bundle)
        if observed:
            validator._observed_sessions(bundle)
        identity_fn = window_input_identity if observed else rule_input_identity
        identity = identity_fn(bundle, self.window)
        if guard().get('input_identity') != identity:
            raise PermissionError('RULE_INPUT_NOT_AUTHORIZED')
        days, symbols = self.window['calendar'], self.window['symbols']
        first = days.index(self.window['account_start'])
        account_days = days[first:]
        closes = {s['market_date']: s for s in bundle['close_snapshots']} if observed else {}
        opens = {s['market_date']: s for s in bundle['open_snapshots']} if observed else {}
        warmup_bars = bundle['daily'].loc[bundle['daily'].date < account_days[0]].to_dict('records')
        warmup_turn = bundle['turn'].loc[bundle['turn'].date < account_days[0]].to_dict('records')
        events = deepcopy(bundle['events'])
        warmup_events = [e for e in events if e['effective_date'] < account_days[0]]
        policy = {'initial_cash': self.initial_cash, 'symbols': symbols, 'portfolio': {
            'policy_id': 'RULE_HISTORICAL_V2', 'members': [{'strategy_id': strategy.strategy_id,
                'rule_identity': strategy.rule_identity, 'weight_bps': 10000, 'priority': 0}],
            'purpose': 'ENGINEERING_OBSERVATION', 'max_positions': self.max_positions,
            'max_symbol_exposure_bps': self.max_symbol_exposure_bps, 'max_buy_turnover_bps': 10000,
            'valid_until': (pd.Timestamp(str(days[-1]), tz='Asia/Shanghai') + pd.Timedelta(days=2)).isoformat()}}
        header = {'policy': policy, 'warmup': {'bars': warmup_bars, 'turn': warmup_turn,
            'corporate_actions': warmup_events}, 'calendar': days[first-1:],
            'strategies': {strategy.strategy_id: {'proposal': strategy.payload}},
            'source_identity': stable_hash(self.describe()), 'header_id': identity,
            'costs': self.costs, 'company_actions': ('OBSERVED_FROZEN_CASH_DIVIDEND_V2' if observed
                                                   else 'HISTORICAL_CASH_DIVIDEND_V2'),
            'account_events': [e for e in events if e['record_date'] >= account_days[0]],
            'feature_events': events, 'observed_opens': opens}
        engine = ForwardPaperEngineV1(header)
        # 仅在公共入口的已消费回执有效期间授予本次历史账户执行许可，不产生档案准入。
        admissions = {strategy.strategy_id: {'allowed': True, 'archive_hash': stable_hash(strategy.parameters),
            'strategy_qualified': False, 'source_profile': bundle['profile']}}
        # 预热只准备指标。与原买持基准、Paper 首个 CLOSE 一致，首日不提前买入。
        decisions = []
        all_decisions, daily_accounts = [], []
        for offset, day in enumerate(account_days):
            guard()
            previous_day = days[first + offset - 1]
            plan = build_portfolio_plan(policy=engine.portfolio, decisions=decisions,
                ledger=engine.engine.ledger, admissions=admissions, input_identity=identity,
                decision_at=closes[previous_day]['received_at'] if observed else _stamp(previous_day, 15, 30),
                next_session=day)
            all_decisions.append({'date': previous_day, 'decisions': deepcopy(decisions), 'plan': plan})
            full = bundle['daily'].loc[bundle['daily'].date == day].to_dict('records')
            turns = bundle['turn'].loc[bundle['turn'].date == day].to_dict('records')
            states = [{'symbol': r['symbol'], 'date': day, 'listed': bool(r['listed']),
                'delisted': bool(r['delisted']), 'is_st': r['st_status'] == 'ST', 'board': r['board'],
                'suspended': r['suspension_status'] != 'TRADING' or not r['universe_member']
                             or r['eligibility_status'] != 'ELIGIBLE'}
                for r in bundle['states'].loc[bundle['states'].trade_date == day].to_dict('records')]
            prior = {r['symbol']: r for r in engine.bars if r['date'] == previous_day}
            opened = [{**r, **{k: r['open'] for k in ('high', 'low', 'close')},
                'volume': prior[r['symbol']]['volume'], 'amount': prior[r['symbol']]['amount']} for r in full]
            engine.open(opens[day] if observed else _snapshot(day, 'OPEN', opened, [], states), plan, admissions)
            decisions = engine.close(closes[day] if observed else _snapshot(day, 'CLOSE', full, turns, states))
            daily_accounts.append(_independent_reconcile(engine, self.initial_cash, day))
        guard()
        if identity != identity_fn(bundle, self.window):
            raise ValueError('RULE_INPUT_CHANGED_DURING_EXECUTION')
        state = engine.state()
        equity = [self.initial_cash] + [r['equity'] for r in daily_accounts]
        peak, drawdown = equity[0], 0.
        for value in equity:
            peak = max(peak, value)
            drawdown = max(drawdown, 1-value/peak)
        fills = state['economic']['trades']
        return {'status': 'OBSERVED_ACCOUNT_COMPLETED' if observed else 'HISTORICAL_MODELED_ACCOUNT_COMPLETED',
            'profile': bundle['profile'],
            'research_only': not observed, 'strategy_qualified': False, 'independent_confirmation_eligible': False,
            'limitations': (['实际快照上的模拟成交，不是券商账户，不自动授予统计资格。'] if observed else list(LIMITATIONS))
                + ['开盘流动性以此前交易日成交量建模；不是实时开盘成交量。'],
            'input_identity': identity, 'fills': fills,
            'final_account_checkpoint': state, 'decisions': all_decisions, 'daily_accounts': daily_accounts,
            'metrics': {'net_return': equity[-1]/self.initial_cash-1, 'max_drawdown': drawdown,
                'total_fees': sum(t['fee'] for t in fills), 'trade_count': len(fills)},
            'reconciliation': {'passed': True, 'days': len(daily_accounts)}}


def _stamp(day, hour, minute):
    return pd.Timestamp(str(day), tz='Asia/Shanghai') + pd.Timedelta(hours=hour, minutes=minute)


def _snapshot(day, phase, bars, turn, states):
    return {'market_date': day, 'phase': phase,
        'received_at': _stamp(day, 9 if phase == 'OPEN' else 15, 30).isoformat(),
        'payload': {'bars': bars, 'turn': turn, 'states': states}}


def _independent_reconcile(paper, initial_cash, day):
    """从成交及原始条款重建现金、FIFO、红利税和收盘权益，不读取账本计算结果作输入。"""
    ledger = paper.engine.ledger
    cash, quantities, lots, entitlements, paid = initial_cash, {}, [], {}, set()
    tax_total = 0.
    events = ledger.events
    costs = paper.header['costs']
    for session in paper.header['calendar'][1:]:
        if session > day:
            break
        for event in events:
            if event['payment_date'] == session:
                amount = entitlements.get(event['event_id'], 0) * float(event['terms']['cash_per_share'])
                cash += amount
                if amount:
                    paid.add(event['event_id'])
        for trade in ledger.trades:
            trade_day = int(trade.fill_time.strftime('%Y%m%d'))
            if trade_day != session:
                continue
            buy = trade.side.value == 'BUY'
            direction = 1 if buy else -1
            raw = next(r for r in paper.bars if r['date'] == session and r['symbol'] == trade.symbol)
            if paper.header.get('observed_opens'):
                raw = next(r for r in paper.header['observed_opens'][session]['payload']['bars'] if r['symbol'] == trade.symbol)
                if trade.fill_time != pd.Timestamp(paper.header['observed_opens'][session]['received_at']):
                    raise ValueError('RULE_OBSERVED_FILL_TIME_CONFLICT')
            price = round(float(raw['open']) * (1 + direction*costs['slippage_bps']), 4)
            fee = round(max(trade.quantity*price*costs['commission_rate'], costs['min_commission'])
                        + (0 if buy else trade.quantity*price*costs['stamp_tax_rate']), 4)
            if abs(price-trade.price) > 1e-6 or abs(fee-trade.fee) > .0001:
                raise ValueError('RULE_FILL_PRICE_OR_FEE_RECONCILIATION_FAILED')
            cash -= direction * price * trade.quantity + fee
            key = (trade.strategy_id, trade.symbol)
            quantities[key] = quantities.get(key, 0) + direction*trade.quantity
            if buy:
                lots.append({'key': key, 'bought': session, 'remaining': trade.quantity, 'dividends': {}})
            else:
                remaining = trade.quantity
                for lot in lots:
                    if lot['key'] != key or not lot['remaining'] or not remaining:
                        continue
                    if session <= lot['bought']:
                        raise ValueError('RULE_T1_RECONCILIATION_FAILED')
                    sold = min(lot['remaining'], remaining)
                    for event in events:
                        entitled = min(sold, lot['dividends'].get(event['event_id'], 0))
                        if entitled:
                            bought, sold_at = pd.Timestamp(str(lot['bought'])), pd.Timestamp(str(session))
                            rate = .2 if sold_at <= bought + pd.DateOffset(months=1) else (
                                .1 if sold_at <= bought + pd.DateOffset(years=1) else 0.)
                            tax = round(entitled*float(event['terms']['cash_per_share'])*rate, 4)
                            cash -= tax
                            tax_total += tax
                            lot['dividends'][event['event_id']] -= entitled
                    lot['remaining'] -= sold
                    remaining -= sold
                if remaining:
                    raise ValueError('RULE_LOT_RECONCILIATION_FAILED')
        for event in events:
            if event['record_date'] == session:
                eligible = [lot for lot in lots if lot['key'][1] == event['symbol']]
                entitlements[event['event_id']] = sum(lot['remaining'] for lot in eligible)
                for lot in eligible:
                    lot['dividends'][event['event_id']] = lot['remaining']
    receivable = sum(entitlements.get(e['event_id'], 0)*float(e['terms']['cash_per_share'])
                     for e in events if e['effective_date'] <= day and e['event_id'] not in paid)
    prices = {r['symbol']: float(r['close']) for r in paper.bars if r['date'] == day}
    equity = cash + receivable + sum(q*prices[key[1]] for key,q in quantities.items())
    if (abs(cash-ledger.cash) > .02 or abs(equity-ledger.current_equity()) > .02
            or abs(tax_total-ledger.dividend_tax_withheld) > .001
            or entitlements != ledger.entitlements or paid != ledger.payments
            or any(ledger.position_qty(*key) != quantity for key, quantity in quantities.items())):
        raise ValueError('RULE_INDEPENDENT_ACCOUNT_RECONCILIATION_FAILED')
    if ledger.check_invariants():
        raise ValueError('RULE_LEDGER_INVARIANT_FAILED')
    return {'date': day, 'cash': cash, 'equity': equity,
        'cash_difference': cash-ledger.cash, 'equity_difference': equity-ledger.current_equity(),
        'positions': [{'strategy_id': k[0], 'symbol': k[1], 'quantity': v} for k,v in sorted(quantities.items())],
        'passed': True}
