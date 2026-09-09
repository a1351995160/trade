# Execution semantic pitfalls

- Shadow EOD runtime 必须使用 `data/shadow_scan/<trade_date>/`，不能把正式 Prospective Reader 当作 fallback；迟到或回填源必须标记为 `LATE_RECONSTRUCTED`，不得进入同日 shadow。
- 固定持有期必须使用冻结交易 session index，不能用自然日加法。
- BASE 与 SMALL_CAPITAL_10K 是两个独立组合；10K 不能复用 BASE 的 exit decision/order stream。
- SELL 必须绑定具体 `lot_id`，并遵守 T+1、FIFO 和剩余数量约束。
- 最后交易日拒单或过期的 SELL 如果没有后续 session，只能保留为明确的 `SELL_PENDING`，不能伪造 retry，也不能停留在 `EXIT_DUE`。
- 指标必须从 corrected fill path 重算；成本压力测试不得改变订单、退出决策或 lot 生命周期。

## Forward Data Contract pitfalls

- Prospective observation 的起点必须由 Policy Freeze 之后的显式 host trading calendar 确认；工作日推断和历史回填都不能替代交易日历。
- 现有 Paper host 的 `E_CONSEC_LIMIT` runtime 不能冒充当前 `E_LIMITUP_SENT` event-reversal candidate 的 prospective evidence；候选运行适配器未就绪时必须 fail closed。
- Validation Policy 的声明契约 hash 与文档文件 SHA-256 是两个不同字段；冻结 lineage 必须分别记录并比较。

## Validation classification integration pitfalls

- Candidate-local classification 不能直接驱动 Strategy Registry；必须等 testing family 的 BH 完成后由 `FinalResearchAdjudicatorV1` 产生 final decision。
- 历史分类校正只能追加 `ClassificationCorrectionEventV1` 并物化 current view；Batch1 与 corrected V3 必须各自使用原始冻结 decision family。
- `RESEARCH_PASSED` 和 `PROMISING` 不进入 Failure Knowledge；工程异常必须保持 `ENGINE_FAILURE`，不能 fall-through 到 `GOVERNANCE_FAILURE`。

## Frozen contract identity pitfalls

- 守护进程修复冻结合同身份冲突时，跨报告/合同扫描必须按 `candidate_id` 缓存；不得在逐历史记录加载路径中重复扫描全量报告。
- 已接入但 Provider 不兼容的冻结合同不能原地改写或删除；只能在确认无 TrialLedger、无 PerformanceAccess、无候选预算预留后追加哈希链纠错事件。`ONE_SHOT` 已接受调用对应的唯一候选被失效后，恢复逻辑必须进入 `GOVERNANCE_DECISION_REQUIRED`，不得生成第二份 AI 交接。
- Executable Materialization confirm 必须在写入 Confirmation Receipt 前比较已有 Durable Contract `content_hash` 与 immutable Preview 的 `durable_contract_hash`；Candidate ID/hash 相同但内容 hash 不同仍是 canonical conflict。
- Executable Materialization Confirmation Receipt 只证明人工批准，不是 READY 状态工件；应保持 `materialization_complete=false`、`contract_materialization_required=true`，不能单独宣称 `structural_preflight_ready=true`。

## Structural Provider pitfalls

- Headless daemon 的全窗口结构 Provider 必须显式沿用已验证的 `streaming=True, partition_session_count=80` 配置；不能因省略参数回落到默认 `20`，否则可能在长时间运行后触发 Provider 工程阻断。
- Durable Contract 自带的 inline 事件/因子不能只列入 `event_ids`/`factor_ids` 后再去全局 Registry 查找；Provider 必须按白名单从冻结定义物化，并记录 `INLINE_FROZEN_DEFINITION`、available-at 与 warmup 证据。新增 inline 物化能力时必须提升 streaming algorithm version，防止复用旧的零样本缓存。
- Lower-bound integrity 必须按语义识别 legacy 与当前 canonical 合同字段；`NEXT_LEGAL_SESSION_OPEN`、`same_session_sell_forbidden`、`holding_period_trading_sessions`、`tie_breakers` 和 PIT/risk/execution filters 不得因为字段方言不同被误判为证据缺失。没有 `END_OF_WINDOW_TRUNCATION` 且上下界相等时，不得反向要求尾部截断证明。
- Lower-bound integrity audit 会把架构安全计数 `PROSPECTIVE: 0` 作为证据嵌入结构结果；性能盲区 guard 只能按精确证据路径放行该统计字段，不能全局放行同名字段。
- Durable Frozen Contract 是持久化身份，不等同于 Provider 可直接执行的 payload；daemon 必须先重建 `full_semantic_record`、核对重复冻结字段并生成 canonical provider payload，禁止把原始合同 Mapping 直接传给 Provider。Lower-bound audit 的 Top-N、最大持仓、持有期及因子/事件来源必须读取当前冻结合同，不能复用历史候选常量。

