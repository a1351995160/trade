# PHASE1_CERTIFICATION_BLOCKER_FIXES_V1

## 目标与边界

本轮只修复 Phase 1 治理认证阻断项，不增加 Alpha 功能，不启动 Predictive，不创建真实 Trial，不访问真实 Research Workspace。所有新增判断都在正式开发仓库的 synthetic/临时测试夹具中验证。

## 冻结层次与权威

治理冻结、人工确认、执行物化和 Structural 就绪是四个不同层次：

1. `CANDIDATE_GOVERNANCE_FROZEN` 表示 Candidate Registry 与 Freeze Receipt 已完成治理冻结。
2. `EXECUTABLE_MATERIALIZATION_PREVIEW_READY` 表示系统已经生成 immutable Preview，等待明确的人工确认。
3. `HUMAN_EXECUTABLE_MATERIALIZATION_APPROVAL_AUTHORITY` 是人工对同一个 Preview、Candidate 和 Contract 身份的确认凭证。
4. `DurableFrozenCandidateContractV1` 是可重建执行语义的持久合同。
5. `READY_FOR_STRUCTURAL_PREFLIGHT` 只在合同和人工确认凭证均通过完整身份/哈希校验后成立。

`Durable Contract alone is NOT sufficient executable authority.`

Candidate Registry、Freeze Receipt、Preview、Durable Contract 或 Confirmation Receipt 任一单独工件都不能授权 Structural。Executable Candidate Authority 必须同时满足：

```text
Valid DurableFrozenCandidateContractV1
+ Valid matching Executable Materialization Confirmation Receipt
+ Preview / Objective / Proposal / Candidate / Contract / context identity all match
```

## Crash-safe materialization protocol

执行物化采用 receipt-first journal 语义：

```text
immutable Preview
  -> explicit human confirmation
  -> immutable Confirmation Receipt
  -> deterministic Durable Contract append
  -> projection/state update
  -> READY_FOR_STRUCTURAL_PREFLIGHT
```

Receipt 至少绑定 `objective_id`、`proposal_id`、`preview_id`、`preview_hash`、`candidate_id`、`candidate_hash`、`durable_contract_hash`、AI Design identity、AI Design approval hash、`source_context_id`、`source_context_hash`、`reviewer`、`confirmed_at`、`idempotency_key` 和 `receipt_hash`。Receipt 使用 create-only 写入，不能覆盖；Contract Registry 使用既有 canonical identity 检查，不能覆盖冲突记录。

Receipt 先落盘的意义是：如果在 Receipt 与 Contract 之间崩溃，重启后能够证明“人工确认已经发生”，但仍不会把未完成的物化当作 executable。恢复接口只从当前 immutable Preview 重建同一份 Contract，不能调用 AI、创建 Candidate、修改参数、创建 Trial、消费 Budget、访问 Performance 或启动 Structural。

相同 Preview/Candidate/Contract identity 的重试是 exact-once；不同 `idempotency_key`、reviewer 或任何身份/哈希冲突均为 `MATERIALIZATION_IDEMPOTENCY_CONFLICT` 或 `CANONICAL_CONFLICT`，系统不自动选择、不覆盖、不修复。

## Crash recovery state matrix

| Case | Receipt | Contract | 重启后的状态 | Structural | 允许动作 |
| --- | --- | --- | --- | --- | --- |
| A | No | No | `EXECUTABLE_MATERIALIZATION_PREVIEW_READY` | 不就绪 | `HUMAN_CONFIRM_EXECUTABLE_MATERIALIZATION` |
| B | Yes | No | `EXECUTABLE_MATERIALIZATION_RECOVERY_REQUIRED` | 不就绪 | 仅 `RECOVER_EXECUTABLE_MATERIALIZATION` |
| C | No | Yes | `EXECUTABLE_MATERIALIZATION_CONFIRMATION_MISSING` | 不就绪 | 重新读取当前 Preview 并取得明确人工确认 |
| D | Yes | Yes，match | `READY_FOR_STRUCTURAL_PREFLIGHT` | 就绪 | `RUN_STRUCTURAL_PREFLIGHT` |
| E | Yes | Yes，mismatch | `CANONICAL_CONFLICT` / integrity failure | 不就绪 | 停止并人工对账 |
| F | Yes | Yes，相同重试 | `READY_FOR_STRUCTURAL_PREFLIGHT` | 就绪 | exact-once，无第二份 Receipt/Contract |

状态 B 的恢复是确定性的 journal completion；状态 C 永远不能根据 Contract 推断用户确认过，也不能自动伪造 Receipt。

