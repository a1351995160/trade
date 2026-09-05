"""StrategyDefinition / StrategyLibrary / PromotionRecord。

只有 PROMISING_FACTOR / PROMISING_EVENT / PROMISING_INTERACTION 才能升级为
StrategyDefinition，并必须通过 BT_ENGINE_V2 验证。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_STRATEGY_DIR = Path("data/research/strategy_library")
DEFAULT_PROMOTION_DIR = Path("data/research/promotion_records")


@dataclass
class StrategyDefinition:
    strategy_id: str
    version: str
    hypothesis_id: str
    factors: list = field(default_factory=list)
    events: list = field(default_factory=list)
    universe: str = "TOP500_AMOUNT_PIT"
    ranking: str = "factor_score_desc"
    entry: str = "NEXT_SESSION_OPEN"
    exit: str = "max_holding_days"
    holding: int = 5
    position_sizing: str = "equal_weight"
    execution_policy: str = "NEXT_SESSION_OPEN"
    risk: str = "universe_gate_t1_price_limit"
    regime: str = "ANY"
    created_at: str = ""
    # Strategy-translation lineage and execution contract.
    parent_hypothesis_id: str = ""
    mechanism_id: str = ""
    source_seed_id: str = ""
    signal_definition_hash: str = ""
    event_definition_hash: str = ""
    available_at_rule: str = "T_CLOSE"
    ranking_policy: str = ""
    position_count_policy: str = ""
    holding_policy: str = ""
    exit_policy: str = ""
    risk_policy: str = ""
    capital_assumption: str = "10M_CNY_BASELINE"
    cost_model: str = ""
    slippage_model: str = ""
    engine_version: str = "BT_ENGINE_V2"
    data_version: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyDefinition":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class StrategyLibrary:
    def __init__(self, path: Path = DEFAULT_STRATEGY_DIR / "library.json"):
        self.path = Path(path)
        self._items: dict[tuple[str, str], StrategyDefinition] = {}

    def register(self, s: StrategyDefinition) -> None:
        self._items[(s.strategy_id, s.version)] = s

    def get(self, strategy_id: str, version: str | None = None) -> StrategyDefinition | None:
        if version:
            return self._items.get((strategy_id, version))
        matches = [s for (sid, _), s in self._items.items() if sid == strategy_id]
        return max(matches, key=lambda s: s.version) if matches else None

    def items(self) -> list:
        return list(self._items.values())

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"strategies": [s.to_dict() for s in self._items.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path: Path = DEFAULT_STRATEGY_DIR / "library.json") -> "StrategyLibrary":
        lib = cls(path)
        p = Path(path)
        if p.exists():
            payload = json.loads(p.read_text(encoding="utf-8"))
            for d in payload.get("strategies", []):
                lib.register(StrategyDefinition.from_dict(d))
        return lib


@dataclass
class PromotionRecord:
    strategy_id: str
    from_version: str
    to_version: str
    evidence: dict
    validation: dict
    red_team: dict
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PromotionRecord":
        return cls(**d)


class PromotionLedger:
    def __init__(self, path: Path = DEFAULT_PROMOTION_DIR / "ledger.jsonl"):
        self.path = Path(path)

    def record(self, r: PromotionRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    def load(self) -> list:
        if not self.path.exists():
            return []
        return [PromotionRecord.from_dict(json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
