# 自主量化研究系统总体规划 V1

> 文档性质：工程与产品路线规划，不是研究执行授权、交易授权或新的 canonical 状态源。
> GitHub：a1351995160/trade
> 本次重新核对的 main 基线：7f22237958731fbc5e7803ee41ff40a55a277ff1
> Phase 2 已合并；本规划不代表 Phase 3 或后续功能已经实现。
> 本次交付只生成规划与提示词，没有修改远程仓库，没有执行项目或访问真实研究数据。

## 1. 产品终点

主线是：

研究目标 → AI 提出因子/机制/假设 → 可执行候选 → 严格验证 → 最终裁决 → 失败知识回流 → 下一轮研究 → 合格策略库 → 多策略每日选股与持仓计划。

AI 负责提出方案，系统负责可执行性、数据权限、预算、统计验证与状态一致性；用户决定研究授权范围和真实交易。近期不接真实 Broker，不以自动下单作为系统完成标准。

保持 A 股短周期研究方向。此前讨论的 2–10 个交易日和约 1 万元小资金执行可行性是业务参考；实施时以当前冻结 Objective、Candidate 和政策为准，不能因本规划擅自改写参数。

产品必须允许“本轮无合格策略”和“今日不交易”。工程验收通过不等于发现 Alpha，发现研究证据不等于获准投入使用。

## 2. 基线与可信范围

GitHub Source Baseline 文档明确说明：仓库是精简源码基线，不是原始工作区的镜像。行情、真实 Objective/Trial/Performance、AI staging、缓存和其他运行产物被排除。因此：

- 源码中有某个模块，不代表在当前环境可以完整运行。
- CI 通过不代表所有本地集成测试、真实数据覆盖和当前运行态都通过。
- 历史文档的 READY、策略数量和数据缺口不能直接当作当前 canonical 状态。
- 每个工作包必须绑定实际 BASE_COMMIT、验证命令、运行环境和证据。

此前区分的两个本地目录必须保持分离，并以 git remote、HEAD 和目录用途再次核对：

| 用途 | 已知目录 | 默认规则 |
|---|---|---|
| GitHub 开发仓库 | E:\llmwiki\chanlun-trading-system-github-v2 | 在独立分支或干净 worktree 开发、测试 |
| 原始真实研究工作区 | E:\llmwiki\chanlun-trading-system | 不作为测试根目录，不修改，不复制研究运行产物进 Git |

只有一个目录名称相似，不足以证明它是正确工作区。不得对 dirty 工作区使用 reset --hard、clean -fd 或覆盖文件来凑出干净基线。

## 3. 架构原则

### 3.1 复用，不再平行造系统

优先复用现有数据 Provider、ResearchDataRouter、BacktestEngineV2、Candidate 合同、TrialLedger、SearchBudget、统计裁决、策略注册和控制台。允许为明确缺口新增窄接口或小模块；不新增平行账本、第二套研究权威或全新总控制器。

状态适配层可以统一术语，但事实仍来自既有 canonical artifacts。Daemon 和 Orchestrator 可以保留调度、进程监督、心跳和等待逻辑；同一受管理 Objective 的研究授权与副作用执行必须一致。

### 3.2 开发、测试、研究、使用四类许可分离

批准开发某个功能，不等于批准它访问真实数据或执行真实 Trial；批准运行 Trial，不等于批准 Final Test、向前观察、推荐或订单。

合成环境不是“所有权限自动通过”。它只是独立的数据根目录，仍要经过真实治理服务和权限校验。模式或环境变量只能收紧能力，不能凭一个字符串创造授权。

### 3.3 幂等与恢复的语义分离

查询历史 COMPLETED receipt，可以返回经过完整性校验的历史执行事实；不能把该事实当作当前继续执行的许可。

新副作用必须经过当前 canonical reconciliation、上下文和权限检查。恢复旧 STARTED 时，要在把旧动作简单判定为 stale 之前识别同身份的已发生副作用，避免破坏 Phase 2 的恢复语义。

### 3.4 主线与旁线并行

Paper/Prospective 观察是研究成果的证据链，不是所有架构开发的前置条件。可以并行开发模拟接口、每日计划界面和研究能力；真正使用封存期或向前数据、运行 Paper、发布可执行计划时，必须通过各自权限和数据门禁。

## 4. 工作包总览

以下编号是本规划的交付编号，不修改代码中历史 Phase/Stage 编号。

