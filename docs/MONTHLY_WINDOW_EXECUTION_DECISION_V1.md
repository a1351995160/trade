# 固定月末候选外窗：已获准访问及门槛适用性

## 实际进展

用户回答“允许”已登记为限定窗口释放回执：2025-08-01至2026-07-31，仅MONTHLY_REVERSAL_HOLD_20，之前21个独立session预热。2026-08-01起仍封存。回执绑定原Objective父回执、原候选事前合同、上一份具体方案哈希、实际任务身份和批准文本；旧政策、消费、期限、Windows OPEN不变。没有再请求同一读取批准。

复用原BaoStock Provider和Windows资源worker完成实际请求：独立日历确定预热起点2025-07-03；固定探针sh.600000的RAW/HFQ各263条，其中获准验证窗口各242条，末日均2026-07-31。响应、查询参数、哈希和实际访问记录均保存。这证明一个固定样本能够取得，不证明全股票池覆盖、时点或公司行动条款完整。

私有证据根：`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/monthly-independent-window-v1`。

- WINDOW_RELEASE.json：实际用户窗口释放，尚未登记账户增量预算。
- ACQUISITION_CODE.json：查询前冻结的源码与固定探针身份。
- responses/calendar.json、probe-3.json、probe-1.json：原响应及同名started/access回执。
- CALENDAR_WINDOW.json、PROVIDER_COVERAGE.json：日历与输入覆盖摘要。
- resources/probe.started.json、probe.completed.json：实际worker资源回执。
- CALENDAR_GATE_APPLICABILITY.json、CALENDAR_GATE_ACCESS.json：仅日历的理论上限及读取记录。

## 新发现的具体冲突

原`degraded_train_v1.feasibility_verdict`要求完整路径>=30、开仓日>=20、证券>=2。原月末公式只在独立日历月末产生候选，并于下一session开盘。

依据此次实际日历，即使乐观假设每个信号月末均有三只股票可成交，窗口内信号也至多产生11个开仓日、33条完整候选路径；即使额外纳入预热末日信号，上限也只有12日。向原裁决函数提交这些明确标为合成的理论上限路径，仍返回INSUFFICIENT_EXECUTABLE_EVIDENCE。没有使用实际收益、实际排名或实际证券成交推断。

这是一年窗口与旧20日门槛不适配，不是策略失败或数据源拒绝。上一份方案漏检此条件。原门槛不可达，因此尚未进行全股票池下载、信号计算、账户预算登记或账户运行；不会先支出完整取数资源再返回同一个必然失败。

## 唯一新增待决定事项

建议仅为这次一年外窗创建版本化可行性规则：完整候选路径>=30、不同实际候选开仓日>=10、证券>=2；原TRAIN的20日门槛保留。10日是30条Top-3路径对应的最小日期数，也是该11个可能开仓日的一致数量级，在实际股票排名和收益读取前提出。它仅判断一次有限账户探索是否值得执行，不是统计样本充分性或正式资格标准。实际数据仍可能因容量、公司行动、缺字段等无法达到这些条件。

此决定尚未生效。只需确认是否允许上述独立窗口门槛，既有窗口、取数、额度、期限授权不用重批。不得把确认解释为新策略或增加额度。

若获准，继续原固定公式、阈值、Top-3、20-session持有、下一开盘、原费用/滑点/T+1/FIFO和hazard处理；预热仅初始化，首个窗口内月末信号最早2025-08-29、下一session开仓。复用已读日历和探针，准备真实历史股票池/状态、RAW/HFQ及调整事件证据；正式在原预算组件登记1主+1确证工程修复，不挪旧余额。数据180分钟、计算90分钟、单worker900秒/2048MiB/数值线程1及原到期优先。

## 验证与状态

11项相关测试通过：窗口/候选/预热/资源/身份边界、到期、撤销、默认封存不变及月末上限证明。实际worker退出0。没有触碰旧训练/探索结果，没有跑V4或价格绩效。

ACCOUNT_BACKTEST_STARTED=false；ACCOUNT_BACKTEST_COMPLETED=false；本次MAIN/REPAIR曝光均0。新窗口行情已发生真实信息访问，不能再声称从未读取；尚未计算该窗口策略表现。

READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false；AUTONOMOUS_STRATEGY_GOAL_COMPLETED=false。已有成本脆弱性、历史消费/缺项/曝光与Windows OPEN全部保留。
