# ALPHA ROBUSTIFICATION STANDARD

## 状态
- PROMISING：有统计证据，但未完成完整 Red Team。
- ROBUST_PRETEST：FINAL_TEST 仍 SEALED，已通过 PIT / Walk Forward / Robustness / Red Team / Cost / Delay / Universe / Concentration / Bootstrap / Placebo。
- FINAL_VALIDATED：只有用户授权打开 Final Test 后一次冻结评估。

## Factor 审计（A1）
每个原 PROMISING Factor 不改公式 / lookback / universe / threshold，先审计原定义：
- IC / RankIC / ICIR / Positive IC Ratio / Coverage / Turnover / Autocorrelation
- Horizons: 1,2,3,5,7,10,20
- IC Decay Curve -> Natural Holding Horizon
- Temporal stability (Year / Half-Year)
- Neutralization: Raw / Industry / Size / Industry+Size
- Quantile: Q1~Q5, Top10%, Top5%
- Extreme period (2024-09-24~2024-10-08) inclusion/exclusion
- Concentration removal: strongest month / sector / extreme observations
- Bootstrap IC + bootstrap quantile spread
- Placebo: cross-sectional shuffle / date shift / randomized factor
- Multiple testing: family_test_count / global_test_count / BH-FDR

## 晋级判定（预判）
同时满足 TRAIN supportive、Legacy Validation supportive、Walk-Forward supportive、Temporal stability、IC decay logical、
Quantile spread logical、Extreme survives、Neutralization acceptable、Bootstrap support、Placebo superiority、
Multiple-testing acceptable、No PIT issue，才可 ROBUST_PRETEST。
