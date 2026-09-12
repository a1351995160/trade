# 固定探索策略的回测与信号预览一致性

本次在独立工作区 `E:/llmwiki/fixed-strategy-signal-parity-v1`、分支 `codex/fixed-strategy-signal-parity-v1` 实现，基线为 `27d6a861cd34f113c2b02d678c5734ca9a1a686f`。2026-09-12 经用户明确批准，已将改动集成到 `E:/llmwiki/bounded-offline-strategy-research-v1` 的工作区，尚未创建 Git 提交。集成前确认取数结束、没有 BaoStock 运行进程，且目标工作区干净。

## 已完成的衔接

`fixed_account_rules.py` 承接既有固定合同的入场排序、数据可用时间、已有持仓排除、Top3、公司行动拒绝和按交易 session 计算的 lot 退出。`degraded_execution_v2.py` 的真实账户回调和新增 `fixed_account_preview.py` 共用这些规则。事件拒绝后不补位，订单、费用、成交和账户执行仍由既有引擎负责。

`preview_fixed_account_plan` 只接收已经准备好的内存输入：固定 BaoStock 合同、交易日历、账户 ledger、单日因子切片、hazards、unsupported_lots，以及合同和输入身份。它读取本地源码计算身份，不取行情、不写文件、不创建订单。预览使用账户副本，避免退出器修改调用方的 lot 状态。

| account_stage | plan_at（上海时区） | 输入账户时点与结果 |
| --- | --- | --- |
| POST_OPEN_ORDERS | 交易日 09:30 | 既有订单已处理、尚未选新买入；使用上一交易 session 的因子生成入场候选 |
| AFTER_CLOSE | 交易日 15:30 | 退出判断前；逐 lot 判断退出，展示下一交易日最早执行时间 |

调用方必须提供对应时点的账户，不能把开盘前账户当成已处理订单的账户。收盘预览不提前生成次日买入。持仓展示包含剩余数量、可卖数量，以及 HOLD、EXIT、SELL_PENDING 或 BLOCKED；部分卖出仍展示剩余数量。

`factor_rows=None` 表示缺数据，返回 NOT_READY；带完整字段的空表才表示已核验的空候选。重复证券、非有限因子、错误来源日和无时区的可用时间会被拒绝。有退出但日历缺少下一 session 时返回 NOT_READY，不伪造成交。

计划身份绑定合同、输入内容、完整账户和源码。两个现有执行脚本的源码身份清单已加入共享规则文件。旧源码冻结与新源码不一致时必须继续拒绝执行，不能绕过或覆盖旧回执。

## 当前适用范围

入口仅支持既有 BaoStock 固定合同及 20220801—20240731 TRAIN 探索窗口。输出保持 `NOT_FOR_QUALIFICATION`、`TRAIN_EXPLORATORY_SIGNAL_ONLY` 和 `execution_ready=False`。输入 hash 不能代替来源与使用授权验证。

入场信号是候选，不是已通过资金、成交容量等约束的订单；退出决策也不是已成交记录。本次保持原探索合同的未来事件排除口径，因此其探索偏差仍然存在，不能据此声称具有实时预测能力或统计有效性。

尚未实现最新真实行情装载、实时账户快照适配、策略自主搜索与验证闭环，也没有启动真实回测、Paper 或交易。上述事项不能由本次合成测试通过推导为已完成。

## 验证与集成

在本工作区独立 `.venv` 中，按 `.github/workflows/requirements-p3b.txt` 的哈希锁安装依赖。测试设置 `CHANLUN_TEST_ISOLATION=1`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=src`，并将 OMP、OPENBLAS、MKL 的线程数设为 1。

运行 `python -m pytest -q`，选择 `tests/research_factory/` 下的 `test_fixed_account_signal_parity.py`、`test_degraded_execution_v2.py`、`test_baostock_price_views_v1.py`、`test_baostock_governance_v1.py`、`test_baostock_preparation.py`、`test_train_account_runner_v1.py`、`test_daily_plan.py`。`--basetemp` 使用 C 盘独立 NTFS 临时目录；E 盘 exFAT 不支持旧治理测试需要的硬链接。不得为此去掉硬链接保护或跳过断言。

集成已同时带入共享规则、预览、回测调用点、两个源码身份清单和测试。外部取数数据、原回执、预算和批准文件均未改动；没有启动输入准备或真实回测。集成前尚无 CODE_FREEZE.json、INPUT_READY.json；原任务继续执行前应重新核对当前事实，按原授权和实际入口生成源码身份，不复用旧身份或绕过冻结检查。

目标工作区的测试使用本次修复专用的独立 `.venv` 解释器，工作目录及 `PYTHONPATH` 指向目标工作区；不是复用原研究环境运行测试。原任务可先查看 `git diff` 和本说明，再在既有授权内继续输入物化、可行性检查与固定账户探索。不能把本次预览当成最新行情接入或资格升级，也无需为代码集成重新下载已有合格行情。
