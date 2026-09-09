# R1 源码闭包与数据就绪核验 V1

## 业务结论与范围

本轮交付受限的只读合成输入核验及源码来源修正，不能将正式预测路径认证为闭包通过。`run_engine_corrected_phase4_v3.py` 在发布仓库及取回的全部 Git 历史中缺失，其内部 legacy 依赖链未知。没有恢复、替代或编造算法，也没有访问原研究工作区补拿文件。正式源码闭包为 **BLOCKED_MISSING_SOURCE**；实际可冷加载的入口为 PARTIAL 正向证据。

基线 `1f6f29c7ad3e8d9371168dfa3bd5201723b48abd`，父提交为 `e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08` 和 `7556b3d1e800b22df8e8645a010466c20126810a`。已 fetch 并核对 origin=https://github.com/a1351995160/trade.git。从开发库创建独立分支 `codex/r1-source-closure-data-readiness-v1`，worktree 为 `E:/llmwiki/chanlun-trading-system-r1-source-closure-data-readiness-v1`。没有在 P3-C 分支累加。

项目只有根 CLAUDE.md，无 AGENTS.md/DEVAGENT.md 或下级同名规范；另遵守用户提供的 AGENTS 指令。已读取附件、总体规划、源码基线说明、P3-A/B/C 和 progress 历史。附件中的交付建议按用户本轮明确授权使用，历史文档中的执行命令不作为研究授权。

初始命令在会话默认原目录尝试读取不存在的 AGENTS.md，并运行 Git status/remote/branch；status 枚举了未跟踪文件名。没有读取那些文件内容，之后所有命令显式绑定开发库/独立 worktree。该元数据枚举不记成“零接触原目录”；没有读取真实行情、合同、绩效、Final Test 或未提交源码。

## Phase 3 工程收尾

用户已提供 PR #5 普通 merge 与独立最终差异复核结论，本地核对 merge 父子关系一致。`PHASE3_ENGINEERING_CERTIFICATION_CLOSED=true` 仅指已认证 synthetic 入口与 Windows/Linux 平台；P3-C 最终 HEAD 为 `7556b3d1e800b22df8e8645a010466c20126810a`。原文中的 false/PENDING 是历史证据，保留不改。

P3-A 默认只读、无 Web startup recovery；P3-B 公共恢复/attempt；P3-C 完整语义审批/授权一致性均保留。没有修改日期、预算、执行权限或真实 canonical 状态。

## 短审计与实施落点

1. 两处 corrected loader 混用研究 root/scripts。新增 `source_dependencies.py`，仅从实际部署源码 checkout 精确定位；`predictive_executor.py`、`real_runtime.py` 复用它，engine hash 同样引用源码根。缺脚本明确失败，数据根同名脚本不会执行。
2. `CodexResearchAgentBackendV1` 默认从 adapter 的数据根读取正式 prompt；空根构造先复现失败，再改为默认从部署源码读取，显式 prompt 注入仍保留，未改进程/AI 调用逻辑。`GuardedResearchReader` 原默认每次写相对 cwd 的审计文件，增加显式 `audit_sink` 内存出口，默认行为不变；只读核验不更改全局 AUDIT_PATH、不 monkeypatch 生产 reader。
3. 新增 `data_readiness.py`，从 Durable 重建、provider payload、语义编译、显式冻结政策/锁及因子定义派生需求；在系统临时目录合成包中读真实 Parquet 并校验。
4. 新增冷加载 worker、合成包 fixture、正负向认证以及独立双平台 CI；其余正式算法、reader 路由、Guard、预算及恢复协议保持原状。

## 依赖与部署矩阵

部署契约为完整**源码 checkout**，不是 wheel：从 checkout 使用 `PYTHONPATH=<checkout>/src`，Python 依赖使用 `.github/workflows/requirements-p3b.txt` 的现有 hash 锁安装。Windows Python 3.13、Linux Python 3.11；具体 patch 版本取证于每次 CI。前端沿用 package-lock、npm ci --ignore-scripts、build。config.yaml 为源码资源，其本机 TDX 路径只表示尚待显式配置的外部数据依赖，本轮不改运行配置。

pyproject 只发现 src packages，并未打包 scripts/docs/config 或声明完整运行依赖；因此 **wheel deployment NOT_VERIFIED/不受支持**。不利用开发机偶然 sys.path 宣称可部署。

