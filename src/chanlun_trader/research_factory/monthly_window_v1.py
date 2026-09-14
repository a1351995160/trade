"""一次获准月末候选外窗；旧TRAIN合同、门槛及默认封存不变。"""
VERSION='MONTHLY_FIXED_INDEPENDENT_WINDOW_V1'
START, END, WARMUP_START=20250801,20260731,20250703


def contract():
    from .train_search_batch_v1 import contract as original
    from .common import stable_hash
    frozen=original('MONTHLY_REVERSAL_HOLD_20')
    return {**frozen,'version':VERSION,'adapter_version':VERSION,
        'source_candidate_contract_hash':stable_hash(frozen),
        'signal_window':[START,END],'input_window':[WARMUP_START,END],
        'thresholds':{'closed_paths':30,'entry_dates':10,'symbols':2},
        'hazards':'ALL_PROVIDER_ADJUSTMENT_DATES_SIGNAL_THROUGH_EARLIEST_EXIT_INCLUSIVE',
        'purpose':'FIXED_CANDIDATE_INDEPENDENT_WINDOW_VALIDATION',
        'result_type':'INDEPENDENT_WINDOW_ACCOUNT_EXPLORATORY_NOT_QUALIFIED'}


def validate(value):
    if value!=contract():
        raise PermissionError('MONTHLY_WINDOW_FIXED_CONTRACT_CHANGED')


def signal_window(value):
    from .residual_window_v1 import VERSION as RESIDUAL, CONFIRM_VERSIONS, TURNOVER_VERSIONS, SCALE_VERSIONS, TREND_RISK_VERSIONS, validate as validate_residual
    if value.get('version')==RESIDUAL or value.get('version') in CONFIRM_VERSIONS.values() or value.get('version') in TURNOVER_VERSIONS.values() or value.get('version') in SCALE_VERSIONS.values() or value.get('version') in TREND_RISK_VERSIONS.values():
        validate_residual(value)
        return tuple(value['signal_window'])
    from .weekly_window_v1 import VERSION as WEEKLY, validate as validate_weekly
    if value.get('version')==WEEKLY:
        validate_weekly(value)
        return tuple(value['signal_window'])
    if value.get('version')!=VERSION:
        if 'signal_window' in value or 'input_window' in value:
            raise PermissionError('UNRECOGNIZED_WINDOW_OVERRIDE')
        return 20220801,20240731
    validate(value)
    return START,END


def feasibility(paths,value):
    validate(value)
    from .degraded_train_v1 import feasibility_verdict
    old=feasibility_verdict(paths)
    thresholds=value['thresholds']
    passed=all(old['counts'][k]>=v for k,v in thresholds.items())
    return {**old,'thresholds':thresholds,'passed':passed,
        'classification':'EXECUTABLE_EVIDENCE_SUFFICIENT' if passed else 'INSUFFICIENT_EXECUTABLE_EVIDENCE',
        'window_gate_version':VERSION,'original_train_verdict':old}
