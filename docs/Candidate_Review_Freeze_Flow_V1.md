# Candidate Review & Freeze Flow V1

## 业务结论

本版本把 Candidate Proposal 治理为 Frozen Candidate，但冻结必须经过第二次明确的人工确认。系统在确认后停在 `READY_FOR_STRUCTURAL_PREFLIGHT`，不会自动进入 Structural Preflight、回测、Trial 或下一轮研究。

流程如下：

```text
CANDIDATE_PROPOSAL_READY
        ↓
HUMAN_REVIEW_REQUIRED
        ↓（第一道人审 approve）
FREEZE_PREVIEW_READY
        ↓（第二次人工 confirm）
FROZEN
        ↓
READY_FOR_STRUCTURAL_PREFLIGHT
```

拒绝分支为：

```text
HUMAN_REVIEW_REQUIRED → REJECTED → CLOSED
```

`APPROVED` 只表示第一道人审通过；`FREEZE_PREVIEW_READY` 只表示冻结方案已经准备好。两者都不代表 Candidate 已登记。

## 输入与 Preview

冻结只接受已经持久化的 Candidate Proposal 和 Freeze Preview。Freeze Preview 位于：

```text
reports/research_candidates/proposals/<objective_id>/CANDIDATE_FREEZE_PREVIEW.json
```

Preview 固定绑定以下身份：

- Candidate ID 与 Candidate Hash；
- Objective ID 与 Proposal ID/Hash；
- Objective research lineage；
- mechanism family；
- factor、execution、data contract；
- Multiple Testing family。

Preview 的 `preview_hash` 覆盖完整 Preview 内容，包含生成时间；读取、冻结和重启恢复都会校验 hash。确认后不修改 Preview 文件，实际 Registry 记录沿用 Preview 的 Candidate Hash。

## 人工接口

只读接口：

```text
GET /api/research/candidates/proposals
GET /api/research/candidates/proposals/{proposal_id}
GET /api/research/candidates/proposals/{proposal_id}/freeze-preview
```

第一道人审：

```text
POST /api/research/candidates/proposals/{proposal_id}/review
```

批准只生成 Freeze Preview，不创建 Candidate。

第二次人工确认：

```text
POST /api/research/candidates/proposals/{proposal_id}/freeze
```

请求必须包含：

```json
{
  "action": "FREEZE_CANDIDATE",
  "confirmed": true,
  "reviewer": "human-reviewer",
  "proposal_hash": "<current proposal hash>",
  "candidate_hash": "<preview candidate hash>"
}
```

服务端会记录不可变的 `freeze_id`、`reviewer`、`timestamp`、`proposal_hash` 和 `candidate_hash`，并将冻结审计追加到 Proposal 的 `reviews.jsonl`。

## Candidate Registry

人工确认成功后，才会创建：

```text
data/research/research_factory/candidates/<objective_id>/CANDIDATE_REGISTRY.json
```

Registry 为追加式记录，候选项至少包含：

- `candidate_id`；
- `candidate_hash`；
- `contract`：factor、execution、data 和 Multiple Testing family；
- `lineage`；
- `creation_reason=HUMAN_CONFIRMED_CANDIDATE_FREEZE`；
- `state=FROZEN`。

同一个 Candidate ID 如果再次写入，必须与已有 hash、合同、lineage 和状态完全一致；不一致时以 `NEW_CANDIDATE_REQUIRED` 失败，旧 Candidate 不会被覆盖。

## 修改保护与恢复

冻结后对 factor、execution 或 data contract 的修改会被 `detect_frozen_modification` 检测为 `NEW_CANDIDATE`，返回旧/新 Candidate 身份和变更合同字段。服务不会把修改写回旧 Registry。

确认流程的审计记录、治理记录、回执、Registry 和状态文件均采用原子写入与幂等校验。进程在 Registry 写入或最终状态写入前退出时，启动恢复只会继续已经存在的明确 Freeze 确认，不会把单纯的 Preview 自动冻结。

## 治理边界

本版本明确保证：

- Proposal 审核通过不会创建 Candidate；
- Freeze Preview 不消耗或预留 Budget；
- Freeze 不读取 Performance；
- Freeze 不调用 AI；
- Freeze 不启动 Structural Preflight；
- Freeze 不运行回测、不创建或启动 Trial；
- `structural_preflight_started`、`trial_started`、`ai_called` 和 `budget_consumed` 均保持 `false`；
- 最终状态只为 `READY_FOR_STRUCTURAL_PREFLIGHT`，后续 Structural 仍需独立人工入口。

## Web Console

页面：

```text
/research/candidates/proposals
```

页面展示 Proposal 来源、机制、factor/execution/data contract、lineage、Multiple Testing family、Candidate Hash 和治理历史。第一道人审后显示 Freeze Preview；只有用户点击“确认冻结 Candidate”才调用 Freeze POST 接口。冻结后显示 `Candidate Frozen`、Hash、Lineage 和等待 Structural Preflight 的状态，不提供自动 Trial 入口。

## 验证范围

测试覆盖：

1. Proposal → Freeze Preview → Freeze 状态流转；
2. Preview、Proposal 和 Registry Candidate Hash 一致；
3. 未确认不能创建 Registry；
4. Freeze 不启动 Structural、Trial、AI 或 Budget；
5. Reject 不修改父研究历史；
6. 冻结后合同修改返回 `NEW_CANDIDATE` 且不覆盖旧记录；
7. 重复确认只返回同一冻结回执；
8. Registry 写入中断后的重启恢复；
9. HTTP Freeze 接口要求明确确认并保持本机治理边界。
