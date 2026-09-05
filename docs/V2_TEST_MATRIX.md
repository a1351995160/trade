# V2 TEST MATRIX

运行命令：`python -m pytest tests/golden tests/lookahead tests/engine tests/reality tests/regression -q`
结果：`30 passed in 0.72s`（本机）

## Golden Scenarios — tests/golden/test_golden_scenarios.py

| Test Name | Result | Duration | Purpose |
|-----------|--------|----------|---------|
| test_scenario_a_daily_close_signal_next_session_open_buy | PASS | 0.01s | 日线 T 日 15:00 信号 → T+1 09:30 open 成交；价格=raw open+滑点 |
| test_scenario_b_5min_signal_next_bar_execution | PASS | 0.01s | 5分钟 10:35 信号 → 下一根 10:40 bar open 成交，禁止同 bar 使用 |
| test_scenario_c_day1_buy_day1_sell_t1_reject | PASS | 0.01s | T+1：当日买入当日卖出订单被拒绝 |
| test_scenario_d_two_lots_day2_sellable_qty | PASS | 0.00s | 多 Lot：T1 买入 100、T2 再买入 100，T2 只可卖 100（FIFO） |
| test_scenario_e_stale_sell_order_not_sell_new_position | PASS | 0.00s | 旧卖单按 position_id 绑定；完全平仓后新仓不能被旧卖单卖出 |
| test_scenario_f_limit_and_suspension | PASS | 0.00s | 一字涨停买不进、一字跌停卖不出、停牌 DAY 订单拒绝 |
| test_scenario_g_fees_min_commission_stamp_and_slippage | PASS | 0.00s | 佣金 min 5、卖出印花税、双边滑点进入 Ledger |
| test_scenario_h_corporate_action_unsupported_flagged | PASS | 0.00s | Corporate Action 未实现时必须显式 UNSUPPORTED，不静默通过 |

## Lookahead — tests/lookahead/

| Test Name | Result | Duration | Purpose |
|-----------|--------|----------|---------|
| test_index_filter_no_same_day_close_leakage | PASS | 0.00s | P0 回归：V2 `_index_ok` 只读严格早于当日指数收盘；V1 `side='right'` 同日泄漏被禁止 |
| test_asof_guard_raises_on_future_read | PASS | 0.00s | AsOfDataView 读取 now 之后的数据抛 `LookaheadViolation` |
| test_future_truncation_feature_history_unchanged | PASS | 0.00s | 未来数据截断不改变历史特征值 |
| test_clock_daily_event_order | PASS | 0.00s | DAILY 事件顺序：SESSION_OPEN → BAR_CLOSE → SESSION_CLOSE → AFTER_CLOSE |
| test_clock_5min_events | PASS | 0.00s | 5MIN 事件顺序：48 根 BAR_CLOSE + SESSION_OPEN/SESSION_CLOSE/AFTER_CLOSE |
| test_daily_open_fill_does_not_depend_on_future_daily_volume | PASS | 0.00s | Daily Fill Timing：09:30 成交量使用前一日已知 volume，不随 T 日全天 volume 改变 |

## Engine — tests/engine/

| Test Name | Result | Duration | Purpose |
|-----------|--------|----------|---------|
| test_strategy_fn_full_buy_sell_flow | PASS | 0.01s | Strategy 回调 API 全流程：Signal→Intent→Order→Fill→Ledger，买入→T+1 卖出 |
| test_order_lifecycle_transitions | PASS | 0.00s | Order 状态机 CREATED→ACCEPTED→FILLED / REJECTED 等 |
| test_daily_fill_model_partial_fill | PASS | 0.00s | DailyBarFillModel participation 上限产生 PARTIALLY_FILLED |
| test_limit_up_reject_in_broker | PASS | 0.00s | 涨停开盘拒绝买入订单 |
| test_suspension_day_order_rejected | PASS | 0.00s | 停牌 DAY 订单拒绝 |
| test_universe_is_eligible_snapshot | PASS | 0.00s | UniverseService PIT 集合按日期快照正确 |
| test_risk_universe_gate_rejects_non_member | PASS | 0.00s | RiskManager 订单前 Universe 复查拒绝非成员 |

## Reality — tests/reality/

| Test Name | Result | Duration | Purpose |
|-----------|--------|----------|---------|
| test_st_stock_limit_5pct | PASS | 0.00s | ST 股票涨跌停 5% |
| test_board_limit_by_prefix | PASS | 0.00s | 主板 10%、创业板/科创板 20%、北交所 30% |
| test_pit_st_state_switch | PASS | 0.00s | SecurityMaster.as_of 支持 ST 状态 PIT 切换 |
| test_limit_up_opened_daily_conservative | PASS | 0.00s | 日线涨停开板但无法确定开板时点 → 保守拒绝 |
| test_suspension_model | PASS | 0.00s | 无 bar / volume=0 / 一字无法交易判定 |
| test_trade_crossing_corporate_action_is_flagged | PASS | 0.00s | CorporateActionGuard：卖出跨越除权日 → TRADE_REALITY_UNSUPPORTED |
| test_trade_without_corporate_action_stays_ok | PASS | 0.00s | 无除权事件的交易保持 OK |

## Regression — tests/regression/

| Test Name | Result | Duration | Purpose |
|-----------|--------|----------|---------|
| test_legacy_signal_adapter_maps_to_next_session_open | PASS | 0.00s | Legacy Signal(`signal_date:int`) → V2 Signal（T 15:00 + NEXT_SESSION_OPEN） |
| test_engine_determinism_same_inputs_same_outputs | PASS | 0.01s | 同输入同 seed 下，Trade/Equity 完全一致 |
