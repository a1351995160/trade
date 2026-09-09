# P3-C 生命周期认证与完整语义合同

## 授权语义一致性复核修正（2026-09-09）

本节为当前工作，后续 c37fd46/977c64e 的认证为历史证据。WORK_PACKAGE=P3-C；REVIEWED_HEAD=`977c64e4a5e57357dd71d5d0551e90be115143d4`；继续原独立分支。用户明确授权仅修复授权读取的一致性，不改变人工治理、预测执行权限、预算、journal 或锁；不回写历史，不开始新包。

短审计：现有读取验证 decision_hash、decision_id、confirmation_token_hash 和预算快照，但只按 decision_status 判断授权。治理服务已有三组唯一对应关系；无需新状态机或身份体系。文件级计划及实际落点：

- test_phase3c_lifecycle_boundaries_v1.py：先以真实 Scenario 建立 Design、人工批准、Proposal、Freeze、物化和显式合成 Structural PASS，再由真实 PredictiveGovernanceService 生成 DEFER/END；只篡改 status/hash，覆盖公共读取、dry-run、重启与历史只读。追加最新矛盾记录不得回退到先前授权的矩阵。
- predictive_authorization.py：提取既有决定类型→状态、状态→next_action 映射供写入和读取共同使用；纯校验要求已有身份字段完整、类型/状态/派生动作一致且 structural_status=PASS。
- autonomous_control_plane.py：在既有哈希/确认/预算验证之前验证上述语义；矛盾、未知或缺失语义返回 authorized=false、PREDICTIVE_AUTHORIZATION_INVALID，保留原校验。合法授权仍是 authorized=true + PHASE2_PREDICTIVE_EXECUTION_DISABLED，二者不混淆。
- 本文、progress.md、CLAUDE.md：保留失败证据，追加批准范围、验证和复核停止点；不改原五阶段选择或 skip/xfail。

### 项目级 red / green

外部证据仅为源码检查及抽取函数探针。本次本地 **真实服务链** red：两个参数均在公共 inspect 的 `assert authorized is False` 失败，实际返回 authorized=true。DEFER_PREDICTIVE_TRIAL 与 END_CANDIDATE_RESEARCH_DIRECTION 的原 decision_type、身份、确认 token、next_action 等字段均未改变；测试逐键断言差异集合恰为 decision_status、decision_hash。

保留日志 `tmp/p3c-evidence/authorization-consistency-red.log`（2 failed / 53 deselected，6.33 秒）；生产修正后同两例 `authorization-consistency-green.log`（2 passed / 53 deselected，13.42 秒）。这里 deselected 是定向 red/green 的 `-k` 选择，不是完整认证排除项。初次 red 的关键原文：

```text
FAILED ...test_non_authorizing_status_rehash_fails_closed_in_real_chain[DEFER_PREDICTIVE_TRIAL]
FAILED ...test_non_authorizing_status_rehash_fails_closed_in_real_chain[END_CANDIDATE_RESEARCH_DIRECTION]
E   AssertionError: {'authorized': True, 'decision_status': 'AUTHORIZED', 'authorization_id': 'P3C_AUTH', ...}
E   assert True is False
2 failed, 53 deselected in 6.33s
```

green 还验证新进程 inspect/dry_run 返回同一安全标记，文件内容、mtime、目录快照不变，无 TrialLedger。合法三类决定、合法 DEFER 后新明确批准、预算/身份/确认篡改、新旧语义合同和 L1—L10 沿用并重新运行。没有用全局 DENY 掩盖无效授权，也没有把合法正向验收改成拒绝。

完整回归首轮 104 passed（299.45 秒）后，顺序复查再复现“同候选最新记录缺 candidate_hash 被跳过，回退旧授权”：`authorization-missing-hash-red.log` 为 1 failed / 73 deselected（7.98 秒），同样是公共 inspect 返回 True 导致失败。因此将完整性校验前移至候选 ID 匹配后、候选 hash 过滤前。范围仍是当前必需字段与 fail-closed，不更改跨候选身份协议。追加普通 tick 断言：不规划 START、不执行领域动作、canonical JSON/JSONL 和预算原文不变；控制平面只可写其既有观察投影。

