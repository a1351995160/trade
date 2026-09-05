"""Broad Search 策略假设库。

每个策略函数签名：
    strategy(cfg, dfs, calendar) -> list[Signal]

- dfs: {code: DataFrame[date, open, high, low, close, volume, amount]}（qfq 价格）
- calendar: 交易日列表（int YYYYMMDD）
- 只做多头。
- 每个策略先定义 Alpha Hypothesis，再实现，禁止无逻辑指标堆叠。
"""
from __future__ import annotations

import bisect
import numpy as np
import pandas as pd

from .chan import Signal


def _next_day(calendar: list[int], date: int) -> int | None:
    idx = bisect.bisect_right(calendar, date) - 1
    if idx < 0:
        return None
    out = idx + 1
    return calendar[out] if out < len(calendar) else None


def _add_days(calendar: list[int], date: int, n: int) -> int | None:
    idx = bisect.bisect_right(calendar, date) - 1
    if idx < 0:
        return None
    out = idx + n
    return calendar[out] if out < len(calendar) else None


def _exit_after(calendar: list[int], signal_date: int, hold_days: int, sell_code: str) -> list[Signal]:
    """在持有 hold_days 个交易日后的下一根 K 线卖出。"""
    exit_sig = _add_days(calendar, signal_date, hold_days)
    if exit_sig is None:
        return []
    return [Signal(code=sell_code, signal_date=exit_sig, direction="sell", signal_type="SX",
                   price_ref=0.0, stop_low=0.0, score=0.0)]


def _stop_from_close(price: float, stop_pct: float) -> float:
    return float(price * (1 - stop_pct))

# ---------------------------------------------------------------- Trend Family