## Predictive executor pitfalls

- Headless daemon 必须通过 `CanonicalPredictiveExecutorV1` 复用现有 corrected engine、预注册、`SearchBudgetRegistryV1`、`ResearchFactoryTrialLedgerFacadeV1`、`FinalResearchAdjudicatorV1` 和 `FailureKnowledgeAdapterV1`；不能另造单候选性能回测或账本。
- daemon 启动时必须先按 `candidate_id` 与 `candidate_hash` 对账 canonical terminal Trial；已访问且已终结的 Trial 只能复用，不能重新打开 `PerformanceAccess` 或创建免费 retry。
- 新候选的预算预留必须使用既有 objective/batch/family/candidate bucket；缺失或 hash/policy identity 不一致时 fail closed，不动态扩容预算。
- 同一 batch 内多个 Frozen Candidate 的 Trial ID 必须按 `candidate_hash` 隔离；只有单候选 batch 才允许使用裸 `batch_id_T001`，已存在的 terminal Trial 仍按 `candidate_id` 与 `candidate_hash` 精确复用。
- `SearchBudgetRegistryV1.reserve_trial()` 的 reservation identity 是确定性的；`RELEASED` 只表示上次预性能失败，下一次同候选安全重试时必须重新激活，`CONSUMED` 才是不可重新激活的终态。
- `python -m chanlun_trader.research_daemon` 会以 `__main__` 执行入口；入口必须把 `chanlun_trader.research_daemon` alias 到同一模块，避免 canonical executor 导入第二份 `PredictiveResult` 后触发类型身份不一致。
- 当结构预检已 `PASS` 但权威预测预算为 `0` 时，必须允许 `STRUCTURAL_PASS -> BUDGET_EXHAUSTED`；该边界不得调用 `PerformanceAccess`、预注册 Trial 或创建新候选。
- predictive 前置 cache 必须覆盖当前 Frozen Candidate contracts 的全部 executable `factor_ids`；旧阶段生成的因子 cache 不能冒充当前队列的完整 canonical cache。缺列时先用 `UnifiedFactorLibrary.FactorCompiler` 做 outcome-blind repair，再按日期/证券键校验后恢复 daemon。
- `RANK_ONLY`/`CROSS_SECTIONAL_PERCENTILE` 是横截面排名描述而非阈值条件；执行器和 runner 的预筛选不得读取缺失的 `operator/value`，必须在 PIT eligible universe 上按冻结 `ranking_rule` 计算百分位复合分数，并使用声明的稳定符号 tie-break。
- T001 访问绩效后的工程失效只能通过独立的 `AUTHORIZE_NEW_PREDICTIVE_TRIAL` → `START_PREDICTIVE_TRIAL_2` 受保护链路创建新 Trial；新 Trial 必须携带新的 `trial_id`、`trial_number` 和预算 reservation identity，并通过 CandidateWork metadata 让 canonical executor 精确选择它，不能复用 T001 或旧的候选级 reservation。
- daemon 读取 Frozen Candidate contracts 时必须严格按 `policy_identity.objective_id` 过滤；其他 Research Objective 的合同不得混入当前队列，也不得通过改写合同身份来规避 `CANONICAL_POLICY_OBJECTIVE_MISMATCH`。批量隔离后必须保留 out-of-scope 清单和安全对账证据。

## Checkpoint reconciliation pitfalls

