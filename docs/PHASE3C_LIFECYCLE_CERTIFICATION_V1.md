# P3-C 生命周期认证：当前阻断与最小衔接方案

状态：BLOCKED_AT_MATERIALIZATION。这是失败证据与待批准的核心合同方案，不是 P3-C 通过报告。没有生产代码修改，没有真实研究授权。

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
