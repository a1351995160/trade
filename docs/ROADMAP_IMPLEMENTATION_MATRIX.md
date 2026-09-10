# 剩余路线连续工程交付

本文件是工程进度与证据索引，不是运行授权或 canonical 状态。用户于 2026-09-10 批准 R1/R2/R3/D1/D2/M1 连续工程和隔离完整合成服务验收；旧阶段文档的小包停止点保留为历史，不再作为本次工程停止点。

## 接续基线

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

## 精简剩余需求矩阵

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

状态：WAITING_POLICY_APPROVAL；不是已经实施的治理变化。

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
