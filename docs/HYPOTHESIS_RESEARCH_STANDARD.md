# HYPOTHESIS RESEARCH STANDARD

- Hypothesis First: register market_logic / expected_direction / expected_horizon / falsification_condition before experiment.
- Max 3~8 parameter points per hypothesis; no 1000-threshold grids.
- All experiments (success/failure/error) go into ExperimentLedger; failures also into FailureLibrary.
- Broad Search is TRAIN ONLY; VALIDATION only for PROMISING and counted.
- Family budget: <=8 hypotheses per family; total 45~90.
- Multiple testing: BH-FDR, permutation, placebo; Deflated Sharpe marked NOT_AVAILABLE if unreliable.
