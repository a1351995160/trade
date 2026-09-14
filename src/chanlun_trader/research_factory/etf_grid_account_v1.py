"""ETF固定模型账户；复用原Broker、订单、费用类型和公司行动账本。"""
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
import math
import numpy as np
import pandas as pd

from ..engine.asof import MarketDataStore
from ..engine.broker import BrokerSimulator
from ..engine.corporate_accounting_v1 import CorporateActionAccountingV1
from ..engine.fee import FeeModel, FeeBreakdown
from ..engine.fill import EventOpenFillModel
from ..engine.order import Order
from ..engine.order_manager import OrderManager
from ..engine.risk import RiskConfig, RiskManager, RiskResult
from ..engine.security_state import ChinaPriceLimitModel
from ..engine.signal import Side
from ..engine.slippage import FixedBpsSlippage
from ..engine.time_types import TradingCalendar, TradingClock, EventKind
from .etf_grid_spec_v1 import SPEC
from .etf_grid_rules_v1 import grid_spacing, state, adjacent_grid


CONTRACT={**SPEC.as_dict(),'version':'ETF_GRID_ACCOUNT_V1',
    'commission_rate':.00025,'other_fee_rate':.00001,'stamp_tax_rate':0,
    'other_fee_evidence':'USER_COST_ASSUMPTION_NOT_BROKER_ATTESTED',
    'indicator_prices':'RAW','fill_prices':'RAW','dividends':'CASH_ENTITLEMENT_THEN_PAYMENT',
    'atr_rsi_smoothing':'WILDER_SMA_SEED','initial_anchor':'LAST_WARMUP_CLOSE',
    'one_grid_order_per_close':True,'sell_priority':'LOWEST_LEVEL_NUMBER_FIRST',
    'grid_buy_priority':'LOWEST_UNUSED_LEVEL_NUMBER_FIRST','failed_buy_level':'USED_ON_TRIGGER',
    'economics_gate':'200*BUY_LEVEL*SPACING>=20','initial_core_sizing':'PRIOR_CLOSE_FEE_INCLUSIVE_100_LOT',
    'benchmark':'9000_BUY_HOLD_PLUS_1000_CASH','terminal':'MARK_TO_MARKET_NO_FORCED_SALE',
    'result_type':'ETF_TRAIN_ACCOUNT_MODEL_NOT_FOR_QUALIFICATION'}


class ETFFeesV1(FeeModel):
    def calc(self,side,quantity,price):
        value=quantity*price
        commission=max(value*CONTRACT['commission_rate'],SPEC.minimum_commission)
        other=value*CONTRACT['other_fee_rate']
        return FeeBreakdown(round(commission,4),0,round(other,4),round(commission+other,4))


class ETFLimitsV1(ChinaPriceLimitModel):
    def limit_prices(self,symbol,ts,prev_close):
        def tick(x):return float(Decimal(str(x)).quantize(Decimal('.001'),rounding=ROUND_HALF_UP))
        return tick(prev_close*1.1),tick(prev_close*.9)


class ETFBucketRiskV1(RiskManager):
    """同ETF允许分批入场；费用计入桶预算，现金桶不能互借。"""
    def __init__(self,ledger):
        super().__init__(ledger,RiskConfig(max_positions=1,max_position_weight=1,require_universe=False))

    def pre_trade(self,order,ts,estimated_price,index_ok=True):
        result=super().pre_trade(order,ts,estimated_price,index_ok)
        if not result.ok and result.reason!='MAX_POSITIONS':return result
        if order.side==Side.BUY:
            if order.symbol!=SPEC.symbol:return RiskResult(False,'FIXED_ETF_ONLY')
            if order.metadata.get('grid') and order.quantity!=SPEC.grid_quantity:
                return RiskResult(False,'GRID_REQUIRES_200_SHARES')
            fee=ETFFeesV1().calc('BUY',order.quantity,estimated_price).total_fee
            cost=order.quantity*estimated_price+fee
            invested=sum(lot.cost*lot.remaining_quantity/lot.quantity for lot in self.ledger.lots.values())
            cap=order.metadata['capital_cap']
            if cost>self.ledger.available_cash()+1e-8 or cost+invested>cap+1e-8:
                return RiskResult(False,'FEE_INCLUSIVE_BUCKET_CAP')
        return RiskResult(True)


