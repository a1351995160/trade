"""新机制的纯决策原型，只供合成验证；尚未接真实执行许可。"""
import math
from copy import deepcopy

FAMILY=('ETF_MONTHLY_TREND_RISK_EXIT_V1','ETF_SHOCK_REPAIR_EXIT_V1',
        'ETF_COMPRESSION_BREAKOUT_EXIT_V1','ETF_MONTH_TURN_EXIT_V1')
BENCHMARK='ETF_SHARED_RISK_BENCHMARK_V1'

SPEC={
    'candidate_id':'ETF_MONTHLY_TREND_RISK_EXIT_V1',
    'symbol':'510300.SH','momentum_sessions':63,'volatility_sessions':20,
    'volatility_ddof':1,'annualization_sessions':252,'atr_sessions':14,
    'target_annual_volatility':.08,'max_weight':.90,'trailing_atr_multiple':2,
    'rebalance':'CALENDAR_MONTH_END_NEXT_SESSION_OPEN',
    'minimum_resize_notional':2000,'entry_exit_exempt_resize_threshold':True,
    'permanent_core':False,'averaging_down':False,
    'hypothesis_status':'FROZEN_MECHANISM_SYNTHETIC_ONLY',
    'novelty_status':'NOT_YET_ADJUDICATED_RELATED_TO_PRIOR_TREND_FAMILY',
}


def decision(*, momentum, annual_volatility, close, peak_close, atr,
             held_weight, equity, month_end, benchmark=False):
    """None表示不发调仓指令；0才是退出，不把缺失值当空仓信号。"""
    if not 0<=held_weight<=1 or not math.isfinite(equity) or equity<=0:
        raise ValueError('ACCOUNT_STATE_INVALID')
    if not benchmark and held_weight>0 and all(math.isfinite(x) and x>0 for x in (close,peak_close,atr)):
        if close<=peak_close-SPEC['trailing_atr_multiple']*atr:
            return {'target_weight':0.,'reason':'EXIT_TRAILING_CLOSE'}
    if not month_end:return {'target_weight':None,'reason':'HOLD_UNTIL_MONTH_END'}
    if not benchmark:
        if not math.isfinite(momentum):return {'target_weight':None,'reason':'MOMENTUM_UNKNOWN'}
        if momentum<=0:return {'target_weight':0.,'reason':'EXIT_OR_STAY_CASH_NEGATIVE_TREND'}
    if not math.isfinite(annual_volatility) or annual_volatility<=0:
        return {'target_weight':None,'reason':'VOLATILITY_UNKNOWN_OR_ZERO'}
    target=min(SPEC['max_weight'],SPEC['target_annual_volatility']/annual_volatility)
    if held_weight>0 and target>held_weight:
        return {'target_weight':None,'reason':'NO_ADD_TO_EXISTING_POSITION'}
    if held_weight>0 and abs(target-held_weight)*equity<SPEC['minimum_resize_notional']:
        return {'target_weight':None,'reason':'SMALL_RESIZE_COST_FILTER'}
    return {'target_weight':target,'reason':'MONTH_END_RISK_TARGET'}


def contract(candidate):
    if candidate not in (*FAMILY,BENCHMARK):raise ValueError('UNREGISTERED_ETF_POLICY')
    from .etf_grid_account_v1 import CONTRACT as base
    common={k:deepcopy(base[k]) for k in ('symbol','train_start','train_end','initial_cash','cash_reserve',
        'slippage_per_side','minimum_commission','commission_rate','other_fee_rate','stamp_tax_rate',
        'other_fee_evidence','indicator_prices','fill_prices','dividends','result_type')}
    return {**common,'version':'ETF_FAMILY_ACCOUNT_V1','candidate_id':candidate,
            'risk_target':.08,'weight_cap':.9,'volatility_sessions':20,'atr_sessions':14,
            'family':list(FAMILY),'portfolio':'SINGLE_ETF_RISK_BUCKET','benchmark':BENCHMARK,
            'rule_definition':{
                FAMILY[0]:'MONTHLY_MOM63_WITH_DAILY_2ATR_TRAIL',
                FAMILY[1]:'RET2_LT_MINUS_2_SIGMA20_SQRT2_AND_C_GT_MA60_EXIT_MA5_OR_2ENTRYATR_OR_5SESSIONS',
                FAMILY[2]:'SIGMA20_LT_PREV60_MEDIAN_AND_C_GT_PREV20_HIGH_EXIT_PREV10_LOW_OR_2ATR_TRAIL_OR_20SESSIONS',
                FAMILY[3]:'THIRD_LAST_CLOSE_ENTER_NEXT_OPEN_EXIT_NEXT_MONTH_THIRD_CLOSE_OR_2ENTRYATR',
                BENCHMARK:'MONTHLY_RISK_CAP_WITHOUT_TREND_OR_STOP',
            }[candidate],'cooldown_complete_sessions':1 if candidate in FAMILY[1:3] else 0,
            'truth_boundary':'REQUIRES_FROZEN_CONTRACT_INPUT_AND_GOVERNANCE'}


def features(frame):
    from .etf_grid_account_v1 import indicators
    out=indicators(frame)
    ret=out.close.pct_change(fill_method=None)
    out['sigma']=ret.rolling(20,min_periods=20).std(ddof=1)
    out['sigma_median']=out.sigma.shift(1).rolling(60,min_periods=60).median()
    out['momentum']=out.close/out.close.shift(63)-1
    out['ret2']=out.close/out.close.shift(2)-1
    out['ma5']=out.close.rolling(5,min_periods=5).mean()
    out['prior_high20']=out.high.shift(1).rolling(20,min_periods=20).max()
    out['prior_low10']=out.low.shift(1).rolling(10,min_periods=10).min()
    return out


