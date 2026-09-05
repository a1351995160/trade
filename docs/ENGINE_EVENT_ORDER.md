# ENGINE EVENT ORDER

Deterministic event ordering for `BacktestEngineV2.run()`.

## Clock events (within one trading day)

| Time | EventKind | DAILY | 5MIN |
|------|-----------|-------|------|
| 09:30 | SESSION_OPEN | Y | Y |
| 09:35..15:00 | BAR_CLOSE | only 15:00 | every 5 minutes |
| 15:00 | SESSION_CLOSE | Y | Y |
| 15:30 | AFTER_CLOSE | Y | Y |

`ClockEvent` 排序 key: `(timestamp, kind.value)` — 同 timestamp 先 SESSION_OPEN? 实际 15:00 时 BAR_CLOSE 与 SESSION_CLOSE 同 timestamp；按 kind.value 排序 BAR_CLOSE < SESSION_CLOSE。

## Per-event fixed processing order

```
1. Market State Update
   (SESSION_OPEN: 用 raw open 更新 last_price; BAR_CLOSE/SESSION_CLOSE/AFTER_CLOSE: 用 raw close)
2. Pending Order Fill
   (BrokerSimulator.process_orders: 只有 eligible_at <= now 的 open orders)
3. Data Visibility Update
   (AsOfDataView 的 now = 当前事件 timestamp)
4. Strategy Signal Generation (BAR_CLOSE / AFTER_CLOSE 才调用 strategy_fn)
5. Signal -> OrderIntent -> Order (generated_at <= now 的 signal 才提交)
6. Ledger Mark (snapshot)
```

## Same-timestamp deterministic ordering

同 timestamp 冲突按以下 key 排序（不依赖 Python dict 顺序）：

- Orders: `(priority, sequence, order_id)`
- Signals: `(generated_at, signal_id)`

## Fill ordering

同 timestamp 的 open orders 按上述 order key 顺序尝试成交；先到先得。后续若做 Order Netting，会引入 sequence_number 与 netting 规则（见 Multi Strategy）。

## Corporate Action (future)

预留位置在 `Market State Update` 之前：

```
0. Corporate Actions (作用于 Position / Lot / Cash / Open Orders)
```

V2 当前 UNSUPPORTED；数据就绪后必须插在最前，保证除权除息先于任何成交和估值。
