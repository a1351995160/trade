
# CURRENT SYSTEM ARCHITECTURE REVIEW V1

审计时间：2026-09-04（Asia/Shanghai）

审计对象：E:/llmwiki/chanlun-trading-system

审计口径：本报告只依据当前工作区内的源代码、配置、持久化数据、报告工件、前端构建物和当前进程/端口探测结果编写，不依据历史聊天记录推断当前状态。

审计时 Git worktree 不是干净基线：工作区已有大量已修改和未跟踪的代码、脚本、数据、前端产物、报告和 AI staging 工件。本报告不对这些变更做历史归因，也没有清理、回滚或覆盖它们；下文描述的是当前磁盘可见状态，不等同于某个已提交 commit 的状态。

本次审计是只读审计。没有启动 Web 服务、测试、AI 研究任务、Candidate、Trial 或预算操作。本次唯一业务交付是本报告；没有修改代码、配置或研究数据。

## 结论摘要

当前系统已经具备一个较完整的“受治理研究工厂”骨架：它可以把研究 Objective、AI 研究设计、Candidate Proposal、冻结 Candidate、Structural Preflight、Predictive Trial、Validation、Multiple Testing、Budget 和 Failure/Evolution 串成可持久化的研究链路，并在关键位置保留人工确认。

但当前系统还不能被判定为一个可安全开启的 Autonomous Alpha Research Loop V1。原因不是单个模块缺失，而是生产状态存在多套事实源并存、最近的 Evolution Objective 尚未形成 Candidate Proposal、运行快照与规范账本存在滞后/冲突、数据供应仍有明确缺口，以及缺少与 Web Console 等价的机器可调用治理动作层。

因此，接管人当前应把系统视为：

> 具有自主研究能力组件、但仍处于人工治理和恢复对账阶段的 Research Factory；不是正在运行的全自动研究循环。

# 项目定位

## 1. 系统现在解决什么问题

系统同时包含两条产品线：

1. 旧的缠论选股/回测线：读取通达信本地行情，执行筛选、缠论信号、回测和 Web 展示。
2. 新的 Research Factory 线：把研究假设、数据能力、因子/策略 Candidate、结构可行性、预测性 Trial、统计验证、预算、Multiple Testing、失败分析和研究演进组织成可追溯的研究生命周期。

对于未来的量化研究，Research Factory 线解决的是以下工程问题：

- 研究对象、父子 lineage 和研究原因可以持久化；
- Candidate 和冻结合同可以通过 hash 保护，避免无声覆盖历史定义；
- 结构可行性和预测性 Trial 分离；
- 预算、检验家族、Trial 账本和最终裁决有独立的规范模块；
- 失败结果可以转换成 outcome-blind 的下一轮 Research Proposal 和 AI Research Design；
- Proposal、AI Design、Candidate Proposal 和 Candidate Freeze 等边界有人工治理点。

它当前解决的是“可审计、可恢复、可治理的研究流程编排”，不是“自动产生可交易 Alpha”，也不是实盘交易系统。目录中仍然保留 legacy backtest/screener 入口，因此项目整体的安全边界不能只根据 Research Factory 的规范链路判断。

# 系统架构

## 2. 当前整体架构

当前架构可以分成四个平面：

- 交互平面：Vue 3 Research Console 和旧版选股/回测页面。
- API 与读模型平面：FastAPI、Research Console read service、各治理/启动服务。
- 研究编排平面：AutonomousResearchOrchestratorV2、research daemon、Research Factory 的状态和恢复逻辑。
- 规范研究平面：Objective/lineage、Candidate contracts、Structural Preflight、TrialLedger、Validation、Multiple Testing、Budget、Failure/Evolution 和数据访问保护。

其中 Orchestrator 和 daemon 只应负责控制状态、调度和恢复提示；Candidate、Trial、Budget、Validation 和 ArtifactGraph 等事实必须从各自的规范账本、合同和报告中恢复。当前代码已经有这种设计意图，但运行工件中仍能观察到 checkpoint、daemon snapshot 和规范账本不同步的情况。

## 3. 分层数据流

~~~text
Frontend
  ├─ Vue Research Console
  └─ legacy Screener / Backtest UI
        │ HTTP JSON
        ▼
API
  └─ FastAPI: src/chanlun_trader/webapp.py
        │
        ▼
Research Console
  ├─ read model: objectives / candidates / trials / budget / lineage / AI status
  ├─ governance writes: review / confirm / reconcile / authorize
  └─ loopback-only write boundary for local console requests
        │
        ▼
Orchestrator
  ├─ AutonomousResearchOrchestratorV2
  └─ research_daemon
     （控制状态、checkpoint、锁和恢复提示；不是 Candidate/Trial/Budget 事实源）
        │
        ▼
Research Factory
  ├─ Objective / lineage / ArtifactGraph
  ├─ AI backend / manual handoff / AI design
  ├─ Candidate Proposal / frozen contract / Candidate Registry
  ├─ Structural Preflight / sample feasibility
  ├─ Multiple Testing / SearchBudgetRegistryV1
  ├─ Predictive Executor / TrialLedger
  ├─ Validation / FinalResearchAdjudicatorV1
  └─ Failure Adapter / Evolution Manager / Proposal Bridge
        │
        ▼
Candidate Registry
  └─ durable frozen candidate contracts and append-only governance records
        │
        ▼
Trial Engine
  └─ corrected predictive engine + explicit authorization/start + TrialLedger
        │
        ▼
Validation
  ├─ PIT Stage 1 / future-access checks
  ├─ performance and statistical validation after access gate
  ├─ BH / Multiple Testing family adjudication
  └─ final classification and failure extraction
        │
        ▼
Governance
  ├─ human review / confirmation
  ├─ immutable previews and hashes
  ├─ budget and authorization records
  └─ rejection, closure, correction and recovery decisions
~~~

## 4. 当前架构的重要边界

Research Factory 的规范链路与旧入口并存：

- 规范链路：以 objective-scoped contracts、TrialLedger、SearchBudgetRegistryV1、ArtifactGraph 和治理记录为主。
- 旧入口：/api/screener、/api/backtest、旧的策略运行脚本和 legacy research artifacts 仍然存在。

这意味着“某条规范链路不会自动启动 Trial”不等于“整个仓库不存在任何可以执行回测/研究的入口”。交接时必须先区分 canonical research path、legacy backtest path 和当前实际运行态。

## 5. 物理存储不是单一 canonical workspace

逻辑上各模块共享同一个项目根目录，但物理上是多源联邦存储，而不是一个统一研究数据库：

- 外部行情依赖 E:/new_tdx_mock/vipdoc 和相关 hq_cache；
- data/research 下同时存在 event、factor、label、market_5m、registry、ledger、performance_cache 等多个域；
- data/research_full.db 之外还存在其他 research SQLite 数据库；
- reports/research_orchestrator_v2、reports/research_daemon、reports/research_factory 保存不同运行投影；
- research_ai_staging 保存 AI handoff 和结果；
- data/cache、data/tdx/cache、performance cache 和外部 alpha cache 并存；
- experiments 下还有独立的 CSV/Parquet 台账。

