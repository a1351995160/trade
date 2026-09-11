# 剩余路线连续工程交付

## ffbff08集中审计后的修正状态

独立集中审计结论为CHANGES_REQUESTED，CA-01～CA-04按同一总分支统一修正，当前尚未完成修正差异复核。最新范围、服务级red/green、记录兼容和最终证据见[集中修正清单](CONSOLIDATED_REMEDIATION_CA01_CA04.md)。下文先前交付状态属于被复核版本，不表示四项已关闭。

本文件是工程进度与证据索引，不是运行授权或 canonical 状态。用户于 2026-09-10 批准 R1/R2/R3/D1/D2/M1 连续工程和隔离完整合成服务验收；旧阶段文档的小包停止点保留为历史，不再作为本次工程停止点。

## 接续基线

### 当前统一批准清单（2026-09-10 接续 e288746）

本表覆盖下文历史阶段的待批状态；历史记录和既有集中审计 ZIP 不改写。工程批准不构成具体运行授权。

| 独立协议 | 最新批准及边界 | 当前工程状态 |
|---|---|---|
| A Objective execution_binding | 已批准新版本创建前绑定政策、研究窗口及 registry；确认复核并复用原创建事务；不迁移 v1、不改变预算/统计/其他门禁 | 本地组件及正式 R2 合成闭环通过；最终整体/双平台待验收 |
| B synthetic R3 有界批次执行授权 | 已批准明确已审批/冻结/物化候选集合的版本化预览、人工测试确认、受限委托、实际有界执行及暂停/停止/到期/撤销/恢复；必须强制资源与原账本边界 | 服务、正式API及统一页面已实现；单/多候选、控制、恢复、性能前竞争及实际页面本地通过；最终整体认证待验收 |
| 事前新颖性比较集绑定 | 已批准明确来源全集、结果盲化、精确自身排除、实际确认、性能前复核及原 Gate；不授予启动权 | 组件及正式 R2/R3 合成本地验收通过；最终整体认证待验收 |
| 计划/Paper 测试使用资格 | 已批准仅隔离 synthetic 的请求、真实确认、用途及有效期、撤销；不授予 Trial 或批次权限 | 已有服务及子链验收；不能充当真实资格 |

A、B 分别实施和记录，不互相代替，也不替代另两类合同。真实数据保持 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false、R1_FULLY_CLOSED=false；真实 Trial/Paper/订单及旧 CP 预测执行仍不授权。

接续顺序：A 合同与兼容测试 → 正式创建至完整 R2 合成闭环 → B 实际批次与资源/恢复测试 → 固定新 HEAD 的整体回归、双平台及新版集中审计。沿用当前总集成分支和 Draft PR #9，不 merge main、不 auto-merge。所有必要工程验收前保持 PARTIAL。

当前 R2 进展见 [R2_FORMAL_SYNTHETIC_SERVICES.md](R2_FORMAL_SYNTHETIC_SERVICES.md)：实际两个 engine、裁决、registry 和失败回流完成，并通过新进程重放及已消费同 Trial 的实际确认恢复。固定合成输入最终 BLOCKED；无真实合格策略。148 项本地阶段回归通过，不替代最终新 HEAD 双平台或 B 的批次边界验收。

当前 B 进展见 [SYNTHETIC_BATCH_AUTHORIZATION_V1.md](SYNTHETIC_BATCH_AUTHORIZATION_V1.md)及[SYNTHETIC_BATCH_RESOURCES_V1.md](SYNTHETIC_BATCH_RESOURCES_V1.md)。新批次不再是只读申请，但必要验收尚未全部完成，不能标工程完成。

### 新版本全路线需求→代码→测试→证据→限制

本表为 A/B 实施后的当前工程投影，覆盖下方历史待批/缺项描述。独立外审仍 PENDING。最终固定 HEAD、测试数量、平台结果、完整差异及未执行清单在仓库外 `final-bounded-execution/delivery.json` 与其原始证据报告，不把预定执行算作通过。审计导航见 [ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V2.md](ROADMAP_CONTINUOUS_DELIVERY_AUDIT_V2.md)。

