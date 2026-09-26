"""固定 51 票规则的因果后复权特征适配；成交仍走原始价。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pandas as pd

from chanlun_trader.research_factory.causal_dividend_features_v1 import causal_hfq_bars
from scripts.s1_public_entry_strategy_v1 import (
    Fixed51AccountBackend, Fixed51VoteStrategy, SOURCE_FILES,
)


ROOT = Path(__file__).resolve().parents[1]
FEATURE_SOURCE = ROOT / "src/chanlun_trader/research_factory/causal_dividend_features_v1.py"


class Causal51VoteStrategy(Fixed51VoteStrategy):
    # 买卖规则与阈值不变；价格口径变化属于独立的策略计划身份。
    source_files = tuple(str(path) for path in (*SOURCE_FILES, FEATURE_SOURCE))
    requirements = replace(Fixed51VoteStrategy.requirements, price_view="CAUSAL_HFQ_FEATURE_RAW_EXECUTION")

    def __init__(self):
        super().__init__()
        self.parameters = {**self.parameters, "feature_price_model": "CASH_DIVIDEND_CAUSAL_HFQ_V1"}

    def validate(self):
        expected = Fixed51VoteStrategy()
        if (self.definition != expected.definition
                or self.parameters != {**expected.parameters,
                                       "feature_price_model": "CASH_DIVIDEND_CAUSAL_HFQ_V1"}):
            raise ValueError("FROZEN_51_RULE_OR_FEATURE_MODE_CHANGED")


class Causal51AccountBackend(Fixed51AccountBackend):
    def __init__(self, events: tuple[dict, ...]):
        self.events = events
        self.price_evidence: dict[str, list[dict]] = {}

    def check(self, requirements):
        if requirements != Causal51VoteStrategy.requirements:
            raise ValueError("UNSUPPORTED_CAUSAL_51_REQUIREMENTS")

    def describe(self) -> dict:
        description = super().describe()
        paths = (Path(__file__), FEATURE_SOURCE)
        description["backend"] = "S1_FIXED_51_CAUSAL_FEATURE_RAW_ACCOUNT_V1"
        description["source_hashes"].update({
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        return description

    def _matrix(self, bars: pd.DataFrame, vendor: pd.DataFrame, definition: dict) -> pd.DataFrame:
        feature_bars, evidence = causal_hfq_bars(bars, self.events)
        self.price_evidence[str(bars.iloc[0]["symbol"])] = evidence
        return super()._matrix(feature_bars, vendor, definition)
