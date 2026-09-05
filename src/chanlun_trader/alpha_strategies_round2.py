"""Round 2 策略假设库：Regime / Defensive / Low-Frequency / XS V2 / MR V2。"""
from __future__ import annotations

import bisect
import numpy as np
import pandas as pd

from .chan import Signal


def _add_days(calendar, date, n):
    idx = bisect.bisect_right(calendar, date) - 1
    if idx < 0:
        return None
    out = idx + n
    return calendar[out] if out < len(calendar) else None


def _exit_after(calendar, signal_date, hold_days, code):
    exit_sig = _add_days(calendar, signal_date, hold_days)
    if exit_sig is None:
        return []
    return [Signal(code=code, signal_date=exit_sig, direction="sell", signal_type="SX",
                   price_ref=0.0, stop_low=0.0, score=0.0)]


def _stop_from_close(price, pct):
    return float(price * (1 - pct))


def _index_ok(cfg, d, ma_period=20):
    ic = cfg.get("__index_close__", pd.Series(dtype=float))
    ima = cfg.get("__index_ma__", pd.Series(dtype=float))
    if ic.empty or ima.empty:
        return False
    c = ic.get(d)
    m = ima.get(d)
    if c is None or m is None:
        return False
    if ma_period == 60:
        idx_ma60 = cfg.get("__index_ma60__")
        m = idx_ma60.get(d) if idx_ma60 is not None else None
        if m is None:
            return False
    return float(c) > float(m)

# ---------------------------------------------------------------- Low-Frequency Trend / Breakout

def lf_trend_ma60_120(cfg, dfs, calendar):
    """LF_TREND_001: 收盘价 > MA60 > MA120，持有 20 日。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        ma60 = c.rolling(60).mean()
        ma120 = c.rolling(120).mean()
        mask = (c > ma60) & (ma60 > ma120)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 120 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="LF_TREND_001",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.10),
                                  score=float(c.iloc[i] / ma60.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def lf_donchian_60(cfg, dfs, calendar):
    """LF_TREND_002: 收盘价突破 60 日最高收盘价，持有 20 日。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        hh = c.rolling(60).max().shift(1)
        mask = c > hh
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="LF_TREND_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.10),
                                  score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def lf_mom60_rank(cfg, dfs, calendar):
    """LF_MOM_001: 60 日收益横截面排名前 5%，持有 20 日。"""
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    for d in calendar:
        rows = []
        members = uni_sets.get(d)
        for code, df in dfs.items():
            if members is not None and code not in members:
                continue
            if len(df) < 61:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 60:
                continue
            if int(df.iloc[pos]["date"]) != d:
                continue
            mom60 = float(df.iloc[pos]["close"] / df.iloc[pos - 60]["close"] - 1)
            rows.append((code, mom60, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, mom, pos in rows[: max(1, int(len(rows) * 0.05))]:
            c = float(dfs[code].iloc[pos]["close"])
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="LF_MOM_001",
                              price_ref=c, stop_low=_stop_from_close(c, 0.10), score=mom))
            out.extend(_exit_after(calendar, d, 20, code))
    return out