- `PROMISING_FOLLOWUP / ONE_SHOT` 的唯一候选已有身份匹配的 canonical terminal Trial、`remaining_frozen_candidates=0` 且设计机会已消费时，剩余数值预算不代表还能继续设计。`RECOVER` 必须以 `AI_ONE_SHOT_POLICY_EXHAUSTED` 自动收官并进入治理，保持预算不变；旧缺陷留下的 `ACTIVE / RECOVERY_COMPLETE + process_pid=null` 只能在同一精确指纹下开放迁移恢复，普通 `ACTIVE` 不得放宽。
- daemon checkpoint 只是编排状态；恢复前必须优先对账 canonical TrialLedger、SearchBudget、ArtifactGraph、DurableFrozenCandidateContract 和 Candidate Registry，不能把 stale `ENGINEERING_BLOCKED` 直接当作当前研究真状态。
- 修复后的结构 `PASS` 不得通过启动完整 daemon 循环来“顺便写回”，否则同一次调用会跨入 predictive boundary。必须使用结构-only 对账：重跑 canonical structural preflight，锁定预算文件哈希和零 TrialLedger，写回后停在 `PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED`。
- 手动 AI 交接合同的 `policy_identity` 按协议只能含 `objective_id`；正式预测执行器必须从绩效访问前已落盘且身份一致的结构对账报告取得 V2 政策 ID、版本与哈希钉住。AI handoff 目录名和机制名不是预算桶身份；只能在目标预算注册表中唯一匹配既有 batch/family，禁止创建或扩充预算。
- 内联冻结事件/因子通过结构预检后，预测执行器必须复用同一冻结定义进行数据身份哈希和物化；不得假定一定存在 `event_store_repaired/<event>_v2.parquet`，也不得另造不同语义的事件数据。
- Web Console 的生命周期必须优先投影已落盘的结构执行事实：结构 `PASS` 且无 Trial 时，结构/样本阶段为已完成、预测阶段为当前并显示等待单独授权；外层 `AI_HANDOFF_BLOCKED` 仍作为精确 Orchestrator 状态单独展示，不能把阶段条压回 Candidate。
- daemon 重启恢复时若 checkpoint 停在 `STRUCTURAL_PASS`、`READY` 或 `NEXT_CANDIDATE` 且权威预测预算已耗尽，必须在选择候选或运行结构预检前转入 `BUDGET_EXHAUSTED`；不得尝试 `STRUCTURAL_PASS -> STRUCTURAL_PENDING`。
- 只有在旧 checkpoint 候选与 canonical terminal Trial 的 Candidate hash 精确一致时，才能清理旧编排阻断并清空当前候选；B10 身份修复不等于可以覆盖其他候选的 Provider 错误。
- 若阻断原因为 Trial ID collision，只有在碰撞候选没有任何 TrialLedger 事件、没有 PerformanceAccess、候选预算 `used=0/reserved=0` 且被占用 ID 已明确属于其他 Candidate 时，才能对账到 `READY` 并生成候选级替代 Trial ID；其他情况必须保持 `ENGINEERING_BLOCKED`。
- 若阻断原因为 `CANONICAL_BUDGET_RESERVATION_NOT_ACTIVE`，只有在候选没有 TrialLedger 事件、没有 PerformanceAccess、候选预算 `used=0/reserved=0` 且该 reservation canonical 状态为 `RELEASED` 时，才能对账到 `READY`；不得把 `CONSUMED` 或未知 reservation 当作可重试。
- B10 没有结构结果或 PerformanceAccess 时，canonical 真状态是 `STRUCTURAL_PENDING`；对账命令只能写 checkpoint/report/event，不得调用 Provider、结构预检、predictive executor 或 Performance reader。

## 人类输出语言规范

- 所有面向用户的摘要、报告、文档、CLI 输出和 PowerShell 输出默认使用简体中文（`zh-CN`）。
- Candidate/Strategy/Trial/Objective/run/artifact ID、Reason Code、enum、状态机值、schema、JSON key、registry key、hash、checkpoint 和其他机器合同保持 canonical English。
- 新的人类报告必须复用 `src/chanlun_trader/presentation.py` 的 `ZhCNPresentation`；machine JSON 与中文 human report 分离，不能为中文化覆盖历史 canonical artifact。
- daemon CLI 默认中文；`--json` 保持稳定的 English machine JSON。PowerShell helper 默认 UTF-8。
- 详细规则见 `docs/项目人类可读输出规范_V1.md` 与 `docs/项目报告索引.md`。

