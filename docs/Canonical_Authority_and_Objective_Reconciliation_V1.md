# Canonical Authority 与 Objective Reconciliation V1

## 目标

本版本提供 Research Factory 的只读事实对账层。它将 Objective 定义、治理证据、可执行 Candidate 合同、TrialLedger、预算、ArtifactGraph 与运行投影拆开读取，生成不改变研究状态的有效 Objective 状态。

本版本不自动修复冲突，不生成或冻结 Candidate，不生成 Durable Frozen Candidate Contract，不运行 Structural Preflight、Predictive Trial、Trial、AI、Daemon 或 Orchestrator，也不修改 Budget、Validation 或 Final Adjudication。

## Authority 边界

| 对象 | Canonical authority | 允许作为投影的对象 | 关键规则 |
|---|---|---|---|
| Objective Definition | data/research/research_factory/objectives/<objective_id>.json | lifecycle 与 next-action 展示 | JSON 是定义/创建事实；动态生命周期字段不能单独决定最终状态 |
| AI Research Design | AI_RESEARCH_DESIGN_PROPOSAL.json | AI_RESEARCH_DESIGN_STATE.json | AI_DESIGN_READY 不等于 AI_DESIGN_APPROVED |
| AI Design Approval | `reports/research_evolution/ai_design/<objective_id>/AI_DESIGN_APPROVAL_RECEIPT.json`（`AI_DESIGN_APPROVAL_AUTHORITY`） | AI design state、Objective lifecycle、Daemon / Orchestrator、Console | 只有绑定当前 design/source context hash 且 receipt integrity PASS 的不可变回执可以证明批准；没有凭证时输出 AI_DESIGN_APPROVAL_EVIDENCE_MISSING，不得推断批准 |
| Candidate Governance | Proposal、Review、Freeze Receipt、CANDIDATE_REGISTRY.json | Candidate state | 这些是治理/库存事实，不是 executable contract |
| Executable Candidate | DurableFrozenCandidateContractV1 + matching Materialization Confirmation Receipt | daemon contract cache | 必须唯一匹配 objective_id、candidate_id、candidate_hash、Preview durable_contract_hash，并通过 from_dict()、provider_candidate_payload() 和 Receipt identity reconciliation |
| Structural Preflight | canonical structural reconciliation | daemon/orchestrator structural state | 不把运行中的 Structural 状态提升为事实 |
| Predictive Authorization | 持久化授权凭证 | daemon/orchestrator predictive state | 不从 Structural PASS 推断授权 |
| Trial Lifecycle | factory_trial_ledger.json | daemon/orchestrator checkpoint、Trial Registry | 按 trial_id、Candidate identity、事件和终态对账，不按时间戳覆盖 |
| Budget | SearchBudgetRegistryV1 | daemon/orchestrator budget view | 多个 registry 必须通过不可变治理/Objective receipt 或显式 canonical ref 选定，否则歧义 |
| Lineage | ArtifactGraph 与不可变 lineage | console、checkpoint canonical_refs | 缺边是 REPAIRABLE_INDEX_DRIFT；身份/哈希冲突是 CANONICAL_CONFLICT |
| Daemon / Orchestrator | 无，二者均为 RUNTIME_PROJECTION | — | checkpoint 不是 canonical truth |
| Autonomous Research Control Plane | 无，Decision 与 Action Execution Receipt 均为 RUNTIME_DECISION_EVIDENCE | Console、控制平面状态、下一动作与恢复记录 | 只读 canonical facts；不能替代 Objective、Candidate、Trial、Budget、Structural、Predictive、Final、Prospective 或 Real Order authority |

## Objective Dialect

ObjectiveDialectClassifierV1 输出：

- DEFINITION_ONLY：只有 Objective 定义事实；
- EVOLUTION_MANUAL_CREATED：人工创建的 Evolution Objective，等待人工治理动作；
- GOVERNANCE_EXECUTION_READY：已有治理执行授权与执行语义；
- LEGACY_UNKNOWN：身份或方言字段不足、互相矛盾或无法确认。

所有输出都带 dynamic_lifecycle_authoritative=false。未知方言只报告 OBJECTIVE_DIALECT_UNKNOWN，不触发自动恢复。

## 有效状态推导

有效状态不改写任何输入来源。典型路径如下：

