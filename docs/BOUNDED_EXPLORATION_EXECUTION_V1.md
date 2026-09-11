# 两协议受限探索执行 V1

本适配落实用户对 REVISED_RESEARCH_DECISION_PROPOSAL_V1 的明确批准。旧提案保留原文，最新决定由本轮批准附件及不可变服务回执承接；不是逐候选人工签名。旧正式计划 BLOCKED，V4 不获准，三个正式完成/就绪标志保持 false。

## 唯一入口和治理

在本研究工作区使用 `.venv/Scripts/python.exe scripts/run_bounded_exploration.py --execute-approved-plan`，并将本工作区 `src` 加入 PYTHONPATH。真实入口拒绝测试隔离标签；测试继续使用原隔离机制。输入、输出及合同均在入口中封闭固定，不提供任意路径、参数或阈值选项。

服务将本任务中的明确批准来源、附件实际 SHA256、提案内容、代码提交、代码逐文件 SHA256、输入、日历、四合同及历史证据绑定到 `revised-exploration-v1/governance/confirmation.json`。消息原始时间戳未由当前接口提供，明确为 UNKNOWN；服务记录实际登记时间，不伪造用户签字或旧批准时刻。原截止 2026-09-14T10:05:03+08:00 不变。

回执的 `canonical_increment_budget` 明确指定原 SearchBudgetRegistryV1 负责的新用途增量：总6、固定实验4、确证工程重算2、各固定合同1。沿用原预留/消费/释放与持久账本，Objective 仍为 RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1。旧12消费、8/12争议、全局UNKNOWN不改；新桶不是把旧上限改成18。追加事件记录执行、曝光开始和结算。重复运行不重算；未结算状态必须先对账，不能换ID免费重跑。重算接口需要受影响合同及 red/green/修复提交证据，本轮没有默认重算。

## 冻结计算及读取

唯一数值来源为批准的 TRAIN_INPUT_SNAPSHOT_DIAGNOSTIC.parquet，SHA256 为 741690ba3b068610908e49465b8731ef45a6638391e3db1d3b84ebe1b645ad72。只物化 symbol/date/close/high/low；不读取 Validation、Final Test 或旧绩效。读取前固定清单并校验哈希/schema，worker 再核对实际行数、日历窗口及来源成员集合。总体5182、观测5036、缺源146保留。

四合同沿用提案 A/B 表达式、0/0.8 分组、t+3标签和 t-20 信号 placebo。按独立交易日历重索引。A需完整6 session信号窗口，B需完整20 session窗口；标签要求t至t+3完整4 session，缺口不压缩。预热、缺价格、非法价格、零区间、尾端和空组保留日期及不可计算状态；均值不填0。原因计数可以重叠，不可相加推断总体；独立 not_computable_count 与两组有效数合计5182。

新颖性使用原 CandidateNoveltyGateV2，比较既有获准盲化投影中的实际身份/可用设计字段及同批合同，判重即拒绝；保留历史不完整限制，不声称全局新颖性已证明。旧16/19联合身份、23次Validation访问及18因子声明和未知映射继续保留。

## 执行、隔离和归档

复用原资源worker，Windows Job安装后才放行子进程：并发1、每worker900秒/2048MiB、数值线程1；累计5400秒和原截止共同限制。先持久消费再计算，崩溃后保守计费并保留可能曝光，禁止自动免费重算。原 Windows OPEN 和旧诊断不变。

`evaluation/` 归档完整逐日数值、计数、原因及子进程输出，仅供评估侧；设计侧不读取这些数值。`execution/` 保存非绩效完成、资源和错误码；`summary.json` 只给完成数、曝光及资源消费和正式状态。原盲化检查约束摘要。输出版本 `exploratory-raw-price-relation-v1`，类型 EXPLORATORY_RAW_PRICE_RELATION，带 NOT_TRADABLE / NOT_FOR_QUALIFICATION；没有p/q、排名、费用推断、订单或合格裁决。

本轮工程验证先于真实执行。执行结果随后在本文件追加，不覆盖原失败、政策或锁。回滚代码不能返还已发生曝光；外部账本和证据必须保留。
