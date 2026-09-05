# Research Evolution Design Bridge V1

## 业务目的

`Research Evolution Manager V1` 能够回答“为什么本轮研究失败”，但失败分析如果不能约束下一轮研究，仍然容易回到同一机制上做微调。Research Evolution Design Bridge V1 在两者之间增加一个明确的、结果盲化的 Proposal 层：

```text
Failure Analysis
    ↓
Research Evolution Proposal
    ↓
Human Governance Decision
    ↓
Approved 后才允许创建新的 Research Objective
    ↓
AI Research Design
```

Bridge 只生成研究方向建议，最终停在 `HUMAN_REVIEW_REQUIRED`。它不是 Autonomous Alpha Research Loop。

## 输入边界

`ResearchEvolutionProposalManager` 允许读取：

- `research_evolution_report.json`：失败类别、失败解释、机制身份和 lineage；
- Objective-scoped `failure_landscape.json`：已失败机制和覆盖情况；
- Candidate Contract：机制身份、factor family 和 research lineage；
- `MECHANISM_COVERAGE_REGISTRY.json`：已覆盖机制与未探索方向。

报告中的绩效观测字段不会进入 Proposal。生成器只抽取身份、失败类别、机制家族、报告引用和研究方向，并在写入前调用 `PerformanceBlindGuard`。

## Proposal 输出

输出位置固定为：

```text
reports/research_evolution/proposals/RESEARCH_EVOLUTION_PROPOSAL.json
```

核心字段包括：

- `parent_objective_id`、`parent_candidate_id`、`parent_trial_id`：父研究 lineage；
- `source_failure_report`：失败分析来源引用；
- `failed_mechanism`：失败机制身份；
- `failure_summary`：失败类别数组，不含绩效数字；
- `avoid_mechanism_family`：禁止重复探索的机制家族；
- `suggested_research_directions`：仅允许高层方向 ID，例如 `event_driven`、`volatility_structure`、`capital_flow`；
- `governance_state`：固定为 `HUMAN_REVIEW_REQUIRED`；
- `human_approval_required`：固定为 `true`。

Proposal 不生成买入/卖出条件、阈值、持仓、窗口或参数调整。失败结果不能被反向拟合成策略参数。

## Mechanism Coverage Registry

注册表位置为：

```text
reports/research_evolution/proposals/MECHANISM_COVERAGE_REGISTRY.json
```

注册表保存 `covered` 和 `unexplored` 两组结果。每次生成 Proposal 时，将父 Candidate 的机制家族幂等加入 `covered`；同一机制的后缀变体（例如 `AMOUNT_ACCEL_VARIANT`、`liquidity_acceleration_fast`）不会被当作新的建议方向。注册表本身也必须保持 `outcome_blind`。

## 治理边界

Proposal 的状态历史为：

```text
EVOLUTION_ANALYZED
    → PROPOSAL_CREATED
    → HUMAN_REVIEW_REQUIRED
```

生成 Proposal 不会：

- 创建 Candidate；
- 创建 Research Objective；
- 启动 Trial；
- 调用 Codex；
- 修改预算或消耗研究次数；
- 修改历史报告、Candidate Contract、Trial Ledger 或 Validation。

`APPROVED` 和 `CREATE_NEW_OBJECTIVE` 只是人工审核后的后续状态，不是本模块可以执行的动作。

## 使用方式

从已生成的失败报告构建建议：

```powershell
python -m chanlun_trader.research_factory.research_evolution_proposal `
  --root . `
  --objective-id <objective_id>
```

若省略输入对象，管理器会在当前 Objective 下选择最新的 `research_evolution_report.json`。也支持在测试或受控调用中传入内存 Mapping；两种方式都使用相同的结果盲化和治理校验。

## Web Console

研究演进建议页面：

```text
/research/evolution/proposals?objective_id=<objective_id>
```

对应只读接口：

```text
GET /api/research-console/<objective_id>/evolution/proposals
```

页面展示失败来源、失败机制、禁止重复方向、建议探索方向和研究覆盖情况，并提供“查看研究方案”按钮展开只读 lineage。页面没有直接启动研究的按钮，也不会在 GET 请求中生成 Proposal。

## 验证

测试覆盖：

1. Proposal 字段完整且关联 Objective、Candidate、Trial；
2. Proposal 和 Mechanism Coverage Registry 通过 Outcome Blind 检查；
3. 已失败机制的变体不会进入建议方向；
4. 生成过程只写派生 Proposal/Registry，不创建 Objective、Candidate 或 Trial；
5. 重复运行不会重复写入覆盖记录；
6. Web Console 只读读取已生成 Proposal，跨 Objective 不展示。
