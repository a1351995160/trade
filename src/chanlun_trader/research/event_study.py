"""EventStudy: 事件发生后 1/2/3/5/7/10D 的收益分布与 MAE/MFE。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .label import HORIZONS


@dataclass
class EventStudyResult:
    event_id: str
    version: str
    horizon: int
    n: int
    mean: float
    median: float
    win_rate: float
    std: float
    q05: float
    q25: float
    q75: float
    q95: float
    mae: float
    mfe: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class EventStudy:
    """事件研究：event 与 label 按 symbol+event_time 对齐。

    labels 使用 timestamp=T，与 event_time 对齐后的未来收益分布。
    """

    def run(self, event_id: str, version: str, events: pd.DataFrame,
            labels: pd.DataFrame, horizon: int = 5,
            event_time_col: str = "event_time") -> EventStudyResult:
        lab = labels[labels["horizon"] == horizon].rename(columns={"timestamp": event_time_col})
        merged = events.merge(lab, on=["symbol", event_time_col], how="inner")
        if merged.empty:
            return EventStudyResult(event_id, version, horizon, 0, float("nan"), float("nan"),
                                    float("nan"), float("nan"), float("nan"), float("nan"),
                                    float("nan"), float("nan"), float("nan"), float("nan"))
        r = merged["future_return"].astype(float)
        return EventStudyResult(
            event_id=event_id, version=version, horizon=horizon, n=len(r),
            mean=float(r.mean()), median=float(r.median()), win_rate=float((r > 0).mean()),
            std=float(r.std(ddof=1)) if len(r) > 1 else 0.0,
            q05=float(r.quantile(0.05)), q25=float(r.quantile(0.25)),
            q75=float(r.quantile(0.75)), q95=float(r.quantile(0.95)),
            mae=float(r.min()), mfe=float(r.max()),
        )

    def run_horizons(self, event_id, version, events, labels, horizons=HORIZONS) -> pd.DataFrame:
        return pd.DataFrame([self.run(event_id, version, events, labels, h).to_dict() for h in horizons])
