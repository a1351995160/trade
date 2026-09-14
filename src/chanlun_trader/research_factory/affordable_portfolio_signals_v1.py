"""信号日整手预算约束是新候选规则；原账户仍独立检查次日真实成交。"""
import numpy as np
import pandas as pd

MOM='AFFORDABLE_LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20'
RISK='AFFORDABLE_WEEKLY_LOW_VOL_MARKET_HOLD_20'
NAMES=(MOM,RISK)
PARENTS={MOM:'LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20',RISK:'WEEKLY_LOW_VOL_FIXED_MARKET_HOLD_20'}
WARMUP={MOM:21,RISK:200}
POLICY={'price':'SIGNAL_SESSION_RAW_CLOSE','reference_cash':10000,'slots':3,'lot_size':100,
        'slippage_fraction':.001,'commission_rate':.00025,'min_commission':5,
        'rule':'close*1.001*100+max(5,close*1.001*100*0.00025)<=10000/3',
        'next_open_affordability':'UNKNOWN_ENGINE_RECHECK_REQUIRED'}
FORMULAS={name:'Parent '+parent+' unchanged; before Top3 require known signal-session RAW-close estimated 100-share cost including original slippage/commission <= initial_cash/3; missing cost uncomputable; no next-open lookahead' for name,parent in PARENTS.items()}


def transform(rows,sessions,name):
    from .technical_train_signals_v1 import transform as parent_transform
    if name not in NAMES:raise ValueError('UNFROZEN_AFFORDABLE_CANDIDATE')
    out=parent_transform(rows,sessions,PARENTS[name])
    close=pd.to_numeric(rows.set_index('timestamp').close.reindex(sessions),errors='raise')
    known=np.isfinite(close)&close.gt(0)
    notional=close*(1+POLICY['slippage_fraction'])*POLICY['lot_size']
    cost=notional+np.maximum(POLICY['min_commission'],notional*POLICY['commission_rate'])
    affordable=cost.le(POLICY['reference_cash']/POLICY['slots'])&known
    out['value']=out.value.where(affordable.to_numpy(),1.).where(known.to_numpy()&out.value.notna())
    out['computable']=out.computable&known.to_numpy()
    out['signal_version']='TRAIN_SEARCH_BATCH_V1_'+name
    return out