| 正式入口 → loader | 依赖/预期来源 | 本轮证据与状态 |
|---|---|---|
| CanonicalPredictiveExecutorV1._corrected_module | checkout/scripts/run_engine_corrected_phase4_v3.py | 文件及 Git 历史均无；BLOCKED_MISSING_SOURCE；冷进程实际调用 loader |
| RealFactoryRuntime 路径 → 同一 loader | 同上 | 静态调用点已归一；独立调用同一 loader，未进入真实 run；BLOCKED_MISSING_SOURCE |
| corrected → legacy | ResearchDataRouter、ResearchDataAccessGuard、load_universe_sets、PITStateMap、load_market_index、market_regimes、build_store、load_events、sha256 | 调用方可确定的接口；legacy 实际模块名/完整内部依赖未知，不假设已闭合 |
| corrected → 执行与统计 | run_corrected_candidate、Side、bootstrap_result、concentration、subperiods、regime_split、recompute_cost_stress_metrics、classification | 调用方可确定；原始实现、内部导入与算法契约缺失，不反推算法 |
| PredictiveTrialStart/再授权 | checkout 中 executor、治理和恢复模块 | import 和 auto_run=False 构造通过；start/resume/recover_all 未调用 |
| Structural Provider | GuardedResearchReader、FactorCompiler、PIT/安全状态模型 | 真实 import 和空 root 构造通过；build 未调用；FactorCompiler 是已提交 FactorExecutor 的别名 |
| Durable → 语义重建 → compiler | durability.py、strategy_candidate.py、strategy_semantic.py | 合成合法合同 provider payload 与 compile 正向；未调用发信号/引擎执行 |
| BacktestEngineV2 | engine/ledger/broker/portfolio_exit 等已提交模块 | 实际 import 通过；运行分支 NOT_VERIFIED |
| ResearchDataRouter | 数据根 routing_policy.json、daily_all.parquet、market_5m、明确 TDX root | import 正向；空根构造是 MISSING_DATA_POLICY，区别于缺源码；未调用下载或路径回退 |
| TDX 7709 Provider | data/minute/tdx_raw_hq_provider.py、pytdx | 真实模块/包版本已加载；未 fetch/connect，不重写下载器 |
| minute manifest/validator/gap | 已提交 base/manifest/validator/gap_resolution | 冷加载；正式 reader + validator 另以生成 48 根 bar-end 正向和非法标签负向验证 |
| Codex prompt/schema | checkout/docs/CODEX_RESEARCH_PROMPT_V1.md；_proposal_schema() | 精确源码 root 正向读、schema 校验；移除资源副本后 MISSING_RESOURCE；不调用 AI |
| Web/config/frontend | config.yaml、构建的 frontend/dist、create_app | YAML 读取、原 P3-A 先验与前端 build；真实 TDX 配置 NOT_VERIFIED |
| 冻结政策/registry | 显式数据包内引用，不是源码模板 | 合成政策锁与文件哈希校验；缺真实政策及因子 registry 为 NOT_VERIFIED |

来源恢复调查命令：`git log --all -- scripts/run_engine_corrected_phase4_v3.py` 无输出。相似的 `scripts/run_broad_search_corrected.py` 来自发布根提交 `b49ab4b1fa8cf08929bb7abbcc22c5aca78a9fd7`，使用 CustomBacktestRunner，不具备上述接口，不能等价替换。发布基线记录的原提交 42fbc52a 并非发布仓库历史，不去原研究库查找。

**最小源码补充需求**：提供有不可变 SHA/来源路径的 corrected 原脚本、其全部非标准库 helper 及资源清单，保留当前 corrected fill、PortfolioExitEvaluator、成本压力和统计分类契约；随后对每个内部延迟分支重做冷加载。当前无法知道脚本内部完整链条，不将未知数量写零。

## 数据需求矩阵