| 路线/要求 | 实际代码与调用入口 | 正向、负向、异常测试 | 阶段原始证据 | 范围和限制 |
|---|---|---|---|---|
| R1 源码、真实文件/PIT/时点与双组合 | source_dependencies、caller_inputs、data_readiness；canonical及real_runtime复用；原corrected两runner | test_r1_source_closure/data_readiness/snapshot_integration/caller_io_parity/r1_batch_caller_inputs；含F01–F04、NaT/available_at、零价、双源缺失、冷启动 | r1-stage、r1-final-affected、d1-shared-regression；原caller交付证据 | 源码checkout、V2 DAILY RAW支持矩阵；分钟、未知公司行动、真实数据不冒充完整支持；R1_FULLY_CLOSED=false |
| R2 A正式创建至单候选闭环 | objective_execution_binding V2 → 原设计/治理/冻结/物化 → StructuralEntry → 新颖性绑定 → SyntheticNoveltyTrialStart → CanonicalPredictiveExecutor → 原预算/Trial/裁决/registry/failure | test_objective_execution_binding、test_r2_formal_services、test_synthetic_novelty；正式创建不补字段/家族/回执；真实进程退出/同Trial恢复/完成重放 | objective-binding阶段、r2-formal阶段及 batch-snapshot-boundary；R2_FORMAL_SYNTHETIC_SERVICES | 实际合成最终BLOCKED合法；旧v1不迁移；真实Trial未授权；各门禁职责分开 |
| R3 B明确清单与合法有限运行 | synthetic_batch、synthetic_batch_delegation、synthetic_batch_worker；正式本机API与SyntheticBatchConsole；原CP/规划/失败知识保持 | test_synthetic_batch_contract/resources/recovery/races/web；两候选四动作、额度/清单/动作、到期撤销、跨根、重启/并发、实际OS资源不足；原Phase2 backend/盲化回归 | batch-core-final、batch-admission-review、batch-snapshot-boundary、batch-web-final、batch-ui-* | 仅已审/冻结/物化清单；并发1、重试0、NONE模型/调用/token/费用0；原CP预测禁令不变；真实批次未授权 |
| D1 同信号/退出的每日计划 | daily_plan → 共享corrected策略语义、原ledger；strategy_admission + SyntheticUsageService；workbench preview/publish | test_daily_plan/strategy_admission/synthetic_usage/engineering_workbench；空库、时点/现金/持仓变化、预算/T+1、资格到期撤销/历史丢失 | d1-archive、strategy-admission-final、synthetic-usage-expiry/history-green | 研究窗口收盘计划；合成用途资格不代表统计通过/真实使用资格；真实策略0 |
| D2 原engine模拟账户与对账 | PaperReplaySession → 原broker/ledger/费用/lot；advance、事件存储、冷重放 | test_paper_replay/engineering_workspace/synthetic_usage；同输入对照、部分成交原语义、拒单/重复/退出73/重启/输入变化 | d2-final-core、workbench-restart-final、synthetic-usage-ui-final/restart-server | SIMULATED_TIME；独立候选账户，原缩量FILLED语义不改；真实Paper/观察未开始 |
| M1 多策略组合与统一界面 | PortfolioPreviewPolicy、portfolio_plan → 源计划；资金/冲突/归属；EngineeringWorkbench + 新批次区域 | test_portfolio_plan/synthetic_usage；0/1/多个、现金竞争、同股重叠、买卖冲突、策略失效、归档；真实浏览器 | m1-preview-final、synthetic-usage-complete、batch-ui-* | 共享资金组合计划；不把独立Paper账户相加成组合资产；真实组合政策未批准，不做收益权重优化 |
| 运维与所有入口 | engineering_workspace inspect/serve、engineering_backup；原只读策略、本机确认、状态/停止、故障结算 | test_engineering_workspace/backup、原P3A/B/C及批次Web；不同cwd/两个根/备份/恢复/只读不写 | workbench-package-cli/server、workbench-backup-cli/restore-cli、batch-controller/recovery、最终认证原件 | 无startup recovery、真实后台服务或定时任务；源码部署，不宣称wheel；L1/L6继续OPEN |

320e980阶段Ubuntu R1通过，Windows新夹具在来源路径校验失败，原日志与附件保留；d104910在首次创建前规范临时根，实际别名路径本地通过。新的双平台结果未到达前保持合成整体验证PARTIAL，不能据此关闭既有Windows事件。全量collect中的未运行现场测试列出原因；此前22项依赖缺失历史合同的失败不能由读取真实目录补齐或记作PASS。

```text
REPOSITORY=a1351995160/trade
ACTUAL_MAIN_AT_START=e72fa6ae0ace0dbff6eeac87ae0e09082431d89a
CALLER_IO_COMPLETED_HEAD=4f780cf6454c36124f8a9477ca73098551d49f04
DELIVERY_START_HEAD=4f780cf6454c36124f8a9477ca73098551d49f04
REVIEW_BASE_MAIN=e72fa6ae0ace0dbff6eeac87ae0e09082431d89a
INTEGRATION_BRANCH=codex/roadmap-engineering-completion-v1
WORK_MODE=CONTINUOUS_ENGINEERING_WITH_CONSOLIDATED_AUDIT
EXTERNAL_CONSOLIDATED_AUDIT=PENDING
```

caller PR #8 是 OPEN/Draft/未合并；本地与远端 SHA 一致。交付清单中的12个源码/文档文件及19份日志 SHA256 全部逐项匹配。已查询当前 push/PR 检查，19项均 SUCCESS（含 Sonar）；不是沿用交付 JSON 内旧 PENDING。其工程范围只认证 prepare/invoke/result，完整 execute 不在旧验收内。完整 ancestry 保留，最终差异含未合并 caller。

独立工程根 `E:/llmwiki/roadmap-engineering-completion-v1`；原研究根只检查过 Git 元数据/文件状态及不存在的 AGENTS.md，未打开研究文件内容。根 AGENTS.md/DEVAGENT.md 未检出，遵循用户提供的 AGENTS 及实际 CLAUDE.md。工程原始证据写 `E:/llmwiki/roadmap-engineering-evidence`；现场合成数据使用临时独立根。

## 启动时的剩余需求矩阵（历史快照）

| 路线 | 已有实现/代码位置 | 必要工程缺项 | 依赖与验收 | 尚未授权/真实条件 |
|---|---|---|---|---|
| R1 | source_dependencies、data_readiness、caller_inputs、predictive_executor；真实 reader/runner/engine | 部署与实际闭环需要的输入输出缺口；real_runtime 尚未适配；数据支持范围逐项核验 | 既有 R1 209项本地及双平台；新改动另验，不泛化旧结果 | 真实候选、数据/因子来源未核验；分钟/事件、公司行动、基准按支持矩阵保留限制 |
| R2 | predictive_authorization、predictive_trial_start、predictive_executor、budget、trial_adapter、最终裁决/registry/failure | 从合法治理、Structural 到 start/engine/结算/裁决的完整合成组合，进程中断恢复；硬编码 gate 须以实际证据验收 | R1；真实服务正负向、真实进程边界、预算和副作用对账 | 真实 Trial 未授权；需要治理语义变更时单列具体批准 |
| R3 | autonomous_control_plane、autonomous_orchestrator_v2、backend、预算及失败知识 | 有界合法循环及等待/停止；批次范围请求/只读校验；入口集成 | R2；不得解除 CP 预测禁令或新增批准权 | 批次自动授权模型 WAITING_POLICY_APPROVAL |
| D1 | strategy_adapter、research/selector、engine/portfolio_exit、Web console | 同冻结策略语义的实际计划服务/API/UI、时间/现金/持仓身份验证 | 合成准入与相同 runner 输入对照；空库正确 | WAITING_QUALIFIED_STRATEGIES；真实数据与使用资格未授权 |
| D2 | 已部署 engine/broker/ledger；当前源码清单未发现旧 Paper host | 实际模拟账户、计划成交、持久恢复/对账和界面；不扫描原研究目录取旧组件 | D1；部分成交/T+1/lot/成本、重复与重启、回测对照 | 真实 Paper 未授权；REAL_OBSERVATION_TIME 不以回放替代 |
| M1 | strategy registry、现有 console | 冻结组合政策接口、资金冲突/归属/版本失效、统一界面 | D1/D2；0/1/多个合成合格策略全路径 | WAITING_QUALIFIED_STRATEGIES / WAITING_POLICY_APPROVAL |

