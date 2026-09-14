"""明确批准的周低波动固定外窗；200session起点由实际日历确定。"""
VERSION='WEEKLY_FIXED_EXTERNAL_WINDOW_V1'
NAME='WEEKLY_LOW_VOL_FIXED_MARKET_HOLD_20'
START,END,WARMUP_START=20250801,20260731,20241009


def contract():
    from .train_search_batch_v1 import contract as original
    from .common import stable_hash
    frozen=original(NAME)
    return {**frozen,'version':VERSION,'adapter_version':VERSION,
        'source_candidate_contract_hash':stable_hash(frozen),
        'signal_window':[START,END],'input_window':[WARMUP_START,END],
        'thresholds':{'closed_paths':30,'entry_dates':20,'symbols':2},
        'hazards':'ALL_PROVIDER_ADJUSTMENT_DATES_SIGNAL_THROUGH_EARLIEST_EXIT_INCLUSIVE',
        'purpose':'FIXED_WEEKLY_EXTERNAL_WINDOW_VALIDATION',
        'result_type':'EXPLORATORY_FIXED_RULE_EXTERNAL_WINDOW_ACCOUNT_NOT_QUALIFIED'}


def validate(value):
    if value!=contract():raise PermissionError('WEEKLY_WINDOW_FIXED_CONTRACT_CHANGED')