| 编号 | 目标 | 主要性质 | 前置条件 | 验收终点 |
|---|---|---|---|---|
| P3-A | 执行策略、工作区与 Web 启动隔离 | 最小改造 | 固定 Phase 2 基线 | 打开控制台不自动执行；合成测试不会串到真实 root |
| P3-B | 公共重启恢复、Action 校验、dry-run 与跨入口互斥 | 修复与整合 | P3-A | 新进程只凭 root + Objective ID 恢复；不重复、不越权 |
| P3-C | 自包含全生命周期认证 | 集成测试与必要补丁 | P3-A/B | 多 tick、人工闸门、跨进程重启均正确，停在预测边界 |
| R1 | 源码运行依赖闭包与候选数据就绪 | 核验与补齐 | 静态盘点可与 P3 并行；真实访问另行授权 | 干净环境能加载正式路径，指定候选的数据身份/覆盖可复现 |
| R2 | 单候选正式研究闭环 | 接通已有正式服务 | P3-C、R1，以及具体研究授权 | 一个 Trial 从授权到最终裁决、预算结算、失败记录可追溯 |
| R3 | 授权范围内的有限自动研究 | 授权契约与编排集成 | R2，以及单独批准的批次授权模型 | 在预算和范围内推进，异常/耗尽/暂停时停止 |
| D1 | 合格策略到每日选股/持仓计划 | 产品化衔接 | 接口可提前开发；使用需策略与数据准入 | 计划可追溯到冻结规则；无资格时 NO_TRADE/NOT_READY |
| D2 | Paper/向前观察与回测语义一致性 | 复用、适配与验证 | 冻结策略、获准数据窗口、明确运行授权 | 同一策略按实际到达数据运行，逐日信号、成交与持仓可对账 |
| M1 | 多策略组合选股平台 | 条件性业务交付 | 多个合格策略及组合级契约 | 冲突信号、风险预算、重叠持仓与组合表现有统一规则 |

可观测性、依赖管理、备份恢复、隐私、停止按钮与 CI 是贯穿事项，不另造一个无限扩大的平台工程阶段。

## 5. Phase 3 的三个有限交付

### P3-A：执行隔离与启动安全

当前 webapp.py 在模块层构造多个 PROJECT_ROOT 服务，startup 调用 predictive、reauthorization、proposal、AI design 和 candidate 的恢复方法。先把“创建/启动应用”和“执行恢复任务”分开。

需要：

- 可显式绑定 research_root 和 execution_policy 的应用/服务组合，保留必要导入兼容。
- 默认只读，不因 import、startup、普通 GET 或读模式构造而恢复研究、启动进程或改业务文件。
- 现有人工治理动作在明确模式、真实确认和领域验证下继续可测试，不删除功能来实现表面安全。
- 只读模式拒绝既有 Web 写入口、CP tick 写模式、legacy backtest 等有副作用入口；只读 inspect 与真正无写 dry-run可以保留。
- 合成测试只能使用临时工作区；共享服务不得回退到模块级真实 root。
- 执行策略在实际调用前生效，环境变量不能代替实现。

停止点：只认证 P3-A；不修改全系统 planner、共享 lease 和 Phase 2 crash 语义，不启用真实研究。

### P3-B：真实重启路径与并发恢复

必须证明重启者不需要持有旧 Action 对象。仅给 root 和 Objective ID，经公共入口即可识别未完成执行意图，并判断同身份完成、未执行可重试、stale 或冲突。

重点：

- 不把恢复寄托在测试继续保存的 Python 对象上；意图与身份信息要可持久重建。
- Action、上下文、必需能力和 expected identity 由受信数据验证或重新规划；hash 不是外部授权凭证。
- direct execute_action(dry_run=True) 的所有路径不写 journal，包括 recovered COMPLETED 路径。
- 完成回执查询不产生执行授权；journal 缺失不能成为免费重试依据。
- 同机多进程下，为相关 canonical mutation 建立共享互斥边界，明确 owner、锁顺序、嵌套调用和恢复策略。
- 不凭 TTL 到期就让第二个仍可能与旧执行者并行的进程接管；优先复用成熟的单机锁机制，暂不承诺跨主机分布式 exactly-once。
- 损坏、半写和 identity 冲突保留证据并 fail closed，不静默清空后重跑。

### P3-C：生命周期 E2E 与认证

在自包含 synthetic Objective 上经过真实服务组合，从设计与确认、候选、冻结、物化、显式 Structural 到 Predictive 授权边界。人工动作由测试通过真实治理 API/服务提交，不通过篡改 receipt 造成功状态。

覆盖 multi-tick、wrong identity、stale context、两进程竞争、进程强制退出、投影删除、日志损坏、结果字段注入、Web/CLI/CP 一致性。

Stage-level 停止点是预测执行仍被禁用。合成 Structural provider 的显式调用与真实 Structural 自动启动分开计数；合法合成测试执行不应为了统一写 0 而伪造计数。

## 6. R1：源码闭包与数据基线

先验证正式执行路径的源码依赖，再验证数据。动态 import、scripts/helper、配置/schema/政策模板缺失属于源码或部署闭包问题，不能一概归因“本地数据没带”。正式 runner 的加载测试不应依赖把源工作区加入 sys.path。