当前以上缺项均未标完成。阶段按顺序实现、验证、追加 progress 并小提交；单个受限路径暂停时推进其他合法工程。最终固定 HEAD 后运行整体回归、双平台认证，形成差异、日志、部署说明及审计 ZIP。尚有必要工程缺项时只能报告 PARTIAL_WITH_BLOCKERS。

## 集中真实授权待办

| 类别 | 未来必须明确的范围 | 本次状态 |
|---|---|---|
| A 只读数据核验 | 候选/合同/政策/因子身份；绝对数据文件、字段、日期与预热；只读操作、独立证据根、资源上限；封存/未知来源即停 | NOT_AUTHORIZED / NOT_VERIFIED |
| B 单次 Trial | A已就绪；一个候选及Trial身份、人工确认、预算、精确窗口、输出、停止条件；不含批次执行 | NOT_AUTHORIZED |
| C 批次运行 | 动作集合、数量/token/费用/资源、有效期/撤销、数据版本、逐项人工门禁与停止；权限语义单独批准 | WAITING_POLICY_APPROVAL |
| D Paper | 候选/策略资格、数据窗口及封存交集决定、模拟账户输出、启动/停止；不含broker | NOT_AUTHORIZED / OBSERVATION_NOT_STARTED |
| E 组合使用 | 合格策略版本、明确组合政策和资金/冲突规则、数据与用途；不含真实下单 | WAITING_QUALIFIED_STRATEGIES / WAITING_POLICY_APPROVAL |

真实研究/绩效/账户内容不读取；真实业务模型调用、行情下载、broker、daemon、schedule和部署均不执行。以上待办不是可供运行的授权文件。

## 保留事件与限制

- WINDOWS_L1 / WINDOWS_L6 = OPEN_ROOT_CAUSE_UNCONFIRMED，保留原诊断；不扩 timeout、skip、隔离白名单。
- F01–F04、available_at、P3-A/B/C 必须保留回归。
- HISTORICAL_PROVENANCE=UNVERIFIED；REAL_CANDIDATE_DATA_READINESS=NOT_VERIFIED；READY_FOR_REAL_TRIAL=false；R1_FULLY_CLOSED=false。
- MAIN_MERGED=false；AUTO_MERGE=false。合成计数与真实计数分开，工程完成与业务验收分开。

## R1 内部阶段：批次输入输出适配

RealFactoryRuntimeV1 已在正式 run 中逐候选复用 canonical caller 的 prepare/invoke/result，使用冻结日历和原始 available_at；BASE/10K 保持独立 engine。数据/PIT gate 来自实际准备诊断，基准仍 UNKNOWN。正常及恢复裁决引用实际 provisional 文件，不再用 BASE_RESEARCH 猜测路径。准备失败释放本批次仍活动的预性能预留，未改变消费定义。

批次旧合同的 factor_registry 使用 path/sha256，caller 旧合同使用 hash，两者按原格式分别验证。文件格式必须绑定研究根内原文件的字节 SHA256；同时声明内容 hash 时也必须一致。不改写旧身份、不为旧格式补字段。缺时点、缺列和缺日历拒绝，GAP_SIZE 缺缓存不派生。支持范围仍是 V2/DAILY/RAW/单缓存因子/显式完整正常双源 PIT；V1、事件、分钟、多因子等未认证。

测试：新 venv Windows Python3.13.5、原完整哈希锁及 pip check；隔离先验66 passed。第一轮 R1 全矩阵213 passed/251.15s；随后补文件身份，最终受影响矩阵74 passed/48.13s，包括原 caller63、新批次5及政策6。不是两次测试相加计数。新增批次正向通过真实 reader/runner 和两个独立 engine，负向拒绝空时间/缺列/缺日历/冻结文件字节变更；输入与治理文件内容不变。

扩展批次相关回归39 passed/1 failed：旧 policy-pin 测试从 cwd 读取未交付政策，是 fixture 缺口而非业务 red。改为临时生成默认政策/锁后6 passed，未读原研究文件、未扩大 skip。compile及收集成功，1094 collected（新增第五项前的收集，非passed）；原 legacy module skip保留。原始日志/XML见仓库外证据目录。完整 RealFactoryRuntime.run、正式恢复和 R2 全链仍 NOT_VERIFIED；不能把本节子路径验收算作完整服务通过。

## R2 必需的具体批准请求：新 Objective 执行绑定

当前状态：IMPLEMENTING；2026-09-10 已单独获 A 限定实施批准。以下为原缺口及批准范围记录，最新实现见 [OBJECTIVE_EXECUTION_BINDING_V2.md](OBJECTIVE_EXECUTION_BINDING_V2.md)，完整 R2 尚未验收。

现状证据：在全新临时根，通过实际 ResearchProposalGovernanceServiceV1.review/confirm 创建 Objective、预算、家族、lineage 和真实服务回执，生成的 Objective 缺少 batch_id、policy_identity、research_period_identity、factor_event_registry_identities 四字段。原始结果为仓库外 r2-objective-binding-gap.log，合成根 r2-objective-service-jaidcwv2。research_evolution_ai_design.py 的完整执行语义审批要求这四项逐项一致，因此生成目标无法直接进入已批准的完整语义设计路径。旧 P3-C/R1 fixture 在首次设计前手工初始化这些字段，并手工创建初始目标/演进状态，不能冒充“正式 Objective 创建 → 全闭环”的认证。