本地实际 Windows Python 3.13.5（MSC v.1943），fixture 临时根 C:/Users/84219/AppData/Local/Temp，C: NTFS。当前新 HEAD 的完整回归、push CI、PR-context CI 和 SonarCloud 在提交前后按实际结果补充；尚未取得的证据为 PENDING，不能沿用历史 HEAD 的绿色结论。

最终本地结果：定向新增 21 passed（64.97 秒）；完整 P3-C **105 passed**（298.48 秒）；Phase 1 **235 passed / 12 deselected**；Phase 2 **24 passed**；P3-A **66 passed**；P3-B **35 passed**。collect-only **880 collected**，另 1 个继承 legacy module skip；compileall、git diff --check、前端构建及 8 tests 成功。最终日志为 `tmp/p3c-evidence/authorization-final.log`、`authorization-final.xml`、`authorization-final-regressions.log`，各阶段原文为 `*-authorization-final.log`。

本次各阶段主进程 template/approve/confirm：P3-A 5/1/4，Phase 1 72/70/74，Phase 2 18/17/17，P3-B 33/33/33，P3-C 1/82/82；均为运行局部探针，不是独立人工行为数。已安装的 forbidden_predictive/structural/ai 与 network/process/protected_accesses 实测均 0；保留 worker 的独立检查点与 P3-B 硬退出 UNAVAILABLE 标记，不把缺失统计、合成调用或未安装的 Performance/Final Test/订单全局探针写为 0。

NEW_HEAD 为包含本节及生产修正的分支提交；push/PR run_id 与最终 HEAD 由交付报告和 PR 检查记录绑定。CI_STATUS_AT_COMMIT=PENDING。项目级证据已经取得，但 READY_FOR_FINAL_INDEPENDENT_REVIEW 在本提交时仍等待新 HEAD 的双平台和 PR-context 检查，不沿用旧 HEAD 的 ready 声明。

实际 main ruleset 22374784（main-merge-governance）要求 strict `Deterministic governance suite`，无 bypass actor；classic branch protection API 返回 Branch not protected，不能据此称没有 ruleset。SonarCloud 是否出现及结果须读取本次 PR checks，不臆测为 required。

REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO；PHASE3_CLOSED=false；MAIN_MERGED=false；NEXT_PACKAGE_STARTED=false。回滚点为 reviewed HEAD；提交后可在本分支 `git revert --no-edit <本次修正提交SHA>`，不 reset main、不删除研究历史。

## 双平台认证结论（2026-09-09）

实现 HEAD：`c37fd46c9bfa83968dc1d9ba7effd5d89f4047bd`。分支仍为 `codex/phase3c-lifecycle-certification-v1`；origin/main 为 `e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08`，main 基线与 P3-B 认证 HEAD 的 ancestry 检查均 exit 0。

