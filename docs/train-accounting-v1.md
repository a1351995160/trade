# 固定候选 TRAIN 账户入口与公司行动 V1

本版本承接 eba7b861915caab812caa3e83b5a5a6496e26fb1，仅落实当前用户批准的固定参考账户复现。旧正式研究计划、V4、历史预算消费及 Windows OPEN 事件不变。不读取 Validation / Final Test，不运行 Paper 或交易接口。

## 固定合同

RETURN_5D < 0，因子升序，数值相同按 symbol 升序确定次序，Top-3。收盘后产生信号，下一独立交易 session 开盘最早成交。实际成交 lot 的 entry index + 3 到达时收盘决定退出，下一 session 开盘最早卖出。资金 10000 元，最多三仓，每手 100 股，佣金 0.00025、最低 5 元、卖出印花税 0.0005、参与率 0.10、滑点比例 0.001。没有参数搜索。

复用 BacktestEngineV2、broker、订单、原费用计算和 PortfolioExitEvaluatorV1。CorporateActionBacktestEngineV1 是显式离线版本，旧账本和旧引擎行为不改。原新颖性 Gate 的固定参考重复结论保留，回执用途为用户明确指定的账户复现，不冒充新 alpha，也不清除历史曝光。

## 外部输入验收

唯一所有者入口为 `E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/accounting-v1/owner-export/OWNER_DELIVERY_MANIFEST.json`。本轮未收到此包。

要求 OWNER_EXECUTION_EXPORT_V1；窗口 20220722—20240731；所有者、物理限窗声明和六个精确文件角色：daily_supplement、state_supplement、security_master、units、unit_source_evidence、actions_manifest。每项绑定同目录 basename 和真实 SHA256。Parquet 行组日期统计必须先证明全文件限窗，再读数据行；不沿额外角色或新引用扩读。

补价格只允许追加缺失证券日；已有证券日仅可叠加来源参考价和发布时间，原 OHLC/量额不允许变化。保留全部 5182 原成员、146 缺源成员和 2700 预热缺格。原缺格只有取得明确非成员或停牌证据后才可作为无 Bar 日保留。

原历史状态 available_at 是下一 session 开盘时间模型。旧字段不覆盖；所有者必须证明原生产者语义，并提供实际观测时间来源，才建立独立 effective_state_available_at。原字段若是真实较晚时间证据仍优先。日线 modeled_available_at 为当日收盘后，已知较晚时间取最大值，UNKNOWN 保留。RETURN_ONLY 的原 registry 定义不改，复用原 FactorCompiler 的五 session 收益定义，六个完整收盘价不足不填值。

单位必须由对应版本的本机来源证明，不猜 shares/手或元/万元。公司行动除权日需来源给出的交易所参考价，不能用理论复权价替代涨跌停参考价。新输入按内容身份另存，不覆盖 materialized-v3。

## 公司行动合同

WindowedCorporateActionDatasetV1 绑定固定窗口、数据集身份、事件文件哈希和完整成员覆盖。空事件文件只有所有者明确完整性声明才接受。不得来自此前跨窗 incident 响应，不再调用 get_divid_factors。

现金分红要求每股金额、权利日、支付日、来源及明确净额/固定每股税款规则。除权时建立应收款并计入权益，支付日前不进入 available_cash。支付、重复事件和检查点恢复幂等。

拆合股保持总成本，改变股数和每股成本。送转创建继承原持有期的新 lot，原股份和新增未到账股份分别记账；新股份按明确 credit/tradable 日期锁定。pending_share_credits 表示经济归属但尚未到账的股份。已有退出委托在股数变化后保留原最早成交时间，不能顺延一日。

V1 拒绝碎股、配股、无明确退市结算，以及送转权利登记后发生持仓变化或尚未到账股份叠加另一股份事件。异常保留部分账户证据并标 INCOMPLETE_FAIL_CLOSED，不产出完整可执行结论。实际事件覆盖和资源规模尚待真实所有者包验收；合成通过不等于真实账户完成。

## 执行与恢复

在本工作区，设置 `PYTHONPATH=src`，使用 `.venv/Scripts/python.exe scripts/run_train_account_v1.py --execute-approved`。此前实际调用已在数据包缺失处拒绝；没有新补件不要重复相同命令。包到齐后，该命令依次验收、写新输入身份、确认原 Objective 的用途受限增量、预留、受限 worker 启动、记曝光、运行和结算，不再停在 READY。

预算由原 SearchBudgetRegistryV1 管理，新增 1 主 + 1 确证修复，不修改旧消费或旧修复余额。输入未就绪不发服务回执。有效期不得晚于原回执或 2026-09-14T10:05:03+08:00。worker 使用原资源限制器，并发 1、单次 900 秒/2048 MiB、数值线程 1，主+修复累计 1800 秒；进程 PID、资源和退出结果归档。真实规模在数据包缺失期间没有基准实测。

`--recovery-status` 只对账、不重跑。RESERVED / 已消费但未写 STARTED / 未结算都不可免费重复。中断后先核对既有 worker_started、process、completed 与原预算；确认 worker 已结束后，调用同一 TrainExecutionGovernanceV1.settle，按证据记录耗时，无法恢复耗时则保守计入本次 worker 上限，completed 必须有原结果哈希证据。服务对重复结算幂等，已消费但缺 STARTED 保留 POSSIBLE_CHARGED。它不是整场回测断点自动续跑；账本 checkpoint 可恢复事件和资产状态，不授予新执行权限。

修复命令附 `--repair-proof <JSON>`，严格包含 fix_commit、red_evidence、green_evidence、affected_contract。red/green 文件必须匹配同一失败案例和冻结合同；green 另含 affects_input=false、实际代码哈希。仅支持不改变已冻结输入的确证代码修复；输入变更需要新版本证据，不能凭旧 READY 文件重算。

输出完整订单、拒单、成交、费用、滑点、lot、持仓、现金、应收款、公司行动审计、逐日权益、退出决定及未结事项。训练指标只作描述；闭合 lot 的交易损益与公司行动收入分列，费用加回数不是零费用重跑或统计资格。

## 验证与当前状态

最终 85 项合成/原回归通过，包括原探索治理和旧输入适配；网络、额外进程、受保护目录探针均 0。没有重跑完整 CI、V4 或旧探索。

TRAIN_EXECUTION_INPUT_READY=false；CORPORATE_ACTION_ACCOUNTING_READY=true（仅上述版本范围及合成验证）；TRAIN_ACCOUNT_BACKTEST_STARTED=false；TRAIN_ACCOUNT_BACKTEST_COMPLETED=false；主/修复新增曝光均 0。三个正式资格标志仍 false。

外部交付只保留一个 EXTERNAL_DATA_BLOCKER_PACKET，详细对象、字段、成员、日期、提供者要求和实际拒绝日志放在 accounting-v1。旧证据不改；回滚工程可基于 eba7b86 逐项撤销本轮文件，审计和已发生访问记录不得删除。
