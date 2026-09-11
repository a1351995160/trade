"""仅显式离线调用的公司行动账本V1；旧PortfolioLedger行为不变。"""
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
import json
import math

import pandas as pd

from .ledger import PortfolioLedger, LedgerSnapshot, TradeRecord
from .position import Position, PositionLot
from .time_types import ensure_aware


class CorporateActionError(ValueError):
    pass


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def at_open(day):
    if not isinstance(day,int):
        raise CorporateActionError('ACTION_DATE_MISSING')
    return pd.Timestamp(str(day),tz='Asia/Shanghai')+pd.Timedelta(hours=9,minutes=30)


@dataclass
class ActionSnapshotV1(LedgerSnapshot):
    cash_receivable: float = 0.0
    available_cash: float = 0.0


class CorporateActionAccountingV1(PortfolioLedger):
    version = 'CorporateActionAccountingV1'

    def __init__(self, initial_cash, events, dataset_id):
        super().__init__(initial_cash)
        self.events=json.loads(json.dumps(events,allow_nan=False))
        if len({e['event_id'] for e in events})!=len(events):
            raise CorporateActionError('DUPLICATE_EVENT_ID')
        self.dataset_id=dataset_id
        self.events_hash=identity(self.events)
        self.entitlements={}
        self.receivables={}
        self.applied=set()
        self.payments=set()
        self.action_audit=[]
        self.action_income=0.0
        self.blocked_reason=None
        self.executed_fills=[]
        self.pending_share_credits={}
        self.bonus_parent_lots={}

    @property
    def cash_receivable(self):
        return sum(self.receivables.values())

    def current_equity(self):
        return super().current_equity()+self.cash_receivable

    def snapshot(self, ts):
        old=super().snapshot(ts)
        snap=ActionSnapshotV1(**asdict(old),cash_receivable=self.cash_receivable,available_cash=self.available_cash())
        snap.equity=round(snap.equity+self.cash_receivable,4)
        self.snapshots[-1]=snap
        return snap

    def _reject(self, reason):
        self.blocked_reason=reason
        raise CorporateActionError(reason)

    def _check_active(self):
        if self.blocked_reason:self._reject(self.blocked_reason)

    @property
    def certification_status(self):
        return 'INVALID' if self.blocked_reason else super().certification_status

    def _cash_terms(self,event):
        try:
            terms=event['terms']; amount=float(terms['cash_per_share']); tax=terms['tax_rule']
            if event['units']!='CNY_PER_SHARE' or not math.isfinite(amount) or amount<0 or tax['source'] in {None,'','UNKNOWN'} or event['source'] in {None,'','UNKNOWN'}:
                raise ValueError()
            if tax['kind']=='EXPLICIT_NET':net=amount
            elif tax['kind']=='EXPLICIT_FIXED_PER_SHARE':net=amount-float(tax['tax_per_share'])
            else:raise ValueError()
            if not math.isfinite(net) or not 0<=net<=amount:raise ValueError()
            record,payment=at_open(event['record_date']),at_open(event['payment_date'])
            if not record<at_open(event['effective_date'])<=payment:raise ValueError()
            return net
        except (KeyError,TypeError,ValueError):
            self._reject('CASH_DIVIDEND_TERMS_OR_TAX_UNKNOWN')

    def on_close(self,ts):
        self._check_active(); ts=ensure_aware(ts)
        day=int(ts.strftime('%Y%m%d'))
        if ts.hour<15:self._reject('ENTITLEMENT_BEFORE_RECORD_CLOSE')
        for event in self.events:
            if event['event_type'] in {'CASH_DIVIDEND','BONUS','CAPITALIZATION'} and event.get('record_date')==day:
                key=event['event_id']
                if key not in self.entitlements:
                    qty=self.total_quantity(event['symbol'])
                    if qty and event['event_type']=='CASH_DIVIDEND':self._cash_terms(event)
                    self.entitlements[key]=qty if event['event_type']=='CASH_DIVIDEND' else {
                        lot.lot_id:lot.remaining_quantity for lot in self.lots.values()
                        if lot.symbol==event['symbol'] and lot.remaining_quantity}
                    self.action_audit.append({'event_id':key,'phase':'RECORD_CLOSE','quantity':qty,'timestamp':str(ts)})

    def on_open(self,ts):
        self._check_active(); ts=ensure_aware(ts)
        day=int(ts.strftime('%Y%m%d'))
        for event in self.events:
            key=event['event_id']; effective=event['effective_date']
            if effective<=day and key not in self.applied:
                kind=event['event_type']; qty=self.total_quantity(event['symbol'])
                if effective<day:
                    self._reject('MISSED_ACTION_EVENT_REQUIRES_CHECKPOINT_RECONCILIATION')
                if kind=='CASH_DIVIDEND':
                    if key not in self.entitlements:
                        self._reject('DIVIDEND_ENTITLEMENT_HISTORY_MISSING')
                    entitled=self.entitlements[key]
                    if entitled:
                        net=self._cash_terms(event)
                        self.receivables[key]=entitled*net
                        self.action_income+=entitled*net
                elif qty:
                    if kind in {'SPLIT','BONUS','CAPITALIZATION','CONSOLIDATION'}:
                        self._split(event,ts)
                    elif kind=='RIGHTS':self._reject('TRADE_REALITY_UNSUPPORTED_CORPORATE_ACTION_RIGHTS')
                    elif kind in {'DELISTING','TERMINATION'}:self._reject('TRADE_REALITY_UNSUPPORTED_DELISTING_SETTLEMENT')
                    else:self._reject('TRADE_REALITY_UNSUPPORTED_CORPORATE_ACTION_UNKNOWN')
                self.applied.add(key)
                self.action_audit.append({'event_id':key,'phase':'EFFECTIVE','timestamp':str(ts),'held_quantity':qty})
            if key in self.receivables and key not in self.payments and event.get('payment_date',day+1)<=day:
                amount=self.receivables.pop(key)
                self.cash+=amount
                self.payments.add(key)
                self.action_audit.append({'event_id':key,'phase':'PAYMENT','amount':amount,'timestamp':str(ts)})
        for key,credit in list(self.pending_share_credits.items()):
            if credit['credit_date']<=day:
                self.action_audit.append({'event_id':key,'phase':'SHARE_CREDIT','lots':credit['lots'],'timestamp':str(ts)})
                del self.pending_share_credits[key]

    def _split(self,event,ts):
        try:
            if event['source'] in {None,'','UNKNOWN'}:self._reject('SHARE_ACTION_SOURCE_UNKNOWN')
            if any(self.lots[key].symbol==event['symbol'] for item in self.pending_share_credits.values() for key in item['lots']):
                self._reject('OVERLAPPING_UNCREDITED_SHARE_ACTIONS_UNSUPPORTED')
            terms=event['terms']
            numerator,denominator=terms['ratio_numerator'],terms['ratio_denominator']
            if type(numerator) is not int or type(denominator) is not int or min(numerator,denominator)<=0:
                raise ValueError()
            ratio=Fraction(numerator,denominator)
            if event['units']!='NEW_SHARES_PER_OLD_SHARE':raise ValueError()
            credit,tradable=at_open(event['share_credit_date']),at_open(event['tradable_date'])
            if credit<at_open(event['effective_date']) or tradable<credit:
                self._reject('SHARE_CREDIT_TIMING_UNSUPPORTED_V1')
            lots=[lot for lot in self.lots.values() if lot.symbol==event['symbol'] and lot.remaining_quantity]
            bonus=event['event_type'] in {'BONUS','CAPITALIZATION'}
            if bonus:
                captured=self.entitlements.get(event['event_id'])
                if captured is None or captured!={lot.lot_id:lot.remaining_quantity for lot in lots}:
                    self._reject('BONUS_ENTITLEMENT_CHANGED_OR_MISSING_V1')
                if ratio<=1:self._reject('BONUS_RATIO_MUST_EXCEED_ONE')
            changes=[]
            for lot in lots:
                total=lot.quantity*ratio; remaining=lot.remaining_quantity*ratio
                if total.denominator!=1 or remaining.denominator!=1:self._reject('FRACTIONAL_SHARES_UNSUPPORTED_V1')
                changes.append((lot,int(total),int(remaining)))
        except (KeyError,TypeError,ValueError,ZeroDivisionError):
            if self.blocked_reason:raise
            self._reject('SHARE_ACTION_TERMS_UNKNOWN')
        # 所有lot验证后统一变更；成本、原成交记录、entry index不变。
        credited_lots={}
        for lot,total,remaining in changes:
            if bonus:
                cost=lot.cost
                child=replace(lot,lot_id=self._new_lot_id(),quantity=total-lot.quantity,
                    remaining_quantity=remaining-lot.remaining_quantity,cost=cost*(1-1/float(ratio)),
                    sellable_from=max(lot.sellable_from,tradable),sellable_from_session=int(tradable.strftime('%Y%m%d')),
                    sellable_from_session_index=None)
                lot.cost=cost/float(ratio)
                self.lots[child.lot_id]=child
                self.bonus_parent_lots[child.lot_id]=lot.lot_id
                credited_lots[child.lot_id]=child.remaining_quantity
            else:
                lot.quantity=total; lot.remaining_quantity=remaining
                lot.sellable_from=max(lot.sellable_from,tradable)
                lot.sellable_from_session=int(lot.sellable_from.strftime('%Y%m%d'))
                lot.sellable_from_session_index=None
                credited_lots[lot.lot_id]=lot.remaining_quantity
        # quantity表示生效后的经济归属；未到股份到账日的部分单独列示、不可卖。
        self.pending_share_credits[event['event_id']]={'credit_date':event['share_credit_date'],'lots':credited_lots}
        self._sync_costs(event['symbol'])
        if event['symbol'] in self.last_price:
            self.last_price[event['symbol']]/=float(ratio)

    def _sync_costs(self,symbol):
        for pos in self.positions.values():
            if pos.symbol!=symbol:continue
            lots=[lot for lot in self.lots.values() if lot.position_id==pos.position_id and lot.remaining_quantity]
            pos.quantity=sum(lot.remaining_quantity for lot in lots)
            remaining_cost=sum(lot.cost*lot.remaining_quantity/lot.quantity for lot in lots)
            pos.average_cost=remaining_cost/pos.quantity if pos.quantity else 0.0

    def apply_fill(self,fill,order_id='',lot_id=None):
        self._check_active()
        before={key:(lot.remaining_quantity,lot.cost/lot.quantity) for key,lot in self.lots.items()
            if lot.symbol==fill.symbol and lot.remaining_quantity}
        result=super().apply_fill(fill,order_id,lot_id)
        if result[1]=='OK':
            self._sync_costs(fill.symbol)
            allocations=[]
            for key,(remaining,basis) in before.items():
                sold=remaining-self.lots[key].remaining_quantity
                if sold>0:allocations.append({'lot_id':key,'quantity':sold,
                    'realized_pnl':(fill.price-basis)*sold-fill.total_fee*sold/fill.quantity})
            if not allocations:allocations=[{'lot_id':result[0].lot_id,'quantity':fill.quantity,'realized_pnl':0.0}]
            self.executed_fills.append({**asdict(fill),'lot_allocations':allocations})
        return result

    def checkpoint(self):
        state={key:value for key,value in self.__dict__.items() if key not in {'positions','lots','trades','snapshots','events','corporate_action_guard'}}
        state.update(positions={k:asdict(v) for k,v in self.positions.items()},lots={k:asdict(v) for k,v in self.lots.items()},
            trades=[asdict(v) for v in self.trades],snapshots=[asdict(v) for v in self.snapshots],
            applied=sorted(self.applied),payments=sorted(self.payments))
        value=json.loads(json.dumps({'version':self.version,'state':state},default=str,allow_nan=False))
        return {**value,'checkpoint_hash':identity(value)}

    @classmethod
    def restore(cls,checkpoint,events,dataset_id):
        value={k:v for k,v in checkpoint.items() if k!='checkpoint_hash'}
        if checkpoint['checkpoint_hash']!=identity(value) or value['version']!=cls.version:
            raise CorporateActionError('CHECKPOINT_IDENTITY_CONFLICT')
        state=value['state']; ledger=cls(state['initial_cash'],events,dataset_id)
        if state['dataset_id']!=dataset_id or state['events_hash']!=ledger.events_hash:
            raise CorporateActionError('CHECKPOINT_DATASET_CONFLICT')
        ledger.__dict__.update(state)
        ledger.positions={k:Position(**v) for k,v in state['positions'].items()}
        ledger.lots={k:PositionLot(**v) for k,v in state['lots'].items()}
        ledger.trades=[TradeRecord(**v) for v in state['trades']]
        ledger.snapshots=[ActionSnapshotV1(**v) for v in state['snapshots']]
        ledger.applied=set(state['applied']); ledger.payments=set(state['payments'])
        if ledger.check_invariants():raise CorporateActionError('CHECKPOINT_LEDGER_INVARIANT')
        return ledger