Research Console 通过后端聚合这些来源，并不直接把它们变成一个事务性事实源。审计还发现 Candidate Registry 与 durable frozen contract 的写入/读取路径不完全相同，canonical batch TrialLedger 与 reports/research_factory 下的历史 ledger 也并存。接管人必须把“共享项目目录”与“单一事实空间”区分开。

# 数据流

## 1. 数据来源与进入方式

当前数据能力登记在 data/research/data_capability.json，路由规则登记在 data/research/data_routing/routing_policy.json。主要来源和角色如下：

| 数据来源 | Provider / 入口 | 进入系统后的主要用途 | 当前约束 |
|---|---|---|---|
| 通达信 .day 日线 | TDXDailyProvider / TQ local | 日线 OHLCVA、基准和研究日线 | 本地文件、manifest 和 available-at 规则必须一致 |
| 通达信 GBBQ | TDX corporate-action provider | 复权/公司行动/PIT 状态 | 不能把复权数据和可交易性状态混为一层 |
| BaoStock 5 分钟 | baostock_provider.py | 冻结训练期 5m 数据 | 训练集路由已冻结，使用 bar-end 语义 |
| 通达信 .lc5 | tdx_raw_hq_provider.py、TDX5MinProvider 相关路径 | 较新区间、补缺和交叉核验 | 已知 2024-08-01 至 2024-10-08 缺口必须 fail closed |
| TQ 在线数据 | tdx/providers.py 的 pro/event provider | 财务、龙虎榜、涨停生态、资金流等能力探测 | 当前登记为 online-only，未持久化前不应进入正式批量研究 |
| 本地研究 Parquet / SQLite | guarded reader、research stores | 日线、5m、factor、label、security state、failure library | 需要由 manifest、版本和 objective lineage 约束使用范围 |

系统推荐的数据路径是：

~~~text
TDX / BaoStock / 本地文件 / 在线能力
  → Provider
  → normalizer + guarded loader
  → cache + manifest + data capability
  → daily / minute / event / factor / label / security-state stores
  → unified factor / strategy semantic contract
  → frozen Candidate contract
  → Structural Preflight
  → corrected predictive engine
  → TrialLedger / Validation / Failure / Evolution
~~~

## 2. Provider、Loader、Cache、Feature、Signal 的责任

- Provider 负责连接具体来源、取得原始数据、报告可用区间和供应商语义。
- Loader/normalizer 负责字段、时间戳、OHLC、数值和交易时段的规范化。
- Cache/manifest 负责保存数据版本、文件来源、覆盖范围、available-at 和数据完整性证据。
- Feature 层把可用数据转换为注册因子或事件特征，不能绕过 PIT 和研究数据访问 guard。
- Signal/strategy semantic 层把研究意图映射成显式的 factor contract、execution contract、data contract 和 validation policy。
- Candidate Registry/frozen contract 固化研究定义；后续修改必须形成新的 Candidate，而不能覆盖旧 Candidate。

## 3. PIT、可交易性和未来函数保护

当前代码中已有多层保护：

- src/chanlun_trader/engine/asof.py 使用 AsOfDataView，要求读取时间不超过当前研究时钟。
- src/chanlun_trader/research/pit_tradability.py 管理 PIT security master、ST、停牌、available-at 和未知状态；未知不能静默转换为可交易或停牌。
- strategy_validation.py 在 Stage 1 执行 PIT preflight 和 future-access check，性能数据必须等访问闸门打开后才能读取。
- 研究路由明确区分训练期 5m、后续期 .lc5 和不同事件数据，不允许静默拼接 Provider。
- 缺少分钟数据时不能把缺失 bar 当成停牌；已知数据缺口应返回 GAP_FAIL_CLOSED。
- 交易执行还受 T+1、FIFO/lot、涨跌停、公司行动和固定交易 session index 约束。

因此，PIT 保护在规范代码路径中是明确存在的。但它依赖 Provider、manifest、数据范围和运行时使用的具体入口；legacy 入口、外部本地数据漂移和未持久化 online-only 数据仍然是未来函数风险的主要来源。

## 4. 当前已知数据风险

当前 capability registry 表示：

- 日线、公司行动、日线数据和分钟数据能力已登记为 PIT-safe，但这不代表所有外部文件在任何时点都新鲜或完整。
- 财务、龙虎榜、涨停生态和资金流仍是 online-only；若没有批量持久化、available-at 和版本登记，不能视为正式研究数据。
- 5m 路由存在明确历史缺口；系统设计为 fail closed，而不是自动填充。
- 本地 TDX/mock 数据的实际最新日期会随外部目录变化，因此研究结果必须绑定 data manifest、source fingerprint 和 research cutoff。

# 状态机

## 1. 端到端研究状态机

下面是依据当前代码、治理常量、报告和实际工件整理的业务状态机。它是跨模块的交接视图，不代表仓库中已经存在一个单一的全局枚举。

~~~text
Objective CREATED
    │ 需要 AI 设计
    ▼
NEED_AI_RESEARCH_DESIGN
    │ AI 设计结果已落盘，并等待人工确认
    ▼
AI_DESIGN_READY
    │ 生成候选建议
    ▼
CANDIDATE_PROPOSAL_READY
    │
    ▼
HUMAN_REVIEW_REQUIRED
    ├────────────── reject ──────────────► REJECTED ─► CLOSED
    │ approve
    ▼
FREEZE_PREVIEW_READY
    │ 人工确认冻结
    ▼
FROZEN
    │ 仅允许进入结构预检入口，不自动执行
    ▼
READY_FOR_STRUCTURAL_PREFLIGHT
    │ 人工/治理动作启动结构预检
    ├─ PASS ─────────────────────────────► READY_FOR_PREDICTIVE_AUTHORIZATION
    ├─ UNKNOWN / insufficient ───────────► BLOCKED
    └─ integrity failure ────────────────► ENGINEERING_BLOCKED
                                             │
                                             ▼
                              PREDICTIVE_VALIDATION_AUTHORIZATION_REQUIRED
                                             │ 人工授权和确认
                                             ▼
                                      PREDICTIVE_RUNNING
                                             │
                                             ▼
                                      PREDICTIVE_COMPLETE
                                             │ Final adjudication
                         ┌───────────────────┴──────────────────┐
                         ▼                                      ▼
                 RESEARCH_PASSED / PROMISING              WEAK / REJECTED
                         │                                      │
                         └──────────────► Evolution / next objective
~~~

Proposal Governance、AI Design、Candidate Generation、Predictive Governance、ResearchBatch 和 Orchestrator 各自拥有局部状态。因此，接管时不能只看一个 state 字段就判断整个生命周期。

