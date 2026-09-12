"""已证明DAY数量语义后的降级接线；复用原交易组件，保留严格路径。"""
from dataclasses import asdict
from bisect import bisect_left
import math
import os

import pandas as pd

from .degraded_train_v1 import CONTRACT as ORIGINAL_CONTRACT, capacity
from .fixed_account_rules import FixedAccountRules
from .train_account_runner_v1 import ExactStateMasterV1, TrainingAccountExecutionFailure
from chanlun_trader.engine.security_state import SecurityState, ChinaPriceLimitModel
from chanlun_trader.engine.corporate_action_engine_v1 import CorporateActionBacktestEngineV1
from chanlun_trader.engine.engine import EngineConfig
from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.fill import DailyBarFillModel
from chanlun_trader.engine.time_types import EventKind
from chanlun_trader.engine.signal import Side

CONTRACT = {**ORIGINAL_CONTRACT, 'adapter_version': 'DEGRADED_EXECUTION_V2',
    'volume_semantics': 'TDX_DAY_VOLUME_SEMANTICS_V1',
    'availability_clock': 'SOURCE_T_VISIBLE_T_PLUS_1_OPEN_DECISION_THEN_SAME_OPEN_ATTEMPT',
    'state_usage': 'PRIOR_SESSION_SELECTION_CURRENT_SESSION_EXECUTION_REALITY_MODELED',
    'hazard_conditioning_bias': True}


class DegradedStateMasterV1(ExactStateMasterV1):
    """当前session状态仅供成交现实拒绝；选股使用前session状态。"""
    def row(self, symbol, day, ts):
        if (symbol, day) not in self.rows.index:
            raise ValueError('EXECUTION_STATE_UNKNOWN')
        row = self.rows.loc[(symbol, day)].to_dict()
        if (row['st_status'] not in {'NORMAL', 'ST'} or
                row['suspension_status'] not in {'TRADING', 'SUSPENDED'} or
                row['eligibility_status'] == 'CONFLICT'):
            raise ValueError('EXECUTION_STATE_UNKNOWN_OR_CONFLICT')
        return row

    def as_of(self, symbol, ts):
        day = int(ts.strftime('%Y%m%d')); row = self.row(symbol, day, ts)
        boards = {'SH_MAIN':'MAIN','SZ_MAIN':'MAIN','MAIN':'MAIN',
                  'GEM':'CHINEXT','CHINEXT':'CHINEXT','STAR':'STAR'}
        if row['board'] not in boards:
            raise ValueError('EXECUTION_BOARD_UNKNOWN')
        return SecurityState(symbol, day, listed=bool(row['listed']), delisted=bool(row['delisted']),
            is_st=row['st_status']=='ST', board=boards[row['board']],
            suspended=row['suspension_status']=='SUSPENDED')


class DegradedPriceLimitV1(ChinaPriceLimitModel):
    def __init__(self, master):
        super().__init__(master)
        self.unsupported_symbols = set()

    def allowed(self, symbol, ts, buy):
        if symbol in self.unsupported_symbols:
            return False, 'ACCOUNTING_UNSUPPORTED_CORPORATE_ACTION'
        try:
            r = self.master.row(symbol, int(ts.strftime('%Y%m%d')), ts)
            self.master.as_of(symbol, ts)
        except ValueError as exc:
            return False, str(exc)
        if r['suspension_status']=='SUSPENDED':
            return False, 'SUSPENDED'
        if r['delisted'] or not r['listed']:
            return False, 'LIFECYCLE_NOT_EXECUTABLE'
        if buy and (r['st_status']!='NORMAL' or not r['universe_member'] or r['eligibility_status']!='ELIGIBLE'):
            return False, 'KNOWN_INELIGIBLE'
        return True, 'OK'

    def can_buy_at_open(self, symbol, ts, bar):
        ok, reason = self.allowed(symbol, ts, True)
        if not ok:
            return ok, reason
        if bar is None or not math.isfinite(float(bar.get('prev_close', float('nan')))):
            return False, 'PRIOR_SESSION_PRICE_UNKNOWN'
        return super().can_buy_at_open(symbol, ts, bar)

    def can_sell_at_open(self, symbol, ts, bar):
        ok, reason = self.allowed(symbol, ts, False)
        if not ok:
            return ok, reason
        if bar is None or not math.isfinite(float(bar.get('prev_close', float('nan')))):
            return False, 'PRIOR_SESSION_PRICE_UNKNOWN'
        return super().can_sell_at_open(symbol, ts, bar)


class IntersectionFillV1(DailyBarFillModel):
    def __init__(self):
        super().__init__(max_participation_rate=.10)
        self.audit = []

    def try_fill(self, order, ts, bar):
        info = capacity(float(bar.get('volume', float('nan'))), order.remaining_quantity, schema_proven=True)
        qty = info['accepted_qty']
        if order.side == Side.BUY:
            qty = qty // 100 * 100
        self.audit.append({**info, 'order_id':order.order_id, 'symbol':order.symbol, 'timestamp':str(ts),
            'capacity_allowed_lot_quantity':qty, 'final_fill_quantity_recorded_separately':True,
            'volume_known_asof':bar.get('volume_known_asof')})
        if qty <= 0:
            return None, 0, 'PARTICIPATION_LIMIT'
        return float(bar['open']), qty, 'OK'


