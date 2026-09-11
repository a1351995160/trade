"""Lookahead / AsOf Guard / Future Truncation tests."""
import pandas as pd
import pytest

from src.chanlun_trader.engine.asof import AsOfDataView, LookaheadViolation, MarketDataStore
from src.chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from src.chanlun_trader.engine.time_types import TradingCalendar, TradingClock, EventKind, tz_aware

from tests.golden._helpers import CAL, make_daily_df


def test_index_filter_no_same_day_close_leakage():
    store = make_store = MarketDataStore()
    for sym in ["600000.SH"]:
        store.add_daily_raw(sym, make_daily_df(days=CAL))
        store.add_daily_qfq(sym, make_daily_df(days=CAL))
    # 构造：T-1 close=10（高），T close=1（低）
    index_closes = {CAL[0]: 10.0, CAL[1]: 1.0}
    eng = BacktestEngineV2(store, CAL, config=EngineConfig(mode="DAILY", persist_run_manifest=False), index_closes=index_closes, source_identity=("UNKNOWN", True))
    eng._build()
    # V2 在 T (CAL[1]) 只能看到 CAL[0] close=10 -> 均线 10 -> allowed
    assert eng._index_ok(CAL[1]) is True
    # V1 searchsorted(date,'right')-1 会读到当日 close=1 -> 1 < ma(5.5) -> blocked
    s = pd.Series(index_closes).sort_index()
    pos_v1 = s.index.searchsorted(CAL[1], side="right") - 1
    close_v1 = float(s.iloc[pos_v1])
    ma_v1 = float(s.iloc[: pos_v1 + 1].tail(20).mean())
    assert close_v1 < ma_v1  # 明确 V1 会误判
    assert close_v1 == 1.0


def test_asof_guard_raises_on_future_read():
    store = MarketDataStore()
    store.add_daily_raw("600000.SH", make_daily_df(days=CAL))
    view = AsOfDataView(store, tz_aware(2025, 1, 3, 10, 0))
    with pytest.raises(LookaheadViolation):
        view.daily_history("600000.SH", tz_aware(2025, 1, 6, 10, 0))


def test_future_truncation_feature_history_unchanged():
    store = MarketDataStore()
    df = make_daily_df(days=CAL)
    store.add_daily_qfq("600000.SH", df)
    view = AsOfDataView(store, tz_aware(2025, 1, 3, 15, 0))
    h1 = view.daily_history("600000.SH", tz_aware(2025, 1, 3, 15, 0))
    # 追加未来数据后，同一 as_of 视图不变
    store.add_daily_qfq("600000.SH", df)  # same data, no change
    h2 = view.daily_history("600000.SH", tz_aware(2025, 1, 3, 15, 0))
    pd.testing.assert_frame_equal(h1, h2)


def test_clock_daily_event_order():
    cal = TradingCalendar(CAL)
    clock = TradingClock(cal, mode="DAILY")
    first_day = [e for e in clock.events if e.date == CAL[0]]
    kinds = [e.kind for e in first_day]
    assert kinds == [EventKind.SESSION_OPEN, EventKind.BAR_CLOSE, EventKind.SESSION_CLOSE, EventKind.AFTER_CLOSE]
    times = [e.timestamp.strftime("%H:%M") for e in first_day]
    assert times == ["09:30", "15:00", "15:00", "15:30"]


def test_clock_5min_events():
    cal = TradingCalendar([20250102])
    clock = TradingClock(cal, mode="5MIN")
    bar_closes = [e for e in clock.events if e.kind == EventKind.BAR_CLOSE]
    assert len(bar_closes) == 48
    assert bar_closes[0].timestamp.strftime("%H:%M") == "09:35"
    assert bar_closes[-1].timestamp.strftime("%H:%M") == "15:00"