## 2. 每一步的输入、输出、状态与负责人

| 步骤 | 输入 | 输出 | 业务状态 | 主要负责人 |
|---|---|---|---|---|
| Objective 创建 | 已批准 Proposal、Objective Creation Preview、父 lineage、预算和检验家族建议 | immutable objective id、objective JSON、lineage、budget registry、governance record、ArtifactGraph | Objective CREATED；编排器随后可进入 NEED_AI_RESEARCH_DESIGN | research_proposal_governance.py、governance_execution.py |
| AI Research Design | Proposal、Failure Landscape、Mechanism Coverage Registry、Objective lineage、data capability；输入必须 outcome-blind | AI_RESEARCH_DESIGN_INPUT.json、AI_RESEARCH_DESIGN_PROPOSAL.json、AI state | AI_DESIGN_READY，下一动作是人工确认 | research_evolution_ai_design.py、AI backend/manual handoff |
| Candidate Proposal | 已确认的 AI Design、Objective contract、允许的 factor/data/execution contract | 人工可审的 Candidate Proposal 及其 hash/lineage | CANDIDATE_PROPOSAL_READY、HUMAN_REVIEW_REQUIRED | candidate_generation.py |
| Candidate Freeze | Candidate Proposal、人工 review、Freeze Preview、proposal/candidate hash | immutable Freeze Preview、freeze receipt、Candidate Registry、durable frozen contract | FROZEN，随后 READY_FOR_STRUCTURAL_PREFLIGHT | candidate_generation.py、Candidate governance service |
| Structural Preflight | Frozen Candidate、PIT/data capability、sample feasibility、execution/data contract | structure-only feasibility、lower-bound integrity、阻断或放行证据 | PASS、UNKNOWN、BLOCKED 或 ENGINEERING_BLOCKED | sample_feasibility.py、sample_feasibility_v2.py、real_sample_feasibility.py、structural_reconciliation.py |
| Predictive Trial | 已通过结构入口的 frozen contract、预注册、budget reservation、testing family、显式授权 | corrected engine output、TrialLedger events、预算消费记录、Trial artifact | PREDICTIVE_RUNNING、PREDICTIVE_COMPLETE 或终止/失效 | predictive_executor.py、predictive_trial_start.py、predictive_trial_reauthorization.py |
| Validation | 完整 Trial artifact、PIT 检查、性能访问闸门、testing family | validation report、BH 结果、FinalResearchAdjudicator decision、classification | CLASSIFYING 后进入 RESEARCH_PASSED、PROMISING、WEAK 或 REJECTED | strategy_validation.py、multiple_testing.py、classification_integration.py |
| Evolution | 最终裁决、Failure Landscape、Failure Knowledge Adapter、Mechanism Coverage Registry | Research Evolution Report、Proposal、下一 Objective/AI Design 输入 | FAILURE_EXTRACTING、Proposal CREATED/HUMAN_REVIEW_REQUIRED 等 | research_evolution_manager.py、research_evolution_proposal.py、failure_adapter.py |

## 3. 当前代码中的局部状态机

### Orchestrator 状态

AutonomousResearchOrchestratorV2 定义了 BOOTSTRAP、RECOVER、ACTIVE、LOCAL_RESEARCH_RUNNING、NEED_AI_RESEARCH_DESIGN、AI_MANUAL_HANDOFF_REQUIRED、AI_RESEARCH_DISABLED、AI_INVOCATION_PENDING/RUNNING、AI_OUTPUT_VALIDATING、AI_BATCH_INGESTING、GOVERNANCE_DECISION_REQUIRED、RESEARCH_PASSED、BUDGET_EXHAUSTED、GLOBAL_SEARCH_EXHAUSTED、SAFETY_STOP、PAUSED 等状态。AI 允许触发点被限制为 NEED_AI_RESEARCH_DESIGN，治理要求、预算耗尽、安全停止和暂停状态不允许直接触发 AI。

### ResearchBatch 状态

state_machine.py 的正常链路是：

~~~text
CREATED
 → BUDGET_RESERVED
 → DESIGNING
 → HYPOTHESES_FROZEN
 → CANDIDATES_FROZEN
 → STAGE1_VALIDATING
 → PERFORMANCE_VALIDATING
 → CLASSIFYING
 → FAILURE_EXTRACTING
 → COMPLETED
~~~

BLOCKED、ENGINEERING_BLOCKED、BUDGET_EXHAUSTED 和 INVALIDATED 是终止或阻断分支。

### Proposal 与 Candidate Governance 状态

Proposal Governance 已实现：

~~~text
CREATED
 → HUMAN_REVIEW_REQUIRED
 → APPROVED
 → OBJECTIVE_CREATION_READY
 → READY_FOR_CONFIRMATION
 → CREATE_OBJECTIVE

HUMAN_REVIEW_REQUIRED
 → REJECTED
 → CLOSED
~~~

Candidate Governance 已实现：

~~~text
CANDIDATE_PROPOSAL_READY
 → HUMAN_REVIEW_REQUIRED
 → FREEZE_PREVIEW_READY
 → FROZEN
 → READY_FOR_STRUCTURAL_PREFLIGHT

HUMAN_REVIEW_REQUIRED
 → REJECTED
 → CLOSED
~~~

冻结、确认和创建均有 hash、reviewer、timestamp、idempotency 或 receipt 约束；但这些局部状态仍需要被单一的 objective-scoped read model 和恢复对账机制统一。

# 模块清单

以下状态区分“代码能力已实现”和“当前生产链路是否有对应工件”。代码已实现不代表最新 Evolution Objective 已经执行到该步骤。

