"""前瞻观察的薄账户适配：使用既有订单、成交、风控和账本。"""
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
from .strategy_interface_v1 import Context
from scripts.probe_all_indicator_strategy_v1 import pilot_registry


class ForwardPaperEngineV1:
    def __init__(self, header):
        self.header = header
        self.policy = header['policy']
        self.symbols = self.policy['symbols']
        self.bars = deepcopy(header['warmup']['bars'])
        self.turn = deepcopy(header['warmup']['turn'])
        self.actions = tuple(header['warmup'].get('corporate_actions', []))
        self.strategies = {key: validate_candidate(value['proposal'], strategy_id=key)
                           for key, value in header['strategies'].items()}
        self.store, self.master = MarketDataStore(), SecurityMaster()
        self._load_bars(self.bars)
        from .portfolio_execution_v1 import PortfolioExecutionPolicyV1
        self.portfolio = PortfolioExecutionPolicyV1.model_validate_json(json.dumps(self.policy['portfolio']))
        config = EngineConfig(initial_cash=self.policy['initial_cash'],
            max_positions=self.portfolio.max_positions,
            max_position_weight=self.portfolio.max_symbol_exposure_bps / 10000,
            max_holding_days=100000, feature_price_mode='raw',
            start_date=header['calendar'][0], end_date=header['calendar'][-1],
            enable_index_filter=False, index_filter_enabled=False, persist_run_manifest=False,
            pit_eligibility_enforced=True, strategy_hash=stable_hash(header['strategies']),
            data_manifest_hash=header['header_id'], calendar_version=stable_hash(header['calendar']))
        calendar = sorted(set(header['calendar']) | {int(row['date']) for row in self.bars})
        self.engine = BacktestEngineV2(self.store, calendar, config=config,
                                      security_master=self.master, seed=0,
                                      source_identity=(header['source_identity'], False))
        self.engine._build()
        self.base_risk_config = deepcopy(self.engine.risk.config)
        self.skips = []

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

    def open(self, snapshot, plan, admissions, *, admission_check=None):
        from .portfolio_execution_v1 import PortfolioExecutionRiskV1, validate_portfolio_plan
        ts = pd.Timestamp(snapshot.get('processed_at', snapshot['received_at']))
        validate_portfolio_plan(plan, policy=self.portfolio, ledger=self.engine.ledger,
            input_identity=plan['input_identity'], event_at=ts, admissions=admissions)
        # OPEN仅装入实际收到的开盘快照；完整日线直到CLOSE阶段才进入账户。
        self._load_bars(self.bars + snapshot['payload']['bars'])
        self._load_states(snapshot)
        self.engine._mark_to_market(ts, EventKind.SESSION_OPEN)
        bars = {row['symbol']: row for row in snapshot['payload']['bars']}
        states = {row['symbol']: row for row in snapshot['payload']['states']}
        prices = {key: self.engine.slippage.apply('BUY', float(row['open'])) for key, row in bars.items()}
        risk = PortfolioExecutionRiskV1(self.engine.ledger, self.base_risk_config,
            policy=self.portfolio, fee_model=self.engine.broker.fee_model,
            admission_check=admission_check or (lambda key: admissions[key]),
            orders_provider=self.engine.order_manager.open_orders, prices=prices, session_at=ts)
        self.engine.risk = self.engine.broker.risk = risk
        for item in plan['intents']:
            symbol, strategy_id, side = item['symbol'], item['strategy_id'], item['side']
            state = states[symbol]
            if state['suspended'] or not state['listed'] or state['delisted']:
                self.skips.append({'intent_id': item['intent_id'], 'reason': 'SECURITY_NOT_TRADABLE'})
                continue
            if side == 'BUY':
                quantity = risk.buy_quantity(strategy_id, symbol, prices[symbol], ts,
                                             target_weight=item['target_weight'])
                position_id = None
            else:
                quantity = self.engine.ledger.position_qty(strategy_id, symbol)
                position = self.engine.ledger.get_position(strategy_id, symbol)
                position_id = position.position_id if position else None
            if quantity <= 0:
                self.skips.append({'intent_id': item['intent_id'], 'reason': 'NO_PERMITTED_QUANTITY'})
                continue
            order = Order(order_id='', strategy_id=strategy_id, intent_id=item['intent_id'],
                signal_id=item['intent_id'], symbol=symbol, side=Side(side), quantity=quantity,
                created_at=ts, eligible_at=ts, time_in_force=TimeInForce.DAY,
                position_id=position_id, priority=item['priority'],
                metadata={'plan_id': plan['plan_id'], 'paper': True,
                          'target_weight':item.get('target_weight',0.0)}, reason='FORWARD_OBSERVED_OPEN')
            self.engine.order_manager.create_order(order, ts)
            self.engine.order_manager.submit(order, ts)
        self.engine.broker.process_orders(EventKind.SESSION_OPEN, ts)
        self.engine._sync_lot_contract_fields()
        self.engine.ledger.snapshot(ts)
        self._invariants()

    def close(self, snapshot):
        ts = pd.Timestamp(snapshot.get('processed_at', snapshot['received_at']))
        self.bars.extend(deepcopy(snapshot['payload']['bars']))
        self.turn.extend(deepcopy(snapshot['payload']['turn']))
        self._load_bars(self.bars)
        self._load_states(snapshot)
        # 未在开盘成交的余单到期；不在收到收盘日线时再次用开盘价成交。
        self.engine.broker.process_orders(EventKind.SESSION_CLOSE, ts)
        self.engine._mark_to_market(ts, EventKind.AFTER_CLOSE)
        self.engine.ledger.snapshot(ts)
        self._invariants()
        return self.decisions(snapshot['payload']['states'])

    def decisions(self, states):
        registry = pilot_registry()
        result = []
        all_bars, all_turn = pd.DataFrame(self.bars), pd.DataFrame(self.turn)
        by_symbol = {row['symbol']: row for row in states}
        for symbol in self.symbols:
            bars = all_bars.loc[all_bars.symbol == symbol].sort_values('date').copy()
            bars['adjustflag'] = '3'
            bars, _ = causal_hfq_bars(bars, self.actions)
            index = pd.Index(bars.date.astype(int), name='date')
            series = {key: pd.Series(bars[key].to_numpy(dtype=float), index=index)
                      for key in ('open','high','low','close','volume','amount','prev_close')}
            vendor = all_turn.loc[all_turn.symbol == symbol].set_index('date').reindex(index)
            turnover = pd.Series(vendor.turn.to_numpy(dtype=float), index=index)
            columns = {}
            definition = next(iter(self.strategies.values())).definition
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
                decision = strategy.on_close(context)
                side = decision.reason if decision.intent is not None else 'HOLD'
                state = by_symbol[symbol]
                if side == 'BUY' and (state['is_st'] or state['suspended'] or not state['listed'] or state['delisted']):
                    side = 'HOLD'
                result.append({'strategy_id': strategy_id, 'symbol': symbol, 'side': side,
                               'reason': decision.reason,
                               'target_weight':decision.intent.weight if decision.intent is not None else 0.0})
        return result

    def _invariants(self):
        if self.engine.ledger.check_invariants():
            raise ValueError('FORWARD_PAPER_LEDGER_INVARIANT_FAILED')

    def state(self):
        return json.loads(canonical_json({'economic': economic_state(self.engine),
            'equity': self.engine.ledger.current_equity(), 'skipped_intents': self.skips,
            'invariant_errors': self.engine.ledger.check_invariants()}))