def calendar_fields(days,index):
    # 月末定位要求日历中已包含后一个月，末端不猜交易日。
    month=days[index]//100
    positions=[i for i,d in enumerate(days) if d//100==month]
    complete=positions[-1]+1<len(days)
    return {'month_end':complete and index==positions[-1],
            'third_last':complete and index==positions[-1]-2,
            'month_ordinal':positions.index(index)+1,'month':month}


def batch_decision(candidate,row,calendar,holding,*,held_weight,equity):
    contract(candidate)
    held=holding is not None
    def answer(target,reason):return {'target_weight':target,'reason':reason}
    if candidate in (FAMILY[0],BENCHMARK):
        return decision(momentum=row['momentum'],annual_volatility=row['sigma']*math.sqrt(252),
            close=row['close'],peak_close=holding['peak'] if held else row['close'],atr=row['atr'],
            held_weight=held_weight,equity=equity,month_end=calendar['month_end'],benchmark=candidate==BENCHMARK)
    if held:
        if candidate in (FAMILY[1],FAMILY[3]) and row['close']<=holding['entry_price']-2*holding['entry_atr']:
            return answer(0.,'EXIT_ENTRY_ATR_STOP')
        if candidate==FAMILY[1] and (row['close']>=row['ma5'] or holding['sessions']>=5):
            return answer(0.,'EXIT_REPAIR_OR_TIME')
        if candidate==FAMILY[2] and (row['close']<row['prior_low10'] or
                row['close']<=holding['peak']-2*row['atr'] or holding['sessions']>=20):
            return answer(0.,'EXIT_BREAKOUT_FAILURE_OR_TIME')
        if candidate==FAMILY[3] and calendar['month']!=holding['entry_month'] and calendar['month_ordinal']>=3:
            return answer(0.,'EXIT_MONTH_TURN')
        return answer(None,'HOLD_NO_ADD')
    required={'close','sigma','atr'}
    if candidate==FAMILY[1]:required|={'ret2','ma60'}
    if candidate==FAMILY[2]:required|={'sigma_median','prior_high20'}
    if any(not math.isfinite(row[k]) for k in required) or row['sigma']<=0 or row['atr']<=0:
        return answer(None,'INPUT_UNKNOWN')
    triggered=(row['close']>row['ma60'] and row['ret2']<-2*row['sigma']*math.sqrt(2)) if candidate==FAMILY[1] else (
        row['sigma']<row['sigma_median'] and row['close']>row['prior_high20']) if candidate==FAMILY[2] else calendar['third_last']
    return answer(min(.9,.08/(row['sigma']*math.sqrt(252))) if triggered else None,
                  'ENTRY_EVENT' if triggered else 'NO_ENTRY_EVENT')


class ETFPolicyStrategy:
    """旧四候选/基准的规则插件；持仓状态来自实际成交快照，不写账户。"""
    source_files=()

    def __init__(self,candidate):
        from .strategy_interface_v1 import Requirements
        from pathlib import Path
        self.strategy_id=candidate;self.parameters=contract(candidate)
        self.requirements=Requirements('ETF','1D','RAW',
            ('date','open','high','low','close','volume_shares','amount_cny'),120,('TARGET_WEIGHT',))
        self.source_files=(str(Path(__file__).with_name('etf_grid_account_v1.py')),
                           str(Path(__file__).with_name('etf_grid_spec_v1.py')))

    def on_close(self,context):
        from .strategy_interface_v1 import Decision,TargetWeight
        state=deepcopy(context.state);account=context.account;i=context.index
        row=features(context.history).iloc[-1];day=int(row.date);qty=account['quantity']
        held=state.get('holding');before=state.get('quantity',0)
        if not before and qty:
            fill=account['fills'][-1]
            # 只从实际买单携带的信号ATR恢复入场条件。
            held={'entry_price':fill['price'],'entry_atr':account['last_fill_metadata']['signal_atr'],
                  'peak':float(row.close),'entry_index':i,'entry_month':day//100,'sessions':1}
        if before and not qty:held=None;state['last_exit']=i;state['exit_pending']=False
        if held:held['peak']=max(held['peak'],float(row.close));held['sessions']=i-held['entry_index']+1
        last_exit=state.get('last_exit')
        if not qty and self.strategy_id in FAMILY[1:3] and last_exit is not None and i<=last_exit+1:
            chosen={'target_weight':None,'reason':'POST_EXIT_COOLDOWN'}
        elif state.get('exit_pending') and qty:chosen={'target_weight':0.,'reason':'RETRY_UNCOMPLETED_EXIT'}
        else:chosen=batch_decision(self.strategy_id,row,calendar_fields(context.calendar,i),held,
                                  held_weight=account['weight'],equity=account['equity'])
        target=chosen['target_weight']
        if target==0:state['exit_pending']=bool(qty)
        state.update(holding=held,quantity=qty)
        return Decision(chosen['reason'],None if target is None else TargetWeight(target,False),state,
                        {'signal_atr':float(row.atr)})

    def validate(self):
        if self.parameters!=contract(self.strategy_id):raise ValueError('FIXED_POLICY_PARAMETERS_CHANGED')
