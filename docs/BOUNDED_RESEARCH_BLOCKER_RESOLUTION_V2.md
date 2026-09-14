# 有限研究前置解阻 V2

承接 042364c3c94b69ef5b484b40e5dfd37cdc0e182b。用户明确批准 V2 附件 A/B/C 准备工作；具体研究方法/计划未批准。原截止投影 2026-09-14T10:05:03+08:00 不重置。五份草稿均已在附件指定路径读取，仍为未绑定设计。旧 62 项回归及三轮阻断证据保留。

## A 历史盲化对账

已实现只读服务 history_blind_reconciliation，复用 SearchBudgetRegistryV1 与 TrialRegistryV1 reader；只读指定 research_factory 子目录的明确账本/身份文件模式。自由文本、classification、收益和精确 p 值不进入投影或异常日志；其他目录引用不跟随。真实投影不进入 Git。

实际输出：E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2/HISTORY_BUDGET_BLIND_RECONCILIATION_FINAL.json。144 个来源文件，6 个预算注册表，观察到 16 个唯一已曝光 Trial。登记时的 performance_accessed=false 是登记事实，不代替实际 Trial 事件；按整个事件历史确定是否曾曝光。

政策对应的 RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1 为推荐历史关联根，理由是原冻结政策 research_objective_scope 明确指定，而非表现或余额。对应 RUN_AUTONOMOUS_ALPHA_AFTER_SAMPLE_POLICY_V2_B01 预算 objective 桶 limit=12、used=12、reserved=0、remaining=0；结算 CONSUMED=12、RELEASED=1。Objective 原件 max_total_trials=8，与预算12存在需要授权 lineage 解释的差异，不能自动修正。

其余分支余额分别 4、4、4、3、3，不属于可任意转移的公共额度。不能换到这些 Objective 刷新本任务预算。不同批次目录（特别是 AI_HANDOFF）没有本地账本，不证明历史记录丢失或试验为零；需要解析其真实预算/Trial引用。全局剩余和训练/Validation曝光分拆保持 UNKNOWN。旧文档23次/18因子不能被本次16个已观察Trial覆盖或替代。

需要进一步取得的精确外部权威路径：从 canonical executor 的 report_dir 规则生成的各 Trial performance_access_gate.json、pre-performance contract，以及原治理确认/预算变更回执。它们位于 reports 等当前A许可之外；最终确认包列出逐项路径和只需的字段，不泛扫或读取这些原件。预计历史关联根无剩余额度，任何新增性能试验均需原服务的明确新增额度，不先写新桶。

测试：test_history_blind_reconciliation.py 3 passed；覆盖嵌套绩效及分类不输出、原reader只读、缺失计数拒绝默认0。隔离探针全部0。该投影是诊断，不认证历史完整性，也不授予任何策略资格。

## B 来源与因子语义

已按事前清单实际读取486个训练PIT分区与5036个TDX文件，形成2370849行诊断快照；146个历史成员缺源，原列表保留。日期键定位与允许范围内价格读取分开审计，未整读/整hash混合.day文件。available_at、公司行动与成交单位缺证据保持UNKNOWN，不认证可执行性。RETURN_5D真实编译器的合成对照证明限定计算等价，不证明公司行动调整等价；旧registry和caller未改。

## C 统计提案与实际失败校准

新增隔离原型statistical_proposal_v3，定义共同stationary时间块、边界居中单侧均值检验、完整家族BY、固定训练内walk-forward、实际标签结束日purge及20session过去信号placebo。没有接入正式runner、旧政策或资格裁决。

5个纯合成场景各200次模拟，每次252session、3成员、10000抽样。IID、AR0.6与10日重叠场景raw拒绝率分别8.5%、9.5%、8.5%，未达到预注册诊断要求；方法不可启用。通过8项实现测试不代表统计适用性通过。首次校准遗漏导入模块的源码绑定，补齐绑定后以相同种子原样复核；逐场景计数一致，两轮原件均保留。

## 持久交付和恢复

外部交付根：E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2。

- RESEARCH_PLAN_CONFIRMATION_PACKET.md：唯一具体计划包，包含资源/原到期/精确缺项/合法取得路径；状态尚不可确认启用。
- DATA_PROVENANCE_AND_TIME_EVIDENCE.md、FACTOR_PRICE_SEMANTICS_COMPATIBILITY.md：字段证据和版本影响。
- STATISTICAL_METHOD_CONTRACT_V3_PROPOSED.md：完整方法、原理来源、合成校准与适用性失败。
- statistical-calibration-v3-bound-source：正式绑定源码的预注册与合成结果；旧输出保留。
- checkpoint-v2.json：本轮事实检查点，不是授权回执；优先于旧checkpoint中已过时的五份路径未知/未对账描述。

恢复时先核对此处checkpoint与真实预算/到期，不重复同一元数据检查。缺失的外部治理/曝光回执需受控盲化取得，来源缺证据需限定导出；当前没有合法真实试验下一步。不得将新增40次上限当作已入账余额，不实现无治理的真实回测，不以文档完成宣称解阻完成。AUTONOMOUS_STRATEGY_GOAL_COMPLETED、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED均false。