对目标候选提供如下清单：字段 → Provider → 可用窗口 → available-at → 版本/hash → 覆盖/缺失 → 合法用途。

复用已有 TDX 7709 Provider；无需重写已经固化的公网分页与故障切换。历史可获取长度要以当前证据为准，不把“约 700 天”写成永远成立的供应商保证。在线可查也不等于具备历史 PIT 数据。

冻结数据版本、交易日历、股票池、复权和公司行动语义，区分“数据未知”和“确认无事件”。先补 Guard 的未知状态与数据完整性，再按目标候选需要实现公司行动账务。

R1 不得移动 Final Test 日期来取得更多数据，也不得把新下载的数据自动并入既有冻结集。原数据集保持不变；新增版本与用途必须显式声明。

## 7. R2：一个正式研究闭环

建议首次验证范围为一个冻结候选、一次明确授权的正式 Trial。该范围是未来建议，不是本规划给出的执行许可；预算与候选身份由既有 canonical 记录决定。

复用既有 authorization → start → reservation → PerformanceAccess → engine → TrialLedger → multiple testing/final adjudication → registry → failure/evolution 链路。

验收过程正确，而非必须正收益：REJECTED 也可以是有效的工程结果。访问绩效前后的失败分开处理，重启不得免费重测，最终分类必须经过正式裁决。测试家族、分母与适应性搜索的控制规则应预先锁定，不能看到结果后修改。

## 8. R3：有限自动研究

目标是用户不必每个 tick 都操作，同时 AI 不获得改治理规则或自我授权的能力。

先核对并复用已有 AI backend、候选去重、家族多样性、调用预算与失败知识模块。新增的是明确的批次授权契约：Objective、允许动作、数据窗口、候选/Trial/批次数、AI 调用/token/cost 上限、资源上限、到期/撤销和停止条件。

默认保留现有逐项人工闸门。是否允许部分动作在人工批准的批次范围内执行，必须作为单独治理变更批准；严禁 auto_approve、自动扩预算、降低阈值或不断重试直到 PASS。

设计 Agent 不接触精确收益曲线和绩效数值来调参；统计/评估模块在自身授权边界内处理这些信息，向设计流返回经过既有政策允许的结构化信息。

## 9. D1、D2、M1：把研究转成可使用产品

### D1 每日计划

不是把 experimental watchlist 的空 production_candidates 改成股票名单。应由使用资格明确的冻结策略驱动同一套 signal/exit semantics，并结合当日数据质量、PIT 股票池、持仓、现金和可卖数量输出观察/买入计划/持有/退出/不交易原因。

模式与标签分清 EXPERIMENTAL、PAPER 和可供用户采用的计划；未获准时保持 NO_TRADE/NOT_READY。计划有版本、来源与时间，禁止用历史回填伪装当天生成。

### D2 向前观察

先核对原工作区已存在的 Shadow/Paper 实现，再复用并补当前候选适配。不同候选和事件的观察证据不能混用。Paper 在明确授权后才能运行；它不阻塞研究架构的持续开发。

研究封存窗口与向前观察窗口必须分离；不得通过新 reader 绕过既有 Final Test 禁令。重叠时先做治理决策，而不是默认换名即可访问。

### M1 多策略平台

这是最终产品主线的一部分，不要求现在就拼出组合。只有多个候选满足相应准入，才设计组合权重、风险预算、重叠持仓、信号冲突、容量和换手控制，并冻结组合级政策。

策略数量不足时系统仍应完整可用：清楚报告无合格组合，而不是把 PROMISING 自动升级。

## 10. 贯穿的运行与维护要求

默认显式启动、可暂停、可停止。区分进程 alive、任务 progressing 和 research state；UI 显示当前 Objective、动作、等待人类原因、预算、恢复状态和最后异常。

记录代码/数据/合同/政策版本，建立结构化日志、资源限制、备份恢复与回退验证。真实研究环境不由 GitHub Actions 部署或启动；CI 只运行隔离测试。

安全计数来自调用探针或真实账本对账，说明范围。没有读取真实工作区时不得宣称“独立确认所有历史计数为 0”。

## 11. 文档、证据与变更治理

建议保存主规划为 docs/AUTONOMOUS_RESEARCH_MASTER_ROADMAP_V1.md，progress.md 只链接工作包和证据，避免复制十份状态。

每个工作包包含：目标、前置条件、允许改动、明确不做、验证矩阵、当前状态与下一交接。

推荐工作状态：PLANNED → IMPLEMENTING → TESTED → INDEPENDENT_REVIEW_PENDING → ACCEPTED/MERGED。实现者不得自己把整个 Phase 宣布 CLOSED。

