"""唯一固定TRAIN参考策略；使用原broker、订单、费用、退出与新版公司行动账本。"""
from dataclasses import asdict
import math
import os

import pandas as pd

from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import EngineConfig
from chanlun_trader.engine.corporate_action_engine_v1 import CorporateActionBacktestEngineV1
from chanlun_trader.engine.security_state import SecurityMaster,SecurityState,ChinaPriceLimitModel
from chanlun_trader.engine.portfolio_exit import PortfolioExitEvaluatorV1
from chanlun_trader.engine.signal import Signal,Side,ExecutionPolicy
from chanlun_trader.engine.fill import DailyBarFillModel


FIXED_CONTRACT={'factor_id':'RETURN_5D','operator':'LT','threshold':0,'ranking':'FACTOR_ASC_SYMBOL_ASC',
    'top_n':3,'entry':'NEXT_SESSION_OPEN','holding_sessions':3,'exit':'NEXT_SESSION_OPEN',
    'initial_cash':10000,'max_positions':3,'lot_size':100,'commission_rate':0.00025,
    'min_commission':5,'stamp_tax_rate':0.0005,'slippage_fraction':0.001,'max_participation_rate':0.10,
    'result_type':'TRAIN_EXECUTION_BACKTEST_EXPLORATORY','purpose':'FIXED_REFERENCE_REPRODUCTION_NOT_NEW_ALPHA'}


class TrainingAccountExecutionFailure(ValueError):
    def __init__(self,message,evidence):
        super().__init__(message)
        self.evidence=evidence


class ExactStateMasterV1(SecurityMaster):
    def __init__(self,states):
        super().__init__()
        states=states.copy()
        states['trade_date']=states.trade_date.astype(str).str.replace('-','').astype(int)
        self.rows=states.set_index(['symbol','trade_date']).sort_index()

    def row(self,symbol,day,ts):
        row=self.rows.loc[(symbol,day)].to_dict() if (symbol,day) in self.rows.index else None
        availability=row.get('effective_state_available_at') if row is not None else None
        if pd.isna(availability):availability=row.get('available_at') if row is not None else None
        if row is None or not availability or pd.Timestamp(availability)>ts:
            raise ValueError('EXECUTION_STATE_PIT_UNKNOWN')
        if row['st_status'] not in {'NORMAL','ST'} or row['suspension_status'] not in {'TRADING','SUSPENDED'}:
            raise ValueError('EXECUTION_STATE_UNKNOWN')
        if row['eligibility_status']=='CONFLICT':raise ValueError('EXECUTION_STATE_CONFLICT')
        return row

    def as_of(self,symbol,ts):
        day=int(ts.strftime('%Y%m%d'));r=self.row(symbol,day,ts)
        boards={'SZ_MAIN':'MAIN','SH_MAIN':'MAIN','MAIN':'MAIN','CHINEXT':'CHINEXT','STAR':'STAR'}
        if r['board'] not in boards:raise ValueError('EXECUTION_BOARD_UNKNOWN')
        return SecurityState(symbol,day,listed=r['listed'],delisted=r['delisted'],is_st=r['st_status']=='ST',
            board=boards[r['board']],suspended=r['suspension_status']=='SUSPENDED')


class ExactPriceLimitV1(ChinaPriceLimitModel):
    def can_buy_at_open(self,symbol,ts,bar):
        row=self.master.row(symbol,int(ts.strftime('%Y%m%d')),ts)
        if row['suspension_status']=='SUSPENDED':return False,'SUSPENDED'
        if row['st_status']=='ST' or not row['listed'] or row['delisted'] or not row['universe_member'] or row['eligibility_status']!='ELIGIBLE':
            return False,'KNOWN_INELIGIBLE'
        return super().can_buy_at_open(symbol,ts,bar)

    def can_sell_at_open(self,symbol,ts,bar):
        row=self.master.row(symbol,int(ts.strftime('%Y%m%d')),ts)
        if row['delisted']:raise ValueError('TRADE_REALITY_UNSUPPORTED_DELISTING_SETTLEMENT')
        if row['suspension_status']=='SUSPENDED':return False,'SUSPENDED'
        return super().can_sell_at_open(symbol,ts,bar)


