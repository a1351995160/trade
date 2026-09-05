# BACKTEST / LIVE PARITY DESIGN

## 原则

Strategy / Risk / PortfolioConstruction 不感知运行模式（Backtest / Paper / Live）。

## 接口

```python
# interfaces.py
class DataFeedInterface:
    def asof_view(self, now): ...

class BrokerInterface:
    def submit_intent(self, intent, ts) -> Optional[str]: ...
    def cancel_order(self, order_id, ts) -> bool: ...
    def query_positions(self): ...
    def query_cash(self): ...
```

## 替换矩阵

| 组件 | Backtest | Paper | Live |
|------|----------|-------|------|
| DataFeed | MarketDataStore + AsOfDataView | TQClient via DataAdapter + AsOfDataView | TQClient via DataAdapter |
| Broker | BrokerSimulator | PaperBroker（同 BrokerSimulator，但用实时 bar） | TdxBrokerAdapter(order_stock/cancel_order_stock) |
| Ledger | PortfolioLedger | PortfolioLedger | Broker 对账 + Ledger 影子账 |
| EventLog | BacktestEventLog | 同左 | 同左 + 委托编号 |

## Paper 模式要点

- Paper 下单也用 `OrderManager`，成交判定走同一套 `FillModel/FeeModel/SlippageModel`。
- 5分钟实时 bar 到达后触发 `BAR_CLOSE`，Broker 在该事件上处理 eligible 订单。
- 集合竞价不下单（future extension）。

## Live 模式要点

- `BrokerInterface` 由 TdxBrokerAdapter 实现：`submit_intent` -> `order_stock`，`cancel_order` -> `cancel_order_stock`。
- 客户端返回 `Value=1`（待确认）时订单状态为 SUBMITTED，不视为已成交。
- 成交回报用 `query_stock_orders` 的 Status 更新 Order 状态；费用以券商实际为准（FeeModel 只作预估值）。
