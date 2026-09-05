# BACKTEST ENGINE V2 ARCHITECTURE

状态: `BT_ENGINE_V2_STATUS = READY`（核心日线 + 5分钟合成路径；详见 Known Limitations）
版本: 2.0.0

## 1. 设计目标

服务 A股 日线 + 5分钟、盘后事件、T+1、多策略 Portfolio、真实费用、涨跌停/停牌、Paper Trading。
原则: Correctness > No Lookahead > Reproducibility > Execution Realism > Extensibility > Performance > Fancy Architecture。

## 2. 混合事件驱动边界

```
Raw/PIT Data (TDX/TQ + DataAdapter)
        ↓
Feature Research (numpy/pandas 批量，保留现有体系)
        ↓
Signal Generation (批量)
        ↓
==================== EVENT-DRIVEN BOUNDARY ====================
        ↓
Signal (generated_at >= 所有输入 available_at)
        ↓
OrderIntent (execution_policy)
        ↓
PortfolioTarget / PositionSizer
        ↓
RiskManager (pre-trade)
        ↓
OrderManager (order_id + lifecycle)
        ↓
BrokerSimulator (Fill/Fee/Slippage/PriceLimit/Suspension/T+1)
        ↓
PortfolioLedger (SINGLE SOURCE OF TRUTH)
        ↓
Metrics (只读 Ledger)
```

研究层不全部事件化；从 Signal 开始全部进入事件驱动执行内核。

## 3. 模块清单

| 文件 | 职责 |
|------|------|
| `engine/time_types.py` | TradingTimestamp(Asia/Shanghai tz-aware), TradingCalendar, TradingSession, TradingClock (DAILY/5MIN) |
| `engine/asof.py` | MarketDataStore(raw/qfq/hfq/5min), AsOfDataView 未来函数防火墙, LookaheadViolation |
| `engine/signal.py` | Signal / Side / ExecutionPolicy / OrderIntent / PortfolioTarget / TargetType |
| `engine/order.py` | Order / OrderStatus(CREATED..EXPIRED) / TimeInForce(DAY,GTC_SIM) |
| `engine/order_manager.py` | 唯一订单生命周期管理，状态转换集中，产生 OrderEvent |
| `engine/fill.py` | Fill / DailyBarFillModel / FiveMinuteFillModel (participation) |
| `engine/fee.py` | FeeBreakdown / ChinaAStockFeeModel (佣金+最低5元+卖出印花税) |
| `engine/slippage.py` | FixedBpsSlippage (兼容旧 0.1%) |
| `engine/position.py` | Position / PositionLot (sellable_from 显式) |
| `engine/ledger.py` | PortfolioLedger (Cash/Lots/Positions/Fees/Realized/Unrealized/Equity/Turnover) |
| `engine/risk.py` | RiskConfig / RiskManager (Universe, Cash, Exposure, Position Limits) |
| `engine/universe.py` | UniverseService.is_eligible / snapshot |
| `engine/security_state.py` | SecurityState / SecurityMaster.as_of / ChinaPriceLimitModel / SuspensionModel |
| `engine/broker.py` | BrokerSimulator (接受/拒绝/等待/成交判定/T+1/涨跌停/停牌/费用/滑点) |
| `engine/engine.py` | BacktestEngineV2 / EngineConfig / BacktestRunContext / EngineResult |
| `engine/event_log.py` | BacktestEventLog |
| `engine/interfaces.py` | DataFeedInterface / BrokerInterface (Backtest/Paper parity) |
| `engine/legacy.py` | LegacySignalAdapter (V1 Signal -> V2 Signal) |

## 4. 时间模型

- 统一 `pd.Timestamp` (Asia/Shanghai tz-aware)。
- `TradingClock` 模式:
  - DAILY: SESSION_OPEN(09:30) -> BAR_CLOSE(15:00) -> SESSION_CLOSE(15:00) -> AFTER_CLOSE(15:30)
  - 5MIN: SESSION_OPEN(09:30) -> BAR_CLOSE(09:35..15:00, 每5分钟) -> SESSION_CLOSE(15:00) -> AFTER_CLOSE(15:30)
- 数据可见性:
  - 日线 bar 在当日 15:00 后对 Strategy 可见 (generated_at >= 15:00)
  - 5分钟 bar 在 bar 完成后对 Strategy 可见 (generated_at >= bar close)
  - 盘后事件 (LHB等) generated_at >= available_at

## 5. Signal -> Order -> Fill -> Ledger 流程

