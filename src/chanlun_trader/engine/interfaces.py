"""V2 Backtest / Paper / Live 接口边界。

Strategy / Risk / PortfolioConstruction 保持一致；
只替换 HistoricalDataFeed -> LiveDataFeed, BrokerSimulator -> PaperBroker/BrokerAdapter。
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .signal import OrderIntent


class DataFeedInterface:
    def asof_view(self, now: pd.Timestamp):
        raise NotImplementedError


class BrokerInterface:
    def submit_intent(self, intent: OrderIntent, ts: pd.Timestamp) -> Optional[str]:
        """返回 order_id；None 表示被拒。"""
        raise NotImplementedError

    def cancel_order(self, order_id: str, ts: pd.Timestamp) -> bool:
        raise NotImplementedError

    def query_positions(self):
        raise NotImplementedError

    def query_cash(self):
        raise NotImplementedError