| 模块 | 代码位置 | 作用 | 当前完成状态 |
|---|---|---|---|
| Research Evolution Manager | src/chanlun_trader/research_factory/research_evolution_manager.py | 从最终 Trial/Validation/分类结果提取失败机制、Failure Landscape 和 Evolution Report | 已实现；读取型失败分析工件存在；不修改 Trial/Budget |
| Research Evolution Design Bridge | src/chanlun_trader/research_factory/research_evolution_proposal.py | 将 outcome-blind 失败知识转换成 Research Proposal 和 Mechanism Coverage Registry | 已实现；当前 Proposal 工件存在 |
| Research Proposal Governance | src/chanlun_trader/research_factory/research_proposal_governance.py | Proposal review、reject、approve、Preview、人工确认和 Objective materialization | 已实现；当前 Proposal 有 review/confirm/create 记录 |
| AI Research Design | src/chanlun_trader/research_factory/research_evolution_ai_design.py | 读取 Proposal、失败景观、机制覆盖、lineage 和数据能力，生成 AI Research Design Proposal | 已实现；当前 Evolution Objective 有 AI_RESEARCH_DESIGN_INPUT.json 和 AI_RESEARCH_DESIGN_PROPOSAL.json；Objective registry 与 AI artifact 的状态存在分裂 |
| Candidate Generation Governance | src/chanlun_trader/research_factory/candidate_generation.py | 将 AI Design 转成 Candidate Proposal，提供 review 和 reject | 已实现；当前工件目录没有最新 Evolution Objective 的 Candidate Proposal |
| Candidate Freeze | src/chanlun_trader/research_factory/candidate_generation.py | 生成 immutable Freeze Preview，人工确认后写 Candidate Registry 和 frozen contract | 已实现；历史 frozen contract 存在，但最新 Evolution Objective 尚无对应生产 Registry 工件 |
| Structural Preflight | src/chanlun_trader/research_factory/sample_feasibility.py、sample_feasibility_v2.py、real_sample_feasibility.py、structural_reconciliation.py | 只验证样本、数据、PIT、执行和组合结构可行性，不读取收益结果 | 已实现；规范入口存在；历史 Objective 有结构工件；不自动启动 Trial |
| Predictive Trial | src/chanlun_trader/research_factory/predictive_executor.py、predictive_trial_start.py、predictive_trial_reauthorization.py | 统一 corrected engine、预注册、预算预留、TrialLedger 和显式授权/启动 | 已实现；4 个 canonical ledger、16 个唯一 Trial ID；当前无实时运行进程 |
| Validation Engine | src/chanlun_trader/research/strategy_validation.py 及 research_factory/classification_integration.py | PIT、future access、性能访问闸门、统计检验、FinalResearchAdjudicator 和 append-only classification | 已实现；同时存在 legacy/V1/V2 代际，规范主链仍需统一事实源 |
| Multiple Testing | src/chanlun_trader/research/multiple_testing.py、strategy_validation.py | 注册 testing family，执行 BH，并在 final adjudication 固定 family/q/method/hash | 已实现；当前预算 registry 中有多个 testing family |
| Budget Governance | src/chanlun_trader/research_factory/budget.py、run_budget.py | 试验级/运行级预算 reservation、consume、release、identity 和 reconcile | 已实现；canonical budget 可读；daemon snapshot 与 SearchBudgetRegistry 存在已观测冲突 |
| Web Console | frontend/src/console/ResearchConsole.vue、frontend/src/console/api.ts、src/chanlun_trader/webapp.py | 展示研究状态、Proposal、AI Design、Candidate、Trial、Budget，并承载本地治理动作 | 已实现；页面和 API 存在；写入限制为本地控制台请求，但尚无等价 agent tool registry |
| Orchestrator / Daemon | src/chanlun_trader/research_factory/autonomous_orchestrator_v2.py、src/chanlun_trader/research_daemon.py | 编排、checkpoint、锁、恢复和状态投影 | 已实现；当前部分 checkpoint/daemon 状态落后于 canonical ledger |
| ArtifactGraph / Lineage | src/chanlun_trader/research_factory/artifact_graph.py、objective/governance 模块 | 追踪 Proposal、Objective、Candidate、Trial、Validation 和 Evolution 的父子关系 | 已实现；历史和当前工件同时存在，需要清理 orphan/legacy 解释 |

# 当前运行状态

## 1. 运行环境探测

审计时没有发现运行中的 Python research daemon、Uvicorn 服务或当前研究进程；端口 8000、8001、8050、8501、17709 没有发现目标监听。frontend/dist 已存在，但静态构建物存在不等于 Web 服务当前正在运行。

因此，下面的 ACTIVE 只表示持久化 checkpoint 的原始字段，不能解释成“现在有进程正在执行”。

## 2. Objective 清单

当前 data/research/research_factory/objectives/ 中有 7 个 Objective 文件：

| Objective | Objective registry 原始状态 | Orchestrator / daemon 证据 | Candidate / Trial 证据 | Budget 证据 | 交接解释 |
|---|---|---|---|---|---|
| RESEARCH_OBJECTIVE_EVOLUTION_V1_BE80F967D29706F19703C869 | lifecycle_state=CREATED；next_action=HUMAN_REVIEW_REQUIRED；activation_authorized=false；ai_invocation=DISABLED | 没有当前 orchestrator/daemon checkpoint；AI Design state 是 AI_DESIGN_READY，下一动作 HUMAN_CONFIRM_AI_RESEARCH_DESIGN | 当前 Candidate Proposal 0；canonical Trial 0 | 0/4 used，剩余 4 | WAITING。最新 Evolution 链停止在 AI Design 人工确认前后，且 Objective registry 与 AI artifact 状态需要对账 |
| RESEARCH_OBJECTIVE_GOVERNED_NEW_MECHANISM_V1_A8D9D0C881545ABC845C | READY；activation true | checkpoint 原始 ACTIVE；daemon 为 READY，其持久化运行信息还出现 predictive complete | 1 个 durable frozen contract；2 个 Trial：T001 engineering invalidated，T002 final classification BLOCKED | 2/6 used，剩余 4 | 状态冲突，不能直接续跑；应先以 TrialLedger、SearchBudget 和 ArtifactGraph 对账 |
| RESEARCH_OBJECTIVE_GOVERNED_PROMISING_FOLLOWUP_V1_97AA9C76F4453762D2BF | READY；activation true | checkpoint GOVERNANCE_DECISION_REQUIRED；daemon READY | 42 个 durable contract ID；当前 B01 没有 canonical Trial ledger | 0/4 used，剩余 4 | WAITING，历史合同数量多，但当前 Trial 是否可启动需重新走治理和对账 |
| RESEARCH_OBJECTIVE_GOVERNED_PROMISING_FOLLOWUP_V1_C882F44B3C581B08ACF8 | READY；activation true | checkpoint GOVERNANCE_DECISION_REQUIRED；daemon READY | 1 个 durable contract；1 个 final Trial，classification BLOCKED | 1/4 used，剩余 3；其 family 视图为 1/1 已用 | WAITING/历史终止结果；不能仅按 Objective 剩余预算直接重试 |
| RESEARCH_OBJECTIVE_GOVERNED_PROMISING_FOLLOWUP_V1_FC8E61C3225889C40F98 | READY；activation true | checkpoint GOVERNANCE_DECISION_REQUIRED；daemon READY | 1 个 durable contract；1 个 final Trial，classification BLOCKED | 1/4 used，剩余 3 | WAITING/历史终止结果 |
| RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1 | legacy/simple schema，无新的 lifecycle 字段 | checkpoint GOVERNANCE_DECISION_REQUIRED；daemon BUDGET_EXHAUSTED | 22 个 unique durable contract ID；12 个 Trial，其中有完成和 engineering invalidated 历史 | 12/12 used，剩余 0 | BLOCKED / BUDGET_EXHAUSTED；不得恢复为普通 ACTIVE |
| CODEX_GUIDED_AUTONOMOUS_RESEARCH_PILOT_V1_OBJECTIVE | legacy/simple schema，无当前 lifecycle/activation | 没有当前 orchestrator/daemon checkpoint | 没有可归属到该 ID 的当前 canonical 运行证据 | 未形成当前可比预算视图 | WAITING/UNKNOWN；先归档其历史语义，不能当作当前 ACTIVE |