| 维度 | 派生来源与本轮证据 |
|---|---|
| 身份 | P3-C Scenario 经真实设计/批准/Freeze/物化；Durable candidate/hash/schema/content_hash、policy_identity、registry_identities、research_period_identity 原样输出；诊断包另绑定 policy_hash/registry_hash/dataset ID/version |
| 时间 | 请求 2024-07-03..2024-07-04，显式合成 session 日历；示例因子定义声明 2 个预热 bar，派生 2024-07-01 起；Asia/Shanghai、T_CLOSE/available-at；不改 2025-07-31/2025-08-01 硬锁 |
| 字段 | 合同 required_data/frequency、OHLCVA 和各因子 required_fields 并集；因子依赖图递归解析；缺定义时 warmup=None 并阻断，不猜预热 |
| 股票池 | 显式 securities × frozen calendar 为状态分母；必须有 listed/ST/suspended/liquid 与 available_at。只有确认未上市/停牌才从行情分母排除；缺行情不能反推停牌；未知状态拒绝 |
| 调整 | 合成检查 RAW、SHARE、CNY；未知公司行动和已知事件均不认为已支持。现有 CorporateActionGuard.from_tdx 异常返回空集合，不能据此证明无事件；processor 未实现，未修改它 |
| 因子/事件 | 复用 UnifiedFactorRegistry、FactorCompiler。值与声明计算图精确比较；future available-at 拒绝。事件/分钟候选与 PIT_QFQ 组合尚未集成，明确 UNSUPPORTED/NOT_VERIFIED |
| 来源 | GuardedResearchReader 实际 Parquet；不是 manifest 自述。来源文件前后 SHA、policy lock、registry、合同、窗口、相关源码哈希绑定报告；身份变化不可复用旧结果 |
| 质量 | 行数分母来自独立日历/状态；检查重复、缺字段/股票/预热、OHLC、数值、单位、时区、时间戳；48 bar 正向只认证 minute reader/validator，没有声称全市场 minute 覆盖 |
| 执行补充依赖 | 正式 executor 还要求 prev_close、市场基准、PIT、路由/股票池政策、factor cache、事件；warmup 常量 20210802 保留。缺 corrected 导致完整执行需求一致性 NOT_VERIFIED |
| 用途 | 仅 SYNTHETIC_DAILY_INPUTS 的局部就绪；真实候选、历史 PIT 可复现、Trial 权限均 NOT_VERIFIED/false |

**合成因子定义边界**：发布源码没有 VOLUME_ACCEL 原始 registry 定义。本轮 fixture 显式声明测试计算图 `field(volume)` 及 2 bar 预热，仅证明“输入定义 → 需求 → 实测”的传递和计算校验；它不是 VOLUME_ACCEL 的真实公式，未纳入生产 registry、不冒充算法恢复。真实公式/版本/预热仍缺证据。缺 registry 定义的独立负向保留。R1 在 Scenario 首次设计前将初始合成 Objective 的 policy_id/version/hash 与 registry hash 明确绑定，再经原审批链派生合同；不升级历史 P3-C 或真实合同。核验要求这些合同引用与实际加载文件一致，重算合同哈希也不能掩盖引用冲突。

## 只读接口与限制

```text
python -m chanlun_trader.research_factory.data_readiness --source-root <绝对checkout> --dataset-root <系统临时目录中的合成包> --start 20240703 --end 20240704
```

默认只输出 JSON，无报告文件/缓存/修复/业务写入。示例包由 tests/research_factory/r1_fixture.py 现场生成；readiness.json 显式引用合同、政策、锁、因子 registry、日历、证券、三个 Parquet 文件。它是测试/诊断输入清单，不是 canonical manifest 或新的授权协议。

返回 `SYNTHETIC_SCOPE_READY` 仅说明该合同/定义/数据/窗口下已检查的日线输入通过。报告始终包含 REAL_DATA、HISTORICAL_SOURCE_AVAILABILITY、CORRECTED_EXECUTION_INPUT_PARITY 未验证，真实 readiness 为 NOT_VERIFIED，ready_for_real_trial=false。新核验始终重新读取引用；previous_identity 只比较，不跳过校验。单次读取期间文件发生变化则 CONFLICT，不修改原冻结集。

入口先检查请求日期，再做临时目录词法约束与既有链接/junction 校验；拒绝 source/data 重叠、越界引用和原目录。每个 Parquet 分片先检查日期统计，必须整体落在本次含预热范围，再读/哈希内容；缺日期统计拒绝。此方式只支持独立合成分片，不认证混合封存窗口大文件或抗恶意并发替换的 OS 沙箱。空/未知数据不生成乐观 READY。

## 验证与未覆盖矩阵

冷 worker 无 pytest conftest，使用全新进程、复制的纯 src/docs/config，无历史产物；生产模块来源都须位于该副本。禁止文件写入、业务网络/进程和执行函数调用，仅拦截副作用，不提供 sys.modules 替身。缺 corrected 是真实阻断，不把它计入正常加载。现有 pytest conftest 只在回归主进程拦截 Predictive.execute、Structural.build、Codex.invoke；这与冷加载证据分开。

