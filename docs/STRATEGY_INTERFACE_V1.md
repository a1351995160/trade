# 统一策略接入接口 V1

> 当前完整迁移状态见 [现有账户统一接入完成说明 V2](STRATEGY_INTERFACE_MIGRATION_V2.md)。下文保留V1交付历史；其中网格、股票未迁移以及无公共CLI的限制已被V2取代。

## 已落地的工作方式

新增策略实现一个本地规则插件，通过 `strategy_interface_v1.prepare / evaluate_novelty / run` 接入。无需在账户循环、预算服务或报告中增加策略名称分支。后端是能力边界，不是“任何策略都必定能运行”的承诺。

本轮为工程改造，没有注册真实新预算、读取行情或重新计算真实策略结果。原四候选和共享基准经兼容入口使用新公共循环；旧双资金桶网格仍保留原入口和历史合同，未宣称已迁入单资金桶后端。

## 策略提供什么

- `strategy_id` 与集中 `parameters`：规则身份及参数，修改后产生新的计划身份。
- `Requirements`：资产类别、频率、价格视图、输入字段、预热长度、交易意图种类、执行时点和额外能力。
- `source_files`：除插件本体外的规则依赖源码；本体自动绑定hash，外部辅助模块须显式列出。Python插件为可信本地代码，不是沙箱，不可接入未经审阅的不可信代码。
- `on_close(Context) -> Decision`：读取截止当前收盘的行情副本、独立交易日历、账户副本和自身状态，返回决策。未来日历日期可见，未来行情不可见；日历证据仍需上层输入服务验证。

决策包括原因、下一份策略状态及至多一个交易意图：`TargetWeight` 表达目标仓位（可声明禁止加已有持仓）；`QuantityOrder` 表达买卖份数并支持卖出绑定lot。不交易使用 `intent=None`；零目标仓位才表示退出。策略不直接改变现金、订单或lot。元数据不能覆盖执行侧的资金桶上限。

## 当前后端能力

`ETFDailyBackend` 复用原Broker、OrderManager、手续费、滑点、公司行动与账本，负责撮合、现金、100份整手、T+1/FIFO、前日容量、分红和拒单。当前配置仍为510300、原TRAIN窗口、10000本金、1000永久现金、9000单资金桶、目标权重上限90%、次session开盘。

支持目标仓位与数量意图（包括分批买入、按lot部分卖出），每个收盘至多一条意图。90%为信号定仓限制，不能保证跳空后的实际仓位严格等于目标。费用、价格筛选与原模型保持一致。

分钟数据、复权成交视图、期货、限价单、独立多资金桶等未支持的声明在 `prepare` 阶段拒绝；不默默降成日线市价单。其他市场或新的会计行为应实现/扩展对应后端能力一次，再供所有规则复用。该版本没有宣称旧股票批次、双资金桶网格、分钟或衍生品全部完成迁移。

## 同一执行流程

1. 由既有数据服务提供合法输入身份；`prepare(plugin, backend)` 不读行情、不写预算，返回规则、依赖源码、后端合同及其hash构成的计划。
2. 对取得授权的历史设计投影调用 `evaluate_novelty(plans, history)`，复用原 `CandidateNoveltyGateV2`。不扫描历史报告，不因新接口清零历史曝光；判重通过不代表机制独立。
3. `StrategyBatchGovernanceV1` 接收已具体批准的 `approved_plan_ids`、有效期和输入身份，按任意固定插件清单调用原预算组件登记用途，每计划一次，无修复/搜索余量。该服务不解释聊天、不凭配置代替用户批准。原Objective消费不改写。
4. 既有受限worker负责资源限制、输入/源码冻结与结果归档。在计算前 `start(name)` 消费；传入 `active_check=lambda: governance.active_execution(name)`。没有START、已结算、重复运行、撤销、到期或合同不符均拒绝。
5. 公共 `run` 检查能力、计划、输入及消费回执，调用后端，返回完整账户结果及固定报告。失败也必须由调用方结算；不能因为异常退回已消费额度。
6. 保存实际结果后调用原 `settle`。接口的内存报告明确使用 `in_memory` 引用，不冒充已生成文件；worker归档后才能提供文件路径。公开接口本身不下载数据、不启动无资源限制的后台任务。

本轮未新建另一套worker/CLI/后台调度。新插件由现有调度方调用公共Python API；调度方仍负责已有授权、限时限内存和不可变归档，不应复制每策略执行脚本。

## 接入示例（不构成执行许可）

```python
from chanlun_trader.research_factory.strategy_interface_v1 import prepare, run
from chanlun_trader.research_factory.etf_grid_account_v1 import ETFDailyBackend
from chanlun_trader.research_factory.etf_trend_risk_hypothesis_v1 import ETFPolicyStrategy, FAMILY

plugin = ETFPolicyStrategy(FAMILY[0])
backend = ETFDailyBackend()
plan = prepare(plugin, backend)  # 能力/合同检查；没有真实试验
# 以下只能在原受限worker内，绑定具体输入、判重与合法消费后调用：
# result = run(plugin, backend, frame=approved_frame, actions=approved_actions,
#              input_identity=approved_input_identity,
#              active_check=lambda: governance.active_execution(plugin.strategy_id))
```

新的数量型规则只需实现插件返回 `QuantityOrder`；测试中的插件已经完成“买200、按同一lot卖100、再卖100”，没有修改账户循环、治理服务或报告。

## 实际验证与恢复

26项相关回归通过3.87秒；补充冻结前完整输出比对与撤销/预热用例后，接口专项14项通过8.15秒。二者有重叠，不宣称40个独立测试。

五组完整合成输出与重构前已冻结实现逐项SHA256一致，覆盖逐日权益、订单、成交、决策和账本。测试基线来自family-v1/account-v1/source-archive中的旧账户源码；合成输入120预热+160 session，没有读取真实行情。该一致性只覆盖所测输入，不是任意策略的数学等价证明。

另外验证：新数量插件接入、lot部分卖出、行情/账户副本隔离、未消费拒绝、消费不回放、旧12次消费不变、精确判重、改参数无法复用计划、预热不足和未支持能力拒绝。

本轮修改现有ETF账户、ETF规则、ETF治理模块；新增一个公共接口模块和一个专项测试文件。新增文件是共用基础设施，不是每策略再建引擎。原真实结果/结算/失败和旧预算不改写；源码已变化，旧回执不能重跑当前代码。

恢复：从 `etf-grid-train-v1/family-v1/account-v1/source-archive/src/chanlun_trader/research_factory/` 恢复本轮改动的3个已有模块（etf_grid_account_v1.py、etf_trend_risk_hypothesis_v1.py、etf_account_governance_v1.py），移除新增strategy_interface_v1.py及对应测试。仅在确认这些文件没有后续改动时执行；不要git reset或删除真实证据。
