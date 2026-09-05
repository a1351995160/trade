"""DailyStockSelector — 每日 PIT 选股（ROBUST 生产候选 + PROMISING 实验观察）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .guard import ResearchDataAccessGuard
from .factor import FactorStore


@dataclass
class Candidate:
    symbol: str
    rank: int
    score: float
    candidate_type: str
    triggered_factors: list = field(default_factory=list)
    supporting_events: list = field(default_factory=list)
    negative_factors: list = field(default_factory=list)
    market_regime: str = "ANY"
    suggested_horizon: str = "3~5D"
    confidence_tier: str = "C"
    risk_flags: list = field(default_factory=list)
    data_timestamp: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


class DailyStockSelector:
    """第一版：PROMISING factors 用于 EXPERIMENTAL_WATCHLIST；ROBUST 为空则 NO TRADE。"""

    def __init__(self, factor_store: FactorStore | None = None,
                 guard: ResearchDataAccessGuard | None = None,
                 allowed_factors: Iterable[str] | None = None):
        self.factor_store = factor_store or FactorStore(Path("data/research/factor_store"))
        self.guard = guard or ResearchDataAccessGuard()
        self.allowed_factors = set(allowed_factors) if allowed_factors else None

    def available_factors(self) -> list[str]:
        if not self.factor_store.root.exists():
            return []
        out = []
        for p in self.factor_store.root.glob("*.parquet"):
            fid = p.stem.split("_v")[0]
            if fid and fid not in out:
                out.append(fid)
        return sorted(out)

    def select(self, as_of: int, top_n: int = 10,
               factor_ids: Iterable[str] | None = None,
               candidate_type: str = "EXPERIMENTAL") -> list[Candidate]:
        self.guard.check_date(as_of)
        if factor_ids is None:
            factor_ids = self.available_factors()
        factor_ids = list(factor_ids)
        if self.allowed_factors:
            factor_ids = [f for f in factor_ids if f in self.allowed_factors]
        frames = []
        for fid in factor_ids:
            q = self.factor_store.query(fid, "v1", as_of=as_of)
            if q is None or q.empty:
                continue
            latest = q.sort_values("timestamp").drop_duplicates("symbol", keep="last")
            # 横截面排名 -> 0~1
            latest = latest.copy()
            latest["rank_score"] = latest.groupby("timestamp")["value"].rank(pct=True)
            latest = latest.rename(columns={"value": f"{fid}_value", "rank_score": f"{fid}_rank"})
            frames.append(latest[["symbol", "timestamp", f"{fid}_value", f"{fid}_rank"]])
        if not frames:
            return []
        merged = frames[0]
        for f in frames[1:]:
            merged = merged.merge(f, on=["symbol", "timestamp"], how="outer")
        # 等权平均各因子的横截面 rank；同时记录触发因子（rank >= 0.8）
        rank_cols = [c for c in merged.columns if c.endswith("_rank")]
        merged["score"] = merged[rank_cols].mean(axis=1)
        merged = merged.dropna(subset=["score"]).sort_values(["score", "symbol"], ascending=[False, True])
        top = merged.head(top_n)
        out = []
        for i, (_, row) in enumerate(top.iterrows()):
            triggered = []
            for c in rank_cols:
                v = row[c]
                if v is not None and not (isinstance(v, float) and np.isnan(v)) and v >= 0.8:
                    triggered.append(c.replace("_rank", ""))
            out.append(Candidate(
                symbol=row["symbol"], rank=i + 1, score=float(row["score"]),
                candidate_type=candidate_type, triggered_factors=triggered,
                supporting_events=[], negative_factors=[], market_regime="ANY",
                suggested_horizon="3~5D", confidence_tier="C",
                risk_flags=["PROMISING_NOT_ROBUST"], data_timestamp=str(row["timestamp"]),
            ))
        return out

    def snapshot(self, selection_date: int, candidates: list[Candidate], path: Path | None = None) -> Path:
        path = path or Path(f"data/research/daily_selection/{selection_date}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"immutable snapshot already exists: {path}")
        payload = {
            "selection_date": selection_date,
            "selection_time": pd.Timestamp.now().isoformat(timespec="seconds"),
            "engine_version": "2.0.0",
            "data_version": "tdx-clean-2026.08",
            "candidate_type": "EXPERIMENTAL_WATCHLIST",
            "production_candidates": [],
            "candidates": [c.to_dict() for c in candidates],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
