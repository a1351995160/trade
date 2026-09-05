"""市场状态 PIT 计算与暴露规则。

所有状态只用当日及之前数据（Point-In-Time）。
输入 universe_sets: {date: set(codes)}，dfs: raw 日线。
输出 DataFrame：每行一个交易日，列为各状态变量。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_market_state(calendar, universe_sets, dfs, index_close: pd.Series) -> pd.DataFrame:
    """计算指数趋势、广度、涨跌比、新高新低、波动、回撤等 PIT 状态变量。"""
    idx = index_close.sort_index()
    idx_ma20 = idx.rolling(20).mean()
    idx_ma60 = idx.rolling(60).mean()
    idx_ret1 = idx.pct_change(1)
    idx_ret20 = idx.pct_change(20)
    idx_ret60 = idx.pct_change(60)
    idx_hh60 = idx.rolling(60).max()
    idx_vol20 = idx_ret1.rolling(20).std()

    rows = []
    # 预计算个股状态（raw 日线）
    code_breadth = {}
    code_new_high = {}
    code_new_low = {}
    code_ret1 = {}
    code_dates = {}
    code_close = {}
    for code, df in dfs.items():
        if df.empty:
            continue
        close = df["close"]
        ma20 = close.rolling(20).mean()
        hh120 = close.rolling(120).max()
        ll120 = close.rolling(120).min()
        code_close[code] = close.to_numpy()
        code_dates[code] = df["date"].to_numpy()
        code_breadth[code] = (close > ma20).to_numpy()
        code_new_high[code] = (close >= hh120).to_numpy()
        code_new_low[code] = (close <= ll120).to_numpy()
        code_ret1[code] = close.pct_change(1).to_numpy()

    for d in calendar:
        members = universe_sets.get(int(d))
        if not members:
            rows.append({
                "date": int(d),
                "idx_close": float(idx.get(int(d), np.nan)),
                "idx_ma20": float(idx_ma20.get(int(d), np.nan)),
                "idx_ma60": float(idx_ma60.get(int(d), np.nan)),
                "idx_ret20": float(idx_ret20.get(int(d), np.nan)),
                "idx_ret60": float(idx_ret60.get(int(d), np.nan)),
                "idx_dd60": float(idx.get(int(d), np.nan) / idx_hh60.get(int(d), np.nan) - 1 if pd.notna(idx_hh60.get(int(d), np.nan)) else np.nan),
                "idx_vol20": float(idx_vol20.get(int(d), np.nan)),
                "breadth_ma20": np.nan,
                "adv_ratio": np.nan,
                "new_high_ratio": np.nan,
                "new_low_ratio": np.nan,
            })
            continue
        n = 0
        b = 0
        adv = 0
        nh = 0
        nl = 0
        for code in members:
            dates = code_dates.get(code)
            if dates is None:
                continue
            pos = np.searchsorted(dates, d, side="right") - 1
            if pos < 0:
                continue
            if int(dates[pos]) != int(d):
                continue
            n += 1
            b += 1 if code_breadth[code][pos] else 0
            r1 = code_ret1[code][pos]
            if pd.notna(r1):
                adv += 1 if r1 > 0 else 0
            nh += 1 if code_new_high[code][pos] else 0
            nl += 1 if code_new_low[code][pos] else 0
        rows.append({
            "date": int(d),
            "idx_close": float(idx.get(int(d), np.nan)),
            "idx_ma20": float(idx_ma20.get(int(d), np.nan)),
            "idx_ma60": float(idx_ma60.get(int(d), np.nan)),
            "idx_ret20": float(idx_ret20.get(int(d), np.nan)),
            "idx_ret60": float(idx_ret60.get(int(d), np.nan)),
            "idx_dd60": float(idx.get(int(d), np.nan) / idx_hh60.get(int(d), np.nan) - 1 if pd.notna(idx_hh60.get(int(d), np.nan)) else np.nan),
            "idx_vol20": float(idx_vol20.get(int(d), np.nan)),
            "breadth_ma20": b / n if n else np.nan,
            "adv_ratio": adv / n if n else np.nan,
            "new_high_ratio": nh / n if n else np.nan,
            "new_low_ratio": nl / n if n else np.nan,
        })
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def exposure_from_rule(df: pd.DataFrame, rule: str) -> dict[int, float]:
    """根据规则生成 date -> exposure 映射。"""
    out: dict[int, float] = {}
    for _, r in df.iterrows():
        d = int(r["date"])
        if rule == "ALWAYS":
            out[d] = 1.0
        elif rule == "CASH":
            out[d] = 0.0
        elif rule == "IDX_MA20":
            out[d] = 1.0 if pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"] else 0.0
        elif rule == "IDX_MA60":
            out[d] = 1.0 if pd.notna(r["idx_close"]) and pd.notna(r["idx_ma60"]) and r["idx_close"] > r["idx_ma60"] else 0.0
        elif rule == "IDX_MOM20":
            out[d] = 1.0 if pd.notna(r["idx_ret20"]) and r["idx_ret20"] > 0 else 0.0
        elif rule == "IDX_MOM60":
            out[d] = 1.0 if pd.notna(r["idx_ret60"]) and r["idx_ret60"] > 0 else 0.0
        elif rule == "IDX_DD60":
            out[d] = 1.0 if pd.notna(r["idx_dd60"]) and r["idx_dd60"] > -0.10 else 0.0
        elif rule == "IDX_VOL20":
            out[d] = 1.0 if pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015 else 0.0
        elif rule == "BREADTH_MA20":
            out[d] = 1.0 if pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50 else 0.0
        elif rule == "BREADTH_ADV":
            out[d] = 1.0 if pd.notna(r["adv_ratio"]) and r["adv_ratio"] > 0.50 else 0.0
        elif rule == "COMPOSITE_TB":
            ok = (pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]) and \
                 (pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50)
            out[d] = 1.0 if ok else 0.0
        elif rule == "COMPOSITE_TV":
            ok = (pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]) and \
                 (pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015)
            out[d] = 1.0 if ok else 0.0
        elif rule == "COMPOSITE_BV":
            ok = (pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50) and \
                 (pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015)
            out[d] = 1.0 if ok else 0.0
        elif rule == "COMPOSITE_TBV":
            ok = (pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]) and \
                 (pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50) and \
                 (pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015)
            out[d] = 1.0 if ok else 0.0
        elif rule == "COMPOSITE_TBV_3S":
            trend = pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]
            breadth = pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50
            lowvol = pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015
            if trend and breadth and lowvol:
                out[d] = 1.0
            elif trend:
                out[d] = 0.5
            else:
                out[d] = 0.0
        elif rule == "COMPOSITE_TBV_100_30_0":
            trend = pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]
            breadth = pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50
            lowvol = pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015
            if trend and breadth and lowvol:
                out[d] = 1.0
            elif trend:
                out[d] = 0.3
            else:
                out[d] = 0.0
        elif rule == "COMPOSITE_TBV_100_50_20":
            trend = pd.notna(r["idx_close"]) and pd.notna(r["idx_ma20"]) and r["idx_close"] > r["idx_ma20"]
            breadth = pd.notna(r["breadth_ma20"]) and r["breadth_ma20"] > 0.50
            lowvol = pd.notna(r["idx_vol20"]) and r["idx_vol20"] < 0.015
            if trend and breadth and lowvol:
                out[d] = 1.0
            elif trend:
                out[d] = 0.5
            else:
                out[d] = 0.2
        else:
            out[d] = 1.0
    return out
