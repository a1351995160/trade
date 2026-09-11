"""双价格输入的版本化账户入口；新合同不能使用旧降级回执。"""
from .baostock_price_views_v1 import VERSION
from .common import stable_hash
from .degraded_execution_v2 import CONTRACT as OLD_CONTRACT, _run_account

CONTRACT = {**OLD_CONTRACT, 'adapter_version': 'BAOSTOCK_ACCOUNT_V1',
    'version': 'BAOSTOCK_ACCOUNT_V1', 'factor_id': VERSION,
    'purpose': 'FIXED_HFQ_SIGNAL_EXPLORATION_WITH_HISTORICAL_EXPOSURE',
    'volume_semantics': 'BAOSTOCK_API_SHARES_REQUIRES_VERSIONED_INPUT_VERIFICATION',
    'volume_candidates': ['SHARES'],
    'capacity': 'LEGACY_CONSERVATIVE_INTERSECTION_EQUALS_FLOOR_RAW_SHARES_OVER_10',
    'labels': ['NOT_FOR_QUALIFICATION', 'MODELED_AVAILABILITY', 'HAZARD_CONDITIONING_BIAS'],
    'signal_version': VERSION, 'signal_formula': 'HFQ_CLOSE_T / HFQ_CLOSE_T_MINUS_5 - 1',
    'signal_return_semantics': 'PROVIDER_PRICE_CHANGE_ADJUSTMENT_NOT_DISTRIBUTION_TOTAL_RETURN',
    'execution_price_mode': 'RAW', 'execution_source': 'BAOSTOCK_SAME_VERSION_AS_HFQ',
    'result_type': 'BAOSTOCK_HFQ_SIGNAL_RAW_ACCOUNT_EXPLORATORY',
    'historical_input_pit': 'UNKNOWN_MODELED_NEXT_OPEN', 'NOT_FOR_QUALIFICATION': True}


def run_baostock_account(bundle, source_identity, active_check=None):
    if bundle.contract_identity != stable_hash(CONTRACT):
        raise PermissionError('BAOSTOCK_CONTRACT_IDENTITY_REQUIRED')
    if ('signal_version' not in bundle.ready_factors or
            not bundle.ready_factors.signal_version.eq(VERSION).all()):
        raise ValueError('BAOSTOCK_SIGNAL_VERSION_REQUIRED')
    if 'adjustflag' not in bundle.daily or not bundle.daily.adjustflag.astype(str).eq('3').all():
        raise ValueError('BAOSTOCK_RAW_EXECUTION_REQUIRED')
    return _run_account(bundle, source_identity, active_check, CONTRACT)
