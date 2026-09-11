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

## 已执行交付：2026-09-11

状态：EXPLORATION_COMPLETED。A_main、A_placebo、B_main、B_placebo 全部完成，四份序列均存在可计算关系；这只是计算/归档完成，不是关系具有预测力或候选通过。

- 冻结代码提交：a6e0440（完整提交由确认回执 plan.code_commit 保存）。
- 计划身份：8a6da87671d9e7d1a52c2d69d4ec36d518249e47ede14878672b9163ba3fc3b0。
- 确认回执：e1912600dd1ca8fa4ec378b0c4553ea4be678fc1aed7b5f3dd226778e2f70c33。
- 实际新增真实价格曝光4、可能未明曝光0、工程重算0；预留/开始/结算各4条，无未结算尝试。增量剩余2仅限确证修复重算。
- worker累计wall 28.447202899998956秒；正式资格Trial为0，旧正式计划BLOCKED、V4不获准、三个正式标志false。
- 输入实际核对：获批SHA256、2,370,849行、5,036观测成员与固定来源清单完全一致；训练日历486 session。总体5,182与缺源146保留。

完整本机证据位于 E:/llmwiki/autonomous-strategy-research-v1/revised-exploration-v1/：

- `governance/confirmation.json`：四份合同、代码/输入/证据身份、明确批准及用途增量引用。
- `governance/search_budget_registry.json`、`governance/exposure_events.jsonl`：原预算组件的实际消费与结算。
- `summary.json`：设计侧盲化结果。
- `archive_verification.json`：四份评估结果的精确路径、SHA256及完整性检查；不含均值或曲线。
- `evaluation/<contract>/<execution_id>/result.json`：完整数值仅供评估侧，不返回设计侧调参。
- `execution/`：每次Windows资源安装记录、完成回执和退出状态；`frozen_source/`：执行时原始源码字节归档。
- `archive_verifier.py`：只校验已存结果及账目，不重算价格关系；四份结果的合同/输入/日期/覆盖/哈希及源码归档检查全部通过。

工程70项测试通过，实际入口exit0，评估侧归档核验exit0。设计侧未取得精确均值、p/q或序列；没有收益方向反馈后调参。此次任务到固定四实验结算归档为止，不使用剩余额度另起探索，不触发正式资格、Paper或订单。
