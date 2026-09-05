"""BT_ENGINE_V2 — production-grade A-share backtest execution kernel.

V2 事件驱动执行内核：从 Signal 之后全部走事件驱动。
V1 (BT_ENGINE_V1) 冻结，保留在 backtest.py / strategy_runner.py / exposure_runner.py。
"""
__all__ = ["VERSION", "BT_ENGINE_V2_STATUS", "BT_ENGINE_V1"]
VERSION = "2.0.0"
BT_ENGINE_V2_STATUS = "RESEARCH_READY"
BT_ENGINE_V1 = "LEGACY_FROZEN"