def trend_ma_structure(cfg, dfs, calendar):
    """TREND_001: 收盘价 > MA20 > MA60，均线多头排列。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        mask = (c > ma20) & (ma20 > ma60)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="TREND_001",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(c.iloc[i] / ma20.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def trend_ma_rising(cfg, dfs, calendar):
    """TREND_002: MA20 连续 5 日上升，且收盘价刚上穿 MA20。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        ma20 = c.rolling(20).mean()
        cond = (ma20.diff() > 0).rolling(5).sum().ge(5)
        cross = (c > ma20) & (c.shift(1) <= ma20.shift(1))
        mask = cond & cross
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="TREND_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(c.iloc[i] / ma20.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def trend_donchian(cfg, dfs, calendar):
    """TREND_003: 收盘价突破 20 日最高价（Donchian 价格通道）。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        hh = df["high"].rolling(20).max().shift(1)
        mask = c > hh
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="TREND_003",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out

# ---------------------------------------------------------------- Momentum Family

def momentum_20d(cfg, dfs, calendar):
    """MOM_001: 20 日时序动量 > 10%。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mom = c.pct_change(20)
        mask = mom > 0.10
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MOM_001",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(mom.iloc[i])))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def momentum_60d(cfg, dfs, calendar):
    """MOM_002: 60 日时序动量 > 20%。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mom = c.pct_change(60)
        mask = mom > 0.20
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MOM_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.10), score=float(mom.iloc[i])))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def momentum_acceleration(cfg, dfs, calendar):
    """MOM_003: 20 日动量 > 60 日动量（加速）。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mom20 = c.pct_change(20)
        mom60 = c.pct_change(60)
        mask = (mom20 > 0.05) & (mom20 > mom60)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MOM_003",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(mom20.iloc[i] - mom60.iloc[i])))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def momentum_pullback(cfg, dfs, calendar):
    """MOM_004: 60 日动量 > 10%（强势股）且最近 5 日回撤 > 5%。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mom60 = c.pct_change(60)
        pull5 = c.pct_change(5)
        mask = (mom60 > 0.10) & (pull5 < -0.05)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MOM_004",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(-pull5.iloc[i])))
                out.extend(_exit_after(calendar, d, 10, code))
    return out

# ---------------------------------------------------------------- Breakout Family

def breakout_20d_high(cfg, dfs, calendar):
    """BRK_001: 收盘价突破 20 日最高收盘价。"""
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
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BRK_001",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.06), score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def breakout_55d_high(cfg, dfs, calendar):
    """BRK_002: 收盘价突破 55 日最高收盘价（中长期突破）。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        hh = c.rolling(55).max().shift(1)
        mask = c > hh
        idx = np.where(mask)[0]
        for i in idx:
            if i < 55 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BRK_002",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(c.iloc[i] / hh.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def breakout_atr(cfg, dfs, calendar):
    """BRK_003: 收盘价突破前一收盘 + 1.5*ATR(14)。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        h = df["high"]
        l = df["low"]
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        mask = c > pc + 1.5 * atr
        idx = np.where(mask)[0]
        for i in idx:
            if i < 14 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BRK_003",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] - 1.5 * atr.iloc[i]), score=float(c.iloc[i] / (pc.iloc[i] + 1.5 * atr.iloc[i]) - 1)))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def breakout_vol_expansion(cfg, dfs, calendar):
    """BRK_004: 20 日高点突破 + 量能放大 2 倍。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        v = df["volume"]
        hh = c.rolling(20).max().shift(1)
        vma = v.rolling(20).mean().shift(1)
        mask = (c > hh) & (v > 2 * vma)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BRK_004",
                                  price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.06), score=float(v.iloc[i] / vma.iloc[i])))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def breakout_bb_squeeze(cfg, dfs, calendar):
    """BRK_005: Bollinger 带宽 60 日低位 + 收盘突破上轨。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mid = c.rolling(20).mean()
        std = c.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        width = (upper - lower) / mid
        width_low = width.rolling(60).apply(
            lambda x: (x[-1] <= np.percentile(x[:-1], 20)) if len(x) > 10 else False, raw=True)
        mask = (c > upper) & width_low
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="BRK_005",
                                  price_ref=float(c.iloc[i]), stop_low=float(mid.iloc[i]), score=float(c.iloc[i] / upper.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 10, code))
    return out

# ---------------------------------------------------------------- Mean Reversion Family

def mr_crash_reversal(cfg, dfs, calendar):
    """MR_001: 3 日跌超 10% 后收阳（短期恐慌反转）。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        crash = c.pct_change(3) < -0.10
        up = c > c.shift(1)
        mask = crash & up
        idx = np.where(mask)[0]
        for i in idx:
            if i < 3 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MR_001",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.97), score=float(-c.iloc[i] / c.iloc[i - 3] + 1)))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mr_rsi_oversold(cfg, dfs, calendar):
    """MR_002: RSI(14) < 30 且收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        diff = c.diff()
        gain = diff.clip(lower=0).rolling(14).mean()
        loss = (-diff.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        mask = (rsi < 30) & (c > c.shift(1))
        idx = np.where(mask)[0]
        for i in idx:
            if i < 14 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MR_002",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.97), score=float(30.0 - rsi.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mr_bollinger_lower(cfg, dfs, calendar):
    """MR_003: 收盘跌破布林下轨后次日收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        mid = c.rolling(20).mean()
        std = c.rolling(20).std()
        lower = mid - 2 * std
        prev_below = c.shift(1) < lower.shift(1)
        up = c > c.shift(1)
        mask = prev_below & up
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MR_003",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.97), score=float((lower.iloc[i] - c.iloc[i]) / std.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mr_gap_reversal(cfg, dfs, calendar):
    """MR_004: 跳空低开超 3% 后收阳（假摔）。"""
    out = []
    for code, df in dfs.items():
        o = df["open"]
        c = df["close"]
        pc = c.shift(1)
        gap_down = o < pc * 0.97
        up = c > o
        mask = gap_down & up
        idx = np.where(mask)[0]
        for i in idx:
            if i < 1 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MR_004",
                                  price_ref=float(c.iloc[i]), stop_low=float(df.iloc[i]["low"]), score=float(pc.iloc[i] * 0.97 - o.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def mr_zscore_reversal(cfg, dfs, calendar):
    """MR_005: 5 日收益 Z-Score < -2 且收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        r = c.pct_change()
        std = r.rolling(20).std().replace(0, np.nan)
        z = (r - r.rolling(20).mean()) / std
        mask = (z < -2) & (c > c.shift(1))
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="MR_005",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.97), score=float(-z.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out

# ---------------------------------------------------------------- Volume / Liquidity

def vol_expansion_up(cfg, dfs, calendar):
    """VOL_001: 成交量 3 倍于 20 日均量且收阳。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        v = df["volume"]
        vma = v.rolling(20).mean().shift(1)
        mask = (v > 3 * vma) & (c > c.shift(1))
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="VOL_001",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.95), score=float(v.iloc[i] / vma.iloc[i])))
                out.extend(_exit_after(calendar, d, 5, code))
    return out


