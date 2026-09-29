# 有限任务与生命周期运行

生命周期任务把已有研究、数据采集、验证、Paper、组合和每日计划服务接到同一个有限运行入口。任务本身不批准研究、不增加原会话预算，也不授予策略资格。

创建任务时固定已有目标和会话、原配置身份、任务类型、到期时间、调用次数上限，以及每个阶段允许执行的时间窗口。创建和启动只改变任务状态，默认不会启动后台进程；控制台、CLI 或已授权的调度宿主显式调用 `tick` 推进一步。

## 用户能看到的状态

| 状态 | 含义 |
|---|---|
| `CREATED` | 已登记，尚未启动 |
| `READY` | 可以检查下一阶段，不表示已经有交易资格 |
| `WAITING_WINDOW` | 尚未到阶段时间；不会提前采集 |
| `WAITING_DATA` | 原服务还缺数据 |
| `WAITING_QUALIFICATION` | 原服务尚未准许策略进入下一阶段 |
| `BUDGET_EXHAUSTED` | 原业务服务的研究额度已用完 |
| `CALL_LIMIT_EXHAUSTED` | 本调度任务的有限调用次数用完，不能借此增加研究预算 |
| `PAUSED` | 用户暂停；不执行或自动恢复副作用 |
| `EXPIRED` | 到期，不启动新阶段 |
| `RECOVERY_REQUIRED` | 有开始记录却未保存最终回执，只能核对原服务 |
| `COMPLETED` | 所有指定阶段得到完成回执；不代表策略有效 |
| `COMPLETED_WITH_ISSUES` | 存在错过时段或工程失败，详细原因保留 |
| `ADAPTER_NOT_BOUND` | 当前部署没有绑定该固定业务服务 |

遇到缺数据或资格不足，至少等待 60 秒再查询原服务，不创建新的模型调用。读取状态不会启动任务、恢复账本、创建目录或调用采集服务。

## 时间与证据

真实任务使用本机当前 UTC 时间，不接受调用方伪造时钟。测试时钟只能用于明确标记的 `SYNTHETIC` 任务，合成回执不能作为真实采集或资格证明。

采集、Paper 和每日计划的阶段必须绑定明确的交易日，且该日存在于调用方冻结的交易日日历。采集窗口必须在同一上海时区日期内。调用方负责从可信日历确定实际交易日和开收盘窗口，任务系统不以周一到周五猜测交易日。

错过一个阶段窗口就记录 `MISSED_WINDOW / NO_BACKFILLED_OBSERVATION`，不在电脑重新开机后补造“当时收到”的 OPEN 或 CLOSE。已经超过整个任务到期时间时，状态显示到期，未执行阶段不会被伪装成完成。

## 中断与恢复

每次调用业务服务之前，先保存阶段 `STARTED`、唯一 `operation_id` 和已占用调用次数。业务服务返回的完成或失败结果必须引用它自己的回执。

进程在服务调用前后中断，下次只能调用该服务的 `reconcile` 核对同一操作。原服务无法证明结果时，保留 `RECOVERY_REQUIRED`；不重新调用 `execute`，不退款、不另建阶段进行免费重试。已经完成的阶段重复触发只读取既有结果。

任务状态提交使用现有 `_put/_read` 校验与原子替换。若崩溃留下已准备好的下一状态，写入入口只接受前状态哈希、配置身份和序号完全匹配的接续；只读状态显示恢复需求，不自行落盘。并发写入复用 `ObjectiveMutationLock`，没有新线程锁协议或 PID/超时接管。

到期和调用次数耗尽后，仍允许对已经开始的操作读取并保存原服务的既有结果；这不是新增运行权限。用户暂停时不自动核对，需要恢复后再推进。

## 服务接入契约

`LifecycleJobsV1(root, dispatchers=...)` 的服务映射由部署代码注入，只接受固定类型：`CAPTURE_OPEN`、`CAPTURE_CLOSE`、`RESEARCH`、`VALIDATION`、`PAPER`、`PORTFOLIO`、`DAILY_PLAN`。任务文件不能包含 shell 命令、Python 源码、模块路径或动态函数名称。

固定适配器实现三个方法：

1. `readiness(config, stage)`：只读核对原会话、授权、预算、数据用途和资格，返回 `{status, reason}`。状态只允许 `READY`、`WAITING_DATA`、`WAITING_QUALIFICATION`、`BUDGET_EXHAUSTED`。
2. `execute(config, stage, operation_id)`：调用原服务的合法入口，仍由原服务二次检查授权和预算。返回 `{status, receipt_ref, reason}`。
3. `reconcile(config, stage, operation_id)`：只对原操作核对或执行原服务允许的恢复，不能另建新试验。返回相同格式。

