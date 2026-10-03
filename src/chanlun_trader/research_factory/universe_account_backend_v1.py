"""版本化全范围账户：一个资金池、稀疏行情、原有账本与撮合。"""
from copy import deepcopy
import hashlib
from pathlib import Path

import pandas as pd

from ..engine.broker import BrokerSimulator
from ..engine.engine import BacktestEngineV2
from ..engine.signal import Side
from ..engine.time_types import EventKind, date_key
from .common import canonical_json, stable_hash
from .engine_replay_recovery_v1 import _write_receipt, _read_receipt, ReplayInterrupted
from .forward_paper_engine_v1 import ForwardPaperEngineV1, ledger_rule_account
from .portfolio_execution_v1 import build_portfolio_plan
from .rule_account_backend_v2 import _snapshot, _stamp
from .formal_account_backend_v1 import normalized_costs
from .universe_signal_scan_v1 import UniverseSignalScanV1, decision_from_conditions
from .universe_rule_exit_v1 import PRICE_POLICY


VERSION = 'UNIVERSE_ACCOUNT_BACKEND_V1'


class UniverseBacktestEngineV1(BacktestEngineV2):
    def _sync_lot_contract_fields(self):
        super()._sync_lot_contract_fields()
        for lot in self.ledger.lots.values():
            next_day = self.calendar.next_day(lot.entry_session)
            if next_day is not None:
                if getattr(self.ledger, 'version', '') == 'UniverseCorporateAccountingV2':
                    lot.sellable_from = max(lot.sellable_from, _stamp(next_day, 9, 30))
                    day = int(lot.sellable_from.strftime('%Y%m%d'))
                    lot.sellable_from_session = day
                    try:
                        lot.sellable_from_session_index = self.calendar.date_index(day)
                    except KeyError:
                        if day <= self.calendar.trading_days[-1]:
                            self.ledger._reject('SELLABLE_DATE_NOT_IN_CALENDAR')
                        lot.sellable_from_session_index = None
                else:
                    lot.sellable_from = _stamp(next_day, 9, 30)


class _SharedParticipationFillV2:
    """撮合仍由原模型决定；同证券当日各订单共享已知成交量额度。"""
    def __init__(self, original, consumed):
        self.original, self.consumed = original, consumed

    def try_fill(self, order, ts, bar):
        price, quantity, reason = self.original.try_fill(order, ts, bar)
        if quantity <= 0:
            return price, quantity, reason
        limit = int(float(bar['volume']) * self.original.max_participation_rate)
        quantity = min(quantity, max(0, limit - self.consumed))
        return (price, quantity, reason) if quantity else (None, 0, 'PARTICIPATION_LIMIT')


