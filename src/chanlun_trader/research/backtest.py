"""BT_ENGINE_V2 integration for research strategies。"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from chanlun_trader.engine.engine import BacktestEngineV2, EngineConfig
from chanlun_trader.engine.signal import Signal, Side, ExecutionPolicy
from chanlun_trader.engine.time_types import tz_aware
from chanlun_trader.engine.universe import UniverseService


def date_to_ts(d: int, hh: int = 15, mm: int = 0) -> pd.Timestamp:
    return tz_aware(d // 10000, (d // 100) % 100, d % 100, hh, mm)


def build_signals_from_factor(factor_df: pd.DataFrame, calendar: list[int], strategy_id: str,
                              top_n: int = 5, start: int = 0, end: int = 99_999_999,
                              direction_hint: str = "long") -> list:
    """把 FactorStore 长表转成每日 top-N V2 Signal。

    tie-break：factor 值 -> symbol 字典序（确定性）。
    """
    cal = [d for d in calendar if start <= d <= end]
    sigs = []
    for d in cal:
        day = factor_df[factor_df["timestamp"] == d]
        if day.empty:
            continue
        day = day.sort_values(["value", "symbol"], ascending=[direction_hint != "long", True])
        for _, row in day.head(top_n).iterrows():
            sigs.append(Signal(
                strategy_id=strategy_id,
                signal_id=f"{strategy_id}:{row['symbol']}:{d}:{len(sigs)}",
                symbol=row["symbol"],
                generated_at=date_to_ts(d, 15, 0),
                direction=Side.BUY,
                execution_policy=ExecutionPolicy.NEXT_SESSION_OPEN,
                score=float(row["value"]),
            ))
    return sigs


def run_v2_daily(store, calendar: list[int], signals: list, max_positions: int = 5,
                 max_position_weight: Optional[float] = None, max_holding_days: int = 5,
                 initial_cash: float = 1_000_000.0, universe: Optional[UniverseService] = None):
    w = max_position_weight or (1.0 / max(1, max_positions))
    cfg = EngineConfig(
        initial_cash=initial_cash,
        max_positions=max_positions,
        max_position_weight=w,
        mode="DAILY",
        enable_index_filter=False,
        index_filter_enabled=False,
        max_holding_days=max_holding_days,
    )
    eng = BacktestEngineV2(store, calendar, config=cfg, universe=universe)
    eng.add_signals(signals)
    res = eng.run()
    return res


def metrics_from_result(res, initial_cash: float = 1_000_000.0) -> dict:
    snaps = res.ledger.snapshots
    if not snaps:
        return {"total_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
                "profit_factor": 0.0, "win_rate": 0.0, "trade_count": 0,
                "final_equity": initial_cash}
    eq = pd.Series([float(s.equity) for s in snaps])
    ret = eq / initial_cash - 1.0
    dd = eq / eq.cummax() - 1.0
    daily = eq.pct_change().dropna()
    sharpe = float(np.sqrt(252) * daily.mean() / daily.std(ddof=1)) if len(daily) > 1 and daily.std(ddof=1) > 0 else 0.0
    sells = [t for t in res.ledger.trades if t.side == Side.SELL]
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in sells if t.realized_pnl <= 0]
    pf = float(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0.0)
    wr = float(len(wins) / len(sells)) if sells else 0.0
    return {
        "total_return": float(ret.iloc[-1]),
        "max_drawdown": float(dd.min()),
        "sharpe": float(sharpe),
        "profit_factor": pf,
        "win_rate": wr,
        "trade_count": len(sells),
        "final_equity": float(snaps[-1].equity),
    }