### 按用户要求的状态归类

- ACTIVE：持久化 checkpoint 中只有 New Mechanism 原始显示为 ACTIVE；但没有运行进程，且其 checkpoint/daemon 与 canonical ledger 不一致，因此不能判定为实时 ACTIVE。
- COMPLETED：没有 Objective 文件明确为 COMPLETED。部分 Trial 已有 PREDICTIVE_COMPLETE 和 final adjudication，但 Trial 完成不等于 Objective 完成。
- BLOCKED：Short Horizon 的 daemon 是 BUDGET_EXHAUSTED；若按研究结果看，New Mechanism、C882、FC8E 的若干 final Trial classification 为 BLOCKED。
- WAITING：Evolution、97AA、C882、FC8E 等至少有人工治理、恢复对账或授权前置条件；它们不能被当作无人值守运行。

## 3. Candidate 数量

必须区分三种数量：

1. 当前 Candidate Proposal：0。当前没有 data/research/research_factory/candidates 目录、reports/research_candidates 目录，也没有在当前 factory batch 路径发现新的 CANDIDATE_PROPOSAL.json。
2. 当前 Evolution Objective 的 Candidate Registry/Frozen Candidate：0。最新 Evolution Objective 尚未产生新的 Candidate Registry 工件。
3. 历史 durable frozen contract inventory：62 个文件、94 条合同记录、88 个 unique candidate ID。按当前 7 个 Objective ID 归属的 unique ID 为 67，另有 21 个 orphan/legacy 或缺失 Objective 归属的 ID。这个库存不能直接当作当前可运行 Candidate 数量。

## 4. Trial 数量

canonical factory_trial_ledger.json 共 4 个文件、83 条事件记录、16 个 unique Trial ID：

- New Mechanism：2 个；
- C882：1 个；
- FC8E：1 个；
- Short Horizon：12 个；
- Evolution 和 97AA 当前没有 canonical factory Trial ledger。

此外，reports/research_factory/factory_trial_ledger.json 还存在一份独立的 legacy/回退 ledger。它不计入上述 canonical batch 统计；Orchestrator、Research Console 和不同恢复路径可能读取不同 ledger，因此不能把不同路径的 Trial 数量简单相加，也不能无对账地把 legacy ledger 当作当前事实。

当前没有实时 Trial 进程，也没有在本次审计中运行任何 Trial。

## 5. 预算情况

当前可读到 6 个 SearchBudgetRegistryV1：

| Objective / family | 已用 | 总额 | 剩余 | 解释 |
|---|---:|---:|---:|---|
| Evolution / MTF_PROPOSAL_EVOLUTION_V1_98091EC18FD6C0A192F7 | 0 | 4 | 4 | 尚未进入 Candidate/Trial |
| New Mechanism / MTF_NEW_MECHANISM_V1_956CBB756280B3395508 | 2 | 6 | 4 | 规范账本状态；需与旧 daemon snapshot 对账 |
| 97AA family | 0 | 4 | 4 | 尚未有当前 Trial 消费 |
| C882 family | 1 | 4 | 3 | Objective bucket 与 family bucket 语义需一并核对；family 视图显示 1/1 |
| FC8E family | 1 | 4 | 3 | 有 1 个 canonical Trial |
| Short Horizon | 12 | 12 | 0 | 预算耗尽 |

New Mechanism 的 daemon snapshot 曾显示 used 0/6，而 SearchBudgetRegistry 显示 2/6；这是当前最直接的预算事实源冲突之一。恢复时必须以 canonical registry、TrialLedger 和 reservation identity 对账，不能以 daemon checkpoint 直接恢复。

## 6. 当前最新研究停止在哪里

按当前工件的可验证状态，最新 Evolution Objective 的链路是：

~~~text
Research Proposal
  → Objective registry: CREATED / HUMAN_REVIEW_REQUIRED
  → AI design input and proposal written
  → AI design artifact: AI_DESIGN_READY
  → HUMAN_CONFIRM_AI_RESEARCH_DESIGN
  → 尚未发现 Candidate Proposal
  → 尚未发现 Candidate Registry / Trial
~~~

也就是说，当前系统停在 AI Research Design 的人工确认和状态对账边界，尚未进入 Candidate Proposal，更没有进入 Structural Preflight 或 Predictive Trial。

# AI研究流程

## 1. 当前 AI 可以做什么

在 Evolution Objective 的规范路径中，AI 可以：

- 读取 Research Evolution Proposal；
- 读取 Failure Landscape；
- 读取 Mechanism Coverage Registry；
- 读取 Objective lineage；
- 读取已登记的数据能力和 PIT-safe/online-only 能力说明；
- 根据这些 outcome-blind 上下文生成 AI_RESEARCH_DESIGN_PROPOSAL.json；
- 输出 research hypothesis、mechanism family、candidate design intention、allowed factors、excluded mechanisms 和 validation expectation；
- 以 manual handoff 或受控的 AUTO_CODEX 方式提供设计结果，结果仍需要状态验证和人工确认。

当前实际 Evolution AI Design 工件显示：

- outcome_blind=true；
- outcome_fields_available=false；
- performance_data_loaded=false；
- 输入包含失败机制、禁止重复机制、建议探索方向、lineage 和可用数据能力；
- 输出状态为 AI_DESIGN_READY，下一动作是 HUMAN_CONFIRM_AI_RESEARCH_DESIGN。

## 2. AI 明确不能做什么

在当前治理链路中，AI 设计阶段不能：

- 读取收益、胜率、回撤、p-value、单笔交易或历史绩效字段来决定方向；
- 自动创建 Candidate；
- 自动冻结 Candidate；
- 自动启动 Structural Preflight；
- 自动授权或启动 Predictive Trial；
- 消耗或扩张 Search Budget；
- 修改历史 Candidate、Trial、Validation 或最终裁决；
- 绕过人工 Proposal、Candidate Freeze、Predictive Authorization 等治理点；
- 在禁止触发的治理、预算耗尽、暂停、安全停止等状态下自动调用 AI。

这里的“不能”是 canonical Evolution AI Design contract 的边界。旧版回测、旧脚本和显示完整 Trial 结果的读取路径仍存在，所以不能把它扩展成“仓库任意 Python 代码都无法接触结果”。

## 3. MANUAL_HANDOFF、AUTO、DISABLED

当前策略文件 reports/AI_INVOCATION_MODE_POLICY_V1.json 给出的模式是：

| 模式 | 行为 |
|---|---|
| MANUAL_HANDOFF | 默认模式。系统生成结构化 AI 任务输入和 handoff，人工交接或导入结果；后台不会因为手动交接消耗 AI token |
| AUTO_CODEX | 支持自动调用 Codex/AI backend，但只能从允许的研究设计触发点进入，并且必须通过输入/输出验证和治理状态检查 |
| AI_DISABLED | 不创建 AI 任务，不调用 AI；Objective 当前字段使用 DISABLED 表达这一事实 |