1. `Signal` 只表达 Alpha 观点 (strategy_id, signal_id, symbol, generated_at, direction, score, execution_policy)。
2. `OrderIntent` 表达执行意图 (side, target_type, target_value, created_at, execution_policy)。
3. `PortfolioTarget` + `PositionSizer` (FixedSlotSizer 映射旧 max_positions) 决定数量。
4. `OrderManager.create_order` -> `Order` (CREATED) -> `submit` (SUBMITTED -> ACCEPTED)。
5. 当时钟到达 `eligible_at`，`BrokerSimulator`:
   - 检查 `position_id` 绑定（stale order 防御）
   - 检查 T+1 sellable lots
   - 检查 Universe / Cash / Exposure (RiskManager)
   - 检查停牌 / 涨跌停 (PriceLimitModel)
   - `FillModel.try_fill` 给出价格和数量
   - `SlippageModel.apply` 滑点
   - `FeeModel.calc` 费用
   - 生成 `Fill`，`Ledger.apply_fill` 记账
6. `PortfolioLedger` 是唯一事实来源；`Metrics` 只能读 Ledger。

## 6. A股 Reality

- T+1: `PositionLot.sellable_from = 买入日 + 1 交易日 09:30`；`Ledger.sellable_quantity()` 只汇总可卖 lot。
- 涨跌停: `ChinaPriceLimitModel` 按 SecurityMaster.as_of 的 PIT ST 状态与 board 决定 5%/10%/20%/30%。
  - 日线数据使用 `CONSERVATIVE_DAILY_MODEL`：一字板 100% 拒绝；开板涨停/跌停开盘保守拒绝。
- 停牌: `SuspensionModel` 无 bar / volume<=0 统一判断。
- 费用: `ChinaAStockFeeModel` 佣金双边最低5元 + 卖出印花税。
- 滑点: `FixedBpsSlippage` 默认 0.1%。

## 7. Raw / QFQ 分工

- Broker 执行全部使用 `MarketDataStore.daily_raw`（真实未复权交易价格）。
- Strategy 特征默认使用 `daily_qfq`（feature_price_mode='qfq'），通过 AsOfDataView 访问。
- 禁止把复权价格用于成交。

## 8. Corporate Action

- V2 Phase 4 提供 `SecurityMaster` PIT 状态与 `PriceLimitModel` 的 PIT ST/board。
- 现金分红/送股/转增/拆股/退市的 Ledger 级处理未实现：`UNSUPPORTED`，不静默忽略。
- 接入 `get_divid_factors` 数据后实现 `CorporateActionProcessor`，作用于 Position/Lot/Cash/Open Orders。

## 9. Multi Strategy

- 所有对象现在就有 `strategy_id`。
- 单策略可直接跑；多策略使用 Strategy Sleeve（每策略独立 budget/targets/PnL attribution，Portfolio 聚合）。
- 同股冲突 Phase 1 为 `NOT YET SUPPORTED`，但接口已预留。

## 10. Paper Trading Parity

- `interfaces.py`: `DataFeedInterface` / `BrokerInterface`。
- Backtest: `MarketDataStore` + `BrokerSimulator`。
- Paper: `LiveDataFeed` + `PaperBroker`（复用同一 Strategy / Risk / PortfolioConstruction）。

## 11. Known Limitations

1. 集合竞价不做精确模拟（数据不足）。
2. NEXT_BAR_VWAP 暂不启用（无 VWAP 数据）。
3. Corporate Action Ledger 级处理 UNSUPPORTED。
4. 5分钟真实 TDX 数据尚未接入 engine.MarketDataStore 加载器（接口已留）。
5. Daily 容量模型为粗略 participation；5分钟使用 bar volume participation。
6. 没有 Level2 队列信息；不假装能模拟排队成交。

## 12. 目录

```
src/chanlun_trader/engine/
    __init__.py time_types.py asof.py signal.py order.py order_manager.py
    fill.py fee.py slippage.py position.py ledger.py risk.py universe.py
    security_state.py broker.py engine.py event_log.py events.py
    interfaces.py legacy.py sizing.py
tests/
    golden/ lookahead/ engine/ reality/ regression/
docs/
    BACKTEST_ENGINE_V2_ARCHITECTURE.md
    ENGINE_EVENT_ORDER.md
    BACKTEST_ENGINE_V2_ACCEPTANCE.md
    V1_V2_BACKTEST_DIFF_REPORT.md
    BACKTEST_LIVE_PARITY_DESIGN.md
```
