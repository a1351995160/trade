"""Golden test helpers — synthetic deterministic A-share data."""
import pandas as pd

from src.chanlun_trader.engine.asof import MarketDataStore
from src.chanlun_trader.engine.time_types import tz_aware

CAL = [20250102, 20250103, 20250106, 20250107, 20250108,
       20250109, 20250110, 20250113, 20250114, 20250115]


def make_daily_df(open_px=10.0, close_px=10.2, high_px=10.5, low_px=9.8,
                  volume=1_000_000.0, days=CAL, amount=10_000_000.0):
    rows = []
    prev = None
    for d in days:
        rows.append({
            "open": open_px, "high": high_px, "low": low_px, "close": close_px,
            "volume": volume, "amount": amount,
            "prev_close": prev if prev is not None else open_px,
        })
        prev = close_px
    return pd.DataFrame(rows, index=pd.Index(days, name="date"))


def make_store():
    store = MarketDataStore()
    for sym in ["600000.SH", "000001.SZ", "300001.SZ"]:
        store.add_daily_raw(sym, make_daily_df())
        store.add_daily_qfq(sym, make_daily_df())
    return store


def make_5min_store(symbol="600000.SH", day=20250102):
    """5分钟 bars, 索引为 bar 结束时间。bar 10:35-10:40 的 open=10.4 等。"""
    store = MarketDataStore()
    times = []
    o, h, l, c = 10.0, 10.2, 9.9, 10.1
    rows = []
    for hh, mm in [(9, 35), (9, 40), (9, 45), (10, 0), (10, 5), (10, 10),
                   (10, 15), (10, 20), (10, 25), (10, 30), (10, 35), (10, 40),
                   (10, 45), (10, 50), (10, 55), (11, 0), (11, 5), (11, 10),
                   (11, 15), (11, 20), (11, 25), (11, 30), (13, 5), (13, 10),
                   (13, 15), (13, 20), (13, 25), (13, 30), (13, 35), (13, 40),
                   (13, 45), (13, 50), (13, 55), (14, 0), (14, 5), (14, 10),
                   (14, 15), (14, 20), (14, 25), (14, 30), (14, 35), (14, 40),
                   (14, 45), (14, 50), (14, 55), (15, 0)]:
        y, m, d = day // 10000, (day // 100) % 100, day % 100
        ts = tz_aware(y, m, d, hh, mm)
        # make the 10:40 bar (index 11) open distinctive
        open_px = 10.4 if (hh, mm) == (10, 40) else 10.0 + 0.01 * len(rows)
        rows.append({"open": open_px, "high": open_px + 0.2, "low": open_px - 0.1,
                     "close": open_px + 0.1, "volume": 10_000_000.0, "amount": 100_000_000.0})
        times.append(ts)
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
    store.add_minute_5(symbol, df)
    store.add_daily_raw(symbol, make_daily_df(days=[day], open_px=10.0, close_px=10.2, high_px=10.6, low_px=9.8))
    store.add_daily_qfq(symbol, make_daily_df(days=[day], open_px=10.0, close_px=10.2, high_px=10.6, low_px=9.8))
    return store