此外，R2 探索测试已用真实 Provider、规范化 PIT 服务、实际分片及下界计算取得 Structural PASS（lower64/upper65/minimum30），之后通过真实人工预测授权；旧 fixture 缺 canonical Multiple Testing Family，正式 start 正确拒绝。不能手写 immutable family 或 success receipt 绕过。探索计数、失败和每个临时根均保留；尚未执行预测/PerformanceAccess。

必要变更（仅请求以下范围）：

1. 在 ResearchProposalGovernanceServiceV1 的新版本 Objective 创建 preview/confirm 中接收明确 execution_binding：冻结 policy_id/version/hash、research_period_identity（id/start/end）、factor_event_registry_identities。为未来实际调用保留同等校验，本次仅 synthetic 测试。
2. 将该绑定内容纳入新版本 preview_hash / objective_identity_hash；确认时重新核对政策锁、窗口与 registry 身份。成功创建时派生当前新 objective_id、batch_id，并写入设计服务已要求的四个字段。复用现有 Objective、SearchBudget、Multiple Testing Family 和 lineage 创建事务。
3. 不改变现有预算额度/扣账、统计阈值/家族定义、Final Test、人工确认/冻结步骤，不增加批次自动批准权，不解除 CP 预测禁令。

影响：新增目标的身份计算包含明确执行绑定，因此属于身份/冻结边界扩展，不以普通字段适配掩盖。原 v1 preview、旧目标和既有 receipt 保持原语义；旧目标不得静默补绑或迁移。无 execution_binding 的旧流程继续兼容并保持不能进入完整新语义路径的限制。

兼容/回滚：新增版本明确区分；本次仅在新 synthetic 根验证。单独提交，可 git revert 回到1d461de之后的明确父提交；已生成新版本工件保留且旧代码拒绝继续写，不删除或改写历史。

验证：实际 review→preview→confirmed creation→design→approve→freeze→materialize→Structural→authorize→start；拒绝缺/错/更改绑定、preview陈旧、确认重放身份冲突；原v1/Phase1/P3-A/B/C回归，确认预算及CP禁令不变。

最小批准范围只包含上述新目标创建协议扩展及前后端传递、对应测试。R3批次自动权限、真实数据、真实Trial/Paper/broker均不在批准范围。等待期间暂停此完整R2路径，继续不依赖新治理协议的工程。

## R2 内部自检：结构结果跨服务边界

实际 provider 的零 Prospective 安全计数在原 PerformanceBlindGuard 已允许的两个精确路径内，但持久化层未使用同一合同，造成真实 Structural 结果被拒绝。持久化层现在复用原路径，并拒绝非零 Prospective；未新增隔离白名单。针对性 red 为1 failed/3 passed，修复后4 passed；相关回归88 passed，另2项旧现场测试依赖未交付真实 Objective/报告，失败原样保留（r2-structural-regression.log），不读取真实文件补齐、不增 skip。network/process/protected 探针均0。

五轮独立合成探索保留原日志与根路径，分别定位合约状态、执行约束、PIT资料和下界证明缺口；最终真实 Provider PASS 下界64/上界65/最低30，预测授权由真实服务生成。正式 start 因缺 canonical family 拒绝，预测/engine/PerformanceAccess均0。因此本阶段只关闭结构结果持久化适配，R2 E2E仍未通过。两项现场测试不属于已通过证据；最终全量测试须明确保留此运行条件限制。

## R3 内部阶段：范围申请与有界停止报告

实现 BatchScopeRequestServiceV1 及治理页表单，GET /api/research-console/{objective_id}/batch-scope-request 提供当前安全上下文和只读校验（scope为JSON查询参数，最多8192字符）。绑定Objective、context/data manifest、数据范围/版本、动作、候选/Trial/批次数、模型/token/费用/时间/内存和到期/撤回声明，核对已有预算；固定八类停止条件。只产生申请检查结果，execution_authorized始终false，不生成canonical authorization/receipt，不扣账。撤回仅为该申请的声明，不声称撤销已存在授权。未来真实授权来源和强制执行资源限额仍须另批/集成。

控制平面复用原loop/tick/对账，修复达到max_ticks后错误报告stopped=false：返回stopped=true及MAX_TICKS_REACHED。执行顺序、门禁、预算和锁不改。真实合成Proposal后一步停止，再次进入即等待人工Freeze，无重复生成。

验证：请求首轮17项；原控制平面/权限/隔离合并105项；新增真实一步停止red后最终106 passed/19.86s，探针network/process/protected=0。前端原8项及vue-tsc/Vite构建通过，原大chunk警告保留不调阈值。全新临时根运行只读本地页面，浏览器经实际API验证通过、修改后旧结果消失、撤回拒绝，截图在本任务工具记录；页面和临时服务已结束。旧现场fixture没有orchestrator运行文件，页面准确报读取错误；该演示不认证运行控制全链。服务日志r3-ui-server.log/root.txt与最终测试日志/XML位于外部证据根。

R3仍PARTIAL：已有合法循环及本节申请入口已验；批次自动权限WAITING_POLICY_APPROVAL，不把申请有效当执行授权。完整backend错误矩阵、统一监控/运行控制及D1/D2/M1仍需继续工程，不宣称路线完成。

## D1 内部阶段：共享每日语义与不可覆盖预览

将正式corrected runner的执行支持校验和当日输入提取为共享入口，原runner继续使用同一实现及原日缓存。daily_plan.preview_daily_plan复用编译器、PIT输入和PortfolioExitEvaluatorV1，接收正式caller准备的输入及真实PortfolioLedger，生成买入预览、持有、退出、NO_TRADE/NOT_READY。只操作账户副本，使用既有sizer/lot/fee/slippage估算；价格基于T收盘，实际成交须重验。缺当日PIT/因子、NaT、未来因子、非法计划时间和不一致账户拒绝或NOT_READY。

