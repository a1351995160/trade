# VALIDATION REUSE POLICY

- 上一阶段 VALIDATION_ACCESS_COUNT=23，18 个 PROMISING Factor 的 Validation 已被看过，原 Validation 不再视为完全未知数据。
- Legacy Validation 结果只作为 Historical Evidence，不得用于继续调参 / 选阈值 / 设计新规则。
- 新机制/新策略开发只用 TRAIN + Purged Internal Walk-Forward + Anchored Walk-Forward + Bootstrap + Placebo。
- 冻结后才允许有限查看 Validation；每个新 Strategy Lineage 的 NEW_FORMAL_VALIDATION_EXPOSURE <= 1。
- 任何“看Validation→修改→再看Validation”记录 VALIDATION_REUSE_RISK=HIGH 并禁止晋级最高级。