## Objective Reconciliation

`ObjectiveReconciliationServiceV1` 现在分别投影 Preview、Confirmation 和 Contract 的存在性、完整性、哈希和身份匹配结果。只有 `preview_valid`、唯一有效 Contract、`confirmation_valid` 和 `identity_match` 同时成立，才会输出：

```text
effective_state = READY_FOR_STRUCTURAL_PREFLIGHT
required_action = RUN_STRUCTURAL_PREFLIGHT
structural_preflight_ready = true
executable_frozen_candidate = true
```

因此 `READY_FOR_STRUCTURAL_PREFLIGHT` 是状态，不是 action。Contract-only、Receipt-only、Preview stale、Receipt/Contract mismatch、篡改或重复身份均保持 `safe_to_advance=false` 和 `structural_preflight_ready=false`。

## Structural defense in depth

`StructuralEntryGateV1` 先读取 Objective Reconciliation，再独立读取当前 Preview、Confirmation Receipt 和 Durable Contract。它再次检查 schema、哈希、Objective/Candidate/Contract identity 和 Preview 绑定；缺少 Receipt 或只有 Contract 时，在 provider 初始化/调用前拒绝。Candidate Registry 不是 Structural fallback，Durable Contract-only 也不是 fallback。

Structural PASS 的边界未改变：

```text
Structural PASS
  -> PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED
```

本轮不自动授权、不创建 Predictive Trial、不 reserve/consume Budget、不访问 Performance。

## SafeRuntimeContext freshness

AI Design APPROVED 后，Candidate Proposal Generation 在真正生成 Proposal 之前重新构建 `SafeRuntimeContextBuilderV1(purpose="AI_DESIGN")`。这是 consumer gate，避免让 `AIDesignApprovalService.evaluate()` 递归构建上下文：

```text
SafeRuntimeContext -> ObjectiveReconciliation -> AIDesignApproval.evaluate
```

当前 context 的 ID/hash 必须同时等于 AI Design 和 Approval Receipt 的绑定值。Data manifest、Factor capability、Event capability、Budget canonical identity、Structural canonical identity/state 或 Objective effective-state 发生变化时，生成被拒绝为 `STALE_RUNTIME_CONTEXT`，不增加 Proposal/Candidate，不调用 AI，不创建 Trial，不改变 Budget，不访问 Performance。

AI_DESIGN context 只包含设计阶段允许的 canonical facts；Candidate governance、Durable Contract 和 executable materialization 是下游事实，不被反向并入 AI Design context。

## Manual AI Handoff freshness

Manual Handoff 创建时绑定 `source_context_id`、`source_context_hash`、`safe_runtime_context_identity`、版本和 source hashes。AI Result ingest/validate 之前再次构建 `SafeRuntimeContextBuilderV1(purpose="MANUAL_HANDOFF")`，并比较 handoff 与当前 context 的完整 identity。过期结果以 `STALE_AI_HANDOFF_CONTEXT` 在 ingest 前拒绝，不产生 Candidate、Durable Contract、Trial、Budget 变化或 Performance 访问。

没有 context binding 的 governed Objective handoff 不允许走 legacy bypass。Legacy bypass 仅限显式版本 `research-orchestrator-ai-handoff-v2` 与 `LEGACY_SYNTHETIC_NO_OBJECTIVE_V1` 的无 Objective synthetic fixture。

## OutcomeBlind 与 Web 边界

Preview、Confirmation、Recovery、Reconciliation、AI Design freshness 和 Manual Handoff freshness 均通过 `PerformanceBlindGuard`。新增 Receipt 和 state model 不携带研究结果；写接口继续要求 loopback/local-only，且 Materialization Confirm 继续要求 `confirmed=true`、`reviewer`、`preview_hash` 和 `idempotency_key`。

## Phase 1 deterministic certification workflow

`.github/workflows/phase1-certification.yml` 名称为 `Phase 1 Certification`，在 Pull Request 和 `codex/**` push 触发。它只 checkout 当前仓库，执行：

- `git diff --check`
- `python -m compileall -q src`
- `python -m pytest --collect-only -q`
- 明确列出的 Phase 1 governance deterministic suite

suite 使用 synthetic/`tmp_path` fixture，不访问真实 Research Workspace、公网行情、TDX、真实回测、Performance、Final Test、Prospective 或密钥。仓库原有依赖缺失的 integration tests 保留在完整回归报告中，不冒充 Phase 1 suite 通过。
