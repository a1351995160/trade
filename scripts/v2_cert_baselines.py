import sys, time, random, json
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from pathlib import Path
from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from run_lowvol_qfq_confirm import build_universe_qfq
from short_horizon_lab import TRAIN
from v2_certification import sym, make_store_from_dfs
from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.universe import UniverseService
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy
from chanlun_trader.engine.time_types import tz_aware

cfg = load_config(); tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cfg["tdx"].get("cache_dir"))
stocks = list_a_stocks(tdx.vipdoc); code_market = {s["code"]: s["market"] for s in stocks}
period = TRAIN
t0=time.time(); cal, uni, union, dfs, idx = build_universe_qfq(tdx, cfg, period[0], period[1], top_n=500, amount_lookback=250)
print("universe", len(cal), len(union), round(time.time()-t0,1), flush=True)
store = make_store_from_dfs(union, dfs, code_market)
us = UniverseService()
for d, codes in uni.items():
    us.load_pit_sets({int(d): {sym(c, code_market[c]) for c in codes}})

def run_engine(sigs, label):
    ecfg = EngineConfig(initial_cash=1_000_000.0, max_positions=10, max_position_weight=0.10,
                        mode="DAILY", enable_index_filter=False, index_filter_enabled=False,
                        max_holding_days=60)
    eng = BacktestEngineV2(store, cal, config=ecfg, universe=us)
    eng.add_signals(sigs)
    res = eng.run()
    snaps = res.ledger.snapshots
    ret = snaps[-1].equity/1_000_000 - 1 if snaps else 0.0
    print(label, "ret", round(ret,6), "trades", len(res.ledger.trades), "orders", len(res.orders.orders), flush=True)
    return {"label": label, "ret": ret, "n_trades": len(res.ledger.trades), "n_orders": len(res.orders.orders)}

out = {}
# Cash
out["cash"] = run_engine([], "CASH")

# Buy & Hold: first trading day with >=10 universe members; take first 10 symbols
d0 = next((d for d in cal if len(uni.get(d, set())) >= 10), cal[0])
codes0 = sorted(uni.get(d0, set()))[:10]
sigs = []
for code in codes0:
    y,m,d = d0//10000, (d0//100)%100, d0%100
    sigs.append(Signal(strategy_id="BH", signal_id=f"BH:{code}", symbol=sym(code, code_market[code]),
                       generated_at=tz_aware(y,m,d,15,0), direction=Side.BUY,
                       execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
out["buy_hold_top10"] = run_engine(sigs, "BUY_HOLD_TOP10")

# Random: 1000 random signals with fixed seed
rng = random.Random(42)
all_codes = sorted(union)
sigs = []
for i in range(1000):
    code = rng.choice(all_codes)
    di = rng.randrange(0, len(cal)-5)
    d0 = cal[di]
    y,m,d = d0//10000, (d0//100)%100, d0%100
    sigs.append(Signal(strategy_id="RND", signal_id=f"RND:{i}", symbol=sym(code, code_market[code]),
                       generated_at=tz_aware(y,m,d,15,0), direction=Side.BUY,
                       execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN))
out["random_1000"] = run_engine(sigs, "RANDOM_1000")

Path("reports/certification_baselines.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("SAVED", out)
