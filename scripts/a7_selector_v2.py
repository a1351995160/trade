"""A7: Daily Stock Selector V2 — robust-only production (currently none), promising-only experimental watchlist, NO_TRADE default."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, "src")
from chanlun_trader.research.selector import DailyStockSelector

ROBUST_FACTORS = ["F_REV20","F_LOWAMT","F_IND_DISP_LOW","R2_REV10","R2_REV30","R2_REV_COMPOSITE",
                  "R2_REV20_TOPLIQ","R2_LOWAMT_HIGHPRICE","R2_REV20_INDUP"]
PROMISING_FACTORS = ["F_REV5","F_LOWVOLBURST","F_UP5_INV","R2_LOWAMT_TOPLIQ","R2_LOWVOL_TOPLIQ","R2_REV20_NOLIMIT"]

def main():
    sel = DailyStockSelector(allowed_factors=set(ROBUST_FACTORS) | set(PROMISING_FACTORS))
    asof = 20240731  # last TRAIN date; no Final Test access
    cands = sel.select(as_of=asof, top_n=10, factor_ids=PROMISING_FACTORS, candidate_type="EXPERIMENTAL_WATCHLIST")
    # V2 snapshot with explicit production separation
    out_dir = Path("data/research/daily_selection_v2")
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_path = out_dir / f"{asof}.json"
    if snap_path.exists():
        snap_path.unlink()  # only this script owns v2 dir; A7 gate needs regenerable snapshot
    payload = {
        "selector_version": "2.0.0",
        "selection_date": asof,
        "selection_time": pd.Timestamp.now().isoformat(timespec="seconds"),
        "production_candidates": [],
        "experimental_watchlist": [c.to_dict() for c in cands],
        "no_trade": True,
        "production_policy": "PRODUCTION_CANDIDATES only from ROBUST_PRETEST strategies (count=0)",
        "experimental_policy": "PROMISING factors/hypotheses -> EXPERIMENTAL_WATCHLIST only, NO_TRADE",
        "allowed_factors": sorted(ROBUST_FACTORS + PROMISING_FACTORS),
    }
    snap_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = {
        "selector_version": "2.0.0",
        "production_candidate_strategy_ids": [],
        "production_candidate_factor_ids": [],
        "experimental_watchlist_factor_ids": PROMISING_FACTORS,
        "no_trade_default": True,
        "immutable_snapshot_dir": str(out_dir),
    }
    Path("data/research/daily_selector_v2_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("snapshot", snap_path)
    print("experimental candidates", len(cands))
    print(json.dumps(cfg, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