## Orchestrator launch and Codex CLI pitfalls

- Governance `CREATE_AND_ACTIVATE` 只能通过 canonical launcher 启动 objective-scoped Orchestrator；同一 `execution_id` 的启动记录必须可幂等复用，进程已退出时才允许在保留历史记录的前提下重启。
- Codex CLI 参数必须以实际安装版本的 `exec --help` 为准；旧版可能不支持 `--ask-for-approval` 或服务端 structured-output schema 方言，不能把参数解析错误误报为研究失败。
- Codex 认证、模型版本、MCP 初始化和超时属于工程集成阻塞；必须持久化受限 stdout/stderr 诊断并 fail closed，不能伪造 AI manifest、候选合同或预算消费。
- 手动交接 Schema 不能把 `full_semantic_record` 仅声明成泛化 object；必须暴露完整 Candidate、SignalPredicate、ExitPredicate 字段，并在提示词中写明 `policy_identity == {"objective_id": current_objective}`。端到端验收必须调用 `provider_candidate_payload()`，只测 `from_dict()` 不足以证明可接入。
- `PROMISING_FOLLOWUP` 的 `ONE_SHOT` 必须按 objective 类型继承；已接受调用移入 `invocation_history` 后仍算已消费，Orchestrator 不得因为 current invocation 被下一份等待任务覆盖而生成第二个候选。

## Web Console read-model pitfalls

- canonical Trial 编号可由 objective、批次、64 位候选哈希和序号拼接而成，合法长度可能超过 128；Trial 详情读取必须允许单个安全路径段范围内的完整编号（当前上限 255），但不得放宽字符集或路径穿越校验。
- 绩效访问中断后的 Trial 页面入口只能处理 `PERFORMANCE_ACCESSED` 且未形成 provisional evidence、未完成 final adjudication/registry commit 的同一 canonical Trial。链路必须是“只读诊断 → ledger/budget/checkpoint/TrialRegistry 稳定哈希 → 二次确认 → 本机限定接口 → daemon lock 下重新校验 → 追加 `ENGINEERING_INVALIDATED` 并消费既有预留”；不得重跑绩效、创建替代 Trial、补造统计结果或回退预算。已有 provisional evidence 时必须转入证据恢复与裁决，正常终态必须锁定。
- Provider 不兼容冻结合同的页面纠错必须形成“只读安全预览 → 稳定 preview hash/token → 二次确认 → 本机限定写接口 → 追加哈希链隔离事件”的链路。确认时必须重新证明候选合同身份、Provider 不兼容、零 Trial、零绩效访问和零活动预算占用；预览过期或已有 Trial 时 fail closed，且永远不得原地编辑/删除冻结合同或替用户生成 `ONE_SHOT` 变体。
- 结构修复后的 Web 入口只能调用 `reconcile_structural_pass()`，并形成“只读可用性投影 → 二次确认 → 本机限定写接口 → canonical 服务再校验”的链路；页面不得直接把状态改成 PASS。后端必须继续证明 READY、候选身份一致、零 Trial、零预算占用、outcome-blind、下界完整性和预算哈希不变，并在 PASS 后停在独立预测授权边界。
- 正式预测验证的人工入口必须形成“页面可用性投影 → 二次确认 → 本机限定写接口 → canonical 执行器再次校验”的完整链路；不能只提供后台脚本，也不能让前端直接绕过结构、预算、身份、完整性或运行锁。已存在正式 TrialLedger 记录时，页面和接口都必须拒绝重复授权。
- Web Console 必须把 objective-scoped Orchestrator AI handoff 与 daemon NoOutcome handoff 分开；没有目标身份的全局 Shadow/报告只能显示为平台资料或未绑定数据，不能当作当前 objective 的研究证据。
- 精确 Orchestrator 状态与生命周期阶段必须分开展示；进度条只能做阶段映射，不能用阶段名称覆盖 `AI_INVOCATION_PENDING` 等 canonical 状态。
- 研究目标必须先从 Web Console 的目标列表选择；治理页面不带 `objective_id` 时只能展示等待治理的目标列表，不能静默操作 `DEFAULT_OBJECTIVE_ID`，治理、进度和运行控制的接口请求必须使用同一个明确目标编号。
- `scripts/run_ui.py` 只启动 Web Console 页面服务，不会启动研究执行进程；页面上的“启动 AI 研究调用”必须复用当前 objective 的既有 `process_launch.json`/`execution_id`，通过 canonical launcher 幂等接管或重启 Orchestrator，不能在前端拼接 shell 命令。
- AI 运行进度必须从当前 objective 的 `codex_runtime_diagnostics.json` 做安全投影；页面可以展示状态、心跳时间、Manifest 检测和事件时间线，但不得展示 prompt、原始 stdout/stderr 或绩效字段。
- 治理执行服务必须优先读取 objective-scoped `research_orchestrator_v2/<objective_id>/governance_decision_required.json`；Orchestrator 写入的 `allowed_choices` 必须使用执行服务支持的 canonical action，不能写泛化但不可执行的 `START_NEW_GOVERNED_OBJECTIVE`。
- `START_PROMISING` 治理预览必须在顶层公开 `parent_candidate_identity_refs`，逐项只包含 `candidate_id`、`candidate_hash`、`family_id` 和 `mechanism`；该列表必须直接来自 canonical `PROMISING` 登记、顺序稳定并进入 preview hash。人工选择父候选后，该列表必须恰好一项，预览页与最终确认框必须展示同一项并锁定其哈希。
- `START_PROMISING` 后续验证必须先由人工从 `eligible_parent_candidates` 中选择恰好一个父候选；preview/confirm 必须携带并重新校验 `selected_parent_candidate_id` 与 `selected_parent_candidate_hash`。单父候选的 `parent_candidate_identity_refs` 必须恰好一项，未选候选保持 `PROMISING`，不得自动合并到本轮或修改状态。

