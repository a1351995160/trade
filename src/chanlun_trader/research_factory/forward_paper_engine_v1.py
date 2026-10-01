"""前瞻观察的薄账户适配：使用既有订单、成交、风控和账本。"""
import math
from copy import deepcopy
import json

import pandas as pd

from ..engine.asof import MarketDataStore
from ..engine.engine import BacktestEngineV2, EngineConfig
from ..engine.order import Order, TimeInForce
from ..engine.security_state import SecurityMaster, SecurityState
from ..engine.signal import Side
from ..engine.time_types import EventKind
from .bounded_candidate_v1 import validate_candidate
from .causal_dividend_features_v1 import causal_hfq_bars
from .common import canonical_json, stable_hash
from .engine_replay_recovery_v1 import economic_state
from .strategy_interface_v1 import Context, validate_decision
from scripts.probe_all_indicator_strategy_v1 import pilot_registry


class ForwardPaperEngineV1:
    def __init__(self, header):
        self.header = header
        self.policy = header['policy']
        self.symbols = self.policy['symbols']
        self.bars = deepcopy(header['warmup']['bars'])
        self.turn = deepcopy(header['warmup']['turn'])
        self.actions = tuple(header['warmup'].get('corporate_actions', []))
        self.strategies = {key: paper_strategy(value['proposal'], strategy_id=key)
                           for key, value in header['strategies'].items()}
        self.rule_states = {}
        from .rule_exit_adapter_v3 import RuleExitAdapterV3
        adapter_type = RuleExitAdapterV3
        if header.get('execution_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1':
            from .universe_rule_exit_v1 import UniverseRuleExitV1
            adapter_type = UniverseRuleExitV1
        self.rule_exits = {key: adapter_type(key, strategy.exit_rules)
                           for key, strategy in self.strategies.items()
                           if getattr(strategy, 'exit_rules', None) is not None and strategy.exit_rules.enabled}
        if self.rule_exits:
            self._validate_exit_actions(header.get('account_events', []))
        self.store, self.master = MarketDataStore(), SecurityMaster()
        self._load_bars(self.bars)
        from .portfolio_execution_v1 import PortfolioExecutionPolicyV1
        self.portfolio = PortfolioExecutionPolicyV1.model_validate_json(json.dumps(self.policy['portfolio']))
        costs = header.get('costs', {})
        config = EngineConfig(**costs, initial_cash=self.policy['initial_cash'],
            max_positions=self.portfolio.max_positions,
            max_position_weight=self.portfolio.max_symbol_exposure_bps / 10000,
            max_holding_days=100000, feature_price_mode='raw',
            start_date=header['calendar'][0], end_date=header['calendar'][-1],
            enable_index_filter=False, index_filter_enabled=False, persist_run_manifest=False,
            pit_eligibility_enforced=True, strategy_hash=stable_hash(header['strategies']),
            data_manifest_hash=header['header_id'], calendar_version=stable_hash(header['calendar']))
        calendar = sorted(set(header['calendar']) | {int(row['date']) for row in self.bars})
        engine_type = BacktestEngineV2
        if header.get('execution_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1':
            from .universe_account_backend_v1 import UniverseBacktestEngineV1
            engine_type = UniverseBacktestEngineV1
        self.engine = engine_type(self.store, calendar, config=config,
                                      security_master=self.master, seed=0,
                                      source_identity=(header['source_identity'], False))
        self.engine._build()
        if header.get('company_actions') in ('OBSERVED_CASH_DIVIDEND_V1', 'HISTORICAL_CASH_DIVIDEND_V2', 'OBSERVED_FROZEN_CASH_DIVIDEND_V2'):
            from ..engine.individual_dividend_accounting_v1 import IndividualDividendAccountingV1
            ledger_type = IndividualDividendAccountingV1
            if header.get('execution_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1':
                from .universe_dividend_accounting_v1 import UniverseDividendAccountingV1
                ledger_type = UniverseDividendAccountingV1
            ledger = ledger_type(self.policy['initial_cash'],
                header.get('account_events', []), header['header_id'])
            self.engine.ledger = self.engine.broker.ledger = self.engine.risk.ledger = ledger
        self.base_risk_config = deepcopy(self.engine.risk.config)
        self.skips = []
        if header.get('observation_policy') is not None:
            self.observation = {'peak_equity': float(self.policy['initial_cash']),
                'max_drawdown_bps': 0.0, 'completed_days': 0, 'pending_open_day': None,
                'buy_blocked': False, 'reason_codes': [], 'review_due': False}

    def _validate_exit_actions(self, events):
        if self.header.get('execution_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1':
            if any(event.get('event_type') != 'CASH_DIVIDEND' for event in events):
                raise ValueError('UNIVERSE_EXIT_CORPORATE_ACTION_UNSUPPORTED')
        else:
            from .rule_exit_adapter_v3 import validate_exit_actions
            validate_exit_actions(events)

    def _observe_equity(self):
        if not hasattr(self, 'observation'):
            return
        state = self.observation
        equity = self.engine.ledger.current_equity()
        state['peak_equity'] = max(state['peak_equity'], equity)
        state['max_drawdown_bps'] = max(state['max_drawdown_bps'],
            (1 - equity / state['peak_equity']) * 10000)
        if state['max_drawdown_bps'] >= self.header['observation_policy']['max_drawdown_bps']:
            state['buy_blocked'] = True
            if 'OBSERVATION_DRAWDOWN_LIMIT' not in state['reason_codes']:
                state['reason_codes'].append('OBSERVATION_DRAWDOWN_LIMIT')

    def _observation_decisions(self, decisions):
        if not hasattr(self, 'observation') or not self.observation['buy_blocked']:
            return decisions
        result = []
        for decision in decisions:
            held = self.engine.ledger.position_qty(decision['strategy_id'], decision['symbol'])
            forced = {**decision, 'side': 'SELL' if held else 'HOLD', 'target_weight': 0.0,
                      'reason': self.observation['reason_codes'][0]}
            # 观察风险覆盖是整个持仓退出，不能继承普通风险规则的分笔筛选。
            forced.pop('exit_lot_ids', None)
            result.append(forced)
        return result

    def _load_bars(self, rows):
        frame = pd.DataFrame(rows)
        for symbol in self.symbols:
            bars = frame.loc[frame.symbol == symbol].sort_values('date')
            self.store.add_daily_raw(symbol, bars.set_index('date')[
                ['open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']])

    def _load_states(self, snapshot):
        ts = pd.Timestamp(snapshot.get('processed_at', snapshot['received_at']))
        for row in snapshot['payload']['states']:
            self.master.add_state(SecurityState(
                symbol=row['symbol'], asof_date=row['date'], listed=row['listed'],
                delisted=row['delisted'], is_st=row['is_st'], board=row['board'],
                suspended=row['suspended'], st_status='ST' if row['is_st'] else 'NORMAL',
                researcher_available_at=ts.isoformat(), valid_to=row['date'], strict_daily=True))
            self.master.strict_daily_symbols.add(row['symbol'])

    def _accept_actions(self, snapshot):
        if self.header.get('company_actions') == 'OBSERVED_CASH_DIVIDEND_V1':
            from .corporate_action_lifecycle_v1 import accept_action
            stamp = snapshot.get('processed_at', snapshot['received_at'])
            for envelope in snapshot['payload'].get('corporate_action_envelopes', []):
                accept_action(self.engine.ledger, envelope, asof=stamp)
            # 已接受的事件也必须等到除权生效后才进入指标价格。
            day = int(snapshot['market_date'])
            self.actions = tuple(self.header['warmup'].get('corporate_actions', [])) + tuple(
                event for event in self.engine.ledger.events if event['effective_date'] <= day)
        elif self.header.get('company_actions') in ('HISTORICAL_CASH_DIVIDEND_V2', 'OBSERVED_FROZEN_CASH_DIVIDEND_V2'):
            self.actions = tuple(event for event in self.header.get('feature_events', [])
                                 if event['effective_date'] <= snapshot['market_date'])

    def open(self, snapshot, plan, admissions, *, admission_check=None):
        from .portfolio_execution_v1 import PortfolioExecutionRiskV1, validate_portfolio_plan
        ts = pd.Timestamp(snapshot.get('processed_at', snapshot['received_at']))
        validate_portfolio_plan(plan, policy=self.portfolio, ledger=self.engine.ledger,
            input_identity=plan['input_identity'], event_at=ts, admissions=admissions)
        self._accept_actions(snapshot)
        if self.rule_exits:
            self._validate_exit_actions(getattr(self.engine.ledger, 'events', []))
        if self.header.get('company_actions') in ('OBSERVED_CASH_DIVIDEND_V1', 'HISTORICAL_CASH_DIVIDEND_V2', 'OBSERVED_FROZEN_CASH_DIVIDEND_V2'):
            self.engine.ledger.on_open(ts)
        # OPEN仅装入实际收到的开盘快照；完整日线直到CLOSE阶段才进入账户。
        self._load_bars(self.bars + snapshot['payload']['bars'])
        self._load_states(snapshot)
        self.engine._mark_to_market(ts, EventKind.SESSION_OPEN)
        self._observe_equity()
        if hasattr(self, 'observation'):
            self.observation['pending_open_day'] = int(snapshot['market_date'])
        bars = {row['symbol']: row for row in snapshot['payload']['bars']}
        states = {row['symbol']: row for row in snapshot['payload']['states']}
        prices = {key: self.engine.slippage.apply('BUY', float(row['open'])) for key, row in bars.items()}
        authority_check = admission_check or (lambda key: admissions[key])
        def observed_admission(key):
            answer = authority_check(key)
            self._observe_equity()
            if hasattr(self, 'observation') and self.observation['buy_blocked']:
                return {**answer, 'allowed': False, 'reason_codes': [*answer.get('reason_codes', []),
                        *self.observation['reason_codes']]}
            return answer
        risk = PortfolioExecutionRiskV1(self.engine.ledger, self.base_risk_config,
            policy=self.portfolio, fee_model=self.engine.broker.fee_model,
            admission_check=observed_admission,
            orders_provider=self.engine.order_manager.open_orders, prices=prices, session_at=ts)
        self.engine.risk = self.engine.broker.risk = risk
        execution_intents = []
        for intent in plan['intents']:
            if 'exit_lot_ids' in intent:
                if intent['side'] != 'SELL' or intent['strategy_id'] not in self.rule_exits:
                    raise ValueError('RULE_EXIT_INTENT_SCOPE_INVALID')
                for lot_id in intent['exit_lot_ids']:
                    lot = self.engine.ledger.lots.get(lot_id)
                    if (lot is None or lot.strategy_id != intent['strategy_id'] or lot.symbol != intent['symbol']
                            or lot.exit_state not in ('EXIT_DUE', 'SELL_PENDING', 'PARTIALLY_FILLED')):
                        raise ValueError('RULE_EXIT_PENDING_LOT_REQUIRED')
                    execution_intents.append({**intent, '_lot_id': lot_id,
                        'intent_id': intent['intent_id'] + ':' + lot_id})
            else:
                execution_intents.append(intent)
        for item in execution_intents:
            symbol, strategy_id, side = item['symbol'], item['strategy_id'], item['side']
            state = states[symbol]
            if state['suspended'] or not state['listed'] or state['delisted']:
                self.skips.append({'intent_id': item['intent_id'], 'reason': 'SECURITY_NOT_TRADABLE'})
                continue
            if (side == 'BUY' and self.header.get('execution_version') == 'UNIVERSE_ACCOUNT_BACKEND_V1'
                    and (state.get('universe_member') is not True or state.get('eligibility_status') != 'ELIGIBLE')):
                self.skips.append({'intent_id': item['intent_id'], 'reason': 'SECURITY_NOT_ELIGIBLE_AT_OPEN'})
                continue
            if side == 'BUY':
                quantity = risk.buy_quantity(strategy_id, symbol, prices[symbol], ts,
                                             target_weight=item['target_weight'])
                position_id = None
            else:
                quantity = (self.engine.ledger.sellable_lot_quantity(item['_lot_id'], ts)
                            if '_lot_id' in item else self.engine.ledger.position_qty(strategy_id, symbol))
                position = self.engine.ledger.get_position(strategy_id, symbol)
                position_id = position.position_id if position else None
            if quantity <= 0:
                self.skips.append({'intent_id': item['intent_id'], 'reason': 'NO_PERMITTED_QUANTITY'})
                continue
            order = Order(order_id='', strategy_id=strategy_id, intent_id=item['intent_id'],
                signal_id=item['intent_id'], symbol=symbol, side=Side(side), quantity=quantity,
                created_at=ts, eligible_at=ts, time_in_force=TimeInForce.DAY,
                position_id=position_id, lot_id=item.get('_lot_id'), priority=item['priority'],
                metadata={'plan_id': plan['plan_id'], 'paper': True,
                          'target_weight':item.get('target_weight',0.0)},
                reason=item['reason'] if '_lot_id' in item else 'FORWARD_OBSERVED_OPEN')
            self.engine.order_manager.create_order(order, ts)
            self.engine.order_manager.submit(order, ts)
        self.engine.broker.process_orders(EventKind.SESSION_OPEN, ts)
        self._observe_equity()
        self.engine._sync_lot_contract_fields()
        self.engine.ledger.snapshot(ts)
        self._invariants()

    def close(self, snapshot):
        ts = pd.Timestamp(snapshot.get('processed_at', snapshot['received_at']))
        self._accept_actions(snapshot)
        if self.rule_exits:
            self._validate_exit_actions(getattr(self.engine.ledger, 'events', []))
        self.bars.extend(deepcopy(snapshot['payload']['bars']))
        self.turn.extend(deepcopy(snapshot['payload']['turn']))
        self._load_bars(self.bars)
        self._load_states(snapshot)
        # 未在开盘成交的余单到期；不在收到收盘日线时再次用开盘价成交。
        self.engine.broker.process_orders(EventKind.SESSION_CLOSE, ts)
        if self.header.get('company_actions') in ('OBSERVED_CASH_DIVIDEND_V1', 'HISTORICAL_CASH_DIVIDEND_V2', 'OBSERVED_FROZEN_CASH_DIVIDEND_V2'):
            self.engine.ledger.on_close(ts)
        self.engine._mark_to_market(ts, EventKind.AFTER_CLOSE)
        self._observe_equity()
        if hasattr(self, 'observation'):
            state = self.observation
            if state['pending_open_day'] == int(snapshot['market_date']):
                state['completed_days'] += 1
                state['pending_open_day'] = None
            if state['completed_days'] >= self.header['observation_policy']['review_after']:
                state['review_due'] = state['buy_blocked'] = True
                if 'OBSERVATION_REVIEW_DUE' not in state['reason_codes']:
                    state['reason_codes'].append('OBSERVATION_REVIEW_DUE')
        self.engine.ledger.snapshot(ts)
        self._invariants()
        return self._observation_decisions(self.decisions(snapshot['payload']['states']))

    def decisions(self, states):
        registry = pilot_registry()
        result = []
        all_bars, all_turn = pd.DataFrame(self.bars), pd.DataFrame(self.turn)
        risk_decisions = {}
        calendar = sorted(set(int(row['date']) for row in self.bars) | set(self.header['calendar']))
        day = max(int(row['date']) for row in self.bars)
        for strategy_id, adapter in self.rule_exits.items():
            for exit_decision in adapter.evaluate(self.engine.ledger, self.store, calendar, day):
                risk_decisions.setdefault((strategy_id, exit_decision.symbol), []).append(exit_decision)
        by_symbol = {row['symbol']: row for row in states}
        for symbol in self.symbols:
            bars = all_bars.loc[all_bars.symbol == symbol].sort_values('date').copy()
            bars['adjustflag'] = '3'
            bars, _ = causal_hfq_bars(bars, self.actions)
            index = pd.Index(bars.date.astype(int), name='date')
            series = {key: pd.Series(bars[key].to_numpy(dtype=float), index=index)
                      for key in ('open','high','low','close','volume','amount','prev_close')}
            vendor = all_turn.loc[all_turn.symbol == symbol].set_index('date').reindex(index)
            turnover = pd.Series(vendor['turn'].to_numpy(dtype=float), index=index) if 'turn' in vendor else None
            needs_turn = any('turn' in strategy.requirements.fields for strategy in self.strategies.values())
            if needs_turn and (turnover is None or turnover.isna().any()
                               or not turnover.map(lambda value: math.isfinite(value) and value >= 0).all()):
                raise ValueError('PAPER_REQUIRED_FIELD_MISSING_OR_INVALID:turn')
            columns = {}
            # 旧策略仍复用固定指标矩阵；新规则按实际引用计算多输出。
            legacy = [s for s in self.strategies.values() if not hasattr(s, 'build_feature_matrix')]
            definition = legacy[0].definition if legacy else {'indicators': []}
            for item in definition['indicators']:
                computed = registry.compute(item['id'], series['close'], version=item['version'],
                    high=series['high'], low=series['low'], open_=series['open'], volume=series['volume'],
                    amount=series['amount'], prev_close=series['prev_close'], params=item['params'],
                    extra_data={'vendor_turn': turnover})
                columns[item['id']+'__value'] = computed.output(item['primary_output'])
                columns[item['id']+'__ready'] = computed.ready()
            matrix = pd.DataFrame(columns, index=index)
            context = Context(matrix, tuple(map(int,index)), len(index)-1, {}, {})
            for strategy_id, strategy in self.strategies.items():
                rule_context = context
                state_key = (strategy_id, symbol)
                if hasattr(strategy, 'build_feature_matrix'):
                    features = strategy.build_feature_matrix(bars.set_index('date'), turnover)
                    account = ledger_rule_account(self.engine.ledger, strategy_id, symbol,
                                                  tuple(map(int, index)), int(index[-1]))
                    rule_context = Context(features, tuple(map(int, index)), len(index)-1,
                                           account, deepcopy(self.rule_states.get(state_key, {})))
                decision = (strategy.on_signal_close(rule_context) if hasattr(strategy, 'on_signal_close')
                            else strategy.on_close(rule_context))
                validate_decision(decision, strategy.requirements)
                if hasattr(strategy, 'build_feature_matrix') and decision.state is not None:
                    self.rule_states[state_key] = deepcopy(decision.state)
                side = ('BUY' if decision.intent.weight > 0 else 'SELL') if decision.intent is not None else 'HOLD'
                state = by_symbol[symbol]
                if side == 'BUY' and (state['is_st'] or state['suspended'] or not state['listed'] or state['delisted']):
                    side = 'HOLD'
                record = {'strategy_id': strategy_id, 'symbol': symbol, 'side': side,
                          'reason': decision.reason,
                          'target_weight': decision.intent.weight if decision.intent is not None else 0.0}
                exits = risk_decisions.get(state_key, [])
                # 普通信号已经要求全仓退出时保留该退出；否则仅出售风险触发的分笔。
                if exits and side != 'SELL':
                    record.update(side='SELL', target_weight=0.0, reason='RULE_RISK_EXIT_V3',
                                  exit_lot_ids=sorted(item.lot_id for item in exits))
                result.append(record)
        return result

    def _invariants(self):
        if self.engine.ledger.check_invariants():
            raise ValueError('FORWARD_PAPER_LEDGER_INVARIANT_FAILED')

    def state(self):
        value = {'economic': economic_state(self.engine),
            'equity': self.engine.ledger.current_equity(), 'skipped_intents': self.skips,
            'invariant_errors': self.engine.ledger.check_invariants()}
        if self.rule_exits:
            value['rule_exit_states'] = {key: adapter.state() for key, adapter in sorted(self.rule_exits.items())}
        if self.rule_states:
            value['rule_states'] = {f'{key[0]}:{key[1]}': state for key, state in sorted(self.rule_states.items())}
        if hasattr(self, 'observation'):
            value['observation'] = deepcopy(self.observation)
        return json.loads(canonical_json(value))


def paper_strategy(payload, *, strategy_id):
    if isinstance(payload, dict) and payload.get('version') == 'FULL_POOL_BUY_HOLD_V1':
        from .research_benchmark_v1 import FullPoolBuyHoldStrategyV1
        return FullPoolBuyHoldStrategyV1(payload, strategy_id=strategy_id)
    if isinstance(payload, dict) and payload.get('version') == 'RESEARCH_RULE_STRATEGY_V3':
        from .research_rule_strategy_v3 import ResearchRuleStrategyV3
        return ResearchRuleStrategyV3(payload, strategy_id=strategy_id)
    if isinstance(payload, dict) and payload.get('version') == 'RESEARCH_RULE_STRATEGY_V2':
        from .research_rule_strategy_v2 import ResearchRuleStrategyV2
        return ResearchRuleStrategyV2(payload, strategy_id=strategy_id)
    return validate_candidate(payload, strategy_id=strategy_id)


def ledger_rule_account(ledger, strategy_id, symbol, calendar, day):
    """只根据真实成交和存量lot投影持有/冷却；退出意图不能改变这份账户。"""
    days = tuple(calendar)
    if day not in days:
        raise ValueError('RULE_ACCOUNT_SESSION_MISSING')
    quantity = ledger.position_qty(strategy_id, symbol)
    lots = [lot for lot in ledger.lots.values()
            if lot.strategy_id == strategy_id and lot.symbol == symbol and lot.remaining_quantity]
    entry = min((days.index(int(lot.buy_time.strftime('%Y%m%d'))) for lot in lots), default=None)
    held, last_exit = 0, None
    for trade in ledger.trades:
        if trade.strategy_id == strategy_id and trade.symbol == symbol:
            held += trade.quantity if trade.side == Side.BUY else -trade.quantity
            if held == 0 and trade.side == Side.SELL:
                last_exit = days.index(int(trade.fill_time.strftime('%Y%m%d')))
    stamp = pd.Timestamp(str(day), tz='Asia/Shanghai') + pd.Timedelta(hours=15, minutes=30)
    return {'quantity': int(quantity),
            'sellable_quantity': int(ledger.sellable_quantity(strategy_id, symbol, stamp)),
            'entry_session_index': entry, 'last_exit_session_index': last_exit}
