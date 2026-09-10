# R1 正式调用方输入输出适配 V1

本轮只继续 R1：合法冻结合成合同 → 正式 reader 和实际输入准备 → 部署 loader/runner/engine → 正式调用方复用的结果适配。没有运行完整 execute/start/resume/recover、PerformanceAccess、真实研究、Paper 或真实订单。合成 engine 内的订单与成交是本轮明确要求的测试对象。

## 基线与短审计

实际 origin/main 与预期相同：`e72fa6ae0ace0dbff6eeac87ae0e09082431d89a`。父提交依次为 `d3dcb68934ea8fb058c98039181d29894b6425df`、`19aa6e3c23a3afcb97a9caa0b2a6c3dbfcd22ac7`；认证 HEAD ancestry 成立。PR #7 的 R1 部分工程交付已普通合并，旧记录保留。

从该 main 创建 `codex/r1-caller-io-parity-v1`，独立 worktree 为 `E:/llmwiki/r1-caller-io-parity-v1`。原 dirty 工作区未覆盖；只核对 Git 状态，不采集源码或读取其研究数据。根 AGENTS.md/DEVAGENT.md 不存在，遵循用户提供的 AGENTS 规范及实际 CLAUDE.md。

| 调用方 | 实际准备 | runner 参数 | 实际消费 | 基线缺口 | 本轮 |
|---|---|---|---|---|---|
| CanonicalPredictiveExecutorV1 | `_prepare_inputs` | factor_values/store/exec_calendar/universe/status_map/regimes/events/index_close/policy | engine 账务、metrics、bootstrap/成本/稳健性、证据引用 | 因子列选择丢 available_at；行情推断日历；固定预热；未认证派生；数据/PIT直接 True；无落盘仍猜路径 | 覆盖 prepare、invoke、result shaping；完整正式执行仍禁止 |
| RealFactoryRuntime | 批次内独立准备 | 同类参数，直接调用两次 runner | base/10K metrics、provisional、裁决引用 | 重复路径及默认输出假设仍待适配 | NOT_VERIFIED；未运行或修改 |
| legacy 历史批次 | 已合并禁用入口 | 不适用 | 不适用 | 无本轮授权 | 保持拒绝 |

## 文件级实施与复用

- `predictive_executor.py` 保留原 `_prepare_inputs` 正式入口，新增窄 `_invoke_runner`/`_adapt_result`。原 execute 在原位置调用准备及两次同一 invoke；注册、预留、PerformanceAccess、账本、预算消费、最终裁决顺序不变。
- `caller_inputs.py` 仅承载准备和输入校验；使用部署 legacy 的真实 ResearchDataRouter、GuardedResearchReader、PITStateMap、Universe、MarketDataStore，以及已有 `derive_requirements`/真实语义编译器。没有测试专用生产替身或新许可开关。
- `data_readiness.py` 仅提取原有日线数值/包络/单位校验供双方复用，原检查及错误码不变；caller 另要求正数 prev_close。
- 新增现场合成 fixture、文件链路矩阵、无写冷进程。既有 R1 workflow 仅加新测试与冷进程原字节 artifact，原六工作流/哈希锁/选择器保持。

## 明确支持范围

| 输入/结果 | 本轮合同 |
|---|---|
| 冻结身份 | 真实治理 Scenario 在首次设计/批准前绑定政策与 registry，完成设计、审批、Freeze、物化；准备重读冻结 V2 政策和 lock，并与传入对象/合同身份比较 |
| 研究窗口 | 使用合同 research_period_identity，必须包含于冻结 policy 研究区间；不改 Final Test；最终 fixture 沿用原 V2 政策和锁，首次候选审批前声明其子窗口 20250717..20250731；不修改冻结政策/统计参数 |
| 日历/预热 | 既有 `security_state/raw/trade_calendar.json` 的 trade_dates；严格有序唯一、端点存在；由冻结 registry 的 lookback/min_warmup_bars 推导预热。拒绝缺失，不使用业务日推断、行情日期或原 20210802 常量 |
| 数据 | DAILY/RAW、SH/SZ；完整显式股票池、日期/证券唯一；OHLC、prev_close、volume/amount及 SHARE/CNY/RAW；读取前向正式 reader 传范围，不读后扩大/裁剪许可 |
| 时间 | 日线和单因子缓存均有显式 available_at；缓存原值/时区不改。None/NaT/NaN/空白/解析失败，混合或全部非法均在准备阶段阻断。合法历史/同日/naive/UTC/未来缓存时间交原 runner；未来全不可用输出 INSUFFICIENT_EXECUTION_EVIDENCE |
| 因子 | 单个已缓存、有冻结 DAILY/RAW/T_CLOSE 定义的因子。没有生成/修复缓存；多因子共享行时间缺逐依赖证明，明确 NOT_VERIFIED；缺列不派生 GAP_SIZE/inline |
| PIT | 使用既有逐日 ST、suspension 双来源规则；本轮窄准备要求所有执行证券/日显式 NORMAL/TRADING。缺源、缺日、UNKNOWN和冲突均阻断；这比 runner 的逐行拒绝更严格，不改变 runner 的 F01/F02 |
| 基准/regime | 可选基准 UNKNOWN，无基准文件读取支持。硬 regime 缺证据拒绝；正式结果中的 regime 稳健性不再称 AVAILABLE |
| BASE/10K | 分别运行独立引擎/账务，沿用既有小资金现金/仓位/lot合同；候选 max_positions 与各 policy 必须一致，不改候选凑通过 |
| 输出 | 校验三元返回结构、必需字段、类型、candidate/hash/trial/portfolio、engine身份、从真实账务重算的指标、部署脚本来源哈希；缺失不补 0/成功 |
| 默认证据 | 无写模式为 UNMATERIALIZED，metrics_ref=None，metrics 没有虚构 evidence_dir；显式根与源码/输入互不包含，实际 metrics.json 与返回内容一致才引用 |
| 正式交接 | execute 消费同一适配 engine/metrics，并保存输入诊断和有限结果摘要；已有 provisional 文件写入后才用于最终裁决引用。无交易或 engine 无效在统计/最终裁决之前报输入/执行证据不足，不把零交易判成 Alpha 失败 |

