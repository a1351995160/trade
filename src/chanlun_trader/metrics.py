"""绩效统计：胜率、盈亏比、年化、最大回撤等。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def compute_metrics(result: dict, benchmark_df: pd.DataFrame | None = None) -> dict:
    trades = result["trades"]
    equity_curve = result["equity_curve"]
    initial_cash = float(result["initial_cash"])
    final_cash = float(result["cash"])

    eq = pd.DataFrame(equity_curve, columns=["date", "equity"])
    if eq.empty:
        return {
            "initial_cash": initial_cash,
            "final_cash": final_cash,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "avg_holding_days": 0.0,
            "benchmark_return": 0.0,
        }
    eq = eq.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    final_equity = float(eq.iloc[-1]["equity"])
    total_return = final_equity / initial_cash - 1

    # 年化收益（按实际自然日跨度，一年 365 天）
    from datetime import datetime

    d1 = datetime.strptime(str(int(eq.iloc[0]["date"])), "%Y%m%d")
    d2 = datetime.strptime(str(int(eq.iloc[-1]["date"])), "%Y%m%d")
    days = max((d2 - d1).days, 1)
    years = days / 365.0
    annual_return = (final_equity / initial_cash) ** (1 / years) - 1 if years > 0 and final_equity > 0 else 0.0

    # 最大回撤
    cummax = eq["equity"].cummax()
    drawdown = eq["equity"] / cummax - 1
    max_drawdown = float(drawdown.min())

    # 交易统计
    closed = [t for t in trades if t.sell_reason != "open"]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([abs(t.pnl) for t in losses])) if losses else 0.0
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)
    avg_holding_days = float(np.mean([t.holding_days for t in closed])) if closed else 0.0

    # 盈利因子与最长连续亏损
    gross_profit = float(sum(t.pnl for t in wins)) if wins else 0.0
    gross_loss = float(abs(sum(t.pnl for t in losses))) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    max_consecutive_losses = 0
    streak = 0
    for t in closed:
        if t.pnl <= 0:
            streak += 1
            max_consecutive_losses = max(max_consecutive_losses, streak)
        else:
            streak = 0

    # 日收益统计（基于权益曲线）
    daily_ret = eq["equity"].pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0
    downside = daily_ret[daily_ret < 0]
    downside_std = float(downside.std()) if len(downside) > 1 else 0.0
    sortino = float(daily_ret.mean() / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    calmar = float(annual_return / abs(max_drawdown)) if max_drawdown < 0 else 0.0

    # 基准收益
    bench_return = 0.0
    if benchmark_df is not None and len(benchmark_df):
        b = benchmark_df.sort_values("date")
        b = b[(b["date"] >= int(eq.iloc[0]["date"])) & (b["date"] <= int(eq.iloc[-1]["date"]))]
        if len(b) >= 2:
            bench_return = float(b.iloc[-1]["close"] / b.iloc[0]["close"] - 1)

    return {
        "initial_cash": initial_cash,
        "final_cash": final_cash,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "trade_count": len(closed),
        "win_rate": win_rate,
        "profit_loss_ratio": profit_loss_ratio,
        "avg_holding_days": avg_holding_days,
        "profit_factor": profit_factor,
        "max_consecutive_losses": max_consecutive_losses,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "benchmark_return": bench_return,
        "start_date": int(eq.iloc[0]["date"]),
        "end_date": int(eq.iloc[-1]["date"]),
    }
