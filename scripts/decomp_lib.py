"""Alpha Decomposition 共享库：Universe 构建、Timing/Selection/Exit 信号生成、诊断指标。"""
from __future__ import annotations

import bisect
import random
import numpy as np
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.chan import Signal
from chanlun_trader.tdx_data import TdxData, list_a_stocks


def _add_days(calendar, date, n):
    idx = bisect.bisect_right(calendar, date) - 1
    if idx < 0:
        return None
    out = idx + n
    return calendar[out] if out < len(calendar) else None


def exit_sig(calendar, d, hold_days, code):
    ex = _add_days(calendar, d, hold_days)
    if ex is None:
        return []
    return [Signal(code=code, signal_date=ex, direction="sell", signal_type="SX",
                   price_ref=0.0, stop_low=0.0, score=0.0)]


def build_universe_data(tdx, cfg, start, end, top_n=500, amount_lookback=250):
    """构建 PIT Universe + raw dfs + 市场状态。"""
    bench = tdx.get_benchmark("sh000001")
    calendar = [int(d) for d in bench["date"] if int(start.replace("-", "")) <= int(d) <= int(end.replace("-", ""))]
    stocks = list_a_stocks(tdx.vipdoc)
    amount_trail = {}
    for st in stocks:
        raw = tdx.get_day(st["code"], st["market"])
        if raw.empty or len(raw) < 120:
            continue
        raw = raw.sort_values("date")
        dates = raw["date"].astype(np.int64)
        amount_trail[st["code"]] = pd.Series(raw["amount"].rolling(amount_lookback).mean().to_numpy(), index=dates)
    uni_sets = {}
    union = set()
    for d in calendar:
        vals = []
        for code, ser in amount_trail.items():
            pos = ser.index.searchsorted(d, side="right") - 1
            if pos < 0:
                continue
            v = ser.iloc[pos]
            if pd.notna(v):
                vals.append((code, float(v)))
        vals.sort(key=lambda x: -x[1])
        top = {c for c, v in vals[:top_n]}
        uni_sets[d] = top
        union |= top
    dfs = {}
    code_market = {s["code"]: s["market"] for s in stocks}
    for code in union:
        raw = tdx.get_day(code, code_market[code])
        if raw.empty:
            continue
        dfs[code] = raw.sort_values("date").reset_index(drop=True)
    idx_close = pd.Series(bench["close"].to_numpy(), index=bench["date"].astype(np.int64))
    return calendar, uni_sets, sorted(union), dfs, idx_close


def find_first_full_day(calendar, uni_sets, min_n=500):
    for d in calendar:
        if len(uni_sets.get(d, set())) >= min_n:
            return d
    return None


def timing_signals(calendar, pool_codes, expo_map, pool_date=None):
    """只在曝光状态切换点生成全池买卖信号（二进制或任意正曝光）。

    pool_date 为固定股票池生效日；该日之前不发出任何信号，避免被 PIT 股票池过滤。
    """
    out = []
    prev = None
    for d in calendar:
        if pool_date is not None and int(d) < int(pool_date):
            continue
        e = expo_map.get(int(d), 0.0)
        if e > 0 and (prev is None or prev <= 0):
            for code in pool_codes:
                out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type="TIMING",
                                  price_ref=0.0, stop_low=0.0, score=1.0))
        elif e <= 0 and prev is not None and prev > 0:
            for code in pool_codes:
                out.append(Signal(code=code, signal_date=int(d), direction="sell", signal_type="TIMING_EXIT",
                                  price_ref=0.0, stop_low=0.0, score=1.0))
        prev = e
    return out

# ---------------------------------------------------------------- Selection 信号

