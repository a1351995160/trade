import sys, time, json
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
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
from short_horizon_lab import TRAIN
from v2_certification import sym, prep_from_dfs, v1_signals_to_v2, make_store_from_dfs, v2_metrics, LOWVOL, TB, TB_TOPN, run_v1, run_v2

cfg = load_config(); tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
stocks = list_a_stocks(tdx.vipdoc); code_market = {s["code"]: s["market"] for s in stocks}
period = TRAIN
t0=time.time(); cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1], top_n=500, amount_lookback=250)
print("universe", len(cal), len(union), time.time()-t0, flush=True)
pre = prep_from_dfs(dfs)
lv_sigs = lowvol_signals_qfq(cal, uni, dfs, vol_window=LOWVOL["vol_window"], top_n=LOWVOL["top_n"])

# V1
r1, m1 = run_v1(cfg, tdx, period, cal, uni, union, dfs, lv_sigs, LOWVOL["hold"], LOWVOL["top_n"])
v1_trades = [t for t in r1["trades"] if t.sell_reason != "open"]
print("V1 trades", len(v1_trades), "ret", m1["total_return"], flush=True)

# V2
store = make_store_from_dfs(union, dfs, code_market)
v2sigs = v1_signals_to_v2(lv_sigs, code_market)
us = UniverseService()
for d, codes in uni.items():
    us.load_pit_sets({int(d): {sym(c, code_market[c]) for c in codes}})
w = 1.0 / LOWVOL["top_n"]
ecfg = EngineConfig(initial_cash=1_000_000.0, max_positions=LOWVOL["top_n"], max_position_weight=w,
                    mode="DAILY", enable_index_filter=False, index_filter_enabled=False,
                    max_holding_days=LOWVOL["hold"])
eng = BacktestEngineV2(store, cal, config=ecfg, universe=us)
eng.add_signals(v2sigs)
t0=time.time(); res = eng.run(); print("V2 elapsed", time.time()-t0, flush=True)
print("V2 metrics", v2_metrics(res, 1_000_000.0), flush=True)

# order status counts
from collections import Counter
print("order status", Counter(o.status.name for o in res.orders.orders.values()), flush=True)
# rejected reasons from event log (OrderEvent has message)
reasons = Counter()
for ev in res.event_log._events:
    msg = getattr(ev, "message", None)
    status = getattr(ev, "order_status", None)
    if status is not None:
        reasons[(status.name if hasattr(status, 'name') else str(status), msg)] += 1
print("reject reasons top:")
for k, v in reasons.most_common(15):
    print("  ", k, v, flush=True)
print("buys", sum(1 for t in res.ledger.trades if t.side==Side.BUY), "sells", sum(1 for t in res.ledger.trades if t.side==Side.SELL), flush=True)
# first V1 trade
print("V1 first trades:", flush=True)
for t in v1_trades[:5]:
    print(t.code, t.buy_date, t.buy_price, t.shares, t.sell_date, t.sell_price, t.sell_reason, round(t.pnl,2), flush=True)
print("V2 first trades:", flush=True)
for t in res.ledger.trades[:8]:
    print(t.symbol, t.side, t.quantity, t.price, str(t.fill_time)[:10], t.order_id, t.position_id, round(t.realized_pnl,2), t.reality_flag, flush=True)