当前 Evolution Objective 的 Objective registry 明确为 ai_invocation=DISABLED。磁盘上虽然已有 AI design proposal，但这只能证明设计工件存在，不能证明审计时正在运行 AI，也不能替代 Objective registry 的状态对账。

## 4. AI 输入与输出的隔离

### 允许输入

- 已失败机制和失败类型；
- 禁止重复的 mechanism family；
- 建议探索方向；
- 当前 Objective 的 parent lineage；
- Mechanism Coverage Registry；
- 数据 provider、覆盖范围、PIT-safe 标记、online-only 标记和已知 gap；
- 研究合同中允许的因子、事件和执行能力。

### 禁止输入

- 收益；
- 胜率；
- 回撤；
- p-value；
- 单笔交易；
- 历史绩效；
- 任何可把候选方向按历史结果排序的性能摘要。

### 当前实际风险

当前 Evolution Design 输入已拦截显式的结果数值和常见绩效字段，但审计还发现：

- FailureKnowledgeAdapterV1 会把历史结果映射为方向性的 reason code，例如某机制“不支持”或“非正收益”类原因。即使没有数值，这仍可能向 AI 传递结果方向。
- Evolution 入口、Codex prompt 和自治 handoff 使用不同的上下文/guard 契约，三条入口的字段拦截并非完全同一实现。
- 实际 handoff 的能力摘要可能由已有 Candidate 因子反推，当前没有 Candidate 时可能为空；完整的 Factor/Event capability catalog 尚未成为 AI 的权威输入。
- handoff 有按 current handoff id 复用旧上下文的路径，未必每次都重新核对预算、候选空间、失败知识、data capability 和 runtime freshness。
- daemon 健康、资源、freshness 和当前安全状态主要供 UI read model 使用，尚未完整注入 AI prompt。
- 自动 Codex 执行路径的命令包含 approve-for-me 参数，但审计未确认与设计声明完全一致的操作系统级 sandbox/allowlist；因此逻辑上的 staging 隔离不能等同于已证明的物理读边界。

所以当前可以确认“没有显式绩效数值进入最新 Evolution Design artifact”，但不能把它扩大解释成“没有任何结果方向信息”或“所有 AI 入口都具有同等强度的 outcome blindness”。

### 输出

reports/research_evolution/ai_design/<objective_id>/AI_RESEARCH_DESIGN_PROPOSAL.json 当前包含：

- hypothesis；
- mechanism family；
- candidate design intention；
- allowed factors；
- excluded mechanisms；
- validation expectation；
- design hash；
- outcome-blind 和 governance flags；
- 人工确认所需的 next action。

# 治理安全

## 1. 七项安全检查结论

| 检查项 | 当前证据 | 结论 |
|---|---|---|
| AI 绕过治理 | Orchestrator 将 AI 允许触发限制在 NEED_AI_RESEARCH_DESIGN；AI Design 只写设计工件；Proposal/Candidate/Trial 需要独立治理 | canonical 链路未发现直接绕过；但没有全仓统一 agent action boundary，因此全局“不可绕过”尚未证明 |
| 自动启动 Trial | Predictive executor 依赖结构资格、预注册、budget reservation、授权和显式 start；Candidate Freeze 不自动进入 Structural/Trial | canonical predictive path 受控；legacy backtest/脚本仍是额外执行面，不能宣称全仓绝对禁止自动执行 |
| 修改历史 Candidate | frozen contract、candidate hash、freeze receipt 和 NEW_CANDIDATE 修改检测存在；旧 Candidate 设计为保留 | canonical frozen path 有保护；多套历史 JSON/合同存储仍增加误写和归属风险 |
| 修改 Validation | TrialRegistry、classification correction events 和 FinalResearchAdjudicator 采用 append-only/有效视图思路 | 逻辑上有追加式保护；文件系统多工件没有单一事务提交，恢复/并发时需对账 |
| Budget 绕过 | SearchBudgetRegistryV1 有 reservation、consume、release、identity 和 reconcile；Trial 启动会检查预算 | canonical path 受控；legacy 研究入口、run-level 与 objective-level 多命名空间仍使全仓预算一致性未封闭 |
| Performance 泄漏 | PerformanceBlindGuard、NoOutcome context、AI input flags 和结果盲展示存在 | Evolution AI Design 输入当前是 outcome-blind；但 Trial 详情、legacy 回测和部分报告允许显式读取结果，隔离是上下文约束而非全仓物理隔离 |
| 未来函数风险 | AsOfDataView、PIT tradability、available-at、T+1、数据 gap fail-closed 和 routing policy 存在 | 规范路径具备防护；外部 TDX 文件漂移、online-only 数据和 legacy provider 路径是最高运行时风险 |

总体判断：当前系统的安全设计是“canonical path fail closed + 人工治理 + 账本/合同保护”，还不是“所有入口统一经过一个不可绕过的安全内核”。

## 2. Agent-native 架构审计补充

为便于下一位 AI 工程师接管，按 8 个 agent-native 原则做了一个诊断评分。每项分母固定为 8，是本次交接审计量表，不是产品质量分，也不是测试通过率。

| 原则 | 评分 | 依据 |
|---|---:|---|
| Action Parity | 0/8 | 前端可见的 22 个业务写入/生成/执行动作没有对应的 MCP/FastMCP/机器工具目录；严格 UI-agent action parity 为 0/22 |
| Tools as Primitives | 1/8 | 当前没有第一方 tool registry；严格原子工具评定为 0/8，最接近原子能力的 CodexExecutorV1 仍绑定研究 proposal、staging、schema 和运行时 |
| Context Injection | 4/8 | Proposal、Failure Landscape、Mechanism Registry、lineage 和 data capability 已进入 Evolution Design；严格内容/实际 prompt/新鲜度口径只有 2/6，Factor/Event catalog、runtime freshness 和统一 SafeRuntimeContext 不完整 |
| Shared Workspace | 5/8 | 所有模块位于同一项目根目录，但实际是多源联邦存储；Candidate、Trial、Budget、报告、cache 和 AI staging 有多个路径，旧工件和 stale snapshot 造成一致性问题 |
| CRUD Completeness | 3/8 | 追加式 review、correction、freeze 和 confirm 边界较完整；严格完整 CRUD 为 0/14 个实体，能力单元为 40/56；没有统一机器可读动作模型 |
| UI Integration | 5/8 | Web Console 页面、读模型和本地 POST 动作已接通；状态依赖轮询，实时传播评分为 0/8，但轮询后大部分状态可见约为 7/8 |
| Capability Discovery | 4/8 | API、页面和代码服务可被人工发现；没有正式 capability/tool catalog 供 AI 查询当前可用动作、前置条件和风险 |
| Prompt-Native | 4/8 | manual handoff、AUTO_CODEX、AI policy 和结构化输入存在；核心状态转换、权限和恢复仍主要硬编码在服务/UI 中 |

