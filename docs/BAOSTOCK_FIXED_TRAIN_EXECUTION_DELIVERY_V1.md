# BaoStock固定账户TRAIN执行交付

2026-09-12：真实输入准备、冻结可行性检查、固定账户主回测与权威结算完成。保留另一会话集成的未提交一致性改动，未提交、重置或覆盖这些改动。

原生执行状态COMPLETE，主曝光1、修复0；总资源约174.68/360分钟。输入5182个来源成员对应5181个执行身份。可行性为1025条完整路径、454个开仓日、580个证券，达到原冻结门槛。

期末模型权益1258.8489元（初始10000元），TRAIN模型净回报-87.411511%，最大回撤87.441511%，关闭266个lot。原结果包含信号、委托、成交、拒绝、每日现金/持仓、费用、风险事件和最终账户checkpoint。亏损不能视为工程错误，未使用修复额度。

- [中文交付报告](E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1/FIXED_ACCOUNT_TRAIN_DELIVERY_V1.md)
- [实际文件与账本索引](E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1/DELIVERABLES_INDEX_V1.json)
- [结算摘要](E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1/EXECUTION_SUMMARY.json)

源码冻结绑定实际代码（含fixed_account_rules.py），执行记录dirty_worktree=true；原失败/批准/旧消费不重置。精确结果访问已记录为当前会话评估交付用途，不声称该上下文仍未曝光，不据此改参或启动额外搜索。

本次只完成固定TRAIN探索；回溯HFQ、未知历史发布时间和hazard条件筛选偏差仍在。STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED继续false，不运行V4、Final Test、Paper或真实订单。
