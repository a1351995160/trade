"""V2 POST-REFACTOR CERTIFICATION — Frozen V1 signals replayed on V1 and V2."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from chanlun_trader.metrics import compute_metrics
from chanlun_trader.exposure_runner import PositionExposureRunner
from chanlun_trader.engine.asof import MarketDataStore
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import ExecutionPolicy, Side, Signal as V2Signal
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.engine.universe import UniverseService
from run_lowvol_qfq_confirm import build_universe_qfq, lowvol_signals_qfq
from short_horizon_lab import sig_tight_breakout, TRAIN, VALIDATION

OUT = Path("reports/certification_runs.json")
OUT.parent.mkdir(exist_ok=True)

TRAIN_D = ("2022-08-01", "2024-07-31")
VAL_D = ("2024-08-01", "2025-07-31")
LOWVOL = dict(vol_window=60, top_n=10, hold=25)
TB = dict(range_lb=10, range_max=0.04, vol_mult=2.0, hold=6)
TB_TOPN = 15


def sym(code, market):
    return f"{code}.{'SH' if market == 1 else 'SZ'}"


def v2_metrics(res: BacktestEngineV2, initial_cash: float) -> dict:
    snaps = res.ledger.snapshots
    if not snaps:
        return {"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "sharpe": 0.0, "avg_holding_days": 0.0,
                "turnover": 0.0, "final_equity": initial_cash}
    eq = pd.DataFrame([(s.timestamp.strftime("%Y%m%d"), s.equity) for s in snaps], columns=["date", "equity"])
    eq = eq.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    final_equity = float(eq.iloc[-1]["equity"])
    total_return = final_equity / initial_cash - 1
    cummax = eq["equity"].cummax()
    dd = float((eq["equity"] / cummax - 1).min())
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    wins = [t for t in sells if t.realized_pnl > 0]
    losses = [t for t in sells if t.realized_pnl <= 0]
    gp = sum(t.realized_pnl for t in wins)
    gl = abs(sum(t.realized_pnl for t in losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    daily_ret = eq["equity"].astype(float).pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0
    return {
        "total_return": float(total_return),
        "max_drawdown": float(dd),
        "trade_count": len(sells),
        "win_rate": len(wins) / len(sells) if sells else 0.0,
        "profit_factor": float(pf),
        "sharpe": float(sharpe),
        "avg_holding_days": 0.0,
        "turnover": float(res.ledger.turnover),
        "final_equity": float(final_equity),
    }

def make_store_from_dfs(union, dfs, code_market):
    store = MarketDataStore()
    for code in union:
        df = dfs.get(code)
        if df is None or df.empty:
            continue
        key = sym(code, code_market[code])
        raw = df.set_index("date")[["open", "high", "low", "close", "volume", "amount"]].copy()
        raw["prev_close"] = raw["close"].shift(1)
        store.add_daily_raw(key, raw)
        if "qfq_close" in df.columns:
            q = df.set_index("date")[["qfq_open", "qfq_high", "qfq_low", "qfq_close", "volume", "amount"]].copy()
            q = q.rename(columns={"qfq_open": "open", "qfq_high": "high", "qfq_low": "low", "qfq_close": "close"})
            store.add_daily_qfq(key, q)
        else:
            store.add_daily_qfq(key, raw[["open", "high", "low", "close", "volume", "amount"]].copy())
    return store


def prep_from_dfs(dfs):
    pre = {}
    for code, df in dfs.items():
        if df.empty or "qfq_close" not in df.columns:
            continue
        dates = df["date"].astype(np.int64).to_numpy()
        open_ = df["qfq_open"].to_numpy(dtype=float)
        high = df["qfq_high"].to_numpy(dtype=float)
        low = df["qfq_low"].to_numpy(dtype=float)
        close = df["qfq_close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        amt = df["amount"].to_numpy(dtype=float)
        pre[code] = (dates, open_, high, low, close, vol, amt)
    return pre


def v1_signals_to_v2(sigs, code_market):
    out = []
    for s in sigs:
        d = int(s.signal_date)
        y, m, dd = d // 10000, (d // 100) % 100, d % 100
        out.append(V2Signal(
            strategy_id=s.signal_type or "V1_FROZEN",
            signal_id=f"{s.signal_type}:{s.code}:{d}",
            symbol=sym(s.code, code_market[s.code]),
            generated_at=tz_aware(y, m, dd, 15, 0),
            direction=Side.BUY,
            score=float(s.score or 0.0),
            signal_type=s.signal_type,
            execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
        ))
    return out


def run_v1(cfg, tdx, period, cal, uni, union, dfs, sigs, hold, topn):
    bc = dict(cfg["backtest"])
    bc.update(start=period[0], end=period[1], stop_loss_pct=0.0, max_holding_days=hold,
              max_positions=topn, max_picks_per_day=0, max_per_industry_per_day=0,
              commission_rate=0.00025, min_commission=5.0,
              stamp_tax_rate=0.0005, slippage=0.001)
    cfg["backtest"] = bc
    cfg["trailing_stop"] = {"enabled": False}
    cfg["index_filter"] = {"enabled": False}
    cfg["universe"] = {"amount_top_n": 500, "amount_lookback": 250, "min_amount_ma20": 0}
    cfg["__universe_sets__"] = uni
    cfg["__exposure_by_date__"] = {int(d): 1.0 for d in cal}
    t0 = time.time()
    r = PositionExposureRunner(tdx, cfg, sigs, universe_codes=union).run()
    m = compute_metrics(r, tdx.get_benchmark("sh000300"))
    m["elapsed"] = time.time() - t0
    m["n_signals"] = len(sigs)
    m["n_orders_open"] = len([t for t in r["trades"] if t.sell_reason == "open"])
    return r, m


def run_v2(store, cal, uni, union, code_market, sigs, topn, initial_cash, max_hold, idx_close=None):
    us = UniverseService()
    for d, codes in uni.items():
        us.load_pit_sets({int(d): {sym(c, code_market[c]) for c in codes}})
    w = 1.0 / max(1, topn)
    cfg = EngineConfig(initial_cash=initial_cash, max_positions=topn, max_position_weight=w,
                       mode="DAILY", enable_index_filter=False, index_filter_enabled=False,
                       max_holding_days=max_hold)
    eng = BacktestEngineV2(store, cal, config=cfg, universe=us)
    eng.add_signals(sigs)
    t0 = time.time()
    res = eng.run()
    res.elapsed = time.time() - t0
    return res

def main():
    cfg = load_config()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
    stocks = list_a_stocks(tdx.vipdoc)
    code_market = {s["code"]: s["market"] for s in stocks}
    initial_cash = float(cfg["backtest"]["initial_cash"])
    report = {"runs": [], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    for pname, period in (("TRAIN", TRAIN_D), ("VALIDATION", VAL_D)):
        print(f"== build universe {pname} {period}", flush=True)
        t0 = time.time()
        cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1], top_n=500, amount_lookback=250)
        print(f"   calendar={len(cal)} union={len(union)} elapsed={time.time()-t0:.1f}s", flush=True)
        idx_closes = {int(d): float(v) for d, v in idx.items()}

        pre = prep_from_dfs(dfs)
        lv_sigs = lowvol_signals_qfq(cal, uni, dfs, vol_window=LOWVOL["vol_window"], top_n=LOWVOL["top_n"])
        tb_sigs = sig_tight_breakout(cal, uni, pre, range_lb=TB["range_lb"],
                                     range_max=TB["range_max"], vol_mult=TB["vol_mult"])
        print(f"   LOWVOL signals={len(lv_sigs)}  TB signals={len(tb_sigs)}", flush=True)

        for name, ss in (("LOWVOL", lv_sigs), ("TB", tb_sigs)):
            rows = [{"code": s.code, "signal_date": int(s.signal_date), "direction": s.direction,
                     "signal_type": s.signal_type, "score": float(s.score or 0.0)} for s in ss]
            pd.DataFrame(rows).to_parquet(Path(f"experiments/cert_signals_{name}_{pname}.parquet"), index=False)

        for name, ss, hold, topn in (
            ("LOWVOL_QFQ_V60_H25_N10", lv_sigs, LOWVOL["hold"], LOWVOL["top_n"]),
            ("TB_H6_N15", tb_sigs, TB["hold"], TB_TOPN),
        ):
            r, m = run_v1(cfg, tdx, period, cal, uni, union, dfs, ss, hold, topn)
            m["strategy"] = name; m["period"] = pname; m["engine"] = "V1"
            report["runs"].append(m)
            print(f"   V1 {name} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
                  f"sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} n={m['trade_count']} "
                  f"elapsed={m.get('elapsed',0):.1f}s", flush=True)

        print(f"   build V2 store {pname}", flush=True)
        t0 = time.time()
        store = make_store_from_dfs(union, dfs, code_market)
        print(f"   store symbols={len(store.symbols())} elapsed={time.time()-t0:.1f}s", flush=True)

        for name, ss, topn, hold in (
            ("LOWVOL_QFQ_V60_H25_N10", lv_sigs, LOWVOL["top_n"], LOWVOL["hold"]),
            ("TB_H6_N15", tb_sigs, TB_TOPN, TB["hold"]),
        ):
            v2sigs = v1_signals_to_v2(ss, code_market)
            res = run_v2(store, cal, uni, union, code_market, v2sigs, topn, initial_cash, hold, idx_closes)
            m = v2_metrics(res, initial_cash)
            m["strategy"] = name; m["period"] = pname; m["engine"] = "V2"
            m["elapsed"] = getattr(res, "elapsed", 0.0)
            m["n_signals"] = len(v2sigs)
            m["n_orders_open"] = 0
            report["runs"].append(m)
            print(f"   V2 {name} {pname}: ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
                  f"sharpe={m['sharpe']:.2f} pf={m['profit_factor']:.2f} n={m['trade_count']} "
                  f"elapsed={m.get('elapsed',0):.2f}s", flush=True)

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("SAVED", OUT, flush=True)


if __name__ == "__main__":
    main()