1. Objective 存在但没有 AI Design：NEED_AI_RESEARCH_DESIGN；
2. AI Design 已生成且没有有效持久化批准：AI_DESIGN_AWAITING_CONFIRMATION，required_action 为 HUMAN_CONFIRM_AI_RESEARCH_DESIGN 且 required_action_supported=true；
3. AI Design 取得有效 APPROVED 回执：AI_DESIGN_APPROVED，required_action 为 GENERATE_CANDIDATE_PROPOSAL，safe_to_advance=true，但 structural_preflight_ready=false；
4. 已有治理 Freeze Receipt 与 Candidate Registry，但没有完整 Durable Contract：CANDIDATE_FROZEN_PENDING_EXECUTABLE_MATERIALIZATION；
5. 唯一完整 Durable Contract 通过两项校验：READY_FOR_STRUCTURAL_PREFLIGHT；
6. canonical Budget 耗尽或 Trial 已终态时，不能采信 stale 的 daemon ACTIVE/RUNNING 投影。

Candidate 治理 Freeze 与 executable freeze 是两个独立闸门。Receipt-only 只能进入 `EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED`；只有 Preview、Receipt、Durable Contract 的 full identity/hash match 成立时，报告才会将 `structural_preflight_ready` 置为 true。

## 对账与冲突

Budget 对账检查 active、consumed、released reservation、reservation identity、performance access、Trial 终态和 bucket counts。Trial 对账检查跨 Ledger 来源的 identity 与 terminal status。ArtifactGraph 对账检查 Candidate 到 Durable Contract 的关键边。

冲突等级：

- CONSISTENT：已读取事实之间一致；
- PROJECTION_DRIFT：canonical 事实明确，运行投影滞后或不一致；
- REPAIRABLE_INDEX_DRIFT：实体身份一致，但 Lineage/索引边缺失；
- CANONICAL_CONFLICT：canonical identity、终态、预算权威或批准事实无法唯一确认，必须 fail closed。

Candidate、Trial 和 Artifact 只会被分类为 CURRENT_CANONICAL、HISTORICAL_CANONICAL、LEGACY、ORPHAN 或 UNRESOLVED；本版本不会删除孤儿或遗留对象。

## CLI 与报告

单个 Objective：

    python -m chanlun_trader.research_factory.objective_reconciliation --root . --objective-id <OBJECTIVE_ID>

全部 Objective：

    python -m chanlun_trader.research_factory.objective_reconciliation --root . --all

CLI 只写：

- reports/research_reconciliation/<objective_id>/OBJECTIVE_RECONCILIATION_V1.json
- reports/research_reconciliation/<objective_id>/OBJECTIVE_RECONCILIATION_V1.md
- reports/research_reconciliation/CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1.json
- reports/research_reconciliation/CURRENT_OBJECTIVE_RECONCILIATION_INDEX_V1.md

JSON 面向机器，Markdown 使用简体中文。服务本身的 reconcile() 只读；只有显式调用报告写入方法或 CLI 时才产生上述报告文件。

## Autonomous Control Plane V1 边界

`AutonomousResearchControlPlaneV1` 是 Phase 2 的单 Objective、结果盲化控制平面。它先执行 Objective Reconciliation，再构建 SafeRuntimeContext、解析 Capability 与 Permission，最后最多执行一个已经获得自动权限的副作用动作；动作完成后立即停止并等待下一轮 canonical reconciliation。

控制平面只允许自动执行以下三类已有治理服务动作：生成 Candidate Proposal、创建 Executable Materialization Preview、恢复已经人工确认的同一 Materialization。AI Design 生成、Candidate Governance Freeze、Executable Materialization Confirmation、Structural Entry、Predictive Authorization 与 Trial 结果对账均停在人工闸门；Phase 2 不启动 Predictive Trial，不读取性能结果，不执行 Final Test、Prospective Simulation 或 Real Order。

运行时决策写入 `reports/research_control_plane/<objective_id>/AUTONOMOUS_RESEARCH_DECISION_V1.json`，精确一次执行回执写入 `action_execution_receipts.jsonl`。这些文件只能证明控制平面观察到的状态、计划与执行尝试，不能建立任何 canonical authority。回执若停留在 `STARTED`，下一轮只允许先通过 canonical state 判断是否已经产生副作用：已产生则补齐完成回执，否则显式标记 retry 后重试同一 idempotency key。

CLI 示例：

    python -m chanlun_trader.research_factory.autonomous_control_plane --root . --objective-id <OBJECTIVE_ID> --inspect --json
    python -m chanlun_trader.research_factory.autonomous_control_plane --root . --objective-id <OBJECTIVE_ID> --tick --dry-run --json

`inspect` 与 `--dry-run` 不产生运行时写入。Web Console 只通过同一只读读模型展示状态，并通过本机请求触发单 tick；服务端拒绝非本机控制请求。