class UniverseBrokerV1(BrokerSimulator):
    def _session_consumed(self, symbol, ts):
        day = date_key(ts)
        if getattr(self, '_participation_session', None) != day:
            self._participation_session, self._participation_used = day, {}
            # ledger checkpoint保留实际成交；重建broker时也不能重新获得同日额度。
            for fill in reversed(self.ledger.executed_fills):
                fill_day = date_key(pd.Timestamp(fill['fill_time']))
                if fill_day < day:
                    break
                if fill_day == day:
                    self._participation_used[fill['symbol']] = (
                        self._participation_used.get(fill['symbol'], 0) + fill['quantity'])
        return self._participation_used.get(symbol, 0)

    def _bar_for_order(self, order, ts, ev_kind):
        day = date_key(ts)
        bar = self.store.get_daily_bar(order.symbol, day, price_mode='raw')
        if bar is not None and ev_kind == EventKind.SESSION_OPEN:
            previous = self.clock.calendar.prev_day(day)
            prior = self.store.get_daily_bar(order.symbol, previous, price_mode='raw') if previous else None
            volume = float(prior['volume']) if prior is not None else 0.
            bar.update(volume=volume, current_volume=volume, volume_known_asof='PREVIOUS_EXCHANGE_SESSION',
                       liquidity_reference_unavailable=volume <= 0)
        return bar

    def _try_fill_order(self, order, ts, ev_kind):
        bar = self._bar_for_order(order, ts, ev_kind)
        if bar is not None and bar.get('liquidity_reference_unavailable'):
            return self._reject_or_expire(order, ts, 'LIQUIDITY_REFERENCE_UNAVAILABLE')
        if bar is not None and ev_kind == EventKind.SESSION_OPEN:
            permission = self.price_limit.can_buy_at_open if order.side == Side.BUY else self.price_limit.can_sell_at_open
            allowed, _ = permission(order.symbol, ts, bar)
            if allowed:
                up, down = self.price_limit.limit_prices(order.symbol, ts, bar['prev_close'])
                modeled = round(self.slippage.apply('BUY' if order.side == Side.BUY else 'SELL', bar['open']), 4)
                if (up is not None and modeled > up) or (down is not None and modeled < down):
                    return self._reject_or_expire(order, ts, 'MODELED_FILL_PRICE_OUTSIDE_DAILY_LIMIT')
        shared = (getattr(self.ledger, 'version', '') == 'UniverseCorporateAccountingV2'
                  and ev_kind == EventKind.SESSION_OPEN)
        if not shared:
            return super()._try_fill_order(order, ts, ev_kind)
        consumed, filled = self._session_consumed(order.symbol, ts), order.filled_quantity
        original = self.fill_model
        self.fill_model = _SharedParticipationFillV2(original, consumed)
        try:
            result = super()._try_fill_order(order, ts, ev_kind)
        finally:
            self.fill_model = original
        self._participation_used[order.symbol] = consumed + order.filled_quantity - filled
        return result


class UniversePaperEngineV1(ForwardPaperEngineV1):
    def __init__(self, header, inputs, scanner):
        self.inputs, self.scanner = inputs, scanner
        super().__init__(header)
        from .board_execution_policy_v1 import BoardPriceLimitModelV1
        old = self.engine.broker
        self.engine.broker = UniverseBrokerV1(old.store, old.clock, old.ledger, old.orders,
            fee_model=old.fee_model, slippage_model=old.slippage, fill_model=old.fill_model,
            price_limit_model=BoardPriceLimitModelV1(self.master, calendar=inputs.window['calendar'],
                listing_dates=inputs.bundle['listing_dates'], pit_enforced=True),
            suspension_model=old.suspension, risk_manager=old.risk, lot_size=old.lot_size,
            partial_fill=old.partial_fill, index_ok_fn=old.index_ok_fn)
        self.bars = []
        self.turn = []
        self.scan_days = []

    def _load_states(self, snapshot):
        super()._load_states(snapshot)
        for row in snapshot['payload']['states']:
            state = self.master._states[row['symbol']][-1]
            state.listing_date = self.inputs.listing_dates.get(row['symbol'])
            state.listing_sessions_before_calendar = self.inputs.bundle.get(
                'listing_sessions_before_calendar', {}).get(row['symbol'])

    def _load_bars(self, rows):
        if not rows:
            return
        frame = pd.DataFrame(rows)
        columns = ['open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']
        for symbol, group in frame.groupby('symbol', sort=False):
            incoming = group.drop_duplicates('date', keep='last').set_index('date')[columns]
            current = self.store.daily_raw.get(symbol)
            if current is not None:
                incoming = pd.concat([current, incoming])
                incoming = incoming[~incoming.index.duplicated(keep='last')]
            # 开盘只需当日与此前行情；特征已经单独计算，不向策略暴露未来数据。
            self.store.add_daily_raw(symbol, incoming.sort_index().tail(2))

    def close(self, snapshot):
        ts = pd.Timestamp(snapshot['received_at'])
        self._accept_actions(snapshot)
        tradable = {row['symbol'] for row in snapshot['payload']['states'] if not row['suspended']
                    and row['listed'] and not row['delisted']}
        self.bars = [deepcopy(row) for row in snapshot['payload']['bars'] if row['symbol'] in tradable]
        self._load_bars(self.bars)
        self._load_states(snapshot)
        self.engine.broker.process_orders(EventKind.SESSION_CLOSE, ts)
        self.engine.ledger.on_close(ts)
        self.engine._mark_to_market(ts, EventKind.AFTER_CLOSE)
        self.engine.ledger.snapshot(ts)
        self._invariants()
        return self.decisions(snapshot['payload']['states'])

    def decisions(self, states):
        day = int(states[0]['date'])
        calendar = tuple(self.inputs.window['calendar'])
        index = calendar.index(day)
        strategy_id, strategy = next(iter(self.strategies.items()))
        exits = {}
        if strategy_id in self.rule_exits:
            for exit_item in self.rule_exits[strategy_id].evaluate(self.engine.ledger, self.store, calendar, day):
                exits.setdefault(exit_item.symbol, []).append(exit_item)
        result, scan = [], []
        for security in sorted(states, key=lambda row: row['symbol']):
            symbol = security['symbol']
            truth = self.scanner.at(symbol, day)
            qualification = self.inputs.scan_status(symbol, day)
            account = ledger_rule_account(self.engine.ledger, strategy_id, symbol, calendar, day)
            key = strategy_id, symbol
            decision = decision_from_conditions(strategy, truth, account=account,
                state=self.rule_states.get(key, {}), index=index)
            self.rule_states[key] = decision.state
            side = ('BUY' if decision.intent.weight > 0 else 'SELL') if decision.intent else 'HOLD'
            reason = decision.reason
            if side == 'BUY' and not qualification['entry_eligible']:
                side, reason = 'HOLD', 'SECURITY_NOT_ELIGIBLE'
            record = {'strategy_id': strategy_id, 'symbol': symbol, 'side': side, 'reason': reason,
                'target_weight': decision.intent.weight if decision.intent else 0.}
            if exits.get(symbol) and side != 'SELL':
                record.update(side='SELL', reason='RULE_RISK_EXIT_V3', target_weight=0.,
                              exit_lot_ids=sorted(item.lot_id for item in exits[symbol]))
            result.append(record)
            scan.append({'symbol': symbol, 'conditions': truth, 'qualification': qualification,
                         'reason': record['reason'], 'side': record['side']})
        self.scan_days.append({'date': day, 'target': len(self.symbols), 'processed': len(scan),
                              'rows': scan, 'identity': stable_hash(scan)})
        return result