class DegradedAccountEngineV2(CorporateActionBacktestEngineV1):
    def _sync_lot_contract_fields(self):
        # 原Ledger按自然日生成T+1；在本显式独立日历上只向后投影，绝不提前可卖。
        for lot in self.ledger.lots.values():
            day=int(lot.sellable_from.strftime('%Y%m%d'))
            if not self.calendar.contains(day):
                index=bisect_left(self.calendar.trading_days,day)
                if index<len(self.calendar.trading_days):
                    before=lot.sellable_from
                    lot.sellable_from=pd.Timestamp(str(self.calendar.trading_days[index])+' 09:30',tz='Asia/Shanghai')
                    self.sellability_projections.append({'lot_id':lot.lot_id,'old_time':str(before),
                        'new_time':str(lot.sellable_from),'reason':'T1_CEILING_TO_INDEPENDENT_SESSION'})
        super()._sync_lot_contract_fields()

    def _process_clock_event(self, ev, pending_signals, strategy_fn=None, exit_fn=None):
        if ev.kind == EventKind.SESSION_OPEN:
            day = int(ev.timestamp.strftime('%Y%m%d'))
            for lot in self.ledger.lots.values():
                if lot.remaining_quantity and day in self.hazards.get(lot.symbol, ()):
                    self.unsupported_lots.setdefault(lot.lot_id, {'date':day,'symbol':lot.symbol,
                        'status':'ACCOUNTING_UNSUPPORTED_CORPORATE_ACTION'})
                    self.broker.price_limit.unsupported_symbols.add(lot.symbol)
        # 原引擎先处理已到期退出，再让T日输入于T+1开盘可见并生成本次买入。
        super()._process_clock_event(ev, pending_signals, None, exit_fn)
        if ev.kind == EventKind.AFTER_CLOSE:
            self.account_history[-1]['marked_positions']=[{'symbol':p.symbol,'quantity':p.quantity,
                'ledger_mark':self.ledger.last_price.get(p.symbol,p.average_cost),
                'market_value':p.quantity*self.ledger.last_price.get(p.symbol,p.average_cost)}
                for p in self.ledger.positions.values() if p.quantity]
        if ev.kind == EventKind.SESSION_OPEN and strategy_fn is not None:
            self.active_check()
            for sig in strategy_fn(None, ev.timestamp, int(ev.timestamp.strftime('%Y%m%d'))):
                self.add_signal(sig)
                intent = self._signal_to_intent(sig)
                order = self._submit_intent(intent, ev.timestamp)
                if order is not None:
                    # 保持generated_at不早于模型可见时间；NEXT_SESSION以源T为参照。
                    if self.calendar.prev_day(int(ev.timestamp.strftime('%Y%m%d'))) != sig.metadata['source_session']:
                        raise ValueError('MODELED_NEXT_OPEN_SOURCE_SESSION_CONFLICT')
                    order.eligible_at = ev.timestamp
                else:
                    self.entry_rejections.append({'symbol':sig.symbol,'date':int(ev.timestamp.strftime('%Y%m%d')),
                        'reason':'SIZER_OR_POSITION_REJECT','signal_id':sig.signal_id})
            self.broker.process_orders(EventKind.SESSION_OPEN, ev.timestamp)
            self._sync_lot_contract_fields()
            self.ledger.snapshot(ev.timestamp)


def run_degraded_account(bundle, source_identity, active_check=None):
    return _run_account(bundle, source_identity, active_check, CONTRACT)


