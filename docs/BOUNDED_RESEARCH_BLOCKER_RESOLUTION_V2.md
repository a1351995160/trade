# 有限研究前置解阻 V2

承接 042364c3c94b69ef5b484b40e5dfd37cdc0e182b。用户明确批准 V2 附件 A/B/C 准备工作；具体研究方法/计划未批准。原截止投影 2026-09-14T10:05:03+08:00 不重置。五份草稿均已在附件指定路径读取，仍为未绑定设计。旧 62 项回归及三轮阻断证据保留。

## A 历史盲化对账

已实现只读服务 history_blind_reconciliation，复用 SearchBudgetRegistryV1 与 TrialRegistryV1 reader；只读指定 research_factory 子目录的明确账本/身份文件模式。自由文本、classification、收益和精确 p 值不进入投影或异常日志；其他目录引用不跟随。真实投影不进入 Git。

实际输出：E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2/HISTORY_BUDGET_BLIND_RECONCILIATION_FINAL.json。144 个来源文件，6 个预算注册表，观察到 16 个唯一已曝光 Trial。登记时的 performance_accessed=false 是登记事实，不代替实际 Trial 事件；按整个事件历史确定是否曾曝光。

政策对应的 RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1 为推荐历史关联根，理由是原冻结政策 research_objective_scope 明确指定，而非表现或余额。对应 RUN_AUTONOMOUS_ALPHA_AFTER_SAMPLE_POLICY_V2_B01 预算 objective 桶 limit=12、used=12、reserved=0、remaining=0；结算 CONSUMED=12、RELEASED=1。Objective 原件 max_total_trials=8，与预算12存在需要授权 lineage 解释的差异，不能自动修正。

其余分支余额分别 4、4、4、3、3，不属于可任意转移的公共额度。不能换到这些 Objective 刷新本任务预算。不同批次目录（特别是 AI_HANDOFF）没有本地账本，不证明历史记录丢失或试验为零；需要解析其真实预算/Trial引用。全局剩余和训练/Validation曝光分拆保持 UNKNOWN。旧文档23次/18因子不能被本次16个已观察Trial覆盖或替代。

需要进一步取得的精确外部权威路径：从 canonical executor 的 report_dir 规则生成的各 Trial performance_access_gate.json、pre-performance contract，以及原治理确认/预算变更回执。它们位于 reports 等当前A许可之外；最终确认包列出逐项路径和只需的字段，不泛扫或读取这些原件。预计历史关联根无剩余额度，任何新增性能试验均需原服务的明确新增额度，不先写新桶。

测试：test_history_blind_reconciliation.py 3 passed；覆盖嵌套绩效及分类不输出、原reader只读、缺失计数拒绝默认0。隔离探针全部0。该投影是诊断，不认证历史完整性，也不授予任何策略资格。
