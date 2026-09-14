import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from prepare_weekly_window_decision_v1 import date_capacity


def test_calendar_only_bound_by_hand():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-08-01',periods=43)]
    # 周五在0/5/10/15/20，之后至少有22session；其余周五不完整。
    assert date_capacity(days,1)==5
    assert date_capacity(days,200)==0


def test_calendar_holiday_is_not_record_count_week():
    days=[int(d.strftime('%Y%m%d')) for d in pd.bdate_range('2025-08-01',periods=43)]
    days.remove(20250808)
    assert date_capacity(days,1)==5  # 8月7日成为该周末，不遗漏整周。


def test_duplicate_calendar_rejected():
    with pytest.raises(ValueError,match='CALENDAR'):
        date_capacity([20250801,20250801],1)