合计：26/64（约 41%）。这说明 Research Factory 的共享上下文和治理骨架已经存在，但它目前更像“人操作的 governed research console”，而不是“AI 可以安全操作且动作对等的 agent-native research system”。

另外，external_sources/vibe_trading 下存在一套约 64 个工具的外部 MCP 实现；它不属于当前 primary Research Factory 运行时，未经边界审查不能把它当作本系统的治理工具层。

## 3. Web Console 当前能力

当前前端路由和 API 已覆盖：

- /research/evolution/proposals：Proposal 列表、详情、review、close、confirm；
- /research/evolution/ai-design：AI Research Design 展示和状态；
- /research/candidates/proposals：Candidate Proposal review、Freeze Preview、确认冻结；
- /research/objectives、/research/pipeline、/research/candidates、/research/trials、/research/governance：Objective、流水线、Candidate、Trial 和治理读模型；
- Predictive authorization/start/resume、Structural reconcile、contract correction 和 Trial reconciliation 等本地治理接口。

webapp.py 对写接口使用 loopback/testclient 请求限制，前端动作也通过显式 POST 表达。风险在于：前端按钮和 HTTP API 已有动作，但没有与其完全对应的机器工具层；新的 AI 工程师不能仅靠 agent capability discovery 安全地复现这些动作。

状态传播还依赖普通 fetch 和轮询，而不是 SSE/WebSocket/EventSource。不同页面的轮询周期不同，部分 POST 后刷新可能被正在执行的读请求挡住；因此 Console 是“最终可见”，不是实时一致的控制平面。前端也没有发现通用 Agent budget/audit 状态的完整展示。

# 技术债

以下只列会直接影响未来 Autonomous Alpha Research Loop 的关键问题，不列格式、命名和小型重构问题。

## 1. 事实源分裂，恢复不是单一状态读取

Objective registry、AI Design state、Orchestrator checkpoint、daemon snapshot、TrialLedger、SearchBudgetRegistry、ArtifactGraph 和 durable contracts 都可能描述同一研究链路的不同切面。当前已经观测到：

- Evolution Objective registry 仍是 CREATED/HUMAN_REVIEW_REQUIRED，而 AI Design 工件是 AI_DESIGN_READY；
- New Mechanism checkpoint/daemon 信息落后于 canonical TrialLedger；
- daemon budget 视图为 0/6，而 SearchBudgetRegistry 为 2/6；
- 旧 PID 和旧 required_human_ai_action 仍留在 daemon 状态中。

如果不先定义 canonical source-of-truth、版本、提交顺序和 reconcile 规则，自动循环恢复时可能重复执行、错误释放预算或误判治理状态。

## 2. 最新 Evolution 链没有贯通到 Candidate 生产工件

Research Evolution Manager、Proposal Bridge、Proposal Governance 和 AI Research Design 代码已经存在，但最新 Evolution Objective 还没有 Candidate Proposal、Candidate Registry 或新 frozen contract。也就是说，最需要验证的“失败经验 → 新机制 → Candidate”生产路径尚未在当前 Objective 上闭环。

在没有这条受控 lane 的真实工件、hash、拒绝/确认和 restart 证据前，不能把历史 frozen contracts 当作当前 Evolution 已完成。

## 3. Canonical 与 legacy 执行面没有统一封口

旧版 /api/backtest、screener、策略脚本和多代 research artifacts 与 Research Factory 并存。它们对 budget、performance visibility、data route、状态和研究结果的语义不完全相同。

未来 autonomous loop 如果可以从多个入口启动研究，就会出现“规范链路安全，但另一个入口绕过规范账本”的系统级风险。必须明确 legacy 是只读兼容、隔离实验，还是正式研究入口，并把其状态和权限纳入同一策略。

## 4. Candidate 与 Trial 事实源存在分叉

Candidate 生成器写入 Candidate Registry，而 Research Console 和 Orchestrator 主要扫描 durable frozen candidate contracts；审计范围内没有确认一个可靠、单向、带版本的桥接协议。

Trial 方面，canonical batch ledger 与 reports/research_factory/factory_trial_ledger.json 的事件和 Trial 视图也并存。不同消费者可能因此看到不同的 Candidate/Trial 数量和终态。未来自动循环若没有唯一 ledger authority，exact-once 和预算 reconcile 都无法只靠调用方保证。

此外，CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json 和 CURRENT_EFFECTIVE_STRATEGY_REGISTRY_V1.json 是全局历史投影，部分记录缺少顶层 objective_id，控制台在部分场景需要通过 candidate ID 推断归属。这对跨 Objective 的 lineage 隔离构成风险。

## 5. AI 没有等价的可发现治理工具层

前端已经有约 22 个业务写入/生成/执行动作，但当前没有 MCP/FastMCP 或等价的 agent tool registry。AI 只能通过人工 handoff、既有服务约定或外部脚本间接工作，无法获得：

- 当前 Objective-scoped 可用动作；
- 每个动作的前置状态；
- 是否消耗预算；
- 是否需要 reviewer/confirmation；
- exact-once identity；
- 失败后的恢复动作。

在此问题解决前，“自主”只能停留在设计建议和人工交接，不宜开放自动循环。

## 6. 数据供应和可复现性仍受外部目录影响

当前有 PIT guard 和 fail-closed gap，但数据能力依赖本地 TDX/mock 文件、BaoStock 和部分 online-only provider。数据覆盖、文件最新日期、分钟缺口和事件数据持久化状态如果没有在每次研究前冻结并验证，会让相同 Objective 在不同机器或不同日期得到不同可用数据集合。

这不是普通数据清洗问题，而是 Autonomous Loop 的研究身份、可复现性和未来函数安全问题。

## 7. 文件型多账本提交和恢复复杂度高

系统大量使用 JSON、Parquet、SQLite、ledger、checkpoint、receipt 和 report 文件。各模块已经实现了不少 append-only、hash 和 idempotency 约束，但跨文件的整体提交仍不是一个统一事务。

因此，进程在 Candidate Freeze、Budget Consume、Trial Complete、Final Adjudication 或 Evolution materialization 中途退出时，需要依赖 reconcile，而不是一个原子 transaction。未来自动循环必须把“提交协议”和“恢复协议”当成一等设计。

## 8. Validation 到 Evolution 的自动闭环尚未被当前运行态证明

FinalResearchAdjudicator、FailureKnowledgeAdapter、Evolution Manager 和 Proposal Bridge 已实现，但当前没有证据表明最新 Evolution Objective 已经从 AI Design 自动或半自动地进入下一批 Candidate。当前系统具有闭环组件，尚未具有经过当前事实源对账和端到端恢复验证的闭环运行证据。

