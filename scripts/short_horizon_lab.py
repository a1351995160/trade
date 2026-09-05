"""SHORT-HORIZON STRATEGY LAB.

短周期 Alpha 实验室：在冻结全引擎（PositionExposureRunner）上批量测试
平均持仓 3~10 天的短周期策略。只使用 TRAIN（2022-08-01~2024-07-31）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.chan import Signal
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.tdx_data import TdxData
from run_lowvol_qfq_confirm import build_universe_qfq

TRAIN = ("2022-08-01", "2024-07-31")
VALIDATION = ("2024-08-01", "2025-07-31")
N_POS = 10


def prep_data(cfg, tdx, period=TRAIN):
    cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1])
    # 每个 code 的 numpy 序列
    pre = {}
    for code, df in dfs.items():
        if df.empty or "qfq_close" not in df.columns:
            continue
        dates = df["date"].astype(np.int64).to_numpy()
        close = df["qfq_close"].to_numpy(dtype=float)
        open_ = df["qfq_open"].to_numpy(dtype=float)
        high = df["qfq_high"].to_numpy(dtype=float)
        low = df["qfq_low"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        amt = df["amount"].to_numpy(dtype=float)
        pre[code] = (dates, open_, high, low, close, vol, amt)
    return cal, uni, union, pre


def backtest_config(cfg, period, hold, topn=N_POS, cost_mult=1.0, slip_mult=1.0):
    s, e = period
    bc = dict(cfg["backtest"])
    bc.update(start=s, end=e, stop_loss_pct=0.0, max_holding_days=hold,
              max_positions=topn, max_picks_per_day=0, max_per_industry_per_day=0,
              commission_rate=0.00025 * cost_mult, stamp_tax_rate=0.0005 * cost_mult,
              slippage=0.001 * slip_mult)
    cfg["backtest"] = bc
    cfg["trailing_stop"] = {"enabled": False}
    cfg["index_filter"] = {"enabled": False}
    cfg["universe"] = {"amount_top_n": 500, "amount_lookback": 250, "min_amount_ma20": 0}


def run_one(tdx, cfg, cal, uni, union, pre, sigs, period, hold, cost_mult=1.0, slip_mult=1.0, topn=N_POS):
    backtest_config(cfg, period, hold, topn=topn, cost_mult=cost_mult, slip_mult=slip_mult)
    cfg["__universe_sets__"] = uni
    cfg["__exposure_by_date__"] = {int(d): 1.0 for d in cal}
    r = PositionExposureRunner(tdx, cfg, sigs, universe_codes=union).run()
    m = compute_metrics(r, tdx.get_benchmark("sh000300"))
    closed = [t for t in r["trades"] if t.sell_reason != "open"]
    # 持有/换手统计
    avg_hold = float(np.mean([t.holding_days for t in closed])) if closed else 0.0
    med_hold = float(np.median([t.holding_days for t in closed])) if closed else 0.0
    years = 2.0 if period == TRAIN else 1.0
    buy_val = sum(t.shares * t.buy_price for t in r["trades"])
    eq = pd.Series([e for d, e in r["equity_curve"]]); eq.index = pd.to_datetime([str(d) for d, e in r["equity_curve"]], format="%Y%m%d")
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    avg_eq = float(eq.mean()) if len(eq) else 1.0
    turn = buy_val / avg_eq / years if avg_eq > 0 else 0.0
    trades_year = len(closed) / years if closed else 0.0
    cost_drag = turn * (0.00025 + 0.0005 + 0.001)  # 佣金+印花+滑点 单边近似
    return m, avg_hold, med_hold, turn, trades_year, cost_drag, r


def emit_buy(rows, sig_type, d, score_mult=1.0, max_n=10):
    out = []
    for code, score, c, pos in rows[:max_n]:
        out.append(Signal(code=code, signal_date=int(d), direction="buy", signal_type=sig_type,
                          price_ref=c, stop_low=0.0, score=score * score_mult))
    return out


def ma(arr, n, pos):
    if pos < n - 1:
        return np.nan
    return float(np.mean(arr[pos - n + 1:pos + 1]))


def roc(arr, pos, n):
    if pos < n:
        return np.nan
    return float(arr[pos] / arr[pos - n] - 1)


def sig_pullback(cal, uni, pre, ma_fast=5, ma_mid=20, ma_slow=60,
                 pull_min=0.03, pull_max=0.10, vol_shrink=0.85, vol_lb=20,
                 re_strength=True, hold=5, topn=10):
    """强势股回调：中期向上 + 短期回调 + 缩量 + 转强。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < ma_slow + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            m20 = ma(close, ma_mid, pos); m60 = ma(close, ma_slow, pos)
            if np.isnan(m20) or np.isnan(m60):
                continue
            if not (c > m20 > m60):
                continue
            hi10 = float(np.max(high[pos - 10:pos + 1]))
            pull = c / hi10 - 1
            if not (pull <= -pull_min and pull >= -pull_max):
                continue
            vma = float(np.mean(amt[pos - vol_lb + 1:pos + 1]))
            if vma <= 0 or amt[pos] > vol_shrink * vma:
                continue
            if re_strength and not (c > open_[pos] and c > close[pos - 1]):
                continue
            rows.append((code, -pull, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"PB_f{ma_fast}_m{ma_mid}_s{ma_slow}_p{pull_min}_{pull_max}_v{vol_shrink}", d))
    return out


