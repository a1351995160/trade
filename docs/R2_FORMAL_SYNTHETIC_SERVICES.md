# 正式创建至 R2 合成闭环

本阶段承接 A 的限定批准，在新建临时 synthetic 根完成真实服务组合。它不授权真实研究，也不代表 R3、全路线或最终双平台认证完成。

## 已验证的链路

初始父研究及设计能力资料由测试驱动器生成；新目标通过实际 v2 review/confirm 创建，预算、家族、lineage 和回执均由原正式事务生成。之后使用同一正式目标进入设计、人工设计批准、冻结、物化确认。目标、预算和家族文件在物化结束后逐字节保持创建时内容，未补字段、扩家族或补写成功回执。

实际合成行情和两路 PIT 原始资料经 build_normalized_state 生成；RealSampleFeasibilityProviderV1 读取实际分片，CandidateSampleFeasibilityPreflightV1 计算边界。测量结果支持结构审计资料，不预填 PASS。StructuralEntryServiceV1 的真实结果为 PASS；随后实际新颖性来源声明、快照和确认仍不能替代预测授权，授权前 readiness 明确不可启动。

通过实际预测治理确认与新颖性绑定启动服务后，原 TrialLedger、SearchBudget 和 Multiple Testing 登记执行。实际 CanonicalPredictiveExecutorV1 调用两个独立 engine，完成原 bootstrap/BH/FinalResearchAdjudicator、策略 registry 和盲化失败提取。

固定合成输入的最终分类为 BLOCKED（RAW_BOOTSTRAP_NOT_SUPPORTED），本地分类为 REJECTED；不是可用策略。验收要求正确完成并报告拒绝/阻断，不通过调参或重跑取得 RESEARCH_PASSED。每条完整路径实际性能准入 1 次、两个 engine，Objective/批次/家族各消费 1、预留归零；真实合格策略和真实观察天数均为 0。

## 中断与重放

- 正常完成后，新进程通过正式 confirm/recover 重放：engine 和性能准入新增计数均 0，预算及 TrialLedger 字节不变。
- 另一新根在实际性能准入后、engine 方法体执行前退出 73。新进程先由既有协议结算工程失效，再取得实际 resume_preview/confirm_resume，完成同一个 Trial。恢复过程两个 engine，未新建 Trial、未增加预算额度，最终消费仍为 1。没有伪造恢复成功记录。
- 以上测试驱动器明确扮演合成人工操作方；使用 import-time isolation 的独立进程，不加载 pytest conftest 的替代 runner。原始 stdout/stderr 保留，子进程单次上限 60 秒，不增加原跳过项或隔离白名单。

## 缺陷与失败证据

原 PITStateMap 把源日期集合与执行窗口要求为完全相等，较长历史和预热记录导致窗口内完整双路状态被误报缺失。新增最小回归先得到 1 failed/1 passed，再只投影窗口日期；窗口内任一来源缺失仍拒绝，窗口外日期不可交易。未修改原数据文件、available_at、状态枚举或其他治理语义。

新工作区第一轮缺少 universe policy 输入，在性能准入前失败；第二轮遇到上述 PIT 窗口问题，在性能准入后失败、未运行 engine。这些已失败的 Trial 和原始日志全部保留，后续每轮从新的明确合成根创建新目标，不恢复旧批准或免费重跑。universe policy 合成文件绑定当前合同的 universe_rule 和实际来源，不能冒充真实政策核验。

## 需求到证据

| 需求 | 代码/测试 | 证据 | 限制 |
|---|---|---|---|
| 正式创建及未补字段 | objective_execution_binding、r2_formal_fixture | 创建服务回执与 unchanged_after_materialization 哈希 | 初始父研究是合成 fixture |
| 真实结构/启动/两个 engine/裁决/registry/失败回流 | r2_service_worker；test_r2_formal_services | 子进程 profile 计数、canonical Trial/预算、r2-safe-summary.json | BLOCKED，无合格策略 |
| 中断、人工恢复、重放 | r2_recovery_worker；test_r2_formal_services | 退出73、同Trial恢复、预算/Trial字节比对、原始流 | 不是所有 B 批次并发/撤销竞争的认证 |
| 较长 PIT 历史窗口投影 | test_pit_window_projection | r2-pit-window-red.log、r2-pit-window-green.log/XML | 只修窗口集合误拒绝，不泛化真实数据认证 |
| 安全与 caller 回归 | test_r1_snapshot_integration、test_r1_caller_io_parity | r2-formal-stage.log/XML：148 passed，149.82s，三项隔离探针0 | 当前 Windows 本地；新 HEAD 双平台尚待整体阶段 |

原始证据根 `E:/llmwiki/roadmap-engineering-evidence`；完整组合的逐测试子进程日志位于 r2-formal-stage-process/r2-formal。旧 e288746 ZIP 不改写。回滚本阶段提交后，新创建协议 A 仍可用；新 Trial 历史保留，不能删除或转为未消费。完整合成工程范围仍 PARTIAL，B 有界批次继续 IMPLEMENTING；READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false，真实数据 NOT_VERIFIED，既有 OPEN 事件全部保留。