def indicators(frame):
    out=frame.copy(deep=True)
    for n in SPEC.ma_periods:out[f'ma{n}']=out.close.rolling(n,min_periods=n).mean()
    def wilder(values,n):
        values=np.asarray(values,dtype=float);answer=np.full(len(values),np.nan)
        for i in range(n-1,len(values)):
            if i==n-1:
                if np.isfinite(values[:n]).all():answer[i]=values[:n].mean()
            elif np.isfinite(answer[i-1]) and np.isfinite(values[i]):answer[i]=(answer[i-1]*(n-1)+values[i])/n
        return answer
    prior=out.close.shift(1)
    tr=pd.concat([out.high-out.low,(out.high-prior).abs(),(out.low-prior).abs()],axis=1).max(axis=1)
    out['atr']=wilder(tr,SPEC.atr_period)
    delta=out.close.diff().iloc[1:]
    gain=wilder(delta.clip(lower=0),SPEC.rsi_period)
    loss=wilder((-delta).clip(lower=0),SPEC.rsi_period)
    rsi=np.full(len(gain),np.nan)
    for i,(g,l) in enumerate(zip(gain,loss)):
        if math.isfinite(g) and math.isfinite(l):rsi[i]=50 if g+l==0 else 100*g/(g+l)
    out['rsi']=np.r_[np.nan,rsi]
    return out


def run_account(frame,actions,*,benchmark,active_check):
    """保留旧调用合同；核心/网格现已通过分桶规则插件执行。"""
    from .etf_grid_rules_v1 import ETFGridStrategy
    if not callable(active_check):raise PermissionError('ETF_ACTIVE_RECEIPT_REQUIRED')
    strategy=ETFGridStrategy(benchmark)
    budgets={'hold':9000} if benchmark else {'core':5000,'grid':4000}
    result=ETFBucketBackend(budgets).run(strategy,frame,actions,active_check)
    result['contract']=CONTRACT
    return result


