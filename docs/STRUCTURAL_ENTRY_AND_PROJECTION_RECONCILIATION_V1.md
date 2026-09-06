# Structural Entry & Projection Reconciliation V1

## 目标

本阶段把 Structural Entry、Canonical Structural Result、Daemon/Orchestrator Projection 收敛为一条单向、可恢复、可审计的边界：

```text
READY_FOR_STRUCTURAL_PREFLIGHT
        ↓ 显式 RUN_STRUCTURAL_PREFLIGHT
STRUCTURAL_RUNNING
        ↓
Canonical Structural Result
  PASS                  → PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED
  UNKNOWN / BLOCKED     → STRUCTURAL_BLOCKED
  Integrity / Engineering failure → ENGINEERING_BLOCKED
```

Structural PASS 只证明结构可行性通过，不代表已授权或已启动 Predictive Validation。

## Authority 边界

| 对象 | Authority / 角色 | 允许表达的事实 |
|---|---|---|
| `DurableFrozenCandidateContractV1` | `EXECUTABLE_CANDIDATE_AUTHORITY` | 可供 Structural Provider 执行的冻结 Candidate |
| Provider raw result / execution evidence | `STRUCTURAL_EXECUTION_EVIDENCE` | 本次 Structural 执行了什么、是否完成、Provider 证据 |
| Structural reconciliation report | `STRUCTURAL_RESULT_AUTHORITY` | 身份绑定后的 canonical Structural research fact |
| Predictive authorization receipt | `PREDICTIVE_AUTHORIZATION_AUTHORITY` | 人工是否授权 Predictive |
| Daemon checkpoint/status | `RUNTIME_PROJECTION` | 运行态观察和恢复提示 |
| Orchestrator checkpoint/status | `RUNTIME_PROJECTION` | 编排态观察和控制提示 |
| Research Console | `READ_MODEL / PROJECTION` | 消费 Objective Reconciliation 后的展示模型 |

Daemon、Orchestrator 和 Console 不能把旧的 `RUNNING` 或 `ACTIVE` checkpoint 反向提升为 Structural PASS。

## Structural Entry Gate

CLI、Web、Daemon 显式入口和 domain service 统一经过 `StructuralEntryGateV1`。进入 Provider 前必须同时满足：

1. Objective Reconciliation 的 `effective_state` 为 `READY_FOR_STRUCTURAL_PREFLIGHT`；
2. `structural_preflight_ready=true`；
3. 当前 Objective 存在且只有一个有效的 `DurableFrozenCandidateContractV1`；
4. `objective_id`、`candidate_id`、`candidate_hash` 与合同身份一致；
5. `from_dict()` 和 `provider_candidate_payload()` 均通过；
6. 不存在 `CANONICAL_CONFLICT`、Candidate identity conflict 或过期的 materialization；
7. 操作明确确认 `RUN_STRUCTURAL_PREFLIGHT`。

Candidate Registry 只是治理/库存证据，不能替代 Durable Contract。`READY_FOR_STRUCTURAL_PREFLIGHT` 是资格，不是自动执行命令；Daemon polling、Orchestrator step、Console read 都不会启动 Provider。

## Structural 执行与结果

Structural 执行复用现有 `CanonicalResearchRuntime.structural_preflight`，不创建第二套 Structural Engine。执行证据与 canonical 结果分开保存：

- `reports/research_daemon/<objective_id>/structural_execution_evidence.json`：Provider 执行证据；
- `reports/research_daemon/<objective_id>/structural_preflight_reconciliation_canonical_v1.json`：canonical reconciled result；
- `reports/research_daemon/<objective_id>/structural_preflight_reconciliation_history.json`：追加式历史索引。

Canonical Structural Result 至少绑定 `objective_id`、`candidate_id`、`candidate_hash`、`durable_contract_hash`、Provider payload identity、data/manifest identity、policy identity 和 `result_hash`。

Structural provider/reconciliation 必须 outcome-blind：不得读取 Performance，不得访问 Final Test、Prospective、Real Order，也不得修改 Candidate、AI artifact、TrialLedger 或历史 Structural/Trial/Budget 事实。

## PASS 后硬边界

Canonical Structural `PASS` 的唯一下一状态为：

```text
PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED
required_action = AUTHORIZE_PREDICTIVE_TRIAL
safe_to_advance = false
predictive_authorized = false
```

本阶段不会自动执行以下任何动作：Predictive authorization、Predictive budget reserve/use、Trial 创建、Predictive Engine、Performance 读取、AI 调用或新 Candidate 生成。Trial count、Predictive Budget used/reserved 必须保持不变。

`UNKNOWN`、`INSUFFICIENT_SAMPLE`、`BOUND_INCOMPLETE` 或等价的 Structural 结果统一 fail closed 为 `STRUCTURAL_BLOCKED`；合同、身份、PIT、data contract 或工程异常统一为 `ENGINEERING_BLOCKED`，不能伪装成研究失败后进入 Predictive。

## Projection Reconciliation

Projection 只能由 `ObjectiveReconciliationServiceV1` 和 canonical facts 计算：

```text
canonical Structural Result / effective state
                  ↓
       Daemon / Orchestrator / Console projection
```

当 canonical PASS 而 Daemon 为 `STRUCTURAL_RUNNING`，或 Orchestrator 为 `LOCAL_RESEARCH_RUNNING` 时，报告 `PROJECTION_DRIFT`。修复动作是重新投影，不是修改 canonical Structural Result。

显式 Projection repair 只更新既有 Daemon/Orchestrator checkpoint/status，不创建新运行态文件，并追加 `projection_reconciliation_receipts.jsonl`。每条回执包含：

- `objective_id`、`projection_type`；
- `old_projection_hash`、`canonical_effective_state_hash`、`new_projection_hash`；
- `reason`、`timestamp`、`receipt_hash`。

相同 canonical effective-state hash 与目标 projection 已经存在时不重复写入，保证 repair exact-once。若 Objective Reconciliation 为 `CANONICAL_CONFLICT`，不自动修复，返回 `safe_to_resume=false` 和 `human_action_required=true`。

## Restart / Recovery

Daemon restart/recover 首先读取 Objective Reconciliation Snapshot：

- canonical Structural PASS：只恢复为 `STRUCTURAL_PASS` projection，不重新运行 Provider；
- canonical Structural BLOCKED/ENGINEERING_BLOCKED：保持阻断，不进入 Predictive；
- 明确的 Structural running evidence：保持 `STRUCTURAL_RUNNING`，不因旧 checkpoint 自动重试；
- 已存在终态 canonical result：重复显式启动返回既有 receipt/result；
- terminal execution evidence 但没有 canonical result：进入 `ENGINEERING_BLOCKED`，等待工程对账。

同一 `objective_id + candidate_id + candidate_hash + durable_contract_hash + structural/data/policy identity` 的终态执行不会产生竞争的第二个 canonical result。相同 identity 但输入/result hash 变化时返回 `STRUCTURAL_IDEMPOTENCY_CONFLICT` 并 fail closed。

## CLI / Web

提供以下只读或显式本地操作：

- CLI：`structural-readiness`、`start-structural-preflight --confirmed`；
- Web：`GET .../structural/readiness`、`GET .../structural/reconciliation`、`POST .../structural/start`、`POST .../structural/projection-repair`；
- Structural 写接口均要求 loopback/local-only 和明确确认；
- 没有自动 Predictive action，且旧的 Daemon/Orchestrator 状态不能绕过统一 gate。