class UniverseAccountBackendV1:
    def __init__(self, window, costs='BASE', initial_cash=50000, max_positions=2,
                 max_symbol_exposure_bps=5000, backend_version=VERSION, batch_size=128, checkpoint_path=None):
        from .universe_account_inputs_v1 import normalized_universe_window_v1
        if backend_version != VERSION:
            raise ValueError('UNIVERSE_BACKEND_VERSION_REQUIRED')
        self.window = normalized_universe_window_v1(window)
        self.costs = normalized_costs(costs)
        if type(initial_cash) not in (int, float) or not 0 < initial_cash < 1e12:
            raise ValueError('UNIVERSE_INITIAL_CASH_INVALID')
        if type(max_positions) is not int or not 1 <= max_positions <= len(self.window['symbols']):
            raise ValueError('UNIVERSE_POSITION_LIMIT_INVALID')
        if type(max_symbol_exposure_bps) is not int or not 1 <= max_symbol_exposure_bps <= 10000:
            raise ValueError('UNIVERSE_EXPOSURE_INVALID')
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError('UNIVERSE_BATCH_SIZE_INVALID')
        self.initial_cash, self.max_positions = float(initial_cash), max_positions
        self.max_symbol_exposure_bps, self.batch_size = max_symbol_exposure_bps, batch_size
        self.checkpoint_path = None if checkpoint_path is None else str(Path(checkpoint_path).absolute())

    def validate_strategy(self, strategy):
        from .research_rule_strategy_v3 import ResearchRuleStrategyV3
        if type(strategy) is not ResearchRuleStrategyV3:
            raise ValueError('UNIVERSE_V3_STRATEGY_REQUIRED')

    def check(self, requirements):
        if requirements.asset != 'A_SHARE' or requirements.capabilities != (
                'RESEARCH_RULE_STRATEGY_V3', 'EXECUTION_STATE', 'PERSONAL_CASH_DIVIDEND'):
            raise ValueError('UNIVERSE_REQUIREMENTS_UNSUPPORTED')

    def describe(self):
        from .board_execution_policy_v1 import board_policy_identity
        from .universe_corporate_accounting_v2 import PRICE_POLICY as corporate_price_policy
        folder = Path(__file__).parent
        paths = [folder / name for name in ('universe_account_backend_v1.py', 'universe_signal_scan_v1.py',
            'universe_account_inputs_v1.py', 'universe_rule_exit_v1.py', 'universe_dividend_accounting_v1.py',
            'universe_corporate_accounting_v2.py', 'corporate_action_price_v2.py', 'causal_dividend_features_v1.py',
            'universe_evidence_v1.py', 'board_execution_policy_v1.py', 'forward_paper_engine_v1.py',
            'portfolio_execution_v1.py', 'engine_replay_recovery_v1.py', 'strategy_interface_v1.py',
            'daily_plan.py', 'common.py')]
        paths += list((folder.parent / 'engine').glob('*.py'))
        return {'backend': VERSION, 'window': deepcopy(self.window), 'costs': deepcopy(self.costs),
            'initial_cash': self.initial_cash, 'max_positions': self.max_positions,
            'max_symbol_exposure_bps': self.max_symbol_exposure_bps, 'checkpoint_path': self.checkpoint_path,
            'board_policy_identity': board_policy_identity(), 'exit_price_policy': PRICE_POLICY,
            'supported_exit_price_policies': [PRICE_POLICY, corporate_price_policy],
            'allocation': 'SELL_THEN_PRIORITY_STRATEGY_SYMBOL', 'liquidity': 'PREVIOUS_EXCHANGE_SESSION',
            'profile': 'HISTORICAL_MODELED', 'strategy_qualified': False,
            'independent_confirmation_eligible': False,
            'source_hashes': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}

    def run(self, strategy, bundle, actions, guard, *, stop_after_date=None):
        from .universe_account_inputs_v1 import prepare_universe_account_inputs_v1
        from .universe_evidence_v1 import reconstruct_universe_account
        self.validate_strategy(strategy)
        self.check(strategy.requirements)
        if list(actions) != list(bundle['events']):
            raise ValueError('UNIVERSE_ACTION_INPUT_CONFLICT')
        inputs = prepare_universe_account_inputs_v1(bundle, self.window, stage='ACCOUNT',
            required_fields=strategy.requirements.fields, warmup_bars=strategy.requirements.warmup_sessions)
        identity = inputs.input_identity
        if guard().get('input_identity') != identity:
            raise PermissionError('UNIVERSE_INPUT_NOT_AUTHORIZED')
        scanner = UniverseSignalScanV1(strategy, inputs, batch_size=self.batch_size)
        days = self.window['calendar']
        first = days.index(self.window['account_start'])
        account_days = days[first:]
        if first < 1:
            raise ValueError('UNIVERSE_PRIOR_SESSION_REQUIRED')
        policy = {'initial_cash': self.initial_cash, 'symbols': self.window['symbols'], 'portfolio': {
            'policy_id': VERSION, 'members': [{'strategy_id': strategy.strategy_id,
                'rule_identity': strategy.rule_identity, 'weight_bps': 10000, 'priority': 0}],
            'purpose': 'ENGINEERING_OBSERVATION', 'max_positions': self.max_positions,
            'max_symbol_exposure_bps': self.max_symbol_exposure_bps, 'max_buy_turnover_bps': 10000,
            'valid_until': (pd.Timestamp(str(days[-1]), tz='Asia/Shanghai') + pd.Timedelta(days=2)).isoformat()}}
        warmup = bundle['daily'].loc[bundle['daily'].date < account_days[0]]
        # 只将最近有效原价装入撮合 store；不复制全市场全部历史字典。
        recent = warmup.sort_values('date').groupby('symbol', sort=False).tail(2).to_dict('records')
        header = {'execution_version': VERSION, 'policy': policy, 'warmup': {'bars': recent, 'turn': [], 'corporate_actions': []},
            'calendar': days, 'strategies': {strategy.strategy_id: {'proposal': strategy.payload}},
            'source_identity': stable_hash(self.describe()), 'header_id': identity, 'costs': self.costs,
            'company_actions': ('HISTORICAL_CORPORATE_ACTION_V2'
                if any(e['event_type'] != 'CASH_DIVIDEND' and e['record_date'] >= account_days[0]
                       for e in bundle['events']) else 'HISTORICAL_CASH_DIVIDEND_V2'),
            'account_events': [e for e in bundle['events'] if e['record_date'] >= account_days[0]],
            'feature_events': bundle['events']}
        paper = UniversePaperEngineV1(header, inputs, scanner)
        admissions = {strategy.strategy_id: {'allowed': True, 'archive_hash': stable_hash(strategy.parameters),
            'strategy_qualified': False, 'source_profile': bundle['profile']}}
        checkpoint = Path(self.checkpoint_path) if self.checkpoint_path else None
        old = _read_receipt(checkpoint, checkpoint.parent, identity) if checkpoint and checkpoint.exists() else None
        execution_identity = stable_hash({'description': self.describe(), 'rule': strategy.rule_identity, 'scanner': scanner.identity})
        if old and old.get('execution_identity') != execution_identity:
            raise ValueError('UNIVERSE_RECOVERY_EXECUTION_CHANGED')
        decisions, all_decisions, daily_accounts = [], [], []
        grouped = {int(day): frame for day, frame in bundle['daily'].groupby('date', sort=False)}
        prefix = stable_hash({'input': identity, 'execution': execution_identity})
        for offset, day in enumerate(account_days):
            guard()
            previous_day = days[first + offset - 1]
            plan = build_portfolio_plan(policy=paper.portfolio, decisions=decisions, ledger=paper.engine.ledger,
                admissions=admissions, input_identity=identity, decision_at=_stamp(previous_day, 15, 30), next_session=day)
            all_decisions.append({'date': previous_day, 'decisions': deepcopy(decisions), 'plan': plan})
            raw_frame = grouped.get(day)
            full = raw_frame.to_dict('records') if raw_frame is not None else []
            def states_at(hour, minute):
                rows = []
                for symbol in self.window['symbols']:
                    state = inputs.state(symbol, day, asof=_stamp(day, hour, minute))
                    if not state['state_known']:
                        raise ValueError('UNIVERSE_STATE_NOT_AVAILABLE:' + symbol)
                    rows.append({'symbol': symbol, 'date': day, 'listed': bool(state['listed']),
                        'delisted': bool(state['delisted']), 'is_st': state['st_status'] == 'ST',
                        'suspended': state['suspension_status'] != 'TRADING', 'board': state['board'],
                        'universe_member': bool(state['universe_member']),
                        'eligibility_status': state['eligibility_status']})
                return rows
            states = states_at(9, 30)
            eligible_open = {s['symbol'] for s in states if not s['suspended']
                             and s['listed'] and not s['delisted']}
            opened = []
            for row in full:
                if row['symbol'] not in eligible_open:
                    continue
                prior = inputs.bar(row['symbol'], previous_day)
                opened.append({**row, **{k: row['open'] for k in ('high', 'low', 'close')},
                    'volume': float(prior['volume']) if prior is not None else 0.,
                    'amount': float(prior['amount']) if prior is not None else 0.})
            paper.open(_snapshot(day, 'OPEN', opened, [], states), plan, admissions)
            decisions = paper.close(_snapshot(day, 'CLOSE', full, [], states_at(15, 0)))
            ledger = paper.engine.ledger
            positions = [{'strategy_id': strategy.strategy_id, 'symbol': symbol,
                          'quantity': ledger.position_qty(strategy.strategy_id, symbol)}
                         for symbol in sorted({t.symbol for t in ledger.trades})]
            stale = [{'symbol': p.symbol, 'status': 'STALE_VERIFIED_SUSPENSION'} for p in ledger.positions.values()
                     if p.quantity and inputs.state(p.symbol, day)['suspension_status'] == 'SUSPENDED']
            daily_accounts.append({'date': day, 'cash': ledger.cash, 'equity': ledger.current_equity(),
                'positions': positions, 'stale_valuations': stale})
            prefix = stable_hash([prefix, day, daily_accounts[-1], paper.scan_days[-1], plan,
                                  len(ledger.trades), ledger.dividend_tax_withheld])
            if old and day == old['last_day'] and prefix != old['prefix_hash']:
                raise ValueError('UNIVERSE_RECOVERY_PREFIX_CONFLICT')
            if checkpoint and (old is None or day > old['last_day']):
                _write_receipt(checkpoint, checkpoint.parent, {'version': 'ENGINE_REPLAY_RECOVERY_V1',
                    'input_identity': identity, 'execution_identity': execution_identity,
                    'last_day': day, 'prefix_hash': prefix, 'budget_reused': True})
            if stop_after_date is not None and day == stop_after_date:
                raise ReplayInterrupted('UNIVERSE_DAILY_COMMIT_INTERRUPTED')
        if old and old['last_day'] not in account_days:
            raise ValueError('UNIVERSE_RECOVERY_DAY_OUTSIDE_SCOPE')
        guard()
        inputs.assert_unchanged()
        state = paper.state()
        equity = [self.initial_cash] + [item['equity'] for item in daily_accounts]
        peak, drawdown = equity[0], 0.
        for value in equity:
            peak = max(peak, value)
            drawdown = max(drawdown, 1 - value / peak)
        result = {'status': 'HISTORICAL_MODELED_ACCOUNT_COMPLETED', 'profile': bundle['profile'],
            'execution_version': VERSION, 'input_identity': identity, 'execution_identity': execution_identity,
            'execution_description': self.describe(),
            'account_policy': policy, 'board_policy_identity': bundle['board_policy_identity'],
            'exit_price_policy': (self.describe()['supported_exit_price_policies'][1]
                if header['company_actions'] == 'HISTORICAL_CORPORATE_ACTION_V2' else PRICE_POLICY),
            'scanner_identity': scanner.identity, 'scan_preparation': scanner.preparation, 'scan_days': paper.scan_days,
            'coverage': inputs.coverage, 'fills': state['economic']['trades'], 'decisions': all_decisions,
            'daily_accounts': daily_accounts, 'final_account_checkpoint': state,
            'metrics': {'net_return': equity[-1] / self.initial_cash - 1, 'max_drawdown': drawdown,
                'total_fees': sum(t['fee'] for t in state['economic']['trades']),
                'trade_count': len(state['economic']['trades'])},
            'strategy_qualified': False, 'independent_confirmation_eligible': False,
            'cost_scenario': 'FULL_ACCOUNT_COST_SCENARIO',
            'limitations': ['历史状态可见时点为模型；开盘流动性仅用前一交易所session，复牌可能延迟成交。',
                            '成本场景可能改变可买数量，不能冒称固定交易路径成本压力。']}
        try:
            audit = reconstruct_universe_account(bundle, self.window, result, initial_cash=self.initial_cash,
                costs=self.costs, strategy_id=strategy.strategy_id, rule=strategy.payload)
        except ValueError as error:
            evidence = getattr(error, 'evidence', None)
            if evidence and evidence.get('account_date'):
                # 标明这是期末原始账务，不能冒称失败日计划前的账户。
                evidence['observed_final_date'] = account_days[-1]
                evidence['observed_final_economic'] = __import__('json').loads(canonical_json(state['economic']))
            raise
        result['reconciliation'] = {'passed': True, 'days': len(daily_accounts), 'audit_identity': stable_hash(audit)}
        return __import__('json').loads(canonical_json(result))
