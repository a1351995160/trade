"""显式离线TRAIN公司行动接线，复用原时钟、broker、订单与退出流程。"""
from .engine import BacktestEngineV2
from .corporate_accounting_v1 import CorporateActionAccountingV1
from .time_types import EventKind
from .signal import Side
from dataclasses import replace,asdict
from fractions import Fraction


class CorporateActionBacktestEngineV1(BacktestEngineV2):
    def __init__(self,*args,action_dataset,**kwargs):
        super().__init__(*args,**kwargs)
        if self.config.mode!='DAILY' or self.config.corporate_action_guard is not None:
            raise ValueError('EXPLICIT_DAILY_ACCOUNTING_V1_REQUIRED')
        if not set(self.store.symbols())<=set(action_dataset.coverage_symbols):
            raise ValueError('ACTION_DATASET_COVERAGE_MISSING')
        self.action_dataset=action_dataset
        self.account_history=[]
        self.config.execution_model_version='TRAIN_CORPORATE_ACTION_ACCOUNTING_V1:'+action_dataset.manifest_sha256

    def _build(self):
        super()._build()
        self.ledger=CorporateActionAccountingV1(self.config.initial_cash,self.action_dataset.events,self.action_dataset.dataset_id)
        self.broker.ledger=self.ledger
        self.risk.ledger=self.ledger
        if hasattr(self,'price_limit_factory'):
            self.broker.price_limit=self.price_limit_factory(self.security_master)

    def _sync_lot_contract_fields(self):
        super()._sync_lot_contract_fields()
        for lot in self.ledger.lots.values():
            day=int(lot.sellable_from.strftime('%Y%m%d'))
            lot.sellable_from_session=day
            try:lot.sellable_from_session_index=self.calendar.date_index(day)
            except KeyError:
                if day<=self.calendar.trading_days[-1]:self.ledger._reject('SELLABLE_DATE_NOT_IN_CALENDAR')
                lot.sellable_from_session_index=None

    def _submit_forced_exits(self,ts):
        # 本限定用途由原PortfolioExitEvaluator按实际entry index+3统一决定退出。
        return None

    def _process_clock_event(self,ev,pending_signals,strategy_fn=None,exit_fn=None):
        if hasattr(self,'active_check'):self.active_check()
        if ev.kind==EventKind.SESSION_OPEN:
            before={key:lot.remaining_quantity for key,lot in self.ledger.lots.items()}
            self.ledger.on_open(ev.timestamp)
            for order in list(self.order_manager.open_orders()):
                if order.side!=Side.SELL or not order.lot_id or not before.get(order.lot_id):continue
                eligible=order.eligible_at if order.eligible_at is not None else self.broker.resolve_eligible_at(order,ev.timestamp)
                for child,parent in self.ledger.bonus_parent_lots.items():
                    if parent!=order.lot_id or child in before:continue
                    lot=self.ledger.lots[child]
                    spawned=replace(order,order_id='',lot_id=child,quantity=lot.remaining_quantity,
                        remaining_quantity=lot.remaining_quantity,filled_quantity=0,avg_fill_price=0,total_fee=0,
                        created_at=ev.timestamp,eligible_at=eligible,metadata={**order.metadata,'corporate_action_parent_order':order.order_id,'lot_id':child})
                    self.order_manager.create_order(spawned,ev.timestamp)
                    self.order_manager.submit(spawned,ev.timestamp)
                    self._exit_order_lots[spawned.order_id]=child
                lot=self.ledger.lots[order.lot_id]
                if lot.remaining_quantity==before[order.lot_id]:continue
                remaining=Fraction(order.remaining_quantity*lot.remaining_quantity,before[order.lot_id])
                if remaining.denominator!=1:self.ledger._reject('FRACTIONAL_PENDING_ORDER_UNSUPPORTED')
                replacement=replace(order,order_id='',quantity=int(remaining),remaining_quantity=int(remaining),
                    filled_quantity=0,avg_fill_price=0,total_fee=0,created_at=ev.timestamp,eligible_at=eligible,
                    metadata={**order.metadata,'corporate_action_replaces_order':order.order_id})
                self.order_manager.cancel(order,ev.timestamp,'CORPORATE_ACTION_QUANTITY_REPLACED')
                self.order_manager.create_order(replacement,ev.timestamp)
                self.order_manager.submit(replacement,ev.timestamp)
                self._exit_order_lots.pop(order.order_id,None)
                self._exit_order_lots[replacement.order_id]=order.lot_id
        super()._process_clock_event(ev,pending_signals,strategy_fn,exit_fn)
        if ev.kind==EventKind.AFTER_CLOSE:
            self.ledger.on_close(ev.timestamp)
            self.account_history.append({'date':int(ev.timestamp.strftime('%Y%m%d')),
                'positions':[asdict(p) for p in self.ledger.positions.values() if p.quantity],
                'lots':[asdict(lot) for lot in self.ledger.lots.values() if lot.remaining_quantity],
                'receivables':dict(self.ledger.receivables),'available_cash':self.ledger.available_cash(),
                'pending_share_credits':dict(self.ledger.pending_share_credits),
                'cash':self.ledger.cash,'equity':self.ledger.current_equity()})
