# FACTOR RESEARCH STANDARD

## Pipeline

1. `FactorDefinition` registration (formula/inputs/direction/PIT mode).
2. `FactorStore` immutable version `{factor_id}_{version}.parquet`.
3. TRAIN `FactorEvaluator`: Pearson IC / Rank IC / ICIR / Positive IC Ratio / Coverage / Autocorrelation / Turnover / Q1~Q5.
4. TRAIN pass (RankIC>=0.02 and Positive IC Ratio>=0.55) -> PROMISING_FACTOR, only then `ValidationGuard` may read VALIDATION.
5. VALIDATION pass -> FactorLibrary PROMISING; fail -> REJECTED + FailureLibrary VALIDATION_FAIL.
6. Only full Red Team + V2 backtest pass may become ROBUST.

## Direction

- For long-only candidates, prefer monotonic Q1<...<Q5 and strong Top Decile.
- No random K-fold; use purged/embargo time split.