预览绑定合同/政策/代码/输入/账户/时点身份，DailyPlanArchiveV1首次独占写入、重复校验、损坏拒绝；现金变化生成新版本并将旧版本对比为STALE，不覆盖历史。内容身份不是授权。当前execution_ready=false、usage_qualification=NOT_VERIFIED；现有registry PROMISING或普通登记不能变成可采用计划。范围仍DAILY/RAW、T_CLOSE、冻结研究窗口内，非“今天实时计划”。

测试：首轮新预览与批次14 passed；共享入口最终R1快照/caller/批次/每日共160 passed/136.26s；新增归档后每日12 passed/24.42s，探针全部0。信号对照真实runner，真实ledger Fill产生持仓/T+1并对照同一退出评估器；账户不变、现金约束、时间/PIT负向、归档不可覆盖与损坏检查。证据d1-preview-first.log、d1-shared-regression.log/XML、d1-archive.log。

D1仍PARTIAL：本节为实际计算内核与归档；正式策略准入、发布服务/API/UI及与Paper/组合的完整衔接尚未完成，不能登记缺项后称D1工程通过。下一步继续可独立的Paper回放/对账，再完成统一入口和准入整合；真实使用资格与运行仍未授权。

## D2 内部阶段：真实引擎逐事件回放、恢复与对账

引擎原事件处理和收尾提取为共享方法；corrected runner装配函数同时提供同一真实engine/strategy/exit回调。PaperReplaySessionV1在明确独立输入/输出根内逐步处理这些事件，使用既有broker/ledger/order/fee/fill，不另造账务模型。累计event_count请求可重复；每事件独占落盘并fsync，包含顺序/链哈希/账务摘要/计划。重新构造时用同一合同/输入/源码重放并逐项对账；改输入、缺事件、损坏摘要拒绝，落盘失败后内存会话标RECOVERY_REQUIRED，不能丢失事件后继续。read_paper_archive只读取核对持久历史，不启动引擎。

真实进程在9事件落盘后os._exit(73)，新进程恢复到完成，原始stdout/stderr保存process/paper-replay；中断前实际隔离计数为0/0/0，恢复亦保持隔离。完整回放现金、费用、逐笔交易、lots、orders和event_hash与独立完整runner一致；真实SELL部分成交与涨停拒单通过。SIMULATED_TIME与real_observation_days=0固定分开，未启动真实Paper。

验证记录：首次引擎扩展13 passed/12 failed，全部为旧fixture尝试Git源码探测的进程拒绝，原日志d2-engine-extraction.log保留，不算业务red。七处构造所在六个测试文件显式UNKNOWN身份/禁清单落盘后26 passed（含第一版冷恢复）。后来完整相关184项中183 passed/1 failed；失败为测试误假定BUY会保留余量。现有BUY合约缩量后FILLED，未改其语义；换为真实SELL部分成交fixture后28 passed。最终增加只读归档摘要的受影响40项通过，原失败记录均保留。全量R1快照/caller在上述183项内通过，最终固定HEAD仍需再认证。

D2仍PARTIAL：目前是实际回放/恢复核心，尚无完整公开操作界面和策略使用准入；BUY保留余量重试NOT_SUPPORTED（既有执行合同），真实市场偏差和逐日观察NOT_VERIFIED。不得把本节视为正式Paper验收关闭。

## 中间 CI 修复记录：只读路由清单

54838ac的Phase1/Phase2 CI失败均定位到本轮新增GET后遗漏更新的固定路由数（实际47，旧断言46）。同步精确数量，并明确断言batch-scope-request属于GET；POST仍25，不改权限/隔离。远端失败日志ci-phase1/phase2-54838ac-failure.log保留。相关本地整文件42 passed/2 failed，两项为原CI已排除且依赖真实现场的测试，未补数据/新增skip；精确受影响路由与新请求套件最终19 passed（r3-route-catalog-final.log）。最终平台认证仍以最终HEAD为准。

## M1 内部阶段：共享账本的组合研究预览

PortfolioPreviewPolicyV1要求显式候选/冻结合同、现金比例、优先级、重叠规则、退出冲突、仓位/同股敞口/买入换手上限与有效期；无真实政策默认值或权重优化。不可变模型及内容身份随政策变化更新，完整政策随计划返回，不是治理批准。preview_portfolio_plan从真实ledger投影各策略资金/持仓，逐个调用D1同语义服务，再用共享剩余现金/容量进行裁剪。卖出保留candidate/lot/source_plan_id，未成交卖出款不用于买入；先汇总退出再处理买入，避免顺序漏掉其他策略退出。

0/1/两个独立冻结候选、优先级同股排斥、费用/现金、容量/换手、真实lot退出与T+1、版本/过期剔除、成员缺失及异常账户均有测试。成员不完整时保留未解决持仓提示并禁止新增买入。敞口基于传入账本价格快照，买入基于T_CLOSE估价；不是实盘风险计算或收益优化。测试首次7 passed；最终M1 11+D1 12+D2 8共31 passed/79.22s，隔离三探针0，日志m1-preview-first.log、m1-preview-final.log/XML。

M1仍PARTIAL：这里只认证两个合法冻结但**未取得使用资格**的合成候选预览，不冒充两个合格可用策略。正式策略准入读取/撤销衔接、公开操作服务和统一界面仍待实施；真实组合政策与策略资格保持等待。R2核心协议批准仍未收到，暂停相应路径并继续其他工程。

## D1/D2/M1 入口阶段：实际合成工作台

控制台新增`/research/workbench`，后端`/api/research-engineering/workbench`只读查看/计算预览，以及显式publish/advance。应用构造必须显式注入EngineeringWorkbenchV1并匹配研究根；默认未配置，不能从页面指定磁盘路径或回退真实根。GET不恢复Paper，不写归档；操作沿用现有GOVERNED/SYNTHETIC执行策略和本机请求检查，领域层仍要求confirmed=true及当前context_hash，并复用现有资源锁后二次核对。内容hash单独不提供权限；不会调用Trial、CP预测或修改既有治理回执。公开操作仅为已显式装配的工程合成输入，不能称为真实Paper或策略使用授权。

