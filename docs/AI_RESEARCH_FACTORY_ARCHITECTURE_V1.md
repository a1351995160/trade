# AI Research Factory Orchestrator V1

## 业务边界

Research Factory 只负责从 Research Objective 到研究分类、失败知识和下一批计划的控制平面。它不启动 Prospective Observation，不读取 Final Test，不生成 Recommendation，不连接真实 Broker。

## Canonical flow

`ResearchObjectiveV1 → ResearchBatchPlanV1 → SearchBudgetRegistryV1 → NoOutcomeResearchContextV1 → AIHypothesisGenerator → StrategyCandidateBuilder → StrategyCandidateCompilerV2 → Stage1 PIT/Data Gate → Automated Strategy Validation → Classification → FailureKnowledgeSnapshotV1 → Next Batch / Stop`

Factory 使用 adapter/facade 复用既有 `TrialRegistryV1`、`ExperimentLedger`、`FailureLibrary`、`FailureKnowledgeView`、`StrategyCandidateRegistry`、`StrategyLibrary` 和 `PromotionLedger`，不创建第二套 canonical registry。

## Governance

设计阶段只能访问 factor capability、mechanism history 和高层 failure class。任何 `return/PF/DD/win_rate/p_value/forward/prospective` 字段都会被 `PerformanceBlindGuard` 拒绝。Batch、Hypothesis、Candidate 在 performance access 前冻结；同一 Batch 禁止 retuning。

Validator 是唯一分类裁判。`ENGINE_ERROR`、`EXECUTION_INTEGRITY_FAILURE` 和 `DATA_PIPELINE_ERROR` 映射为 `ENGINEERING_BLOCKED/ENGINE_FAILURE`，不能变成 `REJECTED` 或 Alpha Failure。

## Canonical dependencies

Factory 的执行依赖固定为 `UnifiedFactorRegistry`、`AIHypothesisGenerator`、`StrategyCandidateBuilder`、`StrategyCandidateCompilerV2`、研究数据路由、`BacktestEngineV2`、`PortfolioExitEvaluatorV1` 和 corrected V3 validation pipeline。legacy compiler/backtest/factor path fail closed。

## 停止条件

目标满足、Batch/Trial budget exhausted、没有可用 hypotheses、PIT/data blocked、engineering blocked、manual stop 或达到 `max_batches` 时停止。研究通过不会自动进入 Paper、Prospective 或 Recommendation。

## Batch 1 实际运行入口

`scripts/run_next_research_batch_v1.py` 通过 `AIResearchFactoryOrchestratorV1.run(synthetic=False)` 执行唯一的 Batch 1。该入口固定 12 个假设、8 个冻结候选和最多 8 个真实性能试验；试验前完成 objective/batch/family/trial 预算预留、append-only trial 登记、Stage1 PIT/data preflight 和 `PerformanceAccessGate`，试验阶段使用 `BacktestEngineV2 + PortfolioExitEvaluatorV1`，并分别执行 `BASE_RESEARCH` 与 `SMALL_CAPITAL_10K`。

Batch 1 结束后只生成终态 checkpoint 和审计报告，不生成 Batch 2 计划；resume 从终态 checkpoint 只返回已完成 trial 元数据，不重新执行性能试验。