def run_fixed_reference(bundle,source_identity,active_check=None):
    synthetic=os.environ.get('CHANLUN_TEST_ISOLATION')=='1' and source_identity[0].startswith('SYNTHETIC')
    if active_check is None:
        if not synthetic:raise PermissionError('GOVERNED_TRAIN_EXECUTION_REQUIRED')
        active_check=lambda:None
    if not synthetic:
        receipt=active_check()
        if receipt['plan']['input_identity']!=bundle.input_identity or list(receipt['plan']['contracts'].values())!=[FIXED_CONTRACT]:
            raise PermissionError('GOVERNED_TRAIN_INPUT_CONTRACT_CONFLICT')
    daily,states,factors=bundle.daily,bundle.states,bundle.factors
    calendar=bundle.calendar
    store=MarketDataStore()
    for symbol,rows in daily.groupby('symbol',sort=True):
        # 日历缺口已由输入闭包证明原因；保留原证券日，不能以填零制造Bar。
        executable=rows.dropna(subset=['open','high','low','close','volume','amount'])
        store.add_daily_raw(symbol,executable.set_index('date')[['open','high','low','close','volume','amount','prev_close']])
    master=ExactStateMasterV1(states)
    cfg=EngineConfig(initial_cash=10000,max_positions=3,max_position_weight=1/3,lot_size=100,
        commission_rate=0.00025,min_commission=5,stamp_tax_rate=0.0005,slippage_bps=0.001,
        feature_price_mode='raw',enable_index_filter=False,index_filter_enabled=False,
        fill_model=DailyBarFillModel(max_participation_rate=0.10),
        data_manifest_hash=bundle.input_identity,universe_version_hash=bundle.pool_identity,
        feature_version_hash=bundle.factor_identity,calendar_version=bundle.calendar_identity,
        persist_run_manifest=False,strategy_hash=bundle.contract_identity)
    engine=CorporateActionBacktestEngineV1(store,calendar,cfg,security_master=master,
        source_identity=source_identity,action_dataset=bundle.actions)
    engine.active_check=active_check
    engine.price_limit_factory=ExactPriceLimitV1
    evaluator=PortfolioExitEvaluatorV1('RETURN_5D_FIXED_REFERENCE','FIXED_REFERENCE',
        {'exit_type':'FIXED_HOLD','fixed_holding_sessions':3})
    factor_days={int(day):rows for day,rows in factors.groupby('timestamp')}
    daily_index=daily.set_index(['symbol','date'])
    ranks=[];decisions=[]

    def signals(view,ts,day):
        active_check()
        if ts.hour!=15 or ts.minute!=30 or not 20220801<=day<=20240731:return []
        rows=factor_days.get(day)
        if rows is None:return []
        candidates=[]
        for row in rows.itertuples():
            if not math.isfinite(row.value) or row.value>=0:continue
            if pd.isna(row.effective_available_at) or pd.Timestamp(row.effective_available_at)>ts:continue
            state=master.row(row.symbol,day,ts)
            if not state['universe_member'] or state['eligibility_status']!='ELIGIBLE' or state['st_status']!='NORMAL' or state['suspension_status']!='TRADING':continue
            if engine.ledger.total_quantity(row.symbol):continue
            observation=daily_index.loc[(row.symbol,day)]
            modeled=pd.Timestamp(observation['modeled_available_at'])
            observed=observation.get('effective_available_at',observation.get('available_at'))
            effective=max(modeled,pd.Timestamp(observed)) if pd.notna(observed) and observed!='UNKNOWN' else modeled
            if effective>ts:continue
            candidates.append((row.value,row.symbol))
        candidates.sort()
        selected=candidates[:3]
        ranks.append({'date':day,'eligible_candidates':len(candidates),'top3':[{'symbol':s,'factor':v,'rank':i+1} for i,(v,s) in enumerate(selected)]})
        return [Signal('FIXED_REFERENCE',f'RETURN5D:{day}:{symbol}',symbol,ts,Side.BUY,
            score=-value,execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN) for value,symbol in selected]

    def exits(view,ts,day,ledger):
        if ts.hour!=15 or ts.minute!=30:return []
        found=evaluator.evaluate(ledger.lots.values(),day,calendar.index(day),{},ts)
        decisions.extend(asdict(d) for d in found)
        return found

    try:result=engine.run(strategy_fn=signals,exit_fn=exits)
    except Exception as exc:
        raise TrainingAccountExecutionFailure(str(exc),{'status':'INCOMPLETE_FAIL_CLOSED',
            'exception_type':type(exc).__name__,'message':str(exc),'rankings':ranks,'exit_decisions':decisions,
            'account_checkpoint':engine.ledger.checkpoint() if engine.ledger is not None else None,
            'orders':[asdict(o) for o in engine.order_manager.orders.values()] if engine.order_manager is not None else [],
            'events':engine.event_log.to_records() if engine.event_log is not None else [],
            'daily_holdings':engine.account_history}) from exc
    snapshots=[s for s in result.ledger.snapshots if s.timestamp.hour==15 and s.timestamp.minute==30 and 20220801<=int(s.timestamp.strftime('%Y%m%d'))<=20240731]
    peak=10000;drawdown=0
    for s in snapshots:
        peak=max(peak,s.equity);drawdown=max(drawdown,1-s.equity/peak)
    sells=[t for t in result.trades if t.side==Side.SELL and t.reality_flag=='OK']
    fills=[]
    for fill in result.ledger.executed_fills:
        day=int(pd.Timestamp(fill['fill_time']).strftime('%Y%m%d'))
        reference=float(daily_index.loc[(fill['symbol'],day),'open'])
        fills.append({**fill,'reference_open':reference,'slippage_cost':
            (fill['price']-reference)*(1 if fill['side']==Side.BUY else -1)*fill['quantity']})
    closed={key:0.0 for key,lot in result.ledger.lots.items() if lot.remaining_quantity==0}
    for fill in fills:
        if fill['side']!=Side.SELL:continue
        for allocation in fill['lot_allocations']:
            if allocation['lot_id'] in closed:closed[allocation['lot_id']]+=allocation['realized_pnl']
    metrics={'net_model_return':snapshots[-1].equity/10000-1 if snapshots else None,'max_drawdown':drawdown,
        'sell_fill_count':len(sells),'positive_sell_fills':sum(t.realized_pnl>0 for t in sells),
        'zero_sell_fills':sum(t.realized_pnl==0 for t in sells),'negative_sell_fills':sum(t.realized_pnl<0 for t in sells),
        'total_fees':result.ledger.total_fees,'corporate_action_income':result.ledger.action_income,
        'closed_lot_count':len(closed),'closed_lot_trading_pnl_excludes_action_income':closed,
        'closed_lot_positive_fraction':sum(v>0 for v in closed.values())/len(closed) if closed else None,
        'closed_lot_zero_fraction':sum(v==0 for v in closed.values())/len(closed) if closed else None,
        'closed_lot_negative_fraction':sum(v<0 for v in closed.values())/len(closed) if closed else None,
        'same_fills_fee_added_back_return':(snapshots[-1].equity+result.ledger.total_fees)/10000-1 if snapshots else None,
        'fee_added_back_is_not_a_zero_fee_counterfactual':True,
        'not_out_of_sample':True,'formal_qualification':False}
    return {'type':FIXED_CONTRACT['result_type'],'contract':FIXED_CONTRACT,'metrics':metrics,'rankings':ranks,
        'signals':[asdict(s) for s in result.signals],'orders':[asdict(o) for o in result.orders.orders.values()],
        'trades':[asdict(t) for t in result.trades],'events':result.event_log.to_records(),'exit_decisions':decisions,
        'fills':fills,'daily_holdings':engine.account_history,
        'daily_account':[asdict(s) for s in snapshots],'final_account_checkpoint':result.ledger.checkpoint(),
        'corporate_actions':result.ledger.action_audit,'input_identity':bundle.input_identity}
