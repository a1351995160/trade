# Candidate Generation Governance V1

## 业务目标

Candidate Generation Governance V1 把已经到达 `AI_DESIGN_READY` 的 AI 研究设计转换为一个可供人工审核的 `Candidate Proposal`。本版本只生成研究方案，不登记 Candidate，不冻结 Candidate，也不进入 Structural Preflight 或 Trial。

当前流程是：

```text
AI_DESIGN_READY
    ↓
Candidate Proposal
    ↓
CANDIDATE_PROPOSAL_READY / HUMAN_REVIEW_REQUIRED
    ↓（第一道人审）
APPROVED
    ↓
FREEZE_PREVIEW_READY（只读冻结预览）
    ↓（第二次人工确认）
FROZEN → READY_FOR_STRUCTURAL_PREFLIGHT
```

拒绝分支为：

```text
HUMAN_REVIEW_REQUIRED → REJECTED → CLOSED
```

`FREEZE_PREVIEW_READY` 只表示冻结方案已经准备好，不能解释为 Candidate 已登记。只有后续明确的 `freeze` 人工确认才会写入 Candidate Registry；冻结后仍不会自动执行 Structural Preflight。

## 输入边界

`CandidateGenerationManagerV1` 只读取以下资料：

- `AI_RESEARCH_DESIGN_PROPOSAL.json`；
- 绑定的 Research Evolution Proposal；
- 当前 Objective 与不可变 Objective lineage；
- `Mechanism Coverage Registry`；
- AI 设计中携带的数据能力视图，或只读 `data/research/data_capability.json`。

输入会先生成脱敏的 `CANDIDATE_PROPOSAL_INPUT.json`。输入允许包含研究假设、机制族、已失败/禁止机制、建议方向、可用因子、执行与数据约束、预算上限和 Multiple Testing family。输入禁止携带收益、胜率、回撤、p-value、PnL、单笔交易、历史净值、股票名单等结果字段；共享 `PerformanceBlindGuard` 与本模块的中文字段检查会双重拒绝违规资料。

AI 设计中可能存在的结果型因子名称也不会进入 `factor_contract`。例如 `RETURN_*`、`PNL`、`WIN_RATE` 等会被过滤；没有剩余安全因子时，流程会失败关闭。

## 输出合同

每个 Objective 最多生成一个幂等的候选建议目录：

```text
reports/research_candidates/proposals/<objective_id>/
├── CANDIDATE_PROPOSAL_INPUT.json
├── CANDIDATE_PROPOSAL.json
├── CANDIDATE_PROPOSAL_STATE.json
├── CANDIDATE_FREEZE_PREVIEW.json      # 仅批准后出现
├── CANDIDATE_FREEZE_GOVERNANCE.json   # 仅确认冻结后出现
├── CANDIDATE_FREEZE_RECEIPT.json       # 仅确认冻结后出现
└── reviews.jsonl
```

`CANDIDATE_PROPOSAL.json` 至少包含：

- `proposal_id`、`proposal_hash`、`objective_id`、`lineage`；
- `research_hypothesis`、`mechanism_family`、`candidate_design_intention`；
- `factor_contract`；
- `execution_contract`：`signal_time`、`entry`、`holding_period`、风险约束和禁止同日卖出；
- `data_contract`：必需数据集、PIT 语义、可用数据能力；
- `excluded_family` 与 `excluded_mechanisms`；
- `multiple_testing_family_id`、预算建议；
- `status=CANDIDATE_PROPOSAL_READY`、`human_review_required=true`；
- 零副作用治理标记：Candidate、冻结、Structural、Trial、AI、Budget 均为 `false`。

候选名称由机制族和建议探索方向稳定生成。例如当前演进方向生成 `EVENT_DRIVEN_VOLATILITY_STRUCTURE_V1`。

## 重复机制防护

生成前执行只读 `Candidate Similarity Check`。机制族会归一化后与 Coverage Registry、父 Proposal 的失败/禁止列表和 AI 设计排除列表比较。

以下别名都会归一化到 `liquidity_acceleration`，因此不会再次生成：

- `liquidity_acceleration_v2`；
- `amount_acceleration`；
- `volume_acceleration`。

命中时返回 `DUPLICATE_MECHANISM_REJECTED`，在写入 Candidate Proposal 目录前失败关闭。

## 人工治理接口

只读接口：

```text
GET /api/research/candidates/proposals
GET /api/research/candidates/proposals/{proposal_id}
GET /api/research/candidates/proposals/{proposal_id}/freeze-preview
GET /api/research-console/{objective_id}/candidate-proposals
```

人工审核接口：

```text
POST /api/research/candidates/proposals/{proposal_id}/review
POST /api/research/candidates/proposals/{proposal_id}/freeze
```

请求至少包含 `action=approve|reject` 和 `reviewer`；推荐同时提交当前 `proposal_hash`，防止审核过期内容。审核记录追加到 `reviews.jsonl`，包含 `review_id`、`reviewer`、`timestamp`、`proposal_hash`、动作和结果状态。

批准只会写入 `CANDIDATE_FREEZE_PREVIEW.json` 并把状态推进到 `FREEZE_PREVIEW_READY`。预览中的 Candidate ID/Hash 已经是待确认的稳定身份，但 `candidate_identity_status` 仍为 `PREVIEW_ONLY_NOT_REGISTERED`，不属于实际 Candidate Registry。只有请求体带 `confirmed=true` 的 `freeze` 接口才会创建 `data/research/research_factory/candidates/<objective_id>/CANDIDATE_REGISTRY.json`。

## 明确禁止的副作用

以下动作不在本版本的调用链中：

- 未经第二次确认创建或登记 Candidate；
- 修改 Frozen Candidate Contract；
- 启动 Structural Preflight；
- 创建或启动 Trial；
- 调用 AI 后端；
- 读取 Performance、写入或消耗 Budget；
- 自动进入下一研究循环。

后端启动恢复只补齐已经落盘的 Proposal、状态、批准后的冻结预览或已经明确确认的 Freeze 事务；不会把单纯 Preview 自动冻结，不调用 AI。

## 显式使用

候选建议生成必须由受控运维动作显式调用：

```powershell
python -m chanlun_trader.research_factory.candidate_generation `
  --root . `
  --objective-id RESEARCH_OBJECTIVE_EVOLUTION_V1_BE80F967D29706F19703C869
```

命令成功后应停在 `CANDIDATE_PROPOSAL_READY`，等待 Web Console 的第一道人审。批准后停在 `FREEZE_PREVIEW_READY`；只有用户明确点击确认冻结才登记 Candidate，最终停在 `READY_FOR_STRUCTURAL_PREFLIGHT`，不会自动向 Structural Preflight 或 Predictive Trial 推进。

## 验证范围

V1 测试覆盖：

1. AI Design 到 Candidate Proposal 的字段完整性；
2. 结果盲化和 Performance 文件不可读边界；
3. 失败机制及别名的重复拒绝；
4. 批准不创建 Candidate、Trial 或预算副作用；
5. 拒绝不修改父 Proposal 历史；
6. 批准前不可读取冻结预览；
7. 精确一次生成；
8. 输出写入中断后的重启恢复；
9. Research Console 只读路由与 POST 治理路由隔离。