后两个方法允许 `COMPLETED`、`FAILED`、`UNRESOLVED`。前两种必须有原服务 `receipt_ref`；`UNRESOLVED` 保留原开始记录。适配器不能用人工填入 `COMPLETED` 代替真实回执验证；业务资格继续从原权威服务读取。

任务 `scope_ref` 固定包含 `objective_id`、`session_id`、`scope_hash`，另有原业务 `config_hash`。它们是引用，不是新的授权凭证。新目录或新任务编号不能绕过原服务对相同研究目标的跨批预算和确认次数限制。

源码身份随任务冻结。源码变动后仍可只读查看和暂停，但启动、恢复和推进拒绝沿用旧运行身份；升级需要明确接续方案或保留原部署，不能关闭源码校验。

## 使用与验收边界

典型调用顺序是 `create → start → tick → status`，需要暂停时使用 `pause`，继续使用 `resume`。每个 `tick` 最多执行或核对一个阶段，调用方根据 `next_check_at` 决定后续检查；这里没有无限循环或自行注册定时任务。

测试使用显式合成时钟和 fake 服务，覆盖等待不耗额度、双触发、中断核对、缺回执阻断、过期、暂停、错过窗口、源码变化与只读不写。最终测试结果和真实服务接通证据由整计划统一交付报告记录。单独通过这些测试不证明真实后台长期运行，更不证明独立验证或 Paper 已经积累足够观察日。


## 公共固定入口与DIAGNOSIS_V3接手

固定规则的无AI入口为 `scripts/run_trusted_research_v1.py`，顺序为 `capabilities → preview → freeze → approval → approve → start → status`；参数示例见[用户说明](AUTONOMOUS_RESEARCH_USER_GUIDE.md)。能力说明用 `python scripts/generate_research_capabilities_v1.py --check` 比对同源生成内容并逐项调用公共preview，未读取行情或授予执行许可。当前S1仍缺B验收，CI预检不能替代真实账户验收。

现有有限任务CLI仍为：

```powershell
python scripts/run_strategy_lifecycle_v1.py lifecycle-preview --workspace-root <工作区> --bindings <绑定配置.json>
python scripts/run_strategy_lifecycle_v1.py lifecycle-create --workspace-root <工作区> --bindings <绑定配置.json> --config <有限任务.json>
python scripts/run_strategy_lifecycle_v1.py lifecycle-start --workspace-root <工作区> --bindings <绑定配置.json> --job-id <任务ID>
python scripts/run_strategy_lifecycle_v1.py lifecycle-tick --workspace-root <工作区> --bindings <绑定配置.json> --job-id <任务ID>
python scripts/run_strategy_lifecycle_v1.py lifecycle-status --workspace-root <工作区> --bindings <绑定配置.json> --job-id <任务ID>
```

`DIAGNOSIS_V3` 还要求维护者在Python部署层通过 `LifecycleServiceV2(..., research_services={绝对研究根: 已构建的DiagnosisResearchV3实例})` 注入服务。普通JSON绑定和上述CLI本身不会创建该映射，也不能从候选传入模块或供应商对象。缺少映射时返回 `LIFECYCLE_DIAGNOSIS_V3_DEPLOYMENT_REQUIRED`。切换操作者必须复用原campaign授权、submission服务、预算事件和研究根；读取 `status()` 的进度与原因后再决定是否继续，不能新建目录重置额度。

维护者可将同一个生命周期服务交给 `TrustedResearchHostV1(service)`，用 `run(once=True)` 推进一轮、`run()` 运行受已有任务到期时间与额度约束的宿主，通过 `status()` 和 `stop()` 查询或请求停止。这里是现有Python部署API，没有额外的宿主CLI命令。宿主共享单实例锁；页面刷新与状态查询不会启动它。诊断V3每次 `tick()` 至多推进一个未完成候选，已完成记录只校验和恢复结算，从而给其他Paper任务留下下一轮调度机会。

DATA、VERIFY和账户分别保留受限进程资源凭证；未知模型请求、超时worker或缺失结算不能自动重派。需要恢复报告时核对原账户结果、结算及证据后走原固定报告阶段，不重新执行账户。

真实模型自动模式目前失败关闭：可信适配器必须通过 `enforce_budget_limits` 安装并证明token与费用上限，默认Codex适配器尚不满足。`DIAGNOSIS_MODEL_HARD_BUDGET_UNVERIFIED` 表示部署保障缺失，不能通过填写说明字符串、提高额度、替换目录或把合成适配器用于真实调用来解除。单次真实模型smoke、固定策略运行及合成循环测试分别保留证据，不声称已经完成默认无人研究部署。

## 公共 V3 档案与 Paper 接线（U13）

