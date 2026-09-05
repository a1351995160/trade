"""M6 Discovery Round 1 shared helpers."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

TRAIN = (20220801, 20240731)
VALIDATION = (20240801, 20250731)
EXTREME = (20240924, 20241008)


def load_daily() -> pd.DataFrame:
    df = pd.read_parquet("data/research/daily_all.parquet")
    return df


def load_gbbq() -> pd.DataFrame:
    from chanlun_trader.config import load_config
    from chanlun_trader.tdx_data import TdxData
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    return tdx._load_gbbq()


def pit_return_series(raw: pd.DataFrame, g: pd.DataFrame) -> pd.Series:
    """PIT qfq daily return series。与 qfq_columns_asof(as_of=d) 的日收益一致。"""
    df = raw.sort_values("date").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    C = pd.Series(1.0, index=df.index)
    events = g[(g["category"] == 1)].sort_values("datetime")
    for _, ev in events.iterrows():
        D = int(ev["datetime"])
        prev = df[df["date"] < D]
        if prev.empty:
            continue
        pc = float(prev["close"].iloc[-1])
        hongli = float(ev["hongli_panqianliutong"]) / 10.0
        peigujia = float(ev["peigujia_qianzongguben"])
        songgu = float(ev["songgu_qianzongguben"]) / 10.0
        peigu = float(ev["peigu_houzongguben"]) / 10.0
        denom = pc * (1.0 + songgu + peigu)
        if denom <= 0:
            continue
        adj = (pc - hongli + peigujia * peigu) / denom
        if adj <= 0 or not np.isfinite(adj):
            continue
        C.loc[df["date"] >= D] *= adj
    return df["ret"] * C.shift(1) / C


def build_factor_table(daily: pd.DataFrame, gbbq: pd.DataFrame) -> pd.DataFrame:
    """每只股票日频特征表。日期 2022-08-01..2025-07-31（含训练+验证；只由 Guard 控制读取）。"""
    out_rows = []
    g_by_code = gbbq.groupby("code")
    for sym, raw in daily.groupby("symbol", sort=True):
        raw = raw.sort_values("date").reset_index(drop=True)
        if len(raw) < 130:
            continue
        code = sym.split(".")[0]
        g = g_by_code.get_group(code).copy() if code in g_by_code.groups else pd.DataFrame()
        r = pit_return_series(raw, g)
        close = raw["close"].to_numpy(dtype=float)
        open_ = raw["open"].to_numpy(dtype=float)
        high = raw["high"].to_numpy(dtype=float)
        low = raw["low"].to_numpy(dtype=float)
        vol = raw["volume"].to_numpy(dtype=float)
        amt = raw["amount"].to_numpy(dtype=float)
        prev_close = raw["prev_close"].to_numpy(dtype=float)
        dates = raw["date"].to_numpy(dtype=np.int64)

        r5 = pd.Series(r).rolling(5).apply(lambda x: (1+x).prod()-1, raw=True).to_numpy()
        r20 = pd.Series(r).rolling(20).apply(lambda x: (1+x).prod()-1, raw=True).to_numpy()
        vol20 = pd.Series(r).rolling(20).std(ddof=0).to_numpy()
        vol60 = pd.Series(r).rolling(60).std(ddof=0).to_numpy()
        vol_comp = np.where(vol60 > 0, vol20 / vol60, np.nan)
        hh20 = pd.Series(high).rolling(20).max().to_numpy()
        hc20 = pd.Series(close).rolling(20).max().to_numpy()
        dist_high20 = close / hh20 - 1.0
        dd20 = close / hc20 - 1.0
        ma20v = pd.Series(vol).rolling(20).mean().to_numpy()
        vol_ratio = vol / ma20v
        ma20a = pd.Series(amt).rolling(20).mean().to_numpy()
        amt_ratio = amt / ma20a
        gap = open_ / prev_close - 1.0
        rng = np.maximum(high - low, 1e-9)
        intraday_pos = (close - open_) / rng
        upper_shadow = (high - np.maximum(open_, close)) / rng
        ret1 = np.r_[np.nan, close[1:] / close[:-1] - 1.0]
        vol_chg = np.r_[np.nan, vol[1:] / vol[:-1]]
        vp_div = vol_ratio / (1.0 + pd.Series(r5).fillna(0).to_numpy())
        ret_std60 = pd.Series(ret1).rolling(60).std(ddof=0).to_numpy()
        close_chg5 = pd.Series(close).pct_change(5).to_numpy()
        consecutive_up = (close > np.r_[np.nan, close[:-1]]).astype(float)
        up5 = pd.Series(consecutive_up).rolling(5, min_periods=5).sum().to_numpy()
        # 5日上涨 + 近20日高点
        break_near_high = up5 * (dist_high20 > -0.03).astype(float)

        for i in range(len(raw)):
            d = int(dates[i])
            if d < TRAIN[0] or d > VALIDATION[1]:
                continue
            out_rows.append({
                "symbol": sym, "date": d,
                "ret": float(r.iloc[i]) if i > 0 else np.nan,
                "r5": float(r5[i]), "r20": float(r20[i]),
                "vol20": float(vol20[i]), "vol60": float(vol60[i]),
                "vol_comp": float(vol_comp[i]),
                "dist_high20": float(dist_high20[i]), "dd20": float(dd20[i]),
                "vol_ratio": float(vol_ratio[i]), "amt_ratio": float(amt_ratio[i]),
                "gap": float(gap[i]), "intraday_pos": float(intraday_pos[i]),
                "upper_shadow": float(upper_shadow[i]), "vp_div": float(vp_div[i]),
                "ret_std60": float(ret_std60[i]), "close_chg5": float(close_chg5[i]),
                "up5": float(up5[i]), "break_near_high": float(break_near_high[i]),
            })
    return pd.DataFrame(out_rows)


def load_industry_map() -> pd.DataFrame:
    con = sqlite3.connect("data/research_full.db")
    rows = []
    for _, _, payload in con.execute("SELECT snapshot_id, row_key, payload_json FROM industry_membership").fetchall():
        d = json.loads(payload)
        code = d.get("code")
        ind = d.get("industry_code")
        if code and ind:
            rows.append({"code": code, "industry_code": ind})
    con.close()
    return pd.DataFrame(rows).drop_duplicates("code")


def forward_returns(daily: pd.DataFrame, horizons=(1, 2, 3, 5, 7, 10)) -> pd.DataFrame:
    rows = []
    for sym, g in daily.groupby("symbol", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        close = g["close"].to_numpy(dtype=float)
        dates = g["date"].to_numpy()
        for i in range(len(g)):
            for h in horizons:
                j = i + h
                if j >= len(g) or close[i] <= 0:
                    continue
                rows.append({"symbol": sym, "timestamp": int(dates[i]), "horizon": h,
                             "future_return": float(close[j] / close[i] - 1.0),
                             "available_at": int(dates[j])})
    return pd.DataFrame(rows)
