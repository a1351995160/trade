"""A股 Reality 单元测试：涨跌停 / 停牌 / T+1 / Lot / Fee。"""
import pytest

from src.chanlun_trader.engine.security_state import ChinaPriceLimitModel, PriceLimitState, SecurityMaster, SecurityState
from src.chanlun_trader.engine.time_types import tz_aware


def test_st_stock_limit_5pct():
    master = SecurityMaster()
    master.add_state(SecurityState(symbol="600000.SH", asof_date=20250102, is_st=True, board="MAIN"))
    m = ChinaPriceLimitModel(master)
    assert m.limit_pct("600000.SH", tz_aware(2025, 1, 3, 9, 30)) == 0.05
    up, down = m.limit_prices("600000.SH", tz_aware(2025, 1, 3, 9, 30), 10.0)
    assert up == 10.5 and down == 9.5


def test_board_limit_by_prefix():
    m = ChinaPriceLimitModel()
    assert m.limit_pct("300001.SZ", tz_aware(2025, 1, 3, 9, 30)) == 0.20
    assert m.limit_pct("688001.SH", tz_aware(2025, 1, 3, 9, 30)) == 0.20
    assert m.limit_pct("600000.SH", tz_aware(2025, 1, 3, 9, 30)) == 0.10


def test_pit_st_state_switch():
    master = SecurityMaster()
    master.add_state(SecurityState(symbol="600000.SH", asof_date=20250102, is_st=False, board="MAIN"))
    master.add_state(SecurityState(symbol="600000.SH", asof_date=20250110, is_st=True, board="MAIN"))
    m = ChinaPriceLimitModel(master)
    assert m.limit_pct("600000.SH", tz_aware(2025, 1, 3, 9, 30)) == 0.10
    assert m.limit_pct("600000.SH", tz_aware(2025, 1, 13, 9, 30)) == 0.05


def test_limit_up_opened_daily_conservative():
    m = ChinaPriceLimitModel()
    bar = {"date": 20250103, "open": 11.0, "high": 11.5, "low": 10.9, "close": 11.2,
           "volume": 10000.0, "amount": 1.0, "prev_close": 10.0}
    st = m.classify_daily("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar)
    assert st == PriceLimitState.LIMIT_UP_OPENED
    ok, reason = m.can_buy_at_open("600000.SH", tz_aware(2025, 1, 3, 9, 30), bar)
    assert not ok and reason == "LIMIT_UP_OPEN_DAILY_CONSERVATIVE"


def test_suspension_model():
    from src.chanlun_trader.engine.security_state import SuspensionModel
    sm = SuspensionModel()
    assert sm.is_suspended("600000.SH", tz_aware(2025, 1, 3, 9, 30), None)
    assert sm.is_suspended("600000.SH", tz_aware(2025, 1, 3, 9, 30), {"volume": 0.0})
    assert not sm.is_suspended("600000.SH", tz_aware(2025, 1, 3, 9, 30), {"volume": 100.0})