def selection_signals(calendar, uni_sets, dfs, strategy_id, hold_days=20, max_picks=10, seed=42):
    """每日从 PIT 股票池选 top-N，生成买入信号（固定持有期退出交给引擎 max_holding_days）。"""
    out = []
    rng = random.Random(seed)
    # 预计算每只股票的时序数组，避免逐日切片
    pre = {}
    for code, df in dfs.items():
        if df.empty:
            continue
        close = df["close"]
        volume = df["volume"]
        amount = df["amount"]
        dates = df["date"].to_numpy()
        close_arr = close.to_numpy()
        ret1 = close.pct_change(1)
        vol20 = ret1.rolling(20).std()
        ma60 = close.rolling(60).mean()
        v5 = volume.rolling(5).mean()
        v20 = volume.rolling(20).mean()
        v60 = volume.rolling(60).mean()
        mom20 = close / close.shift(20) - 1
        mom60 = close / close.shift(60) - 1
        pre[code] = {
            "dates": dates, "close": close_arr, "amount": amount.to_numpy(),
            "vol20": vol20.to_numpy(), "ma60": ma60.to_numpy(),
            "v5": v5.to_numpy(), "v20": v20.to_numpy(), "v60": v60.to_numpy(),
            "mom20": mom20.to_numpy(), "mom60": mom60.to_numpy(),
        }
    for d in calendar:
        members = uni_sets.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            p = pre.get(code)
            if p is None:
                continue
            pos = np.searchsorted(p["dates"], d, side="right") - 1
            if pos < 20:
                continue
            if int(p["dates"][pos]) != int(d):
                continue
            c = float(p["close"][pos])
            amount = float(p["amount"][pos])
            mom20 = p["mom20"][pos]
            mom60 = p["mom60"][pos]
            vol20 = p["vol20"][pos]
            ma60 = p["ma60"][pos]
            v20 = p["v20"][pos]
            v60 = p["v60"][pos]
            if strategy_id == "SEL_RANDOM":
                score = rng.random()
            elif strategy_id == "SEL_MOM20":
                score = mom20
            elif strategy_id == "SEL_MOM60":
                score = mom60
            elif strategy_id == "SEL_RS120":
                score = mom60 - 0.5 * mom20
            elif strategy_id == "SEL_TRENDQ":
                if c <= ma60:
                    continue
                score = mom20
            elif strategy_id == "SEL_LOWVOL":
                score = -vol20 if pd.notna(vol20) else np.nan
            elif strategy_id == "SEL_LIQUIDITY":
                score = amount
            elif strategy_id == "SEL_VOLTREND":
                score = v20 / v60 if v60 > 0 else np.nan
            elif strategy_id == "SEL_VOLADJMOM":
                score = mom20 / vol20 if pd.notna(vol20) and vol20 > 0 else np.nan
            elif strategy_id == "SEL_MULTIHORIZON":
                score = 0.5 * mom20 + 0.5 * mom60
            elif strategy_id == "SEL_STRENGTH":
                score = mom60 / (1.0 + vol20) if pd.notna(vol20) else np.nan
            else:
                score = np.nan
            if pd.isna(score):
                continue
            rows.append((code, score, c, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, score, c, pos in rows[:max_picks]:
            out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type=strategy_id,
                              price_ref=c, stop_low=0.0, score=float(score)))
    return out


# ---------------------------------------------------------------- Exit 信号（固定 Entry = SEL_MOM20）

def entry_mom20_signals(calendar, uni_sets, dfs, max_picks=10):
    """固定 Entry：20 日动量 top10，仅买信号。"""
    out = []
    for d in calendar:
        members = uni_sets.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            df = dfs.get(code)
            if df is None or df.empty:
                continue
            pos = df["date"].searchsorted(d, side="right") - 1
            if pos < 20:
                continue
            if int(df.iloc[pos]["date"]) != int(d):
                continue
            c = float(df.iloc[pos]["close"])
            mom20 = float(c / df.iloc[pos - 20]["close"] - 1)
            rows.append((code, mom20, c, pos))
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1])
        for code, mom20, c, pos in rows[:max_picks]:
            out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type="ENTRY_MOM20",
                              price_ref=c, stop_low=0.0, score=mom20))
    return out


