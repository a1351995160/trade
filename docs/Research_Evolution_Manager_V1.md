# Research Evolution Manager V1

## 业务目的

本能力用于在既有研究流程到达 `BLOCKED`、`REJECTED` 或其他失败终态后，读取已经落盘的研究结果，提取失败机制，形成失败空间记录和下一轮研究设计上下文。

流程边界是：

`Candidate → Trial → Validation → Governance → BLOCKED → 失败分析 → 研究演进报告 → AI 研究上下文`

生成上下文后停止。它不是 Autonomous Alpha Research Loop，不会自动生成 Candidate、启动 Trial、调用 Codex 或消耗预测试验预算。

## 输入与输出

输入只读读取以下已绑定对象：

- 研究目标登记文件；
- 冻结 Candidate Contract；
- Trial Contract 与 Trial Ledger；
- 指定 Trial 的 `validation_results.json`、`final_status.json`；
- 可用时读取 `multiple_testing.json`。

输出写入独立的 `reports/research_evolution/` 目录，不覆盖任何 canonical 输入：

```text
reports/research_evolution/<objective_id>/<candidate_id>/<trial_id>/
├── research_evolution_report.json
├── research_evolution_report.md
└── AI_RESEARCH_EVOLUTION_CONTEXT.json
reports/research_evolution/<objective_id>/failure_landscape.json
```

机器报告保存完整 lineage、失败证据、类别分布、研究空间覆盖和治理标记；Markdown 报告复用 `ZhCNPresentation` 输出中文阅读版。

## 失败分类

V1 固定使用以下六类，不与旧版 Failure Knowledge 分类混用：

| 分类 | 典型证据 | 下一轮约束 |
| --- | --- | --- |
| `STATISTICAL_FAILURE` | Bootstrap 支持不足、调整后支持不足 | 先冻结统计检验和假设族边界 |
| `RETURN_FAILURE` | 基础净收益非正、盈利因子未达平衡线 | 需要不同机制假设，不能只调当前参数 |
| `RISK_FAILURE` | 成本或滑点压力下不稳健 | 保留独立成本与风险压力验证 |
| `SAMPLE_FAILURE` | 样本或独立机会低于最低要求 | 先做样本可行性预检 |
| `ENGINEERING_FAILURE` | 数据、PIT、执行语义或完整性检查阻断 | 先完成工程预检和修复 |
| `OVERFITTING_RISK` | 多重检验支持消失、集中度或分段不稳定 | 扩大独立研究方向，避免重复搜索 |

一次 Trial 可以同时落入多个类别；`failure_landscape.json` 按 Candidate/Trial 去重，重复运行不会增加计数。

## AI 研究上下文安全规则

`AI_RESEARCH_EVOLUTION_CONTEXT.json` 只含：已测试机制、失败类别、研究空间覆盖、未探索方向和设计约束。它不含收益、胜率、回撤、单笔交易、p-value、调整后 p-value、绩效结果或最终决策字段。

生成器在写出上下文前调用 `PerformanceBlindGuard`；失败即拒绝写入上下文。上下文明确标记：

- `automatic_candidate_generation_allowed: false`；
- `automatic_trial_start_allowed: false`；
- `codex_invocation_allowed: false`；
- `budget_consumption_allowed: false`；
- `next_boundary: HUMAN_REVIEW_REQUIRED`。

## 使用方式

分析必须由明确命令触发，不由 Web Console GET 请求触发：

```powershell
python -m chanlun_trader.research_factory.research_evolution_manager `
  --root . `
  --objective-id <objective_id> `
  --candidate-id <candidate_id> `
  --trial-id <trial_id>
```

重启或重复执行时，生成器以输入哈希复用相同报告，并对 Failure Landscape 做幂等合并。

## Web Console

访问 `/research/evolution?objective_id=<objective_id>` 查看“研究演进分析”。页面展示：

1. BLOCKED → 失败分析 → AI 上下文的生命周期边界；
2. 失败分类分布与证据说明；
3. 已测试机制和研究空间覆盖；
4. 未探索方向及人工审核边界；
5. lineage 与输出哈希的只读安全检查。

无报告时页面只显示“尚未生成”，不会隐式生成分析。

## 实际示例

本轮对以下已完成 Trial 生成了示例产物：

- Objective：`RESEARCH_OBJECTIVE_GOVERNED_NEW_MECHANISM_V1_A8D9D0C881545ABC845C`；
- Candidate：`CAND_LIQUIDITY_AMOUNT_ACCEL_LOW_ILLIQ_CROSS_SECTIONAL_V1`；
- Trial：`..._T002`；
- 结果边界：`BLOCKED`；
- 主要失败类别：`STATISTICAL_FAILURE`、`RETURN_FAILURE`、`RISK_FAILURE`、`OVERFITTING_RISK`。

页面截图已在本次交付的最终响应中内嵌展示；报告与上下文文件位于上述 `reports/research_evolution/` 目录。

## 变更边界

模块只写 `reports/research_evolution/` 下的派生文件。它不修改 Candidate Contract、Trial Ledger、Validation、Final Status、Multiple Testing、Budget Ledger、Orchestrator 或 AI 调用记录。
