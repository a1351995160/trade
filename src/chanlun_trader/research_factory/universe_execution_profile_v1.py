"""登记的长期资源规格；分段只改变计算组织，不改变一次账户用途。"""
from copy import deepcopy
import math

from .common import stable_hash


SEGMENTED_PROFILE = 'LONG_HORIZON_SEGMENTED_V1'
CONTINUOUS_PROFILE = 'ENGINEERING_CONTINUOUS_REFERENCE_V1'
ENGINEERING_PURPOSE = 'ENGINEERING_CONTINUOUS_REFERENCE'
RESEARCH_PURPOSES = ('RESEARCH_ACCOUNT', 'RESEARCH_PREPARATION',
                     'RESEARCH_VERIFICATION', 'RESEARCH_REPORT')
REGISTRY = {
    SEGMENTED_PROFILE: {'worker_seconds': 900, 'memory_mib': 2048, 'threads': 1,
                        'concurrency': 1, 'metering': 'ACTIVE_SEGMENTS_ONLY'},
    CONTINUOUS_PROFILE: {'memory_mib': 2048, 'threads': 1, 'concurrency': 1,
                         'metering': 'ACTIVE_SEGMENTS_ONLY'},
}
REGISTRY_HASH = stable_hash(REGISTRY)
HOST_OVERHEAD_RESERVE_SECONDS = 10


def worker_wall_seconds(dispatch_upper):
    """登记总段额含启动与收尾；worker硬上限不能耗尽整份宿主额度。"""
    if type(dispatch_upper) not in (int,float) or not math.isfinite(dispatch_upper) or dispatch_upper<=0:
        raise ValueError('UNIVERSE_SEGMENT_UPPER_BOUND_INVALID')
    return dispatch_upper-min(HOST_OVERHEAD_RESERVE_SECONDS,dispatch_upper/2)


def execution_profile(profile_id, account_sessions, purpose='RESEARCH_ACCOUNT'):
    """只能选择登记规格；维护者授权另外绑定此返回值的身份。"""
    if profile_id not in REGISTRY:
        raise ValueError('UNIVERSE_EXECUTION_PROFILE_NOT_REGISTERED')
    if type(account_sessions) is not int or not 1 <= account_sessions <= 504:
        raise ValueError('UNIVERSE_EXECUTION_SESSION_BOUND_INVALID')
    if profile_id == CONTINUOUS_PROFILE:
        if purpose != ENGINEERING_PURPOSE:
            raise PermissionError('ENGINEERING_CONTINUOUS_REFERENCE_PURPOSE_REQUIRED')
    elif purpose not in RESEARCH_PURPOSES:
        raise PermissionError('UNIVERSE_EXECUTION_PURPOSE_NOT_REGISTERED')
    total = 14400 if account_sessions <= 252 else 28800
    result = {'schema_version': 'universe-execution-profile-v1', 'profile_id': profile_id,
              'registry_hash': REGISTRY_HASH, 'account_sessions': account_sessions,
              'purpose': purpose, 'total_seconds': total, **deepcopy(REGISTRY[profile_id])}
    if profile_id == CONTINUOUS_PROFILE:
        result['worker_seconds'] = total
    result['profile_hash'] = stable_hash(result)
    return result


def validate_execution_profile(value):
    if not isinstance(value, dict):
        raise ValueError('UNIVERSE_EXECUTION_PROFILE_INVALID')
    expected = execution_profile(value.get('profile_id'), value.get('account_sessions'), value.get('purpose'))
    if value != expected:
        raise PermissionError('UNIVERSE_EXECUTION_PROFILE_IDENTITY_CONFLICT')
    return expected


def segment_allowance(profile, charged_seconds, *, seconds_to_expiry):
    """等待不扣计算额，但授权绝对到期始终限制下一段。"""
    value = validate_execution_profile(profile)
    if type(charged_seconds) not in (int, float) or not math.isfinite(charged_seconds) or charged_seconds < 0:
        raise ValueError('UNIVERSE_SEGMENT_CHARGE_INVALID')
    if type(seconds_to_expiry) not in (int, float) or not math.isfinite(seconds_to_expiry):
        raise ValueError('UNIVERSE_SEGMENT_EXPIRY_INVALID')
    if seconds_to_expiry <= 0:
        raise PermissionError('UNIVERSE_EXECUTION_AUTHORIZATION_EXPIRED')
    remaining = value['total_seconds'] - charged_seconds
    if remaining <= 0:
        raise PermissionError('UNIVERSE_EXECUTION_RESOURCE_EXHAUSTED')
    return min(value['worker_seconds'], remaining, seconds_to_expiry)


def segment_charge(seconds, upper_bound, evidence_identity, *, outcome='CONTINUE'):
    """无可靠耗时回执时扣派发上界，不能把进程崩溃当免费运行。"""
    if type(upper_bound) not in (int, float) or not math.isfinite(upper_bound) or upper_bound <= 0:
        raise ValueError('UNIVERSE_SEGMENT_UPPER_BOUND_INVALID')
    if seconds is None:
        return upper_bound, ('FAILED_UNMEASURED_CHARGED_DISPATCH_UPPER_BOUND' if outcome=='FAILED'
                             else 'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND')
    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0
            or not isinstance(evidence_identity, str) or not evidence_identity):
        raise ValueError('UNIVERSE_SEGMENT_MEASUREMENT_REQUIRED')
    if seconds > upper_bound:
        if outcome=='FAILED':
            return seconds,'MEASURED_FAILED_RESOURCE_BOUND_VIOLATION'
        raise PermissionError('UNIVERSE_SEGMENT_USAGE_EXCEEDS_DISPATCH_BOUND')
    return seconds, 'MEASURED_ACTIVE_WALL_SECONDS'
