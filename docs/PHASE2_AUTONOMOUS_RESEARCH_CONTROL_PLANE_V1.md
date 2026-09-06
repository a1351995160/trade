# PHASE2_AUTONOMOUS_RESEARCH_CONTROL_PLANE_V1

## Purpose

本版本把已有 Research Factory canonical services 组合成一个可审计、结果盲化、逐动作推进的 Autonomous Research Control Plane。控制平面负责观察、对账、计划、权限解析和有限的安全自动动作，不负责创造研究权威，也不替代任何人工治理确认。

实现范围只覆盖单个 `objective_id`。真实 Research Workspace 仍是只读输入；CI 与测试只使用临时 synthetic fixture。

## Architecture

| 层 | 实现 | 责任 |
|---|---|---|
| Canonical observation | `ObjectiveReconciliationServiceV1` | 先读取并对账 Objective、Candidate、Budget、Structural、Predictive 与 Trial facts |
| Safe context | `SafeRuntimeContextBuilderV1` | 构建带 context id/hash 的结果盲化运行上下文 |
| Capability | `AgentCapabilityRegistryV1` | 声明能力、版本、实现引用、side-effect level 与 outcome-blind 兼容性 |
| Permission | `ActionPermissionResolverV1` | 将 canonical state、authority、context、budget、capability 和人工闸门解析成 ALLOW_AUTOMATIC / ALLOW_MANUAL_ONLY / DENY |
| Action planning | `ResearchActionV1` | 为一个可审计动作绑定 objective/candidate/trial/context/reconciliation/hash/idempotency identity |
| Execution journal | `AutonomousActionExecutionJournalV1` | JSONL append-only exact-once/retry/recovery receipt |
| Control loop | `AutonomousResearchControlPlaneV1` | reconciliation-first、one-action-per-tick、完成后停下等待下一次对账 |
| Console/API | `research_console.py`、`webapp.py`、Vue Research Console | 只读状态展示与本机单 tick 控制入口 |

运行时投影只写入：

    reports/research_control_plane/<objective_id>/AUTONOMOUS_RESEARCH_DECISION_V1.json
    reports/research_control_plane/<objective_id>/AUTONOMOUS_RESEARCH_DECISION_HISTORY_V1.jsonl
    reports/research_control_plane/<objective_id>/action_execution_receipts.jsonl

这些文件属于 `RUNTIME_DECISION_EVIDENCE`，不能成为或覆盖 canonical authority。

## Action Model

每个 `ResearchActionV1` 必须携带：

- 稳定 `action_id`、`action_type` 与 `idempotency_key`；
- `objective_id`、`candidate_id`、`candidate_hash`、`trial_id`；
- `required_state`、`source_state`、`source_context_id`、`source_context_hash`、`source_reconciliation_hash`；
- 所需 capabilities、authorities、confirmation、budget effect、performance access 与 `outcome_blind=true`。

Phase 2 支持的动作标识包括 `RECONCILE_OBJECTIVE`、`BUILD_SAFE_RUNTIME_CONTEXT`、`GENERATE_AI_DESIGN`、`WAIT_FOR_AI_DESIGN_CONFIRMATION`、`GENERATE_CANDIDATE_PROPOSAL`、`WAIT_FOR_CANDIDATE_GOVERNANCE_FREEZE`、`CREATE_EXECUTABLE_MATERIALIZATION_PREVIEW`、`WAIT_FOR_EXECUTABLE_MATERIALIZATION_CONFIRMATION`、`RECOVER_EXECUTABLE_MATERIALIZATION`、`RUN_STRUCTURAL_PREFLIGHT`、`WAIT_FOR_PREDICTIVE_AUTHORIZATION`、`START_PREDICTIVE_TRIAL`、`WAIT_FOR_TRIAL_RESULT`、`RECONCILE_TRIAL`、`STOP_OBJECTIVE` 和 `BLOCKED`。

## Capability Model

Capability 只是实现能力声明，不是授权。Phase 2 自动能力只有三类：

1. `CAN_GENERATE_CANDIDATE_PROPOSAL`：调用现有 Candidate Proposal 生成服务；
2. `CAN_CREATE_MATERIALIZATION_PREVIEW`：创建不可变 Executable Materialization Preview；
3. `CAN_RECOVER_CONFIRMED_MATERIALIZATION`：恢复同一 candidate/proposal/preview identity 已人工确认的执行合同。

AI Design、Structural Entry、Predictive Trial 和性能读取能力全部声明为 manual-only 或非 outcome-blind，不会因为注册在表中就被自动调用。

## Permission Model

权限解析顺序是：

1. Canonical conflict 先 DENY；
2. 检查 SafeRuntimeContext 是否可用、未过期且通过结果盲化守卫；
3. 检查 capability 支持和自动执行声明；
4. 检查 action 对应 authority、当前 canonical state、budget 计划字段和人工 confirmation；
5. 只有三类安全 Research Artifact 动作可以得到 `ALLOW_AUTOMATIC`；
6. 其他治理闸门返回 `ALLOW_MANUAL_ONLY`，禁止控制平面代替审批人；
7. Predictive Trial 即使已有 canonical authorization，在 Phase 2 也返回 `DENY / PHASE2_PREDICTIVE_EXECUTION_DISABLED`。

## One Action Per Tick

每个 tick 严格按以下顺序执行：

    Objective Reconciliation
      -> SafeRuntimeContext
      -> Action + Permission
      -> 0 或 1 个允许的自动动作
      -> 写入本轮 decision/receipt
      -> 停止，等待下一轮 reconciliation

`inspect` 和 `--dry-run` 只做前四步并保证零写入；`loop` 只是有限次调用 tick，遇到人工闸门、DENY、失败或达到 `max_ticks` 立即停止。

