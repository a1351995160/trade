import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from acquire_weekly_window_v1 import resolve_calendar, overlap_check


def calendar_fixture():
    rows=[{'calendar_date':d.strftime('%Y-%m-%d'),'is_trading_day':str(int(d.weekday()<5))}
          for d in pd.date_range('2024-08-01','2025-07-31')]
    existing=[d.strftime('%Y-%m-%d') for d in pd.bdate_range('2025-07-03','2026-07-31')]
    return rows,existing


def test_exact_calendar_warmup_and_sealed_end():
    rows,existing=calendar_fixture()
    result=resolve_calendar(rows,existing)
    assert len([d for d in result['sessions'] if d<'2025-08-01'])==200
    assert result['fetch_end']==existing[4]
    assert result['sessions'][-1]=='2026-07-31'
    assert set(result['missing_sessions']).isdisjoint(existing)


def test_calendar_gap_and_conflict_fail():
    rows,existing=calendar_fixture()
    with pytest.raises(ValueError,match='GAP'):resolve_calendar(rows[:10]+rows[11:],existing)
    rows,existing=calendar_fixture()
    with pytest.raises(ValueError,match='CONFLICT'):resolve_calendar(rows,existing[:2]+existing[3:])


def test_overlap_no_rescaling_no_tolerance():
    rows=[{'date':'2025-07-03','close':'10.0000'}]
    assert overlap_check(rows,[{'date':'2025-07-03','close':'10'}],['2025-07-03'],'date,close')['rows']==1
    for close in ['10.0001','1000','NaN']:
        with pytest.raises(ValueError,match='VALUE_CONFLICT'):
            overlap_check(rows,[{'date':'2025-07-03','close':close}],['2025-07-03'],'date,close')


def test_overlap_missing_is_not_zero_or_dropped():
    with pytest.raises(ValueError,match='COVERAGE'):
        overlap_check([{'date':'2025-07-03','close':'10'}],[],['2025-07-03'],'close')
    assert overlap_check([],[],['2025-07-03'],'close')['status']=='NO_ROWS_IN_EITHER_SOURCE'
