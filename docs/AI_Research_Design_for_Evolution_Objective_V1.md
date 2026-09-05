# AI Research Design for Evolution Objective V1

## 目的

本能力把已经完成治理、已创建的 Evolution Objective 交给一次受控的 AI 研究设计任务。它只生成高层研究设计提案，不生成 Candidate，不启动 Trial，也不进入 Predictive Trial。

流程固定为：

```text
OBJECTIVE_CREATED
  ↓
读取 Proposal / Failure Landscape / Mechanism Coverage Registry / Objective lineage
  ↓
AI_RESEARCH_DESIGN_PROPOSAL.json
  ↓
AI_DESIGN_READY
  ↓
AI_DESIGN_AWAITING_CONFIRMATION
  ↓ 人工审核
AI_DESIGN_REJECTED 或 AI_DESIGN_APPROVED
  ↓ 仅批准后允许显式生成 Candidate Proposal
CANDIDATE_GENERATION_ALLOWED
```

## 输入边界

`ResearchEvolutionAIDesignServiceV1.build_input(objective_id)` 只读取当前 Objective 绑定的父 Proposal、父研究的 `failure_landscape.json`、机制覆盖注册表、当前 Objective 的不可变 lineage 和数据能力注册表。

发送给设计后端的输入包括：

- 已失败机制与失败分类；
- 已覆盖、禁止重复的机制；
- Proposal 和覆盖注册表提出的未探索方向；
- 当前已登记的数据集、字段能力、频率、时点语义和可用状态；
- 当前 Objective 的允许因子范围与 PIT、无前视、T+1 等设计约束。

失败报告正文、绩效明细和预测验证结果不会作为输入读取。来源引用只指向安全投影使用的 canonical 文件，不向 AI 暴露详细失败报告路径。

输入和输出都经过 `PerformanceBlindGuard`。发现被禁止的结果字段时 fail closed，不生成设计文件。

## 输出契约

每个 Objective 只允许一份：

`reports/research_evolution/ai_design/<objective_id>/AI_RESEARCH_DESIGN_PROPOSAL.json`

输出必须包含：

- `research_hypothesis`：研究假设；
- `mechanism_family`：机制族；
- `candidate_design_intention`：候选设计意图，不是 Candidate 实体；
- `allowed_factors`：允许因子；
- `excluded_mechanisms`：排除机制；
- `validation_expectation`：验证预期。

同目录同时保存脱敏输入 `AI_RESEARCH_DESIGN_INPUT.json` 和状态回执 `AI_RESEARCH_DESIGN_STATE.json`。状态回执记录 `OBJECTIVE_CREATED → AI_DESIGN_READY`、设计哈希、人工确认动作和零副作用标记。

## 治理与恢复

- 默认本地适配器只生成可重复的模板研究设计；实际 AI 适配器必须通过 `backend` 显式注入，不能从启动流程隐式调用。
- 已存在且上下文哈希一致的设计会直接重放，不会再次调用 AI；上下文发生变化时拒绝覆盖。
- 如果进程在输出文件写入后、状态回执写入前中断，`recover()` 或后端启动恢复会校验设计哈希并补齐状态，不会再次调用 AI。
- Web Console 页面是只读页面：`/research/evolution/ai-design?objective_id=<objective_id>`。
- 页面只展示父研究来源、失败经验、禁止机制、建议方向和已生成的 AI 研究目标；不提供 Candidate 导入、Trial 启动或预算操作入口。

## 明确禁止

该阶段不读取或计算结果型字段，不创建 Candidate，不修改 Candidate 或 Trial，不启动 Predictive Trial，不调用预算登记或预算扣减逻辑，也不改变既有研究历史。人工确认 AI 设计结果属于后续治理动作。批准回执保存在同一 Objective 目录的 `AI_DESIGN_APPROVAL_RECEIPT.json`，并绑定 `ai_design_id`、`ai_design_hash`、输入上下文哈希和幂等键；本能力不会自行生成 Candidate Proposal。

## 验证

`tests/research_factory/test_research_evolution_ai_design_v1.py` 覆盖：

1. 必要输入资料和 Outcome Blind；
2. 禁止结果字段 fail closed；
3. 生成后无 Candidate、Trial、预算副作用；
4. 同一 Objective exactly once；
5. 输出先落盘后的重启恢复；
6. Objective 与 Proposal lineage 一致；
7. Console 只读视图在生成前后均保持目标隔离。