def vol_contraction_expansion(cfg, dfs, calendar):
    """VOL_002: 缩量（5 日均量 < 0.6*20 日均量）后放量（> 1.5*20 日均量）。"""
    out = []
    for code, df in dfs.items():
        v = df["volume"]
        c = df["close"]
        v5 = v.rolling(5).mean()
        v20 = v.rolling(20).mean()
        shrink = v5 < 0.6 * v20
        expand = v5 > 1.5 * v20
        prev_shrink = shrink.shift(1) | shrink.shift(2)
        mask = prev_shrink & expand & (c > c.shift(1))
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="VOL_002",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.95), score=float(v5.iloc[i] / v20.iloc[i])))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def vol_price_divergence(cfg, dfs, calendar):
    """VOL_003: 价格创 10 日新高但成交量低于 20 日均量（缩量新高）。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        v = df["volume"]
        hh = c.rolling(10).max().shift(1)
        vma = v.rolling(20).mean().shift(1)
        mask = (c > hh) & (v < vma)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 20 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="VOL_003",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] * 0.96), score=float(vma.iloc[i] / v.iloc[i])))
                out.extend(_exit_after(calendar, d, 10, code))
    return out

# ---------------------------------------------------------------- Volatility

def vol_compression_breakout(cfg, dfs, calendar):
    """VLT_001: ATR(14) 处于 60 日低分位 + 收盘突破 10 日高点。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        h = df["high"]
        l = df["low"]
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_rank = atr.rolling(60).apply(
            lambda x: (x[-1] <= np.percentile(x[:-1], 20)) if len(x) > 20 else False, raw=True).astype(bool)
        hh10 = c.rolling(10).max().shift(1)
        mask = atr_rank & (c > hh10)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="VLT_001",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] - 2 * atr.iloc[i]), score=float(c.iloc[i] / hh10.iloc[i] - 1)))
                out.extend(_exit_after(calendar, d, 10, code))
    return out


def vol_expansion_breakout(cfg, dfs, calendar):
    """VLT_002: ATR(14) > 1.5*ATR(60)（波动扩张）+ 收盘突破 20 日高点。"""
    out = []
    for code, df in dfs.items():
        c = df["close"]
        h = df["high"]
        l = df["low"]
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr60 = tr.rolling(60).mean()
        hh20 = c.rolling(20).max().shift(1)
        mask = (atr > 1.5 * atr60) & (c > hh20)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d in calendar:
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="VLT_002",
                                  price_ref=float(c.iloc[i]), stop_low=float(c.iloc[i] - 2 * atr.iloc[i]), score=float(atr.iloc[i] / atr60.iloc[i])))
                out.extend(_exit_after(calendar, d, 10, code))
    return out

# ---------------------------------------------------------------- Cross Section

def xs_momentum_20d_rank(cfg, dfs, calendar):
    """XS_001: 20 日收益横截面排名前 5%。"""
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
            mom = float(df.iloc[pos]["close"] / df.iloc[pos - 20]["close"] - 1)
            rows.append((code, mom, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        topn = max(1, int(len(rows) * 0.05))
        for code, mom, pos in rows[:topn]:
            c = float(dfs[code].iloc[pos]["close"])
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="XS_001",
                              price_ref=c, stop_low=_stop_from_close(c, 0.08), score=float(mom)))
            out.extend(_exit_after(calendar, d, 20, code))
    return out