| 验证 | 当前证据范围 |
|---|---|
| S1/S2 | 17 正式模块来源、两个缺源码 loader、数据目录同名脚本拒绝、空数据 root、服务安全构造 |
| S3/S4 | 缺脚本与缺数据政策/缺 prompt 资源不同；包实际版本记录；Windows/Linux 由分支 CI 取证；独立缺第三方包的诊断分支未认证 |
| D1/D2 | 实际完整合成日线正向；缺文件/字段/股票/预热/因子定义与“manifest COMPLETE”负向 |
| D3/D4 | 独立分母、确认停牌正向、未知状态拒绝、future 因子/行情、封存日期先拒绝、单位冲突、分钟非法 bar 标签 |
| D5/D6 | dataset version 变化旧身份失效；读取期间真实文件变更拒绝；合同/政策篡改；两个数据包隔离、根链接与路径逃逸 |
| D7/D8 | 前后内容/mtime 快照、独立进程写入拦截；局部正向仍无真实 READY/Trial 权限 |
| G1/G2/G3 | 原五阶段选择全部保留，未新增 skip/xfail/排除，原 P3-A 先验在 collect 前 |

本地最初 20 passed / 1 failed 为 Pandas 3 的 bool 列不能直接赋 None 的测试夹具问题；修正 object 列后 25 passed，随后补充 minute/无写子进程测试。保留 r1-data-first.log，不能称它为生产缺陷 red。新的负向记录是实际不合法输入的拒绝证据。回归脚本初版误匹配安装步骤，pip 环境探测被非 Python 进程拦截（计数 1、exit 79），未触及真实内容；修正脚本仅选 pytest/compileall 后重新执行，保留日志，不把早期计数抹零。

无写入子进程先复现 tempfile.gettempdir 首次调用试写（28 passed / 1 failed），移除该隐式试写后同一用例通过，整组 29 passed。随后源码资源追踪复现 backend 从空数据 root 读 prompt（1 failed），修复默认来源后 1 passed，原 Codex backend 测试也通过。保留 r1-final.log、r1-readonly-green.log、r1-prompt-red.log 与 green 日志。

08fdd307 实现提交之后，补充“合同重哈希但指向另一政策”的负向先 1 failed（r1-policy-ref-red.log）。新增合同与 policy/registry 的引用一致性校验，合成定义在首次审批前钉住；合法正向及政策负向先 3 passed，继续加入 registry 引用冲突。此修正只影响诊断校验及测试初始资料，不改变治理合同/预算协议。最终证据须绑定后续提交，不能用 08fdd307 的 CI 代替。

本地、最终分支 SHA/CI 精确结果追加到 progress 与最终交付；提交时 CI=PENDING。日志/JUnit 位于忽略的 tmp/r1-evidence、tmp/r1-results.xml 及分支 CI artifact，不能充当真实研究成果。

未完成：corrected/legacy 原实现闭包；真实因子公式与政策/数据冻结引用；事件、PIT_QFQ、基准及完整分钟候选核验；真实 PIT 历史资格来源适配；全部正式执行分支、真实数据和 wheel。上述缺口使 R1 整体不能 CLOSED。

## 后续真实数据的最小批准范围（本轮不执行）

需要明确候选/合同哈希、政策/registry 版本与具体 dataset ID；允许读取的绝对源目录和具体文件种类（按矩阵所需日历、状态、日线/因子/事件/基准分片选择），允许日期及预热、列和是否可读内容；只读输出位置、内存/时间/行数上限及遇冲突/封存/未知来源即停止的条件。不能笼统授权所有 data/reports。真实访问入口需要该明确范围落实后再接入，当前 CLI 拒绝非临时合成根。

明确排除绩效、Trial 结果、Final Test、自动下载、真实 Structural/Predictive、Paper/Prospective 与订单。不猜 dataset root 或选择候选。

```text
WORK_PACKAGE=R1
PHASE3_ENGINEERING_CERTIFICATION_CLOSED=true
R1_ENGINEERING_IMPLEMENTATION_STATUS=PARTIAL
FORMAL_SOURCE_DEPENDENCY_CLOSURE=BLOCKED_MISSING_SOURCE
REAL_DATA_ACCESS_AUTHORIZED=false
REAL_DATA_ACCESS_PERFORMED=false
REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED
READY_FOR_REAL_TRIAL=false
R1_FULLY_CLOSED=false
MAIN_MERGED=false
SUBSEQUENT_WORK_PACKAGE_STARTED=false
REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO
```

提交/推送独立分支后停止，交独立工程复核；不 merge/auto-merge、不开始 R2。回滚用 `git revert --no-edit <R1实现提交SHA>`；文档提交可单独 revert，不 reset main，不删除历史记录。