def sig_oversold(cal, uni, pre, trend_lb=60, drop_lb=5, drop_min=0.07,
                 pull_hi_lb=10, pull_min=0.10, vol_ratio_max=1.5, hold=5, topn=10):
    """短期超跌反弹：中期趋势未破坏 + 个股短期异常下跌 + 未放量崩跌。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < trend_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            drop = roc(close, pos, drop_lb)
            if np.isnan(drop) or drop > -drop_min:
                continue
            hi10 = float(np.max(high[pos - pull_hi_lb + 1:pos + 1]))
            pull = c / hi10 - 1
            if pull > -pull_min:
                continue
            vma = float(np.mean(amt[pos - 19:pos + 1]))
            if vma > 0 and amt[pos] > vol_ratio_max * vma:
                continue
            rows.append((code, drop, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"OS_t{trend_lb}_d{drop_lb}_{drop_min}_v{vol_ratio_max}", d))
    return out


def sig_breakout(cal, uni, pre, bk_lb=20, vol_mult=1.5, vol_lb=20,
                 confirm_up=True, hold=5, topn=10):
    """放量突破后的次日确认（信号在突破次日发出）。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < bk_lb + 2:
                continue
            if int(dates[pos]) != int(d):
                continue
            # 昨日突破此前 N 日高点（不含昨日）
            prev_hi = float(np.max(high[pos - bk_lb - 1:pos - 1]))
            if high[pos - 1] <= prev_hi:
                continue
            vma = float(np.mean(amt[pos - vol_lb:pos]))
            if vma <= 0 or amt[pos - 1] <= vol_mult * vma:
                continue
            if confirm_up and not (close[pos] > open_[pos]):
                continue
            c = close[pos]
            score = float(amt[pos - 1] / vma)
            rows.append((code, score, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"BO_b{bk_lb}_v{vol_mult}", d))
    return out


def sig_cs_rank(cal, uni, pre, lookback=5, mode="rs", vol_lb=20, hold=5, topn=10):
    """短周期横截面排名：5D/10D RS、波动率调整强度。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < lookback + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            ret = roc(close, pos, lookback)
            if np.isnan(ret):
                continue
            if mode == "rs":
                score = ret
            elif mode == "voladj":
                r = np.diff(close[pos - vol_lb:pos + 1]) / close[pos - vol_lb:pos]
                sd = float(np.std(r)) if len(r) > 1 else 1.0
                score = ret / sd if sd > 0 else 0.0
            else:
                score = ret
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"CS_{mode}{lookback}", d))
    return out


def sig_cs_rev(cal, uni, pre, lookback=5, vol_max=0.0, vol_lb=20, hold=5, topn=10):
    """短周期横截面反转：买短期最弱（负收益最大），可用波动率上限排除崩盘股。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < lookback + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            ret = roc(close, pos, lookback)
            if np.isnan(ret):
                continue
            score = -ret  # 最弱优先
            if vol_max > 0:
                r = np.diff(close[pos - vol_lb:pos + 1]) / close[pos - vol_lb:pos]
                sd = float(np.std(r)) if len(r) > 1 else 1.0
                if sd > vol_max:
                    continue
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"CSR_{lookback}_v{vol_max}", d))
    return out