范围不含 V1 caller 政策、分钟/事件、PIT_QFQ、公司行动处理、inline/new factor 生成、基准文件、真实缓存公式/时点来源真实性、wheel、其他正式调用方和全部深层分支。输入内容哈希只绑定本次消费内容与身份，不认证历史 provenance 或授权。

## 验证口径与失败保留

基线丢列 red：真实治理生成的合成 fixture 经原 `_prepare_inputs` 丢失 available_at，`red.log` 为 1 failed。该最初 fixture 在设计前生成了起点为20250717的合成政策和锁；最终 fixture 不再重建政策，完整保留原 V2 政策，仅在首次候选审批前声明政策内子窗口。不是手工 DataFrame 直接调用 runner 的替代证明；历史 red 与最终 fixture 的差别明确保留。

新增链路正向对照从同一现场文件独立准备参考输入：逐项精确比较因子/时间、日历、股票池、store、信号与排序、订单、成交、lot/退出、metrics/来源。确定性字段不排除、不增加容差；运行时间/临时路径不作为数值相等条件，输入目录前后内容和 mtime一致。实际产生 BUY/SELL；小资金独立账务同样有执行。

新鲜进程从真实文件再次完成 prepare/invoke/result，不加载 pytest 的替身；禁止写、网络、子进程、完整正式执行和 PerformanceAccess。主进程 engine 调用与该子进程计数分开记录。既有合法治理 fixture 的审批/确认次数不伪写为零。

失败分层：首轮扩展 53 passed/1 failed 的 record 负向在非法 ExitPredicate 构造时被拒绝；结构 fixture 首次遗漏新语义 fingerprint，真实治理服务拒绝；支持范围负向首次缺 hypothesis lineage/第二因子 roles，合同装配提前拒绝。这些日志均保留，不计业务 red。修正的是首次冻结前的合成装配，不修改治理验证或已冻结候选。

T1–T14 由新文件链路矩阵与原 R1 用例共同覆盖。T6 的合法结构退出走完整文件链路；无效时间在新准备层先阻断，原已合并 runner 的已持仓空值/固定持有/F01–F04矩阵继续原样回归。T9 新用例覆盖两个输入根、不同 cwd、输入根脚本毒化；同名 sys.modules、缺部署脚本等仍由原 loader 负向覆盖。

本地环境：新 venv，Windows Python 3.13.5，原 requirements-p3b.txt 及递归 hash 锁，正确官方 `https://pypi.org/simple` 安装；pip check通过。源码 E: exFAT，临时 fixture C: NTFS。前端 Node 24.15.0，构建及8项测试通过。依赖安装不代表业务通过。

外部证据：`E:/llmwiki/r1-caller-io-evidence` 中 install/red/各轮 green 日志、XML、cold stdout.bin/stderr.bin、原 workflow 精确命令与 local-suites.json。最终本地回归数记录在 progress.md；提交后的 SHA、push/PR CI run与平台 patch 另存外部记录/PR，提交时远端 CI=PENDING，不使用 PR #7 的旧 CI 代替。

## 保留状态与停止点

```text
WORK_PACKAGE=R1_CALLER_INPUT_OUTPUT_PARITY
HISTORICAL_PROVENANCE=UNVERIFIED
WINDOWS_L1=OPEN_ROOT_CAUSE_UNCONFIRMED
WINDOWS_L6=OPEN_ROOT_CAUSE_UNCONFIRMED
REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED
READY_FOR_REAL_TRIAL=false
R1_FULLY_CLOSED=false
MAIN_MERGED=false
R2_STARTED=false
```

旧 Windows 事件不因新测试通过而关闭；复发留原始输出、状态和工件，不扩大 timeout/白名单，不无假设重跑。默认只读、无 Web startup recovery、人工门禁、预测执行禁令、原锁和统计/预算/最终裁决规则保留。

下一次真实只读核验仍需单独明确：候选/合同 hash、V2政策与锁、registry/公式来源、数据根与指定文件、含预热日期范围/字段、日历、逐日双源PIT、带来源的因子时间、单位和价格模式、独立输出及资源限额。本轮未读取任何真实研究数据。

完成提交推送和分支认证后停止，等待独立差异复核；可建 Draft PR，不 merge/auto-merge，不开始 R2。回滚使用 `git revert <本轮实现提交SHA>`；回滚点为上述 e72fa6a 合并基线，历史日志保留并追加撤销说明。
