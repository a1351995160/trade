"""Register M6/M7 PROMISING factors into FactorRegistry + FactorStore + FactorLibrary."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd

from chanlun_trader.research.factor import FactorDefinition, FactorRegistry, FactorStore
from chanlun_trader.research.library import FactorLibrary, FactorLibraryEntry
from m7_discovery_round2 import build_features
from m6_lib import TRAIN, VALIDATION

FACTORS = [
    ("F_REV20", "20日反转：20日累计涨幅最低", "Momentum", "qfq_pit_ret 20d cumprod, value=-r20"),
    ("F_REV5", "5日反转：5日累计涨幅最低", "Momentum", "qfq_pit_ret 5d cumprod, value=-r5"),
    ("F_LOWVOLBURST", "低量比：volume/MA20低", "Volume", "value=-vol_ratio"),
    ("F_LOWAMT", "低成交额比：amount/MA20低", "Volume", "value=-amt_ratio"),
    ("F_VOLCOMP_LOW", "低波动压缩：vol20/vol60低", "Volatility", "value=-vol_comp"),
    ("F_GAP", "隔夜跳空高开", "Price", "value=gap"),
    ("F_UP5_INV", "连涨5日反向", "Price", "value=-up5"),
    ("F_IND_DISP_LOW", "行业内低离散度", "Sector", "value=-ind_r5_std"),
    ("R2_REV10", "10日反转", "Momentum", "value=-r10"),
    ("R2_REV30", "30日反转", "Momentum", "value=-r30"),
    ("R2_REV_COMPOSITE", "5日+20日复合反转", "Momentum", "value=-0.5*r5-0.5*r20"),
    ("R2_REV20_TOPLIQ", "20日反转且成交额前500", "Momentum", "value=-r20*top500"),
    ("R2_LOWAMT_TOPLIQ", "成交额前500中低成交额比", "Volume", "value=-amt_ratio*top500"),
    ("R2_LOWVOL_TOPLIQ", "成交额前500中低量比", "Volume", "value=-vol_ratio*top500"),
    ("R2_LOWAMT_HIGHPRICE", "低成交额比且价格高于20日中位", "Volume", "value=-amt_ratio*price_rank20"),
    ("R2_REV20_NOLIMIT", "20日反转且20日内无跌停", "Momentum", "value=-r20*(limdn_any20==0)"),
    ("R2_REV20_INDUP", "20日反转且行业未走弱", "Momentum", "value=-r20*(ind_r5>-0.02)"),
    ("R2_GAP_NOTUP5", "跳空高开且非连涨5日", "Price", "value=gap*(up5<5)"),
]


def main():
    t0 = time.time()
    ft = build_features()
    print("features ready", round(time.time() - t0, 1), flush=True)
    reg = FactorRegistry(Path("data/research/factor_registry/registry.json"))
    store = FactorStore(Path("data/research/factor_store"))
    lib = FactorLibrary(Path("data/research/factor_library/library.json"))
    for fid, desc, family, formula in FACTORS:
        reg.register(FactorDefinition(
            factor_id=fid, version="v1", name=fid, family=family, description=desc,
            formula=formula, inputs=["daily_raw", "corporate_action_gbbq", "industry_membership"],
            lookback=60, frequency="DAILY", available_at_rule="T_CLOSE",
            feature_price_mode="qfq_pit" if ("r20" in formula or "r5" in formula or "r10" in formula or "r30" in formula) else "raw",
            PIT_safe=True, direction_hint="long", created_at="2026-08-18", status="PROMISING",
        ))
        sub = ft[["symbol", "date", fid]].rename(columns={"date": "timestamp", fid: "value"})
        sub = sub[(sub["timestamp"] >= TRAIN[0]) & (sub["timestamp"] <= VALIDATION[1])].dropna(subset=["value"])
        sub["available_at"] = sub["timestamp"]
        sub["factor_id"] = fid
        sub["factor_version"] = "v1"
        store.put(fid, "v1", sub, allow_overwrite=True)
        lib.put(FactorLibraryEntry(
            factor_id=fid, version="v1", status="PROMISING",
            ic_mean=None, rank_ic_mean=None, icir=None, n_obs=len(sub),
            notes="Discovery Round1/Round2 promising, not ROBUST (no V2/red-team yet)",
        ))
    reg.save()
    lib.save()
    print("registered", len(FACTORS), "factors", round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