def sig_gap_rev(cal, uni, pre, gap_min=0.03, gap_max=0.10, trend_lb=60,
                stabilize=True, hold=5, topn=10):
    """跳空缺口反转：中期趋势向上，昨日向下跳空，今日企稳（低开高走或收盘>开盘）。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < trend_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            if pos < 1:
                continue
            gap = open_[pos - 1] / close[pos - 2] - 1 if pos >= 2 else 0.0
            if not (gap <= -gap_min and gap >= -gap_max):
                continue
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or close[pos] <= m60:
                continue
            if stabilize and not (close[pos] > open_[pos]):
                continue
            score = -gap
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"GAPR_{gap_min}_{gap_max}", d))
    return out


def sig_gap_cont(cal, uni, pre, gap_min=0.03, gap_max=0.12, hold=5, topn=10):
    """跳空缺口延续：向上跳空且放量，次日不回落。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < 22:
                continue
            if int(dates[pos]) != int(d):
                continue
            gap = open_[pos - 1] / close[pos - 2] - 1 if pos >= 2 else 0.0
            if not (gap >= gap_min and gap <= gap_max):
                continue
            vma = float(np.mean(amt[pos - 21:pos - 1]))
            if vma <= 0 or amt[pos - 1] <= 1.3 * vma:
                continue
            if close[pos] <= open_[pos - 1]:
                continue
            score = gap
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"GAPC_{gap_min}_{gap_max}", d))
    return out


def sig_pv_strength(cal, uni, pre, amt_mult=2.0, ret_lb=5, ret_min=0.03,
                    vol_lb=20, hold=5, topn=10):
    """价量强度：短期正收益 + 今日显著放量 + 收阳。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < ret_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            ret = roc(close, pos, ret_lb)
            if np.isnan(ret) or ret < ret_min:
                continue
            if not (close[pos] > open_[pos]):
                continue
            vma = float(np.mean(amt[pos - vol_lb + 1:pos + 1]))
            if vma <= 0 or amt[pos] <= amt_mult * vma:
                continue
            score = float(amt[pos] / vma) + ret * 10.0
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"PVS_a{amt_mult}_r{ret_min}", d))
    return out


def sig_lower_shadow(cal, uni, pre, drop_lb=5, drop_min=0.04, shadow_min=0.015,
                     trend_lb=60, hold=5, topn=10):
    """长下影反转：短期下跌 + 今日长下影 + 中期趋势未破坏。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < trend_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            ret = roc(close, pos, drop_lb)
            if np.isnan(ret) or ret > -drop_min:
                continue
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or close[pos] <= m60:
                continue
            rng = high[pos] - low[pos]
            if rng <= 0:
                continue
            lower = min(open_[pos], close[pos]) - low[pos]
            if lower / rng < 0.5:
                continue
            if lower < shadow_min * close[pos]:
                continue
            if not (close[pos] > open_[pos]):
                continue
            score = float(lower / rng)
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LSH_d{drop_min}_s{shadow_min}", d))
    return out


def sig_first_pullback(cal, uni, pre, high_lb=60, recent_hi_days=5, pull_min=0.02,
                       pull_max=0.06, trend_lb=60, hold=5, topn=10):
    """新高后的第一次回踩：近期创 N 日新高，当前小幅回落。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < high_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            # 过去 recent_hi_days 内是否创 high_lb 新高
            win_start = pos - recent_hi_days
            if win_start < 0:
                continue
            recent_hi = float(np.max(high[win_start:pos + 1]))
            prior_hi = float(np.max(high[pos - high_lb:win_start])) if win_start > pos - high_lb else 0.0
            if recent_hi <= prior_hi:
                continue
            pull = c / recent_hi - 1
            if not (pull <= -pull_min and pull >= -pull_max):
                continue
            rows.append((code, -pull, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"FP_h{high_lb}_r{recent_hi_days}_p{pull_min}_{pull_max}", d))
    return out


def sig_tight_breakout(cal, uni, pre, range_lb=10, range_max=0.05, vol_mult=1.5,
                       vol_lb=20, trend_lb=60, hold=5, topn=10):
    """缩量整理后突破：近期振幅收窄 + 今日放量突破整理区间高点。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < range_lb + vol_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            rng_hi = float(np.max(high[pos - range_lb:pos]))
            rng_lo = float(np.min(low[pos - range_lb:pos]))
            rng = (rng_hi - rng_lo) / rng_lo if rng_lo > 0 else 1.0
            if rng > range_max:
                continue
            # 今日突破整理区间高点
            if c <= rng_hi or high[pos] <= rng_hi:
                continue
            vma = float(np.mean(amt[pos - vol_lb + 1:pos + 1]))
            if vma <= 0 or amt[pos] <= vol_mult * vma:
                continue
            rows.append((code, float(amt[pos] / vma), c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"TB_r{range_max}_v{vol_mult}", d))
    return out


