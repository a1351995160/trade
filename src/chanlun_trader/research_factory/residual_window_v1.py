"""固定盈利候选的另一时段检验；保留窗口既有曝光，不授予正式资格。"""
VERSION='RESIDUAL_FIXED_EXTERNAL_WINDOW_V1'
NAME='MARKET_PROXY_Z_REVERSAL_GATE_HOLD_20'
START,END,WARMUP_START=20250801,20260731,20241009
from .response_confirmation_signals_v1 import NAMES as CONFIRM_NAMES
CONFIRM_VERSIONS={name:'RESPONSE_CONFIRMATION_FIXED_WINDOW_V1_'+name for name in CONFIRM_NAMES}
TURNOVER_VERSIONS={'LOW_TURNOVER_WEEKLY_MOMENTUM_HOLD_20':'TURNOVER_FIXED_EXTERNAL_WINDOW_V1'}
SCALE_VERSIONS={'WEEKLY_SMALL_SCALE_TREND_HOLD_20':'SCALE_FIXED_EXTERNAL_WINDOW_V1'}
STOCK_TREND_ONLY='WEEKLY_LOW_VOL_STOCK_TREND_ONLY_HOLD_20'
TREND_RISK_VERSIONS={'WEEKLY_LOW_VOL_TREND60_FIXED_HOLD_20':'TREND_RISK_FIXED_EXTERNAL_WINDOW_V1',STOCK_TREND_ONLY:'STOCK_TREND_ONLY_FIXED_EXTERNAL_WINDOW_V1'}


def contract(name=NAME):
    from .train_search_batch_v1 import contract as original
    from .common import stable_hash
    if name!=NAME and name not in CONFIRM_VERSIONS and name not in TURNOVER_VERSIONS and name not in SCALE_VERSIONS and name not in TREND_RISK_VERSIONS:raise PermissionError('UNAPPROVED_WINDOW_CANDIDATE')
    frozen=original(name)
    version=VERSION if name==NAME else (TURNOVER_VERSIONS[name] if name in TURNOVER_VERSIONS else SCALE_VERSIONS[name] if name in SCALE_VERSIONS else TREND_RISK_VERSIONS[name] if name in TREND_RISK_VERSIONS else CONFIRM_VERSIONS[name])
    return {**frozen,'version':version,'adapter_version':version,
        'source_candidate_contract_hash':stable_hash(frozen),
        'signal_window':[START,END],'input_window':[WARMUP_START,END],
        'thresholds':{'closed_paths':30,'entry_dates':20,'symbols':2},
        'purpose':'FIXED_TREND_RISK_EXTERNAL_WINDOW_CHECK' if name in TREND_RISK_VERSIONS else 'FIXED_SCALE_EXTERNAL_WINDOW_CHECK' if name in SCALE_VERSIONS else 'FIXED_TURNOVER_EXTERNAL_WINDOW_CHECK' if name in TURNOVER_VERSIONS else 'FIXED_RESIDUAL_EXTERNAL_WINDOW_CHECK',
        'result_type':'EXPLORATORY_FIXED_RULE_EXTERNAL_WINDOW_ACCOUNT_NOT_QUALIFIED'}


def validate(value):
    if value!=contract(value.get('factor_id',NAME)):raise PermissionError('RESIDUAL_WINDOW_FIXED_CONTRACT_CHANGED')