def xs_rel_strength_60_20(cfg, dfs, calendar):
    """XS_002: 60 日排名前 10% 且 20 日排名前 20%。"""
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
            mom20 = float(df.iloc[pos]["close"] / df.iloc[pos - 20]["close"] - 1)
            rows.append((code, mom60, mom20, pos))
        if not rows:
            continue
        n = len(rows)
        rows.sort(key=lambda x: -x[1])
        top60 = set(r[0] for r in rows[: max(1, int(n * 0.10))])
        rows.sort(key=lambda x: -x[2])
        top20 = set(r[0] for r in rows[: max(1, int(n * 0.20))])
        for code, mom60, mom20, pos in rows:
            if code in top60 and code in top20:
                c = float(dfs[code].iloc[pos]["close"])
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="XS_002",
                                  price_ref=c, stop_low=_stop_from_close(c, 0.08), score=float(mom60 + mom20)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out


def xs_composite_liq_mom(cfg, dfs, calendar):
    """XS_003: 20 日成交额排名前 30% 且 20 日动量排名前 10%。"""
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
            amt20 = float(df.iloc[max(0, pos - 19): pos + 1]["amount"].mean())
            rows.append((code, mom20, amt20, pos))
        if not rows:
            continue
        n = len(rows)
        rows.sort(key=lambda x: -x[2])
        top_liq = set(r[0] for r in rows[: max(1, int(n * 0.30))])
        rows.sort(key=lambda x: -x[1])
        top_mom = set(r[0] for r in rows[: max(1, int(n * 0.10))])
        for code, mom20, amt20, pos in rows:
            if code in top_liq and code in top_mom:
                c = float(dfs[code].iloc[pos]["close"])
                out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="XS_003",
                                  price_ref=c, stop_low=_stop_from_close(c, 0.08), score=float(mom20)))
                out.extend(_exit_after(calendar, d, 20, code))
    return out

# ---------------------------------------------------------------- Market Regime / Defensive

def reg_index_ma_filter_only(cfg, dfs, calendar):
    """REG_001: 趋势结构 + 上证指数收盘 > MA20 才允许买入。"""
    out = []
    index_close = cfg.get("__index_close__", pd.Series(dtype=float))
    index_ma = cfg.get("__index_ma__", pd.Series(dtype=float))
    for code, df in dfs.items():
        c = df["close"]
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        mask = (c > ma20) & (ma20 > ma60)
        idx = np.where(mask)[0]
        for i in idx:
            if i < 60 or i >= len(df) - 1:
                continue
            d = int(df.iloc[i]["date"])
            if d not in calendar:
                continue
            ic = index_close.get(d)
            ima = index_ma.get(d)
            if ic is None or ima is None or ic <= ima:
                continue
            out.append(Signal(code=code, signal_date=d, direction="buy", signal_type="REG_001",
                              price_ref=float(c.iloc[i]), stop_low=_stop_from_close(float(c.iloc[i]), 0.08), score=float(c.iloc[i] / ma20.iloc[i] - 1)))
            out.extend(_exit_after(calendar, d, 20, code))
    return out


ALL_STRATEGIES = {
    "TREND_001": trend_ma_structure,
    "TREND_002": trend_ma_rising,
    "TREND_003": trend_donchian,
    "MOM_001": momentum_20d,
    "MOM_002": momentum_60d,
    "MOM_003": momentum_acceleration,
    "MOM_004": momentum_pullback,
    "BRK_001": breakout_20d_high,
    "BRK_002": breakout_55d_high,
    "BRK_003": breakout_atr,
    "BRK_004": breakout_vol_expansion,
    "BRK_005": breakout_bb_squeeze,
    "MR_001": mr_crash_reversal,
    "MR_002": mr_rsi_oversold,
    "MR_003": mr_bollinger_lower,
    "MR_004": mr_gap_reversal,
    "MR_005": mr_zscore_reversal,
    "VOL_001": vol_expansion_up,
    "VOL_002": vol_contraction_expansion,
    "VOL_003": vol_price_divergence,
    "VLT_001": vol_compression_breakout,
    "VLT_002": vol_expansion_breakout,
    "XS_001": xs_momentum_20d_rank,
    "XS_002": xs_rel_strength_60_20,
    "XS_003": xs_composite_liq_mom,
    "REG_001": reg_index_ma_filter_only,
}
