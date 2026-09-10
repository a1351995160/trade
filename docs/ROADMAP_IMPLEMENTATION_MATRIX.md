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