def exit_signals_for_buys(calendar, dfs, buy_signals, mode):
    """为给定 Entry 生成对应 Exit 卖出信号。

    mode:
      FIXED_5 / FIXED_10 / FIXED_20 / FIXED_60 / NONE
      SIGNAL_MA10 / SIGNAL_MA20
      HYBRID_20_MA10
    """
    out = []
    selected_codes = sorted({s.code for s in buy_signals})
    code_pos = {c: None for c in selected_codes}
    if mode.startswith("FIXED_"):
        # 固定持有期退出交给引擎 max_holding_days 处理，避免陈旧卖出单误卖重新买入的持仓。
        return out
    if mode == "NONE":
        return out
    if mode in ("SIGNAL_MA10", "SIGNAL_MA20", "HYBRID_20_MA10"):
        period = 10 if "MA10" in mode else 20
        for d in calendar:
            for code in selected_codes:
                df = dfs.get(code)
                if df is None or df.empty:
                    continue
                pos = df["date"].searchsorted(d, side="right") - 1
                if pos < period:
                    continue
                if int(df.iloc[pos]["date"]) != int(d):
                    continue
                c = float(df.iloc[pos]["close"])
                ma = float(df["close"].iloc[max(0, pos - period + 1): pos + 1].mean())
                if c < ma:
                    out.append(Signal(code=code, signal_date=int(d), direction="sell", signal_type="SX",
                                      price_ref=0.0, stop_low=0.0, score=0.0))
        # HYBRID_20_MA10 的 20 日固定部分由调用方设置 max_holding_days=20 实现。
        return out
    return out


def exit_atr_stop_signals(buy_signals, dfs, atr_period=20, mult=3.0):
    """ATR 止损：在 buy signal 上设置 stop_low = close - mult*ATR。"""
    out = []
    for s in buy_signals:
        df = dfs.get(s.code)
        if df is None or df.empty:
            out.append(s)
            continue
        pos = df["date"].searchsorted(s.signal_date, side="right") - 1
        if pos < atr_period:
            out.append(s)
            continue
        h = df["high"].iloc[max(0, pos - atr_period + 1): pos + 1]
        l = df["low"].iloc[max(0, pos - atr_period + 1): pos + 1]
        c = df["close"].iloc[max(0, pos - atr_period + 1): pos + 1]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = float(tr.mean())
        stop = s.price_ref - mult * atr
        out.append(Signal(code=s.code, signal_date=s.signal_date, direction=s.direction,
                          signal_type=s.signal_type, price_ref=s.price_ref, stop_low=stop, score=s.score))
    return out

# ---------------------------------------------------------------- 诊断统计

def diagnostic_from_trades(trades, equity_curve=None):
    """统计止损成本、whipsaw、re-entry、平均亏损等。"""
    n = len(trades)
    stop_trades = [t for t in trades if getattr(t, "sell_reason", "") in ("stop_loss", "trailing_stop")]
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    stop_loss_cost = sum(t.pnl for t in stop_trades)
    # 对每个 code，按时间排序，统计 sell 后 N 日内再次买入次数
    buy_sells = {}
    for t in trades:
        buy_sells.setdefault(t.code, []).append((t.buy_date, t.sell_date, t.sell_reason, t.pnl))
    reentry_count = 0
    whipsaw_count = 0
    for code, events in buy_sells.items():
        events.sort(key=lambda x: x[0])
        for i in range(1, len(events)):
            prev_sell = events[i - 1][1]
            buy = events[i][0]
            if prev_sell is not None and buy > prev_sell:
                reentry_count += 1
                if events[i - 1][2] in ("stop_loss", "trailing_stop") and (buy - prev_sell) <= 10:
                    whipsaw_count += 1
    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0
    gross_ret = float(np.prod([1 + t.pnl / (t.shares * t.buy_price) for t in trades])) - 1 if trades else 0.0
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses)) if losses and sum(t.pnl for t in losses) != 0 else float("inf") if wins else 0.0,
        "stop_loss_count": len(stop_trades),
        "stop_loss_cost": stop_loss_cost,
        "reentry_count": reentry_count,
        "whipsaw_count": whipsaw_count,
        "gross_ret_est": gross_ret,
        "avg_holding_days": float(np.mean([t.holding_days for t in trades])) if trades else 0.0,
    }