## Web 启动隔离约束

- Web import/startup 和只读 GET 不得通过 Store 构造创建目录；目录创建放在既有显式写入路径，审计内容与恢复算法保持原语义。
- Web 服务必须通过 create_app 显式绑定 research_root；helper 不得回退 PROJECT_ROOT，静态文件仍属于源码目录。
- 判断会启动进程的治理模式时，必须与领域服务使用相同的大小写规范化；SYNTHETIC 和 GOVERNED 不替代确认、身份与具体执行策略。

## P3-B 恢复与测试约束

- 公共恢复只接收 root 与 Objective；从持久 intent 校验原 Action，先匹配 canonical 副作用，再判断无副作用时是否仍可重试。Action hash 本身不授予权限。
- Objective 写锁使用常驻文件和内核锁；不得按 TTL/PID 删除锁文件接管。共享 registry/graph 的读取、合并和写入必须处于资源锁内。
- dry-run 不创建锁文件、不修补 journal、不生成工件。Windows 锁定字节可能拒绝读取；文件快照应对零长度锁文件使用空内容哈希，不尝试读取锁定字节。
- 隔离认证使用全新 venv 与现场 synthetic workspace；系统 Python 的 editable distribution 发现可能访问原研究目录，不能沿用污染环境并把拦截次数算成零。
- 安全重试必须先确认已有回执，再登记一次 STARTED；FAILED/retry marker 下先 begin 再 allow_retry/begin 会虚增 attempt。回归必须检查落盘 FAILED 和真实 marker 退出点，不能用返回 FAILED 或普通 STARTED 代替。

## P3-C 生命周期认证约束

- 物化单项测试的 `_bridge_fixture` 手工补写下游 Proposal/Freeze/Registry，不能冒充完整合法生命周期。真实 Candidate Freeze 后仍须验证生成内容能直接进入 Materialization；轻量治理 candidate_hash 与语义 preregistration_hash 不可通过覆盖字段混用。
- 完整执行语义必须在 v2 Design 审批前声明并绑定哈希，后续只传递同一语义；旧轻量格式不得静默升级。新路径不能受旧轻量因子选择或执行默认值补猜影响。
- 控制平面读取 Predictive AUTHORIZED 必须验证既有 decision_hash；测试用真实治理服务生成授权，不以手写简化 status 行证明授权有效。即便授权有效，也保留 PHASE2_PREDICTIVE_EXECUTION_DISABLED。
