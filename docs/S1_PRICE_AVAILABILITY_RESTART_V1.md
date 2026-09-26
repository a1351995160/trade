# 固定 51 指标策略：三项补充验收的业务结论

日期：2026-09-26。范围仍为两只股票、2023-10-09 至 2024-07-31 的指标观察，以及 2024-02-01 至 2024-07-31 的模型账户。报告见 [PRICE_PIT_RESTART.json](../reports/s1_trusted_baseline_20260925/PRICE_PIT_RESTART.json)。

| 问题 | 本次处理 | 现在可下的结论 |
| --- | --- | --- |
| 历史数据在做决定时是否可见 | 对原始日线、BaoStock 换手率、历史证券状态分别要求逐日来源发布时间或同期采集时间。只靠后来下载时间、模型假定的 `available_at`，一律不视为证明。 | 当前三个输入均缺少所需记录；严格历史资格为 `BLOCKED`。历史模拟仍可作受限诊断，不能据此称策略已合格。 |
| 分红前后指标与成交价格 | 仅当现金分红公告已在登记日前发布，且原始价的除息参考前收盘与公告金额相符，才从除息日起把价格和成交额转换为因果后复权指标。成交、费用、现金与持仓估值始终按未复权价。 | 两次真实现金分红核对通过。51 票规则不变，但 5 个收盘判断改变；118 日账户共 93 笔成交，独立核账无差异。其他公司行动仍需单独建模。 |
| 回测中断后会不会重记 | 每个完成的交易日写入带输入身份、事件序号和账户/订单/成交/分红状态哈希的原子回执。重启时从原始输入确定性重放，先核对中断前缀，再处理余下日期。 | 登记日和到账日两处中断后，与连续运行的经济结果一致；到账日另有退出进程后新进程恢复的真实数据检查。此证据覆盖离线回测，不覆盖券商接口的外部订单。 |

这一实现没有尝试补造 2024 年的供应商发布时间。当前换手率快照于 2026-09-25 拉取，来源清单明确 `historical_available_at_verified=false`；历史状态的 `available_at` 是按次日开盘构造，原始日线也没有逐行来源发布时间。未来可由持续采集形成同期记录；如果要继续核对 2024 年，应寻找当年保存的供应商公告、数据发布日志或带时间戳的原始文件，再逐条接入资格校验。没有这类记录时，S1 不能通过。

复跑真实数据需要本机已有的 E 盘快照；CI 仅运行不含这些私有数据的边界测试：

```powershell
python scripts/verify_s1_price_recovery_v1.py --daily-parquet E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1/DAILY.parquet --turn-parquet reports/all_indicator_fixed_strategy_pilot_20260925/BAOSTOCK_TURN.parquet --states-parquet E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/baostock-account-v1/STATES.parquet --historical-states-parquet E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3/HISTORICAL_POOL_AND_STATE.parquet --turn-manifest-json reports/all_indicator_fixed_strategy_pilot_20260925/BAOSTOCK_TURN.manifest.json --report reports/s1_trusted_baseline_20260925/PRICE_PIT_RESTART.json
```

报告的 `PASSED_MODELED` 只表示价格、账户和离线恢复联合诊断通过；`strict_source_qualification=BLOCKED` 与 `s1_baseline_status=NOT_PASSED` 才是当前研究资格结论。该固定规则曾用于开发，不属于独立收益检验，也未证明有投资价值。