def _run_account(bundle, source_identity, active_check, contract):
    synthetic = os.environ.get('CHANLUN_TEST_ISOLATION')=='1' and source_identity[0].startswith('SYNTHETIC')
    if active_check is None and not synthetic:
        raise PermissionError('GOVERNED_DEGRADED_EXECUTION_REQUIRED')
    active_check = active_check or (lambda: None)
    if not synthetic:
        receipt = active_check()
        if receipt['plan']['input_identity']!=bundle.input_identity or list(receipt['plan']['contracts'].values())!=[contract]:
            raise PermissionError('DEGRADED_RECEIPT_INPUT_OR_CONTRACT_CONFLICT')
    store = MarketDataStore()
    for symbol, rows in bundle.daily.groupby('symbol',sort=True):
        store.add_daily_raw(symbol, rows.set_index('date')[['open','high','low','close','volume','amount','prev_close']])
    fill = IntersectionFillV1()
    config = EngineConfig(initial_cash=10000,max_positions=3,max_position_weight=1/3,lot_size=100,
        commission_rate=.00025,min_commission=5,stamp_tax_rate=.0005,slippage_bps=.001,
        feature_price_mode='raw',enable_index_filter=False,index_filter_enabled=False,fill_model=fill,
        data_manifest_hash=bundle.input_identity,universe_version_hash=bundle.pool_identity,
        feature_version_hash=bundle.factor_identity,calendar_version=bundle.calendar_identity,
        persist_run_manifest=False,strategy_hash=bundle.contract_identity)
    engine = DegradedAccountEngineV2(store,bundle.calendar,config,
        security_master=DegradedStateMasterV1(bundle.states),source_identity=source_identity,action_dataset=bundle.actions)
    engine.active_check=active_check;engine.price_limit_factory=DegradedPriceLimitV1
    engine.hazards=bundle.hazards;engine.unsupported_lots={};engine.entry_rejections=[]
    engine.sellability_projections=[]
    factor_days={int(d):rows for d,rows in bundle.ready_factors.groupby('timestamp')}
    ranks=[];decisions=[];hazard_checks=[]
    rules=FixedAccountRules(contract)

    def signals(view, ts, day):
        prior=engine.calendar.prev_day(day)
        decision=rules.entries(factor_days.get(prior),engine.calendar,engine.ledger,
            bundle.hazards,engine.unsupported_lots,ts)
        if decision.ranking is not None:
            ranks.append(decision.ranking)
        hazard_checks.extend(decision.checks)
        engine.entry_rejections.extend(record for record in decision.checks if record['reason']!='OK')
        return decision.signals

    def exits(view, ts, day, ledger):
        if ts.hour!=15 or ts.minute!=30:
            return []
        found=rules.exits(engine.calendar,ledger,engine.unsupported_lots,ts)
        decisions.extend(asdict(d) for d in found)
        return found

    try:
        result=engine.run(strategy_fn=signals,exit_fn=exits)
    except Exception as exc:
        raise TrainingAccountExecutionFailure(str(exc),{'status':'INCOMPLETE_ENGINE_FAILURE',
            'exception_type':type(exc).__name__,'message':str(exc),'account_checkpoint':engine.ledger.checkpoint(),
            'orders':[asdict(o) for o in engine.order_manager.orders.values()],
            'events':engine.event_log.to_records(),'daily_holdings':engine.account_history}) from exc
    snapshots=[s for s in result.ledger.snapshots if s.timestamp.hour==15 and s.timestamp.minute==30 and int(s.timestamp.strftime('%Y%m%d'))>=20220801]
    partial=bool(engine.unsupported_lots)
    fills=result.ledger.executed_fills
    daily_index=bundle.daily.set_index(['symbol','date'])
    for f in fills:
        day=int(pd.Timestamp(f['fill_time']).strftime('%Y%m%d'))
        reference=float(daily_index.loc[(f['symbol'],day),'open'])
        f['reference_open']=reference
        f['slippage_cost']=(f['price']-reference)*(1 if f['side']==Side.BUY else -1)*f['quantity']
    closed={key:0.0 for key,lot in result.ledger.lots.items() if lot.remaining_quantity==0}
    for f in fills:
        if f['side']==Side.SELL:
            for a in f['lot_allocations']:
                if a['lot_id'] in closed:
                    closed[a['lot_id']]+=a['realized_pnl']
    positive=sum(v for v in closed.values() if v>0);negative=-sum(v for v in closed.values() if v<0)
    peak=10000.;drawdown=0.
    for s in snapshots:
        peak=max(peak,s.equity);drawdown=max(drawdown,1-s.equity/peak)
    metrics=None if partial else {'ending_equity':snapshots[-1].equity,'train_net_return':snapshots[-1].equity/10000-1,
        'max_drawdown':drawdown,'closed_lots':len(closed),'wins':sum(v>0 for v in closed.values()),
        'losses':sum(v<0 for v in closed.values()),'zeros':sum(v==0 for v in closed.values()),
        'profit_factor':positive/negative if negative else None,'total_fees':result.ledger.total_fees,
        'total_stamp_tax':sum(f['stamp_tax'] for f in fills),
        'total_slippage_cost':sum(f['slippage_cost'] for f in fills),
        'turnover_gross_over_initial_cash':sum(f['price']*f['quantity'] for f in fills)/10000,
        'maximum_single_symbol_equity_weight':max((p['market_value']/h['equity'] for h in engine.account_history
            if h['equity']>0 for p in h['marked_positions']),default=0),
        'unique_bought_symbols':len({f['symbol'] for f in fills if f['side']==Side.BUY})}
    return {'type':contract['result_type'],'status':'PARTIAL_UNSUPPORTED_EVENT' if partial else 'COMPLETE',
        'contract':contract,'metrics':metrics,'rankings':ranks,'signals':[asdict(s) for s in result.signals],
        'orders':[asdict(o) for o in result.orders.orders.values()],'fills':fills,
        'trades':[asdict(t) for t in result.trades],'events':result.event_log.to_records(),
        'candidate_rejections':engine.entry_rejections,'capacity_decisions':fill.audit,
        'hazard_decisions':hazard_checks,'unsupported_lots':engine.unsupported_lots,'exit_decisions':decisions,
        'sellability_projections':engine.sellability_projections,
        'daily_account':[asdict(s) for s in snapshots],'daily_holdings':engine.account_history,
        'final_account_checkpoint':result.ledger.checkpoint(),'input_identity':bundle.input_identity}
