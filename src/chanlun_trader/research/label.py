"""LabelStore：forward return labels，严格与 Feature 分离。"""
from __future__ import annotations

from pathlib import Path
from enum import StrEnum

import numpy as np
import pandas as pd

from .guard import ResearchDataAccessGuard
from .io_safety import GuardedResearchReader

DEFAULT_LABEL_DIR = Path("data/research/label_store")
HORIZONS = (1, 2, 3, 5, 7, 10, 20)


class LabelFamily(StrEnum):
    PREDICTIVE_RETURN_LABEL = "PREDICTIVE_RETURN_LABEL"
    TRADABLE_RETURN_LABEL = "TRADABLE_RETURN_LABEL"
    EVENT_EXECUTION_RETURN = "EVENT_EXECUTION_RETURN"


LABEL_SEMANTICS_REGISTRY = {
    LabelFamily.PREDICTIVE_RETURN_LABEL: {
        "entry_semantics": "T_CLOSE",
        "exit_semantics": "T_PLUS_H_CLOSE",
        "available_at_semantics": "T_PLUS_H_CLOSE",
    },
    LabelFamily.TRADABLE_RETURN_LABEL: {
        "entry_semantics": "T_PLUS_1_OPEN",
        "exit_semantics": "T_PLUS_H_CLOSE",
        "available_at_semantics": "T_PLUS_H_CLOSE",
    },
    LabelFamily.EVENT_EXECUTION_RETURN: {
        "entry_semantics": "EVENT_EXECUTION_ENTRY",
        "exit_semantics": "EVENT_HORIZON_EXIT",
        "available_at_semantics": "EVENT_HORIZON_EXIT",
    },
}


class LabelStore:
    """存储 future_Nd_return。

    - 列：symbol, timestamp, horizon, future_return, available_at
    - timestamp 是特征/决策日（T），future_return 是 T 到 T+N 的收益。
    - available_at = T+horizon 的收盘（标签只有在未来才可观测），禁止作为特征使用。
    - 默认用 raw close 计算；正式研究如需 PIT-adjusted return，必须显式传入
      total_return 序列并说明方法。
    """

    def __init__(self, root: Path = DEFAULT_LABEL_DIR, guard: ResearchDataAccessGuard | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = guard or ResearchDataAccessGuard()

    def compute_forward_returns(self, df: pd.DataFrame, price_col: str = "close",
                                label_family: LabelFamily = LabelFamily.PREDICTIVE_RETURN_LABEL) -> pd.DataFrame:
        """df 必须含 date 与 price_col，按 date 排序。

        返回长表：symbol, timestamp, horizon, future_return, available_at。
        """
        df = df.sort_values("date").reset_index(drop=True)
        semantics = LABEL_SEMANTICS_REGISTRY[label_family]
        dates = df["date"].to_numpy()
        prices = df[price_col].to_numpy(dtype=float)
        rows = []
        for i in range(len(dates)):
            for h in HORIZONS:
                j = i + h
                if j >= len(dates):
                    continue
                if prices[i] <= 0:
                    continue
                rows.append({
                    "timestamp": int(dates[i]),
                    "horizon": h,
                    "future_return": float(prices[j] / prices[i] - 1.0),
                    "available_at": int(dates[j]),   # T+h 收盘
                    "label_family": label_family.value,
                    **semantics,
                })
        return pd.DataFrame(rows)

    def save(self, symbol: str, labels: pd.DataFrame, allow_overwrite: bool = False) -> Path:
        path = self.root / f"labels_{symbol}.parquet"
        if path.exists() and not allow_overwrite:
            raise FileExistsError(f"labels already exist: {path}")
        self.guard.check_frame(labels, "timestamp")
        out = labels.copy()
        out["symbol"] = symbol
        if "label_family" not in out:
            out["label_family"] = LabelFamily.PREDICTIVE_RETURN_LABEL.value
            out["entry_semantics"] = LABEL_SEMANTICS_REGISTRY[LabelFamily.PREDICTIVE_RETURN_LABEL]["entry_semantics"]
            out["exit_semantics"] = LABEL_SEMANTICS_REGISTRY[LabelFamily.PREDICTIVE_RETURN_LABEL]["exit_semantics"]
            out["available_at_semantics"] = LABEL_SEMANTICS_REGISTRY[LabelFamily.PREDICTIVE_RETURN_LABEL]["available_at_semantics"]
        out.to_parquet(path, index=False)
        return path

    def load(self, symbol: str, as_of: int | None = None) -> pd.DataFrame:
        path = self.root / f"labels_{symbol}.parquet"
        if not path.exists():
            return pd.DataFrame()
        reader = GuardedResearchReader(self.guard)
        df = reader.read_parquet(path, date_column="timestamp", start_date=0,
                                 end_date=self.guard.research_end)
        if as_of is not None:
            self.guard.check_date(as_of, f"labels {symbol} as_of")
            df = df[df["available_at"] <= as_of]
        return df.reset_index(drop=True)