## 9. 研究实体只有受控追加/终结，没有统一的交接 CRUD 语义

系统有意采用 immutable contract 和 append-only ledger，这对研究审计是正确方向，不应简单改成自由编辑。但目前不同实体的 create/read/review/close/correction/reconcile 语义分散在服务、CLI 和文件工件中：

- API 主要只有 GET 与 POST，没有统一的实体级 action schema；
- Proposal 使用固定 canonical 文件路径，版本集合不是一等资源；
- Validation、Final Decision、Lineage 多数通过 Trial/report 组合读取；
- 没有统一的 archive/retire/revoke 查询和机器动作模型。

这会增加新 AI 工程师接管时的误操作和状态解释成本，也阻碍未来 capability discovery。

## 10. Console 状态传播不是实时控制面

Console 当前通过轮询获取 Orchestrator、daemon、AI runtime、governance 和 Trial 状态，没有统一事件 cursor、SSE/WebSocket 或跨页面共享 store。状态更新可能在下一次轮询后才可见，异步 pause/stop 的 acknowledged 与实际生效状态也可能分离。

对于人工治理尚可接受；对于未来 autonomous loop，它不足以作为唯一的实时安全观察面。

# 下一阶段路线

## Phase 1：先统一事实源和恢复边界

目标是让接管人能够判断“现在到底是什么状态”，不产生新的研究任务。

建议交付：

1. 明确每类事实的 canonical source：Objective、AI Design、Candidate、Trial、Budget、Validation、lineage 和 governance 各自只认一个规范来源。
2. 为 Objective 建立 objective-scoped reconcile report，显式标出 registry、orchestrator、daemon、ledger、budget、artifact graph 的版本和冲突。
3. 处理 orphan/legacy Candidate 与旧 Objective 的归属：保留历史，不把它们混入当前可运行库存。
4. 让 daemon checkpoint 只做恢复提示；恢复前强制 reconcile canonical TrialLedger、SearchBudget、ArtifactGraph 和 frozen contracts。
5. 冻结当前数据 capability、provider、cutoff、manifest 和已知 5m gap 的判断规则。
6. 建立与 Web Console 动作等价的 agent capability map，先只读和治理预览，不开放执行。
7. 建立统一、版本化、带 source hash 和 freshness 的 SafeRuntimeContext；至少覆盖 factor/event capability、daemon health、budget、current required action 和当前 Objective lineage，且继续保持 outcome-blind。

完成标准：任何接管 AI 都能在不启动任务的情况下得到一个一致的 Objective 状态、预算状态、Candidate/Trial inventory 和可执行下一动作。

## Phase 2：完成一条受控的人工研究 lane

以当前 Evolution Objective 为对象，按真实工件逐步验证：

~~~text
AI Design confirmation
  → Candidate Proposal
  → Candidate human review
  → Freeze Preview
  → Candidate Freeze
  → Structural Preflight
  → Predictive Authorization
  → Predictive Trial
  → Validation / Final Adjudication
  → Failure / Evolution
~~~

这一阶段重点不是提高搜索速度，而是证明：

- approve、reject、freeze、authorize、start 都 exact-once；
- restart 不重复创建 Candidate、Trial 或 budget reservation；
- hash、lineage、contract 和 testing family 在每一层一致；
- Preview 不消耗预算，只有明确的最终动作才改变规范账本；
- Trial 不会自动跨越人工治理边界；
- Validation correction 是追加式；
- 失败结果可以再次生成 outcome-blind Proposal；
- 当前实际 AI input 与所有 handoff/backend 入口使用统一的结果隔离策略。

完成标准：至少一条当前 Objective 的端到端工件链可被另一个工程师从磁盘恢复，并能解释每次状态变化的证据。

## Phase 3：受限的自主研究编排

只有 Phase 1 和 Phase 2 通过后，才建议开放受限自动化：

- AI 只负责 outcome-blind 的研究设计和候选意图提案；
- Candidate Freeze、Structural Preflight、Predictive Authorization 和 Final Validation 保留显式治理；
- 每个 Objective、testing family、budget bucket 和 Candidate identity 都有硬上限；
- 自动化只能在允许状态触发，治理、预算、数据完整性和安全停止优先；
- 所有自动动作都通过可发现的 agent tool/API，工具返回前置条件、结果、receipt 和恢复提示；
- 不开放 Final Test、prospective、real order 或不可逆外部动作；
- Console、agent tool 和后台事件共享同一 objective-scoped read model 与事件 cursor。

## 什么时候适合做 Autonomous Alpha Research Loop V1

现在不适合直接实现或开启 Autonomous Alpha Research Loop V1。

必须至少满足以下条件后再进入：

1. Evolution Objective 的 registry、AI Design、Candidate、Trial、Budget 和 lineage 状态统一并通过 reconcile；
2. 当前 Evolution 链真实生成 Candidate Proposal、Freeze Preview 和 frozen Candidate；
3. Freeze、Structural、Predictive、Validation、Failure/Evolution 的 exact-once 和 restart recovery 有当前工件证明；
4. agent action parity 不再是 0/22，或明确提供覆盖所有治理动作的可发现工具层；
5. 数据 provider、cutoff、PIT、5m gap、online-only 能力和 manifest 在运行前全部冻结；
6. legacy 入口被隔离、降权或纳入同一治理和预算内核；
7. 没有未解释的 checkpoint/daemon/ledger/budget 冲突；
8. 保留人工暂停、安全停止、拒绝、关闭和恢复入口；
9. AI context 统一经过字段级、方向性 reason code 和 freshness 校验。

当前最小下一步不是启动新研究，而是先完成 Phase 1 的“事实源与恢复对账”。在此之前开启 Autonomous Loop，会把当前已有的状态分裂和数据不确定性放大成重复 Trial、预算错误或不可解释的研究结果。

## 交接建议

新 AI 工程师接手时，建议按以下顺序阅读和操作：

1. 先读本报告、根目录 CLAUDE.md 和 pyproject.toml，不要把根目录 legacy README.md 当成 Research Factory 的完整架构说明。
2. 再读 webapp.py、research_console.py、autonomous_orchestrator_v2.py、research_daemon.py，理解控制状态与事实账本的区别。
3. 然后按 Objective ID 检查 Objective registry、AI Design、durable contract、TrialLedger、SearchBudgetRegistry 和 ArtifactGraph。
4. 任何恢复或执行前先生成对账报告；在状态冲突未解决前，不创建 Objective、Candidate，不授权或启动 Trial，不调用 AI。
5. 只在 Phase 1 对账完成后，继续当前 Evolution Objective 的人工治理链。
6. 对当前 Web Console 的人工动作，先记录其 API 前置条件、副作用、审计字段和 exact-once identity，再决定是否工具化。

本报告的定位是当前工作区的接管基线；它不替代运行时 reconcile、数据 manifest 校验或正式测试。