每次代码交付使用独立分支。SHA 改变必须对应新的测试/CI证据。区分 LOCAL_TEST、CI_TEST、COLLECTED、PASSED、SKIPPED、DESELECTED；不能把 collect 当 pass，也不能把 skip 加入 pass。Windows 与 Ubuntu 数量不同应记录原因，不为数字一致修改真实统计。

源码合并不自动触发真实研究。完成后续功能所需的权限变更必须另有明确决策。没有新增证据的反复审计不是进度；每个工作包有有限的验收终点。

## 12. 当前下一项

### 2026-09-09：Phase 3 工程收尾与 R1

用户已完成独立最终差异复核并确认 PR #5 普通 merge。实际 origin/main 为 `1f6f29c7ad3e8d9371168dfa3bd5201723b48abd`，第二父提交为 P3-C 最终认证 HEAD `7556b3d1e800b22df8e8645a010466c20126810a`。PHASE3_ENGINEERING_CERTIFICATION_CLOSED=true，仅限既定 synthetic 入口和 Windows/Linux 平台。真实运行态仍 NOT_VERIFIED，不授权研究执行。

当前工作项为 R1；独立分支 `codex/r1-source-closure-data-readiness-v1`。源码闭包缺原 corrected/legacy 实现，状态 BLOCKED_MISSING_SOURCE；只读合成数据核验及未覆盖范围见 [R1 交付说明](R1_SOURCE_CLOSURE_AND_DATA_READINESS_V1.md)。R1_FULLY_CLOSED=false，不开始 R2。以下保留此前 P3-C 状态，作为历史证据。

NEXT_WORK_PACKAGE = P3-C
GOAL = Self-contained Synthetic Lifecycle Certification
IMPLEMENTATION_SCOPE = P3-C only
P3_A_STATUS = MERGED via PR #3 (7e277dbe1d20061fd672cf53d1358d07f16a0b1b)
P3_B_STATUS = MERGED via PR #4 (e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08)
P3_B_CERTIFIED_HEAD = 75ec0be63a46d10bce6c5d3766e74b46cb63e0a8
P3_C_STATUS = BRANCH_CERTIFIED_AWAITING_INDEPENDENT_REVIEW (实现 c37fd46；P3-C run 34301691534 双平台各 84 passed；未合并 main)
OTHER_PACKAGES = PLANNED, NOT AUTHORIZED TO EXECUTE
REAL_RESEARCH_AUTHORIZED = NO
PREDICTIVE_EXECUTION_AUTHORIZED = NO
FINAL_TEST_AUTHORIZED = NO
REAL_ORDER_AUTHORIZED = NO

P3-B 的历史实现与验证范围见 [PHASE3B_RESTART_RECOVERY_V1.md](PHASE3B_RESTART_RECOVERY_V1.md)。P3-C 的用户批准、历史失败证据、完整语义实现及认证进度见 [PHASE3C_LIFECYCLE_CERTIFICATION_V1.md](PHASE3C_LIFECYCLE_CERTIFICATION_V1.md)。分支认证与独立复核完成前不宣称关闭；不合并 main，不启动 R1/R2。

## 固定基线依据

以下是源码/文档检查依据，不是本轮新运行认证：

- main 提交与父提交：https://github.com/a1351995160/trade/commit/7f22237958731fbc5e7803ee41ff40a55a277ff1
- 源码基线范围：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/docs/GITHUB_SOURCE_BASELINE_V2.md
- Web 服务构造与 startup recovery：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/webapp.py
- 既有 CP 执行与恢复：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/research_factory/autonomous_control_plane.py
- Action journal：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/research_factory/autonomous_action_journal.py
- 正式预测执行器：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/research_factory/predictive_executor.py
- 既有数据路由：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/research/data_router.py
- 每日观察选择器：https://github.com/a1351995160/trade/blob/7f22237958731fbc5e7803ee41ff40a55a277ff1/src/chanlun_trader/research/selector.py

## 2026-09-09 R1 部分交付已合并，可信源码恢复仍阻断

PR #6 已普通 merge 到 d3dcb68934ea8fb058c98039181d29894b6425df，第二父提交为最终认证 R1 部分交付 HEAD 404561c8867fdb27b01879ce553bac5e9079428f。本轮仅继续 R1，独立分支 codex/r1-trusted-source-recovery-v1；原项目固定提交及精确路径历史未提供 corrected 原脚本。详见 [可信源码恢复调查与计划](R1_TRUSTED_SOURCE_RECOVERY_V1.md)。未恢复或替代算法，源码闭包 BLOCKED_MISSING_SOURCE，真实数据 NOT_VERIFIED，READY_FOR_REAL_TRIAL=false，R1_FULLY_CLOSED=false；R2_STARTED=false。保留此前历史状态，不将部分 merge 当作 R1 完成。