页面能计算同股冲突组合预览、归档单策略与组合计划、按累计事件数推进实际引擎、查看现金/费用/逐笔成交/lots/orders及证据，改变选择后原确认失效。各候选Paper账户独立，不能相加为组合资产。正式合格策略数显示0：本入口刻意只装配冻结工程候选，不从PROMISING推断使用资格。

验证：API第一轮7 passed；工作台/原启动隔离/组合/每日/Paper联合106 passed/107.35s，原始workbench-final.log/XML，隔离三探针0。前端原测试8 passed、最终build成功；既有大chunk警告和Starlette弃用警告保留。浏览器真实操作：组合预览和归档成功；最终新根9/44事件有2笔实际合成成交，44/44后6笔，现金333175.17、费用830.83；重载不续跑，切换另一候选NO_SESSION，资格与真实观察天数均0。截图在本次工具会话，HTTP原始日志workbench-ui-server.log及workbench-ui-final-server.log、根指针文件在外部证据目录。服务手动停止后端口8857无监听；UI进程被终止，未把未导出的退出计数宣称为0。

仍未完成：正式registry使用准入/撤销到计划的衔接、合格策略测试合同正向、生产公开装配/跨进程重建工作台、组合账户执行、发布计划到执行入口的版本准入校验、运行维护集中交付。已有Paper核心冷恢复测试不能替代这些入口缺项，全路线维持PARTIAL_WITH_BLOCKERS。

### 当前可复现的合成入口

仅在独立工程checkout和已按原锁安装的venv内运行；root必须是新建且不存在的绝对路径。脚本自行通过既有fixture生成两个冻结候选、实际文件与输入，不要求手改JSON，不代表完整R2创建流程认证。

```powershell
$env:PYTHONPATH='tests/isolation;tests/research_factory;src'
$env:CHANLUN_TEST_ISOLATION='1'
$env:CHANLUN_PROTECTED_ROOT='E:\llmwiki\chanlun-trading-system'
$env:PYTHONDONTWRITEBYTECODE='1'
$demoRoot = Join-Path $env:TEMP ('workbench-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe tests/research_factory/workbench_demo.py --root $demoRoot --port 8857 --governed
```

先在frontend执行`npm ci --ignore-scripts`及`npm run build`；打开`http://127.0.0.1:8857/research/workbench`。不加`--governed`则只读；即使加了仍须页面确认当前上下文才能归档/推进。Ctrl+C停止，不创建定时任务。旧根保留证据，当前demo不支持用旧root启动，不能手动删除归档以免费重跑。

### 后续补齐：新进程工作台装配

新增engineering_workspace的显式save/load及demo的`--resume`。新建演示会独占写入`workbench.json`，只记录相对输入/输出路径、正式冻结合同引用与身份、完整组合政策、输入身份及初始计划账户；不保存执行批准。load重新调用正式caller核对原registry合同/政策/PIT/缓存，不使用上个进程的Python对象；输入变更、配置损坏、路径越界及非有限现金拒绝。load不启动或恢复引擎，只有之后真实确认的公共API动作才续跑。

因此上一节“demo不支持旧root启动”仅属当时阶段状态，现可在同一明确根执行：

```powershell
.\.venv\Scripts\python.exe tests/research_factory/workbench_demo.py --root $demoRoot --port 8857 --resume --governed
```

环境隔离设置与首次命令相同；不加governed仍只读。仅支持保存时的初始计划账户，不能把真实账户或任意持仓状态塞进配置；Paper持仓仍来自原事件序列。已有无workbench.json的早期浏览器临时根不自动迁移。

首轮4 passed；最终配置5+工作台7共12 passed/31.11s，workbench-restart-first/final.log及XML。独立子进程仅凭配置，经公共Web API从9事件继续完成；原输入字节不变、真实观察0。原始进程stdout/stderr见process/workbench-restart，父子隔离探针均0。完整正式准入及R2批准待办仍未关闭。

### 合成备份与新目录恢复

engineering_backup只备份workbench.json及其声明的输入/模拟输出树，在现有工作台资源锁内核对；不枚举父目录中的其他项目。ZIP包含逐文件SHA256与清单身份，要求显式解压字节上限，清单本身也计入上限。只写不存在的新归档/恢复根，不覆盖历史；恢复验证重复/越界路径、清单配置入口、全部内容hash后才写新目录，再用正式caller验证。恢复完成只重建输入，不产生事件或运行授权。异常时保留已写证据，不能清空原根重试。

保持同一隔离环境，先停止演示Web再执行明确路径：

```powershell
.\.venv\Scripts\python.exe -m chanlun_trader.research_factory.engineering_backup backup --config "$demoRoot/workbench.json" --archive "$env:TEMP/workbench-backup.zip" --max-bytes 20000000
.\.venv\Scripts\python.exe -m chanlun_trader.research_factory.engineering_backup restore --archive "$env:TEMP/workbench-backup.zip" --destination "$env:TEMP/workbench-restored-new" --max-bytes 20000000
```

示例20MB是明确合成工件I/O上限，不是研究预算；换路径时仍须全新输出。只在合成根验证，不授权备份真实研究数据。恢复后若需页面，使用新根和demo的resume；是否governed仍在启动时显式选择，页面操作继续逐次确认。

首轮3 passed；与装配/工作台联合15 passed/34.31s；最后补清单字节上限后受影响3 passed。原始workbench-backup-first/final/limit.log和XML保留。实际CLI现场生成两个合成候选，备份90个文件、恢复成功且未执行，备份ZIP及setup/backup/restore原始日志在证据目录；三进程隔离探针0。测试恢复后的Paper从9续至10，原根仍9；内容损坏、清单入口绝对路径、ZIP越界、重复恢复/超限均拒绝。以上不关闭真实备份/恢复或历史来源核验。

## 后续源码修复：微观执行真实性不能写常量通过

