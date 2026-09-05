# AI Design Approval Boundary V1

## 目标

本版本把 `AI_DESIGN_READY` 与 Candidate Proposal 生成明确分开。AI 研究设计生成后必须停在人工确认边界：

```text
AI_DESIGN_READY
  ↓
AI_DESIGN_AWAITING_CONFIRMATION
  ↓ 人工审核
AI_DESIGN_REJECTED 或 AI_DESIGN_APPROVED
  ↓ 仅当 APPROVED
CANDIDATE_GENERATION_ALLOWED
  ↓ 显式调用
CANDIDATE_PROPOSAL_READY
```

`CANDIDATE_GENERATION_ALLOWED` 只代表允许生成 Candidate Proposal，不代表 Candidate 已创建、合同已冻结、Structural Preflight 已执行、Trial 已启动或预算已消费。

## Canonical Approval Authority

每个 Objective 的批准回执固定保存为：

`reports/research_evolution/ai_design/<objective_id>/AI_DESIGN_APPROVAL_RECEIPT.json`

回执是不可变的单一决策记录，至少包含：

- `schema_version`、`approval_id`、`objective_id`；
- `ai_design_id`、`ai_design_hash`，以及设计存在时的 `source_context_id`、`source_context_hash`；
- `proposal_id`、`lineage`（设计提供时）；
- `decision=APPROVED|REJECTED`、`reviewer`、`reviewed_at`；
- `idempotency_key`、`receipt_hash`。

回执还记录 `resulting_state`、`candidate_generation_allowed`、`structural_preflight_ready` 和零副作用标记，便于重启后审计。Objective 的生命周期字段、Daemon、Orchestrator 和普通 Console 投影都不能替代这份回执。

## 完整性与精确一次

Candidate Generation 在所有生成入口（服务、CLI、Web 和内部 helper）统一检查：

1. 当前 Objective 与 AI Design 存在且设计状态为 `AI_DESIGN_READY`；
2. 存在批准回执，决策为 `APPROVED`；
3. 回执绑定当前 `ai_design_id`、`ai_design_hash`、`source_context_id` 和 `source_context_hash`；
4. 回执自身的 `receipt_hash` 校验通过。

检查失败时不写 Candidate Proposal，并返回明确的 `AI_DESIGN_APPROVAL_REQUIRED`、`AI_DESIGN_REJECTED`、`STALE_AI_DESIGN_APPROVAL`、`AI_DESIGN_APPROVAL_OBJECTIVE_MISMATCH` 或 `AI_DESIGN_APPROVAL_INTEGRITY_FAILURE`。

同一设计和 `idempotency_key` 的重复审核返回同一回执，不覆盖原文件；同一设计尝试提交相反决策返回 `APPROVAL_IDEMPOTENCY_CONFLICT`。拒绝不会删除或改写 AI Design，也不能把原设计重新改成批准；必须生成新的设计身份和新的哈希。

## Web Console

AI Design 页面展示设计哈希、批准状态、审核人、审核时间、决策和回执哈希。待确认时只提供本机限定的批准/拒绝入口；批准后只提供“显式生成 Candidate Proposal”按钮，不会自动生成。拒绝后 Candidate 动作保持关闭。

接口：

```text
GET  /api/research-console/<objective_id>/evolution/ai-design
GET  /api/research-console/<objective_id>/evolution/ai-design/approval
POST /api/research-console/<objective_id>/evolution/ai-design/approval
POST /api/research-console/<objective_id>/candidate-proposals/generate
```

写接口继续使用现有 loopback/local console boundary，并要求请求体带 `confirmed=true`、`decision`、`reviewer` 和 `idempotency_key`（缺少幂等键时服务会生成当前设计范围内的稳定默认键）。

## 明确禁止

本版本不生成 DurableFrozenCandidateContract，不登记或修改 Candidate，不执行 Structural Preflight，不做 Predictive Authorization，不启动 Trial，不调用 AI，不预留或消费预算，也不运行 Autonomous Alpha Loop。
