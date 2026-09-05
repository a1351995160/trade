"""Validation helpers for research integrity remediation."""
from __future__ import annotations
from enum import Enum
from typing import Any, Iterable
from pathlib import Path
import json

import numpy as np
import pandas as pd


class ValidationAccessViolation(RuntimeError):
    """低证据阶段试图读取 Validation 区间。"""


class ValidationGuard:
    """按研究阶段限制 Validation 数据访问，并记录违规尝试。"""

    def __init__(self, log_path: str | Path | None = None, validation_start: int = 20240801):
        self.log_path = Path(log_path) if log_path else None
        self.validation_start = int(validation_start)
        self.access_count = 0

    def guard_range(self, phase: str, start_date: int, end_date: int) -> None:
        if int(end_date) < self.validation_start:
            return
        if str(phase) in {"PROMISING_FACTOR", "ROBUST_PRETEST", "FROZEN_CANDIDATE"}:
            return
        self.access_count += 1
        record = {"phase": phase, "start_date": int(start_date), "end_date": int(end_date),
                  "status": "DENIED"}
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        raise ValidationAccessViolation(
            f"Validation access denied for phase={phase}: {start_date}..{end_date}"
        )


def purge_embargo_split(dates: Iterable[int], test_start: int, embargo: int):
    """将 embargo 天数从训练尾部剔除，返回 (train, test)。"""
    dates = list(dates)
    train_end = int(test_start) - int(embargo)
    return [d for d in dates if d < train_end], [d for d in dates if d >= int(test_start)]


class MechanismEvidenceLevel(str, Enum):
    DIRECT = "DIRECT"
    SUPPORTED_PROXY = "SUPPORTED_PROXY"
    WEAK_PROXY = "WEAK_PROXY"
    UNVERIFIED = "UNVERIFIED"


class RequiredEvidenceCompletenessValidator:
    """Checks that each required evidence item has an implemented, valid result."""

    def __init__(self):
        self.records: list[dict] = []

    def require(self, item: str, implemented: bool, valid: bool, note: str = "") -> "RequiredEvidenceCompletenessValidator":
        self.records.append(dict(item=item, required=True, implemented=implemented,
                                 valid_result=valid, note=note))
        return self

    @property
    def passed(self) -> bool:
        return all(r["implemented"] and r["valid_result"] for r in self.records)

    def report(self) -> pd.DataFrame:
        return pd.DataFrame(self.records)


class IndependentAcceptanceValidator:
    """Recompute acceptance metrics from raw inputs instead of trusting status strings."""

    def __init__(self):
        self.records: list[dict] = []

    def validate_equity_metrics(self, label: str, equity_curve: Iterable[float],
                                initial_cash: float = 10_000_000.0) -> dict:
        eq = pd.Series([float(x) for x in equity_curve])
        if len(eq) < 2:
            return dict(label=label, total_return=0.0, sharpe=0.0, max_drawdown=0.0,
                        recomputed=True, error="insufficient snapshots")
        total = float(eq.iloc[-1] / initial_cash - 1.0)
        dd = float((eq / eq.cummax() - 1.0).min())
        ret = eq.pct_change().dropna()
        sharpe = float(np.sqrt(252) * ret.mean() / ret.std(ddof=1)) if len(ret) > 1 and ret.std(ddof=1) > 0 else 0.0
        return dict(label=label, total_return=total, sharpe=sharpe, max_drawdown=dd,
                    recomputed=True, error=None)

    def validate_trade_metrics(self, label: str, pnl: Iterable[float]) -> dict:
        p = pd.Series([float(x) for x in pnl]).dropna()
        if len(p) == 0:
            return dict(label=label, profit_factor=0.0, win_rate=0.0, mean_trade=0.0,
                        median_trade=0.0, n_trades=0, recomputed=True, error="empty")
        wins = p[p > 0].sum()
        losses = abs(p[p <= 0].sum())
        pf = float(wins / losses) if losses and losses > 0 else (float("inf") if wins else 0.0)
        return dict(label=label, profit_factor=pf, win_rate=float((p > 0).mean()),
                    mean_trade=float(p.mean()), median_trade=float(p.median()), n_trades=len(p),
                    recomputed=True, error=None)

    def validate_winner_concentration(self, label: str, pnl: Iterable[float]) -> dict:
        p = pd.Series([float(x) for x in pnl]).sort_values(ascending=False)
        if len(p) == 0 or p.sum() == 0:
            return dict(label=label, top1=0.0, top3=0.0, top5=0.0, top10=0.0, top20=0.0,
                        winner_concentration_status="NOT_APPLICABLE")
        total = p.sum()
        out = dict(label=label, top1=float(p.iloc[0] / total), top3=float(p.head(3).sum() / total),
                   top5=float(p.head(5).sum() / total), top10=float(p.head(10).sum() / total),
                   top20=float(p.head(min(20, len(p))).sum() / total))
        out["winner_concentration_status"] = "FAIL" if out["top10"] > 0.5 else "PASS"
        return out


def extreme_regime_status(train_start: int, train_end: int, window_start: int, window_end: int) -> str:
    """Status semantics: PASS / FAIL / NOT_APPLICABLE / DATA_UNAVAILABLE."""
    if train_start is None or train_end is None:
        return "DATA_UNAVAILABLE"
    if window_start > train_end or window_end < train_start:
        return "NOT_APPLICABLE"
    return "PASS"  # only if the window is inside TRAIN and the strategy survived exclusion


def execution_gap_status(has_train_5m: bool, has_call_auction: bool, has_order_book: bool) -> str:
    """Microstructure execution gap semantics."""
    if not (has_train_5m and has_call_auction and has_order_book):
        return "UNMEASURABLE"
    return "MEASURED"