冻结ValidationDecisionPolicyV2要求核验实际可得时点、合法入场/成交/退出；canonical原gate直接True，batch原gate将非事件策略标NOT_REQUIRED。新增execution_evidence.audit_microstructure，使用真实EngineResult的signals/orders/trades/lots/calendar、caller因子时点和既有ChinaPriceLimitModel核对：缺时点、未来因子、早于eligible、非开盘事件、同日信号成交、停牌/涨跌停、T+1/归属/超量和账本不变量。两个正式调用点使用实际证据，默认DAILY/RAW以外不宣称已认证。未修改成交模型、政策阈值或预算扣账。

red是将原常量判断提取为helper后，以真实runner结果副本注入5类坏证据，5 failed/1 passed；不是完整canonical execute的red。替换核验后6 passed，后加非开盘时点，最终微观7+batch5+每日12+Paper8+工作台7共39 passed/86.08s，execution-evidence-red/green/final.log/XML，隔离探针0。真实正常成交通过，异常证据不通过；原失败日志保留。

仍未验证完整canonical/RealFactoryRuntime.run，完整R2协议阻塞不因此关闭；candidate_similarity_control与search_budget_reservation的最终证据仍需继续核验，不能以本项代表所有hard gates已完成。

### 预算预登记真实证据

search_budget_reservation现读取Trial最早的REGISTERED_BEFORE_PERFORMANCE事件，核对原事件hash、候选/Objective/预留身份与实际预算状态；正常执行要求ACTIVE及原Objective/batch/family占用，恢复要求原预留CONSUMED。批次把已有预算身份字段传入事前登记；未访问性能时核验失败释放当前仍活动预留，不进入原性能失败消费分支。没有扩预算、改扣账规则、补历史事件或生成批准回执。消费后预算v1仅保留预留ID和状态，完整恢复身份仍依赖原canonical恢复门禁，本helper不宣称重建已删除的预算引用。

新增组件首轮6 passed；扩大回归38 passed/22 failed，22项全部在旧test_predictive_trial_start_v1夹具读取未交付AI_HANDOFF_V2_e1ddf合同处FileNotFoundError，尚未进入执行，不从真实根补读。原始budget-registration-final.log/XML保留；可独立关联回归37 passed/27.87s，随后补错batch负向及引用核对后预算7+batch5共12 passed/10.43s，budget-registration-affected与binding日志/XML。隔离三探针0；CI未增加任何skip或排除。完整R2仍未通过，历史夹具依赖列入集中待办。

### 批次新颖性证据接入

批次最终candidate_similarity_control直接引用该候选在性能访问前实际CandidateNoveltyGateV2生成的decision、passed、比较集hash、邻近信息和政策版本。未改变新颖性算法、比较集或冻结规则。批次/既有新颖性与有限编排回归36 passed/20.73s，batch-novelty-evidence.log/XML，隔离三探针0。canonical单候选目前仍缺可追溯事前比较证据，其旧常量gate未被本项认证，属于完整R2必要缺项。

### 策略使用资格的具体批准范围

现有ResearchStrategyRegistryFacadeV1记录研究状态和固定DISABLED的promotion_state，未发现可用于计划/Paper的资格批准、有效期、撤销合同。不能把PROMISING、RESEARCH_PASSED或PROSPECTIVE_SUPPORTED直接解释为使用授权；也不能只签一个synthetic内容hash即放行。

已请求批准仅隔离synthetic域的版本化测试使用资格服务：请求→实际人工确认→撤销，绑定候选、冻结合同、数据身份、测试用途、有效期，供计划/组合准入核验。新增合同不迁移旧记录，不修改Trial/预算/CP/真实资格，兼容旧入口继续无资格；回滚新增服务与调用后回到只读/未就绪。拟验证实际服务生成正向0/1/多个记录、缺/错确认、失效/撤销、跨根和版本变化拒绝，真实资格计数始终0。因新增人工门禁，依据本次附件4.4等待具体批准；不是自动认可的运行权。等待期间只读registry状态衔接继续实施。

2026-09-10用户明确回复“批准上述仅合成测试资格服务”。上述精确synthetic测试资格范围现为APPROVED_TO_IMPLEMENT；Objective执行绑定的另一项请求仍等待答复，两者不能混用。

### 策略库只读事实衔接

工作台按既有canonical预算绑定定位源工作区的实际策略registry，读取研究状态、版本/证据失效和原文件SHA256；不写registry、不把研究状态映射成使用授权。退役、INVALIDATED、失效证据或冻结版本冲突会剔除工程预览来源并拒绝后续回放；registry变化改变上下文与组合计划身份。没有登记的冻结输入仍可做已授权工程预览，使用资格仍为false，界面明确给出原因。

首轮3 passed/1 failed是测试错误请求DRAFT直接RETIRED，原服务正确拒绝；改走既有VALIDATION_BLOCKED→RETIRED，未改状态机。最终只读准入/工作台/冷重建/备份19 passed/47.49s，strategy-admission-first/final.log/XML，隔离三探针0。前端8 passed/build成功（原chunk警告保留）；最终资格功能集成后再做浏览器交互验收。本节仍不代表正向使用资格完成。

## 已批准的合成测试资格、计划与回放衔接

用户上述明确批准已落实为SyntheticUsageServiceV1与公共`/api/research-engineering/workbench/usage/{action}`（request/confirm/revoke）。每一步使用实际GOVERNED/SYNTHETIC策略检查、当前上下文、显式人工确认及既有工作台锁。请求本身不授权；确认必须读取该服务实际保存的请求，复核原冻结合同、真实caller输入、策略注册事实和有效期。普通payload中的permission或内容hash不能跳过这些步骤。未改CP、Trial、预算、真实registry或原promotion_state。

测试资格只表示该冻结输入已通过输入核验，并由显式测试操作方允许指定DAILY_PLAN/PAPER_REPLAY用途，不代表研究统计通过或真实策略可用。请求/确认/撤销各自独占保存，具有来源关系、时间与证据hash；候选/合同/输入、输入输出绝对根、registry身份、用途和有效期绑定。到期、撤销、改根/改版/成员移除不继续放行；复制备份到新根后必须重新生成和确认新根测试资格，历史不迁移、不覆盖。