class ETFBucketBackend:
    """多资金桶的公共账户循环；不含网格价位、指标或买卖规则。"""
    def __init__(self,budgets=None):
        self.budgets=dict(budgets or {'core':5000,'grid':4000})
        if not self.budgets or any(not isinstance(k,str) or not k or not math.isfinite(v) or v<=0 for k,v in self.budgets.items()) or sum(self.budgets.values())!=9000:
            raise ValueError('BUCKET_BUDGETS_MUST_TOTAL_9000')

    def check(self,req):
        if (req.asset,req.frequency,req.price_view,req.execution)!=('ETF','1D','RAW','NEXT_SESSION_OPEN'):
            raise ValueError('BUCKET_BACKEND_CAPABILITY_UNSUPPORTED')
        if set(req.intents)!={'QUANTITY_ORDER'} or not set(req.capabilities)<= {'SEGREGATED_BUCKETS','SELL_LOT','CASH_DIVIDEND'}:
            raise ValueError('BUCKET_BACKEND_INTENT_UNSUPPORTED')
        if not set(req.fields)<={'date','open','high','low','close','volume_shares','amount_cny'}:
            raise ValueError('BUCKET_BACKEND_FIELD_UNSUPPORTED')

    def validate_strategy(self,strategy):
        if hasattr(strategy,'account_budgets') and strategy.account_budgets!=self.budgets:
            raise ValueError('STRATEGY_BUCKET_ALLOCATION_CONFLICT')

    def describe(self):
        base=ETFDailyBackend().describe()
        return {**base,'backend':'ETF_SEGREGATED_DAILY_ACCOUNT_V1','budgets':dict(self.budgets),
                'capabilities':['QUANTITY_ORDER','SEGREGATED_BUCKETS','SELL_LOT','CASH_DIVIDEND'],
                'limits':{'symbol':SPEC.symbol,'reserve_cash':1000,'one_order_per_bucket_per_close':True}}

    def run(self,strategy,frame,actions,guard):
        from copy import deepcopy
        from .strategy_interface_v1 import Context,BucketDecision,Decision,validate_decision
        self.check(strategy.requirements);guard()
        if frame.date.duplicated().any():raise ValueError('DUPLICATE_SESSION')
        if not {'date','open','high','low','close','volume_shares','amount_cny'}<=set(frame):raise ValueError('BUCKET_INPUT_FIELDS_MISSING')
        data=frame.sort_values('date').reset_index(drop=True).copy(deep=True)
        if (data.date>SPEC.train_end).any():raise ValueError('INPUT_OUTSIDE_BACKEND_WINDOW')
        if len(data[data.date<SPEC.train_start])<strategy.requirements.warmup_sessions:raise ValueError('STRATEGY_WARMUP_INSUFFICIENT')
        calendar,buckets=account_components(data,actions,self.budgets)
        history=[];decisions=[];st={};order_keys={}
        for i,row in data.iterrows():
            day=int(row.date);guard()
            ts=pd.Timestamp(str(day)+' 09:30',tz='Asia/Shanghai');close_ts=ts.normalize()+pd.Timedelta(hours=15,minutes=30)
            for ledger,manager,broker in buckets.values():
                ledger.on_open(ts)
                if day>=SPEC.train_start:broker.process_orders(EventKind.SESSION_OPEN,ts)
                for lot in ledger.lots.values():
                    nxt=calendar.next_day(int(lot.buy_time.strftime('%Y%m%d')))
                    if nxt is not None:lot.sellable_from=pd.Timestamp(str(nxt)+' 09:30',tz='Asia/Shanghai')
                ledger.mark_to_market(SPEC.symbol,float(row.close),close_ts)
                broker.process_orders(EventKind.SESSION_CLOSE,close_ts)
                ledger.on_close(close_ts);ledger.snapshot(close_ts)
                if ledger.check_invariants():raise ValueError('ETF_LEDGER_INVARIANT')
            if day>=SPEC.train_start:
                history.append({'date':day,'cash':1000+sum(x[0].cash for x in buckets.values()),
                    'equity':1000+sum(x[0].current_equity() for x in buckets.values()),'reserved_cash':1000,
                    'buckets':{k:{'cash':v[0].cash,'receivable':v[0].cash_receivable,
                    'quantity':v[0].total_quantity(SPEC.symbol)} for k,v in buckets.items()}})
            nxt=calendar.next_day(day)
            if nxt is None or not SPEC.train_start<=nxt<=SPEC.train_end:continue
            view={'buckets':{k:{'initial_cash':v[0].initial_cash,'cash':v[0].cash,
                    'lots':{lid:asdict(lot) for lid,lot in v[0].lots.items()},'fills':v[0].executed_fills}
                    for k,v in buckets.items()},'order_keys':order_keys}
            found=strategy.on_close(Context(data.iloc[:i+1].copy(deep=True),tuple(map(int,data.date)),i,deepcopy(view),deepcopy(st)))
            if not isinstance(found,BucketDecision):raise ValueError('BUCKET_DECISION_REQUIRED')
            guard();seen=set();seen_keys=set()
            # 全部验证后再创建任一订单，避免坏批次半应用。
            for command in found.orders:
                validate_decision(Decision('BUCKET_ORDER',command.order),strategy.requirements)
                if command.bucket not in buckets or command.bucket in seen or command.key in order_keys or command.key in seen_keys or not isinstance(command.key,str) or not command.key:
                    raise ValueError('BUCKET_OR_ORDER_KEY_CONFLICT')
                seen.add(command.bucket);seen_keys.add(command.key)
                cap=command.capital_limit
                if cap is not None and (not math.isfinite(cap) or not 0<cap<=self.budgets[command.bucket]):
                    raise ValueError('BUCKET_CAP_CANNOT_EXPAND')
                if command.order.lot_id and command.order.lot_id not in buckets[command.bucket][0].lots:
                    raise ValueError('LOT_NOT_IN_REQUESTED_BUCKET')
                if 'capital_cap' in (command.metadata or {}):raise ValueError('CAP_OVERRIDE_FORBIDDEN')
            st=deepcopy(found.state);decisions.extend(deepcopy(found.records))
            for command in found.orders:
                name=command.bucket;manager=buckets[name][1];item=command.order
                metadata=deepcopy(command.metadata or {})
                if item.side=='BUY':metadata['capital_cap']=command.capital_limit or self.budgets[name]
                order=Order('',name,'ETF_FIXED',f'{name}-{len(manager.orders)}',SPEC.symbol,Side(item.side),item.quantity,close_ts,
                            lot_id=item.lot_id,metadata=metadata)
                manager.create_order(order,close_ts);manager.submit(order,close_ts);order_keys[command.key]=order.order_id
        if not history:raise ValueError('NO_TRAIN_SESSIONS')
        peak=10000.;dd=0.
        for row in history:peak=max(peak,row['equity']);dd=max(dd,1-row['equity']/peak)
        fills=[{**f,'bucket':name} for name,(ledger,_,_) in buckets.items() for f in ledger.executed_fills]
        return {'contract':deepcopy(strategy.parameters),'benchmark':strategy.parameters.get('benchmark_mode',False),
            'completed':True,'daily':history,'decisions':decisions,
            'metrics':{'net_return':history[-1]['equity']/10000-1,'max_drawdown':dd,
                'total_fees':sum(b[0].total_fees for b in buckets.values()),'trade_count':len(fills)},
            'fills':fills,'orders':{k:[asdict(o) for o in b[1].orders.values()] for k,b in buckets.items()},
            'ledgers':{k:b[0].checkpoint() for k,b in buckets.items()}}