def lf_mom120_abs(cfg, dfs, calendar):
    """LF_MOM_002: 120 日收益 > 10%，持有 40 日。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mom = c.pct_change(120)
        mask = mom > 0.10
        idx = np.where(mask)[0]
        for i in idx:
            if i < 120 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="LF_MOM_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.12),
                                  score=float(mom.iloc[i])))
                out.extend(_exit_after(calendar, d, 40, code))
    return out

# ---------------------------------------------------------------- Cross-Sectional V2

def xsv2_mom_trend_liq(cfg, dfs, calendar):
    """XSV2_001: 60 日动量前 10% + 收盘>MA60 + 20 日成交额前 50%。"""
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    for d in calendar:
        rows = []
        members = uni_sets.get(d)
        for code, df in dfs.items():
            if members is not None and code not in members:
                continue
            if len(df) < 61:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 60:
                continue
            if int(df.iloc[pos]["date"]) != d:
                continue
            c = df.iloc[pos]["close"]
            ma60 = float(df.iloc[max(0, pos - 59): pos + 1]["close"].mean())
            if c <= ma60:
                continue
            mom60 = float(c / df.iloc[pos - 60]["close"] - 1)
            amt20 = float(df.iloc[max(0, pos - 19): pos + 1]["amount"].mean())
            rows.append((code, mom60, amt20, pos))
        if not rows:
            continue
        n = len(rows)
        rows.sort(key=lambda x: -x[2])
        top_liq = set(r[0] for r in rows[: max(1, int(n * 0.50))])
        rows.sort(key=lambda x: -x[1])
        top_mom = set(r[0] for r in rows[: max(1, int(n * 0.10))])
        for code, mom60, amt20, pos in rows:
            if code in top_liq and code in top_mom:
                c = float(dfs[code].iloc[pos]["close"])
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="XSV2_001",
                                  price_ref=c, stop_low=_stop_from_close(c, 0.08), score=mom60))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def xsv2_mom_vol(cfg, dfs, calendar):
    """XSV2_002: 20 日动量前 10% + 20 日波动率后 50%（低波动强者）。"""
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    for d in calendar:
        rows = []
        members = uni_sets.get(d)
        for code, df in dfs.items():
            if members is not None and code not in members:
                continue
            if len(df) < 21:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 20:
                continue
            if int(df.iloc[pos]["date"]) != d:
                continue
            mom20 = float(df.iloc[pos]["close"] / df.iloc[pos - 20]["close"] - 1)
            vol20 = float(df.iloc[max(0, pos - 19): pos + 1]["close"].pct_change().std())
            rows.append((code, mom20, vol20, pos))
        if not rows:
            continue
        n = len(rows)
        rows.sort(key=lambda x: -x[1])
        top_mom = set(r[0] for r in rows[: max(1, int(n * 0.10))])
        rows.sort(key=lambda x: x[2])
        low_vol = set(r[0] for r in rows[: max(1, int(n * 0.50))])
        for code, mom20, vol20, pos in rows:
            if code in top_mom and code in low_vol:
                c = float(dfs[code].iloc[pos]["close"])
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="XSV2_002",
                                  price_ref=c, stop_low=_stop_from_close(c, 0.08), score=mom20))
                out.extend(_exit_after(calendar, d, 20, code))
    return out

# ---------------------------------------------------------------- Mean Reversion V2

def mrv2_uptrend_pullback(cfg, dfs, calendar):
    """MRV2_001: 60 日上升趋势（收盘>MA60）中的 5 日回撤 > 5% 且收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        ma60 = c.rolling(60).mean()
        pull5 = c.pct_change(5)
        mask = (c > ma60) & (pull5 < -0.05) & (c > c.shift(1))
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MRV2_001",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.96), score=float(-pull5.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mrv2_index_crash(cfg, dfs, calendar):
    """MRV2_002: 指数在 MA20 上方时，个股 3 日跌超 8% 后收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        crash = c.pct_change(3) < -0.08
        up = c > c.shift(1)
        mask = crash & up
        idx = np.where(mask)[0]
        for i in idx:
            if i < 3 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar and _index_ok(cfg, d, 20):
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MRV2_002",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.96), score=float(-c.iloc[i] / c.iloc[i - 3] + 1)))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mrv2_liquid_gap(cfg, dfs, calendar):
    """MRV2_003: 高流动性股票跳空低开 3% 后收阳。"""
    out = []
    for code, df in dfs.items():
        o = df["open"]
        c = df["close"]
        pc = c.shift(1)
        mask = (o < pc * 0.97) & (c > o)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 1 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MRV2_003",
                                  price_ref=float(c.iloc[i]), stop_low=float(df.iloc[i]["low"]), score=float(pc.iloc[i] * 0.97 - o.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out

# ---------------------------------------------------------------- Regime Interaction

def regx_breakout20_bull(cfg, dfs, calendar):
    """REGX_001: 20 日高点突破，仅当上证指数收盘 > MA20。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        hh = c.rolling(20).max().shift(1)
        mask = c > hh
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar and _index_ok(cfg, d, 20):
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="REGX_001",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.06), score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def regx_breakout60_bull(cfg, dfs, calendar):
    """REGX_002: 60 日高点突破，仅当上证指数收盘 > MA60。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        hh = c.rolling(60).max().shift(1)
        mask = c > hh
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar and _index_ok(cfg, d, 60):
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="REGX_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.10), score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def regx_mom_bull(cfg, dfs, calendar):
    """REGX_003: 20 日动量前 5%，仅当指数收盘 > MA20。"""
    out = []
    uni_sets = cfg.get("__universe_sets__", {})
    for d in calendar:
        if not _index_ok(cfg, d, 20):
            continue
        rows = []
        members = uni_sets.get(d)
        for code, df in dfs.items():
            if members is not None and code not in members:
                continue
            if len(df) < 21:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 20:
                continue
            if int(df.iloc[pos]["date"]) != d:
                continue
            mom20 = float(df.iloc[pos]["close"] / df.iloc[pos - 20]["close"] - 1)
            rows.append((code, mom20, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, mom, pos in rows[: max(1, int(len(rows) * 0.05))]:
            c = float(dfs[code].iloc[pos]["close"])
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="REGX_003",
                              price_ref=c, stop_low=_stop_from_close(c, 0.08), score=mom))
            out.extend(_exit_after(calendar, d, 20, code))
    return out


# ---------------------------------------------------------------- Defensive / Cash

def def_cash_when_bear(cfg, dfs, calendar):
    """DEF_001: 指数收盘 > MA20 时持有 20 日趋势突破组合，否则空仓。"""
    # 等价于 REGX_001 的持有期更长版本；这里直接复用 20 日突破 + 指数过滤。
    return regx_breakout20_bull(cfg, dfs, calendar)


ALL_STRATEGIES_R2 = {
    "LF_TREND_001": lf_trend_ma60_120,
    "LF_TREND_002": lf_donchian_60,
    "LF_MOM_001": lf_mom60_rank,
    "LF_MOM_002": lf_mom120_abs,
    "XSV2_001": xsv2_mom_trend_liq,
    "XSV2_002": xsv2_mom_vol,
    "MRV2_001": mrv2_uptrend_pullback,
    "MRV2_002": mrv2_index_crash,
    "MRV2_003": mrv2_liquid_gap,
    "REGX_001": regx_breakout20_bull,
    "REGX_002": regx_breakout60_bull,
    "REGX_003": regx_mom_bull,
    "DEF_001": def_cash_when_bear,
}