工作台尚无资格请求时保留原“冻结输入工程预览”模式；一旦建立资格请求，就持续按资格过滤，不会在资格全部失效后回退原模式。组合源计划继续使用原D1语义，组合封套绑定实际测试资格；真实execution_ready和真实资格仍为false/0。发布前重新读取核对输入文件，回放前再检查PAPER_REPLAY用途与有效期；执行请求和实际结果保存到usage-actions并引用资格ID，底层Paper账本、成交、真实观察天数定义不变。单次调用仍是显式、有界的事件推进，不创建常驻任务。

界面操作：选择测试候选与有效期→勾选当前上下文确认→生成请求→核对后再次勾选并确认测试资格。随后计算/归档组合或推进事件；撤销按钮同样需要新的显式确认。真实合格策略数始终0，合成测试资格另列数量。无有效回放用途时按钮禁用，后端亦拒绝直接调用。

验证证据（不可相加）：首轮8 passed；扩展99 passed；加入成员移除/发布输入复核后受影响20 passed；加入执行证据13 passed；时间记录和全部关联最终101 passed/95.79s（synthetic-usage-complete.log/XML）；校验期间到期保护最后受影响21 passed/57.52s（synthetic-usage-expiry.log/XML）。原隔离测试保留，最终关联测试中合法synthetic_template_calls=5、治理请求34/确认37，禁止预测/结构/AI探针0；进程网络/子进程违规/受保护访问均0。新增资格动作本身以实际request/confirmation/revocation及usage-actions文件计数，不冒充这些旧探针已覆盖新服务计数。

前端最终8 passed/build成功，synthetic-usage-frontend-final-test/build.log，原chunk警告保留。浏览器第一轮实际生成并确认两个资格，组合只给同股一个资金分配，另一候选NO_TRADE；回放9事件2笔成交，撤销后直接继续请求409且仍9事件。最终新根含时间记录和执行证据：实际确认→9事件→停止服务→新进程resume读取配置、保留资格及9事件→显式推进10→撤销，资格0且勾选确认也不能续跑。原始synthetic-usage-ui-server/final-server/restart-server.log和两个根指针保留；最终根有1请求、1确认、1撤销、2组执行请求/结果。截图在工具会话；Web进程手动停止未导出退出探针，不把此计数宣称0，8857已无监听。早期无时间字段的开发临时根保留，不自动迁移。

本节关闭“合成测试资格正向0/1/多个及撤销入口”工程缺口，不关闭真实使用资格、完整R2、完整批次研究、真实Paper观察或真实组合政策。底层各Paper账户仍独立，组合交付范围是共享现金的组合计划，不能把这些账户相加为组合实盘资产。

### 自检修复：资格历史丢失不能退出准入模式

结构化自检在f2541c0发现：configured仅由当前请求数推导，移走整个资格历史目录会回退无资格工程回放。使用实际请求/确认后将历史移入同一合成根的保留目录，原实现确实未拒绝第1事件，red 1 failed（synthetic-usage-history-red.log），不是mock结果。现首次实际请求同时独占保存synthetic-usage-mode.json作为持续收紧标记；资格历史丢失后仍要求资格，没有可用记录即阻断，不修写或补造历史。标记只收紧模式，不授予权限，随限定输出树备份。

修复后资格15+备份3共18 passed/50.68s，synthetic-usage-history-green.log/XML，隔离三探针0。旧开发根没有该新标记时不自动迁移；正式复现从新根启动。此修复属于已批准资格语义的实现修正。结构化自检为主任务顺序执行，不是独立审计已完成；完整R2执行绑定与事前相似性证据缺项仍保留。

### 正式源码启动入口

工作台现可从源码内模块启动，不依赖演示脚本或其夹具工厂。先按原哈希锁安装依赖、构建 frontend，使用实际保存的绝对 workbench.json 路径；设置 PYTHONPATH 为源码绝对路径与 tests/isolation 绝对路径，CHANLUN_TEST_ISOLATION=1、CHANLUN_PROTECTED_ROOT 为受保护原根。执行：

```powershell
python -m chanlun_trader.research_factory.engineering_workspace inspect --config <绝对配置路径>
python -m chanlun_trader.research_factory.engineering_workspace serve --config <绝对配置路径> --port 8857
```

serve 固定绑定127.0.0.1，默认READ_ONLY，打开 /research/workbench；仅在隔离合成域显式操作时加 --governed，仍须逐次实际确认与资格验证。Ctrl+C停止，不安装后台任务。此为源码检出部署入口，不宣称独立wheel包含scripts或前端资源。合成输入首次生成仍可使用既有 workbench_demo.py，其后恢复和服务不调用夹具工厂。

不同cwd真实子进程inspect只读验证纳入冷重建套件，6 passed/19.91s，workbench-package-cli.log/XML，原始stdout/stderr在process/workbench-package-cli，隔离探针0。源码serve实际浏览器GET显示既有10/44事件、2成交、真实资格0与观察天数0，确认/归档/推进均禁用，未自动恢复；workbench-package-server.log保留。服务已Ctrl+C停止并核对8857无监听，不把手动停止服务的未导出探针宣称0。

## 2026-09-10 新批准：仅 synthetic 事前新颖性比较集绑定

用户明确批准来源范围解析/快照/人工测试确认/性能前复核及原 CandidateNoveltyGateV2；不批准 Objective execution_binding。实现与需求→代码→测试→证据→限制见 [SYNTHETIC_NOVELTY_BINDING_V1.md](SYNTHETIC_NOVELTY_BINDING_V1.md)。组件 24 项真实服务测试通过，新版本 canonical 准入已接入；完整 R2 及恢复仍 BLOCKED/NOT_VERIFIED，CP 预测禁令、真实数据和运行边界不变。最终固定 HEAD 证据单列 final-novelty，不覆盖 d821d0a 的 final 包；L1/L6 继续 OPEN。