公共 `JOB.json` 的 V3 策略可以通过 `PublicStrategyArchiveV3.freeze(job_path, candidate_id)` 冻结为 `PS_` 档案。入口先调用公共 `verify_job_evidence`，必须取得完整原件绑定与离线核账 PASS，再保存规则、计划、原始输入、完整结果、授权/执行/结算/索引/预算证据。它不接受调用方提交“已合格”标志，不把历史收益或核账 PASS 升成正式资格。原公共作业及源码归档应保留；持久档案包含经济核验和读取策略所需资料，原作业仍是完整来源复查入口。

```python
from chanlun_trader.research_factory.public_strategy_archive_v3 import PublicStrategyArchiveV3
archive = PublicStrategyArchiveV3(archive_root)
frozen = archive.freeze(job_path, candidate_id)
review = archive.review(frozen['strategy_id'])
```

`ForwardPaperSessionV1` 与 `PortfolioQualificationV1` 按固定身份前缀选择档案服务：原 `BS_` 路径继续使用旧类，`PS_` 使用新类；混合档案版本组合明确拒绝。Paper 的参数、五万元资金、股票池和持仓限制仍显式冻结在策略规则与观察 policy 中；三类风险退出复用同一 `ForwardPaperEngineV1` 与退出适配。

`ENGINEERING_OBSERVATION` 只表示执行工程观察。新档案的正式观察仍缺 canonical 方法支持和独立确认，不会创建真实合格策略。合成输入或测试凭证被档案保留为 `SYNTHETIC`，不能用于 `REAL_OBSERVED`；来源不足为 `UNVERIFIED` 并拒绝准入。方法开发报告仅解释不支持，不能拿来当资格证。旧档案不迁移、不改写、不自动晋级。

Paper 原有实时时钟、自然日期、迟到/漏采、重复快照、来源指纹、撤销和持久重放门继续适用。策略档案撤销使新增风险准入关闭；已有账户和记录保留，继续依照冻结退出规则处理。合成回放的 `real_observation_days` 与 `qualified_observation_days` 永远为零。机器离线错过采集不得用后来下载的行情补成真实观察。

新增 `tests/research_factory/test_public_strategy_archive_v3.py` 检查公共档案证据篡改、重开、正式/真实合成隔离、撤销，以及三类退出逐日现金、完整成交、退出状态一致性；在 OPEN 阶段原件已落盘而 HEAD 未提交时模拟崩溃，重开后同一快照只补提交、不重复成交。全局源码指纹测试使用实际指纹；并行修改源码导致 `FORWARD_PAPER_SOURCE_CHANGED` 属于预期拒绝，需要在稳定源码上完成测试，不绕过生产检查。

```powershell
$env:PYTHONPATH = 'src;.;tests/research_factory'
.venv/Scripts/python.exe -m pytest tests/research_factory/test_public_strategy_archive_v3.py tests/research_factory/test_rule_paper_parity_v2.py tests/research_factory/test_forward_paper_process_recovery_v1.py -q --basetemp reports/u13_verify_tmp
```

目前无真实合格 V3 策略，真实 Paper 自然观察验收仍等待方法和资格，工程接线不能宣称真实 Paper 已完成。回滚停用新公共档案入口并还原 Paper/组合的档案路由，保留所有 `PS_` 原件、观察阶段和撤销记录；原 `BS_` 路径保持可用。

公共档案和只读数量计划也可通过固定 CLI 调用：

```powershell
python scripts/run_strategy_lifecycle_v1.py public-freeze --archive-root <archive> --job-path <job>/JOB.json --name <candidate>
python scripts/run_strategy_lifecycle_v1.py review --archive-root <archive> --strategy-id <PS_id>
python scripts/run_strategy_lifecycle_v1.py revoke-strategy --archive-root <archive> --strategy-id <PS_id> --reason <reason>
python scripts/run_strategy_lifecycle_v1.py daily-plan --root <paper>
```

这些入口只分派固定服务，不接受模块名或资格布尔值；`daily-plan` 不下单、不改账本。新增 CLI 分派四项测试已通过，完整 Paper 身份敏感测试须在源码稳定后执行。

### 发布证据与Windows资源边界

`python scripts/run_trusted_research_v1.py publication` 只核对仓库内发布包元数据，不需要部署配置、不读取行情、不运行账户；显示的核验范围是3规则×2池×5万元，不能授予策略资格。源码或凭证变化会显示未验收，执行能力与提交身份不会因新增发布凭证改变。

Windows账户和VERIFY worker默认最多2个进程。固定DATA worker最多3个，用于venv启动器、解释器和Git版本查询；共享总内存上限仍为2048 MiB，时间仍由原授权限定。Linux未实施同一进程数量限制，会在资源凭证中明确标注，不能把声明值当作已强制限制。
