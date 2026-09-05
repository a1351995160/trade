"""M1 FACTOR/EVENT FOUNDATION DEMO — 真实本地数据 PIT 因子计算。

演示：
1) 用 qfq_columns_asof(as_of=D) 计算 5 日动量（PIT-safe）
2) FactorStore 保存
3) LabelStore 保存 raw-close forward returns
4) FactorEvaluator 评估
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd

from chanlun_trader.config import load_config
from chanlun_trader.tdx_data import TdxData, list_a_stocks
from chanlun_trader.research.factor import FactorStore, FactorDefinition, FactorRegistry
from chanlun_trader.research.label import LabelStore
from chanlun_trader.research.evaluation import FactorEvaluator
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research.qfq import qfq_columns_asof


def main() -> None:
    cfg = load_config()
    guard = ResearchDataAccessGuard()
    tdx = TdxData(cfg["tdx"]["vipdoc"], cfg["tdx"]["gbbq"], cache_dir=cfg["tdx"].get("cache_dir"))
    stocks = list_a_stocks(tdx.vipdoc)[:20]
    gbbq = tdx._load_gbbq()

    factor_rows = []
    label_rows = []
    for st in stocks:
        code, market = st["code"], st["market"]
        sym = f"{code}.{'SH' if market == 1 else 'SZ'}"
        raw = tdx.get_day(code, market)
        if raw.empty:
            continue
        raw = raw[(raw["date"] >= 20220801) & (raw["date"] <= 20250731)]
        if len(raw) < 30:
            continue
        raw = raw.reset_index(drop=True)
        g = gbbq[gbbq["code"] == code].copy()
        dates = raw["date"].tolist()
        closes = raw["close"].to_numpy()
        # PIT qfq series per decision date（只演示每天计算；实际可优化为增量）
        for idx, d in enumerate(dates):
            if idx < 6:
                continue
            pit = qfq_columns_asof(raw, g, d)
            # 注意：qfq_columns_asof 输出与原 raw 同序
            c_now = float(pit["qfq_close"].iloc[idx])
            c_prev = float(pit["qfq_close"].iloc[idx - 5])
            if c_now <= 0 or c_prev <= 0:
                continue
            mom5 = c_now / c_prev - 1.0
            factor_rows.append({
                "symbol": sym, "timestamp": int(d), "value": mom5,
                "available_at": int(d),
            })
        # labels from raw close（正式研究建议 PIT-adjusted total return）
        for i in range(len(dates)):
            for h in (1, 5, 10):
                j = i + h
                if j >= len(dates) or closes[i] <= 0:
                    continue
                label_rows.append({
                    "symbol": sym, "timestamp": int(dates[i]), "horizon": h,
                    "future_return": float(closes[j] / closes[i] - 1.0),
                    "available_at": int(dates[j]),
                })

    factor_df = pd.DataFrame(factor_rows)
    labels = pd.DataFrame(label_rows)
    print(f"factor rows={len(factor_df)}, label rows={len(labels)}")

    # Store
    fstore = FactorStore(guard=guard)
    fstore.put("F_MOM5", "v1", factor_df, allow_overwrite=True)
    lstore = LabelStore(guard=guard)
    for sym, g in labels.groupby("symbol"):
        lstore.save(sym, g, allow_overwrite=True)

    # Registry
    reg = FactorRegistry()
    reg.register(FactorDefinition(
        factor_id="F_MOM5", version="v1", name="momentum_5d_pit_qfq", family="Momentum",
        description="PIT-safe 5日动量（qfq as-of 决策日）", formula="qfq_close(D)/qfq_close(D-5)-1",
        inputs=["daily_raw", "corporate_action_gbbq"], lookback=5, frequency="DAILY",
        available_at_rule="T_CLOSE", feature_price_mode="qfq_pit", PIT_safe=True,
        direction_hint="long", created_at="2026-08-18", status="DISCOVERED",
    ))
    reg.save()

    # Evaluate
    ev = FactorEvaluator()
    res = ev.evaluate_horizons("F_MOM5", "v1", factor_df, labels)
    print(res.to_string())
    res.to_csv("reports/M1_FACTOR_DEMO.csv", index=False)
    print("saved reports/M1_FACTOR_DEMO.csv")


if __name__ == "__main__":
    main()
