"""仅供合成验证的新信号提案；不注册因子、不接入真实runner。"""
import math


def five_session_return(sessions, closes, adjustments, *, coverage_complete):
    """六个独立session，五项已核验的每旧股现金/股份调整。"""
    if not coverage_complete:
        return {'value': None, 'reason': 'ACTION_COVERAGE_UNPROVEN'}
    if len(sessions) != 6 or len(set(sessions)) != 6 or sessions != sorted(sessions):
        return {'value': None, 'reason': 'SIX_ORDERED_SESSIONS_REQUIRED'}
    if len(closes) != 6 or len(adjustments) != 5:
        return {'value': None, 'reason': 'MISSING_DEPENDENCY'}
    if any(not math.isfinite(p) or p <= 0 for p in closes):
        return {'value': None, 'reason': 'INVALID_PRICE'}
    wealth = 1.0
    for i, action in enumerate(adjustments, 1):
        if action is None:
            return {'value': None, 'reason': 'ACTION_COVERAGE_UNPROVEN'}
        if action['session'] != sessions[i]:
            return {'value': None, 'reason': 'ACTION_SESSION_CONFLICT'}
        if action['kind'] not in {'VERIFIED_NONE', 'CASH_AND_BONUS'}:
            return {'value': None, 'reason': 'UNSUPPORTED_ACTION'}
        cash, shares = action['cash_per_old_share'], action['new_shares_per_old_share']
        if not all(math.isfinite(v) and v >= 0 for v in (cash, shares)):
            return {'value': None, 'reason': 'INVALID_ACTION_TERMS'}
        if action['kind'] == 'VERIFIED_NONE' and (cash != 0 or shares != 0):
            return {'value': None, 'reason': 'NONE_WITH_NONZERO_TERMS'}
        if not action['terms_known_by_decision']:
            return {'value': None, 'reason': 'TERMS_NOT_KNOWN_BY_DECISION'}
        wealth *= ((1 + shares) * closes[i] + cash) / closes[i - 1]
    return {'value': wealth - 1, 'reason': 'DEFINED_GROSS_SIGNAL_NOT_ACCOUNT_RETURN'}


def universe_preflight(historical_members, rows):
    """缺源保留为阻断，不能将当前可读取集合当历史成员。"""
    members = set(historical_members)
    if len(members) != len(historical_members):
        return {'ready': False, 'reason': 'DUPLICATE_MEMBER'}
    unknown, missing = [], []
    for symbol in sorted(members):
        row = rows.get(symbol)
        if row is None or row['state'] == 'UNKNOWN':
            unknown.append(symbol)
        elif row['state'] == 'ELIGIBLE' and not row['signal_inputs_complete']:
            missing.append(symbol)
        elif row['state'] not in {'ELIGIBLE', 'KNOWN_INELIGIBLE'}:
            unknown.append(symbol)
    return {'ready': not unknown and not missing, 'historical_members': sorted(members),
            'unknown_state': unknown, 'eligible_missing_inputs': missing}