def account_components(data,actions,budgets):
    calendar=TradingCalendar(list(map(int,data.date)));clock=TradingClock(calendar)
    store=MarketDataStore();bars=data.set_index('date').copy()
    bars['volume']=bars.volume_shares;bars['amount']=bars.amount_cny
    bars['prev_close']=bars.close.shift(1)
    for action in actions:
        if action['effective_date'] in bars.index:bars.loc[action['effective_date'],'prev_close']-=action['terms']['cash_per_share']
    store.add_daily_raw(SPEC.symbol,bars[['open','high','low','close','volume','amount','prev_close']])
    buckets={}
    for name,cash in budgets.items():
        ledger=CorporateActionAccountingV1(cash,actions,'ETF_510300_CASH_ACTIONS_V1')
        orders=OrderManager()
        broker=BrokerSimulator(store,clock,ledger,orders,fee_model=ETFFeesV1(),
            slippage_model=FixedBpsSlippage(SPEC.slippage_per_side),fill_model=EventOpenFillModel(.1),
            price_limit_model=ETFLimitsV1(),risk_manager=ETFBucketRiskV1(ledger),lot_size=100)
        buckets[name]=(ledger,orders,broker)
    return calendar,buckets


def run_policy_account(frame,actions,*,candidate,input_identity,active_check):
    """旧批次兼容入口；保留旧回执检查，内部走统一规则/账户循环。"""
    from .etf_trend_risk_hypothesis_v1 import contract,ETFPolicyStrategy
    expected=contract(candidate)
    def guard():
        receipt=active_check()
        if receipt.get('contracts',{}).get(candidate)!=expected or receipt.get('input_identity')!=input_identity:
            raise PermissionError('ETF_FAMILY_RECEIPT_MISMATCH')
        if receipt.get('novelty',{}).get(candidate,{}).get('allowed') is not True:
            raise PermissionError('ETF_FAMILY_NOVELTY_REQUIRED')
    return ETFDailyBackend().run(ETFPolicyStrategy(candidate),frame,actions,guard)


