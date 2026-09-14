"""用户ETF网格的无收益前置核验；不产生信号、账户或预算回执。"""
from .etf_grid_spec_v1 import SPEC
from .etf_grid_rules_v1 import grid_spacing, state, adjacent_grid
import numpy as np
import pandas as pd


def decode_day_volume(raw, reserved):
    """仅识别公开实现的溢出标记；其他reserved值不改变数量。"""
    if not isinstance(raw, int) or not isinstance(reserved, int) or not 0 <= raw <= 0xffffffff or not 0 <= reserved <= 0xffffffff:
        raise ValueError('INVALID_UINT32_VOLUME_RECORD')
    if reserved & 0xffffff00 != 0xc3640000:
        return raw
    remainder=reserved & 0xff
    decoded=raw*100+remainder
    if remainder>=100 or decoded<=0xffffffff:
        raise ValueError('INVALID_OVERFLOW_VOLUME_ENCODING')
    return decoded


def check_model_input(frame, expected_sessions, *, spread_model_approved=False):
    """用户批准的模型价差替代；只证明本模块检查，不授予账户执行许可。"""
    result=check_input(frame,expected_sessions)
    if spread_model_approved is not True:
        raise PermissionError('EXPLICIT_SPREAD_MODEL_APPROVAL_REQUIRED')
    result['reasons'].remove('HISTORICAL_BID_ASK_REQUIRED')
    result['status']='MODEL_BASIC_INPUT_CHECKS_PASSED' if not result['reasons'] else 'NOT_READY'
    result['execution_authorized']=False
    result['slippage_per_side']=SPEC.slippage_per_side
    result['historical_spread']='UNKNOWN_USER_APPROVED_MODEL_SUBSTITUTION'
    return result


def normalize_etf_diagnostic(rows):
    """原read_day_window按/100输出；ETF独立/1000假设，禁止改旧股票reader。"""
    frame=pd.DataFrame(rows).copy(deep=True)
    for field in ('open','high','low','close'):frame[field]=frame[field]/10
    frame['amount_cny']=frame.amount_encoded
    frame['volume_shares']=frame.volume_encoded
    return frame


def check_input(frame,expected_sessions,historical_spread=None):
    reasons=[]
    if frame.date.duplicated().any():raise ValueError('DUPLICATE_DAY')
    if set(frame.date)!=set(expected_sessions):reasons.append('CALENDAR_COVERAGE_MISMATCH')
    prices=frame[['open','high','low','close']]
    valid=np.isfinite(prices).all(axis=1)&prices.gt(0).all(axis=1)
    valid &= frame.high.ge(prices.max(axis=1))&frame.low.le(prices.min(axis=1))
    if not valid.all():reasons.append('INVALID_OHLC')
    v=frame.amount_cny/frame.volume_shares.where(frame.volume_shares.gt(0))
    ratio=v/((frame.low+frame.high)/2)
    good=ratio.between(.5,2)
    if not good.all():reasons.append('DERIVED_UNIT_CONSISTENCY_NOT_ALL_PASS')
    train=frame[frame.date.between(SPEC.train_start,SPEC.train_end)]
    if historical_spread is None:
        spread_status='UNKNOWN';reasons.append('HISTORICAL_BID_ASK_REQUIRED')
    else:
        raise NotImplementedError('HISTORICAL_QUOTE_IDENTITY_AND_TIMING_ADAPTER_REQUIRED')
    return {'status':'NOT_READY' if reasons else 'READY','reasons':reasons,'no_outcome':True,
        'rows':len(frame),'train_rows':len(train),'price_filter_pass_dates':int(train.close.le(SPEC.max_price).sum()),
        'amount_filter_pass_dates':int(train.amount_cny.ge(SPEC.min_daily_amount).sum()),
        'unit_evidence':'DERIVED_INTERNAL_CONSISTENCY_AND_PUBLIC_IMPLEMENTATION_NOT_VENDOR_ATTESTED',
        'unit_ratio_median':float(ratio.median()),'unit_ratio_within_half_to_two':float(good.mean()),
        'historical_spread':spread_status,'account_started':False,'price_exposures':0}
