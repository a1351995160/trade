"""V2 Signal / ExecutionPolicy / OrderIntent / PortfolioTarget.

Signal 只表达 Alpha 观点；执行策略由 ExecutionPolicy 表达；
买多少由 PortfolioTarget / PositionSizer 决定；最终提交由 OrderManager 产生 Order。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

from .time_types import ensure_aware


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionPolicy(str, Enum):
    NEXT_SESSION_OPEN = "NEXT_SESSION_OPEN"
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
    NEXT_BAR_VWAP = "NEXT_BAR_VWAP"  # 预留，暂不启用
    MARKET_ON_OPEN = "MARKET_ON_OPEN"  # 等同于 NEXT_SESSION_OPEN


class TargetType(str, Enum):
    WEIGHT = "WEIGHT"          # 目标仓位权重
    QUANTITY = "QUANTITY"      # 目标数量（股）
    VALUE = "VALUE"            # 目标金额
    CLEAR = "CLEAR"            # 清仓


@dataclass
class Signal:
    """V2 Signal。generated_at 必须 >= 所有输入数据 available_at。"""
    strategy_id: str
    signal_id: str
    symbol: str
    generated_at: pd.Timestamp
    direction: Side
    score: float = 0.0
    signal_type: str = "GENERIC"
    execution_policy: ExecutionPolicy = ExecutionPolicy.NEXT_SESSION_OPEN
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_event_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.generated_at = ensure_aware(self.generated_at)
        if not isinstance(self.direction, Side):
            self.direction = Side(self.direction)


@dataclass
class OrderIntent:
    """Signal -> Order 之间的意图层：什么时候想交易、怎么买、买多少。"""
    intent_id: str
    signal_id: str
    strategy_id: str
    symbol: str
    side: Side
    target_type: TargetType
    target_value: float
    created_at: pd.Timestamp
    execution_policy: ExecutionPolicy
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.created_at = ensure_aware(self.created_at)
        if not isinstance(self.side, Side):
            self.side = Side(self.side)


@dataclass
class PortfolioTarget:
    """组合构建层输出：目标权重/数量/清仓。"""
    strategy_id: str
    symbol: str
    target_type: TargetType
    target_value: float
    generated_at: pd.Timestamp
    priority: int = 0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.generated_at = ensure_aware(self.generated_at)
        if not isinstance(self.target_type, TargetType):
            self.target_type = TargetType(self.target_type)
