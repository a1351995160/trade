"""V2 SlippageModel — 固定bps滑点，兼容旧 0.1%。"""
from __future__ import annotations


class SlippageModel:
    def apply(self, side: str, reference_price: float) -> float:
        raise NotImplementedError


class FixedBpsSlippage(SlippageModel):
    def __init__(self, bps: float = 0.001):
        self.bps = bps

    def apply(self, side: str, reference_price: float) -> float:
        if side in ("BUY", "buy", 0):
            return reference_price * (1 + self.bps)
        return reference_price * (1 - self.bps)
