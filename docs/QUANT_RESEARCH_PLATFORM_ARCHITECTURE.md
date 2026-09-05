# QUANT RESEARCH PLATFORM ARCHITECTURE

## Layers

```text
Data / TDX local files / TQ clean parquet
  -> Provider / Adapter (TdxData, TDX5MinAdapter)
  -> ResearchDataAccessGuard (FINAL TEST hard lock)
  -> Data Store (FactorStore / EventStore / LabelStore / tradable_returns)
  -> Evaluation (FactorEvaluator / EventStudy / FactorLibrary)
  -> Governance (HypothesisEngine / ExperimentLedger / FailureLibrary / ValidationGuard / MultipleTesting)
  -> Strategy (StrategyDefinition / StrategyLibrary / BT_ENGINE_V2 runner)
  -> DailyStockSelector (PIT snapshot / immutable audit)
```

## PIT rules

- All research reads only `available_at <= decision_time`.
- QFQ price features use `qfq_columns_asof(as_of=decision_date)` or equivalent PIT return series; full-sample `get_qfq_day` is PIT_UNSAFE.
- Final Test (>=2025-08-01) is enforced by `ResearchDataAccessGuard`.
- BT_ENGINE_V2 execution prices remain RAW.