## Human Gates

| 闸门 | Phase 2 行为 |
|---|---|
| AI Design generation/approval | `GENERATE_AI_DESIGN` 与确认动作返回 `ALLOW_MANUAL_ONLY` |
| Candidate Governance Freeze | Proposal 生成后等待人工 Freeze |
| Executable Materialization Confirmation | Preview 生成后等待人工确认 |
| Structural Entry | `RUN_STRUCTURAL_PREFLIGHT` 只呈现下一动作，不自动 start |
| Predictive Authorization | 没有 canonical authorization 时等待人工授权 |
| Predictive Trial | 即使授权存在也不执行，返回 Phase 2 disabled |
| Final Test / Prospective Simulation / Real Order | Phase 2 不暴露、不调用、不可自动化 |

## Crash Recovery

`action_execution_receipts.jsonl` 只追加以下阶段性回执：`STARTED`、`RECOVERY_RETRY_ALLOWED`、`COMPLETED`、`FAILED`。同一 `idempotency_key` 的 `COMPLETED` 回执永远优先返回幂等结果，不重复调用领域服务。

若进程在副作用发生前崩溃，下一轮发现旧 `STARTED` 且 canonical state 未体现副作用，追加 retry marker 后以同一 identity 重试。若副作用已发生，下一轮通过 reconciliation 直接补齐 `recovered=true` 的完成回执。若状态/hash/context/reconciliation 变化，旧 action 失效并返回 `STALE_RESEARCH_ACTION`，不会把旧计划强行套到新事实。

同一 Objective 使用 daemon-style lock；并发 tick 被拒绝。控制平面不使用 `git reset`、不覆盖人工文件、不删除旧回执。

## Exact Once

同一 `idempotency_key` 的完成回执是唯一可复用的执行结果；重复 tick、CLI 重放和 Console 重试都必须先读取回执，再决定是否需要恢复或重试。

## Authority Boundaries

控制平面产生的 Decision、Action 和 Receipt 只能说明：当时读取到什么、选择了什么动作、动作是否被允许、是否执行过以及执行后看到什么。它们不能建立以下权威：Objective 定义、AI Design Approval、Candidate Governance Freeze、Durable Frozen Candidate、Budget、Structural PASS、Predictive Authorization、Trial outcome、Final Test、Prospective Simulation、Real Order。

## OutcomeBlind

规划输入经过 `PerformanceBlindGuard`。允许的 performance 字段只限 SafeRuntimeContext 中用于控制访问边界的布尔/状态路径；result summary、decision、Console model 和 receipt 均不得携带 return、PnL、Sharpe、drawdown、win-rate 或其他结果数值。Predictive authorization 仅读取 allowlisted identity/status metadata，用于展示和禁止执行，不会读取 trial performance。

## Predictive Boundary

Structural PASS 后只允许进入 `WAIT_FOR_PREDICTIVE_AUTHORIZATION`。即使已有 canonical authorization，控制平面也只呈现 `START_PREDICTIVE_TRIAL` 的受限计划并返回 `PHASE2_PREDICTIVE_EXECUTION_DISABLED`，不创建 TrialLedger、不预留预算、不读取结果。

## State Machine

    OBSERVE
      -> RECONCILE
      -> PLAN
      -> WAITING_FOR_HUMAN | READY_TO_EXECUTE | BLOCKED | STOPPED
      -> EXECUTING
      -> RECONCILE_AFTER_EXECUTION
      -> WAITING_FOR_HUMAN | BLOCKED | STOPPED

控制平面没有“自动批准”“自动冻结”“自动进入 Trial”“自动读取结果”状态。canonical conflict、过期 context、缺失 capability、预算歧义或不支持动作均 fail closed。

## CLI and Console

只读检查：

    python -m chanlun_trader.research_factory.autonomous_control_plane --root . --objective-id <OBJECTIVE_ID> --inspect --json

安全 dry-run：

    python -m chanlun_trader.research_factory.autonomous_control_plane --root . --objective-id <OBJECTIVE_ID> --tick --dry-run --json

正式 tick：

    python -m chanlun_trader.research_factory.autonomous_control_plane --root . --objective-id <OBJECTIVE_ID> --tick --json

Web Console 的 GET 读取同一 control-plane read model；POST tick 只接受本机请求，并且一次只触发一个 tick。页面展示 canonical state、next action、permission、人工/自动标记、context freshness、budget plan-only 信息、receipt、outcome-blind 标记和阻断原因。

## Tests

Phase 2 synthetic suite 覆盖：capability registry、permission precedence、append-only journal、exact-once、crash recovery、stale action、one-action-per-tick、dry-run zero write、human gates、predictive deny、console GET/POST 和 performance blind。

Phase 1 deterministic certification suite 继续作为回归门禁；Phase 2 CI 不读取真实 Research Workspace，不依赖真实 runtime artifact。前端使用 `npm run build` 验证类型检查和生产构建。

## Limitations

本版本仍不执行任何 Predictive Trial，也不消费任何性能结果。进入 Phase 3 前必须由独立任务明确批准：Predictive Trial service contract、预算 reservation/consumption authority、Trial outcome schema、结果访问审计、停止/回滚语义以及更高风险动作的人工确认协议。没有这些 canonical authority 与独立回归证据，不得把 Phase 2 的 plannable action 解释为可执行授权。

## Phase 3 Prerequisites

Phase 3 必须先补齐 Predictive Trial service contract、预算 reservation/consumption authority、Trial outcome schema、结果访问审计、停止/回滚语义和更高风险动作的人工确认协议，并由独立审计确认这些 authority 不由控制平面重复实现。
