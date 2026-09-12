# 继续研究：固定两机制批次 V1

用户本轮明确委托继续寻找盈利策略。此文档记录代理在该委托下选择的首个执行批次，不冒充用户逐候选批准，不改写旧批准、预算消费和失败。原研究恢复点为764e1c11b94a68969191b14f7346613ac7bf8535；公开代码合并提交c3236771不包含本地研究记录。

使用既有BaoStock配对输入的DAILY/STATES/FEATURES、同一独立TRAIN日历和公司行动hazard。无需下载数据；不访问Validation/Final Test。已有TRAIN绩效已被本会话看过，所有新结果均承接该曝光负担和历史新颖性语义缺项。

## 事前冻结

1. MOMENTUM_5：评分为负的HFQ五session价格变化，评分小于0按升序选股，即检验正向价格延续。
2. STABILITY_20：评分为`-1/(1+std(最近20个独立session的RETURN_5D, ddof=0))`，按升序优先低离散度。它是重叠五session价格变化的离散度代理，不是每日波动率，也不是统计检验。

两项都保持原Top3、10000元、3仓、下一开盘、持有3session、100股、佣金/最低佣金/印花税/滑点、容量、T+1/FIFO和hazard拒绝。没有根据下一批表现选择公式、费用或窗口。20日依赖不足的日期保留不可计算，不压缩缺日，不填零；可用时间取所有依赖中最晚时间。

首次真实读取前保存PREREGISTRATION.json，绑定代码、合同、输入哈希、用户原话、实际任务身份、原新颖性比较记录及决策。输出目录为`E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/train-search-batch-v1`，仅本地保存。

## 执行和记账

每项经原新颖性服务和无收益可行性通过后，由TrainSearchGovernanceV1复用原授权、锁和SearchBudgetRegistry登记用途增量；每项1主+1确证修复，新批总计2主，修复入口本版拒绝使用。旧桶额度/消费不变，没有另一份预算账本。新批规模是代理的执行分批选择，不是用户明确指定的数值额度。

原父批准到期仍按2026-09-14T10:05:03+08:00或更早期限执行。并发1、worker900秒/2048MiB、数值线程1，本批内部计算上限90分钟。资源限制用于可恢复执行，并不把无期限搜索解释为已被证明可以自动延期的父回执。任何撤销、代码/输入身份变化或未结算执行均拒绝自动重跑。

复用原账户引擎保存逐笔信号、订单、成交、拒绝、lot、现金、持仓及费用；调用前消费，结束或失败都结算。反馈只给出完整性和事前二值筛选：COMPLETE、训练净回报大于0、关闭lot至少30。精确结果留评估文件，不按结果改本批参数。

训练筛选通过仅意味着需要独立证据的训练盈利候选，不是总目标完成、统计显著或实际可盈利。STRICT_TRAIN_INPUT_READY、READY_FOR_REAL_TRIAL、R1_FULLY_CLOSED、AUTONOMOUS_STRATEGY_GOAL_COMPLETED均保持false；V4和Windows OPEN保持原状态。此入口不运行正式Trial/Paper/真实订单，不公开真实数据或绩效。

执行：在原研究工作区设置PYTHONPATH为src，运行`.venv/Scripts/python.exe scripts/run_train_search_batch_v1.py`。已经尝试但未完成的执行须先对账，不允许通过删除文件重跑。
