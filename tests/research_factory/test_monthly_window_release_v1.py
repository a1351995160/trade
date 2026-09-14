"""限定窗口授权不能改变默认封存或放大候选范围。"""
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from acquire_monthly_independent_v1 import validate_release
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research.guard import ResearchDataAccessGuard, FinalTestAccessViolation


def grant(**changes):
    value={'candidate':'MONTHLY_REVERSAL_HOLD_20','start':'2025-08-01','end':'2026-07-31',
           'warmup_sessions':21,'parent_receipt_id':'parent','data_seconds':10800,
           'account_seconds':5400,'expires_at':'2026-09-14T10:05:03+08:00',**changes}
    value['identity']=stable_hash(value)
    return value


def test_valid_release_does_not_change_default_seal():
    validate_release(grant(),{'receipt_id':'parent'},datetime(2026,9,12,tzinfo=timezone.utc))
    with pytest.raises(FinalTestAccessViolation):
        ResearchDataAccessGuard().check_date(20250801)


@pytest.mark.parametrize('changes',[{'candidate':'OTHER'},{'end':'2026-08-01'},
    {'warmup_sessions':22},{'parent_receipt_id':'other'},{'data_seconds':10801}])
def test_reject_scope_expansion(changes):
    with pytest.raises(PermissionError,match='SCOPE_CHANGED'):
        validate_release(grant(**changes),{'receipt_id':'parent'},datetime(2026,9,12,tzinfo=timezone.utc))


@pytest.mark.parametrize('revoked,day',[(True,12),(False,15)])
def test_expiry_and_revocation(revoked,day):
    with pytest.raises(PermissionError,match='REVOKED_OR_EXPIRED'):
        validate_release(grant(),{'receipt_id':'parent'},datetime(2026,9,day,tzinfo=timezone.utc),revoked)


def test_modified_receipt_rejected():
    value=grant()
    value['end']='2026-08-01'
    with pytest.raises(PermissionError,match='IDENTITY_CHANGED'):
        validate_release(value,{'receipt_id':'parent'},datetime(2026,9,12,tzinfo=timezone.utc))


def test_monthly_calendar_upper_bound_cannot_pass_old_gate():
    import pandas as pd
    from check_monthly_window_capacity_v1 import calendar_bound
    sessions=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-07-03','2026-07-31')]
    strict=calendar_bound(sessions)
    generous=calendar_bound(sessions,include_warmup_signal=True)
    assert strict['maximum_entry_dates']==11
    assert generous['maximum_entry_dates']==12
    assert not strict['original_gate_on_optimistic_paths']['passed']
    assert not generous['original_gate_on_optimistic_paths']['passed']


def test_calendar_conflict_is_not_silently_deduplicated():
    from check_monthly_window_capacity_v1 import calendar_bound
    with pytest.raises(ValueError,match='CALENDAR_IDENTITY_REQUIRED'):
        calendar_bound([20250801,20250801])