[P3-C run 34301691534](https://github.com/a1351995160/trade/actions/runs/34301691534) 已完成，Linux 与 Windows 均 SUCCESS。以下数字来自该 run 原始 job 日志，不将 collected 当作 passed。

| 验证 | Windows CI | Linux CI |
|---|---:|---:|
| Python | 3.13.15，MSC v.1944 | 3.11.16，GCC 13.3.0 |
| OS | Windows Server 2025 Datacenter 10.0.26100 | Linux 6.17.0-1022-azure |
| 实际 fixture 临时根 | C:/Users/RUNNER~1/AppData/Local/Temp | /tmp |
| fixture 所在文件系统 | C: NTFS | /dev/root，ext4 |
| P3-A | 66 passed | 66 passed |
| Phase 1 | 235 passed / 12 deselected | 234 passed / 1 skipped / 12 deselected |
| Phase 2 | 24 passed | 24 passed |
| P3-B | 35 passed | 35 passed |
| P3-C | **84 passed**，200.89 秒 | **84 passed**，127.42 秒 |
| collect-only | 859 collected，另 1 个 legacy module skip | 相同 |
| frontend | 8 tests，build 成功 | 相同 |

Linux Phase 1 的 1 skipped 是既有 `test_windows_resource_monitor_has_native_fallback_without_psutil`，在非 Windows 上按原条件跳过。12 deselected 与 legacy module skip 均沿用原认证选择，无新增 skip/xfail/排除。pytest 的 1 warning 是既有 anyio BlockingPortal 弃用提示。

同一 HEAD 的独立原工作流全部 SUCCESS：Phase 1 run 34301691757；Phase 2 run 34301691768；P3-A run 34301691558；P3-B run 34301691489。P3-C JUnit artifacts：Linux 10085225030、Windows 10085290888，名称均含完整实现 SHA；测试成功输出与 worker 探针见上述 CI 日志。

两平台各阶段主进程 template/approve/confirm 计数一致：

| 阶段 | synthetic template | approve 尝试 | confirm 尝试 |
|---|---:|---:|---:|
| P3-A | 5 | 1 | 4 |
| Phase 1 | 72 | 70 | 74 |
| Phase 2 | 18 | 17 | 17 |
| P3-B | 33 | 33 | 33 |
| P3-C | 1 | 61 | 61 |

这些不是独立人工批准人数或动作总数。P3-C 的 template 1 来自旧轻量兼容测试，完整设计由显式合成 backend 产生并另有 Scenario 事件。各阶段已安装的 forbidden_predictive / forbidden_structural / forbidden_ai，以及 import 前 network / 非 Python process / protected-root 计数均实测为 0；P3-C worker 检查点逐项断言实际探针为 0。P3-B 硬退出明确输出 UNAVAILABLE_P3B_HARD_EXIT_USE_FSYNC_EVENTS，绝不把缺失统计当零。

本地同一实现 HEAD：Windows Python 3.13.5 / C: NTFS，P3-C 84 passed（233.48 秒），原四阶段回归均通过；日志在 tmp/p3c-evidence/p3c-c37fd46.log、regressions-release.log，CI 原文亦保留在该忽略目录。设计/批准/Proposal/候选/最终 Durable 哈希已在真实正向测试分别输出，并断言不同层不混为一值、同获批语义贯穿物化。

认证仅覆盖临时合成领域服务链。未启动真实研究、AI、行情、Structural/Predictive 实际执行器、Final Test、Prospective/Paper 或订单；未声称真实工作区运行态已认证。预算负向中的显式合成 reserve/consume 不计作真实研究，也不伪写成无预算操作。

实现已完成，待独立复核。本文后续文档提交不改代码或测试；最终交付回复绑定最新文档 HEAD 及其分支 CI，提交时尚未结束的 CI 如实为 PENDING。
P3C_IMPLEMENTATION_STATUS=IMPLEMENTED_AND_SOURCE_CERTIFIED
FULL_SYNTHETIC_LIFECYCLE_REACHED_AUTH_BOUNDARY=true
READY_FOR_INDEPENDENT_RECERTIFICATION=true
PHASE3_CLOSED=false
MAIN_MERGED=false
NEXT_PACKAGE_STARTED=false

回滚实现：在本独立分支执行 `git revert --no-edit c37fd46c9bfa83968dc1d9ba7effd5d89f4047bd`；不修改 main，不删除任何研究历史。

## 2026-09-09 用户明确批准的实施范围

用户已批准在既有 Design → 人工审批 → Proposal → Freeze → Materialization 中传递显式完整执行语义，仅解决已复现的语义缺失与候选身份衔接。此批准覆盖 research_evolution_ai_design.py、candidate_generation.py、必要审批/物化衔接及对应测试文档；其他文件仅限本轮复现的必要缺陷。以下历史“待批准”内容保留为当时记录，当前状态为 IMPLEMENTING。

- 语义必须在审批前确定，经既有 schema、SemanticCandidateRecord、hypothesis、能力、政策和 outcome-blind 校验，进入设计哈希和批准绑定。AI/backend 输入不可信；不在下游补猜执行规则，实质变化必须新设计、新审批。
- 复用既有语义预注册身份；design/proposal/preregistration/approval/content 哈希各司其职，避免循环依赖，不覆盖 candidate_hash、不跳过重建或 provider payload 检查。
- 用明确版本区分新旧路径，旧轻量格式保持原身份与物化阻断；不自动升级、补全或改写历史设计、批准、Freeze、intent、回执，已有合法完整合同继续严格验证。
- 保留 durability.py 严格约束、planner、journal、锁、预算协议、P3-A 只读/无 startup recovery、P3-B 公共恢复与 retry attempt 修复。
- 验收包括完整合法链路、缺失/非法/冲突/审批后篡改、新旧与跨身份、重放/重启/幂等/dry-run、嵌套结果盲化、原回归及 P3-C 双平台认证。保留原失败证据，不把合法正向断言改为拒绝。
- 仅代码与临时合成验证授权，不替代具体领域人工审批。Predictive Trial、真实研究、Final Test、Prospective/Paper、订单均未授权；保持 PHASE2_PREDICTIVE_EXECUTION_DISABLED。完成分支测试/提交/推送/CI 后待独立复核，不 merge/auto-merge，不开后续包。此范围内不重复申请批准；预算、绩效访问、执行权限等新增核心范围另行申请。

验证计划：先使完整输入通过同一真实治理链并保持缺失输入阻断，再用同一场景前缀覆盖生命周期与 L1—L10，最后运行继承回归与双平台 CI。先前 HEAD 5a14f9a 的 run 34298279411 双平台均已完成，P3-C 阶段失败，其前序回归步骤成功；该失败记录不改写。

## 当前实现与认证（用户批准后）

本节保留实现期间的验证过程；最终结果以上文 c37fd46 双平台认证结论为准。更早的“历史阻断记录”仅对应 5a14f9a。

### 文件级落点与身份引用

- research_evolution_ai_design.py：显式 `research-evolution-ai-design-v2`，在审批前验证完整 Durable 输入、SemanticCandidateRecord 重建、provider payload、hypothesis、语义指纹、Objective/政策/数据能力与结果盲化；完整语义进入 design_hash。
- ai_design_approval.py：确认 v2 设计前重新执行持久身份和语义校验。批准绑定 design_hash，未改变人工身份门禁。
- candidate_generation.py：显式 `candidate-proposal-governance-v2`，传递获批的 durable_contract/hypothesis，执行字段直接取自获批 record；v2 不调用旧轻量执行默认值。语义候选使用既有 preregistration identity，旧格式继续原身份算法。
- candidate_executable_materialization.py：现有 Proposal 哈希验证识别 v2。durability.py、provider payload/reconstruct 约束未改。
- autonomous_control_plane.py：本轮 8 个授权篡改用例中有 5 个先失败（auth-identity-red.log：5 failed / 3 passed），追加既有 decision_hash 校验后 8 个通过。只更正“有效授权”读取，不改变全局 Predictive DENY。
- 测试：p3c_scenario.py 只负责临时初始化与显式操作；p3c_process_worker.py 实例化真实服务；四个 test_phase3c_* 文件覆盖主链、语义负向、L1—L10、入口/损坏/并发/预算。原 Phase 2 中手写简化 AUTHORIZED 行的单个测试改由同一真实授权服务生成，保留 START_PREDICTIVE_TRIAL + DENY 的原断言。
- CI：继承原四阶段测试选择，追加全部 P3-C 测试与 JUnit artifact，未增加 skip、xfail、continue-on-error 或排除项。

引用方向：SemanticCandidateRecord.preregistration_hash → v2 candidate_hash；完整输入内容 → design_hash → approval receipt；获批设计/批准/上下文与相同完整语义 → proposal_hash → review/Freeze Receipt；Freeze、确认与来源身份 → 物化 content_hash。各层分别哈希，不相互强行取同一值。物化仍按既有规则追加来源绑定，因此最终 content_hash 与设计中声明合同的 content_hash 可能不同，执行语义及 candidate_hash 必须相同。没有反向把 proposal_hash 纳入已批准设计形成循环。

旧轻量 v1 仍可生成、审核、冻结，缺 full_semantic_record/hypothesis 时物化明确拒绝；不得自动升级旧设计、批准或 Freeze。现存合法完整 Durable 合同由原 Phase 1 测试继续验证。

### 生命周期与重启证据矩阵

所有前缀都从同一自包含 Scenario 初始化，经真实服务产生；没有后补成功工件。初始资料包括合成 Objective、合成父来源及类别资料、能力/政策和真实预算 registry 三类桶。显式 backend 返回完整声明；显式 Structural provider 根据三条合成可用性记录返回计数与证据。没有连接任何真实 provider。

| 点 | 重启位置 | 新进程边界 |
|---|---|---|
| L1 | Design 人工批准后 | 仅下一显式 tick 生成 Proposal |
| L2 | Proposal 已写、CP COMPLETED 未写，os._exit | 公共 tick 匹配副作用、补回执，不重复领域调用 |
| L3 | 人工 Freeze 后 | 仅生成物化预览 |
| L4 | 预览已写、CP COMPLETED 未写，os._exit | 匹配预览恢复，等待人工确认 |
| L5 | Confirmation 已写、Durable 未写，os._exit | CP 仅恢复原获批物化 |
| L6 | Durable 已写，os._exit | canonical 已满足 READY，等待显式 Structural |
| L7 | READY、未调用 Structural | inspect/dry-run 不调用 provider |
| L8 | 显式合成 Structural canonical 已写，os._exit | 等待独立预测授权，不重复 Structural |
| L9 | 等待 Predictive Authorization | 新进程显式人工确认才生成授权 |
| L10 | 有效人工授权后 | CP 仍 PHASE2_PREDICTIVE_EXECUTION_DISABLED |

矩阵每项重启时核对文件哈希与 mtime，inspect/dry-run 不写；继续后比较已存在 Design、批准、Proposal、Freeze、物化确认和 Durable 原文字节，预算不变，合成设计/Structural 各一次，无 TrialLedger。另在完整设计前缀验证领域调用前硬退出、FAILED 后 retry marker 硬退出、同 intent/key 恢复到 attempt 2。P3-B 的进程 kill、竞争 recoverer、共享 graph reload、独立 Objective/锁别名测试随回归原样执行。

投影删除与 evidence 损坏分开：删除派生 state/checkpoint 后，保留 intent/STARTED 可恢复；receipt 半行/错误哈希/丢失、intent 丢失/损坏均 DENY，不补造回执。Web/CLI/CP 分别从获批前缀执行两类自动动作和最终 Trial 禁令，逐次验证 dry-run 原文/mtime 不变、Web startup recovery 关闭。共享锁测试以实际两进程 handshake 重叠 CP 与直接领域入口，完成后验证唯一 Proposal 可物化。

预算正向链从初始化后无 reserve/consume；负向场景显式用既有 registry 构造 reserve 4、consume 4 或第二份冲突 authority，随后 CP 不生成候选/Trial、不改变原证据。实际非 dry-run 仍可创建锁和控制平面观察日志；不把这一点误称全目录只读。缺省策略、重哈希 Action、过期上下文、身份门禁与 daemon/orchestrator 不旁路执行，继续由原 P3-A/B、Phase 1/2 回归覆盖。

授权实际格式来自 PredictiveGovernanceServiceV1：decision_id/hash、authorization_id、governance_decision_id、Objective/candidate 身份、structural_reconciliation_id、preview_hash、confirmation_token_hash、budget_snapshot 等；没有虚构 expiry 或 trial_id 字段。主链与三入口验证真正 AUTHORIZED，缺失、身份错配、字段篡改、DEFERRED、ENDED 和非 PASS Structural 均不形成有效授权。

### 证据口径与当前结果

已执行原回归：Windows Python 3.13.5；Phase 1 235 passed / 12 deselected，Phase 2 24 passed，P3-A 66 passed，P3-B 35 passed。collection 的 846 是当时收集数，不是 passed；后续新增边界后以最终日志为准。前端此前 8 tests/build 通过，本次不改前端。

P3-C 首轮组合为 67 passed / 4 failed，另补人工边界首轮为 4 passed / 2 failed；这些是测试适配错误（旧轻量 backend 选择不适配、非 dry-run 允许观察日志、无治理入口返回明确 404），修正 API 使用后六个针对性用例通过。此前 L6 首轮 11 passed / 1 failed，按已存在 Durable 的实际 canonical READY 修正预期，没有把合法链改为拒绝。完整正向验收始终保留并完成到真实授权。

计数按运行分开：pytest 主进程现有三类禁止执行器探针与 import 前 network/non-Python process/protected-root 探针；P3-C worker 另在普通返回和 os._exit 前输出实际方法调用探针。P3-B 硬退出沿用其 fsync 事件，缺失 atexit 统计不当作零。Scenario 记录 synthetic_design_call、synthetic_structural_provider 和各次人工操作尝试；approve 内部 confirm 不计作两次独立人工批准。没有独立全覆盖 PerformanceAccess/Final Test/订单探针，不伪造这些指标为零。

本地 fixture：C:\Users\84219\AppData\Local\Temp 下的 pytest 临时目录，实际 C: NTFS；Windows Python 3.13.5。Linux 精确版本/文件系统等待新 CI。未认证真实研究运行态、多主机、网络文件系统、断电。
REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO。

授权预算快照补验：auth-budget-red.log 先复现 1 failed；直接比较安全投影的同名哈希会误拒绝合法授权（auth-final.log），改为读取当前唯一 authority 的 SearchBudgetRegistryV1.head_hash，与授权服务保持同一哈希语义。auth-final2.log 为 12 passed / 40 deselected，包含合法授权、篡改及原快照过期。budget.py 与预算协议未改。

本地整组 p3c-release.log 为 80 passed（230.88 秒）；主进程 template/approve/confirm 为 1/57/57，三种禁止执行器和 process 探针均为其各自观测范围内 0。随后重哈希授权身份三项先 3 failed，增加既有 schema/decision_id/token 身份关系校验后授权组合 15 passed；暂缓后更换预算快照再明确授权的正向先 1 failed，修正历史快照仅对该条决定失效后，组合 5 passed。最终 CI 套件为 84 项；不把尚未整组运行的 84 写为本地 passed。原回归再次运行：P3-A 66、Phase 1 235/12 deselected、Phase 2 24 已通过；P3-B 本次重跑尚在运行，前次为 35 passed。collection 实际为 859，另 1 个继承 module skip。

重哈希/再授权失败证据为 auth-protocol-red.log、auth-renewal-red.log；修正后为 auth-protocol-final.log、auth-renewal-final.log。这些校验复用现有授权格式，不引入新权限。新 HEAD 与 run_id 在提交后追加；未完成 CI 必须 PENDING。停止点为独立分支交复核，PHASE3_CLOSED=false、MAIN_MERGED=false、NEXT_PACKAGE_STARTED=false。

## 历史阻断记录（仅 5a14f9a，保留失败证据）

历史状态：BLOCKED_AT_MATERIALIZATION。这是当时的失败证据与待批准方案，已由上文用户批准及实现接续。

## 基线与工作区

- BASE_COMMIT / 实际 origin/main：`e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08`。
- PR #4：MERGED，merge commit 与上述基线一致；认证 HEAD `75ec0be63a46d10bce6c5d3766e74b46cb63e0a8` 在 ancestry 中。
- 分支：`codex/phase3c-lifecycle-certification-v1`。
- 独立 worktree：`E:\llmwiki\chanlun-trading-system-phase3c-lifecycle-certification-v1`。
- 实际存在的项目规范为根目录 CLAUDE.md；AGENTS.md、DEVAGENT.md 不存在。另遵循用户提供的 AGENTS 指令。CLAUDE.md 引用的中文输出规范文件在此源码基线不存在。
- 原研究目录只尝试读取不存在的 AGENTS.md，未作为研究输入输出；未读取其中行情、合同、运行日志或 Final Test。

## 短审计与权威矩阵

| 节点 | canonical 来源 | 入口／权限 | 写入范围 | 当前证据 |
|---|---|---|---|---|
| 设计 | AI Design Proposal、输入身份 | generate_design；显式确定性 template | 当前目标设计目录 | 本轮实际生成 |
| 人工批准 | Approval Receipt | approve；明确 reviewer | 当前目标批准回执 | 本轮真实审批 |
| 候选 | Candidate Proposal | CP tick；ALLOW_AUTOMATIC | Proposal、intent、journal | 本轮 COMPLETED |
| 冻结 | review、Freeze Receipt、Candidate Registry | review + confirmed freeze | 当前目标治理记录 | 本轮真实冻结 |
| 物化 | Preview + Confirmation + Durable Contract | CP preview；人工 confirm | 当前目标物化及共享合同登记 | 本轮被缺失语义阻断 |
| Structural | canonical reconciliation + execution evidence | 显式 StructuralEntry；CP MANUAL_ONLY | reports 中的 canonical 文件 | 主链未到达 |
| 预测授权 | Predictive Governance decision | 人工 confirm | 授权决策与哈希链 | 主链未到达 |
| 预测执行 | 现有策略 | PHASE2_PREDICTIVE_EXECUTION_DISABLED | 不应产生 Trial 或预算预留 | 本轮未到达该断言 |

既有物化 `_bridge_fixture` 手工补写 Proposal、Freeze Receipt、Registry，不能用于本轮主成功路径。当前测试仅复用 `_fixture_root` 的自包含初始化，不复用任何下游成功状态；所有后续成功记录均由真实服务写入。

## 已复现的失败

测试：`test_phase3c_lifecycle_v1.py::test_real_governance_freeze_can_reach_materialization_preview`。

实际经过：初始化 → 设计 → 两次 tick 稳定等待人工 → 显式批准 → CP 生成 Proposal/COMPLETED → 人工审核 → 人工 Freeze。断言 Proposal 原文和预算原文没有被测试改写。随后调用真实 create_preview，正向验收失败。

- 参数 False：缺少 batch_id、factor_event_registry_identities、full_semantic_record、hypothesis、policy_identity、research_period_identity、source_provenance。
- 参数 True：在设计前补齐 Objective 初始化元数据后，仍缺少 **full_semantic_record、hypothesis**。
- 两项均为 `EXECUTABLE_MATERIALIZATION_INCOMPLETE`，`safe_to_advance=false`；没有异常 500 被当作安全通过。
- 本地第一版 1 failed，改为上述两种初始化后 2 failed。未改为 raises/skip/xfail，保留正向验收失败。
- 日志：本地忽略目录 `tmp/p3c-evidence/lifecycle-red.log`、`lifecycle-red-v2.log`；共享证据由分支 CI 同一测试日志提供。初版日志的中文控制台编码有损；第二版显式 UTF-8。

这不是只缺初始政策的 fixture 问题：设计校验只返回六个描述性字段；Candidate 生成只构造轻量因子、执行与数据合同，没有传递完整语义的公开入口。治理 candidate_hash 是轻量 Proposal 身份哈希；Durable Contract 则强制 candidate_hash 等于 SemanticCandidateRecord.preregistration_hash。后者还重建校验，不能通过覆盖 hash 接通。

## 待批准的最小核心合同方案

用户提供的 AGENTS 第 5 节要求“改核心协议、内存地址、数据库结构须用户明确批准”。下列方案改变设计审批和候选冻结的身份含义，需批准后才实施；不是常规字段搬运。

1. 在现有 AI Design 合同中允许明确的完整执行语义输入，复用 SemanticCandidateRecord 与 hypothesis；由确定性合成 backend 给出，不在物化阶段猜默认交易规则。该内容进入设计 hash 和人工批准身份。
2. Candidate 生成验证并传递被批准的相同执行语义。带完整语义的新候选使用已有 semantic preregistration identity；保留旧轻量建议的历史校验和阻断语义，不改写历史 Freeze。
3. Freeze 绑定上述语义，Materialization 继续严格核对同身份、同内容和 provider payload；不放宽任何 hash 门禁。
4. 不改变 planner、journal、预算 schema、锁、默认 READ_ONLY 或预测执行禁令。

拟涉及生产文件：research_evolution_ai_design.py（设计字段及哈希）、candidate_generation.py（传递、校验及身份）；仅在测试进一步复现需要时调整 materialization 衔接。durability.py 的既有语义身份约束优先保持。该方案尚未编码、尚未认证兼容性。

批准后继续文件计划：抽出自包含场景工厂；补 `test_phase3c_lifecycle_restart_v1.py` 与独立 worker；补生命周期负向边界和 Web/CLI/CP 对等测试；用同一场景真实前缀覆盖 L1—L10。当前不创建十个手写成功状态冒充重启矩阵。

## 未完成的认证范围

L1—L10、硬退出、公开 root+Objective 恢复组合、五类人工门禁正反组合、上下文变更、Structural 分支、有效授权身份、投影删除、执行证据损坏、入口对等、相关并发、结果字段注入、预算边界与完整安全探针：**P3-C NOT_VERIFIED**。既有 Phase 1/2/P3-A/P3-B 测试不能代替这些组合证据。

文件分类沿用既有合同：decision display、可重建 checkpoint 属于投影；intent、journal、管理标记是执行证据；批准、Freeze、Durable、Structural、Authorization 和预算是 canonical。不能按 reports 目录整体删除。此次未执行投影删除实验。

授权实际 schema 的完整清单及 missing/mismatched/tampered/stale/deferred/ended/valid 分支尚未完成认证；不声称 expiry 或 trial_id 已验证。Daemon/Orchestrator 只继承既有防旁路回归，不宣称后台生命周期接通。

## 平台、回归与安全口径

本地全新 venv：Windows Python **3.13.5**。实际 fixture 在 `C:\Users\84219\AppData\Local\Temp` 的 pytest 子目录；Get-Volume 结构化输出为 `C: NTFS`。Linux 精确版本与 fixture 文件系统等待实际 CI，不猜测。

本轮局部失败运行：2 个合成模板调用、2 次 approve 尝试、2 次 confirm 尝试（approve 内部调用 confirm，不能合计为四次独立人工批准）。两条链各成功生成一个候选并完成一次 Freeze。预算文件在 Freeze 后保持初始化字节。初始化仍是简化预算视图，不是完成预算桶生命周期认证。

已有探针观测 forbidden_predictive / forbidden_structural / forbidden_ai 均 0；网络、非测试进程、受保护参考目录访问均 0。覆盖位置为 tests/conftest.py 和 tests/isolation/sitecustomize.py。没有新增对所有 PerformanceAccess、订单、Final Test 和所有文件 I/O 的独立探针，不将缺失统计写为 0。没有硬退出 worker，因此缺失硬退出证据不作零调用声明。

REAL_WORKSPACE_RUNTIME_INDEPENDENTLY_VERIFIED=NO。未认证真实数据、真实运行态、Windows Python 3.11、多主机、网络文件系统或断电。

工作流复用既有锁定依赖及原 Phase 1 选择，执行 P3-A/import 先验、compile、collect、Phase 1、Phase 2、P3-B、前端测试与构建，最后执行上述正向 P3-C 用例。没有 continue-on-error、新增 skip/xfail 或排除项。预期 P3-C 阶段报告真实失败，不能将其他回归通过写为 P3-C PASS。

CI_STATUS_AT_COMMIT=PENDING；实际 HEAD 与 run_id 由交付回复绑定。测试日志是证据，不是新增 canonical 权威。

本地实测：Phase 1 为 235 passed / 12 deselected；Phase 2 为 24 passed；P3-A 为 66 passed；P3-B 为 35 passed；P3-C 为 2 failed；collect 为 777（775 基线 + 2 本轮用例），另 1 个继承 legacy module skip。compileall、diff 检查、前端 8 tests 和构建通过。

历史失败保留：Phase 1 首跑 228 passed / 7 failed，其中 6 个页面在前端构建前返回 404，1 个 CLI 子进程按 GBK 解码 UTF-8 失败；构建后第二次为 234 passed / 1 failed；统一 Python UTF-8 后原选择 235 passed。没有更改原测试。日志分别为 Run-Phase-1-deterministic-regression-suite.log、phase1-after-frontend.log、phase1-utf8.log。

本地各最终回归的 template / approve / confirm 探针依次为：P3-A 5/1/4，Phase 1 72/70/74，Phase 2 19/17/17，P3-B 33/33/33。属于各次运行的局部计数，含 fixture 和失败尝试，不能相加当独立人工行为数；P3-B 硬退出子进程仍按既有持久事件证据验证，不把缺失 atexit 当零。

## 回滚与停止状态

本轮仅测试、工作流、文档改动，不改变生产行为。提交后在此独立分支执行 `git revert --no-edit <本轮提交SHA>`；基线为 e50f5abc26bc9aa3b5927c2d5c436f47b3ee3a08。不 reset main，不删除真实或合成执行历史。

P3C_IMPLEMENTATION_STATUS=BLOCKED_CORE_CONTRACT_DECISION
FULL_SYNTHETIC_LIFECYCLE_REACHED_AUTH_BOUNDARY=false
READY_FOR_INDEPENDENT_RECERTIFICATION=false
PHASE3_CLOSED=false
MAIN_MERGED=false
NEXT_PACKAGE_STARTED=false