class ETFDailyBackend:
    """当前已支持的单ETF原价日线账户；新市场/频率须有独立后端能力。"""
    def check(self,req):
        if (req.asset,req.frequency,req.price_view,req.execution)!=('ETF','1D','RAW','NEXT_SESSION_OPEN'):
            raise ValueError('BACKEND_CAPABILITY_UNSUPPORTED')
        if not set(req.intents)<= {'TARGET_WEIGHT','QUANTITY_ORDER'}:
            raise ValueError('BACKEND_INTENT_UNSUPPORTED')
        if not set(req.fields)<= {'date','open','high','low','close','volume_shares','amount_cny'}:
            raise ValueError('BACKEND_DATA_FIELD_UNSUPPORTED')
        if not set(req.capabilities)<= {'SELL_LOT','CASH_DIVIDEND'}:
            raise ValueError('BACKEND_FEATURE_UNSUPPORTED')

    def describe(self):
        import hashlib
        from pathlib import Path
        root=Path(__file__).resolve().parent
        paths=list((root.parent/'engine').glob('*.py'))+[Path(__file__),root/'strategy_interface_v1.py',
              root/'etf_grid_spec_v1.py',root/'etf_grid_rules_v1.py']
        return {'backend':'ETF_RAW_DAILY_ACCOUNT_V1','account_contract':CONTRACT,
                'capabilities':['TARGET_WEIGHT','QUANTITY_ORDER','SELL_LOT','NEXT_SESSION_OPEN'],
                'limits':{'symbol':SPEC.symbol,'cash_reserve':1000,'bucket':9000,'max_weight':.9,
                          'quantity_lot':100,'max_orders_per_close':1},
                'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}

    def run(self,strategy,frame,actions,guard):
        from copy import deepcopy
        from .strategy_interface_v1 import Context,TargetWeight,QuantityOrder,validate_decision
        self.check(strategy.requirements);guard()
        needed={'date','open','high','low','close','volume_shares','amount_cny'}|set(strategy.requirements.fields)
        if not needed<=set(frame.columns):raise ValueError('REQUIRED_INPUT_FIELDS_MISSING')
        if frame.date.duplicated().any():raise ValueError('DUPLICATE_SESSION')
        data=frame.sort_values('date').reset_index(drop=True).copy(deep=True)
        if (data.date>SPEC.train_end).any():raise ValueError('INPUT_OUTSIDE_BACKEND_WINDOW')
        if len(data[data.date<SPEC.train_start])<strategy.requirements.warmup_sessions:
            raise ValueError('STRATEGY_WARMUP_INSUFFICIENT')
        calendar,buckets=account_components(data,actions,{'policy':9000})
        ledger,orders,broker=buckets['policy'];days=tuple(map(int,data.date))
        history=[];decisions=[];state={}
        for i,row in data.iterrows():
            day=int(row.date)
            if day>SPEC.train_end:break
            guard();ts=pd.Timestamp(str(day)+' 09:30',tz='Asia/Shanghai')
            close_ts=ts.normalize()+pd.Timedelta(hours=15,minutes=30)
            ledger.on_open(ts)
            if day>=SPEC.train_start:broker.process_orders(EventKind.SESSION_OPEN,ts)
            qty=ledger.total_quantity(SPEC.symbol)
            for lot in ledger.lots.values():
                nxt=calendar.next_day(int(lot.buy_time.strftime('%Y%m%d')))
                if nxt is not None:lot.sellable_from=pd.Timestamp(str(nxt)+' 09:30',tz='Asia/Shanghai')
            ledger.mark_to_market(SPEC.symbol,float(row.close),close_ts)
            broker.process_orders(EventKind.SESSION_CLOSE,close_ts)
            ledger.on_close(close_ts);ledger.snapshot(close_ts)
            if ledger.check_invariants():raise ValueError('ETF_LEDGER_INVARIANT')
            equity=1000+ledger.current_equity()
            account={'equity':equity,'cash':1000+ledger.cash,'quantity':qty,
                     'weight':qty*float(row.close)/equity,'receivable':ledger.cash_receivable}
            if day>=SPEC.train_start:history.append({'date':day,**account})
            nxt=calendar.next_day(day)
            if nxt is None or not SPEC.train_start<=nxt<=SPEC.train_end:continue
            last=ledger.executed_fills[-1] if ledger.executed_fills else None
            view={**account,'lots':{k:asdict(v) for k,v in ledger.lots.items()},
                  'fills':ledger.executed_fills,'last_fill_metadata':orders.orders[last['order_id']].metadata if last else {}}
            context=Context(data.iloc[:i+1].copy(deep=True),days,i,deepcopy(view),deepcopy(state))
            chosen=strategy.on_close(context);validate_decision(chosen,strategy.requirements)
            guard()
            state=deepcopy(chosen.state if chosen.state is not None else state)
            intent=chosen.intent
            target=intent.weight if isinstance(intent,TargetWeight) else None
            decisions.append({'date':day,'target_weight':target,'reason':chosen.reason})
            if intent is None:continue
            price=round(float(row.close)*(1+SPEC.slippage_per_side),4)
            lot_id=None
            if isinstance(intent,TargetWeight):
                if intent.weight>.9:raise ValueError('BACKEND_WEIGHT_CAP_EXCEEDED')
                target_qty=int(min(9000,equity*target)/price/100)*100 if target else 0
                if qty and not intent.increase_existing:target_qty=min(qty,target_qty)
                if target_qty==qty:continue
                amount=abs(target_qty-qty);side=Side.BUY if target_qty>qty else Side.SELL
                if side==Side.BUY:
                    cap=min(ledger.cash,9000,equity*target-qty*price)
                    while amount and amount*price+ETFFeesV1().calc('BUY',amount,price).total_fee>cap:amount-=100
                    if not amount:decisions[-1]['execution_reason']='FEE_INCLUSIVE_LOT_UNAFFORDABLE';continue
            elif isinstance(intent,QuantityOrder):
                side=Side(intent.side);amount=intent.quantity;lot_id=intent.lot_id
                decisions[-1]['quantity_order']=asdict(intent)
                if side==Side.BUY and (qty+amount)*price>equity*.9:
                    decisions[-1]['execution_reason']='WEIGHT_CAP_REJECT';continue
                if lot_id is not None and lot_id not in ledger.lots:
                    decisions[-1]['execution_reason']='LOT_NOT_FOUND';continue
            if side==Side.BUY and (row.close>SPEC.max_price or row.amount_cny<SPEC.min_daily_amount):
                decisions[-1]['execution_reason']='SCREEN_REJECT';continue
            # capital_cap不可由规则插件覆盖；引擎继续做现金、费用、T+1/FIFO/容量核验。
            metadata={**deepcopy(chosen.metadata or {}),'capital_cap':9000,'decision_reason':chosen.reason}
            order=Order('', 'policy','ETF_FAMILY',str(len(decisions)),SPEC.symbol,side,amount,close_ts,
                        lot_id=lot_id,metadata=metadata)
            orders.create_order(order,close_ts);orders.submit(order,close_ts)
        if not history:raise ValueError('NO_TRAIN_SESSIONS')
        peak=10000.;drawdown=0.
        for row in history:peak=max(peak,row['equity']);drawdown=max(drawdown,1-row['equity']/peak)
        return {'contract':deepcopy(strategy.parameters),'completed':True,'daily':history,'decisions':decisions,
            'fills':ledger.executed_fills,'orders':[asdict(o) for o in orders.orders.values()],
            'order_events':orders.event_log.to_records(),'ledger':ledger.checkpoint(),
            'metrics':{'net_return':history[-1]['equity']/10000-1,'max_drawdown':drawdown,
                       'total_fees':ledger.total_fees,'trade_count':len(ledger.executed_fills)}}