def sig_intraday(cal, uni, pre, lookback=5, hold=5, topn=10):
    """日内收益（收盘/开盘-1）5 日累计排名。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < lookback + 2:
                continue
            if int(dates[pos]) != int(d):
                continue
            acc = 0.0
            for k in range(lookback):
                j = pos - k
                if open_[j] <= 0:
                    acc = np.nan
                    break
                acc += close[j] / open_[j] - 1
            if np.isnan(acc):
                continue
            rows.append((code, acc, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"INTRA_{lookback}", d))
    return out


def sig_overnight(cal, uni, pre, lookback=5, hold=5, topn=10):
    """隔夜收益（今开/昨收-1）5 日累计排名。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < lookback + 2:
                continue
            if int(dates[pos]) != int(d):
                continue
            acc = 0.0
            for k in range(lookback):
                j = pos - k
                if close[j - 1] <= 0:
                    acc = np.nan
                    break
                acc += open_[j] / close[j - 1] - 1
            if np.isnan(acc):
                continue
            rows.append((code, acc, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"ON_{lookback}", d))
    return out


def sig_streak(cal, uni, pre, streak=4, ret_lb=5, ret_min=0.04, amt_mult=1.2,
               hold=5, topn=10):
    """连续强势：连续 N 日上涨 + 短期收益达标 + 量能不萎缩。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < streak + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            if c <= close[pos - 1]:
                continue
            ok = True
            for k in range(streak):
                j = pos - k
                if close[j] <= close[j - 1]:
                    ok = False
                    break
            if not ok:
                continue
            ret = roc(close, pos, ret_lb)
            if np.isnan(ret) or ret < ret_min:
                continue
            vma = float(np.mean(amt[pos - 19:pos + 1]))
            if vma <= 0 or amt[pos] < amt_mult * vma:
                continue
            rows.append((code, ret, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"STREAK{streak}_r{ret_min}", d))
    return out


def sig_obv(cal, uni, pre, obv_lb=5, norm_lb=20, hold=5, topn=10):
    """OBV 资金流加速：5 日 OBV 变化 / 20 日 OBV 标准差。"""
    out = []
    obv_cache = {}
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            if code not in obv_cache:
                dates, open_, high, low, close, vol, amt = item
                direction = np.sign(np.diff(close))
                obv = np.zeros(len(close)); 
                obv[1:] = np.cumsum(direction * vol[1:])
                obv_cache[code] = (dates, close, obv)
            dates, close, obv = obv_cache[code]
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < norm_lb + 5:
                continue
            if int(dates[pos]) != int(d):
                continue
            chg = float(obv[pos] - obv[pos - obv_lb])
            sd = float(np.std(np.diff(obv[pos - norm_lb:pos + 1]))) if pos > norm_lb else 0.0
            score = chg / sd if sd > 0 else 0.0
            rows.append((code, score, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"OBV_{obv_lb}_{norm_lb}", d))
    return out


def sig_limitup_cont(cal, uni, pre, ret_min=0.095, hold=5, topn=10, max_gap_next=0.03):
    """涨停次日延续：昨日涨停（涨幅>ret_min 且收在最高），今日未大幅高开低走。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < 3:
                continue
            if int(dates[pos]) != int(d):
                continue
            y_ret = close[pos - 1] / close[pos - 2] - 1 if close[pos - 2] > 0 else 0.0
            if not (y_ret >= ret_min and close[pos - 1] >= high[pos - 1] * 0.995):
                continue
            # 今日未大跌：开盘不高于昨收太多且今日收盘不崩
            if open_[pos] > close[pos - 1] * (1 + max_gap_next + 0.03):
                continue
            if close[pos] < open_[pos] * 0.97:
                continue
            rows.append((code, y_ret, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LU_CONT_r{ret_min}", d))
    return out


def sig_limitup_pullback(cal, uni, pre, ret_min=0.095, wait=2, pull_min=0.02,
                         pull_max=0.08, hold=5, topn=10):
    """涨停后回踩：涨停发生 wait 日后，价格从涨停后高点回落 pull 区间，且未破涨停日收盘。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < wait + 3:
                continue
            if int(dates[pos]) != int(d):
                continue
            lu = pos - wait - 1  # 涨停日
            if lu < 1:
                continue
            lu_ret = close[lu] / close[lu - 1] - 1 if close[lu - 1] > 0 else 0.0
            if not (lu_ret >= ret_min and close[lu] >= high[lu] * 0.995):
                continue
            hi_after = float(np.max(high[lu + 1:pos + 1]))
            pull = close[pos] / hi_after - 1
            if not (pull <= -pull_min and pull >= -pull_max):
                continue
            if close[pos] < close[lu] * 0.98:
                continue
            rows.append((code, -pull, close[pos], pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LU_PB_w{wait}_{pull_min}_{pull_max}", d))
    return out


def _vol_daily(close, lb, pos):
    if pos < lb + 1:
        return np.nan
    r = np.diff(close[pos - lb:pos + 1]) / close[pos - lb:pos]
    return float(np.std(r))


def _lv_filter(members, pre, pos_map, vol_lb=20, top_frac=0.5):
    """返回低波动股票代码集合（按 vol 排序取前 top_frac 比例）。"""
    arr = []
    for code in members:
        item = pre.get(code)
        if item is None:
            continue
        dates, open_, high, low, close, vol, amt = item
        pos = pos_map.get(code)
        if pos is None or pos < vol_lb + 1:
            continue
        v = _vol_daily(close, vol_lb, pos)
        if np.isnan(v):
            continue
        arr.append((code, v, pos))
    arr.sort(key=lambda x: x[1])
    k = max(1, int(len(arr) * top_frac))
    return arr[:k], arr


def sig_lv_pullback(cal, uni, pre, vol_lb=20, top_frac=0.5, trend_lb=60,
                    drop_min=0.02, drop_max=0.10, turn_up=True, amt_shrink=0.9,
                    hold=5, topn=10):
    """B1 LowVol Pullback：低波股票短期回调后转强。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        pos_map = {}
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos >= 0 and int(dates[pos]) == int(d):
                pos_map[code] = pos
        lv, _ = _lv_filter(members, pre, pos_map, vol_lb=vol_lb, top_frac=top_frac)
        rows = []
        for code, _, pos in lv:
            item = pre[code]
            dates, open_, high, low, close, vol, amt = item
            if pos < trend_lb + 3:
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            drop = c / close[pos - 3] - 1 if close[pos - 3] > 0 else 0.0
            if not (-drop_max <= drop <= -drop_min):
                continue
            if turn_up and c <= open_[pos]:
                continue
            amt_ma5 = float(np.mean(amt[pos - 4:pos + 1]))
            amt_ma20 = float(np.mean(amt[pos - 19:pos + 1]))
            if amt_ma20 > 0 and amt[pos] > amt_shrink * amt_ma5:
                continue
            rows.append((code, -drop, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LVPB_{vol_lb}_{drop_min}", d))
    return out


def sig_lv_breakout(cal, uni, pre, vol_lb=20, top_frac=0.5, range_lb=10,
                    range_max=0.04, vol_mult=2.0, trend_lb=60, hold=5, topn=10):
    """B2 LowVol Breakout：低波股票 + 波动压缩 + 放量突破。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        pos_map = {}
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos >= 0 and int(dates[pos]) == int(d):
                pos_map[code] = pos
        lv, _ = _lv_filter(members, pre, pos_map, vol_lb=vol_lb, top_frac=top_frac)
        rows = []
        for code, _, pos in lv:
            item = pre[code]
            dates, open_, high, low, close, vol, amt = item
            if pos < range_lb + vol_lb + 3:
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            rng_hi = float(np.max(high[pos - range_lb:pos]))
            rng_lo = float(np.min(low[pos - range_lb:pos]))
            rng = (rng_hi - rng_lo) / rng_lo if rng_lo > 0 else 1.0
            if rng > range_max:
                continue
            if c <= rng_hi or high[pos] <= rng_hi:
                continue
            vma = float(np.mean(amt[pos - vol_lb + 1:pos + 1]))
            if vma <= 0 or amt[pos] <= vol_mult * vma:
                continue
            rows.append((code, float(amt[pos] / vma), c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LVBO_{range_max}_v{vol_mult}", d))
    return out


def sig_lv_rs(cal, uni, pre, vol_lb=20, top_frac=0.5, ret_lb=10,
              ret_min=0.0, trend_lb=60, hold=5, topn=10):
    """B3 LowVol Relative Strength：低波 + 短中期相对强势（价格高于 MA60 且 10 日收益非负）。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        pos_map = {}
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos >= 0 and int(dates[pos]) == int(d):
                pos_map[code] = pos
        lv, _ = _lv_filter(members, pre, pos_map, vol_lb=vol_lb, top_frac=top_frac)
        rows = []
        for code, _, pos in lv:
            item = pre[code]
            dates, open_, high, low, close, vol, amt = item
            if pos < trend_lb + 3:
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            ret = c / close[pos - ret_lb] - 1 if close[pos - ret_lb] > 0 else 0.0
            if ret < ret_min:
                continue
            rows.append((code, ret, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LVRS_{ret_lb}_{ret_min}", d))
    return out


def sig_lv_rev(cal, uni, pre, vol_lb=20, top_frac=0.5, drop_min=0.03,
               drop_max=0.12, idx_ma=20, trend_lb=60, hold=5, topn=10):
    """B4 LowVol Mean Reversion：低波 + 短期异常下跌 + 市场非 Risk-Off。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        pos_map = {}
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos >= 0 and int(dates[pos]) == int(d):
                pos_map[code] = pos
        lv, _ = _lv_filter(members, pre, pos_map, vol_lb=vol_lb, top_frac=top_frac)
        rows = []
        for code, _, pos in lv:
            item = pre[code]
            dates, open_, high, low, close, vol, amt = item
            if pos < trend_lb + 3:
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            drop = c / close[pos - 3] - 1 if close[pos - 3] > 0 else 0.0
            if not (-drop_max <= drop <= -drop_min):
                continue
            rows.append((code, -drop, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"LVREV_{drop_min}", d))
    return out



def sig_volcomp_breakout(cal, uni, pre, short_lb=10, long_lb=60, ratio_max=0.7,
                         range_max=0.04, vol_mult=2.0, trend_lb=60, hold=5,
                         topn=10):
    """B6 Volatility Compression：短期波动率 / 长期波动率 收缩后放量突破。"""
    out = []
    for d in cal:
        members = uni.get(int(d))
        if not members:
            continue
        rows = []
        for code in sorted(members):
            item = pre.get(code)
            if item is None:
                continue
            dates, open_, high, low, close, vol, amt = item
            pos = int(np.searchsorted(dates, d, side="right") - 1)
            if pos < long_lb + short_lb + 3:
                continue
            if int(dates[pos]) != int(d):
                continue
            c = close[pos]
            m60 = ma(close, trend_lb, pos)
            if np.isnan(m60) or c <= m60:
                continue
            vs = _vol_daily(close, short_lb, pos)
            vl = _vol_daily(close, long_lb, pos)
            if np.isnan(vs) or np.isnan(vl) or vl <= 0:
                continue
            ratio = vs / vl
            if ratio > ratio_max:
                continue
            rng_hi = float(np.max(high[pos - short_lb:pos]))
            rng_lo = float(np.min(low[pos - short_lb:pos]))
            rng = (rng_hi - rng_lo) / rng_lo if rng_lo > 0 else 1.0
            if rng > range_max:
                continue
            if c <= rng_hi or high[pos] <= rng_hi:
                continue
            vma = float(np.mean(amt[pos - 19:pos + 1]))
            if vma <= 0 or amt[pos] <= vol_mult * vma:
                continue
            rows.append((code, 1.0 - ratio, c, pos))
        rows.sort(key=lambda x: -x[1])
        out.extend(emit_buy(rows, f"VCBO_{ratio_max}", d))
    return out
