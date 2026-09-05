# DAILY SELECTOR V2 DESIGN

- 不重写 V1，只升级 approved inputs / ranking / explanation。
- PRODUCTION_CANDIDATES 只能来自 ROBUST_PRETEST strategies。
- PROMISING 只能进入 EXPERIMENTAL_WATCHLIST。
- 继续支持 NO TRADE。
- Ranking：Validated Strategy Rank + Independent Alpha Confirmation + Risk Filter；不得简单等权所有 Factor 相加（除非研究证明）。
- 研究 1 vs 2 vs 3 independent alphas 是否有增量，不能默认越多越好。
- Explanation：输出 primary alpha mechanism 与支持/反对证据，同时提供原始 Factor 证据。
- 继续 immutable daily snapshot。
